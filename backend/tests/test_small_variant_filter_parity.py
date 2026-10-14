"""The family search reads each small-variant filter alike in ClickHouse and in Python (ENG-1).

The plain page filters in ClickHouse (``_small_query_filter_parts``). The ranked view and the
compound-het, recessive and carrier-screening searches apply the same filters again in
Python (``_kept_small_candidates`` → ``_small_record_matches``) to the rows ClickHouse
returned, so where Python read a filter more narrowly those views dropped variants the plain
page listed. ``integration/test_small_variant_filter_parity_clickhouse.py`` runs every filter
through both paths on real ClickHouse; these tests pin, without a database, each rule the two
paths had read apart.
"""

from __future__ import annotations

from typing import Any

from backend.app.services.clickhouse_variant_queries import (
    _small_annotation_scope_clauses,
    _small_record_matches,
    _small_variant_where_clauses,
    _variant_overlaps_regions,
    _xpos,
)
from backend.app.services.clickhouse_variant_records import (
    PanelFilterConstraints,
    Region,
    SmallVariantCall,
    SmallVariantRecord,
)
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.family_variant_filters import SmallVariantQueryFilters, small_variant_type_matches


def _record(variant_id: str, *, annotations: list[dict[str, Any]] | None = None, **fields: Any) -> SmallVariantRecord:
    """A variant as the family search reads it back: its chromosome as stored."""
    chrom, pos, ref, alt = variant_id.split("-")
    values: dict[str, Any] = {
        "variant_key": None,
        "variant_id": variant_id,
        "chr": chrom,
        "start": int(pos),
        "end": int(pos) + len(ref) - 1,
        "ref": ref,
        "alt": alt,
        "source": "clair3",
        "rsid": None,
        "filters": ["PASS"],
        "gene_symbols": [],
        "annotations": annotations if annotations is not None else [{"gene": "GENE1", "impact": "MODERATE"}],
        "calls": [SmallVariantCall(sample="PROBAND", gt="0/1", gq=40.0, dp=30, af=[0.5], ad=[15, 15], ps=None)],
    }
    values.update(fields)
    return SmallVariantRecord(**values)


