"""Each request-audit row and UI event carries its event's own id, unique (SEC-17).

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-27

The request audit (``audit_log_events``) and the UI-event log (``ui_events``) are written by a
worker that writes a batch again after a failed attempt. A commit whose answer never reached the
worker looked like a failure, so the batch was written again and each of its rows stored twice.
SEC-17 gave both tables the column ``event_id`` (a UUID, unique): the id the backend gives each
event when it makes it, before the event is queued. Every write of the event carries it, and the
writers' INSERT does nothing for an id the table holds, so the event is stored once.

``CREATE TABLE IF NOT EXISTS`` does not bring the column to a database created before it, so
04_traceability.sql adds it with one upgrade statement per table, ``ALTER TABLE ... ADD COLUMN
IF NOT EXISTS event_id UUID UNIQUE``. This runs those two statements as the baseline holds them,
so a database ends with the column and its unique constraint whether or not the baselines are
re-applied first. Each row stored before it keeps no event id: both tables are append-only, and
Postgres's unique constraint lets any number of rows have none. On a database that already has
the column it does nothing.
"""

from collections.abc import Callable

from alembic import context, op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

_PREFIXES = (
    "ALTER TABLE audit_log_events ADD COLUMN IF NOT EXISTS event_id ",
    "ALTER TABLE ui_events ADD COLUMN IF NOT EXISTS event_id ",
)


def selected_statements(baseline_statements: Callable[[str], list[str]]) -> list[str]:
    """The baseline's statements this revision runs: 04_traceability.sql's upgrade of the two
    event logs, in the order the file has them."""

    found = [
        statement
        for statement in baseline_statements("04_traceability.sql")
        if " ".join(statement.split()).startswith(_PREFIXES)
    ]
    # One statement per table, the request audit's first.
    if len(found) != len(_PREFIXES) or not all(
        " ".join(statement.split()).startswith(prefix)
        for statement, prefix in zip(found, _PREFIXES)
    ):
        raise RuntimeError(
            f"Revision 0009 expects {len(_PREFIXES)} statements of 04_traceability.sql and found "
            f"{len(found)}: the baseline changed under it."
        )
    return found


def upgrade() -> None:
    bind = op.get_bind()
    for statement in selected_statements(context.config.attributes["baseline_statements"]):
        bind.exec_driver_sql(statement)


def downgrade() -> None:
    raise NotImplementedError(
        "CoGA's schema revisions are forward-only. To go back, restore the database from the "
        "backup taken before the upgrade (docs/database.md, \"Schema Migrations\")."
    )
