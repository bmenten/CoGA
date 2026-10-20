"""A rewrite of a family's structural variants puts back the rows it deleted when it fails
(SAFE-28).

ClickHouse has no transactions. ``rewrite_family_structural_variants`` deletes the family's SV
rows -- one source's, or every source's -- and writes them again, changed: the per-sample SV
upload (its caller's rows, the sample's calls swapped), the admin deletes of a sample's SVs
and of a sample with its data (every source's rows, without the sample's calls) and a
package's chrM SVs (the ``mito_sv`` rows, the calls of the samples it brings files for
swapped). An insert that failed after the delete left the family without those rows -- every
sample's, not only those the write changed -- and nothing marked it; in an update, the
import had no snapshot to put them back.

Now the rewrite writes back the rows it read and deleted, and raises the write's error. When
that fails too it raises ``StructuralVariantsNotRestored``, and the caller marks the family for
what may be missing: the SV upload's mark, written before its rewrite on a connection of its
own, stays, naming each sample whose calls of that caller the rewrite deleted; the admin
delete leaves a mark naming the family's SV datasets but the deleted sample's; the chrM SVs'
dataset is flagged for every sample whose calls they held, not only those the import named.

Against fakes at the stores' edge: ClickHouse's SV rows (written at once, kept whatever becomes
of the request), and the family row's import state in Postgres (a write is its transaction's
until it commits; the upload's mark commits at once on a connection of its own).
"""

from __future__ import annotations

import copy
from contextlib import asynccontextmanager
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import UploadFile
import pytest

from backend.app.routers import structural_variants as sv_router
from backend.app.services import (
    admin_service,
    annotation_manifest_service,
    clickhouse_variant_storage,
    family_package_datasets,
    family_package_import,
    family_package_registration,
    sv_gene_index_service,
    variant_ranking_cache,
    variant_upload_service,
)
from backend.app.services.clickhouse_variant_records import (
    StoredStructuralVariantRow,
    StructuralVariantCall,
    StructuralVariantRecord,
)
from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext

_SAMPLES = ("S1", "S2", "S3")
_FAMILY = FamilyMetadataContext(
    family_uuid="family-uuid",
    family_id="F1",
    project_ids=["project-1"],
    sample_rows=[
        {"sample_uuid": f"uuid-{sample}", "sample_id": sample, "sex": "female", "role": role}
        for sample, role in zip(_SAMPLES, ("proband", "mother", "father"))
    ],
    sample_uuid_to_name={f"uuid-{sample}": sample for sample in _SAMPLES},
    sample_name_to_uuid={sample: f"uuid-{sample}" for sample in _SAMPLES},
    affected_sample_names=["S1"],
    assembly_id="assembly-1",
    assembly_name="GRCh38",
)
_DEL_30000 = "1-30000-31000-DEL---"
_DEL_80000 = "1-80000-80800-DEL---"
_NEEDLR_DEL = "1-7000-12000-DEL---"
_MITO_DEL = "M-8470-13447-DEL---"


def _sample(sample_id: str) -> SampleMetadataContext:
    return SampleMetadataContext(
        sample_uuid=f"uuid-{sample_id}",
        sample_id=sample_id,
        family_uuid="family-uuid",
        family_id="F1",
        sex="female",
        project_ids=["project-1"],
        assembly_id="assembly-1",
        assembly_name="GRCh38",
    )


def _row(variant_id: str, calls: dict[str, str], *, source: str = "sniffles") -> StoredStructuralVariantRow:
    chrom, start, end, sv_type = variant_id.split("-")[:4]
    return StoredStructuralVariantRow(
        project_id="project-1",
        record=StructuralVariantRecord(
            variant_key=abs(hash((variant_id, source))) % 10_000,
            variant_id=variant_id,
            chr=chrom,
            start=int(start),
            end=int(end),
            sv_type=sv_type,
            source=source,
            remote_chr=None,
            remote_start=None,
            remote_end=None,
            sv_len=int(start) - int(end),
            filters=["PASS"],
            gene_symbols=[],
            annotations=[{"sample": sample} for sample in calls] if source == "mito_sv" else [],
            calls=[
                StructuralVariantCall(sample=sample, gt=gt, qual=40.0, read_support=9, filter="PASS")
                for sample, gt in calls.items()
            ],
        ),
    )


