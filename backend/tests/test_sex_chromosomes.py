"""Where a male is hemizygous: chrX and chrY outside the pseudo-autosomal regions.

GRCh38 and GRCh37 by the GRC's bounds; T2T-CHM13v2.0 by the T2T consortium's, as NCBI's
assembly states them, each edge on both sides. Another assembly has none, and a male
is read as diploid there.
"""

from __future__ import annotations

import pytest

from backend.app.services.sex_chromosomes import (
    X_STRETCH_LAST_POSITION,
    grc_build,
    hemizygous_chromosome,
    hemizygous_interval,
    pseudoautosomal_regions,
    sex_chromosome,
    x_stretches,
)

# Another CHM13 release, whose PARs CoGA does not list: hs1 is v2.0.
OTHER_CHM13 = "T2T-CHM13v1.0"


@pytest.mark.parametrize(
    ("chromosome", "position", "expected"),
    [
        # GRCh38 PAR1 and PAR2 on X, 1-based and inclusive (GRC).
        ("chrX", 10_000, "X"),
        ("chrX", 10_001, None),
        ("chrX", 2_781_479, None),
        ("chrX", 2_781_480, "X"),
        ("chrX", 31_500_000, "X"),
        ("chrX", 155_701_382, "X"),
        ("chrX", 155_701_383, None),
        ("chrX", 156_030_895, None),
        # Y has its own PAR2 coordinates.
        ("chrY", 2_787_000, "Y"),
        ("chrY", 500_000, None),
        ("chrY", 56_887_903, None),
        ("chrY", 57_217_415, None),
        ("chrY", 57_217_416, "Y"),
        # Autosomes and the mitochondrion are never hemizygous.
        ("chr1", 31_500_000, None),
        ("chrM", 3_243, None),
    ],
)
def test_grch38_hemizygous_positions(chromosome: str, position: int, expected: str | None) -> None:
    assert hemizygous_chromosome("GRCh38", chromosome, position) == expected


def test_each_assembly_uses_its_own_par_bounds() -> None:
    # 2.70 Mb on X is past GRCh37's PAR1 (60,001-2,699,520) but inside GRCh38's.
    assert hemizygous_chromosome("GRCh37", "X", 2_700_000) == "X"
    assert hemizygous_chromosome("GRCh38", "X", 2_700_000) is None
    assert hemizygous_chromosome("GRCh37", "Y", 59_034_050) is None


def test_sex_chromosomes_are_read_under_any_of_their_names() -> None:
    assert [sex_chromosome(name) for name in ("X", "chrX", "23", "chr23", "x")] == ["X"] * 5
    assert [sex_chromosome(name) for name in ("Y", "chrY", "24")] == ["Y"] * 3
    assert sex_chromosome("22") is None
    assert sex_chromosome(None) is None


def test_assembly_aliases_resolve_and_unknown_assemblies_have_no_bounds() -> None:
    assert pseudoautosomal_regions("hg38") == pseudoautosomal_regions("GRCh38")
    assert pseudoautosomal_regions("GRCh38.p14") == pseudoautosomal_regions("GRCh38")
    assert pseudoautosomal_regions("hg19") == pseudoautosomal_regions("GRCh37")
    # No listed bounds: no position is hemizygous, so the diploid rules apply there.
    for name in (OTHER_CHM13, "T2T-YAO", "GRCm39", "", None):
        assert pseudoautosomal_regions(name) is None, name
        assert hemizygous_chromosome(name, "chrX", 31_500_000) is None, name
        assert hemizygous_interval(name, "chrX", 31_000_000, 32_000_000) is None, name
        assert x_stretches(name, "chrX") is None, name


def test_the_build_of_an_assembly_is_read_from_any_of_its_names() -> None:
    # The rule the PARs are looked up by; the startup also reads it to load the shipped GRCh38
    # segmental-duplication file into a GRCh38 assembly only.
    assert {grc_build(name) for name in ("GRCh38", "grch38", "hg38", "GRCh38.p14", " GRCh38 ")} == {"GRCh38"}
    assert {grc_build(name) for name in ("GRCh37", "hg19", "GRCh37.p13")} == {"GRCh37"}
    assert [grc_build(name) for name in ("T2T CHM13v2.0", "T2T-CHM13v2.0", "hs1", "GRCm39", "", None)] == [None] * 6


@pytest.mark.parametrize(
    ("chromosome", "start", "end", "expected"),
    [
        # Wholly inside PAR1 or PAR2: a male has two copies.
        ("chrX", 500_000, 650_000, None),
        ("chrX", 10_001, 2_781_479, None),
        ("chrX", 155_800_000, 155_900_000, None),
        # Wholly outside both: one copy.
        ("chrX", 2_781_480, 31_500_000, "X"),
        ("chrX", 1, 10_000, "X"),
        # A block that reaches into a PAR is read as two copies throughout.
        ("chrX", 2_000_000, 3_000_000, None),
        ("chrX", 150_000_000, 155_701_383, None),
        ("chrY", 2_781_480, 56_887_902, "Y"),
        ("chrY", 56_000_000, 57_000_000, None),
        ("chr7", 1, 1_000_000, None),
    ],
)
def test_grch38_hemizygous_intervals(chromosome: str, start: int, end: int, expected: str | None) -> None:
    assert hemizygous_interval("GRCh38", chromosome, start, end) == expected


