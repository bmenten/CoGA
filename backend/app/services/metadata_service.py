from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.sql import uuid_list_bindparam, uuid_values
from ..schemas import (
    AssemblyOut,
    FamilyMemberOut,
    FamilyOut,
    ProjectDashboardOut,
    ProjectOut,
    SpeciesOut,
    UserRead,
)


class CurrentUser(BaseModel):
    id: str
    username: str
    email: EmailStr
    first_name: str = ""
    last_name: str = ""
    affiliation: str = ""
    is_active: bool = True
    role: str
    projects: list[str] = Field(default_factory=list)
    metadata_project_ids: list[str] = Field(default_factory=list)
    created_at: datetime

    model_config = ConfigDict(arbitrary_types_allowed=True)


def _is_uuid(value: str | None) -> bool:
    if not value:
        return False
    try:
        UUID(str(value))
    except (TypeError, ValueError):
        return False
    return True


def _string_list(values: Iterable[Any] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        if value is None:
            continue
        text_value = str(value)
        if text_value and text_value not in result:
            result.append(text_value)
    return result


def _normalize_metadata(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _user_read_from_mapping(mapping: dict[str, Any]) -> UserRead:
    return UserRead(
        id=str(mapping["id"]),
        username=mapping["username"],
        email=mapping["email"],
        first_name=mapping.get("first_name") or "",
        last_name=mapping.get("last_name") or "",
        affiliation=mapping.get("affiliation") or "",
        is_active=bool(mapping["is_active"]),
        role=mapping["role"],
        projects=_string_list(mapping.get("metadata_project_ids")),
        created_at=mapping["created_at"],
    )


def _species_out_from_mapping(mapping: dict[str, Any]) -> SpeciesOut:
    return SpeciesOut(
        id=str(mapping["id"]),
        name=mapping["name"],
        common_name=mapping["common_name"],
        tax_id=int(mapping["tax_id"]),
    )


def _assembly_out_from_mapping(mapping: dict[str, Any]) -> AssemblyOut:
    return AssemblyOut(
        id=str(mapping["id"]),
        species_id=str(mapping["species_id"]),
        assembly_name=mapping["assembly_name"],
        version=mapping["version"],
        release_date=mapping["release_date"],
    )


def _project_out_from_mapping(mapping: dict[str, Any]) -> ProjectOut:
    return ProjectOut(
        id=str(mapping["id"]),
        name=mapping["name"],
        description=mapping.get("description"),
        species_id=str(mapping["species_id"]),
        assembly_id=str(mapping["assembly_id"]),
        user_ids=_string_list(mapping.get("user_ids")),
        metadata=_normalize_metadata(mapping.get("metadata")),
    )


def _family_out_from_mapping(
    family_mapping: dict[str, Any],
    members: list[FamilyMemberOut],
    project_ids: list[str],
) -> FamilyOut:
    roi = None
    if family_mapping.get("roi_query"):
        roi = {
            "query": family_mapping["roi_query"],
            "label": family_mapping.get("roi_label") or family_mapping["roi_query"],
            "source": family_mapping["roi_source"],
            "assembly_id": (
                str(family_mapping["roi_assembly_id"])
                if family_mapping.get("roi_assembly_id") is not None
                else None
            ),
            "chr": family_mapping["roi_chr"],
            "start": family_mapping["roi_start"],
            "end": family_mapping["roi_end"],
        }

    return FamilyOut(
        id=str(family_mapping["id"]),
        family_id=family_mapping["family_id"],
        members=members,
        pedigree=family_mapping.get("pedigree"),
        roi=roi,
        projects=project_ids,
        metadata=_normalize_metadata(family_mapping.get("metadata")),
    )


async def _fetch_user_mapping_by_email(session: AsyncSession, email: str) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            """
            SELECT
                u.id::text AS id,
                u.username,
                u.hashed_password,
                u.role,
                u.email,
                u.first_name,
                u.last_name,
                u.affiliation,
                u.is_active,
                u.created_at,
                COALESCE(
                    ARRAY_AGG(DISTINCT pu.project_id::text)
                    FILTER (WHERE pu.project_id IS NOT NULL),
                    '{}'::text[]
                ) AS metadata_project_ids
            FROM users u
            LEFT JOIN project_users pu ON pu.user_id = u.id
            WHERE u.email = :email
            GROUP BY u.id
            """
        ),
        {"email": email},
    )
    row = result.mappings().first()
    return dict(row) if row is not None else None


