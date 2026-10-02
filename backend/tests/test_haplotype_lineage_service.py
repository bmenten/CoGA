"""Tests for pedigree-aware haplotype lineage colouring.

The motivating case is a three-generation family with a dominant disorder: the
affected paternal grandmother (stored with the overloaded role ``mother``) must
show one haplotype matching the affected father's shared homolog and the other
greyed, instead of the old two-green rendering.
"""
import pytest

from backend.app.services.haplotype_lineage_service import (
    GREY,
    MATERNAL,
    MIN_HOMOLOG_MARGIN,
    MIN_INFORMATIVE_PER_MB,
    PATERNAL,
    UNKNOWN,
    UNTRANSMITTED,
    HomologAssignment,
    HomologResolver,
    MatchResult,
    NuclearCore,
    _core_homologs,
    _homolog_consistency,
    _segment_relative_blocks,
    _smooth_runs,
    annotate_lineage,
    build_pedigree,
    founder_shade_map,
    identify_core,
    match_shared_homolog,
)


def _member(sample_id, role, affected=False):
    return {"sample_id": sample_id, "role": role, "affected": affected}


def _parent_child(parent, child, role_a):
    return {
        "relationship_type": "parent_child",
        "sample_id_a": parent,
        "sample_id_b": child,
        "role_a": role_a,
    }


# A three-generation pedigree: GM -> F -> E1, plus the unaffected mother M.
THREE_GEN_MEMBERS = [
    _member("F", "father", affected=True),
    _member("M", "mother", affected=False),
    _member("GM", "mother", affected=True),  # paternal grandmother, role overloaded
    _member("E1", "embryo"),
]
THREE_GEN_RELATIONSHIPS = [
    _parent_child("F", "E1", "father"),
    _parent_child("M", "E1", "mother"),
    _parent_child("GM", "F", "mother"),
]

# Sites where the grandmother's raw homolog 0 is identical-by-descent with the
# father's raw homolog 0 (the haplotype she transmitted to him), while her other
# homolog and the father's grandfather-derived homolog are unrelated.
# Each entry: (gm_raw0, gm_raw1, father_raw1). father_raw0 == gm_raw0 (the shared
# haplotype).
#
# The match is now confirmed from *phase-informative* (opposite-zygosity) sites
# only — one individual heterozygous, the other homozygous — so double-het sites
# (which carry no transmission direction) no longer count. We therefore supply
# both kinds of informative sites in sufficient number (>= MIN_INFORMATIVE_SITES on
# each resolving side):
#   * anchor-informative: grandmother homozygous, father heterozygous
#     -> pins the father's shared homolog (raw0).
#   * relative-informative: grandmother heterozygous, father homozygous
#     -> pins the grandmother's shared homolog (raw0).
# Intent is unchanged from the original 12-site fixture: gm raw0 == father raw0.
_SITES = (
    [(g, g, 1 - g) for g in (0, 1) for _ in range(6)]  # gm hom, father het (anchor side)
    + [(g, 1 - g, g) for g in (0, 1) for _ in range(6)]  # gm het, father hom (relative side)
)


def _three_gen_genotypes():
    rows = []
    for i, (g0, g1, f1) in enumerate(_SITES):
        pos = 1000 + i * 100
        gts = {
            "F": f"{g0}|{f1}",   # father raw0 shared with grandmother, raw1 from grandfather
            "GM": f"{g0}|{g1}",  # grandmother raw0 is the transmitted homolog
            "M": "0|0",
            "E1": f"{g0}|0",
        }
        rows.append((pos, list(gts.keys()), list(gts.values())))
    return rows


def _parent_segments(hap1, hap2, chrom="1", start=1000, end=2100):
    return [{"chr": chrom, "start": start, "end": end, "hap1": hap1, "hap2": hap2, "ps": None}]


def test_build_pedigree_edges():
    ped = build_pedigree(THREE_GEN_MEMBERS, THREE_GEN_RELATIONSHIPS)
    assert ped.parents_of["F"] == {"mother": "GM"}
    assert ped.parents_of["E1"] == {"father": "F", "mother": "M"}
    assert ped.children_of["F"] == {"E1"}
    assert ped.children_of["GM"] == {"F"}
    assert ped.neighbors("F") == {"GM", "E1"}


def test_identify_core_excludes_grandmother():
    ped = build_pedigree(THREE_GEN_MEMBERS, THREE_GEN_RELATIONSHIPS)
    core = identify_core(ped)
    assert core.father == "F"
    assert core.mother == "M"  # the co-parent of the embryo, NOT the grandmother
    assert core.children == {"E1"}
    assert "GM" not in core.members


def test_identify_core_single_parent_donor_family():
    # Donor (single-parent) family: the embryos have only a father; the disorder
    # segregates via the affected paternal grandmother (role "mother"). The donor
    # (maternal) side is None and the grandmother is a relative, not a core parent.
    members = [
        _member("F", "father"),
        _member("GM", "mother", affected=True),  # paternal grandmother
        _member("E1", "embryo"),
        _member("E2", "embryo"),
    ]
    rels = [
        _parent_child("F", "E1", "father"),
        _parent_child("F", "E2", "father"),
        _parent_child("GM", "F", "mother"),  # grandmother -> father (no embryo mother)
    ]
    core = identify_core(build_pedigree(members, rels))
    assert core.father == "F"
    assert core.mother is None
    assert core.children == {"E1", "E2"}
    assert "GM" not in core.members


def _single_parent_genotypes():
    """Father + one embryo, NO mother (donor family). The embryo's raw homolog 0 is
    the one inherited from the father (shared); homolog 1 is the donor's (unrelated).
    Supplies both kinds of phase-informative sites so the IBD match confirms."""
    rows = []
    i = 0
    for g in (0, 1):  # anchor-informative: father het, embryo homozygous (shared allele)
        for _ in range(6):
            rows.append((1000 + i * 100, ["F", "E1"], [f"{g}|{1 - g}", f"{g}|{g}"]))
            i += 1
    for g in (0, 1):  # relative-informative: father homozygous, embryo het (shared|donor)
        for _ in range(6):
            rows.append((1000 + i * 100, ["F", "E1"], [f"{g}|{g}", f"{g}|{1 - g}"]))
            i += 1
    return rows


def test_annotate_lineage_single_parent_greys_donor_lane():
    # Donor family: the embryo's known-parent (paternal) lane is coloured and the
    # donor lane is greyed; the lone known parent shows both its homologs.
    members = [_member("F", "father", affected=True), _member("E1", "embryo")]
    rels = [_parent_child("F", "E1", "father")]  # one known parent, donor absent
    segs = {
        "F": _parent_segments("0", "1"),
        "E1": [{"chr": "1", "start": 1000, "end": 3500, "hap1": "0", "hap2": "0", "ps": None}],
    }
    out = annotate_lineage(
        sample_rows=members,
        relationship_rows=rels,
        segments_by_name=segs,
        genotype_rows=_single_parent_genotypes(),
        chrom="1",
        region_start=1000,
        region_end=3500,
    )
    # Known parent: both lanes paternal (its two homologs).
    assert all(s["hap1_lineage"] == PATERNAL and s["hap2_lineage"] == PATERNAL for s in out["F"])
    # Embryo: exactly one paternal (known-parent) lane and one untransmitted donor lane.
    assert out["E1"]
    for seg in out["E1"]:
        lanes = {seg["hap1_lineage"], seg["hap2_lineage"]}
        assert lanes == {PATERNAL, UNTRANSMITTED}


