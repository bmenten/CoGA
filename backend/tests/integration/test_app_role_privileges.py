"""The restricted runtime role `coga_app` cannot bypass the append-only controls.

Verifying evidence for the privilege separation that closes the hash chains' owner-bypass gap (#262). As
`coga_app` (assumed via ``SET LOCAL ROLE`` — auto-resets at transaction end, so no pooled
connection keeps the role) the test asserts, for every append-only table:
- it MAY ``INSERT`` + ``SELECT`` them (the app's real access), and
- it MAY still delete a user whose ``ON DELETE SET NULL`` cascade nulls an audit FK (so
  account/family deletion keeps working without UPDATE on the append-only table),
- it MAY still delete a family whose ``ON DELETE SET NULL`` cascade nulls ``family_id`` on
  the clinical audit trail, the sign-outs and the structure versions, which keep the
  family's identifier, but
- it may NOT ``UPDATE`` / ``DELETE`` / ``TRUNCATE`` them nor ``DISABLE`` their triggers —
  i.e. it cannot rewrite/remove audit rows, signed reports, integrity anchors, the QC
  cut-off history, the pedigree's structure versions, the UI-event log or the hash-chain
  columns, and cannot re-chain an interior edit.

The append-only tables are the ones guarded by a ``*_block_mutation`` trigger. The test
reads that set from the catalogue and requires it to equal ``_APPEND_ONLY``, so a new
append-only table cannot ship without being exercised here, and so without its REVOKE in
``05_grants.sql`` (``qc_threshold_changes`` once shipped with its trigger but no REVOKE).

The schema-migration ledger (``alembic_version``) is not append-only, since the migration step
moves its one row on, but it is the owner's alone: coga_app may read it and nothing more.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it. The
test connects as the migration owner/superuser, which can ``SET ROLE`` to the NOLOGIN
``coga_app`` to exercise exactly its privilege set.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.integration

_APPEND_ONLY = (
    "audit_log_events",
    "clinical_audit_events",
    "report_signouts",
    "integrity_anchors",
    "qc_threshold_changes",
    "family_structure_versions",
    "ui_events",
)

# A syntactically-valid UPDATE per table (a real column); the privilege check fires before
# the row scan, so a no-op SET still surfaces "permission denied" for coga_app.
_UPDATE_SQL = {
    "audit_log_events": "UPDATE audit_log_events SET status_code = 200",
    "clinical_audit_events": "UPDATE clinical_audit_events SET summary = summary",
    "report_signouts": "UPDATE report_signouts SET signed_out_by = signed_out_by",
    "integrity_anchors": "UPDATE integrity_anchors SET key_id = key_id",
    "qc_threshold_changes": "UPDATE qc_threshold_changes SET reason = reason",
    "family_structure_versions": "UPDATE family_structure_versions SET version = version",
    "ui_events": "UPDATE ui_events SET label = label",
}

# Every table with an append-only guard: each follows the ``<table>_block_mutation()``
# trigger-function naming in 04_traceability.sql.
_TRIGGER_GUARDED_SQL = """
    SELECT DISTINCT c.relname
    FROM pg_trigger t
    JOIN pg_class c ON c.oid = t.tgrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_proc p ON p.oid = t.tgfoid
    WHERE NOT t.tgisinternal AND n.nspname = 'public' AND p.proname ~ '_block_mutation$'
"""

# coga_app's privileges on one table, read from the catalogue without running a mutation
# (TRUNCATE in particular would bypass the row triggers if it were ever granted).
_PRIVILEGES_SQL = """
    SELECT has_table_privilege('coga_app', CAST(:t AS text), 'SELECT'),
           has_table_privilege('coga_app', CAST(:t AS text), 'INSERT'),
           has_table_privilege('coga_app', CAST(:t AS text), 'UPDATE'),
           has_table_privilege('coga_app', CAST(:t AS text), 'DELETE'),
           has_table_privilege('coga_app', CAST(:t AS text), 'TRUNCATE')
"""


async def _denied(sm, sql: str, expect: str) -> None:
    """A statement coga_app must NOT be allowed to run; pin the exact refusal reason
    (``permission denied`` from the REVOKE vs ``must be owner`` from DISABLE TRIGGER) so a
    dropped REVOKE can't be masked by the ownership check still failing."""
    with pytest.raises(DBAPIError) as exc_info:
        async with sm() as session:
            await session.execute(text("SET LOCAL ROLE coga_app"))
            await session.execute(text(sql))
            await session.commit()
    assert expect in str(exc_info.value).lower(), str(exc_info.value)


