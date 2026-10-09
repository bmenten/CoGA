"""The server's ACMG and CNV criterion tables match the classification dialogs' (CLIN-3).

The small-variant dialog offers each ACMG criterion only the strengths of its
``allowedStrengths``, and the CNV dialog each ClinGen criterion only the points of its range.
The server keeps its own copy of both tables: it refuses a strength a criterion does not take
(``acmg_points.ALLOWED_STRENGTHS``) and keeps a point value within the criterion's range
(``cnv_acmg_points``). These tests read the TypeScript catalogues and fail when the two
differ: a strength the dialog offers that the server refused would make a classification
impossible to save, and one the server took that the dialog does not offer could be stored
only by a client calling the API.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.app.schemas import (
    AcmgClassificationPayload,
    AcmgCriterionSelection,
    CnvAcmgClassificationPayload,
    CnvAcmgCriterion,
)
from backend.app.services import acmg_points, cnv_acmg_points
from backend.app.services.small_variant_review_acmg import _normalize_acmg_payload
from backend.app.services.structural_variant_review_pg import _normalize_cnv_acmg_payload

FRONTEND_LIB = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib"
ACMG_CRITERIA_TS = FRONTEND_LIB / "acmg" / "criteria.ts"
CNV_CRITERIA_TS = FRONTEND_LIB / "cnvAcmg" / "criteria.ts"

_QUOTED = r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\""
_NUMBER = r"-?\d+(?:\.\d+)?"


def _array_body(source: str, declaration: str) -> str:
    """The text of the array ``declaration`` opens, up to the ``];`` that closes it."""
    head = re.search(re.escape(declaration) + r"\s*=\s*\[", source)
    assert head, f"{declaration} not found"
    end = source.find("\n];", head.end())
    assert end > 0, f"the end of {declaration} not found"
    return source[head.end() : end]


def _strengths(literal: str) -> frozenset[str]:
    return frozenset(re.findall(r"'(\w+)'", literal))


def _field(criterion: str, name: str, value: str = r"'(\w+)'") -> str:
    """The value of the field ``name`` in the text of one criterion object."""
    match = re.search(rf"^\s*{name}:\s*{value}", criterion, re.M)
    assert match, f"{name} of a criterion in criteria.ts could not be read:\n{criterion}"
    return match.group(1)


def _dialog_acmg_criteria() -> dict[str, tuple[str, str, frozenset[str]]]:
    """code → (direction, default strength, allowed strengths), as criteria.ts defines them."""
    source = ACMG_CRITERIA_TS.read_text(encoding="utf-8")
    # The shared strength lists (PATHOGENIC_STRENGTHS, BENIGN_STRENGTHS) a criterion may name.
    named = {
        name: _strengths(body) for name, body in re.findall(r"const (\w+): AcmgStrength\[\] = \[([^\]]*)\]", source)
    }
    body = _array_body(source, "export const ACMG_CRITERIA: AcmgCriterionDef[]")
    # One chunk per criterion object, from its ``code:`` to the next criterion's.
    starts = [match.start() for match in re.finditer(r"^\s*code:", body, re.M)]
    criteria: dict[str, tuple[str, str, frozenset[str]]] = {}
    for start, end in zip(starts, starts[1:] + [len(body)]):
        chunk = body[start:end]
        code = _field(chunk, "code")
        allowed = _field(chunk, "allowedStrengths", r"(\[[^\]]*\]|\w+)")
        assert code not in criteria, f"{code} is defined twice in criteria.ts"
        criteria[code] = (
            _field(chunk, "direction"),
            _field(chunk, "defaultStrength"),
            _strengths(allowed) if allowed.startswith("[") else named[allowed],
        )
    return criteria


def _dialog_strength_points() -> dict[str, int]:
    source = ACMG_CRITERIA_TS.read_text(encoding="utf-8")
    body = re.search(r"export const STRENGTH_POINTS: Record<AcmgStrength, number> = \{([^}]*)\}", source)
    assert body, "STRENGTH_POINTS not found in criteria.ts"
    return {name: int(points) for name, points in re.findall(rf"(\w+):\s*({_NUMBER})", body.group(1))}


def _dialog_cnv_criteria(kind: str) -> dict[str, tuple[str, float, float, float]]:
    """code → (section, default, min, max) of the CNV loss or gain catalogue in criteria.ts."""
    source = CNV_CRITERIA_TS.read_text(encoding="utf-8")
    body = _array_body(source, f"export const CNV_{kind.upper()}_CRITERIA: CnvCriterionDef[]")
    calls = re.findall(
        rf"\bc\(\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*(?:{_QUOTED})\s*,"
        rf"\s*({_NUMBER})\s*,\s*({_NUMBER})\s*,\s*({_NUMBER})\s*,?\s*\)",
        body,
    )
    assert len(calls) == len(re.findall(r"\bc\(", body)), f"a {kind} criterion in criteria.ts could not be read"
    criteria = {code: (section, float(default), float(low), float(high)) for code, section, default, low, high in calls}
    assert len(criteria) == len(calls), f"a {kind} criterion is defined twice in criteria.ts"
    return criteria


# --- small variants (ACMG/AMP 2015) -----------------------------------------------------------


def test_the_server_knows_the_dialog_s_criteria_with_their_direction_and_points() -> None:
    dialog = _dialog_acmg_criteria()
    assert len(dialog) == 28
    assert {code: direction for code, (direction, _, _) in dialog.items()} == acmg_points.CRITERION_DIRECTION
    assert set(acmg_points.ALLOWED_STRENGTHS) == acmg_points.VALID_CODES
    assert _dialog_strength_points() == acmg_points.STRENGTH_POINTS


def test_the_server_allows_each_criterion_the_strengths_the_dialog_offers() -> None:
    differences = [
        f"{code}: dialog {sorted(allowed)}, server {sorted(acmg_points.ALLOWED_STRENGTHS.get(code, ()))}"
        for code, (_direction, _default, allowed) in sorted(_dialog_acmg_criteria().items())
        if allowed != acmg_points.ALLOWED_STRENGTHS.get(code)
    ]
    assert differences == []


def test_a_save_takes_every_strength_the_dialog_offers_and_refuses_any_other() -> None:
    # The dialog sends a criterion at one of its allowed strengths, or at its default when the
    # analyst ticks it without choosing one.
    wrong: list[str] = []
    for code, (_direction, default, allowed) in sorted(_dialog_acmg_criteria().items()):
        offered = allowed | {default}
        for strength in sorted(acmg_points.VALID_STRENGTHS):
            payload = AcmgClassificationPayload(
                criteria=[AcmgCriterionSelection(code=code, strength=strength, accepted=True)]
            )
            try:
                blob, _points, _class = _normalize_acmg_payload(payload)
            except HTTPException as refused:
                assert refused.status_code == 400
                taken = False
            else:
                assert blob is not None and blob["criteria"][0]["strength"] == strength
                taken = True
            if taken != (strength in offered):
                wrong.append(f"{code} at {strength}: {'taken' if taken else 'refused'}")
    assert wrong == []


# --- copy-number variants (ClinGen 2019) ------------------------------------------------------


def _server_cnv_criteria(kind: str) -> dict[str, tuple[str, float, float, float]]:
    catalogue = cnv_acmg_points.CNV_GAIN_CRITERIA if kind == "gain" else cnv_acmg_points.CNV_LOSS_CRITERIA
    return {code: (spec["section"], spec["default"], spec["min"], spec["max"]) for code, spec in catalogue.items()}


@pytest.mark.parametrize("kind", ["loss", "gain"])
def test_the_cnv_catalogues_match_the_dialog_s(kind: str) -> None:
    assert _dialog_cnv_criteria(kind) == _server_cnv_criteria(kind)


@pytest.mark.parametrize("kind", ["loss", "gain"])
def test_a_save_keeps_every_point_value_the_dialog_offers(kind: str) -> None:
    for code, (_section, default, low, high) in _dialog_cnv_criteria(kind).items():
        for points in sorted({default, low, high}):
            payload = CnvAcmgClassificationPayload(
                kind=kind, criteria=[CnvAcmgCriterion(code=code, points=points, accepted=True)]
            )
            blob, _total, _class = _normalize_cnv_acmg_payload(payload)
            assert blob is not None and blob["criteria"][0]["points"] == points, (kind, code, points)
