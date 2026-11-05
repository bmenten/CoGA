"""A package is validated and imported with the manifest CoGA keeps, instead of the folder's own.

The test deployment reads packages from a share mounted read-only. Validation and the import
read the manifest from the package folder alone, and Write manifest cannot put one there, so a
package on the share imported only when its pipeline wrote a CoGA manifest into it. An import
request now gives the manifest (``manifest_yaml``): the job keeps it with its SHA-256
(``family_import_jobs.manifest_text``, ``manifest_sha256``), validation and the import read it
instead of a file in the folder, and its paths are read relative to the package, which they
must not leave. The report and the family's package record name the manifest read
(``manifest_source``, ``manifest_sha256``), the folder's file too, by the SHA-256 of its bytes;
the family's raw files hold a managed copy of a kept one. Writing a manifest into a folder CoGA
cannot write answers 409 with what to do, not a 500.

All samples and values here are synthetic.
"""

from __future__ import annotations

import errno
import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException
import pytest

from backend.app.core.config import settings
from backend.app.services import family_package_discovery, family_package_jobs, family_package_registration, raw_import_files_pg
from backend.app.services.family_package_validation import load_validated_family_package

_PED = "FAM1\tPROBAND\t0\t0\t1\t2\n"
_KEPT = "schema_version: 1\nfamily_id: FAM1\nped: family.ped\n"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture
def package(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A one-member package with a PED and no manifest, under no import root (development)."""
    monkeypatch.setattr(settings, "family_import_roots", [])
    root = tmp_path / "FAM1"
    root.mkdir()
    (root / "family.ped").write_text(_PED, encoding="utf-8")
    return root


def test_a_folder_without_a_manifest_validates_with_the_kept_one(package: Path) -> None:
    folder_only, _ = load_validated_family_package(package)
    assert [issue.code for issue in folder_only.errors] == ["manifest_missing"]
    assert "kept in CoGA" in folder_only.errors[0].message

    validation, bundle = load_validated_family_package(package, manifest_text=_KEPT)

    assert validation.valid, validation.errors
    assert (validation.family_id, validation.sample_ids, validation.manifest_path) == ("FAM1", ["PROBAND"], None)
    assert validation.metadata["manifest_source"] == "coga"
    assert validation.metadata["manifest_sha256"] == _sha256(_KEPT)
    assert bundle is not None
    assert (bundle.manifest_path, bundle.kept_manifest_text, bundle.manifest_sha256) == (None, _KEPT, _sha256(_KEPT))
    assert sorted(path.name for path in package.iterdir()) == ["family.ped"]


def test_the_kept_manifest_is_read_instead_of_the_folders_own_and_says_so(package: Path) -> None:
    # A folder manifest naming another family: the kept one is the one read.
    (package / "manifest.yaml").write_text(_KEPT.replace("FAM1", "OTHER"), encoding="utf-8")

    validation, bundle = load_validated_family_package(package, manifest_text=_KEPT)

    assert validation.valid, validation.errors
    assert validation.family_id == "FAM1"
    assert "manifest_in_folder_not_read" in [issue.code for issue in validation.warnings]
    assert bundle is not None and bundle.kept_manifest_text == _KEPT


def test_a_kept_manifests_paths_stay_in_the_package(package: Path) -> None:
    (package.parent / "outside.vcf").write_text("##fileformat=VCFv4.2\n", encoding="utf-8")
    leaving = _KEPT + "datasets:\n  snv:\n    family_vcf: ../outside.vcf\n"

    # Refused as a folder manifest naming such a path is.
    with pytest.raises(HTTPException) as refused:
        load_validated_family_package(package, manifest_text=leaving)

    assert refused.value.status_code == 400
    assert "within the package directory" in str(refused.value.detail)


@pytest.mark.parametrize(
    ("text", "words"),
    [(_KEPT + "notes: a\x00b\n", "NUL"), (_KEPT + "notes: a\ud800b\n", "lone surrogate")],
)
def test_a_kept_manifest_that_no_text_can_hold_does_not_parse(package: Path, text: str, words: str) -> None:
    validation, bundle = load_validated_family_package(package, manifest_text=text)

    assert bundle is None
    assert [issue.code for issue in validation.errors] == ["manifest_parse_failed"]
    assert words in validation.errors[0].message


def test_a_folder_manifest_is_named_by_the_sha256_of_its_bytes(package: Path) -> None:
    raw = _KEPT.replace("\n", "\r\n").encode("utf-8")
    (package / "manifest.yaml").write_bytes(raw)

    validation, bundle = load_validated_family_package(package)

    assert validation.valid, validation.errors
    assert validation.manifest_path == str(package / "manifest.yaml")
    assert validation.metadata["manifest_source"] == "folder"
    assert validation.metadata["manifest_sha256"] == hashlib.sha256(raw).hexdigest()
    assert bundle is not None and bundle.kept_manifest_text is None


class _JobSession:
    """Answers the project look-up and keeps the job INSERT's parameters."""

    def __init__(self) -> None:
        self.inserted: dict[str, Any] | None = None

    async def execute(self, statement: Any, params: Any = None) -> Any:
        sql = str(statement)
        if "FROM projects" in sql:
            return SimpleNamespace(first=lambda: (1,))
        assert "INSERT INTO family_import_jobs" in sql
        self.inserted = dict(params)
        row = {
            "id": "00000000-0000-4000-8000-000000000001",
            "submitted_path": params["submitted_path"],
            "project_id": params["project_id"],
            "status": "queued",
            "dry_run": params["dry_run"],
            "requested_by": params["requested_by"],
            "requested_at": params["requested_at"],
            "manifest_sha256": params["manifest_sha256"],
        }
        return SimpleNamespace(mappings=lambda: SimpleNamespace(one=lambda: row))

    async def commit(self) -> None:
        return None


_REQUESTER = SimpleNamespace(id="00000000-0000-4000-8000-0000000000aa", email="superuser@example.org")
_PROJECT = "00000000-0000-4000-8000-0000000000bb"


@pytest.mark.asyncio
async def test_the_job_keeps_the_manifest_with_its_sha256() -> None:
    session = _JobSession()

    job = await family_package_jobs.queue_family_import_job(
        session,  # type: ignore[arg-type]
        folder_path="/data/share/FAM1",
        project_id=_PROJECT,
        dry_run=False,
        requested_by=_REQUESTER,  # type: ignore[arg-type]
        manifest_text=_KEPT,
    )

    assert session.inserted is not None
    assert (session.inserted["manifest_text"], session.inserted["manifest_sha256"]) == (_KEPT, _sha256(_KEPT))
    assert job.manifest_sha256 == _sha256(_KEPT)


@pytest.mark.asyncio
async def test_a_job_without_a_kept_manifest_keeps_none() -> None:
    session = _JobSession()

    job = await family_package_jobs.queue_family_import_job(
        session,  # type: ignore[arg-type]
        folder_path="/data/families/FAM1",
        project_id=_PROJECT,
        dry_run=True,
        requested_by=_REQUESTER,  # type: ignore[arg-type]
    )

    assert session.inserted is not None
    assert (session.inserted["manifest_text"], session.inserted["manifest_sha256"]) == (None, None)
    assert job.manifest_sha256 is None


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [_KEPT + "# \x00\n", _KEPT + "# \ud800\n"])
async def test_a_manifest_the_job_cannot_store_is_refused_before_the_job_is_written(text: str) -> None:
    session = _JobSession()

    with pytest.raises(HTTPException) as refused:
        await family_package_jobs.queue_family_import_job(
            session,  # type: ignore[arg-type]
            folder_path="/data/share/FAM1",
            project_id=_PROJECT,
            dry_run=False,
            requested_by=_REQUESTER,  # type: ignore[arg-type]
            manifest_text=text,
        )

    assert refused.value.status_code == 400
    assert session.inserted is None


