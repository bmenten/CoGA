"""The phasing-QC check of a PGT embryo call at the region of interest: the thresholds, in one
place, and the verdict (REQ-PGT-005; TF-01 §3.3).

The owner decided on 2026-10-20 that an embryo call gets a clear warning when its phasing QC at
the ROI falls below a threshold: the call stays shown, and the analyst decides. So this is a
warning, never a gate: no call is withheld or changed, and no sign-out is refused on it,
but for the one gate below.
``pgt_analysis`` gives each called embryo the verdict made here, the family page shows it
beside the call, and a sign-out freezes it, with the thresholds in force
(``pgt_analysis.RULES``), into the signed record.

The measures are those of the embryo's phasing QC at the ROI as the ROI marker review counts
them (``phased_marker_service.compute_phased_markers``, ``pgt_analysis._embryo_qc``), over the
window the call reads: the ROI and ``pgt_embryo_calls.ROI_WINDOW_FLANK`` (1 Mb) on each side of
it. The owner decided on 2026-10-30 to count them there, and the markers on each flank apart: a
run of the PGT pipeline gives the variant itself as its ROI, in which no marker lies, so every
embryo was warned of 0 markers, while Mendelian errors or a missing parental allele near the ROI,
which weaken a call, went unseen. The Mendelian error count is not checked
apart from its rate:

* informative sites: the window's sites where both parents and the embryo have a phased genotype
  (for a son on chrX outside the PARs, whose one X is his mother's, she and he), the sites the
  Mendelian error rate is read on;
* the Mendelian error rate: the share of those sites whose genotype the embryo cannot have from
  these parents. A raised rate points to a sample swap, a wrong pedigree, contamination, or a
  deletion or uniparental segment at the locus, any of which can make a haplotype call wrong;
* the markers on each parental side the call reads, on each flank of the ROI: the sites left of
  the ROI (below its start) and right of it (above its end) where the homolog the embryo
  inherited from that parent is read, the evidence the call on that side rests on. The ROI's own
  sites count on neither flank. Only
  the sides the call reads are checked (``pgt_embryo_calls.call_sides``): the side(s) a disease
  haplotype was resolved on, and for a son at chrX outside the PARs his mother's. A side the
  call does not read (the other parent's in a dominant family, a donor's) is not checked, so
  it cannot raise a warning about evidence the call does not use.

One measure is a gate, not a warning: the markers of a side that mix both of that parent's
homologs (``mixed_homolog_sides``, the owner's decision of 2026-10-30). An embryo's haplotype
blocks show one homolog of each parent wherever it is; an embryo that carries both of one
parent's homologs at the ROI, as in a trisomy of that chromosome from a meiosis I error, gets
blocks just as clean, of one of them, and a call that it does not carry the other, which it
does. What shows it is the embryo's own markers: with one homolog they agree with it, or with
two either side of a crossover, at nearly every site; with both, imputation forced into two
copies assigns the sites to either homolog, and they mix. Their discordance
(``marker_discordance``) is the share of a side's markers off the best reading of the window as
one homolog, or two with one crossover between, so a crossover alone does not raise it. Above
``MAX_MARKER_DISCORDANCE`` on a side the call reads, the embryo is not called
(``pgt_analysis``: uninformative), whatever the blocks say: the analyst reads its copy number
and APCAD tracks, as for any aneuploidy (REQ-PGT-008). It is a safety net under that review,
not an aneuploidy detector: an isodisomic trisomy (two copies of one homolog) leaves the markers
in agreement, and a trisomy away from the ROI is not looked at.

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
    PgtQcFlank,
    PgtQcThresholdsOut,
)
from .pgt_embryo_calls import Origin

# The Mendelian error rate in the window above which a call is warned. 2% is where CoGA's
# genome-wide Sample QC starts to warn (``sample_integrity_qc.MENDEL_WARN_RATE``) and what the
# haplotype track already marks for a child in the region in view (``MENDEL_WARN_RATE`` in
# HaplotypePhasedTrack.tsx): above it a child's rate is read as a signal (a swap, a wrong
# pedigree), below it as genotyping or imputation noise. At exactly 2% the call passes, as the
# track has it.
MAX_MENDEL_RATE = 0.02

# The informative sites in the window below which a call is warned. 50 = 1 / MAX_MENDEL_RATE: with
# fewer sites a single Mendelian error already puts the rate above 2%, so the rate cannot tell
# one genotyping error from a swap; and with no error among 50 sites, a rate of about 6% or more
# is excluded with 95% confidence (rule of three, 3/50), near the 5% at which the Sample QC
# fails a child. The Sample QC's own floor for a genome-wide rate is 200 sites
# (``sample_integrity_qc.MIN_MENDEL_SITES``); around the ROI fewer sites can be had.
MIN_INFORMATIVE_SITES = 50

# The markers on each flank of the ROI, on each parental side the call reads, below which a call
# is warned. The ESHRE PGT Consortium's good practice recommendations for monogenic disorders
# (Carvalho et al., Hum Reprod Open 2020, hoaa018) ask for at least six informative SNPs (or two
# STRs) on each side of the locus when the embryo is read from linked markers alone, as CoGA's
# haplotype call reads it. One difference is for the laboratory director to weigh: CoGA reads
# imputed low-pass genotypes, not the amplified loci with under 5% allele drop-out those
# recommendations assume (they ask for more markers where drop-out is higher).
MIN_MARKERS_PER_FLANK = 6

# The discordance of a side's markers above which the embryo is taken to carry both of that
# parent's homologs, and not called. Measured over 2 Mb windows on every autosome of a real PGT
# family's embryos (not in the repository): an embryo with one homolog of a parent stayed well
# under 10% wherever it had markers, a crossover in the window included, imputation runs and
# all; a chromosome that carried both of the mother's homologs (a meiotic trisomy) read above
# 10% in nearly every window, the father's side of the same chromosome near 0. Below the
# minimum number of markers a side is not judged: a few discordant sites are noise.
MAX_MARKER_DISCORDANCE = 0.10
MIN_DISCORDANCE_MARKERS = 20

QC_THRESHOLDS = PgtQcThresholdsOut(
    min_informative_sites=MIN_INFORMATIVE_SITES,
    max_mendel_rate=MAX_MENDEL_RATE,
    min_markers_per_flank=MIN_MARKERS_PER_FLANK,
    max_marker_discordance=MAX_MARKER_DISCORDANCE,
    min_discordance_markers=MIN_DISCORDANCE_MARKERS,
)

_SIDES: tuple[Origin, Origin] = ("paternal", "maternal")
_FLANKS: tuple[PgtQcFlank, PgtQcFlank] = ("left", "right")


def embryo_qc_verdict(
    qc: PgtEmbryoQcOut | None,
    sides: Sequence[Origin],
    thresholds: PgtQcThresholdsOut | None = None,
) -> PgtEmbryoQcVerdictOut:
    """The embryo's phasing QC at the ROI checked against the thresholds.

    ``thresholds`` defaults to ``QC_THRESHOLDS``, the object the analysis states in its rules,
    so the record names the thresholds its verdicts were made against. ``sides`` are the
    parental sides the call reads; their markers are checked, the other side's are not.
    Without a QC (``None``: the window held more sites than the marker review reads, or the
    embryo is not recorded as the couple's child) the verdict is ``not_counted``: the call could
    not be checked, which is not a pass. Each measure outside its threshold is a finding, in a
    fixed order: the informative sites, the Mendelian error rate, then the markers on the
    father's and on the mother's side, each left of the ROI and then right of it.
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
    flank_markers: dict[tuple[Origin, PgtQcFlank], int] = {
        ("paternal", "left"): qc.paternal_markers_left,
        ("paternal", "right"): qc.paternal_markers_right,
        ("maternal", "left"): qc.maternal_markers_left,
        ("maternal", "right"): qc.maternal_markers_right,
    }
    for side in checked:
        for flank in _FLANKS:
            markers = flank_markers[(side, flank)]
            if markers < limits.min_markers_per_flank:
                findings.append(
                    PgtEmbryoQcFindingOut(
                        measure="paternal_markers" if side == "paternal" else "maternal_markers",
                        value=markers,
                        threshold=limits.min_markers_per_flank,
                        flank=flank,
                    )
                )
    return PgtEmbryoQcVerdictOut(status="warning" if findings else "pass", sides=checked, findings=findings)


def marker_discordance(homologs: Sequence[int]) -> float:
    """The share of a side's markers, in position order, that disagree with the best reading of
    them as one homolog, or as two with one crossover between: 0 for an embryo that carries one
    homolog of the parent (a crossover in the window included), up to one half where its
    markers mix both homologs evenly. 0 without markers."""
    total = len(homologs)
    if not total:
        return 0.0
    ones = [0]
    for homolog in homologs:
        ones.append(ones[-1] + (1 if homolog else 0))
    best = total
    for split in range(total + 1):
        left_ones = ones[split]
        right_ones = ones[total] - left_ones
        off = min(left_ones, split - left_ones) + min(right_ones, total - split - right_ones)
        best = min(best, off)
    return best / total


def mixed_homolog_sides(
    qc: PgtEmbryoQcOut | None,
    sides: Sequence[Origin],
    thresholds: PgtQcThresholdsOut | None = None,
) -> list[Origin]:
    """The parental sides the call reads whose markers at the ROI mix both of that parent's
    homologs: a discordance above ``max_marker_discordance`` over at least
    ``min_discordance_markers`` markers. Without a QC none is judged."""
    limits = thresholds or QC_THRESHOLDS
    if qc is None:
        return []
    measured: dict[Origin, tuple[int, float]] = {
        "paternal": (qc.paternal_markers, qc.paternal_discordance),
        "maternal": (qc.maternal_markers, qc.maternal_discordance),
    }
    return [
        side
        for side in _SIDES
        if side in sides
        and measured[side][0] >= limits.min_discordance_markers
        and measured[side][1] > limits.max_marker_discordance
    ]
