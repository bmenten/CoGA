"""A package folder that changes, or goes, after its import (the owner's decisions of 2026-11-11).

A family's record names the delivery its data was last imported from (``families.metadata.
package_import.delivery``), and CoGA serves the family's read files (IGV), signal tracks and QC
report from that delivery's package folder, where it lies. An import with update skips the
datasets the family holds already, so only an import that read the family's data whole, as a new
family or with overwrite, says what the source files were: the record keeps that delivery as its
**reference** (``package_import.reference_delivery``; an update keeps the reference the record
had, and the first package import of a family is its reference whatever its mode), and the
import's mode (``package_import.import_mode``).

**The rule.** The folder CoGA serves a family's source files from (its record's delivery's) must be
as the reference was imported: the same package fingerprint. So the import watcher follows, each
round (``follow_imported_deliveries``, from ``import_watcher.run_watch_round``), for each family's
reference delivery, the folder of the delivery its record names, by its package fingerprint alone:
each file's size and modification time, nothing of its content, so a slow share costs one listing
of the folder. The change is kept on the reference delivery, whatever its origin (a watched
folder's, or one added by hand):

- **Changed.** The folder's fingerprint differs from the reference's: the reference is
  ``changed_after_import``, and its ``source_change`` names the files added, removed and changed
  (in size or modification time), read against the listing kept with the reference
  (``import_deliveries.file_listing``), each list to ``NAMED_PATHS`` paths with its count. A
  folder kept without a listing (more entries than ``PACKAGE_LISTING_MAX_ENTRIES``) is said to
  have changed without naming its files. The folder's new delivery imported with update keeps the
  change raised (the record still names the same reference, and the folder still differs from
  it); so does an update from another folder, whose files CoGA then serves.
- **Gone.** The folder cannot be listed for ``GONE_AFTER_ROUNDS`` rounds while the folder it lies
  under (its watched folder; the import root of one added by hand) lists something: the same
  state, whose change says that the source files are no longer available. A folder it lies under
  that cannot be read, or that lists nothing (a share that is not mounted), decides nothing.
- **Archived.** A superuser confirms a gone folder as deliberately gone, with a reason
  (``archive_gone_delivery``): the reference is ``archived``. A sign-out is no longer warned of
  it, but the family page says the source files are archived and no longer available, and a
  signed record freezes it. A folder that comes back as imported ends it; one that comes back
  otherwise is a change again.

The family page shows the change (``family_package_source_change``) and a sign-out warns of it, to
acknowledge with a reason (``report_signout_service``, the gate ``package_source_change``); a
sign-out also takes the folder's fingerprint itself (``check_family_package_folder``), so a change
the watcher has not seen yet is warned of too. The change ends only:

- by itself, when the folder is as the reference was imported again (the same fingerprint);
- when a delivery of the family is imported with overwrite: the record's reference is then that
  delivery, and the one it was before is no longer followed (``end_changes_no_family_names``);
- when the folder's new delivery is refused with a reason (``resolve_refused``): a folder state a
  refused delivery of the folder has is never raised again, and a state neither imported nor
  refused is.

Each transition is an event on the clinical audit chain ``system:import-inbox``; the log names
counts, never a folder or a family.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..core.coga_logging import describe_error
from ..core.config import settings
from ..core.sql import require_uuid
from ..schemas import ImportDeliveryOut
from .access_control import CurrentUser
from .clinical_audit_service import audit_actor, record_clinical_event
from .family_package_fingerprint import PackageFolderTooLarge, PackageListing, package_listing
from .family_package_source import local_family_package_folder
from .import_watcher import GONE_AFTER_ROUNDS, IMPORT_INBOX_CHAIN, WATCHER_ACTOR, _names_in, get_import_delivery

logger = logging.getLogger(__name__)

# The state of a reference delivery whose followed folder changed, or went, after its import.
CHANGED_STATE = "changed_after_import"
# The state of a reference delivery whose gone folder a superuser confirmed as deliberately gone.
ARCHIVED_STATE = "archived"
# The states that keep a change (``source_change``): the family page shows it.
CHANGE_STATES = (CHANGED_STATE, ARCHIVED_STATE)
# The states of a delivery imported: the watcher follows while a family's record names it as its
# reference.
FOLLOWED_STATES = ("imported", *CHANGE_STATES)
# The most paths of each kind (added, removed, changed) a change names; its counts say how many.
NAMED_PATHS = 50
# The longest a sign-out waits for the listing of a family's package folder (a slow share): one
# not listed by then is a folder that cannot be read, which warns, never one as imported.
SIGN_OUT_FOLDER_CHECK_SECONDS = 30.0

# The keys of a family's package record (``families.metadata.package_import``) this module reads:
# the delivery its data was last imported from whole (as a new family or with overwrite), and the
# mode of its last import (``new_family``, ``update`` or ``overwrite``).
REFERENCE_KEY = "reference_delivery"
IMPORT_MODE_KEY = "import_mode"
# The modes of an import that reads the family's data whole: its delivery is the reference.
WHOLE_IMPORT_MODES = frozenset({"new_family", "overwrite"})

ChangeKind = Literal["changed", "gone"]
Resolution = Literal["as_imported", "refused", "overwritten", "not_named"]

# The reference delivery ``d`` a family ``f``'s record names.
_REFERENCE_OF_A_FAMILY = "f.metadata -> 'package_import' -> 'reference_delivery' ->> 'id' = d.id::text"
# The delivery ``n`` a family ``f``'s record names as the one its last import read: CoGA serves the
# family's source files from its folder.
_NAMED_BY_THE_RECORD = "n.id::text = f.metadata -> 'package_import' -> 'delivery' ->> 'id'"


def reference_delivery(
    *, import_mode: str, delivery: Mapping[str, Any], previous_reference: Any
) -> dict[str, Any]:
    """The reference delivery a family's package record keeps after an import of ``delivery``
    with ``import_mode`` (the owner's decision of 2026-11-11): ``delivery`` for an import that
    read the family's data whole (a new family, an overwrite) or for the family's first package
    import (no ``previous_reference``); the record's ``previous_reference`` for an update, which
    skips the datasets the family holds."""
    if import_mode in WHOLE_IMPORT_MODES or not isinstance(previous_reference, Mapping):
        return dict(delivery)
    return dict(previous_reference)


def listing_difference(
    before: Mapping[str, Any] | None, after: Mapping[str, Any] | None
) -> dict[str, Any]:
    """What changed from the listing ``before`` (kept with the delivery) to ``after`` (the folder
    now): the paths added, removed, and changed (another size, modification time or kind), each
    sorted and to ``NAMED_PATHS``, with their counts. Without either listing (a folder of more
    entries than a listing keeps) nothing is named, and ``listed`` is false."""
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        return {
            "listed": False,
            "added": [],
            "removed": [],
            "changed": [],
            "added_count": None,
            "removed_count": None,
            "changed_count": None,
        }
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(path for path in set(before) & set(after) if list(before[path]) != list(after[path]))
    return {
        "listed": True,
        "added": added[:NAMED_PATHS],
        "removed": removed[:NAMED_PATHS],
        "changed": changed[:NAMED_PATHS],
        "added_count": len(added),
        "removed_count": len(removed),
        "changed_count": len(changed),
    }


def changed_source(fingerprint: str | None, difference: Mapping[str, Any], detected_at: str) -> dict[str, Any]:
    """A delivery's ``source_change`` for a folder that changed: its fingerprint now (None for one
    too large to fingerprint) and what changed."""
    return {"kind": "changed", "fingerprint": fingerprint, **difference, "detected_at": detected_at}


def gone_source(detected_at: str) -> dict[str, Any]:
    """A delivery's ``source_change`` for a folder that is gone, or cannot be read."""
    return {"kind": "gone", "fingerprint": None, **listing_difference(None, None), "detected_at": detected_at}


