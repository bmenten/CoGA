"""Package paths: a manifest file in the folder, ``~`` in a manifest path, symlink loops, a NUL.

The import reads the manifest CoGA keeps for its job, never a file of the package folder: a
``manifest.yaml`` (or ``.yml``, ``.json``) there that is a symlink out of the package, or one
that loops, is not opened, and the package takes nothing from it. A manifest that names a PED
is refused (``manifest_names_a_ped``) and the path it names is never resolved. A path the
manifest names is read relative to the package folder: one starting with ``~`` is refused (400)
and never expanded, as is one through a symlink loop or one holding a NUL. A package folder
whose path names an unknown home folder, loops or holds a NUL is refused
(``package_folder_not_allowed``); what Discover looks for counts as absent when it loops. The
guards look a resolved path up to find a loop, so every Python answers alike: each loop test
runs with this Python's ``Path.resolve`` and with 3.12's, which raises RuntimeError on a loop,
emulated here when the tests run on 3.13. A symlink that stays in the package is followed.

The package list checks each folder with selection's guard before it looks anything up in it,
and leaves out one selection refuses (a symlink out of the import roots, or one that loops); a
symlink that stays under the roots is followed, so every folder the list shows is one selection
takes.

All packages, IDs and files are synthetic.
"""

from __future__ import annotations

import builtins
from collections.abc import Iterator
from contextlib import contextmanager
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
from backend.app.services.family_package_common import NUL_IN_PATH, _package_path_or_none, _resolve_package_path, package_path_within
from backend.app.services.family_package_discovery import discover_family_package_manifest
from backend.app.services.family_package_source import scan_family_import_packages
from backend.app.services.family_package_validation import load_validated_family_package
from backend.tests._kept_manifest import kept_manifest, manifest_with_members, validated
from backend.tests._family_import_fakes import authorize_import_root, signed_in_client

FAMILY = "FAM1"
TRIO_PED = f"{FAMILY} F1 0 0 1 1\n{FAMILY} M1 0 0 2 1\n{FAMILY} C1 F1 M1 1 2\n"
# A manifest outside the package, for another family: nothing of it may reach an answer.
DECOY_FAMILY, DECOY_TYPE = "DECOYFAM", "decoy_analysis"
# A home folder no machine has.
UNKNOWN_HOME = "~coga-no-such-user"
LOOP = "runs through a symlink loop"
# The manifest CoGA keeps for an import of FAM1, whose members are the trio of TRIO_PED.
KEPT_MANIFEST = json.dumps(manifest_with_members({"schema_version": 1, "family_id": FAMILY}, TRIO_PED))


def _tilde_message(value: str) -> str:
    return (
        f"Package manifest path '{value}' starts with '~': a manifest path is relative to the "
        "package directory, and CoGA does not read a home folder"
    )


@pytest.fixture
def imports(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """The authorised import root; ``outside/`` beside it is not under it."""
    return authorize_import_root(monkeypatch, tmp_path)


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


def _codes(issues: Any) -> list[str]:
    return [issue.code for issue in issues]


def _dataset(answer: FamilyPackageManifestBuildOut, dataset_type: str) -> Any:
    return next(item for item in answer.datasets if item.dataset_type == dataset_type)


# --------------------------------------------------------------------------- #
# A manifest file in the package folder
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("link", ["out of the package", "a loop"])
@pytest.mark.parametrize("name", ["manifest.yaml", "manifest.yml", "manifest.json"])
def test_validation_opens_no_manifest_file_of_the_package_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, imports: Path, resolve_as: str, name: str, link: str
) -> None:
    decoy = _decoy_manifest(tmp_path, "decoy.json" if name.endswith(".json") else "decoy.yaml")
    package = _package(imports)
    if link == "a loop":
        _loop(package / name)
    else:
        (package / name).symlink_to(decoy)

    with _opened(monkeypatch) as opened:
        validation, bundle = load_validated_family_package(package, manifest_text=KEPT_MANIFEST)

    # The kept manifest is the package's: the link in the folder is neither opened nor refused.
    assert [path for path in opened if path in {os.path.realpath(decoy), os.path.realpath(package / name)}] == []
    assert validation.valid, validation.errors
    assert bundle is not None and bundle.kept_manifest_text == KEPT_MANIFEST
    assert (validation.family_id, validation.sample_ids) == (FAMILY, ["F1", "M1", "C1"])
    assert DECOY_FAMILY not in validation.model_dump_json()


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

    # Never expanded: `~/x.vcf` would name a file in the home folder, and an unknown user's `~`
    # makes Path.expanduser raise RuntimeError.
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
        {"datasets": {"snv": {"family_vcf": f"{UNKNOWN_HOME}/x.vcf"}}},
        {"phenotypes": {"file": f"{UNKNOWN_HOME}/x.tsv"}},
    ],
    ids=["dataset", "phenotypes"],
)
def test_validation_refuses_a_manifest_path_starting_with_a_tilde(imports: Path, manifest: dict[str, Any]) -> None:
    package = _package(imports, manifest=manifest)

    with pytest.raises(HTTPException) as refused:
        validated(package)

    # Refused before Path.expanduser, which raises RuntimeError for an unknown user.
    assert refused.value.status_code == 400
    assert refused.value.detail.startswith(f"Package manifest path '{UNKNOWN_HOME}/")
    assert refused.value.detail.endswith("starts with '~': a manifest path is relative to the package directory, and CoGA does not read a home folder")


