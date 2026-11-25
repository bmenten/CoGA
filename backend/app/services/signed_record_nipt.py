"""The NIPT section of the signed analysis record (owner decision of 2026-10-14).

A monogenic NIPT family is signed out like every family: through the same sign-out, gates
and report code, one record per analysis (``report_signout_service``). Its record also holds
``nipt``, the NIPT analysis as the NIPT page showed it to the analyst, computed by the
backend (``nipt_service``, ``nipt_quality_checks``), not by the browser:

* the fetal fraction with its interval, site count, per-site median and verdict, and the de
  novo window it sets (REQ-NIPT-001, REQ-NIPT-011);
* the quality checks with their verdicts (REQ-NIPT-015): paternity, the fetal sex from the
  father's X alleles and the plasma's chrY coverage, the maternal plasma sample's sex
  profile; the coverage of the capture targets (REQ-NIPT-006); the category counts, the
  filter funnel and why cfDNA calls failed the quality filter (REQ-NIPT-004, REQ-NIPT-010);
* each reported variant (tagged ``report``) as the NIPT variant list classifies it: its
  category, maternal state and fetal inheritance, the plasma's and the father's reads, its
  fetal-inheritance probabilities (REQ-NIPT-012), its flags, and for a de novo candidate in
  the fetal window its triage (REQ-NIPT-011), one the de novo view leaves out as recurrent
  included; a reported variant the analysis does not classify, with why; and the
  recessive fetal risk of the genes the reported variants make both parents carriers in
  (REQ-NIPT-013);
* the inputs it ran with: the trio, the per-target coverage tables (file, targets, when
  loaded), the assay's artifact list (its entries and a digest of its alleles, what it
  removed from the family's analysis and what the family's annotation kept in), and the model
  (its validation, constants and quality filter);
* without a per-target coverage table, the notes of the coverage check over the plasma's
  coverage tracks, word for word as the NIPT page shows them, each naming a track the check
  does not read as depth, with what the check ran for (``coverage_notes``, the owner's
  decision of 2026-10-28). The check depends on the gene or panel the page's search
  gives it, which the sign-out is not told: the sign-out runs it as the page runs it without a
  gene or panel, over the family's region of interest, the page's view on opening. Null with a
  per-target table, whose targets the check reads instead (``target_coverage``).

The Sample QC's NIPT checks (paternity, fetal sex from the paternal X, the category
distribution, the parents' sex) and their notes are in the record's ``sample_qc``, read from
the same run of the analysis as this section (``nipt_service.SharedNiptAnalysis``). A family
that is no monogenic NIPT family holds no ``nipt`` key, so its record is as it was. A NIPT
quality check that failed gates the sign-out like a failed Sample QC check
(``nipt_signout_concerns``).

Deterministic and JSON-safe, as every section is: lists sorted or in the analysis's own
order, numbers that JSONB cannot hold (NaN, infinity) frozen as null, and -0.0 as 0.0.
"""

from __future__ import annotations

import dataclasses
import json
import math
from datetime import datetime
from typing import Any, Collection, Iterable, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .family_analysis_types import family_analysis_types
from .family_metadata_context import FamilyMetadataContext
from .family_package_records import analysis_members
from .hash_chain import canonical_hash
from .nipt import (
    DEFAULT_NIPT_ASSAY_KEY,
    MONOGENIC_NIPT_ANALYSIS_TYPE,
    NIPT_TRIO_UNRESOLVED,
    NiptTrio,
    assay_key_for_sample_metadata,
    multiple_pregnancy_note,
    nipt_analysis_in_scope,
    resolve_nipt_trio_from_members,
)
from .nipt_analysis import (
    BACKGROUND_ERROR_RATE,
    FF_HARD_FLOOR,
    FF_MAX_CI_HALFWIDTH,
    FF_MIN_SITES,
    MATERNAL_HET_BIAS,
    NIPT_MODEL_REFERENCE,
    OVERDISPERSION,
    NiptQualityThresholds,
)
from .nipt_coverage import TargetRegion
from .nipt_quality_checks import NiptQualityChecks, nipt_quality_checks
from .nipt_service import (
    NiptClassifiedVariant,
    NiptFamilyRecord,
    SharedNiptAnalysis,
    get_family_nipt_record,
    nipt_analysis_key,
    nipt_coverage_notes,
    nipt_coverage_source,
    nipt_panel_target_regions,
    roi_target_region,
)
from .analysis_gene_panel import AnalysisGenePanel, load_analysis_gene_panel
from .nipt_target_coverage import TARGET_COVERAGE_TRACK_TYPE, TargetCoverageSummary, summarize_target_coverage
from .nipt_triage import RecessiveAllele, RecessiveGeneRisk

