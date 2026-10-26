"""A deletion of a family's data marks the family before its first ClickHouse write (SAFE-31).

ClickHouse has no transactions. The admin's deletion of a sample with its data deleted the
sample's tracks, then rewrote the family's small variants (with no restore) and structural
variants without the sample's calls, before the sample's Postgres rows went; the admin's
family delete deleted the family's variants and tracks before its Postgres rows; a PED upload
or a manual family that replaces a family, and a structure change that clears the family's
data, delete its ClickHouse data last, just before their commit. A failure after a
ClickHouse write -- a rewrite, a Postgres write, the commit -- or a process that stopped there
rolled the family back without what was deleted, and nothing marked it: the report could be
signed out as complete, with a member that lost its calls looking homozygous reference.

Now each such deletion writes a mark into ``import_unfinished`` before its first ClickHouse
write (``deletion-…``, committed on a connection of its own, ``FamilyDeletion``), listing
what it may leave partly written or missing, one sample at a time: a process that stops
leaves it. Its commit removes it -- with the family, for a deletion of the family -- and the
deletion of a sample strikes that sample's items from every mark: nothing of a sample that is
gone can be missing. A deletion that fails leaves the mark listing exactly what its writes may
have left so, given how far it got (the rewrite of the family's small variants now puts the
rows back as the SV rewrite does), or removes it when that is nothing. It gates sign-out as an
import's entry does, and goes item by item as each dataset is brought again with overwrite.

Against fakes at the stores' edge: ClickHouse (written at once, kept whatever becomes of the
request), and the family row in Postgres (a write is the transaction's until it commits; the
mark's commits at once on a connection of its own).
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from io import BytesIO
import json
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException, UploadFile
import pytest

from backend.app.schemas import FamilyOut, FamilyStructureUpdate, ManualPedFamilyCreate, ManualPedMemberCreate
from backend.app.services import (
    admin_service,
    clickhouse_variant_storage,
    family_package_registration as registration,
    family_structure_service,
    ped_service,
)
from backend.app.services.access_control import CurrentUser
from backend.app.services.clickhouse_variant_records import (
    StoredStructuralVariantRow,
    StructuralVariantCall,
    StructuralVariantRecord,
)
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.family_package_common import SUPPORTED_DATASETS

_SAMPLES = ("S1", "S2", "S3")
_FAMILY = FamilyMetadataContext(
    family_uuid="family-uuid",
    family_id="F1",
    project_ids=["project-1"],
    sample_rows=[
        {"sample_uuid": f"uuid-{sample}", "sample_id": sample, "sex": "female", "role": role}
        for sample, role in zip(_SAMPLES, ("proband", "sibling", "father"))
    ],
    sample_uuid_to_name={f"uuid-{sample}": sample for sample in _SAMPLES},
    sample_name_to_uuid={sample: f"uuid-{sample}" for sample in _SAMPLES},
    affected_sample_names=["S1"],
    assembly_id="assembly-1",
    assembly_name="GRCh38",
)

# What the family's imports and uploads brought (``imported_scopes``): the direct calls in one
# family VCF, each sample's chrM calls (small variants and SVs), NeedlR's SVs in one family
# VCF, two samples' Sniffles calls and coverage BEDs, and a TRGT upload (Postgres alone).
_IMPORTED = {
    "snv": [{"source": "clair3", "samples": None}],
    "mito": [{"source": None, "samples": ["S1"]}, {"source": None, "samples": ["S2"]}],
    "sv_needlr": [{"source": None, "samples": None}],
    "sv_sniffles": [{"source": None, "samples": ["S1"]}, {"source": None, "samples": ["S2"]}],
    "bed_coverage": [{"source": None, "samples": ["S1"]}, {"source": None, "samples": ["S2"]}],
    "repeats_trgt": [{"source": None, "samples": ["S2"]}],
}


def _item(dataset: str, *samples: str, part: str | None = None) -> tuple[Any, ...]:
    """An item of a mark, as ``_items`` reads it: the dataset, the samples its scope names
    (none for a family-level file), and the one sample whose part of it the item stands for."""
    return (dataset, samples or None, part)


_SMALL = {_item("snv"), _item("mito", "S1"), _item("mito", "S2")}
_SV = {_item("mito", "S1"), _item("mito", "S2"), _item("sv_needlr"), _item("sv_sniffles", "S1"), _item("sv_sniffles", "S2")}
_TRACKS = {_item("bed_coverage", "S1"), _item("bed_coverage", "S2")}
# S2's data: its own items, and its part of each family-level file.
_S2_SMALL = {_item("snv", part="S2"), _item("mito", "S2")}
_S2_SV = {_item("mito", "S2"), _item("sv_needlr", part="S2"), _item("sv_sniffles", "S2")}
_S2_TRACKS = {_item("bed_coverage", "S2")}


class _ProcessStopped(BaseException):
    """The process running the deletion stops there (a restart, a crash, running out of
    memory): nothing after it runs, neither the rest of the request nor its clean-up."""


def _entry(position: int, calls: dict[str, str]) -> dict[str, Any]:
    """A stored small-variant row of the direct calls, its calls by sample id or uuid."""
    return {
        "key": position,
        "source": "clair3",
        "project_guid": "project-1",
        "calls.sampleId": list(calls),
        "calls.gt": list(calls.values()),
    }


def _sv_row(variant_id: str, calls: dict[str, str], *, source: str) -> StoredStructuralVariantRow:
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
            annotations=[],
            calls=[
                StructuralVariantCall(sample=sample, gt=gt, qual=40.0, read_support=9, filter="PASS")
                for sample, gt in calls.items()
            ],
        ),
    )


_STORED_SMALL = [
    _entry(100, {"S1": "0/1", "uuid-S2": "0/1", "S3": "0/0"}),
    _entry(200, {"S2": "1/1"}),
    _entry(300, {"S1": "0/1", "S3": "0/1"}),
]
_STORED_SV = [
    _sv_row("1-7000-12000-DEL---", {"S1": "0/1", "S2": "0/1"}, source="needlr"),
    _sv_row("1-30000-31000-DEL---", {"S2": "0/1", "S1": "1/1"}, source="sniffles"),
]


class _ClickHouse:
    """The family's rows in ClickHouse, written at once and kept whatever becomes of the
    request: its small-variant rows, its SV rows and each sample's tracks. A rewrite of the
    small variants deletes them, then writes; ``small_rewrite_failures`` such writes fail, or
    with ``stop_small`` the next stops the process. ``sv_insert_failures`` SV inserts fail, or
    with ``stop_sv`` the next stops the process. ``events`` (shared with Postgres) is what was
    done, in order."""

    def __init__(self, events: list[tuple[Any, ...]]) -> None:
        self.events = events
        self.small = copy.deepcopy(_STORED_SMALL)
        self.sv = copy.deepcopy(_STORED_SV)
        self.tracks = {"uuid-S1": 3, "uuid-S2": 3}
        self.small_rewrite_failures = 0
        self.stop_small = False
        self.sv_insert_failures = 0
        self.stop_sv = False

    async def fetch_small(self, _assembly, _family) -> list[dict[str, Any]]:
        return copy.deepcopy(self.small)

    async def rewrite_small(self, _assembly, _family, entries, *, source=None) -> None:
        self.events.append(("rewrite small variants", len(entries)))
        self.small = []
        if self.stop_small:
            raise _ProcessStopped()
        if self.small_rewrite_failures:
            self.small_rewrite_failures -= 1
            raise RuntimeError("ClickHouse insert failed")
        self.small = copy.deepcopy(list(entries))

    async def delete_small(self, _assembly, _family, *, source=None) -> None:
        self.events.append(("delete small variants",))
        self.small = []

    async def fetch_sv(self, _assembly, _family, *, source=None) -> list[StoredStructuralVariantRow]:
        return copy.deepcopy(self.sv)

    async def delete_sv(self, _assembly, _family, *, source=None) -> None:
        self.events.append(("delete SVs",))
        self.sv = [row for row in self.sv if source is not None and row.record.source != source]

    async def insert_sv(self, _assembly, _family, project_ids, records) -> None:
        if self.stop_sv:
            raise _ProcessStopped()
        if self.sv_insert_failures:
            self.sv_insert_failures -= 1
            raise RuntimeError("ClickHouse insert failed")
        self.events.append(("insert SVs", len(records)))
        self.sv.extend(
            StoredStructuralVariantRow(project_id=project_ids[0], record=copy.deepcopy(record)) for record in records
        )

    async def delete_tracks(self, _assembly, *, sample_uuid=None, family_uuid=None, **_kwargs) -> None:
        self.events.append(("delete tracks", sample_uuid or "family"))
        if sample_uuid is not None:
            self.tracks.pop(sample_uuid, None)
        else:
            self.tracks = {}

    async def count_small(self, *_args, **_kwargs) -> int:
        return len(self.small)

    async def count_sv(self, *_args, **_kwargs) -> int:
        return len(self.sv)

    def small_calls(self) -> list[list[str]]:
        return [list(entry["calls.sampleId"]) for entry in self.small]

    def sv_calls(self) -> dict[str, set[str]]:
        return {row.record.variant_id: {call.sample for call in row.record.calls} for row in self.sv}


class _Postgres:
    """The family row as the deletions read and write it (``unfinished``: import_unfinished,
    ``imported``: imported_scopes, ``exists``: the row is there), and the other statements
    of their transaction, recorded. A write is the transaction's until it commits; a rollback
    discards it. ``failing_commits`` commits fail (the transaction stays open). ``connection()``
    is the connection the deletion writes its mark on: committed at once."""

    def __init__(self, events: list[tuple[Any, ...]], *, unfinished: Any = None, imported: Any = None) -> None:
        self.events = events
        self.committed: dict[str, Any] = {
            "unfinished": copy.deepcopy(unfinished),
            "imported": copy.deepcopy(imported if imported is not None else _IMPORTED),
            "exists": True,
            "samples": list(_SAMPLES),
        }
        self.working = copy.deepcopy(self.committed)
        self.commits = 0
        self.failing_commits = 0
        self.unavailable = False  # the mark's connection cannot be had

    async def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        params = dict(params or {})
        if "-> 'imported_scopes' AS imported_scopes" in sql:
            row = (
                {"imported_scopes": copy.deepcopy(self.working["imported"]), "sample_ids": list(self.working["samples"])}
                if self.working["exists"]
                else None
            )
            return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: row))
        if sql.startswith("SELECT metadata -> 'import_unfinished'"):
            assert sql.endswith("FOR NO KEY UPDATE"), sql
            row = {"unfinished": copy.deepcopy(self.working["unfinished"])} if self.working["exists"] else None
            return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: row))
        if sql.startswith("UPDATE families") and "unfinished" in params:
            self.working["unfinished"] = json.loads(params["unfinished"])
            return SimpleNamespace(rowcount=1)
        if sql.startswith("UPDATE families") and "pedigree" in params:
            self.events.append(("update the pedigree",))
            return SimpleNamespace(rowcount=1)
        if sql.startswith("DELETE FROM families"):
            self.working["exists"] = False
            self.events.append(("delete the family",))
            return SimpleNamespace(fetchall=lambda: [("family-uuid",)], rowcount=1)
        if sql.startswith("DELETE FROM"):
            self.events.append((sql.split(" WHERE ")[0].lower(),))
            return SimpleNamespace(fetchall=lambda: [], rowcount=0)
        raise AssertionError(f"unexpected statement: {sql}")

    async def commit(self) -> None:
        if self.failing_commits:
            self.failing_commits -= 1
            raise ConnectionError("the commit failed")
        self.committed = copy.deepcopy(self.working)
        self.commits += 1
        self.events.append(("commit",))

    async def rollback(self) -> None:
        self.working = copy.deepcopy(self.committed)

    def connection(self):
        database = self
        if database.unavailable:
            raise ConnectionError("database unavailable")

        class _Connection:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc) -> bool:
                return False

            async def execute(self, statement, params=None):
                sql = " ".join(str(statement).split())
                assert sql.startswith("UPDATE families"), sql
                params = dict(params or {})
                for state in (database.committed, database.working):
                    state["unfinished"] = {
                        **(state["unfinished"] or {}),
                        params["import_key"]: json.loads(params["entry"]),
                    }
                database.events.append(("mark",))
                return SimpleNamespace(rowcount=1)

            async def commit(self) -> None:
                return None

        return _Connection()

    def marks(self) -> dict[str, dict[str, Any]]:
        """The family's committed import_unfinished entries (none once it is gone)."""
        if not self.committed["exists"]:
            return {}
        return dict(self.committed["unfinished"] or {})


