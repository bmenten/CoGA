"""Sequencing-QC thresholds: the catalogue of gateable metrics, admin CRUD, and evaluation.

Per-sample sequencing QC is recorded at package import into
``samples.metadata["sequencing_qc"]`` (see ``family_package_qc.py``). On its own it is
just numbers; a lab needs a cut-off to say whether a run is interpretable. This module
holds that cut-off configuration and turns a sample's metrics into a verdict.

Two deliberate splits:

* **The metric catalogue is code, the cut-offs are data.** Which metrics exist, their
  units, and whether a *low* or a *high* value is the bad one follow from what the QC
  parsers emit — they are not opinions. Making direction configurable would let an
  operator invert a comparison and turn a failing run green, so it is fixed here.
* **Thresholds are grouped into named profiles.** A single global cut-off cannot serve
  more than one assay: ~18x mean depth is normal for PacBio HiFi WGS and a hard failure
  on a 100x panel.

Evaluation is server-side so the same verdict reaches the workspace, the sample-QC page
and a report, rather than three clients re-deriving it from raw numbers.
"""

from __future__ import annotations

import math
import re

from dataclasses import dataclass
from typing import Any, Literal, Sequence

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .family_analysis_types import analysis_type_of
from .sample_integrity_qc import Status


# A lower value is the failing side for almost every sequencing-QC metric (less depth,
# shorter reads, worse quality, fewer bases). The exceptions are spread/one-sided-high
# measures, which are called out individually below.
Direction = Literal["lower_is_worse", "higher_is_worse"]

# The repo already has one QC status vocabulary — sample_integrity_qc.Status — and the
# frontend types it as QcStatus. Reuse it rather than adding a third spelling: "skip" is
# this codebase's word for "not assessed", and "fail" for what the operator calls an
# error. The admin UI still *labels* the bounds "warning" and "error".
QcVerdict = Status

# Severity order, so a sample's overall verdict is the worst of its metrics.
_VERDICT_RANK: dict[QcVerdict, int] = {"skip": 0, "pass": 1, "warn": 2, "fail": 3}


#: Where a metric's value comes from. ``sequencing_qc`` metrics are read straight out of
#: ``samples.metadata["sequencing_qc"]`` by ``evaluate_sequencing_qc``. ``mtdna`` metrics
#: are computed by the mitochondrial analysis from its own coverage/contamination inputs,
#: so that service evaluates them itself — but their cut-offs are configured here, in the
#: one place, rather than hard-coded in the analysis.
#: ``cnipt`` bounds are the conventional NIPT's result rule (``cnipt_result``), which reads them
#: from its own profile (``nipt_conventional``) and applies its own comparisons. ``mnipt``
#: bounds are the monogenic NIPT's QC gate (``nipt_qc_gate``), read from ``nipt_monogenic``.
MetricSource = Literal["sequencing_qc", "mtdna", "cnipt", "mnipt"]


@dataclass(frozen=True, slots=True)
class QcMetric:
    key: str
    label: str
    unit: str
    direction: Direction
    #: Dotted path into the metric's source document — e.g. ``depth.mean_depth``.
    path: tuple[str, ...]
    help_text: str
    source: MetricSource = "sequencing_qc"


def _metric(
    key: str,
    label: str,
    unit: str,
    direction: Direction,
    help_text: str,
    source: MetricSource = "sequencing_qc",
) -> QcMetric:
    return QcMetric(
        key=key,
        label=label,
        unit=unit,
        direction=direction,
        path=tuple(key.split(".")),
        help_text=help_text,
        source=source,
    )


