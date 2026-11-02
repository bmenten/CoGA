"""The family search's annotation filters read the family's own variants, not the cohort's.

The annotation tables (``variants/annotations``, ``annotation_index``, ``gene_index``) are
shared by every family on the assembly. The family search tests its annotation filters as
``(e.key, e.annotation_version, e.annotationSetHash) IN (SELECT … FROM <annotation table>
WHERE <filter>)``, and ClickHouse gathers what that subquery returns into a set before it reads
the family's rows. Without a bound the set held every matching variant of the whole cohort: a
frequency filter alone held almost every variant stored, 1.6 GiB for 8 M variants, and would
pass the 4 GB a query may hold at a few hundred genomes.

* Every annotation subquery of a family search keeps the variants the family carries
  (``_small_family_key_condition``): ``identity(<alias>.key) IN`` the keys of the family's
  rows, in its projects and within the search's locus, each as the search's own WHERE has it,
  so the set holds every row the search reads and the answer is the same, for ``IN`` and
  ``NOT IN`` alike. The key is tested through ``identity`` so that ClickHouse keeps no second
  copy of the set for the annotation tables' index.
* One search writes the clause once in text, whichever filters it has, so ClickHouse builds
  the family's key set once for all of them.
* The Variant Explorer, which reads every family at once, keeps no such bound.

That the filters keep the same variants in ClickHouse is shown by
integration/test_small_variant_filter_parity_clickhouse.py, which runs every filter field
through the family search's SQL.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

import pytest

from backend.app.services.clickhouse_variant_queries import (
    _small_panel_filter_condition,
    _small_query_filter_parts,
    _small_variant_where_clauses,
)
from backend.app.services.clickhouse_variant_records import PanelFilterConstraints
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.family_variant_filters import SmallVariantQueryFilters
from backend.app.services.variant_explorer_service import ExplorerScope

_CONTEXT = FamilyMetadataContext(
    family_uuid="keys-family",
    family_id="KEYS",
    project_ids=["keys-project-a", "keys-project-b"],
    sample_rows=[{"sample_id": "PROBAND"}],
    sample_uuid_to_name={},
    sample_name_to_uuid={"PROBAND": "keys-sample"},
    affected_sample_names=["PROBAND"],
    assembly_id="keys-assembly",
    assembly_name="GRCh38",
)
_ENTRIES = "coga.`GRCh38/SNV_INDEL/entries`"
_ANNOTATION_TABLES = {
    "a": "coga.`GRCh38/SNV_INDEL/variants/annotations`",
    "ai": "coga.`GRCh38/SNV_INDEL/variants/annotation_index`",
    "gi": "coga.`GRCh38/SNV_INDEL/variants/gene_index`",
}
# Every annotation filter of the family search: the gene (gene index), the rsID (annotation
# index), the transcript filters (annotations), the excluded ClinVar terms (annotations, NOT
# IN), with a panel's genes and excluded genes (gene index, the latter NOT IN) passed beside.
_EVERY_ANNOTATION_FILTER: dict[str, Any] = {
    "gene": "BRCA1",
    "rsid": "rs80357906",
    "impact": ["HIGH", "MODERATE"],
    "max_gnomad_af": 0.01,
    "exclude_clinvar": ["benign"],
}
_PANEL = PanelFilterConstraints(genes=("BRCA2",))
_BOUND_HEAD = re.compile(r"identity\((a|ai|gi)\.key\) IN \(SELECT fk\.key FROM (\S+) AS fk WHERE ")


def _bounds(where: str) -> list[tuple[str, str, str, int]]:
    """Each family-key bound in ``where``: the annotation alias it narrows, the table its keys
    are read from, the WHERE of those rows and the index just past its closing parenthesis.
    The rows' WHERE holds parentheses of its own (``%(…)s``, the overlap rule), so the bound
    ends at the parenthesis that balances the one its ``IN`` opens."""
    found = []
    for match in _BOUND_HEAD.finditer(where):
        depth = 1
        index = match.end()
        while depth:
            depth += {"(": 1, ")": -1}.get(where[index], 0)
            index += 1
        found.append((match.group(1), match.group(2), where[match.end() : index - 1], index))
    return found


def _search(filters: SmallVariantQueryFilters, context: FamilyMetadataContext = _CONTEXT) -> tuple[str, dict[str, Any]]:
    clauses, params, _detail = _small_query_filter_parts(
        context, filters, panel_constraints=_PANEL, exclude_gene_terms=("TP53",)
    )
    return " AND ".join(clauses), params


def _subqueries(where: str) -> list[str]:
    """The alias of each annotation subquery the search's WHERE holds, in order."""
    return [
        alias
        for table, alias in re.findall(r"FROM (coga\.`[^`]+`) AS (a|ai|gi) WHERE ", where)
        if _ANNOTATION_TABLES[alias] == table
    ]


def _locus_clauses(filters: SmallVariantQueryFilters) -> list[str]:
    """The search's own locus clauses, as the family's key rows must repeat them."""
    search_clauses, _params = _small_variant_where_clauses(_CONTEXT, filters)
    return [
        clause.replace("e.", "fk.")
        for clause in search_clauses
        if clause.startswith(("e.chrom", "e.pos", "(e.pos", "e.xpos"))
    ]