def _context() -> FamilyMetadataContext:
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="FAM_ENG1",
        project_ids=["project-uuid"],
        sample_rows=[{"sample_id": "PROBAND", "sex": "female", "clinical_status": "affected"}],
        sample_uuid_to_name={"sample-proband": "PROBAND"},
        sample_name_to_uuid={"PROBAND": "sample-proband"},
        affected_sample_names=["PROBAND"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


def _where(**filters: Any) -> tuple[list[str], dict[str, Any]]:
    """The family search's WHERE clauses and their parameters for ``filters``."""
    return _small_variant_where_clauses(_context(), SmallVariantQueryFilters(page=1, page_size=100, **filters))


def _keeps(record: SmallVariantRecord, **kwargs: Any) -> bool:
    """Whether the Python record filter keeps ``record``: the filters and the scope given."""
    scope = {
        "include_regions": kwargs.pop("include_regions", []),
        "exclude_regions": kwargs.pop("exclude_regions", []),
        "exclude_gene_regions": kwargs.pop("exclude_gene_regions", []),
        "panel_constraints": kwargs.pop("panel_constraints", None),
    }
    filters = SmallVariantQueryFilters(page=1, page_size=100, **kwargs)
    return _small_record_matches(record, filters, **scope)


# --- chromosome names -------------------------------------------------------------------


def test_a_chromosome_filter_names_chrm_by_either_name() -> None:
    # The SQL keeps a row whose chromosome is one of the filter's aliases, chrM's all of
    # M, MT, chrM and chrMT. A GRCh38 VCF writes chrM (stored M), a GRCh37 one MT.
    for stored in ("M", "MT"):
        for named in ("M", "MT", "chrM", "chrMT"):
            assert _keeps(_record(f"{stored}-3243-A-G"), chromosome=named), (stored, named)
    assert _keeps(_record("11-100-C-T"), chromosome="chr11")
    assert not _keeps(_record("11-100-C-T"), chromosome="1")


def test_regions_compare_chromosomes_as_the_sql_does() -> None:
    # In any capitals, with or without chr, chrM by either name: _chromosome_match_key.
    assert _variant_overlaps_regions("X", 100, 100, [Region("x", 50, 150)])
    assert _variant_overlaps_regions("X", 100, 100, [Region("chrx", 50, 150)])
    assert _variant_overlaps_regions("M", 3243, 3243, [Region("chrMT", 1, 16_569)])
    assert _variant_overlaps_regions("MT", 3243, 3243, [Region("chrM", 1, 16_569)])
    assert _variant_overlaps_regions("11", 100, 100, [Region("chr11", 50, 150)])
    assert not _variant_overlaps_regions("1", 100, 100, [Region("11", 50, 150)])
    assert not _variant_overlaps_regions("X", 100, 100, [Region("x", 101, 150)])


def test_an_interval_in_lower_case_keeps_and_leaves_out_as_the_sql_does() -> None:
    on_x = _record("X-72000100-C-T")
    assert _keeps(on_x, include_regions=[Region("x", 72_000_000, 72_000_105)])
    assert not _keeps(on_x, exclude_regions=[Region("x", 72_000_000, 72_001_000)])
    assert not _keeps(on_x, exclude_gene_regions=[Region("x", 72_000_000, 72_001_000)])


def test_a_panel_region_on_chrmt_keeps_a_variant_stored_on_m() -> None:
    panel = PanelFilterConstraints(regions=(Region("chrMT", 12_000, 13_000),))
    assert _keeps(_record("M-12345-A-G"), panel_constraints=panel)
    assert not _keeps(_record("11-12345-A-G"), panel_constraints=panel)


# --- the location -----------------------------------------------------------------------


def test_a_location_keeps_a_variant_that_starts_in_it() -> None:
    # The SQL and the Variant Explorer read a variant's position: a deletion that starts
    # before the end and ends after it is in. Python needed the whole variant inside.
    deletion = _record("11-95100248-GACGTA-G")  # 248 to 253
    assert _keeps(deletion, chromosome="11", start=95_100_100, end=95_100_250)
    assert not _keeps(deletion, chromosome="11", start=95_100_100, end=95_100_247)
    # Without overlap a variant that starts before the start is out, whatever it reaches.
    assert not _keeps(deletion, chromosome="11", start=95_100_250, end=95_100_300)
    assert _keeps(deletion, chromosome="11", start=95_100_250, end=95_100_300, overlap=True)


def test_an_overlapping_location_from_a_start_keeps_a_variant_that_reaches_it() -> None:
    # With overlap and a start alone, the SQL read the variant's start (and the xpos range
    # from the start), so a deletion from 140 to 142 was out from 141. Python kept it.
    deletion = _record("11-95100140-CTT-C")
    assert _keeps(deletion, chromosome="11", start=95_100_141, overlap=True)
    clauses, params = _where(chromosome="11", start=95_100_141, overlap=True)
    assert "(e.pos + length(e.ref) - 1) >= %(start)s" in clauses
    assert "e.pos >= %(start)s" not in clauses
    assert params["xpos_start"] == _xpos("11", 0)
    scope = _small_annotation_scope_clauses(
        SmallVariantQueryFilters(page=1, page_size=100, chromosome="11", start=95_100_141, overlap=True),
        params=params,
    )
    assert "(ai.pos + length(ai.ref) - 1) >= %(start)s" in scope


def test_the_location_reads_alike_with_and_without_overlap_otherwise() -> None:
    clauses, params = _where(chromosome="11", start=100, end=200)
    assert {"e.pos >= %(start)s", "e.pos <= %(end)s"} <= set(clauses)
    assert (params["xpos_start"], params["xpos_end"]) == (_xpos("11", 100), _xpos("11", 200))
    clauses, params = _where(chromosome="11", start=100, end=200, overlap=True)
    assert {"(e.pos + length(e.ref) - 1) >= %(start)s", "e.pos <= %(end)s"} <= set(clauses)
    assert (params["xpos_start"], params["xpos_end"]) == (_xpos("11", 0), _xpos("11", 200))


# --- the variant type -------------------------------------------------------------------

_SNV, _INSERTION, _DELETION, _MNV = "11-100-C-T", "11-200-C-CA", "11-300-CTT-C", "11-400-AT-GC"


def test_the_variant_type_filter_reads_snv_indel_and_mnv_as_the_sql_does() -> None:
    # INDEL is every variant that is no SNV, an MNV included, as the SQL has always read it.
    kept = {
        "SNV": {_SNV},
        "snv": {_SNV},
        "INDEL": {_INSERTION, _DELETION, _MNV},
        "MNV": {_MNV},
        " mnv ": {_MNV},
    }
    for variant_type, expected in kept.items():
        found = {
            variant_id
            for variant_id in (_SNV, _INSERTION, _DELETION, _MNV)
            if _keeps(_record(variant_id), variant_type=variant_type)
        }
        assert found == expected, variant_type
    assert small_variant_type_matches("AT", "GC", "MNV")
    assert not small_variant_type_matches("A", "G", "MNV")


def test_the_sql_condition_of_each_variant_type() -> None:
    assert "length(e.ref) = 1 AND length(e.alt) = 1" in _where(variant_type="SNV")[0]
    assert "NOT (length(e.ref) = 1 AND length(e.alt) = 1)" in _where(variant_type="INDEL")[0]
    assert "length(e.ref) = length(e.alt) AND length(e.ref) > 1" in _where(variant_type="MNV")[0]


def test_a_variant_type_that_names_none_asks_nothing_in_either_path() -> None:
    # The SQL adds no condition for it; Python used to look for its text in "SNV" or
    # "INDEL", and dropped every variant whose type did not hold it.
    assert all(_keeps(_record(variant_id), variant_type="ins") for variant_id in (_SNV, _INSERTION, _MNV))
    assert _where(variant_type="ins")[0] == _where()[0]


# --- the callset and the rsID: the text as typed, but for the spaces around it ---------


def test_the_sql_reads_a_callset_and_an_rsid_without_the_spaces_around_them() -> None:
    clauses, params = _where(source=" clair3 ", rsid=" rs1001 ")
    assert (params["source"], params["rsid"]) == ("clair3", "rs1001")
    assert "positionCaseInsensitive(e.source, %(source)s) > 0" in clauses
    # Python already read them so.
    record = _record("11-100-C-T", rsid="rs1001")
    assert _keeps(record, source=" clair3 ")
    assert _keeps(record, rsid=" rs1001 ")


def test_a_blank_callset_or_rsid_asks_nothing_in_either_path() -> None:
    blank_clauses, blank_params = _small_variant_where_clauses(
        _context(), SmallVariantQueryFilters(page=1, page_size=100, source="  ", rsid=" "), exclude_imputed=True
    )
    clauses, params = _small_variant_where_clauses(
        _context(), SmallVariantQueryFilters(page=1, page_size=100), exclude_imputed=True
    )
    # No condition on the text, and the imputed callsets stay hidden.
    assert (blank_clauses, blank_params) == (clauses, params)
    assert "lowerUTF8(e.source) NOT IN %(imputed_sources)s" in blank_clauses
    assert _keeps(_record("11-100-C-T"), source="  ", rsid=" ")
