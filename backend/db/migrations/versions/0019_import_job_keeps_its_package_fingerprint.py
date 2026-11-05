"""An import job keeps the fingerprint of the package folder it was queued for.

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-05

``family_import_jobs`` in 03_assay.sql gained a column and a check: ``package_fingerprint``, the
fingerprint of the local package folder when the job was queued (``family_package_fingerprint``:
a SHA-256 over each file's path, size and modification time, and the kept manifest's SHA-256),
with ``family_import_jobs_package_fingerprint_check``: none, or 64 lowercase hexadecimal digits.
The job imports nothing when the folder's fingerprint differs as it starts. ``CREATE TABLE IF NOT
EXISTS`` brings neither to a table that already exists, so they are added here. Every job stored
before was queued without one and keeps none. On a database that already has the column, nothing
is changed but the check, replaced by itself.
"""

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

# As 03_assay.sql defines it.
_PACKAGE_FINGERPRINT_CHECK = "CHECK (((package_fingerprint IS NULL) OR (package_fingerprint ~ '^[0-9a-f]{64}$'::text)))"


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        "ALTER TABLE family_import_jobs "
        "ADD COLUMN IF NOT EXISTS package_fingerprint text, "
        "DROP CONSTRAINT IF EXISTS family_import_jobs_package_fingerprint_check, "
        f"ADD CONSTRAINT family_import_jobs_package_fingerprint_check {_PACKAGE_FINGERPRINT_CHECK}"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "CoGA's schema revisions are forward-only. To go back, restore the database from the "
        "backup taken before the upgrade (docs/database.md, \"Schema Migrations\")."
    )
