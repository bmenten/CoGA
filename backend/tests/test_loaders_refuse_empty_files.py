"""A loader refuses a file without a record before it deletes what the file replaces (SAFE-29).

Most loaders replace what they load: they delete the stored data of their source, then read
the file and write its records. ClickHouse has no transactions, and a package import commits
each dataset as it goes. A file without a record -- a header alone, a file cut short before its
first record, nothing at all -- was read as a result with nothing in it: the stored data was
deleted and none written, the upload answered 200 and the dataset ended ``imported``. An upload
or import that brings a dataset again strikes it off a whole-family entry and removes a failure
the import-incomplete flag records for it (``record_family_upload``,
``imported_dataset_scopes``), so a file that brought nothing completed what was missing.

Each of these loaders now reads its file before it deletes anything, and refuses one without a
record, the TRGT upload with 400 and a package dataset by failing, with the stored data as it
was:

- the TRGT upload, and a package's TRGT files (one per sample, or a family VCF);
- the interval-track readers: WisecondorX and QDNAseq bins and segments, HiFiCNV's
  copy-number bedGraph and its depth and minor-allele-fraction bigWigs, APCAD (a VCF or BED per
  embryo, or a family VCF) and the PCF segment tables;
- Paraphase's JSON, a sample's NeedlR file and a long-read sample's SNV file, which each
  replaced the sample's data with none while the other samples' files imported.

A chrM SV file with its header and no call is a result, not an empty file: most samples carry
no mtDNA deletion. It replaces the sample's earlier chrM SV calls with none, as before; one
without its header is refused before anything is written.

A dataset of one file per sample checks every sample's file before it writes any (SAFE-3): a
later sample's refused file used to fail the dataset with the earlier samples' data replaced.
The APCAD file per embryo and the coverage BED or per-target table per sample were the last
checked as each was written. A TRGT value that is no number is refused with 400 (it answered
500), and a dry run warns of a data file without a record, which the import will refuse. A
Paraphase copy number that its Postgres ``integer`` column cannot hold fails the dataset as such
a TRGT value does (DATA-7: it failed the insert with a database error, after the sample's
results were deleted).

The stores are faked at their edge: the interval table answers the statements
``clickhouse_interval_tracks`` sends ClickHouse, and the session the Postgres statements the
loaders send (the track records, the TRGT calls, Paraphase's results, the family row). The
session refuses a Paraphase copy number beyond Postgres's ``integer``, as the driver does.
"""

from __future__ import annotations

import copy
import gzip
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest
from fastapi import HTTPException, UploadFile

from backend.app.routers import repeat_expansions as trgt_router
from backend.app.schemas import FamilyImportDatasetSummary
from backend.app.services import (
    annotation_manifest_service,
    clickhouse_interval_tracks,
    family_package_datasets,
    family_package_registration as registration,
    per_sample_small_variants,
)
from backend.app.services.family_metadata_context import FamilyMetadataContext
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
from backend.app.services.bed_service import BED_UPLOAD_SOURCE
from backend.app.services.family_package_common import VcfSampleColumnError
from backend.app.services.family_package_tracks import EmptyTrackFileError
from backend.app.services.family_package_validation import _validate_dataset
from backend.app.services.nipt import MONOGENIC_NIPT_ANALYSIS_TYPE
from backend.app.services.nipt_target_coverage import TARGET_COVERAGE_SOURCE, TARGET_COVERAGE_TRACK_TYPE

FAMILY = "FAM029"
SAMPLES = ("S1", "S2")


def _uuid(sample_id: str) -> str:
    return f"uuid-{sample_id}"


# --- the stores --------------------------------------------------------------------------------


def _matches(key: tuple[str, ...], params: Mapping[str, Any]) -> bool:
    """Whether a (sample uuid, track type, source, file name) key is one a statement's bound
    filters pick: by sample, track type, source and file name, each when bound (one family)."""
    sample, track_type, source, filename = key
    return all(
        params.get(name) in (None, value)
        for name, value in (
            ("sample_uuid", sample),
            ("track_type", track_type),
            ("source", source),
            ("filename", filename),
        )
    )


# Postgres's ``integer``, the type of the Paraphase copy-number columns (03_assay.sql).
_PG_INTEGER = (-(2**31), 2**31 - 1)
_PARAPHASE_INTEGER_COLUMNS = ("total_cn", "gene_cn", "highest_total_cn")


class _PostgresDataError(Exception):
    """A bound value its column's type cannot hold, as the driver refuses it (asyncpg's
    ``DataError``, "value out of int32 range"): the statement fails, as on the database."""


def _bind_integer_columns(row: Mapping[str, Any], columns: tuple[str, ...]) -> None:
    for column in columns:
        value = row[column]
        if value is not None and not _PG_INTEGER[0] <= value <= _PG_INTEGER[1]:
            raise _PostgresDataError(f"invalid input for query argument {column}: {value} (value out of int32 range)")


def _result(*, scalar: Any = None, rows: list[dict[str, Any]] | None = None) -> SimpleNamespace:
    found = rows or []
    return SimpleNamespace(
        scalar_one=lambda: scalar,
        scalar_one_or_none=lambda: scalar,
        mappings=lambda: SimpleNamespace(all=lambda: found, first=lambda: found[0] if found else None),
    )


