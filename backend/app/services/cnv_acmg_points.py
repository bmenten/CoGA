"""ClinGen 2019 copy-number variant (CNV) classification scoring.

Implements the ClinGen/ACMG technical-standard point system for copy-number
LOSS and GAIN (Riggs et al., 2020): each accepted evidence criterion contributes
a (possibly signed) point value, the points sum, and the total maps onto the five
classes. Unlike sequence-variant ACMG, the evidence sections and point weights
differ between losses and gains, and within-criterion the evaluator may choose a
value inside a suggested range, so the server clamps each submitted value to the
criterion's allowed range and recomputes the total — a persisted classification
never trusts a client-supplied total.

Kept in parity with the frontend engine in ``frontend/src/lib/cnvAcmg``:
``backend/tests/test_acmg_frontend_parity.py`` fails when the catalogues differ.
"""

from __future__ import annotations

from typing import Iterable, Mapping

# Total-score → class thresholds (shared by loss and gain).
#   >= 0.99            Pathogenic
#   0.90 .. 0.98       Likely pathogenic
#   -0.89 .. 0.89      Uncertain significance
#   -0.98 .. -0.90     Likely benign
#   <= -0.99           Benign
CLASS_LABELS: dict[str, str] = {
    "cnv_class_5": "Pathogenic - class 5",
    "cnv_class_4": "Likely Pathogenic - class 4",
    "cnv_class_3": "VUS - class 3",
    "cnv_class_2": "Likely benign - class 2",
    "cnv_class_1": "Benign - class 1",
}


def _c(name: str, section: str, default: float, low: float, high: float) -> dict:
    return {"name": name, "section": section, "default": default, "min": low, "max": high}


# --- Copy-number LOSS (deletion) evidence catalog --------------------------
CNV_LOSS_CRITERIA: dict[str, dict] = {
    "1A": _c("Contains protein-coding or known functional elements", "1", 0.0, 0.0, 0.0),
    "1B": _c("Does NOT contain protein-coding or known functional elements", "1", -0.60, -0.60, -0.60),
    "2A": _c("Complete overlap of an established haploinsufficient (HI) gene/region", "2", 1.0, 1.0, 1.0),
    "2B": _c("Partial overlap of an established HI genomic region", "2", 0.0, 0.0, 0.90),
    "2C-1": _c("Partial overlap of 5' end of an established HI gene — coding sequence involved", "2", 0.90, 0.0, 0.90),
    "2C-2": _c("Partial overlap of 5' end of an established HI gene — 5'UTR only", "2", 0.0, 0.0, 0.45),
    "2D-2": _c("Partial overlap of 3' end of an established HI gene — last exon, other pathogenic SNVs reported", "2", 0.90, 0.0, 0.90),
    "2D-3": _c("Partial overlap of 3' end of an established HI gene — last exon, no pathogenic SNVs", "2", 0.30, 0.0, 0.45),
    "2E": _c("Both breakpoints within the same established HI gene (intragenic, PVS1-like)", "2", 0.90, -0.45, 0.90),
    "2F": _c("Completely contained within an established benign CNV region", "2", -1.0, -1.0, -1.0),
    "2G": _c("Overlaps an established benign CNV but includes additional genomic material", "2", 0.0, 0.0, 0.0),
    "2H": _c("Two or more HI predictors suggest the gene(s) are haploinsufficient", "2", 0.15, 0.0, 0.15),
    "3A": _c("0–24 protein-coding genes wholly or partially included", "3", 0.0, 0.0, 0.0),
    "3B": _c("25–34 protein-coding genes wholly or partially included", "3", 0.45, 0.45, 0.45),
    "3C": _c("35+ protein-coding genes wholly or partially included", "3", 0.90, 0.90, 0.90),
    "4A": _c("Reported proband(s) with a consistent phenotype, de novo", "4", 0.0, 0.0, 0.90),
    "4B": _c("Reported proband(s) with a consistent phenotype, inheritance unknown", "4", 0.0, 0.0, 0.45),
    "4C": _c("Reported proband(s) with a nonspecific or limited phenotype", "4", 0.0, 0.0, 0.30),
    "4D": _c("Reported in unaffected individuals or population controls", "4", 0.0, -0.90, 0.0),
    "4F": _c("Functional studies support a deleterious effect", "4", 0.0, 0.0, 0.45),
    "4G": _c("Functional studies support no deleterious effect", "4", 0.0, -0.45, 0.0),
    "5A": _c("De novo, confirmed (both parents tested)", "5", 0.0, 0.0, 0.45),
    "5B": _c("De novo, assumed (parental testing incomplete)", "5", 0.0, 0.0, 0.30),
    "5C": _c("Inherited from an affected parent / segregates with disease", "5", 0.0, 0.0, 0.45),
    "5D": _c("Inherited from an unaffected parent", "5", 0.0, -0.45, 0.0),
    "5E": _c("Does not segregate with the phenotype in the family", "5", 0.0, -0.90, 0.0),
    "5F": _c("Inheritance information unavailable", "5", 0.0, 0.0, 0.0),
}

