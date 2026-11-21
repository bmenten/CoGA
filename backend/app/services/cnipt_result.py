"""The automatic result of a conventional NIPT: per target, from ccnv's output of the maternal
plasma (ccnv_output.py), by the rule the owner chose on 2026-11-19 ("z-score and fetal-fraction
concordance"). Pure: it reads no database.

The targets are each autosome (an aneuploidy, whole or of an arm), the sex chromosomes and the
segmental CNVs. Each gets one result:

- ``high_risk``: a segment whose |z-score| reaches the call bound, with a ratio concordant with
  the fetal fraction (its implied fetal fraction within a band around it, ccnv not flagging it
  discordant) and an origin that is not maternal or mosaic; for the sex chromosomes, ccnv's
  sex-chromosome call of an aneuploidy at the confidence bound;
- ``inconclusive``: such a segment that is not concordant (sub- or super-fetal, flagged
  discordant by ccnv, of maternal or mosaic origin), a segment in the grey zone below the call
  bound, or a sex-chromosome call below the confidence bound or of mixed signals; and a claimed
  target that would be low risk while the fetal fraction is below ccnv's limit of detection of
  arm-level events (``lod_ff_35mb``: the owner's decision of 2026-11-21), which cannot say that
  such an aneuploidy is absent;
- ``low_risk``: none of these;
- ``no_result``: the plasma's fetal fraction is below its bound, or a bound the rule needs is not
  set; or, for a chromosome, ccnv gives no segment of it;
- ``not_assessed``: segmental CNVs where ccnv's limits of detection say the fetal fraction cannot
  show them.

Each target says whether the intended purpose claims it (``claimed``, ``CLAIMED_TARGETS``): the
common trisomies and the sex chromosomes. The other autosomes and the segmental CNVs are shown
outside the validated claim.

The bounds are the laboratory's to set, on the QC thresholds page (profile
``nipt_conventional``, ``qc_threshold_service``), each change with its reason in the append-only
history. Until QA sets one, the provisional value below applies, and the result says so for each
bound (``provisional``). The provisional values are for development: QA and the laboratory
director confirm or change them before the release candidate.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable, Literal, Mapping, Sequence

from .ccnv_output import CcnvQc
from .data_scope import normalize_chromosome

Verdict = Literal["high_risk", "inconclusive", "low_risk", "no_result", "not_assessed"]

#: The QC-threshold profile whose bounds the result reads: the conventional NIPT's assay.
CNIPT_THRESHOLD_PROFILE = "nipt_conventional"

#: The bounds of the rule, by their key in the QC-threshold catalogue
#: (``qc_threshold_service.QC_METRICS``), with the provisional (warning, error) value each takes
#: until QA sets one.
FETAL_FRACTION = "cnipt.fetal_fraction"
CALL_ZSCORE = "cnipt.call_zscore"
IMPLIED_FF_RATIO_MIN = "cnipt.implied_ff_ratio_min"
IMPLIED_FF_RATIO_MAX = "cnipt.implied_ff_ratio_max"
SCA_CONFIDENCE = "cnipt.sca_confidence"
MIN_CNV_SIZE = "cnipt.min_cnv_size"

PROVISIONAL_BOUNDS: dict[str, tuple[float | None, float | None]] = {
    # No result below a fetal fraction of 4 % (ccnv's own clinical lower limit).
    FETAL_FRACTION: (None, 0.04),
    # A finding from |z| 3; a grey zone from 2.5.
    CALL_ZSCORE: (2.5, 3.0),
    # Concordant with the fetal fraction: an implied fetal fraction from half to one and a half
    # times it.
    IMPLIED_FF_RATIO_MIN: (None, 0.5),
    IMPLIED_FF_RATIO_MAX: (None, 1.5),
    # ccnv's sex-chromosome call at a confidence of 99 % or more.
    SCA_CONFIDENCE: (None, 0.99),
    # Segmental CNVs from 3 Mb.
    MIN_CNV_SIZE: (None, 3.0),
}

#: The bounds the rule cannot do without: an error bound for each. A warning bound is optional
#: (the call z-score's is the grey zone; the fetal fraction's a warning on the result).
_REQUIRED_ERROR_BOUNDS = tuple(PROVISIONAL_BOUNDS)

#: The findings of one direction (gain or loss) on a chromosome that span together at least this
#: share of its segmented length are read as an aneuploidy of that chromosome (whole, or of most of
#: it), not as segmental CNVs: one segment or several, as ccnv cut it (``_aneuploid_signals``).
#: Fixed in code, frozen with every result.
WHOLE_CHROMOSOME_SHARE = 0.5

#: ccnv's fragmentomic origins that make a finding inconclusive: maternal (the mother's own
#: event, inherited by the fetus or not) or mosaic (the mother's, or confined to the placenta).
MATERNAL_OR_MOSAIC_ORIGINS = frozenset(
    {"MATERNAL_UNINHERITED", "MATERNAL_AND_FETAL_INHERITED", "MATERNAL_MOSAIC", "PLACENTAL_MOSAIC"}
)

#: ccnv's sex-chromosome calls of a euploid fetus, and its call of signals that disagree.
_EUPLOID_SCA_CALLS = frozenset({"XX", "XY"})
_MIXED_SCA_CALL = "DISCORDANT_MIXED"

#: The size classes ccnv's limits of detection are given for (``lod_ff_3mb``, ``lod_ff_35mb``).
_FOCAL_MB = 3.0
_ARM_MB = 35.0

_SEVERITY: dict[str, int] = {"low_risk": 1, "inconclusive": 2, "high_risk": 3}

AUTOSOMES = tuple(str(number) for number in range(1, 23))
SEX_CHROMOSOMES_TARGET = "sex_chromosomes"
SEGMENTAL_TARGET = "segmental"
#: The targets the intended purpose claims (TF-01 §3.7, the owner's decision of 2026-11-20): the
#: trisomies 21, 18 and 13 and the sex-chromosome aneuploidies. A change is a change of the
#: intended purpose, under change control.
CLAIMED_TARGETS = frozenset({"21", "18", "13", SEX_CHROMOSOMES_TARGET})


@dataclass(frozen=True, slots=True)
class CniptBound:
    """One bound of the rule as the result applies it: QA's (``provisional`` False) or the
    provisional value QA has not replaced."""

    key: str
    warn_value: float | None
    error_value: float | None
    provisional: bool


@dataclass(frozen=True, slots=True)
class CcnvSegment:
    """One of ccnv's segments of the plasma, as its ``segments`` track stores it."""

    chrom: str
    start: int
    end: int
    ratio: float
    details: Mapping[str, Any]


