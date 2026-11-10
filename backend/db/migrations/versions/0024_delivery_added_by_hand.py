"""A delivery can be a folder a person added by hand.

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-10

``import_deliveries`` in 03_assay.sql gained ``origin`` (``watched``, the default, for a package
the import watcher found; ``manual`` for a folder a person added on the Package Import page,
whose package CoGA made from the folder) with ``import_deliveries_origin_check``. And the
uniqueness of a folder and its fingerprint holds among the watcher's deliveries only: the
constraint ``import_deliveries_folder_fingerprint_key`` became a unique index of that name with
``WHERE (origin = 'watched'::text)``, so a folder imported before can be added again by hand.

``CREATE TABLE IF NOT EXISTS`` changes neither on a table that already exists, and the
baseline's ``CREATE UNIQUE INDEX IF NOT EXISTS`` finds the constraint's own index of that name
there and leaves it. So this adds the column and its check, drops the constraint (and its index
with it) and creates the partial index, each statement a no-op where it is done already. Every
delivery stored before was the watcher's and keeps the default; no two of them shared a folder
and fingerprint, so the index holds them all.
"""

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

# As 03_assay.sql defines them, and as pg_get_constraintdef / pg_get_indexdef read them back.
_ORIGIN_CHECK = "CHECK ((origin = ANY (ARRAY['watched'::text, 'manual'::text])))"
_FOLDER_FINGERPRINT_INDEX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS import_deliveries_folder_fingerprint_key ON import_deliveries "
    "USING btree (folder_path, package_fingerprint) WHERE (origin = 'watched'::text)"
)


def upgrade() -> None:
    bind = op.get_bind()
    # One statement for the column and its check: the table is never without the check.
    bind.exec_driver_sql(
        "ALTER TABLE import_deliveries "
        "ADD COLUMN IF NOT EXISTS origin text DEFAULT 'watched'::text NOT NULL, "
        "DROP CONSTRAINT IF EXISTS import_deliveries_origin_check, "
        f"ADD CONSTRAINT import_deliveries_origin_check {_ORIGIN_CHECK}, "
        "DROP CONSTRAINT IF EXISTS import_deliveries_folder_fingerprint_key"
    )
    bind.exec_driver_sql(_FOLDER_FINGERPRINT_INDEX)


def downgrade() -> None:
    raise NotImplementedError(
        "CoGA's schema revisions are forward-only. To go back, restore the database from the "
        "backup taken before the upgrade (docs/database.md, \"Schema Migrations\")."
    )
