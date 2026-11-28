"""The monogenic NIPT reads its family's calls without their annotation, on real ClickHouse (E2E).

Every NIPT view (the summary, the variant list, the report, the sign-out) estimates the fetal
fraction from the family's every record, and a plasma's per-sample callset holds every
low-level Mutect2 call: a million rows or more. The analysis reads their calls, so the family
load reads the records without their annotation (``_fetch_small_variant_rows(annotated=False)``),
and the records whose annotation is read are annotated apart (``annotate_small_variant_records``):
the family's listed artefacts, whose protection reads it, and the selected and reported
variants. The stored details are read ``_ANNOTATION_SET_CHUNK`` annotation sets per query, so no
read writes a query past ClickHouse's ``max_query_size``.

Over a copy of the synthetic demo NIPT package (``demo/nipt_family``), imported through the real
package import, with annotated records added to its callset by the importer's own writer under
the family's write lock (three alleles on the run's artifact list: one CoGA's ClinVar release
calls pathogenic (a release of the test's own, read as the assembly's current one), one common
in the population, one neither; and a de novo candidate of HIGH impact), this shows:

* each record read without its annotation, then annotated apart, is the record read whole;
* the details read one annotation set per query are those read at once;
* the NIPT summary with what the artifact list did to it, the variant list and the signed
  record's NIPT analysis are those computed over the family's records read whole, each with its
  annotation.

The run's plasma is on an assay panel of its own (``_nipt_demo.prepare_copy``), the scope of its
artifact list, whose entries are removed at the end.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from backend.tests.e2e._clinvar_release import clinvar_release
from backend.tests.e2e._nipt_demo import DEMO, import_demo, prepare_copy

pytestmark = pytest.mark.integration

ARTIFACT_CLINVAR = "2-5001-C-T"
ARTIFACT_COMMON = "2-5101-C-T"
ARTIFACT_PLAIN = "2-5201-C-T"
DE_NOVO_HIGH = "2-6001-G-A"
DEMO_DE_NOVO = "1-1001-A-G"  # GENE_DENOVO, category 1, in the fetal window
DEMO_PATERNAL = "1-10000-A-G"  # a paternal allele the fetus inherited, category 7
REPORTED = [DE_NOVO_HIGH, ARTIFACT_CLINVAR, ARTIFACT_PLAIN, DEMO_DE_NOVO, DEMO_PATERNAL]
# CoGA's ClinVar release of the test's own: ARTIFACT_CLINVAR pathogenic.
_RELEASE = date(2026, 10, 25)
_HELD = {ARTIFACT_CLINVAR: "Pathogenic"}

# Each added variant's plasma allele depths and its one transcript's annotation.
_ADDED: dict[str, tuple[list[int], dict[str, Any]]] = {
    ARTIFACT_CLINVAR: ([490, 10], {"gene": "GENE_ART", "impact": "MODERATE"}),
    ARTIFACT_COMMON: ([490, 10], {"gene": "GENE_ART", "impact": "LOW", "gnomad_af": 0.2}),
    ARTIFACT_PLAIN: ([490, 10], {"gene": "GENE_ART", "impact": "MODERATE", "gnomad_af": 0.0001}),
    DE_NOVO_HIGH: ([470, 30], {"gene": "GENE_HIGH", "impact": "HIGH", "consequence": "stop_gained"}),
}


def _added_records(source: str, father: str, plasma: str) -> list:
    from backend.app.services.clickhouse_variant_records import SmallVariantCall, SmallVariantRecord

    records = []
    for variant_id, (plasma_ad, annotation) in _ADDED.items():
        chrom, pos, ref, alt = variant_id.split("-")
        records.append(
            SmallVariantRecord(
                variant_key=None,
                variant_id=variant_id,
                chr=chrom,
                start=int(pos),
                end=int(pos),
                ref=ref,
                alt=alt,
                source=source,
                rsid=None,
                filters=["PASS"],
                gene_symbols=[annotation["gene"]],
                annotations=[{**annotation, "transcript_id": f"ENST_{annotation['gene']}"}],
                calls=[
                    SmallVariantCall(sample=father, gt="0/0", gq=None, dp=50, af=[0.0], ad=[50, 0], ps=None),
                    SmallVariantCall(
                        sample=plasma,
                        gt="0/1",
                        gq=None,
                        dp=sum(plasma_ad),
                        af=[plasma_ad[1] / sum(plasma_ad)],
                        ad=plasma_ad,
                        ps=None,
                    ),
                ],
                qual=60.0,
            )
        )
    return records


@contextmanager
def _family_read_whole() -> Iterator[None]:
    """The NIPT analysis over the family's records read whole, each with its annotation, none
    annotated apart."""
    from backend.app.services import nipt_service
    from backend.app.services.family_variant_filters import SmallVariantQueryFilters

    async def whole(context: Any) -> list:
        return await nipt_service._fetch_small_variant_rows(
            context, SmallVariantQueryFilters(page=1, page_size=100), limit=None
        )

    async def as_read(_context: Any, records: Any) -> list:
        return list(records)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(nipt_service, "_load_family_records", whole)
        patch.setattr(nipt_service, "annotate_small_variant_records", as_read)
        yield


async def _analyses(family_id: str, project_id: str, admin: Any) -> dict[str, Any]:
    """The summary with the artifact counts, the variant list and the signed record's analysis."""
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services import nipt_service

    async with get_postgres_sessionmaker()() as session:
        summary, counts = await nipt_service.get_family_nipt_summary_with_artifacts(
            session, family_id=family_id, user=admin, project_id=project_id
        )
        variants = await nipt_service.get_family_nipt_variants(
            session, family_id=family_id, user=admin, project_id=project_id, page_size=None
        )
        trio, context, assay_key = await nipt_service._resolve_trio_and_context(
            session, family_id=family_id, user=admin, project_id=project_id, analysis=None
        )
        record = await nipt_service.get_family_nipt_record(
            session, context=context, trio=trio, assay_key=assay_key, variant_ids=REPORTED
        )
    return {"summary": summary, "counts": counts, "variants": variants, "record": record}


