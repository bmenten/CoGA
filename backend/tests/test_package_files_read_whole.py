"""A package file the import cannot read whole is refused at validation, before anything is written.

The validation reads every package VCF to its end with the import's readers of it
(``read_package_vcf_whole``), and an embryo's APCAD and segregation tables to theirs, so a file
whose header reads well but whose records do not fails the validation and a dry run, not its
dataset once the import has begun writing:

- Discover on a package whose SNV family VCF (or a sample's SNV file) cannot be read refuses it by
  name (``dataset_vcf_unreadable``), never with a 500;
- the validation refuses, for a family VCF and a sample's VCF of SNV, NeedlR and TRGT, and a
  sample's chrM and chrM SV VCF: a BGZF file cut where one of its blocks ends, a gzip file damaged
  or cut short in its records (``dataset_vcf_unreadable``) and a plain-text VCF whose last record
  is cut short (``dataset_vcf_record_cut``), naming the file and the sample, never the error's
  own text; the header readers pass each of these;
- a file larger than the import reads (``dataset_vcf_too_large``) and a VCF read whole that is not
  UTF-8 text are refused too;
- an embryo's APCAD table and segregation table, ``.csv.gz`` or ``.bed.gz``, are read: one that
  cannot be read is refused (``dataset_table_unreadable``), one naming another embryo too;
- whole files pass, bgzipped, gzipped or plain.

All values are synthetic.
"""

from __future__ import annotations

import gzip
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

from backend.app.core.config import settings
from backend.app.schemas import FamilyPackageManifestBuildRequest
from backend.app.services import family_package_common as common
from backend.app.services import variant_upload_service
from backend.app.services.family_package_discovery import discover_family_package_manifest
from backend.app.services.family_package_import import execute_family_package_import
from backend.tests._bgzf import BGZF_EOF_BLOCK, bgzf_blocks, bgzip, bgzip_cut_after
from backend.tests._kept_manifest import kept_manifest, validated

FAMILY = "FAM"
PED = (
    f"{FAMILY}\tFATHER1\t0\t0\t1\t1\n"
    f"{FAMILY}\tMOTHER\t0\t0\t2\t1\n"
    f"{FAMILY}\tPROBAND\tFATHER1\tMOTHER\t1\t2\n"
)
TRIO = ("PROBAND", "MOTHER", "FATHER1")
REFUSALS = {"dataset_vcf_unreadable", "dataset_vcf_record_cut", "dataset_vcf_too_large", "dataset_table_unreadable"}


def _vcf(columns: tuple[str, ...], *, records: int = 6000) -> str:
    """A VCF whose header lies well inside the first BGZF block, and whose records fill several
    more."""
    return (
        "##fileformat=VCFv4.2\n"
        + "".join(f'##INFO=<ID=X{n},Number=1,Type=Integer,Description="filler {n}">\n' for n in range(40))
        + "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t"
        + "\t".join(columns)
        + "\n"
        + "".join(
            f"chr1\t{1000 + 7 * n}\t.\tA\tG\t50\tPASS\tX{n % 40}={n}\tGT:AD\t"
            + "\t".join("0/1:6,4" for _ in columns)
            + "\n"
            for n in range(records)
        )
    )


def _bgzf_cut_in_its_records(text: str) -> bytes:
    # Cut where the first block ends: gzip reads a whole file holding the header and the first
    # records; only the missing end-of-file block tells it was cut.
    return bgzip_cut_after(text.encode(), 1)


