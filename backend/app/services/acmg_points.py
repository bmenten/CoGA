"""ACMG/AMP point scoring, kept in parity with the frontend engine.

The classification is the Tavtigian/ClinGen Bayesian points system: each accepted
criterion contributes points by its applied strength (pathogenic positive, benign
negative) and the signed total maps onto the five ACMG classes. The server
recomputes this from the submitted criteria so a persisted classification never
depends on a client-supplied total.
"""

from __future__ import annotations

from typing import Iterable, Mapping

# Point magnitude per strength (unsigned); benign criteria negate these. BA1 is a
# stand-alone benign override and does not score by points.
STRENGTH_POINTS: dict[str, int] = {
    "very_strong": 8,
    "strong": 4,
    "moderate": 2,
    "supporting": 1,
    "stand_alone": 0,
}

# Direction for every ACMG/AMP 2015 criterion code.
CRITERION_DIRECTION: dict[str, str] = {
    code: "pathogenic"
    for code in (
        "PVS1",
        "PS1",
        "PS2",
        "PS3",
        "PS4",
        "PM1",
        "PM2",
        "PM3",
        "PM4",
        "PM5",
        "PM6",
        "PP1",
        "PP2",
        "PP3",
        "PP4",
        "PP5",
    )
}
CRITERION_DIRECTION.update(
    {
        code: "benign"
        for code in (
            "BA1",
            "BS1",
            "BS2",
            "BS3",
            "BS4",
            "BP1",
            "BP2",
            "BP3",
            "BP4",
            "BP5",
            "BP6",
            "BP7",
        )
    }
)

VALID_CODES = frozenset(CRITERION_DIRECTION)
VALID_STRENGTHS = frozenset(STRENGTH_POINTS)

# The strengths each criterion may be applied at: the ones the classification dialog offers,
# mirrored from ``allowedStrengths`` in frontend/src/lib/acmg/criteria.ts
# (backend/tests/test_acmg_frontend_parity.py fails when the two differ). A save that applies
# a criterion at any other strength is refused: BS1 at very strong would score -8, PM2 at
# very strong +8, and PVS1 at stand-alone nothing. Stand-alone is BA1's alone.
_PATHOGENIC_STRENGTHS = frozenset({"very_strong", "strong", "moderate", "supporting"})
_BENIGN_STRENGTHS = frozenset({"strong", "moderate", "supporting"})
_SUPPORTING_OR_MODERATE = frozenset({"supporting", "moderate"})
ALLOWED_STRENGTHS: dict[str, frozenset[str]] = {
    "PVS1": _PATHOGENIC_STRENGTHS,
    "PS1": _PATHOGENIC_STRENGTHS,
    "PS2": _PATHOGENIC_STRENGTHS,
    "PS3": _PATHOGENIC_STRENGTHS,
    "PS4": _PATHOGENIC_STRENGTHS,
    "PM1": _PATHOGENIC_STRENGTHS,
    "PM2": frozenset({"moderate", "supporting"}),
    "PM3": _PATHOGENIC_STRENGTHS,
    "PM4": _PATHOGENIC_STRENGTHS,
    "PM5": _PATHOGENIC_STRENGTHS,
    "PM6": _PATHOGENIC_STRENGTHS,
    "PP1": _PATHOGENIC_STRENGTHS,
    "PP2": _PATHOGENIC_STRENGTHS,
    "PP3": _PATHOGENIC_STRENGTHS,
    "PP4": _PATHOGENIC_STRENGTHS,
    "PP5": _PATHOGENIC_STRENGTHS,
    "BA1": frozenset({"stand_alone"}),
    "BS1": _BENIGN_STRENGTHS,
    "BS2": _BENIGN_STRENGTHS,
    "BS3": _BENIGN_STRENGTHS,
    "BS4": _BENIGN_STRENGTHS,
    "BP1": _SUPPORTING_OR_MODERATE,
    "BP2": _SUPPORTING_OR_MODERATE,
    "BP3": _SUPPORTING_OR_MODERATE,
    "BP4": frozenset({"supporting", "moderate", "strong"}),
    "BP5": _SUPPORTING_OR_MODERATE,
    "BP6": _SUPPORTING_OR_MODERATE,
    "BP7": _SUPPORTING_OR_MODERATE,
}

# Display labels matching the frontend ACMG_CLASS_LABELS / ACMG_CLASSIFICATION_TAGS.
CLASS_LABELS: dict[str, str] = {
    "acmg_class_5": "Pathogenic - class 5",
    "acmg_class_4": "Likely Pathogenic - class 4",
    "acmg_class_3": "VUS - class 3",
    "acmg_class_2": "Likely benign - class 2",
    "acmg_class_1": "Benign - class 1",
}


def is_valid_code(code: str) -> bool:
    return code in VALID_CODES


def is_allowed_strength(code: str, strength: str) -> bool:
    """Whether ``code`` may be applied at ``strength``: one the dialog offers for it."""
    return strength in ALLOWED_STRENGTHS.get(code, frozenset())


def selection_points(code: str, strength: str) -> int:
    magnitude = STRENGTH_POINTS.get(strength, 0)
    return -magnitude if CRITERION_DIRECTION.get(code) == "benign" else magnitude


def class_key_for_points(points: int) -> str:
    if points >= 10:
        return "acmg_class_5"
    if points >= 6:
        return "acmg_class_4"
    if points >= 0:
        return "acmg_class_3"
    if points >= -6:
        return "acmg_class_2"
    return "acmg_class_1"


def vus_tier_for_points(points: int, class_key: str) -> str | None:
    """MAGI-ACMG VUS sub-tier over the Tavtigian VUS band (points 0–5).

    Kept in parity with the frontend (score.vusTierForPoints): 4–5 hot · 2–3 warm
    · 0–1 cold. Returns None unless the variant is a VUS (acmg_class_3).
    """

    if class_key != "acmg_class_3":
        return None
    if points >= 4:
        return "hot"
    if points >= 2:
        return "warm"
    return "cold"


def compute_classification(
    criteria: Iterable[Mapping[str, object]],
) -> tuple[int, str, str]:
    """Return (point_total, class_key, class_label) from accepted criteria.

    Each item is a mapping with at least ``code``, ``strength`` and ``accepted``. Every item
    counts, so the caller passes each criterion once, at a strength it takes (a save is
    checked so in ``small_variant_review_acmg._normalize_acmg_payload``).
    An accepted BA1 forces the Benign class regardless of the point total.
    """

    points = 0
    stand_alone_benign = False
    for item in criteria:
        if not item.get("accepted"):
            continue
        code = str(item.get("code", ""))
        strength = str(item.get("strength", ""))
        if code == "BA1":
            stand_alone_benign = True
        points += selection_points(code, strength)

    class_key = "acmg_class_1" if stand_alone_benign else class_key_for_points(points)
    return points, class_key, CLASS_LABELS[class_key]
