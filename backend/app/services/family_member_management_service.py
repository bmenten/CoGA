from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas import (
    FamilyMemberDeleteOut,
    FamilyMemberBatchUpdate,
    FamilyMemberBatchUpdateOut,
    FamilyMemberBatchUpdateItem,
    FamilyMemberDetailOut,
    FamilyMemberImpactOut,
    FamilyMemberOut,
    FamilyMemberUpdate,
    FamilyMemberUpdateOut,
    FamilyStructureCoupleUpdate,
    FamilyStructureMemberUpdate,
    FamilyStructureParentChildUpdate,
    FamilyStructureRelationshipsUpdate,
    FamilyStructureUpdate,
)
from .clickhouse_variant_storage import (
    count_family_small_variants_by_sample,
    count_family_structural_variants_by_sample,
)
from .family_structure_service import (
    _clean_sample_id,
    _family_assembly_groups,
    _sample_key,
    update_family_structure_for_admin,
)
from .hpo_service import list_family_hpo_annotations
from .metadata_service import get_accessible_family_mapping, get_family_record
from .access_control import CurrentUser

GENOMIC_DATA_KEYS = {
    "small_variants",
    "structural_variants",
    "repeat_expansions",
    "paraphase",
    "coverage",
    "segments",
    "apcad",
    "apcad_pcf",
    "haplotype",
}


def _payload_has_field(payload: Any, field_name: str) -> bool:
    fields_set = getattr(payload, "model_fields_set", None)
    return isinstance(fields_set, set) and field_name in fields_set


def _find_member(family_members: list[FamilyMemberOut], sample_id: str) -> FamilyMemberOut:
    key = _sample_key(sample_id)
    for member in family_members:
        if _sample_key(member.sample_id) == key:
            return member
    raise HTTPException(status_code=404, detail="Family member was not found")


def _parents_for_member(
    relationships: list[Any],
    *,
    sample_id: str,
) -> tuple[str | None, str | None]:
    father_id: str | None = None
    mother_id: str | None = None
    child_key = _sample_key(sample_id)
    for relationship in relationships:
        if relationship.relationship_type != "parent_child":
            continue
        if _sample_key(relationship.sample_id_b) != child_key:
            continue
        if relationship.role_a == "father":
            father_id = relationship.sample_id_a
        elif relationship.role_a == "mother":
            mother_id = relationship.sample_id_a
    return father_id, mother_id


async def _sample_uuid_for_member(
    session: AsyncSession,
    *,
    family_uuid: str,
    sample_id: str,
) -> tuple[str, str]:
    result = await session.execute(
        text(
            """
            SELECT s.id::text AS sample_uuid, s.sample_id
            FROM family_members fm
            JOIN samples s ON s.id = fm.sample_id
            WHERE fm.family_id = CAST(:family_uuid AS uuid)
              AND lower(s.sample_id) = :sample_key
            """
        ),
        {"family_uuid": family_uuid, "sample_key": _sample_key(sample_id)},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Family member was not found")
    return str(row["sample_uuid"]), str(row["sample_id"])


async def _ensure_sample_id_available(
    session: AsyncSession,
    *,
    sample_uuid: str,
    new_sample_id: str,
) -> None:
    result = await session.execute(
        text(
            """
            SELECT sample_id
            FROM samples
            WHERE lower(sample_id) = :sample_key
              AND id <> CAST(:sample_uuid AS uuid)
            """
        ),
        {"sample_key": _sample_key(new_sample_id), "sample_uuid": sample_uuid},
    )
    if result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail=f"Sample id already exists: {new_sample_id}")


