"""A folder of a package that is a symbolic link is followed, and reported (SEC-27).

The package list and Discover read a package's folders by their layout: the PGT pipeline's
``ped/`` folder, and the long-read pipeline's dataset folders (``snv/``, ``repeats/``, ...)
with a folder per sample in each. They follow such a folder when it is a symbolic link,
wherever it leads: a PED in a linked ``ped/`` lists the package, and Discover proposes a
member from a linked per-sample folder, though what it holds may be another family's. The
owner chose a warning over a refusal (2026-10-28): Discover and the package validation (a
dry run, and the import, which runs it) still follow such a link, and report it
(``package_folder_link``). The warning names the folder in the package and where it leads:
the folder relative to the import folder that holds it, or that it lies outside the import
folders, never an absolute path. The containment guards stay as they were: a file is read
only when it lies in the package. All packages, IDs and files are synthetic.
"""

from __future__ import annotations

import gzip
from pathlib import Path
from typing import Any

import pytest
import yaml

from backend.app.core.config import settings
from backend.app.schemas import FamilyImportValidationIssue, FamilyPackageManifestBuildRequest
from backend.app.services.family_package_discovery import discover_family_package_manifest
from backend.app.services.family_package_source import scan_family_import_packages
from backend.app.services.family_package_validation import load_validated_family_package

MOTHER, FATHER = "HG002", "HG003"
TRIO_PED = "FAM1 F1 0 0 1 1\nFAM1 M1 0 0 2 1\nFAM1 C1 F1 M1 1 2\n"


