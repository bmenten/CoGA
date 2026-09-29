from __future__ import annotations

import random

from backend.app.services.sample_integrity_qc import (
    PedigreeSpec,
    classify_relatedness,
    evaluate_fetal_sex,
    evaluate_nipt_category_qc,
    evaluate_paternity,
    evaluate_sample_integrity,
    infer_sex,
    king_relatedness,
    mendelian_stats,
    profile_for,
    resolve_application,
)

_WGS = profile_for("wgs")

N_SITES = 4_000


def _founder(rng: random.Random, n: int = N_SITES, p: float = 0.5):
    return [(int(rng.random() < p), int(rng.random() < p)) for _ in range(n)]


def _child(rng: random.Random, father, mother):
    # Inherit one allele from each parent at every site.
    return [(rng.choice(f), rng.choice(m)) for f, m in zip(father, mother)]


def _trio(seed: int):
    rng = random.Random(seed)
    father = _founder(rng)
    mother = _founder(rng)
    child = _child(rng, father, mother)
    return father, mother, child


# --- KING relatedness --------------------------------------------------------

def test_king_duplicate_is_half() -> None:
    father, _mother, _child = _trio(1)
    kinship, ibs0, n = king_relatedness(father, father)
    assert n == N_SITES
    assert kinship == 0.5
    assert classify_relatedness(kinship, ibs0, n) == "duplicate"


def test_king_parent_child_is_first_degree_with_zero_ibs0() -> None:
    father, _mother, child = _trio(2)
    kinship, ibs0, n = king_relatedness(father, child)
    assert 0.18 < kinship < 0.32  # ~0.25
    assert ibs0 == 0.0  # a true parent and child are never opposite homozygotes
    assert classify_relatedness(kinship, ibs0, n) == "parent-child"


def test_king_unrelated_parents_are_unrelated() -> None:
    father, mother, _child = _trio(3)
    kinship, ibs0, n = king_relatedness(father, mother)
    assert kinship < 0.0442
    assert classify_relatedness(kinship, ibs0, n) == "unrelated"


def test_king_full_siblings_are_first_degree_with_nonzero_ibs0() -> None:
    rng = random.Random(4)
    father = _founder(rng)
    mother = _founder(rng)
    sib1 = _child(rng, father, mother)
    sib2 = _child(rng, father, mother)
    kinship, ibs0, n = king_relatedness(sib1, sib2)
    assert 0.18 < kinship < 0.32
    assert ibs0 > 0.0  # siblings can be opposite homozygotes
    assert classify_relatedness(kinship, ibs0, n) == "sibling"


def test_classify_relatedness_needs_minimum_sites() -> None:
    assert classify_relatedness(0.25, 0.0, 10) == "indeterminate"


# --- Sex inference -----------------------------------------------------------

def test_infer_sex_male_from_hemizygous_x() -> None:
    # Males call essentially no heterozygotes on the X.
    x = [(1, 1) if i % 2 else (0, 0) for i in range(N_SITES)]
    inferred, het_rate, n = infer_sex(x)
    assert inferred == "male"
    assert het_rate == 0.0
    assert n == N_SITES


def test_infer_sex_female_from_heterozygous_x() -> None:
    rng = random.Random(5)
    x = _founder(rng)  # ~50% het
    inferred, het_rate, _n = infer_sex(x)
    assert inferred == "female"
    assert het_rate is not None and het_rate > 0.15


def test_infer_sex_indeterminate_without_enough_sites() -> None:
    inferred, _rate, n = infer_sex([(0, 1), (1, 1)])
    assert inferred == "indeterminate"
    assert n == 2


# --- Mendelian errors --------------------------------------------------------

def test_mendelian_clean_trio_has_no_errors() -> None:
    father, mother, child = _trio(6)
    informative, errors = mendelian_stats(child, father, mother)
    assert informative == N_SITES
    assert errors == 0


def test_mendelian_swapped_child_has_many_errors() -> None:
    father, mother, _child = _trio(7)
    stranger = _founder(random.Random(99))  # unrelated "child"
    informative, errors = mendelian_stats(stranger, father, mother)
    assert informative == N_SITES
    # An unrelated child violates transmission at a large fraction of sites.
    assert errors / informative > 0.1


def test_mendelian_single_parent_flags_no_shared_allele() -> None:
    # child shares an allele with the parent at every site -> no error.
    parent = [(0, 1)] * 100
    child_ok = [(1, 1)] * 100
    informative, errors = mendelian_stats(child_ok, parent, None)
    assert (informative, errors) == (100, 0)
    # child homozygous for an allele the parent lacks -> error every site.
    child_bad = [(0, 0)] * 100
    parent_alt = [(1, 1)] * 100
    informative, errors = mendelian_stats(child_bad, parent_alt, None)
    assert (informative, errors) == (100, 100)


