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
  own callset;
* the small-variant list and its inheritance filter read one analysis at a time: a reading that
  names neither analysis is refused (422), and the variant both called is listed in each with its
  own callset's calls;
* a review is one analysis's (the owner's decision of 2026-11-20): a review save that names no
  analysis is refused (422); a variant both callsets hold, reviewed (tagged for the report) in the
  long-read analysis only, is in the long-read record and not in the short-read one, and its list
  row carries the review in the long-read analysis only; then reviewed in both with another
  classification in each, each record holds its own; each review change's audit event names its
  analysis.

A second family is sequenced short-read as a trio and then long-read for the proband alone, the
long-read package's PED listing the parents (the owner's decision of 2026-11-20: the de novo /
dominant filter keeps reading their missing calls as reference, and the list says so):

* the long-read analysis's list names the parents as members without calls, each read by an
  inheritance filter as unaffected; the short-read analysis's names none;
* its de novo / dominant filter keeps a variant the short-read callset shows the mother passed on,
  which the short-read analysis's filter leaves out;
* its export names the parents in its last column, on every row; the short-read export's is empty.

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
BOTH = "1-1000-A-G"
SIGN_OUT = {
    "acknowledge_drift": True,
    "drift_acknowledgement_reason": "e2e: the reported variant holds no ACMG classification",
    "acknowledge_qc": True,
    "qc_acknowledgement_reason": "e2e: two variants are too few for the trio's QC",
}


