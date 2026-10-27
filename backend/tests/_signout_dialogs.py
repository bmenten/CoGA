"""Sign a family out as the sign-out dialogs do (TRACE-19).

An acknowledgement covers the concerns its dialog showed: it carries the fingerprint of the
concerns its gate's refusal listed, and a sign-out whose concerns are no longer those is
refused with them as they are now (``report_signout_service.concerns_fingerprint``). So no
test acknowledges a gate blind. It signs out with no acknowledgement first and, each time a
gate refuses that it acknowledges, again with that acknowledgement and the fingerprint the
refusal carried, as the analyst does in the dialogs (``reportSignOut.tsx``).

``sign_out_report_through_the_dialogs`` calls the service, as the unit and integration tests
do; ``backend/tests/e2e/_harness.py::sign_out`` posts to the API with the same steps.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Mapping

from fastapi import HTTPException

# Each gate an analyst acknowledges in a dialog: the request's acknowledgement, its reason
# and the fingerprint of the concerns the dialog showed.
GATE_FIELDS: dict[str, tuple[str, str, str]] = {
    "evidence_drift": ("acknowledge_drift", "drift_acknowledgement_reason", "drift_concerns_fingerprint"),
    "sample_qc": ("acknowledge_qc", "qc_acknowledgement_reason", "qc_concerns_fingerprint"),
    "import_incomplete": (
        "acknowledge_import_incomplete",
        "import_incomplete_acknowledgement_reason",
        "import_incomplete_concerns_fingerprint",
    ),
}
_ACKNOWLEDGEMENT_FIELDS = frozenset(field for fields in GATE_FIELDS.values() for field in fields)


def without_acknowledgements(body: Mapping[str, Any]) -> dict[str, Any]:
    """``body`` with no gate acknowledged: what the dialogs send first."""
    return {key: value for key, value in body.items() if key not in _ACKNOWLEDGEMENT_FIELDS}


def acknowledging(
    sent: Mapping[str, Any],
    status: int,
    detail: Any,
    acknowledgements: Mapping[str, Any],
) -> dict[str, Any] | None:
    """What the dialogs send after a refusal: ``sent`` with the refused gate acknowledged as
    ``acknowledgements`` acknowledges it, with the fingerprint the refusal carried.

    None when the refusal is no gate ``acknowledgements`` acknowledges, and when that gate
    was acknowledged already: its concerns changed since, and the test sees that refusal.
    """
    if status != 409 or not isinstance(detail, Mapping):
        return None
    fields = GATE_FIELDS.get(str(detail.get("gate")))
    if fields is None:
        return None
    acknowledge, reason, fingerprint = fields
    if not acknowledgements.get(acknowledge) or fingerprint in sent:
        return None
    return {
        **sent,
        acknowledge: True,
        reason: acknowledgements.get(reason),
        fingerprint: detail.get("concerns_fingerprint"),
    }


async def sign_out_report_through_the_dialogs(
    sign_out_report: Callable[..., Awaitable[dict[str, Any]]],
    session: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    """``sign_out_report(session, **kwargs)`` as the dialogs call it: each gate that
    ``kwargs`` acknowledges is acknowledged once it refuses, with the fingerprint its refusal
    carried. A refusal of any other kind is raised, as is one of a gate ``kwargs`` does not
    acknowledge."""
    sent = without_acknowledgements(kwargs)
    while True:
        try:
            return await sign_out_report(session, **sent)
        except HTTPException as exc:
            following = acknowledging(sent, exc.status_code, exc.detail, kwargs)
            if following is None:
                raise
            sent = following
