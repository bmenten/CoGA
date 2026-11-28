"""*ClinVar pathogenic / likely pathogenic overrules the frequency, consequence and in-silico
filters*: a variant with an allele CoGA's ClinVar release holds pathogenic or likely pathogenic
is kept past the consequence filter, the in-silico filter and the frequency ceilings when the
search asks for it (``clinvar_pathogenic_override``).

The override lifts the whole consequence block (the impacts, the consequences and the SpliceAI
minimum, any of which keeps a transcript), the whole in-silico block (CADD, REVEL, SIFT,
PolyPhen, SpliceAI, any of which keeps it) and the whole frequency block (the population
frequencies and the gnomAD homozygote, hemizygote and allele counts), for an allele the release
classifies pathogenic or likely pathogenic, ClinVar's aggregate form included; never for a
benign or conflicting classification, an allele the release does not hold or a search without a
release, never the other per-transcript filters (the transcript flags) or the ClinVar statuses,
and not at all when the search does not ask for it. The classification is the allele's, so it
lifts the blocks on every transcript of the row (``_clinvar_override``); the transcript a
variant names as the one its filters held on reads every filter strictly. The real-ClickHouse
filter spec holds it to the rule (integration/test_small_variant_filter_parity_clickhouse.py).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from backend.app.services.analysis_scope import WHOLE_FAMILY_SCOPE
from backend.app.services.clickhouse_variant_queries import (
    _small_annotation_filter_clauses,
    _small_annotation_filter_condition,
)
from backend.app.services.clickhouse_variant_records import (
    CLINVAR_OVERRIDE_TERMS,
    _status_filter_terms,
    _status_terms,
)
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.family_variant_filters import SmallVariantQueryFilters
from backend.tests._clinvar_release_fakes import stored_allele

_RELEASE = date(2026, 11, 22)
_CONTEXT = FamilyMetadataContext(
    family_uuid="override-family",
    family_id="OVERRIDE",
    project_ids=["override-project"],
    sample_rows=[{"sample_uuid": "uuid-S1", "sample_id": "PROBAND", "role": "proband", "affected": True}],
    sample_uuid_to_name={"uuid-S1": "PROBAND"},
    sample_name_to_uuid={"PROBAND": "uuid-S1"},
    affected_sample_names=["PROBAND"],
    assembly_id="assembly-uuid",
    assembly_name="GRCh38",
    scope=WHOLE_FAMILY_SCOPE,
)
# An allele of the row the release holds (likely) pathogenic.
_PATHOGENIC = (
    "arrayExists(k -> k IN (SELECT allele_key FROM coga.`GRCh38/SNV_INDEL/clinvar` WHERE release = "
    "%(clinvar_override_release)s AND hasAny(classification_terms, %(clinvar_override_terms)s)), e.allele_keys)"
)
_FREQUENCY = (
    "ifNull(a.gnomad_af, 0) <= toFloat32(%(detail_max_gnomad_af)s)"
    " AND ifNull(a.gnomad_popmax_af, 0) <= toFloat32(%(detail_max_gnomad_popmax_af)s)"
)


def _own(condition: str) -> str:
    """``condition`` held on one of the row's own transcripts."""
    return f"arrayExists(a -> ({condition}), e.transcripts)"


def _sql(release: date | None = _RELEASE, **overrides: Any) -> tuple[str, dict[str, Any]]:
    """The family search's annotation clauses for these filters, in CoGA's release ``release``."""
    base: dict[str, Any] = dict(page=1, page_size=100, max_gnomad_af=0.01, max_gnomad_popmax_af=0.01)
    base.update(overrides)
    params: dict[str, Any] = {}
    filters = SmallVariantQueryFilters(clinvar_coga_release=release, **base)
    return " AND ".join(_small_annotation_filter_clauses(_CONTEXT, filters, params=params)), params


# --- The frequency block ----------------------------------------------------------------


def test_the_frequency_block_is_lifted_for_an_allele_the_release_holds_pathogenic() -> None:
    sql, params = _sql(clinvar_pathogenic_override=True)
    # The frequency block on a transcript, or a pathogenic allele: nothing else is asked.
    assert sql == f"({_own(f'({_FREQUENCY})')} OR {_PATHOGENIC})"
    assert params["clinvar_override_terms"] == ["pathogenic", "likely pathogenic"]
    assert params["clinvar_override_release"] == _RELEASE


