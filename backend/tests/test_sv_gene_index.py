"""Phase 0 of SNV + SV compound het: the SV→gene index + the badge summary."""

from __future__ import annotations

import asyncio
import types

import pytest

from backend.app.services import clickhouse_family_variants as cfv
from backend.app.services.sv_gene_index_service import summarize_second_hit


def _sv(sv_type: str, gt: dict[str, str], ps: dict[str, int] | None = None) -> dict:
    return {
        "sv_id": "v",
        "sv_type": sv_type,
        "chr": "1",
        "start": 1,
        "end": 9,
        "gt": gt,
        "ps": ps or {},
    }


def test_deletion_in_affected_is_flagged_and_het() -> None:
    summary = summarize_second_hit([_sv("DEL", {"S1": "0/1"})], ["S1"])
    assert summary["sv_count"] == 1
    assert summary["sv_types"] == ["DEL"]
    assert summary["affected_zygosity"] == "het"
    assert summary["has_deletion"] is True  # the unmasking case


def test_homozygous_sv() -> None:
    summary = summarize_second_hit([_sv("DUP", {"S1": "1/1"})], ["S1"])
    assert summary["affected_zygosity"] == "hom"
    assert summary["has_deletion"] is False


def test_mixed_zygosity_across_svs() -> None:
    summary = summarize_second_hit(
        [_sv("DEL", {"S1": "0/1"}), _sv("INS", {"S1": "1/1"})], ["S1"]
    )
    assert summary["affected_zygosity"] == "mixed"
    assert summary["sv_types"] == ["DEL", "INS"]
    assert summary["has_deletion"] is True


def test_no_affected_genotype_leaves_zygosity_unknown() -> None:
    # SV present in the family but not called in the affected sample.
    summary = summarize_second_hit([_sv("INV", {"S2": "0/1"})], ["S1"])
    assert summary["affected_zygosity"] is None
    assert summary["has_deletion"] is False


def _pedigree(parents_of: dict[str, set[str]], males: set[str] | None = None):
    # Imported here so the rest of the module still runs where the pedigree type is absent.
    from backend.app.services.compound_het_phase import FamilyPedigree

    return FamilyPedigree(
        parents_of={child: frozenset(parents) for child, parents in parents_of.items()},
        males=frozenset(males or ()),
        assembly_name="GRCh38",
    )


TRIO = {"child": {"mother", "father"}}


def test_phase_trans_with_unaffected_parents() -> None:
    # Affected child het for the SNV and the DEL. The father carries the SNV and not the
    # DEL, the mother the DEL and not the SNV: one hit from each parent.
    summary = summarize_second_hit(
        [_sv("DEL", {"child": "0/1", "mother": "0/1", "father": "0/0"})],
        ["child"],
        unaffected_samples=["mother", "father"],
        snv_gt_by_sample={"child": "0/1", "mother": "0/0", "father": "0/1"},
        pedigree=_pedigree(TRIO),
    )
    assert summary["phase"] == "trans"
    assert summary["deletion_unmasked"] is True  # DEL in trans with a het SNV → biallelic


def test_an_unaffected_relative_carrying_neither_hit_is_not_evidence_of_trans() -> None:
    # The defect: an unaffected sibling who carries neither hit says nothing about which
    # copy each hit is on, yet the verdict used to be trans (and the pair "biallelic").
    summary = summarize_second_hit(
        [_sv("DEL", {"child": "0/1", "sib": "0/0"})],
        ["child"],
        unaffected_samples=["sib"],
        snv_gt_by_sample={"child": "0/1", "sib": "0/0"},
    )
    assert summary["phase"] == "unknown"
    assert summary["phase_evidence"] is None
    assert summary["deletion_unmasked"] is False


def test_unaffected_members_without_a_pedigree_are_not_evidence_of_trans() -> None:
    # The same genotypes as the trio above, but nothing says these two are the child's
    # parents: carrying one hit each is only informative in a parent.
    summary = summarize_second_hit(
        [_sv("DEL", {"child": "0/1", "mother": "0/1", "father": "0/0"})],
        ["child"],
        unaffected_samples=["mother", "father"],
        snv_gt_by_sample={"child": "0/1", "mother": "0/0", "father": "0/1"},
    )
    assert summary["phase"] == "unknown"
    assert summary["deletion_unmasked"] is False


def test_parents_carrying_neither_hit_leave_the_phase_unknown() -> None:
    # Both hits absent from both parents (de novo, or missed calls): either could sit on
    # either copy. This used to be trans because no unaffected member carried both.
    summary = summarize_second_hit(
        [_sv("DEL", {"child": "0/1", "mother": "0/0", "father": "0/0"})],
        ["child"],
        unaffected_samples=["mother", "father"],
        snv_gt_by_sample={"child": "0/1", "mother": "0/0", "father": "0/0"},
        pedigree=_pedigree(TRIO),
    )
    assert summary["phase"] == "unknown"
    assert summary["deletion_unmasked"] is False


