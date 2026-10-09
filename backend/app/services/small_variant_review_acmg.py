from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import HTTPException

from ..schemas import AcmgClassificationPayload
from . import acmg_points
from .review_pg_utils import _json_payload, _read_stored_classification


def _normalize_acmg_payload(
    payload: AcmgClassificationPayload | None,
) -> tuple[dict[str, Any] | None, int | None, str | None]:
    """Validate the submitted criteria and recompute the class/points server-side.

    Returns ``(blob, point_total, class_key)`` where ``blob`` is the JSON to store
    (or ``None`` to clear). The client-supplied total is ignored.

    Each criterion must be known, listed once and applied at a strength it takes
    (``acmg_points.ALLOWED_STRENGTHS``), as the classification dialog sends them; any other
    payload is refused with 400. Only a save is checked: a stored classification is read
    back as it was written.
    """

    if payload is None or not payload.criteria:
        return None, None, None

    normalized_criteria: list[dict[str, Any]] = []
    listed: set[str] = set()
    for criterion in payload.criteria:
        code = (criterion.code or "").strip().upper()
        if not acmg_points.is_valid_code(code):
            raise HTTPException(status_code=400, detail=f"Unknown ACMG criterion: {criterion.code}")
        # Every entry is scored, so a repeated code would count twice (PVS1 sent twice
        # scores +16) and leave the record ambiguous; the dialog holds one entry per code.
        if code in listed:
            raise HTTPException(status_code=400, detail=f"Repeated ACMG criterion: {code}")
        listed.add(code)
        strength = (criterion.strength or "").strip()
        if strength not in acmg_points.VALID_STRENGTHS:
            raise HTTPException(status_code=400, detail=f"Invalid ACMG strength: {criterion.strength}")
        if not acmg_points.is_allowed_strength(code, strength):
            raise HTTPException(status_code=400, detail=f"Invalid ACMG strength for {code}: {strength}")
        normalized_criteria.append(
            {
                "code": code,
                "strength": strength,
                "accepted": bool(criterion.accepted),
                "evidence": (criterion.evidence or "").strip() or None,
                "auto_suggested": bool(criterion.auto_suggested),
            }
        )

    point_total, class_key, class_label = acmg_points.compute_classification(normalized_criteria)
    blob = {
        "criteria": normalized_criteria,
        "point_total": point_total,
        "classification": class_label,
        "vus_tier": acmg_points.vus_tier_for_points(point_total, class_key),
    }
    return blob, point_total, class_key


def _acmg_json_or_none(value: Any) -> str | None:
    """Serialize the ACMG blob for a JSONB column, or None for SQL NULL."""
    if not value:
        return None
    return _json_payload(value)


def _json_or_none(value: Any) -> str | None:
    """Serialize a JSONB column value, or None for SQL NULL."""
    return None if value is None else _json_payload(value)


def build_evidence_snapshot(record: Any, captured_at: datetime) -> dict[str, Any]:
    """Freeze the evidence a classification was based on (clinical traceability).

    The annotation-set identity (``annotation_set_hash``) is the cheap drift key —
    it changes whenever any annotation changes — alongside the ClinVar significance,
    the most decision-relevant value, read from the per-transcript annotations.
    """
    clinvar = None
    for annotation in getattr(record, "annotations", None) or []:
        value = annotation.get("clinvar") or annotation.get("clinvarClinicalSignificance")
        text_value = str(value).strip() if value not in (None, "", ".", "-") else ""
        if text_value:
            clinvar = text_value
            break
    return {
        "annotation_version": getattr(record, "annotation_version", None),
        "annotation_set_hash": getattr(record, "annotation_set_hash", None),
        "clinvar": clinvar,
        "captured_at": captured_at.isoformat(),
    }


def _deserialize_acmg(value: Any) -> AcmgClassificationPayload | None:
    """The stored ACMG classification, or None (see ``_read_stored_classification``)."""
    return _read_stored_classification(value, AcmgClassificationPayload, "ACMG classification")