def test_a_package_folder_in_a_home_folder_the_server_lacks_is_refused(imports: Path) -> None:
    folder = f"{UNKNOWN_HOME}/{FAMILY}"

    answer = _discover(folder)
    validation, bundle = load_validated_family_package(folder, manifest_text=KEPT_MANIFEST)
    # Refused before Path.expanduser, which raises RuntimeError for an unknown user.
    message = f"Family import path '{folder}' starts with '~', and the server has no such home folder"
    assert [(issue.code, issue.message, issue.path) for issue in answer.errors] == [
        ("package_folder_not_allowed", message, folder)
    ]
    assert [(issue.code, issue.message, issue.path) for issue in validation.errors] == [
        ("package_folder_not_allowed", message, folder)
    ]
    assert bundle is None


# --------------------------------------------------------------------------- #
# Symlink loops
# --------------------------------------------------------------------------- #


def test_a_package_folder_that_loops_is_refused(imports: Path, resolve_as: str) -> None:
    folder = _loop(imports / "LOOPFAM")

    answer = _discover(folder)
    validation, bundle = load_validated_family_package(folder, manifest_text=KEPT_MANIFEST)

    # Refused alike whether Path.resolve raises RuntimeError on the loop (3.12) or returns it
    # unresolved (3.13).
    message = f"Family import path '{folder}' {LOOP}"
    assert [(issue.code, issue.message) for issue in answer.errors] == [("package_folder_not_allowed", message)]
    assert [(issue.code, issue.message) for issue in validation.errors] == [("package_folder_not_allowed", message)]
    assert bundle is None


@pytest.mark.parametrize("looping", ["a folder on the path", "the file itself"])
@pytest.mark.parametrize("where", ["dataset", "phenotypes"])
def test_validation_refuses_a_manifest_path_through_a_symlink_loop(
    imports: Path, resolve_as: str, where: str, looping: str
) -> None:
    value = "loop/x" if looping == "a folder on the path" else "loop"
    manifests: dict[str, dict[str, Any]] = {
        "dataset": {"datasets": {"snv": {"family_vcf": value}}},
        "phenotypes": {"phenotypes": {"file": value}},
    }
    package = _package(imports, manifest=manifests[where])
    _loop(package / "loop")

    with pytest.raises(HTTPException) as refused:
        validated(package)

    # Refused alike whether Path.resolve raises RuntimeError on the loop (3.12) or returns it
    # unresolved (3.13).
    assert (refused.value.status_code, refused.value.detail) == (400, f"Package manifest path '{value}' {LOOP}")


