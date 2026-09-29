"""Aligned reads of a family package imported from a bucket (STORAGE_BACKEND=gcs/s3).

A whole-genome CRAM is tens of GB and nothing in the import reads it: the alignments
importer records where the file lies, and the genome browser streams it from the store
through a signed URL. Staging used to download every object of a gs:// or s3:// package
into /tmp, which on Cloud Run is memory, and the CRAM endpoint then only looked at
``<family>/[bams/|alignments/]<sample>.cram`` at the bucket root, not where the package
keeps its reads (``imports/<family>/bams/<sample>.cram``). These tests pin the contract:

* staging leaves alignments and their indexes in the store and says which files it left;
* only the alignments dataset may point at such a file, since only its importer never
  reads the bytes;
* the alignments importer asks the store whether the file exists and records its URI;
* raw-file provenance still names every file, including the ones left in the store;
* the CRAM endpoint serves the recorded object first -- but only an object in the
  configured bucket below FAMILY_IMPORT_ROOTS, so a tampered metadata row cannot make it
  sign a URL for anything else -- and falls back to the layout probes.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from app.core import object_storage
from app.core.config import settings
from app.routers import cram
from app.schemas import FamilyImportDatasetSummary
from app.services import (
    family_package_datasets,
    family_package_registration,
    family_package_source,
    family_package_validation,
)
from app.services.family_metadata_context import SampleMetadataContext
from app.services.family_package_common import (
    FamilyPackageBundle,
    ManifestDataset,
    PackageManifest,
    ParsedPed,
)
from app.services.family_package_datasets import DatasetImportJob
from backend.tests._object_store_fakes import BUCKET, package_objects, stage_under, use_store


PED = "F1 S1 0 0 1 2\n"
ALIGNMENTS_MANIFEST = """schema_version: 1
family_id: F1
ped: family.ped
datasets:
  alignments:
    per_sample:
      S1:
        file: bams/S1.cram
        index: bams/S1.cram.crai
