"""Raw-file download, Verify, the list and the purges keep to the family's own folders.

Every importer records the source file it read in ``raw_import_files``. The admin page lists
the rows, downloads a file and verifies its SHA-256 at the recorded ``storage_path``, and a
family or sample delete unlinks the family's ``managed`` copies (web uploads, written under
``data/raw_imports``). The path is read back from the database as data; used as found, an
edited row would let an admin download or hash any file the backend can read, and turn a
family or sample delete into deleting any file the backend may write.

A recorded local path is read only when its real path, symlinks resolved, lies inside the
package folder the family's own import read (``families.metadata.package_import.folder_path``,
below a local import root, as the IGV endpoints serve a sample's files) or the upload folder,
which holds every managed copy (an upload, a manifest kept for an import); the file is then
read by that real path. Any other path, another family's package in the import area among
them, is refused before anything is read: the download and Verify answer 403 and log it, and the
list shows the file unavailable. The store is asked about a recorded object only in the family's
bucket package folder. A purge unlinks a managed copy only inside the upload folder, skips any
other row with a log line, and never unlinks its path. A package folder need not be named after
its family, nor an upload folder after the family's present ID.

The paths and IDs are synthetic, under ``tmp_path``: ``imports`` is the import root, holding
F1's package ``run-2026-10`` and F2's ``run-other``; ``staging`` the staging folder, ``data`` the
data directory and ``elsewhere`` a folder the backend may read and write that is none of them.
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
    "The file's recorded storage path lies outside its family's package folder (below "
    "FAMILY_IMPORT_ROOTS) and CoGA's upload folder, so the file is not read."
)
OUTSIDE_THE_PACKAGE = (
    "The file's recorded storage path names no object in its family's package folder below "
    "FAMILY_IMPORT_ROOTS, so the object store is not asked about it."
)


def _place(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


@pytest.fixture()
def where(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """The folders, with a file in each: F1's package file in a package folder that is not
    named after its family, F2's package file beside it, a file at the import root itself, an
    upload in the family's upload folder, a staged copy, and a secret elsewhere; a link in the
    package to the secret, one to the package file and one to F2's package file. ``folder`` is
    F1's package folder, as its import recorded it."""
    base = Path(os.path.realpath(tmp_path))
    imports, staging, data, elsewhere = (base / name for name in ("imports", "staging", "data", "elsewhere"))
    staging.mkdir()
    monkeypatch.setattr(settings, "family_import_roots", [str(imports)])
    monkeypatch.setattr(family_package_source, "_staging_root", lambda: staging)
    monkeypatch.setattr(rif, "DATA_DIR", data)
    found = SimpleNamespace(
        package=_place(imports / "run-2026-10" / "F1.vcf.gz", "package file"),
        other_package=_place(imports / "run-other" / "F2.vcf.gz", "another family's package"),
        at_root=_place(imports / "loose.vcf.gz", "a file at the import root"),
        upload=_place(data / "raw_imports" / "F1" / "0a1b2c__upload.vcf", "upload"),
        staged=_place(staging / "pkg-x" / "qc" / "S1.txt", "staged copy"),
        secret=_place(elsewhere / "secret.txt", "secret"),
        folder=str(imports / "run-2026-10"),
        imports=imports,
        data=data,
        elsewhere=elsewhere,
        base=base,
    )
    (imports / "run-2026-10" / "linked-out.txt").symlink_to(found.secret)
    (imports / "run-2026-10" / "linked-in.vcf.gz").symlink_to(found.package)
    (imports / "run-2026-10" / "linked-across.vcf.gz").symlink_to(found.other_package)
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


# Recorded paths in the import area outside the family's own package, as an edited row could
# hold them: below the import root, or, the staged copy, in the staging folder.
def _other_package_paths(where: SimpleNamespace) -> dict[str, str]:
    return {
        "another family's package": str(where.other_package),
        "`..` from the package into another's": str(where.imports / "run-2026-10" / ".." / "run-other" / "F2.vcf.gz"),
        "a symlink in the package into another's": str(where.imports / "run-2026-10" / "linked-across.vcf.gz"),
        "a file at the import root": str(where.at_root),
        "a staged copy": str(where.staged),
    }


OTHER_PACKAGE_CASES = [
    "another family's package",
    "`..` from the package into another's",
    "a symlink in the package into another's",
    "a file at the import root",
    "a staged copy",
]


def _record(
    storage_path: str, record_id: str = "rec-1", sha256: str | None = None, *, package_folder: Any
) -> dict[str, Any]:
    """A raw-file record as the admin page reads it, with its family's package folder."""
    return {
        "id": record_id,
        "storage_path": storage_path,
        "file_name": "F1.vcf.gz",
        "sha256": sha256,
        "package_folder": package_folder,
    }


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


