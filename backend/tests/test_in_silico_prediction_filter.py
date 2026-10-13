"""SIFT and PolyPhen filters: the ClickHouse condition and the Python matcher agree (CLIN-45).

VEP writes a SIFT or PolyPhen call with its score, ``deleterious(0.01)`` or
``probably_damaging(0.998)``, and the import stores the value so, case-folded. A filter keeps
a transcript whose value names the prediction the filter names, whatever its score:
``deleterious`` keeps ``deleterious(0.01)`` and ``deleterious``, not
``deleterious_low_confidence(0.03)``, which the filter form offers as a choice of its own.
The SQL condition matched the filter's text anywhere in the value, so ``deleterious`` also
kept ``deleterious_low_confidence`` and ``tolerated`` kept ``tolerated_low_confidence``. The
Python matcher, which runs after the SQL in the ranked view, the pair-based inheritance modes
and carrier screening, needed the whole value, so ``deleterious(0.01)`` was not
``deleterious`` and a SIFT- or PolyPhen-only filter kept nothing there.

The SQL condition is evaluated here as ClickHouse evaluates it on the stored value, with a
stand-in for each function it has used: ``positionCaseInsensitive`` (the text anywhere, in any
capitals) and ``match`` (an RE2 pattern; the filter's pattern holds only what RE2 and Python's
``re`` read alike). ``e2e/test_e2e_in_silico_prediction_filters.py`` runs it in ClickHouse.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import pytest

from backend.app.services.clickhouse_variant_queries import _small_detail_filter_clauses
from backend.app.services.clickhouse_variant_records import (
    SmallVariantRecord,
    _annotation_matches_normal,
)
from backend.app.services.clickhouse_variant_rows import _small_annotation_row
from backend.app.services.family_variant_filters import SmallVariantQueryFilters

# A SIFT value, and the predictions it names. VEP writes the first ten forms: with the
# score, and without it when run to write the prediction alone. No importer writes the
# capitals and spaces below, but an INFO field of another annotator could. A score alone
# (VEP run to write the score only) names no prediction.
_SIFT_VALUES: list[tuple[str, frozenset[str]]] = [
    ("deleterious(0.01)", frozenset({"deleterious"})),
    ("deleterious(0)", frozenset({"deleterious"})),
    ("deleterious", frozenset({"deleterious"})),
    ("deleterious_low_confidence(0.03)", frozenset({"deleterious_low_confidence"})),
    ("deleterious_low_confidence", frozenset({"deleterious_low_confidence"})),
    ("tolerated(0.42)", frozenset({"tolerated"})),
    ("tolerated(1)", frozenset({"tolerated"})),
    ("tolerated", frozenset({"tolerated"})),
    ("tolerated_low_confidence(0.61)", frozenset({"tolerated_low_confidence"})),
    ("tolerated_low_confidence", frozenset({"tolerated_low_confidence"})),
    ("Deleterious(0.01)", frozenset({"deleterious"})),
    ("DELETERIOUS", frozenset({"deleterious"})),
    ("deleterious low confidence (0.03)", frozenset({"deleterious_low_confidence"})),
    ("Tolerated Low Confidence", frozenset({"tolerated_low_confidence"})),
    ("tolerated(0.2)&deleterious(0.01)", frozenset({"tolerated", "deleterious"})),
    ("0.01", frozenset()),
]
_POLYPHEN_VALUES: list[tuple[str, frozenset[str]]] = [
    ("probably_damaging(0.998)", frozenset({"probably_damaging"})),
    ("probably_damaging(1)", frozenset({"probably_damaging"})),
    ("probably_damaging", frozenset({"probably_damaging"})),
    ("possibly_damaging(0.612)", frozenset({"possibly_damaging"})),
    ("possibly_damaging", frozenset({"possibly_damaging"})),
    ("benign(0.004)", frozenset({"benign"})),
    ("benign", frozenset({"benign"})),
    ("unknown(0)", frozenset({"unknown"})),
    ("unknown", frozenset({"unknown"})),
    ("Probably Damaging", frozenset({"probably_damaging"})),
    ("POSSIBLY_DAMAGING(0.6)", frozenset({"possibly_damaging"})),
    ("benign(0.01),possibly_damaging(0.5)", frozenset({"benign", "possibly_damaging"})),
    ("0.998", frozenset()),
]

# The filter form's choices (SIFT_OPTIONS and POLYPHEN_OPTIONS in
# smallVariantFilterSections.tsx), and the predictions the API takes besides them.
_SIFT_OPTIONS = ("deleterious", "deleterious_low_confidence", "tolerated", "tolerated_low_confidence")
_POLYPHEN_OPTIONS = ("probably_damaging", "possibly_damaging", "benign", "unknown")

_CASES = [
    pytest.param(column, option, value, option in names, id=f"{column}={option}:{value}")
    for column, options, values in (
        ("sift", _SIFT_OPTIONS, _SIFT_VALUES),
        ("polyphen", _POLYPHEN_OPTIONS, _POLYPHEN_VALUES),
    )
    for option in options
    for value, names in values
]


def _stored(annotation: dict[str, Any]) -> dict[str, str]:
    """A transcript's ``sift`` and ``polyphen`` as the import writes them to the
    ``annotations`` table (``_small_annotation_row``, whose last two columns they are)."""
    record = SmallVariantRecord(
        variant_key=1,
        variant_id="1-100-A-T",
        chr="1",
        start=100,
        end=100,
        ref="A",
        alt="T",
        source=None,
        rsid=None,
        filters=[],
        gene_symbols=[],
        annotations=[annotation],
        calls=[],
    )
    row = _small_annotation_row(
        variant_key=1,
        variant_id="1-100-A-T",
        annotation_version="current",
        annotation_set_hash=1,
        record=record,
        annotation=annotation,
    )
    return {"sift": row[-2], "polyphen": row[-1]}


# ClickHouse's functions in the condition, on the stored text: each stand-in reads one call
# (``function(a.column, %(param)s)``) and decides it as ClickHouse does.
_FUNCTION_CALL = re.compile(r"(\w+)\(a\.(\w+), %\((\w+)\)s\)( > 0)?")
_STAND_INS: dict[str, Callable[[str, str], bool]] = {
    "positionCaseInsensitive": lambda stored, needle: needle.casefold() in stored.casefold(),
    "match": lambda stored, pattern: re.search(pattern, stored) is not None,
}


def _closing_parenthesis(text: str) -> int:
    """Where the parenthesis that opens ``text`` closes, or -1."""
    depth = 0
    for index, character in enumerate(text):
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _strip_parentheses(text: str) -> str:
    """``text`` without the pairs of parentheses around all of it."""
    text = text.strip()
    while text.startswith("(") and _closing_parenthesis(text) == len(text) - 1:
        text = text[1:-1].strip()
    return text


def _sql_keeps(filters: SmallVariantQueryFilters, annotation: dict[str, Any]) -> bool:
    """Whether the family search's SQL keeps a transcript row with this annotation.

    Built for the in-silico predictions alone: one clause, the predictors OR-ed in it."""
    clauses, params = _small_detail_filter_clauses(filters)
    if not clauses:
        return True
    (clause,) = clauses
    stored = _stored(annotation)
    decisions = []
    for part in _strip_parentheses(clause).split(" OR "):
        call = _FUNCTION_CALL.fullmatch(_strip_parentheses(part))
        assert call is not None, f"not a condition this test reads: {clause}"
        function, column, param, _positive = call.groups()
        decisions.append(_STAND_INS[function](stored[column], params[param]))
    return any(decisions)


@pytest.mark.parametrize(("column", "option", "value", "names_it"), _CASES)
def test_sql_and_python_keep_a_transcript_whose_value_names_the_prediction(
    column: str, option: str, value: str, names_it: bool
) -> None:
    # One rule in both paths: the transcript is kept when its value names the prediction,
    # with or without a score, in any capitals, spaces and underscores alike; never for
    # another prediction that begins with it, such as the low-confidence ones.
    filters = SmallVariantQueryFilters(page=1, page_size=100, **{column: option})
    annotation = {column: value}
    assert (_sql_keeps(filters, annotation), _annotation_matches_normal(annotation, filters)) == (
        names_it,
        names_it,
    )


@pytest.mark.parametrize("column", ["sift", "polyphen"])
def test_a_transcript_without_the_prediction_is_kept_by_neither(column: str) -> None:
    filters = SmallVariantQueryFilters(page=1, page_size=100, **{column: "benign"})
    for annotation in ({}, {column: ""}, {"cadd_phred": 30.0}):
        assert (_sql_keeps(filters, annotation), _annotation_matches_normal(annotation, filters)) == (
            False,
            False,
        )


@pytest.mark.parametrize(
    "filter_value",
    ["Deleterious", "DELETERIOUS", " deleterious ", "deleterious(0.5)", "deleterious (0.5)"],
)
def test_the_filter_value_is_read_as_a_stored_value_is(filter_value: str) -> None:
    # The API takes any text: its prediction is read as a stored value's is.
    filters = SmallVariantQueryFilters(page=1, page_size=100, sift=filter_value)
    for value, kept in (
        ("deleterious(0.01)", True),
        ("deleterious", True),
        ("deleterious_low_confidence(0.03)", False),
    ):
        annotation = {"sift": value}
        assert (_sql_keeps(filters, annotation), _annotation_matches_normal(annotation, filters)) == (
            kept,
            kept,
        ), value


@pytest.mark.parametrize("filter_value", ["deleterious low confidence", "Deleterious_Low_Confidence(0.1)"])
def test_spaces_and_underscores_name_the_same_prediction(filter_value: str) -> None:
    filters = SmallVariantQueryFilters(page=1, page_size=100, sift=filter_value)
    for value, kept in (
        ("deleterious_low_confidence(0.03)", True),
        ("deleterious(0.01)", False),
    ):
        annotation = {"sift": value}
        assert (_sql_keeps(filters, annotation), _annotation_matches_normal(annotation, filters)) == (
            kept,
            kept,
        ), value


@pytest.mark.parametrize("filter_value", [" ", "_", "(0.01)"])
def test_a_filter_value_that_names_no_prediction_asks_nothing_in_either_path(filter_value: str) -> None:
    # Blank, or a score alone: no SQL condition, and the Python matcher keeps the transcript
    # too. It used to keep nothing in Python while the SQL ignored it.
    filters = SmallVariantQueryFilters(page=1, page_size=100, sift=filter_value)
    assert _small_detail_filter_clauses(filters) == ([], {})
    for annotation in ({"sift": "tolerated(0.4)"}, {}):
        assert _annotation_matches_normal(annotation, filters)


def test_one_predictor_is_enough_in_both_paths() -> None:
    # SIFT or PolyPhen (or a score minimum): one is enough, in SQL as in Python.
    filters = SmallVariantQueryFilters(page=1, page_size=100, sift="deleterious", polyphen="probably_damaging")
    for annotation, kept in (
        ({"sift": "deleterious(0.01)", "polyphen": "benign(0.1)"}, True),
        ({"sift": "tolerated(0.3)", "polyphen": "probably_damaging(0.99)"}, True),
        ({"sift": "deleterious_low_confidence(0.03)", "polyphen": "possibly_damaging(0.6)"}, False),
        ({"sift": "tolerated(0.3)", "polyphen": "benign(0.1)"}, False),
    ):
        assert (_sql_keeps(filters, annotation), _annotation_matches_normal(annotation, filters)) == (
            kept,
            kept,
        ), annotation


def test_the_variant_explorer_applies_the_same_condition() -> None:
    # The explorer builds its per-transcript condition with the family search's function
    # (CLIN-29), so it reads SIFT and PolyPhen by the same rule.
    from backend.app.services.variant_explorer_service import (
        GlobalVariantFilters,
        _transcript_annotation_condition,
    )

    for filters, column, kept, dropped in (
        (GlobalVariantFilters(sift="deleterious"), "sift", "deleterious(0.01)", "deleterious_low_confidence(0.03)"),
        (GlobalVariantFilters(sift="tolerated"), "sift", "tolerated(0.4)", "tolerated_low_confidence(0.6)"),
        (GlobalVariantFilters(polyphen="benign"), "polyphen", "benign(0.004)", "probably_damaging(0.9)"),
    ):
        params: dict[str, Any] = {}
        condition = _transcript_annotation_condition(filters, params)
        assert condition is not None
        call = _FUNCTION_CALL.fullmatch(_strip_parentheses(condition))
        assert call is not None, condition
        function, called_column, param, _positive = call.groups()
        assert called_column == column
        decide = _STAND_INS[function]
        assert decide(_stored({column: kept})[column], params[param])
        assert not decide(_stored({column: dropped})[column], params[param])


def test_the_nipt_filters_apply_the_same_condition() -> None:
    # The monogenic NIPT page sends the small-variant filter form's values to the same SQL.
    from backend.app.services.nipt_service import _build_nipt_query_filters

    filters = _build_nipt_query_filters({"sift": "deleterious", "polyphen": "possibly_damaging"})
    assert _sql_keeps(filters, {"sift": "deleterious(0.02)"})
    assert _sql_keeps(filters, {"polyphen": "possibly_damaging(0.7)"})
    assert not _sql_keeps(filters, {"sift": "deleterious_low_confidence(0.02)", "polyphen": "probably_damaging(0.9)"})


# --- The rule itself (family_variant_filters) --------------------------------------------
# Imported in each test, so the parity tests above also run on code without it.


@pytest.mark.parametrize(
    ("value", "prediction"),
    [
        ("deleterious(0.01)", "deleterious"),
        ("Deleterious low confidence (0.03)", "deleterious_low_confidence"),
        ("  PROBABLY_DAMAGING  ", "probably_damaging"),
        ("probably__damaging", "probably_damaging"),
        ("probably damaging( 0.9 )", "probably_damaging"),
        ("(0.01)", ""),
        (" _ ", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_the_prediction_a_filter_value_names(value: str | None, prediction: str) -> None:
    from backend.app.services.family_variant_filters import in_silico_prediction

    assert in_silico_prediction(value) == prediction


def test_the_pattern_holds_only_what_re2_and_python_read_alike() -> None:
    # Literals, [...] classes, quantifiers, non-capturing groups and anchors: ClickHouse's
    # RE2 and Python's re read them alike (the e2e test runs it in ClickHouse).
    from backend.app.services.family_variant_filters import in_silico_prediction_pattern

    assert in_silico_prediction_pattern("deleterious_low_confidence") == (
        "(?:^|[,;&|/])[ _]*deleterious[ _]+low[ _]+confidence[ _]*"
        "(?:[(][^()]*[)][ _]*)?(?:[,;&|/]|$)"
    )
    assert in_silico_prediction_pattern("Probably Damaging (0.9)") == in_silico_prediction_pattern(
        "probably_damaging"
    )
    for no_prediction in (None, "", " ", "(0.5)"):
        assert in_silico_prediction_pattern(no_prediction) is None


def test_a_filter_value_is_matched_as_text_never_as_a_pattern() -> None:
    # The API takes any text: its characters are escaped, so none acts as a pattern.
    from backend.app.services.family_variant_filters import in_silico_prediction_matches

    assert in_silico_prediction_matches("delete.ious(0.1)", "delete.ious")
    assert not in_silico_prediction_matches("deleterious(0.1)", "delete.ious")
    assert not in_silico_prediction_matches("deleterious", "dele*")
    assert not in_silico_prediction_matches("deleterious", "[d]eleterious")


def test_no_prediction_matches_nothing() -> None:
    from backend.app.services.family_variant_filters import in_silico_prediction_matches

    assert not in_silico_prediction_matches("deleterious(0.01)", None)
    assert not in_silico_prediction_matches("deleterious(0.01)", "(0.5)")
    assert not in_silico_prediction_matches(None, "deleterious")
    assert not in_silico_prediction_matches("", "deleterious")
