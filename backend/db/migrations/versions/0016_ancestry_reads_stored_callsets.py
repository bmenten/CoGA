"""The ancestry PCA reads the family's stored callsets; the worker queues its own runs.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-04

The exploratory ancestry PCA (outside the intended purpose; docs/ancestry-pca.md) no longer
reads a registered WGS file: it reads the family's small variants in ClickHouse at the
reference markers, and its worker queues a run of its own once a family's genotypes changed.
04_traceability.sql now defines, on ``ancestry_runs``, a ``source_id`` and a ``requested_by``
that may be NULL, a ``trigger`` (``user`` or ``automatic``, the latter without a requester)
and a ``storage_version``; on ``ancestry_results`` a ``source_id`` that may be NULL and a
``storage_version``. ``CREATE TABLE IF NOT EXISTS`` brings none of that to a table that
already exists, so it is done here. Every existing run was a user's, on a WGS file source,
and keeps its values; ``wgs_sources`` keeps its rows, which those runs name.
"""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

# As 04_traceability.sql defines them.
_TRIGGER_CHECK = "CHECK (trigger IN ('user','automatic'))"
_REQUESTER_CHECK = "CHECK (trigger = 'automatic' OR requested_by IS NOT NULL)"


def upgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql(
        "ALTER TABLE ancestry_runs "
        "ALTER COLUMN source_id DROP NOT NULL, "
        "ALTER COLUMN requested_by DROP NOT NULL, "
        "ADD COLUMN IF NOT EXISTS trigger text NOT NULL DEFAULT 'user', "
        "ADD COLUMN IF NOT EXISTS storage_version text, "
        "DROP CONSTRAINT IF EXISTS ancestry_runs_trigger_check, "
        f"ADD CONSTRAINT ancestry_runs_trigger_check {_TRIGGER_CHECK}, "
        "DROP CONSTRAINT IF EXISTS ancestry_runs_requester_check, "
        f"ADD CONSTRAINT ancestry_runs_requester_check {_REQUESTER_CHECK}"
    )
    # ancestry_results is append-only: no row is changed, a column is added and one relaxed.
    bind.exec_driver_sql(
        "ALTER TABLE ancestry_results "
        "ALTER COLUMN source_id DROP NOT NULL, "
        "ADD COLUMN IF NOT EXISTS storage_version text"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "CoGA's schema revisions are forward-only. To go back, restore the database from the "
        "backup taken before the upgrade (docs/database.md, \"Schema Migrations\")."
    )