async def _records(family_id: str, project_id: str, admin: Any) -> dict[str, Any]:
    """The family's records read whole, read without their annotation and annotated apart, and
    read whole one annotation set per details query (with the number of those queries)."""
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services import clickhouse_family_variants as cfv
    from backend.app.services import nipt_service
    from backend.app.services.family_variant_filters import SmallVariantQueryFilters

    async with get_postgres_sessionmaker()() as session:
        _trio, context, _assay_key = await nipt_service._resolve_trio_and_context(
            session, family_id=family_id, user=admin, project_id=project_id, analysis=None
        )
    filters = SmallVariantQueryFilters(page=1, page_size=100)
    whole = await cfv._fetch_small_variant_rows(context, filters, limit=None)
    calls_only = await cfv._fetch_small_variant_rows(context, filters, limit=None, annotated=False)
    annotated = await cfv.annotate_small_variant_records(context, calls_only)
    queries: list[str] = []
    execute = cfv._execute_clickhouse

    async def counted(query: str, params: dict[str, Any]) -> Any:
        queries.append(query)
        return await execute(query, params)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(cfv, "_ANNOTATION_SET_CHUNK", 1)
        patch.setattr(cfv, "_execute_clickhouse", counted)
        one_set_per_query = await cfv._fetch_small_variant_rows(context, filters, limit=None)
    return {
        "whole": whole,
        "calls_only": calls_only,
        "annotated": annotated,
        "one_set_per_query": one_set_per_query,
        "details_queries": sum("variants/details" in query for query in queries),
        "annotation_sets": len({cfv._annotation_set_identity(record) for record in whole}),
    }


