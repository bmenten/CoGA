"""The columns no code reads go, and so does the families' metadata key nothing read (ENG-6).

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-28

The owner's decision of 2026-10-28 (ENG-6): the schema keeps no column that nothing reads.
01_access.sql and 03_assay.sql create the tables without three of them:

- ``auth_login_attempts.last_success_at``: never written or read; a successful sign-in deletes
  the scope's row;
- ``small_variant_reviews.compound_het_partner_variant_keys``: never written or read; a pair
  review names its partner by id (``compound_het_partner_variant_ids``);
- ``structural_variant_reviews.variant_key``: never written (#853), so the unique index on
  ``(family_id, variant_key)`` held no row either; Postgres drops it with its column. The
  sign-out read the column and froze it, always null, in each reported structural variant; it
  still freezes that null, so a signed record keeps its form.

``CREATE TABLE IF NOT EXISTS`` leaves an existing table as it is, so this drops them from a
database made before. Every row stays, with its other values; a new database never had them.

It also removes ``unresolved_roi`` from the families' ``metadata``: a package import wrote it
when it could not resolve the manifest's region of interest in the family's assembly, nothing
read it, and a later import that resolved the region did not clear it. The import no longer
writes it, and leaves the family's region of interest as it was, as before. The family's other
metadata stays. On a database without them, every statement changes nothing.
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

_STATEMENTS = (
    "ALTER TABLE auth_login_attempts DROP COLUMN IF EXISTS last_success_at",
    "ALTER TABLE small_variant_reviews DROP COLUMN IF EXISTS compound_het_partner_variant_keys",
    "ALTER TABLE structural_variant_reviews DROP COLUMN IF EXISTS variant_key",
    "UPDATE families SET metadata = metadata - 'unresolved_roi' WHERE metadata ? 'unresolved_roi'",
)


def upgrade() -> None:
    bind = op.get_bind()
    for statement in _STATEMENTS:
        bind.exec_driver_sql(statement)


def downgrade() -> None:
    raise NotImplementedError(
        "CoGA's schema revisions are forward-only. To go back, restore the database from the "
        "backup taken before the upgrade (docs/database.md, \"Schema Migrations\")."
    )