def test_a_de_novo_deletion_is_not_placed_opposite_the_inherited_snv() -> None:
    # The mother passed on the SNV; neither parent carries the DEL, so it arose de novo
    # and may sit on the maternal copy as easily as on the paternal one.
    summary = summarize_second_hit(
        [_sv("DEL", {"child": "0/1", "mother": "0/0", "father": "0/0"})],
        ["child"],
        unaffected_samples=["mother", "father"],
        snv_gt_by_sample={"child": "0/1", "mother": "0/1", "father": "0/0"},
        pedigree=_pedigree(TRIO),
    )
    assert summary["phase"] == "unknown"
    assert summary["deletion_unmasked"] is False


def test_one_parent_carrying_exactly_one_hit_places_the_other_on_the_other_copy() -> None:
    # Only the mother is sequenced. She carries the SNV and has a reference call at the
    # DEL, so the child's DEL is on the paternal copy: trans.
    summary = summarize_second_hit(
        [_sv("DEL", {"child": "0/1", "mother": "0/0"})],
        ["child"],
        unaffected_samples=["mother"],
        snv_gt_by_sample={"child": "0/1", "mother": "0/1"},
        pedigree=_pedigree({"child": {"mother"}}),
    )
    assert summary["phase"] == "trans"
    assert summary["phase_evidence"] == "segregation"
    assert summary["deletion_unmasked"] is True


def test_a_parent_without_a_call_at_the_deletion_does_not_count_as_lacking_it() -> None:
    # Per-sample SV callsets: the mother has no call in the child's SV record, which is
    # not the same as a reference call, so nothing places the DEL.
    summary = summarize_second_hit(
        [_sv("DEL", {"child": "0/1"})],
        ["child"],
        unaffected_samples=["mother"],
        snv_gt_by_sample={"child": "0/1", "mother": "0/1"},
        pedigree=_pedigree({"child": {"mother"}}),
    )
    assert summary["phase"] == "unknown"


def test_a_shallow_parental_reference_call_is_not_evidence() -> None:
    # The mother's SNV reference call has 3 reads: too few to say she does not carry it.
    summary = summarize_second_hit(
        [_sv("DEL", {"child": "0/1", "mother": "0/1"})],
        ["child"],
        unaffected_samples=["mother"],
        snv_gt_by_sample={"child": "0/1", "mother": "0/0"},
        snv_dp_by_sample={"child": 30, "mother": 3},
        pedigree=_pedigree({"child": {"mother"}}),
    )
    assert summary["phase"] == "unknown"

    deep = summarize_second_hit(
        [_sv("DEL", {"child": "0/1", "mother": "0/1"})],
        ["child"],
        unaffected_samples=["mother"],
        snv_gt_by_sample={"child": "0/1", "mother": "0/0"},
        snv_dp_by_sample={"child": 30, "mother": 30},
        pedigree=_pedigree({"child": {"mother"}}),
    )
    assert deep["phase"] == "trans"


def test_both_hits_from_one_parent_are_cis_when_both_parents_are_genotyped() -> None:
    # The mother (affection status not recorded) carries both hits and the father
    # neither: the child's two hits are both on the maternal copy.
    summary = summarize_second_hit(
        [_sv("DEL", {"child": "0/1", "mother": "0/1", "father": "0/0"})],
        ["child"],
        unaffected_samples=["father"],
        snv_gt_by_sample={"child": "0/1", "mother": "0/1", "father": "0/0"},
        pedigree=_pedigree(TRIO),
    )
    assert summary["phase"] == "cis"
    assert summary["phase_evidence"] == "segregation"
    assert summary["deletion_unmasked"] is False


def test_a_male_on_chrx_outside_the_pars_is_not_traced_through_two_parents() -> None:
    # A son has one X, from his mother: a father "carrying" the other hit cannot have
    # passed it to him, so the autosomal trace does not apply.
    sv = _sv("DEL", {"son": "0/1", "mother": "0/1", "father": "0/0"})
    sv.update({"chr": "X", "start": 50_000_000, "end": 50_010_000})
    summary = summarize_second_hit(
        [sv],
        ["son"],
        unaffected_samples=["mother", "father"],
        snv_gt_by_sample={"son": "0/1", "mother": "0/0", "father": "0/1"},
        pedigree=_pedigree({"son": {"mother", "father"}}, males={"son", "father"}),
        snv_locus=("X", 50_005_000),
    )
    assert summary["phase"] == "unknown"


def test_phase_cis_when_unaffected_carries_both() -> None:
    summary = summarize_second_hit(
        [_sv("DUP", {"child": "0/1", "mother": "0/1"})],
        ["child"],
        unaffected_samples=["mother"],
        snv_gt_by_sample={"child": "0/1", "mother": "0/1"},  # mother carries SNV + SV
    )
    assert summary["phase"] == "cis"
    assert summary["deletion_unmasked"] is False