def _items(entry: dict[str, Any]) -> set[tuple[Any, ...]]:
    """A mark's items, as ``_item`` writes them."""
    return {
        (
            item["dataset"],
            tuple(item["scope"]["samples"]) if item["scope"]["samples"] else None,
            item.get(registration.ITEM_SAMPLE_KEY),
        )
        for item in entry[registration.WHOLE_FAMILY_KEY]
    }


def _deletion_mark(database: _Postgres) -> tuple[str, dict[str, Any]]:
    [(key, entry)] = [(key, entry) for key, entry in database.marks().items() if key.startswith("deletion-")]
    return key, entry


@pytest.fixture
def stores(monkeypatch):
    """``stores(database)``: ClickHouse faked at its edge for the admin, PED and structure
    services, and the mark's connection; every Postgres write but the family row's patched
    out (each test patches what it fails)."""

    def install(database: _Postgres) -> _ClickHouse:
        clickhouse = _ClickHouse(database.events)

        async def lock(*_args, **_kwargs) -> None:
            database.events.append(("lock",))

        async def nothing(*_args, **_kwargs):
            return None

        async def none_counted(*_args, **_kwargs) -> int:
            return 0

        async def no_pedigree(*_args, **_kwargs):
            return {"members": [], "relationships": []}

        async def sample_row(_session, sample_id):
            return {"sample_uuid": f"uuid-{sample_id}", "sample_id": sample_id, "family_uuid": "family-uuid", "family_id": "F1"}

        async def sample_rows(_session, family_uuids):
            return {family_uuid: [] for family_uuid in family_uuids}

        async def contexts(_session, **_kwargs):
            return [_FAMILY]

        async def family_uuid(_session, _family_id):
            return "family-uuid"

        for name, value in {
            "_sample_row_or_404": sample_row,
            "_sample_rows_by_family": sample_rows,
            "_family_assembly_contexts": contexts,
            "_resolve_family_uuid": family_uuid,
            "lock_family_variant_writes": lock,
            "lock_family_pedigree": nothing,
            "read_structure_snapshot": no_pedigree,
            "record_structure_change": nothing,
            "record_phenotypes_deleted_with_sample": nothing,
            "record_phenotypes_deleted_with_family": nothing,
            "record_family_deleted": nothing,
            "delete_interval_track_sources": none_counted,
            "purge_sample_managed_files": none_counted,
            "purge_family_managed_files": none_counted,
            "delete_interval_tracks": clickhouse.delete_tracks,
            "fetch_family_small_variant_entries": clickhouse.fetch_small,
            "rewrite_family_small_variant_entries": clickhouse.rewrite_small,
            "fetch_family_structural_variant_rows": clickhouse.fetch_sv,
            "count_family_small_variants": clickhouse.count_small,
            "count_family_structural_variants": clickhouse.count_sv,
            "delete_family_small_variants": clickhouse.delete_small,
            "delete_family_structural_variants": clickhouse.delete_sv,
        }.items():
            monkeypatch.setattr(admin_service, name, value)
        # The SV rewrite and its restore run as written, down to ClickHouse's edge.
        monkeypatch.setattr(clickhouse_variant_storage, "delete_family_structural_variants", clickhouse.delete_sv)
        monkeypatch.setattr(clickhouse_variant_storage, "insert_structural_variant_records", clickhouse.insert_sv)
        for module in (ped_service, family_structure_service):
            monkeypatch.setattr(module, "lock_family_variant_writes", lock)
            monkeypatch.setattr(module, "delete_family_small_variants", clickhouse.delete_small)
            monkeypatch.setattr(module, "delete_family_structural_variants", clickhouse.delete_sv)
        monkeypatch.setattr(family_structure_service, "delete_interval_tracks", clickhouse.delete_tracks)
        monkeypatch.setattr(family_structure_service, "delete_interval_track_sources", none_counted)
        monkeypatch.setattr(registration, "get_postgres_sessionmaker", lambda: database.connection, raising=False)
        return clickhouse

    return install


