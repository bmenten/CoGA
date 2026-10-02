"""Integration test: a link of unknown degree and a parent's phase corrections are stored.

A ``relative`` relationship (a member related to the family through another, by an
unknown degree) must pass the ``family_relationships`` type check, and a structure edit
that does not send the links of unknown degree must keep them. The parents' phase
switches the haplotype blocks undid are written into ``families.metadata`` with
``jsonb_set``, which must create the key on a family without it, keep the other keys,
and replace an earlier upload's list.

The unit tests pin the values; these run the real writers against Postgres, on families
of their own, deleted afterwards. Only Postgres is needed. Skipped unless
``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


async def _family(session, label: str, members: list[tuple[str, str, str]]) -> str:
    family_uuid = (
        await session.execute(
            text(
                "INSERT INTO families (family_id, metadata) VALUES (:f, CAST(:m AS jsonb)) RETURNING id::text"
            ),
            {"f": label, "m": json.dumps({"pipeline": {"reference": "GRCh38"}})},
        )
    ).scalar_one()
    for sample_id, sex, role in members:
        sample_uuid = (
            await session.execute(
                text(
                    "INSERT INTO samples (sample_id, family_id, sex) "
                    "VALUES (:s, CAST(:f AS uuid), :sex) RETURNING id::text"
                ),
                {"s": f"{label}-{sample_id}", "f": family_uuid, "sex": sex},
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO family_members (family_id, sample_id, role, clinical_status) "
                "VALUES (CAST(:f AS uuid), CAST(:s AS uuid), :role, 'unknown')"
            ),
            {"f": family_uuid, "s": sample_uuid, "role": role},
        )
    await session.commit()
    return str(family_uuid)


def test_a_link_of_unknown_degree_is_stored_and_kept_and_the_phase_corrections_replaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.app.core.postgres import close_postgres_engine, get_postgres_sessionmaker, init_postgres_schema
    from backend.app.schemas import FamilyStructureUpdate
    from backend.app.services import family_structure_service as service
    from backend.app.services.access_control import CurrentUser
    from backend.app.services.haplotype_phase_correction import PhaseCorrection
    from backend.app.services.variant_upload_service import record_haplotype_phase_corrections

    async def no_counts(_session, *, family_uuid):
        return {}

    monkeypatch.setattr(service, "_family_genomic_data_counts", no_counts)
    label = f"itest-relative-{uuid4()}"
    members = [("DAD", "male", "father"), ("MOM", "female", "mother"), ("EMB", "und", "embryo"), ("IDX", "female", "relative")]

    async def _relatives(session) -> list[tuple[str, str, str]]:
        rows = await session.execute(
            text(
                """
                SELECT fr.relationship_type, sa.sample_id, sb.sample_id
                FROM family_relationships fr
                JOIN families f ON f.id = fr.family_id
                JOIN samples sa ON sa.id = fr.sample_id_a
                JOIN samples sb ON sb.id = fr.sample_id_b
                WHERE f.family_id = :f AND fr.active AND fr.relationship_type = 'relative'
                """
            ),
            {"f": label},
        )
        return [tuple(row) for row in rows.all()]

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
                family_uuid = await _family(session, label, members)
            admin = CurrentUser(
                id=user_id,
                username="itest-admin",
                email="itest-admin@example.org",
                role="admin",
                created_at=datetime.now(timezone.utc),
            )
            parent_child = [
                {"parent": f"{label}-DAD", "child": f"{label}-EMB", "parent_role": "father"},
                {"parent": f"{label}-MOM", "child": f"{label}-EMB", "parent_role": "mother"},
            ]
            async with sessionmaker() as session:
                await service.update_family_structure_for_admin(
                    session,
                    family_id=label,
                    update=FamilyStructureUpdate.model_validate(
                        {
                            "relationships": {
                                "parent_child": parent_child,
                                "relatives": [{"member": f"{label}-IDX", "related_to": f"{label}-MOM"}],
                            }
                        }
                    ),
                    user=admin,
                )
            async with sessionmaker() as session:
                assert await _relatives(session) == [("relative", f"{label}-MOM", f"{label}-IDX")]
            # An edit that does not send the links of unknown degree keeps them.
            async with sessionmaker() as session:
                await service.update_family_structure_for_admin(
                    session,
                    family_id=label,
                    update=FamilyStructureUpdate.model_validate({"relationships": {"parent_child": parent_child}}),
                    user=admin,
                )
            async with sessionmaker() as session:
                assert await _relatives(session) == [("relative", f"{label}-MOM", f"{label}-IDX")]

            correction = PhaseCorrection(
                parent=f"{label}-DAD", side="father", chrom="7", position=30_000_000, end=30_400_000,
                children_switching=4, children=5,
            )
            for corrections in ([correction], []):
                async with sessionmaker() as session:
                    await record_haplotype_phase_corrections(session, family_uuid=family_uuid, corrections=corrections)
                    await session.commit()
                async with sessionmaker() as session:
                    metadata = (
                        await session.execute(
                            text("SELECT metadata FROM families WHERE id = CAST(:id AS uuid)"), {"id": family_uuid}
                        )
                    ).scalar_one()
                assert metadata["haplotype_phase_corrections"] == [c.as_metadata() for c in corrections]
                assert metadata["pipeline"] == {"reference": "GRCh38"}
        finally:
            if sessionmaker is not None:
                async with sessionmaker() as session:
                    await session.execute(text("DELETE FROM families WHERE family_id = :f"), {"f": label})
                    if user_id is not None:
                        await session.execute(text("DELETE FROM users WHERE id = CAST(:id AS uuid)"), {"id": user_id})
                    await session.commit()
            await close_postgres_engine()

    asyncio.run(_run())