# The family's Sniffles calls before an upload: S2 and S3 on one deletion, S1 on another.
_SNIFFLES = [_row(_DEL_30000, {"S2": "0/1", "S3": "1/1"}), _row(_DEL_80000, {"S1": "0/1"})]
_SNIFFLES_CALLS = {_DEL_30000: {"S2": "0/1", "S3": "1/1"}, _DEL_80000: {"S1": "0/1"}}

_SNIFFLES_HEADER = (
    "##fileformat=VCFv4.2\n"
    "##source=Sniffles2_2.2\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n"
)


def _sniffles_file() -> UploadFile:
    """One sample's Sniffles VCF: a call on the deletion S2 and S3 share."""
    body = _SNIFFLES_HEADER + (
        "1\t30000\tS.1\tN\t<DEL>\t40\tPASS\tSVTYPE=DEL;SVLEN=-1000;END=31000;SUPPORT=9\tGT\t0/1\n"
    )
    return UploadFile(file=BytesIO(body.encode()), filename="calls.sniffles.vcf")


class _ProcessStopped(BaseException):
    """The process running the write stops there (a restart, a crash, running out of
    memory): nothing after it runs, neither the rest of the request nor its clean-up."""


class _ClickHouse:
    """The family's SV rows, written at once and kept whatever becomes of the request.
    ``failing_inserts`` inserts fail before one works again; with ``stop``, the next insert
    stops the process. ``events`` is what was done, in order (shared with the mark's
    connection, which adds ``("mark", key)``)."""

    def __init__(self, rows: list[StoredStructuralVariantRow]) -> None:
        self.rows = copy.deepcopy(rows)
        self.events: list[tuple[Any, ...]] = []
        self.failing_inserts = 0
        self.stop = False

    async def fetch(self, _assembly, _family, *, source=None) -> list[StoredStructuralVariantRow]:
        self.events.append(("read", source))
        return [copy.deepcopy(row) for row in self.rows if source is None or row.record.source == source]

    async def delete(self, _assembly, _family, *, source=None) -> None:
        self.events.append(("delete", source))
        self.rows = [row for row in self.rows if source is not None and row.record.source != source]

    async def insert(self, _assembly, _family, project_ids, records) -> None:
        if self.stop:
            raise _ProcessStopped()
        if self.failing_inserts:
            self.failing_inserts -= 1
            self.events.append(("insert failed",))
            raise RuntimeError("ClickHouse insert failed")
        self.events.append(("insert", len(records)))
        self.rows.extend(
            StoredStructuralVariantRow(project_id=project_ids[0], record=copy.deepcopy(record)) for record in records
        )

    def calls(self, source: str = "sniffles") -> dict[str, dict[str, str]]:
        """Each stored SV of ``source`` with its calls (sample: genotype), by id."""
        out: dict[str, dict[str, str]] = {}
        for row in self.rows:
            if row.record.source == source:
                out.setdefault(row.record.variant_id, {}).update({call.sample: call.gt for call in row.record.calls})
        return out


