"""The gene-search expression indexes (#264) serve their real queries (real Postgres).

Seeds a representative gene/gene_info set (one assembly, ~600 spread symbols + a unique
marker), ANALYZEs, then EXPLAINs the ACTUAL service query shapes. A wrong index expression
or operator class would force the planner onto a full ``Seq Scan`` of the 120k-class
genes/gene_info table — which the assertions reject — and the latency-critical autocomplete
is pinned to its prefix index specifically (since the pre-existing assembly-region index can
also satisfy the assembly_id half and must not win).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

_MARK_SYMBOL = "ZQXMARK"          # unique upper symbol (autocomplete prefix ZQX% → 1 row)
_MARK_GENE_ID = "ENSGTESTMARKXYZ"
_MARK_TX = "NMTESTMARKXYZ"


async def _seed(session) -> str:
    tax = uuid4().int % 2_000_000_000
    species = (
        await session.execute(
            text(
                "INSERT INTO species (name, common_name, tax_id) "
                "VALUES (:n, 'test', :t) RETURNING id::text"
            ),
            {"n": f"gene-search {uuid4()}", "t": tax},
        )
    ).scalar_one()
    assembly = (
        await session.execute(
            text(
                "INSERT INTO assemblies (species_id, assembly_name, version, release_date) "
                "VALUES (CAST(:s AS uuid), :a, 'v1', '2020-01-01') RETURNING id::text"
            ),
            {"s": species, "a": f"gene-search-{uuid4()}"},
        )
    ).scalar_one()
    # ~600 spread genes so a selective predicate beats both a seq scan and the assembly-region index.
    await session.execute(
        text(
            "INSERT INTO genes (assembly_id, gene_id, hgnc_symbol, chr, start, \"end\", "
            "strand, biotype, description, source, extra) "
            "SELECT CAST(:a AS uuid), 'ENSG' || lpad(g::text, 8, '0'), "
            "upper(substr(md5(g::text), 1, 6)), '1', g * 1000, g * 1000 + 500, 1, "
            "'protein_coding', 't', 't', "
            "jsonb_build_object('transcript_id', 'NM_' || g) "
            "FROM generate_series(1, 600) g"
        ),
        {"a": assembly},
    )
    await session.execute(
        text(
            "INSERT INTO genes (assembly_id, gene_id, hgnc_symbol, chr, start, \"end\", "
            "strand, biotype, description, source, extra) VALUES "
            "(CAST(:a AS uuid), :gid, :sym, '1', 1, 2, 1, 'protein_coding', 't', 't', "
            "jsonb_build_object('transcript_id', CAST(:tx AS text)))"
        ),
        {"a": assembly, "gid": _MARK_GENE_ID, "sym": _MARK_SYMBOL, "tx": _MARK_TX},
    )
    await session.execute(
        text(
            "INSERT INTO gene_info (assembly_id, hgnc_symbol, gene_id) "
            "SELECT CAST(:a AS uuid), upper(substr(md5(g::text), 1, 6)), "
            "'ENSG' || lpad(g::text, 8, '0') FROM generate_series(1, 600) g "
            "ON CONFLICT (assembly_id, hgnc_symbol) DO NOTHING"
        ),
        {"a": assembly},
    )
    await session.execute(
        text(
            "INSERT INTO gene_info (assembly_id, hgnc_symbol, gene_id) VALUES "
            "(CAST(:a AS uuid), :sym, :gid) ON CONFLICT (assembly_id, hgnc_symbol) DO NOTHING"
        ),
        {"a": assembly, "sym": _MARK_SYMBOL, "gid": _MARK_GENE_ID},
    )
    await session.commit()
    return assembly


def test_gene_search_indexes_serve_their_real_queries() -> None:
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            async with sm() as s:
                assembly = await _seed(s)
            async with sm() as s:
                await s.execute(text("ANALYZE genes"))
                await s.execute(text("ANALYZE gene_info"))
                await s.commit()

            # (query, must-not-Seq-Scan table, optional index that MUST be chosen)
            cases = [
                # autocomplete — pinned to the prefix index (assembly-region index must not win)
                (
                    f"SELECT g.hgnc_symbol FROM genes g WHERE g.assembly_id IN "
                    f"(CAST('{assembly}' AS uuid)) AND upper(g.hgnc_symbol) LIKE 'ZQX%'",
                    "genes",
                    "idx_genes_assembly_symbol_prefix",
                ),
                # panel resolve — upper(hgnc_symbol) IN
                (
                    "SELECT upper(g.hgnc_symbol) FROM genes g JOIN assemblies a "
                    f"ON a.id = g.assembly_id WHERE upper(g.hgnc_symbol) IN ('{_MARK_SYMBOL}')",
                    "genes",
                    None,
                ),
                # region resolve — (upper(hgnc_symbol) IN OR upper(gene_id) IN)
                (
                    "SELECT chr, start, \"end\" FROM genes WHERE "
                    f"(upper(hgnc_symbol) IN ('{_MARK_SYMBOL}') OR upper(gene_id) IN ('{_MARK_GENE_ID}'))",
                    "genes",
                    None,
                ),
                # candidate resolve — lower() OR over symbol / gene_id / transcript_id
                (
                    f"SELECT g.hgnc_symbol FROM genes g WHERE g.assembly_id IN (CAST('{assembly}' AS uuid)) "
                    f"AND (lower(g.hgnc_symbol) IN ('{_MARK_SYMBOL.lower()}') "
                    f"OR lower(g.gene_id) IN ('{_MARK_GENE_ID.lower()}') "
                    f"OR lower(COALESCE(g.extra->>'transcript_id','')) IN ('{_MARK_TX.lower()}'))",
                    "genes",
                    None,
                ),
                # genes case-insensitive single-symbol lookup — lower(hgnc_symbol) = lower(:s)
                (
                    f"SELECT hgnc_symbol FROM genes WHERE assembly_id IN (CAST('{assembly}' AS uuid)) "
                    f"AND lower(hgnc_symbol) = lower('{_MARK_SYMBOL}')",
                    "genes",
                    None,
                ),
                # gene_info constraint enrichment — (upper(hgnc_symbol) IN OR gene_id IN)
                (
                    "SELECT hgnc_symbol, gene_id FROM gene_info WHERE "
                    f"(upper(hgnc_symbol) IN ('{_MARK_SYMBOL}') OR gene_id IN ('{_MARK_GENE_ID}'))",
                    "gene_info",
                    None,
                ),
            ]
            for sql, table, must_use in cases:
                async with sm() as s:
                    await s.execute(text("SET LOCAL enable_seqscan = off"))
                    plan = "\n".join(r[0] for r in (await s.execute(text("EXPLAIN " + sql))).all())
                    assert f"Seq Scan on {table}" not in plan, f"{table} seq-scanned:\n{plan}"
                    if must_use:
                        assert must_use in plan, f"{must_use} not used:\n{plan}"
        finally:
            await close_postgres_engine()

    asyncio.run(_run())
