"""A person's decision on a delivery of a watched import folder: approve it, or refuse it.

The import watcher (``import_watcher``) checks each package a pipeline delivers and records it
as ``ready`` or ``needs_attention`` (``import_deliveries``); nothing is imported until a person
decides. A superuser approves a delivery from the Package Import page, as a dry run or for real,
or refuses it with a reason; overwriting an existing family stays an administrator's (the
route's check, as for every overwrite).

- **Approve.** A delivery that is ``ready``, or that needs attention only because its family
  exists (``existing_family``, with ``existing_samples``) and the approver chooses update or
  overwrite. One whose package's members differ from the family beyond the members it adds
  (``package_members_differ_from_family``) is approved only with overwrite, which makes the
  package's members the family's pedigree. An ``imported`` delivery is approved again only with
  overwrite, when its family's record names it as its last import, made with update, while the
  family's reference delivery is ``changed_after_import``: only an overwrite ends that change (a
  failed overwrite leaves it ``imported``). Its warnings must be acknowledged. Its folder must be
  as it was checked (the same package fingerprint); one that changed is left to the watcher's next
  round, which makes it a new delivery. The approval names the revision of the package the approver saw
  (``import_delivery_revisions``: 0 when it was not corrected); one corrected since is refused.
  The import job is queued as the approver, into the project the package names, with the
  manifest the delivery keeps (made from the package as last corrected), and names the delivery
  (its id, run, package file's SHA-256 and fingerprint, the revision, the SHA-256 of the package
  it imports, and every correction: what it changed, why, by whom and when), which the family's
  package record keeps. A real approval makes the delivery ``queued``; a dry run leaves it as it
  is. The delivery is locked while it is decided, and the job, the delivery's change and its
  audit event are one transaction.
- **Refuse.** An open delivery (``incomplete``, ``ready``, ``needs_attention``), with a reason.
  Refusing the new delivery of a folder that changed after its import refuses that change: the
  imported delivery whose change is to the refused fingerprint is ``imported`` again, and the
  watcher never raises that folder state again (``package_changed_after_import.resolve_refused``).
  Approving it instead ends the change only with overwrite: an update skips the datasets the family
  holds, so the family's record keeps the delivery its data was imported from whole as the one the
  folder is compared with, and the change stays raised. A gone folder is confirmed as archived
  apart (``package_changed_after_import.archive_gone_delivery``).
- **The job's end.** A queued delivery whose import job has ended is ``imported`` or ``failed``
  (``sync_delivery_jobs``, at the start of each watch round).

Each decision is an event on the clinical audit chain ``system:import-inbox``, by the person
who made it, and is in the request audit too.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..core.sql import require_uuid
from ..schemas import FamilyPackageImportJobOut, ImportDeliveryOut
from .access_control import CurrentUser
from .clinical_audit_service import audit_actor, record_clinical_event
from .family_package_fingerprint import PackageFolderTooLarge, package_fingerprint
from .family_package_jobs import queue_family_import_job
from .coga_package import package_document_sha256
from .import_watcher import (
    IMPORT_INBOX_CHAIN,
    UPDATE_IMPORT_UNDER_A_CHANGE,
    WATCHER_ACTOR,
    get_import_delivery,
    latest_revision,
)

DELIVERY_NOT_FOUND = "Delivery not found"
# The issues an approver resolves by choosing update or overwrite: the family exists.
_RESOLVED_BY_A_CHOICE = frozenset({"existing_family", "existing_samples"})
# The issue an approver resolves only by choosing overwrite: the package's members differ from
# the family's beyond the members it adds, and an overwrite makes them the family's pedigree.
_RESOLVED_BY_AN_OVERWRITE = frozenset({"package_members_differ_from_family"})
OPEN_STATES = ("incomplete", "ready", "needs_attention")

DELIVERY_CHANGED = (
    "The delivery's folder changed since it was checked: nothing was queued. The watcher checks "
    "it again in its next round, as a new delivery."
)
DELIVERY_REVISED_SINCE = (
    "The delivery's package was corrected since you looked at it: look at it again, and approve "
    "it as it is now."
)
WARNINGS_NOT_ACKNOWLEDGED = (
    "The delivery has warnings: check them, and confirm that you did (acknowledge_warnings), to "
    "approve it."
)
REAPPROVAL_NEEDS_AN_OVERWRITE = (
    "The delivery is imported: it is approved again only to overwrite its family's data, after an "
    "update import of it left the family's package folder warning of a change."
)
NOT_REAPPROVABLE = (
    "The delivery is imported. Only a delivery imported with update, whose family's package folder "
    "still warns of a change since its last import as a new family or with overwrite, can be "
    "approved again, with overwrite."
)
MEMBERS_DIFFER_NEED_AN_OVERWRITE = (
    "The package's members differ from the family in CoGA beyond the members it adds: an update "
    "changes none the family holds. Correct the package, or overwrite, which makes its members the "
    "family's pedigree."
)


def _issue_codes(value: Any) -> set[str]:
    items = value if isinstance(value, list) else json.loads(value or "[]")
    return {str(item.get("code")) for item in items if isinstance(item, dict)}


async def _locked_delivery(session: AsyncSession, delivery_id: str) -> dict[str, Any]:
    delivery_uuid = require_uuid(delivery_id, DELIVERY_NOT_FOUND, status_code=404)
    row = (
        await session.execute(
            text(
                """
                SELECT id::text AS id, folder_path, folder_name, state, package_fingerprint,
                       marker_sha256, run_id, family_id, project_id::text AS project_id, issues,
                       warnings, manifest_text, package, origin
                FROM import_deliveries
                WHERE id = CAST(:id AS uuid)
                FOR UPDATE
                """
            ),
            {"id": delivery_uuid},
        )
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail=DELIVERY_NOT_FOUND)
    return dict(row)


async def _folder_fingerprint(folder: str) -> str | None:
    try:
        return (await asyncio.to_thread(package_fingerprint, Path(folder), kept_manifest_sha256=None)).sha256
    except (OSError, PackageFolderTooLarge):
        return None


async def _record(
    session: AsyncSession, *, user: CurrentUser | None, action: str, delivery: dict[str, Any], summary: str, after: dict[str, Any]
) -> None:
    await record_clinical_event(
        session,
        family_uuid=None,
        family_identifier=IMPORT_INBOX_CHAIN,
        variant_id=None,
        actor=audit_actor(user) if user is not None else WATCHER_ACTOR,
        actor_id=getattr(user, "id", None),
        action=action,
        summary=summary,
        after={"folder": delivery["folder_name"], "package_fingerprint": delivery["package_fingerprint"], **after},
        metadata={"delivery_id": delivery["id"]},
    )


async def _corrections(session: AsyncSession, delivery_id: str) -> list[dict[str, Any]]:
    """The corrections of the delivery's package, as the family's record keeps them."""
    rows = (
        await session.execute(
            text(
                """
                SELECT revision, changes, reason, created_by_name, created_at
                FROM import_delivery_revisions
                WHERE delivery_id = CAST(:id AS uuid)
                ORDER BY revision
                """
            ),
            {"id": delivery_id},
        )
    ).mappings().all()
    return [
        {
            "revision": int(row["revision"]),
            "changes": row["changes"] if isinstance(row["changes"], list) else json.loads(row["changes"] or "[]"),
            "reason": row["reason"],
            "by": row["created_by_name"],
            "at": row["created_at"].isoformat(),
        }
        for row in rows
    ]


async def approve_delivery(
    session: AsyncSession,
    *,
    delivery_id: str,
    user: CurrentUser,
    revision: int,
    dry_run: bool,
    conflict_mode: str,
    acknowledge_warnings: bool,
) -> tuple[ImportDeliveryOut, FamilyPackageImportJobOut]:
    """Approve the delivery ``delivery_id`` (see the module's docstring) as its package is at
    ``revision``: queue its import job, or its dry run, as ``user``. Refused: 404 for no such
    delivery; 409 for one in another state, one whose issues a choice does not resolve, one
    corrected since ``revision``, or one whose folder changed; 400 for a choice that does not fit
    it, or warnings not acknowledged."""
    delivery = await _locked_delivery(session, delivery_id)
    codes = _issue_codes(delivery["issues"])
    state = delivery["state"]
    # An imported delivery is approved again only to overwrite, after an update import of it left
    # its family's package folder warning of a change (the owner's decision of 2026-11-11).
    reapproval = state == "imported"
    if reapproval:
        if conflict_mode != "overwrite":
            raise HTTPException(status_code=400, detail=REAPPROVAL_NEEDS_AN_OVERWRITE)
        if not await _imported_with_update_under_a_change(session, delivery["id"]):
            raise HTTPException(status_code=409, detail=NOT_REAPPROVABLE)
    elif state not in ("ready", "needs_attention"):
        raise HTTPException(
            status_code=409,
            detail=f"The delivery is {state.replace('_', ' ')}: only one that is ready, or whose family exists, can be approved.",
        )
    existing = reapproval or "existing_family" in codes
    if state == "needs_attention" and not (existing and codes <= _RESOLVED_BY_A_CHOICE | _RESOLVED_BY_AN_OVERWRITE):
        raise HTTPException(
            status_code=409,
            detail=(
                "The delivery needs attention: resolve its issues in the package, which makes a new "
                "delivery, or, for its project, in CoGA, after which the watcher checks it again."
            ),
        )
    if existing and conflict_mode == "cancel":
        raise HTTPException(
            status_code=400,
            detail="The delivery's family exists: choose update (add missing datasets) or overwrite.",
        )
    if not existing and conflict_mode != "cancel":
        raise HTTPException(status_code=400, detail="The delivery is a new family: it is imported with cancel.")
    if codes & _RESOLVED_BY_AN_OVERWRITE and conflict_mode != "overwrite":
        raise HTTPException(status_code=400, detail=MEMBERS_DIFFER_NEED_AN_OVERWRITE)
    if _issue_codes(delivery["warnings"]) and not acknowledge_warnings:
        raise HTTPException(status_code=400, detail=WARNINGS_NOT_ACKNOWLEDGED)
    if delivery["project_id"] is None:
        raise HTTPException(status_code=409, detail="The delivery names no project CoGA has.")
    current, revised = await latest_revision(session, delivery["id"])
    if revision != current:
        raise HTTPException(status_code=409, detail=DELIVERY_REVISED_SINCE)
    if await _folder_fingerprint(delivery["folder_path"]) != delivery["package_fingerprint"]:
        raise HTTPException(status_code=409, detail=DELIVERY_CHANGED)
    package = revised or (
        delivery["package"] if isinstance(delivery["package"], dict) else json.loads(delivery["package"] or "null")
    )
    corrections = await _corrections(session, delivery["id"]) if current else []

    job = await queue_family_import_job(
        session,
        folder_path=delivery["folder_path"],
        project_id=delivery["project_id"],
        dry_run=dry_run,
        requested_family_id=delivery["family_id"] if existing else None,
        conflict_mode=conflict_mode,
        requested_by=user,
        manifest_text=delivery["manifest_text"],
        delivery={
            "id": delivery["id"],
            "run_id": delivery["run_id"],
            "marker_sha256": delivery["marker_sha256"],
            "package_fingerprint": delivery["package_fingerprint"],
            "revision": current,
            "package_sha256": package_document_sha256(package) if isinstance(package, dict) else None,
            "corrections": corrections,
            # ``manual``: a folder a person added by hand, whose package CoGA made.
            "origin": delivery["origin"],
            # Approved again, to overwrite, after its update import.
            **({"reapproval": True} if reapproval else {}),
        },
        commit=False,
    )
    await session.execute(
        text(
            """
            UPDATE import_deliveries
            SET job_id = CAST(:job AS uuid),
                state = CASE WHEN :dry_run THEN state ELSE 'queued' END,
                state_changed_at = CASE WHEN :dry_run THEN state_changed_at ELSE now() END
            WHERE id = CAST(:id AS uuid)
            """
        ),
        {"job": str(job.id), "dry_run": dry_run, "id": delivery["id"]},
    )
    await _record(
        session,
        user=user,
        action="import_delivery_dry_run" if dry_run else "import_delivery_queued",
        delivery=delivery,
        summary=f"Delivery {delivery['folder_name']}: {'dry run queued' if dry_run else 'approved, import queued'}",
        after={
            "state": state if dry_run else "queued",
            "job_id": str(job.id),
            "dry_run": dry_run,
            "conflict_mode": conflict_mode,
            "project_id": delivery["project_id"],
            "family_id": delivery["family_id"],
            "run_id": delivery["run_id"],
            "marker_sha256": delivery["marker_sha256"],
            "revision": current,
            "warnings_acknowledged": bool(acknowledge_warnings),
            **({"reapproval": True} if reapproval else {}),
        },
    )
    await session.commit()
    return await _delivery(session, delivery["id"]), job


async def _imported_with_update_under_a_change(session: AsyncSession, delivery_id: str) -> bool:
    """Whether the imported delivery ``delivery_id`` is the one a family's record names as its
    last import, made with update, while the family's reference delivery (its data's last import
    as a new family or with overwrite) is ``changed_after_import``: the folder CoGA serves the
    family's source files from is not the one imported whole, and only an overwrite ends that."""
    found = (
        await session.execute(
            text(f"SELECT {UPDATE_IMPORT_UNDER_A_CHANGE} FROM import_deliveries d WHERE d.id = CAST(:delivery_id AS uuid)"),
            {"delivery_id": delivery_id},
        )
    ).scalar_one_or_none()
    return bool(found)


