from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings  # noqa: F401  (tests monkeypatch fpi.settings' attributes; shared singleton)
from ..core.postgres import get_postgres_sessionmaker
from ..schemas import (
    FamilyImportDatasetSummary,
    FamilyPackageValidationOut,
)
from .family_metadata_context import (
    build_family_metadata_context,
)
from .metadata_service import CurrentUser, get_current_user_by_email
from . import ped_service
from .bed_service import precompute_family_haplotype_lineage
from .clickhouse_family_snapshot import (
    discard_family_clickhouse_snapshot,
    restore_family_clickhouse_state,
    snapshot_family_clickhouse_state,
)

# Re-exported so existing import paths and orig.<name> attribute reads keep resolving.
from .family_package_common import _run_with_periodic_progress, ManifestDataset, ManifestPhenotypes, PackageManifest, PedMember, ParsedPed, FamilyPackageBundle, PackageExecutionResult, ProgressCallback, DatasetProgressCallback, SUPPORTED_DATASETS, APCAD_PCF_TRACK_TYPE, APCAD_PCF_SOURCE, _HEADER_NORMALIZER, _issue, _resolve_package_path, _display_path, _vcf_index_candidates, _is_uncompressed_vcf, _json_list, _json_dict, _issue_list, _dataset_summary_list, _model_list_json, _metadata_dict, _missing_scalar, _coerce_int, _coerce_finite_float, _normalize_header_key, _read_package_text, _open_package_text, _is_vcf_file, _jsonb_safe, _split_gene_symbols, _parse_vcf_info, _parse_format, _first_info_value  # noqa: F401
from .family_package_source import _staging_root, _authorized_local_roots, _authorized_s3_roots, _load_manifest_dict, _scan_manifest_info, _existing_manifest_dict, scan_family_import_packages, _ensure_authorized_package_path, _ensure_authorized_s3_source, _stage_s3_package, staged_package_source, staged_package_source_async, _manifest_candidates, _find_manifest, _parse_manifest  # noqa: F401
from .family_package_manifest import _PED_SEX_CODES, _PED_STATUS_VALUES, _PED_NUMERIC_STATUS_VALUES, _PED_ROLE_VALUES, _TRUE_VALUES, _INHERITANCE_MODELS, _normalize_ped_sex, _parse_ped_annotations, _ped_clinical_status, _normalize_ped_status, _ped_numeric_status_values, _ped_role_hint, _ped_carrier_type, _ped_is_carrier, _lookup_normalized_key, _manifest_pgt_source, _manifest_sample_id_list, _normalize_manifest_inheritance_model, _manifest_pgt_metadata, _manifest_carrier_types, _manifest_family_payload, _normalize_manifest_clinical_status, _normalize_manifest_carrier_status, _normalize_manifest_carrier_type, _manifest_member_overrides, _manifest_relationships, _parse_ped_text_strict, _normalize_manifest_samples, _manifest_roi_value, _is_ped_embryo, _ped_embryo_sample_ids, _ped_members_for_import  # noqa: F401
from .family_package_validation import _add_missing_optional_dataset_warnings, _require_file, _validate_vcf_index, _validate_family_vcf_dataset, _sample_entry_mapping, _validate_per_sample_id, _validate_wisecondorx_dataset, _validate_coverage_dataset, _validate_qdnaseq_dataset, _validate_apcad_dataset, _pcf_role_path, _validate_pcf_dataset, _validate_haplotypes_dataset, _validate_paraphase_dataset, _validate_dataset, _manifest_individuals, _hpo_issue_to_validation_issue, _manifest_hpo_rows, _validate_manifest_hpo_annotations, load_validated_family_package, validate_family_package  # noqa: F401
from .family_package_discovery import NAMING_SCHEMES, _format_pattern, _choose_candidate_path, _availability_file, _detect_ped_path, _availability_from_manifest_dataset, _family_dataset_availability, _per_sample_dataset_availability, _qdnaseq_dataset_availability, _apcad_dataset_availability, _pcf_dataset_availability, _haplotypes_dataset_availability, _build_manifest_payload, discover_family_package_manifest, write_family_package_manifest  # noqa: F401
from .family_package_jobs import FAMILY_IMPORT_STALE_HEARTBEAT, _serialize_job, queue_family_import_job, get_family_import_job, list_family_import_jobs, claim_next_family_import_job, _update_job_progress  # noqa: F401
from .family_package_registration import _family_sample_contexts, _fetch_existing_family, existing_family_sample_ids, _dataset_provenance, _sample_provenance, _PROVENANCE_PATH_KEYS, _dataset_top_level_files, _record_package_raw_files, _register_package_provenance, _ROI_REGION_PATTERN, _resolve_manifest_roi, _apply_manifest_roi, _ensure_family_from_ped, _delete_family_shell, _flag_family_import_incomplete, _clear_family_import_incomplete, _enabled_dataset_summaries, _register_only, _normalized_conflict_mode, _execution_metadata, _merge_validation_metadata, _existing_sample_ids, _existing_package_entity_warnings, _interval_track_count, _repeat_expansion_count, _paraphase_count  # noqa: F401
from .family_package_tracks import _header_map, _normalized_header_map, _header_value, _normalized_header_value, _split_delimited_line, _looks_like_interval_header, _first_header_value, _delete_sample_interval_track, _delete_sample_interval_source, _insert_interval_track_rows, _COPY_NUMBER_VALUE_COLUMNS, _COPY_NUMBER_SEGMENT_VALUE_COLUMNS, _COPY_NUMBER_METADATA_COLUMNS, _parse_copy_number_interval_row, _parse_wisecondorx_interval_row, _import_wisecondorx_track, _import_copy_number_track, _APCAD_VALUE_KEYS, _APCAD_ORIGIN_KEYS, _first_mapping_value, _first_finite_from_list, _normalize_origin, _apcad_value, _apcad_origin, _gt_has_alt_allele, _infer_apcad_origin_from_parent_genotypes, _apcad_metadata, _apcad_row, _parse_apcad_interval_row, _import_apcad_interval_file, _import_apcad_vcf_file, _import_apcad_track_file, _pcf_value, _parse_pcf_segment_row, _import_pcf_segment_file  # noqa: F401
from .family_package_variants import _needlr_query_sample_id, _needlr_call, _needlr_parent_sample_ids, _iter_needlr_structural_records, _update_sv_file_metadata, _paraphase_rows_for_sample, _replace_sample_paraphase_rows  # noqa: F401
from .family_package_datasets import _local_upload, _import_snv_dataset, _import_haplotypes_dataset, _import_wisecondorx_dataset, _import_qdnaseq_dataset, _import_sv_needlr_dataset, _import_apcad_dataset, _import_coverage_dataset, _import_pcf_dataset, _import_repeats_dataset, _import_paraphase_dataset, _import_phenotypes_dataset, _import_dataset  # noqa: F401

