"""A family's pedigree on the clinical record (SEC-7).

Two records keep a family's pedigree, both append-only and hash-chained per family:

- its structure versions (``family_structure_versions``): one row per change, holding the
  pedigree as it then stood (``snapshot``) and its hash (``structure_hash``), which the
  ranking cache keys on;
- the clinical audit trail (``clinical_audit_events``): each change is a ``pedigree``
  event naming who made it, when, and what changed, before and after; the family's deletion
  is the trail's last event (``family_deleted``), written before the family row goes.

The pedigree is what the segregation, the haplotypes, the carrier screen and the ranking
rest on: the members with their sex, role, affected status and carrier status, whether each
is active, and their relationships (parents, couples, relatives of unknown degree).

The structure-version chain works like the clinical audit trail's (``hash_chain``): under a
per-family advisory lock the writer reads the chain head and stores
``row_hash = H(prev_hash ‖ canonical(content))``. It is partitioned and ordered on the
immutable ``family_identifier`` (the family's own ID, which survives the family row's
deletion; that deletion nulls ``family_id``) and on ``created_at``, then ``id``. The hashed
content leaves out the columns the append-only trigger lets a cascade null (``family_id``,
``created_by``): who made a change is on the clinical audit trail.

What is hashed here, on either chain, hashes the same when it is read back to verify the
chain: the pedigree is read from its tables, so each of its values is one Postgres stored
and gave back, and the rest is text and whole numbers (the reason, the warnings, the counts,
the version and its hash).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .clinical_audit_service import record_clinical_event
from .hash_chain import ChainVerification, chain_row_hash, verify_chain

PEDIGREE_ACTION = "pedigree"
FAMILY_DELETED_ACTION = "family_deleted"

# Where a pedigree change came from, in its audit event's metadata.
FAMILY_CREATED = "family_created"
STRUCTURE_EDIT = "structure_edit"
PACKAGE_IMPORT = "package_import"
SAMPLE_DELETED = "sample_deleted"

# Why a family was deleted, in its last audit event's metadata.
ADMIN_DELETE = "admin_delete"
PEDIGREE_REPLACED = "pedigree_replaced"
IMPORT_FAILED = "import_failed"

_SUMMARY_PREFIX = {
    STRUCTURE_EDIT: "Pedigree changed",
    PACKAGE_IMPORT: "Pedigree changed by a package import",
    SAMPLE_DELETED: "Pedigree changed by deleting a sample with its data",
}

_DELETED_PREFIX = {
    ADMIN_DELETE: "Family deleted with its data",
    PEDIGREE_REPLACED: "Family deleted, to be replaced by the pedigree uploaded with overwrite",
    IMPORT_FAILED: "Family deleted after the package import that created it imported nothing",
}

# A summary names at most this many changes; the event's before and after hold them all.
_MAX_SUMMARY_CHANGES = 5


# ----------------------------------------------------------------------------- the snapshot

_SNAPSHOT_MEMBERS = text(
    """
    SELECT
        s.sample_id,
        s.sex,
        fm.role,
        fm.clinical_status,
        fm.carrier_status,
        fm.carrier_type,
        fm.carrier_evidence,
        fm.active
    FROM family_members fm
    JOIN samples s ON s.id = fm.sample_id
    WHERE fm.family_id = CAST(:family_uuid AS uuid)
    ORDER BY lower(s.sample_id)
    """
)

_SNAPSHOT_RELATIONSHIPS = text(
    """
    SELECT
        fr.relationship_type,
        sa.sample_id AS sample_id_a,
        sb.sample_id AS sample_id_b,
        fr.role_a,
        fr.role_b,
        fr.source,
        fr.metadata,
        fr.active
    FROM family_relationships fr
    JOIN samples sa ON sa.id = fr.sample_id_a
    JOIN samples sb ON sb.id = fr.sample_id_b
    WHERE fr.family_id = CAST(:family_uuid AS uuid)
    ORDER BY fr.relationship_type, lower(sa.sample_id), lower(sb.sample_id), fr.role_a, fr.role_b
    """
)


async def read_structure_snapshot(session: AsyncSession, family_uuid: str) -> dict[str, list[dict[str, Any]]]:
    """The family's pedigree as a structure version records it: every member (an inactive
    one too) and every relationship row (an inactive one too), in a fixed order."""
    member_result = await session.execute(_SNAPSHOT_MEMBERS, {"family_uuid": family_uuid})
    relationship_result = await session.execute(_SNAPSHOT_RELATIONSHIPS, {"family_uuid": family_uuid})
    return {
        "members": [dict(row) for row in member_result.mappings().all()],
        "relationships": [dict(row) for row in relationship_result.mappings().all()],
    }


def snapshot_json(snapshot: Mapping[str, Any]) -> str:
    """The snapshot as it is stored and hashed into ``structure_hash``."""
    return json.dumps(snapshot, sort_keys=True, default=str)


def structure_hash(snapshot: Mapping[str, Any]) -> str:
    """SHA-256 of the stored snapshot: the ranking cache's key for the pedigree."""
    return hashlib.sha256(snapshot_json(snapshot).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------------- the chain


def structure_chain_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    """The immutable content of a structure version that its hash chain binds.

    Leaves out ``family_id`` and ``created_by``, which the ON DELETE SET NULL cascades may
    null, and binds ``family_identifier`` instead. ``created_at`` is rendered the same way
    when the row is written and when it is read to verify the chain.
    """
    created_at = row["created_at"]
    return {
        "id": str(row["id"]),
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at),
        "family_identifier": row.get("family_identifier"),
        "version": row.get("version"),
        "structure_hash": row.get("structure_hash"),
        "snapshot": row.get("snapshot"),
        "metadata": row.get("metadata"),
    }


