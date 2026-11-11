"""The family search's small-variant filters as its SQL reads them.

The family search's SQL (``_small_query_filter_parts``) is the one definition of every
small-variant filter: the plain page reads it, and so does every view that pairs, ranks or
samples the rows it returns; no second, Python reading of the same filters applies them again.
These tests pin, without a database, the SQL for each rule such a reading could read apart;
``integration/test_small_variant_filter_parity_clickhouse.py`` holds the SQL to
the documented rule on real ClickHouse, in every view of the family page.
"""

from __future__ import annotations

from typing import Any

from backend.app.services.clickhouse_variant_queries import (
    _small_annotation_filter_condition,
    _small_annotation_scope_clauses,
    _small_query_filter_parts,
    _small_variant_where_clauses,
    _variant_overlaps_regions,
    _xpos,
)
from backend.app.services.clickhouse_variant_records import Region
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.family_variant_filters import SmallVariantQueryFilters


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


def _filters(**values: Any) -> SmallVariantQueryFilters:
    return SmallVariantQueryFilters(page=1, page_size=100, **values)


def _where(**filters: Any) -> tuple[list[str], dict[str, Any]]:
    """The family search's base WHERE clauses and their parameters for ``filters``."""
    return _small_variant_where_clauses(_context(), _filters(**filters))


def _parts(**filters: Any) -> tuple[list[str], dict[str, Any]]:
    """The family search's whole WHERE (``_small_query_filter_parts``) for ``filters``."""
    scope = {key: filters.pop(key) for key in ("exclude_gene_terms",) if key in filters}
    clauses, params, _detail = _small_query_filter_parts(_context(), _filters(**filters), **scope)
    return clauses, params


# --- chromosome names and regions -------------------------------------------------------


def test_a_chromosome_filter_names_chrm_by_either_name() -> None:
    # The SQL keeps a row whose chromosome is one of the filter's aliases, chrM's all of
    # M, MT, chrM and chrMT. A GRCh38 VCF writes chrM (stored M), a GRCh37 one MT.
    for named in ("M", "MT", "chrM", "chrMT"):
        clauses, params = _where(chromosome=named)
        assert "e.chrom IN %(chromosomes)s" in clauses
        assert {"M", "MT", "chrM", "chrMT"} <= set(params["chromosomes"]), named
    assert set(_where(chromosome="chr11")[1]["chromosomes"]) == {"11", "chr11"}


def test_regions_compare_chromosomes_as_the_sql_does() -> None:
    # The region test the structural-variant filter reads: in any capitals, with or without
    # chr, chrM by either name, as the SQL's _chromosome_match_key.
    assert _variant_overlaps_regions("X", 100, 100, [Region("x", 50, 150)])
    assert _variant_overlaps_regions("X", 100, 100, [Region("chrx", 50, 150)])
    assert _variant_overlaps_regions("M", 3243, 3243, [Region("chrMT", 1, 16_569)])
    assert _variant_overlaps_regions("MT", 3243, 3243, [Region("chrM", 1, 16_569)])
    assert _variant_overlaps_regions("11", 100, 100, [Region("chr11", 50, 150)])
    assert not _variant_overlaps_regions("1", 100, 100, [Region("11", 50, 150)])
    assert not _variant_overlaps_regions("X", 100, 100, [Region("x", 101, 150)])


def test_a_region_is_read_from_its_lower_coordinate_to_its_higher_as_the_sql_reads_it() -> None:
    # A region written end first keeps every variant in it, as the SQL does (CLIN-50).
    assert _variant_overlaps_regions("1", 100, 100, [Region("1", 150, 50)])
    assert _variant_overlaps_regions("1", 40, 60, [Region("1", 150, 50)])
    assert not _variant_overlaps_regions("1", 151, 160, [Region("1", 150, 50)])


# --- the location -----------------------------------------------------------------------


