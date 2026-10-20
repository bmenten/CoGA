"""A relative of a PGT index and a panel's per-target coverage can be stored (#747, #749).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-19

Two checks in 03_assay.sql gained a value that ``CREATE TABLE IF NOT EXISTS`` did not bring
to an existing table: ``family_relationships_relationship_type_check`` gained ``relative``
(#747: the distant relative a PGT package links to its index) and
``sample_interval_track_sources_track_type_check`` gained ``target_coverage`` (#749: a NIPT
capture panel's coverage per target). On a database created before them, the PGT import of
such a relative and the import of a per-target coverage table failed on the old check. This
gives both tables the baseline's checks. Each new list holds the old one, so the stored rows
pass; on a database that already has them, each check is replaced by itself.
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

# As 03_assay.sql defines them, and as pg_get_constraintdef reads them back.
_RELATIONSHIP_TYPE_CHECK = (
    "CHECK ((relationship_type = ANY (ARRAY['parent_child'::text, 'couple'::text, "
    "'relative'::text])))"
)
_TRACK_TYPE_CHECK = (
    "CHECK ((track_type = ANY (ARRAY['coverage'::text, 'apcad'::text, 'apcad_pcf'::text, "
    "'segments'::text, 'haplotype'::text, 'target_coverage'::text])))"
)


def upgrade() -> None:
    bind = op.get_bind()
    # One statement per table: it is never without its check, and is read once to validate it.
    bind.exec_driver_sql(
        "ALTER TABLE family_relationships "
        "DROP CONSTRAINT IF EXISTS family_relationships_relationship_type_check, "
        f"ADD CONSTRAINT family_relationships_relationship_type_check {_RELATIONSHIP_TYPE_CHECK}"
    )
    bind.exec_driver_sql(
        "ALTER TABLE sample_interval_track_sources "
        "DROP CONSTRAINT IF EXISTS sample_interval_track_sources_track_type_check, "
        f"ADD CONSTRAINT sample_interval_track_sources_track_type_check {_TRACK_TYPE_CHECK}"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "CoGA's schema revisions are forward-only. To go back, restore the database from the "
        "backup taken before the upgrade (docs/database.md, \"Schema Migrations\")."
    )
