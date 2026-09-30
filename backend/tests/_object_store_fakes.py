"""In-memory stand-ins for a GCS or S3 bucket, shared by the remote-import tests.

Just enough of google-cloud-storage and boto3 for what the backend calls: listing a
prefix, existence, download, and the object metadata a store keeps (size, generation
or version, checksums). ``use_store`` points the backend at one, laid out as Terraform
lays out the PHI bucket: bucket ``phi`` with FAMILY_IMPORT_ROOTS=<scheme>://phi/imports.

Import as ``from backend.tests._object_store_fakes import ...``. The helpers patch the
``app.`` module tree, which is the one the tests using them import.
"""

from __future__ import annotations

import base64
import hashlib
import threading
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError

from app.core import object_storage
from app.core.config import settings

BUCKET = "phi"


def md5_base64(body: str) -> str:
    """How GCS reports an object's MD5 (``md5Hash``)."""
    return base64.b64encode(hashlib.md5(body.encode()).digest()).decode()


class _GcsBlob:
    def __init__(self, store: "FakeGcs", bucket: str, name: str) -> None:
        self._store, self._bucket, self.name = store, bucket, name

    @property
    def _body(self) -> str:
        return self._store.objects[(self._bucket, self.name)]

    def exists(self) -> bool:
        return (self._bucket, self.name) in self._store.objects

    def download_to_filename(self, target: str) -> None:
        self._store.record_download(self.name)
        Path(target).write_text(self._body)

    # Object metadata, as a Blob from get_blob() carries it.
    @property
    def size(self) -> int:
        return len(self._body.encode())

    @property
    def generation(self) -> int:
        return self._store.generation

    @property
    def md5_hash(self) -> str:
        return md5_base64(self._body)

    @property
    def crc32c(self) -> str:
        return f"crc32c-of-{self.name}"

    @property
    def etag(self) -> str:
        return f"etag-of-{self.name}"


class _GcsBucket:
    def __init__(self, store: "FakeGcs", name: str) -> None:
        self._store, self._name = store, name

    def blob(self, key: str) -> _GcsBlob:
        return _GcsBlob(self._store, self._name, key)

    def get_blob(self, key: str) -> _GcsBlob | None:
        blob = _GcsBlob(self._store, self._name, key)
        return blob if blob.exists() else None


class FakeGcs:
    generation = 1727000000000001

    def __init__(self, objects: dict[str, str]) -> None:
        self.objects = {(BUCKET, key): body for key, body in objects.items()}
        self.downloaded: list[str] = []
        self._lock = threading.Lock()

    def record_download(self, key: str) -> None:
        with self._lock:  # staging downloads from a thread pool
            self.downloaded.append(key)

    def bucket(self, name: str) -> _GcsBucket:
        return _GcsBucket(self, name)

    def list_blobs(self, bucket: str, prefix: str | None = None, delimiter: str | None = None):
        return [
            _GcsBlob(self, name_bucket, key)
            for name_bucket, key in sorted(self.objects)
            if name_bucket == bucket and key.startswith(prefix or "")
        ]


class FakeS3(FakeGcs):
    # What HEAD Object returns beyond the size (with ChecksumMode=ENABLED): an ETag that
    # is not a content hash (multipart upload), a version id, and additional checksums.
    head_extra: dict[str, Any] = {
        "ETag": '"9b2cf535f27731c974343645a3985328-3"',
        "VersionId": "3HL4kqtJlcpXroDTDmJ-rmSpXd3dIbrHY",
        "ChecksumCRC32C": "crc32c-full-object",
        "ChecksumType": "FULL_OBJECT",
    }

    def get_paginator(self, name: str) -> "FakeS3":
        assert name == "list_objects_v2"
        return self

    def paginate(self, Bucket: str, Prefix: str):  # boto3's keyword names
        return [
            {
                "Contents": [
                    {"Key": key}
                    for bucket, key in sorted(self.objects)
                    if bucket == Bucket and key.startswith(Prefix)
                ]
            }
        ]

    def download_file(self, bucket: str, key: str, target: str) -> None:
        self.record_download(key)
        Path(target).write_text(self.objects[(bucket, key)])

    def head_object(self, Bucket: str, Key: str, **kwargs: Any) -> dict[str, Any]:
        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
        self.last_head_kwargs = kwargs
        return {"ContentLength": len(self.objects[(Bucket, Key)].encode()), **self.head_extra}


def use_store(monkeypatch: pytest.MonkeyPatch, scheme: str, objects: dict[str, str]) -> FakeGcs:
    """Point the backend at a fake bucket ``phi`` with FAMILY_IMPORT_ROOTS=<scheme>://phi/imports."""
    if scheme == "gs":
        store: FakeGcs = FakeGcs(objects)
        monkeypatch.setattr(object_storage, "_gcs_client", lambda: store)
        monkeypatch.setattr(settings, "storage_backend", "gcs")
        monkeypatch.setattr(settings, "gcs_bucket", BUCKET)
        monkeypatch.setattr(settings, "gcs_prefix", "")
    else:
        store = FakeS3(objects)
        monkeypatch.setattr(object_storage, "_s3_client", lambda: store)
        monkeypatch.setattr(settings, "storage_backend", "s3")
        monkeypatch.setattr(settings, "s3_bucket", BUCKET)
        monkeypatch.setattr(settings, "s3_prefix", "")
    monkeypatch.setattr(settings, "family_import_roots", [f"{scheme}://{BUCKET}/imports"])
    return store


def package_objects(package: dict[str, str], family: str = "F1") -> dict[str, str]:
    """A package's files as objects under imports/<family>/."""
    return {f"imports/{family}/{name}": body for name, body in package.items()}


def stage_under(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Stage remote packages under ``tmp_path`` rather than the system temp dir."""
    from app.services import family_package_source

    staging = (tmp_path / "staging").resolve()
    staging.mkdir()
    monkeypatch.setattr(family_package_source, "_staging_root", lambda: staging)
    return staging
