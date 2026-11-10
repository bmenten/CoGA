"""Repeat loci that are pathogenic by contraction rather than expansion.

Almost every catalogued locus is pathogenic by expansion, where "at or above the
threshold" is the whole rule. Two are not — VWA1 (normal exactly 2, pathogenic 1 or 3)
and MIR7-2 (normal 4, pathogenic 3) — and a bare ``>=`` test called every healthy call
at them pathogenic.
"""

import pytest

from backend.app.services import repeat_expansion_pg
from backend.app.services.repeat_expansion_pg import (
    REPEAT_STATUS_RANK,
    _reclassify_repeat_alleles,
    classify_repeat_count,
    summarize_repeat_status,
)
from backend.tests._repeat_fakes import RowsSession, family_context

# Straight from STRchive-loci.json.
VWA1 = {"warning_min": None, "pathogenic_min": 1, "benign_min": 2, "benign_max": 2, "pathogenic_max": 3}
MIR7_2 = {"warning_min": None, "pathogenic_min": 3, "benign_min": 4, "benign_max": 4, "pathogenic_max": 3}
HTT = {"warning_min": 27, "pathogenic_min": 36, "benign_min": 6, "benign_max": 26, "pathogenic_max": 250}
# Monotonic loci whose benign range shares its upper endpoint with the grey zone.
SCA8_ATXN8OS = {"warning_min": 50, "pathogenic_min": 71, "benign_min": 15, "benign_max": 50, "pathogenic_max": 1300}
CANVAS_RFC1 = {"warning_min": 11, "pathogenic_min": 400, "benign_min": 0, "benign_max": 11, "pathogenic_max": 2750}


def classify(count, locus):
    return classify_repeat_count(
        count,
        locus["warning_min"],
        locus["pathogenic_min"],
        benign_min=locus["benign_min"],
        benign_max=locus["benign_max"],
        pathogenic_max=locus["pathogenic_max"],
    )


class TestContractionLoci:
    @pytest.mark.parametrize("count", [2])
    def test_vwa1_normal_count_is_not_flagged(self, count: int) -> None:
        # pathogenic_min is 1, so a ">= pathogenic_min" rule called this pathogenic.
        assert classify(count, VWA1) == "normal"

    @pytest.mark.parametrize("count", [1, 3])
    def test_vwa1_deviation_either_side_is_pathogenic(self, count: int) -> None:
        # 1 is the contraction, 3 the recurrent c.62_71dup.
        assert classify(count, VWA1) == "pathogenic"

    def test_vwa1_count_outside_every_stated_range_is_review(self) -> None:
        # Not "normal": at a locus where risk does not rise with count, being further
        # from the threshold proves nothing, and the catalog does not cover this. It is
        # "review" rather than "unknown", so it outranks a normal allele.
        assert classify(5, VWA1) == "review"

    def test_mir7_2_normal_is_not_flagged_and_the_deletion_is(self) -> None:
        assert classify(4, MIR7_2) == "normal"
        assert classify(3, MIR7_2) == "pathogenic"


class TestExpansionLociUnchanged:
    @pytest.mark.parametrize(
        ("count", "expected"),
        [(20, "normal"), (26, "normal"), (30, "intermediate"), (36, "pathogenic"), (45, "pathogenic")],
    )
    def test_htt_thresholds_still_apply(self, count: int, expected: str) -> None:
        assert classify(count, HTT) == expected

    def test_a_count_beyond_pathogenic_max_stays_pathogenic(self) -> None:
        # HTT records pathogenic_max 250 as the largest count observed, not a ceiling on
        # harm. Treating it as a bound would downgrade a juvenile-onset allele.
        assert classify(300, HTT) == "pathogenic"
        assert classify(1000, HTT) == "pathogenic"

    def test_a_locus_without_ranges_uses_the_thresholds_alone(self) -> None:
        assert classify_repeat_count(40, 27, 36) == "pathogenic"
        assert classify_repeat_count(30, 27, 36) == "intermediate"
        assert classify_repeat_count(10, 27, 36) == "normal"

    def test_an_uncatalogued_locus_is_not_assessed_and_a_no_call_is_unknown(self) -> None:
        # Not "normal", so an expansion there can be seen (CLIN-26,
        # test_repeat_not_assessed.py).
        assert classify_repeat_count(12, None, None) == "not_assessed"
        assert classify_repeat_count(None, 27, 36) == "unknown"
        assert classify_repeat_count(None, None, None) == "unknown"


