"""The family search's annotation filters cost what the family costs, not what the cohort costs.

The annotation tables (``variants/annotations``, ``annotation_index``, ``gene_index``) are
shared by every family on the assembly. A filter read from them as ``(e.key,
e.annotationSetHash) IN (SELECT … FROM <annotation table> WHERE <filter>)`` reads the table's
rows of the whole cohort, and gathers what matches into a set before it reads the family's
rows.

* The per-transcript filters (impact, consequence, frequencies, scores, Canonical, MANE and LoF
  only, and what the ClinVar P/LP override leaves of them) and the salient impact are read on
  the row's own transcripts (``e.transcripts``, written with the row):
  ``arrayExists(a -> (<filter>), e.transcripts)``. The filter text is the one the shared table
  is read with, and every column it reads is one the transcripts hold, under the same name.
  ClinVar is no transcript's: it is each allele's in CoGA's release, read on the row's allele
  keys against the release's partition, which holds no family's variants.
* With a transcript or HGVS text, which the row's transcripts do not hold, the per-transcript
  filters read the shared table, as do the rsID filter (the annotation index) and a panel's
  genes (the gene index). Each such
  subquery keeps the variants the family carries (``_small_family_key_condition``): the keys
  of the family's rows, in its projects and within the search's locus, each as the search's
  own WHERE has it, so the set holds every row the search reads and the answer is the same,
  for ``IN`` and ``NOT IN`` alike. The key is tested through ``identity`` so that ClickHouse
  keeps no second copy of the set for the annotation tables' index. One search writes the
  clause once in text, so ClickHouse builds the family's key set once for all of them.
* The genes a user typed (the gene filter, the excluded genes) are read from the gene index by
  its sort key, a few granules per gene: they go without that bound, which would cost more than
  they do. A panel's genes, thousands of them, keep it: without it their set grows with the
  cohort.
* The Variant Explorer, which reads every family at once, reads the shared tables and keeps
  no family's bound.
* A subquery matches a row to the annotation rows of its key and annotation set, under any
  version (``_OWN_ANNOTATION``): matched on the version too, each set would be two to three
  times slower to build.

That the filters keep the same variants in ClickHouse, read on the row's transcripts as on the
shared table, is shown by integration/test_small_variant_own_transcripts_clickhouse.py and
integration/test_small_variant_filter_parity_clickhouse.py.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date
from typing import Any

import pytest

from backend.app.services.clickhouse_variant_queries import (
    _SALIENT_IMPACT,
    _small_annotation_filter_condition,
    _small_panel_filter_condition,
    _small_query_filter_parts,
    _small_variant_where_clauses,
)
from backend.app.services.clickhouse_variant_records import PanelFilterConstraints
from backend.app.services.clickhouse_variant_rows import ENTRY_TRANSCRIPT_FIELDS
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
_CLINVAR = "coga.`GRCh38/SNV_INDEL/clinvar`"
_RELEASE = date(2026, 11, 22)
_ANNOTATION_TABLES = {
    "a": "coga.`GRCh38/SNV_INDEL/variants/annotations`",
    "ai": "coga.`GRCh38/SNV_INDEL/variants/annotation_index`",
    "gi": "coga.`GRCh38/SNV_INDEL/variants/gene_index`",
}
# Every annotation filter of the family search: the gene (gene index), the rsID (annotation
# index), the transcript filters, the excluded ClinVar statuses (NOT, in CoGA's release), with a
# panel's genes and excluded genes (gene index, the latter NOT IN) passed beside.
_EVERY_ANNOTATION_FILTER: dict[str, Any] = {
    "gene": "BRCA1",
    "rsid": "rs80357906",
    "impact": ["HIGH", "MODERATE"],
    "max_gnomad_af": 0.01,
    "exclude_clinvar": ["benign"],
    "clinvar_coga_release": _RELEASE,
}
# Every per-transcript filter but the text ones, each set.
_EVERY_TRANSCRIPT_FILTER: dict[str, Any] = {
    "impact": ["HIGH"],
    "effect": ["missense_variant"],
    "clinvar": ["pathogenic"],
    "clinvar_pathogenic_override": True,
    "canonical_only": True,
    "mane_only": True,
    "lof_only": True,
    "max_gnomad_af": 0.01,
    "max_gnomad_exomes_af": 0.01,
    "max_gnomad_genomes_af": 0.01,
    "max_gnomad_popmax_af": 0.01,
    "max_topmed_af": 0.01,
    "max_gnomad_ac": 10,
    "max_gnomad_hom_count": 1,
    "max_gnomad_hemi_count": 1,
    "min_cadd": 20.0,
    "min_revel": 0.5,
    "min_spliceai": 0.2,
    "sift": "deleterious",
    "polyphen": "probably_damaging",
    "exclude_clinvar": ["benign"],
    "salient_only": True,
    "clinvar_coga_release": _RELEASE,
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


def _filters(**fields: Any) -> SmallVariantQueryFilters:
    return SmallVariantQueryFilters(page=1, page_size=100, **fields)


def _subqueries(where: str) -> list[str]:
    """The alias of each annotation subquery the search's WHERE holds, in order."""
    return [
        alias
        for table, alias in re.findall(r"FROM (coga\.`[^`]+`) AS (a|ai|gi) WHERE ", where)
        if _ANNOTATION_TABLES[alias] == table
    ]


