"""Raw-file download, Verify, the list and the purges keep to CoGA's own folders (SEC-29).

Every importer records the source file it read in ``raw_import_files``. The admin page lists
the rows, downloads a file and verifies its SHA-256 at the recorded ``storage_path``, and a
family or sample delete unlinks the family's ``managed`` copies (web uploads, written under
``data/raw_imports``). The path is read back from the database as data, and each of these
used it as found: an edited row let an admin download or hash any file the backend can read,
and turned a family or sample delete into deleting any file the backend may write.

Now a recorded local path is read only when its real path, symlinks resolved, lies inside a
local FAMILY_IMPORT_ROOTS folder, the staging folder of a package downloaded from a bucket or
the upload folder; the file is then read by that real path. Any other path is refused before
anything is read: the download and Verify answer 403 and log it, and the list shows the file
unavailable. The store is asked about a recorded object only below a remote
FAMILY_IMPORT_ROOTS entry. A purge unlinks a managed copy only inside the upload folder, skips
any other row with a log line, and never unlinks its path. A package folder need not be named
after its family, nor an upload folder after the family's present ID.

The paths and IDs are synthetic, under ``tmp_path``: ``imports`` is the import root,
``staging`` the staging folder, ``data`` the data directory and ``elsewhere`` a folder the
backend may read and write that is none of them.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import logging
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException
import httpx
import pytest

from backend.app.core.config import settings
from backend.app.core.postgres import get_postgres_session
from backend.app.main import app
from backend.app.middleware import request_logging
from backend.app.routers import admin
from backend.app.services import admin_service, family_package_source
from backend.app.services import raw_import_files_pg as rif
from backend.app.services.access_control import CurrentUser

OUTSIDE = (
    "The file's recorded storage path lies outside the folders CoGA reads source files from "
    "(FAMILY_IMPORT_ROOTS and its upload folder), so the file is not read."
)
OUTSIDE_THE_IMPORT_ROOTS = (
    "The file's recorded storage path names no object below FAMILY_IMPORT_ROOTS, so the "
    "object store is not asked about it."
)


def _place(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


@pytest.fixture()
def where(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """The folders, with a file in each: a package file in a package folder that is not named
    after its family, an upload in the family's upload folder, a staged copy, and a secret
    elsewhere; a link in the package to the secret, and one to the package file."""
    base = Path(os.path.realpath(tmp_path))
    imports, staging, data, elsewhere = (base / name for name in ("imports", "staging", "data", "elsewhere"))
    staging.mkdir()
    monkeypatch.setattr(settings, "family_import_roots", [str(imports)])
    monkeypatch.setattr(family_package_source, "_staging_root", lambda: staging)
    monkeypatch.setattr(rif, "DATA_DIR", data)
    found = SimpleNamespace(
        package=_place(imports / "run-2026-10" / "F1.vcf.gz", "package file"),
        upload=_place(data / "raw_imports" / "F1" / "0a1b2c__upload.vcf", "upload"),
        staged=_place(staging / "pkg-x" / "qc" / "S1.txt", "staged copy"),
        secret=_place(elsewhere / "secret.txt", "secret"),
        imports=imports,
        data=data,
        elsewhere=elsewhere,
        base=base,
    )
    (imports / "run-2026-10" / "linked-out.txt").symlink_to(found.secret)
    (imports / "run-2026-10" / "linked-in.vcf.gz").symlink_to(found.package)
    return found


# Recorded paths outside the folders, as an edited row could hold them.
def _outside_paths(where: SimpleNamespace) -> dict[str, str]:
    return {
        "an absolute path elsewhere": str(where.secret),
        "`..` that climbs out of the import root": str(where.imports / "run-2026-10" / ".." / ".." / "elsewhere" / "secret.txt"),
        "a symlink in the package that leads out": str(where.imports / "run-2026-10" / "linked-out.txt"),
        "a folder beside the import root sharing its name": str(_place(where.base / "imports-old" / "F1.vcf.gz", "old")),
        "a folder beside the upload folder sharing its name": str(_place(where.data / "raw_imports-old" / "x.vcf", "old")),
        "the data directory outside the upload folder": str(_place(where.data / "F1" / "F1.vcf.gz", "flat")),
        "a relative path": "elsewhere/secret.txt",
    }


OUTSIDE_CASES = [
    "an absolute path elsewhere",
    "`..` that climbs out of the import root",
    "a symlink in the package that leads out",
    "a folder beside the import root sharing its name",
    "a folder beside the upload folder sharing its name",
    "the data directory outside the upload folder",
    "a relative path",
]


def _record(storage_path: str, record_id: str = "rec-1", sha256: str | None = None) -> dict[str, Any]:
    return {"id": record_id, "storage_path": storage_path, "file_name": "F1.vcf.gz", "sha256": sha256}


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


# --------------------------------------------------------------------------- #
# Download, through the app
# --------------------------------------------------------------------------- #


@pytest.fixture()
def records(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, Any]]:
    """The app with a signed-in admin; the raw-file records it finds, by id, are the returned
    mapping's."""
    found: dict[str, dict[str, Any]] = {}

    async def record_by_id(session: Any, *, file_id: str) -> dict[str, Any]:
        if file_id not in found:
            raise HTTPException(status_code=404, detail="File not found")
        return found[file_id]

    async def no_session():
        yield None

    async def an_admin() -> CurrentUser:
        return CurrentUser(
            id="user-admin",
            username="admin@example.com",
            email="admin@example.com",
            role="admin",
            created_at=datetime.now(timezone.utc),
        )

    async def keep_audit_row(payload: Any) -> None:
        return None

    # The download reads the record through the router's name, Verify through the service's.
    monkeypatch.setattr(admin, "get_raw_import_file_record", record_by_id)
    monkeypatch.setattr(admin_service, "get_raw_import_file_record", record_by_id)
    monkeypatch.setattr(request_logging, "write_audit_log_event", keep_audit_row)
    monkeypatch.setitem(app.dependency_overrides, get_postgres_session, no_session)
    monkeypatch.setitem(app.dependency_overrides, admin.get_current_admin_user, an_admin)
    return found


