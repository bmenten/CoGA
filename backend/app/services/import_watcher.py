"""The import watcher: the packages pipelines deliver into the watched import folders.

A pipeline, or the step that copies its output onto the share, writes a package folder into the
folder of its project in a watched import folder (``FAMILY_IMPORT_WATCH_ROOTS``:
``<watched folder>/<project>/<package>``) with its package file ``coga-package.json``
(``coga_package``, docs/coga-package.md), which says what the package is and, by its ``status``,
whether it is complete. Every ``FAMILY_IMPORT_WATCH_INTERVAL_SECONDS`` the watcher looks into
each watched folder and each project folder in it. A package folder directly in the watched
folder, or in the folder of another project than its package names, needs attention for it
(``package_not_in_project_folder``, ``package_project_folder_differs``). A folder without a
package file is no delivery. One whose file says the run
is under way, or failed, is no delivery either, and an open delivery of that folder before it is
superseded: the pipeline is writing the folder again. A folder whose file says it is complete is
a delivery per package fingerprint (``family_package_fingerprint``): a folder that changes is a
new delivery, and an open one before it of the same folder is superseded. A new delivery is
checked once, and its state stored (``import_deliveries``, whose ``marker`` columns hold the
package file):

- ``incomplete``: a file the package file lists is missing or of another size: the copy runs
  still (its folder's fingerprint changes once it has finished, which is a new delivery);
- ``needs_attention``: the package file, a checksum, the project, the assembly, the manifest
  made from the file (``coga_package_manifest``), the package's validation, the family or its
  samples stand in the way, each an issue;
- ``ready``: it can be approved for import (``import_delivery_review``).

Some of those issues come from what CoGA holds, not from the folder: a project the package file
names that CoGA lacks, or of another assembly, and a family or samples that already exist. An open
delivery (``ready``, ``needs_attention``) seen again is checked again, as a whole, when that has
changed since its check (a project added, a family imported or deleted): its row then holds what
the new check found, and the chain the change.

A delivery still open whose folder, or package file, has been missing for ``GONE_AFTER_ROUNDS`` rounds
while its watched folder could be read is ``gone``. A watched folder that cannot be read, or that
lists nothing but hidden entries while it had open deliveries (a share not mounted reads as an
empty folder), is skipped for the round: nothing is decided on it. One that lists anything else,
even folders without a package file, is mounted and watched. Each change of a delivery's state is an event on the
clinical audit chain ``system:import-inbox``, by ``system:import-watcher``. Logs name counts,
never a folder or a family.

One watcher looks at a time, wherever import workers run: it holds a Postgres advisory lock on a
connection of its own (``WatcherLeadership``), and another process's watcher skips its rounds
until that connection is gone.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError
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
from .coga_package import (
    PACKAGE_FILE_NAME,
    CogaPackage,
    PackageIssue,
    PackageRead,
    check_package_files,
    package_document,
    package_issues,
    read_package_file,
)
from .coga_package_manifest import package_manifest, proposed_package, proposed_sections
from .family_identifiers import identifier_problem
from .family_package_common import _json_list, manifest_text_sha256
from .family_package_discovery import _detect_ped_path, discover_family_package_manifest
from .family_package_fingerprint import PackageFolderTooLarge, package_fingerprint
from .family_package_manifest import (
    _manifest_carrier_types,
    _manifest_member_overrides,
    _parse_ped_text_strict,
    _ped_members_for_import,
)
from .family_package_nipt import NIPT_VCF_COLUMN_WARNING
from .family_package_registration import (
    _existing_package_entity_warnings,
    family_member_differences,
    members_added_message,
    manifest_roi_issue,
    members_differ_message,
)
from .family_package_source import _authorized_local_roots, _listed_package_folder
from .family_package_validation import load_validated_family_package
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
# The issues a check takes from what CoGA holds, not from the folder: the project the package
# file names, and the family and samples the package holds.
_PROJECT_ISSUES = frozenset({"package_project_unknown", "package_assembly_differs"})
_EXISTING_ISSUES = frozenset({"existing_family", "existing_samples"})
GONE_AFTER_ROUNDS = 2
# How many unlisted files, or members, a warning names.
_UNLISTED_NAMED = 5

_PED_SEX = {"1": "male", "2": "female"}


class DeliveryIssue(BaseModel):
    """Why a delivery needs attention, or what an approver should see: a code and a fixed
    sentence; ``path`` a path in the package when it is about one file."""

    code: str
    message: str
    path: str | None = None


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
    package file (in its project folders, and directly in it); ``empty``, whether it lists nothing
    but hidden entries (a share that is not mounted reads as an empty folder; one that lists any
    other entry is mounted)."""

    folders: list[Path]
    empty: bool