async def _ends_request(database: _Postgres, write) -> Any:
    """Run ``write``; the request's session ends with it: what it did not commit is rolled
    back."""
    try:
        return await write
    finally:
        await database.rollback()


async def _delete_s2(database: _Postgres) -> Any:
    return await _ends_request(
        database, admin_service.delete_sample_with_data(database, "S2", True)  # type: ignore[arg-type]
    )


# --- the admin's deletion of a sample with its data ------------------------------------------


@pytest.mark.asyncio
async def test_a_sample_deletion_whose_process_stops_leaves_the_family_marked_for_all_it_may_reach(stores) -> None:
    database = _Postgres([])
    clickhouse = stores(database)
    clickhouse.stop_small = True

    with pytest.raises(_ProcessStopped):
        await _delete_s2(database)

    # S2's tracks are gone and every small variant with them; nothing of the request committed.
    # Before: nothing marked the family, which read as complete.
    assert clickhouse.small == [] and "uuid-S2" not in clickhouse.tracks and database.commits == 0
    key, mark = _deletion_mark(database)
    assert key.startswith("deletion-")
    assert (mark["job_id"], mark["finished_datasets"]) == (None, [])
    assert mark["datasets"] == ["bed_coverage", "mito", "snv", "sv_needlr", "sv_sniffles"]
    # Every small variant and SV of the family, which it rewrites, and S2's tracks; not S1's
    # tracks, nor what Postgres alone holds (the TRGT calls, deleted in its transaction).
    assert _items(mark) == _SMALL | _SV | _S2_TRACKS
    # Written under the family's locks, before the first ClickHouse write.
    assert database.events[:3] == [("lock",), ("mark",), ("delete tracks", "uuid-S2")]


