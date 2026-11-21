"""A short-read (Illumina) WGS family folder: what Discover proposes for it, and that it can be
added by hand and validated.

The laboratory delivers such a family as a folder holding its PED at the top, its joint GATK
callset unannotated (``<family>.vcf.gz``) and VEP-annotated (``<family>.GRCh38.annotated.vcf.gz``),
and a folder per sample with its CRAM (``backend/tests/_short_read_wgs_package.py``). Discover
takes the annotated callset as the family's SNV callset, also when its name carries a suffix after
``annotated`` (``<family>.GRCh38.annotated.gnomad41j.vcf.gz``), never the unannotated one, and
refuses a folder with two annotated callsets rather than pick one. It declares the callset by its
caller (``source_format: haplotypecaller``): GATK's header describes its physical phasing, so the
loader's own reading of a file whose first record is phased takes it for imputed genotypes, which
the diagnostic views leave out. The CRAMs are the alignments.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml
from fastapi import UploadFile

from backend.app.core.config import settings
from backend.app.schemas import FamilyPackageManifestBuildRequest
from backend.app.services import import_watcher as watcher
from backend.app.services.coga_package import CogaPackage
from backend.app.services.family_package_datasets import _small_variant_callset
from backend.app.services.family_package_discovery import discover_family_package_manifest
from backend.app.services.family_package_source import scan_family_import_packages
from backend.app.services.family_package_validation import load_validated_family_package
from backend.tests._short_read_wgs_package import members, write_short_read_wgs_folder

FAMILY = "FAM001"


@pytest.fixture
def folder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path)])
    return write_short_read_wgs_folder(tmp_path / "SR-WGS" / FAMILY, FAMILY)


def _discover(folder: Path) -> tuple[dict, list]:
    out = discover_family_package_manifest(FamilyPackageManifestBuildRequest(folder_path=str(folder)))
    assert out.valid, out.errors
    return yaml.safe_load(out.manifest_yaml), out.datasets


def test_discover_takes_the_annotated_callset_and_each_samples_cram(folder: Path) -> None:
    manifest, availability = _discover(folder)

    snv = manifest["datasets"]["snv"]
    assert snv["enabled"] is True
    assert snv["family_vcf"] == f"{FAMILY}.GRCh38.annotated.vcf.gz"
    assert snv["index"] == f"{FAMILY}.GRCh38.annotated.vcf.gz.tbi"
    alignments = manifest["datasets"]["alignments"]
    assert alignments["enabled"] is True
    assert alignments["per_sample"] == {
        sample: {"file": f"{sample}/{sample}.cram", "index": f"{sample}/{sample}.cram.crai"}
        for sample in members(FAMILY)
    }
    # The members are the PED's.
    proband, mother, father = members(FAMILY)
    added = {entry["sample_id"]: entry for entry in manifest["family"]["add_members"]}
    assert set(added) == {proband, mother, father}
    assert (added[proband]["father"], added[proband]["mother"]) == (father, mother)
    # The unannotated callset is named nowhere.
    assert f"{FAMILY}.vcf.gz" not in yaml.safe_dump(manifest)
    assert {item.dataset_type for item in availability if item.complete} >= {"snv", "alignments"}


def test_the_gatk_callset_is_named_by_its_caller(folder: Path) -> None:
    annotated = folder / f"{FAMILY}.GRCh38.annotated.vcf.gz"
    # Its header names GATK HaplotypeCaller, so it is read as that caller's direct calls,
    # although the header says "phased" and its first record is physically phased.
    with annotated.open("rb") as handle:
        assert _small_variant_callset(UploadFile(file=handle, filename=annotated.name)) == "haplotypecaller"

    manifest, _availability = _discover(folder)

    snv = manifest["datasets"]["snv"]
    assert snv["source_format"] == "haplotypecaller"
    # GATK writes no reference-block records to leave out.
    assert "exclude_filters" not in snv


def test_without_crams_the_callset_is_still_found(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path)])
    folder = write_short_read_wgs_folder(tmp_path / FAMILY, FAMILY, crams=False)

    manifest, _availability = _discover(folder)

    assert manifest["datasets"]["snv"]["family_vcf"] == f"{FAMILY}.GRCh38.annotated.vcf.gz"
    assert manifest["datasets"]["alignments"]["enabled"] is False


def test_the_folder_is_listed_to_be_added_by_hand_and_its_draft_validates(folder: Path) -> None:
    listed = [(package["name"], package.get("project_folder")) for package in scan_family_import_packages()]
    assert listed == [(FAMILY, "SR-WGS")]

    out = discover_family_package_manifest(FamilyPackageManifestBuildRequest(folder_path=str(folder)))
    validation, bundle = load_validated_family_package(folder, manifest_text=out.manifest_yaml)

    assert validation.valid, validation.errors
    assert bundle is not None
    assert {member.iid for member in bundle.ped.members} == set(members(FAMILY))
    assert bundle.manifest.datasets["snv"].family_vcf == f"{FAMILY}.GRCh38.annotated.vcf.gz"


def test_a_package_may_name_the_short_read_wgs_analysis() -> None:
    package = CogaPackage.model_validate(
        {
            "schema": "coga-package/1",
            "status": "complete",
            "written_at": "2026-11-13T08:00:00Z",
            "pipeline": {"name": "example", "version": "1.0.0", "run_id": "run-1"},
            "project": "Rare disease",
            "assembly": "GRCh38",
            "analysis_type": "short_read_wgs",
            "family": {"family_id": FAMILY},
            "files": [{"path": f"{FAMILY}.GRCh38.annotated.vcf.gz", "size": 10}],
        }
    )
    assert package.analysis_type == "short_read_wgs"


SUFFIXED = f"{FAMILY}.GRCh38.annotated.gnomad41j.vcf.gz"


def _rename_annotated(folder: Path, name: str, *, index_suffix: str | None = ".tbi") -> None:
    """The annotated callset under the name ``name``, with its index as ``<name><index_suffix>``
    (none when None)."""
    annotated = folder / f"{FAMILY}.GRCh38.annotated.vcf.gz"
    annotated.rename(folder / name)
    index = folder / f"{FAMILY}.GRCh38.annotated.vcf.gz.tbi"
    if index_suffix is None:
        index.unlink()
    else:
        index.rename(folder / f"{name}{index_suffix}")


def _minimal_package() -> CogaPackage:
    """A package file that names no members and no datasets: Discover proposes both."""
    return CogaPackage.model_validate(
        {
            "schema": "coga-package/1",
            "status": "complete",
            "written_at": "2026-11-21T08:00:00Z",
            "pipeline": {"name": "example", "version": "1.0.0", "run_id": "run-1"},
            "project": "Rare disease",
            "assembly": "GRCh38",
            "analysis_type": "short_read_wgs",
            "family": {"family_id": FAMILY},
        }
    )


@pytest.mark.parametrize("index_suffix", [".tbi", ".csi"])
def test_an_annotated_callset_named_with_a_suffix_is_taken(folder: Path, index_suffix: str) -> None:
    _rename_annotated(folder, SUFFIXED, index_suffix=index_suffix)
    # The resource fork a Mac writes beside a file on a network share is no second callset.
    (folder / f"._{SUFFIXED}").write_bytes(b"\x00\x05\x16\x07")

    manifest, availability = _discover(folder)

    snv = manifest["datasets"]["snv"]
    assert snv["enabled"] is True
    assert snv["family_vcf"] == SUFFIXED
    assert snv["index"] == f"{SUFFIXED}{index_suffix}"
    assert snv["source_format"] == "haplotypecaller"
    assert f"{FAMILY}.vcf.gz" not in yaml.safe_dump(manifest)
    assert {item.dataset_type for item in availability if item.complete} >= {"snv", "alignments"}


def test_a_suffixed_callset_takes_only_its_own_index(folder: Path) -> None:
    # The suffixed callset has no index beside it; the one left of the annotated callset's
    # earlier name is another file's.
    annotated = folder / f"{FAMILY}.GRCh38.annotated.vcf.gz"
    annotated.rename(folder / SUFFIXED)

    manifest, availability = _discover(folder)

    snv = manifest["datasets"]["snv"]
    assert snv["enabled"] is False
    assert (snv["family_vcf"], snv["index"]) == (SUFFIXED, f"{SUFFIXED}.tbi")
    assert next(item for item in availability if item.dataset_type == "snv").complete is False


@pytest.mark.parametrize(
    "second",
    [f"{FAMILY}.GRCh38.annotated.vcf.gz", f"{FAMILY}.GRCh38.annotated.gnomad40.vcf.gz"],
    ids=["unsuffixed", "other-suffix"],
)
def test_two_annotated_callsets_are_refused_not_guessed(folder: Path, second: str) -> None:
    _rename_annotated(folder, SUFFIXED)
    if not (folder / second).exists():
        (folder / second).write_bytes((folder / SUFFIXED).read_bytes())
        (folder / f"{second}.tbi").write_bytes(b"TBI\x01")

    out = discover_family_package_manifest(FamilyPackageManifestBuildRequest(folder_path=str(folder)))

    assert out.valid is False
    refused = [issue for issue in out.errors if issue.code == "snv_multiple_candidates"]
    assert len(refused) == 1 and refused[0].dataset == "snv"
    assert SUFFIXED in refused[0].message and second in refused[0].message
    snv = yaml.safe_load(out.manifest_yaml)["datasets"]["snv"]
    assert snv["enabled"] is False
    assert snv["family_vcf"] not in (SUFFIXED, second)


@pytest.mark.parametrize(
    "name",
    [
        f"{FAMILY}.GRCh37.annotated.gnomad41j.vcf.gz",
        f"{FAMILY}.annotated.gnomad41j.vcf.gz",
        f"{FAMILY}2.GRCh38.annotated.gnomad41j.vcf.gz",
        f"{FAMILY}.GRCh38.annotated.gnomad41j.vcf",
    ],
    ids=["other-assembly", "no-assembly", "other-family", "not-bgzipped"],
)
def test_no_other_callset_is_taken_for_the_annotated_one(folder: Path, name: str) -> None:
    # Beside the unannotated callset, which is never read.
    _rename_annotated(folder, name)

    manifest, availability = _discover(folder)

    assert manifest["datasets"]["snv"]["enabled"] is False
    assert name not in yaml.safe_dump(manifest)
    assert f"{FAMILY}.vcf.gz" not in yaml.safe_dump(manifest)
    assert "snv" not in {item.dataset_type for item in availability if item.complete}


def test_a_minimal_package_file_gets_the_suffixed_callset_proposed(folder: Path) -> None:
    _rename_annotated(folder, SUFFIXED)

    completed, warnings, issues = asyncio.run(watcher._proposal(folder, _minimal_package()))

    assert issues == []
    snv = (completed.datasets or {})["snv"]
    assert (snv["family_vcf"], snv["index"], snv["source_format"]) == (SUFFIXED, f"{SUFFIXED}.tbi", "haplotypecaller")
    assert sorted(member.sample_id for member in completed.members or []) == sorted(members(FAMILY))
    assert "package_datasets_proposed" in {warning.code for warning in warnings}


def test_a_minimal_package_file_with_two_annotated_callsets_needs_attention(folder: Path) -> None:
    _rename_annotated(folder, SUFFIXED)
    (folder / f"{FAMILY}.GRCh38.annotated.vcf.gz").write_bytes((folder / SUFFIXED).read_bytes())

    completed, _warnings, issues = asyncio.run(watcher._proposal(folder, _minimal_package()))

    # Nothing is proposed: the delivery is not imported with its alignments and without its SNVs.
    assert completed.datasets is None and completed.members is None
    assert {"discover_failed", "snv_multiple_candidates"} <= {issue.code for issue in issues}
