from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
import errno
import gzip
import json
import logging
import math
import os
from pathlib import Path
import re
from typing import Any, Awaitable, Callable, Iterable, TextIO

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import String, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas import (
    FamilyImportDatasetSummary,
    FamilyImportValidationIssue,
    FamilyPackageValidationOut,
)
from .family_identifiers import CONTROL_CHARACTERS, IDENTIFIER_RULE, identifier_problem, visible
from .upload_safety import read_path_text_bounded


logger = logging.getLogger(__name__)


SUPPORTED_DATASETS = (
    "snv",
    "sv_needlr",
    "repeats_trgt",
    "wisecondorx",
    "qdnaseq",
    "apcad",
    "pcf",
    "haplotypes",
    "paraphase",
    "coverage",
    # Long-read (nf-core/lrsvar) outputs. All per-sample: the pipeline writes
    # <dataset>/<sample_id>/[annotation/]<sample_id>_<suffix>.
    "cnv",
    "mito",
    "alignments",
    "qc",
    "pipeline_info",
)


# Datasets a package is expected to carry. Anything outside this set is genuinely
# optional and its absence is not worth a warning -- without the split, every
# package emits an "optional dataset missing" warning for each of the dataset types
# it was never going to contain (assay-specific ones like PGT's apcad/pcf, or the
# long-read ones below), which buries the warnings that matter.
CORE_DATASETS = ("snv", "sv_needlr")


# Where a package keeps its PED when it is not at the top: the PGT pipeline
# (nf-cmgg/copgtm) writes ped/combined.ped.
PED_FOLDER = "ped"


# Roles of the ``qc`` dataset. Per sample: the long-read pipeline's NanoPlot/MultiQC
# report, NanoStats and mosdepth files, and the PGT pipeline's (nf-cmgg/copgtm) Qualimap
# report and summary, mean-coverage file and ngs-bits sex check. Per family, the PGT
# pipeline's tables covering every embryo or pair at once: allele drop-out/drop-in
# (Picard), Mendelian concordance before and after imputation (RTG Tools) and KING
# kinship.
QC_SAMPLE_ROLES = (
    "report",
    "read_stats",
    "depth_summary",
    "depth_regions",
    "depth_global_dist",
    "qualimap_summary",
    "mean_coverage",
    "sex_check",
)
QC_FAMILY_ROLES = ("ado_adi", "concordance", "imputed_concordance", "kinship")


# The long-read datasets, which the pipeline writes per sample: each sample's required
# roles, and the optional companions (an index, an annotation, an auxiliary output) that
# are read only when present. Discovery proposes and validation checks them alike.
LONG_READ_DATASET_ROLES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "cnv": (("vcf",), ("index", "copy_number_bedgraph", "depth_bigwig", "maf_bigwig", "summary_html")),
    "mito": (("vcf",), ("index", "annotation_tsv", "sv_vcf", "sv_index", "sv_annotation_tsv")),
    "alignments": (("file",), ("index",)),
    "qc": ((), QC_SAMPLE_ROLES),
}


# Optional family-level files of the ``haplotypes`` dataset: the PGT pipeline's reading
# of which haplotype of the affected parent is the affected one (a table of the sites
# each haplotype shares with the index, and its one-line conclusion). Recorded as
# pipeline evidence; CoGA's own risk-haplotype inference does not use it.
HAPLOTYPE_ORIGIN_ROLES = ("haplotype_origin", "haplotype_conclusion")


# The names a ``pcf`` entry may give each parent's segment table, in the order they are
# read: validation and the importer take the first one the entry gives
# (``_pcf_role_path``), and the raw-file provenance names the file under any of them.
PCF_ROLE_KEYS: dict[str, tuple[str, ...]] = {
    "maternal": ("maternal", "mat", "maternal_file", "mat_file"),
    "paternal": ("paternal", "pat", "paternal_file", "pat_file"),
}


APCAD_PCF_TRACK_TYPE = "apcad_pcf"


APCAD_PCF_SOURCE = "pcf"


