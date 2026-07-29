from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
import gzip
import json
import logging
import math
from pathlib import Path
import re
from typing import Any, Awaitable, Callable

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..schemas import (
    FamilyImportDatasetSummary,
    FamilyImportValidationIssue,
    FamilyPackageValidationOut,
)
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


APCAD_PCF_TRACK_TYPE = "apcad_pcf"


APCAD_PCF_SOURCE = "pcf"


# ClickHouse ``source`` tags for the long-read callsets. Each one scopes its own
# delete/re-import, so a family can hold NeedlR SVs, HiFiCNV CNV calls and chrM
# variants side by side and re-importing one never removes another.
CNV_SOURCE = "hificnv"


MITO_SOURCE = "mito"


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
    ped: str
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
    ped_path: Path
    ped: ParsedPed
    # When the package was staged from S3, the original s3:// source so provenance
    # records the durable S3 URI rather than the ephemeral staging path.
    source_uri: str | None = None


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


def _issue(
    code: str,
    message: str,
    *,
    dataset: str | None = None,
    sample_id: str | None = None,
    path: Path | str | None = None,
) -> FamilyImportValidationIssue:
    return FamilyImportValidationIssue(
        code=code,
        message=message,
        dataset=dataset,
        sample_id=sample_id,
        path=str(path) if path is not None else None,
    )


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
)


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


def read_vcf_sample_columns(path: Path) -> list[str]:
    """Sample columns from a VCF's ``#CHROM`` line, reading only as far as that line.

    Used by validation to check sample names without materialising a multi-GB VCF.
    Returns ``[]`` for a headerless or sample-less file (the annotated NeedlR VCF has
    only the eight fixed columns).
    """
    with (
        gzip.open(path, "rt", encoding="utf-8", errors="replace")
        if path.name.endswith(".gz")
        else path.open("r", encoding="utf-8", errors="replace")
    ) as handle:
        for line in handle:
            if line.startswith("#CHROM"):
                return line.rstrip("\n\r").split("\t")[9:]
            if not line.startswith("#"):
                break
    return []


def _resolve_package_path(root: Path, value: str | None) -> Path | None:
    if value is None or not str(value).strip():
        return None
    candidate = Path(str(value).strip()).expanduser()
    # A manifest-declared asset path must stay inside the (already-authorized) package
    # root. Resolving the joined path collapses any '..'/symlink and lets an absolute
    # value override the join, so the containment check below rejects a crafted manifest
    # trying to read arbitrary host files (e.g. `ped: /etc/passwd` or `../../etc/passwd`)
    # while still allowing ordinary relative paths and absolute paths that point inside
    # the package root.
    root_resolved = root.resolve()
    resolved = (root_resolved / candidate).resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="Package manifest paths must stay within the package directory",
        ) from exc
    return resolved


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
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(str(value)))
        except (TypeError, ValueError):
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
    # used for user uploads (upload_safety). Only the family SV VCF flows through here,
    # and it is read whole (used twice: record parsing + header-provenance mining).
    return read_path_text_bounded(path, kind="Package VCF")


@asynccontextmanager
async def _open_package_text(path: Path):
    if path.name.endswith(".gz"):
        handle = gzip.open(path, "rt", encoding="utf-8", errors="replace")
    else:
        handle = path.open("r", encoding="utf-8", errors="replace")
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
