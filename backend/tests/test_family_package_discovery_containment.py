"""Discover reads the package's own files, whatever an ID names.

Discover names files after the family and sample IDs: ``<family ID>.ped``,
``cram/<sample ID>.cram``, a NIPT parent's ``<sample ID>.vcf.gz``. An ID is a name, but the ID
rule lets it hold a path: an absolute one, or ``..`` that climbs out of the package folder, so
the file named after it lay outside the package. Discover looked such files up and read them:
another folder's PED (its sample IDs and its path came back in the draft), a VCF's header (its
column count decided whether a NIPT pair was proposed), any ``.ped`` on the host through an
absolute family ID; a NIPT parent's absolute ID failed with a 500, a sample ID beginning with
``~`` named the home folder. A ``*``, ``?`` or ``[`` in an ID widened a pattern, so a sample took
another's file. Now no path an ID leads out of the package is looked up, the ID is named in a
warning, a symlink cannot lead a PED or a NIPT VCF out either, and an ID's wildcard characters
match only themselves.

Each package sits next to decoys, files named as an ID would lead to, that Discover must
never open, look up or name (a symlink in the package is looked up to tell where it leads, but
what it leads to is not opened). All IDs and files are synthetic.
"""

from __future__ import annotations

import builtins
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
import gzip
import io
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from backend.app.core.config import settings
from backend.app.schemas import FamilyPackageManifestBuildOut, FamilyPackageManifestBuildRequest
from backend.app.services.family_package_discovery import NAMING_SCHEMES, discover_family_package_manifest

FAMILY = "FAM1"
TRIO_PED = f"{FAMILY} F1 0 0 1 1\n{FAMILY} M1 0 0 2 1\n{FAMILY} C1 F1 M1 1 2\n"
DECOY_PED = "DECOYFAM DECOYKID 0 0 1 2\n"
# What a decoy holds: none of it may reach the answer.
DECOY_MARKERS = ("DECOYFAM", "DECOYKID", "DECOYMOTHER", "DECOYFATHER")


