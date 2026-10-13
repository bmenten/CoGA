"""A track loader replaces only its own source's track of a sample (SAFE-22).

A sample can carry several callers' tracks of one type, each under its own source, and the
track viewers draw one track per caller: coverage from a BED (``web``: a BED upload, or a
package's ``coverage`` dataset), WisecondorX, QDNAseq and HiFiCNV; segments from the same
callers; on the ``apcad`` track a package's APCAD (``apcad``), HiFiCNV's minor allele
fraction (``hificnv``) and a BED upload's (``web``); on ``apcad_pcf`` a package's PCF
tables (``pcf``) and a BED upload's.

``upload_bed_data`` (the BED upload, and through it a package's coverage BED) deleted, with
overwrite, every source's track of its type, rows and records, and so did the APCAD import.
A package with WisecondorX or QDNAseq bins and a coverage BED for one sample lost the bins,
which are imported first, and an APCAD import removed HiFiCNV's minor allele fraction. A
BED upload with overwrite deleted the tracks before it read the file: an invalid file
answered 400 with the rows gone. Its 409 counted every source's records, so an upload was
refused for another caller's track, and an update of the coverage or APCAD dataset skipped
a sample for another caller's rows.

The stores are faked at their edge, as in test_import_update_skips_by_stored_rows.py: the
interval table answers the statements ``clickhouse_interval_tracks`` sends ClickHouse, and
the session the statements on ``sample_interval_track_sources``. The same runs through the
package import and the upload route against Postgres and ClickHouse in
e2e/test_e2e_track_loaders_keep_other_sources.py.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest
from fastapi import HTTPException, UploadFile

from backend.app.schemas import FamilyImportDatasetSummary
from backend.app.services import (
    bed_service,
    clickhouse_interval_tracks,
    family_package_datasets,
    family_package_registration as registration,
    variant_upload_service,
)
from backend.app.services import family_package_common
from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext
from backend.app.services.family_package_common import (
    APCAD_PCF_SOURCE,
    CNV_SOURCE,
    FamilyPackageBundle,
    ManifestDataset,
    PackageManifest,
    ParsedPed,
)

FAMILY = "FAM022"
SAMPLES = ("S1", "S2")
# The sources the two loaders write, as stored and as the track availability serves them.
WEB = "web"  # bed_service.BED_UPLOAD_SOURCE: a BED upload's, and a package's coverage BED's
APCAD_SOURCE = "apcad"  # family_package_common.APCAD_SOURCE: the apcad dataset's
_BED = "1\t0\t1000\t0.1\n1\t1000\t2000\t-0.2\n1\t2000\t3000\t0.05\n"
# A per-sample APCAD file as the importer's interval reader takes it: chromosome, start,
# end, id, the alternate-allele fraction and the parent of origin.
_APCAD = "1\t99\t100\trs1\t0.48\tpaternal\n1\t199\t200\trs2\t0.52\tmaternal\n"
# Each other caller's tracks of a sample, as the package's loaders store them: (track type,
# source, rows).
_OTHER_CALLERS = (
    ("coverage", "wisecondorx", 5),
    ("coverage", "qdnaseq", 4),
    ("coverage", CNV_SOURCE, 6),
    ("segments", "wisecondorx", 2),
    ("segments", CNV_SOURCE, 3),
    ("apcad", APCAD_SOURCE, 7),
    ("apcad", CNV_SOURCE, 8),
    ("apcad_pcf", APCAD_PCF_SOURCE, 2),
)


def _uuid(sample_id: str) -> str:
    return f"uuid-{sample_id}"


# --- the two stores of a track ------------------------------------------------------------


def _matches(key: tuple[str, str, str], params: Mapping[str, Any]) -> bool:
    """Whether a (sample uuid, track type, source) key is one a statement's bound filters
    pick: by sample, track type and source, each when bound (one family here)."""
    assert params.get("filename") is None, "a delete of one file's rows is not modelled"
    sample, track_type, source = key
    return all(
        params.get(name) in (None, value)
        for name, value in (("sample_uuid", sample), ("track_type", track_type), ("source", source))
    )


class _Stores:
    """Where a track is: its rows in ClickHouse's interval table (``rows``) and Postgres's
    record of it (``records``: ``sample_interval_track_sources``), each a row count by
    (sample uuid, track type, source). ``deletes`` lists each delete sent to either."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str], int] = {}
        self.records: dict[tuple[str, str, str], int] = {}
        self.deletes: list[tuple[str, dict[str, Any]]] = []

    def track(self, sample_id: str, track_type: str, source: str, *, rows: int, recorded: bool = True) -> None:
        key = (_uuid(sample_id), track_type, source)
        if rows:
            self.rows[key] = rows
        if recorded:
            self.records[key] = rows or 3

    def other_callers(self, sample_id: str) -> None:
        for track_type, source, rows in _OTHER_CALLERS:
            self.track(sample_id, track_type, source, rows=rows)

    def of(self, sample_id: str) -> tuple[dict[tuple[str, str], int], dict[tuple[str, str], int]]:
        """A sample's tracks: its rows and its records, by (track type, source)."""
        sample = _uuid(sample_id)
        return (
            {key[1:]: count for key, count in self.rows.items() if key[0] == sample},
            {key[1:]: count for key, count in self.records.items() if key[0] == sample},
        )

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
            self.deletes.append(("rows", dict(parameters)))
            for key in [key for key in self.rows if _matches(key, parameters)]:
                del self.rows[key]
            return None
        if sql.startswith("SELECT count()"):
            return [(sum(count for key, count in self.rows.items() if _matches(key, parameters)),)]
        raise AssertionError(f"unmodelled ClickHouse statement: {sql}")