async def _fetch_user_mapping_by_id(session: AsyncSession, user_id: str) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            """
            SELECT
                u.id::text AS id,
                u.username,
                u.hashed_password,
                u.role,
                u.email,
                u.first_name,
                u.last_name,
                u.affiliation,
                u.is_active,
                u.created_at,
                COALESCE(
                    ARRAY_AGG(DISTINCT pu.project_id::text)
                    FILTER (WHERE pu.project_id IS NOT NULL),
                    '{}'::text[]
                ) AS metadata_project_ids
            FROM users u
            LEFT JOIN project_users pu ON pu.user_id = u.id
            WHERE u.id = CAST(:user_id AS uuid)
            GROUP BY u.id
            """
        ),
        {"user_id": user_id},
    )
    row = result.mappings().first()
    return dict(row) if row is not None else None


async def _fetch_all_user_mappings(session: AsyncSession) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT
                u.id::text AS id,
                u.username,
                u.role,
                u.email,
                u.first_name,
                u.last_name,
                u.affiliation,
                u.is_active,
                u.created_at,
                COALESCE(
                    ARRAY_AGG(DISTINCT pu.project_id::text)
                    FILTER (WHERE pu.project_id IS NOT NULL),
                    '{}'::text[]
                ) AS metadata_project_ids
            FROM users u
            LEFT JOIN project_users pu ON pu.user_id = u.id
            GROUP BY u.id
            ORDER BY lower(u.email)
            """
        )
    )
    return [dict(row) for row in result.mappings().all()]


async def get_current_user_by_email(session: AsyncSession, email: str) -> CurrentUser | None:
    mapping = await _fetch_user_mapping_by_email(session, email)
    if mapping is None:
        return None
    metadata_project_ids = _string_list(mapping.get("metadata_project_ids"))
    return CurrentUser(
        id=str(mapping["id"]),
        username=mapping["username"],
        email=mapping["email"],
        first_name=mapping.get("first_name") or "",
        last_name=mapping.get("last_name") or "",
        affiliation=mapping.get("affiliation") or "",
        is_active=bool(mapping["is_active"]),
        role=mapping["role"],
        projects=metadata_project_ids,
        metadata_project_ids=metadata_project_ids,
        created_at=mapping["created_at"],
    )


async def get_auth_user_mapping_by_email(
    session: AsyncSession,
    email: str,
) -> dict[str, Any] | None:
    return await _fetch_user_mapping_by_email(session, email)


async def create_user_account(
    session: AsyncSession,
    *,
    email: str,
    hashed_password: str,
    first_name: str,
    last_name: str,
    affiliation: str,
) -> UserRead:
    existing = await session.execute(
        text("SELECT id FROM users WHERE email = :email"),
        {"email": email},
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=400, detail="Email already registered")

    created = await session.execute(
        text(
            """
            INSERT INTO users (
                username,
                hashed_password,
                role,
                email,
                first_name,
                last_name,
                affiliation,
                is_active,
                metadata,
                created_at
            )
            VALUES (
                :username,
                :hashed_password,
                'viewer',
                :email,
                :first_name,
                :last_name,
                :affiliation,
                false,
                '{}'::jsonb,
                :created_at
            )
            RETURNING id::text
            """
        ),
        {
            "username": email,
            "hashed_password": hashed_password,
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "affiliation": affiliation,
            "created_at": datetime.now(timezone.utc),
        },
    )
    user_id = str(created.scalar_one())
    await session.commit()
    mapping = await _fetch_user_mapping_by_id(session, user_id)
    if mapping is None:
        raise HTTPException(status_code=500, detail="Failed to create user")
    return _user_read_from_mapping(mapping)


async def list_user_accounts(session: AsyncSession) -> list[UserRead]:
    return [_user_read_from_mapping(mapping) for mapping in await _fetch_all_user_mappings(session)]


async def update_user_account(
    session: AsyncSession,
    *,
    user_id: str,
    is_active: bool | None,
) -> UserRead:
    mapping = await _fetch_user_mapping_by_id(session, user_id)
    if mapping is None:
        raise HTTPException(status_code=404, detail="User not found")

    if is_active is not None:
        await session.execute(
            text("UPDATE users SET is_active = :is_active WHERE id = CAST(:user_id AS uuid)"),
            {"user_id": user_id, "is_active": is_active},
        )
        await session.commit()

    updated_mapping = await _fetch_user_mapping_by_id(session, user_id)
    if updated_mapping is None:
        raise HTTPException(status_code=404, detail="User not found")
    return _user_read_from_mapping(updated_mapping)


async def list_species_records(session: AsyncSession) -> list[SpeciesOut]:
    result = await session.execute(
        text(
            """
            SELECT id::text AS id, name, common_name, tax_id
            FROM species
            ORDER BY lower(name)
            """
        )
    )
    return [_species_out_from_mapping(dict(row)) for row in result.mappings().all()]


async def create_species_record(
    session: AsyncSession,
    *,
    name: str,
    common_name: str,
    tax_id: int,
) -> SpeciesOut:
    existing = await session.execute(
        text("SELECT id FROM species WHERE name = :name OR tax_id = :tax_id"),
        {"name": name, "tax_id": tax_id},
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Species already exists")

    created = await session.execute(
        text(
            """
            INSERT INTO species (name, common_name, tax_id)
            VALUES (:name, :common_name, :tax_id)
            RETURNING id::text AS id, name, common_name, tax_id
            """
        ),
        {"name": name, "common_name": common_name, "tax_id": tax_id},
    )
    await session.commit()
    return _species_out_from_mapping(dict(created.mappings().one()))


async def get_species_mapping(session: AsyncSession, species_id: str) -> dict[str, Any]:
    result = await session.execute(
        text(
            """
            SELECT id::text AS id, name, common_name, tax_id
            FROM species
            WHERE id = CAST(:species_id AS uuid)
            """
        ),
        {"species_id": species_id},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Species not found")
    return dict(row)


async def get_assembly_mapping(session: AsyncSession, assembly_id: str) -> dict[str, Any]:
    result = await session.execute(
        text(
            """
            SELECT
                id::text AS id,
                species_id::text AS species_id,
                assembly_name,
                version,
                release_date
            FROM assemblies
            WHERE id = CAST(:assembly_id AS uuid)
            """
        ),
        {"assembly_id": assembly_id},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Assembly not found")
    return dict(row)


async def list_assembly_records(
    session: AsyncSession,
    *,
    species_id: str | None = None,
) -> list[AssemblyOut]:
    if species_id is None:
        result = await session.execute(
            text(
                """
                SELECT
                    id::text AS id,
                    species_id::text AS species_id,
                    assembly_name,
                    version,
                    release_date
                FROM assemblies
                ORDER BY assembly_name, version
                """
            )
        )
    else:
        result = await session.execute(
            text(
                """
                SELECT
                    id::text AS id,
                    species_id::text AS species_id,
                    assembly_name,
                    version,
                    release_date
                FROM assemblies
                WHERE species_id = CAST(:species_id AS uuid)
                ORDER BY assembly_name, version
                """
            ),
            {"species_id": species_id},
        )
    return [_assembly_out_from_mapping(dict(row)) for row in result.mappings().all()]


async def create_assembly_record(
    session: AsyncSession,
    *,
    species_id: str,
    assembly_name: str,
    version: str,
    release_date: Any,
) -> AssemblyOut:
    await get_species_mapping(session, species_id)
    existing = await session.execute(
        text(
            """
            SELECT id
            FROM assemblies
            WHERE species_id = CAST(:species_id AS uuid)
              AND assembly_name = :assembly_name
              AND version = :version
            """
        ),
        {
            "species_id": species_id,
            "assembly_name": assembly_name,
            "version": version,
        },
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Assembly already exists")

    created = await session.execute(
        text(
            """
            INSERT INTO assemblies (species_id, assembly_name, version, release_date)
            VALUES (CAST(:species_id AS uuid), :assembly_name, :version, :release_date)
            RETURNING id::text AS id, species_id::text AS species_id, assembly_name, version, release_date
            """
        ),
        {
            "species_id": species_id,
            "assembly_name": assembly_name,
            "version": version,
            "release_date": release_date,
        },
    )
    await session.commit()
    return _assembly_out_from_mapping(dict(created.mappings().one()))


async def _validate_project_user_ids(session: AsyncSession, user_ids: list[str]) -> list[str]:
    deduped = list(dict.fromkeys(user_ids))
    if not deduped:
        return []
    stmt = text("SELECT id::text AS id FROM users WHERE id IN :user_ids").bindparams(
        uuid_list_bindparam("user_ids")
    )
    result = await session.execute(stmt, {"user_ids": uuid_values(deduped)})
    found = {str(row.id) for row in result}
    if len(found) != len(deduped):
        raise HTTPException(status_code=404, detail="One or more users were not found")
    return deduped


async def _validate_species_and_assembly(
    session: AsyncSession,
    species_id: str,
    assembly_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    species = await get_species_mapping(session, species_id)
    assembly = await get_assembly_mapping(session, assembly_id)
    if assembly["species_id"] != species["id"]:
        raise HTTPException(status_code=404, detail="Assembly not found")
    return species, assembly


async def _set_project_user_links(
    session: AsyncSession,
    project_id: str,
    user_ids: list[str],
) -> None:
    await session.execute(
        text("DELETE FROM project_users WHERE project_id = CAST(:project_id AS uuid)"),
        {"project_id": project_id},
    )
    if not user_ids:
        return
    for user_id in user_ids:
        await session.execute(
            text(
                """
                INSERT INTO project_users (project_id, user_id)
                VALUES (CAST(:project_id AS uuid), CAST(:user_id AS uuid))
                ON CONFLICT DO NOTHING
                """
            ),
            {"project_id": project_id, "user_id": user_id},
        )


async def _fetch_project_mappings(
    session: AsyncSession,
    *,
    project_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    if project_ids is None:
        result = await session.execute(
            text(
                """
                SELECT
                    p.id::text AS id,
                    p.name,
                    p.description,
                    p.species_id::text AS species_id,
                    p.assembly_id::text AS assembly_id,
                    p.metadata,
                    s.name AS species_name,
                    a.assembly_name,
                    a.version AS assembly_version,
                    COALESCE(
                        ARRAY_AGG(DISTINCT pu.user_id::text)
                        FILTER (WHERE pu.user_id IS NOT NULL),
                        '{}'::text[]
                    ) AS user_ids
                FROM projects p
                JOIN species s ON s.id = p.species_id
                JOIN assemblies a ON a.id = p.assembly_id
                LEFT JOIN project_users pu ON pu.project_id = p.id
                GROUP BY p.id, s.name, a.assembly_name, a.version
                ORDER BY lower(p.name)
                """
            )
        )
    else:
        if not project_ids:
            return []
        stmt = text(
            """
            SELECT
                p.id::text AS id,
                p.name,
                p.description,
                p.species_id::text AS species_id,
                p.assembly_id::text AS assembly_id,
                p.metadata,
                s.name AS species_name,
                a.assembly_name,
                a.version AS assembly_version,
                COALESCE(
                    ARRAY_AGG(DISTINCT pu.user_id::text)
                    FILTER (WHERE pu.user_id IS NOT NULL),
                    '{}'::text[]
                ) AS user_ids
            FROM projects p
            JOIN species s ON s.id = p.species_id
            JOIN assemblies a ON a.id = p.assembly_id
            LEFT JOIN project_users pu ON pu.project_id = p.id
            WHERE p.id IN :project_ids
            GROUP BY p.id, s.name, a.assembly_name, a.version
            ORDER BY lower(p.name)
            """
        ).bindparams(uuid_list_bindparam("project_ids"))
        result = await session.execute(stmt, {"project_ids": uuid_values(project_ids)})
    return [dict(row) for row in result.mappings().all()]


async def _fetch_project_mapping(session: AsyncSession, project_id: str) -> dict[str, Any]:
    rows = await _fetch_project_mappings(session, project_ids=[project_id])
    if not rows:
        raise HTTPException(status_code=404, detail="Project not found")
    return rows[0]


async def _fetch_project_family_rows(
    session: AsyncSession,
    project_ids: list[str],
) -> tuple[dict[str, FamilyOut], dict[str, list[str]]]:
    if not project_ids:
        return {}, {}

    project_family_stmt = text(
        """
        SELECT
            fp.project_id::text AS project_id,
            f.id::text AS family_uuid,
            f.family_id,
            f.pedigree,
            f.metadata,
            f.roi_query,
            f.roi_label,
            f.roi_source,
            f.roi_assembly_id::text AS roi_assembly_id,
            f.roi_chr,
            f.roi_start,
            f.roi_end
        FROM family_projects fp
        JOIN families f ON f.id = fp.family_id
        WHERE fp.project_id IN :project_ids
        ORDER BY lower(f.family_id)
        """
    ).bindparams(uuid_list_bindparam("project_ids"))
    project_family_result = await session.execute(
        project_family_stmt,
        {"project_ids": uuid_values(project_ids)},
    )
    project_family_rows = [dict(row) for row in project_family_result.mappings().all()]

    family_ids = list(dict.fromkeys(row["family_uuid"] for row in project_family_rows))
    if not family_ids:
        return {}, {}

    family_project_stmt = text(
        """
        SELECT family_id::text AS family_uuid, project_id::text AS project_id
        FROM family_projects
        WHERE family_id IN :family_ids
          AND project_id IN :project_ids
        """
    ).bindparams(uuid_list_bindparam("family_ids"), uuid_list_bindparam("project_ids"))
    family_project_result = await session.execute(
        family_project_stmt,
        {"family_ids": uuid_values(family_ids), "project_ids": uuid_values(project_ids)},
    )
    family_project_rows = [dict(row) for row in family_project_result.mappings().all()]
    family_project_ids: dict[str, list[str]] = defaultdict(list)
    for row in family_project_rows:
        family_project_ids[row["family_uuid"]].append(row["project_id"])

    member_stmt = text(
        """
        SELECT
            fm.family_id::text AS family_uuid,
            s.sample_id,
            fm.role,
            fm.affected,
            s.sex
        FROM family_members fm
        JOIN samples s ON s.id = fm.sample_id
        WHERE fm.family_id IN :family_ids
        ORDER BY lower(s.sample_id)
        """
    ).bindparams(uuid_list_bindparam("family_ids"))
    member_result = await session.execute(member_stmt, {"family_ids": uuid_values(family_ids)})
    member_rows = [dict(row) for row in member_result.mappings().all()]
    members_by_family: dict[str, list[FamilyMemberOut]] = defaultdict(list)
    for row in member_rows:
        members_by_family[row["family_uuid"]].append(
            FamilyMemberOut(
                sample_id=row["sample_id"],
                role=row["role"],
                affected=bool(row["affected"]),
                sex=row["sex"],
            )
        )

    family_out_by_id: dict[str, FamilyOut] = {}
    for row in project_family_rows:
        family_uuid = row["family_uuid"]
        if family_uuid in family_out_by_id:
            continue
        family_mapping = dict(row)
        family_mapping["id"] = family_uuid
        family_out_by_id[family_uuid] = _family_out_from_mapping(
            family_mapping,
            members_by_family.get(family_uuid, []),
            _string_list(family_project_ids.get(family_uuid)),
        )

    families_by_project: dict[str, list[str]] = defaultdict(list)
    for row in project_family_rows:
        families_by_project[row["project_id"]].append(row["family_uuid"])

    return family_out_by_id, families_by_project


async def create_project_record(
    session: AsyncSession,
    *,
    name: str,
    description: str | None,
    species_id: str,
    assembly_id: str,
    user_ids: list[str],
) -> ProjectOut:
    await _validate_species_and_assembly(session, species_id, assembly_id)
    validated_user_ids = await _validate_project_user_ids(session, user_ids)

    existing = await session.execute(
        text("SELECT id FROM projects WHERE name = :name"),
        {"name": name},
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Project already exists")

    created = await session.execute(
        text(
            """
            INSERT INTO projects (name, description, species_id, assembly_id, metadata)
            VALUES (:name, :description, CAST(:species_id AS uuid), CAST(:assembly_id AS uuid), '{}'::jsonb)
            RETURNING id::text
            """
        ),
        {
            "name": name,
            "description": description,
            "species_id": species_id,
            "assembly_id": assembly_id,
        },
    )
    project_id = str(created.scalar_one())
    await _set_project_user_links(session, project_id, validated_user_ids)
    await session.commit()
    return _project_out_from_mapping(await _fetch_project_mapping(session, project_id))


async def update_project_record(
    session: AsyncSession,
    *,
    project_id: str,
    name: str | None,
    description: str | None,
    species_id: str | None,
    assembly_id: str | None,
    user_ids: list[str] | None,
) -> ProjectOut:
    existing = await _fetch_project_mapping(session, project_id)

    updates: dict[str, Any] = {}
    if name is not None:
        name_lookup = await session.execute(
            text("SELECT id::text AS id FROM projects WHERE name = :name"),
            {"name": name},
        )
        existing_name_match = name_lookup.mappings().first()
        if existing_name_match is not None and existing_name_match["id"] != project_id:
            raise HTTPException(status_code=409, detail="Project already exists")
        updates["name"] = name
    if description is not None:
        updates["description"] = description
    if species_id is not None or assembly_id is not None:
        if not species_id or not assembly_id:
            raise HTTPException(
                status_code=400,
                detail="Species and assembly must be updated together",
            )
        await _validate_species_and_assembly(session, species_id, assembly_id)
        updates["species_id"] = species_id
        updates["assembly_id"] = assembly_id

    if updates:
        set_clause = []
        params: dict[str, Any] = {"project_id": project_id}
        for index, (field, value) in enumerate(updates.items()):
            placeholder = f"value_{index}"
            if field in {"species_id", "assembly_id"}:
                set_clause.append(f"{field} = CAST(:{placeholder} AS uuid)")
            else:
                set_clause.append(f"{field} = :{placeholder}")
            params[placeholder] = value
        await session.execute(
            text(f"UPDATE projects SET {', '.join(set_clause)} WHERE id = CAST(:project_id AS uuid)"),
            params,
        )

    validated_user_ids = (
        await _validate_project_user_ids(session, user_ids)
        if user_ids is not None
        else _string_list(existing.get("user_ids"))
    )
    await _set_project_user_links(session, project_id, validated_user_ids)
    await session.commit()
    return _project_out_from_mapping(await _fetch_project_mapping(session, project_id))


async def list_project_dashboards(
    session: AsyncSession,
    user: CurrentUser,
) -> list[ProjectDashboardOut]:
    project_ids = None if user.role == "admin" else _string_list(user.metadata_project_ids)
    project_rows = await _fetch_project_mappings(session, project_ids=project_ids)
    if not project_rows:
        return []

    family_out_by_id, families_by_project = await _fetch_project_family_rows(
        session,
        [row["id"] for row in project_rows],
    )

    dashboards: list[ProjectDashboardOut] = []
    for row in project_rows:
        linked_family_ids = families_by_project.get(row["id"], [])
        linked_families = [family_out_by_id[family_id] for family_id in linked_family_ids]
        linked_samples = list(
            dict.fromkeys(
                member.sample_id
                for family in linked_families
                for member in family.members
            )
        )
        dashboards.append(
            ProjectDashboardOut(
                id=row["id"],
                name=row["name"],
                description=row.get("description"),
                species_id=row["species_id"],
                assembly_id=row["assembly_id"],
                user_ids=_string_list(row.get("user_ids")),
                metadata=_normalize_metadata(row.get("metadata")),
                species_name=row.get("species_name"),
                assembly_name=row.get("assembly_name"),
                assembly_version=row.get("assembly_version"),
                families=linked_families,
                samples=linked_samples,
            )
        )
    return dashboards


def _user_metadata_project_ids(user: CurrentUser) -> list[str]:
    return _string_list(getattr(user, "metadata_project_ids", []))


def _visible_metadata_project_ids(project_ids: Iterable[Any] | None, user: CurrentUser) -> list[str]:
    normalized_project_ids = _string_list(project_ids)
    if user.role == "admin":
        return normalized_project_ids
    allowed_project_ids = set(_user_metadata_project_ids(user))
    return [project_id for project_id in normalized_project_ids if project_id in allowed_project_ids]


def _ensure_user_can_access_metadata_projects(project_ids: list[str], user: CurrentUser) -> None:
    if user.role == "admin":
        return
    if not set(project_ids).intersection(_user_metadata_project_ids(user)):
        raise HTTPException(status_code=403, detail="Not authorized")


async def _fetch_family_rows(
    session: AsyncSession,
    *,
    family_identifiers: list[str] | None = None,
    metadata_project_ids: list[str] | None = None,
    family_uuids: list[str] | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    bind_params = []
    params: dict[str, Any] = {}

    if family_identifiers is not None:
        if not family_identifiers:
            return []
        clauses.append("f.family_id IN :family_identifiers")
        bind_params.append(bindparam("family_identifiers", expanding=True))
        params["family_identifiers"] = family_identifiers

    if family_uuids is not None:
        if not family_uuids:
            return []
        clauses.append("f.id IN :family_uuids")
        bind_params.append(uuid_list_bindparam("family_uuids"))
        params["family_uuids"] = uuid_values(family_uuids)

    if metadata_project_ids is not None:
        if not metadata_project_ids:
            return []
        clauses.append(
            """
            EXISTS (
                SELECT 1
                FROM family_projects afp
                WHERE afp.family_id = f.id
                  AND afp.project_id IN :metadata_project_ids
            )
            """.strip()
        )
        bind_params.append(uuid_list_bindparam("metadata_project_ids"))
        params["metadata_project_ids"] = uuid_values(metadata_project_ids)

    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    stmt = text(
        f"""
        SELECT
            f.id::text AS id,
            f.family_id,
            f.pedigree,
            f.roi_query,
            f.roi_label,
            f.roi_source,
            f.roi_assembly_id::text AS roi_assembly_id,
            f.roi_chr,
            f.roi_start,
            f.roi_end,
            f.metadata,
            COALESCE(
                ARRAY_AGG(DISTINCT fp.project_id::text)
                FILTER (WHERE fp.project_id IS NOT NULL),
                '{{}}'::text[]
            ) AS project_ids
        FROM families f
        LEFT JOIN family_projects fp ON fp.family_id = f.id
        {where_clause}
        GROUP BY f.id
        ORDER BY lower(f.family_id)
        """
    )
    if bind_params:
        stmt = stmt.bindparams(*bind_params)
    result = await session.execute(stmt, params)
    return [dict(row) for row in result.mappings().all()]


async def _fetch_family_sample_rows(
    session: AsyncSession,
    family_uuids: list[str],
) -> dict[str, list[dict[str, Any]]]:
    if not family_uuids:
        return {}

    stmt = text(
        """
        SELECT
            fm.family_id::text AS family_uuid,
            s.id::text AS sample_uuid,
            s.sample_id,
            s.sex,
            fm.role,
            fm.affected
        FROM family_members fm
        JOIN samples s ON s.id = fm.sample_id
        WHERE fm.family_id IN :family_uuids
        ORDER BY lower(s.sample_id)
        """
    ).bindparams(uuid_list_bindparam("family_uuids"))
    result = await session.execute(stmt, {"family_uuids": uuid_values(family_uuids)})
    rows_by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in result.mappings().all():
        payload = dict(row)
        rows_by_family[payload["family_uuid"]].append(payload)
    return rows_by_family


def _family_out_from_rows(
    family_row: dict[str, Any],
    sample_rows: list[dict[str, Any]],
    project_ids: list[str] | None = None,
) -> FamilyOut:
    members = [
        FamilyMemberOut(
            sample_id=row["sample_id"],
            role=row["role"],
            affected=bool(row["affected"]),
            sex=row["sex"],
        )
        for row in sample_rows
    ]
    return _family_out_from_mapping(
        family_row,
        members,
        _string_list(project_ids if project_ids is not None else family_row.get("project_ids")),
    )


async def list_family_records(
    session: AsyncSession,
    user: CurrentUser,
) -> list[FamilyOut]:
    family_rows = await _fetch_family_rows(
        session,
        metadata_project_ids=None if user.role == "admin" else _user_metadata_project_ids(user),
    )
    sample_rows_by_family = await _fetch_family_sample_rows(session, [row["id"] for row in family_rows])
    return [
        _family_out_from_rows(
            row,
            sample_rows_by_family.get(row["id"], []),
            _visible_metadata_project_ids(row.get("project_ids"), user),
        )
        for row in family_rows
    ]


async def get_accessible_family_mapping(
    session: AsyncSession,
    family_identifier: str,
    user: CurrentUser,
) -> dict[str, Any]:
    rows = await _fetch_family_rows(session, family_identifiers=[family_identifier])
    if not rows:
        raise HTTPException(status_code=404, detail="Family not found")
    family_row = rows[0]
    _ensure_user_can_access_metadata_projects(_string_list(family_row.get("project_ids")), user)
    return family_row


async def get_family_record(
    session: AsyncSession,
    family_identifier: str,
    user: CurrentUser,
) -> FamilyOut:
    family_row = await get_accessible_family_mapping(session, family_identifier, user)
    sample_rows_by_family = await _fetch_family_sample_rows(session, [family_row["id"]])
    return _family_out_from_rows(
        family_row,
        sample_rows_by_family.get(family_row["id"], []),
        _visible_metadata_project_ids(family_row.get("project_ids"), user),
    )


async def list_family_project_assignments(
    session: AsyncSession,
) -> list[dict[str, Any]]:
    family_rows = await _fetch_family_rows(session)
    sample_rows_by_family = await _fetch_family_sample_rows(session, [row["id"] for row in family_rows])
    assignments: list[dict[str, Any]] = []
    for row in family_rows:
        project_ids = _string_list(row.get("project_ids"))
        assignments.append(
            {
                "family_id": row["family_id"],
                "projects": project_ids,
                "samples": [
                    {"sample_id": sample_row["sample_id"], "projects": project_ids}
                    for sample_row in sample_rows_by_family.get(row["id"], [])
                ],
            }
        )
    return assignments


async def _validate_project_ids(session: AsyncSession, project_ids: list[str]) -> list[str]:
    deduped = list(dict.fromkeys(project_ids))
    if not deduped:
        return []
    for project_id in deduped:
        if not _is_uuid(project_id):
            raise HTTPException(status_code=400, detail=f"Invalid project id: {project_id}")
    existing = await _fetch_project_mappings(session, project_ids=deduped)
    if len(existing) != len(deduped):
        raise HTTPException(status_code=404, detail="One or more projects were not found")
    return deduped


async def _family_sample_uuid_rows(
    session: AsyncSession,
    family_uuid: str,
) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT id::text AS id, sample_id
            FROM samples
            WHERE family_id = CAST(:family_uuid AS uuid)
            ORDER BY lower(sample_id)
            """
        ),
        {"family_uuid": family_uuid},
    )
    return [dict(row) for row in result.mappings().all()]


