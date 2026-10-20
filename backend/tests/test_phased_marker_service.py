import asyncio

import pytest

from backend.app.services import phased_marker_service
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.phased_marker_service import (
    PHASED_FETCH_LIMIT,
    compute_phased_markers,
    get_family_phased_markers_response,
    hemizygous_x_ranges,
    is_one_copy_x_site,
)
from backend.app.services.haplotype_block_builder import _parent_sample_names
from backend.app.services.sex_chromosomes import pseudoautosomal_regions


def _context(sample_rows, relationship_rows):
    """Minimal context carrying only what ``_parent_sample_names`` reads."""
    return FamilyMetadataContext(
        family_uuid="fam-uuid",
        family_id="fam",
        project_ids=[],
        sample_rows=sample_rows,
        sample_uuid_to_name={},
        sample_name_to_uuid={},
        affected_sample_names=[],
        assembly_id=None,
        assembly_name=None,
        relationship_rows=relationship_rows,
    )


def _sample(sample_id, role):
    return {"sample_id": sample_id, "role": role}


def _parent_child(parent, child, role_a):
    return {
        "relationship_type": "parent_child",
        "sample_id_a": parent,
        "sample_id_b": child,
        "role_a": role_a,
    }


def test_parent_names_pick_index_father_over_lexically_first_grandfather():
    # Two role=father samples: a paternal grandfather "AAA_GF" sorts first, but only
    # the index father "FATHER" co-parents the children with the mother. The
    # pedigree-aware resolver must return the index father, not the grandfather.
    sample_rows = [
        _sample("AAA_GF", "father"),  # paternal grandfather, sorts lexically first
        _sample("FATHER", "father"),  # index father
        _sample("MOTHER", "mother"),
        _sample("E1", "embryo"),
        _sample("E2", "embryo"),
    ]
    relationship_rows = [
        _parent_child("AAA_GF", "FATHER", "father"),  # grandfather -> father
        _parent_child("FATHER", "E1", "father"),
        _parent_child("FATHER", "E2", "father"),
        _parent_child("MOTHER", "E1", "mother"),
        _parent_child("MOTHER", "E2", "mother"),
    ]
    father, mother = _parent_sample_names(_context(sample_rows, relationship_rows))
    assert (father, mother) == ("FATHER", "MOTHER")


def test_parent_names_pick_index_mother_over_lexically_first_grandmother():
    # Symmetric case: two role=mother samples, the grandmother sorts first but does
    # not co-parent the index children.
    sample_rows = [
        _sample("AAA_GM", "mother"),  # maternal grandmother, sorts lexically first
        _sample("FATHER", "father"),
        _sample("MOTHER", "mother"),  # index mother
        _sample("E1", "embryo"),
        _sample("E2", "embryo"),
    ]
    relationship_rows = [
        _parent_child("AAA_GM", "MOTHER", "mother"),  # grandmother -> mother
        _parent_child("FATHER", "E1", "father"),
        _parent_child("FATHER", "E2", "father"),
        _parent_child("MOTHER", "E1", "mother"),
        _parent_child("MOTHER", "E2", "mother"),
    ]
    father, mother = _parent_sample_names(_context(sample_rows, relationship_rows))
    assert (father, mother) == ("FATHER", "MOTHER")


def test_parent_names_fall_back_to_role_first_match_without_relationships():
    # No relationships -> cannot build a pedigree; fall back to the first father /
    # mother in sample_rows order (existing behaviour).
    sample_rows = [
        _sample("AAA_GF", "father"),  # first role=father in order
        _sample("FATHER", "father"),
        _sample("MOTHER", "mother"),  # first role=mother in order
        _sample("AAA_GM", "mother"),
        _sample("E1", "embryo"),
    ]
    father, mother = _parent_sample_names(_context(sample_rows, []))
    assert (father, mother) == ("AAA_GF", "MOTHER")


