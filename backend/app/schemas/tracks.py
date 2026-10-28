"""Haplotypes, phased markers, BED and signal tracks, repeat expansions and Paraphase."""

from datetime import datetime
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field

from .families import (
    FamilyMemberOut,
)


class AlignmentManifestEntryOut(BaseModel):
    sample_id: str
    format: Literal["bam", "cram"]
    url: str
    index_url: str


class HaplotypeSegment(BaseModel):
    chr: Optional[str] = None
    start: int
    end: int
    hap1: str
    hap2: str
    ps: Optional[int] = None
    # Pedigree-aware colour class for each lane: "paternal"/"maternal" (coloured
    # by the hap value's shade), "untransmitted"/"unknown" (grey). Absent for
    # data that predates lineage tagging — the frontend then falls back to the
    # role-based colourer.
    hap1_lineage: Optional[str] = None
    hap2_lineage: Optional[str] = None
    # A male carries one copy of this block: chrX or chrY outside the pseudo-autosomal
    # regions of the family's assembly. False on an autosome, on a block that reaches into a
    # PAR, and on an assembly whose PARs are not known: a male then has two copies.
    hemizygous_in_males: bool = False


class HaplotypeSample(BaseModel):
    sample: str
    segments: List[HaplotypeSegment] = Field(default_factory=list)


class HaplotypeResponse(BaseModel):
    chr: str
    start: Optional[int] = None
    end: Optional[int] = None
    # The stored blocks were built for another pedigree or other sexes than the family's
    # (CLIN-66): the tracks then read no risk state from them.
    blocks_outdated: bool = False
    samples: List[HaplotypeSample] = Field(default_factory=list)


class PhasedMarker(BaseModel):
    """One informative imputed marker (raw call), with the value drawn on each
    haplotype lane (0/1, null = uninformative):

    - For a child: ``hap1`` = the paternal homolog inherited, ``hap2`` = the
      maternal homolog inherited, oriented to match the stored haplotype blocks.
    - For a parent: ``hap1`` / ``hap2`` are the alleles on their own two phased
      homologs (the raw per-site version of their haplotype blocks).

    The father's and mother's genotype at a site (shown in the tooltip) are
    reconstructed client-side from the father/mother marker rows at that position,
    so they are not duplicated onto every marker."""

    pos: int
    hap1: Optional[int] = None
    hap2: Optional[int] = None


class PhasedSampleQc(BaseModel):
    """Per-child phasing QC summary for one region.

    Computed only for the index couple's own children (the members for whom
    parent-of-origin is defined). ``informative_sites`` counts sites where both
    parents and the child carry a valid phased genotype (the joint-informative
    denominator); for a son on chrX outside the PARs, whose one X is his mother's,
    the sites where she and he have a genotype. ``mendel_errors`` counts the subset
    of those sites whose child genotype is impossible given the parents' alleles — a
    true Mendelian inconsistency (likely sample swap / wrong pedigree), NOT mere
    parent-of-origin ambiguity. ``mendel_rate`` = mendel_errors / informative_sites
    (0 when none)."""

    informative_sites: int = 0
    mendel_errors: int = 0
    mendel_rate: float = 0.0


class PhasedMarkerSample(BaseModel):
    sample: str
    markers: List[PhasedMarker] = Field(default_factory=list)
    # True for the index parents, whose marker lanes are their own ref/alt alleles
    # (the reference phasing) rather than inherited-homolog indices. The client
    # colours these by their haplotype block (constant per lane) instead of by the
    # allele value, so they match the blocks.
    reference: bool = False
    # Per-child QC (informative-site count + Mendelian-error signal). Absent for
    # parents/relatives, for whom parent-of-origin (and thus a Mendel check) is
    # undefined.
    qc: Optional["PhasedSampleQc"] = None


class PhasedSite(BaseModel):
    """Raw phased genotypes at one imputed site, for the marker hover tooltip.

    ``gts`` is aligned to the response's ``samples`` order; each entry is that
    member's phased ``a|b`` genotype (allele indices as a string), decoded to
    nucleotides client-side via ``ref``/``alt``."""

    pos: int
    ref: str
    alt: str
    gts: List[str] = Field(default_factory=list)


