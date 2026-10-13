"""A panel's resolution is kept only while what it is read from is unchanged (ENG-14) —
real Postgres.

A panel filter applies the panel's genes, its regions stored for the family's assembly and
each gene's loci in that assembly's gene reference (``_fetch_panel_constraints``). The
resolution is now kept per panel and assembly while its token is unchanged: the panel's
version, a SHA-256 of its gene rows and of its region rows in the assembly, and the
gene-reference imports recorded for the assembly. The unit tests
(``test_panel_resolution_cache.py``) drive the cache over an in-memory stand-in; this runs
the token's statement against the real schema, with a gene reference loaded and reloaded
through the reference import and a panel made and edited through the panel service:

* resolved again unchanged, the kept resolution serves, without the panel being read;
* an edit through the panel service reads the panel again, with its new gene;
* a gene written into the panel's rows, or taken out, without a new version, is read too;
* a gene-reference reload that moves a gene reads its loci again, and the check that lets
  a broader ranking serve a sub-panel (CLIN-27) passes over a ranking recorded on the old
  loci, and takes one recorded on the new.

Every record is the test's own (a species, assembly, user and gene symbols of its own) and
is removed at the end. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py).
"""

from __future__ import annotations

import asyncio
import json
from collections import OrderedDict
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


def _gene_row(assembly_id: str, symbol: str, transcript: str, chr_: str, start: int, end: int) -> dict:
    return {
        "assembly_id": assembly_id,
        "gene_id": transcript,
        "hgnc_symbol": symbol,
        "chr": chr_,
        "start": start,
        "end": end,
        "exons": json.dumps([]),
        "strand": 1,
        "biotype": "protein_coding",
        "description": "",
        "source": "gencode",
        "extra": json.dumps({}),
    }