@pytest.mark.asyncio
async def test_a_sample_deletion_whose_sv_rewrite_fails_leaves_the_samples_data_marked(stores) -> None:
    database = _Postgres([])
    clickhouse = stores(database)
    clickhouse.sv_insert_failures = 1

    with pytest.raises(RuntimeError, match="ClickHouse insert failed"):
        await _delete_s2(database)

    # The SV rewrite put every SV back; the small variants had been rewritten without S2's
    # calls, and its tracks deleted, while the rollback brings S2 back. Before: S2 came back
    # without its data, and nothing marked the family.
    assert clickhouse.sv_calls() == {"1-7000-12000-DEL---": {"S1", "S2"}, "1-30000-31000-DEL---": {"S1", "S2"}}
    assert clickhouse.small_calls() == [["S1", "S3"], ["S1", "S3"]]
    _key, mark = _deletion_mark(database)
    # Exactly S2's data: its tracks, and its small-variant calls, its part of the direct calls
    # among them. Not the SVs, which were put back, nor the other samples' calls.
    assert _items(mark) == _S2_TRACKS | _S2_SMALL
    assert mark["datasets"] == ["bed_coverage", "mito", "snv"]


@pytest.mark.asyncio
async def test_a_sample_deletion_whose_small_rewrite_fails_puts_the_rows_back(stores) -> None:
    database = _Postgres([])
    clickhouse = stores(database)
    clickhouse.small_rewrite_failures = 1

    with pytest.raises(RuntimeError, match="ClickHouse insert failed"):
        await _delete_s2(database)

    # The rows as they were, S2's calls included (before: every small variant of the family
    # was gone), and no SV touched.
    assert clickhouse.small == _STORED_SMALL
    assert ("delete SVs",) not in database.events
    # Only S2's tracks, which went first, may be missing.
    assert _items(_deletion_mark(database)[1]) == _S2_TRACKS