@pytest.fixture
def imports(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """The authorised import root; ``outside/`` beside it is not under it."""
    root = tmp_path / "imports"
    root.mkdir()
    (tmp_path / "outside").mkdir()
    monkeypatch.setattr(settings, "family_import_roots", [str(root)])
    return root


def _package(imports: Path, name: str = FAMILY, *, ped: str | None = TRIO_PED) -> Path:
    package = imports / name
    package.mkdir()
    if ped is not None:
        (package / "family.ped").write_text(ped, encoding="utf-8")
    return package


def _vcf(path: Path, sample: str) -> Path:
    """A one-sample VCF header, gzipped."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt") as handle:
        handle.write(f"##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample}\n")
    return path


def _discover(package: Path, **request: Any) -> FamilyPackageManifestBuildOut:
    return discover_family_package_manifest(FamilyPackageManifestBuildRequest(folder_path=str(package), **request))


class _Access:
    """The paths Discover opened, listed or found with a pattern (``opened``) and those it
    looked up (``probed``: a stat, as an existence check or a symlink resolution makes),
    each normalised. A file opened through a symlink is recorded as the file it reaches too."""

    def __init__(self) -> None:
        self.opened: list[str] = []
        self.probed: list[str] = []
        self.resolving = False

    @staticmethod
    def _normalised(value: Any) -> str | None:
        if isinstance(value, int):
            return None  # a file descriptor
        return os.path.normpath(os.path.abspath(os.fsdecode(os.fspath(value))))

    def found(self, value: Any) -> None:
        path = self._normalised(value)
        if path is not None:
            self.opened.append(path)

    def open(self, value: Any) -> None:
        path = self._normalised(value)
        if path is None:
            return
        self.resolving = True  # resolving the symlinks is the test's look-up, not Discover's
        try:
            self.opened += [path, os.path.realpath(path)]
        finally:
            self.resolving = False

    def probe(self, value: Any) -> None:
        path = None if self.resolving else self._normalised(value)
        if path is not None:
            self.probed.append(path)

    def touching(self, *decoys: Path, probes: bool = True) -> list[str]:
        """The recorded paths that are a decoy or lie under one."""
        targets = [os.path.normpath(str(decoy)) for decoy in decoys]
        paths = [*self.opened, *(self.probed if probes else [])]
        return [path for path in paths if any(path == target or path.startswith(target + os.sep) for target in targets)]


@contextmanager
def _recording(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Access]:
    """Record the file system calls made inside the block: ``open`` (a text read, gzip, a
    bounded read), ``os.scandir``, ``Path.glob``'s matches, ``os.stat`` and ``os.lstat`` (an
    existence check, ``Path.resolve``)."""
    access = _Access()
    real_open, real_scandir, real_stat, real_lstat = builtins.open, os.scandir, os.stat, os.lstat
    real_glob = Path.glob

    def spy_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        access.open(file)
        return real_open(file, *args, **kwargs)

    def spy_scandir(path: Any = ".") -> Any:
        access.found(path)
        return real_scandir(path)

    def spy_stat(path: Any, *args: Any, **kwargs: Any) -> Any:
        access.probe(path)
        return real_stat(path, *args, **kwargs)

    def spy_lstat(path: Any, *args: Any, **kwargs: Any) -> Any:
        access.probe(path)
        return real_lstat(path, *args, **kwargs)

    def spy_glob(self: Path, pattern: str, *args: Any, **kwargs: Any) -> Iterator[Path]:
        found = list(real_glob(self, pattern, *args, **kwargs))
        for path in found:
            access.found(path)
        return iter(found)

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", spy_open)
        patch.setattr(io, "open", spy_open)
        patch.setattr(os, "scandir", spy_scandir)
        patch.setattr(os, "stat", spy_stat)
        patch.setattr(os, "lstat", spy_lstat)
        patch.setattr(Path, "glob", spy_glob)
        yield access


def _holds(answer: FamilyPackageManifestBuildOut, *texts: str) -> list[str]:
    """The ``texts`` (a decoy's content, another sample's file name) the answer holds."""
    dumped = answer.model_dump_json()
    return [text for text in texts if text in dumped]


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _paths_naming(answer: FamilyPackageManifestBuildOut, package: Path, *decoys: Path) -> list[str]:
    """The paths in the answer (its PED, each file it lists, each issue's, each in the
    draft) that name a decoy, read from the package folder as validation would read them.
    An ID that is a path shows in a path built from it, so the strings are compared by what
    they name, not by what they contain."""
    manifest = yaml.safe_load(answer.manifest_yaml) or {}
    values = [
        answer.ped_path,
        *(item.path for dataset in answer.datasets for item in dataset.files),
        *(issue.path for issue in (*answer.errors, *answer.warnings)),
        manifest.get("ped"),
        *_strings(manifest.get("datasets")),
    ]
    targets = {os.path.normpath(str(decoy)) for decoy in decoys}
    return [
        value
        for value in values
        if isinstance(value, str) and value and os.path.normpath(os.path.join(package, value)) in targets
    ]


# --------------------------------------------------------------------------- #
# A family ID that leads out of the package
# --------------------------------------------------------------------------- #

# The family ID, and where its PED would be: beside the package in the import root, or in a
# folder outside the import root (reached by an absolute ID or by climbing out of both).
_FAMILY_IDS_OUT = {
    "sibling": ("../decoy", "imports/decoy.ped"),
    "absolute": ("{tmp}/outside/decoy", "outside/decoy.ped"),
    "climbing": ("../../outside/decoy", "outside/decoy.ped"),
}


@pytest.mark.parametrize("source", ["request", "manifest"])
@pytest.mark.parametrize("case", sorted(_FAMILY_IDS_OUT))
def test_a_family_id_that_leads_out_of_the_package_reads_no_ped_there(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, imports: Path, case: str, source: str
) -> None:
    family_id, decoy_relative = _FAMILY_IDS_OUT[case]
    family_id = family_id.format(tmp=tmp_path)
    decoy = tmp_path / decoy_relative
    decoy.write_text(DECOY_PED, encoding="utf-8")
    package = _package(imports)
    if source == "manifest":
        (package / "manifest.yaml").write_text(yaml.safe_dump({"schema_version": 1, "family_id": family_id}))

    with _recording(monkeypatch) as access:
        answer = _discover(package, family_id=family_id if source == "request" else None)

    # The decoy was never opened nor looked up; the package's own PED was read.
    assert access.touching(decoy, tmp_path / "outside") == []
    assert answer.ped_path == "family.ped"
    assert answer.sample_ids == ["F1", "M1", "C1"]
    # Neither the decoy's content nor its path is in the answer.
    assert _holds(answer, *DECOY_MARKERS) == []
    assert _paths_naming(answer, package, decoy) == []
    warning = next(issue for issue in answer.warnings if issue.code == "family_id_leaves_package")
    origin = "the family_id of the request" if source == "request" else "the manifest's family_id"
    assert warning.message == (
        f"Family ID '{family_id}' ({origin}) is a path out of the package folder: Discover looks "
        "for files only within the package directory. Check the ID."
    )
    # The PED's family is not that ID: the draft says so, as for any other mismatch.
    assert "ped_family_mismatch" in [issue.code for issue in answer.errors]


def test_without_a_ped_the_draft_names_none_outside_the_package(
    monkeypatch: pytest.MonkeyPatch, imports: Path
) -> None:
    decoy = imports / "decoy.ped"
    decoy.write_text(DECOY_PED, encoding="utf-8")
    package = _package(imports, ped=None)

    with _recording(monkeypatch) as access:
        answer = _discover(package, family_id="../decoy")

    assert access.touching(decoy) == []
    # Discover found no PED, and does not propose one outside the package.
    assert [issue.code for issue in answer.errors] == ["ped_file_missing"]
    assert answer.ped_path is None
    assert "ped" not in yaml.safe_load(answer.manifest_yaml)
    assert _holds(answer, *DECOY_MARKERS) == []
    assert _paths_naming(answer, package, decoy) == []


@pytest.mark.parametrize("link", ["FAM1.ped", "family.ped", "ped/combined.ped"])
def test_a_ped_that_is_a_symlink_out_of_the_package_is_not_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, imports: Path, link: str
) -> None:
    decoy = tmp_path / "outside" / "decoy.ped"
    decoy.write_text(DECOY_PED, encoding="utf-8")
    package = _package(imports, ped=None)
    (package / link).parent.mkdir(parents=True, exist_ok=True)
    (package / link).symlink_to(decoy)

    with _recording(monkeypatch) as access:
        answer = _discover(package, family_id=FAMILY)

    # Resolving the symlink looks its target up; nothing reads it.
    assert access.touching(decoy, probes=False) == []
    assert [issue.code for issue in answer.errors] == ["ped_file_missing"]
    assert answer.sample_ids == []
    assert _holds(answer, *DECOY_MARKERS) == []
    assert _paths_naming(answer, package, decoy) == []


@pytest.mark.parametrize("family_id", ["FAM*", "FAM?", "FAM[12]"])
def test_a_family_id_with_a_wildcard_takes_no_other_familys_callset(imports: Path, family_id: str) -> None:
    package = _package(imports, ped=f"{family_id} S1 0 0 1 2\n")
    # Another family's joint callset in the same folder: a pattern widened by the ID's
    # wildcard took it for this family's.
    for name in ("snv/FAM2.annotated.vcf.gz", "snv/FAM2.annotated.vcf.gz.tbi"):
        (package / name).parent.mkdir(parents=True, exist_ok=True)
        (package / name).write_text("x")

    answer = _discover(package, family_id=family_id)

    snv = yaml.safe_load(answer.manifest_yaml)["datasets"]["snv"]
    assert snv == {
        "enabled": False,
        "family_vcf": f"snv/{family_id}.annotated.vcf.gz",
        "index": f"snv/{family_id}.annotated.vcf.gz.tbi",
    }
    assert _holds(answer, "FAM2") == []


def test_a_family_id_with_a_wildcard_finds_its_own_callset(imports: Path) -> None:
    package = _package(imports, ped="FAM* S1 0 0 1 2\n")
    for name in ("snv/FAM*.annotated.vcf.gz", "snv/FAM*.annotated.vcf.gz.tbi", "snv/FAM2.annotated.vcf.gz"):
        (package / name).parent.mkdir(parents=True, exist_ok=True)
        (package / name).write_text("x")

    snv = yaml.safe_load(_discover(package, family_id="FAM*").manifest_yaml)["datasets"]["snv"]

    assert snv["enabled"] is True
    assert snv["family_vcf"] == "snv/FAM*.annotated.vcf.gz"


# --------------------------------------------------------------------------- #
# A sample ID that leads out of the package
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("sample_id", "decoy_relative", "warned"),
    [
        ("../decoy", "imports/decoy.cram", True),
        ("{tmp}/outside/decoy", "outside/decoy.cram", True),
        ("../../outside/decoy", "outside/decoy.cram", True),
        # "~" is the home folder only in a path someone types: in an ID it is a name.
        ("~/decoy", "outside/decoy.cram", False),
        ("~decoy", "outside/decoy.cram", False),
    ],
)
def test_a_sample_id_that_leads_out_of_the_package_is_looked_up_nowhere_outside(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, imports: Path, sample_id: str, decoy_relative: str, warned: bool
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "outside"))
    sample_id = sample_id.format(tmp=tmp_path)
    decoy = tmp_path / decoy_relative
    decoy.write_text("not a CRAM")
    package = _package(imports, ped=f"{FAMILY} {sample_id} 0 0 1 2\n")

    with _recording(monkeypatch) as access:
        answer = _discover(package, family_id=FAMILY)

    # Discover answers (it refused the whole package with a 400, or failed with a 500 for
    # "~decoy"), and looks nothing up outside the package.
    assert access.touching(decoy, tmp_path / "outside") == []
    assert answer.sample_ids == [sample_id]
    alignments = next(item for item in answer.datasets if item.dataset_type == "alignments")
    assert not alignments.complete
    assert _paths_naming(answer, package, decoy) == []
    warnings = [issue for issue in answer.warnings if issue.code == "sample_id_leaves_package"]
    if warned:
        assert [(issue.sample_id, issue.message) for issue in warnings] == [
            (
                sample_id,
                f"Sample ID '{sample_id}' is a path out of the package folder: Discover looks for "
                "files only within the package directory. Check the ID.",
            )
        ]
    else:
        assert warnings == []


@pytest.mark.parametrize(
    ("father", "mother"),
    [
        ("../decoyf", "../decoym"),
        ("{imports}/decoyf", "{imports}/decoym"),
        ("../decoyf*", "../decoym*"),
    ],
)
def test_a_nipt_parent_id_that_leads_out_reads_no_vcf_there(
    monkeypatch: pytest.MonkeyPatch, imports: Path, father: str, mother: str
) -> None:
    # One-sample VCFs beside the package, named as the parent IDs lead: Discover read their
    # headers and proposed them as a NIPT pair (a 400 then), or failed on an absolute ID.
    decoys = [_vcf(imports / "decoyf.vcf.gz", "DECOYFATHER"), _vcf(imports / "decoym.vcf.gz", "DECOYMOTHER")]
    father, mother = father.format(imports=imports), mother.format(imports=imports)
    package = _package(imports, "NIPTFAM1", ped=f"NIPTFAM1 FETUS1 {father} {mother} 0 0\n")

    with _recording(monkeypatch) as access:
        answer = _discover(package, family_id="NIPTFAM1")

    assert access.touching(*decoys) == []
    assert "nipt_pair_detected" not in [issue.code for issue in answer.warnings]
    assert "analysis_type" not in yaml.safe_load(answer.manifest_yaml)
    assert _holds(answer, *DECOY_MARKERS, "decoyf.vcf.gz", "decoym.vcf.gz") == []
    assert _paths_naming(answer, package, *decoys) == []
    # The parents are not in the PED, which the draft reports as before.
    assert {issue.code for issue in answer.errors} == {"ped_missing_father", "ped_missing_mother"}


def _nipt_package(imports: Path, *, father: str, mother: str) -> Path:
    return _package(
        imports,
        "NIPTFAM1",
        ped=f"NIPTFAM1 {father} 0 0 1 1\nNIPTFAM1 {mother} 0 0 2 1\nNIPTFAM1 FETUS1 {father} {mother} 0 0\n",
    )


def test_a_nipt_parent_id_with_a_wildcard_takes_no_other_samples_vcf(imports: Path) -> None:
    package = _nipt_package(imports, father="FATHER?", mother="CFDNA*")
    _vcf(package / "CFDNA1.mutect2.vcf.gz", "CFDNA1")
    _vcf(package / "FATHER1.mutect2.vcf.gz", "FATHER1")

    answer = _discover(package, family_id="NIPTFAM1")

    # CFDNA1's and FATHER1's files were proposed as the plasma's and the father's.
    assert "nipt_pair_detected" not in [issue.code for issue in answer.warnings]
    assert _holds(answer, "CFDNA1.mutect2.vcf.gz", "FATHER1.mutect2.vcf.gz") == []


def test_a_nipt_parent_id_with_a_wildcard_finds_its_own_vcf(imports: Path) -> None:
    package = _nipt_package(imports, father="FATHER?", mother="CFDNA*")
    _vcf(package / "CFDNA*.vcf.gz", "CFDNA*")
    _vcf(package / "FATHER?.mutect2.vcf.gz", "FATHER?")
    _vcf(package / "CFDNA1.mutect2.vcf.gz", "CFDNA1")

    answer = _discover(package, family_id="NIPTFAM1")

    assert "nipt_pair_detected" in [issue.code for issue in answer.warnings]
    snv = yaml.safe_load(answer.manifest_yaml)["datasets"]["snv"]
    assert snv["per_sample"] == {"CFDNA*": {"vcf": "CFDNA*.vcf.gz"}, "FATHER?": {"vcf": "FATHER?.mutect2.vcf.gz"}}


def test_a_nipt_vcf_that_is_a_symlink_out_of_the_package_is_not_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, imports: Path
) -> None:
    decoy = _vcf(tmp_path / "outside" / "decoym.vcf.gz", "DECOYMOTHER")
    package = _nipt_package(imports, father="FATHER1", mother="CFDNA1")
    (package / "CFDNA1.vcf.gz").symlink_to(decoy)
    _vcf(package / "FATHER1.vcf.gz", "FATHER1")

    with _recording(monkeypatch) as access:
        answer = _discover(package, family_id="NIPTFAM1")

    # Discover read its header and proposed it as the plasma's VCF (a 400 then).
    assert access.touching(decoy, probes=False) == []
    assert "nipt_pair_detected" not in [issue.code for issue in answer.warnings]
    assert _holds(answer, *DECOY_MARKERS) == []


def test_a_dataset_file_that_is_a_symlink_out_of_the_package_counts_as_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, imports: Path
) -> None:
    decoy = tmp_path / "outside" / "decoy.cram"
    decoy.write_text("not a CRAM")
    package = _package(imports, ped=f"{FAMILY} S1 0 0 1 2\n")
    (package / "cram").mkdir()
    (package / "cram" / "S1.cram").symlink_to(decoy)

    with _recording(monkeypatch) as access:
        answer = _discover(package, family_id=FAMILY)

    # The package guard refused the whole package with a 400; the file is absent now.
    assert access.touching(decoy, probes=False) == []
    alignments = next(item for item in answer.datasets if item.dataset_type == "alignments")
    assert not alignments.complete
    assert [item.exists for item in alignments.files] == [False]


def test_a_sample_id_with_a_wildcard_takes_no_other_samples_file(imports: Path) -> None:
    package = _package(imports, ped=f"{FAMILY} S? 0 0 1 2\n{FAMILY} S[12] 0 0 2 2\n{FAMILY} S* 0 0 1 2\n")
    for name in ("S1.cram", "S1.cram.crai", "S2.cram", "S2.cram.crai", "S1.dedup.cram"):
        (package / "cram").mkdir(exist_ok=True)
        (package / "cram" / name).write_text("x")
    # The one file named after one of them is that sample's.
    (package / "cram" / "S?.cram").write_text("x")

    answer = _discover(package, family_id=FAMILY)

    alignments = next(item for item in answer.datasets if item.dataset_type == "alignments")
    found = {(item.sample_id, item.path) for item in alignments.files if item.exists}
    assert found == {("S?", "cram/S?.cram")}
    assert alignments.samples == ["S?"]


# --------------------------------------------------------------------------- #
# The guard
# --------------------------------------------------------------------------- #


def test_the_guard_reads_a_path_built_from_an_id_as_a_name_in_the_package(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from backend.app.services.family_package_common import package_path_within, path_leaves_package

    root = tmp_path / "PKG"
    (root / "cram").mkdir(parents=True)
    (root / "cram" / "S1.cram").write_text("x")
    outside = tmp_path / "outside.cram"
    outside.write_text("x")
    (root / "cram" / "link.cram").symlink_to(outside)

    folder = root.resolve()
    assert package_path_within(root, "cram/S1.cram") == folder / "cram" / "S1.cram"
    assert package_path_within(root, "cram/x/../S1.cram") == folder / "cram" / "S1.cram"
    # "~" is part of the name; a NUL names no file; a symlink cannot lead out.
    assert package_path_within(root, "~/S1.cram") == folder / "~" / "S1.cram"
    assert package_path_within(root, "cram/S\x001.cram") is None
    assert package_path_within(root, "cram/link.cram") is None
    for leaving in ("../outside.cram", "cram/../../outside.cram", "..", str(outside), str(root / "cram" / "S1.cram")):
        assert path_leaves_package(leaving), leaving
        with _recording(monkeypatch) as access:
            assert package_path_within(root, leaving) is None
        # Told from the string: nothing outside the package was looked up.
        assert access.touching(outside) == [], leaving
    for staying in ("cram/S1.cram", "cram/x/../S1.cram", "~/S1.cram", "...", "a/.."):
        assert not path_leaves_package(staying), staying


def _id_templates() -> list[str]:
    """Every path Discover builds from a family or sample ID, as a template."""
    from backend.app.services.family_package_long_read import _TRGT_VCF_PATTERNS
    from backend.app.services.family_package_nipt import _NIPT_COVERAGE_GLOBS, _NIPT_VCF_GLOBS

    templates = [
        template
        for roles in NAMING_SCHEMES["standard_v1"]["datasets"].values()
        for candidates in roles.values()
        for template in candidates
    ]
    templates += ["{family_id}.ped", "ped/{family_id}.ped", "king/{family_id}.kin0", "ngsbits/{sample_id}.tsv"]
    templates += list(_TRGT_VCF_PATTERNS)
    templates += [pattern.replace("{sample}", "{sample_id}") for pattern in (*_NIPT_VCF_GLOBS, *_NIPT_COVERAGE_GLOBS)]
    return templates


@pytest.mark.parametrize(
    "identifier",
    ["S1", ".", "..", "../x", "../../x", "a/..", "a/../..", "a/../../x", "./../x", "x/./../../y", "/abs/x", "~/x"],
)
def test_an_id_leads_a_path_out_of_the_package_only_when_it_is_a_path_out_itself(identifier: str) -> None:
    # Discover warns about an ID that is a path out of the package folder. A path built from
    # any other ID stays in the folder, so every path Discover leaves unlooked-up because it
    # leads out comes from an ID it warns about; and no pattern leaves the folder of itself.
    from backend.app.services.family_package_common import path_leaves_package

    for template in _id_templates():
        assert not path_leaves_package(template.format(family_id="S1", sample_id="S1")), template
        rendered = template.format(family_id=identifier, sample_id=identifier)
        if path_leaves_package(rendered):
            assert path_leaves_package(identifier), (template, identifier)


# --------------------------------------------------------------------------- #
# The route
# --------------------------------------------------------------------------- #


@contextmanager
def _client_as(role: str) -> Iterator[Any]:
    from fastapi.testclient import TestClient

    from backend.app.core.postgres import get_postgres_session
    from backend.app.dependencies import get_current_user
    from backend.app.main import app
    from backend.app.services.access_control import CurrentUser

    original_overrides = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True
    user = CurrentUser(
        id="00000000-0000-0000-0000-000000000001",
        username=f"{role}@example.com",
        email=f"{role}@example.com",
        role=role,
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
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides = original_overrides


@pytest.mark.parametrize("role", ["viewer", "admin"])
def test_discover_stays_admin_only_and_reads_nothing_outside_through_the_route(
    monkeypatch: pytest.MonkeyPatch, imports: Path, role: str
) -> None:
    from backend.app.routers import family_imports

    decoy = imports / "decoy.ped"
    decoy.write_text(DECOY_PED, encoding="utf-8")
    package = _package(imports)
    calls: list[str | None] = []

    async def no_family(session: Any, family_id: str | None) -> list[str]:
        return []

    def discover(request: FamilyPackageManifestBuildRequest, **kwargs: Any) -> FamilyPackageManifestBuildOut:
        calls.append(request.family_id)
        return discover_family_package_manifest(request, **kwargs)

    monkeypatch.setattr(family_imports, "existing_family_sample_ids", no_family)
    monkeypatch.setattr(family_imports, "discover_family_package_manifest", discover)
    with _client_as(role) as client:
        response = client.post(
            "/api/family-imports/manifest/discover", json={"folder_path": str(package), "family_id": "../decoy"}
        )

    if role == "viewer":
        assert (response.status_code, response.json()) == (403, {"detail": "Admin access required"})
        assert calls == []
        return
    assert response.status_code == 200, response.text
    assert calls == ["../decoy"]
    answer = FamilyPackageManifestBuildOut.model_validate(response.json())
    assert _holds(answer, *DECOY_MARKERS) == []
    assert _paths_naming(answer, package, decoy) == []
    assert answer.sample_ids == ["F1", "M1", "C1"]