def test_founder_shade_map_reads_stored_lane_values():
    # No orientation flip: raw homolog 0 -> shade 0, raw homolog 1 -> shade 1.
    assert founder_shade_map(_parent_segments("0", "1")) == {0: 0, 1: 1}
    # Flipped father: stored values swapped -> raw 0 shows light, raw 1 shows dark.
    assert founder_shade_map(_parent_segments("1", "0")) == {0: 1, 1: 0}


def test_match_shared_homolog_identifies_transmitted_pair():
    gm = {pos: (g0, g1) for (pos, _ids, _gts), (g0, g1, _f1) in zip(_three_gen_genotypes(), _SITES)}
    father = {pos: (g0, f1) for (pos, _ids, _gts), (g0, _g1, f1) in zip(_three_gen_genotypes(), _SITES)}
    match = match_shared_homolog(gm, father)
    assert match is not None
    assert match.relative_idx == 0  # grandmother's transmitted homolog
    assert match.anchor_idx == 0  # father's grandmother-derived homolog
    assert match.confidence > 0.9


def test_match_returns_none_for_unrelated_homozygous_region():
    # Both individuals homozygous-ref everywhere: nothing distinguishes the homologs.
    flat = {1000 + i: (0, 0) for i in range(20)}
    assert match_shared_homolog(flat, dict(flat)) is None


def test_match_rejects_het_heavy_unrelated_pair():
    """H5(B): two unrelated samples that are double-heterozygous everywhere must NOT
    confirm a share. Every site is a biallelic double-het — under the old
    ``allele in other`` scoring both homologs read 1.0 (a false confirm); restricting
    to phase-informative (opposite-zygosity) sites leaves zero informative sites, so
    there is no signal and the match is rejected."""
    het = {1000 + i * 1000: (0, 1) for i in range(40)}
    assert match_shared_homolog(dict(het), dict(het)) is None
    # And the phase-informative consistency itself sees no informative sites here.
    cons, informative = _homolog_consistency(het, het, list(het))
    assert informative == 0
    assert cons == (0.0, 0.0)


def test_match_rejects_ambiguous_directional_split_without_margin():
    """H5(C-i): the margin gate. When the relative's shared homolog is not clearly
    one side — here the anchor-informative sites split 6/4 between the two homologs —
    there is no decisive transmitted homolog, so the chosen homolog fails to out-score
    its sibling by ``MIN_HOMOLOG_MARGIN`` and the match is rejected (no false confirm
    on a noisy near-even split). A clean split is accepted (next test)."""
    rel: dict[int, tuple[int, int]] = {}
    anc: dict[int, tuple[int, int]] = {}
    # 6 sites point at anchor homolog 0, 4 at homolog 1: consistency 0.6 vs 0.4,
    # margin 0.2 < MIN_HOMOLOG_MARGIN.
    pattern = [0, 0, 0, 0, 0, 0, 1, 1, 1, 1]
    for i, allele in enumerate(pattern):
        pos = 1000 + i * 1000
        rel[pos] = (allele, allele)  # relative homozygous -> anchor-informative site
        anc[pos] = (0, 1)  # anchor heterozygous
    assert match_shared_homolog(rel, anc) is None
    assert 0.2 < MIN_HOMOLOG_MARGIN  # guard the constant the test relies on


def test_match_accepts_clean_directional_split():
    """Companion to the margin test: a decisive split (all informative sites agree)
    clears both the consistency floor and the margin gate."""
    rel = {1000 + i * 1000: (0, 0) for i in range(20)}
    anc = {1000 + i * 1000: (0, 1) for i in range(20)}
    match = match_shared_homolog(rel, anc)
    assert match is not None
    assert match.anchor_idx == 0
    assert match.confidence == 1.0


def test_match_rejects_sparse_informative_sites_over_huge_span():
    """H5(C-ii): the per-Mb informative-density floor. A clean directional signal at
    only a handful of sites smeared across an entire chromosome carries no real
    linkage and must not confirm a share."""
    rel: dict[int, tuple[int, int]] = {}
    anc: dict[int, tuple[int, int]] = {}
    # 12 clean phase-informative sites, but spread 10 Mb apart (120 Mb span):
    # density ~0.1 informative/Mb, below MIN_INFORMATIVE_PER_MB.
    for i in range(12):
        pos = 1_000_000 + i * 10_000_000
        rel[pos] = (0, 0)
        anc[pos] = (0, 1)
    span_mb = (max(rel) - min(rel)) / 1_000_000
    assert len(rel) / span_mb < MIN_INFORMATIVE_PER_MB  # guard the gate this test targets
    assert match_shared_homolog(rel, anc) is None
    # The same 12 sites packed densely DO confirm (isolating the density gate).
    rel_dense = {1_000 + i * 1_000: (0, 0) for i in range(12)}
    anc_dense = {1_000 + i * 1_000: (0, 1) for i in range(12)}
    assert match_shared_homolog(rel_dense, anc_dense) is not None


def test_annotate_lineage_colours_grandmother_one_blue_one_grey():
    segments_by_name = {
        "F": _parent_segments("0", "1"),
        "M": _parent_segments("0", "1"),
        "E1": [{"chr": "1", "start": 1000, "end": 2100, "hap1": "0", "hap2": "0", "ps": None}],
        "GM": [{"chr": "1", "start": 1, "end": 9999, "hap1": "1", "hap2": "0", "ps": None}],  # stored junk
    }
    out = annotate_lineage(
        sample_rows=THREE_GEN_MEMBERS,
        relationship_rows=THREE_GEN_RELATIONSHIPS,
        segments_by_name=segments_by_name,
        genotype_rows=_three_gen_genotypes(),
        chrom="1",
        region_start=1000,
        region_end=2100,
    )

    # Nuclear core tagged by role.
    assert all(s["hap1_lineage"] == PATERNAL and s["hap2_lineage"] == PATERNAL for s in out["F"])
    assert all(s["hap1_lineage"] == MATERNAL and s["hap2_lineage"] == MATERNAL for s in out["M"])
    assert out["E1"][0]["hap1_lineage"] == PATERNAL
    assert out["E1"][0]["hap2_lineage"] == MATERNAL

    # Grandmother: exactly one lane coloured paternal (matching the father's shared
    # dark-blue homolog, shade 0) and the other greyed.
    gm = out["GM"]
    assert len(gm) == 1
    seg = gm[0]
    assert seg["hap1_lineage"] == PATERNAL
    assert seg["hap1"] == "0"  # father's shared homolog shade (dark blue)
    assert seg["hap2_lineage"] == UNTRANSMITTED
    assert seg["start"] == 1000 and seg["end"] == 2100


def test_annotate_lineage_greys_unplaceable_relative():
    # An extra relative with no informative genotypes cannot be matched -> grey,
    # keeping its stored block geometry rather than being mis-coloured.
    members = THREE_GEN_MEMBERS + [_member("UNREL", "relative")]
    rels = THREE_GEN_RELATIONSHIPS + [_parent_child("UNREL", "GM", "mother")]
    segments_by_name = {
        "F": _parent_segments("0", "1"),
        "M": _parent_segments("0", "1"),
        "E1": [{"chr": "1", "start": 1000, "end": 2100, "hap1": "0", "hap2": "0", "ps": None}],
        "GM": [{"chr": "1", "start": 1, "end": 9999, "hap1": "1", "hap2": "0", "ps": None}],
        "UNREL": [{"chr": "1", "start": 1, "end": 9999, "hap1": "0", "hap2": "1", "ps": None}],
    }
    out = annotate_lineage(
        sample_rows=members,
        relationship_rows=rels,
        segments_by_name=segments_by_name,
        genotype_rows=_three_gen_genotypes(),  # no UNREL genotypes
        chrom="1",
        region_start=1000,
        region_end=2100,
    )
    assert out["UNREL"][0]["hap1_lineage"] == UNKNOWN
    assert out["UNREL"][0]["hap2_lineage"] == UNKNOWN