def test_an_interval_is_one_copy_only_where_the_assembly_s_pars_are_known() -> None:
    # 2.70-2.75 Mb on X is past GRCh37's PAR1 but inside GRCh38's.
    assert hemizygous_interval("GRCh37", "X", 2_700_000, 2_750_000) == "X"
    assert hemizygous_interval("GRCh38", "X", 2_700_000, 2_750_000) is None
    # No listed bounds: never one copy, so a male is read as having two.
    assert hemizygous_interval(OTHER_CHM13, "chrX", 31_000_000, 32_000_000) is None
    assert hemizygous_interval(None, "chrX", 31_000_000, 32_000_000) is None


# ---- T2T-CHM13v2.0 -------------------------------------------------------------------------------

# The T2T consortium's chm13v2.0_PAR.bed (0-based, half-open), and NCBI's region report of
# GCF_009914755.1 (1-based, inclusive), as both read on 2026-11-04. The code holds NCBI's.
T2T_PAR_BED = (
    ("chrX", 0, 2_394_410),
    ("chrX", 153_925_834, 154_259_566),
    ("chrY", 0, 2_458_320),
    ("chrY", 62_122_809, 62_460_029),
)
NCBI_T2T_PARS = (
    ("X", 1, 2_394_410),
    ("X", 153_925_835, 154_259_566),
    ("Y", 1, 2_458_320),
    ("Y", 62_122_810, 62_460_029),
)
# NCBI's assembly report: chrX and chrY's lengths. PAR2 ends at each chromosome's last base.
T2T_LENGTHS = {"X": 154_259_566, "Y": 62_460_029}


def test_t2t_s_pars_are_the_consortium_s_bed_read_one_based_and_ncbi_s_regions() -> None:
    from_bed = tuple((chrom.removeprefix("chr"), start + 1, end) for chrom, start, end in T2T_PAR_BED)
    assert from_bed == NCBI_T2T_PARS
    regions = pseudoautosomal_regions("T2T-CHM13v2.0")
    held = tuple((chrom, start, end) for chrom in ("X", "Y") for start, end in regions[chrom])
    assert held == NCBI_T2T_PARS
    for chrom, length in T2T_LENGTHS.items():
        assert regions[chrom][1][1] == length


@pytest.mark.parametrize("name", ["T2T-CHM13v2.0", "T2T CHM13v2.0", "T2T_CHM13v2.0", "T2T-CHM13", "CHM13v2.0", "hs1", "HS1"])
def test_t2t_s_pars_are_read_under_each_of_its_names(name: str) -> None:
    assert pseudoautosomal_regions(name) == pseudoautosomal_regions("T2T-CHM13v2.0")
    assert grc_build(name) is None  # it is no GRC build: its PARs are its own


@pytest.mark.parametrize(
    ("chromosome", "position", "expected"),
    [
        # PAR1 from the first base: a T2T assembly has no telomeric gap before it.
        ("chrX", 1, None),
        ("chrX", 2_394_410, None),
        ("chrX", 2_394_411, "X"),
        # GRCh38's PAR1 reaches 2,781,479; on T2T this lies on the X between the PARs.
        ("chrX", 2_700_000, "X"),
        ("chrX", 31_500_000, "X"),
        ("chrX", 153_925_834, "X"),
        ("chrX", 153_925_835, None),
        ("chrX", 154_259_566, None),
        ("chrY", 1, None),
        ("chrY", 2_458_320, None),
        ("chrY", 2_458_321, "Y"),
        ("chrY", 62_122_809, "Y"),
        ("chrY", 62_122_810, None),
        ("chrY", 62_460_029, None),
        # Under the names some pipelines give the sex chromosomes.
        ("23", 31_500_000, "X"),
        ("24", 2_458_321, "Y"),
        ("chr1", 31_500_000, None),
        ("chrM", 3_243, None),
    ],
)
def test_t2t_hemizygous_positions(chromosome: str, position: int, expected: str | None) -> None:
    assert hemizygous_chromosome("T2T-CHM13v2.0", chromosome, position) == expected


@pytest.mark.parametrize(
    ("chromosome", "start", "end", "expected"),
    [
        ("chrX", 1, 2_394_410, None),
        ("chrX", 2_394_411, 153_925_834, "X"),
        # Reaching one base into a PAR: two copies throughout.
        ("chrX", 2_394_410, 3_000_000, None),
        ("chrX", 150_000_000, 153_925_835, None),
        ("chrY", 2_458_321, 62_122_809, "Y"),
        ("chrY", 2_458_320, 3_000_000, None),
        ("chrY", 62_000_000, 62_122_810, None),
    ],
)
def test_t2t_hemizygous_intervals(chromosome: str, start: int, end: int, expected: str | None) -> None:
    assert hemizygous_interval("T2T-CHM13v2.0", chromosome, start, end) == expected


def test_t2t_s_x_is_cut_at_its_par_edges_with_no_stretch_before_par1() -> None:
    assert x_stretches("T2T-CHM13v2.0", "chrX") == (
        (1, 2_394_410),
        (2_394_411, 153_925_834),
        (153_925_835, 154_259_566),
        (154_259_567, X_STRETCH_LAST_POSITION),
    )
    # GRCh38's PAR1 starts after a 10 kb gap, which is a stretch of its own.
    assert x_stretches("GRCh38", "chrX")[0] == (1, 10_000)
    assert x_stretches("T2T-CHM13v2.0", "chrY") is None