class _Postgres:
    """The family row's import state as the writes read and write it (``flag``:
    import_incomplete, ``unfinished``: import_unfinished, ``imported``: imported_scopes),
    and its samples' metadata (empty). A write is the transaction's until it commits; a
    rollback discards it. ``connection()`` is the connection a single-file upload writes its
    mark on: committed at once."""

    def __init__(self, *, imported: Any = None, unfinished: Any = None, flag: Any = None) -> None:
        self.committed: dict[str, Any] = {"flag": flag, "unfinished": unfinished, "imported": imported}
        self.working = copy.deepcopy(self.committed)
        self.commits = 0
        self.events: list[tuple[Any, ...]] | None = None
        self.unavailable: str | None = None  # SQL that fails, as a database that went away

    @staticmethod
    def _apply(state: dict[str, Any], params: dict[str, Any]) -> None:
        if "entry" in params:  # an entry written under its key
            state["unfinished"] = {**(state["unfinished"] or {}), params["import_key"]: json.loads(params["entry"])}
            return
        if set(params) == {"family_uuid", "import_key"}:  # an entry removed alone
            left = dict(state["unfinished"] or {})
            left.pop(params["import_key"], None)
            state["unfinished"] = left or None
            return
        recorded = json.loads(params.get("imported_scopes", "null"))
        if recorded is not None:  # a JSON null leaves it as it is
            state["imported"] = recorded
        if "unfinished" in params:
            state["unfinished"] = json.loads(params["unfinished"])
        if "flag" in params:
            state["flag"] = json.loads(params["flag"])

    async def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        params = dict(params or {})
        if self.unavailable and self.unavailable in sql:
            raise ConnectionError("database unavailable")
        if sql.startswith("SELECT metadata FROM samples"):
            return SimpleNamespace(scalar_one_or_none=lambda: {})
        if sql.startswith("UPDATE samples"):
            return SimpleNamespace(rowcount=1)
        if sql.startswith("SELECT") and "FROM families" in sql:
            row = {
                "family_uuid": "family-uuid",
                "flag": copy.deepcopy(self.working["flag"]),
                "unfinished": copy.deepcopy(self.working["unfinished"]),
                "imported_scopes": copy.deepcopy(self.working["imported"]),
            }
            return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: row))
        assert sql.startswith("UPDATE families"), sql
        self._apply(self.working, params)
        return SimpleNamespace(rowcount=1)

    async def commit(self) -> None:
        self.committed = copy.deepcopy(self.working)
        self.commits += 1

    async def rollback(self) -> None:
        self.working = copy.deepcopy(self.committed)

    def connection(self):
        database = self

        class _Connection:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc) -> bool:
                return False

            async def execute(self, statement, params=None):
                sql = " ".join(str(statement).split())
                assert sql.startswith("UPDATE families"), sql
                for state in (database.committed, database.working):
                    database._apply(state, dict(params or {}))
                if database.events is not None:
                    database.events.append(("mark", params["import_key"]))
                return SimpleNamespace(rowcount=1)

            async def commit(self) -> None:
                return None

        return _Connection()


def _marks(database: _Postgres) -> dict[str, dict[str, Any]]:
    """The family's committed import_unfinished entries."""
    return dict(database.committed["unfinished"] or {})


def _per_sample(dataset: str, *samples: str) -> list[dict[str, Any]]:
    return [{"dataset": dataset, "scope": {"source": None, "samples": [sample]}} for sample in samples]


@pytest.fixture
def stores(monkeypatch):
    """``stores(rows, database)``: the family's SV rows in ClickHouse, faked at the storage's
    edge so that the rewrite and its restore run as written, and its Postgres."""

    def install(rows: list[StoredStructuralVariantRow], database: _Postgres) -> _ClickHouse:
        clickhouse = _ClickHouse(rows)
        database.events = clickhouse.events

        async def nothing(*_args, **_kwargs):
            return None

        async def no_genes(*_args, **_kwargs):
            return {}

        async def known(*_args, **_kwargs):
            return set(_SAMPLES)

        async def family_context(*_args, **_kwargs):
            return _FAMILY

        for module in (variant_upload_service, admin_service, family_package_datasets):
            monkeypatch.setattr(module, "fetch_family_structural_variant_rows", clickhouse.fetch)
            monkeypatch.setattr(module, "lock_family_variant_writes", nothing)
        monkeypatch.setattr(clickhouse_variant_storage, "delete_family_structural_variants", clickhouse.delete)
        monkeypatch.setattr(clickhouse_variant_storage, "insert_structural_variant_records", clickhouse.insert)
        monkeypatch.setattr(variant_upload_service, "_fetch_genes_for_chroms", no_genes)
        monkeypatch.setattr(variant_upload_service, "known_vcf_sample_ids", known)
        monkeypatch.setattr(annotation_manifest_service, "merge_vcf_header_provenance", nothing)
        monkeypatch.setattr(sv_router, "build_family_metadata_context", family_context)
        monkeypatch.setattr(sv_router, "record_upload_file_obj", nothing)
        monkeypatch.setattr(
            family_package_registration, "get_postgres_sessionmaker", lambda: database.connection, raising=False
        )
        return clickhouse

    return install


async def _upload(database: _Postgres, sample_id: str, *, overwrite: bool = True):
    """S``sample_id``'s Sniffles VCF through the structural-variant upload's route. The
    request's session ends with it: what it did not commit is rolled back."""
    with pytest.MonkeyPatch.context() as patch:

        async def sample_context(*_args, **_kwargs):
            return _sample(sample_id)

        patch.setattr(sv_router, "build_sample_metadata_context", sample_context)
        try:
            return await sv_router.upload_structural_variants(
                sample_id=sample_id,
                file=_sniffles_file(),
                overwrite=overwrite,
                source_format="sniffles",
                session=database,  # type: ignore[arg-type]
                user=SimpleNamespace(id="admin", username="admin"),
            )
        finally:
            await database.rollback()