def _gzip_cut_in_its_records(text: str) -> bytes:
    whole = gzip.compress(text.encode())
    return whole[: len(whole) * 2 // 3]


def _damaged_in_its_records(text: str) -> bytes:
    # The last BGZF block's compressed data replaced by bytes no deflate stream holds; its
    # header, its checksum, every other block and the end-of-file block kept.
    blocks = bgzf_blocks(text.encode())
    assert len(blocks) > 1
    block = blocks[-1]
    blocks[-1] = block[:18] + bytes([0xFF] * (len(block) - 26)) + block[-8:]
    return b"".join(blocks) + BGZF_EOF_BLOCK


def _record_cut(text: str) -> bytes:
    # A plain-text copy cut in its last record, after its position.
    last = text.rstrip("\n").rindex("\n") + 1
    return text[: last + len("chr1\t1000")].encode()


# Each broken file: how it is made, the name it has, the refusal's code and words.
_UNREADABLE = ("dataset_vcf_unreadable", "could not be read: it is cut short or damaged, or is not gzip")
BROKEN: dict[str, tuple[Callable[[str], bytes], str, tuple[str, str]]] = {
    "bgzf_cut_in_its_records": (_bgzf_cut_in_its_records, ".vcf.gz", _UNREADABLE),
    "gzip_cut_in_its_records": (_gzip_cut_in_its_records, ".vcf.gz", _UNREADABLE),
    "damaged_in_its_records": (_damaged_in_its_records, ".vcf.gz", _UNREADABLE),
    "record_cut": (_record_cut, ".vcf", ("dataset_vcf_record_cut", "ends in a record cut short, as a copy cut short leaves it")),
}
_COPY_AGAIN = "Copy it again from where the pipeline wrote it."


# Each VCF the validation reads whole: the dataset, its block with the file as ``{file}``, what
# a refusal calls the file, the sample it names, and the file's sample columns.
CASES: dict[str, tuple[str, dict[str, Any], str, str | None, tuple[str, ...]]] = {
    "snv_family": ("snv", {"family_vcf": "{file}", "source_format": "deepvariant"}, "The SNV family VCF", None, TRIO),
    "snv_sample": (
        "snv",
        {"source_format": "deepvariant", "per_sample": {"PROBAND": {"vcf": "{file}"}}},
        "The SNV file of PROBAND",
        "PROBAND",
        ("PROBAND",),
    ),
    "needlr_family": ("sv_needlr", {"family_vcf": "{file}"}, "The NeedlR family VCF", None, ()),
    "needlr_sample": ("sv_needlr", {"per_sample": {"PROBAND": {"vcf": "{file}"}}}, "The NeedlR file of PROBAND", "PROBAND", ()),
    "trgt_family": ("repeats_trgt", {"family_vcf": "{file}"}, "The TRGT family VCF", None, TRIO),
    "trgt_sample": ("repeats_trgt", {"per_sample": {"PROBAND": {"file": "{file}"}}}, "The TRGT VCF of PROBAND", "PROBAND", ("PROBAND",)),
    "mito_sample": ("mito", {"per_sample": {"PROBAND": {"vcf": "{file}"}}}, "The chrM VCF of PROBAND", "PROBAND", ("PROBAND",)),
    "mito_sv": (
        "mito",
        {"per_sample": {"PROBAND": {"vcf": "mito/PROBAND.chrM.vcf.gz", "sv_vcf": "{file}"}}},
        "The chrM SV VCF of PROBAND",
        "PROBAND",
        ("PROBAND",),
    ),
}


@pytest.fixture(autouse=True)
def _any_package_folder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path)])


def _filled(value: Any, file: str) -> Any:
    if isinstance(value, dict):
        return {key: _filled(item, file) for key, item in value.items()}
    return file if value == "{file}" else value


def _package(root: Path, case: str, content: bytes, *, suffix: str = ".vcf.gz") -> tuple[Path, dict[str, Any], str]:
    """A package of the trio whose one dataset reads ``content`` as the case's file: the
    folder, its manifest, and the file's path in it."""
    dataset, block, _label, _sample, _columns = CASES[case]
    package = root / FAMILY
    (package / "data").mkdir(parents=True)
    (package / "mito").mkdir()
    (package / "family.ped").write_text(PED, encoding="utf-8")
    # A sample's whole chrM VCF, beside whose chrM SV VCF.
    (package / "mito/PROBAND.chrM.vcf.gz").write_bytes(bgzip(_vcf(("PROBAND",)).encode()))
    file = f"data/{case}{suffix}"
    (package / file).write_bytes(content)
    if suffix.endswith(".gz") and "family_vcf" in block:
        (package / f"{file}.tbi").write_bytes(b"")
    manifest = {"schema_version": 1, "family_id": FAMILY, "ped": "family.ped", "datasets": {dataset: _filled(block, file)}}
    return package, manifest, file


