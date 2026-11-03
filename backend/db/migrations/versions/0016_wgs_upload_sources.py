"""Retained-upload WGS sources and explicit exploratory input policies."""
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE IF EXISTS pgs_sources ADD COLUMN IF NOT EXISTS raw_file_id uuid")
    op.execute("ALTER TABLE IF EXISTS pgs_sources ADD COLUMN IF NOT EXISTS qualification jsonb NOT NULL DEFAULT '{}'::jsonb")


def downgrade() -> None:
    raise NotImplementedError("Forward-only: restore the database from the backup.")
