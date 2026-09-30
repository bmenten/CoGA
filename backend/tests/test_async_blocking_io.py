"""Tests for the #11 async-offloading / caching changes:
- cram manifest resolves samples concurrently while preserving order + dedup,
- object_storage.download_prefix downloads every object under a prefix.
"""
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core import object_storage as s
from app.routers import cram


# --------------------------------------------------------------------------- #
# cram manifest                                                               #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_get_alignment_manifest_dedups_preserves_order_and_filters(monkeypatch):
    async def fake_sample_ids(session, family_id, user):
        return {"S1", "S2", "S3"}  # S4 is not part of the family

    resolved: list[str] = []
    resolved_lock = threading.Lock()

    def fake_resolve(family_id, sample_id, recorded=None):
        with resolved_lock:
            resolved.append(sample_id)
        if sample_id == "S2":
            return None  # sample has no alignment data
        return SimpleNamespace(sample_id=sample_id)

    monkeypatch.setattr(cram, "_get_accessible_family_sample_ids", fake_sample_ids)
    monkeypatch.setattr(cram, "_resolve_alignment_manifest_entry", fake_resolve)

    result = await cram.get_alignment_manifest(
        "FAM1",
        sample_ids=["S3", "S1", "S1", "S2", "S4"],
        session=object(),
        user=object(),
    )

    # asyncio.gather preserves input order; S4 (out of family) dropped, S1 deduped,
    # S2 (resolved to None) filtered out.
    assert [entry.sample_id for entry in result] == ["S3", "S1"]
    # Each retained, in-family, deduped sample is resolved exactly once (order across
    # threads is non-deterministic, so compare as a set).
    assert set(resolved) == {"S1", "S2", "S3"}
    assert len(resolved) == 3


@pytest.mark.asyncio
async def test_get_cram_header_offloads_blocking_read(monkeypatch):
    async def fake_ensure(*_args, **_kwargs):
        return None

    read_args: dict[str, tuple[str, str]] = {}

    def fake_read(family_id, sample_id, recorded=None):
        read_args["args"] = (family_id, sample_id)
        return {"SQ": [{"SN": "chr1", "LN": 1000}]}

    offloaded: list[str] = []
    real_to_thread = cram.asyncio.to_thread

    async def spy_to_thread(func, *args, **kwargs):
        offloaded.append(getattr(func, "__name__", repr(func)))
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(cram, "_ensure_accessible_alignment_sample", fake_ensure)
    monkeypatch.setattr(cram, "_read_alignment_header", fake_read)
    monkeypatch.setattr(cram.asyncio, "to_thread", spy_to_thread)

    header = await cram.get_cram_header(
        family_id="F1",
        sample_id="S1",
        session=object(),
        user=object(),
    )

    assert header == {"SQ": [{"SN": "chr1", "LN": 1000}]}
    assert read_args["args"] == ("F1", "S1")
    # The blocking pysam read is offloaded off the event loop via asyncio.to_thread,
    # not awaited inline (the offloaded callable is the patched _read_alignment_header).
    assert offloaded == ["fake_read"]


# --------------------------------------------------------------------------- #
# object_storage.download_prefix                                              #
# --------------------------------------------------------------------------- #
class _FakePaginator:
    def __init__(self, pages):
        self._pages = pages

    def paginate(self, Bucket, Prefix):
        return list(self._pages)


class _FakeClient:
    def __init__(self, pages):
        self._pages = pages
        self.downloaded: list[tuple[str, str]] = []
        self._lock = threading.Lock()

    def get_paginator(self, name):
        return _FakePaginator(self._pages)

    def download_file(self, bucket, key, target):
        with self._lock:
            self.downloaded.append((key, target))
        Path(target).write_text("x")


def test_download_prefix_downloads_all_objects(monkeypatch, tmp_path):
    pages = [
        {
            "Contents": [
                {"Key": "fam/F1/a.vcf.gz"},
                {"Key": "fam/F1/sub/b.cram"},
                {"Key": "fam/F1/"},  # directory marker -> skipped
            ]
        },
        {"Contents": [{"Key": "fam/F1/c.bam"}]},
    ]
    client = _FakeClient(pages)
    monkeypatch.setattr(s, "_s3_client", lambda: client)

    count = s.download_prefix("s3://bucket/fam/F1", tmp_path)

    assert count == 3
    assert (tmp_path / "a.vcf.gz").read_text() == "x"
    assert (tmp_path / "sub" / "b.cram").read_text() == "x"
    assert (tmp_path / "c.bam").read_text() == "x"
    assert {key for key, _ in client.downloaded} == {
        "fam/F1/a.vcf.gz",
        "fam/F1/sub/b.cram",
        "fam/F1/c.bam",
    }


def test_download_prefix_empty_returns_zero(monkeypatch, tmp_path):
    client = _FakeClient([{"Contents": []}])
    monkeypatch.setattr(s, "_s3_client", lambda: client)

    assert s.download_prefix("s3://bucket/fam/F1", tmp_path) == 0
    assert client.downloaded == []