def test_annotate_lineage_greys_relative_past_truncated_genotype_fetch():
    """Truncation guard: when the per-chromosome genotype fetch hit its cap, the
    fetched rows only cover the lowest-coordinate sites (p-arm). A relative must NOT be
    coloured across the q-arm — where no genotype confirms the share — even though the
    stored block geometry spans the whole chromosome. The coloured block stops at the
    last fetched site; the tail beyond is greyed (mirrors the marker path's M1 guard)."""
    # Stored blocks span a whole chromosome (to 9_000_000), but the genotype fetch only
    # reached the p-arm (max site < 3000, from _three_gen_genotypes()).
    segments_by_name = {
        "F": _parent_segments("0", "1", end=9_000_000),
        "M": _parent_segments("0", "1", end=9_000_000),
        "E1": [{"chr": "1", "start": 1000, "end": 9_000_000, "hap1": "0", "hap2": "0", "ps": None}],
        "GM": [{"chr": "1", "start": 1, "end": 9_000_000, "hap1": "1", "hap2": "0", "ps": None}],
    }
    genotypes = _three_gen_genotypes()
    max_pos = max(row[0] for row in genotypes)
    # Whole-chromosome path (no explicit region) -> span comes from the stored blocks.
    out = annotate_lineage(
        sample_rows=THREE_GEN_MEMBERS,
        relationship_rows=THREE_GEN_RELATIONSHIPS,
        segments_by_name=segments_by_name,
        genotype_rows=genotypes,
        chrom="1",
        genotype_truncated=True,
    )
    gm = out["GM"]
    # The grandmother is matched on the p-arm: at least one coloured block there.
    coloured = [seg for seg in gm if PATERNAL in (seg["hap1_lineage"], seg["hap2_lineage"])]
    assert coloured, "grandmother lost her p-arm colour"
    # No coloured block may extend past the last fetched genotype site.
    assert all(seg["end"] <= max_pos + 1 for seg in coloured)
    # The q-arm tail (past the last evidence) is greyed, not coloured, and reaches the
    # stored chromosome end so the lane is fully covered.
    assert gm[-1]["hap1_lineage"] == UNTRANSMITTED and gm[-1]["hap2_lineage"] == UNTRANSMITTED
    assert gm[-1]["end"] == 9_000_000
    assert gm[-1]["start"] >= max_pos


def test_annotate_lineage_not_truncated_extends_to_chromosome_end():
    """Counterpart: without truncation the genotypes are assumed to cover the span, so
    the relative's coloured block legitimately reaches the stored chromosome end (no
    spurious grey tail is inserted)."""
    segments_by_name = {
        "F": _parent_segments("0", "1", end=9_000_000),
        "M": _parent_segments("0", "1", end=9_000_000),
        "E1": [{"chr": "1", "start": 1000, "end": 9_000_000, "hap1": "0", "hap2": "0", "ps": None}],
        "GM": [{"chr": "1", "start": 1, "end": 9_000_000, "hap1": "1", "hap2": "0", "ps": None}],
    }
    out = annotate_lineage(
        sample_rows=THREE_GEN_MEMBERS,
        relationship_rows=THREE_GEN_RELATIONSHIPS,
        segments_by_name=segments_by_name,
        genotype_rows=_three_gen_genotypes(),
        chrom="1",
        genotype_truncated=False,
    )
    assert out["GM"][-1]["end"] == 9_000_000
    # The final block is the grandmother's coloured (paternal) homolog, not a grey tail.
    assert out["GM"][0]["hap1_lineage"] == PATERNAL


def test_core_homologs_missing_shade_is_grey_not_raw_index():
    """H5(A): a missing founder shade must NOT silently fall back to the raw homolog
    index (which would fabricate an orientation). The unresolved homolog is greyed."""
    core = NuclearCore(father="F", mother="M", children={"E1"}, members={"F", "M", "E1"})
    # Only homolog 0's shade is known for the father; homolog 1 is unresolved.
    homs = _core_homologs("F", core, father_shade={0: 1}, mother_shade={0: 0, 1: 1})
    assert homs[0].origin == PATERNAL and homs[0].shade == 1
    # The old code returned HomologAssignment(PATERNAL, 1) here via .get(1, 1) — a
    # raw-index colour. It must now be grey.
    assert homs[1] == GREY
    assert not homs[1].is_founder


def test_annotate_lineage_missing_father_shade_does_not_emit_raw_index_colour():
    """End-to-end H5(A): if the father has no stored shade for the homolog the
    grandmother shares, her shared lane is greyed (UNTRANSMITTED), never a raw-index
    blue. Here the father's stored block carries no resolvable shade on either lane."""
    segments_by_name = {
        # Father stored blocks have non-numeric lane values -> no shade recoverable.
        "F": [{"chr": "1", "start": 1000, "end": 3300, "hap1": ".", "hap2": ".", "ps": None}],
        "M": _parent_segments("0", "1", end=3300),
        "E1": [{"chr": "1", "start": 1000, "end": 3300, "hap1": "0", "hap2": "0", "ps": None}],
        "GM": [{"chr": "1", "start": 1, "end": 9999, "hap1": "1", "hap2": "0", "ps": None}],
    }
    out = annotate_lineage(
        sample_rows=THREE_GEN_MEMBERS,
        relationship_rows=THREE_GEN_RELATIONSHIPS,
        segments_by_name=segments_by_name,
        genotype_rows=_three_gen_genotypes(),
        chrom="1",
        region_start=1000,
        region_end=3300,
    )
    # The grandmother is still matched (IBD from genotypes), but the anchor homolog
    # she shares has no founder shade -> her shared lane must be grey, not coloured.
    for seg in out["GM"]:
        assert seg["hap1_lineage"] in (UNTRANSMITTED,)
        assert seg["hap2_lineage"] in (UNTRANSMITTED,)
        # No founder shade leaked through as a raw index.


def test_annotate_lineage_does_not_ibd_colour_x_chromosome():
    """H5(D): non-autosomal chromosomes break the diploid two-homolog IBD assumption
    (hemizygous X, no Y recombination). Relatives on chrX are left grey rather than
    mis-coloured with autosomal logic. The nuclear core keeps its role tags."""
    segments_by_name = {
        "F": _parent_segments("0", "1", chrom="X", end=3300),
        "M": _parent_segments("0", "1", chrom="X", end=3300),
        "E1": [{"chr": "X", "start": 1000, "end": 3300, "hap1": "0", "hap2": "0", "ps": None}],
        "GM": [{"chr": "X", "start": 1, "end": 9999, "hap1": "1", "hap2": "0", "ps": None}],
    }
    # Same informative genotypes, but on chrX.
    genotype_rows = [(pos, ids, gts) for (pos, ids, gts) in _three_gen_genotypes()]
    out = annotate_lineage(
        sample_rows=THREE_GEN_MEMBERS,
        relationship_rows=THREE_GEN_RELATIONSHIPS,
        segments_by_name=segments_by_name,
        genotype_rows=genotype_rows,
        chrom="X",
        region_start=1000,
        region_end=3300,
    )
    # Core still role-tagged.
    assert all(s["hap1_lineage"] == PATERNAL for s in out["F"])
    # Grandmother NOT IBD-coloured on X: both lanes grey (unknown), never paternal.
    for seg in out["GM"]:
        assert seg["hap1_lineage"] != PATERNAL
        assert seg["hap2_lineage"] != PATERNAL


