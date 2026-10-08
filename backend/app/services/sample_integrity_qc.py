"""Sample-integrity / pedigree-concordance QC (pure core).

In a pedigree-driven pipeline the worst silent failure is a sample swap or a
mislabelled relationship: the variants are real but attributed to the wrong
person, so inheritance reasoning is quietly wrong. This module runs three
reference-free checks that catch that before interpretation:

  * Sex concordance -- genotype-inferred sex (chrX heterozygosity) vs the
    recorded sex.
  * Relatedness concordance -- KING-robust kinship + IBS0 between sample pairs
    vs the relationship the pedigree asserts (parent-child / sibling / unrelated).
  * Mendelian error rate -- parent-child / trio transmission consistency.

All inputs are pre-parsed genotype arrays; there is no I/O here so the maths is
unit-testable. The loader and pedigree resolution live in the service layer.

Genotypes are ``tuple[int, int]`` allele-index pairs (e.g. ``(0, 1)``); ``None``
marks a missing / unusable call at that site. Arrays for different samples are
aligned by index (same site at the same position in every sample's list).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .nipt_analysis import MIN_FETAL_SEX_SITES

Genotype = tuple[int, int]
Status = Literal["pass", "warn", "fail", "skip"]

# --- Relatedness (KING-robust) thresholds -----------------------------------
# Standard KING kinship cut-points (Manichaikul et al. 2010): duplicate ~0.5,
# first-degree ~0.25, second-degree ~0.125, third-degree ~0.0625. They are
# genome-wide expectations, so the genotypes are a sample of sites across every
# autosome (sample_integrity_service). Siblings share 0, 1 or 2 haplotypes in
# blocks tens of Mb long: over the genome their kinship stays near 0.25 (about
# 0.19-0.31, Visscher et al. 2006), over a few blocks it can reach either
# neighbouring band.
MIN_RELATEDNESS_SITES = 1_000
KINSHIP_DUPLICATE = 0.354
KINSHIP_FIRST_DEGREE = 0.177
KINSHIP_SECOND_DEGREE = 0.0884
KINSHIP_THIRD_DEGREE = 0.0442
# Within first-degree, parent and child share an allele at every site, so their
# IBS0 is genotype error alone. Full siblings are opposite homozygotes only where
# they share no haplotype (about a quarter of the genome), at a rate set by the
# allele frequencies of the sites, so their IBS0 depends on the callset. A sibling
# pair below the cut reads as parent-child, which a recorded sibling pair accepts
# as first degree.
IBS0_PARENT_CHILD_MAX = 0.008

# --- Sex inference (chrX heterozygosity) ------------------------------------
MIN_X_SITES = 200
MALE_MAX_X_HET = 0.05  # hemizygous males call almost no X heterozygotes
FEMALE_MIN_X_HET = 0.15

# --- Mendelian error rate ----------------------------------------------------
MIN_MENDEL_SITES = 200
MENDEL_WARN_RATE = 0.02
MENDEL_FAIL_RATE = 0.05

# --- NIPT paternity (the father's alleles in the plasma) --------------------
# Read at the autosomal sites where the father carries an allele and the mother does not,
# with enough plasma depth that a transmitted allele could not be missed
# (nipt_analysis.paternal_transmission_evidence). The fetus inherits every allele its
# father is homozygous for, and half of his het ones: the true father's homozygous alleles
# are all seen but a few coverage dropouts, another man's far from all, and his het ones
# well under half. Pooling the two kinds hid a wrong paternal file: the share of its
# alleles absent stayed under the old 40% warning.
MIN_PATERNITY_HOM_ALT_SITES = 20
MIN_PATERNITY_HET_SITES = 50
PATERNITY_HOM_ALT_PASS_RATE = 0.90
PATERNITY_HOM_ALT_FAIL_RATE = 0.80
PATERNITY_HET_PASS_RANGE = (0.35, 0.65)
PATERNITY_HET_FAIL_RANGE = (0.25, 0.75)

# --- NIPT category-distribution QC ------------------------------------------
# De novo (category 1) is rare; an excess flags artifacts or contamination.
NIPT_DENOVO_WARN_RATE = 0.05
NIPT_DENOVO_FAIL_RATE = 0.15
# A true mother transmits each maternal-het allele to the fetus ~50% of the time
# (categories 3+4 inherited vs 2 not inherited). Gross deviation flags the wrong
# mother / a sample issue; cfDNA detection biases keep the tolerance wide.
MIN_MATERNAL_TRANSMISSION_SITES = 20
MATERNAL_TRANSMISSION_WARN_DELTA = 0.15
MATERNAL_TRANSMISSION_FAIL_DELTA = 0.30


@dataclass(slots=True)
class SexCheck:
    sample_id: str
    recorded_sex: str
    inferred_sex: str  # "male" | "female" | "indeterminate"
    x_het_rate: float | None
    x_sites: int
    status: Status
    message: str


@dataclass(slots=True)
class RelatednessCheck:
    sample_a: str
    sample_b: str
    expected_relationship: str
    inferred_relationship: str
    kinship: float
    ibs0_rate: float
    informative_sites: int
    status: Status
    message: str


@dataclass(slots=True)
class MendelianCheck:
    child: str
    parents: list[str]
    informative_sites: int
    mendel_errors: int
    mendel_rate: float
    status: Status
    message: str


@dataclass(slots=True)
class PaternityCheck:
    """NIPT paternity: whether the fetus inherited the father's alleles (no genotype
    relatedness; the fetus is not sequenced).

    His homozygous alleles must all be in the plasma (the fetus inherits one of each);
    his het ones half. A homozygous allele missing at a depth where it cannot be missed
    (category 8) points at another father or a sample mixup.
    """

    father: str
    hom_alt_transmitted: int
    hom_alt_not_transmitted: int
    het_transmitted: int
    het_not_transmitted: int
    status: Status
    message: str

    @property
    def cat7_transmitted(self) -> int:
        return self.hom_alt_transmitted + self.het_transmitted

    @property
    def cat8_absent(self) -> int:
        return self.hom_alt_not_transmitted

    @property
    def informative_sites(self) -> int:
        return (
            self.hom_alt_transmitted
            + self.hom_alt_not_transmitted
            + self.het_transmitted
            + self.het_not_transmitted
        )


@dataclass(slots=True)
class FetalSexCheck:
    """NIPT fetal sex from paternal X transmission (cfDNA, no chrY needed)."""

    inferred_sex: str  # "female" | "male" | "indeterminate"
    x_transmitted: int
    x_not_transmitted: int
    informative_sites: int
    status: Status
    message: str


@dataclass(slots=True)
class NiptCategoryQc:
    """QC on the NIPT category distribution.

    De-novo (category 1) and paternal-absent (category 8) excess flag artifacts /
    non-paternity, and the maternal transmission rate (categories 3+4 inherited vs
    2 not inherited) should sit near 50% for the true mother.
    """

    denovo: int  # category 1
    paternal_absent: int  # category 8
    maternal_informative: int  # categories 2 + 3 + 4
    maternal_inherited: int  # categories 3 + 4
    maternal_inherited_rate: float
    status: Status
    message: str


# The five applications CoGA runs, each with a different input modality and a
# different set of meaningful integrity checks (see profile_for).
ApplicationKind = Literal["wgs", "pgt", "nipt", "couple", "single", "unknown"]


@dataclass(slots=True)
class QcProfile:
    application: ApplicationKind
    label: str
    summary: str
    run_sex: bool
    run_relatedness: bool
    run_mendelian: bool
    run_paternity: bool


@dataclass(slots=True)
class SampleIntegrityReport:
    overall_status: Status
    application: ApplicationKind = "unknown"
    application_label: str = ""
    application_summary: str = ""
    genotype_source: str | None = None
    sex_checks: list[SexCheck] = field(default_factory=list)
    relatedness_checks: list[RelatednessCheck] = field(default_factory=list)
    mendelian_checks: list[MendelianCheck] = field(default_factory=list)
    paternity_check: PaternityCheck | None = None
    fetal_sex_check: FetalSexCheck | None = None
    category_qc_check: NiptCategoryQc | None = None
    autosomal_sites: int = 0
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PedigreeSpec:
    """The pedigree's assertions, resolved from family metadata."""

    recorded_sex: dict[str, str]  # sample_id -> "male" | "female" | "unknown"
    # child -> {"father": id, "mother": id} (either parent may be absent)
    parents_of: dict[str, dict[str, str]]