class _Stores:
    """The family's stored data. ``rows``: the interval table's rows, and ``records``:
    Postgres's record of each track, both a row count by (sample uuid, track type, source,
    file name). ``repeats`` and ``paraphase``: each sample's TRGT calls and Paraphase results
    (by sample uuid); ``paraphase_rows`` the Paraphase rows inserted. ``unfinished``, ``flag``
    and ``imported``: the family row's ``import_unfinished``, ``import_incomplete`` and
    ``imported_scopes``. ``deletes`` lists each delete sent to either store, ``statements``
    every Postgres statement in order."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, ...], int] = {}
        self.records: dict[tuple[str, ...], int] = {}
        self.repeats: dict[str, int] = {}
        self.paraphase: dict[str, int] = {}
        self.paraphase_rows: list[dict[str, Any]] = []
        self.unfinished: Any = None
        self.flag: Any = None
        self.imported: Any = None
        self.deletes: list[tuple[str, dict[str, Any]]] = []
        self.statements: list[str] = []
        self.commits = 0

    def track(self, sample_id: str, track_type: str, source: str, filename: str = "stored.bed", *, rows: int) -> None:
        key = (_uuid(sample_id), track_type, source, filename)
        self.rows[key] = rows
        self.records[key] = rows

    def tracks(self) -> tuple[dict[tuple[str, ...], int], dict[tuple[str, ...], int]]:
        return dict(self.rows), dict(self.records)

    def index(self, prefix: str) -> int:
        """The position of the first Postgres statement that starts with ``prefix``."""
        return next(i for i, sql in enumerate(self.statements) if sql.startswith(prefix))

    def first(self, part: str) -> int:
        """The position of the first Postgres statement that holds ``part``."""
        return next(i for i, sql in enumerate(self.statements) if part in sql)

    async def clickhouse(self, query: str, parameters: Any = None) -> Any:
        """The interval table, as ``clickhouse_interval_tracks`` addresses it."""
        sql = " ".join(query.split())
        assert "INTERVAL/entries" in sql, sql
        if sql.startswith("CREATE TABLE"):
            return None
        if sql.startswith("INSERT INTO"):
            # _interval_row_tuple: family, sample, track type, source, file name, ...
            for row in parameters:
                key = (str(row[1]), str(row[2]), str(row[3]), str(row[4]))
                self.rows[key] = self.rows.get(key, 0) + 1
            return None
        if sql.startswith("ALTER TABLE") and " DELETE WHERE " in sql:
            self.deletes.append(("rows", dict(parameters)))
            for key in [key for key in self.rows if _matches(key, parameters)]:
                del self.rows[key]
            return None
        if sql.startswith("SELECT count()"):
            return [(sum(count for key, count in self.rows.items() if _matches(key, parameters)),)]
        raise AssertionError(f"unmodelled ClickHouse statement: {sql}")


class _Session:
    """Postgres, as the loaders address it."""

    def __init__(self, stores: _Stores) -> None:
        self.stores = stores

    async def execute(self, statement: Any, params: Any = None) -> Any:
        sql = " ".join(str(statement).split())
        stores = self.stores
        stores.statements.append(sql)
        bound: dict[str, Any] = params if isinstance(params, dict) else {}
        if "sample_interval_track_sources" in sql:
            if sql.startswith("SELECT COALESCE(SUM(row_count), 0)"):
                return _result(scalar=sum(n for key, n in stores.records.items() if _matches(key, bound)))
            if sql.startswith("DELETE FROM"):
                stores.deletes.append(("records", dict(bound)))
                for key in [key for key in stores.records if _matches(key, bound)]:
                    del stores.records[key]
                return _result()
            assert sql.startswith("INSERT INTO"), sql
            key = (bound["sample_id"], bound["track_type"], bound["source"], bound["filename"])
            stores.records[key] = bound["row_count"]
            return _result()
        if "FROM repeat_loci" in sql:
            return _result(rows=[])
        if "OR sample_id IN" in sql:  # the sample ids a per-sample file's column may name
            return _result(rows=[{"sample_id": sample} for sample in SAMPLES])
        if sql.startswith("SELECT COUNT(*) FROM repeat_expansions"):
            return _result(scalar=stores.repeats.get(bound["sample_id"], 0))
        if sql.startswith("DELETE FROM repeat_expansions"):
            stores.deletes.append(("repeats", dict(bound)))
            stores.repeats[bound["sample_id"]] = 0
            return _result()
        if sql.startswith("INSERT INTO repeat_expansions"):
            for record in params:
                stores.repeats[record["sample_id"]] = stores.repeats.get(record["sample_id"], 0) + 1
            return _result()
        if "sample_paraphase_results" in sql:
            if sql.startswith("DELETE FROM"):
                stores.deletes.append(("paraphase", dict(bound)))
                stores.paraphase[bound["sample_id"]] = 0
                return _result()
            assert sql.startswith("INSERT INTO"), sql
            for row in params:
                _bind_integer_columns(row, _PARAPHASE_INTEGER_COLUMNS)
            for row in params:
                stores.paraphase[row["sample_id"]] = stores.paraphase.get(row["sample_id"], 0) + 1
                stores.paraphase_rows.append(dict(row))
            return _result()
        if sql.startswith("UPDATE samples"):
            return _result()
        if sql.startswith("SELECT") and "FROM families" in sql:  # record_family_upload's row
            row = {
                "unfinished": copy.deepcopy(stores.unfinished),
                "imported_scopes": copy.deepcopy(stores.imported),
                "flag": copy.deepcopy(stores.flag),
            }
            return _result(rows=[row])
        if sql.startswith("UPDATE families"):
            stores.imported = json.loads(bound["imported_scopes"])
            if "unfinished" in bound:
                stores.unfinished = json.loads(bound["unfinished"])
            if "flag" in bound:
                stores.flag = json.loads(bound["flag"])
            return _result()
        raise AssertionError(f"unmodelled statement: {sql}")

    async def commit(self) -> None:
        self.stores.commits += 1
        self.stores.statements.append("COMMIT")

    async def rollback(self) -> None:
        return None


@pytest.fixture
def stores(monkeypatch: pytest.MonkeyPatch) -> _Stores:
    state = _Stores()

    async def nothing(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(clickhouse_interval_tracks, "execute_clickhouse", state.clickhouse)
    monkeypatch.setattr(annotation_manifest_service, "merge_vcf_header_provenance", nothing)
    return state


def _family_context() -> FamilyMetadataContext:
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id=FAMILY,
        project_ids=["project-uuid"],
        sample_rows=[
            {"sample_uuid": _uuid("S1"), "sample_id": "S1", "sex": "female", "role": "proband", "affected": True},
            {"sample_uuid": _uuid("S2"), "sample_id": "S2", "sex": "male", "role": "father", "affected": False},
        ],
        sample_uuid_to_name={_uuid(sample): sample for sample in SAMPLES},
        sample_name_to_uuid={sample: _uuid(sample) for sample in SAMPLES},
        affected_sample_names=["S1"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


def _samples() -> dict[str, Any]:
    return registration._family_sample_contexts(_family_context())


def _file(root: Path, name: str, content: str | bytes) -> str:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return name


def _job(
    stores: _Stores,
    root: Path,
    dataset_type: str,
    dataset: ManifestDataset,
    *,
    conflict_mode: str = "overwrite",
) -> family_package_datasets.DatasetImportJob:
    family = _family_context()
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
        family_context=family,
        sample_contexts=_samples(),
        conflict_mode=conflict_mode,
    )


async def _run(job: family_package_datasets.DatasetImportJob) -> FamilyImportDatasetSummary:
    return await family_package_datasets.DATASET_IMPORTERS[job.summary.dataset_type](job)


# --- TRGT: the upload ------------------------------------------------------------------------

_TRGT_HEADER = "##fileformat=VCFv4.2\n##source=TRGT\n"
_TRGT_COLUMNS = "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{samples}\n"
_TRGT_CALL = "chr4\t3074876\t.\tA\t<STR>\t.\tPASS\tEND=3074933;TRID=HD_HTT;MOTIFS=CAG\tGT:MC\t{calls}\n"


def _trgt_vcf(samples: list[str], *calls: list[str]) -> str:
    return (
        _TRGT_HEADER
        + _TRGT_COLUMNS.format(samples="\t".join(samples))
        + "".join(_TRGT_CALL.format(calls="\t".join(call)) for call in calls)
    )


# A file with no record for S1: what TRGT never writes, as it writes one per catalogue locus.
_EMPTY_TRGT = [
    pytest.param(_trgt_vcf(["S1"]), id="header-alone"),
    pytest.param(_TRGT_HEADER, id="no-chrom-line"),
    pytest.param("", id="nothing"),
    # Lines too short to be a record: none is read.
    pytest.param(_trgt_vcf(["S1"]) + "chr4\t3074876\t.\tA\n", id="no-readable-record"),
]

_REPEATS_S1 = {"source": None, "samples": ["S1"]}


def _whole_family_entry() -> dict[str, Any]:
    """The entry a restore that failed part-way left: the family's coverage, and S1's TRGT
    calls an upload brought, are still to be brought again."""
    mark = registration.ImportMark.begin(
        job_id="job-1", datasets=["coverage"], scopes={"coverage": {"source": None, "samples": ["S1"]}}
    )
    return {
        mark.key: registration.whole_family_entry(
            mark,
            imported={"repeats_trgt": [_REPEATS_S1]},
            unfinished={},
            family_samples=list(SAMPLES),
        )
    }


def _repeats_failed() -> dict[str, Any]:
    """A flag for an earlier failure of S1's TRGT calls."""
    return {
        "at": "2026-10-10T09:00:00+00:00",
        "failed_datasets": ["repeats_trgt"],
        "imported_datasets": [],
        "job_id": "job-0",
        "failed_jobs": {"repeats_trgt": "job-0"},
        "scopes": {"repeats_trgt": _REPEATS_S1},
    }


@pytest.fixture
def upload_trgt(stores: _Stores, monkeypatch: pytest.MonkeyPatch):
    """``await upload_trgt(text)``: the TRGT upload's route for S1, with ``overwrite``,
    against the stores."""

    async def nothing(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def sample_context(*_args: Any, **_kwargs: Any) -> Any:
        return _samples()["S1"]

    monkeypatch.setattr(trgt_router, "build_sample_metadata_context", sample_context)
    monkeypatch.setattr(trgt_router, "lock_family_variant_writes", nothing)
    monkeypatch.setattr(trgt_router, "record_upload_file_obj", nothing)

    async def run(text: str) -> dict[str, Any]:
        return await trgt_router.upload_repeat_expansions(
            sample_id="S1",
            file=UploadFile(file=BytesIO(text.encode()), filename="S1.trgt.vcf"),
            overwrite=True,
            session=_Session(stores),  # type: ignore[arg-type]
            user=SimpleNamespace(id="admin", username="admin"),  # type: ignore[arg-type]
        )

    return run


@pytest.mark.asyncio
@pytest.mark.parametrize("text", _EMPTY_TRGT)
async def test_a_trgt_upload_without_a_record_is_refused_with_the_calls_and_the_familys_record_as_they_were(
    stores: _Stores, upload_trgt, text: str
) -> None:
    stores.repeats[_uuid("S1")] = 5
    stores.unfinished = _whole_family_entry()
    stores.flag = _repeats_failed()
    stores.imported = {"repeats_trgt": [_REPEATS_S1]}
    before = copy.deepcopy((stores.unfinished, stores.flag, stores.imported))

    with pytest.raises(HTTPException) as refused:
        await upload_trgt(text)

    # Before: 200 with the sample's calls deleted and none stored, its TRGT calls struck off
    # the whole-family entry and its failure removed from the flag.
    assert refused.value.status_code == 400
    assert refused.value.detail == "No valid TRGT records found"
    assert stores.repeats == {_uuid("S1"): 5}
    assert stores.deletes == []
    assert (stores.unfinished, stores.flag, stores.imported) == before
    assert stores.commits == 0


@pytest.mark.asyncio
async def test_a_trgt_upload_reads_its_file_whole_before_it_replaces_the_calls(stores: _Stores, upload_trgt) -> None:
    stores.repeats[_uuid("S1")] = 5
    stores.unfinished = _whole_family_entry()
    stores.flag = _repeats_failed()

    result = await upload_trgt(_trgt_vcf(["S1_sort"], ["0/1:18_40"]))

    assert result["inserted"] == 1
    assert stores.repeats == {_uuid("S1"): 1}
    # The file is read (the catalogue it is scored against, the column of the sample) before
    # the calls are deleted; then the upload is recorded, the calls stored and committed.
    # Before: the calls were deleted first.
    deleted = stores.index("DELETE FROM repeat_expansions")
    assert stores.first("FROM repeat_loci") < deleted
    assert stores.first("OR sample_id IN") < deleted
    assert (
        stores.index("DELETE FROM repeat_expansions")
        < stores.index("UPDATE families")
        < stores.index("INSERT INTO repeat_expansions")
        < stores.index("COMMIT")
    )
    # A file that brought the calls strikes them off the entry and completes the failure.
    [entry] = stores.unfinished.values()
    assert entry["whole_family"] == [{"dataset": "coverage", "scope": {"source": None, "samples": ["S1"]}}]
    assert stores.flag is None


# --- TRGT: a package's files ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_trgt_file_without_a_record_fails_the_dataset_with_the_samples_calls_as_they_were(
    stores: _Stores, tmp_path: Path
) -> None:
    stores.repeats = {_uuid("S1"): 4, _uuid("S2"): 6}
    dataset = ManifestDataset(
        per_sample={
            "S1": {"file": _file(tmp_path, "trgt/S1.vcf", _trgt_vcf(["S1"], ["0/1:18_40"]))},
            "S2": {"file": _file(tmp_path, "trgt/S2.vcf", _trgt_vcf(["S2"]))},
        }
    )

    with pytest.raises(HTTPException, match="No valid TRGT records found"):
        await _run(_job(stores, tmp_path, "repeats_trgt", dataset))

    # Every sample's file is read before any sample's calls are replaced: S1's as much as
    # S2's are as they were. Before: S2's calls were deleted and the dataset ended imported
    # (SAFE-29); then S1's file was stored before S2's was read, so the dataset failed with
    # S1's calls replaced (SAFE-3).
    assert stores.repeats == {_uuid("S1"): 4, _uuid("S2"): 6}
    assert stores.deletes == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "detail"),
    [
        pytest.param(_trgt_vcf(["S1", "S2"]), "No valid TRGT records found for S1, S2", id="header-alone"),
        pytest.param(_TRGT_HEADER, "No valid TRGT records found", id="no-chrom-line"),
        # A record cut short after S1's column: nothing for S2, whose calls it replaces.
        pytest.param(
            _trgt_vcf(["S1", "S2"])
            + "chr4\t3074876\t.\tA\t<STR>\t.\tPASS\tEND=3074933;TRID=HD_HTT;MOTIFS=CAG\tGT:MC\t0/1:18_40\n",
            "No valid TRGT records found for S2",
            id="none-for-a-sample",
        ),
    ],
)
async def test_a_family_trgt_vcf_without_a_record_for_a_sample_replaces_no_call(
    stores: _Stores, tmp_path: Path, text: str, detail: str
) -> None:
    stores.repeats = {_uuid("S1"): 4, _uuid("S2"): 6}
    dataset = ManifestDataset(family_vcf=_file(tmp_path, "trgt/family.vcf", text))

    with pytest.raises(HTTPException) as refused:
        await _run(_job(stores, tmp_path, "repeats_trgt", dataset))

    # Before: both samples' calls were deleted as the #CHROM line was read.
    assert refused.value.detail == detail
    assert stores.repeats == {_uuid("S1"): 4, _uuid("S2"): 6}
    assert stores.deletes == []
    assert stores.commits == 0