class _Session:
    """Postgres's records of the tracks, as the loaders address them."""

    def __init__(self, stores: _Stores) -> None:
        self.stores = stores
        self.commits = 0

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
        sql = " ".join(str(statement).split())
        params = params or {}
        records = self.stores.records
        assert "sample_interval_track_sources" in sql, f"unmodelled statement: {sql}"
        if sql.startswith("SELECT COALESCE(SUM(row_count), 0)"):
            return _scalar(sum(n for key, n in records.items() if _matches(key, params)))
        if sql.startswith("DELETE FROM"):
            self.stores.deletes.append(("records", dict(params)))
            for key in [key for key in records if _matches(key, params)]:
                del records[key]
            return _scalar(None)
        assert sql.startswith("INSERT INTO"), sql
        records[(params["sample_id"], params["track_type"], params["source"])] = params["row_count"]
        return _scalar(None)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        return None


def _scalar(value: Any) -> SimpleNamespace:
    return SimpleNamespace(scalar_one=lambda: value)


@pytest.fixture
def stores(monkeypatch: pytest.MonkeyPatch) -> _Stores:
    state = _Stores()
    monkeypatch.setattr(clickhouse_interval_tracks, "execute_clickhouse", state.clickhouse)
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


def _sample(sample_id: str = "S1") -> SampleMetadataContext:
    return registration._family_sample_contexts(_family_context())[sample_id]


def _upload(text: str | bytes, name: str = "track.bed") -> UploadFile:
    data = text.encode() if isinstance(text, str) else text
    return UploadFile(file=BytesIO(data), filename=name)


async def _upload_bed(stores: _Stores, bed_type: str, text: str | bytes, *, overwrite: bool) -> dict[str, int]:
    return await bed_service.upload_bed_data(
        _Session(stores),  # type: ignore[arg-type]
        sample_context=_sample(),
        bed_type=bed_type,
        file=_upload(text),
        overwrite=overwrite,
    )


def test_each_loader_names_the_source_it_stores_and_replaces() -> None:
    assert bed_service.BED_UPLOAD_SOURCE == WEB
    assert family_package_common.APCAD_SOURCE == APCAD_SOURCE