@dataclass(frozen=True, slots=True)
class StructureVersion:
    """A structure version as it was stored.

    ``deleted_family_versions`` is how many structure versions the families deleted under the
    family ID stored on its chain before this family, of ``deleted_families`` families: 0 for a
    family never made again under its ID. Versions are numbered per family row, so a family
    made again numbers its own from 1 again, and the two numbers name a version apart on the
    ID's chain (ENG-19)."""

    version: int
    structure_hash: str
    row_hash: str
    deleted_family_versions: int = 0
    deleted_families: int = 0


_NEXT_VERSION = text(
    """
    SELECT COALESCE(MAX(version), 0) + 1
    FROM family_structure_versions
    WHERE family_id = CAST(:family_uuid AS uuid)
    """
)

# The versions on a family ID's chain that the families deleted under it stored (their rows
# unlinked from the family row, which the deletion nulls), and how many such families: each
# numbered its own from 1.
_DELETED_FAMILY_VERSIONS = text(
    """
    SELECT COUNT(*) AS versions, COUNT(*) FILTER (WHERE version = 1) AS families
    FROM family_structure_versions
    WHERE family_identifier = :family_identifier
      AND row_hash IS NOT NULL
      AND family_id IS DISTINCT FROM CAST(:family_uuid AS uuid)
    """
)


async def _deleted_family_versions(
    session: AsyncSession, *, family_uuid: str, family_identifier: str
) -> tuple[int, int]:
    """How many structure versions the families deleted under ``family_identifier`` stored on
    its chain before the family ``family_uuid``, and how many families they were."""
    row = (
        await session.execute(
            _DELETED_FAMILY_VERSIONS,
            {"family_uuid": family_uuid, "family_identifier": family_identifier},
        )
    ).mappings().first()
    if row is None or not int(row["versions"] or 0):
        return 0, 0
    # At least one family: one whose first version predates the chain has no version 1 on it.
    return int(row["versions"]), max(int(row["families"] or 0), 1)

_CHAIN_HEAD = text(
    """
    SELECT created_at, row_hash
    FROM family_structure_versions
    WHERE family_identifier = :family_identifier
      AND row_hash IS NOT NULL
    ORDER BY created_at DESC, id DESC
    LIMIT 1
    """
)

_INSERT_VERSION = text(
    """
    INSERT INTO family_structure_versions (
        id,
        family_id,
        family_identifier,
        version,
        structure_hash,
        snapshot,
        metadata,
        created_by,
        created_at,
        row_hash,
        prev_hash
    )
    VALUES (
        CAST(:id AS uuid),
        CAST(:family_uuid AS uuid),
        :family_identifier,
        :version,
        :structure_hash,
        CAST(:snapshot AS jsonb),
        CAST(:metadata AS jsonb),
        CAST(:created_by AS uuid),
        :created_at,
        :row_hash,
        :prev_hash
    )
    """
)