def test_grey_assignment_constant():
    assert GREY.origin == UNTRANSMITTED and GREY.shade is None
    assert not GREY.is_founder
    assert HomologAssignment(PATERNAL, 0).is_founder


# --- recombination segmentation ---------------------------------------------

def test_smooth_runs_commits_only_a_sustained_switch():
    # 60 idx-0 pins, then 60 idx-1 pins spanning >500kb -> a real crossover.
    pins = [(1_000_000 + i * 10_000, 0) for i in range(60)]
    switch_at = 1_000_000 + 60 * 10_000
    pins += [(switch_at + i * 10_000, 1) for i in range(60)]
    runs = _smooth_runs(pins, fallback=0, start_pos=1_000_000)
    assert runs == [(1_000_000, 0), (switch_at, 1)]


def test_smooth_runs_ignores_isolated_noise():
    # A handful of stray idx-1 pins amid idx-0 is imputation noise, not a crossover.
    pins = [(1_000_000 + i * 10_000, 0) for i in range(100)]
    pins[20] = (pins[20][0], 1)
    pins[21] = (pins[21][0], 1)
    runs = _smooth_runs(pins, fallback=0, start_pos=1_000_000)
    assert runs == [(1_000_000, 0)]


def test_smooth_runs_commits_an_early_short_leading_tract_then_sustained_switch():
    # Regression for H4: a genuine leading tract of <50 informative pins followed
    # by a *sustained* switch. The old head-window majority vote (pins[:50]) would
    # let the 60 idx-1 pins outvote the 24-pin idx-0 head and seed the run at 1,
    # collapsing 0 -> 1 into a single mis-coloured run and hiding the breakpoint.
    # Seeding from the first pin keeps the start at 0 and emits the crossover.
    lead = [(1_000_000 + i * 10_000, 0) for i in range(24)]
    switch_at = 1_000_000 + 24 * 10_000
    tail = [(switch_at + i * 10_000, 1) for i in range(60)]
    runs = _smooth_runs(lead + tail, fallback=1, start_pos=1_000_000)
    # Start coloured correctly (idx 0, from the leading tract) AND the breakpoint
    # to idx 1 emitted at the head of the sustained run.
    assert runs == [(1_000_000, 0), (switch_at, 1)]


def test_segment_relative_blocks_splits_at_a_crossover():
    # The relative's transmitted homolog switches from raw0 (first half) to raw1
    # (second half); the anchor's shared homolog (and thus the colour) is fixed.
    # Result: the coloured lane jumps hap1 -> hap2 at the crossover, grey follows.
    #
    # NB (anchor-shade fix): the founder shade is only painted where an anchor-
    # informative site (anchor het, relative hom) pins WHICH founder homolog is
    # shared — otherwise the shade is undetermined and the lane is greyed. We
    # therefore interleave such sites that consistently pin anchor homolog 1 (the
    # fixed shared anchor homolog, shade 1) across the whole region; only the
    # relative side crosses over.
    seg1 = [1_000_000 + i * 10_000 for i in range(70)]
    seg2 = [2_500_000 + i * 10_000 for i in range(70)]
    rel_alleles = {}
    anc_alleles = {}
    for pos in seg1:
        rel_alleles[pos] = (0, 1)  # het -> pinnable (relative side)
        anc_alleles[pos] = (0, 0)  # hom 0 -> pins relative raw0 as shared
    for pos in seg2:
        rel_alleles[pos] = (0, 1)
        anc_alleles[pos] = (1, 1)  # hom 1 -> pins relative raw1 as shared
    # Anchor-informative sites pinning anchor homolog 1 (relative hom for the allele
    # on anchor homolog 1) throughout, so the founder shade is genuinely resolved.
    for pos in seg1 + seg2:
        anc_alleles[pos + 5_000] = (0, 1)  # anchor het
        rel_alleles[pos + 5_000] = (1, 1)  # relative hom 1 -> shared anchor homolog = idx 1
    anchor_homologs = {0: HomologAssignment(PATERNAL, 0), 1: HomologAssignment(PATERNAL, 1)}
    match = MatchResult(relative_idx=0, anchor_idx=1, informative=140, confidence=1.0)
    positions = sorted(rel_alleles)
    region_end = positions[-1] + 1
    blocks, assignment, _resolver = _segment_relative_blocks(
        chrom="1",
        positions=positions,
        rel_alleles=rel_alleles,
        anc_alleles=anc_alleles,
        anchor_homologs=anchor_homologs,
        match=match,
        region_start=1_000_000,
        region_end=region_end,
    )
    assert len(blocks) == 2
    first, second = blocks
    # First half: relative raw0 shared -> coloured lane is hap1 (light blue, shade 1).
    assert first["hap1_lineage"] == PATERNAL and first["hap1"] == "1"
    assert first["hap2_lineage"] == UNTRANSMITTED
    assert first["start"] == 1_000_000 and first["end"] == 2_500_000
    # Second half: relative raw1 shared -> coloured lane jumps to hap2, grey follows.
    assert second["hap2_lineage"] == PATERNAL and second["hap2"] == "1"
    assert second["hap1_lineage"] == UNTRANSMITTED
    assert second["start"] == 2_500_000 and second["end"] == region_end
    # Propagation map uses the chromosome-level (global) shared homolog.
    assert assignment[0].is_founder and not assignment[1].is_founder


def test_segment_relative_blocks_single_block_without_recombination():
    positions = [1_000_000 + i * 10_000 for i in range(40)]
    rel_alleles = {p: (0, 1) for p in positions}
    anc_alleles = {p: (0, 0) for p in positions}
    blocks, _assignment, _resolver = _segment_relative_blocks(
        chrom="1",
        positions=positions,
        rel_alleles=rel_alleles,
        anc_alleles=anc_alleles,
        anchor_homologs={0: HomologAssignment(PATERNAL, 0), 1: HomologAssignment(PATERNAL, 1)},
        match=MatchResult(relative_idx=0, anchor_idx=0, informative=40, confidence=1.0),
        region_start=1_000_000,
        region_end=positions[-1] + 1,
    )
    assert len(blocks) == 1


def test_segment_relative_blocks_greys_shared_lane_when_anchor_shade_unresolved():
    """Anchor-shade fix: when only the relative side resolved the match (the anchor is
    homozygous wherever it could distinguish its homologs, so there are zero
    anchor-informative pins), WHICH founder homolog the relative shares is genuinely
    undetermined. ``match.anchor_idx`` is then just a coin flip, so the shared lane must
    be GREY (UNTRANSMITTED), never a confident founder shade — picking dark vs light
    blue by chance would flip a dominant disease signature and misclassify embryos."""
    positions = [1_000_000 + i * 10_000 for i in range(40)]
    rel_alleles = {p: (0, 1) for p in positions}  # relative het -> relative-informative
    anc_alleles = {p: (0, 0) for p in positions}  # anchor hom -> NO anchor-informative site
    # A couple of double-het sites (both het) carry no transmission direction either.
    for extra in (positions[5] + 5_000, positions[20] + 5_000):
        rel_alleles[extra] = (0, 1)
        anc_alleles[extra] = (0, 1)
    blocks, assignment, resolver = _segment_relative_blocks(
        chrom="1",
        positions=sorted(rel_alleles),
        rel_alleles=rel_alleles,
        anc_alleles=anc_alleles,
        anchor_homologs={0: HomologAssignment(PATERNAL, 0), 1: HomologAssignment(PATERNAL, 1)},
        # The coin-flip seed the reviewer reproduced.
        match=MatchResult(relative_idx=0, anchor_idx=0, informative=40, confidence=1.0),
        region_start=1_000_000,
        region_end=max(rel_alleles) + 1,
    )
    # No lane may be painted a definite founder shade.
    for seg in blocks:
        assert seg["hap1_lineage"] == UNTRANSMITTED and seg["hap2_lineage"] == UNTRANSMITTED
    # And the propagated assignment / resolver must not seed a downstream hop with a
    # guessed founder colour.
    assert not assignment[0].is_founder and not assignment[1].is_founder
    assert not resolver.at(0, 1_000_000).is_founder
    assert not resolver.at(1, 1_000_000).is_founder


