"""File-integrity (checksum) verification — REQ-DATA-004.

Verifies that `verify_raw_import_file` recomputes the stored file's SHA-256 and
correctly reports verified / mismatch / missing / unverifiable. A wrong or
corrupted source file must be detected, not silently trusted (risk H4). A file above
the size cap, or one whose hash runs out of time, is reported too large to verify
inline, never hashed past the cap.
"""

import asyncio
import hashlib
from pathlib import Path
import time

from fastapi import HTTPException
import pytest

from app.core.config import settings
from app.services import raw_import_files_pg as rif


@pytest.fixture(autouse=True)
def _files_in_the_familys_package(monkeypatch, tmp_path) -> None:
    """The local files verified here lie in ``tmp_path``, the family's package folder below an
    import root (its parent): Verify and the list read a recorded path only inside the package
    folder the family's import read, or the upload folder. Each record names that folder, as
    the admin page reads it with the row."""
    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path.parent)])


def _sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_verify_reports_verified_when_checksum_matches(tmp_path):
    f = tmp_path / "sample.vcf.gz"
    f.write_bytes(b"clinical genomic payload")
    expected = _sha256_of(f)

    result = asyncio.run(
        rif.verify_raw_import_file(
            {"id": "rec-1", "package_folder": str(tmp_path), "storage_path": str(f), "sha256": expected}
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
            {"id": "rec-2", "package_folder": str(tmp_path), "storage_path": str(f), "sha256": "0" * 64}
        )
    )

    assert result["status"] == "mismatch"
    assert result["computed_sha256"] == _sha256_of(f)
    assert result["computed_sha256"] != result["expected_sha256"]


def test_verify_reports_missing_when_file_is_absent(tmp_path):
    result = asyncio.run(
        rif.verify_raw_import_file(
            {"id": "rec-3", "package_folder": str(tmp_path), "storage_path": str(tmp_path / "gone.vcf.gz"), "sha256": "abc"}
        )
    )

    assert result["status"] == "missing"
    assert result["computed_sha256"] is None


def test_verify_reports_unverifiable_without_a_recorded_checksum(tmp_path):
    f = tmp_path / "sample.vcf.gz"
    f.write_bytes(b"payload")

    result = asyncio.run(
        rif.verify_raw_import_file(
            {"id": "rec-4", "package_folder": str(tmp_path), "storage_path": str(f), "sha256": None}
        )
    )

    assert result["status"] == "unverifiable"
    assert result["computed_sha256"] == _sha256_of(f)


# --- Verify is bounded --------------------------------------------------------------
#
# Inline verification must not tie up a worker, and the synchronous Verify response, for
# minutes on a multi-GB CRAM: above the size cap the file is not hashed, and a hash that runs
# out of time is no verdict either. Both are reported as too large to verify inline.


@pytest.mark.parametrize(
    ("cap", "status", "hashed"),
    [(4, "too_large", False), (1024, "verified", True)],
    ids=["over-the-cap", "under-the-cap"],
)
def test_verify_hashes_a_local_file_only_under_the_size_cap(monkeypatch, tmp_path, cap, status, hashed):
    monkeypatch.setattr(rif, "_VERIFY_MAX_BYTES", cap)
    f = tmp_path / "file.bin"
    f.write_bytes(b"0123456789")  # 10 bytes
    expected = _sha256_of(f)

    result = asyncio.run(rif.verify_raw_import_file({"id": "rec-5", "package_folder": str(tmp_path), "storage_path": str(f), "sha256": expected}))

    assert result["status"] == status
    # Over the cap the file was never hashed.
    assert result["computed_sha256"] == (expected if hashed else None)


def test_a_hash_that_runs_out_of_time_is_not_a_verdict(monkeypatch, tmp_path):
    f = tmp_path / "slow.vcf.gz"
    f.write_bytes(b"payload on slow storage")

    def slow(path):
        time.sleep(0.2)
        return _sha256_of(path), 23

    monkeypatch.setattr(rif, "_hash_and_size", slow)
    monkeypatch.setattr(rif, "_VERIFY_TIMEOUT_SECONDS", 0.02)

    result = asyncio.run(rif.verify_raw_import_file({"id": "rec-6", "package_folder": str(tmp_path), "storage_path": str(f), "sha256": _sha256_of(f)}))

    assert (result["status"], result["computed_sha256"]) == ("too_large", None)
    assert "timed out" in result["message"]


def test_a_file_whose_size_cannot_be_read_is_still_hashed(monkeypatch, tmp_path):
    # The size is read only to skip a file above the cap; a file whose size the store will
    # not tell is hashed under the timeout, and still gets a verdict.
    f = tmp_path / "sample.vcf.gz"
    f.write_bytes(b"payload")
    stat_calls: list[str] = []

    class _SizeUnreadable(type(Path())):
        def stat(self, *args, **kwargs):
            stat_calls.append(str(self))
            if len(stat_calls) == 1:  # the size read; the hash's own look-ups succeed
                raise PermissionError("stat refused")
            return super().stat(*args, **kwargs)

    monkeypatch.setattr(rif, "Path", _SizeUnreadable)

    result = asyncio.run(rif.verify_raw_import_file({"id": "rec-7", "package_folder": str(tmp_path), "storage_path": str(f), "sha256": _sha256_of(f)}))

    assert stat_calls[0] == str(f.resolve())
    assert (result["status"], result["computed_sha256"]) == ("verified", _sha256_of(f))


def test_the_hash_of_a_file_that_is_not_there_is_none(tmp_path):
    # Read by its real path after the check that it is a file: one gone (or replaced by a
    # folder) in between has no checksum and no size, never an error.
    folder = tmp_path / "folder"
    folder.mkdir()
    f = tmp_path / "sample.vcf.gz"
    f.write_bytes(b"payload")

    assert rif._hash_and_size(tmp_path / "gone.vcf.gz") == (None, None)
    assert rif._hash_and_size(folder) == (None, None)
    assert rif._hash_and_size(f) == (_sha256_of(f), len(b"payload"))


def test_a_recorded_path_holding_a_nul_is_read_nowhere(tmp_path):
    # Every look-up of such a path raises ValueError: it names no file CoGA reads, and
    # Verify refuses it (403) as it refuses a path outside the folders, never a 500.
    recorded = str(tmp_path / "sample\x00.vcf.gz")

    assert rif.readable_local_path(recorded) is None
    assert rif.readable_local_path(recorded, folders=[str(tmp_path)]) is None
    with pytest.raises(HTTPException) as exc:
        asyncio.run(rif.verify_raw_import_file({"id": "rec-8", "package_folder": str(tmp_path), "storage_path": recorded, "sha256": "abc"}))
    assert (exc.value.status_code, exc.value.detail) == (403, rif.OUTSIDE_FOLDERS_DETAIL)


# --- The list ------------------------------------------------------------------------


def test_the_admin_list_says_whether_each_file_is_there(tmp_path):
    local = tmp_path / "F1.vcf.gz"
    local.write_bytes(b"vcf")

    package = {"package_folder": str(tmp_path)}
    local_row = rif._row_to_dict({"storage_path": str(local), "created_at": None, **package})
    gone_row = rif._row_to_dict({"storage_path": str(tmp_path / "gone.vcf.gz"), "created_at": None, **package})

    assert (local_row["exists"], local_row["download_available"], local_row["outside_read_folders"]) == (True, True, False)
    assert (gone_row["exists"], gone_row["download_available"], gone_row["outside_read_folders"]) == (False, False, False)