@pytest.mark.asyncio
async def test_a_family_trgt_vcf_with_a_record_for_each_sample_replaces_their_calls(
    stores: _Stores, tmp_path: Path
) -> None:
    stores.repeats = {_uuid("S1"): 4, _uuid("S2"): 6}
    text = _trgt_vcf(["S1", "S2"], ["0/1:18_40", "0/0:17_19"])
    dataset = ManifestDataset(family_vcf=_file(tmp_path, "trgt/family.vcf", text))

    result = await _run(_job(stores, tmp_path, "repeats_trgt", dataset))

    assert result.status == "imported"
    assert stores.repeats == {_uuid("S1"): 1, _uuid("S2"): 1}
    assert stores.commits == 1


# --- the interval-track readers -------------------------------------------------------------------


def _assert_untouched(stores: _Stores, before: tuple[dict[tuple[str, ...], int], dict[tuple[str, ...], int]]) -> None:
    """Nothing was deleted or written: the rows and records are as they were."""
    assert stores.deletes == []
    assert stores.tracks() == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        pytest.param("chr\tstart\tend\tratio\tzscore\n", id="header-alone"),
        pytest.param("", id="nothing"),
        pytest.param("chr\tstart\tend\tratio\tzscore\n1\tnot\ta\tbin\t.\n", id="no-readable-bin"),
    ],
)
async def test_a_wisecondorx_file_without_a_bin_fails_the_dataset_before_the_track_is_cleared(
    stores: _Stores, tmp_path: Path, content: str
) -> None:
    stores.track("S1", "coverage", "wisecondorx", "S1_bins.bed", rows=5)
    stores.track("S1", "segments", "wisecondorx", "S1_segments.bed", rows=2)
    before = stores.tracks()
    dataset = ManifestDataset(per_sample={"S1": {"bins": _file(tmp_path, "wcx/S1_bins.bed", content)}})

    with pytest.raises(EmptyTrackFileError, match="S1_bins.bed holds no record of the coverage track"):
        await _run(_job(stores, tmp_path, "wisecondorx", dataset))

    # Before: the sample's WisecondorX coverage was cleared, none stored, and the dataset
    # ended imported.
    _assert_untouched(stores, before)


@pytest.mark.asyncio
async def test_a_wisecondorx_file_with_bins_replaces_the_track(stores: _Stores, tmp_path: Path) -> None:
    stores.track("S1", "coverage", "wisecondorx", "old_bins.bed", rows=5)
    content = "chr\tstart\tend\tratio\tzscore\n1\t0\t1000\t0.1\t0.5\n1\t1000\t2000\t-0.2\t-1.1\n"
    dataset = ManifestDataset(per_sample={"S1": {"bins": _file(tmp_path, "wcx/S1_bins.bed", content)}})

    result = await _run(_job(stores, tmp_path, "wisecondorx", dataset))

    assert result.status == "imported"
    assert stores.rows == {(_uuid("S1"), "coverage", "wisecondorx", "S1_bins.bed"): 2}


@pytest.mark.asyncio
async def test_a_qdnaseq_file_without_a_bin_fails_the_dataset_before_the_track_is_cleared(
    stores: _Stores, tmp_path: Path
) -> None:
    stores.track("S1", "coverage", "qdnaseq", "S1_bins.csv", rows=4)
    before = stores.tracks()
    dataset = ManifestDataset(
        per_sample={"S1": {"bins": _file(tmp_path, "qdnaseq/S1_bins.csv", "chromosome,start,end,ratio\n")}}
    )

    with pytest.raises(EmptyTrackFileError, match="S1_bins.csv holds no record of the coverage track"):
        await _run(_job(stores, tmp_path, "qdnaseq", dataset))

    _assert_untouched(stores, before)


_CNV_VCF = (
    "##fileformat=VCFv4.2\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSample0\n"
    "chr1\t1001\t.\tN\t<DEL>\t57\tPASS\tSVTYPE=DEL;END=2000\tGT:CN\t0/1:1\n"
)


def _write_bigwig(path: Path, contigs: dict[str, list[float]]) -> None:
    """A bigWig of 1 kb bins on each contig, one per value."""
    import pyBigWig  # the importer's own reader, a pinned dependency

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = pyBigWig.open(str(path), "w")
    handle.addHeader([(contig, 100_000) for contig in contigs], maxZooms=0)
    for contig, bins in contigs.items():
        handle.addEntries(
            [contig] * len(bins),
            [index * 1000 for index in range(len(bins))],
            ends=[(index + 1) * 1000 for index in range(len(bins))],
            values=bins,
        )
    handle.close()


@pytest.fixture
def cnv_stores(stores: _Stores, monkeypatch: pytest.MonkeyPatch) -> _Stores:
    """The stores, with the HiFiCNV calls' own writes recorded as nothing."""

    async def nothing(*_args: Any, **_kwargs: Any) -> None:
        return None

    for name in (
        "lock_family_variant_writes",
        "replace_family_structural_variants",
        "_update_sv_file_metadata",
        "_record_sample_signal_tracks",
    ):
        monkeypatch.setattr(family_package_datasets, name, nothing)
    return stores


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("track", "track_type", "make", "message"),
    [
        pytest.param(
            "copy_number_bedgraph",
            "segments",
            lambda path: path.write_text("track type=bedGraph\n", encoding="utf-8"),
            "holds no record of the segments track",
            id="bedgraph",
        ),
        # Read depth zero throughout: the import leaves zero bins out.
        pytest.param(
            "depth_bigwig",
            "coverage",
            lambda path: _write_bigwig(path, {"chr1": [0.0, 0.0, 0.0]}),
            "holds no interval on a primary chromosome with a value other than zero",
            id="depth-bigwig",
        ),
        pytest.param(
            "maf_bigwig",
            "apcad",
            lambda path: _write_bigwig(path, {"chrUn_KI270742v1": [0.25]}),
            "holds no interval on a primary chromosome",
            id="maf-bigwig",
        ),
    ],
)
async def test_a_hificnv_signal_file_without_a_value_fails_the_dataset_before_its_track_is_cleared(
    cnv_stores: _Stores, tmp_path: Path, track: str, track_type: str, make: Any, message: str
) -> None:
    stores = cnv_stores
    stores.track("S1", track_type, CNV_SOURCE, "S1.signal", rows=7)
    before = stores.tracks()
    path = tmp_path / "cnv" / f"S1.{track}"
    path.parent.mkdir(parents=True, exist_ok=True)
    make(path)
    dataset = ManifestDataset(
        per_sample={"S1": {"vcf": _file(tmp_path, "cnv/S1.vcf", _CNV_VCF), track: f"cnv/S1.{track}"}}
    )

    with pytest.raises(EmptyTrackFileError, match=message):
        await _run(_job(stores, tmp_path, "cnv", dataset))

    # Before: the track was cleared, none stored, and the dataset went on to import the calls.
    _assert_untouched(stores, before)


_PCF_HEADER = "sampleID,chrom,arm,start.pos,end.pos,n.probes,mean\n"