def resolve_cnipt_bounds(configured: Mapping[str, Mapping[str, float | None]]) -> dict[str, CniptBound]:
    """Each bound of the rule: the one QA set in the conventional NIPT's profile
    (``configured``: metric key → ``{"warn_value", "error_value"}``), else the provisional
    value. A bound QA set without an error bound stays without one: the rule then gives no
    result, rather than a provisional value standing in for one QA chose to leave out."""
    bounds: dict[str, CniptBound] = {}
    for key, (warn, error) in PROVISIONAL_BOUNDS.items():
        set_by_qa = configured.get(key)
        if set_by_qa is not None:
            bounds[key] = CniptBound(
                key=key,
                warn_value=set_by_qa.get("warn_value"),
                error_value=set_by_qa.get("error_value"),
                provisional=False,
            )
        else:
            bounds[key] = CniptBound(key=key, warn_value=warn, error_value=error, provisional=True)
    return bounds


def ccnv_segment_rows(rows: Iterable[Mapping[str, Any]], details_of: Any) -> list[CcnvSegment]:
    """The plasma's segments from its stored ``segments`` rows (``chr``, ``start``, ``end``,
    ``value``, ``metadata_json``), each with ccnv's values (``details_of``: a row's
    ``metadata_json`` → its details, ``bed_service._segment_details``)."""
    segments: list[CcnvSegment] = []
    for row in rows:
        value = row.get("value")
        if value is None:
            continue
        segments.append(
            CcnvSegment(
                chrom=normalize_chromosome(str(row["chr"])),
                start=int(row["start"]),
                end=int(row["end"]),
                ratio=float(value),
                details=details_of(row.get("metadata_json")) or {},
            )
        )
    return sorted(segments, key=lambda segment: (_chrom_order(segment.chrom), segment.start))


