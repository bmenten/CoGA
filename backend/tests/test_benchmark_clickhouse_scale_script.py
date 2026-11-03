"""The ClickHouse scale benchmark (scripts/benchmark_clickhouse_scale.py): the synthetic
cohort it builds, the server it refuses to write to, and the rows it generates against
the storage's own columns and rules. The benchmark itself runs against a throwaway
ClickHouse (docs/clickhouse-scale-benchmark.md)."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2] / "scripts"))
import benchmark_clickhouse_scale as bench

from backend.app.services.clickhouse_variant_ids import _xpos
from backend.app.services.clickhouse_variant_storage import SMALL_VARIANT_ENTRY_COLUMNS


def test_a_thousand_genomes_are_300_trios_and_100_singletons_with_700_founders() -> None:
    model = bench.CohortModel()
    count = bench.families_for_genomes(model, 1000, projects=4)
    families = bench.cohort_families(model, count, projects=4)

    assert sum(family.genomes for family in families) == 1000
    assert sum(1 for family in families if family.kind == "trio") == 300
    assert sum(1 for family in families if family.kind == "singleton") == 100
    assert bench.founder_count(families) == 700
    assert len({family.project_uuid for family in families}) == 4


def test_every_identifier_is_synthetic_and_unique() -> None:
    model = bench.CohortModel()
    families = bench.cohort_families(model, 400, projects=4)
    samples = [sample for family in families for sample in family.samples]

    assert all(re.fullmatch(r"FAM-\d{4}", family.family_id) for family in families)
    assert all(re.fullmatch(r"(PROBAND|MOTHER|FATHER1)-\d{4}", sample.sample_id) for sample in samples)
    assert len({sample.sample_id for sample in samples}) == len(samples)
    assert len({sample.sample_uuid for sample in samples}) == len(samples)
    assert len({family.family_uuid for family in families}) == len(families)


def test_each_founder_carries_the_same_number_of_rare_variants() -> None:
    model = bench.CohortModel()

    for founder in range(700):
        assert model.rare_new(founder) + model.rare_recurring(founder) == model.rare_per_genome
        assert model.rare_recurring(founder) >= 0
    assert model.rare_seen(1) == model.rare_per_genome
    # About 52 M rare and 9 M common variants: some 60 M distinct at 1,000 genomes.
    assert 50_000_000 < model.rare_seen(700) < 54_000_000


def test_the_variant_scale_shrinks_every_pool() -> None:
    model = bench.CohortModel(variant_scale=0.25)

    assert model.common == 2_275_000
    assert model.rare_per_genome == 112_500


def test_the_measured_families_are_two_trios_and_a_singleton() -> None:
    families = bench.cohort_families(bench.CohortModel(), 4, projects=4)

    searched_trio, searched_singleton, overwritten = bench.probe_families(families)

    assert (searched_trio.kind, searched_singleton.kind, overwritten.kind) == ("trio", "singleton", "trio")
    assert searched_trio != overwritten


@pytest.mark.parametrize("chromosome", bench.CHROMOSOMES)
def test_the_generated_xpos_ranks_chromosomes_as_the_storage_does(chromosome: str) -> None:
    rank = bench.CHROMOSOMES.index(chromosome) + 1

    assert rank * 1_000_000_000 + 12_345 == _xpos(chromosome, 12_345)


def test_the_generated_entries_rows_fill_every_column_the_storage_writes() -> None:
    family = bench.cohort_families(bench.CohortModel(), 1, projects=1)[0]

    insert = bench.entries_insert("db.`GRCh38/SNV_INDEL/entries`", bench.CohortModel(), family)
    columns = re.match(r"INSERT INTO \S+ \(([^)]*)\)", insert)

    assert columns is not None
    assert [column.strip().strip("`") for column in columns.group(1).split(",")] == list(
        SMALL_VARIANT_ENTRY_COLUMNS
    )
    for sample in family.samples:
        assert f"'{sample.sample_id}'" in insert
    assert f"'{family.family_uuid}'" in insert and f"'{family.project_uuid}'" in insert


def test_the_generator_binds_no_parameters() -> None:
    model = bench.CohortModel(variant_scale=0.01)
    source = bench.shared_sources(model, 0, 2)[0]
    statements = [
        bench.details_insert("t", model, source),
        bench.annotations_insert("t", model, source),
        bench.annotation_index_insert("t", model, source),
        bench.gene_index_insert("t", model, source),
        bench.entries_insert("t", model, bench.cohort_families(model, 4, projects=1)[3]),
    ]

    assert all("%(" not in statement for statement in statements)


class _Server:
    def __init__(self, databases: list[str], tables: list[str]) -> None:
        self.database = "coga_scale_benchmark"
        self._databases = databases
        self._tables = tables

    def rows(self, query: str, parameters: dict[str, Any] | None = None) -> list[tuple[Any, ...]]:
        if "system.databases" in query:
            return [(name,) for name in self._databases]
        return [(name,) for name in self._tables]


def test_a_server_holding_another_database_is_refused() -> None:
    server = _Server(["system", "default", "coga"], [])

    with pytest.raises(SystemExit, match="other databases"):
        bench.assert_throwaway(server, fresh=True)  # type: ignore[arg-type]


def test_a_fresh_run_refuses_a_database_with_tables_and_a_measurement_needs_one() -> None:
    filled = _Server(["system", "default", "coga_scale_benchmark"], ["GRCh38/SNV_INDEL/entries"])
    empty = _Server(["system", "default", "coga_scale_benchmark"], [])

    with pytest.raises(SystemExit, match="not empty"):
        bench.assert_throwaway(filled, fresh=True)  # type: ignore[arg-type]
    with pytest.raises(SystemExit, match="no benchmark data"):
        bench.assert_throwaway(empty, fresh=False)  # type: ignore[arg-type]
    bench.assert_throwaway(empty, fresh=True)  # type: ignore[arg-type]
