"""A family's variant writes run one at a time (unit, fakes).

Every write to a family's variants reads its stored rows, deletes them and writes them back
changed, in ClickHouse, which has no transactions. The per-sample SV upload reads its
source's rows and merges the file's calls into them; the admin per-sample SV delete rewrites
the family's rows without the sample; the per-sample small-variant upload (the mitochondrial
calls) merges its file's calls into the callset's rows. Two such writes of one family ran at
once, each from the rows it had read before the other wrote. One writer's calls were then
lost although both answered 200, or one variant was stored twice with different calls, and a
part merge keeps only one of the two rows.

The storage is faked as ClickHouse behaves: a read returns the rows as stored, and a rewrite
deletes them and then inserts the new rows, with other work able to run in between. Postgres
is faked as far as the writers use it, with its transaction-scoped advisory locks: a session
holds a key until its transaction ends, and may take it again meanwhile. Each read waits,
for a moment at most, until the other writer has read too: the interleaving two uploads
started together can take. ``e2e/test_e2e_family_variant_writes_serialized.py`` runs the same
writes against Postgres and ClickHouse.

Then each writer's lock is pinned: which of the family's locks it takes, that it takes them
before its first read and holds them until its commit, that a writer which waited for a
delete of its family or sample writes nothing, and that the package import holds both from
its snapshot until its restore while its own dataset loaders take none of their own. Last,
the report sign-out's share of the locks: a write started while a sign-out reads the family
waits for it, and a sign-out is refused at once, without waiting, while a writer holds them.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
import copy
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException, UploadFile
import pytest

from backend.app.core.config import settings
from backend.app.schemas import FamilyImportDatasetSummary
from backend.app.services import (
    admin_service,
    annotation_manifest_service,
    family_package_datasets,
    family_package_import,
    family_package_registration,
    family_structure_service,
    family_variant_write_lock,
    ped_service,
    variant_upload_service,
)
from backend.app.services.access_control import CurrentUser
from backend.app.services.clickhouse_variant_ids import small_variant_key
from backend.app.services.clickhouse_variant_records import (
    StoredStructuralVariantRow,
    StructuralVariantCall,
    StructuralVariantRecord,
)
from backend.app.services.clickhouse_variant_storage import SMALL_VARIANT_ENTRY_COLUMNS
from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext
from backend.app.services.family_package_common import ManifestDataset
from backend.app.services.family_package_datasets import DatasetImportJob
from backend.app.services.family_variant_write_lock import (
    SMALL_VARIANTS,
    STRUCTURAL_VARIANTS,
    VARIANT_TYPES,
    family_variant_lock_key,
    hold_family_variant_writes,
    lock_family_variant_writes,
)

_FAMILY = "family-uuid"
_SAMPLES = {"PROBAND": "uuid-proband", "MOTHER": "uuid-mother", "FATHER": "uuid-father"}
_DEL = "1-30000-31000-DEL---"
_NEEDLR = "SVDEL1"


def _key(variant_type: str, family_uuid: str = _FAMILY) -> str:
    return family_variant_lock_key(family_uuid, variant_type)


# --- Postgres, as far as the writers use it ------------------------------------------


class _Result:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self._rows = rows or []

    def scalar_one_or_none(self) -> Any:
        return self._rows[0][0] if self._rows else None

    def scalars(self) -> "_Result":
        return _Result([(row[0],) for row in self._rows])

    def all(self) -> list[Any]:
        return [row[0] if len(row) == 1 else row for row in self._rows]

    def first(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)

    def scalar_one(self) -> Any:
        assert len(self._rows) == 1
        return self._rows[0][0]


class _Postgres:
    """The stored families and samples, and the transaction-scoped advisory locks as the
    server keeps them: one exclusive holder per key, or any number of shared holders, until
    their transaction ends. An exclusive request waits for the shared holders; a shared
    try-lock is refused while the key is held or waited for exclusively, as Postgres queues
    it behind the waiting request. ``log`` records, in order, each lock granted or refused,
    each transaction's end and each storage call made."""

    def __init__(self) -> None:
        self.samples: dict[str, set[str]] = {_FAMILY: set(_SAMPLES.values())}
        self._locks: dict[str, asyncio.Lock] = {}
        self.shared: dict[str, int] = {}
        self.log: list[tuple[str, ...]] = []
        self.lock_statements: list[tuple[str, dict[str, Any]]] = []

    def lock(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    def session(self, name: str) -> "_Session":
        return _Session(self, name)

    def trace(self, *names: str) -> list[str]:
        """The log of the named sessions and of the storage, as short lines."""
        lines = []
        for entry in self.log:
            if entry[0] == "storage":
                lines.append(entry[1])
            elif entry[0] in names:
                lines.append(" ".join(entry[1:]))
        return lines


class _Session:
    def __init__(self, postgres: _Postgres, name: str) -> None:
        self.postgres = postgres
        self.name = name
        self.held: list[str] = []
        self.shared: list[str] = []

    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        sql = " ".join(str(statement).split())
        params = params if isinstance(params, dict) else {}
        if "pg_try_advisory_xact_lock_shared" in sql:
            self.postgres.lock_statements.append((sql, dict(params)))
            key = str(params["k"])
            if self.postgres.lock(key).locked():
                self.postgres.log.append((self.name, "share refused", key))
                return _Result([(False,)])
            self.postgres.shared[key] = self.postgres.shared.get(key, 0) + 1
            self.shared.append(key)
            self.postgres.log.append((self.name, "share", key))
            return _Result([(True,)])
        if "pg_advisory_xact_lock" in sql:
            self.postgres.lock_statements.append((sql, dict(params)))
            key = str(params["k"])
            if key not in self.held:  # a key the session holds is granted again at once
                await self.postgres.lock(key).acquire()
                self.held.append(key)
                while self.postgres.shared.get(key):  # wait for the shared holders
                    await asyncio.sleep(0.001)
            self.postgres.log.append((self.name, "lock", key))
            return _Result()
        if "FROM families f" in sql and "family_uuid" in params:
            # Which of the named samples the family still has; no row without the family.
            stored = self.postgres.samples.get(str(params["family_uuid"]))
            if stored is None:
                return _Result()
            named = [str(value) for value in params.get("sample_uuids") or []]
            return _Result([(value,) for value in named if value in stored] or [(None,)])
        if "FROM families WHERE family_id" in sql:
            return _Result([(_FAMILY,)])
        return _Result()

    async def commit(self) -> None:
        self._end("commit")

    async def rollback(self) -> None:
        self._end("rollback")

    async def close(self) -> None:
        self._end("close")

    def _end(self, how: str) -> None:
        for key in self.held:
            self.postgres.lock(key).release()
        self.held.clear()
        for key in self.shared:
            self.postgres.shared[key] -= 1
        self.shared.clear()
        self.postgres.log.append((self.name, how))


def _recorder(postgres: _Postgres, name: str, result: Any = None):
    async def record(*_args, **_kwargs) -> Any:
        postgres.log.append(("storage", name))
        return copy.deepcopy(result)

    return record


# --- ClickHouse storage, as it behaves ----------------------------------------------


class _Interleaving:
    """Holds each writer after its read until every writer has read, or a moment has
    passed. Without a lock, the writers then all write from what they read. With one, the
    others cannot read before the first has committed, and its wait ends by the timeout."""

    def __init__(self, writers: int = 2, timeout: float = 0.2) -> None:
        self.writers = writers
        self.timeout = timeout
        self.reads = 0
        self._all_read = asyncio.Event()

    async def after_read(self) -> None:
        self.reads += 1
        if self.reads >= self.writers:
            self._all_read.set()
        with suppress(TimeoutError):
            await asyncio.wait_for(self._all_read.wait(), timeout=self.timeout)


def _record(variant_id: str, source: str, calls: dict[str, str]) -> StructuralVariantRecord:
    start, end = (30000, 31000) if variant_id == _DEL else (7000, 12000)
    return StructuralVariantRecord(
        variant_key=None,
        variant_id=variant_id,
        chr="1",
        start=start,
        end=end,
        sv_type="DEL",
        source=source,
        remote_chr=None,
        remote_start=None,
        remote_end=None,
        sv_len=None,
        filters=[],
        gene_symbols=[],
        annotations=[],
        calls=[
            StructuralVariantCall(sample=sample, gt=gt, qual=None, read_support=None, filter=None)
            for sample, gt in sorted(calls.items())
        ],
    )


class _SvStore:
    """The family's stored SV rows (one project), read and rewritten like ClickHouse."""

    def __init__(
        self, records: list[StructuralVariantRecord], interleaving: _Interleaving, postgres: _Postgres | None
    ) -> None:
        self.rows = [StoredStructuralVariantRow(project_id="project-uuid", record=r) for r in records]
        self.interleaving = interleaving
        self.postgres = postgres

    def _log(self, what: str) -> None:
        if self.postgres is not None:
            self.postgres.log.append(("storage", what))

    async def fetch(self, _assembly, _family, *, source=None) -> list[StoredStructuralVariantRow]:
        self._log("read SVs")
        rows = [copy.deepcopy(row) for row in self.rows if source is None or row.record.source == source]
        await self.interleaving.after_read()
        return rows

    async def rewrite(self, _assembly, _family, rows, *, source=None) -> None:
        self._log("rewrite SVs")
        # The delete and the inserts are separate statements; nothing holds them together.
        self.rows = [row for row in self.rows if source is not None and row.record.source != source]
        await asyncio.sleep(0)
        self.rows.extend(copy.deepcopy(list(rows)))

    def calls(self) -> dict[tuple[str, str], list[dict[str, str]]]:
        """Each stored row's calls, by source and SV: one dict per row stored."""
        out: dict[tuple[str, str], list[dict[str, str]]] = {}
        for row in self.rows:
            out.setdefault((row.record.source, row.record.variant_id), []).append(
                {call.sample: call.gt for call in row.record.calls}
            )
        return out


def _entry(pos: int, calls: dict[str, str]) -> dict[str, Any]:
    """One stored ``SNV_INDEL/entries`` row of the chrM ``pos`` A>G call, every column."""
    row: dict[str, Any] = {column: None for column in SMALL_VARIANT_ENTRY_COLUMNS}
    row.update(
        key=small_variant_key("GRCh38", f"M-{pos}-A-G"),
        variantId=f"M-{pos}-A-G",
        annotation_version="vcf_info",
        annotationSetHash=11,
        project_guid="project-uuid",
        family_guid=_FAMILY,
        sample_type="WGS",
        xpos=25_000_000_000 + pos,
        chrom="M",
        pos=pos,
        ref="A",
        alt="G",
        source="mito",
        is_gnomad_gt_5_percent=False,
        is_annotated_in_any_gene=False,
        gene_symbols=[],
        filters=["PASS"],
        qual=30.0,
        sign=1,
    )
    row["calls.sampleId"] = list(calls)
    row["calls.gt"] = list(calls.values())
    for column in ("calls.gq", "calls.dp", "calls.ab", "calls.ps"):
        row[column] = [None for _ in calls]
    row["calls.af"] = [[1.0] for _ in calls]
    row["calls.ad"] = [[0, 500] for _ in calls]
    return row


class _SmallStore:
    """The family's stored small-variant entry rows, read and rewritten like ClickHouse."""

    def __init__(self, entries: list[dict[str, Any]], interleaving: _Interleaving, postgres: _Postgres | None) -> None:
        self.entries = entries
        self.interleaving = interleaving
        self.postgres = postgres

    async def fetch(self, _assembly, _family, *, source=None) -> list[dict[str, Any]]:
        if self.postgres is not None:
            self.postgres.log.append(("storage", "read small variants"))
        rows = [copy.deepcopy(row) for row in self.entries if source is None or row["source"] == source]
        await self.interleaving.after_read()
        return rows

    async def rewrite(self, _assembly, _family, entries, *, source=None) -> None:
        self.entries = [row for row in self.entries if source is not None and row["source"] != source]
        await asyncio.sleep(0)
        self.entries.extend(copy.deepcopy(list(entries)))

    def calls(self) -> dict[int, list[dict[str, str]]]:
        out: dict[int, list[dict[str, str]]] = {}
        for row in self.entries:
            out.setdefault(row["pos"], []).append(dict(zip(row["calls.sampleId"], row["calls.gt"])))
        return out


class _CallsetStore:
    """A callset's rows as a whole-family upload writes them: counted first, then deleted
    and inserted batch by batch, like ClickHouse."""

    def __init__(self, interleaving: _Interleaving) -> None:
        self.rows: list[tuple[str, str, str]] = []
        self.interleaving = interleaving

    async def count(self, _assembly, _family, *, project_ids=None, source=None) -> int:
        stored = sum(1 for row in self.rows if row[0] == source)
        await self.interleaving.after_read()
        return stored

    async def delete(self, _assembly, _family, *, source=None) -> None:
        self.rows = [row for row in self.rows if source is not None and row[0] != source]
        await asyncio.sleep(0)

    async def insert(self, _assembly, _family, _projects, records, **_kwargs) -> None:
        for record in records:
            self.rows.append((record.source, record.variant_id, record.calls[0].gt))
        await asyncio.sleep(0)


# --- The family ----------------------------------------------------------------------


def _family_context() -> FamilyMetadataContext:
    return FamilyMetadataContext(
        family_uuid=_FAMILY,
        family_id="FAM",
        project_ids=["project-uuid"],
        sample_rows=[
            {"sample_uuid": uuid, "sample_id": name, "sex": "und", "role": "member", "affected": False}
            for name, uuid in _SAMPLES.items()
        ],
        sample_uuid_to_name={uuid: name for name, uuid in _SAMPLES.items()},
        sample_name_to_uuid=dict(_SAMPLES),
        affected_sample_names=[],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


def _sample_context(sample: str) -> SampleMetadataContext:
    return SampleMetadataContext(
        sample_uuid=_SAMPLES[sample],
        sample_id=sample,
        family_uuid=_FAMILY,
        family_id="FAM",
        sex="und",
        project_ids=["project-uuid"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


def _admin_user() -> CurrentUser:
    return CurrentUser(
        id="00000000-0000-0000-0000-000000000001",
        username="admin",
        email="admin@example.com",
        role="admin",
        created_at=datetime.now(timezone.utc),
    )


def _sniffles(*records: str) -> UploadFile:
    body = (
        "##fileformat=VCFv4.2\n"
        "##source=Sniffles2_2.2\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n"
        + "".join(f"{record}\n" for record in records)
    )
    return UploadFile(file=BytesIO(body.encode()), filename="calls.sniffles.vcf")


def _sniffles_del(gt: str) -> str:
    return f"1\t30000\tS.1\tN\t<DEL>\t40\tPASS\tSVTYPE=DEL;SVLEN=-1000;END=31000;SUPPORT=9\tGT\t{gt}"


def _mito(sample: str, *positions: int) -> UploadFile:
    body = (
        "##fileformat=VCFv4.2\n"
        f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample}\n"
        + "".join(f"chrM\t{pos}\t.\tA\tG\t50\tPASS\t.\tGT:DP:AD:VAF\t1/1:500:0,500:1\n" for pos in positions)
    )
    return UploadFile(file=BytesIO(body.encode()), filename=f"{sample}.mito.vcf")


def _family_vcf(*records: tuple[int, str, str, str]) -> UploadFile:
    body = (
        "##fileformat=VCFv4.2\n"
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPROBAND\n"
        + "".join(f"1\t{pos}\t.\t{ref}\t{alt}\t.\tPASS\t.\tGT\t{gt}\n" for pos, ref, alt, gt in records)
    )
    return UploadFile(file=BytesIO(body.encode()), filename="family.vcf")


async def _nothing(*_args, **_kwargs) -> None:
    return None


async def _no_genes(*_args, **_kwargs) -> dict:
    return {}


async def _family_sample_ids(*_args, **_kwargs) -> set[str]:
    """The sample ids an SV upload checks its file's columns against: the family's."""
    return set(_SAMPLES)


@pytest.fixture
def sv_store(monkeypatch: pytest.MonkeyPatch):
    def install(
        records: list[StructuralVariantRecord], *, writers: int = 2, postgres: _Postgres | None = None
    ) -> _SvStore:
        store = _SvStore(records, _Interleaving(writers), postgres)
        for module in (variant_upload_service, admin_service):
            monkeypatch.setattr(module, "fetch_family_structural_variant_rows", store.fetch)
            monkeypatch.setattr(module, "rewrite_family_structural_variants", store.rewrite)
        monkeypatch.setattr(variant_upload_service, "known_vcf_sample_ids", _family_sample_ids)
        monkeypatch.setattr(variant_upload_service, "_fetch_genes_for_chroms", _no_genes)
        monkeypatch.setattr(annotation_manifest_service, "merge_vcf_header_provenance", _nothing)
        return store

    return install


@pytest.fixture
def small_store(monkeypatch: pytest.MonkeyPatch):
    def install(entries: list[dict[str, Any]], *, postgres: _Postgres | None = None) -> _SmallStore:
        store = _SmallStore(entries, _Interleaving(), postgres)
        monkeypatch.setattr(variant_upload_service, "fetch_family_small_variant_entries", store.fetch)
        monkeypatch.setattr(variant_upload_service, "rewrite_family_small_variant_entries", store.rewrite)
        # The shared details and annotations: written for every family alike, not faked here.
        monkeypatch.setattr(variant_upload_service, "insert_small_variant_records", _nothing)
        monkeypatch.setattr(annotation_manifest_service, "merge_vcf_header_provenance", _nothing)
        return store

    return install


async def _sv_upload(postgres: _Postgres, sample: str, *records: str) -> dict[str, Any]:
    session = postgres.session(f"upload {sample}")
    try:
        return await variant_upload_service.upload_structural_variant_file(
            session,  # type: ignore[arg-type]
            family_context=_family_context(),
            sample_context=_sample_context(sample),
            file=_sniffles(*records),
            overwrite=True,
            format_hint="sniffles",
        )
    finally:
        await session.close()  # the request's session: rolled back, if not committed


async def _mito_upload(postgres: _Postgres, sample: str, *positions: int) -> dict[str, Any]:
    session = postgres.session(f"upload {sample}")
    try:
        return await variant_upload_service.upload_family_small_variant_file(
            session,  # type: ignore[arg-type]
            context=_family_context(),
            sample_contexts={name: _sample_context(name) for name in _SAMPLES},
            file=_mito(sample, *positions),
            overwrite=True,
            format_hint="mito",
            overwrite_scope="samples",
        )
    finally:
        await session.close()


def _admin_family(monkeypatch: pytest.MonkeyPatch, store: _SvStore | None = None) -> None:
    """The admin delete's own lookups of the sample and the family, answered directly."""

    async def sample_row(_session, sample_id):
        return {
            "sample_uuid": _SAMPLES[sample_id],
            "sample_id": sample_id,
            "family_uuid": _FAMILY,
            "family_id": "FAM",
        }

    async def rows_by_family(_session, family_uuids):
        return {family_uuid: [] for family_uuid in family_uuids}

    async def contexts(_session, **_kwargs):
        return [_family_context()]

    async def count(*_args, **_kwargs):
        return len(store.rows) if store is not None else 0

    monkeypatch.setattr(admin_service, "_sample_row_or_404", sample_row)
    monkeypatch.setattr(admin_service, "_sample_rows_by_family", rows_by_family)
    monkeypatch.setattr(admin_service, "_family_assembly_contexts", contexts)
    monkeypatch.setattr(admin_service, "count_family_structural_variants", count)


async def _admin_sv_delete(postgres: _Postgres, sample: str) -> dict[str, Any]:
    session = postgres.session(f"admin delete {sample}")
    try:
        return await admin_service.delete_sample_data_by_type(
            session,  # type: ignore[arg-type]
            sample,
            "structural_variants",
            True,
        )
    finally:
        await session.close()


# --- Two writers of one family --------------------------------------------------------


@pytest.mark.asyncio
async def test_two_sv_uploads_of_one_family_keep_both_samples_calls(sv_store) -> None:
    store = sv_store(
        [
            _record(_DEL, "sniffles", {"FATHER": "0/1"}),
            _record(_NEEDLR, "needlr", {"FATHER": "0/1", "MOTHER": "0/0", "PROBAND": "0/1"}),
        ]
    )
    postgres = _Postgres()

    results = await asyncio.gather(
        _sv_upload(postgres, "PROBAND", _sniffles_del("0/1")),
        _sv_upload(postgres, "MOTHER", _sniffles_del("1/1")),
    )

    assert [result["merged"] for result in results] == [1, 1]
    # Before: each upload wrote back the rows it had read before the other one wrote, so
    # the deletion was stored twice, once with each upload's call beside the father's.
    assert store.calls() == {
        ("needlr", _NEEDLR): [{"FATHER": "0/1", "MOTHER": "0/0", "PROBAND": "0/1"}],
        ("sniffles", _DEL): [{"FATHER": "0/1", "MOTHER": "1/1", "PROBAND": "0/1"}],
    }


@pytest.mark.asyncio
async def test_an_admin_sv_delete_and_an_upload_of_one_family_keep_each_others_change(
    sv_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = sv_store(
        [
            _record(_DEL, "sniffles", {"FATHER": "0/1", "PROBAND": "0/1"}),
            _record(_NEEDLR, "needlr", {"FATHER": "0/1", "MOTHER": "0/0", "PROBAND": "0/1"}),
        ]
    )
    _admin_family(monkeypatch, store)
    postgres = _Postgres()

    await asyncio.gather(
        _admin_sv_delete(postgres, "FATHER"),
        _sv_upload(postgres, "MOTHER", _sniffles_del("1/1")),
    )

    # Before: the upload wrote back the father's call it had read, or the admin delete
    # wrote back the family's rows without the mother's new call.
    assert store.calls() == {
        ("needlr", _NEEDLR): [{"MOTHER": "0/0", "PROBAND": "0/1"}],
        ("sniffles", _DEL): [{"MOTHER": "1/1", "PROBAND": "0/1"}],
    }


@pytest.mark.asyncio
async def test_two_per_sample_small_variant_uploads_of_one_family_keep_both_samples_calls(
    small_store,
) -> None:
    store = small_store([_entry(73, {"FATHER": "1/1"})])
    postgres = _Postgres()

    results = await asyncio.gather(
        _mito_upload(postgres, "PROBAND", 73, 3243),
        _mito_upload(postgres, "MOTHER", 73, 16519),
    )

    assert [result["inserted"] for result in results] == [2, 2]
    # Before: each upload merged its file into the rows it had read, and the second rewrite
    # removed the first upload's calls, or stored their variant twice.
    assert store.calls() == {
        73: [{"FATHER": "1/1", "MOTHER": "1/1", "PROBAND": "1/1"}],
        3243: [{"PROBAND": "1/1"}],
        16519: [{"MOTHER": "1/1"}],
    }


@pytest.mark.asyncio
async def test_two_whole_family_uploads_of_one_callset_leave_one_of_the_two_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _CallsetStore(_Interleaving())
    for name, fn in {
        "count_family_small_variants": store.count,
        "delete_family_small_variants": store.delete,
        "insert_small_variant_records": store.insert,
        "refresh_family_small_variant_summaries": _nothing,
    }.items():
        monkeypatch.setattr(variant_upload_service, name, fn)
    monkeypatch.setattr(annotation_manifest_service, "merge_vcf_header_provenance", _nothing)
    postgres = _Postgres()

    async def upload(name: str, file: UploadFile) -> dict[str, Any]:
        session = postgres.session(name)
        try:
            return await variant_upload_service.upload_family_small_variant_file(
                session,  # type: ignore[arg-type]
                context=_family_context(),
                sample_contexts={sample: _sample_context(sample) for sample in _SAMPLES},
                file=file,
                overwrite=True,
                format_hint="clair3",
            )
        finally:
            await session.close()

    first = [("clair3", "1-100-A-G", "0/1"), ("clair3", "1-200-C-T", "0/1")]
    second = [("clair3", "1-100-A-G", "1/1"), ("clair3", "1-300-G-A", "0/1")]
    await asyncio.gather(
        upload("first", _family_vcf((100, "A", "G", "0/1"), (200, "C", "T", "0/1"))),
        upload("second", _family_vcf((100, "A", "G", "1/1"), (300, "G", "A", "0/1"))),
    )

    # Before: both counted an empty callset and inserted, so every variant of both files
    # was stored, 1:100 twice with different genotypes.
    assert sorted(store.rows) in (sorted(first), sorted(second))


# --- Every writer holds its family's lock ---------------------------------------------


@pytest.mark.asyncio
async def test_the_sv_upload_holds_the_lock_from_its_read_until_its_commit(sv_store) -> None:
    postgres = _Postgres()
    sv_store([_record(_DEL, "sniffles", {"FATHER": "0/1"})], writers=1, postgres=postgres)

    await _sv_upload(postgres, "MOTHER", _sniffles_del("1/1"))

    assert postgres.trace("upload MOTHER") == [
        f"lock {_key(STRUCTURAL_VARIANTS)}",
        "read SVs",
        "rewrite SVs",
        "commit",
        "close",
    ]
    assert not postgres.lock(_key(STRUCTURAL_VARIANTS)).locked()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("delete", "variant_types"),
    [
        (
            lambda session: admin_service.delete_sample_data_by_type(session, "FATHER", "structural_variants", True),
            [STRUCTURAL_VARIANTS],
        ),
        (
            lambda session: admin_service.delete_family_data_by_type(session, "FAM", "small_variants", True),
            [SMALL_VARIANTS],
        ),
        (lambda session: admin_service.delete_sample_with_data(session, "FATHER", True), list(VARIANT_TYPES)),
        (lambda session: admin_service.delete_family_with_data(session, "FAM", True), list(VARIANT_TYPES)),
    ],
    ids=["sample-svs", "family-small-variants", "sample", "family"],
)
async def test_each_admin_delete_holds_its_familys_locks_from_its_first_read_until_its_commit(
    monkeypatch: pytest.MonkeyPatch, delete, variant_types: list[str]
) -> None:
    postgres = _Postgres()
    _admin_family(monkeypatch)
    for name, result in {
        "count_family_small_variants": 0,
        "count_family_structural_variants": 0,
        "fetch_family_small_variant_entries": [],
        "fetch_family_structural_variant_rows": [],
        "rewrite_family_small_variant_entries": None,
        "rewrite_family_structural_variants": None,
        "delete_family_small_variants": None,
        "delete_family_structural_variants": None,
        "delete_interval_tracks": None,
        "delete_interval_track_sources": 0,
        "purge_sample_managed_files": 0,
        "purge_family_managed_files": 0,
    }.items():
        monkeypatch.setattr(admin_service, name, _recorder(postgres, name, result))

    await delete(postgres.session("admin"))

    trace = postgres.trace("admin")
    locks = [f"lock {_key(variant_type)}" for variant_type in variant_types]
    assert trace[: len(locks)] == locks
    assert trace[-1] == "commit"
    storage = trace[len(locks) : -1]
    assert storage and not any(line.startswith("lock") or line == "commit" for line in storage)
    for variant_type in variant_types:
        assert not postgres.lock(_key(variant_type)).locked()


async def _no_known_samples(_session, **_kwargs) -> set[str]:
    return set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("importer", "dataset"),
    [
        (family_package_datasets._import_sv_needlr_dataset, ManifestDataset(family_vcf="sv/family.needlr.vcf")),
        (family_package_datasets._import_cnv_dataset, ManifestDataset(per_sample={"PROBAND": {"vcf": "cnv/PROBAND.vcf"}})),
    ],
    ids=["sv_needlr", "cnv"],
)
async def test_the_package_sv_datasets_hold_the_lock_until_their_sv_files_are_recorded(
    monkeypatch: pytest.MonkeyPatch, importer, dataset: ManifestDataset
) -> None:
    postgres = _Postgres()
    records = [_record(_DEL, "needlr", {"PROBAND": "0/1"})]

    async def record_files(session, **_kwargs) -> None:
        postgres.log.append(("storage", "record SV files"))
        await session.commit()

    for name, fn in {
        "_resolve_package_path": lambda _root, value: Path(value) if value else None,
        # A HiFiCNV file's one column is its sample slot, which binds to the entry's sample.
        "_read_package_text": lambda _path: "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSample0\n",
        "known_vcf_sample_ids": _no_known_samples,
        "_iter_needlr_structural_records": lambda *_args, **_kwargs: records,
        "_iter_cnv_structural_records": lambda *_args, **_kwargs: records,
        "replace_family_structural_variants": _recorder(postgres, "replace SVs"),
        "_update_sv_file_metadata": record_files,
    }.items():
        monkeypatch.setattr(family_package_datasets, name, fn)
    monkeypatch.setattr(annotation_manifest_service, "merge_vcf_header_provenance", _nothing)
    job = DatasetImportJob(
        session=postgres.session("import"),  # type: ignore[arg-type]
        bundle=SimpleNamespace(root=Path("/package"), ped=None, source_uri=None),  # type: ignore[arg-type]
        dataset=dataset,
        summary=FamilyImportDatasetSummary(dataset_type="sv", status="valid"),
        family_context=_family_context(),
        sample_contexts={name: _sample_context(name) for name in _SAMPLES},
    )

    summary = await importer(job)

    assert summary.status == "imported"
    assert postgres.trace("import")[:4] == [
        f"lock {_key(STRUCTURAL_VARIANTS)}",
        "replace SVs",
        "record SV files",
        "commit",
    ]


@pytest.mark.asyncio
async def test_removing_a_samples_mitochondrial_calls_holds_the_lock_from_its_read(small_store) -> None:
    postgres = _Postgres()
    small_store([_entry(73, {"MOTHER": "1/1", "PROBAND": "1/1"})], postgres=postgres)
    session = postgres.session("import")

    removed = await variant_upload_service.remove_family_small_variant_sample_calls(
        session,  # type: ignore[arg-type]
        _family_context(),
        [_sample_context("PROBAND")],
        source="mito",
    )

    assert removed == 1
    # Held until the import commits.
    assert postgres.trace("import") == [f"lock {_key(SMALL_VARIANTS)}", "read small variants"]
    assert session.held == [_key(SMALL_VARIANTS)]


@pytest.mark.asyncio
async def test_a_ped_overwrite_locks_each_replaced_family_in_one_order_before_deleting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    postgres = _Postgres()

    async def existing_rows(_session, _family_ids):
        return [
            {"family_uuid": "uuid-b", "family_id": "FAM_B", "project_id": "p", "assembly_name": "GRCh38"},
            {"family_uuid": "uuid-a", "family_id": "FAM_A", "project_id": "p", "assembly_name": "GRCh38"},
        ]

    monkeypatch.setattr(ped_service, "_existing_family_rows", existing_rows)
    monkeypatch.setattr(ped_service, "delete_family_small_variants", _recorder(postgres, "delete small variants"))
    monkeypatch.setattr(ped_service, "delete_family_structural_variants", _recorder(postgres, "delete SVs"))

    await ped_service._replace_existing_families(
        postgres.session("ped"),  # type: ignore[arg-type]
        ["FAM_B", "FAM_A"],
        True,
        _admin_user(),
    )

    trace = postgres.trace("ped")
    assert trace[:4] == [
        f"lock {_key(SMALL_VARIANTS, 'uuid-a')}",
        f"lock {_key(STRUCTURAL_VARIANTS, 'uuid-a')}",
        f"lock {_key(SMALL_VARIANTS, 'uuid-b')}",
        f"lock {_key(STRUCTURAL_VARIANTS, 'uuid-b')}",
    ]
    assert trace[4:] == ["delete small variants", "delete SVs"] * 2


@pytest.mark.asyncio
async def test_clearing_a_familys_data_takes_both_locks_before_deleting(monkeypatch: pytest.MonkeyPatch) -> None:
    postgres = _Postgres()

    async def counts(*_args, **_kwargs):
        return {}

    async def groups(*_args, **_kwargs):
        return {"GRCh38": ["project-uuid"]}

    monkeypatch.setattr(family_structure_service, "_family_genomic_data_counts", counts)
    monkeypatch.setattr(family_structure_service, "_family_assembly_groups", groups)
    for name in (
        "delete_family_small_variants",
        "delete_family_structural_variants",
        "delete_interval_tracks",
        "delete_interval_track_sources",
    ):
        monkeypatch.setattr(family_structure_service, name, _recorder(postgres, name))

    await family_structure_service._clear_family_genomic_data(
        postgres.session("structure"),  # type: ignore[arg-type]
        family_uuid=_FAMILY,
    )

    assert postgres.trace("structure") == [
        f"lock {_key(SMALL_VARIANTS)}",
        f"lock {_key(STRUCTURAL_VARIANTS)}",
        "delete_family_small_variants",
        "delete_family_structural_variants",
        "delete_interval_tracks",
        "delete_interval_track_sources",
    ]


@pytest.mark.asyncio
async def test_an_import_shell_delete_takes_both_locks_before_deleting(monkeypatch: pytest.MonkeyPatch) -> None:
    postgres = _Postgres()
    for name in ("delete_family_small_variants", "delete_family_structural_variants", "delete_interval_tracks"):
        monkeypatch.setattr(family_package_registration, name, _recorder(postgres, name))

    await family_package_registration._delete_family_shell(
        postgres.session("import"),  # type: ignore[arg-type]
        _family_context(),
    )

    assert postgres.trace("import") == [
        f"lock {_key(SMALL_VARIANTS)}",
        f"lock {_key(STRUCTURAL_VARIANTS)}",
        "delete_family_small_variants",
        "delete_family_structural_variants",
        "delete_interval_tracks",
    ]


# --- The lock --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_write_that_needs_both_locks_takes_the_small_variant_lock_first() -> None:
    postgres = _Postgres()

    await lock_family_variant_writes(
        postgres.session("writer"),  # type: ignore[arg-type]
        _FAMILY,
        [STRUCTURAL_VARIANTS, SMALL_VARIANTS],
    )

    # One order for every writer: two that each need both never hold one and wait for the
    # other. The key is bound, not written into the statement.
    assert postgres.lock_statements == [
        ("SELECT pg_advisory_xact_lock(hashtext(:k))", {"k": _key(SMALL_VARIANTS)}),
        ("SELECT pg_advisory_xact_lock(hashtext(:k))", {"k": _key(STRUCTURAL_VARIANTS)}),
    ]
    assert _key(SMALL_VARIANTS) == "family-variant-writes:small_variants:family-uuid"
    with pytest.raises(ValueError):
        await lock_family_variant_writes(postgres.session("writer"), _FAMILY, ["snv"])  # type: ignore[arg-type]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("deleted", "detail"),
    [("sample", "Sample not found"), ("family", "Family not found")],
)
async def test_an_upload_that_waited_for_a_delete_of_its_family_or_sample_writes_nothing(
    sv_store, deleted: str, detail: str
) -> None:
    postgres = _Postgres()
    store = sv_store([_record(_DEL, "sniffles", {"FATHER": "0/1", "MOTHER": "0/1"})], writers=1)
    stored = store.calls()
    # An admin delete of the mother (or the family) holds the family's locks.
    admin = postgres.session("admin")
    await lock_family_variant_writes(admin, _FAMILY, VARIANT_TYPES)  # type: ignore[arg-type]

    upload = asyncio.create_task(_sv_upload(postgres, "MOTHER", _sniffles_del("1/1")))
    await asyncio.sleep(0.05)
    assert not upload.done() and store.interleaving.reads == 0

    if deleted == "sample":
        postgres.samples[_FAMILY].discard(_SAMPLES["MOTHER"])
    else:
        del postgres.samples[_FAMILY]
    await admin.commit()

    with pytest.raises(HTTPException) as exc:
        await upload
    assert (exc.value.status_code, exc.value.detail) == (404, detail)
    # Before: the upload read and wrote the mother's calls back for a sample or a family
    # that no longer exists.
    assert store.interleaving.reads == 0
    assert store.calls() == stored


def _write_package(root: Path) -> None:
    root.mkdir(parents=True)
    (root / "family.ped").write_text(
        "FAM FATHER 0 0 1 1\nFAM MOTHER 0 0 2 1\nFAM PROBAND FATHER MOTHER 1 2\n", encoding="utf-8"
    )
    (root / "manifest.yaml").write_text(
        "schema_version: 1\nfamily_id: FAM\nped: family.ped\n", encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_the_package_import_holds_the_familys_locks_from_its_snapshot_until_its_restore(
    sv_store, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    postgres = _Postgres()
    monkeypatch.setattr(
        family_variant_write_lock, "get_postgres_sessionmaker", lambda: lambda: postgres.session("import lock")
    )
    stored = [_record(_DEL, "sniffles", {"FATHER": "0/1"})]
    store = sv_store(stored, writers=1, postgres=postgres)
    import_running = asyncio.Event()

    async def upload_during_the_import() -> dict[str, Any]:
        # A request of its own, as the server runs it: not a task of the import.
        await import_running.wait()
        return await _sv_upload(postgres, "MOTHER", _sniffles_del("1/1"))

    upload = asyncio.create_task(upload_during_the_import())

    async def ensure_family(_session, **_kwargs):
        return _family_context(), False

    async def no_warnings(*_args, **_kwargs):
        return []

    def step(name: str, result: Any = None):
        async def record(*_args, **_kwargs):
            postgres.log.append(("storage", name))
            return result

        return record

    async def failing_dataset(_session, *, summary, **_kwargs):
        postgres.log.append(("storage", "dataset"))
        import_running.set()
        await asyncio.sleep(0.05)
        assert not upload.done() and store.interleaving.reads == 0
        raise RuntimeError("ClickHouse insert failed")

    async def restore(_snapshot):
        postgres.log.append(("storage", "restore"))
        store.rows = [StoredStructuralVariantRow(project_id="project-uuid", record=r) for r in copy.deepcopy(stored)]

    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path)])
    for name, fn in {
        "_ensure_family_from_ped": ensure_family,
        "_existing_package_entity_warnings": no_warnings,
        "_enabled_dataset_summaries": lambda _validation: [
            FamilyImportDatasetSummary(dataset_type="snv", status="valid")
        ],
        "_import_dataset": failing_dataset,
        "snapshot_family_postgres_state": step("snapshot", {"rows": []}),
        "snapshot_family_clickhouse_state": step("snapshot ClickHouse", object()),
        "restore_family_clickhouse_state": restore,
        "restore_family_postgres_state": step("restore Postgres"),
        "discard_family_clickhouse_snapshot": step("discard snapshot"),
    }.items():
        monkeypatch.setattr(family_package_import, name, fn)
    _write_package(tmp_path / "FAM")

    result = await family_package_import.execute_family_package_import(
        postgres.session("import"),  # type: ignore[arg-type]
        folder_path=tmp_path / "FAM",
        project_id="project-uuid",
        dry_run=False,
        user=_admin_user(),
        conflict_mode="overwrite",
    )
    await asyncio.wait_for(upload, timeout=1)

    assert result.error is not None and "snv" in result.error
    trace = postgres.trace("import lock", "upload MOTHER")
    assert trace[: trace.index("rewrite SVs") + 1] == [
        f"lock {_key(SMALL_VARIANTS)}",
        f"lock {_key(STRUCTURAL_VARIANTS)}",
        "snapshot",
        "snapshot ClickHouse",
        "dataset",
        "restore",
        "restore Postgres",
        "discard snapshot",
        "rollback",
        "close",
        f"lock {_key(STRUCTURAL_VARIANTS)}",
        "read SVs",
        "rewrite SVs",
    ]
    # Before: the upload wrote the mother's call while the import ran, and the restore put
    # back the rows as they were before the import, without it.
    assert store.calls() == {("sniffles", _DEL): [{"FATHER": "0/1", "MOTHER": "1/1"}]}


