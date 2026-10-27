"""Lookups past 32,767 items, on real Postgres (DATA-6).

asyncpg refuses a statement with more than 32,767 parameters, and these lookups bound one
parameter per item (an expanding IN): a panel's genes and a NIPT target's gene terms, a PED
file's families and samples, the family list's families (every family the caller may see),
and the admin data inventory's families and samples. A laboratory's families and samples grow
past that limit in time; the others need an input that long. Each lookup now binds its items
as one array, as the export lookups do since #835.

Each lookup is run with 40,000 items, the seeded one among them past the 32,767th, and finds
exactly what was seeded for it; the project family lookup, which reads a project's families
itself, is run over one. Synthetic data only, deleted afterwards. Skipped unless
``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

# Items per lookup, and where the seeded one sits among them: past the 32,767 parameters
# asyncpg sends in one statement.
_ITEMS = 40_000
_SEEDED_AT = 35_000


def _among_others(item: str, other) -> list[str]:
    """``_ITEMS`` items, ``item`` at ``_SEEDED_AT`` and ``other(i)`` everywhere else."""
    return [item if index == _SEEDED_AT else other(index) for index in range(_ITEMS)]


def _uuids_with(item: str) -> list[str]:
    return _among_others(item, lambda _index: str(uuid4()))


async def _seed(session, label: str) -> dict[str, str]:
    """A species, an assembly with one gene, a project, and a family of two members in it
    with a relationship, a repeat call and a coverage track for the proband."""
    seeded: dict[str, str] = {"label": label, "gene": f"DATA6{uuid4().hex[:10].upper()}"}
    seeded["species"] = (
        await session.execute(
            text("INSERT INTO species (name, common_name, tax_id) VALUES (:n, 'test', :t) RETURNING id::text"),
            {"n": f"data6 {label}", "t": uuid4().int % 2_000_000_000},
        )
    ).scalar_one()
    seeded["assembly"] = (
        await session.execute(
            text(
                "INSERT INTO assemblies (species_id, assembly_name, version, release_date) "
                "VALUES (CAST(:s AS uuid), :a, 'v1', '2020-01-01') RETURNING id::text"
            ),
            {"s": seeded["species"], "a": f"data6-{label}"},
        )
    ).scalar_one()
    await session.execute(
        text(
            "INSERT INTO genes (assembly_id, gene_id, hgnc_symbol, chr, start, \"end\", strand, biotype, "
            "description, source, extra) VALUES (CAST(:a AS uuid), :g, :g, '7', 1000, 2000, 1, "
            "'protein_coding', 't', 't', '{}'::jsonb)"
        ),
        {"a": seeded["assembly"], "g": seeded["gene"]},
    )
    seeded["project"] = (
        await session.execute(
            text(
                "INSERT INTO projects (name, species_id, assembly_id) "
                "VALUES (:n, CAST(:s AS uuid), CAST(:a AS uuid)) RETURNING id::text"
            ),
            {"n": f"data6 {label}", "s": seeded["species"], "a": seeded["assembly"]},
        )
    ).scalar_one()
    seeded["family"] = (
        await session.execute(text("INSERT INTO families (family_id) VALUES (:f) RETURNING id::text"), {"f": label})
    ).scalar_one()
    await session.execute(
        text("INSERT INTO family_projects (family_id, project_id) VALUES (CAST(:f AS uuid), CAST(:p AS uuid))"),
        {"f": seeded["family"], "p": seeded["project"]},
    )
    for role, sex in (("father", "male"), ("proband", "female")):
        seeded[role] = (
            await session.execute(
                text(
                    "INSERT INTO samples (sample_id, family_id, sex) "
                    "VALUES (:s, CAST(:f AS uuid), :sex) RETURNING id::text"
                ),
                {"s": f"{label}-{role}", "f": seeded["family"], "sex": sex},
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO family_members (family_id, sample_id, role, clinical_status) "
                "VALUES (CAST(:f AS uuid), CAST(:s AS uuid), :role, 'unaffected')"
            ),
            {"f": seeded["family"], "s": seeded[role], "role": role},
        )
    await session.execute(
        text(
            "INSERT INTO family_relationships (family_id, relationship_type, sample_id_a, sample_id_b) "
            "VALUES (CAST(:f AS uuid), 'parent_child', CAST(:a AS uuid), CAST(:b AS uuid))"
        ),
        {"f": seeded["family"], "a": seeded["father"], "b": seeded["proband"]},
    )
    await session.execute(
        text(
            "INSERT INTO repeat_expansions (sample_id, family_id, locus_id, gene, display_name, disease, "
            "chr, start, \"end\") VALUES (CAST(:s AS uuid), CAST(:f AS uuid), 'HTT', 'HTT', 'HTT', "
            "'Huntington disease', '4', 3074877, 3074940)"
        ),
        {"s": seeded["proband"], "f": seeded["family"]},
    )
    await session.execute(
        text(
            "INSERT INTO sample_interval_track_sources (sample_id, family_id, track_type, row_count) "
            "VALUES (CAST(:s AS uuid), CAST(:f AS uuid), 'coverage', 7)"
        ),
        {"s": seeded["proband"], "f": seeded["family"]},
    )
    await session.commit()
    return seeded


async def _remove(session, seeded: dict[str, str]) -> None:
    await session.rollback()
    for statement in (
        "DELETE FROM families WHERE id = CAST(:family AS uuid)",
        "DELETE FROM projects WHERE id = CAST(:project AS uuid)",
        "DELETE FROM genes WHERE assembly_id = CAST(:assembly AS uuid)",
        "DELETE FROM assemblies WHERE id = CAST(:assembly AS uuid)",
        "DELETE FROM species WHERE id = CAST(:species AS uuid)",
    ):
        await session.execute(text(statement), seeded)
    await session.commit()


def test_each_lookup_finds_its_item_among_40000() -> None:
    from fastapi import HTTPException

    from backend.app.core.postgres import close_postgres_engine, get_postgres_sessionmaker, init_postgres_schema
    from backend.app.services import admin_service, metadata_service, nipt_service, panel_metadata_service, ped_service
    from backend.app.services.access_control import CurrentUser
    from backend.app.services.clickhouse_interval_tracks import interval_counts_by_family, interval_counts_by_sample

    label = f"DATA6-{uuid4().hex[:12]}"

    async def scenario() -> None:
        await init_postgres_schema()
        sessionmaker = get_postgres_sessionmaker()
        async with sessionmaker() as session:
            seeded = await _seed(session, label)
            try:
                family, proband, gene = seeded["family"], seeded["proband"], seeded["gene"]
                families, samples = _uuids_with(family), _uuids_with(proband)
                genes = _among_others(gene, lambda index: f"DATA6-ABSENT-{index}")

                # A panel's genes, and a NIPT target's gene terms.
                regions, missing = await panel_metadata_service._resolve_gene_regions(session, genes)
                assert [(region.gene, region.assembly_id) for region in regions] == [(gene, seeded["assembly"])]
                assert len(missing) == _ITEMS - 1 and gene not in missing
                targets = await nipt_service._fetch_labeled_gene_regions(
                    session, terms=genes, assembly_id=seeded["assembly"]
                )
                assert [target.label for target in targets] == [gene]

                # A PED file's families and samples.
                family_names = _among_others(label, lambda index: f"DATA6-ABSENT-{index}")
                rows = await ped_service._existing_family_rows(session, family_names)
                assert {row["family_uuid"] for row in rows} == {family}
                sample_names = _among_others(f"{label}-proband", lambda index: f"DATA6-ABSENT-{index}")
                with pytest.raises(HTTPException) as refused:
                    await ped_service._ensure_sample_ids_are_available(session, sample_names)
                assert refused.value.status_code == 409 and f"{label}-proband" in str(refused.value.detail)

                # The family list: by name, and every family's members, relationships and
                # structure version.
                listed = await metadata_service._fetch_family_rows(session, family_identifiers=family_names)
                assert [row["id"] for row in listed] == [family]
                members = await metadata_service._fetch_family_sample_rows(session, families)
                assert sorted(row["role"] for row in members[family]) == ["father", "proband"]
                assert set(members) == {family}
                relationships = await metadata_service._fetch_family_relationship_rows(session, families)
                assert set(relationships) == {family} and len(relationships[family]) == 1
                assert await metadata_service._fetch_family_structure_version_rows(session, families) == {}
                admin = CurrentUser(
                    id=str(uuid4()),
                    username="data6-admin",
                    email="data6-admin@example.org",
                    role="admin",
                    created_at=datetime.now(timezone.utc),
                )
                by_family_id, families_by_project = await metadata_service._fetch_project_family_rows(
                    session, [seeded["project"]], admin
                )
                assert set(by_family_id) == {family} and families_by_project[seeded["project"]] == [family]

                # The admin data inventory: members, repeat calls and coverage tracks.
                inventory = await admin_service._sample_rows_by_family(session, families)
                assert {key for key, rows in inventory.items() if rows} == {family}
                assert len(inventory[family]) == 2
                assert await admin_service._repeat_counts_by_family(session, families) == {family: 1}
                assert await admin_service._repeat_counts_by_sample(session, samples) == {proband: 1}
                by_family = await interval_counts_by_family(session, families, ["coverage"])
                assert by_family[family]["coverage"] == 7
                by_sample = await interval_counts_by_sample(session, samples, ["coverage"])
                assert by_sample[proband]["coverage"] == 7
                assert sum(counts["coverage"] for counts in by_sample.values()) == 7
            finally:
                await _remove(session, seeded)
        await close_postgres_engine()

    asyncio.run(scenario())
