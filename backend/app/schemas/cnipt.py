"""The conventional NIPT's result for a family's maternal plasma (``cnipt_result``), as
``GET /families/{family_id}/cnipt`` serves it."""

from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel

CniptVerdict = Literal["high_risk", "inconclusive", "low_risk", "no_result", "not_assessed"]


class CniptFetalFractionOut(BaseModel):
    """ccnv's fetal fractions of the plasma, from 0 to 1: the effective one the result and the
    expected lines use, the fused active one with its standard error and source, and each
    estimator's own."""

    effective: float
    active: float
    active_se: Optional[float] = None
    active_source: Optional[str] = None
    ffy: Optional[float] = None
    ffx: Optional[float] = None
    ffa: Optional[float] = None
    ff_size: Optional[float] = None


class CniptScaOut(BaseModel):
    """ccnv's sex-chromosome call: its karyotype (XX, XY, XO, XXX, XXY, XYY, DISCORDANT_MIXED),
    whether it is an aneuploidy, and its confidence."""

    call: str
    is_aneuploid: bool
    confidence: Optional[float] = None


class CniptLimitsOut(BaseModel):
    """ccnv's limits of detection (as fetal fractions) of arm-level and 3 Mb events, the size
    segmental CNVs are assessed from at this fetal fraction (none when they are not), and a
    sentence that says so."""

    lod_ff_35mb: Optional[float] = None
    lod_ff_3mb: Optional[float] = None
    segmental_assessed_from_mb: Optional[float] = None
    statement: str


class CniptTargetOut(BaseModel):
    """One target's result: an autosome (``1``…``22``), ``sex_chromosomes`` or ``segmental``,
    with the reasons for it, and whether the intended purpose claims it (``claimed``: the
    trisomies 21, 18 and 13 and the sex chromosomes; the rest is shown outside the claim)."""

    target: str
    verdict: CniptVerdict
    reasons: List[str]
    claimed: bool


class CniptFindingOut(BaseModel):
    """A segment the rule looked at: one ccnv called, or one whose |z-score| reaches the grey
    zone. ``kind`` says whether it is read as an aneuploidy of its chromosome (``chromosome``)
    or as a segmental CNV; ``implied_ff_ratio`` is its implied fetal fraction over the
    plasma's."""

    chrom: str
    start: int
    end: int
    size_mb: float
    direction: Literal["gain", "loss"]
    ratio: float
    zscore: Optional[float] = None
    bin_count: Optional[int] = None
    ccnv_call: Optional[Literal["gain", "loss"]] = None
    expected_ratio: Optional[float] = None
    implied_ff: Optional[float] = None
    implied_ff_ratio: Optional[float] = None
    discordant: bool
    discordance_reason: Optional[str] = None
    origin_call: Optional[str] = None
    kind: Literal["chromosome", "segmental"]
    verdict: CniptVerdict
    reasons: List[str]


class CniptBoundOut(BaseModel):
    """One bound of the rule as the result applied it: QA's, from the profile
    ``nipt_conventional``, or the provisional value QA has not replaced (``provisional``)."""

    key: str
    label: str
    unit: str
    warn_value: Optional[float] = None
    error_value: Optional[float] = None
    provisional: bool


class CniptRuleOut(BaseModel):
    """What of the rule is fixed in code: the profile its bounds are read from, the share of a
    chromosome a segment must span to be read as its aneuploidy, and ccnv's origins that make a
    finding inconclusive."""

    threshold_profile: str
    whole_chromosome_share: float
    maternal_or_mosaic_origins: List[str]


class CniptSampleResultOut(BaseModel):
    """The result for one maternal plasma (``status`` ``no_result`` with its reasons when there
    is none), with what it rests on."""

    sample_id: str
    qc_file: str
    status: Literal["result", "no_result"]
    no_result_reasons: List[str]
    fetal_fraction: CniptFetalFractionOut
    fetal_sex: str
    sca: CniptScaOut
    gestation: Optional[str] = None
    limits_of_detection: CniptLimitsOut
    read_metrics: Dict[str, float]
    warnings: List[str]
    targets: List[CniptTargetOut]
    findings: List[CniptFindingOut]
    bounds: List[CniptBoundOut]
    rule: CniptRuleOut


class FamilyCniptOut(BaseModel):
    """A conventional NIPT family's result: one per maternal plasma with ccnv output."""

    family_id: str
    samples: List[CniptSampleResultOut]
