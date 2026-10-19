"""The report code of a signed analysis record: the code the EPD report quotes.

CoGA's sign-out is the analyst's analytical sign-off: it closes and freezes the analysis.
The medical sign-off of the patient report happens in the hospital's EPD, and the EPD report
refers to the frozen record, one to one, by its report code (owner decision of 2026-10-14).

The code is derived from fields of the record that never change, so no column holds it::

    FFFFFF-V-HHHC        e.g. 7KQM2X-2-4PHT

* ``FFFFFF``: six symbols from the SHA-256 of the family identifier, the same in every
  version of the family's record, so the codes of an amendment and of the record it amends
  start alike;
* ``V``: the record's version, in decimal;
* ``HHH``: three symbols from the record's content hash, which tie the code to the content
  that was signed;
* ``C``: a check symbol over all the others (the Damm algorithm over GF(32)). It detects
  every mistyped symbol and every swap of two neighbouring symbols, so a code copied wrong
  is refused as a typo instead of finding another record.

The symbols are Crockford's base 32: the digits and the capitals without I, L, O and U.
Whoever reads a code back may type an I or L for a 1 and an O for a 0, in any case, with or
without the hyphens: :func:`parse_report_code` reads them as meant. A code is at most 16
characters up to version 9999.

The code is unique: a sign-out whose code another record already has signs again a
microsecond later, which changes its content hash and so its code
(``report_signout_service.sign_out_report``).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

# Crockford's base 32: value = index.
REPORT_CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_VALUES = {symbol: value for value, symbol in enumerate(REPORT_CODE_ALPHABET)}
# What a reader may type for a symbol: Crockford's aliases.
_ALIASES = str.maketrans({"I": "1", "L": "1", "O": "0"})
_SEPARATORS = re.compile(r"[\s\-]+")

FAMILY_SYMBOLS = 6
HASH_SYMBOLS = 3
# The shortest code: six family symbols, a one-digit version, three hash symbols, a check.
_MIN_LENGTH = FAMILY_SYMBOLS + 1 + HASH_SYMBOLS + 1

# x^5 + x^2 + 1, a primitive polynomial of GF(32).
_GF32_MODULUS = 0b100101
# The Damm operation x * y = A.x + y over GF(32) is a totally anti-symmetric quasigroup for
# any A other than 0 and 1.
_DAMM_A = 2


class ReportCodeError(ValueError):
    """A string that is not a report code, with why in words a reader can act on."""


@dataclass(frozen=True, slots=True)
class ParsedReportCode:
    """A report code as read: its canonical form and its parts."""

    code: str
    family_part: str
    version: int
    hash_part: str


def _gf32_multiply(left: int, right: int) -> int:
    product = 0
    while right:
        if right & 1:
            product ^= left
        right >>= 1
        left <<= 1
        if left & 0b100000:
            left ^= _GF32_MODULUS
    return product


def _damm_interim(symbols: str) -> int:
    interim = 0
    for symbol in symbols:
        interim = _gf32_multiply(_DAMM_A, interim) ^ _VALUES[symbol]
    return interim


def check_symbol(symbols: str) -> str:
    """The symbol that makes the Damm check of ``symbols`` followed by it come out at zero."""
    return REPORT_CODE_ALPHABET[_gf32_multiply(_DAMM_A, _damm_interim(symbols))]


def _base32(value: int, symbols: int) -> str:
    return "".join(
        REPORT_CODE_ALPHABET[(value >> (5 * (symbols - 1 - index))) & 0b11111]
        for index in range(symbols)
    )


def family_part(family_identifier: str) -> str:
    """The six symbols every version of a family's record shares."""
    digest = hashlib.sha256(family_identifier.encode("utf-8")).digest()
    return _base32(int.from_bytes(digest[:4], "big") >> 2, FAMILY_SYMBOLS)


def hash_part(content_hash: str) -> str | None:
    """The three symbols from the first 15 bits of a content hash; None when it is not one."""
    if not isinstance(content_hash, str) or not re.fullmatch(r"[0-9a-fA-F]{4,}", content_hash):
        return None
    return _base32(int(content_hash[:4], 16) >> 1, HASH_SYMBOLS)


def content_hash_prefix(hash_symbols: str) -> str:
    """The first three hex digits every content hash with these hash symbols starts with."""
    value = 0
    for symbol in hash_symbols:
        value = (value << 5) | _VALUES[symbol]
    return format(value >> 3, "03x")


def report_code(family_identifier: str | None, version: int | None, content_hash: str | None) -> str | None:
    """The record's report code, or None when a field it is made from is missing."""
    if not family_identifier or not isinstance(version, int) or version < 1:
        return None
    symbols = hash_part(content_hash or "")
    if symbols is None:
        return None
    family = family_part(family_identifier)
    body = f"{family}{version}{symbols}"
    return f"{family}-{version}-{symbols}{check_symbol(body)}"


def parse_report_code(value: str) -> ParsedReportCode:
    """Read a report code as a reader typed it, or raise :class:`ReportCodeError`."""
    compact = _SEPARATORS.sub("", str(value or "")).upper().translate(_ALIASES)
    if not compact:
        raise ReportCodeError("Enter a report code.")
    unknown = sorted({symbol for symbol in compact if symbol not in _VALUES})
    if unknown:
        raise ReportCodeError(
            f"A report code holds only digits and the letters A–Z without I, L, O and U; "
            f"this one also holds {', '.join(unknown)}."
        )
    if len(compact) < _MIN_LENGTH:
        raise ReportCodeError("This is too short to be a report code.")
    family = compact[:FAMILY_SYMBOLS]
    version_digits = compact[FAMILY_SYMBOLS:-(HASH_SYMBOLS + 1)]
    symbols = compact[-(HASH_SYMBOLS + 1):-1]
    if not version_digits.isdigit() or version_digits.startswith("0"):
        raise ReportCodeError("This is not a report code: its middle part is not a version number.")
    if _damm_interim(compact) != 0:
        raise ReportCodeError(
            "This report code does not check out: a symbol was mistyped or two were swapped."
        )
    version = int(version_digits)
    return ParsedReportCode(
        code=f"{family}-{version}-{symbols}{compact[-1]}",
        family_part=family,
        version=version,
        hash_part=symbols,
    )
