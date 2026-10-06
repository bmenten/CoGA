"""How far a dataset's import has got, and how long it should still take.

An importer that streams its input files puts ``bytes_read`` and ``bytes_total`` in the
stats of its progress reports: the bytes of its files read so far, of their total (of a
gzip file, the compressed bytes). ``DatasetTimer`` turns those reports into the dataset's
progress: the share read, and the time left at the pace it has read since its first
report. The time before that report is left out of the pace: it is the importer's start-up
(a VEP table parsed, an overwrite's rows deleted), and reads nothing of the files.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone
import gzip
import io
from pathlib import Path
import time
from typing import Any

from ..schemas import FamilyImportDatasetProgress

# The time left is first estimated once the import has read for this long since its first
# report: the pace over a few batches swings too much to tell.
MIN_ESTIMATE_SECONDS = 30.0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def read_stats(bytes_read: int | None, bytes_total: int | None) -> dict[str, int]:
    """The progress stats of an importer that has read ``bytes_read`` of its files'
    ``bytes_total``; none when either is unknown."""
    if bytes_read is None or not bytes_total:
        return {}
    return {"bytes_read": int(bytes_read), "bytes_total": int(bytes_total)}


def bytes_read_on_disk(handle: Any) -> int | None:
    """How far ``handle`` has read into its file on disk: of a gzip file, the compressed
    bytes, which compare with the file's size. Reads ahead of the lines handed out by at
    most a buffer. None when the handle cannot tell."""
    current = handle
    while True:
        if isinstance(current, io.TextIOWrapper):
            current = current.buffer
        elif isinstance(current, gzip.GzipFile):
            current = current.fileobj
        else:
            break
    try:
        return int(current.tell())
    except (AttributeError, OSError, ValueError):
        return None


def file_size(path: Path) -> int:
    """The file's size in bytes. Progress never fails an import: a file it cannot size
    counts for nothing, and the importer's own read of it says what is wrong."""
    try:
        return path.stat().st_size
    except OSError:
        return 0


class FilesReadInTurn:
    """The progress stats of an importer that reads several files one after another, in
    this order: the bytes read of them all, from what the reader of the current file
    reports of it."""

    def __init__(self, paths: Iterable[Path]) -> None:
        self._sizes = [file_size(path) for path in paths]
        self.bytes_total = sum(self._sizes)
        self._current = 0
        # The bytes of the files before the current one, read in full.
        self._bytes_before = 0

    def stats(self, file_stats: Mapping[str, Any]) -> dict[str, int]:
        """Over all the files, the stats of the current file's ``bytes_read``; none when
        its reader did not report it."""
        try:
            file_bytes_read = int(file_stats["bytes_read"])
        except (KeyError, TypeError, ValueError):
            return {}
        if self._current < len(self._sizes):
            file_bytes_read = min(file_bytes_read, self._sizes[self._current])
        return read_stats(self._bytes_before + file_bytes_read, self.bytes_total)

    def next_file(self) -> None:
        """The current file has been read: what is reported from now on is of the next."""
        if self._current < len(self._sizes):
            self._bytes_before += self._sizes[self._current]
            self._current += 1


def _fraction_read(stats: Mapping[str, Any]) -> float | None:
    try:
        bytes_read = int(stats["bytes_read"])
        bytes_total = int(stats["bytes_total"])
    except (KeyError, TypeError, ValueError):
        return None
    if bytes_total <= 0:
        return None
    return min(max(bytes_read / bytes_total, 0.0), 1.0)


class DatasetTimer:
    """A dataset's progress while it imports: started when the dataset starts, told each
    progress report of its importer (``observe``), and finished when the dataset ends,
    whichever way (``finish``)."""

    def __init__(
        self,
        *,
        now: Callable[[], datetime] = _utc_now,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._now = now
        self._monotonic = monotonic
        self._progress = FamilyImportDatasetProgress(started_at=now())
        # The first report that measured a share read: (monotonic time, share read).
        self._first: tuple[float, float] | None = None

    @property
    def progress(self) -> FamilyImportDatasetProgress:
        return self._progress

    def observe(self, stats: Mapping[str, Any]) -> FamilyImportDatasetProgress:
        """The progress after a report with these stats. A report that measures no share
        read (a heartbeat, a dataset whose importer does not count bytes) keeps the last
        measurement."""
        fraction = _fraction_read(stats)
        if fraction is None:
            return self._progress
        clock = self._monotonic()
        if self._first is None:
            self._first = (clock, fraction)
        first_clock, first_fraction = self._first
        reading_seconds = clock - first_clock
        seconds_left: float | None = None
        if fraction >= 1.0:
            seconds_left = 0.0
        elif reading_seconds >= MIN_ESTIMATE_SECONDS and fraction > first_fraction:
            seconds_left = (1.0 - fraction) * reading_seconds / (fraction - first_fraction)
        self._progress = self._progress.model_copy(
            update={
                "measured_at": self._now(),
                "fraction_read": fraction,
                "seconds_left": seconds_left,
            }
        )
        return self._progress

    def finish(self) -> FamilyImportDatasetProgress:
        """The progress of the dataset once it has ended: when it began and ended, and the
        share it had read; no time left."""
        self._progress = self._progress.model_copy(
            update={"finished_at": self._now(), "seconds_left": None}
        )
        return self._progress