class PhasedMarkerResponse(BaseModel):
    chr: str
    start: Optional[int] = None
    end: Optional[int] = None
    samples: List[PhasedMarkerSample] = Field(default_factory=list)
    sites: List[PhasedSite] = Field(default_factory=list)
    # The per-site fetch is capped (ORDER BY pos LIMIT) and deterministically drops
    # the highest-coordinate tail when the region holds more sites than the cap, so
    # a partial overlay would silently stop part-way across a full-length block. When
    # this is True the client must NOT render the markers/sites (which cover only
    # ``covered`` = [min_pos, max_pos] of the requested window) and should instead
    # prompt the user to zoom in. Blocks remain fully renderable.
    truncated: bool = False
    covered: Optional[List[int]] = None
    # The parts of the window on chrX outside the pseudo-autosomal regions of the family's
    # assembly, as [first, last] positions (inclusive): there a male has one X, and the
    # per-child QC reads a member recorded as male as one copy. The ROI marker review checks
    # its sites by the same rule. Empty off chrX, inside a PAR, and on an assembly whose PAR
    # bounds are not known, where every member is read as two copies.
    hemizygous_x_ranges: List[List[int]] = Field(default_factory=list)


class SignalTrackManifestEntryOut(BaseModel):
    """One caller signal file the genome browser can draw."""

    sample_id: str
    source: str
    kind: str
    name: str
    format: Literal["bigwig", "bedgraph"]
    url: str
    # Fixed axis bounds where the quantity has them (MAF is 0-0.5 by construction);
    # None means let the browser autoscale, which is right for read depth.
    min: Optional[float] = None
    max: Optional[float] = None


class TrackAvailabilityOut(BaseModel):
    coverage: bool = False
    segments: bool = False
    # Which callers actually have rows, so the views can draw one track per caller.
    # `coverage`/`segments` stay as the "any at all" flags the older callers read.
    coverage_sources: List[str] = Field(default_factory=list)
    segments_sources: List[str] = Field(default_factory=list)
    # What each caller's coverage values are, as its track's records name the transform
    # the import put them through (`sample_interval_track_sources.metadata.normalization`):
    # `log2_ratio_to_autosomal_median`, or `none` for HiFiCNV depths stored as read because
    # the sample had no usable autosomal median. A caller whose records name none is not
    # listed: its values are its file's.
    coverage_normalizations: Dict[str, List[str]] = Field(default_factory=dict)
    # What each caller's coverage track holds, by the rule the depth readers read it by
    # (`coverage_read_depth.coverage_track_content`, TRACE-9): `read_depth`, `log2_ratio` or
    # `unknown`. A coverage BED (`web`) holds what it declared when it was loaded (its
    # record's `content`), `unknown` when it declared nothing; another caller's track what
    # its transform made of its file. The views draw a track of read depth on a depth axis,
    # a log2 ratio on their log2-ratio axis, and an unknown one on neither, with a note.
    coverage_contents: Dict[str, Literal["read_depth", "log2_ratio", "unknown"]] = Field(
        default_factory=dict
    )
    # Which callers wrote the APCAD track. It holds parent-of-origin markers (0-1)
    # for a phased trio and folded minor allele fraction (0-0.5) from a depth
    # caller's bigWig; the two need different axes.
    apcad_sources: List[str] = Field(default_factory=list)
    apcad: bool = False
    apcad_pcf: bool = False
    variants: bool = False
    small_variants: bool = False
    haplotypes: bool = False
    repeat_expansions: bool = False


class FamilyTrackAvailabilityOut(BaseModel):
    samples: Dict[str, TrackAvailabilityOut] = Field(default_factory=dict)


class BedRecordOut(BaseModel):
    """One row of a coverage or segments track, as the BED reads serve it (``routers/bed.py``);
    a windowed coverage read serves each window's mean. ``value`` is null where the stored row
    holds none."""

    chr: str
    start: int
    end: int
    value: Optional[float]


class BedApcadRecordOut(BedRecordOut):
    """One APCAD site or PCF segment: the row with the parent it is inherited from
    (``paternal`` or ``maternal``; ``und`` where that is not known)."""

    origin: str


class BedTrackOut(BaseModel):
    """A BED read of a coverage or segments track."""

    bed_type: Literal["coverage", "segments"]
    items: List[BedRecordOut]


class BedApcadTrackOut(BaseModel):
    """A BED read of an APCAD or PCF track: each row with its origin."""

    bed_type: Literal["apcad", "apcad_pcf"]
    items: List[BedApcadRecordOut]


# What a BED read answers: the track type it read decides the shape of its rows.
BedReadOut = Annotated[Union[BedTrackOut, BedApcadTrackOut], Field(discriminator="bed_type")]


class BedUploadResult(BaseModel):
    """What a BED upload stored: its number of rows."""

    inserted: int