# --- the rewrite ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_rewrite_that_fails_after_its_delete_puts_the_stored_rows_back(stores) -> None:
    clickhouse = stores(_SNIFFLES, _Postgres())
    clickhouse.failing_inserts = 1
    stored = await clickhouse.fetch("GRCh38", "family-uuid", source="sniffles")

    with pytest.raises(RuntimeError, match="ClickHouse insert failed"):
        await clickhouse_variant_storage.rewrite_family_structural_variants(
            "GRCh38", "family-uuid", [_row(_DEL_30000, {"S1": "0/1"})], source="sniffles", stored=stored
        )

    # The write's error, with the source's rows as they were: deleted again (with what the
    # failed write stored) and written back. Before: deleted, and nothing put them back.
    assert clickhouse.events[1:] == [("delete", "sniffles"), ("insert failed",), ("delete", "sniffles"), ("insert", 2)]
    assert clickhouse.calls() == _SNIFFLES_CALLS


@pytest.mark.asyncio
async def test_a_rewrite_that_fails_in_its_delete_is_put_back_the_same_way(stores, monkeypatch) -> None:
    clickhouse = stores(_SNIFFLES, _Postgres())
    stored = copy.deepcopy(_SNIFFLES)
    deletes = {"count": 0}

    async def delete_fails_once(assembly, family, *, source=None) -> None:
        deletes["count"] += 1
        await clickhouse.delete(assembly, family, source=source)  # how far it got is not known
        if deletes["count"] == 1:
            raise RuntimeError("mutation failed")

    monkeypatch.setattr(clickhouse_variant_storage, "delete_family_structural_variants", delete_fails_once)

    with pytest.raises(RuntimeError, match="mutation failed"):
        await clickhouse_variant_storage.rewrite_family_structural_variants(
            "GRCh38", "family-uuid", [], source="sniffles", stored=stored
        )

    assert clickhouse.calls() == _SNIFFLES_CALLS


@pytest.mark.asyncio
async def test_rows_the_rewrite_cannot_put_back_are_said_to_be_missing(stores) -> None:
    clickhouse = stores(_SNIFFLES, _Postgres())
    clickhouse.failing_inserts = 2  # the write's insert, and the restore's
    stored = copy.deepcopy(_SNIFFLES)

    with pytest.raises(Exception) as raised:
        await clickhouse_variant_storage.rewrite_family_structural_variants(
            "GRCh38", "family-uuid", [_row(_DEL_30000, {"S1": "0/1"})], source="sniffles", stored=stored
        )

    not_restored = clickhouse_variant_storage.StructuralVariantsNotRestored
    assert isinstance(raised.value, not_restored)
    assert raised.value.source == "sniffles"
    # Raised from the write's own error.
    assert str(raised.value.__cause__) == "ClickHouse insert failed"
    assert clickhouse.calls() == {}


# --- the structural-variant upload ------------------------------------------------------


@pytest.mark.asyncio
async def test_an_sv_upload_marks_the_family_before_its_rewrite_and_its_commit_removes_the_mark(stores) -> None:
    database = _Postgres()
    clickhouse = stores(_SNIFFLES, database)

    result = await _upload(database, "S1")

    assert result["merged"] == 1
    # The mark is written, on a connection of its own, before the rewrite deletes anything.
    [mark] = [event for event in clickhouse.events if event[0] == "mark"]
    assert clickhouse.events.index(mark) < clickhouse.events.index(("delete", "sniffles"))
    assert mark[1].startswith("upload-")
    # The upload's commit removed it, and recorded what the upload brought.
    assert database.committed["unfinished"] is None
    assert database.committed["imported"] == {"sv_sniffles": [{"source": None, "samples": ["S1"]}]}
    assert clickhouse.calls() == {_DEL_30000: {"S1": "0/1", "S2": "0/1", "S3": "1/1"}}


