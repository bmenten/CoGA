"""File-integrity (checksum) verification — REQ-DATA-004.

Verifies that `verify_raw_import_file` recomputes the stored file's SHA-256 and
correctly reports verified / mismatch / missing / unverifiable. A wrong or
corrupted source file must be detected, not silently trusted (risk H4).
"""

import asyncio
import hashlib
from pathlib import Path

from app.services import raw_import_files_pg as rif


def _sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_verify_reports_verified_when_checksum_matches(tmp_path):
    f = tmp_path / "sample.vcf.gz"
    f.write_bytes(b"clinical genomic payload")
    expected = _sha256_of(f)

    result = asyncio.run(
        rif.verify_raw_import_file(
            {"id": "rec-1", "storage_path": str(f), "sha256": expected}
        )
    )

    assert result["status"] == "verified"
    assert result["computed_sha256"] == expected
    assert result["expected_sha256"] == expected


def test_verify_detects_a_corrupted_or_altered_file(tmp_path):
    f = tmp_path / "sample.vcf.gz"
    f.write_bytes(b"original payload")

    result = asyncio.run(
        rif.verify_raw_import_file(
            {"id": "rec-2", "storage_path": str(f), "sha256": "0" * 64}
        )
    )

    assert result["status"] == "mismatch"
    assert result["computed_sha256"] == _sha256_of(f)
    assert result["computed_sha256"] != result["expected_sha256"]


def test_verify_reports_missing_when_file_is_absent(tmp_path):
    result = asyncio.run(
        rif.verify_raw_import_file(
            {"id": "rec-3", "storage_path": str(tmp_path / "gone.vcf.gz"), "sha256": "abc"}
        )
    )

    assert result["status"] == "missing"
    assert result["computed_sha256"] is None


def test_verify_reports_unverifiable_without_a_recorded_checksum(tmp_path):
    f = tmp_path / "sample.vcf.gz"
    f.write_bytes(b"payload")

    result = asyncio.run(
        rif.verify_raw_import_file(
            {"id": "rec-4", "storage_path": str(f), "sha256": None}
        )
    )

    assert result["status"] == "unverifiable"
    assert result["computed_sha256"] == _sha256_of(f)


# --- A file kept in an object store (a package imported from a bucket) -------------
#
# Its row names the object's URI, which is not a local path: it must not be reported
# missing. Verify asks the store instead, and compares the object with what was
# recorded at import -- without re-hashing, since an alignment is tens of GB.

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app.routers import admin  # noqa: E402
from backend.tests._object_store_fakes import BUCKET, FakeGcs, FakeS3, use_store  # noqa: E402

CRAM_URI = f"gs://{BUCKET}/imports/F1/bams/S1.cram"


def _remote_record(uri=CRAM_URI, *, size=4, sha256=None, **store_object):
    return {
        "id": "rec-remote",
        "storage_path": uri,
        "sha256": sha256,
        "file_size": size,
        "metadata": {"store_object": store_object} if store_object else {},
    }


def _no_rehash(monkeypatch):
    def refuse(path):
        raise AssertionError("a file in an object store is not re-hashed on Verify")

    monkeypatch.setattr(rif, "_hash_and_size", refuse)


def test_a_file_left_in_the_store_is_verified_against_the_stores_record(monkeypatch):
    use_store(monkeypatch, "gs", {"imports/F1/bams/S1.cram": "cram"})
    _no_rehash(monkeypatch)

    result = asyncio.run(rif.verify_raw_import_file(_remote_record(generation=str(FakeGcs.generation))))

    assert result["status"] == "verified"
    assert result["computed_sha256"] is None
    assert "size" in result["message"] and "generation" in result["message"]


def test_a_staged_file_is_verified_by_its_object_not_re_hashed(monkeypatch):
    use_store(monkeypatch, "gs", {"imports/F1/qc/S1.NanoStats.txt": "stats"})
    _no_rehash(monkeypatch)
    record = _remote_record(
        f"gs://{BUCKET}/imports/F1/qc/S1.NanoStats.txt",
        size=5,
        sha256="ab" * 32,
        generation=str(FakeGcs.generation),
    )

    result = asyncio.run(rif.verify_raw_import_file(record))

    assert (result["status"], result["expected_sha256"], result["computed_sha256"]) == ("verified", "ab" * 32, None)


