"""A coverage read of the BED endpoints is of one caller's coverage (TRACE-10).

``GET /bed/{sample}/coverage`` and its ``/batch`` read a sample's coverage track. A sample can
hold several callers' coverage at once, each under its own source (a coverage BED, WisecondorX's
and QDNAseq's bins, HiFiCNV's depth). A read that named no ``source`` came back with every
caller's rows merged, and a windowed read averaged them: a depth and log2 ratios made one value
that is none of them. The views always name the caller (#852). Now:

* a coverage read without ``source`` where the sample holds several callers' coverage, in the
  region read, is refused (400), naming the callers; with ``source`` it reads that caller's rows;
* where the sample holds one caller's coverage there, a read without ``source`` is answered as
  before; the other track types are not refused (segments and APCAD are not averaged);
* the text format names its value column ``value``, as the JSON does, whatever the track holds;
  it said ``ratio``.

The interval store is a fake: each (source, chromosome) with its rows, answering as ClickHouse
answers (``source=None``: every source's rows).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastapi import HTTPException

from backend.app.services import bed_service
from backend.app.services.family_metadata_context import SampleMetadataContext

SAMPLE = SampleMetadataContext(
    sample_uuid="uuid-proband",
    sample_id="PROBAND",
    family_uuid="family-uuid",
    family_id="FAM_BED",
    sex="female",
    project_ids=["project-uuid"],
    assembly_id="assembly-uuid",
    assembly_name="GRCh38",
)


class _Store:
    """Each track's rows by (track type, source): (chromosome, start, end, value)."""

    def __init__(self, tracks: dict[tuple[str, str], list[tuple[str, int, int, float]]]) -> None:
        self.tracks = tracks
        self.asked: list[dict[str, Any]] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(bed_service, "get_interval_track_sources_by_sample", self.sources)
        monkeypatch.setattr(bed_service, "fetch_interval_track_rows", self.rows)

    async def sources(self, _assembly, *, family_uuid, sample_uuid_to_name, track_type, chromosomes, start=None, end=None):
        found = sorted(
            {
                source
                for (kind, source), rows in self.tracks.items()
                if kind == track_type and any(chrom in chromosomes for chrom, *_rest in rows)
            }
        )
        return {name: found for name in sample_uuid_to_name.values()} if found else {}

    async def rows(self, _assembly, *, track_type, chromosomes, source=None, limit=None, **kwargs):
        self.asked.append({"track_type": track_type, "source": source})
        return [
            {"chr": chrom, "start": start, "end": end, "value": value, "record_id": None, "origin": None}
            for (kind, track_source), rows in self.tracks.items()
            if kind == track_type and (source is None or track_source == source)
            for chrom, start, end, value in rows
            if chrom in chromosomes
        ]


def _json(response) -> dict[str, Any]:
    return json.loads(response.body)


# A coverage BED of read depth and WisecondorX's bins (log2 ratios) on chromosome 1.
TWO_CALLERS = {
    ("coverage", "web"): [("1", 0, 1000, 400.0)],
    ("coverage", "wisecondorx"): [("1", 0, 1000, 0.1)],
    ("segments", "web"): [("1", 0, 1000, 0.0)],
    ("segments", "wisecondorx"): [("1", 0, 1000, 0.1)],
}


def test_a_coverage_read_without_a_caller_is_refused_where_the_sample_holds_several(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _Store(TWO_CALLERS)
    store.install(monkeypatch)

    for read in (
        bed_service.fetch_bed_json(
            None, sample_context=SAMPLE, bed_type="coverage", chrom="1", window=500, start=None, end=None, limit=10
        ),
        bed_service.fetch_bed_batch_text(
            None, sample_context=SAMPLE, bed_type="coverage", chroms=["1", "2"], window=None, start=None, end=None, limit=10
        ),
    ):
        with pytest.raises(HTTPException) as refused:
            asyncio.run(read)
        assert refused.value.status_code == 400
        assert refused.value.detail == (
            "The sample holds coverage of several callers here (web, wisecondorx): name one with source"
        )
    # Before: both callers' rows, averaged in each window to 200.05.
    assert store.asked == []

    # Naming the caller reads its rows alone.
    named = _json(
        asyncio.run(
            bed_service.fetch_bed_json(
                None,
                sample_context=SAMPLE,
                bed_type="coverage",
                chrom="1",
                window=500,
                start=None,
                end=None,
                limit=10,
                source="web",
            )
        )
    )
    assert [item["value"] for item in named["items"]] == [400.0]


def test_a_coverage_read_of_one_callers_track_and_the_other_track_types_are_answered_as_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _Store({("coverage", "web"): [("1", 0, 1000, 400.0)], ("coverage", "wisecondorx"): [("2", 0, 1000, 0.1)]}).install(
        monkeypatch
    )
    # One caller's coverage on chromosome 1: no caller need be named there.
    one = _json(
        asyncio.run(
            bed_service.fetch_bed_json(
                None, sample_context=SAMPLE, bed_type="coverage", chrom="1", window=None, start=None, end=None, limit=10
            )
        )
    )
    assert [item["value"] for item in one["items"]] == [400.0]

    # Segments are not averaged: read as before, every caller's.
    _Store(TWO_CALLERS).install(monkeypatch)
    segments = _json(
        asyncio.run(
            bed_service.fetch_bed_json(
                None, sample_context=SAMPLE, bed_type="segments", chrom="1", window=None, start=None, end=None, limit=10
            )
        )
    )
    assert sorted(item["value"] for item in segments["items"]) == [0.0, 0.1]


def test_the_text_format_names_its_value_column_value(monkeypatch: pytest.MonkeyPatch) -> None:
    _Store(TWO_CALLERS).install(monkeypatch)

    def _header(bed_type: str, source: str) -> str:
        response = asyncio.run(
            bed_service.fetch_bed_text(
                None,
                sample_context=SAMPLE,
                bed_type=bed_type,
                chrom="1",
                window=None,
                start=None,
                end=None,
                limit=10,
                source=source,
            )
        )
        return response.body.decode().splitlines()[0]

    # Before: `ratio`, whatever the track held (a read depth here).
    assert _header("coverage", "web") == "chr\tstart\tend\tid\tvalue"
    assert _header("segments", "wisecondorx") == "chr\tstart\tend\tvalue"