@pytest.mark.parametrize("value", [f"{UNKNOWN_HOME}/family.ped", "loop/x", "loop", "family\x00.ped"])
def test_a_ped_the_manifest_names_is_refused_and_never_resolved(
    monkeypatch: pytest.MonkeyPatch, imports: Path, resolve_as: str, value: str
) -> None:
    package = _package(imports)
    _loop(package / "loop")
    kept = {**json.loads(KEPT_MANIFEST), "ped": value}

    with _opened(monkeypatch) as opened:
        validation, bundle = load_validated_family_package(package, manifest_text=json.dumps(kept))

    # The import reads no PED: the manifest is refused for naming one, whatever its path.
    assert (_codes(validation.errors), bundle) == (["manifest_names_a_ped"], None)
    assert [path for path in opened if _within(path, package.resolve())] == []


def test_a_ped_that_loops_counts_as_absent(imports: Path, resolve_as: str) -> None:
    package = _package(imports, ped=None)
    _loop(package / "family.ped")

    answer = _discover(package, family_id=FAMILY)

    # Found by listing, the loop is not read as the PED (reading it raises OSError).
    assert _codes(answer.errors) == ["ped_file_missing"]
    assert answer.sample_ids == []


def test_a_folder_named_like_a_ped_counts_as_absent(imports: Path) -> None:
    # Found by listing *.ped, a folder is not read as the PED (reading it raises
    # IsADirectoryError).
    package = _package(imports, ped=None)
    (package / "family.ped").mkdir()
    (package / "ped").mkdir()
    (package / "ped" / "other.ped").mkdir()

    answer = _discover(package, family_id=FAMILY)

    assert _codes(answer.errors) == ["ped_file_missing"]
    assert answer.sample_ids == []
    # Nor does the package list count one: with no manifest, the folder is no package.
    assert scan_family_import_packages() == []


def test_a_ped_beside_a_folder_named_like_one_is_read(imports: Path) -> None:
    package = _package(imports, ped=None)
    (package / "a.ped").mkdir()
    (package / "family.ped").write_text(TRIO_PED, encoding="utf-8")

    answer = _discover(package, family_id=FAMILY)

    assert not _codes(answer.errors)
    assert answer.sample_ids


def test_a_file_discover_finds_by_a_pattern_that_loops_counts_as_absent(imports: Path, resolve_as: str) -> None:
    package = _package(imports, ped=f"{FAMILY} S1 0 0 1 2\n")
    _loop(package / "sv" / "S1" / "needlr" / "S1_sv_phased.needLR.4.0.vcf.gz")

    answer = _discover(package, family_id=FAMILY)

    # Put through the package guard, which refuses the loop alike on every Python.
    sv = _dataset(answer, "sv_needlr")
    assert not sv.complete
    assert not any(item.exists for item in sv.files)


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

    # The loops are no samples, and the package is listed (looking a loop up raises OSError).
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

        # Python 3.12 raises the RuntimeError, and a strict resolve the OSError: a 500.
        message = f"Family import path '{package}' {LOOP}"
        assert [(issue.code, issue.message) for issue in answer.errors] == [("package_folder_not_allowed", message)]


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
    # The package's own files resolve, and the folder itself is in the package.
    assert _resolve_package_path(root, "snv/x.vcf") == root.resolve() / "snv" / "x.vcf"
    assert _resolve_package_path(root, ".") == root.resolve()
    assert runs_through_symlink_loop(root / "loop/x.vcf")
    assert not runs_through_symlink_loop(root / "snv" / "x.vcf")
    assert not runs_through_symlink_loop(root / "snv" / "missing.vcf")


# --------------------------------------------------------------------------- #
# The package list: a package folder that is a symlink out of the import root
# --------------------------------------------------------------------------- #


def _foreign_package(folder: Path) -> Path:
    """A package at ``folder``, outside the import root, for another family: its manifest and
    its PED. Nothing of it may reach the package list."""
    folder.mkdir(parents=True)
    payload = {"schema_version": 1, "family_id": DECOY_FAMILY, "ped": "family.ped", "analysis_type": DECOY_TYPE}
    (folder / "manifest.yaml").write_text(yaml.safe_dump(payload), encoding="utf-8")
    (folder / "family.ped").write_text(f"{DECOY_FAMILY} D1 0 0 1 2\n", encoding="utf-8")
    return folder