@dataclass(frozen=True, slots=True)
class FolderNow:
    """A followed folder as a round finds it: ``listing`` when it could be listed; ``gone`` when it
    cannot be (missing, unreadable); neither for one too large to fingerprint, which changed."""

    listing: PackageListing | None = None
    gone: bool = False

    @property
    def fingerprint(self) -> str | None:
        return self.listing.fingerprint.sha256 if self.listing is not None else None


def _folder_now(folder: str) -> FolderNow:
    try:
        return FolderNow(listing=package_listing(folder))
    except PackageFolderTooLarge:
        return FolderNow()
    except OSError:
        return FolderNow(gone=True)


@dataclass(frozen=True, slots=True)
class Step:
    """What a round does with one followed delivery: ``raise`` a change (``change``), ``resolve``
    it (``resolution``), ``count`` a round its folder could not be listed, or ``keep`` it as it is.
    ``missing_rounds`` is the delivery's count of such rounds after the step."""

    action: Literal["raise", "resolve", "count", "keep"]
    missing_rounds: int
    change: dict[str, Any] | None = None
    resolution: Resolution | None = None


def decide(
    *,
    state: str,
    imported_fingerprint: str,
    change: Mapping[str, Any] | None,
    missing_rounds: int,
    now: FolderNow,
    refused: bool,
    difference: Mapping[str, Any] | None = None,
    detected_at: str = "",
) -> Step:
    """What a round does with a followed reference delivery (``state``, imported with
    ``imported_fingerprint``, with its ``change`` when it is changed or archived already and its
    count of ``missing_rounds``), the folder it is followed by found as ``now``; ``refused``: a
    refused delivery of the folder has the folder's fingerprint now. ``difference`` is what
    changed since the listing kept with the delivery (``listing_difference``), read only when a
    change is raised. An archived delivery whose folder is still gone is kept as it is."""
    archived = state == ARCHIVED_STATE
    raised = state in CHANGE_STATES
    if now.gone:
        if archived:
            return Step("keep", missing_rounds)
        missing = missing_rounds + 1
        if missing >= GONE_AFTER_ROUNDS and not (raised and change is not None and change.get("kind") == "gone"):
            return Step("raise", missing, change=gone_source(detected_at))
        return Step("count", missing)
    fingerprint = now.fingerprint
    if fingerprint == imported_fingerprint:
        return Step("resolve", 0, resolution="as_imported") if raised else Step("keep", 0)
    if refused:
        return Step("resolve", 0, resolution="refused") if raised else Step("keep", 0)
    # Raised already for the folder as it is now (one too large to fingerprint has none).
    if (
        state == CHANGED_STATE
        and change is not None
        and change.get("kind") == "changed"
        and change.get("fingerprint") == fingerprint
    ):
        return Step("keep", 0)
    return Step("raise", 0, change=changed_source(fingerprint, difference or listing_difference(None, None), detected_at))


