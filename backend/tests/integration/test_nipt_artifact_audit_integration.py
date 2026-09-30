"""Every change to the NIPT artifact list is a hash-chained clinical audit event (#683) —
real Postgres.

The list decides which variants every NIPT analysis of its scope filters out, and it is
curated through the admin API alone. This adds an entry, updates it, auto-seeds two more
(the recurrence lookup stubbed), removes one, and checks that each change left one event
naming the actor, the variant and its before/after state, on the list's own chain, and that
the chain verifies.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


def test_each_artifact_list_change_is_audited_on_its_own_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )
    from backend.app.services import nipt_artifact_pg
    from backend.app.services.clinical_audit_service import verify_clinical_audit_chain

    tag = uuid4().hex[:8].upper()
    assay = f"AUDIT_{tag}"

    async def fake_recurrent(assembly_name, *, min_carrier_samples, **_kwargs):
        return [(f"2-200-C-T-{tag}", 6), (f"3-300-G-A-{tag}", 5)]

    monkeypatch.setattr(nipt_artifact_pg, "fetch_recurrent_small_variant_ids", fake_recurrent)

    async def _events(session, since) -> list[dict]:
        rows = await session.execute(
            text(
                "SELECT action, actor, variant_id, before, after, metadata "
                "FROM clinical_audit_events "
                "WHERE family_identifier = :chain AND created_at > :since "
                "AND metadata ->> 'assay_key' = :assay ORDER BY created_at"
            ),
            {"chain": nipt_artifact_pg.NIPT_ARTIFACT_AUDIT_CHAIN, "since": since, "assay": assay},
        )
        return [dict(row) for row in rows.mappings().all()]

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            async with sm() as s:
                species = (
                    await s.execute(
                        text(
                            "INSERT INTO species (name, common_name, tax_id) "
                            "VALUES (:n, 'test', :t) RETURNING id::text"
                        ),
                        {"n": f"nipt-audit {tag}", "t": uuid4().int % 2_000_000_000},
                    )
                ).scalar_one()
                assembly_id = (
                    await s.execute(
                        text(
                            "INSERT INTO assemblies (species_id, assembly_name, version, release_date) "
                            "VALUES (CAST(:s AS uuid), :a, 'v1', '2020-01-01') RETURNING id::text"
                        ),
                        {"s": species, "a": f"NIPTAUDIT{tag}"},
                    )
                ).scalar_one()
                since = (await s.execute(text("SELECT now()"))).scalar_one()
                await s.commit()

            variant = f"1-100-A-G-{tag}"
            async with sm() as s:
                added = await nipt_artifact_pg.add_nipt_artifact(
                    s, assembly_id=assembly_id, assay_key=assay, variant_id=variant,
                    label="seen in the run", actor="curator",
                )
                await nipt_artifact_pg.add_nipt_artifact(
                    s, assembly_id=assembly_id, assay_key=assay, variant_id=variant,
                    label="recurrent in the run", actor="curator",
                )
                seeded = await nipt_artifact_pg.auto_seed_nipt_artifacts(
                    s, assembly_id=assembly_id, assay_key=assay, actor="curator",
                )
                assert seeded["seeded"] == 2
                assert await nipt_artifact_pg.delete_nipt_artifact(
                    s, artifact_id=added["id"], actor="curator",
                )
                # Nothing left to remove: no event.
                assert not await nipt_artifact_pg.delete_nipt_artifact(
                    s, artifact_id=added["id"], actor="curator",
                )

            async with sm() as s:
                events = await _events(s, since)
                assert [e["action"] for e in events] == [
                    "nipt_artifact_added",
                    "nipt_artifact_updated",
                    "nipt_artifacts_auto_seeded",
                    "nipt_artifact_removed",
                ]
                assert {e["actor"] for e in events} == {"curator"}
                added_event, updated_event, seed_event, removed_event = events
                assert added_event["variant_id"] == variant and added_event["before"] is None
                assert added_event["after"]["label"] == "seen in the run"
                assert updated_event["before"]["label"] == "seen in the run"
                assert updated_event["after"]["label"] == "recurrent in the run"
                assert [v["variant_id"] for v in seed_event["after"]["variants"]] == [
                    f"2-200-C-T-{tag}",
                    f"3-300-G-A-{tag}",
                ]
                assert removed_event["before"]["variant_id"] == variant
                assert removed_event["after"] is None

                chain = await verify_clinical_audit_chain(s, nipt_artifact_pg.NIPT_ARTIFACT_AUDIT_CHAIN)
                assert chain.verified, chain.reason
        finally:
            await close_postgres_engine()

    asyncio.run(_run())
