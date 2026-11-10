"""The import watcher: the packages pipelines deliver into the watched import folders.

A pipeline, or the step that copies its output onto the share, writes a package folder into a
watched import folder (``FAMILY_IMPORT_WATCH_ROOTS``) and, last, its ready marker
``coga-ready.json`` (``import_ready_marker``). Every ``FAMILY_IMPORT_WATCH_INTERVAL_SECONDS``
the watcher looks into each watched folder. A folder without a marker is no delivery yet. A
folder with one is a delivery per package fingerprint (``family_package_fingerprint``): a folder
that changes is a new delivery, and an open one before it of the same folder is superseded. A
new delivery is checked once, and its state stored (``import_deliveries``):

- ``incomplete``: a file the marker lists is missing or of another size: the copy runs still
  (its folder's fingerprint changes once it has finished, which is a new delivery);
- ``needs_attention``: the marker, a checksum, the project, the assembly, the analysis type, the
  manifest, the package's validation, the family or its samples stand in the way, each an issue;
- ``ready``: it can be approved for import (an approval queue the import page will offer).

Some of those issues come from what CoGA holds, not from the folder: a project the marker names
that CoGA lacks, or of another assembly, and a family or samples that already exist. An open
delivery (``ready``, ``needs_attention``) seen again is checked again, as a whole, when that has
changed since its check (a project added, a family imported or deleted): its row then holds what
the new check found, and the chain the change.

A delivery still open whose folder, or marker, has been missing for ``GONE_AFTER_ROUNDS`` rounds
while its watched folder could be read is ``gone``. A watched folder that cannot be read, or that
lists nothing but hidden entries while it had open deliveries (a share not mounted reads as an
empty folder), is skipped for the round: nothing is decided on it. One that lists anything else,
even folders without a marker, is mounted and watched. Each change of a delivery's state is an event on the
clinical audit chain ``system:import-inbox``, by ``system:import-watcher``. Logs name counts,
never a folder or a family.

One watcher looks at a time, wherever import workers run: it holds a Postgres advisory lock on a
connection of its own (``WatcherLeadership``), and another process's watcher skips its rounds
until that connection is gone.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from ..core.coga_logging import describe_error
from ..core.config import settings
from ..core.object_storage import is_remote_uri
from ..core.postgres import get_postgres_engine, get_postgres_sessionmaker
from ..schemas import (
    ApiId,
    FamilyPackageManifestBuildRequest,
    FamilyPackageValidationOut,
    ImportDeliveryIssue,
    ImportDeliveryOut,
)
from .clinical_audit_service import record_clinical_event
from .family_identifiers import identifier_problem
from .family_package_common import _json_list, manifest_text_sha256
from .family_package_discovery import discover_family_package_manifest
from .family_package_fingerprint import PackageFolderTooLarge, package_fingerprint
from .family_package_registration import _existing_package_entity_warnings, manifest_roi_issue
from .family_package_source import _authorized_local_roots, _find_manifest, _listed_package_folder
from .family_package_validation import load_validated_family_package
from .import_ready_marker import (
    MARKER_FILE_NAME,
    MarkerIssue,
    MarkerRead,
    ReadyMarker,
    check_marker_files,
    read_ready_marker,
)
from .worker_tasks import stop_worker

logger = logging.getLogger(__name__)

# The clinical audit chain of the deliveries, and who writes it.
IMPORT_INBOX_CHAIN = "system:import-inbox"
WATCHER_ACTOR = "system:import-watcher"
# The advisory lock one watcher holds while it looks.
_LEADER_LOCK = "coga:import-watcher"
# A delivery is open while nobody has decided on it.
OPEN_STATES = ("incomplete", "ready", "needs_attention")
# The open deliveries checked again when what CoGA holds changes (an incomplete one's folder
# changes once its copy has finished, which is a new delivery).
_CHECKED_AGAIN_STATES = ("ready", "needs_attention")
# The issues a check takes from what CoGA holds, not from the folder: the project the marker
# names, and the family and samples the package holds.
_PROJECT_ISSUES = frozenset({"marker_project_unknown", "marker_assembly_differs"})
_EXISTING_ISSUES = frozenset({"existing_family", "existing_samples"})
GONE_AFTER_ROUNDS = 2
# How many unlisted files a warning names.
_UNLISTED_NAMED = 5

_PED_SEX = {"1": "male", "2": "female"}


def watch_roots() -> list[Path]:
    """The watched import folders: each local folder of FAMILY_IMPORT_WATCH_ROOTS that lies under
    FAMILY_IMPORT_ROOTS (any folder, in development with no roots configured), resolved. One that
    is a bucket, or outside the import roots, is left out, and the log says how many."""
    roots: list[Path] = []
    import_roots = _authorized_local_roots()
    left_out = 0
    for written in settings.family_import_watch_roots:
        if is_remote_uri(written):
            left_out += 1
            continue
        path = Path(written).expanduser().resolve()
        if settings.family_import_roots and not any(path == root or root in path.parents for root in import_roots):
            left_out += 1
            continue
        if path not in roots:
            roots.append(path)
    if left_out:
        logger.warning(
            "%d of FAMILY_IMPORT_WATCH_ROOTS is not watched: a bucket, or a folder outside "
            "FAMILY_IMPORT_ROOTS",
            left_out,
        )
    return roots


@dataclass(slots=True)
class WatchedFolderListing:
    """A watched folder as a round lists it: ``folders``, the package folders in it that hold a
    ready marker; ``empty``, whether it lists nothing but hidden entries (a share that is not
    mounted reads as an empty folder; one that lists any other entry is mounted)."""

    folders: list[Path]
    empty: bool


def marked_folders(root: Path) -> WatchedFolderListing | None:
    """The package folders directly in the watched folder ``root`` that hold a ready marker, as
    selection takes them (``_listed_package_folder``: links resolved, none out of the import
    roots), and whether ``root`` lists nothing but hidden entries; None when ``root`` cannot be
    read."""
    try:
        if not root.is_dir():
            return None
        with os.scandir(root) as listing:
            names = sorted(entry.name for entry in listing if not entry.name.startswith("."))
    except OSError:
        return None
    folders: list[Path] = []
    for name in names:
        folder = _listed_package_folder(root / name)
        if folder is None or not folder.is_dir():
            continue
        try:
            os.lstat(folder / MARKER_FILE_NAME)
        except OSError:
            continue
        if folder not in folders:
            folders.append(folder)
    return WatchedFolderListing(folders=folders, empty=not names)


@dataclass(slots=True)
class DeliveryAssessment:
    """A new delivery as checked: its state, issues and warnings, and what the row keeps."""

    state: str
    issues: list[MarkerIssue] = field(default_factory=list)
    warnings: list[MarkerIssue] = field(default_factory=list)
    marker_sha256: str | None = None
    marker: dict[str, Any] | None = None
    run_id: str | None = None
    family_id: str | None = None
    project_id: str | None = None
    validation: dict[str, Any] | None = None
    manifest_text: str | None = None


def _issue(code: str, message: str, path: str | None = None) -> MarkerIssue:
    return MarkerIssue(code=code, message=message, path=path)


def _as_issue(issue: Any) -> MarkerIssue:
    """A validation or Discover issue as a delivery keeps it: its code and message."""
    return MarkerIssue(code=str(issue.code), message=str(issue.message), path=None)


async def _project(session: AsyncSession, name: str) -> tuple[str, str | None] | None:
    row = (
        await session.execute(
            text(
                """
                SELECT p.id::text AS id, a.assembly_name
                FROM projects p
                LEFT JOIN assemblies a ON a.id = p.assembly_id
                WHERE p.name = :name
                """
            ),
            {"name": name},
        )
    ).mappings().first()
    return (str(row["id"]), row["assembly_name"]) if row is not None else None


async def _project_issues(
    session: AsyncSession, name: str | None, assembly: str | None
) -> tuple[str | None, list[MarkerIssue]]:
    """The project the marker names (``name``, with its ``assembly``) as CoGA holds it now: the
    project's id, None when CoGA has no such project, and the issues: a project CoGA lacks, or one
    of another assembly."""
    if not name:
        return None, []
    project = await _project(session, name)
    if project is None:
        return None, [
            _issue(
                "marker_project_unknown",
                f"coga-ready.json names the project '{name}', which CoGA does not have. Once it does, "
                "CoGA checks the delivery again.",
            )
        ]
    if assembly and project[1] and assembly != project[1]:
        return project[0], [
            _issue("marker_assembly_differs", f"coga-ready.json gives the assembly {assembly}; the project's is {project[1]}.")
        ]
    return project[0], []


def _relative(root: Path, path: str | None) -> str | None:
    if not path:
        return None
    try:
        return Path(path).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def _files_the_import_reads(folder: Path, validation: FamilyPackageValidationOut) -> list[str]:
    """The files the import reads, as paths in the package: the PED and each file an enabled
    dataset names (a folder it names, every file in it)."""
    paths: set[str] = set()
    ped = _relative(folder, validation.ped_path)
    if ped:
        paths.add(ped)
    for dataset in validation.datasets:
        if not dataset.enabled:
            continue
        for written in dataset.files:
            target = folder / written
            if target.is_dir():
                paths.update(
                    path.relative_to(folder).as_posix() for path in target.rglob("*") if path.is_file()
                )
            else:
                paths.add(Path(written).as_posix())
    return sorted(paths)


def _sample_issues(marker: ReadyMarker, sexes: dict[str, str]) -> tuple[list[MarkerIssue], list[MarkerIssue]]:
    """The marker's samples against the package's members (``sexes``: each member's PED sex):
    each must be a member, of the sex the PED gives; a member it does not name is a warning (a
    fetus has no data of its own)."""
    issues: list[MarkerIssue] = []
    warnings: list[MarkerIssue] = []
    named = {sample.sample_id for sample in marker.samples or []}
    for sample in marker.samples or []:
        if sample.sample_id not in sexes:
            issues.append(
                _issue("marker_sample_not_in_package", f"coga-ready.json names sample '{sample.sample_id}', which the package does not hold.")
            )
        elif sample.sex in ("male", "female") and sexes[sample.sample_id] in ("male", "female") and sample.sex != sexes[sample.sample_id]:
            issues.append(
                _issue(
                    "marker_sample_sex_differs",
                    f"coga-ready.json gives sample '{sample.sample_id}' another sex than the PED.",
                )
            )
    missing = sorted(set(sexes) - named)
    if marker.samples is not None and missing:
        warnings.append(
            _issue(
                "marker_sample_not_named",
                "coga-ready.json does not name the package's member(s) " + ", ".join(missing) + ".",
            )
        )
    return issues, warnings


async def assess_delivery(session_factory: async_sessionmaker[AsyncSession], folder: Path) -> DeliveryAssessment:
    """Check the new delivery in the package folder ``folder`` (see the module's docstring)."""
    read: MarkerRead = await asyncio.to_thread(read_ready_marker, folder)
    assessment = DeliveryAssessment(state="needs_attention", marker_sha256=read.sha256, marker=read.payload)
    if read.marker is None:
        assessment.issues = list(read.issues)
        return assessment
    marker = read.marker
    assessment.run_id = marker.run.id if marker.run else None
    assessment.family_id = marker.family_id
    sizes = await asyncio.to_thread(check_marker_files, folder, marker.files or [], checksums=False)
    if sizes.incomplete:
        assessment.state = "incomplete"
        assessment.issues = [issue for issue in sizes.issues if issue.code in ("file_missing", "file_size_differs")]
        return assessment
    issues: list[MarkerIssue] = list(read.issues)
    warnings: list[MarkerIssue] = []
    checked = await asyncio.to_thread(check_marker_files, folder, marker.files or [], checksums=True)
    issues.extend(checked.issues)
    if checked.unlisted:
        named = ", ".join(checked.unlisted[:_UNLISTED_NAMED])
        more = len(checked.unlisted) - _UNLISTED_NAMED
        warnings.append(
            _issue(
                "file_not_in_marker",
                f"The folder holds {len(checked.unlisted)} file(s) coga-ready.json does not list: {named}"
                + (f", and {more} more." if more > 0 else "."),
            )
        )
    if marker.family_id and identifier_problem(marker.family_id) is not None:
        issues.append(_issue("marker_family_id_invalid", "coga-ready.json names a family ID no family can be stored under."))
    if marker.qc is not None and marker.qc.status == "fail":
        issues.append(_issue("marker_qc_failed", "The pipeline's QC failed (coga-ready.json, qc.status)."))
    elif marker.qc is not None and marker.qc.status == "warn":
        warnings.append(_issue("marker_qc_warned", "The pipeline's QC warned (coga-ready.json, qc.status)."))

    async with session_factory() as session:
        project_id, project_issues = await _project_issues(session, marker.project, marker.assembly)
    assessment.project_id = project_id
    issues.extend(project_issues)

    # The manifest: the folder's own, or the one Discover drafts, which the delivery keeps.
    if await asyncio.to_thread(_find_manifest, folder) is None:
        draft = await asyncio.to_thread(
            discover_family_package_manifest, FamilyPackageManifestBuildRequest(folder_path=str(folder))
        )
        if draft.errors or not draft.manifest_yaml:
            issues.append(_issue("discover_failed", "Discover could not draft a manifest for the folder."))
            issues.extend(_as_issue(issue) for issue in draft.errors)
            assessment.issues, assessment.warnings = issues, warnings
            return assessment
        assessment.manifest_text = draft.manifest_yaml
        warnings.extend(_as_issue(issue) for issue in draft.warnings if issue.code != "dataset_not_detected")

    validation, bundle = await asyncio.to_thread(load_validated_family_package, folder, manifest_text=assessment.manifest_text)
    assessment.validation = {
        "valid": validation.valid,
        "family_id": validation.family_id,
        "sample_ids": validation.sample_ids,
        "datasets": [
            {"dataset_type": dataset.dataset_type, "status": dataset.status}
            for dataset in validation.datasets
            if dataset.enabled
        ],
        "package_fingerprint": validation.package_fingerprint,
    }
    issues.extend(_as_issue(issue) for issue in validation.errors)
    warnings.extend(_as_issue(issue) for issue in validation.warnings)
    if validation.family_id and marker.family_id and validation.family_id != marker.family_id:
        issues.append(
            _issue("marker_family_differs", f"coga-ready.json names the family '{marker.family_id}'; the package is '{validation.family_id}'.")
        )
    if bundle is not None:
        not_listed = [path for path in _files_the_import_reads(folder, validation) if path not in checked.sha256]
        issues.extend(
            _issue("manifest_file_not_in_marker", "The import reads a file coga-ready.json does not list, or whose checksum differs.", path)
            for path in not_listed
            if not any(issue.path == path for issue in checked.issues)
        )
        sample_issues, sample_warnings = _sample_issues(
            marker, {member.iid: _PED_SEX.get(member.sex, "unknown") for member in bundle.ped.members}
        )
        issues.extend(sample_issues)
        warnings.extend(sample_warnings)
    async with session_factory() as session:
        # A family or samples that already exist: an approver chooses update or overwrite.
        issues.extend(
            _as_issue(issue)
            for issue in await _existing_package_entity_warnings(
                session, family_id=validation.family_id, sample_ids=validation.sample_ids
            )
        )
        if project_id is not None:
            roi = await manifest_roi_issue(
                session, roi_query=validation.metadata.get("roi"), project_id=project_id, family_id=validation.family_id
            )
            if roi is not None:
                warnings.append(_as_issue(roi))
    assessment.issues, assessment.warnings = issues, warnings
    assessment.state = "needs_attention" if issues else "ready"
    return assessment


async def _record(session: AsyncSession, *, action: str, delivery_id: str, after: dict[str, Any], summary: str) -> None:
    await record_clinical_event(
        session,
        family_uuid=None,
        family_identifier=IMPORT_INBOX_CHAIN,
        variant_id=None,
        actor=WATCHER_ACTOR,
        actor_id=None,
        action=action,
        summary=summary,
        after=after,
        metadata={"delivery_id": delivery_id},
    )


def _issues_json(issues: list[MarkerIssue]) -> str:
    return json.dumps([issue.model_dump() for issue in issues])


async def _fingerprint(folder: Path) -> str | None:
    try:
        return (await asyncio.to_thread(package_fingerprint, folder, kept_manifest_sha256=None)).sha256
    except (OSError, PackageFolderTooLarge):
        return None


def _json_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


async def _coga_side_changed(session: AsyncSession, delivery: Mapping[Any, Any]) -> bool:
    """Whether what CoGA holds has changed, since the open delivery ``delivery`` was checked, in what
    its check read there: the project its marker names (added, deleted, or of another assembly),
    and the family and samples its package holds (imported, or deleted). It compares only what the
    check read: the project when the marker could be read (the check named it unknown, kept its id,
    or went on to validate the package), the family and samples when it validated the package. So
    a check that could not run, or a marker that could not be read, is never checked again: what
    it found holds until the folder changes."""
    codes = {str(item.get("code")) for item in _json_list(delivery["issues"]) if isinstance(item, dict)}
    marker = _json_object(delivery["marker"])
    validation = _json_object(delivery["validation"])
    project_read = "marker_project_unknown" in codes or delivery["project_id"] is not None or validation is not None
    if marker is not None and project_read:
        project_id, project_issues = await _project_issues(session, _text(marker.get("project")), _text(marker.get("assembly")))
        if project_id != delivery["project_id"] or {issue.code for issue in project_issues} != codes & _PROJECT_ISSUES:
            return True
    if validation is not None:
        existing = await _existing_package_entity_warnings(
            session,
            family_id=_text(validation.get("family_id")),
            sample_ids=[str(sample) for sample in validation.get("sample_ids") or []],
        )
        if {str(issue.code) for issue in existing} != codes & _EXISTING_ISSUES:
            return True
    return False


async def _assess(session_factory: async_sessionmaker[AsyncSession], folder: Path) -> DeliveryAssessment:
    """``assess_delivery``, or, when the check fails, the delivery as one that needs attention for it."""
    try:
        return await assess_delivery(session_factory, folder)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - one folder's failure never stops the round
        logger.warning("Import watcher could not check a delivery: %s", describe_error(exc))
        return DeliveryAssessment(
            state="needs_attention",
            issues=[_issue("watch_check_failed", "CoGA could not check the delivery; it tries again when the folder changes.")],
        )


async def look_at_folder(
    session_factory: async_sessionmaker[AsyncSession], root: Path, folder: Path
) -> tuple[str, str] | None:
    """Look at one marked package folder: a new delivery is checked and stored, and an open one
    before it of the folder superseded; a delivery seen before is seen again, and checked again
    when it is open (``ready`` or ``needs_attention``) and what CoGA holds has changed in what its
    check read (``_coga_side_changed``). Returns ``("new", state)`` or ``("checked_again",
    state)``, or None when the delivery was only seen again or the folder changed while it was
    checked (the next round takes it)."""
    fingerprint = await _fingerprint(folder)
    if fingerprint is None:
        return None
    async with session_factory() as session:
        seen = (
            await session.execute(
                text(
                    """
                    UPDATE import_deliveries
                    SET last_seen_at = now(), missing_rounds = 0
                    WHERE folder_path = :folder AND package_fingerprint = :fingerprint
                    RETURNING id::text AS id, state, issues, project_id::text AS project_id, marker, validation
                    """
                ),
                {"folder": str(folder), "fingerprint": fingerprint},
            )
        ).mappings().first()
        if seen is not None:
            check_again = seen["state"] in _CHECKED_AGAIN_STATES and await _coga_side_changed(session, seen)
            await session.commit()
            if not check_again:
                return None
    if seen is not None:
        assessment = await _assess(session_factory, folder)
        if await _fingerprint(folder) != fingerprint:
            return None
        state = await _store_check_again(session_factory, folder, str(seen["id"]), fingerprint, assessment)
        return ("checked_again", state) if state is not None else None
    assessment = await _assess(session_factory, folder)
    # Changed while it was checked: what was checked is not what is there.
    if await _fingerprint(folder) != fingerprint:
        return None
    stored = await _store_new_delivery(session_factory, root, folder, fingerprint, assessment)
    return ("new", stored) if stored is not None else None


async def _store_new_delivery(
    session_factory: async_sessionmaker[AsyncSession],
    root: Path,
    folder: Path,
    fingerprint: str,
    assessment: DeliveryAssessment,
) -> str | None:
    """``_store_delivery``; when that fails, the delivery without what its check found, so it is
    not checked again each round."""
    try:
        return await _store_delivery(session_factory, root, folder, fingerprint, assessment)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - kept without what it could not store, never retried each round
        logger.warning("Import watcher could not store a delivery: %s", describe_error(exc))
    unstored = DeliveryAssessment(
        state="needs_attention",
        issues=[_issue("watch_store_failed", "CoGA could not keep what the delivery's check found; it checks again when the folder changes.")],
    )
    try:
        return await _store_delivery(session_factory, root, folder, fingerprint, unstored)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - one folder's failure never stops the round
        logger.warning("Import watcher could not store a delivery: %s", describe_error(exc))
        return None


async def _store_delivery(
    session_factory: async_sessionmaker[AsyncSession],
    root: Path,
    folder: Path,
    fingerprint: str,
    assessment: DeliveryAssessment,
) -> str | None:
    """Store a new delivery as checked, record it on the chain, and supersede the open ones of
    the folder before it, in one transaction. Returns its state, or None when it was stored
    already."""
    async with session_factory() as session:
        inserted = (
            await session.execute(
                text(
                    """
                    INSERT INTO import_deliveries (
                        watch_root, folder_path, folder_name, package_fingerprint, marker_sha256,
                        marker, run_id, family_id, project_id, state, issues, warnings, validation,
                        manifest_text, manifest_sha256
                    )
                    VALUES (
                        :root, :folder, :name, :fingerprint, :marker_sha256, CAST(:marker AS jsonb),
                        :run_id, :family_id, CAST(:project_id AS uuid), :state, CAST(:issues AS jsonb),
                        CAST(:warnings AS jsonb), CAST(:validation AS jsonb), :manifest_text, :manifest_sha256
                    )
                    ON CONFLICT (folder_path, package_fingerprint) DO NOTHING
                    RETURNING id::text
                    """
                ),
                {
                    "root": str(root),
                    "folder": str(folder),
                    "name": folder.name,
                    "fingerprint": fingerprint,
                    "marker_sha256": assessment.marker_sha256,
                    "marker": json.dumps(assessment.marker) if assessment.marker is not None else None,
                    "run_id": assessment.run_id,
                    "family_id": assessment.family_id,
                    "project_id": assessment.project_id,
                    "state": assessment.state,
                    "issues": _issues_json(assessment.issues),
                    "warnings": _issues_json(assessment.warnings),
                    "validation": json.dumps(assessment.validation) if assessment.validation is not None else None,
                    "manifest_text": assessment.manifest_text,
                    "manifest_sha256": (
                        manifest_text_sha256(assessment.manifest_text) if assessment.manifest_text is not None else None
                    ),
                },
            )
        ).first()
        if inserted is None:
            return None
        delivery_id = str(inserted[0])
        summary = {
            "folder": folder.name,
            "watch_root": str(root),
            "package_fingerprint": fingerprint,
            "marker_sha256": assessment.marker_sha256,
            "run_id": assessment.run_id,
            "family_id": assessment.family_id,
        }
        await _record(
            session,
            action=f"import_delivery_{assessment.state}",
            delivery_id=delivery_id,
            after={**summary, "state": assessment.state, "issues": [issue.code for issue in assessment.issues]},
            summary=f"Delivery {folder.name}: {assessment.state.replace('_', ' ')}",
        )
        superseded = await session.execute(
            text(
                """
                UPDATE import_deliveries
                SET state = 'superseded', state_changed_at = now()
                WHERE folder_path = :folder AND id <> CAST(:id AS uuid) AND state = ANY(CAST(:open AS text[]))
                RETURNING id::text, package_fingerprint
                """
            ),
            {"folder": str(folder), "id": delivery_id, "open": list(OPEN_STATES)},
        )
        for old_id, old_fingerprint in superseded.all():
            await _record(
                session,
                action="import_delivery_superseded",
                delivery_id=str(old_id),
                after={"folder": folder.name, "package_fingerprint": old_fingerprint, "superseded_by": delivery_id},
                summary=f"Delivery {folder.name}: superseded, the folder changed",
            )
        await session.commit()
    return assessment.state


async def _store_check_again(
    session_factory: async_sessionmaker[AsyncSession],
    folder: Path,
    delivery_id: str,
    fingerprint: str,
    assessment: DeliveryAssessment,
) -> str | None:
    """``_update_checked_again``; when that fails, the delivery without what its check found, so it
    is not checked again each round."""
    try:
        return await _update_checked_again(session_factory, folder, delivery_id, fingerprint, assessment)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - kept without what it could not store, never retried each round
        logger.warning("Import watcher could not store a delivery: %s", describe_error(exc))
    unstored = DeliveryAssessment(
        state="needs_attention",
        issues=[_issue("watch_store_failed", "CoGA could not keep what the delivery's check found; it checks again when the folder changes.")],
    )
    try:
        return await _update_checked_again(session_factory, folder, delivery_id, fingerprint, unstored)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - one folder's failure never stops the round
        logger.warning("Import watcher could not store a delivery: %s", describe_error(exc))
        return None


async def _update_checked_again(
    session_factory: async_sessionmaker[AsyncSession],
    folder: Path,
    delivery_id: str,
    fingerprint: str,
    assessment: DeliveryAssessment,
) -> str | None:
    """Store what checking the delivery ``delivery_id`` again found, in its row, and record it on
    the chain with what it replaced, in one transaction. The row is locked first: a delivery
    decided meanwhile (approved, refused) is left as it is. What the marker said is kept when the
    check could not read it (the folder is the same). Returns its state, or None when it was left."""
    async with session_factory() as session:
        before = (
            await session.execute(
                text(
                    """
                    SELECT state, issues, marker_sha256, run_id, family_id
                    FROM import_deliveries
                    WHERE id = CAST(:id AS uuid) AND package_fingerprint = :fingerprint
                    FOR UPDATE
                    """
                ),
                {"id": delivery_id, "fingerprint": fingerprint},
            )
        ).mappings().first()
        if before is None or before["state"] not in _CHECKED_AGAIN_STATES:
            return None
        await session.execute(
            text(
                """
                UPDATE import_deliveries
                SET marker_sha256 = COALESCE(:marker_sha256, marker_sha256),
                    marker = COALESCE(CAST(:marker AS jsonb), marker),
                    run_id = COALESCE(:run_id, run_id),
                    family_id = COALESCE(:family_id, family_id),
                    project_id = CAST(:project_id AS uuid),
                    state = :state,
                    issues = CAST(:issues AS jsonb),
                    warnings = CAST(:warnings AS jsonb),
                    validation = CAST(:validation AS jsonb),
                    manifest_text = :manifest_text,
                    manifest_sha256 = :manifest_sha256,
                    state_changed_at = CASE WHEN state = :state THEN state_changed_at ELSE now() END
                WHERE id = CAST(:id AS uuid)
                """
            ),
            {
                "id": delivery_id,
                "marker_sha256": assessment.marker_sha256,
                "marker": json.dumps(assessment.marker) if assessment.marker is not None else None,
                "run_id": assessment.run_id,
                "family_id": assessment.family_id,
                "project_id": assessment.project_id,
                "state": assessment.state,
                "issues": _issues_json(assessment.issues),
                "warnings": _issues_json(assessment.warnings),
                "validation": json.dumps(assessment.validation) if assessment.validation is not None else None,
                "manifest_text": assessment.manifest_text,
                "manifest_sha256": (
                    manifest_text_sha256(assessment.manifest_text) if assessment.manifest_text is not None else None
                ),
            },
        )
        previous = [str(item.get("code")) for item in _json_list(before["issues"]) if isinstance(item, dict)]
        await _record(
            session,
            action="import_delivery_checked_again",
            delivery_id=delivery_id,
            after={
                "folder": folder.name,
                "package_fingerprint": fingerprint,
                "marker_sha256": assessment.marker_sha256 or before["marker_sha256"],
                "run_id": assessment.run_id or before["run_id"],
                "family_id": assessment.family_id or before["family_id"],
                "state": assessment.state,
                "issues": [issue.code for issue in assessment.issues],
                "previous_state": before["state"],
                "previous_issues": previous,
            },
            summary=f"Delivery {folder.name}: checked again, what CoGA holds changed; {assessment.state.replace('_', ' ')}",
        )
        await session.commit()
    return assessment.state


async def mark_missing(session_factory: async_sessionmaker[AsyncSession], root: Path, seen: set[str]) -> int:
    """Count a round against each open delivery of the watched folder ``root`` whose folder, or
    marker, was not seen; one missing for ``GONE_AFTER_ROUNDS`` rounds is gone. Returns how many
    went."""
    gone = 0
    async with session_factory() as session:
        rows = (
            await session.execute(
                text(
                    """
                    UPDATE import_deliveries
                    SET missing_rounds = missing_rounds + 1
                    WHERE watch_root = :root AND state = ANY(CAST(:open AS text[]))
                      AND NOT (folder_path = ANY(CAST(:seen AS text[])))
                    RETURNING id::text, folder_name, package_fingerprint, missing_rounds
                    """
                ),
                {"root": str(root), "open": list(OPEN_STATES), "seen": sorted(seen)},
            )
        ).all()
        for delivery_id, name, fingerprint, missing in rows:
            if missing < GONE_AFTER_ROUNDS:
                continue
            await session.execute(
                text("UPDATE import_deliveries SET state = 'gone', state_changed_at = now() WHERE id = CAST(:id AS uuid)"),
                {"id": delivery_id},
            )
            await _record(
                session,
                action="import_delivery_gone",
                delivery_id=str(delivery_id),
                after={"folder": name, "package_fingerprint": fingerprint},
                summary=f"Delivery {name}: gone, its folder or ready marker is no longer there",
            )
            gone += 1
        await session.commit()
    return gone


@dataclass(slots=True)
class WatchRound:
    """What a round did, in counts."""

    roots_watched: int = 0
    roots_skipped: int = 0
    deliveries: dict[str, int] = field(default_factory=dict)
    checked_again: dict[str, int] = field(default_factory=dict)
    gone: int = 0
    jobs_ended: int = 0


async def _open_deliveries(session_factory: async_sessionmaker[AsyncSession], root: Path) -> int:
    async with session_factory() as session:
        return int(
            (
                await session.execute(
                    text(
                        "SELECT count(*) FROM import_deliveries WHERE watch_root = :root AND state = ANY(CAST(:open AS text[]))"
                    ),
                    {"root": str(root), "open": list(OPEN_STATES)},
                )
            ).scalar_one()
        )


async def run_watch_round(session_factory: async_sessionmaker[AsyncSession], roots: list[Path]) -> WatchRound:
    """One round over the watched folders ``roots`` (see the module's docstring). It first records
    the end of each queued delivery's import job (``import_delivery_review.sync_delivery_jobs``)."""
    # Imported here: the review module reads this one's chain and list.
    from .import_delivery_review import sync_delivery_jobs

    result = WatchRound()
    result.jobs_ended = await sync_delivery_jobs(session_factory)
    for root in roots:
        listing = await asyncio.to_thread(marked_folders, root)
        # Nothing is decided on a folder that cannot be read, or that lists nothing while it had
        # open deliveries: a share that is not mounted. A folder that lists anything else is
        # mounted, so a delivery whose folder or marker is not in it is missing.
        if listing is None or (listing.empty and await _open_deliveries(session_factory, root)):
            result.roots_skipped += 1
            continue
        folders = listing.folders
        result.roots_watched += 1
        for folder in folders:
            looked = await look_at_folder(session_factory, root, folder)
            if looked is not None:
                kind, state = looked
                counts = result.deliveries if kind == "new" else result.checked_again
                counts[state] = counts.get(state, 0) + 1
        result.gone += await mark_missing(session_factory, root, {str(folder) for folder in folders})
    return result


class WatcherLeadership:
    """The advisory lock that lets one watcher look at a time, held on a connection of its own for
    as long as that connection lives."""

    def __init__(self) -> None:
        self._connection: AsyncConnection | None = None

    async def hold(self) -> bool:
        """Whether this watcher holds the lock now: it is checked when held, taken when free."""
        if self._connection is not None:
            try:
                await self._connection.execute(text("SELECT 1"))
                await self._connection.commit()
                return True
            except Exception:  # noqa: BLE001 - a lost connection loses the lock with it
                connection, self._connection = self._connection, None
                await connection.invalidate()
                await connection.close()
        connection = await get_postgres_engine().connect()
        try:
            taken = (
                await connection.execute(text("SELECT pg_try_advisory_lock(hashtext(:key))"), {"key": _LEADER_LOCK})
            ).scalar_one()
            await connection.commit()
        except Exception:
            await connection.close()
            raise
        if not taken:
            await connection.close()
            return False
        self._connection = connection
        return True

    async def release(self) -> None:
        """Give the lock up. The connection goes back to the pool, which keeps it open, so the
        lock is released on it first; a connection that cannot release it is discarded, which
        ends its session and the lock with it."""
        connection, self._connection = self._connection, None
        if connection is None:
            return
        try:
            await connection.execute(text("SELECT pg_advisory_unlock(hashtext(:key))"), {"key": _LEADER_LOCK})
            await connection.commit()
        except Exception:  # noqa: BLE001 - a lost connection: discarded below
            await connection.invalidate()
        try:
            await connection.close()
        except Exception:  # noqa: BLE001 - closing a lost connection
            pass


async def import_watcher(stop: asyncio.Event) -> None:
    """Look into the watched import folders every FAMILY_IMPORT_WATCH_INTERVAL_SECONDS until
    ``stop`` is set, while this watcher holds the leadership. Returns at once when no folder is
    watched. A round that fails is logged, without its values, and the next one runs."""
    roots = watch_roots()
    if not roots:
        return
    leadership = WatcherLeadership()
    logger.info("Import watcher watching %d import folder(s)", len(roots))
    try:
        while not stop.is_set():
            try:
                if await leadership.hold():
                    done = await run_watch_round(get_postgres_sessionmaker(), roots)
                    if done.deliveries or done.checked_again or done.gone or done.roots_skipped or done.jobs_ended:
                        logger.info(
                            "Import watch round: new deliveries %s, checked again %s, %d gone, "
                            "%d import job(s) ended, %d of %d import folder(s) skipped",
                            dict(sorted(done.deliveries.items())),
                            dict(sorted(done.checked_again.items())),
                            done.gone,
                            done.jobs_ended,
                            done.roots_skipped,
                            len(roots),
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a failed round never stops the watcher
                logger.warning("Import watch round failed: %s", describe_error(exc))
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.family_import_watch_interval_seconds)
            except TimeoutError:
                pass
    finally:
        await leadership.release()


stop_import_watcher = stop_worker


# The deliveries the list shows at most.
DELIVERY_LIST_LIMIT = 200

# A fixed column list (never input), with the delivery's import job's status.
_DELIVERY_COLUMNS = """
    d.id::text AS id, d.watch_root, d.folder_path, d.folder_name, d.state, d.package_fingerprint,
    d.marker_sha256, d.marker, d.run_id, d.family_id, d.project_id::text AS project_id, d.issues,
    d.warnings, d.manifest_sha256, d.job_id::text AS job_id, d.first_seen_at, d.last_seen_at,
    d.state_changed_at, j.status AS job_status, j.dry_run AS job_dry_run
"""


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _delivery_out(row: Any) -> ImportDeliveryOut:
    marker: dict[str, Any] = row["marker"] if isinstance(row["marker"], dict) else {}
    pipeline: dict[str, Any] = marker["pipeline"] if isinstance(marker.get("pipeline"), dict) else {}
    return ImportDeliveryOut(
        _id=ApiId(row["id"]),
        watch_root=row["watch_root"],
        folder_path=row["folder_path"],
        folder_name=row["folder_name"],
        state=row["state"],
        package_fingerprint=row["package_fingerprint"],
        marker_sha256=row["marker_sha256"],
        run_id=row["run_id"],
        family_id=row["family_id"],
        project_id=ApiId(row["project_id"]) if row["project_id"] else None,
        project=_text(marker.get("project")),
        analysis_type=_text(marker.get("analysis_type")),
        pipeline_name=_text(pipeline.get("name")),
        pipeline_version=_text(pipeline.get("version")),
        request_id=_text(marker.get("request_id")),
        issues=[ImportDeliveryIssue.model_validate(item) for item in _json_list(row["issues"])],
        warnings=[ImportDeliveryIssue.model_validate(item) for item in _json_list(row["warnings"])],
        manifest_sha256=row["manifest_sha256"],
        job_id=ApiId(row["job_id"]) if row["job_id"] else None,
        job_status=row["job_status"],
        job_dry_run=row["job_dry_run"],
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
        state_changed_at=row["state_changed_at"],
    )


async def list_import_deliveries(
    session: AsyncSession, *, states: list[str] | None, limit: int
) -> list[ImportDeliveryOut]:
    """The deliveries, the latest change first; with ``states``, those in one of them."""
    rows = (
        await session.execute(
            text(
                f"""
                SELECT {_DELIVERY_COLUMNS}
                FROM import_deliveries d
                LEFT JOIN family_import_jobs j ON j.id = d.job_id
                WHERE CAST(:states AS text[]) IS NULL OR d.state = ANY(CAST(:states AS text[]))
                ORDER BY d.state_changed_at DESC, d.id
                LIMIT :limit
                """
            ),
            {"states": states, "limit": max(1, min(int(limit), DELIVERY_LIST_LIMIT))},
        )
    ).mappings().all()
    return [_delivery_out(row) for row in rows]


async def get_import_delivery(session: AsyncSession, delivery_id: str) -> ImportDeliveryOut | None:
    """The delivery ``delivery_id`` (a canonical UUID), or None."""
    row = (
        await session.execute(
            text(
                f"""
                SELECT {_DELIVERY_COLUMNS}
                FROM import_deliveries d
                LEFT JOIN family_import_jobs j ON j.id = d.job_id
                WHERE d.id = CAST(:id AS uuid)
                """
            ),
            {"id": delivery_id},
        )
    ).mappings().first()
    return _delivery_out(row) if row is not None else None
