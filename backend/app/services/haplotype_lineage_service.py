"""Pedigree-aware haplotype lineage colouring.

The stored haplotype blocks (built at upload time) only make sense for the index
*nuclear family* — a father, a mother, and their direct children/embryos. There,
the four founder homologs get a stable colour:

    father homolog 0/1  -> dark / light blue   (paternal)
    mother homolog 0/1  -> dark / light green  (maternal)

and every child's inherited paternal (hap1) / maternal (hap2) homolog is coloured
to match the founder it came from. That labelling is grounded by the Mendelian
trio phasing done in ``variant_upload_service``.

Anyone *outside* that nuclear family (a grandparent, an aunt/uncle, a cousin) is
not part of the trio phasing, so their stored blocks are meaningless — the upload
pipeline treats every non-parent as a "child of the proband's parents" and runs
reverse-Mendelian transmission on them, producing junk. Worse, the flat role
model reuses ``mother``/``father`` for *any* parent, so a paternal grandmother is
stored with ``role = "mother"`` and the role-based colourer paints both her
homologs green.

This module fixes both problems by recomputing the colour of every relative from
the *raw phased genotypes*, propagating founder identity outward through the
pedigree:

  * Start from the nuclear core (founders + their children), already coloured.
  * Walk the pedigree graph along parent-child edges (BFS). For each relative
    reached from an already-coloured member, identity-by-descent (IBD) match the
    relative's two homologs against that member's two homologs. The homolog they
    share inherits the member's colour for that homolog; the relative's other
    homolog is *untransmitted* and is greyed out.

So a paternal grandmother gets exactly one homolog coloured (dark or light blue —
whichever the affected father shares with her) and the other grey, which is what
makes it possible to read off *which* paternal haplotype carries the dominant
disease allele.

The functions here are deliberately pure (no DB/IO): they take the pedigree rows,
the stored segments, and the raw phased genotype rows, and return lineage-tagged
segments. The callers in ``bed_service`` do the fetching.
"""
from __future__ import annotations

import bisect
import logging
from dataclasses import dataclass, field
from typing import Any, Collection, Iterable

logger = logging.getLogger(__name__)

# Lineage tags written onto each haplotype lane. The frontend maps:
#   paternal -> blue palette (shade by hap value 0/1)
#   maternal -> green palette (shade by hap value 0/1)
#   untransmitted / unknown -> grey
PATERNAL = "paternal"
MATERNAL = "maternal"
UNTRANSMITTED = "untransmitted"
UNKNOWN = "unknown"

# A parent and child share one whole haplotype, so on the side that did NOT
# recombine to connect them, one homolog is present in the other individual at
# essentially every informative site (consistency ~1.0) while the unshared homolog
# is only there by chance (~0.5). We confirm the relationship — and which homolog
# is shared — from that clearly-resolved side. Crucially this stays true even when
# the OTHER side recombined mid-chromosome (its homologs then split ~50/50); the
# per-block pin segmentation handles that side. Demanding a single genome-stable
# pair instead would wrongly drop a relative on chromosomes where their shared
# homolog crosses over (the original two-grey bug).
MIN_SHARED_CONSISTENCY = 0.90
# Need at least this many informative (distinguishing) sites to trust a match.
MIN_INFORMATIVE_SITES = 10
# The chosen shared homolog must out-score its sibling homolog by at least this
# margin. On het-rich imputed data a biallelic double-het site carries no
# transmission direction, so both homologs can score high by chance; a true
# parent-child share is asymmetric (one homolog ~1.0, the other ~0.5). Demanding
# a clear margin rejects unrelated / mislabelled pairs that score symmetrically
# high (the false-confirm failure mode), the way PLINK/Haplarithmisis restrict to
# informative sites and look for a directional signal.
MIN_HOMOLOG_MARGIN = 0.30
# A handful of informative sites smeared over a huge span carry no real linkage
# signal. Require at least this many informative (opposite-zygosity) sites per
# megabase of the matched span before trusting the share — a per-Mb density floor.
MIN_INFORMATIVE_PER_MB = 0.5

# Recombination smoothing. A relative's shared homolog (or the anchor's, for a
# downstream relative) switches at meiotic crossovers; isolated single-site
# disagreements are imputation/phasing noise, not crossovers. A switch is only
# committed once a run of contradicting pins is both long enough and wide enough,
# matching the segregation-block builder in variant_upload_service so the
# recombination boundaries line up with the children's tracks.
LINEAGE_SWITCH_MIN_MARKERS = 50
LINEAGE_SWITCH_MIN_SPAN = 500_000


# Autosomes after stripping any "chr" prefix. The diploid-autosomal IBD logic
# here assumes two homologs at every site; sex chromosomes (hemizygous X in males,
# Y) and the mitochondrion break that assumption, so relatives on them are left
# grey rather than mis-coloured.
_AUTOSOMES = frozenset(str(number) for number in range(1, 23))


def _normalize_role(role: str | None) -> str:
    return str(role or "").strip().lower()


def _is_autosome(chrom: Any) -> bool:
    """True only for chromosomes 1..22 (after stripping any ``chr`` prefix)."""
    return str(chrom or "").strip().lower().removeprefix("chr") in _AUTOSOMES


def _allele_int(allele: str) -> int | None:
    return int(allele) if allele.isdigit() else None


def _phased_alleles(gt: str | None) -> tuple[int, int] | None:
    """Parse a phased ``a|b`` genotype into integer alleles, or ``None`` if it is
    unphased / missing / multi-allelic-with-dots."""
    if not gt or "|" not in gt:
        return None
    left, right = gt.split("|", 1)
    a = _allele_int(left)
    b = _allele_int(right)
    if a is None or b is None:
        return None
    return a, b


@dataclass(slots=True)
class Pedigree:
    """Minimal pedigree graph derived from family member + relationship rows."""

    roles: dict[str, str]
    # child sample name -> {"father": name, "mother": name} (parents by role_a)
    parents_of: dict[str, dict[str, str]]
    # parent sample name -> set of child sample names
    children_of: dict[str, set[str]]
    # sample name -> the members it is related to by an unknown degree (``relative`` edges)
    relatives_of: dict[str, set[str]] = field(default_factory=dict)

    def parents(self, name: str) -> set[str]:
        return set(self.parents_of.get(name, {}).values())

    def neighbors(self, name: str) -> set[str]:
        """All pedigree neighbours reachable by a single parent-child edge."""
        return self.parents(name) | self.children_of.get(name, set())

    def relatives(self, name: str) -> set[str]:
        """The members ``name`` is related to by an unknown degree."""
        return set(self.relatives_of.get(name, set()))