def _names_in(folder: Path) -> list[str] | None:
    """The entries of ``folder`` but its hidden ones, sorted; None when it cannot be read."""
    try:
        with os.scandir(folder) as listing:
            return sorted(entry.name for entry in listing if not entry.name.startswith("."))
    except OSError:
        return None


def _holds_package_file(folder: Path) -> bool:
    try:
        os.lstat(folder / PACKAGE_FILE_NAME)
    except OSError:
        return False
    return True


def marked_folders(root: Path) -> WatchedFolderListing | None:
    """The package folders in the watched folder ``root`` that hold a package file, as selection
    takes them (``_listed_package_folder``: links resolved, none out of the import roots), and
    whether ``root`` lists nothing but hidden entries; None when ``root`` cannot be read.

    The watched folder holds a folder per project, named as the project, and a pipeline delivers
    each package folder into the folder of its project (``<root>/<project>/<package>``, the owner's
    decision of 2026-11-10). A folder directly in ``root`` that holds a package file is a package
    folder too, outside any project folder: its delivery needs attention for it
    (``package_not_in_project_folder``)."""
    if not root.is_dir():
        return None
    names = _names_in(root)
    if names is None:
        return None
    folders: list[Path] = []
    for name in names:
        child = _listed_package_folder(root / name)
        if child is None or not child.is_dir():
            continue
        if _holds_package_file(child):
            if child not in folders:
                folders.append(child)
            continue
        for inner in _names_in(child) or []:
            folder = _listed_package_folder(child / inner)
            if folder is not None and folder.is_dir() and _holds_package_file(folder) and folder not in folders:
                folders.append(folder)
    return WatchedFolderListing(folders=folders, empty=not names)


def project_folder_of(root: Path, folder: Path) -> str | None:
    """The name of the project folder the package folder ``folder`` lies in under the watched
    folder ``root``; None when it lies directly in ``root``."""
    return None if folder.parent.resolve() == root.resolve() else folder.parent.name


def _project_folder_issue(project_folder: str | None, project: str | None) -> DeliveryIssue | None:
    """Why a delivery's place in its watched folder is not the folder of its project: it lies
    directly in the watched folder, or in the folder of another project than its package names (the
    owner's decision of 2026-11-10). None when it lies in the folder of its project, and when the
    package names no project (``package_issues`` says so)."""
    if project_folder is None:
        return _issue(
            "package_not_in_project_folder",
            "The package folder lies directly in the watched import folder, not in the folder of its "
            "project: move it into <watched folder>/<project>/, or refuse the delivery.",
        )
    if project is not None and project != project_folder:
        return _issue(
            "package_project_folder_differs",
            f"The package lies in the folder of project '{project_folder}', and {PACKAGE_FILE_NAME} "
            f"names project '{project}': correct the project, or move the package into the folder of "
            "its project.",
        )
    return None


@dataclass(slots=True)
class DeliveryAssessment:
    """A new delivery as checked: its state, issues and warnings, and what the row keeps (``marker``
    and ``marker_sha256``: the package file as written, and its bytes' SHA-256)."""

    state: str
    issues: list[DeliveryIssue] = field(default_factory=list)
    warnings: list[DeliveryIssue] = field(default_factory=list)
    marker_sha256: str | None = None
    marker: dict[str, Any] | None = None
    run_id: str | None = None
    family_id: str | None = None
    project_id: str | None = None
    validation: dict[str, Any] | None = None
    manifest_text: str | None = None
    # The package as checked (the file, with what it leaves out proposed by Discover), and what
    # reading the folder found, which a check again reuses (``assess_content``).
    package: dict[str, Any] | None = None
    package_check: dict[str, Any] | None = None


def _issue(code: str, message: str, path: str | None = None) -> DeliveryIssue:
    return DeliveryIssue(code=code, message=message, path=path)


