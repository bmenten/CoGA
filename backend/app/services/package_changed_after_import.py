"""A package folder that changes, or goes, after its import (the owner's decisions of 2026-11-11).

A family's record names the delivery its data was imported from
(``families.metadata.package_import.delivery``), and CoGA serves the family's read files (IGV),
signal tracks and QC report from that delivery's package folder, where it lies. So the import
watcher follows that folder after the import, each round (``follow_imported_deliveries``, from
``import_watcher.run_watch_round``), whatever the delivery's origin (a watched folder's, or one
added by hand), by its package fingerprint alone: each file's size and modification time, nothing
of its content, so a slow share costs one listing of the folder.

- **Changed.** The folder's fingerprint differs from the one the delivery was imported with: the
  delivery is ``changed_after_import``, and its ``source_change`` names the files added, removed
  and changed (in size or modification time), read against the listing kept with the delivery
  (``import_deliveries.file_listing``), each list to ``NAMED_PATHS`` paths with its count. A
  folder kept without a listing (more entries than ``PACKAGE_LISTING_MAX_ENTRIES``) is said to
  have changed without naming its files.
- **Gone.** The folder cannot be listed for ``GONE_AFTER_ROUNDS`` rounds while the folder it lies
  under (its watched folder; the import root of one added by hand) lists something: the same
  state, whose change says that the source files are no longer available. A folder it lies under
  that cannot be read, or that lists nothing (a share that is not mounted), decides nothing.

The family page shows the change (``family_package_source_change``) and a sign-out warns of it,
to acknowledge with a reason (``report_signout_service``, the gate ``package_source_change``).
The change ends only:

- by itself, when the folder is as it was imported again (the same fingerprint);
- when the folder's new delivery is imported (update or overwrite): the family's record then names
  that delivery, and the one it named before is not followed (``end_changes_no_family_names``);
- when the folder's new delivery is refused with a reason (``resolve_refused``): a folder state a
  refused delivery of the folder has is never raised again, and a state neither imported nor
  refused is.

A delivery no family's record names any more is not followed. Each transition is an event on the
clinical audit chain ``system:import-inbox``; the log names counts, never a folder or a family.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..core.coga_logging import describe_error
from .access_control import CurrentUser
from .clinical_audit_service import audit_actor, record_clinical_event
from .family_package_fingerprint import PackageFolderTooLarge, PackageListing, package_listing
from .import_watcher import GONE_AFTER_ROUNDS, IMPORT_INBOX_CHAIN, WATCHER_ACTOR, _names_in

logger = logging.getLogger(__name__)

# The state of a delivery whose folder changed, or went, after its import.
CHANGED_STATE = "changed_after_import"
# The states of a delivery imported: the watcher follows its folder while a family's record names it.
FOLLOWED_STATES = ("imported", CHANGED_STATE)
# The most paths of each kind (added, removed, changed) a change names; its counts say how many.
NAMED_PATHS = 50

ChangeKind = Literal["changed", "gone"]
Resolution = Literal["as_imported", "refused", "imported", "not_named"]

# The deliveries a family's record names, as the family's import wrote it.
_NAMED_BY_A_FAMILY = "f.metadata -> 'package_import' -> 'delivery' ->> 'id' = d.id::text"


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
    """What a round does with a followed delivery (``state``, imported with
    ``imported_fingerprint``, with its ``change`` when it is changed already and its count of
    ``missing_rounds``), its folder found as ``now``; ``refused``: a refused delivery of the folder
    has the folder's fingerprint now. ``difference`` is what changed since the listing kept with
    the delivery (``listing_difference``), read only when a change is raised."""
    raised = state == CHANGED_STATE
    if now.gone:
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
    if raised and change is not None and change.get("kind") == "changed" and change.get("fingerprint") == fingerprint:
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
    """End the change of each changed delivery no family's record names any more: back to
    ``imported``, recorded with the delivery of its folder a family's record names now (the new
    delivery imported: resolution ``imported``), or, when none does, ``not_named``. Returns how
    many ended."""
    async with session_factory() as session:
        rows = (
            await session.execute(
                text(
                    f"""
                    SELECT d.id::text AS id, d.folder_path, d.folder_name, d.package_fingerprint
                    FROM import_deliveries d
                    WHERE d.state = :changed
                      AND NOT EXISTS (SELECT 1 FROM families f WHERE {_NAMED_BY_A_FAMILY})
                    FOR UPDATE OF d SKIP LOCKED
                    """
                ),
                {"changed": CHANGED_STATE},
            )
        ).mappings().all()
        for row in rows:
            named = (
                await session.execute(
                    text(
                        f"""
                        SELECT d.id::text, f.family_id FROM import_deliveries d
                        JOIN families f ON {_NAMED_BY_A_FAMILY}
                        WHERE d.folder_path = :folder AND d.id <> CAST(:id AS uuid)
                        ORDER BY d.state_changed_at DESC
                        LIMIT 1
                        """
                    ),
                    {"folder": row["folder_path"], "id": row["id"]},
                )
            ).first()
            resolution: Resolution = "imported" if named is not None else "not_named"
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
                    + ("its new delivery was imported" if named is not None else "no family's record names it any more")
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
    """Follow the folder of each delivery a family's record names (see the module's docstring).
    ``skipped_roots`` are the watched folders the round skipped (not mounted): nothing is decided
    on a delivery of theirs. ``seen_fingerprints`` are the fingerprints the round took of the
    folders it looked at, by path: a folder seen as it was imported is not listed again."""
    result = FollowRound()
    ended = await end_changes_no_family_names(session_factory)
    if ended:
        result.resolved["imported_or_not_named"] = ended
    async with session_factory() as session:
        rows = (
            await session.execute(
                text(
                    f"""
                    SELECT d.id::text AS id, d.watch_root, d.folder_path, d.folder_name,
                           d.package_fingerprint, d.state, d.source_change, d.missing_rounds,
                           f.family_id
                    FROM import_deliveries d
                    JOIN families f ON {_NAMED_BY_A_FAMILY}
                    WHERE d.state = ANY(CAST(:followed AS text[]))
                    ORDER BY d.folder_path, d.id
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
    """Do the step ``decide`` gives for the followed delivery ``row`` whose folder a round found
    as ``now``, in one transaction with its event. The row is locked first: one decided meanwhile
    (its state or change no longer as read) is left to the next round. Returns the step, or None
    when the row was left."""
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
        refused_id = await _refused_delivery(session, row["folder_path"], now.fingerprint)
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
                    "change": json.dumps(step.change),
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
                    "change": step.change,
                    "previous_change": change,
                },
                summary=f"Delivery {row['folder_name']}: changed after its import, {_describe(step.change)}",
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
    """End the change of each changed delivery of the folder of the delivery ``refused`` (its
    ``id``, ``folder_path``, ``folder_name`` and ``package_fingerprint``) whose change is to that
    fingerprint: refusing the new delivery of a changed folder refuses the change (the owner's
    decision of 2026-11-11). Each goes back to ``imported``, recorded by ``user`` with the refused
    delivery and ``reason``, in the caller's transaction. Returns how many ended."""
    rows = (
        await session.execute(
            text(
                """
                UPDATE import_deliveries
                SET state = 'imported', source_change = NULL, missing_rounds = 0, state_changed_at = now()
                WHERE folder_path = :folder AND state = :changed
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


# --- What a family's page and its sign-out read --------------------------------------

# The change of the delivery the family's record names, when it is changed after its import.
_FAMILY_SOURCE_CHANGE = text(
    f"""
    SELECT jsonb_build_object(
               'delivery_id', d.id::text,
               'folder', d.folder_name,
               'imported_fingerprint', d.package_fingerprint
           ) || d.source_change
    FROM families f
    JOIN import_deliveries d ON {_NAMED_BY_A_FAMILY}
    WHERE f.id = CAST(:family_uuid AS uuid) AND d.state = :changed AND d.source_change IS NOT NULL
    """
)


def _paths(value: Any) -> list[str]:
    return sorted(str(item) for item in value) if isinstance(value, list) else []


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def source_change_out(raw: Any) -> dict[str, Any] | None:
    """A delivery's change as a family's page and its signed record read it, in one shape: the
    delivery and its folder's name, the kind (``changed``, ``gone``), the fingerprint imported and
    the folder's now, whether its files are named, the paths added, removed and changed with their
    counts, and when it was found. None for no change. Each list sorted: the record hashes it."""
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
    }


async def family_package_source_change(session: AsyncSession, family_uuid: str) -> dict[str, Any] | None:
    """The change after its import of the package folder the family's data came from: the
    delivery its record names, when that delivery is ``changed_after_import`` (``source_change_out``);
    None when it is not, or the family was not imported from a delivery."""
    raw = (
        await session.execute(_FAMILY_SOURCE_CHANGE, {"family_uuid": family_uuid, "changed": CHANGED_STATE})
    ).scalar_one_or_none()
    return source_change_out(raw)
