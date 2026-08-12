"""Repeat loci that are pathogenic by contraction rather than expansion.

Almost every catalogued locus is pathogenic by expansion, where "at or above the
threshold" is the whole rule. Two are not — VWA1 (normal exactly 2, pathogenic 1 or 3)
and MIR7-2 (normal 4, pathogenic 3) — and a bare ``>=`` test called every healthy call
at them pathogenic.
"""

import pytest

from backend.app.services.repeat_expansion_pg import (
    _reclassify_repeat_alleles,
    classify_repeat_count,
)

# Straight from STRchive-loci.json.
VWA1 = {"warning_min": None, "pathogenic_min": 1, "benign_min": 2, "benign_max": 2, "pathogenic_max": 3}
MIR7_2 = {"warning_min": None, "pathogenic_min": 3, "benign_min": 4, "benign_max": 4, "pathogenic_max": 3}
HTT = {"warning_min": 27, "pathogenic_min": 36, "benign_min": 6, "benign_max": 26, "pathogenic_max": 250}


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

    def test_vwa1_count_outside_every_stated_range_is_unknown(self) -> None:
        # Not "normal": at a locus where risk does not rise with count, being further
        # from the threshold proves nothing, and the catalog does not cover this.
        assert classify(5, VWA1) == "unknown"

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

    def test_an_uncatalogued_locus_is_normal_and_a_no_call_is_unknown(self) -> None:
        assert classify_repeat_count(12, None, None) == "normal"
        assert classify_repeat_count(None, 27, 36) == "unknown"


class TestReclassification:
    def test_stored_alleles_are_re_read_against_the_ranges(self) -> None:
        # Rows written before the ranges were consulted carry the old status; the read
        # path recomputes it.
        alleles = [
            {"repeat_count": 2, "status": "pathogenic"},
            {"repeat_count": 3, "status": "pathogenic"},
        ]
        result = _reclassify_repeat_alleles(
            alleles,
            warning_min=None,
            pathogenic_min=1,
            benign_min=2,
            benign_max=2,
            pathogenic_max=3,
        )
        assert [allele["status"] for allele in result] == ["normal", "pathogenic"]

    def test_ranges_alone_are_enough_to_reclassify(self) -> None:
        # warning_min and pathogenic_min may both be absent; the benign range still
        # decides, so the guard cannot key on the thresholds only.
        result = _reclassify_repeat_alleles(
            [{"repeat_count": 4, "status": "pathogenic"}],
            warning_min=None,
            pathogenic_min=None,
            benign_min=4,
            benign_max=4,
            pathogenic_max=None,
        )
        assert result[0]["status"] == "normal"

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
