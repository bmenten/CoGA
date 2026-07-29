from __future__ import annotations

import logging
from pathlib import Path
import re
from typing import Any

from fastapi import HTTPException
import yaml

from ..schemas import (
    FamilyManifestDatasetAvailability,
    FamilyManifestFileAvailability,
    FamilyImportValidationIssue,
    FamilyPackageManifestBuildOut,
    FamilyPackageManifestBuildRequest,
    FamilyPackageManifestWriteOut,
)

from .family_package_common import PackageManifest, ParsedPed, _display_path, _is_uncompressed_vcf, _issue, _resolve_package_path  # noqa: F401
from .family_package_manifest import _parse_ped_text_strict  # noqa: F401
from .family_package_source import _ensure_authorized_package_path, _existing_manifest_dict  # noqa: F401
from .family_package_validation import validate_family_package  # noqa: F401


logger = logging.getLogger(__name__)


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
                    # nf-core/lrsvar writes the VEP-annotated callset per sample. For a
                    # single-sample family that file *is* the family callset, so these
                    # are only rendered when the family has exactly one sample.
                    "snv/{sample_id}/annotation/{sample_id}_annot.vcf.gz",
                    "snv/annotation/{sample_id}/{sample_id}_annot.vcf.gz",
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
                    "snv/{sample_id}/annotation/{sample_id}_annot.vcf.gz.tbi",
                    "snv/{sample_id}/annotation/{sample_id}_annot.vcf.gz.csi",
                    "snv/annotation/{sample_id}/{sample_id}_annot.vcf.gz.tbi",
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
                    # nf-core/lrsvar: sv/<sample>/annotation/<sample>_sv_phased.needLR.<version>.vcf.gz
                    # The NeedlR release is part of the filename, so only a glob matches.
                    "sv/{sample_id}/annotation/{sample_id}*.needLR.*.vcf.gz",
                    "sv/annotation/{sample_id}/{sample_id}*.needLR.*.vcf.gz",
                ],
                "index": [
                    "needlr/{family_id}.sv.annotated.vcf.gz.tbi",
                    "needlr/family.sv.annotated.vcf.gz.tbi",
                    "sv_needlr/{family_id}.sv.annotated.vcf.gz.tbi",
                    "sv_needlr/family.sv.annotated.vcf.gz.tbi",
                    "sv/{sample_id}/annotation/{sample_id}*.needLR.*.vcf.gz.tbi",
                    "sv/{sample_id}/annotation/{sample_id}*.needLR.*.vcf.gz.csi",
                    "sv/annotation/{sample_id}/{sample_id}*.needLR.*.vcf.gz.tbi",
                ],
            },
            "repeats_trgt": {
                # Per-sample roles first: the long-read pipeline never writes a joint
                # TRGT VCF, so a family package usually declares repeats per sample.
                "file": [
                    "repeats/{sample_id}/{sample_id}_tr.vcf.gz",
                    "repeats/{sample_id}/{sample_id}.trgt.vcf.gz",
                    "repeats/{sample_id}/{sample_id}_tr.vcf",
                    "repeats/{sample_id}_tr.vcf.gz",
                    "repeats/{sample_id}.trgt.vcf.gz",
                ],
                "sample_index": [
                    "repeats/{sample_id}/{sample_id}_tr.vcf.gz.csi",
                    "repeats/{sample_id}/{sample_id}_tr.vcf.gz.tbi",
                    "repeats/{sample_id}/{sample_id}.trgt.vcf.gz.csi",
                    "repeats/{sample_id}/{sample_id}.trgt.vcf.gz.tbi",
                    "repeats/{sample_id}_tr.vcf.gz.csi",
                    "repeats/{sample_id}_tr.vcf.gz.tbi",
                ],
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
                    # Tolerate a suffixed per-sample directory (e.g. a copy artefact
                    # such as "HG002 2") so the package still discovers cleanly.
                    "paraphase/{sample_id}*/{sample_id}.paraphase.json",
                ],
                "bam": [
                    "paraphase/{sample_id}/{sample_id}.paraphase.bam",
                    "paraphase/{sample_id}*/{sample_id}.paraphase.bam",
                ],
            },
            # ---- nf-core/lrsvar long-read datasets (all per-sample) ----------------
            "cnv": {
                "vcf": [
                    "cnv/{sample_id}/annotation/{sample_id}_annot.vcf.gz",
                    "cnv/annotation/{sample_id}/{sample_id}_annot.vcf.gz",
                    "cnv/{sample_id}/{sample_id}*.vcf.gz",
                ],
                "index": [
                    "cnv/{sample_id}/annotation/{sample_id}_annot.vcf.gz.tbi",
                    "cnv/{sample_id}/annotation/{sample_id}_annot.vcf.gz.csi",
                    "cnv/annotation/{sample_id}/{sample_id}_annot.vcf.gz.tbi",
                    "cnv/{sample_id}/{sample_id}*.vcf.gz.tbi",
                ],
                "copy_number_bedgraph": [
                    "cnv/{sample_id}/{sample_id}*.copynum.bedgraph",
                    "cnv/{sample_id}/{sample_id}.copynum.bedgraph",
                ],
                "depth_bigwig": [
                    "cnv/{sample_id}/{sample_id}*.depth.bw",
                ],
                "summary_html": [
                    "cnv/{sample_id}/annotation/{sample_id}_annot.vcf.gz_summary.html",
                ],
            },
            "mito": {
                "vcf": [
                    "mito/{sample_id}/{sample_id}.vcf.gz",
                    "mito/{sample_id}.vcf.gz",
                ],
                "index": [
                    "mito/{sample_id}/{sample_id}.vcf.gz.tbi",
                    "mito/{sample_id}/{sample_id}.vcf.gz.csi",
                    "mito/{sample_id}.vcf.gz.tbi",
                ],
                "annotation_tsv": [
                    "mito/annotation/{sample_id}/{sample_id}_snv_annot.txt",
                    "mito/{sample_id}/annotation/{sample_id}_snv_annot.txt",
                ],
                "sv_vcf": [
                    "mito/{sample_id}/{sample_id}_sv.vcf.gz",
                    "mito/{sample_id}_sv.vcf.gz",
                ],
                "sv_index": [
                    "mito/{sample_id}/{sample_id}_sv.vcf.gz.tbi",
                    "mito/{sample_id}/{sample_id}_sv.vcf.gz.csi",
                ],
                "sv_annotation_tsv": [
                    "mito/annotation/{sample_id}/{sample_id}_sv_annot.txt",
                    "mito/{sample_id}/annotation/{sample_id}_sv_annot.txt",
                ],
            },
            "alignments": {
                "file": [
                    "bams/{sample_id}.cram",
                    "bams/{sample_id}.bam",
                    "alignments/{sample_id}.cram",
                    "alignments/{sample_id}.bam",
                    "{sample_id}.cram",
                    "{sample_id}.bam",
                ],
                "index": [
                    "bams/{sample_id}.cram.crai",
                    "bams/{sample_id}.bam.bai",
                    "alignments/{sample_id}.cram.crai",
                    "alignments/{sample_id}.bam.bai",
                    "{sample_id}.cram.crai",
                    "{sample_id}.bam.bai",
                ],
            },
            "qc": {
                # MultiQC first: the pipeline is moving from per-sample NanoPlot to a
                # MultiQC report, and whichever exists is the one to link.
                "report": [
                    "qc/multiqc/{sample_id}/multiqc_report.html",
                    "qc/multiqc/multiqc_report.html",
                    "qc/nanoplot/{sample_id}/{sample_id}NanoPlot-report.html",
                    "qc/nanoplot/{sample_id}/NanoPlot-report.html",
                    "qc/{sample_id}/*report*.html",
                ],
                "read_stats": [
                    "qc/nanoplot/{sample_id}/{sample_id}NanoStats.txt",
                    "qc/nanoplot/{sample_id}/NanoStats.txt",
                ],
                "depth_summary": [
                    "qc/depth/{sample_id}/{sample_id}.mosdepth.summary.txt",
                    "qc/mosdepth/{sample_id}/{sample_id}.mosdepth.summary.txt",
                ],
                "depth_regions": [
                    "qc/depth/{sample_id}/{sample_id}.regions.bed.gz",
                    "qc/mosdepth/{sample_id}/{sample_id}.regions.bed.gz",
                ],
                "depth_global_dist": [
                    "qc/depth/{sample_id}/{sample_id}.mosdepth.global.dist.txt",
                    "qc/mosdepth/{sample_id}/{sample_id}.mosdepth.global.dist.txt",
                ],
            },
            # Family-level Nextflow run record (traceability, not variant data).
            "pipeline_info": {
                "params": ["pipeline_info/params_*.json", "pipeline_info/params.json"],
                "versions": [
                    "pipeline_info/software_versions.yaml",
                    "pipeline_info/software_versions.yml",
                ],
                "execution_trace": ["pipeline_info/execution_trace_*.txt"],
                "execution_report": ["pipeline_info/execution_report_*.html"],
            },
        },
    }
}