@pytest.mark.asyncio
async def test_an_sv_upload_whose_rewrite_fails_leaves_every_samples_calls_as_they_were(stores) -> None:
    database = _Postgres()
    clickhouse = stores(_SNIFFLES, database)
    clickhouse.failing_inserts = 1  # the rewrite's first insert

    with pytest.raises(RuntimeError, match="ClickHouse insert failed"):
        await _upload(database, "S1")

    # Before: the rewrite had deleted every sample's Sniffles calls -- S2's and S3's too, which
    # the upload did not change -- and nothing marked the family.
    assert clickhouse.calls() == _SNIFFLES_CALLS
    # Nothing is missing, so nothing marks the family: its mark went again.
    assert database.committed["unfinished"] is None
    assert database.committed["flag"] is None and database.committed["imported"] is None


@pytest.mark.asyncio
async def test_an_sv_upload_whose_rows_cannot_be_put_back_leaves_the_family_marked_for_each_sample(stores) -> None:
    database = _Postgres()
    clickhouse = stores(_SNIFFLES, database)
    clickhouse.failing_inserts = 99  # ClickHouse keeps failing

    with pytest.raises(Exception) as raised:
        await _upload(database, "S1")

    assert type(raised.value).__name__ == "StructuralVariantsNotRestored"
    assert clickhouse.calls() == {}
    # Marked for the Sniffles calls of every sample the rewrite deleted and was writing, one
    # sample at a time, as an upload of each records them (before: nothing marked it).
    [(key, mark)] = _marks(database).items()
    assert key.startswith("upload-")
    assert (mark["job_id"], mark["datasets"], mark["finished_datasets"]) == (None, ["sv_sniffles"], [])
    assert mark["whole_family"] == _per_sample("sv_sniffles", "S1", "S2", "S3")
    assert database.committed["flag"] is None


@pytest.mark.asyncio
async def test_an_sv_upload_whose_process_stops_in_its_rewrite_leaves_the_family_marked(stores) -> None:
    database = _Postgres()
    clickhouse = stores(_SNIFFLES, database)
    clickhouse.stop = True

    with pytest.raises(_ProcessStopped):
        await _upload(database, "S1")

    # Nothing of the upload's own transaction committed; its mark did, before the delete.
    assert database.commits == 0
    assert clickhouse.calls() == {}
    [(key, mark)] = _marks(database).items()
    assert key.startswith("upload-")
    assert mark["whole_family"] == _per_sample("sv_sniffles", "S1", "S2", "S3")


@pytest.mark.asyncio
async def test_each_samples_upload_with_overwrite_strikes_its_calls_off_the_mark(stores) -> None:
    database = _Postgres()
    clickhouse = stores(_SNIFFLES, database)
    clickhouse.failing_inserts = 99
    with pytest.raises(Exception):
        await _upload(database, "S1")
    [key] = _marks(database)
    clickhouse.failing_inserts = 0

    await _upload(database, "S1")
    # S1's calls are back: its item goes; the others' stay until theirs are uploaded again.
    assert _marks(database)[key]["whole_family"] == _per_sample("sv_sniffles", "S2", "S3")
    assert _marks(database)[key]["datasets"] == ["sv_sniffles"]
    await _upload(database, "S2")
    await _upload(database, "S3")
    assert database.committed["unfinished"] is None


@pytest.mark.asyncio
async def test_an_sv_upload_that_fails_before_its_rewrite_leaves_no_mark(stores) -> None:
    database = _Postgres()
    clickhouse = stores(_SNIFFLES, database)
    # The upload's record of what it brings, written before its rewrite, cannot be written.
    database.unavailable = "FOR NO KEY UPDATE"

    with pytest.raises(ConnectionError):
        await _upload(database, "S1")

    assert not [event for event in clickhouse.events if event[0] in {"delete", "insert"}]
    assert clickhouse.calls() == _SNIFFLES_CALLS
    assert database.committed["unfinished"] is None


# --- the admin deletes of a sample's structural variants ---------------------------------


# The family's SVs from every source, and what its imports and uploads recorded bringing.
_ALL_SOURCES = [
    _row(_NEEDLR_DEL, {"S1": "0/1", "S2": "0/1"}, source="needlr"),
    _row(_DEL_30000, {"S2": "0/1", "S1": "1/1"}),
]
_IMPORTED = {
    "snv": [{"source": "clair3", "samples": None}],
    "sv_needlr": [{"source": None, "samples": None}],
    "sv_sniffles": [{"source": None, "samples": ["S1"]}, {"source": None, "samples": ["S2"]}],
}


