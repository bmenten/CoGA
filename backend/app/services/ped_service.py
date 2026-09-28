from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from fastapi import HTTPException, UploadFile
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas import ManualPedFamilyCreate, ManualPedMemberCreate, PedUploadResult
from .clickhouse_variant_storage import delete_family_small_variants, delete_family_structural_variants
from .upload_safety import decode_upload_text
from .access_control import CurrentUser, is_admin_user

INHERITANCE_MODELS = {"AD", "AR", "XLD", "XLR", "mitochondrial"}


def _parse_ped_text(text_value: str) -> dict[str, list[dict[str, str]]]:
    lines = [line for line in text_value.splitlines() if line.strip()]
    if not lines:
        raise HTTPException(status_code=400, detail="PED file is empty")
    families: dict[str, list[dict[str, str]]] = {}
    for line in lines:
        parts = line.strip().split()
        if len(parts) < 6:
            continue
        family_id, individual_id, father_id, mother_id, sex, phenotype = parts[:6]
        families.setdefault(family_id, []).append(
            {
                "iid": individual_id,
                "pid": father_id,
                "mid": mother_id,
                "sex": sex,
                "phen": phenotype,
            }
        )
    return families


def _sex_code_from_label(sex: str) -> str:
    return {"male": "1", "female": "2"}.get(sex, "0")


def _phenotype_code_from_clinical_status(clinical_status: str) -> str:
    if clinical_status == "affected":
        return "2"
    if clinical_status == "unaffected":
        return "1"
    return "0"


def _pedigree_status_from_phenotype(phenotype: str) -> str:
    normalized = phenotype.strip().lower()
    if normalized == "1":
        return "unaffected"
    if normalized == "2":
        return "affected"
    if normalized in {"0", "-9"}:
        return "unknown"
    raise HTTPException(
        status_code=400,
        detail=f"Unsupported PED phenotype '{phenotype}'. Use 0/-9 for missing, 1 for unaffected, or 2 for affected.",
    )


def _split_sample_id_list(value: str | None) -> list[str]:
    if value is None:
        return []
    return [
        item.strip()
        for item in value.replace(";", ",").split(",")
        if item.strip()
    ]


