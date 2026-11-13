"""A short-read (Illumina) WGS family folder: what Discover proposes for it, and that it can be
added by hand and validated.

The laboratory delivers such a family as a folder holding its PED at the top, its joint GATK
callset unannotated (``<family>.vcf.gz``) and VEP-annotated (``<family>.GRCh38.annotated.vcf.gz``),
and a folder per sample with its CRAM (``backend/tests/_short_read_wgs_package.py``). Discover
takes the annotated callset as the family's SNV callset, never the unannotated one, declared as
the primary callset of direct calls (``source_format: clair3``): GATK's header describes its
physical phasing, so the loader's own reading of a file whose first record is phased takes it
for imputed genotypes, which the diagnostic views leave out. The CRAMs are the alignments.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi import UploadFile

from backend.app.core.config import settings
from backend.app.schemas import FamilyPackageManifestBuildRequest
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


def test_the_gatk_callset_is_declared_the_primary_callset_of_direct_calls(folder: Path) -> None:
    annotated = folder / f"{FAMILY}.GRCh38.annotated.vcf.gz"
    # Read by itself the file is taken for imputed genotypes: its header says "phased" and its
    # first record is physically phased.
    with annotated.open("rb") as handle:
        assert _small_variant_callset(UploadFile(file=handle, filename=annotated.name)) == "glimpse2"

    manifest, _availability = _discover(folder)

    snv = manifest["datasets"]["snv"]
    assert snv["source_format"] == "clair3"
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