def test_phase_unknown_for_singleton() -> None:
    summary = summarize_second_hit(
        [_sv("DEL", {"child": "0/1"})],
        ["child"],
        unaffected_samples=[],
        snv_gt_by_sample={"child": "0/1"},
    )
    assert summary["phase"] == "unknown"
    assert summary["deletion_unmasked"] is False


def test_phase_unknown_without_snv_genotype() -> None:
    summary = summarize_second_hit([_sv("DEL", {"S1": "0/1"})], ["S1"])
    assert summary["phase"] == "unknown"


def test_read_phase_trans_from_shared_phase_set() -> None:
    # SNV alt on hap 1 (0|1), SV alt on hap 0 (1|0), same phase set 1000 → trans by reads.
    summary = summarize_second_hit(
        [_sv("DEL", {"child": "1|0"}, ps={"child": 1000})],
        ["child"],
        unaffected_samples=[],  # singleton → segregation could not decide, but reads can
        snv_gt_by_sample={"child": "0|1"},
        snv_ps_by_sample={"child": 1000},
    )
    assert summary["phase"] == "trans"
    assert summary["phase_evidence"] == "read"
    assert summary["deletion_unmasked"] is True


def test_read_phase_cis_from_shared_phase_set() -> None:
    # Both alts on hap 1 (0|1) in the same block → cis by reads.
    summary = summarize_second_hit(
        [_sv("DUP", {"child": "0|1"}, ps={"child": 1000})],
        ["child"],
        snv_gt_by_sample={"child": "0|1"},
        snv_ps_by_sample={"child": 1000},
    )
    assert summary["phase"] == "cis"
    assert summary["phase_evidence"] == "read"


def test_read_phase_skipped_for_different_phase_sets() -> None:
    # Different PS blocks can't be compared → fall back to segregation (here unknown).
    summary = summarize_second_hit(
        [_sv("DEL", {"child": "1|0"}, ps={"child": 2000})],
        ["child"],
        unaffected_samples=[],
        snv_gt_by_sample={"child": "0|1"},
        snv_ps_by_sample={"child": 1000},
    )
    assert summary["phase"] == "unknown"
    assert summary["phase_evidence"] is None


# A half call has one allele missing, so it does not say which haplotype carries the alt:
# the missing allele may be an alt too. The SNV + SNV pair declines it; the badge read
# `1|.` as the alt on haplotype 0, and showed trans by read phasing — for a deletion,
# "effectively biallelic" — from a call that places nothing.
@pytest.mark.parametrize("sv_gt", ["1|.", ".|1", "|1", "1|", "2|.", ".|2"])
def test_a_half_called_sv_next_to_a_phased_snv_gives_no_read_backed_phase(sv_gt: str) -> None:
    summary = summarize_second_hit(
        [_sv("DEL", {"child": sv_gt}, ps={"child": 1000})],
        ["child"],
        unaffected_samples=[],  # singleton: the family cannot decide either
        snv_gt_by_sample={"child": "0|1"},
        snv_ps_by_sample={"child": 1000},
    )
    assert summary["phase"] == "unknown"
    assert summary["phase_evidence"] is None
    assert summary["deletion_unmasked"] is False


@pytest.mark.parametrize("snv_gt", ["1|.", ".|1"])
def test_a_half_called_snv_next_to_a_phased_sv_gives_no_read_backed_phase(snv_gt: str) -> None:
    summary = summarize_second_hit(
        [_sv("DEL", {"child": "1|0"}, ps={"child": 1000})],
        ["child"],
        unaffected_samples=[],
        snv_gt_by_sample={"child": snv_gt},
        snv_ps_by_sample={"child": 1000},
    )
    assert summary["phase"] == "unknown"
    assert summary["phase_evidence"] is None
    assert summary["deletion_unmasked"] is False


def test_a_half_called_sv_leaves_the_phase_to_the_parents() -> None:
    # The reads place nothing, so the trio decides, as for calls without a phase set: the
    # father passed on the SNV and has a reference call at the DEL, the mother the DEL.
    summary = summarize_second_hit(
        [_sv("DEL", {"child": ".|1", "mother": "0/1", "father": "0/0"}, ps={"child": 1000})],
        ["child"],
        unaffected_samples=["mother", "father"],
        snv_gt_by_sample={"child": "1|0", "mother": "0/0", "father": "0/1"},
        snv_ps_by_sample={"child": 1000},
        pedigree=_pedigree(TRIO),
    )
    assert summary["phase"] == "trans"
    assert summary["phase_evidence"] == "segregation"


@pytest.mark.parametrize(
    ("snv_gt", "sv_gt", "phase"),
    [("0|1", "1|0", "trans"), ("1|0", "0|1", "trans"), ("0|1", "0|1", "cis"), ("0|1", "2|0", "trans")],
)
def test_fully_called_phased_genotypes_keep_their_read_backed_phase(
    snv_gt: str, sv_gt: str, phase: str
) -> None:
    summary = summarize_second_hit(
        [_sv("DEL", {"child": sv_gt}, ps={"child": 1000})],
        ["child"],
        snv_gt_by_sample={"child": snv_gt},
        snv_ps_by_sample={"child": 1000},
    )
    assert summary["phase"] == phase
    assert summary["phase_evidence"] == "read"
    assert summary["deletion_unmasked"] is (phase == "trans")