_STATUS_RANK: dict[Status, int] = {"skip": 0, "pass": 1, "warn": 2, "fail": 3}


def _worst(statuses: list[Status]) -> Status:
    worst: Status = "skip"
    for status in statuses:
        if _STATUS_RANK[status] > _STATUS_RANK[worst]:
            worst = status
    return worst


def _is_het(gt: Genotype) -> bool:
    return gt[0] != gt[1]


def _is_biallelic(gt: Genotype) -> bool:
    return gt[0] in (0, 1) and gt[1] in (0, 1)


# --- chrX heterozygosity → sex ----------------------------------------------

def x_heterozygosity(genotypes: list[Genotype | None]) -> tuple[float | None, int]:
    """(het_rate, n_called) over biallelic chrX sites."""
    called = 0
    het = 0
    for gt in genotypes:
        if gt is None or not _is_biallelic(gt):
            continue
        called += 1
        if _is_het(gt):
            het += 1
    if called == 0:
        return None, 0
    return het / called, called


def infer_sex(genotypes: list[Genotype | None]) -> tuple[str, float | None, int]:
    het_rate, n = x_heterozygosity(genotypes)
    if het_rate is None or n < MIN_X_SITES:
        return "indeterminate", het_rate, n
    if het_rate <= MALE_MAX_X_HET:
        return "male", het_rate, n
    if het_rate >= FEMALE_MIN_X_HET:
        return "female", het_rate, n
    return "indeterminate", het_rate, n


