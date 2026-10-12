"""Bounded outbound download + gzip decompression for reference-data fetches.

The reference / gene / phenotype refresh paths pull files from external hosts and
``gzip.decompress()`` them fully in memory. A compromised, MITM'd, or simply misbehaving
upstream could return a decompression bomb (a tiny ``.gz`` that inflates to gigabytes) and
OOM the worker. These helpers cap both the compressed bytes read and the decompressed
size. Unlike ``upload_safety`` (which handles CLIENT uploads and raises HTTP 4xx), an
oversized/invalid response here is a server-side / upstream fault, so we raise
``ValueError`` for the caller to surface as a 5xx / failed refresh.

The ceilings are generous — real reference/gene/phenotype files are tens-to-hundreds of
MB — so they only stop a runaway from exhausting worker memory, never a legitimate file.
"""
from __future__ import annotations

import zlib

import httpx

_READ_CHUNK = 1 << 20  # 1 MiB per streamed chunk
_DECOMP_STEP = 1 << 20  # bound decompressed output produced per step
_INFLATE_SLICE = 1 << 16  # compressed bytes handed to zlib per step

# OOM-protection ceilings (not tight limits).
MAX_DOWNLOAD_BYTES = 1 << 30  # 1 GiB of compressed/raw response
MAX_DECOMPRESSED_BYTES = 4 << 30  # 4 GiB after gunzip


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

    Raises ``ValueError`` when the output passes ``max_bytes`` ("expands beyond"), and
    unless ``data`` is one or more complete gzip members and nothing else ("is not valid
    gzip"): bytes that are not gzip, a corrupt member (bad deflate data, CRC or length), an
    empty input or a member cut short ("is truncated"), and bytes after the last member
    that do not form a complete member of their own (trailing garbage or padding).
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
                    raise ValueError(f"{label} expands beyond {max_bytes} bytes")
                if member.eof:
                    # The member ends in this slice; unused_data is what follows it. Its
                    # unconsumed_tail can repeat those bytes then, so it is not read here.
                    pos += len(piece) - len(member.unused_data)
                    break
                consumed = len(piece) - len(member.unconsumed_tail)
                if not consumed and not produced:
                    # No input left, and the member has not ended.
                    raise ValueError(f"{label} is not valid gzip: it is truncated")
                pos += consumed
            if pos == len(view):
                return bytes(out)
    except zlib.error as exc:
        raise ValueError(f"{label} is not valid gzip") from exc