@dataclass(slots=True)
class FollowRound:
    """What a round of following imported deliveries did, in counts."""

    followed: int = 0
    changed: int = 0
    gone: int = 0
    resolved: dict[str, int] = field(default_factory=dict)


async def _record(
    session: AsyncSession,
    *,
    action: str,
    delivery_id: str,
    after: dict[str, Any],
    summary: str,
    user: CurrentUser | None = None,
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
        after=after,
        metadata={"delivery_id": delivery_id},
    )


def _json_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _describe(change: Mapping[str, Any]) -> str:
    """A change in the words an event's summary uses."""
    if change.get("kind") == "gone":
        return "its folder is gone or cannot be read"
    if not change.get("listed"):
        return "its folder changed"
    return (
        f"its folder changed: {change.get('added_count')} file(s) added, {change.get('removed_count')} "
        f"removed, {change.get('changed_count')} changed"
    )


async def _refused_delivery(session: AsyncSession, folder: str, fingerprint: str | None) -> str | None:
    """The refused delivery of ``folder`` with ``fingerprint``, the latest; None when none was."""
    if fingerprint is None:
        return None
    row = (
        await session.execute(
            text(
                """
                SELECT id::text FROM import_deliveries
                WHERE folder_path = :folder AND package_fingerprint = :fingerprint AND state = 'refused'
                ORDER BY state_changed_at DESC
                LIMIT 1
                """
            ),
            {"folder": folder, "fingerprint": fingerprint},
        )
    ).first()
    return str(row[0]) if row is not None else None


