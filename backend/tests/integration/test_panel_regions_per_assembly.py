"""Gene-panel regions are scoped per assembly (TF-06 H12) — real Postgres.

A panel's regions are stored per assembly, so a GRCh38 family is never filtered on T2T
coordinates, nor the other way round. This checks, against the real schema, that

* a gene is resolved in every assembly that has it, each region tagged with its assembly;
* a family's panel filter reads only its own assembly's stored regions.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text

from backend.tests.integration._datastores import fresh_assembly, fresh_species, gene_locus

pytestmark = pytest.mark.integration

def _symbols() -> tuple[str, str]:
    # Unique per test: the integration database is shared across tests.
    tag = uuid4().hex[:8].upper()
    return f"ZQX{tag}A", f"ZQX{tag}B"


async def _seed(session, gene: str, only_in_b: str) -> dict[str, str]:
    species = await fresh_species(session, f"panel-regions {uuid4()}")
    assembly_a = await fresh_assembly(session, species, f"panel-regions-A-{uuid4()}")
    assembly_b = await fresh_assembly(session, species, f"panel-regions-B-{uuid4()}")

    # Same gene, different coordinates per assembly; a second gene only in B.
    await gene_locus(session, assembly_a, gene, "1", 1_000, 2_000)
    await gene_locus(session, assembly_b, gene, "1", 5_000, 6_500)
    await gene_locus(session, assembly_b, only_in_b, "2", 10_000, 11_000)

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
            {"n": f"panel-regions panel {uuid4()}", "u": user},
        )
    ).scalar_one()
    await session.execute(
        text(
            "INSERT INTO gene_panel_genes (panel_id, gene) "
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
        _PanelMember,
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
                resolution = await _resolve_gene_regions(s, [gene, only_in_b, "ZQXNOWHERE"])
                regions = resolution.regions
                assert resolution.missing == ["ZQXNOWHERE"]
                by_key = {(r.gene, r.assembly_id): (r.chr, r.start, r.end) for r in regions}
                assert by_key == {
                    (gene, ids["a"]): ("1", 1_000, 2_000),
                    (gene, ids["b"]): ("1", 5_000, 6_500),
                    (only_in_b, ids["b"]): ("2", 10_000, 11_000),
                }
                await _replace_panel_members(
                    s,
                    panel_id=ids["panel"],
                    members=[_PanelMember(gene), _PanelMember(only_in_b)],
                    regions=regions,
                )
                await s.commit()
            async with sm() as s:
                # A family on assembly A never sees B's coordinates: the stored B-only region
                # (chr2) stays out of A's filter.
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


def test_a_gene_named_by_its_ensembl_gene_id_is_read_by_the_id_and_by_its_symbol_only_without_ids() -> None:
    """A panel gene named by its Ensembl gene ID: its loci are those of the ID in a reference
    that gives IDs (GENCODE's), never another gene of its symbol there, and those of its symbol
    in a reference that gives none (a RefSeq-derived one). Only an Ensembl gene ID carries a
    symbol in the panel's rows."""
    from sqlalchemy.exc import IntegrityError

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

    symbol, _ = _symbols()
    gene_id = f"ENSG{uuid4().int % 10**11:011d}"
    other_id = f"ENSG{uuid4().int % 10**11:011d}"

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            async with sm() as s:
                species = await fresh_species(s, f"panel-ensembl {uuid4()}")
                with_ids = await fresh_assembly(s, species, f"panel-ensembl-A-{uuid4()}")
                without_ids = await fresh_assembly(s, species, f"panel-ensembl-B-{uuid4()}")
                await gene_locus(s, with_ids, symbol, "1", 1_000, 2_000, ensembl_gene_id=gene_id)
                # Another gene of the same symbol, under another ID.
                await gene_locus(s, with_ids, symbol, "3", 50_000, 60_000, ensembl_gene_id=other_id)
                await gene_locus(s, without_ids, symbol, "1", 5_000, 6_500)
                user = (
                    await s.execute(
                        text(
                            "INSERT INTO users (username, hashed_password, role, email) "
                            "VALUES (:u, 'x', 'admin', :e) RETURNING id::text"
                        ),
                        {"u": f"panel-ensembl-{uuid4()}", "e": f"panel-ensembl-{uuid4()}@x.org"},
                    )
                ).scalar_one()
                panel = (
                    await s.execute(
                        text(
                            "INSERT INTO gene_panels (name, created_by) "
                            "VALUES (:n, CAST(:u AS uuid)) RETURNING id::text"
                        ),
                        {"n": f"panel-ensembl panel {uuid4()}", "u": user},
                    )
                ).scalar_one()
                await s.commit()
            async with sm() as s:
                resolution = await _resolve_gene_regions(s, [gene_id.lower() + ".7"])
                assert resolution.names == [gene_id]
                assert resolution.symbols == {gene_id: symbol}
                assert {(r.gene, r.assembly_id, r.chr, r.start) for r in resolution.regions} == {
                    (gene_id, with_ids, "1", 1_000),
                    (gene_id, without_ids, "1", 5_000),
                }
                await _replace_panel_members(
                    s, panel_id=panel, members=resolution.members(), regions=resolution.regions
                )
                await s.commit()
            async with sm() as s:
                in_a = await _fetch_panel_constraints(s, panel, assembly_id=with_ids)
                assert {(r.chr, r.start) for r in in_a.regions} == {("1", 1_000)}
                assert set(in_a.genes) == {gene_id, symbol}
                in_b = await _fetch_panel_constraints(s, panel, assembly_id=without_ids)
                assert {(r.chr, r.start) for r in in_b.regions} == {("1", 5_000)}
            async with sm() as s:
                with pytest.raises(IntegrityError):
                    await s.execute(
                        text(
                            "INSERT INTO gene_panel_genes (panel_id, gene, symbol) "
                            "VALUES (CAST(:p AS uuid), :g, :g)"
                        ),
                        {"p": panel, "g": symbol},
                    )
                await s.rollback()
        finally:
            await close_postgres_engine()

    asyncio.run(_run())