# The notes of the coverage check over the coverage tracks, in the section.
COVERAGE_NOTES_KEY = "coverage_notes"

_FAMILY_METADATA_SQL = text(
    """
    SELECT metadata
    FROM families
    WHERE id = CAST(:family_uuid AS uuid)
    """
)

# The family's region of interest, as the family page reads it (metadata_service: a ROI is set
# when it has a query).
_FAMILY_ROI_SQL = text(
    """
    SELECT roi_query, roi_label, roi_chr, roi_start, roi_end
    FROM families
    WHERE id = CAST(:family_uuid AS uuid)
    """
)

_TARGET_TABLES_SQL = text(
    """
    SELECT s.sample_id, src.filename, src.row_count, src.uploaded_at
    FROM sample_interval_track_sources src
    JOIN samples s ON s.id = src.sample_id
    WHERE src.family_id = CAST(:family_uuid AS uuid)
      AND src.track_type = :track_type
      AND src.source = :source
      AND s.sample_id = ANY(CAST(:samples AS text[]))
    ORDER BY s.sample_id, src.filename
    """
)


def _number(value: Any) -> Any:
    """A number as JSONB keeps it: None for NaN and infinity, 0.0 for -0.0 (JSONB has no
    negative zero, so the content hash would not survive the round trip)."""
    if isinstance(value, float):
        return value + 0.0 if math.isfinite(value) else None
    return value


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value is not None else None


async def _family_metadata(session: AsyncSession, context: FamilyMetadataContext) -> dict[str, Any] | None:
    """The family's ``families.metadata``."""
    row = (
        await session.execute(_FAMILY_METADATA_SQL, {"family_uuid": context.family_uuid})
    ).mappings().first()
    metadata = row.get("metadata") if row is not None else None
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    return metadata if isinstance(metadata, dict) else None


async def is_nipt_family(session: AsyncSession, context: FamilyMetadataContext) -> bool:
    """Whether the family holds a monogenic NIPT analysis (``family_analysis_types``), as the
    NIPT page and the Sample QC read it."""
    return MONOGENIC_NIPT_ANALYSIS_TYPE in family_analysis_types(await _family_metadata(session, context))


async def family_nipt_trio(
    session: AsyncSession, context: FamilyMetadataContext
) -> tuple[NiptTrio, str] | None:
    """:func:`nipt_trio_of`, of the monogenic NIPT the record is of (the analysis signed out,
    or the family's only one: ``nipt.nipt_analysis_in_scope``) and the members its package
    lists."""
    metadata = await _family_metadata(session, context)
    analysis = nipt_analysis_in_scope(metadata, MONOGENIC_NIPT_ANALYSIS_TYPE, context.scope.analysis)
    return nipt_trio_of(
        context,
        listed=analysis_members(metadata, analysis) if analysis is not None else frozenset(),
        analysis=analysis,
    )


