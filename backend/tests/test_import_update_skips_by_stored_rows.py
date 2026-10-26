"""An update skips what the store holds, and a skip never counts as an import (SAFE-20).

A package import in ``update`` mode skips a dataset, or a sample of it, that already holds
data. The track datasets (coverage, APCAD, PCF, WisecondorX, QDNAseq, the HiFiCNV tracks)
read that from Postgres's record of each track (``sample_interval_track_sources``), not from
the track's rows in ClickHouse. A failed overwrite whose restore stops part-way has deleted
the family's rows, table by table, and never reaches the Postgres restore: the record
outlives the rows. An update then skipped a track whose rows were gone, and reported the
dataset as ``imported``, so the import counted it as imported again and cleared its failure
from the family's ``import_incomplete`` flag (#807) while its rows were missing; only the
restore's whole-family entry (#829) still held the sign-out gate.

Now an update reads the store: it imports a track whose rows are gone and skips one whose
rows are there. A dataset it skips whole ends ``skipped``, and a sample it skips is left
out of what the dataset imported again, so a skip clears no failure.

The stores are faked at their edge: the interval table answers the statements
``clickhouse_interval_tracks`` sends ClickHouse, and the session the statements on
``sample_interval_track_sources``, ``sample_paraphase_results`` and the family's row. The
part-way restore itself runs against Postgres and ClickHouse in
e2e/test_e2e_import_restore_fails_part_way.py.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import copy
import dataclasses
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from backend.app.core.config import settings
from backend.app.schemas import FamilyImportDatasetSummary
from backend.app.services import (
    clickhouse_interval_tracks,
    family_package_datasets,
    family_package_import as package_import,
    family_package_registration as registration,
    sv_gene_index_service,
    variant_ranking_cache,
)
from backend.app.services.access_control import CurrentUser
from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext
from backend.app.services.family_package_common import (
    APCAD_PCF_SOURCE,
    APCAD_PCF_TRACK_TYPE,
    APCAD_SOURCE,
    CNV_SOURCE,
    FamilyPackageBundle,
    ManifestDataset,
    PackageManifest,
    ParsedPed,
)
from backend.app.services.nipt_target_coverage import TARGET_COVERAGE_SOURCE, TARGET_COVERAGE_TRACK_TYPE

FAMILY = "FAM001"
SAMPLES = ("S1", "S2")
_BED = "1\t0\t1000\t31.0\n1\t1000\t2000\t29.5\n1\t2000\t3000\t30.2\n"
_TARGET_TABLE = "#build\tchromosome\tstart\tend\tattribute\tmean\nhg38\t7\t100\t200\tGENEA;NM_1;1\t1500\n"
_CNV_VCF = (
    "##fileformat=VCFv4.2\n"
    '##INFO=<ID=SVTYPE,Number=1,Type=String,Description="Type of the SV.">\n'
    '##INFO=<ID=END,Number=1,Type=Integer,Description="End position">\n'
    '##FORMAT=<ID=CN,Number=1,Type=Float,Description="Copy number">\n'
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSample0\n"
    "chr1\t1001\t.\tN\t<DEL>\t57\tPASS\tSVTYPE=DEL;END=2000\tGT:CN\t0/1:1\n"
)


def _uuid(sample_id: str) -> str:
    return f"uuid-{sample_id}"


# --- the two stores of a track ------------------------------------------------------------


def _matches(key: tuple[str, str, str], params: Mapping[str, Any]) -> bool:
    """Whether a (sample uuid, track type, source) key is one a statement's bound filters
    pick: by sample, track type and source, each when bound (one family here)."""
    sample, track_type, source = key
    return all(
        params.get(name) in (None, value)
        for name, value in (("sample_uuid", sample), ("track_type", track_type), ("source", source))
    )


class _Stores:
    """Where a track is: its rows in ClickHouse's interval table (``rows``) and Postgres's
    record of it (``records``: ``sample_interval_track_sources``), each a row count by
    (sample uuid, track type, source); and the Paraphase results Postgres stores
    (``paraphase``: rows by sample uuid). The family's import state is ``flag``
    (``import_incomplete``), ``unfinished`` (``import_unfinished``) and ``imported``
    (``imported_scopes``)."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str], int] = {}
        self.records: dict[tuple[str, str, str], int] = {}
        self.paraphase: dict[str, int] = {}
        self.flag: dict[str, Any] | None = None
        self.unfinished: dict[str, Any] = {}
        self.imported: dict[str, Any] | None = None

    def track(self, sample_id: str, track_type: str, source: str, *, rows: int, recorded: bool = True) -> None:
        """A stored track: ``rows`` rows in the interval table and, when ``recorded``, its record."""
        key = (_uuid(sample_id), track_type, source)
        if rows:
            self.rows[key] = rows
        if recorded:
            self.records[key] = rows or 3

    async def clickhouse(self, query: str, parameters: Any = None) -> Any:
        """The interval table, as ``clickhouse_interval_tracks`` addresses it."""
        sql = " ".join(query.split())
        assert "INTERVAL/entries" in sql, sql
        if sql.startswith("CREATE TABLE"):
            return None
        if sql.startswith("INSERT INTO"):
            for row in parameters:  # _interval_row_tuple: family, sample, track type, source, ...
                key = (str(row[1]), str(row[2]), str(row[3]))
                self.rows[key] = self.rows.get(key, 0) + 1
            return None
        if sql.startswith("ALTER TABLE") and " DELETE WHERE " in sql:
            for key in [key for key in self.rows if _matches(key, parameters)]:
                del self.rows[key]
            return None
        if sql.startswith("SELECT count()"):
            return [(sum(count for key, count in self.rows.items() if _matches(key, parameters)),)]
        raise AssertionError(f"unmodelled ClickHouse statement: {sql}")


