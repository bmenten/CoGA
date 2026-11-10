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
from backend.tests._kept_manifest import kept_manifest

_DEMO_DIR = Path(__file__).resolve().parents[2] / "demo" / "nipt_family"


def test_the_scan_lists_the_folders_that_can_be_added_by_hand(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    nipt = tmp_path / "FAM_NIPT_DEMO"
    nipt.mkdir()
    (nipt / "nipt_trio.ped").write_text("FAM_NIPT_DEMO\tF\t0\t0\t1\t1\n")
    # A manifest alone makes no package: CoGA reads none in a folder.
    manifest_only = tmp_path / "MANIFEST_ONLY"
    manifest_only.mkdir()
    (manifest_only / "manifest.yaml").write_text("schema_version: 1\nfamily_id: MANIFEST_ONLY\n")
    # A folder with its own package file is delivered through a watched folder.
    delivered = tmp_path / "DELIVERED"
    delivered.mkdir()
    (delivered / "family.ped").write_text("DELIVERED\tP\t0\t0\t1\t2\n")
    (delivered / "coga-package.json").write_text("{}")
    (tmp_path / "not-a-package").mkdir()
    # A folder in a project folder, as a pipeline delivers it.
    in_project = tmp_path / "WGS" / "FAM_IN_PROJECT"
    in_project.mkdir(parents=True)
    (in_project / "family.ped").write_text("FAM_IN_PROJECT\tP\t0\t0\t1\t2\n")

    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path)])

    packages = {pkg["name"]: pkg for pkg in scan_family_import_packages()}

    assert packages == {
        "FAM_NIPT_DEMO": {
            "folder_path": str(nipt.resolve()),
            "name": "FAM_NIPT_DEMO",
            "family_id": "FAM_NIPT_DEMO",
            "has_ped": True,
            "project_folder": None,
        },
        "FAM_IN_PROJECT": {
            "folder_path": str(in_project.resolve()),
            "name": "FAM_IN_PROJECT",
            "family_id": "FAM_IN_PROJECT",
            "has_ped": True,
            "project_folder": "WGS",
        },
    }


def test_scan_returns_empty_without_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "family_import_roots", [])
    assert scan_family_import_packages() == []


def test_manifest_accepts_analysis_type() -> None:
    manifest = PackageManifest(analysis_type="monogenic_nipt")
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
    validation, _bundle = load_validated_family_package(_DEMO_DIR, manifest_text=kept_manifest(_DEMO_DIR))

    assert validation.valid, validation.errors
    assert validation.family_id == "FAM_NIPT_DEMO"
    datasets = {summary.dataset_type: summary for summary in validation.datasets}
    # The combined (uncompressed, unindexed) VCF and the cfDNA coverage BED both
    # validate as importable datasets.
    assert datasets["snv"].status == "valid"
    assert datasets["coverage"].status == "valid"


_PAIR_PED = (
    "NIPTPAIR\tFATHER1\t0\t0\t1\t1\n"
    "NIPTPAIR\tCFDNA1\t0\t0\t2\t1\n"
    "NIPTPAIR\tFETUS1\tFATHER1\tCFDNA1\t0\t2\n"
)
_COVERAGE_HEADER = (
    "#build\tchromosome\tstart\tend\tattribute\tlength\tmin\tmax\tmean\tmedian\tstdev\t"
    "zero_coverage_bases\tproportion_covered\n"
)


def _one_sample_vcf(sample: str) -> str:
    return (
        "##fileformat=VCFv4.2\n"
        f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample}\n"
        "chr7\t1000\t.\tA\tG\t.\tPASS\tTLOD=300\tGT:AD:DP\t0/1:900,100:1000\n"
    )


def _write_pair_folder(folder: Path, *, manifest: str | None = None) -> None:
    import gzip

    folder.mkdir()
    (folder / "nipt_trio.ped").write_text(_PAIR_PED)
    for sample in ("CFDNA1", "FATHER1"):
        with gzip.open(folder / f"{sample}.mutect2.vcf.gz", "wt") as handle:
            handle.write(_one_sample_vcf(sample))
    (folder / "coverage_CFDNA1.txt").write_text(
        _COVERAGE_HEADER + 'hg38\tchr7\t900\t1100\t"GENEA;NM_1.1;ENST1;ENSE1;1"\t200\t800\t1500\t1100,5\t1102\t20,1\t0\t100\n'
    )
    if manifest is not None:
        (folder / "manifest.yaml").write_text(manifest)