async def _sample_postgres_counts(
    session: AsyncSession,
    *,
    family_uuid: str,
    sample_uuid: str,
) -> tuple[dict[str, int], dict[str, int]]:
    result = await session.execute(
        text(
            """
            SELECT 'as_parent' AS key, COUNT(*)::bigint AS count
            FROM family_relationships
            WHERE family_id = CAST(:family_uuid AS uuid)
              AND sample_id_a = CAST(:sample_uuid AS uuid)
              AND relationship_type = 'parent_child'
              AND active
            UNION ALL
            SELECT 'as_child', COUNT(*)::bigint
            FROM family_relationships
            WHERE family_id = CAST(:family_uuid AS uuid)
              AND sample_id_b = CAST(:sample_uuid AS uuid)
              AND relationship_type = 'parent_child'
              AND active
            UNION ALL
            SELECT 'as_partner', COUNT(*)::bigint
            FROM family_relationships
            WHERE family_id = CAST(:family_uuid AS uuid)
              AND (sample_id_a = CAST(:sample_uuid AS uuid) OR sample_id_b = CAST(:sample_uuid AS uuid))
              AND relationship_type = 'couple'
              AND active
            UNION ALL
            SELECT 'hpo_annotations', COUNT(*)::bigint
            FROM individual_hpo
            WHERE family_id = CAST(:family_uuid AS uuid)
              AND sample_id = CAST(:sample_uuid AS uuid)
            UNION ALL
            SELECT 'sample_projects', COUNT(*)::bigint
            FROM sample_projects
            WHERE sample_id = CAST(:sample_uuid AS uuid)
            UNION ALL
            SELECT 'repeat_expansions', COUNT(*)::bigint
            FROM repeat_expansions
            WHERE family_id = CAST(:family_uuid AS uuid)
              AND sample_id = CAST(:sample_uuid AS uuid)
            UNION ALL
            SELECT 'paraphase', COUNT(*)::bigint
            FROM sample_paraphase_results
            WHERE family_id = CAST(:family_uuid AS uuid)
              AND sample_id = CAST(:sample_uuid AS uuid)
            """
        ),
        {"family_uuid": family_uuid, "sample_uuid": sample_uuid},
    )
    rows = {str(row["key"]): int(row["count"] or 0) for row in result.mappings().all()}
    pedigree_counts = {
        "as_parent": rows.pop("as_parent", 0),
        "as_child": rows.pop("as_child", 0),
        "as_partner": rows.pop("as_partner", 0),
    }

    track_result = await session.execute(
        text(
            """
            SELECT
                track_type,
                GREATEST(COALESCE(SUM(row_count), 0), COUNT(*))::bigint AS count
            FROM sample_interval_track_sources
            WHERE family_id = CAST(:family_uuid AS uuid)
              AND sample_id = CAST(:sample_uuid AS uuid)
            GROUP BY track_type
            """
        ),
        {"family_uuid": family_uuid, "sample_uuid": sample_uuid},
    )
    for row in track_result.mappings().all():
        rows[str(row["track_type"])] = int(row["count"] or 0)
    return pedigree_counts, rows


async def _family_review_counts(
    session: AsyncSession,
    *,
    family_uuid: str,
) -> dict[str, int]:
    result = await session.execute(
        text(
            """
            SELECT 'small_variant_reviews' AS key, COUNT(*)::bigint AS count
            FROM small_variant_reviews
            WHERE family_id = CAST(:family_uuid AS uuid)
            UNION ALL
            SELECT 'structural_variant_reviews', COUNT(*)::bigint
            FROM structural_variant_reviews
            WHERE family_id = CAST(:family_uuid AS uuid)
            """
        ),
        {"family_uuid": family_uuid},
    )
    return {str(row["key"]): int(row["count"] or 0) for row in result.mappings().all()}


async def _sample_clickhouse_counts(
    session: AsyncSession,
    *,
    family_uuid: str,
    sample_id: str,
) -> dict[str, int]:
    counts = {"small_variants": 0, "structural_variants": 0}
    for assembly_name, project_ids in (await _family_assembly_groups(session, family_uuid=family_uuid)).items():
        small_counts = await count_family_small_variants_by_sample(
            assembly_name,
            family_uuid,
            sample_ids=[sample_id],
            project_ids=project_ids,
        )
        structural_counts = await count_family_structural_variants_by_sample(
            assembly_name,
            family_uuid,
            sample_ids=[sample_id],
            project_ids=project_ids,
        )
        counts["small_variants"] += small_counts.get(sample_id, 0)
        counts["structural_variants"] += structural_counts.get(sample_id, 0)
    return counts


