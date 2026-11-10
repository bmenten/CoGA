"""A project's deletion keeps its review tags, inactive and detached from it (SAFE-37).

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-10

``small_variant_tag_definitions`` in 03_assay.sql changed in two ways. Its foreign key to
``projects`` deletes nothing: ``ON DELETE CASCADE`` became ``ON DELETE SET NULL``, so the
deletion of a project no longer deletes the tags defined for it, whose keys the reviews, the
signed records and the clinical audit trail still hold. ``delete_project_record`` deactivates
those tags before it deletes the project. And its two identical checks
(``small_variant_tag_definitions_check`` and ``small_variant_tag_definitions_scope_project_check``)
became one, ``small_variant_tag_definitions_scope_project_check``, which also lets an inactive
project tag be without a project: a global tag has no project, an active project tag has one.
A detached tag stays a project tag, so no reader lists it as global, and its key stays taken.

``CREATE TABLE IF NOT EXISTS`` changes neither on a table that already exists, so this gives the
table the baseline's check and foreign key, in one statement. The new check holds every row the
old ones did, and every stored tag's project exists, so each row stays; on a database that
already has them, both are replaced by themselves.
"""

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

# As 03_assay.sql defines them, and as pg_get_constraintdef reads them back.
_SCOPE_PROJECT_CHECK = (
    "CHECK ((((scope = 'global'::text) AND (project_id IS NULL)) OR ((scope = 'project'::text) "
    "AND ((project_id IS NOT NULL) OR (NOT is_active)))))"
)
_PROJECT_FOREIGN_KEY = "FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL"


def upgrade() -> None:
    # One statement: the table is never without its check or its foreign key.
    op.get_bind().exec_driver_sql(
        "ALTER TABLE small_variant_tag_definitions "
        "DROP CONSTRAINT IF EXISTS small_variant_tag_definitions_check, "
        "DROP CONSTRAINT IF EXISTS small_variant_tag_definitions_scope_project_check, "
        f"ADD CONSTRAINT small_variant_tag_definitions_scope_project_check {_SCOPE_PROJECT_CHECK}, "
        "DROP CONSTRAINT IF EXISTS small_variant_tag_definitions_project_id_fkey, "
        f"ADD CONSTRAINT small_variant_tag_definitions_project_id_fkey {_PROJECT_FOREIGN_KEY}"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "CoGA's schema revisions are forward-only. To go back, restore the database from the "
        "backup taken before the upgrade (docs/database.md, \"Schema Migrations\")."
    )