@contextmanager
def _looked_up(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """The paths looked up in the block: each folder listed, and each name asked about
    (``is_dir``, ``is_file``, ``exists``), as the path it reaches (its symlinks resolved).
    Resolving a path, which a guard does to tell where a symlink leads, is not counted."""
    looked: list[str] = []

    def spied(original: Any) -> Any:
        def spy(*args: Any, **kwargs: Any) -> Any:
            if args and not isinstance(args[0], int):
                looked.append(os.path.realpath(os.fspath(args[0])))
            return original(*args, **kwargs)

        return spy

    with monkeypatch.context() as patch:
        for name in ("is_dir", "is_file", "exists", "iterdir", "glob"):
            patch.setattr(Path, name, spied(getattr(Path, name)))
        for name in ("scandir", "listdir"):
            patch.setattr(os, name, spied(getattr(os, name)))
        yield looked


def _within(path: str, folder: Path) -> bool:
    return path == str(folder) or path.startswith(f"{folder}{os.sep}")


def test_the_package_list_leaves_out_a_package_folder_linked_out_of_the_import_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, imports: Path
) -> None:
    outside = _foreign_package(tmp_path / "outside" / "OUTFAM").resolve()
    (imports / "LINKED").symlink_to(outside)
    _package(imports)

    with _looked_up(monkeypatch) as looked, _opened(monkeypatch) as opened:
        listed = scan_family_import_packages()

    # LINKED is left out, and nothing in the folder it leads to is listed or read.
    assert [(item["name"], item["folder_path"]) for item in listed] == [(FAMILY, str((imports / FAMILY).resolve()))]
    assert [path for path in looked + opened if _within(path, outside)] == []


def test_the_package_list_leaves_out_a_package_folder_that_loops(imports: Path, resolve_as: str) -> None:
    _loop(imports / "LOOPFAM")
    _package(imports)

    listed = scan_family_import_packages()

    # Left out by the guard that refuses it for selection, before it is looked up.
    assert [item["name"] for item in listed] == [FAMILY]


@pytest.mark.parametrize("where", ["its own import root", "another import root"])
def test_the_package_list_follows_a_package_folder_linked_under_the_import_roots(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, imports: Path, where: str
) -> None:
    holder = imports / "archive" / "kept"
    if where == "another import root":
        second = tmp_path / "second-imports"
        holder = second / "archive" / "kept"
        monkeypatch.setattr(settings, "family_import_roots", [str(imports), str(second)])
    holder.mkdir(parents=True)
    target = _package(holder, manifest={"analysis_type": "trio_check"})
    (imports / "LINKED").symlink_to(target)

    listed = scan_family_import_packages()
    validation, bundle = load_validated_family_package(imports / "LINKED", manifest_text=kept_manifest(target))

    # Listed and read: the folder it leads to lies under the import roots.
    assert [(item["name"], item["folder_path"], item["family_id"]) for item in listed] == [
        ("LINKED", str(target.resolve()), "LINKED")
    ]
    # And selection takes it.
    assert validation.valid, validation.errors
    assert bundle is not None


def test_selection_takes_each_folder_the_package_list_shows_and_refuses_each_it_leaves_out(
    tmp_path: Path, imports: Path, resolve_as: str
) -> None:
    linked_out = imports / "LINKED"
    linked_out.symlink_to(_foreign_package(tmp_path / "outside" / "OUTFAM"))
    looping = _loop(imports / "LOOPFAM")
    (imports / "archive" / "kept").mkdir(parents=True)
    (imports / "INLINK").symlink_to(_package(imports / "archive" / "kept", "INSIDE"))
    _package(imports)

    listed = scan_family_import_packages()

    assert [item["name"] for item in listed] == [FAMILY, "INLINK"]
    for item in listed:
        validation, _bundle = load_validated_family_package(item["folder_path"], manifest_text=KEPT_MANIFEST)
        assert "package_folder_not_allowed" not in _codes(validation.errors), (item, validation.errors)
    # The folders it leaves out, selection refuses.
    for folder in (linked_out, looping):
        validation, bundle = load_validated_family_package(folder, manifest_text=KEPT_MANIFEST)
        assert (_codes(validation.errors), bundle) == (["package_folder_not_allowed"], None)


