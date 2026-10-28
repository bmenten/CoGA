"""End-to-end (#266): the aggregated small-variant presence query against real ClickHouse.

Ingests a tiny fixture (one variant where S1 is non-ref + S2 is ref on chr1; another where
S2 is non-ref on chr2) and asserts the aggregate's per-sample presence: non-ref counts,
ref does not, and the chromosome filter scopes correctly. This both validates the
real-ClickHouse semantics and catches any SQL-execution error in the new query.

The aggregate is one SELECT over the family's rows since ENG-19 (each row's samples with an
ALT allele picked from its call arrays); it was an ARRAY JOIN over a subquery, which
ClickHouse's analyzer was slow to analyse under a large panel. The members it counts for
each kind of call are pinned here, and a loose guard holds the analyzer's time over it near
its time over a member's track count, which applies the same panel clause.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from backend.app.services import clickhouse_family_variants
from backend.app.services.clickhouse_family_variants import _small_variant_present_sample_names
from backend.app.services.clickhouse_variant_records import (
    PanelFilterConstraints,
    Region,
    SmallVariantCall,
    SmallVariantRecord,
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
            family_id="FAM-PRESENCE",
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


# Each member's one call in the variant, and whether it carries an ALT allele (#511's classes).
_GENOTYPES = {
    "HET": ("0/1", True),
    "HOM": ("1|1", True),
    "HAPLOID": ("1", True),
    "MULTI": ("1/2", True),
    "HALF": ("./1", True),
    "TWO_DIGITS": ("0/10", True),
    "REF": ("0/0", False),
    "HAPLOID_REF": ("0", False),
    "HALF_REF": ("./0", False),
    "MISSING": ("./.", False),
    "TRIPLOID_REF": ("0/0/0", False),
}


def _family(family_uuid: str, project: str, members: list[str]) -> FamilyMetadataContext:
    """A family whose ClickHouse sample ids are its members' names."""
    return FamilyMetadataContext(
        family_uuid=family_uuid,
        family_id="FAM-PRESENCE-CALLS",
        project_ids=[project],
        sample_rows=[{"sample_id": name} for name in members],
        sample_uuid_to_name={name: name for name in members},
        sample_name_to_uuid={name: name for name in members},
        affected_sample_names=[],
        assembly_id=str(uuid4()),
        assembly_name=_ASSEMBLY,
    )


def test_small_variant_presence_counts_each_call_with_an_alt_allele_against_clickhouse() -> None:
    """A member is present when a matching variant's call of theirs carries an ALT allele,
    and only then: a het, hom-alt, haploid, multi-allelic or half call, or one with an allele
    index of two digits, but not a reference, half-reference or missing call. A call of a
    sample outside the family is not read. The ARRAY JOIN over a subquery that ENG-19
    replaced counted the same members."""
    from backend.app.services.clickhouse_variant_storage import (
        ensure_clickhouse_variant_tables,
        insert_small_variant_records,
    )

    async def _run() -> None:
        await ensure_clickhouse_variant_tables(_ASSEMBLY)
        family_uuid = str(uuid4())
        project = str(uuid4())
        ctx = _family(family_uuid, project, list(_GENOTYPES))
        calls = [_call(name, gt) for name, (gt, _alt) in _GENOTYPES.items()]
        await insert_small_variant_records(
            _ASSEMBLY,
            family_uuid,
            [project],
            [_record("4-400-A-G", "4", 400, [*calls, _call("STRANGER", "1/1")])],
        )

        present = await _small_variant_present_sample_names(ctx, _filters(chromosome="4"))
        assert present == {name for name, (_gt, alt) in _GENOTYPES.items() if alt}, present

    _run_with_ch_cleanup(_run)