def test_parent_names_single_parent_donor_family():
    # Single-parent (donor) family: the embryos have only a father; the only other
    # role=mother sample is the paternal grandmother. The resolver must return
    # (father, None) -- NOT back-fill the donor side with the grandmother.
    sample_rows = [
        _sample("FATHER", "father"),
        _sample("AAA_GM", "mother"),  # paternal grandmother (role mother), sorts first
        _sample("E1", "embryo"),
    ]
    relationship_rows = [
        _parent_child("FATHER", "E1", "father"),
        _parent_child("AAA_GM", "FATHER", "mother"),  # grandmother -> father
    ]
    father, mother = _parent_sample_names(_context(sample_rows, relationship_rows))
    assert (father, mother) == ("FATHER", None)


def test_transmitted_single_parent_haplotype():
    t = phased_marker_service._transmitted_single_parent_haplotype
    assert t(("0", "1"), ("0", "0")) == "0"  # parent het, child hom-0 -> homolog 0
    assert t(("0", "1"), ("1", "1")) == "1"  # parent het, child hom-1 -> homolog 1
    assert t(("1", "0"), ("0", "0")) == "1"  # parent het (reversed) -> homolog 1
    assert t(("0", "1"), ("0", "1")) is None  # child het -> donor could supply either
    assert t(("0", "0"), ("0", "0")) is None  # parent hom -> homolog indistinguishable


def test_single_parent_markers_paternal_lane_only():
    # Donor family: only the father is known. The embryo's inherited paternal homolog
    # is resolvable at father-het + child-hom sites; the donor (maternal) lane is blank.
    rows = _rows(
        (100, {"F": "0|1", "C": "0|0"}),  # F het, C hom-0 -> transmitted homolog 0
        (200, {"F": "0|1", "C": "1|1"}),  # F het, C hom-1 -> transmitted homolog 1
        (300, {"F": "0|1", "C": "0|1"}),  # F het, C het  -> ambiguous (donor unknown)
        (400, {"F": "1|1", "C": "0|1"}),  # F hom        -> homolog indistinguishable
    )
    out, qc = compute_phased_markers(
        rows, father="F", mother=None, children=["C"], father_shade={0: 0, 1: 1}, mother_shade={}
    )
    # Embryo: only the two resolvable sites, on the paternal lane; donor lane is None.
    assert [(m.pos, m.hap1, m.hap2) for m in out["C"]] == [(100, 0, None), (200, 1, None)]
    # Father still emits his own raw allele markers; the donor is absent entirely.
    assert [(m.pos, m.hap1, m.hap2) for m in out["F"]][0] == (100, 0, 1)
    assert "M" not in out
    assert qc["C"].informative_sites == 4 and qc["C"].mendel_errors == 0


def _rows(*variants):
    """variants: (pos, {sample: gt}) -> the (pos, sample_ids, gts) tuple shape."""
    return [(pos, list(gt.keys()), list(gt.values())) for pos, gt in variants]


def test_tracks_a_paternal_recombination_mother_homozygous():
    rows = _rows(
        (100, {"F": "0|1", "M": "0|0", "C": "0|0"}),  # paternal homolog 0
        (200, {"F": "0|1", "M": "0|0", "C": "0|0"}),  # 0
        (300, {"F": "0|1", "M": "0|0", "C": "0|1"}),  # 1  <- switch
        (400, {"F": "0|1", "M": "0|0", "C": "0|1"}),  # 1
    )
    # Identity shade maps (paternal block shows shade 0 on hap1, 1 on hap2) -> the
    # raw inherited homolog index passes through unchanged. The point of the test is
    # that the recombination switch (homolog 0 -> 1) is tracked per site.
    out, _ = compute_phased_markers(
        rows, father="F", mother="M", children=["C"], father_shade={0: 0, 1: 1}, mother_shade={}
    )
    markers = out["C"]
    assert [m.hap1 for m in markers] == [0, 0, 1, 1]  # paternal homolog inherited
    # mother homozygous everywhere -> maternal side uninformative
    assert all(m.hap2 is None for m in markers)


def test_maternal_informative_when_father_homozygous():
    rows = _rows((100, {"F": "0|0", "M": "0|1", "C": "0|1"}))
    out, _ = compute_phased_markers(
        rows, father="F", mother="M", children=["C"], father_shade={}, mother_shade={0: 0, 1: 1}
    )
    marker = out["C"][0]
    assert marker.hap2 == 1  # maternal homolog inherited
    assert marker.hap1 is None