def _package(
    root: Path,
    family_id: str,
    analysis: str,
    trio: tuple[str, str, str],
    *,
    calls_of: str | None = None,
    called: tuple[str, ...] | None = None,
) -> Path:
    """The package of ``analysis``, its callset that of ``calls_of`` (default: its own), with the
    calls of ``called`` (default: the trio); its PED lists the trio whatever its callset holds."""
    proband, mother, father = trio
    caller = calls_of or analysis
    columns = called or trio
    genotypes = {proband: "0/1:30", mother: f"{_MOTHER_GT[caller]}:30", father: "0/0:30"}
    root.mkdir(parents=True)
    (root / "family.ped").write_text(
        f"{family_id}\t{proband}\t{father}\t{mother}\t1\t2\n"
        f"{family_id}\t{mother}\t0\t0\t2\t1\n"
        f"{family_id}\t{father}\t0\t0\t1\t1\n",
        encoding="utf-8",
    )
    lines = [
        "##fileformat=VCFv4.2\n",
        "##contig=<ID=chr1>\n",
        _HEADERS[caller],
        "\t".join(["#CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO", "FORMAT", *columns]) + "\n",
    ]
    lines += [
        f"chr1\t{pos}\t.\t{ref}\t{alt}\t50\tPASS\t.\tGT:DP\t" + "\t".join(genotypes[sample] for sample in columns) + "\n"
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
        unnamed_list = await ac.get(f"{api}/small-variants", params={"page_size": 50})
        out["unnamed_list"] = (unnamed_list.status_code, unnamed_list.json().get("detail"))
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
        review = f"{api}/small-variants/{{variant}}/review"
        # A review is one analysis's: a save that names none, of a family of two, is refused.
        unnamed = await ac.put(review.format(variant=BOTH), json={"tags": ["report"]})
        out["unnamed"] = {"status": unnamed.status_code, "json": unnamed.json()}
        tagged = await ac.put(
            review.format(variant=LONG_READ_ONLY), params={"analysis": "long_read_wgs"}, json={"tags": ["report"]}
        )
        out["tagged"] = tagged.status_code
        # The variant both callsets hold, reviewed in the long-read analysis only.
        both = await ac.put(
            review.format(variant=BOTH),
            params={"analysis": "long_read_wgs"},
            json={"tags": ["report"], "classification": "Likely pathogenic - class 4", "expected_updated_at": None},
        )
        out["both_long_read"] = {"status": both.status_code, "json": both.json()}
        out["list_rows"] = {}
        for analysis in ("short_read_wgs", "long_read_wgs"):
            listed = await ac.get(f"{api}/small-variants", params={"analysis": analysis, "page_size": 50})
            out["list_rows"][analysis] = {
                str(row["_id"]): (row.get("review") or {}).get("tags") for row in listed.json().get("variants", [])
            }
        out["first"] = await _sign_out_both(ac, api)
        # Then reviewed in the short-read analysis too, with another classification.
        short = await ac.put(
            review.format(variant=BOTH),
            params={"analysis": "short_read_wgs"},
            json={"tags": ["report"], "classification": "VUS - class 3", "expected_updated_at": None},
        )
        out["both_short_read"] = {"status": short.status_code, "json": short.json()}
        out["second"] = await _sign_out_both(ac, api)
        trail = await ac.get(f"{api}/clinical-audit", params={"limit": 200})
        out["trail"] = [
            (event["variant_id"], event["action"], event.get("analysis")) for event in trail.json().get("events", [])
        ]
    return out


async def _exercise_proband_only(base: Path, family_id: str) -> dict[str, Any]:
    """Short-read WGS of the trio, then long-read WGS of the proband alone whose PED lists the
    parents: each analysis's members without calls, its de novo / dominant filter and its export."""
    import csv
    import io

    from httpx import ASGITransport, AsyncClient

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness
    from backend.tests.e2e._harness import ensure_postgres_schema

    await ensure_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    trio = tuple(f"{role}_{family_id}" for role in ("PROBAND", "MOTHER", "FATHER"))
    out: dict[str, Any] = {"trio": trio, "imports": {}}
    for analysis, mode, called in (("short_read_wgs", "cancel", None), ("long_read_wgs", "update", trio[:1])):
        root = _package(base / family_id / analysis, family_id, analysis, trio, called=called)  # type: ignore[arg-type]
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
        out["imports"][analysis] = {"completed": result.completed, "error": result.error}
    if not all(item["completed"] for item in out["imports"].values()):
        return out

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"
        api = f"/api/families/{family_id}"
        for analysis in ("short_read_wgs", "long_read_wgs"):
            listed = await ac.get(f"{api}/small-variants", params={"page_size": 50, "analysis": analysis})
            dominant = await ac.get(
                f"{api}/small-variants",
                params={"page_size": 50, "analysis": analysis, "inheritance": "de_novo_dominant"},
            )
            exported = await ac.get(
                f"{api}/small-variants/export", params={"analysis": analysis, "inheritance": "de_novo_dominant"}
            )
            table = list(csv.reader(io.StringIO(exported.text)))
            out[analysis] = {
                "status": (listed.status_code, dominant.status_code, exported.status_code),
                "members_without_calls": listed.json().get("members_without_calls"),
                "de_novo_dominant": sorted(row["_id"] for row in dominant.json().get("variants", [])),
                "dominant_members_without_calls": dominant.json().get("members_without_calls"),
                "export_header": table[0] if table else [],
                "export_last_column": [row[-1] for row in table[1:]],
            }
    return out


async def _sign_out_both(ac: Any, api: str) -> dict[str, Any]:
    """Sign out each analysis; each record as served."""
    from backend.tests.e2e import _harness

    signed_out: dict[str, Any] = {}
    for analysis in ("short_read_wgs", "long_read_wgs"):
        signed = await _harness.sign_out(ac, f"{api}/report/sign-out", {**SIGN_OUT, "analysis_type": analysis})
        signed_out[analysis] = {"status": signed.status_code, "json": signed.json()}
        version = (signed_out[analysis]["json"] or {}).get("version")
        if version is not None:
            record = await ac.get(f"{api}/report/sign-outs/{version}")
            signed_out[analysis]["snapshot"] = record.json().get("snapshot")
    return signed_out


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


@pytest.fixture(scope="module")
def proband_only(tmp_path_factory) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("proband_only_long_read")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base)])
    try:
        return _harness.run_async(lambda: _exercise_proband_only(base, f"SRPO_{uuid4().hex[:8].upper()}"))
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
    status, detail = run["unnamed_list"]
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
    short_read, long_read = run["first"]["short_read_wgs"], run["first"]["long_read_wgs"]
    assert short_read["status"] == 200 and long_read["status"] == 200, (short_read, long_read)
    assert short_read["snapshot"]["analysis_type"] == "short_read_wgs"
    assert short_read["snapshot"]["reported_variants"] == []
    assert [entry["variant_id"] for entry in long_read["snapshot"]["reported_variants"]] == [BOTH, LONG_READ_ONLY]
    assert short_read["snapshot"]["sample_qc"]["genotype_source"] == "haplotypecaller"
    assert long_read["snapshot"]["sample_qc"]["genotype_source"] == "deepvariant"


def test_a_review_save_that_names_no_analysis_of_the_family_is_refused(run) -> None:
    assert run["unnamed"]["status"] == 422, run["unnamed"]
    assert "name the analysis" in str(run["unnamed"]["json"]["detail"])