def _refusals(errors: list[Any]) -> list[tuple[str, str, str | None, str | None]]:
    return [(issue.code, issue.message, issue.dataset, issue.sample_id) for issue in errors if issue.code in REFUSALS]


# --- What the header readers see -------------------------------------------------------------


@pytest.mark.parametrize("broken", sorted(BROKEN))
def test_the_header_readers_pass_each_broken_file(tmp_path: Path, broken: str) -> None:
    # Why the file is read whole: each reads to its #CHROM line and its first record.
    make, suffix, _refusal = BROKEN[broken]
    path = tmp_path / f"PROBAND{suffix}"
    path.write_bytes(make(_vcf(TRIO)))

    assert common.read_vcf_sample_columns(path) == list(TRIO)
    assert common.vcf_holds_a_data_line(path) is True


# --- The validation ---------------------------------------------------------------------------


@pytest.mark.parametrize("broken", sorted(BROKEN))
@pytest.mark.parametrize("case", sorted(CASES))
def test_the_validation_refuses_a_vcf_it_cannot_read_whole(tmp_path: Path, case: str, broken: str) -> None:
    dataset, _block, label, sample_id, columns = CASES[case]
    make, suffix, (code, problem) = BROKEN[broken]
    package, manifest, file = _package(tmp_path, case, make(_vcf(columns)), suffix=suffix)

    validation = validated(package, manifest)

    assert validation.valid is False
    assert _refusals(validation.errors) == [(code, f"{label} ({file}) {problem}. {_COPY_AGAIN}", dataset, sample_id)]
    [refusal] = [issue for issue in validation.errors if issue.code == code]
    assert refusal.path is not None and refusal.path.endswith(file)
    summary = next(item for item in validation.datasets if item.dataset_type == dataset)
    assert summary.status == "error"


@pytest.mark.parametrize("compress", [bgzip, gzip.compress, None], ids=["bgzf", "gzip", "plain"])
@pytest.mark.parametrize("case", sorted(CASES))
def test_whole_files_pass(tmp_path: Path, case: str, compress: Callable[[bytes], bytes] | None) -> None:
    columns = CASES[case][4]
    text = _vcf(columns).encode()
    package, manifest, _file = _package(
        tmp_path, case, compress(text) if compress else text, suffix=".vcf.gz" if compress else ".vcf"
    )

    validation = validated(package, manifest)

    assert validation.valid is True, validation.errors


def test_a_whole_file_without_its_last_line_end_passes(tmp_path: Path) -> None:
    # Its last record holds every column: only a record cut short is refused.
    text = _vcf(("PROBAND",)).rstrip("\n")
    package, manifest, _file = _package(tmp_path, "snv_sample", text.encode(), suffix=".vcf")

    assert validated(package, manifest).valid is True


def test_a_vcf_larger_than_the_import_reads_whole_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package, manifest, file = _package(tmp_path, "needlr_sample", bgzip(_vcf(()).encode()))
    monkeypatch.setattr(settings, "max_upload_bytes", 1000)

    validation = validated(package, manifest)

    assert _refusals(validation.errors) == [
        (
            "dataset_vcf_too_large",
            f"The NeedlR file of PROBAND ({file}) is larger than the import reads whole (MAX_UPLOAD_BYTES on "
            "disk, MAX_DECOMPRESSED_UPLOAD_BYTES unpacked). The import would refuse it.",
            "sv_needlr",
            "PROBAND",
        )
    ]