def test_a_vanished_object_is_reported_missing_from_the_store(monkeypatch):
    use_store(monkeypatch, "gs", {})

    result = asyncio.run(rif.verify_raw_import_file(_remote_record(generation="1")))

    assert result["status"] == "missing"
    assert "store" in result["message"]


@pytest.mark.parametrize(
    ("record", "differs"),
    [
        (_remote_record(size=999, generation=str(FakeGcs.generation)), "size"),
        (_remote_record(generation="1600000000000000"), "generation"),
    ],
    ids=["size", "generation (the object was replaced)"],
)
def test_an_object_that_is_not_the_one_imported_is_a_mismatch(monkeypatch, record, differs):
    use_store(monkeypatch, "gs", {"imports/F1/bams/S1.cram": "cram"})

    result = asyncio.run(rif.verify_raw_import_file(record))

    assert result["status"] == "mismatch"
    assert differs in result["message"]


def test_an_s3_object_is_compared_by_version_id_where_the_bucket_keeps_versions(monkeypatch):
    use_store(monkeypatch, "s3", {"imports/F1/bams/S1.cram": "cram"})
    uri = f"s3://{BUCKET}/imports/F1/bams/S1.cram"

    same = asyncio.run(rif.verify_raw_import_file(_remote_record(uri, version_id=FakeS3.head_extra["VersionId"])))
    replaced = asyncio.run(rif.verify_raw_import_file(_remote_record(uri, version_id="older-version")))

    assert same["status"] == "verified"
    assert replaced["status"] == "mismatch" and "version" in replaced["message"]


def test_an_unversioned_s3_object_is_compared_by_etag(monkeypatch):
    # Without versioning the ETag is what changes when the object is rewritten.
    store = use_store(monkeypatch, "s3", {"imports/F1/bams/S1.cram": "cram"})
    store.head_extra = {"ETag": '"new-etag"'}
    uri = f"s3://{BUCKET}/imports/F1/bams/S1.cram"

    result = asyncio.run(rif.verify_raw_import_file(_remote_record(uri, version_id=None, etag='"old-etag"')))

    assert result["status"] == "mismatch" and "ETag" in result["message"]


def test_nothing_recorded_about_an_object_leaves_nothing_to_verify(monkeypatch):
    use_store(monkeypatch, "gs", {"imports/F1/bams/S1.cram": "cram"})

    result = asyncio.run(rif.verify_raw_import_file(_remote_record(size=None)))

    assert result["status"] == "unverifiable"


def test_an_unreachable_store_is_an_error_not_a_verdict(monkeypatch):
    store = use_store(monkeypatch, "gs", {"imports/F1/bams/S1.cram": "cram"})

    def unavailable(self, key):
        raise RuntimeError("503 Service Unavailable")

    monkeypatch.setattr(type(store.bucket(BUCKET)), "get_blob", unavailable)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(rif.verify_raw_import_file(_remote_record(generation="1")))
    assert exc.value.status_code == 502


def test_the_admin_list_does_not_call_a_file_in_an_object_store_missing(tmp_path):
    local = tmp_path / "F1.vcf.gz"
    local.write_bytes(b"vcf")

    remote_row = rif._row_to_dict({"storage_path": CRAM_URI, "created_at": None})
    local_row = rif._row_to_dict({"storage_path": str(local), "created_at": None})
    gone_row = rif._row_to_dict({"storage_path": str(tmp_path / "gone.vcf.gz"), "created_at": None})

    # Not checked when listing -- Verify asks the store -- and not downloaded through CoGA.
    assert (remote_row["in_object_store"], remote_row["exists"], remote_row["download_available"]) == (True, None, False)
    assert (local_row["in_object_store"], local_row["exists"], local_row["download_available"]) == (False, True, True)
    assert (gone_row["exists"], gone_row["download_available"]) == (False, False)


def test_downloading_a_file_in_an_object_store_is_refused_without_calling_it_gone(monkeypatch):
    async def record(session, *, file_id):
        return {"id": file_id, "storage_path": CRAM_URI, "file_name": "S1.cram"}

    monkeypatch.setattr(admin, "get_raw_import_file_record", record)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(admin.download_raw_import_file("rec-remote", session=None, user=None))
    assert exc.value.status_code == 409
    assert "object store" in exc.value.detail
