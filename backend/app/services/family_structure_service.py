from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from fastapi import HTTPException
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas import (
    FamilyStructureCoupleUpdate,
    FamilyStructureMemberCreate,
    FamilyStructureParentChildUpdate,
    FamilyStructureRelativeUpdate,
    FamilyStructureUpdate,
    FamilyStructureUpdateOut,
)
from .clickhouse_interval_tracks import (
    delete_interval_track_sources,
    delete_interval_tracks,
)
from .clickhouse_variant_storage import (
    count_family_small_variants,
    count_family_structural_variants,
    delete_family_small_variants,
    delete_family_structural_variants,
)
from .family_variant_write_lock import VARIANT_TYPES, lock_family_variant_writes
from .metadata_service import get_accessible_family_mapping, get_family_record
from .access_control import CurrentUser
from .ped_service import _record_family_structure_version, _require_storable_id

CLINICAL_STATUS_VALUES = {"unknown", "unaffected", "affected"}
CARRIER_STATUS_VALUES = {"unknown", "not_carrier", "carrier"}
MEMBER_ROLES = {"proband", "father", "mother", "sibling", "embryo", "relative"}
SEX_VALUES = {"male", "female", "und"}
INTERVAL_TRACK_TYPES = ("coverage", "segments", "apcad", "apcad_pcf", "haplotype", "target_coverage")


def _clean_sample_id(value: str, *, field_name: str = "sample_id") -> str:
    sample_id = str(value or "").strip()
    if not sample_id:
        raise HTTPException(status_code=400, detail=f"{field_name} is required")
    return sample_id


def _sample_key(sample_id: str) -> str:
    return sample_id.strip().lower()


def _phenotype_code(clinical_status: str) -> str:
    if clinical_status == "affected":
        return "2"
    if clinical_status == "unaffected":
        return "1"
    return "0"


def _sex_code(sex: str) -> str:
    if sex == "male":
        return "1"
    if sex == "female":
        return "2"
    return "0"


def _normalize_carrier_state(
    carrier_status: str | None,
    carrier_type: str | None,
) -> tuple[str, str | None]:
    status = (carrier_status or "unknown").strip().lower()
    if status not in CARRIER_STATUS_VALUES:
        status = "unknown"
    carrier_type_value = (carrier_type or "").strip().lower() or None
    if carrier_type_value and status != "carrier":
        status = "carrier"
    if status != "carrier":
        carrier_type_value = None
    return status, carrier_type_value


async def _fetch_current_structure_version(
    session: AsyncSession,
    *,
    family_uuid: str,
) -> int:
    result = await session.execute(
        text(
            """
            SELECT COALESCE(MAX(version), 0)
            FROM family_structure_versions
            WHERE family_id = CAST(:family_uuid AS uuid)
            """
        ),
        {"family_uuid": family_uuid},
    )
    return int(result.scalar_one() or 0)


async def _fetch_family_member_rows(
    session: AsyncSession,
    *,
    family_uuid: str,
) -> dict[str, dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT
                fm.family_id::text AS family_uuid,
                s.id::text AS sample_uuid,
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
        ),
        {"family_uuid": family_uuid},
    )
    return {
        _sample_key(row["sample_id"]): dict(row)
        for row in result.mappings().all()
    }


