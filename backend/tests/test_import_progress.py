"""A dataset's import progress: the share of its files read, and the time it should
still take at the pace it has read."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import gzip
import json
import random
from pathlib import Path

import pytest

from backend.app.schemas import FamilyImportDatasetSummary
from backend.app.services import import_progress
from backend.app.services.family_package_common import _dataset_summary_list, _model_list_json
from backend.app.services.import_progress import (
    DatasetTimer,
    FilesReadInTurn,
    bytes_read_on_disk,
    file_size,
    read_stats,
)

_START = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)


class _Clock:
    """The wall clock and the monotonic clock of a test, moved on by hand."""

    def __init__(self) -> None:
        self.seconds = 0.0

    def now(self) -> datetime:
        return _START + timedelta(seconds=self.seconds)

    def monotonic(self) -> float:
        return 1_000.0 + self.seconds


def _timer(clock: _Clock) -> DatasetTimer:
    return DatasetTimer(now=clock.now, monotonic=clock.monotonic)


def test_the_time_left_is_the_rest_of_the_files_at_the_pace_read_since_the_first_report() -> None:
    clock = _Clock()
    timer = _timer(clock)
    clock.seconds = 40  # start-up: an overwrite's delete, before the first report
    first = timer.observe({"bytes_read": 100, "bytes_total": 1_000})
    clock.seconds = 100
    later = timer.observe({"bytes_read": 400, "bytes_total": 1_000})

    assert timer.progress.started_at == _START
    # One report is no pace yet.
    assert first.fraction_read == pytest.approx(0.1)
    assert first.seconds_left is None
    # 300 bytes in 60 s, 600 to go: 120 s. The 40 s of start-up are not in the pace.
    assert later.fraction_read == pytest.approx(0.4)
    assert later.seconds_left == pytest.approx(120.0)
    assert later.measured_at == _START + timedelta(seconds=100)


def test_no_time_left_is_told_before_the_import_has_read_for_long_enough() -> None:
    clock = _Clock()
    timer = _timer(clock)
    timer.observe({"bytes_read": 100, "bytes_total": 1_000})
    clock.seconds = import_progress.MIN_ESTIMATE_SECONDS - 1
    early = timer.observe({"bytes_read": 300, "bytes_total": 1_000})
    clock.seconds = import_progress.MIN_ESTIMATE_SECONDS
    enough = timer.observe({"bytes_read": 300, "bytes_total": 1_000})

    assert early.seconds_left is None
    assert enough.seconds_left is not None


def test_a_report_without_bytes_keeps_the_last_measurement() -> None:
    clock = _Clock()
    timer = _timer(clock)
    timer.observe({"bytes_read": 100, "bytes_total": 1_000})
    clock.seconds = 60
    measured = timer.observe({"bytes_read": 400, "bytes_total": 1_000})
    clock.seconds = 90
    # The periodic heartbeat of a dataset: its static stats, no bytes.
    heartbeat = timer.observe({"family_vcf": "snv/family.vcf.gz", "stage": "running"})

    assert heartbeat == measured


def test_a_file_read_to_its_end_has_no_time_left() -> None:
    clock = _Clock()
    timer = _timer(clock)
    timer.observe({"bytes_read": 100, "bytes_total": 1_000})
    clock.seconds = 5
    # Read ahead past the end by a buffer: still the whole file, and finishing.
    progress = timer.observe({"bytes_read": 1_200, "bytes_total": 1_000})

    assert progress.fraction_read == 1.0
    assert progress.seconds_left == 0.0


def test_a_finished_dataset_keeps_when_it_ran_and_how_far_it_read() -> None:
    clock = _Clock()
    timer = _timer(clock)
    timer.observe({"bytes_read": 100, "bytes_total": 1_000})
    clock.seconds = 60
    timer.observe({"bytes_read": 400, "bytes_total": 1_000})
    clock.seconds = 75
    finished = timer.finish()

    assert finished.started_at == _START
    assert finished.finished_at == _START + timedelta(seconds=75)
    assert finished.fraction_read == pytest.approx(0.4)
    assert finished.seconds_left is None


def test_a_dataset_whose_importer_counts_no_bytes_only_has_its_times() -> None:
    clock = _Clock()
    timer = _timer(clock)
    timer.observe({"processed": 10, "inserted": 10})
    clock.seconds = 3
    finished = timer.finish()

    assert finished.fraction_read is None
    assert finished.measured_at is None
    assert finished.finished_at == _START + timedelta(seconds=3)


def test_files_read_in_turn_add_up_to_the_datasets_bytes(tmp_path: Path) -> None:
    first = tmp_path / "first.vcf"
    second = tmp_path / "second.vcf"
    first.write_bytes(b"x" * 100)
    second.write_bytes(b"y" * 300)
    files = FilesReadInTurn([first, second])

    assert files.bytes_total == 400
    assert files.stats({"bytes_read": 60, "processed": 2}) == {"bytes_read": 60, "bytes_total": 400}
    # A reader's look-ahead never counts past its file.
    assert files.stats({"bytes_read": 150}) == {"bytes_read": 100, "bytes_total": 400}
    files.next_file()
    assert files.stats({"bytes_read": 30}) == {"bytes_read": 130, "bytes_total": 400}
    assert files.stats({"processed": 5}) == {}


def test_a_file_that_cannot_be_sized_counts_for_nothing(tmp_path: Path) -> None:
    assert file_size(tmp_path / "missing.vcf.gz") == 0
    assert FilesReadInTurn([tmp_path / "missing.vcf.gz"]).bytes_total == 0


def test_read_stats_need_both_counts() -> None:
    assert read_stats(10, 100) == {"bytes_read": 10, "bytes_total": 100}
    assert read_stats(None, 100) == {}
    assert read_stats(10, None) == {}
    assert read_stats(10, 0) == {}


def test_a_gzip_files_lines_are_read_by_its_compressed_bytes(tmp_path: Path) -> None:
    path = tmp_path / "calls.vcf.gz"
    rng = random.Random(7)
    lines = [f"chr1\t{index}\t.\t{rng.getrandbits(64):016x}\n" for index in range(200_000)]
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.writelines(lines)
    size = path.stat().st_size

    positions: list[int] = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for index, _line in enumerate(handle, start=1):
            if index % 20_000 == 0:
                position = bytes_read_on_disk(handle)
                assert position is not None
                positions.append(position)

    assert positions == sorted(positions)
    assert positions[0] < size / 4
    assert size * 0.9 < positions[-1] <= size


def test_a_plain_files_lines_are_read_by_its_bytes(tmp_path: Path) -> None:
    path = tmp_path / "calls.vcf"
    path.write_text("".join(f"chr1\t{index}\t.\tA\tG\n" for index in range(50_000)), encoding="utf-8")
    size = path.stat().st_size

    with path.open("r", encoding="utf-8") as handle:
        for index, _line in enumerate(handle, start=1):
            if index == 25_000:
                halfway = bytes_read_on_disk(handle)

    assert halfway is not None and size * 0.4 < halfway < size * 0.6


def test_a_handle_that_cannot_tell_reads_as_unknown() -> None:
    assert bytes_read_on_disk(object()) is None


def test_the_progress_is_kept_in_the_jobs_record_and_read_back() -> None:
    clock = _Clock()
    timer = _timer(clock)
    timer.observe({"bytes_read": 100, "bytes_total": 1_000})
    clock.seconds = 60
    summary = FamilyImportDatasetSummary(
        dataset_type="snv",
        status="running",
        progress=timer.observe({"bytes_read": 400, "bytes_total": 1_000}),
    )

    stored = _model_list_json([summary])
    (read_back,) = _dataset_summary_list(json.loads(stored))

    assert read_back == summary
    assert json.loads(stored)[0]["progress"]["started_at"] == "2026-01-05T09:00:00Z"