def test_coga_app_role_is_locked_out_of_append_only_mutations() -> None:
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()

            # Every trigger-guarded table is listed here, and coga_app holds exactly SELECT +
            # INSERT on each: the REVOKE in 05_grants.sql covers them all.
            async with sm() as s:
                guarded = {row[0] for row in (await s.execute(text(_TRIGGER_GUARDED_SQL))).all()}
                assert guarded == set(_APPEND_ONLY), guarded ^ set(_APPEND_ONLY)
                for table in _APPEND_ONLY:
                    privileges = (await s.execute(text(_PRIVILEGES_SQL), {"t": table})).one()
                    assert tuple(privileges) == (True, True, False, False, False), (table, privileges)

            # Positive: coga_app may INSERT then read its own durable write back.
            marker = f"p1-3-grant-{uuid4()}"
            async with sm() as s:
                await s.execute(text("SET LOCAL ROLE coga_app"))
                await s.execute(
                    text(
                        "INSERT INTO clinical_audit_events (actor, action, summary) "
                        "VALUES ('p1-3', 'test', :m)"
                    ),
                    {"m": marker},
                )
                await s.commit()
            async with sm() as s:
                await s.execute(text("SET LOCAL ROLE coga_app"))
                found = (
                    await s.execute(
                        text("SELECT 1 FROM clinical_audit_events WHERE summary = :m"),
                        {"m": marker},
                    )
                ).first()
                assert found is not None  # coga_app's committed INSERT is readable by coga_app

            # Negative: no UPDATE / DELETE (REVOKE → "permission denied") and no DISABLE
            # TRIGGER (non-owner → "must be owner") on any append-only table.
            for table in _APPEND_ONLY:
                await _denied(sm, _UPDATE_SQL[table], "permission denied")
                await _denied(sm, f"DELETE FROM {table}", "permission denied")
                await _denied(sm, f"ALTER TABLE {table} DISABLE TRIGGER USER", "must be owner")

            # The ON DELETE SET NULL carve-out still works for coga_app on every append-only
            # table with a user FK (integrity_anchors has none): deleting a user nulls each
            # table's user FK without coga_app holding UPDATE on them, so account/family
            # deletion keeps functioning.
            label = f"p1-3-{uuid4()}"
            async with sm() as s:  # set up as the owner: one row per table with a user FK
                fam = (
                    await s.execute(
                        text("INSERT INTO families (family_id) VALUES (:f) RETURNING id::text"),
                        {"f": label},
                    )
                ).scalar_one()
                uid = (
                    await s.execute(
                        text(
                            "INSERT INTO users (username, hashed_password, role, email) "
                            "VALUES (:u, 'x', 'viewer', :e) RETURNING id::text"
                        ),
                        {"u": f"p1-3-{uuid4()}", "e": f"p1-3-{uuid4()}@x.org"},
                    )
                ).scalar_one()
                await s.execute(
                    text(
                        "INSERT INTO clinical_audit_events (family_id, actor_id, actor, "
                        "action, summary) VALUES (CAST(:f AS uuid), CAST(:u AS uuid), "
                        "'x', 'test', 'cascade')"
                    ),
                    {"f": fam, "u": uid},
                )
                await s.execute(
                    text(
                        "INSERT INTO audit_log_events (method, path, status_code, user_id) "
                        "VALUES ('GET', :p, 200, CAST(:u AS uuid))"
                    ),
                    {"p": f"/p1-3-cascade-{label}", "u": uid},
                )
                await s.execute(
                    text(
                        "INSERT INTO report_signouts (family_id, family_identifier, version, "
                        "signed_out_by, signed_out_by_id, content_hash, snapshot) VALUES "
                        "(CAST(:f AS uuid), :fl, 1, 'x', CAST(:u AS uuid), 'deadbeef', '{}'::jsonb)"
                    ),
                    {"f": fam, "fl": label, "u": uid},
                )
                await s.execute(
                    text(
                        "INSERT INTO qc_threshold_changes (profile_key, metric_key, changed_by, "
                        "changed_by_email, warn_value, reason) VALUES "
                        "(:fl, 'mean_depth', CAST(:u AS uuid), 'x@x.org', 10, 'cascade')"
                    ),
                    {"fl": label, "u": uid},
                )
                await s.execute(
                    text(
                        "INSERT INTO family_structure_versions (family_id, family_identifier, "
                        "version, structure_hash, snapshot, created_by) VALUES "
                        "(CAST(:f AS uuid), :fl, 1, 'deadbeef', '{}'::jsonb, CAST(:u AS uuid))"
                    ),
                    {"f": fam, "fl": label, "u": uid},
                )
                await s.execute(
                    text(
                        "INSERT INTO ui_events (event_type, label, user_id, user_email) "
                        "VALUES ('click', :fl, CAST(:u AS uuid), 'x@x.org')"
                    ),
                    {"fl": label, "u": uid},
                )
                await s.commit()
            async with sm() as s:  # as coga_app: the delete + 6-way cascade must succeed
                await s.execute(text("SET LOCAL ROLE coga_app"))
                await s.execute(
                    text("DELETE FROM users WHERE id = CAST(:u AS uuid)"), {"u": uid}
                )
                await s.commit()
            async with sm() as s:  # every audit row survived, with its user FK nulled
                cae = (
                    await s.execute(
                        text("SELECT actor_id FROM clinical_audit_events WHERE family_id = CAST(:f AS uuid)"),
                        {"f": fam},
                    )
                ).first()
                ale = (
                    await s.execute(
                        text("SELECT user_id FROM audit_log_events WHERE path = :p"),
                        {"p": f"/p1-3-cascade-{label}"},
                    )
                ).first()
                rso = (
                    await s.execute(
                        text("SELECT signed_out_by_id FROM report_signouts WHERE family_identifier = :fl"),
                        {"fl": label},
                    )
                ).first()
                qtc = (
                    await s.execute(
                        text(
                            "SELECT changed_by, changed_by_email, warn_value "
                            "FROM qc_threshold_changes WHERE profile_key = :fl"
                        ),
                        {"fl": label},
                    )
                ).first()
                fsv = (
                    await s.execute(
                        text(
                            "SELECT created_by, family_id::text FROM family_structure_versions "
                            "WHERE family_identifier = :fl"
                        ),
                        {"fl": label},
                    )
                ).first()
                uie = (
                    await s.execute(
                        text("SELECT user_id, user_email FROM ui_events WHERE label = :fl"),
                        {"fl": label},
                    )
                ).first()
                assert cae is not None and cae[0] is None, cae
                assert ale is not None and ale[0] is None, ale
                assert rso is not None and rso[0] is None, rso
                # Only the FK is nulled; the denormalised actor and the recorded value stay.
                assert qtc is not None and tuple(qtc) == (None, "x@x.org", 10), qtc
                assert fsv is not None and tuple(fsv) == (None, fam), fsv
                assert uie is not None and tuple(uie) == (None, "x@x.org"), uie

            # Deleting the family works for coga_app too: its ON DELETE SET NULL cascade nulls
            # family_id on the clinical audit trail, the sign-outs and the structure versions
            # without coga_app holding UPDATE on them, and each row keeps the family's
            # identifier (the clinical audit row's family_identifier was never set here).
            async with sm() as s:
                await s.execute(text("SET LOCAL ROLE coga_app"))
                await s.execute(
                    text("DELETE FROM families WHERE id = CAST(:f AS uuid)"), {"f": fam}
                )
                await s.commit()
            async with sm() as s:
                unlinked = {}
                for table in ("report_signouts", "family_structure_versions"):
                    unlinked[table] = (
                        await s.execute(
                            text(
                                f"SELECT family_id IS NULL FROM {table} "
                                "WHERE family_identifier = :fl"
                            ),
                            {"fl": label},
                        )
                    ).scalar_one()
                remaining = (
                    await s.execute(
                        text("SELECT count(*) FROM clinical_audit_events WHERE family_id = CAST(:f AS uuid)"),
                        {"f": fam},
                    )
                ).scalar_one()
                assert unlinked == {"report_signouts": True, "family_structure_versions": True}, unlinked
                assert remaining == 0, remaining
        finally:
            await close_postgres_engine()

    asyncio.run(_run())


def test_coga_app_can_read_the_schema_ledger_but_not_write_it() -> None:
    """The ledger (``alembic_version``) records which schema revision ran; only the migration
    step writes it, as the owner. coga_app may read it, and every write is refused by the
    REVOKE in 05_grants.sql (the broad GRANT and the default privileges would allow it)."""

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
                privileges = (await s.execute(text(_PRIVILEGES_SQL), {"t": "alembic_version"})).one()
                assert tuple(privileges) == (True, False, False, False, False), privileges
            async with sm() as s:
                await s.execute(text("SET LOCAL ROLE coga_app"))
                ledger = (await s.execute(text("SELECT version_num FROM alembic_version"))).all()
                assert len(ledger) == 1, ledger
            for sql in (
                "INSERT INTO alembic_version (version_num) VALUES ('9999')",
                "UPDATE alembic_version SET version_num = version_num",
                "DELETE FROM alembic_version",
                "TRUNCATE alembic_version",
            ):
                await _denied(sm, sql, "permission denied")
        finally:
            await close_postgres_engine()

    asyncio.run(_run())
