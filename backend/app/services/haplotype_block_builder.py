"""Haplotype blocks from phased (glimpse2) genotypes, built record by record (#528).

Two builders, as the loader chooses. With both parents in the callset, each child's
blocks follow which parental homolog it inherited (segregation haplotypes), a switch
committed only after repeated evidence; the parents' labels are then oriented by the
affected child. Otherwise each sample's blocks follow its own phase sets. Pure logic:
the loader in ``variant_upload_service`` feeds it records and stores what it returns.
Split out of ``upload_family_small_variant_file``.

With segregation haplotypes, a chromosome's transmissions are kept until the chromosome
ends, so that a switch in a parent's phase, which every child of the couple shows at the
same place, is read and undone before the children's blocks are built (see
``haplotype_phase_correction``).

On chrX outside the pseudo-autosomal regions of the family's assembly a male has one X, and
the segregation haplotypes read it by the rule of the phasing QC (``phased_marker_service``,
CLIN-60), with the same helpers. A member recorded as male is read as one copy there: his
call written as one allele (``1``) or as the same allele twice (``1|1``) is that allele, and
a call of two different alleles is no call. A son's X is his mother's: the homolog he
inherited from her is read where she is heterozygous, and he has no paternal side (his
father gives him a Y). A daughter's maternal homolog is read where her father's one allele is
known, and her paternal side is her father's one X: every daughter receives the same one, so
it carries no segregation information and is set rather than read. A member whose sex is not
recorded is read as two copies, as everywhere else. Each block lies in one stretch of chrX:
PAR1, the X between the PARs, or PAR2. A child's homologs are read afresh in each stretch, so
none read in a PAR is carried over the X, and a man's block outside the PARs is marked one
copy (``bed_service._mark_hemizygous_blocks``). On an assembly whose PAR bounds
``sex_chromosomes`` does not list (T2T-CHM13) every member is read as two copies and no block
is cut.
"""

from __future__ import annotations

import bisect
import re
from array import array
from typing import Any

from .data_scope import normalize_chromosome
from .family_metadata_context import FamilyMetadataContext, SampleMetadataContext
from .haplotype_lineage_service import build_pedigree, identify_core
from .haplotype_phase_correction import PhaseCorrection, find_phase_switches, is_autosome
from .pgt_embryo_calls import normalize_sex
from .sex_chromosomes import X_STRETCH_LAST_POSITION, hemizygous_chromosome, x_stretches


# A child's inherited homolog switches only after this many consecutive markers over at
# least this span agree, so a crossover splits the blocks but genotype noise does not.
SEGREGATION_HAPLOTYPE_SWITCH_MIN_MARKERS = 50
SEGREGATION_HAPLOTYPE_SWITCH_MIN_SPAN = 500_000
# A child counts towards reading a parent's phase switch on a chromosome when it has at
# least this many sites where the homolog it inherited from that parent is known.
PHASE_SWITCH_MIN_CHILD_MARKERS = 2 * SEGREGATION_HAPLOTYPE_SWITCH_MIN_MARKERS

# The bytearray code of a site where no homolog was read on a side.
_NO_HAPLOTYPE = 2

# A daughter's paternal side outside the PARs: her father's one X, the homolog his own blocks
# show there (``pgt_embryo_calls.renderable_lanes`` reads a father's one X on ``hap1``).
_FATHER_ONE_X = "0"
# The end of the stretch of chrX after PAR2, which no position reaches.
_LAST_POSITION = X_STRETCH_LAST_POSITION


class _ChildTransmissions:
    """One child's transmissions on one chromosome, in record order: the site, the homolog
    read on each parent's side (``_NO_HAPLOTYPE`` for none) and, where the call has one,
    its phase set. Compact, as a chromosome holds a few hundred thousand sites."""

    __slots__ = ("positions", "paternal", "maternal", "phase_sets")

    def __init__(self) -> None:
        self.positions = array("q")
        self.paternal = bytearray()
        self.maternal = bytearray()
        self.phase_sets: dict[int, int] = {}

    def append(self, pos: int, paternal: str | None, maternal: str | None, ps: int | None) -> None:
        if ps is not None:
            self.phase_sets[len(self.positions)] = ps
        self.positions.append(pos)
        self.paternal.append(_NO_HAPLOTYPE if paternal is None else int(paternal))
        self.maternal.append(_NO_HAPLOTYPE if maternal is None else int(maternal))