def test_orients_homologs_by_the_parent_stored_block_shade():
    # The father's STORED block was oriented (flipped) at upload so raw homolog 0
    # displays shade 1 and raw homolog 1 displays shade 0. The child's markers must
    # be mapped through that shade map, NOT recounted in-region — so raw [0,0,0,1]
    # becomes displayed shades [1,1,1,0], matching the father's block everywhere.
    rows = _rows(
        (100, {"F": "0|1", "M": "0|0", "C": "0|0"}),  # raw 0
        (200, {"F": "0|1", "M": "0|0", "C": "0|0"}),  # raw 0
        (300, {"F": "0|1", "M": "0|0", "C": "0|0"}),  # raw 0
        (400, {"F": "0|1", "M": "0|0", "C": "0|1"}),  # raw 1
    )
    out, _ = compute_phased_markers(
        rows, father="F", mother="M", children=["C"], father_shade={0: 1, 1: 0}, mother_shade={}
    )
    assert [m.hap1 for m in out["C"]] == [1, 1, 1, 0]


def test_child_marker_matches_parent_block_shade_regardless_of_affected_status():
    # Regression for FIX H1: orientation comes ONLY from the parents' stored-block
    # shade maps, never from which/whether children are 'affected'. The same rows
    # and shade maps must yield the same shade-mapped markers no matter what an
    # affected-status set would have been — there is no longer an `affected` knob.
    rows = _rows(
        (100, {"F": "0|1", "M": "0|1", "C": "0|0"}),  # paternal raw 0, maternal raw 0
        (200, {"F": "0|1", "M": "0|1", "C": "1|1"}),  # paternal raw 1, maternal raw 1
    )
    # Father block flipped at upload (0->1, 1->0); mother block un-flipped (identity).
    father_shade = {0: 1, 1: 0}
    mother_shade = {0: 0, 1: 1}
    out, _ = compute_phased_markers(
        rows, father="F", mother="M", children=["C"], father_shade=father_shade, mother_shade=mother_shade
    )
    # paternal raw [0,1] -> shade [1,0]; maternal raw [0,1] -> shade [0,1].
    assert [(m.hap1, m.hap2) for m in out["C"]] == [(1, 0), (0, 1)]


def test_single_marker_switches_are_preserved_raw():
    # An isolated switch (the "0" amid 1s) is the kind of phasing noise/uncertainty
    # this overlay exists to surface. It must NOT be smoothed away — every site is
    # emitted as its own raw call, one marker per position.
    rows = _rows(
        (10, {"F": "0|1", "M": "0|0", "C": "0|1"}),  # paternal 1
        (20, {"F": "0|1", "M": "0|0", "C": "0|1"}),  # 1
        (30, {"F": "0|1", "M": "0|0", "C": "0|0"}),  # 0  (isolated switch)
        (40, {"F": "0|1", "M": "0|0", "C": "0|1"}),  # 1
    )
    out, _ = compute_phased_markers(
        rows, father="F", mother="M", children=["C"], father_shade={0: 0, 1: 1}, mother_shade={}
    )
    assert [(m.pos, m.hap1) for m in out["C"]] == [(10, 1), (20, 1), (30, 0), (40, 1)]


def test_emits_parent_markers_as_their_own_phased_alleles():
    rows = _rows(
        (100, {"F": "0|1", "M": "0|0", "C": "0|0"}),
        (200, {"F": "1|0", "M": "0|1", "C": "0|1"}),
    )
    # Parent markers are their own raw alleles and must NOT be shade-mapped, even
    # when shade maps are supplied (those map child homolog indices only).
    out, _ = compute_phased_markers(
        rows, father="F", mother="M", children=["C"], father_shade={0: 1, 1: 0}, mother_shade={0: 1, 1: 0}
    )
    # Parents carry the alleles on their own two homologs (raw, not oriented).
    assert [(m.pos, m.hap1, m.hap2) for m in out["F"]] == [(100, 0, 1), (200, 1, 0)]
    assert [(m.pos, m.hap1, m.hap2) for m in out["M"]] == [(100, 0, 0), (200, 0, 1)]


