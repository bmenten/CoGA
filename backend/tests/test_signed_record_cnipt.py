"""The conventional NIPT section of the signed analysis record (``signed_record_cnipt``), with its
datastores faked: each maternal plasma's result frozen as the cNIPT page shows it, with the
inputs it read; and what of it gates the sign-out (a plasma without a result, a result on
provisional bounds). All values are synthetic (``_ccnv_output.py``)."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.services import cnipt_service
from backend.app.services import signed_record_cnipt as src
from backend.app.services.hash_chain import canonical_hash
from backend.app.services.family_package_records import suffixed_source
from backend.tests._ccnv_output import qc_payload

_LOADED = datetime(2026, 11, 19, 9, 30, tzinfo=timezone.utc)
_EVERY_BOUND_SET = {
    "cnipt.fetal_fraction": {"warn_value": None, "error_value": 0.04},
    "cnipt.call_zscore": {"warn_value": 2.5, "error_value": 3.0},
    "cnipt.implied_ff_ratio_min": {"warn_value": None, "error_value": 0.5},
    "cnipt.implied_ff_ratio_max": {"warn_value": None, "error_value": 1.5},
    "cnipt.sca_confidence": {"warn_value": None, "error_value": 0.99},
    "cnipt.min_cnv_size": {"warn_value": None, "error_value": 3.0},
}


def _row(chrom: str, start: int, end: int, value: float, **details: Any) -> dict[str, Any]:
    return {
        "chr": chrom,
        "start": start,
        "end": end,
        "value": value,
        "metadata_json": json.dumps({"source": "ccnv", "filename": "s.bed.gz", "line_no": 2, **details}),
    }


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> list[dict[str, Any]]:
        return self._rows


class _Session:
    """Answers the query of the segments files; records what it was asked."""

    def __init__(self, files: list[dict[str, Any]] | None = None) -> None:
        self.files = files if files is not None else []
        self.asked: list[dict[str, Any]] = []

    async def execute(self, _statement: Any, params: dict[str, Any]) -> _Result:
        self.asked.append(params)
        return _Result(self.files)


# The conventional NIPT of the pregnancy ZW1, by its key; its package lists the plasma and the fetus.
CNIPT = "conventional_nipt:ZW1"


def _context(sample_metadata: dict[str, Any]) -> Any:
    return SimpleNamespace(
        family_id="CNIPT1",
        family_uuid="family-uuid",
        assembly_name="GRCh38",
        sample_rows=[
            {"sample_id": "CFDNA1", "sample_uuid": "uuid-1", "sample_metadata": sample_metadata, "role": "mother"},
            {"sample_id": "FATHER1", "sample_uuid": "uuid-2", "sample_metadata": {}, "role": "father"},
            {"sample_id": "FETUS1", "sample_uuid": "uuid-3", "sample_metadata": {}, "role": "fetus"},
        ],
        family_metadata={"analyses": [CNIPT], "package_imports": {CNIPT: {"members": ["CFDNA1", "FETUS1"]}}},
        scope=SimpleNamespace(analysis=CNIPT),
    )


def _stored(**prenatal: Any) -> dict[str, Any]:
    return {"ccnv": {"qc": qc_payload("CFDNA1", **prenatal), "qc_file": "CFDNA1_qc.json"}}


def _install(monkeypatch: pytest.MonkeyPatch, *, configured: dict, segments: list[dict[str, Any]] | None = None) -> None:
    async def rows(_assembly: str, **_kwargs: Any) -> list[dict[str, Any]]:
        return (
            segments
            if segments is not None
            else [
                _row("21", 0, 46_000_000, 0.07, zscore=9.0, call="gain", implied_ff=0.12, discordant=False, origin_call="FETAL_CONSTITUTIONAL"),
                _row("1", 0, 240_000_000, 0.001, zscore=0.1),
            ]
        )

    async def thresholds(_session: Any, _profile_key: str) -> dict:
        return configured

    monkeypatch.setattr(cnipt_service, "fetch_interval_track_rows", rows)
    monkeypatch.setattr(cnipt_service, "resolve_profile_thresholds", thresholds)


@pytest.mark.asyncio
async def test_the_section_freezes_the_page_result_with_its_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, configured=_EVERY_BOUND_SET)
    stored = _stored()
    context = _context(stored)
    session = _Session(
        [
            {"sample_uuid": "uuid-1", "filename": "CFDNA1_segments.bed.gz", "row_count": 2, "uploaded_at": _LOADED},
            {"sample_uuid": "uuid-9", "filename": "OTHER_segments.bed.gz", "row_count": 5, "uploaded_at": _LOADED},
        ]
    )

    section = await src.cnipt_results(session, context)  # type: ignore[arg-type]

    # The cNIPT page's object, from the same code, beside what it was computed from.
    pregnancy = cnipt_service.cnipt_pregnancy(context, CNIPT)
    page = (await cnipt_service.family_cnipt_results(None, context, pregnancy))[0].result.model_dump(mode="json")  # type: ignore[arg-type]
    (sample,) = section["samples"]
    inputs = sample.pop("inputs")
    assert sample == page
    assert (sample["sample_id"], sample["status"], sample["qc_file"]) == ("CFDNA1", "result", "CFDNA1_qc.json")
    assert {target["target"]: target["verdict"] for target in sample["targets"]}["21"] == "high_risk"
    assert inputs == {
        "qc_digest": canonical_hash(stored["ccnv"]["qc"]),
        "segments": 2,
        "segments_digest": inputs["segments_digest"],
        # Only the plasma's own files.
        "segment_files": [{"file": "CFDNA1_segments.bed.gz", "rows": 2, "loaded_at": _LOADED.isoformat()}],
    }
    assert len(inputs["segments_digest"]) == 64
    # The pregnancy's own ccnv source (family_package_records.suffixed_source).
    assert session.asked == [{"family_uuid": "family-uuid", "source": suffixed_source("ccnv", "ZW1")}]
    assert (section["analysis"], section["fetuses"], section["multiple_pregnancy_note"]) == (CNIPT, ["FETUS1"], None)
    # Deterministic, and as JSONB keeps it.
    assert await src.cnipt_results(_Session(session.files), context) == {  # type: ignore[arg-type]
        "analysis": CNIPT,
        "fetuses": ["FETUS1"],
        "multiple_pregnancy_note": None,
        "samples": [{**sample, "inputs": inputs}],
    }
    assert json.loads(json.dumps(section, allow_nan=False)) == section


@pytest.mark.asyncio
async def test_another_segment_reads_as_another_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, configured=_EVERY_BOUND_SET)
    first = await src.cnipt_results(_Session(), _context(_stored()))  # type: ignore[arg-type]
    _install(monkeypatch, configured=_EVERY_BOUND_SET, segments=[_row("21", 0, 46_000_000, 0.071, zscore=9.0, call="gain")])
    second = await src.cnipt_results(_Session(), _context(_stored()))  # type: ignore[arg-type]
    assert first["samples"][0]["inputs"]["segments_digest"] != second["samples"][0]["inputs"]["segments_digest"]


def test_a_number_jsonb_cannot_hold_is_frozen_as_null() -> None:
    assert src._json_safe({"a": [math.nan, -0.0, math.inf, 1.5], "b": (2, "x")}) == {"a": [None, 0.0, None, 1.5], "b": [2, "x"]}
    assert math.copysign(1.0, src._json_safe(-0.0)) == 1.0


def test_only_a_family_with_ccnv_output_holds_the_section() -> None:
    assert src.holds_ccnv_output(_context(_stored())) is True
    assert src.holds_ccnv_output(_context({"assays": ["nipt_cfdna"]})) is False


@pytest.mark.asyncio
async def test_a_result_on_provisional_bounds_is_a_concern_of_the_sign_out(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, configured={"cnipt.call_zscore": {"warn_value": 2.5, "error_value": 3.0}})
    section = await src.cnipt_results(_Session(), _context(_stored()))  # type: ignore[arg-type]

    assert src.cnipt_signout_concerns(section, of_cnipt=True) == [
        (
            "Sample CFDNA1 (cNIPT maternal plasma)",
            "The result rests on provisional bounds, which QA has not set: fetal fraction; "
            "implied fetal fraction, lowest concordant share; implied fetal fraction, highest "
            "concordant share; sex-chromosome call confidence; smallest segmental CNV assessed.",
        )
    ]


@pytest.mark.asyncio
async def test_a_plasma_without_a_result_is_a_concern_of_the_sign_out(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, configured=_EVERY_BOUND_SET)
    section = await src.cnipt_results(_Session(), _context(_stored(effective_ff=0.031)))  # type: ignore[arg-type]

    assert section["samples"][0]["status"] == "no_result"
    assert src.cnipt_signout_concerns(section, of_cnipt=True) == [
        ("Sample CFDNA1 (cNIPT maternal plasma)", "No result — the fetal fraction (3.1%) is below 4.0%.")
    ]


@pytest.mark.asyncio
async def test_a_result_on_bounds_qa_set_raises_no_concern(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, configured=_EVERY_BOUND_SET)
    section = await src.cnipt_results(_Session(), _context(_stored()))  # type: ignore[arg-type]
    assert src.cnipt_signout_concerns(section, of_cnipt=True) == []


def test_another_analysis_without_the_section_raises_no_concern() -> None:
    # The record of another analysis holds no cNIPT result to gate on.
    assert src.cnipt_signout_concerns(None, of_cnipt=False) == []
    assert src.cnipt_signout_concerns({"unavailable": "The conventional NIPT results could not be read."}, of_cnipt=False) == []
    assert src.cnipt_signout_concerns({"samples": [{"sample_id": "CFDNA1", "status": "no_result"}]}, of_cnipt=False) == [
        ("Sample CFDNA1 (cNIPT maternal plasma)", "No result — no reason recorded.")
    ]


def test_a_conventional_nipt_record_without_a_result_is_a_concern() -> None:
    # A sign-out of a conventional NIPT closes its result: a record that froze none is gated.
    assert src.cnipt_signout_concerns({"unavailable": "The conventional NIPT results could not be read."}, of_cnipt=True) == [
        ("Conventional NIPT", "The results could not be read: the record holds no result of a maternal plasma.")
    ]
    assert src.cnipt_signout_concerns(None, of_cnipt=True) == [
        ("Conventional NIPT", "The results were not read: the record holds no result of a maternal plasma.")
    ]
    for section in ({"samples": []}, {"samples": ["not an object"]}):
        assert src.cnipt_signout_concerns(section, of_cnipt=True) == [
            ("Conventional NIPT", "No maternal plasma of the analysis holds a ccnv result.")
        ]
