"""Bounded upload decode (``upload_safety``): an upload, or a package file read from disk, is
read into UTF-8 text with its size capped before and after gzip, so a crafted file cannot
exhaust a worker's memory (413), and a file that is not complete gzip or UTF-8 text is refused
(400), never read as the part that was there. gzip is told by its magic bytes, not its name.
"""

from __future__ import annotations

import gzip
import io

import pytest
from fastapi import HTTPException, UploadFile

from app.core.config import settings
from app.services.upload_safety import decode_upload_text, read_path_text_bounded
from backend.tests._bgzf import bgzip, bgzip_cut_after
from backend.tests._deadline import deadline


def _upload(data: bytes) -> UploadFile:
    return UploadFile(file=io.BytesIO(data))


@pytest.mark.asyncio
async def test_plain_text_under_cap_round_trips():
    text = "chrom\tstart\tend\nchr1\t1\t2\n"
    out = await decode_upload_text(_upload(text.encode()), kind="BED")
    assert out == text


@pytest.mark.asyncio
async def test_gzipped_text_is_decompressed():
    text = "##fileformat=VCFv4.2\n"
    out = await decode_upload_text(_upload(gzip.compress(text.encode())), kind="SV")
    assert out == text


@pytest.mark.asyncio
async def test_oversized_plain_upload_rejected(monkeypatch):
    monkeypatch.setattr(settings, "max_upload_bytes", 64)
    with pytest.raises(HTTPException) as exc:
        await decode_upload_text(_upload(b"x" * 1000), kind="BED")
    assert (exc.value.status_code, exc.value.detail) == (413, "BED upload exceeds the maximum allowed size")


@pytest.mark.asyncio
async def test_gzip_bomb_rejected_by_decompressed_cap(monkeypatch):
    # ~8 MiB of zeros compresses to a few KB; cap the decompressed size low so the
    # bomb is stopped at the cap instead of inflating fully into memory.
    monkeypatch.setattr(settings, "max_upload_bytes", 100 * 1024 * 1024)
    monkeypatch.setattr(settings, "max_decompressed_upload_bytes", 1 * 1024 * 1024)
    bomb = gzip.compress(b"\x00" * (8 * 1024 * 1024))
    assert len(bomb) < 1 * 1024 * 1024  # tiny compressed payload
    with pytest.raises(HTTPException) as exc:
        await decode_upload_text(_upload(bomb), kind="Reference")
    assert (exc.value.status_code, exc.value.detail) == (413, "Reference upload expands beyond the maximum allowed size")


@pytest.mark.asyncio
async def test_corrupt_gzip_rejected_as_bad_request():
    # gzip magic byte prefix but an invalid header/body.
    data = b"\x1f\x8b" + b"\x00" * 32
    with pytest.raises(HTTPException) as exc:
        await decode_upload_text(_upload(data), kind="TRGT")
    assert (exc.value.status_code, exc.value.detail) == (400, "TRGT file is not valid gzip")


@pytest.mark.asyncio
async def test_non_utf8_plain_rejected_as_bad_request():
    with pytest.raises(HTTPException) as exc:
        await decode_upload_text(_upload(b"\xff\xfe\x00bad"), kind="PED")
    assert (exc.value.status_code, exc.value.detail) == (400, "PED file must be UTF-8 text or gzipped UTF-8 text")


# ---------------------------------------------------------------------------
# read_path_text_bounded — the on-disk analogue used by package SV import.
# ---------------------------------------------------------------------------


def test_read_path_plain_file_round_trips(tmp_path):
    text = "##fileformat=VCFv4.2\nchr1\t1\t.\tA\tT\n"
    path = tmp_path / "sv.vcf"
    path.write_text(text, encoding="utf-8")
    assert read_path_text_bounded(path, kind="Package VCF") == text


def test_read_path_gzip_file_is_decompressed(tmp_path):
    text = "##fileformat=VCFv4.2\n"
    path = tmp_path / "sv.vcf.gz"
    path.write_bytes(gzip.compress(text.encode()))
    assert read_path_text_bounded(path, kind="Package VCF") == text