def nipt_trio_of(
    context: FamilyMetadataContext, *, listed: Collection[str], analysis: str | None
) -> tuple[NiptTrio, str] | None:
    """The trio of the family's monogenic NIPT ``analysis`` (its key) and the artifact-list scope
    of its cfDNA assay, from its active members and those its package lists (``listed``,
    ``nipt.resolve_nipt_trio``'s rules); None when the trio cannot be resolved."""
    rows = list(context.sample_rows)
    trio = resolve_nipt_trio_from_members(
        (
            (
                str(row.get("sample_id")),
                row.get("role"),
                row.get("sample_metadata") if isinstance(row.get("sample_metadata"), Mapping) else None,
            )
            for row in rows
            if row.get("sample_id")
        ),
        listed=listed,
        analysis=analysis,
    )
    if trio is None:
        return None
    assay_key = next(
        (
            assay_key_for_sample_metadata(
                row.get("sample_metadata") if isinstance(row.get("sample_metadata"), dict) else None,
                analysis,
            )
            for row in rows
            if str(row.get("sample_id")) == trio.cfdna_sample_id
        ),
        DEFAULT_NIPT_ASSAY_KEY,
    )
    return trio, assay_key


async def _target_tables(
    session: AsyncSession, context: FamilyMetadataContext, trio: NiptTrio
) -> list[dict[str, Any]]:
    """The per-target coverage tables the analysis read: the father's and the plasma's of its
    pregnancy (``nipt_coverage_source``: a father sampled for two pregnancies holds a table of
    each), each with its file, its number of targets and when it was loaded."""
    rows = (
        await session.execute(
            _TARGET_TABLES_SQL,
            {
                "family_uuid": context.family_uuid,
                "track_type": TARGET_COVERAGE_TRACK_TYPE,
                "source": nipt_coverage_source(trio),
                "samples": sorted({trio.father_sample_id, trio.cfdna_sample_id}),
            },
        )
    ).mappings().all()
    return [
        {
            "sample": str(row.get("sample_id")),
            "file": row.get("filename") or None,
            "targets": int(row["row_count"]) if row.get("row_count") is not None else None,
            "loaded_at": _iso(row.get("uploaded_at")),
        }
        for row in rows
    ]


async def _family_roi_region(session: AsyncSession, context: FamilyMetadataContext) -> TargetRegion | None:
    """The family's region of interest as the coverage check's region when no gene or panel is
    given (``nipt_service.roi_target_region``); None when the family has none."""
    row = (
        await session.execute(_FAMILY_ROI_SQL, {"family_uuid": context.family_uuid})
    ).mappings().first()
    if not row or not row.get("roi_query"):
        return None
    if row.get("roi_chr") is None or row.get("roi_start") is None or row.get("roi_end") is None:
        return None
    return roi_target_region(
        query=str(row["roi_query"]),
        label=row.get("roi_label") or None,
        chrom=str(row["roi_chr"]),
        start=int(row["roi_start"]),
        end=int(row["roi_end"]),
    )


async def _coverage_notes(
    session: AsyncSession,
    context: FamilyMetadataContext,
    trio: NiptTrio,
    analysis: NiptFamilyRecord,
    panel: AnalysisGenePanel | None,
) -> dict[str, Any] | None:
    """The notes of the coverage check over the plasma's coverage tracks, as the NIPT page shows
    them for the analysis's diagnostic panel, or, without one (or with one whose genes have no
    loci on the assembly), for no gene and no panel: over the family's region of interest, none
    without one. None when the plasma has a per-target coverage table: the check reads its
    targets instead."""
    if analysis.summary.target_coverage is not None:
        return None
    # As the page reads a panel (nipt_service._resolve_nipt_target_regions): its genes' loci and
    # regions, else, when they give none, the family's region of interest.
    regions = await nipt_panel_target_regions(session, context, panel.panel_id) if panel is not None else []
    if not regions:
        roi = await _family_roi_region(session, context)
        regions = [roi] if roi is not None else []
    return {
        # What the check ran for: the analysis's diagnostic panel, else the family's region of
        # interest.
        "gene": None,
        "panel": panel.panel_id if panel is not None else None,
        "regions": [
            {"label": region.label, "chrom": region.chrom, "start": region.start, "end": region.end}
            for region in regions
        ],
        "notes": await nipt_coverage_notes(session, context, trio.cfdna_sample_id, regions),
    }