# --- Orchestration ----------------------------------------------------------

def _trio_spec(sexes: dict[str, str]) -> PedigreeSpec:
    return PedigreeSpec(
        recorded_sex=sexes,
        parents_of={"CHILD": {"father": "FATHER", "mother": "MOTHER"}},
    )


def test_evaluate_clean_trio_passes() -> None:
    father, mother, child = _trio(10)
    autosomal = {"FATHER": father, "MOTHER": mother, "CHILD": child}
    x = {
        "FATHER": [(0, 0)] * N_SITES,
        "MOTHER": _founder(random.Random(11)),
        "CHILD": [(0, 0)] * N_SITES,
    }
    spec = _trio_spec({"FATHER": "male", "MOTHER": "female", "CHILD": "male"})
    report = evaluate_sample_integrity(autosomal, x, spec, profile=_WGS)

    assert report.overall_status == "pass"
    pc = [c for c in report.relatedness_checks if c.expected_relationship == "parent-child"]
    assert len(pc) == 2 and all(c.status == "pass" for c in pc)
    assert all(c.status == "pass" for c in report.mendelian_checks)
    assert all(c.status == "pass" for c in report.sex_checks)


def test_evaluate_swapped_child_fails_relatedness_and_mendel() -> None:
    father, mother, _child = _trio(12)
    stranger = _founder(random.Random(123))
    autosomal = {"FATHER": father, "MOTHER": mother, "CHILD": stranger}
    spec = _trio_spec({"FATHER": "male", "MOTHER": "female", "CHILD": "male"})
    report = evaluate_sample_integrity(autosomal, {}, spec, profile=_WGS)

    assert report.overall_status == "fail"
    pc = [c for c in report.relatedness_checks if c.expected_relationship == "parent-child"]
    assert any(c.status == "fail" for c in pc)
    assert any(c.status == "fail" for c in report.mendelian_checks)


def test_evaluate_sex_mismatch_fails() -> None:
    father, mother, child = _trio(13)
    autosomal = {"FATHER": father, "MOTHER": mother, "CHILD": child}
    # CHILD genotypes on X are heterozygous (female-like) but recorded male.
    x = {"CHILD": _founder(random.Random(14))}
    spec = _trio_spec({"FATHER": "male", "MOTHER": "female", "CHILD": "male"})
    report = evaluate_sample_integrity(autosomal, x, spec, profile=_WGS)

    child_sex = next(c for c in report.sex_checks if c.sample_id == "CHILD")
    assert child_sex.inferred_sex == "female"
    assert child_sex.status == "fail"
    assert report.overall_status == "fail"


# --- Application resolution + per-profile gating -----------------------------

def test_resolve_application_per_input_shape() -> None:
    assert resolve_application(analysis_type="monogenic_nipt", roles={}, parents_of={}, sample_count=3) == "nipt"
    assert resolve_application(
        analysis_type="", roles={"E1": "embryo", "F": "father", "M": "mother"},
        parents_of={"E1": {"father": "F", "mother": "M"}}, sample_count=3,
    ) == "pgt"
    assert resolve_application(analysis_type="", roles={"A": "father", "B": "mother"}, parents_of={}, sample_count=2) == "couple"
    assert resolve_application(analysis_type="", roles={"A": "proband"}, parents_of={}, sample_count=1) == "single"
    assert resolve_application(
        analysis_type="", roles={"P": "proband", "F": "father", "M": "mother"},
        parents_of={"P": {"father": "F", "mother": "M"}}, sample_count=3,
    ) == "wgs"


def test_single_profile_runs_sex_only() -> None:
    autosomal = {"S": _founder(random.Random(20))}
    x = {"S": [(0, 0)] * N_SITES}
    spec = PedigreeSpec(recorded_sex={"S": "male"}, parents_of={})
    report = evaluate_sample_integrity(autosomal, x, spec, profile=profile_for("single"))
    assert report.application == "single"
    assert report.sex_checks and report.sex_checks[0].status == "pass"
    assert report.relatedness_checks == [] and report.mendelian_checks == []


def test_couple_profile_keeps_unrelated_confirmation_and_flags_relatedness() -> None:
    rng = random.Random(21)
    a = _founder(rng)
    b = _founder(rng)  # independent -> unrelated
    spec = PedigreeSpec(recorded_sex={"A": "male", "B": "female"}, parents_of={})
    ok = evaluate_sample_integrity({"A": a, "B": b}, {}, spec, profile=profile_for("couple"))
    # The expected-unrelated confirmation is kept (not suppressed) for couples.
    assert len(ok.relatedness_checks) == 1
    assert ok.relatedness_checks[0].inferred_relationship == "unrelated"
    assert ok.relatedness_checks[0].status == "pass"
    # A secretly-identical couple (sample mixup / consanguinity) is flagged.
    dup = evaluate_sample_integrity({"A": a, "B": a}, {}, spec, profile=profile_for("couple"))
    assert dup.relatedness_checks[0].status == "fail"
    assert dup.overall_status == "fail"