def _impact_scopes(
    *,
    pedigree_counts: dict[str, int],
    data_counts: dict[str, int],
) -> tuple[list[str], list[str], list[str]]:
    scopes: list[str] = []
    stale_scopes: list[str] = []
    warnings: list[str] = []
    if sum(pedigree_counts.values()) > 0:
        scopes.extend(["pedigree", "segregation", "haplotype-phasing", "embryo-classification"])
        stale_scopes.extend(["pedigree", "segregation", "haplotypes", "phasing"])
        warnings.append("This individual is referenced by active pedigree relationships.")
    if data_counts.get("small_variants", 0) > 0 or data_counts.get("structural_variants", 0) > 0:
        scopes.extend(["vcf-genotypes", "segregation", "variant-interpretation"])
        stale_scopes.extend(["segregation", "inheritance-filtering", "variant-interpretation"])
        warnings.append("This individual has linked genotype calls in imported variant data.")
    if data_counts.get("haplotype", 0) > 0:
        scopes.extend(["haplotype-phasing", "shared-haplotype-calculations"])
        stale_scopes.extend(["haplotypes", "phasing"])
        warnings.append("This individual has haplotype track data.")
    if data_counts.get("coverage", 0) > 0 or data_counts.get("apcad", 0) > 0 or data_counts.get("apcad_pcf", 0) > 0:
        scopes.extend(["coverage", "apcad"])
        stale_scopes.extend(["coverage", "apcad"])
        warnings.append("This individual has coverage or APCAD-derived data.")
    if data_counts.get("repeat_expansions", 0) > 0:
        scopes.append("repeat-expansions")
        stale_scopes.append("repeat-expansions")
    if data_counts.get("paraphase", 0) > 0:
        scopes.append("paraphase")
        stale_scopes.append("paraphase")
    if data_counts.get("hpo_annotations", 0) > 0:
        scopes.extend(["phenotype-annotations", "phenotype-driven-filtering"])
        stale_scopes.extend(["segregation", "inheritance-filtering"])
    if data_counts.get("small_variant_reviews", 0) > 0 or data_counts.get("structural_variant_reviews", 0) > 0:
        scopes.append("saved-variant-reviews")
        warnings.append("Family-level saved variant reviews may need reassessment after this change.")
    return (
        list(dict.fromkeys(scopes)),
        list(dict.fromkeys(stale_scopes)),
        warnings,
    )


async def get_family_member_impact_for_user(
    session: AsyncSession,
    *,
    family_id: str,
    sample_id: str,
    user: CurrentUser,
    family_uuid: str | None = None,
) -> FamilyMemberImpactOut:
    # When the caller has already resolved the family (and checked access) it
    # can thread the UUID down to skip re-running the family-mapping GROUP BY.
    if family_uuid is None:
        family_row = await get_accessible_family_mapping(session, family_id, user)
        family_uuid = str(family_row["id"])
    sample_uuid, resolved_sample_id = await _sample_uuid_for_member(
        session,
        family_uuid=family_uuid,
        sample_id=sample_id,
    )
    pedigree_counts, data_counts = await _sample_postgres_counts(
        session,
        family_uuid=family_uuid,
        sample_uuid=sample_uuid,
    )
    data_counts.update(await _family_review_counts(session, family_uuid=family_uuid))
    try:
        data_counts.update(
            await _sample_clickhouse_counts(
                session,
                family_uuid=family_uuid,
                sample_id=resolved_sample_id,
            )
        )
    except Exception:
        data_counts["clickhouse_counts_unavailable"] = 1
    data_counts = {key: count for key, count in data_counts.items() if count > 0}
    pedigree_counts = {key: count for key, count in pedigree_counts.items() if count > 0}
    scopes, stale_scopes, warnings = _impact_scopes(
        pedigree_counts=pedigree_counts,
        data_counts=data_counts,
    )
    if data_counts.get("clickhouse_counts_unavailable"):
        warnings.append("ClickHouse genotype counts could not be verified; treat genotype linkage as unknown.")
    destructive = bool(data_counts or pedigree_counts)
    return FamilyMemberImpactOut(
        sample_id=resolved_sample_id,
        pedigree_references=pedigree_counts,
        data_counts=data_counts,
        affected_analysis_scopes=scopes,
        stale_analysis_scopes=stale_scopes,
        warnings=warnings,
        destructive=destructive,
        requires_manual_recalculation=bool(stale_scopes),
    )


