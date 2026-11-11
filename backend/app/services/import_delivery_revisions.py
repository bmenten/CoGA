"""Corrections of a delivered package, made in CoGA before it is approved.

A pipeline's package file says what the pipeline knows; the laboratory completes or corrects the
rest in **Deliveries** before the import (the owner's decisions of 2026-11-10, docs/coga-package.md,
"What can be changed in CoGA"). The pipeline's file is never changed (``import_deliveries.marker``):
each correction is a **revision** (``import_delivery_revisions``, append-only), numbered from 1,
that keeps what it changed (each field, its value before and after, its kind), the reason, who
made it and when, and the whole package after it. The import reads the latest.

Which fields may change:

- **Fixed, the pipeline's**: the format, the status and time, the pipeline and its run, the
  assembly, the family ID, every sample ID, the files, the QC, the request, the reference, the
  notes, ``extra`` and ``pipeline_used_pedigree``. A correction of one is refused (400): a wrong
  value is a refused delivery and a new run or copy.
- **Members added and removed** (``["members", <sample ID>]``, the member as its value, or null),
  the laboratory's (the owner's decision of 2026-11-11). To a pipeline's package only a sample its
  files name and no member is (``dataset_vcf_sample_unknown``: a column of the phased family VCF,
  an affected relative read for segregation) is added, so no sample ID is typed; only a member
  added in CoGA is removed again. CoGA made the package of a folder added by hand (``origin``
  ``manual``), so there any member is added or removed. A member added with parents, or removed,
  changes the pedigree the pipeline used (``import_watcher._pedigree_change_issue``).
- **Used by the pipeline**: a member's ``sex``, ``father`` and ``mother``, and the ``couples``.
  They change, with a reason; while a dataset the pipeline computed with them
  (``pipeline_used_pedigree``) is in the import, the delivery needs attention
  (``package_pedigree_changed``, ``import_watcher._pedigree_change_issue``).
- **The laboratory's**: the ``project``, the ``analysis_type``, a member's ``role``,
  ``clinical_status``, carrier fields, ``hpo``, ``related_to``, ``assay`` and ``assay_panel``, the
  family's ``roi`` and ``pgt``, and each dataset's entry (in or out of the import, its options,
  another file the package lists).

A correction is a list of changes, or the whole package as the person edited it
(``package_changes``: the changes are what differs, field by field, and the same rules hold).
It names the revision it was made on (a correction made on another is refused, 409),
and is made on a delivery that is ready or needs attention and whose folder is as it was checked.
It is checked at once as the watcher checks a delivery (``import_watcher.assess_content``), with
what reading the folder found; the delivery's state, issues, warnings and manifest follow. Each
revision is an event on the clinical audit chain ``system:import-inbox``, by the person who made
it, with what it changed and why.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_sessionmaker
from ..core.sql import require_uuid
from ..schemas import (
    ImportDeliveryChange,
    ImportDeliveryPackageOut,
    ImportDeliveryRevisedOut,
    ImportDeliveryRevisionChange,
    ImportDeliveryRevisionOut,
)
from .access_control import CurrentUser
from .clinical_audit_service import audit_actor
from .coga_package import CogaPackage, package_document, package_document_sha256
from .family_package_common import _json_list, manifest_text_sha256
from .import_delivery_review import (
    DELIVERY_CHANGED,
    DELIVERY_NOT_FOUND,
    _delivery,
    _folder_fingerprint,
    _record,
)
from .import_watcher import DeliveryAssessment, assess_content, latest_revision

ChangeKind = Literal["laboratory", "pipeline_used"]

# The states a delivery is corrected in: open, and its package read.
REVISABLE_STATES = ("ready", "needs_attention")
LABORATORY_MEMBER_FIELDS = frozenset(
    {"role", "clinical_status", "carrier_status", "carrier_type", "hpo", "related_to", "assay", "assay_panel"}
)
PIPELINE_MEMBER_FIELDS = frozenset({"sex", "father", "mother"})
_PGT_FIELDS = frozenset({"inheritance_model", "affected_parents", "indexes"})
# Fields that are lists: set to nothing, they are empty.
_LIST_FIELDS = frozenset({"related_to", "affected_parents", "indexes", "couples"})

DELIVERY_REVISED = (
    "The delivery was corrected since you looked at it: look at it again, and correct or approve "
    "the package as it is now."
)
FIXED_FIELD = (
    "is the pipeline's and is not changed in CoGA: a wrong value is a refused delivery, and a new "
    "run or copy of the package."
)
# The issue of a sample the package's files name and no member is: the members a correction may
# add to a pipeline's package (family_package_validation, the phased family VCF's columns).
ADDABLE_SAMPLE_ISSUE = "dataset_vcf_sample_unknown"


@dataclass(frozen=True)
class MemberRule:
    """Which members a correction may add or remove (see the module's docstring): ``any_member``
    for a package CoGA made (a folder added by hand); otherwise the samples the package's files name
    and no member is (``addable``), and no member of the package as checked (``as_checked``)."""

    any_member: bool = False
    addable: frozenset[str] = field(default_factory=frozenset)
    as_checked: frozenset[str] = field(default_factory=frozenset)


def addable_samples(issues: Any) -> list[str]:
    """The samples the package's files name and no member is, from a delivery's ``issues``."""
    return sorted(
        {
            str(issue["sample_id"])
            for issue in _json_list(issues)
            if isinstance(issue, dict) and issue.get("code") == ADDABLE_SAMPLE_ISSUE and issue.get("sample_id")
        }
    )


def _member_ids(document: dict[str, Any] | None) -> list[str]:
    return [
        str(member.get("sample_id"))
        for member in (document or {}).get("members") or []
        if isinstance(member, dict) and member.get("sample_id")
    ]


def change_kind(path: list[str]) -> ChangeKind:
    """The kind of the field ``path`` names (see the module's docstring); a field that is fixed,
    or that the format does not have, is refused (400)."""
    where = ".".join(path)
    if path in (["project"], ["analysis_type"]) or path == ["family", "roi"] or path == ["family", "pgt"]:
        return "laboratory"
    if len(path) == 3 and path[:2] == ["family", "pgt"] and path[2] in _PGT_FIELDS:
        return "laboratory"
    if len(path) == 2 and path[0] == "datasets":
        return "laboratory"
    if path == ["couples"]:
        return "pipeline_used"
    if len(path) == 3 and path[0] == "members":
        if path[2] in LABORATORY_MEMBER_FIELDS:
            return "laboratory"
        if path[2] in PIPELINE_MEMBER_FIELDS:
            return "pipeline_used"
        if path[2] == "sample_id":
            raise HTTPException(status_code=400, detail=f"{where} {FIXED_FIELD}")
    known_fixed = {"schema", "status", "written_at", "pipeline", "assembly", "files", "qc", "request_id",
                   "reference", "notes", "extra", "pipeline_used_pedigree", "family", "members"}
    if path and path[0] in known_fixed:
        raise HTTPException(status_code=400, detail=f"{where} {FIXED_FIELD}")
    raise HTTPException(status_code=400, detail=f"{where} is no field of the package that can be corrected.")


def _find_member(document: dict[str, Any], sample_id: str) -> dict[str, Any] | None:
    for member in document.get("members") or []:
        if member.get("sample_id") == sample_id:
            return member
    return None


def _member(document: dict[str, Any], sample_id: str) -> dict[str, Any]:
    member = _find_member(document, sample_id)
    if member is None:
        raise HTTPException(
            status_code=400,
            detail=f"members.{sample_id} is no member of the package: add the member first.",
        )
    return member


def _add_or_remove_member(
    document: dict[str, Any], sample_id: str, value: Any, rule: MemberRule | None
) -> ImportDeliveryRevisionChange:
    """Add the member ``value`` (null: remove the member) ``sample_id`` names to ``document``, as
    ``rule`` allows (see the module's docstring), and say what changed."""
    path = ["members", sample_id]
    existing = _find_member(document, sample_id)
    if value is None:
        if existing is None:
            raise HTTPException(status_code=400, detail=f"members.{sample_id} is no member of the package.")
        if rule is None or (not rule.any_member and sample_id in rule.as_checked):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"members.{sample_id} is a member of the pipeline's package and is not removed in CoGA: "
                    "a wrong member is a refused delivery."
                ),
            )
        document["members"] = [member for member in document.get("members") or [] if member is not existing]
        return ImportDeliveryRevisionChange(path=path, kind="laboratory", before=copy.deepcopy(existing), after=None)
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail=f"members.{sample_id} is added as a member: give its fields.")
    if existing is not None:
        raise HTTPException(
            status_code=400, detail=f"members.{sample_id} is a member already: correct its fields instead."
        )
    if value.get("sample_id", sample_id) != sample_id:
        raise HTTPException(status_code=400, detail=f"members.{sample_id} is added with another sample_id.")
    if rule is None or (not rule.any_member and sample_id not in rule.addable):
        raise HTTPException(
            status_code=400,
            detail=(
                f"members.{sample_id} is no sample the package's files name without it being a member: "
                "only such a sample is added to a pipeline's package in CoGA."
            ),
        )
    member = {"sample_id": sample_id, **{key: copy.deepcopy(item) for key, item in value.items() if key != "sample_id"}}
    members = document.get("members")
    if not isinstance(members, list):
        members = document["members"] = []
    members.append(member)
    return ImportDeliveryRevisionChange(path=path, kind="laboratory", before=None, after=copy.deepcopy(member))


