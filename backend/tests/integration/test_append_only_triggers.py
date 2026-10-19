"""Integration tests that FIRE the append-only triggers (real Postgres, smoke job).

These prove the database-level immutability controls on six append-only tables
— ``audit_log_events`` (the HTTP access log), ``clinical_audit_events`` (the clinical
action log), ``report_signouts`` (frozen signed reports), ``qc_threshold_changes``
(the history of QC acceptance limits), ``family_structure_versions`` (the pedigree's
history) and ``ui_events`` (the UI-event log): a direct UPDATE of a non-carve-out column
and any DELETE are rejected by the ``BEFORE UPDATE/DELETE`` triggers, while the FK ``ON
DELETE SET NULL`` carve-out (the user/actor unlink that keeps account deletion working, and
for the structure versions the family unlink) is permitted — and nothing else. This is the
verifying evidence for RTM REQ-TRACE-008 and, for the QC history, REQ-QC-007. The seventh,
``integrity_anchors``, is not fired here: a marker anchor would break the anchor-chain
verification that ``test_integrity_anchor_integration.py`` runs on the same database. Its
REVOKE is asserted in ``test_app_role_privileges.py``.

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

            # --- family_structure_versions (a recorded pedigree must not be rewritten) ---
            async with sm() as s:
                fsv_id = (
                    await s.execute(
                        text(
                            "INSERT INTO family_structure_versions "
                            "(family_identifier, version, structure_hash, snapshot) "
                            "VALUES ('p1-5-test', 1, 'deadbeef', '{}'::jsonb) RETURNING id"
                        )
                    )
                ).scalar_one()
                await s.commit()
            await _assert_blocked(
                sm,
                "UPDATE family_structure_versions SET structure_hash = 'tampered' WHERE id = :id",
                {"id": fsv_id},
                "family_structure_versions is append-only; UPDATE is not permitted",
            )
            await _assert_blocked(
                sm,
                "DELETE FROM family_structure_versions WHERE id = :id",
                {"id": fsv_id},
                "family_structure_versions is append-only; DELETE is not permitted",
            )

            # --- ui_events (the UI-event log is not rewritten or pruned) ---
            async with sm() as s:
                uie_id = (
                    await s.execute(
                        text(
                            "INSERT INTO ui_events (event_type, label) "
                            "VALUES ('click', 'p1-5-trigger-test') RETURNING id"
                        )
                    )
                ).scalar_one()
                await s.commit()
            await _assert_blocked(
                sm,
                "UPDATE ui_events SET label = 'tampered' WHERE id = :id",
                {"id": uie_id},
                "ui_events is append-only; UPDATE is not permitted",
            )
            await _assert_blocked(
                sm,
                "DELETE FROM ui_events WHERE id = :id",
                {"id": uie_id},
                "ui_events is append-only; DELETE is not permitted",
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
                    # No user seeded (another test may have deleted its own): make one, so
                    # the carve-outs are exercised whatever ran before.
                    user_id = (
                        await s.execute(
                            text(
                                "INSERT INTO users (username, hashed_password, role, email) "
                                "VALUES ('p1-5-carveout', :h, 'viewer', 'p1-5-carveout@x.org') "
                                "RETURNING id"
                            ),
                            {"h": "unusable"},
                        )
                    ).scalar_one()
                    await s.commit()

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

            # A structure version may lose its creator (account removed) and its family row
            # (family deleted), each nulled by the cascade; the family_identifier, the chain
            # and the recorded pedigree stay, and nothing else may change.
            async with sm() as s:
                family_uuid = (
                    await s.execute(
                        text(
                            "INSERT INTO families (family_id) VALUES ('p1-5-fsv-' || "
                            "gen_random_uuid()::text) RETURNING id"
                        )
                    )
                ).scalar_one()
                fsv_id = (
                    await s.execute(
                        text(
                            "INSERT INTO family_structure_versions (family_id, "
                            "family_identifier, version, structure_hash, snapshot, created_by) "
                            "VALUES (:fid, 'p1-5-test', 1, 'deadbeef', '{}'::jsonb, :uid) "
                            "RETURNING id"
                        ),
                        {"fid": family_uuid, "uid": user_id},
                    )
                ).scalar_one()
                await s.commit()
            async with sm() as s:
                await s.execute(
                    text("UPDATE family_structure_versions SET created_by = NULL WHERE id = :id"),
                    {"id": fsv_id},
                )
                await s.execute(
                    text("UPDATE family_structure_versions SET family_id = NULL WHERE id = :id"),
                    {"id": fsv_id},
                )
                await s.commit()
            async with sm() as s:
                row = (
                    await s.execute(
                        text(
                            "SELECT created_by, family_id, family_identifier, structure_hash "
                            "FROM family_structure_versions WHERE id = :id"
                        ),
                        {"id": fsv_id},
                    )
                ).mappings().one()
            assert (row["created_by"], row["family_id"]) == (None, None)
            assert (row["family_identifier"], row["structure_hash"]) == ("p1-5-test", "deadbeef")
            # Re-linking the version to a family or a user, or changing what it records, stays
            # blocked.
            await _assert_blocked(
                sm,
                "UPDATE family_structure_versions SET family_id = :fid WHERE id = :id",
                {"fid": family_uuid, "id": fsv_id},
                "family_structure_versions is append-only; UPDATE is not permitted",
            )
            await _assert_blocked(
                sm,
                "UPDATE family_structure_versions SET created_by = :uid WHERE id = :id",
                {"uid": user_id, "id": fsv_id},
                "family_structure_versions is append-only; UPDATE is not permitted",
            )
            await _assert_blocked(
                sm,
                "UPDATE family_structure_versions SET family_identifier = 'other' WHERE id = :id",
                {"id": fsv_id},
                "family_structure_versions is append-only; UPDATE is not permitted",
            )

            # A UI event keeps its user_email and user_role when its user_id is unlinked.
            async with sm() as s:
                uie_id = (
                    await s.execute(
                        text(
                            "INSERT INTO ui_events (event_type, label, user_id, user_email) "
                            "VALUES ('click', 'p1-5-carveout', :uid, 'p1-5@x.org') RETURNING id"
                        ),
                        {"uid": user_id},
                    )
                ).scalar_one()
                await s.commit()
            async with sm() as s:
                await s.execute(
                    text("UPDATE ui_events SET user_id = NULL WHERE id = :id"), {"id": uie_id}
                )
                await s.commit()
            async with sm() as s:
                row = (
                    await s.execute(
                        text("SELECT user_id, user_email FROM ui_events WHERE id = :id"),
                        {"id": uie_id},
                    )
                ).mappings().one()
            assert row["user_id"] is None
            assert row["user_email"] == "p1-5@x.org"
            await _assert_blocked(
                sm,
                "UPDATE ui_events SET user_id = :uid WHERE id = :id",
                {"uid": user_id, "id": uie_id},
                "ui_events is append-only; UPDATE is not permitted",
            )
            await _assert_blocked(
                sm,
                "UPDATE ui_events SET user_email = 'someone-else@x.org' WHERE id = :id",
                {"id": uie_id},
                "ui_events is append-only; UPDATE is not permitted",
            )
        finally:
            await close_postgres_engine()

    asyncio.run(_run())