def test_nipt_profile_runs_no_genotype_checks() -> None:
    # NIPT skips genotype sex/relatedness/Mendelian even if genotypes are passed.
    father, mother, child = _trio(22)
    autosomal = {"FATHER": father, "MOTHER": mother, "CHILD": child}
    spec = _trio_spec({"FATHER": "male", "MOTHER": "female", "CHILD": "male"})
    report = evaluate_sample_integrity(autosomal, {}, spec, profile=profile_for("nipt"))
    assert report.application == "nipt"
    assert report.sex_checks == []
    assert report.relatedness_checks == []
    assert report.mendelian_checks == []


def test_nipt_profile_summary_names_the_checks_it_runs() -> None:
    # The NIPT page runs fetal sex (from paternal-X transmission, no chrY) and chrX-zygosity
    # sex for the father and the cfDNA sample; its summary said the first was "not yet
    # wired" and that chrX-heterozygosity sex did not apply.
    summary = profile_for("nipt").summary.lower()
    assert "not yet wired" not in summary
    assert "heterozygosity sex do not apply" not in summary
    assert "fetal sex" in summary and "paternal-x" in summary
    assert "chrx" in summary and "father" in summary and "cfdna sample" in summary
    # What genuinely does not run on a maternal/fetal mixture is still said.
    assert "relatedness" in summary and "mendelian" in summary


def test_evaluate_paternity_supported_vs_mixup() -> None:
    # Plenty of cat-7 (paternal transmitted), little cat-8 absence -> paternity ok.
    ok = evaluate_paternity("FATHER", {7: 40, 8: 2})
    assert ok.status == "pass" and ok.cat7_transmitted == 40
    # Paternal alleles essentially absent -> non-paternity / mixup.
    bad = evaluate_paternity("FATHER", {7: 0, 8: 30})
    assert bad.status == "fail"
    # Too few paternal-informative sites -> warn, not a confident verdict.
    thin = evaluate_paternity("FATHER", {7: 2, 8: 1})
    assert thin.status == "warn"


def test_evaluate_fetal_sex_wraps_the_call() -> None:
    female = evaluate_fetal_sex("female", 12, 0, 12)
    assert female.status == "pass" and "female" in female.message
    male = evaluate_fetal_sex("male", 0, 15, 15)
    assert male.status == "pass" and "male" in male.message
    unknown = evaluate_fetal_sex("indeterminate", 1, 2, 3)
    assert unknown.status == "warn"


def test_evaluate_nipt_category_qc() -> None:
    # ~50% maternal transmission (30/60), no de-novo excess -> pass.
    ok = evaluate_nipt_category_qc({1: 0, 2: 30, 3: 16, 4: 14, 7: 40, 8: 2})
    assert ok.status == "pass"
    assert ok.maternal_inherited == 30 and ok.maternal_informative == 60
    # De-novo (cat 1) excess -> fail (artifacts / contamination).
    denovo = evaluate_nipt_category_qc({1: 50, 2: 30, 3: 15, 4: 15})
    assert denovo.status == "fail" and "de-novo" in denovo.message
    # Maternal transmission far from 50% -> fail (wrong mother / sample issue).
    skewed = evaluate_nipt_category_qc({2: 90, 3: 5, 4: 5})
    assert skewed.status == "fail" and "maternal transmission" in skewed.message


def test_wgs_keeps_coparent_check_and_flags_consanguinity() -> None:
    father, mother, child = _trio(30)
    spec = _trio_spec({"FATHER": "male", "MOTHER": "female", "CHILD": "male"})

    # Unrelated parents: the co-parent check is KEPT (visible) and confirms no consanguinity.
    report = evaluate_sample_integrity(
        {"FATHER": father, "MOTHER": mother, "CHILD": child}, {}, spec, profile=_WGS
    )
    coparent = [c for c in report.relatedness_checks if {c.sample_a, c.sample_b} == {"FATHER", "MOTHER"}]
    assert len(coparent) == 1
    assert coparent[0].status == "pass" and "consanguinity" in coparent[0].message.lower()

    # Parents share genotypes (look related) -> flagged as consanguinity.
    report2 = evaluate_sample_integrity(
        {"FATHER": father, "MOTHER": father, "CHILD": child}, {}, spec, profile=_WGS
    )
    coparent2 = [c for c in report2.relatedness_checks if {c.sample_a, c.sample_b} == {"FATHER", "MOTHER"}]
    assert len(coparent2) == 1
    assert coparent2[0].status == "fail" and "consanguinity" in coparent2[0].message.lower()
