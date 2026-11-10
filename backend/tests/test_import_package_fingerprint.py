"""A package's fingerprint, taken as the import is validated and queued, and checked as it starts.

An import is validated, queued and run at different moments, and the folder it reads is a
pipeline's output on a share that other hosts write. The fingerprint of a local package folder
(``family_package_fingerprint``) is the SHA-256 of each entry's path with a file's size and
modification time, or a link's target, and of the manifest CoGA keeps for the import: it tells
that the folder changed without reading a file's content. The validation takes it before reading
the folder and reports it; the job keeps the one it was queued with, and imports nothing when the
folder's differs as it starts; after each dataset it is taken again, and a change stops and
undoes the import (test_import_undone_when_package_changes.py). The family's package record
keeps it.

All samples and values here are synthetic.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.core.config import settings
from backend.app.schemas import FamilyPackageValidationOut
from backend.app.services import family_package_fingerprint, family_package_import, family_package_jobs, family_package_validation
from backend.app.services.family_package_common import _issue
from backend.app.services.family_package_fingerprint import PackageFolderTooLarge, package_fingerprint
from backend.app.services.family_package_validation import load_validated_family_package, take_package_fingerprint

_PED = "FAM1\tPROBAND\t0\t0\t1\t2\n"
# The manifest CoGA keeps for the import of the package: its one member under family.add_members.
_MANIFEST = (
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
    """A one-member package folder as a pipeline writes it, under no import root (development).
    Its ``manifest.yaml`` and PED are files like any other: the import reads the manifest CoGA
    keeps for it (``_MANIFEST``)."""
    monkeypatch.setattr(settings, "family_import_roots", [])
    root = tmp_path / "FAM1"
    (root / "snv").mkdir(parents=True)
    (root / "family.ped").write_text(_PED, encoding="utf-8")
    (root / "manifest.yaml").write_text(_MANIFEST, encoding="utf-8")
    (root / "snv" / "family.vcf").write_text("##fileformat=VCFv4.2\n", encoding="utf-8")
    return root


def _fingerprint(root: Path, kept: str | None = None) -> str:
    return package_fingerprint(root, kept_manifest_sha256=kept).sha256


# --- the fingerprint ------------------------------------------------------------------------


def test_the_fingerprint_is_the_same_for_the_same_folder_and_counts_its_files(package: Path) -> None:
    first = package_fingerprint(package, kept_manifest_sha256=None)

    assert first == package_fingerprint(package, kept_manifest_sha256=None)
    assert len(first.sha256) == 64 and set(first.sha256) <= set("0123456789abcdef")
    assert first.files == 3
    assert first.short == first.sha256[:12]


@pytest.mark.parametrize(
    "change",
    [
        pytest.param(lambda root: (root / "snv" / "family.vcf").write_text("##fileformat=VCFv4.3\n#\n"), id="size"),
        pytest.param(lambda root: os.utime(root / "family.ped", ns=(1, 1)), id="modification-time"),
        pytest.param(lambda root: (root / "snv" / "family.vcf.tbi").write_bytes(b"x"), id="file-added"),
        pytest.param(lambda root: (root / "snv" / "family.vcf").unlink(), id="file-removed"),
        pytest.param(lambda root: (root / "family.ped").rename(root / "other.ped"), id="file-renamed"),
        pytest.param(lambda root: (root / "snv").rename(root / "calls"), id="folder-renamed"),
        pytest.param(lambda root: (root / "linked.ped").symlink_to("family.ped"), id="link-added"),
    ],
)
def test_any_change_to_a_file_makes_another_fingerprint(package: Path, change: Any) -> None:
    before = _fingerprint(package)

    change(package)

    assert _fingerprint(package) != before


def test_a_links_target_is_part_of_the_fingerprint_and_the_link_is_not_followed(package: Path) -> None:
    (package / "other.ped").write_text(_PED, encoding="utf-8")
    (package / "linked.ped").symlink_to("family.ped")
    # A link that leads out of the package, to a folder the fingerprint does not walk.
    outside = package.parent / "outside"
    outside.mkdir()
    (package / "elsewhere").symlink_to(outside, target_is_directory=True)
    before = _fingerprint(package)

    (outside / "big.cram").write_bytes(b"\0" * 10)
    assert _fingerprint(package) == before

    (package / "linked.ped").unlink()
    (package / "linked.ped").symlink_to("other.ped")
    assert _fingerprint(package) != before


def test_what_a_file_browser_writes_and_an_empty_folder_change_nothing(package: Path) -> None:
    before = _fingerprint(package)

    for name in (".DS_Store", "._family.ped", "Thumbs.db", "desktop.ini"):
        (package / name).write_bytes(b"browser")
    (package / "snv" / ".DS_Store").write_bytes(b"browser")
    (package / "empty").mkdir()

    assert _fingerprint(package) == before


def test_the_kept_manifest_is_part_of_the_fingerprint(package: Path) -> None:
    folder = _fingerprint(package)
    kept = _fingerprint(package, _sha256(_MANIFEST))

    assert kept != folder
    assert _fingerprint(package, _sha256(_MANIFEST + "# edited\n")) != kept


def test_names_that_hold_separators_are_told_apart(tmp_path: Path) -> None:
    one, other = tmp_path / "one", tmp_path / "other"
    one.mkdir()
    other.mkdir()
    (one / "a\tb").write_bytes(b"")
    (other / "a").write_bytes(b"")
    os.utime(one / "a\tb", ns=(5, 5))
    os.utime(other / "a", ns=(5, 5))

    assert _fingerprint(one) != _fingerprint(other)


def test_the_fingerprint_does_not_depend_on_the_order_the_folder_lists_its_entries(tmp_path: Path) -> None:
    def build(root: Path, names: list[str]) -> None:
        root.mkdir()
        for name in names:
            (root / name).write_bytes(name.encode())
            os.utime(root / name, ns=(7, 7))

    build(tmp_path / "one", ["b.vcf", "a.ped", "c.txt"])
    build(tmp_path / "other", ["c.txt", "a.ped", "b.vcf"])

    assert _fingerprint(tmp_path / "one") == _fingerprint(tmp_path / "other")


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root lists a folder without permission")
def test_a_folder_that_cannot_be_listed_is_part_of_the_fingerprint(package: Path) -> None:
    before = _fingerprint(package)
    os.chmod(package / "snv", 0)
    try:
        hidden = _fingerprint(package)
    finally:
        os.chmod(package / "snv", 0o755)

    assert hidden != before
    assert _fingerprint(package) == before


def test_a_folder_too_large_to_be_a_package_is_not_walked_further(package: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(family_package_fingerprint, "PACKAGE_FINGERPRINT_MAX_ENTRIES", 3)

    with pytest.raises(PackageFolderTooLarge):
        _fingerprint(package)


def test_an_entry_that_cannot_be_looked_up_or_is_no_file_is_part_of_the_fingerprint(
    package: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _fingerprint(package)
    os.mkfifo(package / "pipe")
    with_fifo = _fingerprint(package)
    assert with_fifo != before

    real_scandir = os.scandir

    class _Vanished:
        def __init__(self, entry: os.DirEntry[str]) -> None:
            self._entry = entry
            self.name, self.path = entry.name, entry.path

        def is_symlink(self) -> bool:
            return False

        def stat(self, *, follow_symlinks: bool = True) -> os.stat_result:
            if self.name == "family.ped":
                raise FileNotFoundError(2, "gone")
            return self._entry.stat(follow_symlinks=follow_symlinks)

    class _Listing:
        def __init__(self, path: Any) -> None:
            self._listing = real_scandir(path)

        def __enter__(self) -> list[_Vanished]:
            return [_Vanished(entry) for entry in self._listing]

        def __exit__(self, *exc: object) -> None:
            self._listing.close()

    monkeypatch.setattr(family_package_fingerprint.os, "scandir", _Listing)

    assert _fingerprint(package) != with_fifo


# --- the validation -------------------------------------------------------------------------


def test_the_validation_reports_the_fingerprint_taken_before_it_read_the_folder(
    package: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = _fingerprint(package, _sha256(_MANIFEST))
    taken: list[str] = []
    real = family_package_validation._validate_and_load_package

    def validate(*args: Any, **kwargs: Any) -> Any:
        # Read after the fingerprint: a change now shows as one the folder no longer has.
        taken.append("validated")
        (package / "late.txt").write_text("written while the validation reads", encoding="utf-8")
        return real(*args, **kwargs)

    monkeypatch.setattr(family_package_validation, "_validate_and_load_package", validate)

    validation, bundle = load_validated_family_package(package, manifest_text=_MANIFEST)

    assert taken == ["validated"]
    assert validation.valid, validation.errors
    assert validation.package_fingerprint == expected
    assert bundle is not None and bundle.package_fingerprint == expected
    assert _fingerprint(package, _sha256(_MANIFEST)) != expected


def test_the_validation_reports_the_fingerprint_read_with_its_kept_manifest(package: Path) -> None:
    edited = _MANIFEST + "# edited\n"

    validation, bundle = load_validated_family_package(package, manifest_text=_MANIFEST)
    other, _other_bundle = load_validated_family_package(package, manifest_text=edited)

    assert validation.valid, validation.errors
    assert validation.package_fingerprint == _fingerprint(package, _sha256(_MANIFEST))
    assert bundle is not None and bundle.package_fingerprint == validation.package_fingerprint
    # The same folder with another kept manifest: another fingerprint.
    assert other.package_fingerprint == _fingerprint(package, _sha256(edited))
    assert other.package_fingerprint != validation.package_fingerprint


def test_a_package_staged_from_a_bucket_has_no_fingerprint(package: Path) -> None:
    validation, bundle = load_validated_family_package(
        package, manifest_text=_MANIFEST, source_uri="gs://bucket/packages/FAM1"
    )

    assert validation.valid, validation.errors
    assert validation.package_fingerprint is None
    assert bundle is not None and bundle.package_fingerprint is None


def test_a_folder_too_large_is_refused_before_anything_in_it_is_read(
    package: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(family_package_fingerprint, "PACKAGE_FINGERPRINT_MAX_ENTRIES", 2)

    def never(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("the folder was read")

    monkeypatch.setattr(family_package_validation, "_validate_and_load_package", never)

    validation, bundle = load_validated_family_package(package, manifest_text=_MANIFEST)

    assert bundle is None
    assert [issue.code for issue in validation.errors] == ["package_folder_too_large"]
    assert "more than 2 files and folders" in validation.errors[0].message


def test_a_folder_that_cannot_be_listed_is_refused(package: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(family_package_validation, "package_fingerprint", refuse)

    validation, bundle = load_validated_family_package(package, manifest_text=_MANIFEST)

    assert bundle is None
    assert [issue.code for issue in validation.errors] == ["package_folder_unreadable"]
    assert validation.errors[0].message == "The package folder could not be listed: Permission denied"


def test_a_folder_the_validation_refuses_itself_has_no_fingerprint(package: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert take_package_fingerprint(package / "missing", manifest_text=_MANIFEST) == (None, None)
    assert take_package_fingerprint(package / "family.ped", manifest_text=_MANIFEST) == (None, None)
    assert take_package_fingerprint(package, manifest_text=_MANIFEST + "# \x00\n") == (None, None)
    monkeypatch.setattr(settings, "family_import_roots", [str(package.parent / "elsewhere")])
    assert take_package_fingerprint(package, manifest_text=_MANIFEST) == (None, None)


# --- the queue ------------------------------------------------------------------------------


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
            "package_fingerprint": params["package_fingerprint"],
        }
        return SimpleNamespace(mappings=lambda: SimpleNamespace(one=lambda: row))

    async def commit(self) -> None:
        return None


_REQUESTER = SimpleNamespace(id="00000000-0000-4000-8000-0000000000aa", email="superuser@example.org")
_PROJECT = "00000000-0000-4000-8000-0000000000bb"


async def _queue(session: _JobSession, folder: str) -> Any:
    return await family_package_jobs.queue_family_import_job(
        session,  # type: ignore[arg-type]
        folder_path=folder,
        project_id=_PROJECT,
        dry_run=False,
        requested_by=_REQUESTER,  # type: ignore[arg-type]
        manifest_text=_MANIFEST,
    )


@pytest.mark.asyncio
async def test_the_job_keeps_the_fingerprint_of_the_folder_as_it_is_queued(package: Path) -> None:
    # Read with the manifest the job keeps.
    session = _JobSession()

    job = await _queue(session, str(package))

    assert session.inserted is not None
    assert session.inserted["manifest_sha256"] == _sha256(_MANIFEST)
    assert session.inserted["package_fingerprint"] == _fingerprint(package, _sha256(_MANIFEST))
    assert job.package_fingerprint == _fingerprint(package, _sha256(_MANIFEST))


@pytest.mark.asyncio
async def test_a_folder_the_queue_cannot_read_and_a_bucket_are_queued_without_a_fingerprint(package: Path) -> None:
    session = _JobSession()

    missing = await _queue(session, str(package / "gone"))
    remote = await _queue(session, "gs://bucket/packages/FAM1")

    assert (missing.package_fingerprint, remote.package_fingerprint) == (None, None)


def _validation(fingerprint: str | None, *, errors: list[Any] | None = None) -> FamilyPackageValidationOut:
    return FamilyPackageValidationOut(valid=not errors, package_fingerprint=fingerprint, errors=errors or [])


def test_a_job_whose_folder_is_as_it_was_queued_goes_on() -> None:
    logs: list[str] = []

    checked = family_package_import._check_queued_package_fingerprint(
        _validation("a" * 64), logs, source_uri=None, queued="a" * 64
    )

    assert checked.valid and checked.errors == []
    assert logs == [f"Package fingerprint {'a' * 12}: the folder is as it was when the job was queued."]


def test_a_job_whose_folder_changed_since_it_was_queued_imports_nothing() -> None:
    logs: list[str] = []

    checked = family_package_import._check_queued_package_fingerprint(
        _validation("b" * 64), logs, source_uri=None, queued="a" * 64
    )

    assert checked.valid is False
    assert [(issue.code, issue.message) for issue in checked.errors] == [
        ("package_changed_since_queued", family_package_import.PACKAGE_CHANGED_SINCE_QUEUED)
    ]
    assert logs == [f"Package fingerprint {'b' * 12} differs from {'a' * 12}, taken when the job was queued."]


def test_a_job_queued_without_a_fingerprint_says_there_is_none_to_compare() -> None:
    logs: list[str] = []

    checked = family_package_import._check_queued_package_fingerprint(
        _validation("a" * 64), logs, source_uri=None, queued=None
    )

    assert checked.valid
    assert logs == [f"Package fingerprint {'a' * 12}; there is no earlier fingerprint to compare it with."]


def test_a_job_from_a_bucket_says_it_is_not_checked() -> None:
    logs: list[str] = []

    checked = family_package_import._check_queued_package_fingerprint(
        _validation(None), logs, source_uri="gs://bucket/packages/FAM1", queued=None
    )

    assert checked.valid
    assert logs == [
        "A package staged from a bucket has no fingerprint: whether it changed since the job was queued is not checked."
    ]


def test_a_folder_the_validation_refused_keeps_that_refusal_alone() -> None:
    gone = _issue("package_folder_missing", "Family package folder does not exist")
    logs: list[str] = []
    validation = _validation(None, errors=[gone])

    checked = family_package_import._check_queued_package_fingerprint(validation, logs, source_uri=None, queued="a" * 64)

    assert checked.errors == [gone]
    assert logs == []


@pytest.mark.asyncio
async def test_a_folder_as_the_validation_read_it_has_not_changed(package: Path) -> None:
    _validation_out, bundle = load_validated_family_package(package, manifest_text=_MANIFEST)
    assert bundle is not None
    logs: list[str] = []

    assert await family_package_import._package_changed_since_validation(bundle, logs) is False
    assert logs == []
    assert await family_package_import._package_changed_since_validation(bundle, logs, say_unchanged=True) is False
    assert logs == [
        f"The package folder did not change while it was imported (fingerprint {bundle.package_fingerprint[:12]})."  # type: ignore[index]
    ]


@pytest.mark.asyncio
async def test_a_folder_changed_since_the_validation_read_it_is_told(package: Path) -> None:
    _validation_out, bundle = load_validated_family_package(package, manifest_text=_MANIFEST)
    assert bundle is not None and bundle.package_fingerprint is not None
    (package / "snv" / "family.vcf").write_text("##fileformat=VCFv4.2\n#rerun\n", encoding="utf-8")
    logs: list[str] = []

    assert await family_package_import._package_changed_since_validation(bundle, logs) is True
    after = _fingerprint(package, _sha256(_MANIFEST))
    assert logs == [
        f"The package folder changed while it was imported: its fingerprint is {after[:12]}, "
        f"not {bundle.package_fingerprint[:12]}."
    ]


@pytest.mark.asyncio
async def test_a_folder_that_can_no_longer_be_read_counts_as_changed(package: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _validation_out, bundle = load_validated_family_package(package, manifest_text=_MANIFEST)
    assert bundle is not None and bundle.package_fingerprint is not None

    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(family_package_import, "package_fingerprint", refuse)
    logs: list[str] = []

    assert await family_package_import._package_changed_since_validation(bundle, logs) is True
    assert logs == [
        f"The package folder changed while it was imported: its fingerprint is unreadable, "
        f"not {bundle.package_fingerprint[:12]}."
    ]


@pytest.mark.asyncio
async def test_a_package_from_a_bucket_is_not_read_again(package: Path) -> None:
    _validation_out, bundle = load_validated_family_package(
        package, manifest_text=_MANIFEST, source_uri="gs://bucket/packages/FAM1"
    )
    assert bundle is not None
    (package / "late.txt").write_text("changed", encoding="utf-8")
    logs: list[str] = []

    assert await family_package_import._package_changed_since_validation(bundle, logs) is False
    assert logs == []