def _format_pattern(pattern: str, *, family_id: str, sample_id: str | None = None) -> str:
    return pattern.format(family_id=family_id, sample_id=sample_id or "")


_GLOB_METACHARACTERS = ("*", "?", "[")

_NATURAL_SORT_SPLIT = re.compile(r"(\d+)")


def _has_glob(value: str) -> bool:
    return any(character in value for character in _GLOB_METACHARACTERS)


def _natural_sort_key(value: str) -> tuple[tuple[int, int | str], ...]:
    """Sort key that orders embedded numbers numerically, so ``needLR.10.0`` sorts
    after ``needLR.4.0`` (plain lexicographic order would put ``10`` first).
    Digit runs sort before text runs at the same position so the tuple elements
    stay mutually comparable."""
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.lower())
        for part in _NATURAL_SORT_SPLIT.split(value)
        if part != ""
    )


def _glob_candidate_paths(root: Path, pattern: str) -> list[str]:
    """Package-relative paths of existing files matching a glob pattern, newest-looking
    last (natural sort).

    Pipeline outputs embed a tool version in the filename (``…needLR.4.0.vcf.gz``) or a
    directory suffix, so a literal candidate path cannot match them. Every hit is put
    back through ``_resolve_package_path`` so the containment guard still applies: a
    match that resolves outside the package root (via a symlink) is dropped rather than
    returned.
    """
    normalized = pattern.strip().lstrip("/")
    if not normalized or ".." in Path(normalized).parts:
        return []
    root_resolved = root.resolve()
    try:
        hits = list(root_resolved.glob(normalized))
    except (OSError, ValueError, NotImplementedError, IndexError):
        return []
    matches: list[str] = []
    for hit in hits:
        try:
            value = str(hit.relative_to(root_resolved))
        except ValueError:
            continue
        try:
            resolved = _resolve_package_path(root_resolved, value)
        except HTTPException:
            continue
        if resolved is None or not resolved.is_file():
            continue
        matches.append(value)
    return sorted(matches, key=_natural_sort_key)


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
        if _has_glob(value):
            # Only the discovery scanner understands globs; a written manifest always
            # holds the concrete path a glob resolved to, so validation and import keep
            # treating manifest values as literal paths.
            matches = _glob_candidate_paths(root, value)
            if matches:
                return matches[-1], True
            continue
        path = _resolve_package_path(root, value)
        if path is not None and path.is_file():
            return value, True
    # Nothing matched: fall back to a literal candidate for the "expected path" display
    # so a glob pattern is never written into a manifest.
    fallback = next((value for value in rendered if not _has_glob(value)), rendered[0])
    return fallback, False


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


