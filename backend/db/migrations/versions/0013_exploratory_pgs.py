"""Immutable exploratory PGS models, full-WGS sources and results.

Revision ID: 0013
Revises: 0012
"""
from alembic import context, op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Select the same idempotent DDL and restricted-role REVOKE as the baseline. This also
    # reaches a database once the first release candidate freezes baseline replay.
    statements = context.config.attributes["baseline_statements"]
    for filename in ("04_traceability.sql", "05_grants.sql"):
        for statement in statements(filename):
            if "pgs_" in statement:
                op.get_bind().exec_driver_sql(statement)


def downgrade() -> None:
    raise NotImplementedError("Forward-only: restore the database from the backup taken before the upgrade.")
