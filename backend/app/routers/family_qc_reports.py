"""Access to a family package's rendered sequencing-QC report (NanoPlot / MultiQC).

The report is a large HTML document produced by the analysis pipeline. It is treated
as **untrusted content**: it carries inline scripts and references external CDNs, so it
is never inlined into the application and never served from the application's own
origin without isolation. Two things enforce that:

* the response carries ``Content-Security-Policy: sandbox``, which loads the document
  into an opaque origin — it cannot read the application origin's storage (where the
  session token lives) or call the API as the user;
* the served path is never client-supplied. It comes from the path recorded on the
  sample at import time and is re-checked for containment inside the authorized
  package root, so this endpoint cannot be turned into an arbitrary file read.

Access is a two-step flow because the SPA authenticates with a bearer token, which a
plain ``<a href>`` navigation cannot carry: the client asks for a link, gets a
short-lived URL scoped to exactly this one report, and opens that.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.object_storage import object_exists, object_key, presigned_get_url, storage_is_remote
from ..core.postgres import get_postgres_session
from ..dependencies import get_current_user
from ..schemas import FamilyQcReportLinkOut
from ..services.family_package_common import _metadata_dict, _resolve_package_path
from ..services.family_package_source import _ensure_authorized_package_path
from ..services.metadata_service import CurrentUser, get_family_record


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/families", tags=["families"])

# The link is handed straight to a browser navigation, so it must outlive only the
# click that follows it.
QC_REPORT_LINK_TTL = timedelta(minutes=5)

_QC_REPORT_TOKEN_PURPOSE = "qc_report"

DATA_DIR = Path(__file__).resolve().parents[3] / "data"


def _qc_report_token(family_id: str, sample_id: str) -> str:
    """Sign a token that authorises exactly one report fetch.

    ``purpose`` pins it to this endpoint so a token minted here can never be replayed
    against the ordinary bearer-authenticated API.
    """
    return jwt.encode(
        {
            "purpose": _QC_REPORT_TOKEN_PURPOSE,
            "family_id": family_id,
            "sample_id": sample_id,
            "exp": datetime.now(timezone.utc) + QC_REPORT_LINK_TTL,
        },
        settings.secret_key,
        algorithm=settings.algorithm,
    )


def _verify_qc_report_token(token: str, family_id: str, sample_id: str) -> None:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=403, detail="QC report link is invalid or expired") from exc
    if (
        payload.get("purpose") != _QC_REPORT_TOKEN_PURPOSE
        or payload.get("family_id") != family_id
        or payload.get("sample_id") != sample_id
    ):
        raise HTTPException(status_code=403, detail="QC report link is invalid or expired")


async def _recorded_report_path(
    session: AsyncSession,
    *,
    family_id: str,
    sample_id: str,
    user: CurrentUser,
) -> str:
    """The package-relative report path recorded on the sample at import.

    Reading the family through ``get_family_record`` applies the project-scoped RBAC
    and confirms the sample belongs to this family.
    """
    family = await get_family_record(session, family_id, user)
    if sample_id not in {member.sample_id for member in family.members}:
        raise HTTPException(status_code=404, detail="Sample not found in family")
    result = await session.execute(
        text(
            """
            SELECT s.metadata
            FROM samples s
            JOIN family_members fm ON fm.sample_id = s.id
            JOIN families f ON f.id = fm.family_id
            WHERE f.family_id = :family_id AND s.sample_id = :sample_id
            """
        ),
        {"family_id": family_id, "sample_id": sample_id},
    )
    metadata = _metadata_dict(result.scalar_one_or_none())
    report = _metadata_dict(metadata.get("sequencing_qc")).get("report")
    if not isinstance(report, str) or not report.strip():
        raise HTTPException(status_code=404, detail="No QC report is recorded for this sample")
    return report.strip()


def _resolve_report_file(family_id: str, relative_path: str) -> Path:
    """Resolve the recorded relative path inside the family's package folder.

    ``_ensure_authorized_package_path`` restricts the root to the configured import
    roots and ``_resolve_package_path`` rejects anything that escapes it, so a
    tampered metadata value cannot reach outside the package.
    """
    for root in (DATA_DIR / "families" / family_id, DATA_DIR / family_id):
        try:
            package_root = _ensure_authorized_package_path(root)
        except HTTPException:
            continue
        if not package_root.is_dir():
            continue
        resolved = _resolve_package_path(package_root, relative_path)
        if resolved is not None and resolved.is_file():
            return resolved
    raise HTTPException(status_code=404, detail="QC report file is no longer available")


@router.get("/{family_id}/qc-report/{sample_id}/link", response_model=FamilyQcReportLinkOut)
async def get_family_qc_report_link(
    family_id: str,
    sample_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> FamilyQcReportLinkOut:
    """Short-lived URL for the sample's QC report, for the client to open in a new tab."""
    relative_path = await _recorded_report_path(
        session, family_id=family_id, sample_id=sample_id, user=user
    )
    expires_at = datetime.now(timezone.utc) + QC_REPORT_LINK_TTL
    if storage_is_remote():
        key = object_key(family_id, relative_path)
        if not object_exists(key):
            raise HTTPException(status_code=404, detail="QC report file is no longer available")
        return FamilyQcReportLinkOut(
            url=presigned_get_url(key, filename=Path(relative_path).name),
            expires_at=expires_at,
            filename=Path(relative_path).name,
        )
    # Confirm the file is there before handing out a link that would 404 on click.
    _resolve_report_file(family_id, relative_path)
    token = _qc_report_token(family_id, sample_id)
    return FamilyQcReportLinkOut(
        url=f"/families/{family_id}/qc-report/{sample_id}?token={token}",
        expires_at=expires_at,
        filename=Path(relative_path).name,
    )


@router.get("/{family_id}/qc-report/{sample_id}")
async def get_family_qc_report(
    family_id: str,
    sample_id: str,
    token: str = Query(...),
    session: AsyncSession = Depends(get_postgres_session),
):
    """Serve the QC report to a browser navigation carrying a link token.

    No bearer dependency: a top-level navigation cannot attach one. The link token is
    the authorisation, and it was only issued after the RBAC check in
    ``get_family_qc_report_link``.
    """
    _verify_qc_report_token(token, family_id, sample_id)
    result = await session.execute(
        text(
            """
            SELECT s.metadata
            FROM samples s
            JOIN family_members fm ON fm.sample_id = s.id
            JOIN families f ON f.id = fm.family_id
            WHERE f.family_id = :family_id AND s.sample_id = :sample_id
            """
        ),
        {"family_id": family_id, "sample_id": sample_id},
    )
    metadata = _metadata_dict(result.scalar_one_or_none())
    relative_path = _metadata_dict(metadata.get("sequencing_qc")).get("report")
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise HTTPException(status_code=404, detail="No QC report is recorded for this sample")
    if storage_is_remote():
        key = object_key(family_id, relative_path.strip())
        if not object_exists(key):
            raise HTTPException(status_code=404, detail="QC report file is no longer available")
        return RedirectResponse(
            presigned_get_url(key, filename=Path(relative_path).name), status_code=302
        )
    path = _resolve_report_file(family_id, relative_path.strip())
    return FileResponse(
        path,
        media_type="text/html",
        headers={
            # Opaque origin: the report's own scripts render, but they cannot reach the
            # application origin's storage or call the API as the signed-in user.
            "Content-Security-Policy": "sandbox allow-scripts allow-popups",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "private, no-store",
        },
    )
