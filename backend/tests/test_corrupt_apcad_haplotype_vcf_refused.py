"""A corrupt APCAD VCF or phased family VCF is refused at validation, by name.

The validation reads an APCAD VCF's and the phased family VCF's samples from the whole file, so a
``.vcf.gz`` cut short (in its header, or a BGZF file anywhere), damaged or not gzip at all fails
the validation and a dry run, not its dataset at import:

- the validation refuses such an APCAD VCF (an embryo's, or the family's) and such a phased
  family VCF with ``dataset_vcf_unreadable``, naming the file and, for an embryo's, the embryo,
  never the error's own text; a BGZF file without its end-of-file block is refused so too;
- it refuses a phased family VCF that names no sample (``dataset_vcf_no_sample_columns``): the
  import reads each member's genotypes from the member's own column;
- an APCAD file without a VCF header is not refused: the import reads it as a BED, by design;
- Discover reads none of these files' content, and drafts their manifest all the same.

All values are synthetic.
"""

from __future__ import annotations

import gzip
from pathlib import Path
from typing import Callable

import pytest
import yaml

from backend.app.core.config import settings
from backend.app.schemas import FamilyPackageManifestBuildRequest
from backend.app.services import family_package_common as common
from backend.app.services.family_package_discovery import discover_family_package_manifest
from backend.app.services.family_package_import import execute_family_package_import
from backend.tests._kept_manifest import kept_manifest, validated
from backend.tests._bgzf import bgzip, bgzip_cut_after

FAMILY = "PGTFAM"
PED = (
    f"{FAMILY}\tFATHER1\t0\t0\t1\t1\n"
    f"{FAMILY}\tMOTHER1\t0\t0\t2\t1\n"
    f"{FAMILY}\tEMB1\tFATHER1\tMOTHER1\t0\t0\n"
    f"{FAMILY}\tEMB2\tFATHER1\tMOTHER1\t0\t0\n"
)
APCAD_EMB1 = "split_trio/filter_trio/EMB1.trio_filtered.vcf.gz"
APCAD_EMB2 = "split_trio/filter_trio/EMB2.trio_filtered.vcf.gz"
PHASED = f"phasing/shapeit_filter/{FAMILY}_shapeit_rephased_final.vcf.gz"
MANIFEST = (
    "schema_version: 1\n"
    f"family_id: {FAMILY}\n"
    "ped: family.ped\n"
    "family:\n"
    "  members:\n"
    "    EMB1: {role: embryo}\n"
    "    EMB2: {role: embryo}\n"
    "datasets:\n"
    "  apcad:\n"
    "    enabled: true\n"
    "    per_sample:\n"
    f"      EMB1: {{vcf: {APCAD_EMB1}}}\n"
    f"      EMB2: {{vcf: {APCAD_EMB2}}}\n"
    "  haplotypes:\n"
    "    enabled: true\n"
    f"    family_vcf: {PHASED}\n"
)
FAMILY_APCAD_MANIFEST = (
    "schema_version: 1\n"
    f"family_id: {FAMILY}\n"
    "ped: family.ped\n"
    "datasets:\n"
    "  apcad:\n"
    "    enabled: true\n"
    f"    family_vcf: {APCAD_EMB1}\n"
)


def _vcf(columns: tuple[str, ...], *, records: int = 6000) -> str:
    """A VCF whose header fills the first BGZF block well short of its end, and whose
    records fill several more."""
    return (
        "##fileformat=VCFv4.2\n"
        + "".join(f'##INFO=<ID=X{n},Number=1,Type=Integer,Description="filler {n}">\n' for n in range(40))
        + "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t"
        + "\t".join(columns)
        + "\n"
        + "".join(
            f"chr1\t{1000 + n}\t.\tA\tG\t50\tPASS\t.\tGT:AD\t" + "\t".join("0|1:6,4" for _ in columns) + "\n"
            for n in range(records)
        )
    )


def _trio(embryo: str) -> str:
    return _vcf(("FATHER1", "MOTHER1", embryo))


def _phased() -> str:
    return _vcf(("FATHER1", "MOTHER1", "EMB1", "EMB2"))