def _admin_family(monkeypatch) -> None:
    async def sample_row(_session, _sample_id):
        return {"sample_uuid": "uuid-S2", "sample_id": "S2", "family_uuid": "family-uuid", "family_id": "F1"}

    async def sample_rows(_session, family_uuids):
        return {family_uuid: [] for family_uuid in family_uuids}

    async def contexts(_session, **_kwargs):
        return [_FAMILY]

    async def count(*_args, **_kwargs):
        return 2

    monkeypatch.setattr(admin_service, "_sample_row_or_404", sample_row)
    monkeypatch.setattr(admin_service, "_sample_rows_by_family", sample_rows)
    monkeypatch.setattr(admin_service, "_family_assembly_contexts", contexts)
    monkeypatch.setattr(admin_service, "count_family_structural_variants", count)


@pytest.mark.asyncio
async def test_an_admin_delete_of_a_samples_svs_whose_rewrite_fails_puts_the_familys_svs_back(
    stores, monkeypatch
) -> None:
    database = _Postgres(imported=copy.deepcopy(_IMPORTED))
    clickhouse = stores(_ALL_SOURCES, database)
    _admin_family(monkeypatch)
    clickhouse.failing_inserts = 1

    with pytest.raises(RuntimeError, match="ClickHouse insert failed"):
        await admin_service.delete_sample_data_by_type(
            database, "S2", "structural_variants", True  # type: ignore[arg-type]
        )

    # Before: every source's SVs gone, S1's with S2's.
    assert clickhouse.calls("needlr") == {_NEEDLR_DEL: {"S1": "0/1", "S2": "0/1"}}
    assert clickhouse.calls("sniffles") == {_DEL_30000: {"S2": "0/1", "S1": "1/1"}}
    assert database.committed["unfinished"] is None


@pytest.mark.asyncio
async def test_an_admin_delete_whose_svs_cannot_be_put_back_marks_the_family_for_its_sv_datasets(
    stores, monkeypatch
) -> None:
    database = _Postgres(imported=copy.deepcopy(_IMPORTED))
    clickhouse = stores(_ALL_SOURCES, database)
    _admin_family(monkeypatch)
    clickhouse.failing_inserts = 99

    with pytest.raises(Exception) as raised:
        await admin_service.delete_sample_data_by_type(
            database, "S2", "structural_variants", True  # type: ignore[arg-type]
        )

    assert type(raised.value).__name__ == "StructuralVariantsNotRestored"
    # Marked, in a commit of its own, for each SV dataset the family holds, one sample at a
    # time: not its small variants, and not the deleted sample's calls, which were to go
    # (before: nothing marked the family).
    [(key, mark)] = _marks(database).items()
    assert key.startswith("deletion-")
    assert mark["datasets"] == ["sv_needlr", "sv_sniffles"]
    assert mark["whole_family"] == [
        {"dataset": "sv_needlr", "scope": {"source": None, "samples": None}},
        *_per_sample("sv_sniffles", "S1"),
    ]
    assert database.commits == 1


# --- a package's chrM structural variants -------------------------------------------------


_MITO_SV_HEADER = (
    "##fileformat=VCFv4.2\n"
    "##source=Sniffles2_2.7.3\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSample0\n"
)
_MITO_VCF = (
    "##fileformat=VCFv4.2\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSample0\n"
    "chrM\t150\t.\tC\tT\t60\tPASS\t.\tGT:AF\t1:0.98\n"
)