@pytest.mark.parametrize("code", [errno.EROFS, errno.EACCES, errno.EPERM])
def test_writing_into_a_folder_coga_cannot_write_answers_409_with_what_to_do(
    package: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    def refuse(_root: Path, _text: str) -> Path:
        raise OSError(code, "refused")

    monkeypatch.setattr(family_package_discovery, "_write_manifest_in_folder", refuse)

    with pytest.raises(HTTPException) as refused:
        family_package_discovery.write_family_package_manifest(
            folder_path=package, manifest_yaml=_KEPT, overwrite=False
        )

    assert refused.value.status_code == 409
    assert refused.value.detail == family_package_discovery.MANIFEST_FOLDER_NOT_WRITABLE


def test_another_write_error_is_not_taken_for_a_read_only_folder(package: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def full(_root: Path, _text: str) -> Path:
        raise OSError(errno.ENOSPC, "no space")

    monkeypatch.setattr(family_package_discovery, "_write_manifest_in_folder", full)

    with pytest.raises(OSError):
        family_package_discovery.write_family_package_manifest(
            folder_path=package, manifest_yaml=_KEPT, overwrite=False
        )


class _RawFileSession:
    """Answers the family's sample lookup and keeps each raw_import_files row written."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def execute(self, statement: Any, params: Any = None) -> Any:
        if "INSERT INTO raw_import_files" in str(statement):
            self.rows.append(dict(params))
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: []))


@pytest.mark.asyncio
async def test_the_family_holds_a_managed_copy_of_the_kept_manifest(
    package: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(raw_import_files_pg, "DATA_DIR", tmp_path / "data")
    _validation, bundle = load_validated_family_package(package, manifest_text=_KEPT)
    assert bundle is not None
    session = _RawFileSession()

    await family_package_registration._record_package_raw_files(
        session,  # type: ignore[arg-type]
        bundle=bundle,
        family_uuid="family-uuid",
        family_identifier="FAM1",
    )

    assert [
        (row["dataset"], row["scope"], row["file_name"], row["managed"], row["sha256"], row["file_size"])
        for row in session.rows
    ] == [("manifest", "family", "manifest.yaml", True, _sha256(_KEPT), len(_KEPT.encode("utf-8")))]
    stored = Path(session.rows[0]["storage_path"])
    assert stored.parent == tmp_path / "data" / "raw_imports" / "FAM1"
    assert stored.read_text(encoding="utf-8") == _KEPT


@pytest.mark.asyncio
async def test_a_folder_manifest_gets_no_managed_copy(package: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(raw_import_files_pg, "DATA_DIR", tmp_path / "data")
    (package / "manifest.yaml").write_text(_KEPT, encoding="utf-8")
    _validation, bundle = load_validated_family_package(package)
    assert bundle is not None
    session = _RawFileSession()

    await family_package_registration._record_package_raw_files(
        session,  # type: ignore[arg-type]
        bundle=bundle,
        family_uuid="family-uuid",
        family_identifier="FAM1",
    )

    assert session.rows == []
    assert not (tmp_path / "data").exists()
