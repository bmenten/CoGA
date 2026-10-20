"""Each coverage BED declares what it holds: read depth or a log2 ratio (TRACE-9).

A coverage BED (source ``web``: the BED upload's, and a package's ``coverage`` dataset's) recorded
nothing of what its values are. The importer called it a BED of depths and the depth readers read
it as depth (CLIN-46), while the coverage chart drew it on its log2-ratio axis. The owner decided
(2026-10-14) that each coverage BED declares what it holds, in the upload form and in the package
manifest, and that an undeclared file is "unknown" and never read as depth:

* the BED upload takes ``content`` (``read_depth`` or ``log2_ratio``) as a required query
  parameter for a coverage BED, refused (422) when it is missing, and for another track type;
* the track's record keeps it (``sample_interval_track_sources.metadata.content``), as ``unknown``
  for a package entry without the manifest's ``content`` key, and what the family holds (SAFE-19's
  ``imported_scopes``) says it with the sample: a later record replaces it, and no overwrite
  compares it;
* the validation warns of an entry without it and refuses a value that is neither;
* the track availability serves what each coverage track holds (``coverage_contents``), by the rule
  the depth readers read it by (``coverage_read_depth``; the readers themselves are in
  test_coverage_readers_read_depth.py).

The stores are faked at their edge: the session keeps what each track-source INSERT binds, and the
rows bound for ClickHouse are kept as they are inserted. The same runs against Postgres and
ClickHouse in e2e/test_e2e_coverage_bed_declares_content.py.
"""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException, UploadFile

from backend.app.routers import bed as bed_router
from backend.app.schemas import FamilyImportDatasetSummary
from backend.app.services import bed_service, family_package_datasets
from backend.app.services import family_package_registration as registration
from backend.app.services import family_package_validation
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.family_package_common import (
    FamilyPackageBundle,
    ManifestDataset,
    PackageManifest,
    ParsedPed,
)
from backend.app.services.family_service import get_family_track_availability_for_user

FAMILY = "FAM009"
SAMPLES = ("S1", "S2")
_BED = "chrM\t0\t8000\t400\nchrM\t8000\t16569\t380\n"


def _uuid(sample_id: str) -> str:
    return f"uuid-{sample_id}"


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