# --- the BED upload ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("bed_type", sorted(bed_service.VALID_BED_TYPES))
async def test_a_bed_upload_with_overwrite_replaces_only_the_track_a_bed_brought(
    stores: _Stores, bed_type: str
) -> None:
    stores.track("S1", bed_type, WEB, rows=9)
    stores.other_callers("S1")
    others = {key: count for key, count in stores.of("S1")[0].items() if key != (bed_type, WEB)}
    text = _APCAD if bed_type in {"apcad", "apcad_pcf"} else _BED

    result = await _upload_bed(stores, bed_type, text, overwrite=True)

    rows, records = stores.of("S1")
    expected_rows = 2 if bed_type in {"apcad", "apcad_pcf"} else 3
    assert result == {"inserted": expected_rows}
    # Before: every source's track of the type went, rows and records (the coverage of
    # WisecondorX, QDNAseq and HiFiCNV; HiFiCNV's minor allele fraction beside the APCAD).
    assert rows == {**others, (bed_type, WEB): expected_rows}
    assert records == {**others, (bed_type, WEB): expected_rows}
    assert all(params.get("source") == WEB for _store, params in stores.deletes)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        pytest.param("track\tname\tnot\ta record\n# a comment\n", id="no-record"),
        pytest.param(b"\xff\xfe\x00\x81 not text", id="not-text"),
    ],
)
async def test_an_invalid_bed_upload_with_overwrite_leaves_the_stored_tracks_and_their_records(
    stores: _Stores, text: str | bytes
) -> None:
    stores.track("S1", "coverage", WEB, rows=3)
    stores.other_callers("S1")
    before = stores.of("S1")

    with pytest.raises(HTTPException) as refused:
        await _upload_bed(stores, "coverage", text, overwrite=True)

    assert refused.value.status_code == 400
    # Read and checked before anything is replaced. Before: the rows of every source's
    # coverage were deleted first, and the 400 rolled back only Postgres's delete of their
    # records, which then named rows that were gone.
    assert stores.deletes == []
    assert stores.of("S1") == before


@pytest.mark.asyncio
@pytest.mark.parametrize("bed_type", sorted(bed_service.VALID_BED_TYPES))
async def test_a_bed_upload_is_not_refused_for_another_callers_track(stores: _Stores, bed_type: str) -> None:
    stores.other_callers("S1")
    before = stores.of("S1")[0]
    text = _APCAD if bed_type in {"apcad", "apcad_pcf"} else _BED

    # Before: 409 "BED data already exists for this sample", for the other callers' records.
    result = await _upload_bed(stores, bed_type, text, overwrite=False)

    rows, _records = stores.of("S1")
    assert result["inserted"] == rows[(bed_type, WEB)]
    assert {key: count for key, count in rows.items() if key != (bed_type, WEB)} == before
    assert [store for store, _params in stores.deletes if store == "rows"] == []


@pytest.mark.asyncio
async def test_a_bed_upload_without_overwrite_is_refused_while_the_rows_a_bed_brought_are_stored(
    stores: _Stores,
) -> None:
    # Rows a BED brought, without their record (an upload stopped between its rows and its
    # commit). Before: Postgres's records were read, none named this track, and the file's
    # rows were added to the stored ones, one track of two files.
    stores.track("S1", "coverage", WEB, rows=3, recorded=False)

    with pytest.raises(HTTPException) as refused:
        await _upload_bed(stores, "coverage", _BED, overwrite=False)

    assert refused.value.status_code == 409
    assert stores.rows == {(_uuid("S1"), "coverage", WEB): 3}
    assert stores.deletes == []


@pytest.mark.asyncio
async def test_a_bed_upload_without_overwrite_replaces_a_record_whose_rows_are_gone(stores: _Stores) -> None:
    # Postgres records a track a BED brought whose rows a failed overwrite's restore deleted:
    # the upload reads the store, as an update does (SAFE-20), and its record replaces the
    # stale one rather than standing beside it.
    stores.track("S1", "coverage", WEB, rows=0)
    stores.records[(_uuid("S1"), "coverage", WEB)] = 40
    stores.track("S1", "coverage", "wisecondorx", rows=5)

    await _upload_bed(stores, "coverage", _BED, overwrite=False)

    rows, records = stores.of("S1")
    assert rows == {("coverage", WEB): 3, ("coverage", "wisecondorx"): 5}
    assert records == {("coverage", WEB): 3, ("coverage", "wisecondorx"): 5}