def _haplotype_state_end(
    state: dict[str, Any],
    *,
    next_chrom: str | None,
    next_start: int | None,
    chromosome_sizes: dict[str, int],
) -> int:
    state_start = int(state["start"])
    state_last_pos = int(state["last_pos"] or state_start)
    state_chrom = normalize_chromosome(str(state["chr"]))
    if next_chrom is not None and normalize_chromosome(next_chrom) == state_chrom:
        end = max(int(next_start or state_start), state_start + 1)
    else:
        chrom_size = chromosome_sizes.get(state_chrom)
        if chrom_size is not None:
            end = max(chrom_size, state_last_pos + 1)
        else:
            end = max(state_last_pos + 1, state_start + 1)
    end_limit = state.get("end_limit")
    if end_limit is not None:
        # A block on chrX ends with its stretch (PAR1, the X between the PARs, PAR2), so it
        # is marked one copy in males exactly where it lies outside the PARs.
        end = max(min(end, int(end_limit)), state_start)
    return end


def _phased_haplotype_alleles(gt_value: str | None) -> tuple[str, str] | None:
    if not gt_value or "|" not in gt_value:
        return None
    hap1, hap2 = gt_value.split("|", 1)
    if not hap1 or not hap2 or hap1 == "." or hap2 == ".":
        return None
    return hap1, hap2


def _call_alleles(gt_value: str | None) -> tuple[str, ...] | None:
    """The alleles of a call written with one allele (``1``) or two (``0|1``, ``0/1``); None
    for a no-call, a half call (``0|.``) or anything else."""
    alleles = tuple(re.split(r"[|/]", (gt_value or "").strip()))
    if len(alleles) > 2 or not all(allele.isdigit() for allele in alleles):
        return None
    return alleles


def _one_copy_alleles(gt_value: str | None) -> tuple[str, str] | None:
    """A male's call where he has one X, as the pair the Mendelian rules read: one allele
    written alone (``1``) or twice (``1|1``, ``1/1``) is that allele, read as ``(1, 1)``.
    None for a no-call and for a call of two different alleles, which a male cannot
    carry there (``phased_marker_service._two_different_alleles``)."""
    alleles = _call_alleles(gt_value)
    if alleles is None or len(set(alleles)) != 1:
        return None
    return alleles[0], alleles[0]


def is_one_copy_x_site(assembly_name: str | None, chrom: str | None, pos: int) -> bool:
    """Is this site on chrX outside the pseudo-autosomal regions of the assembly, where a
    male carries one copy of the X? False in a PAR, off chrX, and on an assembly whose PAR
    bounds ``sex_chromosomes`` does not list (T2T-CHM13): every member is then read as two
    copies, by the haplotype blocks and the phasing QC alike."""
    return hemizygous_chromosome(assembly_name, chrom, pos) == "X"


def _new_haplotype_state(
    *,
    chrom: str,
    start: int,
    hap1: str,
    hap2: str,
    ps: int | None,
    end_limit: int | None = None,
) -> dict[str, Any]:
    return {
        "start": start,
        "hap1": hap1,
        "hap2": hap2,
        "ps": ps,
        "chr": chrom,
        "last_pos": start,
        # The last position the block may reach: the end of its stretch of chrX.
        "end_limit": end_limit,
    }


def _empty_haplotype_state() -> dict[str, Any]:
    return {
        "start": None,
        "hap1": None,
        "hap2": None,
        "ps": None,
        "chr": None,
        "last_pos": None,
        "end_limit": None,
    }


def _empty_segregation_side_state() -> dict[str, Any]:
    return {
        "chr": None,
        "hap": None,
        "pending_hap": None,
        "pending_start": None,
        "pending_last": None,
        "pending_count": 0,
    }


def _clear_segregation_pending(state: dict[str, Any]) -> None:
    state["pending_hap"] = None
    state["pending_start"] = None
    state["pending_last"] = None
    state["pending_count"] = 0


def _observe_segregation_haplotype(
    state: dict[str, Any],
    *,
    chrom: str,
    start: int,
    hap: str | None,
) -> tuple[int, str] | None:
    if hap not in {"0", "1"}:
        return None
    if state["chr"] != chrom:
        state["chr"] = chrom
        state["hap"] = hap
        _clear_segregation_pending(state)
        return start, hap
    if state["hap"] is None:
        state["hap"] = hap
        _clear_segregation_pending(state)
        return start, hap
    if hap == state["hap"]:
        _clear_segregation_pending(state)
        return None
    if state["pending_hap"] == hap:
        state["pending_count"] = int(state["pending_count"]) + 1
        state["pending_last"] = start
    else:
        state["pending_hap"] = hap
        state["pending_start"] = start
        state["pending_last"] = start
        state["pending_count"] = 1

    pending_start = int(state["pending_start"] or start)
    pending_last = int(state["pending_last"] or start)
    if (
        int(state["pending_count"]) >= SEGREGATION_HAPLOTYPE_SWITCH_MIN_MARKERS
        and pending_last - pending_start >= SEGREGATION_HAPLOTYPE_SWITCH_MIN_SPAN
    ):
        state["hap"] = hap
        _clear_segregation_pending(state)
        return pending_start, hap
    return None


