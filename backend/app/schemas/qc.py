"""Sequencing-QC metrics and the admin-managed QC thresholds."""

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from .common import (
    ApiId,
)


class QcMetricCatalogueOut(BaseModel):
    """A sequencing-QC metric a threshold can be set on.

    The catalogue is fixed in code (it follows what the QC parsers emit), so the admin
    UI reads it rather than letting an operator invent metric keys or invert a
    comparison direction.
    """

    key: str
    label: str
    unit: str
    direction: Literal["lower_is_worse", "higher_is_worse"]
    help_text: str


class QcThresholdOut(BaseModel):
    metric_key: str
    label: str
    unit: str
    direction: Literal["lower_is_worse", "higher_is_worse"]
    #: False when a stored row's metric is no longer in the catalogue, so an admin can
    #: still see and clear it instead of it silently gating nothing.
    known_metric: bool = True
    warn_value: Optional[float] = None
    error_value: Optional[float] = None


class QcThresholdProfileOut(BaseModel):
    """A named set of sequencing-QC cut-offs (one per assay type)."""

    id: ApiId
    key: str
    label: str
    description: Optional[str] = None
    is_default: bool = False
    sort_order: int = 500
    thresholds: List[QcThresholdOut] = Field(default_factory=list)


class QcThresholdChangeOut(BaseModel):
    """One entry of the append-only cut-off history, with both sides of the edit."""

    profile_key: str
    metric_key: str
    previous_warn_value: Optional[float] = None
    previous_error_value: Optional[float] = None
    warn_value: Optional[float] = None
    error_value: Optional[float] = None
    changed_by_email: Optional[str] = None
    changed_at: datetime
    # Null only for edits made before the field was required.
    reason: Optional[str] = None


class QcThresholdCatalogueOut(BaseModel):
    metrics: List[QcMetricCatalogueOut] = Field(default_factory=list)
    profiles: List[QcThresholdProfileOut] = Field(default_factory=list)
    changes: List[QcThresholdChangeOut] = Field(default_factory=list)


class QcThresholdProfileCreate(BaseModel):
    """A new, empty QC threshold profile.

    No cut-offs are copied from anywhere: a profile exists because an assay is judged
    differently, so an inherited number would carry an authority nobody granted it.
    """

    label: str
    # Derived from the label when omitted. Immutable afterwards — a family names its
    # profile by key in `qc_profile` metadata.
    key: Optional[str] = None
    description: Optional[str] = None


class QcThresholdUpdate(BaseModel):
    """Set or clear one metric's cut-offs in a profile.

    Both bounds null clears the threshold — a metric with no cut-off is not configured.
    """

    # Required: the values either side are recorded automatically, but they cannot say
    # whether a limit moved because a validation study supported it or because a run was
    # inconvenient. Points at the test's clinical validation report, which is where the
    # cut-off is actually agreed.
    reason: str = Field(min_length=1, max_length=2000)

    metric_key: str
    warn_value: Optional[float] = None
    error_value: Optional[float] = None


class SampleSequencingQcMetricOut(BaseModel):
    """One sequencing-QC metric measured against its configured cut-offs."""

    metric_key: str
    label: str
    unit: str
    direction: Literal["lower_is_worse", "higher_is_worse"]
    value: float
    warn_value: Optional[float] = None
    error_value: Optional[float] = None
    # `skip` means not checked — no cut-off is configured for this metric. It is
    # deliberately distinct from `pass`, which means measured and within bounds.
    # Vocabulary shared with sample-integrity QC (`QcStatus`) rather than a new one.
    verdict: Literal["pass", "warn", "fail", "skip"]


class SampleSequencingQcEvaluationOut(BaseModel):
    """A sample's sequencing QC and its overall verdict (the worst metric)."""

    verdict: Literal["pass", "warn", "fail", "skip"]
    metrics: List[SampleSequencingQcMetricOut] = Field(default_factory=list)
    breached: List[str] = Field(default_factory=list)
    profile_key: Optional[str] = None
    profile_label: Optional[str] = None


class FamilyQcReportLinkOut(BaseModel):
    """Short-lived URL for a sample's rendered sequencing-QC report.

    The report is untrusted pipeline HTML, so it is opened in a new tab against an
    isolated response rather than embedded in the application.
    """

    url: str
    expires_at: datetime
    filename: str
