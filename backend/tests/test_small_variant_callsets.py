"""Each small-variant callset is named by the caller that made it (small_variant_callsets.py).

Short-read and long-read WGS of the same DNA keep their calls apart only when each callset is
stored under its own name, so a callset is named by its caller (the owner's decisions of
2026-11-19): ``haplotypecaller`` (short-read WGS), ``mutect2`` (a NIPT plasma), ``clair3`` and
``deepvariant`` (long-read WGS), or any other caller a package names. The caller can change with
the preprocessing, so a package declares it or the VCF header names it; a VCF whose caller
cannot be told is refused, never stored under a guessed name.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile

from backend.app.services.small_variant_callsets import (
    UNNAMED_CALLER_MESSAGE,
    callset_label,
    callset_preference,
    caller_from_vcf_header,
    is_callset_name,
    is_direct_callset,
)
from backend.app.services.variant_upload_service import (
    UnnamedCallerHTTPError,
    _detect_small_variant_format_from_upload,
)

_COLUMNS = "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
_DIRECT_RECORD = "1\t100\t.\tA\tG\t60\tPASS\t.\tGT\t0/1\n"


@pytest.mark.parametrize(
    ("header", "caller"),
    [
        # GATK names each tool on its command line, in the current and the older notation.
        ('##GATKCommandLine=<ID=HaplotypeCaller,CommandLine="HaplotypeCaller -R ref.fa",Version="4.5.0.0">', "haplotypecaller"),
        ("##GATKCommandLine.HaplotypeCaller=<ID=HaplotypeCaller,Version=3.8>", "haplotypecaller"),
        ('##GATKCommandLine=<ID=Mutect2,CommandLine="Mutect2 -R ref.fa",Version="4.5.0.0">', "mutect2"),
        ("##source=Mutect2", "mutect2"),
        ("##MutectVersion=2.2", "mutect2"),
        ("##DeepVariant_version=1.6.1", "deepvariant"),
        ("##source=Clair3", "clair3"),
        ("##clair3_version=1.0.10", "clair3"),
    ],
)
def test_a_header_names_its_caller(header: str, caller: str) -> None:
    assert caller_from_vcf_header(["##fileformat=VCFv4.2", header, _COLUMNS.strip()]) == caller


def test_a_header_that_names_no_caller_or_two_names_none() -> None:
    # A tool that is not a caller (a joint genotyping step, an annotation) names none.
    assert caller_from_vcf_header(["##fileformat=VCFv4.2", '##GATKCommandLine=<ID=GenotypeGVCFs,Version="4.5">']) is None
    assert caller_from_vcf_header(["##source=CoGADemoFamilyClair3VEP", "##VEP=v116"]) is None
    # A merge of two callers' files: its package names the callset.
    assert caller_from_vcf_header(["##DeepVariant_version=1.6.1", "##source=Clair3"]) is None
    # One caller named twice is that caller.
    joint = [
        '##GATKCommandLine=<ID=HaplotypeCaller,Version="4.5">',
        '##GATKCommandLine=<ID=GenotypeGVCFs,Version="4.5">',
        "##GATKCommandLine.HaplotypeCaller=<ID=HaplotypeCaller>",
    ]
    assert caller_from_vcf_header(joint) == "haplotypecaller"


def test_any_caller_a_package_names_is_a_direct_callset() -> None:
    for name in ("haplotypecaller", "mutect2", "clair3", "deepvariant", "dragen_snv", "octopus"):
        assert is_callset_name(name) and is_direct_callset(name), name
    # The imputed genotypes, the mtDNA calls and a NIPT's per-sample calls are read by rules of their own.
    for name in ("glimpse2", "shapeit", "mito", "nipt"):
        assert is_callset_name(name) and not is_direct_callset(name), name
    # ``auto`` asks the loader to read the caller from the header: it names no callset.
    for name in ("auto", "Clair3", "gatk haplotypecaller", "1caller", "a", "x" * 40, "", None, "auto/../x"):
        assert not is_callset_name(name), name
        assert not is_direct_callset(name), name


def test_callsets_are_named_and_ordered_as_a_reader_takes_them() -> None:
    assert callset_label("haplotypecaller") == "GATK HaplotypeCaller"
    assert callset_label("octopus") == "octopus"
    # Direct calls (the known callers first, then any other by name), a NIPT's per-sample
    # calls, the imputed genotypes, then the rest.
    callsets = ["mito", "glimpse2", "nipt", "octopus", "haplotypecaller", "deepvariant", "clair3", "shapeit"]
    assert sorted(callsets, key=callset_preference) == [
        "clair3",
        "deepvariant",
        "haplotypecaller",
        "octopus",
        "nipt",
        "glimpse2",
        "shapeit",
        "mito",
    ]


def _upload(text: str, name: str = "calls.vcf") -> UploadFile:
    return UploadFile(file=io.BytesIO(text.encode("utf-8")), filename=name)


def test_the_loader_stores_a_vcf_under_the_caller_its_header_names() -> None:
    gatk = '##fileformat=VCFv4.2\n##GATKCommandLine=<ID=HaplotypeCaller,Version="4.5">\n' + _COLUMNS
    # Physically phased, as GATK writes it, and described "phased": still HaplotypeCaller's calls.
    phased = gatk.replace("#CHROM", '##FORMAT=<ID=PGT,Number=1,Type=String,Description="Physical phasing haplotype">\n#CHROM')
    assert _detect_small_variant_format_from_upload(_upload(phased + "1\t100\t.\tA\tG\t60\tPASS\t.\tGT\t0|1\n"), "auto") == "haplotypecaller"
    # A callset the request names is stored under that name, whatever the header says.
    assert _detect_small_variant_format_from_upload(_upload(gatk + _DIRECT_RECORD), "octopus") == "octopus"


def test_the_loader_reads_imputed_genotypes_without_a_caller() -> None:
    imputed = "##fileformat=VCFv4.2\n" + _COLUMNS + "1\t100\t.\tA\tG\t.\tPASS\t.\tGT:GP\t0|1:0.1,0.8,0.1\n"
    assert _detect_small_variant_format_from_upload(_upload(imputed), "auto") == "glimpse2"


def test_the_loader_refuses_a_vcf_whose_caller_it_cannot_tell() -> None:
    with pytest.raises(UnnamedCallerHTTPError) as refused:
        _detect_small_variant_format_from_upload(_upload("##fileformat=VCFv4.2\n" + _COLUMNS + _DIRECT_RECORD), "auto")
    assert refused.value.status_code == 400 and refused.value.detail == UNNAMED_CALLER_MESSAGE
    assert "GATK HaplotypeCaller" in UNNAMED_CALLER_MESSAGE and "source_format" in UNNAMED_CALLER_MESSAGE
    # A callset named otherwise than a callset's name is refused too.
    with pytest.raises(HTTPException) as invalid:
        _detect_small_variant_format_from_upload(_upload("##fileformat=VCFv4.2\n" + _COLUMNS + _DIRECT_RECORD), "Clair 3")
    assert invalid.value.status_code == 400


def test_a_package_snv_dataset_whose_caller_cannot_be_told_is_refused_before_any_write(tmp_path: Path) -> None:
    from backend.app.services.family_package_common import ManifestDataset
    from backend.app.services.family_package_validation import _check_snv_callset_named

    unnamed = tmp_path / "family.vcf"
    unnamed.write_text("##fileformat=VCFv4.2\n" + _COLUMNS + _DIRECT_RECORD, encoding="utf-8")
    named = tmp_path / "named.vcf"
    named.write_text("##fileformat=VCFv4.2\n##DeepVariant_version=1.6.1\n" + _COLUMNS + _DIRECT_RECORD, encoding="utf-8")

    def issues(path: Path | None, **extra: str) -> list[tuple[str, str]]:
        errors: list = []
        dataset = ManifestDataset.model_validate({"family_vcf": path.name if path else None, **extra})
        _check_snv_callset_named(dataset, path, errors)
        return [(issue.code, issue.message) for issue in errors]

    assert issues(unnamed) == [("dataset_callset_unnamed", UNNAMED_CALLER_MESSAGE)]
    assert issues(named) == []
    # A package that names the caller is read as it says.
    assert issues(unnamed, source_format="haplotypecaller") == []
    assert issues(unnamed, source_format="auto") == [("dataset_callset_unnamed", UNNAMED_CALLER_MESSAGE)]
    assert [code for code, _message in issues(unnamed, source_format="GATK HaplotypeCaller")] == ["dataset_callset_invalid"]
    # A file that cannot be read is said by its own checks.
    assert issues(tmp_path / "missing.vcf") == []
    assert issues(None) == []