def _fetal_fraction(checks: NiptQualityChecks) -> dict[str, Any]:
    ff = checks.fetal_fraction
    return {
        "ff": _number(ff.ff),
        "ff_computed": _number(ff.ff_computed),
        "ff_median": _number(ff.ff_median),
        "ci_low": _number(ff.ci_low),
        "ci_high": _number(ff.ci_high),
        "n_sites": ff.n_sites,
        "method": ff.method,
        "low_confidence": ff.low_confidence,
        "vaf_q05": _number(ff.vaf_q05),
        "vaf_q95": _number(ff.vaf_q95),
        "status": checks.fetal_fraction_check.status,
        "message": checks.fetal_fraction_check.message,
    }


def _checks(checks: NiptQualityChecks) -> dict[str, Any]:
    evidence = checks.paternal_transmission
    paternity = checks.paternity
    fetal_sex = checks.fetal_sex
    plasma = checks.plasma_sample
    coverage = plasma.coverage
    return {
        "paternity": {
            "status": paternity.status,
            "message": paternity.message,
            "hom_alt_transmitted": evidence.hom_alt_transmitted,
            "hom_alt_not_transmitted": evidence.hom_alt_not_transmitted,
            "het_transmitted": evidence.het_transmitted,
            "het_not_transmitted": evidence.het_not_transmitted,
            "hom_alt_rate": _number(evidence.hom_alt_rate),
            "het_rate": _number(evidence.het_rate),
        },
        "fetal_sex": {
            "call": fetal_sex.call,
            "status": fetal_sex.status,
            "message": fetal_sex.message,
            "paternal_x": fetal_sex.paternal_x.inferred,
            "x_transmitted": fetal_sex.paternal_x.x_transmitted,
            "x_not_transmitted": fetal_sex.paternal_x.x_not_transmitted,
            "informative_sites": fetal_sex.paternal_x.informative_sites,
            "chry": fetal_sex.chry,
            "chry_profile": fetal_sex.chry_profile,
            "chry_fetal_fraction": _number(coverage.fetal_fraction_estimate) if coverage else None,
        },
        "plasma_sample": {
            "status": plasma.status,
            "message": plasma.message,
            "profile": plasma.profile,
            "x_ratio": _number(coverage.x_ratio) if coverage else None,
            "y_ratio": _number(coverage.y_ratio) if coverage else None,
            "y_targets": coverage.y_targets if coverage else None,
        },
    }


def _target_coverage(summary: TargetCoverageSummary | None) -> dict[str, Any] | None:
    """The coverage QC of every capture target, and each gene with a weak target."""
    if summary is None:
        return None
    return {
        "targets": summary.targets,
        "median_mean": _number(summary.median_mean),
        "q05_mean": _number(summary.q05_mean),
        "below_critical": summary.below_critical,
        "below_advisory": summary.below_advisory,
        "zero_mean": summary.zero_mean,
        "incomplete": summary.incomplete,
        "critical_mean_depth": _number(summary.critical_mean_depth),
        "advisory_mean_depth": _number(summary.advisory_mean_depth),
        "genes": [
            {
                "gene": gene.gene,
                "targets": gene.targets,
                "weak_targets": gene.weak_targets,
                "min_mean": _number(gene.min_mean),
                "mean_of_means": _number(gene.mean_of_means),
            }
            for gene in summary.genes
        ],
    }