def test_the_frequency_block_is_strict_without_the_override() -> None:
    sql, params = _sql(clinvar_pathogenic_override=False)
    assert sql == _own(f"({_FREQUENCY})")
    assert not any(name.startswith("clinvar_") for name in params)


def test_without_a_release_the_override_lifts_nothing() -> None:
    # No allele has ClinVar without a release: no variant is kept for it.
    sql, params = _sql(None, clinvar_pathogenic_override=True)
    assert sql == _own(f"({_FREQUENCY})")
    assert not any(name.startswith("clinvar_") for name in params)


def test_the_override_covers_counts_as_well_as_frequencies() -> None:
    sql, _params = _sql(
        clinvar_pathogenic_override=True,
        max_gnomad_af=None,
        max_gnomad_popmax_af=None,
        max_gnomad_hom_count=10,
    )
    assert sql == f"({_own('(ifNull(a.gnomad_hom_count, 0) <= %(detail_max_gnomad_hom_count)s)')} OR {_PATHOGENIC})"


def test_no_override_clause_without_a_filter_it_lifts() -> None:
    sql, params = _sql(
        clinvar_pathogenic_override=True, max_gnomad_af=None, max_gnomad_popmax_af=None, canonical_only=True
    )
    assert sql == _own("(a.canonical)")
    assert "clinvar_override_terms" not in params


# --- The consequence block --------------------------------------------------------------


def test_the_impact_is_lifted_for_an_allele_the_release_holds_pathogenic() -> None:
    # A synonymous or splice-region variant the release holds pathogenic passes HIGH/MODERATE.
    sql, params = _sql(
        clinvar_pathogenic_override=True,
        max_gnomad_af=None,
        max_gnomad_popmax_af=None,
        impact=["HIGH", "MODERATE"],
    )
    assert sql == f"({_own('((a.impact IN %(detail_impact_terms)s))')} OR {_PATHOGENIC})"
    assert params["detail_impact_terms"] == ("high", "moderate")
    assert params["clinvar_override_terms"] == ["pathogenic", "likely pathogenic"]


def test_the_override_lifts_the_whole_consequence_block() -> None:
    # Impact, consequence and the SpliceAI minimum are one unit: any of them keeps a
    # transcript, and the override keeps a pathogenic allele that meets none.
    sql, _params = _sql(
        clinvar_pathogenic_override=True,
        max_gnomad_af=None,
        max_gnomad_popmax_af=None,
        impact=["HIGH"],
        effect=["stop_gained"],
        min_spliceai=0.5,
    )
    consequence = (
        "(a.impact IN %(detail_impact_terms)s OR hasAny(a.effects, %(detail_effect_terms)s)"
        " OR ifNull(a.spliceai_max, -1) >= toFloat32(%(detail_min_spliceai)s))"
    )
    spliceai = "(ifNull(a.spliceai_max, -1) >= toFloat32(%(detail_min_spliceai)s))"
    assert sql == f"({_own(f'({consequence} AND {spliceai})')} OR {_PATHOGENIC})"


def test_the_consequence_block_is_strict_without_the_override() -> None:
    sql, params = _sql(max_gnomad_af=None, max_gnomad_popmax_af=None, impact=["HIGH"], effect=["stop_gained"])
    assert "clinvar_override_terms" not in params
    assert sql == _own("((a.impact IN %(detail_impact_terms)s OR hasAny(a.effects, %(detail_effect_terms)s)))")


# --- The in-silico block ----------------------------------------------------------------


def test_the_override_lifts_the_whole_in_silico_block() -> None:
    # Any one predictor keeps a transcript; the override keeps a pathogenic allele none does.
    sql, params = _sql(
        clinvar_pathogenic_override=True,
        max_gnomad_af=None,
        max_gnomad_popmax_af=None,
        min_cadd=20,
        min_revel=0.5,
        sift="deleterious",
        polyphen="probably_damaging",
    )
    in_silico = (
        "((ifNull(a.cadd_phred, -1) >= toFloat32(%(detail_min_cadd)s)"
        " OR ifNull(a.revel, -1) >= toFloat32(%(detail_min_revel)s)"
        " OR match(a.sift, %(detail_sift_pattern)s)"
        " OR match(a.polyphen, %(detail_polyphen_pattern)s)))"
    )
    assert sql == f"({_own(in_silico)} OR {_PATHOGENIC})"
    assert params["clinvar_override_terms"] == ["pathogenic", "likely pathogenic"]


