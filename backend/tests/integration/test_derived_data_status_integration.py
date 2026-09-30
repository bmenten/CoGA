"""Integration test: the stale markers in `families.metadata.derived_data_status` are stored.

A structure update (`PUT /families/{id}/structure`) writes what it made stale, and whether
it kept the imported data, under `derived_data_status.family_metadata`; an HPO edit writes
`derived_data_status.hpo_annotations`. Both went through a nested `jsonb_set`, which does
nothing when the parent key is missing, and no family has a `derived_data_status` until
the first marker, so neither was ever stored.

The unit tests (test_family_structure_cleared_data.py) pin the values; these run the real
writers against Postgres, on families of their own, and read back what was stored. The
ClickHouse counts and deletes are stood in for, so only Postgres is needed. The writers
commit, so the families and the user created here are deleted afterwards.
Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

DATA = {"small_variants": 120, "structural_variants": 4}


async def _family_with_two_members(session, label: str) -> None:
    family_uuid = (
        await session.execute(
            text("INSERT INTO families (family_id) VALUES (:f) RETURNING id::text"), {"f": label}
        )
    ).scalar_one()
    for sample_id, sex, role in ((f"{label}-F", "male", "father"), (f"{label}-P", "female", "proband")):
        sample_uuid = (
            await session.execute(
                text(
                    "INSERT INTO samples (sample_id, family_id, sex) "
                    "VALUES (:s, CAST(:f AS uuid), :sex) RETURNING id::text"
                ),
                {"s": sample_id, "f": family_uuid, "sex": sex},
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO family_members (family_id, sample_id, role, clinical_status) "
                "VALUES (CAST(:f AS uuid), CAST(:s AS uuid), :role, 'unaffected')"
            ),
            {"f": family_uuid, "s": sample_uuid, "role": role},
        )
    await session.commit()


async def _stored(session, label: str) -> tuple[dict[str, Any], dict[str, Any]]:
    status = (
        await session.execute(
            text(
                "SELECT metadata -> 'derived_data_status' -> 'family_metadata' "
                "FROM families WHERE family_id = :f"
            ),
            {"f": label},
        )
    ).scalar_one()
    version = (
        await session.execute(
            text(
                "SELECT v.metadata FROM family_structure_versions v "
                "JOIN families f ON f.id = v.family_id WHERE f.family_id = :f "
                "ORDER BY v.version DESC LIMIT 1"
            ),
            {"f": label},
        )
    ).scalar_one()
    return status, version


def test_a_structure_update_records_whether_it_kept_the_data(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )
    from backend.app.schemas import FamilyStructureUpdate
    from backend.app.services import family_structure_service as service
    from backend.app.services.access_control import CurrentUser

    cleared: list[str] = []

    async def fake_counts(_session, *, family_uuid):
        return dict(DATA)

    async def fake_clear(_session, *, family_uuid):
        cleared.append(family_uuid)
        return dict(DATA)

    monkeypatch.setattr(service, "_family_genomic_data_counts", fake_counts)
    monkeypatch.setattr(service, "_clear_family_genomic_data", fake_clear)
    kept_label, cleared_label = f"itest-structure-kept-{uuid4()}", f"itest-structure-cleared-{uuid4()}"

    async def _run() -> None:
        sessionmaker = None
        user_id = None
        try:
            await init_postgres_schema()
            sessionmaker = get_postgres_sessionmaker()
            async with sessionmaker() as session:
                user_id = (
                    await session.execute(
                        text(
                            "INSERT INTO users (username, hashed_password, role, email) "
                            "VALUES (:u, 'x', 'admin', :e) RETURNING id::text"
                        ),
                        {"u": f"itest-{uuid4()}", "e": f"itest-{uuid4()}@example.org"},
                    )
                ).scalar_one()
                await _family_with_two_members(session, kept_label)
                await _family_with_two_members(session, cleared_label)
            admin = CurrentUser(
                id=user_id,
                username="itest-admin",
                email="itest-admin@example.org",
                role="admin",
                created_at=datetime.now(timezone.utc),
            )

            for label, clear in ((kept_label, False), (cleared_label, True)):
                async with sessionmaker() as session:
                    await service.update_family_structure_for_admin(
                        session,
                        family_id=label,
                        update=FamilyStructureUpdate(
                            remove_members=[f"{label}-F"], clear_existing_genomic_data=clear
                        ),
                        user=admin,
                    )

            async with sessionmaker() as session:
                kept_status, kept_version = await _stored(session, kept_label)
                cleared_status, cleared_version = await _stored(session, cleared_label)

            assert kept_status["state"] == "stale"
            assert kept_status["raw_datasets_preserved"] is True
            assert kept_version["cleared_data_counts"] == {}

            assert len(cleared) == 1
            assert cleared_status["state"] == "stale"
            assert cleared_status["raw_datasets_preserved"] is False
            assert cleared_version["cleared_data_counts"] == DATA
        finally:
            if sessionmaker is not None:
                async with sessionmaker() as session:
                    await session.execute(
                        text("DELETE FROM families WHERE family_id = ANY(:labels)"),
                        {"labels": [kept_label, cleared_label]},
                    )
                    if user_id is not None:
                        await session.execute(
                            text("DELETE FROM users WHERE id = CAST(:id AS uuid)"), {"id": user_id}
                        )
                    await session.commit()
            await close_postgres_engine()

    asyncio.run(_run())


def test_an_hpo_edit_marks_the_phenotype_views_stale_and_keeps_the_rest() -> None:
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )
    from backend.app.services.hpo_service import mark_family_hpo_annotations_stale

    existing = {
        "pipeline": {"reference": "GRCh38"},
        "derived_data_status": {"family_metadata": {"state": "stale", "scopes": ["segregation"]}},
    }

    async def _marked(session, metadata: dict[str, Any]) -> dict[str, Any]:
        family_uuid = (
            await session.execute(
                text(
                    "INSERT INTO families (family_id, metadata) "
                    "VALUES (:f, CAST(:m AS jsonb)) RETURNING id::text"
                ),
                {"f": f"itest-hpo-stale-{uuid4()}", "m": json.dumps(metadata)},
            )
        ).scalar_one()
        await mark_family_hpo_annotations_stale(
            session, family_uuid=family_uuid, sample_id="PROBAND", reason="hpo_annotation_created"
        )
        return (
            await session.execute(
                text("SELECT metadata FROM families WHERE id = CAST(:id AS uuid)"), {"id": family_uuid}
            )
        ).scalar_one()

    async def _run() -> None:
        try:
            await init_postgres_schema()
            async with get_postgres_sessionmaker()() as session:
                try:
                    fresh = await _marked(session, {})
                    earlier = await _marked(session, existing)
                finally:
                    await session.rollback()

            # A family with no marker yet gets one.
            assert fresh["derived_data_status"]["hpo_annotations"]["state"] == "stale"
            assert fresh["derived_data_status"]["hpo_annotations"]["sample_id"] == "PROBAND"
            assert fresh["derived_data_status"]["updated_at"]
            # An earlier structure marker and the rest of the metadata are kept.
            assert earlier["pipeline"] == {"reference": "GRCh38"}
            assert earlier["derived_data_status"]["family_metadata"] == existing["derived_data_status"]["family_metadata"]
            assert earlier["derived_data_status"]["hpo_annotations"]["reason"] == "hpo_annotation_created"
        finally:
            await close_postgres_engine()

    asyncio.run(_run())
