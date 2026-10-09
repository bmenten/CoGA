"""Every file a family package's datasets name gets its raw-file provenance row.

The import records each file a dataset of the manifest names in ``raw_import_files`` (the
file, its dataset, scope and sample, its SHA-256 and size) and lists it in its sample's
package record (``samples.metadata.package_import``): the traceability record of the files a
released interpretation rests on, which *Verify* checks the files against later. It records
the files named under the keys it knows (``_PROVENANCE_PATH_KEYS``); a file named under any
other key is validated and imported without a row. A monogenic NIPT pair's per-target
coverage tables (``target_table``), which give the NIPT analysis its depths, were imported
that way, and so were a PCF entry's segment tables named ``maternal_file``, ``mat_file``,
``paternal_file`` or ``pat_file``. The guard explores every key validation reads as a file,
so a new kind of file cannot be left out again.

All samples and values here are synthetic.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
import gzip
import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.core.config import settings
from backend.app.services import family_package_registration, family_package_validation
from backend.app.services.family_package_common import (
    SUPPORTED_DATASETS,
    FamilyPackageBundle,
    ManifestDataset,
)
from backend.app.services.family_package_validation import load_validated_family_package


class _RawFileSession:
    """Answers the family's sample lookup and keeps each raw_import_files row written,
    by the path it names."""

    def __init__(self, sample_ids: list[str]) -> None:
        self.samples = [{"sample_uuid": f"uuid-{sample_id}", "sample_id": sample_id} for sample_id in sample_ids]
        self.rows: dict[str, dict[str, Any]] = {}

    async def execute(self, statement: Any, params: Any = None) -> Any:
        if "INSERT INTO raw_import_files" in str(statement):
            self.rows[params["storage_path"]] = params
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: self.samples))


async def _raw_file_rows(bundle: FamilyPackageBundle) -> dict[str, dict[str, Any]]:
    session = _RawFileSession(bundle.ped.sample_ids)
    await family_package_registration._record_package_raw_files(
        session, bundle=bundle, family_uuid="family-uuid"  # type: ignore[arg-type]
    )
    return session.rows


def _assert_recorded(
    rows: dict[str, dict[str, Any]], bundle: FamilyPackageBundle, name: str, *, dataset: str, sample_id: str
) -> None:
    """The file has its row: its dataset, its sample, and the SHA-256 and size of its bytes."""
    path = bundle.root / name
    assert str(path) in rows, f"{name} has no raw-file row"
    row = rows[str(path)]
    content = path.read_bytes()
    assert (row["dataset"], row["scope"], row["sample_id"], row["file_name"]) == (
        dataset,
        "individual",
        f"uuid-{sample_id}",
        path.name,
    )
    assert (row["sha256"], row["file_size"]) == (hashlib.sha256(content).hexdigest(), len(content))


# --------------------------------------------------------------------------- #
# A monogenic NIPT pair: one VCF and one per-target coverage table per parent
# --------------------------------------------------------------------------- #

_PAIR_PED = (
    "NIPTPAIR\tFATHER1\t0\t0\t1\t1\n"
    "NIPTPAIR\tCFDNA1\t0\t0\t2\t1\n"
    "NIPTPAIR\tFETUS1\tFATHER1\tCFDNA1\t0\t2\n"
)
_PAIR_MANIFEST = (
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
    "  coverage:\n"
    "    per_sample:\n"
    "      CFDNA1: {target_table: coverage_CFDNA1.txt}\n"
    "      FATHER1: {target_table: coverage_FATHER1.txt}\n"
)
_COVERAGE_HEADER = (
    "#build\tchromosome\tstart\tend\tattribute\tlength\tmin\tmax\tmean\tmedian\tstdev\t"
    "zero_coverage_bases\tproportion_covered\n"
)


def _write_nipt_pair(folder: Path) -> Path:
    folder.mkdir()
    (folder / "nipt_trio.ped").write_text(_PAIR_PED)
    (folder / "manifest.yaml").write_text(_PAIR_MANIFEST)
    for sample, mean in (("CFDNA1", "1100,5"), ("FATHER1", "640,2")):
        with gzip.open(folder / f"{sample}.mutect2.vcf.gz", "wt") as handle:
            handle.write(
                "##fileformat=VCFv4.2\n"
                f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample}\n"
                "chr7\t1000\t.\tA\tG\t.\tPASS\tTLOD=300\tGT:AD:DP\t0/1:900,100:1000\n"
            )
        (folder / f"coverage_{sample}.txt").write_text(
            _COVERAGE_HEADER
            + f'hg38\tchr7\t900\t1100\t"GENEA;NM_1.1;ENST1;ENSE1;1"\t200\t500\t1500\t{mean}\t1102\t20,1\t0\t100\n'
        )
    return folder


@pytest.mark.asyncio
async def test_a_nipt_pairs_coverage_tables_are_recorded_with_their_checksums(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The NIPT analysis reads each parent's depths from its coverage table: the table must
    # be in the record, with a checksum, as the VCFs are.
    monkeypatch.setattr(settings, "family_import_roots", [])
    validation, bundle = load_validated_family_package(_write_nipt_pair(tmp_path / "NIPTPAIR"))
    assert validation.valid, validation.errors
    assert bundle is not None

    rows = await _raw_file_rows(bundle)

    for sample_id in ("CFDNA1", "FATHER1"):
        _assert_recorded(rows, bundle, f"coverage_{sample_id}.txt", dataset="coverage", sample_id=sample_id)
        _assert_recorded(rows, bundle, f"{sample_id}.mutect2.vcf.gz", dataset="snv", sample_id=sample_id)
    assert len(rows) == 4
    # The sample's own package record names its table too.
    package_record = family_package_registration._sample_provenance(bundle)
    assert package_record["CFDNA1"]["coverage"] == {"target_table": "coverage_CFDNA1.txt"}


# --------------------------------------------------------------------------- #
# A PCF entry: the maternal and paternal segment tables, under any of their names
# --------------------------------------------------------------------------- #

_PCF_PED = "PGTFAM\tFATHER\t0\t0\t1\t1\nPGTFAM\tMOTHER\t0\t0\t2\t1\nPGTFAM\tEMB1\tFATHER\tMOTHER\t0\t0\n"


def _write_pcf_package(folder: Path, *, maternal_key: str, paternal_key: str) -> Path:
    (folder / "PCF").mkdir(parents=True)
    (folder / "family.ped").write_text(_PCF_PED)
    for origin, mean in (("mat", "0.51"), ("pat", "0.49")):
        (folder / "PCF" / f"EMB1_pcf_{origin}_data.csv").write_text(
            '"sampleID","CHROM","arm","start.pos","end.pos","n.probes","mean"\n'
            f'"EMB1","chr1","p",1703565,119614882,232,{mean}\n'
        )
    (folder / "manifest.yaml").write_text(
        "schema_version: 1\n"
        "family_id: PGTFAM\n"
        "ped: family.ped\n"
        "datasets:\n"
        "  pcf:\n"
        "    per_sample:\n"
        f"      EMB1: {{{maternal_key}: PCF/EMB1_pcf_mat_data.csv, {paternal_key}: PCF/EMB1_pcf_pat_data.csv}}\n"
    )
    return folder


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("maternal_key", "paternal_key"),
    [("maternal", "paternal"), ("mat", "pat"), ("maternal_file", "paternal_file"), ("mat_file", "pat_file")],
)
async def test_a_pcf_entry_records_its_tables_under_each_of_their_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, maternal_key: str, paternal_key: str
) -> None:
    monkeypatch.setattr(settings, "family_import_roots", [])
    validation, bundle = load_validated_family_package(
        _write_pcf_package(tmp_path / "PGTFAM", maternal_key=maternal_key, paternal_key=paternal_key)
    )
    assert validation.valid, validation.errors
    assert {summary.dataset_type: summary.status for summary in validation.datasets}["pcf"] == "valid"
    assert bundle is not None

    rows = await _raw_file_rows(bundle)

    _assert_recorded(rows, bundle, "PCF/EMB1_pcf_mat_data.csv", dataset="pcf", sample_id="EMB1")
    _assert_recorded(rows, bundle, "PCF/EMB1_pcf_pat_data.csv", dataset="pcf", sample_id="EMB1")
    assert family_package_registration._sample_provenance(bundle)["EMB1"]["pcf"] == {
        maternal_key: "PCF/EMB1_pcf_mat_data.csv",
        paternal_key: "PCF/EMB1_pcf_pat_data.csv",
    }


# --------------------------------------------------------------------------- #
# The guard: every key validation reads as a file is one the import records
# --------------------------------------------------------------------------- #

_NAMED = "named-under/"

# ManifestDataset's own file fields (``json`` is the alias of its ``json_path``).
_TOP_LEVEL_FILE_FIELDS = ("family_vcf", "annotation_tsv", "index", "bed", "vcf", "file", "json")


class _NamesAFileUnderEveryKey(dict[str, Any]):
    """A dataset entry that names a file under whatever key it is asked for (the path
    ``named-under/<key>``), except the keys switched off: the keys whose path reaches path
    resolution are the keys validation reads as files."""

    def __init__(self, off: frozenset[str]) -> None:
        # Never empty, so `dataset.model_extra or {}` keeps it.
        super().__init__(placeholder=None)
        self._off = off

    def get(self, key: Any, default: Any = None) -> Any:
        return None if key in self._off else f"{_NAMED}{key}"

    def __getitem__(self, key: Any) -> Any:
        value = self.get(key)
        if value is None:
            raise KeyError(key)
        return value

    def __contains__(self, key: object) -> bool:
        return key not in self._off


def _dataset_naming_files(off: frozenset[str], *, with_samples: bool) -> ManifestDataset:
    """A dataset naming a file under each of its own fields, under every other key of its
    top level and (``with_samples``) under every key of one sample's entry, except the
    keys switched off."""
    dataset = ManifestDataset(**{key: f"{_NAMED}{key}" for key in _TOP_LEVEL_FILE_FIELDS if key not in off})
    if with_samples:
        dataset.per_sample = {"S1": _NamesAFileUnderEveryKey(off)}
    object.__setattr__(dataset, "__pydantic_extra__", _NamesAFileUnderEveryKey(off))
    return dataset


def _explore(keys_read: Callable[[frozenset[str]], set[str]]) -> set[str]:
    """Every key read as a file, switching off in turn each key a run read: a key read only
    in another's absence (``bed`` without ``target_table``, ``mat`` without ``maternal``)
    is read once that one is off. A switch that reveals no new key is not followed."""
    found: set[str] = set()
    pending: list[frozenset[str]] = [frozenset()]
    seen: set[frozenset[str]] = set()
    while pending:
        off = pending.pop()
        if off in seen:
            continue
        seen.add(off)
        read = keys_read(off)
        if off and read <= found:
            continue
        found |= read
        pending.extend(off | {key} for key in read)
    return found


def test_every_file_validation_checks_is_one_the_import_records(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Validation checks every file the importers read, under the keys they read it by,
    # before anything is written; the import records the files named under
    # _PROVENANCE_PATH_KEYS. A key validation reads as a file that the set lacks is a file
    # imported without a raw-file row.
    read: set[str] = set()

    def recording(resolve: Callable[[Path, str | None], Path | None]) -> Callable[[Path, str | None], Path | None]:
        def resolve_and_record(root: Path, value: str | None) -> Path | None:
            if isinstance(value, str) and value.startswith(_NAMED):
                read.add(value.removeprefix(_NAMED))
            return resolve(root, value)

        return resolve_and_record

    for name in ("_resolve_package_path", "_package_path_or_none"):
        monkeypatch.setattr(family_package_validation, name, recording(getattr(family_package_validation, name)))

    def keys_read(dataset_type: str, with_samples: bool, off: frozenset[str]) -> set[str]:
        read.clear()
        family_package_validation._validate_dataset(
            root=tmp_path,
            dataset_type=dataset_type,
            dataset=_dataset_naming_files(off, with_samples=with_samples),
            ped_sample_ids={"S1"},
            errors=[],
            warnings=[],
        )
        return set(read)

    read_as_files: set[str] = set()
    for dataset_type in SUPPORTED_DATASETS:
        for with_samples in (True, False):
            read_as_files |= _explore(partial(keys_read, dataset_type, with_samples))

    # The exploration reaches every name a file goes by, the fallbacks included, and leaves
    # out a key validation reads as a setting.
    assert {"family_vcf", "index", "target_table", "bed", "file", "bcf_index", "mat_file", "pat_file"} <= (
        read_as_files
    )
    assert "source_format" not in read_as_files
    assert read_as_files - family_package_registration._PROVENANCE_PATH_KEYS == set()
