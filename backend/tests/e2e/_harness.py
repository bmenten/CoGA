"""End-to-end harness: boot the real stack, import a package through the front
door, and gather per-stage facts for assertions.

This reuses the production code paths verbatim:

* ``init_postgres_schema`` / ``init_clickhouse_schema`` apply the real schema.
* a Postgres ``species -> assembly -> project`` is set up so the import resolves a
  single assembly and datasets reach status ``imported`` (not ``registered``).
* ``execute_family_package_import`` is the same entry the API/worker uses.
* facts are read back via the same service-layer query/count functions the API
  serves (ClickHouse counts, the family small-variant page, interval-track
  registry), so the assertions exercise the read path too.

Everything runs inside ONE event loop (``run_async``) which closes the
loop-bound ClickHouse client and Postgres engine in ``finally`` — reusing
``asyncio.run`` across calls otherwise hits a closed loop (see
``test_variant_explorer_keyset_integration``).
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from sqlalchemy import text

ASSEMBLY = "GRCh38"
SPECIES = "Homo sapiens"
PROJECT_NAME = "e2e-golden"
FAMILY_ID = "FAM_TRIO"


def run_async(make_coro: Callable[[], Awaitable[Any]]) -> Any:
    """Run a coroutine factory under its own loop, then reset the module-global
    ClickHouse client and Postgres engine (both bind to the loop)."""
    from backend.app.core.clickhouse import close_clickhouse_client
    from backend.app.core.postgres import close_postgres_engine

    async def _wrapped() -> Any:
        try:
            return await make_coro()
        finally:
            await close_clickhouse_client()
            await close_postgres_engine()

    return asyncio.run(_wrapped())


def _admin_current_user(user_id: str):
    from backend.app.core.config import settings
    from backend.app.services.access_control import CurrentUser

    return CurrentUser(
        id=user_id,
        username=settings.admin_username,
        email=settings.admin_email,
        role="admin",
        created_at=datetime.now(timezone.utc),
    )


async def _ensure_admin(session):
    from backend.app.core.config import settings
    from backend.app.dependencies import get_password_hash

    existing = await session.execute(
        text("SELECT id::text AS id FROM users WHERE username = :u"),
        {"u": settings.admin_username},
    )
    row = existing.scalar_one_or_none()
    if row is not None:
        return _admin_current_user(str(row))

    created = await session.execute(
        text(
            """
            INSERT INTO users (username, hashed_password, role, email, metadata, created_at)
            VALUES (:username, :hashed_password, 'admin', :email, '{}'::jsonb, :created_at)
            RETURNING id::text AS id
            """
        ),
        {
            "username": settings.admin_username,
            "hashed_password": get_password_hash(settings.admin_password),
            "email": settings.admin_email,
            "created_at": datetime.now(timezone.utc),
        },
    )
    await session.commit()
    return _admin_current_user(str(created.scalar_one()))


async def _ensure_species(session) -> str:
    result = await session.execute(
        text("SELECT id::text AS id FROM species WHERE lower(name) = lower(:name)"),
        {"name": SPECIES},
    )
    existing = result.scalar_one_or_none()
    if existing:
        return str(existing)
    created = await session.execute(
        text(
            """
            INSERT INTO species (name, common_name, tax_id)
            VALUES (:name, 'Human', 9606)
            RETURNING id::text AS id
            """
        ),
        {"name": SPECIES},
    )
    await session.commit()
    return str(created.scalar_one())


async def _ensure_assembly(session, species_id: str) -> str:
    result = await session.execute(
        text(
            """
            SELECT id::text AS id FROM assemblies
            WHERE species_id = CAST(:sid AS uuid) AND assembly_name = :name
            ORDER BY release_date DESC, version DESC LIMIT 1
            """
        ),
        {"sid": species_id, "name": ASSEMBLY},
    )
    existing = result.scalar_one_or_none()
    if existing:
        return str(existing)
    created = await session.execute(
        text(
            """
            INSERT INTO assemblies (species_id, assembly_name, version, release_date)
            VALUES (CAST(:sid AS uuid), :name, 'e2e', :rel)
            RETURNING id::text AS id
            """
        ),
        {"sid": species_id, "name": ASSEMBLY, "rel": date(2013, 12, 17)},
    )
    await session.commit()
    return str(created.scalar_one())


async def _ensure_project(session, *, species_id: str, assembly_id: str, admin_user_id: str) -> str:
    result = await session.execute(
        text(
            """
            SELECT id::text AS id, species_id::text AS species_id, assembly_id::text AS assembly_id
            FROM projects WHERE name = :name
            """
        ),
        {"name": PROJECT_NAME},
    )
    row = result.mappings().first()
    if row is None:
        created = await session.execute(
            text(
                """
                INSERT INTO projects (name, description, species_id, assembly_id, metadata)
                VALUES (:name, 'E2E golden trio', CAST(:sid AS uuid), CAST(:aid AS uuid), '{}'::jsonb)
                RETURNING id::text AS id
                """
            ),
            {"name": PROJECT_NAME, "sid": species_id, "aid": assembly_id},
        )
        project_id = str(created.scalar_one())
    else:
        project_id = str(row["id"])
        if str(row["species_id"]) != species_id or str(row["assembly_id"]) != assembly_id:
            raise RuntimeError(f"Project '{PROJECT_NAME}' bound to a different species/assembly")
    await session.execute(
        text(
            """
            INSERT INTO project_users (project_id, user_id)
            VALUES (CAST(:pid AS uuid), CAST(:uid AS uuid))
            ON CONFLICT DO NOTHING
            """
        ),
        {"pid": project_id, "uid": admin_user_id},
    )
    await session.commit()
    return project_id


async def ensure_e2e_project(session) -> tuple[Any, str, str]:
    """Set up admin -> species -> assembly -> project bound to GRCh38 and return
    ``(admin_current_user, project_id, assembly_id)``. Idempotent."""
    admin = await _ensure_admin(session)
    species_id = await _ensure_species(session)
    assembly_id = await _ensure_assembly(session, species_id)
    project_id = await _ensure_project(
        session, species_id=species_id, assembly_id=assembly_id, admin_user_id=admin.id
    )
    return admin, project_id, assembly_id


async def login_admin_token(ac) -> str:
    """Log in as the seeded admin over an httpx client and return the bearer
    token. Used with an in-loop ASGITransport client so the global DB engine /
    ClickHouse client stay bound to a single event loop."""
    from backend.app.core.config import settings

    resp = await ac.post(
        "/api/auth/login",
        json={"email": settings.admin_email, "password": settings.admin_password},
    )
    if resp.status_code != 200:
        raise RuntimeError(f"admin login failed: {resp.status_code} {resp.text}")
    return resp.json()["access_token"]


async def import_golden_trio(root: Path) -> dict[str, Any]:
    """Set up a project, run the real package import, and read back per-stage
    facts. Returns a plain dict so the test asserts synchronously."""
    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.services import family_package_import as package_import
    from backend.app.services.clickhouse_variant_storage import (
        count_family_small_variants,
        count_family_structural_variants,
        ensure_clickhouse_variant_tables,
    )
    from backend.app.services.clickhouse_interval_tracks import count_interval_track_source_rows
    from backend.app.services.family_metadata_context import build_family_metadata_context
    from backend.app.services.clickhouse_family_variants import get_family_small_variants_page
    from backend.app.services.repeat_expansion_pg import seed_builtin_repeat_catalog

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(ASSEMBLY)

    sessionmaker = get_postgres_sessionmaker()

    async with sessionmaker() as session:
        admin, project_id, assembly_id = await ensure_e2e_project(session)
        await seed_builtin_repeat_catalog(session)
        # The phenotype import drops HPO terms not present in hpo_term. CI does not
        # bootstrap the ontology (HPO_BOOTSTRAP_ON_STARTUP=false), so seed the golden
        # fixture's term deterministically — otherwise the HPO assertion is
        # environment-dependent (passes locally where the ontology is loaded, fails
        # on a fresh CI database).
        await session.execute(
            text(
                "INSERT INTO hpo_term (hpo_id, label) VALUES ('HP:0001250', 'Seizure') "
                "ON CONFLICT (hpo_id) DO NOTHING"
            )
        )
        await session.commit()

    async with sessionmaker() as session:
        result = await package_import.execute_family_package_import(
            session,
            folder_path=str(root),
            project_id=project_id,
            dry_run=False,
            user=admin,
            conflict_mode="overwrite",
        )

    facts: dict[str, Any] = {
        "project_id": project_id,
        "assembly_id": assembly_id,
        "completed": result.completed,
        "error": result.error,
        "family_id": result.family_id,
        "dataset_status": {d.dataset_type: d.status for d in result.datasets},
        "dataset_message": {d.dataset_type: d.message for d in result.datasets},
        "logs_tail": result.logs[-3:] if result.logs else [],
    }

    async with sessionmaker() as session:
        family_uuid = (
            await session.execute(
                text("SELECT id::text FROM families WHERE family_id = :f"), {"f": FAMILY_ID}
            )
        ).scalar_one()
        facts["family_uuid"] = family_uuid

        facts["n_samples"] = (
            await session.execute(
                text("SELECT count(*) FROM samples WHERE family_id = CAST(:f AS uuid)"),
                {"f": family_uuid},
            )
        ).scalar_one()
        facts["n_members"] = (
            await session.execute(
                text("SELECT count(*) FROM family_members WHERE family_id = CAST(:f AS uuid)"),
                {"f": family_uuid},
            )
        ).scalar_one()
        facts["member_roles"] = {
            r["sample_id"]: r["role"]
            for r in (
                await session.execute(
                    text(
                        """
                        SELECT s.sample_id, fm.role
                        FROM family_members fm JOIN samples s ON s.id = fm.sample_id
                        WHERE fm.family_id = CAST(:f AS uuid)
                        """
                    ),
                    {"f": family_uuid},
                )
            ).mappings().all()
        }
        facts["n_hpo"] = (
            await session.execute(
                text(
                    """
                    SELECT count(*) FROM individual_hpo ih
                    JOIN samples s ON s.id = ih.sample_id
                    WHERE s.family_id = CAST(:f AS uuid)
                    """
                ),
                {"f": family_uuid},
            )
        ).scalar_one()

        repeat = (
            await session.execute(
                text(
                    """
                    SELECT re.status, re.pathogenic_min, re.gene, re.locus_id, s.sample_id
                    FROM repeat_expansions re JOIN samples s ON s.id = re.sample_id
                    WHERE s.family_id = CAST(:f AS uuid) AND re.locus_id = 'HD_HTT'
                    ORDER BY s.sample_id
                    """
                ),
                {"f": family_uuid},
            )
        ).mappings().all()
        facts["repeat_rows"] = [dict(r) for r in repeat]

        facts["paraphase_genes"] = [
            r
            for r in (
                await session.execute(
                    text(
                        """
                        SELECT pr.gene_symbol
                        FROM sample_paraphase_results pr JOIN samples s ON s.id = pr.sample_id
                        WHERE s.family_id = CAST(:f AS uuid)
                        ORDER BY pr.gene_symbol
                        """
                    ),
                    {"f": family_uuid},
                )
            ).scalars().all()
        ]

        # SUM(row_count) across coverage source registry rows...
        facts["coverage_total_rows"] = await count_interval_track_source_rows(
            session, family_uuid=family_uuid, track_type="coverage"
        )
        # ...and the number of registry entries (one per sample's coverage BED).
        facts["coverage_source_count"] = (
            await session.execute(
                text(
                    """
                    SELECT count(*) FROM sample_interval_track_sources
                    WHERE family_id = CAST(:f AS uuid) AND track_type = 'coverage'
                    """
                ),
                {"f": family_uuid},
            )
        ).scalar_one()

        # Read path: build the same context the API uses and page the family.
        context = await build_family_metadata_context(
            session, family_identifier=FAMILY_ID, user=admin, project_id=project_id
        )
        ch_page = await get_family_small_variants_page(
            session, context=context, page=1, page_size=100, inheritance="compound_het"
        )
        facts["comp_het_groups"] = [
            {"gene": g.gene, "n": len(g.variants)} for g in getattr(ch_page, "variant_groups", [])
        ]

        brca2_page = await get_family_small_variants_page(
            session, context=context, page=1, page_size=100, gene="BRCA2"
        )
        facts["brca2_sv_second_hits"] = [
            v.sv_second_hit.model_dump() for v in brca2_page.variants if v.sv_second_hit is not None
        ]

    facts["n_small_variants"] = await count_family_small_variants(
        ASSEMBLY, family_uuid, project_ids=[project_id]
    )
    facts["n_structural_variants"] = await count_family_structural_variants(
        ASSEMBLY, family_uuid, project_ids=[project_id]
    )
    return facts


def import_golden_trio_sync(root: Path) -> dict[str, Any]:
    """Synchronous wrapper for tests: runs the import under a managed loop."""
    return run_async(lambda: import_golden_trio(root))
