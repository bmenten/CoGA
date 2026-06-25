from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager, suppress
import csv
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import gzip
import json
import logging
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Awaitable, Callable
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession
import yaml

from ..core.config import settings
from ..core.object_storage import (
    download_prefix,
    is_s3_uri,
    join_s3_uri,
    list_s3_package_candidates,
)
from ..core.postgres import get_postgres_sessionmaker
from ..schemas import (
    FamilyManifestDatasetAvailability,
    FamilyManifestFileAvailability,
    FamilyImportDatasetSummary,
    FamilyImportValidationIssue,
    FamilyPackageImportJobOut,
    FamilyPackageManifestBuildOut,
    FamilyPackageManifestBuildRequest,
    FamilyPackageManifestWriteOut,
    FamilyPackageValidationOut,
)
from .bed_service import upload_bed_data
from .clickhouse_family_variants import StructuralVariantCall, StructuralVariantRecord
from .clickhouse_interval_tracks import (
    count_interval_track_source_rows,
    delete_interval_track_sources,
    delete_interval_tracks,
    insert_interval_track_rows,
    upsert_interval_track_source,
)
from .clickhouse_variant_storage import (
    build_structural_variant_id,
    count_family_small_variants,
    count_family_structural_variants,
    delete_family_small_variants,
    replace_family_structural_variants,
)
from .data_scope import normalize_chromosome
from .family_metadata_context import (
    FamilyMetadataContext,
    SampleMetadataContext,
    build_family_metadata_context,
)
from .hpo_service import (
    HpoAnnotationImportIssue,
    HpoAnnotationImportRow,
    import_family_hpo_annotations,
    parse_hpo_tsv_path,
    parse_manifest_inline_hpo,
)
from .metadata_service import CurrentUser, get_current_user_by_email
from . import ped_service
from .raw_import_files_pg import record_raw_import_file
from .repeat_expansion_pg import (
    clear_sample_repeat_expansions,
    decode_repeat_upload_text,
    ingest_family_trgt_text,
    ingest_trgt_text,
)
from .variant_upload_service import upload_family_small_variant_file
from .bed_service import precompute_family_haplotype_lineage

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
)
APCAD_PCF_TRACK_TYPE = "apcad_pcf"
APCAD_PCF_SOURCE = "pcf"
FAMILY_IMPORT_WORKER_POLL_SECONDS = 2.0
FAMILY_IMPORT_STALE_HEARTBEAT = timedelta(minutes=10)

NAMING_SCHEMES: dict[str, dict[str, Any]] = {
    "standard_v1": {
        "label": "Standard family package",
        "datasets": {
            "snv": {
                "family_vcf": [
                    "snv/{family_id}.annotated.vcf.gz",
                    "snv/{family_id}/{family_id}_phased.vcf.gz",
                    "snv/{family_id}/{family_id}.vcf.gz",
                    "snv/{family_id}_phased.vcf.gz",
                    "snv/family.annotated.vcf.gz",
                ],
                "index": [
                    "snv/{family_id}.annotated.vcf.gz.tbi",
                    "snv/{family_id}/{family_id}_phased.vcf.gz.tbi",
                    "snv/{family_id}/{family_id}_phased.vcf.gz.csi",
                    "snv/{family_id}/{family_id}.vcf.gz.tbi",
                    "snv/{family_id}/{family_id}.vcf.gz.csi",
                    "snv/{family_id}_phased.vcf.gz.tbi",
                    "snv/{family_id}_phased.vcf.gz.csi",
                    "snv/family.annotated.vcf.gz.tbi",
                ],
                "annotation_tsv": [
                    "snv/annotation/{family_id}_annot.tsv.gz",
                    "snv/annotation/{family_id}.annot.tsv.gz",
                    "snv/{family_id}_annot.tsv.gz",
                    "snv/{family_id}.annot.tsv.gz",
                ],
            },
            "sv_needlr": {
                "family_vcf": [
                    "needlr/{family_id}.sv.annotated.vcf.gz",
                    "needlr/family.sv.annotated.vcf.gz",
                    "sv_needlr/{family_id}.sv.annotated.vcf.gz",
                    "sv_needlr/family.sv.annotated.vcf.gz",
                ],
                "index": [
                    "needlr/{family_id}.sv.annotated.vcf.gz.tbi",
                    "needlr/family.sv.annotated.vcf.gz.tbi",
                    "sv_needlr/{family_id}.sv.annotated.vcf.gz.tbi",
                    "sv_needlr/family.sv.annotated.vcf.gz.tbi",
                ],
            },
            "repeats_trgt": {
                "family_vcf": [
                    "repeats/{family_id}.trgt.vcf.gz",
                    "repeats/{family_id}_tr.vcf.gz",
                    "repeats/{family_id}.trgt.vcf",
                    "repeats/{family_id}_tr.vcf",
                    "repeats/family.trgt.vcf.gz",
                    "repeats/family.trgt.vcf",
                ],
                "index": [
                    "repeats/{family_id}.trgt.vcf.gz.tbi",
                    "repeats/{family_id}.trgt.vcf.gz.csi",
                    "repeats/{family_id}_tr.vcf.gz.tbi",
                    "repeats/{family_id}_tr.vcf.gz.csi",
                    "repeats/{family_id}.trgt.vcf.tbi",
                    "repeats/{family_id}.trgt.vcf.csi",
                    "repeats/{family_id}_tr.vcf.tbi",
                    "repeats/{family_id}_tr.vcf.csi",
                    "repeats/family.trgt.vcf.gz.tbi",
                    "repeats/family.trgt.vcf.gz.csi",
                    "repeats/family.trgt.vcf.tbi",
                    "repeats/family.trgt.vcf.csi",
                ],
            },
            "wisecondorx": {
                "bins": [
                    "wisecondorx/{sample_id}/bins.bed",
                    "wisecondorx/{sample_id}/sample_bins.bed",
                    "wisecondorx/{sample_id}/{sample_id}_bins.bed",
                ],
                "segments": [
                    "wisecondorx/{sample_id}/segments.bed",
                    "wisecondorx/{sample_id}/sample_segments.bed",
                    "wisecondorx/{sample_id}/{sample_id}_segments.bed",
                ],
            },
            "qdnaseq": {
                "bins": [
                    "QDNAseq/{sample_id}/bins.csv",
                    "QDNAseq/{sample_id}/sample_bins.csv",
                    "QDNAseq/{sample_id}/{sample_id}_bins.csv",
                    "QDNAseq/{sample_id}.bins.csv",
                    "QDNAseq/{sample_id}_bins.csv",
                    "QDNAseq/{sample_id}_cnv_results.csv",
                    "QDNAseq/{sample_id}.csv",
                    "qdnaseq/{sample_id}/bins.csv",
                    "qdnaseq/{sample_id}/sample_bins.csv",
                    "qdnaseq/{sample_id}/{sample_id}_bins.csv",
                    "qdnaseq/{sample_id}.bins.csv",
                    "qdnaseq/{sample_id}_bins.csv",
                    "qdnaseq/{sample_id}_cnv_results.csv",
                    "qdnaseq/{sample_id}.csv",
                ],
                "segments": [
                    "QDNAseq/{sample_id}/segments.csv",
                    "QDNAseq/{sample_id}/sample_segments.csv",
                    "QDNAseq/{sample_id}/{sample_id}_segments.csv",
                    "QDNAseq/{sample_id}.segments.csv",
                    "QDNAseq/{sample_id}_segments.csv",
                    "QDNAseq/{sample_id}_cnv_results.csv",
                    "qdnaseq/{sample_id}/segments.csv",
                    "qdnaseq/{sample_id}/sample_segments.csv",
                    "qdnaseq/{sample_id}/{sample_id}_segments.csv",
                    "qdnaseq/{sample_id}.segments.csv",
                    "qdnaseq/{sample_id}_segments.csv",
                    "qdnaseq/{sample_id}_cnv_results.csv",
                ],
            },
            "apcad": {
                "family_vcf": [
                    "APCAD/{family_id}_embryo_filtered_imp_parent.vcf.gz",
                    "APCAD/{family_id}_embryo_filtered_imp_parent.vcf",
                    "APCAD/{family_id}.apcad.vcf.gz",
                    "APCAD/{family_id}.apcad.vcf",
                    "APCAD/{family_id}.vcf.gz",
                    "APCAD/{family_id}.vcf",
                    "APCAD/family.apcad.vcf.gz",
                    "APCAD/family.apcad.vcf",
                    "APCAD/family.vcf.gz",
                    "APCAD/family.vcf",
                    "apcad/{family_id}_embryo_filtered_imp_parent.vcf.gz",
                    "apcad/{family_id}_embryo_filtered_imp_parent.vcf",
                    "apcad/{family_id}.apcad.vcf.gz",
                    "apcad/{family_id}.apcad.vcf",
                    "apcad/{family_id}.vcf.gz",
                    "apcad/{family_id}.vcf",
                    "apcad/family.apcad.vcf.gz",
                    "apcad/family.apcad.vcf",
                    "apcad/family.vcf.gz",
                    "apcad/family.vcf",
                ],
                "index": [
                    "APCAD/{family_id}_embryo_filtered_imp_parent.vcf.gz.tbi",
                    "APCAD/{family_id}_embryo_filtered_imp_parent.vcf.gz.csi",
                    "APCAD/{family_id}.apcad.vcf.gz.tbi",
                    "APCAD/{family_id}.apcad.vcf.gz.csi",
                    "APCAD/{family_id}.vcf.gz.tbi",
                    "APCAD/{family_id}.vcf.gz.csi",
                    "APCAD/family.apcad.vcf.gz.tbi",
                    "APCAD/family.apcad.vcf.gz.csi",
                    "APCAD/family.vcf.gz.tbi",
                    "APCAD/family.vcf.gz.csi",
                    "apcad/{family_id}_embryo_filtered_imp_parent.vcf.gz.tbi",
                    "apcad/{family_id}_embryo_filtered_imp_parent.vcf.gz.csi",
                    "apcad/{family_id}.apcad.vcf.gz.tbi",
                    "apcad/{family_id}.apcad.vcf.gz.csi",
                    "apcad/{family_id}.vcf.gz.tbi",
                    "apcad/{family_id}.vcf.gz.csi",
                    "apcad/family.apcad.vcf.gz.tbi",
                    "apcad/family.apcad.vcf.gz.csi",
                    "apcad/family.vcf.gz.tbi",
                    "apcad/family.vcf.gz.csi",
                ],
                "bed": [
                    "APCAD/{sample_id}.apcad.vcf.gz",
                    "APCAD/{sample_id}.apcad.vcf",
                    "APCAD/{sample_id}.vcf.gz",
                    "APCAD/{sample_id}.vcf",
                    "APCAD/{sample_id}.apcad.bed",
                    "APCAD/{sample_id}.bed",
                    "APCAD/{sample_id}.apcad.tsv",
                    "apcad/{sample_id}.apcad.bed",
                    "apcad/{sample_id}.bed",
                    "apcad/{sample_id}.apcad.tsv",
                    "apcad/{sample_id}.apcad.vcf.gz",
                    "apcad/{sample_id}.apcad.vcf",
                    "apcad/{sample_id}.vcf.gz",
                    "apcad/{sample_id}.vcf",
                ],
            },
            "pcf": {
                "maternal": [
                    "PCF/{sample_id}_pcf_mat_data.csv",
                    "PCF/{sample_id}.pcf.mat_data.csv",
                    "pcf/{sample_id}_pcf_mat_data.csv",
                    "pcf/{sample_id}.pcf.mat_data.csv",
                ],
                "paternal": [
                    "PCF/{sample_id}_pcf_pat_data.csv",
                    "PCF/{sample_id}.pcf.pat_data.csv",
                    "pcf/{sample_id}_pcf_pat_data.csv",
                    "pcf/{sample_id}.pcf.pat_data.csv",
                ],
            },
            "haplotypes": {
                "family_vcf": [
                    "GLIMPSE2/{family_id}_phased_final.vcf.gz",
                    "GLIMPSE2/{family_id}_phased_final.vcf",
                    "GLIMPSE2/{family_id}.glimpse2.vcf.gz",
                    "GLIMPSE2/{family_id}.glimpse2.vcf",
                    "GLIMPSE2/{family_id}.vcf.gz",
                    "GLIMPSE2/{family_id}.vcf",
                    "GLIMPSE2/family.glimpse2.vcf.gz",
                    "GLIMPSE2/family.glimpse2.vcf",
                    "GLIMPSE2/family.vcf.gz",
                    "GLIMPSE2/family.vcf",
                    "haplotypes/{family_id}_phased_final.vcf.gz",
                    "haplotypes/{family_id}_phased_final.vcf",
                    "haplotypes/{family_id}.glimpse2.vcf.gz",
                    "haplotypes/{family_id}.glimpse2.vcf",
                    "haplotypes/{family_id}.vcf.gz",
                    "haplotypes/{family_id}.vcf",
                    "haplotypes/family.glimpse2.vcf.gz",
                    "haplotypes/family.glimpse2.vcf",
                    "haplotypes/family.vcf.gz",
                    "haplotypes/family.vcf",
                ],
                "index": [
                    "GLIMPSE2/{family_id}_phased_final.vcf.gz.tbi",
                    "GLIMPSE2/{family_id}_phased_final.vcf.gz.csi",
                    "GLIMPSE2/{family_id}.glimpse2.vcf.gz.tbi",
                    "GLIMPSE2/{family_id}.glimpse2.vcf.gz.csi",
                    "GLIMPSE2/{family_id}.vcf.gz.tbi",
                    "GLIMPSE2/{family_id}.vcf.gz.csi",
                    "GLIMPSE2/family.glimpse2.vcf.gz.tbi",
                    "GLIMPSE2/family.glimpse2.vcf.gz.csi",
                    "GLIMPSE2/family.vcf.gz.tbi",
                    "GLIMPSE2/family.vcf.gz.csi",
                    "haplotypes/{family_id}_phased_final.vcf.gz.tbi",
                    "haplotypes/{family_id}_phased_final.vcf.gz.csi",
                    "haplotypes/{family_id}.glimpse2.vcf.gz.tbi",
                    "haplotypes/{family_id}.glimpse2.vcf.gz.csi",
                    "haplotypes/{family_id}.vcf.gz.tbi",
                    "haplotypes/{family_id}.vcf.gz.csi",
                    "haplotypes/family.glimpse2.vcf.gz.tbi",
                    "haplotypes/family.glimpse2.vcf.gz.csi",
                    "haplotypes/family.vcf.gz.tbi",
                    "haplotypes/family.vcf.gz.csi",
                ],
                "file": [
                    "GLIMPSE2/{sample_id}.glimpse2.bcf",
                    "haplotypes/{sample_id}.glimpse2.bcf",
                ],
                "bcf_index": [
                    "GLIMPSE2/{sample_id}.glimpse2.bcf.csi",
                    "haplotypes/{sample_id}.glimpse2.bcf.csi",
                ],
            },
            "paraphase": {
                "json": [
                    "paraphase/{sample_id}.paraphase.json",
                    "paraphase/{sample_id}/{sample_id}.paraphase.json",
                    "paraphase/{sample_id}.json",
                ],
            },
        },
    }
}


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


def _staging_root() -> Path:
    """Local scratch dir under which s3:// packages are staged for an import."""
    root = Path(tempfile.gettempdir()) / "coga-family-imports"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _authorized_local_roots() -> list[Path]:
    return [
        Path(root).expanduser().resolve()
        for root in settings.family_import_roots
        if not is_s3_uri(root)
    ]


def _authorized_s3_roots() -> list[str]:
    return [root.strip() for root in settings.family_import_roots if is_s3_uri(root)]


def _load_manifest_dict(manifest_path: Path) -> dict[str, Any]:
    """Loosely load a manifest (YAML/JSON) to a dict, or {} on any error."""
    try:
        text_value = manifest_path.read_text()
        if manifest_path.suffix in (".yaml", ".yml"):
            data = yaml.safe_load(text_value)
        else:
            data = json.loads(text_value)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _scan_manifest_info(manifest_path: Path) -> dict[str, Any]:
    """Loosely read a manifest's family_id / analysis_type for the package list."""
    data = _load_manifest_dict(manifest_path)
    return {"family_id": data.get("family_id"), "analysis_type": data.get("analysis_type")}


def _existing_manifest_dict(root: Path) -> dict[str, Any]:
    manifest_path = _find_manifest(root)
    return _load_manifest_dict(manifest_path) if manifest_path is not None else {}