async def _collect(root: Path, ids: dict[str, str]) -> dict[str, Any]:
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services import nipt_service
    from backend.app.services.clickhouse_variant_storage import insert_small_variant_records
    from backend.app.services.family_variant_filters import SmallVariantQueryFilters
    from backend.app.services.family_variant_write_lock import SMALL_VARIANTS, lock_family_variant_writes
    from backend.app.services.nipt_artifact_pg import add_nipt_artifact, delete_nipt_artifact
    from backend.tests.e2e import _harness

    out: dict[str, Any] = {"import": await import_demo(root)}
    family_id, project_id = ids["FAM_NIPT_DEMO"], out["import"]["project_id"]
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        admin, _project, assembly_id = await _harness.ensure_e2e_project(session)
        _trio, context, _assay_key = await nipt_service._resolve_trio_and_context(
            session, family_id=family_id, user=admin, project_id=project_id, analysis=None
        )
    # The added rows in the demo callset's own source, so the analysis reads them as its own.
    (source,) = {
        record.source
        for record in await nipt_service._fetch_small_variant_rows(
            context, SmallVariantQueryFilters(page=1, page_size=100), limit=None, annotated=False
        )
    }
    async with sessionmaker() as session:
        await lock_family_variant_writes(session, context.family_uuid, [SMALL_VARIANTS])
        await insert_small_variant_records(
            _harness.ASSEMBLY,
            context.family_uuid,
            [project_id],
            _added_records(source, ids["FATHER_NIPT"], ids["CFDNA_NIPT"]),
        )
        await session.commit()
    artifact_ids: list[str] = []
    try:
        async with sessionmaker() as session:
            for variant_id in (ARTIFACT_CLINVAR, ARTIFACT_COMMON, ARTIFACT_PLAIN):
                row = await add_nipt_artifact(
                    session, assembly_id=assembly_id, assay_key=ids["panel"], variant_id=variant_id, label="e2e"
                )
                artifact_ids.append(str(row["id"]))
        out["records"] = await _records(family_id, project_id, admin)
        async with clinvar_release(_harness.ASSEMBLY, _RELEASE, _HELD):
            out["served"] = await _analyses(family_id, project_id, admin)
            with _family_read_whole():
                out["read_whole"] = await _analyses(family_id, project_id, admin)
    finally:
        async with sessionmaker() as session:
            for artifact_id in artifact_ids:
                await delete_nipt_artifact(session, artifact_id=artifact_id)
    return out


@pytest.fixture(scope="module")
def loaded(tmp_path_factory, request) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    if not (DEMO / "coga-package.json").exists():
        pytest.fail("the demo NIPT package is missing: it is committed (scripts/generate_nipt_demo.py rebuilds it)")
    copy = prepare_copy(tmp_path_factory.mktemp("nipt_family_load"))
    root = Path(copy["root"])
    patch = pytest.MonkeyPatch()
    patch.setattr(settings, "family_import_roots", [str(root.parent)])
    request.addfinalizer(patch.undo)
    out = _harness.run_async(lambda: _collect(root, copy))
    assert out["import"]["completed"] is True, out["import"]
    return out


def test_a_record_read_without_its_annotation_then_annotated_is_the_record_read_whole(loaded) -> None:
    records = loaded["records"]
    whole = {record.variant_id: record for record in records["whole"]}
    assert set(_ADDED) <= set(whole)
    assert records["calls_only"] == [replace(record, annotations=[]) for record in records["whole"]]
    assert records["annotated"] == records["whole"]
    # The added records hold their annotation: the comparison reads one.
    assert whole[DE_NOVO_HIGH].annotations[0]["impact"] == "HIGH"


def test_the_details_read_one_annotation_set_per_query_are_those_read_at_once(loaded) -> None:
    records = loaded["records"]
    assert records["one_set_per_query"] == records["whole"]
    assert records["details_queries"] == records["annotation_sets"] > 1


def test_the_summary_and_the_artifact_counts_are_those_of_the_records_read_whole(loaded) -> None:
    served, whole = loaded["served"], loaded["read_whole"]
    assert served["summary"] == whole["summary"]
    assert served["counts"] == whole["counts"]
    # The family's listed artefacts were read with their annotation and their ClinVar in
    # CoGA's release: two of them protected.
    assert (served["counts"].in_family, served["counts"].removed) == (3, 1)
    assert served["counts"].protected == {"clinvar": 1, "common": 1}


def test_the_variant_list_is_that_of_the_records_read_whole(loaded) -> None:
    served, whole = loaded["served"]["variants"], loaded["read_whole"]["variants"]
    assert served == whole
    listed = {item.record.variant_id for item in served.variants}
    assert DE_NOVO_HIGH in listed and ARTIFACT_PLAIN not in listed


def test_the_signed_records_analysis_is_that_of_the_records_read_whole(loaded) -> None:
    served, whole = loaded["served"]["record"], loaded["read_whole"]["record"]
    assert served == whole
    assert served.unclassified == {ARTIFACT_PLAIN: "artifact_list"}
    reported = {item.record.variant_id: item for item in served.variants}
    # Each reported variant with its annotation, which the de novo triage reads.
    assert reported[DE_NOVO_HIGH].record.annotations[0]["impact"] == "HIGH"
    assert reported[DE_NOVO_HIGH].de_novo is not None
    assert reported[ARTIFACT_CLINVAR].classification.flags.count("artifact_list_protected") == 1
