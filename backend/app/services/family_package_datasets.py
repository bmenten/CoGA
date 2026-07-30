from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from functools import partial
import json
from math import log2
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas import (
    FamilyImportDatasetSummary,
)
from .bed_service import upload_bed_data
from .clickhouse_interval_tracks import (
    count_interval_track_source_rows,
    delete_interval_track_sources,
    delete_interval_tracks,
)
from .clickhouse_variant_storage import (
    count_family_small_variants,
    count_family_structural_variants,
    delete_family_small_variants,
    replace_family_structural_variants,
)
from .family_metadata_context import (
    FamilyMetadataContext,
    SampleMetadataContext,
)
from .hpo_service import (
    import_family_hpo_annotations,
)
from .repeat_expansion_pg import (
    clear_sample_repeat_expansions,
    decode_repeat_upload_text,
    ingest_family_trgt_text,
    ingest_trgt_text,
)
from .upload_safety import read_path_text_bounded
from .variant_upload_service import parse_mutserve_annotation_path, upload_family_small_variant_file

from .family_package_bigwig import autosomal_median, open_bigwig
from .family_package_common import APCAD_PCF_SOURCE, APCAD_PCF_TRACK_TYPE, CNV_SOURCE, DatasetProgressCallback, FamilyPackageBundle, ManifestDataset, MITO_SOURCE, _display_path, _read_package_text, _resolve_package_path, _run_with_periodic_progress, read_vcf_sample_columns, vcf_sample_alias_map  # noqa: F401
from .family_package_manifest import _ped_embryo_sample_ids  # noqa: F401
from .family_package_qc import (  # noqa: F401
    extract_pipeline_versions,
    parse_mosdepth_summary_text,
    parse_nanostats_text,
    parse_pipeline_params,
    record_family_pipeline_metadata as _record_family_pipeline_metadata,
    record_sample_alignment_metadata as _record_sample_alignment_metadata,
    record_sample_mtdna_metadata,
    record_sample_signal_tracks as _record_sample_signal_tracks,
    record_sample_qc_metadata as _record_sample_qc_metadata,
)
from .family_package_registration import _interval_track_count, _paraphase_count, _register_only, _repeat_expansion_count  # noqa: F401
from .family_package_tracks import _delete_sample_interval_source, _import_apcad_track_file, _import_bigwig_interval_track, _import_copy_number_track, _import_pcf_segment_file, _import_wisecondorx_track  # noqa: F401
from .family_package_validation import _manifest_hpo_rows, _pcf_role_path  # noqa: F401
from .family_package_variants import _iter_cnv_structural_records, _iter_needlr_structural_records, _paraphase_rows_for_sample, _replace_sample_paraphase_rows, _update_sv_file_metadata  # noqa: F401


logger = logging.getLogger(__name__)


