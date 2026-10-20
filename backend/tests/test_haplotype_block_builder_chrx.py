"""The haplotype blocks on chrX, where a man has one X outside the pseudo-autosomal regions.

The block builder read every member as two copies. With men's calls written as one allele
(``1``), as GLIMPSE2 writes a haploid call, no child had a transmission on chrX outside the
PARs: the embryos at an X-linked ROI had no blocks there, or their PAR1 blocks ran on over
the whole X with the homologs read in PAR1. Written twice (``1|1``), a son's maternal
homolog was read only where his father's allele was his.

The builder now reads chrX by the rule of the phasing QC (CLIN-60), where
``sex_chromosomes`` knows the PAR bounds of the family's assembly: a member recorded as male
has one copy outside the PARs, his call written once or twice. A son's blocks come from his
mother alone: his maternal homolog is read where she is heterozygous, and he has no
paternal side. A daughter's maternal homolog is read where her father's one allele is
known, and her paternal side is her father's one X, the same in every daughter. A member
whose sex is not recorded is read as two copies. Every block lies in one stretch of chrX:
PAR1, the X between the PARs, or PAR2, so a man's block outside the PARs is marked one copy.

These tests run the real builder on simulated families, and the embryo calls through the
analysis the family page shows (``pgt_analysis``), with only the stores stubbed. Every
identifier and genotype is synthetic.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Any

import pytest

from backend.app.services import (
    bed_service,
    clickhouse_family_variants,
    pgt_analysis,
    phased_marker_service,
)
from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext
from backend.app.services.haplotype_block_builder import HaplotypeBlockBuilder
from backend.app.services.sex_chromosomes import hemizygous_chromosome, hemizygous_interval

MB = 1_000_000
X_SIZE = 156_040_895  # GRCh38 chrX
PAR1_LAST = 2_781_479  # GRCh38 PAR1: 10,001 to 2,781,479
PAR2_FIRST = 155_701_383  # GRCh38 PAR2: 155,701,383 to 156,030,895
ROI = {"roi_query": "DMD", "roi_label": "DMD", "roi_source": "gene", "roi_chr": "X",
       "roi_start": 31_000_000, "roi_end": 31_100_000}

PAR1_SITES = list(range(1_000_000, 2_700_000, 5_000))
ROI_SITES = list(range(30_000_000, 32_000_000, 2_000))
PAR2_SITES = list(range(155_720_000, 156_020_000, 5_000))


@dataclass(frozen=True)
class Child:
    """A child of the couple: its sex (``recorded`` is what its member row says), the
    mother's homolog it carries (``maternal``, switching to the other at ``crossover``), and
    its role and status."""

    sex: str
    maternal: int
    crossover: int | None = None
    recorded: str | None = None
    role: str = "embryo"
    affected: bool = False

    def maternal_at(self, pos: int) -> int:
        return 1 - self.maternal if self.crossover is not None and pos >= self.crossover else self.maternal


@dataclass
class _Call:
    sample: str
    gt: str
    ps: int | None = None


def _man(allele: int, written: str) -> str:
    return str(allele) if written == "once" else f"{allele}|{allele}"


def _genotypes(
    children: dict[str, Child], *, written: str, sites: list[int], seed: int = 11
) -> list[tuple[int, list[str], list[str]]]:
    """The family's calls at ``sites`` on chrX (GRCh38). In a PAR every member has two copies:
    the father his X's (his homolog 0) and his Y's (homolog 1), which a daughter and a son
    inherit. Outside the PARs a man has one X, his call written ``once`` (``1``) or ``twice``
    (``1|1``); a son carries his mother's homolog, a daughter her father's X and hers."""
    rng = random.Random(seed)
    names = ["FATHER", "MOTHER", *children]
    rows = []
    for pos in sites:
        one_copy = hemizygous_chromosome("GRCh38", "X", pos) == "X"
        mother = (rng.randint(0, 1), rng.randint(0, 1))
        father = (rng.randint(0, 1), rng.randint(0, 1))
        gts = {
            "FATHER": _man(father[0], written) if one_copy else f"{father[0]}|{father[1]}",
            "MOTHER": f"{mother[0]}|{mother[1]}",
        }
        for name, child in children.items():
            maternal = mother[child.maternal_at(pos)]
            if not one_copy:
                gts[name] = f"{father[1 if child.sex == 'male' else 0]}|{maternal}"
            elif child.sex == "male":
                gts[name] = _man(maternal, written)
            else:
                gts[name] = f"{father[0]}|{maternal}"
        rows.append((pos, names, [gts[name] for name in names]))
    return rows


def _member_rows(
    children: dict[str, Child], *, father: dict[str, Any] | None = None, mother: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    def row(name: str, role: str, sex: str, affected: bool = False, **status: Any) -> dict[str, Any]:
        return {
            "sample_id": name,
            "role": role,
            "sex": sex,
            "affected": affected,
            "clinical_status": "affected" if affected else "unknown",
            "carrier_status": status.get("carrier_status", "unknown"),
            "carrier_type": status.get("carrier_type"),
        }

    return [
        row("FATHER", "father", **({"sex": "male"} | (father or {}))),
        row("MOTHER", "mother", **({"sex": "female"} | (mother or {}))),
        *(
            row(name, child.role, child.sex if child.recorded is None else child.recorded, child.affected)
            for name, child in children.items()
        ),
    ]


def _context(members: list[dict[str, Any]], *, assembly_name: str = "GRCh38") -> FamilyMetadataContext:
    names = [str(row["sample_id"]) for row in members]
    children = [name for name in names if name not in ("FATHER", "MOTHER")]
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="FAM_X",
        project_ids=["project-uuid"],
        sample_rows=members,
        sample_uuid_to_name={f"{name}-uuid": name for name in names},
        sample_name_to_uuid={name: f"{name}-uuid" for name in names},
        affected_sample_names=[str(row["sample_id"]) for row in members if row["affected"]],
        assembly_id="assembly-uuid",
        assembly_name=assembly_name,
        relationship_rows=[
            {"relationship_type": "parent_child", "sample_id_a": parent, "sample_id_b": child, "role_a": role}
            for child in children
            for parent, role in (("FATHER", "father"), ("MOTHER", "mother"))
        ],
    )


def _build(context: FamilyMetadataContext, rows: list[tuple[int, list[str], list[str]]]) -> list[dict[str, Any]]:
    """The blocks the upload stores, from the real builder fed the records in order."""
    names = list(context.sample_name_to_uuid)
    sample_contexts = {
        name: SampleMetadataContext(
            sample_uuid=f"{name}-uuid",
            sample_id=name,
            family_uuid="family-uuid",
            family_id="FAM_X",
            sex=str(next(row["sex"] for row in context.sample_rows if row["sample_id"] == name)),
            project_ids=["project-uuid"],
            assembly_id="assembly-uuid",
            assembly_name=context.assembly_name,
        )
        for name in names
    }
    builder = HaplotypeBlockBuilder(
        context=context, sample_contexts=sample_contexts, chromosome_sizes={"X": X_SIZE}, metadata_json="{}"
    )
    for name in names:
        builder.add_sample(name)
    for pos, sample_names, gts in rows:
        calls = [_Call(name, gt) for name, gt in zip(sample_names, gts)]
        builder.observe(
            chrom="X", start=pos, sample_names=sample_names, calls=calls, calls_by_sample={c.sample: c for c in calls}
        )
    return builder.finish()


def _blocks(rows: list[dict[str, Any]], name: str) -> list[tuple[int, int, str, str]]:
    return sorted(
        (int(row["start"]), int(row["end"]), str(row["hap1"]), str(row["hap2"]))
        for row in rows
        if row["sample_id"] == f"{name}-uuid"
    )


def _homologs(rows: list[dict[str, Any]], name: str, first: int, last: int) -> tuple[set[str], set[str]]:
    """The paternal and the maternal homologs the member's blocks over ``[first, last]`` name.
    A side not read yet where a block begins ('?', as at a chromosome's first site) names none."""
    blocks = [(hap1, hap2) for start, end, hap1, hap2 in _blocks(rows, name) if start <= last and end >= first]
    return {hap1 for hap1, _ in blocks if hap1 != "?"}, {hap2 for _, hap2 in blocks if hap2 != "?"}


# --- The analysis the family page shows, from the blocks built here ------------------------


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> "_Result":
        return self

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class _Session:
    def __init__(self, model: str) -> None:
        self.family = {**ROI, "pgt": {"inheritance_model": model}}

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        return _Result([self.family] if "FROM families" in str(statement) else [])


def _analysis(
    monkeypatch: pytest.MonkeyPatch,
    context: FamilyMetadataContext,
    genotypes: list[tuple[int, list[str], list[str]]],
    blocks: list[dict[str, Any]],
    *,
    model: str,
):
    """``family_pgt_analysis`` with the stores answering from the blocks and genotypes: the
    haplotype view, its lineage tags and one-copy marks, the calls and the marker QC run as
    they do for the family page."""

    async def fetch_blocks(_assembly: str, *, sample_uuids=None, track_type, start=None, end=None, **_kwargs):
        assert track_type == "haplotype"
        return [
            {"sample_uuid": row["sample_id"], "chr": row["chr"], "start": row["start"], "end": row["end"],
             "record_id": None, "value": None, "origin": None, "hap1": row["hap1"], "hap2": row["hap2"],
             "ps": row["ps"]}
            for row in blocks
            if (not sample_uuids or row["sample_id"] in sample_uuids)
            and (start is None or end is None or (row["start"] <= end and row["end"] >= start))
        ]

    async def fetch_genotypes(_context, *, chrom, start, end, limit, source="glimpse2"):
        return [(pos, "A", "G", names, gts) for pos, names, gts in genotypes if start <= pos <= end]

    monkeypatch.setattr(bed_service, "fetch_interval_track_rows", fetch_blocks)
    monkeypatch.setattr(phased_marker_service, "fetch_interval_track_rows", fetch_blocks)
    monkeypatch.setattr(phased_marker_service, "fetch_imputed_phased_genotypes", fetch_genotypes)
    monkeypatch.setattr(clickhouse_family_variants, "fetch_imputed_phased_genotypes", fetch_genotypes)
    return asyncio.run(pgt_analysis.family_pgt_analysis(_Session(model), context))  # type: ignore[arg-type]


def _calls(analysis) -> dict[str, str]:
    return {embryo.sample: embryo.call for embryo in analysis.embryos}


# --- A son and a daughter outside the PARs -------------------------------------------------

SON_AND_DAUGHTER = {
    "SON": Child("male", maternal=1),
    "DAUGHTER": Child("female", maternal=0),
}


@pytest.mark.parametrize("written", ["once", "twice"])
def test_a_son_is_read_on_his_mother_alone_and_a_daughter_gets_her_father_one_x(written: str) -> None:
    context = _context(_member_rows(SON_AND_DAUGHTER))
    rows = _build(context, _genotypes(SON_AND_DAUGHTER, written=written, sites=ROI_SITES))

    # The son carries his mother's homolog 1 and no father's homolog: his father gave him a Y.
    assert {(hap1, hap2) for _start, _end, hap1, hap2 in _blocks(rows, "SON")} == {("?", "1")}
    # The daughter carries her father's one X (his homolog 0, the lane his own blocks show)
    # and her mother's homolog 0.
    assert {(hap1, hap2) for _start, _end, hap1, hap2 in _blocks(rows, "DAUGHTER")} == {("0", "0")}
    # The father's one X has blocks however his calls are written.
    assert {(hap1, hap2) for _start, _end, hap1, hap2 in _blocks(rows, "FATHER")} == {("0", "1")}
    for name in ("FATHER", "MOTHER", "SON", "DAUGHTER"):
        blocks = _blocks(rows, name)
        assert blocks and blocks[0][0] <= ROI_SITES[100] and blocks[-1][1] >= ROI_SITES[-1], (name, blocks)
        # Outside the PARs to the end: the PGT call reads a male's one X on each of them.
        assert all(hemizygous_interval("GRCh38", "X", start, end) == "X" for start, end, _h1, _h2 in blocks), (
            name, blocks)


def test_a_son_maternal_homolog_is_read_where_his_father_allele_is_not_his() -> None:
    # Written twice and read as two copies, a son's maternal homolog was read only where his
    # father's allele was his: here his father's X carries the other allele at every site.
    children = {"SON": Child("male", maternal=1, crossover=31 * MB)}
    rows: list[tuple[int, list[str], list[str]]] = []
    for index, pos in enumerate(ROI_SITES):
        mother = ("0", "1") if index % 2 == 0 else ("1", "0")
        son = mother[children["SON"].maternal_at(pos)]
        father = "1" if son == "0" else "0"
        rows.append((pos, ["FATHER", "MOTHER", "SON"], [f"{father}|{father}", "|".join(mother), f"{son}|{son}"]))
    blocks = _build(_context(_member_rows(children)), rows)

    # His crossover at 31 Mb shows: homolog 1, then homolog 0.
    assert [(hap1, hap2) for _start, _end, hap1, hap2 in _blocks(blocks, "SON")] == [("?", "1"), ("?", "0")]
    assert _blocks(blocks, "SON")[1][0] == 31 * MB


def test_a_call_of_two_alleles_from_a_man_is_not_read() -> None:
    # A son's call of two different alleles (a genotype he cannot have on his one X) gives no
    # transmission; a father's is no call, so his daughter is not read at that site either.
    clean = _genotypes(SON_AND_DAUGHTER, written="once", sites=ROI_SITES)
    noisy = list(clean)
    for index in range(10, len(clean) - 10, 7):
        pos, names, gts = clean[index]
        gts = list(gts)
        gts[names.index("SON")] = "0|1"
        if index % 2:
            gts[names.index("FATHER")] = "1|0"
        noisy[index] = (pos, names, gts)
    context = _context(_member_rows(SON_AND_DAUGHTER))

    assert _build(context, noisy) == _build(context, clean)


# --- The PAR boundaries -------------------------------------------------------------------


@pytest.mark.parametrize("written", ["once", "twice"])
def test_each_block_lies_in_one_stretch_and_carries_no_par_homolog_over_the_x(written: str) -> None:
    sites = PAR1_SITES + ROI_SITES + PAR2_SITES
    context = _context(_member_rows(SON_AND_DAUGHTER))
    rows = _build(context, _genotypes(SON_AND_DAUGHTER, written=written, sites=sites))

    for name in ("FATHER", "MOTHER", "SON", "DAUGHTER"):
        for start, end, _hap1, _hap2 in _blocks(rows, name):
            # PAR1, the X between the PARs, or PAR2: never across a boundary.
            assert (end <= PAR1_LAST) or (start > PAR1_LAST and end < PAR2_FIRST) or (start >= PAR2_FIRST), (
                name, start, end)
    # In PAR1 the son carries his father's Y copy (homolog 1) and his mother's homolog 1;
    # outside the PARs no father's homolog, though one was read in PAR1; in PAR2 the Y copy.
    assert _homologs(rows, "SON", 1, PAR1_LAST) == ({"1"}, {"1"})
    assert _homologs(rows, "SON", PAR1_LAST + 1, PAR2_FIRST - 1) == (set(), {"1"})
    assert _homologs(rows, "SON", PAR2_FIRST, X_SIZE) == ({"1"}, {"1"})
    # The daughter carries her father's X copy (homolog 0) in the PARs, his one X between them.
    assert _homologs(rows, "DAUGHTER", 1, X_SIZE) == ({"0"}, {"0"})
    between = [block for block in _blocks(rows, "SON") if PAR1_LAST < block[0] < PAR2_FIRST]
    assert between and all(hemizygous_interval("GRCh38", "X", start, end) == "X" for start, end, _h1, _h2 in between)


def test_without_a_man_recorded_no_par_homolog_runs_over_the_x() -> None:
    # Nobody's sex recorded: every member is read as two copies, as before. The men's calls
    # written once give no transmission outside the PARs, so there the children have no
    # blocks rather than their PAR1 homologs carried over the X.
    children = {name: Child(child.sex, child.maternal, recorded="und") for name, child in SON_AND_DAUGHTER.items()}
    context = _context(_member_rows(children, father={"sex": "und"}, mother={"sex": "und"}))
    rows = _build(context, _genotypes(children, written="once", sites=PAR1_SITES + ROI_SITES))

    for name in ("SON", "DAUGHTER"):
        blocks = _blocks(rows, name)
        assert blocks and all(end <= PAR1_LAST for _start, end, _h1, _h2 in blocks), (name, blocks)
        assert blocks[-1][1] == PAR1_LAST, (name, blocks)


def test_a_member_whose_sex_is_not_recorded_is_read_as_two_copies() -> None:
    # Unrecorded sex: a one-allele call is no genotype, and a call written twice is read as
    # a daughter's maternal homolog would be, with no father's X assumed.
    children = {
        "SON": Child("male", maternal=1),
        "UNKNOWN_ONCE": Child("male", maternal=1, recorded="und"),
    }
    context = _context(_member_rows(children))
    once = _build(context, _genotypes(children, written="once", sites=ROI_SITES))
    twice = _build(context, _genotypes(children, written="twice", sites=ROI_SITES))

    assert _blocks(once, "UNKNOWN_ONCE") == []
    assert {(hap1, hap2) for _start, _end, hap1, hap2 in _blocks(twice, "UNKNOWN_ONCE")} == {("?", "1")}
    assert {(hap1, hap2) for _start, _end, hap1, hap2 in _blocks(once, "SON")} == {("?", "1")}


def test_on_an_assembly_without_par_bounds_every_member_has_two_copies() -> None:
    # T2T-CHM13: CoGA does not know its PARs, so a man's one-allele calls are not read and
    # the blocks are not cut, as before.
    context = _context(_member_rows(SON_AND_DAUGHTER), assembly_name="T2T-CHM13v2.0")
    rows = _build(context, _genotypes(SON_AND_DAUGHTER, written="once", sites=ROI_SITES))

    assert _blocks(rows, "SON") == [] and _blocks(rows, "DAUGHTER") == [] and _blocks(rows, "FATHER") == []


def test_a_daughter_father_x_is_not_counted_to_orient_the_father() -> None:
    # Every daughter receives her father's one X: it is no evidence of which of his
    # homologs an affected daughter inherited, so it does not orient his labels. Her
    # mother's homolog she inherited is read, and labelled 1 as an affected child's is.
    children = {"DAUGHTER": Child("female", maternal=0, role="proband", affected=True)}
    context = _context(_member_rows(children))
    names = list(context.sample_name_to_uuid)
    builder = HaplotypeBlockBuilder(
        context=context,
        sample_contexts={
            name: SampleMetadataContext(
                sample_uuid=f"{name}-uuid", sample_id=name, family_uuid="family-uuid", family_id="FAM_X",
                sex="und", project_ids=[], assembly_id="assembly-uuid", assembly_name="GRCh38",
            )
            for name in names
        },
        chromosome_sizes={"X": X_SIZE},
        metadata_json="{}",
    )
    for name in names:
        builder.add_sample(name)
    for pos, sample_names, gts in _genotypes(children, written="once", sites=ROI_SITES):
        calls = [_Call(name, gt) for name, gt in zip(sample_names, gts)]
        builder.observe(chrom="X", start=pos, sample_names=sample_names, calls=calls,
                        calls_by_sample={c.sample: c for c in calls})
    rows = builder.finish()

    assert builder.affected_parent_counts["father"] == {"0": 0, "1": 0}
    assert builder.affected_parent_counts["mother"]["0"] > 0 and builder.affected_parent_counts["mother"]["1"] == 0
    assert {(hap1, hap2) for _start, _end, hap1, hap2 in _blocks(rows, "DAUGHTER")} == {("0", "1")}
    assert {(hap1, hap2) for _start, _end, hap1, hap2 in _blocks(rows, "MOTHER")} == {("1", "0")}


# --- The embryo calls at an X-linked ROI ---------------------------------------------------

XLR_FAMILY = {
    "INDEX": Child("male", maternal=1, role="proband", affected=True),
    "E_SON_RISK": Child("male", maternal=1),
    "E_SON_CLEAR": Child("male", maternal=0),
    "E_DAUGHTER_CARRIER": Child("female", maternal=1),
    "E_DAUGHTER_CLEAR": Child("female", maternal=0),
}


@pytest.mark.parametrize("pars", [False, True], ids=["X only", "with the PARs"])
@pytest.mark.parametrize("written", ["once", "twice"])
def test_x_linked_recessive_embryos_are_called_from_the_blocks(monkeypatch, written: str, pars: bool) -> None:
    # A carrier mother and an affected son: the disease haplotype is the mother's homolog he
    # carries. With men written once no embryo had a block at the ROI; with the PARs in the
    # callset, the affected son's PAR1 block ran over the X and gave two candidates.
    context = _context(_member_rows(XLR_FAMILY, mother={"carrier_status": "carrier", "carrier_type": "obligate"}))
    sites = PAR1_SITES + ROI_SITES + PAR2_SITES if pars else ROI_SITES
    genotypes = _genotypes(XLR_FAMILY, written=written, sites=sites)
    analysis = _analysis(monkeypatch, context, genotypes, _build(context, genotypes), model="XLR")

    assert [(d.origin, d.homolog, d.kind) for d in analysis.disease_haplotypes] == [("maternal", "1", "x-linked")]
    assert _calls(analysis) == {
        "E_SON_RISK": "affected_or_at_risk",
        "E_SON_CLEAR": "unaffected_non_carrier",
        "E_DAUGHTER_CARRIER": "carrier",
        "E_DAUGHTER_CLEAR": "unaffected_non_carrier",
    }
    sons = [embryo for embryo in analysis.embryos if embryo.sample.startswith("E_SON")]
    # A son is called on his one X, his mother's: the side his QC is checked on.
    assert all(embryo.qc_verdict is not None and embryo.qc_verdict.sides == ["maternal"] for embryo in sons)


@pytest.mark.parametrize("written", ["once", "twice"])
def test_x_linked_dominant_from_an_affected_father_reaches_every_daughter_and_no_son(monkeypatch, written: str) -> None:
    # The affected father and his affected daughter share his one X: every daughter carries
    # it and no son does. Read as two copies, the daughter carried no father's homolog.
    children = {
        "INDEX": Child("female", maternal=1, role="proband", affected=True),
        "E_DAUGHTER": Child("female", maternal=0),
        "E_SON": Child("male", maternal=1),
    }
    context = _context(_member_rows(children, father={"affected": True}))
    genotypes = _genotypes(children, written=written, sites=ROI_SITES)
    analysis = _analysis(monkeypatch, context, genotypes, _build(context, genotypes), model="XLD")

    assert [(d.origin, d.homolog, d.kind) for d in analysis.disease_haplotypes] == [("paternal", "0", "x-linked")]
    assert _calls(analysis) == {"E_DAUGHTER": "affected_or_at_risk", "E_SON": "unaffected_non_carrier"}


def test_x_linked_dominant_from_the_mother_is_called_at_the_roi_not_in_par1(monkeypatch) -> None:
    # The affected daughter carries her mother's homolog 0 in PAR1 and homolog 1 from her
    # crossover at 10 Mb on, over the ROI. With men written once nothing was read outside the
    # PARs, so the PAR1 blocks ran over the ROI: the disease haplotype was read in PAR1 and
    # the son, who carries his mother's homolog 0 at the ROI, was called at risk.
    children = {
        "INDEX": Child("female", maternal=0, crossover=10 * MB, role="proband", affected=True),
        "E_SON": Child("male", maternal=0),
        "E_DAUGHTER": Child("female", maternal=1, crossover=15 * MB),
    }
    context = _context(_member_rows(children, mother={"affected": True}))
    genotypes = _genotypes(children, written="once", sites=PAR1_SITES + list(range(3 * MB, 33 * MB, 5_000)))
    analysis = _analysis(monkeypatch, context, genotypes, _build(context, genotypes), model="XLD")

    assert [(d.origin, d.kind) for d in analysis.disease_haplotypes] == [("maternal", "x-linked")]
    [disease] = analysis.disease_haplotypes
    assert _calls(analysis) == {"E_SON": "unaffected_non_carrier", "E_DAUGHTER": "unaffected_non_carrier"}
    son = next(embryo for embryo in analysis.embryos if embryo.sample == "E_SON")
    assert all(homolog.homolog != disease.homolog for homolog in son.inherited if homolog.origin == "maternal")