# Manifest keys that configure a dataset rather than pointing at a file, so the
# availability scan must not try to stat them.
_NON_PATH_MANIFEST_KEYS = frozenset(
    {
        "enabled",
        "per_sample",
        "source_format",
        "source",
        "format",
        "sample_name",
        "vcf_sample",
        "exclude_filters",
        "track_type",
    }
)


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
            # Resolve through the containment guard rather than probing `root / value`
            # directly: a manifest declaring `../../etc/passwd` must not have its
            # existence stat'd outside the package root. An escaping path resolves to
            # None here and is reported as not-present.
            try:
                resolved = _resolve_package_path(root, value)
            except HTTPException:
                resolved = None
            files.append(
                FamilyManifestFileAvailability(
                    role=role,
                    path=value,
                    exists=bool(resolved is not None and resolved.exists()),
                    sample_id=sample_id,
                )
            )

    if isinstance(config, dict):
        enabled = bool(config.get("enabled", True))
        # Any string-valued key is a declared path. Enumerating roles explicitly used
        # to silently ignore new ones (a manifest's `params:` or `report:` showed the
        # dataset as having no files), so scan every scalar entry instead and skip the
        # handful of keys that are configuration rather than paths.
        for role, value in config.items():
            if role in _NON_PATH_MANIFEST_KEYS:
                continue
            _add(role, value)
        per_sample = config.get("per_sample")
        if isinstance(per_sample, dict):
            for sample_id, entry in per_sample.items():
                samples.append(str(sample_id))
                if isinstance(entry, dict):
                    for role, value in entry.items():
                        if role in _NON_PATH_MANIFEST_KEYS:
                            continue
                        _add(role, value, sample_id=str(sample_id))
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