@pytest.mark.asyncio
async def test_writers_inside_the_imports_hold_take_no_lock_of_their_own(monkeypatch: pytest.MonkeyPatch) -> None:
    postgres = _Postgres()
    monkeypatch.setattr(
        family_variant_write_lock, "get_postgres_sessionmaker", lambda: lambda: postgres.session("import lock")
    )
    loader = postgres.session("loader")
    hold_started = asyncio.Event()

    async def other_writer() -> None:
        # A request of its own, as the server runs it: not a task of the import.
        await hold_started.wait()
        await lock_family_variant_writes(postgres.session("other"), _FAMILY, [STRUCTURAL_VARIANTS])  # type: ignore[arg-type]

    other = asyncio.create_task(other_writer())

    async with hold_family_variant_writes(_FAMILY, samples=list(_SAMPLES.values())):
        hold_started.set()
        # The import's own dataset loaders, on the import's session: nothing to wait for.
        await asyncio.wait_for(
            lock_family_variant_writes(loader, _FAMILY, VARIANT_TYPES, samples=[_SAMPLES["MOTHER"]]),  # type: ignore[arg-type]
            timeout=1,
        )
        assert loader.held == []
        # Another family's writes are not the import's.
        await lock_family_variant_writes(loader, "other-family", [SMALL_VARIANTS])  # type: ignore[arg-type]
        assert loader.held == [_key(SMALL_VARIANTS, "other-family")]
        # Every other writer of the family waits for the import.
        await asyncio.sleep(0.05)
        assert not other.done()
        # A task the import starts inherits the hold only while the block lasts.
        block_ended = asyncio.Event()

        async def later_writer() -> None:
            await block_ended.wait()
            await lock_family_variant_writes(postgres.session("later"), _FAMILY, [SMALL_VARIANTS])  # type: ignore[arg-type]

        later = asyncio.create_task(later_writer())

    block_ended.set()
    await asyncio.wait_for(other, timeout=1)
    await asyncio.wait_for(later, timeout=1)
    trace = postgres.trace("import lock", "other", "later")
    released = trace.index("rollback")
    assert sorted(line for line in trace[released:] if line.startswith("lock")) == [
        f"lock {_key(SMALL_VARIANTS)}",
        f"lock {_key(STRUCTURAL_VARIANTS)}",
    ]


