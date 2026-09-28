from typing import Dict, List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..dependencies import get_current_user
from ..schemas import (
    FamilyMitoDNAAnalysisOut,
    FamilyParaphaseTableOut,
    FamilyRepeatExpansionTableOut,
    FamilyTrackAvailabilityOut,
    HaplotypeResponse,
    PhasedMarkerResponse,
    RepeatExpansionTrackResponse,
    VariantLengthOut,
)
from ..services.family_metadata_context import build_family_metadata_context
from ..services.family_service import (
    get_family_haplotypes_batch_for_user,
    get_family_haplotypes_for_user,
    get_family_phased_markers_for_user,
    get_family_structural_variant_lengths_for_user,
    get_family_track_availability_for_user,
    get_shared_family_structural_variant_counts_for_user,
)
from ..services.access_control import CurrentUser
from ..services.mitochondrial_analysis import get_family_mitochondrial_analysis_response
from ..services.paraphase_pg import get_family_paraphase_table_response
from ..services.repeat_expansion_pg import (
    get_family_repeat_expansion_table_response,
    get_sample_repeat_expansion_track_response,
)


router = APIRouter()


@router.get("/{family_id}/haplotypes", response_model=HaplotypeResponse)
async def get_family_haplotypes(
    family_id: str,
    chr: str,
    start: int | None = None,
    end: int | None = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> HaplotypeResponse:
    return await get_family_haplotypes_for_user(
        session,
        family_id=family_id,
        user=user,
        chr=chr,
        start=start,
        end=end,
    )


@router.get("/{family_id}/phased-markers", response_model=PhasedMarkerResponse)
async def get_family_phased_markers(
    family_id: str,
    chr: str,
    start: int | None = None,
    end: int | None = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> PhasedMarkerResponse:
    return await get_family_phased_markers_for_user(
        session,
        family_id=family_id,
        user=user,
        chr=chr,
        start=start,
        end=end,
    )


@router.get("/{family_id}/haplotypes/batch", response_model=HaplotypeResponse)
async def get_family_haplotypes_batch(
    family_id: str,
    chroms: List[str] = Query(..., alias="chr"),
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> HaplotypeResponse:
    return await get_family_haplotypes_batch_for_user(
        session,
        family_id=family_id,
        user=user,
        chromosomes=chroms,
    )


@router.get("/{family_id}/repeat-expansions", response_model=FamilyRepeatExpansionTableOut)
async def get_family_repeat_expansions(
    family_id: str,
    project_id: str | None = None,
    count_only: bool = False,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> FamilyRepeatExpansionTableOut:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
        project_id=project_id,
    )
    return await get_family_repeat_expansion_table_response(
        session,
        context=context,
        count_only=count_only,
    )


@router.get("/{family_id}/paraphase", response_model=FamilyParaphaseTableOut)
async def get_family_paraphase(
    family_id: str,
    project_id: str | None = None,
    count_only: bool = False,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> FamilyParaphaseTableOut:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
        project_id=project_id,
    )
    return await get_family_paraphase_table_response(
        session,
        context=context,
        count_only=count_only,
    )


@router.get("/{family_id}/mitochondrial-dna", response_model=FamilyMitoDNAAnalysisOut)
async def get_family_mitochondrial_dna(
    family_id: str,
    project_id: str | None = None,
    count_only: bool = False,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> FamilyMitoDNAAnalysisOut:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
        project_id=project_id,
    )
    return await get_family_mitochondrial_analysis_response(
        session,
        context=context,
        count_only=count_only,
    )


@router.get(
    "/{family_id}/repeat-expansions/sample/{sample_id}",
    response_model=RepeatExpansionTrackResponse,
)
async def get_sample_repeat_expansions(
    family_id: str,
    sample_id: str,
    chroms: List[str] = Query(default_factory=list, alias="chr"),
    start: int | None = None,
    end: int | None = None,
    project_id: str | None = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> RepeatExpansionTrackResponse:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
        project_id=project_id,
    )
    return await get_sample_repeat_expansion_track_response(
        session,
        context=context,
        sample_name=sample_id,
        chromosomes=chroms,
        start=start,
        end=end,
    )


@router.get("/{family_id}/track-availability", response_model=FamilyTrackAvailabilityOut)
async def get_family_track_availability(
    family_id: str,
    chroms: List[str] = Query(default_factory=list, alias="chrom"),
    start: int | None = None,
    end: int | None = None,
    type: str | None = None,
    source: str | None = None,
    length: int | None = None,
    min_length: int | None = None,
    remote_chr: str | None = None,
    remote_start: int | None = None,
    panel_id: str | None = None,
    ps: int | None = None,
    sample_filters: List[str] = Query(default_factory=list, alias="sample_filter"),
    project_id: str | None = None,
    include_small_variants: bool = True,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> FamilyTrackAvailabilityOut:
    if not chroms:
        chroms = [str(value) for value in range(1, 23)] + ["X", "Y", "MT"]
    return await get_family_track_availability_for_user(
        session,
        family_id=family_id,
        user=user,
        chromosomes=chroms,
        start=start,
        end=end,
        variant_type=type,
        source=source,
        length=length,
        min_length=min_length,
        remote_chr=remote_chr,
        remote_start=remote_start,
        panel_id=panel_id,
        phase_set=ps,
        sample_filters=sample_filters,
        project_id=project_id,
        include_small_variants=include_small_variants,
    )


@router.get("/{family_id}/structural-variant-lengths", response_model=List[VariantLengthOut])
async def get_structural_variant_lengths(
    family_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
    limit: int = Query(100000, ge=1, le=100000),
) -> List[VariantLengthOut]:
    return await get_family_structural_variant_lengths_for_user(
        session,
        family_id=family_id,
        user=user,
        limit=limit,
    )


@router.get("/{family_id}/shared-structural-variant-counts", response_model=Dict[str, Dict[str, int]])
async def get_shared_structural_variant_counts(
    family_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> Dict[str, Dict[str, int]]:
    return await get_shared_family_structural_variant_counts_for_user(
        session,
        family_id=family_id,
        user=user,
    )