def build_pedigree(
    sample_rows: Iterable[dict[str, Any]],
    relationship_rows: Iterable[dict[str, Any]],
) -> Pedigree:
    roles: dict[str, str] = {}
    for row in sample_rows:
        name = str(row.get("sample_id") or "")
        if name:
            roles[name] = _normalize_role(row.get("role"))
    parents_of: dict[str, dict[str, str]] = {}
    children_of: dict[str, set[str]] = {}
    relatives_of: dict[str, set[str]] = {}
    for rel in relationship_rows:
        if str(rel.get("relationship_type")) == "relative":
            one, other = str(rel.get("sample_id_a") or ""), str(rel.get("sample_id_b") or "")
            if one and other and one != other:
                relatives_of.setdefault(one, set()).add(other)
                relatives_of.setdefault(other, set()).add(one)
            continue
        if str(rel.get("relationship_type")) != "parent_child":
            continue
        parent = str(rel.get("sample_id_a") or "")
        child = str(rel.get("sample_id_b") or "")
        if not parent or not child or parent == child:
            continue
        role_a = _normalize_role(rel.get("role_a"))
        side = "father" if role_a == "father" else "mother" if role_a == "mother" else role_a or "parent"
        parents_of.setdefault(child, {})[side] = parent
        children_of.setdefault(parent, set()).add(child)
    return Pedigree(roles=roles, parents_of=parents_of, children_of=children_of, relatives_of=relatives_of)


@dataclass(slots=True)
class NuclearCore:
    father: str | None
    mother: str | None
    children: set[str]
    members: set[str] = field(default_factory=set)


def identify_core(pedigree: Pedigree) -> NuclearCore:
    """Identify the index nuclear family.

    PGT classifies the EMBRYOS, so the index parents are the embryos' parents. This
    anchors on the youngest generation and (a) avoids mistaking grandparents for the
    index couple, and (b) supports SINGLE-PARENT (donor) families where the embryos
    have only one recorded parent — the other side is a donor/unknown and is left
    ``None`` (its lane is greyed downstream). Families with no embryos fall back to
    the couple sharing the most children.
    """
    embryos = sorted(name for name, role in pedigree.roles.items() if role == "embryo")
    if embryos:
        fathers_of = {pedigree.parents_of.get(e, {}).get("father") for e in embryos} - {None}
        mothers_of = {pedigree.parents_of.get(e, {}).get("mother") for e in embryos} - {None}
        father = next(iter(fathers_of)) if len(fathers_of) == 1 else None
        mother = next(iter(mothers_of)) if len(mothers_of) == 1 else None
        if father or mother:
            if father and mother:
                children = pedigree.children_of.get(father, set()) & pedigree.children_of.get(mother, set())
            else:
                children = set(pedigree.children_of.get(father or mother, set()))
            members = {p for p in (father, mother) if p} | children
            return NuclearCore(father=father, mother=mother, children=children, members=members)

    # No embryos (or no recorded embryo parents): the couple sharing the most children.
    fathers = [name for name, role in pedigree.roles.items() if role == "father" and name in pedigree.children_of]
    mothers = [name for name, role in pedigree.roles.items() if role == "mother" and name in pedigree.children_of]
    if not fathers or not mothers:
        return NuclearCore(father=None, mother=None, children=set())

    best: tuple[int, str, str, set[str]] | None = None
    tied: list[tuple[str, str]] = []
    for father in fathers:
        for mother in mothers:
            shared = pedigree.children_of.get(father, set()) & pedigree.children_of.get(mother, set())
            if not shared:
                continue
            if best is None or len(shared) > best[0]:
                best = (len(shared), father, mother, shared)
                tied = [(father, mother)]
            elif len(shared) == best[0]:
                tied.append((father, mother))
    if best is not None and len(tied) > 1:
        logger.warning(
            "identify_core: ambiguous nuclear core — %d couples each share %d "
            "child(ren) %s; picking %s/%s. Lineage colouring may be grounded on the "
            "wrong couple.",
            len(tied),
            best[0],
            tied,
            best[1],
            best[2],
        )
    if best is None:
        # No couple shares a child (single-parent data); fall back to the first
        # father/mother and their direct children.
        father = fathers[0]
        mother = mothers[0]
        children = pedigree.children_of.get(father, set()) | pedigree.children_of.get(mother, set())
    else:
        _, father, mother, children = best
    members = {father, mother} | children
    return NuclearCore(father=father, mother=mother, children=children, members=members)


# --- founder shade recovery ---------------------------------------------------

def _segment_value_on_lane(segments: list[dict[str, Any]], lane: str) -> int | None:
    """The displayed shade (0/1) a parent shows on a haplotype lane.

    A parent's stored blocks carry constant homolog labels (``hap1`` for raw
    homolog 0, ``hap2`` for raw homolog 1) whose *value* is the post-orientation
    shade — exactly what the frontend palette indexes. We read it straight off the
    stored block, so the relative's shade always agrees with the parent's,
    genome-wide, with no need to recompute the orientation flip."""
    for seg in segments:
        value = _allele_int(str(seg.get(lane) or ""))
        if value in (0, 1):
            return value
    return None


def founder_shade_map(parent_segments: list[dict[str, Any]]) -> dict[int, int]:
    """Map a parent's raw homolog index (0/1) to its displayed shade (0/1).

    Raw homolog 0 -> lane ``hap1``; raw homolog 1 -> lane ``hap2``."""
    shade: dict[int, int] = {}
    h1 = _segment_value_on_lane(parent_segments, "hap1")
    h2 = _segment_value_on_lane(parent_segments, "hap2")
    if h1 is not None:
        shade[0] = h1
    if h2 is not None:
        shade[1] = h2
    return shade


# --- IBD homolog matching -----------------------------------------------------

@dataclass(slots=True)
class HomologAssignment:
    """The colour identity of a single homolog: an origin + shade, or grey."""

    origin: str  # PATERNAL / MATERNAL / UNTRANSMITTED / UNKNOWN
    shade: int | None  # 0/1 for coloured lanes; None for grey

    @property
    def is_founder(self) -> bool:
        return self.origin in (PATERNAL, MATERNAL) and self.shade is not None


GREY = HomologAssignment(origin=UNTRANSMITTED, shade=None)


@dataclass(slots=True)
class HomologResolver:
    """Position-aware lookup of an anchor's homolog colour.

    A founder (nuclear-core parent) has a genome-stable colour per raw homolog, so
    its resolver is built from a flat ``{idx: assignment}`` map. A *relative* used as
    a downstream anchor may itself have crossed over mid-chromosome, so the founder
    colour carried by one of its raw homologs *changes with position*; its resolver
    is built from per-homolog runs ``{idx: [(start_pos, assignment), ...]}`` produced
    by :func:`_segment_relative_blocks`. Resolving by position is what lets a 3rd-
    generation relative track the 2nd-generation anchor's crossover instead of
    inheriting one wrong shade across the whole region (fix H3)."""

    # raw homolog index (0/1) -> runs [(start_pos, assignment), ...], ascending by start.
    runs: dict[int, list[tuple[int, HomologAssignment]]]

    @classmethod
    def from_flat(cls, homologs: dict[int, HomologAssignment]) -> "HomologResolver":
        """A position-independent resolver (founders, or a plain test fixture)."""
        return cls(runs={idx: [(0, a)] for idx, a in homologs.items()})

    def at(self, idx: int, pos: int) -> HomologAssignment:
        runs = self.runs.get(idx)
        if not runs:
            return GREY
        value = runs[0][1]
        for start, assignment in runs:
            if start <= pos:
                value = assignment
            else:
                break
        return value