async def _download(records: dict[str, dict[str, Any]], storage_path: str) -> tuple[int, Any]:
    records["rec-1"] = _record(storage_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://coga.test") as client:
        response = await client.get("/api/admin/data/files/rec-1/download")
    if response.status_code == 200:
        return 200, response.text
    return response.status_code, response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("which", "content"),
    [
        ("package", "package file"),
        ("upload", "upload"),
        ("staged", "staged copy"),
        ("link to the package file", "package file"),
    ],
)
async def test_a_file_in_the_folders_is_downloaded(
    where: SimpleNamespace, records: dict[str, dict[str, Any]], which: str, content: str
) -> None:
    path = {
        "package": where.package,
        "upload": where.upload,
        "staged": where.staged,
        "link to the package file": where.imports / "run-2026-10" / "linked-in.vcf.gz",
    }[which]

    assert await _download(records, str(path)) == (200, content)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", OUTSIDE_CASES)
async def test_a_recorded_path_outside_the_folders_is_not_downloaded(
    where: SimpleNamespace, records: dict[str, dict[str, Any]], case: str, caplog: pytest.LogCaptureFixture
) -> None:
    storage_path = _outside_paths(where)[case]

    with caplog.at_level(logging.WARNING, logger=rif.__name__):
        answer = await _download(records, storage_path)

    # Before: 200 with the file's bytes (the secret, the old package, ...), or 410 for the
    # relative path, which was looked up from the backend's working directory.
    assert answer == (403, OUTSIDE)
    assert [record.getMessage() for record in caplog.records if record.name == rif.__name__] == [
        f"Raw file rec-1 was not read: its recorded storage path {storage_path} is outside "
        "FAMILY_IMPORT_ROOTS and the upload folder"
    ]


