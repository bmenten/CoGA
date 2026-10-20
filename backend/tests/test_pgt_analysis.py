"""The PGT analysis the family page shows and a sign-out freezes (REQ-PGT-005).

``pgt_analysis.family_pgt_analysis`` reads the family's ROI, inheritance model and members,
the haplotype blocks over the ROI and 1 Mb on each side as the haplotype endpoint serves
them, and each child's phasing QC at the ROI as the ROI marker review counts it; it calls the
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
from backend.app.services import pgt_analysis
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
        return PhasedMarkerResponse(chr="7", start=30_500_000, end=30_600_000, truncated=True, covered=[1, 2])
    return PhasedMarkerResponse(
        chr="7",
        start=30_500_000,
        end=30_600_000,
        samples=[
            PhasedMarkerSample(sample="FATHER", reference=True),
            PhasedMarkerSample(
                sample="E_RISK",
                markers=[PhasedMarker(pos=30_510_000, hap1=1, hap2=None), PhasedMarker(pos=30_520_000, hap1=1, hap2=0)],
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
    return asked


def _session(roi: dict[str, Any] | None = ROI, pgt: Any = None) -> _Session:
    family = {**(roi or {"roi_query": None, "roi_label": None, "roi_source": None, "roi_chr": None,
                         "roi_start": None, "roi_end": None}),
              "pgt": {"inheritance_model": "AD"} if pgt is None else pgt}
    return _Session({"FROM families": [family], "FROM sample_interval_track_sources": SOURCES})


def test_each_embryo_is_called_at_the_roi_from_the_blocks_the_haplotype_view_reads(monkeypatch) -> None:
    asked = _patch_reads(monkeypatch)
    out = asyncio.run(pgt_analysis.family_pgt_analysis(_session(), _context()))

    # The blocks are read over the ROI and 1 Mb on each side, the QC over the ROI itself.
    assert asked["haplotypes"] == ("7", 29_500_000, 31_600_000)
    assert asked["markers"] == ("7", 30_500_000, 30_600_000)
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
    assert (risk.qc.paternal_markers, risk.qc.maternal_markers) == (2, 1)
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
