"""Monogenic NIPT: the locked diagnostic criteria (REQ-NIPT-020), pure and without I/O.

The R validation (NIPT-M v0.7.3, its blinded diagnostic analysis, "step 15") decides which of a
pregnancy's variants are left for further investigation after the technical filters: those that
are rare and change the protein. These are its criteria, frozen here with their version; the
NIPT report lists the candidates they leave in each view, and a sign-out freezes the criteria
and the list (the owner's decision of 2026-11-25). The NIPT page's own filters stay adjustable
for exploring.

* **Rare.** The highest population allele frequency CoGA holds for the allele (gnomAD, its exomes
  and genomes, popmax, TopMed: the R pipeline reads VEP's MAX_AF) at most
  ``rare_max_af_dominant`` (0.1 %) for a de novo, paternal or maternal candidate, at most
  ``rare_max_af_recessive`` (1 %) for an allele of the recessive pairing. An allele without a
  population frequency is rare, unless it recurs in ``cohort_common_min_samples`` (3) families
  of the assay or more: then it is an unannotated common allele (a paralogue, a VNTR, a
  population variant missing from the annotation). As the R validation counts them
  (``is_cohort_common``): an allele read off the plasma, by the plasmas that show it; the
  father's allele of the recessive pairing, by the plasmas that show it or the fathers that
  carry it, whichever are more, this family's each counted only when it does.
* **Protein effect.** A HIGH or MODERATE impact, or a SpliceAI delta score of 0.2 or more.
* **ClinVar.** A ClinVar record that calls the allele pathogenic or likely pathogenic, with no
  benign or conflicting assertion, and a population frequency of at most ``clinvar_max_af``
  (5 %), passes the effect filter and counts as rare for the recessive pairing (carrier
  frequencies of 1-5 %: GJB2 c.35delG, CFTR). A de novo or dominant candidate must still be rare
  itself; the R validation found the wider override inflated those lists. Such a de novo
  candidate is exempt from the rule that it be in no other cfDNA sample (a recurrent pathogenic
  de novo, e.g. FGFR3 c.1138G>A).
* **Repeat context.** A de novo indel in a repeat (Mutect2 RPA of ``repeat_min_units`` (3) or
  more, or an inserted or deleted sequence that is itself a tandem repeat of a unit of at most
  6 bases) is a low-confidence candidate: most such candidates were artefacts.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Literal

NIPT_DIAGNOSTIC_CRITERIA_REFERENCE = "R NIPT-M v0.7.3 diagnostic criteria (blinded analysis, step 15)"

_TOKEN_RE = re.compile(r"[a-z]+")


@dataclass(frozen=True, slots=True)
class NiptDiagnosticCriteria:
    """The locked criteria and the R validation they come from."""

    rare_max_af_dominant: float = 0.001
    rare_max_af_recessive: float = 0.01
    effect_impacts: tuple[str, ...] = ("HIGH", "MODERATE")
    min_spliceai: float = 0.2
    clinvar_max_af: float = 0.05
    cohort_common_min_samples: int = 3
    repeat_min_units: float = 3.0
    reference: str = NIPT_DIAGNOSTIC_CRITERIA_REFERENCE

    def frozen(self) -> dict[str, Any]:
        """What a signed record holds of them."""
        values = asdict(self)
        values["effect_impacts"] = list(self.effect_impacts)
        return values


@dataclass(frozen=True, slots=True)
class DiagnosticAnnotation:
    """What the criteria read of a variant: its highest population allele frequency, most severe
    impact, highest SpliceAI delta score, whether ClinVar calls it pathogenic without a benign or
    conflicting assertion, in how many other cfDNA samples of the assay it is called and in how
    many other families of the assay the father carries it, and whether this family's plasma
    shows it and its father carries it."""

    max_population_af: float | None
    impact: str | None
    spliceai_max: float | None
    clinvar_pathogenic: bool
    other_cfdna_carriers: int = 0
    other_fathers: int = 0
    plasma_calls: bool = True
    father_carries: bool = False


# Whose allele a count reads: one read off the plasma (a de novo, paternal or maternal candidate,
# the mother's allele of the recessive pairing), or the father's allele of the recessive pairing.
CohortSide = Literal["plasma", "father"]


def cohort_count(annotation: DiagnosticAnnotation, side: CohortSide = "plasma") -> int:
    """In how many families of the assay the allele recurs, as the R validation counts it: the
    plasmas that show it; for the father's allele, those or the fathers that carry it, whichever
    are more. This family's plasma and father each count only when they show or carry it."""
    plasmas = annotation.other_cfdna_carriers + int(annotation.plasma_calls)
    if side == "plasma":
        return plasmas
    return max(plasmas, annotation.other_fathers + int(annotation.father_carries))


