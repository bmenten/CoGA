"""Bounded outbound download + gunzip (Refs #336).

Reference/gene/phenotype refreshes pull gzip files from external hosts and decompress
them in memory. These tests prove the caps hold: a tiny "bomb" that inflates to tens of
MiB is aborted at the ceiling instead of exhausting worker memory, an oversized transfer
is stopped mid-stream, and the optional-file (404) and upstream-error paths are preserved.
They also prove every member of a multi-member (bgzip) file is read, under one cap on
the output of all members, and that a truncated file or trailing bytes are refused.
"""
from __future__ import annotations

import gzip
import random
import struct
import zlib
from types import SimpleNamespace

import httpx
import pytest

from backend.app.services import bounded_download
from backend.app.services.bounded_download import (
    download_bounded_bytes,
    gunzip_bounded,
)
from backend.tests._deadline import deadline


# --- gunzip_bounded --------------------------------------------------------------------


def test_gunzip_bounded_roundtrips_valid_gzip() -> None:
    payload = b"gene\tdisease\n" * 5000
    assert gunzip_bounded(gzip.compress(payload), source="t") == payload


def test_gunzip_bounded_rejects_oversized_output() -> None:
    payload = b"x" * 10_000
    with pytest.raises(ValueError, match="expands beyond"):
        gunzip_bounded(gzip.compress(payload), max_bytes=1_000, source="t")


def test_gunzip_bounded_rejects_invalid_stream() -> None:
    with pytest.raises(ValueError, match="not valid gzip"):
        gunzip_bounded(b"this is not gzip", source="t")


def test_gunzip_bounded_halts_decompression_bomb_at_cap() -> None:
    # ~64 MiB of zeros compresses to a few KiB; the cap must abort long before it inflates.
    bomb = gzip.compress(b"\x00" * (64 << 20))
    assert len(bomb) < (1 << 20)  # compressed stays tiny
    with pytest.raises(ValueError, match="expands beyond"):
        gunzip_bounded(bomb, max_bytes=(1 << 20), source="bomb")


# --- every member of a multi-member gzip (bgzip) -----------------------------------------
#
# gzip allows members back to back, and bgzip writes nothing else. A decompressor stops at
# the end of the first member, so reading only one silently cuts the file short.

# The end-of-file block of the BGZF specification (SAM/BAM format, section 4.1.2): an
# empty member that bgzip writes last.
_BGZF_EOF_BLOCK = bytes.fromhex("1f8b08040000000000ff0600424302001b0003000000000000000000")
_VCF_LINE = b"chr1\t1000\t.\tA\tT\t50\tPASS\tDP=30\n"


def _bgzf_block(payload: bytes) -> bytes:
    """One BGZF block as bgzip writes it: a gzip member whose extra subfield "BC" holds
    the size of the whole block less one."""
    deflater = zlib.compressobj(6, zlib.DEFLATED, -zlib.MAX_WBITS)
    cdata = deflater.compress(payload) + deflater.flush()
    header = (
        b"\x1f\x8b\x08\x04"  # gzip magic, deflate, FEXTRA
        + b"\x00\x00\x00\x00\x00\xff"  # MTIME, XFL, OS (unknown)
        + struct.pack("<H2sHH", 6, b"BC", 2, 18 + len(cdata) + 8 - 1)  # XLEN, "BC" subfield
    )
    return header + cdata + struct.pack("<2I", zlib.crc32(payload), len(payload))


def test_gunzip_bounded_reads_every_member() -> None:
    first, second = b"first member\n", b"second member\n"
    data = gzip.compress(first) + gzip.compress(second)
    assert gunzip_bounded(data, source="t") == first + second


def test_gunzip_bounded_reads_a_bgzip_file_whole() -> None:
    assert _bgzf_block(b"") == _BGZF_EOF_BLOCK  # the builder writes real BGZF
    text = _VCF_LINE * 20_000  # 620,000 bytes
    # bgzip puts 0xFF00 bytes of input in each block: ten blocks here.
    blocks = [_bgzf_block(text[i : i + 0xFF00]) for i in range(0, len(text), 0xFF00)]
    assert len(blocks) == 10
    assert gunzip_bounded(b"".join(blocks) + _BGZF_EOF_BLOCK, source="t") == text
    # bgzip's output for an empty file is the end-of-file block alone: valid, and empty.
    assert gunzip_bounded(_BGZF_EOF_BLOCK, source="t") == b""


@pytest.mark.parametrize(
    "first",
    [
        # Compresses to a few KiB: read in steps cut by the output cap of one step.
        pytest.param(b"\x00" * (3 << 20), id="compressible"),
        # Does not compress: read in steps cut by the input slice of one step.
        pytest.param(random.Random(0).randbytes(3 << 20), id="incompressible"),
    ],
)
def test_gunzip_bounded_reads_the_member_after_one_larger_than_a_step(first: bytes) -> None:
    # The first member inflates past one decompression step (1 MiB), so it takes several
    # calls before the next member starts. A reader that then takes the next member from
    # the decompressor's unconsumed_tail keeps feeding it to the finished decompressor and
    # never returns: the deadline turns that into a failure.
    second = b"second member\n"
    data = gzip.compress(first) + gzip.compress(second)
    with deadline(30):
        assert gunzip_bounded(data, source="t") == first + second


