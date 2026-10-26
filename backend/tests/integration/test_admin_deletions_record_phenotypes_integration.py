"""An administrator's deletion records the phenotypes it removes, on real Postgres (TRACE-16).

A sample's or a family's phenotypes go with it (``individual_hpo`` cascades from both), but an
administrator's deletion with its data (Admin → Data) recorded them as no ``phenotype`` event:
its ``pedigree`` or ``family_deleted`` event does not name them, so the trail did not say which
phenotypes the family lost, or when. A PED upload or the Family Builder that replaces a family,
and a failed import's rollback, recorded theirs already.

A family of two members, each with a phenotype, loses the father with his data, then itself:
each deletion writes one ``phenotype`` event per member whose phenotypes went, before its own
event, as made by the administrator, with the phenotypes as they were and none after, and the
family's chain verifies. Synthetic data only. Skipped unless ``RUN_INTEGRATION=1`` (see
conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


async def _seed(session, label: str) -> dict[str, str]:
    """A family of a proband and a father, each with one phenotype, and an administrator."""
    for hpo_id, name in (("HP:0001250", "Seizure"), ("HP:0001263", "Global developmental delay")):
        await session.execute(
            text("INSERT INTO hpo_term (hpo_id, label) VALUES (:i, :l) ON CONFLICT (hpo_id) DO NOTHING"),
            {"i": hpo_id, "l": name},
        )
    seeded = {
        "admin": (
            await session.execute(
                text(
                    "INSERT INTO users (username, hashed_password, role, email) "
                    "VALUES (:u, 'x', 'admin', :e) RETURNING id::text"
                ),
                {"u": f"itest-{uuid4()}", "e": f"itest-{uuid4()}@example.org"},
            )
        ).scalar_one(),
        "family": (
            await session.execute(text("INSERT INTO families (family_id) VALUES (:f) RETURNING id::text"), {"f": label})
        ).scalar_one(),
    }
    for role, sex, hpo_id, status in (
        ("proband", "female", "HP:0001250", "present"),
        ("father", "male", "HP:0001263", "absent"),
    ):
        sample = (
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
            {"f": seeded["family"], "s": sample, "role": role},
        )
        await session.execute(
            text(
                "INSERT INTO individual_hpo (family_id, sample_id, hpo_id, status) "
                "VALUES (CAST(:f AS uuid), CAST(:s AS uuid), :h, :st)"
            ),
            {"f": seeded["family"], "s": sample, "h": hpo_id, "st": status},
        )
    await session.commit()
    return seeded


async def _events(session, label: str) -> list[dict]:
    rows = await session.execute(
        text(
            "SELECT action, actor, summary, before, after, metadata FROM clinical_audit_events "
            "WHERE family_identifier = :f ORDER BY created_at, id"
        ),
        {"f": label},
    )
    return [dict(row) for row in rows.mappings().all()]


def test_an_administrators_deletions_record_the_phenotypes_they_remove() -> None:
    from backend.app.core.postgres import close_postgres_engine, get_postgres_sessionmaker, init_postgres_schema
    from backend.app.services import admin_service
    from backend.app.services.access_control import CurrentUser
    from backend.app.services.clinical_audit_service import verify_clinical_audit_chain

    label = f"TRACE16-{uuid4().hex[:12]}"

    async def scenario() -> None:
        await init_postgres_schema()
        sessionmaker = get_postgres_sessionmaker()
        async with sessionmaker() as session:
            seeded = await _seed(session, label)
        admin = CurrentUser(
            id=seeded["admin"],
            username="trace16-admin",
            email="trace16-admin@example.org",
            role="admin",
            created_at=datetime.now(timezone.utc),
        )

        # The father, with his data: his phenotype is recorded as removed, before the pedigree
        # change; the proband's is not touched.
        async with sessionmaker() as session:
            await admin_service.delete_sample_with_data(session, f"{label}-father", True, user=admin)
        async with sessionmaker() as session:
            events = await _events(session, label)
        assert [event["action"] for event in events] == ["phenotype", "pedigree"]
        phenotype = events[0]
        assert phenotype["actor"] == "trace16-admin"
        assert phenotype["metadata"] == {
            "sample_id": f"{label}-father",
            "source": "admin_deletion",
            "change": "deleted_with_sample",
        }
        assert phenotype["summary"].startswith(
            f"Phenotypes removed for {label}-father: an administrator deleted the sample with its data:"
        )
        assert phenotype["after"] is None
        assert [(item["hpo_id"], item["status"]) for item in phenotype["before"]["phenotypes"]] == [
            ("HP:0001263", "absent")
        ]

        # The family, with its data: the proband's phenotype, then the family's deletion.
        async with sessionmaker() as session:
            await admin_service.delete_family_with_data(session, label, True, user=admin)
        async with sessionmaker() as session:
            events = await _events(session, label)
            chain = await verify_clinical_audit_chain(session, label)
        assert [event["action"] for event in events] == ["phenotype", "pedigree", "phenotype", "family_deleted"]
        removed = events[2]
        assert removed["metadata"] == {
            "sample_id": f"{label}-proband",
            "source": "admin_deletion",
            "change": "deleted_with_family",
        }
        assert removed["summary"].startswith(
            f"Phenotypes removed for {label}-proband: an administrator deleted the family with its data:"
        )
        assert [(item["hpo_id"], item["status"]) for item in removed["before"]["phenotypes"]] == [
            ("HP:0001250", "present")
        ]
        assert removed["after"] is None
        assert chain.verified, chain
        await close_postgres_engine()

    asyncio.run(scenario())