async def _download(records: dict[str, dict[str, Any]], storage_path: str, package_folder: Any) -> tuple[int, Any]:
    records["rec-1"] = _record(storage_path, package_folder=package_folder)
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
        ("link to the package file", "package file"),
    ],
)
async def test_a_file_in_the_folders_is_downloaded(
    where: SimpleNamespace, records: dict[str, dict[str, Any]], which: str, content: str
) -> None:
    path = {
        "package": where.package,
        "upload": where.upload,
        "link to the package file": where.imports / "run-2026-10" / "linked-in.vcf.gz",
    }[which]

    assert await _download(records, str(path), where.folder) == (200, content)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", OTHER_PACKAGE_CASES)
async def test_a_file_in_the_import_area_outside_the_familys_package_is_not_downloaded(
    where: SimpleNamespace, records: dict[str, dict[str, Any]], case: str
) -> None:
    # refused, another family's package file among them.
    assert await _download(records, _other_package_paths(where)[case], where.folder) == (403, OUTSIDE)


@pytest.mark.asyncio
async def test_a_family_whose_package_folder_is_unusable_has_only_its_uploads_read(
    where: SimpleNamespace, records: dict[str, dict[str, Any]]
) -> None:
    # The family's package folder comes back from the database as data, and is used only when
    # it lies below a local import root, as the IGV endpoints use it.
    for package_folder in (
        None,  # never imported from a package (a family made in the Family Builder)
        str(where.imports),  # the import root itself, with every family's package below it
        str(where.elsewhere),  # outside the import roots
        "gs://phi/imports/F1",  # a bucket folder: its files are named by their URIs
        "run-2026-10",  # a relative path
        42,  # not text
    ):
        assert await _download(records, str(where.package), package_folder) == (403, OUTSIDE), package_folder
        assert await _download(records, str(where.upload), package_folder) == (200, "upload"), package_folder


@pytest.mark.asyncio
@pytest.mark.parametrize("case", OUTSIDE_CASES)
async def test_a_recorded_path_outside_the_folders_is_not_downloaded(
    where: SimpleNamespace, records: dict[str, dict[str, Any]], case: str, caplog: pytest.LogCaptureFixture
) -> None:
    storage_path = _outside_paths(where)[case]

    with caplog.at_level(logging.WARNING, logger=rif.__name__):
        answer = await _download(records, storage_path, where.folder)

    # Refused, the relative path too: it is not looked up from the backend's working directory.
    assert answer == (403, OUTSIDE)
    # The line names the record and the kind of place, never the path: it can name another
    # family's folder (the owner's decision of 2026-11-10).
    kind = "a relative local path" if case == "a relative path" else "an absolute local path"
    messages = [record.getMessage() for record in caplog.records if record.name == rif.__name__]
    assert messages == [
        f"Raw file rec-1 was not read: its recorded storage path ({kind}) is outside its "
        "family's package folder and the upload folder"
    ]
    assert storage_path not in messages[0]



@pytest.mark.parametrize(
    ("storage_path", "kind"),
    [
        ("/data/families/OTHER_FAMILY/snv/OTHER_FAMILY.vcf.gz", "an absolute local path"),
        ("OTHER_FAMILY/snv.vcf.gz", "a relative local path"),
        ("gs://bucket/packages/OTHER_FAMILY/snv.vcf.gz", "an object-store URI"),
        ("", "no path"),
        (None, "no path"),
    ],
)
def test_a_refused_path_is_logged_by_its_kind_never_as_written(storage_path: object, kind: str) -> None:
    # A refused path can name another family's folder, and so its ID: the log line gives its
    # kind; the record, by its ID, keeps the path (the owner's decision of 2026-11-10).
    assert rif._storage_path_kind(storage_path) == kind

@pytest.mark.asyncio
async def test_a_missing_file_and_a_file_in_an_object_store_keep_their_answers(
    where: SimpleNamespace, records: dict[str, dict[str, Any]]
) -> None:
    assert await _download(records, str(where.imports / "run-2026-10" / "gone.vcf.gz"), where.folder) == (
        410,
        "The source file is no longer available at its storage path.",
    )
    assert await _download(records, "", where.folder) == (410, "The source file is no longer available at its storage path.")
    assert await _download(records, "gs://phi/imports/F1/bams/S1.cram", "gs://phi/imports/F1") == (
        409,
        "The file is kept in an object store at its gs:// or s3:// URI; it is not downloaded through CoGA.",
    )


