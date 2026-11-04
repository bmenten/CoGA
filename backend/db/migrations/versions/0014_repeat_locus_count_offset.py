"""QA's record per repeat catalogue locus holds a count correction.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-04

``repeat_locus_count_units`` in 02_reference.sql gained ``count_offset``: the repeat units QA adds
to TRGT's count at the locus before it is classified (CLIN-57, the owner's decision of
2026-11-04), 0 for none, at most 20 either way. ``CREATE TABLE IF NOT EXISTS`` does not bring a
new column to an existing table, so a database created before it gets the column here, with the
baseline's default and check. Every record stored before gets 0: no correction, as it was read
then. On a database that already has the column, the column is kept and the check replaced by
itself.
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

# As 02_reference.sql defines it, and as pg_get_constraintdef reads it back.
_COUNT_OFFSET_CHECK = "CHECK (((count_offset >= '-20'::integer) AND (count_offset <= 20)))"


def upgrade() -> None:
    # One statement: the column never exists without its check, and the table is read once.
    op.get_bind().exec_driver_sql(
        "ALTER TABLE repeat_locus_count_units "
        "ADD COLUMN IF NOT EXISTS count_offset integer DEFAULT 0 NOT NULL, "
        "DROP CONSTRAINT IF EXISTS repeat_locus_count_units_count_offset_check, "
        f"ADD CONSTRAINT repeat_locus_count_units_count_offset_check {_COUNT_OFFSET_CHECK}"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "CoGA's schema revisions are forward-only. To go back, restore the database from the "
        "backup taken before the upgrade (docs/database.md, \"Schema Migrations\")."
    )