def _coloured_lane_shade(seg):
    """The (lane, shade) of the single coloured lane in a relative block, or None if
    both lanes are grey."""
    for lane in ("hap1", "hap2"):
        if seg[f"{lane}_lineage"] == PATERNAL:
            return lane, seg[lane]
    return None


def test_segment_relative_blocks_resolver_carries_anchor_crossover():
    """Fix H3 (mechanism): B is anchored to founder A and B has an *anchor* crossover —
    the side that switches WHICH of A's homologs B shares — at the region midpoint, so
    the founder shade B's shared homolog carries changes 0 -> 1. The resolver returned
    for B must record that change per position, not a single shade, so the next hop can
    read the correct shade either side of the crossover."""
    first = [1_000_000 + i * 10_000 for i in range(70)]
    second = [2_500_000 + i * 10_000 for i in range(70)]
    rel_alleles = {}
    anc_alleles = {}
    # B's shared raw homolog is fixed at 0 (B het, A hom 0 -> pins B raw0) everywhere,
    # so the relative side never crosses over.
    for pos in first:
        rel_alleles[pos] = (0, 1)
        anc_alleles[pos] = (0, 0)
    for pos in second:
        rel_alleles[pos] = (0, 1)
        anc_alleles[pos] = (0, 0)
    # Interleave anchor-informative sites (A het, B hom) that pin WHICH A homolog is
    # shared: A homolog 0 in the first half, A homolog 1 in the second half — the
    # anchor crossover. B is homozygous there (consistent: B raw0 is the shared one).
    for pos in first:
        anc_alleles[pos + 5_000] = (0, 1)  # A het -> anchor-informative
        rel_alleles[pos + 5_000] = (0, 0)  # B hom 0 -> shared A homolog carries 0 = idx 0
    for pos in second:
        anc_alleles[pos + 5_000] = (0, 1)
        rel_alleles[pos + 5_000] = (1, 1)  # B hom 1 -> shared A homolog carries 1 = idx 1

    positions = sorted(rel_alleles)
    region_start = positions[0]
    region_end = positions[-1] + 1
    # A is the founder: homolog 0 = dark blue (shade 0), homolog 1 = light blue (shade 1).
    anchor_homologs = {0: HomologAssignment(PATERNAL, 0), 1: HomologAssignment(PATERNAL, 1)}
    match = MatchResult(relative_idx=0, anchor_idx=0, informative=len(positions), confidence=1.0)

    b_blocks, _b_global, b_resolver = _segment_relative_blocks(
        chrom="1",
        positions=positions,
        rel_alleles=rel_alleles,
        anc_alleles=anc_alleles,
        anchor_homologs=anchor_homologs,
        match=match,
        region_start=region_start,
        region_end=region_end,
    )
    # B's own display already tracks the crossover: shared lane shade 0 then 1.
    b_shades = [_coloured_lane_shade(seg) for seg in b_blocks]
    assert ("hap1", "0") in b_shades and ("hap1", "1") in b_shades
    # The resolver for B's shared raw homolog 0 must carry BOTH shades, at the right
    # positions — not collapse to one.
    assert b_resolver.at(0, region_start).shade == 0
    assert b_resolver.at(0, region_end - 1).shade == 1
    # B's untransmitted raw homolog 1 stays grey throughout.
    assert not b_resolver.at(1, region_start).is_founder
    assert not b_resolver.at(1, region_end - 1).is_founder


def test_three_generation_propagation_tracks_mid_region_crossover():
    """Fix H3 (end of chain): C is anchored to B, where B itself crossed over the
    anchor side mid-region (shade 0 -> 1). C shares B's founder-coloured homolog with
    no crossover of its own, so C's coloured lane must track B's crossover (shade 0 in
    the first half, shade 1 in the second), NOT a single shade smeared across the whole
    region (the pre-fix bug, where B's flat chromosome-level homolog map lost the
    crossover and coloured C uniformly)."""
    # Build B's resolver via the same anchor-crossover construction as above.
    first = [1_000_000 + i * 10_000 for i in range(70)]
    second = [2_500_000 + i * 10_000 for i in range(70)]
    rel_alleles = {}
    anc_alleles = {}
    for pos in first + second:
        rel_alleles[pos] = (0, 1)
        anc_alleles[pos] = (0, 0)
    for pos in first:
        anc_alleles[pos + 5_000] = (0, 1)
        rel_alleles[pos + 5_000] = (0, 0)
    for pos in second:
        anc_alleles[pos + 5_000] = (0, 1)
        rel_alleles[pos + 5_000] = (1, 1)
    b_positions = sorted(rel_alleles)
    region_start = b_positions[0]
    region_end = b_positions[-1] + 1
    _b_blocks, _b_global, b_resolver = _segment_relative_blocks(
        chrom="1",
        positions=b_positions,
        rel_alleles=rel_alleles,
        anc_alleles=anc_alleles,
        anchor_homologs={0: HomologAssignment(PATERNAL, 0), 1: HomologAssignment(PATERNAL, 1)},
        match=MatchResult(relative_idx=0, anchor_idx=0, informative=len(b_positions), confidence=1.0),
        region_start=region_start,
        region_end=region_end,
    )

    # C shares B's raw homolog 0 (the founder-coloured one) with NO crossover of its
    # own. C is het at the main sites (relative-informative: pins C raw0 as the shared
    # lane). To resolve WHICH of B's homologs C shares — required now that an anchor
    # shade is only painted where an anchor-informative site supports it — we interleave
    # sites where B is het and C is homozygous for the allele on B's raw homolog 0,
    # pinning B raw0 (the founder-coloured one) as the shared anchor homolog throughout.
    c_rel = {}
    c_anc = {}
    for pos in b_positions:
        c_rel[pos] = (0, 1)  # C het -> relative-informative, pins C raw0 shared
        c_anc[pos] = (0, 0)  # B hom 0 here -> no anchor pin (which B homolog is unknown)
    for pos in b_positions:
        c_anc[pos + 2_000] = (0, 1)  # B het -> anchor-informative
        c_rel[pos + 2_000] = (0, 0)  # C hom 0 -> shared B homolog carries 0 = B raw0
    c_positions = sorted(c_rel)
    c_blocks, _c_global, _c_resolver = _segment_relative_blocks(
        chrom="1",
        positions=c_positions,
        rel_alleles=c_rel,
        anc_alleles=c_anc,
        # The KEY of the fix: propagate B's POSITION-AWARE resolver, not a flat map.
        anchor_homologs=b_resolver,
        match=MatchResult(relative_idx=0, anchor_idx=0, informative=len(c_positions), confidence=1.0),
        region_start=region_start,
        region_end=region_end,
    )

    # C must show TWO coloured segments tracking B's crossover: shade 0 then shade 1.
    c_shades = [_coloured_lane_shade(seg) for seg in c_blocks]
    assert ("hap1", "0") in c_shades, f"C lost the dark shade before the crossover: {c_shades}"
    assert ("hap1", "1") in c_shades, f"C lost the light shade after the crossover: {c_shades}"
    # Concretely: dark (0) at the start, light (1) at the end.
    assert _coloured_lane_shade(c_blocks[0]) == ("hap1", "0")
    assert _coloured_lane_shade(c_blocks[-1]) == ("hap1", "1")

    # Regression guard: a FLAT (position-blind) propagation — the pre-fix behaviour —
    # would colour C with a single shade across the whole region. Feeding B's flat
    # chromosome-level map reproduces that, proving the resolver is what fixes it.
    flat_anchor = {0: HomologAssignment(PATERNAL, 0), 1: GREY}  # B's single-shade global map
    flat_blocks, _g, _r = _segment_relative_blocks(
        chrom="1",
        positions=c_positions,
        rel_alleles=c_rel,
        anc_alleles=c_anc,
        anchor_homologs=flat_anchor,
        match=MatchResult(relative_idx=0, anchor_idx=0, informative=len(c_positions), confidence=1.0),
        region_start=region_start,
        region_end=region_end,
    )
    flat_shades = {_coloured_lane_shade(seg) for seg in flat_blocks}
    assert flat_shades == {("hap1", "0")}  # one wrong shade everywhere — the bug we fixed


