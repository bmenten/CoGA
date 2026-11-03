"""Fixed public ancestry PCA references, durable jobs and immutable projections."""
from alembic import context, op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    statements = context.config.attributes["baseline_statements"]
    for statement in statements("04_traceability.sql"):
        if (statement.startswith("CREATE TABLE IF NOT EXISTS wgs_sources")
                or statement.startswith("CREATE INDEX IF NOT EXISTS wgs_sources")
                or statement.startswith("CREATE OR REPLACE FUNCTION wgs_sources_")
                or statement.startswith("DROP TRIGGER IF EXISTS wgs_sources_")
                or statement.startswith("CREATE TRIGGER wgs_sources_")
                or statement.startswith("CREATE TABLE IF NOT EXISTS ancestry_")
                or statement.startswith("CREATE INDEX IF NOT EXISTS ancestry_")
                or statement.startswith("CREATE OR REPLACE FUNCTION ancestry_")
                or statement.startswith("DROP TRIGGER IF EXISTS ancestry_")
                or statement.startswith("CREATE TRIGGER ancestry_")):
            op.get_bind().exec_driver_sql(statement)


def downgrade() -> None:
    raise NotImplementedError("Forward-only: restore the database from the backup taken before the upgrade.")
