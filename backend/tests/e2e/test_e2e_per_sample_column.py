"""A per-sample file is read from its own sample's column (E2E).

A package's per-sample TRGT, mitochondrial and HiFiCNV files, and the admin TRGT upload
(``POST /repeat-expansions/upload/{sample_id}``), each store one sample's calls. They used
to take the file's first (TRGT) or only (mito, HiFiCNV) column as that sample's whatever it
was named: a family TRGT VCF uploaded for the child stored the mother's repeat sizes as the
child's, and the mother's file under the child's entry -- a swapped path, a wrong upload --
replaced the child's calls with hers without a word.

The family here is its own (a new id each run):

* a package import with each trio member's own TRGT (``<sample>_sort``), chrM
  (``<sample>``) and HiFiCNV (``Sample0``) file stores each member's own calls;
* a second import whose child entries point at the mother's chrM and HiFiCNV files fails
  those datasets, and the child's stored calls stay;
* uploading the mother's TRGT file for the child, with ``overwrite=true``, is refused (400)
  and the child's stored calls stay -- the overwrite's delete is rolled back;
* uploading the family TRGT VCF for the child stores the child's column only, and leaves
  the mother's and the father's calls as they were.

Everything runs in ONE event loop, with an in-process ``httpx.ASGITransport`` client for
the API (see test_e2e_api_contract.py for why).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration


def _trgt_vcf(columns: list[str], counts: list[int]) -> str:
    """A TRGT VCF with one HTT record, each column's repeat count in ``counts``."""
    calls = "\t".join(f"0/0:{count}_0" for count in counts)
    return (
        "##fileformat=VCFv4.2\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t" + "\t".join(columns) + "\n"
        f"chr4\t3074876\t.\tA\t<STR>\t.\tPASS\tEND=3074933;TRID=HD_HTT;MOTIFS=CAG,CAA\tGT:MC\t{calls}\n"
    )


def _mito_vcf(column: str, position: int) -> str:
    """A chrM VCF with one heteroplasmic call at ``position``: each member's own site."""
    return (
        "##fileformat=VCFv4.2\n"
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Read depth">\n'
        '##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allele depths">\n'
        '##FORMAT=<ID=VAF,Number=A,Type=Float,Description="Variant allele fraction">\n'
        f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{column}\n"
        f"chrM\t{position}\t.\tA\tG\t30\tPASS\t.\tGT:DP:AD:VAF\t0/1:500:400,100:0.2\n"
    )


def _cnv_vcf(start: int) -> str:
    """A HiFiCNV VCF (its column is its own sample slot) with one deletion at ``start``."""
    return (
        "##fileformat=VCFv4.2\n"
        '##INFO=<ID=SVTYPE,Number=1,Type=String,Description="Type of the SV.">\n'
        '##INFO=<ID=END,Number=1,Type=Integer,Description="End position">\n'
        '##FORMAT=<ID=CN,Number=1,Type=Float,Description="Copy number">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSample0\n"
        f"chr1\t{start}\t.\tN\t<DEL>\t57\tPASS\tSVTYPE=DEL;END={start + 50000}\tGT:CN\t0/1:1\n"
    )


def _ped(family_id: str) -> str:
    father, mother, proband = (f"{role}_{family_id}" for role in ("FATHER", "MOTHER", "PROBAND"))
    return (
        f"{family_id}\t{father}\t0\t0\t1\t1\n"
        f"{family_id}\t{mother}\t0\t0\t2\t1\n"
        f"{family_id}\t{proband}\t{father}\t{mother}\t1\t2\n"
    )


# Each member's own chrM site and HiFiCNV deletion start, so a stored call shows whose
# file it came from.
_MITO_SITE = {"FATHER": 263, "MOTHER": 16519, "PROBAND": 9000}
_CNV_START = {"FATHER": 3_000_000, "MOTHER": 1_000_000, "PROBAND": 2_000_000}


