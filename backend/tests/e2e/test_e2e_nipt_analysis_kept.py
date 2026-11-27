"""A worker process keeps a family's monogenic NIPT analysis between requests, on real ClickHouse
and Postgres (E2E).

Every NIPT view (the summary, the statistics, the variant list, the report, the sign-out) reads
the family's analysis, which reads the family's every record, and a plasma's per-sample callset
holds every low-level Mutect2 call: a million rows or more. So a worker process keeps the
analysis (``nipt_analysis_cache``) without the records: what they give each other's
observations is kept as ``NiptCounterparts``, and the variant list classifies against the
summary's fetal fraction.

Over a copy of the synthetic demo NIPT package (``demo/nipt_family``), imported through the real
package import, with per-sample records of the pregnancy's ``nipt`` callset added by the
importer's own writer under the family's write lock (the father's MNV, which the plasma calls as
its two SNVs, and the plasma's MNV, one base of which the father calls), this shows:

* the summary, the variant list (whole, and filtered to a gene that leaves out the record a
  listed variant's call is read from) and the signed record's NIPT analysis are those computed
  with the family's every record at hand: the analysis of the family's observations, each listed
  site the family's own observation of it, against the fetal fraction estimated over them;
* read again, every view is answered from the kept analysis without the family's records being
  read again;
* a write of the family's variants, or a change of the assay's artifact list, is never answered
  from an analysis kept before it.

The run's plasma is on an assay panel of its own (``_nipt_demo.prepare_copy``), the scope of its
artifact list, whose entries are removed at the end.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from backend.tests.e2e._nipt_demo import DEMO, import_demo, prepare_copy

pytestmark = pytest.mark.integration

# The per-sample records added: (variant, gene, the father's allele depths, the plasma's).
_PER_SAMPLE: dict[str, tuple[str, list[int] | None, list[int] | None]] = {
    # The father's MNV, called by the plasma as its two SNVs.
    "3-7000-AC-GT": ("GENE_OTHER", [150, 150], None),
    "3-7000-A-G": ("GENE_OTHER", None, [1380, 120]),
    "3-7001-C-T": ("GENE_LISTED", None, [1370, 130]),
    # The plasma's MNV, one base of which the father carries.
    "3-8000-AC-GT": ("GENE_OTHER", None, [1350, 150]),
    "3-8001-C-T": ("GENE_LISTED", [140, 160], None),
}
# A listed variant whose call is read from a record its gene filter leaves out.
FATHER_CALL_BORROWED = "3-7001-C-T"
PLASMA_CALL_BORROWED = "3-8001-C-T"
LISTED_GENE = "GENE_LISTED"
# Written once the analysis is kept: a plasma call of the joint callset.
WRITTEN_LATER = "2-9001-C-T"
DEMO_PATERNAL = "1-10000-A-G"  # a paternal allele the fetus inherited, category 7
NOT_IN_DATA = "5-1-A-C"


def _record(variant_id: str, source: str, gene: str, calls: dict[str, list[int]]):
    from backend.app.services.clickhouse_variant_records import SmallVariantCall, SmallVariantRecord

    chrom, pos, ref, alt = variant_id.split("-")
    return SmallVariantRecord(
        variant_key=None,
        variant_id=variant_id,
        chr=chrom,
        start=int(pos),
        end=int(pos) + len(ref) - 1,
        ref=ref,
        alt=alt,
        source=source,
        rsid=None,
        filters=["PASS"],
        gene_symbols=[gene],
        annotations=[{"gene": gene, "transcript_id": f"ENST_{gene}", "impact": "MODERATE"}],
        calls=[
            SmallVariantCall(
                sample=sample, gt="0/1", gq=None, dp=sum(ad), af=[ad[1] / sum(ad)], ad=ad, ps=None,
                metrics={"TLOD": 300.0},
            )
            for sample, ad in calls.items()
        ],
        qual=60.0,
    )


async def _write(context: Any, project_id: str, records: list) -> None:
    """``records`` added to the family's callsets by the importer's own writer, under the
    family's write lock."""
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services.clickhouse_variant_storage import insert_small_variant_records
    from backend.app.services.family_variant_write_lock import SMALL_VARIANTS, lock_family_variant_writes
    from backend.tests.e2e import _harness

    async with get_postgres_sessionmaker()() as session:
        await lock_family_variant_writes(session, context.family_uuid, [SMALL_VARIANTS])
        await insert_small_variant_records(_harness.ASSEMBLY, context.family_uuid, [project_id], records)
        await session.commit()


@contextmanager
def _counted_family_loads(loads: list[str]) -> Iterator[None]:
    from backend.app.services import nipt_service

    load = nipt_service._load_family_records

    async def counted(context: Any) -> list:
        loads.append(context.family_uuid)
        return await load(context)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(nipt_service, "_load_family_records", counted)
        yield