@pytest.mark.asyncio
async def test_with_no_import_roots_only_the_upload_folder_is_read(
    where: SimpleNamespace, records: dict[str, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    # FAMILY_IMPORT_ROOTS empty: the import takes a package folder anywhere (development
    # mode). A recorded path is still bounded: by the upload folder alone, as the IGV
    # endpoints serve nothing from a package folder no root lists. The staging folder is not
    # read either.
    monkeypatch.setattr(settings, "family_import_roots", [])

    assert await _download(records, str(where.package), where.folder) == (403, OUTSIDE)
    assert await _download(records, str(where.secret), where.folder) == (403, OUTSIDE)
    assert await _download(records, str(where.upload), where.folder) == (200, "upload")
    assert await _download(records, str(where.staged), where.folder) == (403, OUTSIDE)


# --------------------------------------------------------------------------- #
# Verify
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize("case", OUTSIDE_CASES + OTHER_PACKAGE_CASES)
async def test_verify_refuses_a_recorded_path_outside_the_folders_without_reading_it(
    where: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    def no_read(path: Path) -> Any:
        raise AssertionError(f"{path} was read")

    monkeypatch.setattr(rif, "_hash_and_size", no_read)
    storage_path = {**_outside_paths(where), **_other_package_paths(where)}[case]

    with pytest.raises(HTTPException) as refused:
        # Refused before the file is hashed (a path outside the import area,
        # one in it outside the family's package).
        await rif.verify_raw_import_file(_record(storage_path, sha256=_sha256("secret"), package_folder=where.folder))

    assert (refused.value.status_code, refused.value.detail) == (403, OUTSIDE)


@pytest.mark.asyncio
async def test_verify_reads_a_file_in_the_folders_by_its_real_path(where: SimpleNamespace) -> None:
    for path, content in (
        (where.package, "package file"),
        (where.upload, "upload"),
        (where.imports / "run-2026-10" / "linked-in.vcf.gz", "package file"),
    ):
        result = await rif.verify_raw_import_file(_record(str(path), sha256=_sha256(content), package_folder=where.folder))
        assert (result["status"], result["computed_sha256"]) == ("verified", _sha256(content)), path

    missing = await rif.verify_raw_import_file(
        _record(str(where.imports / "run-2026-10" / "gone.vcf.gz"), sha256="ab" * 32, package_folder=where.folder)
    )
    assert missing["status"] == "missing"


@pytest.mark.asyncio
async def test_verify_through_the_app_answers_403_for_a_path_outside_the_folders(
    where: SimpleNamespace, records: dict[str, dict[str, Any]]
) -> None:
    records["rec-1"] = _record(str(where.secret), sha256=_sha256("secret"), package_folder=where.folder)
    records["rec-2"] = _record(
        str(where.package), record_id="rec-2", sha256=_sha256("package file"), package_folder=where.folder
    )
    records["rec-3"] = _record(
        str(where.other_package),
        record_id="rec-3",
        sha256=_sha256("another family's package"),
        package_folder=where.folder,
    )

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://coga.test") as client:
        refused = await client.post("/api/admin/data/files/rec-1/verify")
        verified = await client.post("/api/admin/data/files/rec-2/verify")
        across = await client.post("/api/admin/data/files/rec-3/verify")

    # The secret and the other package's file are refused; the family's own is verified.
    assert (refused.status_code, refused.json()) == (403, {"detail": OUTSIDE})
    assert (verified.status_code, verified.json()["status"]) == (200, "verified")
    assert (across.status_code, across.json()) == (403, {"detail": OUTSIDE})


# Objects named by a recorded URI, with the import root gs://phi/imports and the family's
# package folder gs://phi/imports/F1.
REMOTE_OUTSIDE = {
    # below the import root, outside the family's package. The store is not asked.
    "another family's package": "gs://phi/imports/F2/bams/S2.cram",
    "a folder beside the package sharing its name": "gs://phi/imports/F1-old/bams/S1.cram",
    "`..` from the package into another's": "gs://phi/imports/F1/../F2/bams/S2.cram",
    # outside the import root.
    "another prefix of the bucket": "gs://phi/reports/F1/report.pdf",
    "a prefix beside the root sharing its name": "gs://phi/imports-old/F1/S1.cram",
    "another bucket": "gs://other/imports/F1/S1.cram",
    "another store": "s3://phi/imports/F1/S1.cram",
    "`..` that climbs out of the root": "gs://phi/imports/../reports/F1/report.pdf",
    "an empty segment": "gs://phi/imports/F1//S1.cram",
    "a query": "gs://phi/imports/F1/S1.cram?generation=1",
}


@pytest.mark.asyncio
async def test_verify_asks_the_store_only_about_an_object_in_the_familys_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[str] = []

    def identity(uri: str) -> dict[str, Any]:
        asked.append(uri)
        return {"store": "gcs", "uri": uri, "size": 4, "generation": "7"}

    monkeypatch.setattr(settings, "family_import_roots", ["gs://phi/imports"])
    monkeypatch.setattr(rif, "remote_object_identity", identity)
    inside = "gs://phi/imports/F1/bams/S1.cram"
    record = {
        **_record(inside, package_folder="gs://phi/imports/F1"),
        "file_size": 4,
        "metadata": {"store_object": {"generation": "7"}},
    }

    assert (await rif.verify_raw_import_file(record))["status"] == "verified"
    refusals = {}
    for case, uri in REMOTE_OUTSIDE.items():
        with pytest.raises(HTTPException) as refused:
            await rif.verify_raw_import_file({**record, "storage_path": uri})
        refusals[case] = (refused.value.status_code, refused.value.detail)
    # The family's package folder is data too: one that is no package folder below the root
    # admits no object, as for the IGV endpoints.
    for package_folder in (None, "gs://phi/imports", "gs://phi/elsewhere/F1", "gs://phi/imports/F1/..", 42):
        with pytest.raises(HTTPException) as refused:
            await rif.verify_raw_import_file({**record, "package_folder": package_folder})
        refusals[f"package folder {package_folder!r}"] = (refused.value.status_code, refused.value.detail)

    # The store is asked about the object inside the family's package alone.
    assert set(refusals.values()) == {(403, OUTSIDE_THE_PACKAGE)}
    assert len(refusals) == len(REMOTE_OUTSIDE) + 5
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
    outside = {**_outside_paths(where), **_other_package_paths(where)}
    cases = OUTSIDE_CASES + OTHER_PACKAGE_CASES
    paths = [
        str(where.package),
        str(where.upload),
        str(where.imports / "run-2026-10" / "gone.vcf.gz"),
        *(outside[case] for case in cases),
    ]
    # Each row is read with its family's package folder.
    rows = [{"storage_path": path, "created_at": None, "package_folder": where.folder} for path in paths]

    listed = await rif.list_raw_import_files(_ListSession(rows), "family-uuid")  # type: ignore[arg-type]

    availability = [(row["exists"], row["download_available"], row["in_object_store"]) for row in listed]
    # A file outside the folders is listed neither present nor downloadable, whether it lies
    # outside the import area or in another family's package, at the import root or in
    # the staging folder.
    assert availability == [(True, True, False), (True, True, False)] + [(False, False, False)] * (1 + len(cases))
    # And the row says why: a file that is gone is not a location CoGA does not read.
    assert [row["outside_read_folders"] for row in listed] == [False, False, False] + [True] * len(cases)


@pytest.mark.asyncio
async def test_the_list_marks_an_object_outside_the_familys_package(
    where: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "family_import_roots", [str(where.imports), "gs://phi/imports"])
    uris = ["gs://phi/imports/F1/bams/S1.cram", *REMOTE_OUTSIDE.values()]
    rows = [{"storage_path": uri, "created_at": None, "package_folder": "gs://phi/imports/F1"} for uri in uris]

    listed = await rif.list_raw_import_files(_ListSession(rows), "family-uuid")  # type: ignore[arg-type]

    # Each is listed as kept in the object store, and marked outside the folders Verify reads
    #.
    assert [row["in_object_store"] for row in listed] == [True] * len(uris)
    assert [row["outside_read_folders"] for row in listed] == [False] + [True] * len(REMOTE_OUTSIDE)


# --------------------------------------------------------------------------- #
# The purges: the managed rows read in a delete's transaction, their copies unlinked once
# it has committed (test_deletions_unlink_files_after_commit.py)
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
    # The package file, the file elsewhere, the one reached by `..` and the one reached
    # through the linked folder are kept.
    assert where.package.read_text() == "package file"
    assert {name: path.read_text() for name, path in victims.items()} == {name: name for name in victims}
    assert (uploads / "F1" / "linked-out.vcf").is_symlink()
    skipped = ("rec-package", "rec-elsewhere", "rec-climbed", "rec-link-out", "rec-folder-link")
    paths = dict((record_id, path) for path, record_id in rows)
    messages = [record.getMessage() for record in caplog.records if record.name == rif.__name__]
    assert messages == [
        f"The file of raw-file record {record_id} was not deleted: its recorded storage path "
        "(an absolute local path) is outside the upload folder"
        for record_id in skipped
    ]
    # Never the path: it can name another family's folder.
    assert not any(paths[record_id] in message for record_id in skipped for message in messages)


@pytest.mark.asyncio
async def test_a_sample_delete_unlinks_only_managed_copies_in_the_upload_folder(where: SimpleNamespace) -> None:
    session = _PurgeSession([(str(where.upload), "rec-upload"), (str(where.secret), "rec-secret")])

    listed = await rif.sample_managed_files(session, "sample-uuid")  # type: ignore[arg-type]
    removed = await rif.unlink_managed_files(listed)

    assert session.params == [{"sample_uuid": "sample-uuid"}]
    assert removed == 1 and not where.upload.exists()
    # Kept: it is outside the upload folder.
    assert where.secret.read_text() == "secret"
