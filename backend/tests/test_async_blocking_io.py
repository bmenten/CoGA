"""The CRAM manifest resolves samples concurrently, off the event loop, while preserving order
and dropping duplicates.
"""
import threading
from types import SimpleNamespace

import pytest

from app.routers import cram


# --------------------------------------------------------------------------- #
# cram manifest                                                               #
# --------------------------------------------------------------------------- #
async def _nothing_recorded(session, sample_ids):
    """No sample records where its import put its reads."""
    return {}


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
    monkeypatch.setattr(cram, "_recorded_alignments", _nothing_recorded)

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
    monkeypatch.setattr(cram, "_recorded_alignments", _nothing_recorded)
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
