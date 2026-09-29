"""Replacing a family's annotation manifest is on the family's hash chain (real Postgres).

The manifest row is overwritten in place and every later sign-out freezes it into the
signed report, so an admin's replacement must leave a clinical audit event with the
manifest it replaced and the one it wrote. Against real Postgres this proves that:
- the REAL writers (the VCF-header import path, then ``set_family_annotation_manifest``)
  put the replacement on the family's chain after the events already there, and
  ``verify_clinical_audit_chain`` still accepts the chain (JSONB round-trip included);
- the overwrite and its event share one transaction: when the event cannot be written,
  the manifest is not replaced.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
Admin gating and the event's content are unit-tested in ``test_annotation_manifest.py``
and ``test_admin_route_gating.py``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


async def _fresh_family(session, label: str) -> str:
    return (
        await session.execute(
            text("INSERT INTO families (family_id) VALUES (:f) RETURNING id::text"),
            {"f": label},
        )
    ).scalar_one()


async def _fresh_admin(session):
    from backend.app.services.access_control import CurrentUser

    username = f"manifest-admin-{uuid4()}"
    user_id = (
        await session.execute(
            text(
                "INSERT INTO users (username, hashed_password, role, email) "
                "VALUES (:u, 'x', 'admin', :e) RETURNING id::text"
            ),
            {"u": username, "e": f"{username}@example.org"},
        )
    ).scalar_one()
    return CurrentUser(
        id=user_id,
        username=username,
        email=f"{username}@example.org",
        role="admin",
        created_at=datetime.now(timezone.utc),
    )


async def _events(session, label: str) -> list[dict]:
    rows = (
        await session.execute(
            text(
                "SELECT action, actor, actor_id::text AS actor_id, before, after "
                "FROM clinical_audit_events WHERE family_identifier = :f "
                "ORDER BY created_at ASC, id ASC"
            ),
            {"f": label},
        )
    ).mappings().all()
    return [dict(row) for row in rows]


async def _stored_manifest(session, family_uuid: str) -> dict:
    row = (
        await session.execute(
            text(
                "SELECT source, modules FROM family_annotation_manifest "
                "WHERE family_id = CAST(:f AS uuid)"
            ),
            {"f": family_uuid},
        )
    ).mappings().one()
    return dict(row)


def test_a_manifest_replacement_is_chained_and_atomic(monkeypatch) -> None:
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )
    from backend.app.services import annotation_manifest_service as ams
    from backend.app.services.clinical_audit_service import (
        record_clinical_event,
        verify_clinical_audit_chain,
    )

    imported = {"vep": {"version": "110"}, "gnomad": {"version": "4.1"}}
    # Floats included: the chain must survive their JSONB round-trip.
    replacement = {
        "vep": {"version": "112", "cache": "112_GRCh38"},
        "gnomad": {"version": "4.1", "af_threshold": 0.01},
        "clinvar": "2026-09",
    }

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            label = f"manifest-{uuid4()}"
            async with sm() as s:
                family_uuid = await _fresh_family(s, label)
                admin = await _fresh_admin(s)
                # A classification already on the family's chain.
                await record_clinical_event(
                    s, family_uuid=family_uuid, family_identifier=label,
                    variant_id="1-100-A-G", actor="alice", actor_id=None,
                    action="classification", summary="event 0", metadata={},
                )
                await s.commit()
                # The import's provenance, written by the real VCF-header path.
                await ams.merge_vcf_header_provenance(
                    s, family_uuid=family_uuid, assembly_id=None, modules=imported, modality="snv"
                )
                await s.commit()

            async with sm() as s:
                out = await ams.set_family_annotation_manifest(
                    s, family_id=label, user=admin, modules=replacement
                )
            assert out["source"] == "manual"
            assert {m["key"]: m["version"] for m in out["modules"] if m["layer"] == "pipeline"} == {
                "vep": "112", "gnomad": "4.1", "clinvar": "2026-09",
            }

            async with sm() as s:
                events = await _events(s, label)
                assert [e["action"] for e in events] == ["classification", "annotation_manifest"]
                event = events[-1]
                assert (event["actor"], event["actor_id"]) == (admin.username, admin.id)
                assert event["before"]["source"] == "vcf_header"
                assert event["before"]["recorded_by"] == "import (vcf_header)"
                assert {k: v["version"] for k, v in event["before"]["modules"].items()} == {
                    "vep": "110", "gnomad": "4.1",
                }
                assert event["after"] == {"source": "manual", "modules": replacement}
                chain = await verify_clinical_audit_chain(s, label)
                assert chain.verified and chain.rows_checked == 2, chain
                stored = await _stored_manifest(s, family_uuid)
                assert stored == {"source": "manual", "modules": replacement}

            # The event cannot be written -> the manifest is not replaced either.
            async def _failing_event(*args, **kwargs):
                raise RuntimeError("clinical audit unavailable")

            monkeypatch.setattr(ams, "record_clinical_event", _failing_event)
            with pytest.raises(RuntimeError, match="clinical audit unavailable"):
                async with sm() as s:
                    await ams.set_family_annotation_manifest(
                        s, family_id=label, user=admin, modules={"vep": {"version": "999"}}
                    )
            monkeypatch.undo()

            async with sm() as s:
                assert await _stored_manifest(s, family_uuid) == {
                    "source": "manual", "modules": replacement,
                }
                assert len(await _events(s, label)) == 2
                assert (await verify_clinical_audit_chain(s, label)).verified
        finally:
            await close_postgres_engine()

    asyncio.run(_run())