def test_a_variant_reviewed_in_one_analysis_is_in_that_analysiss_record_only(run) -> None:
    # Both callsets hold the variant; only the long-read analysis reviewed it.
    assert run["both_long_read"]["status"] == 200, run["both_long_read"]
    assert run["list_rows"]["long_read_wgs"][BOTH] == ["report"]
    assert run["list_rows"]["short_read_wgs"][BOTH] is None
    short_read, long_read = run["first"]["short_read_wgs"], run["first"]["long_read_wgs"]
    assert BOTH not in [entry["variant_id"] for entry in short_read["snapshot"]["reported_variants"]]
    [entry] = [entry for entry in long_read["snapshot"]["reported_variants"] if entry["variant_id"] == BOTH]
    assert entry["classification"] == "Likely pathogenic - class 4"


def test_a_variant_reviewed_in_both_analyses_is_in_each_record_with_its_own_review(run) -> None:
    assert run["both_short_read"]["status"] == 200, run["both_short_read"]
    short_read, long_read = run["second"]["short_read_wgs"], run["second"]["long_read_wgs"]
    assert short_read["status"] == 200 and long_read["status"] == 200, (short_read, long_read)
    classified = {
        analysis: {entry["variant_id"]: entry["classification"] for entry in record["snapshot"]["reported_variants"]}
        for analysis, record in (("short_read_wgs", short_read), ("long_read_wgs", long_read))
    }
    assert classified["short_read_wgs"] == {BOTH: "VUS - class 3"}
    assert classified["long_read_wgs"] == {BOTH: "Likely pathogenic - class 4", LONG_READ_ONLY: None}


def test_each_review_change_is_audited_under_its_analysis(run) -> None:
    events = [event for event in run["trail"] if event[0] == BOTH and event[1] in ("tags", "classification")]
    assert sorted(events) == sorted(
        [
            (BOTH, "classification", "long_read_wgs"),
            (BOTH, "tags", "long_read_wgs"),
            (BOTH, "classification", "short_read_wgs"),
            (BOTH, "tags", "short_read_wgs"),
        ]
    )


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


def test_a_proband_only_long_read_package_whose_ped_lists_the_parents_is_imported(proband_only) -> None:
    for analysis, item in proband_only["imports"].items():
        assert item["completed"] is True, (analysis, item)


def test_the_long_read_list_names_the_parents_as_members_without_calls(proband_only) -> None:
    proband, mother, father = proband_only["trio"]
    short_read, long_read = proband_only["short_read_wgs"], proband_only["long_read_wgs"]
    assert short_read["status"] == long_read["status"] == (200, 200, 200)
    # The trio's short-read callset holds every member's calls.
    assert short_read["members_without_calls"] == []
    # The long-read callset holds the proband's alone: the parents its PED lists have none, and
    # an inheritance filter reads each as an unaffected member. The list says so with the filter
    # applied too.
    expected = [
        {"sample_id": father, "role": "father", "inheritance_reading": "unaffected"},
        {"sample_id": mother, "role": "mother", "inheritance_reading": "unaffected"},
    ]
    assert long_read["members_without_calls"] == expected
    assert long_read["dominant_members_without_calls"] == expected
    assert proband not in {member["sample_id"] for member in long_read["members_without_calls"]}


def test_the_de_novo_dominant_filter_still_reads_missing_parental_calls_as_reference(proband_only) -> None:
    # The owner kept the filter as it is: the short-read callset shows the mother passed on both
    # its variants, so its filter keeps none; the long-read callset holds no parental call, so its
    # filter keeps both of its variants, the inherited 1-1000-A-G among them.
    assert proband_only["short_read_wgs"]["de_novo_dominant"] == []
    assert proband_only["long_read_wgs"]["de_novo_dominant"] == [BOTH, LONG_READ_ONLY]


def test_the_export_names_the_members_without_calls_on_every_row(proband_only) -> None:
    _proband, mother, father = proband_only["trio"]
    long_read = proband_only["long_read_wgs"]
    assert long_read["export_header"][-1] == "Members without calls"
    assert long_read["export_last_column"] == [f"{father}; {mother}"] * 2
    # The short-read export's column is empty: its filter kept no row, and its header still ends so.
    assert proband_only["short_read_wgs"]["export_header"][-1] == "Members without calls"
    assert proband_only["short_read_wgs"]["export_last_column"] == []
