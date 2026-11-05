"""One PanelApp panel can be imported once per choice of confidence levels.

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-05

``gene_panels`` in 02_reference.sql gained ``external_selection``, the part of the external
panel a panel holds: for a PanelApp panel, the confidence levels it was imported with, lowest
first ("3", "2,3", "1,2,3"). Its unique index ``idx_gene_panels_source_external_selection``
(source, external id and selection, NULL equal to NULL) replaces
``idx_gene_panels_source_external`` (source and external id), which allowed one panel per
PanelApp panel: an import with other confidence levels made a new version of that panel instead
of a panel of its own. ``CREATE TABLE IF NOT EXISTS`` brings neither the column nor the index to
a table that already exists, so this adds the column as the baseline's upgrade statement does,
gives each PanelApp panel stored before the levels its last import took (as its
``source_metadata`` records them under ``import_options``; a panel whose metadata records none
keeps none), and swaps the index. Every row stays. On a database that already has the column
and the index, it changes nothing but a panel without a selection whose metadata records one.
"""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

# As 02_reference.sql holds them.
_ADD_COLUMN = "ALTER TABLE gene_panels ADD COLUMN IF NOT EXISTS external_selection text"
_UNIQUE_INDEX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_gene_panels_source_external_selection ON gene_panels "
    "USING btree (source, external_id, external_selection) NULLS NOT DISTINCT "
    "WHERE (external_id IS NOT NULL)"
)

# The levels the panel's last import took, each once, lowest first, as the import writes them.
_SELECTION_FROM_METADATA = """
UPDATE gene_panels
SET external_selection = (
    SELECT string_agg(level, ',' ORDER BY level)
    FROM (
        SELECT DISTINCT level
        FROM jsonb_array_elements_text(source_metadata -> 'import_options' -> 'confidence_levels') AS levels(level)
    ) AS distinct_levels
)
WHERE source = 'panelapp'
  AND external_selection IS NULL
  AND jsonb_typeof(source_metadata -> 'import_options' -> 'confidence_levels') = 'array'
  AND jsonb_array_length(source_metadata -> 'import_options' -> 'confidence_levels') > 0
"""


def upgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql(_ADD_COLUMN)
    bind.exec_driver_sql(_SELECTION_FROM_METADATA)
    bind.exec_driver_sql("DROP INDEX IF EXISTS idx_gene_panels_source_external")
    bind.exec_driver_sql(_UNIQUE_INDEX)


def downgrade() -> None:
    raise NotImplementedError(
        "CoGA's schema revisions are forward-only. To go back, restore the database from the "
        "backup taken before the upgrade (docs/database.md, \"Schema Migrations\")."
    )
