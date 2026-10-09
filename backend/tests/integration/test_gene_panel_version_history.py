"""Gene-panel version history (REQ-CARR-003) — real Postgres.

A gene panel decides which genes a carrier screen or a panel-filtered analysis reads, so
each version of its gene set must stay recoverable. Creating a local panel archives it in
``gene_panel_versions`` as version 1, each edit archives the next version, and an earlier
version is never rewritten. The unit tests in ``test_gene_panel_versions.py`` pin what the
service archives and when; this runs the real statements against the real schema:

* a create and an edit archive versions 1 and 2, an edit that changes nothing archives
  nothing, and the history lists them newest first, also for the panel id spelled in
  capitals and braces;
* each listed version reads back with its own genes, regions (with their assembly) and
  description through the real jsonb round trip;
* the schema refuses a second archive under a version number already used;
* a PanelApp import and a re-import that changes the genes archive no version (today's
  behaviour, reported for QA under REG-1).

Every record is the test's own (unique names and gene symbols) and is removed at the end.
Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.integration


def _admin(user_id: str, tag: str):
    from backend.app.services.access_control import CurrentUser

    return CurrentUser(
        id=user_id,
        username=f"panel-versions-{tag}",
        email=f"panel-versions-{tag.lower()}@example.org",
        role="admin",
        created_at=datetime.now(timezone.utc),
    )


async def _user_row(session, admin_tag: str) -> str:
    return (
        await session.execute(
            text(
                "INSERT INTO users (username, hashed_password, role, email) "
                "VALUES (:u, 'x', 'admin', :e) RETURNING id::text"
            ),
            {"u": f"panel-versions-{admin_tag}", "e": f"panel-versions-{admin_tag.lower()}@example.org"},
        )
    ).scalar_one()


async def _seed(session, tag: str, genes: tuple[str, str, str]) -> dict[str, str]:
    """A species of its own with two assemblies; the first gene in both, the others in A."""
    species = (
        await session.execute(
            text(
                "INSERT INTO species (name, common_name, tax_id) "
                "VALUES (:n, 'test', :t) RETURNING id::text"
            ),
            {"n": f"Panel versions {tag} {uuid4()}", "t": uuid4().int % 2_000_000_000},
        )
    ).scalar_one()

    async def _assembly(label: str) -> tuple[str, str]:
        name = f"PV-{label}-{tag}"
        assembly_id = (
            await session.execute(
                text(
                    "INSERT INTO assemblies (species_id, assembly_name, version, release_date) "
                    "VALUES (CAST(:s AS uuid), :a, 'v1', '2020-01-01') RETURNING id::text"
                ),
                {"s": species, "a": name},
            )
        ).scalar_one()
        return assembly_id, name

    (assembly_a, name_a), (assembly_b, name_b) = await _assembly("A"), await _assembly("B")

    async def _gene(assembly: str, symbol: str, chr_: str, start: int, end: int) -> None:
        await session.execute(
            text(
                'INSERT INTO genes (assembly_id, gene_id, hgnc_symbol, chr, start, "end", '
                "strand, biotype, description, source) VALUES "
                "(CAST(:a AS uuid), :gid, :sym, :chr, :start, :end, 1, 'protein_coding', 't', 't')"
            ),
            {"a": assembly, "gid": f"ENSG-{symbol}-{uuid4()}", "sym": symbol, "chr": chr_, "start": start, "end": end},
        )

    first, second, third = genes
    await _gene(assembly_a, first, "1", 1_000, 2_000)
    await _gene(assembly_b, first, "1", 5_000, 6_500)
    await _gene(assembly_a, second, "2", 10_000, 11_000)
    await _gene(assembly_a, third, "3", 20_000, 21_000)
    user = await _user_row(session, tag)
    await session.commit()
    return {"species": species, "name_a": name_a, "name_b": name_b, "user": user}


async def _remove(sm, *, panel_ids: list[str], species: str | None, user: str | None) -> None:
    async with sm() as session:
        for panel_id in panel_ids:
            # The panel's genes, regions and archived versions go with it (ON DELETE CASCADE).
            await session.execute(
                text("DELETE FROM gene_panels WHERE id = CAST(:p AS uuid)"), {"p": panel_id}
            )
        if species:
            # Its assemblies and their genes go with it.
            await session.execute(
                text("DELETE FROM species WHERE id = CAST(:s AS uuid)"), {"s": species}
            )
        if user:
            await session.execute(text("DELETE FROM users WHERE id = CAST(:u AS uuid)"), {"u": user})
        await session.commit()


def test_each_version_is_archived_listed_read_back_and_never_rewritten() -> None:
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )
    from backend.app.schemas import GenePanelCreate, GenePanelUpdate
    from backend.app.services.panel_metadata_service import (
        _snapshot_panel_version,
        create_panel_data,
        get_panel_version,
        list_panel_versions,
        update_panel_data,
    )

    tag = uuid4().hex[:8].upper()
    first_gene, second_gene, third_gene = (f"ZQV{tag}{suffix}" for suffix in "ABC")
    unknown_gene = f"ZQV{tag}NOWHERE"

    async def _run() -> None:
        sm = None
        seeded: dict[str, str] = {}
        panel_ids: list[str] = []
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            async with sm() as s:
                seeded = await _seed(s, tag, (first_gene, second_gene, third_gene))
            admin = _admin(seeded["user"], tag)

            async with sm() as s:
                created = await create_panel_data(
                    s,
                    GenePanelCreate(
                        name=f"Panel versions {tag}",
                        genes=[first_gene, second_gene, unknown_gene],
                        description="First curation",
                    ),
                    admin,
                )
            panel_id = created.panel.id
            panel_ids.append(panel_id)
            assert created.panel.version == 1
            async with sm() as s:
                edited = await update_panel_data(
                    s, panel_id, GenePanelUpdate(genes=[second_gene, third_gene]), admin
                )
            assert edited.panel.version == 2
            # The same genes in another order: nothing to archive.
            async with sm() as s:
                unchanged = await update_panel_data(
                    s, panel_id, GenePanelUpdate(genes=[third_gene, second_gene]), admin
                )
            assert unchanged.panel.version == 2

            async with sm() as s:
                history = await list_panel_versions(s, "{" + panel_id.upper() + "}")
                assert history.panel_id == panel_id
                assert history.current_version == 2
                assert [
                    (entry.version, entry.gene_count, entry.source, entry.created_by_email)
                    for entry in history.versions
                ] == [(2, 2, "local", admin.email), (1, 3, "local", admin.email)]
                versions = {
                    entry.version: await get_panel_version(s, panel_id, entry.version)
                    for entry in history.versions
                }

            first, second = versions[1], versions[2]
            assert (first.version, first.name, first.description) == (1, f"Panel versions {tag}", "First curation")
            assert first.genes == [first_gene, second_gene, unknown_gene]
            assert [(r.gene, r.assembly, r.chr, r.start, r.end) for r in first.regions] == [
                (first_gene, seeded["name_a"], "1", 1_000, 2_000),
                (first_gene, seeded["name_b"], "1", 5_000, 6_500),
                (second_gene, seeded["name_a"], "2", 10_000, 11_000),
            ]
            assert first.source_metadata == {}
            assert (second.version, second.description) == (2, "First curation")
            assert second.genes == [second_gene, third_gene]
            assert [(r.gene, r.chr, r.start) for r in second.regions] == [
                (second_gene, "2", 10_000),
                (third_gene, "3", 20_000),
            ]

            # Version 1 cannot be archived again under its number: it stays as it was.
            async with sm() as s:
                with pytest.raises(IntegrityError) as refused:
                    await _snapshot_panel_version(
                        s,
                        panel_id=panel_id,
                        version=1,
                        name="Rewritten",
                        description=None,
                        source="local",
                        external_version=None,
                        genes=[third_gene],
                        regions=[],
                        source_metadata={},
                        user=admin,
                    )
                assert "gene_panel_versions_panel_id_version_key" in str(refused.value)
                await s.rollback()
            async with sm() as s:
                again = await get_panel_version(s, panel_id, 1)
                assert (again.name, again.genes) == (f"Panel versions {tag}", [first_gene, second_gene, unknown_gene])
                with pytest.raises(HTTPException) as missing:
                    await get_panel_version(s, panel_id, 3)
                assert missing.value.status_code == 404
        finally:
            if sm is not None:
                await _remove(sm, panel_ids=panel_ids, species=seeded.get("species"), user=seeded.get("user"))
            await close_postgres_engine()

    asyncio.run(_run())


def test_a_panelapp_import_archives_no_version(monkeypatch) -> None:
    """Today's behaviour, reported for QA under REG-1: a PanelApp panel carries PanelApp's
    version only, and a re-import that changes its genes keeps version 1 with no history."""
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )
    from backend.app.schemas import PanelAppImportRequest
    from backend.app.services import panel_metadata_service as pms
    from backend.app.services.panelapp_service import PanelAppImportContent

    tag = uuid4().hex[:8].upper()
    panelapp_id = 900_000_000 + uuid4().int % 99_999_999  # no PanelApp id another test uses
    imported_genes = [f"ZQW{tag}A"]

    async def _fetch(requested_id, version=None):
        return {"id": requested_id, "name": f"Panel versions {tag}", "version": "3.1"}

    monkeypatch.setattr(pms, "fetch_panelapp_panel", _fetch)
    monkeypatch.setattr(
        pms,
        "extract_panelapp_import_content",
        lambda payload, **kwargs: PanelAppImportContent(genes=list(imported_genes), regions=[], metadata={}),
    )

    async def _run() -> None:
        sm = None
        user = None
        panel_ids: list[str] = []
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            async with sm() as s:
                user = await _user_row(s, tag)
                await s.commit()
            admin = _admin(user, tag)

            async with sm() as s:
                first = await pms.import_panelapp_panel_data(s, PanelAppImportRequest(panelapp_id=panelapp_id), admin)
            panel_ids.append(first.panel.id)
            imported_genes.append(f"ZQW{tag}B")  # say, amber genes taken in as well
            async with sm() as s:
                again = await pms.import_panelapp_panel_data(
                    s, PanelAppImportRequest(panelapp_id=panelapp_id, confidence_levels=["3", "2"]), admin
                )

            assert again.panel.id == first.panel.id
            assert (first.panel.version, again.panel.version) == (1, 1)
            assert again.panel.external_version == "3.1"
            async with sm() as s:
                stored = (
                    await s.execute(
                        text(
                            "SELECT gene_symbol FROM gene_panel_genes "
                            "WHERE panel_id = CAST(:p AS uuid) ORDER BY gene_symbol"
                        ),
                        {"p": first.panel.id},
                    )
                ).scalars().all()
                assert stored == [f"ZQW{tag}A", f"ZQW{tag}B"]
                archived = (
                    await s.execute(
                        text("SELECT count(*) FROM gene_panel_versions WHERE panel_id = CAST(:p AS uuid)"),
                        {"p": first.panel.id},
                    )
                ).scalar_one()
                assert archived == 0
                history = await pms.list_panel_versions(s, first.panel.id)
                assert (history.current_version, history.versions) == (1, [])
        finally:
            if sm is not None:
                await _remove(sm, panel_ids=panel_ids, species=None, user=user)
            await close_postgres_engine()

    asyncio.run(_run())