def test_segregation_used_when_no_phasing() -> None:
    # The father has no call in the DEL record. The mother's SNV reference call still
    # puts the child's SNV on the paternal copy, and the DEL is credited to the only
    # parent known to carry it, the mother: trans.
    summary = summarize_second_hit(
        [_sv("DEL", {"child": "0/1", "mother": "0/1"})],
        ["child"],
        unaffected_samples=["mother", "father"],
        snv_gt_by_sample={"child": "0/1", "mother": "0/0", "father": "0/1"},
        pedigree=_pedigree(TRIO),
    )
    assert summary["phase"] == "trans"
    assert summary["phase_evidence"] == "segregation"


# CLIN-28: each SV of the gene gets its own phase against the SNV, and the badge's verdict
# is drawn from theirs. One read verdict used to cover all of a gene's SVs, and any cis
# won it: a duplication in cis hid a deletion in trans in the same phase block, and the
# badge said cis✓ where the SNV is the only copy left.


def _sv_at(
    sv_type: str,
    start: int,
    end: int,
    gt: dict[str, str],
    ps: dict[str, int] | None = None,
    cn: dict[str, int] | None = None,
    chrom: str = "1",
) -> dict:
    return {
        "sv_id": f"{sv_type}-{start}",
        "sv_type": sv_type,
        "chr": chrom,
        "start": start,
        "end": end,
        "gt": gt,
        "ps": ps or {},
        "cn": cn or {},
    }


def _verdicts(summary: dict) -> list[tuple[str, str, str | None]]:
    return [(sv["sv_type"], sv["phase"], sv["phase_evidence"]) for sv in summary["svs"]]


# The SNV's alt sits on haplotype 1 of phase set 1000 (``0|1``): an SV at ``0|1`` in that
# block is in cis with it, one at ``1|0`` in trans.
PHASED_SNV = {"snv_gt_by_sample": {"child": "0|1"}, "snv_ps_by_sample": {"child": 1000}}
DUP_IN_CIS = _sv_at("DUP", 2_000, 3_000, {"child": "0|1"}, ps={"child": 1000})
DEL_IN_TRANS = _sv_at("DEL", 5_000, 9_000, {"child": "1|0"}, ps={"child": 1000})
DEL_IN_CIS = _sv_at("DEL", 5_000, 9_000, {"child": "0|1"}, ps={"child": 1000})
DUP_IN_TRANS = _sv_at("DUP", 2_000, 3_000, {"child": "1|0"}, ps={"child": 1000})


@pytest.mark.parametrize("order", [1, -1], ids=["dup-first", "del-first"])
def test_a_deletion_in_trans_wins_over_a_duplication_in_cis_in_one_phase_block(order: int) -> None:
    summary = summarize_second_hit(
        [DUP_IN_CIS, DEL_IN_TRANS][::order], ["child"], unaffected_samples=[], **PHASED_SNV
    )
    # The deletion removes the copy the SNV is not on: the SNV is the only copy left.
    assert summary["phase"] == "trans"
    assert summary["phase_evidence"] == "read"
    assert summary["deletion_unmasked"] is True
    assert summary["has_deletion"] is True
    # Each SV with its own verdict, by position.
    assert _verdicts(summary) == [("DUP", "cis", "read"), ("DEL", "trans", "read")]
    assert [
        (sv["sv_id"], sv["chr"], sv["start"], sv["end"], sv["affected_zygosity"], sv["copy_loss"])
        for sv in summary["svs"]
    ] == [("DUP-2000", "1", 2_000, 3_000, "het", False), ("DEL-5000", "1", 5_000, 9_000, "het", True)]


def test_a_duplication_in_trans_beside_a_deletion_in_cis_is_a_trans_candidate() -> None:
    # The deletion is on the SNV's copy, so nothing is unmasked; the duplication is on the
    # other copy, a compound-het candidate the gene's verdict must not hide behind the cis.
    summary = summarize_second_hit(
        [DEL_IN_CIS, DUP_IN_TRANS], ["child"], unaffected_samples=[], **PHASED_SNV
    )
    assert summary["phase"] == "trans"
    assert summary["phase_evidence"] == "read"
    assert summary["deletion_unmasked"] is False
    assert summary["has_deletion"] is True
    assert _verdicts(summary) == [("DUP", "trans", "read"), ("DEL", "cis", "read")]


def test_two_svs_both_in_cis_keep_the_gene_in_cis() -> None:
    summary = summarize_second_hit(
        [DUP_IN_CIS, DEL_IN_CIS], ["child"], unaffected_samples=[], **PHASED_SNV
    )
    assert summary["phase"] == "cis"
    assert summary["phase_evidence"] == "read"
    assert summary["deletion_unmasked"] is False
    assert _verdicts(summary) == [("DUP", "cis", "read"), ("DEL", "cis", "read")]


