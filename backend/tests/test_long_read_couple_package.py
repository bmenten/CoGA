"""A long-read (nf-core/lrsvar) package of a couple screened for carriership.

The pipeline writes no PED and no joint callset for a couple: each partner has their own
annotated SNV VCF (``snv/<S>/annotation/<S>_annot.vcf.gz``), NeedlR SV VCF
(``sv/<S>/needlr/<S>_sv_phased.needLR.<version>.vcf.gz``), TRGT VCF and Paraphase JSON.
Discovery takes the members from the per-sample folders, sexed by the karyotype TRGT ran
with, proposes the couple, and drafts the per-sample callsets; validation accepts the
members named in the manifest; the import reads each partner's files as theirs. All
samples and values here are synthetic.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.core.config import settings
from backend.app.schemas import FamilyImportDatasetSummary, FamilyPackageManifestBuildRequest
from backend.app.services import family_package_datasets, sample_integrity_service
from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext
from backend.app.services.family_package_common import ManifestDataset, ParsedPed, PedMember, VcfSampleColumnError
from backend.app.services.family_package_discovery import discover_family_package_manifest, write_family_package_manifest
from backend.app.services.family_package_long_read import (
    long_read_family_block,
    long_read_sample_ids,
    trgt_karyotype_sex,
)
from backend.app.services.family_package_source import scan_family_import_packages
from backend.app.services.family_package_validation import _validate_dataset, load_validated_family_package
from backend.app.services.family_package_variants import _iter_needlr_per_sample_records

MOTHER, FATHER = "MOTHER1", "FATHER1"


def _gz(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt") as handle:
        handle.write(text)
    return path


def _touch(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _snv_vcf(sample: str) -> str:
    return (
        "##fileformat=VCFv4.2\n"
        '##FILTER=<ID=PASS,Description="All filters passed">\n'
        '##FILTER=<ID=RefCall,Description="Genotyping model thinks this site is reference.">\n'
        '##FILTER=<ID=NoCall,Description="Site has depth=0 resulting in no call.">\n'
        "##DeepVariant_version=1.10.0\n"
        f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample}_3500_4000\n"
        "chr1\t100\t.\tA\tG\t30\tPASS\t.\tGT\t0/1\n"
    )


def _trgt_vcf(sample: str, karyotype: str) -> str:
    return (
        "##fileformat=VCFv4.2\n##trgtVersion=5.0.0\n"
        f"##trgtCommand=trgt genotype --genome ref.fna --reads {sample}_sort.bam "
        f"--repeats catalog.bed --karyotype {karyotype} --threads 12 --output-prefix {sample}\n"
        f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample}_sort\n"
    )


def _needlr_vcf(sample: str, records: list[tuple[str, int, int, str, str, str]]) -> str:
    header = (
        "##fileformat=VCFv4.2\n"
        '##INFO=<ID=Genes,Number=.,Type=String,Description="Genes overlapped by the SV (gencode v45, +/-5kb)">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
    )
    lines = [
        f"{chrom}\t{start}\t.\tN\t<{sv_type}>\t.\t.\tSVLEN=-{end - start};SVTYPE={sv_type};End_Pos={end};"
        f"Query_ID={query}_sv_phased;Genotype={gt};Alt_Reads=8;Genes=GENE1\n"
        for chrom, start, end, sv_type, gt, query in records
    ]
    return header + "".join(lines)


def _write_couple_package(root: Path, *, karyotypes: tuple[str, str] = ("XX", "XY")) -> Path:
    for sample, karyotype in zip((MOTHER, FATHER), karyotypes):
        _gz(root / f"snv/{sample}/annotation/{sample}_annot.vcf.gz", _snv_vcf(sample))
        _touch(root / f"snv/{sample}/annotation/{sample}_annot.vcf.gz.tbi")
        _gz(root / f"repeats/{sample}/{sample}_tr.vcf.gz", _trgt_vcf(sample, karyotype))
        _touch(root / f"repeats/{sample}/{sample}_tr.vcf.gz.csi")
        _gz(
            root / f"sv/{sample}/needlr/{sample}_sv_phased.needLR.4.0.vcf.gz",
            _needlr_vcf(sample, [("chrX", 1000, 2000, "DEL", "0/1", sample)]),
        )
        _touch(root / f"sv/{sample}/needlr/{sample}_sv_phased.needLR.4.0.vcf.gz.tbi")
        _touch(root / f"sv/{sample}/needlr/{sample}_sv_phased.needLR.4.0_RESULTS.tsv")
        _touch(root / f"paraphase/{sample}/{sample}.paraphase.json", json.dumps({"smn1": {"smn1_cn": 1, "smn2_cn": 2}}))
        _touch(root / f"cnv/{sample}/{sample}_cnv_raw_bins.bed")
    return root


# --------------------------------------------------------------------------- #
# The members of a package without a PED
# --------------------------------------------------------------------------- #


def test_the_samples_are_the_per_sample_folders_that_hold_their_own_files(tmp_path: Path) -> None:
    root = _write_couple_package(tmp_path / "COUPLE1")
    # A folder whose files are not named after it is not a sample (an annotation folder,
    # a copy left beside the others).
    _touch(root / "snv/annotation/README.txt")
    _touch(root / "repeats/OTHER/notes.txt")
    assert long_read_sample_ids(root) == [FATHER, MOTHER]
    assert long_read_sample_ids(tmp_path / "missing") == []


@pytest.mark.parametrize(("karyotype", "sex"), [("XX", "female"), ("XY", "male")])
def test_the_sex_is_the_karyotype_trgt_ran_with(tmp_path: Path, karyotype: str, sex: str) -> None:
    path = _gz(tmp_path / "S_tr.vcf.gz", _trgt_vcf("S", karyotype))
    assert trgt_karyotype_sex(path) == sex


def test_a_trgt_vcf_without_a_karyotype_gives_no_sex(tmp_path: Path) -> None:
    path = _gz(tmp_path / "S_tr.vcf.gz", _trgt_vcf("S", "XX").replace(" --karyotype XX", ""))
    assert trgt_karyotype_sex(path) is None


def test_two_members_of_opposite_sex_are_proposed_as_a_couple(tmp_path: Path) -> None:
    root = _write_couple_package(tmp_path / "COUPLE1")
    block, warnings = long_read_family_block(root, [FATHER, MOTHER])
    assert block == {
        "add_members": [
            {"sample_id": FATHER, "sex": "male", "role": "father"},
            {"sample_id": MOTHER, "sex": "female", "role": "mother"},
        ],
        "relationships": {"couples": [{"partners": [MOTHER, FATHER], "context": "carrier screening"}]},
    }
    assert [issue.code for issue in warnings] == ["ped_proposed_from_folders"]
    assert f"{MOTHER} (female)" in warnings[0].message and f"{FATHER} (male)" in warnings[0].message


def test_members_of_unknown_or_one_sex_are_proposed_without_a_link(tmp_path: Path) -> None:
    root = _write_couple_package(tmp_path / "PAIR1", karyotypes=("XX", "XX"))
    block, warnings = long_read_family_block(root, [FATHER, MOTHER])
    assert "relationships" not in block
    assert [member["role"] for member in block["add_members"]] == ["relative", "relative"]
    assert "Nothing in the package says how they are related" in warnings[0].message

    (root / f"repeats/{FATHER}/{FATHER}_tr.vcf.gz").unlink()
    block, warnings = long_read_family_block(root, [FATHER, MOTHER])
    assert "sex" not in block["add_members"][0]
    assert "no recorded sex" in warnings[0].message


# --------------------------------------------------------------------------- #
# Discovery and validation of the couple's package
# --------------------------------------------------------------------------- #


def test_discover_drafts_the_couple_and_its_per_sample_callsets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path)])
    root = _write_couple_package(tmp_path / "COUPLE1")

    assert [package["name"] for package in scan_family_import_packages()] == ["COUPLE1"]
    out = discover_family_package_manifest(
        FamilyPackageManifestBuildRequest(folder_path=str(root), naming_scheme="standard_v1")
    )

    assert out.valid, out.errors
    assert out.ped_path is None
    assert out.sample_ids == [FATHER, MOTHER]
    manifest = __import__("yaml").safe_load(out.manifest_yaml)
    assert "ped" not in manifest
    assert manifest["family"]["relationships"]["couples"] == [
        {"partners": [MOTHER, FATHER], "context": "carrier screening"}
    ]
    snv = manifest["datasets"]["snv"]
    assert snv["source_format"] == "clair3"
    assert snv["exclude_filters"] == ["RefCall", "NoCall"]
    assert "family_vcf" not in snv
    assert snv["per_sample"][MOTHER] == {
        "vcf": f"snv/{MOTHER}/annotation/{MOTHER}_annot.vcf.gz",
        "index": f"snv/{MOTHER}/annotation/{MOTHER}_annot.vcf.gz.tbi",
    }
    assert manifest["datasets"]["sv_needlr"]["per_sample"][FATHER]["vcf"] == (
        f"sv/{FATHER}/needlr/{FATHER}_sv_phased.needLR.4.0.vcf.gz"
    )
    assert manifest["datasets"]["repeats_trgt"]["per_sample"][MOTHER]["file"] == f"repeats/{MOTHER}/{MOTHER}_tr.vcf.gz"
    assert manifest["datasets"]["paraphase"]["per_sample"][FATHER]["json"] == f"paraphase/{FATHER}/{FATHER}.paraphase.json"

    written = write_family_package_manifest(folder_path=root, manifest_yaml=out.manifest_yaml, overwrite=False)
    assert written.validation.valid, written.validation.errors
    validation, bundle = load_validated_family_package(root)
    assert validation.metadata["ped_source"] == "manifest"
    assert bundle is not None and bundle.ped_path is None
    sexes = {member.iid: (member.sex, member.role_hint) for member in bundle.ped.members}
    assert sexes == {FATHER: ("1", "father"), MOTHER: ("2", "mother")}


def test_a_manifest_without_a_ped_must_name_its_members(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "family_import_roots", [])
    root = _write_couple_package(tmp_path / "COUPLE1")
    _touch(root / "manifest.yaml", "schema_version: 1\nfamily_id: COUPLE1\ndatasets: {}\n")
    validation, bundle = load_validated_family_package(root)
    assert bundle is None
    assert "ped_missing_path" in {issue.code for issue in validation.errors}


def test_a_per_sample_snv_callset_outside_nipt_must_declare_its_primary_source(tmp_path: Path) -> None:
    _write_couple_package(tmp_path)
    per_sample = {
        sample: {"vcf": f"snv/{sample}/annotation/{sample}_annot.vcf.gz"} for sample in (MOTHER, FATHER)
    }
    errors: list = []
    _validate_dataset(
        root=tmp_path,
        dataset_type="snv",
        dataset=ManifestDataset(per_sample=per_sample),
        ped_sample_ids={MOTHER, FATHER},
        errors=errors,
    )
    # Without it the files could be a NIPT pair's whose analysis type was left out.
    assert [issue.code for issue in errors] == ["dataset_per_sample_unsupported"]

    errors = []
    summary = _validate_dataset(
        root=tmp_path,
        dataset_type="snv",
        dataset=ManifestDataset.model_validate({"per_sample": per_sample, "source_format": "clair3"}),
        ped_sample_ids={MOTHER, FATHER},
        errors=errors,
    )
    assert (summary.status, errors) == ("valid", [])


def test_needlr_can_be_declared_per_sample(tmp_path: Path) -> None:
    _write_couple_package(tmp_path)
    errors: list = []
    summary = _validate_dataset(
        root=tmp_path,
        dataset_type="sv_needlr",
        dataset=ManifestDataset(
            per_sample={FATHER: {"vcf": f"sv/{FATHER}/needlr/{FATHER}_sv_phased.needLR.4.0.vcf.gz"}}
        ),
        ped_sample_ids={MOTHER, FATHER},
        errors=errors,
    )
    assert (summary.status, summary.samples, errors) == ("valid", [FATHER], [])


# --------------------------------------------------------------------------- #
# The import reads each partner's files as theirs
# --------------------------------------------------------------------------- #


def _ped() -> ParsedPed:
    members = [
        PedMember(family_id="COUPLE1", iid=sample, pid="0", mid="0", sex=sex, phen="0", line_no=index + 1, clinical_status="unknown")
        for index, (sample, sex) in enumerate(((MOTHER, "2"), (FATHER, "1")))
    ]
    return ParsedPed(family_ids=["COUPLE1"], members=members, sample_ids=[MOTHER, FATHER], text="")


def _contexts() -> dict[str, SampleMetadataContext]:
    return {
        name: SampleMetadataContext(
            sample_uuid=f"{name.lower()}-uuid",
            sample_id=name,
            family_uuid="family-uuid",
            family_id="COUPLE1",
            sex="und",
            project_ids=["p1"],
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
        )
        for name in (MOTHER, FATHER)
    }


def test_each_needlr_file_binds_its_calls_to_its_sample() -> None:
    shared = ("chrX", 1000, 2000, "DEL", "0/1")
    records = _iter_needlr_per_sample_records(
        [
            (MOTHER, _needlr_vcf(MOTHER, [(*shared, MOTHER), ("chr7", 500, 900, "DEL", "1/1", MOTHER)])),
            # A query the alias rules cannot read is still this file's sample.
            (FATHER, _needlr_vcf(FATHER, [(*shared, "Sample0")])),
        ],
        ped=_ped(),
        sample_contexts=_contexts(),
    )
    calls = {(record.chr, record.start): sorted((call.sample, call.gt) for call in record.calls) for record in records}
    # One deletion called in both partners with the same alleles is one SV with both calls.
    assert calls == {
        ("X", 1000): [(FATHER, "0/1"), (MOTHER, "0/1")],
        ("7", 500): [(MOTHER, "1/1")],
    }


def _job(dataset: ManifestDataset, *, dataset_type: str) -> family_package_datasets.DatasetImportJob:
    return family_package_datasets.DatasetImportJob(
        session=SimpleNamespace(),  # type: ignore[arg-type]
        bundle=SimpleNamespace(root=Path("/package"), ped=_ped(), manifest=SimpleNamespace(analysis_type=None)),  # type: ignore[arg-type]
        dataset=dataset,
        summary=FamilyImportDatasetSummary(dataset_type=dataset_type, status="valid"),
        family_context=FamilyMetadataContext(
            family_uuid="family-uuid",
            family_id="COUPLE1",
            project_ids=["p1"],
            sample_rows=[],
            sample_uuid_to_name={},
            sample_name_to_uuid={},
            affected_sample_names=[],
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
        ),
        sample_contexts=_contexts(),
        conflict_mode="overwrite",
    )


@pytest.mark.asyncio
async def test_the_needlr_import_refuses_the_other_partners_file_before_any_write(monkeypatch: pytest.MonkeyPatch) -> None:
    writes: list[str] = []

    async def known(_session, **_kwargs) -> set[str]:
        return {MOTHER, FATHER}

    async def must_not_write(*_args, **_kwargs):
        writes.append("write")

    for name, fn in {
        "_resolve_package_path": lambda _root, value: Path(value) if value else None,
        # The father's file under the mother's entry.
        "_read_package_text": lambda _path: _needlr_vcf(FATHER, [("chrX", 1000, 2000, "DEL", "0/1", FATHER)]),
        "known_vcf_sample_ids": known,
        "lock_family_variant_writes": must_not_write,
        "replace_family_structural_variants": must_not_write,
    }.items():
        monkeypatch.setattr(family_package_datasets, name, fn)

    with pytest.raises(RuntimeError, match=f"'{FATHER}_sv_phased' is {FATHER}"):
        await family_package_datasets._import_sv_needlr_dataset(
            _job(ManifestDataset(per_sample={MOTHER: {"vcf": "sv/x.vcf.gz"}}), dataset_type="sv_needlr")
        )
    assert writes == []


@pytest.mark.asyncio
async def test_the_snv_import_refuses_the_other_partners_file_before_any_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    father_file = _gz(tmp_path / "father.vcf.gz", _snv_vcf(FATHER))
    writes: list[str] = []

    async def known(_session, **_kwargs) -> set[str]:
        return {MOTHER, FATHER}

    async def must_not_write(*_args, **_kwargs):
        writes.append("write")
        return {}

    monkeypatch.setattr(family_package_datasets, "_resolve_package_path", lambda _root, _value: father_file)
    monkeypatch.setattr(family_package_datasets, "known_vcf_sample_ids", known)
    monkeypatch.setattr(family_package_datasets, "upload_family_per_sample_small_variant_files", must_not_write)
    dataset = ManifestDataset.model_validate(
        {"per_sample": {MOTHER: {"vcf": "snv/x.vcf.gz"}}, "source_format": "clair3"}
    )

    with pytest.raises(VcfSampleColumnError, match=f"'{FATHER}_3500_4000' is {FATHER}"):
        await family_package_datasets._import_snv_dataset(_job(dataset, dataset_type="snv"))
    assert writes == []


# --------------------------------------------------------------------------- #
# Sample-integrity QC of a callset read one file per sample
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_qc_reads_a_partner_without_a_record_as_reference_and_an_unsequenced_member_as_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        ("1", 100, "A", "G", [MOTHER, FATHER], ["0/1", "1/1"]),
        ("1", 200, "C", "T", [MOTHER], ["0/1"]),
        ("1", 300, "G", "A", [FATHER], ["0/1"]),
    ]

    async def fake_sample(_context, *, scope, limit, source):
        return rows

    monkeypatch.setattr(sample_integrity_service, "fetch_genotype_site_sample", fake_sample)
    arrays: dict[str, Any] = await sample_integrity_service._load_genotype_sample(
        SimpleNamespace(), "autosomes", 10, [MOTHER, FATHER, "SIB1"], "clair3"  # type: ignore[arg-type]
    )
    assert arrays[MOTHER] == [(0, 1), (0, 1), (0, 0)]
    assert arrays[FATHER] == [(1, 1), (0, 0), (0, 1)]
    # A member with no file in the callset is not read as reference everywhere.
    assert arrays["SIB1"] == [None, None, None]


def test_qc_says_when_it_read_missing_records_as_reference() -> None:
    note = sample_integrity_service._per_sample_callset_note({MOTHER: 40, FATHER: 25}, 100)
    assert note is not None
    assert f"{FATHER} at 25%, {MOTHER} at 40%" in note
    assert "unrelated does not exclude a relationship" in note
    # A joint VCF calls every sample at every site: nothing to say.
    assert sample_integrity_service._per_sample_callset_note({}, 100) is None
    assert sample_integrity_service._per_sample_callset_note({MOTHER: 0}, 100) is None
