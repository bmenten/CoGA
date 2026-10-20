"""The NIPT section of the signed analysis record (REG-2; owner decision of 2026-10-14).

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
  (its validation, constants and quality filter).

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
import math
from datetime import datetime
from typing import Any, Iterable, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .family_metadata_context import FamilyMetadataContext
from .hash_chain import canonical_hash
from .nipt import (
    DEFAULT_NIPT_ASSAY_KEY,
    MONOGENIC_NIPT_ANALYSIS_TYPE,
    NiptTrio,
    assay_key_for_sample_metadata,
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
from .nipt_quality_checks import NiptQualityChecks, nipt_quality_checks
from .nipt_service import (
    NiptClassifiedVariant,
    NiptFamilyRecord,
    SharedNiptAnalysis,
    get_family_nipt_record,
)
from .nipt_target_coverage import TARGET_COVERAGE_TRACK_TYPE, TargetCoverageSummary
from .nipt_triage import RecessiveAllele, RecessiveGeneRisk

# Frozen in the section's place when the trio cannot be resolved: no father, or no maternal
# plasma sample, among the family's active members (the NIPT page cannot run either).
TRIO_UNRESOLVED = (
    "The NIPT trio could not be resolved: the family has no active father or no maternal "
    "plasma sample."
)

_ANALYSIS_TYPE_SQL = text(
    """
    SELECT metadata ->> 'analysis_type' AS analysis_type
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


async def is_nipt_family(session: AsyncSession, context: FamilyMetadataContext) -> bool:
    """Whether the family is a monogenic NIPT family (``metadata.analysis_type``), as the
    NIPT page and the Sample QC read it."""
    row = (
        await session.execute(_ANALYSIS_TYPE_SQL, {"family_uuid": context.family_uuid})
    ).mappings().first()
    return row is not None and row.get("analysis_type") == MONOGENIC_NIPT_ANALYSIS_TYPE


def nipt_trio_of(context: FamilyMetadataContext) -> tuple[NiptTrio, str] | None:
    """The family's NIPT trio and the artifact-list scope of its cfDNA assay, from its active
    members (``nipt.resolve_nipt_trio``'s rules); None when the trio cannot be resolved."""
    rows = list(context.sample_rows)
    trio = resolve_nipt_trio_from_members(
        (
            str(row.get("sample_id")),
            row.get("role"),
            row.get("sample_metadata") if isinstance(row.get("sample_metadata"), Mapping) else None,
        )
        for row in rows
        if row.get("sample_id")
    )
    if trio is None:
        return None
    assay_key = next(
        (
            assay_key_for_sample_metadata(
                row.get("sample_metadata") if isinstance(row.get("sample_metadata"), dict) else None
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
    """The per-target coverage tables the analysis read: the father's and the plasma's, each
    with its file, its number of targets and when it was loaded."""
    rows = (
        await session.execute(
            _TARGET_TABLES_SQL,
            {
                "family_uuid": context.family_uuid,
                "track_type": TARGET_COVERAGE_TRACK_TYPE,
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
    qc: NiptQualityThresholds,
) -> dict[str, Any]:
    """The ``nipt`` section from the family's NIPT analysis (``nipt_service``)."""
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
            "fetus": trio.fetus_sample_id,
        },
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
                # Its alleles' digest: the list states no version, and it changes as the
                # lab curates it (every change is on the system:nipt-artifacts audit chain).
                "digest": canonical_hash(sorted(analysis.listed_artifacts)),
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
    resolved = nipt_trio_of(context)
    if resolved is None:
        return {"unavailable": TRIO_UNRESOLVED}
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
    target_tables = await _target_tables(session, context, trio)
    return nipt_section(
        trio=trio, assay_key=assay_key, analysis=analysis, target_tables=target_tables, qc=qc
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def nipt_signout_concerns(section: Any) -> list[tuple[str | None, str]]:
    """The NIPT quality checks of a record's ``nipt`` section that failed, each as (what it
    is about, why): no fetal-fraction estimate, fetal-sex signals that disagree, male DNA
    given as the maternal plasma. They gate the sign-out like a failed Sample QC check.
    [] for a section that holds no checks (none, or one that could not be read).

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
