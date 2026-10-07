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
import re

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.coga_logging import describe_error, scrub_log
from .clickhouse_family_variants import (
    GenotypeSampleScope,
    fetch_family_variant_sources,
    fetch_genotype_site_sample,
)
from .family_metadata_context import FamilyMetadataContext, build_family_metadata_context
from .genotypes import NO_CALL, classify_genotype
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

# The genotypes the checks read: a fixed sample of up to QC_AUTOSOMAL_SITES sites spread
# over every autosome, for relatedness and Mendelian errors, and of up to QC_X_SITES on
# chrX outside the pseudo-autosomal regions, for sex (fetch_genotype_site_sample). The
# sample must span the genome. Siblings share 0, 1 or 2 haplotypes in blocks tens of Mb
# long, so the first 30,000 sites of chr1-3, which the checks once read, measured three
# such blocks, and sibling embryos looked like duplicates.
QC_AUTOSOMAL_SITES = 90_000
QC_X_SITES = 20_000

# Genotype callsets preferred for QC, best first: real SNV calls before imputed.
_SOURCE_PREFERENCE = ("clair3", "glimpse2")


def _parse_genotype(gt: str | None) -> Genotype | None:
    """Parse a phased (``0|1``), unphased (``0/1``) or haploid (``1``) GT; None unless
    every allele is called.

    The shared genotype classes (#511) decide what is a call. A haploid call -- a male's
    non-PAR chrX from callers that emit ploidy 1 -- is hemizygous and reads as the
    homozygote (``1`` -> (1, 1), ``0`` -> (0, 0)), so a haploid-called father is sexed
    rather than left indeterminate. A no-call, a half call (``./1``), a non-genotype
    and a call of more than two alleles stay None: missing data is never read as a
    genotype.
    """
    if classify_genotype(gt) == NO_CALL:
        return None
    alleles = re.split(r"[/|]", (gt or "").strip())
    if len(alleles) == 1:
        return (int(alleles[0]), int(alleles[0]))
    if len(alleles) != 2 or not all(allele.isdigit() for allele in alleles):
        return None
    return (int(alleles[0]), int(alleles[1]))


def _choose_genotype_source(available: list[str]) -> str | None:
    """Pick the QC genotype source from the family's callsets (clair3 > glimpse2)."""
    lowered = [s.lower() for s in available]
    for preferred in _SOURCE_PREFERENCE:
        for raw, low in zip(available, lowered):
            if preferred in low:
                return raw
    return available[0] if available else None


async def _load_genotype_sample(
    context: FamilyMetadataContext,
    scope: GenotypeSampleScope,
    limit: int,
    samples: list[str],
    source: str,
    *,
    read_as_reference: dict[str, int] | None = None,
) -> dict[str, list[Genotype | None]]:
    """Each sample's genotypes at the sampled sites. ``read_as_reference``, when given,
    is told for each sample how many sites it was read as reference at without a call of
    its own (see below)."""
    rows = await fetch_genotype_site_sample(context, scope=scope, limit=limit, source=source)
    # A sample without a call at a site of a callset stored one file per sample (a
    # monogenic NIPT's, or the long-read pipeline's per-sample callsets read as one) had no
    # alt read there that its caller reported: reference, as a joint VCF would have called
    # it. A joint VCF calls each of its samples at every site, so there it never happens.
    # A sample with no call at any site read has no file in the callset (it was not
    # sequenced): its genotypes stay missing, never reference.
    called = {sample for _chrom, _pos, _ref, _alt, sample_ids, _gts in rows for sample in sample_ids}
    arrays: dict[str, list[Genotype | None]] = {sample: [] for sample in samples}
    for _chrom, _pos, _ref, _alt, sample_ids, gts in rows:
        gt_by_sample = dict(zip(sample_ids, gts))
        for sample in samples:
            gt = gt_by_sample.get(sample)
            if gt is None and sample in called:
                gt = "0/0"
                if read_as_reference is not None:
                    read_as_reference[sample] = read_as_reference.get(sample, 0) + 1
            arrays[sample].append(_parse_genotype(gt))
    return arrays


def _per_sample_callset_note(read_as_reference: dict[str, int], sites: int) -> str | None:
    """The note a relatedness reading needs when its call set was made one sample at a
    time: a missing record is read as reference, and a site without reads is one too."""
    shares = {sample: count / sites for sample, count in read_as_reference.items() if count and sites}
    if not shares:
        return None
    described = ", ".join(f"{sample} at {share:.0%}" for sample, share in sorted(shares.items()))
    return (
        "The call set was made one sample at a time (no joint VCF). Where a sample's own file "
        f"has no record ({described} of the sites read), the checks read it as reference, as a "
        "joint VCF calls a covered site. A site without reads is read that way too, which "
        "lowers the kinship: on low-coverage data, unrelated does not exclude a relationship."
    )


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
        x_genotypes = await _load_genotype_sample(context, "chrX", QC_X_SITES, present, source)
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
    # Only sites with a confident father call, where the mother does not carry the
    # allele and a transmitted allele could not have been missed, count.
    evidence = result.paternal_transmission
    paternity = evaluate_paternity(
        trio.father_sample_id,
        hom_alt_transmitted=evidence.hom_alt_transmitted,
        hom_alt_not_transmitted=evidence.hom_alt_not_transmitted,
        het_transmitted=evidence.het_transmitted,
        het_not_transmitted=evidence.het_not_transmitted,
    )
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

    # The notes are frozen into the signed report snapshot with the rest of the report. A
    # check that could not run says so in a fixed sentence, never with the error's text: a
    # failed query's text quotes its SQL and parameters. The warning logged beside it names
    # the error.
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
            # Named, not quoted: a failed query's text holds its parameters.
            logger.warning(
                "NIPT cfDNA QC could not run for family %s: %s", scrub_log(family_id), describe_error(exc)
            )
            service_notes.append("NIPT cfDNA analysis could not run.")

    autosomal: dict[str, list[Genotype | None]] = {sample: [] for sample in samples}
    x_genotypes: dict[str, list[Genotype | None]] = {sample: [] for sample in samples}
    genotype_source: str | None = None
    needs_genotypes = profile.run_sex or profile.run_relatedness or profile.run_mendelian
    if needs_genotypes and context.assembly_name and samples:
        try:
            genotype_source = _choose_genotype_source(await fetch_family_variant_sources(context))
            if genotype_source:
                read_as_reference: dict[str, int] = {}
                autosomal = await _load_genotype_sample(
                    context,
                    "autosomes",
                    QC_AUTOSOMAL_SITES,
                    samples,
                    genotype_source,
                    read_as_reference=read_as_reference,
                )
                x_genotypes = await _load_genotype_sample(
                    context, "chrX", QC_X_SITES, samples, genotype_source
                )
                sites = max((len(values) for values in autosomal.values()), default=0)
                note = _per_sample_callset_note(read_as_reference, sites)
                if note is not None and profile.run_relatedness:
                    service_notes.append(note)
        except Exception as exc:  # noqa: BLE001 — degrade to a warning, never 500 the page
            logger.warning(
                "Genotypes could not be loaded for family %s: %s", scrub_log(family_id), describe_error(exc)
            )
            service_notes.append("Genotypes could not be loaded.")
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
