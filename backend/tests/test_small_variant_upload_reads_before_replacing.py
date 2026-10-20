"""A small-variant upload with overwrite reads and checks the whole file before it deletes
the callset it replaces (SAFE-25).

Before, the upload deleted the family's rows of its callset, and for imputed genotypes the
family's haplotype blocks, and only then read the file: a file it refused (a sample the
family does not have, no record it could store, a gzip file cut short, a line over the
limit) answered 400 or 413, or a read error 500, with the callset and the blocks gone.
Now a first read checks the whole file, building each record and the blocks as the write
does and storing nothing; only then is the callset deleted and the file read again and
written. A write that fails after that delete (a ClickHouse insert error) leaves the
callset without its rows: the upload's clean-up removes what it wrote, and the family is
flagged import-incomplete for what the upload was to bring, until it is brought again.

The upload runs through its route (``POST /families/{family_id}/small-variants/upload``)
against fakes at the stores' edge: ClickHouse (the callsets' rows and the haplotype
blocks), written at once and kept whatever becomes of the request; and Postgres (the family
row's import state, the haplotype blocks' records and phase corrections), whose writes are
the request's transaction's until it commits. What a reader sees afterwards is the
ClickHouse rows and Postgres's committed state.
"""

from __future__ import annotations

import copy
import gzip
import json
from io import BytesIO
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException, UploadFile
import pytest

from app.routers import families_small_variants as snv_router
from app.services import annotation_manifest_service, family_package_registration, variant_upload_service
from app.services.family_metadata_context import FamilyMetadataContext

_SAMPLE_ROWS = [
    {"sample_uuid": "uuid-S1", "sample_id": "S1", "sex": "female", "role": "proband"},
    {"sample_uuid": "uuid-S2", "sample_id": "S2", "sex": "male", "role": "sibling"},
]
_FAMILY = FamilyMetadataContext(
    family_uuid="family-uuid",
    family_id="F1",
    project_ids=["project-1"],
    sample_rows=_SAMPLE_ROWS,
    sample_uuid_to_name={"uuid-S1": "S1", "uuid-S2": "S2"},
    sample_name_to_uuid={"S1": "uuid-S1", "S2": "uuid-S2"},
    affected_sample_names=["S1"],
    assembly_id="assembly-1",
    assembly_name="GRCh38",
)
_HEADER = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\tS2\n"


def _calls(count: int, *, start: int = 1000) -> str:
    """``count`` records on chr1 of the directly called callset."""
    return "".join(
        f"1\t{start + index * 10}\t.\tA\tG\t50\tPASS\t.\tGT:DP\t0/1:30\t0/0:31\n" for index in range(count)
    )


def _imputed(count: int, *, start: int = 1000) -> str:
    """``count`` phased records on chr1 of the imputed callset, in one phase set."""
    return "".join(
        f"1\t{start + index * 10}\t.\tC\tT\t.\tPASS\t.\tGT:PS\t0|1:{start}\t1|0:{start}\n"
        for index in range(count)
    )


def _cut_short(text: str) -> bytes:
    """``text`` gzipped, its last bytes lost: an interrupted copy."""
    whole = gzip.compress(text.encode())
    return whole[: len(whole) - 12]


# The family's data before each upload: the two callsets' rows, and the haplotype blocks
# the imputed callset made (their rows, Postgres's record of them, and the parents' phase
# corrections).
_STORED_ROWS = {"clair3": ["stored-clair3-1", "stored-clair3-2"], "glimpse2": ["stored-glimpse2-1"]}
_STORED_BLOCK = {"sample_id": "uuid-S1", "track_type": "haplotype", "source": "glimpse2", "start": 1}
_STORED_RECORD = {"sample": "uuid-S1", "source": "glimpse2", "filename": "imputed-before.vcf"}
_STORED_CORRECTIONS = [{"parent": "father", "chr": "1", "start": 5}]
_IMPORTED = {"haplotypes": [{"source": None, "samples": None}], "snv": [{"source": "clair3", "samples": None}]}


