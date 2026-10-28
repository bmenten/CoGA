"""BGZF files as bgzip writes them, built in the tests.

BGZF (the SAM/BAM format specification, section 4.1) is gzip in blocks: each block is a
gzip member of at most 64 KiB whose extra field holds the subfield ``BC`` (the block's size
less one), and the file ends with an empty block, the end-of-file block. Import as
``from backend.tests._bgzf import bgzip``.
"""

from __future__ import annotations

import struct
import zlib

# The end-of-file block of the specification (section 4.1.2), as bgzip writes it last.
BGZF_EOF_BLOCK = bytes.fromhex("1f8b08040000000000ff0600424302001b0003000000000000000000")
# bgzip puts at most this many bytes of input in a block.
BGZF_BLOCK_INPUT = 0xFF00


def bgzf_block(payload: bytes, *, extra: bytes = b"") -> bytes:
    """One BGZF block holding ``payload``: a gzip member whose extra field holds ``extra``
    (other subfields, none as bgzip writes it) and then the subfield ``BC``."""
    deflater = zlib.compressobj(6, zlib.DEFLATED, -zlib.MAX_WBITS)
    cdata = deflater.compress(payload) + deflater.flush()
    xlen = len(extra) + 6
    header = (
        b"\x1f\x8b\x08\x04"  # gzip magic, deflate, FEXTRA
        + b"\x00\x00\x00\x00\x00\xff"  # MTIME, XFL, OS (unknown)
        + struct.pack("<H", xlen)
        + extra
        + struct.pack("<2sHH", b"BC", 2, 12 + xlen + len(cdata) + 8 - 1)
    )
    return header + cdata + struct.pack("<2I", zlib.crc32(payload), len(payload) & 0xFFFFFFFF)


def bgzf_blocks(data: bytes) -> list[bytes]:
    """``data`` in BGZF blocks of ``BGZF_BLOCK_INPUT`` bytes of input each, as bgzip cuts it,
    without the end-of-file block."""
    return [bgzf_block(data[start : start + BGZF_BLOCK_INPUT]) for start in range(0, len(data), BGZF_BLOCK_INPUT)]


def bgzip(data: bytes) -> bytes:
    """``data`` as bgzip writes it: its blocks, then the end-of-file block."""
    return b"".join(bgzf_blocks(data)) + BGZF_EOF_BLOCK


def bgzip_cut_after(data: bytes, blocks: int) -> bytes:
    """``data`` bgzipped and cut exactly where its block number ``blocks`` ends: a whole gzip
    file, which an interrupted copy leaves, short of the rest of ``data`` and of the
    end-of-file block."""
    written = bgzf_blocks(data)
    assert 0 < blocks <= len(written), (blocks, len(written))
    return b"".join(written[:blocks])