@pytest.mark.parametrize(
    ("del_gt", "del_ps"),
    [("0/1", None), (".|1", 1000), ("1|.", 1000), ("1|0", 2000)],
    ids=["unphased", "half-call", "half-call-left", "other-phase-set"],
)
def test_a_deletion_without_read_backed_phase_leaves_a_singleton_unknown(
    del_gt: str, del_ps: int | None
) -> None:
    # The duplication is in cis by the reads, but the deletion is not placed (no phase set,
    # a half call that places nothing (#803), another phase block), and a singleton cannot
    # place it. The gene is no longer called cis on the duplication's word.
    deletion = _sv_at(
        "DEL", 5_000, 9_000, {"child": del_gt}, ps={"child": del_ps} if del_ps else None
    )
    summary = summarize_second_hit(
        [DUP_IN_CIS, deletion], ["child"], unaffected_samples=[], **PHASED_SNV
    )
    assert summary["phase"] == "unknown"
    assert summary["phase_evidence"] is None
    assert summary["deletion_unmasked"] is False
    assert _verdicts(summary) == [("DUP", "cis", "read"), ("DEL", "unknown", None)]


def test_a_deletion_without_read_backed_phase_is_placed_by_the_parents() -> None:
    # The same, in a trio: the father passed on the SNV and has a reference call at the
    # deletion, the mother carries the deletion: it is in trans, and wins.
    duplication = _sv_at(
        "DUP", 2_000, 3_000, {"child": "0|1", "mother": "0/0", "father": "0/0"}, ps={"child": 1000}
    )
    deletion = _sv_at("DEL", 5_000, 9_000, {"child": "0/1", "mother": "0/1", "father": "0/0"})
    summary = summarize_second_hit(
        [duplication, deletion],
        ["child"],
        unaffected_samples=["mother", "father"],
        snv_gt_by_sample={"child": "0|1", "mother": "0/0", "father": "0/1"},
        snv_ps_by_sample={"child": 1000},
        pedigree=_pedigree(TRIO),
    )
    assert summary["phase"] == "trans"
    assert summary["phase_evidence"] == "segregation"
    assert summary["deletion_unmasked"] is True
    assert _verdicts(summary) == [("DUP", "cis", "read"), ("DEL", "trans", "segregation")]


def test_a_parent_carrying_the_snv_and_another_sv_does_not_put_the_deletion_in_cis() -> None:
    # Joint calls. The unaffected father carries the SNV and the duplication, and has a
    # reference call at the deletion; the mother carries only the deletion. The father
    # carried "an SV of the gene" and the SNV, so the gene was cis by segregation, and the
    # deletion from the mother, in trans, was hidden.
    duplication = _sv_at("DUP", 2_000, 3_000, {"child": "0/1", "mother": "0/0", "father": "0/1"})
    deletion = _sv_at("DEL", 5_000, 9_000, {"child": "0/1", "mother": "0/1", "father": "0/0"})
    summary = summarize_second_hit(
        [duplication, deletion],
        ["child"],
        unaffected_samples=["mother", "father"],
        snv_gt_by_sample={"child": "0/1", "mother": "0/0", "father": "0/1"},
        pedigree=_pedigree(TRIO),
    )
    assert summary["phase"] == "trans"
    assert summary["phase_evidence"] == "segregation"
    assert summary["deletion_unmasked"] is True
    assert _verdicts(summary) == [("DUP", "cis", "segregation"), ("DEL", "trans", "segregation")]


def test_a_parents_own_call_of_the_same_sv_stands_in_for_a_per_sample_file() -> None:
    # Per-sample SV files: each record holds one sample's call. The mother's own file holds
    # the child's deletion (its breakpoints a few bases off), the father's an insertion in
    # the gene. Only the same SV counts: the mother carries the deletion, the father is not
    # known to, and the trace puts it in trans. Any SV of the father's used to count, so
    # the father, unaffected and carrying the SNV, made it cis.
    svs = [
        _sv_at("DEL", 10_000, 15_000, {"child": "0/1"}),
        _sv_at("DEL", 10_040, 14_980, {"mother": "0/1"}),
        _sv_at("INS", 12_000, 12_000, {"father": "0/1"}),
    ]
    summary = summarize_second_hit(
        svs,
        ["child"],
        unaffected_samples=["mother", "father"],
        snv_gt_by_sample={"child": "0/1", "mother": "0/0", "father": "0/1"},
        snv_dp_by_sample={"child": 30, "mother": 30, "father": 30},
        pedigree=_pedigree(TRIO),
    )
    assert summary["phase"] == "trans"
    assert summary["phase_evidence"] == "segregation"
    assert summary["deletion_unmasked"] is True
    # Only the child's own call is their second hit; the parents' calls get no phase.
    assert [
        (sv["sv_type"], sv["start"], sv["affected_zygosity"], sv["phase"]) for sv in summary["svs"]
    ] == [
        ("DEL", 10_000, "het", "trans"),
        ("DEL", 10_040, None, "unknown"),
        ("INS", 12_000, None, "unknown"),
    ]