def _write_first_package(root: Path, family_id: str) -> None:
    """Each trio member's own TRGT, chrM and HiFiCNV file."""
    root.mkdir(parents=True)
    (root / "family.ped").write_text(_ped(family_id), encoding="utf-8")
    for folder in ("repeats", "mito", "cnv"):
        (root / folder).mkdir()
    datasets = {"repeats_trgt": [], "mito": [], "cnv": []}
    counts = {"FATHER": 20, "MOTHER": 17, "PROBAND": 45}
    for role in ("FATHER", "MOTHER", "PROBAND"):
        sample = f"{role}_{family_id}"
        (root / "repeats" / f"{sample}_tr.vcf").write_text(
            _trgt_vcf([f"{sample}_sort"], [counts[role]]), encoding="utf-8"
        )
        (root / "mito" / f"{sample}.vcf").write_text(_mito_vcf(sample, _MITO_SITE[role]), encoding="utf-8")
        (root / "cnv" / f"{sample}.vcf").write_text(_cnv_vcf(_CNV_START[role]), encoding="utf-8")
        datasets["repeats_trgt"].append(f"      {sample}: {{file: repeats/{sample}_tr.vcf}}")
        datasets["mito"].append(f"      {sample}: {{vcf: mito/{sample}.vcf}}")
        datasets["cnv"].append(f"      {sample}: {{vcf: cnv/{sample}.vcf}}")
    lines = ["schema_version: 1", f"family_id: {family_id}", "ped: family.ped", "datasets:"]
    for dataset, entries in datasets.items():
        lines += [f"  {dataset}:", "    per_sample:", *entries]
    (root / "manifest.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_swapped_package(root: Path, family_id: str) -> None:
    """The child's chrM and HiFiCNV entries point at the mother's files, named after her."""
    root.mkdir(parents=True)
    (root / "family.ped").write_text(_ped(family_id), encoding="utf-8")
    (root / "mito").mkdir()
    (root / "cnv").mkdir()
    mother, proband = f"MOTHER_{family_id}", f"PROBAND_{family_id}"
    (root / "mito" / f"{mother}.vcf").write_text(_mito_vcf(mother, 3243), encoding="utf-8")
    # The mother's HiFiCNV file, its column renamed after her (as a lab's rename would).
    (root / "cnv" / f"{mother}.vcf").write_text(
        _cnv_vcf(5_000_000).replace("FORMAT\tSample0", f"FORMAT\t{mother}"), encoding="utf-8"
    )
    lines = [
        "schema_version: 1",
        f"family_id: {family_id}",
        "ped: family.ped",
        "datasets:",
        "  mito:",
        "    per_sample:",
        f"      {proband}: {{vcf: mito/{mother}.vcf}}",
        "  cnv:",
        "    per_sample:",
        f"      {proband}: {{vcf: cnv/{mother}.vcf}}",
    ]
    (root / "manifest.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def _stored(session, family_uuid: str) -> dict[str, dict]:
    """Each member's stored calls: TRGT HTT repeat counts (with their VCF column), chrM sites
    and HiFiCNV deletion starts."""
    from sqlalchemy import text

    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.services.clickhouse_variant_ids import _small_table_name
    from backend.app.services.clickhouse_variant_storage import fetch_family_structural_variant_rows
    from backend.tests.e2e import _harness

    samples = {
        str(row["id"]): str(row["sample_id"])
        for row in (
            await session.execute(
                text("SELECT id::text AS id, sample_id FROM samples WHERE family_id = CAST(:f AS uuid)"),
                {"f": family_uuid},
            )
        ).mappings()
    }
    out: dict[str, dict] = {name: {"trgt": None, "mito": set(), "cnv": set()} for name in samples.values()}
    trgt_rows = (
        await session.execute(
            text(
                """
                SELECT sample_id::text AS sample_uuid, alleles, metadata ->> 'vcf_sample' AS vcf_sample
                FROM repeat_expansions
                WHERE family_id = CAST(:f AS uuid) AND source = 'trgt'
                """
            ),
            {"f": family_uuid},
        )
    ).mappings().all()
    for row in trgt_rows:
        counts = [int(a["repeat_count"]) for a in row["alleles"] if a.get("repeat_count") is not None]
        out[samples[row["sample_uuid"]]]["trgt"] = (counts, row["vcf_sample"])

    def name_of(sample_id: str) -> str:
        return samples.get(sample_id, sample_id)

    mito_rows = await execute_clickhouse(
        f"SELECT pos, `calls.sampleId` FROM {_small_table_name(_harness.ASSEMBLY, 'entries')} "
        "WHERE family_guid = %(f)s AND source = 'mito' AND sign = 1",
        {"f": family_uuid},
    )
    for pos, sample_ids in mito_rows:
        for sample_id in sample_ids:
            out[name_of(str(sample_id))]["mito"].add(int(pos))
    for row in await fetch_family_structural_variant_rows(_harness.ASSEMBLY, family_uuid, source="hificnv"):
        for call in row.record.calls:
            out[name_of(call.sample)]["cnv"].add(row.record.start)
    return out


async def _exercise(base: Path, family_id: str) -> dict:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

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

    father, mother, proband = (f"{role}_{family_id}" for role in ("FATHER", "MOTHER", "PROBAND"))
    out: dict = {"samples": {"father": father, "mother": mother, "proband": proband}}

    async def run_import(folder: Path, conflict_mode: str) -> dict:
        async with sessionmaker() as session:
            result = await package_import.execute_family_package_import(
                session,
                folder_path=str(folder),
                project_id=project_id,
                dry_run=False,
                user=admin,
                conflict_mode=conflict_mode,
            )
        return {
            "completed": result.completed,
            "error": result.error,
            "datasets": {d.dataset_type: (d.status, d.message) for d in result.datasets},
        }

    first = base / "first" / family_id
    _write_first_package(first, family_id)
    out["first"] = await run_import(first, "cancel")
    async with sessionmaker() as session:
        family_uuid = (
            await session.execute(text("SELECT id::text FROM families WHERE family_id = :f"), {"f": family_id})
        ).scalar_one()
        out["after_first"] = await _stored(session, family_uuid)

    swapped = base / "swapped" / family_id
    _write_swapped_package(swapped, family_id)
    out["swapped"] = await run_import(swapped, "overwrite")
    async with sessionmaker() as session:
        out["after_swapped"] = await _stored(session, family_uuid)

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        token = await _harness.login_admin_token(ac)
        ac.headers["Authorization"] = f"Bearer {token}"

        async def upload(text_value: str, filename: str) -> dict:
            response = await ac.post(
                f"/api/repeat-expansions/upload/{proband}",
                params={"overwrite": "true"},
                files={"file": (filename, text_value.encode("utf-8"), "text/plain")},
            )
            return {"status": response.status_code, "json": response.json()}

        out["wrong_member"] = await upload(_trgt_vcf([f"{mother}_sort"], [17]), "mother_tr.vcf")
        async with sessionmaker() as session:
            out["after_wrong_member"] = await _stored(session, family_uuid)

        out["family_vcf"] = await upload(
            _trgt_vcf([f"{mother}_sort", f"{proband}_sort", f"{father}_sort"], [18, 50, 21]),
            "family.trgt.vcf",
        )
        async with sessionmaker() as session:
            out["after_family_vcf"] = await _stored(session, family_uuid)
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("per_sample_column")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base / "first"), str(base / "swapped")])
    try:
        return _harness.run_async(lambda: _exercise(base, f"COL_{uuid4().hex[:8].upper()}"))
    finally:
        mp.undo()