@pytest.mark.asyncio
async def test_a_pcf_table_without_a_segment_fails_the_dataset_before_the_track_is_cleared(
    stores: _Stores, tmp_path: Path
) -> None:
    stores.track("S1", APCAD_PCF_TRACK_TYPE, APCAD_PCF_SOURCE, "S1_maternal.pcf", rows=3)
    before = stores.tracks()
    dataset = ManifestDataset(
        per_sample={
            "S1": {
                "maternal": _file(tmp_path, "pcf/S1_maternal.pcf", _PCF_HEADER + "S1,1,p,1,1000,10,0.5\n"),
                "paternal": _file(tmp_path, "pcf/S1_paternal.pcf", _PCF_HEADER),
            }
        }
    )

    with pytest.raises(EmptyTrackFileError, match="S1_paternal.pcf holds no segment"):
        await _run(_job(stores, tmp_path, "pcf", dataset))

    # Both of the sample's tables are read before its track is deleted. Before: the track
    # was deleted, the maternal table stored and the paternal one read as none.
    _assert_untouched(stores, before)


_APCAD_VCF_HEADER = (
    "##fileformat=VCFv4.2\n"
    '##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allelic depths">\n'
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{samples}\n"
)


def _apcad_vcf(samples: list[str], *calls: list[str]) -> str:
    return _APCAD_VCF_HEADER.format(samples="\t".join(samples)) + "".join(
        f"1\t{100 * (index + 1)}\t.\tA\tG\t50\tPASS\t.\tGT:AD\t" + "\t".join(call) + "\n"
        for index, call in enumerate(calls)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entry", "error"),
    [
        # Records, none read for S1: the file's one column is another tube's.
        pytest.param(
            {"vcf": ("apcad/S1.vcf", _apcad_vcf(["OTHER"], ["0/1:10,10"]))},
            "S1.vcf holds no APCAD value for S1",
            id="vcf-without-the-samples-column",
        ),
        # No record at all: read as a BED, which holds none either.
        pytest.param(
            {"vcf": ("apcad/S1.vcf", _apcad_vcf(["S1"]))},
            "No valid BED records found",
            id="vcf-header-alone",
        ),
        pytest.param(
            {"bed": ("apcad/S1.apcad.bed", "# chrom start end id value origin\n")},
            "S1.apcad.bed holds no APCAD value for S1",
            id="bed-without-a-record",
        ),
    ],
)
async def test_an_apcad_file_without_a_value_for_its_sample_fails_the_dataset_before_the_track_is_cleared(
    stores: _Stores, tmp_path: Path, entry: dict[str, tuple[str, str]], error: str
) -> None:
    stores.track("S1", "apcad", APCAD_SOURCE, "S1.old.vcf", rows=6)
    before = stores.tracks()
    dataset = ManifestDataset(
        per_sample={"S1": {role: _file(tmp_path, name, content) for role, (name, content) in entry.items()}}
    )

    with pytest.raises((EmptyTrackFileError, HTTPException)) as refused:
        await _run(_job(stores, tmp_path, "apcad", dataset))

    assert error in str(getattr(refused.value, "detail", refused.value))
    # Before: the sample's APCAD was deleted and none stored, and the dataset ended imported
    # (a VCF without a record was refused as a BED only after its delete).
    _assert_untouched(stores, before)


@pytest.mark.asyncio
async def test_an_apcad_family_vcf_without_a_record_fails_the_dataset_before_any_track_is_cleared(
    stores: _Stores, tmp_path: Path
) -> None:
    stores.track("S1", "apcad", APCAD_SOURCE, "family.vcf", rows=6)
    stores.track("S2", "apcad", APCAD_SOURCE, "family.vcf", rows=6)
    before = stores.tracks()
    dataset = ManifestDataset(family_vcf=_file(tmp_path, "apcad/family.vcf", _apcad_vcf(["S1", "S2"])))

    with pytest.raises(EmptyTrackFileError, match="family.vcf holds no APCAD value for S1, S2"):
        await _run(_job(stores, tmp_path, "apcad", dataset))

    _assert_untouched(stores, before)


@pytest.mark.asyncio
async def test_an_apcad_family_vcf_replaces_only_the_tracks_of_the_samples_it_holds_values_for(
    stores: _Stores, tmp_path: Path
) -> None:
    stores.track("S1", "apcad", APCAD_SOURCE, "old.vcf", rows=6)
    stores.track("S2", "apcad", APCAD_SOURCE, "old.vcf", rows=6)
    dataset = ManifestDataset(
        family_vcf=_file(tmp_path, "apcad/family.vcf", _apcad_vcf(["S1"], ["0/1:10,10"], ["0/1:12,8"]))
    )

    result = await _run(_job(stores, tmp_path, "apcad", dataset))

    # S2 has no column in the file: its track stays. Before: deleted, and none stored.
    assert result.status == "imported"
    assert stores.rows == {
        (_uuid("S1"), "apcad", APCAD_SOURCE, "family.vcf"): 2,
        (_uuid("S2"), "apcad", APCAD_SOURCE, "old.vcf"): 6,
    }
    assert {params.get("sample_uuid") for _store, params in stores.deletes} == {_uuid("S1")}


# --- Paraphase, NeedlR, a long-read sample's SNV file ---------------------------------------------


@pytest.mark.asyncio
async def test_a_paraphase_json_without_a_gene_fails_the_dataset_with_the_results_as_they_were(
    stores: _Stores, tmp_path: Path
) -> None:
    stores.paraphase[_uuid("S1")] = 3
    dataset = ManifestDataset(per_sample={"S1": {"json": _file(tmp_path, "paraphase/S1.json", "{}")}})

    with pytest.raises(RuntimeError, match="Paraphase JSON for S1 holds no gene result"):
        await _run(_job(stores, tmp_path, "paraphase", dataset))

    # Before: the sample's results were deleted, none stored, and the dataset ended imported.
    assert stores.paraphase == {_uuid("S1"): 3}
    assert stores.deletes == []


_NEEDLR_HEADER = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"


