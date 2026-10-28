"""Bounded outbound download + gzip decompression for reference-data fetches.

The reference / gene / phenotype refresh paths pull files from external hosts and
``gzip.decompress()`` them fully in memory. A compromised, MITM'd, or simply misbehaving
upstream could return a decompression bomb (a tiny ``.gz`` that inflates to gigabytes) and
OOM the worker. These helpers cap both the compressed bytes read and the decompressed
size. Unlike ``upload_safety`` (which handles CLIENT uploads and raises HTTP 4xx), an
oversized/invalid response here is a server-side / upstream fault, so we raise
``ValueError`` for the caller to surface as a 5xx / failed refresh. ``upload_safety``
gunzips uploads with the same ``gunzip_bounded`` and answers its two errors, which are
``ValueError`` subclasses, with 413 and 400.

The ceilings are generous — real reference/gene/phenotype files are tens-to-hundreds of
MB — so they only stop a runaway from exhausting worker memory, never a legitimate file.

A BGZF file (bgzip's ``.gz``) must end with BGZF's end-of-file block: gzip has no end mark,
so one cut exactly where one of its blocks ends reads as a whole file. ``gunzip_bounded``
refuses it, and the readers that stream a file refuse it before they read it
(``require_bgzf_file_end``, ``require_bgzf_path_end``).
"""
from __future__ import annotations

import os
from typing import BinaryIO
import zlib

import httpx

_READ_CHUNK = 1 << 20  # 1 MiB per streamed chunk
_DECOMP_STEP = 1 << 20  # bound decompressed output produced per step
_INFLATE_SLICE = 1 << 16  # compressed bytes handed to zlib per step

# OOM-protection ceilings (not tight limits).
MAX_DOWNLOAD_BYTES = 1 << 30  # 1 GiB of compressed/raw response
MAX_DECOMPRESSED_BYTES = 4 << 30  # 4 GiB after gunzip

# The empty block bgzip, htslib and every other BGZF writer end a file with: the end-of-file
# marker of the SAM/BAM format specification (section 4.1.2), written "so that unintended
# file truncation can be easily detected".
BGZF_EOF_BLOCK = bytes.fromhex("1f8b08040000000000ff0600424302001b0003000000000000000000")
# A gzip member's header up to its extra field: ID1 ID2 CM FLG, MTIME (4), XFL OS, XLEN (2).
_GZIP_HEADER_FIXED = 12
_GZIP_FLAG_EXTRA = 0x04


class GzipTooLargeError(ValueError):
    """The decompressed output passes the cap."""


class InvalidGzipError(ValueError):
    """The bytes are not one or more complete gzip members and nothing else."""


class TruncatedBgzfError(InvalidGzipError, EOFError):
    """A BGZF file that does not end with BGZF's end-of-file block: cut short, perhaps exactly
    where one of its blocks ends, which gzip alone cannot tell from a whole file (DATA-4).

    It is an ``EOFError`` too, as Python's gzip module raises for a member cut short, so every
    reader refuses it where it refuses a truncated gzip file."""


def _is_bgzf(head: bytes) -> bool:
    """Whether ``head``, the start of a file, is a BGZF block's header: a gzip member whose
    extra field holds the subfield ``BC`` (SI1 66, SI2 67, two bytes long), the block's size.
    ``head`` holds the header's extra field whole, or the file is cut inside it."""
    if len(head) < _GZIP_HEADER_FIXED or head[:3] != b"\x1f\x8b\x08" or not head[3] & _GZIP_FLAG_EXTRA:
        return False
    extra_length = int.from_bytes(head[10:12], "little")
    extra = head[_GZIP_HEADER_FIXED : _GZIP_HEADER_FIXED + extra_length]
    position = 0
    while position + 4 <= len(extra):
        length = int.from_bytes(extra[position + 2 : position + 4], "little")
        if extra[position : position + 2] == b"BC" and length == 2:
            return True
        position += 4 + length
    return False


def _require_bgzf_end(head: bytes, tail: bytes, *, label: str) -> None:
    if _is_bgzf(head) and not tail.endswith(BGZF_EOF_BLOCK):
        raise TruncatedBgzfError(
            f"{label} is not valid gzip: it is truncated (it does not end with BGZF's end-of-file block)"
        )


def require_bgzf_file_end(handle: BinaryIO, *, label: str) -> None:
    """Refuse a BGZF file whose last block is not BGZF's end-of-file block
    (``TruncatedBgzfError``), from the seekable binary ``handle``: its first header and its
    last 28 bytes are read, and ``handle`` is put back where it was. A file that is not BGZF
    passes, plain gzip included: it has no such block.

    For a reader that streams the file, before it reads it: its end is known before anything
    of it is stored. gzip reads a BGZF file cut exactly where one of its blocks ends as a
    whole file, the blocks after the cut gone without a word."""
    start = handle.tell()
    try:
        handle.seek(0)
        head = handle.read(_GZIP_HEADER_FIXED)
        if len(head) == _GZIP_HEADER_FIXED and head[3] & _GZIP_FLAG_EXTRA:
            head += handle.read(int.from_bytes(head[10:12], "little"))
        if not _is_bgzf(head):
            return
        size = handle.seek(0, os.SEEK_END)
        handle.seek(max(0, size - len(BGZF_EOF_BLOCK)))
        tail = handle.read(len(BGZF_EOF_BLOCK))
    finally:
        handle.seek(start)
    _require_bgzf_end(head, tail, label=label)