def test_an_overlapping_location_from_a_start_reads_the_variants_end() -> None:
    # With overlap and a start alone, the SQL reads the variant's end, not its start (nor the
    # xpos range from the start), so a deletion from 140 to 142 is in from 141.
    clauses, params = _where(chromosome="11", start=95_100_141, overlap=True)
    assert "(e.pos + length(e.ref) - 1) >= %(start)s" in clauses
    assert "e.pos >= %(start)s" not in clauses
    assert params["xpos_start"] == _xpos("11", 0)
    scope = _small_annotation_scope_clauses(_filters(chromosome="11", start=95_100_141, overlap=True), params=params)
    assert "(ai.pos + length(ai.ref) - 1) >= %(start)s" in scope


def test_the_location_reads_alike_with_and_without_overlap_otherwise() -> None:
    clauses, params = _where(chromosome="11", start=100, end=200)
    assert {"e.pos >= %(start)s", "e.pos <= %(end)s"} <= set(clauses)
    assert (params["xpos_start"], params["xpos_end"]) == (_xpos("11", 100), _xpos("11", 200))
    clauses, params = _where(chromosome="11", start=100, end=200, overlap=True)
    assert {"(e.pos + length(e.ref) - 1) >= %(start)s", "e.pos <= %(end)s"} <= set(clauses)
    assert (params["xpos_start"], params["xpos_end"]) == (_xpos("11", 0), _xpos("11", 200))


# --- the variant type -------------------------------------------------------------------


def test_the_sql_condition_of_each_variant_type() -> None:
    # INDEL is every variant that is no SNV, an MNV included; any capitals and spaces.
    assert "length(e.ref) = 1 AND length(e.alt) = 1" in _where(variant_type="SNV")[0]
    assert "length(e.ref) = 1 AND length(e.alt) = 1" in _where(variant_type="snv")[0]
    assert "NOT (length(e.ref) = 1 AND length(e.alt) = 1)" in _where(variant_type="INDEL")[0]
    assert "length(e.ref) = length(e.alt) AND length(e.ref) > 1" in _where(variant_type=" mnv ")[0]


def test_a_variant_type_that_names_none_asks_nothing() -> None:
    assert _where(variant_type="ins")[0] == _where()[0]


# --- the callset and the rsID: the text as typed, but for the spaces around it ---------


def test_the_sql_reads_a_callset_and_an_rsid_without_the_spaces_around_them() -> None:
    clauses, params = _where(source=" clair3 ", rsid=" rs1001 ")
    assert (params["source"], params["rsid"]) == ("clair3", "rs1001")
    assert "positionCaseInsensitive(e.source, %(source)s) > 0" in clauses


def test_a_blank_callset_or_rsid_asks_nothing() -> None:
    blank_clauses, blank_params = _small_variant_where_clauses(
        _context(), _filters(source="  ", rsid=" "), exclude_imputed=True
    )
    clauses, params = _small_variant_where_clauses(_context(), _filters(), exclude_imputed=True)
    # No condition on the text, and the imputed callsets stay hidden.
    assert (blank_clauses, blank_params) == (clauses, params)
    assert "lowerUTF8(e.source) NOT IN %(imputed_sources)s" in blank_clauses


def test_the_rsid_filter_searches_the_entrys_and_the_indexs_rsid() -> None:
    clauses, _params = _where(rsid="rs1001")
    (condition,) = [clause for clause in clauses if "%(rsid)s" in clause]
    assert "positionCaseInsensitive(ifNull(e.rsid, ''), %(rsid)s) > 0" in condition
    assert "positionCaseInsensitive(ifNull(ai.rsid, ''), %(rsid)s) > 0" in condition


# --- genes: excluded, and also hit by an SV ---------------------------------------------


def test_an_excluded_gene_leaves_out_every_variant_annotated_with_it() -> None:
    # By the entry's genes or the gene index (symbol or Ensembl id, any capitals), besides
    # those in its loci.
    clauses, params = _parts(exclude_gene="SYNC", exclude_gene_terms=["SYNC"])
    (condition,) = [clause for clause in clauses if "entry_exclude_gene" in clause]
    assert condition.startswith("NOT (") and "gi.gene_term" in condition
    assert [value for name, value in params.items() if name.startswith("entry_exclude_gene")] == [("sync",)]