@dataclass(slots=True)
class ColouredMember:
    """A member whose homologs have been assigned colours, usable as an anchor to
    colour its pedigree neighbours."""

    name: str
    # raw homolog index (0/1) -> assignment. For founders this is the genome-stable
    # colour; for a relative it is the chromosome-level (global) assignment, kept for
    # callers that want a single shade. Position-accurate propagation uses ``resolver``.
    homologs: dict[int, HomologAssignment]
    # pos -> (allele0, allele1) phased genotype, for IBD matching
    alleles: dict[int, tuple[int, int]]
    # Position-aware homolog colour. For founders this is a flat (single-run) map; for
    # a relative it carries the per-segment crossover runs so the NEXT hop reads the
    # correct shade at each position (fix H3). Defaults to a flat view of ``homologs``.
    resolver: HomologResolver | None = None

    def homolog_resolver(self) -> HomologResolver:
        return self.resolver if self.resolver is not None else HomologResolver.from_flat(self.homologs)


@dataclass(slots=True)
class MatchResult:
    relative_idx: int
    anchor_idx: int
    informative: int
    confidence: float


def _homolog_consistency(
    who: dict[int, tuple[int, int]],
    other: dict[int, tuple[int, int]],
    het_positions: list[int],
) -> tuple[tuple[float, float], int]:
    """For each homolog of ``who``, the fraction of *phase-informative* sites where
    that allele is the one ``other`` carries.

    Phase-informative here means a directional opposite-zygosity site: ``who`` is
    heterozygous (the candidate ``het_positions``) **and** ``other`` is homozygous.
    At such a site exactly one of ``who``'s homologs carries ``other``'s single
    allele, so the homolog ``who`` transmitted to (or inherited from) ``other``
    scores ~1.0 while the other scores ~0.0 — a clean directional signal.

    Biallelic *double-het* sites (both individuals heterozygous) are deliberately
    excluded: there both of ``who``'s alleles are present in ``other``, so both
    homologs would score 1.0, carrying no transmission information and inflating
    confidence on het-rich imputed data (the false-match failure mode for
    unrelated / mislabelled samples). This mirrors how PLINK/Haplarithmisis
    restrict IBD scoring to informative sites.

    Returns ``((cons0, cons1), n_informative)`` so callers can apply a density gate
    on the number of informative sites actually used.
    """
    present = [0, 0]
    informative = 0
    for pos in het_positions:
        o = other[pos]
        if o[0] != o[1]:
            continue  # double-het: not phase-informative, no transmission direction
        w = who[pos]
        informative += 1
        for idx in (0, 1):
            if w[idx] == o[0]:
                present[idx] += 1
    if informative == 0:
        return (0.0, 0.0), 0
    return (present[0] / informative, present[1] / informative), informative


def match_shared_homolog(
    relative_alleles: dict[int, tuple[int, int]],
    anchor_alleles: dict[int, tuple[int, int]],
) -> MatchResult | None:
    """Confirm a parent-child sharing and identify each side's shared homolog.

    Of the relative and the anchor, the one that did not recombine to connect them
    has a homolog present in the other at ~every informative site; that side
    resolves cleanly (consistency ~1.0) and confirms the relationship. The other
    side may have recombined mid-chromosome (its homologs then split ~50/50) — that
    is fine here, the returned index is only a seed and the per-block pin
    segmentation tracks where it switches.

    Returns ``None`` unless a side resolves *clearly and asymmetrically*: its shared
    homolog must score above ``MIN_SHARED_CONSISTENCY`` AND out-score the other
    homolog by at least ``MIN_HOMOLOG_MARGIN`` (a directional signal, not two
    symmetrically-high double-het scores), over enough informative sites both in
    absolute count (``MIN_INFORMATIVE_SITES``) and in per-Mb density
    (``MIN_INFORMATIVE_PER_MB``). These guard against false confirms on het-rich
    imputed data from unrelated / mislabelled samples.
    """
    shared_pos = [pos for pos in relative_alleles if pos in anchor_alleles]
    if not shared_pos:
        return None
    rel_het = [pos for pos in shared_pos if relative_alleles[pos][0] != relative_alleles[pos][1]]
    anc_het = [pos for pos in shared_pos if anchor_alleles[pos][0] != anchor_alleles[pos][1]]

    rel_cons, rel_informative = _homolog_consistency(relative_alleles, anchor_alleles, rel_het)
    anc_cons, anc_informative = _homolog_consistency(anchor_alleles, relative_alleles, anc_het)

    relative_idx = 0 if rel_cons[0] >= rel_cons[1] else 1
    anchor_idx = 0 if anc_cons[0] >= anc_cons[1] else 1

    # Span over which informative sites are spread, for the per-Mb density floor.
    span_mb = max(shared_pos) - min(shared_pos)
    span_mb = max(span_mb, 1) / 1_000_000

    # Each side is a candidate; pick the better-supported one, but only if it clears
    # all gates. A side qualifies only with a clear winner→loser margin (rejects the
    # symmetric double-het inflation), enough informative sites, and enough density.
    def _side(cons: tuple[float, float], idx: int, informative: int) -> tuple[float, float] | None:
        chosen, sibling = cons[idx], cons[1 - idx]
        if informative < MIN_INFORMATIVE_SITES:
            return None
        if informative / span_mb < MIN_INFORMATIVE_PER_MB:
            return None
        if chosen < MIN_SHARED_CONSISTENCY:
            return None
        if chosen - sibling < MIN_HOMOLOG_MARGIN:
            return None
        return chosen, chosen - sibling

    rel_side = _side(rel_cons, relative_idx, rel_informative)
    anc_side = _side(anc_cons, anchor_idx, anc_informative)
    if rel_side is None and anc_side is None:
        return None

    # Confidence is the consistency of whichever qualifying side resolved most cleanly.
    confidence = max(
        rel_side[0] if rel_side is not None else 0.0,
        anc_side[0] if anc_side is not None else 0.0,
    )
    return MatchResult(
        relative_idx=relative_idx,
        anchor_idx=anchor_idx,
        informative=max(rel_informative, anc_informative),
        confidence=confidence,
    )


# --- recombination-aware segmentation -----------------------------------------

def _pin_shared_index(
    het_alleles: tuple[int, int],
    hom_allele: int,
) -> int | None:
    """At a site where one individual is heterozygous and the other homozygous for
    ``hom_allele``, the shared homolog of the heterozygous individual is the one
    carrying ``hom_allele`` (the allele that was transmitted). Returns that homolog
    index, or ``None`` if both of its alleles equal ``hom_allele`` (not
    distinguishing)."""
    matches = [idx for idx in (0, 1) if het_alleles[idx] == hom_allele]
    return matches[0] if len(matches) == 1 else None


def _relative_index_pins(
    positions: list[int],
    rel_alleles: dict[int, tuple[int, int]],
    anc_alleles: dict[int, tuple[int, int]],
) -> list[tuple[int, int]]:
    """Per position, which *relative* homolog is the shared one — pinned at sites
    where the relative is het and the anchor is homozygous."""
    pins: list[tuple[int, int]] = []
    for pos in positions:
        r0, r1 = rel_alleles[pos]
        a0, a1 = anc_alleles[pos]
        if r0 != r1 and a0 == a1:
            idx = _pin_shared_index((r0, r1), a0)
            if idx is not None:
                pins.append((pos, idx))
    return pins