def require_bgzf_path_end(path: str | os.PathLike[str], *, label: str) -> None:
    """``require_bgzf_file_end`` for the file at ``path``."""
    with open(path, "rb") as handle:
        require_bgzf_file_end(handle, label=label)


async def download_bounded_bytes(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
    source: str | None = None,
    none_on_404: bool = False,
) -> bytes | None:
    """Stream a GET into memory, aborting if it exceeds ``max_bytes``.

    Streaming (vs ``response.content``) means an oversized body is stopped mid-transfer
    instead of being buffered whole first. ``none_on_404`` returns None for a 404 (the
    "optional file" pattern). Raises ``httpx.HTTPError`` on transport/status errors (the
    caller keeps its own upstream-error handling) and ``ValueError`` when the cap trips.
    """
    label = source or url
    chunks: list[bytes] = []
    total = 0
    async with client.stream("GET", url) as response:
        if none_on_404 and response.status_code == 404:
            return None
        response.raise_for_status()
        async for chunk in response.aiter_bytes(_READ_CHUNK):
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"{label} download exceeds {max_bytes} bytes")
            chunks.append(chunk)
    return b"".join(chunks)


def gunzip_bounded(
    data: bytes, *, max_bytes: int = MAX_DECOMPRESSED_BYTES, source: str | None = None
) -> bytes:
    """Gzip-decompress every member of ``data``, with a hard cap on the total OUTPUT.

    A gzip file may hold several members back to back, and bgzip writes nothing else:
    blocks of at most 64 KiB, each its own member, closed by an empty end-of-file member.
    A zlib decompressor stops at the end of one member, so each member gets its own and
    their output is joined: a bgzipped upstream file is read whole, not cut off after its
    first block.

    The cap counts the output of all members together. ``decompress(..., max_length)`` is
    never asked for more than what is left under the cap plus one byte, so a decompression
    bomb, in one member or spread over many, halts at the cap: at most ``max_bytes + 1``
    bytes are ever held. zlib gets the input in slices of at most 64 KiB, so what it copies
    out per step (the input it did not use, the bytes after a member) stays small and a
    file of many members is read in linear time.

    Raises ``GzipTooLargeError`` when the output passes ``max_bytes`` ("expands beyond"),
    and ``InvalidGzipError`` unless ``data`` is one or more complete gzip members and
    nothing else ("is not valid gzip"): bytes that are not gzip, a corrupt member (bad
    deflate data, CRC or length), an empty input or a member cut short ("is truncated"),
    and bytes after the last member that do not form a complete member of their own
    (trailing garbage or padding). Both are ``ValueError``. A plain gzip file cut exactly
    between two members cannot be told from a whole one: gzip has no end-of-file mark. A
    BGZF file can, and must end with BGZF's end-of-file block: one whose last member is not
    that block is refused as truncated (``TruncatedBgzfError``, an ``InvalidGzipError``;
    the owner's decision of 2026-10-28, DATA-4).
    """
    label = source or "gzip stream"
    view = memoryview(data)
    out = bytearray()
    pos = 0  # first byte of ``data`` that zlib has not consumed
    try:
        while True:
            member = zlib.decompressobj(wbits=31)  # gzip framing, one member
            while True:
                piece = view[pos : pos + _INFLATE_SLICE]
                # Never 0: zlib reads max_length=0 as "no limit".
                room = max(1, min(_DECOMP_STEP, max_bytes - len(out) + 1))
                produced = member.decompress(piece, room)
                out += produced
                if len(out) > max_bytes:
                    raise GzipTooLargeError(f"{label} expands beyond {max_bytes} bytes")
                if member.eof:
                    # The member ends in this slice; unused_data is what follows it. Its
                    # unconsumed_tail can repeat those bytes then, so it is not read here.
                    pos += len(piece) - len(member.unused_data)
                    break
                consumed = len(piece) - len(member.unconsumed_tail)
                if not consumed and not produced:
                    # No input left, and the member has not ended.
                    raise InvalidGzipError(f"{label} is not valid gzip: it is truncated")
                pos += consumed
            if pos == len(view):
                break
    except zlib.error as exc:
        raise InvalidGzipError(f"{label} is not valid gzip") from exc
    # Every member is whole: a BGZF file's last must be the end-of-file block.
    _require_bgzf_end(data, data, label=label)
    return bytes(out)
