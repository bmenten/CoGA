"""The readers of loosely typed stored values (``services/stored_values.py``), shared by the
services that read them: a stored JSON object, and a whole number."""

from __future__ import annotations

import pytest

from backend.app.services import (
    annotation_manifest_service,
    mitochondrial_analysis,
    panel_metadata_service,
    paraphase_pg,
    raw_import_files_pg,
    reference_metadata_service,
    repeat_count_units,
    repeat_expansion_pg,
)
from backend.app.services.stored_values import int_or_none, json_object

# --- json_object -----------------------------------------------------------------------------


def test_a_dict_is_returned_as_it_is() -> None:
    stored = {"filename": "PROBAND.vcf.gz", "nested": {"a": 1}}
    assert json_object(stored) is stored


def test_json_text_holding_an_object_is_parsed() -> None:
    assert json_object('{"source": "STRchive", "count": 3}') == {"source": "STRchive", "count": 3}


@pytest.mark.parametrize(
    "value",
    ["", "   ", "not json", "{cut short", "[1, 2]", '"text"', "3", "null", None, 3, 2.5, [{"a": 1}], b'{"a": 1}'],
    ids=[
        "empty",
        "blank",
        "no-json",
        "cut-short",
        "json-list",
        "json-string",
        "json-number",
        "json-null",
        "none",
        "int",
        "float",
        "list",
        "bytes",
    ],
)
def test_anything_else_is_empty(value: object) -> None:
    assert json_object(value) == {}


def test_json_text_python_will_not_read_is_empty() -> None:
    # An integer of more than 4,300 digits: Python's json refuses it with a plain ValueError,
    # not a JSONDecodeError.
    assert json_object('{"a": ' + "1" * 5_000 + "}") == {}


# --- int_or_none -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [(3, 3), ("3", 3), ("-2", -2), ("3.7", 3), (3.7, 3), ("1e3", 1000), (True, 1)],
)
def test_a_whole_number_is_read_from_an_int_text_or_float(value: object, expected: int) -> None:
    assert int_or_none(value) == expected


@pytest.mark.parametrize("value", [None, "", "n/a", ".", "3 copies", [3], {"n": 3}, "nan"])
def test_no_number_is_none(value: object) -> None:
    assert int_or_none(value) is None


@pytest.mark.parametrize(
    "value", ["inf", "Infinity", "-inf", "1e400", float("inf"), float("-inf"), float("nan")]
)
def test_an_infinity_or_nan_is_none_not_an_error(value: object) -> None:
    # int() raises OverflowError, not ValueError, on an infinity: the reader catches it too.
    assert int_or_none(value) is None


# --- The services --------------------------------------------------------------------------


def test_every_service_reads_through_the_shared_readers() -> None:
    # Under the names each service's code and tests use; a copy of its own would differ again.
    for reader in (
        annotation_manifest_service._as_dict,
        panel_metadata_service._jsonb_dict,
        paraphase_pg._json_object,
        raw_import_files_pg._json_object,
        reference_metadata_service._json_dict,
        repeat_count_units._json_field,
    ):
        assert reader is json_object
    assert mitochondrial_analysis._int is repeat_expansion_pg._as_int is int_or_none


def test_a_repeat_catalogue_rows_metadata_is_read_as_a_json_object() -> None:
    assert repeat_expansion_pg._row_metadata({"metadata": '{"source": "STRchive"}'}) == {"source": "STRchive"}
    assert repeat_expansion_pg._row_metadata({"metadata": "not json"}) == {}
    assert repeat_expansion_pg._row_metadata({}) == {}
