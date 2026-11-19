"""What the views are served to draw a conventional NIPT's maternal plasma (ccnv).

* A segments read serves each segment with the values its caller gives beside it (``details``):
  a ccnv segment's call, expected ratio, implied fetal fraction, discordance and origin; a
  WisecondorX segment's z-score. The row's provenance (its file and line) is not served, nor a
  value no detail can hold.
* The track availability serves a plasma's ccnv results (``ccnv_track_summary``): the fetal
  fraction the expected lines are drawn at is ccnv's effective one, beside every other estimate,
  the fetal sex and the sex-chromosome call. A record the import did not write is refused.

All values are synthetic (``_ccnv_output.py``).
"""

from __future__ import annotations

import json

import pytest

from backend.app.services.bed_service import _segment_details, _serialize_bed_record
from backend.app.services.ccnv_output import CcnvOutputError, ccnv_track_summary
from backend.tests._ccnv_output import qc_payload


def test_a_segment_is_served_with_its_callers_values_and_not_its_provenance() -> None:
    metadata = {
        "source": "ccnv",
        "filename": "CFDNA1_segments.bed.gz",
        "line_no": 12,
        "zscore": 5.6,
        "bin_count": 2,
        "call": "gain",
        "discordant": False,
        "origin_call": "FETAL_CONSTITUTIONAL",
        "nested": {"not": "a detail"},
    }
    row = {"chr": "21", "start": 0, "end": 200000, "value": 0.085, "metadata_json": json.dumps(metadata)}

    assert _serialize_bed_record("segments", row) == {
        "chr": "21",
        "start": 0,
        "end": 200000,
        "value": 0.085,
        "details": {"zscore": 5.6, "bin_count": 2, "call": "gain", "discordant": False, "origin_call": "FETAL_CONSTITUTIONAL"},
    }


def test_a_segment_whose_caller_gives_no_value_has_no_details_and_a_bin_none_at_all() -> None:
    hificnv = json.dumps({"source": "hificnv", "filename": "S1.copynum.bedgraph"})
    assert _segment_details(hificnv) is None
    assert _segment_details(None) is None
    assert _segment_details("not json") is None
    row = {"chr": "1", "start": 0, "end": 100, "value": 0.1, "metadata_json": json.dumps({"zscore": 1.0})}
    assert "details" not in _serialize_bed_record("coverage", row)
    assert _serialize_bed_record("segments", {**row, "metadata_json": hificnv})["details"] is None


def test_the_plasmas_summary_draws_at_the_effective_fetal_fraction_beside_every_estimate() -> None:
    summary = ccnv_track_summary({"qc": qc_payload("CFDNA1"), "qc_file": "CFDNA1_qc.json"})

    assert summary == {
        "fetal_fraction": 0.121111,
        "active_ff": 0.081234,
        "active_ff_se": 0.005,
        "ffy": 0.0,
        "ffx": 0.09,
        "ffa": 0.085,
        "ff_size": 0.07,
        "active_ff_source": "FUSED",
        "fetal_sex": "F",
        "sca_call": "XX",
        "sca_confidence": 0.998,
        "low_ff_warning": False,
    }


def test_an_estimate_the_qc_file_does_not_give_is_served_as_none() -> None:
    summary = ccnv_track_summary(
        {"qc": qc_payload("CFDNA1", ffy=None, active_ff_source=None, low_ff_warning=None), "qc_file": "q.json"}
    )
    assert (summary["ffy"], summary["active_ff_source"], summary["low_ff_warning"]) == (None, None, None)


@pytest.mark.parametrize(
    "stored",
    [
        pytest.param({"qc_file": "q.json"}, id="no-qc"),
        pytest.param({"qc": {**qc_payload("CFDNA1"), "mode": "germline"}, "qc_file": "q.json"}, id="germline"),
        pytest.param({"qc": qc_payload("CFDNA1", effective_ff=None), "qc_file": "q.json"}, id="no-effective-ff"),
    ],
)
def test_a_record_the_import_did_not_write_is_refused(stored: dict) -> None:
    with pytest.raises(CcnvOutputError):
        ccnv_track_summary(stored)
