"""*ClinVar (CoGA)*: each small variant's ClinVar in the release CoGA loaded itself, beside the
ClinVar of its annotation.

A request reads the assembly's current release once (``clinvar_coga_read``), passes it to every
ClickHouse query it runs (the *ClinVar (CoGA)* filter and the P/LP override,
``SmallVariantQueryFilters.clinvar_coga_release``) and serves it with its answer, so one page
never shows two releases, even while a newer one is released. The variants a page shows are
looked up by the allele key of each of their ALTs (``record_allele_keys``), as the import keys
its rows: each ALT gets its classification, review status, stars, conditions and VariationID, or
the reason it has none (``ClinvarCogaOut.status``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas import ClinvarCogaOut, VariantPage
from .annotation_manifest_service import analysis_clinvar_release
from .clickhouse_small_variants import SmallVariantFamilyRecord
from .clickhouse_variant_ids import minimal_allele_id, small_allele_key
from .clinvar_release_store import ClinvarStoredAllele, clinvar_alleles
from .clinvar_releases import CLINVAR_ASSEMBLY_FOLDERS, current_clinvar_release
from .clinvar_review_status import clinvar_stars
from .family_metadata_context import FamilyMetadataContext


@dataclass(frozen=True, slots=True)
class ClinvarCogaRead:
    """The CoGA ClinVar release one request reads: ``release`` None when the assembly has none
    loaded, ``available`` False when ClinVar publishes no VCF for it."""

    assembly_name: str
    release: date | None
    available: bool


async def clinvar_coga_read(session: AsyncSession, assembly_name: str | None) -> ClinvarCogaRead:
    """The assembly's current ClinVar release, read once for a request."""
    if not assembly_name or assembly_name not in CLINVAR_ASSEMBLY_FOLDERS:
        return ClinvarCogaRead(assembly_name or "", None, False)
    current = await current_clinvar_release(session, assembly_name)
    return ClinvarCogaRead(assembly_name, current.release if current is not None else None, True)


# A small variant as the lookup reads it: its chromosome, position, REF and ALT as stored.
Locus = tuple[str, int, str | None, str | None]

# The read of a request that reads no release (an assembly ClinVar publishes no VCF for).
NO_CLINVAR_COGA = ClinvarCogaRead("", None, False)


def _alleles(read: ClinvarCogaRead, locus: Locus) -> list[tuple[str, int | None]]:
    """Each ALT of a small variant with its allele key (None for one that names no allele)."""
    chrom, pos, ref, alts = locus
    if not ref or not alts:
        return []
    alleles: list[tuple[str, int | None]] = []
    for alt in alts.split(","):
        allele = minimal_allele_id(chrom, pos, ref, alt)
        alleles.append((alt, small_allele_key(read.assembly_name, allele) if allele is not None else None))
    return alleles


def _value(read: ClinvarCogaRead, alt: str, stored: ClinvarStoredAllele | None) -> ClinvarCogaOut:
    if not read.available:
        return ClinvarCogaOut(alt=alt, status="not_available")
    if read.release is None:
        return ClinvarCogaOut(alt=alt, status="no_release")
    if stored is None:
        return ClinvarCogaOut(alt=alt, status="not_in_release", release=read.release)
    return ClinvarCogaOut(
        alt=alt,
        status="in_release",
        release=read.release,
        classification=stored.classification or None,
        review_status=stored.review_status or None,
        stars=clinvar_stars(stored.review_status),
        conditions=list(stored.conditions),
        variation_id=stored.variation_id,
        included_only=stored.included_only,
    )


async def clinvar_coga_values(read: ClinvarCogaRead, loci: Sequence[Locus]) -> list[list[ClinvarCogaOut]]:
    """Each small variant's ClinVar (CoGA), one value per ALT, in the order of ``loci``: from the
    release ``read`` names, looked up for these alleles alone."""
    alleles = [_alleles(read, locus) for locus in loci]
    keys = {key for pairs in alleles for _alt, key in pairs if key is not None}
    stored = await clinvar_alleles(read.assembly_name, read.release, keys) if read.release is not None else {}
    return [[_value(read, alt, stored.get(key) if key is not None else None) for alt, key in pairs] for pairs in alleles]


def clinvar_coga_of(alt: str | None, alt_allele: str | None, values: Sequence[ClinvarCogaOut]) -> ClinvarCogaOut | None:
    """The value of the ALT a variant is shown with (its annotation's ``alt_allele``, else its
    ALT), or its only ALT's; as the frontend reads it (``clinvarCogaOf``, ``lib/clinvar.ts``)."""
    shown = alt_allele or alt
    return next((value for value in values if value.alt == shown), values[0] if len(values) == 1 else None)


def clinvar_by_the_rule(annotated: str | None, coga: ClinvarCogaOut | None) -> str | None:
    """The design's one rule for the readers of phase 2 (B8): the classification in CoGA's
    ClinVar release where it holds the allele, else the annotation's. An allele the release
    holds only as part of a combination has no classification there."""
    return coga.classification if coga is not None and coga.status == "in_release" else annotated


async def clinvar_coga_by_id(
    read: ClinvarCogaRead, records: Mapping[str, SmallVariantFamilyRecord]
) -> dict[str, list[ClinvarCogaOut]]:
    """Each family record's ClinVar (CoGA), one value per ALT, by the key ``records`` names it
    under: what a classification freezes (``build_evidence_snapshot``) and the drift check reads
    now."""
    values = await clinvar_coga_values(read, [(r.chrom, r.pos, r.ref, r.alt) for r in records.values()])
    return dict(zip(records, values))


async def serve_clinvar_coga(
    session: AsyncSession, context: FamilyMetadataContext, read: ClinvarCogaRead, page: VariantPage
) -> None:
    """A family's small-variant page with its two ClinVar releases (design B7): each variant's
    ClinVar (CoGA) per ALT, from the release the request read; that release; and the release of
    the ClinVar file the analysis's annotation names, which the annotation's value is of."""
    variants = [*page.variants, *(variant for group in page.variant_groups for variant in group.variants)]
    if not variants:
        return  # a page without a variant names no ClinVar
    values = await clinvar_coga_values(read, [(v.chr, v.start, v.ref, v.alt) for v in variants])
    for variant, value in zip(variants, values):
        variant.clinvar_coga = value
    page.clinvar_coga_release = read.release
    page.clinvar_annotation_release = await analysis_clinvar_release(session, context)
