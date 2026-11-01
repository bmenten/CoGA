"""PGS Catalog imports and immutable metadata/scoring-readiness per model version."""
from alembic import context, op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    statements = context.config.attributes["baseline_statements"]
    for statement in statements("04_traceability.sql"):
        if (statement.startswith("ALTER TABLE pgs_model_versions ADD COLUMN")
                or statement.startswith("ALTER TABLE pgs_model_versions DROP CONSTRAINT IF EXISTS pgs_model_versions_prepared_check")
                or statement.startswith("CREATE TABLE IF NOT EXISTS pgs_catalog_imports")
                or statement.startswith("CREATE INDEX IF NOT EXISTS pgs_catalog_imports_queue")):
            op.get_bind().exec_driver_sql(statement)


def downgrade() -> None:
    raise NotImplementedError("Forward-only: restore the database from the backup taken before the upgrade.")