# The source of the ``apcad`` track the ``apcad`` dataset writes. It replaces only that
# source's track of a sample: HiFiCNV's minor allele fraction is stored on the same track
# type (source ``hificnv``), and a BED upload's under ``web`` (SAFE-22).
APCAD_SOURCE = "apcad"


# ClickHouse ``source`` tags for the long-read callsets. Each one scopes its own
# delete/re-import, so a family can hold NeedlR SVs, HiFiCNV CNV calls and chrM
# variants side by side and re-importing one never removes another.
CNV_SOURCE = "hificnv"


MITO_SOURCE = "mito"
# chrM structural variants (Sniffles2 --mosaic on chrM): their own SV source, so a nuclear
# SV re-import never removes them, nor they it.
MITO_SV_SOURCE = "mito_sv"


class ManifestDataset(BaseModel):
    enabled: bool = True
    family_vcf: str | None = None
    annotation_tsv: str | None = None
    index: str | None = None
    bed: str | None = None
    vcf: str | None = None
    file: str | None = None
    json_path: str | None = Field(default=None, alias="json")
    per_sample: dict[str, dict[str, Any]] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class ManifestPhenotypes(BaseModel):
    enabled: bool = True
    file: str | None = None
    format: str = "hpo_tsv"

    model_config = ConfigDict(extra="allow")


class PackageManifest(BaseModel):
    schema_version: int = 1
    family_id: str | None = None
    # The PED file. A package without one names its members under family.add_members
    # (the long-read pipeline writes no PED for a couple screened for carriership).
    ped: str | None = None
    # Optional analysis type for the family, e.g. "monogenic_nipt". Promoted to
    # families.metadata["analysis_type"] so the workspace surfaces the NIPT tab
    # and resolve_nipt_trio can find the cfDNA sample (see docs/monogenic-nipt.md).
    analysis_type: str | None = None
    roi: str | dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    samples: dict[str, Any] | list[Any] | None = None
    phenotypes: ManifestPhenotypes | None = None
    datasets: dict[str, ManifestDataset] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow")


@dataclass(slots=True)
class PedMember:
    family_id: str
    iid: str
    pid: str
    mid: str
    sex: str
    phen: str
    line_no: int
    clinical_status: str
    role_hint: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    extra_columns: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ParsedPed:
    family_ids: list[str]
    members: list[PedMember]
    sample_ids: list[str]
    text: str


@dataclass(slots=True)
class FamilyPackageBundle:
    root: Path
    manifest_path: Path
    manifest: PackageManifest
    # None when the manifest names the members itself (family.add_members, no PED file).
    ped_path: Path | None
    ped: ParsedPed
    # When the package was staged from S3, the original s3:// source so provenance
    # records the durable S3 URI rather than the ephemeral staging path.
    source_uri: str | None = None
    # Package-relative paths of files staging left in the object store (a remote
    # package's alignments): they exist although ``root`` does not hold them.
    remote_only_files: frozenset[str] = frozenset()


@dataclass(slots=True)
class PackageExecutionResult:
    validation: FamilyPackageValidationOut
    datasets: list[FamilyImportDatasetSummary]
    logs: list[str]
    family_id: str | None
    completed: bool
    error: str | None = None


ProgressCallback = Callable[
    [FamilyPackageValidationOut | None, list[FamilyImportDatasetSummary], list[str], str | None],
    Awaitable[None],
]


DatasetProgressCallback = Callable[[FamilyImportDatasetSummary], Awaitable[None]]


# Awaited with the identifier of the family an import is about to write, once its package
# has validated and before anything of that family is written. It raises when it cannot
# record that, and the import then stops, having written nothing of the family.
FamilyRecordCallback = Callable[[str], Awaitable[None]]


async def _run_with_periodic_progress(
    work: Awaitable[Any],
    *,
    report: Callable[[dict[str, Any]], Awaitable[None]] | None,
    stats: dict[str, Any],
    interval_seconds: float = 60.0,
) -> Any:
    if report is None:
        return await work
    task = asyncio.create_task(work)
    try:
        while True:
            done, _pending = await asyncio.wait({task}, timeout=interval_seconds)
            if task in done:
                return await task
            try:
                await report({**stats, "stage": "running"})
            except Exception:  # pragma: no cover
                logger.exception("Family package import heartbeat progress update failed")
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


