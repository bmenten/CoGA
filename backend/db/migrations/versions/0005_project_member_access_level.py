"""Each project membership has an access level, an analyst's unless it says reader (SEC-1).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-20

SEC-1 (#887) gave ``project_users`` the column ``access_level`` (``analyst`` or ``reader``,
``analyst`` by default, with ``project_users_access_level_check``). ``CREATE TABLE IF NOT
EXISTS`` does not bring it to a database created before it, so 01_access.sql adds it with its
one upgrade statement, ``ALTER TABLE project_users ADD COLUMN IF NOT EXISTS access_level``.
This runs that statement as the baseline holds it, so a database ends with the column and its
check whether or not the baselines are re-applied first. Each membership stored before it
becomes an analyst's, as every member could change the project's records until then. On a
database that already has the column it does nothing.
"""

from collections.abc import Callable

from alembic import context, op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

_PREFIX = "ALTER TABLE project_users ADD COLUMN IF NOT EXISTS access_level "


def selected_statements(baseline_statements: Callable[[str], list[str]]) -> list[str]:
    """The baseline's statement this revision runs: 01_access.sql's upgrade of project_users."""

    found = [
        statement
        for statement in baseline_statements("01_access.sql")
        if " ".join(statement.split()).startswith(_PREFIX)
    ]
    if len(found) != 1:
        raise RuntimeError(
            f"Revision 0005 expects 1 statement of 01_access.sql and found {len(found)}: "
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