# --------------------------------------------------------------------------- #
# The routes
# --------------------------------------------------------------------------- #


def _admin_client() -> Any:
    """The app as an admin sees it, with no database behind it: these requests name no
    family, so none is looked up. A request the app fails on is answered 500, as a server
    answers it."""
    return signed_in_client("admin", raise_server_exceptions=False)


def test_the_package_list_route_lists_a_long_read_package_beside_an_entry_that_loops(imports: Path) -> None:
    long_read = imports / "COUPLE1"
    _long_read_file(long_read / "snv" / "MOTHER1" / "MOTHER1_annot.vcf.gz", "MOTHER1")
    _loop(long_read / "snv" / "GHOST1")
    _loop(imports / "LOOPFOLDER")

    with _admin_client() as client:
        listed = client.get("/api/family-imports/packages")

    # The looping long-read entry and the looping folder are left out; the package is listed.
    assert listed.status_code == 200, listed.text
    assert [item["name"] for item in listed.json()] == ["COUPLE1"]


def test_the_package_list_route_leaves_out_a_folder_linked_out_of_the_import_root(tmp_path: Path, imports: Path) -> None:
    outside = _foreign_package(tmp_path / "outside" / "OUTFAM").resolve()
    (imports / "LINKED").symlink_to(outside)
    _loop(imports / "LOOPFAM")
    (imports / "archive" / "kept").mkdir(parents=True)
    inside = _package(imports / "archive" / "kept", "INSIDE")
    (imports / "INLINK").symlink_to(inside)
    _package(imports)

    with _admin_client() as client:
        listed = client.get("/api/family-imports/packages")

    # LINKED is left out, and nothing of the folder it leads to is named.
    assert listed.status_code == 200, listed.text
    assert [(item["name"], item["folder_path"]) for item in listed.json()] == [
        (FAMILY, str((imports / FAMILY).resolve())),
        ("INLINK", str(inside.resolve())),
    ]
    for value in (str(outside), DECOY_FAMILY, DECOY_TYPE):
        assert value not in listed.text


# --------------------------------------------------------------------------- #
# A NUL in a path
# --------------------------------------------------------------------------- #


def _nul_message(written: str) -> str:
    return f"Package manifest path '{written}' {NUL_IN_PATH}"


def test_a_manifest_path_holding_a_nul_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "PKG"
    root.mkdir()

    with pytest.raises(HTTPException) as refused:
        _resolve_package_path(root, "snv/x\x00.vcf")

    # Refused before os.path.realpath, which raises ValueError ("embedded null byte").
    assert (refused.value.status_code, refused.value.detail) == (400, _nul_message("snv/x\\x00.vcf"))
    assert _package_path_or_none(root, "snv/x\x00.vcf") is None


@pytest.mark.parametrize(
    ("manifest", "written"),
    [
        ({"datasets": {"snv": {"family_vcf": "x\x00.vcf"}}}, "x\\x00.vcf"),
        ({"phenotypes": {"file": "x\x00.tsv"}}, "x\\x00.tsv"),
    ],
    ids=["dataset", "phenotypes"],
)
def test_validation_refuses_a_manifest_path_holding_a_nul(imports: Path, manifest: dict[str, Any], written: str) -> None:
    package = _package(imports, manifest=manifest)

    with pytest.raises(HTTPException) as refused:
        validated(package)

    assert (refused.value.status_code, refused.value.detail) == (400, _nul_message(written))


def test_a_package_folder_holding_a_nul_is_refused(imports: Path) -> None:
    folder = f"{imports}/FAM\x001"

    answer = _discover(folder)
    validation, bundle = load_validated_family_package(folder, manifest_text=KEPT_MANIFEST)

    # Refused before Path.resolve, which raises ValueError on a NUL.
    message = f"Family import path '{imports}/FAM\\x001' {NUL_IN_PATH}"
    assert [(issue.code, issue.message) for issue in answer.errors] == [("package_folder_not_allowed", message)]
    assert [(issue.code, issue.message) for issue in validation.errors] == [("package_folder_not_allowed", message)]
    assert bundle is None