async def get_family_member_detail_for_user(
    session: AsyncSession,
    *,
    family_id: str,
    sample_id: str,
    user: CurrentUser,
) -> FamilyMemberDetailOut:
    family = await get_family_record(session, family_id, user)
    member = _find_member(family.members, sample_id)
    father_id, mother_id = _parents_for_member(family.relationships, sample_id=member.sample_id)
    # get_family_record already resolved the family (and checked access); reuse
    # its UUID instead of re-running the family-mapping GROUP BY for the HPO
    # lookup and again inside the impact call.
    family_uuid = str(family.id)
    hpo_annotations = await list_family_hpo_annotations(
        session,
        family_uuid=family_uuid,
        sample_id=member.sample_id,
    )
    impact = await get_family_member_impact_for_user(
        session,
        family_id=family_id,
        sample_id=member.sample_id,
        user=user,
        family_uuid=family_uuid,
    )
    return FamilyMemberDetailOut(
        member=member,
        father_id=father_id,
        mother_id=mother_id,
        hpo_annotations=hpo_annotations,
        impact=impact,
    )


def _relationship_payload_for_member_update(
    family_relationships: list[Any],
    *,
    sample_id: str,
    update: FamilyMemberUpdate,
) -> FamilyStructureRelationshipsUpdate | None:
    replace_father = _payload_has_field(update, "father_id")
    replace_mother = _payload_has_field(update, "mother_id")
    if not replace_father and not replace_mother:
        return None

    target_key = _sample_key(sample_id)
    parent_child: list[FamilyStructureParentChildUpdate] = []
    couples: list[FamilyStructureCoupleUpdate] = []
    for relationship in family_relationships:
        if relationship.relationship_type == "parent_child":
            if _sample_key(relationship.sample_id_b) == target_key:
                if relationship.role_a == "father" and replace_father:
                    continue
                if relationship.role_a == "mother" and replace_mother:
                    continue
            parent_child.append(
                FamilyStructureParentChildUpdate(
                    parent=relationship.sample_id_a,
                    child=relationship.sample_id_b,
                    parent_role=relationship.role_a
                    if relationship.role_a in {"father", "mother", "parent"}
                    else "parent",
                    metadata=relationship.metadata or {},
                )
            )
        elif relationship.relationship_type == "couple":
            couples.append(
                FamilyStructureCoupleUpdate(
                    partners=[relationship.sample_id_a, relationship.sample_id_b],
                    context=(relationship.metadata or {}).get("context"),
                    metadata=relationship.metadata or {},
                )
            )

    if replace_father and update.father_id:
        parent_child.append(
            FamilyStructureParentChildUpdate(
                parent=_clean_sample_id(update.father_id, field_name="father_id"),
                child=sample_id,
                parent_role="father",
                metadata={},
            )
        )
    if replace_mother and update.mother_id:
        parent_child.append(
            FamilyStructureParentChildUpdate(
                parent=_clean_sample_id(update.mother_id, field_name="mother_id"),
                child=sample_id,
                parent_role="mother",
                metadata={},
            )
        )
    return FamilyStructureRelationshipsUpdate(parent_child=parent_child, couples=couples)


def _apply_status_fields(values: dict[str, Any], update: Any) -> None:
    """Copy the independent phenotype/carrier status fields when set.

    clinical_status (phenotype) and carrier_status (genotype) are stored
    separately; carrier_type only applies to carriers.
    """

    if _payload_has_field(update, "clinical_status"):
        values["clinical_status"] = update.clinical_status
    if _payload_has_field(update, "carrier_status"):
        values["carrier_status"] = update.carrier_status
    if _payload_has_field(update, "carrier_type"):
        values["carrier_type"] = update.carrier_type


def _member_update_payload(
    *,
    sample_id: str,
    update: FamilyMemberUpdate,
) -> FamilyStructureMemberUpdate:
    values: dict[str, Any] = {"sample_id": sample_id}
    if _payload_has_field(update, "sex"):
        values["sex"] = update.sex
    if _payload_has_field(update, "role"):
        values["role"] = update.role
    _apply_status_fields(values, update)
    return FamilyStructureMemberUpdate(**values)