# A repeat allele's, call's or locus's status (repeat_expansion_pg.classify_repeat_count):
# `not_assessed` is a call at a locus without thresholds (CLIN-26) whose count is outside any
# benign range the catalogue states there (CLIN-36), `unknown` a no-call.
RepeatStatus = Literal[
    "normal", "review", "not_assessed", "intermediate", "premutation", "pathogenic", "unknown"
]
# Why a call or a locus reads ``review``: a count the catalogue cannot classify
# (``outside_ranges``), or a male's two different chrX alleles (``male_x_alleles``).
RepeatReviewReason = Literal["outside_ranges", "male_x_alleles"]


class RepeatExpansionMotifCountOut(BaseModel):
    motif: str
    count: int


class RepeatCountBoundaryOut(BaseModel):
    """The nearest count on one side of a call's count that reads another status."""

    repeat_count: int
    status: RepeatStatus


class RepeatCountUnitWarningOut(BaseModel):
    """A count within ``margin`` repeats of a classification boundary, at a locus whose TRGT
    counting units QA has not validated (repeat_count_units): a difference of that size
    between TRGT's count and the clinical repeat number could change the status. The status
    itself is unchanged."""

    repeat_count: int
    margin: int
    # The nearest count below and above (within the margin) that reads another status.
    boundaries: List[RepeatCountBoundaryOut] = Field(default_factory=list)


class RepeatExpansionAlleleOut(BaseModel):
    repeat_count: Optional[int] = None
    bp_length: Optional[int] = None
    confidence_interval: Optional[str] = None
    support_reads: Optional[int] = None
    purity: Optional[float] = None
    methylation: Optional[float] = None
    motif_counts: List[RepeatExpansionMotifCountOut] = Field(default_factory=list)
    motif_spans: Optional[str] = None
    interrupted: bool = False
    interruption_label: Optional[str] = None
    status: RepeatStatus = "unknown"
    # Set when the count is near a boundary at a locus whose counting units are not validated.
    count_unit_warning: Optional[RepeatCountUnitWarningOut] = None


class RepeatExpansionSampleCallOut(BaseModel):
    sample: str
    role: Optional[str] = None
    affected: Optional[bool] = None
    sex: Optional[str] = None
    genotype: str
    allele_count: int = 0
    alleles: List[RepeatExpansionAlleleOut] = Field(default_factory=list)
    status: RepeatStatus = "unknown"
    # Why a ``review`` call reads so (RepeatReviewReason); None for any other status.
    review_reason: Optional[RepeatReviewReason] = None
    # Why the call needs a second look beyond its allele sizes (a male with two
    # different chrX alleles), or why no catalogue locus classifies it (its TRID names
    # several STRchive loci and its region does not pick one, CLIN-31), or None.
    note: Optional[str] = None


class RepeatExpansionRowOut(BaseModel):
    locus_id: str
    gene: str
    display_name: str
    disease: str
    inheritance: Optional[str] = None
    chr: str
    start: int
    end: int
    motif: Optional[str] = None
    warning_min: Optional[int] = None
    pathogenic_min: Optional[int] = None
    # The catalog's full ranges. Needed because a couple of loci are pathogenic by
    # contraction, where "pathogenic at or above pathogenic_min" is not the rule and a
    # cutoff stated as a single lower bound would read backwards.
    benign_min: Optional[int] = None
    benign_max: Optional[int] = None
    pathogenic_max: Optional[int] = None
    # The first count of a premutation (FMR1 55), between the grey zone and the
    # pathogenic threshold; None for a locus without one.
    premutation_min: Optional[int] = None
    # Whether QA validated that TRGT's count at the catalogue row classifying the calls is
    # the clinical repeat number; False until it did. And what is known of TRGT's count at
    # the gene (FMR1, HTT, POLG), or None.
    count_units_validated: bool = False
    count_units_note: Optional[str] = None
    status: RepeatStatus = "unknown"
    # Why a ``review`` locus reads so: a count the catalogue cannot classify in any call,
    # else the male chrX note; None for any other status.
    review_reason: Optional[RepeatReviewReason] = None
    calls: Dict[str, RepeatExpansionSampleCallOut] = Field(default_factory=dict)


class FamilyRepeatExpansionTableOut(BaseModel):
    samples: List[FamilyMemberOut] = Field(default_factory=list)
    loci: List[RepeatExpansionRowOut] = Field(default_factory=list)
    # Number of loci with data. Always populated; with ``count_only`` the heavy
    # ``loci`` payload is skipped and only this presence count is returned.
    loci_count: int = 0


class ParaphaseMetricOut(BaseModel):
    key: str
    label: str
    value: Optional[float] = None


class ParaphaseHaplotypeGroupOut(BaseModel):
    key: str
    label: str
    count: int = 0
    haplotypes: List[str] = Field(default_factory=list)


