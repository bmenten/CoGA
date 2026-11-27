"""Monogenic NIPT: fetal fraction, classifications, coverage, artifacts."""

from datetime import date, datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from .variants import (
    VariantOut,
)


class NiptFetalFractionOut(BaseModel):
    """Fetal-fraction estimate for a monogenic NIPT family."""

    ff: float
    ff_computed: Optional[float] = None
    ff_median: Optional[float] = None
    ci_low: Optional[float] = None
    ci_high: Optional[float] = None
    n_sites: int
    method: str
    low_confidence: bool
    # The 5th and 95th percentile of the FF sites' allele fractions (the de novo window).
    vaf_q05: Optional[float] = None
    vaf_q95: Optional[float] = None


class NiptDeNovoWindowOut(BaseModel):
    """The family's fetal window the de novo triage reads (allele fractions)."""

    strict_min: float
    strict_max: float
    loose_min: float
    loose_max: float


class NiptPaternityOut(BaseModel):
    """Whether the fetus inherited the father's alleles: at the autosomal sites where he
    carries one and the mother does not, with enough plasma depth to see it. The fetus
    inherits every allele he is homozygous for and half of his het ones."""

    hom_alt_transmitted: int
    hom_alt_not_transmitted: int
    het_transmitted: int
    het_not_transmitted: int
    hom_alt_rate: Optional[float] = None
    het_rate: Optional[float] = None
    status: Literal["pass", "warn", "fail"]
    message: str


class NiptFetalSexOut(BaseModel):
    """The fetal sex from the father's X alleles in the plasma and from its chrY coverage."""

    call: Literal["female", "male", "indeterminate", "discordant"]
    # The check's verdict: signals that disagree fail it, none that tells warns
    # (nipt_quality_checks.fetal_sex_verdict).
    status: Literal["pass", "warn", "fail"]
    paternal_x: str
    x_transmitted: int
    x_not_transmitted: int
    informative_sites: int
    chry_profile: Optional[str] = None
    y_ratio: Optional[float] = None
    x_ratio: Optional[float] = None
    # Indicative only: the panel has a single chrY target.
    chry_fetal_fraction: Optional[float] = None


class NiptTargetCoverageGeneOut(BaseModel):
    gene: str
    targets: int
    weak_targets: int
    min_mean: Optional[float] = None
    mean_of_means: Optional[float] = None
    weak: List["NiptTargetOut"] = Field(default_factory=list)
    # Its targets too thin for the QC gate's fetal reads; None without a fetal-read depth.
    below_fetal_read_depth: Optional[int] = None


class NiptTargetOut(BaseModel):
    """A capture target (an exon of a panel transcript) and its depth."""

    chr: str
    start: int
    end: int
    gene: str
    attribute: str
    mean: Optional[float] = None
    median: Optional[float] = None
    min: Optional[float] = None
    proportion_covered: Optional[float] = None


class NiptTargetDetailOut(NiptTargetOut):
    """A target and whether it is weak: a mean below the critical depth, or a base without
    coverage (``TargetCoverageRow.is_weak``)."""

    weak: bool


class NiptGeneTargetsOut(BaseModel):
    """Every capture target of one gene in the plasma's target table, for the coverage page
    (REQ-NIPT-006); empty without a target table or for a gene the panel does not capture."""

    family_id: str
    gene: str
    critical_mean_depth: float
    targets: List[NiptTargetDetailOut] = Field(default_factory=list)


class NiptTargetCoverageOut(BaseModel):
    """The plasma's per-target coverage QC: every target, or a panel's or genes'. A target
    is weak when its mean depth is below ``critical_mean_depth`` or a base has no coverage."""

    targets: int
    median_mean: Optional[float] = None
    q05_mean: Optional[float] = None
    below_critical: int
    below_advisory: int
    zero_mean: int
    incomplete: int
    critical_mean_depth: float
    advisory_mean_depth: float
    genes: List[NiptTargetCoverageGeneOut] = Field(default_factory=list)
    # The mean depth at which a fetal-specific allele expects the QC gate's reads
    # (2 x bound / FF), and the targets below it; None without a fetal-fraction estimate.
    fetal_read_depth: Optional[float] = None
    below_fetal_read_depth: Optional[int] = None


