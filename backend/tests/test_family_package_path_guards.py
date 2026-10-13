"""Package paths: a manifest through a symlink, ``~`` in a manifest path, and symlink loops.

A package's ``manifest.yaml`` (or ``.yml``, ``.json``) could be a symlink that leads out of the
package folder. Discover (``_existing_manifest_dict``) and validation (``_parse_manifest``) read
it as the package's manifest, so any YAML or JSON file the backend can read gave the package
its family and its files; the package list showed its family and analysis type; and Write
manifest wrote through the link, replacing the file it pointed at, or creating the one a
dangling link named. A manifest path starting with ``~`` was expanded: ``~/x.vcf`` named a file
in the backend's home folder, and ``~nouser/x.vcf`` failed validation and Discover with a
RuntimeError (a 500), as did a package folder ``~nouser/FAM1``. A symlink loop failed them too.
On Python 3.12, which CI and the image run, ``Path.resolve`` raises RuntimeError on one: a 500
for a package folder, a manifest path or a file Discover finds by a pattern that loops. From
3.13 it does not, but Discover read a looping PED it found by listing, and Write manifest wrote
through a looping manifest.yaml (OSError, a 500), and a long-read package with a looping
per-sample entry failed Discover and the package list.

Now a manifest name that is a symlink out of the package, or one that loops, is refused and
nothing is read or written through it (``manifest_outside_package``; Write manifest 400); a
manifest path starting with ``~`` is refused (400) and never expanded; a package folder whose
path names an unknown home folder or loops is refused (``package_folder_not_allowed``, 400); a
manifest path through a loop is refused (400); what Discover looks for counts as absent when
it loops. The guards look a resolved path up to find a loop, so every Python answers alike:
each loop test runs with this Python's ``Path.resolve`` and with 3.12's, emulated here when
the tests run on 3.13. A symlink that stays in the package is still followed.

All packages, IDs and files are synthetic.
"""

from __future__ import annotations

import builtins
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import gzip
import io
import json
import os
from pathlib import Path
from typing import Any

from fastapi import HTTPException
import pytest
import yaml

from backend.app.core.config import settings
from backend.app.schemas import FamilyPackageManifestBuildOut, FamilyPackageManifestBuildRequest
from backend.app.services.family_package_common import _package_path_or_none, _resolve_package_path, package_path_within
from backend.app.services.family_package_discovery import discover_family_package_manifest, write_family_package_manifest
from backend.app.services.family_package_source import scan_family_import_packages
from backend.app.services.family_package_validation import load_validated_family_package, validate_family_package

FAMILY = "FAM1"
TRIO_PED = f"{FAMILY} F1 0 0 1 1\n{FAMILY} M1 0 0 2 1\n{FAMILY} C1 F1 M1 1 2\n"
# A manifest outside the package, for another family: nothing of it may reach an answer.
DECOY_FAMILY, DECOY_TYPE = "DECOYFAM", "decoy_analysis"
# A home folder no machine has.
UNKNOWN_HOME = "~coga-no-such-user"
MANIFEST_YAML = f"schema_version: 1\nfamily_id: {FAMILY}\nped: family.ped\n"
LOOP = "runs through a symlink loop"


def _manifest_link_message(name: str) -> str:
    return (
        f"{name} is a symlink that leads out of the package folder, or one that loops: CoGA "
        "reads and writes a package's manifest only in the package folder. Put the manifest "
        "itself in the folder."
    )


def _tilde_message(value: str) -> str:
    return (
        f"Package manifest path '{value}' starts with '~': a manifest path is relative to the "
        "package directory, and CoGA does not read a home folder"
    )