def _own_transcripts(where: str) -> list[str]:
    """Each condition the search reads on the row's own transcripts, ``NOT`` before a negated
    one: the text of ``arrayExists(a -> (…), e.transcripts)``."""
    found = []
    for match in re.finditer(r"(NOT )?arrayExists\(a -> \(", where):
        depth = 1
        index = match.end()
        while depth:
            depth += {"(": 1, ")": -1}.get(where[index], 0)
            index += 1
        assert where[index:].startswith(", e.transcripts)")
        found.append((match.group(1) or "") + where[match.end() : index - 1])
    return found


def _locus_clauses(filters: SmallVariantQueryFilters) -> list[str]:
    """The search's own locus clauses, as the family's key rows must repeat them."""
    search_clauses, _params = _small_variant_where_clauses(_CONTEXT, filters)
    return [
        clause.replace("e.", "fk.")
        for clause in search_clauses
        if clause.startswith(("e.chrom", "e.pos", "(e.pos", "e.xpos"))
    ]


def test_the_per_transcript_filters_read_the_row_s_own_transcripts() -> None:
    filters = _filters(**_EVERY_TRANSCRIPT_FILTER)
    where, params = _search(filters)
    # The same filter text the shared table is read with, on the row's own transcripts: one
    # transcript meeting every per-transcript filter, or, for an allele CoGA's release holds
    # (likely) pathogenic, one meeting those the ClinVar P/LP override does not lift; one of a
    # salient impact. ClinVar is read on the row's allele keys in the release, never on a
    # transcript.
    expected_params: dict[str, Any] = {}
    detail = _small_annotation_filter_condition(filters, params=expected_params)
    lifted = _small_annotation_filter_condition(filters, params=expected_params, lifted=True)
    assert _own_transcripts(where) == [detail, lifted, _SALIENT_IMPACT]
    assert {name: params[name] for name in expected_params} == expected_params
    assert params["salient_impacts"] == ("high", "moderate", "medium")
    assert "clinvar_terms" not in where
    assert where.count(f"SELECT allele_key FROM {_CLINVAR} WHERE release = ") == 5
    assert {
        params[f"{prefix}_release"]
        for prefix in ("clinvar_override", "clinvar_status", "clinvar_excluded", "clinvar_diamond", "clinvar_benign")
    } == {_RELEASE}
    # No subquery reads the shared transcript table: the gene index is read for the panel's
    # genes, for the family's keys, and the excluded genes alone.
    assert _ANNOTATION_TABLES["a"] not in where
    assert _subqueries(where) == ["gi", "gi"]
    assert [alias for alias, _table, _rows, _end in _bounds(where)] == ["gi"]


