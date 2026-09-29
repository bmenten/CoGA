"""A family package imported from a bucket (gs:// or s3://) is traceable like a local one.

The import stages the package into a temporary directory that is deleted afterwards,
so nothing it records may point at, or be derived from, that directory:

* raw-file provenance (``raw_import_files``) identifies every file. A staged file is
  hashed from its staged copy, as a local file is hashed where it lies. A file left in
  the store (an alignment) is identified by the store's own record of the object --
  size, generation or version, and the store's checksums, each labelled with its
  algorithm, because none of them is the SHA-256 the ``sha256`` column holds.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.services import family_package_registration
from app.services.family_package_common import (
    FamilyPackageBundle,
    ManifestDataset,
    PackageManifest,
    ParsedPed,
)
from backend.tests._object_store_fakes import BUCKET, FakeS3, md5_base64, use_store

PED = "F1 S1 0 0 1 2\n"


class _ProvenanceSession:
    """Answers the sample lookup and keeps each raw_import_files row written."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def execute(self, statement: Any, params: Any = None) -> Any:
        if "INSERT INTO raw_import_files" in str(statement):
            self.rows.append({**params, "metadata": json.loads(params["metadata"])})
        samples = [{"sample_uuid": "uuid-S1", "sample_id": "S1"}]
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: samples))

    def by_path(self) -> dict[str, dict[str, Any]]:
        return {row["storage_path"]: row for row in self.rows}


def _package_bundle(root: Path, *, source_uri: str | None, remote_only_files: frozenset[str]) -> FamilyPackageBundle:
    """A package whose paraphase JSON was staged and whose CRAM stayed in the store."""
    json_path = root / "paraphase" / "S1" / "S1.paraphase.json"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text("{}")
    return FamilyPackageBundle(
        root=root,
        manifest_path=root / "manifest.yaml",
        manifest=PackageManifest(
            schema_version=1,
            ped="family.ped",
            datasets={
                "alignments": ManifestDataset(per_sample={"S1": {"file": "bams/S1.cram"}}),
                "paraphase": ManifestDataset(
                    per_sample={"S1": {"json": "paraphase/S1/S1.paraphase.json"}}
                ),
            },
        ),
        ped_path=root / "family.ped",
        ped=ParsedPed(family_ids=["F1"], members=[], sample_ids=["S1"], text=PED),
        source_uri=source_uri,
        remote_only_files=remote_only_files,
    )


async def _record(bundle: FamilyPackageBundle) -> dict[str, dict[str, Any]]:
    session = _ProvenanceSession()
    await family_package_registration._record_package_raw_files(
        session, bundle=bundle, family_uuid="family-uuid"  # type: ignore[arg-type]
    )
    return session.by_path()


# ---------------------------------------------------------------------------
# Raw-file provenance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("scheme", ["gs", "s3"])
async def test_a_staged_file_is_hashed_from_its_staged_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, scheme: str
) -> None:
    # The URI is what the row names, but it is not a file: hashing it gave no checksum
    # and no size for every file of a remote package.
    use_store(monkeypatch, scheme, {"imports/F1/bams/S1.cram": "cram"})
    source = f"{scheme}://{BUCKET}/imports/F1"

    rows = await _record(
        _package_bundle(tmp_path, source_uri=source, remote_only_files=frozenset({"bams/S1.cram"}))
    )

    staged = rows[f"{source}/paraphase/S1/S1.paraphase.json"]
    assert staged["sha256"] == hashlib.sha256(b"{}").hexdigest()
    assert staged["file_size"] == 2
    assert staged["metadata"] == {}


@pytest.mark.asyncio
async def test_a_file_left_in_gcs_is_identified_by_the_stores_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_store(monkeypatch, "gs", {"imports/F1/bams/S1.cram": "cram"})
    source = f"gs://{BUCKET}/imports/F1"

    rows = await _record(
        _package_bundle(tmp_path, source_uri=source, remote_only_files=frozenset({"bams/S1.cram"}))
    )

    cram = rows[f"{source}/bams/S1.cram"]
    # Not a SHA-256 the application computed, so not in the column that says so.
    assert cram["sha256"] is None
    assert cram["file_size"] == 4
    assert cram["metadata"] == {
        "store_object": {
            "store": "gcs",
            "uri": f"{source}/bams/S1.cram",
            "size": 4,
            "generation": "1727000000000001",
            "etag": "etag-of-imports/F1/bams/S1.cram",
            "checksums": [
                {"algorithm": "md5", "encoding": "base64", "value": md5_base64("cram")},
                {"algorithm": "crc32c", "encoding": "base64", "value": "crc32c-of-imports/F1/bams/S1.cram"},
            ],
        }
    }