async def refuse_delivery(session: AsyncSession, *, delivery_id: str, user: CurrentUser, reason: str) -> ImportDeliveryOut:
    """Refuse the open delivery ``delivery_id`` with ``reason``: it is ``refused``, and the watcher
    leaves its folder as it is until it changes. The change after its import of an imported
    delivery of the folder to this folder state ends with it (``resolve_refused``). 404 for no
    such delivery, 409 for one decided."""
    # Imported here: it reads the watcher's chain, as this module does.
    from .package_changed_after_import import resolve_refused

    delivery = await _locked_delivery(session, delivery_id)
    if delivery["state"] not in OPEN_STATES:
        raise HTTPException(
            status_code=409,
            detail=f"The delivery is {delivery['state'].replace('_', ' ')}: only an open one can be refused.",
        )
    await session.execute(
        text("UPDATE import_deliveries SET state = 'refused', state_changed_at = now() WHERE id = CAST(:id AS uuid)"),
        {"id": delivery["id"]},
    )
    await _record(
        session,
        user=user,
        action="import_delivery_refused",
        delivery=delivery,
        summary=f"Delivery {delivery['folder_name']}: refused",
        after={"state": "refused", "reason": reason, "family_id": delivery["family_id"], "run_id": delivery["run_id"]},
    )
    await resolve_refused(session, refused=delivery, user=user, reason=reason)
    await session.commit()
    return await _delivery(session, delivery["id"])


