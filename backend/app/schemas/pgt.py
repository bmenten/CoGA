"""The PGT analysis's embryo results at the region of interest (REQ-PGT-005).

``GET /families/{family_id}/pgt-analysis`` serves them to the family page, and a sign-out of a
PGT family freezes the same object into the signed record (snapshot key ``pgt_analysis``,
``services/signed_record_sections.py``). The record holds it as JSON: a field added here is a
change to the signed-record format, which freezes at the release candidate.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

PgtRiskState = Literal["affected_or_at_risk", "carrier", "unaffected_non_carrier", "uninformative"]


class PgtRegionOfInterestOut(BaseModel):
    """The family's region of interest, as set on the family page: the embryos are called there."""

    query: str
    label: str
    source: Optional[str] = None
    chrom: str
    start: int
    end: int


class PgtWindowOut(BaseModel):
    """The span whose haplotype blocks were read: the ROI and 1 Mb on each side."""

    chrom: str
    start: int
    end: int


class PgtInheritanceModelOut(BaseModel):
    # As recorded for the family (``metadata.pgt.inheritance_model``); None when none is.
    recorded: Optional[str] = None
    # The model the calls used: the recorded one, or assumed (AR when a member is marked a
    # carrier, AD otherwise).
    applied: str
    mode: Literal["dominant", "recessive", "x_linked_dominant", "x_linked_recessive", "unknown"]


class PgtMemberOut(BaseModel):
    """A member as the disease-haplotype inference read it: its role, sex and statuses."""

    sample: str
    role: Optional[str] = None
    sex: Optional[str] = None
    affected: bool = False
    clinical_status: Optional[str] = None
    carrier_status: Optional[str] = None
    carrier_type: Optional[str] = None


class PgtDiseaseHaplotypeOut(BaseModel):
    """A disease haplotype at the ROI: one parental homolog (the track's P1/P2, M1/M2 for
    labels 0/1) and the kind of risk it carries."""

    origin: Literal["paternal", "maternal"]
    homolog: str
    kind: Literal["dominant", "recessive-paternal", "recessive-maternal", "x-linked"]


class PgtInheritedHaplotypeOut(BaseModel):
    """A parental homolog an embryo carries at the ROI, with the extent of its block."""

    origin: Literal["paternal", "maternal"]
    homolog: str
    start: int
    end: int


class PgtPhaseCorrectionOut(BaseModel):
    """A parent's phase switch the haplotype blocks undid, inside or close to the ROI."""

    parent: str
    side: Literal["father", "mother"]
    chr: str
    position: int
    end: int
    children_switching: int = 0
    children: int = 0


class PgtSexDependentCallOut(BaseModel):
    """X-linked recessive, the embryo's sex not recorded: the call a son and a daughter would get."""

    if_male: PgtRiskState
    if_female: PgtRiskState


class PgtEmbryoQcOut(BaseModel):
    """The embryo's phasing QC at the ROI, as the ROI marker review counts it (REQ-PGT-004)."""

    informative_sites: int = 0
    mendel_errors: int = 0
    mendel_rate: float = 0.0
    # The ROI's sites where the homolog inherited from each parent is read.
    paternal_markers: int = 0
    maternal_markers: int = 0


class PgtEmbryoOut(BaseModel):
    sample: str
    sex: Optional[str] = None
    # None: not assessed (the family has no ROI, or no haplotypes were read).
    call: Optional[PgtRiskState] = None
    # Uninformative because the embryo's own haplotype does not cover the ROI on a parental
    # side the call needs (missing data), not because no disease haplotype was resolved.
    roi_not_covered: bool = False
    sex_dependent: Optional[PgtSexDependentCallOut] = None
    inherited: List[PgtInheritedHaplotypeOut] = Field(default_factory=list)
    # Where the embryo's blocks change inside the ROI or within the recombination flank.
    recombinations: List[int] = Field(default_factory=list)
    # None when not counted: no ROI, the ROI held more sites than the marker review reads
    # (``inputs.markers_truncated``), or the embryo is not recorded as the couple's child.
    qc: Optional[PgtEmbryoQcOut] = None


class PgtHaplotypeSourceOut(BaseModel):
    """A member's stored haplotype blocks: the file they were built from and when."""

    sample: str
    source: str
    filename: str
    blocks: int
    loaded_at: Optional[str] = None


class PgtInputsOut(BaseModel):
    haplotype_sources: List[PgtHaplotypeSourceOut] = Field(default_factory=list)
    # The blocks read over the window, every member's, and a SHA-256 digest of them as read
    # (lineage tags included): what the calls were made from. None when none were read.
    blocks_read: int = 0
    blocks_digest: Optional[str] = None
    # The ROI held more sites than the marker review reads, so no embryo QC was counted.
    markers_truncated: bool = False


class PgtRulesOut(BaseModel):
    """The distances the calls and their warnings were read with (``pgt_embryo_calls``)."""

    window_flank: int
    recombination_flank: int
    phase_switch_uncertainty: int


class PgtAnalysisOut(BaseModel):
    """The PGT analysis at the family's ROI: the disease haplotype and each embryo's call."""

    region_of_interest: Optional[PgtRegionOfInterestOut] = None
    window: Optional[PgtWindowOut] = None
    inheritance_model: PgtInheritanceModelOut
    members: List[PgtMemberOut] = Field(default_factory=list)
    # Whether a disease haplotype was resolved at the ROI; without one every call is
    # uninformative.
    disease_haplotype_resolved: bool = False
    disease_haplotypes: List[PgtDiseaseHaplotypeOut] = Field(default_factory=list)
    phase_corrections: List[PgtPhaseCorrectionOut] = Field(default_factory=list)
    embryos: List[PgtEmbryoOut] = Field(default_factory=list)
    inputs: PgtInputsOut = Field(default_factory=PgtInputsOut)
    rules: PgtRulesOut