def _as_issue(issue: Any) -> DeliveryIssue:
    """A validation or Discover issue as a delivery keeps it: its code and message."""
    return DeliveryIssue(code=str(issue.code), message=str(issue.message), path=None)


def _mapping(value: Any) -> dict[str, Any]:
    """``value`` when it is a JSON object, else an empty one."""
    return value if isinstance(value, dict) else {}


def _package_issue(issue: PackageIssue) -> DeliveryIssue:
    """An issue of the package file: one about a listed file names its path."""
    path = issue.location if issue.code.startswith("file_") else None
    return DeliveryIssue(code=issue.code, message=issue.message, path=path)


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
) -> tuple[str | None, list[DeliveryIssue]]:
    """The project the package file names (``name``, with its ``assembly``) as CoGA holds it now:
    the project's id, None when CoGA has no such project, and the issues: a project CoGA lacks, or
    one of another assembly."""
    if not name:
        return None, []
    project = await _project(session, name)
    if project is None:
        return None, [
            _issue(
                "package_project_unknown",
                f"{PACKAGE_FILE_NAME} names the project '{name}', which CoGA does not have. Once it does, "
                "CoGA checks the delivery again.",
            )
        ]
    if assembly and project[1] and assembly != project[1]:
        return project[0], [
            _issue("package_assembly_differs", f"{PACKAGE_FILE_NAME} gives the assembly {assembly}; the project's is {project[1]}.")
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
    """The files the import reads, as paths in the package: each file an enabled dataset names (a
    folder it names, every file in it)."""
    paths: set[str] = set()
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


def _folder_ped_differences(folder: Path, package: CogaPackage) -> DeliveryIssue | None:
    """A warning when the folder holds a PED, which the pipeline's own tools may have read (to
    phase, to filter trios), that says otherwise than the package file's members: a member of the
    PED the file does not name, or one whose sex or parents differ. The import reads the file's
    members, never the PED. None when there is no PED, or it agrees."""
    if package.members is None or package.family is None:
        return None
    ped_path, _errors, _warnings = _detect_ped_path(folder, family_id=package.family.family_id)
    if ped_path is None:
        return None
    try:
        ped, _ped_errors = _parse_ped_text_strict(ped_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        ped = None
    if ped is None:
        return None
    members = {member.sample_id: member for member in package.members}
    differing: list[str] = []
    for row in ped.members:
        member = members.get(row.iid)
        if member is None:
            differing.append(row.iid)
            continue
        ped_sex = _PED_SEX.get(row.sex)
        sex_differs = ped_sex is not None and member.sex != "unknown" and ped_sex != member.sex
        parents = (row.pid if row.pid not in {"", "0"} else None, row.mid if row.mid not in {"", "0"} else None)
        if sex_differs or parents != (member.father, member.mother):
            differing.append(row.iid)
    if not differing:
        return None
    named = ", ".join(sorted(differing)[:_UNLISTED_NAMED])
    more = len(differing) - _UNLISTED_NAMED
    return _issue(
        "package_ped_differs",
        f"The folder's PED ({_relative(folder, str(ped_path)) or ped_path.name}) says otherwise than "
        f"{PACKAGE_FILE_NAME} of {named}" + (f", and {more} more" if more > 0 else "") + ": a member it "
        "does not name, or another sex or parents. The import reads the package file's members; a "
        "dataset the pipeline computed with the PED may not match them.",
    )


def _pedigree_change_issue(base: CogaPackage, effective: CogaPackage) -> DeliveryIssue | None:
    """An issue when the package was corrected in CoGA in what the pipeline used (a member's sex or
    parents, the couples) while a dataset the pipeline computed with them (its
    ``pipeline_used_pedigree``) is still in the import: the correction does not change what the
    pipeline computed (H4). None when nothing such changed, or none of those datasets is read."""
    base_members = {member.sample_id: member for member in base.members or []}
    changed = sorted(
        member.sample_id
        for member in effective.members or []
        if (previous := base_members.get(member.sample_id)) is not None
        and (member.sex, member.father, member.mother) != (previous.sex, previous.father, previous.mother)
    )
    couples_changed = [sorted(couple.partners) for couple in base.couples] != [
        sorted(couple.partners) for couple in effective.couples
    ]
    if not changed and not couples_changed:
        return None
    datasets = effective.datasets or {}
    used = [
        name
        for name in effective.pipeline_used_pedigree
        if name in datasets and datasets[name].get("enabled", True) is not False
    ]
    if not used:
        return None
    what = ", ".join(changed) if changed else "the couples"
    return _issue(
        "package_pedigree_changed",
        f"The pedigree was corrected in CoGA ({what}), but the pipeline computed "
        f"{', '.join(used)} with the pedigree it was given: leave those datasets out of the import, "
        "or run the pipeline again.",
    )


async def assess_content(
    session_factory: async_sessionmaker[AsyncSession],
    folder: Path,
    assessment: DeliveryAssessment,
    *,
    base: CogaPackage,
    effective: CogaPackage,
) -> DeliveryAssessment:
    """Check the delivery ``assessment`` describes with the package ``effective`` (the package as
    checked, ``base``, as corrected since): what reading its folder found
    (``assessment.package_check``, kept from its first check), the package itself, the pipeline's
    QC, the project, a correction of what the pipeline used, the manifest made from the package
    and its validation, the files the import reads, the family and samples CoGA holds, and the
    region of interest. Sets the assessment's state, issues, warnings, manifest and validation."""
    check = assessment.package_check or {}
    # A folder added by hand lists no files with checksums: the folder's fingerprint guards it until
    # the import, which records each file's SHA-256 (import_manual_delivery).
    by_hand = check.get("origin") == "manual"
    issues: list[DeliveryIssue] = [
        _package_issue(issue)
        for issue in package_issues(effective)
        if not (by_hand and issue.code == "package_field_missing" and issue.location == "files")
    ]
    issues.extend(DeliveryIssue.model_validate(item) for item in check.get("issues") or [])
    warnings: list[DeliveryIssue] = [DeliveryIssue.model_validate(item) for item in check.get("warnings") or []]
    place = check.get("project_folder")
    if isinstance(place, dict):
        # Delivered into the folder of a project: that project and the package's must agree.
        place_issue = _project_folder_issue(place.get("name"), effective.project)
        if place_issue is not None:
            issues.append(place_issue)
    if effective.qc is not None and effective.qc.status == "fail":
        issues.append(_issue("package_qc_failed", f"The pipeline's QC failed ({PACKAGE_FILE_NAME}, qc.status)."))
    elif effective.qc is not None and effective.qc.status == "warn":
        warnings.append(_issue("package_qc_warned", f"The pipeline's QC warned ({PACKAGE_FILE_NAME}, qc.status)."))
    async with session_factory() as session:
        project_id, project_issues = await _project_issues(session, effective.project, effective.assembly)
    assessment.project_id = project_id
    issues.extend(project_issues)
    assessment.manifest_text = None
    assessment.validation = None
    if effective.family is None or check.get("proposal_issues"):
        # A complete file names its family (package_issues), and what it leaves out could not be
        # proposed: nothing more is read.
        issues.extend(DeliveryIssue.model_validate(item) for item in check.get("proposal_issues") or [])
        assessment.issues, assessment.warnings = issues, warnings
        assessment.state = "needs_attention"
        return assessment
    pedigree_changed = _pedigree_change_issue(base, effective)
    if pedigree_changed is not None:
        issues.append(pedigree_changed)
    # JSON, read as YAML: every ID stays text.
    assessment.manifest_text = json.dumps(package_manifest(effective, None), indent=2, ensure_ascii=False)
    ped_differs = await asyncio.to_thread(_folder_ped_differences, folder, effective)
    if ped_differs is not None:
        warnings.append(ped_differs)

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
    if bundle is not None and not by_hand:
        verified = check.get("verified") or {}
        not_listed = [path for path in _files_the_import_reads(folder, validation) if path not in verified]
        issues.extend(
            _issue(
                "package_file_not_listed",
                f"The import reads a file {PACKAGE_FILE_NAME} does not list, or whose checksum differs.",
                path,
            )
            for path in not_listed
            if not any(issue.path == path for issue in issues)
        )
    async with session_factory() as session:
        # A family or samples that already exist: an approver chooses update or overwrite.
        issues.extend(
            _as_issue(issue)
            for issue in await _existing_package_entity_warnings(
                session, family_id=validation.family_id, sample_ids=validation.sample_ids
            )
        )
        # The package's members are the pedigree the import reads: an update adds the members the
        # family in CoGA lacks (a warning), and one that changes or lacks a member the family holds
        # is corrected first, or overwrites the family's pedigree (an administrator's choice).
        if bundle is not None and validation.family_id:
            differences = await family_member_differences(session, family_id=validation.family_id, ped=bundle.ped)
            if not differences.kept_by_an_update:
                issues.append(
                    _issue("package_members_differ_from_family", members_differ_message(validation.family_id, differences))
                )
            elif differences.added:
                warnings.append(
                    _issue("package_adds_members", members_added_message(validation.family_id, differences))
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


# Discover's warnings a delivery does not keep: a dataset it did not find (the delivery's own check
# says what the import lacks), and what validation checks again at every check of the delivery,
# against the package as corrected: kept, the warning a correction resolved would stay.
_NOT_KEPT_FROM_DISCOVER = frozenset({"dataset_not_detected", NIPT_VCF_COLUMN_WARNING, "package_folder_link"})


async def _proposal(
    folder: Path, package: CogaPackage
) -> tuple[CogaPackage, list[DeliveryIssue], list[DeliveryIssue]]:
    """The package with what it leaves out proposed from its folder by Discover, for the family it
    names (``proposed_package``), with Discover's notes and a warning per section proposed; or the
    package as it is, with the issues that kept a proposal from being made."""
    proposed = proposed_sections(package)
    if not proposed or package.family is None:
        return package, [], []
    draft = await asyncio.to_thread(
        discover_family_package_manifest,
        # The family is the one the package file names, not the folder's name.
        FamilyPackageManifestBuildRequest(folder_path=str(folder), family_id=package.family.family_id),
    )
    failed = [_issue("discover_failed", "Discover could not propose what the package file leaves out.")]
    if draft.errors or not draft.manifest_yaml:
        return package, [], [*failed, *(_as_issue(issue) for issue in draft.errors)]
    loaded = yaml.safe_load(draft.manifest_yaml)
    draft_manifest = loaded if isinstance(loaded, dict) else {}
    validation, bundle = await asyncio.to_thread(load_validated_family_package, folder, manifest_text=draft.manifest_yaml)
    if bundle is None:
        return package, [], [*failed, *(_as_issue(issue) for issue in validation.errors)]
    members = _ped_members_for_import(
        bundle.ped,
        carrier_types=_manifest_carrier_types(bundle.manifest),
        member_overrides=_manifest_member_overrides(bundle.manifest),
    )
    try:
        completed = proposed_package(package, draft_manifest, members)
    except ValidationError:
        return package, [], failed
    warnings = [_as_issue(issue) for issue in draft.warnings if issue.code not in _NOT_KEPT_FROM_DISCOVER]
    for section in proposed:
        warnings.append(
            _issue(
                f"package_{section}_proposed",
                f"{PACKAGE_FILE_NAME} names no {section}: CoGA proposed them from the folder"
                + (", from its PED or its per-sample folders" if section == "members" else ", from its layout")
                + ". Check them before approving.",
            )
        )
    return completed, warnings, []


async def assess_delivery(
    session_factory: async_sessionmaker[AsyncSession],
    folder: Path,
    read: PackageRead | None = None,
    *,
    watched_in: Path | None = None,
) -> DeliveryAssessment:
    """Check the new delivery in the package folder ``folder`` (see the module's docstring),
    with its package file as ``read`` read it (read here when not given): read the folder once
    (sizes, checksums, what the file leaves out proposed), then its content (``assess_content``).
    ``watched_in`` is the watched folder the watcher found it in: the project folder it lies in
    there is kept with the delivery, and compared with its package's project at every check."""
    if read is None:
        read = await asyncio.to_thread(read_package_file, folder)
    assessment = DeliveryAssessment(state="needs_attention", marker_sha256=read.sha256, marker=read.payload)
    payload = read.payload or {}
    assessment.run_id = _text(_mapping(payload.get("pipeline")).get("run_id"))
    family_id = _text(_mapping(payload.get("family")).get("family_id"))
    # A family ID no family can be stored under is not kept with the delivery.
    assessment.family_id = family_id if family_id and identifier_problem(family_id) is None else None
    if read.package is None:
        assessment.issues = [_package_issue(issue) for issue in read.issues]
        return assessment
    files = read.package.files or []
    sizes = await asyncio.to_thread(check_package_files, folder, files, checksums=False)
    if sizes.incomplete:
        assessment.state = "incomplete"
        assessment.issues = [
            _package_issue(issue) for issue in sizes.issues if issue.code in ("file_missing", "file_size_differs")
        ]
        return assessment
    checked = await asyncio.to_thread(check_package_files, folder, files, checksums=True)
    folder_warnings: list[DeliveryIssue] = []
    if checked.unlisted:
        named = ", ".join(checked.unlisted[:_UNLISTED_NAMED])
        more = len(checked.unlisted) - _UNLISTED_NAMED
        folder_warnings.append(
            _issue(
                "file_not_in_package",
                f"The folder holds {len(checked.unlisted)} file(s) {PACKAGE_FILE_NAME} does not list: {named}"
                + (f", and {more} more." if more > 0 else "."),
            )
        )
    package, proposal_warnings, proposal_issues = await _proposal(folder, read.package)
    assessment.package = package_document(package)
    assessment.package_check = {
        "issues": [_package_issue(issue).model_dump() for issue in checked.issues],
        "warnings": [issue.model_dump() for issue in [*folder_warnings, *proposal_warnings]],
        "verified": checked.sha256,
        "proposed": proposed_sections(read.package),
        "proposal_issues": [issue.model_dump() for issue in proposal_issues],
    }
    if watched_in is not None:
        assessment.package_check["project_folder"] = {"name": project_folder_of(watched_in, folder)}
    return await assess_content(session_factory, folder, assessment, base=package, effective=package)


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


def _issues_json(issues: list[DeliveryIssue]) -> str:
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
    its check read there: the project its package file names (added, deleted, or of another
    assembly), and the family and samples its package holds (imported, or deleted). It compares
    only what the check read: the project when the package file could be read (the check named it
    unknown, kept its id, or went on to validate the package), the family and samples when it
    validated the package. So a check that could not run, or a package file that could not be
    read, is never checked again: what it found holds until the folder changes."""
    codes = {str(item.get("code")) for item in _json_list(delivery["issues"]) if isinstance(item, dict)}
    # The project of the package as checked, or as corrected since; else the package file's.
    marker = _json_object(delivery.get("effective")) or _json_object(delivery["marker"])
    validation = _json_object(delivery["validation"])
    project_read = "package_project_unknown" in codes or delivery["project_id"] is not None or validation is not None
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


async def _assess(
    session_factory: async_sessionmaker[AsyncSession],
    folder: Path,
    read: PackageRead | None = None,
    *,
    watched_in: Path | None = None,
) -> DeliveryAssessment:
    """``assess_delivery``, or, when the check fails, the delivery as one that needs attention for it."""
    try:
        return await assess_delivery(session_factory, folder, read, watched_in=watched_in)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - one folder's failure never stops the round
        return _failed_check(exc)


async def latest_revision(session: AsyncSession, delivery_id: str) -> tuple[int, dict[str, Any] | None]:
    """The latest correction of the delivery ``delivery_id`` made in CoGA
    (``import_delivery_revisions``): its number and the package it ends with; ``(0, None)`` when
    there is none."""
    row = (
        await session.execute(
            text(
                """
                SELECT revision, package FROM import_delivery_revisions
                WHERE delivery_id = CAST(:id AS uuid)
                ORDER BY revision DESC
                LIMIT 1
                """
            ),
            {"id": delivery_id},
        )
    ).mappings().first()
    return (int(row["revision"]), _json_object(row["package"])) if row is not None else (0, None)


def _failed_check(exc: BaseException) -> DeliveryAssessment:
    logger.warning("Import watcher could not check a delivery: %s", describe_error(exc))
    return DeliveryAssessment(
        state="needs_attention",
        issues=[_issue("watch_check_failed", "CoGA could not check the delivery; it tries again when the folder changes.")],
    )


async def _assess_again(
    session_factory: async_sessionmaker[AsyncSession], folder: Path, seen: Mapping[str, Any]
) -> DeliveryAssessment:
    """Check the delivery ``seen`` again, as a whole but for its folder's files: the package it
    kept, as corrected since (``seen["effective"]``), with what reading its folder found. A delivery
    stored without them is checked again from its folder (``assess_delivery``)."""
    package = _json_object(seen.get("package"))
    package_check = _json_object(seen.get("package_check"))
    if package is None or package_check is None:
        return await _assess(session_factory, folder)
    try:
        base = CogaPackage.model_validate(package)
        effective = CogaPackage.model_validate(_json_object(seen.get("effective")) or package)
        assessment = DeliveryAssessment(
            state="needs_attention",
            marker_sha256=seen.get("marker_sha256"),
            marker=_json_object(seen.get("marker")),
            run_id=seen.get("run_id"),
            family_id=seen.get("family_id"),
            package=package,
            package_check=package_check,
        )
        return await assess_content(session_factory, folder, assessment, base=base, effective=effective)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - one folder's failure never stops the round
        return _failed_check(exc)


async def look_at_folder(
    session_factory: async_sessionmaker[AsyncSession], root: Path, folder: Path
) -> tuple[str, str] | None:
    """Look at one package folder: one whose package file says its run is under way, or failed, is
    no delivery, and the open deliveries of the folder before it are superseded
    (``_supersede_for_run``). Otherwise a new delivery is checked and stored, and an open one
    before it of the folder superseded; a delivery seen before is seen again, and checked again
    when it is open (``ready`` or ``needs_attention``) and what CoGA holds has changed in what its
    check read (``_coga_side_changed``). Returns ``("new", state)``, ``("checked_again", state)``
    or ``("superseded", count)`` with the number superseded for a run, or None when the delivery
    was only seen again or the folder changed while it was checked (the next round takes it)."""
    read = await asyncio.to_thread(read_package_file, folder)
    if not read.offered:
        superseded = await _supersede_for_run(session_factory, folder, read)
        return ("superseded", str(superseded)) if superseded else None
    fingerprint = await _fingerprint(folder)
    if fingerprint is None:
        return None
    async with session_factory() as session:
        found = (
            await session.execute(
                text(
                    """
                    UPDATE import_deliveries
                    SET last_seen_at = now(), missing_rounds = 0
                    WHERE folder_path = :folder AND package_fingerprint = :fingerprint AND origin = 'watched'
                    RETURNING id::text AS id, state, issues, project_id::text AS project_id, marker, validation,
                              marker_sha256, run_id, family_id, package, package_check
                    """
                ),
                {"folder": str(folder), "fingerprint": fingerprint},
            )
        ).mappings().first()
        seen: dict[str, Any] | None = dict(found) if found is not None else None
        if seen is not None:
            check_again = False
            if seen["state"] in _CHECKED_AGAIN_STATES:
                # The package as corrected in CoGA, if it was: its project is the one compared.
                _revision, revised = await latest_revision(session, str(seen["id"]))
                seen = {**seen, "effective": revised or seen.get("package")}
                check_again = await _coga_side_changed(session, seen)
            await session.commit()
            if not check_again:
                return None
    if seen is not None:
        assessment = await _assess_again(session_factory, folder, seen)
        if await _fingerprint(folder) != fingerprint:
            return None
        state = await _store_check_again(session_factory, folder, str(seen["id"]), fingerprint, assessment)
        return ("checked_again", state) if state is not None else None
    # The package file as read before the fingerprint: a folder changed since then is left below.
    assessment = await _assess(session_factory, folder, read, watched_in=root)
    # Changed while it was checked: what was checked is not what is there.
    if await _fingerprint(folder) != fingerprint:
        return None
    stored = await _store_new_delivery(session_factory, root, folder, fingerprint, assessment)
    return ("new", stored) if stored is not None else None


async def _supersede_for_run(
    session_factory: async_sessionmaker[AsyncSession], folder: Path, read: PackageRead
) -> int:
    """Supersede the open deliveries of ``folder``, whose package file says a run is under way in
    it or failed (``read.status``): the pipeline is writing the folder again, so nothing checked
    before is what it will deliver. Each is recorded on the chain with the run. Returns how many."""
    run_id = _text(_mapping((read.payload or {}).get("pipeline")).get("run_id"))
    async with session_factory() as session:
        rows = (
            await session.execute(
                text(
                    """
                    UPDATE import_deliveries
                    SET state = 'superseded', state_changed_at = now()
                    WHERE folder_path = :folder AND state = ANY(CAST(:open AS text[]))
                    RETURNING id::text, package_fingerprint
                    """
                ),
                {"folder": str(folder), "open": list(OPEN_STATES)},
            )
        ).all()
        for delivery_id, old_fingerprint in rows:
            await _record(
                session,
                action="import_delivery_superseded",
                delivery_id=str(delivery_id),
                after={
                    "folder": folder.name,
                    "package_fingerprint": old_fingerprint,
                    "package_status": read.status,
                    "run_id": run_id,
                    "package_sha256": read.sha256,
                },
                summary=f"Delivery {folder.name}: superseded, a pipeline run in its folder is {read.status}",
            )
        await session.commit()
    return len(rows)


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
                        manifest_text, manifest_sha256, package, package_check
                    )
                    VALUES (
                        :root, :folder, :name, :fingerprint, :marker_sha256, CAST(:marker AS jsonb),
                        :run_id, :family_id, CAST(:project_id AS uuid), :state, CAST(:issues AS jsonb),
                        CAST(:warnings AS jsonb), CAST(:validation AS jsonb), :manifest_text, :manifest_sha256,
                        CAST(:package AS jsonb), CAST(:package_check AS jsonb)
                    )
                    ON CONFLICT (folder_path, package_fingerprint) WHERE origin = 'watched' DO NOTHING
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
                    "package": json.dumps(assessment.package) if assessment.package is not None else None,
                    "package_check": json.dumps(assessment.package_check) if assessment.package_check is not None else None,
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
    decided meanwhile (approved, refused) is left as it is. What the package file said is kept when the
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
                    package = COALESCE(CAST(:package AS jsonb), package),
                    package_check = COALESCE(CAST(:package_check AS jsonb), package_check),
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
                "package": json.dumps(assessment.package) if assessment.package is not None else None,
                "package_check": json.dumps(assessment.package_check) if assessment.package_check is not None else None,
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
    package file, was not seen; one missing for ``GONE_AFTER_ROUNDS`` rounds is gone. Returns how many
    went."""
    gone = 0
    async with session_factory() as session:
        rows = (
            await session.execute(
                text(
                    """
                    UPDATE import_deliveries
                    SET missing_rounds = missing_rounds + 1
                    WHERE watch_root = :root AND origin = 'watched' AND state = ANY(CAST(:open AS text[]))
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
                summary=f"Delivery {name}: gone, its folder or package file is no longer there",
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
    # Open deliveries superseded because a run is under way in their folder, or failed.
    superseded_for_run: int = 0
    gone: int = 0
    jobs_ended: int = 0


async def _open_deliveries(session_factory: async_sessionmaker[AsyncSession], root: Path) -> int:
    async with session_factory() as session:
        return int(
            (
                await session.execute(
                    text(
                        "SELECT count(*) FROM import_deliveries WHERE watch_root = :root AND origin = 'watched' "
                        "AND state = ANY(CAST(:open AS text[]))"
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
        # mounted, so a delivery whose folder or package file is not in it is missing.
        if listing is None or (listing.empty and await _open_deliveries(session_factory, root)):
            result.roots_skipped += 1
            continue
        folders = listing.folders
        result.roots_watched += 1
        for folder in folders:
            looked = await look_at_folder(session_factory, root, folder)
            if looked is not None:
                kind, state = looked
                if kind == "superseded":
                    result.superseded_for_run += int(state)
                    continue
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
                    if (
                        done.deliveries
                        or done.checked_again
                        or done.superseded_for_run
                        or done.gone
                        or done.roots_skipped
                        or done.jobs_ended
                    ):
                        logger.info(
                            "Import watch round: new deliveries %s, checked again %s, %d superseded by a "
                            "run, %d gone, %d import job(s) ended, %d of %d import folder(s) skipped",
                            dict(sorted(done.deliveries.items())),
                            dict(sorted(done.checked_again.items())),
                            done.superseded_for_run,
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
    d.state_changed_at, d.origin, d.package, j.status AS job_status, j.dry_run AS job_dry_run,
    (SELECT COALESCE(max(r.revision), 0) FROM import_delivery_revisions r WHERE r.delivery_id = d.id) AS revision
"""


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _delivery_out(row: Any) -> ImportDeliveryOut:
    # What the package file says; for a folder added by hand, which has none, the package CoGA made.
    marker: dict[str, Any] = (
        row["marker"]
        if isinstance(row["marker"], dict)
        else (row["package"] if isinstance(row["package"], dict) else {})
    )
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
        revision=int(row["revision"]),
        origin=row["origin"],
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