def test_gunzip_bounded_cap_counts_the_output_of_every_member() -> None:
    data = b"".join(gzip.compress(bytes([65 + i]) * 1_000) for i in range(3))
    assert len(gunzip_bounded(data, max_bytes=3_000, source="t")) == 3_000
    with pytest.raises(ValueError, match="expands beyond"):
        gunzip_bounded(data, max_bytes=2_999, source="t")


def test_gunzip_bounded_halts_a_bomb_spread_over_many_members() -> None:
    bomb = gzip.compress(b"\x00" * (1 << 20)) * 64  # 64 MiB across members, tiny compressed
    assert len(bomb) < (1 << 20)
    with pytest.raises(ValueError, match="expands beyond"):
        gunzip_bounded(bomb, max_bytes=(4 << 20), source="bomb")


def test_gunzip_bounded_holds_no_more_than_the_cap(monkeypatch) -> None:
    # Each step asks zlib for no more than what is left under the cap plus one byte, so a
    # bomb is refused before more than max_bytes + 1 bytes were inflated.
    inflated: list[int] = []

    class _CountingDecompressor:
        def __init__(self, *args, **kwargs) -> None:
            self._inner = zlib.decompressobj(*args, **kwargs)

        def decompress(self, data, max_length=0):
            out = self._inner.decompress(data, max_length)
            inflated.append(len(out))
            return out

        def __getattr__(self, name):
            return getattr(self._inner, name)

    monkeypatch.setattr(
        bounded_download,
        "zlib",
        SimpleNamespace(decompressobj=_CountingDecompressor, error=zlib.error),
    )
    bomb = gzip.compress(b"\x00" * (8 << 20))
    with pytest.raises(ValueError, match="expands beyond"):
        gunzip_bounded(bomb, max_bytes=100_000, source="bomb")
    assert sum(inflated) <= 100_001


@pytest.mark.parametrize(
    "trailer",
    [
        pytest.param(b"not gzip", id="garbage"),
        pytest.param(b"\x00" * 8, id="zero padding"),
        pytest.param(b"\x1f\x8b", id="cut-off header"),
    ],
)
def test_gunzip_bounded_refuses_bytes_after_the_last_member(trailer: bytes) -> None:
    # Whatever follows the last member must be a complete member itself; the stdlib's
    # gzip.decompress refuses garbage too (it lets zero padding through).
    with pytest.raises(ValueError, match="not valid gzip"):
        gunzip_bounded(gzip.compress(b"complete member\n") + trailer, source="t")


_INCOMPRESSIBLE_MEMBER = gzip.compress(random.Random(1).randbytes(4_000))


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(b"", id="empty"),
        pytest.param(_INCOMPRESSIBLE_MEMBER[:2_000], id="inside the deflate data"),
        pytest.param(gzip.compress(b"one member\n")[:-4], id="inside the trailer"),
        pytest.param(
            gzip.compress(b"first\n") + _INCOMPRESSIBLE_MEMBER[:2_000],
            id="inside the second member",
        ),
    ],
)
def test_gunzip_bounded_refuses_a_truncated_member(data: bytes) -> None:
    # A file cut short is refused, never returned as the part that was there.
    with pytest.raises(ValueError, match="not valid gzip: it is truncated"):
        gunzip_bounded(data, source="t")


# --- download_bounded_bytes ------------------------------------------------------------


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_download_bounded_bytes_returns_full_body() -> None:
    async with _client(lambda req: httpx.Response(200, content=b"hello world")) as client:
        assert await download_bounded_bytes(client, "https://x/y", source="t") == b"hello world"


@pytest.mark.asyncio
async def test_download_bounded_bytes_aborts_over_cap() -> None:
    async with _client(lambda req: httpx.Response(200, content=b"x" * 5_000)) as client:
        with pytest.raises(ValueError, match="exceeds"):
            await download_bounded_bytes(client, "https://x/y", max_bytes=1_000, source="t")


@pytest.mark.asyncio
async def test_download_bounded_bytes_optional_404_returns_none() -> None:
    async with _client(lambda req: httpx.Response(404)) as client:
        assert await download_bounded_bytes(client, "https://x/y", none_on_404=True) is None


@pytest.mark.asyncio
async def test_download_bounded_bytes_404_without_flag_raises() -> None:
    async with _client(lambda req: httpx.Response(404)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await download_bounded_bytes(client, "https://x/y")


@pytest.mark.asyncio
async def test_download_bounded_bytes_5xx_raises() -> None:
    async with _client(lambda req: httpx.Response(503)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await download_bounded_bytes(client, "https://x/y")
