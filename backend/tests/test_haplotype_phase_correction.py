"""A switch in a parent's phase, read from the couple's children and undone.

A switch error in a parent's phasing makes every child of the couple seem to cross over
at the same place on that parent's side; the haplotype blocks undo it, the family keeps
where, and the views that read the parents' phase from the genotypes swap that parent's
alleles there. These tests pin the reading (enough children, together, not by chance),
the swap, and the blocks of a simulated family. Every identifier and number is synthetic.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass

import pytest

from backend.app.services import phased_marker_service
from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext
from backend.app.services.haplotype_block_builder import HaplotypeBlockBuilder
from backend.app.services.haplotype_phase_correction import (
    PhaseSwitch,
    corrected_genotype_rows,
    corrections_from_metadata,
    expected_chance_clusters,
    find_phase_switches,
    required_switching_children,
)

MB = 1_000_000


@pytest.mark.parametrize(("children", "needed"), [(1, None), (2, None), (3, 3), (4, 3), (5, 4), (8, 7)])
def test_a_phase_switch_needs_all_but_one_child_and_at_least_three(children: int, needed: int | None) -> None:
    assert required_switching_children(children) == needed


def _extents(children: list[str], length: int = 200 * MB) -> dict[str, int]:
    return {child: length for child in children}


def test_children_switching_together_are_read_as_one_phase_switch() -> None:
    children = ["E1", "E2", "E3", "E4", "E5"]
    switches = {
        "E1": [40 * MB, 120 * MB],  # the second is E1's own crossover
        "E2": [40 * MB + 150_000],
        "E3": [40 * MB + 600_000, 41 * MB],
        "E4": [40 * MB + 20_000],
        "E5": [40 * MB + 900_000, 160 * MB],
    }

    found = find_phase_switches(switches, informative_children=5, extents_by_child=_extents(children))

    assert found == [PhaseSwitch(position=40 * MB, end=40 * MB + 900_000, children_switching=5)]


def test_a_cluster_of_all_but_one_child_is_read_and_fewer_are_not() -> None:
    children = ["E1", "E2", "E3", "E4", "E5"]
    four = {"E1": [70 * MB], "E2": [70 * MB + 100_000], "E3": [70 * MB + 300_000], "E4": [70 * MB + 50_000], "E5": [150 * MB]}
    three = {**four, "E4": [10 * MB]}

    assert [switch.children_switching for switch in find_phase_switches(four, informative_children=5, extents_by_child=_extents(children))] == [4]
    assert find_phase_switches(three, informative_children=5, extents_by_child=_extents(children)) == []


def test_two_children_cannot_tell_a_phase_switch_from_crossovers() -> None:
    switches = {"E1": [5 * MB], "E2": [5 * MB]}

    assert find_phase_switches(switches, informative_children=2, extents_by_child=_extents(["E1", "E2"])) == []


def test_clusters_as_likely_by_chance_are_not_read() -> None:
    # Three children with switches every few hundred kilobases: all three fall within
    # a megabase of each other somewhere by chance.
    children = ["E1", "E2", "E3"]
    switches = {child: [index * 700_000 + offset for index in range(1, 7)] for child, offset in zip(children, (0, 200_000, 400_000))}

    chance = expected_chance_clusters(switches, _extents(children, 5 * MB), children_switching=3, spread=400_000)

    assert chance > 1
    assert find_phase_switches(switches, informative_children=3, extents_by_child=_extents(children, 5 * MB)) == []


def test_a_childs_own_crossover_next_to_the_phase_switch_is_left_alone() -> None:
    # E1 crossed over 1.5 Mb before the phase switch: its switch nearest the others' is taken.
    children = ["E1", "E2", "E3", "E4"]
    switches = {
        "E1": [58_500_000, 60_000_000 + 80_000],
        "E2": [60_000_000],
        "E3": [60_000_000 + 40_000],
        "E4": [60_000_000 + 120_000],
    }

    found = find_phase_switches(switches, informative_children=4, extents_by_child=_extents(children))

    assert found == [PhaseSwitch(position=60_000_000, end=60_000_000 + 120_000, children_switching=4)]


def test_the_parents_alleles_are_swapped_from_each_correction_on() -> None:
    rows = [
        (100, ["F", "M", "C"], ["0|1", "1|0", "0|1"]),
        (200, ["F", "M", "C"], ["0|1", "1|0", "0|1"]),
        (300, ["F", "M", "C"], ["0|1", "1|0", "0|1"]),
        (400, ["F", "M", "C"], ["0/1", "1|0", "0|1"]),
    ]
    corrections = [
        {"parent": "F", "chr": "1", "position": 150},
        {"parent": "F", "chr": "1", "position": 250},  # swapped back
        {"parent": "M", "chr": "2", "position": 0},  # another chromosome
    ]

    corrected = corrected_genotype_rows(rows, corrections, chrom="chr1")

    assert [row[-1] for row in corrected] == [
        ["0|1", "1|0", "0|1"],
        ["1|0", "1|0", "0|1"],
        ["0|1", "1|0", "0|1"],
        ["0/1", "1|0", "0|1"],  # unphased: nothing to swap
    ]
    # The fetched rows themselves are left as called.
    assert rows[1][-1] == ["0|1", "1|0", "0|1"]


def test_only_well_formed_corrections_are_read_from_the_family() -> None:
    value = [{"parent": "F", "chr": "1", "position": 10}, {"parent": "F", "chr": "1"}, "junk"]

    assert corrections_from_metadata(value) == [{"parent": "F", "chr": "1", "position": 10}]
    assert corrections_from_metadata({"not": "a list"}) == []


# ---------------------------------------------------------------------------
# The blocks of a simulated family
# ---------------------------------------------------------------------------

EMBRYOS = ["E1", "E2", "E3", "E4", "E5"]


@dataclass
class _Call:
    sample: str
    gt: str
    ps: int | None = None


def _builder() -> HaplotypeBlockBuilder:
    names = ["F", "M", *EMBRYOS]
    context = FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="FAM1",
        project_ids=[],
        sample_rows=[
            {"sample_id": name, "role": "father" if name == "F" else "mother" if name == "M" else "embryo"}
            for name in names
        ],
        sample_uuid_to_name={f"{name}-uuid": name for name in names},
        sample_name_to_uuid={name: f"{name}-uuid" for name in names},
        affected_sample_names=[],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
        relationship_rows=[
            {"relationship_type": "parent_child", "sample_id_a": parent, "sample_id_b": child, "role_a": role}
            for child in EMBRYOS
            for parent, role in (("F", "father"), ("M", "mother"))
        ],
    )
    sample_contexts = {
        name: SampleMetadataContext(
            sample_uuid=f"{name}-uuid",
            sample_id=name,
            family_uuid="family-uuid",
            family_id="FAM1",
            sex="und",
            project_ids=[],
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
        )
        for name in names
    }
    builder = HaplotypeBlockBuilder(
        context=context, sample_contexts=sample_contexts, chromosome_sizes={"1": 60 * MB}, metadata_json="{}"
    )
    for name in names:
        builder.add_sample(name)
    return builder


def _simulate(
    builder: HaplotypeBlockBuilder,
    *,
    phase_switch_at: int | None,
    crossover: tuple[str, int] | None,
    split_at: int | None = None,
) -> None:
    """Feed a 60 Mb chromosome: the father's genotypes swap their phase from
    ``phase_switch_at`` on (a switch error of his phasing), and the embryo ``crossover``
    names crosses over on the father's side there; one call in 200 is wrong. With
    ``split_at``, the records from there on come after another chromosome's, as in an
    unsorted file."""
    rng = random.Random(4)
    paternal = {embryo: rng.randint(0, 1) for embryo in EMBRYOS}
    maternal = {embryo: rng.randint(0, 1) for embryo in EMBRYOS}
    names = ["F", "M", *EMBRYOS]
    later: list[tuple[int, list[_Call]]] = []
    for pos in range(5_000, 60 * MB, 5_000):
        father = (rng.randint(0, 1), rng.randint(0, 1))
        mother = (rng.randint(0, 1), rng.randint(0, 1))
        gts = {}
        for embryo in EMBRYOS:
            homolog = paternal[embryo]
            if crossover and embryo == crossover[0] and pos >= crossover[1]:
                homolog = 1 - homolog
            alleles = [father[homolog], mother[maternal[embryo]]]
            if rng.random() < 0.005:
                alleles[0] = 1 - alleles[0]
            gts[embryo] = f"{alleles[0]}|{alleles[1]}"
        shown_father = father[::-1] if phase_switch_at is not None and pos >= phase_switch_at else father
        gts["F"] = f"{shown_father[0]}|{shown_father[1]}"
        gts["M"] = f"{mother[0]}|{mother[1]}"
        calls = [_Call(name, gts[name]) for name in names]
        if split_at is not None and pos >= split_at:
            later.append((pos, calls))
            continue
        builder.observe(chrom="1", start=pos, sample_names=names, calls=calls, calls_by_sample={c.sample: c for c in calls})
    if later:
        between = [_Call(name, "0|1") for name in names]
        builder.observe(chrom="2", start=1_000, sample_names=names, calls=between, calls_by_sample={c.sample: c for c in between})
        for pos, calls in later:
            builder.observe(chrom="1", start=pos, sample_names=names, calls=calls, calls_by_sample={c.sample: c for c in calls})


def _paternal_switches(rows: list[dict], embryo: str) -> list[int]:
    blocks = sorted((row for row in rows if row["sample_id"] == f"{embryo}-uuid"), key=lambda row: row["start"])
    return [right["start"] for left, right in zip(blocks, blocks[1:]) if left["hap1"] != right["hap1"]]


def test_a_phase_switch_in_the_father_is_undone_and_a_real_crossover_kept() -> None:
    builder = _builder()
    _simulate(builder, phase_switch_at=30 * MB, crossover=("E3", 45 * MB))

    rows = builder.finish()

    [correction] = builder.phase_corrections
    assert (correction.parent, correction.side, correction.chrom) == ("F", "father", "1")
    assert 30 * MB <= correction.position < 30 * MB + 500_000
    assert (correction.children_switching, correction.children) == (5, 5)
    for embryo in EMBRYOS:
        switches = _paternal_switches(rows, embryo)
        if embryo == "E3":
            assert len(switches) == 1 and abs(switches[0] - 45 * MB) < 500_000, switches
        else:
            assert switches == [], (embryo, switches)


def test_a_chromosome_an_unsorted_file_splits_keeps_its_phase_corrections() -> None:
    builder = _builder()
    _simulate(builder, phase_switch_at=30 * MB, crossover=None, split_at=40 * MB)

    rows = builder.finish()

    # One correction, found in the first part; the second part is swapped as well, as the
    # views swap every site after it.
    assert [(c.chrom, 30 * MB <= c.position < 30 * MB + 500_000) for c in builder.phase_corrections] == [("1", True)]
    for embryo in EMBRYOS:
        chrom1 = [row for row in rows if row["sample_id"] == f"{embryo}-uuid" and row["chr"] == "1"]
        assert len({row["hap1"] for row in chrom1}) == 1, (embryo, [(r["start"], r["hap1"]) for r in chrom1])


def test_without_a_phase_switch_the_embryos_keep_their_own_crossovers() -> None:
    builder = _builder()
    _simulate(builder, phase_switch_at=None, crossover=("E3", 45 * MB))

    rows = builder.finish()

    assert builder.phase_corrections == []
    assert len(_paternal_switches(rows, "E3")) == 1
    assert all(_paternal_switches(rows, embryo) == [] for embryo in EMBRYOS if embryo != "E3")


# ---------------------------------------------------------------------------
# The phased markers read the corrected phase
# ---------------------------------------------------------------------------


def test_the_phased_markers_read_the_corrected_phase_and_show_the_genotypes_as_called(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        (1000, "A", "G", ["FATHER", "MOTHER", "CHILD"], ["0|1", "0|0", "0|0"]),
        (2000, "C", "T", ["FATHER", "MOTHER", "CHILD"], ["0|1", "0|0", "1|0"]),
    ]

    async def fetch(context, *, chrom, start, end, limit):
        return rows

    async def shade_maps(context, *, chr, father, mother, start, end):
        return {}, {}

    monkeypatch.setattr(phased_marker_service, "fetch_imputed_phased_genotypes", fetch)
    monkeypatch.setattr(phased_marker_service, "_parent_block_shade_maps", shade_maps)
    context = FamilyMetadataContext(
        family_uuid="fam-uuid",
        family_id="fam",
        project_ids=[],
        sample_rows=[
            {"sample_id": "FATHER", "role": "father"},
            {"sample_id": "MOTHER", "role": "mother"},
            {"sample_id": "CHILD", "role": "embryo"},
        ],
        sample_uuid_to_name={},
        sample_name_to_uuid={"FATHER": "f-uuid", "MOTHER": "m-uuid", "CHILD": "c-uuid"},
        affected_sample_names=[],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
        relationship_rows=[
            {"relationship_type": "parent_child", "sample_id_a": "FATHER", "sample_id_b": "CHILD", "role_a": "father"},
            {"relationship_type": "parent_child", "sample_id_a": "MOTHER", "sample_id_b": "CHILD", "role_a": "mother"},
        ],
        phase_corrections=[{"parent": "FATHER", "chr": "1", "position": 1500}],
    )

    response = asyncio.run(
        phased_marker_service.get_family_phased_markers_response(context, chr="1", start=0, end=10_000)
    )

    child = next(sample for sample in response.samples if sample.sample == "CHILD")
    # The child inherited the father's first homolog at both sites once his phase is
    # swapped back from 1500; uncorrected, the second site would read as a switch.
    assert [marker.hap1 for marker in child.markers] == [0, 0]
    father = next(sample for sample in response.samples if sample.sample == "FATHER")
    assert [(marker.hap1, marker.hap2) for marker in father.markers] == [(0, 1), (1, 0)]
    # The tooltip shows the genotypes as called.
    assert response.sites[1].gts[0] == "0|1"