@pytest.mark.asyncio
async def test_a_file_left_in_s3_is_identified_by_the_stores_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = use_store(monkeypatch, "s3", {"imports/F1/bams/S1.cram": "cram"})
    source = f"s3://{BUCKET}/imports/F1"

    rows = await _record(
        _package_bundle(tmp_path, source_uri=source, remote_only_files=frozenset({"bams/S1.cram"}))
    )

    cram = rows[f"{source}/bams/S1.cram"]
    assert cram["sha256"] is None
    assert cram["file_size"] == 4
    assert cram["metadata"] == {
        "store_object": {
            "store": "s3",
            "uri": f"{source}/bams/S1.cram",
            "size": 4,
            "version_id": "3HL4kqtJlcpXroDTDmJ-rmSpXd3dIbrHY",
            # A multipart ETag: an opaque identifier, not the object's MD5.
            "etag": '"9b2cf535f27731c974343645a3985328-3"',
            "checksums": [
                {"algorithm": "crc32c", "encoding": "base64", "value": "crc32c-full-object", "type": "FULL_OBJECT"},
            ],
        }
    }
    # S3 returns the additional checksums only when asked.
    assert isinstance(store, FakeS3) and store.last_head_kwargs == {"ChecksumMode": "ENABLED"}


@pytest.mark.asyncio
async def test_a_sha256_the_store_keeps_is_labelled_and_kept_out_of_the_sha256_column(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A multipart upload's SHA-256 is a checksum of the parts' checksums: it would never
    # match a SHA-256 of the file, so it must not sit where one is expected.
    store = use_store(monkeypatch, "s3", {"imports/F1/bams/S1.cram": "cram"})
    assert isinstance(store, FakeS3)
    store.head_extra = {"ETag": '"etag"', "ChecksumSHA256": "c2hhMjU2LW9mLXBhcnRz-3", "ChecksumType": "COMPOSITE"}
    source = f"s3://{BUCKET}/imports/F1"

    rows = await _record(
        _package_bundle(tmp_path, source_uri=source, remote_only_files=frozenset({"bams/S1.cram"}))
    )

    cram = rows[f"{source}/bams/S1.cram"]
    assert cram["sha256"] is None
    assert cram["metadata"]["store_object"]["checksums"] == [
        {"algorithm": "sha256", "encoding": "base64", "value": "c2hhMjU2LW9mLXBhcnRz-3", "type": "COMPOSITE"}
    ]
    assert cram["metadata"]["store_object"]["version_id"] is None  # an unversioned bucket


@pytest.mark.asyncio
async def test_a_store_error_costs_one_row_its_identity_not_the_package_its_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = use_store(monkeypatch, "gs", {"imports/F1/bams/S1.cram": "cram"})

    def unavailable(key: str) -> Any:
        raise RuntimeError("503 Service Unavailable")

    monkeypatch.setattr(store.bucket(BUCKET).__class__, "get_blob", lambda self, key: unavailable(key))
    source = f"gs://{BUCKET}/imports/F1"

    rows = await _record(
        _package_bundle(tmp_path, source_uri=source, remote_only_files=frozenset({"bams/S1.cram"}))
    )

    cram = rows[f"{source}/bams/S1.cram"]
    assert (cram["sha256"], cram["file_size"], cram["metadata"]) == (None, None, {})
    # The staged file is still hashed.
    assert rows[f"{source}/paraphase/S1/S1.paraphase.json"]["sha256"] == hashlib.sha256(b"{}").hexdigest()


@pytest.mark.asyncio
async def test_a_local_package_is_still_hashed_where_it_lies(tmp_path: Path) -> None:
    (tmp_path / "bams").mkdir()
    (tmp_path / "bams" / "S1.cram").write_text("cram")

    rows = await _record(_package_bundle(tmp_path, source_uri=None, remote_only_files=frozenset()))

    assert rows[str(tmp_path / "bams" / "S1.cram")]["sha256"] == hashlib.sha256(b"cram").hexdigest()
    json_row = rows[str(tmp_path / "paraphase" / "S1" / "S1.paraphase.json")]
    assert (json_row["sha256"], json_row["file_size"], json_row["metadata"]) == (
        hashlib.sha256(b"{}").hexdigest(),
        2,
        {},
    )