class NiptExpectedFetalReadsOut(BaseModel):
    """The QC gate (REQ-NIPT-021): the plasma's median target depth x FF/2 against its bound,
    QA's or provisional (``nipt_qc_gate.expected_fetal_reads_verdict``)."""

    status: Literal["pass", "warn", "fail"]
    message: str
    expected_reads: Optional[float] = None
    median_depth: Optional[float] = None
    fetal_fraction: Optional[float] = None
    warn_value: Optional[float] = None
    error_value: Optional[float] = None
    provisional: bool


class NiptModelOut(BaseModel):
    """The model the analysis ran with: its validation, the cfDNA quality filter and the
    paternal genotype classes."""

    reference: str
    overdispersion: float
    maternal_het_bias: float
    min_quality: float
    min_alt_reads: int
    min_vaf: float
    vaf_ff_fraction: float
    max_strand_bias_fs: float
    father_het_min_vaf: float
    father_hom_alt_min_vaf: float
    min_father_depth: int


class NiptQcOut(BaseModel):
    """The NIPT quality checks: the de novo window, paternity, fetal sex, the plasma's sex
    profile and target coverage, and why cfDNA calls failed the quality filter."""

    de_novo_window: Optional[NiptDeNovoWindowOut] = None
    paternity: NiptPaternityOut
    # The fetal-fraction check: fail without an estimate, warn on a low-confidence one
    # (nipt_quality_checks.fetal_fraction_verdict).
    fetal_fraction_status: Literal["pass", "warn", "fail"]
    expected_fetal_reads: NiptExpectedFetalReadsOut
    fetal_sex: NiptFetalSexOut
    plasma_profile_status: Literal["pass", "warn", "fail", "unknown"]
    plasma_profile_message: str
    target_coverage: Optional[NiptTargetCoverageOut] = None
    quality_failures: Dict[str, int] = Field(default_factory=dict)
    model: NiptModelOut


class NiptSummaryOut(BaseModel):
    """Monogenic NIPT analysis summary of one pregnancy: fetal fraction and category/filter
    counts. ``analysis`` is the key of the monogenic NIPT read (``monogenic_nipt:ZW1``; None for a
    family the Family Builder tagged), ``fetuses`` the pregnancy's fetuses, and
    ``multiple_pregnancy_note`` what a fetal result of a multiple pregnancy says of them
    (``nipt.multiple_pregnancy_note``)."""

    family_id: str
    analysis: Optional[str] = None
    fetuses: List[str] = Field(default_factory=list)
    multiple_pregnancy_note: Optional[str] = None
    fetal_fraction: NiptFetalFractionOut
    category_counts: Dict[int, int]
    filter_counts: Dict[str, int]
    qc: Optional[NiptQcOut] = None


NiptGenotype = Literal["hom_ref", "het", "hom_alt"]


class NiptStatisticsGroupOut(BaseModel):
    """One group of the plasma's sites by the mother's and the fetus's genotype: a category
    of the NIPT analysis (``category``), or the father's het alleles the fetus did not
    inherit (``key`` ``paternal_not_transmitted``, no category). The NIPT-M tab of the
    variant summary lists them by the mother's genotype and then the fetus's."""

    key: str
    category: Optional[int] = None
    maternal_genotype: NiptGenotype
    fetal_genotype: NiptGenotype
    sites: int
    # The plasma allele fraction the category predicts at the family's fetal fraction (none
    # for an allele the fetus did not inherit), and the median of the plasma's own calls'
    # (none without one).
    expected_vaf: Optional[float] = None
    observed_median_vaf: Optional[float] = None
    # Sites classified with a confidence of 0.99 or more, and below the analysis's 0.90
    # separation (flagged ambiguous).
    high_confidence: int
    ambiguous: int
    # By the father's genotype as the analysis reads it (hom_ref, het, hom_alt, missing) and
    # by variant class (SNV, MNV, indel).
    by_father: Dict[str, int] = Field(default_factory=dict)
    by_variant_class: Dict[str, int] = Field(default_factory=dict)
    # The plasma's own calls' allele fractions in bins of ``vaf_bin_width`` from 0 to 1.
    vaf_histogram: List[int] = Field(default_factory=list)


