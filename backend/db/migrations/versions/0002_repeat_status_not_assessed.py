"""A repeat call at a locus without thresholds can be stored as not_assessed (#826).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-19

``repeat_expansions_status_check`` gained ``review`` and ``premutation`` (#763) and then
``not_assessed`` (#826) in 03_assay.sql. ``CREATE TABLE IF NOT EXISTS`` leaves an existing
table as it is, so a database created before them kept the old check, and a TRGT import with
a call at a locus without thresholds failed on it, with the whole repeat dataset. This gives
the table the baseline's check. Every earlier list of statuses is part of this one, so the
stored rows pass it; on a database that already has it, the check is replaced by itself.
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# As 03_assay.sql defines it, and as pg_get_constraintdef reads it back.
_STATUS_CHECK = (
    "CHECK ((status = ANY (ARRAY['normal'::text, 'review'::text, 'not_assessed'::text, "
    "'intermediate'::text, 'premutation'::text, 'pathogenic'::text, 'unknown'::text])))"
)


def upgrade() -> None:
    # One statement: the table is never without the check, and is read once to validate it.
    op.get_bind().exec_driver_sql(
        "ALTER TABLE repeat_expansions "
        "DROP CONSTRAINT IF EXISTS repeat_expansions_status_check, "
        f"ADD CONSTRAINT repeat_expansions_status_check {_STATUS_CHECK}"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "CoGA's schema revisions are forward-only. To go back, restore the database from the "
        "backup taken before the upgrade (docs/database.md, \"Schema Migrations\")."
    )