def _confirmed_segregation_haplotype(state: dict[str, Any], chrom: str) -> str:
    if state["chr"] == chrom and state["hap"] in {"0", "1"}:
        return str(state["hap"])
    return "?"


def _haplotype_state_matches_block(
    state: dict[str, Any],
    *,
    chrom: str,
    ps: int | None,
) -> bool:
    if state["chr"] != chrom:
        return False
    if state["ps"] is not None or ps is not None:
        return state["ps"] == ps
    return True


def _haplotype_state_matches_segment(
    state: dict[str, Any],
    *,
    chrom: str,
    hap1: str,
    hap2: str,
    ps: int | None,
) -> bool:
    return (
        state["chr"] == chrom
        and state["hap1"] == hap1
        and state["hap2"] == hap2
        and state["ps"] == ps
    )


def _append_haplotype_state_row(
    rows: list[dict[str, Any]],
    sample_context: SampleMetadataContext,
    state: dict[str, Any],
    *,
    next_chrom: str | None,
    next_start: int | None,
    chromosome_sizes: dict[str, int],
    metadata_json: str,
) -> None:
    if state["start"] is None:
        return
    rows.append(
        _haplotype_row(
            sample_context,
            chrom=str(state["chr"]),
            start=int(state["start"]),
            end=_haplotype_state_end(
                state,
                next_chrom=next_chrom,
                next_start=next_start,
                chromosome_sizes=chromosome_sizes,
            ),
            hap1=str(state["hap1"]),
            hap2=str(state["hap2"]),
            ps=state["ps"],
            metadata_json=metadata_json,
        )
    )


def _update_haplotype_state(
    *,
    states: dict[str, dict[str, Any]],
    rows: list[dict[str, Any]],
    sample_contexts: dict[str, SampleMetadataContext],
    sample_name: str,
    chrom: str,
    start: int,
    hap1: str,
    hap2: str,
    ps: int | None,
    chromosome_sizes: dict[str, int],
    metadata_json: str,
    split_on_haplotype_change: bool,
    end_limit: int | None = None,
) -> None:
    state = states[sample_name]
    if state["start"] is None:
        states[sample_name] = _new_haplotype_state(
            chrom=chrom,
            start=start,
            hap1=hap1,
            hap2=hap2,
            ps=ps,
            end_limit=end_limit,
        )
        return
    if split_on_haplotype_change:
        matches = _haplotype_state_matches_segment(
            state,
            chrom=chrom,
            hap1=hap1,
            hap2=hap2,
            ps=ps,
        )
    else:
        matches = _haplotype_state_matches_block(state, chrom=chrom, ps=ps)
    if matches:
        state["last_pos"] = start
        return
    _append_haplotype_state_row(
        rows,
        sample_contexts[sample_name],
        state,
        next_chrom=chrom,
        next_start=start,
        chromosome_sizes=chromosome_sizes,
        metadata_json=metadata_json,
    )
    states[sample_name] = _new_haplotype_state(
        chrom=chrom,
        start=start,
        hap1=hap1,
        hap2=hap2,
        ps=ps,
        end_limit=end_limit,
    )


def _close_haplotype_state(
    *,
    states: dict[str, dict[str, Any]],
    rows: list[dict[str, Any]],
    sample_contexts: dict[str, SampleMetadataContext],
    sample_name: str,
    next_chrom: str | None,
    next_start: int | None,
    chromosome_sizes: dict[str, int],
    metadata_json: str,
) -> None:
    state = states[sample_name]
    _append_haplotype_state_row(
        rows,
        sample_contexts[sample_name],
        state,
        next_chrom=next_chrom,
        next_start=next_start,
        chromosome_sizes=chromosome_sizes,
        metadata_json=metadata_json,
    )
    states[sample_name] = _empty_haplotype_state()


def _role_first_parent_names(
    context: FamilyMetadataContext,
) -> tuple[str | None, str | None]:
    """First sample (in ``sample_rows`` order) tagged with the flat ``father`` /
    ``mother`` role. Used only as a fallback when the pedigree cannot resolve the
    index couple."""
    father_name: str | None = None
    mother_name: str | None = None
    for row in context.sample_rows:
        role = str(row.get("role") or "").strip().lower()
        sample_name = str(row.get("sample_id") or "")
        if role == "father" and sample_name:
            father_name = father_name or sample_name
        elif role == "mother" and sample_name:
            mother_name = mother_name or sample_name
    return father_name, mother_name