def _chrom_order(chrom: str) -> tuple[int, str]:
    return (int(chrom), "") if chrom.isdigit() else (100, chrom)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(float(value)) else None


def _percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def _worst(verdicts: Iterable[str]) -> str | None:
    ranked = [verdict for verdict in verdicts if verdict in _SEVERITY]
    return max(ranked, key=lambda verdict: _SEVERITY[verdict]) if ranked else None


@dataclass(frozen=True, slots=True)
class _Rule:
    """The rule's bounds, each error bound set (``evaluate_cnipt`` gives no result otherwise)."""

    z_error: float
    z_warn: float | None
    ratio_min: float
    ratio_max: float
    sca_min_confidence: float


def _finding(
    segment: CcnvSegment,
    *,
    kind: str,
    fetal_fraction: float,
    rule: _Rule,
    assessed_from_mb: float | None,
) -> dict[str, Any] | None:
    """A segment as a finding with its own result and reasons, or None for a segment that is no
    finding: below the grey zone and not called by ccnv."""
    details = segment.details
    zscore = _number(details.get("zscore"))
    implied_ff = _number(details.get("implied_ff"))
    ccnv_call = details.get("call") if details.get("call") in ("gain", "loss") else None
    size_mb = (segment.end - segment.start) / 1_000_000
    z_error, z_warn = rule.z_error, rule.z_warn
    magnitude = abs(zscore) if zscore is not None else None
    in_grey = magnitude is not None and z_warn is not None and magnitude >= z_warn
    if ccnv_call is None and (magnitude is None or (magnitude < z_error and not in_grey)):
        return None
    ratio_to_ff = implied_ff / fetal_fraction if implied_ff is not None and fetal_fraction > 0 else None
    reasons: list[str] = []
    verdict: Verdict
    if kind == "segmental" and assessed_from_mb is None:
        verdict = "not_assessed"
        reasons.append("the fetal fraction is below ccnv's limit of detection of segmental CNVs")
    elif kind == "segmental" and assessed_from_mb is not None and size_mb < assessed_from_mb:
        verdict = "not_assessed"
        reasons.append(f"smaller than the {assessed_from_mb:g} Mb segmental CNVs are assessed from")
    elif magnitude is None:
        verdict = "inconclusive"
        reasons.append("ccnv gives the segment no z-score")
    elif magnitude >= z_error:
        if details.get("discordant") is True:
            reason = details.get("discordance_reason")
            reasons.append(f"ccnv flags it discordant with the fetal fraction ({reason})" if reason else "ccnv flags it discordant with the fetal fraction")
        if implied_ff is None or ratio_to_ff is None:
            reasons.append("ccnv gives no implied fetal fraction")
        elif ratio_to_ff < rule.ratio_min:
            reasons.append(
                f"its implied fetal fraction ({_percent(implied_ff)}) is below {rule.ratio_min:g} × the fetal fraction (sub-fetal)"
            )
        elif ratio_to_ff > rule.ratio_max:
            reasons.append(
                f"its implied fetal fraction ({_percent(implied_ff)}) is above {rule.ratio_max:g} × the fetal fraction (super-fetal)"
            )
        origin = details.get("origin_call")
        if origin in MATERNAL_OR_MOSAIC_ORIGINS:
            reasons.append(f"its origin is {origin}")
        if reasons:
            verdict = "inconclusive"
        else:
            verdict = "high_risk"
            reasons.append(
                f"|z| {magnitude:.1f} ≥ {z_error:g}, concordant with the fetal fraction"
                + (f", origin {origin}" if origin else "")
            )
    elif in_grey:
        verdict = "inconclusive"
        reasons.append(f"|z| {magnitude:.1f} is in the grey zone ({z_warn:g} to {z_error:g})")
    else:
        verdict = "low_risk"
        reasons.append(f"ccnv called it, but |z| {magnitude:.1f} is below {z_warn if z_warn is not None else z_error:g}")
    return {
        "chrom": segment.chrom,
        "start": segment.start,
        "end": segment.end,
        "size_mb": round(size_mb, 3),
        "direction": "gain" if segment.ratio >= 0 else "loss",
        "ratio": segment.ratio,
        "zscore": zscore,
        "bin_count": details.get("bin_count") if isinstance(details.get("bin_count"), int) else None,
        "ccnv_call": ccnv_call,
        "expected_ratio": _number(details.get("expected_ratio")),
        "implied_ff": implied_ff,
        "implied_ff_ratio": round(ratio_to_ff, 4) if ratio_to_ff is not None else None,
        "discordant": details.get("discordant") is True,
        "discordance_reason": details.get("discordance_reason") or None,
        "origin_call": details.get("origin_call") or None,
        "kind": kind,
        "verdict": verdict,
        "reasons": reasons,
    }


