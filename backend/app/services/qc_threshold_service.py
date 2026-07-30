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

from dataclasses import dataclass
import logging
from typing import Any, Literal, Sequence

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .sample_integrity_qc import Status


logger = logging.getLogger(__name__)


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
MetricSource = Literal["sequencing_qc", "mtdna"]


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
        "Genome-wide mean coverage from mosdepth. The first number an interpreter reads.",
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
    # Mitochondrial QC. Evaluated by mitochondrial_analysis against its own coverage and
    # contamination inputs, but configured here so there is one place a lab sets a QC
    # cut-off. These replace numbers that used to be hard-coded in that module.
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
    thresholds: dict[str, dict[str, float | None]],
) -> dict[str, Any] | None:
    """Per-metric verdicts plus an overall verdict for one sample.

    ``thresholds`` maps metric key -> ``{"warn_value": …, "error_value": …}``, already
    resolved to the sample's profile. Returns None when the sample has no recorded QC,
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
        metric = QC_METRICS_BY_KEY.get(row["metric_key"])
        profile["thresholds"].append(
            {
                "metric_key": row["metric_key"],
                # A row whose metric left the catalogue is still surfaced, so an admin
                # can see and remove it instead of it silently doing nothing.
                "label": metric.label if metric else row["metric_key"],
                "unit": metric.unit if metric else "",
                "direction": metric.direction if metric else "lower_is_worse",
                "known_metric": metric is not None,
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
) -> None:
    """Append both sides of a cut-off edit to the immutable history.

    The request-audit pipeline already records the path, body and actor, but not the
    value that was replaced — so it cannot answer "who lowered the depth limit, and from
    what". This can.
    """
    await session.execute(
        text(
            """
            INSERT INTO qc_threshold_changes (
                profile_key, metric_key,
                previous_warn_value, previous_error_value,
                warn_value, error_value,
                changed_by, changed_by_email
            )
            VALUES (
                :profile_key, :metric_key,
                :previous_warn_value, :previous_error_value,
                :warn_value, :error_value,
                CAST(NULLIF(:user_id, '') AS uuid), NULLIF(:user_email, '')
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
                       changed_by_email, changed_at
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


async def set_qc_threshold(
    session: AsyncSession,
    *,
    profile_key: str,
    metric_key: str,
    warn_value: float | None,
    error_value: float | None,
    user_id: str | None,
    user_email: str | None = None,
) -> dict[str, Any]:
    """Upsert one metric's cut-offs within a profile.

    Clearing both bounds deletes the row: a metric with no cut-off is not configured,
    and keeping an all-null row would make the admin list imply otherwise.
    """
    metric = QC_METRICS_BY_KEY.get(metric_key)
    if metric is None:
        raise HTTPException(status_code=400, detail=f"Unknown QC metric: {metric_key}")
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

    if warn_value is None and error_value is None:
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
    )
    await session.commit()
    return {
        "profile_key": profile_key,
        "metric_key": metric_key,
        "warn_value": warn_value,
        "error_value": error_value,
    }


async def resolve_family_qc_thresholds(
    session: AsyncSession | None,
    *,
    family_uuid: str,
) -> dict[str, Any]:
    """The thresholds that apply to a family: its named profile, else the default.

    Returns ``{"profile_key", "profile_label", "thresholds": {metric_key: {...}}}``.
    An unknown or absent ``qc_profile`` falls back to the default profile rather than
    gating nothing, so a family cannot quietly opt out of its QC gate by carrying a
    typo.

    With no session (presence probes and unit tests call the analysis without one) there
    is nothing to read, so no cut-offs apply and every metric evaluates to ``skip``. That
    is the safe direction: a sample is never reported as passing a gate that was not
    consulted.
    """
    if session is None:
        return {"profile_key": None, "profile_label": None, "thresholds": {}}
    row = (
        await session.execute(
            text(
                """
                SELECT p.key, p.label, p.id::text AS id
                FROM qc_threshold_profiles p
                WHERE p.key = (
                    SELECT f.metadata->>'qc_profile' FROM families f
                    WHERE f.id = CAST(:family_uuid AS uuid)
                )
                """
            ),
            {"family_uuid": family_uuid},
        )
    ).mappings().first()
    if row is None:
        row = (
            await session.execute(
                text(
                    "SELECT key, label, id::text AS id FROM qc_threshold_profiles WHERE is_default LIMIT 1"
                )
            )
        ).mappings().first()
    if row is None:
        return {"profile_key": None, "profile_label": None, "thresholds": {}}
    threshold_rows = (
        await session.execute(
            text(
                """
                SELECT metric_key, warn_value, error_value
                FROM qc_thresholds
                WHERE profile_id = CAST(:profile_id AS uuid)
                """
            ),
            {"profile_id": row["id"]},
        )
    ).mappings()
    return {
        "profile_key": row["key"],
        "profile_label": row["label"],
        "thresholds": {
            entry["metric_key"]: {
                "warn_value": entry["warn_value"],
                "error_value": entry["error_value"],
            }
            for entry in threshold_rows
        },
    }