class NiptChromosomeTransmissionOut(BaseModel):
    """Per autosome: which parent's alleles the fetus inherited. The paternal counts are the
    paternity check's (sites where the mother does not carry the allele, the father's call
    is confident and the plasma deep enough to see it); the maternal ones count her het
    alleles beside a confident hom-ref or hom-alt father, classified without ambiguity.
    ``marks`` names a parent none of whose alleles reached the fetus over at least
    ``chromosome_mark_min_sites`` informative sites: a hint of uniparental disomy or a
    deletion, not a call."""

    chrom: str
    paternal_het_transmitted: int = 0
    paternal_het_not_transmitted: int = 0
    paternal_hom_alt_transmitted: int = 0
    paternal_hom_alt_not_transmitted: int = 0
    maternal_het_inherited: int = 0
    maternal_het_not_inherited: int = 0
    fetal_hom_alt: int = 0
    marks: List[Literal["no_paternal_alleles", "no_maternal_alleles"]] = Field(default_factory=list)


class NiptArtifactCountsOut(BaseModel):
    """What the assay's recurrent-artifact list did to the family's analysis: the alleles the
    list holds for the assay, the family's alleles among them, those removed from the analysis
    (the funnel's ``failed_artifact`` counts only the plasma calls among them that passed the
    quality filter) and those the family's annotation kept in, by reason (``clinvar``:
    ClinVar pathogenic or likely pathogenic; ``common``: a population frequency above 5%)."""

    listed: int
    in_family: int
    removed: int
    protected: Dict[str, int] = Field(default_factory=dict)


class NiptVariantStatisticsOut(BaseModel):
    """The variant summary's NIPT-M tab: the plasma's sites grouped by the mother's and the
    fetus's genotype, their allele fractions, transmission per autosome, the father's
    genotypes and the plasma calls' funnel. Read from the same analysis run as the NIPT
    page (its fetal fraction, filters and categories). Descriptive: no rule reads it and the
    signed record does not hold it."""

    family_id: str
    fetal_fraction: NiptFetalFractionOut
    qc: NiptQcOut
    filter_counts: Dict[str, int]
    # The caller's own FILTER of the cfDNA calls that went in: PASS, or each other value.
    caller_filters: Dict[str, int] = Field(default_factory=dict)
    classified_sites: int
    groups: List[NiptStatisticsGroupOut] = Field(default_factory=list)
    # Sites the analysis read but gave no group, by reason.
    not_classified: Dict[str, int] = Field(default_factory=dict)
    vaf_bin_width: float
    chromosomes: List[NiptChromosomeTransmissionOut] = Field(default_factory=list)
    chromosome_mark_min_sites: int
    # The father's genotype class at the analysed sites, from his allele fraction.
    father_genotypes: Dict[str, int] = Field(default_factory=dict)
    artifacts: NiptArtifactCountsOut