def test_also_hit_by_an_sv_keeps_a_variant_of_a_gene_an_sv_hits() -> None:
    clauses, params = _parts(require_sv_second_hit=True, sv_hit_genes=["synb"])
    assert any("sv_hit_gene" in clause for clause in clauses)
    # None resolved: nothing qualifies.
    clauses, _params = _parts(require_sv_second_hit=True, sv_hit_genes=[])
    assert "0" in clauses


# --- the callers' FILTER ("Pass variants only"; the owner's decision of 2026-11-03) -------


def test_the_sql_condition_of_pass_only() -> None:
    clauses, params = _where(pass_only=True)
    (condition,) = [clause for clause in clauses if "pass_only_filter_values" in clause]
    # Over the calls of the family's members alone (the owner's decision of 2026-11-06).
    assert condition.startswith(
        "if(arrayExists((sample_id, own) -> sample_id IN %(visible_sample_ids)s AND notEmpty(own), "
        "e.calls.sampleId, e.calls.filters), "
    )
    assert "arrayAll((sample_id, gt, own) -> NOT (sample_id IN %(visible_sample_ids)s) OR NOT (" in condition
    assert "e.calls.sampleId, e.calls.gt, e.calls.filters" in condition
    assert "arrayAll(value -> value IN %(pass_only_filter_values)s, e.filters)" in condition
    assert params["pass_only_filter_values"] == ("", ".", "PASS")
    # The genotypes that carry the ALT allele, as the genotype filters read them.
    assert "0/1" in params["pass_only_alt_genotypes"] and "0/0" not in params["pass_only_alt_genotypes"]
    assert _where()[0] == [clause for clause in clauses if clause != condition]


def test_the_sql_condition_of_the_phase_set_reads_the_family_members_calls() -> None:
    # A phase set only a member the family does not show holds keeps nothing (the owner's
    # decision of 2026-11-06).
    clauses, params = _where(phase_set=4321)
    (condition,) = [clause for clause in clauses if "phase_set" in clause]
    assert condition == (
        "arrayExists((sample_id, ps) -> sample_id IN %(visible_sample_ids)s AND ps = %(phase_set)s, "
        "e.calls.sampleId, e.calls.ps)"
    )
    assert params["phase_set"] == 4321 and params["visible_sample_ids"]


# --- the consequence --------------------------------------------------------------------


def test_a_consequence_value_joining_two_terms_is_read_as_its_terms() -> None:
    # The SQL splits a value as VEP joins terms (&, and , ; | /) and keeps a transcript
    # with any of them.
    for value in ("missense_variant&splice_region_variant", "splice_region_variant/missense_variant"):
        params: dict[str, Any] = {}
        _small_annotation_filter_condition(_filters(effect=[value]), params=params)
        assert sorted(params["detail_effect_terms"]) == ["missense_variant", "splice_region_variant"]


# --- thresholds at the precision a value is stored at -----------------------------------


