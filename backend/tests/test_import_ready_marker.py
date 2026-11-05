"""The ready marker a pipeline writes last into a package folder, read and checked.

``coga-ready.json`` (format ``coga-ready/1``) says that a package folder is complete, what each
of its files is (path, size, SHA-256 or MD5) and where the package belongs. CoGA reads it as a
regular file in the folder, never through a link and never past ``MARKER_MAX_BYTES``; a field the
format requires that is missing is an issue, and the folder is checked against the files it lists:
a file missing or of another size means the copy runs still, a checksum that differs a copy that
is not what the pipeline made, and a path out of the folder is refused. A file the marker does
not list is reported apart, but for what a file browser writes.

All samples and values here are synthetic.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from backend.app.services import import_ready_marker as marker_module
from backend.app.services.import_ready_marker import (
    MARKER_FILE_NAME,
    MarkerFile,
    check_marker_files,
    marker_path_in_folder,
    read_ready_marker,
)


def _entry(folder: Path, path: str, *, md5: bool = False) -> dict[str, Any]:
    content = (folder / path).read_bytes()
    entry: dict[str, Any] = {"path": path, "size": len(content)}
    if md5:
        entry["md5"] = hashlib.md5(content, usedforsecurity=False).hexdigest()
    else:
        entry["sha256"] = hashlib.sha256(content).hexdigest()
    return entry


def _marker(folder: Path, **fields: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema": "coga-ready/1",
        "written_at": "2026-11-05T14:02:11Z",
        "project": "PROJECT1",
        "family_id": "FAM1",
        "analysis_type": "wgs_trio",
        "assembly": "GRCh38",
        "pipeline": {"name": "nf-core/example", "version": "1.0.0"},
        "run": {"id": "run-1"},
        "files": [_entry(folder, "family.ped"), _entry(folder, "snv/family.vcf")],
    }
    body.update(fields)
    return body


def _write(folder: Path, body: Any) -> None:
    (folder / MARKER_FILE_NAME).write_text(json.dumps(body) if not isinstance(body, str) else body, encoding="utf-8")


@pytest.fixture
def package(tmp_path: Path) -> Path:
    root = tmp_path / "FAM1"
    (root / "snv").mkdir(parents=True)
    (root / "family.ped").write_text("FAM1\tPROBAND\t0\t0\t1\t2\n", encoding="utf-8")
    (root / "snv" / "family.vcf").write_text("##fileformat=VCFv4.2\n", encoding="utf-8")
    return root


def _codes(issues: Any) -> list[str]:
    return [issue.code for issue in issues]


# --- reading ---------------------------------------------------------------------------------


def test_a_complete_marker_is_read_with_its_sha256(package: Path) -> None:
    _write(package, _marker(package, request_id="REQ-1", samples=[{"sample_id": "PROBAND", "sex": "female"}], extra={"a": 1}))

    read = read_ready_marker(package)

    assert read.issues == []
    assert read.marker is not None
    assert (read.marker.family_id, read.marker.request_id, read.marker.run.id) == ("FAM1", "REQ-1", "run-1")  # type: ignore[union-attr]
    assert read.sha256 == hashlib.sha256((package / MARKER_FILE_NAME).read_bytes()).hexdigest()
    # A field the format does not know is kept.
    assert read.payload is not None and read.payload["extra"] == {"a": 1}


@pytest.mark.parametrize(
    ("change", "code"),
    [
        (lambda folder: None, "marker_missing"),
        (lambda folder: _write(folder, "{not json"), "marker_not_json"),
        (lambda folder: _write(folder, "[1, 2]"), "marker_not_json"),
        (lambda folder: (folder / MARKER_FILE_NAME).write_bytes(b"\xff\xfe"), "marker_not_json"),
        (lambda folder: _write(folder, {"schema": "coga-ready/2"}), "marker_schema_unknown"),
        (lambda folder: _write(folder, {"schema": "coga-ready/1", "files": [{"path": "a", "size": -1}]}), "marker_invalid"),
        (lambda folder: _write(folder, {"schema": "coga-ready/1", "files": [{"path": "a", "size": 1, "sha256": "XYZ"}]}), "marker_invalid"),
    ],
)
def test_a_marker_that_is_not_one_of_the_format_is_told(package: Path, change: Any, code: str) -> None:
    change(package)

    read = read_ready_marker(package)

    assert read.marker is None
    assert _codes(read.issues) == [code]


@pytest.mark.parametrize("escape", ["\\u0000", "\\ud800"])
def test_a_marker_no_stored_text_can_hold_is_refused(package: Path, escape: str) -> None:
    text = json.dumps(_marker(package)).replace('"FAM1"', f'"FAM{escape}1"', 1)
    _write(package, text)

    read = read_ready_marker(package)

    assert (read.marker, read.payload, _codes(read.issues)) == (None, None, ["marker_unstorable"])


def test_a_marker_that_is_a_link_is_not_followed(package: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(_marker(package)), encoding="utf-8")
    (package / MARKER_FILE_NAME).symlink_to(outside)

    read = read_ready_marker(package)

    assert (read.marker, read.sha256, _codes(read.issues)) == (None, None, ["marker_not_a_file"])


def test_a_marker_too_large_is_not_read(package: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(package, _marker(package))
    monkeypatch.setattr(marker_module, "MARKER_MAX_BYTES", 10)

    assert _codes(read_ready_marker(package).issues) == ["marker_too_large"]


def test_a_marker_that_cannot_be_read_is_told(package: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(package, _marker(package))

    def refuse(_self: Path) -> bytes:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "read_bytes", refuse)

    assert _codes(read_ready_marker(package).issues) == ["marker_unreadable"]


def test_each_field_the_format_requires_and_the_marker_lacks_is_an_issue(package: Path) -> None:
    _write(package, {"schema": "coga-ready/1", "pipeline": {"name": "nf-core/example"}, "run": {}, "files": []})

    read = read_ready_marker(package)

    assert read.marker is not None
    assert [issue.message for issue in read.issues] == [
        f"coga-ready.json does not give {name}."
        for name in (
            "written_at",
            "project",
            "family_id",
            "analysis_type",
            "assembly",
            "pipeline.version",
            "run.id",
            "files",
        )
    ]


def test_an_analysis_type_coga_does_not_know_is_an_issue(package: Path) -> None:
    _write(package, _marker(package, analysis_type="tumour_normal"))

    assert _codes(read_ready_marker(package).issues) == ["marker_analysis_type_unknown"]


# --- paths -----------------------------------------------------------------------------------


@pytest.mark.parametrize("written", ["/etc/passwd", "../outside.vcf", "snv/../../outside.vcf", "snv\\family.vcf", "a\x00b", "", ".", "snv//family.vcf"])
def test_a_path_that_leaves_the_package_is_none(package: Path, written: str) -> None:
    assert marker_path_in_folder(package, written) is None


def test_a_path_through_a_link_out_of_the_package_is_none(package: Path, tmp_path: Path) -> None:
    (tmp_path / "elsewhere").mkdir()
    (package / "linked").symlink_to(tmp_path / "elsewhere", target_is_directory=True)

    assert marker_path_in_folder(package, "linked/file.vcf") is None
    assert marker_path_in_folder(package, "snv/family.vcf") == "snv/family.vcf"


# --- files -----------------------------------------------------------------------------------


def _files(entries: list[dict[str, Any]]) -> list[MarkerFile]:
    return [MarkerFile.model_validate(entry) for entry in entries]


def test_a_folder_as_its_marker_lists_it_passes_with_each_files_sha256(package: Path) -> None:
    entries = [_entry(package, "family.ped"), _entry(package, "snv/family.vcf", md5=True)]
    _write(package, {"schema": "coga-ready/1"})
    (package / ".DS_Store").write_bytes(b"browser")
    (package / "snv" / "._family.vcf").write_bytes(b"browser")

    check = check_marker_files(package, _files(entries), checksums=True)

    assert (check.issues, check.incomplete, check.unlisted) == ([], False, [])
    assert check.sha256 == {
        "family.ped": hashlib.sha256((package / "family.ped").read_bytes()).hexdigest(),
        "snv/family.vcf": hashlib.sha256((package / "snv" / "family.vcf").read_bytes()).hexdigest(),
    }


def test_a_file_missing_or_of_another_size_means_the_copy_runs_still(package: Path) -> None:
    entries = [_entry(package, "family.ped"), _entry(package, "snv/family.vcf")]
    entries[1]["size"] += 10
    entries.append({"path": "snv/family.vcf.tbi", "size": 4, "sha256": "0" * 64})

    check = check_marker_files(package, _files(entries), checksums=False)

    assert check.incomplete is True
    assert [(issue.code, issue.path) for issue in check.issues] == [
        ("file_size_differs", "snv/family.vcf"),
        ("file_missing", "snv/family.vcf.tbi"),
    ]
    assert check.sha256 == {}


@pytest.mark.parametrize("md5", [False, True])
def test_a_checksum_that_differs_is_a_copy_the_pipeline_did_not_make(package: Path, md5: bool) -> None:
    entries = [_entry(package, "family.ped", md5=md5)]
    (package / "family.ped").write_text("FAM1\tPROBAND\t0\t0\t2\t2\n", encoding="utf-8")

    check = check_marker_files(package, _files(entries), checksums=True)

    assert check.incomplete is False
    assert [(issue.code, issue.path) for issue in check.issues] == [("file_checksum_differs", "family.ped")]
    assert check.unlisted == ["snv/family.vcf"]


def test_a_path_out_of_the_folder_twice_listed_or_without_a_checksum_is_an_issue(package: Path) -> None:
    entries = [
        {"path": "../outside.vcf", "size": 1, "sha256": "0" * 64},
        _entry(package, "family.ped"),
        _entry(package, "family.ped"),
        {"path": "snv/family.vcf", "size": (package / "snv" / "family.vcf").stat().st_size},
    ]

    check = check_marker_files(package, _files(entries), checksums=False)

    assert _codes(check.issues) == ["marker_path_outside", "marker_path_twice", "marker_checksum_missing"]


def test_a_listed_link_or_folder_is_no_file(package: Path) -> None:
    (package / "linked.ped").symlink_to("family.ped")

    check = check_marker_files(
        package,
        _files([{"path": "linked.ped", "size": 1, "sha256": "0" * 64}, {"path": "snv", "size": 1, "sha256": "0" * 64}]),
        checksums=True,
    )

    assert [(issue.code, issue.path) for issue in check.issues] == [
        ("file_not_regular", "linked.ped"),
        ("file_not_regular", "snv"),
    ]


def test_a_listed_file_that_cannot_be_read_is_told(package: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entries = [_entry(package, "family.ped")]

    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(marker_module, "_checksums", refuse)

    check = check_marker_files(package, _files(entries), checksums=True)

    assert _codes(check.issues) == ["file_unreadable"]


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root lists a folder without permission")
def test_a_subfolder_that_cannot_be_listed_hides_no_issue(package: Path) -> None:
    entries = [_entry(package, "family.ped")]
    os.chmod(package / "snv", 0)
    try:
        check = check_marker_files(package, _files(entries), checksums=True)
    finally:
        os.chmod(package / "snv", 0o755)

    assert (check.issues, check.unlisted) == ([], [])


def test_a_folder_too_large_to_be_a_package_is_refused(package: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(marker_module, "PACKAGE_FINGERPRINT_MAX_ENTRIES", 1)

    with pytest.raises(marker_module.PackageFolderTooLarge):
        check_marker_files(package, [], checksums=False)
