"""Gene-panel regions are scoped per assembly (#515, TF-06 H12) — real Postgres.

Before, a panel stored one region per gene across all assemblies (GRCh38 first, else T2T)
in a table with no assembly column, and every family's panel filter unioned them: a GRCh38
family could be filtered on T2T coordinates and vice versa. This checks, against the real
schema, that

* a gene is resolved in every assembly that has it, each region tagged with its assembly;
* a family's panel filter reads only its own assembly's stored regions;
* a table created before the fix is upgraded in place by the idempotent schema load:
  gene-reference rows are attributed to their assembly, rows that match no gene record are
  dropped, and the assembly becomes part of the primary key.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

def _symbols() -> tuple[str, str]:
    # Unique per test: the integration database is shared, and the schema upgrade
    # matches regions to gene records by symbol and coordinates across all assemblies.
    tag = uuid4().hex[:8].upper()
    return f"ZQX{tag}A", f"ZQX{tag}B"

# The table exactly as it was before #515.
_PRE_515_TABLE = """
CREATE TABLE gene_panel_regions (
    panel_id uuid NOT NULL,
    gene text NOT NULL,
    chr text NOT NULL,
    start bigint NOT NULL,
    "end" bigint NOT NULL,
    CONSTRAINT gene_panel_regions_pkey PRIMARY KEY (panel_id, gene, chr, start, "end"),
    CONSTRAINT gene_panel_regions_panel_id_fkey FOREIGN KEY (panel_id)
        REFERENCES gene_panels(id) ON DELETE CASCADE
)
"""


async def _seed(session, gene: str, only_in_b: str) -> dict[str, str]:
    tax = uuid4().int % 2_000_000_000
    species = (
        await session.execute(
            text(
                "INSERT INTO species (name, common_name, tax_id) "
                "VALUES (:n, 'test', :t) RETURNING id::text"
            ),
            {"n": f"#515 {uuid4()}", "t": tax},
        )
    ).scalar_one()

    async def _assembly(label: str) -> str:
        return (
            await session.execute(
                text(
                    "INSERT INTO assemblies (species_id, assembly_name, version, release_date) "
                    "VALUES (CAST(:s AS uuid), :a, 'v1', '2020-01-01') RETURNING id::text"
                ),
                {"s": species, "a": f"#515-{label}-{uuid4()}"},
            )
        ).scalar_one()

    assembly_a = await _assembly("A")
    assembly_b = await _assembly("B")

    async def _gene(assembly: str, symbol: str, chr_: str, start: int, end: int) -> None:
        await session.execute(
            text(
                'INSERT INTO genes (assembly_id, gene_id, hgnc_symbol, chr, start, "end", '
                "strand, biotype, description, source) VALUES "
                "(CAST(:a AS uuid), :gid, :sym, :chr, :start, :end, 1, 'protein_coding', 't', 't')"
            ),
            {
                "a": assembly,
                "gid": f"ENSG-{symbol}-{uuid4()}",
                "sym": symbol,
                "chr": chr_,
                "start": start,
                "end": end,
            },
        )

    # Same gene, different coordinates per assembly; a second gene only in B.
    await _gene(assembly_a, gene, "1", 1_000, 2_000)
    await _gene(assembly_b, gene, "1", 5_000, 6_500)
    await _gene(assembly_b, only_in_b, "2", 10_000, 11_000)

    user = (
        await session.execute(
            text(
                "INSERT INTO users (username, hashed_password, role, email) "
                "VALUES (:u, 'x', 'admin', :e) RETURNING id::text"
            ),
            {"u": f"p515-{uuid4()}", "e": f"p515-{uuid4()}@x.org"},
        )
    ).scalar_one()
    panel = (
        await session.execute(
            text(
                "INSERT INTO gene_panels (name, created_by) "
                "VALUES (:n, CAST(:u AS uuid)) RETURNING id::text"
            ),
            {"n": f"#515 panel {uuid4()}", "u": user},
        )
    ).scalar_one()
    await session.execute(
        text(
            "INSERT INTO gene_panel_genes (panel_id, gene_symbol) "
            "VALUES (CAST(:p AS uuid), :g1), (CAST(:p AS uuid), :g2)"
        ),
        {"p": panel, "g1": gene, "g2": only_in_b},
    )
    await session.commit()
    return {"a": assembly_a, "b": assembly_b, "panel": panel}


def test_panel_regions_are_resolved_stored_and_read_per_assembly() -> None:
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )
    from backend.app.services.clickhouse_family_variants import _fetch_panel_constraints
    from backend.app.services.panel_metadata_service import (
        _replace_panel_members,
        _resolve_gene_regions,
    )

    gene, only_in_b = _symbols()

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            async with sm() as s:
                ids = await _seed(s, gene, only_in_b)
            async with sm() as s:
                regions, missing = await _resolve_gene_regions(s, [gene, only_in_b, "ZQXNOWHERE"])
                assert missing == ["ZQXNOWHERE"]
                by_key = {(r.gene, r.assembly_id): (r.chr, r.start, r.end) for r in regions}
                assert by_key == {
                    (gene, ids["a"]): ("1", 1_000, 2_000),
                    (gene, ids["b"]): ("1", 5_000, 6_500),
                    (only_in_b, ids["b"]): ("2", 10_000, 11_000),
                }
                await _replace_panel_members(
                    s, panel_id=ids["panel"], genes=[gene, only_in_b], regions=regions
                )
                await s.commit()
            async with sm() as s:
                # A family on assembly A never sees B's coordinates — before #515 the
                # stored B-only region (chr2) leaked into A's filter.
                for_a = await _fetch_panel_constraints(s, ids["panel"], assembly_id=ids["a"])
                assert {(r.chr, r.start, r.end) for r in for_a.regions} == {("1", 1_000, 2_000)}
                for_b = await _fetch_panel_constraints(s, ids["panel"], assembly_id=ids["b"])
                assert {(r.chr, r.start, r.end) for r in for_b.regions} == {
                    ("1", 5_000, 6_500),
                    ("2", 10_000, 11_000),
                }
                # Without a resolved assembly no coordinates can be trusted.
                unscoped = await _fetch_panel_constraints(s, ids["panel"], assembly_id=None)
                assert not unscoped.regions
                assert set(unscoped.genes) == {gene, only_in_b}
        finally:
            await close_postgres_engine()

    asyncio.run(_run())


def test_a_pre_515_region_table_is_upgraded_in_place() -> None:
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )

    gene, only_in_b = _symbols()

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            async with sm() as s:
                ids = await _seed(s, gene, only_in_b)
                # A second gene whose locus is identical in both assemblies, as the
                # mtDNA genes are in GRCh38 and T2T.
                await s.execute(
                    text(
                        'INSERT INTO genes (assembly_id, gene_id, hgnc_symbol, chr, start, "end", '
                        "strand, biotype, description, source) VALUES "
                        "(CAST(:a AS uuid), :gid_a, :sym, 'MT', 3307, 4262, 1, 'protein_coding', 't', 't'), "
                        "(CAST(:b AS uuid), :gid_b, :sym, 'MT', 3307, 4262, 1, 'protein_coding', 't', 't')"
                    ),
                    {"a": ids["a"], "b": ids["b"], "gid_a": f"MT-{uuid4()}", "gid_b": f"MT-{uuid4()}", "sym": only_in_b + "MT"},
                )
                await s.commit()
            async with sm() as s:
                await s.execute(text("DROP TABLE gene_panel_regions"))
                await s.execute(text(_PRE_515_TABLE))
                await s.execute(
                    text(
                        'INSERT INTO gene_panel_regions (panel_id, gene, chr, start, "end") VALUES '
                        # copied from assembly B's gene record
                        "(CAST(:p AS uuid), :g, '1', 5000, 6500), "
                        # identical in both assemblies: kept for each
                        "(CAST(:p AS uuid), :mt, 'MT', 3307, 4262), "
                        # PanelApp's own coordinates: no gene record matches them
                        "(CAST(:p AS uuid), 'ISCA-REGION', '3', 100, 900)"
                    ),
                    {"p": ids["panel"], "g": gene, "mt": only_in_b + "MT"},
                )
                await s.commit()

            await init_postgres_schema()  # the upgrade
            await init_postgres_schema()  # and it stays a no-op afterwards

            async with sm() as s:
                rows = (
                    await s.execute(
                        text(
                            "SELECT gene, assembly_id::text AS assembly_id FROM gene_panel_regions "
                            "WHERE panel_id = CAST(:p AS uuid)"
                        ),
                        {"p": ids["panel"]},
                    )
                ).mappings().all()
                assert {(r["gene"], r["assembly_id"]) for r in rows} == {
                    (gene, ids["b"]),
                    (only_in_b + "MT", ids["a"]),
                    (only_in_b + "MT", ids["b"]),
                }
                nullable = (
                    await s.execute(
                        text(
                            "SELECT is_nullable FROM information_schema.columns "
                            "WHERE table_schema = current_schema() "
                            "AND table_name = 'gene_panel_regions' AND column_name = 'assembly_id'"
                        )
                    )
                ).scalar_one()
                assert nullable == "NO"
                pk = (
                    await s.execute(
                        text(
                            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                            "WHERE conname = 'gene_panel_regions_pkey'"
                        )
                    )
                ).scalar_one()
                assert "assembly_id" in pk
                fk = (
                    await s.execute(
                        text(
                            "SELECT count(*) FROM pg_constraint "
                            "WHERE conname = 'gene_panel_regions_assembly_id_fkey'"
                        )
                    )
                ).scalar_one()
                assert fk == 1
        finally:
            # Whatever happened above, leave the schema in its current shape for the
            # tests that follow.
            await init_postgres_schema()
            await close_postgres_engine()

    asyncio.run(_run())