class _ClickHouse:
    """The family's rows in ClickHouse: written at once, kept whatever becomes of the request.
    ``events`` is what the upload did, in order, its reads of the file included."""

    def __init__(self) -> None:
        self.rows: dict[str, list[str]] = copy.deepcopy(_STORED_ROWS)
        self.blocks: list[dict[str, Any]] = [dict(_STORED_BLOCK)]
        self.events: list[tuple[Any, ...]] = []
        self.inserts = 0
        # The insert (counted from 1) that fails, as ClickHouse failing mid-write would.
        self.failing_insert: int | None = None

    async def count(self, _assembly, _family, *, project_ids=None, source=None) -> int:
        return len(self.rows.get(source, []))

    async def delete(self, _assembly, _family, *, source=None) -> None:
        self.events.append(("delete", source))
        self.rows[source] = []

    async def insert(self, _assembly, _family, _projects, records, **_kwargs) -> None:
        self.inserts += 1
        if self.inserts == self.failing_insert:
            raise RuntimeError("ClickHouse insert failed")
        self.events.append(("insert", records[0].source, len(records)))
        for record in records:
            self.rows.setdefault(record.source, []).append(record.variant_id)

    async def presence(self, _session, *, context, track_type, chromosomes) -> set[str]:
        return {context.sample_uuid_to_name[block["sample_id"]] for block in self.blocks}

    async def delete_blocks(self, _assembly, *, family_uuid, track_type, source=None) -> None:
        self.events.append(("delete blocks", source))
        self.blocks = [block for block in self.blocks if source is not None and block["source"] != source]

    async def insert_blocks(self, _assembly, rows) -> None:
        self.events.append(("insert blocks", len(rows)))
        self.blocks.extend(rows)


class _Postgres:
    """The family's Postgres state as the upload reads and writes it: the family row's import
    state (``flag``: import_incomplete, ``unfinished``: import_unfinished, ``imported``:
    imported_scopes), the haplotype blocks' records and the phase corrections. A write is the
    transaction's until it commits; a rollback discards it."""

    def __init__(self, *, flag=None, unfinished=None) -> None:
        self.committed: dict[str, Any] = {
            "flag": flag,
            "unfinished": unfinished,
            "imported": copy.deepcopy(_IMPORTED),
            "records": [dict(_STORED_RECORD)],
            "corrections": copy.deepcopy(_STORED_CORRECTIONS),
        }
        self.working = copy.deepcopy(self.committed)
        self.commits = 0

    async def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        params = params or {}
        if sql.startswith("SELECT") and "FROM families" in sql:
            row = {
                "family_uuid": "family-uuid",
                "flag": copy.deepcopy(self.working["flag"]),
                "unfinished": copy.deepcopy(self.working["unfinished"]),
                "imported_scopes": copy.deepcopy(self.working["imported"]),
            }
            return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: row))
        assert sql.startswith("UPDATE families"), sql
        if set(params) == {"family_uuid", "import_key"}:  # an entry removed alone: a mark
            unfinished = dict(self.working["unfinished"] or {})
            unfinished.pop(params["import_key"], None)
            self.working["unfinished"] = unfinished or None
            return SimpleNamespace(rowcount=1)
        recorded = json.loads(params.get("imported_scopes", "null"))
        if recorded is not None:  # a JSON null leaves it as it is
            self.working["imported"] = recorded
        if "unfinished" in params:
            self.working["unfinished"] = json.loads(params["unfinished"])
        if "flag" in params:
            self.working["flag"] = json.loads(params["flag"])
        return SimpleNamespace(rowcount=1)

    async def commit(self) -> None:
        self.committed = copy.deepcopy(self.working)
        self.commits += 1

    async def rollback(self) -> None:
        self.working = copy.deepcopy(self.committed)


class _MarkConnection:
    """The connection the upload writes its mark on before its first ClickHouse write
    (``family_package_registration.mark_family_upload``, SAFE-28): committed at once, so
    the upload's transaction sees it, and its rollback keeps it."""

    def __init__(self, database: _Postgres) -> None:
        self.database = database

    async def __aenter__(self) -> "_MarkConnection":
        return self

    async def __aexit__(self, *_exc) -> bool:
        return False

    async def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        assert sql.startswith("UPDATE families"), sql
        for state in (self.database.committed, self.database.working):
            state["unfinished"] = {
                **(state["unfinished"] or {}),
                params["import_key"]: json.loads(params["entry"]),
            }
        return SimpleNamespace(rowcount=1)

    async def commit(self) -> None:
        return None