async def _views(family_id: str, project_id: str, admin: Any) -> dict[str, Any]:
    """Every view of the family's analysis: the summary with the artifact counts, the variant
    list, whole and of ``LISTED_GENE``, and the signed record's analysis."""
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services import nipt_service

    async with get_postgres_sessionmaker()() as session:
        summary, counts = await nipt_service.get_family_nipt_summary_with_artifacts(
            session, family_id=family_id, user=admin, project_id=project_id
        )
        variants = await nipt_service.get_family_nipt_variants(
            session, family_id=family_id, user=admin, project_id=project_id, page_size=None
        )
        gene = await nipt_service.get_family_nipt_variants(
            session,
            family_id=family_id,
            user=admin,
            project_id=project_id,
            page_size=None,
            query_filters={"gene": LISTED_GENE},
        )
        trio, context, assay_key = await nipt_service._resolve_trio_and_context(
            session, family_id=family_id, user=admin, project_id=project_id, analysis=None
        )
        record = await nipt_service.get_family_nipt_record(
            session,
            context=context,
            trio=trio,
            assay_key=assay_key,
            variant_ids=[DEMO_PATERNAL, FATHER_CALL_BORROWED, PLASMA_CALL_BORROWED, NOT_IN_DATA],
        )
    return {"summary": summary, "counts": counts, "variants": variants, "gene": gene, "record": record}


async def _with_every_record(family_id: str, project_id: str, admin: Any) -> dict[str, Any]:
    """The family's analysis computed with its every record at hand, read whole: its
    observations (by variant id), and their analysis with the artifact list's removals."""
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services import nipt_service
    from backend.app.services.family_variant_filters import SmallVariantQueryFilters
    from backend.app.services.nipt_analysis import NiptQualityThresholds, run_nipt_analysis
    from backend.app.services.nipt_target_coverage import TargetDepthLookup, sex_chromosome_coverage

    qc = NiptQualityThresholds()
    async with get_postgres_sessionmaker()() as session:
        trio, context, assay_key = await nipt_service._resolve_trio_and_context(
            session, family_id=family_id, user=admin, project_id=project_id, analysis=None
        )
        listed = await nipt_service.load_nipt_artifacts(session, assembly_id=context.assembly_id, assay_key=assay_key)
    records = await nipt_service._fetch_small_variant_rows(
        context, SmallVariantQueryFilters(page=1, page_size=100), limit=None
    )
    plasma = await nipt_service._load_target_rows(context, trio, trio.cfdna_sample_id, with_metadata=True)
    father = await nipt_service._load_target_rows(context, trio, trio.father_sample_id, with_metadata=False)
    removed, _protected = nipt_service.family_artifact_ids(
        [record for record in records if record.variant_id in listed],
        listed,
        father_sample_id=trio.father_sample_id,
        cfdna_sample_id=trio.cfdna_sample_id,
        qc=qc,
    )
    sites = list(
        nipt_service.build_nipt_observations(
            records,
            father_sample_id=trio.father_sample_id,
            cfdna_sample_id=trio.cfdna_sample_id,
            qc=qc,
            plasma_depth=TargetDepthLookup(plasma) if plasma else None,
            father_depth=TargetDepthLookup(father) if father else None,
        )
    )
    analysis = run_nipt_analysis(
        sites,
        qc,
        artifact_lookup=removed.__contains__,
        chry_profile=sex_chromosome_coverage(plasma).profile if plasma else None,
        fetuses=len(trio.fetus_sample_ids),
    )
    return {"sites": {site.variant_id: site for site in sites}, "analysis": analysis}


async def _collect(root: Path, ids: dict[str, str]) -> dict[str, Any]:
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services import nipt_service
    from backend.app.services.family_analysis_types import pregnancy_of
    from backend.app.services.family_package_records import suffixed_source
    from backend.app.services.family_variant_filters import SmallVariantQueryFilters
    from backend.app.services.nipt_artifact_pg import add_nipt_artifact, delete_nipt_artifact
    from backend.app.services.small_variant_callsets import NIPT_CALLSET
    from backend.tests.e2e import _harness

    out: dict[str, Any] = {"import": await import_demo(root)}
    family_id, project_id = ids["FAM_NIPT_DEMO"], out["import"]["project_id"]
    father, plasma = ids["FATHER_NIPT"], ids["CFDNA_NIPT"]
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        admin, _project, assembly_id = await _harness.ensure_e2e_project(session)
        trio, context, _assay_key = await nipt_service._resolve_trio_and_context(
            session, family_id=family_id, user=admin, project_id=project_id, analysis=None
        )
    (joint,) = {
        record.source
        for record in await nipt_service._fetch_small_variant_rows(
            context, SmallVariantQueryFilters(page=1, page_size=100), limit=None, annotated=False
        )
    }
    per_sample = suffixed_source(NIPT_CALLSET, pregnancy_of(trio.analysis))
    await _write(
        context,
        project_id,
        [
            _record(
                variant_id,
                per_sample,
                gene,
                {sample: ad for sample, ad in ((father, father_ad), (plasma, plasma_ad)) if ad is not None},
            )
            for variant_id, (gene, father_ad, plasma_ad) in _PER_SAMPLE.items()
        ],
    )
    nipt_service._KEPT_ANALYSES.clear()
    loads: list[str] = []
    artifact_id: str | None = None
    try:
        with _counted_family_loads(loads):
            out["served"] = await _views(family_id, project_id, admin)
            out["loads_first"] = len(loads)
            out["served_again"] = await _views(family_id, project_id, admin)
            out["loads_again"] = len(loads)
        out["every_record"] = await _with_every_record(family_id, project_id, admin)

        # The family's variants written: the next view reads them.
        await _write(context, project_id, [_record(WRITTEN_LATER, joint, "GENE_OTHER", {father: [60, 0], plasma: [470, 30]})])
        with _counted_family_loads(loads):
            out["after_write"] = await _views(family_id, project_id, admin)
        out["loads_after_write"] = len(loads)
        out["every_record_after_write"] = await _with_every_record(family_id, project_id, admin)

        # The artifact list changed: the next view reads it.
        async with sessionmaker() as session:
            row = await add_nipt_artifact(
                session, assembly_id=assembly_id, assay_key=ids["panel"], variant_id=WRITTEN_LATER, label="e2e"
            )
            artifact_id = str(row["id"])
        with _counted_family_loads(loads):
            out["after_listing"] = await _views(family_id, project_id, admin)
        out["loads_after_listing"] = len(loads)
        out["every_record_after_listing"] = await _with_every_record(family_id, project_id, admin)
    finally:
        if artifact_id is not None:
            async with sessionmaker() as session:
                await delete_nipt_artifact(session, artifact_id=artifact_id)
        nipt_service._KEPT_ANALYSES.clear()
    return out


