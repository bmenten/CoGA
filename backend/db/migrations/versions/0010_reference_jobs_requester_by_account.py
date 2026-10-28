"""A gene-reference refresh's and a clinical CNV knowledgebase rebuild's requester are kept by the account's id (SEC-35).

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-28

Only an administrator asks for a gene-reference refresh or a knowledgebase rebuild, and the
job ran whoever had asked for it by then: an account deactivated, or no longer an
administrator, since it asked still had its refresh or rebuild run, as the owner decided on
2026-10-28 it must not. The jobs kept their requester only by the e-mail address it asked
under, which a Microsoft sign-in may change (SEC-32). 02_reference.sql keys them on the
account instead: ``gene_info_refresh_jobs.requested_by_id`` and
``clinical_cnv_kb_jobs.requested_by_id`` (nullable, ``ON DELETE SET NULL``), by which the
worker finds the account before it runs the job. ``requested_by`` stays, as the address
shown with the job (``startup-bootstrap`` for the refresh a start queues, no account's).

This runs the baseline's own statements for it: per table, the upgrade, a ``DO`` block that
runs only while the column is missing: the column, each job linked to the account its address
finds (to none when no account has it), and the foreign key. On a database that already has
the columns they change nothing. Each job is kept.
"""

from collections.abc import Callable

from alembic import context, op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

_UPGRADED = ("gene_info_refresh_jobs", "clinical_cnv_kb_jobs")


def _is_selected(statement: str) -> bool:
    """Whether ``statement`` of 02_reference.sql is one of SEC-35's: a job table's upgrade."""

    flat = " ".join(statement.split())
    return flat.startswith("DO $$") and any(
        f"WHERE attrelid = '{table}'::regclass AND attname = 'requested_by_id'" in flat for table in _UPGRADED
    )


def selected_statements(baseline_statements: Callable[[str], list[str]]) -> list[str]:
    """The baseline's statements this revision runs, in the order 02_reference.sql has them."""

    found = [statement for statement in baseline_statements("02_reference.sql") if _is_selected(statement)]
    # One upgrade per table: a baseline that no longer holds one of them stops the revision
    # rather than leave part of the change out.
    if len(found) != len(_UPGRADED):
        raise RuntimeError(
            f"Revision 0010 expects {len(_UPGRADED)} statements of 02_reference.sql and found "
            f"{len(found)}: the baseline changed under it."
        )
    return found


def upgrade() -> None:
    bind = op.get_bind()
    for statement in selected_statements(context.config.attributes["baseline_statements"]):
        bind.exec_driver_sql(statement)


def downgrade() -> None:
    raise NotImplementedError(
        "CoGA's schema revisions are forward-only. To go back, restore the database from the "
        "backup taken before the upgrade (docs/database.md, \"Schema Migrations\")."
    )
