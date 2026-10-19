"""The five baseline files, backend/db/schema/postgres/01_access.sql to 05_grants.sql.

Revision ID: 0001
Revises:
Create Date: 2026-10-19

A database gets them here when its ledger is empty: a new one, and one the baselines created
before the ledger existed. They are idempotent, and they leave a table that exists as it is;
the revisions after this one bring such a table up to date. Until the release candidate the
migration step also re-applies them before every run, so that a change to a baseline reaches a
database past this revision too. The release candidate freezes them, and from then on this
revision is the only step that applies them (docs/database.md, "Schema Migrations").
"""

from alembic import context, op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The migration step's own function (core/postgres_migrations.py), handed over in the
    # Alembic config: the same statements, split the same way, as its pre-RC re-apply.
    apply_baselines = context.config.attributes["apply_baselines"]
    apply_baselines(op.get_bind())


def downgrade() -> None:
    raise NotImplementedError(
        "CoGA's schema revisions are forward-only. To go back, restore the database from the "
        "backup taken before the upgrade (docs/database.md, \"Schema Migrations\")."
    )
