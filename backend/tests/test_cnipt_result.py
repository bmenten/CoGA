"""The conventional NIPT's automatic result (``cnipt_result``): the rule the owner chose on
2026-11-19, "z-score and fetal-fraction concordance", per target (each autosome, the sex
chromosomes, segmental CNVs).

* A segment at |z| ≥ the call bound, concordant with the fetal fraction (implied fetal fraction
  within the band, ccnv not flagging it discordant, origin not maternal or mosaic) is high risk;
  one that is not concordant, or in the grey zone, inconclusive.
* A segment spanning half its chromosome is read as an aneuploidy of it; a shorter one as a
  segmental CNV, assessed from the smallest size (and not at all, or from 35 Mb, where ccnv's
  limits of detection say the fetal fraction cannot show it).
* The sex chromosomes follow ccnv's sex-chromosome call and its confidence.
* Below the fetal-fraction bound, or with a bound set without its error value, there is no result.
* Each bound is QA's where set, else the provisional value, and the result says which.

All values are synthetic.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.app.services.ccnv_output import read_ccnv_qc
from backend.app.services.cnipt_result import (
    CLAIMED_TARGETS,
    CcnvSegment,
    evaluate_cnipt,
    resolve_cnipt_bounds,
)
from backend.tests._ccnv_output import qc_payload

FF = 0.10
CHROM_MB = 50  # each synthetic chromosome's segmented length


def _qc(**prenatal: Any):
    payload = qc_payload("CFDNA1", **{"effective_ff": FF, **prenatal})
    return read_ccnv_qc(json.dumps(payload).encode(), file="CFDNA1_qc.json")


def _segment(chrom: str, start_mb: float, end_mb: float, **details: Any) -> CcnvSegment:
    ratio = details.pop("ratio", 0.0)
    return CcnvSegment(chrom=chrom, start=int(start_mb * 1e6), end=int(end_mb * 1e6), ratio=ratio, details=details)


def _genome(*events: CcnvSegment) -> list[CcnvSegment]:
    """A neutral segment over each chromosome (1–22, X) a synthetic event does not cover, cut
    around the events."""
    segments = list(events)
    for chrom in [*(str(n) for n in range(1, 23)), "X"]:
        mine = sorted((e for e in events if e.chrom == chrom), key=lambda e: e.start)
        cursor = 0
        for event in mine:
            if event.start > cursor:
                segments.append(CcnvSegment(chrom, cursor, event.start, 0.001, {"zscore": 0.2}))
            cursor = event.end
        if cursor < CHROM_MB * 1_000_000:
            segments.append(CcnvSegment(chrom, cursor, CHROM_MB * 1_000_000, 0.001, {"zscore": 0.2}))
    return segments


TRISOMY = {"ratio": 0.07, "zscore": 9.0, "call": "gain", "implied_ff": 0.098, "discordant": False, "origin_call": "FETAL_CONSTITUTIONAL"}


def _targets(result: dict) -> dict[str, tuple[str, list[str]]]:
    return {target["target"]: (target["verdict"], target["reasons"]) for target in result["targets"]}


def test_a_whole_chromosome_gain_concordant_with_the_fetal_fraction_is_high_risk() -> None:
    result = evaluate_cnipt(_qc(), _genome(_segment("21", 0, CHROM_MB, **TRISOMY)), resolve_cnipt_bounds({}))

    targets = _targets(result)
    assert result["status"] == "result"
    assert targets["21"] == ("high_risk", ["|z| 9.0 ≥ 3, concordant with the fetal fraction, origin FETAL_CONSTITUTIONAL"])
    assert targets["18"][0] == targets["13"][0] == "low_risk"
    assert targets["sex_chromosomes"] == ("low_risk", ["ccnv calls XX (confidence 99.8%)"])
    assert targets["segmental"] == ("low_risk", ["Segmental CNVs are assessed from 3 Mb."])
    (finding,) = result["findings"]
    assert (finding["chrom"], finding["kind"], finding["verdict"], finding["implied_ff_ratio"]) == ("21", "chromosome", "high_risk", 0.98)


@pytest.mark.parametrize(
    ("details", "reason"),
    [
        ({"discordant": True, "discordance_reason": "SUB_FETAL_RATIO"}, "ccnv flags it discordant with the fetal fraction (SUB_FETAL_RATIO)"),
        ({"implied_ff": 0.03}, "its implied fetal fraction (3.0%) is below 0.5 × the fetal fraction (sub-fetal)"),
        ({"implied_ff": 0.4}, "its implied fetal fraction (40.0%) is above 1.5 × the fetal fraction (super-fetal)"),
        ({"origin_call": "MATERNAL_MOSAIC"}, "its origin is MATERNAL_MOSAIC"),
        ({"origin_call": "PLACENTAL_MOSAIC"}, "its origin is PLACENTAL_MOSAIC"),
    ],
)
def test_a_call_that_is_not_concordant_with_the_fetal_fraction_is_inconclusive(details: dict, reason: str) -> None:
    result = evaluate_cnipt(_qc(), _genome(_segment("18", 0, 40, **{**TRISOMY, **details})), resolve_cnipt_bounds({}))

    assert _targets(result)["18"] == ("inconclusive", [reason])


def test_an_origin_ccnv_cannot_tell_does_not_hold_back_a_concordant_call() -> None:
    result = evaluate_cnipt(
        _qc(), _genome(_segment("13", 0, CHROM_MB, **{**TRISOMY, "origin_call": "INDETERMINATE"})), resolve_cnipt_bounds({})
    )
    assert _targets(result)["13"][0] == "high_risk"


def test_the_grey_zone_is_inconclusive_and_below_it_is_no_finding() -> None:
    grey = _segment("21", 0, CHROM_MB, ratio=0.02, zscore=2.7)
    below = _segment("18", 0, CHROM_MB, ratio=0.01, zscore=2.0)
    result = evaluate_cnipt(_qc(), _genome(grey, below), resolve_cnipt_bounds({}))

    targets = _targets(result)
    assert targets["21"] == ("inconclusive", ["|z| 2.7 is in the grey zone (2.5 to 3)"])
    assert targets["18"] == ("low_risk", [])
    assert [finding["chrom"] for finding in result["findings"]] == ["21"]


def test_a_ccnv_call_below_the_grey_zone_is_listed_as_low_risk() -> None:
    called = _segment("21", 0, CHROM_MB, ratio=0.02, zscore=2.2, call="gain")
    result = evaluate_cnipt(_qc(), _genome(called), resolve_cnipt_bounds({}))

    (finding,) = result["findings"]
    assert (finding["verdict"], finding["reasons"]) == ("low_risk", ["ccnv called it, but |z| 2.2 is below 2.5"])
    assert _targets(result)["21"][0] == "low_risk"


def test_a_short_segment_is_a_segmental_cnv_assessed_from_the_smallest_size() -> None:
    deletion = _segment("4", 10, 20, ratio=-0.07, zscore=-6.0, call="loss", implied_ff=0.1, discordant=False)
    tiny = _segment("6", 30, 30.2, ratio=-0.6, zscore=-5.0, call="loss", implied_ff=0.6, discordant=True)
    result = evaluate_cnipt(_qc(), _genome(deletion, tiny), resolve_cnipt_bounds({}))

    targets = _targets(result)
    # A 10 Mb loss on a 50 Mb chromosome is segmental, not chromosome 4's aneuploidy.
    assert targets["4"] == ("low_risk", [])
    assert targets["segmental"] == ("high_risk", ["chr4:10000000-20000000: |z| 6.0 ≥ 3, concordant with the fetal fraction"])
    tiny_finding = next(finding for finding in result["findings"] if finding["chrom"] == "6")
    assert (tiny_finding["verdict"], tiny_finding["reasons"]) == ("not_assessed", ["smaller than the 3 Mb segmental CNVs are assessed from"])


@pytest.mark.parametrize(
    ("lods", "from_mb", "segmental"),
    [
        # Assessed from 35 Mb: the 10 Mb loss is not assessed, and the low risk says so.
        (
            {"lod_ff_3mb": 0.12, "lod_ff_35mb": 0.03},
            35.0,
            (
                "low_risk",
                [
                    "Segmental CNVs are assessed from 35 Mb: the fetal fraction is below ccnv's limit of detection of 3 Mb events (12.0%).",
                    "chr4:10000000-20000000: smaller than the 35 Mb segmental CNVs are assessed from",
                ],
            ),
        ),
        (
            {"lod_ff_3mb": 0.12, "lod_ff_35mb": 0.11},
            None,
            (
                "not_assessed",
                ["Segmental CNVs are not assessed: the fetal fraction is below ccnv's limit of detection of arm-level events (11.0%)."],
            ),
        ),
        (
            {"lod_ff_3mb": 0.08, "lod_ff_35mb": 0.03},
            3.0,
            ("high_risk", ["chr4:10000000-20000000: |z| 6.0 ≥ 3, concordant with the fetal fraction"]),
        ),
    ],
)
def test_ccnvs_limits_of_detection_bound_the_segmental_cnvs_assessed(lods: dict, from_mb: float | None, segmental: tuple) -> None:
    deletion = _segment("4", 10, 20, ratio=-0.07, zscore=-6.0, call="loss", implied_ff=0.1)
    result = evaluate_cnipt(_qc(**lods), _genome(deletion), resolve_cnipt_bounds({}))

    assert result["limits_of_detection"]["segmental_assessed_from_mb"] == from_mb
    assert _targets(result)["segmental"] == segmental


@pytest.mark.parametrize(
    ("sca", "verdict", "reason"),
    [
        ({"call": "XO", "is_aneuploid": True, "confidence": 0.995}, "high_risk", "ccnv calls XO (confidence 99.5%)"),
        ({"call": "XXY", "is_aneuploid": True, "confidence": 0.95}, "inconclusive", "ccnv calls XXY at a confidence of 95.0%, below 99.0%"),
        ({"call": "DISCORDANT_MIXED", "is_aneuploid": True, "confidence": 0.999}, "inconclusive", "ccnv's sex-chromosome signals disagree (DISCORDANT_MIXED)"),
        ({"call": "XY", "is_aneuploid": False, "confidence": 0.999}, "low_risk", "ccnv calls XY (confidence 99.9%)"),
    ],
)
def test_the_sex_chromosomes_follow_ccnvs_call_and_its_confidence(sca: dict, verdict: str, reason: str) -> None:
    sex = "M" if "Y" in sca["call"] else "F"
    result = evaluate_cnipt(_qc(sca=sca, fetal_sex=sex), _genome(), resolve_cnipt_bounds({}))

    assert _targets(result)["sex_chromosomes"] == (verdict, [reason])


def test_each_target_says_whether_the_intended_purpose_claims_it() -> None:
    # TF-01 §3.7: the trisomies 21, 18 and 13 and the sex-chromosome aneuploidies. The other
    # autosomes and the segmental CNVs are shown outside the claim, with or without a result.
    assert CLAIMED_TARGETS == {"21", "18", "13", "sex_chromosomes"}
    for result in (
        evaluate_cnipt(_qc(), _genome(_segment("21", 0, CHROM_MB, **TRISOMY)), resolve_cnipt_bounds({})),
        evaluate_cnipt(_qc(effective_ff=0.03), _genome(), resolve_cnipt_bounds({})),
    ):
        claimed = {target["target"] for target in result["targets"] if target["claimed"]}
        assert claimed == {"21", "18", "13", "sex_chromosomes"}
        assert len(result["targets"]) == 24


def test_below_the_fetal_fraction_bound_there_is_no_result() -> None:
    result = evaluate_cnipt(_qc(effective_ff=0.03), _genome(_segment("21", 0, CHROM_MB, **TRISOMY)), resolve_cnipt_bounds({}))

    assert result["status"] == "no_result"
    assert result["no_result_reasons"] == ["the fetal fraction (3.0%) is below 4.0%"]
    assert {target["verdict"] for target in result["targets"]} == {"no_result"}
    assert result["findings"] == []


def test_qa_bounds_replace_the_provisional_ones_and_the_result_says_which_applied() -> None:
    bounds = resolve_cnipt_bounds(
        {"cnipt.fetal_fraction": {"warn_value": 0.12, "error_value": 0.05}, "cnipt.call_zscore": {"warn_value": None, "error_value": 10.0}}
    )
    result = evaluate_cnipt(_qc(), _genome(_segment("21", 0, CHROM_MB, **TRISOMY)), bounds)

    applied = {bound["key"]: (bound["warn_value"], bound["error_value"], bound["provisional"]) for bound in result["bounds"]}
    assert applied["cnipt.fetal_fraction"] == (0.12, 0.05, False)
    assert applied["cnipt.call_zscore"] == (None, 10.0, False)
    assert applied["cnipt.sca_confidence"] == (None, 0.99, True)
    # |z| 9 is below QA's 10, and without a warning bound there is no grey zone.
    assert _targets(result)["21"] == ("low_risk", ["ccnv called it, but |z| 9.0 is below 10"])
    assert "the fetal fraction (10.0%) is below the warning bound of 12.0%" in result["warnings"]


def test_a_bound_qa_set_without_its_error_value_gives_no_result() -> None:
    bounds = resolve_cnipt_bounds({"cnipt.implied_ff_ratio_min": {"warn_value": 0.6, "error_value": None}})
    result = evaluate_cnipt(_qc(), _genome(), bounds)

    assert result["status"] == "no_result"
    assert result["no_result_reasons"] == ["the bound cnipt.implied_ff_ratio_min has no error value: it is set without one"]


def test_a_chromosome_ccnv_gives_no_segment_of_has_no_result() -> None:
    segments = [segment for segment in _genome() if segment.chrom != "22"]
    result = evaluate_cnipt(_qc(), segments, resolve_cnipt_bounds({}))

    assert _targets(result)["22"] == ("no_result", ["ccnv gives no segment of chromosome 22"])


def test_warnings_name_a_twin_pregnancy_and_ccnvs_own_warnings() -> None:
    payload = qc_payload("CFDNA1", effective_ff=FF, gestation="twin", low_ff_warning=True, warnings=["WARN_EXAMPLE"])
    payload["fragmentomics"]["warnings"] = ["WARN_HIGH_LEUKOCYTE_DNA"]
    qc = read_ccnv_qc(json.dumps(payload).encode(), file="q.json")

    result = evaluate_cnipt(qc, _genome(), resolve_cnipt_bounds({}))

    assert result["gestation"] == "twin"
    assert result["warnings"] == [
        "ccnv warns of a low fetal fraction",
        "ccnv: WARN_EXAMPLE",
        "ccnv fragmentomics: WARN_HIGH_LEUKOCYTE_DNA",
        "ccnv reads a twin pregnancy: an aneuploidy of one fetus lies at about half the fetal fraction",
    ]


def test_the_result_is_the_same_for_the_same_input() -> None:
    segments = _genome(_segment("21", 0, CHROM_MB, **TRISOMY))
    first = evaluate_cnipt(_qc(), segments, resolve_cnipt_bounds({}))
    again = evaluate_cnipt(_qc(), list(reversed(segments)), resolve_cnipt_bounds({}))
    assert json.dumps(first, sort_keys=True) == json.dumps(again, sort_keys=True)