def test_ambiguous_markers_are_dropped_for_the_child_only():
    # both parents het, child het -> parent-of-origin ambiguous on both sides
    rows = _rows((100, {"F": "0|1", "M": "0|1", "C": "0|1"}))
    out, _ = compute_phased_markers(
        rows, father="F", mother="M", children=["C"], father_shade={0: 0, 1: 1}, mother_shade={0: 0, 1: 1}
    )
    assert out["C"] == []
    # ...but the parents still get their own raw markers at that site.
    assert [(m.hap1, m.hap2) for m in out["F"]] == [(0, 1)]
    assert [(m.hap1, m.hap2) for m in out["M"]] == [(0, 1)]


def test_requires_both_parents_present_in_the_data():
    rows = _rows((100, {"M": "0|0", "C": "0|1"}))  # father absent
    out, _ = compute_phased_markers(
        rows, father="F", mother="M", children=["C"], father_shade={}, mother_shade={}
    )
    assert out["C"] == []
    assert out["F"] == []
    assert out["M"] == []


def test_qc_counts_informative_sites_and_mendel_errors():
    # Five jointly-genotyped sites for child C:
    #   100: F 0|0, M 0|0, C 1|1  -> impossible (no parent carries a 1) => MENDEL ERROR
    #   200: F 0|1, M 0|1, C 0|1  -> consistent but parent-of-origin ambiguous (NOT an error)
    #   300: F 0|1, M 0|0, C 0|1  -> consistent, paternal-informative
    #   400: F 0|0, M 0|1, C 0|1  -> consistent, maternal-informative
    #   500: F 1|1, M 0|0, C 0|0  -> impossible (father can only give a 1) => MENDEL ERROR
    # A 6th site (600) has the father missing -> NOT jointly informative, must be ignored.
    rows = _rows(
        (100, {"F": "0|0", "M": "0|0", "C": "1|1"}),
        (200, {"F": "0|1", "M": "0|1", "C": "0|1"}),
        (300, {"F": "0|1", "M": "0|0", "C": "0|1"}),
        (400, {"F": "0|0", "M": "0|1", "C": "0|1"}),
        (500, {"F": "1|1", "M": "0|0", "C": "0|0"}),
        (600, {"M": "0|0", "C": "0|1"}),  # father missing -> not jointly informative
    )
    _, qc = compute_phased_markers(
        rows, father="F", mother="M", children=["C"], father_shade={}, mother_shade={}
    )
    child_qc = qc["C"]
    assert child_qc.informative_sites == 5  # site 600 (father missing) excluded
    assert child_qc.mendel_errors == 2  # only the two impossible sites, NOT the ambiguous one
    assert child_qc.mendel_rate == 2 / 5
    # Parents and relatives have no QC object.
    assert set(qc.keys()) == {"C"}


def test_qc_clean_trio_has_no_mendel_errors():
    rows = _rows(
        (100, {"F": "0|1", "M": "0|0", "C": "0|1"}),
        (200, {"F": "0|0", "M": "0|1", "C": "0|1"}),
    )
    _, qc = compute_phased_markers(
        rows, father="F", mother="M", children=["C"], father_shade={}, mother_shade={}
    )
    assert qc["C"].informative_sites == 2
    assert qc["C"].mendel_errors == 0
    assert qc["C"].mendel_rate == 0.0


def _full_context():
    """Context with a resolvable father/mother/child trio and an assembly name, so
    ``get_family_phased_markers_response`` reaches the fetch (rather than the early
    no-parents guard)."""
    sample_rows = [
        _sample("FATHER", "father"),
        _sample("MOTHER", "mother"),
        _sample("CHILD", "embryo"),
    ]
    relationship_rows = [
        _parent_child("FATHER", "CHILD", "father"),
        _parent_child("MOTHER", "CHILD", "mother"),
    ]
    return FamilyMetadataContext(
        family_uuid="fam-uuid",
        family_id="fam",
        project_ids=[],
        sample_rows=sample_rows,
        sample_uuid_to_name={},
        sample_name_to_uuid={"FATHER": "f-uuid", "MOTHER": "m-uuid", "CHILD": "c-uuid"},
        affected_sample_names=[],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
        relationship_rows=relationship_rows,
    )