def test_an_snv_vcf_with_a_line_longer_than_the_loader_reads_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package, manifest, file = _package(tmp_path, "snv_family", bgzip(_vcf(TRIO).encode()))
    monkeypatch.setattr(variant_upload_service, "MAX_UPLOAD_LINE_BYTES", 60)

    validation = validated(package, manifest)

    assert _refusals(validation.errors) == [
        (
            "dataset_vcf_too_large",
            f"The SNV family VCF ({file}) holds a line longer than the import reads (16 MiB). The import would refuse it.",
            "snv",
            None,
        )
    ]


def test_a_vcf_read_whole_that_is_not_utf8_text_is_refused(tmp_path: Path) -> None:
    text = _vcf(("PROBAND",)).encode().replace(b"PASS", b"P\xffSS", 1)
    package, manifest, file = _package(tmp_path, "trgt_sample", text, suffix=".vcf")

    validation = validated(package, manifest)

    assert _refusals(validation.errors) == [
        (
            "dataset_vcf_unreadable",
            f"The TRGT VCF of PROBAND ({file}) could not be read: it is not UTF-8 text. {_COPY_AGAIN}",
            "repeats_trgt",
            "PROBAND",
        )
    ]


def test_a_chrm_vcf_is_read_by_the_loader_too(tmp_path: Path) -> None:
    # A plain-text chrM VCF named .gz: the whole-text read takes it as text, the small-variant
    # loader that stores it as gzip, which it is not.
    package, manifest, file = _package(tmp_path, "mito_sample", _vcf(("PROBAND",)).encode())

    validation = validated(package, manifest)

    assert _refusals(validation.errors) == [
        ("dataset_vcf_unreadable", f"The chrM VCF of PROBAND ({file}) {_UNREADABLE[1]}. {_COPY_AGAIN}", "mito", "PROBAND")
    ]


def test_a_refused_vcf_gets_no_warning_of_holding_no_record(tmp_path: Path) -> None:
    header_only = _vcf(("PROBAND",), records=0)
    package, manifest, _file = _package(tmp_path, "trgt_sample", bgzip(header_only.encode()))
    assert [issue.code for issue in validated(package, manifest).warnings if issue.sample_id == "PROBAND"] == [
        "dataset_file_without_record"
    ]

    (package / "data/trgt_sample.vcf.gz").write_bytes(bgzip_cut_after(header_only.encode(), 1))
    validation = validated(package, manifest)
    assert [issue.code for issue in validation.errors if issue.sample_id == "PROBAND"] == ["dataset_vcf_unreadable"]
    assert not [issue for issue in validation.warnings if issue.code == "dataset_file_without_record"]


@pytest.mark.asyncio
async def test_a_dry_run_stops_at_the_refusal(tmp_path: Path) -> None:
    package, manifest, _file = _package(tmp_path, "snv_family", _bgzf_cut_in_its_records(_vcf(TRIO)))

    result = await execute_family_package_import(
        None,
        folder_path=package,
        project_id=None,
        dry_run=True,
        user=None,
        manifest_text=kept_manifest(package, manifest),
    )

    assert (result.completed, result.error) == (False, "Package validation failed")
    assert [code for code, *_ in _refusals(result.validation.errors)] == ["dataset_vcf_unreadable"]
    assert "Package validation failed; no data were imported." in result.logs


# --- Discover ---------------------------------------------------------------------------------


def _damaged_in_its_header(text: str) -> bytes:
    whole = gzip.compress(text.encode())
    # The gzip header kept, the compressed data after it replaced by bytes no deflate stream holds.
    return whole[:10] + bytes([0xFF] * 64) + whole[-8:]


