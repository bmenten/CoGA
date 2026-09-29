from __future__ import annotations

import pytest

from pathlib import Path

from backend.app.schemas import FamilyPackageManifestBuildRequest
from backend.app.core.config import settings
from backend.app.services.family_package_common import ManifestDataset, PackageManifest
from backend.app.services.family_package_manifest import _normalize_manifest_samples
from backend.app.services.family_package_validation import (
    _validate_coverage_dataset,
    load_validated_family_package,
)
from backend.app.services.family_package_discovery import discover_family_package_manifest
from backend.app.services.family_package_source import scan_family_import_packages

_DEMO_DIR = Path(__file__).resolve().parents[2] / "demo" / "nipt_family"


def _write_nipt_package(folder) -> None:
    folder.mkdir()
    (folder / "manifest.yaml").write_text(
        "schema_version: 1\n"
        "family_id: FAM_NIPT_DEMO\n"
        "analysis_type: monogenic_nipt\n"
        "ped: nipt_trio.ped\n"
        "samples:\n"
        "  CFDNA_NIPT:\n"
        "    assay: nipt_cfdna\n"
    )
    (folder / "nipt_trio.ped").write_text(
        "FAM_NIPT_DEMO\tFATHER_NIPT\t0\t0\t1\t1\n"
        "FAM_NIPT_DEMO\tCFDNA_NIPT\t0\t0\t2\t1\n"
        "FAM_NIPT_DEMO\tFETUS_NIPT\tFATHER_NIPT\tCFDNA_NIPT\t0\t2\n"
    )


def test_discover_uses_manifest_family_id_not_folder_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(settings, "family_import_roots", [])
    # Folder name (nipt_family) deliberately differs from the declared family_id.
    package = tmp_path / "nipt_family"
    _write_nipt_package(package)

    result = discover_family_package_manifest(
        FamilyPackageManifestBuildRequest(folder_path=str(package))
    )

    assert result.family_id == "FAM_NIPT_DEMO"
    assert not any(issue.code == "ped_family_mismatch" for issue in result.errors)
    # The NIPT tags are preserved in the regenerated manifest preview.
    assert "analysis_type: monogenic_nipt" in result.manifest_yaml
    assert "nipt_cfdna" in result.manifest_yaml


def test_scan_lists_packages_with_manifest_and_ped(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    nipt = tmp_path / "FAM_NIPT_DEMO"
    nipt.mkdir()
    (nipt / "manifest.yaml").write_text(
        "schema_version: 1\n"
        "family_id: FAM_NIPT_DEMO\n"
        "analysis_type: monogenic_nipt\n"
        "ped: nipt_trio.ped\n"
    )
    (nipt / "nipt_trio.ped").write_text("FAM_NIPT_DEMO\tF\t0\t0\t1\t1\n")

    ped_only = tmp_path / "PLAIN_FAM"
    ped_only.mkdir()
    (ped_only / "family.ped").write_text("PLAIN_FAM\tP\t0\t0\t1\t2\n")

    (tmp_path / "not-a-package").mkdir()  # no manifest, no ped -> skipped

    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path)])

    packages = {pkg["name"]: pkg for pkg in scan_family_import_packages()}

    assert set(packages) == {"FAM_NIPT_DEMO", "PLAIN_FAM"}

    nipt_pkg = packages["FAM_NIPT_DEMO"]
    assert nipt_pkg["family_id"] == "FAM_NIPT_DEMO"
    assert nipt_pkg["has_manifest"] is True
    assert nipt_pkg["has_ped"] is True
    assert nipt_pkg["analysis_type"] == "monogenic_nipt"
    assert nipt_pkg["folder_path"] == str(nipt.resolve())

    plain_pkg = packages["PLAIN_FAM"]
    assert plain_pkg["family_id"] == "PLAIN_FAM"  # falls back to the folder name
    assert plain_pkg["has_manifest"] is False
    assert plain_pkg["has_ped"] is True
    assert plain_pkg["analysis_type"] is None


