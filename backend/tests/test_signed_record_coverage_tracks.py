"""The signed record freezes what each coverage track holds (owner decision of 2026-10-20).

Each coverage BED declares whether it holds read depth or a log2 ratio (TRACE-9), and the depth
readers (the mtDNA coverage with its ``mtdna.mean_depth`` verdict, the NIPT coverage check without
a per-target table) read only a track of read depth. The signed record froze none of it, so a
signed analysis could not say which kind of coverage a depth check rested on. Its
``coverage_tracks`` section (``signed_record_sections.coverage_tracks``) now holds, for every
coverage track of the family's members, per sample and caller:

* what it holds, by the depth readers' one rule over the same records
  (``coverage_read_depth.coverage_track_content``): a coverage BED what it declared, another
  caller's what its import's transform made of its file;
* each file it was loaded from: its name, its rows, when, and from where -- a single-file upload or
  a package's dataset, which the BED loaders now record (``metadata.uploaded_from``), since the
  upload and a package's coverage BED share the source ``web``.

The section is bound into the content hash and compared by the sign-out check like every section;
a record signed before it still reads, the check names the section not compared, and nothing it
predates is called not captured. The same runs against Postgres and ClickHouse in
e2e/test_e2e_signed_coverage_tracks.py.
"""

from __future__ import annotations

import asyncio
import copy
import json
import types
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from fastapi import UploadFile

from backend.app.routers import bed as bed_router
from backend.app.schemas import FamilyImportDatasetSummary
from backend.app.services import bed_service, family_package_datasets
from backend.app.services import family_package_registration as registration
from backend.app.services import report_signout_service as rss
from backend.app.services import signed_record_sections as srs
from backend.app.services.coverage_read_depth import coverage_track_content
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.family_package_common import (
    FamilyPackageBundle,
    ManifestDataset,
    PackageManifest,
    ParsedPed,
)

FAMILY = "FAM_COV"
SAMPLES = ("S1", "S2", "S3")
LOADED = datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc)


def _uuid(sample_id: str) -> str:
    return f"uuid-{sample_id}"


