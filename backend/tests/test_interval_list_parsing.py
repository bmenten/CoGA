"""The small-variant interval lists (#604): an entry that cannot be read as written is
refused and named, never skipped. Skipped, the search covered less than the list asked,
and a list with no entry left was answered as a family without variants."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.app.services.clickhouse_variant_queries import _parse_interval_regions
from backend.app.services.clickhouse_variant_records import Region


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("chr13:32315086-32400266", [Region("13", 32315086, 32400266)]),
        ("13:32,315,086-32,400,266", [Region("13", 32315086, 32400266)]),
        # An en dash, as the viewer writes a range, and spaces around the dash.
        ("chr13:32,315,086–32,400,266", [Region("13", 32315086, 32400266)]),
        ("chrX : 100 - 200", [Region("X", 100, 200)]),
        # A single position is a one-base interval.
        ("chr7:117559590", [Region("7", 117559590, 117559590)]),
        (
            "chr13:100-200\r\n\nchr17:300-400; chr2:3243",
            [Region("13", 100, 200), Region("17", 300, 400), Region("2", 3243, 3243)],
        ),
    ],
)
def test_reads_each_entry_as_written(text: str, expected: list[Region]) -> None:
    assert _parse_interval_regions(text) == expected


@pytest.mark.parametrize("text", [None, "", " \n \n ", ";;"])
def test_a_list_without_entries_restricts_nothing(text: str | None) -> None:
    assert _parse_interval_regions(text) == []


@pytest.mark.parametrize(
    ("entry", "reason"),
    [
        # A BED line: BED is 0-based, so it is not read as a 1-based interval.
        ("chr17\t43044295\t43125482", "is not chr:start-end"),
        ("chr1:100-", "is not chr:start-end"),
        ("chr1:abc", "is not chr:start-end"),
        ("BRCA1", "is not chr:start-end"),
        ("chr1:200-100", "ends before it starts"),
    ],
)
def test_an_unreadable_entry_is_refused_and_named(entry: str, reason: str) -> None:
    # Among readable entries: the list is refused as a whole, not applied in part.
    with pytest.raises(HTTPException) as refused:
        _parse_interval_regions(f"chr13:32315086-32400266\n{entry}\nchr2:1-2")

    assert refused.value.status_code == 422
    assert refused.value.detail == f"Interval {entry!r} {reason}."


def test_an_excluded_interval_is_named_as_one() -> None:
    with pytest.raises(HTTPException) as refused:
        _parse_interval_regions("chr1:5-", label="Excluded interval")

    assert refused.value.detail == "Excluded interval 'chr1:5-' is not chr:start-end."