# --- KING-robust kinship + IBS0 ---------------------------------------------

def king_relatedness(
    a: list[Genotype | None], b: list[Genotype | None]
) -> tuple[float, float, int]:
    """KING-robust kinship and IBS0 rate over jointly-called biallelic sites.

    kinship = (N_het,het - 2*N_opposite-hom) / (N_het(a) + N_het(b)); duplicates
    ~0.5, first-degree ~0.25. IBS0 (opposite homozygotes) separates parent-child
    (~0) from full siblings within the first-degree band.
    """
    het_a = het_b = het_het = opp_hom = n = 0
    for ga, gb in zip(a, b):
        if ga is None or gb is None or not _is_biallelic(ga) or not _is_biallelic(gb):
            continue
        n += 1
        a_het, b_het = _is_het(ga), _is_het(gb)
        if a_het:
            het_a += 1
        if b_het:
            het_b += 1
        if a_het and b_het:
            het_het += 1
        elif not a_het and not b_het and ga[0] != gb[0]:
            opp_hom += 1
    if n == 0:
        return 0.0, 0.0, 0
    denom = het_a + het_b
    kinship = (het_het - 2 * opp_hom) / denom if denom else 0.0
    return kinship, opp_hom / n, n


def classify_relatedness(kinship: float, ibs0_rate: float, n_sites: int) -> str:
    if n_sites < MIN_RELATEDNESS_SITES:
        return "indeterminate"
    if kinship > KINSHIP_DUPLICATE:
        return "duplicate"
    if kinship > KINSHIP_FIRST_DEGREE:
        return "parent-child" if ibs0_rate < IBS0_PARENT_CHILD_MAX else "sibling"
    if kinship > KINSHIP_SECOND_DEGREE:
        return "second-degree"
    if kinship > KINSHIP_THIRD_DEGREE:
        return "third-degree"
    return "unrelated"


# --- Mendelian consistency ---------------------------------------------------

def _mendelian_consistent(father: Genotype, mother: Genotype, child: Genotype) -> bool:
    """Child genotype forms from one paternal + one maternal allele."""
    child_state = tuple(sorted(child))
    for pa in father:
        for ma in mother:
            if tuple(sorted((pa, ma))) == child_state:
                return True
    return False


def _shares_allele(a: Genotype, b: Genotype) -> bool:
    return bool({a[0], a[1]} & {b[0], b[1]})