@pytest.fixture
def stores(monkeypatch):
    """The stores the upload writes, faked at their edge, and the file's reads recorded:
    ``("read",)`` as a read of the file begins, ``("read to its end",)`` as it ends."""
    clickhouse = _ClickHouse()
    real_lines = variant_upload_service._iter_upload_text_lines

    def recorded_lines(file, *, kind):
        clickhouse.events.append(("read",))
        yield from real_lines(file, kind=kind)
        clickhouse.events.append(("read to its end",))

    async def lock(*_args, **_kwargs) -> None:
        clickhouse.events.append(("lock",))

    async def nothing(*_args, **_kwargs) -> None:
        return None

    async def sizes(*_args, **_kwargs) -> dict[str, int]:
        return {"1": 10_000_000}

    async def delete_records(session, *, family_uuid, track_type, source=None) -> None:
        session.working["records"] = [
            record for record in session.working["records"] if source is not None and record["source"] != source
        ]

    async def upsert_record(session, *, sample_context, track_type, source, filename, row_count, metadata) -> None:
        session.working["records"].append(
            {"sample": sample_context.sample_uuid, "source": source, "filename": filename}
        )

    async def corrections(session, *, family_uuid, corrections) -> None:
        session.working["corrections"] = [correction.as_metadata() for correction in corrections]

    async def family_context(*_args, **_kwargs) -> FamilyMetadataContext:
        return _FAMILY

    patches = {
        "_iter_upload_text_lines": recorded_lines,
        "lock_family_variant_writes": lock,
        "count_family_small_variants": clickhouse.count,
        "delete_family_small_variants": clickhouse.delete,
        "insert_small_variant_records": clickhouse.insert,
        "refresh_family_small_variant_summaries": nothing,
        "get_track_presence_by_sample": clickhouse.presence,
        "_fetch_chromosome_sizes": sizes,
        "delete_interval_tracks": clickhouse.delete_blocks,
        "insert_interval_track_rows": clickhouse.insert_blocks,
        "delete_interval_track_sources": delete_records,
        "upsert_interval_track_source": upsert_record,
        "record_haplotype_phase_corrections": corrections,
    }
    for name, value in patches.items():
        monkeypatch.setattr(variant_upload_service, name, value)
    monkeypatch.setattr(annotation_manifest_service, "merge_vcf_header_provenance", nothing)
    monkeypatch.setattr(snv_router, "build_family_metadata_context", family_context)
    monkeypatch.setattr(snv_router, "record_upload_file_obj", nothing)
    return clickhouse


async def _upload(
    database: _Postgres,
    body: str | bytes,
    *,
    callset: str = "clair3",
    overwrite: bool = True,
    name: str = "family.vcf",
):
    """The single-file upload of ``body`` through its route; its mark is written on a
    connection of its own (``_MarkConnection``)."""
    data = body.encode() if isinstance(body, str) else body
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            family_package_registration, "get_postgres_sessionmaker", lambda: lambda: _MarkConnection(database)
        )
        return await snv_router.upload_family_small_variants(
            family_id="F1",
            file=UploadFile(file=BytesIO(data), filename=name),
            overwrite=overwrite,
            source_format=callset,  # type: ignore[arg-type]
            session=database,  # type: ignore[arg-type]
            user=SimpleNamespace(id="admin", username="admin"),
        )


def _as_stored_before(clickhouse: _ClickHouse, database: _Postgres) -> None:
    """The family as it was before the upload: both callsets, the blocks, their record and
    phase corrections, what the family holds, and no flag."""
    assert clickhouse.rows == _STORED_ROWS
    assert clickhouse.blocks == [_STORED_BLOCK]
    assert database.committed == {
        "flag": None,
        "unfinished": None,
        "imported": _IMPORTED,
        "records": [_STORED_RECORD],
        "corrections": _STORED_CORRECTIONS,
    }