def test_truncated_fetch_flags_truncated_and_suppresses_markers(monkeypatch):
    # When the per-site fetch returns the full cap (PHASED_FETCH_LIMIT rows) the tail
    # beyond the highest fetched pos was silently dropped, so the overlay would be
    # partial/misleading. The response must flag truncated=True, carry no markers or
    # sites, and report the [min_pos, max_pos] span that was actually covered.
    capped_rows = [
        (1000 + i, "A", "G", ["FATHER", "MOTHER", "CHILD"], ["0|1", "0|0", "0|0"])
        for i in range(PHASED_FETCH_LIMIT)
    ]

    async def _fake_fetch(context, *, chrom, start, end, limit):
        assert limit == PHASED_FETCH_LIMIT
        return capped_rows

    async def _fake_shade_maps(context, *, chr, father, mother, start, end):
        return {}, {}

    monkeypatch.setattr(phased_marker_service, "fetch_imputed_phased_genotypes", _fake_fetch)
    monkeypatch.setattr(phased_marker_service, "_parent_block_shade_maps", _fake_shade_maps)

    response = asyncio.run(
        get_family_phased_markers_response(
            _full_context(), chr="1", start=0, end=10_000_000
        )
    )

    assert response.truncated is True
    assert response.covered == [1000, 1000 + PHASED_FETCH_LIMIT - 1]
    assert response.sites == []
    # Members are still listed (so the track can label lanes) but carry no marker dots.
    assert {s.sample for s in response.samples} == {"FATHER", "MOTHER", "CHILD"}
    assert all(s.markers == [] for s in response.samples)


def test_untruncated_fetch_keeps_markers_and_is_not_flagged(monkeypatch):
    # A normal (sub-cap) fetch returns real markers and truncated stays False.
    rows = [
        (1000, "A", "G", ["FATHER", "MOTHER", "CHILD"], ["0|1", "0|0", "0|0"]),
        (2000, "C", "T", ["FATHER", "MOTHER", "CHILD"], ["0|1", "0|0", "0|1"]),
    ]

    async def _fake_fetch(context, *, chrom, start, end, limit):
        return rows

    async def _fake_shade_maps(context, *, chr, father, mother, start, end):
        return {}, {}

    monkeypatch.setattr(phased_marker_service, "fetch_imputed_phased_genotypes", _fake_fetch)
    monkeypatch.setattr(phased_marker_service, "_parent_block_shade_maps", _fake_shade_maps)

    response = asyncio.run(
        get_family_phased_markers_response(
            _full_context(), chr="1", start=0, end=10_000_000
        )
    )

    assert response.truncated is False
    assert response.covered is None
    assert len(response.sites) == 2
    child = next(s for s in response.samples if s.sample == "CHILD")
    # The child inherited the paternal homolog at the second (informative) site.
    assert any(m.hap1 is not None for m in child.markers)
    # QC is surfaced for the child: both sites are jointly informative, neither is a
    # Mendelian inconsistency.
    assert child.qc is not None
    assert child.qc.informative_sites == 2
    assert child.qc.mendel_errors == 0
    assert child.qc.mendel_rate == 0.0
    # Parents carry no QC object (parent-of-origin is undefined for them).
    father = next(s for s in response.samples if s.sample == "FATHER")
    assert father.qc is None


# --- chrX outside the pseudo-autosomal regions: a man has one X (CLIN-60) ----------------
#
# There a member recorded as male is read as one copy, his call written as one allele ("1")
# or twice ("1|1"); a son is checked against his mother alone, and a daughter receives her
# father's one X. 31 Mb (DMD) lies outside the PARs of GRCh38 and GRCh37.

X_SITE = 31_000_000
SEXES = {"F": "male", "M": "female", "SON": "male", "DAU": "female", "UND": "und"}