class TestReclassification:
    def test_stored_alleles_are_read_against_the_ranges(self) -> None:
        # Stored as the import read TRGT's counts (3 pathogenic, 4 outside every range); a
        # correction of -1 QA records since makes them 2 and 3, and the read path classifies
        # those against the ranges, whatever status the alleles were stored with.
        alleles = [
            {"repeat_count": 3, "status": "pathogenic"},
            {"repeat_count": 4, "status": "review"},
        ]
        result = _reclassify_repeat_alleles(
            alleles,
            warning_min=None,
            pathogenic_min=1,
            benign_min=2,
            benign_max=2,
            pathogenic_max=3,
            count_offset=-1,
        )
        assert [allele["status"] for allele in result] == ["normal", "pathogenic"]

    def test_a_benign_range_alone_classifies_only_the_counts_inside_it(self) -> None:
        # Without an intermediate or pathogenic threshold nothing says where harm starts, so
        # a count outside a benign range alone (STRchive's POLG states 10) stays not assessed
        # (CLIN-26), and a count inside it is normal (CLIN-36).
        result = _reclassify_repeat_alleles(
            [
                {"repeat_count": 3, "status": "not_assessed"},
                {"repeat_count": 4, "status": "normal"},
                {"repeat_count": 5, "status": "not_assessed"},
            ],
            warning_min=None,
            pathogenic_min=None,
            benign_min=4,
            benign_max=4,
            pathogenic_max=None,
        )
        assert [allele["status"] for allele in result] == ["not_assessed", "normal", "not_assessed"]

    def test_a_no_call_allele_is_left_alone(self) -> None:
        result = _reclassify_repeat_alleles(
            [{"repeat_count": None, "status": "unknown"}],
            warning_min=None,
            pathogenic_min=1,
            benign_min=2,
            benign_max=2,
            pathogenic_max=3,
        )
        assert result[0]["status"] == "unknown"


class TestReviewStatusSurfaces:
    """A count the catalog cannot classify must never hide behind a normal allele."""

    def test_a_review_allele_outranks_a_normal_one(self) -> None:
        # VWA1 2/5: one normal allele, one outside every catalogued range. Summarised as
        # "review", so the aberrant-only view shows the row.
        assert summarize_repeat_status([classify(2, VWA1), classify(5, VWA1)]) == "review"

    def test_ranking_order(self) -> None:
        order = ["unknown", "normal", "review", "not_assessed", "intermediate", "premutation", "pathogenic"]
        assert sorted(REPEAT_STATUS_RANK, key=REPEAT_STATUS_RANK.get) == order
        assert summarize_repeat_status(["review", "intermediate"]) == "intermediate"
        assert summarize_repeat_status(["review", "pathogenic"]) == "pathogenic"

    def test_a_no_call_still_ranks_lowest(self) -> None:
        # A missing allele (no call) is not a finding and must not flag a row.
        assert summarize_repeat_status(["unknown", "normal"]) == "normal"
        assert summarize_repeat_status(["unknown"]) == "unknown"


class TestSharedBoundaryOnMonotonicLoci:
    """Where the catalog's benign range shares its upper endpoint with the grey zone, the
    monotonic thresholds decide: the ambiguous count reads as intermediate."""

    @pytest.mark.parametrize(
        ("count", "locus", "expected"),
        [
            (49, SCA8_ATXN8OS, "normal"),
            (50, SCA8_ATXN8OS, "intermediate"),
            (71, SCA8_ATXN8OS, "pathogenic"),
            (10, CANVAS_RFC1, "normal"),
            (11, CANVAS_RFC1, "intermediate"),
            (400, CANVAS_RFC1, "pathogenic"),
        ],
    )
    def test_boundary_counts(self, count: int, locus: dict, expected: str) -> None:
        assert classify(count, locus) == expected


@pytest.mark.asyncio
async def test_catalog_join_breaks_ties_deterministically() -> None:
    # A gene can be catalogued twice (built-in ATXN8OS and STRchive SCA8_ATXN8OS); the
    # call's thresholds must not depend on row order.
    session = RowsSession([])

    table = await repeat_expansion_pg.get_family_repeat_expansion_table_response(
        session, context=family_context(("00000000-0000-0000-0000-000000000001", "S1", "proband", True, "female"))
    )

    assert table.loci == []
    [sql] = session.statements
    assert "((repeat_loci.metadata ->> 'benign_max') IS NOT NULL) DESC, repeat_loci.locus_id" in sql