def test_every_annotation_subquery_reads_only_the_family_s_keys() -> None:
    where, params = _search(SmallVariantQueryFilters(page=1, page_size=100, **_EVERY_ANNOTATION_FILTER))
    subqueries = _subqueries(where)
    # The gene and the panel's genes (gene index), the excluded genes (gene index), the rsID
    # (annotation index), the transcript filters and the excluded ClinVar terms (annotations).
    assert sorted(subqueries) == ["a", "a", "ai", "gi", "gi", "gi"]
    bounds = _bounds(where)
    assert [alias for alias, _table, _rows, _end in bounds] == subqueries
    assert {table for _alias, table, _rows, _end in bounds} == {_ENTRIES}
    assert {rows for _alias, _table, rows, _end in bounds} == {
        "fk.family_guid = %(annotation_family_guid)s AND fk.sign = 1"
        " AND fk.project_guid IN %(annotation_project_ids)s"
    }
    assert params["annotation_family_guid"] == params["family_guid"] == "keys-family"
    assert params["annotation_project_ids"] == params["project_ids"] == ("keys-project-a", "keys-project-b")
    # The excluded ClinVar terms and genes are bounded too: for a row of the family the
    # narrowed set answers NOT IN as the whole one did.
    assert ") NOT IN (SELECT a.key, a.annotation_version, a.annotationSetHash FROM " in where
    assert "NOT (" in where


def test_each_bound_closes_its_own_subquery() -> None:
    # The family's keys narrow the annotation rows the subquery reads, inside it: the bound
    # stands right before the subquery's closing parenthesis, never on the search's own rows.
    where, _params = _search(SmallVariantQueryFilters(page=1, page_size=100, **_EVERY_ANNOTATION_FILTER))
    bounds = _bounds(where)
    assert bounds
    for alias, _table, rows, end in bounds:
        start = where.rfind("identity(", 0, end - len(rows))
        opening = where.rfind(f"FROM {_ANNOTATION_TABLES[alias]} AS {alias} WHERE ", 0, start)
        assert opening >= 0
        assert "(e." not in where[opening:start] and " e." not in where[opening:start]
        assert where[end] == ")"


@pytest.mark.parametrize(
    "locus",
    [
        {"chromosome": "17"},
        {"chromosome": "chr17", "start": 43_000_000, "end": 43_100_000},
        {"chromosome": "17", "start": 43_000_000, "end": 43_100_000, "overlap": True},
        {"chromosome": "MT", "start": 100},
        {"chromosome": "X", "end": 2_000_000},
        {"start": 100},
        {"end": 100, "overlap": True},
    ],
)
def test_the_family_s_keys_hold_every_row_the_search_reads_within_its_locus(locus: dict[str, Any]) -> None:
    # The key rows must hold every row the search reads, or a row would be dropped from an
    # IN or kept by a NOT IN it fails: they are narrowed by the family, its projects and the
    # search's own locus clauses, nothing else, and those as the search's WHERE has them.
    filters = SmallVariantQueryFilters(page=1, page_size=100, **_EVERY_ANNOTATION_FILTER, **locus)
    where, _params = _search(filters)
    locus_clauses = _locus_clauses(filters)
    assert locus_clauses or "chromosome" not in locus
    expected = " AND ".join(
        [
            "fk.family_guid = %(annotation_family_guid)s",
            "fk.sign = 1",
            "fk.project_guid IN %(annotation_project_ids)s",
            *locus_clauses,
        ]
    )
    bounds = _bounds(where)
    assert len(bounds) == len(_subqueries(where)) == 6
    # One text for every subquery of the search, the rsID's among them: one set to build.
    assert {rows for _alias, _table, rows, _end in bounds} == {expected}


def test_a_family_without_projects_reads_its_keys_in_every_project() -> None:
    context = replace(_CONTEXT, project_ids=[])
    where, params = _search(SmallVariantQueryFilters(page=1, page_size=100, **_EVERY_ANNOTATION_FILTER), context)
    assert {rows for _alias, _table, rows, _end in _bounds(where)} == {
        "fk.family_guid = %(annotation_family_guid)s AND fk.sign = 1"
    }
    assert "annotation_project_ids" not in params


def test_a_search_without_annotation_filters_reads_no_family_keys() -> None:
    clauses, params, _detail = _small_query_filter_parts(_CONTEXT, SmallVariantQueryFilters(page=1, page_size=100))
    assert not any("fk." in clause for clause in clauses)
    assert "annotation_family_guid" not in params


def test_the_variant_explorer_reads_the_annotations_of_every_family() -> None:
    # The explorer's panel condition is the family page's, for every family at once: it
    # keeps no family's bound.
    scope = ExplorerScope(assembly_id="a1", assembly_name="GRCh38", project_ids=["p1"])
    params: dict[str, Any] = {}
    condition = _small_panel_filter_condition(
        scope, SmallVariantQueryFilters(page=1, page_size=1), _PANEL, params=params
    )
    assert condition is not None
    assert f"FROM {_ANNOTATION_TABLES['gi']} AS gi WHERE " in condition
    assert "fk." not in condition
    assert "annotation_family_guid" not in params
