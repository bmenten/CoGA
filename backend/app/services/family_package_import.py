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

from ..core.postgres import get_postgres_sessionmaker
from ..schemas import (
    FamilyImportDatasetSummary,
    FamilyPackageValidationOut,
)
from .family_metadata_context import (
    FamilyMetadataContext,
    SampleMetadataContext,
    build_family_metadata_context,
)
from .family_variant_write_lock import hold_family_variant_writes
from .metadata_service import get_current_user_by_email
from .access_control import CurrentUser
from . import ped_service
from .bed_service import precompute_family_haplotype_lineage
from .clickhouse_family_snapshot import (
    discard_family_clickhouse_snapshot,
    restore_family_clickhouse_state,
    snapshot_family_clickhouse_state,
)
from .postgres_family_snapshot import (
    restore_family_postgres_state,
    snapshot_family_postgres_state,
)
from .family_package_common import FamilyPackageBundle, PackageExecutionResult, ProgressCallback, _issue, _json_dict
from .family_package_source import package_folder_path, staged_package_source_async
from .family_package_validation import load_validated_family_package
from .family_package_jobs import claim_next_family_import_job, _update_job_progress
from .family_package_registration import (
    _family_sample_contexts,
    _ensure_family_from_ped,
    _delete_family_shell,
    _flag_family_import_incomplete,
    _clear_family_import_incomplete,
    _enabled_dataset_summaries,
    _normalized_conflict_mode,
    _execution_metadata,
    _merge_validation_metadata,
    _existing_package_entity_warnings,
)
from .family_package_datasets import _import_dataset

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
    job_id: str | None = None,
) -> PackageExecutionResult:
    """Run an import, staging the package to a temp dir first when the source is a
    gs:// or s3:// URI (cleaned up afterwards; its alignments stay in the store).
    ``job_id`` is the import job running it, if any: an incomplete-import flag names it."""
    async with staged_package_source_async(folder_path) as staged:
        return await _execute_family_package_import_local(
            session,
            folder_path=staged.root,
            source_uri=staged.source_uri,
            remote_only_files=staged.remote_only_files,
            project_id=project_id,
            dry_run=dry_run,
            user=user,
            requested_family_id=requested_family_id,
            conflict_mode=conflict_mode,
            progress=progress,
            job_id=job_id,
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
    job_id: str | None = None,
    remote_only_files: frozenset[str] = frozenset(),
) -> PackageExecutionResult:
    fallback_ped_text = await db_pedigree_fallback(session, requested_family_id)
    validation, bundle = load_validated_family_package(
        folder_path,
        fallback_ped_text=fallback_ped_text,
        remote_only_files=remote_only_files,
        source_uri=source_uri,
    )
    conflict_mode = _normalized_conflict_mode(conflict_mode)
    request_metadata = _execution_metadata(
        requested_family_id=requested_family_id,
        conflict_mode=conflict_mode,
    )
    validation = _merge_validation_metadata(validation, request_metadata)
    # A package staged from a bucket is named by its source folder: the staging copy
    # is gone once the import ends.
    logs = [f"Validated package path {source_uri or package_folder_path(folder_path)}."]
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

    # One write of the family's variants at a time, and this import is one write from its
    # snapshot to its restore: it commits after its datasets, and a restore puts back the
    # family's rows as they were before the import, so a write that ran in between would
    # be undone. The locks are held for the whole run on a connection of their own; the
    # dataset loaders take none of their own for this family. Taken after the family's
    # registration has committed, so this session holds no lock another writer waits for.
    async with hold_family_variant_writes(
        family_context.family_uuid,
        samples=[sample.sample_uuid for sample in sample_contexts.values()],
    ):
        return await _import_family_datasets(
            session,
            bundle=bundle,
            validation=validation,
            datasets=datasets,
            logs=logs,
            family_context=family_context,
            family_created=family_created,
            sample_contexts=sample_contexts,
            conflict_mode=conflict_mode,
            progress=progress,
            job_id=job_id,
            dry_run=dry_run,
        )