# --- the package's track datasets -------------------------------------------------------------


def _file(root: Path, name: str, text: str) -> str:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return name


def _job(
    stores: _Stores,
    root: Path,
    dataset_type: str,
    dataset: ManifestDataset,
    *,
    conflict_mode: str,
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
        sample_contexts=registration._family_sample_contexts(family),
        conflict_mode=conflict_mode,
    )


async def _run(job: family_package_datasets.DatasetImportJob) -> FamilyImportDatasetSummary:
    return await family_package_datasets.DATASET_IMPORTERS[job.summary.dataset_type](job)


@pytest.mark.asyncio
@pytest.mark.parametrize("conflict_mode", ["cancel", "overwrite"])
async def test_a_coverage_bed_keeps_the_other_callers_coverage(
    stores: _Stores, tmp_path: Path, conflict_mode: str
) -> None:
    # WisecondorX and QDNAseq import their bins before coverage; HiFiCNV's depth is from an
    # import before. The coverage BED replaces the BED's coverage alone.
    stores.track("S1", "coverage", WEB, rows=9)
    stores.other_callers("S1")
    others = {key: count for key, count in stores.of("S1")[0].items() if key != ("coverage", WEB)}
    dataset = ManifestDataset(per_sample={"S1": {"bed": _file(tmp_path, "coverage/S1.bed", _BED)}})

    result = await _run(_job(stores, tmp_path, "coverage", dataset, conflict_mode=conflict_mode))

    assert result.status == "imported"
    rows, records = stores.of("S1")
    # Before: only the BED's coverage was left, its 3 rows.
    assert rows == {**others, ("coverage", WEB): 3}
    assert records == {**others, ("coverage", WEB): 3}


@pytest.mark.asyncio
async def test_an_update_imports_a_coverage_bed_beside_another_callers_coverage(
    stores: _Stores, tmp_path: Path
) -> None:
    stores.track("S1", "coverage", "wisecondorx", rows=5)  # S1: another caller's coverage
    stores.track("S2", "coverage", WEB, rows=2)  # S2: the BED's coverage is stored
    dataset = ManifestDataset(
        per_sample={sample: {"bed": _file(tmp_path, f"coverage/{sample}.bed", _BED)} for sample in SAMPLES}
    )
    job = _job(stores, tmp_path, "coverage", dataset, conflict_mode="update")

    result = await _run(job)

    # Before: S1 skipped as well, "coverage already exists", for the WisecondorX bins, and the
    # dataset ended skipped with the BED's coverage never imported.
    assert (result.status, result.message) == (
        "imported",
        "Imported coverage; skipped existing samples in update mode: S2",
    )
    assert stores.of("S1")[0] == {("coverage", "wisecondorx"): 5, ("coverage", WEB): 3}
    assert stores.of("S2")[0] == {("coverage", WEB): 2}
    assert job.skipped_samples == {"S2"}


def _apcad_dataset(root: Path) -> ManifestDataset:
    return ManifestDataset(per_sample={"S1": {"bed": _file(root, "apcad/S1.apcad.bed", _APCAD)}})


@pytest.mark.asyncio
@pytest.mark.parametrize("conflict_mode", ["cancel", "overwrite"])
async def test_an_apcad_import_keeps_hificnvs_minor_allele_fraction(
    stores: _Stores, tmp_path: Path, conflict_mode: str
) -> None:
    stores.other_callers("S1")
    stores.track("S1", "apcad", WEB, rows=4)  # a BED upload's APCAD
    others = {key: count for key, count in stores.of("S1")[0].items() if key != ("apcad", APCAD_SOURCE)}

    result = await _run(_job(stores, tmp_path, "apcad", _apcad_dataset(tmp_path), conflict_mode=conflict_mode))

    assert result.status == "imported"
    rows, records = stores.of("S1")
    # Before: HiFiCNV's minor allele fraction and the uploaded APCAD went with the old APCAD.
    assert rows == {**others, ("apcad", APCAD_SOURCE): 2}
    assert records == {**others, ("apcad", APCAD_SOURCE): 2}