def test_homolog_resolver_position_lookup():
    """The resolver returns the assignment in effect at a position; a flat map is a
    single run starting at 0 (position-independent)."""
    flat = HomologResolver.from_flat({0: HomologAssignment(PATERNAL, 0), 1: GREY})
    assert flat.at(0, 0).shade == 0 and flat.at(0, 10_000_000).shade == 0
    assert not flat.at(1, 0).is_founder
    # Multi-run homolog: shade 0 until 2_000_000, then shade 1.
    runs = HomologResolver(
        runs={0: [(0, HomologAssignment(PATERNAL, 0)), (2_000_000, HomologAssignment(PATERNAL, 1))]}
    )
    assert runs.at(0, 1_999_999).shade == 0
    assert runs.at(0, 2_000_000).shade == 1
    assert runs.at(0, 5_000_000).shade == 1
    # Unknown homolog index -> grey.
    assert not runs.at(1, 0).is_founder




# --- relatives of unknown degree ---------------------------------------------------

import random  # noqa: E402

from backend.app.services.haplotype_lineage_service import (  # noqa: E402
    parent_or_child_links,
    relative_link_matches,
    relative_links,
)

_CHROM_LENGTH = 60_000_000
_SITE_STEP = 5_000


def _simulated_relative_rows(
    *,
    seed: int,
    shares: tuple[tuple[int, int, int], ...] = (),
    ibd2: tuple[tuple[int, int], ...] = (),
    error_rate: float = 0.01,
    chrom: str = "1",
    length: int = _CHROM_LENGTH,
    first: int = _SITE_STEP,
    step: int = _SITE_STEP,
):
    """Genotypes of a couple, an embryo and a relative R of the mother's, at a site every
    ``step`` from ``first``: R carries the mother's homolog ``homolog`` on its first lane
    over each ``(start, end, homolog)`` of ``shares``, both her homologs over each ``ibd2``
    stretch, and alleles of its own elsewhere; its lanes come in either order, and a
    fraction ``error_rate`` of its alleles is flipped."""
    rng = random.Random(seed)
    rows = []
    for pos in range(first, length, step):
        frequency = rng.uniform(0.05, 0.95)

        def draw(frequency: float = frequency) -> int:
            return 1 if rng.random() < frequency else 0

        m0, m1, f0, f1, r0, r1 = draw(), draw(), draw(), draw(), draw(), draw()
        for start, end, homolog in shares:
            if start <= pos < end:
                r0 = (m0, m1)[homolog]
        for start, end in ibd2:
            if start <= pos < end:
                r0, r1 = m0, m1
        if rng.random() < error_rate:
            if rng.random() < 0.5:
                r0 = 1 - r0
            else:
                r1 = 1 - r1
        if rng.random() < 0.5:
            r0, r1 = r1, r0
        gts = {"F": f"{f0}|{f1}", "M": f"{m0}|{m1}", "E1": f"{f0}|{m0}", "R": f"{r0}|{r1}"}
        rows.append((pos, list(gts), list(gts.values())))
    return rows


_RELATIVE_MEMBERS = [
    _member("F", "father"),
    _member("M", "mother", affected=True),
    _member("E1", "embryo"),
    _member("R", "relative", affected=True),
]
_RELATIVE_RELATIONSHIPS = [
    _parent_child("F", "E1", "father"),
    _parent_child("M", "E1", "mother"),
    {"relationship_type": "relative", "sample_id_a": "M", "sample_id_b": "R", "role_a": "relative", "role_b": "relative"},
]


def _annotate_relative(
    rows, parent_or_child=(("R", "M"),), length=_CHROM_LENGTH, locus=None, relationships=_RELATIVE_RELATIONSHIPS
):
    whole = {"chr": "1", "start": 0, "end": length, "ps": None}
    return annotate_lineage(
        sample_rows=_RELATIVE_MEMBERS,
        relationship_rows=relationships,
        segments_by_name={
            "F": [{**whole, "hap1": "0", "hap2": "1"}],
            "M": [{**whole, "hap1": "0", "hap2": "1"}],
            "E1": [{**whole, "hap1": "0", "hap2": "0"}],
            "R": [{**whole, "hap1": "1", "hap2": "0"}],  # stored junk
        },
        genotype_rows=rows,
        chrom="1",
        region_start=0,
        region_end=length,
        parent_or_child=parent_or_child,
        locus=locus,
    )


def _coloured_spans(blocks, lane):
    """``(start, end, shade)`` of the blocks whose ``lane`` carries a founder colour."""
    return [
        (block["start"], block["end"], block[lane])
        for block in blocks
        if block[f"{lane}_lineage"] in (PATERNAL, MATERNAL)
    ]


def _genome_counts(kind: str, *, seed: int):
    """``{link: (autosomes sharing, autosomes tested)}`` over 22 short chromosomes for a
    relative of the mother's that is her ``child``, a ``sibling`` or ``unrelated``."""
    rng = random.Random(seed)
    counts: dict = {}
    for chrom in range(1, 23):
        length = 6_000_000
        if kind == "child":
            shares, ibd2 = ((0, length, rng.randint(0, 1)),), ()
        elif kind == "sibling":
            # IBD0, IBD1 and IBD2 in stretches, as siblings share.
            cut1, cut2 = sorted(rng.sample(range(1_000_000, length - 1_000_000, 100_000), 2))
            states = rng.sample(["ibd0", "ibd1", "ibd2"], 3)
            shares, ibd2 = (), ()
            for (start, end), state in zip(((0, cut1), (cut1, cut2), (cut2, length)), states):
                if state == "ibd1":
                    shares = ((start, end, rng.randint(0, 1)),)
                elif state == "ibd2":
                    ibd2 = ((start, end),)
        else:
            shares, ibd2 = (), ()
        rows = _simulated_relative_rows(seed=seed * 100 + chrom, shares=shares, ibd2=ibd2, length=length)
        for link, shared in relative_link_matches(rows, [("R", "M")], chrom=str(chrom)).items():
            sharing, tested = counts.get(link, (0, 0))
            counts[link] = (sharing + int(shared), tested + 1)
    return counts