def _container(document: dict[str, Any], path: list[str]) -> tuple[dict[str, Any], str]:
    """The object that holds the field ``path`` names, and the field's name in it."""
    if path[0] == "members":
        return _member(document, path[1]), path[2]
    if path[0] == "family":
        family = document.setdefault("family", {})
        if len(path) == 3:
            pgt = family.get("pgt")
            if not isinstance(pgt, dict):
                pgt = family["pgt"] = {}
            return pgt, path[2]
        return family, path[1]
    if path[0] == "datasets":
        datasets = document.get("datasets")
        if not isinstance(datasets, dict):
            datasets = document["datasets"] = {}
        return datasets, path[1]
    return document, path[0]


def apply_changes(
    document: dict[str, Any], changes: list[ImportDeliveryChange], members: MemberRule | None = None
) -> tuple[dict[str, Any], list[ImportDeliveryRevisionChange]]:
    """The package ``document`` with ``changes`` made: each field set to its value, or removed when
    the value is null (a list emptied), and each member added or removed as ``members`` allows (none
    without it). Returns the new package and what changed, each with its kind and its value before
    and after. A field named twice, a fixed one, or changes that change nothing are refused (400)."""
    revised = copy.deepcopy(document)
    made: list[ImportDeliveryRevisionChange] = []
    seen: set[tuple[str, ...]] = set()
    # Members added or removed first, so a change of an added member's field (or of another
    # member's parent, to one added) finds it.
    ordered = sorted(changes, key=lambda change: 0 if len(change.path) == 2 and change.path[0] == "members" else 1)
    for change in ordered:
        path = list(change.path)
        if tuple(path) in seen:
            raise HTTPException(status_code=400, detail=f"{'.'.join(path)} is changed twice in one correction.")
        seen.add(tuple(path))
        if len(path) == 2 and path[0] == "members":
            made.append(_add_or_remove_member(revised, path[1], change.value, members))
            continue
        kind = change_kind(path)
        holder, name = _container(revised, path)
        before = copy.deepcopy(holder.get(name))
        after = change.value
        if after is None and name in _LIST_FIELDS:
            after = []
        if before == after or (before in (None, []) and after in (None, [])):
            continue
        if after is None:
            holder.pop(name, None)
        else:
            holder[name] = copy.deepcopy(after)
        made.append(ImportDeliveryRevisionChange(path=path, kind=kind, before=before, after=after))
    if not made:
        raise HTTPException(status_code=400, detail="The correction changes nothing in the package.")
    family = revised.get("family")
    if isinstance(family, dict) and family.get("pgt") == {}:
        family.pop("pgt")
    return revised, made