async def lock_family_pedigree(session: AsyncSession, family_identifier: str) -> None:
    """Hold the family's pedigree lock until the transaction ends: one change of a family's
    pedigree at a time. A writer takes it before it reads the pedigree it changes, so the
    pedigree its audit event records as before is the one the change replaced; taken again
    in the same transaction, it is granted at once. A writer that also holds the family's
    variant-write locks takes those first."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"fsv:{family_identifier}"}
    )


async def append_structure_version(
    session: AsyncSession,
    *,
    family_uuid: str,
    family_identifier: str,
    snapshot: Mapping[str, Any],
    metadata: Mapping[str, Any] | None = None,
    created_by: str | None = None,
) -> StructureVersion:
    """Store the family's next structure version, hash-chained (no commit: the caller owns
    the transaction).

    One writer per family at a time, until the transaction ends: the version number, the
    chain head and the insert are read and written under the family's pedigree lock, so
    two changes of a family neither take one number nor fork the chain.
    """
    await lock_family_pedigree(session, family_identifier)
    version = int((await session.execute(_NEXT_VERSION, {"family_uuid": family_uuid})).scalar_one())
    head = (
        await session.execute(_CHAIN_HEAD, {"family_identifier": family_identifier})
    ).mappings().first()
    # An empty chain holds no deleted family's versions.
    deleted_versions, deleted_families = (
        await _deleted_family_versions(session, family_uuid=family_uuid, family_identifier=family_identifier)
        if head is not None
        else (0, 0)
    )
    now = datetime.now(timezone.utc)
    # Strictly increasing per family, so the chain's order is the same on every read.
    if head is not None and head["created_at"] is not None and now <= head["created_at"]:
        created_at = head["created_at"] + timedelta(microseconds=1)
    else:
        created_at = now
    prev_hash = head["row_hash"] if head is not None else None

    version_id = uuid4()
    stored_snapshot = snapshot_json(snapshot)
    stored_metadata = json.dumps(dict(metadata or {}))
    digest = hashlib.sha256(stored_snapshot.encode("utf-8")).hexdigest()
    row_hash = chain_row_hash(
        prev_hash,
        structure_chain_payload(
            {
                "id": version_id,
                "created_at": created_at,
                "family_identifier": family_identifier,
                "version": version,
                "structure_hash": digest,
                # As Postgres gives them back: JSON values, not the Python objects.
                "snapshot": json.loads(stored_snapshot),
                "metadata": json.loads(stored_metadata),
            }
        ),
    )
    await session.execute(
        _INSERT_VERSION,
        {
            "id": str(version_id),
            "family_uuid": family_uuid,
            "family_identifier": family_identifier,
            "version": version,
            "structure_hash": digest,
            "snapshot": stored_snapshot,
            "metadata": stored_metadata,
            "created_by": created_by,
            "created_at": created_at,
            "row_hash": row_hash,
            "prev_hash": prev_hash,
        },
    )
    return StructureVersion(
        version=version,
        structure_hash=digest,
        row_hash=row_hash,
        deleted_family_versions=deleted_versions,
        deleted_families=deleted_families,
    )


async def verify_family_structure_chain(
    session: AsyncSession, family_identifier: str | None
) -> ChainVerification:
    """Walk a family's structure-version chain and say whether it is intact.

    Partitioned on the immutable ``family_identifier``, so the chain of a family whose row
    was deleted can still be walked. Versions written before the chain (no ``row_hash``)
    are not part of it.
    """
    rows = (
        await session.execute(
            text(
                "SELECT id::text AS id, created_at, family_identifier, version, structure_hash, "
                "snapshot, metadata, row_hash, prev_hash "
                "FROM family_structure_versions "
                "WHERE family_identifier IS NOT DISTINCT FROM :family_identifier "
                "AND row_hash IS NOT NULL "
                "ORDER BY created_at ASC, id ASC"
            ),
            {"family_identifier": family_identifier},
        )
    ).mappings().all()
    return verify_chain([dict(row) for row in rows], structure_chain_payload)


# ------------------------------------------------------------------------- the audited pedigree


def _json_object(value: Any) -> dict[str, Any]:
    """A stored JSON object as a dict (JSON text is parsed); anything else is empty."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _optional_text(value: Any) -> str | None:
    text_value = str(value).strip() if value is not None else ""
    return text_value or None


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def pedigree_state(
    members: Iterable[Mapping[str, Any]], relationships: Iterable[Mapping[str, Any]]
) -> dict[str, Any]:
    """The pedigree an audit event records: each member by sample ID, with its sex, role,
    affected status, carrier status, carrier type and evidence, whether it is active and its
    parents; and the couples and the relatives of unknown degree.

    ``members`` and ``relationships`` are rows as the snapshot reads them; a relationship
    row that is not active is left out (a row without ``active`` is an active one).
    """
    state_members: dict[str, dict[str, Any]] = {}
    for row in members:
        sample_id = str(row["sample_id"])
        state_members[sample_id] = {
            "sample_id": sample_id,
            "sex": _optional_text(row.get("sex")),
            "role": _optional_text(row.get("role")),
            "clinical_status": _optional_text(row.get("clinical_status")),
            "carrier_status": _optional_text(row.get("carrier_status")),
            "carrier_type": _optional_text(row.get("carrier_type")),
            "carrier_evidence": _json_object(row.get("carrier_evidence")),
            "active": bool(row.get("active", True)),
            "father": None,
            "mother": None,
            "parents": [],
        }
    couples: list[dict[str, Any]] = []
    relatives: list[dict[str, Any]] = []
    for relationship in relationships:
        if not relationship.get("active", True):
            continue
        kind = relationship.get("relationship_type")
        sample_a = str(relationship.get("sample_id_a"))
        sample_b = str(relationship.get("sample_id_b"))
        metadata = _json_object(relationship.get("metadata"))
        if kind == "parent_child":
            child = state_members.get(sample_b)
            if child is None:
                continue
            role = relationship.get("role_a")
            if role in ("father", "mother") and child[str(role)] is None:
                child[str(role)] = sample_a
            else:
                # A parent recorded without a sex-specific role ("parent").
                child["parents"].append(sample_a)
        elif kind == "couple":
            couples.append({"partners": sorted([sample_a, sample_b]), "metadata": metadata})
        elif kind == "relative":
            # sample_id_a is the member the link goes through, sample_id_b the relative.
            relatives.append({"member": sample_b, "related_to": sample_a, "metadata": metadata})
    for member in state_members.values():
        member["parents"].sort()
    return {
        "members": state_members,
        "couples": sorted(couples, key=_canonical),
        "relatives": sorted(relatives, key=_canonical),
    }


