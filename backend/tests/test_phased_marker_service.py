import asyncio

from backend.app.services import phased_marker_service
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.phased_marker_service import (
    PHASED_FETCH_LIMIT,
    compute_phased_markers,
    get_family_phased_markers_response,
)
from backend.app.services.haplotype_block_builder import _parent_sample_names


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