def _batch_member_update_payload(
    *,
    sample_id: str,
    update: FamilyMemberBatchUpdateItem,
) -> FamilyStructureMemberUpdate:
    values: dict[str, Any] = {"sample_id": sample_id}
    if _payload_has_field(update, "sex"):
        values["sex"] = update.sex
    if _payload_has_field(update, "role"):
        values["role"] = update.role
    _apply_status_fields(values, update)
    return FamilyStructureMemberUpdate(**values)


def _current_relationships_update(family: Any) -> FamilyStructureRelationshipsUpdate:
    parent_child: list[FamilyStructureParentChildUpdate] = []
    couples: list[FamilyStructureCoupleUpdate] = []
    for relationship in family.relationships:
        if relationship.relationship_type == "parent_child":
            parent_child.append(
                FamilyStructureParentChildUpdate(
                    parent=relationship.sample_id_a,
                    child=relationship.sample_id_b,
                    parent_role=relationship.role_a
                    if relationship.role_a in {"father", "mother", "parent"}
                    else "parent",
                    metadata=relationship.metadata or {},
                )
            )
        elif relationship.relationship_type == "couple":
            couples.append(
                FamilyStructureCoupleUpdate(
                    partners=[relationship.sample_id_a, relationship.sample_id_b],
                    context=(relationship.metadata or {}).get("context"),
                    metadata=relationship.metadata or {},
                )
            )
    return FamilyStructureRelationshipsUpdate(parent_child=parent_child, couples=couples)


def _replace_sample_id_in_relationships(
    relationships: FamilyStructureRelationshipsUpdate,
    *,
    old_sample_id: str,
    new_sample_id: str,
) -> None:
    for relationship in relationships.parent_child:
        if _sample_key(relationship.parent) == _sample_key(old_sample_id):
            relationship.parent = new_sample_id
        if _sample_key(relationship.child) == _sample_key(old_sample_id):
            relationship.child = new_sample_id
    for relationship in relationships.couples:
        relationship.partners = [
            new_sample_id if _sample_key(partner) == _sample_key(old_sample_id) else partner
            for partner in relationship.partners
        ]


def _apply_parent_updates(
    relationships: FamilyStructureRelationshipsUpdate,
    *,
    child_id: str,
    update: FamilyMemberBatchUpdateItem,
) -> None:
    replace_father = _payload_has_field(update, "father_id")
    replace_mother = _payload_has_field(update, "mother_id")
    if not replace_father and not replace_mother:
        return
    child_key = _sample_key(child_id)
    relationships.parent_child = [
        relationship
        for relationship in relationships.parent_child
        if not (
            _sample_key(relationship.child) == child_key
            and (
                (relationship.parent_role == "father" and replace_father)
                or (relationship.parent_role == "mother" and replace_mother)
            )
        )
    ]
    if replace_father and update.father_id:
        relationships.parent_child.append(
            FamilyStructureParentChildUpdate(
                parent=_clean_sample_id(update.father_id, field_name="father_id"),
                child=child_id,
                parent_role="father",
                metadata={},
            )
        )
    if replace_mother and update.mother_id:
        relationships.parent_child.append(
            FamilyStructureParentChildUpdate(
                parent=_clean_sample_id(update.mother_id, field_name="mother_id"),
                child=child_id,
                parent_role="mother",
                metadata={},
            )
        )


def _genomic_data_count(counts: dict[str, int]) -> int:
    return sum(count for key, count in counts.items() if key in GENOMIC_DATA_KEYS)


def _rename_block_reason(counts: dict[str, int]) -> str | None:
    """Why renaming this member would risk orphaning genomic data keyed by the old
    sample id, or None if it is safe.

    Fails CLOSED: if the ClickHouse genotype counts could not be verified (ClickHouse
    unreachable), we cannot confirm there is nothing to orphan, so the rename is blocked
    rather than allowed. Previously the ``clickhouse_counts_unavailable`` marker was not
    in GENOMIC_DATA_KEYS, so the guard summed to 0 and the rename proceeded — silently
    orphaning ClickHouse variant rows still keyed by the old sample-id string.
    """
    if _genomic_data_count(counts) > 0:
        return (
            "Renaming a family member with imported genomic data is blocked because raw "
            "datasets are preserved and still use the source sample id."
        )
    if counts.get("clickhouse_counts_unavailable"):
        return (
            "Renaming is blocked because this sample's ClickHouse genotype linkage could "
            "not be verified (ClickHouse unreachable) — a rename could silently orphan "
            "variant data keyed by the old sample id. Retry when ClickHouse is reachable."
        )
    return None