class ParaphaseDisorderOut(BaseModel):
    name: str
    omim_url: Optional[str] = None


class ParaphaseClinicalOut(BaseModel):
    interpretation: Optional[str] = None
    normal: Optional[str] = None
    carrier: Optional[str] = None
    pathogenic: Optional[str] = None
    caveats: Optional[str] = None


class ParaphaseRegionInfoOut(BaseModel):
    region_id: str
    display_name: str
    genes: List[str] = Field(default_factory=list)
    summary: Optional[str] = None
    clinical: Optional[ParaphaseClinicalOut] = None
    clinical_priority: int = 999
    key_copy_number_fields: List[str] = Field(default_factory=list)
    key_read_fields: List[str] = Field(default_factory=list)
    key_haplotype_fields: List[str] = Field(default_factory=list)
    key_extra_fields: List[str] = Field(default_factory=list)
    field_descriptions: Dict[str, str] = Field(default_factory=dict)
    notes: List[str] = Field(default_factory=list)
    disorders: List[ParaphaseDisorderOut] = Field(default_factory=list)


class ParaphaseExtraFieldOut(BaseModel):
    key: str
    label: str
    value: Any = None
    description: Optional[str] = None


class ParaphaseSampleResultOut(BaseModel):
    sample: str
    role: Optional[str] = None
    affected: Optional[bool] = None
    sex: Optional[str] = None
    # Clinical status derived from locus-specific copy-number rules where defined,
    # else "review" when a copy-number change is present: pathogenic | carrier |
    # normal | review | no_call | none.
    clinical_status: Optional[str] = None
    total_cn: Optional[int] = None
    gene_cn: Optional[int] = None
    highest_total_cn: Optional[int] = None
    sample_sex: Optional[str] = None
    phase_region: Optional[str] = None
    region_depth: Dict[str, Any] = Field(default_factory=dict)
    genome_depth: Optional[float] = None
    final_haplotype_count: int = 0
    assembled_haplotype_count: int = 0
    variant_site_count: int = 0
    heterozygous_site_count: int = 0
    fusion_count: Optional[int] = None
    copy_number_signal: bool = False
    copy_number_metrics: List[ParaphaseMetricOut] = Field(default_factory=list)
    read_metrics: List[ParaphaseMetricOut] = Field(default_factory=list)
    haplotype_groups: List[ParaphaseHaplotypeGroupOut] = Field(default_factory=list)
    extra_fields: List[ParaphaseExtraFieldOut] = Field(default_factory=list)
    uploaded_at: Optional[datetime] = None


class ParaphaseGeneResultOut(BaseModel):
    gene_symbol: str
    is_medically_relevant: bool = False
    region_info: Optional[ParaphaseRegionInfoOut] = None
    max_total_cn: Optional[int] = None
    max_gene_cn: Optional[int] = None
    max_highest_total_cn: Optional[int] = None
    has_copy_number_signal: bool = False
    samples: Dict[str, ParaphaseSampleResultOut] = Field(default_factory=dict)


class FamilyParaphaseTableOut(BaseModel):
    samples: List[FamilyMemberOut] = Field(default_factory=list)
    genes: List[ParaphaseGeneResultOut] = Field(default_factory=list)
    # Number of genes with data; with ``count_only`` only this count is returned.
    genes_count: int = 0


class RepeatExpansionTrackItemOut(BaseModel):
    sample: str
    locus_id: str
    gene: str
    display_name: str
    disease: str
    chr: str
    start: int
    end: int
    motif: Optional[str] = None
    warning_min: Optional[int] = None
    pathogenic_min: Optional[int] = None
    # The call as the family table reads it (CLIN-14): against the catalogue row that
    # classifies it now, not the status and thresholds stored at import.
    status: RepeatStatus = "unknown"
    review_reason: Optional[RepeatReviewReason] = None
    allele_repeat_counts: List[int] = Field(default_factory=list)
    allele_bp_lengths: List[int] = Field(default_factory=list)
    # As on the family table, against the same catalogue row: QA's record for it, what is
    # known of TRGT's count at the gene, and one warning per allele near a boundary while it
    # is not validated.
    count_units_validated: bool = False
    count_units_note: Optional[str] = None
    count_unit_warnings: List[RepeatCountUnitWarningOut] = Field(default_factory=list)


class RepeatExpansionTrackResponse(BaseModel):
    items: List[RepeatExpansionTrackItemOut] = Field(default_factory=list)


class RepeatExpansionUploadResult(BaseModel):
    processed: int
    inserted: int
    source_format: Literal["trgt"]