def _mendeliome_sized_panel() -> PanelFilterConstraints:
    """5,300 genes of eight nested transcript loci each, on chromosomes 1 to 22 from 40 Mb, as
    a gene reference of one row per transcript resolves the Mendeliome."""
    genes: list[str] = []
    loci: list[Region] = []
    for index in range(5_300):
        chrom = str(index % 22 + 1)
        start = 40_000_000 + 10_000 * (index // 22) + 1
        genes.append(f"PRSNCF{index:04d}")
        for transcript in range(8):
            loci.append(Region(chr=chrom, start=start + 100 * transcript, end=start + 5_000 - 50 * transcript))
    return PanelFilterConstraints(genes=tuple(genes), regions=tuple(loci))


def test_the_presence_costs_the_analyzer_about_what_a_members_track_count_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A loose guard (ENG-19). Under a Mendeliome-sized panel, ClickHouse's analyzer takes
    about as long over the presence as over a member's track count, one SELECT that applies
    the same panel clause. The presence wrapped the family's rows in a subquery, ARRAY JOINed
    and grouped outside it, and took three times as long (36 ms against 11 on the throwaway
    stack), whatever the window. Both queries are built by the app and run here in turn, five
    times each, and the fastest analysis of each is read from the query log: a slower or a
    loaded machine slows both. Skipped where the analyzer records no such time."""
    from backend.app.core.clickhouse import get_clickhouse_client
    from backend.app.services.clickhouse_variant_storage import (
        ensure_clickhouse_variant_tables,
        insert_small_variant_records,
    )

    built: list[tuple[str, dict[str, Any]]] = []

    async def build_only(query: str, params: Any = None) -> list[tuple[Any, ...]]:
        built.append((query, dict(params or {})))
        return [(0,)] if "count()" in query else []

    measured: dict[str, int] = {}

    async def _run() -> None:
        await ensure_clickhouse_variant_tables(_ASSEMBLY)
        family_uuid = str(uuid4())
        project = str(uuid4())
        ctx = _family(family_uuid, project, ["S1", "S2"])
        await insert_small_variant_records(
            _ASSEMBLY,
            family_uuid,
            [project],
            [_record("7-30000000-A-G", "7", 30_000_000, [_call("S1", "0/1"), _call("S2", "0/0")])],
        )
        panel = _mendeliome_sized_panel()
        window = {"chromosome": "7", "start": 29_500_000, "end": 31_500_000, "overlap": True}
        monkeypatch.setattr(clickhouse_family_variants, "_execute_clickhouse", build_only)
        await _small_variant_present_sample_names(ctx, _filters(**window), panel_constraints=panel)
        # A member's track, as the chromosome view asks for it: their own ALT calls.
        await clickhouse_family_variants._count_small_variant_rows_bounded(
            ctx, _filters(**window, sample_filters=["S1:het|hom"]), panel_constraints=panel
        )
        monkeypatch.undo()
        queries = dict(zip(("presence", "track count"), built))

        client = await get_clickhouse_client()
        ids: dict[str, list[str]] = {name: [] for name in queries}
        for _ in range(5):
            for name, (query, params) in queries.items():
                query_id = str(uuid4())
                await client.query(query, parameters=params, settings={"query_id": query_id})
                ids[name].append(query_id)
        await client.command("SYSTEM FLUSH LOGS")
        logged = await client.query(
            "SELECT query_id, ProfileEvents['QueryAnalysisMicroseconds'] FROM system.query_log"
            " WHERE type = 'QueryFinish' AND query_id IN %(ids)s",
            parameters={"ids": [query_id for group in ids.values() for query_id in group]},
        )
        analysis = {str(query_id): int(micros) for query_id, micros in logged.result_rows}
        for name, group in ids.items():
            measured[name] = min(analysis.get(query_id, 0) for query_id in group)

    _run_with_ch_cleanup(_run)

    if not measured["track count"]:
        pytest.skip("the analyzer recorded no analysis time")
    assert measured["presence"] <= 2 * measured["track count"] + 2_000, measured


def _sv_call(sample: str, gt: str):
    from backend.app.services.clickhouse_variant_records import StructuralVariantCall

    return StructuralVariantCall(sample=sample, gt=gt, qual=100.0, read_support=10, filter="PASS")


def _sv_record(variant_id: str, chrom: str, start: int, end: int, calls):
    from backend.app.services.clickhouse_variant_records import StructuralVariantRecord

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
