"""A folder a person adds to Deliveries by hand: CoGA makes its package from the folder.

Phase 3 of the owner's plan of 2026-11-10 (docs/coga-package.md): a family added by hand goes
the way a pipeline's delivery goes. A superuser names a package folder under the import roots, the
project it belongs to and, where they differ from the folder's, the family ID and the analysis.
CoGA makes the package that a pipeline's ``coga-package.json`` would hold: the project, its
assembly, the analysis and the family, with the members and datasets Discover proposes from the
folder (``import_watcher._proposal``: from its PED, its per-sample folders and its layout), and
records it as a delivery of origin ``manual`` (``import_deliveries.origin``). It is then checked as
a pipeline's delivery is (``import_watcher.assess_content``), and corrected, approved or refused
the same way (``import_delivery_revisions``, ``import_delivery_review``).

What differs from a pipeline's delivery:

- CoGA writes nothing in the folder, and keeps the package it made with the delivery.
- No file list with checksums is made (the owner's decision of 2026-11-10): the folder's
  fingerprint (each file's size and time) guards it from now until the import, which records each
  file it reads with its SHA-256 (``raw_import_files``), as an import of a typed folder does.
- No watcher looks at it before its import: what CoGA holds changing (a project added, a family
  imported) does not check it again; a correction does. After its import the watcher follows its
  folder as it does a watched delivery's (``package_changed_after_import``): the listing its
  fingerprint was taken over is kept with it (``file_listing``), so a change says which files
  changed. A changed folder is added again by hand, and that delivery imported or refused.

Refused: a folder outside the import roots or that cannot be read, a folder that holds its own ``coga-package.json`` (deliver it through a watched
import folder), a family ID no family can be stored under, a project CoGA does not have, and a
second open delivery of the same folder (409, naming it). The addition is an event on the clinical
audit chain ``system:import-inbox``, by the person who made it.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.postgres import get_postgres_sessionmaker
from ..core.sql import require_uuid
from ..schemas import ImportDeliveryOut
from .access_control import CurrentUser
from .coga_package import PACKAGE_FILE_NAME, CogaPackage, package_document
from .coga_package_manifest import proposed_sections
from .family_identifiers import identifier_problem
from .family_package_common import manifest_text_sha256
from .family_package_fingerprint import PackageFolderTooLarge, package_listing
from .family_package_source import _authorized_local_roots, _ensure_authorized_package_path
from .import_delivery_review import _delivery, _record
from .import_watcher import OPEN_STATES, DeliveryAssessment, _issue, _proposal, assess_content

MANUAL_PIPELINE_NAME = "CoGA, added by hand"
ADDED_BY_HAND_WARNING = (
    "Added by hand: CoGA made the package from the folder and checked no checksums. The folder's "
    "fingerprint guards it until the import, which records each file it reads with its SHA-256."
)


def _import_root(folder: Path) -> str:
    """The import root the folder lies under, as the delivery's ``watch_root`` names it; the
    folder's parent where no root is configured (development)."""
    for root in _authorized_local_roots():
        if folder == root or root in folder.parents:
            return str(root)
    return str(folder.parent)


