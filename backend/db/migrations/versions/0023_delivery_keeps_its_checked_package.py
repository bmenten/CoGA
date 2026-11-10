"""A delivery keeps the package it checked and what reading its folder found.

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-10

``import_deliveries`` in 03_assay.sql gained two columns: ``package``, the package as the import
watcher checked it (the pipeline's package file, with what it leaves out proposed by Discover),
and ``package_check``, what reading the folder found (the file checks, the files whose checksum
was verified, what was proposed). A correction made in CoGA (``import_delivery_revisions``, a new
table the baseline creates) and a check again after a change in CoGA reuse them, rather than read
every file again. ``CREATE TABLE IF NOT EXISTS`` brings neither column to a table that already
exists, so they are added here. A delivery stored before has neither, and is checked again from
its folder as before.
"""

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        "ALTER TABLE import_deliveries "
        "ADD COLUMN IF NOT EXISTS package jsonb, "
        "ADD COLUMN IF NOT EXISTS package_check jsonb"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "CoGA's schema revisions are forward-only. To go back, restore the database from the "
        "backup taken before the upgrade (docs/database.md, \"Schema Migrations\")."
    )