def test_a_kept_panel_resolution_follows_every_change_of_what_it_is_read_from(monkeypatch) -> None:
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )
    from backend.app.schemas import GenePanelCreate, GenePanelUpdate
    from backend.app.services import clickhouse_family_variants as cfv
    from backend.app.services import reference_metadata_service as rms
    from backend.app.services.access_control import CurrentUser
    from backend.app.services.panel_metadata_service import create_panel_data, update_panel_data
    from backend.app.services.variant_ranking_cache import panel_members_digest

    tag = uuid4().hex[:8].upper()
    gene_a, gene_b, gene_c, gene_d = (f"ZQE{tag}{suffix}" for suffix in "ABCD")

    # No resolution kept by another test, and a count of the resolutions read from the tables.
    monkeypatch.setattr(cfv, "_panel_resolutions", OrderedDict())
    reads: list[str] = []
    read_panel_constraints = cfv._read_panel_constraints

    async def counted_read(session, panel_id, *, assembly_id):
        reads.append(panel_id)
        return await read_panel_constraints(session, panel_id, assembly_id=assembly_id)

    monkeypatch.setattr(cfv, "_read_panel_constraints", counted_read)

    def loci(constraints) -> set[tuple[str, int, int]]:
        return {(region.chr, region.start, region.end) for region in constraints.regions}

    async def _run() -> None:
        sm = None
        ids: dict[str, str] = {}
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            async with sm() as s:
                ids["species"] = (
                    await s.execute(
                        text(
                            "INSERT INTO species (name, common_name, tax_id) "
                            "VALUES (:n, 'test', :t) RETURNING id::text"
                        ),
                        {"n": f"ENG-14 {tag} {uuid4()}", "t": uuid4().int % 2_000_000_000},
                    )
                ).scalar_one()
                ids["assembly"] = (
                    await s.execute(
                        text(
                            "INSERT INTO assemblies (species_id, assembly_name, version, release_date) "
                            "VALUES (CAST(:s AS uuid), :a, 'v1', '2020-01-01') RETURNING id::text"
                        ),
                        {"s": ids["species"], "a": f"ENG14-{tag}"},
                    )
                ).scalar_one()
                ids["user"] = (
                    await s.execute(
                        text(
                            "INSERT INTO users (username, hashed_password, role, email) "
                            "VALUES (:u, 'x', 'admin', :e) RETURNING id::text"
                        ),
                        {"u": f"eng14-{tag}", "e": f"eng14-{tag.lower()}@example.org"},
                    )
                ).scalar_one()
                await s.commit()
            assembly = ids["assembly"]
            admin = CurrentUser(
                id=ids["user"],
                username=f"eng14-{tag}",
                email=f"eng14-{tag.lower()}@example.org",
                role="admin",
                created_at=datetime.now(timezone.utc),
            )

            # The gene reference, loaded through the reference import: two transcripts of A.
            async with sm() as s:
                await rms.apply_reference_gene_rows(
                    s,
                    assembly_id=assembly,
                    rows=[
                        _gene_row(assembly, gene_a, f"ENST{tag}A1", "1", 1_000, 2_000),
                        _gene_row(assembly, gene_a, f"ENST{tag}A2", "1", 1_200, 1_800),
                        _gene_row(assembly, gene_b, f"ENST{tag}B1", "2", 10_000, 11_000),
                        _gene_row(assembly, gene_c, f"ENST{tag}C1", "3", 20_000, 21_000),
                    ],
                    overwrite=False,
                    source="ENG-14 integration test",
                )
            async with sm() as s:
                created = await create_panel_data(s, GenePanelCreate(name=f"ENG-14 {tag}", genes=[gene_a, gene_b]), admin)
            ids["panel"] = panel = created.panel.id

            async def resolve():
                async with sm() as s:
                    return await cfv._fetch_panel_constraints(s, panel, assembly_id=assembly)

            # The token is read from the real schema.
            async with sm() as s:
                token = await cfv._panel_resolution_token(s, panel, assembly)
            assert token is not None
            version, genes_sha256, regions_sha256, gene_imports, last_gene_import = token
            assert version == 1 and gene_imports == 1 and last_gene_import is not None
            assert len(genes_sha256) == 64 and len(regions_sha256) == 64

            first = await resolve()
            assert first.genes == (gene_a, gene_b)
            assert loci(first) == {("1", 1_000, 2_000), ("1", 1_200, 1_800), ("2", 10_000, 11_000)}
            assert await resolve() is first
            assert reads == [panel]  # the second came from its token alone

            # An edit through the panel service: the next version, with C.
            async with sm() as s:
                await update_panel_data(s, panel, GenePanelUpdate(genes=[gene_a, gene_b, gene_c]), admin)
            edited = await resolve()
            assert len(reads) == 2
            assert edited.genes == (gene_a, gene_b, gene_c)
            assert ("3", 20_000, 21_000) in loci(edited)
            assert await resolve() is edited

            # A gene written into the panel's rows without a new version, then taken out.
            async with sm() as s:
                await s.execute(
                    text("INSERT INTO gene_panel_genes (panel_id, gene_symbol) VALUES (CAST(:p AS uuid), :g)"),
                    {"p": panel, "g": gene_d},
                )
                await s.commit()
            with_d = await resolve()
            assert len(reads) == 3
            assert gene_d in with_d.genes
            async with sm() as s:
                await s.execute(
                    text("DELETE FROM gene_panel_genes WHERE panel_id = CAST(:p AS uuid) AND gene_symbol = :g"),
                    {"p": panel, "g": gene_d},
                )
                await s.commit()
            without_d = await resolve()
            assert len(reads) == 4
            assert gene_d not in without_d.genes

            # A gene-reference reload that moves A: no panel changes version.
            recorded_before = panel_members_digest(without_d)
            async with sm() as s:
                await rms.apply_reference_gene_rows(
                    s,
                    assembly_id=assembly,
                    rows=[
                        _gene_row(assembly, gene_a, f"ENST{tag}A1", "1", 900, 2_600),
                        _gene_row(assembly, gene_b, f"ENST{tag}B1", "2", 10_000, 11_000),
                        _gene_row(assembly, gene_c, f"ENST{tag}C1", "3", 20_000, 21_000),
                    ],
                    overwrite=True,
                    source="ENG-14 integration test, reloaded",
                )
            reloaded = await resolve()
            assert len(reads) == 5
            assert ("1", 900, 2_600) in loci(reloaded)
            assert ("1", 1_200, 1_800) not in loci(reloaded)
            assert panel_members_digest(reloaded) != recorded_before

            # CLIN-27: a ranking recorded on the old loci no longer serves a sub-panel; one
            # recorded on the new loci does, from the kept resolution.
            async with sm() as s:
                old = {"panel_id": panel, "provenance": {"panel_members": recorded_before}}
                assert await cfv._superset_panel_if_unchanged(s, old, assembly_id=assembly) is None
                new = {"panel_id": panel, "provenance": {"panel_members": panel_members_digest(reloaded)}}
                assert await cfv._superset_panel_if_unchanged(s, new, assembly_id=assembly) is reloaded
            assert len(reads) == 5
        finally:
            if sm is not None:
                async with sm() as s:
                    if ids.get("panel"):
                        # Its genes, regions and archived versions go with it.
                        await s.execute(text("DELETE FROM gene_panels WHERE id = CAST(:p AS uuid)"), {"p": ids["panel"]})
                    if ids.get("species"):
                        # Its assembly, the assembly's genes and import records go with it.
                        await s.execute(text("DELETE FROM species WHERE id = CAST(:s AS uuid)"), {"s": ids["species"]})
                    if ids.get("user"):
                        await s.execute(text("DELETE FROM users WHERE id = CAST(:u AS uuid)"), {"u": ids["user"]})
                    await s.commit()
            await close_postgres_engine()

    asyncio.run(_run())
