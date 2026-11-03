"""Labelled partial exploratory PGS sums while preserving historical results."""
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE IF EXISTS pgs_results DROP CONSTRAINT IF EXISTS pgs_results_status_check")
    op.execute("ALTER TABLE IF EXISTS pgs_results DROP CONSTRAINT IF EXISTS pgs_results_check")
    op.execute("ALTER TABLE IF EXISTS pgs_results DROP CONSTRAINT IF EXISTS pgs_results_score_status_check")
    op.execute("ALTER TABLE IF EXISTS pgs_results ADD CONSTRAINT pgs_results_status_check CHECK (status IN ('complete', 'partial', 'incomplete'))")
    op.execute("ALTER TABLE IF EXISTS pgs_results ADD CONSTRAINT pgs_results_score_status_check CHECK ((status IN ('complete', 'partial')) = (raw_score IS NOT NULL))")


def downgrade() -> None:
    raise NotImplementedError("Forward-only: restore the database from the backup taken before the upgrade.")