class NiptClassificationOut(BaseModel):
    """The NIPT classification block attached to each cfDNA variant.

    The variant payload itself is the full small-variant shape (``VariantOut``);
    this is the maternal/fetal interpretation layered on top. ``NiptVariantOut``
    (defined after ``VariantOut`` below) combines the two."""

    category: Optional[int] = None
    category_label: str
    maternal_state: str
    fetal_inheritance: str
    expected_vaf: float
    observed_vaf: Optional[float] = None
    confidence: float
    flags: List[str] = Field(default_factory=list)
    runner_up_category: Optional[int] = None
    runner_up_confidence: Optional[float] = None
    # The fetal-inheritance probabilities, where the site tells them.
    paternal_transmission_probability: Optional[float] = None
    maternal_allele_probability: Optional[float] = None
    fetal_hom_alt_probability: Optional[float] = None
    fetal_genotype_posterior: Optional[Dict[str, float]] = None
    quality_failures: List[str] = Field(default_factory=list)
    # The reads behind the call: the plasma's (estimated from its coverage when it has no
    # call there) and the father's genotype class, allele fraction and depth.
    cf_alt_reads: Optional[int] = None
    cf_depth: Optional[int] = None
    cf_depth_estimated: bool = False
    father_state: Optional[str] = None
    father_vaf: Optional[float] = None
    father_depth: Optional[int] = None
    father_alt_reads: Optional[int] = None
    # Where the plasma's or the father's VCF writes this allele another way (an MNV for its
    # SNVs, or the reverse), the record whose call the analysis read for that sample
    # (``plasma_call_other_representation``, ``father_call_other_representation``): the
    # variant's own record holds no call of that sample.
    cf_call_variant_id: Optional[str] = None
    father_call_variant_id: Optional[str] = None
    de_novo: Optional["NiptDeNovoTriageOut"] = None
    recurrence: "NiptRecurrenceOut"


class NiptRecurrenceOut(BaseModel):
    """How the allele recurs in the other cfDNA samples of the family's assay, every
    project's (counts only). ``other_samples`` have a call on the assembly; ``carriers``
    call the allele with at least 5 alt reads and 1% of the reads. Of those, ``maternal``
    sit at an allele fraction of 0.25 or more (that mother carries it), ``paternal_low_vaf``
    lower where that father carries it, and ``unexplained_low_vaf`` lower where no parent
    does: the mark of a recurrent artefact, at a median fraction of
    ``unexplained_median_vaf``. ``protection`` names why the recurrence filter keeps the
    variant whatever its count: ``clinvar`` (a ClinVar record may call it pathogenic) or
    ``common`` (a population frequency above 5%)."""

    other_samples: int
    carriers: int
    maternal: int
    paternal_low_vaf: int
    unexplained_low_vaf: int
    unexplained_median_vaf: Optional[float] = None
    protection: Optional[Literal["clinvar", "common"]] = None


class NiptDeNovoTriageOut(BaseModel):
    """A de novo candidate's triage (the R NIPT-M rules): where its allele fraction falls
    in the fetal window, its score and priority, and what lowered or raised it."""

    window: Literal["strict", "loose", "below", "above"]
    score: int
    label: Literal["high", "medium", "low", "excluded_recurrent", "outside_window"]
    reasons: List[str] = Field(default_factory=list)
    other_cfdna_carriers: int = 0


class NiptRecessiveAlleleOut(BaseModel):
    variant_id: str
    inherited_probability: Optional[float] = None
    category: Optional[int] = None
    note: Optional[str] = None