def _context() -> FamilyMetadataContext:
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id=FAMILY,
        project_ids=["project-uuid"],
        sample_rows=[
            {"sample_uuid": _uuid(sample), "sample_id": sample, "sex": "female", "role": "proband", "affected": True}
            for sample in SAMPLES
        ],
        sample_uuid_to_name={_uuid(sample): sample for sample in SAMPLES},
        sample_name_to_uuid={sample: _uuid(sample) for sample in SAMPLES},
        affected_sample_names=["S1"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


def _record(
    sample: str,
    source: str,
    filename: str,
    *,
    declared: str | None = None,
    normalization: str | None = None,
    loaded_from: str | None = None,
    rows: int = 2,
) -> dict[str, Any]:
    """A ``sample_interval_track_sources`` row as the section's query returns it."""
    return {
        "sample_uuid": _uuid(sample),
        "source": source,
        "filename": filename,
        "row_count": rows,
        "uploaded_at": LOADED,
        "declared": declared,
        "normalization": normalization,
        "loaded_from": loaded_from,
    }


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> "_Result":
        return self

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class _TrackSession:
    """Answers the section's query with fixed track records; keeps what it was asked."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.executed: list[tuple[str, Any]] = []
        self.savepoints = 0

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        sql = str(statement)
        self.executed.append((sql, params))
        return _Result(self.rows if "FROM sample_interval_track_sources" in sql else [])

    def begin_nested(self):
        session = self

        class _Savepoint:
            async def __aenter__(self) -> None:
                session.savepoints += 1

            async def __aexit__(self, *exc: Any) -> bool:
                return False

        return _Savepoint()


def _tracks(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return asyncio.run(srs.coverage_tracks(_TrackSession(rows), _context()))  # type: ignore[arg-type]


def _file(
    name: str | None,
    *,
    loaded_from: str | None,
    declared: str | None = None,
    normalization: str | None = None,
    rows: int = 2,
) -> dict[str, Any]:
    return {
        "file": name,
        "rows": rows,
        "loaded_at": LOADED.isoformat(),
        "loaded_from": loaded_from,
        "declared": declared,
        "normalization": normalization,
    }


# --- The section ---------------------------------------------------------------------------------


def test_each_coverage_track_is_frozen_with_what_it_holds_and_where_it_came_from() -> None:
    out = _tracks(
        [
            # A package's coverage BED declared as read depth, and WisecondorX's bins.
            _record("S1", "web", "S1.bed", declared="read_depth", loaded_from="family_package"),
            _record("S1", "wisecondorx", "S1_bins.bed", loaded_from="family_package", rows=3000),
            # An uploaded BED declared as a log2 ratio; HiFiCNV's depths stored as read.
            _record("S2", "web", "S2.upload.bed", declared="log2_ratio", loaded_from="web"),
            _record("S2", "hificnv", "S2.depth.bw", normalization="none", loaded_from="family_package"),
            # A manifest entry that declared nothing; HiFiCNV's depth as a log2 ratio; a caller
            # whose import records nothing of what its values are.
            _record("S3", "web", "S3.bed", declared="unknown", loaded_from="family_package"),
            _record(
                "S3",
                "hificnv",
                "S3.depth.bw",
                normalization="log2_ratio_to_autosomal_median",
                loaded_from="family_package",
            ),
            _record("S3", "cnvkit", "S3.cnr", loaded_from="family_package"),
            # A sample that is no active member of the family: no view shows its tracks.
            {**_record("GONE", "web", "GONE.bed", declared="read_depth"), "sample_uuid": "uuid-GONE"},
        ]
    )

    assert out == {
        "tracks": [
            {
                "sample": "S1",
                "source": "web",
                "content": "read_depth",
                "files": [_file("S1.bed", loaded_from="package", declared="read_depth")],
            },
            {
                "sample": "S1",
                "source": "wisecondorx",
                "content": "log2_ratio",
                "files": [_file("S1_bins.bed", loaded_from="package", rows=3000)],
            },
            {
                "sample": "S2",
                "source": "hificnv",
                "content": "read_depth",
                "files": [_file("S2.depth.bw", loaded_from="package", normalization="none")],
            },
            {
                "sample": "S2",
                "source": "web",
                "content": "log2_ratio",
                "files": [_file("S2.upload.bed", loaded_from="upload", declared="log2_ratio")],
            },
            {
                "sample": "S3",
                "source": "cnvkit",
                "content": "unknown",
                "files": [_file("S3.cnr", loaded_from="package")],
            },
            {
                "sample": "S3",
                "source": "hificnv",
                "content": "log2_ratio",
                "files": [
                    _file(
                        "S3.depth.bw",
                        loaded_from="package",
                        normalization="log2_ratio_to_autosomal_median",
                    )
                ],
            },
            {
                "sample": "S3",
                "source": "web",
                "content": "unknown",
                "files": [_file("S3.bed", loaded_from="package", declared="unknown")],
            },
        ]
    }


def test_each_track_holds_what_the_depth_readers_read_it_as() -> None:
    # Per track, the readers' one rule over the values its records name, never another reading.
    cases = [
        ("web", {"declared": "read_depth"}),
        ("web", {"declared": "log2_ratio"}),
        ("web", {"declared": "unknown"}),
        ("web", {}),
        ("wisecondorx", {}),
        ("qdnaseq", {}),
        ("hificnv", {"normalization": "none"}),
        ("hificnv", {"normalization": "log2_ratio_to_autosomal_median"}),
        ("hificnv", {"normalization": "a transform not known here"}),
        ("hificnv", {}),
    ]
    for source, recorded in cases:
        [track] = _tracks([_record("S1", source, f"S1.{source}", **recorded)])["tracks"]
        expected = coverage_track_content(
            source,
            [recorded["normalization"]] if "normalization" in recorded else [],
            [recorded["declared"]] if "declared" in recorded else [],
        )
        assert track["content"] == expected, (source, recorded)
    # Only a coverage BED declared as read depth, or depths stored as read, hold read depth.
    held = {
        (source, json.dumps(recorded, sort_keys=True)): _tracks([_record("S1", source, "f", **recorded)])["tracks"][0]["content"]
        for source, recorded in cases
    }
    assert [key for key, content in held.items() if content == "read_depth"] == [
        ("web", '{"declared": "read_depth"}'),
        ("hificnv", '{"normalization": "none"}'),
    ]


def test_a_coverage_bed_stored_before_beds_declared_what_they_hold_reads_as_unknown() -> None:
    # No `content` and no `uploaded_from` in its record: unknown, and from nowhere recorded.
    [track] = _tracks([_record("S1", "web", "old.bed")])["tracks"]
    assert track["content"] == "unknown"
    assert track["files"] == [_file("old.bed", loaded_from=None)]


def test_two_records_of_one_track_that_disagree_are_both_named_and_the_track_is_unknown() -> None:
    out = _tracks(
        [
            _record("S1", "web", "b.bed", declared="read_depth", loaded_from="web"),
            _record("S1", "web", "a.bed", declared="log2_ratio", loaded_from="family_package"),
        ]
    )
    [track] = out["tracks"]
    # The depth readers read such a track as unknown, never as depth.
    assert track["content"] == "unknown"
    assert [entry["file"] for entry in track["files"]] == ["a.bed", "b.bed"]
    assert [entry["declared"] for entry in track["files"]] == ["log2_ratio", "read_depth"]


def test_another_loader_is_frozen_as_its_record_names_it() -> None:
    [track] = _tracks([_record("S1", "web", "S1.bed", declared="log2_ratio", loaded_from="demo-script")])["tracks"]
    assert track["files"][0]["loaded_from"] == "demo-script"


def test_a_family_without_a_coverage_track_holds_none() -> None:
    # Held all the same: a track loaded after sign-out then reads as a change.
    assert _tracks([]) == {"tracks": []}


def test_the_section_is_read_by_bound_values_and_is_deterministic() -> None:
    rows = [
        _record("S2", "web", "S2.bed", declared="read_depth", loaded_from="web"),
        _record("S1", "wisecondorx", "S1_bins.bed", loaded_from="family_package"),
        _record("S1", "web", "S1.bed", declared="unknown", loaded_from="family_package"),
    ]
    session = _TrackSession(rows)
    out = asyncio.run(srs.coverage_tracks(session, _context()))  # type: ignore[arg-type]
    [(sql, params)] = session.executed
    assert params == {
        "family_uuid": "family-uuid",
        "track_type": "coverage",
        "content_key": "content",
        "normalization_key": "normalization",
        "loaded_from_key": "uploaded_from",
    }
    # Bound, never spliced into the statement.
    assert "CAST(:family_uuid AS uuid)" in sql and "track_type = :track_type" in sql
    assert "family-uuid" not in sql
    # The same records in another order freeze the same section.
    assert _tracks(list(reversed(rows))) == out
    # JSON-safe: it reads back from JSONB as it was hashed.
    assert json.loads(json.dumps(out)) == out


def test_every_family_holds_the_coverage_tracks(monkeypatch) -> None:
    async def _empty(session, *, context):
        return types.SimpleNamespace(loci=[], genes=[])

    monkeypatch.setattr(srs, "get_family_repeat_expansion_table_response", _empty)
    monkeypatch.setattr(srs, "get_family_paraphase_table_response", _empty)
    session = _TrackSession([_record("S1", "web", "S1.bed", declared="read_depth", loaded_from="web")])
    sections = asyncio.run(
        srs.build_record_sections(session, context=_context(), reported=[], reported_structural=[])  # type: ignore[arg-type]
    )
    assert srs.COVERAGE_TRACKS.application is None
    assert sections["coverage_tracks"]["tracks"][0]["content"] == "read_depth"
    assert "coverage_tracks" in rss.REPORT_CONTENT_SECTIONS


def test_coverage_tracks_that_cannot_be_read_are_frozen_as_unavailable_and_named(monkeypatch) -> None:
    async def _fails(session, context):
        raise RuntimeError("SELECT ... WHERE sample = 'S1-secret'")

    async def _empty(session, *, context):
        return types.SimpleNamespace(loci=[], genes=[])

    monkeypatch.setattr(srs, "coverage_tracks", _fails)
    monkeypatch.setattr(srs, "get_family_repeat_expansion_table_response", _empty)
    monkeypatch.setattr(srs, "get_family_paraphase_table_response", _empty)
    sections = asyncio.run(
        srs.build_record_sections(_TrackSession([]), context=_context(), reported=[], reported_structural=[])  # type: ignore[arg-type]
    )
    # A fixed sentence, never the error's text, which may quote what it read.
    assert sections["coverage_tracks"] == {"unavailable": "The coverage tracks could not be read."}
    assert "secret" not in str(sections)
    assert srs.record_section_gaps(sections) == [
        {"section": "coverage_tracks", "item": "Coverage tracks", "reason": "The coverage tracks could not be read."}
    ]


# --- The content hash and the sign-out check -----------------------------------------------------

_SIGNED_TRACKS = {
    "tracks": [
        {
            "sample": "S1",
            "source": "web",
            "content": "read_depth",
            "files": [_file("S1.bed", loaded_from="package", declared="read_depth")],
        },
        {
            "sample": "S1",
            "source": "wisecondorx",
            "content": "log2_ratio",
            "files": [_file("S1_bins.bed", loaded_from="package")],
        },
    ]
}


class _LatestSignout:
    """Answers the check's read of the latest sign-out with one stored row."""

    def __init__(self, snapshot: dict[str, Any]) -> None:
        # Stored the way sign_out_report stores it: JSON-encoded, read back from JSONB.
        self.row = {"version": 3, "content_hash": "h" * 64, "snapshot": json.loads(json.dumps(snapshot))}

    async def execute(self, *args: Any, **kwargs: Any) -> _Result:
        return _Result([self.row])


def _check(monkeypatch, *, signed: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    async def _ctx(session, *, family_identifier, user, project_id=None):
        return types.SimpleNamespace(family_uuid="family-uuid", family_id=FAMILY)

    async def _snapshot(session, *, family_id, user, project_id=None):
        return copy.deepcopy(current)

    monkeypatch.setattr(rss, "build_family_metadata_context", _ctx)
    monkeypatch.setattr(rss, "build_report_snapshot", _snapshot)
    return asyncio.run(
        rss.compare_report_with_latest_signout(
            _LatestSignout(signed),  # type: ignore[arg-type]
            family_id=FAMILY,
            user=types.SimpleNamespace(username="analyst"),  # type: ignore[arg-type]
        )
    )


def _body(tracks: Any) -> dict[str, Any]:
    return {"assembly": "GRCh38", "reported_variants": [], "coverage_tracks": tracks}


def test_the_coverage_tracks_are_bound_into_the_content_hash() -> None:
    signed = _body(_SIGNED_TRACKS)
    redeclared = copy.deepcopy(_SIGNED_TRACKS)
    redeclared["tracks"][0]["content"] = "log2_ratio"
    assert rss._canonical_hash(signed) != rss._canonical_hash(_body(redeclared))
    # Through the JSONB round trip the section hashes as it was frozen.
    assert rss._canonical_hash(json.loads(json.dumps(signed))) == rss._canonical_hash(signed)


def test_the_check_matches_the_coverage_tracks_as_signed(monkeypatch) -> None:
    out = _check(monkeypatch, signed=_body(_SIGNED_TRACKS), current=_body(copy.deepcopy(_SIGNED_TRACKS)))
    assert (out["matches"], out["changed_sections"], out["not_compared"], out["not_captured"]) == (True, [], [], [])


@pytest.mark.parametrize(
    "change",
    [
        # S1's BED uploaded again, declared as a log2 ratio: no depth check reads it now.
        lambda tracks: tracks["tracks"][0].update(
            content="log2_ratio",
            files=[_file("S1.bed", loaded_from="upload", declared="log2_ratio")],
        ),
        # Loaded again from the same file, later.
        lambda tracks: tracks["tracks"][1]["files"][0].update(loaded_at="2026-10-21T09:00:00+00:00"),
        # A track loaded since.
        lambda tracks: tracks["tracks"].append(
            {"sample": "S2", "source": "web", "content": "unknown", "files": [_file("S2.bed", loaded_from="package")]}
        ),
    ],
)
def test_the_check_names_coverage_tracks_that_changed_since_sign_out(monkeypatch, change) -> None:
    current = copy.deepcopy(_SIGNED_TRACKS)
    change(current)
    out = _check(monkeypatch, signed=_body(_SIGNED_TRACKS), current=_body(current))
    assert out["matches"] is False
    assert out["changed_sections"] == ["coverage_tracks"]


def test_a_record_signed_before_the_section_names_it_not_compared_and_nothing_not_captured(monkeypatch) -> None:
    signed = _body(_SIGNED_TRACKS)
    signed.pop("coverage_tracks")
    out = _check(monkeypatch, signed=signed, current=_body(_SIGNED_TRACKS))
    # Not compared, and still matching: the record predates the section, which it does not
    # hold; nothing it predates is called "not captured".
    assert (out["matches"], out["changed_sections"], out["not_compared"]) == (True, [], ["coverage_tracks"])
    assert out["not_captured"] == []
    assert rss.snapshot_gaps(signed) == []


def test_coverage_tracks_not_captured_at_sign_out_are_named_by_the_check(monkeypatch) -> None:
    unavailable = {"unavailable": "The coverage tracks could not be read."}
    out = _check(monkeypatch, signed=_body(unavailable), current=_body(_SIGNED_TRACKS))
    assert out["not_captured"] == [
        {"section": "coverage_tracks", "item": "Coverage tracks", "reason": "The coverage tracks could not be read."}
    ]
    assert out["changed_sections"] == ["coverage_tracks"]


# --- Where a coverage BED came from: the loaders record it ---------------------------------------

_BED = "chrM\t0\t8000\t400\nchrM\t8000\t16569\t380\n"


class _Stores:
    """What each track-source INSERT bound (sample, track type, source -> metadata), and the
    rows bound for ClickHouse."""

    def __init__(self) -> None:
        self.records: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.rows: list[dict[str, Any]] = []


class _StoreSession:
    def __init__(self, stores: _Stores) -> None:
        self.stores = stores

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
        sql = " ".join(str(statement).split())
        params = params or {}
        assert "sample_interval_track_sources" in sql, f"unmodelled statement: {sql}"
        if sql.startswith("SELECT COALESCE(SUM(row_count), 0)"):
            return types.SimpleNamespace(scalar_one=lambda: 0)
        if sql.startswith("DELETE FROM"):
            return types.SimpleNamespace(scalar_one=lambda: None)
        assert sql.startswith("INSERT INTO"), sql
        self.stores.records[(params["sample_id"], params["track_type"], params["source"])] = json.loads(
            params["metadata_json"]
        )
        return types.SimpleNamespace(scalar_one=lambda: None)

    async def commit(self) -> None:
        return None


@pytest.fixture
def stores(monkeypatch: pytest.MonkeyPatch) -> _Stores:
    state = _Stores()

    async def no_rows(*_args: Any, **_kwargs: Any) -> int:
        return 0

    async def insert(_assembly: str, rows: list[dict[str, Any]]) -> None:
        state.rows.extend(rows)

    async def nothing(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def mark(*, family_uuid: str, items: Any) -> Any:
        return types.SimpleNamespace(key="upload-mark")

    monkeypatch.setattr(bed_service, "count_interval_track_rows", no_rows)
    monkeypatch.setattr(bed_service, "insert_interval_track_rows", insert)
    monkeypatch.setattr(bed_service, "delete_interval_tracks", nothing)
    monkeypatch.setattr(bed_service, "record_family_upload", nothing)
    monkeypatch.setattr(bed_service, "mark_family_upload", mark)
    monkeypatch.setattr(bed_service, "end_family_write_mark", nothing)
    return state


@pytest.mark.asyncio
@pytest.mark.parametrize("uploaded_from", ["web", "family_package"])
async def test_a_bed_loader_keeps_where_the_file_came_from_in_the_tracks_record(
    stores: _Stores, uploaded_from: str
) -> None:
    sample = registration._family_sample_contexts(_context())["S1"]
    await bed_service.upload_bed_data(
        _StoreSession(stores),  # type: ignore[arg-type]
        sample_context=sample,
        bed_type="coverage",
        file=UploadFile(file=BytesIO(_BED.encode()), filename="S1.bed"),
        overwrite=True,
        content="read_depth",
        uploaded_from=uploaded_from,
    )
    record = stores.records[(_uuid("S1"), "coverage", "web")]
    assert (record["uploaded_from"], record["content"]) == (uploaded_from, "read_depth")
    # In the record alone, as the other loaders keep it: not on every row.
    assert all("uploaded_from" not in json.loads(row["metadata_json"]) for row in stores.rows)


@pytest.mark.asyncio
async def test_a_bed_loader_that_names_no_origin_records_none(stores: _Stores) -> None:
    sample = registration._family_sample_contexts(_context())["S1"]
    await bed_service.upload_bed_data(
        _StoreSession(stores),  # type: ignore[arg-type]
        sample_context=sample,
        bed_type="coverage",
        file=UploadFile(file=BytesIO(_BED.encode()), filename="S1.bed"),
        overwrite=True,
        content="read_depth",
    )
    assert "uploaded_from" not in stores.records[(_uuid("S1"), "coverage", "web")]


@pytest.mark.asyncio
async def test_the_bed_upload_route_records_a_single_file_upload(monkeypatch) -> None:
    asked: dict[str, Any] = {}
    sample = registration._family_sample_contexts(_context())["S1"]

    async def _sample_context(session, *, sample_identifier, user):
        return sample

    async def _lock(*args: Any, **kwargs: Any) -> None:
        return None

    async def _upload(session, **kwargs: Any) -> dict[str, int]:
        asked.update(kwargs)
        return {"inserted": 2}

    async def _raw_file(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(bed_router, "build_sample_metadata_context", _sample_context)
    monkeypatch.setattr(bed_router, "lock_family_variant_writes", _lock)
    monkeypatch.setattr(bed_router, "upload_bed_data", _upload)
    monkeypatch.setattr(bed_router, "record_upload_file_obj", _raw_file)
    await bed_router.upload_bed(
        sample_id="S1",
        bed_type="coverage",
        file=UploadFile(file=BytesIO(_BED.encode()), filename="S1.bed"),
        overwrite=True,
        content="log2_ratio",
        session=None,  # type: ignore[arg-type]
        user=None,  # type: ignore[arg-type]
    )
    assert (asked["uploaded_from"], asked["content"], asked["record_upload"]) == ("web", "log2_ratio", True)


@pytest.mark.asyncio
async def test_a_package_coverage_bed_records_that_a_package_loaded_it(stores: _Stores, tmp_path: Path) -> None:
    path = tmp_path / "coverage" / "S1.bed"
    path.parent.mkdir(parents=True)
    path.write_text(_BED, encoding="utf-8")
    dataset = ManifestDataset(per_sample={"S1": {"bed": "coverage/S1.bed", "content": "read_depth"}})
    family = _context()
    job = family_package_datasets.DatasetImportJob(
        session=_StoreSession(stores),  # type: ignore[arg-type]
        bundle=FamilyPackageBundle(
            root=tmp_path,
            manifest_path=tmp_path / "manifest.yaml",
            manifest=PackageManifest(ped="family.ped", datasets={"coverage": dataset}),
            ped_path=tmp_path / "family.ped",
            ped=ParsedPed(family_ids=[FAMILY], members=[], sample_ids=list(SAMPLES), text=""),
        ),
        dataset=dataset,
        summary=FamilyImportDatasetSummary(dataset_type="coverage", status="valid"),
        family_context=family,
        sample_contexts=registration._family_sample_contexts(family),
        conflict_mode="overwrite",
    )

    result = await family_package_datasets.DATASET_IMPORTERS["coverage"](job)

    assert result.status == "imported"
    record = stores.records[(_uuid("S1"), "coverage", "web")]
    assert (record["uploaded_from"], record["content"], record["filename"]) == ("family_package", "read_depth", "S1.bed")


def _package_job(tmp_path: Path, stores: _Stores, name: str, dataset: ManifestDataset):
    """A package import's job for one dataset of the family, writing records into ``stores``."""
    family = _context()
    return family_package_datasets.DatasetImportJob(
        session=_StoreSession(stores),  # type: ignore[arg-type]
        bundle=FamilyPackageBundle(
            root=tmp_path,
            manifest_path=tmp_path / "manifest.yaml",
            manifest=PackageManifest(ped="family.ped", datasets={name: dataset}),
            ped_path=tmp_path / "family.ped",
            ped=ParsedPed(family_ids=[FAMILY], members=[], sample_ids=list(SAMPLES), text=""),
        ),
        dataset=dataset,
        summary=FamilyImportDatasetSummary(dataset_type=name, status="valid"),
        family_context=family,
        sample_contexts=registration._family_sample_contexts(family),
        conflict_mode="overwrite",
    )


@pytest.mark.asyncio
async def test_a_packages_apcad_bed_records_that_a_package_loaded_it(
    stores: _Stores, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An APCAD file the VCF reader finds no record in is read as a BED, into the dataset's own
    # track. Its record now says a package loaded it, as every other package loader's does
    # (TRACE-17; it said nothing).
    async def no_vcf_records(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(family_package_datasets, "_import_apcad_track_file", no_vcf_records)
    path = tmp_path / "apcad" / "S1.bed"
    path.parent.mkdir(parents=True)
    path.write_text("chr1\t100\t200\tsnp1\t0.4\tpat\n", encoding="utf-8")
    dataset = ManifestDataset(per_sample={"S1": {"bed": "apcad/S1.bed"}})

    result = await family_package_datasets.DATASET_IMPORTERS["apcad"](_package_job(tmp_path, stores, "apcad", dataset))

    assert result.status == "imported"
    record = stores.records[(_uuid("S1"), "apcad", "apcad")]
    assert (record["uploaded_from"], record["filename"]) == ("family_package", "S1.bed")


@pytest.mark.asyncio
async def test_a_packages_per_target_coverage_table_records_that_a_package_loaded_it(
    stores: _Stores, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A capture panel's per-target coverage table (monogenic NIPT): its record now says a
    # package loaded it, as every other package loader's does (TRACE-17; it said nothing).
    from backend.app.services.nipt_target_coverage import TARGET_COVERAGE_SOURCE, TARGET_COVERAGE_TRACK_TYPE

    async def nothing(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def insert(_assembly: str, rows: list[dict[str, Any]]) -> None:
        stores.rows.extend(rows)

    monkeypatch.setattr(family_package_datasets, "_delete_sample_interval_source", nothing)
    monkeypatch.setattr(family_package_datasets, "insert_interval_track_rows", insert)
    path = tmp_path / "coverage" / "S1.targets.tsv"
    path.parent.mkdir(parents=True)
    path.write_text("chromosome\tstart\tend\tattribute\tmean\nchr1\t100\t200\tGENE1\t80\n", encoding="utf-8")
    dataset = ManifestDataset(per_sample={"S1": {"target_table": "coverage/S1.targets.tsv"}})

    result = await family_package_datasets.DATASET_IMPORTERS["coverage"](
        _package_job(tmp_path, stores, "coverage", dataset)
    )

    assert result.status == "imported"
    record = stores.records[(_uuid("S1"), TARGET_COVERAGE_TRACK_TYPE, TARGET_COVERAGE_SOURCE)]
    assert (record["uploaded_from"], record["format"], record["targets"]) == (
        "family_package",
        "per_target_coverage_table",
        1,
    )
