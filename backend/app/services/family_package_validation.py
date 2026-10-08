from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError
import yaml

from ..core.object_storage import join_remote_uri, remote_folder_name
from ..schemas import (
    FamilyImportDatasetSummary,
    FamilyImportValidationIssue,
    FamilyPackageValidationOut,
)
from .hpo_service import (
    HpoAnnotationImportIssue,
    HpoAnnotationImportRow,
    parse_hpo_tsv_path,
    parse_manifest_inline_hpo,
)

from .family_package_common import CORE_DATASETS, HAPLOTYPE_ORIGIN_ROLES, IDENTIFIER_ISSUE_CODES, QC_FAMILY_ROLES, QC_SAMPLE_ROLES, FamilyPackageBundle, ManifestDataset, PackageManifest, ParsedPed, SUPPORTED_DATASETS, _display_path, _is_uncompressed_vcf, _issue, _resolve_package_path, _vcf_index_candidates, family_id_issue, read_vcf_sample_columns, sample_id_issues
from .family_package_manifest import _manifest_added_member_entries, _manifest_added_ped_rows, _manifest_derived_statuses, _manifest_pgt_metadata, _manifest_relationship_issues, _manifest_roi_value, _normalize_manifest_samples, _parse_ped_text_strict
from .family_package_source import _ensure_authorized_package_path, _find_manifest, _parse_manifest, staged_package_source
from .nipt import MONOGENIC_NIPT_ANALYSIS_TYPE
from .nipt_target_coverage import target_table_missing_columns


def _add_missing_optional_dataset_warnings(
    warnings: list[FamilyImportValidationIssue],
    summaries: list[FamilyImportDatasetSummary],
    present_datasets: set[str],
) -> None:
    for dataset_type in SUPPORTED_DATASETS:
        if dataset_type in present_datasets:
            continue
        # Every supported dataset still gets a "skipped" summary row so the import
        # report shows the full dataset table, but only the datasets a package is
        # actually expected to carry raise a warning. Assay-specific ones (PGT's
        # apcad/pcf, the long-read cnv/mito/qc/…) are absent by design in most
        # packages, and warning on each of them buries the real warnings.
        if dataset_type in CORE_DATASETS:
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
    remote_only_files: frozenset[str] = frozenset(),
) -> Path | None:
    """Resolve a manifest path and require the file. ``remote_only_files`` are package
    files staging left in the object store; they count as present."""
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
    if not path.is_file() and _display_path(root, path) not in remote_only_files:
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


def _vcf_columns_if_readable(path: Path | None) -> list[str] | None:
    """A VCF's sample columns, read from its header only, or None when the file cannot be
    read as a VCF here; the importer then reports what is wrong with it."""
    if path is None or not path.is_file():
        return None
    try:
        return read_vcf_sample_columns(path)
    except (OSError, EOFError, UnicodeError, ValueError):
        return None


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


# The ``source_format`` a per-sample SNV callset outside monogenic NIPT declares: the
# family's primary callset of germline calls, one file per sample (the long-read
# pipeline's), which the import reads side by side as one callset.
PER_SAMPLE_PRIMARY_SOURCE_FORMAT = "clair3"