@pytest.fixture
def imports(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """The import root; ``outside/`` beside it is under no import root."""
    root = tmp_path / "imports"
    root.mkdir()
    (tmp_path / "outside").mkdir()
    monkeypatch.setattr(settings, "family_import_roots", [str(root)])
    return root


def _long_read_sample(package: Path, sample_id: str, karyotype: str) -> None:
    """A long-read sample's per-sample folders: an annotated SNV VCF, and a TRGT VCF whose
    header names the karyotype, each named after the sample."""
    snv = package / "snv" / sample_id / "annotation" / f"{sample_id}_annot.vcf.gz"
    trgt = package / "repeats" / sample_id / f"{sample_id}_tr.vcf.gz"
    for path, header in (
        (snv, "##fileformat=VCFv4.2\n"),
        (trgt, f"##fileformat=VCFv4.2\n##trgtCommand=trgt genotype --karyotype {karyotype}\n"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "wt") as handle:
            handle.write(f"{header}#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample_id}\n")


def _loop(at: Path) -> None:
    other = at.with_name(f"{at.name}.loop")
    at.symlink_to(other.name)
    other.symlink_to(at.name)


def _discover(package: Path) -> Any:
    return discover_family_package_manifest(FamilyPackageManifestBuildRequest(folder_path=str(package)))


def _links(issues: list[FamilyImportValidationIssue]) -> list[tuple[str | None, str]]:
    return [(issue.path, issue.message) for issue in issues if issue.code == "package_folder_link"]


def _warning(folder: str, leads_to: str) -> str:
    return (
        f"Folder '{folder}' of the package is a symbolic link to {leads_to}: check that what it "
        "holds belongs to this family."
    )


def _added_members(answer: Any) -> list[str]:
    return [member["sample_id"] for member in yaml.safe_load(answer.manifest_yaml)["family"]["add_members"]]


# --------------------------------------------------------------------------- #
# A long-read per-sample folder
# --------------------------------------------------------------------------- #


def test_discover_follows_a_per_sample_folder_linked_into_another_package_and_reports_it(imports: Path) -> None:
    couple = imports / "COUPLE1"
    _long_read_sample(couple, MOTHER, "XX")
    other = imports / "COUPLE2"
    _long_read_sample(other, FATHER, "XY")
    (couple / "snv" / FATHER).symlink_to(other / "snv" / FATHER)

    answer = _discover(couple)

    # Still followed: the linked folder gives the package a member, as before.
    assert _added_members(answer) == [MOTHER, FATHER]
    # Reported: the folder, and where it leads within the import folders.
    assert _links(answer.warnings) == [
        (str(couple.resolve() / "snv" / FATHER), _warning("snv/HG003", "'COUPLE2/snv/HG003' in the import folders"))
    ]
    # The other package holds no link.
    assert _links(_discover(other).warnings) == []


def test_a_per_sample_folder_linked_out_of_the_import_folders_is_reported_without_its_path(
    tmp_path: Path, imports: Path
) -> None:
    couple = imports / "COUPLE1"
    _long_read_sample(couple, MOTHER, "XX")
    elsewhere = tmp_path / "outside" / "run7"
    _long_read_sample(elsewhere, FATHER, "XY")
    (couple / "snv" / FATHER).symlink_to(elsewhere / "snv" / FATHER)

    answer = _discover(couple)

    assert _added_members(answer) == [MOTHER, FATHER]
    assert _links(answer.warnings) == [
        (str(couple.resolve() / "snv" / FATHER), _warning("snv/HG003", "a folder outside the import folders"))
    ]
    # Nothing of where it leads is named.
    assert not [issue for issue in answer.warnings if "outside/" in issue.message or "run7" in issue.message]


def test_a_dataset_folder_linked_inside_the_package_is_followed_and_reported(imports: Path) -> None:
    couple = imports / "COUPLE1"
    _long_read_sample(couple / "results", MOTHER, "XX")
    _long_read_sample(couple / "results", FATHER, "XY")
    (couple / "snv").symlink_to(couple / "results" / "snv")
    (couple / "repeats").symlink_to("results/repeats")

    answer = _discover(couple)

    # Followed: the members come from the linked snv/, their sexes from the linked repeats/.
    members = yaml.safe_load(answer.manifest_yaml)["family"]["add_members"]
    assert {member["sample_id"]: member.get("sex") for member in members} == {MOTHER: "female", FATHER: "male"}
    root = couple.resolve()
    assert _links(answer.warnings) == [
        (str(root / "repeats"), _warning("repeats", "'COUPLE1/results/repeats' in the import folders")),
        (str(root / "snv"), _warning("snv", "'COUPLE1/results/snv' in the import folders")),
    ]


def test_only_a_link_to_a_folder_is_reported_and_nothing_under_one(imports: Path) -> None:
    couple = imports / "COUPLE1"
    _long_read_sample(couple, MOTHER, "XX")
    # Under snv/: a link that leads nowhere, one to a file and one that loops. Nothing
    # follows them as folders.
    (couple / "snv" / "GONE").symlink_to(couple / "nowhere")
    (couple / "snv" / "FILE").symlink_to(couple / "snv" / MOTHER / "annotation" / f"{MOTHER}_annot.vcf.gz")
    _loop(couple / "snv" / "LOOP")
    # A linked folder that holds a link itself: the outer link is the one reported.
    elsewhere = imports / "ELSEWHERE"
    (elsewhere / "x").mkdir(parents=True)
    (elsewhere / FATHER).symlink_to(elsewhere / "x")
    (couple / "sv").symlink_to(elsewhere)

    answer = _discover(couple)

    assert _links(answer.warnings) == [
        (str(couple.resolve() / "sv"), _warning("sv", "'ELSEWHERE' in the import folders"))
    ]


# --------------------------------------------------------------------------- #
# The PED folder
# --------------------------------------------------------------------------- #


def _package_with_a_linked_ped_folder(imports: Path) -> Path:
    """FAM1 with its own PED and manifest, and a ped/ folder that is a link to PEDS, a folder
    in the import root that holds a PED."""
    shared = imports / "PEDS"
    shared.mkdir()
    (shared / "FAM1.ped").write_text(TRIO_PED, encoding="utf-8")
    package = imports / "FAM1"
    package.mkdir()
    (package / "family.ped").write_text(TRIO_PED, encoding="utf-8")
    (package / "manifest.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "family_id": "FAM1", "ped": "family.ped"}), encoding="utf-8"
    )
    (package / "ped").symlink_to(shared)
    return package


def test_a_ped_folder_that_is_a_link_is_reported_by_discover(imports: Path) -> None:
    package = _package_with_a_linked_ped_folder(imports)

    answer = _discover(package)

    assert answer.ped_path == "family.ped"
    assert _links(answer.warnings) == [
        (str(package.resolve() / "ped"), _warning("ped", "'PEDS' in the import folders"))
    ]


def test_a_ped_folder_that_is_a_link_is_reported_by_the_validation(imports: Path) -> None:
    package = _package_with_a_linked_ped_folder(imports)

    validation, bundle = load_validated_family_package(package)

    # A warning: the package still validates, and imports.
    assert validation.valid, validation.errors
    assert bundle is not None
    assert _links(validation.warnings) == [
        (str(package.resolve() / "ped"), _warning("ped", "'PEDS' in the import folders"))
    ]


def test_the_package_list_still_lists_a_package_by_a_ped_in_a_linked_ped_folder(imports: Path) -> None:
    shared = imports / "PEDS"
    shared.mkdir()
    (shared / "FAM1.ped").write_text(TRIO_PED, encoding="utf-8")
    package = imports / "FAM1"
    package.mkdir()
    (package / "ped").symlink_to(shared)

    listed = {item["name"]: item for item in scan_family_import_packages()}

    # Followed, as before: the PED behind the link lists the package.
    assert listed["FAM1"]["has_ped"] is True


# --------------------------------------------------------------------------- #
# The validation of a long-read package
# --------------------------------------------------------------------------- #


def test_the_validation_reports_a_linked_per_sample_folder_its_manifest_names_no_file_in(imports: Path) -> None:
    couple = imports / "COUPLE1"
    _long_read_sample(couple, MOTHER, "XX")
    other = imports / "COUPLE2"
    _long_read_sample(other, FATHER, "XY")
    (couple / "snv" / FATHER).symlink_to(other / "snv" / FATHER)
    # The manifest Discover drafts for it: both members, the father's files absent.
    (couple / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "family_id": "COUPLE1",
                "family": {"add_members": [{"sample_id": MOTHER, "sex": "female"}, {"sample_id": FATHER}]},
            }
        ),
        encoding="utf-8",
    )

    validation, bundle = load_validated_family_package(couple)

    assert validation.valid, validation.errors
    assert bundle is not None
    assert validation.sample_ids == [MOTHER, FATHER]
    assert _links(validation.warnings) == [
        (str(couple.resolve() / "snv" / FATHER), _warning("snv/HG003", "'COUPLE2/snv/HG003' in the import folders"))
    ]


def test_a_package_without_a_linked_folder_gets_no_such_warning(imports: Path) -> None:
    couple = imports / "COUPLE1"
    _long_read_sample(couple, MOTHER, "XX")
    _long_read_sample(couple, FATHER, "XY")

    assert _links(_discover(couple).warnings) == []
