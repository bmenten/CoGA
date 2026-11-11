"""The package file ``coga-package.json`` in its folder, and the folder checked against it.

CoGA reads the package file as a regular file in the package folder, never through a link and
never past ``PACKAGE_MAX_BYTES``; it reads the file's ``status`` even when the file breaks the
format, so that a run under way is never offered for import. It checks the folder against the
files the package file lists: a file missing or of another size means the copy runs still, a
checksum that differs a copy that is not what the pipeline made, and a path that leads out of the
folder through a link is refused. A file the package file does not list is reported apart, but for
what a file browser writes and the package file itself.

All samples and values here are synthetic.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from backend.app.services import coga_package as package_module
from backend.app.services.coga_package import (
    PACKAGE_FILE_NAME,
    PackageFile,
    check_package_files,
    read_package_file,
)


def _entry(folder: Path, path: str, *, md5: bool = False) -> dict[str, Any]:
    content = (folder / path).read_bytes()
    entry: dict[str, Any] = {"path": path, "size": len(content)}
    if md5:
        entry["md5"] = hashlib.md5(content, usedforsecurity=False).hexdigest()
    else:
        entry["sha256"] = hashlib.sha256(content).hexdigest()
    return entry


def _package_file(folder: Path, **fields: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema": "coga-package/1",
        "status": "complete",
        "written_at": "2026-11-10T14:02:11Z",
        "pipeline": {"name": "nf-core/example", "version": "1.0.0", "run_id": "RUN1"},
        "project": "PROJECT1",
        "assembly": "GRCh38",
        "analysis_type": "wgs_trio",
        "family": {"family_id": "FAM1"},
        "files": [_entry(folder, "family.ped"), _entry(folder, "snv/family.vcf")],
    }
    body.update(fields)
    return body


def _write(folder: Path, body: Any) -> None:
    (folder / PACKAGE_FILE_NAME).write_text(json.dumps(body) if not isinstance(body, str) else body, encoding="utf-8")


@pytest.fixture
def package(tmp_path: Path) -> Path:
    root = tmp_path / "FAM1"
    (root / "snv").mkdir(parents=True)
    (root / "family.ped").write_text("FAM1\tPROBAND\t0\t0\t1\t2\n", encoding="utf-8")
    (root / "snv" / "family.vcf").write_text("##fileformat=VCFv4.2\n", encoding="utf-8")
    return root


def _codes(issues: Any) -> list[str]:
    return [issue.code for issue in issues]


# --- reading the file in its folder ----------------------------------------------------------


def test_a_complete_package_file_is_read_with_its_sha256_and_offered(package: Path) -> None:
    body = _package_file(package)
    _write(package, body)

    read = read_package_file(package)

    assert read.package is not None and read.issues == []
    assert read.sha256 == hashlib.sha256((package / PACKAGE_FILE_NAME).read_bytes()).hexdigest()
    assert (read.status, read.payload, read.offered) == ("complete", body, True)


@pytest.mark.parametrize("status", ["running", "failed"])
def test_a_run_under_way_or_failed_is_not_offered(package: Path, status: str) -> None:
    _write(package, {"schema": "coga-package/1", "status": status, "written_at": "2026-11-10T09:00:00Z",
                     "pipeline": {"name": "p", "version": "1", "run_id": "RUN2"}})

    read = read_package_file(package)

    assert (read.status, read.offered, read.issues) == (status, False, [])


@pytest.mark.parametrize(
    ("body", "offered"),
    [
        # A run under way that breaks the format is still a run under way.
        ({"schema": "coga-package/1", "status": "running"}, False),
        ({"schema": "coga-package/2", "status": "running"}, False),
        # A status the format does not know is offered, with what is wrong with it.
        ({"schema": "coga-package/1", "status": "done"}, True),
        ({"schema": "coga-package/1"}, True),
    ],
)
def test_the_status_is_read_from_a_file_that_breaks_the_format(package: Path, body: dict[str, Any], offered: bool) -> None:
    _write(package, body)

    read = read_package_file(package)

    assert read.package is None and read.issues
    assert read.offered is offered
    assert read.payload == body


@pytest.mark.parametrize("escape", ["\\u0000", "\\ud800"])
def test_a_package_file_no_stored_text_can_hold_is_kept_without_its_payload(package: Path, escape: str) -> None:
    _write(package, '{"schema": "coga-package/1", "status": "running", "notes": "a' + escape + '"}')

    read = read_package_file(package)

    assert (read.package, read.payload, read.status, _codes(read.issues)) == (None, None, "running", ["package_unstorable_text"])


def test_a_package_file_that_is_a_link_is_not_followed(package: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(_package_file(package)), encoding="utf-8")
    (package / PACKAGE_FILE_NAME).symlink_to(outside)

    read = read_package_file(package)

    assert (read.package, read.sha256, _codes(read.issues), read.offered) == (None, None, ["package_not_a_file"], True)


def test_a_folder_without_a_package_file_is_told(package: Path) -> None:
    assert _codes(read_package_file(package).issues) == ["package_missing"]


def test_a_package_file_too_large_is_not_read(package: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(package, _package_file(package))
    monkeypatch.setattr(package_module, "PACKAGE_MAX_BYTES", 10)

    assert _codes(read_package_file(package).issues) == ["package_too_large"]


def test_a_package_file_that_cannot_be_read_is_told(package: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(package, _package_file(package))

    def refuse(*_args: Any, **_kwargs: Any) -> int:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(package_module.os, "open", refuse)

    assert _codes(read_package_file(package).issues) == ["package_unreadable"]


# --- the folder against the files listed -----------------------------------------------------


def _files(entries: list[dict[str, Any]]) -> list[PackageFile]:
    return [PackageFile.model_validate(entry) for entry in entries]


def test_a_folder_as_its_package_file_lists_it_passes_with_each_files_sha256(package: Path) -> None:
    entries = [_entry(package, "family.ped"), _entry(package, "snv/family.vcf", md5=True)]
    _write(package, {"schema": "coga-package/1"})
    (package / ".DS_Store").write_bytes(b"browser")
    (package / "snv" / "._family.vcf").write_bytes(b"browser")
    # The temporary name a pipeline writes the package file under before renaming it.
    (package / f".{PACKAGE_FILE_NAME}.tmp").write_bytes(b"{}")

    check = check_package_files(package, _files(entries), checksums=True)

    assert (check.issues, check.incomplete, check.unlisted) == ([], False, [])
    assert check.sha256 == {
        "family.ped": hashlib.sha256((package / "family.ped").read_bytes()).hexdigest(),
        "snv/family.vcf": hashlib.sha256((package / "snv" / "family.vcf").read_bytes()).hexdigest(),
    }


def test_a_file_missing_or_of_another_size_means_the_copy_runs_still(package: Path) -> None:
    entries = [_entry(package, "family.ped"), _entry(package, "snv/family.vcf")]
    entries[1]["size"] += 10
    entries.append({"path": "snv/family.vcf.tbi", "size": 4, "sha256": "0" * 64})

    check = check_package_files(package, _files(entries), checksums=False)

    assert check.incomplete is True
    assert [(issue.code, issue.location) for issue in check.issues] == [
        ("file_size_differs", "snv/family.vcf"),
        ("file_missing", "snv/family.vcf.tbi"),
    ]
    assert check.sha256 == {}


def test_a_file_listed_without_a_checksum_has_its_sha256_computed_and_is_named(package: Path) -> None:
    # The owner's decision of 2026-11-11: a checksum per file is optional. CoGA computes the
    # SHA-256 of a file listed without one, and names it, since the copy is then unchecked.
    with_one = _entry(package, "family.ped")
    without = {key: value for key, value in _entry(package, "snv/family.vcf").items() if key != "sha256"}

    check = check_package_files(package, _files([with_one, without]), checksums=True)

    assert check.issues == []
    assert check.sha256 == {
        "family.ped": hashlib.sha256((package / "family.ped").read_bytes()).hexdigest(),
        "snv/family.vcf": hashlib.sha256((package / "snv" / "family.vcf").read_bytes()).hexdigest(),
    }
    assert check.without_checksum == ["snv/family.vcf"]
    # Its size is still checked: a copy still running is told without reading a file.
    short = {**without, "size": without["size"] + 1}
    sizes = check_package_files(package, _files([short]), checksums=False)
    assert (sizes.incomplete, [issue.code for issue in sizes.issues]) == (True, ["file_size_differs"])


@pytest.mark.parametrize("md5", [False, True])
def test_a_checksum_that_differs_is_a_copy_the_pipeline_did_not_make(package: Path, md5: bool) -> None:
    entries = [_entry(package, "family.ped", md5=md5)]
    (package / "family.ped").write_text("FAM1\tPROBAND\t0\t0\t2\t2\n", encoding="utf-8")

    check = check_package_files(package, _files(entries), checksums=True)

    assert check.incomplete is False
    assert [(issue.code, issue.location) for issue in check.issues] == [("file_checksum_differs", "family.ped")]
    assert check.unlisted == ["snv/family.vcf"]


def test_a_path_through_a_link_out_of_the_folder_is_refused_and_a_file_listed_twice_read_once(
    package: Path, tmp_path: Path
) -> None:
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "file.vcf").write_text("x", encoding="utf-8")
    (package / "linked").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    entries = [
        {"path": "linked/file.vcf", "size": 1, "sha256": "0" * 64},
        _entry(package, "family.ped"),
        _entry(package, "family.ped"),
    ]

    check = check_package_files(package, _files(entries), checksums=True)

    assert [(issue.code, issue.location) for issue in check.issues] == [("file_outside", "linked/file.vcf")]
    assert list(check.sha256) == ["family.ped"]


def test_a_listed_link_or_folder_is_no_file(package: Path) -> None:
    (package / "linked.ped").symlink_to("family.ped")

    check = check_package_files(
        package,
        _files([{"path": "linked.ped", "size": 1, "sha256": "0" * 64}, {"path": "snv", "size": 1, "sha256": "0" * 64}]),
        checksums=True,
    )

    assert [(issue.code, issue.location) for issue in check.issues] == [
        ("file_not_regular", "linked.ped"),
        ("file_not_regular", "snv"),
    ]


def test_a_listed_file_that_cannot_be_read_is_told(package: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entries = [_entry(package, "family.ped")]

    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(package_module, "_checksums", refuse)

    check = check_package_files(package, _files(entries), checksums=True)

    assert _codes(check.issues) == ["file_unreadable"]


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root lists a folder without permission")
def test_a_subfolder_that_cannot_be_listed_hides_no_issue(package: Path) -> None:
    entries = [_entry(package, "family.ped")]
    os.chmod(package / "snv", 0)
    try:
        check = check_package_files(package, _files(entries), checksums=True)
    finally:
        os.chmod(package / "snv", 0o755)

    assert (check.issues, check.unlisted) == ([], [])


def test_a_folder_too_large_to_be_a_package_is_refused(package: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(package_module, "PACKAGE_FINGERPRINT_MAX_ENTRIES", 1)

    with pytest.raises(package_module.PackageFolderTooLarge):
        check_package_files(package, [], checksums=False)
