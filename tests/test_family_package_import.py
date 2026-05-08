from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from backend.app.services import clickhouse_family_variants as family_variants
from backend.app.services import family_package_import as package_import
from backend.app.schemas import FamilyPackageManifestBuildRequest
from backend.app.services.family_variant_filters import StructuralVariantQueryFilters
from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext
from backend.app.services.metadata_service import CurrentUser


def _write_minimal_package(root: Path, *, family_id: str | None = None) -> None:
    resolved_family_id = family_id or root.name
    root.mkdir(parents=True, exist_ok=True)
    (root / "family.ped").write_text(
        f"{resolved_family_id} S1 0 0 1 2\n{resolved_family_id} S2 0 0 2 1\n",
        encoding="utf-8",
    )
    (root / "manifest.yaml").write_text(
        "schema_version: 1\n"
        f"family_id: {resolved_family_id}\n"
        "ped: family.ped\n"
        "metadata:\n"
        "  hpo:\n"
        "    - HP:0001250\n",
        encoding="utf-8",
    )


def _current_admin() -> CurrentUser:
    return CurrentUser(
        id="00000000-0000-0000-0000-000000000001",
        username="admin@example.com",
        email="admin@example.com",
        role="admin",
        created_at=datetime.now(timezone.utc),
    )


