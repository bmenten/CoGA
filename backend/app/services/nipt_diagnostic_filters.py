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
  population frequency is rare, unless it recurs in ``cohort_common_min_samples`` (3) cfDNA
  samples of the assay or more, this one included: then it is an unannotated common allele
  (a paralogue, a VNTR, a population variant missing from the annotation).
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
from typing import Any, Iterable

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
    conflicting assertion, and in how many other cfDNA samples of the assay it is called."""

    max_population_af: float | None
    impact: str | None
    spliceai_max: float | None
    clinvar_pathogenic: bool
    other_cfdna_carriers: int = 0


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


def is_cohort_common(annotation: DiagnosticAnnotation, criteria: NiptDiagnosticCriteria) -> bool:
    """An allele without a population frequency that recurs in ``cohort_common_min_samples``
    cfDNA samples of the assay or more, this one included."""
    return (
        annotation.max_population_af is None
        and annotation.other_cfdna_carriers + 1 >= criteria.cohort_common_min_samples
    )


def clinvar_override(annotation: DiagnosticAnnotation, criteria: NiptDiagnosticCriteria) -> bool:
    """ClinVar pathogenic, at a population frequency of at most ``clinvar_max_af``."""
    af = annotation.max_population_af
    return annotation.clinvar_pathogenic and (af is None or af <= criteria.clinvar_max_af)


def _rare(annotation: DiagnosticAnnotation, criteria: NiptDiagnosticCriteria, ceiling: float) -> bool:
    af = annotation.max_population_af
    if af is None:
        return not is_cohort_common(annotation, criteria)
    return af <= ceiling


def is_rare_dominant(annotation: DiagnosticAnnotation, criteria: NiptDiagnosticCriteria) -> bool:
    return _rare(annotation, criteria, criteria.rare_max_af_dominant)


def is_rare_recessive(annotation: DiagnosticAnnotation, criteria: NiptDiagnosticCriteria) -> bool:
    return _rare(annotation, criteria, criteria.rare_max_af_recessive) or clinvar_override(annotation, criteria)


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


def recessive_failures(annotation: DiagnosticAnnotation, criteria: NiptDiagnosticCriteria) -> list[str]:
    """Why an allele is left out of the recessive pairing; [] when it qualifies."""
    failures = []
    if not is_rare_recessive(annotation, criteria):
        failures.append("not_rare")
    if not has_protein_effect(annotation, criteria):
        failures.append("no_protein_effect")
    return failures