def _couple_children(context: FamilyMetadataContext) -> set[str]:
    """The children of the couple by the pedigree, who alone tell a parent's phase switch
    from crossovers; empty when the pedigree does not name the couple."""
    if not context.relationship_rows:
        return set()
    core = identify_core(build_pedigree(context.sample_rows, context.relationship_rows))
    return set(core.children) if core.father and core.mother else set()


def _parent_sample_names(context: FamilyMetadataContext) -> tuple[str | None, str | None]:
    """Resolve the *index couple* — the father/mother who co-parent the index
    children — so the marker overlay and the upload block builder agree with the
    pedigree-aware lineage path (``identify_core``).

    The flat role model reuses ``father`` / ``mother`` for *any* parent (a paternal
    grandfather and the index father can both be ``role = "father"``), so the naive
    role-first match can pick the wrong individual. We instead derive the index
    parent(s) from the pedigree graph: ``identify_core`` selects the parents of the
    embryos. This returns ``None`` for the donor side of a SINGLE-PARENT family — we
    must preserve that ``None`` (not back-fill it with a role-first match, which would
    grab a grandparent). We fall back to the role-first match only when no index
    parent can be identified from the pedigree at all (missing relationships)."""
    if context.relationship_rows:
        pedigree = build_pedigree(context.sample_rows, context.relationship_rows)
        core = identify_core(pedigree)
        if core.children and (core.father or core.mother):
            return core.father, core.mother
    return _role_first_parent_names(context)


def _transmitted_parent_haplotype(
    parent_alleles: tuple[str, str] | None,
    other_parent_alleles: tuple[str, str] | None,
    child_alleles: tuple[str, str] | None,
) -> str | None:
    if parent_alleles is None or other_parent_alleles is None or child_alleles is None:
        return None
    child_state = tuple(sorted(child_alleles))
    possible: set[int] = set()
    for parent_index, parent_allele in enumerate(parent_alleles):
        for other_allele in other_parent_alleles:
            if tuple(sorted((parent_allele, other_allele))) == child_state:
                possible.add(parent_index)
    if len(possible) != 1:
        return None
    return str(next(iter(possible)))


def _transmitted_single_parent_haplotype(
    parent_alleles: tuple[str, str] | None,
    child_alleles: tuple[str, str] | None,
) -> str | None:
    """With only ONE known parent (donor family), which parent homolog (0/1) the
    child inherited is resolvable only where the parent is heterozygous AND the child
    is homozygous: the child's duplicated allele is the transmitted one and exactly
    one parent homolog carries it. Parent-homozygous or child-heterozygous sites are
    ambiguous — the absent/donor parent could supply either allele — so they yield
    ``None``. A son's one X, read as ``(1, 1)``, is read against his mother so."""
    if parent_alleles is None or child_alleles is None:
        return None
    p0, p1 = parent_alleles
    c0, c1 = child_alleles
    if p0 == p1 or c0 != c1:
        return None
    allele = c0
    if p0 == allele and p1 != allele:
        return "0"
    if p1 == allele and p0 != allele:
        return "1"
    return None


def _flip_parent_haplotype(value: str, *, flip: bool) -> str:
    if not flip:
        return value
    if value == "0":
        return "1"
    if value == "1":
        return "0"
    return value


def _orient_haplotype_rows_by_affected_child(
    rows: list[dict[str, Any]],
    *,
    sample_contexts: dict[str, SampleMetadataContext],
    father_name: str,
    mother_name: str,
    affected_parent_counts: dict[str, dict[str, int]],
) -> None:
    father_counts = affected_parent_counts["father"]
    mother_counts = affected_parent_counts["mother"]
    father_flip = father_counts.get("0", 0) > father_counts.get("1", 0)
    mother_flip = mother_counts.get("0", 0) > mother_counts.get("1", 0)
    if not father_flip and not mother_flip:
        return
    sample_name_by_uuid = {
        sample_context.sample_uuid: sample_name
        for sample_name, sample_context in sample_contexts.items()
    }
    for row in rows:
        sample_name = sample_name_by_uuid.get(str(row.get("sample_id")))
        if sample_name == father_name:
            row["hap1"] = _flip_parent_haplotype(str(row["hap1"]), flip=father_flip)
            row["hap2"] = _flip_parent_haplotype(str(row["hap2"]), flip=father_flip)
        elif sample_name == mother_name:
            row["hap1"] = _flip_parent_haplotype(str(row["hap1"]), flip=mother_flip)
            row["hap2"] = _flip_parent_haplotype(str(row["hap2"]), flip=mother_flip)
        else:
            row["hap1"] = _flip_parent_haplotype(str(row["hap1"]), flip=father_flip)
            row["hap2"] = _flip_parent_haplotype(str(row["hap2"]), flip=mother_flip)


