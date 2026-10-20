"""Each account can carry the Microsoft object ID it signs in with (SEC-4).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-20

SEC-4 (#895) gave ``users`` the column ``azure_object_id`` (a UUID, unique, empty for a
password account), by which a Microsoft sign-in finds its account. ``CREATE TABLE IF NOT
EXISTS`` does not bring it to a database created before it, so 01_access.sql adds it with its
upgrade statement, ``ALTER TABLE users ADD COLUMN IF NOT EXISTS azure_object_id``. This runs
that statement as the baseline holds it, so a database ends with the column and its unique
constraint whether or not the baselines are re-applied first. Each account stored before it
keeps no object ID: a password account, until a Microsoft sign-in makes its own account. On a
database that already has the column it does nothing.
"""

from collections.abc import Callable

from alembic import context, op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

_PREFIX = "ALTER TABLE users ADD COLUMN IF NOT EXISTS azure_object_id "


def selected_statements(baseline_statements: Callable[[str], list[str]]) -> list[str]:
    """The baseline's statement this revision runs: 01_access.sql's upgrade of users."""

    found = [
        statement
        for statement in baseline_statements("01_access.sql")
        if " ".join(statement.split()).startswith(_PREFIX)
    ]
    if len(found) != 1:
        raise RuntimeError(
            f"Revision 0006 expects 1 statement of 01_access.sql and found {len(found)}: "
            "the baseline changed under it."
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
