from pathlib import Path

import pytest

from app.schemas import FamilyPackageManifestBuildRequest
from app.services.family_package_discovery import discover_family_package_manifest
from app.services.family_package_validation import load_validated_family_package
from app.services.ped_service import build_pedigree_text


@pytest.fixture(autouse=True)
def _unrestricted_import_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    # These tests validate/discover packages in tmp dirs; clear the configured
    # roots so the path-authorization guard (now defaulting to /data/families)
    # does not reject the temp paths. Path auth is covered in test_s3_import_and_cram.
    from app.core.config import settings

    monkeypatch.setattr(settings, "family_import_roots", [])


class _FakeResult:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def mappings(self):  # mimics SQLAlchemy Result.mappings()
        return self

    def all(self):
        return self._rows


class _FakeSession:
    """Returns canned results for successive execute() calls."""

    def __init__(self, *result_rows: list[dict]) -> None:
        self._queue = list(result_rows)

    async def execute(self, *_args, **_kwargs):
        return _FakeResult(self._queue.pop(0))


@pytest.mark.asyncio
async def test_build_pedigree_text_reconstructs_from_database() -> None:
    members = [
        {"sample_id": "DAD", "sex": "male", "clinical_status": "unaffected", "role": "father"},
        {"sample_id": "MOM", "sex": "female", "clinical_status": "unaffected", "role": "mother"},
        {"sample_id": "KID", "sex": "male", "clinical_status": "affected", "role": "proband"},
    ]
    relationships = [
        {"parent_id": "DAD", "child_id": "KID", "role_a": "father"},
        {"parent_id": "MOM", "child_id": "KID", "role_a": "mother"},
    ]
    session = _FakeSession(members, relationships)

    ped_text = await build_pedigree_text(session, family_id="FAM1")

    assert ped_text == (
        "FAM1 DAD 0 0 1 1\n"
        "FAM1 MOM 0 0 2 1\n"
        "FAM1 KID DAD MOM 1 2\n"
    )


@pytest.mark.asyncio
async def test_build_pedigree_text_missing_family_raises() -> None:
    session = _FakeSession([])  # no members
    with pytest.raises(Exception):
        await build_pedigree_text(session, family_id="UNKNOWN")


def _write_manifest(root: Path, *, ped_name: str = "FAM1.ped") -> None:
    (root / "manifest.yaml").write_text(
        "schema_version: 1\n"
        "family_id: FAM1\n"
        f"ped: {ped_name}\n",
        encoding="utf-8",
    )


def test_validation_uses_database_fallback_when_ped_absent(tmp_path: Path) -> None:
    _write_manifest(tmp_path)

    validation, bundle = load_validated_family_package(
        tmp_path,
        fallback_ped_text="FAM1 KID 0 0 1 2\n",
    )

    assert validation.valid is True
    assert validation.sample_ids == ["KID"]
    assert validation.metadata.get("ped_source") == "database"
    assert bundle is not None
    assert bundle.ped.sample_ids == ["KID"]


def test_validation_requires_ped_without_fallback(tmp_path: Path) -> None:
    _write_manifest(tmp_path)

    validation, bundle = load_validated_family_package(tmp_path)

    assert validation.valid is False
    assert bundle is None
    assert any(issue.code == "ped_file_missing" for issue in validation.errors)


def test_discover_uses_db_sample_ids_when_no_ped(tmp_path: Path) -> None:
    request = FamilyPackageManifestBuildRequest(
        folder_path=str(tmp_path),
        family_id="FAM1",
    )

    result = discover_family_package_manifest(request, db_sample_ids=["KID", "MOM"])

    assert result.sample_ids == ["KID", "MOM"]
    assert not any(issue.code == "ped_file_missing" for issue in result.errors)
    assert any(issue.code == "ped_from_database" for issue in result.warnings)