class NiptRecessiveGeneOut(BaseModel):
    """A gene where both parents carry an allele (on chrX outside the pseudo-autosomal regions
    also one where a son's mother, one parent of a daughter, or either parent of a fetus whose
    sex is not known carries one): whether the fetus inherited each, the probability it is
    affected (``risk``; a carrier couple's prior is 25%; none when the fetal sex is not known),
    a carrier or neither, and its fetal status (``carrier_or_unaffected``: one allele surely
    not inherited, the other not told)."""

    gene: str
    maternal: List[NiptRecessiveAlleleOut]
    paternal: List[NiptRecessiveAlleleOut]
    risk: Optional[float]
    # The pair the status rests on (none when the fetal sex is not known).
    maternal_variant_id: Optional[str]
    paternal_variant_id: Optional[str]
    risk_uses_prior: bool
    status: Literal["affected", "carrier", "unaffected", "carrier_or_unaffected", "undetermined"]
    carrier_probability: Optional[float]
    unaffected_probability: Optional[float]
    # autosomal; x_linked_daughter (paired as an autosome, one parent's allele alone at most a
    # carrier); x_linked_son (his mother's allele alone); x_linked_sex_unknown (each parent's
    # alleles, the fetal sex not known).
    mode: Literal["autosomal", "x_linked_daughter", "x_linked_son", "x_linked_sex_unknown"]


class NiptRecessiveGeneGroupOut(NiptRecessiveGeneOut):
    """A gene of the NIPT list's recessive view, as the list groups it: its fetal status, its card
    gene's id, and every carrier variant of the gene, as the list shows a variant."""

    group_type: Literal["nipt_recessive_gene"] = "nipt_recessive_gene"
    group_key: str
    gene_id: Optional[str] = None
    # NiptVariantOut subclasses VariantOut, which is defined later in this module.
    variants: List["NiptVariantOut"] = Field(default_factory=list)


class NiptNotClassifiedOut(BaseModel):
    """A variant the review-tag filter selects that the NIPT analysis does not classify, so
    that no variant list shows it, and why: ``not_in_data`` (it is not in the family's
    NIPT data), ``artifact_list`` (it is on the assay's recurrent-artifact list, which the
    analysis leaves out) or ``no_plasma_call`` (the plasma has no call of it). The signed
    record's ``nipt`` section names a reported variant the same way (``not_classified``)."""

    variant_id: str
    reason: Literal["not_in_data", "artifact_list", "no_plasma_call"]


class NiptTrioOut(BaseModel):
    """The samples the monogenic NIPT of one pregnancy reads: the maternal plasma (cfDNA, a
    mixture of the mother's and the fetus's DNA, so no genotype), the father, and the
    pregnancy's fetuses, placeholder members that hold no data (none when the family has none,
    two or more in a multiple pregnancy)."""

    cfdna_sample_id: str
    father_sample_id: str
    fetus_sample_ids: List[str] = Field(default_factory=list)


class NiptVariantPage(BaseModel):
    """A page of classified cfDNA variants plus the family's fetal fraction."""

    family_id: str
    trio: NiptTrioOut
    total: int
    # True when more variants matched the scope than the list classifies at once
    # (``count_limit``, the first ones in genomic order): ``total`` is then a lower bound,
    # and the list stops part-way through the genome.
    total_is_estimated: bool = False
    count_limit: Optional[int] = None
    fetal_fraction: NiptFetalFractionOut
    # NiptVariantOut subclasses VariantOut, which is defined later in this module.
    variants: List["NiptVariantOut"]
    # The recessive view's results: its genes, most likely affected first, each with its
    # carrier variants (``variants`` then holds none, and ``total`` counts the genes);
    # ``gene_total`` and ``variant_total`` count them on every page.
    variant_groups: List[NiptRecessiveGeneGroupOut] = Field(default_factory=list)
    gene_total: Optional[int] = None
    variant_total: Optional[int] = None
    # The variants the review-tag filter selects that the analysis does not classify,
    # whatever the other filters, by variant id; [] without a review-tag filter.
    not_classified: List[NiptNotClassifiedOut] = Field(default_factory=list)
    # The two ClinVar releases the list names (design B7), as the family page's VariantPage: the
    # release of the ClinVar file the analysis's annotation names, and CoGA's own release the
    # analysis read, of each variant's ``clinvar_coga``.
    clinvar_annotation_release: Optional[date] = None
    clinvar_coga_release: Optional[date] = None


