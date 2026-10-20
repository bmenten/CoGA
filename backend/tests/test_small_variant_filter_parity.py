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

from backend.app.services.clickhouse_family_variants import _kept_small_candidates, _SmallVariantScope
from backend.app.services.clickhouse_variant_queries import (
    _small_annotation_filter_condition,
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
from backend.app.services.family_variant_filters import (
    SmallVariantQueryFilters,
    small_variant_type_matches,
    stored_precision,
)


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


def test_a_gene_panel_of_more_than_1000_regions_keeps_a_variant_in_one_of_its_regions() -> None:
    # A panel keeps a variant annotated with one of its genes or in one of its regions,
    # whatever its size. A gene panel of more than 1,000 regions, such as the Mendeliome,
    # was matched on its genes alone, in SQL and here, and left out a variant a smaller
    # panel reaches by position only (owner decision 2026-10-14). The SQL side runs on
    # ClickHouse in integration/test_small_variant_filter_parity_clickhouse.py.
    fillers = tuple(Region("21", 30_000_000 + 1_000 * index, 30_000_500 + 1_000 * index) for index in range(1_000))
    over_the_variant = Region("11", 50, 150)
    in_a_region_only = _record("11-100-C-T", annotations=[{"gene": "OTHER"}])
    outside = _record("11-200-C-T", annotations=[{"gene": "OTHER"}])
    large = PanelFilterConstraints(genes=("GENE1",), regions=(*fillers, over_the_variant))
    assert _keeps(in_a_region_only, panel_constraints=large)
    assert _keeps(_record("11-100-C-T"), panel_constraints=large)
    assert _keeps(_record("11-200-C-T"), panel_constraints=large)  # by its gene, outside every region
    assert not _keeps(outside, panel_constraints=large)
    # A smaller panel keeps the same variants.
    small = PanelFilterConstraints(genes=("GENE1",), regions=(over_the_variant,))
    assert _keeps(in_a_region_only, panel_constraints=small)
    assert not _keeps(outside, panel_constraints=small)


def _scope(**fields: Any) -> _SmallVariantScope:
    """A search's scope: no panel, review selection, region or excluded gene but those given."""
    values: dict[str, Any] = {
        "panel_constraints": PanelFilterConstraints(),
        "review_variant_ids": None,
        "excluded_review_variant_ids": set(),
        "include_review_filter_active": False,
        "include_regions": [],
        "exclude_regions": [],
        "exclude_gene_regions": [],
        "exclude_gene_terms": [],
    }
    values.update(fields)
    return _SmallVariantScope(**values)


def test_an_excluded_gene_leaves_out_every_variant_annotated_with_it() -> None:
    # The SQL leaves out a variant annotated with an excluded gene (in the entry's genes or
    # the gene index: symbol or Ensembl id, any capitals), besides those in its loci; Python
    # left out those in its loci alone.
    in_gene = _record("11-100-C-T", annotations=[{"gene": "SYNC", "gene_id": "ENSG_SYNC"}])
    other = _record("11-200-C-T", annotations=[{"gene": "OTHER"}])
    filters = SmallVariantQueryFilters(page=1, page_size=100, exclude_gene="SYNC")
    for terms in (["SYNC"], ["sync"], ["ENSG_SYNC"]):
        kept = _kept_small_candidates([in_gene, other], filters, _scope(exclude_gene_terms=terms), _context())
        assert [record.variant_id for record in kept] == [other.variant_id], terms


def test_also_hit_by_an_sv_keeps_a_variant_of_a_gene_an_sv_hits() -> None:
    # The SQL keeps a variant whose genes (the entry's) hold one the family's SVs hit, and
    # nothing when none is resolved; Python kept every variant.
    in_hit_gene = _record("11-100-C-T", gene_symbols=["SYNB"])
    other = _record("11-200-C-T", gene_symbols=["OTHER"])
    assert _keeps(in_hit_gene, require_sv_second_hit=True, sv_hit_genes=["synb"])
    assert not _keeps(other, require_sv_second_hit=True, sv_hit_genes=["SYNB"])
    assert not _keeps(in_hit_gene, require_sv_second_hit=True, sv_hit_genes=[])
    assert _keeps(other, require_sv_second_hit=False, sv_hit_genes=["SYNB"])


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


def test_the_rsid_filter_searches_the_variants_stored_rsid() -> None:
    # The stored rsID is the VCF's ID, else the existing variants VEP lists on the first
    # transcript (the writer's _annotation_rsid); the SQL searches it alone. Python also
    # searched every transcript's list, so it kept a variant for another id VEP lists.
    annotations = [{"gene": "GENE1", "rsid": "rs1001&COSV55"}]
    with_vcf_id = _record("11-100-C-T", rsid="rs1001", annotations=annotations)
    assert _keeps(with_vcf_id, rsid="rs1001")
    assert not _keeps(with_vcf_id, rsid="COSV55")
    without_vcf_id = _record("11-100-C-T", rsid="rs1001&COSV55", annotations=annotations)
    assert _keeps(without_vcf_id, rsid="COSV55")


# --- the consequence --------------------------------------------------------------------


def test_a_consequence_value_joining_two_terms_is_read_as_the_sql_reads_it() -> None:
    # The SQL splits a value as VEP joins terms (&, and , ; | /) and keeps a transcript
    # with any of them; Python looked for the joined text as one term and kept nothing.
    params: dict[str, Any] = {}
    _small_annotation_filter_condition(
        SmallVariantQueryFilters(page=1, page_size=100, effect=["missense_variant&splice_region_variant"]),
        params=params,
    )
    assert sorted(params["detail_effect_terms"]) == ["missense_variant", "splice_region_variant"]
    missense = _record("11-100-C-T", annotations=[{"gene": "GENE1", "effect": "missense_variant"}])
    assert _keeps(missense, effect=["missense_variant&splice_region_variant"])
    assert _keeps(missense, effect=["splice_region_variant/missense_variant"])
    assert not _keeps(missense, effect=["splice_region_variant&intron_variant"])


# --- in-silico scores -------------------------------------------------------------------


def test_a_score_of_zero_meets_a_minimum_of_zero() -> None:
    # The SQL reads ifNull(score, -1) >= minimum; Python read `score or -1`, so a score of 0
    # (a SpliceAI 0.00, most variants' own) missed a minimum of 0.
    scored = _record(
        "11-100-C-T",
        annotations=[{"gene": "GENE1", "impact": "LOW", "spliceai_max": 0.0, "cadd_phred": 0.0, "revel": 0.0}],
    )
    assert _keeps(scored, min_spliceai=0.0)
    assert _keeps(scored, min_cadd=0.0)
    assert _keeps(scored, min_revel=0.0)
    assert not _keeps(scored, min_spliceai=0.01)


def test_a_missing_score_reads_as_minus_one_in_either_path() -> None:
    unscored = _record("11-100-C-T", annotations=[{"gene": "GENE1", "impact": "LOW"}])
    assert not _keeps(unscored, min_cadd=0.0)
    # A minimum below the scores' range keeps it, as ifNull(score, -1) does.
    assert _keeps(unscored, min_cadd=-1.0)


# --- thresholds at the precision a value is stored at -----------------------------------


def test_stored_precision_is_the_nearest_32_bit_value() -> None:
    # ClickHouse stores frequencies, scores and allele fractions as Float32. These round
    # past the threshold of the same value at 64 bits.
    assert stored_precision(0.45) < 0.45
    assert stored_precision(25.3) < 25.3
    assert stored_precision(0.001) > 0.001
    assert stored_precision(0.5) == 0.5
    assert stored_precision(stored_precision(0.7)) == stored_precision(0.7)
    assert stored_precision(1e300) == float("inf")
    assert stored_precision(-1e300) == float("-inf")


def test_the_sql_compares_each_stored_float_with_its_threshold_at_32_bits() -> None:
    params: dict[str, Any] = {}
    condition = _small_annotation_filter_condition(
        SmallVariantQueryFilters(
            page=1,
            page_size=100,
            max_gnomad_af=0.001,
            max_gnomad_exomes_af=0.001,
            max_gnomad_genomes_af=0.001,
            max_gnomad_popmax_af=0.001,
            max_topmed_af=0.001,
            max_gnomad_hom_count=1,
            min_cadd=25.3,
            min_revel=0.7,
            min_spliceai=0.35,
        ),
        params=params,
    )
    assert condition is not None
    for column in ("gnomad_af", "gnomad_exomes_af", "gnomad_genomes_af", "gnomad_popmax_af", "topmed_af"):
        assert f"ifNull(a.{column}, 0) <= toFloat32(%(detail_max_{column})s)" in condition
    # A count is stored as an integer, and compared as one.
    assert "ifNull(a.gnomad_hom_count, 0) <= %(detail_max_gnomad_hom_count)s" in condition
    assert "ifNull(a.cadd_phred, -1) >= toFloat32(%(detail_min_cadd)s)" in condition
    assert "ifNull(a.revel, -1) >= toFloat32(%(detail_min_revel)s)" in condition
    assert condition.count("ifNull(a.spliceai_max, -1) >= toFloat32(%(detail_min_spliceai)s)") == 2
    clauses, _params = _where(sample_filters=["PROBAND::::0.45"])
    assert any(
        "arrayMax(af) >= toFloat32(%(sample_filter_0_min_af)s)" in clause
        and "ab >= toFloat32(%(sample_filter_0_min_af)s)" in clause
        for clause in clauses
    )


def test_python_compares_a_frequency_and_a_score_at_32_bits() -> None:
    # A value that equals the threshold at 32 bits is the threshold's, in SQL and Python.
    just_above = 0.001 + 1e-12
    assert stored_precision(just_above) == stored_precision(0.001)
    frequent = _record("11-100-C-T", annotations=[{"gene": "GENE1", "gnomad_af": just_above}])
    assert _keeps(frequent, max_gnomad_af=0.001)
    assert not _keeps(frequent, max_gnomad_af=0.0009)
    scored = _record("11-100-C-T", annotations=[{"gene": "GENE1", "cadd_phred": 25.3 - 1e-12}])
    assert _keeps(scored, min_cadd=25.3)


def test_python_compares_a_calls_allele_fraction_at_32_bits() -> None:
    # The call's fraction as the search reads it back: 0.45 stored, a little below 0.45.
    call = SmallVariantCall(
        sample="PROBAND", gt="0/1", gq=40.0, dp=20, af=[stored_precision(0.45)], ad=[11, 9], ps=None
    )
    record = _record("11-100-C-T", calls=[call])
    assert _keeps(record, sample_filters=["PROBAND::::0.45"])
    assert not _keeps(record, sample_filters=["PROBAND::::0.46"])