def _cut_in_its_header(text: str) -> bytes:
    # A header of 4,000 lines, so a cut at a third of the compressed bytes lies inside it.
    filler = "".join(f'##INFO=<ID=Y{n},Number=1,Type=Integer,Description="filler {n}">\n' for n in range(4000))
    whole = gzip.compress(text.replace("##fileformat=VCFv4.2\n", "##fileformat=VCFv4.2\n" + filler, 1).encode())
    return whole[: len(whole) // 3]


def _damaged(text: str) -> bytes:
    whole = gzip.compress(text.encode())
    # The gzip header kept, the compressed data after it replaced by bytes no deflate stream holds.
    return whole[:10] + bytes([0xFF] * 64) + whole[-8:]


def _plain_text(text: str) -> bytes:
    return text.encode()


def _bgzf_cut_in_its_records(text: str) -> bytes:
    # Cut where the first block ends: gzip reads it as a whole file holding the header and
    # the first records; only the missing end-of-file block tells it was cut.
    return bgzip_cut_after(text.encode(), 1)


BROKEN: dict[str, Callable[[str], bytes]] = {
    "cut_in_its_header": _cut_in_its_header,
    "damaged": _damaged,
    "plain_text": _plain_text,
    "bgzf_cut_in_its_records": _bgzf_cut_in_its_records,
}
_REASON = ": it is cut short or damaged, or is not gzip. Copy it again from where the pipeline wrote it."


@pytest.fixture(autouse=True)
def _any_package_folder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path)])


def _package(
    root: Path,
    *,
    emb1: bytes | None = None,
    phased: bytes | None = None,
    manifest: str | None = MANIFEST,
) -> Path:
    package = root / FAMILY
    (package / "split_trio/filter_trio").mkdir(parents=True)
    (package / "phasing/shapeit_filter").mkdir(parents=True)
    (package / "family.ped").write_text(PED, encoding="utf-8")
    (package / APCAD_EMB1).write_bytes(emb1 if emb1 is not None else bgzip(_trio("EMB1").encode()))
    (package / APCAD_EMB2).write_bytes(gzip.compress(_trio("EMB2").encode()))
    (package / PHASED).write_bytes(phased if phased is not None else bgzip(_phased().encode()))
    if manifest is not None:
        (package / "manifest.yaml").write_text(manifest, encoding="utf-8")
    return package


def _refusals(errors: list) -> list[tuple[str, str, str | None, str | None]]:
    return [
        (issue.code, issue.message, issue.dataset, issue.sample_id)
        for issue in errors
        if issue.code in {"dataset_vcf_unreadable", "dataset_vcf_no_sample_columns"}
    ]


# --- What a header reader sees -------------------------------------------------------------


def test_the_bgzf_file_cut_in_its_records_reads_whole_as_its_header() -> None:
    # Why the header alone does not tell: the file reads to its #CHROM line and beyond.
    cut = _bgzf_cut_in_its_records(_trio("EMB1"))
    text = gzip.decompress(cut).decode()
    assert "#CHROM" in text and 0 < text.count("\nchr1\t") < 6000


# --- An embryo's APCAD VCF --------------------------------------------------------------------


@pytest.mark.parametrize("broken", sorted(BROKEN))
def test_the_validation_refuses_an_embryos_apcad_vcf_it_cannot_read(tmp_path: Path, broken: str) -> None:
    validation = validated(_package(tmp_path, emb1=BROKEN[broken](_trio("EMB1"))))

    assert validation.valid is False
    assert _refusals(validation.errors) == [
        ("dataset_vcf_unreadable", f"The APCAD VCF of EMB1 ({APCAD_EMB1}) could not be read{_REASON}", "apcad", "EMB1")
    ]
    [error] = [issue for issue in validation.errors if issue.code == "dataset_vcf_unreadable"]
    assert error.path is not None and error.path.endswith(APCAD_EMB1)
    # The other embryo's file, and the phased VCF, are read.
    assert not [issue for issue in validation.errors if issue.sample_id == "EMB2"]
    apcad = next(summary for summary in validation.datasets if summary.dataset_type == "apcad")
    haplotypes = next(summary for summary in validation.datasets if summary.dataset_type == "haplotypes")
    assert (apcad.status, haplotypes.status) == ("error", "valid")


@pytest.mark.parametrize("broken", sorted(BROKEN))
def test_the_validation_refuses_a_family_apcad_vcf_it_cannot_read(tmp_path: Path, broken: str) -> None:
    package = _package(tmp_path, emb1=BROKEN[broken](_trio("EMB1")), manifest=FAMILY_APCAD_MANIFEST)

    validation = validated(package)

    assert validation.valid is False
    assert _refusals(validation.errors) == [
        ("dataset_vcf_unreadable", f"The family APCAD VCF ({APCAD_EMB1}) could not be read{_REASON}", "apcad", None)
    ]


def test_an_apcad_file_without_a_vcf_header_is_still_read_as_a_bed(tmp_path: Path) -> None:
    # A file named .vcf.gz in which the reader finds no VCF record is read as a BED by the import
    # (`_import_apcad_track_file`), by design: no header is no refusal.
    bed = gzip.compress(b"chr1\t999\t1000\t0.4\nchr1\t1999\t2000\t0.6\n")

    validation = validated(_package(tmp_path, emb1=bed))

    assert validation.valid is True, validation.errors


# --- The phased family VCF --------------------------------------------------------------------


