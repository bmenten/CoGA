"""The PGT embryo call at the region of interest, as the backend makes it (REQ-PGT-005).

The rules are those the haplotype tracks apply in the browser (``haplotypeRisk.ts``): both
answer every case of ``frontend/src/lib/__tests__/fixtures/pgtEmbryoCallCases.json`` alike,
which ``haplotypeRisk.test.ts`` runs too. The embryo calls themselves are made here only:
the family page shows them and a sign-out freezes them. These tests also pin what the calls
rest on: the recombinations near the ROI, the parents' phase corrections near it, and the
embryos a family lists.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backend.app.services import pgt_embryo_calls as calls
from backend.app.services.pgt_embryo_calls import Region

CASES_FILE = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "lib"
    / "__tests__"
    / "fixtures"
    / "pgtEmbryoCallCases.json"
)
CASES: list[dict[str, Any]] = json.loads(CASES_FILE.read_text(encoding="utf-8"))["cases"]


def _region(raw: dict[str, Any]) -> Region:
    return Region(raw.get("chr"), int(raw["start"]), int(raw["end"]))


def _model_json(model: calls.DiseaseModel) -> dict[str, Any]:
    return {
        "mode": model.mode,
        "informative": model.informative,
        "signatures": [
            {"origin": signature.origin, "value": signature.value, "kind": signature.kind}
            for signature in model.signatures
        ],
    }


def _call_json(assessment: calls.Assessment) -> dict[str, Any]:
    return {
        "state": assessment.state,
        "roi_not_covered": assessment.roi_not_covered,
        "sex_dependent": (
            {"if_male": assessment.sex_dependent.if_male, "if_female": assessment.sex_dependent.if_female}
            if assessment.sex_dependent is not None
            else None
        ),
    }


# --- Parity with the browser's rules -------------------------------------------------


def test_the_parity_cases_cover_every_model_and_every_call() -> None:
    assert len(CASES) >= 20
    modes = {case["model"]["mode"] for case in CASES}
    assert modes == {"dominant", "recessive", "x_linked_dominant", "x_linked_recessive"}
    states = {expected["state"] for case in CASES for expected in case["calls"].values()}
    assert states == {"affected_or_at_risk", "carrier", "unaffected_non_carrier", "uninformative"}
    assert any(expected["sex_dependent"] for case in CASES for expected in case["calls"].values())
    assert any(expected["roi_not_covered"] for case in CASES for expected in case["calls"].values())


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_the_backend_answers_each_parity_case_as_the_browser_does(case: dict[str, Any]) -> None:
    region = _region(case["region"])
    members = case["members"]
    model = calls.infer_disease_haplotypes(
        samples=case["samples"],
        members=members,
        inheritance_model=calls.resolve_inheritance_model(case["inheritance_model"], members),
        region=region,
    )
    assert _model_json(model) == case["model"]
    by_sample = {member["sample_id"]: member for member in [*members, *case.get("assessed_only", [])]}
    for sample, expected in case["calls"].items():
        assessment = calls.assess_member(
            model=model, samples=case["samples"], member=by_sample[sample], region=region
        )
        assert _call_json(assessment) == expected, sample


# --- The embryos, and what their calls rest on ----------------------------------------

REGION = Region("1", 1_000_000, 1_001_000)


def _block(hap1: str, hap2: str, lineage: tuple[str, str] = ("paternal", "maternal"), start: int = 0, end: int = 2_000_000) -> dict[str, Any]:
    return {
        "chr": "1",
        "start": start,
        "end": end,
        "hap1": hap1,
        "hap2": hap2,
        "hap1_lineage": lineage[0],
        "hap2_lineage": lineage[1],
        "hemizygous_in_males": False,
    }


DOMINANT_MEMBERS = [
    {"sample_id": "FATHER", "role": "father", "affected": True, "sex": "male"},
    {"sample_id": "PROBAND", "role": "proband", "affected": True, "sex": "female"},
    {"sample_id": "E_RISK", "role": "embryo", "affected": False, "sex": "female"},
    {"sample_id": "E_CLEAR", "role": "embryo", "affected": False, "sex": "female"},
]
DOMINANT_SAMPLES = [
    {"sample": "FATHER", "segments": [_block("0", "1", ("paternal", "paternal"))]},
    {"sample": "PROBAND", "segments": [_block("1", "0")]},
    {"sample": "E_RISK", "segments": [_block("1", "1")]},
    {"sample": "E_CLEAR", "segments": [_block("0", "1")]},
]


def _by_sample(embryos: list[calls.EmbryoCall]) -> dict[str, calls.EmbryoCall]:
    return {embryo.sample: embryo for embryo in embryos}


def test_each_embryo_is_called_against_the_shared_dominant_haplotype() -> None:
    model, embryos = calls.call_embryos(
        members=DOMINANT_MEMBERS, samples=DOMINANT_SAMPLES, inheritance_model="AD", region=REGION
    )
    out = _by_sample(embryos)

    assert [embryo.sample for embryo in embryos] == ["E_RISK", "E_CLEAR"]
    assert [(signature.origin, signature.value, signature.kind) for signature in model.signatures] == [
        ("paternal", "1", "dominant")
    ]
    assert out["E_RISK"].assessment == calls.Assessment("affected_or_at_risk")
    assert out["E_CLEAR"].assessment == calls.Assessment("unaffected_non_carrier")
    # What each call reads: the homologs the embryo carries at the ROI, with their blocks.
    assert out["E_RISK"].inherited == (
        calls.Homolog(calls.Signature("paternal", "1"), 0, 2_000_000),
        calls.Homolog(calls.Signature("maternal", "1"), 0, 2_000_000),
    )
    assert out["E_RISK"].recombinations == ()


def test_an_embryo_without_its_own_haplotype_over_the_roi_is_uninformative_for_missing_data() -> None:
    members = [
        *DOMINANT_MEMBERS,
        {"sample_id": "E_FLANK", "role": "embryo", "affected": False, "sex": "female"},
        {"sample_id": "E_NONE", "role": "embryo", "affected": False, "sex": "female"},
    ]
    samples = [
        *DOMINANT_SAMPLES,
        {
            "sample": "E_FLANK",
            "segments": [_block("0", "1", end=900_000), _block("0", "1", start=1_100_000)],
        },
    ]
    out = _by_sample(calls.call_embryos(members=members, samples=samples, inheritance_model="AD", region=REGION)[1])

    for sample in ("E_FLANK", "E_NONE"):
        assert out[sample].assessment == calls.Assessment("uninformative", roi_not_covered=True)
        assert out[sample].inherited == ()
    assert out["E_CLEAR"].assessment.state == "unaffected_non_carrier"


def test_no_resolved_disease_haplotype_makes_every_embryo_uninformative_for_that_reason() -> None:
    members = [
        {"sample_id": "PROBAND", "role": "proband", "affected": True, "sex": "female"},
        {"sample_id": "E1", "role": "embryo", "affected": False, "sex": "female"},
    ]
    samples = [
        {"sample": "PROBAND", "segments": [_block("1", "1")]},
        {"sample": "E1", "segments": [_block("1", "0")]},
    ]
    model, embryos = calls.call_embryos(members=members, samples=samples, inheritance_model="AD", region=REGION)

    assert model.informative is False
    # The reason is the unresolved disease haplotype, not the embryo's data.
    assert embryos[0].assessment == calls.Assessment("uninformative", roi_not_covered=False)
    # What it carries at the ROI is still read.
    assert len(embryos[0].inherited) == 2


def test_an_x_linked_embryo_of_unrecorded_sex_gets_both_calls() -> None:
    region = Region("X", 1_000_000, 1_001_000)

    def x_block(hap2: str) -> dict[str, Any]:
        return {**_block("?", hap2), "chr": "X", "hemizygous_in_males": True}

    members = [
        {"sample_id": "SON", "role": "proband", "affected": True, "sex": "male"},
        {"sample_id": "E_RISK", "role": "embryo", "affected": False, "sex": "und"},
        {"sample_id": "E_CLEAR", "role": "embryo", "affected": False, "sex": "und"},
    ]
    samples = [
        {"sample": "SON", "segments": [x_block("1")]},
        {"sample": "E_RISK", "segments": [x_block("1")]},
        {"sample": "E_CLEAR", "segments": [x_block("0")]},
    ]
    out = _by_sample(calls.call_embryos(members=members, samples=samples, inheritance_model="XLR", region=region)[1])

    assert out["E_RISK"].assessment == calls.Assessment(
        "affected_or_at_risk", sex_dependent=calls.SexDependentCall("affected_or_at_risk", "carrier")
    )
    assert out["E_CLEAR"].assessment == calls.Assessment("unaffected_non_carrier")


def test_the_model_a_family_records_is_used_and_else_assumed_from_its_carriers() -> None:
    carriers = [{"sample_id": "M", "carrier_status": "carrier"}]
    assert calls.resolve_inheritance_model("XLR", carriers) == "XLR"
    assert calls.resolve_inheritance_model(None, carriers) == "AR"
    assert calls.resolve_inheritance_model("  ", [{"sample_id": "M", "carrier_status": True}]) == "AR"
    assert calls.resolve_inheritance_model("mitochondrial", [{"sample_id": "M"}]) == "AD"
    assert calls.normalize_inheritance("autosomal-dominant") == "dominant"
    assert calls.normalize_inheritance("x linked") == "x_linked_recessive"
    assert calls.normalize_inheritance("XL_DOMINANT") == "x_linked_dominant"
    assert calls.normalize_inheritance(None) == "unknown"


def test_a_model_of_no_mode_calls_no_one_whatever_it_holds() -> None:
    # The inference resolves nothing without a mode; a model made by hand reads the same.
    model = calls.DiseaseModel("unknown", (calls.DiseaseSignature("paternal", "1", "dominant"),), True)
    member = {"sample_id": "E1", "role": "embryo", "sex": "female"}
    samples = [{"sample": "E1", "segments": [_block("1", "0")]}]
    assert calls.assess_member(model=model, samples=samples, member=member, region=REGION) == calls.Assessment(
        "uninformative"
    )
    assert calls.infer_disease_haplotypes(samples=samples, members=[member], inheritance_model="", region=REGION) == (
        calls.DiseaseModel("unknown", (), False)
    )


def test_only_members_recorded_as_embryos_are_called() -> None:
    members = [
        {"sample_id": "E1", "role": "Embryo"},
        {"sample_id": "E2", "role": " embryo "},
        {"sample_id": "S1", "role": "sibling"},
        {"sample_id": "X", "role": None},
    ]
    assert [member["sample_id"] for member in members if calls.is_embryo(member)] == ["E1"]


# --- Recombination near the ROI ---------------------------------------------------------


def test_a_block_boundary_inside_the_roi_is_a_recombination_near_it() -> None:
    segments = [
        _block("1", "1", end=1_000_500),
        _block("0", "1", start=1_000_500),
    ]
    assert calls.recombinations_near(segments, REGION) == [1_000_500]


def test_a_boundary_far_from_the_roi_is_not() -> None:
    segments = [_block("1", "1", end=500_000), _block("0", "1", start=500_000)]
    assert calls.recombinations_near(segments, REGION) == []


def test_a_boundary_within_the_flank_counts_and_one_just_past_it_does_not() -> None:
    edge = REGION.end + calls.ROI_RECOMBINATION_FLANK
    assert calls.recombinations_near([_block("1", "1", end=edge), _block("0", "1", start=edge)], REGION) == [edge]
    past = edge + 1
    assert calls.recombinations_near([_block("1", "1", end=past), _block("0", "1", start=past)], REGION) == []


def test_a_lineage_change_is_a_recombination_and_an_unchanged_neighbour_is_not() -> None:
    lineage = [
        _block("0", "1", end=1_000_200),
        _block("0", "1", ("untransmitted", "maternal"), start=1_000_200, end=1_000_400),
        _block("0", "1", ("untransmitted", "maternal"), start=1_000_400),
    ]
    assert calls.recombinations_near(lineage, REGION) == [1_000_200]


def test_recombinations_are_read_on_the_roi_chromosome_in_position_order() -> None:
    other = {**_block("1", "0", start=1_000_100), "chr": "2"}
    unsorted = [_block("0", "1", start=1_000_300), other, _block("1", "1", end=1_000_300)]
    assert calls.recombinations_near(unsorted, REGION) == [1_000_300]
    # A block without a chromosome is read as on it.
    assert calls.recombinations_near([{**_block("1", "1", end=1_000_300), "chr": None}, _block("0", "1", start=1_000_300)], REGION) == [1_000_300]


# --- The parents' phase corrections near the ROI ------------------------------------------

CORRECTION = {
    "parent": "FATHER1",
    "side": "father",
    "chr": "7",
    "position": 30_000_000,
    "end": 30_400_000,
    "children_switching": 5,
    "children": 5,
}


def test_the_corrections_whose_switch_lies_within_the_flank_of_the_roi_are_found() -> None:
    near = Region("chr7", 30_500_000, 30_600_000)
    assert calls.phase_corrections_near([CORRECTION], near) == [CORRECTION]
    assert calls.phase_corrections_near([CORRECTION], Region("7", 31_000_000, 31_100_000)) == []
    # The phase may have switched up to 2 Mb before the correction: a ROI there is near it too.
    assert calls.phase_corrections_near([CORRECTION], Region("7", 28_000_000, 28_100_000)) == [CORRECTION]
    assert calls.phase_corrections_near([CORRECTION], Region("7", 27_000_000, 27_100_000)) == []
    assert calls.phase_corrections_near([CORRECTION], Region("8", 30_500_000, 30_600_000)) == []
    assert calls.phase_corrections_near([CORRECTION], Region(None, 30_500_000, 30_600_000)) == []


def test_a_correction_is_read_as_the_family_metadata_holds_it_or_left_out() -> None:
    assert calls.phase_correction({"parent": "M", "side": "mother", "chr": "7", "position": 5}) == {
        "parent": "M",
        "side": "mother",
        "chr": "7",
        "position": 5,
        "end": 5,
        "children_switching": 0,
        "children": 0,
    }
    assert calls.phase_correction({**CORRECTION, "side": "anything"})["side"] == "father"
    assert calls.phase_correction({"parent": "M", "chr": "7"}) is None
    assert calls.phase_correction({"parent": "M", "chr": "7", "position": True}) is None
    assert calls.phase_correction({"parent": 1, "chr": "7", "position": 5}) is None
    assert calls.phase_corrections_near([{"parent": "M"}], Region("7", 1, 2)) == []
