"""Access to a family package's rendered sequencing-QC report (NanoPlot / MultiQC).

The report is a large HTML document produced by the analysis pipeline. It is treated
as **untrusted content**: it carries inline scripts and references external CDNs, so it
is never inlined into the application and never served from the application's own
origin without isolation. Two things enforce that:

* the response carries ``Content-Security-Policy: sandbox``, which loads the document
  into an opaque origin — it cannot read the application origin's storage (where the
  session token lives) or call the API as the user;
* the served path is never client-supplied. It comes from the path recorded on the
  sample at import time and is re-checked for containment inside the family's own
  folders, below the authorized import roots, so this endpoint cannot be turned into an
  arbitrary file read, nor into a read of another family's report.

Access is a two-step flow because the SPA authenticates with a bearer token, which a
plain ``<a href>`` navigation cannot carry: the client asks for a link, gets a
short-lived URL scoped to exactly this one report, and opens that.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from urllib.parse import urlencode

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.family_folders import PACKAGES_FOLDER, family_folders, path_in_family_folder
from ..core.object_storage import object_exists, object_key, presigned_get_url, storage_is_remote
from ..core.postgres import get_postgres_session
from ..core.url_path import url_path
from ..dependencies import get_current_user
from ..schemas import FamilyQcReportLinkOut
from ..services.family_package_common import _metadata_dict
from ..services.family_package_source import _ensure_authorized_package_path
from ..services.metadata_service import get_family_record
from ..services.access_control import CurrentUser


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


async def _recorded_report(session: AsyncSession, *, family_id: str, sample_id: str) -> str:
    """The report path recorded in the sample's metadata at import, as stored (unstripped);
    404 when none is recorded. No access check: the callers make their own."""
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
    return report


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
    report = await _recorded_report(session, family_id=family_id, sample_id=sample_id)
    return report.strip()


def _stored_report_url(family_id: str, relative_path: str) -> str:
    """Remote mode: a short-lived URL of the report's object under the storage prefix; 404
    when the object is gone. Both are requests to the object store, so the routes run this in
    a worker thread, off the event loop (SEC-14)."""
    key = object_key(family_id, relative_path)
    if not object_exists(key):
        raise HTTPException(status_code=404, detail="QC report file is no longer available")
    return presigned_get_url(key, filename=Path(relative_path).name)


def _resolve_report_file(family_id: str, relative_path: str) -> Path:
    """The file the recorded relative path names in the family's package folder,
    ``<data>/families/<family>``, else in its flat folder ``<data>/<family>``; 404 when
    neither holds it.

    The path comes back from ``samples.metadata`` as data: an edited row can hold ``..``
    or an absolute path. It is joined to the folder and normalised as a string, and one
    that leaves the folder is dropped before anything is looked up by it. One that stays
    is kept only when its real path lies inside that folder too, so a symlink (the package
    folder itself, or a folder or file in it) leads neither into another family's folder
    nor anywhere else, and a family ID that is no single folder name has no folder
    (``core/family_folders.py``). The folder must lie under the configured import roots
    (``_ensure_authorized_package_path``).
    """
    folders = family_folders(DATA_DIR, family_id)
    for parent in (PACKAGES_FOLDER, ""):
        folder = folders.get(parent)
        if folder is None:
            continue
        path = path_in_family_folder(folder, [folder], relative_path)
        if path is None:
            continue
        try:
            _ensure_authorized_package_path(Path(folder))
        except HTTPException:
            continue
        if os.path.isfile(path):
            return Path(path)
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
        return FamilyQcReportLinkOut(
            url=await asyncio.to_thread(_stored_report_url, family_id, relative_path),
            expires_at=expires_at,
            filename=Path(relative_path).name,
        )
    # Confirm the file is there before handing out a link that would 404 on click; the file
    # system is read in a worker thread (SEC-14).
    await asyncio.to_thread(_resolve_report_file, family_id, relative_path)
    token = _qc_report_token(family_id, sample_id)
    # Each ID one encoded segment: an ID may hold `/`, `?`, `#`, `%` or `..`.
    path = url_path("families", family_id, "qc-report", sample_id)
    return FamilyQcReportLinkOut(
        url=f"{path}?{urlencode({'token': token})}",
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
    relative_path = await _recorded_report(session, family_id=family_id, sample_id=sample_id)
    if storage_is_remote():
        return RedirectResponse(
            await asyncio.to_thread(_stored_report_url, family_id, relative_path.strip()),
            status_code=302,
        )
    path = await asyncio.to_thread(_resolve_report_file, family_id, relative_path.strip())
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