@pytest.mark.asyncio
async def test_a_missing_file_and_a_file_in_an_object_store_keep_their_answers(
    where: SimpleNamespace, records: dict[str, dict[str, Any]]
) -> None:
    assert await _download(records, str(where.imports / "run-2026-10" / "gone.vcf.gz")) == (
        410,
        "The source file is no longer available at its storage path.",
    )
    assert await _download(records, "") == (410, "The source file is no longer available at its storage path.")
    assert await _download(records, "gs://phi/imports/F1/bams/S1.cram") == (
        409,
        "The file is kept in an object store at its gs:// or s3:// URI; it is not downloaded through CoGA.",
    )


@pytest.mark.asyncio
async def test_with_no_import_roots_only_the_upload_and_staging_folders_are_read(
    where: SimpleNamespace, records: dict[str, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    # FAMILY_IMPORT_ROOTS empty: the import takes a package folder anywhere (development
    # mode). A recorded path is still bounded: by the upload and staging folders alone.
    monkeypatch.setattr(settings, "family_import_roots", [])

    assert await _download(records, str(where.package)) == (403, OUTSIDE)
    assert await _download(records, str(where.secret)) == (403, OUTSIDE)
    assert await _download(records, str(where.upload)) == (200, "upload")
    assert await _download(records, str(where.staged)) == (200, "staged copy")


# --------------------------------------------------------------------------- #
# Verify
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize("case", OUTSIDE_CASES)
async def test_verify_refuses_a_recorded_path_outside_the_folders_without_reading_it(
    where: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    def no_read(path: Path) -> Any:
        raise AssertionError(f"{path} was read")

    monkeypatch.setattr(rif, "_hash_and_size", no_read)

    with pytest.raises(HTTPException) as refused:
        # Before: the file was hashed, and its checksum reported.
        await rif.verify_raw_import_file(_record(_outside_paths(where)[case], sha256=_sha256("secret")))

    assert (refused.value.status_code, refused.value.detail) == (403, OUTSIDE)


@pytest.mark.asyncio
async def test_verify_reads_a_file_in_the_folders_by_its_real_path(where: SimpleNamespace) -> None:
    for path, content in (
        (where.package, "package file"),
        (where.upload, "upload"),
        (where.staged, "staged copy"),
        (where.imports / "run-2026-10" / "linked-in.vcf.gz", "package file"),
    ):
        result = await rif.verify_raw_import_file(_record(str(path), sha256=_sha256(content)))
        assert (result["status"], result["computed_sha256"]) == ("verified", _sha256(content)), path

    missing = await rif.verify_raw_import_file(_record(str(where.imports / "gone.vcf.gz"), sha256="ab" * 32))
    assert missing["status"] == "missing"


@pytest.mark.asyncio
async def test_verify_through_the_app_answers_403_for_a_path_outside_the_folders(
    where: SimpleNamespace, records: dict[str, dict[str, Any]]
) -> None:
    records["rec-1"] = _record(str(where.secret), sha256=_sha256("secret"))
    records["rec-2"] = _record(str(where.package), record_id="rec-2", sha256=_sha256("package file"))

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://coga.test") as client:
        refused = await client.post("/api/admin/data/files/rec-1/verify")
        verified = await client.post("/api/admin/data/files/rec-2/verify")

    # Before: 200, "verified", with the secret's checksum.
    assert (refused.status_code, refused.json()) == (403, {"detail": OUTSIDE})
    assert (verified.status_code, verified.json()["status"]) == (200, "verified")


# Objects named by a recorded URI, with the import root gs://phi/imports.
REMOTE_OUTSIDE = {
    "another prefix of the bucket": "gs://phi/reports/F1/report.pdf",
    "a prefix beside the root sharing its name": "gs://phi/imports-old/F1/S1.cram",
    "another bucket": "gs://other/imports/F1/S1.cram",
    "another store": "s3://phi/imports/F1/S1.cram",
    "`..` that climbs out of the root": "gs://phi/imports/../reports/F1/report.pdf",
    "an empty segment": "gs://phi/imports/F1//S1.cram",
    "a query": "gs://phi/imports/F1/S1.cram?generation=1",
}


@pytest.mark.asyncio
async def test_verify_asks_the_store_only_about_an_object_below_the_import_roots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[str] = []

    def identity(uri: str) -> dict[str, Any]:
        asked.append(uri)
        return {"store": "gcs", "uri": uri, "size": 4, "generation": "7"}

    monkeypatch.setattr(settings, "family_import_roots", ["gs://phi/imports"])
    monkeypatch.setattr(rif, "remote_object_identity", identity)
    inside = "gs://phi/imports/F1/bams/S1.cram"
    record = {**_record(inside), "file_size": 4, "metadata": {"store_object": {"generation": "7"}}}

    assert (await rif.verify_raw_import_file(record))["status"] == "verified"
    refusals = {}
    for case, uri in REMOTE_OUTSIDE.items():
        with pytest.raises(HTTPException) as refused:
            await rif.verify_raw_import_file({**record, "storage_path": uri})
        refusals[case] = (refused.value.status_code, refused.value.detail)

    # Before: the store was asked about each, and its size and generation compared.
    assert refusals == {case: (403, OUTSIDE_THE_IMPORT_ROOTS) for case in REMOTE_OUTSIDE}
    assert asked == [inside]


# --------------------------------------------------------------------------- #
# The list
# --------------------------------------------------------------------------- #


class _ListSession:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    async def execute(self, statement: Any, params: Any) -> Any:
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: self.rows))


@pytest.mark.asyncio
async def test_the_list_shows_a_file_outside_the_folders_unavailable(where: SimpleNamespace) -> None:
    outside = _outside_paths(where)
    rows = [
        {"storage_path": str(where.package), "created_at": None},
        {"storage_path": str(where.upload), "created_at": None},
        {"storage_path": str(where.imports / "gone.vcf.gz"), "created_at": None},
        *({"storage_path": outside[case], "created_at": None} for case in OUTSIDE_CASES),
    ]

    listed = await rif.list_raw_import_files(_ListSession(rows), "family-uuid")  # type: ignore[arg-type]

    availability = [(row["exists"], row["download_available"], row["in_object_store"]) for row in listed]
    # Before: every file outside the folders that is there was listed present and
    # downloadable (all but the relative path).
    assert availability == [(True, True, False), (True, True, False)] + [(False, False, False)] * (1 + len(OUTSIDE_CASES))
    # And the row says why: a file that is gone is not a location CoGA does not read (SEC-27).
    assert [row["outside_read_folders"] for row in listed] == [False, False, False] + [True] * len(OUTSIDE_CASES)


@pytest.mark.asyncio
async def test_the_list_marks_an_object_below_no_import_root(
    where: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "family_import_roots", [str(where.imports), "gs://phi/imports"])
    uris = ["gs://phi/imports/F1/bams/S1.cram", *REMOTE_OUTSIDE.values()]

    listed = await rif.list_raw_import_files(  # type: ignore[arg-type]
        _ListSession([{"storage_path": uri, "created_at": None} for uri in uris]), "family-uuid"
    )

    # Before: each was listed as kept in the object store, though Verify refuses it (SEC-27).
    assert [row["in_object_store"] for row in listed] == [True] * len(uris)
    assert [row["outside_read_folders"] for row in listed] == [False] + [True] * len(REMOTE_OUTSIDE)


# --------------------------------------------------------------------------- #
# The purges: the managed rows read in a delete's transaction, their copies unlinked once
# it has committed (test_deletions_unlink_files_after_commit.py, SAFE-35)
# --------------------------------------------------------------------------- #


class _PurgeSession:
    """Answers the read of the managed rows with ``(storage_path, record id)`` rows."""

    def __init__(self, rows: list[tuple[str, str]]) -> None:
        self.rows = rows
        self.params: list[Any] = []

    async def execute(self, statement: Any, params: Any) -> Any:
        self.params.append(params)
        return SimpleNamespace(fetchall=lambda: self.rows)


@pytest.mark.asyncio
async def test_a_family_delete_unlinks_only_managed_copies_in_the_upload_folder(
    where: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    uploads = where.data / "raw_imports"
    # The family's upload under an ID it no longer has: its folder is not named after it.
    renamed = _place(uploads / "F1-OLD" / "3d4e5f__upload.vcf", "upload under the former ID")
    kept_target = _place(uploads / "F1" / "6a7b8c__other.vcf", "another upload")
    (uploads / "F1" / "linked-in.vcf").symlink_to(kept_target)
    victims = {name: _place(where.elsewhere / f"{name}.txt", name) for name in ("victim", "climbed", "linked")}
    (uploads / "F1" / "linked-out.vcf").symlink_to(victims["linked"])
    # An upload folder that is a link to a folder elsewhere.
    victims["folder"] = _place(where.elsewhere / "folder-target" / "folder.txt", "folder")
    (uploads / "F9").symlink_to(where.elsewhere / "folder-target")

    rows = [
        (str(where.upload), "rec-upload"),
        (str(renamed), "rec-renamed"),
        (str(uploads / "F1" / "linked-in.vcf"), "rec-link-in"),
        # As an edited managed row could hold them:
        (str(where.package), "rec-package"),
        (str(victims["victim"]), "rec-elsewhere"),
        (str(uploads / "F1" / ".." / ".." / ".." / "elsewhere" / "climbed.txt"), "rec-climbed"),
        (str(uploads / "F1" / "linked-out.vcf"), "rec-link-out"),
        (str(uploads / "F9" / "folder.txt"), "rec-folder-link"),
    ]
    session = _PurgeSession(rows)

    with caplog.at_level(logging.WARNING, logger=rif.__name__):
        listed = await rif.family_managed_files(session, "family-uuid")  # type: ignore[arg-type]
        removed = await rif.unlink_managed_files(listed)

    assert session.params == [{"family_uuid": "family-uuid"}]
    assert listed == rows
    assert removed == 3
    assert not where.upload.exists() and not renamed.exists()
    # The link inside the folder is unlinked, never its target.
    assert not (uploads / "F1" / "linked-in.vcf").is_symlink() and kept_target.read_text() == "another upload"
    # Before: the package file, the file elsewhere, the one reached by `..` and the one
    # reached through the linked folder were deleted.
    assert where.package.read_text() == "package file"
    assert {name: path.read_text() for name, path in victims.items()} == {name: name for name in victims}
    assert (uploads / "F1" / "linked-out.vcf").is_symlink()
    skipped = ("rec-package", "rec-elsewhere", "rec-climbed", "rec-link-out", "rec-folder-link")
    paths = dict((record_id, path) for path, record_id in rows)
    assert [record.getMessage() for record in caplog.records if record.name == rif.__name__] == [
        f"The file of raw-file record {record_id} was not deleted: its recorded storage path "
        f"{paths[record_id]} is outside the upload folder"
        for record_id in skipped
    ]


@pytest.mark.asyncio
async def test_a_sample_delete_unlinks_only_managed_copies_in_the_upload_folder(where: SimpleNamespace) -> None:
    session = _PurgeSession([(str(where.upload), "rec-upload"), (str(where.secret), "rec-secret")])

    listed = await rif.sample_managed_files(session, "sample-uuid")  # type: ignore[arg-type]
    removed = await rif.unlink_managed_files(listed)

    assert session.params == [{"sample_uuid": "sample-uuid"}]
    assert removed == 1 and not where.upload.exists()
    # Before: deleted.
    assert where.secret.read_text() == "secret"