def test_the_row_s_transcripts_hold_every_column_a_per_transcript_filter_reads() -> None:
    # A filter reading a column the transcripts do not hold would fail on every row: each
    # column it reads is one of theirs, under the name the shared table gives it.
    filters = _filters(**_EVERY_TRANSCRIPT_FILTER)
    where, _params = _search(filters)
    read = {column for condition in _own_transcripts(where) for column in re.findall(r"\ba\.(\w+)", condition)}
    held = {name for name, _type in ENTRY_TRANSCRIPT_FIELDS}
    assert read and read <= held
    # Every field held is one a filter reads, but the annotation's ClinVar terms: ClinVar is
    # CoGA's release's, read on the row's allele keys.
    assert held - read == {"clinvar_terms"}


@pytest.mark.parametrize("text_filter", [{"transcript": "ENST0001"}, {"hgvsc": "c.76"}, {"hgvsp": "p.Arg"}])
def test_a_transcript_or_hgvs_text_reads_the_shared_table_for_the_family_s_keys(
    text_filter: dict[str, Any],
) -> None:
    # The row's transcripts hold no text: with a transcript or HGVS filter, every
    # per-transcript filter is read on one row of the shared table, for the family's keys.
    # The excluded ClinVar statuses are the alleles' in CoGA's release, read on the row's
    # allele keys whatever the text.
    filters = _filters(
        impact=["HIGH"], max_gnomad_af=0.01, exclude_clinvar=["benign"], clinvar_coga_release=_RELEASE, **text_filter
    )
    where, params = _search(filters)
    expected_params: dict[str, Any] = {}
    detail = _small_annotation_filter_condition(filters, params=expected_params)
    assert detail is not None
    assert f"(e.key, e.annotationSetHash) IN (SELECT a.key, a.annotationSetHash FROM {_ANNOTATION_TABLES['a']} AS a WHERE ({detail}) AND identity(a.key) IN (" in where
    assert sorted(alias for alias, _table, _rows, _end in _bounds(where)) == ["a", "gi"]
    assert _own_transcripts(where) == []
    assert f"NOT (notEmpty(e.allele_keys) AND arrayAll(k -> k IN (SELECT allele_key FROM {_CLINVAR} " in where
    assert params["annotation_family_guid"] == "keys-family"


def test_the_typed_genes_read_the_gene_index_alone_and_a_panel_s_genes_for_the_family_s_keys() -> None:
    # A gene and the excluded genes are read from the gene index by its sort key: the
    # family's keys would cost more than the subquery itself. A panel's genes, thousands of
    # them, gather the cohort's variants in them unless narrowed to the family's keys.
    clauses, params, _detail = _small_query_filter_parts(
        _CONTEXT, _filters(gene="BRCA1"), exclude_gene_terms=("TP53",)
    )
    where = " AND ".join(clauses)
    assert _subqueries(where) == ["gi", "gi"]
    assert not _bounds(where)
    assert "fk." not in where and "annotation_family_guid" not in params
    panel_params: dict[str, Any] = {}
    panel = _small_panel_filter_condition(_CONTEXT, _filters(), _PANEL, params=panel_params)
    assert panel is not None
    assert [alias for alias, _table, _rows, _end in _bounds(panel)] == ["gi"]
    assert panel_params["annotation_family_guid"] == "keys-family"