async def _fetch_current_relationship_rows(
    session: AsyncSession,
    *,
    family_uuid: str,
) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT
                fr.relationship_type,
                sa.sample_id AS sample_id_a,
                sb.sample_id AS sample_id_b,
                fr.role_a,
                fr.role_b,
                fr.source,
                fr.metadata
            FROM family_relationships fr
            JOIN samples sa ON sa.id = fr.sample_id_a
            JOIN samples sb ON sb.id = fr.sample_id_b
            WHERE fr.family_id = CAST(:family_uuid AS uuid)
              AND fr.active
            ORDER BY fr.relationship_type, lower(sa.sample_id), lower(sb.sample_id)
            """
        ),
        {"family_uuid": family_uuid},
    )
    return [dict(row) for row in result.mappings().all()]


async def _family_assembly_groups(
    session: AsyncSession,
    *,
    family_uuid: str,
) -> dict[str, list[str]]:
    result = await session.execute(
        text(
            """
            SELECT
                a.assembly_name,
                fp.project_id::text AS project_id
            FROM family_projects fp
            JOIN projects p ON p.id = fp.project_id
            JOIN assemblies a ON a.id = p.assembly_id
            WHERE fp.family_id = CAST(:family_uuid AS uuid)
            ORDER BY a.assembly_name, fp.project_id
            """
        ),
        {"family_uuid": family_uuid},
    )
    groups: dict[str, list[str]] = defaultdict(list)
    for row in result.mappings().all():
        assembly_name = str(row["assembly_name"] or "").strip()
        project_id = str(row["project_id"] or "").strip()
        if assembly_name and project_id:
            groups[assembly_name].append(project_id)
    return groups


async def _family_postgres_data_counts(
    session: AsyncSession,
    *,
    family_uuid: str,
) -> dict[str, int]:
    result = await session.execute(
        text(
            """
            SELECT 'repeat_expansions' AS data_type, COUNT(*)::bigint AS count
            FROM repeat_expansions
            WHERE family_id = CAST(:family_uuid AS uuid)
            UNION ALL
            SELECT 'paraphase', COUNT(*)::bigint
            FROM sample_paraphase_results
            WHERE family_id = CAST(:family_uuid AS uuid)
            UNION ALL
            SELECT 'small_variant_reviews', COUNT(*)::bigint
            FROM small_variant_reviews
            WHERE family_id = CAST(:family_uuid AS uuid)
            UNION ALL
            SELECT 'structural_variant_reviews', COUNT(*)::bigint
            FROM structural_variant_reviews
            WHERE family_id = CAST(:family_uuid AS uuid)
            UNION ALL
            SELECT track_type, COALESCE(SUM(row_count), 0)::bigint
            FROM sample_interval_track_sources
            WHERE family_id = CAST(:family_uuid AS uuid)
            GROUP BY track_type
            """
        ),
        {"family_uuid": family_uuid},
    )
    counts = {track_type: 0 for track_type in INTERVAL_TRACK_TYPES}
    counts.update(
        {
            "small_variants": 0,
            "structural_variants": 0,
            "repeat_expansions": 0,
            "paraphase": 0,
            "small_variant_reviews": 0,
            "structural_variant_reviews": 0,
        }
    )
    for row in result.mappings().all():
        counts[str(row["data_type"])] = int(row["count"] or 0)
    return counts


async def _family_genomic_data_counts(
    session: AsyncSession,
    *,
    family_uuid: str,
) -> dict[str, int]:
    counts = await _family_postgres_data_counts(session, family_uuid=family_uuid)
    for assembly_name, project_ids in (await _family_assembly_groups(session, family_uuid=family_uuid)).items():
        counts["small_variants"] += await count_family_small_variants(
            assembly_name,
            family_uuid,
            project_ids=project_ids,
        )
        counts["structural_variants"] += await count_family_structural_variants(
            assembly_name,
            family_uuid,
            project_ids=project_ids,
        )
    return counts


async def _clear_family_genomic_data(
    session: AsyncSession,
    *,
    family_uuid: str,
) -> dict[str, int]:
    # Until the structure change commits, so no write that read the family's variants
    # before the clear writes them back after it.
    await lock_family_variant_writes(session, family_uuid, VARIANT_TYPES)
    counts = await _family_genomic_data_counts(session, family_uuid=family_uuid)
    assembly_groups = await _family_assembly_groups(session, family_uuid=family_uuid)
    for assembly_name in assembly_groups:
        await delete_family_small_variants(assembly_name, family_uuid)
        await delete_family_structural_variants(assembly_name, family_uuid)
        await delete_interval_tracks(assembly_name, family_uuid=family_uuid)
    await delete_interval_track_sources(session, family_uuid=family_uuid)
    for table_name in (
        "repeat_expansions",
        "sample_paraphase_results",
        "small_variant_reviews",
        "structural_variant_reviews",
    ):
        await session.execute(
            text(
                f"""
                DELETE FROM {table_name}
                WHERE family_id = CAST(:family_uuid AS uuid)
                """
            ),
            {"family_uuid": family_uuid},
        )
    return counts


async def _sample_id_conflicts(
    session: AsyncSession,
    *,
    sample_ids: list[str],
) -> list[dict[str, Any]]:
    if not sample_ids:
        return []
    result = await session.execute(
        text(
            """
            SELECT sample_id, family_id::text AS family_uuid
            FROM samples
            WHERE lower(sample_id) IN :sample_ids
            """
        ).bindparams(bindparam("sample_ids", expanding=True)),
        {"sample_ids": [_sample_key(sample_id) for sample_id in sample_ids]},
    )
    return [dict(row) for row in result.mappings().all()]


def _member_payload(
    member: FamilyStructureMemberCreate,
    *,
    family_uuid: str,
) -> dict[str, Any]:
    carrier_status, carrier_type = _normalize_carrier_state(
        member.carrier_status,
        member.carrier_type,
    )
    # The ID the new member is stored under, refused (400) before it is looked up or
    # written if CoGA cannot store it.
    sample_id = _clean_sample_id(member.sample_id)
    _require_storable_id(sample_id, kind="Sample ID")
    return {
        "family_uuid": family_uuid,
        "sample_uuid": None,
        "sample_id": sample_id,
        "sex": member.sex,
        "role": member.role,
        "clinical_status": member.clinical_status,
        "carrier_status": carrier_status,
        "carrier_type": carrier_type,
        "carrier_evidence": dict(member.carrier_evidence or {}),
        "active": True,
        "is_new": True,
    }


def _apply_member_update(
    target: dict[str, Any],
    update_fields: dict[str, Any],
) -> tuple[bool, bool]:
    status_changed = False
    structure_changed = False
    if "sex" in update_fields and update_fields["sex"] is not None:
        if update_fields["sex"] not in SEX_VALUES:
            raise HTTPException(status_code=400, detail="Invalid sex value")
        structure_changed = structure_changed or target.get("sex") != update_fields["sex"]
        target["sex"] = update_fields["sex"]
    if "role" in update_fields and update_fields["role"] is not None:
        if update_fields["role"] not in MEMBER_ROLES:
            raise HTTPException(status_code=400, detail="Invalid family member role")
        structure_changed = structure_changed or target.get("role") != update_fields["role"]
        target["role"] = update_fields["role"]
    if "clinical_status" in update_fields and update_fields["clinical_status"] is not None:
        if update_fields["clinical_status"] not in CLINICAL_STATUS_VALUES:
            raise HTTPException(status_code=400, detail="Invalid clinical status")
        status_changed = status_changed or target.get("clinical_status") != update_fields["clinical_status"]
        target["clinical_status"] = update_fields["clinical_status"]
    if "carrier_status" in update_fields or "carrier_type" in update_fields:
        carrier_status = update_fields.get("carrier_status", target.get("carrier_status"))
        carrier_type = update_fields.get("carrier_type", target.get("carrier_type"))
        if "carrier_status" in update_fields and carrier_status != "carrier" and "carrier_type" not in update_fields:
            carrier_type = None
        normalized_status, normalized_type = _normalize_carrier_state(carrier_status, carrier_type)
        status_changed = status_changed or target.get("carrier_status") != normalized_status
        status_changed = status_changed or target.get("carrier_type") != normalized_type
        target["carrier_status"] = normalized_status
        target["carrier_type"] = normalized_type
    if "carrier_evidence" in update_fields and update_fields["carrier_evidence"] is not None:
        evidence = dict(update_fields["carrier_evidence"] or {})
        status_changed = status_changed or target.get("carrier_evidence") != evidence
        target["carrier_evidence"] = evidence
    if "active" in update_fields and update_fields["active"] is not None:
        structure_changed = structure_changed or bool(target.get("active")) != bool(update_fields["active"])
        target["active"] = bool(update_fields["active"])
    return status_changed, structure_changed


def _validate_member_updates(
    *,
    add_members: list[FamilyStructureMemberCreate],
    updates: list[dict[str, Any]],
    remove_members: list[str],
) -> None:
    seen_adds: set[str] = set()
    for member in add_members:
        key = _sample_key(_clean_sample_id(member.sample_id))
        if key in seen_adds:
            raise HTTPException(status_code=400, detail="Added member sample ids must be unique")
        seen_adds.add(key)
    seen_updates: set[str] = set()
    for update in updates:
        key = _sample_key(_clean_sample_id(update["sample_id"]))
        if key in seen_updates:
            raise HTTPException(status_code=400, detail="Member updates must be unique by sample id")
        if key in seen_adds:
            raise HTTPException(status_code=400, detail="Do not add and update the same member in one request")
        seen_updates.add(key)
    seen_removals: set[str] = set()
    for sample_id in remove_members:
        key = _sample_key(_clean_sample_id(sample_id, field_name="remove_members"))
        if key in seen_removals:
            raise HTTPException(status_code=400, detail="Removed member sample ids must be unique")
        if key in seen_adds:
            raise HTTPException(status_code=400, detail="Do not add and remove the same member in one request")
        seen_removals.add(key)


def _validate_parent_role(
    *,
    parent: str,
    child: str,
    parent_role: str,
    target_members: dict[str, dict[str, Any]],
) -> None:
    parent_row = target_members[_sample_key(parent)]
    if parent_role == "father" and parent_row.get("sex") == "female":
        raise HTTPException(
            status_code=400,
            detail=f"{parent} cannot be recorded as father because the member sex is female",
        )
    if parent_role == "mother" and parent_row.get("sex") == "male":
        raise HTTPException(
            status_code=400,
            detail=f"{parent} cannot be recorded as mother because the member sex is male",
        )
    if _sample_key(parent) == _sample_key(child):
        raise HTTPException(status_code=400, detail="A member cannot be their own parent")


def _relationship_rows_from_payload(
    relationships: FamilyStructureUpdate,
    *,
    target_members: dict[str, dict[str, Any]],
    current_relationships: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if relationships.relationships is None:
        raise ValueError("relationships payload is required")
    rows: list[dict[str, Any]] = []
    for relationship in relationships.relationships.parent_child:
        rows.append(_parent_child_relationship_row(relationship, target_members=target_members))
    for relationship in relationships.relationships.couples:
        rows.append(_couple_relationship_row(relationship, target_members=target_members))
    if relationships.relationships.relatives is None:
        # A payload without the links of unknown degree keeps them (for members still in
        # the family), so an editor that does not show them cannot drop them.
        rows.extend(
            relationship
            for relationship in current_relationships
            if relationship.get("relationship_type") == "relative"
            and target_members.get(_sample_key(str(relationship["sample_id_a"])), {}).get("active")
            and target_members.get(_sample_key(str(relationship["sample_id_b"])), {}).get("active")
        )
    else:
        for relative in relationships.relationships.relatives:
            rows.append(_relative_relationship_row(relative, target_members=target_members))
    return rows


def _relative_relationship_row(
    relationship: FamilyStructureRelativeUpdate,
    *,
    target_members: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    # sample_id_a is the member the link goes through, sample_id_b the relative.
    related_to = _ensure_relationship_member(relationship.related_to, target_members=target_members)
    member = _ensure_relationship_member(relationship.member, target_members=target_members)
    return {
        "relationship_type": "relative",
        "sample_id_a": related_to,
        "sample_id_b": member,
        "role_a": "relative",
        "role_b": "relative",
        "source": "manual",
        "metadata": dict(relationship.metadata or {}),
    }


def _ensure_relationship_member(
    sample_id: str,
    *,
    target_members: dict[str, dict[str, Any]],
) -> str:
    cleaned = _clean_sample_id(sample_id)
    row = target_members.get(_sample_key(cleaned))
    if row is None:
        raise HTTPException(status_code=400, detail=f"Relationship member '{cleaned}' is not in this family")
    if not row.get("active"):
        raise HTTPException(status_code=400, detail=f"Relationship member '{cleaned}' is inactive")
    return str(row["sample_id"])


def _parent_child_relationship_row(
    relationship: FamilyStructureParentChildUpdate,
    *,
    target_members: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    parent = _ensure_relationship_member(relationship.parent, target_members=target_members)
    child = _ensure_relationship_member(relationship.child, target_members=target_members)
    _validate_parent_role(
        parent=parent,
        child=child,
        parent_role=relationship.parent_role,
        target_members=target_members,
    )
    return {
        "relationship_type": "parent_child",
        "sample_id_a": parent,
        "sample_id_b": child,
        "role_a": relationship.parent_role,
        "role_b": "child",
        "source": "manual",
        "metadata": dict(relationship.metadata or {}),
    }


def _couple_relationship_row(
    relationship: FamilyStructureCoupleUpdate,
    *,
    target_members: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    partners = [_ensure_relationship_member(sample_id, target_members=target_members) for sample_id in relationship.partners]
    if _sample_key(partners[0]) == _sample_key(partners[1]):
        raise HTTPException(status_code=400, detail="A couple relationship needs two different members")
    metadata = dict(relationship.metadata or {})
    if relationship.context:
        metadata["context"] = relationship.context
    return {
        "relationship_type": "couple",
        "sample_id_a": partners[0],
        "sample_id_b": partners[1],
        "role_a": "partner",
        "role_b": "partner",
        "source": "manual",
        "metadata": metadata,
    }


def _validate_relationship_graph(
    relationships: list[dict[str, Any]],
    *,
    target_members: dict[str, dict[str, Any]],
) -> None:
    parent_edges: set[tuple[str, str]] = set()
    couple_edges: set[tuple[str, str]] = set()
    relative_edges: set[tuple[str, str]] = set()
    parents_by_child: dict[str, list[dict[str, Any]]] = defaultdict(list)
    children_by_parent: dict[str, list[str]] = defaultdict(list)

    for relationship in relationships:
        relationship_type = relationship.get("relationship_type")
        sample_a = _ensure_relationship_member(str(relationship.get("sample_id_a")), target_members=target_members)
        sample_b = _ensure_relationship_member(str(relationship.get("sample_id_b")), target_members=target_members)
        key_a = _sample_key(sample_a)
        key_b = _sample_key(sample_b)
        if relationship_type == "parent_child":
            if key_a == key_b:
                raise HTTPException(status_code=400, detail="A member cannot be their own parent")
            edge_key = (key_a, key_b)
            if edge_key in parent_edges:
                raise HTTPException(status_code=400, detail=f"Duplicate parent-child relationship for {sample_a} and {sample_b}")
            parent_edges.add(edge_key)
            parent_role = str(relationship.get("role_a") or "parent")
            _validate_parent_role(
                parent=sample_a,
                child=sample_b,
                parent_role=parent_role,
                target_members=target_members,
            )
            parents_by_child[key_b].append({"sample_id": sample_a, "role": parent_role})
            children_by_parent[key_a].append(key_b)
        elif relationship_type == "couple":
            if key_a == key_b:
                raise HTTPException(status_code=400, detail="A couple relationship needs two different members")
            edge_key = tuple(sorted((key_a, key_b)))
            if edge_key in couple_edges:
                raise HTTPException(status_code=400, detail=f"Duplicate couple relationship for {sample_a} and {sample_b}")
            couple_edges.add(edge_key)
        elif relationship_type == "relative":
            if key_a == key_b:
                raise HTTPException(status_code=400, detail="A member cannot be related to themselves")
            edge_key = tuple(sorted((key_a, key_b)))
            if edge_key in relative_edges:
                raise HTTPException(status_code=400, detail=f"Duplicate relative link for {sample_a} and {sample_b}")
            relative_edges.add(edge_key)
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported relationship type '{relationship_type}'")

    # A link of unknown degree between two members whose relationship is recorded says
    # less than the record, and would draw both.
    known_pairs = {tuple(sorted(edge)) for edge in parent_edges} | couple_edges
    for edge_key in sorted(relative_edges & known_pairs):
        sample_a, sample_b = (target_members[key]["sample_id"] for key in edge_key)
        raise HTTPException(
            status_code=400,
            detail=f"{sample_a} and {sample_b} are already linked as parent and child or as a couple",
        )

    for child_key, parents in parents_by_child.items():
        if len(parents) > 2:
            child = target_members[child_key]["sample_id"]
            raise HTTPException(status_code=400, detail=f"{child} cannot have more than two parents")
        father_count = sum(1 for parent in parents if parent["role"] == "father")
        mother_count = sum(1 for parent in parents if parent["role"] == "mother")
        if father_count > 1 or mother_count > 1:
            child = target_members[child_key]["sample_id"]
            raise HTTPException(status_code=400, detail=f"{child} has duplicate father or mother relationships")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(sample_key: str) -> None:
        if sample_key in visiting:
            sample_id = target_members[sample_key]["sample_id"]
            raise HTTPException(status_code=400, detail=f"Parent-child relationships contain a cycle near {sample_id}")
        if sample_key in visited:
            return
        visiting.add(sample_key)
        for child_key in children_by_parent.get(sample_key, []):
            visit(child_key)
        visiting.remove(sample_key)
        visited.add(sample_key)

    for member_key in target_members:
        if target_members[member_key].get("active"):
            visit(member_key)


def _pedigree_text_from_target(
    *,
    family_id: str,
    target_members: dict[str, dict[str, Any]],
    relationships: list[dict[str, Any]],
) -> str:
    parents_by_child: dict[str, dict[str, str | None]] = defaultdict(lambda: {"father": None, "mother": None})
    generic_parents: dict[str, list[str]] = defaultdict(list)
    for relationship in relationships:
        if relationship.get("relationship_type") != "parent_child":
            continue
        parent = str(relationship["sample_id_a"])
        child = str(relationship["sample_id_b"])
        parent_key = _sample_key(parent)
        child_key = _sample_key(child)
        role = str(relationship.get("role_a") or "parent")
        parent_sex = str(target_members[parent_key].get("sex") or "und")
        if role == "father" or (role == "parent" and parent_sex == "male"):
            parents_by_child[child_key]["father"] = parent
        elif role == "mother" or (role == "parent" and parent_sex == "female"):
            parents_by_child[child_key]["mother"] = parent
        else:
            generic_parents[child_key].append(parent)

    for child_key, parents in generic_parents.items():
        for parent in parents:
            if parents_by_child[child_key]["father"] is None:
                parents_by_child[child_key]["father"] = parent
            elif parents_by_child[child_key]["mother"] is None:
                parents_by_child[child_key]["mother"] = parent

    lines: list[str] = []
    active_members = [
        row
        for row in target_members.values()
        if row.get("active")
    ]
    for row in sorted(active_members, key=lambda item: str(item["sample_id"]).lower()):
        sample_id = str(row["sample_id"])
        parents = parents_by_child[_sample_key(sample_id)]
        lines.append(
            " ".join(
                [
                    family_id,
                    sample_id,
                    parents.get("father") or "0",
                    parents.get("mother") or "0",
                    _sex_code(str(row.get("sex") or "und")),
                    _phenotype_code(str(row.get("clinical_status") or "unknown")),
                ]
            )
        )
    return "\n".join(lines)


def _relationship_key(relationship: dict[str, Any]) -> tuple[Any, ...]:
    metadata_key = json.dumps(relationship.get("metadata") or {}, sort_keys=True, default=str)
    if relationship.get("relationship_type") == "relative":
        return (
            "relative",
            _sample_key(str(relationship.get("sample_id_a"))),
            _sample_key(str(relationship.get("sample_id_b"))),
            relationship.get("source") or "manual",
            metadata_key,
        )
    if relationship.get("relationship_type") == "couple":
        partner_keys = sorted([
            _sample_key(str(relationship.get("sample_id_a"))),
            _sample_key(str(relationship.get("sample_id_b"))),
        ])
        return ("couple", *partner_keys, relationship.get("source") or "manual", metadata_key)
    return (
        "parent_child",
        _sample_key(str(relationship.get("sample_id_a"))),
        _sample_key(str(relationship.get("sample_id_b"))),
        relationship.get("role_a") or "parent",
        relationship.get("source") or "manual",
        metadata_key,
    )


def _nonzero_counts(counts: dict[str, int]) -> dict[str, int]:
    return {
        data_type: count
        for data_type, count in counts.items()
        if count > 0
    }


async def _insert_new_member(
    session: AsyncSession,
    *,
    family_uuid: str,
    member: dict[str, Any],
) -> None:
    sample_result = await session.execute(
        text(
            """
            INSERT INTO samples (sample_id, family_id, sex, metadata)
            VALUES (
                :sample_id,
                CAST(:family_uuid AS uuid),
                :sex,
                '{}'::jsonb
            )
            RETURNING id::text AS sample_uuid
            """
        ),
        {
            "sample_id": member["sample_id"],
            "family_uuid": family_uuid,
            "sex": member["sex"],
        },
    )
    member["sample_uuid"] = str(sample_result.scalar_one())
    await session.execute(
        text(
            """
            INSERT INTO family_members (
                family_id,
                sample_id,
                role,
                clinical_status,
                carrier_status,
                carrier_type,
                carrier_evidence,
                affected,
                active
            )
            VALUES (
                CAST(:family_uuid AS uuid),
                CAST(:sample_uuid AS uuid),
                :role,
                :clinical_status,
                :carrier_status,
                :carrier_type,
                CAST(:carrier_evidence AS jsonb),
                :affected,
                TRUE
            )
            """
        ),
        {
            "family_uuid": family_uuid,
            "sample_uuid": member["sample_uuid"],
            "role": member["role"],
            "clinical_status": member["clinical_status"],
            "carrier_status": member["carrier_status"],
            "carrier_type": member["carrier_type"],
            "carrier_evidence": json.dumps(member.get("carrier_evidence") or {}),
            "affected": member["clinical_status"] == "affected",
        },
    )
    await session.execute(
        text(
            """
            INSERT INTO sample_projects (sample_id, project_id)
            SELECT CAST(:sample_uuid AS uuid), project_id
            FROM family_projects
            WHERE family_id = CAST(:family_uuid AS uuid)
            ON CONFLICT DO NOTHING
            """
        ),
        {
            "sample_uuid": member["sample_uuid"],
            "family_uuid": family_uuid,
        },
    )


async def _update_member_row(
    session: AsyncSession,
    *,
    member: dict[str, Any],
) -> None:
    await session.execute(
        text(
            """
            UPDATE samples
            SET sex = :sex
            WHERE id = CAST(:sample_uuid AS uuid)
            """
        ),
        {
            "sample_uuid": member["sample_uuid"],
            "sex": member["sex"],
        },
    )
    await session.execute(
        text(
            """
            UPDATE family_members
            SET role = :role,
                clinical_status = :clinical_status,
                carrier_status = :carrier_status,
                carrier_type = :carrier_type,
                carrier_evidence = CAST(:carrier_evidence AS jsonb),
                affected = :affected,
                active = :active,
                updated_at = timezone('utc', now())
            WHERE family_id = CAST(:family_uuid AS uuid)
              AND sample_id = CAST(:sample_uuid AS uuid)
            """
        ),
        {
            "family_uuid": member["family_uuid"],
            "sample_uuid": member["sample_uuid"],
            "role": member["role"],
            "clinical_status": member["clinical_status"],
            "carrier_status": member["carrier_status"],
            "carrier_type": member["carrier_type"],
            "carrier_evidence": json.dumps(member.get("carrier_evidence") or {}),
            "affected": member["clinical_status"] == "affected",
            "active": bool(member.get("active")),
        },
    )


async def _replace_relationship_rows(
    session: AsyncSession,
    *,
    family_uuid: str,
    target_members: dict[str, dict[str, Any]],
    relationships: list[dict[str, Any]],
) -> None:
    await session.execute(
        text(
            """
            UPDATE family_relationships
            SET active = FALSE
            WHERE family_id = CAST(:family_uuid AS uuid)
              AND active
            """
        ),
        {"family_uuid": family_uuid},
    )
    if not relationships:
        return
    params = []
    for relationship in relationships:
        sample_a = target_members[_sample_key(str(relationship["sample_id_a"]))]
        sample_b = target_members[_sample_key(str(relationship["sample_id_b"]))]
        params.append(
            {
                "family_uuid": family_uuid,
                "relationship_type": relationship["relationship_type"],
                "sample_uuid_a": sample_a["sample_uuid"],
                "sample_uuid_b": sample_b["sample_uuid"],
                "role_a": relationship.get("role_a"),
                "role_b": relationship.get("role_b"),
                "source": relationship.get("source") or "manual",
                "metadata": json.dumps(relationship.get("metadata") or {}),
            }
        )
    await session.execute(
        text(
            """
            INSERT INTO family_relationships (
                family_id,
                relationship_type,
                sample_id_a,
                sample_id_b,
                role_a,
                role_b,
                source,
                metadata
            )
            VALUES (
                CAST(:family_uuid AS uuid),
                :relationship_type,
                CAST(:sample_uuid_a AS uuid),
                CAST(:sample_uuid_b AS uuid),
                :role_a,
                :role_b,
                :source,
                CAST(:metadata AS jsonb)
            )
            """
        ),
        params,
    )


def _warning_payload(
    *,
    added_sample_ids: list[str],
    removed_sample_ids: list[str],
    status_changed: bool,
    structure_changed: bool,
    relationships_changed: bool,
    cleared_data_counts: dict[str, int] | None = None,
) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    stale_scopes: list[str] = []
    cleared_counts = _nonzero_counts(cleared_data_counts or {})
    clear_stated = False
    if added_sample_ids:
        warnings.append("New family members have no genomic tracks until sample data are imported.")
        stale_scopes.append("sample-data")
    if removed_sample_ids:
        if cleared_counts:
            warnings.append("Removed family members are inactive and previously imported genomic data was cleared for reload.")
            clear_stated = True
        else:
            warnings.append("Removed family members are inactive; imported genomic datasets were preserved.")
        stale_scopes.append("sample-data")
    if relationships_changed:
        if cleared_counts:
            warnings.append("Relationship-dependent data was cleared; reload genomic datasets before review.")
            clear_stated = True
        else:
            warnings.append("Relationship-dependent analyses were marked stale; imported genomic datasets were preserved.")
        stale_scopes.extend(["segregation", "haplotypes", "phasing"])
    elif structure_changed:
        if cleared_counts:
            warnings.append("Family structure changed and genomic data was cleared; reload datasets before review.")
            clear_stated = True
        else:
            warnings.append("Family structure metadata changed; relationship-dependent outputs were marked stale without reloading raw datasets.")
        stale_scopes.extend(["segregation", "haplotypes"])
    if status_changed:
        warnings.append("Phenotype and carrier labels were updated; downstream interpretation views were marked stale without reloading raw datasets.")
        stale_scopes.append("variant-interpretation")
    if cleared_counts:
        # The imported data is gone, whatever else changed: it has to be imported again,
        # and the response says so even when the request changed nothing else.
        if not clear_stated:
            warnings.append("Imported genomic data was cleared; reload the datasets before review.")
        stale_scopes.append("sample-data")
    deduped_scopes = list(dict.fromkeys(stale_scopes))
    return warnings, deduped_scopes


async def update_family_structure_for_admin(
    session: AsyncSession,
    *,
    family_id: str,
    update: FamilyStructureUpdate,
    user: CurrentUser,
) -> FamilyStructureUpdateOut:
    family_row = await get_accessible_family_mapping(session, family_id, user)
    family_uuid = str(family_row["id"])
    current_version = await _fetch_current_structure_version(session, family_uuid=family_uuid)
    if update.expected_structure_version is not None and update.expected_structure_version != current_version:
        raise HTTPException(
            status_code=409,
            detail=f"Family structure changed since it was loaded; current version is {current_version}",
        )

    update_dicts = [
        {**member.model_dump(exclude_unset=True), "sample_id": _clean_sample_id(member.sample_id)}
        for member in update.members
    ]
    remove_members = [_clean_sample_id(sample_id, field_name="remove_members") for sample_id in update.remove_members]
    _validate_member_updates(
        add_members=update.add_members,
        updates=update_dicts,
        remove_members=remove_members,
    )

    current_members = await _fetch_family_member_rows(session, family_uuid=family_uuid)
    current_relationships = await _fetch_current_relationship_rows(session, family_uuid=family_uuid)
    target_members = {key: dict(row) for key, row in current_members.items()}
    # Track which members actually changed so only they are re-written below —
    # avoids re-UPDATEing every member (2 UPDATEs each) and bumping updated_at on
    # unchanged rows for a single-field edit.
    dirty_member_keys: set[str] = set()

    new_member_payloads = [_member_payload(member, family_uuid=family_uuid) for member in update.add_members]
    true_new_members: list[dict[str, Any]] = []
    added_sample_ids: list[str] = []
    reactivated_sample_ids: list[str] = []
    for member in new_member_payloads:
        key = _sample_key(member["sample_id"])
        existing = target_members.get(key)
        if existing and existing.get("active"):
            raise HTTPException(status_code=409, detail=f"Family member '{member['sample_id']}' already exists")
        if existing and not existing.get("active"):
            member["sample_uuid"] = existing["sample_uuid"]
            member["is_new"] = False
            target_members[key] = {**existing, **member, "active": True}
            reactivated_sample_ids.append(member["sample_id"])
            dirty_member_keys.add(key)
        else:
            target_members[key] = member
            true_new_members.append(member)
            added_sample_ids.append(member["sample_id"])
            dirty_member_keys.add(key)

    conflicts = await _sample_id_conflicts(
        session,
        sample_ids=[member["sample_id"] for member in true_new_members],
    )
    if conflicts:
        conflicting_ids = ", ".join(sorted({str(row["sample_id"]) for row in conflicts}))
        raise HTTPException(status_code=409, detail=f"Sample id already exists: {conflicting_ids}")

    status_changed = False
    structure_changed = bool(new_member_payloads)
    removed_sample_ids: list[str] = []
    for update_fields in update_dicts:
        sample_id = _clean_sample_id(str(update_fields["sample_id"]))
        key = _sample_key(sample_id)
        target = target_members.get(key)
        if target is None:
            raise HTTPException(status_code=404, detail=f"Family member '{sample_id}' not found")
        was_active = bool(target.get("active"))
        changed_status, changed_structure = _apply_member_update(target, update_fields)
        status_changed = status_changed or changed_status
        structure_changed = structure_changed or changed_structure
        if changed_status or changed_structure:
            dirty_member_keys.add(key)
        if was_active and not target.get("active"):
            removed_sample_ids.append(str(target["sample_id"]))

    for sample_id in remove_members:
        key = _sample_key(sample_id)
        target = target_members.get(key)
        if target is None:
            raise HTTPException(status_code=404, detail=f"Family member '{sample_id}' not found")
        if target.get("active"):
            dirty_member_keys.add(key)
            if str(target["sample_id"]) not in removed_sample_ids:
                removed_sample_ids.append(str(target["sample_id"]))
                structure_changed = True
        target["active"] = False

    active_members = [row for row in target_members.values() if row.get("active")]
    if not active_members:
        raise HTTPException(status_code=400, detail="A family must have at least one active member")
    active_probands = [row["sample_id"] for row in active_members if row.get("role") == "proband"]
    if len(active_probands) > 1:
        raise HTTPException(status_code=400, detail="A family can have only one active proband")

    if update.relationships is not None:
        target_relationships = _relationship_rows_from_payload(
            update, target_members=target_members, current_relationships=current_relationships
        )
    else:
        target_relationships = [
            relationship
            for relationship in current_relationships
            if target_members.get(_sample_key(str(relationship["sample_id_a"])), {}).get("active")
            and target_members.get(_sample_key(str(relationship["sample_id_b"])), {}).get("active")
        ]
    _validate_relationship_graph(target_relationships, target_members=target_members)

    current_relationship_keys = {_relationship_key(relationship) for relationship in current_relationships}
    target_relationship_keys = {_relationship_key(relationship) for relationship in target_relationships}
    relationships_changed = current_relationship_keys != target_relationship_keys
    data_counts: dict[str, int] = {}
    cleared_data_counts: dict[str, int] = {}
    if update.clear_existing_genomic_data:
        data_counts = await _family_genomic_data_counts(session, family_uuid=family_uuid)
        if _nonzero_counts(data_counts):
            cleared_data_counts = await _clear_family_genomic_data(session, family_uuid=family_uuid)

    for member in true_new_members:
        await _insert_new_member(session, family_uuid=family_uuid, member=member)
    for key, member in target_members.items():
        if key not in dirty_member_keys:
            continue
        if member.get("sample_uuid") is None:
            raise HTTPException(status_code=500, detail=f"Missing sample id for '{member['sample_id']}'")
        await _update_member_row(session, member=member)

    if relationships_changed:
        await _replace_relationship_rows(
            session,
            family_uuid=family_uuid,
            target_members=target_members,
            relationships=target_relationships,
        )
    pedigree = _pedigree_text_from_target(
        family_id=str(family_row["family_id"]),
        target_members=target_members,
        relationships=target_relationships,
    )
    warnings, stale_scopes = _warning_payload(
        added_sample_ids=[*added_sample_ids, *reactivated_sample_ids],
        removed_sample_ids=removed_sample_ids,
        status_changed=status_changed,
        structure_changed=structure_changed,
        relationships_changed=relationships_changed,
        cleared_data_counts=cleared_data_counts,
    )
    # `derived_data_status` is built whole rather than with a nested jsonb_set: that returns
    # its input unchanged when the parent key is missing, as it is until the first marker.
    await session.execute(
        text(
            """
            UPDATE families
            SET pedigree = :pedigree,
                metadata = CASE
                    WHEN :stale_scopes = '[]' THEN metadata
                    ELSE jsonb_set(
                        COALESCE(metadata, '{}'::jsonb),
                        '{derived_data_status}',
                        CASE
                            WHEN jsonb_typeof(metadata -> 'derived_data_status') = 'object'
                                THEN metadata -> 'derived_data_status'
                            ELSE '{}'::jsonb
                        END
                        || jsonb_build_object(
                            'family_metadata', jsonb_build_object(
                                'state', 'stale',
                                'reason', CAST(:reason AS text),
                                'scopes', CAST(:stale_scopes AS jsonb),
                                'raw_datasets_preserved', CAST(:raw_datasets_preserved AS boolean),
                                'updated_at', timezone('utc', now())
                            ),
                            'updated_at', timezone('utc', now())
                        ),
                        TRUE
                    )
                END
            WHERE id = CAST(:family_uuid AS uuid)
            """
        ),
        {
            "family_uuid": family_uuid,
            "pedigree": pedigree,
            "reason": update.change_reason or "family_structure_metadata_updated",
            "stale_scopes": json.dumps(stale_scopes),
            # False once this update deleted imported data (clear_existing_genomic_data).
            "raw_datasets_preserved": not _nonzero_counts(cleared_data_counts),
        },
    )

    await _record_family_structure_version(
        session,
        family_uuid=family_uuid,
        created_by=str(user.id),
        metadata={
            "reason": update.change_reason,
            "warnings": warnings,
            "stale_analysis_scopes": stale_scopes,
            "data_counts_before_change": data_counts,
            "cleared_data_counts": cleared_data_counts,
        },
    )
    await session.commit()
    family = await get_family_record(session, family_id, user)
    return FamilyStructureUpdateOut(
        family=family,
        warnings=warnings,
        stale_analysis_scopes=stale_scopes,
        data_counts=data_counts,
        cleared_data_counts=cleared_data_counts,
    )