def _x_counts(rows, *, children, father="F", mother="M", chrom="X", assembly_name="GRCh38", sexes=SEXES):
    """The markers, and each child's (informative sites, Mendelian errors)."""
    out, qc = compute_phased_markers(
        rows,
        father=father,
        mother=mother,
        children=children,
        father_shade={},
        mother_shade={},
        chrom=chrom,
        assembly_name=assembly_name,
        sexes=sexes,
    )
    return out, {child: (counts.informative_sites, counts.mendel_errors) for child, counts in qc.items()}


@pytest.mark.parametrize(("father", "son"), [("0", "1"), ("0|0", "1|1")], ids=["one-allele", "two-copy-coded"])
def test_x_a_son_is_read_on_his_mother_x_alone(father, son):
    # His mother is heterozygous at every site and he carries her homolog 1 (the ALT); his
    # father's one X carries the REF. Read as two copies, the father's calls written once
    # left no site, and written twice made every site a Mendelian error.
    rows = _rows(*((X_SITE + i, {"F": father, "M": "0|1", "SON": son}) for i in range(4)))
    out, counts = _x_counts(rows, children=["SON"])
    assert counts == {"SON": (4, 0)}
    # A maternal marker at every site, never a paternal one: his father gave him a Y.
    assert [(m.hap1, m.hap2) for m in out["SON"]] == [(None, 1)] * 4


def test_x_a_father_son_pair_is_neither_informative_nor_an_error():
    rows = _rows(
        (X_SITE, {"F": "1", "M": "0|0", "SON": "0"}),  # his father's allele is not his: no error
        (X_SITE + 1, {"M": "0|1", "SON": "1"}),  # his father has no call: he is read all the same
        (X_SITE + 2, {"F": "0|1", "M": "0|1", "SON": "0|0"}),  # nor does a call his father cannot have matter
    )
    out, counts = _x_counts(rows, children=["SON"])
    assert counts == {"SON": (3, 0)}
    assert [(m.pos, m.hap1, m.hap2) for m in out["SON"]] == [(X_SITE + 1, None, 1), (X_SITE + 2, None, 0)]


def test_x_a_son_of_a_known_father_alone_is_not_counted():
    # A donor mother: the son's X is hers, so nothing of his can be checked against his father.
    rows = _rows((X_SITE, {"F": "0|0", "SON": "1|1"}), (X_SITE + 1, {"F": "1", "SON": "0"}))
    out, counts = _x_counts(rows, children=["SON"], mother=None)
    assert counts == {"SON": (0, 0)}
    assert out["SON"] == []


def test_x_a_son_allele_his_mother_lacks_or_a_call_of_two_alleles_is_an_error():
    rows = _rows(
        (X_SITE, {"F": "1", "M": "0|0", "SON": "1"}),  # an allele his mother does not carry
        (X_SITE + 1, {"F": "0", "M": "0|1", "SON": "0|1"}),  # two different alleles: not one X
        (X_SITE + 2, {"F": "0", "M": "0|0", "SON": "0/1"}),  # unphased, the same
        (X_SITE + 3, {"F": "0", "M": "0|1", "SON": "1/1"}),  # consistent, however written
        (X_SITE + 4, {"F": "0", "M": "0|1", "SON": "./."}),  # no call: not counted
        (X_SITE + 5, {"F": "0", "M": "0|1", "SON": "1|."}),  # a half call: not counted
        (X_SITE + 6, {"F": "0", "M": "0/1", "SON": "1"}),  # his mother's call is not phased: not counted
    )
    out, counts = _x_counts(rows, children=["SON"])
    assert counts == {"SON": (4, 3)}
    assert [(m.pos, m.hap2) for m in out["SON"]] == [(X_SITE + 3, 1)]


@pytest.mark.parametrize("father", ["1", "1|1"], ids=["one-allele", "two-copy-coded"])
def test_x_a_daughter_receives_her_father_one_x(father):
    rows = _rows(
        (X_SITE, {"F": father, "M": "0|1", "DAU": "1|0"}),  # his allele and her mother's homolog 0
        (X_SITE + 1, {"F": father, "M": "0|1", "DAU": "1|1"}),  # his allele and her mother's homolog 1
        (X_SITE + 2, {"F": father, "M": "0|0", "DAU": "0|0"}),  # she lacks his allele: an error
    )
    out, counts = _x_counts(rows, children=["DAU"])
    assert counts == {"DAU": (3, 1)}
    # Her mother's homolog is read wherever her mother is heterozygous. Her father has one X,
    # so his side tells no homolog apart.
    assert [(m.pos, m.hap1, m.hap2) for m in out["DAU"]] == [(X_SITE, None, 0), (X_SITE + 1, None, 1)]
    # The parents' own markers: his one allele on both lanes, as a call written twice reads.
    assert [(m.pos, m.hap1, m.hap2) for m in out["F"]] == [(pos, 1, 1) for pos in range(X_SITE, X_SITE + 3)]