class NiptCoverageRegionOut(BaseModel):
    """On-target coverage for a single target region."""

    label: str
    chr: str
    start: int
    end: int
    median_coverage: Optional[float] = None
    covered_bases: int
    target_bases: int


class NiptCoverageLowRegionOut(BaseModel):
    """A panel gene / target region flagged as inadequately interrogated."""

    label: str
    chr: str
    median_coverage: Optional[float] = None
    covered_fraction: float
    reason: str  # "no_coverage" | "low_depth" | "partial_coverage"


class NiptCoverageSummaryOut(BaseModel):
    """Median on-target coverage for a monogenic NIPT family's cfDNA sample.

    ``low_coverage_regions`` is a compact QC list (only the failing genes, not
    the full per-region table) flagging genes that may harbour silent gaps.
    """

    family_id: str
    overall_median_on_target: Optional[float] = None
    target_region_count: int
    per_region: List[NiptCoverageRegionOut]
    min_depth: float
    min_covered_fraction: float
    low_coverage_regions: List[NiptCoverageLowRegionOut] = Field(default_factory=list)
    # The capture targets' QC when the plasma has a per-target coverage table; the fields
    # above then stay empty.
    targets: Optional[NiptTargetCoverageOut] = None
    # Without a table: why each of the plasma's coverage tracks over the regions was not read
    # as depth, a log2 ratio or a coverage BED that does not declare what it holds,
    # so regions without coverage say why. Empty when a track of read depth was read, or none
    # is there.
    coverage_notes: List[str] = Field(default_factory=list)


class NiptArtifactOut(BaseModel):
    """A recurrent-artifact (panel-of-normals) entry for monogenic NIPT."""

    id: str
    assembly_id: str
    assay_key: str
    variant_id: str
    recurrence_count: int
    source: str
    label: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class NiptArtifactDeleteOut(BaseModel):
    """A NIPT artifact deleted (an unknown one is a 404)."""

    deleted: bool


class NiptArtifactCreate(BaseModel):
    """Create/update a NIPT artifact within an assembly + assay/panel scope."""

    assembly_id: str
    variant_id: str
    assay_key: str = "nipt_cfdna"
    label: Optional[str] = None
    source: Literal["curated", "auto"] = "curated"
    recurrence_count: int = 0


class NiptArtifactAutoSeed(BaseModel):
    """Seed the artifact list with the alleles that are an unexplained low-VAF call in at
    least ``min_unexplained_samples`` cfDNA samples of the assay. The default suits a start
    with about 20 families."""

    assembly_id: str
    assay_key: str = "nipt_cfdna"
    min_unexplained_samples: int = Field(default=3, ge=1)


class NiptArtifactScopeOut(BaseModel):
    """An artifact-list scope (``assay_key``): the default ``nipt_cfdna``, a panel that a
    cfDNA sample's ``assay_panel`` names, or a scope the assembly's list holds entries for.
    ``cfdna_samples`` counts the samples tagged ``assay: nipt_cfdna`` whose panel resolves
    to the scope, in every project: the samples whose calls the auto-seed reads. ``listed``
    counts the scope's entries on the assembly's list."""

    assay_key: str
    cfdna_samples: int
    listed: int


class NiptArtifactSeedVariantOut(BaseModel):
    """An allele the auto-seed lists, or would list: an unexplained low-VAF call in
    ``recurrence_count`` cfDNA samples of the assay, the count its entry takes.
    ``listed_source`` is the source of the allele's entry already on the list, whose
    recurrence count the seed refreshes and whose source and label it keeps; ``None`` for
    an allele the seed adds."""

    variant_id: str
    recurrence_count: int
    listed_source: Optional[Literal["curated", "auto"]] = None


class NiptArtifactAutoSeedPreviewOut(BaseModel):
    """What an auto-seed at ``min_unexplained_samples`` would write now, read as the seed
    reads it, without writing: the number of cfDNA samples of the assay whose calls it
    reads, and each allele it would list, most recurrent first."""

    assay_key: str
    min_unexplained_samples: int
    cfdna_samples: int
    variants: List[NiptArtifactSeedVariantOut]


