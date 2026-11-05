"""An import job can keep the manifest it validates and imports with.

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-05

``family_import_jobs`` in 03_assay.sql gained two columns and a check: ``manifest_text``, the
manifest CoGA keeps for the job, which its validation and import read instead of the package
folder's own (a folder CoGA cannot write, such as a share mounted read-only), and
``manifest_sha256``, its SHA-256, with ``family_import_jobs_manifest_check``: the job keeps both
or neither. ``CREATE TABLE IF NOT EXISTS`` brings none of it to a table that already exists, so
it is done here. Every job stored before read its folder's manifest and keeps neither. On a
database that already has the columns, nothing is changed but the check, replaced by itself.
"""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

# As 03_assay.sql defines it.
_MANIFEST_CHECK = "CHECK (((manifest_text IS NULL) = (manifest_sha256 IS NULL)))"


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        "ALTER TABLE family_import_jobs "
        "ADD COLUMN IF NOT EXISTS manifest_text text, "
        "ADD COLUMN IF NOT EXISTS manifest_sha256 text, "
        "DROP CONSTRAINT IF EXISTS family_import_jobs_manifest_check, "
        f"ADD CONSTRAINT family_import_jobs_manifest_check {_MANIFEST_CHECK}"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "CoGA's schema revisions are forward-only. To go back, restore the database from the "
        "backup taken before the upgrade (docs/database.md, \"Schema Migrations\")."
    )