def _differs(before: Any, after: Any) -> bool:
    return not (before == after or (before in (None, []) and after in (None, [])))


def _member_changes(before: Any, after: Any) -> list[ImportDeliveryChange]:
    """What differs between two member lists, member by member: one added or removed whole, and
    each field of one in both."""
    old = {member["sample_id"]: member for member in before or [] if isinstance(member, dict) and "sample_id" in member}
    new = {member["sample_id"]: member for member in after or [] if isinstance(member, dict) and "sample_id" in member}
    changes: list[ImportDeliveryChange] = []
    for sample_id, member in old.items():
        if sample_id not in new:
            changes.append(ImportDeliveryChange(path=["members", sample_id], value=None))
            continue
        edited = new[sample_id]
        for name in sorted((set(member) | set(edited)) - {"sample_id"}):
            if _differs(member.get(name), edited.get(name)):
                changes.append(ImportDeliveryChange(path=["members", sample_id, name], value=edited.get(name)))
    for sample_id, member in new.items():
        if sample_id not in old:
            changes.append(ImportDeliveryChange(path=["members", sample_id], value=member))
    return changes


def package_changes(before: dict[str, Any], after: Any) -> list[ImportDeliveryChange]:
    """The changes that make the package ``before`` into the package ``after`` (the whole package
    as a person edited it), field by field as a correction names them: a member added or removed, a
    member's field, a family field or PGT field, a dataset, or a top-level field. A fixed field
    that differs is among them, and is refused when the changes are made. ``after`` must follow the
    format (400), and is compared as CoGA keeps a package."""
    if not isinstance(after, dict):
        raise HTTPException(status_code=400, detail="The edited package is no JSON object.")
    try:
        edited = package_document(CogaPackage.model_validate(after))
    except ValidationError as exc:
        where = ", ".join(sorted({".".join(str(part) for part in error["loc"]) for error in exc.errors()}))
        raise HTTPException(status_code=400, detail=f"The edited package does not follow the format: {where}.") from exc
    changes: list[ImportDeliveryChange] = []
    for key in sorted(set(before) | set(edited)):
        old, new = before.get(key), edited.get(key)
        if key == "members":
            changes.extend(_member_changes(old, new))
        elif key in ("family", "datasets") and isinstance(old, dict) and isinstance(new, dict):
            for name in sorted(set(old) | set(new)):
                old_value, new_value = old.get(name), new.get(name)
                if key == "family" and name == "pgt" and isinstance(old_value or {}, dict) and isinstance(new_value or {}, dict):
                    old_pgt, new_pgt = old_value or {}, new_value or {}
                    changes.extend(
                        ImportDeliveryChange(path=["family", "pgt", field_name], value=new_pgt.get(field_name))
                        for field_name in sorted(set(old_pgt) | set(new_pgt))
                        if _differs(old_pgt.get(field_name), new_pgt.get(field_name))
                    )
                elif _differs(old_value, new_value):
                    changes.append(ImportDeliveryChange(path=[key, name], value=new_value))
        elif _differs(old, new):
            changes.append(ImportDeliveryChange(path=[key], value=new))
    if not changes:
        raise HTTPException(status_code=400, detail="The correction changes nothing in the package.")
    return changes