@pytest.mark.asyncio
async def test_a_needlr_file_without_a_record_fails_the_dataset_before_the_calls_are_replaced(
    stores: _Stores, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replaced: list[Any] = []

    async def nothing(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def replace(*args: Any, **kwargs: Any) -> None:
        replaced.append(args[3])

    monkeypatch.setattr(family_package_datasets, "lock_family_variant_writes", nothing)
    monkeypatch.setattr(family_package_datasets, "replace_family_structural_variants", replace)
    monkeypatch.setattr(family_package_datasets, "_update_sv_file_metadata", nothing)
    record = "1\t1000\tsv1\tN\t<DEL>\t60\tPASS\tSVTYPE=DEL;END=2000;SVLEN=-1000;Query_ID=S1_sv_phased;Genotype=0/1\n"
    dataset = ManifestDataset(
        per_sample={
            "S1": {"vcf": _file(tmp_path, "sv/S1.needlr.vcf", _NEEDLR_HEADER + record)},
            "S2": {"vcf": _file(tmp_path, "sv/S2.needlr.vcf", _NEEDLR_HEADER)},
        }
    )

    with pytest.raises(RuntimeError, match=r"The NeedlR file of S2 \(S2.needlr.vcf\) holds no structural variant"):
        await _run(_job(stores, tmp_path, "sv_needlr", dataset))

    # Before: the family's NeedlR calls were replaced by S1's alone, S2's removed.
    assert replaced == []


_SNV_HEADER = (
    "##fileformat=VCFv4.2\n##contig=<ID=chr1,length=248956422>\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample}\n"
)


@pytest.mark.asyncio
async def test_a_long_read_samples_snv_file_without_a_record_is_refused_before_the_callset_is_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []

    async def nothing(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def stored(*_args: Any, **_kwargs: Any) -> int:
        return 10

    async def called_samples(*_args: Any, **_kwargs: Any) -> set[str]:
        return {_uuid("S1"), _uuid("S2")}

    def record(name: str):
        async def recorded(*_args: Any, **_kwargs: Any) -> None:
            events.append(name)

        return recorded

    for name, fake in {
        "lock_family_variant_writes": nothing,
        "count_family_small_variants": stored,
        "family_small_variant_call_samples": called_samples,
        "delete_family_small_variants": record("delete"),
        "insert_small_variant_records": record("insert"),
        "refresh_family_small_variant_summaries": nothing,
    }.items():
        monkeypatch.setattr(per_sample_small_variants, name, fake)
    monkeypatch.setattr(annotation_manifest_service, "merge_vcf_header_provenance", nothing)
    s1 = tmp_path / "S1.vcf"
    s1.write_text(_SNV_HEADER.format(sample="S1") + "chr1\t100\t.\tA\tG\t30\tPASS\t.\tGT\t0/1\n", encoding="utf-8")
    s2 = tmp_path / "S2.vcf"
    s2.write_text(_SNV_HEADER.format(sample="S2"), encoding="utf-8")

    with pytest.raises(HTTPException) as refused:
        await per_sample_small_variants.upload_family_per_sample_small_variant_files(
            SimpleNamespace(),  # type: ignore[arg-type]
            context=_family_context(),
            sample_contexts=_samples(),
            files=[
                per_sample_small_variants.PerSampleVcf(sample_id="S1", path=s1),
                per_sample_small_variants.PerSampleVcf(sample_id="S2", path=s2),
            ],
            overwrite=True,
        )

    # Before: the callset was deleted and S1's calls stored, S2 left without a call.
    assert refused.value.status_code == 400
    assert "The SNV file of S2 (S2.vcf) holds no record" in str(refused.value.detail)
    assert events == []


# --- the chrM SV file: a header alone is a result ---------------------------------------------------

_MITO_SV_HEADER = (
    "##fileformat=VCFv4.2\n##source=Sniffles2_2.7.3\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSample0\n"
)
_MITO_DELETION = (
    "chrM\t8470\tSniffles2.DEL.1M0\tN\t<DEL>\t60\tPASS\t"
    "PRECISE;SVTYPE=DEL;SVLEN=-4977;END=13447;SUPPORT=20;VAF=0.05\tGT:GQ:DR:DV\t0/1:60:380:20\n"
)


def test_a_chrm_sv_file_with_its_header_and_no_call_replaces_the_samples_calls_with_none() -> None:
    """Most samples carry no mtDNA deletion, so Sniffles2 writes its header and no call: a
    result, which replaces the sample's earlier calls (the decision kept by SAFE-29). The
    other samples' calls stay."""
    from backend.app.services.clickhouse_variant_records import StoredStructuralVariantRow
    from backend.app.services.family_package_variants import _iter_mito_sv_records, _mito_sv_rows_after_import

    stored = _iter_mito_sv_records(_MITO_SV_HEADER + _MITO_DELETION, sample_id="S2")[0]
    earlier = _iter_mito_sv_records(_MITO_SV_HEADER + _MITO_DELETION, sample_id="S1")[0]
    stored.calls.extend(earlier.calls)
    stored.annotations.extend(earlier.annotations)

    new = _iter_mito_sv_records(_MITO_SV_HEADER, sample_id="S1")
    rows = _mito_sv_rows_after_import(
        [StoredStructuralVariantRow(project_id="project-uuid", record=stored)],
        new,
        replaced_samples={"S1", _uuid("S1")},
        project_ids=["project-uuid"],
    )

    assert new == []
    [row] = rows
    assert [call.sample for call in row.record.calls] == ["S2"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [pytest.param("", id="nothing"), pytest.param("##fileformat=VCFv4.2\n", id="no-chrom-line")],
)
async def test_a_chrm_sv_file_without_its_header_fails_the_mito_dataset_before_anything_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str
) -> None:
    writes: list[str] = []

    async def must_not_write(*_args: Any, **_kwargs: Any) -> None:
        writes.append("write")
        raise AssertionError("nothing may be written for a refused mito dataset")

    async def known(*_args: Any, **_kwargs: Any) -> set[str]:
        return set(SAMPLES)

    monkeypatch.setattr(family_package_datasets, "known_vcf_sample_ids", known)
    for name in ("upload_family_small_variant_file", "lock_family_variant_writes", "rewrite_family_structural_variants"):
        monkeypatch.setattr(family_package_datasets, name, must_not_write)
    chrm = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
    dataset = ManifestDataset(
        per_sample={
            "S1": {
                "vcf": _file(tmp_path, "mito/S1.vcf", chrm + "chrM\t3243\t.\tA\tG\t30\tPASS\t.\tGT\t0/1\n"),
                "sv_vcf": _file(tmp_path, "mito/S1_sv.vcf", content),
            }
        }
    )
    job = _job(_Stores(), tmp_path, "mito", dataset)

    with pytest.raises(VcfSampleColumnError, match="Mito SV VCF of S1 has no sample column"):
        await _run(job)

    assert writes == []


# --- SAFE-3: every sample's file is checked before anything of its dataset is written -----------


def _trgt_line(*, pos: str = "3074876", end: str = "3074933", fmt: str = "GT:MC", call: str = "0/1:18_40") -> str:
    return f"chr4\t{pos}\t.\tA\t<STR>\t.\tPASS\tEND={end};TRID=HD_HTT;MOTIFS=CAG\t{fmt}\t{call}\n"


# A record with a value that is no number where one is read: on line 4, after the two
# meta-information lines and the #CHROM line.
_MALFORMED_TRGT = [
    pytest.param(_trgt_line(pos="x"), id="position-no-number"),
    pytest.param(_trgt_line(pos="3074876.5"), id="position-a-fraction"),
    pytest.param(_trgt_line(end="9" * 25), id="end-beyond-any-chromosome"),
    pytest.param(_trgt_line(call="0/1:18_x"), id="motif-count-no-number"),
    pytest.param(_trgt_line(fmt="GT:MC:AP", call="0/1:18_40:nan,0.9"), id="purity-nan"),
]
_TRGT_REFUSAL = "TRGT VCF line 4 holds a value that cannot be read where a number is expected; nothing was stored"


@pytest.mark.asyncio
@pytest.mark.parametrize("record", _MALFORMED_TRGT)
async def test_a_trgt_upload_with_a_value_that_is_no_number_is_refused_with_the_calls_as_they_were(
    stores: _Stores, upload_trgt, record: str
) -> None:
    stores.repeats[_uuid("S1")] = 5

    with pytest.raises(HTTPException) as refused:
        await upload_trgt(_TRGT_HEADER + _TRGT_COLUMNS.format(samples="S1") + record)

    # Before: 500, from the int() or float() that read the value, or from the insert, as
    # Postgres holds no 25-digit position and no NaN in JSON (DATA-7).
    assert (refused.value.status_code, refused.value.detail) == (400, _TRGT_REFUSAL)
    assert stores.repeats == {_uuid("S1"): 5}
    assert stores.deletes == []
    assert stores.commits == 0


@pytest.mark.asyncio
async def test_a_later_samples_trgt_file_with_a_value_that_is_no_number_fails_the_dataset_with_every_samples_calls(
    stores: _Stores, tmp_path: Path
) -> None:
    stores.repeats = {_uuid("S1"): 4, _uuid("S2"): 6}
    s2 = _TRGT_HEADER + _TRGT_COLUMNS.format(samples="S2") + _trgt_line(pos="x")
    dataset = ManifestDataset(
        per_sample={
            "S1": {"file": _file(tmp_path, "trgt/S1.vcf", _trgt_vcf(["S1"], ["0/1:18_40"]))},
            "S2": {"file": _file(tmp_path, "trgt/S2.vcf", s2)},
        }
    )

    with pytest.raises(HTTPException, match="TRGT VCF line 4 holds a value"):
        await _run(_job(stores, tmp_path, "repeats_trgt", dataset))

    # Before: S1's calls were replaced, then S2's file failed the dataset with a ValueError.
    assert stores.repeats == {_uuid("S1"): 4, _uuid("S2"): 6}
    assert stores.deletes == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("s2_entry", "refused"),
    [
        # S2's entry holds S1's VCF: its column names S1.
        pytest.param(
            {"vcf": ("cnv/S2.vcf", _CNV_VCF.replace("Sample0", "S1"))},
            (VcfSampleColumnError, "CNV VCF of S2"),
            id="vcf-of-another-sample",
        ),
        pytest.param(
            {"vcf": ("cnv/S2.vcf", _CNV_VCF), "copy_number_bedgraph": ("cnv/S2.bedgraph", "track type=bedGraph\n")},
            (EmptyTrackFileError, "S2.bedgraph holds no record of the segments track"),
            id="signal-file-without-a-value",
        ),
    ],
)
async def test_a_later_samples_refused_hificnv_file_fails_the_dataset_before_any_track_is_written(
    cnv_stores: _Stores, tmp_path: Path, s2_entry: dict[str, tuple[str, str]], refused: tuple[type, str]
) -> None:
    stores = cnv_stores
    stores.track("S1", "segments", CNV_SOURCE, "S1.bedgraph", rows=7)
    before = stores.tracks()
    dataset = ManifestDataset(
        per_sample={
            "S1": {
                "vcf": _file(tmp_path, "cnv/S1.vcf", _CNV_VCF),
                "copy_number_bedgraph": _file(tmp_path, "cnv/S1.bedgraph", "chr1\t0\t1000\t2\nchr1\t1000\t2000\t1\n"),
            },
            "S2": {role: _file(tmp_path, name, content) for role, (name, content) in s2_entry.items()},
        }
    )
    error, message = refused

    with pytest.raises(error, match=message):
        await _run(_job(stores, tmp_path, "cnv", dataset))

    # Before: S1's copy-number track was replaced, then S2's file failed the dataset.
    _assert_untouched(stores, before)


@pytest.mark.asyncio
async def test_a_later_samples_wisecondorx_file_without_a_bin_fails_the_dataset_before_any_track_is_cleared(
    stores: _Stores, tmp_path: Path
) -> None:
    stores.track("S1", "coverage", "wisecondorx", "S1_bins.bed", rows=5)
    before = stores.tracks()
    bins = "chr\tstart\tend\tratio\tzscore\n1\t0\t1000\t0.1\t0.5\n"
    dataset = ManifestDataset(
        per_sample={
            "S1": {"bins": _file(tmp_path, "wcx/S1_bins.bed", bins)},
            "S2": {"bins": _file(tmp_path, "wcx/S2_bins.bed", "chr\tstart\tend\tratio\tzscore\n")},
        }
    )

    with pytest.raises(EmptyTrackFileError, match="S2_bins.bed holds no record of the coverage track"):
        await _run(_job(stores, tmp_path, "wisecondorx", dataset))

    # Before: S1's bins replaced its track, then S2's file failed the dataset.
    _assert_untouched(stores, before)


@pytest.mark.asyncio
async def test_a_later_samples_pcf_table_without_a_segment_fails_the_dataset_before_any_track_is_deleted(
    stores: _Stores, tmp_path: Path
) -> None:
    stores.track("S1", APCAD_PCF_TRACK_TYPE, APCAD_PCF_SOURCE, "S1_maternal.pcf", rows=3)
    before = stores.tracks()
    dataset = ManifestDataset(
        per_sample={
            "S1": {"maternal": _file(tmp_path, "pcf/S1_maternal.pcf", _PCF_HEADER + "S1,1,p,1,1000,10,0.5\n")},
            "S2": {"maternal": _file(tmp_path, "pcf/S2_maternal.pcf", _PCF_HEADER)},
        }
    )

    with pytest.raises(EmptyTrackFileError, match="S2_maternal.pcf holds no segment"):
        await _run(_job(stores, tmp_path, "pcf", dataset))

    # Before: S1's track was deleted and its table stored, then S2's failed the dataset.
    _assert_untouched(stores, before)


def _mito_job_writing_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sv_content: str | bytes
) -> tuple[family_package_datasets.DatasetImportJob, list[str]]:
    """A mito job for S1, its chrM VCF with a record and its chrM SV VCF ``sv_content``,
    whose every write fails the test; and the list of writes."""
    writes: list[str] = []

    async def must_not_write(*_args: Any, **_kwargs: Any) -> None:
        writes.append("write")
        raise AssertionError("nothing may be written for a refused mito dataset")

    async def known(*_args: Any, **_kwargs: Any) -> set[str]:
        return set(SAMPLES)

    monkeypatch.setattr(family_package_datasets, "known_vcf_sample_ids", known)
    for name in ("upload_family_small_variant_file", "lock_family_variant_writes", "rewrite_family_structural_variants"):
        monkeypatch.setattr(family_package_datasets, name, must_not_write)
    chrm = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
    dataset = ManifestDataset(
        per_sample={
            "S1": {
                "vcf": _file(tmp_path, "mito/S1.vcf", chrm + "chrM\t3243\t.\tA\tG\t30\tPASS\t.\tGT\t0/1\n"),
                "sv_vcf": _file(tmp_path, "mito/S1_sv.vcf", sv_content),
            }
        }
    )
    return _job(_Stores(), tmp_path, "mito", dataset), writes