async def update_family_project_assignments(
    session: AsyncSession,
    family_identifier: str,
    project_ids: list[str],
) -> dict[str, Any]:
    family_rows = await _fetch_family_rows(session, family_identifiers=[family_identifier])
    if not family_rows:
        raise HTTPException(status_code=404, detail="Family not found")
    family_row = family_rows[0]
    validated_project_ids = await _validate_project_ids(session, project_ids)
    sample_rows = await _family_sample_uuid_rows(session, family_row["id"])

    await session.execute(
        text("DELETE FROM family_projects WHERE family_id = CAST(:family_uuid AS uuid)"),
        {"family_uuid": family_row["id"]},
    )
    for project_id in validated_project_ids:
        await session.execute(
            text(
                """
                INSERT INTO family_projects (family_id, project_id)
                VALUES (CAST(:family_uuid AS uuid), CAST(:project_id AS uuid))
                ON CONFLICT DO NOTHING
                """
            ),
            {"family_uuid": family_row["id"], "project_id": project_id},
        )

    sample_ids = [row["id"] for row in sample_rows]
    if sample_ids:
        await session.execute(
            text("DELETE FROM sample_projects WHERE sample_id IN :sample_ids").bindparams(
                uuid_list_bindparam("sample_ids")
            ),
            {"sample_ids": uuid_values(sample_ids)},
        )
        for sample_id in sample_ids:
            for project_id in validated_project_ids:
                await session.execute(
                    text(
                        """
                        INSERT INTO sample_projects (sample_id, project_id)
                        VALUES (CAST(:sample_id AS uuid), CAST(:project_id AS uuid))
                        ON CONFLICT DO NOTHING
                        """
                    ),
                    {"sample_id": sample_id, "project_id": project_id},
                )

    await session.commit()
    return {
        "family_id": family_identifier,
        "project_ids": validated_project_ids,
        "message": "Family projects updated",
    }


