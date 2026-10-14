"""The SV ranking scores an SV by the copy-number change it makes, read back from ClickHouse
(E2E, CLIN-47), and the genome tracks draw it by that change (CLIN-54).

The SV ranking matched exact type names: a ``DEL:ME``, a ``DUP:TANDEM`` and every CNV,
whatever its copy number, scored as an unknown event, while the SNV + SV second-hit badge
counted a ``DEL:ME`` and a CNV loss as copy losses (#840). Both now read one rule
(``sv_copy_change.copy_change``).

The family here is its own (a new id each run): a trio whose son and mother each have a
HiFiCNV file, imported as a package, each record over one gene with a pLI of 0.99. The family
SV search ranked by phenotype priority (``GET /families/{family_id}/structural-variants`` with
``prioritize=true``) reads them back, each call with the copy number ClickHouse stores for it
and the son's sex from the PED, and scores:

* a ``DEL:ME`` and a CNV with one copy as deletions, haploinsufficient in this gene (0.9);
* a CNV with three copies and a ``DUP:TANDEM`` as duplications (0.5), and a ``DUP`` as before;
* the son's CNV with one copy of chrX outside the PARs as neither (0.4): he has one copy
  there; his mother's, one of her two copies, as a deletion (0.9).

The list serves each SV with that change (``copy_change``), and so does each member's genome
track (``track_mode=true`` with ``sample``, as the genome overview asks), from the member's own
call; the track leaves it out where there is none. The genome, chromosome and Circos views draw
an SV by it: they drew only the exact types DEL, DUP, INV, INS and BND, so none of these CNVs,
nor the ``DEL:ME`` or the ``DUP:TANDEM``, was drawn (CLIN-54).

Everything runs in ONE event loop, with an in-process ``httpx.ASGITransport`` client for the
API (see test_e2e_api_contract.py for why).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

_HEADER = (
    "##fileformat=VCFv4.2\n"
    '##INFO=<ID=SVTYPE,Number=1,Type=String,Description="Type of the SV.">\n'
    '##INFO=<ID=SVLEN,Number=1,Type=Integer,Description="Length of the SV">\n'
    '##INFO=<ID=END,Number=1,Type=Integer,Description="End position">\n'
    '##INFO=<ID=pLI,Number=1,Type=Float,Description="pLI of the overlapped gene">\n'
    '##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence annotations from Ensembl VEP. '
    'Format: Allele|Consequence|IMPACT|SYMBOL|Gene">\n'
    '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
    '##FORMAT=<ID=CN,Number=1,Type=Float,Description="Copy number">\n'
    # HiFiCNV names its column after its own sample slot.
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSample0\n"
)

# Each member's calls: (chromosome, start, type, genotype, copy number), the pathogenicity
# the ranking gives each, and the copy change served with it (None: neither). chrX 50-60 Mb
# lies outside the PARs on GRCh38.
_Call = tuple[str, int, str, str, int, float, str | None]
_SON_CALLS: list[_Call] = [
    ("chr1", 1_000_000, "DEL:ME", "0/1", 1, 0.9, "loss"),
    ("chr1", 2_000_000, "CNV", "0/1", 1, 0.9, "loss"),
    ("chr1", 3_000_000, "CNV", "0/1", 3, 0.5, "gain"),
    ("chr1", 4_000_000, "DUP:TANDEM", "0/1", 3, 0.5, "gain"),
    ("chr1", 5_000_000, "DUP", "0/1", 3, 0.5, "gain"),
    ("chrX", 50_000_000, "CNV", "1", 1, 0.4, None),
]
_MOTHER_CALLS: list[_Call] = [
    ("chrX", 60_000_000, "CNV", "0/1", 1, 0.9, "loss"),
]
_LENGTH = 100_000


def _cnv_vcf(calls: list[_Call]) -> str:
    lines = []
    for index, (chrom, start, sv_type, genotype, copies, _score, _change) in enumerate(calls, start=1):
        end = start + _LENGTH
        lines.append(
            f"{chrom}\t{start}\t.\tN\t<{sv_type}>\t50\tPASS\t"
            f"SVTYPE={sv_type};END={end};SVLEN={_LENGTH};pLI=0.99;"
            f"CSQ={sv_type}|feature_truncation|HIGH|CNVGENE{index}|ENSG{index:011d}"
            f"\tGT:CN\t{genotype}:{copies}\n"
        )
    return _HEADER + "".join(lines)


def _ped(family_id: str) -> str:
    father, mother, son = (f"{role}_{family_id}" for role in ("FATHER", "MOTHER", "SON"))
    return (
        f"{family_id}\t{father}\t0\t0\t1\t1\n"
        f"{family_id}\t{mother}\t0\t0\t2\t1\n"
        f"{family_id}\t{son}\t{father}\t{mother}\t1\t2\n"
    )


def _write_package(root: Path, family_id: str) -> None:
    """The son's and the mother's own HiFiCNV files."""
    root.mkdir(parents=True)
    (root / "family.ped").write_text(_ped(family_id), encoding="utf-8")
    (root / "cnv").mkdir()
    entries = []
    for role, calls in (("SON", _SON_CALLS), ("MOTHER", _MOTHER_CALLS)):
        sample = f"{role}_{family_id}"
        (root / "cnv" / f"{sample}.vcf").write_text(_cnv_vcf(calls), encoding="utf-8")
        entries.append(f"      {sample}: {{vcf: cnv/{sample}.vcf}}")
    lines = [
        "schema_version: 1",
        f"family_id: {family_id}",
        "ped: family.ped",
        "datasets:",
        "  cnv:",
        "    per_sample:",
        *entries,
    ]
    (root / "manifest.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def _exercise(base: Path, family_id: str) -> dict:
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

    root = base / "packages" / family_id
    _write_package(root, family_id)
    async with sessionmaker() as session:
        result = await package_import.execute_family_package_import(
            session,
            folder_path=str(root),
            project_id=project_id,
            dry_run=False,
            user=admin,
            conflict_mode="cancel",
        )
    out: dict = {
        "import": {
            "completed": result.completed,
            "error": result.error,
            "datasets": {d.dataset_type: (d.status, d.message) for d in result.datasets},
        }
    }

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        token = await _harness.login_admin_token(ac)
        ac.headers["Authorization"] = f"Bearer {token}"
        response = await ac.get(
            f"/api/families/{family_id}/structural-variants",
            params={"prioritize": "true", "page_size": 50},
        )
        out["ranked"] = {"status": response.status_code, "text": response.text}
        if response.status_code == 200:
            out["ranked"]["json"] = response.json()
        # Each member's genome track, as the genome overview asks for it.
        for role in ("SON", "MOTHER"):
            response = await ac.get(
                f"/api/families/{family_id}/structural-variants",
                params={"page_size": 0, "track_mode": "true", "sample": f"{role}_{family_id}"},
            )
            out[f"track_{role}"] = {"status": response.status_code, "text": response.text}
            if response.status_code == 200:
                out[f"track_{role}"]["json"] = response.json()
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("sv_ranking_copy_change")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base / "packages")])
    try:
        return _harness.run_async(lambda: _exercise(base, f"CNV_{uuid4().hex[:8].upper()}"))
    finally:
        mp.undo()


def test_the_cnv_files_import(run) -> None:
    assert run["import"]["completed"] is True, run["import"]
    assert run["import"]["datasets"]["cnv"][0] == "imported", run["import"]["datasets"]


def test_the_sv_ranking_scores_each_sv_by_its_copy_number_change(run) -> None:
    assert run["ranked"]["status"] == 200, run["ranked"]["text"]
    page = run["ranked"]["json"]
    scores = {
        (variant["chr"].removeprefix("chr"), variant["start"], variant["type"]): (
            variant["priority"]["pathogenicity_score"]
        )
        for variant in page["variants"]
    }
    expected = {
        (chrom.removeprefix("chr"), start, sv_type): score
        for chrom, start, sv_type, _genotype, _copies, score, _change in [*_SON_CALLS, *_MOTHER_CALLS]
    }
    assert scores == expected


def _served_changes(calls: list[_Call]) -> dict[tuple[str, int, str], str | None]:
    return {(chrom.removeprefix("chr"), start, sv_type): change for chrom, start, sv_type, *_rest, change in calls}


def test_the_sv_list_serves_each_sv_with_the_copy_change_the_ranking_scores_on(run) -> None:
    assert run["ranked"]["status"] == 200, run["ranked"]["text"]
    served = {
        (variant["chr"].removeprefix("chr"), variant["start"], variant["type"]): variant["copy_change"]
        for variant in run["ranked"]["json"]["variants"]
    }
    assert served == _served_changes([*_SON_CALLS, *_MOTHER_CALLS])


@pytest.mark.parametrize(("role", "calls"), [("SON", _SON_CALLS), ("MOTHER", _MOTHER_CALLS)])
def test_each_members_genome_track_serves_the_copy_change_of_the_members_own_calls(
    run, role: str, calls: list[_Call]
) -> None:
    track = run[f"track_{role}"]
    assert track["status"] == 200, track["text"]
    served = {
        (variant["chr"].removeprefix("chr"), variant["start"], variant["type"]): variant.get("copy_change")
        for variant in track["json"]["variants"]
    }
    assert served == _served_changes(calls)