@pytest.mark.parametrize("broken", sorted(BROKEN))
def test_the_validation_refuses_a_phased_family_vcf_it_cannot_read(tmp_path: Path, broken: str) -> None:
    validation = validated(_package(tmp_path, phased=BROKEN[broken](_phased())))

    assert validation.valid is False
    assert _refusals(validation.errors) == [
        ("dataset_vcf_unreadable", f"The phased family VCF ({PHASED}) could not be read{_REASON}", "haplotypes", None)
    ]
    haplotypes = next(summary for summary in validation.datasets if summary.dataset_type == "haplotypes")
    apcad = next(summary for summary in validation.datasets if summary.dataset_type == "apcad")
    assert (haplotypes.status, apcad.status) == ("error", "valid")


_NO_SAMPLES = (
    f"The phased family VCF ({PHASED}) has no #CHROM line naming its samples, so the import "
    "could give no member a genotype from it. Copy it again from where the pipeline wrote it."
)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(_phased().replace("#CHROM", "##CHROM", 1), id="no_chrom_line"),
        pytest.param("##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\nchr1\t1\t.\tA\tG\t.\t.\t.\n", id="no_sample_column"),
        pytest.param("", id="empty"),
    ],
)
def test_the_validation_refuses_a_phased_family_vcf_naming_no_sample(tmp_path: Path, text: str) -> None:
    validation = validated(_package(tmp_path, phased=bgzip(text.encode())))

    assert validation.valid is False
    assert _refusals(validation.errors) == [("dataset_vcf_no_sample_columns", _NO_SAMPLES, "haplotypes", None)]


# --- Whole files ------------------------------------------------------------------------------


@pytest.mark.parametrize("compress", [bgzip, gzip.compress, None], ids=["bgzf", "gzip", "plain"])
def test_whole_files_pass(tmp_path: Path, compress: Callable[[bytes], bytes] | None) -> None:
    emb1, phased = _trio("EMB1").encode(), _phased().encode()
    package = _package(
        tmp_path,
        emb1=compress(emb1) if compress else emb1,
        phased=compress(phased) if compress else phased,
    )
    if compress is None:
        # Uncompressed, under the names a plain VCF has.
        (package / APCAD_EMB2).write_bytes(_trio("EMB2").encode())
        for name in (APCAD_EMB1, APCAD_EMB2, PHASED):
            (package / name).rename(package / name.removesuffix(".gz"))
        (package / "manifest.yaml").write_text(MANIFEST.replace(".vcf.gz", ".vcf"), encoding="utf-8")

    validation = validated(package)

    assert validation.valid is True, validation.errors


def test_the_whole_vcf_reader_reads_columns_and_refuses_a_cut_by_name(tmp_path: Path) -> None:
    path = tmp_path / "EMB1.vcf.gz"
    path.write_bytes(bgzip(_trio("EMB1").encode()))
    assert common.read_whole_vcf_sample_columns(path) == ["FATHER1", "MOTHER1", "EMB1"]

    path.write_bytes(_bgzf_cut_in_its_records(_trio("EMB1")))
    # The header reader alone reads the cut file as a whole one.
    assert common.read_vcf_sample_columns(path) == ["FATHER1", "MOTHER1", "EMB1"]
    with pytest.raises(common.UnreadableVcf) as caught:
        common.read_whole_vcf_sample_columns(path, label="The APCAD VCF of EMB1")
    assert str(caught.value) == "The APCAD VCF of EMB1 (EMB1.vcf.gz) could not be read: it is cut short or damaged, or is not gzip"


# --- The routes and the dry run ---------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("broken", sorted(BROKEN))
async def test_a_dry_run_stops_at_the_refusal(tmp_path: Path, broken: str) -> None:
    package = _package(tmp_path, emb1=BROKEN[broken](_trio("EMB1")), phased=BROKEN[broken](_phased()))

    result = await execute_family_package_import(
        None, folder_path=package, project_id=None, dry_run=True, user=None, manifest_text=kept_manifest(package)
    )

    assert (result.completed, result.error) == (False, "Package validation failed")
    assert sorted(code for code, *_ in _refusals(result.validation.errors)) == ["dataset_vcf_unreadable"] * 2
    assert "Package validation failed; no data were imported." in result.logs


# --- Discover ---------------------------------------------------------------------------------


@pytest.mark.parametrize("broken", sorted(BROKEN))
def test_discover_reads_neither_file_and_drafts_them(tmp_path: Path, broken: str) -> None:
    package = _package(
        tmp_path, emb1=BROKEN[broken](_trio("EMB1")), phased=BROKEN[broken](_phased()), manifest=None
    )

    result = discover_family_package_manifest(FamilyPackageManifestBuildRequest(folder_path=str(package)))

    assert result.errors == []
    datasets = yaml.safe_load(result.manifest_yaml)["datasets"]
    assert datasets["apcad"]["per_sample"]["EMB1"]["vcf"] == APCAD_EMB1
    assert datasets["haplotypes"]["family_vcf"] == PHASED