class _Stores:
    """The track's rows as inserted (``rows``), the records Postgres was given (``records``:
    sample, track type, source -> the metadata bound), what the upload recorded for the
    family (``recorded``: the ``record_family_upload`` calls) and how it marked the family
    before its first ClickHouse write (``marked``: the ``mark_family_upload`` items, SAFE-28)."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.records: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.recorded: list[dict[str, Any]] = []
        self.marked: list[list[tuple[str, dict[str, Any]]]] = []


class _Session:
    def __init__(self, stores: _Stores) -> None:
        self.stores = stores

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
        sql = " ".join(str(statement).split())
        params = params or {}
        assert "sample_interval_track_sources" in sql, f"unmodelled statement: {sql}"
        if sql.startswith("SELECT COALESCE(SUM(row_count), 0)"):
            return SimpleNamespace(scalar_one=lambda: 0)
        if sql.startswith("DELETE FROM"):
            return SimpleNamespace(scalar_one=lambda: None)
        assert sql.startswith("INSERT INTO"), sql
        self.stores.records[(params["sample_id"], params["track_type"], params["source"])] = json.loads(
            params["metadata_json"]
        )
        return SimpleNamespace(scalar_one=lambda: None)

    async def commit(self) -> None:
        return None


@pytest.fixture
def stores(monkeypatch: pytest.MonkeyPatch) -> _Stores:
    state = _Stores()

    async def no_rows(*_args: Any, **_kwargs: Any) -> int:
        return 0

    async def insert(_assembly: str, rows: list[dict[str, Any]]) -> None:
        state.rows.extend(rows)

    async def delete(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def record(
        _session: Any, *, family_uuid: str, dataset: str, scope: dict[str, Any], overwrite: bool, mark: Any = None
    ) -> None:
        state.recorded.append({"dataset": dataset, "scope": scope, "overwrite": overwrite})

    async def mark(*, family_uuid: str, items: Any) -> Any:
        state.marked.append(list(items))
        return SimpleNamespace(key="upload-mark")

    async def end_mark(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(bed_service, "count_interval_track_rows", no_rows)
    monkeypatch.setattr(bed_service, "insert_interval_track_rows", insert)
    monkeypatch.setattr(bed_service, "delete_interval_tracks", delete)
    monkeypatch.setattr(bed_service, "record_family_upload", record)
    monkeypatch.setattr(bed_service, "mark_family_upload", mark)
    monkeypatch.setattr(bed_service, "end_family_write_mark", end_mark)
    return state


def _upload(text: str = _BED, name: str = "S1.coverage.bed") -> UploadFile:
    return UploadFile(file=BytesIO(text.encode()), filename=name)


class _Untouched:
    """A session no statement may reach: the request is refused before anything is read."""

    async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("refused before anything is read")


# --- the BED upload ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_coverage_bed_upload_that_declares_nothing_is_refused_before_anything_is_read() -> None:
    # Before: stored, and read as depth by the mtDNA and NIPT checks while the chart drew it as
    # a log2 ratio.
    with pytest.raises(HTTPException) as refused:
        await bed_router.upload_bed(
            sample_id="S1",
            bed_type="coverage",
            file=_upload(),
            overwrite=True,
            content=None,
            session=_Untouched(),  # type: ignore[arg-type]
            user=None,  # type: ignore[arg-type]
        )

    assert refused.value.status_code == 422
    assert refused.value.detail == (
        "Declare what the coverage BED holds: content=read_depth or content=log2_ratio"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("bed_type", ["segments", "apcad", "apcad_pcf"])
async def test_only_a_coverage_bed_declares_what_it_holds(bed_type: str) -> None:
    with pytest.raises(HTTPException) as refused:
        await bed_router.upload_bed(
            sample_id="S1",
            bed_type=bed_type,
            file=_upload(),
            overwrite=True,
            content="read_depth",
            session=_Untouched(),  # type: ignore[arg-type]
            user=None,  # type: ignore[arg-type]
        )

    assert refused.value.status_code == 422
    assert refused.value.detail == (
        f"content declares what a coverage BED holds; it does not apply to a {bed_type} BED"
    )


def test_the_upload_takes_the_declaration_as_one_of_two_values_in_its_query() -> None:
    from fastapi.testclient import TestClient

    from backend.app.main import app

    # A query parameter, so the request's audit row (its query string) records it.
    schema = TestClient(app).get("/openapi.json").json()
    [parameter] = [
        parameter
        for parameter in schema["paths"]["/api/bed/upload/{sample_id}/{bed_type}"]["post"]["parameters"]
        if parameter["name"] == "content"
    ]
    assert parameter["in"] == "query"
    assert parameter["required"] is False  # required for coverage only: the route refuses it (422)
    [declared, _null] = parameter["schema"]["anyOf"]
    assert declared["enum"] == ["read_depth", "log2_ratio"]


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["read_depth", "log2_ratio"])
async def test_what_a_coverage_bed_declares_is_stored_with_its_track_and_recorded_for_the_family(
    stores: _Stores, content: str
) -> None:
    sample = registration._family_sample_contexts(_family_context())["S1"]

    await bed_service.upload_bed_data(
        _Session(stores),  # type: ignore[arg-type]
        sample_context=sample,
        bed_type="coverage",
        file=_upload(),
        overwrite=True,
        record_upload=True,
        content=content,
    )

    # The track's record keeps it, and each row's metadata too.
    record = stores.records[(_uuid("S1"), "coverage", "web")]
    assert record["content"] == content
    assert {json.loads(row["metadata_json"])["content"] for row in stores.rows} == {content}
    # What the family holds says what the sample's coverage BED declared (SAFE-19's record),
    # and so does the mark the upload leaves until it commits (SAFE-28).
    scope = {"source": None, "samples": ["S1"], "content": content}
    assert stores.recorded == [{"dataset": "bed_coverage", "scope": scope, "overwrite": True}]
    assert stores.marked == [[("bed_coverage", scope)]]


@pytest.mark.asyncio
@pytest.mark.parametrize("bed_type", ["segments", "apcad"])
async def test_another_track_types_record_declares_nothing(stores: _Stores, bed_type: str) -> None:
    sample = registration._family_sample_contexts(_family_context())["S1"]
    text = "1\t99\t100\trs1\t0.48\tpaternal\n" if bed_type == "apcad" else "1\t0\t1000\t-0.2\n"

    await bed_service.upload_bed_data(
        _Session(stores),  # type: ignore[arg-type]
        sample_context=sample,
        bed_type=bed_type,
        file=_upload(text),
        overwrite=True,
        record_upload=True,
    )

    assert "content" not in stores.records[(_uuid("S1"), bed_type, "web")]
    assert stores.recorded[0]["scope"] == {"source": None, "samples": ["S1"]}


# --- the package's coverage dataset ---------------------------------------------------------------


def _job(stores: _Stores, root: Path, dataset: ManifestDataset) -> family_package_datasets.DatasetImportJob:
    family = _family_context()
    return family_package_datasets.DatasetImportJob(
        session=_Session(stores),  # type: ignore[arg-type]
        bundle=FamilyPackageBundle(
            root=root,
            manifest_path=root / "manifest.yaml",
            manifest=PackageManifest(ped="family.ped", datasets={"coverage": dataset}),
            ped_path=root / "family.ped",
            ped=ParsedPed(family_ids=[FAMILY], members=[], sample_ids=list(SAMPLES), text=""),
        ),
        dataset=dataset,
        summary=FamilyImportDatasetSummary(dataset_type="coverage", status="valid"),
        family_context=family,
        sample_contexts=registration._family_sample_contexts(family),
        conflict_mode="overwrite",
    )


def _bed_file(root: Path, sample: str) -> str:
    path = root / "coverage" / f"{sample}.bed"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_BED, encoding="utf-8")
    return f"coverage/{sample}.bed"


@pytest.mark.asyncio
async def test_a_package_coverage_bed_stores_what_its_manifest_entry_declares(
    stores: _Stores, tmp_path: Path
) -> None:
    dataset = ManifestDataset(
        per_sample={
            "S1": {"bed": _bed_file(tmp_path, "S1"), "content": "log2_ratio"},
            # No `content`: stored as unknown, never as depth.
            "S2": {"bed": _bed_file(tmp_path, "S2")},
        }
    )

    result = await family_package_datasets.DATASET_IMPORTERS["coverage"](_job(stores, tmp_path, dataset))

    assert result.status == "imported"
    # Each sample's result names what its BED declared, so the import's record shows it.
    assert result.summary == {
        "S1": {"inserted": 2, "content": "log2_ratio"},
        "S2": {"inserted": 2, "content": "unknown"},
    }
    # Before: neither record said what its BED held, and both were read as depth.
    assert stores.records[(_uuid("S1"), "coverage", "web")]["content"] == "log2_ratio"
    assert stores.records[(_uuid("S2"), "coverage", "web")]["content"] == "unknown"
    # A package import records what it imported itself, when it ends: not per file.
    assert stores.recorded == []


def test_what_a_package_imported_says_what_each_coverage_bed_declared(tmp_path: Path) -> None:
    dataset = ManifestDataset(
        per_sample={
            "S1": {"bed": "coverage/S1.bed", "content": "read_depth"},
            "S2": {"bed": "coverage/S2.bed"},
        }
    )
    bundle = _job(_Stores(), tmp_path, dataset).bundle

    scopes = registration.dataset_scopes(bundle, ["coverage"])

    assert scopes == {
        "coverage": {
            "source": None,
            "samples": ["S1", "S2"],
            "content": {"S1": "read_depth", "S2": "unknown"},
        }
    }
    # Recorded one sample at a time in what the family holds, each with its own declaration.
    assert registration.merged_imported_scopes(None, scopes.items()) == {
        "coverage": [
            {"source": None, "samples": ["S1"], "content": "read_depth"},
            {"source": None, "samples": ["S2"], "content": "unknown"},
        ]
    }


def test_a_later_record_of_a_samples_bed_replaces_what_the_family_holds_of_it() -> None:
    held = registration.merged_imported_scopes(
        None, [("bed_coverage", {"source": None, "samples": ["S1"], "content": "read_depth"})]
    )

    # The sample's BED uploaded again, declared as a log2 ratio: what the family holds is the
    # new declaration, not both.
    again = registration.merged_imported_scopes(
        held, [("bed_coverage", {"source": None, "samples": ["S1"], "content": "log2_ratio"})]
    )

    assert again == {"bed_coverage": [{"source": None, "samples": ["S1"], "content": "log2_ratio"}]}
    # An upload record written before BEDs declared what they hold reads as it was.
    assert registration.merged_imported_scopes(
        {"bed_coverage": [{"source": None, "samples": ["S2"]}]}, ()
    ) == {"bed_coverage": [{"source": None, "samples": ["S2"]}]}


def test_no_overwrite_compares_what_a_bed_declared() -> None:
    # A restore that failed part-way names the sample's BED, declared as read depth; uploading it
    # again declared as a log2 ratio brings that BED again, and strikes it off.
    mark = registration.ImportMark.begin(
        job_id="job-1", datasets=["snv"], scopes={"snv": {"source": "clair3", "samples": None}}
    )
    entry = registration.whole_family_entry(
        mark,
        imported={"bed_coverage": [{"source": None, "samples": ["S1"], "content": "read_depth"}]},
        unfinished={},
        family_samples=["S1"],
    )
    assert {"dataset": "bed_coverage", "scope": {"source": None, "samples": ["S1"], "content": "read_depth"}} in entry[
        "whole_family"
    ]

    left = registration.whole_family_left(
        entry, {"bed_coverage": {"source": None, "samples": ["S1"], "content": "log2_ratio"}}
    )

    assert left is not None
    assert [item["dataset"] for item in left["whole_family"]] == ["snv"]


# --- the package's validation -----------------------------------------------------------------------


def _validate(root: Path, entries: dict[str, dict[str, Any]]) -> tuple[list[Any], list[Any]]:
    errors: list[Any] = []
    warnings: list[Any] = []
    family_package_validation._validate_dataset(
        root=root,
        dataset_type="coverage",
        dataset=ManifestDataset(per_sample=entries),
        ped_sample_ids=set(SAMPLES),
        errors=errors,
        warnings=warnings,
    )
    return errors, warnings


def test_the_validation_warns_of_a_coverage_bed_that_declares_nothing(tmp_path: Path) -> None:
    errors, warnings = _validate(
        tmp_path,
        {
            "S1": {"bed": _bed_file(tmp_path, "S1"), "content": "read_depth"},
            "S2": {"bed": _bed_file(tmp_path, "S2")},
        },
    )

    assert errors == []
    assert [(issue.code, issue.sample_id, issue.message) for issue in warnings] == [
        (
            "coverage_content_undeclared",
            "S2",
            "The coverage BED of S2 does not declare what it holds (content: read_depth or "
            "log2_ratio): it is imported as unknown, drawn without a depth or log2-ratio axis and "
            "never read as depth",
        )
    ]


def test_the_validation_refuses_a_declaration_that_is_neither(tmp_path: Path) -> None:
    errors, warnings = _validate(tmp_path, {"S1": {"bed": _bed_file(tmp_path, "S1"), "content": "depth"}})

    # A misspelt declaration must not import as unknown without a word.
    assert [(issue.code, issue.sample_id, issue.message) for issue in errors] == [
        (
            "coverage_content_invalid",
            "S1",
            "The coverage BED of S1 declares content 'depth': it must be read_depth or log2_ratio",
        )
    ]
    assert warnings == []


def test_a_per_target_table_declares_nothing(tmp_path: Path) -> None:
    table = tmp_path / "coverage_S1.txt"
    table.write_text("chromosome\tstart\tend\tattribute\tmean\n1\t100\t200\tGENE1\t80\n", encoding="utf-8")

    errors, warnings = _validate(tmp_path, {"S1": {"target_table": "coverage_S1.txt"}})

    assert errors == [] and warnings == []


# --- the track availability ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_track_availability_serves_what_each_coverage_track_holds(monkeypatch: pytest.MonkeyPatch) -> None:
    service = "backend.app.services.family_service."

    async def family(*_args: Any, **_kwargs: Any) -> FamilyMetadataContext:
        return _family_context()

    async def sources(_session: Any, *, track_type: str, **_kwargs: Any) -> dict[str, list[str]]:
        if track_type != "coverage":
            return {}
        return {"S1": ["hificnv", "web", "wisecondorx"], "S2": ["web"]}

    async def nobody(*_args: Any, **_kwargs: Any) -> set[str]:
        return set()

    async def normalizations(*_args: Any, **_kwargs: Any) -> dict[str, dict[str, list[str]]]:
        return {"S1": {"hificnv": ["none"]}}

    async def declarations(*_args: Any, **_kwargs: Any) -> dict[str, dict[str, list[str]]]:
        # S1's BED was declared as read depth; S2's declared nothing (a package entry without
        # `content` records unknown, a track stored before BEDs declared it records none).
        return {"S1": {"web": ["read_depth"]}}

    monkeypatch.setattr(service + "build_family_metadata_context", family)
    monkeypatch.setattr(service + "get_track_sources_by_sample", sources)
    monkeypatch.setattr(service + "get_track_presence_by_sample", nobody)
    monkeypatch.setattr(service + "_repeat_expansion_presence_by_sample", nobody)
    monkeypatch.setattr(service + "_structural_present_sample_names", nobody)
    monkeypatch.setattr(service + "interval_track_normalizations_by_sample", normalizations)
    monkeypatch.setattr(service + "interval_track_contents_by_sample", declarations)

    availability = await get_family_track_availability_for_user(
        None,  # type: ignore[arg-type]
        family_id=FAMILY,
        user=None,  # type: ignore[arg-type]
        chromosomes=["M"],
        include_small_variants=False,
    )

    # Before: nothing said what a coverage BED held, and the views drew each on their log2-ratio
    # axis.
    assert {name: sample.coverage_contents for name, sample in availability.samples.items()} == {
        "S1": {"hificnv": "read_depth", "web": "read_depth", "wisecondorx": "log2_ratio"},
        "S2": {"web": "unknown"},
    }
