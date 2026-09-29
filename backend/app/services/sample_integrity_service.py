"""Loader, application detection and pedigree resolution for sample-integrity QC.

CoGA runs several applications with different input modalities, so a single
generic QC is wrong. This layer resolves the application (long-read WGS family /
shallow-WGS PGT / monogenic NIPT / carrier couple (BEGECS) / single targeted),
picks the matching genotype callset (clair3 SNVs, GLIMPSE2 imputed, …), runs only
the checks that make sense for it, and — for NIPT — derives paternity from the
cfDNA classification instead of genotype relatedness. The QC maths is in the pure
``sample_integrity_qc`` module.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.coga_logging import scrub_log
from .clickhouse_family_variants import (
    fetch_family_variant_sources,
    fetch_imputed_phased_genotypes,
)
from .family_metadata_context import FamilyMetadataContext, build_family_metadata_context
from .haplotype_lineage_service import build_pedigree
from .metadata_service import get_family_record
from .access_control import CurrentUser
from .nipt import resolve_nipt_trio
from .sample_integrity_qc import (
    FetalSexCheck,
    Genotype,
    NiptCategoryQc,
    PaternityCheck,
    PedigreeSpec,
    SampleIntegrityReport,
    SexCheck,
    _evaluate_sex,
    _norm_sex,
    evaluate_fetal_sex,
    evaluate_nipt_category_qc,
    evaluate_paternity,
    evaluate_sample_integrity,
    profile_for,
    resolve_application,
)

logger = logging.getLogger(__name__)

QC_AUTOSOMES: tuple[str, ...] = ("1", "2", "3")
QC_SITES_PER_CHROM = 30_000
QC_X_CHROM = "X"
QC_X_SITES = 20_000
_WHOLE_CHROM_END = 300_000_000

# Genotype callsets preferred for QC, best first: real SNV calls before imputed.
_SOURCE_PREFERENCE = ("clair3", "glimpse2")


def _parse_genotype(gt: str | None) -> Genotype | None:
    """Parse a phased (``0|1``) or unphased (``0/1``) diploid GT; None if missing."""
    if not gt:
        return None
    sep = "|" if "|" in gt else "/" if "/" in gt else None
    if sep is None:
        return None
    a, b = gt.split(sep, 1)
    if not (a.isdigit() and b.isdigit()):
        return None
    return (int(a), int(b))


def _choose_genotype_source(available: list[str]) -> str | None:
    """Pick the QC genotype source from the family's callsets (clair3 > glimpse2)."""
    lowered = [s.lower() for s in available]
    for preferred in _SOURCE_PREFERENCE:
        for raw, low in zip(available, lowered):
            if preferred in low:
                return raw
    return available[0] if available else None


async def _load_chromosome(
    context: FamilyMetadataContext,
    chrom: str,
    limit: int,
    samples: list[str],
    source: str,
) -> dict[str, list[Genotype | None]]:
    rows = await fetch_imputed_phased_genotypes(
        context, chrom=chrom, start=0, end=_WHOLE_CHROM_END, limit=limit, source=source
    )
    arrays: dict[str, list[Genotype | None]] = {sample: [] for sample in samples}
    for _pos, _ref, _alt, sample_ids, gts in rows:
        gt_by_sample = dict(zip(sample_ids, gts))
        for sample in samples:
            arrays[sample].append(_parse_genotype(gt_by_sample.get(sample)))
    return arrays


def _build_pedigree_spec(context: FamilyMetadataContext) -> PedigreeSpec:
    recorded_sex = {
        str(row["sample_id"]): str(row.get("sex") or "")
        for row in context.sample_rows
        if row.get("sample_id")
    }
    pedigree = build_pedigree(context.sample_rows, context.relationship_rows)
    return PedigreeSpec(recorded_sex=recorded_sex, parents_of=pedigree.parents_of)


async def _nipt_parent_sex_checks(
    context: FamilyMetadataContext, *, parents: list[str]
) -> list[SexCheck]:
    """Sex the NIPT parents from X-SNV zygosity (father hemizygous → male; the
    cfDNA maternal-plasma sample is ~all maternal so reads female-het).

    Best-effort: returns [] (rather than raising) when genotypes are unavailable,
    so a family with partial/mock data degrades to a warning instead of erroring.
    """
    present = [p for p in parents if p and p in context.sample_name_to_uuid]
    if not present or not context.assembly_name:
        return []
    try:
        source = _choose_genotype_source(await fetch_family_variant_sources(context))
        if not source:
            return []
        x_genotypes = await _load_chromosome(context, QC_X_CHROM, QC_X_SITES, present, source)
    except Exception:  # noqa: BLE001 — missing/mock genotypes shouldn't fail the page
        return []
    recorded = {
        str(row["sample_id"]): str(row.get("sex") or "")
        for row in context.sample_rows
        if row.get("sample_id")
    }
    return [
        _evaluate_sex(pid, _norm_sex(recorded.get(pid)), x_genotypes.get(pid)) for pid in present
    ]