# Every scalar the QC parsers emit. Nested structures (``reads.quality_cutoffs``,
# ``depth.mean_depth_by_chromosome``) are deliberately excluded: they are per-bucket and
# per-contig detail, and a threshold on "chr7 depth" is a different feature with a
# different UI. ``depth.total_bases`` is excluded too — it duplicates
# ``reads.total_bases_aligned`` closely enough that gating on both is noise.
QC_METRICS: tuple[QcMetric, ...] = (
    _metric(
        "depth.mean_depth",
        "Mean depth",
        "x",
        "lower_is_worse",
        "Genome-wide mean coverage (mosdepth, or the PGT pipeline's mean-coverage step). "
        "The first number an interpreter reads.",
    ),
    _metric(
        "depth.mito_mean_depth",
        "chrM mean depth",
        "x",
        "lower_is_worse",
        "Mitochondrial coverage; drives whether heteroplasmy is callable.",
    ),
    _metric(
        "reads.read_length_n50",
        "Read-length N50",
        "bp",
        "lower_is_worse",
        "Half the sequenced bases sit in reads at least this long. The headline long-read metric.",
    ),
    _metric(
        "reads.median_read_length",
        "Median read length",
        "bp",
        "lower_is_worse",
        "Median length across all reads.",
    ),
    _metric(
        "reads.mean_read_length",
        "Mean read length",
        "bp",
        "lower_is_worse",
        "Mean length across all reads.",
    ),
    _metric(
        "reads.median_read_quality",
        "Median read quality",
        "Q",
        "lower_is_worse",
        "Median per-read Phred quality.",
    ),
    _metric(
        "reads.mean_read_quality",
        "Mean read quality",
        "Q",
        "lower_is_worse",
        "Mean per-read Phred quality.",
    ),
    _metric(
        "reads.median_percent_identity",
        "Median identity",
        "%",
        "lower_is_worse",
        "Median per-read alignment identity against the reference.",
    ),
    _metric(
        "reads.mean_percent_identity",
        "Mean identity",
        "%",
        "lower_is_worse",
        "Mean per-read alignment identity against the reference.",
    ),
    _metric(
        "reads.fraction_bases_aligned",
        "Fraction bases aligned",
        "fraction",
        "lower_is_worse",
        "Share of sequenced bases that aligned. A low value points at contamination or the wrong reference.",
    ),
    _metric(
        "reads.read_count",
        "Read count",
        "reads",
        "lower_is_worse",
        "Number of reads in the run.",
    ),
    _metric(
        "reads.total_bases",
        "Total bases",
        "bp",
        "lower_is_worse",
        "Sequenced yield before alignment.",
    ),
    _metric(
        "reads.total_bases_aligned",
        "Total bases aligned",
        "bp",
        "lower_is_worse",
        "Aligned yield — the bases actually available to call from.",
    ),
    # The one genuine higher-is-worse metric here: read-length spread. A very wide
    # distribution on a long-read run points at fragmentation or a library problem,
    # whereas a narrow one is never a fault.
    _metric(
        "reads.stdev_read_length",
        "Read-length spread (SD)",
        "bp",
        "higher_is_worse",
        "Standard deviation of read length. A wide spread points at library fragmentation.",
    ),
    # Alignment QC from Qualimap bamqc (the PGT pipeline runs it on every sample).
    _metric(
        "alignment.mapped_reads_percent",
        "Mapped reads",
        "%",
        "lower_is_worse",
        "Share of reads that aligned (Qualimap). A low value points at contamination or the wrong reference.",
    ),
    _metric(
        "alignment.duplicated_reads_percent",
        "Duplicate reads",
        "%",
        "higher_is_worse",
        "Share of reads flagged as duplicates (Qualimap). Whole-genome amplification of a "
        "biopsy raises it.",
    ),
    # Embryo QC from the PGT pipeline (nf-cmgg/copgtm), per embryo against its parents.
    _metric(
        "pgt.allele_dropout_rate",
        "Allele drop-out (ADO)",
        "%",
        "higher_is_worse",
        "Share of sites where the embryo's call lost one parental allele (Picard Mendelian "
        "violations). Whole-genome amplification drops alleles; a high rate makes the "
        "embryo's heterozygous calls, and so its haplotypes, unreliable.",
    ),
    _metric(
        "pgt.allele_dropin_rate",
        "Allele drop-in (ADI)",
        "%",
        "higher_is_worse",
        "Share of sites where the embryo's call carries an allele neither parent has "
        "(Picard Mendelian violations). Points at contamination or amplification artefacts.",
    ),
    _metric(
        "pgt.mendelian_concordance",
        "Mendelian concordance before imputation",
        "%",
        "lower_is_worse",
        "Share of the embryo's directly called genotypes consistent with the parents' (RTG "
        "Tools), before the parents were imputed. Low by design at low parental depth.",
    ),
    _metric(
        "pgt.mendelian_concordance_imputed",
        "Mendelian concordance after imputation",
        "%",
        "lower_is_worse",
        "The same consistency check against the imputed parental genotypes (RTG Tools). A "
        "low value points at a sample swap or a wrong pedigree.",
    ),
    # Mitochondrial QC. Evaluated by mitochondrial_analysis against its own coverage and
    # contamination inputs, but configured here so there is one place a lab sets a QC
    # cut-off.
    _metric(
        "mtdna.mean_depth",
        "mtDNA mean depth",
        "x",
        "lower_is_worse",
        "Mean chrM coverage behind the mitochondrial analysis; drives whether heteroplasmy is callable.",
        source="mtdna",
    ),
    _metric(
        "mtdna.contamination",
        "mtDNA contamination",
        "fraction",
        "higher_is_worse",
        "Haplocheck-style contamination estimate. A raised value makes apparent heteroplasmy unreliable.",
        source="mtdna",
    ),
    # The conventional NIPT's result rule (``cnipt_result``), set in the profile
    # ``nipt_conventional``. Until QA sets a bound, the rule applies its provisional value and
    # says so in the result. The rule compares with "at or above" and "below" as each text says.
    _metric(
        "cnipt.fetal_fraction",
        "cNIPT: fetal fraction",
        "fraction",
        "lower_is_worse",
        "ccnv's effective fetal fraction of the maternal plasma. Below the error bound the plasma "
        "gets no result; below the warning bound its result carries a warning. Provisional: "
        "error 0.04.",
        source="cnipt",
    ),
    _metric(
        "cnipt.call_zscore",
        "cNIPT: segment |z-score|",
        "z",
        "higher_is_worse",
        "A segment's |z-score| at or above the error bound is a finding: high risk where it is "
        "concordant with the fetal fraction, inconclusive where not. At or above the warning "
        "bound and below the error bound it is inconclusive (the grey zone). Provisional: "
        "warning 2.5, error 3.",
        source="cnipt",
    ),
    _metric(
        "cnipt.implied_ff_ratio_min",
        "cNIPT: implied fetal fraction, lowest concordant share",
        "× FF",
        "lower_is_worse",
        "A finding whose implied fetal fraction is below the error bound times the fetal "
        "fraction is sub-fetal (a mosaic or a maternal event): inconclusive. Provisional: "
        "error 0.5.",
        source="cnipt",
    ),
    _metric(
        "cnipt.implied_ff_ratio_max",
        "cNIPT: implied fetal fraction, highest concordant share",
        "× FF",
        "higher_is_worse",
        "A finding whose implied fetal fraction is above the error bound times the fetal "
        "fraction is super-fetal (a maternal event): inconclusive. Provisional: error 1.5.",
        source="cnipt",
    ),
    _metric(
        "cnipt.sca_confidence",
        "cNIPT: sex-chromosome call confidence",
        "fraction",
        "lower_is_worse",
        "ccnv's sex-chromosome call below the error bound is inconclusive. Provisional: "
        "error 0.99.",
        source="cnipt",
    ),
    _metric(
        "cnipt.min_cnv_size",
        "cNIPT: smallest segmental CNV assessed",
        "Mb",
        "lower_is_worse",
        "A segmental CNV shorter than the error bound is not assessed (listed only). ccnv's "
        "limits of detection raise it at a low fetal fraction. Provisional: error 3.",
        source="cnipt",
    ),
    # The monogenic NIPT's QC gate (``nipt_qc_gate``), set in the profile ``nipt_monogenic``.
    # Until QA sets the bound, the gate applies its provisional value and says so.
    _metric(
        "mnipt.expected_fetal_reads",
        "mNIPT: expected fetal reads",
        "reads",
        "lower_is_worse",
        "The maternal plasma's median target depth times half its fetal fraction: the reads a "
        "fetal-specific allele (the father's, or a de novo one) is expected to have. Below the "
        "error bound the check fails (a result without such an allele is not reliable); below "
        "the warning bound it warns. A capture target whose mean depth gives fewer than the "
        "error bound is counted as too thin for it. Provisional: error 40 (the R NIPT-M "
        "v0.7.3 review).",
        source="mnipt",
    ),
)