def clinvar_strictly_pathogenic(values: Iterable[str | None]) -> bool:
    """Whether ClinVar calls the allele pathogenic or likely pathogenic and asserts nothing
    benign or conflicting, over every significance its annotations hold (the R pipeline's rule;
    stricter than ``variant_prioritization.clinvar_may_assert_pathogenic``, which also reads a
    conflict as possibly pathogenic)."""
    pathogenic = False
    for value in values:
        text = (value or "").strip().lower()
        if not text:
            continue
        if "conflict" in text or "benign" in _TOKEN_RE.findall(text):
            return False
        if "pathogenic" in _TOKEN_RE.findall(text):
            pathogenic = True
    return pathogenic


def is_cohort_common(
    annotation: DiagnosticAnnotation, criteria: NiptDiagnosticCriteria, side: CohortSide = "plasma"
) -> bool:
    """An allele without a population frequency that recurs in ``cohort_common_min_samples``
    families of the assay or more (``cohort_count``)."""
    return (
        annotation.max_population_af is None
        and cohort_count(annotation, side) >= criteria.cohort_common_min_samples
    )


def clinvar_override(annotation: DiagnosticAnnotation, criteria: NiptDiagnosticCriteria) -> bool:
    """ClinVar pathogenic, at a population frequency of at most ``clinvar_max_af``."""
    af = annotation.max_population_af
    return annotation.clinvar_pathogenic and (af is None or af <= criteria.clinvar_max_af)


def _rare(
    annotation: DiagnosticAnnotation, criteria: NiptDiagnosticCriteria, ceiling: float, side: CohortSide
) -> bool:
    af = annotation.max_population_af
    if af is None:
        return not is_cohort_common(annotation, criteria, side)
    return af <= ceiling


def is_rare_dominant(annotation: DiagnosticAnnotation, criteria: NiptDiagnosticCriteria) -> bool:
    return _rare(annotation, criteria, criteria.rare_max_af_dominant, "plasma")


def is_rare_recessive(
    annotation: DiagnosticAnnotation, criteria: NiptDiagnosticCriteria, side: CohortSide = "plasma"
) -> bool:
    return _rare(annotation, criteria, criteria.rare_max_af_recessive, side) or clinvar_override(
        annotation, criteria
    )


def has_protein_effect(annotation: DiagnosticAnnotation, criteria: NiptDiagnosticCriteria) -> bool:
    impact = (annotation.impact or "").upper()
    spliceai = annotation.spliceai_max
    return (
        impact in criteria.effect_impacts
        or (spliceai is not None and spliceai >= criteria.min_spliceai)
        or clinvar_override(annotation, criteria)
    )


def is_periodic(sequence: str, *, max_unit: int = 6, min_length: int = 4) -> bool:
    """Whether ``sequence`` is a tandem repeat of a unit of at most ``max_unit`` bases (at least
    ``min_length`` bases long): ``CAGCAG``, ``AAAA``."""
    length = len(sequence)
    if length < min_length:
        return False
    for unit in range(1, min(max_unit, length // 2) + 1):
        motif = sequence[:unit]
        if (motif * (length // unit + 1))[:length] == sequence:
            return True
    return False


def is_repeat_context_indel(
    ref: str, alt: str, repeat_units: float | None, criteria: NiptDiagnosticCriteria
) -> bool:
    """An indel in a repeat: the caller's repeat-unit count (Mutect2 RPA) of
    ``repeat_min_units`` or more, or an inserted or deleted sequence that is itself periodic."""
    ref = (ref or "").upper()
    alt = (alt or "").upper()
    if len(ref) == len(alt):
        return False
    changed = ref[len(alt):] if len(ref) > len(alt) else alt[len(ref):]
    return (repeat_units is not None and repeat_units >= criteria.repeat_min_units) or is_periodic(changed)


def dominant_failures(annotation: DiagnosticAnnotation, criteria: NiptDiagnosticCriteria) -> list[str]:
    """Why a de novo, paternal or maternal candidate is left out: ``not_rare``,
    ``no_protein_effect``; [] when it is a diagnostic candidate."""
    failures = []
    if not is_rare_dominant(annotation, criteria):
        failures.append("not_rare")
    if not has_protein_effect(annotation, criteria):
        failures.append("no_protein_effect")
    return failures


def recessive_failures(
    annotation: DiagnosticAnnotation, criteria: NiptDiagnosticCriteria, side: CohortSide = "plasma"
) -> list[str]:
    """Why an allele is left out of the recessive pairing as the mother's (``plasma``) or the
    father's (``father``) allele; [] when it qualifies."""
    failures = []
    if not is_rare_recessive(annotation, criteria, side):
        failures.append("not_rare")
    if not has_protein_effect(annotation, criteria):
        failures.append("no_protein_effect")
    return failures