@pytest.mark.asyncio
async def test_a_chrm_sv_file_whose_records_all_lie_off_chrm_fails_the_mito_dataset_before_anything_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sv_content = _MITO_SV_HEADER + _MITO_DELETION.replace("chrM", "chr1")
    job, writes = _mito_job_writing_nothing(tmp_path, monkeypatch, sv_content)

    with pytest.raises(RuntimeError, match=r"the chrM SV VCF of S1 \(S1_sv.vcf\) holds 1 record\(s\), none on chrM"):
        await _run(job)

    # Before: the chrM calls were written, and the file read as one without a call removed
    # the sample's chrM SVs.
    assert writes == []


@pytest.mark.asyncio
async def test_a_chrm_sv_file_that_cannot_be_read_fails_the_mito_dataset_before_its_chrm_calls_are_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Its header reads, a byte of a record is no UTF-8.
    sv_content = (_MITO_SV_HEADER + _MITO_DELETION).encode().replace(b"PRECISE", b"PRECISE\xff")
    job, writes = _mito_job_writing_nothing(tmp_path, monkeypatch, sv_content)

    with pytest.raises(HTTPException, match="must be UTF-8 text"):
        await _run(job)

    # Before: the chrM calls were written, then the SV file failed the dataset.
    assert writes == []


@pytest.mark.asyncio
async def test_a_nipt_fathers_snv_file_without_a_record_fails_the_dataset_before_the_plasma_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writes: list[str] = []

    async def must_not_write(*_args: Any, **_kwargs: Any) -> None:
        writes.append("write")
        raise AssertionError("nothing may be written for a refused NIPT dataset")

    monkeypatch.setattr(family_package_datasets, "upload_family_small_variant_file", must_not_write)
    dataset = ManifestDataset(
        per_sample={
            "S1": {"vcf": _file(tmp_path, "S1.vcf", _SNV_HEADER.format(sample="S1") + "chr1\t100\t.\tA\tG\t30\tPASS\t.\tGT\t0/1\n")},
            "S2": {"vcf": _file(tmp_path, "S2.vcf", _SNV_HEADER.format(sample="S2"))},
        }
    )
    job = _job(_Stores(), tmp_path, "snv", dataset)
    # S1 is the maternal plasma, read first; S2 the father.
    job.bundle.manifest = PackageManifest(
        ped="family.ped",
        analysis_type=MONOGENIC_NIPT_ANALYSIS_TYPE,
        samples={"S1": {"assay": "nipt_cfdna"}},
        datasets={"snv": dataset},
    )

    with pytest.raises(RuntimeError, match=r"The NIPT SNV file of S2 \(S2.vcf\) holds no record"):
        await _run(job)

    # Before: the plasma's calls were written, then the father's file failed the dataset.
    assert writes == []


# An APCAD file per embryo and a coverage file per sample, checked as each was written until the
# rest of SAFE-3: every one is now checked before any sample's track is cleared.

_COVERAGE_BED = "chr1\t0\t1000\t30\nchr1\t1000\t2000\t32\n"
_TARGET_TABLE = "chromosome\tstart\tend\tattribute\tmean\n"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("s2_entry", "error"),
    [
        # Records, none read for S2: the file's one column is another tube's.
        pytest.param(
            {"vcf": ("apcad/S2.vcf", _apcad_vcf(["OTHER"], ["0/1:10,10"]))},
            "S2.vcf holds no APCAD value for S2",
            id="vcf-without-the-embryos-column",
        ),
        # No record at all: read as a BED, which holds none either.
        pytest.param({"vcf": ("apcad/S2.vcf", _apcad_vcf(["S2"]))}, "No valid BED records found", id="vcf-header-alone"),
        pytest.param(
            {"bed": ("apcad/S2.apcad.bed", "# chrom start end id value origin\n")},
            "S2.apcad.bed holds no APCAD value for S2",
            id="bed-without-a-record",
        ),
    ],
)
async def test_a_later_embryos_apcad_file_without_a_value_fails_the_dataset_before_any_track_is_cleared(
    stores: _Stores, tmp_path: Path, s2_entry: dict[str, tuple[str, str]], error: str
) -> None:
    stores.track("S1", "apcad", APCAD_SOURCE, "S1.old.vcf", rows=6)
    stores.track("S2", "apcad", APCAD_SOURCE, "S2.old.vcf", rows=4)
    before = stores.tracks()
    dataset = ManifestDataset(
        per_sample={
            "S1": {"vcf": _file(tmp_path, "apcad/S1.vcf", _apcad_vcf(["S1"], ["0/1:10,10"], ["0/1:12,8"]))},
            "S2": {role: _file(tmp_path, name, content) for role, (name, content) in s2_entry.items()},
        }
    )

    with pytest.raises((EmptyTrackFileError, HTTPException)) as refused:
        await _run(_job(stores, tmp_path, "apcad", dataset))

    assert error in str(getattr(refused.value, "detail", refused.value))
    # Before: S1's track was replaced by its file's two sites, then S2's file failed the
    # dataset.
    _assert_untouched(stores, before)


@pytest.mark.asyncio
async def test_every_embryos_apcad_file_checked_the_dataset_replaces_each_embryos_track(
    stores: _Stores, tmp_path: Path
) -> None:
    stores.track("S1", "apcad", APCAD_SOURCE, "S1.old.vcf", rows=6)
    stores.track("S2", "apcad", APCAD_SOURCE, "S2.old.vcf", rows=4)
    dataset = ManifestDataset(
        per_sample={
            "S1": {"vcf": _file(tmp_path, "apcad/S1.vcf", _apcad_vcf(["S1"], ["0/1:10,10"], ["0/1:12,8"]))},
            # A VCF without a record, read as a BED, is checked and stored as one.
            "S2": {"vcf": _file(tmp_path, "apcad/S2.vcf", "1\t100\t101\trs1\t0.5\tpaternal\n")},
        }
    )

    result = await _run(_job(stores, tmp_path, "apcad", dataset))

    assert result.status == "imported"
    assert stores.tracks() == (
        {(_uuid("S1"), "apcad", APCAD_SOURCE, "S1.vcf"): 2, (_uuid("S2"), "apcad", APCAD_SOURCE, "S2.vcf"): 1},
        {(_uuid("S1"), "apcad", APCAD_SOURCE, "S1.vcf"): 2, (_uuid("S2"), "apcad", APCAD_SOURCE, "S2.vcf"): 1},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "detail"),
    [
        pytest.param(b"track name=coverage\n", "No valid BED records found", id="no-record"),
        pytest.param(b"", "No valid BED records found", id="nothing"),
        # A byte that is no UTF-8 after the first records: the BED loader decodes the file whole.
        pytest.param(
            _COVERAGE_BED.encode() + b"chr1\t2000\t3000\t3\xff\n",
            "BED file must be UTF-8 text or gzipped UTF-8 text",
            id="not-utf-8",
        ),
        pytest.param(gzip.compress(_COVERAGE_BED.encode() * 50)[:-12], "BED file is not valid gzip", id="gzip-cut-short"),
    ],
)
async def test_a_later_samples_refused_coverage_bed_fails_the_dataset_before_any_track_is_cleared(
    stores: _Stores, tmp_path: Path, content: bytes, detail: str
) -> None:
    stores.track("S1", "coverage", BED_UPLOAD_SOURCE, "S1.old.bed", rows=5)
    stores.track("S2", "coverage", BED_UPLOAD_SOURCE, "S2.old.bed", rows=3)
    before = stores.tracks()
    dataset = ManifestDataset(
        per_sample={
            "S1": {"bed": _file(tmp_path, "coverage/S1.bed", _COVERAGE_BED), "content": "read_depth"},
            "S2": {"bed": _file(tmp_path, "coverage/S2.bed", content), "content": "read_depth"},
        }
    )

    with pytest.raises(HTTPException) as refused:
        await _run(_job(stores, tmp_path, "coverage", dataset))

    assert (refused.value.status_code, refused.value.detail) == (400, detail)
    # Before: S1's coverage was replaced by its BED's two bins, then S2's BED failed the
    # dataset.
    _assert_untouched(stores, before)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("table", "error"),
    [
        pytest.param(_TARGET_TABLE, "coverage_S2.txt holds no target", id="no-target"),
        pytest.param(
            "chromosome\tstart\tend\n1\t100\t200\n", "The coverage table has no column attribute, mean", id="no-mean"
        ),
    ],
)
async def test_a_later_samples_refused_coverage_table_fails_the_dataset_before_any_table_is_replaced(
    stores: _Stores, tmp_path: Path, table: str, error: str
) -> None:
    stores.track("S1", TARGET_COVERAGE_TRACK_TYPE, TARGET_COVERAGE_SOURCE, "coverage_S1.old.txt", rows=4)
    before = stores.tracks()
    dataset = ManifestDataset(
        per_sample={
            "S1": {"target_table": _file(tmp_path, "coverage_S1.txt", _TARGET_TABLE + "1\t100\t200\tGENE1\t80\n")},
            "S2": {"target_table": _file(tmp_path, "coverage_S2.txt", table)},
        }
    )

    with pytest.raises(RuntimeError, match=error):
        await _run(_job(stores, tmp_path, "coverage", dataset))

    # A monogenic NIPT pair's per-target tables. Before: the plasma's (S1's) table replaced
    # its track, then the father's failed the dataset.
    _assert_untouched(stores, before)


