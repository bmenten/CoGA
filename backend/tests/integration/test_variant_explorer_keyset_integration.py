"""End-to-end (#274): the explorer's keyset (seek) pagination against real ClickHouse.

Ingests five distinct variants with varying carrier counts (including a tie on
``total_samples``), then pages through them at ``page_size=2`` following ``next_cursor``.
Asserts every variant appears exactly once, in non-increasing sort order, across the
expected number of pages — validating the ``HAVING`` seek predicate, the cursor round-trip,
and the next-cursor termination against the real engine (which unit tests can't cover).

``resolve_scope`` is monkeypatched so the test needs only ClickHouse data + a Postgres
session (for the empty review/annotation joins), not a seeded project/user/assembly.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

_ASSEMBLY = "GRCh38"


def _run_with_ch_cleanup(run) -> None:
    """Run a coroutine factory under its own loop, resetting the module-global ClickHouse
    client afterwards (it binds to the loop, so the next asyncio.run would otherwise hit a
    closed loop)."""
    import asyncio as _asyncio

    from backend.app.core.clickhouse import close_clickhouse_client

    async def _wrapped() -> None:
        try:
            await run()
        finally:
            await close_clickhouse_client()

    _asyncio.run(_wrapped())


def _call(sample: str, gt: str):
    from backend.app.services.clickhouse_variant_records import SmallVariantCall

    return SmallVariantCall(sample=sample, gt=gt, gq=99.0, dp=30, af=[0.5], ad=[15, 15], ps=None)


def _record(variant_id: str, pos: int, calls):
    from backend.app.services.clickhouse_variant_records import SmallVariantRecord

    return SmallVariantRecord(
        variant_key=None, variant_id=variant_id, chr="1", start=pos, end=pos, ref="A", alt="G",
        source="test", rsid=None, filters=["PASS"], gene_symbols=[], annotations=[], calls=calls,
        qual=100.0,
    )


def test_explorer_keyset_pagination_against_clickhouse(monkeypatch) -> None:
    import backend.app.services.variant_explorer_service as svc
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services.clickhouse_variant_storage import (
        ensure_clickhouse_variant_tables,
        insert_small_variant_records,
    )

    project = str(uuid4())
    family_uuid = str(uuid4())
    scope = svc.ExplorerScope(
        assembly_id=str(uuid4()), assembly_name=_ASSEMBLY, project_ids=[project]
    )

    async def _fake_scope(session, user, assembly_id):
        return scope

    monkeypatch.setattr(svc, "resolve_scope", _fake_scope)

    async def _run() -> None:
        await ensure_clickhouse_variant_tables(_ASSEMBLY)
        await insert_small_variant_records(
            _ASSEMBLY,
            family_uuid,
            [project],
            [
                _record("1-100-A-G", 100, [_call("S1", "0/1"), _call("S2", "0/1"), _call("S3", "0/1")]),  # 3
                _record("1-200-A-G", 200, [_call("S1", "0/1"), _call("S2", "0/1")]),  # 2
                _record("1-400-A-G", 400, [_call("S2", "0/1"), _call("S3", "0/1")]),  # 2 (tie, later xpos)
                _record("1-300-A-G", 300, [_call("S1", "0/1")]),  # 1
                _record("1-500-A-G", 500, [_call("S3", "0/1")]),  # 1 (tie, later xpos)
            ],
        )

        session_factory = get_postgres_sessionmaker()
        async with session_factory() as session:
            seen_ids: list[str] = []
            totals: list[int] = []
            cursor: str | None = None
            pages = 0
            while True:
                page = await svc.search_global_small_variants(
                    session,
                    user=None,
                    filters=svc.GlobalVariantFilters(),
                    assembly_id=scope.assembly_id,
                    cursor=cursor,
                    page_size=2,
                    sort="total_samples",
                    order="desc",
                )
                pages += 1
                assert pages < 10, "next_cursor failed to terminate"
                seen_ids.extend(v.variant_id for v in page.variants)
                totals.extend(v.total_samples for v in page.variants)
                if not page.next_cursor:
                    break
                cursor = page.next_cursor

            # Every variant exactly once, no overlap or skips across pages.
            assert sorted(seen_ids) == [
                "1-100-A-G", "1-200-A-G", "1-300-A-G", "1-400-A-G", "1-500-A-G"
            ]
            assert len(seen_ids) == len(set(seen_ids)) == 5
            # Carrier counts are non-increasing across the whole sequence (the sort holds
            # across page boundaries) and span the full range.
            assert totals == sorted(totals, reverse=True), totals
            assert totals[0] == 3 and totals[-1] == 1
            # 5 rows at page_size 2 -> pages of 2 + 2 + 1; the short last page ends paging.
            assert pages == 3

    _run_with_ch_cleanup(_run)