def _sca_target(qc_payload: Mapping[str, Any], minimum: float) -> tuple[str, list[str]]:
    sca = qc_payload["prenatal"]["sca"]
    call = str(sca.get("call"))
    confidence = _number(sca.get("confidence"))
    if call == _MIXED_SCA_CALL:
        return "inconclusive", ["ccnv's sex-chromosome signals disagree (DISCORDANT_MIXED)"]
    if confidence is None or confidence < minimum:
        shown = _percent(confidence) if confidence is not None else "none"
        return "inconclusive", [f"ccnv calls {call} at a confidence of {shown}, below {_percent(minimum)}"]
    if sca.get("is_aneuploid") is True or call not in _EUPLOID_SCA_CALLS:
        return "high_risk", [f"ccnv calls {call} (confidence {_percent(confidence)})"]
    return "low_risk", [f"ccnv calls {call} (confidence {_percent(confidence)})"]


def _direction(segment: CcnvSegment) -> str:
    return "gain" if segment.ratio >= 0 else "loss"


def _is_signal(segment: CcnvSegment, rule: _Rule) -> bool:
    """Whether a segment is a finding (``_finding`` returns one): called by ccnv, or with a
    |z-score| from the grey zone up."""
    details = segment.details
    if details.get("call") in ("gain", "loss"):
        return True
    zscore = _number(details.get("zscore"))
    if zscore is None:
        return False
    return abs(zscore) >= (rule.z_warn if rule.z_warn is not None else rule.z_error)


def _aneuploid_signals(
    segments: Sequence[CcnvSegment], segmented: Mapping[str, int], rule: _Rule
) -> set[tuple[str, str]]:
    """The chromosomes and directions whose findings are an aneuploidy (``WHOLE_CHROMOSOME_SHARE``):
    the segments of one direction that are findings span together at least that share of the
    chromosome's segmented length. A trisomy ccnv cuts into several segments is one aneuploidy of
    its chromosome, never a few segmental CNVs that leave the chromosome's own target low risk."""
    spans: dict[tuple[str, str], int] = {}
    for segment in segments:
        if _is_signal(segment, rule):
            key = (segment.chrom, _direction(segment))
            spans[key] = spans.get(key, 0) + (segment.end - segment.start)
    return {
        key
        for key, span in spans.items()
        if segmented.get(key[0]) and span / segmented[key[0]] >= WHOLE_CHROMOSOME_SHARE
    }


def _rule(bounds: Mapping[str, CniptBound]) -> _Rule | None:
    """The rule's bounds, or None when an error bound it needs is not set."""
    z, low, high, sca = (
        bounds[key].error_value for key in (CALL_ZSCORE, IMPLIED_FF_RATIO_MIN, IMPLIED_FF_RATIO_MAX, SCA_CONFIDENCE)
    )
    if z is None or low is None or high is None or sca is None:
        return None
    return _Rule(z_error=z, z_warn=bounds[CALL_ZSCORE].warn_value, ratio_min=low, ratio_max=high, sca_min_confidence=sca)