@pytest.mark.asyncio
async def test_every_samples_coverage_file_checked_the_dataset_replaces_each_samples_track(
    stores: _Stores, tmp_path: Path
) -> None:
    stores.track("S1", "coverage", BED_UPLOAD_SOURCE, "S1.old.bed", rows=5)
    stores.track("S2", TARGET_COVERAGE_TRACK_TYPE, TARGET_COVERAGE_SOURCE, "coverage_S2.old.txt", rows=4)
    dataset = ManifestDataset(
        per_sample={
            "S1": {"bed": _file(tmp_path, "coverage/S1.bed", _COVERAGE_BED), "content": "read_depth"},
            "S2": {"target_table": _file(tmp_path, "coverage_S2.txt", _TARGET_TABLE + "1\t100\t200\tGENE1\t80\n")},
        }
    )

    result = await _run(_job(stores, tmp_path, "coverage", dataset))

    assert result.status == "imported"
    expected = {
        (_uuid("S1"), "coverage", BED_UPLOAD_SOURCE, "S1.bed"): 2,
        (_uuid("S2"), TARGET_COVERAGE_TRACK_TYPE, TARGET_COVERAGE_SOURCE, "coverage_S2.txt"): 1,
    }
    assert stores.tracks() == (expected, expected)


@pytest.mark.asyncio
async def test_an_update_checks_no_file_of_a_coverage_track_it_leaves_as_stored(stores: _Stores, tmp_path: Path) -> None:
    stores.track("S1", "coverage", BED_UPLOAD_SOURCE, "S1.old.bed", rows=5)
    dataset = ManifestDataset(
        per_sample={
            "S1": {"bed": _file(tmp_path, "coverage/S1.bed", "track name=coverage\n"), "content": "read_depth"},
            "S2": {"bed": _file(tmp_path, "coverage/S2.bed", _COVERAGE_BED), "content": "read_depth"},
        }
    )

    result = await _run(_job(stores, tmp_path, "coverage", dataset, conflict_mode="update"))

    # S1's coverage is stored, so the update leaves it and does not read its file; S2's is
    # imported.
    assert (result.status, result.message) == (
        "imported",
        "Imported coverage; skipped existing samples in update mode: S1",
    )
    expected = {
        (_uuid("S1"), "coverage", BED_UPLOAD_SOURCE, "S1.old.bed"): 5,
        (_uuid("S2"), "coverage", BED_UPLOAD_SOURCE, "S2.bed"): 2,
    }
    assert stores.tracks() == (expected, expected)


# --- SAFE-3: a dry run reports a data file without a record ---------------------------------------


def _validated(tmp_path: Path, dataset_type: str, dataset: ManifestDataset) -> list[tuple[str, str | None]]:
    """The warnings of a dataset's validation, as (code, sample)."""
    errors: list[Any] = []
    warnings: list[Any] = []
    _validate_dataset(
        root=tmp_path,
        dataset_type=dataset_type,
        dataset=dataset,
        ped_sample_ids=set(SAMPLES),
        errors=errors,
        warnings=warnings,
    )
    assert errors == []
    return [(warning.code, warning.sample_id) for warning in warnings]


_HEADER_ALONE = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS2\n"
_A_RECORD = "chr1\t100\t.\tA\tG\t30\tPASS\t.\tGT\t0/1\n"


@pytest.mark.parametrize(
    ("dataset_type", "entry", "extra"),
    [
        pytest.param("repeats_trgt", "file", {}, id="trgt"),
        pytest.param("sv_needlr", "vcf", {}, id="needlr"),
        pytest.param("mito", "vcf", {}, id="chrm-vcf"),
        pytest.param("snv", "vcf", {"source_format": "clair3"}, id="long-read-snv"),
    ],
)
def test_a_dry_run_warns_of_a_samples_data_file_without_a_record(
    tmp_path: Path, dataset_type: str, entry: str, extra: dict[str, str]
) -> None:
    dataset = ManifestDataset(
        per_sample={
            "S1": {entry: _file(tmp_path, f"{dataset_type}/S1.vcf", _HEADER_ALONE.replace("S2", "S1") + _A_RECORD)},
            "S2": {entry: _file(tmp_path, f"{dataset_type}/S2.vcf", _HEADER_ALONE)},
        },
        **extra,
    )

    # Before: no warning; the import then failed the dataset.
    assert _validated(tmp_path, dataset_type, dataset) == [("dataset_file_without_record", "S2")]


@pytest.mark.parametrize("dataset_type", ["snv", "sv_needlr", "repeats_trgt"])
def test_a_dry_run_warns_of_a_family_vcf_without_a_record(tmp_path: Path, dataset_type: str) -> None:
    dataset = ManifestDataset(family_vcf=_file(tmp_path, "family.vcf", _HEADER_ALONE))

    assert _validated(tmp_path, dataset_type, dataset) == [("dataset_file_without_record", None)]


def test_a_dry_run_reads_a_compressed_file_up_to_its_first_record(tmp_path: Path) -> None:
    import gzip

    header = gzip.compress(_HEADER_ALONE.encode())
    with_record = gzip.compress((_HEADER_ALONE + _A_RECORD).encode())
    dataset = ManifestDataset(
        per_sample={
            "S1": {"file": _file(tmp_path, "trgt/S1.vcf.gz", with_record)},
            "S2": {"file": _file(tmp_path, "trgt/S2.vcf.gz", header)},
        }
    )

    assert _validated(tmp_path, "repeats_trgt", dataset) == [("dataset_file_without_record", "S2")]


def test_a_dry_run_does_not_warn_of_a_result_without_a_call(tmp_path: Path) -> None:
    """A HiFiCNV VCF or a chrM SV VCF without a call is the caller's answer, and imports;
    a file the dry run cannot read is left to the import, which says what is wrong."""
    cnv = ManifestDataset(per_sample={"S2": {"vcf": _file(tmp_path, "cnv/S2.vcf", _HEADER_ALONE)}})
    mito = ManifestDataset(
        per_sample={
            "S2": {
                "vcf": _file(tmp_path, "mito/S2.vcf", _HEADER_ALONE + _A_RECORD),
                "sv_vcf": _file(tmp_path, "mito/S2_sv.vcf", _HEADER_ALONE),
            }
        }
    )
    not_gzip = ManifestDataset(per_sample={"S2": {"file": _file(tmp_path, "trgt/S2.vcf.gz", "not gzip")}})

    assert _validated(tmp_path, "cnv", cnv) == []
    assert _validated(tmp_path, "mito", mito) == []
    assert _validated(tmp_path, "repeats_trgt", not_gzip) == []


