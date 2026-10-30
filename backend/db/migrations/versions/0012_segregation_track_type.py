"""An embryo's segregation table can be stored as a track of its own.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-30

``sample_interval_track_sources_track_type_check`` in 03_assay.sql gained
``apcad_segregation``: the PGT pipeline's table of an embryo's sites where one parent is
heterozygous (nf-cmgg/copgtm), which the ``apcad`` dataset reads for the parental-origin view of
an aneuploid chromosome. ``CREATE TABLE IF NOT EXISTS`` does not bring a new value to an
existing table's check, so on a database created before it the import of such a table failed
on the old check. This gives the table the baseline's check. The new list holds the old one,
so the stored rows pass; on a database that already has it, the check is replaced by itself.
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

# As 03_assay.sql defines it, and as pg_get_constraintdef reads it back.
_TRACK_TYPE_CHECK = (
    "CHECK ((track_type = ANY (ARRAY['coverage'::text, 'apcad'::text, 'apcad_pcf'::text, "
    "'segments'::text, 'haplotype'::text, 'target_coverage'::text, 'apcad_segregation'::text])))"
)


def upgrade() -> None:
    # One statement: the table is never without its check, and is read once to validate it.
    op.get_bind().exec_driver_sql(
        "ALTER TABLE sample_interval_track_sources "
        "DROP CONSTRAINT IF EXISTS sample_interval_track_sources_track_type_check, "
        f"ADD CONSTRAINT sample_interval_track_sources_track_type_check {_TRACK_TYPE_CHECK}"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "CoGA's schema revisions are forward-only. To go back, restore the database from the "
        "backup taken before the upgrade (docs/database.md, \"Schema Migrations\")."
    )