def test_x_a_call_of_two_alleles_from_a_father_is_not_read():
    rows = _rows((X_SITE, {"F": "0|1", "M": "0|0", "DAU": "0|1", "SON": "0"}))
    out, counts = _x_counts(rows, children=["DAU", "SON"])
    # His daughter is not checked against a genotype he cannot have; his son does not need him.
    assert counts == {"DAU": (0, 0), "SON": (1, 0)}
    # The parents get no marker of their own at the site.
    assert out["F"] == [] and out["M"] == []


def test_x_a_child_whose_sex_is_not_recorded_has_two_copies_as_before():
    # Read as receiving one X from each parent, as before; her father is read by his own sex.
    rows = _rows(
        (X_SITE, {"F": "0|0", "M": "0|1", "UND": "1|1"}),
        (X_SITE + 1, {"F": "0", "M": "0|1", "UND": "0|1"}),
    )
    out, counts = _x_counts(rows, children=["UND"])
    assert counts == {"UND": (2, 1)}
    assert [(m.pos, m.hap1, m.hap2) for m in out["UND"]] == [(X_SITE + 1, None, 1)]
    # Without any recorded sex every member has two copies, and a call written once is no call.
    _, unrecorded = _x_counts(rows, children=["UND"], sexes=None)
    assert unrecorded == {"UND": (1, 1)}


@pytest.mark.parametrize(
    ("assembly", "chrom"),
    [("GRCh38", "X"), ("GRCh38", "chrX"), ("GRCh37", "X"), ("hg19", "chrX")],
)
def test_x_a_man_has_two_copies_inside_the_pars(assembly, chrom):
    # A son written as two copies of an allele his father does not carry: inside a PAR he has
    # two copies (his father passes him the PAR on his Y), so it is an error there, as before;
    # one position outside the PAR it is his mother's allele, and no error.
    (par1_first, par1_last), (par2_first, par2_last) = pseudoautosomal_regions(assembly)["X"]
    expected = {
        par1_first: (1, 1),
        par1_last: (1, 1),
        par1_last + 1: (1, 0),
        par2_first - 1: (1, 0),
        par2_first: (1, 1),
        par2_last: (1, 1),
        par2_last + 1: (1, 0),
    }
    for pos, counts in expected.items():
        rows = _rows((pos, {"F": "0|0", "M": "0|1", "SON": "1|1"}))
        assert _x_counts(rows, children=["SON"], chrom=chrom, assembly_name=assembly)[1] == {"SON": counts}, pos


@pytest.mark.parametrize(
    ("chrom", "assembly"),
    [("7", "GRCh38"), ("X", "T2T-CHM13v2.0"), ("X", None), (None, "GRCh38")],
    ids=["autosome", "T2T", "no-assembly", "no-chromosome"],
)
def test_x_two_copies_where_a_man_is_not_known_to_have_one(chrom, assembly):
    # On an autosome, on an assembly whose PAR bounds CoGA does not list (T2T-CHM13), and
    # without the chromosome or assembly, every member has two copies, as before.
    rows = _rows((X_SITE, {"F": "0|0", "M": "0|1", "SON": "1|1"}), (X_SITE + 1, {"F": "0", "M": "0|1", "SON": "1"}))
    assert _x_counts(rows, children=["SON"], chrom=chrom, assembly_name=assembly)[1] == {"SON": (1, 1)}