def _haplotype_row(
    sample_context: SampleMetadataContext,
    *,
    chrom: str,
    start: int,
    end: int,
    hap1: str,
    hap2: str,
    ps: int | None,
    metadata_json: str,
) -> dict[str, Any]:
    return {
        "sample_id": sample_context.sample_uuid,
        "family_id": sample_context.family_uuid,
        "assembly_id": sample_context.assembly_id or "",
        "track_type": "haplotype",
        "source": "glimpse2",
        "chr": normalize_chromosome(chrom),
        "start": start,
        "end": end,
        "hap1": hap1,
        "hap2": hap2,
        "ps": ps,
        "metadata_json": metadata_json,
    }


class HaplotypeBlockBuilder:
    """The haplotype blocks of one glimpse2 upload, fed its records in file order.

    ``add_sample`` for each sample column of the ``#CHROM`` line, ``observe`` for each
    record, and ``finish`` for the blocks: the open blocks closed, and with segregation
    haplotypes the parents' labels oriented by the affected child.
    """

    def __init__(
        self,
        *,
        context: FamilyMetadataContext,
        sample_contexts: dict[str, SampleMetadataContext],
        chromosome_sizes: dict[str, int],
        metadata_json: str,
    ) -> None:
        self.sample_contexts = sample_contexts
        self.chromosome_sizes = chromosome_sizes
        self.metadata_json = metadata_json
        self.father_name, self.mother_name = _parent_sample_names(context)
        # Segregation haplotypes need both parents in the callset; otherwise each sample's
        # blocks follow its own phase sets.
        self.use_segregation = (
            self.father_name in sample_contexts and self.mother_name in sample_contexts
        )
        self.affected_sample_names = set(context.affected_sample_names)
        self.affected_parent_counts = {
            "father": {"0": 0, "1": 0},
            "mother": {"0": 0, "1": 0},
        }
        self.rows: list[dict[str, Any]] = []
        self.states: dict[str, dict[str, Any]] = {}
        self.side_states: dict[str, dict[str, dict[str, Any]]] = {}
        # Segregation haplotypes: the current chromosome's transmissions per child, and
        # the parents' phase switches found so far.
        self.couple_children = _couple_children(context)
        self.transmission_chrom: str | None = None
        self.transmissions: dict[str, _ChildTransmissions] = {}
        self.phase_corrections: list[PhaseCorrection] = []
        # Per chromosome, where each parent's phase is swapped back: a chromosome an
        # unsorted file splits keeps the swaps its first part found, as the views do.
        self.phase_flips_by_chrom: dict[str, dict[str, list[int]]] = {}
        # chrX outside the PARs: the members recorded as male have one X there, and a
        # daughter (recorded as female) receives her father's. A member whose sex is not
        # recorded is read as two copies.
        self.assembly_name = context.assembly_name
        sexes = {str(row.get("sample_id")): normalize_sex(row.get("sex")) for row in context.sample_rows}
        self.males = {name for name, sex in sexes.items() if sex == "male"}
        self.females = {name for name, sex in sexes.items() if sex == "female"}
        self._stretches_by_chrom: dict[str, tuple[tuple[int, int], ...] | None] = {}

    def _stretch(self, chrom: str, pos: int) -> tuple[int, int] | None:
        """The stretch of chrX (``x_stretches``) that holds ``pos``; None off chrX and on an
        assembly whose PAR bounds are not known, where no block is cut."""
        if chrom not in self._stretches_by_chrom:
            self._stretches_by_chrom[chrom] = x_stretches(self.assembly_name, chrom)
        stretches = self._stretches_by_chrom[chrom]
        if stretches is None:
            return None
        index = bisect.bisect_right(stretches, (pos, _LAST_POSITION)) - 1
        return stretches[max(index, 0)]

    def _close_block_outside(self, sample_name: str, *, chrom: str, stretch: tuple[int, int]) -> bool:
        """End ``sample_name``'s open block on ``chrom`` when it lies in another stretch of
        chrX than ``stretch``: it ends with its own. Whether a block was ended."""
        state = self.states.get(sample_name)
        if state is None or state["start"] is None or state["chr"] != chrom:
            return False
        if self._stretch(chrom, int(state["start"])) == stretch:
            return False
        _close_haplotype_state(
            states=self.states,
            rows=self.rows,
            sample_contexts=self.sample_contexts,
            sample_name=sample_name,
            next_chrom=chrom,
            next_start=stretch[0],
            chromosome_sizes=self.chromosome_sizes,
            metadata_json=self.metadata_json,
        )
        return True

    def _parent_alleles(self, name: str | None, call: Any, *, one_copy: bool) -> tuple[str, str] | None:
        """A parent's genotype as the transmissions read it: where a male has one X, a
        parent recorded as male is one allele, written once or twice (a call of two
        different alleles is no call); otherwise two phased copies."""
        gt_value = call.gt if call is not None else None
        if one_copy and name in self.males:
            return _one_copy_alleles(gt_value)
        return _phased_haplotype_alleles(gt_value)

    def add_sample(self, name: str) -> None:
        self.states[name] = _empty_haplotype_state()
        self.side_states[name] = {
            "father": _empty_segregation_side_state(),
            "mother": _empty_segregation_side_state(),
        }

    def observe(
        self,
        *,
        chrom: str,
        start: int,
        sample_names: list[str],
        calls: list[Any],
        calls_by_sample: dict[str, Any],
    ) -> None:
        """One record: ``calls`` in sample-column order, ``calls_by_sample`` by sample."""
        if not self.use_segregation:
            for call in calls:
                self._observe_own_phase(call, chrom=chrom, start=start)
        elif self.father_name and self.mother_name:
            self._observe_segregation(
                chrom=chrom,
                start=start,
                sample_names=sample_names,
                calls_by_sample=calls_by_sample,
            )

    def _observe_own_phase(self, call: Any, *, chrom: str, start: int) -> None:
        sample_name = call.sample
        gt_val = call.gt
        state = self.states[sample_name]
        ps_val = call.ps
        phased_alleles = _phased_haplotype_alleles(gt_val)
        if phased_alleles is not None:
            hap1, hap2 = phased_alleles
            _update_haplotype_state(
                states=self.states,
                rows=self.rows,
                sample_contexts=self.sample_contexts,
                sample_name=sample_name,
                chrom=chrom,
                start=start,
                hap1=hap1,
                hap2=hap2,
                ps=ps_val,
                chromosome_sizes=self.chromosome_sizes,
                metadata_json=self.metadata_json,
                split_on_haplotype_change=False,
            )
        elif state["start"] is not None:
            _close_haplotype_state(
                states=self.states,
                rows=self.rows,
                sample_contexts=self.sample_contexts,
                sample_name=sample_name,
                next_chrom=chrom,
                next_start=start,
                chromosome_sizes=self.chromosome_sizes,
                metadata_json=self.metadata_json,
            )

    def _observe_segregation(
        self,
        *,
        chrom: str,
        start: int,
        sample_names: list[str],
        calls_by_sample: dict[str, Any],
    ) -> None:
        father_name, mother_name = self.father_name, self.mother_name
        father_call = calls_by_sample.get(father_name)
        mother_call = calls_by_sample.get(mother_name)
        # On chrX each block lies in one stretch, and outside the PARs a male has one X.
        stretch = self._stretch(chrom, start)
        one_copy = stretch is not None and is_one_copy_x_site(self.assembly_name, chrom, start)
        end_limit = stretch[1] if stretch is not None else None
        father_alleles = self._parent_alleles(father_name, father_call, one_copy=one_copy)
        mother_alleles = self._parent_alleles(mother_name, mother_call, one_copy=one_copy)

        for parent_name, parent_call, parent_alleles in (
            (father_name, father_call, father_alleles),
            (mother_name, mother_call, mother_alleles),
        ):
            if parent_name is None:
                continue
            if stretch is not None:
                self._close_block_outside(parent_name, chrom=chrom, stretch=stretch)
            if parent_call is not None and parent_alleles is not None:
                _update_haplotype_state(
                    states=self.states,
                    rows=self.rows,
                    sample_contexts=self.sample_contexts,
                    sample_name=parent_name,
                    chrom=chrom,
                    start=start,
                    hap1="0",
                    hap2="1",
                    ps=parent_call.ps,
                    chromosome_sizes=self.chromosome_sizes,
                    metadata_json=self.metadata_json,
                    split_on_haplotype_change=True,
                    end_limit=end_limit,
                )

        if chrom != self.transmission_chrom:
            self._build_child_blocks()
            self.transmission_chrom = chrom
        for sample_name in sample_names:
            if sample_name in {father_name, mother_name}:
                continue
            child_call = calls_by_sample.get(sample_name)
            child_gt = child_call.gt if child_call else None
            if one_copy and sample_name in self.males:
                # A son's one X is his mother's: the homolog he inherited from her is read
                # where she is heterozygous. His father gives him a Y: no paternal side.
                paternal_hap = None
                maternal_hap = _transmitted_single_parent_haplotype(mother_alleles, _one_copy_alleles(child_gt))
            else:
                # Two copies: where a daughter's father has one X, his one allele makes her
                # maternal homolog readable, and no homolog of his is told apart.
                child_alleles = _phased_haplotype_alleles(child_gt)
                paternal_hap = _transmitted_parent_haplotype(
                    father_alleles,
                    mother_alleles,
                    child_alleles,
                )
                maternal_hap = _transmitted_parent_haplotype(
                    mother_alleles,
                    father_alleles,
                    child_alleles,
                )
            if paternal_hap is None and maternal_hap is None:
                continue
            self.transmissions.setdefault(sample_name, _ChildTransmissions()).append(
                start, paternal_hap, maternal_hap, child_call.ps if child_call else None
            )

    def _build_child_blocks(self) -> None:
        """The children's blocks of the chromosome whose transmissions are kept: the
        parents' phase switches found and undone, then every child's transmissions fed
        to its blocks in order."""
        chrom = self.transmission_chrom
        transmissions, self.transmissions = self.transmissions, {}
        if chrom is None or not transmissions:
            return
        chrom_key = normalize_chromosome(chrom)
        known_flips = self.phase_flips_by_chrom.get(chrom_key)
        flips: dict[str, list[int]] = known_flips or {"father": [], "mother": []}
        if known_flips is None and is_autosome(chrom):
            for side, parent in (("father", self.father_name), ("mother", self.mother_name)):
                if parent is None:
                    continue
                switches, extents = self._child_switches(chrom, transmissions, side)
                for switch in find_phase_switches(
                    switches, informative_children=len(switches), extents_by_child=extents
                ):
                    flips[side].append(switch.position)
                    self.phase_corrections.append(
                        PhaseCorrection(
                            parent=parent,
                            side=side,
                            chrom=normalize_chromosome(chrom),
                            position=switch.position,
                            end=switch.end,
                            children_switching=switch.children_switching,
                            children=len(switches),
                        )
                    )
        self.phase_flips_by_chrom[chrom_key] = flips
        father_flips, mother_flips = sorted(flips["father"]), sorted(flips["mother"])
        # chrX of an assembly with known PAR bounds is read one stretch at a time.
        on_x = self._stretch(chrom, 1) is not None
        for sample_name, record in transmissions.items():
            stretch: tuple[int, int] | None = None
            for index, pos in enumerate(record.positions):
                if on_x:
                    site_stretch = self._stretch(chrom, pos)
                    if site_stretch != stretch and site_stretch is not None:
                        self._enter_stretch(sample_name, chrom=chrom, stretch=site_stretch)
                        stretch = site_stretch
                # Swapped where an odd number of the parent's corrections lie at or before
                # the site, as corrected_genotype_rows swaps the parent's alleles.
                father_flip = bisect.bisect_right(father_flips, pos) % 2
                mother_flip = bisect.bisect_right(mother_flips, pos) % 2
                paternal = record.paternal[index]
                maternal = record.maternal[index]
                self._observe_child_transmission(
                    chrom=chrom,
                    start=pos,
                    sample_name=sample_name,
                    paternal_hap=None if paternal == _NO_HAPLOTYPE else str(paternal ^ father_flip),
                    maternal_hap=None if maternal == _NO_HAPLOTYPE else str(maternal ^ mother_flip),
                    ps=record.phase_sets.get(index),
                    end_limit=stretch[1] if stretch is not None else None,
                )

    def _enter_stretch(self, sample_name: str, *, chrom: str, stretch: tuple[int, int]) -> None:
        """A child's transmissions reach another stretch of chrX: its open block ends with
        its own stretch, and its homologs are read afresh, as on a new chromosome, so none
        read in a PAR is carried over the X. Outside the PARs a daughter's paternal side is
        her father's one X: every daughter receives the same one, so it is set, not read,
        and it does not orient his labels."""
        state = self.states[sample_name]
        if (
            state["chr"] == chrom
            and state["start"] is not None
            and self._stretch(chrom, int(state["start"])) == stretch
        ):
            return  # its block in this stretch goes on (an unsorted file split the chromosome)
        self._close_block_outside(sample_name, chrom=chrom, stretch=stretch)
        side_states = self.side_states[sample_name]
        side_states["father"] = _empty_segregation_side_state()
        side_states["mother"] = _empty_segregation_side_state()
        if (
            sample_name in self.females
            and self.father_name in self.males
            and is_one_copy_x_site(self.assembly_name, chrom, stretch[0])
        ):
            side_states["father"]["chr"] = chrom
            side_states["father"]["hap"] = _FATHER_ONE_X

    def _child_switches(
        self, chrom: str, transmissions: dict[str, _ChildTransmissions], side: str
    ) -> tuple[dict[str, list[int]], dict[str, int]]:
        """Where each of the couple's children with enough sites on ``side`` to show a
        switch at all switches the homolog it inherited there, by the rule its blocks
        follow, and the stretch its sites cover."""
        switches: dict[str, list[int]] = {}
        extents: dict[str, int] = {}
        for sample_name, record in transmissions.items():
            if self.couple_children and sample_name not in self.couple_children:
                continue
            haplotypes = record.paternal if side == "father" else record.maternal
            state = _empty_segregation_side_state()
            positions: list[int] = []
            sites = 0
            first_site = last_site = 0
            for pos, haplotype in zip(record.positions, haplotypes):
                if haplotype == _NO_HAPLOTYPE:
                    continue
                sites += 1
                if sites == 1:
                    first_site = pos
                last_site = pos
                change = _observe_segregation_haplotype(state, chrom=chrom, start=pos, hap=str(haplotype))
                # The first change is the chromosome's first homolog, not a switch.
                if change is not None and sites > 1:
                    positions.append(change[0])
            if sites >= PHASE_SWITCH_MIN_CHILD_MARKERS:
                switches[sample_name] = positions
                extents[sample_name] = last_site - first_site
        return switches, extents

    def _observe_child_transmission(
        self,
        *,
        chrom: str,
        start: int,
        sample_name: str,
        paternal_hap: str | None,
        maternal_hap: str | None,
        ps: int | None,
        end_limit: int | None = None,
    ) -> None:
        if paternal_hap is None and maternal_hap is None:
            return
        if sample_name in self.affected_sample_names:
            if paternal_hap is not None:
                self.affected_parent_counts["father"][paternal_hap] += 1
            if maternal_hap is not None:
                self.affected_parent_counts["mother"][maternal_hap] += 1
        side_states = self.side_states[sample_name]
        changes: dict[int, dict[str, str]] = {}
        paternal_change = _observe_segregation_haplotype(
            side_states["father"],
            chrom=chrom,
            start=start,
            hap=paternal_hap,
        )
        maternal_change = _observe_segregation_haplotype(
            side_states["mother"],
            chrom=chrom,
            start=start,
            hap=maternal_hap,
        )
        if paternal_change is not None:
            switch_start, confirmed_hap = paternal_change
            changes.setdefault(switch_start, {})["hap1"] = confirmed_hap
        if maternal_change is not None:
            switch_start, confirmed_hap = maternal_change
            changes.setdefault(switch_start, {})["hap2"] = confirmed_hap
        for switch_start, change in sorted(changes.items()):
            state = self.states[sample_name]
            if state["chr"] == chrom and state["start"] is not None:
                hap1 = str(state["hap1"])
                hap2 = str(state["hap2"])
            else:
                hap1 = _confirmed_segregation_haplotype(side_states["father"], chrom)
                hap2 = _confirmed_segregation_haplotype(side_states["mother"], chrom)
            hap1 = change.get("hap1", hap1)
            hap2 = change.get("hap2", hap2)
            if (
                state["chr"] == chrom
                and state["start"] is not None
                and switch_start <= int(state["start"])
            ):
                state["hap1"] = hap1
                state["hap2"] = hap2
                state["ps"] = ps
                continue
            _update_haplotype_state(
                states=self.states,
                rows=self.rows,
                sample_contexts=self.sample_contexts,
                sample_name=sample_name,
                chrom=chrom,
                start=switch_start,
                hap1=hap1,
                hap2=hap2,
                ps=ps,
                chromosome_sizes=self.chromosome_sizes,
                metadata_json=self.metadata_json,
                split_on_haplotype_change=True,
                end_limit=end_limit,
            )
        state = self.states[sample_name]
        if (
            state["chr"] == chrom
            and state["start"] is not None
            and start >= int(state["start"])
        ):
            state["last_pos"] = start

    def finish(self) -> list[dict[str, Any]]:
        if self.use_segregation:
            self._build_child_blocks()
        for sample_name, state in self.states.items():
            if state["start"] is None:
                continue
            _append_haplotype_state_row(
                self.rows,
                self.sample_contexts[sample_name],
                state,
                next_chrom=None,
                next_start=None,
                chromosome_sizes=self.chromosome_sizes,
                metadata_json=self.metadata_json,
            )
        if self.use_segregation and self.father_name and self.mother_name:
            _orient_haplotype_rows_by_affected_child(
                self.rows,
                sample_contexts=self.sample_contexts,
                father_name=self.father_name,
                mother_name=self.mother_name,
                affected_parent_counts=self.affected_parent_counts,
            )
        return self.rows
