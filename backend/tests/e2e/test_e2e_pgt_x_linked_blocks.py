"""An X-linked PGT family's haplotype blocks and embryo calls, from its import (CLIN-60's rule).

Over a synthetic X-linked recessive family imported from a package (the father, the mother,
their affected son as the index, a son and a daughter as embryos; a phased GLIMPSE2 family VCF
on chrX with PAR1 sites and sites around the ROI, the men's calls outside the PARs written as
one allele, as GLIMPSE2 writes a haploid call; the model XLR and an ROI on chrX), through the
API, this:

* reads the blocks the import stored, as ``GET /families/{id}/haplotypes`` serves them:
  outside the PARs the sons carry their mother's homolog and no father's, the daughter her
  father's one X and her mother's other homolog, the father has blocks there; no block runs
  from PAR1 over the X, and the sons' blocks over the ROI are marked one copy in males;
* reads the embryo calls the family page shows (``GET /families/{id}/pgt-analysis``): the
  disease haplotype is the mother's homolog the affected son carries, the son embryo who
  carries it is at risk and the daughter unaffected, each QC read on the mother's side.

Read as two copies, the men's one-allele calls gave no transmission outside the PARs: the PAR1
blocks ran over the X, the affected son was read on both lanes and gave two candidates for the
disease haplotype, and every embryo was uninformative.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

_FORMAT = "GT:DP:GP"
# GRCh38: PAR1 ends at 2,781,479. The sites outside the PARs run from 50 kb before the ROI
# to 50 kb past it, so the ROI holds the hundred from 31,000,500.
PAR1_LAST = 2_781_479
ROI = "X:31000000-31100000"
PAR1_SITES = [1_000_000 + index * 10_000 for index in range(20)]
X_SITES = [30_950_500 + index * 1_000 for index in range(200)]


def _cell(gt: str) -> str:
    return f"{gt}:30:0.05,0.9,0.05"


def _write_package(root: Path, family_id: str) -> dict[str, str]:
    """An X-linked recessive PGT package. In PAR1 everyone has two copies: the father's X
    copy is his homolog 0, his Y copy homolog 1, which the sons carry. Outside the PARs the
    mother is heterozygous at every site; the affected index and the son embryo carry her
    homolog 1, the daughter embryo her homolog 0 and her father's one X."""
    names = {role: f"{role}_{family_id}" for role in ("FATHER", "MOTHER", "INDEX", "E_SON", "E_DAUGHTER")}
    father, mother = names["FATHER"], names["MOTHER"]
    root.mkdir(parents=True, exist_ok=True)
    (root / "family.ped").write_text(
        "\n".join(
            [
                f"{family_id}\t{father}\t0\t0\t1\t1",
                f"{family_id}\t{mother}\t0\t0\t2\t1",
                f"{family_id}\t{names['INDEX']}\t{father}\t{mother}\t1\t2",
                f"{family_id}\t{names['E_SON']}\t{father}\t{mother}\t1\t0",
                f"{family_id}\t{names['E_DAUGHTER']}\t{father}\t{mother}\t2\t0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "manifest.yaml").write_text(
        "schema_version: 1\n"
        f"family_id: {family_id}\n"
        "ped: family.ped\n"
        f"roi: \"{ROI}\"\n"
        "metadata:\n"
        "  pgt:\n"
        "    inheritance_model: XLR\n"
        "family:\n"
        "  members:\n"
        f"    {names['E_SON']}: {{role: embryo}}\n"
        f"    {names['E_DAUGHTER']}: {{role: embryo}}\n"
        "datasets:\n"
        "  haplotypes:\n"
        "    family_vcf: haplotypes/family.glimpse2.vcf\n"
        "    source_format: glimpse2\n",
        encoding="utf-8",
    )
    columns = [names[role] for role in ("FATHER", "MOTHER", "INDEX", "E_SON", "E_DAUGHTER")]
    header = [
        "##fileformat=VCFv4.2",
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
        '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">',
        '##FORMAT=<ID=GP,Number=G,Type=Float,Description="Genotype posteriors">',
        "#" + "\t".join(["CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO", "FORMAT", *columns]),
    ]
    rows = []
    for index, position in enumerate(PAR1_SITES):
        x_copy = index % 2
        y_copy = 1 - x_copy
        mother_0, mother_1 = (index // 2) % 2, 1 - (index // 2) % 2
        genotypes = [
            f"{x_copy}|{y_copy}",
            f"{mother_0}|{mother_1}",
            f"{y_copy}|{mother_1}",
            f"{y_copy}|{mother_1}",
            f"{x_copy}|{mother_0}",
        ]
        rows.append("\t".join(["X", str(position), ".", "A", "G", "60", "PASS", ".", _FORMAT, *map(_cell, genotypes)]))
    for index, position in enumerate(X_SITES):
        mother_1 = index % 2
        mother_0 = 1 - mother_1
        father_x = (index // 2) % 2
        # The men's calls outside the PARs are one allele each.
        genotypes = [
            str(father_x),
            f"{mother_0}|{mother_1}",
            str(mother_1),
            str(mother_1),
            f"{father_x}|{mother_0}",
        ]
        rows.append("\t".join(["X", str(position), ".", "A", "G", "60", "PASS", ".", _FORMAT, *map(_cell, genotypes)]))
    vcf = root / "haplotypes" / "family.glimpse2.vcf"
    vcf.parent.mkdir(parents=True, exist_ok=True)
    vcf.write_text("\n".join(header + rows) + "\n", encoding="utf-8")
    return names


def _cap(resp) -> dict:
    out = {"status": resp.status_code, "text": resp.text}
    try:
        out["json"] = resp.json()
    except ValueError:  # pragma: no cover
        out["json"] = None
    return out


async def _collect(root: Path, family_id: str) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)
    async with sessionmaker() as session:
        result = await package_import.execute_family_package_import(
            session, folder_path=str(root), project_id=project_id, dry_run=False, user=admin, conflict_mode="cancel"
        )
    out: dict = {
        "completed": result.completed,
        "error": result.error,
        "statuses": {dataset.dataset_type: dataset.status for dataset in result.datasets},
        "responses": {},
    }
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"
        family = f"/api/families/{family_id}"
        out["responses"]["haplotypes"] = _cap(
            await ac.get(f"{family}/haplotypes", params={"chr": "X", "start": 0, "end": 40_000_000})
        )
        out["responses"]["analysis"] = _cap(await ac.get(f"{family}/pgt-analysis"))
    return out


@pytest.fixture(scope="module")
def x_linked(tmp_path_factory, request) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("e2e_pgt_x_linked")
    family_id = f"FAM_PGTX_{uuid4().hex[:8]}"
    names = _write_package(base / family_id, family_id)
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base)])
    request.addfinalizer(mp.undo)
    out = _harness.run_async(lambda: _collect(base / family_id, family_id))
    out["names"] = names
    assert out["completed"] is True, out
    assert out["statuses"].get("haplotypes") == "imported", out
    return out