def test_scan_returns_empty_without_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "family_import_roots", [])
    assert scan_family_import_packages() == []


def test_manifest_accepts_analysis_type() -> None:
    manifest = PackageManifest(ped="trio.ped", analysis_type="monogenic_nipt")
    assert manifest.analysis_type == "monogenic_nipt"


def test_manifest_samples_carry_assay() -> None:
    # The per-sample assay is what _register_package_provenance promotes to the
    # top of sample.metadata so resolve_nipt_trio can find the cfDNA sample.
    samples = _normalize_manifest_samples({"CFDNA_NIPT": {"assay": "nipt_cfdna"}})
    assert samples["CFDNA_NIPT"]["assay"] == "nipt_cfdna"


def test_family_import_roots_default_is_data_families() -> None:
    from backend.app.core.config import Settings

    default = Settings.model_fields["family_import_roots"].default_factory()
    assert default == ["/data/families"]


def test_scan_includes_s3_packages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "family_import_roots", ["s3://bucket/families"])

    def fake_list(uri: str):
        assert uri == "s3://bucket/families"
        return [
            {
                "name": "FAM_S3",
                "uri": "s3://bucket/families/FAM_S3",
                "has_manifest": True,
                "has_ped": True,
            }
        ]

    monkeypatch.setattr("backend.app.services.family_package_source.list_remote_package_candidates", fake_list)

    packages = {pkg["name"]: pkg for pkg in scan_family_import_packages()}
    assert "FAM_S3" in packages
    assert packages["FAM_S3"]["folder_path"] == "s3://bucket/families/FAM_S3"
    assert packages["FAM_S3"]["has_manifest"] is True
    assert packages["FAM_S3"]["family_id"] == "FAM_S3"


def test_scan_s3_failure_is_best_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "family_import_roots", ["s3://bucket/families"])

    def boom(_uri: str):
        raise RuntimeError("no credentials")

    monkeypatch.setattr("backend.app.services.family_package_source.list_remote_package_candidates", boom)
    assert scan_family_import_packages() == []


def test_coverage_dataset_requires_per_sample() -> None:
    errors: list = []
    summary = _validate_coverage_dataset(
        root=Path("/tmp"), dataset=ManifestDataset(), ped_sample_ids=set(), errors=errors
    )
    assert summary.status == "error"
    assert any(issue.code == "dataset_per_sample_missing" for issue in errors)


def test_demo_package_validates_with_snv_and_coverage_datasets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "family_import_roots", [])
    validation, _bundle = load_validated_family_package(_DEMO_DIR)

    assert validation.valid, validation.errors
    assert validation.family_id == "FAM_NIPT_DEMO"
    datasets = {summary.dataset_type: summary for summary in validation.datasets}
    # The combined (uncompressed, unindexed) VCF and the cfDNA coverage BED both
    # validate as importable datasets.
    assert datasets["snv"].status == "valid"
    assert datasets["coverage"].status == "valid"


def test_discover_preserves_explicit_snv_and_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yaml

    monkeypatch.setattr(settings, "family_import_roots", [])
    result = discover_family_package_manifest(
        FamilyPackageManifestBuildRequest(folder_path=str(_DEMO_DIR))
    )
    manifest = yaml.safe_load(result.manifest_yaml)
    # The scanner's disabled auto-detected snv block must not clobber the
    # manifest's explicit combined-VCF dataset; coverage is augmented in.
    assert manifest["datasets"]["snv"]["family_vcf"] == "nipt_combined.vcf"
    assert (
        manifest["datasets"]["coverage"]["per_sample"]["CFDNA_NIPT"]["bed"]
        == "nipt_coverage.bed"
    )

    # The availability table reflects the manifest's datasets as enabled/available
    # (not "not enabled" just because the filenames don't match scanner patterns).
    availability = {item.dataset_type: item for item in result.datasets}
    assert availability["snv"].enabled and availability["snv"].complete
    assert availability["coverage"].enabled and availability["coverage"].complete