class NiptArtifactAutoSeedOut(NiptArtifactAutoSeedPreviewOut):
    """What an auto-seed wrote: the ``seeded`` alleles, each in ``variants``."""

    seeded: int


class NiptArtifactImportOut(BaseModel):
    """What an artefact-table import did: the rows it read, those the table does not flag
    as artefacts or could not read, the alleles it added, and those it refused because
    their annotation is common or may assert pathogenic in ClinVar."""

    rows_read: int
    not_flagged: int
    invalid: int
    imported: int
    protected_common: int
    protected_clinvar: int


class NiptDiagnosticPanelOut(BaseModel):
    """A monogenic NIPT's diagnostic gene panel as it is now (REQ-NIPT-019): the panel, its local
    version and the release of its source, and when the analysis took it."""

    panel_id: str
    name: str
    version: int
    source: Optional[str] = None
    gene_count: int
    external_version: Optional[str] = None
    set_at: Optional[datetime] = None


class NiptDiagnosticPanelStateOut(BaseModel):
    """The analysis (its key) and its diagnostic panel; ``panel`` is None when it has none."""

    analysis: str
    panel: Optional[NiptDiagnosticPanelOut] = None


class NiptDiagnosticPanelUpdate(BaseModel):
    """Set the analysis's diagnostic panel (a gene panel's id), or clear it (an explicit null):
    the field is required, so an empty body clears nothing."""

    panel_id: Optional[str] = Field(..., max_length=64)


class NiptVariantOut(VariantOut):
    """A classified cfDNA variant: the full small-variant payload plus the NIPT
    classification block. Defined here so it can extend ``VariantOut`` (declared
    earlier in this module); ``NiptVariantPage`` forward-references it."""

    nipt: NiptClassificationOut


class NiptDiagnosticCriteriaOut(BaseModel):
    """The locked diagnostic criteria of the R validation (REQ-NIPT-020) and their version."""

    reference: str
    rare_max_af_dominant: float
    rare_max_af_recessive: float
    effect_impacts: List[str]
    min_spliceai: float
    clinvar_max_af: float
    cohort_common_min_samples: int
    repeat_min_units: float


class NiptDiagnosticCandidateOut(BaseModel):
    """A variant the locked criteria leave for further investigation in a view, with its
    confidence (``low``: a de novo indel in a repeat) and its flags (``repeat_context``,
    ``clinvar_pathogenic``)."""

    variant: NiptVariantOut
    confidence: Literal["standard", "low"]
    flags: List[str] = Field(default_factory=list)


class NiptDiagnosticCandidatesOut(BaseModel):
    """The locked diagnostic candidates of a NIPT report's scope (its gene panel and gene), per
    view, against the analysis's fetal fraction. ``capped``: the scope held more variants than
    the analysis classifies at once, so the lists may be incomplete."""

    family_id: str
    trio: NiptTrioOut
    fetal_fraction: NiptFetalFractionOut
    criteria: NiptDiagnosticCriteriaOut
    panel_id: Optional[str] = None
    gene: Optional[str] = None
    capped: bool = False
    de_novo: List[NiptDiagnosticCandidateOut] = Field(default_factory=list)
    paternal: List[NiptDiagnosticCandidateOut] = Field(default_factory=list)
    maternal: List[NiptDiagnosticCandidateOut] = Field(default_factory=list)
    recessive_genes: List[NiptRecessiveGeneOut] = Field(default_factory=list)
    recessive_variants: List[NiptDiagnosticCandidateOut] = Field(default_factory=list)


NiptRecessiveGeneGroupOut.model_rebuild()
NiptVariantPage.model_rebuild()
NiptClassificationOut.model_rebuild()
NiptTargetCoverageGeneOut.model_rebuild()
