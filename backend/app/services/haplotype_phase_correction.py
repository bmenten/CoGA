"""Phase switches in a parent, read from the couple's children.

A switch error in a parent's statistical phasing swaps the parent's two homolog labels
from one site on. Each child of the couple then seems to change the homolog it inherited
from that parent at that site, all of them together, while a real crossover is one
child's own. So where (nearly) all the couple's children switch on one parent's side at
about the same place, the parent's phase switched there: the haplotype blocks swap that
parent's labels back from that point. The children's spurious crossovers disappear, and
a child that did not switch there turns out to have crossed over there.

The genotypes stay as the callset has them. The corrections are kept on the family
(``families.metadata.haplotype_phase_corrections``), and every view that reads a
parent's phase from the genotypes (the lineage colouring, the phased markers) swaps that
parent's alleles at them, so it reads the phase the blocks were built from.

Pure logic: the block builder finds the corrections, the readers apply them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from .data_scope import normalize_chromosome

# A child's switch belongs to a cluster when it lies within this span of the cluster's
# first switch. The children's switches at one phase switch spread out, as each child's
# switch is placed where its own run of agreeing sites begins, which noise delays: in an
# example family the clusters spread up to 1.8 Mb.
PHASE_SWITCH_CLUSTER_SPAN = 2_000_000
# A cluster is read as a phase switch only when fewer than this many clusters of its size
# and spread are expected by chance from the children's other switches on the chromosome.
PHASE_SWITCH_MAX_CHANCE_CLUSTERS = 0.05
# The spread a chance estimate assumes at least, so a tight cluster is not taken as
# impossible by chance.
_MIN_CHANCE_SPREAD = 100_000

FAMILY_METADATA_KEY = "haplotype_phase_corrections"

_AUTOSOMES = frozenset(str(number) for number in range(1, 23))


def is_autosome(chrom: str) -> bool:
    return normalize_chromosome(chrom) in _AUTOSOMES


@dataclass(slots=True, frozen=True)
class PhaseCorrection:
    """The parent ``parent``'s homolog labels are swapped from ``position`` on ``chrom``:
    ``children_switching`` of its ``children`` informative children switched there,
    between ``position`` and ``end``."""

    parent: str
    side: str  # "father" or "mother"
    chrom: str
    position: int
    end: int
    children_switching: int
    children: int

    def as_metadata(self) -> dict[str, Any]:
        return {
            "parent": self.parent,
            "side": self.side,
            "chr": self.chrom,
            "position": self.position,
            "end": self.end,
            "children_switching": self.children_switching,
            "children": self.children,
        }


@dataclass(slots=True, frozen=True)
class PhaseSwitch:
    """A cluster read as a phase switch: the children's switches lie in ``[position, end]``."""

    position: int
    end: int
    children_switching: int


def required_switching_children(children: int) -> int | None:
    """How many of ``children`` informative children must switch together to read a
    phase switch: all of three, all but one of four or more. With fewer than three, a
    phase switch cannot be told from the children's own crossovers, and none is read.

    Crossovers fall about once per 100 Mb per meiosis, so even two children crossing
    over within the same 2 Mb is rare, and four of five, never seen by chance; the one
    left over may have crossed over right there, which the swap then shows."""
    if children < 3:
        return None
    if children == 3:
        return 3
    return max(3, children - 1)


def _at_least(probabilities: Sequence[float], count: int) -> float:
    """The probability that at least ``count`` of independent events with these
    probabilities happen."""
    if count <= 0:
        return 1.0
    # ways[j]: the probability that exactly j of the events so far happened.
    ways = [1.0] + [0.0] * len(probabilities)
    for probability in probabilities:
        for happened in range(len(ways) - 1, 0, -1):
            ways[happened] = ways[happened] * (1 - probability) + ways[happened - 1] * probability
        ways[0] *= 1 - probability
    return sum(ways[count:])


def expected_chance_clusters(
    switches_by_child: dict[str, Sequence[int]],
    extents_by_child: dict[str, int],
    *,
    children_switching: int,
    spread: int,
) -> float:
    """How many clusters of ``children_switching`` children switching within ``spread``
    the children's switches would form by chance if each were the child's own: each
    child's switches, the clustered one among them, taken as falling at random at its
    rate over the stretch it has sites on. Counting every switch errs on the side of no
    correction: a parent's other phase switches on the chromosome raise every rate."""
    rates = {
        child: len(positions) / max(extents_by_child.get(child, 0), 1)
        for child, positions in switches_by_child.items()
    }
    window = 2 * max(spread, _MIN_CHANCE_SPREAD)
    expected = 0.0
    for anchor, positions in switches_by_child.items():
        if not positions:
            continue
        nearby = [1 - math.exp(-rates[child] * window) for child in rates if child != anchor]
        expected += len(positions) * _at_least(nearby, children_switching - 1)
    return expected