def scan_family_import_packages() -> list[dict[str, Any]]:
    """List candidate family packages directly under each local import root.

    A candidate is an immediate subdirectory containing a manifest
    (manifest.yaml/.yml/.json) or a ``*.ped`` file. The folder path can then be
    selected in the import UI instead of typed by hand.
    """
    packages: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in _authorized_local_roots():
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir(), key=lambda path: path.name.lower()):
            if not child.is_dir():
                continue
            manifest_path = _find_manifest(child)
            ped_paths = sorted(child.glob("*.ped"))
            if manifest_path is None and not ped_paths:
                continue
            resolved = str(child.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            info = _scan_manifest_info(manifest_path) if manifest_path is not None else {}
            family_id = info.get("family_id") or child.name
            packages.append(
                {
                    "folder_path": resolved,
                    "name": child.name,
                    "family_id": str(family_id),
                    "has_manifest": manifest_path is not None,
                    "has_ped": bool(ped_paths),
                    "analysis_type": info.get("analysis_type"),
                }
            )
    for root_uri in _authorized_s3_roots():
        # S3 listing is best-effort: a misconfigured or unreachable bucket must
        # not break the local scan.
        try:
            candidates = list_s3_package_candidates(root_uri)
        except Exception:
            continue
        for candidate in candidates:
            uri = str(candidate["uri"])
            if uri in seen:
                continue
            seen.add(uri)
            name = str(candidate["name"])
            packages.append(
                {
                    "folder_path": uri,
                    "name": name,
                    "family_id": name,
                    "has_manifest": bool(candidate["has_manifest"]),
                    "has_ped": bool(candidate["has_ped"]),
                    "analysis_type": None,
                }
            )
    return packages


def _ensure_authorized_package_path(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    staging_root = _staging_root()
    # A package staged from S3 lives under the staging root and is pre-authorized
    # (its s3:// source was checked before download).
    if resolved == staging_root or staging_root in resolved.parents:
        return resolved
    allowed_roots = _authorized_local_roots()
    if not allowed_roots:
        return resolved
    if any(resolved == root or root in resolved.parents for root in allowed_roots):
        return resolved
    roots = ", ".join(str(root) for root in allowed_roots)
    raise HTTPException(
        status_code=403,
        detail=f"Family import path is outside configured FAMILY_IMPORT_ROOTS: {roots}",
    )


def _ensure_authorized_s3_source(uri: str) -> str:
    normalized = str(uri).strip()
    allowed = _authorized_s3_roots()
    if not allowed:
        raise HTTPException(
            status_code=403,
            detail="S3 family import sources require an s3:// entry in FAMILY_IMPORT_ROOTS",
        )
    for root in allowed:
        prefix = root.rstrip("/")
        if normalized == prefix or normalized.startswith(prefix + "/"):
            return normalized
    raise HTTPException(
        status_code=403,
        detail=f"S3 source is outside configured FAMILY_IMPORT_ROOTS: {', '.join(allowed)}",
    )


def _stage_s3_package(uri: str) -> Path:
    """Download an s3:// package prefix into a fresh temp dir under the staging root."""
    _ensure_authorized_s3_source(uri)
    dest = Path(tempfile.mkdtemp(prefix="pkg-", dir=_staging_root()))
    try:
        downloaded = download_prefix(uri, dest)
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    if downloaded == 0:
        shutil.rmtree(dest, ignore_errors=True)
        raise HTTPException(status_code=404, detail=f"No objects found at S3 family package source: {uri}")
    return dest


@contextmanager
def staged_package_source(folder_path: str | Path):
    """Yield ``(local_root, source_uri)``. For an s3:// source the package is
    downloaded to a temp dir (cleaned up on exit) and ``source_uri`` is the s3 URI;
    for a local path it is yielded unchanged with ``source_uri = None``."""
    if is_s3_uri(folder_path):
        uri = str(folder_path).strip()
        dest = _stage_s3_package(uri)
        try:
            yield str(dest), uri
        finally:
            shutil.rmtree(dest, ignore_errors=True)
    else:
        yield str(folder_path), None


@asynccontextmanager
async def staged_package_source_async(folder_path: str | Path):
    """Async variant: the S3 download (and cleanup) run in a worker thread so the
    event loop is not blocked during a large package transfer."""
    if is_s3_uri(folder_path):
        uri = str(folder_path).strip()
        dest = await asyncio.to_thread(_stage_s3_package, uri)
        try:
            yield str(dest), uri
        finally:
            await asyncio.to_thread(shutil.rmtree, dest, True)
    else:
        yield str(folder_path), None


def _manifest_candidates(root: Path) -> list[Path]:
    return [root / "manifest.yaml", root / "manifest.yml", root / "manifest.json"]


def _find_manifest(root: Path) -> Path | None:
    return next((candidate for candidate in _manifest_candidates(root) if candidate.is_file()), None)


def _parse_manifest(path: Path) -> tuple[dict[str, Any], PackageManifest]:
    """Return both the raw parsed payload and the validated model so callers can
    inspect the original keys (e.g. schema_version presence) without re-reading
    and re-parsing the file from disk."""
    raw_text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        payload = json.loads(raw_text)
    else:
        payload = yaml.safe_load(raw_text)
    if not isinstance(payload, dict):
        raise ValueError("Manifest must contain a mapping/object at the top level")
    return payload, PackageManifest.model_validate(payload)


def _resolve_package_path(root: Path, value: str | None) -> Path | None:
    if value is None or not str(value).strip():
        return None
    candidate = Path(str(value).strip()).expanduser()
    return candidate if candidate.is_absolute() else root / candidate


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


_PED_SEX_CODES = {
    "0": "0",
    "unknown": "0",
    "und": "0",
    "u": "0",
    "1": "1",
    "male": "1",
    "m": "1",
    "2": "2",
    "female": "2",
    "f": "2",
}
_PED_STATUS_VALUES = {
    "unknown": "unknown",
    "unk": "unknown",
    "normal": "unaffected",
    "unaffected": "unaffected",
    "healthy": "unaffected",
    "control": "unaffected",
    "affected": "affected",
    "case": "affected",
}
_PED_NUMERIC_STATUS_VALUES = {
    "-9": "unknown",
    "0": "unknown",
    "1": "unaffected",
    "2": "affected",
}
_PED_ROLE_VALUES = {"proband", "father", "mother", "sibling", "embryo", "relative"}
_TRUE_VALUES = {"1", "true", "yes", "y", "carrier"}
_INHERITANCE_MODELS = {"AD", "AR", "XLD", "XLR", "mitochondrial"}
_HEADER_NORMALIZER = re.compile(r"[^a-z0-9]+")


def _normalize_header_key(value: str) -> str:
    return _HEADER_NORMALIZER.sub("", value.strip().lower())


def _normalize_ped_sex(value: str) -> str | None:
    return _PED_SEX_CODES.get(value.strip().lower())


def _parse_ped_annotations(extra_columns: list[str]) -> tuple[dict[str, str], set[str]]:
    annotations: dict[str, str] = {}
    flags: set[str] = set()
    for raw_token in extra_columns:
        token = raw_token.strip()
        if not token:
            continue
        if "=" in token:
            key, value = token.split("=", 1)
            annotations[_normalize_header_key(key)] = value.strip()
        else:
            flags.add(token.lower())
    return annotations, flags


def _ped_clinical_status(
    phenotype: str,
    *,
    annotations: dict[str, str],
    flags: set[str],
    numeric_status_values: dict[str, str],
) -> str | None:
    for key in ("clinicalstatus", "status", "phenotype"):
        value = annotations.get(key)
        if value is None:
            continue
        normalized = _normalize_ped_status(value, numeric_status_values)
        if normalized is not None:
            return normalized
    for flag in flags:
        normalized = _normalize_ped_status(flag, numeric_status_values)
        if normalized is not None:
            return normalized
    return _normalize_ped_status(phenotype, numeric_status_values)


def _normalize_ped_status(value: str, numeric_status_values: dict[str, str]) -> str | None:
    token = value.strip().lower()
    return numeric_status_values.get(token) or _PED_STATUS_VALUES.get(token)


def _ped_numeric_status_values() -> dict[str, str]:
    return _PED_NUMERIC_STATUS_VALUES


def _ped_role_hint(
    *,
    annotations: dict[str, str],
    flags: set[str],
) -> str | None:
    for key in ("role", "sampletype", "type"):
        value = annotations.get(key)
        if value is None:
            continue
        normalized = value.strip().lower()
        if normalized in _PED_ROLE_VALUES:
            return normalized
    for flag in flags:
        if flag in _PED_ROLE_VALUES:
            return flag
    return None


def _ped_carrier_type(member: PedMember) -> str | None:
    for key in ("carriertype", "carrierkind", "carrierstatus"):
        value = member.extra.get(key)
        if value is None:
            continue
        normalized = str(value).strip().lower()
        if normalized in {"obligate", "proven"}:
            return normalized
    flags = {flag.lower() for flag in member.extra_columns}
    if {"obligatecarrier", "obligate_carrier", "obligate-carrier"}.intersection(flags):
        return "obligate"
    if {"provencarrier", "proven_carrier", "proven-carrier"}.intersection(flags):
        return "proven"
    return None


def _ped_is_carrier(member: PedMember) -> bool:
    if _ped_carrier_type(member) is not None:
        return True
    for key in ("carrier", "carrierstatus"):
        value = member.extra.get(key)
        if value is None:
            continue
        normalized = str(value).strip().lower()
        if normalized in _TRUE_VALUES or normalized in {"obligate", "proven"}:
            return True
    flags = {flag.lower() for flag in member.extra_columns}
    return bool({"carrier", "obligatecarrier", "provencarrier"}.intersection(flags))


def _lookup_normalized_key(payload: dict[str, Any], *keys: str) -> Any:
    normalized_keys = {_normalize_header_key(key) for key in keys}
    for key, value in payload.items():
        if _normalize_header_key(str(key)) in normalized_keys:
            return value
    return None


def _manifest_pgt_source(manifest: PackageManifest) -> dict[str, Any]:
    metadata = _metadata_dict(manifest.metadata)
    pgt_metadata = _metadata_dict(metadata.get("pgt"))
    extras = _metadata_dict(getattr(manifest, "model_extra", None))
    return {
        **extras,
        **metadata,
        **pgt_metadata,
    }


def _manifest_sample_id_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item for item in re.split(r"[\s,;]+", value.strip()) if item]
    if isinstance(value, (list, tuple, set)):
        sample_ids: list[str] = []
        for item in value:
            sample_ids.extend(_manifest_sample_id_list(item))
        return sample_ids
    return [str(value).strip()] if str(value).strip() else []


def _normalize_manifest_inheritance_model(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    for model in _INHERITANCE_MODELS:
        if normalized.lower() == model.lower():
            return model
    return None


def _manifest_pgt_metadata(manifest: PackageManifest) -> dict[str, Any]:
    source = _manifest_pgt_source(manifest)
    inheritance_model = _normalize_manifest_inheritance_model(
        _lookup_normalized_key(source, "inheritance_model", "inheritanceModel", "inheritance", "model")
    )
    obligate_carriers = sorted(
        set(_manifest_sample_id_list(_lookup_normalized_key(source, "obligate_carriers", "obligateCarriers")))
    )
    proven_carriers = sorted(
        set(_manifest_sample_id_list(_lookup_normalized_key(source, "proven_carriers", "provenCarriers")))
    )
    metadata: dict[str, Any] = {}
    if inheritance_model:
        metadata["inheritance_model"] = inheritance_model
    if obligate_carriers:
        metadata["obligate_carriers"] = obligate_carriers
    if proven_carriers:
        metadata["proven_carriers"] = proven_carriers
    return metadata


def _manifest_carrier_types(manifest: PackageManifest) -> dict[str, str]:
    pgt_metadata = _manifest_pgt_metadata(manifest)
    carrier_types: dict[str, str] = {}
    for sample_id in pgt_metadata.get("obligate_carriers", []):
        carrier_types[str(sample_id)] = "obligate"
    for sample_id in pgt_metadata.get("proven_carriers", []):
        carrier_types[str(sample_id)] = "proven"
    return carrier_types


def _manifest_family_payload(manifest: PackageManifest) -> dict[str, Any]:
    extras = _metadata_dict(getattr(manifest, "model_extra", None))
    return _metadata_dict(extras.get("family"))


def _normalize_manifest_clinical_status(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"unknown", "unaffected", "affected"}:
        return normalized
    if normalized in {"0", "-9"}:
        return "unknown"
    if normalized == "1":
        return "unaffected"
    if normalized == "2":
        return "affected"
    return None


def _normalize_manifest_carrier_status(value: Any, carrier_type: str | None = None) -> str | None:
    if value is None:
        return "carrier" if carrier_type else None
    normalized = str(value).strip().lower()
    if normalized in {"unknown", "not_carrier", "carrier"}:
        return "carrier" if carrier_type and normalized != "carrier" else normalized
    if normalized in {"1", "true", "yes", "y"}:
        return "carrier"
    if normalized in {"0", "false", "no", "n"}:
        return "not_carrier"
    return "carrier" if carrier_type else None


def _normalize_manifest_carrier_type(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized if normalized in {"obligate", "proven", "reported", "inferred"} else None


def _manifest_member_overrides(manifest: PackageManifest) -> dict[str, dict[str, Any]]:
    members = _manifest_family_payload(manifest).get("members")
    if not isinstance(members, dict):
        return {}
    overrides: dict[str, dict[str, Any]] = {}
    for sample_id, payload in members.items():
        if not isinstance(payload, dict):
            continue
        carrier_type = _normalize_manifest_carrier_type(
            _lookup_normalized_key(payload, "carrier_type", "carrierType")
        )
        carrier_status = _normalize_manifest_carrier_status(
            _lookup_normalized_key(payload, "carrier_status", "carrierStatus", "carrier"),
            carrier_type,
        )
        override: dict[str, Any] = {}
        clinical_status = _normalize_manifest_clinical_status(
            _lookup_normalized_key(payload, "clinical_status", "clinicalStatus", "phenotype")
        )
        if clinical_status:
            override["clinical_status"] = clinical_status
        if carrier_status:
            override["carrier_status"] = carrier_status
        if carrier_type:
            override["carrier_type"] = carrier_type
        evidence = _metadata_dict(_lookup_normalized_key(payload, "carrier_evidence", "carrierEvidence"))
        if evidence:
            override["carrier_evidence"] = evidence
        role = _lookup_normalized_key(payload, "role")
        if isinstance(role, str) and role.strip().lower() in _PED_ROLE_VALUES:
            override["role"] = role.strip().lower()
        overrides[str(sample_id)] = override
    return overrides


def _manifest_relationships(manifest: PackageManifest) -> list[dict[str, Any]]:
    relationships = _metadata_dict(_manifest_family_payload(manifest).get("relationships"))
    result: list[dict[str, Any]] = []
    couples = relationships.get("couples")
    if isinstance(couples, list):
        for couple in couples:
            if not isinstance(couple, dict):
                continue
            partners = couple.get("partners")
            if not isinstance(partners, list) or len(partners) != 2:
                continue
            metadata = _metadata_dict(couple.get("metadata"))
            if couple.get("context"):
                metadata["context"] = str(couple["context"])
            result.append(
                {
                    "relationship_type": "couple",
                    "sample_id_a": str(partners[0]),
                    "sample_id_b": str(partners[1]),
                    "role_a": "partner",
                    "role_b": "partner",
                    "source": "manifest",
                    "metadata": metadata,
                }
            )
    parent_child = relationships.get("parent_child")
    if isinstance(parent_child, list):
        for relationship in parent_child:
            if not isinstance(relationship, dict):
                continue
            child = relationship.get("child")
            parents = relationship.get("parents")
            if child is None or not isinstance(parents, list):
                continue
            for index, parent in enumerate(parents[:2]):
                if parent in {None, "", "0"}:
                    continue
                role = "father" if index == 0 else "mother"
                result.append(
                    {
                        "relationship_type": "parent_child",
                        "sample_id_a": str(parent),
                        "sample_id_b": str(child),
                        "role_a": role,
                        "role_b": "child",
                        "source": "manifest",
                        "metadata": {},
                    }
                )
    return result


def _parse_ped_text_strict(text_value: str) -> tuple[ParsedPed | None, list[FamilyImportValidationIssue]]:
    errors: list[FamilyImportValidationIssue] = []
    members: list[PedMember] = []
    seen_samples: set[str] = set()
    duplicate_samples: set[str] = set()
    rows: list[tuple[int, list[str]]] = []
    for line_no, line in enumerate(text_value.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) < 6:
            errors.append(
                _issue(
                    "ped_malformed_row",
                    f"PED row {line_no} has {len(parts)} columns; expected at least 6",
                )
            )
            continue
        rows.append((line_no, parts))

    numeric_status_values = _ped_numeric_status_values()
    for line_no, parts in rows:
        family_id, individual_id, father_id, mother_id, sex, phenotype = parts[:6]
        extra_columns = parts[6:]
        annotations, flags = _parse_ped_annotations(extra_columns)
        normalized_sex = _normalize_ped_sex(sex)
        clinical_status = _ped_clinical_status(
            phenotype,
            annotations=annotations,
            flags=flags,
            numeric_status_values=numeric_status_values,
        )
        role_hint = _ped_role_hint(annotations=annotations, flags=flags)
        if individual_id in seen_samples:
            duplicate_samples.add(individual_id)
        seen_samples.add(individual_id)
        if normalized_sex is None:
            errors.append(
                _issue(
                    "ped_invalid_sex",
                    f"PED row {line_no} has unsupported sex code '{sex}'",
                    sample_id=individual_id,
                )
            )
            normalized_sex = sex
        if clinical_status is None:
            errors.append(
                _issue(
                    "ped_invalid_phenotype",
                    f"PED row {line_no} has unsupported phenotype/status '{phenotype}'",
                    sample_id=individual_id,
                )
            )
            clinical_status = "unknown"
        members.append(
            PedMember(
                family_id=family_id,
                iid=individual_id,
                pid=father_id,
                mid=mother_id,
                sex=normalized_sex,
                phen=phenotype,
                line_no=line_no,
                clinical_status=clinical_status,
                role_hint=role_hint,
                extra=dict(annotations),
                extra_columns=extra_columns,
            )
        )

    if not members:
        errors.append(_issue("ped_empty", "PED file does not contain any sample rows"))
        return None, errors
    for sample_id in sorted(duplicate_samples):
        errors.append(_issue("ped_duplicate_sample", f"PED sample ID is duplicated: {sample_id}", sample_id=sample_id))

    sample_ids = [member.iid for member in members]
    sample_id_set = set(sample_ids)
    member_by_id = {member.iid: member for member in members}
    for member in members:
        if member.pid not in {"", "0"} and member.pid not in sample_id_set:
            errors.append(
                _issue(
                    "ped_missing_father",
                    f"Father ID '{member.pid}' for sample '{member.iid}' is not present in the PED",
                    sample_id=member.iid,
                )
            )
        if member.mid not in {"", "0"} and member.mid not in sample_id_set:
            errors.append(
                _issue(
                    "ped_missing_mother",
                    f"Mother ID '{member.mid}' for sample '{member.iid}' is not present in the PED",
                    sample_id=member.iid,
                )
            )
        father = member_by_id.get(member.pid)
        mother = member_by_id.get(member.mid)
        if father is not None and father.sex == "2":
            errors.append(
                _issue(
                    "ped_father_sex_mismatch",
                    f"Father ID '{member.pid}' for sample '{member.iid}' has female sex in the PED",
                    sample_id=member.iid,
                )
            )
        if mother is not None and mother.sex == "1":
            errors.append(
                _issue(
                    "ped_mother_sex_mismatch",
                    f"Mother ID '{member.mid}' for sample '{member.iid}' has male sex in the PED",
                    sample_id=member.iid,
                )
            )

    family_ids = list(dict.fromkeys(member.family_id for member in members))
    return ParsedPed(
        family_ids=family_ids,
        members=members,
        sample_ids=sample_ids,
        text="\n".join(
            " ".join(
                [
                    member.family_id,
                    member.iid,
                    member.pid,
                    member.mid,
                    member.sex,
                    member.phen,
                    *member.extra_columns,
                ]
            )
            for member in members
        ),
    ), errors


def _normalize_manifest_samples(samples: dict[str, Any] | list[Any] | None) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    if samples is None:
        return normalized
    if isinstance(samples, dict):
        for sample_id, payload in samples.items():
            normalized[str(sample_id)] = payload if isinstance(payload, dict) else {"value": payload}
        return normalized
    for entry in samples:
        if isinstance(entry, str):
            normalized[entry] = {}
            continue
        if not isinstance(entry, dict):
            continue
        sample_id = entry.get("sample_id") or entry.get("id")
        if sample_id:
            normalized[str(sample_id)] = dict(entry)
    return normalized


def _add_missing_optional_dataset_warnings(
    warnings: list[FamilyImportValidationIssue],
    summaries: list[FamilyImportDatasetSummary],
    present_datasets: set[str],
) -> None:
    for dataset_type in SUPPORTED_DATASETS:
        if dataset_type in present_datasets:
            continue
        warnings.append(
            _issue(
                "optional_dataset_missing",
                f"Optional dataset '{dataset_type}' is not present in the manifest",
                dataset=dataset_type,
            )
        )
        summaries.append(
            FamilyImportDatasetSummary(
                dataset_type=dataset_type,
                enabled=False,
                status="skipped",
                message="Optional dataset not present in manifest",
            )
        )


def _require_file(
    *,
    root: Path,
    dataset_type: str,
    value: str | None,
    field_name: str,
    errors: list[FamilyImportValidationIssue],
    files: list[str],
    sample_id: str | None = None,
) -> Path | None:
    path = _resolve_package_path(root, value)
    if path is None:
        errors.append(
            _issue(
                "dataset_missing_path",
                f"Dataset '{dataset_type}' is missing required path '{field_name}'",
                dataset=dataset_type,
                sample_id=sample_id,
            )
        )
        return None
    files.append(_display_path(root, path))
    if not path.is_file():
        errors.append(
            _issue(
                "dataset_file_missing",
                f"Referenced file does not exist for '{dataset_type}': {_display_path(root, path)}",
                dataset=dataset_type,
                sample_id=sample_id,
                path=path,
            )
        )
        return path
    return path


def _validate_vcf_index(
    *,
    root: Path,
    dataset_type: str,
    vcf_path: Path | None,
    index_value: str | None,
    errors: list[FamilyImportValidationIssue],
    files: list[str],
) -> None:
    if vcf_path is None:
        return
    if index_value:
        _require_file(
            root=root,
            dataset_type=dataset_type,
            value=index_value,
            field_name="index",
            errors=errors,
            files=files,
        )
        return
    for candidate in _vcf_index_candidates(vcf_path):
        if candidate.is_file():
            files.append(_display_path(root, candidate))
            return
    errors.append(
        _issue(
            "dataset_vcf_index_missing",
            f"VCF dataset '{dataset_type}' is missing an index file (.tbi/.csi/.idx)",
            dataset=dataset_type,
            path=vcf_path,
        )
    )


def _validate_family_vcf_dataset(
    *,
    root: Path,
    dataset_type: str,
    dataset: ManifestDataset,
    errors: list[FamilyImportValidationIssue],
) -> FamilyImportDatasetSummary:
    files: list[str] = []
    before = len(errors)
    vcf_path = _require_file(
        root=root,
        dataset_type=dataset_type,
        value=dataset.family_vcf,
        field_name="family_vcf",
        errors=errors,
        files=files,
    )
    # An uncompressed .vcf without a declared index is accepted for every
    # family-VCF dataset (the small-variant loader reads plain text and needs no
    # tabix index); a .gz still requires its index.
    index_optional = (
        vcf_path is not None
        and _is_uncompressed_vcf(vcf_path)
        and not dataset.index
    )
    if not index_optional:
        _validate_vcf_index(
            root=root,
            dataset_type=dataset_type,
            vcf_path=vcf_path,
            index_value=dataset.index,
            errors=errors,
            files=files,
        )
    if dataset_type == "snv" and dataset.annotation_tsv:
        _require_file(
            root=root,
            dataset_type=dataset_type,
            value=dataset.annotation_tsv,
            field_name="annotation_tsv",
            errors=errors,
            files=files,
        )
    return FamilyImportDatasetSummary(
        dataset_type=dataset_type,
        enabled=True,
        status="error" if len(errors) > before else "valid",
        files=list(dict.fromkeys(files)),
    )


def _sample_entry_mapping(
    *,
    dataset_type: str,
    sample_id: str,
    entry: Any,
    errors: list[FamilyImportValidationIssue],
) -> dict[str, Any]:
    if isinstance(entry, dict):
        return entry
    errors.append(
        _issue(
            "dataset_sample_entry_invalid",
            f"Dataset '{dataset_type}' entry for sample '{sample_id}' must be an object",
            dataset=dataset_type,
            sample_id=sample_id,
        )
    )
    return {}


def _validate_per_sample_id(
    *,
    dataset_type: str,
    sample_id: str,
    ped_sample_ids: set[str],
    errors: list[FamilyImportValidationIssue],
) -> None:
    if sample_id not in ped_sample_ids:
        errors.append(
            _issue(
                "dataset_unknown_sample",
                f"Dataset '{dataset_type}' references sample '{sample_id}', which is not present in the PED",
                dataset=dataset_type,
                sample_id=sample_id,
            )
        )


def _validate_wisecondorx_dataset(
    *,
    root: Path,
    dataset: ManifestDataset,
    ped_sample_ids: set[str],
    errors: list[FamilyImportValidationIssue],
) -> FamilyImportDatasetSummary:
    files: list[str] = []
    samples: list[str] = []
    before = len(errors)
    if not dataset.per_sample:
        errors.append(
            _issue(
                "dataset_per_sample_missing",
                "WisecondorX dataset must define per_sample entries",
                dataset="wisecondorx",
            )
        )
    for sample_id, raw_entry in dataset.per_sample.items():
        samples.append(sample_id)
        _validate_per_sample_id(
            dataset_type="wisecondorx",
            sample_id=sample_id,
            ped_sample_ids=ped_sample_ids,
            errors=errors,
        )
        entry = _sample_entry_mapping(
            dataset_type="wisecondorx",
            sample_id=sample_id,
            entry=raw_entry,
            errors=errors,
        )
        _require_file(
            root=root,
            dataset_type="wisecondorx",
            value=entry.get("bins"),
            field_name="bins",
            errors=errors,
            files=files,
            sample_id=sample_id,
        )
        _require_file(
            root=root,
            dataset_type="wisecondorx",
            value=entry.get("segments"),
            field_name="segments",
            errors=errors,
            files=files,
            sample_id=sample_id,
        )
    return FamilyImportDatasetSummary(
        dataset_type="wisecondorx",
        enabled=True,
        status="error" if len(errors) > before else "valid",
        files=list(dict.fromkeys(files)),
        samples=samples,
    )


def _validate_coverage_dataset(
    *,
    root: Path,
    dataset: ManifestDataset,
    ped_sample_ids: set[str],
    errors: list[FamilyImportValidationIssue],
) -> FamilyImportDatasetSummary:
    files: list[str] = []
    samples: list[str] = []
    before = len(errors)
    if not dataset.per_sample:
        errors.append(
            _issue(
                "dataset_per_sample_missing",
                "Coverage dataset must define per_sample entries",
                dataset="coverage",
            )
        )
    for sample_id, raw_entry in dataset.per_sample.items():
        samples.append(sample_id)
        _validate_per_sample_id(
            dataset_type="coverage",
            sample_id=sample_id,
            ped_sample_ids=ped_sample_ids,
            errors=errors,
        )
        entry = _sample_entry_mapping(
            dataset_type="coverage",
            sample_id=sample_id,
            entry=raw_entry,
            errors=errors,
        )
        _require_file(
            root=root,
            dataset_type="coverage",
            value=entry.get("bed") or entry.get("file"),
            field_name="bed",
            errors=errors,
            files=files,
            sample_id=sample_id,
        )
    return FamilyImportDatasetSummary(
        dataset_type="coverage",
        enabled=True,
        status="error" if len(errors) > before else "valid",
        files=list(dict.fromkeys(files)),
        samples=samples,
    )


def _validate_qdnaseq_dataset(
    *,
    root: Path,
    dataset: ManifestDataset,
    ped_sample_ids: set[str],
    errors: list[FamilyImportValidationIssue],
) -> FamilyImportDatasetSummary:
    files: list[str] = []
    samples: list[str] = []
    before = len(errors)
    if not dataset.per_sample:
        errors.append(
            _issue(
                "dataset_per_sample_missing",
                "QDNAseq dataset must define per_sample entries",
                dataset="qdnaseq",
            )
        )
    for sample_id, raw_entry in dataset.per_sample.items():
        samples.append(sample_id)
        _validate_per_sample_id(
            dataset_type="qdnaseq",
            sample_id=sample_id,
            ped_sample_ids=ped_sample_ids,
            errors=errors,
        )
        entry = _sample_entry_mapping(
            dataset_type="qdnaseq",
            sample_id=sample_id,
            entry=raw_entry,
            errors=errors,
        )
        bins_value = entry.get("bins") or entry.get("file")
        if bins_value is None:
            errors.append(
                _issue(
                    "dataset_missing_path",
                    "QDNAseq sample entries must define bins or file",
                    dataset="qdnaseq",
                    sample_id=sample_id,
                )
            )
        else:
            _require_file(
                root=root,
                dataset_type="qdnaseq",
                value=bins_value,
                field_name="bins",
                errors=errors,
                files=files,
                sample_id=sample_id,
            )
        if entry.get("segments"):
            _require_file(
                root=root,
                dataset_type="qdnaseq",
                value=entry.get("segments"),
                field_name="segments",
                errors=errors,
                files=files,
                sample_id=sample_id,
            )
    return FamilyImportDatasetSummary(
        dataset_type="qdnaseq",
        enabled=True,
        status="error" if len(errors) > before else "valid",
        files=list(dict.fromkeys(files)),
        samples=samples,
    )


def _validate_apcad_dataset(
    *,
    root: Path,
    dataset: ManifestDataset,
    ped_sample_ids: set[str],
    errors: list[FamilyImportValidationIssue],
) -> FamilyImportDatasetSummary:
    files: list[str] = []
    samples: list[str] = []
    before = len(errors)
    if dataset.family_vcf:
        family_vcf_path = _require_file(
            root=root,
            dataset_type="apcad",
            value=dataset.family_vcf,
            field_name="family_vcf",
            errors=errors,
            files=files,
        )
        if dataset.index:
            _require_file(
                root=root,
                dataset_type="apcad",
                value=dataset.index,
                field_name="index",
                errors=errors,
                files=files,
            )
        elif family_vcf_path is not None:
            for candidate in _vcf_index_candidates(family_vcf_path):
                if candidate.is_file():
                    files.append(_display_path(root, candidate))
                    break
    elif dataset.per_sample:
        for sample_id, raw_entry in dataset.per_sample.items():
            samples.append(sample_id)
            _validate_per_sample_id(
                dataset_type="apcad",
                sample_id=sample_id,
                ped_sample_ids=ped_sample_ids,
                errors=errors,
            )
            entry = _sample_entry_mapping(
                dataset_type="apcad",
                sample_id=sample_id,
                entry=raw_entry,
                errors=errors,
            )
            _require_file(
                root=root,
                dataset_type="apcad",
                value=entry.get("bed") or entry.get("file") or entry.get("vcf"),
                field_name="bed",
                errors=errors,
                files=files,
                sample_id=sample_id,
            )
    elif dataset.bed:
        _require_file(
            root=root,
            dataset_type="apcad",
            value=dataset.bed,
            field_name="bed",
            errors=errors,
            files=files,
        )
    else:
        errors.append(_issue("dataset_missing_path", "APCAD dataset must define bed or per_sample entries", dataset="apcad"))
    return FamilyImportDatasetSummary(
        dataset_type="apcad",
        enabled=True,
        status="error" if len(errors) > before else "valid",
        files=list(dict.fromkeys(files)),
        samples=samples,
    )


def _pcf_role_path(entry: dict[str, Any], role: str) -> Any:
    if role == "maternal":
        return entry.get("maternal") or entry.get("mat") or entry.get("maternal_file") or entry.get("mat_file")
    return entry.get("paternal") or entry.get("pat") or entry.get("paternal_file") or entry.get("pat_file")


def _validate_pcf_dataset(
    *,
    root: Path,
    dataset: ManifestDataset,
    ped_sample_ids: set[str],
    errors: list[FamilyImportValidationIssue],
) -> FamilyImportDatasetSummary:
    files: list[str] = []
    samples: list[str] = []
    before = len(errors)
    for sample_id, raw_entry in dataset.per_sample.items():
        entry = _sample_entry_mapping(
            dataset_type="pcf",
            sample_id=sample_id,
            entry=raw_entry,
            errors=errors,
        )
        sample_files = 0
        _validate_per_sample_id(
            dataset_type="pcf",
            sample_id=sample_id,
            ped_sample_ids=ped_sample_ids,
            errors=errors,
        )
        for role in ("maternal", "paternal"):
            value = _pcf_role_path(entry, role)
            if value is None:
                continue
            sample_files += 1
            _require_file(
                root=root,
                dataset_type="pcf",
                value=value,
                field_name=role,
                errors=errors,
                files=files,
                sample_id=sample_id,
            )
        if sample_files:
            samples.append(sample_id)
    if not samples:
        return FamilyImportDatasetSummary(
            dataset_type="pcf",
            enabled=True,
            status="skipped",
            message="No PCF files were provided",
        )
    return FamilyImportDatasetSummary(
        dataset_type="pcf",
        enabled=True,
        status="error" if len(errors) > before else "valid",
        files=list(dict.fromkeys(files)),
        samples=samples,
    )


def _validate_haplotypes_dataset(
    *,
    root: Path,
    dataset: ManifestDataset,
    ped_sample_ids: set[str],
    errors: list[FamilyImportValidationIssue],
) -> FamilyImportDatasetSummary:
    files: list[str] = []
    samples: list[str] = []
    before = len(errors)
    if dataset.family_vcf:
        family_vcf_path = _require_file(
            root=root,
            dataset_type="haplotypes",
            value=dataset.family_vcf,
            field_name="family_vcf",
            errors=errors,
            files=files,
        )
        if dataset.index:
            _require_file(
                root=root,
                dataset_type="haplotypes",
                value=dataset.index,
                field_name="index",
                errors=errors,
                files=files,
            )
        elif family_vcf_path is not None:
            for candidate in _vcf_index_candidates(family_vcf_path):
                if candidate.is_file():
                    files.append(_display_path(root, candidate))
                    break
        return FamilyImportDatasetSummary(
            dataset_type="haplotypes",
            enabled=True,
            status="error" if len(errors) > before else "valid",
            files=list(dict.fromkeys(files)),
        )
    if not dataset.per_sample:
        errors.append(
            _issue(
                "dataset_per_sample_missing",
                "Haplotype dataset must define family_vcf or per_sample entries",
                dataset="haplotypes",
            )
        )
    for sample_id, raw_entry in dataset.per_sample.items():
        samples.append(sample_id)
        _validate_per_sample_id(
            dataset_type="haplotypes",
            sample_id=sample_id,
            ped_sample_ids=ped_sample_ids,
            errors=errors,
        )
        entry = _sample_entry_mapping(
            dataset_type="haplotypes",
            sample_id=sample_id,
            entry=raw_entry,
            errors=errors,
        )
        _require_file(
            root=root,
            dataset_type="haplotypes",
            value=entry.get("file"),
            field_name="file",
            errors=errors,
            files=files,
            sample_id=sample_id,
        )
        _require_file(
            root=root,
            dataset_type="haplotypes",
            value=entry.get("index") or entry.get("bcf_index"),
            field_name="index",
            errors=errors,
            files=files,
            sample_id=sample_id,
        )
    return FamilyImportDatasetSummary(
        dataset_type="haplotypes",
        enabled=True,
        status="error" if len(errors) > before else "valid",
        files=list(dict.fromkeys(files)),
        samples=samples,
    )


def _validate_paraphase_dataset(
    *,
    root: Path,
    dataset: ManifestDataset,
    ped_sample_ids: set[str],
    errors: list[FamilyImportValidationIssue],
) -> FamilyImportDatasetSummary:
    files: list[str] = []
    samples: list[str] = []
    before = len(errors)
    if not dataset.per_sample:
        errors.append(
            _issue(
                "dataset_per_sample_missing",
                "Paraphase dataset must define per_sample entries",
                dataset="paraphase",
            )
        )
    for sample_id, raw_entry in dataset.per_sample.items():
        samples.append(sample_id)
        _validate_per_sample_id(
            dataset_type="paraphase",
            sample_id=sample_id,
            ped_sample_ids=ped_sample_ids,
            errors=errors,
        )
        entry = _sample_entry_mapping(
            dataset_type="paraphase",
            sample_id=sample_id,
            entry=raw_entry,
            errors=errors,
        )
        json_path = _require_file(
            root=root,
            dataset_type="paraphase",
            value=entry.get("json"),
            field_name="json",
            errors=errors,
            files=files,
            sample_id=sample_id,
        )
        if json_path is not None and json_path.is_file():
            try:
                json.loads(json_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                errors.append(
                    _issue(
                        "dataset_json_invalid",
                        f"Paraphase JSON for sample '{sample_id}' does not parse: {exc.msg}",
                        dataset="paraphase",
                        sample_id=sample_id,
                        path=json_path,
                    )
                )
    return FamilyImportDatasetSummary(
        dataset_type="paraphase",
        enabled=True,
        status="error" if len(errors) > before else "valid",
        files=list(dict.fromkeys(files)),
        samples=samples,
    )


def _validate_dataset(
    *,
    root: Path,
    dataset_type: str,
    dataset: ManifestDataset,
    ped_sample_ids: set[str],
    errors: list[FamilyImportValidationIssue],
) -> FamilyImportDatasetSummary:
    if not dataset.enabled:
        return FamilyImportDatasetSummary(
            dataset_type=dataset_type,
            enabled=False,
            status="disabled",
            message="Dataset disabled in manifest",
        )
    if dataset_type in {"snv", "sv_needlr", "repeats_trgt"}:
        return _validate_family_vcf_dataset(
            root=root,
            dataset_type=dataset_type,
            dataset=dataset,
            errors=errors,
        )
    if dataset_type == "wisecondorx":
        return _validate_wisecondorx_dataset(
            root=root,
            dataset=dataset,
            ped_sample_ids=ped_sample_ids,
            errors=errors,
        )
    if dataset_type == "coverage":
        return _validate_coverage_dataset(
            root=root,
            dataset=dataset,
            ped_sample_ids=ped_sample_ids,
            errors=errors,
        )
    if dataset_type == "qdnaseq":
        return _validate_qdnaseq_dataset(
            root=root,
            dataset=dataset,
            ped_sample_ids=ped_sample_ids,
            errors=errors,
        )
    if dataset_type == "apcad":
        return _validate_apcad_dataset(
            root=root,
            dataset=dataset,
            ped_sample_ids=ped_sample_ids,
            errors=errors,
        )
    if dataset_type == "pcf":
        return _validate_pcf_dataset(
            root=root,
            dataset=dataset,
            ped_sample_ids=ped_sample_ids,
            errors=errors,
        )
    if dataset_type == "haplotypes":
        return _validate_haplotypes_dataset(
            root=root,
            dataset=dataset,
            ped_sample_ids=ped_sample_ids,
            errors=errors,
        )
    if dataset_type == "paraphase":
        return _validate_paraphase_dataset(
            root=root,
            dataset=dataset,
            ped_sample_ids=ped_sample_ids,
            errors=errors,
        )
    return FamilyImportDatasetSummary(dataset_type=dataset_type, enabled=True, status="error")


def _manifest_individuals(manifest: PackageManifest) -> Any:
    return getattr(manifest, "individuals", None)


def _hpo_issue_to_validation_issue(
    issue: HpoAnnotationImportIssue,
    *,
    path: Path | None = None,
) -> FamilyImportValidationIssue:
    location = f"line {issue.line_no}: " if issue.line_no else ""
    return _issue(
        issue.code,
        f"{location}{issue.message}",
        dataset="phenotypes",
        sample_id=issue.sample_id,
        path=path,
    )


def _manifest_hpo_rows(
    *,
    root: Path,
    manifest: PackageManifest,
    family_id: str,
) -> tuple[list[HpoAnnotationImportRow], list[HpoAnnotationImportIssue], list[str], list[FamilyImportValidationIssue]]:
    rows: list[HpoAnnotationImportRow] = []
    issues: list[HpoAnnotationImportIssue] = []
    files: list[str] = []
    fatal_errors: list[FamilyImportValidationIssue] = []

    phenotype_config = manifest.phenotypes
    if phenotype_config is not None and phenotype_config.enabled:
        phenotype_format = phenotype_config.format.strip().lower()
        if phenotype_format != "hpo_tsv":
            fatal_errors.append(
                _issue(
                    "phenotype_format_unsupported",
                    f"Unsupported phenotypes format '{phenotype_config.format}'; expected hpo_tsv",
                    dataset="phenotypes",
                )
            )
        elif not phenotype_config.file:
            fatal_errors.append(
                _issue(
                    "phenotype_file_missing_path",
                    "Phenotypes manifest must define a file path",
                    dataset="phenotypes",
                )
            )
        else:
            phenotype_path = _resolve_package_path(root, phenotype_config.file)
            if phenotype_path is None:
                fatal_errors.append(
                    _issue(
                        "phenotype_file_missing_path",
                        "Phenotypes manifest must define a file path",
                        dataset="phenotypes",
                    )
                )
            else:
                files.append(_display_path(root, phenotype_path))
                if not phenotype_path.is_file():
                    fatal_errors.append(
                        _issue(
                            "phenotype_file_missing",
                            f"Phenotype TSV does not exist: {_display_path(root, phenotype_path)}",
                            dataset="phenotypes",
                            path=phenotype_path,
                        )
                    )
                else:
                    parsed_rows, parsed_issues = parse_hpo_tsv_path(
                        phenotype_path,
                        expected_family_id=family_id,
                        default_source=f"family_package:{phenotype_path.name}",
                    )
                    rows.extend(parsed_rows)
                    issues.extend(parsed_issues)
    inline_rows, inline_issues = parse_manifest_inline_hpo(
        _manifest_individuals(manifest),
        family_id=family_id,
    )
    rows.extend(inline_rows)
    issues.extend(inline_issues)
    return rows, issues, files, fatal_errors


def _validate_manifest_hpo_annotations(
    *,
    root: Path,
    manifest: PackageManifest,
    family_id: str,
    ped_sample_ids: set[str],
    errors: list[FamilyImportValidationIssue],
    warnings: list[FamilyImportValidationIssue],
) -> FamilyImportDatasetSummary | None:
    has_phenotype_block = manifest.phenotypes is not None
    has_inline_block = _manifest_individuals(manifest) is not None
    if not has_phenotype_block and not has_inline_block:
        return None
    if manifest.phenotypes is not None and not manifest.phenotypes.enabled and not has_inline_block:
        return FamilyImportDatasetSummary(
            dataset_type="phenotypes",
            enabled=False,
            status="disabled",
            message="Phenotype import disabled in manifest",
        )
    before_errors = len(errors)
    before_warnings = len(warnings)
    rows, issues, files, fatal_errors = _manifest_hpo_rows(
        root=root,
        manifest=manifest,
        family_id=family_id,
    )
    errors.extend(fatal_errors)
    for issue in issues:
        warnings.append(_hpo_issue_to_validation_issue(issue))
    for row in rows:
        if row.individual_id not in ped_sample_ids:
            warnings.append(
                _issue(
                    "phenotype_sample_unknown",
                    f"Phenotype annotation references '{row.individual_id}', which is not present in the PED",
                    dataset="phenotypes",
                    sample_id=row.individual_id,
                )
            )
    status = "error" if len(errors) > before_errors else "warning" if len(warnings) > before_warnings else "valid"
    return FamilyImportDatasetSummary(
        dataset_type="phenotypes",
        enabled=True,
        status=status,
        files=list(dict.fromkeys(files)),
        samples=sorted({row.individual_id for row in rows}),
        message=(
            f"Validated {len(rows)} HPO phenotype annotation row(s)"
            if rows
            else "No valid HPO phenotype annotation rows were found"
        ),
        summary={
            "rows": len(rows),
            "issues": len(issues),
            "assumption": "PED files remain structural; per-individual HPO annotations are imported from phenotypes.tsv or manifest individuals.*.hpo.",
        },
    )


def load_validated_family_package(
    folder_path: str | Path,
    *,
    fallback_ped_text: str | None = None,
) -> tuple[FamilyPackageValidationOut, FamilyPackageBundle | None]:
    try:
        root = _ensure_authorized_package_path(Path(folder_path))
    except HTTPException as exc:
        errors = [
            _issue(
                "package_folder_not_allowed",
                str(exc.detail),
                path=Path(folder_path).expanduser(),
            )
        ]
        return FamilyPackageValidationOut(valid=False, errors=errors), None
    errors: list[FamilyImportValidationIssue] = []
    warnings: list[FamilyImportValidationIssue] = []
    summaries: list[FamilyImportDatasetSummary] = []
    metadata: dict[str, Any] = {"schema_version": 1}

    if not root.exists():
        errors.append(_issue("package_folder_missing", "Family package folder does not exist", path=root))
        return FamilyPackageValidationOut(valid=False, errors=errors, warnings=warnings, datasets=summaries), None
    if not root.is_dir():
        errors.append(_issue("package_folder_not_directory", "Family package path is not a directory", path=root))
        return FamilyPackageValidationOut(valid=False, errors=errors, warnings=warnings, datasets=summaries), None

    manifest_path = _find_manifest(root)
    if manifest_path is None:
        errors.append(
            _issue(
                "manifest_missing",
                "Manifest file not found; expected manifest.yaml, manifest.yml, or manifest.json",
                path=root,
            )
        )
        return FamilyPackageValidationOut(valid=False, errors=errors, warnings=warnings, datasets=summaries), None

    try:
        manifest_payload, manifest = _parse_manifest(manifest_path)
    except (OSError, json.JSONDecodeError, ValueError, ValidationError, yaml.YAMLError) as exc:
        errors.append(_issue("manifest_parse_failed", f"Manifest could not be parsed: {exc}", path=manifest_path))
        return (
            FamilyPackageValidationOut(
                valid=False,
                manifest_path=str(manifest_path),
                errors=errors,
                warnings=warnings,
                datasets=summaries,
            ),
            None,
        )

    metadata = {
        "schema_version": manifest.schema_version,
        "manifest_metadata": manifest.metadata,
    }
    roi_query = _manifest_roi_value(manifest)
    if roi_query:
        metadata["roi"] = roi_query
    pgt_metadata = _manifest_pgt_metadata(manifest)
    if pgt_metadata:
        metadata["pgt"] = pgt_metadata
    if "schema_version" not in manifest_payload:
        warnings.append(
            _issue(
                "manifest_schema_version_missing",
                "Manifest did not specify schema_version; defaulting to schema_version 1",
                path=manifest_path,
            )
        )
    if manifest.schema_version != 1:
        errors.append(
            _issue(
                "manifest_schema_version_unsupported",
                f"Unsupported manifest schema_version {manifest.schema_version}; expected 1",
                path=manifest_path,
            )
        )

    family_id = (manifest.family_id or root.name).strip()
    ped_path = _resolve_package_path(root, manifest.ped)
    ped: ParsedPed | None = None
    ped_text: str | None = None
    if ped_path is not None and ped_path.is_file():
        try:
            ped_text = ped_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            errors.append(_issue("ped_decode_failed", f"PED file is not UTF-8 text: {exc}", path=ped_path))
    elif fallback_ped_text is not None:
        # No PED on disk: fall back to the pedigree already configured in the
        # database. This lets an incremental import add data to an existing
        # family without re-supplying a PED file.
        ped_text = fallback_ped_text
        metadata["ped_source"] = "database"
    elif ped_path is None:
        errors.append(_issue("ped_missing_path", "Manifest must define a PED path", path=manifest_path))
    else:
        errors.append(_issue("ped_file_missing", "PED file does not exist", path=ped_path))

    if ped_text is not None:
        ped, ped_errors = _parse_ped_text_strict(ped_text)
        errors.extend(ped_errors)

    if ped is not None:
        if len(ped.family_ids) > 1:
            errors.append(
                _issue(
                    "ped_multiple_families",
                    f"PED contains multiple family IDs: {', '.join(ped.family_ids)}",
                    path=ped_path,
                )
            )
        for ped_family_id in ped.family_ids:
            if ped_family_id != family_id:
                errors.append(
                    _issue(
                        "ped_family_mismatch",
                        f"PED family ID '{ped_family_id}' does not match package family_id '{family_id}'",
                        path=ped_path,
                    )
                )

        sample_metadata = _normalize_manifest_samples(manifest.samples)
        for sample_id in sample_metadata:
            if sample_id not in set(ped.sample_ids):
                errors.append(
                    _issue(
                        "manifest_sample_unknown",
                        f"Manifest samples section references '{sample_id}', which is not present in the PED",
                        sample_id=sample_id,
                    )
                )

        supported_set = set(SUPPORTED_DATASETS)
        present_datasets = set(manifest.datasets)
        for dataset_type in sorted(present_datasets - supported_set):
            errors.append(
                _issue(
                    "dataset_unsupported",
                    f"Unsupported dataset type in manifest: {dataset_type}",
                    dataset=dataset_type,
                )
            )
            summaries.append(
                FamilyImportDatasetSummary(
                    dataset_type=dataset_type,
                    enabled=True,
                    status="error",
                    message="Unsupported dataset type",
                )
            )

        ped_sample_ids = set(ped.sample_ids)
        for dataset_type in SUPPORTED_DATASETS:
            dataset = manifest.datasets.get(dataset_type)
            if dataset is None:
                continue
            summaries.append(
                _validate_dataset(
                    root=root,
                    dataset_type=dataset_type,
                    dataset=dataset,
                    ped_sample_ids=ped_sample_ids,
                    errors=errors,
                )
            )
        phenotype_summary = _validate_manifest_hpo_annotations(
            root=root,
            manifest=manifest,
            family_id=family_id,
            ped_sample_ids=ped_sample_ids,
            errors=errors,
            warnings=warnings,
        )
        if phenotype_summary is not None:
            summaries.append(phenotype_summary)
        _add_missing_optional_dataset_warnings(warnings, summaries, present_datasets)

    validation = FamilyPackageValidationOut(
        valid=not errors,
        family_id=family_id,
        manifest_path=str(manifest_path),
        ped_path=str(ped_path) if ped_path is not None else None,
        sample_ids=ped.sample_ids if ped is not None else [],
        errors=errors,
        warnings=warnings,
        datasets=summaries,
        metadata=metadata,
    )
    if errors or ped is None or ped_path is None:
        return validation, None
    return validation, FamilyPackageBundle(
        root=root,
        manifest_path=manifest_path,
        manifest=manifest,
        ped_path=ped_path,
        ped=ped,
    )


def validate_family_package(
    folder_path: str | Path,
    *,
    fallback_ped_text: str | None = None,
) -> FamilyPackageValidationOut:
    with staged_package_source(folder_path) as (local_root, _source_uri):
        validation, _bundle = load_validated_family_package(
            local_root,
            fallback_ped_text=fallback_ped_text,
        )
    return validation


async def db_pedigree_fallback(
    session: AsyncSession | None,
    requested_family_id: str | None,
) -> str | None:
    """PED text reconstructed from an existing family's database structure, or
    ``None`` when there is no existing family to fall back to. Used so imports
    that target an already-configured family do not require a PED file."""
    if session is None or not requested_family_id:
        return None
    try:
        return await ped_service.build_pedigree_text(session, family_id=requested_family_id)
    except HTTPException:
        return None


async def existing_family_sample_ids(
    session: AsyncSession,
    family_id: str | None,
) -> list[str]:
    """Sample IDs of an already-configured family, or ``[]`` when it does not
    exist. Used so manifest discovery can scan per-sample dataset files for an
    incremental import without requiring a PED file."""
    if not family_id:
        return []
    existing = await _fetch_existing_family(session, family_id=family_id)
    if existing is None:
        return []
    return [str(sample_id) for sample_id in existing.get("sample_ids", []) if sample_id]


def _format_pattern(pattern: str, *, family_id: str, sample_id: str | None = None) -> str:
    return pattern.format(family_id=family_id, sample_id=sample_id or "")


def _choose_candidate_path(
    root: Path,
    patterns: list[str],
    *,
    family_id: str,
    sample_id: str | None = None,
) -> tuple[str, bool]:
    rendered = [
        _format_pattern(pattern, family_id=family_id, sample_id=sample_id)
        for pattern in patterns
    ]
    for value in rendered:
        path = _resolve_package_path(root, value)
        if path is not None and path.is_file():
            return value, True
    return rendered[0], False


def _availability_file(
    *,
    root: Path,
    role: str,
    path_value: str,
    sample_id: str | None = None,
) -> FamilyManifestFileAvailability:
    path = _resolve_package_path(root, path_value)
    return FamilyManifestFileAvailability(
        role=role,
        path=path_value,
        exists=bool(path is not None and path.is_file()),
        sample_id=sample_id,
    )


def _detect_ped_path(
    root: Path,
    *,
    requested_ped_path: str | None,
    family_id: str,
) -> tuple[Path | None, list[FamilyImportValidationIssue], list[FamilyImportValidationIssue]]:
    errors: list[FamilyImportValidationIssue] = []
    warnings: list[FamilyImportValidationIssue] = []
    if requested_ped_path:
        ped_path = _resolve_package_path(root, requested_ped_path)
        if ped_path is None or not ped_path.is_file():
            errors.append(
                _issue(
                    "ped_file_missing",
                    "PED file does not exist",
                    path=ped_path or requested_ped_path,
                )
            )
            return None, errors, warnings
        return ped_path, errors, warnings

    preferred = root / f"{family_id}.ped"
    if preferred.is_file():
        return preferred, errors, warnings
    ped_files = sorted(root.glob("*.ped"))
    if len(ped_files) == 1:
        return ped_files[0], errors, warnings
    if len(ped_files) > 1:
        warnings.append(
            _issue(
                "ped_multiple_candidates",
                "Multiple PED files were found; choose one explicitly before writing a manifest",
                path=root,
            )
        )
        return None, errors, warnings
    errors.append(
        _issue(
            "ped_file_missing",
            "No PED file was found in the family folder",
            path=root,
        )
    )
    return None, errors, warnings


def _availability_from_manifest_dataset(
    root: Path, dataset_type: str, config: Any
) -> FamilyManifestDatasetAvailability:
    """Availability for a dataset the manifest already declares, based on whether
    its declared files exist -- so the discover table reflects an explicit
    manifest rather than only the filename scanner (which would mark a
    custom-named file like nipt_combined.vcf as not detected)."""
    files: list[FamilyManifestFileAvailability] = []
    samples: list[str] = []
    enabled = True

    def _add(role: str, value: Any, sample_id: str | None = None) -> None:
        if isinstance(value, str) and value.strip():
            files.append(
                FamilyManifestFileAvailability(
                    role=role,
                    path=value,
                    exists=(root / value).exists(),
                    sample_id=sample_id,
                )
            )

    if isinstance(config, dict):
        enabled = bool(config.get("enabled", True))
        for role in ("family_vcf", "index", "annotation_tsv", "bed", "vcf", "file", "json"):
            _add(role, config.get(role))
        per_sample = config.get("per_sample")
        if isinstance(per_sample, dict):
            for sample_id, entry in per_sample.items():
                samples.append(str(sample_id))
                if isinstance(entry, dict):
                    for role in ("bed", "vcf", "file", "bins", "segments", "json"):
                        _add(role, entry.get(role), sample_id=str(sample_id))
    complete = bool(files) and all(item.exists for item in files)
    return FamilyManifestDatasetAvailability(
        dataset_type=dataset_type,
        enabled=enabled and complete,
        complete=complete,
        files=files,
        samples=samples,
        message="Available (declared in manifest)"
        if complete
        else "Declared in manifest, but some files are missing",
    )


def _family_dataset_availability(
    *,
    root: Path,
    family_id: str,
    dataset_type: str,
    patterns: dict[str, list[str]],
) -> tuple[FamilyManifestDatasetAvailability, dict[str, Any]]:
    vcf_value, vcf_exists = _choose_candidate_path(
        root,
        patterns["family_vcf"],
        family_id=family_id,
    )
    index_value, index_exists = _choose_candidate_path(
        root,
        patterns["index"],
        family_id=family_id,
    )
    index_optional = dataset_type == "repeats_trgt" and _is_uncompressed_vcf(vcf_value)
    complete = vcf_exists and (index_exists or index_optional)
    files = [_availability_file(root=root, role="family_vcf", path_value=vcf_value)]
    if index_exists or not index_optional:
        files.append(_availability_file(root=root, role="index", path_value=index_value))
    manifest_block = {
        "enabled": complete,
        "family_vcf": vcf_value,
    }
    if index_exists or not index_optional:
        manifest_block["index"] = index_value
    if "annotation_tsv" in patterns:
        annotation_value, annotation_exists = _choose_candidate_path(
            root,
            patterns["annotation_tsv"],
            family_id=family_id,
        )
        if annotation_exists:
            files.append(_availability_file(root=root, role="annotation_tsv", path_value=annotation_value))
            manifest_block["annotation_tsv"] = annotation_value
    return (
        FamilyManifestDatasetAvailability(
            dataset_type=dataset_type,
            enabled=complete,
            complete=complete,
            files=files,
            message=(
                "Available"
                if complete
                else "Expected family VCF was not found"
                if index_optional
                else "Expected family VCF and index were not both found"
            ),
        ),
        manifest_block,
    )


def _per_sample_dataset_availability(
    *,
    root: Path,
    family_id: str,
    sample_ids: list[str],
    dataset_type: str,
    patterns: dict[str, list[str]],
    required_roles: list[str],
) -> tuple[FamilyManifestDatasetAvailability, dict[str, Any]]:
    files: list[FamilyManifestFileAvailability] = []
    per_sample: dict[str, dict[str, str]] = {}
    # Every sample's resolved role->path entry, kept so the incomplete-dataset
    # display below can reuse it instead of re-stat'ing each candidate path.
    all_sample_entries: dict[str, dict[str, str]] = {}
    complete_samples: list[str] = []
    for sample_id in sample_ids:
        sample_entry: dict[str, str] = {}
        sample_complete = True
        for role in required_roles:
            path_value, exists = _choose_candidate_path(
                root,
                patterns[role],
                family_id=family_id,
                sample_id=sample_id,
            )
            files.append(
                _availability_file(
                    root=root,
                    role=role,
                    path_value=path_value,
                    sample_id=sample_id,
                )
            )
            sample_complete = sample_complete and exists
            sample_entry[role] = path_value
        all_sample_entries[sample_id] = sample_entry
        if sample_complete:
            complete_samples.append(sample_id)
            per_sample[sample_id] = sample_entry

    complete = bool(complete_samples)
    display_entry: dict[str, Any] = {
        "enabled": complete,
        "per_sample": per_sample if complete else all_sample_entries,
    }
    return (
        FamilyManifestDatasetAvailability(
            dataset_type=dataset_type,
            enabled=complete,
            complete=complete,
            files=files,
            samples=complete_samples,
            message=(
                f"Available for {len(complete_samples)} sample(s)"
                if complete
                else "No complete per-sample file set found"
            ),
        ),
        display_entry,
    )


def _qdnaseq_dataset_availability(
    *,
    root: Path,
    family_id: str,
    sample_ids: list[str],
    patterns: dict[str, list[str]],
) -> tuple[FamilyManifestDatasetAvailability, dict[str, Any]]:
    files: list[FamilyManifestFileAvailability] = []
    per_sample: dict[str, dict[str, str]] = {}
    complete_samples: list[str] = []
    for sample_id in sample_ids:
        bins_value, bins_exists = _choose_candidate_path(
            root,
            patterns["bins"],
            family_id=family_id,
            sample_id=sample_id,
        )
        files.append(_availability_file(root=root, role="bins", path_value=bins_value, sample_id=sample_id))
        segments_value, segments_exists = _choose_candidate_path(
            root,
            patterns["segments"],
            family_id=family_id,
            sample_id=sample_id,
        )
        files.append(_availability_file(root=root, role="segments", path_value=segments_value, sample_id=sample_id))
        entry = {"bins": bins_value}
        if segments_exists:
            entry["segments"] = segments_value
        if bins_exists:
            complete_samples.append(sample_id)
            per_sample[sample_id] = entry
        else:
            per_sample[sample_id] = {"bins": bins_value, "segments": segments_value}
    complete = bool(complete_samples)
    return (
        FamilyManifestDatasetAvailability(
            dataset_type="qdnaseq",
            enabled=complete,
            complete=complete,
            files=files,
            samples=complete_samples,
            message=(
                f"Available for {len(complete_samples)} sample(s)"
                if complete
                else "No QDNAseq bin CSV files were found"
            ),
        ),
        {"enabled": complete, "per_sample": per_sample},
    )


def _apcad_dataset_availability(
    *,
    root: Path,
    family_id: str,
    sample_ids: list[str],
    patterns: dict[str, list[str]],
) -> tuple[FamilyManifestDatasetAvailability, dict[str, Any]]:
    family_vcf_value, family_vcf_exists = _choose_candidate_path(
        root,
        patterns["family_vcf"],
        family_id=family_id,
    )
    index_value, index_exists = _choose_candidate_path(
        root,
        patterns["index"],
        family_id=family_id,
    )
    if family_vcf_exists:
        files = [_availability_file(root=root, role="family_vcf", path_value=family_vcf_value)]
        if index_exists:
            files.append(_availability_file(root=root, role="index", path_value=index_value))
        block: dict[str, Any] = {"enabled": True, "family_vcf": family_vcf_value}
        if index_exists:
            block["index"] = index_value
        return (
            FamilyManifestDatasetAvailability(
                dataset_type="apcad",
                enabled=True,
                complete=True,
                files=files,
                message="Available as family APCAD VCF",
            ),
            block,
        )
    return _per_sample_dataset_availability(
        root=root,
        family_id=family_id,
        sample_ids=sample_ids,
        dataset_type="apcad",
        patterns=patterns,
        required_roles=["bed"],
    )


def _pcf_dataset_availability(
    *,
    root: Path,
    family_id: str,
    sample_ids: list[str],
    patterns: dict[str, list[str]],
) -> tuple[FamilyManifestDatasetAvailability, dict[str, Any]]:
    files: list[FamilyManifestFileAvailability] = []
    per_sample: dict[str, dict[str, str]] = {}
    complete_samples: list[str] = []
    for sample_id in sample_ids:
        entry: dict[str, str] = {}
        for role in ("maternal", "paternal"):
            path_value, exists = _choose_candidate_path(
                root,
                patterns[role],
                family_id=family_id,
                sample_id=sample_id,
            )
            files.append(
                _availability_file(
                    root=root,
                    role=role,
                    path_value=path_value,
                    sample_id=sample_id,
                )
            )
            if exists:
                entry[role] = path_value
        if entry:
            complete_samples.append(sample_id)
            per_sample[sample_id] = entry

    complete = bool(complete_samples)
    return (
        FamilyManifestDatasetAvailability(
            dataset_type="pcf",
            enabled=complete,
            complete=complete,
            files=files,
            samples=complete_samples,
            message=(
                f"Available for {len(complete_samples)} sample(s)"
                if complete
                else "No PCF segment CSV files were found"
            ),
        ),
        {"enabled": complete, "per_sample": per_sample},
    )


def _haplotypes_dataset_availability(
    *,
    root: Path,
    family_id: str,
    sample_ids: list[str],
    patterns: dict[str, list[str]],
) -> tuple[FamilyManifestDatasetAvailability, dict[str, Any]]:
    family_vcf_value, family_vcf_exists = _choose_candidate_path(
        root,
        patterns["family_vcf"],
        family_id=family_id,
    )
    index_value, index_exists = _choose_candidate_path(
        root,
        patterns["index"],
        family_id=family_id,
    )
    if family_vcf_exists:
        files = [_availability_file(root=root, role="family_vcf", path_value=family_vcf_value)]
        if index_exists:
            files.append(_availability_file(root=root, role="index", path_value=index_value))
        block: dict[str, Any] = {
            "enabled": True,
            "family_vcf": family_vcf_value,
            "source_format": "glimpse2",
        }
        if index_exists:
            block["index"] = index_value
        return (
            FamilyManifestDatasetAvailability(
                dataset_type="haplotypes",
                enabled=True,
                complete=True,
                files=files,
                message="Available as family GLIMPSE2 VCF",
            ),
            block,
        )
    return _per_sample_dataset_availability(
        root=root,
        family_id=family_id,
        sample_ids=sample_ids,
        dataset_type="haplotypes",
        patterns=patterns,
        required_roles=["file", "bcf_index"],
    )


def _build_manifest_payload(
    *,
    root: Path,
    family_id: str,
    ped_relative_path: str,
    sample_ids: list[str],
    naming_scheme: str,
    hpo_terms: list[str],
    notes: str | None,
) -> tuple[dict[str, Any], list[FamilyManifestDatasetAvailability]]:
    scheme = NAMING_SCHEMES[naming_scheme]["datasets"]
    datasets: dict[str, Any] = {}
    availability: list[FamilyManifestDatasetAvailability] = []
    for dataset_type in ("snv", "sv_needlr", "repeats_trgt"):
        item, block = _family_dataset_availability(
            root=root,
            family_id=family_id,
            dataset_type=dataset_type,
            patterns=scheme[dataset_type],
        )
        availability.append(item)
        datasets[dataset_type] = block

    per_sample_roles = {
        "wisecondorx": ["bins", "segments"],
        "paraphase": ["json"],
    }
    for dataset_type, roles in per_sample_roles.items():
        item, block = _per_sample_dataset_availability(
            root=root,
            family_id=family_id,
            sample_ids=sample_ids,
            dataset_type=dataset_type,
            patterns=scheme[dataset_type],
            required_roles=roles,
        )
        availability.append(item)
        datasets[dataset_type] = block

    item, block = _qdnaseq_dataset_availability(
        root=root,
        family_id=family_id,
        sample_ids=sample_ids,
        patterns=scheme["qdnaseq"],
    )
    availability.append(item)
    datasets["qdnaseq"] = block

    item, block = _apcad_dataset_availability(
        root=root,
        family_id=family_id,
        sample_ids=sample_ids,
        patterns=scheme["apcad"],
    )
    availability.append(item)
    datasets["apcad"] = block

    item, block = _pcf_dataset_availability(
        root=root,
        family_id=family_id,
        sample_ids=sample_ids,
        patterns=scheme["pcf"],
    )
    availability.append(item)
    datasets["pcf"] = block

    item, block = _haplotypes_dataset_availability(
        root=root,
        family_id=family_id,
        sample_ids=sample_ids,
        patterns=scheme["haplotypes"],
    )
    availability.append(item)
    datasets["haplotypes"] = block

    metadata: dict[str, Any] = {}
    cleaned_hpo = [term.strip() for term in hpo_terms if term.strip()]
    if cleaned_hpo:
        metadata["hpo"] = cleaned_hpo
    if notes and notes.strip():
        metadata["notes"] = notes.strip()
    payload: dict[str, Any] = {
        "schema_version": 1,
        "family_id": family_id,
        "ped": ped_relative_path,
    }
    if metadata:
        payload["metadata"] = metadata
    payload["samples"] = {sample_id: {} for sample_id in sample_ids}
    payload["datasets"] = datasets
    return payload, availability


def discover_family_package_manifest(
    request: FamilyPackageManifestBuildRequest,
    *,
    db_sample_ids: list[str] | None = None,
) -> FamilyPackageManifestBuildOut:
    try:
        root = _ensure_authorized_package_path(Path(request.folder_path))
    except HTTPException as exc:
        return FamilyPackageManifestBuildOut(
            valid=False,
            manifest_path=str(Path(request.folder_path).expanduser() / "manifest.yaml"),
            naming_scheme=request.naming_scheme,
            manifest_yaml="",
            errors=[
                _issue(
                    "package_folder_not_allowed",
                    str(exc.detail),
                    path=Path(request.folder_path).expanduser(),
                )
            ],
        )
    errors: list[FamilyImportValidationIssue] = []
    warnings: list[FamilyImportValidationIssue] = []
    if request.naming_scheme not in NAMING_SCHEMES:
        errors.append(
            _issue(
                "naming_scheme_unsupported",
                f"Unsupported naming scheme: {request.naming_scheme}",
            )
        )
        return FamilyPackageManifestBuildOut(
            valid=False,
            family_id=request.family_id,
            manifest_path=str(root / "manifest.yaml"),
            naming_scheme=request.naming_scheme,
            manifest_yaml="",
            errors=errors,
        )
    if not root.exists() or not root.is_dir():
        errors.append(
            _issue(
                "package_folder_missing",
                "Family package folder does not exist",
                path=root,
            )
        )
        return FamilyPackageManifestBuildOut(
            valid=False,
            family_id=request.family_id,
            manifest_path=str(root / "manifest.yaml"),
            naming_scheme=request.naming_scheme,
            manifest_yaml="",
            errors=errors,
        )

    # Prefer an explicit request id, then an existing manifest's family_id (the
    # validate/import path already does this), and only fall back to the folder
    # name. Otherwise a package whose folder name differs from its declared
    # family_id (and PED) is wrongly rejected as a mismatch on discover.
    existing_manifest = _existing_manifest_dict(root)
    manifest_family_id = existing_manifest.get("family_id")
    family_id = (
        request.family_id
        or (manifest_family_id if isinstance(manifest_family_id, str) else None)
        or root.name
    ).strip()
    ped_path, ped_errors, ped_warnings = _detect_ped_path(
        root,
        requested_ped_path=request.ped_path,
        family_id=family_id,
    )
    parsed_ped: ParsedPed | None = None
    use_db_structure = ped_path is None and bool(db_sample_ids)
    if use_db_structure:
        # Existing family with no PED on disk: take the sample list from the
        # family already configured in the database instead of erroring.
        warnings.append(
            _issue(
                "ped_from_database",
                "No PED file found; using the family structure already configured in the database.",
                path=root,
            )
        )
    else:
        errors.extend(ped_errors)
        warnings.extend(ped_warnings)
        if ped_path is not None:
            try:
                parsed_ped, ped_parse_errors = _parse_ped_text_strict(
                    ped_path.read_text(encoding="utf-8")
                )
                errors.extend(ped_parse_errors)
            except UnicodeDecodeError as exc:
                errors.append(_issue("ped_decode_failed", f"PED file is not UTF-8 text: {exc}", path=ped_path))

    if parsed_ped is not None:
        sample_ids = parsed_ped.sample_ids
    elif use_db_structure:
        sample_ids = list(db_sample_ids or [])
    else:
        sample_ids = []
    if parsed_ped is not None:
        if len(parsed_ped.family_ids) > 1:
            errors.append(
                _issue(
                    "ped_multiple_families",
                    f"PED contains multiple family IDs: {', '.join(parsed_ped.family_ids)}",
                    path=ped_path,
                )
            )
        for ped_family_id in parsed_ped.family_ids:
            if ped_family_id != family_id:
                errors.append(
                    _issue(
                        "ped_family_mismatch",
                        f"PED family ID '{ped_family_id}' does not match selected family_id '{family_id}'",
                        path=ped_path,
                    )
                )

    ped_relative_path = _display_path(root, ped_path) if ped_path is not None else (request.ped_path or f"{family_id}.ped")
    manifest_payload, availability = _build_manifest_payload(
        root=root,
        family_id=family_id,
        ped_relative_path=ped_relative_path,
        sample_ids=sample_ids,
        naming_scheme=request.naming_scheme,
        hpo_terms=request.hpo_terms,
        notes=request.notes,
    )
    # Preserve an existing manifest's analysis_type / samples (e.g. the NIPT tags)
    # so re-discovering and writing the manifest does not silently drop them.
    existing_analysis_type = existing_manifest.get("analysis_type")
    if isinstance(existing_analysis_type, str) and existing_analysis_type.strip():
        manifest_payload["analysis_type"] = existing_analysis_type.strip()
    existing_samples = existing_manifest.get("samples")
    if existing_samples:
        manifest_payload["samples"] = existing_samples
    # The existing manifest is authoritative for the datasets it declares; the
    # scanner only augments with newly detected ones. Without this, an explicit
    # dataset (e.g. snv: {family_vcf: nipt_combined.vcf}) is clobbered by the
    # disabled auto-detected block whose naming pattern matched nothing, so the
    # combined VCF would silently not import.
    existing_datasets = existing_manifest.get("datasets")
    if isinstance(existing_datasets, dict):
        payload_datasets = manifest_payload.setdefault("datasets", {})
        availability_by_type = {item.dataset_type: index for index, item in enumerate(availability)}
        for dataset_type, dataset_config in existing_datasets.items():
            payload_datasets[dataset_type] = dataset_config
            # Reflect the explicit dataset in the availability table too, so it is
            # not shown as "not enabled" just because its filename does not match
            # a scanner pattern.
            item = _availability_from_manifest_dataset(root, dataset_type, dataset_config)
            if dataset_type in availability_by_type:
                availability[availability_by_type[dataset_type]] = item
            else:
                availability_by_type[dataset_type] = len(availability)
                availability.append(item)
    manifest_yaml = yaml.safe_dump(
        manifest_payload,
        sort_keys=False,
        default_flow_style=False,
    )
    for item in availability:
        if not item.complete:
            warnings.append(
                _issue(
                    "dataset_not_detected",
                    item.message or f"{item.dataset_type} files were not detected",
                    dataset=item.dataset_type,
                )
            )

    return FamilyPackageManifestBuildOut(
        valid=not errors,
        family_id=family_id,
        ped_path=ped_relative_path,
        manifest_path=str(root / "manifest.yaml"),
        naming_scheme=request.naming_scheme,
        sample_ids=sample_ids,
        manifest_yaml=manifest_yaml,
        datasets=availability,
        errors=errors,
        warnings=warnings,
        metadata={
            "hpo_terms": [term.strip() for term in request.hpo_terms if term.strip()],
            "notes": request.notes.strip() if request.notes and request.notes.strip() else None,
        },
    )


def write_family_package_manifest(
    *,
    folder_path: str | Path,
    manifest_yaml: str,
    overwrite: bool,
    fallback_ped_text: str | None = None,
) -> FamilyPackageManifestWriteOut:
    root = _ensure_authorized_package_path(Path(folder_path))
    if not root.exists() or not root.is_dir():
        raise HTTPException(status_code=404, detail="Family package folder not found")
    manifest_path = root / "manifest.yaml"
    if manifest_path.exists() and not overwrite:
        raise HTTPException(status_code=409, detail="manifest.yaml already exists")
    try:
        payload = yaml.safe_load(manifest_yaml)
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=400, detail=f"Manifest YAML does not parse: {exc}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Manifest YAML must contain a mapping/object")
    PackageManifest.model_validate(payload)
    manifest_path.write_text(manifest_yaml, encoding="utf-8")
    return FamilyPackageManifestWriteOut(
        manifest_path=str(manifest_path),
        validation=validate_family_package(root, fallback_ped_text=fallback_ped_text),
    )


def _serialize_job(mapping: dict[str, Any]) -> FamilyPackageImportJobOut:
    return FamilyPackageImportJobOut(
        id=str(mapping["id"]),
        submitted_path=str(mapping["submitted_path"]),
        family_id=mapping.get("family_id"),
        project_id=str(mapping["project_id"]) if mapping.get("project_id") else None,
        status=mapping["status"],
        dry_run=bool(mapping.get("dry_run")),
        worker_id=mapping.get("worker_id"),
        requested_by=mapping["requested_by"],
        requested_at=mapping["requested_at"],
        started_at=mapping.get("started_at"),
        heartbeat_at=mapping.get("heartbeat_at"),
        completed_at=mapping.get("completed_at"),
        validation_errors=_issue_list(mapping.get("validation_errors")),
        validation_warnings=_issue_list(mapping.get("validation_warnings")),
        logs=[str(item) for item in _json_list(mapping.get("logs"))],
        datasets=_dataset_summary_list(mapping.get("dataset_summaries")),
        metadata=_json_dict(mapping.get("metadata")),
        error=mapping.get("error"),
    )


async def queue_family_import_job(
    session: AsyncSession,
    *,
    folder_path: str,
    project_id: str | None,
    dry_run: bool,
    requested_family_id: str | None = None,
    conflict_mode: str = "cancel",
    requested_by: str,
) -> FamilyPackageImportJobOut:
    metadata = {
        "requested_family_id": requested_family_id,
        "conflict_mode": conflict_mode,
    }
    result = await session.execute(
        text(
            """
            INSERT INTO family_import_jobs (
                submitted_path,
                project_id,
                status,
                dry_run,
                metadata,
                requested_by,
                requested_at
            )
            VALUES (
                :submitted_path,
                CAST(NULLIF(:project_id, '') AS uuid),
                'queued',
                :dry_run,
                CAST(:metadata AS jsonb),
                :requested_by,
                :requested_at
            )
            RETURNING
                id::text AS id,
                submitted_path,
                family_id,
                project_id::text AS project_id,
                status,
                dry_run,
                worker_id,
                requested_by,
                requested_at,
                started_at,
                heartbeat_at,
                completed_at,
                validation_errors,
                validation_warnings,
                logs,
                dataset_summaries,
                metadata,
                error
            """
        ),
        {
            "submitted_path": str(Path(folder_path).expanduser()),
            "project_id": project_id or "",
            "dry_run": dry_run,
            "metadata": json.dumps(metadata),
            "requested_by": requested_by,
            "requested_at": datetime.now(timezone.utc),
        },
    )
    await session.commit()
    return _serialize_job(dict(result.mappings().one()))


async def get_family_import_job(
    session: AsyncSession,
    *,
    job_id: str,
    user: CurrentUser,
) -> FamilyPackageImportJobOut:
    result = await session.execute(
        text(
            """
            SELECT
                id::text AS id,
                submitted_path,
                family_id,
                project_id::text AS project_id,
                status,
                dry_run,
                worker_id,
                requested_by,
                requested_at,
                started_at,
                heartbeat_at,
                completed_at,
                validation_errors,
                validation_warnings,
                logs,
                dataset_summaries,
                metadata,
                error
            FROM family_import_jobs
            WHERE id = CAST(:job_id AS uuid)
            """
        ),
        {"job_id": job_id},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Family import job not found")
    if user.role != "admin" and str(row["requested_by"]) != user.email:
        raise HTTPException(status_code=403, detail="Not authorized for this import job")
    return _serialize_job(dict(row))


async def list_family_import_jobs(
    session: AsyncSession,
    *,
    user: CurrentUser,
    limit: int = 25,
) -> list[FamilyPackageImportJobOut]:
    clauses: list[str] = []
    params: dict[str, Any] = {"limit": limit}
    if user.role != "admin":
        clauses.append("requested_by = :requested_by")
        params["requested_by"] = user.email
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    result = await session.execute(
        text(
            f"""
            SELECT
                id::text AS id,
                submitted_path,
                family_id,
                project_id::text AS project_id,
                status,
                dry_run,
                worker_id,
                requested_by,
                requested_at,
                started_at,
                heartbeat_at,
                completed_at,
                validation_errors,
                validation_warnings,
                logs,
                dataset_summaries,
                metadata,
                error
            FROM family_import_jobs
            {where}
            ORDER BY requested_at DESC
            LIMIT :limit
            """
        ),
        params,
    )
    return [_serialize_job(dict(row)) for row in result.mappings().all()]


async def claim_next_family_import_job(
    session: AsyncSession,
    *,
    worker_id: str,
) -> dict[str, Any] | None:
    now = datetime.now(timezone.utc)
    stale_before = now - FAMILY_IMPORT_STALE_HEARTBEAT
    result = await session.execute(
        text(
            """
            WITH candidate AS (
                SELECT id
                FROM family_import_jobs
                WHERE status = 'queued'
                   OR (status IN ('validating', 'running') AND heartbeat_at < :stale_before)
                   OR (status IN ('validating', 'running') AND heartbeat_at IS NULL)
                ORDER BY requested_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            UPDATE family_import_jobs AS job
            SET
                status = 'validating',
                worker_id = :worker_id,
                started_at = COALESCE(job.started_at, :now),
                heartbeat_at = :now,
                completed_at = NULL,
                error = NULL
            FROM candidate
            WHERE job.id = candidate.id
            RETURNING
                job.id::text AS id,
                job.submitted_path,
                job.family_id,
                job.project_id::text AS project_id,
                job.status,
                job.dry_run,
                job.worker_id,
                job.requested_by,
                job.requested_at,
                job.started_at,
                job.heartbeat_at,
                job.completed_at,
                job.validation_errors,
                job.validation_warnings,
                job.logs,
                job.dataset_summaries,
                job.metadata,
                job.error
            """
        ),
        {
            "worker_id": worker_id,
            "now": now,
            "stale_before": stale_before,
        },
    )
    row = result.mappings().first()
    if row is None:
        await session.rollback()
        return None
    await session.commit()
    return dict(row)


async def _update_job_progress(
    session: AsyncSession,
    *,
    job_id: str,
    worker_id: str | None,
    status: str | None = None,
    family_id: str | None = None,
    validation: FamilyPackageValidationOut | None = None,
    datasets: list[FamilyImportDatasetSummary] | None = None,
    logs: list[str] | None = None,
    error: str | None = None,
    completed: bool = False,
) -> None:
    params: dict[str, Any] = {
        "job_id": job_id,
        "heartbeat_at": datetime.now(timezone.utc),
    }
    clauses = ["heartbeat_at = :heartbeat_at"]
    if worker_id is not None:
        params["worker_id"] = worker_id
    if status is not None:
        clauses.append("status = :status")
        params["status"] = status
    if family_id is not None:
        clauses.append("family_id = :family_id")
        params["family_id"] = family_id
    if validation is not None:
        clauses.append("validation_errors = CAST(:validation_errors AS jsonb)")
        clauses.append("validation_warnings = CAST(:validation_warnings AS jsonb)")
        clauses.append("metadata = CAST(:metadata AS jsonb)")
        params["validation_errors"] = _model_list_json(validation.errors)
        params["validation_warnings"] = _model_list_json(validation.warnings)
        params["metadata"] = json.dumps(validation.metadata)
    if datasets is not None:
        clauses.append("dataset_summaries = CAST(:dataset_summaries AS jsonb)")
        params["dataset_summaries"] = _model_list_json(datasets)
    if logs is not None:
        clauses.append("logs = CAST(:logs AS jsonb)")
        params["logs"] = json.dumps(logs)
    if error is not None:
        clauses.append("error = :error")
        params["error"] = error
    if completed:
        clauses.append("completed_at = :completed_at")
        clauses.append("worker_id = NULL")
        params["completed_at"] = datetime.now(timezone.utc)

    worker_clause = " AND worker_id = :worker_id" if worker_id is not None else ""
    await session.execute(
        text(
            f"""
            UPDATE family_import_jobs
            SET {', '.join(clauses)}
            WHERE id = CAST(:job_id AS uuid)
            {worker_clause}
            """
        ),
        params,
    )
    await session.commit()


@asynccontextmanager
async def _local_upload(path: Path):
    handle = path.open("rb")
    upload = UploadFile(file=handle, filename=path.name)
    try:
        yield upload
    finally:
        await upload.close()


def _is_ped_embryo(member: PedMember, *, fathers: set[str], mothers: set[str]) -> bool:
    if member.role_hint == "embryo":
        return True
    if member.iid in fathers or member.iid in mothers:
        return False
    has_recorded_parents = member.pid not in {"", "0"} and member.mid not in {"", "0"}
    return has_recorded_parents and member.sex == "0" and member.clinical_status in {"unknown", "unaffected"}


def _ped_embryo_sample_ids(ped: ParsedPed) -> set[str]:
    fathers = {member.pid for member in ped.members if member.pid not in {"", "0"}}
    mothers = {member.mid for member in ped.members if member.mid not in {"", "0"}}
    return {
        member.iid
        for member in ped.members
        if _is_ped_embryo(member, fathers=fathers, mothers=mothers)
    }


def _ped_members_for_import(
    ped: ParsedPed,
    *,
    carrier_types: dict[str, str] | None = None,
    member_overrides: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    fathers = {member.pid for member in ped.members if member.pid not in {"", "0"}}
    mothers = {member.mid for member in ped.members if member.mid not in {"", "0"}}
    carrier_types = carrier_types or {}
    member_overrides = member_overrides or {}
    family_members: list[dict[str, Any]] = []
    assigned_proband = False
    for member in ped.members:
        role = member.role_hint if member.role_hint in _PED_ROLE_VALUES else None
        if member.iid in fathers:
            role = "father"
        elif member.iid in mothers:
            role = "mother"
        elif role is None and _is_ped_embryo(member, fathers=fathers, mothers=mothers):
            role = "embryo"
        elif role is None and member.clinical_status == "affected" and not assigned_proband:
            role = "proband"
        elif role is None and family_members:
            role = "sibling"
        elif role is None:
            role = "proband"
        if role == "proband":
            assigned_proband = True
        carrier_type = carrier_types.get(member.iid) or _ped_carrier_type(member)
        carrier_status = "carrier" if member.iid in carrier_types or _ped_is_carrier(member) else "unknown"
        override = member_overrides.get(member.iid, {})
        clinical_status = override.get("clinical_status") or member.clinical_status
        carrier_type = override.get("carrier_type") or carrier_type
        carrier_status = override.get("carrier_status") or (
            "carrier" if carrier_type else carrier_status
        )
        role = override.get("role") or role
        metadata: dict[str, Any] = {}
        if carrier_status == "carrier":
            metadata["carrier_status"] = True
        if carrier_type:
            metadata["carrier_type"] = carrier_type
        family_members.append(
            {
                "sample_id": member.iid,
                "father_id": member.pid if member.pid not in {"", "0"} else None,
                "mother_id": member.mid if member.mid not in {"", "0"} else None,
                "sex": {"1": "male", "2": "female"}.get(member.sex, "und"),
                "role": role,
                "clinical_status": clinical_status,
                "carrier_status": carrier_status,
                "carrier_type": carrier_type,
                "carrier_evidence": override.get("carrier_evidence") or {},
                "affected": clinical_status == "affected",
                "metadata": metadata,
            }
        )
    return family_members


def _family_sample_contexts(context: FamilyMetadataContext) -> dict[str, SampleMetadataContext]:
    return {
        row["sample_id"]: SampleMetadataContext(
            sample_uuid=row["sample_uuid"],
            sample_id=row["sample_id"],
            family_uuid=context.family_uuid,
            family_id=context.family_id,
            sex=row["sex"],
            project_ids=context.project_ids,
            assembly_id=context.assembly_id,
            assembly_name=context.assembly_name,
        )
        for row in context.sample_rows
    }


async def _fetch_existing_family(
    session: AsyncSession,
    *,
    family_id: str,
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            """
            SELECT
                f.id::text AS family_uuid,
                f.metadata,
                COALESCE(
                    ARRAY_AGG(DISTINCT s.sample_id) FILTER (WHERE s.sample_id IS NOT NULL),
                    '{}'::text[]
                ) AS sample_ids
            FROM families f
            LEFT JOIN samples s ON s.family_id = f.id
            WHERE f.family_id = :family_id
            GROUP BY f.id
            """
        ),
        {"family_id": family_id},
    )
    row = result.mappings().first()
    return dict(row) if row is not None else None


def _dataset_provenance(validation: FamilyPackageValidationOut) -> dict[str, Any]:
    return {
        summary.dataset_type: {
            "enabled": summary.enabled,
            "status": summary.status,
            "files": summary.files,
            "samples": summary.samples,
            "summary": summary.summary,
            "message": summary.message,
        }
        for summary in validation.datasets
        if summary.enabled
    }


def _sample_provenance(bundle: FamilyPackageBundle) -> dict[str, dict[str, Any]]:
    sample_payloads: dict[str, dict[str, Any]] = {}
    for dataset_type, dataset in bundle.manifest.datasets.items():
        if not dataset.enabled:
            continue
        for sample_id, raw_entry in dataset.per_sample.items():
            if not isinstance(raw_entry, dict):
                continue
            files = {
                key: _display_path(bundle.root, resolved)
                for key, value in raw_entry.items()
                if key in _PROVENANCE_PATH_KEYS
                for resolved in [_resolve_package_path(bundle.root, str(value))]
                if resolved is not None
            }
            sample_payloads.setdefault(sample_id, {})[dataset_type] = files
    return sample_payloads


_PROVENANCE_PATH_KEYS = {
    "bins",
    "segments",
    "file",
    "index",
    "bcf_index",
    "json",
    "bed",
    "vcf",
    "family_vcf",
    "annotation_tsv",
    "maternal",
    "paternal",
    "mat",
    "pat",
}


def _dataset_top_level_files(dataset: ManifestDataset) -> dict[str, str]:
    """Top-level (family-scoped) file references on a dataset, excluding per-sample
    entries. Includes manifest extras so non-standard keys are still captured."""
    payload: dict[str, Any] = dict(dataset.model_extra or {})
    payload.update(
        {
            "family_vcf": dataset.family_vcf,
            "annotation_tsv": dataset.annotation_tsv,
            "index": dataset.index,
            "bed": dataset.bed,
            "vcf": dataset.vcf,
            "file": dataset.file,
            "json": dataset.json_path,
        }
    )
    return {
        key: str(value)
        for key, value in payload.items()
        if key in _PROVENANCE_PATH_KEYS and isinstance(value, str) and value.strip()
    }


async def _record_package_raw_files(
    session: AsyncSession,
    *,
    bundle: FamilyPackageBundle,
    family_uuid: str,
) -> None:
    """Record provenance rows for every raw file referenced by the package manifest,
    grouped into family-level and individual-level scope. Files are referenced in
    place (not copied). Best-effort: never fails the import."""
    try:
        result = await session.execute(
            text(
                "SELECT id::text AS sample_uuid, sample_id "
                "FROM samples WHERE family_id = CAST(:family_uuid AS uuid)"
            ),
            {"family_uuid": family_uuid},
        )
        sample_uuid_by_id = {
            str(row["sample_id"]): str(row["sample_uuid"]) for row in result.mappings().all()
        }

        def _provenance_path(resolved: Path) -> str:
            # For an S3-staged package, record the durable s3:// URI (the staging
            # temp dir is deleted after the import); otherwise the local path.
            if bundle.source_uri:
                try:
                    relative = resolved.relative_to(bundle.root)
                except ValueError:
                    return str(resolved)
                return join_s3_uri(bundle.source_uri, str(relative))
            return str(resolved)

        for dataset_type, dataset in bundle.manifest.datasets.items():
            if not dataset.enabled:
                continue
            for value in _dataset_top_level_files(dataset).values():
                resolved = _resolve_package_path(bundle.root, value)
                if resolved is None or not resolved.exists() or not resolved.is_file():
                    continue
                await record_raw_import_file(
                    session,
                    family_uuid=family_uuid,
                    sample_uuid=None,
                    scope="family",
                    dataset=dataset_type,
                    file_name=resolved.name,
                    storage_path=_provenance_path(resolved),
                    managed=False,
                    source="family_package",
                )
            for sample_id, raw_entry in dataset.per_sample.items():
                if not isinstance(raw_entry, dict):
                    continue
                sample_uuid = sample_uuid_by_id.get(str(sample_id))
                for key, value in raw_entry.items():
                    if (
                        key not in _PROVENANCE_PATH_KEYS
                        or not isinstance(value, str)
                        or not value.strip()
                    ):
                        continue
                    resolved = _resolve_package_path(bundle.root, value)
                    if resolved is None or not resolved.exists() or not resolved.is_file():
                        continue
                    await record_raw_import_file(
                        session,
                        family_uuid=family_uuid,
                        sample_uuid=sample_uuid,
                        scope="individual",
                        dataset=dataset_type,
                        file_name=resolved.name,
                        storage_path=_provenance_path(resolved),
                        managed=False,
                        source="family_package",
                    )
    except Exception:  # pragma: no cover - provenance is non-critical
        logger.warning("Failed to record raw import file provenance", exc_info=True)


async def _register_package_provenance(
    session: AsyncSession,
    *,
    bundle: FamilyPackageBundle,
    validation: FamilyPackageValidationOut,
    family_uuid: str,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    family_result = await session.execute(
        text("SELECT metadata FROM families WHERE id = CAST(:family_uuid AS uuid)"),
        {"family_uuid": family_uuid},
    )
    family_metadata = _metadata_dict(family_result.scalar_one_or_none())
    pgt_metadata = _manifest_pgt_metadata(bundle.manifest)
    if pgt_metadata:
        family_metadata["pgt"] = {
            **_metadata_dict(family_metadata.get("pgt")),
            **pgt_metadata,
        }
    family_metadata["package_import"] = {
        "source": "family_package",
        "folder_path": str(bundle.root),
        "manifest_path": _display_path(bundle.root, bundle.manifest_path),
        "ped_path": _display_path(bundle.root, bundle.ped_path),
        "schema_version": bundle.manifest.schema_version,
        "family_id": validation.family_id,
        "metadata": bundle.manifest.metadata,
        "datasets": _dataset_provenance(validation),
        "registered_at": now,
    }
    # Promote a declared analysis type (e.g. monogenic_nipt) to the top level so
    # the workspace surfaces the matching analysis section.
    analysis_type = bundle.manifest.analysis_type
    if isinstance(analysis_type, str) and analysis_type.strip():
        family_metadata["analysis_type"] = analysis_type.strip()
    await session.execute(
        text(
            """
            UPDATE families
            SET metadata = CAST(:metadata AS jsonb),
                pedigree = :pedigree
            WHERE id = CAST(:family_uuid AS uuid)
            """
        ),
        {
            "family_uuid": family_uuid,
            "metadata": json.dumps(family_metadata),
            "pedigree": bundle.ped.text,
        },
    )

    sample_metadata = _normalize_manifest_samples(bundle.manifest.samples)
    sample_provenance = _sample_provenance(bundle)
    manifest_carrier_types = _manifest_carrier_types(bundle.manifest)
    member_overrides = _manifest_member_overrides(bundle.manifest)
    ped_member_state: dict[str, dict[str, Any]] = {}
    for member in bundle.ped.members:
        carrier_type = manifest_carrier_types.get(member.iid) or _ped_carrier_type(member)
        override = member_overrides.get(member.iid, {})
        clinical_status = override.get("clinical_status") or member.clinical_status
        carrier_type = override.get("carrier_type") or carrier_type
        carrier_status = override.get("carrier_status") or (
            "carrier" if carrier_type or member.iid in manifest_carrier_types or _ped_is_carrier(member) else "unknown"
        )
        ped_member_state[member.iid] = {
            "clinical_status": clinical_status,
            "carrier_status": carrier_status,
            "carrier_type": carrier_type,
            "carrier_evidence": override.get("carrier_evidence") or {},
            "role": override.get("role") or member.role_hint,
        }
    if sample_metadata or sample_provenance or ped_member_state:
        result = await session.execute(
            text(
                """
                SELECT id::text AS sample_uuid, sample_id, metadata
                FROM samples
                WHERE family_id = CAST(:family_uuid AS uuid)
                """
            ),
            {"family_uuid": family_uuid},
        )
        for row in result.mappings().all():
            sample_id = str(row["sample_id"])
            metadata = _metadata_dict(row.get("metadata"))
            if sample_id in ped_member_state:
                state = ped_member_state[sample_id]
                await session.execute(
                    text(
                        """
                        UPDATE family_members
                        SET clinical_status = :clinical_status,
                            carrier_status = :carrier_status,
                            carrier_type = :carrier_type,
                            carrier_evidence = CAST(:carrier_evidence AS jsonb),
                            affected = :affected,
                            updated_at = timezone('utc', now())
                        WHERE family_id = CAST(:family_uuid AS uuid)
                          AND sample_id = CAST(:sample_uuid AS uuid)
                        """
                    ),
                    {
                        "family_uuid": family_uuid,
                        "sample_uuid": str(row["sample_uuid"]),
                        "clinical_status": state["clinical_status"],
                        "carrier_status": state["carrier_status"],
                        "carrier_type": state.get("carrier_type"),
                        "carrier_evidence": json.dumps(state.get("carrier_evidence") or {}),
                        "affected": state["clinical_status"] == "affected",
                    },
                )
            if sample_id in sample_metadata:
                metadata["package_sample_metadata"] = sample_metadata[sample_id]
                # Promote a per-sample assay (e.g. nipt_cfdna) to the top level so
                # resolve_nipt_trio can identify the maternal-plasma cfDNA sample.
                assay = sample_metadata[sample_id].get("assay")
                if isinstance(assay, str) and assay.strip():
                    metadata["assay"] = assay.strip()
            if sample_id in sample_provenance:
                metadata["package_import"] = {
                    "source": "family_package",
                    "datasets": sample_provenance[sample_id],
                    "registered_at": now,
                }
            await session.execute(
                text(
                    """
                    UPDATE samples
                    SET metadata = CAST(:metadata AS jsonb)
                    WHERE id = CAST(:sample_uuid AS uuid)
                    """
                ),
                {
                    "sample_uuid": str(row["sample_uuid"]),
                    "metadata": json.dumps(metadata),
                },
            )
    await _record_package_raw_files(session, bundle=bundle, family_uuid=family_uuid)
    await session.commit()


_ROI_REGION_PATTERN = re.compile(
    r"^(?P<chrom>(?:chr)?[A-Za-z0-9_]+):(?P<start>[0-9,]+)(?:-(?P<end>[0-9,]+))?$",
    re.IGNORECASE,
)


def _manifest_roi_value(manifest: PackageManifest) -> str | None:
    raw_roi = manifest.roi if manifest.roi is not None else manifest.metadata.get("roi")
    if raw_roi is None:
        return None
    if isinstance(raw_roi, str):
        return raw_roi.strip() or None
    if isinstance(raw_roi, dict):
        for key in ("query", "gene", "region", "label"):
            value = raw_roi.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
    return None


async def _resolve_manifest_roi(
    session: AsyncSession,
    *,
    assembly_id: str | None,
    query: str,
) -> dict[str, Any] | None:
    if not assembly_id:
        return None
    region_match = _ROI_REGION_PATTERN.match(query.strip())
    if region_match:
        chrom = normalize_chromosome(region_match.group("chrom"))
        start = int(region_match.group("start").replace(",", ""))
        end_value = region_match.group("end")
        end = int(end_value.replace(",", "")) if end_value else start
        if end < start:
            start, end = end, start
        return {
            "query": query,
            "label": query,
            "source": "region",
            "assembly_id": assembly_id,
            "chr": chrom,
            "start": start,
            "end": end,
        }
    gene_result = await session.execute(
        text(
            """
            SELECT hgnc_symbol, gene_id, chr, start, "end"
            FROM genes
            WHERE assembly_id = CAST(:assembly_id AS uuid)
              AND (
                lower(hgnc_symbol) = lower(:query)
                OR lower(gene_id) = lower(:query)
              )
            ORDER BY ("end" - start) DESC, hgnc_symbol
            LIMIT 1
            """
        ),
        {"assembly_id": assembly_id, "query": query},
    )
    gene_row = gene_result.mappings().first()
    if gene_row is None:
        return None
    return {
        "query": query,
        "label": gene_row.get("hgnc_symbol") or gene_row.get("gene_id") or query,
        "source": "gene",
        "assembly_id": assembly_id,
        "chr": normalize_chromosome(str(gene_row["chr"])),
        "start": int(gene_row["start"]),
        "end": int(gene_row["end"]),
    }


async def _apply_manifest_roi(
    session: AsyncSession,
    *,
    bundle: FamilyPackageBundle,
    context: FamilyMetadataContext,
) -> None:
    roi_query = _manifest_roi_value(bundle.manifest)
    if not roi_query:
        return
    roi = await _resolve_manifest_roi(
        session,
        assembly_id=context.assembly_id,
        query=roi_query,
    )
    if roi is None:
        family_result = await session.execute(
            text("SELECT metadata FROM families WHERE id = CAST(:family_uuid AS uuid)"),
            {"family_uuid": context.family_uuid},
        )
        metadata = _metadata_dict(family_result.scalar_one_or_none())
        metadata["unresolved_roi"] = {"query": roi_query, "source": "manifest"}
        await session.execute(
            text(
                """
                UPDATE families
                SET metadata = CAST(:metadata AS jsonb)
                WHERE id = CAST(:family_uuid AS uuid)
                """
            ),
            {
                "family_uuid": context.family_uuid,
                "metadata": json.dumps(metadata),
            },
        )
        await session.commit()
        return
    await session.execute(
        text(
            """
            UPDATE families
            SET roi_query = :query,
                roi_label = :label,
                roi_source = :source,
                roi_assembly_id = CAST(:assembly_id AS uuid),
                roi_chr = :chr,
                roi_start = :start,
                roi_end = :end
            WHERE id = CAST(:family_uuid AS uuid)
            """
        ),
        {
            "family_uuid": context.family_uuid,
            "query": roi["query"],
            "label": roi["label"],
            "source": roi["source"],
            "assembly_id": roi["assembly_id"],
            "chr": roi["chr"],
            "start": roi["start"],
            "end": roi["end"],
        },
    )
    await session.commit()


async def _ensure_family_from_ped(
    session: AsyncSession,
    *,
    bundle: FamilyPackageBundle,
    project_id: str | None,
    user: CurrentUser,
    validation: FamilyPackageValidationOut,
    conflict_mode: str = "cancel",
) -> FamilyMetadataContext:
    resolved_project_id = await ped_service._resolve_accessible_project_id(session, user, project_id)
    family_id = validation.family_id or bundle.ped.family_ids[0]
    existing = await _fetch_existing_family(session, family_id=family_id)
    if existing is None:
        await ped_service._ensure_sample_ids_are_available(session, bundle.ped.sample_ids)
        members = _ped_members_for_import(
            bundle.ped,
            carrier_types=_manifest_carrier_types(bundle.manifest),
            member_overrides=_manifest_member_overrides(bundle.manifest),
        )
        relationships = ped_service._relationships_from_members(members)
        seen_relationships = {
            ped_service._relationship_key(
                relationship["relationship_type"],
                relationship["sample_id_a"],
                relationship["sample_id_b"],
                relationship.get("role_a"),
                relationship.get("role_b"),
            )
            for relationship in relationships
        }
        for relationship in _manifest_relationships(bundle.manifest):
            key = ped_service._relationship_key(
                relationship["relationship_type"],
                relationship["sample_id_a"],
                relationship["sample_id_b"],
                relationship.get("role_a"),
                relationship.get("role_b"),
            )
            if key not in seen_relationships:
                seen_relationships.add(key)
                relationships.append(relationship)
        await ped_service._create_family(
            session,
            family_id=family_id,
            pedigree=bundle.ped.text,
            members=members,
            relationships=relationships,
            project_id=resolved_project_id,
            created_by=user.id,
        )
        await session.commit()
    else:
        if conflict_mode == "cancel":
            raise RuntimeError(
                f"Family '{family_id}' already exists; choose update or overwrite to import data."
            )
        existing_samples = set(str(sample_id) for sample_id in existing.get("sample_ids", []) if sample_id)
        requested_samples = set(bundle.ped.sample_ids)
        if existing_samples != requested_samples:
            raise RuntimeError(
                "Existing family has different sample IDs; refusing to attach package import "
                f"to {family_id}"
            )
        if resolved_project_id is not None:
            await session.execute(
                text(
                    """
                    INSERT INTO family_projects (family_id, project_id)
                    VALUES (CAST(:family_uuid AS uuid), CAST(:project_id AS uuid))
                    ON CONFLICT DO NOTHING
                    """
                ),
                {"family_uuid": existing["family_uuid"], "project_id": resolved_project_id},
            )
            await session.execute(
                text(
                    """
                    INSERT INTO sample_projects (sample_id, project_id)
                    SELECT id, CAST(:project_id AS uuid)
                    FROM samples
                    WHERE family_id = CAST(:family_uuid AS uuid)
                    ON CONFLICT DO NOTHING
                    """
                ),
                {"family_uuid": existing["family_uuid"], "project_id": resolved_project_id},
            )
            await session.commit()

    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
        project_id=resolved_project_id,
    )
    await _register_package_provenance(
        session,
        bundle=bundle,
        validation=validation,
        family_uuid=context.family_uuid,
    )
    await _apply_manifest_roi(session, bundle=bundle, context=context)
    return context


def _enabled_dataset_summaries(validation: FamilyPackageValidationOut) -> list[FamilyImportDatasetSummary]:
    return [
        summary
        for summary in validation.datasets
        if summary.enabled and summary.status in {"valid", "warning"}
    ]


async def _register_only(summary: FamilyImportDatasetSummary, message: str) -> FamilyImportDatasetSummary:
    return summary.model_copy(
        update={
            "status": "registered",
            "message": message,
        }
    )


def _normalized_conflict_mode(value: str | None) -> str:
    return value if value in {"cancel", "update", "overwrite"} else "cancel"


def _execution_metadata(
    *,
    requested_family_id: str | None,
    conflict_mode: str,
) -> dict[str, Any]:
    return {
        "requested_family_id": requested_family_id,
        "conflict_mode": conflict_mode,
    }


def _merge_validation_metadata(
    validation: FamilyPackageValidationOut,
    metadata: dict[str, Any],
) -> FamilyPackageValidationOut:
    return validation.model_copy(
        update={
            "metadata": {
                **validation.metadata,
                **metadata,
            }
        }
    )


async def _existing_sample_ids(
    session: AsyncSession,
    sample_ids: list[str],
) -> list[str]:
    if not sample_ids:
        return []
    result = await session.execute(
        text(
            """
            SELECT sample_id
            FROM samples
            WHERE sample_id IN :sample_ids
            ORDER BY sample_id
            """
        ).bindparams(bindparam("sample_ids", expanding=True)),
        {"sample_ids": list(dict.fromkeys(sample_ids))},
    )
    return [str(row["sample_id"]) for row in result.mappings().all()]


async def _existing_package_entity_warnings(
    session: AsyncSession,
    *,
    family_id: str | None,
    sample_ids: list[str],
) -> list[FamilyImportValidationIssue]:
    warnings: list[FamilyImportValidationIssue] = []
    if family_id:
        existing_family = await _fetch_existing_family(session, family_id=family_id)
        if existing_family is not None:
            warnings.append(
                _issue(
                    "existing_family",
                    f"Family '{family_id}' already exists. Choose update, overwrite, or cancel before importing data.",
                )
            )
    existing_samples = await _existing_sample_ids(session, sample_ids)
    if existing_samples:
        preview = ", ".join(existing_samples[:10])
        suffix = "" if len(existing_samples) <= 10 else f", and {len(existing_samples) - 10} more"
        warnings.append(
            _issue(
                "existing_samples",
                f"Sample ID(s) already exist in the system: {preview}{suffix}.",
            )
        )
    return warnings


async def _interval_track_count(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    track_type: str,
    source: str | None = None,
) -> int:
    return await count_interval_track_source_rows(
        session,
        sample_uuid=sample_context.sample_uuid,
        track_type=track_type,
        source=source,
    )


async def _repeat_expansion_count(
    session: AsyncSession,
    *,
    sample_contexts: dict[str, SampleMetadataContext],
) -> int:
    sample_uuids = [context.sample_uuid for context in sample_contexts.values()]
    if not sample_uuids:
        return 0
    result = await session.execute(
        text(
            """
            SELECT COUNT(*)
            FROM repeat_expansions
            WHERE sample_id::text IN :sample_uuids
              AND source = 'trgt'
            """
        ).bindparams(bindparam("sample_uuids", expanding=True)),
        {"sample_uuids": sample_uuids},
    )
    return int(result.scalar_one() or 0)


async def _paraphase_count(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
) -> int:
    result = await session.execute(
        text(
            """
            SELECT COUNT(*)
            FROM sample_paraphase_results
            WHERE sample_id = CAST(:sample_id AS uuid)
            """
        ),
        {"sample_id": sample_context.sample_uuid},
    )
    return int(result.scalar_one() or 0)


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
    if path.name.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return handle.read()
    return path.read_text(encoding="utf-8")


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


async def _delete_sample_interval_track(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    track_type: str,
) -> None:
    if not sample_context.assembly_name:
        raise RuntimeError("Cannot delete interval tracks without an assembly name")
    await delete_interval_tracks(
        sample_context.assembly_name,
        sample_uuid=sample_context.sample_uuid,
        track_type=track_type,
    )
    await delete_interval_track_sources(
        session,
        sample_uuid=sample_context.sample_uuid,
        track_type=track_type,
    )


async def _delete_sample_interval_source(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    track_type: str,
    source: str,
    filename: str | None = None,
) -> None:
    if not sample_context.assembly_name:
        raise RuntimeError("Cannot delete interval tracks without an assembly name")
    await delete_interval_tracks(
        sample_context.assembly_name,
        sample_uuid=sample_context.sample_uuid,
        track_type=track_type,
        source=source,
        filename=filename,
    )
    await delete_interval_track_sources(
        session,
        sample_uuid=sample_context.sample_uuid,
        track_type=track_type,
        source=source,
        filename=filename,
    )


async def _insert_interval_track_rows(
    session: AsyncSession,
    rows: list[dict[str, Any]],
) -> None:
    _ = session
    if not rows:
        return
    assembly_names = {str(row.get("assembly_name") or "") for row in rows}
    assembly_names.discard("")
    if len(assembly_names) != 1:
        raise RuntimeError("Interval-track rows must belong to exactly one assembly")
    await insert_interval_track_rows(next(iter(assembly_names)), rows)


def _header_map(parts: list[str]) -> dict[str, int]:
    return {part.strip().lower(): index for index, part in enumerate(parts)}


def _normalized_header_map(parts: list[str]) -> dict[str, int]:
    return {_normalize_header_key(part): index for index, part in enumerate(parts)}


def _header_value(parts: list[str], header: dict[str, int], *names: str) -> str | None:
    for name in names:
        index = header.get(name)
        if index is not None and index < len(parts):
            return parts[index]
    return None


def _normalized_header_value(parts: list[str], header: dict[str, int], *names: str) -> str | None:
    for name in names:
        index = header.get(_normalize_header_key(name))
        if index is not None and index < len(parts):
            return parts[index]
    return None


def _split_delimited_line(line: str) -> list[str]:
    stripped = line.strip()
    if "," in stripped:
        return next(csv.reader([stripped]))
    if ";" in stripped:
        return next(csv.reader([stripped], delimiter=";"))
    if "\t" in stripped:
        return stripped.split("\t")
    return stripped.split()


def _looks_like_header(parts: list[str], required: set[str]) -> bool:
    normalized = {_normalize_header_key(part) for part in parts}
    return required.issubset(normalized)


def _looks_like_interval_header(parts: list[str]) -> bool:
    normalized = {_normalize_header_key(part) for part in parts}
    has_chrom = bool(normalized & {"chr", "chrom", "chromosome"})
    has_start = bool(normalized & {"start", "windowstart", "from", "pos", "position"})
    has_end = bool(normalized & {"end", "stop", "windowend", "to", "pos", "position"})
    return has_chrom and has_start and has_end


_COPY_NUMBER_VALUE_COLUMNS = (
    "ratio",
    "value",
    "log2",
    "log2ratio",
    "log2copyratio",
    "copynumber",
    "copy",
    "cn",
    "segmented",
    "segmentedratio",
    "segmean",
    "mean",
)
_COPY_NUMBER_SEGMENT_VALUE_COLUMNS = (
    "segmented",
    "segmentedratio",
    "segmean",
    "segmentmean",
    "segment",
    "ratio",
    "value",
    "log2",
    "log2ratio",
    "log2copyratio",
    "copynumber",
    "copy",
    "cn",
    "mean",
)
_COPY_NUMBER_METADATA_COLUMNS = (
    "zscore",
    "z",
    "call",
    "probes",
    "nprobes",
    "reads",
    "gc",
    "mappability",
    "blacklist",
    "residual",
    "use",
)


def _first_header_value(
    parts: list[str],
    header: dict[str, int],
    names: tuple[str, ...],
) -> str | None:
    return _normalized_header_value(parts, header, *names)


def _parse_copy_number_interval_row(
    parts: list[str],
    *,
    header: dict[str, int] | None,
    sample_context: SampleMetadataContext,
    track_type: str,
    source: str,
    path: Path,
    line_no: int,
) -> dict[str, Any] | None:
    if header is not None:
        chrom = _first_header_value(parts, header, ("chr", "chrom", "chromosome"))
        start_raw = _first_header_value(parts, header, ("start", "window_start", "from"))
        end_raw = _first_header_value(parts, header, ("end", "stop", "window_end", "to"))
        record_id = _first_header_value(parts, header, ("id", "record_id", "name", "bin"))
        value_raw = _first_header_value(
            parts,
            header,
            _COPY_NUMBER_SEGMENT_VALUE_COLUMNS if track_type == "segments" else _COPY_NUMBER_VALUE_COLUMNS,
        )
    else:
        if len(parts) < 4:
            return None
        chrom, start_raw, end_raw = parts[:3]
        record_id = parts[3] if len(parts) > 4 and _coerce_finite_float(parts[3]) is None else None
        value_candidates = parts[4:] if record_id is not None else parts[3:]
        value_raw = next(
            (value for value in value_candidates if _coerce_finite_float(value) is not None),
            None,
        )

    start = _coerce_int(start_raw)
    end = _coerce_int(end_raw)
    value = _coerce_finite_float(value_raw)
    if chrom is None or start is None or end is None or value is None:
        return None

    metadata: dict[str, Any] = {
        "source": source,
        "filename": path.name,
        "line_no": line_no,
    }
    if header is not None:
        for column in _COPY_NUMBER_METADATA_COLUMNS:
            raw_value = _normalized_header_value(parts, header, column)
            if raw_value in (None, ""):
                continue
            numeric_value = _coerce_finite_float(raw_value)
            metadata[_normalize_header_key(column)] = numeric_value if numeric_value is not None else raw_value

    return {
        "sample_id": sample_context.sample_uuid,
        "family_id": sample_context.family_uuid,
        "assembly_id": sample_context.assembly_id or "",
        "assembly_name": sample_context.assembly_name or "",
        "track_type": track_type,
        "source": source,
        "chr": normalize_chromosome(str(chrom)),
        "start": start,
        "end": end,
        "record_id": record_id or f"{chrom}:{start}-{end}",
        "value": value,
        "origin": None,
        "metadata_json": json.dumps(_jsonb_safe(metadata)),
    }


def _parse_wisecondorx_interval_row(
    parts: list[str],
    *,
    header: dict[str, int] | None,
    sample_context: SampleMetadataContext,
    track_type: str,
    path: Path,
    line_no: int,
) -> dict[str, Any] | None:
    if header is not None:
        chrom = _header_value(parts, header, "chr", "chrom", "chromosome")
        start_raw = _header_value(parts, header, "start", "window_start")
        end_raw = _header_value(parts, header, "end", "stop", "window_end")
        record_id = _header_value(parts, header, "id", "record_id", "name")
        value_raw = _header_value(parts, header, "ratio", "value", "log2", "log2ratio")
        zscore_raw = _header_value(parts, header, "zscore", "z_score", "z")
    else:
        if len(parts) < 4:
            return None
        chrom, start_raw, end_raw = parts[:3]
        record_id = parts[3] if track_type == "coverage" and len(parts) > 4 else None
        value_raw = parts[4] if track_type == "coverage" and len(parts) > 4 else parts[3]
        zscore_raw = (
            parts[5]
            if track_type == "coverage" and len(parts) > 5
            else (parts[4] if len(parts) > 4 else None)
        )

    start = _coerce_int(start_raw)
    end = _coerce_int(end_raw)
    value = _coerce_finite_float(value_raw)
    if chrom is None or start is None or end is None or value is None:
        return None

    zscore = _coerce_finite_float(zscore_raw)
    metadata: dict[str, Any] = {
        "source": "wisecondorx",
        "filename": path.name,
        "line_no": line_no,
    }
    if zscore is not None:
        metadata["zscore"] = zscore

    return {
        "sample_id": sample_context.sample_uuid,
        "family_id": sample_context.family_uuid,
        "assembly_id": sample_context.assembly_id or "",
        "assembly_name": sample_context.assembly_name or "",
        "track_type": track_type,
        "source": "wisecondorx",
        "chr": normalize_chromosome(str(chrom)),
        "start": start,
        "end": end,
        "record_id": record_id or f"{chrom}:{start}-{end}",
        "value": value,
        "origin": None,
        "metadata_json": json.dumps(metadata),
    }


async def _import_wisecondorx_track(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    path: Path,
    track_type: str,
    progress: Callable[[dict[str, int]], Awaitable[None]] | None = None,
) -> dict[str, int]:
    if not sample_context.assembly_name:
        raise RuntimeError("Cannot import WisecondorX interval tracks without an assembly name")
    await _delete_sample_interval_source(
        session,
        sample_context=sample_context,
        track_type=track_type,
        source="wisecondorx",
        filename=path.name,
    )

    processed = 0
    inserted = 0
    skipped = 0
    last_reported = 0
    batch: list[dict[str, Any]] = []
    header: dict[str, int] | None = None
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split("\t") if "\t" in stripped else stripped.split()
            lowered = [part.strip().lower() for part in parts]
            if header is None and {"chr", "start", "end"}.issubset(set(lowered)):
                header = _header_map(parts)
                continue
            processed += 1
            row = _parse_wisecondorx_interval_row(
                parts,
                header=header,
                sample_context=sample_context,
                track_type=track_type,
                path=path,
                line_no=line_no,
            )
            if row is None:
                skipped += 1
                continue
            batch.append(row)
            if len(batch) >= 5000:
                await _insert_interval_track_rows(session, batch)
                inserted += len(batch)
                batch = []
                if progress is not None and processed - last_reported >= 50000:
                    last_reported = processed
                    await progress(
                        {
                            "processed": processed,
                            "inserted": inserted,
                            "skipped": skipped,
                        }
                    )
    if batch:
        await _insert_interval_track_rows(session, batch)
        inserted += len(batch)
    await upsert_interval_track_source(
        session,
        sample_context=sample_context,
        track_type=track_type,
        source="wisecondorx",
        filename=path.name,
        row_count=inserted,
        metadata={
            "source": "wisecondorx",
            "filename": path.name,
            "uploaded_from": "family_package",
        },
    )
    await session.commit()
    result = {
        "processed": processed,
        "inserted": inserted,
        "skipped": skipped,
    }
    if progress is not None:
        await progress(result)
    return result


async def _import_copy_number_track(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    path: Path,
    track_type: str,
    source: str,
    progress: Callable[[dict[str, int]], Awaitable[None]] | None = None,
) -> dict[str, int]:
    if not sample_context.assembly_name:
        raise RuntimeError("Cannot import copy-number interval tracks without an assembly name")
    await _delete_sample_interval_source(
        session,
        sample_context=sample_context,
        track_type=track_type,
        source=source,
        filename=path.name,
    )

    processed = 0
    inserted = 0
    skipped = 0
    last_reported = 0
    batch: list[dict[str, Any]] = []
    header: dict[str, int] | None = None
    async with _open_package_text(path) as handle:
        for line_no, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = _split_delimited_line(stripped)
            if header is None and _looks_like_interval_header(parts):
                header = _normalized_header_map(parts)
                continue
            processed += 1
            row = _parse_copy_number_interval_row(
                parts,
                header=header,
                sample_context=sample_context,
                track_type=track_type,
                source=source,
                path=path,
                line_no=line_no,
            )
            if row is None:
                skipped += 1
                continue
            batch.append(row)
            if len(batch) >= 5000:
                await _insert_interval_track_rows(session, batch)
                inserted += len(batch)
                batch = []
                if progress is not None and processed - last_reported >= 50000:
                    last_reported = processed
                    await progress(
                        {
                            "processed": processed,
                            "inserted": inserted,
                            "skipped": skipped,
                        }
                    )
    if batch:
        await _insert_interval_track_rows(session, batch)
        inserted += len(batch)
    await upsert_interval_track_source(
        session,
        sample_context=sample_context,
        track_type=track_type,
        source=source,
        filename=path.name,
        row_count=inserted,
        metadata={
            "source": source,
            "filename": path.name,
            "uploaded_from": "family_package",
        },
    )
    await session.commit()
    result = {
        "processed": processed,
        "inserted": inserted,
        "skipped": skipped,
    }
    if progress is not None:
        await progress(result)
    return result


_APCAD_VALUE_KEYS = (
    "APCAD",
    "AP",
    "BAF",
    "AF",
    "AB",
    "VAF",
    "RATIO",
    "VALUE",
)
_APCAD_ORIGIN_KEYS = (
    "ORIGIN",
    "PO",
    "POO",
    "PARENT",
    "PARENT_ORIGIN",
    "PARENTAL_ORIGIN",
    "PARENT_OF_ORIGIN",
    "TRANSMITTED_FROM",
)


def _first_mapping_value(mapping: dict[str, str], keys: tuple[str, ...]) -> str | None:
    normalized_keys = {_normalize_header_key(key): value for key, value in mapping.items()}
    for key in keys:
        value = normalized_keys.get(_normalize_header_key(key))
        if not _missing_scalar(value):
            return value
    return None


def _first_finite_from_list(value: str | None) -> float | None:
    if value is None:
        return None
    for item in str(value).replace("|", ",").split(","):
        parsed = _coerce_finite_float(item)
        if parsed is not None:
            return parsed
    return None


def _normalize_origin(value: str | None) -> str:
    if value is None:
        return "und"
    token = str(value).split(",", 1)[0].strip().lower()
    if token in {"paternal", "pat", "father", "dad", "p", "fa"}:
        return "paternal"
    if token in {"maternal", "mat", "mother", "mom", "m", "mo"}:
        return "maternal"
    return "und"


def _apcad_value(
    info: dict[str, str],
    fmt_vals: dict[str, str],
    *,
    allow_info_fallback: bool = True,
) -> float | None:
    value = _first_finite_from_list(_first_mapping_value(fmt_vals, _APCAD_VALUE_KEYS))
    if value is not None:
        return value
    ad_raw = _first_mapping_value(fmt_vals, ("AD",))
    if ad_raw is not None:
        depths = [_coerce_int(item) for item in ad_raw.split(",")]
        depths = [depth for depth in depths if depth is not None]
        if len(depths) >= 2:
            total = sum(depths)
            return depths[1] / total if total > 0 else None
    if not allow_info_fallback:
        return None
    return _first_finite_from_list(_first_mapping_value(info, _APCAD_VALUE_KEYS))


def _apcad_origin(info: dict[str, str], fmt_vals: dict[str, str]) -> str:
    return _normalize_origin(
        _first_mapping_value(fmt_vals, _APCAD_ORIGIN_KEYS)
        or _first_mapping_value(info, _APCAD_ORIGIN_KEYS)
    )


def _gt_has_alt_allele(fmt_vals: dict[str, str], allele: str = "1") -> bool:
    gt = _first_mapping_value(fmt_vals, ("GT",))
    if gt in (None, "", "."):
        return False
    return allele in {token for token in re.split(r"[\/|]", gt) if token not in {"", "."}}


def _infer_apcad_origin_from_parent_genotypes(
    *,
    father_fmt: dict[str, str] | None,
    mother_fmt: dict[str, str] | None,
) -> str:
    paternal = _gt_has_alt_allele(father_fmt or {})
    maternal = _gt_has_alt_allele(mother_fmt or {})
    if paternal and not maternal:
        return "paternal"
    if maternal and not paternal:
        return "maternal"
    return "und"


def _apcad_metadata(
    *,
    source: str,
    path: Path,
    line_no: int,
    extra: dict[str, Any] | None = None,
) -> str:
    return json.dumps(
        _jsonb_safe(
            {
                "source": source,
                "filename": path.name,
                "line_no": line_no,
                "uploaded_from": "family_package",
                **(extra or {}),
            }
        )
    )


def _apcad_row(
    *,
    sample_context: SampleMetadataContext,
    path: Path,
    line_no: int,
    chrom: str,
    start: int,
    end: int,
    record_id: str | None,
    value: float,
    origin: str,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "sample_id": sample_context.sample_uuid,
        "family_id": sample_context.family_uuid,
        "assembly_id": sample_context.assembly_id or "",
        "assembly_name": sample_context.assembly_name or "",
        "track_type": "apcad",
        "source": "apcad",
        "chr": normalize_chromosome(chrom),
        "start": start,
        "end": end,
        "record_id": record_id,
        "value": value,
        "origin": origin,
        "metadata_json": _apcad_metadata(
            source="apcad",
            path=path,
            line_no=line_no,
            extra=extra_metadata,
        ),
    }


def _parse_apcad_interval_row(
    parts: list[str],
    *,
    header: dict[str, int] | None,
    sample_context: SampleMetadataContext,
    path: Path,
    line_no: int,
) -> dict[str, Any] | None:
    if header is not None:
        chrom = _normalized_header_value(parts, header, "chr", "chrom", "chromosome")
        start_raw = _normalized_header_value(parts, header, "start", "window_start")
        end_raw = _normalized_header_value(parts, header, "end", "stop", "window_end")
        pos_raw = _normalized_header_value(parts, header, "pos", "position")
        record_id = _normalized_header_value(parts, header, "id", "record_id", "name")
        value_raw = _normalized_header_value(parts, header, *_APCAD_VALUE_KEYS)
        origin_raw = _normalized_header_value(parts, header, *_APCAD_ORIGIN_KEYS)
        ref = _normalized_header_value(parts, header, "ref")
        alt = _normalized_header_value(parts, header, "alt")
    else:
        if len(parts) >= 7 and _coerce_int(parts[1]) is not None and _coerce_int(parts[2]) is None:
            chrom = parts[0]
            pos_raw = parts[1]
            start_raw = None
            end_raw = None
            ref = parts[2]
            alt = parts[3]
            record_id = parts[4]
            origin_raw = parts[5]
            value_raw = parts[6]
        elif len(parts) >= 6:
            chrom, start_raw, end_raw, record_id, value_raw, origin_raw = parts[:6]
            pos_raw = None
            ref = None
            alt = None
        else:
            return None
    if chrom is None:
        return None
    pos = _coerce_int(pos_raw)
    start = _coerce_int(start_raw)
    end = _coerce_int(end_raw)
    if pos is not None and (start is None or end is None):
        start = max(0, pos - 1)
        end = pos
    value = _coerce_finite_float(value_raw)
    if start is None or end is None or value is None:
        return None
    return _apcad_row(
        sample_context=sample_context,
        path=path,
        line_no=line_no,
        chrom=chrom,
        start=start,
        end=end,
        record_id=None if record_id in (None, "", ".") else str(record_id),
        value=value,
        origin=_normalize_origin(origin_raw),
        extra_metadata={"ref": ref, "alt": alt},
    )


async def _import_apcad_interval_file(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    path: Path,
) -> dict[str, int]:
    if not sample_context.assembly_name:
        raise RuntimeError("Cannot import APCAD interval tracks without an assembly name")
    processed = 0
    inserted = 0
    skipped = 0
    batch: list[dict[str, Any]] = []
    header: dict[str, int] | None = None
    async with _open_package_text(path) as handle:
        for line_no, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = _split_delimited_line(stripped)
            if header is None and _looks_like_interval_header(parts):
                header = _normalized_header_map(parts)
                continue
            processed += 1
            row = _parse_apcad_interval_row(
                parts,
                header=header,
                sample_context=sample_context,
                path=path,
                line_no=line_no,
            )
            if row is None:
                skipped += 1
                continue
            batch.append(row)
            if len(batch) >= 5000:
                await _insert_interval_track_rows(session, batch)
                inserted += len(batch)
                batch = []
    if batch:
        await _insert_interval_track_rows(session, batch)
        inserted += len(batch)
    return {"processed": processed, "inserted": inserted, "skipped": skipped}


async def _import_apcad_vcf_file(
    session: AsyncSession,
    *,
    path: Path,
    sample_contexts: dict[str, SampleMetadataContext],
    ped: ParsedPed | None = None,
    selected_sample_id: str | None = None,
    selected_vcf_sample: str | None = None,
) -> dict[str, Any]:
    sample_names: list[str] = []
    sample_index_by_name: dict[str, int] = {}
    sample_results: dict[str, dict[str, int]] = {}
    batches: dict[str, list[dict[str, Any]]] = {}
    parent_ids_by_sample = (
        {
            member.iid: {
                "father": None if member.pid in {"", "0"} else member.pid,
                "mother": None if member.mid in {"", "0"} else member.mid,
            }
            for member in ped.members
        }
        if ped is not None
        else {}
    )

    async def flush_sample(sample_id: str) -> None:
        batch = batches.get(sample_id) or []
        if not batch:
            return
        await _insert_interval_track_rows(session, batch)
        sample_results.setdefault(sample_id, {"processed": 0, "inserted": 0, "skipped": 0})
        sample_results[sample_id]["inserted"] += len(batch)
        batches[sample_id] = []

    async with _open_package_text(path) as handle:
        for line_no, line in enumerate(handle, start=1):
            if line.startswith("#CHROM"):
                header = line.strip().split("\t")
                sample_names = header[9:]
                sample_index_by_name = {name: index for index, name in enumerate(sample_names)}
                continue
            if not line or line.startswith("#"):
                continue
            fields = line.rstrip("\n\r").split("\t")
            if len(fields) < 8:
                continue
            chrom, pos_raw, record_id, ref, alt, qual, filt, info_raw = fields[:8]
            pos = _coerce_int(pos_raw)
            if pos is None:
                continue
            info = _parse_vcf_info(info_raw)
            fmt_keys = fields[8].split(":") if len(fields) > 8 else []
            sample_fields = fields[9:] if len(fields) > 9 else []
            targets: list[tuple[str, SampleMetadataContext, dict[str, str]]] = []
            if selected_sample_id is not None:
                sample_context = sample_contexts.get(selected_sample_id)
                if sample_context is None:
                    continue
                vcf_sample_name = selected_vcf_sample or selected_sample_id
                sample_index = sample_index_by_name.get(vcf_sample_name)
                fmt_vals = (
                    _parse_format(":".join(fmt_keys), sample_fields[sample_index])
                    if sample_index is not None and sample_index < len(sample_fields)
                    else {}
                )
                targets.append((selected_sample_id, sample_context, fmt_vals))
            elif sample_names:
                for sample_name, sample_field in zip(sample_names, sample_fields):
                    sample_context = sample_contexts.get(sample_name)
                    if sample_context is None:
                        continue
                    targets.append((sample_name, sample_context, _parse_format(":".join(fmt_keys), sample_field)))
            else:
                for sample_id, sample_context in sample_contexts.items():
                    targets.append((sample_id, sample_context, {}))

            for sample_id, sample_context, fmt_vals in targets:
                sample_results.setdefault(sample_id, {"processed": 0, "inserted": 0, "skipped": 0})
                sample_results[sample_id]["processed"] += 1
                value = _apcad_value(
                    info,
                    fmt_vals,
                    allow_info_fallback=not bool(sample_names),
                )
                if value is None:
                    sample_results[sample_id]["skipped"] += 1
                    continue
                origin = _apcad_origin(info, fmt_vals)
                if origin == "und" and sample_names:
                    parent_ids = parent_ids_by_sample.get(sample_id) or {}
                    father_fmt: dict[str, str] | None = None
                    mother_fmt: dict[str, str] | None = None
                    father_id = parent_ids.get("father")
                    if father_id:
                        father_index = sample_index_by_name.get(father_id)
                        if father_index is not None and father_index < len(sample_fields):
                            father_fmt = _parse_format(":".join(fmt_keys), sample_fields[father_index])
                    mother_id = parent_ids.get("mother")
                    if mother_id:
                        mother_index = sample_index_by_name.get(mother_id)
                        if mother_index is not None and mother_index < len(sample_fields):
                            mother_fmt = _parse_format(":".join(fmt_keys), sample_fields[mother_index])
                    origin = _infer_apcad_origin_from_parent_genotypes(
                        father_fmt=father_fmt,
                        mother_fmt=mother_fmt,
                    )
                row = _apcad_row(
                    sample_context=sample_context,
                    path=path,
                    line_no=line_no,
                    chrom=chrom,
                    start=max(0, pos - 1),
                    end=pos,
                    record_id=None if record_id in {"", "."} else record_id,
                    value=value,
                    origin=origin,
                    extra_metadata={
                        "ref": ref,
                        "alt": alt,
                        "qual": qual,
                        "filter": filt,
                        "vcf_sample": selected_vcf_sample or sample_id,
                    },
                )
                batches.setdefault(sample_id, []).append(row)
                if len(batches[sample_id]) >= 5000:
                    await flush_sample(sample_id)
    for sample_id in list(batches):
        await flush_sample(sample_id)
    return sample_results


async def _import_apcad_track_file(
    session: AsyncSession,
    *,
    sample_contexts: dict[str, SampleMetadataContext],
    path: Path,
    ped: ParsedPed | None = None,
    selected_sample_id: str | None = None,
    selected_vcf_sample: str | None = None,
) -> dict[str, Any]:
    target_contexts = (
        {selected_sample_id: sample_contexts[selected_sample_id]}
        if selected_sample_id is not None and selected_sample_id in sample_contexts
        else sample_contexts
    )
    for sample_context in target_contexts.values():
        await _delete_sample_interval_track(
            session,
            sample_context=sample_context,
            track_type="apcad",
        )
    if _is_vcf_file(path):
        sample_results = await _import_apcad_vcf_file(
            session,
            path=path,
            sample_contexts=sample_contexts,
            ped=ped,
            selected_sample_id=selected_sample_id,
            selected_vcf_sample=selected_vcf_sample,
        )
    else:
        sample_results = {}
        for sample_id, sample_context in target_contexts.items():
            sample_results[sample_id] = await _import_apcad_interval_file(
                session,
                sample_context=sample_context,
                path=path,
            )
    for sample_id, stats in sample_results.items():
        sample_context = sample_contexts.get(sample_id)
        if sample_context is None:
            continue
        inserted = int(stats.get("inserted", 0)) if isinstance(stats, dict) else 0
        await upsert_interval_track_source(
            session,
            sample_context=sample_context,
            track_type="apcad",
            source="apcad",
            filename=path.name,
            row_count=inserted,
            metadata={
                "source": "apcad",
                "filename": path.name,
                "uploaded_from": "family_package",
            },
        )
    await session.commit()
    return sample_results


def _pcf_value(row: dict[str, Any], *names: str) -> str | None:
    normalized = {_normalize_header_key(str(key)): value for key, value in row.items()}
    for name in names:
        value = normalized.get(_normalize_header_key(name))
        if not _missing_scalar(value):
            return str(value)
    return None


def _parse_pcf_segment_row(
    row: dict[str, Any],
    *,
    sample_context: SampleMetadataContext,
    path: Path,
    line_no: int,
    origin: str,
) -> dict[str, Any] | None:
    chrom = _pcf_value(row, "CHROM", "chr", "chromosome")
    start = _coerce_int(_pcf_value(row, "start.pos", "start", "start_pos"))
    end = _coerce_int(_pcf_value(row, "end.pos", "end", "end_pos", "stop"))
    value = _coerce_finite_float(_pcf_value(row, "mean", "value", "seg.mean", "segment_mean"))
    if chrom is None or start is None or end is None or value is None:
        return None
    if end < start:
        return None

    sample_id_from_file = _pcf_value(row, "sampleID", "sample_id", "sample")
    arm = _pcf_value(row, "arm")
    n_probes = _coerce_int(_pcf_value(row, "n.probes", "n_probes", "probes"))
    normalized_chrom = normalize_chromosome(chrom)
    record_id_parts = [sample_context.sample_id, origin, normalized_chrom]
    if arm:
        record_id_parts.append(arm)
    record_id_parts.append(f"{start}-{end}")
    metadata = {
        "source": APCAD_PCF_SOURCE,
        "filename": path.name,
        "line_no": line_no,
        "uploaded_from": "family_package",
        "sample_id": sample_id_from_file,
        "arm": arm,
        "n_probes": n_probes,
    }
    return {
        "sample_id": sample_context.sample_uuid,
        "family_id": sample_context.family_uuid,
        "assembly_id": sample_context.assembly_id or "",
        "assembly_name": sample_context.assembly_name or "",
        "track_type": APCAD_PCF_TRACK_TYPE,
        "source": APCAD_PCF_SOURCE,
        "chr": normalized_chrom,
        "start": start,
        "end": end,
        "record_id": ":".join(record_id_parts),
        "value": value,
        "origin": origin,
        "metadata_json": json.dumps(_jsonb_safe(metadata)),
    }


async def _import_pcf_segment_file(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    path: Path,
    origin: str,
) -> dict[str, int]:
    if not sample_context.assembly_name:
        raise RuntimeError("Cannot import PCF segment tracks without an assembly name")
    processed = 0
    inserted = 0
    skipped = 0
    batch: list[dict[str, Any]] = []
    async with _open_package_text(path) as handle:
        reader = csv.DictReader(handle)
        for raw_row in reader:
            processed += 1
            row = _parse_pcf_segment_row(
                raw_row,
                sample_context=sample_context,
                path=path,
                line_no=reader.line_num,
                origin=origin,
            )
            if row is None:
                skipped += 1
                continue
            batch.append(row)
            if len(batch) >= 5000:
                await _insert_interval_track_rows(session, batch)
                inserted += len(batch)
                batch = []
    if batch:
        await _insert_interval_track_rows(session, batch)
        inserted += len(batch)
    await upsert_interval_track_source(
        session,
        sample_context=sample_context,
        track_type=APCAD_PCF_TRACK_TYPE,
        source=APCAD_PCF_SOURCE,
        filename=path.name,
        row_count=inserted,
        metadata={
            "source": APCAD_PCF_SOURCE,
            "filename": path.name,
            "origin": origin,
            "uploaded_from": "family_package",
        },
    )
    await session.commit()
    return {"processed": processed, "inserted": inserted, "skipped": skipped}


def _needlr_query_sample_id(info: dict[str, str], sample_ids: set[str]) -> str | None:
    query_id = _first_info_value(info, "Query_ID", "QueryId", "Sample", "SAMPLE")
    if query_id is None:
        return None
    if query_id in sample_ids:
        return query_id
    for suffix in ("_sv", ".sv", "-sv"):
        if query_id.endswith(suffix) and query_id[: -len(suffix)] in sample_ids:
            return query_id[: -len(suffix)]
    for sample_id in sample_ids:
        if query_id.startswith(f"{sample_id}_") or query_id.startswith(f"{sample_id}."):
            return sample_id
    return None


def _needlr_call(
    sample_id: str,
    *,
    info: dict[str, str],
    gt_key: str,
    alt_reads_key: str,
    qual: float | None,
    filt: str | None,
) -> StructuralVariantCall:
    gt = _first_info_value(info, gt_key) or "./."
    read_support = _coerce_int(_first_info_value(info, alt_reads_key))
    return StructuralVariantCall(
        sample=sample_id,
        gt=gt,
        qual=qual,
        read_support=read_support,
        filter=filt,
    )


def _needlr_parent_sample_ids(ped: ParsedPed, sample_id: str) -> tuple[str | None, str | None]:
    member = next((item for item in ped.members if item.iid == sample_id), None)
    if member is None:
        return None, None
    mother = member.mid if member.mid not in {"", "0"} else None
    father = member.pid if member.pid not in {"", "0"} else None
    return mother, father


def _iter_needlr_structural_records(
    text_value: str,
    *,
    ped: ParsedPed,
    sample_contexts: dict[str, SampleMetadataContext],
) -> list[StructuralVariantRecord]:
    sample_ids = set(sample_contexts)
    merged: dict[str, StructuralVariantRecord] = {}
    for line in text_value.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 8:
            continue
        chrom, pos_raw, record_id, ref, alt, qual_raw, filt_raw, info_raw = parts[:8]
        start = _coerce_int(pos_raw)
        if start is None:
            continue
        info = _parse_vcf_info(info_raw)
        sv_type = _first_info_value(info, "SVTYPE") or alt.strip("<>") or "SV"
        sv_len = _coerce_int(_first_info_value(info, "SVLEN"))
        end = _coerce_int(_first_info_value(info, "END", "End_Pos", "END_POS", "End"))
        if end is None:
            end = start + abs(sv_len or 1)
        qual = _coerce_finite_float(qual_raw)
        filt = None if filt_raw in {"", "."} else filt_raw
        query_sample = _needlr_query_sample_id(info, sample_ids)
        calls: list[StructuralVariantCall] = []
        if query_sample is not None:
            calls.append(
                _needlr_call(
                    query_sample,
                    info=info,
                    gt_key="Genotype",
                    alt_reads_key="Alt_Reads",
                    qual=qual,
                    filt=filt,
                )
            )
            mother_id, father_id = _needlr_parent_sample_ids(ped, query_sample)
            if mother_id in sample_ids:
                calls.append(
                    _needlr_call(
                        mother_id,
                        info=info,
                        gt_key="Maternal_GT",
                        alt_reads_key="Maternal_Alt_Reads",
                        qual=qual,
                        filt=filt,
                    )
                )
            if father_id in sample_ids:
                calls.append(
                    _needlr_call(
                        father_id,
                        info=info,
                        gt_key="Paternal_GT",
                        alt_reads_key="Paternal_Alt_Reads",
                        qual=qual,
                        filt=filt,
                    )
                )
        if not calls:
            continue

        variant_id = (
            record_id
            if record_id and record_id != "."
            else build_structural_variant_id(chrom, start, end, sv_type)
        )
        annotation = {
            "source": "needlr",
            "ref": ref,
            "alt": alt,
            "info": info,
        }
        gene_symbols = _split_gene_symbols(info.get("Genes"))
        existing = merged.get(variant_id)
        if existing is None:
            merged[variant_id] = StructuralVariantRecord(
                variant_key=None,
                variant_id=variant_id,
                chr=normalize_chromosome(chrom),
                start=start,
                end=end,
                sv_type=sv_type,
                source="needlr",
                remote_chr=None,
                remote_start=None,
                remote_end=None,
                sv_len=sv_len,
                filters=[] if filt is None else [filt],
                gene_symbols=gene_symbols,
                annotations=[annotation],
                calls=sorted(calls, key=lambda call: call.sample),
            )
            continue
        call_by_sample = {call.sample: call for call in existing.calls}
        for call in calls:
            call_by_sample[call.sample] = call
        merged[variant_id] = StructuralVariantRecord(
            variant_key=existing.variant_key,
            variant_id=existing.variant_id,
            chr=existing.chr,
            start=existing.start,
            end=existing.end,
            sv_type=existing.sv_type,
            source=existing.source,
            remote_chr=existing.remote_chr,
            remote_start=existing.remote_start,
            remote_end=existing.remote_end,
            sv_len=existing.sv_len,
            filters=list(dict.fromkeys([*existing.filters, *([] if filt is None else [filt])])),
            gene_symbols=list(dict.fromkeys([*existing.gene_symbols, *gene_symbols])),
            annotations=[*existing.annotations, annotation],
            calls=sorted(call_by_sample.values(), key=lambda call: call.sample),
        )
    return list(merged.values())


async def _update_sv_file_metadata(
    session: AsyncSession,
    *,
    sample_contexts: dict[str, SampleMetadataContext],
    source: str,
    filename: str,
) -> None:
    for sample_context in sample_contexts.values():
        result = await session.execute(
            text("SELECT metadata FROM samples WHERE id = CAST(:sample_id AS uuid)"),
            {"sample_id": sample_context.sample_uuid},
        )
        metadata = _metadata_dict(result.scalar_one_or_none())
        sv_files = dict(metadata.get("sv_files") or {})
        sv_files[source] = filename
        metadata["sv_files"] = sv_files
        await session.execute(
            text(
                """
                UPDATE samples
                SET metadata = CAST(:metadata_json AS jsonb)
                WHERE id = CAST(:sample_id AS uuid)
                """
            ),
            {
                "sample_id": sample_context.sample_uuid,
                "metadata_json": json.dumps(metadata),
            },
        )
    await session.commit()


def _paraphase_rows_for_sample(
    *,
    sample_context: SampleMetadataContext,
    path: Path,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    metadata_json = json.dumps(
        {
            "source": "paraphase",
            "filename": path.name,
            "uploaded_from": "family_package",
        }
    )
    rows: list[dict[str, Any]] = []
    for gene_symbol, raw_result in sorted(payload.items()):
        if not isinstance(raw_result, dict):
            continue
        rows.append(
            {
                "sample_id": sample_context.sample_uuid,
                "family_id": sample_context.family_uuid,
                "assembly_id": sample_context.assembly_id or "",
                "gene_symbol": str(gene_symbol),
                "total_cn": _coerce_int(raw_result.get("total_cn")),
                "gene_cn": _coerce_int(raw_result.get("gene_cn")),
                "highest_total_cn": _coerce_int(raw_result.get("highest_total_cn")),
                "sample_sex": (
                    None
                    if _missing_scalar(raw_result.get("sample_sex"))
                    else str(raw_result.get("sample_sex"))
                ),
                "phase_region": (
                    None
                    if _missing_scalar(raw_result.get("phase_region"))
                    else str(raw_result.get("phase_region"))
                ),
                "region_depth_json": json.dumps(_jsonb_safe(raw_result.get("region_depth") or {})),
                "genome_depth": _coerce_finite_float(raw_result.get("genome_depth")),
                "payload_json": json.dumps(_jsonb_safe(raw_result)),
                "metadata_json": metadata_json,
            }
        )
    return rows


async def _replace_sample_paraphase_rows(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    rows: list[dict[str, Any]],
) -> None:
    await session.execute(
        text(
            """
            DELETE FROM sample_paraphase_results
            WHERE sample_id = CAST(:sample_id AS uuid)
            """
        ),
        {"sample_id": sample_context.sample_uuid},
    )
    for index in range(0, len(rows), 1000):
        await session.execute(
            text(
                """
                INSERT INTO sample_paraphase_results (
                    sample_id,
                    family_id,
                    assembly_id,
                    gene_symbol,
                    total_cn,
                    gene_cn,
                    highest_total_cn,
                    sample_sex,
                    phase_region,
                    region_depth,
                    genome_depth,
                    payload,
                    metadata,
                    uploaded_at
                )
                VALUES (
                    CAST(:sample_id AS uuid),
                    CAST(:family_id AS uuid),
                    CAST(NULLIF(:assembly_id, '') AS uuid),
                    :gene_symbol,
                    :total_cn,
                    :gene_cn,
                    :highest_total_cn,
                    :sample_sex,
                    :phase_region,
                    CAST(:region_depth_json AS jsonb),
                    :genome_depth,
                    CAST(:payload_json AS jsonb),
                    CAST(:metadata_json AS jsonb),
                    timezone('utc', now())
                )
                """
            ),
            rows[index : index + 1000],
        )
    await session.commit()


async def _import_snv_dataset(
    session: AsyncSession,
    *,
    bundle: FamilyPackageBundle,
    dataset: ManifestDataset,
    summary: FamilyImportDatasetSummary,
    family_context: FamilyMetadataContext,
    sample_contexts: dict[str, SampleMetadataContext],
    conflict_mode: str = "overwrite",
    progress: DatasetProgressCallback | None = None,
) -> FamilyImportDatasetSummary:
    if not family_context.assembly_name:
        return await _register_only(summary, "Registered only; family is not linked to a single assembly")
    vcf_path = _resolve_package_path(bundle.root, dataset.family_vcf)
    if vcf_path is None:
        return await _register_only(summary, "Registered only; family_vcf path is unavailable")
    if conflict_mode == "update":
        existing_count = await count_family_small_variants(
            family_context.assembly_name,
            family_context.family_uuid,
            project_ids=family_context.project_ids,
        )
        if existing_count:
            return summary.model_copy(
                update={
                    "status": "skipped",
                    "message": "Skipped SNV import in update mode because small variants already exist for this family",
                    "summary": {"existing": existing_count},
                }
            )
    source_format = str((dataset.model_extra or {}).get("source_format") or "auto")
    annotation_path = _resolve_package_path(bundle.root, dataset.annotation_tsv)
    progress_lock = asyncio.Lock()

    async def report_snv_progress(stats: dict[str, Any]) -> None:
        if progress is None:
            return
        async with progress_lock:
            await progress(
                summary.model_copy(
                    update={
                        "status": "running",
                        "message": "Importing SNV VCF and VEP annotations",
                        "summary": stats,
                    }
                )
            )

    if progress is not None:
        await report_snv_progress(
            {
                "stage": "starting",
                "family_vcf": _display_path(bundle.root, vcf_path),
                "annotation_tsv": _display_path(bundle.root, annotation_path) if annotation_path else None,
            }
        )

    async def run_upload() -> dict[str, Any]:
        if annotation_path is not None:
            async with _local_upload(vcf_path) as upload:
                async with _local_upload(annotation_path) as annotation_upload:
                    return await upload_family_small_variant_file(
                        session,
                        context=family_context,
                        sample_contexts=sample_contexts,
                        file=upload,
                        annotation_file=annotation_upload,
                        overwrite=True,
                        format_hint=source_format,  # type: ignore[arg-type]
                        progress=report_snv_progress,
                    )
        async with _local_upload(vcf_path) as upload:
            return await upload_family_small_variant_file(
                session,
                context=family_context,
                sample_contexts=sample_contexts,
                file=upload,
                overwrite=True,
                format_hint=source_format,  # type: ignore[arg-type]
                progress=report_snv_progress,
            )

    try:
        result = await _run_with_periodic_progress(
            run_upload(),
            report=report_snv_progress if progress is not None else None,
            stats={
                "family_vcf": _display_path(bundle.root, vcf_path),
                "annotation_tsv": _display_path(bundle.root, annotation_path) if annotation_path else None,
            },
        )
    except Exception:
        with suppress(Exception):
            await delete_family_small_variants(
                family_context.assembly_name,
                family_context.family_uuid,
            )
        with suppress(Exception):
            await delete_interval_tracks(
                family_context.assembly_name,
                family_uuid=family_context.family_uuid,
                track_type="haplotype",
            )
        with suppress(Exception):
            await delete_interval_track_sources(
                session,
                family_uuid=family_context.family_uuid,
                track_type="haplotype",
            )
        raise
    return summary.model_copy(
        update={
            "status": "imported",
            "message": "Imported through existing family small-variant loader",
            "summary": result,
        }
    )


async def _import_haplotypes_dataset(
    session: AsyncSession,
    *,
    bundle: FamilyPackageBundle,
    dataset: ManifestDataset,
    summary: FamilyImportDatasetSummary,
    family_context: FamilyMetadataContext,
    sample_contexts: dict[str, SampleMetadataContext],
    conflict_mode: str = "overwrite",
    progress: DatasetProgressCallback | None = None,
) -> FamilyImportDatasetSummary:
    if not dataset.family_vcf:
        return await _register_only(
            summary,
            "Registered only; direct per-sample GLIMPSE2 BCF haplotype import is not implemented yet",
        )
    if not family_context.assembly_name:
        return await _register_only(summary, "Registered only; family is not linked to a single assembly")
    vcf_path = _resolve_package_path(bundle.root, dataset.family_vcf)
    if vcf_path is None:
        return await _register_only(summary, "Registered only; family_vcf path is unavailable")
    if conflict_mode == "update":
        existing_count = await count_family_small_variants(
            family_context.assembly_name,
            family_context.family_uuid,
            project_ids=family_context.project_ids,
        )
        existing_haplotype_count = await count_interval_track_source_rows(
            session,
            family_uuid=family_context.family_uuid,
            track_type="haplotype",
            source="glimpse2",
        )
        if existing_count or existing_haplotype_count:
            return summary.model_copy(
                update={
                    "status": "skipped",
                    "message": "Skipped GLIMPSE2 import in update mode because small variants or haplotypes already exist",
                    "summary": {
                        "existing_small_variants": existing_count,
                        "existing_haplotypes": existing_haplotype_count,
                    },
                }
            )

    progress_lock = asyncio.Lock()

    async def report_haplotype_progress(stats: dict[str, Any]) -> None:
        if progress is None:
            return
        async with progress_lock:
            await progress(
                summary.model_copy(
                    update={
                        "status": "running",
                        "message": "Importing GLIMPSE2 VCF and haplotype blocks",
                        "summary": stats,
                    }
                )
            )

    async def run_upload() -> dict[str, Any]:
        async with _local_upload(vcf_path) as upload:
            return await upload_family_small_variant_file(
                session,
                context=family_context,
                sample_contexts=sample_contexts,
                file=upload,
                annotation_file=None,
                overwrite=True,
                format_hint="glimpse2",
                progress=report_haplotype_progress,
            )

    try:
        result = await _run_with_periodic_progress(
            run_upload(),
            report=report_haplotype_progress if progress is not None else None,
            stats={"family_vcf": _display_path(bundle.root, vcf_path)},
        )
    except Exception:
        with suppress(Exception):
            await delete_family_small_variants(
                family_context.assembly_name,
                family_context.family_uuid,
            )
        with suppress(Exception):
            await delete_interval_tracks(
                family_context.assembly_name,
                family_uuid=family_context.family_uuid,
                track_type="haplotype",
            )
        with suppress(Exception):
            await delete_interval_track_sources(
                session,
                family_uuid=family_context.family_uuid,
                track_type="haplotype",
            )
        raise
    return summary.model_copy(
        update={
            "status": "imported",
            "message": "Imported GLIMPSE2 family VCF as small variants and haplotype blocks",
            "summary": result,
        }
    )


async def _import_wisecondorx_dataset(
    session: AsyncSession,
    *,
    bundle: FamilyPackageBundle,
    dataset: ManifestDataset,
    summary: FamilyImportDatasetSummary,
    sample_contexts: dict[str, SampleMetadataContext],
    conflict_mode: str = "overwrite",
    progress: DatasetProgressCallback | None = None,
) -> FamilyImportDatasetSummary:
    sample_results: dict[str, Any] = {}
    for sample_id, raw_entry in dataset.per_sample.items():
        sample_context = sample_contexts.get(sample_id)
        if sample_context is None or not isinstance(raw_entry, dict):
            continue
        sample_results[sample_id] = {}

        async def report_track(role: str, stats: dict[str, int]) -> None:
            sample_results.setdefault(sample_id, {})[role] = stats
            if progress is not None:
                await progress(
                    summary.model_copy(
                        update={
                            "status": "running",
                            "message": f"Importing WisecondorX {role} for {sample_id}",
                            "summary": sample_results,
                        }
                    )
                )

        bins_path = _resolve_package_path(bundle.root, raw_entry.get("bins"))
        segments_path = _resolve_package_path(bundle.root, raw_entry.get("segments"))
        if bins_path is not None:
            existing_bins = await _interval_track_count(
                session,
                sample_context=sample_context,
                track_type="coverage",
                source="wisecondorx",
            )
            if conflict_mode == "update" and existing_bins:
                sample_results[sample_id]["bins"] = {"skipped": True, "existing": existing_bins}
            else:
                sample_results[sample_id]["bins"] = await _import_wisecondorx_track(
                    session,
                    sample_context=sample_context,
                    path=bins_path,
                    track_type="coverage",
                    progress=lambda stats, role="bins": report_track(role, stats),
                )
        if segments_path is not None:
            existing_segments = await _interval_track_count(
                session,
                sample_context=sample_context,
                track_type="segments",
                source="wisecondorx",
            )
            if conflict_mode == "update" and existing_segments:
                sample_results[sample_id]["segments"] = {"skipped": True, "existing": existing_segments}
            else:
                sample_results[sample_id]["segments"] = await _import_wisecondorx_track(
                    session,
                    sample_context=sample_context,
                    path=segments_path,
                    track_type="segments",
                    progress=lambda stats, role="segments": report_track(role, stats),
                )
    skipped = [
        f"{sample_id}:{role}"
        for sample_id, roles in sample_results.items()
        for role, stats in roles.items()
        if isinstance(stats, dict) and stats.get("existing") is not None
    ]
    return summary.model_copy(
        update={
            "status": "imported",
            "message": (
                "Imported WisecondorX bins as coverage and segments as segment interval tracks"
                if not skipped
                else f"Imported WisecondorX data; skipped existing tracks in update mode: {', '.join(skipped)}"
            ),
            "summary": sample_results,
        }
    )


async def _import_qdnaseq_dataset(
    session: AsyncSession,
    *,
    bundle: FamilyPackageBundle,
    dataset: ManifestDataset,
    summary: FamilyImportDatasetSummary,
    sample_contexts: dict[str, SampleMetadataContext],
    conflict_mode: str = "overwrite",
    progress: DatasetProgressCallback | None = None,
) -> FamilyImportDatasetSummary:
    sample_results: dict[str, Any] = {}
    for sample_id, raw_entry in dataset.per_sample.items():
        sample_context = sample_contexts.get(sample_id)
        if sample_context is None or not isinstance(raw_entry, dict):
            continue
        sample_results[sample_id] = {}

        async def report_track(role: str, stats: dict[str, int]) -> None:
            sample_results.setdefault(sample_id, {})[role] = stats
            if progress is not None:
                await progress(
                    summary.model_copy(
                        update={
                            "status": "running",
                            "message": f"Importing QDNAseq {role} for {sample_id}",
                            "summary": sample_results,
                        }
                    )
                )

        bins_path = _resolve_package_path(bundle.root, raw_entry.get("bins") or raw_entry.get("file"))
        segments_path = _resolve_package_path(bundle.root, raw_entry.get("segments"))
        if bins_path is not None:
            existing_bins = await _interval_track_count(
                session,
                sample_context=sample_context,
                track_type="coverage",
                source="qdnaseq",
            )
            if conflict_mode == "update" and existing_bins:
                sample_results[sample_id]["bins"] = {"skipped": True, "existing": existing_bins}
            else:
                sample_results[sample_id]["bins"] = await _import_copy_number_track(
                    session,
                    sample_context=sample_context,
                    path=bins_path,
                    track_type="coverage",
                    source="qdnaseq",
                    progress=lambda stats, role="bins": report_track(role, stats),
                )
        if segments_path is not None:
            existing_segments = await _interval_track_count(
                session,
                sample_context=sample_context,
                track_type="segments",
                source="qdnaseq",
            )
            if conflict_mode == "update" and existing_segments:
                sample_results[sample_id]["segments"] = {"skipped": True, "existing": existing_segments}
            else:
                sample_results[sample_id]["segments"] = await _import_copy_number_track(
                    session,
                    sample_context=sample_context,
                    path=segments_path,
                    track_type="segments",
                    source="qdnaseq",
                    progress=lambda stats, role="segments": report_track(role, stats),
                )
    skipped = [
        f"{sample_id}:{role}"
        for sample_id, roles in sample_results.items()
        for role, stats in roles.items()
        if isinstance(stats, dict) and stats.get("existing") is not None
    ]
    return summary.model_copy(
        update={
            "status": "imported",
            "message": (
                "Imported QDNAseq bins as coverage and segments as segment interval tracks"
                if not skipped
                else f"Imported QDNAseq data; skipped existing tracks in update mode: {', '.join(skipped)}"
            ),
            "summary": sample_results,
        }
    )


async def _import_sv_needlr_dataset(
    session: AsyncSession,
    *,
    bundle: FamilyPackageBundle,
    dataset: ManifestDataset,
    summary: FamilyImportDatasetSummary,
    family_context: FamilyMetadataContext,
    sample_contexts: dict[str, SampleMetadataContext],
    conflict_mode: str = "overwrite",
) -> FamilyImportDatasetSummary:
    if not family_context.assembly_name:
        return await _register_only(summary, "Registered only; family is not linked to a single assembly")
    vcf_path = _resolve_package_path(bundle.root, dataset.family_vcf)
    if vcf_path is None:
        return await _register_only(summary, "Registered only; family_vcf path is unavailable")
    if conflict_mode == "update":
        existing_count = await count_family_structural_variants(
            family_context.assembly_name,
            family_context.family_uuid,
            project_ids=family_context.project_ids,
            source="needlr",
        )
        if existing_count:
            return summary.model_copy(
                update={
                    "status": "skipped",
                    "message": "Skipped Needlr SV import in update mode because Needlr SVs already exist for this family",
                    "summary": {"existing": existing_count},
                }
            )
    text_value = _read_package_text(vcf_path)
    records = _iter_needlr_structural_records(
        text_value,
        ped=bundle.ped,
        sample_contexts=sample_contexts,
    )
    if not records:
        raise RuntimeError("No Needlr structural variants with PED sample calls were found")
    await replace_family_structural_variants(
        family_context.assembly_name,
        family_context.family_uuid,
        family_context.project_ids,
        records,
        source="needlr",
    )
    await _update_sv_file_metadata(
        session,
        sample_contexts=sample_contexts,
        source="needlr",
        filename=vcf_path.name,
    )
    return summary.model_copy(
        update={
            "status": "imported",
            "message": "Imported Needlr family SV VCF into structural variant storage",
            "summary": {
                "processed": len(records),
                "source": "needlr",
            },
        }
    )


async def _import_apcad_dataset(
    session: AsyncSession,
    *,
    bundle: FamilyPackageBundle,
    dataset: ManifestDataset,
    summary: FamilyImportDatasetSummary,
    sample_contexts: dict[str, SampleMetadataContext],
    conflict_mode: str = "overwrite",
) -> FamilyImportDatasetSummary:
    if dataset.family_vcf:
        vcf_path = _resolve_package_path(bundle.root, dataset.family_vcf)
        if vcf_path is None:
            return await _register_only(summary, "Registered only; family_vcf path is unavailable")
        embryo_sample_ids = _ped_embryo_sample_ids(bundle.ped)
        target_sample_contexts = (
            {
                sample_id: sample_context
                for sample_id, sample_context in sample_contexts.items()
                if sample_id in embryo_sample_ids
            }
            or sample_contexts
        )
        existing_by_sample = {
            sample_id: await _interval_track_count(
                session,
                sample_context=sample_context,
                track_type="apcad",
            )
            for sample_id, sample_context in target_sample_contexts.items()
        }
        if conflict_mode == "update" and any(existing_by_sample.values()):
            return summary.model_copy(
                update={
                    "status": "skipped",
                    "message": "Skipped APCAD import in update mode because APCAD tracks already exist",
                    "summary": {"existing": existing_by_sample},
                }
            )
        sample_results = await _import_apcad_track_file(
            session,
            sample_contexts=target_sample_contexts,
            path=vcf_path,
            ped=bundle.ped,
        )
        return summary.model_copy(
            update={
                "status": "imported",
                "message": "Imported APCAD VCF into embryo APCAD interval tracks",
                "summary": sample_results,
            }
        )
    if not dataset.per_sample:
        return await _register_only(
            summary,
            "Registered only; this manifest uses a family-level APCAD BED and existing loaders are sample-scoped",
        )
    sample_results: dict[str, Any] = {}
    for sample_id, raw_entry in dataset.per_sample.items():
        sample_context = sample_contexts.get(sample_id)
        if sample_context is None or not isinstance(raw_entry, dict):
            continue
        bed_path = _resolve_package_path(
            bundle.root,
            raw_entry.get("bed") or raw_entry.get("file") or raw_entry.get("vcf"),
        )
        if bed_path is None:
            continue
        existing_count = await _interval_track_count(
            session,
            sample_context=sample_context,
            track_type="apcad",
        )
        if conflict_mode == "update" and existing_count:
            sample_results[sample_id] = {"skipped": True, "existing": existing_count}
            continue
        import_result = await _import_apcad_track_file(
            session,
            sample_contexts=sample_contexts,
            path=bed_path,
            ped=bundle.ped,
            selected_sample_id=sample_id,
            selected_vcf_sample=raw_entry.get("sample_name") or raw_entry.get("vcf_sample"),
        )
        sample_results[sample_id] = (
            import_result.get(sample_id, import_result)
            if isinstance(import_result, dict)
            else import_result
        )
        if not import_result:
            async with _local_upload(bed_path) as upload:
                sample_results[sample_id] = await upload_bed_data(
                    session,
                    sample_context=sample_context,
                    bed_type="apcad",
                    file=upload,
                    overwrite=True,
                )
    skipped = [
        sample_id
        for sample_id, stats in sample_results.items()
        if isinstance(stats, dict) and stats.get("existing") is not None
    ]
    return summary.model_copy(
        update={
            "status": "imported",
            "message": (
                "Imported APCAD data into interval tracks"
                if not skipped
                else f"Imported APCAD data; skipped existing samples in update mode: {', '.join(skipped)}"
            ),
            "summary": sample_results,
        }
    )


async def _import_coverage_dataset(
    session: AsyncSession,
    *,
    bundle: FamilyPackageBundle,
    dataset: ManifestDataset,
    summary: FamilyImportDatasetSummary,
    sample_contexts: dict[str, SampleMetadataContext],
    conflict_mode: str = "overwrite",
) -> FamilyImportDatasetSummary:
    if not dataset.per_sample:
        return await _register_only(
            summary, "Registered only; coverage dataset has no per_sample entries"
        )
    sample_results: dict[str, Any] = {}
    for sample_id, raw_entry in dataset.per_sample.items():
        sample_context = sample_contexts.get(sample_id)
        if sample_context is None or not isinstance(raw_entry, dict):
            continue
        bed_path = _resolve_package_path(
            bundle.root, raw_entry.get("bed") or raw_entry.get("file")
        )
        if bed_path is None:
            continue
        existing_count = await _interval_track_count(
            session, sample_context=sample_context, track_type="coverage"
        )
        if conflict_mode == "update" and existing_count:
            sample_results[sample_id] = {"skipped": True, "existing": existing_count}
            continue
        async with _local_upload(bed_path) as upload:
            sample_results[sample_id] = await upload_bed_data(
                session,
                sample_context=sample_context,
                bed_type="coverage",
                file=upload,
                overwrite=True,
            )
    skipped = [
        sample_id
        for sample_id, stats in sample_results.items()
        if isinstance(stats, dict) and stats.get("existing") is not None
    ]
    return summary.model_copy(
        update={
            "status": "imported",
            "message": (
                "Imported coverage into interval tracks"
                if not skipped
                else f"Imported coverage; skipped existing samples in update mode: {', '.join(skipped)}"
            ),
            "summary": sample_results,
        }
    )


async def _import_pcf_dataset(
    session: AsyncSession,
    *,
    bundle: FamilyPackageBundle,
    dataset: ManifestDataset,
    summary: FamilyImportDatasetSummary,
    sample_contexts: dict[str, SampleMetadataContext],
    conflict_mode: str = "overwrite",
) -> FamilyImportDatasetSummary:
    if not dataset.per_sample:
        return await _register_only(summary, "No PCF segment files were provided")
    sample_results: dict[str, Any] = {}
    for sample_id, raw_entry in dataset.per_sample.items():
        sample_context = sample_contexts.get(sample_id)
        if sample_context is None or not isinstance(raw_entry, dict):
            continue
        role_paths: list[tuple[str, Path]] = []
        for role, origin in (("maternal", "maternal"), ("paternal", "paternal")):
            path = _resolve_package_path(bundle.root, _pcf_role_path(raw_entry, role))
            if path is not None:
                role_paths.append((origin, path))
        if not role_paths:
            continue

        existing_count = await count_interval_track_source_rows(
            session,
            sample_uuid=sample_context.sample_uuid,
            track_type=APCAD_PCF_TRACK_TYPE,
            source=APCAD_PCF_SOURCE,
        )
        if conflict_mode == "update" and existing_count:
            sample_results[sample_id] = {"skipped": True, "existing": existing_count}
            continue

        await _delete_sample_interval_source(
            session,
            sample_context=sample_context,
            track_type=APCAD_PCF_TRACK_TYPE,
            source=APCAD_PCF_SOURCE,
        )
        sample_results[sample_id] = {}
        for origin, path in role_paths:
            sample_results[sample_id][origin] = await _import_pcf_segment_file(
                session,
                sample_context=sample_context,
                path=path,
                origin=origin,
            )

    skipped = [
        sample_id
        for sample_id, stats in sample_results.items()
        if isinstance(stats, dict) and stats.get("existing") is not None
    ]
    return summary.model_copy(
        update={
            "status": "imported" if sample_results else "skipped",
            "message": (
                "Imported PCF APCAD segment overlays into interval tracks"
                if sample_results and not skipped
                else f"Imported PCF data; skipped existing samples in update mode: {', '.join(skipped)}"
                if skipped
                else "No PCF segment files were imported"
            ),
            "summary": sample_results,
        }
    )


async def _import_repeats_dataset(
    session: AsyncSession,
    *,
    bundle: FamilyPackageBundle,
    dataset: ManifestDataset,
    summary: FamilyImportDatasetSummary,
    sample_contexts: dict[str, SampleMetadataContext],
    conflict_mode: str = "overwrite",
) -> FamilyImportDatasetSummary:
    if conflict_mode == "update":
        existing_count = await _repeat_expansion_count(session, sample_contexts=sample_contexts)
        if existing_count:
            return summary.model_copy(
                update={
                    "status": "skipped",
                    "message": "Skipped TRGT repeat import in update mode because repeat expansions already exist for this family",
                    "summary": {"existing": existing_count},
                }
            )
    family_vcf_path = _resolve_package_path(bundle.root, dataset.family_vcf)
    if family_vcf_path is not None:
        async with _local_upload(family_vcf_path) as upload:
            text_value = await decode_repeat_upload_text(upload)
            result = await ingest_family_trgt_text(
                session,
                sample_contexts=sample_contexts,
                text_value=text_value,
                metadata={
                    "source": "trgt_family",
                    "filename": family_vcf_path.name,
                    "uploaded_from": "family_package",
                    "family_vcf": _display_path(bundle.root, family_vcf_path),
                },
            )
        return summary.model_copy(
            update={
                "status": "imported",
                "message": "Imported family TRGT VCF through existing repeat-expansion storage",
                "summary": result,
            }
        )
    if not dataset.per_sample:
        return await _register_only(
            summary,
            "Registered only; no family VCF or per-sample TRGT files were provided",
        )
    sample_results: dict[str, Any] = {}
    for sample_id, raw_entry in dataset.per_sample.items():
        sample_context = sample_contexts.get(sample_id)
        if sample_context is None or not isinstance(raw_entry, dict):
            continue
        vcf_path = _resolve_package_path(bundle.root, raw_entry.get("file") or raw_entry.get("vcf"))
        if vcf_path is None:
            continue
        await clear_sample_repeat_expansions(session, sample_uuid=sample_context.sample_uuid)
        async with _local_upload(vcf_path) as upload:
            text_value = await decode_repeat_upload_text(upload)
            sample_results[sample_id] = await ingest_trgt_text(
                session,
                sample_context=sample_context,
                text_value=text_value,
                metadata={
                    "source": "trgt",
                    "filename": vcf_path.name,
                    "uploaded_from": "family_package",
                },
            )
    return summary.model_copy(
        update={
            "status": "imported",
            "message": "Imported sample-scoped TRGT files through existing repeat-expansion loader",
            "summary": sample_results,
        }
    )


async def _import_paraphase_dataset(
    session: AsyncSession,
    *,
    bundle: FamilyPackageBundle,
    dataset: ManifestDataset,
    summary: FamilyImportDatasetSummary,
    sample_contexts: dict[str, SampleMetadataContext],
    conflict_mode: str = "overwrite",
) -> FamilyImportDatasetSummary:
    sample_results: dict[str, Any] = {}
    for sample_id, raw_entry in dataset.per_sample.items():
        sample_context = sample_contexts.get(sample_id)
        if sample_context is None or not isinstance(raw_entry, dict):
            continue
        existing_count = await _paraphase_count(session, sample_context=sample_context)
        if conflict_mode == "update" and existing_count:
            sample_results[sample_id] = {"skipped": True, "existing": existing_count}
            continue
        json_path = _resolve_package_path(bundle.root, raw_entry.get("json"))
        if json_path is None:
            continue
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError(f"Paraphase JSON for {sample_id} must contain an object")
        rows = _paraphase_rows_for_sample(
            sample_context=sample_context,
            path=json_path,
            payload=payload,
        )
        await _replace_sample_paraphase_rows(
            session,
            sample_context=sample_context,
            rows=rows,
        )
        sample_results[sample_id] = {
            "genes": len(rows),
            "filename": json_path.name,
        }
    skipped = [
        sample_id
        for sample_id, stats in sample_results.items()
        if isinstance(stats, dict) and stats.get("existing") is not None
    ]
    return summary.model_copy(
        update={
            "status": "imported",
            "message": (
                "Imported Paraphase JSON into sample paraphase result storage"
                if not skipped
                else f"Imported Paraphase JSON; skipped existing samples in update mode: {', '.join(skipped)}"
            ),
            "summary": sample_results,
        }
    )


async def _import_phenotypes_dataset(
    session: AsyncSession,
    *,
    bundle: FamilyPackageBundle,
    summary: FamilyImportDatasetSummary,
    family_context: FamilyMetadataContext,
    sample_contexts: dict[str, SampleMetadataContext],
) -> FamilyImportDatasetSummary:
    rows, issues, _files, fatal_errors = _manifest_hpo_rows(
        root=bundle.root,
        manifest=bundle.manifest,
        family_id=family_context.family_id,
    )
    if fatal_errors:
        return summary.model_copy(
            update={
                "status": "failed",
                "message": "; ".join(error.message for error in fatal_errors),
                "summary": {"errors": [error.model_dump() for error in fatal_errors]},
            }
        )
    result = await import_family_hpo_annotations(
        session,
        family_uuid=family_context.family_uuid,
        family_id=family_context.family_id,
        sample_uuids_by_id={
            sample_id: sample_context.sample_uuid
            for sample_id, sample_context in sample_contexts.items()
        },
        rows=rows,
        issues=issues,
    )
    status = "imported" if result["imported"] else "skipped"
    if result["errors"]:
        status = "warning" if result["imported"] else "skipped"
    return summary.model_copy(
        update={
            "status": status,
            "message": (
                f"Imported {result['imported']} HPO phenotype annotation row(s)"
                if result["imported"]
                else "No HPO phenotype annotation rows were imported"
            ),
            "summary": {
                **result,
                "assumption": "PED phenotype remains coarse affected/unaffected status; detailed HPO phenotypes are stored in individual_hpo.",
            },
        }
    )


async def _import_dataset(
    session: AsyncSession,
    *,
    bundle: FamilyPackageBundle,
    summary: FamilyImportDatasetSummary,
    family_context: FamilyMetadataContext,
    sample_contexts: dict[str, SampleMetadataContext],
    conflict_mode: str = "overwrite",
    progress: DatasetProgressCallback | None = None,
) -> FamilyImportDatasetSummary:
    if summary.dataset_type == "phenotypes":
        return await _import_phenotypes_dataset(
            session,
            bundle=bundle,
            summary=summary,
            family_context=family_context,
            sample_contexts=sample_contexts,
        )
    dataset = bundle.manifest.datasets.get(summary.dataset_type)
    if dataset is None or not dataset.enabled:
        return summary
    if summary.dataset_type == "snv":
        return await _import_snv_dataset(
            session,
            bundle=bundle,
            dataset=dataset,
            summary=summary,
            family_context=family_context,
            sample_contexts=sample_contexts,
            conflict_mode=conflict_mode,
            progress=progress,
        )
    if summary.dataset_type == "wisecondorx":
        return await _import_wisecondorx_dataset(
            session,
            bundle=bundle,
            dataset=dataset,
            summary=summary,
            sample_contexts=sample_contexts,
            conflict_mode=conflict_mode,
            progress=progress,
        )
    if summary.dataset_type == "qdnaseq":
        return await _import_qdnaseq_dataset(
            session,
            bundle=bundle,
            dataset=dataset,
            summary=summary,
            sample_contexts=sample_contexts,
            conflict_mode=conflict_mode,
            progress=progress,
        )
    if summary.dataset_type == "apcad":
        return await _import_apcad_dataset(
            session,
            bundle=bundle,
            dataset=dataset,
            summary=summary,
            sample_contexts=sample_contexts,
            conflict_mode=conflict_mode,
        )
    if summary.dataset_type == "coverage":
        return await _import_coverage_dataset(
            session,
            bundle=bundle,
            dataset=dataset,
            summary=summary,
            sample_contexts=sample_contexts,
            conflict_mode=conflict_mode,
        )
    if summary.dataset_type == "pcf":
        return await _import_pcf_dataset(
            session,
            bundle=bundle,
            dataset=dataset,
            summary=summary,
            sample_contexts=sample_contexts,
            conflict_mode=conflict_mode,
        )
    if summary.dataset_type == "repeats_trgt":
        return await _import_repeats_dataset(
            session,
            bundle=bundle,
            dataset=dataset,
            summary=summary,
            sample_contexts=sample_contexts,
            conflict_mode=conflict_mode,
        )
    if summary.dataset_type == "sv_needlr":
        return await _import_sv_needlr_dataset(
            session,
            bundle=bundle,
            dataset=dataset,
            summary=summary,
            family_context=family_context,
            sample_contexts=sample_contexts,
            conflict_mode=conflict_mode,
        )
    if summary.dataset_type == "haplotypes":
        return await _import_haplotypes_dataset(
            session,
            bundle=bundle,
            dataset=dataset,
            summary=summary,
            family_context=family_context,
            sample_contexts=sample_contexts,
            conflict_mode=conflict_mode,
            progress=progress,
        )
    if summary.dataset_type == "paraphase":
        return await _import_paraphase_dataset(
            session,
            bundle=bundle,
            dataset=dataset,
            summary=summary,
            sample_contexts=sample_contexts,
            conflict_mode=conflict_mode,
        )
    return summary


async def execute_family_package_import(
    session: AsyncSession | None,
    *,
    folder_path: str | Path,
    project_id: str | None,
    dry_run: bool,
    user: CurrentUser | None,
    requested_family_id: str | None = None,
    conflict_mode: str = "cancel",
    progress: ProgressCallback | None = None,
) -> PackageExecutionResult:
    """Run an import, staging the package from S3 to a temp dir first when the
    source is an s3:// URI (cleaned up afterwards)."""
    async with staged_package_source_async(folder_path) as (local_root, source_uri):
        return await _execute_family_package_import_local(
            session,
            folder_path=local_root,
            source_uri=source_uri,
            project_id=project_id,
            dry_run=dry_run,
            user=user,
            requested_family_id=requested_family_id,
            conflict_mode=conflict_mode,
            progress=progress,
        )


async def _execute_family_package_import_local(
    session: AsyncSession | None,
    *,
    folder_path: str | Path,
    source_uri: str | None,
    project_id: str | None,
    dry_run: bool,
    user: CurrentUser | None,
    requested_family_id: str | None = None,
    conflict_mode: str = "cancel",
    progress: ProgressCallback | None = None,
) -> PackageExecutionResult:
    fallback_ped_text = await db_pedigree_fallback(session, requested_family_id)
    validation, bundle = load_validated_family_package(
        folder_path,
        fallback_ped_text=fallback_ped_text,
    )
    if bundle is not None:
        bundle.source_uri = source_uri
    conflict_mode = _normalized_conflict_mode(conflict_mode)
    request_metadata = _execution_metadata(
        requested_family_id=requested_family_id,
        conflict_mode=conflict_mode,
    )
    validation = _merge_validation_metadata(validation, request_metadata)
    logs = [f"Validated package path {Path(folder_path).expanduser()}."]
    if requested_family_id and validation.family_id and requested_family_id != validation.family_id:
        validation = validation.model_copy(
            update={
                "valid": False,
                "errors": [
                    *validation.errors,
                    _issue(
                        "selected_family_mismatch",
                        f"Selected existing family '{requested_family_id}' does not match package family_id '{validation.family_id}'.",
                    ),
                ],
            }
        )
    if session is not None and hasattr(session, "execute") and bundle is not None:
        existing_warnings = await _existing_package_entity_warnings(
            session,
            family_id=validation.family_id,
            sample_ids=bundle.ped.sample_ids,
        )
        if existing_warnings:
            validation = validation.model_copy(
                update={"warnings": [*validation.warnings, *existing_warnings]}
            )
            if conflict_mode == "cancel" and not dry_run:
                validation = validation.model_copy(
                    update={
                        "valid": False,
                        "errors": [
                            *validation.errors,
                            _issue(
                                "existing_family_or_samples",
                                "Family or sample IDs already exist; choose update or overwrite to import data.",
                            ),
                        ],
                    }
                )
    datasets = [summary.model_copy() for summary in validation.datasets]
    if progress is not None:
        await progress(validation, datasets, logs, validation.family_id)

    if validation.errors:
        logs.append("Package validation failed; no data were imported.")
        return PackageExecutionResult(
            validation=validation,
            datasets=datasets,
            logs=logs,
            family_id=validation.family_id,
            completed=False,
            error="Package validation failed",
        )
    if dry_run:
        logs.append("Dry run completed successfully; no data were imported.")
        return PackageExecutionResult(
            validation=validation,
            datasets=datasets,
            logs=logs,
            family_id=validation.family_id,
            completed=True,
        )
    if session is None or user is None or bundle is None:
        raise RuntimeError("A database session and user are required for non-dry-run imports")

    logs.append("Registering family metadata and package provenance.")
    family_context = await _ensure_family_from_ped(
        session,
        bundle=bundle,
        project_id=project_id,
        user=user,
        validation=validation,
        conflict_mode=conflict_mode,
    )
    sample_contexts = _family_sample_contexts(family_context)
    logs.append(
        f"Family {family_context.family_id} is registered with {len(sample_contexts)} sample(s)."
    )
    if progress is not None:
        await progress(validation, datasets, logs, family_context.family_id)

    for summary in _enabled_dataset_summaries(validation):
        index = next(
            (idx for idx, item in enumerate(datasets) if item.dataset_type == summary.dataset_type),
            None,
        )
        if index is None:
            continue
        datasets[index] = datasets[index].model_copy(update={"status": "running"})
        if progress is not None:
            await progress(validation, datasets, logs, family_context.family_id)
        try:
            async def dataset_progress(partial_summary: FamilyImportDatasetSummary) -> None:
                datasets[index] = partial_summary
                if progress is not None:
                    await progress(validation, datasets, logs, family_context.family_id)

            datasets[index] = await _import_dataset(
                session,
                bundle=bundle,
                summary=summary,
                family_context=family_context,
                sample_contexts=sample_contexts,
                conflict_mode=conflict_mode,
                progress=dataset_progress,
            )
            logs.append(f"Dataset {summary.dataset_type}: {datasets[index].status}.")
        except Exception as exc:
            await session.rollback()
            datasets[index] = summary.model_copy(
                update={
                    "status": "failed",
                    "message": str(exc),
                }
            )
            logs.append(f"Dataset {summary.dataset_type} failed: {exc}")
            if progress is not None:
                await progress(validation, datasets, logs, family_context.family_id)
            continue
        if progress is not None:
            await progress(validation, datasets, logs, family_context.family_id)

    failed_datasets = [dataset.dataset_type for dataset in datasets if dataset.status == "failed"]
    if failed_datasets:
        logs.append(
            "Family package import completed with failed dataset(s): "
            + ", ".join(failed_datasets)
            + "."
        )
    else:
        logs.append("Family package import completed.")

    # (Re)importing variant data changes the prioritised ranking, but the cache's input
    # hash doesn't cover variant content — drop any cached ranking so it recomputes.
    if not dry_run and session is not None:
        from .variant_ranking_cache import clear_family_ranking_cache
        from .sv_gene_index_service import clear_family_sv_gene_index

        try:
            await clear_family_ranking_cache(session, family_context.family_uuid)
            # The SV→gene index (the small-variant "also hit by an SV" flag) depends on the
            # imported SVs — drop it so it rebuilds lazily after a re-import.
            await clear_family_sv_gene_index(session, family_context.family_uuid)
        except Exception:  # noqa: BLE001 - best-effort cache invalidation
            logger.warning(
                "Failed to clear caches after import for family %s",
                family_context.family_id,
                exc_info=True,
            )

    return PackageExecutionResult(
        validation=validation,
        datasets=datasets,
        logs=logs,
        family_id=family_context.family_id,
        completed=True,
    )


async def run_family_import_job(
    *,
    job_id: str,
    worker_id: str,
) -> None:
    session_factory = get_postgres_sessionmaker()
    async with session_factory() as session:
        job_result = await session.execute(
            text(
                """
                SELECT
                    id::text AS id,
                    submitted_path,
                    project_id::text AS project_id,
                    dry_run,
                    requested_by,
                    metadata
                FROM family_import_jobs
                WHERE id = CAST(:job_id AS uuid)
                  AND worker_id = :worker_id
                  AND status = 'validating'
                """
            ),
            {"job_id": job_id, "worker_id": worker_id},
        )
        job_row = job_result.mappings().first()
        if job_row is None:
            return

        async def progress(
            validation: FamilyPackageValidationOut | None,
            datasets: list[FamilyImportDatasetSummary],
            logs: list[str],
            family_id: str | None,
        ) -> None:
            next_status = (
                "running"
                if validation is not None
                and not validation.errors
                and not bool(job_row["dry_run"])
                else None
            )
            try:
                async with session_factory() as progress_session:
                    await _update_job_progress(
                        progress_session,
                        job_id=job_id,
                        worker_id=worker_id,
                        status=next_status,
                        family_id=family_id,
                        validation=validation,
                        datasets=datasets,
                        logs=logs,
                    )
            except Exception:  # pragma: no cover
                logger.exception("Family package import progress update failed")

        try:
            user = await get_current_user_by_email(session, str(job_row["requested_by"]))
            if user is None:
                raise RuntimeError("Requesting user no longer exists")
            job_metadata = _json_dict(job_row.get("metadata"))
            result = await execute_family_package_import(
                session,
                folder_path=str(job_row["submitted_path"]),
                project_id=str(job_row["project_id"]) if job_row.get("project_id") else None,
                dry_run=bool(job_row["dry_run"]),
                user=user,
                requested_family_id=job_metadata.get("requested_family_id"),
                conflict_mode=str(job_metadata.get("conflict_mode") or "cancel"),
                progress=progress,
            )
            if result.error:
                await _update_job_progress(
                    session,
                    job_id=job_id,
                    worker_id=worker_id,
                    status="failed",
                    family_id=result.family_id,
                    validation=result.validation,
                    datasets=result.datasets,
                    logs=result.logs,
                    error=result.error,
                    completed=True,
                )
                return
            # Warm the genome-overview lineage cache so the first view is instant and
            # precise (precise relative colours genome-wide). Best-effort and run
            # once per import: a failure here simply leaves the overview on its fast
            # grey-relatives fallback, so it must never fail the import.
            if result.family_id and not bool(job_row["dry_run"]):
                try:
                    lineage_context = await build_family_metadata_context(
                        session, family_identifier=str(result.family_id), user=user
                    )
                    stored_blocks = await precompute_family_haplotype_lineage(lineage_context)
                    logger.info(
                        "Precomputed genome-wide haplotype lineage for family %s: %s blocks",
                        result.family_id,
                        stored_blocks,
                    )
                except Exception:  # pragma: no cover - precompute is best-effort
                    logger.exception(
                        "Haplotype lineage precompute failed for family %s; genome "
                        "overview will use the fast grey-relatives fallback",
                        result.family_id,
                    )
            await _update_job_progress(
                session,
                job_id=job_id,
                worker_id=worker_id,
                status="completed",
                family_id=result.family_id,
                validation=result.validation,
                datasets=result.datasets,
                logs=result.logs,
                completed=True,
            )
        except Exception as exc:
            logger.exception("Family package import job failed")
            await session.rollback()
            await _update_job_progress(
                session,
                job_id=job_id,
                worker_id=worker_id,
                status="failed",
                error=str(exc),
                completed=True,
            )
            raise


async def family_package_import_worker(stop_event: asyncio.Event | None = None) -> None:
    session_factory = get_postgres_sessionmaker()
    worker_id = f"{os.getpid()}-{uuid4().hex}"
    while True:
        if stop_event is not None and stop_event.is_set():
            return
        try:
            async with session_factory() as session:
                job_row = await claim_next_family_import_job(session, worker_id=worker_id)
            if job_row is None:
                await asyncio.sleep(FAMILY_IMPORT_WORKER_POLL_SECONDS)
                continue
            await run_family_import_job(job_id=job_row["id"], worker_id=worker_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover
            logger.exception("Family package import worker encountered an unexpected error")
            await asyncio.sleep(FAMILY_IMPORT_WORKER_POLL_SECONDS)


async def stop_family_package_import_worker(
    task: asyncio.Task[Any] | None,
    stop_event: asyncio.Event | None,
) -> None:
    if stop_event is not None:
        stop_event.set()
    if task is None:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