def _blank_control_characters(value: str | None) -> str | None:
    return CONTROL_CHARACTERS.sub(" ", value) if value is not None else None


def _issue(
    code: str,
    message: str,
    *,
    dataset: str | None = None,
    sample_id: str | None = None,
    path: Path | str | None = None,
) -> FamilyImportValidationIssue:
    # An ID, a file name or an exception's text can carry a control character: it becomes a
    # space, as in a log line, since the import job stores the issue (a NUL could not be
    # stored) and the import page shows it. A check that names such a value on purpose
    # writes it with `visible` first, so that its message shows which character it is.
    return FamilyImportValidationIssue(
        code=code,
        message=CONTROL_CHARACTERS.sub(" ", message),
        dataset=_blank_control_characters(dataset),
        sample_id=_blank_control_characters(sample_id),
        path=_blank_control_characters(str(path)) if path is not None else None,
    )


# The errors for an ID CoGA cannot store (see family_identifiers): the validation reads
# nothing else of the package until it is corrected.
IDENTIFIER_ISSUE_CODES = frozenset({"family_id_invalid", "sample_id_invalid"})


def family_id_issue(
    family_id: str, *, source: str, path: Path | str | None = None
) -> FamilyImportValidationIssue | None:
    """A ``family_id_invalid`` error when ``family_id``, read from ``source`` ("the folder
    name", "in the PED"), cannot be stored as a family's ID; None when it can."""
    problem = identifier_problem(family_id)
    if problem is None:
        return None
    return _issue(
        "family_id_invalid",
        f"Family ID '{visible(family_id)}' ({source}) {problem}. {IDENTIFIER_RULE}",
        path=path,
    )


def sample_id_issues(
    sample_ids: Iterable[str], *, source: str, path: Path | str | None = None
) -> list[FamilyImportValidationIssue]:
    """A ``sample_id_invalid`` error for each of ``sample_ids``, read from ``source`` ("in
    the PED", "under family.add_members"), that cannot be stored as a sample's ID."""
    issues: list[FamilyImportValidationIssue] = []
    for sample_id in dict.fromkeys(sample_ids):
        problem = identifier_problem(sample_id)
        if problem is not None:
            issues.append(
                _issue(
                    "sample_id_invalid",
                    f"Sample ID '{visible(sample_id)}' ({source}) {problem}. {IDENTIFIER_RULE}",
                    path=path,
                )
            )
    return issues


# Suffixes long-read tools append to the sample name when they write a VCF's sample
# column after the input file rather than the sample: TRGT names it after the sorted
# BAM (``HG002_sort``), longphase after its own output prefix (``HG002_sv_phased``).
_VCF_SAMPLE_SUFFIXES = (
    "_sort",
    "_sorted",
    "_sv_phased",
    "_snv_phased",
    "_phased",
    "_sv",
    "_snv",
    ".sort",
    ".sorted",
    ".sv",
    "-sv",
)


def vcf_sample_name_candidates(header_sample: str) -> list[str]:
    """The sample ids a VCF column could be named after: the name itself first, then the
    name with each known tool suffix stripped (``HG002_sort`` -> ``HG002``)."""
    name = header_sample.strip()
    if not name:
        return []
    candidates = [name]
    lowered = name.lower()
    for suffix in _VCF_SAMPLE_SUFFIXES:
        if lowered.endswith(suffix) and len(name) > len(suffix):
            stripped = name[: -len(suffix)]
            if stripped not in candidates:
                candidates.append(stripped)
    return candidates