async def _delivery(session: AsyncSession, delivery_id: str) -> ImportDeliveryOut:
    found = await get_import_delivery(session, delivery_id)
    if found is None:  # pragma: no cover - the row was locked and updated in this request
        raise HTTPException(status_code=404, detail=DELIVERY_NOT_FOUND)
    return found


async def sync_delivery_jobs(session_factory: async_sessionmaker[AsyncSession]) -> int:
    """Record the end of each queued delivery's import job: ``imported`` when it completed,
    ``failed`` when it failed. Returns how many changed."""
    async with session_factory() as session:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT d.id::text AS id, d.folder_name, d.package_fingerprint, j.id::text AS job_id, j.status,
                           COALESCE((j.metadata -> 'delivery' ->> 'reapproval')::boolean, FALSE) AS reapproval
                    FROM import_deliveries d
                    JOIN family_import_jobs j ON j.id = d.job_id
                    WHERE d.state = 'queued' AND NOT j.dry_run AND j.status IN ('completed', 'failed')
                    FOR UPDATE OF d SKIP LOCKED
                    """
                )
            )
        ).mappings().all()
        for row in rows:
            completed = row["status"] == "completed"
            # A delivery approved again to overwrite, whose overwrite failed, is still the one
            # imported with update: the failed import put the family back as it was.
            state = "imported" if completed or row["reapproval"] else "failed"
            action = (
                "import_delivery_overwrite_failed"
                if row["reapproval"] and not completed
                else f"import_delivery_{'imported' if completed else 'failed'}"
            )
            await session.execute(
                text("UPDATE import_deliveries SET state = :state, state_changed_at = now() WHERE id = CAST(:id AS uuid)"),
                {"state": state, "id": row["id"]},
            )
            await _record(
                session,
                user=None,
                action=action,
                delivery=dict(row),
                summary=f"Delivery {row['folder_name']}: {'its overwrite failed, imported with update still' if action == 'import_delivery_overwrite_failed' else state}",
                after={"state": state, "job_id": row["job_id"], **({"reapproval": True} if row["reapproval"] else {})},
            )
        await session.commit()
    return len(rows)
