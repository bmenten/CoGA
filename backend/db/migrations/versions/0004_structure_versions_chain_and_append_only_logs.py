"""The pedigree's structure versions are append-only and hash-chained, and the UI-event log is append-only (SEC-7).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-20

SEC-7 changed two tables that a database created before it already has:

- ``family_structure_versions`` gained ``family_identifier``, ``row_hash`` and ``prev_hash``
  (the per-family hash chain) and the chain's index; ``family_id`` became nullable, and the
  family's deletion now nulls it (``ON DELETE SET NULL``) instead of deleting the family's
  versions;
- ``family_structure_versions`` and ``ui_events`` gained their ``*_block_mutation`` trigger,
  and the runtime role ``coga_app`` lost UPDATE, DELETE and TRUNCATE on them.

This runs the baselines' own statements for exactly these changes (03_assay.sql,
04_traceability.sql, 05_grants.sql), split as the baselines are run, so the database ends at
the baselines' definitions. Each is idempotent: on a database that already has the change it
leaves it as it is. Every stored row stays; a version written before the chain keeps no
``family_identifier`` and no hashes, and is not part of the chain.
"""

from collections.abc import Callable

from alembic import context, op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def _is_selected(filename: str, statement: str) -> bool:
    """Whether ``statement`` of a baseline file is one of SEC-7's changes to an existing table."""

    if filename == "03_assay.sql":
        return (
            statement.startswith("ALTER TABLE family_structure_versions ")
            or statement.startswith("CREATE INDEX IF NOT EXISTS idx_family_structure_versions_chain ")
            or (statement.startswith("DO $$") and "family_structure_versions_family_id_fkey" in statement)
        )
    if filename == "04_traceability.sql":
        return any(
            statement.startswith(prefix)
            for prefix in (
                "CREATE OR REPLACE FUNCTION ui_events_block_mutation()",
                "DROP TRIGGER IF EXISTS ui_events_immutable ",
                "CREATE TRIGGER ui_events_immutable",
                "CREATE OR REPLACE FUNCTION family_structure_versions_block_mutation()",
                "DROP TRIGGER IF EXISTS family_structure_versions_immutable ",
                "CREATE TRIGGER family_structure_versions_immutable",
            )
        )
    if filename == "05_grants.sql":
        return statement.startswith("REVOKE UPDATE, DELETE, TRUNCATE ON") and "family_structure_versions" in statement
    return False


# How many statements each file gives: a baseline that no longer holds one of them stops the
# revision rather than leave part of the change out.
_EXPECTED = {"03_assay.sql": 6, "04_traceability.sql": 6, "05_grants.sql": 1}


def selected_statements(baseline_statements: Callable[[str], list[str]]) -> list[str]:
    """The baselines' statements this revision runs, in file order."""

    selected: list[str] = []
    for filename, expected in _EXPECTED.items():
        found = [statement for statement in baseline_statements(filename) if _is_selected(filename, statement)]
        if len(found) != expected:
            raise RuntimeError(
                f"Revision 0004 expects {expected} statements of {filename} and found {len(found)}: "
                "the baseline changed under it."
            )
        selected.extend(found)
    return selected


def upgrade() -> None:
    bind = op.get_bind()
    for statement in selected_statements(context.config.attributes["baseline_statements"]):
        bind.exec_driver_sql(statement)


def downgrade() -> None:
    raise NotImplementedError(
        "CoGA's schema revisions are forward-only. To go back, restore the database from the "
        "backup taken before the upgrade (docs/database.md, \"Schema Migrations\")."
    )