def resolve_vcf_sample_id(
    header_sample: str,
    family_sample_ids: set[str] | dict[str, Any],
    *,
    declared: str | None = None,
) -> str | None:
    """Family sample id a VCF ``#CHROM`` sample column belongs to, or None.

    Resolution order, most explicit first:

    1. ``declared`` -- a manifest ``vcf_sample``/``sample_name`` override, which always
       wins so an operator can fix a mapping the heuristics get wrong.
    2. an exact match on a family sample id.
    3. the column with a known tool suffix stripped (``HG002_sort`` -> ``HG002``).
    4. a family sample id the column is prefixed with (``HG002_sv_phased`` -> ``HG002``),
       longest id first so ``S1`` never shadows ``S10``.

    Returns None when nothing matches; callers report that as a validation error
    rather than importing rows under a sample that is not in the family.
    """
    sample_ids = set(family_sample_ids)
    if declared and declared in sample_ids:
        return declared
    name = header_sample.strip()
    if not name:
        return None
    if name in sample_ids:
        return name
    lowered = name.lower()
    for suffix in _VCF_SAMPLE_SUFFIXES:
        if lowered.endswith(suffix) and name[: -len(suffix)] in sample_ids:
            return name[: -len(suffix)]
    for sample_id in sorted(sample_ids, key=len, reverse=True):
        if name.startswith(f"{sample_id}_") or name.startswith(f"{sample_id}."):
            return sample_id
    return None


# A caller's slot for an unnamed sample (HiFiCNV writes ``Sample0``), never a sample id: it
# is not looked up among the stored samples, so a sample someone named "Sample0" does not
# turn every HiFiCNV file into another sample's.
_CALLER_SLOT_COLUMN = re.compile(r"^sample\d*$", re.IGNORECASE)

_KNOWN_SAMPLE_IDS_SQL = text(
    """
    SELECT sample_id
    FROM samples
    WHERE family_id = CAST(:family_uuid AS uuid)
       OR sample_id IN :names
    """
).bindparams(bindparam("names", expanding=True, type_=String()))


class VcfSampleColumnError(ValueError):
    """A per-sample VCF has no column for its sample, or its column names another sample."""


class DatasetLeftMissing(RuntimeError):
    """A dataset failed after its write had deleted rows of samples beyond those it names,
    which it could not put back (SAFE-28): ``samples`` (sample ids) are those whose data of
    the dataset may now be missing too. The import flags the dataset for them as well as for
    its own samples (``family_package_registration.scope_with_samples``)."""

    def __init__(self, message: str, *, samples: Iterable[str]) -> None:
        super().__init__(message)
        self.samples = sorted({str(sample) for sample in samples if sample})


async def known_vcf_sample_ids(
    session: AsyncSession,
    *,
    family_uuid: str,
    header_samples: list[str],
) -> set[str]:
    """The sample ids a per-sample file's columns are checked against: every member of the
    target's family, and any stored sample a column is named after (as is, or with a tool
    suffix stripped). Sample ids are unique across CoGA, so a column naming a sample of
    another family is recognised too."""
    names = sorted(
        {
            candidate
            for column in header_samples
            if not _CALLER_SLOT_COLUMN.match(column.strip())
            for candidate in vcf_sample_name_candidates(column)
        }
    ) or [""]
    result = await session.execute(
        _KNOWN_SAMPLE_IDS_SQL,
        {"family_uuid": family_uuid, "names": names},
    )
    return {str(row["sample_id"]) for row in result.mappings().all() if row.get("sample_id")}


