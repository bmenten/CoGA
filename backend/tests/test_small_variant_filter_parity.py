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
    _small_record_matches,
    _variant_overlaps_regions,
)
from backend.app.services.clickhouse_variant_records import (
    PanelFilterConstraints,
    Region,
    SmallVariantCall,
    SmallVariantRecord,
)
from backend.app.services.family_variant_filters import SmallVariantQueryFilters


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
