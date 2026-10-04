"""Sample-integrity checks: sex, relatedness, Mendelian, paternity, fetal sex."""

from typing import List, Optional

from pydantic import BaseModel, Field


class SampleIntegritySexCheckOut(BaseModel):
    """Genotype-inferred sex vs the recorded sex for one sample."""

    sample_id: str
    recorded_sex: str
    inferred_sex: str  # "male" | "female" | "indeterminate"
    x_het_rate: Optional[float] = None
    x_sites: int
    status: str  # "pass" | "warn" | "fail" | "skip"
    message: str


class SampleIntegrityRelatednessCheckOut(BaseModel):
    """KING-robust relatedness for a sample pair vs the pedigree's assertion."""

    sample_a: str
    sample_b: str
    expected_relationship: str
    inferred_relationship: str
    kinship: float
    ibs0_rate: float
    informative_sites: int
    status: str
    message: str


class SampleIntegrityMendelianCheckOut(BaseModel):
    """Mendelian-error rate for a child against its present parent(s)."""

    child: str
    parents: List[str]
    informative_sites: int
    mendel_errors: int
    mendel_rate: float
    status: str
    message: str


class SampleIntegrityPaternityCheckOut(BaseModel):
    """NIPT paternity: the father's homozygous alleles must all be in the plasma, his het
    ones half. ``cat7_transmitted`` counts both kinds seen, ``cat8_absent`` the homozygous
    ones missing."""

    father: str
    cat7_transmitted: int
    cat8_absent: int
    informative_sites: int
    hom_alt_transmitted: int = 0
    hom_alt_not_transmitted: int = 0
    het_transmitted: int = 0
    het_not_transmitted: int = 0
    status: str
    message: str


class SampleIntegrityFetalSexCheckOut(BaseModel):
    """NIPT fetal sex from paternal X transmission (cfDNA)."""

    inferred_sex: str  # "female" | "male" | "indeterminate"
    x_transmitted: int
    x_not_transmitted: int
    informative_sites: int
    status: str
    message: str


class SampleIntegrityCategoryQcOut(BaseModel):
    """NIPT category-distribution QC (de-novo / paternal-absent / maternal transmission)."""

    denovo: int
    paternal_absent: int
    maternal_informative: int
    maternal_inherited: int
    maternal_inherited_rate: float
    status: str
    message: str


class SampleIntegrityQcOut(BaseModel):
    """Per-family sample-integrity QC, adapted to the application.

    Catches sample swaps and mislabelled relationships before interpretation. The
    checks run depend on the application (``application``): full WGS pedigrees run
    sex + relatedness + Mendelian; PGT highlights embryo sex + parentage; NIPT runs
    paternity (cat 7/8) instead of genotype relatedness; couples run sex (+ an
    expected-unrelated confirmation); single samples run sex only.
    """

    family_id: str
    overall_status: str  # "pass" | "warn" | "fail" | "skip"
    application: str  # "wgs" | "pgt" | "nipt" | "couple" | "single" | "unknown"
    application_label: str = ""
    application_summary: str = ""
    genotype_source: Optional[str] = None
    sex_checks: List[SampleIntegritySexCheckOut] = Field(default_factory=list)
    relatedness_checks: List[SampleIntegrityRelatednessCheckOut] = Field(default_factory=list)
    mendelian_checks: List[SampleIntegrityMendelianCheckOut] = Field(default_factory=list)
    paternity_check: Optional[SampleIntegrityPaternityCheckOut] = None
    fetal_sex_check: Optional[SampleIntegrityFetalSexCheckOut] = None
    category_qc_check: Optional[SampleIntegrityCategoryQcOut] = None
    autosomal_sites: int = 0
    notes: List[str] = Field(default_factory=list)
