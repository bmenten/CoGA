"""P2-1a end-to-end: the aggregated small-variant presence query against real ClickHouse.

Ingests a tiny fixture (one variant where S1 is non-ref + S2 is ref on chr1; another where
S2 is non-ref on chr2) and asserts the aggregate's per-sample presence: non-ref counts,
ref does not, and the chromosome filter scopes correctly. This both validates the
real-ClickHouse semantics and catches any SQL-execution error in the new query.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from backend.app.services.clickhouse_family_variants import (
    SmallVariantCall,
    SmallVariantRecord,
    _small_variant_present_sample_names,
)
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.family_variant_filters import SmallVariantQueryFilters

pytestmark = pytest.mark.integration

_ASSEMBLY = "GRCh38"


def _run_with_ch_cleanup(run) -> None:
    """Run a coroutine factory under its own loop, resetting the module-global ClickHouse
    client afterwards — otherwise the next test's asyncio.run() reuses a client bound to
    this (now-closed) loop and raises 'Event loop is closed'."""
    import asyncio as _asyncio

    from backend.app.core.clickhouse import close_clickhouse_client

    async def _wrapped() -> None:
        try:
            await run()
        finally:
            await close_clickhouse_client()

    _asyncio.run(_wrapped())


def _call(sample: str, gt: str) -> SmallVariantCall:
    return SmallVariantCall(sample=sample, gt=gt, gq=99.0, dp=30, af=[0.5], ad=[15, 15], ps=None)


def _record(variant_id: str, chrom: str, pos: int, calls: list[SmallVariantCall]) -> SmallVariantRecord:
    return SmallVariantRecord(
        variant_key=None,
        variant_id=variant_id,
        chr=chrom,
        start=pos,
        end=pos,
        ref="A",
        alt="G",
        source="test",
        rsid=None,
        filters=["PASS"],
        gene_symbols=[],
        annotations=[],
        calls=calls,
        qual=100.0,
    )


def _filters(**kwargs) -> SmallVariantQueryFilters:
    base = dict(page=1, page_size=1, chromosome=None, sample_filters=[], overlap=False)
    base.update(kwargs)
    return SmallVariantQueryFilters(**base)


def test_small_variant_presence_aggregate_against_clickhouse() -> None:
    from backend.app.services.clickhouse_variant_storage import (
        ensure_clickhouse_variant_tables,
        insert_small_variant_records,
    )

    async def _run() -> None:
        await ensure_clickhouse_variant_tables(_ASSEMBLY)
        family_uuid = str(uuid4())
        project = str(uuid4())
        # Sample CH ids are the names here (sample_name_to_uuid maps name->name).
        ctx = FamilyMetadataContext(
            family_uuid=family_uuid,
            family_id="FAM-P2-1",
            project_ids=[project],
            sample_rows=[{"sample_id": "S1"}, {"sample_id": "S2"}],
            sample_uuid_to_name={"S1": "S1", "S2": "S2"},
            sample_name_to_uuid={"S1": "S1", "S2": "S2"},
            affected_sample_names=[],
            assembly_id=str(uuid4()),
            assembly_name=_ASSEMBLY,
        )
        await insert_small_variant_records(
            _ASSEMBLY,
            family_uuid,
            [project],
            [
                _record("1-100-A-G", "1", 100, [_call("S1", "0/1"), _call("S2", "0/0")]),
                _record("2-200-A-G", "2", 200, [_call("S2", "1/1")]),
                # chr3 variant where S2 is ABSENT from the calls entirely (only S1 called).
                _record("3-300-A-G", "3", 300, [_call("S1", "0/1")]),
            ],
        )

        # Genome view (no chromosome): S1 (non-ref on chr1/3) + S2 (non-ref on chr2) present.
        present_all = await _small_variant_present_sample_names(ctx, _filters())
        assert present_all == {"S1", "S2"}, present_all

        # chr1 only: S1 is non-ref there; S2 is ref-only on chr1 -> not present.
        present_chr1 = await _small_variant_present_sample_names(ctx, _filters(chromosome="1"))
        assert present_chr1 == {"S1"}, present_chr1

        # Reference-parent case (de-novo style): on chr3 S2 is ABSENT from the variant's
        # calls, but an explicit S2:0/0 (include_absent) filter must still mark S2 present
        # (the base query matches), matching the old per-sample probe. S1 is non-ref there.
        present_ref_parent = await _small_variant_present_sample_names(
            ctx, _filters(chromosome="3", sample_filters=["S2:0/0"])
        )
        assert present_ref_parent == {"S1", "S2"}, present_ref_parent

    _run_with_ch_cleanup(_run)


def _sv_call(sample: str, gt: str):
    from backend.app.services.clickhouse_family_variants import StructuralVariantCall

    return StructuralVariantCall(sample=sample, gt=gt, qual=100.0, read_support=10, filter="PASS")


def _sv_record(variant_id: str, chrom: str, start: int, end: int, calls):
    from backend.app.services.clickhouse_family_variants import StructuralVariantRecord

    return StructuralVariantRecord(
        variant_key=None, variant_id=variant_id, chr=chrom, start=start, end=end,
        sv_type="DEL", source="test", remote_chr=None, remote_start=None, remote_end=None,
        sv_len=end - start, filters=["PASS"], gene_symbols=[], annotations=[], calls=calls,
    )


def test_structural_variant_presence_aggregate_against_clickhouse() -> None:
    from backend.app.services.clickhouse_family_variants import _structural_present_sample_names
    from backend.app.services.clickhouse_variant_storage import (
        ensure_clickhouse_variant_tables,
        insert_structural_variant_records,
    )
    from backend.app.services.family_variant_filters import StructuralVariantQueryFilters

    def _svf(**kwargs) -> StructuralVariantQueryFilters:
        base = dict(page=1, page_size=1, chromosome=None)
        base.update(kwargs)
        return StructuralVariantQueryFilters(**base)

    async def _run() -> None:
        await ensure_clickhouse_variant_tables(_ASSEMBLY)
        family_uuid = str(uuid4())
        project = str(uuid4())
        ctx = FamilyMetadataContext(
            family_uuid=family_uuid, family_id="FAM-SV", project_ids=[project],
            sample_rows=[{"sample_id": "S1"}, {"sample_id": "S2"}, {"sample_id": "S3"}],
            sample_uuid_to_name={"S1": "S1", "S2": "S2", "S3": "S3"},
            sample_name_to_uuid={"S1": "S1", "S2": "S2", "S3": "S3"},
            affected_sample_names=[], assembly_id=str(uuid4()), assembly_name=_ASSEMBLY,
        )
        await insert_structural_variant_records(
            _ASSEMBLY, family_uuid, [project],
            [
                _sv_record("sv-1", "1", 100, 500, [_sv_call("S1", "0/1"), _sv_call("S2", "0/0")]),
                # S3 is called only on chr2, so chromosome scoping must exclude it from chr1.
                _sv_record("sv-2", "2", 100, 500, [_sv_call("S2", "1/1"), _sv_call("S3", "0/1")]),
            ],
        )
        # chr1: S1 (0/1) + S2 (ref 0/0 still counts for SVs); S3 is chr2-only -> excluded.
        assert await _structural_present_sample_names(ctx, _svf(chromosome="1")) == {"S1", "S2"}
        # Genome view diverges: S3 now appears (present only via its chr2 call).
        assert await _structural_present_sample_names(ctx, _svf()) == {"S1", "S2", "S3"}

    _run_with_ch_cleanup(_run)