async def update_family_member_for_admin(
    session: AsyncSession,
    *,
    family_id: str,
    sample_id: str,
    update: FamilyMemberUpdate,
    user: CurrentUser,
) -> FamilyMemberUpdateOut:
    old_sample_id = _clean_sample_id(sample_id)
    family_row = await get_accessible_family_mapping(session, family_id, user)
    family_uuid = str(family_row["id"])
    sample_uuid, resolved_sample_id = await _sample_uuid_for_member(
        session,
        family_uuid=family_uuid,
        sample_id=old_sample_id,
    )
    family = await get_family_record(session, family_id, user)
    _find_member(family.members, resolved_sample_id)

    new_sample_id = resolved_sample_id
    if _payload_has_field(update, "sample_id") and update.sample_id is not None:
        new_sample_id = _clean_sample_id(update.sample_id)
        if _sample_key(new_sample_id) != _sample_key(resolved_sample_id):
            await _ensure_sample_id_available(
                session,
                sample_uuid=sample_uuid,
                new_sample_id=new_sample_id,
            )
            impact = await get_family_member_impact_for_user(
                session,
                family_id=family_id,
                sample_id=resolved_sample_id,
                user=user,
            )
            block_reason = _rename_block_reason(impact.data_counts)
            if block_reason is not None:
                raise HTTPException(
                    status_code=409,
                    detail={"message": block_reason, "impact": impact.model_dump()},
                )
            await session.execute(
                text(
                    """
                    UPDATE samples
                    SET sample_id = :sample_id
                    WHERE id = CAST(:sample_uuid AS uuid)
                    """
                ),
                {"sample_uuid": sample_uuid, "sample_id": new_sample_id},
            )
            family = await get_family_record(session, family_id, user)

    structure_update = FamilyStructureUpdate(
        expected_structure_version=update.expected_structure_version,
        change_reason=update.change_reason or "family_member_detail",
        clear_existing_genomic_data=False,
        members=[_member_update_payload(sample_id=new_sample_id, update=update)],
        relationships=_relationship_payload_for_member_update(
            family.relationships,
            sample_id=new_sample_id,
            update=update,
        ),
    )
    response = await update_family_structure_for_admin(
        session,
        family_id=family_id,
        update=structure_update,
        user=user,
    )
    member = _find_member(response.family.members, new_sample_id)
    father_id, mother_id = _parents_for_member(response.family.relationships, sample_id=member.sample_id)
    impact = await get_family_member_impact_for_user(
        session,
        family_id=family_id,
        sample_id=member.sample_id,
        user=user,
    )
    return FamilyMemberUpdateOut(
        family=response.family,
        member=member,
        father_id=father_id,
        mother_id=mother_id,
        impact=impact,
        warnings=response.warnings,
        stale_analysis_scopes=response.stale_analysis_scopes,
        data_counts=response.data_counts or impact.data_counts,
        cleared_data_counts=response.cleared_data_counts,
    )


