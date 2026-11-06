"""A logout ends every session of its account on the server.

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-06

``users`` in 01_access.sql gained ``sessions_ended_at``, when the account last signed out:
``get_current_user`` refuses every token issued to the account before then, so a token stops
working at the logout instead of at its expiry. ``CREATE TABLE IF NOT EXISTS`` does not add it
to a table that already exists, so it is added here, empty: no account stored before has signed
out this way. On a database that already has the column, nothing changes.
"""

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS sessions_ended_at timestamptz"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "CoGA's schema revisions are forward-only. To go back, restore the database from the "
        "backup taken before the upgrade (docs/database.md, \"Schema Migrations\")."
    )