@pytest.mark.parametrize(
    ("dataset_type", "role", "s1", "s2"),
    [
        pytest.param(
            "apcad",
            "vcf",
            ("S1.vcf", _HEADER_ALONE.replace("S2", "S1") + _A_RECORD),
            ("S2.vcf", _HEADER_ALONE),
            id="apcad-vcf",
        ),
        pytest.param(
            "apcad",
            "bed",
            ("S1.apcad.bed", "1\t100\t101\trs1\t0.5\tpaternal\n"),
            ("S2.apcad.bed", "# chrom\tstart\tend\tid\tvalue\torigin\n"),
            id="apcad-bed",
        ),
        pytest.param("coverage", "bed", ("S1.bed", _COVERAGE_BED), ("S2.bed", ""), id="coverage-bed"),
    ],
)
def test_a_dry_run_warns_of_an_embryos_apcad_file_or_a_samples_coverage_bed_without_a_record(
    tmp_path: Path, dataset_type: str, role: str, s1: tuple[str, str], s2: tuple[str, str]
) -> None:
    declared = {"content": "read_depth"} if dataset_type == "coverage" else {}
    dataset = ManifestDataset(
        per_sample={
            sample: {role: _file(tmp_path, f"{dataset_type}/{name}", content), **declared}
            for sample, (name, content) in (("S1", s1), ("S2", s2))
        }
    )

    # Before: no warning; the import then failed the dataset.
    assert _validated(tmp_path, dataset_type, dataset) == [("dataset_file_without_record", "S2")]


def test_a_dry_run_warns_of_an_apcad_family_vcf_without_a_record(tmp_path: Path) -> None:
    dataset = ManifestDataset(family_vcf=_file(tmp_path, "apcad/family.vcf", _HEADER_ALONE))

    assert _validated(tmp_path, "apcad", dataset) == [("dataset_file_without_record", None)]


# --- CLIN-39: a file whose every call states no extent is no answer that none was called ---------
#
# A deletion, duplication, inversion or CNV that states neither END nor SVLEN is left out and
# counted (the owner's decision, 2026-10-27). A HiFiCNV or chrM SV file whose every call is so is
# then no file without a call (which replaces the sample's earlier calls with none): it fails the
# dataset before anything is written, saying why, and so does such a NeedlR file.

_EXTENT_REFUSAL = (
    "1 record(s) of a deletion, duplication, inversion or CNV state neither END nor SVLEN, so how far "
    "they reach is not known, and were left out"
)


@pytest.mark.asyncio
async def test_a_chrm_sv_file_whose_calls_state_no_extent_fails_the_mito_dataset_before_anything_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sv_content = _MITO_SV_HEADER + "chrM\t8470\tS.1\tN\t<DEL>\t60\tPASS\tSVTYPE=DEL;VAF=0.05\tGT:GQ:DR:DV\t0/1:60:380:20\n"
    job, writes = _mito_job_writing_nothing(tmp_path, monkeypatch, sv_content)

    with pytest.raises(RuntimeError) as refused:
        await _run(job)

    # Before: stored on its POS (it was not left out yet); left out, the file read as "no call".
    assert str(refused.value) == (
        "No mitochondrial call was replaced: the chrM SV VCF of S1 (S1_sv.vcf) holds no call it can read: "
        + _EXTENT_REFUSAL
    )
    assert writes == []


@pytest.mark.asyncio
async def test_a_hificnv_vcf_whose_calls_state_no_extent_fails_the_dataset_before_any_track_is_written(
    cnv_stores: _Stores, tmp_path: Path
) -> None:
    stores = cnv_stores
    stores.track("S1", "segments", CNV_SOURCE, "S1.bedgraph", rows=7)
    before = stores.tracks()
    vcf = _CNV_VCF.replace("SVTYPE=DEL;END=2000", "SVTYPE=DEL")
    dataset = ManifestDataset(
        per_sample={
            "S1": {
                "vcf": _file(tmp_path, "cnv/S1.vcf", vcf),
                "copy_number_bedgraph": _file(tmp_path, "cnv/S1.bedgraph", "chr1\t0\t1000\t2\n"),
            }
        }
    )

    with pytest.raises(RuntimeError) as refused:
        await _run(_job(stores, tmp_path, "cnv", dataset))

    assert str(refused.value) == (
        "No HiFiCNV call was replaced: the CNV VCF of S1 (S1.vcf) holds no call it can read: " + _EXTENT_REFUSAL
    )
    _assert_untouched(stores, before)


@pytest.mark.asyncio
async def test_a_needlr_file_whose_calls_state_no_extent_says_why_it_holds_none(
    stores: _Stores, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def nothing(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(family_package_datasets, "lock_family_variant_writes", nothing)
    record = "1\t1000\tsv1\tN\t<DEL>\t60\tPASS\tSVTYPE=DEL;Query_ID=S1_sv_phased;Genotype=0/1\n"
    dataset = ManifestDataset(per_sample={"S1": {"vcf": _file(tmp_path, "sv/S1.needlr.vcf", _NEEDLR_HEADER + record)}})

    with pytest.raises(RuntimeError) as refused:
        await _run(_job(stores, tmp_path, "sv_needlr", dataset))

    assert str(refused.value) == (
        "The NeedlR file of S1 (S1.needlr.vcf) holds no structural variant: "
        + _EXTENT_REFUSAL
        + "; no NeedlR call was replaced"
    )


# --- DATA-7: a Paraphase copy number that no Postgres integer column holds -----------------------
#
# The copy numbers are Postgres ``integer`` columns (``total_cn``, ``gene_cn``,
# ``highest_total_cn``) filled from the sample's JSON. One beyond them failed the insert with a
# database error, after the sample's results were deleted. Such a file is refused as a TRGT
# value that cannot be read as a number is (#985): before any sample's results are replaced,
# the dataset failed with a message that names where the value is, never the value.


def _paraphase_json(**smn1: Any) -> str:
    """A sample's Paraphase JSON: GBA's result, and SMN1's with ``smn1`` over its values."""
    result = {"total_cn": 2, "gene_cn": 2, "highest_total_cn": 2, "sample_sex": "female", "genome_depth": 30.5}
    return json.dumps({"GBA": dict(result), "SMN1": {**result, **smn1}})


def _paraphase_refusal(sample_id: str, where: str) -> str:
    return (
        f"Paraphase JSON for {sample_id} holds a value that cannot be read where a number is expected "
        f"({where}); nothing was stored"
    )


# Each beyond Postgres's ``integer`` (-2,147,483,648 to 2,147,483,647), however the JSON writes it.
_COPY_NUMBERS_NO_INTEGER_HOLDS = [
    pytest.param({"total_cn": 2**31}, "total_cn", id="total-cn-one-above"),
    pytest.param({"gene_cn": -(2**31) - 1}, "gene_cn", id="gene-cn-one-below"),
    pytest.param({"highest_total_cn": "99999999999"}, "highest_total_cn", id="highest-total-cn-as-text"),
    pytest.param({"total_cn": 1e10}, "total_cn", id="total-cn-as-a-float"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("smn1", "field"), _COPY_NUMBERS_NO_INTEGER_HOLDS)
async def test_a_paraphase_copy_number_no_integer_column_holds_fails_the_dataset_with_the_results_as_they_were(
    stores: _Stores, tmp_path: Path, smn1: dict[str, Any], field: str
) -> None:
    stores.paraphase[_uuid("S1")] = 3
    dataset = ManifestDataset(per_sample={"S1": {"json": _file(tmp_path, "paraphase/S1.json", _paraphase_json(**smn1))}})

    with pytest.raises(RuntimeError) as refused:
        await _run(_job(stores, tmp_path, "paraphase", dataset))

    # Before: the sample's results deleted, then the insert failed with a database error.
    assert str(refused.value) == _paraphase_refusal("S1", f"{field} of SMN1")
    assert stores.paraphase == {_uuid("S1"): 3}
    assert stores.deletes == []
    assert stores.commits == 0


@pytest.mark.asyncio
async def test_a_later_samples_paraphase_copy_number_no_integer_column_holds_fails_the_dataset_with_every_samples_results(
    stores: _Stores, tmp_path: Path
) -> None:
    stores.paraphase = {_uuid("S1"): 3, _uuid("S2"): 4}
    dataset = ManifestDataset(
        per_sample={
            "S1": {"json": _file(tmp_path, "paraphase/S1.json", _paraphase_json())},
            "S2": {"json": _file(tmp_path, "paraphase/S2.json", _paraphase_json(total_cn=2**31))},
        }
    )

    with pytest.raises(RuntimeError) as refused:
        await _run(_job(stores, tmp_path, "paraphase", dataset))

    # Before: S1's results were replaced and committed, then S2's insert failed with a
    # database error.
    assert str(refused.value) == _paraphase_refusal("S2", "total_cn of SMN1")
    assert stores.paraphase == {_uuid("S1"): 3, _uuid("S2"): 4}
    assert stores.deletes == []
    assert stores.commits == 0


@pytest.mark.asyncio
async def test_a_paraphase_copy_number_at_the_integer_columns_bounds_is_stored_as_the_file_has_it(
    stores: _Stores, tmp_path: Path
) -> None:
    stores.paraphase = {_uuid("S1"): 3, _uuid("S2"): 4}
    dataset = ManifestDataset(
        per_sample={
            "S1": {"json": _file(tmp_path, "paraphase/S1.json", _paraphase_json(total_cn=2**31 - 1))},
            "S2": {"json": _file(tmp_path, "paraphase/S2.json", _paraphase_json(gene_cn=-(2**31)))},
        }
    )

    outcome = await _run(_job(stores, tmp_path, "paraphase", dataset))

    assert outcome.status == "imported"
    assert stores.paraphase == {_uuid("S1"): 2, _uuid("S2"): 2}
    smn1 = {row["sample_id"]: row for row in stores.paraphase_rows if row["gene_symbol"] == "SMN1"}
    assert (smn1[_uuid("S1")]["total_cn"], smn1[_uuid("S2")]["gene_cn"]) == (2**31 - 1, -(2**31))
