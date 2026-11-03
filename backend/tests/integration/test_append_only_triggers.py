"""Integration tests that FIRE the append-only triggers (real Postgres, smoke job).

These prove the database-level immutability controls on six append-only tables (and on the
three of the exploratory ancestry PCA, outside the intended purpose: ``wgs_sources``,
``ancestry_reference_panels`` and ``ancestry_results``) —
``audit_log_events`` (the HTTP access log), ``clinical_audit_events`` (the clinical action
log), ``report_signouts`` (frozen signed reports), ``qc_threshold_changes`` (the history of
QC acceptance limits), ``family_structure_versions`` (the pedigree's history) and
``ui_events`` (the UI-event log): a direct UPDATE of a non-carve-out column and any DELETE
are rejected by the ``BEFORE UPDATE/DELETE`` triggers, while the FK ``ON DELETE SET NULL``
carve-out (the user/actor unlink that keeps account deletion working, and for the structure
versions the family unlink) is permitted — and nothing else. This is the verifying evidence
for RTM REQ-TRACE-008 and, for the QC history, REQ-QC-007. The seventh,
``integrity_anchors``, is not fired here: a marker anchor would break the anchor-chain
verification that ``test_integrity_anchor_integration.py`` runs on the same database; that
test fires it on a database of its own. Its REVOKE is asserted in
``test_app_role_privileges.py``. The set of trigger-guarded tables is read from the
catalogue and must be the six fired here and ``integrity_anchors``, so a new append-only
table cannot ship with a trigger no test fires.

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

from backend.tests.integration._datastores import append_only_tables, run_closing_stores

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


# A marker WGS source, and a fresh reference checksum (the panels' key, 64 hex digits).
_WGS_SOURCE_MARKER = (
    "(family_uuid, family_identifier, project_id, package_path, relative_vcf, sha256, storage_version, "
    "storage_fingerprint, samples, absent_record_is_hom_ref, file_size, file_mtime_ns) VALUES "
    "(gen_random_uuid(), 'p1-5-test', gen_random_uuid(), '/p1-5-test', 'marker.vcf', 'deadbeef', '0:0', "
    "'marker', '[]'::jsonb, false, 0, 0)"
)
_MARKER_SHA256 = "md5(random()::text) || md5(random()::text)"

# Per append-only table: a marker row's INSERT (returning its id), and an UPDATE of a value it
# records, by its id. integrity_anchors is not fired here: a marker anchor would break the
# anchor-chain verification that test_integrity_anchor_integration.py runs on the same database
# (that test fires it, on a database of its own).
_FIRED = {
    "audit_log_events": (
        "INSERT INTO audit_log_events (method, path, status_code) "
        "VALUES ('GET', '/p1-5-trigger-test', 200) RETURNING id",
        "UPDATE audit_log_events SET status_code = 500 WHERE id = :id",
    ),
    "clinical_audit_events": (
        "INSERT INTO clinical_audit_events (actor, action, summary) "
        "VALUES ('p1-5-test', 'test', 'marker') RETURNING id",
        "UPDATE clinical_audit_events SET summary = 'tampered' WHERE id = :id",
    ),
    # A signed report must never change.
    "report_signouts": (
        "INSERT INTO report_signouts (version, signed_out_by, content_hash, snapshot) "
        "VALUES (1, 'p1-5-test', 'deadbeef', '{}'::jsonb) RETURNING id",
        "UPDATE report_signouts SET content_hash = 'tampered' WHERE id = :id",
    ),
    # A past acceptance limit must not be rewritten.
    "qc_threshold_changes": (
        "INSERT INTO qc_threshold_changes (profile_key, metric_key, warn_value, reason) "
        "VALUES ('p1-5-test', 'mean_depth', 10, 'marker') RETURNING id",
        "UPDATE qc_threshold_changes SET warn_value = 5 WHERE id = :id",
    ),
    # A recorded pedigree must not be rewritten.
    "family_structure_versions": (
        "INSERT INTO family_structure_versions (family_identifier, version, structure_hash, snapshot) "
        "VALUES ('p1-5-test', 1, 'deadbeef', '{}'::jsonb) RETURNING id",
        "UPDATE family_structure_versions SET structure_hash = 'tampered' WHERE id = :id",
    ),
    # The UI-event log is not rewritten or pruned.
    "ui_events": (
        "INSERT INTO ui_events (event_type, label) VALUES ('click', 'p1-5-trigger-test') RETURNING id",
        "UPDATE ui_events SET label = 'tampered' WHERE id = :id",
    ),
    # The exploratory ancestry PCA: what a projection read, the reference it used and what it
    # found are not rewritten.
    "wgs_sources": (
        f"INSERT INTO wgs_sources {_WGS_SOURCE_MARKER} RETURNING id",
        "UPDATE wgs_sources SET sha256 = 'tampered' WHERE id = :id",
    ),
    "ancestry_reference_panels": (
        f"INSERT INTO ancestry_reference_panels (sha256, snapshot) VALUES ({_MARKER_SHA256}, '{{}}'::jsonb) "
        "RETURNING sha256",
        "UPDATE ancestry_reference_panels SET snapshot = '[]'::jsonb WHERE sha256 = :id",
    ),
    "ancestry_results": (
        f"WITH s AS (INSERT INTO wgs_sources {_WGS_SOURCE_MARKER} RETURNING id), "
        f"p AS (INSERT INTO ancestry_reference_panels (sha256, snapshot) VALUES ({_MARKER_SHA256}, '{{}}'::jsonb) "
        "RETURNING sha256), "
        "r AS (INSERT INTO ancestry_runs (family_uuid, family_identifier, project_id, source_id, reference_sha256, "
        "requested_by) SELECT gen_random_uuid(), 'p1-5-test', gen_random_uuid(), s.id, p.sha256, gen_random_uuid() "
        "FROM s, p RETURNING id, source_id, reference_sha256) "
        "INSERT INTO ancestry_results (run_id, source_id, reference_sha256, samples, calculation_version, assumptions) "
        "SELECT id, source_id, reference_sha256, '[]'::jsonb, 'marker', '{}'::jsonb FROM r RETURNING run_id",
        "UPDATE ancestry_results SET calculation_version = 'tampered' WHERE run_id = :id",
    ),
}
# The column a marker row is found by, where it is not ``id``.
_KEY = {"ancestry_reference_panels": "sha256", "ancestry_results": "run_id"}
_NOT_FIRED = {"integrity_anchors"}


@pytest.fixture(scope="module")
def schema() -> None:
    """The tables and triggers exist, whatever ran before (idempotent)."""
    from backend.app.core.postgres import init_postgres_schema

    run_closing_stores(init_postgres_schema)


def test_every_append_only_table_but_the_anchors_is_fired_here(schema) -> None:
    """A new append-only table cannot ship with a trigger that is never fired: its marker row
    and UPDATE belong in ``_FIRED`` (its REVOKE in ``test_app_role_privileges.py``)."""
    from backend.app.core.postgres import get_postgres_sessionmaker

    async def _guarded() -> set[str]:
        async with get_postgres_sessionmaker()() as s:
            return await append_only_tables(s)

    guarded = run_closing_stores(_guarded)
    assert guarded == set(_FIRED) | _NOT_FIRED, guarded ^ (set(_FIRED) | _NOT_FIRED)


@pytest.mark.parametrize("table", list(_FIRED))
def test_append_only_triggers_block_update_and_delete(schema, table: str) -> None:
    from backend.app.core.postgres import get_postgres_sessionmaker

    insert, update = _FIRED[table]
    key = _KEY.get(table, "id")

    async def _run() -> None:
        sm = get_postgres_sessionmaker()
        async with sm() as s:
            row_id = (await s.execute(text(insert))).scalar_one()
            await s.commit()
        await _assert_blocked(sm, update, {"id": row_id}, f"{table} is append-only; UPDATE is not permitted")
        await _assert_blocked(
            sm, f"DELETE FROM {table} WHERE {key} = :id", {"id": row_id}, f"{table} is append-only; DELETE is not permitted"
        )
        # Both refused: the row is there as it was written.
        async with sm() as s:
            assert (await s.execute(text(f"SELECT count(*) FROM {table} WHERE {key} = :id"), {"id": row_id})).scalar_one() == 1

    run_closing_stores(_run)


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
