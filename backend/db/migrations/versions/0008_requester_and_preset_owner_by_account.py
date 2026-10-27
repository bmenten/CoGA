"""An import job's requester and a filter preset's owner are kept by the account's id (SEC-32).

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-27

A Microsoft sign-in follows the token's e-mail address and user name (SEC-4), and the import
job's requester was found by the address the job was requested under, a user's filter presets
by their user name. A changed address made the import fail, or run as the account that had the
address by then; a changed user name took the presets out of their owner's lists, and gave them
to an account that took the name. 03_assay.sql keys them on the account instead:
``family_import_jobs.requested_by_id`` (nullable, ``ON DELETE SET NULL``) and
``small_variant_filter_presets.owner_id`` and ``structural_variant_filter_presets.owner_id``
(required, ``ON DELETE CASCADE``), each preset table's unique index on the account in place of
the one on the user name. ``requested_by`` and ``owner`` stay, as the address and the user name
shown with the record.

This runs the baseline's own statements for it, split as the baselines are run: per table, the
upgrade (a ``DO`` block that runs only while the column is missing: the column, each row linked
to the account its address or user name finds, a preset no account's user name finds removed,
the column required for a preset, the foreign key, the old unique index dropped), and each
preset table's new unique index. On a database that already has the columns they change
nothing. Each job is kept, linked to no account when none has its address.
"""

from collections.abc import Callable

from alembic import context, op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

_UPGRADED = {
    "family_import_jobs": "requested_by_id",
    "small_variant_filter_presets": "owner_id",
    "structural_variant_filter_presets": "owner_id",
}


def _is_selected(statement: str) -> bool:
    """Whether ``statement`` of 03_assay.sql is one of SEC-32's: a table's upgrade, or a preset
    table's unique index."""

    flat = " ".join(statement.split())
    if flat.startswith("DO $$"):
        return any(
            f"WHERE attrelid = '{table}'::regclass AND attname = '{column}'" in flat
            for table, column in _UPGRADED.items()
        )
    return flat.startswith(
        (
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_small_variant_filter_presets_unique ",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_structural_variant_filter_presets_unique ",
        )
    )


# A baseline that no longer holds one of them stops the revision rather than leave part of the
# change out.
_EXPECTED = 5


def selected_statements(baseline_statements: Callable[[str], list[str]]) -> list[str]:
    """The baseline's statements this revision runs, in the order 03_assay.sql has them."""

    found = [statement for statement in baseline_statements("03_assay.sql") if _is_selected(statement)]
    if len(found) != _EXPECTED:
        raise RuntimeError(
            f"Revision 0008 expects {_EXPECTED} statements of 03_assay.sql and found "
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