QC_METRICS_BY_KEY: dict[str, QcMetric] = {metric.key: metric for metric in QC_METRICS}


def metric_value(sequencing_qc: dict[str, Any] | None, metric: QcMetric) -> float | None:
    """Follow a metric's dotted path into a sample's recorded QC, or None."""
    if not isinstance(sequencing_qc, dict):
        return None
    node: Any = sequencing_qc
    for part in metric.path:
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    if isinstance(node, bool) or not isinstance(node, (int, float)):
        return None
    return float(node)


def evaluate_metric(
    value: float | None,
    *,
    direction: Direction,
    warn_value: float | None,
    error_value: float | None,
) -> QcVerdict:
    """Verdict for one metric.

    ``skip`` when there is nothing to judge — no measurement, or no cut-off
    configured. That is distinct from ``pass``: a metric nobody set a threshold for has
    not been checked, and must not be reported as if it had been.

    The error bound is tested first, so an error/warn pair entered the wrong way round
    still fails rather than silently reporting a warning.
    """
    if value is None:
        return "skip"
    if warn_value is None and error_value is None:
        return "skip"
    if direction == "lower_is_worse":
        if error_value is not None and value < error_value:
            return "fail"
        if warn_value is not None and value < warn_value:
            return "warn"
        return "pass"
    if error_value is not None and value > error_value:
        return "fail"
    if warn_value is not None and value > warn_value:
        return "warn"
    return "pass"


