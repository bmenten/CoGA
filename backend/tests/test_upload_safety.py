from __future__ import annotations

import gzip
import io

import pytest
from fastapi import HTTPException, UploadFile

from app.core.config import settings
from app.services.upload_safety import decode_upload_text, read_path_text_bounded


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
    assert exc.value.status_code == 413


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
    assert exc.value.status_code == 413


@pytest.mark.asyncio
async def test_corrupt_gzip_rejected_as_bad_request():
    # gzip magic byte prefix but an invalid header/body.
    data = b"\x1f\x8b" + b"\x00" * 32
    with pytest.raises(HTTPException) as exc:
        await decode_upload_text(_upload(data), kind="TRGT")
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_non_utf8_plain_rejected_as_bad_request():
    with pytest.raises(HTTPException) as exc:
        await decode_upload_text(_upload(b"\xff\xfe\x00bad"), kind="PED")
    assert exc.value.status_code == 400


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


def test_read_path_gzip_bomb_rejected_by_decompressed_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_bytes", 100 * 1024 * 1024)
    monkeypatch.setattr(settings, "max_decompressed_upload_bytes", 1 * 1024 * 1024)
    path = tmp_path / "bomb.vcf.gz"
    path.write_bytes(gzip.compress(b"\x00" * (8 * 1024 * 1024)))
    assert path.stat().st_size < 1 * 1024 * 1024  # tiny on disk
    with pytest.raises(HTTPException) as exc:
        read_path_text_bounded(path, kind="Package VCF")
    assert exc.value.status_code == 413


def test_read_path_oversized_plain_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_bytes", 64)
    path = tmp_path / "big.vcf"
    path.write_bytes(b"x" * 1000)
    with pytest.raises(HTTPException) as exc:
        read_path_text_bounded(path, kind="Package VCF")
    assert exc.value.status_code == 413


def test_read_path_detects_gzip_by_magic_not_extension(tmp_path, monkeypatch):
    # A .gz-named plain file must not be force-decompressed; a gzip-content file with a
    # non-.gz name must still be capped. Cap the decompressed size and confirm a gzip
    # bomb named ".vcf" is still rejected.
    monkeypatch.setattr(settings, "max_upload_bytes", 100 * 1024 * 1024)
    monkeypatch.setattr(settings, "max_decompressed_upload_bytes", 1 * 1024 * 1024)
    path = tmp_path / "bomb.vcf"  # gzip content, non-.gz name
    path.write_bytes(gzip.compress(b"\x00" * (8 * 1024 * 1024)))
    with pytest.raises(HTTPException) as exc:
        read_path_text_bounded(path, kind="Package VCF")
    assert exc.value.status_code == 413


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


def test_read_path_multi_member_gzip_bomb_rejected(tmp_path, monkeypatch):
    # The cumulative decompressed size across members must stay bounded so a bomb
    # split over many members is rejected just like a single-member one.
    monkeypatch.setattr(settings, "max_upload_bytes", 100 * 1024 * 1024)
    monkeypatch.setattr(settings, "max_decompressed_upload_bytes", 4 * 1024 * 1024)
    member = gzip.compress(b"\x00" * (1 * 1024 * 1024))  # 1 MiB decompressed per member
    path = tmp_path / "bomb.vcf.gz"
    path.write_bytes(member * 16)  # ~16 MiB across members, tiny on disk
    assert path.stat().st_size < 1 * 1024 * 1024
    with pytest.raises(HTTPException) as exc:
        read_path_text_bounded(path, kind="Package VCF")
    assert exc.value.status_code == 413
