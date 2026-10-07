"""Global Small Variant Explorer endpoints.

A variant-centric, cross-project aggregation view over small variants. See
``app/services/variant_explorer_service.py`` for the aggregation strategy.
"""

import csv
import io
from typing import List

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.csv_export import csv_safe_cell
from ..core.postgres import get_postgres_session
from ..core.sql import canonical_uuid
from ..dependencies import get_current_user
from ..schemas import (
    GlobalVariantPageOut,
    GlobalVariantRowOut,
    SmallVariantTagDefinitionOut,
    VariantCarriersOut,
    VariantExplorerAssemblyOut,
)
from ..services.access_control import CurrentUser, is_admin_user
from ..services.panel_metadata_service import _fetch_panel_genes
from ..services.small_variant_review_tags import list_small_variant_tag_definitions
from ..services.variant_explorer_service import (
    GlobalVariantFilters,
    export_global_small_variants,
    get_variant_carriers,
    list_explorer_assemblies,
    list_explorer_samples,
    search_global_small_variants,
    _accessible_project_rows,
)

router = APIRouter(prefix="/variant-explorer", tags=["variant-explorer"])

_GENOTYPE_MODES = {"het", "hom", "het_hom"}


def _parse_sample_genotype_filters(values: List[str]) -> list[tuple[str, str]]:
    """Parse ``sampleId:mode`` query values into (sample, mode) pairs."""

    parsed: list[tuple[str, str]] = []
    for entry in values:
        text_value = str(entry or "").strip()
        if not text_value:
            continue
        # A sample id may itself contain ":" (LAB:1), so the text after the last ":" is
        # the mode only when it is one; otherwise the whole value is the sample id.
        sample, separator, mode = text_value.rpartition(":")
        mode = mode.strip().lower()
        if not separator or (mode and mode not in _GENOTYPE_MODES):
            sample, mode = text_value, "het_hom"
        sample = sample.strip()
        mode = mode or "het_hom"
        if not sample:
            continue
        parsed.append((sample, mode))
    return parsed