@pytest.mark.asyncio
async def test_a_sample_deletion_whose_small_variants_cannot_be_put_back_marks_each_small_variant_dataset(
    stores,
) -> None:
    database = _Postgres([])
    clickhouse = stores(database)
    clickhouse.small_rewrite_failures = 2

    with pytest.raises(clickhouse_variant_storage.SmallVariantsNotRestored) as raised:
        await _delete_s2(database)

    assert str(raised.value.__cause__) == "ClickHouse insert failed"
    assert clickhouse.small == []
    # Every sample's small variants may be missing, and S2's tracks.
    assert _items(_deletion_mark(database)[1]) == _SMALL | _S2_TRACKS


@pytest.mark.asyncio
async def test_a_sample_deletion_whose_commit_fails_leaves_the_samples_data_marked(stores) -> None:
    database = _Postgres([])
    clickhouse = stores(database)
    database.failing_commits = 1

    with pytest.raises(ConnectionError, match="the commit failed"):
        await _delete_s2(database)

    # Everything of S2 in ClickHouse went, and the rollback brings S2 back.
    assert all("S2" not in calls and "uuid-S2" not in calls for calls in clickhouse.small_calls())
    assert _items(_deletion_mark(database)[1]) == _S2_TRACKS | _S2_SMALL | _S2_SV


@pytest.mark.asyncio
async def test_a_sample_deletion_that_completes_strikes_the_sample_off_every_mark(stores) -> None:
    upload_key = "upload-" + "0a" * 16
    earlier_key = "deletion-" + "1b" * 16
    database = _Postgres(
        [],
        unfinished={
            # An upload of S1's and S2's Sniffles calls that did not finish.
            upload_key: registration.FamilyWriteMark.begin(
                registration.UPLOAD_MARK_PREFIX, [("sv_sniffles", {"source": None, "samples": ["S1", "S2"]})]
            ).entry(),
            # An earlier deletion of S2 that failed: S2's track and calls, and NeedlR's SVs
            # of every sample, which its rewrite could not put back.
            earlier_key: registration.FamilyWriteMark.listing(
                registration.DELETION_MARK_PREFIX,
                [
                    {"dataset": "bed_coverage", "scope": {"source": None, "samples": ["S2"]}},
                    {"dataset": "snv", "scope": {"source": "clair3", "samples": None}, "sample": "S2"},
                    {"dataset": "sv_needlr", "scope": {"source": None, "samples": None}},
                ],
            ).entry(),
        },
    )
    clickhouse = stores(database)

    result = await _delete_s2(database)

    assert result["deleted"]["samples"] == 1
    assert ("delete from samples",) in database.events and database.commits == 1
    marks = database.marks()
    # Its own mark went with its commit, and S2's items went from each mark: nothing of a
    # sample that is gone can be missing. What may still be missing of the others stays.
    assert set(marks) == {upload_key, earlier_key}
    assert _items(marks[upload_key]) == {_item("sv_sniffles", "S1")}
    assert _items(marks[earlier_key]) == {_item("sv_needlr")}
    assert marks[earlier_key]["datasets"] == ["sv_needlr"]
    assert clickhouse.sv_calls() == {"1-7000-12000-DEL---": {"S1"}, "1-30000-31000-DEL---": {"S1"}}


@pytest.mark.asyncio
async def test_a_deletion_leaves_out_what_a_sample_no_longer_in_the_family_held(stores) -> None:
    # S4's Sniffles calls, recorded before S4 was deleted: no upload or import can bring them
    # again, so a mark listing them would never go.
    database = _Postgres(
        [], imported={**_IMPORTED, "sv_sniffles": [{"source": None, "samples": ["S2"]}, {"source": None, "samples": ["S4"]}]}
    )
    clickhouse = stores(database)
    clickhouse.stop_small = True

    with pytest.raises(_ProcessStopped):
        await _delete_s2(database)

    items = _items(_deletion_mark(database)[1])
    assert _item("sv_sniffles", "S2") in items and _item("sv_sniffles", "S4") not in items