@pytest.mark.parametrize(
    ("father_sv", "phase"),
    [
        # Another deletion of the father's, 25 kb away: not the child's, so not evidence.
        (_sv_at("DEL", 40_000, 40_300, {"father": "0/1"}), "trans"),
        # One 900 bp past the child's end may be the same deletion called apart: the
        # unaffected father then carries the SNV and the deletion, which is cis.
        (_sv_at("DEL", 15_900, 16_500, {"father": "0/1"}), "cis"),
    ],
    ids=["far", "within-1kb"],
)
def test_only_a_nearby_call_of_the_same_type_stands_in(father_sv: dict, phase: str) -> None:
    svs = [
        _sv_at("DEL", 10_000, 15_000, {"child": "0/1"}),
        _sv_at("DEL", 10_020, 15_010, {"mother": "0/1"}),
        father_sv,
    ]
    summary = summarize_second_hit(
        svs,
        ["child"],
        unaffected_samples=["mother", "father"],
        snv_gt_by_sample={"child": "0/1", "mother": "0/0", "father": "0/1"},
        pedigree=_pedigree(TRIO),
    )
    assert summary["phase"] == phase
    assert summary["deletion_unmasked"] is (phase == "trans")


@pytest.mark.parametrize(
    ("other", "same"),
    [
        (_sv_at("DEL", 9_000, 11_000, {}), True),  # overlapping
        (_sv_at("DEL", 16_000, 17_000, {}), True),  # 1,000 bp apart
        (_sv_at("DEL", 16_001, 17_000, {}), False),  # 1,001 bp apart
        (_sv_at("DEL:ME", 10_000, 15_000, {}), True),  # a subtype of the same type
        (_sv_at("DUP", 10_000, 15_000, {}), False),  # another type
        (_sv_at("DEL", 10_000, 15_000, {}, chrom="2"), False),  # another chromosome
        ({**_sv_at("DEL", 10_000, 15_000, {}), "end": None}, False),  # no position to compare
    ],
)
def test_what_may_be_the_same_sv_called_in_another_file(other: dict, same: bool) -> None:
    from backend.app.services.sv_gene_index_service import _same_sv

    assert _same_sv(_sv_at("DEL", 10_000, 15_000, {}), other) is same


@pytest.mark.parametrize(
    ("sv_type", "cn", "copy_loss"),
    [
        ("DEL", None, True),
        ("del", None, True),
        ("DEL:ME", None, True),
        ("CNV", None, True),  # no copy number stored: may be a loss, counts as one
        ("CNV", 1, True),
        ("CNV", 0, True),
        ("CNV", 2, False),
        ("CNV", 3, False),  # a gain
        ("DUP", None, False),
        ("INS", None, False),
        ("INV", None, False),
        ("BND", None, False),
    ],
)
def test_which_svs_count_as_a_deletion(sv_type: str, cn: int | None, copy_loss: bool) -> None:
    sv = _sv_at(
        sv_type, 5_000, 9_000, {"child": "1|0"}, ps={"child": 1000},
        cn={"child": cn} if cn is not None else None,
    )
    summary = summarize_second_hit([sv], ["child"], unaffected_samples=[], **PHASED_SNV)
    assert summary["phase"] == "trans"
    assert summary["has_deletion"] is copy_loss
    assert summary["deletion_unmasked"] is copy_loss
    assert summary["svs"][0]["copy_loss"] is copy_loss


@pytest.mark.parametrize(("cn", "copy_loss"), [(1, False), (0, True)])
def test_a_male_keeps_one_copy_of_chrx_outside_the_pars(cn: int, copy_loss: bool) -> None:
    sv = _sv_at("CNV", 50_000_000, 50_010_000, {"son": "1|0"}, ps={"son": 7}, cn={"son": cn}, chrom="X")
    summary = summarize_second_hit(
        [sv],
        ["son"],
        unaffected_samples=[],
        snv_gt_by_sample={"son": "0|1"},
        snv_ps_by_sample={"son": 7},
        pedigree=_pedigree({}, males={"son"}),
        snv_locus=("X", 50_005_000),
    )
    assert summary["deletion_unmasked"] is copy_loss
    assert summary["svs"][0]["copy_loss"] is copy_loss


def test_an_sv_no_affected_member_carries_is_listed_without_a_phase() -> None:
    # Only the father carries the deletion (the child has a reference call): it is not
    # the child's second hit, so the duplication's cis stands for the gene.
    deletion = _sv_at("DEL", 5_000, 9_000, {"child": "0/0", "father": "0/1"})
    summary = summarize_second_hit(
        [DUP_IN_CIS, deletion], ["child"], unaffected_samples=["father"], **PHASED_SNV
    )
    assert summary["phase"] == "cis"
    assert summary["deletion_unmasked"] is False
    assert [(sv["sv_type"], sv["affected_zygosity"], sv["phase"]) for sv in summary["svs"]] == [
        ("DUP", "het", "cis"),
        ("DEL", None, "unknown"),
    ]