# --- Copy-number GAIN (duplication) evidence catalog -----------------------
CNV_GAIN_CRITERIA: dict[str, dict] = {
    "1A": _c("Contains protein-coding or known functional elements", "1", 0.0, 0.0, 0.0),
    "1B": _c("Does NOT contain protein-coding or known functional elements", "1", -0.60, -0.60, -0.60),
    "2A": _c("Complete overlap of an established triplosensitive (TS) gene/region", "2", 1.0, 1.0, 1.0),
    "2B": _c("Partial overlap of an established TS genomic region", "2", 0.0, 0.0, 0.90),
    "2C": _c("Identical in gene content to an established benign copy-number gain", "2", -1.0, -1.0, -1.0),
    "2D": _c("Smaller than an established benign gain; breakpoints do not interrupt protein-coding genes", "2", -1.0, -1.0, -1.0),
    "2E": _c("Smaller than an established benign gain; breakpoints potentially interrupt protein-coding genes", "2", 0.0, 0.0, 0.0),
    "2F": _c("Larger than an established benign gain; no additional protein-coding genes", "2", -1.0, -1.0, -1.0),
    "2G": _c("Overlaps an established benign gain but includes additional genomic material", "2", 0.0, 0.0, 0.0),
    "2H": _c("An established HI gene is fully contained within the observed gain", "2", 0.0, 0.0, 0.0),
    "2I": _c("Both breakpoints are within the same gene (gene disruption)", "2", 0.0, 0.0, 0.45),
    "3A": _c("0–34 protein-coding genes wholly or partially included", "3", 0.0, 0.0, 0.0),
    "3B": _c("35–49 protein-coding genes wholly or partially included", "3", 0.45, 0.45, 0.45),
    "3C": _c("50+ protein-coding genes wholly or partially included", "3", 0.90, 0.90, 0.90),
    "4A": _c("Reported proband(s) with a consistent phenotype, de novo", "4", 0.0, 0.0, 0.90),
    "4B": _c("Reported proband(s) with a consistent phenotype, inheritance unknown", "4", 0.0, 0.0, 0.45),
    "4C": _c("Reported proband(s) with a nonspecific or limited phenotype", "4", 0.0, 0.0, 0.30),
    "4D": _c("Reported in unaffected individuals or population controls", "4", 0.0, -0.90, 0.0),
    "4F": _c("Functional studies support a deleterious effect", "4", 0.0, 0.0, 0.45),
    "4G": _c("Functional studies support no deleterious effect", "4", 0.0, -0.45, 0.0),
    "5A": _c("De novo, confirmed (both parents tested)", "5", 0.0, 0.0, 0.45),
    "5B": _c("De novo, assumed (parental testing incomplete)", "5", 0.0, 0.0, 0.30),
    "5C": _c("Inherited from an affected parent / segregates with disease", "5", 0.0, 0.0, 0.45),
    "5D": _c("Inherited from an unaffected parent", "5", 0.0, -0.45, 0.0),
    "5E": _c("Does not segregate with the phenotype in the family", "5", 0.0, -0.90, 0.0),
    "5F": _c("Inheritance information unavailable", "5", 0.0, 0.0, 0.0),
}

VALID_KINDS = frozenset({"loss", "gain"})


def _catalog(kind: str) -> dict[str, dict]:
    return CNV_GAIN_CRITERIA if kind == "gain" else CNV_LOSS_CRITERIA


def is_valid_kind(kind: str) -> bool:
    return kind in VALID_KINDS


def is_valid_code(kind: str, code: str) -> bool:
    return code in _catalog(kind)


def clamp_points(kind: str, code: str, points: float) -> float:
    spec = _catalog(kind).get(code)
    if spec is None:
        return 0.0
    return max(spec["min"], min(spec["max"], float(points)))


def class_key_for_points(points: float) -> str:
    if points >= 0.99:
        return "cnv_class_5"
    if points >= 0.90:
        return "cnv_class_4"
    if points > -0.90:
        return "cnv_class_3"
    if points > -0.99:
        return "cnv_class_2"
    return "cnv_class_1"


def compute_classification(
    kind: str,
    criteria: Iterable[Mapping[str, object]],
) -> tuple[float, str, str]:
    """Return ``(point_total, class_key, class_label)`` from accepted criteria.

    Each item is a mapping with at least ``code``, ``points`` and ``accepted``.
    Points are clamped to each criterion's allowed range before summing. Every item
    counts, so the caller passes each criterion once (a save is checked so in
    ``structural_variant_review_pg._normalize_cnv_acmg_payload``).
    """

    total = 0.0
    for item in criteria:
        if not item.get("accepted"):
            continue
        code = str(item.get("code", ""))
        try:
            points = float(item.get("points", 0.0))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            points = 0.0
        total += clamp_points(kind, code, points)
    total = round(total, 2)
    class_key = class_key_for_points(total)
    return total, class_key, CLASS_LABELS[class_key]
