"""The report code of a signed analysis record: the code the EPD report quotes.

Made from the record's immutable fields (family identifier, version, content hash), short,
and typo-detecting: every mistyped symbol and every swap of two neighbours is refused by
its check symbol, so a code copied wrong never finds another record.
"""

from __future__ import annotations

import hashlib

import pytest

from backend.app.services.report_code import (
    REPORT_CODE_ALPHABET,
    ReportCodeError,
    content_hash_prefix,
    family_part,
    parse_report_code,
    report_code,
)


def _hash(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def test_a_code_is_made_from_the_family_the_version_and_the_content_hash() -> None:
    code = report_code("FAM_TRIO", 2, _hash("signed content"))
    assert code is not None
    family, version, tail = code.split("-")
    assert family == family_part("FAM_TRIO") and len(family) == 6
    assert version == "2"
    assert len(tail) == 4
    assert set(code.replace("-", "")) <= set(REPORT_CODE_ALPHABET)
    # The same fields always make the same code; no clock, no randomness.
    assert report_code("FAM_TRIO", 2, _hash("signed content")) == code


def test_every_version_of_a_family_shares_the_first_group() -> None:
    first = report_code("FAM_TRIO", 1, _hash("v1"))
    amended = report_code("FAM_TRIO", 2, _hash("v2"))
    other = report_code("FAM_OTHER", 1, _hash("v1"))
    assert first is not None and amended is not None and other is not None
    assert first.split("-")[0] == amended.split("-")[0]
    assert first.split("-")[0] != other.split("-")[0]


def test_other_content_or_another_version_makes_another_code() -> None:
    base = report_code("FAM_TRIO", 1, _hash("a"))
    assert report_code("FAM_TRIO", 2, _hash("a")) != base
    # The first 15 bits of the hash: two contents that differ there differ in the code.
    differs = next(
        _hash(f"b{index}")
        for index in range(1000)
        if _hash(f"b{index}")[:4] != _hash("a")[:4]
    )
    assert report_code("FAM_TRIO", 1, differs) != base


def test_a_code_stays_short() -> None:
    assert len(report_code("FAM_TRIO", 1, _hash("x")) or "") == 13
    # Up to version 9999 a code is at most 16 characters, a long family identifier included.
    assert len(report_code("F" * 200, 9999, _hash("x")) or "") == 16


def test_a_record_without_the_fields_has_no_code() -> None:
    assert report_code(None, 1, _hash("x")) is None
    assert report_code("FAM", 0, _hash("x")) is None
    assert report_code("FAM", 1, None) is None
    assert report_code("FAM", 1, "not a hash") is None


def test_a_code_reads_back_as_typed_by_a_reader() -> None:
    code = report_code("FAM_TRIO", 12, _hash("signed content"))
    assert code is not None
    parsed = parse_report_code(code)
    assert (parsed.code, parsed.version, parsed.family_part) == (code, 12, code.split("-")[0])
    # Lower case, spaces for hyphens, no separators at all, spaces around it.
    assert parse_report_code(code.lower()).code == code
    assert parse_report_code(code.replace("-", " ")).code == code
    assert parse_report_code(f"  {code.replace('-', '')}  ").code == code


def test_a_reader_may_type_i_or_l_for_one_and_o_for_zero() -> None:
    code = next(
        candidate
        for index in range(5000)
        if (candidate := report_code(f"FAM{index}", 10, _hash(str(index)))) is not None
        and "1" in candidate.split("-")[0]
        and "0" in candidate
    )
    typed = code.replace("1", "l").replace("0", "O")
    assert parse_report_code(typed).code == code


def test_every_mistyped_symbol_and_every_neighbour_swap_is_caught() -> None:
    for seed in ("a", "b", "c"):
        code = report_code(f"FAM-{seed}", 3, _hash(seed))
        assert code is not None
        compact = code.replace("-", "")
        for index, symbol in enumerate(compact):
            for other in REPORT_CODE_ALPHABET:
                if other == symbol:
                    continue
                mistyped = compact[:index] + other + compact[index + 1 :]
                with pytest.raises(ReportCodeError):
                    parse_report_code(mistyped)
        for index in range(len(compact) - 1):
            if compact[index] == compact[index + 1]:
                continue
            swapped = compact[:index] + compact[index + 1] + compact[index] + compact[index + 2 :]
            with pytest.raises(ReportCodeError):
                parse_report_code(swapped)


@pytest.mark.parametrize(
    ("typed", "said"),
    [
        ("", "Enter a report code."),
        ("ABC", "too short"),
        ("7KQM2X-2-4PH!", "also holds !"),
        ("7KQM2U-2-4PHT", "also holds U"),
        ("7KQM2X-A-4PHT", "not a version number"),
        ("7KQM2X-02-4PHT", "not a version number"),
    ],
)
def test_what_is_not_a_report_code_is_refused_in_words(typed: str, said: str) -> None:
    with pytest.raises(ReportCodeError) as excinfo:
        parse_report_code(typed)
    assert said in str(excinfo.value)


def test_the_hash_symbols_name_the_content_hash_prefix_a_lookup_reads() -> None:
    for seed in range(50):
        content_hash = _hash(str(seed))
        code = report_code("FAM", 1, content_hash)
        assert code is not None
        assert content_hash.startswith(content_hash_prefix(parse_report_code(code).hash_part))