@router.get("/assemblies", response_model=List[VariantExplorerAssemblyOut])
async def list_assemblies(
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> List[VariantExplorerAssemblyOut]:
    return await list_explorer_assemblies(session, user)


@router.get("/samples", response_model=List[str])
async def list_samples(
    assembly_id: str | None = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> List[str]:
    return await list_explorer_samples(session, user, assembly_id=assembly_id)


@router.get("/small-variant-tags", response_model=List[SmallVariantTagDefinitionOut])
async def list_explorer_tag_definitions(
    include_inactive: bool = False,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> List[SmallVariantTagDefinitionOut]:
    if is_admin_user(user):
        return await list_small_variant_tag_definitions(
            session,
            family_uuid="",
            project_ids=[],
            include_all_project_tags=True,
            include_inactive=include_inactive,
        )
    rows = await _accessible_project_rows(session, user)
    project_ids = sorted({row["project_id"] for row in rows})
    return await list_small_variant_tag_definitions(
        session, family_uuid="", project_ids=project_ids, include_inactive=include_inactive
    )


async def _build_global_variant_filters(
    chr: str | None = None,
    start: int | None = None,
    end: int | None = None,
    type: str | None = None,
    gene: str | None = None,
    panel_id: str | None = None,
    impact: List[str] = Query(default_factory=list),
    effect: List[str] = Query(default_factory=list),
    clinvar: List[str] = Query(default_factory=list),
    exclude_clinvar: List[str] = Query(default_factory=list, alias="exclude_clinvar"),
    rsid: str | None = None,
    hgvsc: str | None = None,
    hgvsp: str | None = None,
    canonical_only: bool = False,
    mane_only: bool = False,
    lof_only: bool = False,
    max_gnomad_af: float | None = None,
    max_gnomad_exomes_af: float | None = None,
    max_gnomad_genomes_af: float | None = None,
    max_gnomad_popmax_af: float | None = None,
    max_topmed_af: float | None = None,
    max_gnomad_ac: int | None = None,
    max_gnomad_hom_count: int | None = None,
    max_gnomad_hemi_count: int | None = None,
    clinvar_overrides_frequency: bool = False,
    min_cadd: float | None = None,
    min_revel: float | None = None,
    min_spliceai: float | None = None,
    sift: str | None = None,
    polyphen: str | None = None,
    classification: List[str] = Query(default_factory=list),
    review_tag: List[str] = Query(default_factory=list, alias="review_tag"),
    include_imputed: bool = False,
    sample_gt: List[str] = Query(default_factory=list, alias="sample_gt"),
    session: AsyncSession = Depends(get_postgres_session),
) -> GlobalVariantFilters:
    """Parse the shared small-variant filter query params into a filter object.

    Used by both the paginated listing and the CSV export so they stay in sync.
    """

    panel_genes: list[str] = []
    panel_uuid = canonical_uuid(panel_id)
    if panel_uuid is not None:
        panel_genes = (await _fetch_panel_genes(session, [panel_uuid])).get(panel_uuid, [])

    return GlobalVariantFilters(
        chromosome=chr,
        start=start,
        end=end,
        variant_type=type,
        gene=gene,
        panel_genes=panel_genes,
        impacts=impact,
        effects=effect,
        clinvar=clinvar,
        exclude_clinvar=exclude_clinvar,
        rsid=rsid,
        hgvsc=hgvsc,
        hgvsp=hgvsp,
        canonical_only=canonical_only,
        mane_only=mane_only,
        lof_only=lof_only,
        max_gnomad_af=max_gnomad_af,
        max_gnomad_exomes_af=max_gnomad_exomes_af,
        max_gnomad_genomes_af=max_gnomad_genomes_af,
        max_gnomad_popmax_af=max_gnomad_popmax_af,
        max_topmed_af=max_topmed_af,
        max_gnomad_ac=max_gnomad_ac,
        max_gnomad_hom_count=max_gnomad_hom_count,
        max_gnomad_hemi_count=max_gnomad_hemi_count,
        clinvar_overrides_frequency=clinvar_overrides_frequency,
        min_cadd=min_cadd,
        min_revel=min_revel,
        min_spliceai=min_spliceai,
        sift=sift,
        polyphen=polyphen,
        classifications=classification,
        review_tags=review_tag,
        include_imputed=include_imputed,
        sample_genotype_filters=_parse_sample_genotype_filters(sample_gt),
    )


@router.get("/small-variants", response_model=GlobalVariantPageOut)
async def list_global_small_variants(
    assembly_id: str | None = None,
    cursor: str | None = Query(default=None),
    page_size: int = Query(default=50, ge=1, le=200),
    sort: str = "total_samples",
    order: str = "desc",
    filters: GlobalVariantFilters = Depends(_build_global_variant_filters),
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> GlobalVariantPageOut:
    return await search_global_small_variants(
        session,
        user=user,
        filters=filters,
        assembly_id=assembly_id,
        cursor=cursor,
        page_size=page_size,
        sort=sort,
        order=order,
    )


# CSV column order for the variant export. Mirrors the on-screen table plus the
# extra annotation fields that don't fit in the UI.
_EXPORT_COLUMNS: list[tuple[str, str]] = [
    ("chr", "Chromosome"),
    ("pos", "Position"),
    ("ref", "Ref"),
    ("alt", "Alt"),
    ("variant_id", "Variant ID"),
    ("rsid", "rsID"),
    ("type", "Type"),
    ("gene", "Gene"),
    ("gene_symbols", "Gene symbols"),
    ("impact", "Impact"),
    ("consequence", "Consequence"),
    ("effects", "Effects"),
    ("hgvsc", "HGVSc"),
    ("hgvsp", "HGVSp"),
    ("clinvar", "ClinVar"),
    ("classification", "Classification"),
    ("tags", "Tags"),
    ("total_samples", "Total samples"),
    ("het_samples", "Het"),
    ("hom_samples", "Hom"),
    ("total_families", "Families"),
    ("last_seen", "Last reviewed"),
]


def _export_cell(row: GlobalVariantRowOut, field: str) -> str:
    value = getattr(row, field, None)
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    return str(value)


@router.get("/small-variants/export")
async def export_global_small_variants_csv(
    assembly_id: str | None = None,
    sort: str = "total_samples",
    order: str = "desc",
    filters: GlobalVariantFilters = Depends(_build_global_variant_filters),
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> StreamingResponse:
    """Download every filtered variant (with annotation data) as a CSV file."""

    assembly_name, rows = await export_global_small_variants(
        session,
        user=user,
        filters=filters,
        assembly_id=assembly_id,
        sort=sort,
        order=order,
    )

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([label for _, label in _EXPORT_COLUMNS])
    for row in rows:
        writer.writerow([csv_safe_cell(_export_cell(row, field)) for field, _ in _EXPORT_COLUMNS])

    filename = f"variant-explorer-{assembly_name or 'export'}.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/small-variants/{variant_key}/carriers", response_model=VariantCarriersOut)
async def list_variant_carriers(
    variant_key: int,
    assembly_id: str | None = None,
    genotype: str | None = None,
    include_imputed: bool = False,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> VariantCarriersOut:
    return await get_variant_carriers(
        session,
        user=user,
        variant_key=variant_key,
        assembly_id=assembly_id,
        genotype=genotype,
        include_imputed=include_imputed,
    )