@pytest.fixture
def imports(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """The authorised import root; ``outside/`` beside it is not under it."""
    root = tmp_path / "imports"
    root.mkdir()
    (tmp_path / "outside").mkdir()
    monkeypatch.setattr(settings, "family_import_roots", [str(root)])
    return root


def _python312_resolve(self: Path, strict: bool = False) -> Path:
    """``Path.resolve`` as Python 3.12 has it: a symlink loop raises RuntimeError, where 3.13
    returns the looping part unresolved."""
    try:
        resolved = os.path.realpath(self, strict=strict)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise RuntimeError(f"Symlink loop from {exc.filename!r}") from None
        raise
    path = type(self)(resolved)
    if not strict:
        try:
            path.stat()
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise RuntimeError(f"Symlink loop from {exc.filename!r}") from None
    return path


@pytest.fixture(params=["native", "python3.12"])
def resolve_as(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """A loop test runs with this Python's ``Path.resolve``, and with Python 3.12's."""
    if request.param == "python3.12":
        monkeypatch.setattr(Path, "resolve", _python312_resolve)
    return str(request.param)


def _package(
    imports: Path, name: str = FAMILY, *, ped: str | None = TRIO_PED, manifest: dict[str, Any] | None = None
) -> Path:
    package = imports / name
    package.mkdir()
    if ped is not None:
        (package / "family.ped").write_text(ped, encoding="utf-8")
    if manifest is not None:
        payload = {"schema_version": 1, "family_id": FAMILY, "ped": "family.ped", **manifest}
        (package / "manifest.yaml").write_text(yaml.safe_dump(payload), encoding="utf-8")
    return package


def _loop(at: Path) -> Path:
    """A symlink at ``at`` that loops, through a second one beside it."""
    at.parent.mkdir(parents=True, exist_ok=True)
    other = at.with_name(f"{at.name}.loop")
    at.symlink_to(other.name)
    other.symlink_to(at.name)
    return at


def _decoy_manifest(tmp_path: Path, name: str = "decoy.yaml") -> Path:
    """A valid manifest outside the package, for another family."""
    payload = {"schema_version": 1, "family_id": DECOY_FAMILY, "ped": "family.ped", "analysis_type": DECOY_TYPE}
    decoy = tmp_path / "outside" / name
    decoy.write_text(json.dumps(payload) if name.endswith(".json") else yaml.safe_dump(payload), encoding="utf-8")
    return decoy


@contextmanager
def _opened(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """The files opened in the block, each as the file it reaches (its symlinks resolved)."""
    opened: list[str] = []
    real_open = builtins.open

    def spy(file: Any, *args: Any, **kwargs: Any) -> Any:
        if not isinstance(file, int):
            opened.append(os.path.realpath(os.fspath(file)))
        return real_open(file, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", spy)
        patch.setattr(io, "open", spy)
        yield opened


def _discover(folder: Path | str, **request: Any) -> FamilyPackageManifestBuildOut:
    return discover_family_package_manifest(FamilyPackageManifestBuildRequest(folder_path=str(folder), **request))


def _write(folder: Path | str, *, overwrite: bool = True) -> Any:
    return write_family_package_manifest(folder_path=folder, manifest_yaml=MANIFEST_YAML, overwrite=overwrite)


def _codes(issues: Any) -> list[str]:
    return [issue.code for issue in issues]


def _dataset(answer: FamilyPackageManifestBuildOut, dataset_type: str) -> Any:
    return next(item for item in answer.datasets if item.dataset_type == dataset_type)


# --------------------------------------------------------------------------- #
# A manifest that is a symlink out of the package
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", ["manifest.yaml", "manifest.yml", "manifest.json"])
def test_validation_reads_no_manifest_through_a_symlink_out_of_the_package(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, imports: Path, name: str
) -> None:
    decoy = _decoy_manifest(tmp_path, "decoy.json" if name.endswith(".json") else "decoy.yaml")
    package = _package(imports)
    (package / name).symlink_to(decoy)

    with _opened(monkeypatch) as opened:
        validation, bundle = load_validated_family_package(package)

    # The decoy was read as the package's manifest: the package took its family.
    assert os.path.realpath(decoy) not in opened
    assert bundle is None
    assert [(issue.code, issue.message, issue.path) for issue in validation.errors] == [
        ("manifest_outside_package", _manifest_link_message(name), str(package.resolve() / name))
    ]
    assert (validation.family_id, validation.manifest_path, validation.sample_ids) == (None, None, [])
    assert DECOY_FAMILY not in validation.model_dump_json()


def test_discover_reads_no_manifest_through_a_symlink_out_of_the_package(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, imports: Path
) -> None:
    decoy = _decoy_manifest(tmp_path)
    package = _package(imports)
    (package / "manifest.yaml").symlink_to(decoy)

    with _opened(monkeypatch) as opened:
        answer = _discover(package)

    # Discover took the decoy's family for the package's, and drafted from it.
    assert os.path.realpath(decoy) not in opened
    assert (answer.valid, answer.family_id, answer.manifest_yaml, answer.datasets) == (False, None, "", [])
    assert [(issue.code, issue.message) for issue in answer.errors] == [
        ("manifest_outside_package", _manifest_link_message("manifest.yaml"))
    ]
    assert DECOY_FAMILY not in answer.model_dump_json()


@pytest.mark.parametrize("overwrite", [True, False])
def test_write_manifest_writes_nothing_through_a_symlink_out_of_the_package(
    tmp_path: Path, imports: Path, overwrite: bool
) -> None:
    decoy = _decoy_manifest(tmp_path)
    before = decoy.read_bytes()
    package = _package(imports)
    (package / "manifest.yaml").symlink_to(decoy)

    with pytest.raises(HTTPException) as refused:
        _write(package, overwrite=overwrite)

    # The decoy was replaced by the package's manifest (without overwrite, a 409 said it
    # existed).
    assert (refused.value.status_code, refused.value.detail) == (400, _manifest_link_message("manifest.yaml"))
    assert decoy.read_bytes() == before
    assert (package / "manifest.yaml").is_symlink()


def test_a_dangling_manifest_link_out_of_the_package_is_refused_and_nothing_written_through_it(
    tmp_path: Path, imports: Path
) -> None:
    planted = tmp_path / "outside" / "planted.yaml"
    package = _package(imports)
    (package / "manifest.yaml").symlink_to(planted)

    answer = _discover(package)
    validation, bundle = load_validated_family_package(package)
    with pytest.raises(HTTPException) as refused:
        _write(package, overwrite=False)

    # Discover drafted as for a package without a manifest, and Write manifest then created
    # the file the link named, outside the package.
    assert (_codes(answer.errors), answer.manifest_yaml) == (["manifest_outside_package"], "")
    assert (_codes(validation.errors), bundle) == (["manifest_outside_package"], None)
    assert (refused.value.status_code, refused.value.detail) == (400, _manifest_link_message("manifest.yaml"))
    assert not planted.exists()


def test_the_package_list_reads_no_manifest_through_a_symlink_out_of_the_package(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, imports: Path
) -> None:
    decoy = _decoy_manifest(tmp_path)
    package = _package(imports, "LINKED")
    (package / "manifest.yaml").symlink_to(decoy)

    with _opened(monkeypatch) as opened:
        listed = {item["name"]: item for item in scan_family_import_packages()}

    # It was listed under the decoy's family and analysis type; now as an unreadable
    # manifest is, under its folder's name.
    assert os.path.realpath(decoy) not in opened
    assert (listed["LINKED"]["family_id"], listed["LINKED"]["analysis_type"]) == ("LINKED", None)


def test_a_manifest_symlink_that_stays_in_the_package_is_followed(imports: Path) -> None:
    package = _package(imports)
    current = package / "manifests" / "current.yaml"
    current.parent.mkdir()
    current.write_text(
        yaml.safe_dump({"schema_version": 1, "family_id": FAMILY, "ped": "family.ped", "analysis_type": "trio_check"})
    )
    (package / "manifest.yaml").symlink_to(Path("manifests") / "current.yaml")

    validation, bundle = load_validated_family_package(package)
    answer = _discover(package)
    listed = {item["name"]: item for item in scan_family_import_packages()}
    written = write_family_package_manifest(
        folder_path=package, manifest_yaml=MANIFEST_YAML + "notes: rewritten\n", overwrite=True
    )

    assert validation.valid, validation.errors
    assert bundle is not None and bundle.manifest.analysis_type == "trio_check"
    assert yaml.safe_load(answer.manifest_yaml)["analysis_type"] == "trio_check"
    assert listed[FAMILY]["analysis_type"] == "trio_check"
    # Written through the link, into the package's own file.
    assert written.validation.valid
    assert yaml.safe_load(current.read_text())["notes"] == "rewritten"
    assert (package / "manifest.yaml").is_symlink()


# --------------------------------------------------------------------------- #
# A manifest path that starts with ~
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("value", ["~/x.vcf", f"{UNKNOWN_HOME}/x.vcf", "~", "  ~/x.vcf"])
def test_a_manifest_path_starting_with_a_tilde_is_refused_not_expanded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "x.vcf").write_text("a file in the backend's home folder")
    monkeypatch.setenv("HOME", str(home))
    root = tmp_path / "PKG"
    root.mkdir()

    with pytest.raises(HTTPException) as refused:
        _resolve_package_path(root, value)

    # `~/x.vcf` named the home folder's file (then refused for leaving the package), and an
    # unknown user's `~` raised a RuntimeError (a 500).
    assert (refused.value.status_code, refused.value.detail) == (400, _tilde_message(value.strip()))
    assert _package_path_or_none(root, value) is None


def test_a_tilde_after_the_start_of_a_manifest_path_is_part_of_the_name(tmp_path: Path) -> None:
    root = tmp_path / "PKG"
    (root / "snv").mkdir(parents=True)

    for value in ("snv/~x.vcf", "snv/x~.vcf", "a~/b.vcf", "./~/x.vcf"):
        assert _resolve_package_path(root, value) == Path(os.path.normpath(root.resolve() / value)), value


@pytest.mark.parametrize(
    "manifest",
    [
        {"ped": f"{UNKNOWN_HOME}/family.ped"},
        {"datasets": {"snv": {"family_vcf": f"{UNKNOWN_HOME}/x.vcf"}}},
        {"phenotypes": {"file": f"{UNKNOWN_HOME}/x.tsv"}},
    ],
    ids=["ped", "dataset", "phenotypes"],
)
def test_validation_refuses_a_manifest_path_starting_with_a_tilde(imports: Path, manifest: dict[str, Any]) -> None:
    package = _package(imports, manifest=manifest)

    with pytest.raises(HTTPException) as refused:
        validate_family_package(package)

    # Path.expanduser raised a RuntimeError for the unknown user: a 500.
    assert refused.value.status_code == 400
    assert refused.value.detail.startswith(f"Package manifest path '{UNKNOWN_HOME}/")
    assert refused.value.detail.endswith("starts with '~': a manifest path is relative to the package directory, and CoGA does not read a home folder")


def test_discover_answers_for_a_manifest_naming_a_path_that_starts_with_a_tilde(imports: Path) -> None:
    package = _package(imports, manifest={"datasets": {"snv": {"family_vcf": f"{UNKNOWN_HOME}/x.vcf.gz"}}})

    answer = _discover(package)

    # Discover's availability check expanded the `~` and failed with a RuntimeError (a 500).
    assert answer.manifest_yaml
    assert [(item.path, item.exists) for item in _dataset(answer, "snv").files] == [(f"{UNKNOWN_HOME}/x.vcf.gz", False)]


def test_discover_refuses_a_requested_ped_path_that_starts_with_a_tilde(imports: Path) -> None:
    package = _package(imports)

    with pytest.raises(HTTPException) as refused:
        _discover(package, ped_path=f"{UNKNOWN_HOME}/family.ped")

    # A RuntimeError: a 500.
    assert (refused.value.status_code, refused.value.detail) == (400, _tilde_message(f"{UNKNOWN_HOME}/family.ped"))


def test_a_package_folder_in_a_home_folder_the_server_lacks_is_refused(imports: Path) -> None:
    folder = f"{UNKNOWN_HOME}/{FAMILY}"

    answer = _discover(folder)
    validation, bundle = load_validated_family_package(folder)
    with pytest.raises(HTTPException) as refused:
        _write(folder)

    # Path.expanduser raised a RuntimeError: Discover, validation and Write manifest failed (500).
    message = f"Family import path '{folder}' starts with '~', and the server has no such home folder"
    assert [(issue.code, issue.message, issue.path) for issue in answer.errors] == [
        ("package_folder_not_allowed", message, folder)
    ]
    assert answer.manifest_path == f"{folder}/manifest.yaml"
    assert [(issue.code, issue.message, issue.path) for issue in validation.errors] == [
        ("package_folder_not_allowed", message, folder)
    ]
    assert bundle is None
    assert (refused.value.status_code, refused.value.detail) == (400, message)


# --------------------------------------------------------------------------- #
# Symlink loops
# --------------------------------------------------------------------------- #


def test_a_package_folder_that_loops_is_refused(imports: Path, resolve_as: str) -> None:
    folder = _loop(imports / "LOOPFAM")

    answer = _discover(folder)
    validation, bundle = load_validated_family_package(folder)
    with pytest.raises(HTTPException) as refused:
        _write(folder)

    # Python 3.12 raised RuntimeError (a 500); 3.13 said the folder does not exist.
    message = f"Family import path '{folder}' {LOOP}"
    assert [(issue.code, issue.message) for issue in answer.errors] == [("package_folder_not_allowed", message)]
    assert [(issue.code, issue.message) for issue in validation.errors] == [("package_folder_not_allowed", message)]
    assert bundle is None
    assert (refused.value.status_code, refused.value.detail) == (400, message)


@pytest.mark.parametrize("looping", ["a folder on the path", "the file itself"])
@pytest.mark.parametrize("where", ["ped", "dataset", "phenotypes"])
def test_validation_refuses_a_manifest_path_through_a_symlink_loop(
    imports: Path, resolve_as: str, where: str, looping: str
) -> None:
    value = "loop/x" if looping == "a folder on the path" else "loop"
    manifests: dict[str, dict[str, Any]] = {
        "ped": {"ped": value},
        "dataset": {"datasets": {"snv": {"family_vcf": value}}},
        "phenotypes": {"phenotypes": {"file": value}},
    }
    package = _package(imports, manifest=manifests[where])
    _loop(package / "loop")

    with pytest.raises(HTTPException) as refused:
        validate_family_package(package)

    # Python 3.12 raised RuntimeError (a 500); 3.13 reported a missing file.
    assert (refused.value.status_code, refused.value.detail) == (400, f"Package manifest path '{value}' {LOOP}")


def test_a_manifest_that_loops_is_refused(imports: Path, resolve_as: str) -> None:
    package = _package(imports)
    _loop(package / "manifest.yaml")

    answer = _discover(package)
    validation, bundle = load_validated_family_package(package)
    with pytest.raises(HTTPException) as refused:
        _write(package)

    # Discover drafted as for a package without a manifest, validation said it had none,
    # and Write manifest failed with an OSError (a 500).
    assert (_codes(answer.errors), answer.manifest_yaml) == (["manifest_outside_package"], "")
    assert (_codes(validation.errors), bundle) == (["manifest_outside_package"], None)
    assert (refused.value.status_code, refused.value.detail) == (400, _manifest_link_message("manifest.yaml"))
    assert (package / "manifest.yaml").is_symlink()


def test_a_ped_that_loops_counts_as_absent(imports: Path, resolve_as: str) -> None:
    package = _package(imports, ped=None)
    _loop(package / "family.ped")

    answer = _discover(package, family_id=FAMILY)

    # Found by listing and read as the PED: an OSError (a 500) on Python 3.13.
    assert _codes(answer.errors) == ["ped_file_missing"]
    assert answer.sample_ids == []


def test_a_file_discover_finds_by_a_pattern_that_loops_counts_as_absent(imports: Path, resolve_as: str) -> None:
    package = _package(imports, ped=f"{FAMILY} S1 0 0 1 2\n")
    _loop(package / "sv" / "S1" / "needlr" / "S1_sv_phased.needLR.4.0.vcf.gz")

    answer = _discover(package, family_id=FAMILY)

    # Put back through the package guard: a RuntimeError (a 500) on Python 3.12.
    sv = _dataset(answer, "sv_needlr")
    assert not sv.complete
    assert not any(item.exists for item in sv.files)


def test_discover_answers_for_a_manifest_naming_a_path_through_a_loop(imports: Path, resolve_as: str) -> None:
    package = _package(imports, manifest={"datasets": {"snv": {"family_vcf": "loop/x.vcf.gz"}}})
    _loop(package / "loop")

    answer = _discover(package)

    # Its availability check raised RuntimeError (a 500) on Python 3.12.
    assert answer.manifest_yaml
    assert [(item.path, item.exists) for item in _dataset(answer, "snv").files] == [("loop/x.vcf.gz", False)]


def test_discover_refuses_a_requested_ped_path_through_a_loop(imports: Path, resolve_as: str) -> None:
    package = _package(imports)
    _loop(package / "loop")

    with pytest.raises(HTTPException) as refused:
        _discover(package, ped_path="loop/family.ped")

    # Python 3.12 raised RuntimeError (a 500); 3.13 said the PED does not exist.
    assert (refused.value.status_code, refused.value.detail) == (400, f"Package manifest path 'loop/family.ped' {LOOP}")


def _long_read_file(path: Path, sample_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt") as handle:
        handle.write(f"##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample_id}\n")


def test_a_long_read_entry_that_loops_is_no_sample(imports: Path, resolve_as: str) -> None:
    package = imports / "COUPLE1"
    _long_read_file(package / "snv" / "MOTHER1" / "MOTHER1_annot.vcf.gz", "MOTHER1")
    # A looping entry named after its sample, beside the folder that holds its file.
    _long_read_file(package / "snv" / "FATHER1" / "annotation" / "FATHER1_annot.vcf.gz", "FATHER1")
    _loop(package / "snv" / "FATHER1" / "FATHER1_old.vcf.gz")
    # A looping entry where a sample's folder would be.
    _loop(package / "snv" / "GHOST1")

    answer = _discover(package)
    listed = scan_family_import_packages()

    # Looked up, the loop raised OSError: Discover and the whole package list failed (500).
    assert answer.sample_ids == ["FATHER1", "MOTHER1"]
    assert [item["name"] for item in listed] == ["COUPLE1"]


def test_a_loop_that_resolve_reports_refuses_the_package_folder(monkeypatch: pytest.MonkeyPatch, imports: Path) -> None:
    package = _package(imports)
    real_resolve = Path.resolve
    for error in (RuntimeError("Symlink loop from 'x'"), OSError(errno.ELOOP, "Too many levels of symbolic links")):

        def resolve(self: Path, strict: bool = False, error: BaseException = error) -> Path:
            if self == package:
                raise error
            return real_resolve(self, strict=strict)

        monkeypatch.setattr(Path, "resolve", resolve)
        answer = _discover(package)
        with pytest.raises(HTTPException) as refused:
            _write(package)

        # Python 3.12 raises the RuntimeError, and a strict resolve the OSError: a 500.
        message = f"Family import path '{package}' {LOOP}"
        assert [(issue.code, issue.message) for issue in answer.errors] == [("package_folder_not_allowed", message)]
        assert (refused.value.status_code, refused.value.detail) == (400, message)


def test_another_error_resolve_raises_is_not_taken_for_a_loop(monkeypatch: pytest.MonkeyPatch, imports: Path) -> None:
    package = _package(imports)
    real_resolve = Path.resolve

    def resolve(self: Path, strict: bool = False) -> Path:
        if self == package:
            raise PermissionError(errno.EACCES, "Permission denied")
        return real_resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve)

    with pytest.raises(PermissionError):
        _discover(package)


def test_the_guard_refuses_a_symlink_loop_alike_on_every_python(tmp_path: Path, resolve_as: str) -> None:
    from backend.app.services.family_package_common import runs_through_symlink_loop

    root = tmp_path / "PKG"
    (root / "snv").mkdir(parents=True)
    (root / "snv" / "x.vcf").write_text("x")
    _loop(root / "loop")
    _loop(root / "snv" / "self.vcf")

    for value in ("loop", "loop/x.vcf", "snv/self.vcf", "snv/../loop/y.vcf"):
        with pytest.raises(HTTPException) as refused:
            _resolve_package_path(root, value)
        assert (refused.value.status_code, refused.value.detail) == (400, f"Package manifest path '{value}' {LOOP}")
        assert _package_path_or_none(root, value) is None, value
        assert package_path_within(root, value) is None, value
    # The package's own files resolve as before, and the folder itself is in the package.
    assert _resolve_package_path(root, "snv/x.vcf") == root.resolve() / "snv" / "x.vcf"
    assert _resolve_package_path(root, ".") == root.resolve()
    assert runs_through_symlink_loop(root / "loop/x.vcf")
    assert not runs_through_symlink_loop(root / "snv" / "x.vcf")
    assert not runs_through_symlink_loop(root / "snv" / "missing.vcf")


# --------------------------------------------------------------------------- #
# The routes
# --------------------------------------------------------------------------- #


@contextmanager
def _admin_client() -> Iterator[Any]:
    """The app as an admin sees it, with no database behind it: these requests name no
    family, so none is looked up. A request the app fails on is answered 500, as a server
    answers it."""
    from fastapi.testclient import TestClient

    from backend.app.core.postgres import get_postgres_session
    from backend.app.dependencies import get_current_user
    from backend.app.main import app
    from backend.app.services.access_control import CurrentUser

    original_overrides = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True
    user = CurrentUser(
        id="00000000-0000-0000-0000-000000000001",
        username="admin@example.com",
        email="admin@example.com",
        role="admin",
        created_at=datetime.now(timezone.utc),
    )

    class _Session:
        async def rollback(self) -> None:
            return None

    async def override_get_postgres_session() -> Any:
        yield _Session()

    async def override_get_current_user() -> CurrentUser:
        return user

    app.dependency_overrides[get_postgres_session] = override_get_postgres_session
    app.dependency_overrides[get_current_user] = override_get_current_user
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client
    finally:
        app.dependency_overrides = original_overrides


def test_the_routes_refuse_a_manifest_symlink_out_of_the_package(tmp_path: Path, imports: Path) -> None:
    decoy = _decoy_manifest(tmp_path)
    before = decoy.read_bytes()
    package = _package(imports)
    (package / "manifest.yaml").symlink_to(decoy)

    with _admin_client() as client:
        discover = client.post("/api/family-imports/manifest/discover", json={"folder_path": str(package)})
        validate = client.post("/api/family-imports/validate", json={"folder_path": str(package)})
        write = client.post(
            "/api/family-imports/manifest/write",
            json={"folder_path": str(package), "manifest_yaml": MANIFEST_YAML, "overwrite": True},
        )
        listed = client.get("/api/family-imports/packages")

    for response in (discover, validate):
        assert response.status_code == 200, response.text
        assert [issue["code"] for issue in response.json()["errors"]] == ["manifest_outside_package"]
        assert DECOY_FAMILY not in response.text
    assert (write.status_code, write.json()) == (400, {"detail": _manifest_link_message("manifest.yaml")})
    assert decoy.read_bytes() == before
    assert listed.status_code == 200, listed.text
    assert DECOY_FAMILY not in listed.text


def test_the_routes_refuse_a_tilde_and_a_symlink_loop_without_a_500(imports: Path) -> None:
    tilde = _package(imports, "TILDE", manifest={"datasets": {"snv": {"family_vcf": f"{UNKNOWN_HOME}/x.vcf"}}})
    looping = _package(imports, "LOOPED", manifest={"datasets": {"snv": {"family_vcf": "loop/x.vcf"}}})
    _loop(looping / "loop")
    looping_folder = _loop(imports / "LOOPFOLDER")
    long_read = imports / "COUPLE1"
    _long_read_file(long_read / "snv" / "MOTHER1" / "MOTHER1_annot.vcf.gz", "MOTHER1")
    _loop(long_read / "snv" / "GHOST1")

    with _admin_client() as client:
        validate_tilde = client.post("/api/family-imports/validate", json={"folder_path": str(tilde)})
        validate_loop = client.post("/api/family-imports/validate", json={"folder_path": str(looping)})
        discover_tilde_ped = client.post(
            "/api/family-imports/manifest/discover",
            json={"folder_path": str(tilde), "ped_path": f"{UNKNOWN_HOME}/family.ped"},
        )
        discover_tilde_folder = client.post(
            "/api/family-imports/manifest/discover", json={"folder_path": f"{UNKNOWN_HOME}/{FAMILY}"}
        )
        discover_loop_folder = client.post(
            "/api/family-imports/manifest/discover", json={"folder_path": str(looping_folder)}
        )
        discover_long_read = client.post("/api/family-imports/manifest/discover", json={"folder_path": str(long_read)})
        listed = client.get("/api/family-imports/packages")

    # Each was a 500 (the folder that loops on Python 3.12 only; the manifest path through a
    # loop a 500 there, a missing file on 3.13); the package list failed whole on the looping
    # long-read entry.
    for response, detail in (
        (validate_tilde, _tilde_message(f"{UNKNOWN_HOME}/x.vcf")),
        (validate_loop, f"Package manifest path 'loop/x.vcf' {LOOP}"),
        (discover_tilde_ped, _tilde_message(f"{UNKNOWN_HOME}/family.ped")),
    ):
        assert response.status_code == 400, response.text
        assert response.json() == {"detail": detail}
    for response, message in (
        (discover_tilde_folder, f"Family import path '{UNKNOWN_HOME}/{FAMILY}' starts with '~', and the server has no such home folder"),
        (discover_loop_folder, f"Family import path '{looping_folder}' {LOOP}"),
    ):
        assert response.status_code == 200, response.text
        assert [(issue["code"], issue["message"]) for issue in response.json()["errors"]] == [
            ("package_folder_not_allowed", message)
        ]
    assert discover_long_read.status_code == 200, discover_long_read.text
    assert discover_long_read.json()["sample_ids"] == ["MOTHER1"]
    assert listed.status_code == 200, listed.text
    assert {item["name"] for item in listed.json()} >= {"TILDE", "LOOPED", "COUPLE1"}