def _single_sample_id(sample_ids: list[str]) -> str | None:
    """The one sample id when the family has exactly one, else None.

    Family-level datasets (snv, sv_needlr) are one VCF holding every family member's
    genotypes. The long-read pipeline only writes *per-sample* annotated callsets, so
    for a single-sample family the sample's file is the family callset and the
    ``{sample_id}`` patterns may be rendered. With two or more samples that
    substitution would be wrong (it would pick one member's calls and present them as
    the family's), so those patterns are skipped and the package is reported as
    missing its family VCF.
    """
    return sample_ids[0] if len(sample_ids) == 1 else None


def _patterns_for_family_scope(patterns: list[str], sample_id: str | None) -> list[str]:
    """Drop ``{sample_id}`` patterns when there is no single sample to render them
    with -- otherwise they would render an empty segment (``snv//annotation/...``)
    and could match an unrelated file."""
    if sample_id is not None:
        return patterns
    return [pattern for pattern in patterns if "{sample_id}" not in pattern]


def _family_dataset_availability(
    *,
    root: Path,
    family_id: str,
    dataset_type: str,
    patterns: dict[str, list[str]],
    sample_ids: list[str] | None = None,
) -> tuple[FamilyManifestDatasetAvailability, dict[str, Any]]:
    sample_id = _single_sample_id(sample_ids or [])
    vcf_value, vcf_exists = _choose_candidate_path(
        root,
        _patterns_for_family_scope(patterns["family_vcf"], sample_id),
        family_id=family_id,
        sample_id=sample_id,
    )
    index_value, index_exists = _choose_candidate_path(
        root,
        _patterns_for_family_scope(patterns["index"], sample_id),
        family_id=family_id,
        sample_id=sample_id,
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
            _patterns_for_family_scope(patterns["annotation_tsv"], sample_id),
            family_id=family_id,
            sample_id=sample_id,
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


def _optional_role_dataset_availability(
    *,
    root: Path,
    family_id: str,
    sample_ids: list[str],
    dataset_type: str,
    patterns: dict[str, list[str]],
    required_roles: tuple[str, ...],
    optional_roles: tuple[str, ...],
) -> tuple[FamilyManifestDatasetAvailability, dict[str, Any]]:
    """Per-sample availability where only some roles are required.

    The long-read datasets pair a primary artefact with optional companions (an index,
    an annotation TSV, a coverage bedgraph). ``_per_sample_dataset_availability``
    treats every role as required and drops a sample entirely if one is missing, which
    would discard a whole CNV callset just because the bigwig was not produced. Here a
    sample counts as available once its required roles resolve, and each optional role
    is recorded only when it actually exists.
    """
    files: list[FamilyManifestFileAvailability] = []
    per_sample: dict[str, dict[str, str]] = {}
    complete_samples: list[str] = []
    for sample_id in sample_ids:
        entry: dict[str, str] = {}
        sample_complete = True
        for role in required_roles:
            path_value, exists = _choose_candidate_path(
                root,
                patterns[role],
                family_id=family_id,
                sample_id=sample_id,
            )
            files.append(
                _availability_file(root=root, role=role, path_value=path_value, sample_id=sample_id)
            )
            sample_complete = sample_complete and exists
            entry[role] = path_value
        for role in optional_roles:
            if role not in patterns:
                continue
            path_value, exists = _choose_candidate_path(
                root,
                patterns[role],
                family_id=family_id,
                sample_id=sample_id,
            )
            if not exists:
                continue
            files.append(
                _availability_file(root=root, role=role, path_value=path_value, sample_id=sample_id)
            )
            entry[role] = path_value
        # With no required roles (qc), any optional artefact makes the sample usable.
        if not required_roles:
            sample_complete = bool(entry)
        if sample_complete and entry:
            complete_samples.append(sample_id)
            per_sample[sample_id] = entry
    complete = bool(complete_samples)
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
                else f"No {dataset_type} files were found"
            ),
        ),
        {"enabled": complete, "per_sample": per_sample},
    )