async def end_changes_no_family_names(session_factory: async_sessionmaker[AsyncSession]) -> int:
    """End the change of each changed or archived delivery no family's record names as its
    reference any more: back to ``imported``, recorded with the reference the family's record
    names now (a delivery of the family, or of the folder, imported with overwrite: resolution
    ``overwritten``), or, when none does, ``not_named``. A delivery whose family's new reference
    is still being imported (``queued``) is left to the round after its job's end: a failed
    import that puts the family back puts its record back too. Returns how many ended."""
    async with session_factory() as session:
        rows = (
            await session.execute(
                text(
                    f"""
                    SELECT d.id::text AS id, d.folder_path, d.folder_name, d.package_fingerprint, d.family_id
                    FROM import_deliveries d
                    WHERE d.state = ANY(CAST(:raised AS text[]))
                      AND NOT EXISTS (SELECT 1 FROM families f WHERE {_REFERENCE_OF_A_FAMILY})
                      AND NOT EXISTS (
                          SELECT 1 FROM families f
                          JOIN import_deliveries q
                            ON q.id::text = f.metadata -> 'package_import' -> 'reference_delivery' ->> 'id'
                          WHERE q.state = 'queued' AND (q.folder_path = d.folder_path OR f.family_id = d.family_id)
                      )
                    FOR UPDATE OF d SKIP LOCKED
                    """
                ),
                {"raised": list(CHANGE_STATES)},
            )
        ).mappings().all()
        for row in rows:
            named = (
                await session.execute(
                    text(
                        f"""
                        SELECT d.id::text, f.family_id FROM import_deliveries d
                        JOIN families f ON {_REFERENCE_OF_A_FAMILY}
                        WHERE (d.folder_path = :folder OR f.family_id = :family_id) AND d.id <> CAST(:id AS uuid)
                        ORDER BY d.state_changed_at DESC
                        LIMIT 1
                        """
                    ),
                    {"folder": row["folder_path"], "family_id": row["family_id"], "id": row["id"]},
                )
            ).first()
            resolution: Resolution = "overwritten" if named is not None else "not_named"
            await session.execute(
                text(
                    """
                    UPDATE import_deliveries
                    SET state = 'imported', source_change = NULL, missing_rounds = 0, state_changed_at = now()
                    WHERE id = CAST(:id AS uuid)
                    """
                ),
                {"id": row["id"]},
            )
            await _record(
                session,
                action="import_delivery_change_resolved",
                delivery_id=row["id"],
                after={
                    "folder": row["folder_name"],
                    "package_fingerprint": row["package_fingerprint"],
                    "state": "imported",
                    "resolution": resolution,
                    "imported_delivery_id": str(named[0]) if named is not None else None,
                    "family_id": str(named[1]) if named is not None else None,
                },
                summary=(
                    f"Delivery {row['folder_name']}: the change of its folder ended, "
                    + (
                        "the family was imported again with overwrite"
                        if named is not None
                        else "no family's record names it any more"
                    )
                ),
            )
        await session.commit()
    return len(rows)


async def _root_lists_something(root: str) -> bool:
    names = await asyncio.to_thread(_names_in, Path(root))
    return bool(names)


async def follow_imported_deliveries(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    skipped_roots: set[str] | frozenset[str] = frozenset(),
    seen_fingerprints: Mapping[str, str] | None = None,
) -> FollowRound:
    """Follow, for each family's reference delivery, the folder of the delivery the family's
    record names (see the module's docstring). ``skipped_roots`` are the watched folders the round
    skipped (not mounted): nothing is decided on a folder under one of them. ``seen_fingerprints``
    are the fingerprints the round took of the folders it looked at, by path: a folder seen as
    the reference was imported is not listed again."""
    result = FollowRound()
    ended = await end_changes_no_family_names(session_factory)
    if ended:
        result.resolved["overwritten_or_not_named"] = ended
    async with session_factory() as session:
        rows = (
            await session.execute(
                text(
                    f"""
                    SELECT d.id::text AS id, d.folder_name, d.package_fingerprint, d.state, d.source_change,
                           d.missing_rounds, f.family_id,
                           COALESCE(n.watch_root, d.watch_root) AS watch_root,
                           COALESCE(n.folder_path, d.folder_path) AS folder_path,
                           COALESCE(n.folder_name, d.folder_name) AS followed_name
                    FROM families f
                    JOIN import_deliveries d ON {_REFERENCE_OF_A_FAMILY}
                    LEFT JOIN import_deliveries n ON {_NAMED_BY_THE_RECORD}
                    WHERE d.state = ANY(CAST(:followed AS text[]))
                    ORDER BY COALESCE(n.folder_path, d.folder_path), d.id
                    """
                ),
                {"followed": list(FOLLOWED_STATES)},
            )
        ).mappings().all()
    readable: dict[str, bool] = {}
    seen = seen_fingerprints or {}
    for row in rows:
        root = str(row["watch_root"])
        if root in skipped_roots:
            continue
        if root not in readable:
            readable[root] = await _root_lists_something(root)
        if not readable[root]:
            continue
        result.followed += 1
        folder = str(row["folder_path"])
        if row["state"] == "imported" and seen.get(folder) == row["package_fingerprint"]:
            continue
        try:
            done = await _follow_one(session_factory, dict(row), await asyncio.to_thread(_folder_now, folder))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - one folder's failure never stops the round
            logger.warning("Import watcher could not follow an imported delivery: %s", describe_error(exc))
            continue
        if done is None:
            continue
        if done.action == "raise" and done.change is not None:
            if done.change.get("kind") == "gone":
                result.gone += 1
            else:
                result.changed += 1
        elif done.action == "resolve" and done.resolution is not None:
            result.resolved[done.resolution] = result.resolved.get(done.resolution, 0) + 1
    return result