async def _nipt_checks(
    session: AsyncSession,
    *,
    family,
    family_id: str,
    user: CurrentUser,
    project_id: str | None,
    context: FamilyMetadataContext,
) -> tuple[PaternityCheck | None, FetalSexCheck | None, NiptCategoryQc | None, list[SexCheck]]:
    """NIPT cfDNA integrity: paternity (cat 7/8), fetal sex (paternal X), category
    distribution QC, and germline parent sex (X zygosity)."""
    trio = resolve_nipt_trio(family)
    if trio is None:
        return None, None, None, []
    # Imported lazily: nipt_service pulls in the heavy analysis stack.
    from .nipt_service import run_family_nipt_analysis

    result = await run_family_nipt_analysis(
        session, family_id=family_id, user=user, project_id=project_id
    )
    paternity = evaluate_paternity(trio.father_sample_id, result.category_counts)
    fs = result.fetal_sex
    fetal_sex = evaluate_fetal_sex(
        fs.inferred, fs.x_transmitted, fs.x_not_transmitted, fs.informative_sites
    )
    category_qc = evaluate_nipt_category_qc(result.category_counts)
    # The cfDNA sample is the maternal-plasma (mostly maternal) — sex it as the
    # mother; there is no separate maternal germline sample in the NIPT model.
    parent_sex = await _nipt_parent_sex_checks(
        context, parents=[trio.father_sample_id, trio.cfdna_sample_id]
    )
    return paternity, fetal_sex, category_qc, parent_sex


async def get_family_sample_integrity_qc(
    session: AsyncSession,
    *,
    family_id: str,
    user: CurrentUser,
    project_id: str | None = None,
) -> SampleIntegrityReport:
    context = await build_family_metadata_context(
        session, family_identifier=family_id, user=user, project_id=project_id
    )
    family = await get_family_record(session, family_id, user)
    analysis_type = str((getattr(family, "metadata", None) or {}).get("analysis_type") or "")

    spec = _build_pedigree_spec(context)
    pedigree = build_pedigree(context.sample_rows, context.relationship_rows)
    samples = sorted(context.sample_name_to_uuid)

    application = resolve_application(
        analysis_type=analysis_type,
        roles=pedigree.roles,
        parents_of=pedigree.parents_of,
        sample_count=len(samples),
    )
    profile = profile_for(application)

    service_notes: list[str] = []
    paternity_check: PaternityCheck | None = None
    fetal_sex_check: FetalSexCheck | None = None
    category_qc_check: NiptCategoryQc | None = None
    nipt_parent_sex_checks: list[SexCheck] = []
    if profile.run_paternity:
        try:
            paternity_check, fetal_sex_check, category_qc_check, nipt_parent_sex_checks = (
                await _nipt_checks(
                    session,
                    family=family,
                    family_id=family_id,
                    user=user,
                    project_id=project_id,
                    context=context,
                )
            )
        except Exception as exc:  # noqa: BLE001 — degrade to a warning, never 500 the page
            logger.warning("NIPT cfDNA QC could not run for family %s: %s", scrub_log(family_id), scrub_log(exc))
            service_notes.append(f"NIPT cfDNA analysis could not run ({exc}).")

    autosomal: dict[str, list[Genotype | None]] = {sample: [] for sample in samples}
    x_genotypes: dict[str, list[Genotype | None]] = {sample: [] for sample in samples}
    genotype_source: str | None = None
    needs_genotypes = profile.run_sex or profile.run_relatedness or profile.run_mendelian
    if needs_genotypes and context.assembly_name and samples:
        try:
            genotype_source = _choose_genotype_source(await fetch_family_variant_sources(context))
            if genotype_source:
                for chrom in QC_AUTOSOMES:
                    part = await _load_chromosome(
                        context, chrom, QC_SITES_PER_CHROM, samples, genotype_source
                    )
                    for sample in samples:
                        autosomal[sample].extend(part[sample])
                x_genotypes = await _load_chromosome(
                    context, QC_X_CHROM, QC_X_SITES, samples, genotype_source
                )
        except Exception as exc:  # noqa: BLE001 — degrade to a warning, never 500 the page
            logger.warning("Genotypes could not be loaded for family %s: %s", scrub_log(family_id), scrub_log(exc))
            service_notes.append(f"Genotypes could not be loaded ({exc}).")
            autosomal = {sample: [] for sample in samples}
            x_genotypes = {sample: [] for sample in samples}

    return evaluate_sample_integrity(
        autosomal,
        x_genotypes,
        spec,
        profile=profile,
        genotype_source=genotype_source,
        paternity_check=paternity_check,
        fetal_sex_check=fetal_sex_check,
        category_qc_check=category_qc_check,
        extra_sex_checks=nipt_parent_sex_checks,
        extra_notes=service_notes,
    )