def _normalize_inheritance_model(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    cleaned = value.strip()
    normalized = cleaned.upper()
    if normalized in {"MITO", "MITOCHONDRIAL"}:
        return "mitochondrial"
    if normalized in INHERITANCE_MODELS:
        return normalized
    raise HTTPException(
        status_code=400,
        detail="Inheritance model must be one of AD, AR, XLD, XLR, or mitochondrial",
    )


def _carrier_annotations(
    sample_id: str,
    *,
    obligate_carriers: set[str],
    proven_carriers: set[str],
) -> dict[str, Any]:
    if sample_id in proven_carriers:
        return {"carrier_status": "carrier", "carrier_type": "proven"}
    if sample_id in obligate_carriers:
        return {"carrier_status": "carrier", "carrier_type": "obligate"}
    return {"carrier_status": "unknown", "carrier_type": None}


def _build_ped_text_from_manual_family(family: ManualPedFamilyCreate) -> str:
    return "\n".join(
        [
            " ".join(
                [
                    family.family_id,
                    member.sample_id,
                    member.father_id or "0",
                    member.mother_id or "0",
                    _sex_code_from_label(member.sex),
                    _phenotype_code_from_clinical_status(
                        _manual_clinical_status(member)
                    ),
                ]
            )
            for member in family.members
        ]
    )


_PED_ROLE_SORT_RANK = {"father": 0, "mother": 1, "proband": 2, "sibling": 3}


async def build_pedigree_text(session: AsyncSession, *, family_id: str) -> str:
    """Reconstruct PED text for a family from its current database structure.

    Uses the live ``family_members`` / ``family_relationships`` / ``samples`` rows
    rather than the stored ``families.pedigree`` blob, so the result reflects any
    edits made through the family workspace after the original import.
    """
    member_result = await session.execute(
        text(
            """
            SELECT
                s.sample_id,
                s.sex,
                fm.clinical_status,
                fm.role
            FROM families f
            JOIN family_members fm ON fm.family_id = f.id
            JOIN samples s ON s.id = fm.sample_id
            WHERE f.family_id = :family_id
              AND fm.active IS NOT FALSE
            ORDER BY
                CASE fm.role
                    WHEN 'father' THEN 0
                    WHEN 'mother' THEN 1
                    WHEN 'proband' THEN 2
                    WHEN 'sibling' THEN 3
                    ELSE 4
                END,
                lower(s.sample_id)
            """
        ),
        {"family_id": family_id},
    )
    members = member_result.mappings().all()
    if not members:
        raise HTTPException(
            status_code=404,
            detail=f"Family '{family_id}' was not found or has no members",
        )

    relationship_result = await session.execute(
        text(
            """
            SELECT
                sa.sample_id AS parent_id,
                sb.sample_id AS child_id,
                fr.role_a
            FROM families f
            JOIN family_relationships fr ON fr.family_id = f.id
            JOIN samples sa ON sa.id = fr.sample_id_a
            JOIN samples sb ON sb.id = fr.sample_id_b
            WHERE f.family_id = :family_id
              AND fr.relationship_type = 'parent_child'
              AND fr.active IS NOT FALSE
            """
        ),
        {"family_id": family_id},
    )
    father_by_child: dict[str, str] = {}
    mother_by_child: dict[str, str] = {}
    for row in relationship_result.mappings().all():
        if row["role_a"] == "father":
            father_by_child[row["child_id"]] = row["parent_id"]
        elif row["role_a"] == "mother":
            mother_by_child[row["child_id"]] = row["parent_id"]

    lines = [
        " ".join(
            [
                family_id,
                member["sample_id"],
                father_by_child.get(member["sample_id"], "0"),
                mother_by_child.get(member["sample_id"], "0"),
                _sex_code_from_label(member["sex"]),
                _phenotype_code_from_clinical_status(member["clinical_status"] or "unknown"),
            ]
        )
        for member in members
    ]
    return "\n".join(lines) + "\n"


def _manual_clinical_status(member: ManualPedMemberCreate) -> str:
    if member.clinical_status != "unknown":
        return member.clinical_status
    if member.affected:
        return "affected"
    return "unknown"


def _normalize_carrier_status(status: str | None, carrier_type: str | None = None) -> str:
    normalized = (status or "").strip().lower()
    if normalized in {"unknown", "not_carrier", "carrier"}:
        return "carrier" if carrier_type and normalized != "carrier" else normalized
    return "carrier" if carrier_type else "unknown"


def _normalize_parent_id(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _normalize_project_id(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _metadata_project_ids_for_user(user: CurrentUser) -> set[str]:
    return {
        str(project_id)
        for project_id in getattr(user, "metadata_project_ids", []) or []
        if project_id
    }


def _require_uuid(value: str, detail: str) -> None:
    try:
        UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=detail) from exc


async def _resolve_accessible_project_id(
    session: AsyncSession,
    user: CurrentUser,
    project_id: str | None,
) -> str | None:
    normalized_project_id = _normalize_project_id(project_id)
    if normalized_project_id is None:
        if is_admin_user(user):
            return None
        raise HTTPException(status_code=400, detail="Project assignment is required")

    _require_uuid(normalized_project_id, "Invalid project id")
    result = await session.execute(
        text("SELECT id::text AS id FROM projects WHERE id = CAST(:project_id AS uuid)"),
        {"project_id": normalized_project_id},
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if not is_admin_user(user) and normalized_project_id not in _metadata_project_ids_for_user(user):
        raise HTTPException(status_code=403, detail="Not authorized for this project")
    return normalized_project_id


def _resolve_manual_family_roles(
    members: list[ManualPedMemberCreate],
) -> dict[str, str]:
    father_ids = {member.father_id for member in members if member.father_id}
    mother_ids = {member.mother_id for member in members if member.mother_id}
    explicit_proband = next((member.sample_id for member in members if member.is_proband), None)
    fallback_proband = next(
        (
            member.sample_id
            for member in members
            if member.sample_id not in father_ids and member.sample_id not in mother_ids
        ),
        members[0].sample_id,
    )
    proband_id = explicit_proband or fallback_proband
    roles: dict[str, str] = {}
    for member in members:
        if member.sample_id in father_ids:
            roles[member.sample_id] = "father"
        elif member.sample_id in mother_ids:
            roles[member.sample_id] = "mother"
        elif member.sample_id == proband_id:
            roles[member.sample_id] = "proband"
        else:
            roles[member.sample_id] = "sibling"
    return roles


def _validate_manual_family(family: ManualPedFamilyCreate) -> list[ManualPedMemberCreate]:
    normalized_members: list[ManualPedMemberCreate] = []
    sample_ids: list[str] = []
    for member in family.members:
        sample_id = member.sample_id.strip()
        if not sample_id:
            raise HTTPException(status_code=400, detail="Sample id is required for every member")
        clinical_status = _manual_clinical_status(member)
        carrier_status = _normalize_carrier_status(member.carrier_status, member.carrier_type)
        normalized_members.append(
            member.model_copy(
                update={
                    "sample_id": sample_id,
                    "father_id": _normalize_parent_id(member.father_id),
                    "mother_id": _normalize_parent_id(member.mother_id),
                    "clinical_status": clinical_status,
                    "affected": clinical_status == "affected",
                    "carrier_status": carrier_status,
                    "carrier_type": member.carrier_type if carrier_status == "carrier" else None,
                }
            )
        )
        sample_ids.append(sample_id)
    if len(sample_ids) != len(set(sample_ids)):
        raise HTTPException(status_code=400, detail="Sample ids must be unique within a family")

    member_map = {member.sample_id: member for member in normalized_members}
    probands = [member.sample_id for member in normalized_members if member.is_proband]
    if len(probands) > 1:
        raise HTTPException(status_code=400, detail="Only one proband can be selected")
    for member in normalized_members:
        if member.father_id == member.sample_id or member.mother_id == member.sample_id:
            raise HTTPException(status_code=400, detail="A member cannot reference themselves as a parent")
        if member.father_id and member.mother_id and member.father_id == member.mother_id:
            raise HTTPException(status_code=400, detail="Father and mother must be different individuals")
        if member.father_id and member.father_id not in member_map:
            raise HTTPException(status_code=400, detail=f"Father id not found: {member.father_id}")
        if member.mother_id and member.mother_id not in member_map:
            raise HTTPException(status_code=400, detail=f"Mother id not found: {member.mother_id}")
        if member.father_id and member_map[member.father_id].sex == "female":
            raise HTTPException(status_code=400, detail="Father must not have female sex")
        if member.mother_id and member_map[member.mother_id].sex == "male":
            raise HTTPException(status_code=400, detail="Mother must not have male sex")
    for couple in family.couples:
        left, right = [partner.strip() for partner in couple.partners]
        if not left or not right or left == right:
            raise HTTPException(status_code=400, detail="Couple relationships require two different sample ids")
        if left not in member_map or right not in member_map:
            raise HTTPException(status_code=400, detail="Couple partners must be members of the family")
    return normalized_members


async def _existing_family_rows(
    session: AsyncSession,
    family_ids: list[str],
) -> list[dict[str, Any]]:
    if not family_ids:
        return []
    result = await session.execute(
        text(
            """
            SELECT
                f.id::text AS family_uuid,
                f.family_id,
                fp.project_id::text AS project_id,
                a.assembly_name
            FROM families f
            LEFT JOIN family_projects fp ON fp.family_id = f.id
            LEFT JOIN projects p ON p.id = fp.project_id
            LEFT JOIN assemblies a ON a.id = p.assembly_id
            WHERE f.family_id IN :family_ids
            """
        ).bindparams(bindparam("family_ids", expanding=True)),
        {"family_ids": family_ids},
    )
    return [dict(row) for row in result.mappings().all()]


def _ensure_user_can_replace_existing_families(user: CurrentUser) -> None:
    # Replacing existing families/samples is admin-only — a role-based policy,
    # not a per-project one.
    if is_admin_user(user):
        return
    raise HTTPException(
        status_code=403,
        detail="Only admins can overwrite existing families or samples",
    )


async def _replace_existing_families(
    session: AsyncSession,
    family_ids: list[str],
    overwrite: bool,
    user: CurrentUser,
) -> None:
    existing_rows = await _existing_family_rows(session, family_ids)
    if not existing_rows:
        return
    existing_family_ids = sorted({row["family_id"] for row in existing_rows if row["family_id"]})
    if not overwrite:
        raise HTTPException(
            status_code=409,
            detail=f"Family id already exists: {', '.join(existing_family_ids)}",
        )
    _ensure_user_can_replace_existing_families(user)
    assembly_rows_by_family: dict[str, set[str]] = {}
    family_uuid_by_id: dict[str, str] = {}
    for row in existing_rows:
        family_id = str(row["family_id"])
        family_uuid_by_id[family_id] = str(row["family_uuid"])
        assembly_name = row.get("assembly_name")
        if assembly_name:
            assembly_rows_by_family.setdefault(family_id, set()).add(str(assembly_name))
    for family_id, family_uuid in family_uuid_by_id.items():
        for assembly_name in assembly_rows_by_family.get(family_id, set()):
            await delete_family_small_variants(assembly_name, family_uuid)
            await delete_family_structural_variants(assembly_name, family_uuid)
        await session.execute(
            text("DELETE FROM families WHERE id = CAST(:family_uuid AS uuid)"),
            {"family_uuid": family_uuid},
        )


async def _ensure_sample_ids_are_available(
    session: AsyncSession,
    sample_ids: list[str],
) -> None:
    if not sample_ids:
        return
    result = await session.execute(
        text(
            """
            SELECT sample_id
            FROM samples
            WHERE sample_id IN :sample_ids
            """
        ).bindparams(bindparam("sample_ids", expanding=True)),
        {"sample_ids": sample_ids},
    )
    existing_sample_ids = [str(row[0]) for row in result.all() if row[0]]
    if existing_sample_ids:
        raise HTTPException(
            status_code=409,
            detail=f"Sample id already exists: {', '.join(existing_sample_ids)}",
        )


def _relationship_key(
    relationship_type: str,
    sample_id_a: str,
    sample_id_b: str,
    role_a: str | None,
    role_b: str | None,
) -> tuple[str, str, str, str, str]:
    if relationship_type == "couple" and sample_id_b < sample_id_a:
        sample_id_a, sample_id_b = sample_id_b, sample_id_a
        role_a, role_b = role_b, role_a
    return (
        relationship_type,
        sample_id_a,
        sample_id_b,
        role_a or "",
        role_b or "",
    )


def _relationships_from_members(
    members: list[dict[str, Any]],
    explicit_couples: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    relationships: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()

    def add_relationship(
        relationship_type: str,
        sample_id_a: str,
        sample_id_b: str,
        *,
        role_a: str | None = None,
        role_b: str | None = None,
        source: str = "pedigree",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        key = _relationship_key(relationship_type, sample_id_a, sample_id_b, role_a, role_b)
        if key in seen:
            return
        seen.add(key)
        relationships.append(
            {
                "relationship_type": relationship_type,
                "sample_id_a": key[1],
                "sample_id_b": key[2],
                "role_a": role_a,
                "role_b": role_b,
                "source": source,
                "metadata": metadata or {},
            }
        )

    for member in members:
        child_id = member["sample_id"]
        father_id = member.get("father_id")
        mother_id = member.get("mother_id")
        if father_id:
            add_relationship(
                "parent_child",
                father_id,
                child_id,
                role_a="father",
                role_b="child",
            )
        if mother_id:
            add_relationship(
                "parent_child",
                mother_id,
                child_id,
                role_a="mother",
                role_b="child",
            )
        if father_id and mother_id:
            add_relationship(
                "couple",
                father_id,
                mother_id,
                role_a="partner",
                role_b="partner",
                source="pedigree",
                metadata={"has_children": True},
            )

    for couple in explicit_couples or []:
        partners = couple.get("partners") or []
        if len(partners) != 2:
            continue
        metadata = dict(couple.get("metadata") or {})
        if couple.get("context"):
            metadata["context"] = couple["context"]
        add_relationship(
            "couple",
            str(partners[0]),
            str(partners[1]),
            role_a="partner",
            role_b="partner",
            source=couple.get("source") or "manual",
            metadata=metadata,
        )
    return relationships


async def _record_family_structure_version(
    session: AsyncSession,
    *,
    family_uuid: str,
    created_by: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    member_result = await session.execute(
        text(
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
        ),
        {"family_uuid": family_uuid},
    )
    relationship_result = await session.execute(
        text(
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
        ),
        {"family_uuid": family_uuid},
    )
    snapshot = {
        "members": [dict(row) for row in member_result.mappings().all()],
        "relationships": [dict(row) for row in relationship_result.mappings().all()],
    }
    snapshot_json = json.dumps(snapshot, sort_keys=True, default=str)
    structure_hash = hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()
    version_result = await session.execute(
        text(
            """
            SELECT COALESCE(MAX(version), 0) + 1
            FROM family_structure_versions
            WHERE family_id = CAST(:family_uuid AS uuid)
            """
        ),
        {"family_uuid": family_uuid},
    )
    next_version = int(version_result.scalar_one())
    await session.execute(
        text(
            """
            INSERT INTO family_structure_versions (
                family_id,
                version,
                structure_hash,
                snapshot,
                metadata,
                created_by
            )
            VALUES (
                CAST(:family_uuid AS uuid),
                :version,
                :structure_hash,
                CAST(:snapshot AS jsonb),
                CAST(:metadata AS jsonb),
                CAST(:created_by AS uuid)
            )
            """
        ),
        {
            "family_uuid": family_uuid,
            "version": next_version,
            "structure_hash": structure_hash,
            "snapshot": snapshot_json,
            "metadata": json.dumps(metadata or {}),
            "created_by": created_by,
        },
    )


async def _create_family(
    session: AsyncSession,
    *,
    family_id: str,
    pedigree: str,
    members: list[dict[str, Any]],
    project_id: str | None,
    family_metadata: dict[str, Any] | None = None,
    roi: dict[str, Any] | None = None,
    relationships: list[dict[str, Any]] | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    created = await session.execute(
        text(
            """
            INSERT INTO families (
                family_id,
                pedigree,
                metadata,
                roi_query,
                roi_label,
                roi_source,
                roi_assembly_id,
                roi_chr,
                roi_start,
                roi_end
            )
            VALUES (
                :family_id,
                :pedigree,
                CAST(:metadata_json AS jsonb),
                :roi_query,
                :roi_label,
                :roi_source,
                CAST(:roi_assembly_id AS uuid),
                :roi_chr,
                :roi_start,
                :roi_end
            )
            RETURNING id::text AS family_uuid, family_id
            """
        ),
        {
            "family_id": family_id,
            "pedigree": pedigree,
            "metadata_json": json.dumps(family_metadata or {}),
            "roi_query": roi.get("query") if roi else None,
            "roi_label": roi.get("label") if roi else None,
            "roi_source": roi.get("source") if roi else None,
            "roi_assembly_id": roi.get("assembly_id") if roi else None,
            "roi_chr": roi.get("chr") if roi else None,
            "roi_start": roi.get("start") if roi else None,
            "roi_end": roi.get("end") if roi else None,
        },
    )
    family_row = dict(created.mappings().one())
    sample_ids = [member["sample_id"] for member in members]
    sample_rows: list[dict[str, str]] = []
    for member in members:
        sample_result = await session.execute(
            text(
                """
                INSERT INTO samples (sample_id, family_id, sex, metadata)
                VALUES (
                    :sample_id,
                    CAST(:family_uuid AS uuid),
                    :sex,
                    CAST(:metadata_json AS jsonb)
                )
                RETURNING id::text AS sample_uuid, sample_id
                """
            ),
            {
                "sample_id": member["sample_id"],
                "family_uuid": family_row["family_uuid"],
                "sex": member["sex"],
                "metadata_json": json.dumps(member.get("metadata") or {}),
            },
        )
        sample_rows.append(dict(sample_result.mappings().one()))
    sample_uuid_by_id = {row["sample_id"]: row["sample_uuid"] for row in sample_rows}
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
                affected
            )
            VALUES (
                CAST(:family_uuid AS uuid),
                CAST(:sample_uuid AS uuid),
                :role,
                :clinical_status,
                :carrier_status,
                :carrier_type,
                CAST(:carrier_evidence AS jsonb),
                :affected
            )
            """
        ),
        [
            {
                "family_uuid": family_row["family_uuid"],
                "sample_uuid": sample_uuid_by_id[member["sample_id"]],
                "role": member["role"],
                "clinical_status": member.get("clinical_status") or (
                    "affected" if bool(member.get("affected")) else "unknown"
                ),
                "carrier_status": _normalize_carrier_status(
                    member.get("carrier_status"),
                    member.get("carrier_type"),
                ),
                "carrier_type": member.get("carrier_type"),
                "carrier_evidence": json.dumps(member.get("carrier_evidence") or {}),
                "affected": (member.get("clinical_status") == "affected") or bool(member.get("affected")),
            }
            for member in members
        ],
    )
    family_relationships = relationships if relationships is not None else _relationships_from_members(members)
    relationship_params = [
        {
            "family_uuid": family_row["family_uuid"],
            "relationship_type": relationship["relationship_type"],
            "sample_uuid_a": sample_uuid_by_id[relationship["sample_id_a"]],
            "sample_uuid_b": sample_uuid_by_id[relationship["sample_id_b"]],
            "role_a": relationship.get("role_a"),
            "role_b": relationship.get("role_b"),
            "source": relationship.get("source") or "manual",
            "metadata": json.dumps(relationship.get("metadata") or {}),
        }
        for relationship in family_relationships
        if relationship.get("sample_id_a") in sample_uuid_by_id
        and relationship.get("sample_id_b") in sample_uuid_by_id
    ]
    if relationship_params:
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
            relationship_params,
        )
    if project_id is not None:
        await session.execute(
            text(
                """
                INSERT INTO family_projects (family_id, project_id)
                VALUES (CAST(:family_uuid AS uuid), CAST(:project_id AS uuid))
                ON CONFLICT DO NOTHING
                """
            ),
            {"family_uuid": family_row["family_uuid"], "project_id": project_id},
        )
        sample_project_rows = [
            {"sample_uuid": sample_row["sample_uuid"], "project_id": project_id}
            for sample_row in sample_rows
        ]
        if sample_project_rows:
            # One batched (executemany) statement instead of a per-sample await loop.
            await session.execute(
                text(
                    """
                    INSERT INTO sample_projects (sample_id, project_id)
                    VALUES (CAST(:sample_uuid AS uuid), CAST(:project_id AS uuid))
                    ON CONFLICT DO NOTHING
                    """
                ),
                sample_project_rows,
            )
    await _record_family_structure_version(
        session,
        family_uuid=family_row["family_uuid"],
        created_by=created_by,
    )
    return {"family_id": family_id, "samples": sample_ids}


async def upload_ped_data(
    session: AsyncSession,
    file: UploadFile,
    overwrite: bool,
    user: CurrentUser,
    project_id: str | None,
    roi_query: str | None = None,
    inheritance_model: str | None = None,
    obligate_carriers: str | None = None,
    proven_carriers: str | None = None,
) -> PedUploadResult:
    resolved_project_id = await _resolve_accessible_project_id(session, user, project_id)
    text_value = await decode_upload_text(file, kind="PED")
    families = _parse_ped_text(text_value)
    await _replace_existing_families(session, list(families.keys()), overwrite, user)
    sample_ids = sorted({member["iid"] for members in families.values() for member in members})
    await _ensure_sample_ids_are_available(session, sample_ids)
    inheritance = _normalize_inheritance_model(inheritance_model)
    obligate_carrier_ids = set(_split_sample_id_list(obligate_carriers))
    proven_carrier_ids = set(_split_sample_id_list(proven_carriers))
    unknown_carrier_ids = sorted((obligate_carrier_ids | proven_carrier_ids) - set(sample_ids))
    if unknown_carrier_ids:
        raise HTTPException(
            status_code=400,
            detail=f"Carrier sample IDs are not present in the PED: {', '.join(unknown_carrier_ids)}",
        )
    roi_payload: dict[str, Any] | None = None
    if roi_query and roi_query.strip():
        if resolved_project_id is None:
            raise HTTPException(status_code=400, detail="Project assignment is required when setting an ROI")
        project_result = await session.execute(
            text(
                """
                SELECT assembly_id::text AS assembly_id
                FROM projects
                WHERE id = CAST(:project_id AS uuid)
                """
            ),
            {"project_id": resolved_project_id},
        )
        assembly_id = project_result.scalar_one_or_none()
        if assembly_id is None:
            raise HTTPException(status_code=404, detail="Project assembly not found")
        from .family_service import _build_family_roi_payload

        roi_payload = await _build_family_roi_payload(
            session,
            assembly_id=str(assembly_id),
            query=roi_query,
        )
    family_metadata: dict[str, Any] = {}
    if inheritance or obligate_carrier_ids or proven_carrier_ids:
        family_metadata["pgt"] = {
            "inheritance_model": inheritance,
            "obligate_carriers": sorted(obligate_carrier_ids),
            "proven_carriers": sorted(proven_carrier_ids),
        }

    inserted: list[dict[str, Any]] = []
    for family_id, members in families.items():
        fathers = {member["pid"] for member in members if member["pid"] not in {"", "0"}}
        mothers = {member["mid"] for member in members if member["mid"] not in {"", "0"}}
        family_members: list[dict[str, Any]] = []
        for member in members:
            sample_id = member["iid"]
            role = "proband"
            if sample_id in fathers:
                role = "father"
            elif sample_id in mothers:
                role = "mother"
            elif family_members:
                role = "sibling"
            pedigree_status = _pedigree_status_from_phenotype(member["phen"])
            carrier_metadata = _carrier_annotations(
                sample_id,
                obligate_carriers=obligate_carrier_ids,
                proven_carriers=proven_carrier_ids,
            )
            family_members.append(
                {
                    "sample_id": sample_id,
                    "father_id": member["pid"] if member["pid"] not in {"", "0"} else None,
                    "mother_id": member["mid"] if member["mid"] not in {"", "0"} else None,
                    "sex": {"1": "male", "2": "female"}.get(member["sex"], "und"),
                    "role": role,
                    "clinical_status": pedigree_status,
                    "carrier_status": carrier_metadata["carrier_status"],
                    "carrier_type": carrier_metadata.get("carrier_type"),
                    "affected": pedigree_status == "affected",
                    "metadata": {},
                }
            )
        inserted.append(
            await _create_family(
                session,
                family_id=family_id,
                pedigree="\n".join(
                    " ".join(
                        [
                            family_id,
                            member["iid"],
                            member["pid"],
                            member["mid"],
                            member["sex"],
                            member["phen"],
                        ]
                    )
                    for member in members
                ),
                members=family_members,
                project_id=resolved_project_id,
                family_metadata=family_metadata,
                roi=roi_payload,
                created_by=user.id,
            )
        )
    await session.commit()
    return PedUploadResult(families=inserted)


async def create_manual_family_data(
    session: AsyncSession,
    family: ManualPedFamilyCreate,
    overwrite: bool,
    user: CurrentUser,
) -> PedUploadResult:
    resolved_project_id = await _resolve_accessible_project_id(session, user, family.project_id)
    normalized_members = _validate_manual_family(family)
    await _replace_existing_families(session, [family.family_id], overwrite, user)
    await _ensure_sample_ids_are_available(
        session,
        [member.sample_id for member in normalized_members],
    )
    roles = _resolve_manual_family_roles(normalized_members)
    inserted = await _create_family(
        session,
        family_id=family.family_id,
        family_metadata=family.metadata or None,
        pedigree=_build_ped_text_from_manual_family(family),
        members=[
            {
                "sample_id": member.sample_id,
                "father_id": member.father_id,
                "mother_id": member.mother_id,
                "sex": member.sex,
                "role": roles[member.sample_id],
                "clinical_status": member.clinical_status,
                "carrier_status": member.carrier_status,
                "carrier_type": member.carrier_type,
                "carrier_evidence": member.carrier_evidence,
                "affected": member.clinical_status == "affected",
                "metadata": member.metadata,
            }
            for member in normalized_members
        ],
        relationships=_relationships_from_members(
            [
                {
                    "sample_id": member.sample_id,
                    "father_id": member.father_id,
                    "mother_id": member.mother_id,
                }
                for member in normalized_members
            ],
            explicit_couples=[
                {
                    "partners": [partner.strip() for partner in couple.partners],
                    "context": couple.context,
                    "metadata": couple.metadata,
                    "source": "manual",
                }
                for couple in family.couples
            ],
        ),
        project_id=resolved_project_id,
        created_by=user.id,
    )
    await session.commit()
    return PedUploadResult(families=[inserted])