def _mito_package(root: Path) -> Path:
    """A package of family F1 whose mito dataset brings S1's chrM calls and SVs."""
    (root / "mito").mkdir(parents=True)
    (root / "mito" / "S1.chrM.vcf").write_text(_MITO_VCF, encoding="utf-8")
    (root / "mito" / "S1.chrM.sv.vcf").write_text(
        _MITO_SV_HEADER
        + "chrM\t8470\tSniffles2.DEL.1M0\tN\t<DEL>\t60\tPASS\t"
        "PRECISE;SVTYPE=DEL;SVLEN=-4977;END=13447;SUPPORT=140;VAF=0.35\tGT:GQ:DR:DV\t0/1:60:260:140\n",
        encoding="utf-8",
    )
    (root / "family.ped").write_text(
        "F1\tS1\tS3\tS2\t2\t2\nF1\tS2\t0\t0\t2\t1\nF1\tS3\t0\t0\t1\t1\n", encoding="utf-8"
    )
    (root / "manifest.yaml").write_text(
        "schema_version: 1\nfamily_id: F1\nped: family.ped\n"
        "datasets:\n  mito:\n    per_sample:\n      S1:\n"
        "        vcf: mito/S1.chrM.vcf\n        sv_vcf: mito/S1.chrM.sv.vcf\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture
def mito_update(monkeypatch, tmp_path):
    """``await mito_update(database, clickhouse)``: an update of family F1 (it exists) that
    brings S1's chrM calls and SVs, through the import and its real mito importer; the
    family's chrM SNVs are faked away (none stored, so the update imports the dataset)."""
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path)])
    root = _mito_package(tmp_path / "package")

    async def register(session, *, import_mark=None, **_kwargs):
        await family_package_registration._mark_family_import_unfinished(
            session, family_uuid="family-uuid", mark=import_mark
        )
        return _FAMILY, False

    @asynccontextmanager
    async def hold(_family_uuid, **_kwargs):
        yield

    async def nothing(*_args, **_kwargs):
        return None

    async def none_stored(*_args, **_kwargs):
        return 0

    async def no_warnings(*_args, **_kwargs):
        return []

    async def known(*_args, **_kwargs):
        return set(_SAMPLES)

    async def chrm_calls(*_args, **_kwargs):
        return {"inserted": 1}

    monkeypatch.setattr(family_package_import, "_ensure_family_from_ped", register)
    monkeypatch.setattr(family_package_import, "hold_family_variant_writes", hold)
    monkeypatch.setattr(family_package_import, "_existing_package_entity_warnings", no_warnings)
    monkeypatch.setattr(family_package_datasets, "count_family_small_variants", none_stored)
    monkeypatch.setattr(family_package_datasets, "known_vcf_sample_ids", known)
    monkeypatch.setattr(family_package_datasets, "upload_family_small_variant_file", chrm_calls)
    monkeypatch.setattr(family_package_datasets, "_record_mtdna_sample_metadata", nothing)
    monkeypatch.setattr(family_package_datasets, "_update_sv_file_metadata", nothing)
    monkeypatch.setattr(variant_ranking_cache, "clear_family_ranking_cache", nothing)
    monkeypatch.setattr(sv_gene_index_service, "clear_family_sv_gene_index", nothing)

    async def run(database: _Postgres):
        return await family_package_import.execute_family_package_import(
            database,  # type: ignore[arg-type]
            folder_path=root,
            project_id=None,
            dry_run=False,
            user=SimpleNamespace(id="admin"),
            conflict_mode="update",
            job_id="job-mito",
        )

    return run


# S2's chrM deletion, from an earlier import of S2's file.
_STORED_MITO = [_row(_MITO_DEL, {"S2": "0/1"}, source="mito_sv")]


@pytest.mark.asyncio
async def test_an_update_whose_chrm_sv_rewrite_fails_puts_the_other_samples_calls_back(stores, mito_update) -> None:
    database = _Postgres()
    clickhouse = stores(_STORED_MITO, database)
    clickhouse.failing_inserts = 1

    result = await mito_update(database)

    assert result.error and "mito" in result.error
    # The update took no snapshot: before, S2's chrM deletion was gone with the failed write.
    assert clickhouse.calls("mito_sv") == {_MITO_DEL: {"S2": "0/1"}}
    # The dataset failed for the samples it names, as before.
    flag = database.committed["flag"]
    assert flag["failed_datasets"] == ["mito"]
    assert [item["scope"] for item in flag["failed_scopes"]["mito"]] == [{"source": None, "samples": ["S1"]}]


@pytest.mark.asyncio
async def test_an_update_whose_chrm_svs_cannot_be_put_back_flags_mito_for_every_sample_they_held(
    stores, mito_update
) -> None:
    database = _Postgres()
    clickhouse = stores(_STORED_MITO, database)
    clickhouse.failing_inserts = 99

    result = await mito_update(database)

    assert result.error and "mito" in result.error
    assert clickhouse.calls("mito_sv") == {}
    # Flagged for S2 too, whose chrM SVs the rewrite deleted: an import of S1's file alone
    # does not bring them again (before: flagged for S1 alone).
    flag = database.committed["flag"]
    assert flag["failed_datasets"] == ["mito"]
    assert [item["scope"] for item in flag["failed_scopes"]["mito"]] == [{"source": None, "samples": ["S1", "S2"]}]
    assert database.committed["unfinished"] is None
