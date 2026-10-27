"""The lineage of relatives on chrX, where a man has one X outside the pseudo-autosomal regions.

Every relative's lineage was grey on chrX, whatever its sex, and so was every embryo's in a
one-parent (donor) family (CLIN-12). chrX is now read one stretch at a time, by the PARs of the
family's assembly: a PAR as an autosome, and the X between the PARs by the members' recorded
sexes. There a man's one X is one of a woman's homologs, run by run, whichever of the two is the
parent: his mother's mosaic, switching at her crossovers, or the X he gave his daughter whole.
Nothing passes from a man to his son.

The families are simulated: a couple screening embryos for an X-linked disorder, with the
mother's parents and brother and the father's parents, and two donor families. Every
identifier and genotype is synthetic.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field

import pytest

from backend.app.services import bed_service, clickhouse_family_variants
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.haplotype_lineage_service import (
    MATERNAL,
    PATERNAL,
    UNKNOWN,
    UNTRANSMITTED,
    _one_copy_alleles,
    annotate_lineage,
    one_copy_share_runs,
)

MB = 1_000_000
ASSEMBLY = "GRCh38"
PAR1_LAST = 2_781_479  # GRCh38 PAR1: 10,001 to 2,781,479
PAR1_SITES = list(range(1 * MB, 2_700_000, 2_000))
X_SITES = list(range(3 * MB, 33 * MB, 5_000))  # the X between the PARs
REGION = (0, 34 * MB)

# Where each member's inherited homolog switches (a crossover in the parent's meiosis).
MOTHER_CROSSOVER = 12 * MB  # in the mother's X from her mother
UNCLE_CROSSOVER = 22 * MB  # in the uncle's X from his mother
FATHER_CROSSOVER = 18 * MB  # in the father's X from his mother
EMBRYO_CROSSOVER = 25 * MB


def _member(sample_id: str, role: str, sex: str | None) -> dict:
    return {"sample_id": sample_id, "role": role, "sex": sex, "affected": False}


def _parent_child(parent: str, child: str, role_a: str) -> dict:
    return {"relationship_type": "parent_child", "sample_id_a": parent, "sample_id_b": child, "role_a": role_a}


@dataclass
class Family:
    members: list[dict]
    relationships: list[dict]
    rows: list[tuple[int, list[str], list[str]]] = field(default_factory=list)


def _gt(alleles: tuple[int, ...], *, one_copy_form: str = "{a}") -> str:
    """A call: two copies phased, one written as ``one_copy_form`` (``{a}`` or ``{a}|{a}``)."""
    if len(alleles) == 1:
        return one_copy_form.format(a=alleles[0])
    return f"{alleles[0]}|{alleles[1]}"


def _x_family(
    *,
    seed: int,
    grandfather_related: bool = True,
    one_copy_form: str = "{a}",
    sexes: dict[str, str | None] | None = None,
    error_rate: float = 0.005,
) -> Family:
    """The X-linked PGT family: the maternal grandparents GF and GM, their son UNC and daughter
    M, the paternal grandparents PGF and PGM and their son F, and the couple's embryos E1
    (female) and E2 (male).

    Between the PARs: M's homolog 0 is GM's homolog 0 up to ``MOTHER_CROSSOVER`` and her
    homolog 1 after it, and her homolog 1 is GF's one X (or, not ``grandfather_related``, an
    X of someone else's). UNC's X is GM's homolog 0 up to ``UNCLE_CROSSOVER``, her homolog 1
    after; F's X is PGM's homolog 0 up to ``FATHER_CROSSOVER``, her homolog 1 after. In PAR1
    every member has two copies: M is (GM's 0, GF's 0), F (PGM's 0, PGF's 0), UNC (GM's 0,
    GF's 1). A fraction ``error_rate`` of the alleles is flipped."""
    rng = random.Random(seed)
    recorded = {
        "GF": "male", "GM": "female", "UNC": "male", "M": "female",
        "PGF": "male", "PGM": "female", "F": "male", "E1": "female", "E2": "male",
        **(sexes or {}),
    }
    roles = {"GF": "father", "GM": "mother", "UNC": "relative", "M": "mother", "PGF": "father",
             "PGM": "mother", "F": "father", "E1": "embryo", "E2": "embryo"}
    family = Family(
        members=[_member(name, roles[name], recorded[name]) for name in roles],
        relationships=[
            _parent_child("GF", "M", "father"), _parent_child("GM", "M", "mother"),
            _parent_child("GF", "UNC", "father"), _parent_child("GM", "UNC", "mother"),
            _parent_child("PGF", "F", "father"), _parent_child("PGM", "F", "mother"),
            _parent_child("F", "E1", "father"), _parent_child("M", "E1", "mother"),
            _parent_child("F", "E2", "father"), _parent_child("M", "E2", "mother"),
        ],
    )
    for pos in PAR1_SITES + X_SITES:
        frequency = rng.uniform(0.05, 0.95)

        def draw(frequency: float = frequency) -> int:
            return 1 if rng.random() < frequency else 0

        gm, pgm = (draw(), draw()), (draw(), draw())
        if pos <= PAR1_LAST:
            gf, pgf = (draw(), draw()), (draw(), draw())
            calls = {
                "GF": gf, "GM": gm, "UNC": (gm[0], gf[1]), "M": (gm[0], gf[0]),
                "PGF": pgf, "PGM": pgm, "F": (pgm[0], pgf[0]),
                "E1": (pgm[0], gm[0]), "E2": (pgf[0], gm[0]),
            }
        else:
            gf, pgf, stranger = draw(), draw(), draw()
            m0 = gm[0] if pos < MOTHER_CROSSOVER else gm[1]
            m1 = gf if grandfather_related else stranger
            f = pgm[0] if pos < FATHER_CROSSOVER else pgm[1]
            from_mother = m0 if pos < EMBRYO_CROSSOVER else m1
            calls = {
                "GF": (gf,), "GM": gm, "UNC": (gm[0] if pos < UNCLE_CROSSOVER else gm[1],),
                "M": (m0, m1), "PGF": (pgf,), "PGM": pgm, "F": (f,),
                "E1": (f, from_mother), "E2": (from_mother,),
            }
        gts = []
        for name in roles:
            alleles = tuple(1 - a if rng.random() < error_rate else a for a in calls[name])
            if len(alleles) == 1 and recorded[name] != "male":
                alleles = (alleles[0], alleles[0])  # a man whose sex is not recorded: written twice
            gts.append(_gt(alleles, one_copy_form=one_copy_form))
        family.rows.append((pos, list(roles), gts))
    return family


def _segments(names: list[str]) -> dict[str, list[dict]]:
    """Stored blocks: the parents' homolog labels, the embryos' and the relatives' (junk)."""
    whole = {"chr": "X", "start": REGION[0], "end": REGION[1], "ps": None}
    return {name: [{**whole, "hap1": "0", "hap2": "1"}] for name in names}


def _annotate(family: Family, *, assembly_name: str | None = ASSEMBLY, chrom: str = "X", region=REGION):
    return annotate_lineage(
        sample_rows=family.members,
        relationship_rows=family.relationships,
        segments_by_name=_segments([member["sample_id"] for member in family.members]),
        genotype_rows=family.rows,
        chrom=chrom,
        region_start=region[0],
        region_end=region[1],
        assembly_name=assembly_name,
    )


def _lanes_at(blocks: list[dict], pos: int) -> tuple[tuple[str, str], tuple[str, str]]:
    """``((hap1, hap1 lineage), (hap2, hap2 lineage))`` of the block holding ``pos``."""
    [block] = [block for block in blocks if block["start"] <= pos < block["end"]]
    return (block["hap1"], block["hap1_lineage"]), (block["hap2"], block["hap2_lineage"])


def _coloured_span(blocks: list[dict], lane: str, colour: tuple[str, str], lo: int, hi: int) -> int:
    """How many bases of ``[lo, hi)`` the ``lane`` carries ``colour`` (shade, lineage) over."""
    return sum(
        max(0, min(block["end"], hi) - max(block["start"], lo))
        for block in blocks
        if (block[lane], block[f"{lane}_lineage"]) == colour
    )


@pytest.mark.parametrize("one_copy_form", ["{a}", "{a}|{a}"])
def test_the_mothers_father_carries_her_paternal_x_whole(one_copy_form: str) -> None:
    family = _x_family(seed=1, one_copy_form=one_copy_form)

    grandfather = _annotate(family)["GF"]

    # His one X is the mother's homolog 1, along the X between the PARs, on both lanes: the
    # views draw a man's one X on whichever lane they choose. Before: grey.
    for lane in ("hap1", "hap2"):
        assert _coloured_span(grandfather, lane, ("1", MATERNAL), 3 * MB, 33 * MB) > 0.97 * 30 * MB


def test_the_mothers_mother_shares_her_maternal_x_switching_at_the_mothers_crossover() -> None:
    family = _x_family(seed=2)

    grandmother = _annotate(family)["GM"]

    # Her homolog 0 up to the mother's crossover, her homolog 1 after it, each the mother's
    # homolog 0 (dark green); her other lane grey. Two women: read as on the autosomes.
    assert _lanes_at(grandmother, 8 * MB) == (("0", MATERNAL), ("0", UNTRANSMITTED))
    assert _lanes_at(grandmother, 16 * MB) == (("0", UNTRANSMITTED), ("0", MATERNAL))
    assert _coloured_span(grandmother, "hap1", ("0", MATERNAL), 3 * MB, 11 * MB) > 0.97 * 8 * MB
    assert _coloured_span(grandmother, "hap2", ("0", MATERNAL), 13 * MB, 33 * MB) > 0.97 * 20 * MB


def test_the_mothers_brother_is_coloured_where_he_carries_the_x_she_carries() -> None:
    family = _x_family(seed=3)

    uncle = _annotate(family)["UNC"]

    # His X is his mother's homolog 0 up to his crossover (22 Mb), her homolog 1 after; the
    # mother carries her homolog 0 up to 12 Mb and her homolog 1 after. Where they carry the
    # same one he takes the mother's colour of it, on his one X; between 12 and 22 Mb his X
    # is the grandmother's homolog the mother did not receive: grey. Not through his father:
    # no X passes from a man to his son.
    assert _lanes_at(uncle, 8 * MB) == (("0", MATERNAL), ("0", MATERNAL))
    assert _lanes_at(uncle, 17 * MB) == (("0", UNTRANSMITTED), ("0", UNTRANSMITTED))
    assert _lanes_at(uncle, 28 * MB) == (("0", MATERNAL), ("0", MATERNAL))


def test_the_fathers_mother_carries_his_one_x_switching_at_his_crossover() -> None:
    family = _x_family(seed=4)

    grandmother = _annotate(family)["PGM"]

    # The father's one X is his homolog 0 (dark blue, as his blocks show it on hap1): her
    # homolog 0 carries it up to his crossover (18 Mb), her homolog 1 after; her other lane
    # is grey.
    assert _lanes_at(grandmother, 10 * MB) == (("0", PATERNAL), ("0", UNTRANSMITTED))
    assert _lanes_at(grandmother, 26 * MB) == (("0", UNTRANSMITTED), ("0", PATERNAL))


def test_the_fathers_father_gives_him_no_x_and_is_read_in_par1_only() -> None:
    family = _x_family(seed=5)

    grandfather = _annotate(family)["PGF"]

    # In PAR1 the father has two copies, one of them his father's: read as on an autosome,
    # his homolog 1 (light blue) on the lane of the copy he passed on. Between the PARs he
    # passed none: grey (unknown).
    assert _lanes_at(grandfather, 2 * MB) == (("1", PATERNAL), ("0", UNTRANSMITTED))
    assert _lanes_at(grandfather, 10 * MB) == (("0", UNKNOWN), ("0", UNKNOWN))
    assert _coloured_span(grandfather, "hap1", ("0", UNKNOWN), PAR1_LAST + 1, REGION[1]) == REGION[1] - PAR1_LAST - 1


def test_each_relative_is_read_one_stretch_at_a_time() -> None:
    family = _x_family(seed=6)

    out = _annotate(family)

    # Every block lies in one stretch of chrX: before PAR1, PAR1 or the X after it.
    stretches = [(0, 10_001), (10_001, PAR1_LAST + 1), (PAR1_LAST + 1, REGION[1])]
    for name in ("GF", "GM", "PGF", "PGM", "UNC"):
        blocks = out[name]
        assert blocks[0]["start"] == REGION[0] and blocks[-1]["end"] == REGION[1], name
        for block in blocks:
            assert any(lo <= block["start"] and block["end"] <= hi for lo, hi in stretches), (name, block)
    # The mother's father in PAR1: the copy he gave her, her homolog 1 (light green).
    assert _lanes_at(out["GF"], 2 * MB) == (("1", MATERNAL), ("0", UNTRANSMITTED))


def test_a_man_who_is_not_the_mothers_father_stays_grey() -> None:
    family = _x_family(seed=7, grandfather_related=False)

    grandfather = _annotate(family)["GF"]

    # His allele names one of her homologs at random: the runs agree with about half the pins.
    assert _coloured_span(grandfather, "hap1", ("1", MATERNAL), 3 * MB, 33 * MB) == 0
    assert _coloured_span(grandfather, "hap1", ("0", MATERNAL), 3 * MB, 33 * MB) == 0


def test_a_member_whose_sex_is_not_recorded_is_read_as_two_copies() -> None:
    # The mother's father without a recorded sex: his one X written twice reads as two equal
    # homologs; the mother's side names her homolog 1 throughout, so he is coloured on one
    # lane, as a member with two copies is, and never with another colour.
    family = _x_family(seed=8, sexes={"GF": None})

    grandfather = _annotate(family)["GF"]

    assert _coloured_span(grandfather, "hap1", ("1", MATERNAL), 3 * MB, 33 * MB) > 0.97 * 30 * MB
    for block in grandfather:
        assert (block["hap2"], block["hap2_lineage"]) != ("0", MATERNAL)


@pytest.mark.parametrize(
    ("chrom", "assembly_name"),
    [("X", None), ("X", "T2T-CHM13"), ("Y", ASSEMBLY), ("MT", ASSEMBLY)],
)
def test_chry_chrm_and_chrx_of_an_assembly_whose_pars_are_not_known_stay_grey(chrom: str, assembly_name) -> None:
    family = _x_family(seed=9)

    out = _annotate(family, chrom=chrom, assembly_name=assembly_name)

    for name in ("GF", "GM", "UNC", "PGF", "PGM"):
        assert {(block["hap1_lineage"], block["hap2_lineage"]) for block in out[name]} == {(UNKNOWN, UNKNOWN)}, name
    # The core keeps its role tags.
    assert {block["hap1_lineage"] for block in out["F"]} == {PATERNAL}


# --- a donor family -------------------------------------------------------------------------------


def _donor_family(*, seed: int, known: str) -> Family:
    """One known parent (``mother`` or ``father``) and a donor, a daughter D1 and a son D2.
    Between the PARs a known mother's homolog 0 goes to each child up to
    ``EMBRYO_CROSSOVER``, her homolog 1 after; a known father's one X goes to his daughter."""
    rng = random.Random(seed)
    parent = "KM" if known == "mother" else "KF"
    members = [
        _member(parent, known, "female" if known == "mother" else "male"),
        _member("D1", "embryo", "female"),
        _member("D2", "embryo", "male"),
    ]
    family = Family(
        members=members,
        relationships=[_parent_child(parent, "D1", known), _parent_child(parent, "D2", known)],
    )
    for pos in X_SITES:
        frequency = rng.uniform(0.05, 0.95)

        def draw(frequency: float = frequency) -> int:
            return 1 if rng.random() < frequency else 0

        donor = (draw(), draw())
        if known == "mother":
            km = (draw(), draw())
            from_mother = km[0] if pos < EMBRYO_CROSSOVER else km[1]
            calls = {"KM": km, "D1": (donor[0], from_mother), "D2": (from_mother,)}
        else:
            kf = draw()
            calls = {"KF": (kf,), "D1": (kf, donor[0]), "D2": (donor[1],)}
        family.rows.append((pos, list(calls), [_gt(alleles) for alleles in calls.values()]))
    return family


def test_a_known_mothers_son_carries_her_x_switching_at_her_crossover() -> None:
    out = _annotate(_donor_family(seed=10, known="mother"), region=(3 * MB, 33 * MB))

    # Before: every embryo of a one-parent family was grey on chrX.
    assert _lanes_at(out["D2"], 10 * MB) == (("0", MATERNAL), ("0", MATERNAL))
    assert _lanes_at(out["D2"], 30 * MB) == (("1", MATERNAL), ("1", MATERNAL))
    # His sister: her maternal lane as on an autosome, the donor's grey.
    daughter = out["D1"]
    assert {(block["hap1_lineage"], block["hap2_lineage"]) for block in daughter} <= {
        (UNTRANSMITTED, MATERNAL), (MATERNAL, UNTRANSMITTED)
    }
    assert _coloured_span(daughter, "hap2", ("0", MATERNAL), 3 * MB, 24 * MB) > 0.95 * 21 * MB
    assert _coloured_span(daughter, "hap2", ("1", MATERNAL), 26 * MB, 33 * MB) > 0.95 * 7 * MB


def test_a_known_fathers_daughter_carries_his_one_x_and_his_son_none() -> None:
    out = _annotate(_donor_family(seed=11, known="father"), region=(3 * MB, 33 * MB))

    # His one X, dark blue, on both of his lanes; on his daughter's lane that carries it.
    assert _lanes_at(out["KF"], 10 * MB) == (("0", PATERNAL), ("0", PATERNAL))
    assert _coloured_span(out["D1"], "hap1", ("0", PATERNAL), 3 * MB, 33 * MB) > 0.97 * 30 * MB
    assert {block["hap2_lineage"] for block in out["D1"]} == {UNTRANSMITTED}
    # His son's X is the donor's: grey.
    assert {(block["hap1_lineage"], block["hap2_lineage"]) for block in out["D2"]} == {(UNKNOWN, UNKNOWN)}


# --- the reading of a man's call and of his share ---------------------------------------------------


@pytest.mark.parametrize(
    ("gt", "alleles"),
    [("1", (1, 1)), ("0|0", (0, 0)), ("1/1", (1, 1)), ("0|1", None), (".", None), ("", None), (None, None), ("1|.", None)],
)
def test_a_mans_call_where_he_has_one_x(gt, alleles) -> None:
    assert _one_copy_alleles(gt) == alleles


def test_too_few_pins_cannot_tell() -> None:
    woman = {pos: (0, 1) for pos in range(1_000, 10_000, 1_000)}
    man = {pos: (0, 0) for pos in woman}

    assert one_copy_share_runs(woman, man, start=0) is None  # 9 pins


def test_a_sons_runs_follow_his_mothers_crossover() -> None:
    rng = random.Random(12)
    woman: dict[int, tuple[int, int]] = {}
    man: dict[int, tuple[int, int]] = {}
    for pos in range(1 * MB, 21 * MB, 10_000):
        a, b = rng.randint(0, 1), rng.randint(0, 1)
        woman[pos] = (a, b)
        allele = a if pos < 9 * MB else b
        man[pos] = (allele, allele)

    runs = one_copy_share_runs(woman, man, start=1 * MB)

    assert runs is not None
    assert [idx for _start, idx in runs] == [0, 1]
    assert 9 * MB <= runs[1][0] < 10 * MB


# --- the view ---------------------------------------------------------------------------------------


def test_the_chromosome_view_reads_chrx_by_the_familys_assembly(monkeypatch: pytest.MonkeyPatch) -> None:
    family = _x_family(seed=13)
    names = [member["sample_id"] for member in family.members]
    context = FamilyMetadataContext(
        family_uuid="fam-uuid",
        family_id="XLINKED",
        project_ids=[],
        sample_rows=[{**member, "sample_uuid": f"u-{member['sample_id']}"} for member in family.members],
        sample_uuid_to_name={f"u-{name}": name for name in names},
        sample_name_to_uuid={name: f"u-{name}" for name in names},
        affected_sample_names=[],
        assembly_id="a1",
        assembly_name=ASSEMBLY,
        relationship_rows=family.relationships,
    )

    async def stored_rows(*_args, **_kwargs):
        return [
            {"sample_uuid": f"u-{name}", "chr": "X", "start": REGION[0], "end": REGION[1], "hap1": "0", "hap2": "1"}
            for name in names
        ]

    async def genotypes(_context, *, chrom, start, end, limit):
        return [row for row in family.rows if start <= row[0] <= end]

    async def no_sources(session, context):
        return []

    monkeypatch.setattr(bed_service, "fetch_interval_track_rows", stored_rows)
    monkeypatch.setattr(bed_service, "haplotype_source_rows", no_sources)
    monkeypatch.setattr(clickhouse_family_variants, "fetch_imputed_phased_genotypes", genotypes)

    response = asyncio.run(
        bed_service.get_family_haplotypes_response(None, context=context, chr="X", start=REGION[0], end=REGION[1])
    )

    [grandfather] = [sample for sample in response.samples if sample.sample == "GF"]
    at_10mb = [segment for segment in grandfather.segments if segment.start <= 10 * MB < segment.end]
    assert [(segment.hap1, segment.hap1_lineage, segment.hemizygous_in_males) for segment in at_10mb] == [
        ("1", MATERNAL, True)
    ]
