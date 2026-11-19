"""The conventional NIPT's result for a family (``cnipt_service``), with its datastores faked:
each maternal plasma's stored ccnv QC and segments judged by the rule with the profile's bounds,
each bound labelled from the QC-threshold catalogue; a family without ccnv output answers 404.
All values are synthetic (``_ccnv_output.py``)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException
import pytest

from backend.app.services import cnipt_service
from backend.tests._ccnv_output import qc_payload


def _row(chrom: str, start: int, end: int, value: float, **details: Any) -> dict[str, Any]:
    return {
        "chr": chrom,
        "start": start,
        "end": end,
        "value": value,
        "metadata_json": json.dumps({"source": "ccnv", "filename": "s.bed.gz", "line_no": 2, **details}),
    }


def _install(monkeypatch: pytest.MonkeyPatch, *, sample_metadata: dict[str, Any], configured: dict) -> list[dict]:
    asked: list[dict] = []

    async def context(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(
            family_id="CNIPT1",
            family_uuid="family-uuid",
            assembly_name="GRCh38",
            sample_rows=[
                {"sample_id": "CFDNA1", "sample_uuid": "uuid-1", "sample_metadata": sample_metadata},
                {"sample_id": "FETUS1", "sample_uuid": "uuid-2", "sample_metadata": {}},
            ],
        )

    async def rows(_assembly: str, **kwargs: Any) -> list[dict[str, Any]]:
        asked.append(kwargs)
        return [
            _row("21", 0, 46_000_000, 0.07, zscore=9.0, call="gain", implied_ff=0.12, discordant=False, origin_call="FETAL_CONSTITUTIONAL"),
            _row("1", 0, 240_000_000, 0.001, zscore=0.1),
        ]

    async def thresholds(_session: Any, profile_key: str) -> dict:
        assert profile_key == "nipt_conventional"
        return configured

    monkeypatch.setattr(cnipt_service, "build_family_metadata_context", context)
    monkeypatch.setattr(cnipt_service, "fetch_interval_track_rows", rows)
    monkeypatch.setattr(cnipt_service, "resolve_profile_thresholds", thresholds)
    return asked


@pytest.mark.asyncio
async def test_each_plasma_is_judged_from_its_stored_qc_and_segments(monkeypatch: pytest.MonkeyPatch) -> None:
    stored = {"ccnv": {"qc": qc_payload("CFDNA1"), "qc_file": "CFDNA1_qc.json"}}
    asked = _install(monkeypatch, sample_metadata=stored, configured={"cnipt.call_zscore": {"warn_value": 2.0, "error_value": 4.0}})

    result = await cnipt_service.get_family_cnipt_result(None, family_id="CNIPT1", user=None)  # type: ignore[arg-type]

    (sample,) = result.samples
    assert (result.family_id, sample.sample_id, sample.qc_file, sample.status) == ("CNIPT1", "CFDNA1", "CFDNA1_qc.json", "result")
    # The plasma's segments of its ccnv track, with their values.
    assert asked == [
        {
            "sample_uuid": "uuid-1",
            "family_uuid": "family-uuid",
            "track_type": "segments",
            "chromosomes": [],
            "source": "ccnv",
            "include_metadata": True,
        }
    ]
    targets = {target.target: target.verdict for target in sample.targets}
    assert (targets["21"], targets["1"]) == ("high_risk", "low_risk")
    bounds = {bound.key: bound for bound in sample.bounds}
    assert (bounds["cnipt.call_zscore"].label, bounds["cnipt.call_zscore"].error_value, bounds["cnipt.call_zscore"].provisional) == (
        "cNIPT: segment |z-score|",
        4.0,
        False,
    )
    assert bounds["cnipt.fetal_fraction"].provisional is True
    assert sample.read_metrics == {"effective_reads_kept": 12_000_000.0, "usable_bins": 6.0, "total_bins": 7.0, "lambda_gc": 1.0}


@pytest.mark.asyncio
async def test_a_family_without_ccnv_output_answers_404(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, sample_metadata={"assays": ["nipt_cfdna"]}, configured={})

    with pytest.raises(HTTPException) as refused:
        await cnipt_service.get_family_cnipt_result(None, family_id="CNIPT1", user=None)  # type: ignore[arg-type]
    assert refused.value.status_code == 404