def mendelian_stats(
    child: list[Genotype | None],
    father: list[Genotype | None] | None,
    mother: list[Genotype | None] | None,
) -> tuple[int, int]:
    """(informative_sites, mendel_errors) for a child against present parent(s).

    A trio site is informative when child and both present parents are called;
    an error is a transmission the parents cannot produce. With a single known
    parent the only detectable error is the child sharing no allele with it.
    """
    informative = errors = 0
    n = len(child)
    for i in range(n):
        c = child[i]
        if c is None:
            continue
        f = father[i] if father is not None and i < len(father) else None
        m = mother[i] if mother is not None and i < len(mother) else None
        if father is not None and mother is not None:
            if f is None or m is None:
                continue
            informative += 1
            if not _mendelian_consistent(f, m, c):
                errors += 1
        else:
            parent = f if father is not None else m
            if parent is None:
                continue
            informative += 1
            if not _shares_allele(parent, c):
                errors += 1
    return informative, errors


# --- Orchestration -----------------------------------------------------------

def _norm_sex(value: str | None) -> str:
    text = (value or "").strip().lower()
    if text in ("male", "m", "1"):
        return "male"
    if text in ("female", "f", "2"):
        return "female"
    return "unknown"


def _evaluate_sex(
    sample_id: str, recorded: str, x_gts: list[Genotype | None] | None
) -> SexCheck:
    if not x_gts:
        return SexCheck(sample_id, recorded, "indeterminate", None, 0, "skip",
                        "No chrX genotypes available for sex inference.")
    inferred, het_rate, n = infer_sex(x_gts)
    rate_text = f"{het_rate:.1%} chrX het over {n} sites" if het_rate is not None else "no sites"
    if inferred == "indeterminate":
        return SexCheck(sample_id, recorded, inferred, het_rate, n, "warn",
                        f"Could not infer sex ({rate_text}).")
    if recorded == "unknown":
        return SexCheck(sample_id, recorded, inferred, het_rate, n, "warn",
                        f"Sex not recorded; genotypes indicate {inferred} ({rate_text}).")
    if inferred == recorded:
        return SexCheck(sample_id, recorded, inferred, het_rate, n, "pass",
                        f"Genotype-inferred sex matches the record ({rate_text}).")
    return SexCheck(sample_id, recorded, inferred, het_rate, n, "fail",
                    f"Recorded {recorded} but genotypes indicate {inferred} "
                    f"({rate_text}) — possible sample swap or mislabel.")


def _relationship_check(
    a: str,
    b: str,
    expected: str,
    autosomal: dict[str, list[Genotype | None]],
    *,
    coparents: bool = False,
) -> RelatednessCheck:
    kinship, ibs0, n = king_relatedness(autosomal.get(a, []), autosomal.get(b, []))
    inferred = classify_relatedness(kinship, ibs0, n)
    metrics = f"kinship {kinship:.3f}, IBS0 {ibs0:.2%}, {n} sites"
    status, message = _relationship_status(expected, inferred, n, metrics, coparents=coparents)
    return RelatednessCheck(a, b, expected, inferred, kinship, ibs0, n, status, message)


# A first-degree observation covers both parent-child and sibling.
_FIRST_DEGREE = {"parent-child", "sibling"}


def _relationship_status(
    expected: str, inferred: str, n: int, metrics: str, *, coparents: bool = False
) -> tuple[Status, str]:
    if inferred == "indeterminate" or n < MIN_RELATEDNESS_SITES:
        return "warn", f"Too few shared sites to assess relatedness ({metrics})."
    if expected == "parent-child":
        if inferred == "parent-child":
            return "pass", f"Confirmed parent-child ({metrics})."
        return "fail", (
            f"Recorded parent-child but genotypes look {inferred} ({metrics}) — "
            "possible sample swap or wrong parent."
        )
    if expected == "sibling":
        if inferred in _FIRST_DEGREE:
            return "pass", f"Consistent with full siblings ({metrics})."
        return "fail", f"Recorded siblings but genotypes look {inferred} ({metrics})."
    # Expected unrelated. Co-parents get a consanguinity-oriented reading.
    if inferred == "unrelated":
        if coparents:
            return "pass", f"Parents are unrelated — no consanguinity ({metrics})."
        return "pass", f"Unrelated, as expected ({metrics})."
    if inferred in ("third-degree", "second-degree"):
        if coparents:
            return "warn", f"Parents look {inferred} — possible consanguinity ({metrics})."
        return "warn", f"Unexpected relatedness — looks {inferred} ({metrics})."
    if coparents:
        return "fail", (
            f"Parents look {inferred} ({metrics}) — consanguinity or a sample duplication."
        )
    return "fail", (
        f"Recorded unrelated but genotypes look {inferred} ({metrics}) — "
        "possible duplicate or swap."
    )