@pytest.mark.parametrize("assembly", ["GRCh38", "GRCh37"])
def test_x_ranges_hold_exactly_the_sites_where_a_man_has_one_copy(assembly):
    regions = pseudoautosomal_regions(assembly)["X"]
    probes = sorted(
        {pos for first, last in regions for pos in (first - 1, first, first + 1, last - 1, last, last + 1)}
        | {0, 1, X_SITE}
    )
    windows = [
        (0, 160_000_000), (0, 5_000), (2_000_000, 3_000_000), (30_000_000, 32_000_000), (154_000_000, 157_000_000),
    ]
    for start, end in windows:
        ranges = hemizygous_x_ranges(assembly, "chrX", start, end)
        for pos in probes:
            if start <= pos <= end:
                one_copy = any(first <= pos <= last for first, last in ranges)
                assert one_copy == is_one_copy_x_site(assembly, "chrX", pos), (start, end, pos)


def test_x_ranges_are_empty_where_no_man_has_one_copy():
    assert hemizygous_x_ranges("GRCh38", "X", 0, 3_000_000) == [[0, 10_000], [2_781_480, 3_000_000]]
    assert hemizygous_x_ranges("GRCh38", "X", 100_000, 200_000) == []  # inside PAR1
    assert hemizygous_x_ranges("GRCh38", "7", 0, 3_000_000) == []
    assert hemizygous_x_ranges("T2T-CHM13v2.0", "X", 0, 3_000_000) == []
    assert hemizygous_x_ranges(None, "X", 0, 3_000_000) == []


def _x_family_context():
    """A couple, a son and a daughter, with their sexes recorded, on GRCh38."""
    sample_rows = [
        {"sample_id": "FATHER", "role": "father", "sex": "male"},
        {"sample_id": "MOTHER", "role": "mother", "sex": "female"},
        {"sample_id": "SON", "role": "embryo", "sex": "male"},
        {"sample_id": "DAUGHTER", "role": "embryo", "sex": "female"},
    ]
    relationship_rows = [
        _parent_child(parent, child, role)
        for child in ("SON", "DAUGHTER")
        for parent, role in (("FATHER", "father"), ("MOTHER", "mother"))
    ]
    return FamilyMetadataContext(
        family_uuid="fam-uuid",
        family_id="fam",
        project_ids=[],
        sample_rows=sample_rows,
        sample_uuid_to_name={},
        sample_name_to_uuid={row["sample_id"]: f"u-{row['sample_id']}" for row in sample_rows},
        affected_sample_names=[],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
        relationship_rows=relationship_rows,
    )


def test_x_the_roi_marker_review_counts_a_son_on_his_mother_x(monkeypatch):
    # The men's calls written as one allele, as GLIMPSE2 writes a haploid X.
    members = ["FATHER", "MOTHER", "SON", "DAUGHTER"]
    rows = [(X_SITE + i, "A", "G", members, ["0", "0|1", "1", "0|1"]) for i in range(3)]

    async def _fake_fetch(context, *, chrom, start, end, limit):
        return rows

    async def _fake_shade_maps(context, *, chr, father, mother, start, end):
        return {}, {}

    monkeypatch.setattr(phased_marker_service, "fetch_imputed_phased_genotypes", _fake_fetch)
    monkeypatch.setattr(phased_marker_service, "_parent_block_shade_maps", _fake_shade_maps)
    response = asyncio.run(
        get_family_phased_markers_response(_x_family_context(), chr="X", start=30_000_000, end=32_000_000)
    )

    by_sample = {sample.sample: sample for sample in response.samples}
    son, daughter = by_sample["SON"], by_sample["DAUGHTER"]
    assert son.qc is not None and (son.qc.informative_sites, son.qc.mendel_errors, son.qc.mendel_rate) == (3, 0, 0.0)
    assert [(m.hap1, m.hap2) for m in son.markers] == [(None, 1)] * 3
    assert daughter.qc is not None and (daughter.qc.informative_sites, daughter.qc.mendel_errors) == (3, 0)
    assert [(m.hap1, m.hap2) for m in daughter.markers] == [(None, 1)] * 3
    # The sites the review shows, and where in its window a man has one X.
    assert [site.pos for site in response.sites] == [X_SITE, X_SITE + 1, X_SITE + 2]
    assert response.hemizygous_x_ranges == [[30_000_000, 32_000_000]]