class _Session:
    """Postgres, as the import addresses it: the track records, the Paraphase results and
    the family row's import state (each statement as its function documents it)."""

    def __init__(self, stores: _Stores) -> None:
        self.stores = stores

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
        sql = " ".join(str(statement).split())
        params = params or {}
        stores = self.stores
        if "sample_interval_track_sources" in sql:
            if sql.startswith("SELECT COALESCE(SUM(row_count), 0)"):
                return _scalar(sum(n for key, n in stores.records.items() if _matches(key, params)))
            if sql.startswith("DELETE FROM"):
                for key in [key for key in stores.records if _matches(key, params)]:
                    del stores.records[key]
                return _scalar(None)
            assert sql.startswith("INSERT INTO"), sql
            stores.records[(params["sample_id"], params["track_type"], params["source"])] = params["row_count"]
            return _scalar(None)
        if "FROM sample_paraphase_results" in sql:
            return _scalar(stores.paraphase.get(params["sample_id"], 0))
        if sql.startswith("SELECT") and "FROM families" in sql:
            row = {
                "family_uuid": "family-uuid",
                "flag": copy.deepcopy(stores.flag),
                "unfinished": copy.deepcopy(stores.unfinished) or None,
                "imported_scopes": copy.deepcopy(stores.imported),
            }
            return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: row))
        assert sql.startswith("UPDATE families"), f"unmodelled statement: {sql}"
        if "entry" in params:  # an import's own entry, as it marks the family
            stores.unfinished[params["import_key"]] = json.loads(params["entry"])
        else:  # an import's failure, or a completed import's clear
            stores.flag = json.loads(params["flag"])
            stores.unfinished = json.loads(params["unfinished"]) or {}
            recorded = json.loads(params.get("imported_scopes", "null"))
            if recorded is not None:
                stores.imported = recorded
        return _scalar(None)

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


def _scalar(value: Any) -> SimpleNamespace:
    return SimpleNamespace(scalar_one=lambda: value)


@pytest.fixture
def stores(monkeypatch: pytest.MonkeyPatch) -> _Stores:
    state = _Stores()
    monkeypatch.setattr(clickhouse_interval_tracks, "execute_clickhouse", state.clickhouse)
    return state


# --- a dataset of the package, imported as the import loop runs its importer --------------


