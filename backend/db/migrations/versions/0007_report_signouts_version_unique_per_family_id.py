"""A family ID's sign-out versions are unique, across every family made under it (TRACE-13).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-20

A family deleted and made again under its ID signs on the sign-out chain of the family it
replaced, which is ordered by version. Its sign-outs were numbered per family row, so its
first took version 1 again, and the chain failed its verification as if it had been tampered
with. The sign-out now numbers on from the family ID's last version, and 04_traceability.sql
makes a family ID's version unique: ``uq_report_signouts_family_identifier_version`` on
``report_signouts (family_identifier, version)``, which also replaces the chain's plain index
``idx_report_signouts_chain``. ``UNIQUE (family_id, version)`` cannot do it, as the family's
deletion nulls ``family_id``.

This runs the baseline's own statements for it, split as the baselines are run: the check that
stops the migration where a family ID already holds one version twice (a family made again
under its ID and signed out in both, before TRACE-13; the message names no family), the unique
index, and the drop of the plain index. Each is idempotent: on a database that already has the
index they change nothing. No stored row changes; a database the check stops keeps everything
as it was, its ledger included.
"""

from collections.abc import Callable

from alembic import context, op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def _is_selected(statement: str) -> bool:
    """Whether ``statement`` of 04_traceability.sql is one of TRACE-13's three: the check, the
    unique index, or the drop of the index it replaces."""

    flat = " ".join(statement.split())
    return (
        (flat.startswith("DO $$") and "FROM report_signouts" in flat and "taken_twice" in flat)
        or flat.startswith("CREATE UNIQUE INDEX IF NOT EXISTS uq_report_signouts_family_identifier_version ")
        or flat == "DROP INDEX IF EXISTS idx_report_signouts_chain"
    )


# A baseline that no longer holds one of them stops the revision rather than leave part of the
# change out.
_EXPECTED = 3


def selected_statements(baseline_statements: Callable[[str], list[str]]) -> list[str]:
    """The baseline's statements this revision runs, in the order 04_traceability.sql has them."""

    found = [statement for statement in baseline_statements("04_traceability.sql") if _is_selected(statement)]
    if len(found) != _EXPECTED:
        raise RuntimeError(
            f"Revision 0007 expects {_EXPECTED} statements of 04_traceability.sql and found "
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