async def update_family_members_batch_for_admin(
    session: AsyncSession,
    *,
    family_id: str,
    update: FamilyMemberBatchUpdate,
    user: CurrentUser,
) -> FamilyMemberBatchUpdateOut:
    if not update.updates:
        family = await get_family_record(session, family_id, user)
        return FamilyMemberBatchUpdateOut(family=family)

    family_row = await get_accessible_family_mapping(session, family_id, user)
    family_uuid = str(family_row["id"])
    family = await get_family_record(session, family_id, user)
    relationships_update = _current_relationships_update(family)
    member_updates: list[FamilyStructureMemberUpdate] = []
    seen: set[str] = set()
    relationships_changed = False

    for item in update.updates:
        current_sample_id = _clean_sample_id(item.sample_id)
        current_key = _sample_key(current_sample_id)
        if current_key in seen:
            raise HTTPException(status_code=400, detail="Batch member updates must be unique by sample id")
        seen.add(current_key)
        _find_member(family.members, current_sample_id)
        target_sample_id = current_sample_id
        if _payload_has_field(item, "new_sample_id") and item.new_sample_id:
            requested_sample_id = _clean_sample_id(item.new_sample_id, field_name="new_sample_id")
            if _sample_key(requested_sample_id) != current_key:
                sample_uuid, resolved_sample_id = await _sample_uuid_for_member(
                    session,
                    family_uuid=family_uuid,
                    sample_id=current_sample_id,
                )
                await _ensure_sample_id_available(
                    session,
                    sample_uuid=sample_uuid,
                    new_sample_id=requested_sample_id,
                )
                impact = await get_family_member_impact_for_user(
                    session,
                    family_id=family_id,
                    sample_id=resolved_sample_id,
                    user=user,
                )
                block_reason = _rename_block_reason(impact.data_counts)
                if block_reason is not None:
                    raise HTTPException(
                        status_code=409,
                        detail={"message": block_reason, "impact": impact.model_dump()},
                    )
                await session.execute(
                    text(
                        """
                        UPDATE samples
                        SET sample_id = :sample_id
                        WHERE id = CAST(:sample_uuid AS uuid)
                        """
                    ),
                    {
                        "sample_uuid": sample_uuid,
                        "sample_id": requested_sample_id,
                    },
                )
                _replace_sample_id_in_relationships(
                    relationships_update,
                    old_sample_id=resolved_sample_id,
                    new_sample_id=requested_sample_id,
                )
                target_sample_id = requested_sample_id
                relationships_changed = True

        father_id, mother_id = _parents_for_member(family.relationships, sample_id=current_sample_id)
        parent_fields_changed = (
            _payload_has_field(item, "father_id")
            and _sample_key(item.father_id or "") != _sample_key(father_id or "")
        ) or (
            _payload_has_field(item, "mother_id")
            and _sample_key(item.mother_id or "") != _sample_key(mother_id or "")
        )
        if parent_fields_changed:
            _apply_parent_updates(
                relationships_update,
                child_id=target_sample_id,
                update=item,
            )
            relationships_changed = True
        member_updates.append(
            _batch_member_update_payload(sample_id=target_sample_id, update=item)
        )

    response = await update_family_structure_for_admin(
        session,
        family_id=family_id,
        update=FamilyStructureUpdate(
            expected_structure_version=update.expected_structure_version,
            change_reason=update.change_reason or "family_member_batch_update",
            clear_existing_genomic_data=False,
            members=member_updates,
            relationships=relationships_update if relationships_changed else None,
        ),
        user=user,
    )
    return FamilyMemberBatchUpdateOut(
        family=response.family,
        warnings=response.warnings,
        stale_analysis_scopes=response.stale_analysis_scopes,
        data_counts=response.data_counts,
        cleared_data_counts=response.cleared_data_counts,
    )


async def delete_family_member_for_admin(
    session: AsyncSession,
    *,
    family_id: str,
    sample_id: str,
    confirmed: bool,
    user: CurrentUser,
) -> FamilyMemberDeleteOut:
    impact = await get_family_member_impact_for_user(
        session,
        family_id=family_id,
        sample_id=sample_id,
        user=user,
    )
    if not confirmed:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Family member deletion requires explicit confirmation.",
                "impact": impact.model_dump(),
            },
        )
    structure_update = FamilyStructureUpdate(
        change_reason="family_member_deleted",
        clear_existing_genomic_data=False,
        remove_members=[sample_id],
    )
    response = await update_family_structure_for_admin(
        session,
        family_id=family_id,
        update=structure_update,
        user=user,
    )
    return FamilyMemberDeleteOut(
        family=response.family,
        impact=impact,
        warnings=response.warnings,
        stale_analysis_scopes=response.stale_analysis_scopes,
        data_counts=response.data_counts or impact.data_counts,
        cleared_data_counts=response.cleared_data_counts,
    )