def worst_verdict(verdicts: Sequence[QcVerdict]) -> QcVerdict:
    """The overall verdict for a sample: the worst of its per-metric verdicts."""
    worst: QcVerdict = "skip"
    for verdict in verdicts:
        if _VERDICT_RANK[verdict] > _VERDICT_RANK[worst]:
            worst = verdict
    return worst


def evaluate_sequencing_qc(
    sequencing_qc: dict[str, Any] | None,
    thresholds: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Per-metric verdicts plus an overall verdict for one sample.

    ``thresholds`` maps metric key -> ``{"warn_value", "error_value", "warn_profile_key",
    "error_profile_key"}``, already resolved bound by bound (``resolve_analysis_qc_thresholds``).
    Returns None when the sample has no recorded QC,
    so the caller can leave the payload untouched rather than attaching an empty verdict.
    """
    if not isinstance(sequencing_qc, dict) or not sequencing_qc:
        return None
    metrics: list[dict[str, Any]] = []
    for metric in QC_METRICS:
        if metric.source != "sequencing_qc":
            continue
        value = metric_value(sequencing_qc, metric)
        if value is None:
            continue
        bounds = thresholds.get(metric.key) or {}
        warn_value = bounds.get("warn_value")
        error_value = bounds.get("error_value")
        # The profile each bound came from (resolve_analysis_qc_thresholds): each bound falls
        # back on its own, so the two may differ. None for a bound no profile sets.
        warn_profile_key = bounds.get("warn_profile_key")
        error_profile_key = bounds.get("error_profile_key")
        verdict = evaluate_metric(
            value,
            direction=metric.direction,
            warn_value=warn_value,
            error_value=error_value,
        )
        metrics.append(
            {
                "metric_key": metric.key,
                "label": metric.label,
                "unit": metric.unit,
                "direction": metric.direction,
                "value": value,
                "warn_value": warn_value,
                "error_value": error_value,
                "verdict": verdict,
                "warn_profile_key": warn_profile_key,
                "error_profile_key": error_profile_key,
            }
        )
    if not metrics:
        return None
    return {
        "verdict": worst_verdict([entry["verdict"] for entry in metrics]),
        "metrics": metrics,
        "breached": [
            entry["metric_key"] for entry in metrics if entry["verdict"] in {"warn", "fail"}
        ],
    }


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


async def list_qc_threshold_profiles(session: AsyncSession) -> list[dict[str, Any]]:
    """Every profile with its thresholds, ordered for display."""
    rows = (
        await session.execute(
            text(
                """
                SELECT p.id::text AS id,
                       p.key,
                       p.label,
                       p.description,
                       p.is_default,
                       p.sort_order,
                       t.metric_key,
                       t.warn_value,
                       t.error_value
                FROM qc_threshold_profiles p
                LEFT JOIN qc_thresholds t ON t.profile_id = p.id
                ORDER BY p.sort_order, p.key, t.metric_key
                """
            )
        )
    ).mappings()
    profiles: dict[str, dict[str, Any]] = {}
    for row in rows:
        profile = profiles.setdefault(
            row["id"],
            {
                "id": row["id"],
                "key": row["key"],
                "label": row["label"],
                "description": row["description"],
                "is_default": row["is_default"],
                "sort_order": row["sort_order"],
                "thresholds": [],
            },
        )
        if row["metric_key"] is None:
            continue
        # A limit is stored only on a metric of the catalogue (``set_qc_threshold``).
        metric = QC_METRICS_BY_KEY[row["metric_key"]]
        profile["thresholds"].append(
            {
                "metric_key": row["metric_key"],
                "label": metric.label,
                "unit": metric.unit,
                "direction": metric.direction,
                "warn_value": row["warn_value"],
                "error_value": row["error_value"],
            }
        )
    return list(profiles.values())


async def _record_threshold_change(
    session: AsyncSession,
    *,
    profile_key: str,
    metric_key: str,
    previous: dict[str, Any] | None,
    warn_value: float | None,
    error_value: float | None,
    user_id: str | None,
    user_email: str | None,
    reason: str,
) -> None:
    """Append both sides of a cut-off edit to the immutable history.

    The request-audit pipeline already records the path, body and actor, but not the
    value that was replaced — so it cannot answer "who lowered the depth limit, and from
    what". This can, and ``reason`` carries the part neither can infer: *why*. The
    numbers either side do not distinguish a limit lowered because a validation study
    supported it from one lowered because a run was inconvenient.
    """
    await session.execute(
        text(
            """
            INSERT INTO qc_threshold_changes (
                profile_key, metric_key,
                previous_warn_value, previous_error_value,
                warn_value, error_value,
                changed_by, changed_by_email, reason
            )
            VALUES (
                :profile_key, :metric_key,
                :previous_warn_value, :previous_error_value,
                :warn_value, :error_value,
                CAST(NULLIF(:user_id, '') AS uuid), NULLIF(:user_email, ''), :reason
            )
            """
        ),
        {
            "profile_key": profile_key,
            "metric_key": metric_key,
            "previous_warn_value": (previous or {}).get("warn_value"),
            "previous_error_value": (previous or {}).get("error_value"),
            "warn_value": warn_value,
            "error_value": error_value,
            "user_id": user_id or "",
            "user_email": user_email or "",
            "reason": reason,
        },
    )


async def list_qc_threshold_changes(
    session: AsyncSession, *, limit: int = 100
) -> list[dict[str, Any]]:
    """Most recent cut-off edits, newest first."""
    rows = (
        await session.execute(
            text(
                """
                SELECT profile_key, metric_key,
                       previous_warn_value, previous_error_value,
                       warn_value, error_value,
                       changed_by_email, changed_at, reason
                FROM qc_threshold_changes
                ORDER BY changed_at DESC
                LIMIT :limit
                """
            ),
            # int-coerced, never interpolated.
            {"limit": max(1, min(int(limit), 500))},
        )
    ).mappings()
    return [dict(row) for row in rows]


_PROFILE_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_]{1,62}$")


async def create_qc_threshold_profile(
    session: AsyncSession,
    *,
    label: str,
    key: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Add an empty QC threshold profile.

    Empty on purpose. A new profile exists because an assay is judged on a
    different footing, so copying another profile's cut-offs into it would put an
    unreviewed number in front of an operator with a laboratory's authority behind
    it. Every metric starts unconfigured, which reports as *not assessed*.

    The key is derived from the label when not given, because it appears in a
    family's ``qc_profile`` metadata and in URLs; it is immutable afterwards, since
    families reference it.
    """

    label = (label or "").strip()
    if not label:
        raise HTTPException(status_code=400, detail="A profile label is required")
    if key is None:
        key = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    key = (key or "").strip().lower()
    if not _PROFILE_KEY_PATTERN.match(key):
        raise HTTPException(
            status_code=400,
            detail=(
                "A profile key must be 2-63 characters of lowercase letters, digits or "
                "underscores, starting with a letter or digit"
            ),
        )
    existing = (
        await session.execute(
            text("SELECT label FROM qc_threshold_profiles WHERE key = :key"),
            {"key": key},
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"A QC threshold profile with key '{key}' already exists ({existing})",
        )
    # New profiles sort after the seeded ones rather than interleaving with them.
    row = (
        await session.execute(
            text(
                """
                INSERT INTO qc_threshold_profiles (key, label, description, is_default, sort_order)
                VALUES (
                    :key,
                    :label,
                    NULLIF(:description, ''),
                    false,
                    COALESCE((SELECT MAX(sort_order) FROM qc_threshold_profiles), 0) + 10
                )
                RETURNING id::text AS id, key, label, description, is_default, sort_order
                """
            ),
            {"key": key, "label": label, "description": (description or "").strip()},
        )
    ).mappings().one()
    await session.commit()
    return dict(row)


async def set_qc_threshold(
    session: AsyncSession,
    *,
    profile_key: str,
    metric_key: str,
    warn_value: float | None,
    error_value: float | None,
    user_id: str | None,
    user_email: str | None = None,
    reason: str,
) -> dict[str, Any]:
    """Upsert one metric's cut-offs within a profile.

    Clearing both bounds deletes the row: a metric with no cut-off is not configured,
    and keeping an all-null row would make the admin list imply otherwise.

    ``reason`` is required and non-empty. The cut-offs themselves are agreed in each
    test's clinical validation report, outside this tool; what the tool has to be able
    to show is that a change here was deliberate and attributable to that decision.

    A metric outside the catalogue takes no cut-off and is refused before anything is
    read (REQ-QC-003).
    """
    reason = (reason or "").strip()
    if not reason:
        raise HTTPException(
            status_code=400,
            detail="A reason is required when changing a QC cut-off.",
        )
    metric = QC_METRICS_BY_KEY.get(metric_key)
    if metric is None:
        raise HTTPException(status_code=400, detail=f"Unknown QC metric: {metric_key}")
    # A cut-off is a finite number: NaN or infinity would switch off the check it bounds (no
    # value is below NaN, every value below infinity) without anything saying so.
    if any(value is not None and not math.isfinite(value) for value in (warn_value, error_value)):
        raise HTTPException(status_code=400, detail="A QC cut-off must be a finite number.")
    clearing = warn_value is None and error_value is None
    profile_id = (
        await session.execute(
            text("SELECT id::text FROM qc_threshold_profiles WHERE key = :key"),
            {"key": profile_key},
        )
    ).scalar_one_or_none()
    if profile_id is None:
        raise HTTPException(status_code=404, detail=f"Unknown QC threshold profile: {profile_key}")
    previous = (
        await session.execute(
            text(
                """
                SELECT warn_value, error_value FROM qc_thresholds
                WHERE profile_id = CAST(:profile_id AS uuid) AND metric_key = :metric_key
                """
            ),
            {"profile_id": profile_id, "metric_key": metric_key},
        )
    ).mappings().first()
    previous_bounds = dict(previous) if previous else None

    if clearing:
        await session.execute(
            text(
                """
                DELETE FROM qc_thresholds
                WHERE profile_id = CAST(:profile_id AS uuid) AND metric_key = :metric_key
                """
            ),
            {"profile_id": profile_id, "metric_key": metric_key},
        )
        await _record_threshold_change(
            session,
            profile_key=profile_key,
            metric_key=metric_key,
            previous=previous_bounds,
            warn_value=None,
            error_value=None,
            user_id=user_id,
            user_email=user_email,
            reason=reason,
        )
        await session.commit()
        return {"profile_key": profile_key, "metric_key": metric_key, "warn_value": None, "error_value": None}

    # A warning that is stricter than the error bound would make the error branch
    # unreachable — reject rather than store a configuration that cannot fire.
    if warn_value is not None and error_value is not None:
        if metric.direction == "lower_is_worse" and warn_value < error_value:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"For '{metric.label}' a lower value is worse, so the warning bound "
                    "must be greater than or equal to the error bound"
                ),
            )
        if metric.direction == "higher_is_worse" and warn_value > error_value:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"For '{metric.label}' a higher value is worse, so the warning bound "
                    "must be less than or equal to the error bound"
                ),
            )

    await session.execute(
        text(
            """
            INSERT INTO qc_thresholds (
                profile_id, metric_key, warn_value, error_value, direction, updated_by
            )
            VALUES (
                CAST(:profile_id AS uuid),
                :metric_key,
                :warn_value,
                :error_value,
                :direction,
                -- NULLIF rather than a CASE on :user_id — asyncpg cannot infer a
                -- parameter's type when its only other use is `IS NULL`, and the insert
                -- fails with AmbiguousParameterError. Matches the pattern used elsewhere.
                CAST(NULLIF(:user_id, '') AS uuid)
            )
            ON CONFLICT (profile_id, metric_key) DO UPDATE SET
                warn_value = EXCLUDED.warn_value,
                error_value = EXCLUDED.error_value,
                direction = EXCLUDED.direction,
                updated_by = EXCLUDED.updated_by,
                updated_at = timezone('utc', now())
            """
        ),
        {
            "profile_id": profile_id,
            "metric_key": metric_key,
            "warn_value": warn_value,
            "error_value": error_value,
            # Written through from the catalogue, not from the request: the direction is
            # a property of the metric, and accepting it from a client would let a
            # comparison be inverted.
            "direction": metric.direction,
            "user_id": user_id or "",
        },
    )
    await _record_threshold_change(
        session,
        profile_key=profile_key,
        metric_key=metric_key,
        previous=previous_bounds,
        warn_value=warn_value,
        error_value=error_value,
        user_id=user_id,
        user_email=user_email,
        reason=reason,
    )
    await session.commit()
    return {
        "profile_key": profile_key,
        "metric_key": metric_key,
        "warn_value": warn_value,
        "error_value": error_value,
    }


