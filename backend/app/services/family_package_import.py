from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import gzip
import json
import logging
import math
import os
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession
import yaml

from ..core.config import settings
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
    replace_family_structural_variants,
)
from .data_scope import normalize_chromosome
from .family_metadata_context import (
    FamilyMetadataContext,
    SampleMetadataContext,
    build_family_metadata_context,
)
from .metadata_service import CurrentUser, get_current_user_by_email
from . import ped_service
from .repeat_expansion_pg import (
    clear_sample_repeat_expansions,
    decode_repeat_upload_text,
    ingest_family_trgt_text,
    ingest_trgt_text,
)
from .variant_upload_service import upload_family_small_variant_file

logger = logging.getLogger(__name__)

SUPPORTED_DATASETS = (
    "snv",
    "sv_needlr",
    "repeats_trgt",
    "wisecondorx",
    "apcad",
    "haplotypes",
    "paraphase",
)
OPTIONAL_DATASETS = set(SUPPORTED_DATASETS)
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
            "apcad": {
                "bed": [
                    "apcad/{sample_id}.apcad.bed",
                    "apcad/{sample_id}.bed",
                    "apcad/{sample_id}.apcad.tsv",
                ],
            },
            "haplotypes": {
                "file": ["haplotypes/{sample_id}.glimpse2.bcf"],
                "index": ["haplotypes/{sample_id}.glimpse2.bcf.csi"],
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
    file: str | None = None
    json_path: str | None = Field(default=None, alias="json")
    per_sample: dict[str, dict[str, Any]] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class PackageManifest(BaseModel):
    schema_version: int = 1
    family_id: str | None = None
    ped: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    samples: dict[str, Any] | list[Any] | None = None
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


def _authorized_root_candidates() -> list[Path]:
    return [Path(root).expanduser().resolve() for root in settings.family_import_roots]


def _ensure_authorized_package_path(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    allowed_roots = _authorized_root_candidates()
    if not allowed_roots:
        return resolved
    if any(resolved == root or root in resolved.parents for root in allowed_roots):
        return resolved
    roots = ", ".join(str(root) for root in allowed_roots)
    raise HTTPException(
        status_code=403,
        detail=f"Family import path is outside configured FAMILY_IMPORT_ROOTS: {roots}",
    )


def _manifest_candidates(root: Path) -> list[Path]:
    return [root / "manifest.yaml", root / "manifest.yml", root / "manifest.json"]


def _find_manifest(root: Path) -> Path | None:
    return next((candidate for candidate in _manifest_candidates(root) if candidate.is_file()), None)


def _parse_manifest(path: Path) -> PackageManifest:
    raw_text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        payload = json.loads(raw_text)
    else:
        payload = yaml.safe_load(raw_text)
    if not isinstance(payload, dict):
        raise ValueError("Manifest must contain a mapping/object at the top level")
    return PackageManifest.model_validate(payload)


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


def _parse_ped_text_strict(text_value: str) -> tuple[ParsedPed | None, list[FamilyImportValidationIssue]]:
    errors: list[FamilyImportValidationIssue] = []
    members: list[PedMember] = []
    seen_samples: set[str] = set()
    duplicate_samples: set[str] = set()
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
        family_id, individual_id, father_id, mother_id, sex, phenotype = parts[:6]
        if individual_id in seen_samples:
            duplicate_samples.add(individual_id)
        seen_samples.add(individual_id)
        if sex not in {"0", "1", "2"}:
            errors.append(
                _issue(
                    "ped_invalid_sex",
                    f"PED row {line_no} has unsupported sex code '{sex}'",
                    sample_id=individual_id,
                )
            )
        if phenotype not in {"0", "1", "2", "-9"}:
            errors.append(
                _issue(
                    "ped_invalid_phenotype",
                    f"PED row {line_no} has unsupported phenotype code '{phenotype}'",
                    sample_id=individual_id,
                )
            )
        members.append(
            PedMember(
                family_id=family_id,
                iid=individual_id,
                pid=father_id,
                mid=mother_id,
                sex=sex,
                phen=phenotype,
                line_no=line_no,
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
            " ".join([member.family_id, member.iid, member.pid, member.mid, member.sex, member.phen])
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
    index_optional = (
        dataset_type == "repeats_trgt"
        and vcf_path is not None
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
    if dataset.per_sample:
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
                value=entry.get("bed") or entry.get("file"),
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
    if not dataset.per_sample:
        errors.append(
            _issue(
                "dataset_per_sample_missing",
                "Haplotype dataset must define per_sample entries",
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
            value=entry.get("index"),
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
    if dataset_type == "apcad":
        return _validate_apcad_dataset(
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


def load_validated_family_package(folder_path: str | Path) -> tuple[FamilyPackageValidationOut, FamilyPackageBundle | None]:
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
        manifest = _parse_manifest(manifest_path)
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
    if "schema_version" not in _json_dict(yaml.safe_load(manifest_path.read_text(encoding="utf-8")) if manifest_path.suffix.lower() != ".json" else json.loads(manifest_path.read_text(encoding="utf-8"))):
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
    if ped_path is None:
        errors.append(_issue("ped_missing_path", "Manifest must define a PED path", path=manifest_path))
    elif not ped_path.is_file():
        errors.append(_issue("ped_file_missing", "PED file does not exist", path=ped_path))
    else:
        try:
            ped_text = ped_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            errors.append(_issue("ped_decode_failed", f"PED file is not UTF-8 text: {exc}", path=ped_path))
        else:
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


def validate_family_package(folder_path: str | Path) -> FamilyPackageValidationOut:
    validation, _bundle = load_validated_family_package(folder_path)
    return validation


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
        if sample_complete:
            complete_samples.append(sample_id)
            per_sample[sample_id] = sample_entry

    complete = bool(complete_samples)
    display_entry: dict[str, Any] = {
        "enabled": complete,
        "per_sample": per_sample if complete else {
            sample_id: {
                role: _choose_candidate_path(
                    root,
                    patterns[role],
                    family_id=family_id,
                    sample_id=sample_id,
                )[0]
                for role in required_roles
            }
            for sample_id in sample_ids
        },
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
        "apcad": ["bed"],
        "haplotypes": ["file", "index"],
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

    family_id = (request.family_id or root.name).strip()
    ped_path, ped_errors, ped_warnings = _detect_ped_path(
        root,
        requested_ped_path=request.ped_path,
        family_id=family_id,
    )
    errors.extend(ped_errors)
    warnings.extend(ped_warnings)
    parsed_ped: ParsedPed | None = None
    if ped_path is not None:
        try:
            parsed_ped, ped_parse_errors = _parse_ped_text_strict(
                ped_path.read_text(encoding="utf-8")
            )
            errors.extend(ped_parse_errors)
        except UnicodeDecodeError as exc:
            errors.append(_issue("ped_decode_failed", f"PED file is not UTF-8 text: {exc}", path=ped_path))

    sample_ids = parsed_ped.sample_ids if parsed_ped is not None else []
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
        validation=validate_family_package(root),
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


def _ped_members_for_import(ped: ParsedPed) -> list[dict[str, Any]]:
    fathers = {member.pid for member in ped.members if member.pid not in {"", "0"}}
    mothers = {member.mid for member in ped.members if member.mid not in {"", "0"}}
    family_members: list[dict[str, Any]] = []
    for member in ped.members:
        role = "proband"
        if member.iid in fathers:
            role = "father"
        elif member.iid in mothers:
            role = "mother"
        elif family_members:
            role = "sibling"
        family_members.append(
            {
                "sample_id": member.iid,
                "sex": {"1": "male", "2": "female"}.get(member.sex, "und"),
                "role": role,
                "affected": member.phen == "2",
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
    path_keys = {"bins", "segments", "file", "index", "json", "bed", "vcf", "family_vcf", "annotation_tsv"}
    for dataset_type, dataset in bundle.manifest.datasets.items():
        if not dataset.enabled:
            continue
        for sample_id, raw_entry in dataset.per_sample.items():
            if not isinstance(raw_entry, dict):
                continue
            files = {
                key: _display_path(bundle.root, resolved)
                for key, value in raw_entry.items()
                if key in path_keys
                for resolved in [_resolve_package_path(bundle.root, str(value))]
                if resolved is not None
            }
            sample_payloads.setdefault(sample_id, {})[dataset_type] = files
    return sample_payloads


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
    if sample_metadata or sample_provenance:
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
            if sample_id in sample_metadata:
                metadata["package_sample_metadata"] = sample_metadata[sample_id]
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
        await ped_service._create_family(
            session,
            family_id=family_id,
            pedigree=bundle.ped.text,
            members=_ped_members_for_import(bundle.ped),
            project_id=resolved_project_id,
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


def _first_info_value(info: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = info.get(key)
        if not _missing_scalar(value):
            return value
    return None


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
    )
    await delete_interval_track_sources(
        session,
        sample_uuid=sample_context.sample_uuid,
        track_type=track_type,
        source=source,
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


def _header_value(parts: list[str], header: dict[str, int], *names: str) -> str | None:
    for name in names:
        index = header.get(name)
        if index is not None and index < len(parts):
            return parts[index]
    return None


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

    async def report_snv_progress(stats: dict[str, Any]) -> None:
        if progress is None:
            return
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

    if annotation_path is not None:
        async with _local_upload(vcf_path) as upload:
            async with _local_upload(annotation_path) as annotation_upload:
                result = await upload_family_small_variant_file(
                    session,
                    context=family_context,
                    sample_contexts=sample_contexts,
                    file=upload,
                    annotation_file=annotation_upload,
                    overwrite=True,
                    format_hint=source_format,  # type: ignore[arg-type]
                    progress=report_snv_progress,
                )
    else:
        async with _local_upload(vcf_path) as upload:
            result = await upload_family_small_variant_file(
                session,
                context=family_context,
                sample_contexts=sample_contexts,
                file=upload,
                overwrite=True,
                format_hint=source_format,  # type: ignore[arg-type]
                progress=report_snv_progress,
            )
    return summary.model_copy(
        update={
            "status": "imported",
            "message": "Imported through existing family small-variant loader",
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
        if isinstance(stats, dict) and stats.get("skipped")
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
        bed_path = _resolve_package_path(bundle.root, raw_entry.get("bed") or raw_entry.get("file"))
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
        if isinstance(stats, dict) and stats.get("skipped")
    ]
    return summary.model_copy(
        update={
            "status": "imported",
            "message": (
                "Imported through existing APCAD interval-track loader"
                if not skipped
                else f"Imported APCAD data; skipped existing samples in update mode: {', '.join(skipped)}"
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
        if isinstance(stats, dict) and stats.get("skipped")
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
    if summary.dataset_type == "apcad":
        return await _import_apcad_dataset(
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
        return await _register_only(
            summary,
            "Registered only; direct GLIMPSE2 BCF haplotype import is not implemented yet",
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
    validation, bundle = load_validated_family_package(folder_path)
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
            raise
        if progress is not None:
            await progress(validation, datasets, logs, family_context.family_id)

    logs.append("Family package import completed.")
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
            await _update_job_progress(
                session,
                job_id=job_id,
                worker_id=worker_id,
                status=next_status,
                family_id=family_id,
                validation=validation,
                datasets=datasets,
                logs=logs,
            )

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