@pytest.mark.asyncio
async def test_a_sample_deletion_that_cannot_mark_the_family_deletes_nothing(stores) -> None:
    database = _Postgres([])
    clickhouse = stores(database)
    database.unavailable = True

    with pytest.raises(ConnectionError, match="database unavailable"):
        await _delete_s2(database)

    assert database.events == [("lock",)]
    assert clickhouse.small == _STORED_SMALL and clickhouse.sv == _STORED_SV and "uuid-S2" in clickhouse.tracks


# --- the admin's deletion of a sample's structural variants ----------------------------------


@pytest.mark.asyncio
async def test_an_sv_deletion_whose_process_stops_in_its_rewrite_leaves_the_family_marked(stores) -> None:
    database = _Postgres([])
    clickhouse = stores(database)
    clickhouse.stop_sv = True

    with pytest.raises(_ProcessStopped):
        await _ends_request(
            database,
            admin_service.delete_sample_data_by_type(database, "S2", "structural_variants", True),  # type: ignore[arg-type]
        )

    # Every source's SVs are gone. The family is marked for every SV dataset but S2's own,
    # whose calls were to go (before: nothing marked it).
    assert clickhouse.sv == []
    assert _items(_deletion_mark(database)[1]) == {
        _item("mito", "S1"),
        _item("sv_needlr"),
        _item("sv_sniffles", "S1"),
    }


@pytest.mark.asyncio
async def test_an_sv_deletion_that_completes_leaves_no_mark(stores) -> None:
    database = _Postgres([])
    clickhouse = stores(database)

    result = await _ends_request(
        database,
        admin_service.delete_sample_data_by_type(database, "S2", "structural_variants", True),  # type: ignore[arg-type]
    )

    assert result == {"deleted": 0}
    assert database.marks() == {} and database.commits == 1
    assert clickhouse.sv_calls() == {"1-7000-12000-DEL---": {"S1"}, "1-30000-31000-DEL---": {"S1"}}


# --- the admin's deletion of a family --------------------------------------------------------


@pytest.mark.asyncio
async def test_a_family_deletion_that_fails_after_its_first_delete_leaves_the_family_marked(
    stores, monkeypatch
) -> None:
    database = _Postgres([])
    clickhouse = stores(database)

    async def fails(*_args, **_kwargs) -> None:
        database.events.append(("delete SVs",))
        raise RuntimeError("ClickHouse failed the delete")

    monkeypatch.setattr(admin_service, "delete_family_structural_variants", fails)

    with pytest.raises(RuntimeError, match="ClickHouse failed the delete"):
        await _ends_request(database, admin_service.delete_family_with_data(database, "F1", True))  # type: ignore[arg-type]

    # The rollback brings the family back, without its small variants, and marked for what
    # the deletes that began may have left missing: its small variants and its SVs, not its
    # tracks (before: nothing marked it).
    assert database.committed["exists"] is True and clickhouse.small == []
    assert _items(_deletion_mark(database)[1]) == _SMALL | _SV
    assert database.events[:3] == [("lock",), ("mark",), ("delete small variants",)]


@pytest.mark.asyncio
async def test_a_family_deletion_whose_commit_fails_leaves_the_family_marked_for_everything(stores) -> None:
    database = _Postgres([])
    clickhouse = stores(database)
    database.failing_commits = 1

    with pytest.raises(ConnectionError, match="the commit failed"):
        await _ends_request(database, admin_service.delete_family_with_data(database, "F1", True))  # type: ignore[arg-type]

    assert database.committed["exists"] is True and clickhouse.tracks == {}
    assert _items(_deletion_mark(database)[1]) == _SMALL | _SV | _TRACKS


@pytest.mark.asyncio
async def test_a_family_deletion_that_completes_goes_with_its_mark(stores) -> None:
    database = _Postgres([])
    stores(database)

    await _ends_request(database, admin_service.delete_family_with_data(database, "F1", True))  # type: ignore[arg-type]

    assert database.committed["exists"] is False and database.commits == 1


# --- a PED upload or a manual family that replaces a family ---------------------------------


_ADMIN = SimpleNamespace(id="admin", username="admin", role="admin", project_ids=[])
_PED = "F1 C1 F1A M1A 1 2\nF1 F1A 0 0 1 1\nF1 M1A 0 0 2 1\n"


@pytest.fixture
def replacement(monkeypatch, stores):
    """A PED upload or manual family that replaces family F1: its stored row, and what the
    replacement writes besides, faked; ``refusing`` makes the sample-ID check refuse."""

    def install(database: _Postgres, *, refusing: bool = False) -> _ClickHouse:
        clickhouse = stores(database)

        async def project(_session, _user, project_id):
            return project_id

        async def stored(_session, family_ids):
            return [{"family_uuid": "family-uuid", "family_id": "F1", "project_id": "project-1", "assembly_name": "GRCh38"}]

        async def nothing(*_args, **_kwargs):
            return 0

        async def available(_session, sample_ids):
            if refusing:
                raise HTTPException(status_code=409, detail="Sample id already exists in another family")

        async def create(_session, **kwargs):
            database.events.append(("create the family",))
            return {"family_id": kwargs["family_id"], "samples": []}

        for name, value in {
            "_resolve_accessible_project_id": project,
            "_existing_family_rows": stored,
            "_ensure_user_can_replace_existing_families": lambda _user: None,
            "record_phenotypes_deleted_with_family": nothing,
            "record_family_deleted": nothing,
            "_ensure_sample_ids_are_available": available,
            "_create_family": create,
        }.items():
            monkeypatch.setattr(ped_service, name, value)
        return clickhouse

    return install