@pytest.mark.asyncio
async def test_the_imports_hold_refuses_a_family_whose_sample_is_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    postgres = _Postgres()
    monkeypatch.setattr(
        family_variant_write_lock, "get_postgres_sessionmaker", lambda: lambda: postgres.session("import lock")
    )
    postgres.samples[_FAMILY].discard(_SAMPLES["MOTHER"])

    with pytest.raises(HTTPException) as exc:
        async with hold_family_variant_writes(_FAMILY, samples=list(_SAMPLES.values())):
            raise AssertionError("the import must not run")

    assert (exc.value.status_code, exc.value.detail) == (404, "Sample not found")
    for variant_type in VARIANT_TYPES:
        assert not postgres.lock(_key(variant_type)).locked()


@pytest.mark.asyncio
async def test_a_hold_inside_a_hold_of_the_same_family_takes_no_second_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    postgres = _Postgres()
    connections: list[str] = []

    def sessionmaker():
        def open_session() -> _Session:
            connections.append("import lock")
            return postgres.session("import lock")

        return open_session

    monkeypatch.setattr(family_variant_write_lock, "get_postgres_sessionmaker", sessionmaker)

    async with hold_family_variant_writes(_FAMILY):
        # A second connection would wait for the first one's locks, for ever.
        async with asyncio.timeout(1):
            async with hold_family_variant_writes(_FAMILY):
                pass

    assert connections == ["import lock"]