def _panel_target_coverage(
    panel: AnalysisGenePanel | None, analysis: NiptFamilyRecord
) -> dict[str, Any] | None:
    """The coverage QC of the diagnostic panel's capture targets (REQ-NIPT-006, REQ-NIPT-019):
    the panel's genes with a target and without one, the totals over their targets, and each
    gene with a weak target or with no target at all (a gene the assay does not capture is not
    tested). None without a panel or without a per-target coverage table."""
    if panel is None or not analysis.plasma_targets:
        return None
    summary = summarize_target_coverage(analysis.plasma_targets, genes=panel.gene_symbols)
    frozen = _target_coverage(summary) or {}
    genes = frozen.pop("genes", [])
    return {
        **frozen,
        "panel_genes": len(panel.gene_symbols),
        "genes_without_target": sorted(str(gene["gene"]) for gene in genes if not gene["targets"]),
        "genes_with_weak_target": [gene for gene in genes if gene["targets"] and gene["weak_targets"]],
    }


def _variant(item: NiptClassifiedVariant) -> dict[str, Any]:
    """A reported variant as the NIPT variant list classifies it."""
    record = item.record
    classification = item.classification
    site = item.site
    triage = item.de_novo
    posterior = classification.fetal_genotype_posterior
    return {
        "variant_id": record.variant_id,
        "source": record.source,
        "chrom": record.chr,
        "position": record.start,
        "ref": record.ref,
        "alt": record.alt,
        "genes": sorted({gene for gene in record.gene_symbols or [] if gene}),
        "category": classification.category,
        "category_label": classification.category_label,
        "maternal_state": classification.maternal_state,
        "fetal_inheritance": classification.fetal_inheritance,
        "expected_vaf": _number(classification.expected_vaf),
        "observed_vaf": _number(classification.observed_vaf),
        "confidence": _number(classification.confidence),
        "runner_up_category": classification.runner_up_category,
        "runner_up_confidence": _number(classification.runner_up_confidence),
        "paternal_transmission_probability": _number(classification.paternal_transmission_probability),
        "maternal_allele_probability": _number(classification.maternal_allele_probability),
        "fetal_hom_alt_probability": _number(classification.fetal_hom_alt_probability),
        "fetal_genotype_posterior": (
            {key: _number(value) for key, value in sorted(posterior.items())}
            if posterior is not None
            else None
        ),
        "flags": list(classification.flags),
        "quality_failures": list(classification.quality_failures),
        "plasma": {
            "alt_reads": site.cf_alt_reads if site is not None else None,
            "depth": site.cf_dp if site is not None else None,
            "depth_estimated": bool(site.cf_depth_estimated) if site is not None else False,
        },
        "father": {
            "state": site.father_state if site is not None else None,
            "vaf": _number(site.father_vaf) if site is not None else None,
            "depth": site.father_dp if site is not None else None,
        },
        "de_novo": (
            {
                "window": triage.window,
                "score": triage.score,
                "label": triage.label,
                "reasons": list(triage.reasons),
                "other_cfdna_carriers": triage.other_cfdna_carriers,
            }
            if triage is not None
            else None
        ),
        "recurrence": _recurrence(item),
    }


def _recurrence(item: NiptClassifiedVariant) -> dict[str, Any]:
    """How the reported variant's allele recurred in the other cfDNA samples of the assay
    when it was signed out, as the variant list showed it. Not how many other samples there
    were: every pregnancy of the assay imported since would change the record (and make the
    sign-out check name the section as changed), not only one that carries the allele."""
    recurrence = item.recurrence
    if recurrence is None:
        # get_family_nipt_record counts every reported variant's recurrence.
        raise RuntimeError("NIPT variant was recorded before its recurrence was counted")
    counts = recurrence.counts
    return {
        "carriers": counts.carriers,
        "maternal": counts.maternal,
        "paternal_low_vaf": counts.paternal_low_vaf,
        "unexplained_low_vaf": counts.unexplained_low_vaf,
        "unexplained_median_vaf": _number(counts.unexplained_median_vaf),
        "protection": recurrence.protection,
    }


def _allele(allele: RecessiveAllele) -> dict[str, Any]:
    return {
        "variant_id": allele.variant_id,
        "inherited_probability": _number(allele.inherited_probability),
        "category": allele.category,
        "note": allele.note,
    }