logger = logging.getLogger(__name__)

FAMILY_IMPORT_WORKER_POLL_SECONDS = 2.0


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
    family_context, family_created = await _ensure_family_from_ped(
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

    # Overwrite of a PRE-EXISTING family is delete-then-insert, so a mid-import failure
    # can destroy the family's prior ClickHouse variant/interval rows. Snapshot them
    # first so a failed overwrite is atomically rolled back to the pre-import state
    # (issue #365) instead of only being flagged import-incomplete. Only overwrite
    # is destructive — update mode is skip-if-exists, so nothing prior is lost there
    # and the flag path below is sufficient. Best-effort: if the snapshot can't be
    # taken we degrade to the flag rather than failing the import.
    clickhouse_snapshot = None
    if not family_created and conflict_mode == "overwrite" and family_context.assembly_name:
        try:
            clickhouse_snapshot = await snapshot_family_clickhouse_state(
                family_context.assembly_name, family_context.family_uuid
            )
            logs.append(
                "Snapshotted the family's existing variant data for atomic restore on failure."
            )
        except Exception:  # noqa: BLE001 - degrade to the incomplete flag on snapshot failure
            logger.warning(
                "Failed to snapshot family %s before overwrite; a failed import will fall "
                "back to the import-incomplete flag",
                family_context.family_id,
                exc_info=True,
            )
            clickhouse_snapshot = None

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
    imported_datasets = [dataset.dataset_type for dataset in datasets if dataset.status == "imported"]

    # Fail-clean. A failed import must not leave a silently-partial family behind:
    #   * NEW family, and NOTHING imported -> compensate by deleting the freshly-created
    #     shell (+ its ClickHouse variant & interval rows) so nothing partial survives.
    #   * Failed OVERWRITE of a PRE-EXISTING family (we took a snapshot above) ->
    #     atomically restore the family's ClickHouse variant/interval rows to their
    #     pre-import state, so a destructive delete-then-insert can't leave prior data
    #     mangled. If the restore itself fails, fall back to the incomplete flag.
    #   * Any OTHER failure that left the family in place — a partial success (some
    #     datasets imported, some failed) or a failed update (skip-if-exists, no
    #     snapshot) of a PRE-EXISTING family — is NOT torn down (successfully-imported
    #     datasets are deliberately preserved). Instead the family metadata is flagged
    #     import-incomplete so the partial state is explicit and auditable rather than
    #     silently queryable as if complete.
    #   * In every failed case `error` is set -> the job row ends status='failed'
    #     (never a silent "completed").
    compensated = False
    restored = False
    if failed_datasets and not imported_datasets and family_created:
        await session.rollback()
        await _delete_family_shell(session, family_context)
        await session.commit()
        compensated = True
        logs.append(
            "No dataset imported successfully; rolled back the newly-created family shell."
        )
    elif failed_datasets and clickhouse_snapshot is not None:
        try:
            await restore_family_clickhouse_state(clickhouse_snapshot)
            restored = True
            logs.append(
                "Import failed; atomically restored the family's variant data to its "
                "pre-import state."
            )
        except Exception:  # noqa: BLE001 - restore failed; fall back to the flag
            logger.warning(
                "Failed to restore family %s after a failed overwrite; flagging "
                "import-incomplete",
                family_context.family_id,
                exc_info=True,
            )
            await _flag_family_import_incomplete(
                session,
                family_context,
                failed_datasets=failed_datasets,
                imported_datasets=imported_datasets,
            )
            logs.append(
                "Import failed and the snapshot restore also failed; flagged the family "
                "metadata as import-incomplete."
            )
    elif failed_datasets:
        await _flag_family_import_incomplete(
            session,
            family_context,
            failed_datasets=failed_datasets,
            imported_datasets=imported_datasets,
        )
        logs.append(
            "Import left the family partially populated (some datasets failed); flagged "
            "the family metadata as import-incomplete. Successfully-imported datasets are "
            "kept; re-run the import to complete it."
        )

    # The snapshot has served its purpose (success kept the new data; failure restored
    # the old) -> drop the backup tables. Best-effort so cleanup never fails the import.
    if clickhouse_snapshot is not None:
        await discard_family_clickhouse_snapshot(clickhouse_snapshot)

    if failed_datasets:
        error = "Family package import failed for dataset(s): " + ", ".join(
            sorted(set(failed_datasets))
        )
        logs.append(error)
    else:
        error = None
        logs.append("Family package import completed.")
        # A fully-successful (re)import clears any stale incompleteness flag left by a
        # prior failed update/overwrite so a now-complete family isn't misreported.
        if not compensated and session is not None:
            await _clear_family_import_incomplete(session, family_context)

    # (Re)importing variant data changes the prioritised ranking, but the cache's input
    # hash doesn't cover variant content — drop any cached ranking so it recomputes.
    # Skip when the family shell was just compensated away (nothing to recache), or when
    # a failed overwrite was restored to its pre-import state (variant content unchanged,
    # so any existing cache still matches).
    if not compensated and not restored and not dry_run and session is not None:
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
        completed=error is None,
        error=error,
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