def _a_records_annotation_fails(monkeypatch) -> None:
    """The third record's annotation cannot be read (an error, not a refusal)."""
    real = variant_upload_service.extract_small_variant_annotations
    seen = {"records": 0}

    def annotation(info, state):
        seen["records"] += 1
        if seen["records"] == 3:
            raise ValueError("unreadable annotation")
        return real(info, state)

    monkeypatch.setattr(variant_upload_service, "extract_small_variant_annotations", annotation)


# Files the upload refuses, or cannot read, each with what it answers. The fault is in the
# header, at the end of the file, or in a record after the first ones: before, each
# answered with the callset deleted, and the records before the fault stored and removed.
_INVALID_FILES = {
    "a sample the family does not have": (
        _HEADER.replace("\tS2\n", "\tS9\n") + _calls(3),
        400,
        "Sample 'S9' not found in family",
    ),
    # Each record's POS unreadable.
    "no record it can store": (
        _HEADER + _calls(3).replace("1\t10", "1\tx10"),
        400,
        "No valid small-variant records found",
    ),
    "a gzip file cut short after its first records": (
        _cut_short(_HEADER + _calls(400)),
        400,
        "VCF file must be plain text or gzipped",
    ),
    "a line over the limit after its first records": (
        _HEADER + _calls(3) + "1\t2000\t.\tA\tG\t50\tPASS\t" + "X" * 300 + "\tGT\t0/1\t0/0\n",
        413,
        "VCF file contains a line exceeding the maximum allowed length",
    ),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", sorted(_INVALID_FILES))
async def test_an_invalid_file_uploaded_with_overwrite_leaves_the_callset_as_it_was(
    stores, monkeypatch, case
) -> None:
    monkeypatch.setattr(variant_upload_service, "MAX_UPLOAD_LINE_BYTES", 200)
    body, status, detail = _INVALID_FILES[case]
    database = _Postgres()

    with pytest.raises(HTTPException) as refused:
        await _upload(database, body, name="family.vcf.gz" if isinstance(body, bytes) else "family.vcf")

    assert (refused.value.status_code, refused.value.detail) == (status, detail)
    # Before: the clair3 rows deleted first, and what was read of the file stored and then
    # removed again (a gzip file cut short even answered 500).
    assert not [event for event in stores.events if event[0] in {"delete", "insert"}]
    _as_stored_before(stores, database)
    assert database.commits == 0


@pytest.mark.asyncio
async def test_a_record_that_cannot_be_read_leaves_the_callset_as_it_was(stores, monkeypatch) -> None:
    # Not a refusal: an error while building the third record. It is the check's, before
    # anything is deleted, and the upload answers with it.
    _a_records_annotation_fails(monkeypatch)
    database = _Postgres()

    with pytest.raises(ValueError, match="unreadable annotation"):
        await _upload(database, _HEADER + _calls(5))

    assert not [event for event in stores.events if event[0] in {"delete", "insert"}]
    _as_stored_before(stores, database)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [_HEADER, _HEADER + _calls(3).replace("1\t10", "1\tx10")],
    ids=["header-alone", "no-readable-record"],
)
async def test_a_mitochondrial_upload_without_a_record_leaves_the_mitochondrial_calls(stores, body) -> None:
    # The owner's decision (SAFE-29): a mitochondrial SNV file without a variant record is a
    # failed analysis, never "no mitochondrial variant". The upload of the family's chrM calls
    # (source_format=mito) reads its file whole before it replaces them, as every small-variant
    # upload does since SAFE-25, and refuses it with them as they were.
    stores.rows["mito"] = ["stored-mito-1"]
    database = _Postgres()

    with pytest.raises(HTTPException) as refused:
        await _upload(database, body, callset="mito")

    assert (refused.value.status_code, refused.value.detail) == (400, "No valid small-variant records found")
    assert stores.rows["mito"] == ["stored-mito-1"]
    assert not [event for event in stores.events if event[0] in {"delete", "insert"}]
    assert database.commits == 0