def _validate_per_sample_snv_dataset(
    *,
    root: Path,
    dataset: ManifestDataset,
    ped_sample_ids: set[str],
    errors: list[FamilyImportValidationIssue],
    analysis_type: str | None,
) -> FamilyImportDatasetSummary:
    """Validate ``snv.per_sample``: an SNV callset of one file per sample.

    Two kinds are read. A monogenic NIPT's files (the plasma and the father, Mutect2
    tumour-only calls whose GT says nothing of the genotype), and a primary callset of
    germline calls made one sample at a time (the long-read pipeline's), which the
    dataset declares with ``source_format: clair3``. Anything else is refused: a NIPT
    pair's files read as germline calls would give every call the genotype Mutect2 wrote.
    Each file must hold exactly one sample (it is bound to its entry's sample).
    """
    files: list[str] = []
    samples: list[str] = []
    before = len(errors)
    source_format = str((dataset.model_extra or {}).get("source_format") or "").strip()
    if analysis_type != MONOGENIC_NIPT_ANALYSIS_TYPE and source_format != PER_SAMPLE_PRIMARY_SOURCE_FORMAT:
        errors.append(
            _issue(
                "dataset_per_sample_unsupported",
                "A per-sample SNV callset (snv.per_sample) is read for a monogenic NIPT family "
                "(analysis_type: monogenic_nipt), or as the family's callset of germline calls "
                "made one sample at a time when the dataset says source_format: clair3; "
                "otherwise give a joint VCF as snv.family_vcf",
                dataset="snv",
            )
        )
    for sample_id, raw_entry in dataset.per_sample.items():
        samples.append(sample_id)
        _validate_per_sample_id(
            dataset_type="snv",
            sample_id=sample_id,
            ped_sample_ids=ped_sample_ids,
            errors=errors,
        )
        entry = _sample_entry_mapping(
            dataset_type="snv",
            sample_id=sample_id,
            entry=raw_entry,
            errors=errors,
        )
        vcf_path = _require_file(
            root=root,
            dataset_type="snv",
            value=entry.get("vcf") or entry.get("file"),
            field_name="vcf",
            errors=errors,
            files=files,
            sample_id=sample_id,
        )
        if vcf_path is not None and vcf_path.is_file():
            columns = read_vcf_sample_columns(vcf_path)
            if len(columns) != 1:
                errors.append(
                    _issue(
                        "dataset_vcf_not_single_sample",
                        f"The SNV file of {sample_id} must hold exactly one sample; it has {len(columns)}",
                        dataset="snv",
                        sample_id=sample_id,
                        path=vcf_path,
                    )
                )
    return FamilyImportDatasetSummary(
        dataset_type="snv",
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
        if entry.get("target_table"):
            # A capture panel's per-target coverage table (monogenic NIPT); its header
            # must name the columns the parser reads.
            table_path = _require_file(
                root=root,
                dataset_type="coverage",
                value=entry.get("target_table"),
                field_name="target_table",
                errors=errors,
                files=files,
                sample_id=sample_id,
            )
            if table_path is not None and table_path.is_file():
                missing = target_table_missing_columns(table_path)
                if missing:
                    errors.append(
                        _issue(
                            "coverage_target_table_columns",
                            f"The coverage table of {sample_id} has no column {', '.join(missing)}",
                            dataset="coverage",
                            sample_id=sample_id,
                            path=table_path,
                        )
                    )
            continue
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
    warnings: list[FamilyImportValidationIssue] | None = None,
    ped: ParsedPed | None = None,
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
                field_name="vcf" if entry.get("vcf") and not entry.get("bed") else "bed",
                errors=errors,
                files=files,
                sample_id=sample_id,
            )
            if entry.get("index"):
                _require_file(
                    root=root,
                    dataset_type="apcad",
                    value=entry.get("index"),
                    field_name="index",
                    errors=errors,
                    files=files,
                    sample_id=sample_id,
                )
            _check_apcad_sample_vcf_columns(
                root=root,
                sample_id=sample_id,
                entry=entry,
                ped=ped,
                errors=errors,
                warnings=warnings,
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


def _check_apcad_sample_vcf_columns(
    *,
    root: Path,
    sample_id: str,
    entry: dict[str, Any],
    ped: ParsedPed | None,
    errors: list[FamilyImportValidationIssue],
    warnings: list[FamilyImportValidationIssue] | None,
) -> None:
    """A per-sample APCAD VCF must hold the sample's column. A trio VCF (the PGT
    pipeline's) also holds the parents' columns, which give each site its parental
    origin: without one, the import still runs but cannot say which parent an allele
    came from, so that is a warning."""
    value = entry.get("vcf") or entry.get("bed") or entry.get("file")
    if not isinstance(value, str) or not value.lower().endswith((".vcf", ".vcf.gz")):
        return
    try:
        path = _resolve_package_path(root, value)
    except HTTPException:
        return
    columns = _vcf_columns_if_readable(path)
    if not columns:
        return
    column = entry.get("vcf_sample") or entry.get("sample_name") or sample_id
    if column not in columns:
        errors.append(
            _issue(
                "dataset_vcf_sample_missing",
                f"APCAD VCF {value} has no column '{column}' (it has {', '.join(columns)}); "
                "name the sample's column with vcf_sample",
                dataset="apcad",
                sample_id=sample_id,
                path=path,
            )
        )
        return
    member = next((member for member in (ped.members if ped else []) if member.iid == sample_id), None)
    if member is None or warnings is None or len(columns) < 2:
        return
    for parent in (member.pid, member.mid):
        if parent not in {"", "0"} and parent not in columns:
            warnings.append(
                _issue(
                    "apcad_parent_column_missing",
                    f"APCAD VCF {value} has no column for {sample_id}'s parent {parent}: "
                    "its sites cannot be given that parent's origin",
                    dataset="apcad",
                    sample_id=sample_id,
                    path=path,
                )
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
        unknown_columns = [
            column for column in (_vcf_columns_if_readable(family_vcf_path) or []) if column not in ped_sample_ids
        ]
        if unknown_columns:
            # The loader binds every column to a member and stops at the first it cannot:
            # a PGT index the PED lacks would otherwise fail the import after the other
            # datasets were written.
            errors.append(
                _issue(
                    "dataset_vcf_sample_unknown",
                    f"The phased family VCF has sample column(s) {', '.join(unknown_columns)} that "
                    "are not members of the family; add a member the PED lacks under "
                    "family.add_members",
                    dataset="haplotypes",
                    path=family_vcf_path,
                )
            )
        extra = dataset.model_extra or {}
        for role in HAPLOTYPE_ORIGIN_ROLES:
            if extra.get(role):
                _require_file(
                    root=root,
                    dataset_type="haplotypes",
                    value=extra.get(role),
                    field_name=role,
                    errors=errors,
                    files=files,
                )
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


# Per-sample roles each long-read dataset expects. The first entry of each tuple is
# required; the rest are optional companions (index, annotation, auxiliary output)
# that are validated only when the manifest declares them.
_PER_SAMPLE_DATASET_ROLES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # dataset_type: (required roles, optional roles)
    "cnv": (("vcf",), ("index", "copy_number_bedgraph", "depth_bigwig", "maf_bigwig", "summary_html")),
    "mito": (("vcf",), ("index", "annotation_tsv", "sv_vcf", "sv_index", "sv_annotation_tsv")),
    "alignments": (("file",), ("index",)),
    "qc": ((), QC_SAMPLE_ROLES),
    # repeats_trgt is family-level by default; the long-read pipeline writes it per
    # sample (repeats/<sample>/<sample>_tr.vcf.gz), so both shapes are accepted.
    "repeats_trgt": (("file",), ("index",)),
    # So is sv_needlr: sv/<sample>/needlr/<sample>_sv_phased.needLR.<version>.vcf.gz.
    "sv_needlr": (("vcf",), ("index",)),
}


def _validate_per_sample_file_dataset(
    *,
    root: Path,
    dataset_type: str,
    dataset: ManifestDataset,
    ped_sample_ids: set[str],
    errors: list[FamilyImportValidationIssue],
    remote_only_files: frozenset[str] = frozenset(),
) -> FamilyImportDatasetSummary:
    """Validate a dataset declared as ``per_sample: {<sample_id>: {<role>: <path>}}``.

    Shared by the long-read datasets (cnv, mito, alignments, qc), which the pipeline
    all writes per sample. Required roles must resolve to an existing file; optional
    roles are only checked when declared, so a package that ran without (say) mosdepth
    still validates. A file in ``remote_only_files`` exists in the object store without
    having been staged.
    """
    required_roles, optional_roles = _PER_SAMPLE_DATASET_ROLES[dataset_type]
    files: list[str] = []
    samples: list[str] = []
    before = len(errors)
    # The PGT pipeline writes part of its QC per family (one ADO/ADI table, one KING
    # table for all samples), so a qc dataset may declare those files beside, or
    # instead of, its per-sample entries.
    family_roles = QC_FAMILY_ROLES if dataset_type == "qc" else ()
    extra = dataset.model_extra or {}
    declared_family_roles = [role for role in family_roles if extra.get(role)]
    for role in declared_family_roles:
        _require_file(
            root=root,
            dataset_type=dataset_type,
            value=extra.get(role),
            field_name=role,
            errors=errors,
            files=files,
        )
    if not dataset.per_sample and not declared_family_roles:
        errors.append(
            _issue(
                "dataset_per_sample_missing",
                f"Dataset '{dataset_type}' must define per_sample entries",
                dataset=dataset_type,
            )
        )
    for sample_id, raw_entry in dataset.per_sample.items():
        samples.append(sample_id)
        _validate_per_sample_id(
            dataset_type=dataset_type,
            sample_id=sample_id,
            ped_sample_ids=ped_sample_ids,
            errors=errors,
        )
        entry = _sample_entry_mapping(
            dataset_type=dataset_type,
            sample_id=sample_id,
            entry=raw_entry,
            errors=errors,
        )
        for role in required_roles:
            _require_file(
                root=root,
                dataset_type=dataset_type,
                value=entry.get(role),
                field_name=role,
                errors=errors,
                files=files,
                sample_id=sample_id,
                remote_only_files=remote_only_files,
            )
        for role in optional_roles:
            if entry.get(role):
                _require_file(
                    root=root,
                    dataset_type=dataset_type,
                    value=entry.get(role),
                    field_name=role,
                    errors=errors,
                    files=files,
                    sample_id=sample_id,
                    remote_only_files=remote_only_files,
                )
        if dataset_type == "qc" and not any(entry.get(role) for role in optional_roles):
            errors.append(
                _issue(
                    "dataset_missing_path",
                    f"QC dataset for sample '{sample_id}' declares no QC artefact",
                    dataset=dataset_type,
                    sample_id=sample_id,
                )
            )
    return FamilyImportDatasetSummary(
        dataset_type=dataset_type,
        enabled=True,
        status="error" if len(errors) > before else "valid",
        files=list(dict.fromkeys(files)),
        samples=samples,
    )


def _validate_pipeline_info_dataset(
    *,
    root: Path,
    dataset: ManifestDataset,
    errors: list[FamilyImportValidationIssue],
) -> FamilyImportDatasetSummary:
    """Validate the family-level ``pipeline_info`` block (Nextflow run record).

    Both artefacts are optional individually, but declaring the dataset with neither
    is a manifest mistake worth reporting rather than a silent no-op.
    """
    files: list[str] = []
    before = len(errors)
    extra = dataset.model_extra or {}
    declared = {
        role: extra.get(role)
        for role in ("params", "versions", "execution_trace", "execution_report")
    }
    if not any(declared.values()):
        errors.append(
            _issue(
                "dataset_missing_path",
                "Dataset 'pipeline_info' declares neither params nor versions",
                dataset="pipeline_info",
            )
        )
    for role, value in declared.items():
        if value:
            _require_file(
                root=root,
                dataset_type="pipeline_info",
                value=value,
                field_name=role,
                errors=errors,
                files=files,
            )
    return FamilyImportDatasetSummary(
        dataset_type="pipeline_info",
        enabled=True,
        status="error" if len(errors) > before else "valid",
        files=list(dict.fromkeys(files)),
    )


def _validate_dataset(
    *,
    root: Path,
    dataset_type: str,
    dataset: ManifestDataset,
    ped_sample_ids: set[str],
    errors: list[FamilyImportValidationIssue],
    remote_only_files: frozenset[str] = frozenset(),
    warnings: list[FamilyImportValidationIssue] | None = None,
    ped: ParsedPed | None = None,
    analysis_type: str | None = None,
) -> FamilyImportDatasetSummary:
    if not dataset.enabled:
        return FamilyImportDatasetSummary(
            dataset_type=dataset_type,
            enabled=False,
            status="disabled",
            message="Dataset disabled in manifest",
        )
    if dataset_type == "snv" and dataset.per_sample and not dataset.family_vcf:
        return _validate_per_sample_snv_dataset(
            root=root,
            dataset=dataset,
            ped_sample_ids=ped_sample_ids,
            errors=errors,
            analysis_type=analysis_type,
        )
    if dataset_type in {"snv", "sv_needlr", "repeats_trgt"}:
        # TRGT and NeedlR are family-level when a joint VCF exists and per-sample
        # otherwise (the long-read pipeline writes repeats/<sample>/<sample>_tr.vcf.gz and
        # one annotated SV VCF per sample).
        if dataset_type in {"repeats_trgt", "sv_needlr"} and not dataset.family_vcf and dataset.per_sample:
            return _validate_per_sample_file_dataset(
                root=root,
                dataset_type=dataset_type,
                dataset=dataset,
                ped_sample_ids=ped_sample_ids,
                errors=errors,
            )
        return _validate_family_vcf_dataset(
            root=root,
            dataset_type=dataset_type,
            dataset=dataset,
            errors=errors,
        )
    if dataset_type in _PER_SAMPLE_DATASET_ROLES:
        return _validate_per_sample_file_dataset(
            root=root,
            dataset_type=dataset_type,
            dataset=dataset,
            ped_sample_ids=ped_sample_ids,
            errors=errors,
            # Only the alignments importer never reads its files, so only its files may
            # stay in the object store; every other dataset needs a staged copy.
            remote_only_files=remote_only_files if dataset_type == "alignments" else frozenset(),
        )
    if dataset_type == "pipeline_info":
        return _validate_pipeline_info_dataset(root=root, dataset=dataset, errors=errors)
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
            warnings=warnings,
            ped=ped,
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
    # A dataset listed in SUPPORTED_DATASETS but with no validator branch: report it
    # as an error instead of returning status="error" with nothing in `errors`, which
    # would leave the package "valid" while its dataset row says otherwise.
    errors.append(
        _issue(
            "dataset_validator_missing",
            f"Dataset '{dataset_type}' is supported but has no validator",
            dataset=dataset_type,
        )
    )
    return FamilyImportDatasetSummary(
        dataset_type=dataset_type,
        enabled=True,
        status="error",
        message="Supported dataset has no validator",
    )


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
    remote_only_files: frozenset[str] = frozenset(),
    source_uri: str | None = None,
) -> tuple[FamilyPackageValidationOut, FamilyPackageBundle | None]:
    """Validate the package at ``folder_path`` and load it for import.

    For a package staged from a bucket (``StagedPackage``), ``source_uri`` is the folder
    it was staged from and ``remote_only_files`` the files staging left in the store,
    which an alignments dataset may reference. The staging copy is deleted after the
    import, so the report -- which the import job stores and the import panel shows --
    names the source folder's objects rather than staged paths, and the bundle carries
    ``source_uri`` for the records the import writes.
    """
    validation, bundle = _validate_and_load_package(
        folder_path,
        fallback_ped_text=fallback_ped_text,
        remote_only_files=remote_only_files,
        # A manifest without family_id names the family after its folder: for a bucket,
        # the source folder, never the staging directory the copy sits in (pkg-...).
        folder_name=remote_folder_name(source_uri) if source_uri else None,
    )
    if source_uri is None:
        return validation, bundle
    if bundle is not None:
        bundle.source_uri = source_uri
    staged_root = Path(folder_path).expanduser().resolve()
    return _report_staged_paths_as_objects(validation, staged_root, source_uri), bundle