@pytest.mark.parametrize(
    ("name", "content", "cap"),
    [
        pytest.param("bomb.vcf.gz", lambda: gzip.compress(b"\x00" * (8 * 1024 * 1024)), 1, id="one member"),
        # gzip content under a name that is not .gz is still capped: gzip is told by its
        # magic bytes.
        pytest.param("bomb.vcf", lambda: gzip.compress(b"\x00" * (8 * 1024 * 1024)), 1, id="named as plain text"),
        # The cumulative decompressed size across members stays bounded, so a bomb split over
        # many members (1 MiB each, ~16 MiB in all) is refused like a single-member one.
        pytest.param(
            "bomb.vcf.gz", lambda: gzip.compress(b"\x00" * (1 * 1024 * 1024)) * 16, 4, id="split over many members"
        ),
    ],
)
def test_read_path_gzip_bomb_rejected_by_decompressed_cap(tmp_path, monkeypatch, name, content, cap):
    monkeypatch.setattr(settings, "max_upload_bytes", 100 * 1024 * 1024)
    monkeypatch.setattr(settings, "max_decompressed_upload_bytes", cap * 1024 * 1024)
    path = tmp_path / name
    path.write_bytes(content())
    assert path.stat().st_size < 1 * 1024 * 1024  # tiny on disk
    with pytest.raises(HTTPException) as exc:
        read_path_text_bounded(path, kind="Package VCF")
    assert (exc.value.status_code, exc.value.detail) == (
        413,
        "Package VCF upload expands beyond the maximum allowed size",
    )


def test_read_path_oversized_plain_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_bytes", 64)
    path = tmp_path / "big.vcf"
    path.write_bytes(b"x" * 1000)
    with pytest.raises(HTTPException) as exc:
        read_path_text_bounded(path, kind="Package VCF")
    assert (exc.value.status_code, exc.value.detail) == (413, "Package VCF exceeds the maximum allowed size")


def test_read_path_detects_gzip_by_magic_not_extension(tmp_path):
    # A .gz-named plain file must not be force-decompressed; a gzip-content file with a
    # non-.gz name is decompressed (and capped: test_read_path_gzip_bomb_rejected_by_decompressed_cap).
    text = "##fileformat=VCFv4.2\nchr1\t1\t.\tA\tT\n"
    plain = tmp_path / "plain.vcf.gz"
    plain.write_text(text, encoding="utf-8")
    compressed = tmp_path / "compressed.vcf"
    compressed.write_bytes(gzip.compress(text.encode()))

    assert read_path_text_bounded(plain, kind="Package VCF") == text
    assert read_path_text_bounded(compressed, kind="Package VCF") == text


# ---------------------------------------------------------------------------
# Concatenated gzip members (bgzip/BGZF). Real `.vcf.gz` from bcftools/bgzip is a
# series of gzip members; a single decompressobj stops after the first and drops
# the rest, silently truncating large VCFs to (typically) just the header.
# ---------------------------------------------------------------------------


def _concat_gzip_members(*chunks: bytes) -> bytes:
    """Concatenated independent gzip members -- the BGZF layout at the member level."""
    return b"".join(gzip.compress(chunk) for chunk in chunks)


@pytest.mark.asyncio
async def test_multi_member_gzip_upload_is_fully_decompressed():
    parts = [b"##fileformat=VCFv4.2\n", b"chr1\t1\t.\tA\tT\n", b"chr2\t2\t.\tC\tG\n"]
    out = await decode_upload_text(_upload(_concat_gzip_members(*parts)), kind="SV")
    assert out == b"".join(parts).decode()


def test_read_path_multi_member_gzip_is_fully_decompressed(tmp_path):
    parts = [b"##fileformat=VCFv4.2\n", b"chr1\t1\t.\tA\tT\n", b"chr2\t2\t.\tC\tG\n"]
    path = tmp_path / "sv.vcf.gz"
    path.write_bytes(_concat_gzip_members(*parts))
    assert read_path_text_bounded(path, kind="Package VCF") == b"".join(parts).decode()


