"""Guarded unused-model deletion and audited removal of incomplete score rows."""
from alembic import context, op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    statements = context.config.attributes["baseline_statements"]
    prefixes = (
        "CREATE OR REPLACE FUNCTION pgs_model_versions_block_mutation",
        "CREATE OR REPLACE FUNCTION pgs_runs_pin_models", "DROP TRIGGER IF EXISTS pgs_runs_pin_models",
        "CREATE TRIGGER pgs_runs_pin_models", "CREATE OR REPLACE FUNCTION pgs_model_has_family_use",
        "CREATE OR REPLACE FUNCTION delete_unused_pgs_model", "CREATE TABLE IF NOT EXISTS pgs_result_dismissals",
        "CREATE OR REPLACE FUNCTION pgs_result_dismissals_", "DROP TRIGGER IF EXISTS pgs_result_dismissals_",
        "CREATE TRIGGER pgs_result_dismissals_",
    )
    for statement in statements("04_traceability.sql"):
        if statement.startswith(prefixes):
            op.get_bind().exec_driver_sql(statement)
    for statement in statements("05_grants.sql"):
        if "pgs_result_dismissals" in statement or "FUNCTION delete_unused_pgs_model" in statement:
            op.get_bind().exec_driver_sql(statement)


def downgrade() -> None:
    raise NotImplementedError("Forward-only: restore the database from the backup taken before the upgrade.")