@pytest.fixture(scope="module")
def kept(tmp_path_factory, request) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    if not (DEMO / "coga-package.json").exists():
        pytest.fail("the demo NIPT package is missing: it is committed (scripts/generate_nipt_demo.py rebuilds it)")
    copy = prepare_copy(tmp_path_factory.mktemp("nipt_analysis_kept"))
    root = Path(copy["root"])
    patch = pytest.MonkeyPatch()
    patch.setattr(settings, "family_import_roots", [str(root.parent)])
    request.addfinalizer(patch.undo)
    out = _harness.run_async(lambda: _collect(root, copy))
    assert out["import"]["completed"] is True, out["import"]
    return out


def _assert_computed_with_every_record(views: dict[str, Any], every_record: dict[str, Any]) -> None:
    analysis, sites = every_record["analysis"], every_record["sites"]
    assert views["summary"].analysis == analysis
    assert views["record"].summary.analysis == analysis
    for name in ("variants", "gene"):
        listed = views[name]
        assert listed.fetal_fraction == analysis.fetal_fraction
        assert listed.variants and all(item.site == sites[item.record.variant_id] for item in listed.variants)
    assert all(item.site == sites[item.record.variant_id] for item in views["record"].variants)


def test_every_view_is_that_of_the_familys_every_record(kept) -> None:
    _assert_computed_with_every_record(kept["served"], kept["every_record"])
    record = kept["served"]["record"]
    assert {item.record.variant_id for item in record.variants} == {
        DEMO_PATERNAL, FATHER_CALL_BORROWED, PLASMA_CALL_BORROWED,
    }
    assert record.unclassified == {NOT_IN_DATA: "not_in_data"}


def test_a_filtered_variant_reads_its_call_from_a_record_the_filter_left_out(kept) -> None:
    listed = {item.record.variant_id: item.site for item in kept["served"]["gene"].variants}
    assert set(listed) == {FATHER_CALL_BORROWED, PLASMA_CALL_BORROWED}
    assert listed[FATHER_CALL_BORROWED].representation == "father_call_other_representation"
    assert listed[FATHER_CALL_BORROWED].represented_by == "3-7000-AC-GT"
    assert listed[PLASMA_CALL_BORROWED].representation == "plasma_call_other_representation"
    assert listed[PLASMA_CALL_BORROWED].represented_by == "3-8000-AC-GT"
    # Read whole, the plasma's MNV has only one base of the father's.
    assert kept["every_record"]["sites"]["3-8000-AC-GT"].representation == "father_call_overlaps"


def test_read_again_every_view_is_answered_without_reading_the_records_again(kept) -> None:
    assert (kept["loads_first"], kept["loads_again"]) == (1, 1)
    assert kept["served_again"] == kept["served"]


def test_a_write_of_the_familys_variants_is_never_answered_from_an_older_analysis(kept) -> None:
    assert kept["loads_after_write"] == 2
    _assert_computed_with_every_record(kept["after_write"], kept["every_record_after_write"])
    before, after = (kept[name]["summary"].analysis.filter_counts for name in ("served", "after_write"))
    assert after["total_in"] == before["total_in"] + 1
    assert WRITTEN_LATER in {item.record.variant_id for item in kept["after_write"]["variants"].variants}


def test_a_change_of_the_artifact_list_is_never_answered_from_an_older_analysis(kept) -> None:
    assert kept["loads_after_listing"] == 3
    _assert_computed_with_every_record(kept["after_listing"], kept["every_record_after_listing"])
    assert kept["after_listing"]["counts"].removed == kept["after_write"]["counts"].removed + 1
    assert WRITTEN_LATER not in {item.record.variant_id for item in kept["after_listing"]["variants"].variants}