def _ok(x_linked: dict, key: str) -> dict:
    resp = x_linked["responses"][key]
    assert resp["status"] == 200, (key, resp["status"], resp["text"][:2000])
    return resp["json"]


def _segments(x_linked: dict, role: str) -> list[dict]:
    name = x_linked["names"][role]
    [sample] = [sample for sample in _ok(x_linked, "haplotypes")["samples"] if sample["sample"] == name]
    return sorted(sample["segments"], key=lambda segment: segment["start"])


def test_the_stored_blocks_read_a_man_one_x_outside_the_pars(x_linked) -> None:
    for role in ("FATHER", "MOTHER", "INDEX", "E_SON", "E_DAUGHTER"):
        segments = _segments(x_linked, role)
        # A block in PAR1, and the X between the PARs from its first site: none across.
        assert any(segment["end"] <= PAR1_LAST for segment in segments), (role, segments)
        assert not any(segment["start"] <= PAR1_LAST < segment["end"] for segment in segments), (role, segments)
        assert any(segment["start"] > PAR1_LAST for segment in segments), (role, segments)
    for role in ("INDEX", "E_SON"):
        over_x = [segment for segment in _segments(x_linked, role) if segment["start"] > PAR1_LAST]
        # His mother's homolog 1, no father's: his father gave him a Y. One copy in males.
        assert {(segment["hap1"], segment["hap2"]) for segment in over_x} == {("?", "1")}, over_x
        assert all(segment["hemizygous_in_males"] is True for segment in over_x), over_x
    daughter = [segment for segment in _segments(x_linked, "E_DAUGHTER") if segment["start"] > PAR1_LAST]
    # Her father's one X (his homolog 0) and her mother's homolog 0.
    assert {(segment["hap1"], segment["hap2"]) for segment in daughter} == {("0", "0")}, daughter


def test_the_embryos_at_an_x_linked_roi_are_called_from_those_blocks(x_linked) -> None:
    names = x_linked["names"]
    analysis = _ok(x_linked, "analysis")

    assert analysis["region_of_interest"]["chrom"] == "X"
    assert analysis["inheritance_model"]["mode"] == "x_linked_recessive"
    assert analysis["disease_haplotype_resolved"] is True
    assert [(d["origin"], d["homolog"], d["kind"]) for d in analysis["disease_haplotypes"]] == [
        ("maternal", "1", "x-linked")
    ]
    embryos = {embryo["sample"]: embryo for embryo in analysis["embryos"]}
    son, daughter = embryos[names["E_SON"]], embryos[names["E_DAUGHTER"]]
    assert (son["call"], daughter["call"]) == ("affected_or_at_risk", "unaffected_non_carrier")
    assert {(homolog["origin"], homolog["homolog"]) for homolog in son["inherited"]} == {("maternal", "1")}
    # A hundred sites at the ROI, each read on the mother's side: the QC is met.
    for embryo in (son, daughter):
        assert embryo["qc"]["informative_sites"] == 100 and embryo["qc"]["mendel_errors"] == 0, embryo
        assert embryo["qc_verdict"] == {"status": "pass", "sides": ["maternal"], "findings": []}, embryo