def test_a_relative_of_unknown_degree_is_read_as_a_parent_or_child_only_from_the_whole_genome():
    # Every autosome shows the parent-child share for a child of the mother; a sibling has
    # stretches of IBD0 on most, and an unrelated relative on all.
    assert relative_links(_RELATIVE_RELATIONSHIPS) == [("R", "M")]
    for seed in range(3):
        assert parent_or_child_links(_genome_counts("child", seed=seed)) == {("R", "M")}, seed
        assert parent_or_child_links(_genome_counts("sibling", seed=seed)) == set(), seed
        assert parent_or_child_links(_genome_counts("unrelated", seed=seed)) == set(), seed


def test_too_few_autosomes_cannot_tell():
    assert parent_or_child_links({("R", "M"): (14, 14)}) == set()
    assert parent_or_child_links({("R", "M"): (19, 22)}) == set()
    assert parent_or_child_links({("R", "M"): (20, 22)}) == {("R", "M")}


@pytest.mark.parametrize("locus", [None, (30_000_000, 30_200_000)])
def test_a_parent_or_child_of_unknown_degree_is_coloured_as_one(locus):
    rows = _simulated_relative_rows(seed=11, shares=((0, _CHROM_LENGTH, 1),))

    # The ROI does not narrow it: a parent or child is coloured along the chromosome.
    relative = _annotate_relative(rows, locus=locus)["R"]

    coloured = _coloured_spans(relative, "hap1") + _coloured_spans(relative, "hap2")
    # The mother's homolog 1 along the chromosome, on whichever of R's lanes carries it.
    assert {shade for _start, _end, shade in coloured} == {"1"}
    assert sum(end - start for start, end, _shade in coloured) > 0.95 * _CHROM_LENGTH
    for block in relative:
        assert {block["hap1_lineage"], block["hap2_lineage"]} <= {MATERNAL, UNTRANSMITTED}


def test_a_relative_of_unknown_degree_not_found_a_parent_or_child_stays_grey():
    # The same genotypes, but the genome-wide test did not read a parent or child.
    rows = _simulated_relative_rows(seed=11, shares=((0, _CHROM_LENGTH, 1),))

    relative = _annotate_relative(rows, parent_or_child=())["R"]

    assert not _coloured_spans(relative, "hap1") and not _coloured_spans(relative, "hap2")


def test_a_child_of_both_linked_parents_gets_a_lane_from_each():
    rows = _simulated_relative_rows(seed=5, shares=((0, _CHROM_LENGTH, 0),))
    # Make R the couple's child: its other lane carries the father's homolog 1.
    rows = [
        (pos, names, [gts[0], gts[1], gts[2], f"{gts[0].split('|')[1]}|{gts[1].split('|')[0]}"])
        for pos, names, gts in rows
    ]
    relationships = [
        *_RELATIVE_RELATIONSHIPS,
        {"relationship_type": "relative", "sample_id_a": "F", "sample_id_b": "R", "role_a": "relative", "role_b": "relative"},
    ]
    whole = {"chr": "1", "start": 0, "end": _CHROM_LENGTH, "ps": None}

    relative = annotate_lineage(
        sample_rows=_RELATIVE_MEMBERS,
        relationship_rows=relationships,
        segments_by_name={
            "F": [{**whole, "hap1": "0", "hap2": "1"}],
            "M": [{**whole, "hap1": "0", "hap2": "1"}],
            "E1": [{**whole, "hap1": "0", "hap2": "0"}],
            "R": [{**whole, "hap1": "0", "hap2": "0"}],
        },
        genotype_rows=rows,
        chrom="1",
        region_start=0,
        region_end=_CHROM_LENGTH,
        parent_or_child={("R", "M"), ("R", "F")},
    )["R"]

    lineages = {(block["hap1_lineage"], block["hap2_lineage"]) for block in relative}
    assert (PATERNAL, MATERNAL) in lineages
    assert sum(block["end"] - block["start"] for block in relative if {block["hap1_lineage"], block["hap2_lineage"]} == {PATERNAL, MATERNAL}) > 0.9 * _CHROM_LENGTH


def test_a_window_colours_a_relative_of_unknown_degree_from_the_genome_wide_precompute(monkeypatch):
    import asyncio

    from backend.app.services import bed_service, clickhouse_family_variants
    from backend.app.services.family_metadata_context import FamilyMetadataContext

    rows = _simulated_relative_rows(seed=11, shares=((0, _CHROM_LENGTH, 1),))

    async def fetch(context, *, chrom, start, end, limit):
        return [row for row in rows if start <= row[0] <= end]

    precomputed_block = {
        "chr": "1", "start": 0, "end": _CHROM_LENGTH, "hap1": "1", "hap2": "0", "ps": None,
        "hap1_lineage": MATERNAL, "hap2_lineage": UNTRANSMITTED,
    }
    precomputed: dict = {"R-uuid": [precomputed_block]}

    async def stored_lineage(context):
        return precomputed

    monkeypatch.setattr(clickhouse_family_variants, "fetch_imputed_phased_genotypes", fetch)
    monkeypatch.setattr(bed_service, "_fetch_precomputed_lineage", stored_lineage)
    names = [member["sample_id"] for member in _RELATIVE_MEMBERS]
    context = FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="FAM1",
        project_ids=[],
        sample_rows=_RELATIVE_MEMBERS,
        sample_uuid_to_name={f"{name}-uuid": name for name in names},
        sample_name_to_uuid={name: f"{name}-uuid" for name in names},
        affected_sample_names=["M", "R"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
        relationship_rows=_RELATIVE_RELATIONSHIPS,
    )
    window = {"chr": "1", "start": 24_000_000, "end": 26_000_000, "ps": None}
    stored = {
        "F-uuid": [{**window, "hap1": "0", "hap2": "1"}],
        "M-uuid": [{**window, "hap1": "0", "hap2": "1"}],
        "E1-uuid": [{**window, "hap1": "0", "hap2": "0"}],
        "R-uuid": [{**window, "hap1": "1", "hap2": "0"}],
    }

    def lineage():
        return asyncio.run(
            bed_service._apply_haplotype_lineage(
                context, segments_by_uuid=dict(stored), chrom="1", start=24_000_000, end=26_000_000
            )
        )["R-uuid"]

    # The precompute's colours, cut to the window.
    assert lineage() == [{**precomputed_block, "start": 24_000_000, "end": 26_000_000}]
    # No current precompute: grey, never coloured from the window alone.
    precomputed = None
    relative = lineage()
    assert not _coloured_spans(relative, "hap1") and not _coloured_spans(relative, "hap2")


# --- a more distant relative of unknown degree, read at the ROI ---------------------

from backend.app.services.haplotype_lineage_service import (  # noqa: E402
    RELATIVE_LOCUS_FLANK,
    _colour_across,
    relative_share_at_locus,
)

_ROI = (30_000_000, 30_200_000)
_LOCUS_LENGTH = 40_000_000
_WINDOW = (_ROI[0] - RELATIVE_LOCUS_FLANK, _ROI[1] + RELATIVE_LOCUS_FLANK)


def _locus_rows(seed, shares=(), ibd2=()):
    """Sites every 2 kb (about as dense as imputed genotypes are, thinned) from 20 Mb on."""
    return _simulated_relative_rows(
        seed=seed, shares=shares, ibd2=ibd2, length=_LOCUS_LENGTH, first=20_000_000, step=2_000
    )


def _alleles(rows, name):
    column = rows[0][1].index(name)
    return {pos: tuple(int(allele) for allele in gts[column].split("|")) for pos, _names, gts in rows}


def _share_at_roi(rows):
    return relative_share_at_locus(_alleles(rows, "R"), _alleles(rows, "M"), start=_ROI[0], end=_ROI[1])


