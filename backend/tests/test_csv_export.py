from __future__ import annotations

import csv
import io

import pytest

from backend.app.core.csv_export import csv_safe_cell


@pytest.mark.parametrize(
    "trigger",
    ["=", "+", "-", "@", "\t", "\r", "\n"],
)
def test_csv_safe_cell_neutralises_formula_triggers(trigger: str) -> None:
    payload = f"{trigger}HYPERLINK(\"http://evil\",\"x\")"
    guarded = csv_safe_cell(payload)
    assert guarded == "'" + payload
    # The guarded value does not open with a formula trigger for the spreadsheet.
    assert guarded[0] == "'"


def test_csv_safe_cell_passes_through_benign_values() -> None:
    for value in ["", "BRCA1", "0.0001", "chr1:100", "pathogenic", "1/1"]:
        assert csv_safe_cell(value) == value


def test_csv_safe_cell_survives_round_trip_through_csv_reader() -> None:
    # A cell that starts as a formula must read back identically (quote + payload),
    # never as a bare formula, after a real csv.writer/reader round trip.
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([csv_safe_cell("=1+2"), csv_safe_cell("normal")])
    rows = list(csv.reader(io.StringIO(buffer.getvalue())))
    assert rows == [["'=1+2", "normal"]]
