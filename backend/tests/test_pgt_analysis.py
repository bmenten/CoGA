"""The PGT analysis the family page shows and a sign-out freezes (REQ-PGT-005).

``pgt_analysis.family_pgt_analysis`` reads the family's ROI, inheritance model and members,
the haplotype blocks over the ROI and 1 Mb on each side as the haplotype endpoint serves
them, and each child's phasing QC over that same window as the ROI marker review counts it,
the markers left and right of the ROI apart; it calls the
embryos (``pgt_embryo_calls``) and names its inputs. The reads are stubbed here; the real
ones run in ``e2e/test_e2e_signed_pgt_analysis.py``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest

from backend.app.schemas import (
    HaplotypeResponse,
    PhasedMarker,
    PhasedMarkerResponse,
    PhasedMarkerSample,
    PhasedSampleQc,
)
from backend.app.services import pgt_analysis, phased_marker_service
from backend.app.services.family_metadata_context import FamilyMetadataContext


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> "_Result":
        return self

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class _Session:
    """Answers each statement by a fragment of its SQL."""

    def __init__(self, answers: dict[str, list[dict[str, Any]]]) -> None:
        self.answers = answers
        self.executed: list[tuple[str, Any]] = []

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        sql = str(statement)
        self.executed.append((sql, params))
        for fragment, rows in self.answers.items():
            if fragment in sql:
                return _Result(rows)
        return _Result([])


MEMBERS = [
    {"sample_id": "E_RISK", "role": "embryo", "sex": "female", "affected": False, "clinical_status": "unknown",
     "carrier_status": "unknown", "carrier_type": None},
    {"sample_id": "FATHER", "role": "father", "sex": "male", "affected": True, "clinical_status": "affected",
     "carrier_status": "unknown", "carrier_type": None},
    {"sample_id": "E_CLEAR", "role": "embryo", "sex": "und", "affected": False, "clinical_status": "unknown",
     "carrier_status": "unknown", "carrier_type": None},
    {"sample_id": "MOTHER", "role": "mother", "sex": "female", "affected": False, "clinical_status": "unaffected",
     "carrier_status": "unknown", "carrier_type": None},
    {"sample_id": "INDEX", "role": "proband", "sex": "female", "affected": True, "clinical_status": "affected",
     "carrier_status": "unknown", "carrier_type": None},
]

ROI = {"roi_query": "GENE1", "roi_label": "GENE1", "roi_source": "gene", "roi_chr": "7", "roi_start": 30_500_000,
       "roi_end": 30_600_000}


def _context(**overrides: Any) -> FamilyMetadataContext:
    values: dict[str, Any] = {
        "family_uuid": "11111111-1111-1111-1111-111111111111",
        "family_id": "FAM_PGT",
        "project_ids": ["p1"],
        "sample_rows": MEMBERS,
        "sample_uuid_to_name": {f"u-{row['sample_id']}": row["sample_id"] for row in MEMBERS},
        "sample_name_to_uuid": {row["sample_id"]: f"u-{row['sample_id']}" for row in MEMBERS},
        "affected_sample_names": ["FATHER", "INDEX"],
        "assembly_id": "a1",
        "assembly_name": "GRCh38",
        "phase_corrections": [
            {"parent": "FATHER", "side": "father", "chr": "7", "position": 30_400_000, "end": 30_450_000,
             "children_switching": 3, "children": 3},
            {"parent": "MOTHER", "side": "mother", "chr": "7", "position": 60_000_000, "end": 60_100_000,
             "children_switching": 3, "children": 3},
        ],
    }
    values.update(overrides)
    return FamilyMetadataContext(**values)


def _block(hap1: str, hap2: str, lineage: tuple[str, str] = ("paternal", "maternal"), start: int = 29_000_000,
           end: int = 32_000_000) -> dict[str, Any]:
    return {"chr": "7", "start": start, "end": end, "hap1": hap1, "hap2": hap2, "ps": None,
            "hap1_lineage": lineage[0], "hap2_lineage": lineage[1], "hemizygous_in_males": False}


def _haplotypes(order: str = "as stored") -> HaplotypeResponse:
    samples = [
        {"sample": "E_CLEAR", "segments": [_block("0", "1")]},
        {"sample": "E_RISK", "segments": [
            _block("1", "1", end=30_550_000),
            _block("1", "0", start=30_550_000),
        ]},
        {"sample": "FATHER", "segments": [_block("0", "1", ("paternal", "paternal"))]},
        {"sample": "INDEX", "segments": [_block("1", "0")]},
        {"sample": "MOTHER", "segments": [_block("0", "1", ("maternal", "maternal"))]},
    ]
    if order == "reversed":
        samples = [{**sample, "segments": list(reversed(sample["segments"]))} for sample in samples]
    return HaplotypeResponse.model_validate({"chr": "7", "start": 29_500_000, "end": 31_600_000, "samples": samples})


def _markers(*, truncated: bool = False) -> PhasedMarkerResponse:
    if truncated:
        return PhasedMarkerResponse(chr="7", start=29_500_000, end=31_600_000, truncated=True, covered=[1, 2])
    return PhasedMarkerResponse(
        chr="7",
        start=29_500_000,
        end=31_600_000,
        samples=[
            PhasedMarkerSample(sample="FATHER", reference=True),
            PhasedMarkerSample(
                sample="E_RISK",
                # One paternal marker left of the ROI, one inside it, one right of it.
                markers=[
                    PhasedMarker(pos=30_400_000, hap1=1, hap2=None),
                    PhasedMarker(pos=30_510_000, hap1=1, hap2=0),
                    PhasedMarker(pos=30_700_000, hap1=1, hap2=None),
                ],
                qc=PhasedSampleQc(informative_sites=40, mendel_errors=1, mendel_rate=0.025),
            ),
            PhasedMarkerSample(
                sample="E_CLEAR",
                markers=[PhasedMarker(pos=30_510_000, hap1=None, hap2=1)],
                qc=PhasedSampleQc(informative_sites=38, mendel_errors=0, mendel_rate=0.0),
            ),
            PhasedMarkerSample(sample="INDEX", qc=PhasedSampleQc(informative_sites=39)),
        ],
    )


SOURCES = [
    {"sample": "MOTHER", "source": "glimpse2", "filename": "family.vcf.gz", "row_count": 12,
     "uploaded_at": datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)},
    {"sample": "E_RISK", "source": "glimpse2", "filename": "family.vcf.gz", "row_count": 30,
     "uploaded_at": datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)},
    # A sample that is no longer a member of the family is not an input of its calls.
    {"sample": "REMOVED", "source": "glimpse2", "filename": "family.vcf.gz", "row_count": 9, "uploaded_at": None},
]


def _patch_reads(monkeypatch, *, haplotypes: HaplotypeResponse | None = None, markers: PhasedMarkerResponse | None = None) -> dict[str, Any]:
    asked: dict[str, Any] = {}

    async def _haplotype_read(session, *, context, chr, start=None, end=None):
        asked["haplotypes"] = (chr, start, end)
        return haplotypes if haplotypes is not None else _haplotypes()

    async def _marker_read(context, *, chr, start, end):
        asked["markers"] = (chr, start, end)
        return markers if markers is not None else _markers()

    monkeypatch.setattr(pgt_analysis, "get_family_haplotypes_response", _haplotype_read)
    monkeypatch.setattr(pgt_analysis, "get_family_phased_markers_response", _marker_read)
    # The blocks were built for the family's pedigree as it is: test_outdated_haplotype_blocks.py
    # covers blocks built for another (CLIN-66).
    monkeypatch.setattr(pgt_analysis, "blocks_outdated", lambda context, rows: False)
    return asked


def _session(roi: dict[str, Any] | None = ROI, pgt: Any = None) -> _Session:
    family = {**(roi or {"roi_query": None, "roi_label": None, "roi_source": None, "roi_chr": None,
                         "roi_start": None, "roi_end": None}),
              "pgt": {"inheritance_model": "AD"} if pgt is None else pgt}
    return _Session({"FROM families": [family], "FROM sample_interval_track_sources": SOURCES})


def test_each_embryo_is_called_at_the_roi_from_the_blocks_the_haplotype_view_reads(monkeypatch) -> None:
    asked = _patch_reads(monkeypatch)
    out = asyncio.run(pgt_analysis.family_pgt_analysis(_session(), _context()))

    # The blocks and the QC are read over the same window: the ROI and 1 Mb on each side.
    assert asked["haplotypes"] == ("7", 29_500_000, 31_600_000)
    assert asked["markers"] == ("7", 29_500_000, 31_600_000)
    assert out.region_of_interest is not None and out.region_of_interest.label == "GENE1"
    assert (out.window.chrom, out.window.start, out.window.end) == ("7", 29_500_000, 31_600_000)
    assert (out.inheritance_model.recorded, out.inheritance_model.applied, out.inheritance_model.mode) == (
        "AD", "AD", "dominant")
    # The affected father and index share paternal homolog 1: the dominant haplotype.
    assert out.disease_haplotype_resolved is True
    assert [(h.origin, h.homolog, h.kind) for h in out.disease_haplotypes] == [("paternal", "1", "dominant")]

    risk, clear = out.embryos[1], out.embryos[0]
    assert [embryo.sample for embryo in out.embryos] == ["E_CLEAR", "E_RISK"]
    assert (risk.call, risk.roi_not_covered, risk.sex_dependent) == ("affected_or_at_risk", False, None)
    assert (clear.call, clear.sex) == ("unaffected_non_carrier", "und")
    # What the call rests on: the homologs carried at the ROI and the crossover inside it.
    assert [(h.origin, h.homolog, h.start, h.end) for h in risk.inherited] == [
        ("maternal", "1", 29_000_000, 30_550_000),
        ("paternal", "1", 29_000_000, 30_550_000),
        ("maternal", "0", 30_550_000, 32_000_000),
        ("paternal", "1", 30_550_000, 32_000_000),
    ]
    assert risk.recombinations == [30_550_000] and clear.recombinations == []
    # The phasing QC the ROI marker review shows for each embryo.
    assert risk.qc is not None and (risk.qc.informative_sites, risk.qc.mendel_errors, risk.qc.mendel_rate) == (40, 1, 0.025)
    assert (risk.qc.paternal_markers, risk.qc.maternal_markers) == (3, 1)
    # Left and right of the ROI apart; a marker inside the ROI is on neither flank.
    assert (
        risk.qc.paternal_markers_left,
        risk.qc.paternal_markers_right,
        risk.qc.maternal_markers_left,
        risk.qc.maternal_markers_right,
    ) == (1, 1, 0, 0)
    assert clear.qc is not None and (clear.qc.paternal_markers, clear.qc.maternal_markers) == (0, 1)
    # The father's phase correction next to the ROI; the mother's far away is not named.
    assert [(c.parent, c.position) for c in out.phase_corrections] == [("FATHER", 30_400_000)]
    # The members as the inference read them, and the inputs.
    assert [member.sample for member in out.members] == ["E_CLEAR", "E_RISK", "FATHER", "INDEX", "MOTHER"]
    assert out.members[2].affected is True and out.members[2].role == "father"
    assert [(s.sample, s.filename, s.blocks, s.loaded_at) for s in out.inputs.haplotype_sources] == [
        ("E_RISK", "family.vcf.gz", 30, "2026-10-01T09:00:00+00:00"),
        ("MOTHER", "family.vcf.gz", 12, "2026-10-01T09:00:00+00:00"),
    ]
    assert out.inputs.blocks_read == 6 and len(out.inputs.blocks_digest or "") == 64
    assert out.inputs.markers_truncated is False
    assert (out.rules.window_flank, out.rules.recombination_flank, out.rules.phase_switch_uncertainty) == (
        1_000_000, 250_000, 2_000_000)


def _verdict(embryo) -> tuple[str, list[str], list[tuple[str, float, float, str | None]]]:
    verdict = embryo.qc_verdict
    return (
        verdict.status,
        verdict.sides,
        [(finding.measure, finding.value, finding.threshold, finding.flank) for finding in verdict.findings],
    )


def test_each_embryo_gets_its_qc_verdict_checked_on_the_side_its_call_reads(monkeypatch) -> None:
    _patch_reads(monkeypatch)
    out = asyncio.run(pgt_analysis.family_pgt_analysis(_session(), _context()))

    clear, risk = out.embryos
    # The dominant haplotype is the father's: his side is checked, the mother's is not.
    assert _verdict(risk) == (
        "warning",
        ["paternal"],
        [
            ("informative_sites", 40, 50, None),
            ("mendel_rate", 0.025, 0.02, None),
            ("paternal_markers", 1, 6, "left"),
            ("paternal_markers", 1, 6, "right"),
        ],
    )
    assert _verdict(clear) == (
        "warning",
        ["paternal"],
        [("informative_sites", 38, 50, None), ("paternal_markers", 0, 6, "left"), ("paternal_markers", 0, 6, "right")],
    )
    # The call stands: a warning beside it, never a change to it.
    assert (risk.call, clear.call) == ("affected_or_at_risk", "unaffected_non_carrier")
    # The thresholds in force travel with the analysis.
    assert out.rules.qc_thresholds.model_dump() == {
        "min_informative_sites": 50,
        "max_mendel_rate": 0.02,
        "min_markers_per_flank": 6,
    }


def test_an_embryo_whose_qc_meets_every_threshold_passes(monkeypatch) -> None:
    markers = _markers()
    risk = markers.samples[1]
    risk.qc = PhasedSampleQc(informative_sites=60, mendel_errors=1, mendel_rate=1 / 60)
    # Six paternal markers on each flank of the ROI.
    risk.markers = [
        PhasedMarker(pos=position + index, hap1=1, hap2=None)
        for position in (30_400_000, 30_700_000)
        for index in range(6)
    ]
    _patch_reads(monkeypatch, markers=markers)
    out = asyncio.run(pgt_analysis.family_pgt_analysis(_session(), _context()))

    assert out.embryos[1].sample == "E_RISK"
    assert _verdict(out.embryos[1]) == ("pass", ["paternal"], [])


def test_a_qc_not_counted_at_the_roi_reads_as_not_counted_never_as_a_pass(monkeypatch) -> None:
    _patch_reads(monkeypatch, markers=_markers(truncated=True))
    out = asyncio.run(pgt_analysis.family_pgt_analysis(_session(), _context()))
    assert [_verdict(embryo) for embryo in out.embryos] == [
        ("not_counted", ["paternal"], []),
        ("not_counted", ["paternal"], []),
    ]


def test_an_embryo_not_called_has_no_qc_verdict(monkeypatch) -> None:
    _patch_reads(monkeypatch)
    out = asyncio.run(pgt_analysis.family_pgt_analysis(_session(roi=None), _context()))
    assert [embryo.qc_verdict for embryo in out.embryos] == [None, None]
    # The thresholds are stated all the same: they are the analysis's rules.
    assert out.rules.qc_thresholds.min_informative_sites == 50


# --- A record signed before the QC verdict ------------------------------------------------


def _current(monkeypatch) -> dict[str, Any]:
    _patch_reads(monkeypatch)
    return asyncio.run(pgt_analysis.family_pgt_analysis(_session(), _context())).model_dump(mode="json")


def _before_the_verdict(analysis: dict[str, Any]) -> dict[str, Any]:
    """The analysis as a record signed before the QC verdict holds it."""
    return {
        **analysis,
        "embryos": [{key: value for key, value in embryo.items() if key != "qc_verdict"} for embryo in analysis["embryos"]],
        "rules": {key: value for key, value in analysis["rules"].items() if key != "qc_thresholds"},
    }


def test_a_record_signed_before_the_qc_verdict_is_compared_on_what_it_holds(monkeypatch) -> None:
    current = _current(monkeypatch)
    signed = _before_the_verdict(current)
    comparable, predated = pgt_analysis.comparable_with_record(signed, current)
    assert comparable == signed
    assert predated == ["pgt_analysis.qc_verdict"]
    # The current analysis itself is left as it is.
    assert all("qc_verdict" in embryo for embryo in current["embryos"])

    # What the record holds is still compared: a call that changed reads as a change.
    changed = {**current, "embryos": [{**current["embryos"][0], "call": "carrier"}, *current["embryos"][1:]]}
    comparable, _ = pgt_analysis.comparable_with_record(signed, changed)
    assert comparable != signed


def test_a_record_holding_the_qc_verdict_is_compared_whole(monkeypatch) -> None:
    current = _current(monkeypatch)
    assert pgt_analysis.comparable_with_record(current, current) == (current, [])
    # A threshold or verdict that changed since the record was signed is not hidden.
    moved = {**current, "rules": {**current["rules"], "qc_thresholds": {**current["rules"]["qc_thresholds"], "min_markers_per_flank": 3}}}
    assert pgt_analysis.comparable_with_record(current, moved) == (moved, [])


@pytest.mark.parametrize(
    ("signed", "current"),
    [
        ({"unavailable": "The PGT analysis's embryo results could not be read."}, {"embryos": []}),
        ({"rules": {}}, {"unavailable": "The PGT analysis's embryo results could not be read."}),
        ({"rules": {}}, None),
        ({"rules": {}}, {"rules": {"window_flank": 1}, "embryos": ["not an embryo"]}),
    ],
)
def test_a_record_or_analysis_that_holds_no_verdict_is_compared_as_it_is(signed, current) -> None:
    # Unavailable on either side, or nothing the record predates: compared as it is, nothing named.
    assert pgt_analysis.comparable_with_record(signed, current) == (current, [])


def test_the_analysis_does_not_depend_on_the_order_the_store_returns_blocks_in(monkeypatch) -> None:
    _patch_reads(monkeypatch)
    stored = asyncio.run(pgt_analysis.family_pgt_analysis(_session(), _context()))
    _patch_reads(monkeypatch, haplotypes=_haplotypes("reversed"))
    reversed_order = asyncio.run(pgt_analysis.family_pgt_analysis(_session(), _context()))
    assert reversed_order.model_dump(mode="json") == stored.model_dump(mode="json")


def test_the_digest_names_the_blocks_read(monkeypatch) -> None:
    _patch_reads(monkeypatch)
    before = asyncio.run(pgt_analysis.family_pgt_analysis(_session(), _context()))
    moved = _haplotypes()
    moved.samples[0].segments[0].hap2_lineage = "untransmitted"
    _patch_reads(monkeypatch, haplotypes=moved)
    after = asyncio.run(pgt_analysis.family_pgt_analysis(_session(), _context()))
    assert after.inputs.blocks_digest != before.inputs.blocks_digest


def test_without_an_roi_no_embryo_is_called_and_nothing_is_read(monkeypatch) -> None:
    asked = _patch_reads(monkeypatch)
    out = asyncio.run(pgt_analysis.family_pgt_analysis(_session(roi=None), _context()))

    assert out.region_of_interest is None and out.window is None
    assert asked == {}
    assert [(embryo.sample, embryo.call, embryo.qc, embryo.inherited) for embryo in out.embryos] == [
        ("E_CLEAR", None, None, []),
        ("E_RISK", None, None, []),
    ]
    assert out.disease_haplotypes == [] and out.disease_haplotype_resolved is False
    assert out.inputs.blocks_digest is None and out.inputs.blocks_read == 0
    # The inputs it would read are still named.
    assert [source.sample for source in out.inputs.haplotype_sources] == ["E_RISK", "MOTHER"]


def test_without_haplotypes_to_read_no_embryo_is_called(monkeypatch) -> None:
    asked = _patch_reads(monkeypatch, haplotypes=HaplotypeResponse(chr="7", samples=[]))
    out = asyncio.run(pgt_analysis.family_pgt_analysis(_session(), _context(assembly_name=None)))

    assert out.window is not None
    assert "markers" not in asked
    assert all(embryo.call is None for embryo in out.embryos)
    assert out.inputs.blocks_read == 0 and out.inputs.blocks_digest is None


def test_an_roi_with_more_sites_than_the_marker_review_reads_counts_no_qc(monkeypatch) -> None:
    _patch_reads(monkeypatch, markers=_markers(truncated=True))
    out = asyncio.run(pgt_analysis.family_pgt_analysis(_session(), _context()))

    assert out.inputs.markers_truncated is True
    assert all(embryo.qc is None for embryo in out.embryos)
    # The calls stand: they are read from the blocks.
    assert [embryo.call for embryo in out.embryos] == ["unaffected_non_carrier", "affected_or_at_risk"]


@pytest.mark.parametrize(
    ("pgt", "recorded", "applied"),
    [
        ({"inheritance_model": "XLR"}, "XLR", "XLR"),
        ('{"inheritance_model": "AR"}', "AR", "AR"),  # a driver that hands back jsonb as text
        ({}, None, "AD"),
        ("not json", None, "AD"),
        (["AD"], None, "AD"),
        ({"inheritance_model": "  "}, None, "AD"),
    ],
)
def test_the_model_is_the_one_recorded_or_the_one_assumed(monkeypatch, pgt, recorded, applied) -> None:
    _patch_reads(monkeypatch)
    out = asyncio.run(pgt_analysis.family_pgt_analysis(_session(pgt=pgt), _context()))
    assert (out.inheritance_model.recorded, out.inheritance_model.applied) == (recorded, applied)


def test_an_roi_without_a_locus_is_no_roi() -> None:
    assert pgt_analysis._region_of_interest(None) is None
    assert pgt_analysis._region_of_interest({**ROI, "roi_query": " "}) is None
    assert pgt_analysis._region_of_interest({**ROI, "roi_start": None}) is None
    labelled = pgt_analysis._region_of_interest({**ROI, "roi_label": None, "roi_source": None})
    assert labelled is not None and (labelled.label, labelled.source) == ("GENE1", None)


def test_a_family_is_a_pgt_family_when_a_member_is_an_embryo() -> None:
    assert pgt_analysis.is_pgt_family(_context()) is True
    assert pgt_analysis.is_pgt_family(_context(sample_rows=[MEMBERS[1], MEMBERS[3]])) is False
    assert pgt_analysis.is_pgt_family(object()) is False


# --- An X-linked ROI: the QC counts a man's one X (CLIN-60) --------------------------------

X_MEMBERS = [
    {"sample_id": "FATHER", "role": "father", "sex": "male", "affected": False, "clinical_status": "unaffected",
     "carrier_status": "unknown", "carrier_type": None},
    {"sample_id": "MOTHER", "role": "mother", "sex": "female", "affected": False, "clinical_status": "unaffected",
     "carrier_status": "carrier", "carrier_type": "obligate"},
    {"sample_id": "INDEX", "role": "proband", "sex": "male", "affected": True, "clinical_status": "affected",
     "carrier_status": "unknown", "carrier_type": None},
    {"sample_id": "E_SON", "role": "embryo", "sex": "male", "affected": False, "clinical_status": "unknown",
     "carrier_status": "unknown", "carrier_type": None},
    {"sample_id": "E_DAUGHTER", "role": "embryo", "sex": "female", "affected": False, "clinical_status": "unknown",
     "carrier_status": "unknown", "carrier_type": None},
]
X_ROI = {"roi_query": "DMD", "roi_label": "DMD", "roi_source": "gene", "roi_chr": "X", "roi_start": 31_000_000,
         "roi_end": 31_100_000}


def _x_context() -> FamilyMetadataContext:
    return _context(
        sample_rows=X_MEMBERS,
        sample_uuid_to_name={f"u-{row['sample_id']}": row["sample_id"] for row in X_MEMBERS},
        sample_name_to_uuid={row["sample_id"]: f"u-{row['sample_id']}" for row in X_MEMBERS},
        affected_sample_names=["INDEX"],
        phase_corrections=[],
        relationship_rows=[
            {"relationship_type": "parent_child", "sample_id_a": parent, "sample_id_b": child, "role_a": role}
            for child in ("INDEX", "E_SON", "E_DAUGHTER")
            for parent, role in (("FATHER", "father"), ("MOTHER", "mother"))
        ],
    )


def _x_block(hap1: str, hap2: str, lineage: tuple[str, str]) -> dict[str, Any]:
    # chrX outside the PARs: a male's block is one-copy, as the haplotype endpoint marks it.
    return {"chr": "X", "start": 30_000_000, "end": 32_000_000, "hap1": hap1, "hap2": hap2, "ps": None,
            "hap1_lineage": lineage[0], "hap2_lineage": lineage[1], "hemizygous_in_males": True}


def _x_haplotypes() -> HaplotypeResponse:
    # The affected brother and the son carry their mother's homolog 1; the daughter her homolog 0.
    samples = [
        {"sample": "FATHER", "segments": [_x_block("0", "1", ("paternal", "paternal"))]},
        {"sample": "MOTHER", "segments": [_x_block("0", "1", ("maternal", "maternal"))]},
        {"sample": "INDEX", "segments": [_x_block("0", "1", ("paternal", "maternal"))]},
        {"sample": "E_SON", "segments": [_x_block("0", "1", ("paternal", "maternal"))]},
        {"sample": "E_DAUGHTER", "segments": [_x_block("0", "0", ("paternal", "maternal"))]},
    ]
    return HaplotypeResponse.model_validate({"chr": "X", "start": 30_000_000, "end": 32_100_000, "samples": samples})


@pytest.mark.parametrize("written", ["one allele", "twice"])
def test_at_an_x_linked_roi_a_man_one_x_raises_no_qc_warning(monkeypatch, written) -> None:
    # 60 sites around the ROI, 30 on each flank. The mother is heterozygous at each (homolog 1 carries the ALT); the
    # father's one X carries the REF at half of them and the ALT at the other half. The son
    # and his affected brother carry her homolog 1, the daughter her father's X and her
    # mother's homolog 0. Read as two copies, the men's calls written once left no site, and
    # written twice made the son's every site where his father's allele is not his an error.
    def man(allele: str) -> str:
        return allele if written == "one allele" else f"{allele}|{allele}"

    members = ["FATHER", "MOTHER", "INDEX", "E_SON", "E_DAUGHTER"]
    rows = [
        (
            (30_970_000 + 1_000 * i) if i < 30 else (31_110_000 + 1_000 * (i - 30)),
            "A",
            "G",
            members,
            [man(f), "0|1", man("1"), man("1"), f"{f}|0"],
        )
        for i, f in enumerate(["0", "1"] * 30)
    ]

    async def _haplotype_read(session, *, context, chr, start=None, end=None):
        return _x_haplotypes()

    async def _fetch(context, *, chrom, start, end, limit):
        return rows

    async def _shade_maps(context, *, chr, father, mother, start, end):
        return {}, {}

    # The marker counting runs for real on these genotypes, as the ROI marker review reads them.
    monkeypatch.setattr(pgt_analysis, "get_family_haplotypes_response", _haplotype_read)
    monkeypatch.setattr(phased_marker_service, "fetch_imputed_phased_genotypes", _fetch)
    monkeypatch.setattr(phased_marker_service, "_parent_block_shade_maps", _shade_maps)
    monkeypatch.setattr(pgt_analysis, "blocks_outdated", lambda context, rows: False)
    out = asyncio.run(pgt_analysis.family_pgt_analysis(_session(X_ROI, {"inheritance_model": "XLR"}), _x_context()))

    daughter, son = out.embryos
    assert (son.sample, son.call, daughter.sample, daughter.call) == (
        "E_SON", "affected_or_at_risk", "E_DAUGHTER", "unaffected_non_carrier")
    for embryo in (son, daughter):
        assert embryo.qc is not None
        assert (embryo.qc.informative_sites, embryo.qc.mendel_errors, embryo.qc.mendel_rate) == (60, 0, 0.0)
        # Each reads its mother's homolog at every site; no father's homolog is told apart.
        assert (embryo.qc.paternal_markers, embryo.qc.maternal_markers) == (0, 60)
        assert (embryo.qc.maternal_markers_left, embryo.qc.maternal_markers_right) == (30, 30)
        assert _verdict(embryo) == ("pass", ["maternal"], [])