def per_sample_vcf_column(
    header_samples: list[str],
    *,
    target_sample_id: str,
    known_sample_ids: set[str],
    declared: str | None = None,
    label: str = "VCF",
    remedy: str = "",
) -> int:
    """The index of the ``#CHROM`` sample column that holds ``target_sample_id``'s calls,
    in a file stored as that one sample's (a per-sample package file, an admin upload).

    Such files used to be bound to their sample whatever their column named, so another
    member's file -- or another patient's -- was stored as this sample's without a word.

    * ``declared`` (the entry's ``vcf_sample``) is the operator's recorded word: the column
      it names is read, or, when it is the target's own id, the file's single column.
    * Otherwise the column the shared alias rules resolve to the target is read
      (``<sample>_sort``, ``<sample>_sv_phased``), whichever position it has.
    * A single column that names no known sample (a caller's placeholder, as HiFiCNV's
      ``Sample0``) belongs to the target by construction.
    * A column naming another sample -- a family member, or a sample elsewhere in CoGA --
      with no column for the target is refused, as is a file with two columns for it.
    """
    if not header_samples:
        raise VcfSampleColumnError(f"{label} has no sample column")
    if declared:
        if declared in header_samples:
            return header_samples.index(declared)
        if declared == target_sample_id and len(header_samples) == 1:
            return 0
        if declared != target_sample_id:
            raise VcfSampleColumnError(
                f"{label}: vcf_sample '{declared}' is neither a column of the file "
                f"{header_samples} nor {target_sample_id}"
            )
    resolved = [
        resolve_vcf_sample_id(column, known_sample_ids | {target_sample_id})
        for column in header_samples
    ]
    target_columns = [index for index, sample in enumerate(resolved) if sample == target_sample_id]
    if len(target_columns) == 1:
        return target_columns[0]
    if len(target_columns) > 1:
        raise VcfSampleColumnError(
            f"{label} has more than one sample column for {target_sample_id}: "
            f"{[header_samples[index] for index in target_columns]}"
        )
    if len(header_samples) == 1 and resolved[0] is None:
        return 0
    named = {column: sample for column, sample in zip(header_samples, resolved) if sample is not None}
    if named:
        described = ", ".join(f"'{column}' is {sample}" for column, sample in named.items())
        message = f"{label} has no sample column for {target_sample_id}: {described}."
    else:
        message = (
            f"{label} has no sample column for {target_sample_id}: its columns "
            f"{header_samples} name no known sample."
        )
    raise VcfSampleColumnError(f"{message} {remedy}".strip())


def vcf_sample_alias_map(
    header_samples: list[str],
    family_sample_ids: set[str] | dict[str, Any],
    *,
    declared: str | None = None,
    target_sample_id: str | None = None,
) -> tuple[dict[str, str], list[str]]:
    """``({vcf column: family sample id}, [unresolved columns])``.

    ``target_sample_id`` is the sample a *per-sample* manifest entry declares this file
    for. A single-column VCF under such an entry belongs to that sample by
    construction, whatever the caller happened to name the column, so it is bound
    directly -- this is what makes the CNV caller's ``Sample0`` resolve.
    """
    aliases: dict[str, str] = {}
    unresolved: list[str] = []
    if target_sample_id is not None and len(header_samples) == 1:
        return {header_samples[0]: target_sample_id}, []
    for header_sample in header_samples:
        resolved = resolve_vcf_sample_id(header_sample, family_sample_ids, declared=declared)
        if resolved is None:
            unresolved.append(header_sample)
            continue
        if resolved != header_sample:
            aliases[header_sample] = resolved
    return aliases, unresolved


def package_text_handle(path: Path) -> TextIO:
    """A package file opened as UTF-8 text, undecodable bytes replaced, through gzip when
    its name ends in ``.gz``. The caller closes it."""
    if path.name.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("r", encoding="utf-8", errors="replace")


def read_vcf_sample_columns(path: Path) -> list[str]:
    """Sample columns from a VCF's ``#CHROM`` line, reading only as far as that line.

    Used by validation to check sample names without materialising a multi-GB VCF.
    Returns ``[]`` for a headerless or sample-less file (the annotated NeedlR VCF has
    only the eight fixed columns).
    """
    with package_text_handle(path) as handle:
        for line in handle:
            if line.startswith("#CHROM"):
                return line.rstrip("\n\r").split("\t")[9:]
            if not line.startswith("#"):
                break
    return []


def vcf_holds_a_data_line(path: Path) -> bool:
    """Whether a VCF holds a line after its header, reading only as far as the first one.

    Used by validation to report a data file the import refuses for holding no record
    (SAFE-3) without materialising a multi-GB VCF. Any line counts: only the loader reads a
    record, so a file whose lines it cannot read passes here and fails at import.
    """
    with package_text_handle(path) as handle:
        for line in handle:
            if line.strip() and not line.startswith("#"):
                return True
    return False


def runs_through_symlink_loop(path: str | os.PathLike[str]) -> bool:
    """Whether looking ``path`` up meets a symlink loop.

    ``os.path.realpath`` leaves a loop unresolved rather than raise, and so does
    ``Path.resolve`` from Python 3.13; on 3.12, which CI and the image run, ``Path.resolve``
    raises RuntimeError. A path that ran through a loop is looked up here, so a guard refuses
    it the same way on every version."""
    try:
        os.stat(path)
    except OSError as exc:
        return exc.errno == errno.ELOOP
    return False