def test_a_cis_read_beside_a_cis_by_segregation_is_not_called_read_backed() -> None:
    # The duplication is in cis by the reads, the insertion by segregation (the unaffected
    # father carries it and the SNV). The gene is cis, but not every verdict is read-backed.
    duplication = _sv_at("DUP", 2_000, 3_000, {"child": "0|1", "father": "0/0"}, ps={"child": 1000})
    insertion = _sv_at("INS", 6_000, 6_000, {"child": "0/1", "father": "0/1"})
    summary = summarize_second_hit(
        [duplication, insertion],
        ["child"],
        unaffected_samples=["father"],
        snv_gt_by_sample={"child": "0|1", "father": "0/1"},
        snv_ps_by_sample={"child": 1000},
    )
    assert summary["phase"] == "cis"
    assert summary["phase_evidence"] == "segregation"
    assert _verdicts(summary) == [("DUP", "cis", "read"), ("INS", "cis", "segregation")]


def test_a_deletion_one_affected_sibling_lacks_is_not_placed_by_segregation() -> None:
    # Joint calls: the second affected sibling has a reference call at the deletion, so it
    # is not a second hit the two share, whatever the parents carry.
    deletion = _sv_at(
        "DEL", 5_000, 9_000, {"sib1": "0/1", "sib2": "0/0", "mother": "0/1", "father": "0/0"}
    )
    summary = summarize_second_hit(
        [deletion],
        ["sib1", "sib2"],
        unaffected_samples=["mother", "father"],
        snv_gt_by_sample={"sib1": "0/1", "sib2": "0/1", "mother": "0/0", "father": "0/1"},
        pedigree=_pedigree({"sib1": {"mother", "father"}, "sib2": {"mother", "father"}}),
    )
    assert summary["phase"] == "unknown"
    assert summary["deletion_unmasked"] is False
    assert _verdicts(summary) == [("DEL", "unknown", None)]


def test_a_deletion_in_cis_in_one_affected_sibling_does_not_unmask() -> None:
    # One SV's own cis in any affected sample still rules that SV out as their shared
    # second hit; the deletion-in-trans rule is about the other SVs of the gene.
    sibs = ["sib1", "sib2"]
    deletion = _sv_at(
        "DEL", 5_000, 9_000, {"sib1": "1|0", "sib2": "0|1"}, ps={"sib1": 1000, "sib2": 1000}
    )
    duplication = _sv_at(
        "DUP", 2_000, 3_000, {"sib1": "1|0", "sib2": "1|0"}, ps={"sib1": 1000, "sib2": 1000}
    )
    summary = summarize_second_hit(
        [duplication, deletion],
        sibs,
        unaffected_samples=[],
        snv_gt_by_sample={"sib1": "0|1", "sib2": "0|1"},
        snv_ps_by_sample={"sib1": 1000, "sib2": 1000},
    )
    assert summary["phase"] == "trans"  # the duplication, in trans in both
    assert summary["deletion_unmasked"] is False
    assert _verdicts(summary) == [("DUP", "trans", "read"), ("DEL", "cis", "read")]


def _scan(monkeypatch, rows: list[tuple]) -> tuple[dict, int]:
    async def _fake_execute(query, params):
        assert params["family_guid"] == "u1"
        assert "e.calls.cn" in query  # the copy number a CNV's direction is read from
        return rows

    monkeypatch.setattr(cfv, "execute_clickhouse", _fake_execute)
    context = types.SimpleNamespace(assembly_name="GRCh38", family_uuid="u1")
    return asyncio.run(cfv._scan_family_sv_gene_map(context))


def test_scan_groups_svs_by_gene(monkeypatch) -> None:
    rows = [
        # key, variantId, svType, chrom, start, end, gene_symbols, sampleIds, gts, ps, cn
        (
            11, "sv1", "DEL", "1", 100, 200, ["BRCA2", "fgr"],
            ["S1", "S2"], ["0|1", "0/0"], [1000, None], [1, None],
        ),
        (12, "sv2", "DUP", "1", 300, 400, ["BRCA2"], ["S1"], ["1/1"], [None], [None]),
    ]
    gene_map, sv_total = _scan(monkeypatch, rows)

    assert sv_total == 2  # two distinct SVs
    assert set(gene_map) == {"BRCA2", "FGR"}  # gene symbols upper-cased
    assert len(gene_map["BRCA2"]) == 2
    assert gene_map["BRCA2"][0]["gt"] == {"S1": "0|1", "S2": "0/0"}
    assert gene_map["BRCA2"][0]["ps"] == {"S1": 1000}  # phase set captured, nulls dropped
    assert gene_map["BRCA2"][0]["cn"] == {"S1": 1}  # copy number captured, nulls dropped
    assert gene_map["FGR"][0]["sv_id"] == "sv1"