def find_phase_switches(
    switches_by_child: dict[str, Sequence[int]],
    *,
    informative_children: int,
    extents_by_child: dict[str, int],
    span: int = PHASE_SWITCH_CLUSTER_SPAN,
) -> list[PhaseSwitch]:
    """The phase switches on one parent's side of a chromosome, from each informative
    child's switch positions on that side and the stretch it has sites on.

    The densest cluster (most children switching within ``span``) is taken first, one
    switch per child (the one nearest the cluster's median, so a child's own crossover
    nearby is left alone), and placed at the earliest of them; then the next, until no
    cluster has enough children (see :func:`required_switching_children`). A cluster as
    likely to arise by chance from the children's other switches as
    ``PHASE_SWITCH_MAX_CHANCE_CLUSTERS`` is not read as one (see
    :func:`expected_chance_clusters`): on a short chromosome, or with many switches,
    children's own crossovers fall together often enough."""
    needed = required_switching_children(informative_children)
    if needed is None:
        return []
    remaining = sorted((int(pos), child) for child, positions in switches_by_child.items() for pos in positions)
    found: list[PhaseSwitch] = []
    while remaining:
        best: tuple[tuple[int, int], list[tuple[int, str]]] | None = None
        for index, (start, _child) in enumerate(remaining):
            window = [(pos, child) for pos, child in remaining[index:] if pos - start <= span]
            children = {child for _pos, child in window}
            if len(children) < needed:
                continue
            key = (len(children), -(window[-1][0] - start))
            if best is None or key > best[0]:
                best = (key, window)
        if best is None:
            break
        window = best[1]
        positions = sorted(pos for pos, _child in window)
        median = positions[len(positions) // 2]
        chosen: dict[str, int] = {}
        for pos, child in window:
            if child not in chosen or abs(pos - median) < abs(chosen[child] - median):
                chosen[child] = pos
        start, end = min(chosen.values()), max(chosen.values())
        chance = expected_chance_clusters(
            switches_by_child, extents_by_child, children_switching=len(chosen), spread=end - start
        )
        if chance < PHASE_SWITCH_MAX_CHANCE_CLUSTERS:
            found.append(PhaseSwitch(position=start, end=end, children_switching=len(chosen)))
        consumed = {(pos, child) for child, pos in chosen.items()}
        remaining = [event for event in remaining if event not in consumed]
    return sorted(found, key=lambda switch: switch.position)


def corrections_from_metadata(value: Any) -> list[dict[str, Any]]:
    """The well-formed corrections of a ``families.metadata`` value."""
    if not isinstance(value, list):
        return []
    corrections: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        parent, chrom, position = item.get("parent"), item.get("chr"), item.get("position")
        if isinstance(parent, str) and isinstance(chrom, str) and isinstance(position, int):
            corrections.append(item)
    return corrections


def corrected_genotype_rows(
    rows: Iterable[tuple[Any, ...]],
    corrections: Iterable[dict[str, Any]],
    *,
    chrom: str,
) -> list[tuple[Any, ...]]:
    """``rows`` (``(pos, ..., sample_ids, gts)``) with each corrected parent's two
    alleles swapped wherever an odd number of its corrections on ``chrom`` lie at or
    before the site: the parents' phase as the haplotype blocks were built from."""
    target = normalize_chromosome(chrom)
    positions_by_parent: dict[str, list[int]] = {}
    for correction in corrections:
        if normalize_chromosome(str(correction.get("chr") or "")) != target:
            continue
        positions_by_parent.setdefault(str(correction["parent"]), []).append(int(correction["position"]))
    rows = list(rows)
    if not positions_by_parent:
        return rows
    for positions in positions_by_parent.values():
        positions.sort()
    first = min(positions[0] for positions in positions_by_parent.values())
    corrected: list[tuple[Any, ...]] = []
    for row in rows:
        pos = int(row[0])
        if pos < first:
            corrected.append(row)
            continue
        sample_ids, gts = row[-2], row[-1]
        swapped: list[str] | None = None
        for parent, positions in positions_by_parent.items():
            if sum(1 for position in positions if position <= pos) % 2 == 0:
                continue
            try:
                index = list(sample_ids).index(parent)
            except ValueError:
                continue
            gt = str(gts[index] or "")
            if "|" not in gt:
                continue
            if swapped is None:
                swapped = list(gts)
            left, right = gt.split("|", 1)
            swapped[index] = f"{right}|{left}"
        corrected.append(row if swapped is None else (*row[:-1], swapped))
    return corrected