@asynccontextmanager
async def _local_upload(path: Path):
    handle = path.open("rb")
    upload = UploadFile(file=handle, filename=path.name)
    try:
        yield upload
    finally:
        await upload.close()


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
    extra = dataset.model_extra or {}
    source_format = str(extra.get("source_format") or "auto")
    # The SNV dataset holds primary (directly-called) genotypes — clair3 unless the
    # manifest overrides it. Scope the coexistence checks/cleanup to this source so
    # the imputed glimpse2 callset is never touched by the SNV importer.
    #
    # An explicit source_format is used verbatim: hard-coding "clair3" for anything
    # that is not glimpse2 made the compensating delete below wipe the family's
    # nuclear callset when a differently-sourced dataset failed.
    snv_source = source_format if source_format != "auto" else "clair3"
    # FILTER values whose records carry no variant to review. DeepVariant marks
    # reference blocks RefCall and zero-depth sites NoCall; on a whole-genome long-read
    # callset those are half the file.
    exclude_filters = extra.get("exclude_filters")
    if isinstance(exclude_filters, str):
        exclude_filters = [exclude_filters]
    elif not isinstance(exclude_filters, (list, tuple)):
        exclude_filters = None
    if conflict_mode == "update":
        existing_count = await count_family_small_variants(
            family_context.assembly_name,
            family_context.family_uuid,
            project_ids=family_context.project_ids,
            source=snv_source,
        )
        if existing_count:
            return summary.model_copy(
                update={
                    "status": "skipped",
                    "message": "Skipped SNV import in update mode because small variants already exist for this family",
                    "summary": {"existing": existing_count},
                }
            )
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

    # Callers name the VCF's sample column after their own input file rather than the
    # sample; resolve those to family sample ids up front so the loader does not reject
    # the file outright.
    sample_aliases, unresolved_samples = vcf_sample_alias_map(
        read_vcf_sample_columns(vcf_path),
        set(sample_contexts),
        declared=extra.get("vcf_sample") or extra.get("sample_name"),
    )
    if unresolved_samples:
        raise RuntimeError(
            f"SNV VCF sample column(s) {unresolved_samples} match no sample in the family"
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
                        sample_aliases=sample_aliases,
                        exclude_filters=exclude_filters,
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
                sample_aliases=sample_aliases,
                exclude_filters=exclude_filters,
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
        # The SNV loader only writes its own small-variant source; it never creates
        # haplotype interval tracks (those belong to the glimpse2 loader). Scope the
        # cleanup to this source so a failed SNV import cannot wipe a previously
        # imported glimpse2 callset or its haplotype blocks.
        with suppress(Exception):
            await delete_family_small_variants(
                family_context.assembly_name,
                family_context.family_uuid,
                source=snv_source,
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
            source="glimpse2",
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
        # The glimpse2 loader owns the imputed small-variant source and the haplotype
        # interval tracks, so scope the small-variant cleanup to glimpse2 (leaving the
        # annotated clair3 SNVs intact) while still clearing its own haplotype blocks.
        with suppress(Exception):
            await delete_family_small_variants(
                family_context.assembly_name,
                family_context.family_uuid,
                source="glimpse2",
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

        # bins carry a per-bin ratio (a coverage axis); segments carry the called
        # level. Both go through the shared guard so a re-import owns its whole
        # (track_type, source) pair rather than only the filename it happens to read.
        for role, track_type in (("bins", "coverage"), ("segments", "segments")):
            path = _resolve_package_path(bundle.root, raw_entry.get(role))
            if path is None:
                continue
            sample_results[sample_id][role] = await _import_interval_track_unless_present(
                session,
                sample_context=sample_context,
                track_type=track_type,
                source="wisecondorx",
                conflict_mode=conflict_mode,
                importer=partial(
                    _import_wisecondorx_track,
                    session,
                    sample_context=sample_context,
                    path=path,
                    track_type=track_type,
                    progress=partial(report_track, role),
                ),
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

        role_paths = {
            "bins": raw_entry.get("bins") or raw_entry.get("file"),
            "segments": raw_entry.get("segments"),
        }
        for role, track_type in (("bins", "coverage"), ("segments", "segments")):
            path = _resolve_package_path(bundle.root, role_paths[role])
            if path is None:
                continue
            sample_results[sample_id][role] = await _import_interval_track_unless_present(
                session,
                sample_context=sample_context,
                track_type=track_type,
                source="qdnaseq",
                conflict_mode=conflict_mode,
                importer=partial(
                    _import_copy_number_track,
                    session,
                    sample_context=sample_context,
                    path=path,
                    track_type=track_type,
                    source="qdnaseq",
                    progress=partial(report_track, role),
                ),
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
    # Capture SV provenance into the family's annotation manifest (best-effort;
    # joins the import transaction). The NeedlR SV VCF carries no structured version
    # header lines — its annotation-database releases (GENCODE/OMIM/GenCC/gnomAD/
    # GIAB) live in the ``##INFO`` descriptions — so mine those too.
    from .annotation_manifest_service import merge_vcf_header_provenance
    from .vcf_header_provenance import (
        extract_header_provenance,
        extract_info_description_provenance,
        merge_module_maps,
    )

    sv_lines = text_value.splitlines()
    sv_modules = merge_module_maps(
        extract_header_provenance(sv_lines, modality="sv").as_modules(),
        extract_info_description_provenance(sv_lines),
    )
    await merge_vcf_header_provenance(
        session,
        family_uuid=family_context.family_uuid,
        assembly_id=getattr(family_context, "assembly_id", None),
        modules=sv_modules,
        modality="sv",
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


async def _record_mtdna_sample_metadata(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    annotations: Any,
) -> None:
    """Store the sample's mtDNA haplogroup on the sample record.

    The mtDNA workspace reads ``samples.metadata["mtdna"]["haplogroup"]`` and otherwise
    reports that no haplogroup is available. mutserve assigns haplogroups per *variant*
    (each row lists the Phylotree clades that variant defines), so the sample-level
    haplogroup is taken as the most frequently reported one across the callset.
    """
    if annotations is None or annotations.conn is None:
        return
    counts: dict[str, int] = {}
    for (payload,) in annotations.conn.execute("SELECT annotation_json FROM annotations"):
        try:
            entry = json.loads(payload)
        except json.JSONDecodeError:
            continue
        haplogroup = entry.get("haplogroup")
        if not haplogroup:
            continue
        for token in str(haplogroup).split(","):
            name = token.strip()
            if name:
                counts[name] = counts.get(name, 0) + 1
    if not counts:
        return
    haplogroup = max(counts.items(), key=lambda item: (item[1], item[0]))[0]
    await record_sample_mtdna_metadata(
        session,
        sample_context=sample_context,
        mtdna={"haplogroup": haplogroup, "haplogroup_support": counts[haplogroup]},
    )


def _autosomal_median_depth(path: Path) -> float | None:
    """Autosomal median of a depth bigWig, for normalising it to a log2 ratio.

    Its own pass over the file: the median has to be known before the first row is
    written, and a second streaming pass costs well under a second even for a
    whole-genome track.
    """

    reader = open_bigwig(path)
    try:
        return autosomal_median(reader)
    finally:
        reader.close()


def _log2_ratio_transform(normaliser: float | None) -> Callable[[float], float] | None:
    """Convert read depth to log2(depth / ``normaliser``), or don't convert at all.

    Returns ``None`` when there is no usable normaliser, which leaves the values
    raw rather than inventing a baseline -- a track drawn against a made-up
    reference would look like a genome-wide gain or loss.
    """

    if not normaliser or normaliser <= 0:
        return None

    def transform(value: float) -> float:
        # log2(0) is -inf, which ClickHouse cannot store and no axis can draw. The
        # floor also catches the near-zero depths a smoothed assembly gap leaves
        # behind (0.0005x against a 20x median is -15.3), which would otherwise
        # stretch the plotted range by an order of magnitude to show nothing.
        if value <= 0:
            return _MIN_LOG2_RATIO
        return max(log2(value / normaliser), _MIN_LOG2_RATIO)

    return transform


# A 2^-10 floor: far below any real single-copy loss (-1), so anything at or below
# it reads as "no coverage" rather than as a measurement.
_MIN_LOG2_RATIO = -10.0


async def _import_interval_track_unless_present(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    track_type: str,
    source: str,
    conflict_mode: str,
    importer: Callable[[], Awaitable[dict[str, int]]],
) -> dict[str, Any]:
    """Run ``importer`` unless update mode must leave existing rows alone.

    ``update`` means "add what is missing, touch nothing that is there"; the
    importers themselves always replace their (track_type, source, filename)
    triple, so the guard has to sit outside them. Shared by the three HiFiCNV
    signal tracks, which differ only in what they read and where it lands.

    When the import does run, the whole (track_type, source) pair is cleared
    first rather than just the incoming filename. A source owns a track for a
    sample; a filename-scoped replace silently accumulates rows when the file
    that feeds a track changes. That is not hypothetical -- HiFiCNV's `coverage`
    track used to be fed by the copy-number bedgraph and is now fed by the depth
    bigWig, so a filename-scoped replace would leave the old copy-number rows
    behind to be averaged into the new depth values.
    """

    existing = await _interval_track_count(
        session,
        sample_context=sample_context,
        track_type=track_type,
        source=source,
    )
    if conflict_mode == "update" and existing:
        return {"skipped": True, "existing": existing}
    if existing:
        await _delete_sample_interval_source(
            session,
            sample_context=sample_context,
            track_type=track_type,
            source=source,
        )
    return await importer()


async def _import_cnv_dataset(
    session: AsyncSession,
    *,
    bundle: FamilyPackageBundle,
    dataset: ManifestDataset,
    summary: FamilyImportDatasetSummary,
    family_context: FamilyMetadataContext,
    sample_contexts: dict[str, SampleMetadataContext],
    conflict_mode: str = "overwrite",
) -> FamilyImportDatasetSummary:
    """Import depth-based CNV calls (HiFiCNV) as structural variants, plus the
    caller's per-bin copy-number track."""
    if not family_context.assembly_name:
        return await _register_only(summary, "Registered only; family is not linked to a single assembly")
    if not dataset.per_sample:
        return await _register_only(summary, "Registered only; CNV dataset has no per_sample entries")
    if conflict_mode == "update":
        existing_count = await count_family_structural_variants(
            family_context.assembly_name,
            family_context.family_uuid,
            project_ids=family_context.project_ids,
            source=CNV_SOURCE,
        )
        if existing_count:
            return summary.model_copy(
                update={
                    "status": "skipped",
                    "message": "Skipped CNV import in update mode because CNV calls already exist for this family",
                    "summary": {"existing": existing_count},
                }
            )

    records: list[Any] = []
    sample_results: dict[str, Any] = {}
    for sample_id, raw_entry in dataset.per_sample.items():
        sample_context = sample_contexts.get(sample_id)
        if sample_context is None or not isinstance(raw_entry, dict):
            continue
        vcf_path = _resolve_package_path(bundle.root, raw_entry.get("vcf") or raw_entry.get("file"))
        if vcf_path is None:
            continue
        text_value = _read_package_text(vcf_path)
        sample_records = _iter_cnv_structural_records(
            text_value,
            sample_id=sample_id,
            source=CNV_SOURCE,
        )
        records.extend(sample_records)
        sample_results[sample_id] = {
            "calls": len(sample_records),
            "filename": vcf_path.name,
        }
        # HiFiCNV ships two signal files per sample and they measure different
        # things, so they land on different tracks:
        #
        #   depth.bw          read depth per 2 kb bin  -> `coverage`
        #   copynum.bedgraph  called integer copy number -> `segments`
        #
        # Before this split the bedgraph was the `coverage` track, which put copy
        # number on an axis every other caller uses for depth or log2 ratio -- so
        # a HiFiCNV track could not be compared with the WisecondorX or QDNAseq
        # track stacked beside it.
        bedgraph_path = _resolve_package_path(bundle.root, raw_entry.get("copy_number_bedgraph"))
        if bedgraph_path is not None:
            sample_results[sample_id]["copy_number_track"] = await _import_interval_track_unless_present(
                session,
                sample_context=sample_context,
                track_type="segments",
                source=CNV_SOURCE,
                conflict_mode=conflict_mode,
                importer=partial(
                    _import_copy_number_track,
                    session,
                    sample_context=sample_context,
                    path=bedgraph_path,
                    track_type="segments",
                    source=CNV_SOURCE,
                ),
            )

        depth_path = _resolve_package_path(bundle.root, raw_entry.get("depth_bigwig"))
        if depth_path is not None:
            # Stored as a log2 ratio against the sample's own autosomal median, not as
            # raw depth. The coverage track is drawn on one axis per sample and
            # compared with the WisecondorX and QDNAseq tracks stacked beside it,
            # which are themselves log2 ratios; a 1-copy loss then sits at -1 in all
            # three instead of at a depth that means nothing without knowing the
            # sample's own baseline. The raw bigWig is untouched and is what IGV gets.
            normaliser = await asyncio.to_thread(_autosomal_median_depth, depth_path)
            sample_results[sample_id]["depth_track"] = await _import_interval_track_unless_present(
                session,
                sample_context=sample_context,
                track_type="coverage",
                source=CNV_SOURCE,
                conflict_mode=conflict_mode,
                importer=partial(
                    _import_bigwig_interval_track,
                    session,
                    sample_context=sample_context,
                    path=depth_path,
                    track_type="coverage",
                    source=CNV_SOURCE,
                    # A depth bigWig spans the whole genome, so ~half its bins are
                    # the zero-depth telomeric/centromeric gaps. They plot as a
                    # flat line on the axis and cost ~600k rows per sample.
                    skip_zero=True,
                    value_transform=_log2_ratio_transform(normaliser),
                    # The normaliser is part of what the stored numbers mean, so it is
                    # recorded with the track rather than left to be re-derived.
                    extra_metadata={
                        "normalization": "log2_ratio_to_autosomal_median",
                        "autosomal_median_depth": normaliser,
                    },
                ),
            )

        # Minor allele fraction is BAF-like, so it belongs on the APCAD track the
        # views already draw. It carries no parent-of-origin -- bigWig has nowhere
        # to record one -- hence `und`, which the readers treat as unphased rather
        # than as a missing paternal/maternal call.
        maf_path = _resolve_package_path(bundle.root, raw_entry.get("maf_bigwig"))
        if maf_path is not None:
            sample_results[sample_id]["maf_track"] = await _import_interval_track_unless_present(
                session,
                sample_context=sample_context,
                track_type="apcad",
                source=CNV_SOURCE,
                conflict_mode=conflict_mode,
                importer=partial(
                    _import_bigwig_interval_track,
                    session,
                    sample_context=sample_context,
                    path=maf_path,
                    track_type="apcad",
                    source=CNV_SOURCE,
                    origin="und",
                ),
            )

        # Where the signal files sit, for the browser to stream directly. Recorded
        # rather than re-derived at serve time: HiFiCNV names them after its own run
        # (`HG002.Sample0.depth.bw`, `HG002.HG002.maf.bw`), which no fixed path
        # pattern predicts. Recorded whether or not the file produced ClickHouse
        # rows -- the binned track and the file IGV streams are different artefacts,
        # and the raw depth here is absolute where the binned copy is a log2 ratio.
        signal_tracks = {
            key: _display_path(bundle.root, path)
            for key, path in (
                ("depth_bigwig", depth_path),
                ("maf_bigwig", maf_path),
                ("copy_number_bedgraph", bedgraph_path),
            )
            if path is not None and path.is_file()
        }
        if signal_tracks:
            await _record_sample_signal_tracks(
                session,
                sample_context=sample_context,
                signal_tracks={CNV_SOURCE: signal_tracks},
            )

    if not records:
        return await _register_only(summary, "Registered only; no CNV calls were parsed")
    # Replace only this source: a family can carry NeedlR SVs and HiFiCNV calls at once.
    await replace_family_structural_variants(
        family_context.assembly_name,
        family_context.family_uuid,
        family_context.project_ids,
        records,
        source=CNV_SOURCE,
    )
    await _update_sv_file_metadata(
        session,
        sample_contexts=sample_contexts,
        source=CNV_SOURCE,
        filename=", ".join(
            str(result.get("filename")) for result in sample_results.values() if result.get("filename")
        ),
    )
    return summary.model_copy(
        update={
            "status": "imported",
            "message": "Imported CNV calls into structural variant storage",
            "summary": {"processed": len(records), "source": CNV_SOURCE, "samples": sample_results},
        }
    )


async def _import_mito_dataset(
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
    """Import mitochondrial calls.

    chrM SNVs go into the ordinary small-variant store under a dedicated ``mito``
    source, which is what the existing mtDNA workspace reads (it queries by
    chromosome, not source) and what keeps a nuclear re-import from deleting them.
    The mutserve annotation TSV supplies heteroplasmy and haplogroup context.
    """
    if not family_context.assembly_name:
        return await _register_only(summary, "Registered only; family is not linked to a single assembly")
    if not dataset.per_sample:
        return await _register_only(summary, "Registered only; mito dataset has no per_sample entries")
    if conflict_mode == "update":
        existing_count = await count_family_small_variants(
            family_context.assembly_name,
            family_context.family_uuid,
            project_ids=family_context.project_ids,
            source=MITO_SOURCE,
        )
        if existing_count:
            return summary.model_copy(
                update={
                    "status": "skipped",
                    "message": "Skipped mito import in update mode because mitochondrial variants already exist",
                    "summary": {"existing": existing_count},
                }
            )

    sample_results: dict[str, Any] = {}
    imported_any = False
    for sample_id, raw_entry in dataset.per_sample.items():
        sample_context = sample_contexts.get(sample_id)
        if sample_context is None or not isinstance(raw_entry, dict):
            continue
        vcf_path = _resolve_package_path(bundle.root, raw_entry.get("vcf") or raw_entry.get("file"))
        if vcf_path is None:
            continue
        annotation_path = _resolve_package_path(bundle.root, raw_entry.get("annotation_tsv"))
        mutserve_annotations = (
            parse_mutserve_annotation_path(annotation_path) if annotation_path is not None else None
        )
        aliases, unresolved = vcf_sample_alias_map(
            read_vcf_sample_columns(vcf_path),
            set(sample_contexts),
            declared=raw_entry.get("vcf_sample") or raw_entry.get("sample_name"),
            target_sample_id=sample_id,
        )
        if unresolved:
            raise RuntimeError(
                f"Mito VCF for {sample_id} has sample column(s) {unresolved} that match no family sample"
            )
        try:
            async with _local_upload(vcf_path) as upload:
                result = await upload_family_small_variant_file(
                    session,
                    context=family_context,
                    sample_contexts=sample_contexts,
                    file=upload,
                    overwrite=True,
                    format_hint=MITO_SOURCE,  # type: ignore[arg-type]
                    sample_aliases=aliases,
                    vep_annotations=mutserve_annotations,
                )
        except HTTPException as exc:
            # A run with no chrM variant is a normal outcome, not a dataset failure.
            if exc.status_code == 400 and "No valid small-variant records" in str(exc.detail):
                sample_results[sample_id] = {"inserted": 0, "message": "No chrM variants called"}
                continue
            raise
        imported_any = True
        sample_results[sample_id] = result
        await _record_mtdna_sample_metadata(
            session,
            sample_context=sample_context,
            annotations=mutserve_annotations,
        )

    if not imported_any:
        return summary.model_copy(
            update={
                "status": "imported",
                "message": "No mitochondrial variants were called for this family",
                "summary": sample_results,
            }
        )
    return summary.model_copy(
        update={
            "status": "imported",
            "message": "Imported mitochondrial variants into small-variant storage",
            "summary": sample_results,
        }
    )


async def _import_qc_dataset(
    session: AsyncSession,
    *,
    bundle: FamilyPackageBundle,
    dataset: ManifestDataset,
    summary: FamilyImportDatasetSummary,
    sample_contexts: dict[str, SampleMetadataContext],
    conflict_mode: str = "overwrite",
) -> FamilyImportDatasetSummary:
    """Record sequencing QC.

    The read-level numbers (NanoPlot/NanoStats) and per-chromosome depth (mosdepth)
    are parsed at import and stored on the sample, so the workspace shows them without
    re-reading pipeline output. The rendered HTML report is recorded by path only --
    it is untrusted pipeline output and is never inlined into the application.
    """
    if not dataset.per_sample:
        return await _register_only(summary, "Registered only; QC dataset has no per_sample entries")
    sample_results: dict[str, Any] = {}
    for sample_id, raw_entry in dataset.per_sample.items():
        sample_context = sample_contexts.get(sample_id)
        if sample_context is None or not isinstance(raw_entry, dict):
            continue
        metrics: dict[str, Any] = {}
        read_stats_path = _resolve_package_path(bundle.root, raw_entry.get("read_stats"))
        if read_stats_path is not None and read_stats_path.is_file():
            metrics["reads"] = parse_nanostats_text(
                read_path_text_bounded(read_stats_path, kind="NanoStats")
            )
        depth_summary_path = _resolve_package_path(bundle.root, raw_entry.get("depth_summary"))
        if depth_summary_path is not None and depth_summary_path.is_file():
            metrics["depth"] = parse_mosdepth_summary_text(
                read_path_text_bounded(depth_summary_path, kind="mosdepth summary")
            )
        report_path = _resolve_package_path(bundle.root, raw_entry.get("report"))
        if report_path is not None and report_path.is_file():
            metrics["report"] = _display_path(bundle.root, report_path)
        if not metrics:
            continue
        await _record_sample_qc_metadata(
            session,
            sample_context=sample_context,
            metrics=metrics,
        )
        sample_results[sample_id] = metrics
    if not sample_results:
        return await _register_only(summary, "Registered only; no QC artefacts were readable")
    return summary.model_copy(
        update={
            "status": "imported",
            "message": "Recorded sequencing QC metrics and report location on each sample",
            "summary": sample_results,
        }
    )


async def _import_alignments_dataset(
    session: AsyncSession,
    *,
    bundle: FamilyPackageBundle,
    dataset: ManifestDataset,
    summary: FamilyImportDatasetSummary,
    sample_contexts: dict[str, SampleMetadataContext],
    conflict_mode: str = "overwrite",
) -> FamilyImportDatasetSummary:
    """Record each sample's aligned-reads file so the CRAM/IGV endpoint can find it.

    The alignment itself is never copied or re-read: the package layout puts it under
    ``bams/<sample>.cram``, while the alignment endpoint's convention is
    ``<family>/<sample>.cram``. Recording the package-relative path on the sample lets
    the endpoint resolve either layout.
    """
    if not dataset.per_sample:
        return await _register_only(summary, "Registered only; alignments dataset has no per_sample entries")
    sample_results: dict[str, Any] = {}
    for sample_id, raw_entry in dataset.per_sample.items():
        sample_context = sample_contexts.get(sample_id)
        if sample_context is None or not isinstance(raw_entry, dict):
            continue
        alignment_path = _resolve_package_path(bundle.root, raw_entry.get("file"))
        if alignment_path is None or not alignment_path.is_file():
            continue
        index_path = _resolve_package_path(bundle.root, raw_entry.get("index"))
        entry = {
            "path": _display_path(bundle.root, alignment_path),
            "format": "cram" if alignment_path.name.endswith(".cram") else "bam",
        }
        if index_path is not None and index_path.is_file():
            entry["index_path"] = _display_path(bundle.root, index_path)
        await _record_sample_alignment_metadata(
            session,
            sample_context=sample_context,
            alignment=entry,
        )
        sample_results[sample_id] = entry
    if not sample_results:
        return await _register_only(summary, "Registered only; no alignment files were found")
    return summary.model_copy(
        update={
            "status": "imported",
            "message": "Recorded aligned-read file locations for the genome browser",
            "summary": sample_results,
        }
    )


async def _import_pipeline_info_dataset(
    session: AsyncSession,
    *,
    bundle: FamilyPackageBundle,
    dataset: ManifestDataset,
    summary: FamilyImportDatasetSummary,
    family_context: FamilyMetadataContext,
) -> FamilyImportDatasetSummary:
    """Capture the Nextflow run record into the family's annotation manifest.

    Every tool version behind the callset lives in ``software_versions.yaml``, and the
    run's parameters (reference build, callers, VEP cache, repeat catalogue) in
    ``params_*.json``. Recording them per family is what makes a released report
    traceable back to the exact pipeline that produced its evidence.
    """
    from .annotation_manifest_service import merge_vcf_header_provenance

    extra = dataset.model_extra or {}
    modules: dict[str, Any] = {}
    captured: dict[str, Any] = {}

    versions_path = _resolve_package_path(bundle.root, extra.get("versions"))
    if versions_path is not None and versions_path.is_file():
        modules = extract_pipeline_versions(
            read_path_text_bounded(versions_path, kind="Pipeline versions")
        )
        captured["versions"] = _display_path(bundle.root, versions_path)
        captured["modules"] = sorted(modules)

    params_path = _resolve_package_path(bundle.root, extra.get("params"))
    parameters: dict[str, Any] = {}
    if params_path is not None and params_path.is_file():
        parameters = parse_pipeline_params(
            read_path_text_bounded(params_path, kind="Pipeline params")
        )
        captured["params"] = _display_path(bundle.root, params_path)
        captured["parameters"] = parameters

    if not modules and not parameters:
        return await _register_only(summary, "Registered only; no pipeline run record was readable")

    if modules:
        await merge_vcf_header_provenance(
            session,
            family_uuid=family_context.family_uuid,
            assembly_id=getattr(family_context, "assembly_id", None),
            modules=modules,
            modality="pipeline",
            source="manifest",
        )
    if parameters:
        await _record_family_pipeline_metadata(
            session,
            family_uuid=family_context.family_uuid,
            parameters=parameters,
        )
    return summary.model_copy(
        update={
            "status": "imported",
            "message": "Recorded pipeline tool versions and run parameters for traceability",
            "summary": captured,
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
    if summary.dataset_type == "cnv":
        return await _import_cnv_dataset(
            session,
            bundle=bundle,
            dataset=dataset,
            summary=summary,
            family_context=family_context,
            sample_contexts=sample_contexts,
            conflict_mode=conflict_mode,
        )
    if summary.dataset_type == "mito":
        return await _import_mito_dataset(
            session,
            bundle=bundle,
            dataset=dataset,
            summary=summary,
            family_context=family_context,
            sample_contexts=sample_contexts,
            conflict_mode=conflict_mode,
            progress=progress,
        )
    if summary.dataset_type == "qc":
        return await _import_qc_dataset(
            session,
            bundle=bundle,
            dataset=dataset,
            summary=summary,
            sample_contexts=sample_contexts,
            conflict_mode=conflict_mode,
        )
    if summary.dataset_type == "alignments":
        return await _import_alignments_dataset(
            session,
            bundle=bundle,
            dataset=dataset,
            summary=summary,
            sample_contexts=sample_contexts,
            conflict_mode=conflict_mode,
        )
    if summary.dataset_type == "pipeline_info":
        return await _import_pipeline_info_dataset(
            session,
            bundle=bundle,
            dataset=dataset,
            summary=summary,
            family_context=family_context,
        )
    # A validated, enabled dataset with no importer branch would otherwise report
    # success while importing nothing. Fail loudly instead: adding a dataset type to
    # SUPPORTED_DATASETS without an importer is a bug, not a runtime condition.
    raise RuntimeError(
        f"No importer is registered for dataset type '{summary.dataset_type}'"
    )