"""

# A package as the long-read pipeline lays it out, relative to its folder.
PACKAGE = {
    "manifest.yaml": ALIGNMENTS_MANIFEST,
    "family.ped": PED,
    "snv/F1.vcf.gz": "vcf",
    # A VCF's CSI index is read by the importers: it is staged.
    "snv/F1.vcf.gz.csi": "vcf index",
    "bams/S1.cram": "cram",
    "bams/S1.cram.crai": "crai",
    "bams/S2.bam": "bam",
    "bams/S2.bam.bai": "bai",
    # A BAM's CSI index is an alignment index: it stays in the store.
    "bams/S2.bam.csi": "bam csi",
    "bams/S3.bai": "bai named without .bam",
    "bams/S4.CRAM": "upper-case extension",
    "paraphase/S1/S1.paraphase.bam": "realigned reads",
    "paraphase/S1/S1.paraphase.json": "{}",
}
LEFT_IN_STORE = {
    "bams/S1.cram",
    "bams/S1.cram.crai",
    "bams/S2.bam",
    "bams/S2.bam.bai",
    "bams/S2.bam.csi",
    "bams/S3.bai",
    "bams/S4.CRAM",
    "paraphase/S1/S1.paraphase.bam",
}


def _files_under(root: Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}


# ---------------------------------------------------------------------------
# (a) Staging leaves alignments in the store
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scheme", ["gs", "s3"])
def test_staging_leaves_alignments_and_their_indexes_in_the_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, scheme: str
) -> None:
    store = use_store(monkeypatch, scheme, package_objects(PACKAGE))
    stage_under(monkeypatch, tmp_path)
    staged_names = PACKAGE.keys() - LEFT_IN_STORE

    with family_package_source.staged_package_source(f"{scheme}://{BUCKET}/imports/F1") as staged:
        # Never fetched: a WGS CRAM would fill the memory-backed /tmp of the instance.
        assert set(store.downloaded) == {f"imports/F1/{name}" for name in staged_names}
        root = Path(staged.root)
        assert _files_under(root) == staged_names
        assert staged.source_uri == f"{scheme}://{BUCKET}/imports/F1"
        assert staged.remote_only_files == frozenset(LEFT_IN_STORE)

    assert not root.exists()


@pytest.mark.parametrize("scheme", ["gs", "s3"])
def test_a_remote_package_validates_without_downloading_its_alignments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, scheme: str
) -> None:
    store = use_store(monkeypatch, scheme, package_objects(PACKAGE))
    stage_under(monkeypatch, tmp_path)

    result = family_package_validation.validate_family_package(f"{scheme}://{BUCKET}/imports/F1")

    assert result.valid, result.errors
    assert "imports/F1/bams/S1.cram" not in store.downloaded
    alignments = next(item for item in result.datasets if item.dataset_type == "alignments")
    assert alignments.status == "valid"
    assert alignments.files == ["bams/S1.cram", "bams/S1.cram.crai"]


@pytest.mark.parametrize(
    ("dataset_type", "role"),
    [
        ("coverage", "bed"),  # a validator of its own
        ("cnv", "vcf"),  # shares the per-sample validator with alignments
    ],
)
def test_a_dataset_that_reads_its_file_cannot_use_one_left_in_the_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, dataset_type: str, role: str
) -> None:
    # These importers read their file; a manifest pointing one at an alignment must fail
    # validation rather than import from a file that was never staged.
    package = {
        "manifest.yaml": (
            "schema_version: 1\nfamily_id: F1\nped: family.ped\n"
            f"datasets:\n  {dataset_type}:\n    per_sample:\n      S1:\n        {role}: bams/S1.bam\n"
        ),
        "family.ped": PED,
        "bams/S1.bam": "bam",
    }
    use_store(monkeypatch, "gs", package_objects(package))
    stage_under(monkeypatch, tmp_path)

    result = family_package_validation.validate_family_package(f"gs://{BUCKET}/imports/F1")

    assert not result.valid
    assert [error.code for error in result.errors] == ["dataset_file_missing"]


def test_an_alignment_missing_from_the_store_is_still_a_validation_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    package = {**PACKAGE, "manifest.yaml": ALIGNMENTS_MANIFEST.replace("bams/S1.cram\n", "bams/S9.cram\n")}
    use_store(monkeypatch, "gs", package_objects(package))
    stage_under(monkeypatch, tmp_path)

    result = family_package_validation.validate_family_package(f"gs://{BUCKET}/imports/F1")

    assert not result.valid
    assert [(error.code, error.sample_id) for error in result.errors] == [("dataset_file_missing", "S1")]


# ---------------------------------------------------------------------------
# (b) The alignments importer records where a remote alignment lies
# ---------------------------------------------------------------------------


def _sample_context(sample_id: str) -> SampleMetadataContext:
    return SampleMetadataContext(
        sample_uuid=f"uuid-{sample_id}",
        sample_id=sample_id,
        family_uuid="family-uuid",
        family_id="F1",
        sex="und",
        project_ids=["project-uuid"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


def _bundle(root: Path, *, source_uri: str | None = None, **extra: Any) -> FamilyPackageBundle:
    return FamilyPackageBundle(
        root=root,
        manifest_path=root / "manifest.yaml",
        manifest=PackageManifest(schema_version=1, ped="family.ped"),
        ped_path=root / "family.ped",
        ped=ParsedPed(family_ids=["F1"], members=[], sample_ids=["S1", "S2"], text=PED),
        source_uri=source_uri,
        **extra,
    )


def _capture_alignment_records(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, Any]]:
    recorded: dict[str, dict[str, Any]] = {}

    async def record(session: Any, *, sample_context: SampleMetadataContext, alignment: dict[str, Any]) -> None:
        recorded[sample_context.sample_id] = alignment

    monkeypatch.setattr(family_package_datasets, "_record_sample_alignment_metadata", record)
    return recorded


async def _import_alignments(
    bundle: FamilyPackageBundle, per_sample: dict[str, dict[str, Any]]
) -> FamilyImportDatasetSummary:
    return await family_package_datasets._import_alignments_dataset(
        DatasetImportJob(
            session=object(),  # type: ignore[arg-type] - the record call is captured
            bundle=bundle,
            dataset=ManifestDataset(per_sample=per_sample),
            summary=FamilyImportDatasetSummary(dataset_type="alignments", enabled=True, status="valid"),
            family_context=None,  # type: ignore[arg-type] - not read by this importer
            sample_contexts={sample_id: _sample_context(sample_id) for sample_id in per_sample},
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("scheme", ["gs", "s3"])
async def test_alignments_importer_records_where_a_remote_alignment_lies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, scheme: str
) -> None:
    use_store(
        monkeypatch,
        scheme,
        {"imports/F1/bams/S1.cram": "cram", "imports/F1/bams/S1.cram.crai": "crai"},
    )
    recorded = _capture_alignment_records(monkeypatch)
    source = f"{scheme}://{BUCKET}/imports/F1"

    # The staging directory holds no alignment: staging left it in the store.
    result = await _import_alignments(
        _bundle(tmp_path, source_uri=source),
        {"S1": {"file": "bams/S1.cram", "index": "bams/S1.cram.crai"}},
    )

    assert result.status == "imported"
    assert recorded == {
        "S1": {
            "path": "bams/S1.cram",
            "format": "cram",
            "uri": f"{source}/bams/S1.cram",
            "index_path": "bams/S1.cram.crai",
            "index_uri": f"{source}/bams/S1.cram.crai",
        }
    }


@pytest.mark.asyncio
async def test_alignments_importer_records_only_what_the_store_holds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # S1's CRAM is not in the store; S2's is, but its declared index is not.
    use_store(monkeypatch, "gs", {"imports/F1/bams/S2.bam": "bam"})
    recorded = _capture_alignment_records(monkeypatch)
    source = f"gs://{BUCKET}/imports/F1"

    result = await _import_alignments(
        _bundle(tmp_path, source_uri=source),
        {
            "S1": {"file": "bams/S1.cram", "index": "bams/S1.cram.crai"},
            "S2": {"file": "bams/S2.bam", "index": "bams/S2.bam.bai"},
        },
    )

    assert result.status == "imported"
    assert recorded == {"S2": {"path": "bams/S2.bam", "format": "bam", "uri": f"{source}/bams/S2.bam"}}


@pytest.mark.asyncio
async def test_alignments_importer_keeps_the_package_relative_path_for_a_local_package(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def no_store() -> None:
        raise AssertionError("a local import must not ask an object store")

    monkeypatch.setattr(object_storage, "_gcs_client", no_store)
    monkeypatch.setattr(object_storage, "_s3_client", no_store)
    (tmp_path / "bams").mkdir()
    (tmp_path / "bams" / "S1.cram").write_text("cram")
    (tmp_path / "bams" / "S1.cram.crai").write_text("crai")
    recorded = _capture_alignment_records(monkeypatch)

    result = await _import_alignments(
        _bundle(tmp_path),
        {
            "S1": {"file": "bams/S1.cram", "index": "bams/S1.cram.crai"},
            "S2": {"file": "bams/S2.cram"},  # not on disk
        },
    )

    assert result.status == "imported"
    assert recorded == {
        "S1": {"path": "bams/S1.cram", "format": "cram", "index_path": "bams/S1.cram.crai"}
    }


# ---------------------------------------------------------------------------
# Raw-file provenance still names the files left in the store
# ---------------------------------------------------------------------------


class _SampleRowsSession:
    async def execute(self, statement: Any, params: Any = None) -> Any:
        rows = [{"sample_uuid": "uuid-S1", "sample_id": "S1"}]
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))


@pytest.mark.asyncio
async def test_provenance_names_the_store_location_of_files_left_in_the_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "paraphase" / "S1").mkdir(parents=True)
    (tmp_path / "paraphase" / "S1" / "S1.paraphase.json").write_text("{}")
    use_store(
        monkeypatch,
        "gs",
        package_objects(
            {
                "bams/S1.cram": "cram",
                "bams/S1.cram.crai": "crai",
                "paraphase/S1/S1.paraphase.bam": "bam",
                "paraphase/S1/S1.paraphase.json": "{}",
            }
        ),
    )
    source = f"gs://{BUCKET}/imports/F1"
    bundle = _bundle(
        tmp_path,
        source_uri=source,
        remote_only_files=frozenset(
            {"bams/S1.cram", "bams/S1.cram.crai", "paraphase/S1/S1.paraphase.bam"}
        ),
    )
    bundle.manifest = PackageManifest(
        schema_version=1,
        ped="family.ped",
        datasets={
            "alignments": ManifestDataset(
                per_sample={"S1": {"file": "bams/S1.cram", "index": "bams/S1.cram.crai"}}
            ),
            "paraphase": ManifestDataset(
                per_sample={
                    "S1": {
                        "json": "paraphase/S1/S1.paraphase.json",
                        "bam": "paraphase/S1/S1.paraphase.bam",
                    }
                }
            ),
        },
    )
    rows: list[dict[str, Any]] = []

    async def record(session: Any, **kwargs: Any) -> None:
        rows.append(kwargs)

    monkeypatch.setattr(family_package_registration, "record_raw_import_file", record)

    await family_package_registration._record_package_raw_files(
        _SampleRowsSession(), bundle=bundle, family_uuid="family-uuid"  # type: ignore[arg-type]
    )

    assert {(row["dataset"], row["storage_path"]) for row in rows} == {
        ("alignments", f"{source}/bams/S1.cram"),
        ("alignments", f"{source}/bams/S1.cram.crai"),
        ("paraphase", f"{source}/paraphase/S1/S1.paraphase.json"),
        ("paraphase", f"{source}/paraphase/S1/S1.paraphase.bam"),
    }
    assert {row["sample_uuid"] for row in rows} == {"uuid-S1"}


# ---------------------------------------------------------------------------
# (c) The CRAM endpoint serves the recorded location first
# ---------------------------------------------------------------------------


def _recorded(scheme: str = "gs", *, index: bool = True, **overrides: Any) -> dict[str, Any]:
    """``samples.metadata['alignment']`` as the importer writes it for a remote package."""
    source = f"{scheme}://{BUCKET}/imports/F1"
    entry: dict[str, Any] = {"path": "bams/S1.cram", "format": "cram", "uri": f"{source}/bams/S1.cram"}
    if index:
        entry.update(index_path="bams/S1.cram.crai", index_uri=f"{source}/bams/S1.cram.crai")
    entry.update(overrides)
    return entry


class _RecordedAlignmentsSession:
    """Answers the endpoint's query for each sample's ``metadata -> 'alignment'``."""

    def __init__(self, alignments: dict[str, Any]) -> None:
        self.alignments = alignments
        self.queries = 0

    async def execute(self, statement: Any, params: Any = None) -> Any:
        self.queries += 1
        wanted = set((params or {}).get("sample_ids") or [])
        rows = [(sample_id, value) for sample_id, value in self.alignments.items() if sample_id in wanted]
        return SimpleNamespace(all=lambda: rows)


def _family_members(monkeypatch: pytest.MonkeyPatch, *sample_ids: str) -> None:
    async def family_record(session: Any, family_id: str, user: Any) -> Any:
        return SimpleNamespace(members=[SimpleNamespace(sample_id=sample_id) for sample_id in sample_ids])

    monkeypatch.setattr(cram, "get_family_record", family_record)


def _sign_urls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    signed: list[str] = []

    def presign(key: str, *, filename: str | None = None, expires: int | None = None) -> str:
        signed.append(key)
        return f"https://signed.example/{key}"

    monkeypatch.setattr(cram, "presigned_get_url", presign)
    return signed


@pytest.mark.asyncio
@pytest.mark.parametrize("scheme", ["gs", "s3"])
async def test_manifest_signs_the_recorded_objects(monkeypatch: pytest.MonkeyPatch, scheme: str) -> None:
    use_store(
        monkeypatch,
        scheme,
        {"imports/F1/bams/S1.cram": "cram", "imports/F1/bams/S1.cram.crai": "crai"},
    )
    _family_members(monkeypatch, "S1", "S2")
    _sign_urls(monkeypatch)
    session = _RecordedAlignmentsSession({"S1": _recorded(scheme)})

    entries = await cram.get_alignment_manifest(
        "F1", sample_ids=["S1", "S2"], session=session, user=object()  # type: ignore[arg-type]
    )

    assert [entry.model_dump() for entry in entries] == [
        {
            "sample_id": "S1",
            "format": "cram",
            "url": "https://signed.example/imports/F1/bams/S1.cram",
            "index_url": "https://signed.example/imports/F1/bams/S1.cram.crai",
        }
    ]
    assert session.queries == 1  # one query for all the requested samples


@pytest.mark.asyncio
async def test_get_and_head_redirect_to_the_recorded_objects(monkeypatch: pytest.MonkeyPatch) -> None:
    use_store(
        monkeypatch,
        "gs",
        {
            "imports/F1/bams/S1.cram": "cram",
            "imports/F1/bams/S1.cram.crai": "crai",
            # Some other BAM at a layout-probe key.
            "F1/S1.bam": "bam",
        },
    )
    _family_members(monkeypatch, "S1")
    _sign_urls(monkeypatch)
    session: Any = _RecordedAlignmentsSession({"S1": _recorded()})

    cram_response = await cram.get_cram("F1", "S1", session=session, user=object())
    crai_response = await cram.get_crai("F1", "S1", session=session, user=object())

    assert cram_response.status_code == 302
    assert cram_response.headers["location"] == "https://signed.example/imports/F1/bams/S1.cram"
    assert crai_response.status_code == 302
    assert crai_response.headers["location"] == "https://signed.example/imports/F1/bams/S1.cram.crai"
    assert (await cram.head_cram("F1", "S1", session=session, user=object())).status_code == 200
    assert (await cram.head_crai("F1", "S1", session=session, user=object())).status_code == 200
    # A recorded CRAM answers for the sample: the BAM routes do not probe for another file.
    with pytest.raises(HTTPException) as exc:
        await cram.get_bam("F1", "S1", session=session, user=object())
    assert exc.value.status_code == 404


# Recorded locations a tampered or stale metadata row could carry. The configured bucket
# holds an object at each one's key, so only the check on the location keeps it from
# being signed.
UNTRUSTED_LOCATIONS = {
    "outside FAMILY_IMPORT_ROOTS": _recorded(uri="gs://phi/elsewhere/S1.cram", index=False),
    "a sibling of the import root": _recorded(uri="gs://phi/imports-old/F1/bams/S1.cram", index=False),
    "another bucket": _recorded(uri="gs://other-bucket/imports/F1/bams/S1.cram", index=False),
    "another store's scheme": _recorded(uri="s3://phi/imports/F1/bams/S1.cram", index=False),
    "dot segments": _recorded(uri="gs://phi/imports/F1/../../secrets/S1.cram", index=False),
    "an empty segment": _recorded(uri="gs://phi/imports//S1.cram", index=False),
    "a query string": _recorded(uri="gs://phi/imports/F1/bams/S1.cram?generation=1", index=False),
    "a fragment": _recorded(uri="gs://phi/imports/F1/bams/S1.cram#x", index=False),
    "user info": _recorded(uri="gs://someone@phi/imports/F1/bams/S1.cram", index=False),
    "an extension that is not the format": _recorded(uri="gs://phi/imports/F1/bams/S1.bam", index=False),
    "an unknown format": _recorded(format="sam", index=False),
    "a format that is not a string": _recorded(format=["cram"], index=False),
    "no uri": {"path": "bams/S1.cram", "format": "cram"},
    "not a URI": _recorded(uri=42, index=False),
    "not a mapping": ["gs://phi/imports/F1/bams/S1.cram"],
}


@pytest.mark.asyncio
@pytest.mark.parametrize("recorded", UNTRUSTED_LOCATIONS.values(), ids=UNTRUSTED_LOCATIONS.keys())
async def test_an_untrusted_recorded_location_is_never_signed(
    monkeypatch: pytest.MonkeyPatch, recorded: Any
) -> None:
    uri = recorded.get("uri") if isinstance(recorded, dict) else None
    key = object_storage.parse_remote_uri(uri).key if isinstance(uri, str) else "imports/F1/bams/S1.cram"
    use_store(monkeypatch, "gs", {key: "cram", f"{key}.crai": "crai", "imports/F1/bams/S1.cram": "cram"})
    _family_members(monkeypatch, "S1")
    signed = _sign_urls(monkeypatch)
    session: Any = _RecordedAlignmentsSession({"S1": recorded})

    for endpoint in (cram.get_cram, cram.head_cram, cram.get_crai):
        with pytest.raises(HTTPException) as exc:
            await endpoint("F1", "S1", session=session, user=object())
        assert exc.value.status_code == 404
    assert await cram.get_alignment_manifest("F1", sample_ids=["S1"], session=session, user=object()) == []
    assert signed == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "uri",
    ["gs://other-bucket/imports/F1/bams/S1.cram", "s3://phi/imports/F1/bams/S1.cram"],
    ids=["another bucket", "another store"],
)
async def test_a_location_another_import_root_allows_is_not_signed_in_the_configured_bucket(
    monkeypatch: pytest.MonkeyPatch, uri: str
) -> None:
    # FAMILY_IMPORT_ROOTS may name other buckets or stores to import from, but URLs are
    # signed for keys in the configured bucket: signing this key there would serve
    # whatever object happens to have the same name.
    use_store(monkeypatch, "gs", {"imports/F1/bams/S1.cram": "cram", "imports/F1/bams/S1.cram.crai": "crai"})
    monkeypatch.setattr(
        settings,
        "family_import_roots",
        ["gs://phi/imports", "gs://other-bucket/imports", "s3://phi/imports"],
    )
    _family_members(monkeypatch, "S1")
    signed = _sign_urls(monkeypatch)
    session: Any = _RecordedAlignmentsSession({"S1": _recorded(uri=uri, index=False)})

    with pytest.raises(HTTPException) as exc:
        await cram.get_cram("F1", "S1", session=session, user=object())
    assert exc.value.status_code == 404
    assert await cram.get_alignment_manifest("F1", sample_ids=["S1"], session=session, user=object()) == []
    assert signed == []


@pytest.mark.asyncio
async def test_a_recorded_cram_is_never_paired_with_a_probed_index(monkeypatch: pytest.MonkeyPatch) -> None:
    # The recorded index is gone, but the family-root layout probe holds one. It indexes
    # some other file: IGV would read the recorded CRAM at the wrong offsets.
    use_store(monkeypatch, "gs", {"imports/F1/bams/S1.cram": "cram", "F1/S1.cram.crai": "crai"})
    _family_members(monkeypatch, "S1")
    signed = _sign_urls(monkeypatch)
    session: Any = _RecordedAlignmentsSession({"S1": _recorded()})

    assert await cram.get_alignment_manifest("F1", sample_ids=["S1"], session=session, user=object()) == []
    with pytest.raises(HTTPException) as exc:
        await cram.get_crai("F1", "S1", session=session, user=object())
    assert exc.value.status_code == 404
    assert "F1/S1.cram.crai" not in signed


@pytest.mark.asyncio
async def test_a_recorded_cram_without_a_recorded_index_uses_the_one_beside_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_store(
        monkeypatch,
        "gs",
        {"imports/F1/bams/S1.cram": "cram", "imports/F1/bams/S1.cram.crai": "crai"},
    )
    _family_members(monkeypatch, "S1")
    _sign_urls(monkeypatch)
    session: Any = _RecordedAlignmentsSession({"S1": _recorded(index=False)})

    entries = await cram.get_alignment_manifest("F1", sample_ids=["S1"], session=session, user=object())

    assert [entry.index_url for entry in entries] == ["https://signed.example/imports/F1/bams/S1.cram.crai"]


@pytest.mark.asyncio
async def test_the_layout_probes_still_serve_a_sample_with_no_usable_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # S1's recorded object was removed from the store; S2 has no record at all. Both fall
    # back to the conventional <family>/<sample>.cram keys.
    use_store(
        monkeypatch,
        "gs",
        {
            "F1/S1.cram": "cram",
            "F1/S1.cram.crai": "crai",
            "F1/bams/S2.bam": "bam",
            "F1/bams/S2.bam.bai": "bai",
        },
    )
    _family_members(monkeypatch, "S1", "S2")
    _sign_urls(monkeypatch)
    session: Any = _RecordedAlignmentsSession({"S1": _recorded()})

    entries = await cram.get_alignment_manifest(
        "F1", sample_ids=["S1", "S2"], session=session, user=object()
    )
    response = await cram.get_cram("F1", "S1", session=session, user=object())

    assert [(entry.sample_id, entry.url) for entry in entries] == [
        ("S1", "https://signed.example/F1/S1.cram"),
        ("S2", "https://signed.example/F1/bams/S2.bam"),
    ]
    assert response.headers["location"] == "https://signed.example/F1/S1.cram"


@pytest.mark.asyncio
async def test_local_mode_does_not_read_the_recorded_location(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "storage_backend", "local")
    _family_members(monkeypatch, "S1")

    class _NoQueries:
        async def execute(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError("local mode serves files from the data directory")

    with pytest.raises(HTTPException) as exc:
        await cram.get_cram("F1", "S_NO_FILE", session=_NoQueries(), user=object())  # type: ignore[arg-type]
    assert exc.value.status_code == 404  # the member check, before any lookup
    with pytest.raises(HTTPException) as exc:
        await cram.get_cram("F1", "S1", session=_NoQueries(), user=object())  # type: ignore[arg-type]
    assert exc.value.status_code == 404
    assert exc.value.detail == "CRAM file not found"