async def _upload_ped(database: _Postgres) -> Any:
    return await _ends_request(
        database,
        ped_service.upload_ped_data(
            database,  # type: ignore[arg-type]
            UploadFile(file=BytesIO(_PED.encode()), filename="family.ped"),
            True,
            _ADMIN,  # type: ignore[arg-type]
            "project-1",
        ),
    )


@pytest.mark.asyncio
async def test_a_ped_upload_whose_commit_fails_leaves_the_replaced_family_marked(replacement) -> None:
    database = _Postgres([])
    clickhouse = replacement(database)
    database.failing_commits = 1

    with pytest.raises(ConnectionError, match="the commit failed"):
        await _upload_ped(database)

    # The rollback brings the replaced family back, without its variants (deleted last, just
    # before the commit), and marked for them (before: nothing marked it).
    assert database.committed["exists"] is True
    assert clickhouse.small == [] and clickhouse.sv == []
    assert _items(_deletion_mark(database)[1]) == _SMALL | _SV
    # Marked under the replacement's locks, before its row was touched.
    assert database.events[:3] == [("lock",), ("mark",), ("delete the family",)]


@pytest.mark.asyncio
async def test_a_ped_upload_refused_after_the_replacement_began_leaves_no_mark(replacement) -> None:
    database = _Postgres([])
    clickhouse = replacement(database, refusing=True)

    with pytest.raises(HTTPException) as refused:
        await _upload_ped(database)

    assert refused.value.status_code == 409
    # Refused before any ClickHouse write: the family is as it was, its mark gone again.
    assert database.committed["exists"] is True and database.marks() == {}
    assert clickhouse.small == _STORED_SMALL and clickhouse.sv == _STORED_SV
    assert ("mark",) in database.events


@pytest.mark.asyncio
async def test_a_ped_upload_that_replaces_a_family_goes_with_its_mark(replacement) -> None:
    database = _Postgres([])
    replacement(database)

    result = await _upload_ped(database)

    assert [family.family_id for family in result.families] == ["F1"]
    assert database.committed["exists"] is False and database.commits == 1


@pytest.mark.asyncio
async def test_a_manual_family_whose_commit_fails_leaves_the_replaced_family_marked(replacement) -> None:
    database = _Postgres([])
    replacement(database)
    database.failing_commits = 1
    family = ManualPedFamilyCreate(
        family_id="F1",
        project_id="project-1",
        members=[
            ManualPedMemberCreate(sample_id="F1A", sex="male"),
            ManualPedMemberCreate(sample_id="M1A", sex="female"),
            ManualPedMemberCreate(sample_id="C1", father_id="F1A", mother_id="M1A", is_proband=True),
        ],
    )

    with pytest.raises(ConnectionError, match="the commit failed"):
        await _ends_request(
            database, ped_service.create_manual_family_data(database, family, True, _ADMIN)  # type: ignore[arg-type]
        )

    assert database.committed["exists"] is True
    assert _items(_deletion_mark(database)[1]) == _SMALL | _SV


# --- a structure change that clears the family's data ----------------------------------------