def _expected_first(samples: dict[str, str]) -> dict[str, dict]:
    counts = {"father": 20, "mother": 17, "proband": 45}
    return {
        name: {
            "trgt": ([counts[role]], f"{name}_sort"),
            "mito": {_MITO_SITE[role.upper()]},
            "cnv": {_CNV_START[role.upper()]},
        }
        for role, name in samples.items()
    }


def test_each_members_own_files_are_read_from_their_own_column(run) -> None:
    assert run["first"]["completed"] is True, run["first"]
    for dataset in ("repeats_trgt", "mito", "cnv"):
        assert run["first"]["datasets"][dataset][0] == "imported", run["first"]["datasets"]
    assert run["after_first"] == _expected_first(run["samples"])


def test_the_mothers_mito_and_cnv_files_under_the_childs_entry_are_refused(run) -> None:
    mother = run["samples"]["mother"]
    datasets = run["swapped"]["datasets"]
    for dataset in ("mito", "cnv"):
        status, message = datasets[dataset]
        assert status == "failed", datasets
        assert f"'{mother}' is {mother}" in message, message
    # Nothing of the mother's was stored as the child's, and the child's own calls stay.
    assert run["after_swapped"] == run["after_first"]


def test_another_members_trgt_upload_is_refused_and_the_stored_calls_stay(run) -> None:
    mother = run["samples"]["mother"]
    assert run["wrong_member"]["status"] == 400, run["wrong_member"]
    assert f"'{mother}_sort' is {mother}" in run["wrong_member"]["json"]["detail"]
    # The overwrite deleted the child's calls before the file was read; the refusal
    # rolls that delete back.
    assert run["after_wrong_member"] == run["after_first"]


def test_a_family_trgt_vcf_uploaded_for_the_child_stores_the_childs_column_only(run) -> None:
    proband = run["samples"]["proband"]
    assert run["family_vcf"]["status"] == 200, run["family_vcf"]
    expected = _expected_first(run["samples"])
    expected[proband]["trgt"] = ([50], f"{proband}_sort")
    assert run["after_family_vcf"] == expected