async def _follow_one(
    session_factory: async_sessionmaker[AsyncSession], row: dict[str, Any], now: FolderNow
) -> Step | None:
    """Do the step ``decide`` gives for the followed reference delivery ``row`` whose folder (the
    one its family's record names, ``folder_path``) a round found as ``now``, in one transaction
    with its event. The row is locked first: one decided meanwhile (its state no longer as read)
    is left to the next round. A change raised keeps the folder it was found in (its path and
    name), which a refusal of that folder's new delivery is matched by. Returns the step, or None
    when the row was left."""
    folder = str(row["folder_path"])
    folder_name = str(row.get("followed_name") or row["folder_name"])
    async with session_factory() as session:
        locked = (
            await session.execute(
                text(
                    """
                    SELECT state, source_change, missing_rounds, file_listing
                    FROM import_deliveries
                    WHERE id = CAST(:id AS uuid)
                    FOR UPDATE
                    """
                ),
                {"id": row["id"]},
            )
        ).mappings().first()
        if locked is None or locked["state"] != row["state"]:
            return None
        change = _json_object(locked["source_change"])
        refused_id = await _refused_delivery(session, folder, now.fingerprint)
        difference = (
            listing_difference(_json_object(locked["file_listing"]), now.listing.entries)
            if now.listing is not None
            else None
        )
        step = decide(
            state=str(locked["state"]),
            imported_fingerprint=str(row["package_fingerprint"]),
            change=change,
            missing_rounds=int(locked["missing_rounds"] or 0),
            now=now,
            refused=refused_id is not None,
            difference=difference,
            detected_at=_now(),
        )
        if step.action in ("keep", "count"):
            if step.missing_rounds != int(locked["missing_rounds"] or 0):
                await session.execute(
                    text("UPDATE import_deliveries SET missing_rounds = :missing WHERE id = CAST(:id AS uuid)"),
                    {"missing": step.missing_rounds, "id": row["id"]},
                )
                await session.commit()
            return step
        base = {
            "folder": row["folder_name"],
            "package_fingerprint": row["package_fingerprint"],
            "family_id": row["family_id"],
        }
        if step.action == "raise" and step.change is not None:
            raised = {**step.change, "folder": folder_name, "folder_path": folder}
            await session.execute(
                text(
                    """
                    UPDATE import_deliveries
                    SET state = :changed, source_change = CAST(:change AS jsonb), missing_rounds = :missing,
                        state_changed_at = now()
                    WHERE id = CAST(:id AS uuid)
                    """
                ),
                {
                    "changed": CHANGED_STATE,
                    "change": json.dumps(raised),
                    "missing": step.missing_rounds,
                    "id": row["id"],
                },
            )
            await _record(
                session,
                action="import_delivery_changed_after_import",
                delivery_id=row["id"],
                after={
                    **base,
                    "state": CHANGED_STATE,
                    "change": raised,
                    "previous_state": locked["state"],
                    "previous_change": change,
                },
                summary=f"Delivery {row['folder_name']}: changed after its import, {_describe(raised)}",
            )
        else:
            await session.execute(
                text(
                    """
                    UPDATE import_deliveries
                    SET state = 'imported', source_change = NULL, missing_rounds = 0, state_changed_at = now()
                    WHERE id = CAST(:id AS uuid)
                    """
                ),
                {"id": row["id"]},
            )
            await _record(
                session,
                action="import_delivery_change_resolved",
                delivery_id=row["id"],
                after={
                    **base,
                    "state": "imported",
                    "resolution": step.resolution,
                    "folder_fingerprint": now.fingerprint,
                    "refused_delivery_id": refused_id if step.resolution == "refused" else None,
                    "previous_state": locked["state"],
                    "change": change,
                },
                summary=(
                    f"Delivery {row['folder_name']}: the change of its folder ended, "
                    + (
                        "the folder is as it was imported"
                        if step.resolution == "as_imported"
                        else "the folder's state was refused"
                    )
                ),
            )
        await session.commit()
    return step