async def resolve_profile_thresholds(session: AsyncSession, profile_key: str) -> dict[str, dict[str, float | None]]:
    """The bounds set in the profile ``profile_key``: metric key → ``{"warn_value",
    "error_value"}``; empty for a profile with none set, or none of that key."""
    rows = (
        await session.execute(
            text(
                """
                SELECT t.metric_key, t.warn_value, t.error_value
                FROM qc_thresholds t
                JOIN qc_threshold_profiles p ON p.id = t.profile_id
                WHERE p.key = :profile_key
                """
            ),
            {"profile_key": profile_key},
        )
    ).mappings()
    return {
        row["metric_key"]: {"warn_value": row["warn_value"], "error_value": row["error_value"]}
        for row in rows
    }


# The QC profile of each analysis type a package may name: its seeded profile's key (an analysis
# of a pregnancy is judged by its type's). An analysis without one is judged by the family's named
# profile and the default.
ANALYSIS_QC_PROFILES: dict[str, str] = {
    "long_read_wgs": "long_read_wgs",
    "short_read_wgs": "short_read_wgs",
    "monogenic_nipt": "nipt_monogenic",
    "conventional_nipt": "nipt_conventional",
    "pgt": "pgt",
}

_PROFILE_BY_KEY = text("SELECT key, label, id::text AS id FROM qc_threshold_profiles WHERE key = :key")
_DEFAULT_PROFILE = text("SELECT key, label, id::text AS id FROM qc_threshold_profiles WHERE is_default LIMIT 1")
_FAMILY_PROFILE_KEY = text("SELECT metadata ->> 'qc_profile' FROM families WHERE id = CAST(:family_uuid AS uuid)")
_PROFILE_THRESHOLDS = text(
    """
    SELECT metric_key, warn_value, error_value
    FROM qc_thresholds
    WHERE profile_id = CAST(:profile_id AS uuid)
      AND (warn_value IS NOT NULL OR error_value IS NOT NULL)
    """
)