def _mendelian_check(
    child: str,
    parents: dict[str, str],
    autosomal: dict[str, list[Genotype | None]],
) -> MendelianCheck | None:
    # Robust to the pedigree's parent-side keys ("father"/"mother"/"parent"):
    # use whichever stated parents actually have genotypes.
    parent_ids = [pid for pid in dict.fromkeys(parents.values()) if pid in autosomal]
    if not parent_ids:
        return None
    if len(parent_ids) >= 2:
        father, mother = autosomal[parent_ids[0]], autosomal[parent_ids[1]]
        parent_ids = parent_ids[:2]
    else:
        father, mother = autosomal[parent_ids[0]], None
    informative, errors = mendelian_stats(autosomal.get(child, []), father, mother)
    rate = errors / informative if informative else 0.0
    metrics = f"{errors}/{informative} sites ({rate:.2%})"
    if informative < MIN_MENDEL_SITES:
        status: Status = "warn"
        message = f"Too few informative sites for a reliable estimate ({metrics})."
    elif rate >= MENDEL_FAIL_RATE:
        status = "fail"
        message = f"High Mendelian-error rate {metrics} — likely swap or wrong parent."
    elif rate >= MENDEL_WARN_RATE:
        status = "warn"
        message = f"Elevated Mendelian-error rate {metrics}."
    else:
        status = "pass"
        message = f"Mendelian-error rate within tolerance ({metrics})."
    return MendelianCheck(child, parent_ids, informative, errors, rate, status, message)


def _pair_key(a: str, b: str) -> tuple[str, str]:
    """A sample pair in sorted order, the key for expected relationships."""
    return (a, b) if a <= b else (b, a)