def evaluate_cnipt(
    qc: CcnvQc,
    segments: Sequence[CcnvSegment],
    bounds: Mapping[str, CniptBound],
) -> dict[str, Any]:
    """The conventional NIPT's result for one maternal plasma: its ccnv QC (``qc``), its segments
    and the rule's bounds (``resolve_cnipt_bounds``). JSON-safe and deterministic: the same
    output for the same input, so a signed record can freeze it."""
    payload = qc.payload
    prenatal = payload["prenatal"]
    fetal_fraction = qc.effective_ff
    no_result: list[str] = []
    for key in _REQUIRED_ERROR_BOUNDS:
        if bounds[key].error_value is None:
            no_result.append(f"the bound {key} has no error value: it is set without one")
    ff_bound = bounds[FETAL_FRACTION]
    warnings: list[str] = []
    if ff_bound.error_value is not None and fetal_fraction < ff_bound.error_value:
        no_result.append(
            f"the fetal fraction ({_percent(fetal_fraction)}) is below {_percent(ff_bound.error_value)}"
        )
    elif ff_bound.warn_value is not None and fetal_fraction < ff_bound.warn_value:
        warnings.append(
            f"the fetal fraction ({_percent(fetal_fraction)}) is below the warning bound of {_percent(ff_bound.warn_value)}"
        )
    if prenatal.get("low_ff_warning") is True:
        warnings.append("ccnv warns of a low fetal fraction")
    for source, entries in (("ccnv", prenatal.get("warnings")), ("ccnv fragmentomics", (payload.get("fragmentomics") or {}).get("warnings"))):
        if isinstance(entries, list):
            warnings.extend(f"{source}: {entry}" for entry in entries if isinstance(entry, str))
    # ccnv reads twins (owner, 2026-11-19: in scope, one fetal fraction and one result per plasma).
    gestation = prenatal.get("gestation") if isinstance(prenatal.get("gestation"), str) else None
    if gestation is not None and gestation.lower() != "singleton":
        warnings.append(
            f"ccnv reads a {gestation} pregnancy: an aneuploidy of one fetus lies at about half the fetal fraction"
        )

    lod_arm = _number(prenatal.get("lod_ff_35mb"))
    lod_focal = _number(prenatal.get("lod_ff_3mb"))
    min_cnv = bounds[MIN_CNV_SIZE].error_value
    assessed_from_mb: float | None
    if lod_arm is not None and fetal_fraction < lod_arm:
        assessed_from_mb = None
        resolution = (
            f"Segmental CNVs are not assessed: the fetal fraction is below ccnv's limit of detection of "
            f"arm-level events ({_percent(lod_arm)})."
        )
    elif lod_focal is not None and fetal_fraction < lod_focal:
        assessed_from_mb = max(_ARM_MB, min_cnv or 0.0)
        resolution = (
            f"Segmental CNVs are assessed from {assessed_from_mb:g} Mb: the fetal fraction is below ccnv's "
            f"limit of detection of {_FOCAL_MB:g} Mb events ({_percent(lod_focal)})."
        )
    else:
        assessed_from_mb = max(_FOCAL_MB if lod_focal is not None else 0.0, min_cnv or 0.0)
        resolution = f"Segmental CNVs are assessed from {assessed_from_mb:g} Mb."

    segmented: dict[str, int] = {}
    for segment in segments:
        segmented[segment.chrom] = segmented.get(segment.chrom, 0) + (segment.end - segment.start)
    findings: list[dict[str, Any]] = []
    rule = _rule(bounds) if not no_result else None
    if rule is not None:
        aneuploid = _aneuploid_signals(segments, segmented, rule)
        for segment in segments:
            kind = "chromosome" if (segment.chrom, _direction(segment)) in aneuploid else "segmental"
            finding = _finding(
                segment,
                kind=kind,
                fetal_fraction=fetal_fraction,
                rule=rule,
                assessed_from_mb=assessed_from_mb,
            )
            if finding is not None:
                findings.append(finding)

    targets: list[dict[str, Any]] = []
    if rule is None:
        for target in (*AUTOSOMES, SEX_CHROMOSOMES_TARGET, SEGMENTAL_TARGET):
            targets.append({"target": target, "verdict": "no_result", "reasons": list(no_result)})
    else:
        for chrom in AUTOSOMES:
            mine = [finding for finding in findings if finding["chrom"] == chrom and finding["kind"] == "chromosome"]
            if chrom not in segmented:
                targets.append({"target": chrom, "verdict": "no_result", "reasons": [f"ccnv gives no segment of chromosome {chrom}"]})
                continue
            worst = _worst(finding["verdict"] for finding in mine)
            if worst is None:
                targets.append({"target": chrom, "verdict": "low_risk", "reasons": []})
            else:
                targets.append(
                    {
                        "target": chrom,
                        "verdict": worst,
                        "reasons": [reason for finding in mine if finding["verdict"] == worst for reason in finding["reasons"]],
                    }
                )
        sca_verdict, sca_reasons = _sca_target(payload, rule.sca_min_confidence)
        sex_findings = [finding for finding in findings if finding["chrom"] in ("X", "Y") and finding["kind"] == "chromosome"]
        worst_sex = _worst([sca_verdict, *(finding["verdict"] for finding in sex_findings)]) or sca_verdict
        targets.append(
            {
                "target": SEX_CHROMOSOMES_TARGET,
                "verdict": worst_sex,
                "reasons": sca_reasons
                + [
                    f"chromosome {finding['chrom']}: {reason}"
                    for finding in sex_findings
                    if finding["verdict"] == worst_sex and worst_sex != sca_verdict
                    for reason in finding["reasons"]
                ],
            }
        )
        segmental = [finding for finding in findings if finding["kind"] == "segmental"]
        if assessed_from_mb is None:
            targets.append({"target": SEGMENTAL_TARGET, "verdict": "not_assessed", "reasons": [resolution]})
        else:
            worst_segmental = _worst(finding["verdict"] for finding in segmental) or "low_risk"
            # The reasons of the findings that decide; for a low risk, the sizes assessed and each
            # finding, those left unassessed among them, so a low risk never hides a CNV it did not
            # look at.
            shown = {worst_segmental} if worst_segmental != "low_risk" else {"low_risk", "not_assessed"}
            reasons = [
                f"chr{finding['chrom']}:{finding['start']}-{finding['end']}: {reason}"
                for finding in segmental
                if finding["verdict"] in shown
                for reason in finding["reasons"]
            ]
            targets.append(
                {
                    "target": SEGMENTAL_TARGET,
                    "verdict": worst_segmental,
                    "reasons": reasons if worst_segmental != "low_risk" else [resolution, *reasons],
                }
            )
        if lod_arm is not None and fetal_fraction < lod_arm:
            # Below ccnv's limit of detection of arm-level events, a claimed target is never read as
            # low risk: the plasma cannot show its aneuploidy (the owner's decision of 2026-11-21). A
            # finding it did show keeps its verdict.
            below_lod = (
                f"the fetal fraction ({_percent(fetal_fraction)}) is below ccnv's limit of detection of "
                f"arm-level events ({_percent(lod_arm)}), so it cannot be read as low risk"
            )
            for entry in targets:
                if entry["target"] in CLAIMED_TARGETS and entry["verdict"] == "low_risk":
                    entry["verdict"] = "inconclusive"
                    entry["reasons"] = [*entry["reasons"], below_lod]

    return {
        "status": "no_result" if no_result else "result",
        "no_result_reasons": no_result,
        "fetal_fraction": {
            "effective": qc.effective_ff,
            "active": qc.active_ff,
            "active_se": _number(prenatal.get("active_ff_se")),
            "active_source": prenatal.get("active_ff_source") if isinstance(prenatal.get("active_ff_source"), str) else None,
            "ffy": _number(prenatal.get("ffy")),
            "ffx": _number(prenatal.get("ffx")),
            "ffa": _number(prenatal.get("ffa")),
            "ff_size": _number(prenatal.get("ff_size")),
        },
        "fetal_sex": qc.fetal_sex,
        "sca": {
            "call": qc.sca_call,
            "is_aneuploid": prenatal["sca"].get("is_aneuploid") is True,
            "confidence": _number(prenatal["sca"].get("confidence")),
        },
        "gestation": gestation,
        "limits_of_detection": {
            "lod_ff_35mb": lod_arm,
            "lod_ff_3mb": lod_focal,
            "segmental_assessed_from_mb": assessed_from_mb,
            "statement": resolution,
        },
        "warnings": warnings,
        "targets": [{**target, "claimed": target["target"] in CLAIMED_TARGETS} for target in targets],
        "findings": findings,
        "bounds": [
            {
                "key": bound.key,
                "warn_value": bound.warn_value,
                "error_value": bound.error_value,
                "provisional": bound.provisional,
            }
            for bound in bounds.values()
        ],
        "rule": {
            "threshold_profile": CNIPT_THRESHOLD_PROFILE,
            "whole_chromosome_share": WHOLE_CHROMOSOME_SHARE,
            "maternal_or_mosaic_origins": sorted(MATERNAL_OR_MOSAIC_ORIGINS),
        },
    }