# The bounds of a metric, each with the profile it was read from.
_BOUNDS: tuple[tuple[str, str], ...] = (("warn_value", "warn_profile_key"), ("error_value", "error_profile_key"))


async def resolve_analysis_qc_thresholds(
    session: AsyncSession | None,
    *,
    family_uuid: str,
    analysis: str | None,
) -> dict[str, Any]:
    """The cut-offs that judge a family's QC of one analysis (its package record's key; None
    where no analysis is known), bound by bound (the owner's decisions of 2026-11-19 and
    2026-11-20): each metric's warning bound and its error bound each come, on its own, from
    the analysis's own profile (``ANALYSIS_QC_PROFILES``), else the profile the family names
    (``qc_profile``), else the default profile. So short-read and long-read WGS of one family
    are each judged by their own cut-offs once QA enters them, and no bound QA set on the
    family's or the default profile is lost meanwhile: a warning-only cut-off in the analysis's
    profile keeps the default's error bound. An unknown or absent ``qc_profile`` adds nothing,
    so a family cannot quietly opt out of the default's cut-offs by carrying a typo.

    The two bounds of a metric may thus come from two profiles, in an order neither profile
    alone would accept (``set_qc_threshold``): ``evaluate_metric`` tests the error bound
    first, so such a pair still fails rather than reporting a warning.

    Returns ``{"profiles": [{"key", "label"}…], "thresholds": {metric_key: {"warn_value",
    "error_value", "warn_profile_key", "error_profile_key"}}}``: the profiles in the order they
    are read (each once), and each metric with a bound, each bound with the profile it came
    from (None for a bound no profile sets). With no session (presence probes and unit tests
    call the analyses without one) nothing is read, so no cut-off applies and every metric
    evaluates to ``skip``: a sample is never reported as passing a gate that was not consulted.
    """
    if session is None:
        return {"profiles": [], "thresholds": {}}
    keys = [ANALYSIS_QC_PROFILES.get(analysis_type_of(analysis)) if analysis is not None else None]
    keys.append(
        (await session.execute(_FAMILY_PROFILE_KEY, {"family_uuid": family_uuid})).scalar_one_or_none()
    )
    profiles: list[dict[str, Any]] = []
    for key in keys:
        if isinstance(key, str) and key and key not in {profile["key"] for profile in profiles}:
            row = (await session.execute(_PROFILE_BY_KEY, {"key": key})).mappings().first()
            if row is not None:
                profiles.append(dict(row))
    default = (await session.execute(_DEFAULT_PROFILE)).mappings().first()
    if default is not None and default["key"] not in {profile["key"] for profile in profiles}:
        profiles.append(dict(default))
    thresholds: dict[str, dict[str, Any]] = {}
    for profile in profiles:
        rows = (await session.execute(_PROFILE_THRESHOLDS, {"profile_id": profile["id"]})).mappings()
        for row in rows:
            entry = thresholds.setdefault(
                str(row["metric_key"]),
                {bound: None for pair in _BOUNDS for bound in pair},
            )
            # Each bound from the first profile that sets it, the other bound left to the next.
            for bound, source in _BOUNDS:
                if entry[bound] is None and row[bound] is not None:
                    entry[bound] = row[bound]
                    entry[source] = profile["key"]
    return {
        "profiles": [{"key": profile["key"], "label": profile["label"]} for profile in profiles],
        "thresholds": thresholds,
    }