def _sibling_pairs(parents_of: dict[str, dict[str, str]]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    children = sorted(parents_of)
    for i, c1 in enumerate(children):
        for c2 in children[i + 1:]:
            shared = set(parents_of[c1].values()) & set(parents_of[c2].values())
            if shared:
                pairs.add((c1, c2))
    return pairs


# --- Application profiles ----------------------------------------------------

def resolve_application(
    *,
    analysis_type: str | None,
    roles: dict[str, str],
    parents_of: dict[str, dict[str, str]],
    sample_count: int,
) -> ApplicationKind:
    """Infer the application from the analysis type and the family shape.

    Only monogenic NIPT is tagged explicitly; the rest are read from structure
    (the input files / pedigree the family was built from), per CoGA's design.
    """
    if (analysis_type or "").strip().lower() == "monogenic_nipt":
        return "nipt"
    if any((role or "").strip().lower() == "embryo" for role in roles.values()):
        return "pgt"
    if sample_count <= 1:
        return "single"
    # A carrier couple (BEGECS): two members with no parent-child edge between them.
    if sample_count == 2 and not parents_of:
        return "couple"
    if parents_of:
        return "wgs"
    return "unknown"


_PROFILES: dict[ApplicationKind, QcProfile] = {
    "wgs": QcProfile(
        "wgs", "Long-read WGS family",
        "Full pedigree QC on the SNV call set: sex concordance, relatedness vs the "
        "pedigree, and the Mendelian-error rate for parent-child pairs.",
        run_sex=True, run_relatedness=True, run_mendelian=True,
        run_paternity=False,
    ),
    "pgt": QcProfile(
        "pgt", "Shallow-WGS PGT (imputed)",
        "Embryo integrity from imputed genotypes: each embryo's sex, and that every "
        "embryo is a true first-degree child of both parents (no sample switch).",
        run_sex=True, run_relatedness=True, run_mendelian=True,
        run_paternity=False,
    ),
    # The per-member sex check is off here; the service sexes the father and the cfDNA
    # sample itself (extra_sex_checks), which is why the summary still names it.
    "nipt": QcProfile(
        "nipt", "Monogenic NIPT (cfDNA)",
        "cfDNA integrity from the NIPT classification: paternity is confirmed from "
        "paternal-transmitted sites (categories 7/8), which excludes a sample mixup; "
        "fetal sex is read from paternal-X transmission (no chrY needed); and the "
        "category distribution is checked (de-novo excess, ~50% maternal transmission). "
        "Parent sex is checked from chrX zygosity for the father and for the cfDNA "
        "sample, which reads as the mother because maternal DNA dominates the plasma. "
        "Genotype relatedness and Mendelian errors do not apply to a maternal/fetal "
        "mixture.",
        run_sex=False, run_relatedness=False, run_mendelian=False,
        run_paternity=True,
    ),
    "couple": QcProfile(
        "couple", "Carrier couple (BEGECS)",
        "Sex concordance for each partner. The couple is expected to be unrelated, "
        "which is confirmed (or flagged if they are not).",
        run_sex=True, run_relatedness=True, run_mendelian=False,
        run_paternity=False,
    ),
    "single": QcProfile(
        "single", "Single sample (targeted)",
        "Sex concordance only — a single sample has no relatedness or Mendelian "
        "context.",
        run_sex=True, run_relatedness=False, run_mendelian=False,
        run_paternity=False,
    ),
    "unknown": QcProfile(
        "unknown", "Family",
        "Sex concordance, relatedness and Mendelian-error rate where the data allows.",
        run_sex=True, run_relatedness=True, run_mendelian=True,
        run_paternity=False,
    ),
}


def profile_for(application: ApplicationKind) -> QcProfile:
    return _PROFILES.get(application, _PROFILES["unknown"])


def paternity_unverifiable(*, hom_alt_informative: int, het_informative: int) -> bool:
    """Too few paternal-informative sites of both kinds for a paternity verdict."""
    return hom_alt_informative < MIN_PATERNITY_HOM_ALT_SITES and het_informative < MIN_PATERNITY_HET_SITES


def evaluate_paternity(
    father: str,
    *,
    hom_alt_transmitted: int,
    hom_alt_not_transmitted: int,
    het_transmitted: int,
    het_not_transmitted: int,
) -> PaternityCheck:
    """Paternity from the father's alleles in the plasma (see the constants above): his
    homozygous alleles must all be seen (90% passes, under 80% fails), his het ones half
    (35-65% passes, outside 25-75% fails). Too few sites of both kinds warns."""
    hom_informative = hom_alt_transmitted + hom_alt_not_transmitted
    het_informative = het_transmitted + het_not_transmitted
    statuses: list[Status] = []
    findings: list[str] = []
    if hom_informative >= MIN_PATERNITY_HOM_ALT_SITES:
        rate = hom_alt_transmitted / hom_informative
        findings.append(
            f"{hom_alt_transmitted} of {hom_informative} ({rate:.0%}) of his homozygous alleles seen"
        )
        if rate < PATERNITY_HOM_ALT_FAIL_RATE:
            statuses.append("fail")
        elif rate < PATERNITY_HOM_ALT_PASS_RATE:
            statuses.append("warn")
        else:
            statuses.append("pass")
    if het_informative >= MIN_PATERNITY_HET_SITES:
        rate = het_transmitted / het_informative
        findings.append(f"{het_transmitted} of {het_informative} ({rate:.0%}) of his het alleles seen (half expected)")
        low_pass, high_pass = PATERNITY_HET_PASS_RANGE
        low_fail, high_fail = PATERNITY_HET_FAIL_RANGE
        if rate < low_fail or rate > high_fail:
            statuses.append("fail")
        elif rate < low_pass or rate > high_pass:
            statuses.append("warn")
        else:
            statuses.append("pass")
    check = PaternityCheck(
        father=father,
        hom_alt_transmitted=hom_alt_transmitted,
        hom_alt_not_transmitted=hom_alt_not_transmitted,
        het_transmitted=het_transmitted,
        het_not_transmitted=het_not_transmitted,
        status="warn",
        message="",
    )
    if not statuses or paternity_unverifiable(hom_alt_informative=hom_informative, het_informative=het_informative):
        check.message = (
            f"Too few paternal-informative sites ({hom_informative} homozygous, {het_informative} het) "
            "to assess paternity."
        )
        return check
    check.status = _worst(statuses)
    metrics = "; ".join(findings)
    if check.status == "fail":
        check.message = (
            f"The father's alleles are largely absent from the plasma ({metrics}): another father, "
            "or a sample or file mixup."
        )
    elif check.status == "warn":
        check.message = f"Paternal transmission off its expectation ({metrics}); check the samples."
    else:
        check.message = f"Paternity supported ({metrics})."
    return check


def evaluate_fetal_sex(
    inferred: str, x_transmitted: int, x_not_transmitted: int, informative_sites: int
) -> FetalSexCheck:
    """Wrap the paternal-X-transmission fetal-sex call as a QC result."""
    metrics = f"{x_transmitted} paternal-X transmitted, {x_not_transmitted} absent of {informative_sites} sites"
    if inferred == "indeterminate":
        reason = (
            "too few informative paternal-X sites"
            if informative_sites < MIN_FETAL_SEX_SITES
            else "the father's X alleles are neither mostly present nor mostly absent (check paternity)"
        )
        return FetalSexCheck(inferred, x_transmitted, x_not_transmitted, informative_sites,
                             "warn", f"Fetal sex indeterminate — {reason} ({metrics}).")
    return FetalSexCheck(inferred, x_transmitted, x_not_transmitted, informative_sites,
                         "pass", f"Fetal sex appears {inferred}: paternal X "
                         f"{'transmitted' if inferred == 'female' else 'not transmitted'} ({metrics}).")


def evaluate_nipt_category_qc(category_counts: dict[int, int]) -> NiptCategoryQc:
    """QC the cfDNA category distribution: de-novo / paternal-absent excess and
    the maternal transmission rate (~50% for the true mother)."""
    counts = {int(k): int(v) for k, v in category_counts.items()}
    total = sum(counts.values())
    denovo = counts.get(1, 0)
    paternal_absent = counts.get(8, 0)
    maternal_informative = counts.get(2, 0) + counts.get(3, 0) + counts.get(4, 0)
    maternal_inherited = counts.get(3, 0) + counts.get(4, 0)
    rate = maternal_inherited / maternal_informative if maternal_informative else 0.0

    statuses: list[Status] = ["pass"]
    issues: list[str] = []

    if total:
        denovo_rate = denovo / total
        if denovo_rate >= NIPT_DENOVO_FAIL_RATE:
            statuses.append("fail")
            issues.append(f"de-novo (cat 1) excess {denovo}/{total} ({denovo_rate:.0%}) — artifacts/contamination")
        elif denovo_rate >= NIPT_DENOVO_WARN_RATE:
            statuses.append("warn")
            issues.append(f"elevated de-novo (cat 1) {denovo}/{total} ({denovo_rate:.0%})")

    if maternal_informative >= MIN_MATERNAL_TRANSMISSION_SITES:
        delta = abs(rate - 0.5)
        metrics = f"{maternal_inherited}/{maternal_informative} maternal-het sites"
        if delta >= MATERNAL_TRANSMISSION_FAIL_DELTA:
            statuses.append("fail")
            issues.append(f"maternal transmission {rate:.0%} far from 50% ({metrics}) — wrong mother or sample issue")
        elif delta >= MATERNAL_TRANSMISSION_WARN_DELTA:
            statuses.append("warn")
            issues.append(f"maternal transmission {rate:.0%} off 50% ({metrics})")

    status = _worst(statuses)
    if status == "pass":
        message = (
            f"Category distribution within expectation — maternal transmission {rate:.0%}, "
            f"{denovo} de-novo, {paternal_absent} paternal-absent."
        )
    else:
        message = "; ".join(issues) + "."
    return NiptCategoryQc(denovo, paternal_absent, maternal_informative, maternal_inherited,
                          rate, status, message)


def evaluate_sample_integrity(
    autosomal: dict[str, list[Genotype | None]],
    x_genotypes: dict[str, list[Genotype | None]],
    spec: PedigreeSpec,
    *,
    profile: QcProfile,
    genotype_source: str | None = None,
    paternity_check: PaternityCheck | None = None,
    fetal_sex_check: FetalSexCheck | None = None,
    category_qc_check: NiptCategoryQc | None = None,
    extra_sex_checks: list[SexCheck] | None = None,
    extra_notes: list[str] | None = None,
) -> SampleIntegrityReport:
    """Run the checks the application profile enables and roll up an overall status."""
    samples = sorted(autosomal)
    notes: list[str] = list(extra_notes or [])

    sex_checks = (
        [
            _evaluate_sex(s, _norm_sex(spec.recorded_sex.get(s)), x_genotypes.get(s))
            for s in samples
        ]
        if profile.run_sex
        else []
    )
    # Genotype-derived sex checks the profile doesn't run inline (e.g. NIPT parents
    # checked off-cycle from the cfDNA path).
    if extra_sex_checks:
        sex_checks = sex_checks + list(extra_sex_checks)

    # Build expected relationships from the pedigree, then assess every pair.
    expected: dict[tuple[str, str], str] = {}
    for child, parents in spec.parents_of.items():
        for parent in parents.values():
            if parent in autosomal and child in autosomal:
                expected[_pair_key(parent, child)] = "parent-child"
    for a, b in _sibling_pairs(spec.parents_of):
        if a in autosomal and b in autosomal:
            expected.setdefault(_pair_key(a, b), "sibling")

    # Pairs who are both parents of the same child are expected unrelated; keep
    # them so the matrix confirms "no consanguinity" (or flags it).
    coparent_pairs: set[tuple[str, str]] = set()
    for parents in spec.parents_of.values():
        pids = sorted({p for p in parents.values() if p in autosomal})
        for i in range(len(pids)):
            for j in range(i + 1, len(pids)):
                coparent_pairs.add((pids[i], pids[j]))

    relatedness_checks: list[RelatednessCheck] = []
    if profile.run_relatedness:
        for i, a in enumerate(samples):
            for b in samples[i + 1:]:
                pair = (a, b)
                is_coparent = pair in coparent_pairs
                exp = expected.get(pair, "unrelated")
                check = _relationship_check(a, b, exp, autosomal, coparents=is_coparent)
                # For a couple, or co-parents (consanguinity check), the "unrelated"
                # confirmation IS a wanted result, so keep it; elsewhere suppress the
                # noise of expected-unrelated passes and keep only asserted pairs or
                # surprising findings.
                if (
                    exp == "unrelated"
                    and check.status == "pass"
                    and profile.application != "couple"
                    and not is_coparent
                ):
                    continue
                relatedness_checks.append(check)

    mendelian_checks: list[MendelianCheck] = []
    if profile.run_mendelian:
        for child, parents in spec.parents_of.items():
            if child not in autosomal:
                continue
            mendelian = _mendelian_check(child, parents, autosomal)
            if mendelian is not None:
                mendelian_checks.append(mendelian)

    autosomal_sites = max((len(v) for v in autosomal.values()), default=0)
    genotype_checks_run = profile.run_relatedness or profile.run_mendelian
    if genotype_checks_run:
        if not autosomal:
            notes.append("No genotypes were available; QC could not run.")
        elif autosomal_sites < MIN_RELATEDNESS_SITES:
            notes.append(
                f"Only {autosomal_sites} autosomal sites were available; "
                "relatedness estimates may be unreliable."
            )

    all_statuses = (
        [c.status for c in sex_checks]
        + [c.status for c in relatedness_checks]
        + [c.status for c in mendelian_checks]
        + ([paternity_check.status] if paternity_check is not None else [])
        + ([fetal_sex_check.status] if fetal_sex_check is not None else [])
        + ([category_qc_check.status] if category_qc_check is not None else [])
        # A degraded run (e.g. genotypes/cfDNA analysis unavailable) surfaces as a
        # visible warning rather than a silent skip.
        + (["warn"] if extra_notes else [])
    )
    overall = _worst(all_statuses) if all_statuses else "skip"

    return SampleIntegrityReport(
        overall_status=overall,
        application=profile.application,
        application_label=profile.label,
        application_summary=profile.summary,
        genotype_source=genotype_source,
        sex_checks=sex_checks,
        relatedness_checks=relatedness_checks,
        mendelian_checks=mendelian_checks,
        paternity_check=paternity_check,
        fetal_sex_check=fetal_sex_check,
        category_qc_check=category_qc_check,
        autosomal_sites=autosomal_sites,
        notes=notes,
    )
