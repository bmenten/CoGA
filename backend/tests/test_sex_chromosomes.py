"""Where a male is hemizygous: chrX and chrY outside the pseudo-autosomal regions (#545)."""

from __future__ import annotations

import pytest

from backend.app.services.sex_chromosomes import (
    hemizygous_chromosome,
    pseudoautosomal_regions,
    sex_chromosome,
)


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
    assert pseudoautosomal_regions("T2T-CHM13v2.0") is None
    assert hemizygous_chromosome("T2T-CHM13v2.0", "chrX", 31_500_000) is None
    assert hemizygous_chromosome(None, "chrX", 31_500_000) is None