# ---------------------------------------------------------------------------
# The gzip reader is bounded_download.gunzip_bounded, the one of the reference
# downloads; an upload answers its errors with 413 and 400.
# ---------------------------------------------------------------------------

_VCF_TEXT = "".join(f"chr1\t{position}\t.\tA\tT\n" for position in range(1, 2_001))


def test_read_path_reads_the_member_after_one_larger_than_a_step(tmp_path):
    # Two gzip files joined (`cat a.gz b.gz`), the first inflating past one
    # decompression step (1 MiB). A reader that took the second member from the
    # decompressor's unconsumed_tail and fed it, pass after pass, to the finished
    # decompressor would never return. The deadline turns that into a failure.
    first = b"##fileformat=VCFv4.2\n" + b"#" * (3 * 1024 * 1024) + b"\n"
    second = b"chr1\t1\t.\tA\tT\n"
    path = tmp_path / "sv.vcf.gz"
    path.write_bytes(gzip.compress(first) + gzip.compress(second))
    with deadline(30):
        text = read_path_text_bounded(path, kind="Package VCF")
    assert text == (first + second).decode()


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(gzip.compress(_VCF_TEXT.encode())[:-100], id="member cut short"),
        pytest.param(
            gzip.compress(_VCF_TEXT.encode()) + b"\x1f\x8b", id="cut-off header after it"
        ),
    ],
)
def test_read_path_gzip_cut_short_rejected_as_bad_request(tmp_path, data):
    # Refused, rather than read as the part that was there, as if it were the whole file.
    path = tmp_path / "sv.vcf.gz"
    path.write_bytes(data)
    with pytest.raises(HTTPException) as exc:
        read_path_text_bounded(path, kind="Package VCF")
    assert exc.value.status_code == 400
    assert exc.value.detail == "Package VCF file is not valid gzip"


@pytest.mark.asyncio
async def test_truncated_gzip_upload_rejected_as_bad_request():
    data = gzip.compress(_VCF_TEXT.encode())[:-100]
    with pytest.raises(HTTPException) as exc:
        await decode_upload_text(_upload(data), kind="SV")
    assert exc.value.status_code == 400
    assert exc.value.detail == "SV file is not valid gzip"


# ---------------------------------------------------------------------------
# A BGZF file must end with its end-of-file block (DATA-4, the owner's decision of
# 2026-10-28). Cut exactly where one of its blocks ends, a bgzip file is whole gzip: without
# that check the reader would return its first blocks as the file. A plain gzip file has no
# such block.
# ---------------------------------------------------------------------------

_BGZF_TEXT = (_VCF_TEXT * 4).encode()  # three BGZF blocks
_BGZF_CUT = "is not valid gzip: it is truncated (it does not end with BGZF's end-of-file block)"


def test_read_path_bgzf_cut_where_a_block_ends_rejected_as_bad_request(tmp_path):
    path = tmp_path / "sv.vcf.gz"
    path.write_bytes(bgzip_cut_after(_BGZF_TEXT, 2))
    with pytest.raises(HTTPException) as exc:
        read_path_text_bounded(path, kind="Package VCF")
    assert exc.value.status_code == 400
    assert exc.value.detail == f"Package VCF file {_BGZF_CUT}"


@pytest.mark.asyncio
async def test_bgzf_upload_cut_where_a_block_ends_rejected_as_bad_request():
    with pytest.raises(HTTPException) as exc:
        await decode_upload_text(_upload(bgzip_cut_after(_BGZF_TEXT, 1)), kind="SV")
    assert exc.value.status_code == 400
    assert exc.value.detail == f"SV file {_BGZF_CUT}"


@pytest.mark.asyncio
async def test_a_whole_bgzf_upload_and_a_plain_gzip_upload_are_read():
    assert await decode_upload_text(_upload(bgzip(_BGZF_TEXT)), kind="SV") == _BGZF_TEXT.decode()
    assert await decode_upload_text(_upload(gzip.compress(_BGZF_TEXT)), kind="SV") == _BGZF_TEXT.decode()