@pytest.mark.asyncio
async def test_a_hold_whose_connection_is_lost_keeps_the_blocks_outcome(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    postgres = _Postgres()
    lost = postgres.session("import lock")

    async def connection_lost() -> None:
        raise ConnectionError("server closed the connection")

    lost.rollback = connection_lost  # type: ignore[method-assign]
    monkeypatch.setattr(family_variant_write_lock, "get_postgres_sessionmaker", lambda: lambda: lost)

    with caplog.at_level("WARNING", logger=family_variant_write_lock.__name__):
        async with hold_family_variant_writes(_FAMILY):
            pass

    # The import that ran in the block is not failed after the fact; the lost connection
    # took its locks with it (here: the session's close).
    assert "Could not end the variant-write lock transaction" in caplog.text
    assert lost.held == []


# --- The sign-out, the one reader that shares the locks -------------------------------

_SHARE_SQL = "SELECT pg_try_advisory_xact_lock_shared(hashtext(:k))"


@pytest.mark.asyncio
async def test_a_write_started_during_a_sign_out_waits_for_the_sign_out_to_end() -> None:
    postgres = _Postgres()
    share = family_variant_write_lock.try_share_family_variant_writes
    signout = postgres.session("sign-out")

    assert await share(signout, _FAMILY) is True  # type: ignore[arg-type]
    # Sign-outs do not keep each other out.
    other_signout = postgres.session("other sign-out")
    assert await share(other_signout, _FAMILY) is True  # type: ignore[arg-type]
    await other_signout.rollback()
    upload = asyncio.create_task(
        lock_family_variant_writes(postgres.session("upload"), _FAMILY, [STRUCTURAL_VARIANTS])  # type: ignore[arg-type]
    )
    await asyncio.sleep(0.05)
    assert not upload.done(), "a write must not start while a sign-out reads the family"
    # Another family's writes are not held up.
    await asyncio.wait_for(
        lock_family_variant_writes(postgres.session("elsewhere"), "other-family", VARIANT_TYPES),  # type: ignore[arg-type]
        timeout=1,
    )

    await signout.commit()
    await asyncio.wait_for(upload, timeout=1)
    assert postgres.trace("sign-out", "upload") == [
        f"share {_key(SMALL_VARIANTS)}",
        f"share {_key(STRUCTURAL_VARIANTS)}",
        "commit",
        f"lock {_key(STRUCTURAL_VARIANTS)}",
    ]
    # Shared and without waiting, one statement per key, small variants first.
    assert [(sql, params["k"]) for sql, params in postgres.lock_statements if "shared" in sql] == [
        (_SHARE_SQL, _key(SMALL_VARIANTS)),
        (_SHARE_SQL, _key(STRUCTURAL_VARIANTS)),
    ] * 2


@pytest.mark.asyncio
async def test_a_sign_out_is_refused_while_the_import_holds_the_familys_locks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    postgres = _Postgres()
    monkeypatch.setattr(
        family_variant_write_lock, "get_postgres_sessionmaker", lambda: lambda: postgres.session("import lock")
    )
    share = family_variant_write_lock.try_share_family_variant_writes

    async with hold_family_variant_writes(_FAMILY):
        refused = postgres.session("sign-out")
        async with asyncio.timeout(1):  # refused at once, never waiting for the import
            assert await share(refused, _FAMILY) is False  # type: ignore[arg-type]
        await refused.rollback()

    after = postgres.session("sign-out after")
    assert await share(after, _FAMILY) is True  # type: ignore[arg-type]
    assert postgres.trace("sign-out", "sign-out after") == [
        f"share refused {_key(SMALL_VARIANTS)}",
        "rollback",
        f"share {_key(SMALL_VARIANTS)}",
        f"share {_key(STRUCTURAL_VARIANTS)}",
    ]


@pytest.mark.asyncio
async def test_a_sign_out_refused_on_one_lock_keeps_the_other_until_its_transaction_ends() -> None:
    postgres = _Postgres()
    share = family_variant_write_lock.try_share_family_variant_writes
    upload = postgres.session("SV upload")
    await lock_family_variant_writes(upload, _FAMILY, [STRUCTURAL_VARIANTS])  # type: ignore[arg-type]

    signout = postgres.session("sign-out")
    assert await share(signout, _FAMILY) is False  # type: ignore[arg-type]
    small = asyncio.create_task(
        lock_family_variant_writes(postgres.session("small upload"), _FAMILY, [SMALL_VARIANTS])  # type: ignore[arg-type]
    )
    await asyncio.sleep(0.05)
    assert not small.done()
    await signout.rollback()  # the caller ends its transaction with the refusal
    await asyncio.wait_for(small, timeout=1)
    await upload.commit()
    assert postgres.trace("sign-out", "small upload") == [
        f"share {_key(SMALL_VARIANTS)}",
        f"share refused {_key(STRUCTURAL_VARIANTS)}",
        "rollback",
        f"lock {_key(SMALL_VARIANTS)}",
    ]