async def _fetch_sample_access_mapping(
    session: AsyncSession,
    sample_identifier: str,
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            """
            SELECT
                s.id::text AS id,
                s.sample_id,
                s.sex,
                f.id::text AS family_uuid,
                f.family_id,
                COALESCE(
                    ARRAY_AGG(DISTINCT fp.project_id::text)
                    FILTER (WHERE fp.project_id IS NOT NULL),
                    '{}'::text[]
                ) AS family_project_ids
            FROM samples s
            JOIN families f ON f.id = s.family_id
            LEFT JOIN family_projects fp ON fp.family_id = f.id
            WHERE s.sample_id = :sample_id
            GROUP BY s.id, f.id
            """
        ),
        {"sample_id": sample_identifier},
    )
    row = result.mappings().first()
    return dict(row) if row is not None else None


async def get_accessible_sample_mapping(
    session: AsyncSession,
    sample_identifier: str,
    user: CurrentUser,
) -> dict[str, Any]:
    mapping = await _fetch_sample_access_mapping(session, sample_identifier)
    if mapping is None:
        raise HTTPException(status_code=404, detail="Sample not found")
    _ensure_user_can_access_metadata_projects(_string_list(mapping.get("family_project_ids")), user)
    return mapping


async def delete_project_record(session: AsyncSession, *, project_id: str) -> None:
    await _fetch_project_mapping(session, project_id)
    await session.execute(
        text("DELETE FROM projects WHERE id = CAST(:project_id AS uuid)"),
        {"project_id": project_id},
    )
    await session.commit()


