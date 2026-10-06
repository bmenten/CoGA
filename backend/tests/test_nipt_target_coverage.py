"""A capture panel's per-target coverage table (monogenic NIPT): parsing, the depth at a
position, the target QC and the sex-chromosome profile. Synthetic values only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.services.nipt_target_coverage import (
    TargetCoverageFormatError,
    TargetCoverageRow,
    TargetDepthLookup,
    parse_number,
    parse_target_coverage_table,
    sex_chromosome_coverage,
    summarize_target_coverage,
    target_row_from_track,
    target_row_metadata,
    target_table_missing_columns,
)

_HEADER = (
    "#build\tchromosome\tstart\tend\tattribute\tlength\tmin\tmax\tmean\tmedian\tstdev\t"
    "zero_coverage_bases\tproportion_covered\tlength_above_10X\t%_above_10X\n"
)


def _line(chrom: str, start: int, end: int, attribute: str, mean: str, median: str, covered: str = "100") -> str:
    return (
        f"hg38\t{chrom}\t{start}\t{end}\t\"{attribute}\"\t{end - start}\t10\t2000\t{mean}\t{median}\t"
        f"1,5\t0\t{covered}\t{end - start}\t100\n"
    )


def test_numbers_read_a_decimal_comma_and_thousands_separators() -> None:
    assert parse_number("1183,91") == 1183.91
    assert parse_number("1.183,91") == 1183.91
    assert parse_number("1194") == 1194.0
    assert parse_number("NA") is None and parse_number("") is None and parse_number(None) is None


def test_the_table_parses_its_targets() -> None:
    text = (
        _HEADER
        + _line("chr7", 1000, 1200, "GENEA;NM_1.1;ENST1;ENSE1;1", "1183,91", "1194")
        + _line("chrX", 5000, 5100, "GENEB;/,NM_2.1;ENST2,ENST3;ENSE2;2,2", "800,5", "810")
        + "hg38\tchr7\tnot-a-number\t1\t\"GENEC\"\n"
    )
    rows = parse_target_coverage_table(text.splitlines())
    assert len(rows) == 2
    first = rows[0]
    assert (first.chrom, first.start, first.end, first.gene) == ("7", 1000, 1200, "GENEA")
    assert first.attribute == "GENEA;NM_1.1;ENST1;ENSE1;1"
    assert (first.mean, first.median, first.proportion_covered) == (1183.91, 1194.0, 100.0)
    assert rows[1].gene == "GENEB" and rows[1].chrom == "X"


def test_a_table_without_its_columns_is_refused(tmp_path: Path) -> None:
    with pytest.raises(TargetCoverageFormatError):
        parse_target_coverage_table(["chromosome\tstart\tend\n"])
    path = tmp_path / "coverage_S1.txt"
    path.write_text("#build\tchromosome\tstart\tend\tattribute\n")
    assert target_table_missing_columns(path) == ["mean"]
    path.write_text(_HEADER)
    assert target_table_missing_columns(path) == []


def test_a_stored_row_reads_back_as_the_table_row() -> None:
    [row] = parse_target_coverage_table((_HEADER + _line("chr7", 1000, 1200, "GENEA;NM_1.1", "900,5", "880", "97,5")).splitlines())
    stored = {
        "chr": row.chrom,
        "start": row.start,
        "end": row.end,
        "record_id": row.gene,
        "value": row.mean,
        "metadata_json": target_row_metadata(row),
    }
    assert json.loads(stored["metadata_json"])["proportion_covered"] == 97.5
    assert target_row_from_track(stored) == row


def _target(chrom: str, start: int, end: int, mean: float | None, *, gene: str = "GENEA", median: float | None = None, covered: float = 100.0) -> TargetCoverageRow:
    return TargetCoverageRow(
        chrom=chrom,
        start=start,
        end=end,
        gene=gene,
        attribute=gene,
        mean=mean,
        median=median,
        proportion_covered=covered,
    )


def test_the_depth_at_a_position_uses_bed_coordinates() -> None:
    lookup = TargetDepthLookup([_target("7", 1000, 1200, 900.0, median=850.0), _target("7", 5000, 5010, 50.0)])
    # BED [1000, 1200): bases 1001..1200 in VCF (1-based) positions.
    assert lookup.depth_at("chr7", 1000) is None
    assert lookup.depth_at("7", 1001) == 850.0  # the lower of mean and median
    assert lookup.depth_at("7", 1200) == 850.0
    assert lookup.depth_at("7", 1201) is None
    assert lookup.depth_at("8", 1100) is None


def test_overlapping_targets_resolve_to_the_deepest() -> None:
    lookup = TargetDepthLookup(
        [_target("7", 1000, 5000, 400.0), _target("7", 1100, 1150, 1200.0, gene="GENEB")]
    )
    target = lookup.target_at("7", 1120)
    assert target is not None and target.gene == "GENEB"
    assert lookup.depth_at("7", 3000) == 400.0


def test_the_target_qc_flags_critical_and_incomplete_targets() -> None:
    rows = [
        _target("7", 0, 100, 1500.0, gene="GENEA"),
        _target("7", 200, 300, 250.0, gene="GENEA"),  # critical
        _target("7", 400, 500, 1200.0, gene="GENEB", covered=98.0),  # incomplete
        _target("7", 600, 700, 900.0, gene="GENEC"),  # advisory only
        _target("7", 800, 900, 0.0, gene="GENED"),  # no coverage
    ]
    summary = summarize_target_coverage(rows)
    assert summary.targets == 5
    assert summary.below_critical == 2
    assert summary.below_advisory == 3
    assert summary.zero_mean == 1
    assert summary.incomplete == 1
    assert summary.median_mean == 900.0
    # Without a gene list only the genes with a weak target, weakest first.
    assert [gene.gene for gene in summary.genes] == ["GENED", "GENEA", "GENEB"]
    assert summary.genes[1].weak_targets == 1 and summary.genes[1].targets == 2

    scoped = summarize_target_coverage(rows, genes=["genec", "GENEZ"])
    assert scoped.targets == 1
    # A requested gene the table lacks is listed with no target.
    assert [(gene.gene, gene.targets) for gene in scoped.genes] == [("GENEC", 1), ("GENEZ", 0)]


def test_the_coverage_page_lists_the_genes_whose_targets_all_pass_too() -> None:
    rows = [
        _target("7", 0, 100, 1500.0, gene="GENEA"),
        _target("7", 200, 300, 250.0, gene="GENEA"),  # critical
        _target("7", 600, 700, 900.0, gene="GENEC"),  # advisory only: passes
        _target("8", 0, 100, 1600.0, gene="GENEE"),  # passes
    ]
    summary = summarize_target_coverage(rows, include_passing=True)
    # Every gene in scope, the weak one with its weak target, the passing ones without.
    assert {gene.gene: (gene.targets, gene.weak_targets) for gene in summary.genes} == {
        "GENEA": (2, 1),
        "GENEC": (1, 0),
        "GENEE": (1, 0),
    }
    assert [len(gene.weak) for gene in summary.genes if gene.gene == "GENEA"] == [1]
    # The counts do not change with the list: the same targets are summarised.
    plain = summarize_target_coverage(rows)
    assert (summary.targets, summary.below_critical, summary.below_advisory) == (
        plain.targets,
        plain.below_critical,
        plain.below_advisory,
    )
    assert [gene.gene for gene in plain.genes] == ["GENEA"]


def test_the_sex_profile_of_maternal_plasma() -> None:
    autosomes = [_target(str(chrom), 0, 100, 1000.0) for chrom in range(1, 23)]
    female_fetus = sex_chromosome_coverage([*autosomes, _target("X", 0, 100, 1300.0), _target("Y", 0, 100, 0.0)])
    assert female_fetus.profile == "female_no_chrY_signal"
    assert female_fetus.x_ratio == pytest.approx(1.3)
    assert female_fetus.fetal_fraction_estimate is None

    male_fetus = sex_chromosome_coverage([*autosomes, _target("X", 0, 100, 1150.0), _target("Y", 0, 100, 120.0)])
    assert male_fetus.profile == "female_with_male_fetal_signal"
    assert male_fetus.fetal_fraction_estimate == pytest.approx(0.2)

    male_dna = sex_chromosome_coverage([*autosomes, _target("X", 0, 100, 700.0), _target("Y", 0, 100, 1250.0)])
    assert male_dna.profile == "male_like_not_maternal_plasma"

    assert sex_chromosome_coverage(autosomes).profile == "no_chrY_targets"