def _sample_context(sample_id: str) -> SampleMetadataContext:
    return SampleMetadataContext(
        sample_uuid=f"uuid-{sample_id}",
        sample_id=sample_id,
        family_uuid="family-uuid",
        family_id="FAM001",
        sex="und",
        project_ids=["project-uuid"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


def test_manifest_parsing_supports_yaml_and_optional_dataset_warnings(tmp_path: Path) -> None:
    package_root = tmp_path / "FAM001"
    _write_minimal_package(package_root)

    result = package_import.validate_family_package(package_root)

    assert result.valid is True
    assert result.family_id == "FAM001"
    assert result.sample_ids == ["S1", "S2"]
    assert result.metadata["schema_version"] == 1
    assert {warning.code for warning in result.warnings} == {"optional_dataset_missing"}
    assert {summary.dataset_type for summary in result.datasets} == set(package_import.SUPPORTED_DATASETS)


def test_family_id_defaults_to_folder_name_and_must_match_ped(tmp_path: Path) -> None:
    package_root = tmp_path / "FOLDER_FAM"
    package_root.mkdir()
    (package_root / "family.ped").write_text("PED_FAM S1 0 0 1 2\n", encoding="utf-8")
    (package_root / "manifest.yaml").write_text("schema_version: 1\nped: family.ped\n", encoding="utf-8")

    result = package_import.validate_family_package(package_root)

    assert result.valid is False
    assert result.family_id == "FOLDER_FAM"
    assert "ped_family_mismatch" in {error.code for error in result.errors}


def test_missing_referenced_files_are_validation_errors(tmp_path: Path) -> None:
    package_root = tmp_path / "FAM001"
    _write_minimal_package(package_root)
    (package_root / "manifest.yaml").write_text(
        "schema_version: 1\n"
        "family_id: FAM001\n"
        "ped: family.ped\n"
        "datasets:\n"
        "  snv:\n"
        "    enabled: true\n"
        "    family_vcf: snv/family.annotated.vcf.gz\n",
        encoding="utf-8",
    )

    result = package_import.validate_family_package(package_root)

    assert result.valid is False
    codes = {error.code for error in result.errors}
    assert "dataset_file_missing" in codes
    assert "dataset_vcf_index_missing" in codes


def test_dataset_sample_id_mismatches_are_validation_errors(tmp_path: Path) -> None:
    package_root = tmp_path / "FAM001"
    _write_minimal_package(package_root)
    wise_root = package_root / "wisecondorx" / "S3"
    wise_root.mkdir(parents=True)
    (wise_root / "bins.bed").write_text("1\t0\t10\t0.1\n", encoding="utf-8")
    (wise_root / "segments.bed").write_text("1\t0\t10\t0.1\n", encoding="utf-8")
    (package_root / "manifest.yaml").write_text(
        "schema_version: 1\n"
        "family_id: FAM001\n"
        "ped: family.ped\n"
        "datasets:\n"
        "  wisecondorx:\n"
        "    enabled: true\n"
        "    per_sample:\n"
        "      S3:\n"
        "        bins: wisecondorx/S3/bins.bed\n"
        "        segments: wisecondorx/S3/segments.bed\n",
        encoding="utf-8",
    )

    result = package_import.validate_family_package(package_root)

    assert result.valid is False
    assert "dataset_unknown_sample" in {error.code for error in result.errors}


def test_vcf_index_checks_accept_manifest_index(tmp_path: Path) -> None:
    package_root = tmp_path / "FAM001"
    _write_minimal_package(package_root)
    snv_root = package_root / "snv"
    snv_root.mkdir()
    (snv_root / "family.annotated.vcf.gz").write_text("##fileformat=VCFv4.2\n", encoding="utf-8")
    (snv_root / "family.annotated.vcf.gz.tbi").write_text("index\n", encoding="utf-8")
    (package_root / "manifest.yaml").write_text(
        "schema_version: 1\n"
        "family_id: FAM001\n"
        "ped: family.ped\n"
        "datasets:\n"
        "  snv:\n"
        "    enabled: true\n"
        "    family_vcf: snv/family.annotated.vcf.gz\n"
        "    index: snv/family.annotated.vcf.gz.tbi\n",
        encoding="utf-8",
    )

    result = package_import.validate_family_package(package_root)

    assert result.valid is True
    snv_summary = next(summary for summary in result.datasets if summary.dataset_type == "snv")
    assert snv_summary.status == "valid"
    assert snv_summary.files == ["snv/family.annotated.vcf.gz", "snv/family.annotated.vcf.gz.tbi"]


def test_snv_dataset_accepts_optional_vep_annotation_tsv(tmp_path: Path) -> None:
    package_root = tmp_path / "FAM001"
    _write_minimal_package(package_root)
    snv_root = package_root / "snv"
    annotation_root = snv_root / "annotation"
    annotation_root.mkdir(parents=True)
    (snv_root / "family.vcf.gz").write_text("##fileformat=VCFv4.2\n", encoding="utf-8")
    (snv_root / "family.vcf.gz.tbi").write_text("index\n", encoding="utf-8")
    (annotation_root / "FAM001_annot.tsv.gz").write_text(
        "#Uploaded_variation\tLocation\tAllele\tGene\tFeature\tFeature_type\tConsequence\tIMPACT\tSYMBOL\n",
        encoding="utf-8",
    )
    (package_root / "manifest.yaml").write_text(
        "schema_version: 1\n"
        "family_id: FAM001\n"
        "ped: family.ped\n"
        "datasets:\n"
        "  snv:\n"
        "    enabled: true\n"
        "    family_vcf: snv/family.vcf.gz\n"
        "    index: snv/family.vcf.gz.tbi\n"
        "    annotation_tsv: snv/annotation/FAM001_annot.tsv.gz\n",
        encoding="utf-8",
    )

    result = package_import.validate_family_package(package_root)

    assert result.valid is True
    snv_summary = next(summary for summary in result.datasets if summary.dataset_type == "snv")
    assert snv_summary.status == "valid"
    assert snv_summary.files == [
        "snv/family.vcf.gz",
        "snv/family.vcf.gz.tbi",
        "snv/annotation/FAM001_annot.tsv.gz",
    ]


def test_uncompressed_trgt_family_vcf_does_not_require_index(tmp_path: Path) -> None:
    package_root = tmp_path / "FAM001"
    _write_minimal_package(package_root)
    repeat_root = package_root / "repeats"
    repeat_root.mkdir()
    (repeat_root / "FAM001_tr.vcf").write_text(
        "##fileformat=VCFv4.2\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\tS2\n",
        encoding="utf-8",
    )
    (package_root / "manifest.yaml").write_text(
        "schema_version: 1\n"
        "family_id: FAM001\n"
        "ped: family.ped\n"
        "datasets:\n"
        "  repeats_trgt:\n"
        "    enabled: true\n"
        "    family_vcf: repeats/FAM001_tr.vcf\n",
        encoding="utf-8",
    )

    result = package_import.validate_family_package(package_root)

    assert result.valid is True
    summary = next(summary for summary in result.datasets if summary.dataset_type == "repeats_trgt")
    assert summary.status == "valid"
    assert summary.files == ["repeats/FAM001_tr.vcf"]


def test_unsupported_dataset_types_are_clear_errors(tmp_path: Path) -> None:
    package_root = tmp_path / "FAM001"
    _write_minimal_package(package_root)
    (package_root / "manifest.yaml").write_text(
        "schema_version: 1\n"
        "family_id: FAM001\n"
        "ped: family.ped\n"
        "datasets:\n"
        "  made_up_dataset:\n"
        "    enabled: true\n",
        encoding="utf-8",
    )

    result = package_import.validate_family_package(package_root)

    assert result.valid is False
    assert result.errors[0].code == "dataset_unsupported"
    assert result.errors[0].dataset == "made_up_dataset"


def test_wisecondorx_per_sample_validation_requires_bins_and_segments(tmp_path: Path) -> None:
    package_root = tmp_path / "FAM001"
    _write_minimal_package(package_root)
    wise_root = package_root / "wisecondorx" / "S1"
    wise_root.mkdir(parents=True)
    (wise_root / "bins.bed").write_text("1\t0\t10\t0.1\n", encoding="utf-8")
    (wise_root / "segments.bed").write_text("1\t0\t10\t0.1\n", encoding="utf-8")
    (package_root / "manifest.yaml").write_text(
        "schema_version: 1\n"
        "family_id: FAM001\n"
        "ped: family.ped\n"
        "datasets:\n"
        "  wisecondorx:\n"
        "    enabled: true\n"
        "    per_sample:\n"
        "      S1:\n"
        "        bins: wisecondorx/S1/bins.bed\n"
        "        segments: wisecondorx/S1/segments.bed\n",
        encoding="utf-8",
    )

    result = package_import.validate_family_package(package_root)

    assert result.valid is True
    summary = next(summary for summary in result.datasets if summary.dataset_type == "wisecondorx")
    assert summary.samples == ["S1"]
    assert summary.files == ["wisecondorx/S1/bins.bed", "wisecondorx/S1/segments.bed"]


def test_discover_manifest_generates_yaml_from_ped_and_available_files(tmp_path: Path) -> None:
    package_root = tmp_path / "FAM001"
    _write_minimal_package(package_root)
    (package_root / "snv").mkdir()
    (package_root / "snv" / "annotation").mkdir()
    (package_root / "snv" / "FAM001.annotated.vcf.gz").write_text("vcf\n", encoding="utf-8")
    (package_root / "snv" / "FAM001.annotated.vcf.gz.tbi").write_text("index\n", encoding="utf-8")
    (package_root / "snv" / "annotation" / "FAM001_annot.tsv.gz").write_text("tsv\n", encoding="utf-8")
    wise_root = package_root / "wisecondorx" / "S1"
    wise_root.mkdir(parents=True)
    (wise_root / "bins.bed").write_text("1\t0\t10\t0.1\n", encoding="utf-8")
    (wise_root / "segments.bed").write_text("1\t0\t10\t0.1\n", encoding="utf-8")

    result = package_import.discover_family_package_manifest(
        FamilyPackageManifestBuildRequest(
            folder_path=str(package_root),
            ped_path="family.ped",
            hpo_terms=["HP:0001250"],
            notes="Example",
        )
    )

    assert result.valid is True
    assert result.family_id == "FAM001"
    assert result.sample_ids == ["S1", "S2"]
    assert "hpo:" in result.manifest_yaml
    assert "HP:0001250" in result.manifest_yaml
    assert "snv:" in result.manifest_yaml
    assert "enabled: true" in result.manifest_yaml
    assert "annotation_tsv: snv/annotation/FAM001_annot.tsv.gz" in result.manifest_yaml
    snv = next(dataset for dataset in result.datasets if dataset.dataset_type == "snv")
    assert snv.complete is True
    wise = next(dataset for dataset in result.datasets if dataset.dataset_type == "wisecondorx")
    assert wise.samples == ["S1"]


def test_discover_manifest_detects_uncompressed_trgt_family_vcf(tmp_path: Path) -> None:
    package_root = tmp_path / "FAM001"
    _write_minimal_package(package_root)
    repeat_root = package_root / "repeats"
    repeat_root.mkdir()
    (repeat_root / "FAM001_tr.vcf").write_text(
        "##fileformat=VCFv4.2\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\tS2\n",
        encoding="utf-8",
    )

    result = package_import.discover_family_package_manifest(
        FamilyPackageManifestBuildRequest(
            folder_path=str(package_root),
            ped_path="family.ped",
        )
    )

    manifest = yaml.safe_load(result.manifest_yaml)
    repeat_block = manifest["datasets"]["repeats_trgt"]
    assert repeat_block == {
        "enabled": True,
        "family_vcf": "repeats/FAM001_tr.vcf",
    }
    repeats = next(dataset for dataset in result.datasets if dataset.dataset_type == "repeats_trgt")
    assert repeats.complete is True


def test_write_manifest_creates_manifest_yaml_and_validates_package(tmp_path: Path) -> None:
    package_root = tmp_path / "FAM001"
    _write_minimal_package(package_root)
    (package_root / "manifest.yaml").unlink()
    manifest_yaml = (
        "schema_version: 1\n"
        "family_id: FAM001\n"
        "ped: family.ped\n"
        "datasets:\n"
        "  snv:\n"
        "    enabled: false\n"
    )

    result = package_import.write_family_package_manifest(
        folder_path=package_root,
        manifest_yaml=manifest_yaml,
        overwrite=False,
    )

    assert Path(result.manifest_path).read_text(encoding="utf-8") == manifest_yaml
    assert result.validation.valid is True


def test_wisecondorx_parser_handles_headers_and_skips_nan_values(tmp_path: Path) -> None:
    path = tmp_path / "S1_bins.bed"
    path.write_text(
        "chr\tstart\tend\tid\tratio\tzscore\n"
        "1\t1\t10000\t1:1-10000\tnan\tnan\n"
        "1\t10001\t20000\t1:10001-20000\t-0.25\t-1.5\n",
        encoding="utf-8",
    )
    header = {"chr": 0, "start": 1, "end": 2, "id": 3, "ratio": 4, "zscore": 5}

    skipped = package_import._parse_wisecondorx_interval_row(
        ["1", "1", "10000", "1:1-10000", "nan", "nan"],
        header=header,
        sample_context=_sample_context("S1"),
        track_type="coverage",
        path=path,
        line_no=2,
    )
    parsed = package_import._parse_wisecondorx_interval_row(
        ["1", "10001", "20000", "1:10001-20000", "-0.25", "-1.5"],
        header=header,
        sample_context=_sample_context("S1"),
        track_type="coverage",
        path=path,
        line_no=3,
    )

    assert skipped is None
    assert parsed is not None
    assert parsed["track_type"] == "coverage"
    assert parsed["source"] == "wisecondorx"
    assert parsed["chr"] == "1"
    assert parsed["record_id"] == "1:10001-20000"
    assert parsed["value"] == -0.25
    assert json.loads(parsed["metadata_json"])["zscore"] == -1.5


def test_needlr_family_vcf_parser_builds_proband_and_parent_calls() -> None:
    ped, errors = package_import._parse_ped_text_strict(
        "FAM001 PROBAND FATHER MOTHER 1 2\n"
        "FAM001 MOTHER 0 0 2 1\n"
        "FAM001 FATHER 0 0 1 1\n"
    )
    assert errors == []
    text_value = (
        "##fileformat=VCFv4.2\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr1\t100\t.\tN\t<DEL>\t50\tPASS\t"
        "SVTYPE=DEL;SVLEN=-25;End_Pos=125;Query_ID=PROBAND_sv;"
        "Genotype=0/1;Alt_Reads=8;Maternal_GT=0/0;Maternal_Alt_Reads=0;"
        "Paternal_GT=0/1;Paternal_Alt_Reads=6;Genes=GENE1,GENE2\n"
    )
    sample_contexts = {
        sample_id: _sample_context(sample_id)
        for sample_id in ["PROBAND", "MOTHER", "FATHER"]
    }

    records = package_import._iter_needlr_structural_records(
        text_value,
        ped=ped,
        sample_contexts=sample_contexts,
    )

    assert len(records) == 1
    record = records[0]
    assert record.chr == "1"
    assert record.start == 100
    assert record.end == 125
    assert record.sv_type == "DEL"
    assert record.sv_len == -25
    assert record.gene_symbols == ["GENE1", "GENE2"]
    assert [(call.sample, call.gt, call.read_support) for call in record.calls] == [
        ("FATHER", "0/1", 6),
        ("MOTHER", "0/0", 0),
        ("PROBAND", "0/1", 8),
    ]


def test_needlr_annotations_are_exposed_for_structural_variants() -> None:
    ped, errors = package_import._parse_ped_text_strict(
        "FAM001 PROBAND FATHER MOTHER 1 2\n"
        "FAM001 MOTHER 0 0 2 1\n"
        "FAM001 FATHER 0 0 1 1\n"
    )
    assert errors == []
    text_value = (
        "##fileformat=VCFv4.2\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr1\t100\t.\tN\t<DEL>\t50\tPASS\t"
        "SVTYPE=DEL;SVLEN=-25;End_Pos=125;Query_ID=PROBAND_sv;"
        "Inheritance=de_novo;Genotype=0/1;Alt_Reads=8;"
        "Genes=SMN1;OMIM_phenotype=Spinal_muscular_atrophy;"
        "GENCC_phenotype=Spinal_muscular_atrophy;GENCC_support=Definitive;"
        "GENCC_MOI=Autosomal_recessive;HPO_terms=HP:0001250,HP:0002650;"
        "pLI=0.98;CDS=TRUE;Segdup=TRUE;Allele_Freq_ALL_Control=0.0005;"
        "Allele_Freq_ALL=0.001;GT_het=2;HWE=TRUE\n"
    )
    sample_contexts = {"PROBAND": _sample_context("PROBAND")}
    record = package_import._iter_needlr_structural_records(
        text_value,
        ped=ped,
        sample_contexts=sample_contexts,
    )[0]

    variant = family_variants._structural_variant_out(record, selected_samples=[])

    assert variant.gene == "SMN1"
    assert variant.gene_pli == 0.98
    assert variant.annotation_extra["inheritance"] == "de_novo"
    assert variant.annotation_extra["gencc_support"] == "Definitive"
    assert variant.annotation_extra["control_af"] == 0.0005
    assert variant.annotation_extra["population_af"] == 0.001
    assert variant.annotation_extra["region_flags"] == ["CDS", "Segdup"]


def test_structural_variant_annotation_filters_match_needlr_fields() -> None:
    ped, errors = package_import._parse_ped_text_strict("FAM001 PROBAND 0 0 1 2\n")
    assert errors == []
    text_value = (
        "##fileformat=VCFv4.2\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr1\t100\t.\tN\t<DEL>\t50\tPASS\t"
        "SVTYPE=DEL;SVLEN=-25;End_Pos=125;Query_ID=PROBAND;"
        "Inheritance=de_novo;Genotype=0/1;Alt_Reads=8;Genes=SMN1;"
        "OMIM_phenotype=Spinal_muscular_atrophy;HPO_terms=HP:0001250;"
        "GENCC_support=Definitive;pLI=0.98;CDS=TRUE;Allele_Freq_ALL_Control=0.0005\n"
    )
    record = package_import._iter_needlr_structural_records(
        text_value,
        ped=ped,
        sample_contexts={"PROBAND": _sample_context("PROBAND")},
    )[0]
    filters = StructuralVariantQueryFilters(
        page=1,
        page_size=100,
        inheritance="de_novo",
        phenotype="muscular",
        hpo="HP:0001250",
        gencc_support="Definitive",
        region_flags=["CDS"],
        max_control_af=0.001,
        min_pli=0.9,
    )

    assert family_variants._structural_record_matches(record, filters, [], []) is True

    filters.max_control_af = 0.0001
    assert family_variants._structural_record_matches(record, filters, [], []) is False


def test_paraphase_rows_store_gene_level_json_payload(tmp_path: Path) -> None:
    path = tmp_path / "S1.paraphase.json"
    rows = package_import._paraphase_rows_for_sample(
        sample_context=_sample_context("S1"),
        path=path,
        payload={
            "GBA": {
                "total_cn": 2,
                "gene_cn": None,
                "highest_total_cn": 2,
                "sample_sex": "male",
                "phase_region": "38:chr1:10-20",
                "region_depth": {"median": 42},
                "genome_depth": 33.5,
                "final_haplotypes": {"h1": "GBA_hap1"},
            }
        },
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["gene_symbol"] == "GBA"
    assert row["total_cn"] == 2
    assert row["phase_region"] == "38:chr1:10-20"
    assert json.loads(row["region_depth_json"]) == {"median": 42}
    assert json.loads(row["payload_json"])["final_haplotypes"] == {"h1": "GBA_hap1"}


@pytest.mark.asyncio
async def test_dry_run_validates_without_database_session(tmp_path: Path) -> None:
    package_root = tmp_path / "FAM001"
    _write_minimal_package(package_root)

    result = await package_import.execute_family_package_import(
        None,
        folder_path=package_root,
        project_id=None,
        dry_run=True,
        user=None,
    )

    assert result.completed is True
    assert result.error is None
    assert "Dry run completed successfully" in result.logs[-1]


@pytest.mark.asyncio
async def test_successful_minimal_import_registers_family(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    package_root = tmp_path / "FAM001"
    _write_minimal_package(package_root)
    calls: list[tuple[str, str | None]] = []

    async def fake_ensure_family_from_ped(session, *, bundle, project_id, user, validation, conflict_mode="cancel"):
        calls.append((validation.family_id or "", project_id))
        return FamilyMetadataContext(
            family_uuid="family-uuid",
            family_id=validation.family_id or "",
            project_ids=[project_id or "project-uuid"],
            sample_rows=[
                {
                    "sample_uuid": "sample-1",
                    "sample_id": "S1",
                    "sex": "male",
                    "role": "proband",
                    "affected": True,
                },
                {
                    "sample_uuid": "sample-2",
                    "sample_id": "S2",
                    "sex": "female",
                    "role": "sibling",
                    "affected": False,
                },
            ],
            sample_uuid_to_name={"sample-1": "S1", "sample-2": "S2"},
            sample_name_to_uuid={"S1": "sample-1", "S2": "sample-2"},
            affected_sample_names=["S1"],
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
        )

    monkeypatch.setattr(package_import, "_ensure_family_from_ped", fake_ensure_family_from_ped)

    result = await package_import.execute_family_package_import(
        object(),  # type: ignore[arg-type]
        folder_path=package_root,
        project_id="project-uuid",
        dry_run=False,
        user=_current_admin(),
    )

    assert result.completed is True
    assert result.family_id == "FAM001"
    assert calls == [("FAM001", "project-uuid")]