def _anchor_index_pins(
    positions: list[int],
    rel_alleles: dict[int, tuple[int, int]],
    anc_alleles: dict[int, tuple[int, int]],
) -> list[tuple[int, int]]:
    """Per position, which *anchor* homolog is the shared one — pinned at sites
    where the anchor is het and the relative is homozygous."""
    pins: list[tuple[int, int]] = []
    for pos in positions:
        r0, r1 = rel_alleles[pos]
        a0, a1 = anc_alleles[pos]
        if a0 != a1 and r0 == r1:
            idx = _pin_shared_index((a0, a1), r0)
            if idx is not None:
                pins.append((pos, idx))
    return pins


def _smooth_runs(
    pins: list[tuple[int, int]],
    *,
    fallback: int,
    start_pos: int,
) -> list[tuple[int, int]]:
    """Collapse a noisy sequence of index pins into runs ``[(run_start, idx), ...]``.

    The first run is seeded from the value of the *first pin* (so the chromosome
    *start* is read from local evidence, not from a possibly-arbitrary global seed
    — the side that recombined has no meaningful genome-wide index). A switch away
    from the current index is only committed once the contradicting pins form a run
    of at least ``LINEAGE_SWITCH_MIN_MARKERS`` markers spanning at least
    ``LINEAGE_SWITCH_MIN_SPAN`` bases — so a real crossover splits the track but
    isolated noise does not. Seeding from the first pin (rather than a fixed
    head-window majority vote) keeps the seed *symmetric* with the switch
    criterion: a short leading tract followed by a sustained switch is now read as
    ``seed -> switch`` (both crossovers represented) instead of being swallowed
    into the seed and hiding the early breakpoint. Any stray leading pins are
    corrected by the same commit logic, since the first *sustained* run forces a
    committed switch. Within a region of interest (few pins) this yields a single
    run, exactly the no-recombination case."""
    if not pins:
        return [(start_pos, fallback)]
    initial = pins[0][1]
    runs: list[tuple[int, int]] = [(start_pos, initial)]
    current = initial
    pending_idx: int | None = None
    pending_start = 0
    pending_last = 0
    pending_count = 0
    for pos, idx in pins:
        if idx == current:
            pending_idx = None
            pending_count = 0
            continue
        if pending_idx == idx:
            pending_count += 1
            pending_last = pos
        else:
            pending_idx = idx
            pending_start = pos
            pending_last = pos
            pending_count = 1
        if pending_count >= LINEAGE_SWITCH_MIN_MARKERS and pending_last - pending_start >= LINEAGE_SWITCH_MIN_SPAN:
            runs.append((pending_start, idx))
            current = idx
            pending_idx = None
            pending_count = 0
    return runs


def _runs_value_at(runs: list[tuple[int, int]], pos: int) -> int:
    value = runs[0][1]
    for start, idx in runs:
        if start <= pos:
            value = idx
        else:
            break
    return value


def _any_pos_in_range(sorted_positions: list[int], start: int, end: int) -> bool:
    """True if any position in the ascending ``sorted_positions`` lies in ``[start, end)``.

    Used to check whether a relative-block segment is backed by at least one
    anchor-informative pin; a segment with none has a guessed (coin-flip) anchor
    shade and must be greyed rather than coloured a definite founder shade."""
    lo = bisect.bisect_left(sorted_positions, start)
    return lo < len(sorted_positions) and sorted_positions[lo] < end


def _collapse_assignment_runs(
    runs: list[tuple[int, HomologAssignment]],
) -> list[tuple[int, HomologAssignment]]:
    """Drop consecutive runs that carry the same colour (origin + shade), keeping the
    earliest start — so a homolog's run list only records *real* colour changes."""
    collapsed: list[tuple[int, HomologAssignment]] = []
    for start, assignment in runs:
        if collapsed:
            prev = collapsed[-1][1]
            if prev.origin == assignment.origin and prev.shade == assignment.shade:
                continue
        collapsed.append((start, assignment))
    return collapsed


def _segment_relative_blocks(
    *,
    chrom: str,
    positions: list[int],
    rel_alleles: dict[int, tuple[int, int]],
    anc_alleles: dict[int, tuple[int, int]],
    anchor_homologs: HomologResolver,
    match: MatchResult,
    region_start: int,
    region_end: int,
) -> tuple[list[dict[str, Any]], dict[int, HomologAssignment], "HomologResolver"]:
    """Build recombination-segmented, lineage-tagged blocks for one relative on one
    chromosome.

    Two independent step functions are recovered from the genotypes: which
    *relative* homolog is shared (switches when the relative transmits a recombinant
    to the anchor — an upstream crossover) and which *anchor* homolog is shared
    (switches when the anchor transmits a recombinant to the relative — a downstream
    crossover). At every base the shared relative lane is coloured with the anchor's
    colour for the matched anchor homolog; the other lane is grey. Adjacent blocks
    with the same colouring are merged.

    The anchor's colour is resolved *per position*: ``anchor_homologs`` is a
    :class:`HomologResolver`, whose colour for a raw homolog changes mid-chromosome when
    the anchor itself crossed over (a founder's is flat: ``HomologResolver.from_flat``).

    Returns ``(blocks, global_assignment, resolver)``:
      * ``global_assignment`` — the relative's chromosome-level homolog map (from the
        global match), a single shade per raw homolog, for callers that want one.
      * ``resolver`` — a position-aware :class:`HomologResolver` carrying the relative's
        per-segment crossover runs, so the NEXT hop reads the correct shade at each
        position instead of one wrong shade across a post-crossover span (fix H3).
    """
    resolver = anchor_homologs
    rel_runs = _smooth_runs(
        _relative_index_pins(positions, rel_alleles, anc_alleles),
        fallback=match.relative_idx,
        start_pos=region_start,
    )
    anchor_pins = _anchor_index_pins(positions, rel_alleles, anc_alleles)
    anc_runs = _smooth_runs(
        anchor_pins,
        fallback=match.anchor_idx,
        start_pos=region_start,
    )
    # Positions where the anchor-informative sites actually pin WHICH founder homolog
    # the relative shares. Where no such pin exists, ``anc_idx`` is only the coin-flip
    # ``match.anchor_idx`` fallback (e.g. only the relative side resolved the match, so
    # every site where the founder is het is a double-het carrying no transmission
    # direction). Trusting it would paint the shared lane with an arbitrary founder
    # shade (dark vs light blue) — a wrong dominant signature. So a segment with no
    # supporting anchor pin is greyed (shade UNKNOWN) rather than coloured.
    anchor_pin_positions = sorted(pos for pos, _ in anchor_pins)
    # The anchor's own colour may change mid-region (it is itself a relative that
    # crossed over — fix H3). Those colour-change positions are extra block boundaries:
    # without them the relative would be a single block and the anchor colour would be
    # sampled once, dropping the anchor's crossover for this (and every downstream) hop.
    anchor_colour_starts = {
        start
        for runs in resolver.runs.values()
        for start, _ in runs
        if region_start < start < region_end
    }
    boundaries = sorted(
        {region_start}
        | {start for start, _ in rel_runs if region_start < start < region_end}
        | {start for start, _ in anc_runs if region_start < start < region_end}
        | anchor_colour_starts
    )

    raw_blocks: list[dict[str, Any]] = []
    # Per raw homolog of the relative, the founder colour it carries across segments,
    # for position-aware propagation to the next hop.
    homolog_runs: dict[int, list[tuple[int, HomologAssignment]]] = {0: [], 1: []}
    for i, seg_start in enumerate(boundaries):
        seg_end = boundaries[i + 1] if i + 1 < len(boundaries) else region_end
        rel_idx = _runs_value_at(rel_runs, seg_start)
        anc_idx = _runs_value_at(anc_runs, seg_start)
        # The founder shade is only trustworthy where an anchor-informative pin lies
        # in this segment. Without one, ``anc_idx`` is a guess (coin-flip fallback) and
        # the shared lane must be grey, not a definite founder shade.
        if _any_pos_in_range(anchor_pin_positions, seg_start, seg_end):
            shared = resolver.at(anc_idx, seg_start)
            shared = shared if shared.is_founder else GREY
        else:
            shared = GREY
        assignment = {rel_idx: shared, 1 - rel_idx: GREY}
        homolog_runs[0].append((int(seg_start), assignment[0]))
        homolog_runs[1].append((int(seg_start), assignment[1]))
        block = {"chr": chrom, "start": int(seg_start), "end": int(seg_end), "ps": None}
        for idx, lane in ((0, "hap1"), (1, "hap2")):
            a = assignment[idx]
            block[lane] = str(a.shade) if a.shade is not None else "0"
            block[f"{lane}_lineage"] = a.origin
        raw_blocks.append(block)

    blocks = _merge_adjacent_blocks(raw_blocks)

    # Chromosome-level (single-shade) assignment, from the global match seed. Only
    # trust the seed shade when at least one anchor-informative pin supported the
    # chosen anchor homolog; otherwise the seed is the coin-flip fallback and the
    # relative is greyed (never seeds a downstream hop with a guessed founder shade).
    shared_global = resolver.at(match.anchor_idx, region_start) if anchor_pin_positions else GREY
    global_assignment = {
        match.relative_idx: shared_global if shared_global.is_founder else GREY,
        1 - match.relative_idx: GREY,
    }
    rel_resolver = HomologResolver(
        runs={idx: _collapse_assignment_runs(runs) for idx, runs in homolog_runs.items()}
    )
    return blocks, global_assignment, rel_resolver