def _staged_path_as_object(path: str | None, staged_root: Path, source_uri: str) -> str | None:
    """A path inside the staging copy, as the URI of the object it is a copy of."""
    if path is None:
        return None
    try:
        relative = Path(path).relative_to(staged_root)
    except ValueError:
        return path
    return join_remote_uri(source_uri, relative.as_posix()) if relative.parts else source_uri


def _report_staged_paths_as_objects(
    validation: FamilyPackageValidationOut, staged_root: Path, source_uri: str
) -> FamilyPackageValidationOut:
    def relabel(issues: list[FamilyImportValidationIssue]) -> list[FamilyImportValidationIssue]:
        return [
            issue.model_copy(update={"path": _staged_path_as_object(issue.path, staged_root, source_uri)})
            for issue in issues
        ]

    return validation.model_copy(
        update={
            "manifest_path": _staged_path_as_object(validation.manifest_path, staged_root, source_uri),
            "ped_path": _staged_path_as_object(validation.ped_path, staged_root, source_uri),
            "errors": relabel(validation.errors),
            "warnings": relabel(validation.warnings),
        }
    )


def _validate_and_load_package(
    folder_path: str | Path,
    *,
    fallback_ped_text: str | None,
    remote_only_files: frozenset[str],
    folder_name: str | None,
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

    family_id = (manifest.family_id or folder_name or root.name).strip()
    family_issue = family_id_issue(
        family_id, source="the manifest's family_id" if manifest.family_id else "the folder name"
    )
    if family_issue is not None:
        # Nothing else is read under an ID the family cannot be stored under: the PED rows
        # family.add_members adds would carry it too.
        errors.append(family_issue)
        return (
            FamilyPackageValidationOut(
                valid=False,
                manifest_path=str(manifest_path),
                errors=errors,
                warnings=warnings,
                datasets=summaries,
                metadata=metadata,
            ),
            None,
        )
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
        if _manifest_added_member_entries(manifest):
            # No PED: the manifest names every member under family.add_members (the
            # long-read pipeline writes no PED for a couple screened for carriership).
            ped_text = ""
            metadata["ped_source"] = "manifest"
        else:
            errors.append(
                _issue(
                    "ped_missing_path",
                    "Manifest must define a PED path, or name the family's members under family.add_members",
                    path=manifest_path,
                )
            )
    else:
        errors.append(_issue("ped_file_missing", "PED file does not exist", path=ped_path))

    if ped_text is not None:
        if ped_text.strip():
            ped, ped_errors = _parse_ped_text_strict(ped_text)
        else:
            # The members are the manifest's alone: its added rows below are the PED.
            ped, ped_errors = ParsedPed(family_ids=[], members=[], sample_ids=[], text=""), []
        if ped is not None:
            added_rows, added_errors = _manifest_added_ped_rows(
                manifest,
                family_id=family_id,
                ped_sample_ids=set(ped.sample_ids),
                ped_from_database=metadata.get("ped_source") == "database",
            )
            errors.extend(added_errors)
            if added_rows:
                # Re-parsed with the added rows appended to the file's own text, so the
                # file's rows keep their line numbers and the added members get every
                # PED check (duplicate ids, parents) the file's members get.
                ped_rows = [ped_text.rstrip("\n"), *added_rows] if ped_text.strip() else added_rows
                ped, ped_errors = _parse_ped_text_strict("\n".join(ped_rows))
                metadata["added_members"] = [row.split()[1] for row in added_rows]
            elif not ped.members:
                ped, ped_errors = None, [
                    _issue("ped_empty", "family.add_members names no member the package can import")
                ]
        errors.extend(ped_errors)

    if ped is not None:
        # The PED's own IDs, or the stored pedigree's for an import into an existing family;
        # family.add_members' were checked as their rows were made.
        from_database = metadata.get("ped_source") == "database"
        ped_source = "in the family's stored pedigree" if from_database else "in the PED"
        ped_issue_path = None if from_database else ped_path
        for ped_family_id in ped.family_ids:
            ped_family_issue = (
                family_id_issue(ped_family_id, source=ped_source, path=ped_issue_path)
                if ped_family_id != family_id
                else None
            )
            if ped_family_issue is not None:
                errors.append(ped_family_issue)
        errors.extend(sample_id_issues(ped.sample_ids, source=ped_source, path=ped_issue_path))
        if any(issue.code in IDENTIFIER_ISSUE_CODES for issue in errors):
            # Nothing else is read under an ID that cannot be stored: the dataset summaries
            # and the job's record would carry it.
            ped = None

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

        errors.extend(_manifest_relationship_issues(manifest, set(ped.sample_ids), ped))
        _derived_statuses, status_errors, status_warnings = _manifest_derived_statuses(manifest, ped)
        errors.extend(status_errors)
        warnings.extend(status_warnings)

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
                    remote_only_files=remote_only_files,
                    warnings=warnings,
                    ped=ped,
                    analysis_type=manifest.analysis_type,
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
    if errors or ped is None:
        return validation, None
    return validation, FamilyPackageBundle(
        root=root,
        manifest_path=manifest_path,
        manifest=manifest,
        ped_path=ped_path,
        ped=ped,
        remote_only_files=remote_only_files,
    )


def validate_family_package(
    folder_path: str | Path,
    *,
    fallback_ped_text: str | None = None,
) -> FamilyPackageValidationOut:
    with staged_package_source(folder_path) as staged:
        validation, _bundle = load_validated_family_package(
            staged.root,
            fallback_ped_text=fallback_ped_text,
            remote_only_files=staged.remote_only_files,
            source_uri=staged.source_uri,
        )
    return validation
