"""The class labels the reviews and the filter presets hold are put in sentence case (ENG-6).

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-04

The owner's decision of 2026-10-28 (ENG-6): the classification tags and the VUS tiers are
written in sentence case. The code now writes "Likely pathogenic - class 4" where it wrote
"Likely Pathogenic - class 4", for an ACMG and a CNV classification alike, and names the VUS
tiers "VUS — hot", "VUS — warm" and "VUS — cold". A tag is stored by its key, so only the
classification text a review or a preset holds needs rewriting. This writes each class label
as the code writes it now, where a mutable row holds it in another case:

- a small variant review's classification, its compound-het pair's, and the classification in
  its ACMG scoring (``acmg``);
- a structural variant review's classification and the one in its CNV scoring (``cnv_acmg``);
- each classification a small or structural variant filter preset filters on, in the list a
  small variant preset stores and in the comma-separated text a structural variant preset
  stores.

Only the case changes: text that does not read as a class label, whatever its case, stays as it
is, and so does every other value of the row, its ``updated_at`` included, so a review dialog
open across the upgrade still saves. The append-only tables keep their text: the clinical audit
trail, the signed records and their hash chains, the request audit and the UI-event log. A
record signed before this still says "Likely Pathogenic - class 4"; the comparison with the
latest sign-out reads a class label whatever its case (``report_signout_service``), the filters
match one whatever its case, and a review save stores one sent in another case, by a page loaded
before the upgrade say, as it is now (``review_pg_utils._stored_classification``). On a database
without such text, every statement changes nothing.
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

# The class labels as acmg_points.CLASS_LABELS and cnv_acmg_points.CLASS_LABELS write them now,
# frozen here as this revision applies them.
CLASS_LABELS = (
    "Pathogenic - class 5",
    "Likely pathogenic - class 4",
    "VUS - class 3",
    "Likely benign - class 2",
    "Benign - class 1",
)
_LABELS = "(VALUES " + ", ".join(f"('{label}')" for label in CLASS_LABELS) + ") AS l(label)"


def _column(table: str, column: str) -> str:
    return (
        f"UPDATE {table} AS r SET {column} = l.label FROM {_LABELS} "
        f"WHERE lower(r.{column}) = lower(l.label) AND r.{column} <> l.label"
    )


def _scoring(table: str, column: str) -> str:
    return (
        f"UPDATE {table} AS r SET {column} = jsonb_set(r.{column}, '{{classification}}', to_jsonb(l.label)) "
        f"FROM {_LABELS} WHERE jsonb_typeof(r.{column}) = 'object' "
        f"AND lower(r.{column} ->> 'classification') = lower(l.label) "
        f"AND r.{column} ->> 'classification' <> l.label"
    )


def _preset_list(table: str) -> str:
    # A small variant preset stores its classifications as a JSON list (serializePresetFilters).
    return (
        f"UPDATE {table} AS p SET filters = jsonb_set(p.filters, '{{classification}}', ("
        f"SELECT jsonb_agg(coalesce(to_jsonb(l.label), e.value) ORDER BY e.position) "
        f"FROM jsonb_array_elements(p.filters -> 'classification') WITH ORDINALITY AS e(value, position) "
        f"LEFT JOIN {_LABELS} ON jsonb_typeof(e.value) = 'string' AND lower(e.value #>> '{{}}') = lower(l.label))) "
        f"WHERE jsonb_typeof(p.filters -> 'classification') = 'array' AND EXISTS ("
        f"SELECT 1 FROM jsonb_array_elements_text(p.filters -> 'classification') AS x(value) "
        f"JOIN {_LABELS} ON lower(x.value) = lower(l.label) AND x.value <> l.label)"
    )


def _preset_text(table: str) -> str:
    # A structural variant preset stores them as comma-separated text
    # (serializeStructuralPresetFilters), which the form reads as parseCommaSeparatedValues does:
    # split on commas, each part trimmed, empty parts dropped. Rewritten, it is joined as
    # joinFilterValues joins it.
    return (
        f"UPDATE {table} AS p SET filters = jsonb_set(p.filters, '{{classification}}', to_jsonb(("
        f"SELECT string_agg(coalesce(l.label, btrim(e.value)), ', ' ORDER BY e.position) "
        f"FROM unnest(string_to_array(p.filters ->> 'classification', ',')) WITH ORDINALITY AS e(value, position) "
        f"LEFT JOIN {_LABELS} ON lower(btrim(e.value)) = lower(l.label) "
        f"WHERE btrim(e.value) <> ''))) "
        f"WHERE jsonb_typeof(p.filters -> 'classification') = 'string' AND EXISTS ("
        f"SELECT 1 FROM unnest(string_to_array(p.filters ->> 'classification', ',')) AS x(value) "
        f"JOIN {_LABELS} ON lower(btrim(x.value)) = lower(l.label) AND btrim(x.value) <> l.label)"
    )


STATEMENTS = (
    _column("small_variant_reviews", "classification"),
    _column("small_variant_reviews", "compound_het_classification"),
    _scoring("small_variant_reviews", "acmg"),
    _column("structural_variant_reviews", "classification"),
    _scoring("structural_variant_reviews", "cnv_acmg"),
    _preset_list("small_variant_filter_presets"),
    _preset_text("small_variant_filter_presets"),
    _preset_list("structural_variant_filter_presets"),
    _preset_text("structural_variant_filter_presets"),
)


def upgrade() -> None:
    bind = op.get_bind()
    for statement in STATEMENTS:
        bind.exec_driver_sql(statement)


def downgrade() -> None:
    raise NotImplementedError(
        "CoGA's schema revisions are forward-only. To go back, restore the database from the "
        "backup taken before the upgrade (docs/database.md, \"Schema Migrations\")."
    )