async def _import_family_datasets(
    session: AsyncSession,
    *,
    bundle: FamilyPackageBundle,
    validation: FamilyPackageValidationOut,
    datasets: list[FamilyImportDatasetSummary],
    logs: list[str],
    family_context: FamilyMetadataContext,
    family_created: bool,
    sample_contexts: dict[str, SampleMetadataContext],
    conflict_mode: str,
    progress: ProgressCallback | None,
    job_id: str | None,
    dry_run: bool,
) -> PackageExecutionResult:
    """Import the package's datasets into the registered family, and leave it complete,
    restored to its state before the import, or flagged import-incomplete."""
    # Overwrite of a PRE-EXISTING family is delete-then-insert, so a mid-import failure
    # can destroy the family's prior data. Snapshot it first so a failed overwrite is
    # atomically rolled back to the pre-import state (issue #365) instead of only being
    # flagged import-incomplete. Two stores are captured: the ClickHouse variant/interval
    # rows, and the Postgres rows the dataset loop writes (repeats / paraphase / interval
    # sources / HPO / annotation manifest) — the importers commit those per-dataset, so a
    # later failure would otherwise leave them out of sync with the restored variants.
    # Only overwrite is destructive — update mode is skip-if-exists, so nothing prior is
    # lost and the flag path below suffices. Best-effort: if a snapshot can't be taken we
    # degrade to the flag rather than failing the import.
    clickhouse_snapshot = None
    postgres_snapshot = None
    if not family_created and conflict_mode == "overwrite":
        try:
            postgres_snapshot = await snapshot_family_postgres_state(
                session, family_context.family_uuid
            )
            if family_context.assembly_name:
                clickhouse_snapshot = await snapshot_family_clickhouse_state(
                    family_context.assembly_name, family_context.family_uuid
                )
            logs.append(
                "Snapshotted the family's existing data for atomic restore on failure."
            )
        except Exception:  # noqa: BLE001 - degrade to the incomplete flag on snapshot failure
            logger.warning(
                "Failed to snapshot family %s before overwrite; a failed import will fall "
                "back to the import-incomplete flag",
                family_context.family_id,
                exc_info=True,
            )
            clickhouse_snapshot = None
            postgres_snapshot = None

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
            async def dataset_progress(
                partial_summary: FamilyImportDatasetSummary, *, index: int = index
            ) -> None:
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
    elif failed_datasets and (clickhouse_snapshot is not None or postgres_snapshot is not None):
        try:
            if clickhouse_snapshot is not None:
                await restore_family_clickhouse_state(clickhouse_snapshot)
            if postgres_snapshot is not None:
                await restore_family_postgres_state(session, postgres_snapshot)
            restored = True
            logs.append(
                "Import failed; atomically restored the family's data to its pre-import state."
            )
        except Exception:  # noqa: BLE001 - restore failed; fall back to the flag
            logger.warning(
                "Failed to restore family %s after a failed overwrite; flagging "
                "import-incomplete",
                family_context.family_id,
                exc_info=True,
            )
            # A mid-restore Postgres error leaves the session in a failed transaction;
            # clear it so the flag write below can run.
            with suppress(Exception):
                await session.rollback()
            await _flag_family_import_incomplete(
                session,
                family_context,
                failed_datasets=failed_datasets,
                imported_datasets=imported_datasets,
                job_id=job_id,
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
            job_id=job_id,
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

    # (Re)importing variant data changes the prioritised ranking. The cache's input hash
    # covers the family's storage-level data version, so an outdated ranking is already
    # never served (#509); dropping the rows here just frees entries that can no longer
    # be hit. Skip when the family shell was just compensated away (nothing to recache),
    # or when a failed overwrite was restored to its pre-import state (the content is
    # unchanged; the data version still moves, which costs at most one recompute).
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
                job_id=job_id,
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