async def resolve_refused(
    session: AsyncSession,
    *,
    refused: Mapping[str, Any],
    user: CurrentUser,
    reason: str,
) -> int:
    """End the change of each changed reference delivery whose change was found in the folder of
    the delivery ``refused`` (its ``id``, ``folder_path``, ``folder_name`` and
    ``package_fingerprint``) and is to that fingerprint: refusing the new delivery of a changed
    folder refuses the change (the owner's decision of 2026-11-11). Each goes back to
    ``imported``, recorded by ``user`` with the refused delivery and ``reason``, in the caller's
    transaction. Returns how many ended."""
    rows = (
        await session.execute(
            text(
                """
                UPDATE import_deliveries
                SET state = 'imported', source_change = NULL, missing_rounds = 0, state_changed_at = now()
                WHERE state = :changed AND source_change ->> 'folder_path' = :folder
                  AND source_change ->> 'kind' = 'changed' AND source_change ->> 'fingerprint' = :fingerprint
                RETURNING id::text, folder_name, package_fingerprint
                """
            ),
            {
                "folder": refused["folder_path"],
                "changed": CHANGED_STATE,
                "fingerprint": refused["package_fingerprint"],
            },
        )
    ).all()
    for delivery_id, name, fingerprint in rows:
        await _record(
            session,
            action="import_delivery_change_resolved",
            delivery_id=str(delivery_id),
            user=user,
            after={
                "folder": name,
                "package_fingerprint": fingerprint,
                "state": "imported",
                "resolution": "refused",
                "folder_fingerprint": refused["package_fingerprint"],
                "refused_delivery_id": refused["id"],
                "reason": reason,
            },
            summary=f"Delivery {name}: the change of its folder ended, its new delivery was refused",
        )
    return len(rows)


# --- A gone folder confirmed as deliberately gone ------------------------------------------------

DELIVERY_NOT_FOUND = "Delivery not found"
NOT_A_GONE_FOLDER = (
    "Only a delivery whose package folder is gone since its import (changed after import, gone) "
    "can be confirmed as archived."
)