def _cut_in_its_header(text: str) -> bytes:
    filler = "".join(f'##INFO=<ID=Y{n},Number=1,Type=Integer,Description="filler {n}">\n' for n in range(4000))
    whole = gzip.compress(text.replace("##fileformat=VCFv4.2\n", "##fileformat=VCFv4.2\n" + filler, 1).encode())
    return whole[: len(whole) // 3]


UNREADABLE_HEADERS: dict[str, Callable[[str], bytes]] = {
    "damaged_in_its_header": _damaged_in_its_header,
    "cut_in_its_header": _cut_in_its_header,
    "plain_text": lambda text: text.encode(),
}


@pytest.mark.parametrize("broken", sorted(UNREADABLE_HEADERS))
def test_discover_refuses_an_snv_family_vcf_it_cannot_read(tmp_path: Path, broken: str) -> None:
    package = tmp_path / FAMILY
    (package / "snv").mkdir(parents=True)
    (package / f"{FAMILY}.ped").write_text(PED, encoding="utf-8")
    family_vcf = f"snv/{FAMILY}.annotated.vcf.gz"
    (package / family_vcf).write_bytes(UNREADABLE_HEADERS[broken](_vcf(TRIO, records=10)))
    (package / f"{family_vcf}.tbi").write_bytes(b"")

    result = discover_family_package_manifest(FamilyPackageManifestBuildRequest(folder_path=str(package)))

    assert result.valid is False
    assert [(issue.code, issue.message, issue.dataset, issue.sample_id) for issue in result.errors] == [
        ("dataset_vcf_unreadable", f"The SNV family VCF ({family_vcf}) {_UNREADABLE[1]}. {_COPY_AGAIN}", "snv", None)
    ]
    # The draft names the file all the same, without a caller read from it.
    snv = yaml.safe_load(result.manifest_yaml)["datasets"]["snv"]
    assert snv["family_vcf"] == family_vcf and "source_format" not in snv


@pytest.mark.parametrize("broken", sorted(UNREADABLE_HEADERS))
def test_discover_refuses_a_samples_snv_file_it_cannot_read(tmp_path: Path, broken: str) -> None:
    package = tmp_path / FAMILY
    package.mkdir()
    (package / f"{FAMILY}.ped").write_text(PED, encoding="utf-8")
    deepvariant = _vcf(("{sample}",), records=10).replace("##fileformat=VCFv4.2\n", "##fileformat=VCFv4.2\n##DeepVariant_version=1.6.1\n")
    for sample in TRIO:
        path = package / f"snv/{sample}/annotation/{sample}_annot.vcf.gz"
        path.parent.mkdir(parents=True)
        text = deepvariant.replace("{sample}", sample)
        path.write_bytes(UNREADABLE_HEADERS[broken](text) if sample == "MOTHER" else gzip.compress(text.encode()))

    result = discover_family_package_manifest(FamilyPackageManifestBuildRequest(folder_path=str(package)))

    assert [(issue.code, issue.message, issue.sample_id) for issue in result.errors] == [
        (
            "dataset_vcf_unreadable",
            f"The SNV file of MOTHER (snv/MOTHER/annotation/MOTHER_annot.vcf.gz) {_UNREADABLE[1]}. {_COPY_AGAIN}",
            "MOTHER",
        )
    ]
    snv = yaml.safe_load(result.manifest_yaml)["datasets"]["snv"]
    assert sorted(snv["per_sample"]) == sorted(TRIO) and "source_format" not in snv


def test_discover_reads_a_whole_snv_family_vcfs_caller(tmp_path: Path) -> None:
    package = tmp_path / FAMILY
    (package / "snv").mkdir(parents=True)
    (package / f"{FAMILY}.ped").write_text(PED, encoding="utf-8")
    family_vcf = f"snv/{FAMILY}.annotated.vcf.gz"
    text = _vcf(TRIO, records=10).replace("##fileformat=VCFv4.2\n", "##fileformat=VCFv4.2\n##DeepVariant_version=1.6.1\n")
    (package / family_vcf).write_bytes(bgzip(text.encode()))
    (package / f"{family_vcf}.tbi").write_bytes(b"")

    result = discover_family_package_manifest(FamilyPackageManifestBuildRequest(folder_path=str(package)))

    assert result.errors == []
    assert yaml.safe_load(result.manifest_yaml)["datasets"]["snv"]["source_format"] == "deepvariant"


# --- An embryo's APCAD and segregation tables -------------------------------------------------

PGT_PED = (
    f"{FAMILY}\tFATHER1\t0\t0\t1\t1\n"
    f"{FAMILY}\tMOTHER\t0\t0\t2\t1\n"
    f"{FAMILY}\tEMB1\tFATHER1\tMOTHER\t0\t0\n"
)


def _table(embryo: str, *, rows: int = 6000) -> str:
    return "Chr,Pos,Indiv,APCAD\n" + "".join(f"chr1,{1000 + 7 * n},{embryo},0.{n % 10}\n" for n in range(rows))


def _bed(*, rows: int = 6000) -> str:
    return "".join(f"chr1\t{999 + 7 * n}\t{1000 + 7 * n}\t0.{n % 10}\n" for n in range(rows))


def _pgt_package(root: Path, entry: dict[str, str], files: dict[str, bytes]) -> tuple[Path, dict[str, Any]]:
    package = root / FAMILY
    (package / "apcad").mkdir(parents=True)
    (package / "family.ped").write_text(PGT_PED, encoding="utf-8")
    for name, content in files.items():
        (package / name).write_bytes(content)
    manifest = {
        "schema_version": 1,
        "family_id": FAMILY,
        "ped": "family.ped",
        "family": {"members": {"EMB1": {"role": "embryo"}}},
        "datasets": {"apcad": {"enabled": True, "per_sample": {"EMB1": entry}}},
    }
    return package, manifest


TABLE = "apcad/EMB1_APCAD_parental_data.csv.gz"
BED = "apcad/EMB1.apcad.bed.gz"
SEGREGATION = "apcad/EMB1_segregation.csv.gz"


@pytest.mark.parametrize("compress", [bgzip, gzip.compress], ids=["bgzf", "gzip"])
def test_an_embryos_compressed_tables_are_read(tmp_path: Path, compress: Callable[[bytes], bytes]) -> None:
    package, manifest = _pgt_package(
        tmp_path,
        {"file": TABLE, "segregation": SEGREGATION},
        {TABLE: compress(_table("EMB1").encode()), SEGREGATION: compress(_table("EMB1").encode())},
    )

    assert validated(package, manifest).valid is True


def test_an_embryos_compressed_table_naming_another_embryo_is_refused(tmp_path: Path) -> None:
    package, manifest = _pgt_package(tmp_path, {"file": TABLE}, {TABLE: bgzip(_table("EMB2").encode())})

    validation = validated(package, manifest)

    assert [issue.code for issue in validation.errors] == ["apcad_table_sample_mismatch"]


@pytest.mark.parametrize(
    "broken",
    [
        pytest.param(_bgzf_cut_in_its_records, id="bgzf_cut_in_its_records"),
        pytest.param(_gzip_cut_in_its_records, id="gzip_cut_in_its_records"),
        pytest.param(_damaged_in_its_records, id="damaged_in_its_records"),
    ],
)
@pytest.mark.parametrize(
    ("entry", "file", "kind", "content"),
    [
        pytest.param({"file": TABLE}, TABLE, "APCAD table", _table("EMB1"), id="apcad_table"),
        pytest.param({"bed": BED}, BED, "APCAD table", _bed(), id="apcad_bed"),
        pytest.param({"bed": BED, "segregation": SEGREGATION}, SEGREGATION, "segregation table", _table("EMB1"), id="segregation_table"),
    ],
)
def test_an_embryos_table_that_cannot_be_read_is_refused(
    tmp_path: Path,
    entry: dict[str, str],
    file: str,
    kind: str,
    content: str,
    broken: Callable[[str], bytes],
) -> None:
    files = {name: bgzip((_bed() if name == BED else _table("EMB1")).encode()) for name in entry.values()}
    files[file] = broken(content)
    package, manifest = _pgt_package(tmp_path, entry, files)

    validation = validated(package, manifest)

    assert validation.valid is False
    assert _refusals(validation.errors) == [
        ("dataset_table_unreadable", f"The {kind} of EMB1 ({file}) {_UNREADABLE[1]}. {_COPY_AGAIN}", "apcad", "EMB1")
    ]