NUL = "\x00"
NUL_IN_PATH = "holds a NUL character, which no file or folder name can hold"


def nul_location(value: Any, where: str = "") -> str | None:
    """Where a NUL character sits in a parsed manifest, as the keys and positions that lead
    to it (``datasets.snv.vcf``, ``samples[0].id``), or None when it holds none."""
    if isinstance(value, str):
        return (where or "the document") if NUL in value else None
    if isinstance(value, dict):
        for key, item in value.items():
            name = f"{where}.{visible(str(key))}" if where else visible(str(key))
            if NUL in str(key):
                return name
            found = nul_location(item, name)
            if found is not None:
                return found
    if isinstance(value, list):
        for position, item in enumerate(value):
            found = nul_location(item, f"{where}[{position}]")
            if found is not None:
                return found
    return None


def _resolve_package_path(root: Path, value: str | None) -> Path | None:
    """The path a manifest names (or Discover's requested ``ped_path``) in the package folder
    ``root``, its ``..`` and symlinks resolved; None for an empty value.

    A manifest-declared asset path is relative to the (already-authorized) package folder, or
    an absolute path inside it. Refused with 400: a path that leads out of the package (``ped:
    /etc/passwd``, ``../../etc/passwd``, a symlink that leads out), so that a crafted manifest
    reads no other host file; one that starts with ``~``; one that runs through a symlink
    loop. A ``~`` was expanded: ``~/x`` named a file in the backend's home folder, and
    ``~nouser/x`` failed with a RuntimeError (a 500), as a loop did on Python 3.12.
    """
    if value is None or not str(value).strip():
        return None
    written = str(value).strip()
    if NUL in written:
        # Every look-up of the path raised ValueError on it: a 500 (SAFE-5).
        raise HTTPException(status_code=400, detail=f"Package manifest path '{visible(written)}' {NUL_IN_PATH}")
    if written.startswith("~"):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Package manifest path '{visible(written)}' starts with '~': a manifest path is "
                "relative to the package directory, and CoGA does not read a home folder"
            ),
        )
    # The joined path, resolved (an absolute value replaces the join), is checked against the
    # package folder as a string before anything is looked up by it. os.path.realpath does not
    # raise on a symlink loop, on any Python version: the look-up below finds one.
    folder = os.path.realpath(root)
    resolved = os.path.realpath(os.path.join(folder, written))
    if resolved == folder:
        return Path(folder)
    if not resolved.startswith(folder + os.sep):
        raise HTTPException(
            status_code=400,
            detail="Package manifest paths must stay within the package directory",
        )
    if runs_through_symlink_loop(resolved):
        raise HTTPException(
            status_code=400,
            detail=f"Package manifest path '{visible(written)}' runs through a symlink loop",
        )
    return Path(resolved)


def _package_path_or_none(root: Path, value: str | None) -> Path | None:
    """``_resolve_package_path`` for a lookup that probes for a file rather than requires
    one: a path that leaves the package is no path."""
    try:
        return _resolve_package_path(root, value)
    except HTTPException:
        return None


def path_leaves_package(relative_path: str) -> bool:
    """Whether ``relative_path``, a path in a package as written, leads out of the package
    folder: it is absolute, or its ``..`` climb above the folder. Told from the string
    alone, so nothing is looked up to tell it."""
    normalized = os.path.normpath(relative_path)
    return os.path.isabs(normalized) or normalized == os.pardir or normalized.startswith(os.pardir + os.sep)