# Imputed genotypes the upload refuses.
_INVALID_IMPUTED = {
    "a gzip file cut short": _cut_short(_HEADER + _imputed(400)),
    "a sample the family does not have": _HEADER.replace("\tS2\n", "\tS9\n") + _imputed(3),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", sorted(_INVALID_IMPUTED))
async def test_invalid_imputed_genotypes_leave_the_callset_and_the_haplotype_blocks(stores, case) -> None:
    body = _INVALID_IMPUTED[case]
    database = _Postgres()

    with pytest.raises(HTTPException) as refused:
        await _upload(
            database, body, callset="glimpse2", name="imputed.vcf.gz" if isinstance(body, bytes) else "imputed.vcf"
        )

    assert refused.value.status_code == 400
    # Before: the imputed rows and the blocks' rows deleted; Postgres, whose delete the
    # error rolled back, still recorded the blocks.
    assert not [event for event in stores.events if event[0] in {"delete", "delete blocks", "insert"}]
    _as_stored_before(stores, database)


@pytest.mark.asyncio
async def test_a_valid_file_with_overwrite_is_read_whole_before_it_replaces_the_callset(stores) -> None:
    database = _Postgres()

    result = await _upload(database, _HEADER + _calls(3))

    assert result.inserted == 3 and result.source_format == "clair3"
    # Read to its end once, storing nothing; then the callset deleted, and the file read
    # again and written. Before: the delete came first, and the file was read once.
    assert stores.events == [
        ("lock",),
        ("read",),
        ("read to its end",),
        ("delete", "clair3"),
        ("read",),
        ("read to its end",),
        ("insert", "clair3", 3),
    ]
    # Replaced as before: the callset's rows, its record, in one commit; the imputed
    # callset and the blocks as they were.
    assert stores.rows["clair3"] == ["1-1000-A-G", "1-1010-A-G", "1-1020-A-G"]
    assert stores.rows["glimpse2"] == _STORED_ROWS["glimpse2"]
    assert stores.blocks == [_STORED_BLOCK]
    assert database.commits == 1
    assert database.committed["imported"] == _IMPORTED
    assert database.committed["flag"] is None


@pytest.mark.asyncio
async def test_valid_imputed_genotypes_with_overwrite_replace_the_callset_and_the_blocks(stores) -> None:
    database = _Postgres()

    result = await _upload(database, _HEADER + _imputed(4), callset="glimpse2")

    assert result.inserted == 4 and result.haplotypes_inserted == 2
    # The whole file checked (the blocks made, and dropped) before the imputed rows and the
    # blocks are deleted.
    deleted = stores.events.index(("delete", "glimpse2"))
    assert stores.events[:deleted] == [("lock",), ("read",), ("read to its end",)]
    assert stores.events[deleted:] == [
        ("delete", "glimpse2"),
        ("delete blocks", "glimpse2"),
        ("read",),
        ("read to its end",),
        ("insert", "glimpse2", 4),
        ("insert blocks", 2),
    ]
    assert len(stores.rows["glimpse2"]) == 4
    assert stores.rows["clair3"] == _STORED_ROWS["clair3"]
    # One block per sample, recorded; the phase corrections replaced with the blocks'.
    assert sorted(block["sample_id"] for block in stores.blocks) == ["uuid-S1", "uuid-S2"]
    assert sorted(record["sample"] for record in database.committed["records"]) == ["uuid-S1", "uuid-S2"]
    assert {record["filename"] for record in database.committed["records"]} == {"family.vcf"}
    assert database.committed["corrections"] == []
    assert database.commits == 1


@pytest.mark.asyncio
async def test_with_nothing_stored_to_replace_the_file_is_read_once(stores) -> None:
    stores.rows["clair3"] = []
    database = _Postgres()

    await _upload(database, _HEADER + _calls(2), overwrite=False)

    assert stores.events == [("lock",), ("read",), ("read to its end",), ("insert", "clair3", 2)]


@pytest.mark.asyncio
async def test_a_caller_that_puts_the_family_back_itself_reads_the_file_once(stores) -> None:
    # A package import with overwrite: its snapshot puts the family back when a dataset
    # fails, so its loader deletes and reads once (check_before_replacing=False).
    database = _Postgres()

    await variant_upload_service.upload_family_small_variant_file(
        database,  # type: ignore[arg-type]
        context=_FAMILY,
        sample_contexts=snv_router._family_sample_contexts(_FAMILY),
        file=UploadFile(file=BytesIO((_HEADER + _calls(2)).encode()), filename="family.vcf"),
        overwrite=True,
        format_hint="clair3",
        check_before_replacing=False,
    )

    assert stores.events == [
        ("lock",),
        ("delete", "clair3"),
        ("read",),
        ("read to its end",),
        ("insert", "clair3", 2),
    ]


@pytest.mark.asyncio
async def test_a_caller_is_told_whether_the_load_began_writing(stores) -> None:
    # A package import's clean-up deletes its callset after a failed load only once the load
    # began writing (SAFE-32): one refused before that -- here its VEP table, which has no
    # header -- replaced nothing, and the callset is as it was.
    database = _Postgres()
    refused = variant_upload_service.SmallVariantWriteState()

    with pytest.raises(HTTPException, match="missing a header row"):
        await variant_upload_service.upload_family_small_variant_file(
            database,  # type: ignore[arg-type]
            context=_FAMILY,
            sample_contexts=snv_router._family_sample_contexts(_FAMILY),
            file=UploadFile(file=BytesIO((_HEADER + _calls(2)).encode()), filename="family.vcf"),
            annotation_file=UploadFile(file=BytesIO(b"## ENSEMBL VARIANT EFFECT PREDICTOR\n"), filename="vep.tsv"),
            overwrite=True,
            format_hint="clair3",
            check_before_replacing=False,
            write_state=refused,
        )

    assert refused.began is False
    _as_stored_before(stores, database)

    loaded = variant_upload_service.SmallVariantWriteState()
    await variant_upload_service.upload_family_small_variant_file(
        database,  # type: ignore[arg-type]
        context=_FAMILY,
        sample_contexts=snv_router._family_sample_contexts(_FAMILY),
        file=UploadFile(file=BytesIO((_HEADER + _calls(2)).encode()), filename="family.vcf"),
        overwrite=True,
        format_hint="clair3",
        check_before_replacing=False,
        write_state=loaded,
    )
    assert loaded.began is True


# --- a write that fails after the delete -------------------------------------------------


@pytest.mark.asyncio
async def test_a_write_that_fails_after_the_delete_flags_the_family_for_the_callset(
    stores, monkeypatch
) -> None:
    monkeypatch.setattr(variant_upload_service, "SMALL_VARIANT_UPLOAD_BATCH_SIZE", 2)
    stores.failing_insert = 2  # ClickHouse fails after the first batch is stored
    database = _Postgres()

    with pytest.raises(RuntimeError, match="ClickHouse insert failed"):
        await _upload(database, _HEADER + _calls(5))

    # The file checked, the callset deleted, a first batch stored, and the clean-up's delete
    # of that batch after the second failed.
    assert stores.events == [
        ("lock",),
        ("read",),
        ("read to its end",),
        ("delete", "clair3"),
        ("read",),
        ("insert", "clair3", 2),
        ("delete", "clair3"),
    ]
    assert stores.rows["clair3"] == []
    assert stores.rows["glimpse2"] == _STORED_ROWS["glimpse2"]
    # The family does not read as complete: it is flagged import-incomplete for the callset
    # the upload was to bring, under no import job, in a commit of its own. What the upload
    # would have recorded is not (before: nothing marked the family).
    flag = database.committed["flag"]
    assert flag is not None
    assert (flag["failed_datasets"], flag["imported_datasets"]) == (["snv"], [])
    assert flag["failed_scopes"] == {"snv": [{"scope": {"source": "clair3", "samples": None}, "job_id": None}]}
    assert flag["failed_jobs"] == {"snv": None} and flag["job_id"] is None
    assert database.commits == 1
    assert database.committed["imported"] == _IMPORTED
    assert database.committed["unfinished"] is None


@pytest.mark.asyncio
async def test_the_flag_goes_once_the_callset_is_uploaded_again(stores, monkeypatch) -> None:
    stores.failing_insert = 1
    database = _Postgres()
    with pytest.raises(RuntimeError):
        await _upload(database, _HEADER + _calls(3))
    assert database.committed["flag"]["failed_datasets"] == ["snv"]

    # Another callset brings nothing of it: the flag stays.
    stores.failing_insert = None
    await _upload(database, _HEADER + _imputed(2), callset="glimpse2")
    assert database.committed["flag"]["failed_datasets"] == ["snv"]

    # The callset uploaded again (nothing of it is stored now, so without overwrite too):
    # the flag goes with the failure it named.
    await _upload(database, _HEADER + _calls(3), overwrite=False)
    assert database.committed["flag"] is None
    assert stores.rows["clair3"] == ["1-1000-A-G", "1-1010-A-G", "1-1020-A-G"]


@pytest.mark.asyncio
async def test_a_failed_write_of_imputed_genotypes_flags_the_haplotypes(stores) -> None:
    stores.failing_insert = 1
    database = _Postgres()

    with pytest.raises(RuntimeError, match="ClickHouse insert failed"):
        await _upload(database, _HEADER + _imputed(3), callset="glimpse2")

    # The imputed rows and the blocks' rows are gone; their Postgres record and the phase
    # corrections, whose delete the failed transaction rolled back, are not.
    assert stores.rows["glimpse2"] == [] and stores.blocks == []
    assert database.committed["records"] == [_STORED_RECORD]
    assert database.committed["corrections"] == _STORED_CORRECTIONS
    flag = database.committed["flag"]
    assert flag["failed_datasets"] == ["haplotypes"]
    assert flag["failed_scopes"] == {
        "haplotypes": [{"scope": {"source": None, "samples": None}, "job_id": None}]
    }


@pytest.mark.asyncio
async def test_a_failed_write_with_nothing_replaced_leaves_no_flag(stores) -> None:
    # Nothing was stored to delete: the clean-up leaves the family as it was.
    stores.rows["clair3"] = []
    stores.failing_insert = 1
    database = _Postgres()

    with pytest.raises(RuntimeError):
        await _upload(database, _HEADER + _calls(3), overwrite=False)

    assert stores.rows["clair3"] == []
    # The upload's one commit removes the mark it wrote before its first insert (SAFE-28):
    # nothing is missing or partly written.
    assert database.committed["flag"] is None and database.commits == 1
    assert database.committed["unfinished"] is None


@pytest.mark.asyncio
async def test_a_failed_write_keeps_the_entry_of_a_restore_that_did_not_finish(stores) -> None:
    # A restore that failed part-way listed the callset: the failed upload did not bring it
    # again, so it is not struck off, and the earlier failures of the flag stay with it.
    entry = {
        "job_id": "job-1",
        "at": "2026-10-01T09:00:00+00:00",
        "datasets": ["coverage"],
        "finished_datasets": [],
        "scopes": {},
        "whole_family": [{"dataset": "snv", "scope": {"source": "clair3", "samples": None}}],
    }
    earlier = {
        "at": "2026-10-01T09:00:00+00:00",
        "failed_datasets": ["coverage"],
        "imported_datasets": [],
        "job_id": "job-1",
        "failed_jobs": {"coverage": "job-1"},
        "scopes": {"coverage": {"source": None, "samples": ["S1"]}},
    }
    database = _Postgres(flag=copy.deepcopy(earlier), unfinished={"job-1": copy.deepcopy(entry)})
    stores.failing_insert = 1

    with pytest.raises(RuntimeError):
        await _upload(database, _HEADER + _calls(3))

    assert database.committed["unfinished"] == {"job-1": entry}
    flag = database.committed["flag"]
    assert flag["failed_datasets"] == ["coverage", "snv"]
    assert flag["failed_jobs"] == {"coverage": "job-1", "snv": None}


@pytest.mark.asyncio
async def test_a_flag_that_cannot_be_written_leaves_the_uploads_own_error(stores) -> None:
    class _Unavailable(_Postgres):
        async def execute(self, statement, params=None):
            if "FOR UPDATE" in str(statement):
                raise ConnectionError("database unavailable")
            return await super().execute(statement, params)

    stores.failing_insert = 1
    database = _Unavailable()

    with pytest.raises(RuntimeError, match="ClickHouse insert failed"):
        await _upload(database, _HEADER + _calls(3))

    assert database.committed["flag"] is None and database.commits == 0
    # The mark the upload wrote before its delete stays, and still marks the family for the
    # callset (SAFE-28).
    [(key, mark)] = database.committed["unfinished"].items()
    assert key.startswith("upload-")
    assert mark["datasets"] == ["snv"]
    assert mark["whole_family"] == [{"dataset": "snv", "scope": {"source": "clair3", "samples": None}}]
