"""Cis or trans for two hits in one gene, read from the family.

Two heterozygous hits in one gene explain a recessive disorder only when they sit on
different copies of the gene (in trans). This module decides that from the family's
genotypes, for the small-variant compound-het pairs (``clickhouse_variant_queries``) and
for the SNV + structural-variant second hit (``sv_gene_index_service``) alike, so the same
family evidence gets the same verdict in both. Read-backed phasing, where the calls carry
it, is direct evidence and is applied by the callers first. Both read a phased call with
:func:`phased_alt_haplotype`, so a call one of them cannot place on a haplotype, such as
a half call (``.|1``), is not placed by the other either. The rest of this module answers
when the reads do not.

For a candidate in which every affected individual carries both hits:

* ``cis`` when an unaffected individual carries both hits. Either the two sit on one copy
  in this family, or that individual has the candidate genotype without the disease;
  either way the pair does not explain the phenotype.
* Otherwise each affected individual with parents in the pedigree is traced through them,
  hit by hit. A parent who confidently lacks a hit cannot have passed it on, so the
  child's copy of that hit came from the other parent (named or not). Failing that, a hit
  is credited to the only parent known to carry it. Hits traced to different parents are
  ``trans``. Hits traced to the same parent are ``cis``, but only when both parents are
  genotyped for both hits: a cis verdict removes a candidate, so it is not drawn from one
  parent alone.
* ``unknown`` otherwise. A relative who carries neither hit, or one of them, is evidence
  only as the affected individual's parent: an unaffected sibling carrying neither says
  nothing about which copy each hit is on. Neither does a hit that both parents lack
  (de novo, or a missed call), which could have arisen on either copy.

"Confidently lacks" is a genotyped reference call, with at least
``CONFIDENT_REFERENCE_MIN_DP`` reads when the call reports its depth; a missing call or a
no-call is not absence. A male at a position where he carries a single copy (chrX or chrY
outside the PARs) has no second parental copy to trace, so he is not traced there.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Collection, Iterable, Literal, Mapping, Sequence

from .genotypes import ALT_CLASSES, HOM_REF, classify_genotype
from .sex_chromosomes import hemizygous_chromosome

Carriage = Literal["carrier", "non_carrier", "unknown"]
CARRIER: Carriage = "carrier"
NON_CARRIER: Carriage = "non_carrier"
UNKNOWN_CARRIAGE: Carriage = "unknown"

Phase = Literal["trans", "cis", "unknown"]
PHASE_TRANS: Phase = "trans"
PHASE_CIS: Phase = "cis"
PHASE_UNKNOWN: Phase = "unknown"

# How a verdict was reached, as the API reports it: read-backed phasing (one phase set) or
# the family's genotypes (this module).
PHASE_EVIDENCE_READ = "read"
PHASE_EVIDENCE_SEGREGATION = "segregation"

# Reads a reference call needs before it says "this parent does not carry the allele".
# The de novo check asks the same of a parent's reference call.
CONFIDENT_REFERENCE_MIN_DP = 8

Locus = tuple[str | None, int | None]


def phased_alt_haplotype(gt: str | None) -> int | None:
    """Which haplotype carries the alt in a phased het call: 0, 1, or None.

    ``0|1`` -> 1 and ``1|0`` -> 0. None when the call cannot place a single alt on one
    haplotype: unphased (``0/1``), homozygous, no-call, a half call with one allele missing
    (``.|1``, ``1|.``: the missing allele may be an alt as well), or multi-allelic with an
    alt on both haplotypes (``1|2``), where "the" alt is ambiguous.
    """
    text = str(gt or "").strip()
    if "|" not in text:
        return None
    alleles = text.split("|")
    if len(alleles) != 2:
        return None
    left, right = (allele.strip() for allele in alleles)
    if left in ("", ".") or right in ("", "."):
        return None
    left_is_alt = left != "0"
    right_is_alt = right != "0"
    if left_is_alt == right_is_alt:
        return None
    return 0 if left_is_alt else 1


def small_variant_carriage(gt: str | None, dp: int | None = None) -> Carriage:
    """Whether a small-variant call carries the ALT allele, lacks it, or cannot say."""
    genotype_class = classify_genotype(gt)
    if genotype_class in ALT_CLASSES:
        return CARRIER
    if genotype_class == HOM_REF and (dp is None or dp >= CONFIDENT_REFERENCE_MIN_DP):
        return NON_CARRIER
    return UNKNOWN_CARRIAGE


@dataclass(frozen=True, slots=True)
class FamilyPedigree:
    """What tracing hits through the parents needs from the family."""

    # Each child's parents, by sample name, as the pedigree records them.
    parents_of: Mapping[str, frozenset[str]] = field(default_factory=dict)
    males: frozenset[str] = frozenset()
    assembly_name: str | None = None

    def single_copy_samples(self, loci: Iterable[Locus]) -> frozenset[str]:
        """The males who carry a single copy at any of these loci (chrX/chrY outside the PARs)."""
        if not self.males:
            return frozenset()
        for chrom, position in loci:
            if chrom is None or position is None:
                continue
            if hemizygous_chromosome(self.assembly_name, chrom, int(position)) is not None:
                return self.males
        return frozenset()


def _passed_on_by(first_parent: Carriage, second_parent: Carriage) -> int | None:
    """Which parent's copy carries the child's allele: 0, 1, or None when it cannot be told.

    The second parent may be one the pedigree does not name (``UNKNOWN_CARRIAGE``)."""
    if first_parent == NON_CARRIER and second_parent == NON_CARRIER:
        return None  # neither parent has it: de novo, or a missed call
    if first_parent == NON_CARRIER:
        return 1
    if second_parent == NON_CARRIER:
        return 0
    if first_parent == CARRIER and second_parent == UNKNOWN_CARRIAGE:
        return 0
    if second_parent == CARRIER and first_parent == UNKNOWN_CARRIAGE:
        return 1
    return None  # both carry it, or neither is genotyped


def _traced_through_parents(
    parents: Collection[str],
    first: Callable[[str], Carriage],
    second: Callable[[str], Carriage],
) -> Phase:
    if not parents or len(parents) > 2:
        return PHASE_UNKNOWN  # no parents, or a pedigree error, not evidence
    calls = [(first(parent), second(parent)) for parent in sorted(parents)]
    if len(calls) == 1:
        calls.append((UNKNOWN_CARRIAGE, UNKNOWN_CARRIAGE))  # the parent the pedigree does not name
    (first_a, second_a), (first_b, second_b) = calls
    first_from = _passed_on_by(first_a, first_b)
    second_from = _passed_on_by(second_a, second_b)
    if first_from is None or second_from is None:
        return PHASE_UNKNOWN
    if first_from != second_from:
        return PHASE_TRANS
    if UNKNOWN_CARRIAGE in (first_a, second_a, first_b, second_b):
        return PHASE_UNKNOWN
    return PHASE_CIS


def segregation_phase(
    *,
    affected: Iterable[str],
    unaffected: Iterable[str],
    first: Callable[[str], Carriage],
    second: Callable[[str], Carriage],
    pedigree: FamilyPedigree | None = None,
    loci: Sequence[Locus] = (),
) -> Phase:
    """The phase of two hits, from the family (see the module docstring).

    ``first`` and ``second`` give each sample's carriage of the two hits. The caller has
    already established that every affected sample carries both. ``loci`` are the hits'
    positions, which decide where a male carries one copy.
    """
    if any(first(sample) == CARRIER and second(sample) == CARRIER for sample in unaffected):
        return PHASE_CIS
    if pedigree is None:
        return PHASE_UNKNOWN
    single_copy = pedigree.single_copy_samples(loci)
    verdicts = {
        _traced_through_parents(pedigree.parents_of.get(child, frozenset()), first, second)
        for child in affected
        if child not in single_copy
    }
    # A child with an intact copy rules the pair out as the explanation for all of them.
    if PHASE_CIS in verdicts:
        return PHASE_CIS
    if PHASE_TRANS in verdicts:
        return PHASE_TRANS
    return PHASE_UNKNOWN
