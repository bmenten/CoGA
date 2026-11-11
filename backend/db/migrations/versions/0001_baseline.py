"""The five baseline files, backend/db/schema/postgres/01_access.sql to 05_grants.sql.

Revision ID: 0001
Revises:
Create Date: 2026-10-19

It makes an empty database: the migration step runs it only on a database with an empty
ledger and no table. It applies the baselines and records their digest in schema_baselines,
which every later start compares with its own (docs/database.md, "Schema Migrations"). Until
the release candidate a schema change is an edit of a baseline, and this is the only revision;
the release candidate freezes the baselines, and from then on each schema change is a revision
of its own after this one.
"""

from alembic import context, op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The migration step's own functions (core/postgres_migrations.py), handed over in the
    # Alembic config.
    connection = op.get_bind()
    context.config.attributes["apply_baselines"](connection)
    context.config.attributes["record_baseline_digest"](connection)


def downgrade() -> None:
    raise NotImplementedError(
        "CoGA's schema revisions are forward-only. To go back, restore the database from the "
        "backup taken before the upgrade (docs/database.md, \"Schema Migrations\")."
    )