def _family_context() -> FamilyMetadataContext:
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id=FAMILY,
        project_ids=["project-uuid"],
        sample_rows=[
            {"sample_uuid": _uuid("S1"), "sample_id": "S1", "sex": "male", "role": "proband", "affected": True},
            {"sample_uuid": _uuid("S2"), "sample_id": "S2", "sex": "female", "role": "mother", "affected": False},
        ],
        sample_uuid_to_name={_uuid(sample): sample for sample in SAMPLES},
        sample_name_to_uuid={sample: _uuid(sample) for sample in SAMPLES},
        affected_sample_names=["S1"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


def _samples() -> dict[str, SampleMetadataContext]:
    return registration._family_sample_contexts(_family_context())


def _file(root: Path, name: str, text: str = _BED) -> str:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return name


def _job(
    stores: _Stores,
    root: Path,
    dataset_type: str,
    dataset: ManifestDataset,
) -> family_package_datasets.DatasetImportJob:
    """An update of ``dataset_type``: the job the import loop hands its importer."""
    return family_package_datasets.DatasetImportJob(
        session=_Session(stores),  # type: ignore[arg-type]
        bundle=FamilyPackageBundle(
            root=root,
            manifest_path=root / "manifest.yaml",
            manifest=PackageManifest(ped="family.ped", datasets={dataset_type: dataset}),
            ped_path=root / "family.ped",
            ped=ParsedPed(family_ids=[FAMILY], members=[], sample_ids=list(SAMPLES), text=""),
        ),
        dataset=dataset,
        summary=FamilyImportDatasetSummary(dataset_type=dataset_type, status="valid"),
        family_context=_family_context(),
        sample_contexts=_samples(),
        conflict_mode="update",
    )


async def _run(job: family_package_datasets.DatasetImportJob) -> FamilyImportDatasetSummary:
    return await family_package_datasets.DATASET_IMPORTERS[job.summary.dataset_type](job)


def _coverage(root: Path, *samples: str) -> ManifestDataset:
    return ManifestDataset(
        per_sample={sample: {"bed": _file(root, f"coverage/{sample}.bed")} for sample in samples}
    )


# --- coverage -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_update_imports_coverage_whose_rows_are_gone_though_postgres_records_them(
    stores: _Stores, tmp_path: Path
) -> None:
    # A failed overwrite's restore deleted the sample's coverage rows and stopped before it
    # put Postgres back: the record of the track is all that is left of it.
    stores.track("S1", "coverage", "web", rows=0)
    job = _job(stores, tmp_path, "coverage", _coverage(tmp_path, "S1"))

    result = await _run(job)

    # Before: skipped as there, the rows still gone, and reported as imported.
    assert stores.rows == {(_uuid("S1"), "coverage", "web"): 3}
    assert (result.status, result.message) == ("imported", "Imported coverage into interval tracks")
    # The BED declares nothing of what it holds: stored as unknown (TRACE-9).
    assert result.summary == {"S1": {"inserted": 3, "content": "unknown"}}
    assert job.skipped_samples == set()


@pytest.mark.asyncio
async def test_an_update_skips_coverage_whose_rows_are_stored_and_ends_skipped(
    stores: _Stores, tmp_path: Path
) -> None:
    # Rows stored, recorded or not (a part-written track has rows and no record yet): an
    # update leaves what is stored, as it leaves the small variants it finds.
    stores.track("S1", "coverage", "web", rows=3)
    stores.track("S2", "coverage", "web", rows=2, recorded=False)
    job = _job(stores, tmp_path, "coverage", _coverage(tmp_path, "S1", "S2"))

    result = await _run(job)

    # Before: "imported", so the import counted coverage as imported again; and S2, which
    # Postgres does not record, was replaced.
    assert result.status == "skipped"
    assert result.message == "Skipped coverage import in update mode because coverage already exists for S1, S2"
    assert result.summary == {"S1": {"skipped": True, "existing": 3}, "S2": {"skipped": True, "existing": 2}}
    assert stores.rows == {(_uuid("S1"), "coverage", "web"): 3, (_uuid("S2"), "coverage", "web"): 2}
    assert job.skipped_samples == {"S1", "S2"}


@pytest.mark.asyncio
async def test_an_update_imports_the_samples_whose_coverage_is_gone_and_names_those_it_skipped(
    stores: _Stores, tmp_path: Path
) -> None:
    stores.track("S1", "coverage", "web", rows=5)
    stores.track("S2", "coverage", "web", rows=0)  # recorded, its rows gone
    job = _job(stores, tmp_path, "coverage", _coverage(tmp_path, "S1", "S2"))

    result = await _run(job)

    assert stores.rows == {(_uuid("S1"), "coverage", "web"): 5, (_uuid("S2"), "coverage", "web"): 3}
    assert result.status == "imported"
    assert result.message == "Imported coverage; skipped existing samples in update mode: S1"
    # S1 is not imported again: the import leaves it out of what coverage imported.
    assert job.skipped_samples == {"S1"}


@pytest.mark.asyncio
async def test_an_update_reads_a_coverage_tables_track_from_the_store_too(
    stores: _Stores, tmp_path: Path
) -> None:
    # A NIPT pair's per-target coverage tables: the plasma's track is stored, the father's
    # recorded with its rows gone.
    stores.track("S1", TARGET_COVERAGE_TRACK_TYPE, TARGET_COVERAGE_SOURCE, rows=4)
    stores.track("S2", TARGET_COVERAGE_TRACK_TYPE, TARGET_COVERAGE_SOURCE, rows=0)
    dataset = ManifestDataset(
        per_sample={
            sample: {"target_table": _file(tmp_path, f"coverage_{sample}.txt", _TARGET_TABLE)}
            for sample in SAMPLES
        }
    )
    job = _job(stores, tmp_path, "coverage", dataset)

    result = await _run(job)

    assert stores.rows[(_uuid("S2"), TARGET_COVERAGE_TRACK_TYPE, TARGET_COVERAGE_SOURCE)] == 1
    assert result.summary["S1"] == {"skipped": True, "existing": 4}
    assert (result.status, job.skipped_samples) == ("imported", {"S1"})