def test_discover_proposes_a_nipt_manifest_for_a_parent_pair(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    import yaml

    monkeypatch.setattr(settings, "family_import_roots", [])
    package = tmp_path / "NIPTPAIR"
    _write_pair_folder(package)
    result = discover_family_package_manifest(FamilyPackageManifestBuildRequest(folder_path=str(package)))
    manifest = yaml.safe_load(result.manifest_yaml)
    assert manifest["analysis_type"] == "monogenic_nipt"
    assert manifest["samples"]["CFDNA1"] == {"assay": "nipt_cfdna"}
    assert manifest["datasets"]["snv"]["per_sample"] == {
        "CFDNA1": {"vcf": "CFDNA1.mutect2.vcf.gz"},
        "FATHER1": {"vcf": "FATHER1.mutect2.vcf.gz"},
    }
    assert manifest["datasets"]["coverage"]["per_sample"] == {"CFDNA1": {"target_table": "coverage_CFDNA1.txt"}}
    assert any(issue.code == "nipt_pair_detected" for issue in result.warnings)
    availability = {item.dataset_type: item for item in result.datasets}
    assert availability["snv"].complete and availability["coverage"].complete


_PAIR_MANIFEST = (
    "schema_version: 1\n"
    "family_id: NIPTPAIR\n"
    "ped: nipt_trio.ped\n"
    "{analysis_type}"
    "samples:\n"
    "  CFDNA1: {{assay: nipt_cfdna, assay_panel: PANEL1}}\n"
    "datasets:\n"
    "  snv:\n"
    "    per_sample:\n"
    "      CFDNA1: {{vcf: CFDNA1.mutect2.vcf.gz}}\n"
    "      FATHER1: {{vcf: FATHER1.mutect2.vcf.gz}}\n"
    "  coverage:\n"
    "    per_sample:\n"
    "      CFDNA1: {{target_table: coverage_CFDNA1.txt}}\n"
)


def test_a_nipt_pair_package_validates(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "family_import_roots", [])
    package = tmp_path / "NIPTPAIR"
    _write_pair_folder(package, manifest=_PAIR_MANIFEST.format(analysis_type="analysis_type: monogenic_nipt\n"))
    validation, _bundle = load_validated_family_package(package, manifest_text=kept_manifest(package))
    assert validation.valid, validation.errors
    datasets = {summary.dataset_type: summary for summary in validation.datasets}
    assert datasets["snv"].status == "valid" and datasets["snv"].samples == ["CFDNA1", "FATHER1"]
    assert datasets["coverage"].status == "valid"


def test_a_per_sample_snv_callset_is_read_only_for_a_nipt_family(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "family_import_roots", [])
    package = tmp_path / "NIPTPAIR"
    _write_pair_folder(package, manifest=_PAIR_MANIFEST.format(analysis_type=""))
    validation, _bundle = load_validated_family_package(package, manifest_text=kept_manifest(package))
    assert not validation.valid
    assert any(issue.code == "dataset_per_sample_unsupported" for issue in validation.errors)


def test_a_per_sample_file_must_hold_one_sample_and_a_table_its_columns(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import gzip

    monkeypatch.setattr(settings, "family_import_roots", [])
    package = tmp_path / "NIPTPAIR"
    _write_pair_folder(package, manifest=_PAIR_MANIFEST.format(analysis_type="analysis_type: monogenic_nipt\n"))
    with gzip.open(package / "FATHER1.mutect2.vcf.gz", "wt") as handle:
        handle.write(_one_sample_vcf("FATHER1").replace("\tFATHER1\n", "\tFATHER1\tCFDNA1\n"))
    (package / "coverage_CFDNA1.txt").write_text("#build\tchromosome\tstart\tend\n")
    validation, _bundle = load_validated_family_package(package, manifest_text=kept_manifest(package))
    codes = {issue.code for issue in validation.errors}
    assert "dataset_vcf_not_single_sample" in codes
    assert "coverage_target_table_columns" in codes


# --------------------------------------------------------------------------- #
# The column a NIPT file is read as
# --------------------------------------------------------------------------- #
#
# The import reads a NIPT per-sample SNV file's one sample column as its entry's sample, whatever
# the column is named: the laboratory maps its verified tubes to files named after other tubes.
# The owner decided on 2026-10-28 to warn, not refuse, when the column is named neither as the
# member nor as the entry's vcf_sample. The dry run and the import validate the package alike;
# Discover checks the manifest it drafts the same way.

_COLUMN_WARNING = "nipt_vcf_column_named_otherwise"


def _pair_whose_father_column_is(
    tmp_path,
    column: str,
    *,
    father_entry: str = "",
    father_file: str = "FATHER1.mutect2.vcf.gz",
    manifest: bool = True,
) -> Path:
    """The pair, the father's ``father_file`` holding one column, ``column``, and a manifest
    mapping him to that file (``father_entry`` adds to his entry), unless ``manifest`` is
    False. The folder holds a FATHER1.mutect2.vcf.gz named after him either way."""
    import gzip

    package = tmp_path / "NIPTPAIR"
    text = _PAIR_MANIFEST.format(analysis_type="analysis_type: monogenic_nipt\n").replace(
        "FATHER1: {vcf: FATHER1.mutect2.vcf.gz}", f"FATHER1: {{vcf: {father_file}{father_entry}}}"
    )
    _write_pair_folder(package, manifest=text if manifest else None)
    with gzip.open(package / father_file, "wt") as handle:
        handle.write(_one_sample_vcf(column))
    return package


def _column_warnings(package: Path) -> list:
    validation, _bundle = load_validated_family_package(package, manifest_text=kept_manifest(package))
    # A warning, never a refusal: the package validates.
    assert validation.valid, validation.errors
    assert {summary.dataset_type: summary.status for summary in validation.datasets}["snv"] == "valid"
    return [issue for issue in validation.warnings if issue.code == _COLUMN_WARNING]


_SET_VCF_SAMPLE = (
    ": the import reads that column as FATHER1's calls. If the laboratory verified that this file "
    "holds FATHER1's tube, set the entry's vcf_sample to '{column}'; otherwise correct the manifest"
)


@pytest.mark.parametrize(
    ("column", "father_entry", "named"),
    [
        pytest.param("TUBE9", "", "'TUBE9', not FATHER1", id="another-tube"),
        pytest.param(
            "CFDNA1", "", "'CFDNA1' after CFDNA1, another member of the family, not after FATHER1", id="another-member"
        ),
        pytest.param(
            "TUBE9",
            ", vcf_sample: TUBE7",
            "'TUBE9', neither FATHER1 nor its entry's vcf_sample 'TUBE7'",
            id="another-vcf-sample",
        ),
        pytest.param(
            "CFDNA1",
            ", vcf_sample: TUBE7",
            "'CFDNA1', neither FATHER1 nor its entry's vcf_sample 'TUBE7', but after CFDNA1, another member of the family",
            id="another-member-and-another-vcf-sample",
        ),
        # The entry's own sample confirms another per-sample file's one column; here the
        # decision names the column.
        pytest.param("TUBE9", ", vcf_sample: FATHER1", "'TUBE9', neither FATHER1 nor its entry's vcf_sample 'FATHER1'", id="own-id"),
    ],
)
def test_a_nipt_files_column_named_otherwise_is_warned_of_not_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path, column: str, father_entry: str, named: str
) -> None:
    monkeypatch.setattr(settings, "family_import_roots", [])
    package = _pair_whose_father_column_is(tmp_path, column, father_entry=father_entry)

    [warning] = _column_warnings(package)

    assert (warning.dataset, warning.sample_id) == ("snv", "FATHER1")
    assert warning.path == str(package / "FATHER1.mutect2.vcf.gz")
    assert warning.message == (
        f"The NIPT SNV file of FATHER1 (FATHER1.mutect2.vcf.gz) names its one sample column {named}"
        + _SET_VCF_SAMPLE.format(column=column)
    )


@pytest.mark.parametrize(
    ("column", "father_entry"),
    [
        pytest.param("FATHER1", "", id="named-as-the-member"),
        pytest.param("FATHER1_sorted", "", id="named-as-the-member-with-a-tool-suffix"),
        pytest.param("TUBE9", ", vcf_sample: TUBE9", id="named-in-the-entry"),
        pytest.param("TUBE9", ", sample_name: TUBE9", id="named-in-the-entry-as-sample-name"),
    ],
)
def test_a_nipt_files_column_named_as_the_member_or_in_its_entry_is_not_warned_of(
    monkeypatch: pytest.MonkeyPatch, tmp_path, column: str, father_entry: str
) -> None:
    monkeypatch.setattr(settings, "family_import_roots", [])
    package = _pair_whose_father_column_is(tmp_path, column, father_entry=father_entry)

    assert _column_warnings(package) == []


def test_discover_warns_of_the_column_of_a_nipt_file_it_finds(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    # The father's file is named after him and its one column after another tube: Discover
    # drafts the pair, and says so before the manifest is written.
    import yaml

    monkeypatch.setattr(settings, "family_import_roots", [])
    package = _pair_whose_father_column_is(tmp_path, "TUBE9", manifest=False)

    result = discover_family_package_manifest(FamilyPackageManifestBuildRequest(folder_path=str(package)))

    assert result.valid, result.errors
    assert yaml.safe_load(result.manifest_yaml)["datasets"]["snv"]["per_sample"] == {
        "CFDNA1": {"vcf": "CFDNA1.mutect2.vcf.gz"},
        "FATHER1": {"vcf": "FATHER1.mutect2.vcf.gz"},
    }
    [warning] = [issue for issue in result.warnings if issue.code == _COLUMN_WARNING]
    assert (warning.dataset, warning.sample_id) == ("snv", "FATHER1")
    assert warning.path == str(package / "FATHER1.mutect2.vcf.gz")
    assert warning.message == (
        "The NIPT SNV file of FATHER1 (FATHER1.mutect2.vcf.gz) names its one sample column 'TUBE9', not FATHER1"
        + _SET_VCF_SAMPLE.format(column="TUBE9")
    )


@pytest.mark.asyncio
async def test_the_import_reads_a_nipt_files_one_column_as_its_entrys_sample(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    # The column another tube's name gives is read as the father's calls. The
    # dataset's summary records which column each sample's calls were read from.
    from backend.app.schemas import FamilyImportDatasetSummary
    from backend.app.services import family_package_datasets
    from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext

    monkeypatch.setattr(settings, "family_import_roots", [])
    package = _pair_whose_father_column_is(tmp_path, "TUBE9")
    validation, bundle = load_validated_family_package(package, manifest_text=kept_manifest(package))
    assert bundle is not None
    bound: dict[str, dict[str, str]] = {}

    async def upload(_session, *, sample_aliases, **_kwargs):
        bound.update({sample_id: dict(sample_aliases) for sample_id in sample_aliases.values()})
        return {"inserted": 1}

    monkeypatch.setattr(family_package_datasets, "upload_family_small_variant_file", upload)
    contexts = {
        sample_id: SampleMetadataContext(
            sample_uuid=f"uuid-{sample_id}",
            sample_id=sample_id,
            family_uuid="family-uuid",
            family_id="NIPTPAIR",
            sex=sex,
            project_ids=["project-1"],
            assembly_id="assembly-1",
            assembly_name="GRCh38",
        )
        for sample_id, sex in (("FATHER1", "male"), ("CFDNA1", "female"), ("FETUS1", "und"))
    }
    job = family_package_datasets.DatasetImportJob(
        session=None,  # type: ignore[arg-type]
        bundle=bundle,
        dataset=bundle.manifest.datasets["snv"],
        summary=FamilyImportDatasetSummary(dataset_type="snv", status="valid"),
        family_context=FamilyMetadataContext(
            family_uuid="family-uuid",
            family_id="NIPTPAIR",
            project_ids=["project-1"],
            sample_rows=[],
            sample_uuid_to_name={f"uuid-{sample_id}": sample_id for sample_id in contexts},
            sample_name_to_uuid={sample_id: f"uuid-{sample_id}" for sample_id in contexts},
            affected_sample_names=[],
            assembly_id="assembly-1",
            assembly_name="GRCh38",
        ),
        sample_contexts=contexts,
    )

    result = await family_package_datasets.DATASET_IMPORTERS["snv"](job)

    assert result.status == "imported"
    assert bound == {"CFDNA1": {"CFDNA1": "CFDNA1"}, "FATHER1": {"TUBE9": "FATHER1"}}
    assert {sample_id: result.summary[sample_id]["vcf_column"] for sample_id in ("CFDNA1", "FATHER1")} == {
        "CFDNA1": "CFDNA1",
        "FATHER1": "TUBE9",
    }
    # The warning the import records, from the same validation as the dry run's.
    assert [issue.sample_id for issue in validation.warnings if issue.code == _COLUMN_WARNING] == ["FATHER1"]