async def update_family_roi_record(
    session: AsyncSession,
    *,
    family_identifier: str,
    roi: dict[str, Any] | None,
) -> None:
    family_rows = await _fetch_family_rows(session, family_identifiers=[family_identifier])
    if not family_rows:
        raise HTTPException(status_code=404, detail="Family not found")
    family_row = family_rows[0]

    roi_assembly_id = None
    if roi and roi.get("assembly_id") is not None:
        roi_assembly_id = str(roi["assembly_id"])
        if not _is_uuid(roi_assembly_id):
            raise HTTPException(status_code=400, detail="ROI assembly is invalid")
        await get_assembly_mapping(session, roi_assembly_id)

    await session.execute(
        text(
            """
            UPDATE families
            SET
                roi_query = :roi_query,
                roi_label = :roi_label,
                roi_source = :roi_source,
                roi_assembly_id = CAST(:roi_assembly_id AS uuid),
                roi_chr = :roi_chr,
                roi_start = :roi_start,
                roi_end = :roi_end
            WHERE id = CAST(:family_id AS uuid)
            """
        ),
        {
            "family_id": family_row["id"],
            "roi_query": roi.get("query") if roi else None,
            "roi_label": roi.get("label") if roi else None,
            "roi_source": roi.get("source") if roi else None,
            "roi_assembly_id": roi_assembly_id,
            "roi_chr": roi.get("chr") if roi else None,
            "roi_start": roi.get("start") if roi else None,
            "roi_end": roi.get("end") if roi else None,
        },
    )
    await session.commit()