async def archive_gone_delivery(
    session: AsyncSession, *, delivery_id: str, user: CurrentUser, reason: str
) -> ImportDeliveryOut:
    """Confirm the gone package folder of the delivery ``delivery_id`` as deliberately gone
    (archived), with ``reason``, as ``user`` (a superuser: the route's check). The delivery is
    ``archived``; its change keeps when, by whom and why (``archived``), and the event on the
    ``system:import-inbox`` chain records them. A sign-out is then no longer warned of the folder,
    but the family page says the source files are archived, and a signed record freezes it.
    404 for no such delivery; 409 unless it is ``changed_after_import`` with a change of kind
    ``gone``."""
    delivery_uuid = require_uuid(delivery_id, DELIVERY_NOT_FOUND, status_code=404)
    row = (
        await session.execute(
            text(
                """
                SELECT id::text AS id, folder_name, package_fingerprint, state, source_change, family_id
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
    change = _json_object(row["source_change"])
    if row["state"] != CHANGED_STATE or change is None or change.get("kind") != "gone":
        raise HTTPException(status_code=409, detail=NOT_A_GONE_FOLDER)
    archived = {"archived_at": _now(), "archived_by": audit_actor(user), "reason": reason}
    kept = {**change, "archived": archived}
    await session.execute(
        text(
            """
            UPDATE import_deliveries
            SET state = :archived, source_change = CAST(:change AS jsonb), state_changed_at = now()
            WHERE id = CAST(:id AS uuid)
            """
        ),
        {"archived": ARCHIVED_STATE, "change": json.dumps(kept), "id": row["id"]},
    )
    await _record(
        session,
        action="import_delivery_archived",
        delivery_id=row["id"],
        user=user,
        after={
            "folder": row["folder_name"],
            "package_fingerprint": row["package_fingerprint"],
            "family_id": row["family_id"],
            "state": ARCHIVED_STATE,
            "previous_state": row["state"],
            "change": change,
            "reason": reason,
        },
        summary=f"Delivery {row['folder_name']}: its gone package folder was confirmed as archived",
    )
    await session.commit()
    found = await get_import_delivery(session, row["id"])
    if found is None:  # pragma: no cover - the row was locked and updated in this request
        raise HTTPException(status_code=404, detail=DELIVERY_NOT_FOUND)
    return found


# --- What a family's page and its sign-out read --------------------------------------

# The change of the family's reference delivery, when it is changed after its import or archived.
_FAMILY_SOURCE_CHANGE = text(
    f"""
    SELECT jsonb_build_object(
               'delivery_id', d.id::text,
               'folder', d.folder_name,
               'imported_fingerprint', d.package_fingerprint
           ) || d.source_change
    FROM families f
    JOIN import_deliveries d ON {_REFERENCE_OF_A_FAMILY}
    WHERE f.id = CAST(:family_uuid AS uuid) AND d.state = ANY(CAST(:raised AS text[]))
      AND d.source_change IS NOT NULL
    ORDER BY d.state_changed_at DESC
    LIMIT 1
    """
)


def _paths(value: Any) -> list[str]:
    return sorted(str(item) for item in value) if isinstance(value, list) else []


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _archived_out(value: Any) -> dict[str, str] | None:
    if not isinstance(value, Mapping):
        return None
    return {
        "archived_at": str(value.get("archived_at") or ""),
        "archived_by": str(value.get("archived_by") or ""),
        "reason": str(value.get("reason") or ""),
    }


def source_change_out(raw: Any) -> dict[str, Any] | None:
    """A change as a family's page and its signed record read it, in one shape: the reference
    delivery and the name of the folder it was found in, the kind (``changed``, ``gone``), the
    fingerprint imported and the folder's now, whether its files are named, the paths added,
    removed and changed with their counts, when the watcher found it (or that a sign-out's own
    check did), and, for a gone folder confirmed as deliberately gone, when, by whom and why it
    was archived. None for no change. Each list sorted: the record hashes it."""
    change = _json_object(raw)
    if change is None:
        return None
    kind = "gone" if change.get("kind") == "gone" else "changed"
    return {
        "delivery_id": str(change.get("delivery_id") or ""),
        "folder": str(change.get("folder") or ""),
        "kind": kind,
        "imported_fingerprint": change.get("imported_fingerprint"),
        "fingerprint": change.get("fingerprint") if isinstance(change.get("fingerprint"), str) else None,
        "listed": change.get("listed") is True,
        "added": _paths(change.get("added")),
        "removed": _paths(change.get("removed")),
        "changed": _paths(change.get("changed")),
        "added_count": _count(change.get("added_count")),
        "removed_count": _count(change.get("removed_count")),
        "changed_count": _count(change.get("changed_count")),
        "detected_at": str(change["detected_at"]) if change.get("detected_at") else None,
        "found_at_sign_out": change.get("found_at_sign_out") is True,
        "archived": _archived_out(change.get("archived")) if kind == "gone" else None,
    }


def gates_a_sign_out(change: Mapping[str, Any] | None) -> bool:
    """Whether a change warns a sign-out: every change but a gone folder confirmed as archived."""
    return change is not None and change.get("archived") is None


async def family_package_source_change(session: AsyncSession, family_uuid: str) -> dict[str, Any] | None:
    """The change of the package folder the family's source files are served from, as the
    watcher last found it: the family's reference delivery, when it is ``changed_after_import``
    or ``archived`` (``source_change_out``); None when it is not, or the family was not imported
    from a delivery."""
    raw = (
        await session.execute(_FAMILY_SOURCE_CHANGE, {"family_uuid": family_uuid, "raised": list(CHANGE_STATES)})
    ).scalar_one_or_none()
    return source_change_out(raw)


def _checked_folder(folder: str) -> str | None:
    """The folder a sign-out lists: the family's package folder as CoGA serves files from it
    (``local_family_package_folder``: its real path, below an import root). In development with no
    import root configured, any local folder, as the watcher watches any (the owner's decision of
    2026-11-11): its real path, when the record holds an absolute path without NUL."""
    if settings.family_import_roots:
        return local_family_package_folder(folder)
    if "\x00" in folder or not os.path.isabs(folder):
        return None
    return os.path.realpath(folder)


def _folder_now_or_unreadable(folder: str) -> FolderNow:
    """The family's package folder as a sign-out lists it (``_checked_folder``) and as
    ``_folder_now`` finds it. A folder CoGA serves nothing from (outside every import root, not an
    absolute path) is one that cannot be read, and is not listed; so is any error reading it (a
    path the file system refuses)."""
    try:
        served = _checked_folder(folder)
        return _folder_now(served) if served is not None else FolderNow(gone=True)
    except Exception:  # noqa: BLE001 - never fail open: an error reads as a folder that cannot be read
        return FolderNow(gone=True)


async def _folder_at_sign_out(folder: str) -> FolderNow:
    """The folder as a sign-out finds it, listed off the event loop and waited for at most
    ``SIGN_OUT_FOLDER_CHECK_SECONDS``: a listing not done by then (a slow share; the listing
    thread runs on, and its result is dropped) is a folder that cannot be read."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_folder_now_or_unreadable, folder), timeout=SIGN_OUT_FOLDER_CHECK_SECONDS
        )
    except TimeoutError:
        return FolderNow(gone=True)


# The family's record and the listing kept with its reference delivery, for a sign-out's check.
_FAMILY_PACKAGE_RECORD = text(
    f"""
    SELECT f.metadata -> 'package_import' -> 'reference_delivery' AS reference,
           f.metadata -> 'package_import' ->> 'folder_path' AS folder_path,
           d.file_listing
    FROM families f
    LEFT JOIN import_deliveries d ON {_REFERENCE_OF_A_FAMILY}
    WHERE f.id = CAST(:family_uuid AS uuid)
    """
)


async def check_family_package_folder(session: AsyncSession, family_uuid: str) -> dict[str, Any] | None:
    """A sign-out's own check of the package folder the family's source files are served from
    (its record's ``folder_path``): its fingerprint now, stat-only, against the fingerprint the
    family's reference delivery was imported with (the owner's decision of 2026-11-11). None when
    the folder is as imported, or the family was not imported from a delivery; else the change,
    in ``source_change_out``'s shape, with ``found_at_sign_out``: what changed (read against the
    reference's listing), or, for a folder that cannot be listed, cannot be read in
    ``SIGN_OUT_FOLDER_CHECK_SECONDS``, whose path the record does not hold, or that CoGA serves no
    file from (outside every import root: it is not listed), that it is gone or cannot be read.
    Never fails open: an error reading the folder is a change, not a pass. A
    folder of more entries than a listing keeps is fingerprinted all the same; one too large to
    fingerprint has changed (the reference was fingerprinted)."""
    row = (await session.execute(_FAMILY_PACKAGE_RECORD, {"family_uuid": family_uuid})).mappings().first()
    reference = _json_object(row["reference"]) if row is not None else None
    if row is None or reference is None:
        return None
    imported = reference.get("package_fingerprint")
    folder = row["folder_path"]
    now = await _folder_at_sign_out(str(folder)) if isinstance(folder, str) and folder else FolderNow(gone=True)
    if not now.gone and now.fingerprint is not None and now.fingerprint == imported:
        return None
    if now.gone:
        change = gone_source("")
    else:
        listing = now.listing.entries if now.listing is not None else None
        change = changed_source(now.fingerprint, listing_difference(_json_object(row["file_listing"]), listing), "")
    return source_change_out(
        {
            **change,
            "delivery_id": reference.get("id"),
            "folder": Path(folder).name if isinstance(folder, str) and folder else "",
            "imported_fingerprint": imported if isinstance(imported, str) else None,
            "detected_at": None,
            "found_at_sign_out": True,
        }
    )


def sign_out_source_change(
    watcher: Mapping[str, Any] | None, own: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    """What a sign-out records of the package folder: the watcher's last finding (``watcher``,
    ``family_package_source_change``) and its own check (``own``, ``check_family_package_folder``),
    together. The own check's change, unless the watcher found the same already (the same kind
    and, for a changed folder, the same fingerprint), whose finding it keeps, or the watcher's
    folder was confirmed as archived and the check finds it still gone. With no own change, the
    watcher's finding, a change the watcher raised still warning: the next round ends it."""
    if own is None:
        return dict(watcher) if watcher is not None else None
    if watcher is not None:
        if watcher.get("archived") is not None:
            if own.get("kind") == "gone":
                return dict(watcher)
        elif watcher.get("kind") == own.get("kind") and (
            own.get("kind") == "gone" or watcher.get("fingerprint") == own.get("fingerprint")
        ):
            return dict(watcher)
    return dict(own)
