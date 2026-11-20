"""Short-read and then long-read WGS of the same DNA, imported into one family, E2E.

The owner's decisions of 2026-11-19: a family may be sequenced short-read and later long-read on
the same DNA numbers, each analysis delivered as a package of its own and signed out on its own;
each small-variant callset is named by the caller that made it. Over real Postgres and
ClickHouse, a new family each run:

1. a short-read WGS package (``analysis_type: short_read_wgs``): the trio's joint GATK
   HaplotypeCaller callset, whose header names its caller, and the proband's CRAM;
2. a long-read WGS package (``analysis_type: long_read_wgs``) of the same three samples, a joint
   DeepVariant callset and the proband's CRAM, imported with update;
3. a long-read adaptive-sampling package of the same samples whose callset is DeepVariant's too:
   two analyses of one family never share a storage source (the owner's decision of 2026-11-20).

Then:

* the third import is refused before anything is written, naming the shared callset;
* each callset is stored under its caller's name (``haplotypecaller``, ``deepvariant``), a
  variant both called once per callset: the update does not skip the long-read callset because
  the family holds the short-read one;
* each package record lists its own callset among its sources;
* the family keeps the run record (pipeline parameters) of each analysis's pipeline;
* the proband keeps an alignment of each analysis: the genome browser's manifest lists both,
  each naming its analysis, and each is served from its own package folder;
* a variant only the long-read callset holds, tagged for the report, is in the long-read
  analysis's signed record and not in the short-read one's, and each record's Sample QC reads its
  own callset.

Everything runs in ONE event loop, with an in-process ``httpx.ASGITransport`` client for the
API (see test_e2e_api_contract.py for why).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import yaml

from backend.tests._kept_manifest import kept_manifest

pytestmark = pytest.mark.integration

_COLUMNS = "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{}\t{}\t{}\n"
_HEADERS = {
    "short_read_wgs": '##GATKCommandLine=<ID=HaplotypeCaller,CommandLine="HaplotypeCaller -R ref.fa",Version="4.5.0.0">\n',
    "long_read_wgs": "##DeepVariant_version=1.6.1\n",
}
# Both callsets call 1-1000-A-G; only the short-read one 1-2000-C-T, only the long-read one 1-3000-G-A.
# The short-read callset calls the (unaffected) mother heterozygous, the long-read one reference:
# each analysis's list and inheritance filter read their own callset's calls.
_MOTHER_GT = {"short_read_wgs": "0/1", "long_read_wgs": "0/0"}
_RECORDS = {
    "short_read_wgs": [(1000, "A", "G"), (2000, "C", "T")],
    "long_read_wgs": [(1000, "A", "G"), (3000, "G", "A")],
}
LONG_READ_ONLY = "1-3000-G-A"
SIGN_OUT = {
    "acknowledge_drift": True,
    "drift_acknowledgement_reason": "e2e: the reported variant holds no ACMG classification",
    "acknowledge_qc": True,
    "qc_acknowledgement_reason": "e2e: two variants are too few for the trio's QC",
}


def _package(
    root: Path, family_id: str, analysis: str, trio: tuple[str, str, str], *, calls_of: str | None = None
) -> Path:
    """The package of ``analysis``, its callset that of ``calls_of`` (default: its own)."""
    proband, mother, father = trio
    caller = calls_of or analysis
    root.mkdir(parents=True)
    (root / "family.ped").write_text(
        f"{family_id}\t{proband}\t{father}\t{mother}\t1\t2\n"
        f"{family_id}\t{mother}\t0\t0\t2\t1\n"
        f"{family_id}\t{father}\t0\t0\t1\t1\n",
        encoding="utf-8",
    )
    lines = ["##fileformat=VCFv4.2\n", "##contig=<ID=chr1>\n", _HEADERS[caller], _COLUMNS.format(proband, mother, father)]
    lines += [
        f"chr1\t{pos}\t.\t{ref}\t{alt}\t50\tPASS\t.\tGT:DP\t0/1:30\t{_MOTHER_GT[caller]}:30\t0/0:30\n"
        for pos, ref, alt in _RECORDS[caller]
    ]
    (root / "family.vcf").write_text("".join(lines), encoding="utf-8")
    # The run's parameters (params_*.json), as Nextflow writes them.
    (root / "params.json").write_text(
        '{"genome": "GRCh38", "snv_caller": "%s"}' % ("haplotypecaller" if caller == "short_read_wgs" else "deepvariant"),
        encoding="utf-8",
    )
    (root / "bams").mkdir()
    (root / "bams" / f"{proband}.cram").write_text(f"{analysis} reads of the proband", encoding="utf-8")
    (root / "bams" / f"{proband}.cram.crai").write_text(f"{analysis} index of the proband", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "family_id": family_id,
        "analysis_type": analysis,
        "ped": "family.ped",
        "datasets": {
            "snv": {"family_vcf": "family.vcf"},
            "alignments": {"per_sample": {proband: {"file": f"bams/{proband}.cram", "index": f"bams/{proband}.cram.crai"}}},
            "pipeline_info": {"params": "params.json"},
        },
    }
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return root


async def _exercise(base: Path, family_id: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.clickhouse import execute_clickhouse, init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services.clickhouse_variant_ids import _small_table_name
    from backend.app.services.family_package_common import ImportRefused
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness
    from backend.tests.e2e._harness import ensure_postgres_schema

    await ensure_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    # Sample IDs are unique across families: the same DNA numbers in both packages.
    trio = tuple(f"{role}_{family_id}" for role in ("PROBAND", "MOTHER", "FATHER"))
    out: dict[str, Any] = {"imports": {}}
    for analysis, mode in (("short_read_wgs", "cancel"), ("long_read_wgs", "update")):
        root = _package(base / family_id / analysis, family_id, analysis, trio)  # type: ignore[arg-type]
        async with sessionmaker() as session:
            result = await package_import.execute_family_package_import(
                session,
                folder_path=str(root),
                manifest_text=kept_manifest(root),
                project_id=project_id,
                dry_run=False,
                user=admin,
                conflict_mode=mode,
            )
        out["imports"][analysis] = {
            "completed": result.completed,
            "error": result.error,
            "datasets": {d.dataset_type: (d.status, d.message) for d in result.datasets},
        }
    if not all(item["completed"] for item in out["imports"].values()):
        return out
    shared = _package(
        base / family_id / "long_read_adaptive_sampling", family_id, "long_read_adaptive_sampling", trio,  # type: ignore[arg-type]
        calls_of="long_read_wgs",
    )
    # Refused before it writes anything: the import raises ImportRefused, which its job reports.
    async with sessionmaker() as session:
        try:
            refused = await package_import.execute_family_package_import(
                session,
                folder_path=str(shared),
                manifest_text=kept_manifest(shared),
                project_id=project_id,
                dry_run=False,
                user=admin,
                conflict_mode="overwrite",
            )
            out["shared"] = {"completed": refused.completed, "error": refused.error}
        except ImportRefused as refusal:
            out["shared"] = {"completed": False, "error": str(refusal)}

    async with sessionmaker() as session:
        family = (
            await session.execute(
                text("SELECT id::text AS id, metadata FROM families WHERE family_id = :f"), {"f": family_id}
            )
        ).mappings().one()
    out["records"] = family["metadata"]["package_imports"]
    out["family_pipeline"] = family["metadata"].get("pipeline")
    rows = await execute_clickhouse(
        f"SELECT variantId, source FROM {_small_table_name(_harness.ASSEMBLY, 'entries')} "
        "WHERE family_guid = %(family)s AND sign = 1",
        {"family": family["id"]},
    )
    out["stored"] = sorted((str(variant_id), str(source)) for variant_id, source in rows)

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"
        api = f"/api/families/{family_id}"
        manifest = await ac.get(f"/api/cram/{family_id}/manifest", params={"sample": trio[0]})
        out["alignments"] = [
            (entry["analysis_type"], (await ac.get(f"/api{entry['url']}")).text, (await ac.get(f"/api{entry['index_url']}")).text)
            for entry in manifest.json()
        ]
        # Each analysis's small variants are read on their own: a reading that names none of the
        # family's two analyses with small variants is refused.
        unnamed = await ac.get(f"{api}/small-variants", params={"page_size": 50})
        out["unnamed"] = (unnamed.status_code, unnamed.json().get("detail"))
        out["lists"] = {}
        for analysis in ("short_read_wgs", "long_read_wgs"):
            listed = await ac.get(f"{api}/small-variants", params={"page_size": 50, "analysis": analysis})
            dominant = await ac.get(
                f"{api}/small-variants",
                params={"page_size": 50, "analysis": analysis, "inheritance": "de_novo_dominant"},
            )
            out["lists"][analysis] = {
                "status": listed.status_code,
                "rows": sorted(
                    (row["_id"], row["source"], {gt["sample"]: gt["gt"] for gt in row["genotypes"]}[trio[1]])
                    for row in listed.json().get("variants", [])
                ),
                "de_novo_dominant": sorted(row["_id"] for row in dominant.json().get("variants", [])),
            }
        tagged = await ac.put(
            f"{api}/small-variants/{LONG_READ_ONLY}/review", json={"tags": ["report"]}, params={"analysis": "long_read_wgs"}
        )
        out["tagged"] = tagged.status_code
        for analysis in ("short_read_wgs", "long_read_wgs"):
            signed = await _harness.sign_out(ac, f"{api}/report/sign-out", {**SIGN_OUT, "analysis_type": analysis})
            out[analysis] = {"status": signed.status_code, "json": signed.json()}
            version = (out[analysis]["json"] or {}).get("version")
            if version is not None:
                record = await ac.get(f"{api}/report/sign-outs/{version}")
                out[analysis]["snapshot"] = record.json().get("snapshot")
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("short_and_long_read")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base)])
    try:
        return _harness.run_async(lambda: _exercise(base, f"SRLR_{uuid4().hex[:8].upper()}"))
    finally:
        mp.undo()


def test_both_packages_are_imported_into_the_one_family(run) -> None:
    for analysis, item in run["imports"].items():
        assert item["completed"] is True, (analysis, item)
        assert item["datasets"]["snv"][0] == "imported", (analysis, item)


def test_a_third_analysis_sharing_a_callers_name_is_refused_before_any_write(run) -> None:
    assert run["shared"]["completed"] is False
    assert (
        "The long-read adaptive sampling package would store its small variants as 'deepvariant', which "
        "this family's long-read WGS data already holds." in str(run["shared"]["error"])
    )
    # Nothing of it was written: no record, and the stored rows are the two analyses' own.
    assert sorted(run["records"]) == ["long_read_wgs", "short_read_wgs"]


def test_each_callset_is_stored_under_its_callers_name(run) -> None:
    assert run["stored"] == [
        ("1-1000-A-G", "deepvariant"),
        ("1-1000-A-G", "haplotypecaller"),
        ("1-2000-C-T", "haplotypecaller"),
        ("1-3000-G-A", "deepvariant"),
    ]


def test_the_list_of_a_family_with_two_callsets_is_read_one_analysis_at_a_time(run) -> None:
    status, detail = run["unnamed"]
    assert status == 422 and "name the analysis" in detail
    short_read, long_read = run["lists"]["short_read_wgs"], run["lists"]["long_read_wgs"]
    assert short_read["status"] == long_read["status"] == 200
    # The variant both called is listed in each analysis with its own callset's calls.
    assert short_read["rows"] == [("1-1000-A-G", "haplotypecaller", "0/1"), ("1-2000-C-T", "haplotypecaller", "0/1")]
    assert long_read["rows"] == [("1-1000-A-G", "deepvariant", "0/0"), ("1-3000-G-A", "deepvariant", "0/0")]


def test_an_inheritance_filter_reads_the_analysis_s_own_calls(run) -> None:
    # The short-read callset calls the unaffected mother a carrier of every variant: none is de
    # novo or dominant there. The long-read callset calls her reference: both its variants are.
    assert run["lists"]["short_read_wgs"]["de_novo_dominant"] == []
    assert run["lists"]["long_read_wgs"]["de_novo_dominant"] == ["1-1000-A-G", "1-3000-G-A"]


def test_each_package_record_lists_its_own_callset(run) -> None:
    records = run["records"]
    assert records["short_read_wgs"]["sources"]["small_variants"] == ["haplotypecaller"]
    assert records["long_read_wgs"]["sources"]["small_variants"] == ["deepvariant"]


def test_each_analysiss_record_holds_its_own_variants_and_qc(run) -> None:
    assert run["tagged"] == 200
    short_read, long_read = run["short_read_wgs"], run["long_read_wgs"]
    assert short_read["status"] == 200 and long_read["status"] == 200, (short_read, long_read)
    assert short_read["snapshot"]["analysis_type"] == "short_read_wgs"
    assert short_read["snapshot"]["reported_variants"] == []
    assert [entry["variant_id"] for entry in long_read["snapshot"]["reported_variants"]] == [LONG_READ_ONLY]
    assert short_read["snapshot"]["sample_qc"]["genotype_source"] == "haplotypecaller"
    assert long_read["snapshot"]["sample_qc"]["genotype_source"] == "deepvariant"


def test_the_proband_keeps_an_alignment_of_each_analysis(run) -> None:
    assert run["alignments"] == [
        ("long_read_wgs", "long_read_wgs reads of the proband", "long_read_wgs index of the proband"),
        ("short_read_wgs", "short_read_wgs reads of the proband", "short_read_wgs index of the proband"),
    ]


def test_the_family_keeps_the_run_record_of_each_analysis(run) -> None:
    assert run["family_pipeline"] == {
        "long_read_wgs": {"genome": "GRCh38", "snv_caller": "deepvariant"},
        "short_read_wgs": {"genome": "GRCh38", "snv_caller": "haplotypecaller"},
    }