def _merge_adjacent_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for block in blocks:
        if merged and _same_colouring(merged[-1], block) and merged[-1]["end"] == block["start"]:
            merged[-1]["end"] = block["end"]
        else:
            merged.append(block)
    return merged


def _same_colouring(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return all(a[k] == b[k] for k in ("hap1", "hap2", "hap1_lineage", "hap2_lineage"))


# --- relatives of unknown degree -------------------------------------------------
#
# A member linked to the family by a ``relative`` edge -- related through that member by
# a degree nobody recorded, such as a PGT index known only to be on the mother's side --
# is coloured along the genome when it turns out to be that member's parent or child: when
# it shares one of the member's haplotypes along (nearly) every autosome, which the
# parent-child share test above confirms one chromosome at a time. It is then coloured as
# a parent or child is. A more distant relative shares a haplotype only in stretches, and
# on imputed low-pass genotypes a stretch cannot be found site by site: it cannot be told
# apart from the long runs unrelated people share by state, nor from stretches where a
# sibling shares both haplotypes (in an example family, a model reading such stretches
# site by site called a share at most sites of an unrelated pair and called both
# haplotypes shared at a third of a parent and child's sites). Such a relative is read at
# the region of interest only, as PGT-M reads a distant reference: from the informative
# sites on both sides of it, which must name the same haplotype of the member (see
# ``relative_share_at_locus``); it is grey elsewhere. Both decisions are taken in the
# genome-wide precompute (``bed_service``); the windows read its colours.

# A relative of unknown degree is read as the linked member's parent or child when it
# shares one of the member's haplotypes along at least this share of the autosomes the
# test could read, and the test could read at least this many.
RELATIVE_PARENT_OR_CHILD_MIN_FRACTION = 0.9
RELATIVE_PARENT_OR_CHILD_MIN_CHROMOSOMES = 15


def relative_links(relationship_rows: Iterable[dict[str, Any]]) -> list[tuple[str, str]]:
    """``(member, linked member)`` of each ``relative`` edge: ``sample_id_b`` is related
    through ``sample_id_a``."""
    links: list[tuple[str, str]] = []
    for rel in relationship_rows:
        if str(rel.get("relationship_type")) != "relative":
            continue
        linked, member = str(rel.get("sample_id_a") or ""), str(rel.get("sample_id_b") or "")
        if linked and member and linked != member:
            links.append((member, linked))
    return links


def relative_link_matches(
    genotype_rows: list[tuple[Any, ...]],
    links: Iterable[tuple[str, str]],
    *,
    chrom: str,
) -> dict[tuple[str, str], bool]:
    """Per link, whether the member shares one of the linked member's haplotypes along
    this chromosome (the parent-child share test). A link the chromosome cannot test --
    not an autosome, or either member without phased genotypes here -- is left out."""
    if not _is_autosome(chrom):
        return {}
    alleles = _alleles_by_member(genotype_rows)
    results: dict[tuple[str, str], bool] = {}
    for member, linked in links:
        member_alleles, linked_alleles = alleles.get(member, {}), alleles.get(linked, {})
        if not member_alleles or not linked_alleles:
            continue
        results[(member, linked)] = match_shared_homolog(member_alleles, linked_alleles) is not None
    return results


def parent_or_child_links(counts: dict[tuple[str, str], tuple[int, int]]) -> set[tuple[str, str]]:
    """The links whose member is the linked member's parent or child, from
    ``{link: (autosomes sharing, autosomes tested)}``."""
    return {
        link
        for link, (sharing, tested) in counts.items()
        if tested >= RELATIVE_PARENT_OR_CHILD_MIN_CHROMOSOMES
        and sharing >= RELATIVE_PARENT_OR_CHILD_MIN_FRACTION * tested
    }


# A relative of unknown degree that is not the linked member's parent or child (a sibling,
# an aunt, a cousin) is read at the region of interest only, the way PGT-M reads a distant
# reference: from the informative sites on both sides of the region. On each flank of this
# width, the sites where the member is heterozygous and the relative homozygous (pins) must
# number at least this many, be at least this share of the member's heterozygous sites (in
# a stretch where the relative carries both of the member's haplotypes, nearly none are),
# and name one of the member's haplotypes at least this consistently; both flanks must name
# the same one. On the phased genotypes of an example family's embryo pairs (siblings, whose
# sharing the trios give), this read the shared haplotype at 84% of the loci where one was
# shared, never the other one, and none where none was; it read one at 9 of 5,768 loci
# where the trios give both shared (likely where their reading is off), and at a single
# locus of an unrelated pair, likely a stretch shared from distant ancestry.
RELATIVE_LOCUS_FLANK = 3_000_000
RELATIVE_LOCUS_MIN_PINS = 100
RELATIVE_LOCUS_MIN_PIN_FRACTION = 0.15
RELATIVE_LOCUS_MIN_CONSISTENCY = 0.96


def relative_share_at_locus(
    rel_alleles: dict[int, tuple[int, int]],
    anc_alleles: dict[int, tuple[int, int]],
    *,
    start: int,
    end: int,
) -> int | None:
    """The member's homolog (0 or 1) the relative shares across ``[start, end)``, read
    from both flanks of it, or ``None`` when either flank does not say so clearly."""
    homologs: list[int] = []
    for lo, hi in ((start - RELATIVE_LOCUS_FLANK, start), (end, end + RELATIVE_LOCUS_FLANK)):
        pins = [0, 0]
        heterozygous = 0
        for pos, (a0, a1) in anc_alleles.items():
            if not lo <= pos < hi or a0 == a1:
                continue
            heterozygous += 1
            relative = rel_alleles.get(pos)
            if relative is None or relative[0] != relative[1]:
                continue
            if relative[0] == a0:
                pins[0] += 1
            elif relative[0] == a1:
                pins[1] += 1
        total = pins[0] + pins[1]
        if (
            total < RELATIVE_LOCUS_MIN_PINS
            or total < RELATIVE_LOCUS_MIN_PIN_FRACTION * heterozygous
            or max(pins) < RELATIVE_LOCUS_MIN_CONSISTENCY * total
        ):
            return None
        homologs.append(0 if pins[0] > pins[1] else 1)
    return homologs[0] if homologs[0] == homologs[1] else None


def _colour_across(resolver: HomologResolver, idx: int, start: int, end: int) -> HomologAssignment | None:
    """The colour homolog ``idx`` has throughout ``[start, end)``, or ``None`` where it
    changes there (an anchor that is itself a relative may cross over)."""
    colour = resolver.at(idx, start)
    for run_start, assignment in resolver.runs.get(idx, []):
        if start < run_start < end and assignment != colour:
            return None
    return colour


def _locus_share_blocks(
    *,
    chrom: str,
    rel_alleles: dict[int, tuple[int, int]],
    anc_alleles: dict[int, tuple[int, int]],
    anchor_homolog: int,
    colour: HomologAssignment,
    window: tuple[int, int],
    region_start: int,
    region_end: int,
) -> list[dict[str, Any]]:
    """A relative's blocks when it shares the member's homolog ``anchor_homolog`` across
    ``window`` (the region of interest and its flanks): there, the lane carrying it -- the
    one holding that homolog's allele at most of the relative's heterozygous sites -- takes
    the member's colour and the other is grey; elsewhere both are grey (unknown)."""
    lo = min(max(window[0], region_start), region_end)
    hi = max(min(window[1], region_end), lo)
    votes = [0, 0]
    for pos, (r0, r1) in rel_alleles.items():
        if not lo <= pos < hi or r0 == r1 or pos not in anc_alleles:
            continue
        shared = anc_alleles[pos][anchor_homolog]
        if r0 == shared:
            votes[0] += 1
        elif r1 == shared:
            votes[1] += 1
    lane = "hap1" if votes[0] >= votes[1] else "hap2"
    other = "hap2" if lane == "hap1" else "hap1"
    blocks: list[dict[str, Any]] = []
    if region_start < lo:
        blocks.append(
            {"chr": chrom, "start": region_start, "end": lo, "ps": None, "hap1": "0", "hap2": "0",
             "hap1_lineage": UNKNOWN, "hap2_lineage": UNKNOWN}
        )
    if lo < hi:
        blocks.append(
            {"chr": chrom, "start": lo, "end": hi, "ps": None,
             lane: str(colour.shade), f"{lane}_lineage": colour.origin,
             other: "0", f"{other}_lineage": UNTRANSMITTED}
        )
    if hi < region_end:
        blocks.append(
            {"chr": chrom, "start": hi, "end": region_end, "ps": None, "hap1": "0", "hap2": "0",
             "hap1_lineage": UNKNOWN, "hap2_lineage": UNKNOWN}
        )
    return blocks


def _merge_lane_claims(
    chrom: str,
    claims_by_anchor: list[list[dict[str, Any]]],
    region_start: int,
    region_end: int,
) -> list[dict[str, Any]]:
    """One relative's blocks from several linked members' colourings (each colours one
    lane of a block and greys the other): a lane takes the colour one of them gives it;
    a lane two of them colour is grey (unknown), as nothing tells which is right."""
    unknown = HomologAssignment(origin=UNKNOWN, shade=None)
    boundaries = sorted(
        {region_start, region_end}
        | {int(block["start"]) for blocks in claims_by_anchor for block in blocks}
        | {int(block["end"]) for blocks in claims_by_anchor for block in blocks}
    )
    raw: list[dict[str, Any]] = []
    for index, start in enumerate(boundaries[:-1]):
        end = boundaries[index + 1]
        if end <= region_start or start >= region_end:
            continue
        coloured: dict[str, list[tuple[str, str]]] = {"hap1": [], "hap2": []}
        for blocks in claims_by_anchor:
            for block in blocks:
                if int(block["start"]) <= start and end <= int(block["end"]):
                    for lane in ("hap1", "hap2"):
                        if block[f"{lane}_lineage"] in (PATERNAL, MATERNAL):
                            coloured[lane].append((str(block[lane]), str(block[f"{lane}_lineage"])))
        block_out: dict[str, Any] = {"chr": chrom, "start": int(start), "end": int(end), "ps": None}
        for lane in ("hap1", "hap2"):
            other = "hap2" if lane == "hap1" else "hap1"
            if len(coloured[lane]) == 1:
                block_out[lane], block_out[f"{lane}_lineage"] = coloured[lane][0]
            else:
                block_out[lane] = "0"
                block_out[f"{lane}_lineage"] = (
                    unknown.origin if coloured[lane] or not coloured[other] else UNTRANSMITTED
                )
        raw.append(block_out)
    return _merge_adjacent_blocks(raw)


# --- orchestration ------------------------------------------------------------

def _alleles_by_member(
    genotype_rows: Iterable[tuple[Any, ...]],
) -> dict[str, dict[int, tuple[int, int]]]:
    """Per member, the phased alleles at each site: {name: {pos: (a0, a1)}}. Rows
    are ``(pos, ..., sample_ids, gts)`` — the optional middle holds ref/alt, which
    this colouring path ignores."""
    out: dict[str, dict[int, tuple[int, int]]] = {}
    for row in genotype_rows:
        pos, sample_ids, gts = row[0], row[-2], row[-1]
        for name, gt in zip(sample_ids, gts):
            alleles = _phased_alleles(gt)
            if alleles is None:
                continue
            out.setdefault(name, {})[pos] = alleles
    return out


def _core_homologs(
    name: str,
    core: NuclearCore,
    father_shade: dict[int, int],
    mother_shade: dict[int, int],
) -> dict[int, HomologAssignment]:
    """Raw homolog -> assignment for a nuclear-core *parent* (used as an anchor).

    Children are deliberately not exposed as anchors: their raw homolog order is
    not trivially origin-labelled, and every relative of interest connects to the
    core through a parent or grandparent, not through a proband's own child.

    A founder shade is read from the parent's stored blocks. If a shade is missing
    for a homolog (no stored block resolved it on this chromosome), we must NOT fall
    back to the raw homolog index — that silently invents an orientation and can
    flip dark/light. Instead the unresolved homolog is greyed, so a relative whose
    shared homolog maps to it inherits grey rather than a guessed colour."""
    if name == core.father:
        return _shade_assignments(PATERNAL, father_shade)
    if name == core.mother:
        return _shade_assignments(MATERNAL, mother_shade)
    return {}


def _shade_assignments(origin: str, shade: dict[int, int]) -> dict[int, HomologAssignment]:
    """Per raw homolog, a founder assignment when the shade is known, else GREY.

    Never defaults a missing shade to the raw index (which would fabricate an
    orientation); an unresolved founder homolog stays grey."""
    out: dict[int, HomologAssignment] = {}
    for i in (0, 1):
        value = shade.get(i)
        out[i] = HomologAssignment(origin, value) if value in (0, 1) else GREY
    return out


def annotate_lineage(
    *,
    sample_rows: list[dict[str, Any]],
    relationship_rows: list[dict[str, Any]],
    segments_by_name: dict[str, list[dict[str, Any]]],
    genotype_rows: list[tuple[int, list[str], list[str]]],
    chrom: str,
    region_start: int | None = None,
    region_end: int | None = None,
    genotype_truncated: bool = False,
    parent_or_child: Collection[tuple[str, str]] = (),
    locus: tuple[int, int] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Return segments with per-lane lineage tags.

    ``parent_or_child`` holds the ``relative`` links (member, linked member) whose
    member was found to be the linked member's parent or child genome-wide (see
    "relatives of unknown degree"). A member of any other such link is read at
    ``locus``, the region of interest on this chromosome, from its flanks
    (:func:`relative_share_at_locus`), and is grey elsewhere and where the flanks do not
    agree.

    Nuclear-core members keep their stored blocks, tagged by role. Relatives have
    their stored (meaningless) blocks replaced by lineage-tagged blocks computed
    from IBD matching against the pedigree. Members we cannot place are tagged
    ``unknown`` (rendered grey), never mis-coloured.

    ``genotype_truncated`` signals that the per-chromosome phased-genotype fetch hit
    its row cap, so ``genotype_rows`` only covers the lowest-coordinate sites (the
    fetch is ``ORDER BY pos`` ascending). Relatives must NOT be coloured past the last
    fetched site — there is no IBD evidence there — so their coloured blocks are
    clamped to ``max(genotype pos)`` and a grey tail covers the rest of the
    chromosome, mirroring the marker path's truncation guard.
    """
    pedigree = build_pedigree(sample_rows, relationship_rows)
    core = identify_core(pedigree)
    result: dict[str, list[dict[str, Any]]] = {}

    two_parent = bool(core.father) and bool(core.mother)
    # Single-parent (donor) family: exactly one known parent of the embryos. The
    # other side is a donor/unknown and its lane is greyed.
    single_known = (
        (core.father or core.mother)
        if (bool(core.father) != bool(core.mother)) and core.children
        else None
    )
    single_origin = PATERNAL if (single_known and core.father) else MATERNAL

    # 1) Tag the nuclear core from stored blocks, by role (two-parent families only).
    # In a single-parent family the embryos are coloured by IBD against the known
    # parent below — the donor lane is greyed — so they are NOT role-tagged here.
    for name, segments in segments_by_name.items():
        if two_parent and name == core.father:
            result[name] = _tag_segments(segments, PATERNAL, PATERNAL)
        elif two_parent and name == core.mother:
            result[name] = _tag_segments(segments, MATERNAL, MATERNAL)
        elif two_parent and name in core.children:
            result[name] = _tag_segments(segments, PATERNAL, MATERNAL)
        else:
            result[name] = None  # placeholder; filled below or greyed

    if not two_parent and single_known is None:
        # No identifiable parent at all — cannot ground founder colours.
        return _grey_remaining(result, segments_by_name)

    if not _is_autosome(chrom):
        # Sex chromosomes / mitochondrion break the diploid two-homolog assumption
        # the IBD logic relies on (hemizygous X in males, no recombination on Y).
        # Rather than mis-colour, leave relatives grey on non-autosomes. The core
        # itself keeps its role-based stored-block tags from step 1.
        # TODO(follow-up): proper hemizygous-X handling (single-homolog matching).
        return _grey_remaining(result, segments_by_name)

    alleles = _alleles_by_member(genotype_rows)
    eff_start, eff_end = _region_bounds(
        segments_by_name, genotype_rows, chrom, region_start, region_end
    )
    # When the genotype fetch was truncated, there is no IBD evidence beyond the last
    # fetched site. Clamp the coloured-block span to that position so no relative gets a
    # colour across a region the data never confirmed; the un-evidenced tail is greyed
    # below. ``region_full_end`` is the span we still want fully covered (with grey).
    region_full_end = eff_end
    if genotype_truncated and genotype_rows:
        max_evidence_pos = max(row[0] for row in genotype_rows)
        eff_end = min(eff_end, max_evidence_pos + 1)

    # 2) Seed the BFS roots, then colour neighbours by IBD matching.
    coloured: dict[str, ColouredMember] = {}
    if two_parent:
        father_shade = founder_shade_map(segments_by_name.get(core.father, []))
        mother_shade = founder_shade_map(segments_by_name.get(core.mother, []))
        for parent in (core.father, core.mother):
            coloured[parent] = ColouredMember(
                name=parent,
                homologs=_core_homologs(parent, core, father_shade, mother_shade),
                alleles=alleles.get(parent, {}),
            )
        frontier = [core.father, core.mother]
        visited = set(core.members)
    else:
        # Single-parent (donor) family: root at the one known parent. Its two raw
        # GLIMPSE homologs are the founders (one per grandparent), grounded by raw
        # homolog index — there is no second parent to orient against, and the
        # affected grandparent identifies which homolog carries the disease. The
        # embryos are this root's children, so the BFS IBD-matches them: the
        # known-parent-derived lane is coloured and the donor lane greyed automatically.
        coloured[single_known] = ColouredMember(
            name=single_known,
            homologs={i: HomologAssignment(single_origin, i) for i in (0, 1)},
            alleles=alleles.get(single_known, {}),
        )
        # The known parent's own two homologs span the region (its own chromosomes do
        # not recombine within themselves): one dark, one light of its origin colour.
        result[single_known] = _founder_parent_blocks(chrom, single_origin, eff_start, region_full_end)
        frontier = [single_known]
        visited = {single_known}
    while frontier:
        anchor_name = frontier.pop(0)
        anchor = coloured.get(anchor_name)
        if anchor is None or not anchor.alleles:
            continue
        for neighbor in sorted(pedigree.neighbors(anchor_name)):
            if neighbor in visited or neighbor in coloured:
                continue
            rel_alleles = alleles.get(neighbor, {})
            if not rel_alleles:
                continue
            match = match_shared_homolog(rel_alleles, anchor.alleles)
            if match is None:
                continue
            positions = sorted(pos for pos in rel_alleles if pos in anchor.alleles)
            segs, assignment, rel_resolver = _segment_relative_blocks(
                chrom=chrom,
                positions=positions,
                rel_alleles=rel_alleles,
                anc_alleles=anchor.alleles,
                # Position-aware anchor colour: if this anchor is itself a relative that
                # crossed over, its resolver hands the next hop the correct shade per
                # position instead of one chromosome-wide shade (fix H3).
                anchor_homologs=anchor.homolog_resolver(),
                match=match,
                region_start=eff_start,
                region_end=eff_end,
            )
            if genotype_truncated and region_full_end > eff_end:
                # No genotype evidence past the truncation point: grey the tail so the
                # coloured block does not imply a share we never observed.
                segs = segs + [_grey_tail_block(chrom, eff_end, region_full_end)]
            result[neighbor] = segs
            coloured[neighbor] = ColouredMember(
                name=neighbor,
                homologs=assignment,
                alleles=rel_alleles,
                resolver=rel_resolver,
            )
            visited.add(neighbor)
            frontier.append(neighbor)
        # Relatives of unknown degree, coloured as parents or children of the members
        # they are linked to where ``parent_or_child`` says they are; nothing is coloured
        # through them.
        for neighbor in sorted(pedigree.relatives(anchor_name)):
            if neighbor in visited or neighbor in coloured:
                continue
            rel_alleles = alleles.get(neighbor, {})
            if not rel_alleles:
                continue
            linked = [
                name
                for name in sorted(pedigree.relatives(neighbor))
                if name in coloured and coloured[name].alleles
            ]
            if not linked:
                continue
            visited.add(neighbor)
            claims: list[list[dict[str, Any]]] = []
            for name in linked:
                anchor_member = coloured[name]
                if (neighbor, name) in parent_or_child or (name, neighbor) in parent_or_child:
                    match = match_shared_homolog(rel_alleles, anchor_member.alleles)
                    if match is None:
                        continue
                    segs, _assignment, _resolver = _segment_relative_blocks(
                        chrom=chrom,
                        positions=sorted(pos for pos in rel_alleles if pos in anchor_member.alleles),
                        rel_alleles=rel_alleles,
                        anc_alleles=anchor_member.alleles,
                        anchor_homologs=anchor_member.homolog_resolver(),
                        match=match,
                        region_start=eff_start,
                        region_end=eff_end,
                    )
                    claims.append(segs)
                    continue
                if locus is None:
                    continue
                shared = relative_share_at_locus(
                    rel_alleles, anchor_member.alleles, start=locus[0], end=locus[1]
                )
                if shared is None:
                    continue
                window = (locus[0] - RELATIVE_LOCUS_FLANK, locus[1] + RELATIVE_LOCUS_FLANK)
                colour = _colour_across(anchor_member.homolog_resolver(), shared, *window)
                if colour is None or not colour.is_founder:
                    continue
                claims.append(
                    _locus_share_blocks(
                        chrom=chrom,
                        rel_alleles=rel_alleles,
                        anc_alleles=anchor_member.alleles,
                        anchor_homolog=shared,
                        colour=colour,
                        window=window,
                        region_start=eff_start,
                        region_end=eff_end,
                    )
                )
            if not claims:
                continue
            segs = claims[0] if len(claims) == 1 else _merge_lane_claims(chrom, claims, eff_start, eff_end)
            if genotype_truncated and region_full_end > eff_end:
                segs = segs + [_grey_tail_block(chrom, eff_end, region_full_end)]
            result[neighbor] = segs

    return _grey_remaining(result, segments_by_name)


def _normalize_chrom(value: Any) -> str:
    return str(value or "").strip().lower().removeprefix("chr")


def _region_bounds(
    segments_by_name: dict[str, list[dict[str, Any]]],
    genotype_rows: list[tuple[int, list[str], list[str]]],
    chrom: str,
    region_start: int | None,
    region_end: int | None,
) -> tuple[int, int]:
    """The span to lay relative blocks across. Uses the explicit region when given
    (region-of-interest view); otherwise falls back to the union of stored block
    extents on this chromosome (the whole-chromosome batch view), then to the
    genotype positions."""
    if region_start is not None and region_end is not None and region_end > region_start:
        return int(region_start), int(region_end)
    target = _normalize_chrom(chrom)
    starts: list[int] = []
    ends: list[int] = []
    for segments in segments_by_name.values():
        for seg in segments:
            if target and _normalize_chrom(seg.get("chr")) not in (target, ""):
                continue
            starts.append(int(seg["start"]))
            ends.append(int(seg["end"]))
    if genotype_rows:
        positions = [row[0] for row in genotype_rows]
        starts.append(min(positions))
        ends.append(max(positions) + 1)
    if not starts or not ends:
        return 0, 0
    return min(starts), max(ends)


def _tag_segments(
    segments: list[dict[str, Any]],
    hap1_lineage: str,
    hap2_lineage: str,
) -> list[dict[str, Any]]:
    tagged: list[dict[str, Any]] = []
    for seg in segments:
        new = dict(seg)
        new["hap1_lineage"] = hap1_lineage
        new["hap2_lineage"] = hap2_lineage
        tagged.append(new)
    return tagged


def _grey_tail_block(chrom: str, start: int, end: int) -> dict[str, Any]:
    """A single grey (untransmitted) block spanning ``[start, end)`` — used to cover a
    region with no genotype evidence (e.g. past a truncated per-chromosome fetch) so a
    coloured relative block never extends beyond the data that confirmed it."""
    return {
        "chr": chrom,
        "start": int(start),
        "end": int(end),
        "ps": None,
        "hap1": "0",
        "hap2": "0",
        "hap1_lineage": UNTRANSMITTED,
        "hap2_lineage": UNTRANSMITTED,
    }


def _founder_parent_blocks(chrom: str, origin: str, start: int, end: int) -> list[dict[str, Any]]:
    """Display block for a single-parent family's one known parent: its two raw
    homologs as a dark (hap1='0') and light (hap2='1') lane of ``origin`` colour,
    spanning ``[start, end)``. The absolute dark/light labels are arbitrary (the raw
    GLIMPSE phasing); consistency is what matters, and the affected grandparent
    identifies which homolog carries the disease."""
    return [
        {
            "chr": chrom,
            "start": int(start),
            "end": int(end),
            "ps": None,
            "hap1": "0",
            "hap2": "1",
            "hap1_lineage": origin,
            "hap2_lineage": origin,
        }
    ]


def _grey_remaining(
    result: dict[str, list[dict[str, Any]] | None],
    segments_by_name: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    """Any member still without lineage (a relative we couldn't place) keeps its stored
    segments, both lanes tagged ``unknown`` (rendered grey) — never mis-coloured."""
    final: dict[str, list[dict[str, Any]]] = {}
    for name, segs in result.items():
        if segs is not None:
            final[name] = segs
            continue
        stored = segments_by_name.get(name, [])
        final[name] = _tag_segments(stored, UNKNOWN, UNKNOWN)
    return final