def _family_role_dataset_availability(
    *,
    root: Path,
    family_id: str,
    dataset_type: str,
    patterns: dict[str, list[str]],
) -> tuple[FamilyManifestDatasetAvailability, dict[str, Any]]:
    """Family-level dataset whose roles are independent optional files (pipeline_info)."""
    files: list[FamilyManifestFileAvailability] = []
    block: dict[str, Any] = {}
    for role, candidates in patterns.items():
        path_value, exists = _choose_candidate_path(root, candidates, family_id=family_id)
        if not exists:
            continue
        files.append(_availability_file(root=root, role=role, path_value=path_value))
        block[role] = path_value
    complete = bool(block)
    return (
        FamilyManifestDatasetAvailability(
            dataset_type=dataset_type,
            enabled=complete,
            complete=complete,
            files=files,
            message="Available" if complete else f"No {dataset_type} files were found",
        ),
        {"enabled": complete, **block},
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
            sample_ids=sample_ids,
        )
        if not item.complete and dataset_type == "repeats_trgt":
            # No joint TRGT VCF: fall back to the pipeline's per-sample repeat calls.
            sample_item, sample_block = _optional_role_dataset_availability(
                root=root,
                family_id=family_id,
                sample_ids=sample_ids,
                dataset_type=dataset_type,
                patterns={
                    "file": scheme[dataset_type]["file"],
                    "index": scheme[dataset_type]["sample_index"],
                },
                required_roles=("file",),
                optional_roles=("index",),
            )
            if sample_item.complete:
                item, block = sample_item, sample_block
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

    # Long-read datasets: primary artefact required, companions recorded when present.
    long_read_roles: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
        "cnv": (("vcf",), ("index", "copy_number_bedgraph", "depth_bigwig", "summary_html")),
        "mito": (("vcf",), ("index", "annotation_tsv", "sv_vcf", "sv_index", "sv_annotation_tsv")),
        "alignments": (("file",), ("index",)),
        "qc": ((), ("report", "read_stats", "depth_summary", "depth_regions", "depth_global_dist")),
    }
    for dataset_type, (required_roles, optional_roles) in long_read_roles.items():
        item, block = _optional_role_dataset_availability(
            root=root,
            family_id=family_id,
            sample_ids=sample_ids,
            dataset_type=dataset_type,
            patterns=scheme[dataset_type],
            required_roles=required_roles,
            optional_roles=optional_roles,
        )
        availability.append(item)
        datasets[dataset_type] = block

    item, block = _family_role_dataset_availability(
        root=root,
        family_id=family_id,
        dataset_type="pipeline_info",
        patterns=scheme["pipeline_info"],
    )
    availability.append(item)
    datasets["pipeline_info"] = block

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