def test_the_shared_table_subqueries_read_only_the_family_s_keys() -> None:
    where, params = _search(_filters(**_EVERY_ANNOTATION_FILTER, hgvsc="c.76"))
    # The gene and the panel's genes (gene index), the excluded genes (gene index), the rsID
    # (annotation index), the transcript filters with an HGVS text (annotations); all but the
    # typed genes for the family's keys.
    assert sorted(_subqueries(where)) == ["a", "ai", "gi", "gi", "gi"]
    bounds = _bounds(where)
    assert sorted(alias for alias, _table, _rows, _end in bounds) == ["a", "ai", "gi"]
    assert {table for _alias, table, _rows, _end in bounds} == {_ENTRIES}
    assert {rows for _alias, _table, rows, _end in bounds} == {
        "fk.family_guid = %(annotation_family_guid)s AND fk.sign = 1"
        " AND fk.project_guid IN %(annotation_project_ids)s"
    }
    assert params["annotation_family_guid"] == params["family_guid"] == "keys-family"
    assert params["annotation_project_ids"] == params["project_ids"] == ("keys-project-a", "keys-project-b")


def test_each_bound_closes_its_own_subquery() -> None:
    # The family's keys narrow the annotation rows the subquery reads, inside it: the bound
    # stands right before the subquery's closing parenthesis, never on the search's own rows.
    where, _params = _search(_filters(**_EVERY_ANNOTATION_FILTER, hgvsc="c.76"))
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
    filters = _filters(**_EVERY_ANNOTATION_FILTER, hgvsc="c.76", **locus)
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
    assert len(bounds) == 3
    # One text for every bounded subquery of the search: one set to build.
    assert {rows for _alias, _table, rows, _end in bounds} == {expected}


def test_a_family_without_projects_reads_its_keys_in_every_project() -> None:
    context = replace(_CONTEXT, project_ids=[])
    where, params = _search(_filters(**_EVERY_ANNOTATION_FILTER), context)
    assert {rows for _alias, _table, rows, _end in _bounds(where)} == {
        "fk.family_guid = %(annotation_family_guid)s AND fk.sign = 1"
    }
    assert "annotation_project_ids" not in params


def test_a_search_without_annotation_filters_reads_no_family_keys() -> None:
    clauses, params, _detail = _small_query_filter_parts(_CONTEXT, _filters())
    assert not any("fk." in clause or "e.transcripts" in clause for clause in clauses)
    assert "annotation_family_guid" not in params


def test_the_variant_explorer_reads_the_annotations_of_every_family() -> None:
    # The explorer's panel condition is the family page's, for every family at once: it
    # keeps no family's bound. Its rows hold no transcripts: its per-transcript filters read
    # the shared table (variant_explorer_service._carriers_where).
    scope = ExplorerScope(assembly_id="a1", assembly_name="GRCh38", project_ids=["p1"])
    params: dict[str, Any] = {}
    condition = _small_panel_filter_condition(scope, _filters(), _PANEL, params=params)
    assert condition is not None
    assert f"FROM {_ANNOTATION_TABLES['gi']} AS gi WHERE " in condition
    assert "fk." not in condition
    assert "annotation_family_guid" not in params


def test_every_annotation_subquery_matches_the_row_by_its_key_and_annotation_set() -> None:
    # A row is read with its own annotation: the annotation rows of its key and annotation set,
    # whatever their version (where they came from: vcf_info, vep_tsv). The rows of one key and
    # one set hold the same annotations under any version, and the version, a LowCardinality
    # string, made the set each subquery builds two to three times slower (measured
    # 2026-11-06). Gene, rsID, transcript text, panel and excluded-gene subqueries.
    where, _params = _search(_filters(**_EVERY_ANNOTATION_FILTER, transcript="ENST0001"))
    subqueries = _subqueries(where)
    assert sorted(set(subqueries)) == ["a", "ai", "gi"]
    matched = re.findall(r"(\(e\.[^()]*\)) (?:NOT )?IN \(SELECT (\w+)\.key, \2\.annotationSetHash FROM ", where)
    assert len(matched) == len(subqueries)
    assert {row for row, _alias in matched} == {"(e.key, e.annotationSetHash)"}
    assert "annotation_version" not in where