def test_scan_lists_an_sv_stored_once_per_project_once_per_gene(monkeypatch) -> None:
    # A family in two projects has each SV stored twice (one entries row per project).
    # The badge lists every SV with its own phase, so it must list this one once, and
    # count it once.
    row = (11, "sv1", "DEL", "1", 100, 200, ["BRCA2"], ["S1"], ["0/1"], [None], [None])
    gene_map, sv_total = _scan(monkeypatch, [row, row])

    assert sv_total == 1
    assert [sv["sv_id"] for sv in gene_map["BRCA2"]] == ["sv1"]


class _Result:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def scalar(self):
        return self._rows[0][0] if self._rows else None

    def first(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return types.SimpleNamespace(all=lambda: [row[0] for row in self._rows])


class _IndexSession:
    """Just enough of an AsyncSession to run sv_gene_index_service's SQL in memory."""

    def __init__(self, built_from: dict[str, str | None] | None = None) -> None:
        # family_id -> the SV data version its index was built from (None: before the
        # column existed). A family absent here has no index.
        self.built_from: dict[str, str | None] = dict(built_from or {})
        self.genes: dict[str, set[str]] = {fid: {"STALE"} for fid in self.built_from}
        self.commits = 0

    async def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        rows = params if isinstance(params, list) else [params or {}]
        fid = rows[0].get("fid") if rows else None
        if "pg_advisory_xact_lock" in sql:
            return _Result([])
        if sql.startswith("SELECT") and "FROM family_sv_gene_index_status" in sql:
            if fid not in self.built_from:
                return _Result([])
            return _Result([(self.built_from[fid],)] if "sv_data_version" in sql else [(1,)])
        if sql.startswith("DELETE FROM family_sv_gene_index_status"):
            self.built_from.pop(fid, None)
        elif sql.startswith("DELETE FROM family_sv_gene_index"):
            self.genes.pop(fid, None)
        elif sql.startswith("INSERT INTO family_sv_gene_index_status"):
            self.built_from[fid] = rows[0].get("sv_data_version")
        elif sql.startswith("INSERT INTO family_sv_gene_index"):
            self.genes.setdefault(fid, set()).update(row["gene_symbol"] for row in rows)
        elif sql.startswith("SELECT gene_symbol FROM family_sv_gene_index"):
            return _Result([(gene,) for gene in sorted(self.genes.get(fid, set()))])
        return _Result([])

    async def commit(self) -> None:
        self.commits += 1


def _ensure_index(monkeypatch, session: _IndexSession, *, current_version: str) -> dict:
    """Run the lazy index build against ``session`` with the SV data at ``current_version``."""
    scans = {"count": 0}

    async def fake_scan(context):
        scans["count"] += 1
        return {"BRCA2": [{"sv_id": "sv1", "sv_type": "DEL", "gt": {"S1": "0/1"}}]}, 1

    async def fake_version(assembly_name, family_uuid):
        assert (assembly_name, family_uuid) == ("GRCh38", "u1")
        return current_version

    async def fake_ensure_tables(assembly_name):
        return None

    monkeypatch.setattr(cfv, "_scan_family_sv_gene_map", fake_scan)
    monkeypatch.setattr(cfv, "ensure_clickhouse_variant_tables", fake_ensure_tables)
    monkeypatch.setattr(
        cfv, "get_family_structural_variant_data_version", fake_version, raising=False
    )
    context = types.SimpleNamespace(assembly_name="GRCh38", family_uuid="u1", family_id="F1")
    asyncio.run(cfv._ensure_family_sv_gene_index(session, context))
    return scans


def test_index_is_rebuilt_when_the_family_svs_changed_since_it_was_built(monkeypatch) -> None:
    # A per-sample SV upload or an admin SV delete moves the storage-level SV data
    # version without clearing the index; the next read must not serve the old one.
    session = _IndexSession(built_from={"u1": "1:111"})
    scans = _ensure_index(monkeypatch, session, current_version="2:333")
    assert scans["count"] == 1
    assert session.built_from["u1"] == "2:333"
    assert session.genes["u1"] == {"BRCA2"}  # the stale gene rows are gone


def test_index_built_from_the_current_svs_is_reused(monkeypatch) -> None:
    session = _IndexSession(built_from={"u1": "2:333"})
    scans = _ensure_index(monkeypatch, session, current_version="2:333")
    assert scans["count"] == 0
    assert session.genes["u1"] == {"STALE"}  # untouched


def test_index_built_before_versions_were_recorded_is_rebuilt(monkeypatch) -> None:
    session = _IndexSession(built_from={"u1": None})
    scans = _ensure_index(monkeypatch, session, current_version="0:0")
    assert scans["count"] == 1
    assert session.built_from["u1"] == "0:0"


def test_missing_index_is_built_and_stamped_with_the_version(monkeypatch) -> None:
    session = _IndexSession()
    scans = _ensure_index(monkeypatch, session, current_version="3:9")
    assert scans["count"] == 1
    assert session.built_from["u1"] == "3:9"
    assert session.genes["u1"] == {"BRCA2"}
