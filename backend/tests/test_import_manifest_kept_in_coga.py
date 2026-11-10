"""A package is validated and imported with the manifest CoGA keeps, and no file of its folder.

The test deployment reads packages from a share mounted read-only, and CoGA writes nothing into a
package folder. An approved delivery queues its import with the manifest its package makes: the
job keeps it with its SHA-256
(``family_import_jobs.manifest_text``, ``manifest_sha256``), validation and the import read it,
never a manifest or a PED in the folder, and its paths are read relative to the package, which
they must not leave. The report and the family's package record name the manifest read
(``manifest_source``, ``manifest_sha256``); the family's raw files hold a managed copy of it.

All samples and values here are synthetic.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException
import pytest

from backend.app.core.config import settings
from backend.app.services import family_package_jobs, family_package_registration, raw_import_files_pg
from backend.app.services.family_package_validation import load_validated_family_package

_KEPT = (
    "schema_version: 1\n"
    "family_id: FAM1\n"
    "family:\n"
    "  add_members:\n"
    "    - {sample_id: PROBAND, sex: male, clinical_status: affected}\n"
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture
def package(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A package folder with an SNV VCF and no manifest, under no import root (development)."""
    monkeypatch.setattr(settings, "family_import_roots", [])
    root = tmp_path / "FAM1"
    (root / "snv").mkdir(parents=True)
    (root / "snv" / "family.vcf").write_text("##fileformat=VCFv4.2\n", encoding="utf-8")
    return root


def test_the_kept_manifest_is_the_one_read(package: Path) -> None:
    validation, bundle = load_validated_family_package(package, manifest_text=_KEPT)

    assert validation.valid, validation.errors
    assert (validation.family_id, validation.sample_ids) == ("FAM1", ["PROBAND"])
    assert validation.metadata["manifest_source"] == "coga"
    assert validation.metadata["manifest_sha256"] == _sha256(_KEPT)
    assert bundle is not None
    assert (bundle.kept_manifest_text, bundle.manifest_sha256) == (_KEPT, _sha256(_KEPT))
    # CoGA wrote nothing into the folder.
    assert sorted(path.name for path in package.iterdir()) == ["snv"]


def test_a_manifest_and_a_ped_in_the_folder_are_not_read(package: Path) -> None:
    # A folder manifest naming another family and a PED of other members: the kept manifest
    # alone is read.
    (package / "OTHER.ped").write_text("OTHER\tMOTHER\t0\t0\t2\t1\nOTHER\tFATHER\t0\t0\t1\t1\n", encoding="utf-8")
    (package / "manifest.yaml").write_text("schema_version: 1\nfamily_id: OTHER\nped: OTHER.ped\n", encoding="utf-8")

    validation, bundle = load_validated_family_package(package, manifest_text=_KEPT)

    assert validation.valid, validation.errors
    assert (validation.family_id, validation.sample_ids) == ("FAM1", ["PROBAND"])
    assert bundle is not None and bundle.kept_manifest_text == _KEPT
    assert bundle.ped.sample_ids == ["PROBAND"]


def test_a_kept_manifests_paths_stay_in_the_package(package: Path) -> None:
    (package.parent / "outside.vcf").write_text("##fileformat=VCFv4.2\n", encoding="utf-8")
    leaving = _KEPT + "datasets:\n  snv:\n    family_vcf: ../outside.vcf\n"

    # A file is read only when it lies in the package.
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


def test_an_empty_manifest_is_refused(package: Path) -> None:
    # What a job that keeps no manifest is run with: the folder's files are not read in its place.
    (package / "manifest.yaml").write_text(_KEPT, encoding="utf-8")

    validation, bundle = load_validated_family_package(package, manifest_text="")

    assert bundle is None
    assert [issue.code for issue in validation.errors] == ["manifest_parse_failed"]


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
