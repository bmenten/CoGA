"""Discovery and track ownership for the three CNV callers a package can carry.

WisecondorX, QDNAseq and HiFiCNV all write per-bin and per-segment copy-number
output, and a long-read package can carry all three for the same sample. Two
things then matter: that each caller's files are found where *its* pipeline
writes them, and that re-importing one caller never disturbs another's rows.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.services.family_package_discovery import (
    NAMING_SCHEMES,
    _choose_candidate_path,
)


QDNASEQ = NAMING_SCHEMES["standard_v1"]["datasets"]["qdnaseq"]
WISECONDORX = NAMING_SCHEMES["standard_v1"]["datasets"]["wisecondorx"]


def _touch(path: Path, content: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _resolve(root: Path, patterns: list[str], sample_id: str) -> tuple[str | None, bool]:
    return _choose_candidate_path(root, patterns, family_id="pacbio", sample_id=sample_id)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_qdnaseq_is_found_in_the_lrsvar_layout(tmp_path: Path) -> None:
    # nf-core/lrsvar writes QDNAseq output as BED beside the HiFiCNV results and
    # names it after the caller, rather than as CSV under a QDNAseq/ directory.
    _touch(tmp_path / "cnv/HG002/HG002_cnv_qdnaseq_bins.bed")
    _touch(tmp_path / "cnv/HG002/HG002_cnv_qdnaseq_segs.bed")

    bins, bins_exists = _resolve(tmp_path, QDNASEQ["bins"], "HG002")
    segments, segments_exists = _resolve(tmp_path, QDNASEQ["segments"], "HG002")

    assert (bins, bins_exists) == ("cnv/HG002/HG002_cnv_qdnaseq_bins.bed", True)
    assert (segments, segments_exists) == ("cnv/HG002/HG002_cnv_qdnaseq_segs.bed", True)


def test_the_older_qdnaseq_csv_layout_still_resolves(tmp_path: Path) -> None:
    _touch(tmp_path / "QDNAseq/HG002/bins.csv")
    _touch(tmp_path / "QDNAseq/HG002/segments.csv")

    # The lrsvar patterns are additions, not replacements: packages already in the
    # system keep importing.
    assert _resolve(tmp_path, QDNASEQ["bins"], "HG002")[0] == "QDNAseq/HG002/bins.csv"
    assert _resolve(tmp_path, QDNASEQ["segments"], "HG002")[0] == "QDNAseq/HG002/segments.csv"


def test_qdnaseq_raw_bins_and_calls_are_not_mistaken_for_the_bin_track(tmp_path: Path) -> None:
    # `raw_bins` holds uncorrected read counts and `calls` a discrete call; neither
    # belongs on the coverage axis, and both sit right next to the file that does.
    _touch(tmp_path / "cnv/HG002/HG002_cnv_qdnaseq_raw_bins.bed")
    _touch(tmp_path / "cnv/HG002/HG002_cnv_qdnaseq_calls.bed")

    assert _resolve(tmp_path, QDNASEQ["bins"], "HG002")[1] is False
    assert _resolve(tmp_path, QDNASEQ["segments"], "HG002")[1] is False


def test_wisecondorx_is_found_under_its_sample_prefixed_names(tmp_path: Path) -> None:
    _touch(tmp_path / "wisecondorx/HG002/HG002_bins.bed")
    _touch(tmp_path / "wisecondorx/HG002/HG002_segments.bed")

    assert _resolve(tmp_path, WISECONDORX["bins"], "HG002")[0] == "wisecondorx/HG002/HG002_bins.bed"
    assert (
        _resolve(tmp_path, WISECONDORX["segments"], "HG002")[0]
        == "wisecondorx/HG002/HG002_segments.bed"
    )


# ---------------------------------------------------------------------------
# Track ownership
# ---------------------------------------------------------------------------


class _Recorder:
    """Captures the (track_type, source) pairs cleared before an import runs."""

    def __init__(self, existing: int) -> None:
        self.existing = existing
        self.cleared: list[tuple[str, str]] = []
        self.imported = False

    async def count(self, session: Any, **kwargs: Any) -> int:
        return self.existing

    async def delete(self, session: Any, **kwargs: Any) -> None:
        self.cleared.append((kwargs["track_type"], kwargs["source"]))

    async def importer(self) -> dict[str, int]:
        self.imported = True
        return {"processed": 1, "inserted": 1, "skipped": 0}


async def _run_guard(
    monkeypatch: pytest.MonkeyPatch, recorder: _Recorder, conflict_mode: str
) -> dict[str, Any]:
    from app.services import family_package_datasets as datasets

    monkeypatch.setattr(datasets, "_interval_track_count", recorder.count)
    monkeypatch.setattr(datasets, "_delete_sample_interval_source", recorder.delete)
    return await datasets._import_interval_track_unless_present(
        None,
        sample_context=object(),
        track_type="coverage",
        source="qdnaseq",
        conflict_mode=conflict_mode,
        importer=recorder.importer,
    )


@pytest.mark.asyncio
async def test_reimport_clears_the_whole_source_not_just_the_filename(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder(existing=500)
    await _run_guard(monkeypatch, recorder, "overwrite")

    # A filename-scoped replace accumulates rows when the file feeding a track
    # changes -- which is what moving HiFiCNV's coverage track from the bedGraph to
    # the depth bigWig would have done.
    assert recorder.cleared == [("coverage", "qdnaseq")]
    assert recorder.imported


@pytest.mark.asyncio
async def test_update_mode_leaves_an_existing_track_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder(existing=500)
    result = await _run_guard(monkeypatch, recorder, "update")

    assert result == {"skipped": True, "existing": 500}
    assert recorder.cleared == []
    assert not recorder.imported


@pytest.mark.asyncio
async def test_nothing_is_cleared_when_the_track_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder(existing=0)
    await _run_guard(monkeypatch, recorder, "overwrite")

    # A first import has nothing to replace; issuing the delete anyway would be a
    # pointless round trip per track per sample.
    assert recorder.cleared == []
    assert recorder.imported
