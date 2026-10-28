"""Bounded reading/decompression for user uploads.

Several admin upload endpoints used to ``await file.read()`` the whole upload into
memory and then ``gzip.decompress()`` it — also fully in memory — with no size cap.
A small, highly compressible ``.gz`` ("decompression bomb") could therefore inflate
to exhaust the worker's memory (DoS). ``decode_upload_text`` reads the upload and
decompresses it with explicit caps on both the bytes read and the decompressed size,
rejecting oversized input with HTTP 413 instead of buffering it without limit.

The small-variant ingestion path streams line-by-line via ``gzip.open`` and is already
memory-safe; this helper brings the remaining full-buffer decoders (SV, reference,
BED, repeat-expansion, PED) up to the same standard. A BGZF file that does not end with
BGZF's end-of-file block is refused as cut short (400), here and by the streaming readers.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, UploadFile

from ..core.config import settings
from .bounded_download import GzipTooLargeError, InvalidGzipError, TruncatedBgzfError, gunzip_bounded

_GZIP_MAGIC = b"\x1f\x8b"
_READ_CHUNK = 1 << 20  # 1 MiB per read


async def _read_upload_bounded(file: UploadFile, max_bytes: int, *, kind: str) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_READ_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"{kind} upload exceeds the maximum allowed size",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _gunzip_bounded(data: bytes, max_bytes: int, *, kind: str) -> bytes:
    # The reference downloads' reader: every gzip member (a bgzip `.vcf.gz` is many
    # blocks of at most 64 KiB, each a member), the cap on the output of all members
    # together, and a refusal of anything that is not complete gzip (a member cut short,
    # bytes after the last member, a BGZF file without its end-of-file block). An upload
    # answers its errors with 413 and 400.
    try:
        return gunzip_bounded(data, max_bytes=max_bytes, source=f"{kind} file")
    except GzipTooLargeError as exc:
        raise HTTPException(
            status_code=413,
            detail=f"{kind} upload expands beyond the maximum allowed size",
        ) from exc
    except TruncatedBgzfError as exc:
        # Says why: gzip tools read a BGZF file cut where one of its blocks ends without a
        # word (DATA-4).
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except InvalidGzipError as exc:
        raise HTTPException(
            status_code=400, detail=f"{kind} file is not valid gzip"
        ) from exc


def _decoded_text(raw: bytes, max_decompressed_bytes: int, *, kind: str) -> str:
    """``raw`` as UTF-8 text, gunzipped first (bounded) when its magic bytes say gzip."""
    if raw[:2] == _GZIP_MAGIC:
        raw = _gunzip_bounded(raw, max_decompressed_bytes, kind=kind)
    try:
        return raw.decode()
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{kind} file must be UTF-8 text or gzipped UTF-8 text",
        ) from exc


def _read_stream_bounded(handle, max_bytes: int, *, kind: str) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = handle.read(_READ_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"{kind} exceeds the maximum allowed size",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def read_path_text_bounded(
    path: Path,
    *,
    kind: str,
    max_bytes: int | None = None,
    max_decompressed_bytes: int | None = None,
) -> str:
    """Read an on-disk file (optionally gzip) into UTF-8 text with bounded memory.

    The on-disk analogue of :func:`decode_upload_text` for package assets that are
    already staged to the filesystem. Caps both the bytes read (``MAX_UPLOAD_BYTES``)
    and the decompressed size (``MAX_DECOMPRESSED_UPLOAD_BYTES``) so a crafted package
    ``.gz`` (decompression bomb) can't inflate to exhaust worker memory; oversized
    input is rejected with HTTP 413. gzip framing is detected by magic bytes, so the
    cap applies regardless of the file's name/extension.
    """
    max_bytes = settings.max_upload_bytes if max_bytes is None else max_bytes
    max_decompressed_bytes = (
        settings.max_decompressed_upload_bytes
        if max_decompressed_bytes is None
        else max_decompressed_bytes
    )
    with open(path, "rb") as handle:
        raw = _read_stream_bounded(handle, max_bytes, kind=kind)
    return _decoded_text(raw, max_decompressed_bytes, kind=kind)


async def decode_upload_text(file: UploadFile, *, kind: str) -> str:
    """Read an upload (optionally gzip) into UTF-8 text with bounded memory.

    Caps both the bytes read (``MAX_UPLOAD_BYTES``) and the decompressed size
    (``MAX_DECOMPRESSED_UPLOAD_BYTES``); oversized input is rejected with HTTP 413.
    Raises 400 if the content is neither valid UTF-8 text nor valid gzip.
    """
    raw = await _read_upload_bounded(file, settings.max_upload_bytes, kind=kind)
    return _decoded_text(raw, settings.max_decompressed_upload_bytes, kind=kind)