def _recessive_gene(gene: RecessiveGeneRisk) -> dict[str, Any]:
    return {
        "gene": gene.gene,
        "risk": _number(gene.risk),
        "risk_uses_prior": gene.risk_uses_prior,
        "maternal_variant_id": gene.maternal_variant_id,
        "paternal_variant_id": gene.paternal_variant_id,
        "maternal": [_allele(allele) for allele in gene.maternal],
        "paternal": [_allele(allele) for allele in gene.paternal],
    }


def _model(qc: NiptQualityThresholds) -> dict[str, Any]:
    """The model the analysis ran with: its validation, its constants, the cfDNA quality
    filter and the paternal genotype classes, and the fetal-fraction rules."""
    return {
        "reference": NIPT_MODEL_REFERENCE,
        "overdispersion": OVERDISPERSION,
        "maternal_het_bias": MATERNAL_HET_BIAS,
        "background_error_rate": BACKGROUND_ERROR_RATE,
        "quality_filter": {key: _number(value) for key, value in sorted(dataclasses.asdict(qc).items())},
        "fetal_fraction": {
            "hard_floor": FF_HARD_FLOOR,
            "min_sites": FF_MIN_SITES,
            "max_ci_halfwidth": FF_MAX_CI_HALFWIDTH,
        },
    }


def nipt_section(
    *,
    trio: NiptTrio,
    assay_key: str,
    analysis: NiptFamilyRecord,
    target_tables: list[dict[str, Any]],
    coverage_notes: dict[str, Any] | None,
    qc: NiptQualityThresholds,
    panel: AnalysisGenePanel | None = None,
) -> dict[str, Any]:
    """The ``nipt`` section from the family's NIPT analysis (``nipt_service``), with its
    diagnostic gene panel and the coverage of that panel's targets, and the notes of the
    coverage check over the coverage tracks (``_coverage_notes``)."""
    summary = analysis.summary
    checks = nipt_quality_checks(summary, father=trio.father_sample_id)
    result = summary.analysis
    window = checks.de_novo_window
    variants = sorted(
        (_variant(item) for item in analysis.variants),
        key=lambda entry: (str(entry["variant_id"]), str(entry["source"] or "")),
    )
    return {
        "samples": {
            "father": trio.father_sample_id,
            "maternal_plasma": trio.cfdna_sample_id,
            "fetuses": list(trio.fetus_sample_ids),
        },
        # The pregnancy's monogenic NIPT, and what its fetal results say of a multiple pregnancy.
        "analysis": trio.analysis,
        "multiple_pregnancy_note": multiple_pregnancy_note(len(trio.fetus_sample_ids)),
        "fetal_fraction": _fetal_fraction(checks),
        "de_novo_window": (
            {key: _number(value) for key, value in dataclasses.asdict(window).items()}
            if window is not None
            else None
        ),
        "category_counts": {
            str(category): count for category, count in sorted(result.category_counts.items())
        },
        "filter_counts": {key: result.filter_counts[key] for key in sorted(result.filter_counts)},
        "quality_failures": {key: checks.quality_failures[key] for key in sorted(checks.quality_failures)},
        "checks": _checks(checks),
        "target_coverage": _target_coverage(checks.target_coverage),
        # The analysis's diagnostic gene panel (REQ-NIPT-019): its version and gene list's
        # digest, null when it has none (a sign-out concern), and its targets' coverage.
        "diagnostic_panel": panel.frozen() if panel is not None else None,
        "panel_target_coverage": _panel_target_coverage(panel, analysis),
        COVERAGE_NOTES_KEY: coverage_notes,
        "variants": variants,
        "not_classified": [
            {"variant_id": variant_id, "reason": reason}
            for variant_id, reason in sorted(analysis.unclassified.items())
        ],
        "recessive_genes": [_recessive_gene(gene) for gene in analysis.recessive_genes],
        "inputs": {
            "target_tables": target_tables,
            "artifact_list": {
                "assay_key": assay_key,
                "entries": len(analysis.listed_artifacts),
                # The digest of its alleles and their recurrence counts (a count decides
                # whether an allele the father carries stays in): the list states no
                # version, and it changes as the lab curates it (every change is on the
                # system:nipt-artifacts audit chain).
                "digest": canonical_hash(
                    [[variant_id, count] for variant_id, count in sorted(analysis.listed_artifacts.items())]
                ),
                "removed": len(analysis.removed_artifacts),
                "kept": [
                    {"variant_id": variant_id, "reason": reason}
                    for variant_id, reason in sorted(analysis.protected_artifacts.items())
                ],
            },
        },
        "model": _model(qc),
    }