def _json_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else None


async def _locked_delivery(session: AsyncSession, delivery_id: str) -> dict[str, Any]:
    delivery_uuid = require_uuid(delivery_id, DELIVERY_NOT_FOUND, status_code=404)
    row = (
        await session.execute(
            text(
                """
                SELECT id::text AS id, folder_path, folder_name, state, package_fingerprint,
                       marker_sha256, marker, run_id, family_id, project_id::text AS project_id, issues,
                       warnings, package, package_check, origin
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


async def revisions_of(session: AsyncSession, delivery_id: str) -> list[ImportDeliveryRevisionOut]:
    """The corrections of the delivery ``delivery_id``, the first first."""
    rows = (
        await session.execute(
            text(
                """
                SELECT revision, changes, reason, created_by_name, created_at, package_sha256
                FROM import_delivery_revisions
                WHERE delivery_id = CAST(:id AS uuid)
                ORDER BY revision
                """
            ),
            {"id": delivery_id},
        )
    ).mappings().all()
    return [
        ImportDeliveryRevisionOut(
            revision=int(row["revision"]),
            changes=[ImportDeliveryRevisionChange.model_validate(item) for item in _json_list(row["changes"])],
            reason=str(row["reason"]),
            created_by_name=str(row["created_by_name"]),
            created_at=row["created_at"],
            package_sha256=str(row["package_sha256"]),
        )
        for row in rows
    ]


async def _package_out(session: AsyncSession, delivery: dict[str, Any]) -> ImportDeliveryPackageOut:
    revisions = await revisions_of(session, delivery["id"])
    _revision, revised = await latest_revision(session, delivery["id"])
    as_checked = _json_object(delivery.get("package"))
    package = revised or as_checked
    check = _json_object(delivery.get("package_check")) or {}
    return ImportDeliveryPackageOut(
        delivery_id=delivery["id"],
        revision=revisions[-1].revision if revisions else 0,
        package=package,
        package_sha256=package_document_sha256(package) if package is not None else None,
        original=_json_object(delivery.get("marker")),
        proposed=[str(section) for section in check.get("proposed") or []],
        revisions=revisions,
        origin="manual" if delivery.get("origin") == "manual" else "watched",
        addable_members=addable_samples(delivery.get("issues")),
        members_as_checked=_member_ids(as_checked),
    )


async def delivery_package(session: AsyncSession, delivery_id: str) -> ImportDeliveryPackageOut:
    """The package of the delivery ``delivery_id`` as the import would read it now (the latest
    revision's, or the package as checked), the pipeline's file, what CoGA proposed, and every
    correction. 404 for no such delivery."""
    delivery_uuid = require_uuid(delivery_id, DELIVERY_NOT_FOUND, status_code=404)
    row = (
        await session.execute(
            text(
                """
                SELECT id::text AS id, marker, package, package_check, issues, origin
                FROM import_deliveries
                WHERE id = CAST(:id AS uuid)
                """
            ),
            {"id": delivery_uuid},
        )
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail=DELIVERY_NOT_FOUND)
    return await _package_out(session, dict(row))


async def revise_delivery(
    session: AsyncSession,
    *,
    delivery_id: str,
    user: CurrentUser,
    revision: int,
    changes: list[ImportDeliveryChange] | None = None,
    package: dict[str, Any] | None = None,
    reason: str,
) -> ImportDeliveryRevisedOut:
    """Correct the package of the delivery ``delivery_id`` (see the module's docstring), made on
    its revision ``revision``, as ``user``, for ``reason``, with ``changes`` or the whole edited
    ``package``: store the revision, check the delivery again with it, and record it. Refused: 404 for no such delivery; 409 for one that is not ready
    or needing attention, whose package CoGA could not read, that was corrected since ``revision``
    or whose folder changed; 400 for a change the rules refuse or a package the format refuses."""
    delivery = await _locked_delivery(session, delivery_id)
    if delivery["state"] not in REVISABLE_STATES:
        raise HTTPException(
            status_code=409,
            detail=f"The delivery is {delivery['state'].replace('_', ' ')}: only one that is ready or needs attention is corrected.",
        )
    base_document = _json_object(delivery["package"])
    package_check = _json_object(delivery["package_check"])
    if base_document is None or package_check is None:
        raise HTTPException(
            status_code=409,
            detail="CoGA could not read the delivery's package: correct it in the package, which makes a new delivery.",
        )
    current, revised = await latest_revision(session, delivery["id"])
    if revision != current:
        raise HTTPException(status_code=409, detail=DELIVERY_REVISED)
    if await _folder_fingerprint(delivery["folder_path"]) != delivery["package_fingerprint"]:
        raise HTTPException(status_code=409, detail=DELIVERY_CHANGED)

    before = revised or base_document
    rule = MemberRule(
        any_member=delivery.get("origin") == "manual",
        addable=frozenset(addable_samples(delivery["issues"])),
        as_checked=frozenset(_member_ids(base_document)),
    )
    if package is not None:
        changes = package_changes(before, package)
    document, made = apply_changes(before, changes or [], rule)
    try:
        effective = CogaPackage.model_validate(document)
    except ValidationError as exc:
        where = ", ".join(sorted({".".join(str(part) for part in error["loc"]) for error in exc.errors()}))
        raise HTTPException(status_code=400, detail=f"The corrected package does not follow the format: {where}.") from exc
    document = package_document(effective)
    base = CogaPackage.model_validate(base_document)
    assessment = DeliveryAssessment(
        state="needs_attention",
        marker_sha256=delivery["marker_sha256"],
        marker=_json_object(delivery["marker"]),
        run_id=delivery["run_id"],
        family_id=delivery["family_id"],
        package=base_document,
        package_check=package_check,
    )
    await assess_content(get_postgres_sessionmaker(), Path(delivery["folder_path"]), assessment, base=base, effective=effective)

    number = current + 1
    package_sha256 = package_document_sha256(document)
    await session.execute(
        text(
            """
            INSERT INTO import_delivery_revisions (
                delivery_id, revision, base_sha256, package, package_sha256, changes, reason,
                created_by, created_by_name
            )
            VALUES (
                CAST(:delivery AS uuid), :revision, :base_sha256, CAST(:package AS jsonb), :package_sha256,
                CAST(:changes AS jsonb), :reason, CAST(:created_by AS uuid), :created_by_name
            )
            """
        ),
        {
            "delivery": delivery["id"],
            "revision": number,
            "base_sha256": package_document_sha256(before),
            "package": json.dumps(document),
            "package_sha256": package_sha256,
            "changes": json.dumps([change.model_dump(mode="json") for change in made]),
            "reason": reason,
            "created_by": getattr(user, "id", None),
            "created_by_name": audit_actor(user),
        },
    )
    await session.execute(
        text(
            """
            UPDATE import_deliveries
            SET state = :state,
                issues = CAST(:issues AS jsonb),
                warnings = CAST(:warnings AS jsonb),
                validation = CAST(:validation AS jsonb),
                project_id = CAST(:project_id AS uuid),
                manifest_text = :manifest_text,
                manifest_sha256 = :manifest_sha256,
                state_changed_at = CASE WHEN state = :state THEN state_changed_at ELSE now() END
            WHERE id = CAST(:id AS uuid)
            """
        ),
        {
            "id": delivery["id"],
            "state": assessment.state,
            "issues": json.dumps([issue.model_dump() for issue in assessment.issues]),
            "warnings": json.dumps([issue.model_dump() for issue in assessment.warnings]),
            "validation": json.dumps(assessment.validation) if assessment.validation is not None else None,
            "project_id": assessment.project_id,
            "manifest_text": assessment.manifest_text,
            "manifest_sha256": manifest_text_sha256(assessment.manifest_text) if assessment.manifest_text is not None else None,
        },
    )
    previous = sorted({str(item.get("code")) for item in _json_list(delivery["issues"]) if isinstance(item, dict)})
    await _record(
        session,
        user=user,
        action="import_delivery_revised",
        delivery=delivery,
        summary=f"Delivery {delivery['folder_name']}: corrected (revision {number}); {assessment.state.replace('_', ' ')}",
        after={
            "revision": number,
            "changes": [change.model_dump(mode="json") for change in made],
            "reason": reason,
            "package_sha256": package_sha256,
            "family_id": delivery["family_id"],
            "run_id": delivery["run_id"],
            "state": assessment.state,
            "issues": [issue.code for issue in assessment.issues],
            "previous_state": delivery["state"],
            "previous_issues": previous,
        },
    )
    await session.commit()
    return ImportDeliveryRevisedOut(
        delivery=await _delivery(session, delivery["id"]),
        package=await _package_out(
            session,
            {
                **delivery,
                "package": base_document,
                "issues": [issue.model_dump() for issue in assessment.issues],
            },
        ),
    )