def snapshot_pedigree_state(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """The audited pedigree of a structure snapshot (``read_structure_snapshot``)."""
    return pedigree_state(snapshot.get("members") or [], snapshot.get("relationships") or [])


# The member fields a change names, with the words it names them by. The sample ID is
# named as a rename, and whether the member is active as made inactive or active again.
_MEMBER_FIELDS: tuple[tuple[str, str], ...] = (
    ("role", "role"),
    ("sex", "sex"),
    ("clinical_status", "affected status"),
    ("carrier_status", "carrier status"),
    ("carrier_type", "carrier type"),
    ("carrier_evidence", "carrier evidence"),
    ("father", "father"),
    ("mother", "mother"),
    ("parents", "other parents"),
)

_REFERENCE_FIELDS = ("father", "mother")


def _shown(field_name: str, value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) or "none"
    if value is None or value == "":
        return "none"
    if field_name == "sex" and value == "und":
        return "unknown"
    return str(value)


def _described(member: Mapping[str, Any]) -> str:
    """A member's role, sex and affected status, as a new member is named."""
    sex = member.get("sex")
    status = member.get("clinical_status")
    return ", ".join(
        [
            str(member.get("role") or "no role"),
            "sex unknown" if sex in (None, "", "und") else str(sex),
            "affected status unknown" if status in (None, "", "unknown") else str(status),
        ]
    )


def _member_change(old: Mapping[str, Any], new: Mapping[str, Any], old_as_new: Mapping[str, Any]) -> str | None:
    """The phrase naming what changed of one member, or None when nothing did.

    ``old_as_new`` is the old state with the IDs of renamed members it refers to (as a
    parent) given their new IDs, so that a parent's rename is not counted as a change of
    the child's parents.
    """
    renamed = old["sample_id"] != new["sample_id"]
    head = f"{old['sample_id']} renamed {new['sample_id']}" if renamed else str(new["sample_id"])
    if old["active"] != new["active"]:
        head = f"{head} {'made active again' if new['active'] else 'made inactive'}"
    parts: list[str] = []
    for field_name, label in _MEMBER_FIELDS:
        was, now = old_as_new.get(field_name), new.get(field_name)
        if was == now:
            continue
        if field_name == "carrier_evidence":
            parts.append(
                "carrier evidence added" if not was else "carrier evidence removed" if not now
                else "carrier evidence edited"
            )
            continue
        # The recorded old value, under its old name.
        parts.append(f"{label} {_shown(field_name, old.get(field_name))} → {_shown(field_name, now)}")
    if not parts and not renamed and old["active"] == new["active"]:
        return None
    return f"{head}: {', '.join(parts)}" if parts else head


def _with_ids(value: Any, ids: Mapping[str, str]) -> Any:
    """A sample ID, or a list of them, each under the ID ``ids`` maps it to."""
    if isinstance(value, list):
        return sorted(ids.get(str(item), str(item)) for item in value)
    if isinstance(value, str):
        return ids.get(value, value)
    return value


def _member_with_ids(member: Mapping[str, Any], ids: Mapping[str, str]) -> dict[str, Any]:
    """A member whose references to other members (its parents) are under the mapped IDs."""
    mapped = dict(member)
    for field_name in (*_REFERENCE_FIELDS, "parents"):
        mapped[field_name] = _with_ids(member.get(field_name), ids)
    return mapped


def _links_with_ids(
    links: Iterable[Mapping[str, Any]], keys: tuple[str, ...], ids: Mapping[str, str]
) -> list[dict[str, Any]]:
    mapped: list[dict[str, Any]] = []
    for link in links:
        entry = dict(link)
        for key in keys:
            entry[key] = _with_ids(link.get(key), ids)
        mapped.append(entry)
    return sorted(mapped, key=_canonical)


def pedigree_before_renames(state: Mapping[str, Any], renamed: Mapping[str, str]) -> dict[str, Any]:
    """The audited pedigree as it was before the renames ``renamed`` (each renamed member's
    new sample ID mapped to its old one) made earlier in the same change: each renamed
    member, and every reference to one, under its old ID."""
    old_id = dict(renamed)
    members: dict[str, Any] = {}
    for sample_id, member in (state.get("members") or {}).items():
        restored = _member_with_ids(member, old_id)
        restored["sample_id"] = old_id.get(sample_id, sample_id)
        members[restored["sample_id"]] = restored
    return {
        "members": members,
        "couples": _links_with_ids(state.get("couples") or [], ("partners",), old_id),
        "relatives": _links_with_ids(state.get("relatives") or [], ("member", "related_to"), old_id),
    }


def _link_changes(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    *,
    key_of: Callable[[dict[str, Any]], tuple[str, ...]],
    name_of: Callable[[dict[str, Any]], str],
) -> list[str]:
    """One phrase per couple or relative link added, removed or changed."""
    old = {key_of(link): link for link in before}
    new = {key_of(link): link for link in after}
    phrases: list[str] = []
    for key in sorted(old.keys() | new.keys()):
        was, now = old.get(key), new.get(key)
        if was == now:
            continue
        verb = "added" if was is None else "removed" if now is None else "changed"
        named = now if now is not None else was
        assert named is not None
        phrases.append(f"{name_of(named)} {verb}")
    return phrases


def diff_pedigree(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    renamed: Mapping[str, str] | None = None,
) -> tuple[list[str], dict[str, Any], dict[str, Any]]:
    """What changed between two audited pedigrees (``pedigree_state``).

    Returns one phrase per change, and what changed as it was and as it is: the members
    that changed, under their sample ID now (a member that is gone under its last one;
    None where there is no member), and the couples or the relatives when any of them
    changed. ``renamed`` maps a member's new sample ID to its old one, for the members a
    change renamed: such a member is one member whose ID changed, not one gone and one new.
    """
    renamed = dict(renamed or {})
    before_members: Mapping[str, Any] = before.get("members") or {}
    after_members: Mapping[str, Any] = after.get("members") or {}
    renamed = {new: old for new, old in renamed.items() if new in after_members and old in before_members}
    new_id = {old: new for new, old in renamed.items()}

    phrases: list[str] = []
    members_before: dict[str, Any] = {}
    members_after: dict[str, Any] = {}
    matched: set[str] = set()
    for sample_id in sorted(after_members, key=str.lower):
        now = after_members[sample_id]
        old_id = renamed.get(sample_id, sample_id if sample_id not in new_id else None)
        was = before_members.get(old_id) if old_id is not None else None
        if was is None:
            members_before[sample_id] = None
            members_after[sample_id] = now
            phrases.append(f"{sample_id} added ({_described(now)})")
            continue
        matched.add(str(old_id))
        phrase = _member_change(was, now, _member_with_ids(was, new_id))
        if phrase is None:
            continue
        members_before[sample_id] = was
        members_after[sample_id] = now
        phrases.append(phrase)
    for sample_id in sorted(before_members, key=str.lower):
        if sample_id in matched:
            continue
        members_before[sample_id] = before_members[sample_id]
        members_after[sample_id] = None
        phrases.append(f"{sample_id} deleted")

    changed_before: dict[str, Any] = {}
    changed_after: dict[str, Any] = {}
    if members_before or members_after:
        changed_before["members"] = members_before
        changed_after["members"] = members_after

    couples_before = _links_with_ids(before.get("couples") or [], ("partners",), new_id)
    couples_after = list(after.get("couples") or [])
    if couples_before != couples_after:
        changed_before["couples"] = list(before.get("couples") or [])
        changed_after["couples"] = couples_after
        phrases.extend(
            _link_changes(
                couples_before,
                couples_after,
                key_of=lambda link: tuple(str(partner) for partner in link["partners"]),
                name_of=lambda link: f"couple {' + '.join(str(partner) for partner in link['partners'])}",
            )
        )

    relatives_before = _links_with_ids(before.get("relatives") or [], ("member", "related_to"), new_id)
    relatives_after = list(after.get("relatives") or [])
    if relatives_before != relatives_after:
        changed_before["relatives"] = list(before.get("relatives") or [])
        changed_after["relatives"] = relatives_after
        phrases.extend(
            _link_changes(
                relatives_before,
                relatives_after,
                key_of=lambda link: (str(link["member"]), str(link["related_to"])),
                name_of=lambda link: f"relative {link['member']} (through {link['related_to']})",
            )
        )
    return phrases, changed_before, changed_after


def _bounded(phrases: list[str]) -> str:
    if len(phrases) > _MAX_SUMMARY_CHANGES:
        hidden = len(phrases) - _MAX_SUMMARY_CHANGES
        phrases = [*phrases[:_MAX_SUMMARY_CHANGES], f"and {hidden} more"]
    return "; ".join(phrases)


def pedigree_recorded_summary(state: Mapping[str, Any]) -> str:
    """The summary of a new family's pedigree: its members with their role, sex and
    affected status."""
    members: Mapping[str, Any] = state.get("members") or {}
    named = [f"{sample_id} ({_described(members[sample_id])})" for sample_id in sorted(members, key=str.lower)]
    count = len(named)
    return f"Pedigree recorded with {count} member{'' if count == 1 else 's'}: {_bounded(named) or 'none'}"


def pedigree_changed_summary(source: str, phrases: list[str]) -> str:
    """The summary of a change of the pedigree: what changed, five changes at most."""
    return f"{_SUMMARY_PREFIX.get(source, 'Pedigree changed')}: {_bounded(phrases)}"


def made_again_note(versions: int, families: int) -> str:
    """What the pedigree of a family made again under the ID of a deleted family adds to its
    summary: its structure versions start again at 1, after the deleted families' on the ID's
    chain (ENG-19)."""
    deleted = "a deleted family" if families <= 1 else f"{families} deleted families"
    return (
        f"Made again under this family ID, after {versions} structure version"
        f"{'' if versions == 1 else 's'} of {deleted}: its own start again at 1."
    )


# ---------------------------------------------------------------------------- the audit event


def audit_actor(user: Any) -> str:
    """The name the audit trail records for a user, as for every clinical audit event."""
    return getattr(user, "username", None) or getattr(user, "email", "") or "unknown"


@dataclass(frozen=True, slots=True)
class PedigreeChange:
    """A change of a family's pedigree, for its clinical audit event.

    ``before`` is the audited pedigree before the change (``pedigree_state``), None for a
    new family. ``renamed`` maps the new sample ID of each member the change renamed to its
    old one. ``reason`` is the reason the change was made with, when it gave one.
    """

    source: str
    actor: str
    actor_id: str | None = None
    before: Mapping[str, Any] | None = None
    renamed: Mapping[str, str] = field(default_factory=dict)
    reason: str | None = None


def pedigree_change_by(
    user: Any,
    *,
    source: str,
    before: Mapping[str, Any] | None = None,
    renamed: Mapping[str, str] | None = None,
    reason: str | None = None,
) -> PedigreeChange:
    """A pedigree change made by ``user``."""
    actor_id = getattr(user, "id", None)
    return PedigreeChange(
        source=source,
        actor=audit_actor(user),
        actor_id=str(actor_id) if actor_id is not None else None,
        before=before,
        renamed=dict(renamed or {}),
        reason=_optional_text(reason),
    )


async def record_pedigree_change(
    session: AsyncSession,
    *,
    family_uuid: str,
    family_identifier: str,
    change: PedigreeChange,
    after: Mapping[str, Any],
    version: StructureVersion,
) -> bool:
    """Record a pedigree change on the family's clinical audit trail, in the transaction of
    the change (no commit). Returns whether an event was written: a new family's pedigree
    always is; a change that left the audited pedigree as it was writes nothing."""
    if change.before is None:
        summary = pedigree_recorded_summary(after)
        if version.deleted_family_versions:
            summary = f"{summary}. {made_again_note(version.deleted_family_versions, version.deleted_families)}"
        before_value: dict[str, Any] | None = None
        after_value: dict[str, Any] = dict(after)
    else:
        phrases, before_value, after_value = diff_pedigree(change.before, after, change.renamed)
        if not phrases:
            return False
        summary = pedigree_changed_summary(change.source, phrases)
    metadata: dict[str, Any] = {
        "source": change.source,
        "structure_version": version.version,
        "structure_hash": version.structure_hash,
    }
    if version.deleted_family_versions:
        # The version number repeats one of a deleted family's on the ID's trail: this names
        # it apart (ENG-19).
        metadata["deleted_family_versions"] = version.deleted_family_versions
    if change.reason:
        metadata["reason"] = change.reason
    await record_clinical_event(
        session,
        family_uuid=family_uuid,
        family_identifier=family_identifier,
        variant_id=None,
        actor=change.actor,
        actor_id=change.actor_id,
        action=PEDIGREE_ACTION,
        summary=summary,
        before=before_value,
        after=after_value,
        metadata=metadata,
    )
    return True


async def record_structure_version(
    session: AsyncSession,
    *,
    family_uuid: str,
    family_identifier: str,
    created_by: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    change: PedigreeChange | None = None,
    snapshot: Mapping[str, Any] | None = None,
) -> StructureVersion:
    """Store the family's pedigree as its next structure version and, given the change that
    led to it, record that change on the clinical audit trail (no commit).

    ``snapshot`` is the pedigree as read after the change, under the family's pedigree lock;
    it is read here, under the lock, when not given.
    """
    await lock_family_pedigree(session, family_identifier)
    stored = snapshot if snapshot is not None else await read_structure_snapshot(session, family_uuid)
    version = await append_structure_version(
        session,
        family_uuid=family_uuid,
        family_identifier=family_identifier,
        snapshot=stored,
        metadata=metadata,
        created_by=created_by,
    )
    if change is not None:
        await record_pedigree_change(
            session,
            family_uuid=family_uuid,
            family_identifier=family_identifier,
            change=change,
            after=snapshot_pedigree_state(stored),
            version=version,
        )
    return version


async def record_structure_change(
    session: AsyncSession,
    *,
    family_uuid: str,
    family_identifier: str,
    before_snapshot: Mapping[str, Any],
    user: Any,
    source: str,
    metadata: Mapping[str, Any] | None = None,
) -> StructureVersion | None:
    """After a writer other than the structure editor changed a family's members or
    relationships (an import, a sample's deletion): when the pedigree differs from
    ``before_snapshot``, store it as the next structure version and record the change on
    the clinical audit trail; when it does not, write nothing (no commit)."""
    await lock_family_pedigree(session, family_identifier)
    after_snapshot = await read_structure_snapshot(session, family_uuid)
    if structure_hash(after_snapshot) == structure_hash(before_snapshot):
        return None
    created_by = getattr(user, "id", None)
    return await record_structure_version(
        session,
        family_uuid=family_uuid,
        family_identifier=family_identifier,
        created_by=str(created_by) if created_by is not None else None,
        metadata={"reason": source, **dict(metadata or {})},
        change=pedigree_change_by(user, source=source, before=snapshot_pedigree_state(before_snapshot)),
        snapshot=after_snapshot,
    )


# ------------------------------------------------------------------------- the family's deletion

_FAMILY_SAMPLES = text(
    """
    SELECT sample_id
    FROM samples
    WHERE family_id = CAST(:family_uuid AS uuid)
    ORDER BY lower(sample_id)
    """
)

_LATEST_STRUCTURE_VERSION = text(
    """
    SELECT MAX(version)
    FROM family_structure_versions
    WHERE family_id = CAST(:family_uuid AS uuid)
    """
)

# What the family holds in Postgres that its deletion takes with it: the reviews, the repeat
# calls, the Paraphase results, the interval tracks' source rows and the raw files.
_FAMILY_POSTGRES_DATA = text(
    """
    SELECT 'small_variant_reviews' AS kind, COUNT(*)::bigint AS count
    FROM small_variant_reviews WHERE family_id = CAST(:family_uuid AS uuid)
    UNION ALL
    SELECT 'structural_variant_reviews', COUNT(*)::bigint
    FROM structural_variant_reviews WHERE family_id = CAST(:family_uuid AS uuid)
    UNION ALL
    SELECT 'repeat_expansions', COUNT(*)::bigint
    FROM repeat_expansions WHERE family_id = CAST(:family_uuid AS uuid)
    UNION ALL
    SELECT 'paraphase_results', COUNT(*)::bigint
    FROM sample_paraphase_results WHERE family_id = CAST(:family_uuid AS uuid)
    UNION ALL
    SELECT 'interval_track_sources', COUNT(*)::bigint
    FROM sample_interval_track_sources WHERE family_id = CAST(:family_uuid AS uuid)
    UNION ALL
    SELECT 'raw_import_files', COUNT(*)::bigint
    FROM raw_import_files WHERE family_id = CAST(:family_uuid AS uuid)
    """
)

# The counts a deletion's summary names, with the words it names them by.
_DELETED_NAMED: tuple[tuple[str, str], ...] = (
    ("small_variants", "small variants"),
    ("structural_variants", "structural variants"),
    ("reviews", "variant reviews"),
)


def family_deleted_summary(source: str, samples: list[str], data: Mapping[str, int]) -> str:
    """The summary of a family's deletion: its samples, five by name at most, and how many
    variants and variant reviews went with it."""
    count = len(samples)
    named = ", ".join(samples[:_MAX_SUMMARY_CHANGES])
    if count > _MAX_SUMMARY_CHANGES:
        named = f"{named} and {count - _MAX_SUMMARY_CHANGES} more"
    parts = [f"{count} sample{'' if count == 1 else 's'}" + (f" ({named})" if named else "")]
    for key, label in _DELETED_NAMED:
        if key in data:
            parts.append(f"{data[key]} {label}")
    return f"{_DELETED_PREFIX.get(source, 'Family deleted')}: {', '.join(parts)}"


async def record_family_deleted(
    session: AsyncSession,
    *,
    family_uuid: str,
    family_identifier: str,
    user: Any,
    source: str,
    variant_counts: Mapping[str, int] | None = None,
    reason: str | None = None,
) -> None:
    """Record a family's deletion as the last event of its clinical audit trail (SEC-7), in
    the transaction of the deletion and before the family row goes (no commit).

    The event names who deleted the family and when, and what went with it: its samples,
    its latest structure version, the variant reviews and other data Postgres held of it
    and, given ``variant_counts``, the variants counted in ClickHouse. A deletion that fails
    rolls the event back with it. Afterwards the family's chains (its clinical audit events,
    sign-outs and structure versions) stay, unlinked from the family row, verifiable by its ID.
    """
    await lock_family_pedigree(session, family_identifier)
    sample_rows = (await session.execute(_FAMILY_SAMPLES, {"family_uuid": family_uuid})).all()
    samples = [str(row[0]) for row in sample_rows]
    latest = (await session.execute(_LATEST_STRUCTURE_VERSION, {"family_uuid": family_uuid})).scalar_one_or_none()
    deleted_versions, _families = (
        await _deleted_family_versions(session, family_uuid=family_uuid, family_identifier=family_identifier)
        if latest is not None
        else (0, 0)
    )
    held_rows = (await session.execute(_FAMILY_POSTGRES_DATA, {"family_uuid": family_uuid})).all()
    held = {str(row[0]): int(row[1] or 0) for row in held_rows}
    data: dict[str, int] = {key: int(value) for key, value in (variant_counts or {}).items()}
    data.update(held)
    data["reviews"] = held.get("small_variant_reviews", 0) + held.get("structural_variant_reviews", 0)
    metadata: dict[str, Any] = {"source": source}
    stated_reason = _optional_text(reason)
    if stated_reason:
        metadata["reason"] = stated_reason
    before: dict[str, Any] = {
        "samples": samples,
        "structure_version": int(latest) if latest is not None else None,
        "data": data,
    }
    if deleted_versions:
        # A family made again under the ID: its latest version named apart (ENG-19).
        before["deleted_family_versions"] = deleted_versions
    actor_id = getattr(user, "id", None)
    await record_clinical_event(
        session,
        family_uuid=family_uuid,
        family_identifier=family_identifier,
        variant_id=None,
        actor=audit_actor(user),
        actor_id=str(actor_id) if actor_id is not None else None,
        action=FAMILY_DELETED_ACTION,
        summary=family_deleted_summary(source, samples, data)
        + (f". Reason: {stated_reason}" if stated_reason else ""),
        before=before,
        after=None,
        metadata=metadata,
    )