def test_the_sql_compares_each_stored_float_with_its_threshold_at_32_bits() -> None:
    params: dict[str, Any] = {}
    condition = _small_annotation_filter_condition(
        _filters(
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
    # A score that is missing (or no number, stored as unknown) reads as -1, so a minimum of 0
    # keeps a score of 0 and a minimum below -1 keeps it too.
    assert "ifNull(a.cadd_phred, -1) >= toFloat32(%(detail_min_cadd)s)" in condition
    assert "ifNull(a.revel, -1) >= toFloat32(%(detail_min_revel)s)" in condition
    assert condition.count("ifNull(a.spliceai_max, -1) >= toFloat32(%(detail_min_spliceai)s)") == 2
    clauses, _params = _where(sample_filters=["PROBAND::::0.45"])
    assert any(
        "arrayMax(af) >= toFloat32(%(sample_filter_0_min_af)s)" in clause
        and "ab >= toFloat32(%(sample_filter_0_min_af)s)" in clause
        for clause in clauses
    )


# --- the inheritance modes as the SQL's flags (One filter definition) -----------------------


def _trio_context() -> FamilyMetadataContext:
    names = {"SON": ("male", "affected"), "MOTHER": ("female", "unaffected"), "FATHER": ("male", "unaffected")}
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="FAM_FLAGS",
        project_ids=["project-uuid"],
        sample_rows=[{"sample_id": name, "sex": sex, "clinical_status": status} for name, (sex, status) in names.items()],
        sample_uuid_to_name={f"uuid-{name}": name for name in names},
        sample_name_to_uuid={name: f"uuid-{name}" for name in names},
        affected_sample_names=["SON"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


def test_each_flag_is_the_inheritance_filters_own_sql() -> None:
    # One definition: the column that says a variant fits a mode is the WHERE the mode's
    # filter applies, its parameters named apart.
    from backend.app.services.clickhouse_variant_queries import (
        _small_native_inheritance_clauses,
        _small_segregation_flag_columns,
    )

    context = _trio_context()
    columns, params = _small_segregation_flag_columns(context)
    for mode, column in zip(("de_novo_dominant", "recessive_homozygous", "x_linked"), columns, strict=True):
        clauses, where_params = _small_native_inheritance_clauses(
            context, SmallVariantQueryFilters(page=1, page_size=100, inheritance=mode)
        )
        renamed = " AND ".join(f"({clause})" for clause in clauses).replace(
            "%(inheritance_", f"%(segregation_{mode}_"
        )
        assert column == f"max({renamed}) AS segregation_{mode}"
        assert {name.replace("inheritance_", f"segregation_{mode}_", 1): value for name, value in where_params.items()} == {
            name: value for name, value in params.items() if name.startswith(f"segregation_{mode}_")
        }


def test_without_an_affected_member_no_variant_fits_a_mode() -> None:
    from dataclasses import replace

    from backend.app.services.clickhouse_variant_queries import _small_segregation_flag_columns

    columns, params = _small_segregation_flag_columns(replace(_trio_context(), affected_sample_names=[]))
    assert columns == [f"max(0) AS segregation_{mode}" for mode in ("de_novo_dominant", "recessive_homozygous", "x_linked")]
    assert params == {}


def test_the_flags_bind_no_parameter_the_search_binds() -> None:
    # A search with every part of the WHERE that binds parameters, an inheritance mode and
    # carrier screening among them: the flags' parameters are named apart from each.
    from backend.app.services.clickhouse_family_variants import _merge_params
    from backend.app.services.clickhouse_variant_queries import (
        _small_query_filter_parts,
        _small_segregation_flag_columns,
    )

    context = _trio_context()
    for mode in ("de_novo_dominant", "recessive_homozygous", "x_linked", "compound_het", "recessive"):
        filters = SmallVariantQueryFilters(
            page=1, page_size=100, inheritance=mode, expanded_carrier_screening=True, pass_only=True,
            phase_set=7, chromosome="X", start=1, end=2, source="clair3", rsid="rs1", gene="SYNA",
            impact=["HIGH"], max_gnomad_af=0.01, min_cadd=20.0, sift="deleterious",
            exclude_clinvar=["Benign"], sample_filters=["SON:het:20:10:0.2:4", "MOTHER:ref"],
            require_sv_second_hit=True, sv_hit_genes=["SYNA"],
        )
        where_clauses, params, _ = _small_query_filter_parts(context, filters)
        _columns, flag_params = _small_segregation_flag_columns(context)
        assert not set(params) & set(flag_params), sorted(set(params) & set(flag_params))
        _merge_params(params, flag_params)


def test_a_parameter_bound_twice_or_a_flag_that_is_no_flag_is_refused() -> None:
    import pytest

    from backend.app.services.clickhouse_family_variants import _merge_params, _segregation_flags_from_row

    params = {"a": 1}
    _merge_params(params, {"a": 1, "b": 2})
    assert params == {"a": 1, "b": 2}
    with pytest.raises(ValueError, match="bound twice"):
        _merge_params(params, {"a": 3})
    assert _segregation_flags_from_row((1, 0, 1)).x_linked
    for row in ((1, 0), (1, 0, None), (1, 2, 0)):
        with pytest.raises(ValueError):
            _segregation_flags_from_row(row)
