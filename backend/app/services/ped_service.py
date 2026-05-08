from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import HTTPException, UploadFile
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas import ManualPedFamilyCreate, ManualPedMemberCreate, PedUploadResult
from .clickhouse_variant_storage import delete_family_small_variants, delete_family_structural_variants
from .metadata_service import CurrentUser


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


def _phenotype_code_from_affected(affected: bool) -> str:
    return "2" if affected else "1"


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
                    _phenotype_code_from_affected(member.affected),
                ]
            )
            for member in family.members
        ]
    )


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
        if user.role == "admin":
            return None
        raise HTTPException(status_code=400, detail="Project assignment is required")

    _require_uuid(normalized_project_id, "Invalid project id")
    result = await session.execute(
        text("SELECT id::text AS id FROM projects WHERE id = CAST(:project_id AS uuid)"),
        {"project_id": normalized_project_id},
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if user.role != "admin" and normalized_project_id not in _metadata_project_ids_for_user(user):
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
        normalized_members.append(
            member.model_copy(
                update={
                    "sample_id": sample_id,
                    "father_id": _normalize_parent_id(member.father_id),
                    "mother_id": _normalize_parent_id(member.mother_id),
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


def _ensure_user_can_replace_existing_families(
    existing_rows: list[dict[str, Any]],
    user: CurrentUser,
) -> None:
    del existing_rows
    if user.role == "admin":
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
    _ensure_user_can_replace_existing_families(existing_rows, user)
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


async def _create_family(
    session: AsyncSession,
    *,
    family_id: str,
    pedigree: str,
    members: list[dict[str, Any]],
    project_id: str | None,
) -> dict[str, Any]:
    created = await session.execute(
        text(
            """
            INSERT INTO families (family_id, pedigree, metadata)
            VALUES (:family_id, :pedigree, '{}'::jsonb)
            RETURNING id::text AS family_uuid, family_id
            """
        ),
        {"family_id": family_id, "pedigree": pedigree},
    )
    family_row = dict(created.mappings().one())
    sample_ids = [member["sample_id"] for member in members]
    sample_rows: list[dict[str, str]] = []
    for member in members:
        sample_result = await session.execute(
            text(
                """
                INSERT INTO samples (sample_id, family_id, sex, metadata)
                VALUES (:sample_id, CAST(:family_uuid AS uuid), :sex, '{}'::jsonb)
                RETURNING id::text AS sample_uuid, sample_id
                """
            ),
            {
                "sample_id": member["sample_id"],
                "family_uuid": family_row["family_uuid"],
                "sex": member["sex"],
            },
        )
        sample_rows.append(dict(sample_result.mappings().one()))
    sample_uuid_by_id = {row["sample_id"]: row["sample_uuid"] for row in sample_rows}
    await session.execute(
        text(
            """
            INSERT INTO family_members (family_id, sample_id, role, affected)
            VALUES (
                CAST(:family_uuid AS uuid),
                CAST(:sample_uuid AS uuid),
                :role,
                :affected
            )
            """
        ),
        [
            {
                "family_uuid": family_row["family_uuid"],
                "sample_uuid": sample_uuid_by_id[member["sample_id"]],
                "role": member["role"],
                "affected": bool(member["affected"]),
            }
            for member in members
        ],
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
        for sample_row in sample_rows:
            await session.execute(
                text(
                    """
                    INSERT INTO sample_projects (sample_id, project_id)
                    VALUES (CAST(:sample_uuid AS uuid), CAST(:project_id AS uuid))
                    ON CONFLICT DO NOTHING
                    """
                ),
                {"sample_uuid": sample_row["sample_uuid"], "project_id": project_id},
            )
    return {"family_id": family_id, "samples": sample_ids}


async def upload_ped_data(
    session: AsyncSession,
    file: UploadFile,
    overwrite: bool,
    user: CurrentUser,
    project_id: str | None,
) -> PedUploadResult:
    resolved_project_id = await _resolve_accessible_project_id(session, user, project_id)
    text_value = (await file.read()).decode()
    families = _parse_ped_text(text_value)
    await _replace_existing_families(session, list(families.keys()), overwrite, user)
    sample_ids = sorted({member["iid"] for members in families.values() for member in members})
    await _ensure_sample_ids_are_available(session, sample_ids)

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
            family_members.append(
                {
                    "sample_id": sample_id,
                    "sex": {"1": "male", "2": "female"}.get(member["sex"], "und"),
                    "role": role,
                    "affected": member["phen"] == "2",
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
        pedigree=_build_ped_text_from_manual_family(family),
        members=[
            {
                "sample_id": member.sample_id,
                "sex": member.sex,
                "role": roles[member.sample_id],
                "affected": member.affected,
            }
            for member in normalized_members
        ],
        project_id=resolved_project_id,
    )
    await session.commit()
    return PedUploadResult(families=[inserted])
