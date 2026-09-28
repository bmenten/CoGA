from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import HTTPException

from ..schemas import AcmgClassificationPayload
from . import acmg_points
from .review_pg_utils import _json_payload, _log_unreadable_classification


def _normalize_acmg_payload(
    payload: AcmgClassificationPayload | None,
) -> tuple[dict[str, Any] | None, int | None, str | None]:
    """Validate the submitted criteria and recompute the class/points server-side.

    Returns ``(blob, point_total, class_key)`` where ``blob`` is the JSON to store
    (or ``None`` to clear). The client-supplied total is ignored.
    """

    if payload is None or not payload.criteria:
        return None, None, None

    normalized_criteria: list[dict[str, Any]] = []
    for criterion in payload.criteria:
        code = (criterion.code or "").strip().upper()
        if not acmg_points.is_valid_code(code):
            raise HTTPException(status_code=400, detail=f"Unknown ACMG criterion: {criterion.code}")
        strength = (criterion.strength or "").strip()
        if strength not in acmg_points.VALID_STRENGTHS:
            raise HTTPException(status_code=400, detail=f"Invalid ACMG strength: {criterion.strength}")
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
    """The stored classification, or None when there is none or it cannot be read.

    ``None`` alone cannot tell those apart, so an unreadable record is logged here and
    the review serializer marks it (``acmg_unreadable``) instead of dropping it (#514).
    """
    if not value:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError) as exc:
            _log_unreadable_classification("ACMG classification", exc)
            return None
        if value is None:  # a stored JSON null is no record, not a broken one
            return None
    try:
        return AcmgClassificationPayload.model_validate(value)
    except Exception as exc:  # noqa: BLE001 - served as unreadable, never raised
        _log_unreadable_classification("ACMG classification", exc)
        return None
