"""The phasing-QC check of a PGT embryo call at the region of interest: the thresholds, in one
place, and the verdict (REQ-PGT-005; TF-01 §3.3).

The owner decided on 2026-10-20 that an embryo call gets a clear warning when its phasing QC at
the ROI falls below a threshold: the call stays shown, and the analyst decides. So this is a
warning, never a gate: no call is withheld or changed, and no sign-out is refused on it.
``pgt_analysis`` gives each called embryo the verdict made here, the family page shows it
beside the call, and a sign-out freezes it, with the thresholds in force
(``pgt_analysis.RULES``), into the signed record.

The measures are those of the embryo's phasing QC at the ROI as the ROI marker review counts
them (``phased_marker_service.compute_phased_markers``, ``pgt_analysis._embryo_qc``); the
Mendelian error count is not checked apart from its rate:

* informative sites: the ROI's sites where both parents and the embryo have a phased genotype
  (for a son on chrX outside the PARs, whose one X is his mother's, she and he), the sites the
  Mendelian error rate is read on;
* the Mendelian error rate: the share of those sites whose genotype the embryo cannot have from
  these parents. A raised rate points to a sample swap, a wrong pedigree, contamination, or a
  deletion or uniparental segment at the locus, any of which can make a haplotype call wrong;
* the markers on each parental side the call reads: the ROI's sites where the homolog the
  embryo inherited from that parent is read, the evidence the call on that side rests on. Only
  the sides the call reads are checked (``pgt_embryo_calls.call_sides``): the side(s) a disease
  haplotype was resolved on, and for a son at chrX outside the PARs his mother's. A side the
  call does not read (the other parent's in a dominant family, a donor's) is not checked, so
  it cannot raise a warning about evidence the call does not use.

PROVISIONAL VALUES. The thresholds below are proposed by the developers, with the reasoning
given beside each; the laboratory director and QA are to confirm or change them before
clinical use, and the TF-10 validation (§3.2, sparse informative markers) is to test them.
The in-app reference (``/docs/reference/haplotype-segregation``, *Phasing QC at the ROI*) lists
them for the laboratory; a change here is a change to what the family page warns about and
what a signed record freezes.

Pure: no I/O.
"""

from __future__ import annotations

from typing import Sequence

from ..schemas import (
    PgtEmbryoQcFindingOut,
    PgtEmbryoQcOut,
    PgtEmbryoQcVerdictOut,
    PgtQcThresholdsOut,
)
from .pgt_embryo_calls import Origin

# The Mendelian error rate at the ROI above which a call is warned. 2% is where CoGA's
# genome-wide Sample QC starts to warn (``sample_integrity_qc.MENDEL_WARN_RATE``) and what the
# haplotype track already marks for a child in the region in view (``MENDEL_WARN_RATE`` in
# HaplotypePhasedTrack.tsx): above it a child's rate is read as a signal (a swap, a wrong
# pedigree), below it as genotyping or imputation noise. At exactly 2% the call passes, as the
# track has it.
MAX_MENDEL_RATE = 0.02

# The informative sites at the ROI below which a call is warned. 50 = 1 / MAX_MENDEL_RATE: with
# fewer sites a single Mendelian error already puts the rate above 2%, so the rate cannot tell
# one genotyping error from a swap; and with no error among 50 sites, a rate of about 6% or more
# is excluded with 95% confidence (rule of three, 3/50), near the 5% at which the Sample QC
# fails a child. The Sample QC's own floor for a genome-wide rate is 200 sites
# (``sample_integrity_qc.MIN_MENDEL_SITES``); at the ROI fewer sites can be had.
MIN_INFORMATIVE_SITES = 50

# The markers on each parental side the call reads below which a call is warned. The ESHRE PGT
# Consortium's good practice recommendations for monogenic disorders (Carvalho et al., Hum
# Reprod Open 2020, hoaa018) ask for at least six informative SNPs (or two STRs) on each side of
# the locus when the embryo is read from linked markers alone, as CoGA's haplotype call reads
# it. Two differences are for the laboratory director to weigh: CoGA counts the markers over the
# ROI as a whole, not on each flank of the locus, and it reads imputed low-pass genotypes, not
# the amplified loci with under 5% allele drop-out those recommendations assume (they ask for
# more markers where drop-out is higher).
MIN_MARKERS_PER_SIDE = 6

QC_THRESHOLDS = PgtQcThresholdsOut(
    min_informative_sites=MIN_INFORMATIVE_SITES,
    max_mendel_rate=MAX_MENDEL_RATE,
    min_markers_per_side=MIN_MARKERS_PER_SIDE,
)

_SIDES: tuple[Origin, Origin] = ("paternal", "maternal")


def embryo_qc_verdict(
    qc: PgtEmbryoQcOut | None,
    sides: Sequence[Origin],
    thresholds: PgtQcThresholdsOut | None = None,
) -> PgtEmbryoQcVerdictOut:
    """The embryo's phasing QC at the ROI checked against the thresholds.

    ``thresholds`` defaults to ``QC_THRESHOLDS``, the object the analysis states in its rules,
    so the record names the thresholds its verdicts were made against. ``sides`` are the
    parental sides the call reads; their markers are checked, the other side's are not.
    Without a QC (``None``: the ROI held more sites than the marker review reads, or the embryo
    is not recorded as the couple's child) the verdict is ``not_counted``: the call could not be
    checked, which is not a pass. Each measure outside its threshold is a finding, in a fixed
    order: the informative sites, the Mendelian error rate, then the markers on the father's and
    on the mother's side.
    """
    limits = thresholds or QC_THRESHOLDS
    checked = [side for side in _SIDES if side in sides]
    if qc is None:
        return PgtEmbryoQcVerdictOut(status="not_counted", sides=checked)
    findings: list[PgtEmbryoQcFindingOut] = []
    if qc.informative_sites < limits.min_informative_sites:
        findings.append(
            PgtEmbryoQcFindingOut(
                measure="informative_sites", value=qc.informative_sites, threshold=limits.min_informative_sites
            )
        )
    if qc.mendel_rate > limits.max_mendel_rate:
        findings.append(
            PgtEmbryoQcFindingOut(measure="mendel_rate", value=qc.mendel_rate, threshold=limits.max_mendel_rate)
        )
    for side, markers in (("paternal", qc.paternal_markers), ("maternal", qc.maternal_markers)):
        if side in checked and markers < limits.min_markers_per_side:
            findings.append(
                PgtEmbryoQcFindingOut(
                    measure="paternal_markers" if side == "paternal" else "maternal_markers",
                    value=markers,
                    threshold=limits.min_markers_per_side,
                )
            )
    return PgtEmbryoQcVerdictOut(status="warning" if findings else "pass", sides=checked, findings=findings)
