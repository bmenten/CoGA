"""Whether a structural variant removes a copy of what it spans, adds one, or neither.

One rule for every caller that asks: the SNV + SV second hit (``sv_gene_index_service``: a
copy loss in trans with a heterozygous SNV leaves the SNV the only copy) and the SV ranking
(``variant_prioritization``: a copy loss scores as a deletion, haploinsufficient in a
LoF-intolerant gene, and a copy gain as a duplication). The ranking matched exact type
names, so a ``DEL:ME``, a ``DUP:TANDEM`` and a CNV, whatever its copy number, scored as
neither, while the second-hit badge counted a ``DEL:ME`` and a CNV loss as copy losses
(CLIN-47). The SV list serves each SV with its change (``VariantOut.copy_change``), which the
genome, chromosome and Circos views draw it by (CLIN-54).

* A deletion, ``DEL`` with any subtype (``DEL:ME``), is a loss, and a duplication, ``DUP``
  with any subtype (``DUP:TANDEM``), a gain, whatever copy numbers they store. So are
  ``LOSS`` and ``CNV_LOSS``, and ``GAIN`` and ``CNV_GAIN``: not VCF's types, but types that
  name their direction, which the ranking has read so from the start.
* A CNV names no direction: its copy number (``calls.cn``) says it. It is a loss when a
  sample that carries it has fewer copies than that sample has there: two, or one for a
  male on chrX or chrY outside the PARs. Otherwise it is a gain when such a sample has
  more. A CNV that stores no copy number for its carriers may be a loss, and counts as one;
  one whose carriers all keep their copies is neither.
* Every other type (INS, INV, BND, TRA, ...) changes no copy number.
"""

from __future__ import annotations

from typing import Iterable, Literal

from .compound_het_phase import FamilyPedigree
from .genotypes import genotype_has_alt

CopyChange = Literal["loss", "gain"]
COPY_LOSS: CopyChange = "loss"
COPY_GAIN: CopyChange = "gain"

# The type of a CNV, which names no direction: its copy number says it.
COPY_NUMBER_SV_TYPE = "CNV"
# The types that name their direction, without their subtypes (``sv_kind``).
_LOSS_KINDS = frozenset({"DEL", "LOSS", "CNV_LOSS"})
_GAIN_KINDS = frozenset({"DUP", "GAIN", "CNV_GAIN"})
# The copies a sample has of what an SV spans, but where a male carries one.
_TWO_COPIES = 2

# One sample's call of an SV: the sample, its genotype and its copy number (None when the
# call states none).
SampleCall = tuple[str, str | None, int | None]


def sv_kind(sv_type: str | None) -> str:
    """The SV's type without its subtypes, in capitals: ``DEL:ME`` is ``DEL``."""
    return str(sv_type or "").strip().upper().split(":", 1)[0]


def copy_change(
    sv_type: str | None,
    calls: Iterable[SampleCall] = (),
    *,
    chrom: str | None = None,
    start: int | None = None,
    end: int | None = None,
    pedigree: FamilyPedigree | None = None,
) -> CopyChange | None:
    """``loss``, ``gain`` or None: the copy-number change of an SV of type ``sv_type`` (see
    the module docstring).

    ``calls`` are the SV's calls, each sample with its genotype and copy number; only a CNV
    reads them. ``chrom``, ``start`` and ``end`` place the SV, and ``pedigree`` names the
    males, who carry one copy of chrX and chrY outside the PARs: without it, every sample is
    read as having two copies.
    """
    kind = sv_kind(sv_type)
    if kind in _LOSS_KINDS:
        return COPY_LOSS
    if kind in _GAIN_KINDS:
        return COPY_GAIN
    if kind != COPY_NUMBER_SV_TYPE:
        return None
    stated = [
        (sample, int(copies))
        for sample, genotype, copies in calls
        if copies is not None and genotype_has_alt(str(genotype or ""))
    ]
    if not stated:
        return COPY_LOSS
    single_copy = (
        pedigree.single_copy_samples([(chrom, start), (chrom, end)])
        if pedigree is not None
        else frozenset()
    )

    def usual_copies(sample: str) -> int:
        return 1 if sample in single_copy else _TWO_COPIES

    if any(copies < usual_copies(sample) for sample, copies in stated):
        return COPY_LOSS
    if any(copies > usual_copies(sample) for sample, copies in stated):
        return COPY_GAIN
    return None