def package_path_within(root: Path, relative_path: str) -> Path | None:
    """The path in the package that a path built from an ID names (``<family ID>.ped``,
    ``bams/<sample ID>.cram``), normalised; None when it leads out of the package.

    An ID is a name, but it can hold a path: an absolute one, or ``..`` that climbs out of
    the folder. The joined path is normalised and checked as a string first, so such a path
    is never looked up: nothing outside the package is read, nor found to exist. A path that
    stays in the folder goes through the package guard, so neither a symlink that leads out
    nor one that loops is followed. A leading ``~`` is part of the name, not the home folder
    (a manifest path may not start with one).
    """
    if not relative_path or os.path.isabs(relative_path):
        return None
    folder = str(root.resolve())
    path = os.path.normpath(os.path.join(folder, relative_path))
    if not path.startswith(folder + os.sep):
        return None
    try:
        inside = _package_path_or_none(Path(folder), path) is not None
    except ValueError:
        # A NUL in the name: no file is named after it.
        return None
    return Path(path) if inside else None


def _display_path(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _vcf_index_candidates(vcf_path: Path) -> list[Path]:
    return [
        Path(f"{vcf_path}.tbi"),
        Path(f"{vcf_path}.csi"),
        Path(f"{vcf_path}.idx"),
    ]


def _is_uncompressed_vcf(value: str | Path | None) -> bool:
    if value is None:
        return False
    return str(value).lower().endswith(".vcf")


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return value if isinstance(value, list) else []


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return value if isinstance(value, dict) else {}


def _issue_list(value: Any) -> list[FamilyImportValidationIssue]:
    return [FamilyImportValidationIssue.model_validate(item) for item in _json_list(value)]


def _dataset_summary_list(value: Any) -> list[FamilyImportDatasetSummary]:
    return [FamilyImportDatasetSummary.model_validate(item) for item in _json_list(value)]


def _model_list_json(models: list[BaseModel]) -> str:
    return json.dumps([model.model_dump(mode="json") for model in models])


def _metadata_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


_HEADER_NORMALIZER = re.compile(r"[^a-z0-9]+")


def _normalize_header_key(value: str) -> str:
    return _HEADER_NORMALIZER.sub("", value.strip().lower())


def _missing_scalar(value: Any) -> bool:
    return value is None or str(value).strip() in {"", "."}


def _coerce_int(value: Any) -> int | None:
    if _missing_scalar(value):
        return None
    # OverflowError: an infinite value ("inf", float("inf")) is no whole number either.
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        try:
            return int(float(str(value)))
        except (TypeError, ValueError, OverflowError):
            return None


def _coerce_finite_float(value: Any) -> float | None:
    if _missing_scalar(value):
        return None
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _read_package_text(path: Path) -> str:
    # Bound the read + decompression so a crafted package `.gz` (decompression bomb)
    # can't inflate to exhaust the import worker's memory — mirrors the bounded path
    # used for user uploads (upload_safety). The SV VCFs (NeedlR, HiFiCNV, chrM) flow
    # through here, each read whole.
    return read_path_text_bounded(path, kind="Package VCF")


@asynccontextmanager
async def _open_package_text(path: Path):
    handle = package_text_handle(path)
    try:
        yield handle
    finally:
        handle.close()


def _is_vcf_file(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith(".vcf") or name.endswith(".vcf.gz")


def _jsonb_safe(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _jsonb_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonb_safe(item) for item in value]
    return value


def _split_gene_symbols(value: str | None) -> list[str]:
    if value in (None, "", "."):
        return []
    genes: list[str] = []
    seen: set[str] = set()
    for raw in str(value).replace("|", ",").replace("&", ",").split(","):
        gene = raw.strip()
        if not gene or gene == "." or gene in seen:
            continue
        seen.add(gene)
        genes.append(gene)
    return genes


def _parse_vcf_info(info_field: str) -> dict[str, str]:
    info: dict[str, str] = {}
    if not info_field or info_field == ".":
        return info
    for item in info_field.split(";"):
        if not item:
            continue
        if "=" in item:
            key, value = item.split("=", 1)
            info[key] = value
        else:
            info[item] = "true"
    return info


def _parse_format(format_field: str, sample_field: str) -> dict[str, str]:
    keys = format_field.split(":") if format_field else []
    values = sample_field.split(":") if sample_field else []
    return {key: value for key, value in zip(keys, values)}


def _first_info_value(info: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = info.get(key)
        if not _missing_scalar(value):
            return value
    return None
