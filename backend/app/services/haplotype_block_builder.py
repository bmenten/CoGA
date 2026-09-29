"""Haplotype blocks from phased (glimpse2) genotypes, built record by record (#528).

Two builders, as the loader chooses. With both parents in the callset, each child's
blocks follow which parental homolog it inherited (segregation haplotypes), a switch
committed only after repeated evidence; the parents' labels are then oriented by the
affected child. Otherwise each sample's blocks follow its own phase sets. Pure logic:
the loader in ``variant_upload_service`` feeds it records and stores what it returns.
Split out of ``upload_family_small_variant_file``.
"""

from __future__ import annotations

from typing import Any

from .data_scope import normalize_chromosome
from .family_metadata_context import FamilyMetadataContext, SampleMetadataContext
from .haplotype_lineage_service import build_pedigree, identify_core


# A child's inherited homolog switches only after this many consecutive markers over at
# least this span agree, so a crossover splits the blocks but genotype noise does not.
SEGREGATION_HAPLOTYPE_SWITCH_MIN_MARKERS = 50
SEGREGATION_HAPLOTYPE_SWITCH_MIN_SPAN = 500_000


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
        return max(int(next_start or state_start), state_start + 1)
    chrom_size = chromosome_sizes.get(state_chrom)
    if chrom_size is not None:
        return max(chrom_size, state_last_pos + 1)
    return max(state_last_pos + 1, state_start + 1)


def _phased_haplotype_alleles(gt_value: str | None) -> tuple[str, str] | None:
    if not gt_value or "|" not in gt_value:
        return None
    hap1, hap2 = gt_value.split("|", 1)
    if not hap1 or not hap2 or hap1 == "." or hap2 == ".":
        return None
    return hap1, hap2


def _new_haplotype_state(
    *,
    chrom: str,
    start: int,
    hap1: str,
    hap2: str,
    ps: int | None,
) -> dict[str, Any]:
    return {
        "start": start,
        "hap1": hap1,
        "hap2": hap2,
        "ps": ps,
        "chr": chrom,
        "last_pos": start,
    }


def _empty_haplotype_state() -> dict[str, Any]:
    return {
        "start": None,
        "hap1": None,
        "hap2": None,
        "ps": None,
        "chr": None,
        "last_pos": None,
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
) -> None:
    state = states[sample_name]
    if state["start"] is None:
        states[sample_name] = _new_haplotype_state(
            chrom=chrom,
            start=start,
            hap1=hap1,
            hap2=hap2,
            ps=ps,
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
        father_alleles = _phased_haplotype_alleles(father_call.gt if father_call else None)
        mother_alleles = _phased_haplotype_alleles(mother_call.gt if mother_call else None)

        if father_call is not None and father_alleles is not None:
            _update_haplotype_state(
                states=self.states,
                rows=self.rows,
                sample_contexts=self.sample_contexts,
                sample_name=father_name,
                chrom=chrom,
                start=start,
                hap1="0",
                hap2="1",
                ps=father_call.ps,
                chromosome_sizes=self.chromosome_sizes,
                metadata_json=self.metadata_json,
                split_on_haplotype_change=True,
            )
        if mother_call is not None and mother_alleles is not None:
            _update_haplotype_state(
                states=self.states,
                rows=self.rows,
                sample_contexts=self.sample_contexts,
                sample_name=mother_name,
                chrom=chrom,
                start=start,
                hap1="0",
                hap2="1",
                ps=mother_call.ps,
                chromosome_sizes=self.chromosome_sizes,
                metadata_json=self.metadata_json,
                split_on_haplotype_change=True,
            )

        for sample_name in sample_names:
            if sample_name in {father_name, mother_name}:
                continue
            child_call = calls_by_sample.get(sample_name)
            child_alleles = _phased_haplotype_alleles(child_call.gt if child_call else None)
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
                    state["ps"] = child_call.ps if child_call else None
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
                    ps=child_call.ps if child_call else None,
                    chromosome_sizes=self.chromosome_sizes,
                    metadata_json=self.metadata_json,
                    split_on_haplotype_change=True,
                )
            state = self.states[sample_name]
            if (
                state["chr"] == chrom
                and state["start"] is not None
                and start >= int(state["start"])
            ):
                state["last_pos"] = start

    def finish(self) -> list[dict[str, Any]]:
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
