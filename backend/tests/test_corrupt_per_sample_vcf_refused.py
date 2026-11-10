"""A per-sample ``.vcf.gz`` CoGA cannot read is refused by name, never answered with a 500.

Discover and the package validation read a per-sample SNV file's sample columns from its header.
Gzip raises a different error for each broken file: one cut short in its header (``EOFError``),
one whose compressed data is damaged (``zlib.error``) and a plain-text VCF named ``.vcf.gz``
(``gzip.BadGzipFile``). For each:

- the validation (a delivery's check, a dry run) refuses the file
  with ``dataset_vcf_unreadable``, naming the sample and the file, never the error's own text;
- Discover refuses a NIPT pair's parent file the same way, and drafts its manifest without the
  pair;
- the import's readers of a VCF's header (``read_vcf_sample_columns``, ``vcf_holds_a_data_line``)
  raise ``UnreadableVcf``, whose fixed message names the file, so a dataset that meets such a file
  fails with that message.

All values are synthetic.
"""

from __future__ import annotations

import gzip
from pathlib import Path
import zlib

import pytest
import yaml

from backend.app.core.config import settings
from backend.app.schemas import FamilyPackageManifestBuildRequest
from backend.app.services import family_package_common as common
from backend.app.services.family_package_discovery import discover_family_package_manifest
from backend.tests._kept_manifest import validated

PED = "NIPTPAIR\tFATHER1\t0\t0\t1\t1\nNIPTPAIR\tCFDNA1\t0\t0\t2\t1\nNIPTPAIR\tFETUS1\tFATHER1\tCFDNA1\t0\t2\n"
MANIFEST = (
    "schema_version: 1\n"
    "family_id: NIPTPAIR\n"
    "ped: nipt_trio.ped\n"
    "analysis_type: monogenic_nipt\n"
    "samples:\n"
    "  CFDNA1: {assay: nipt_cfdna}\n"
    "datasets:\n"
    "  snv:\n"
    "    per_sample:\n"
    "      CFDNA1: {vcf: CFDNA1.mutect2.vcf.gz}\n"
    "      FATHER1: {vcf: FATHER1.mutect2.vcf.gz}\n"
)


def _vcf(sample: str) -> str:
    return (
        "##fileformat=VCFv4.2\n"
        + "".join(f'##INFO=<ID=X{n},Number=1,Type=Integer,Description="filler {n}">\n' for n in range(4000))
        + f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample}\n"
        "chr7\t1000\t.\tA\tG\t.\tPASS\tTLOD=300\tGT:AD:DP\t0/1:900,100:1000\n"
    )


def _cut_in_its_header(sample: str) -> bytes:
    whole = gzip.compress(_vcf(sample).encode())
    return whole[: len(whole) // 3]


def _damaged(sample: str) -> bytes:
    whole = gzip.compress(_vcf(sample).encode())
    # The gzip header kept, the compressed data after it replaced by bytes no deflate stream holds.
    return whole[:10] + bytes([0xFF] * 64) + whole[-8:]


def _plain_text(sample: str) -> bytes:
    return _vcf(sample).encode()


BROKEN = {"cut_in_its_header": _cut_in_its_header, "damaged": _damaged, "plain_text": _plain_text}


def test_each_broken_file_raises_out_of_gzip(tmp_path: Path) -> None:
    # What a read without the guard meets: three different errors, none of them a refusal.
    raised = {}
    for name, make in BROKEN.items():
        path = tmp_path / f"{name}.vcf.gz"
        path.write_bytes(make("CFDNA1"))
        with pytest.raises(Exception) as caught:
            with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
                for _line in handle:
                    pass
        raised[name] = type(caught.value)
    assert raised == {"cut_in_its_header": EOFError, "damaged": zlib.error, "plain_text": gzip.BadGzipFile}


def _package(root: Path, broken: str, *, manifest: bool = True) -> Path:
    package = root / "NIPTPAIR"
    package.mkdir()
    (package / "nipt_trio.ped").write_text(PED, encoding="utf-8")
    with gzip.open(package / "CFDNA1.mutect2.vcf.gz", "wt") as handle:
        handle.write(_vcf("CFDNA1"))
    (package / "FATHER1.mutect2.vcf.gz").write_bytes(BROKEN[broken]("FATHER1"))
    if manifest:
        (package / "manifest.yaml").write_text(MANIFEST, encoding="utf-8")
    return package


_MESSAGE = (
    "The SNV file of FATHER1 (FATHER1.mutect2.vcf.gz) could not be read: it is cut short or "
    "damaged, or is not gzip. Copy it again from where the pipeline wrote it."
)


@pytest.fixture(autouse=True)
def _any_package_folder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path)])


# --- The validation -------------------------------------------------------------------------


@pytest.mark.parametrize("broken", sorted(BROKEN))
def test_the_validation_refuses_the_file_by_its_sample_and_name(tmp_path: Path, broken: str) -> None:
    validation = validated(_package(tmp_path, broken))

    assert validation.valid is False
    [error] = [issue for issue in validation.errors if issue.code == "dataset_vcf_unreadable"]
    assert (error.message, error.dataset, error.sample_id) == (_MESSAGE, "snv", "FATHER1")
    assert error.path is not None and error.path.endswith("FATHER1.mutect2.vcf.gz")
    # The other sample's file is read.
    assert not [issue for issue in validation.errors if issue.sample_id == "CFDNA1"]


@pytest.mark.parametrize("broken", sorted(BROKEN))
def test_discover_refuses_a_nipt_parents_file_it_cannot_read(tmp_path: Path, broken: str) -> None:
    package = _package(tmp_path, broken, manifest=False)

    result = discover_family_package_manifest(FamilyPackageManifestBuildRequest(folder_path=str(package)))

    assert result.valid is False
    assert [(issue.code, issue.message, issue.sample_id) for issue in result.errors] == [
        ("dataset_vcf_unreadable", _MESSAGE, "FATHER1")
    ]
    # No NIPT pair is proposed from a file Discover could not read.
    assert not any(issue.code == "nipt_pair_detected" for issue in result.warnings)
    assert "per_sample" not in (yaml.safe_load(result.manifest_yaml)["datasets"].get("snv") or {})


@pytest.mark.parametrize("broken", sorted(BROKEN))
def test_the_header_readers_raise_a_refusal_naming_the_file(tmp_path: Path, broken: str) -> None:
    path = tmp_path / "S1.vcf.gz"
    path.write_bytes(BROKEN[broken]("S1"))

    for read in (common.read_vcf_sample_columns, common.vcf_holds_a_data_line):
        with pytest.raises(common.UnreadableVcf) as caught:
            read(path)
        assert str(caught.value) == "The VCF (S1.vcf.gz) could not be read: it is cut short or damaged, or is not gzip"
        assert caught.value.path == path

    with pytest.raises(common.UnreadableVcf) as named:
        common.read_vcf_sample_columns(path, label="The SNV file of S1")
    assert str(named.value).startswith("The SNV file of S1 (S1.vcf.gz) could not be read")


def test_a_whole_file_is_read(tmp_path: Path) -> None:
    path = tmp_path / "S1.vcf.gz"
    with gzip.open(path, "wt") as handle:
        handle.write(_vcf("S1"))

    assert common.read_vcf_sample_columns(path) == ["S1"]
    assert common.vcf_holds_a_data_line(path) is True