def _admin_user() -> CurrentUser:
    return CurrentUser(
        id="11111111-1111-1111-1111-111111111111",
        username="admin@example.com",
        email="admin@example.com",
        role="admin",
        created_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def structure_change(monkeypatch, stores):
    """``PUT /families/F1/structure`` with ``clear_existing_genomic_data``, removing S3: what
    the change writes besides the family row and ClickHouse, faked; ``failing`` names the
    write that fails."""

    def install(database: _Postgres, *, failing: str | None = None) -> _ClickHouse:
        clickhouse = stores(database)
        members = {
            family_structure_service._sample_key(sample): {
                "family_uuid": "family-uuid",
                "sample_uuid": f"uuid-{sample}",
                "sample_id": sample,
                "sex": "female",
                "role": role,
                "clinical_status": "unaffected",
                "carrier_status": "unknown",
                "carrier_type": None,
                "carrier_evidence": {},
                "active": True,
            }
            for sample, role in zip(_SAMPLES, ("proband", "sibling", "father"))
        }

        async def mapping(*_args, **_kwargs):
            return {"id": "family-uuid", "family_id": "F1"}

        async def version(*_args, **_kwargs):
            return 1

        async def current_members(*_args, **_kwargs):
            return {key: dict(row) for key, row in members.items()}

        async def no_relationships(*_args, **_kwargs):
            return []

        async def counts(*_args, **_kwargs):
            return {"small_variants": 3, "structural_variants": 2, "coverage": 6}

        async def assemblies(*_args, **_kwargs):
            return {"GRCh38": ["project-1"]}

        def write(name: str):
            async def run(*_args, **_kwargs) -> None:
                database.events.append((name,))
                if name == failing:
                    raise RuntimeError(f"{name} failed")

            return run

        async def family_record(*_args, **_kwargs):
            return FamilyOut(
                _id="family-uuid",
                family_id="F1",
                created_at=datetime.now(timezone.utc),
                members=[],
                relationships=[],
                projects=[],
                metadata={},
            )

        async def no_lock(*_args, **_kwargs) -> None:
            return None

        for name, value in {
            "get_accessible_family_mapping": mapping,
            "lock_family_pedigree": no_lock,
            "_fetch_current_structure_version": version,
            "_fetch_family_member_rows": current_members,
            "_fetch_current_relationship_rows": no_relationships,
            "_family_genomic_data_counts": counts,
            "_family_assembly_groups": assemblies,
            "_update_member_row": write("update a member"),
            "_record_family_structure_version": write("record the structure version"),
            "_pedigree_text_from_target": lambda **_kwargs: "",
            "get_family_record": family_record,
        }.items():
            monkeypatch.setattr(family_structure_service, name, value)
        return clickhouse

    return install


async def _clear(database: _Postgres) -> Any:
    return await _ends_request(
        database,
        family_structure_service.update_family_structure_for_admin(
            database,  # type: ignore[arg-type]
            family_id="F1",
            update=FamilyStructureUpdate(remove_members=["S3"], clear_existing_genomic_data=True),
            user=_admin_user(),
        ),
    )


@pytest.mark.asyncio
async def test_a_structure_change_whose_commit_fails_after_clearing_the_data_leaves_the_family_marked(
    structure_change,
) -> None:
    database = _Postgres([])
    clickhouse = structure_change(database)
    database.failing_commits = 1

    with pytest.raises(ConnectionError, match="the commit failed"):
        await _clear(database)

    # The rollback brings the family back with its structure, reviews and repeat calls, but
    # not its variants and tracks (deleted last, just before the commit): marked for each of
    # its datasets held in ClickHouse (before: nothing marked it).
    assert clickhouse.small == [] and clickhouse.sv == [] and clickhouse.tracks == {}
    assert _items(_deletion_mark(database)[1]) == _SMALL | _SV | _TRACKS
    # Marked before the change locked the family row.
    assert database.events.index(("mark",)) < database.events.index(("update the pedigree",))


@pytest.mark.asyncio
async def test_a_structure_change_that_fails_before_its_clickhouse_deletes_leaves_no_mark(structure_change) -> None:
    database = _Postgres([])
    clickhouse = structure_change(database, failing="record the structure version")

    with pytest.raises(RuntimeError, match="record the structure version failed"):
        await _clear(database)

    assert clickhouse.small == _STORED_SMALL and clickhouse.sv == _STORED_SV and clickhouse.tracks
    assert database.marks() == {} and ("mark",) in database.events


@pytest.mark.asyncio
async def test_a_structure_change_that_clears_the_data_removes_its_mark_with_its_commit(structure_change) -> None:
    database = _Postgres([])
    structure_change(database)

    result = await _clear(database)

    assert result.cleared_data_counts == {"small_variants": 3, "structural_variants": 2, "coverage": 6}
    assert database.marks() == {} and database.commits == 1
    assert database.events[-4:] == [("delete small variants",), ("delete SVs",), ("delete tracks", "family"), ("commit",)]


# --- what each dataset keeps in ClickHouse ---------------------------------------------------


def test_each_dataset_says_which_clickhouse_stores_hold_its_data() -> None:
    # Every package dataset (and the phenotypes) is listed, so a new one is never left out of
    # a deletion's mark by a guess.
    listed = set(registration._DATASET_CLICKHOUSE_STORES)
    assert {*SUPPORTED_DATASETS, "phenotypes"} <= listed
    stores = registration.dataset_clickhouse_stores
    assert stores("snv") == {registration.SMALL_VARIANT_DATA}
    assert stores("mito") == {registration.SMALL_VARIANT_DATA, registration.STRUCTURAL_VARIANT_DATA}
    assert stores("sv_sniffles") == {registration.STRUCTURAL_VARIANT_DATA}
    assert stores("bed_target_coverage") == {registration.INTERVAL_TRACK_DATA}
    assert stores("repeats_trgt") == frozenset()
    # One of no known kind counts as held in each.
    assert stores("a_dataset_of_the_future") == {
        registration.SMALL_VARIANT_DATA,
        registration.STRUCTURAL_VARIANT_DATA,
        registration.INTERVAL_TRACK_DATA,
    }
