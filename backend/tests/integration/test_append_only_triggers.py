"""Integration tests that FIRE the append-only triggers (real Postgres, smoke job).

These prove the database-level immutability controls on four append-only tables
— ``audit_log_events`` (the HTTP access log), ``clinical_audit_events`` (the clinical
action log), ``report_signouts`` (frozen signed reports) and ``qc_threshold_changes``
(the history of QC acceptance limits): a direct UPDATE of a non-carve-out column and any
DELETE are rejected by the ``BEFORE UPDATE/DELETE`` triggers, while the FK ``ON DELETE
SET NULL`` carve-out (the user/actor unlink that keeps account deletion working) is
permitted — and nothing else. This is the verifying evidence for RTM REQ-TRACE-008 and,
for the QC history, REQ-QC-007. The fifth, ``integrity_anchors``, is not fired here: a
marker anchor would break the anchor-chain verification that
``test_integrity_anchor_integration.py`` runs on the same database. Its REVOKE is
asserted in ``test_app_role_privileges.py``.

SCOPE CAVEAT (review finding C1): the smoke job — like the application — connects as
the DB OWNER, so this proves the triggers block the *owner's* UPDATE/DELETE, NOT that
the running app credential is unable to bypass them (an owner can still
``ALTER TABLE ... DISABLE TRIGGER`` / ``SET session_replication_role='replica'`` /
``TRUNCATE``). Closing the "immutable against the running app credential" claim needs
the non-owner runtime role (#262) plus a test that runs as that constrained role.

The tests deliberately never disable a trigger and never DELETE (it is blocked); the
few marker rows they insert accumulate harmlessly on the per-job ephemeral CI Postgres.
Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.integration


async def _assert_blocked(sessionmaker, sql: str, params: dict, expect_substr: str) -> None:
    """A mutation that must be rejected by an append-only trigger.

    The trigger's ``RAISE EXCEPTION`` (P0001) surfaces as a SQLAlchemy ``DBAPIError``
    whose message carries the exact trigger text; asserting that message is both
    sufficient and a deliberate pin on the trigger's behaviour. (We do not assert the
    concrete ``.orig`` type because the asyncpg dialect wraps it in its own ``Error``.)
    """
    with pytest.raises(DBAPIError) as exc_info:
        async with sessionmaker() as session:
            await session.execute(text(sql), params)
            await session.commit()
    assert expect_substr in str(exc_info.value)


def test_append_only_triggers_block_update_and_delete() -> None:
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )

    async def _run() -> None:
        try:
            # Idempotent; ensures the tables + triggers exist regardless of test order.
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()

            # --- audit_log_events ---
            async with sm() as s:
                ale_id = (
                    await s.execute(
                        text(
                            "INSERT INTO audit_log_events (method, path, status_code) "
                            "VALUES ('GET', '/p1-5-trigger-test', 200) RETURNING id"
                        )
                    )
                ).scalar_one()
                await s.commit()
            await _assert_blocked(
                sm,
                "UPDATE audit_log_events SET status_code = 500 WHERE id = :id",
                {"id": ale_id},
                "audit_log_events is append-only; UPDATE is not permitted",
            )
            await _assert_blocked(
                sm,
                "DELETE FROM audit_log_events WHERE id = :id",
                {"id": ale_id},
                "audit_log_events is append-only; DELETE is not permitted",
            )

            # --- clinical_audit_events ---
            async with sm() as s:
                cae_id = (
                    await s.execute(
                        text(
                            "INSERT INTO clinical_audit_events (actor, action, summary) "
                            "VALUES ('p1-5-test', 'test', 'marker') RETURNING id"
                        )
                    )
                ).scalar_one()
                await s.commit()
            await _assert_blocked(
                sm,
                "UPDATE clinical_audit_events SET summary = 'tampered' WHERE id = :id",
                {"id": cae_id},
                "clinical_audit_events is append-only; UPDATE is not permitted",
            )
            await _assert_blocked(
                sm,
                "DELETE FROM clinical_audit_events WHERE id = :id",
                {"id": cae_id},
                "clinical_audit_events is append-only; DELETE is not permitted",
            )

            # --- report_signouts (a signed report must never change) ---
            async with sm() as s:
                rs_id = (
                    await s.execute(
                        text(
                            "INSERT INTO report_signouts "
                            "(version, signed_out_by, content_hash, snapshot) "
                            "VALUES (1, 'p1-5-test', 'deadbeef', '{}'::jsonb) RETURNING id"
                        )
                    )
                ).scalar_one()
                await s.commit()
            await _assert_blocked(
                sm,
                "UPDATE report_signouts SET content_hash = 'tampered' WHERE id = :id",
                {"id": rs_id},
                "report_signouts is append-only; UPDATE is not permitted",
            )
            await _assert_blocked(
                sm,
                "DELETE FROM report_signouts WHERE id = :id",
                {"id": rs_id},
                "report_signouts is append-only; DELETE is not permitted",
            )

            # --- qc_threshold_changes (a past acceptance limit must not be rewritten) ---
            async with sm() as s:
                qtc_id = (
                    await s.execute(
                        text(
                            "INSERT INTO qc_threshold_changes "
                            "(profile_key, metric_key, warn_value, reason) "
                            "VALUES ('p1-5-test', 'mean_depth', 10, 'marker') RETURNING id"
                        )
                    )
                ).scalar_one()
                await s.commit()
            await _assert_blocked(
                sm,
                "UPDATE qc_threshold_changes SET warn_value = 5 WHERE id = :id",
                {"id": qtc_id},
                "qc_threshold_changes is append-only; UPDATE is not permitted",
            )
            await _assert_blocked(
                sm,
                "DELETE FROM qc_threshold_changes WHERE id = :id",
                {"id": qtc_id},
                "qc_threshold_changes is append-only; DELETE is not permitted",
            )
        finally:
            await close_postgres_engine()

    asyncio.run(_run())


def test_append_only_fk_null_carveout_is_permitted_but_nothing_else() -> None:
    """The ON DELETE SET NULL cascade (account/family unlink) must still be allowed —
    but ONLY the FK-null; any other change on the same row stays blocked."""
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()

            async with sm() as s:
                user_id = (
                    await s.execute(text("SELECT id FROM users LIMIT 1"))
                ).scalar_one_or_none()
            if user_id is None:
                pytest.skip("no seeded user to exercise the FK-null carve-out")

            # A clinical_audit_events row linked to a real actor.
            async with sm() as s:
                cae_id = (
                    await s.execute(
                        text(
                            "INSERT INTO clinical_audit_events (actor, action, actor_id) "
                            "VALUES ('p1-5-test', 'test', :uid) RETURNING id"
                        ),
                        {"uid": user_id},
                    )
                ).scalar_one()
                await s.commit()

            # Carve-out: nulling the FK is permitted (mirrors the account-deletion cascade).
            async with sm() as s:
                await s.execute(
                    text("UPDATE clinical_audit_events SET actor_id = NULL WHERE id = :id"),
                    {"id": cae_id},
                )
                await s.commit()
            async with sm() as s:
                row = (
                    await s.execute(
                        text(
                            "SELECT actor_id, actor FROM clinical_audit_events WHERE id = :id"
                        ),
                        {"id": cae_id},
                    )
                ).mappings().one()
            assert row["actor_id"] is None
            assert row["actor"] == "p1-5-test"  # denormalised identity preserved

            # ...but any OTHER column change (even on the now-unlinked row) is still blocked.
            await _assert_blocked(
                sm,
                "UPDATE clinical_audit_events SET summary = 'tampered' WHERE id = :id",
                {"id": cae_id},
                "clinical_audit_events is append-only; UPDATE is not permitted",
            )

            # The QC acceptance-limit history has the same carve-out on changed_by.
            async with sm() as s:
                qtc_id = (
                    await s.execute(
                        text(
                            "INSERT INTO qc_threshold_changes (profile_key, metric_key, "
                            "warn_value, changed_by, changed_by_email, reason) VALUES "
                            "('p1-5-test', 'mean_depth', 10, :uid, 'p1-5@x.org', 'marker') "
                            "RETURNING id"
                        ),
                        {"uid": user_id},
                    )
                ).scalar_one()
                await s.commit()
            async with sm() as s:
                await s.execute(
                    text("UPDATE qc_threshold_changes SET changed_by = NULL WHERE id = :id"),
                    {"id": qtc_id},
                )
                await s.commit()
            async with sm() as s:
                row = (
                    await s.execute(
                        text(
                            "SELECT changed_by, changed_by_email FROM qc_threshold_changes "
                            "WHERE id = :id"
                        ),
                        {"id": qtc_id},
                    )
                ).mappings().one()
            assert row["changed_by"] is None
            assert row["changed_by_email"] == "p1-5@x.org"  # denormalised identity preserved
            # Re-attributing the edit, or changing the recorded limit, stays blocked.
            await _assert_blocked(
                sm,
                "UPDATE qc_threshold_changes SET changed_by = :uid WHERE id = :id",
                {"uid": user_id, "id": qtc_id},
                "qc_threshold_changes is append-only; UPDATE is not permitted",
            )
            await _assert_blocked(
                sm,
                "UPDATE qc_threshold_changes SET warn_value = 5 WHERE id = :id",
                {"id": qtc_id},
                "qc_threshold_changes is append-only; UPDATE is not permitted",
            )
        finally:
            await close_postgres_engine()

    asyncio.run(_run())