def test_a_distant_relative_sharing_a_homolog_across_the_roi_is_read_from_both_flanks():
    # A sibling, aunt or cousin of the mother's: one of her homologs over a stretch that
    # holds the ROI and both its flanks.
    for seed in range(8):
        for homolog in (0, 1):
            rows = _locus_rows(seed, shares=((20_000_000, 40_000_000, homolog),))
            assert _share_at_roi(rows) == homolog, (seed, homolog)


@pytest.mark.parametrize(
    ("shares", "ibd2"),
    [
        # No haplotype shared there.
        ((), ()),
        # Both her homologs shared (a full sibling's IBD2): the relative tells them not apart.
        ((), ((20_000_000, 40_000_000),)),
        # A crossover in the ROI: only the left flank shares.
        (((20_000_000, 30_100_000, 1),), ()),
        # The flanks share different homologs.
        (((20_000_000, 30_100_000, 1), (30_100_000, 40_000_000, 0)), ()),
        # A share that ends inside the right flank.
        (((20_000_000, 31_500_000, 1),), ()),
    ],
)
def test_a_distant_relative_is_not_read_at_the_roi_unless_both_flanks_share_one_homolog(shares, ibd2):
    for seed in range(6):
        rows = _locus_rows(seed, shares=shares, ibd2=ibd2)
        assert _share_at_roi(rows) is None, seed


def test_too_few_informative_sites_on_a_flank_cannot_tell():
    rows = _locus_rows(2, shares=((20_000_000, 40_000_000, 1),))
    # Keep one site in five on the left flank: fewer than the pins a flank needs.
    sparse = [row for row in rows if not (_WINDOW[0] <= row[0] < _ROI[0]) or row[0] % 10_000 == 0]

    assert _share_at_roi(rows) == 1
    assert _share_at_roi(sparse) is None


def test_a_flank_with_few_pins_among_the_members_heterozygous_sites_cannot_tell():
    # A relative heterozygous at nearly all of the member's heterozygous sites carries both
    # her haplotypes there, whatever its few homozygous calls say.
    def share(pin_every):
        member, relative = {}, {}
        for first in (_WINDOW[0], _ROI[1]):
            for i in range(1_000):
                member[first + i * 3_000] = (0, 1)
                relative[first + i * 3_000] = (0, 0) if i % pin_every == 0 else (0, 1)
        return relative_share_at_locus(relative, member, start=_ROI[0], end=_ROI[1])

    assert share(5) == 0  # 200 pins of 1,000 sites
    assert share(8) is None  # 125 pins of 1,000 sites


def test_a_distant_relative_is_coloured_across_the_roi_and_its_flanks_and_grey_elsewhere():
    rows = _locus_rows(3, shares=((20_000_000, 40_000_000, 1),))

    relative = _annotate_relative(rows, parent_or_child=(), length=_LOCUS_LENGTH, locus=_ROI)["R"]

    coloured = _coloured_spans(relative, "hap1") + _coloured_spans(relative, "hap2")
    # The mother's homolog 1 (her stored hap2), on one of R's lanes; the other lane grey.
    assert coloured == [(_WINDOW[0], _WINDOW[1], "1")]
    for block in relative:
        lineages = {block["hap1_lineage"], block["hap2_lineage"]}
        if block["end"] <= _WINDOW[0] or block["start"] >= _WINDOW[1]:
            assert lineages == {UNKNOWN}
        else:
            assert lineages == {MATERNAL, UNTRANSMITTED}
    assert (relative[0]["start"], relative[-1]["end"]) == (0, _LOCUS_LENGTH)


@pytest.mark.parametrize(
    ("shares", "ibd2", "locus"),
    [
        # Shared, but this is not the ROI's chromosome.
        (((20_000_000, 40_000_000, 1),), (), None),
        ((), (), _ROI),
        ((), ((20_000_000, 40_000_000),), _ROI),
    ],
)
def test_a_distant_relative_stays_grey_where_the_roi_does_not_show_a_share(shares, ibd2, locus):
    rows = _locus_rows(4, shares=shares, ibd2=ibd2)

    relative = _annotate_relative(rows, parent_or_child=(), length=_LOCUS_LENGTH, locus=locus)["R"]

    assert not _coloured_spans(relative, "hap1") and not _coloured_spans(relative, "hap2")


def test_a_relative_linked_to_both_parents_is_coloured_from_the_side_it_shares():
    rows = _locus_rows(6, shares=((20_000_000, 40_000_000, 0),))
    relationships = [
        *_RELATIVE_RELATIONSHIPS,
        {"relationship_type": "relative", "sample_id_a": "F", "sample_id_b": "R", "role_a": "relative", "role_b": "relative"},
    ]

    relative = _annotate_relative(
        rows, parent_or_child=(), length=_LOCUS_LENGTH, locus=_ROI, relationships=relationships
    )["R"]

    assert _coloured_spans(relative, "hap1") + _coloured_spans(relative, "hap2") == [(_WINDOW[0], _WINDOW[1], "0")]
    assert PATERNAL not in {block[f"{lane}_lineage"] for block in relative for lane in ("hap1", "hap2")}


def test_an_anchor_whose_colour_changes_across_the_window_gives_none():
    dark, light = HomologAssignment(MATERNAL, 0), HomologAssignment(MATERNAL, 1)
    resolver = HomologResolver(runs={0: [(0, dark), (31_000_000, light)], 1: [(0, light)]})

    assert _colour_across(resolver, 0, *_WINDOW) is None
    assert _colour_across(resolver, 0, 0, 30_000_000) == dark
    assert _colour_across(resolver, 1, *_WINDOW) == light


def test_the_genome_wide_precompute_reads_a_distant_relative_at_the_roi(monkeypatch):
    import asyncio

    from backend.app.services import bed_service, clickhouse_family_variants
    from backend.app.services.family_metadata_context import FamilyMetadataContext

    rows = _locus_rows(3, shares=((20_000_000, 40_000_000, 1),))

    async def fetch(context, *, chrom, start, end, limit):
        return [row for row in rows if start <= row[0] <= end]

    monkeypatch.setattr(clickhouse_family_variants, "fetch_imputed_phased_genotypes", fetch)
    names = [member["sample_id"] for member in _RELATIVE_MEMBERS]
    whole = {"chr": "1", "start": 0, "end": _LOCUS_LENGTH, "ps": None}
    segments = {
        "F": [{**whole, "hap1": "0", "hap2": "1"}],
        "M": [{**whole, "hap1": "0", "hap2": "1"}],
        "E1": [{**whole, "hap1": "0", "hap2": "0"}],
        "R": [{**whole, "hap1": "1", "hap2": "0"}],
    }

    def relative(roi):
        context = FamilyMetadataContext(
            family_uuid="family-uuid",
            family_id="FAM1",
            project_ids=[],
            sample_rows=_RELATIVE_MEMBERS,
            sample_uuid_to_name={f"{name}-uuid": name for name in names},
            sample_name_to_uuid={name: f"{name}-uuid" for name in names},
            affected_sample_names=["M", "R"],
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
            relationship_rows=_RELATIVE_RELATIONSHIPS,
            roi=roi,
        )
        blocks = asyncio.run(bed_service._compute_genomewide_lineage(context, segments_by_name=segments))["R"]
        return _coloured_spans(blocks, "hap1") + _coloured_spans(blocks, "hap2")

    # One chromosome cannot show a parent or child, so R is read at the ROI only.
    assert relative(("chr1", *_ROI)) == [(_WINDOW[0], _WINDOW[1], "1")]
    assert relative(("chr2", *_ROI)) == []
    assert relative(None) == []