# --- the other track datasets ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_update_that_skips_every_apcad_sample_ends_skipped(
    stores: _Stores, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def not_read(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("a stored APCAD track was read again")

    monkeypatch.setattr(family_package_datasets, "_import_apcad_track_file", not_read)
    for sample in SAMPLES:
        stores.track(sample, "apcad", APCAD_SOURCE, rows=6)
    dataset = ManifestDataset(per_sample={sample: {"bed": _file(tmp_path, f"apcad/{sample}.bed")} for sample in SAMPLES})
    job = _job(stores, tmp_path, "apcad", dataset)

    result = await _run(job)

    # Before: "imported".
    assert result.status == "skipped"
    assert result.message == "Skipped APCAD import in update mode because APCAD tracks already exist for S1, S2"
    assert job.skipped_samples == {"S1", "S2"}


@pytest.mark.asyncio
async def test_an_update_imports_an_apcad_family_vcf_whose_rows_are_gone(
    stores: _Stores, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    read: list[list[str]] = []

    async def read_family_vcf(_session: Any, *, sample_contexts: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        read.append(sorted(sample_contexts))
        return {sample: {"inserted": 6} for sample in sample_contexts}

    monkeypatch.setattr(family_package_datasets, "_import_apcad_track_file", read_family_vcf)
    for sample in SAMPLES:
        stores.track(sample, "apcad", APCAD_SOURCE, rows=0)  # recorded, the rows gone
    job = _job(stores, tmp_path, "apcad", ManifestDataset(family_vcf=_file(tmp_path, "apcad/family.vcf")))

    result = await _run(job)

    # Before: skipped, "APCAD tracks already exist", with none stored.
    assert read == [list(SAMPLES)]
    assert result.status == "imported"


@pytest.mark.asyncio
async def test_an_update_reads_pcf_overlays_from_the_store(
    stores: _Stores, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def import_segments(_session: Any, *, sample_context: Any, origin: str, **_kwargs: Any) -> dict[str, int]:
        stores.track(sample_context.sample_id, APCAD_PCF_TRACK_TYPE, APCAD_PCF_SOURCE, rows=2)
        return {"inserted": 2}

    monkeypatch.setattr(family_package_datasets, "_import_pcf_segment_file", import_segments)
    stores.track("S1", APCAD_PCF_TRACK_TYPE, APCAD_PCF_SOURCE, rows=2)
    stores.track("S2", APCAD_PCF_TRACK_TYPE, APCAD_PCF_SOURCE, rows=0)  # recorded, the rows gone
    dataset = ManifestDataset(
        per_sample={sample: {"maternal": _file(tmp_path, f"pcf/{sample}.pcf")} for sample in SAMPLES}
    )

    job = _job(stores, tmp_path, "pcf", dataset)
    result = await _run(job)

    assert result.summary == {"S1": {"skipped": True, "existing": 2}, "S2": {"maternal": {"inserted": 2}}}
    assert (result.status, job.skipped_samples) == ("imported", {"S1"})

    # Once both are stored, an update skips the dataset whole. Before: "imported".
    job = _job(stores, tmp_path, "pcf", dataset)
    result = await _run(job)
    assert result.status == "skipped"
    assert result.message == (
        "Skipped PCF import in update mode because PCF segment overlays already exist for S1, S2"
    )


@pytest.mark.asyncio
async def test_an_update_of_wisecondorx_reads_each_track_from_the_store(
    stores: _Stores, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def import_track(_session: Any, *, sample_context: Any, track_type: str, **_kwargs: Any) -> dict[str, int]:
        stores.track(sample_context.sample_id, track_type, "wisecondorx", rows=4)
        return {"processed": 4, "inserted": 4, "skipped": 0}

    monkeypatch.setattr(family_package_datasets, "_import_wisecondorx_track", import_track)
    stores.track("S1", "coverage", "wisecondorx", rows=9)
    stores.track("S1", "segments", "wisecondorx", rows=0)  # recorded, the rows gone
    dataset = ManifestDataset(
        per_sample={
            "S1": {"bins": _file(tmp_path, "wcx/S1_bins.bed"), "segments": _file(tmp_path, "wcx/S1_segments.bed")}
        }
    )

    job = _job(stores, tmp_path, "wisecondorx", dataset)
    result = await _run(job)

    # The segments are imported again (before: skipped), their stale record cleared first;
    # the bins are left, so S1 is not imported again whole.
    assert stores.rows[(_uuid("S1"), "segments", "wisecondorx")] == 4
    assert result.message == "Imported WisecondorX data; skipped existing tracks in update mode: S1:bins"
    assert (result.status, job.skipped_samples) == ("imported", {"S1"})

    # Both stored: skipped whole. Before: "imported".
    job = _job(stores, tmp_path, "wisecondorx", dataset)
    result = await _run(job)
    assert result.status == "skipped"
    assert result.message == (
        "Skipped WisecondorX import in update mode because these tracks already exist: S1:bins, S1:segments"
    )


@pytest.mark.asyncio
async def test_an_update_of_cnv_calls_leaves_out_a_sample_whose_tracks_it_skipped(
    stores: _Stores, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def nothing(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def no_calls_stored(*_args: Any, **_kwargs: Any) -> int:
        return 0

    async def known_samples(*_args: Any, **_kwargs: Any) -> set[str]:
        return {"S1"}

    for name, fake in (
        ("count_family_structural_variants", no_calls_stored),
        ("known_vcf_sample_ids", known_samples),
        ("lock_family_variant_writes", nothing),
        ("replace_family_structural_variants", nothing),
        ("_update_sv_file_metadata", nothing),
        ("_record_sample_signal_tracks", nothing),
    ):
        monkeypatch.setattr(family_package_datasets, name, fake)
    # The calls are gone; the copy-number track is stored.
    stores.track("S1", "segments", CNV_SOURCE, rows=4)
    dataset = ManifestDataset(
        per_sample={
            "S1": {
                "vcf": _file(tmp_path, "cnv/S1.vcf", _CNV_VCF),
                "copy_number_bedgraph": _file(tmp_path, "cnv/S1.copynum.bedgraph", "chr1\t0\t1000\t2\n"),
            }
        }
    )
    job = _job(stores, tmp_path, "cnv", dataset)

    result = await _run(job)

    assert result.status == "imported"
    assert result.message == (
        "Imported CNV calls into structural variant storage; "
        "skipped existing tracks in update mode: S1:copy_number_track"
    )
    # Its calls are imported again, its stored track is not: S1 is not imported again whole.
    assert job.skipped_samples == {"S1"}


# A family VCF of the samples, with no record: the importers below never read past its header.
_FAMILY_VCF = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\tS2\n"


def _small_variant_loader(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The small-variant loader at the importers' edge, the family holding no small variants:
    records each callset it is told to store."""
    loaded: list[str] = []

    async def no_small_variants(*_args: Any, **_kwargs: Any) -> int:
        return 0

    async def load(_session: Any, *, format_hint: str, **_kwargs: Any) -> dict[str, Any]:
        loaded.append(format_hint)
        return {"inserted": 3, "source_format": format_hint}

    async def no_origin(_job: Any) -> None:
        return None

    monkeypatch.setattr(family_package_datasets, "count_family_small_variants", no_small_variants)
    monkeypatch.setattr(family_package_datasets, "upload_family_small_variant_file", load)
    monkeypatch.setattr(family_package_datasets, "_record_pipeline_haplotype_origin", no_origin)
    return loaded


@pytest.mark.asyncio
async def test_an_update_of_the_haplotypes_reads_the_stored_blocks_not_their_record(
    stores: _Stores, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # SAFE-18. The imputed genotypes and their blocks are gone; Postgres still records the
    # blocks, as a restore that stopped part-way leaves it. Before: the update read that
    # record and skipped, the blocks staying missing.
    loaded = _small_variant_loader(monkeypatch)
    stores.track("S1", "haplotype", "glimpse2", rows=0)
    dataset = ManifestDataset(family_vcf=_file(tmp_path, "haplotypes/family.vcf", _FAMILY_VCF))

    result = await _run(_job(stores, tmp_path, "haplotypes", dataset))

    assert (result.status, loaded) == ("imported", ["glimpse2"])

    # The blocks stored, whatever Postgres records of them: skipped.
    stores.records.clear()
    stores.track("S2", "haplotype", "glimpse2", rows=5, recorded=False)
    loaded.clear()
    result = await _run(_job(stores, tmp_path, "haplotypes", dataset))
    assert (result.status, loaded) == ("skipped", [])
    assert result.summary == {"existing_small_variants": 0, "existing_haplotypes": 5}


@pytest.mark.asyncio
async def test_an_snv_update_of_imputed_genotypes_reads_their_blocks_too(
    stores: _Stores, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # SAFE-18 (#873). The family holds the blocks made from imputed genotypes, not the
    # genotypes. Before: the SNV dataset's update counted the genotypes alone, imported them
    # and replaced the blocks, where the haplotypes dataset's update skipped.
    loaded = _small_variant_loader(monkeypatch)
    stores.track("S1", "haplotype", "glimpse2", rows=5, recorded=False)
    imputed = ManifestDataset(
        family_vcf=_file(tmp_path, "snv/family.vcf", _FAMILY_VCF), source_format="glimpse2"
    )

    result = await _run(_job(stores, tmp_path, "snv", imputed))

    assert (result.status, loaded) == ("skipped", [])
    assert result.message == (
        "Skipped SNV import in update mode because haplotype blocks made from imputed genotypes "
        "already exist for this family"
    )
    assert result.summary == {"existing": 0, "existing_haplotypes": 5}

    # Direct calls are another callset: the blocks are no reason to skip them.
    direct = ManifestDataset(family_vcf=imputed.family_vcf, source_format="clair3")
    result = await _run(_job(stores, tmp_path, "snv", direct))
    assert (result.status, loaded) == ("imported", ["clair3"])


@pytest.mark.asyncio
async def test_a_cnv_dataset_whose_vcfs_call_no_cnv_is_imported_and_replaces_the_earlier_calls(
    stores: _Stores, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # SAFE-18. Before: it ended "registered", so the import did not count it as brought
    # again (its tracks were not recorded in imported_scopes), and an overwrite left the
    # family's earlier HiFiCNV calls in place.
    replaced: list[tuple[list[Any], str | None]] = []

    async def nothing(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def known_samples(*_args: Any, **_kwargs: Any) -> set[str]:
        return {"S1"}

    async def replace(_assembly: str, _family: str, _projects: Any, records: Any, *, source: str | None = None) -> None:
        replaced.append((list(records), source))

    for name, fake in (
        ("known_vcf_sample_ids", known_samples),
        ("lock_family_variant_writes", nothing),
        ("replace_family_structural_variants", replace),
        ("_update_sv_file_metadata", nothing),
        ("_record_sample_signal_tracks", nothing),
    ):
        monkeypatch.setattr(family_package_datasets, name, fake)
    no_calls = "".join(f"{line}\n" for line in _CNV_VCF.splitlines() if line.startswith("#"))
    dataset = ManifestDataset(
        per_sample={
            "S1": {
                "vcf": _file(tmp_path, "cnv/S1.vcf", no_calls),
                "copy_number_bedgraph": _file(tmp_path, "cnv/S1.copynum.bedgraph", "chr1\t0\t1000\t2\n"),
            }
        }
    )
    job = dataclasses.replace(_job(stores, tmp_path, "cnv", dataset), conflict_mode="overwrite")

    result = await _run(job)

    assert result.status == "imported"
    assert result.message == "Imported the CNV VCFs, which call no CNV"
    assert replaced == [([], CNV_SOURCE)]
    assert stores.rows[(_uuid("S1"), "segments", CNV_SOURCE)] == 1
    # So the import counts it as brought again, for the samples it names.
    scope = {"source": None, "samples": ["S1"]}
    assert registration.imported_dataset_scopes(
        registration.datasets_brought_again([("cnv", result.status)], {"cnv": scope}), {"cnv": scope}, {}
    ) == {"cnv": scope}


@pytest.mark.asyncio
async def test_an_update_that_skips_every_paraphase_sample_ends_skipped(stores: _Stores, tmp_path: Path) -> None:
    # Paraphase results are stored in Postgres: their table is what the update reads.
    stores.paraphase = {_uuid("S1"): 2, _uuid("S2"): 1}
    dataset = ManifestDataset(
        per_sample={sample: {"json": _file(tmp_path, f"paraphase/{sample}.json", "{}")} for sample in SAMPLES}
    )
    job = _job(stores, tmp_path, "paraphase", dataset)

    result = await _run(job)

    # Before: "imported".
    assert result.status == "skipped"
    assert result.message == (
        "Skipped Paraphase import in update mode because Paraphase results already exist for S1, S2"
    )
    assert job.skipped_samples == {"S1", "S2"}


@pytest.mark.asyncio
async def test_an_overwrite_of_a_track_clears_its_rows_no_record_names(
    stores: _Stores, monkeypatch: pytest.MonkeyPatch
) -> None:
    # SAFE-18. The sample's WisecondorX segments are stored without a record (a restore that
    # stopped part-way, a failed upload). Before: the source's track was cleared only when
    # Postgres recorded it, and the importer's own clear takes its file's rows alone, so those
    # rows stayed beside the new ones.
    cleared: list[tuple[str, str]] = []

    async def clear(_session: Any, *, sample_context: Any, track_type: str, source: str) -> None:
        cleared.append((track_type, source))

    async def importer(*, check_only: bool = False) -> dict[str, int]:
        return {} if check_only else {"inserted": 2}

    monkeypatch.setattr(family_package_datasets, "_delete_sample_interval_source", clear)
    stores.track("S1", "segments", "wisecondorx", rows=4, recorded=False)

    result = await family_package_datasets._import_interval_track_unless_present(
        _Session(stores),  # type: ignore[arg-type]
        sample_context=_samples()["S1"],
        track_type="segments",
        source="wisecondorx",
        conflict_mode="overwrite",
        importer=importer,
    )

    assert result == {"inserted": 2}
    assert cleared == [("segments", "wisecondorx")]

    # Neither recorded nor stored: nothing to clear.
    cleared.clear()
    stores.rows.clear()
    await family_package_datasets._import_interval_track_unless_present(
        _Session(stores),  # type: ignore[arg-type]
        sample_context=_samples()["S1"],
        track_type="segments",
        source="wisecondorx",
        conflict_mode="overwrite",
        importer=importer,
    )
    assert cleared == []


# --- what the import counts as imported again ------------------------------------------------


def test_a_skip_is_not_counted_as_importing_a_dataset_again() -> None:
    scopes = {
        "snv": {"source": "clair3", "samples": None},
        "coverage": {"source": None, "samples": ["S1", "S2"]},
        "paraphase": {"source": None, "samples": ["S1", "S2"]},
        "haplotypes": {"source": None, "samples": None},
    }

    imported = registration.imported_dataset_scopes(
        ["snv", "coverage", "paraphase", "haplotypes"],
        scopes,
        {"coverage": {"S1"}, "paraphase": {"S1", "S2"}, "haplotypes": {"S1"}, "snv": set()},
    )

    assert imported == {
        "snv": {"source": "clair3", "samples": None},
        # Imported again for S2 only.
        "coverage": {"source": None, "samples": ["S2"]},
        # Every sample skipped, or a family-level dataset that skipped one: not imported again.
    }
    # The flag's coverage failure, for S1 and S2, is not completed by it; one for S2 is.
    flag = {
        "failed_datasets": ["coverage"],
        "job_id": "job-1",
        "scopes": {"coverage": {"source": None, "samples": ["S1", "S2"]}},
    }
    assert registration.failures_left_after(flag, imported).failed == {"coverage": "job-1"}
    flag["scopes"] = {"coverage": {"source": None, "samples": ["S2"]}}
    assert registration.failures_left_after(flag, imported).flag is None


# --- through the import: the family's import-incomplete flag ----------------------------------


def _admin() -> CurrentUser:
    return CurrentUser(
        id="00000000-0000-0000-0000-000000000001",
        username="admin@example.com",
        email="admin@example.com",
        role="admin",
        created_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def package(stores: _Stores, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """FAM001's package, coverage for S1 and S2, imported into the existing family by the
    real loop and coverage importer. Registration only marks the family, as the real one
    does; the family's coverage was flagged failed by an overwrite whose restore failed
    part-way (job-1), and that restore's whole-family entry stays."""
    root = tmp_path / FAMILY
    root.mkdir()
    (root / "family.ped").write_text(f"{FAMILY} S1 0 0 1 2\n{FAMILY} S2 0 0 2 1\n", encoding="utf-8")
    manifest = f"schema_version: 1\nfamily_id: {FAMILY}\nped: family.ped\ndatasets:\n  coverage:\n    per_sample:\n"
    for sample in SAMPLES:
        manifest += f"      {sample}:\n        bed: {_file(root, f'coverage/{sample}.bed')}\n"
    (root / "manifest.yaml").write_text(manifest, encoding="utf-8")
    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path)])
    coverage_scope = {"source": None, "samples": list(SAMPLES)}
    stores.flag = {
        "at": "2026-10-13T10:00:00+00:00",
        "failed_datasets": ["coverage", "snv"],
        "imported_datasets": [],
        "job_id": "job-1",
        "failed_jobs": {"coverage": "job-1", "snv": "job-1"},
        "scopes": {"coverage": coverage_scope, "snv": {"source": "auto", "samples": None}},
    }
    stores.unfinished = {
        "job-1": {
            "job_id": "job-1",
            "at": "2026-10-13T10:00:00+00:00",
            "datasets": ["coverage", "snv"],
            "finished_datasets": [],
            "scopes": {},
            registration.WHOLE_FAMILY_KEY: [
                {"dataset": "coverage", "scope": {"source": None, "samples": [sample]}} for sample in SAMPLES
            ]
            + [{"dataset": "snv", "scope": {"source": "auto", "samples": None}}],
        }
    }

    async def register(session: Any, *, import_mark: Any = None, **_kwargs: Any):
        await registration._mark_family_import_unfinished(session, family_uuid="family-uuid", mark=import_mark)
        return _family_context(), False

    @asynccontextmanager
    async def hold(_family_uuid: str, **_kwargs: Any):
        yield

    async def no_warnings(*_args: Any, **_kwargs: Any) -> list[Any]:
        return []

    async def nothing(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(package_import, "_ensure_family_from_ped", register)
    monkeypatch.setattr(package_import, "hold_family_variant_writes", hold)
    monkeypatch.setattr(package_import, "_existing_package_entity_warnings", no_warnings)
    monkeypatch.setattr(variant_ranking_cache, "clear_family_ranking_cache", nothing)
    monkeypatch.setattr(sv_gene_index_service, "clear_family_sv_gene_index", nothing)
    return root


async def _update(stores: _Stores, root: Path) -> Any:
    return await package_import.execute_family_package_import(
        _Session(stores),  # type: ignore[arg-type]
        folder_path=root,
        project_id="project-uuid",
        dry_run=False,
        user=_admin(),
        conflict_mode="update",
        job_id="job-2",
    )


def _dataset(result: Any, dataset_type: str) -> FamilyImportDatasetSummary:
    return next(dataset for dataset in result.datasets if dataset.dataset_type == dataset_type)


@pytest.mark.asyncio
async def test_an_update_that_imports_what_the_restore_removed_clears_its_failure(
    stores: _Stores, package: Path
) -> None:
    # The restore deleted both samples' coverage rows; Postgres still records them.
    for sample in SAMPLES:
        stores.track(sample, "coverage", "web", rows=0)

    result = await _update(stores, package)

    assert result.completed is True
    # Before: both skipped and the rows still gone, yet coverage left the flag.
    assert stores.rows == {(_uuid(sample), "coverage", "web"): 3 for sample in SAMPLES}
    assert _dataset(result, "coverage").status == "imported"
    # Imported again whole: its failure goes, snv's stays, and so does the restore's entry
    # (an update removes nothing from it: only an overwrite does).
    assert stores.flag["failed_datasets"] == ["snv"]
    assert stores.unfinished["job-1"]["datasets"] == ["coverage", "snv"]


@pytest.mark.asyncio
async def test_an_update_that_skips_coverage_leaves_its_failure_flagged(stores: _Stores, package: Path) -> None:
    # The restore failed elsewhere: the coverage rows are stored.
    for sample in SAMPLES:
        stores.track(sample, "coverage", "web", rows=3)

    result = await _update(stores, package)

    assert result.completed is True
    # Before: reported imported, and coverage left the flag with nothing imported again.
    assert _dataset(result, "coverage").status == "skipped"
    assert stores.flag["failed_datasets"] == ["coverage", "snv"]
    assert stores.flag["failed_jobs"] == {"coverage": "job-1", "snv": "job-1"}
    note = next(line for line in result.logs if "stays flagged import-incomplete" in line)
    assert "coverage, snv in import job job-1" in note


@pytest.mark.asyncio
async def test_a_sample_an_update_skipped_keeps_the_datasets_failure_flagged(
    stores: _Stores, package: Path
) -> None:
    stores.track("S1", "coverage", "web", rows=3)  # stored: skipped
    stores.track("S2", "coverage", "web", rows=0)  # recorded, the rows gone: imported

    result = await _update(stores, package)

    assert stores.rows[(_uuid("S2"), "coverage", "web")] == 3
    coverage = _dataset(result, "coverage")
    assert (coverage.status, coverage.message) == (
        "imported",
        "Imported coverage; skipped existing samples in update mode: S1",
    )
    # S1 was not imported again, so the failure, for S1 and S2, stays. Before: cleared.
    assert stores.flag["failed_datasets"] == ["coverage", "snv"]
    # What the family holds records the sample this import imported, with what its BED
    # declared it holds: nothing, so unknown (TRACE-9).
    assert {"source": None, "samples": ["S2"], "content": "unknown"} in stores.imported["coverage"]