async def _project(session: AsyncSession, project_id: str) -> tuple[str, str, str | None]:
    project_uuid = require_uuid(project_id, "Project not found", status_code=404)
    row = (
        await session.execute(
            text(
                """
                SELECT p.id::text AS id, p.name, a.assembly_name
                FROM projects p
                LEFT JOIN assemblies a ON a.id = p.assembly_id
                WHERE p.id = CAST(:id AS uuid)
                """
            ),
            {"id": project_uuid},
        )
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return str(row["id"]), str(row["name"]), row["assembly_name"]


async def add_manual_delivery(
    session: AsyncSession,
    *,
    folder_path: str,
    project_id: str,
    family_id: str | None,
    analysis_type: str | None,
    user: CurrentUser,
) -> ImportDeliveryOut:
    """Add the package folder ``folder_path`` to Deliveries by hand (see the module's docstring),
    for the project ``project_id``, as the family ``family_id`` (the folder's name when None), with
    the analysis ``analysis_type`` when given: make its package, check it, and record it as
    ``user``. Returns the delivery."""
    # The project first: an ID that names none is refused as unknown, whatever the folder.
    project_uuid, project_name, assembly = await _project(session, project_id)
    folder = await asyncio.to_thread(_ensure_authorized_package_path, Path(folder_path))
    if not await asyncio.to_thread(folder.is_dir):
        raise HTTPException(status_code=404, detail="Package folder not found")
    if await asyncio.to_thread(os.path.lexists, folder / PACKAGE_FILE_NAME):
        raise HTTPException(
            status_code=409,
            detail=(
                f"The folder holds its own {PACKAGE_FILE_NAME}: deliver it through a watched import "
                "folder, whose watcher checks it against its checksums."
            ),
        )
    family = (family_id or "").strip() or folder.name
    problem = identifier_problem(family)
    if problem is not None:
        raise HTTPException(status_code=400, detail=f"The family ID {problem}.")

    # One addition of a folder at a time: two at once would both find no open delivery.
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"coga:manual-delivery:{folder}"})
    open_one = (
        await session.execute(
            text(
                """
                SELECT id::text FROM import_deliveries
                WHERE folder_path = :folder AND state = ANY(CAST(:open AS text[]))
                LIMIT 1
                """
            ),
            {"folder": str(folder), "open": list(OPEN_STATES)},
        )
    ).first()
    if open_one is not None:
        raise HTTPException(
            status_code=409,
            detail=f"The folder is a delivery waiting for a decision already ({open_one[0]}): correct, approve or refuse that one.",
        )
    try:
        listing = await asyncio.to_thread(package_listing, folder)
    except (OSError, PackageFolderTooLarge) as exc:
        raise HTTPException(status_code=400, detail="The folder could not be read whole.") from exc
    fingerprint = listing.fingerprint.sha256

    run_id = f"manual-{uuid4().hex[:12]}"
    document: dict[str, Any] = {
        "schema": "coga-package/1",
        "status": "complete",
        "written_at": datetime.now(UTC).isoformat(),
        "pipeline": {"name": MANUAL_PIPELINE_NAME, "version": settings.app_version, "run_id": run_id},
        "project": project_name,
        "family": {"family_id": family},
        "extra": {"origin": "manual"},
    }
    if assembly:
        document["assembly"] = assembly
    if analysis_type:
        document["analysis_type"] = analysis_type
    made = CogaPackage.model_validate(document)
    package, proposal_warnings, proposal_issues = await _proposal(folder, made)
    assessment = DeliveryAssessment(
        state="needs_attention",
        run_id=run_id,
        family_id=family,
        package=package_document(package),
        package_check={
            "origin": "manual",
            "issues": [],
            "warnings": [issue.model_dump() for issue in [_issue("added_by_hand", ADDED_BY_HAND_WARNING), *proposal_warnings]],
            "sha256": {},
            "proposed": proposed_sections(made),
            "proposal_issues": [issue.model_dump() for issue in proposal_issues],
        },
    )
    await assess_content(get_postgres_sessionmaker(), folder, assessment, base=package, effective=package)

    delivery_id = (
        await session.execute(
            text(
                """
                INSERT INTO import_deliveries (
                    watch_root, folder_path, folder_name, package_fingerprint, run_id, family_id,
                    project_id, state, issues, warnings, validation, manifest_text, manifest_sha256,
                    package, package_check, file_listing, origin
                )
                VALUES (
                    :root, :folder, :name, :fingerprint, :run_id, :family_id, CAST(:project_id AS uuid),
                    :state, CAST(:issues AS jsonb), CAST(:warnings AS jsonb), CAST(:validation AS jsonb),
                    :manifest_text, :manifest_sha256, CAST(:package AS jsonb), CAST(:package_check AS jsonb),
                    CAST(:file_listing AS jsonb), 'manual'
                )
                RETURNING id::text
                """
            ),
            {
                "root": _import_root(folder),
                "folder": str(folder),
                "name": folder.name,
                "fingerprint": fingerprint,
                "run_id": run_id,
                "family_id": family,
                "project_id": assessment.project_id or project_uuid,
                "state": assessment.state,
                "issues": json.dumps([issue.model_dump() for issue in assessment.issues]),
                "warnings": json.dumps([issue.model_dump() for issue in assessment.warnings]),
                "validation": json.dumps(assessment.validation) if assessment.validation is not None else None,
                "manifest_text": assessment.manifest_text,
                "manifest_sha256": (
                    manifest_text_sha256(assessment.manifest_text) if assessment.manifest_text is not None else None
                ),
                "package": json.dumps(assessment.package),
                "package_check": json.dumps(assessment.package_check),
                "file_listing": json.dumps(listing.entries) if listing.entries is not None else None,
            },
        )
    ).scalar_one()
    delivery = {"id": str(delivery_id), "folder_name": folder.name, "package_fingerprint": fingerprint}
    await _record(
        session,
        user=user,
        action="import_delivery_added",
        delivery=delivery,
        summary=f"Delivery {folder.name}: added by hand; {assessment.state.replace('_', ' ')}",
        after={
            "origin": "manual",
            "run_id": run_id,
            "family_id": family,
            "project_id": project_uuid,
            "state": assessment.state,
            "issues": [issue.code for issue in assessment.issues],
        },
    )
    await session.commit()
    return await _delivery(session, str(delivery_id))