async def nipt_results(
    session: AsyncSession,
    context: FamilyMetadataContext,
    *,
    reported_ids: Iterable[str],
    shared_nipt: SharedNiptAnalysis | None = None,
) -> dict[str, Any]:
    """The NIPT section of a monogenic NIPT family's record: its analysis as the NIPT page
    computes it now, the reported variants (``reported_ids``) among it. With ``shared_nipt``,
    the analysis the sign-out's Sample QC read, so the two report the same values."""
    resolved = await family_nipt_trio(session, context)
    if resolved is None:
        # Frozen in the section's place: the NIPT page cannot run either.
        return {"unavailable": NIPT_TRIO_UNRESOLVED}
    trio, assay_key = resolved
    qc = NiptQualityThresholds()
    analysis = await get_family_nipt_record(
        session,
        context=context,
        trio=trio,
        assay_key=assay_key,
        variant_ids=reported_ids,
        qc=qc,
        shared_nipt=shared_nipt,
    )
    panel = await load_analysis_gene_panel(
        session, family_uuid=context.family_uuid, analysis=nipt_analysis_key(trio)
    )
    target_tables = await _target_tables(session, context, trio)
    coverage_notes = await _coverage_notes(session, context, trio, analysis, panel)
    return nipt_section(
        trio=trio,
        assay_key=assay_key,
        analysis=analysis,
        target_tables=target_tables,
        coverage_notes=coverage_notes,
        qc=qc,
        panel=panel,
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def nipt_signout_concerns(section: Any) -> list[tuple[str | None, str]]:
    """The NIPT quality checks of a record's ``nipt`` section that failed, each as (what it
    is about, why): no fetal-fraction estimate, fetal-sex signals that disagree, male DNA
    given as the maternal plasma, no diagnostic gene panel (the analysis states no scope).
    They gate the sign-out like a failed Sample QC check. [] for a section that holds no
    checks (none, or one that could not be read).

    Paternity is not among them: the Sample QC reads it from the same analysis, and its gate
    already acts on it (``sample_qc.paternity_check``).
    """
    if not isinstance(section, Mapping) or section.get("unavailable"):
        return []
    concerns: list[tuple[str | None, str]] = []
    fetal_fraction = section.get("fetal_fraction")
    if isinstance(fetal_fraction, Mapping) and fetal_fraction.get("status") == "fail":
        concerns.append(("NIPT fetal fraction", str(fetal_fraction.get("message") or "not estimated.")))
    checks = _mapping(section.get("checks"))
    fetal_sex = checks.get("fetal_sex")
    if isinstance(fetal_sex, Mapping) and fetal_sex.get("status") == "fail":
        concerns.append(
            ("NIPT fetal sex", str(fetal_sex.get("message") or "the two signals disagree."))
        )
    if section.get("diagnostic_panel") is None:
        concerns.append(
            (
                "NIPT diagnostic panel",
                "no diagnostic gene panel is set: the analysis states no scope. Set one on the NIPT page.",
            )
        )
    plasma = checks.get("plasma_sample")
    if isinstance(plasma, Mapping) and plasma.get("status") == "fail":
        sample = _mapping(section.get("samples")).get("maternal_plasma")
        concerns.append(
            (
                f"Sample {sample} (maternal plasma)" if sample else "NIPT maternal plasma sample",
                str(plasma.get("message") or "this sample cannot be maternal plasma."),
            )
        )
    return concerns