@pytest.mark.asyncio
async def test_an_apcad_family_vcf_keeps_hificnvs_minor_allele_fraction(stores: _Stores, tmp_path: Path) -> None:
    stores.track("S1", "apcad", APCAD_SOURCE, rows=7)
    stores.track("S1", "apcad", CNV_SOURCE, rows=8)
    vcf = (
        "##fileformat=VCFv4.2\n"
        '##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allelic depths">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\tS2\n"
        "1\t100\t.\tA\tG\t50\tPASS\t.\tGT:AD\t0/1:10,10\t0/0:20,0\n"
    )
    dataset = ManifestDataset(family_vcf=_file(tmp_path, "apcad/family.vcf", vcf))

    result = await _run(_job(stores, tmp_path, "apcad", dataset, conflict_mode="overwrite"))

    assert result.status == "imported"
    assert stores.of("S1")[0] == {("apcad", APCAD_SOURCE): 1, ("apcad", CNV_SOURCE): 8}
    assert stores.of("S2")[0] == {("apcad", APCAD_SOURCE): 1}


@pytest.mark.asyncio
async def test_an_update_imports_apcad_beside_hificnvs_minor_allele_fraction(
    stores: _Stores, tmp_path: Path
) -> None:
    stores.track("S1", "apcad", CNV_SOURCE, rows=8)
    job = _job(stores, tmp_path, "apcad", _apcad_dataset(tmp_path), conflict_mode="update")

    result = await _run(job)

    # Before: skipped, "APCAD tracks already exist", for HiFiCNV's minor allele fraction.
    assert (result.status, result.message) == ("imported", "Imported APCAD data into interval tracks")
    assert stores.of("S1")[0] == {("apcad", CNV_SOURCE): 8, ("apcad", APCAD_SOURCE): 2}
    assert job.skipped_samples == set()

    # Its own track stored, an update skips it.
    job = _job(stores, tmp_path, "apcad", _apcad_dataset(tmp_path), conflict_mode="update")
    result = await _run(job)
    assert result.status == "skipped"
    assert job.skipped_samples == {"S1"}


@pytest.mark.asyncio
async def test_an_apcad_file_read_as_a_bed_is_stored_as_the_datasets_own_track(
    stores: _Stores, tmp_path: Path
) -> None:
    # A per-sample file named as a VCF whose lines are BED records: the importer's VCF reader
    # finds no record, and the file is read as a BED, into the dataset's track, a BED
    # upload's APCAD left alone. Before: stored as a BED upload's (web), replacing it.
    stores.track("S1", "apcad", WEB, rows=4)
    stores.track("S1", "apcad", CNV_SOURCE, rows=8)
    dataset = ManifestDataset(per_sample={"S1": {"vcf": _file(tmp_path, "apcad/S1.vcf", _APCAD)}})

    result = await _run(_job(stores, tmp_path, "apcad", dataset, conflict_mode="overwrite"))

    assert result.status == "imported"
    assert stores.of("S1")[0] == {("apcad", WEB): 4, ("apcad", CNV_SOURCE): 8, ("apcad", APCAD_SOURCE): 2}


# --- the imputed genotypes' haplotype blocks ----------------------------------------------------


@pytest.mark.asyncio
async def test_the_imputed_upload_deletes_only_its_own_haplotype_blocks(stores: _Stores) -> None:
    # Only the imputed small-variant loader writes haplotype blocks (source glimpse2), and a
    # BED upload cannot: its deletes now name its source, as every track loader's do.
    stores.track("S1", "haplotype", "glimpse2", rows=6)
    stores.track("S1", "haplotype", "another_phaser", rows=2)

    await variant_upload_service._delete_family_haplotype_blocks(
        _Session(stores),  # type: ignore[arg-type]
        assembly_name="GRCh38",
        family_uuid="family-uuid",
    )

    assert stores.of("S1") == ({("haplotype", "another_phaser"): 2}, {("haplotype", "another_phaser"): 2})