def test_the_spliceai_minimum_alone_is_lifted_in_both_blocks() -> None:
    # The SpliceAI minimum passes the consequence block and is one of the predictors: the
    # override lifts it in each.
    sql, _params = _sql(
        clinvar_pathogenic_override=True, max_gnomad_af=None, max_gnomad_popmax_af=None, min_spliceai=0.2
    )
    spliceai = "(ifNull(a.spliceai_max, -1) >= toFloat32(%(detail_min_spliceai)s))"
    assert sql == f"({_own(f'({spliceai} AND {spliceai})')} OR {_PATHOGENIC})"


def test_the_in_silico_block_is_strict_without_the_override() -> None:
    sql, params = _sql(max_gnomad_af=None, max_gnomad_popmax_af=None, min_cadd=20)
    assert sql == _own("((ifNull(a.cadd_phred, -1) >= toFloat32(%(detail_min_cadd)s)))")
    assert "clinvar_override_terms" not in params


# --- What the override leaves strict -----------------------------------------------------


def test_the_override_leaves_the_transcript_flags_and_the_clinvar_statuses_strict() -> None:
    sql, params = _sql(
        clinvar_pathogenic_override=True,
        impact=["HIGH"],
        min_cadd=20,
        clinvar=["Pathogenic"],
        canonical_only=True,
        mane_only=True,
        lof_only=True,
    )
    lifted, statuses = sql.split(" AND arrayExists(k -> k IN (SELECT allele_key FROM ", 1)
    # A pathogenic allele: the transcript flags still hold on one of its transcripts.
    flags = "(a.canonical AND (a.mane_select OR a.mane_plus_clinical) AND a.lof NOT IN ('', '.', 'na', 'n/a'))"
    assert lifted.endswith(f" OR ({_PATHOGENIC} AND {_own(flags)}))")
    # The ClinVar statuses: a clause of their own, on the release, never lifted.
    assert "%(clinvar_status_terms)s" in statuses and params["clinvar_status_terms"] == ["pathogenic"]


def test_the_transcript_a_variant_names_reads_every_filter_strictly() -> None:
    # The transcript a variant names as the one its filters held on (``_filter_transcript_
    # matches``) meets the consequence, frequency and in-silico filters itself: a variant the
    # override alone keeps names none.
    filters = SmallVariantQueryFilters(
        page=1,
        page_size=100,
        impact=["HIGH"],
        min_revel=0.5,
        max_gnomad_af=0.01,
        clinvar_pathogenic_override=True,
        clinvar_coga_release=_RELEASE,
    )
    params: dict[str, object] = {}
    condition = _small_annotation_filter_condition(filters, params=params)
    assert condition == (
        "((a.impact IN %(detail_impact_terms)s)"
        " AND ifNull(a.gnomad_af, 0) <= toFloat32(%(detail_max_gnomad_af)s)"
        " AND (ifNull(a.revel, -1) >= toFloat32(%(detail_min_revel)s)))"
    )
    assert not any(name.startswith("clinvar_") for name in params)


# --- The ClinVar terms ------------------------------------------------------------------


def test_clinvar_aggregate_significance_is_split_into_its_terms() -> None:
    # ClinVar's own CLNSIG aggregate forms.
    assert _status_terms("Pathogenic/Likely_pathogenic") == {"pathogenic", "likely pathogenic"}
    assert _status_terms("Benign/Likely_benign") == {"benign", "likely benign"}
    # VEP's CLIN_SIG joins with "&"; split the same way.
    assert _status_terms("pathogenic&likely_pathogenic") == {"pathogenic", "likely pathogenic"}


def test_aggregate_pathogenic_matches_a_pathogenic_filter_and_the_override() -> None:
    # The terms the release stores for ClinVar's aggregate form meet a Pathogenic filter and the
    # override's terms, which the SQL compares them with (hasAny(classification_terms, …)).
    stored = set(stored_allele("1_100_G_A", "Pathogenic/Likely_pathogenic").classification_terms)
    assert not stored.isdisjoint(_status_filter_terms(["Pathogenic"]))
    assert not stored.isdisjoint(_status_filter_terms(list(CLINVAR_OVERRIDE_TERMS)))


def test_benign_and_conflicting_classifications_are_not_overridden() -> None:
    override_terms = set(_status_filter_terms(list(CLINVAR_OVERRIDE_TERMS)))
    for classification in (
        "Benign/Likely_benign",
        "Conflicting_classifications_of_pathogenicity",
        "Uncertain_significance",
    ):
        stored = set(stored_allele("1_100_G_A", classification).classification_terms)
        assert stored.isdisjoint(override_terms), classification
