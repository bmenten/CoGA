from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Collection, Mapping
from contextlib import AsyncExitStack, suppress
from datetime import datetime, timedelta, timezone
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
from .family_identifiers import identifier_problem
from .family_metadata_context import (
    FamilyMetadataContext,
    SampleMetadataContext,
    build_family_metadata_context,
)
from .family_variant_write_lock import (
    VariantWriteHoldLost,
    hold_family_variant_writes,
    watch_family_variant_holds,
)
from .hpo_service import PACKAGE_IMPORT, PhenotypeImport
from .metadata_service import get_current_user_by_email
from .access_control import CurrentUser
from . import ped_service
from .bed_service import precompute_family_haplotype_lineage
from .clickhouse_family_snapshot import (
    FamilyRestoreRefused,
    discard_family_clickhouse_snapshot,
    drop_import_backup_tables,
    is_job_owned,
    list_import_backup_tables,
    orphaned_import_backups,
    restore_family_clickhouse_state,
    snapshot_family_clickhouse_state,
)
from .postgres_family_snapshot import (
    restore_family_postgres_state,
    snapshot_family_postgres_state,
)
from .family_package_common import (
    DatasetLeftMissing,
    FamilyPackageBundle,
    FamilyRecordCallback,
    PackageExecutionResult,
    ProgressCallback,
    _issue,
    _json_dict,
    _json_list,
)
from .family_package_source import package_folder_path, staged_package_source_async
from .family_package_validation import load_validated_family_package
from .family_package_jobs import (
    FAMILY_IMPORT_STALE_HEARTBEAT,
    claim_next_family_import_job,
    running_family_import_job_ids,
    _beat_family_import_job,
    _record_job_family,
    _update_job_progress,
)
from .family_package_registration import (
    FailuresLeft,
    ImportMark,
    _FINISHED_DATASET_STATUSES,
    _IMPORTED_DATASET_STATUSES,
    _end_import_failed_before_datasets,
    _mark_family_import_unfinished,
    dataset_scopes,
    _family_sample_contexts,
    _package_family_id,
    _ensure_family_from_ped,
    _delete_family_shell,
    _end_family_import_unfinished,
    _flag_family_import_incomplete,
    _flag_family_restore_failed,
    _clear_family_import_incomplete,
    _mark_family_import_restoring,
    _record_family_import_finished,
    _enabled_dataset_summaries,
    datasets_brought_again,
    imported_dataset_scopes,
    _normalized_conflict_mode,
    _execution_metadata,
    _merge_validation_metadata,
    _existing_package_entity_warnings,
    family_sample_ids,
    is_whole_family_entry,
    pending_datasets,
    scope_with_samples,
    unfinished_entry_writer,
)
from .family_package_datasets import _import_dataset
from .import_progress import DatasetTimer

logger = logging.getLogger(__name__)

FAMILY_IMPORT_WORKER_POLL_SECONDS = 2.0
# How often a running job's heartbeat is written, whatever its import is doing (waiting
# for another writer's locks, copying a snapshot): a heartbeat older than
# FAMILY_IMPORT_STALE_HEARTBEAT means the process running it has stopped, or could not
# write it; such an import is stopped when its heartbeat finds the job ended.
FAMILY_IMPORT_HEARTBEAT_SECONDS = 60.0
# An import whose heartbeat has not reached the database for this long stops itself where it
# is, as a stopped process does: half the stale window, so it has stopped well before a
# worker can take its job for stopped and another write of the family can start. It went on
# for as long as its heartbeat could not be written (SAFE-15).
FAMILY_IMPORT_SELF_STOP = FAMILY_IMPORT_STALE_HEARTBEAT / 2
# How often a running import checks the connection that holds its family's variant-write
# locks: Postgres releases them with a lost connection, and the import went on writing
# beside the next writer, unaware (SAFE-15).
FAMILY_IMPORT_LOCK_CHECK_SECONDS = 10.0
# How long a beat or a lock check may take: one that has not answered by then counts as
# failed, so that a database that does not answer cannot hold the heartbeat up (a lost
# connection can take minutes to say so).
FAMILY_IMPORT_DATABASE_TIMEOUT = 30.0
# Why an import whose variant-write locks were lost was stopped, as its job records it.
LOCKS_LOST_ERROR = (
    "Stopped where it was: the connection that held the family's variant-write locks was "
    "lost, and Postgres released the locks with it, so another write of the family may have "
    "started. Nothing was restored; the family stays marked as not completely imported until "
    "it is imported again with overwrite."
)
# How long a backup table of an import run outside a job is kept: nothing records whether
# that import still runs (drop_orphaned_import_backups).
IMPORT_BACKUP_GRACE = timedelta(days=1)


class FamilyImportNotRecorded(RuntimeError):
    """The import job could not record the family it imports, so the import stopped
    before writing anything of it."""


def _still_unfinished_message(family_id: str, remaining: Mapping[str, Any]) -> str:
    """The log line of an import that completed while the family stays marked by an
    earlier import that stopped part-way (its `import_unfinished` entry)."""
    stopped: list[str] = []
    for _key, entry in sorted(remaining.items()):
        job_id = entry.get("job_id") if isinstance(entry, Mapping) else None
        pending = pending_datasets(entry)
        name = f"import job {job_id}" if isinstance(job_id, str) and job_id else "an import"
        if pending:
            stopped.append(f"{name}, which had not finished {', '.join(sorted(pending))}")
        else:
            stopped.append(f"{name}, which did not record what it had not finished")
    return (
        f"Family {family_id} stays marked import-incomplete: an earlier import stopped "
        f"part-way ({'; '.join(stopped)}), and what it had not finished may be partly "
        "written. This import did not replace it (an update keeps the data already there); "
        "import those datasets again with overwrite to complete the family."
    )


def _still_failed_message(family_id: str, failures: FailuresLeft) -> str:
    """The log line of an import that completed while the family stays flagged by an
    earlier failure it did not import again. A dataset is named with each job that holds
    an error of it still flagged: one that failed for several scopes (callsets, samples)
    can have more than one (SAFE-26). A failure no job records is a single-file upload's
    (SAFE-25), or an import's run outside a job, and is said so (SAFE-18)."""
    if failures.unreadable:
        return (
            f"Family {family_id} stays flagged import-incomplete: its flag is not in the shape "
            "an import writes, so nothing shows what would complete it."
        )
    outside = "outside an import job"
    by_job: dict[str, list[str]] = {}
    for name, latest in sorted(failures.failed.items()):
        for job_id in failures.jobs.get(name) or [latest]:
            names = by_job.setdefault(f"in import job {job_id}" if job_id else outside, [])
            if name not in names:
                names.append(name)
    failed = "; ".join(f"{', '.join(names)} {where}" for where, names in sorted(by_job.items()))
    who = "an earlier import or single-file upload" if outside in by_job else "an earlier import"
    return (
        f"Family {family_id} stays flagged import-incomplete: {who} failed for datasets this "
        "import did not import again, for the callset and samples that failed "
        f"({failed}). Import them again to complete the family."
    )


def _still_not_restored_message(family_id: str, remaining: Mapping[str, Any]) -> str:
    """The log line of an import that completed while the family stays marked by an
    earlier failed overwrite whose restore did not finish (its whole-family entry in
    ``import_unfinished``): what of the family no import, or single-file upload, has
    brought again with overwrite since."""
    restores: list[str] = []
    for _key, entry in sorted(remaining.items()):
        job_id = entry.get("job_id") if isinstance(entry, Mapping) else None
        name = f"import job {job_id}" if isinstance(job_id, str) and job_id else "an import"
        pending = sorted(pending_datasets(entry) or ())
        restores.append(f"{name}, still to import: {', '.join(pending)}" if pending else name)
    return (
        f"Family {family_id} stays marked import-incomplete: the restore of an earlier "
        f"overwrite that failed did not finish ({'; '.join(restores)}), so the family's "
        "data of each of these datasets may be partly written or missing, whichever import "
        "or upload wrote it. Import each of them again with overwrite to complete the "
        "family, and upload again with overwrite what a single-file upload brought: the "
        "whole family, not only the datasets that failed."
    )


def _still_unwritten_message(family_id: str, remaining: Mapping[str, Any]) -> str:
    """The log line of an import that completed while the family stays marked by a
    single-file upload (SAFE-28), or a deletion of the family's data (SAFE-31), that did not
    finish (its mark in ``import_unfinished``): what of it no import, or upload, has brought
    again with overwrite since."""
    writes: list[str] = []
    for key, entry in sorted(remaining.items()):
        name = (
            "a single-file upload"
            if unfinished_entry_writer(key) == "upload"
            else "a deletion of this family's data"
        )
        pending = sorted(pending_datasets(entry) or ())
        writes.append(f"{name}, still to bring again: {', '.join(pending)}" if pending else name)
    return (
        f"Family {family_id} stays marked import-incomplete: an earlier write of its data did "
        f"not finish ({'; '.join(writes)}), so the family's data of these datasets may be "
        "partly written or missing. Upload or import each of them again with overwrite to "
        "complete the family."
    )


def _restore_failed_message(flagged_failed: list[str], whole: list[str] | None) -> str:
    """The log line of an overwrite that failed and whose restore failed part-way: its
    flag names every dataset of the import as failed (SAFE-13), and its entry, kept,
    every dataset of the family (SAFE-16)."""
    marked = (
        "the family stays marked import-incomplete until each dataset it holds has been "
        f"imported again with overwrite ({', '.join(whole)})"
        if whole
        else "the family stays marked import-incomplete until it has been imported again "
        "in full with overwrite"
    )
    return (
        "Import failed and the snapshot restore also failed; flagged the family metadata as "
        "import-incomplete. The restore may have removed the family's rows of any dataset, "
        "whichever import or upload wrote them: the flag names every dataset of this import "
        f"as failed, those that imported too ({', '.join(flagged_failed)}), and {marked}. "
        "Import the whole family again with overwrite, not only the datasets that failed, "
        "and upload again with overwrite what a single-file upload brought: an update skips "
        "a dataset whose rows are there."
    )


# How a dataset's loader ended, as the job's record says it once a restore has failed.
_LOADER_ENDINGS = {
    "imported": "imported",
    "warning": "imported with warnings",
    "registered": "registered its files",
    "skipped": "was skipped",
}


def _record_left_by_restore(datasets: list[FamilyImportDatasetSummary], flagged: Collection[str]) -> None:
    """Record, in the job's dataset table, each dataset of ``flagged`` whose loader ended
    well as failed: after a failed overwrite whose restore failed part-way, the flag names
    every dataset of the import as failed and none as imported, as the restore may have
    removed the rows of any of them. The table then says the same, with how the loader had
    ended (SAFE-18)."""
    for position, dataset in enumerate(datasets):
        if dataset.dataset_type not in flagged or dataset.status == "failed":
            continue
        ending = _LOADER_ENDINGS.get(dataset.status, f"ended {dataset.status}")
        message = (
            f"Its loader {ending}, but the import failed and the restore that followed did not "
            "finish, so its rows may be partly written or missing. Import it again with "
            "overwrite."
        )
        if dataset.message:
            message += f" The loader's message: {dataset.message}"
        datasets[position] = dataset.model_copy(update={"status": "failed", "message": message})


def _datasets_set_out(
    validation: FamilyPackageValidationOut, import_mark: ImportMark | None
) -> list[str]:
    """Every dataset an import set out to import, whatever became of each: the ones its
    dataset loop takes up, which its ``import_unfinished`` entry lists too."""
    names = {summary.dataset_type for summary in _enabled_dataset_summaries(validation)}
    if import_mark is not None:
        names.update(import_mark.datasets)
    return sorted(names)


async def db_pedigree_fallback(
    session: AsyncSession | None,
    requested_family_id: str | None,
) -> str | None:
    """PED text reconstructed from an existing family's database structure, or
    ``None`` when there is no existing family to fall back to. Used so imports
    that target an already-configured family do not require a PED file."""
    requested_family_id = (requested_family_id or "").strip()
    if session is None or identifier_problem(requested_family_id) is not None:
        # No family is stored under such an ID (and a NUL could not even be sent to Postgres).
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
    record_family: FamilyRecordCallback | None = None,
) -> PackageExecutionResult:
    """Run an import, staging the package to a temp dir first when the source is a
    gs:// or s3:// URI (cleaned up afterwards; its alignments stay in the store).
    ``job_id`` is the import job running it, if any: an incomplete-import flag names it.
    ``record_family``, if given, is awaited with the family's identifier before anything
    of the family is written; if it raises, the import writes nothing. The job's runner
    records there the family a report sign-out finds the import by. ``progress`` is
    informational: what it fails to record changes nothing a sign-out reads."""
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
            record_family=record_family,
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
    record_family: FamilyRecordCallback | None = None,
) -> PackageExecutionResult:
    # The existing family the request names, read as every ID is: without the whitespace
    # around it.
    requested_family_id = (requested_family_id or "").strip() or None
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

    # A report sign-out finds a running import by the family its job records, and the
    # pedigree, samples and provenance below are written before the variant-write locks
    # are taken: until then the job is all that keeps a sign-out out (TF-06 H16). So the
    # family is recorded, committed, before anything of it is written, and an import that
    # cannot record it stops here, having written nothing.
    if record_family is not None:
        await record_family(_package_family_id(validation, bundle))
    # The family carries this import's entry in `import_unfinished` from before its first
    # write until the import ends, so an import whose process stops part-way (a restart, a
    # crash, out of memory: nothing below runs then) leaves the family marked, naming what
    # it had not finished. Registration records it, or the import stops having written
    # nothing.
    dataset_types = [summary.dataset_type for summary in _enabled_dataset_summaries(validation)]
    import_mark = ImportMark.begin(
        job_id=job_id,
        datasets=dataset_types,
        scopes=dataset_scopes(bundle, dataset_types),
    )
    logs.append("Registering family metadata and package provenance.")
    try:
        family_context, family_created = await _ensure_family_from_ped(
            session,
            bundle=bundle,
            project_id=project_id,
            user=user,
            validation=validation,
            conflict_mode=conflict_mode,
            import_mark=import_mark,
        )
    except Exception:
        # Failed before its first dataset: if it had marked the family, the mark becomes
        # the import_incomplete flag, naming its datasets as failed (it wrote none).
        await _end_import_failed_before_datasets(
            session, family_id=_package_family_id(validation, bundle), mark=import_mark
        )
        raise
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
    async with AsyncExitStack() as stack:
        try:
            await stack.enter_async_context(
                hold_family_variant_writes(
                    family_context.family_uuid,
                    samples=[sample.sample_uuid for sample in sample_contexts.values()],
                )
            )
            # The entry is written again now that this import holds the family's variant
            # writes, before its first dataset: while it waited, another import of the
            # family may have completed and removed it, its overwrite having replaced what
            # the entry listed. No other import's ending runs while this one holds them.
            await _mark_family_import_unfinished(
                session, family_uuid=family_context.family_uuid, mark=import_mark
            )
        except Exception:
            await _end_import_failed_before_datasets(
                session, family_id=family_context.family_id, mark=import_mark
            )
            raise
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
            import_mark=import_mark,
            user=user,
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
    user: CurrentUser,
    import_mark: ImportMark | None = None,
) -> PackageExecutionResult:
    """Import the package's datasets into the registered family, and leave it complete,
    restored to its state before the import, or flagged import-incomplete. ``user`` is the
    import's, who a compensating deletion of a new family is recorded as made by (SEC-7).

    ``import_mark`` is this import's entry in the family's ``import_unfinished``: each
    dataset it finishes is recorded there as it goes, and the entry is removed when the
    import ends, whichever way, but one: after a restore that failed part-way it stays,
    naming every dataset of the family. If the import's process stops first, the entry
    stays.

    Each change of the family's phenotypes it makes (the package's, a restore putting them
    back, the family it created removed again) is on the family's clinical audit trail as
    made by ``user``, who runs the import."""
    import_key = import_mark.key if import_mark is not None else None
    # The import as the phenotypes' audit events name it: who runs it, the package (by the
    # folder or bucket it was read from, as its log says) and its job.
    phenotype_import = PhenotypeImport(
        user=user,
        via=PACKAGE_IMPORT,
        source=bundle.source_uri or package_folder_path(bundle.root),
        job_id=job_id,
    )
    # What the import records each dataset as replacing (dataset_scopes), from before its
    # first write: each importer is handed its dataset's (an SNV family VCF is stored as
    # the callset its scope names, SAFE-23).
    mark_scopes: Mapping[str, Any] = import_mark.scopes if import_mark is not None else {}
    # The scope a failed dataset is flagged for where its failure may have removed the data
    # of samples it does not name (DatasetLeftMissing): its own, with theirs (SAFE-28).
    failed_scopes: dict[str, Any] = {}
    finished: set[str] = set()
    # The samples of each dataset an update left as they were, their rows being there
    # (``DatasetImportJob.skipped_samples``).
    skipped_samples: dict[str, set[str]] = {}
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
                # Named after this import, so a backup its stopped process leaves is found
                # and dropped (handle_claimed_family_import_job, drop_orphaned_import_backups).
                clickhouse_snapshot = await snapshot_family_clickhouse_state(
                    family_context.assembly_name, family_context.family_uuid, owner=import_key
                )
            logs.append(
                "Snapshotted the family's existing data for atomic restore on failure."
            )
        except Exception:  # degrade to the incomplete flag on snapshot failure
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
        # When the dataset began and ended, and while it runs how far it has read and the
        # time it should still take (import_progress).
        timer = DatasetTimer()
        datasets[index] = datasets[index].model_copy(
            update={"status": "running", "progress": timer.progress}
        )
        if progress is not None:
            await progress(validation, datasets, logs, family_context.family_id)
        try:
            async def dataset_progress(
                partial_summary: FamilyImportDatasetSummary,
                *,
                index: int = index,
                timer: DatasetTimer = timer,
            ) -> None:
                datasets[index] = partial_summary.model_copy(
                    update={"progress": timer.observe(partial_summary.summary)}
                )
                if progress is not None:
                    await progress(validation, datasets, logs, family_context.family_id)

            imported = await _import_dataset(
                session,
                bundle=bundle,
                summary=summary,
                family_context=family_context,
                sample_contexts=sample_contexts,
                conflict_mode=conflict_mode,
                progress=dataset_progress,
                skipped_samples=skipped_samples.setdefault(summary.dataset_type, set()),
                scope=mark_scopes.get(summary.dataset_type),
                phenotype_import=phenotype_import,
            )
            datasets[index] = imported.model_copy(update={"progress": timer.finish()})
            logs.append(f"Dataset {summary.dataset_type}: {datasets[index].status}.")
        except Exception as exc:  # noqa: BLE001 - the dataset is recorded as failed and fails the import
            await session.rollback()
            if isinstance(exc, DatasetLeftMissing):
                failed_scopes[summary.dataset_type] = scope_with_samples(
                    mark_scopes.get(summary.dataset_type), exc.samples
                )
            datasets[index] = summary.model_copy(
                update={
                    "status": "failed",
                    "message": str(exc),
                    "progress": timer.finish(),
                }
            )
            logs.append(f"Dataset {summary.dataset_type} failed: {exc}")
            if progress is not None:
                await progress(validation, datasets, logs, family_context.family_id)
            continue
        # Recorded on the family once the dataset has finished, never before: one this
        # import stops inside, or fails, stays pending in its entry.
        if import_mark is not None and datasets[index].status in _FINISHED_DATASET_STATUSES:
            finished.add(summary.dataset_type)
            await _record_family_import_finished(session, family_context, import_mark, finished)
        if progress is not None:
            await progress(validation, datasets, logs, family_context.family_id)

    failed_datasets = [dataset.dataset_type for dataset in datasets if dataset.status == "failed"]
    # Imported in full, or with warnings (the rows its input got wrong left out, each named in
    # the job's record): either way its data is the family's now, so a new family holding it is
    # kept and flagged, never deleted with it, and the flag names it as imported (SAFE-34).
    imported_datasets = [
        dataset.dataset_type for dataset in datasets if dataset.status in _IMPORTED_DATASET_STATUSES
    ]
    # What each dataset it brought again replaced (dataset_scopes): an earlier import's
    # failure or unfinished entry for the same scope is completed by it. Brought again:
    # imported, imported with warnings, or registered when the package carries nothing for
    # it (SAFE-17). Not the samples an update skipped, nor a dataset it skipped whole
    # (`skipped`): a skip imports nothing again, so it completes nothing (SAFE-20).
    # Only the datasets the loop ran: validation names its own `warning`s.
    ran = {summary.dataset_type for summary in _enabled_dataset_summaries(validation)}
    imported_scopes = imported_dataset_scopes(
        datasets_brought_again(
            ((dataset.dataset_type, dataset.status) for dataset in datasets if dataset.dataset_type in ran),
            mark_scopes,
        ),
        mark_scopes,
        skipped_samples,
    )
    # What the flag records each failed dataset as: its scope, widened where its failure
    # reached the data of samples it does not name.
    flag_scopes: Mapping[str, Any] = {**mark_scopes, **failed_scopes}

    # Fail-clean. A failed import must not leave a silently-partial family behind:
    #   * NEW family, and NOTHING imported -> compensate by deleting the freshly-created
    #     shell (+ its ClickHouse variant & interval rows) so nothing partial survives.
    #   * Failed OVERWRITE of a PRE-EXISTING family (we took a snapshot above) ->
    #     atomically restore the family's ClickHouse variant/interval rows to their
    #     pre-import state, so a destructive delete-then-insert can't leave prior data
    #     mangled. If the restore itself fails, fall back to the incomplete flag. A restore
    #     refused before its first delete (a backup table was gone) deleted nothing: the
    #     flag names what failed and what imported, as for a partial success. One that
    #     failed in any other way may have deleted rows of any dataset: the flag names every
    #     dataset this import set out to import as failed, and none as imported, and the
    #     import's `import_unfinished` entry stays, naming every dataset of the family.
    #   * Any OTHER failure that left the family in place — a partial success (some
    #     datasets imported, some failed) or a failed update (skip-if-exists, no
    #     snapshot) of a PRE-EXISTING family — is NOT torn down (successfully-imported
    #     datasets are deliberately preserved). Instead the family metadata is flagged
    #     import-incomplete so the partial state is explicit and auditable rather than
    #     silently queryable as if complete.
    #   * In every failed case `error` is set -> the job row ends status='failed'
    #     (never a silent "completed").
    # Whichever way it ends, the import's `import_unfinished` entry goes -- with the family
    # (compensated), once the family is put back (restored), or with the flag or its clear,
    # in one statement -- except after a restore that failed part-way. Only that, and an
    # import whose process stops before this, leave it.
    compensated = False
    restored = False
    if failed_datasets and not imported_datasets and family_created:
        await session.rollback()
        # Every dataset counts as pending again until the family is gone: a stop in the
        # middle leaves a family part-removed.
        if import_mark is not None:
            await _record_family_import_finished(session, family_context, import_mark, ())
        await _delete_family_shell(session, family_context, user, phenotype_import=phenotype_import)
        await session.commit()
        compensated = True
        logs.append(
            "No dataset imported successfully; rolled back the newly-created family shell."
        )
    elif failed_datasets and (clickhouse_snapshot is not None or postgres_snapshot is not None):
        # The restore rewrites every table's rows of the family, table by table, whichever
        # import wrote them: until it has put the family back, every dataset of the family
        # counts as pending, this import's finished ones too and those of other imports
        # (SAFE-16). If the process stops in the restore, the entry says so.
        if import_mark is not None:
            await _mark_family_import_restoring(
                session, family_context, import_mark, family_samples=family_sample_ids(bundle)
            )
        try:
            if clickhouse_snapshot is not None:
                await restore_family_clickhouse_state(clickhouse_snapshot)
            if postgres_snapshot is not None:
                await restore_family_postgres_state(
                    session,
                    postgres_snapshot,
                    family_identifier=family_context.family_id,
                    phenotype_import=phenotype_import,
                )
            restored = True
            logs.append(
                "Import failed; atomically restored the family's data to its pre-import state."
            )
        except Exception as exc:  # restore failed; fall back to the flag
            # A restore refused for a missing backup table deleted nothing (and the Postgres
            # restore, which follows it, did not run): the family holds what this import
            # left, so what failed did fail and what it imported did import. Any other
            # failure may have come after one of the restore's deletes, which removes the
            # family's rows from a table whichever dataset wrote them: any dataset of this
            # import may now be missing or part-written, the ones it imported as much as
            # the one that failed and the ones it only registered or skipped. So the flag
            # names every dataset this import set out to import as failed, and none as
            # imported (the owner's decision, 2026-10-09): the family stays flagged until an
            # import has imported each of them again, and none counts as imported again for
            # an earlier failure. Those deletes also remove the rows of datasets this import
            # did not carry (another small-variant source, another package's SV calls),
            # which the flag cannot name: so the import's `import_unfinished` entry stays,
            # naming every dataset the family holds, and the gate holds until each has been
            # imported again with overwrite -- the whole family, not only what failed (the
            # owner's decision, 2026-10-13; SAFE-16).
            untouched = isinstance(exc, FamilyRestoreRefused)
            logger.warning(
                "Failed to restore family %s after a failed overwrite (%s); flagging "
                "import-incomplete",
                family_context.family_id,
                "nothing was deleted" if untouched else "rows may have been deleted",
                exc_info=True,
            )
            # A mid-restore Postgres error leaves the session in a failed transaction;
            # clear it so the flag write below can run.
            with suppress(Exception):
                await session.rollback()
            if untouched:
                await _flag_family_import_incomplete(
                    session,
                    family_context,
                    failed_datasets=failed_datasets,
                    imported_datasets=imported_datasets,
                    job_id=job_id,
                    import_key=import_key,
                    scopes=flag_scopes,
                    imported_scopes=imported_scopes,
                )
                logs.append(
                    "Import failed, and the family could not be put back: a table of its "
                    "backup was missing, so the restore deleted nothing. Flagged the family "
                    "metadata as import-incomplete; the datasets that imported are kept."
                )
            else:
                flagged_failed = sorted({*failed_datasets, *_datasets_set_out(validation, import_mark)})
                # The job's record says what the flag says: each dataset failed, the ones
                # whose loader ended well too (SAFE-18).
                _record_left_by_restore(datasets, flagged_failed)
                failed_datasets = list(flagged_failed)
                whole: list[str] | None = None
                if import_mark is not None:
                    whole = await _flag_family_restore_failed(
                        session,
                        family_context,
                        failed_datasets=flagged_failed,
                        job_id=job_id,
                        mark=import_mark,
                        scopes=flag_scopes,
                        family_samples=family_sample_ids(bundle),
                    )
                else:  # an import outside the bookkeeping: there is no entry to keep
                    await _flag_family_import_incomplete(
                        session,
                        family_context,
                        failed_datasets=flagged_failed,
                        imported_datasets=[],
                        job_id=job_id,
                        scopes=flag_scopes,
                    )
                logs.append(_restore_failed_message(flagged_failed, whole))
    elif failed_datasets:
        await _flag_family_import_incomplete(
            session,
            family_context,
            failed_datasets=failed_datasets,
            imported_datasets=imported_datasets,
            job_id=job_id,
            import_key=import_key,
            scopes=flag_scopes,
            imported_scopes=imported_scopes,
        )
        logs.append(
            "Import left the family partially populated (some datasets failed); flagged "
            "the family metadata as import-incomplete. Successfully-imported datasets are "
            "kept; re-run the import to complete it."
        )

    # Put back as it was before the import, so the import's entry goes; an earlier
    # import's entry stays, with the data it describes.
    if restored and import_key is not None:
        await _end_family_import_unfinished(session, family_context, import_key=import_key)

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
        # A fully-successful (re)import removes its own unfinished-import entry, and what
        # of the family's import-incomplete flag it imported again: a failed dataset stays
        # flagged until an import imports it again. An earlier import that stopped
        # part-way keeps its entry unless this one imported again, replacing them, the
        # datasets it had not finished: only an overwrite replaces what is there. The
        # entry a restore that did not finish left loses what this one replaced, and goes
        # once the whole family has been imported again so.
        if not compensated:
            left = await _clear_family_import_incomplete(
                session,
                family_context,
                import_key=import_key,
                rewritten=imported_scopes if conflict_mode == "overwrite" else {},
                imported=imported_scopes,
            )
            if left is None:
                logs.append(
                    "The family's import-incomplete marks could not be cleared, so it stays "
                    "marked import-incomplete; an import that completes clears them."
                )
            else:
                if left.failures.failed or left.failures.unreadable:
                    logs.append(_still_failed_message(family_context.family_id, left.failures))
                # A single-file upload's mark, or a deletion's, is said apart from an
                # import's entry (SAFE-28).
                writes = {
                    key: entry
                    for key, entry in left.unfinished.items()
                    if unfinished_entry_writer(key) != "import"
                }
                stopped = {
                    key: entry
                    for key, entry in left.unfinished.items()
                    if key not in writes and not is_whole_family_entry(entry)
                }
                not_restored = {
                    key: entry
                    for key, entry in left.unfinished.items()
                    if key not in writes and is_whole_family_entry(entry)
                }
                if stopped:
                    logs.append(_still_unfinished_message(family_context.family_id, stopped))
                if not_restored:
                    logs.append(_still_not_restored_message(family_context.family_id, not_restored))
                if writes:
                    logs.append(_still_unwritten_message(family_context.family_id, writes))

    # (Re)importing variant data changes the prioritised ranking. The cache's input hash
    # covers the family's storage-level data version, so an outdated ranking is already
    # never served (#509); dropping the rows here just frees entries that can no longer
    # be hit. Skip when the family shell was just compensated away (nothing to recache),
    # or when a failed overwrite was restored to its pre-import state (the content is
    # unchanged; the data version still moves, which costs at most one recompute).
    if not compensated and not restored:
        from .variant_ranking_cache import clear_family_ranking_cache
        from .sv_gene_index_service import clear_family_sv_gene_index

        try:
            await clear_family_ranking_cache(session, family_context.family_uuid)
            # The SV→gene index (the small-variant "also hit by an SV" flag) depends on the
            # imported SVs — drop it so it rebuilds lazily after a re-import.
            await clear_family_sv_gene_index(session, family_context.family_uuid)
        except Exception:  # best-effort cache invalidation
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


def _log_unrecorded_job_end(job_id: str, outcome: str) -> None:
    """Say in the log how an import ended that its job could not record: the update matched
    no row. Names the job and the outcome only, nothing of the import."""
    logger.warning(
        "Family package import job %s ended %s, but its job could not record it: the update "
        "matched no row, as the job is no longer this worker's (another worker ended it as "
        "interrupted, or claimed it again). The job keeps the record it has.",
        job_id,
        outcome,
    )


async def _end_job_after_lost_locks(
    session_factory: Callable[[], Any], *, job_id: str, worker_id: str
) -> None:
    """End the job of an import stopped where it was because the connection holding its
    family's variant-write locks was lost: failed, with why, and its family left as a stopped
    process leaves it (its ``import_unfinished`` entry stays). Nothing is restored, since
    another write of the family may have run since the locks went, and the backups its
    import made are dropped, as those of an interrupted job are. An end the job cannot record
    is said in the log; a worker ends the job once its heartbeat is stale."""
    logger.warning(
        "Family package import job %s was stopped where it was: the connection that held its "
        "family's variant-write locks was lost, so another write of the family may have started. "
        "Nothing was restored, and the family stays marked as not completely imported.",
        job_id,
    )
    try:
        async with session_factory() as end_session:
            recorded = await _update_job_progress(
                end_session,
                job_id=job_id,
                worker_id=worker_id,
                status="failed",
                error=LOCKS_LOST_ERROR,
                completed=True,
            )
    except Exception:
        logger.warning("Could not record the end of import job %s", job_id, exc_info=True)
        recorded = False
    if not recorded:
        _log_unrecorded_job_end(job_id, "failed")
        return
    try:
        await drop_import_backup_tables(await list_import_backup_tables(owner=job_id))
    except Exception:  # left for the startup sweep (drop_orphaned_import_backups)
        logger.warning("Could not drop the backup tables of import job %s", job_id, exc_info=True)


async def run_family_import_job(
    *,
    job_id: str,
    worker_id: str,
) -> None:
    """Run a claimed job's import while its heartbeat is written, and record how it ended.

    When the heartbeat finds the job no longer this worker's -- it went stale, as the
    process could not write it for FAMILY_IMPORT_STALE_HEARTBEAT, and another worker ended
    the job as interrupted or claimed it again -- it stops the import where it is, as a
    stopped process would be: none of the import's fail-clean runs and it writes nothing
    more, so its family keeps the import's ``import_unfinished`` entry, as the job's
    "Interrupted" record says, and the variant-write locks go with their connection. A
    progress or final update of the job that matches no row is said in the log.

    The heartbeat stops the import the same way, by itself, when its beats have not reached
    the database for FAMILY_IMPORT_SELF_STOP, before a worker can take the job for stopped,
    and when the connection holding the family's variant-write locks is gone: the job of
    the latter is ended failed (``LOCKS_LOST_ERROR``), and nothing is restored, since
    another write of the family may have run once the locks went (SAFE-15).
    """
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
                    metadata,
                    logs
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
        # A job claimed again after its worker stopped keeps what its earlier attempt
        # logged: this run's lines follow them rather than replace them.
        earlier_logs = [str(line) for line in _json_list(job_row.get("logs"))]
        # Set by the heartbeat once it has found the job no longer this worker's, and by
        # the import once it has ended and only its end is left to record.
        job_lost = asyncio.Event()
        ending = asyncio.Event()
        # Set by the heartbeat when it stops the import itself: its beats have not reached
        # the database for FAMILY_IMPORT_SELF_STOP, or the connection holding the family's
        # variant-write locks is gone (SAFE-15).
        beat_lost = asyncio.Event()
        locks_lost = asyncio.Event()
        loop = asyncio.get_running_loop()

        async def keep_alive(run: asyncio.Task[None], check_holds: Callable[[], Awaitable[None]]) -> None:
            # The heartbeat, whatever the import is doing (waiting for another writer's
            # locks, copying a snapshot): a stale one means this process has stopped or
            # cannot write it, and the worker that finds it so ends the job or runs it
            # again. A beat that fails is logged and tried again; once none has reached the
            # database for FAMILY_IMPORT_SELF_STOP, the import stops itself where it is,
            # before a worker can take the job for stopped. One that matches no row finds
            # the job no longer this worker's: the import, which may have gone on meanwhile,
            # is stopped where it is -- unless it has ended, and only its end is left to
            # record (an end the job cannot record is said in the log). Between beats, every
            # FAMILY_IMPORT_LOCK_CHECK_SECONDS, the connection holding the family's
            # variant-write locks is checked, and the import stopped where it is when it is
            # gone.
            tick = min(FAMILY_IMPORT_HEARTBEAT_SECONDS, FAMILY_IMPORT_LOCK_CHECK_SECONDS)
            last_beat = loop.time()
            next_beat = last_beat + FAMILY_IMPORT_HEARTBEAT_SECONDS
            while True:
                await asyncio.sleep(tick)
                try:
                    await asyncio.wait_for(check_holds(), FAMILY_IMPORT_DATABASE_TIMEOUT)
                except (VariantWriteHoldLost, TimeoutError):
                    logger.warning(
                        "Family package import job %s lost the connection that held its "
                        "family's variant-write locks",
                        job_id,
                        exc_info=True,
                    )
                    if not ending.is_set():
                        locks_lost.set()
                        run.cancel()
                    return
                if loop.time() < next_beat:
                    continue
                next_beat = loop.time() + FAMILY_IMPORT_HEARTBEAT_SECONDS
                try:
                    async with asyncio.timeout(FAMILY_IMPORT_DATABASE_TIMEOUT):
                        async with session_factory() as heartbeat_session:
                            still_ours = await _beat_family_import_job(
                                heartbeat_session, job_id=job_id, worker_id=worker_id
                            )
                except Exception:
                    logger.warning("Family package import heartbeat failed", exc_info=True)
                    if (
                        loop.time() - last_beat >= FAMILY_IMPORT_SELF_STOP.total_seconds()
                        and not ending.is_set()
                    ):
                        beat_lost.set()
                        run.cancel()
                        return
                    continue
                last_beat = loop.time()
                if not still_ours:
                    if not ending.is_set():
                        job_lost.set()
                        run.cancel()
                    return

        async def record_family(family_id: str) -> None:
            # The job reads `running` on the family, committed, before the import writes
            # anything of it: a report sign-out finds the import by that, and nothing else
            # keeps a sign-out out until the import holds the family's variant-write locks.
            # If it cannot be recorded the import stops, having written nothing, and the job
            # is ended failed (the except below); a job that cannot be ended either stays
            # claimed, until a worker claims it again and runs it from the start.
            try:
                async with session_factory() as job_session:
                    recorded = await _record_job_family(
                        job_session, job_id=job_id, worker_id=worker_id, family_id=family_id
                    )
            except Exception as exc:
                raise FamilyImportNotRecorded(
                    f"Stopped before writing family {family_id}: the import job could not "
                    f"record it as the family it imports ({type(exc).__name__}), so nothing "
                    "of it was written. Re-run the import."
                ) from exc
            if not recorded:
                # Another worker claimed the job once its heartbeat went stale: the job is
                # that worker's, and this one neither runs nor ends it.
                raise FamilyImportNotRecorded(
                    f"Stopped before writing family {family_id}: the import job is no "
                    "longer this worker's (another worker claimed it), so nothing of it "
                    "was written."
                )

        progress_lost = False

        async def progress(
            validation: FamilyPackageValidationOut | None,
            datasets: list[FamilyImportDatasetSummary],
            logs: list[str],
            family_id: str | None,
        ) -> None:
            # Informational, so best-effort: the validation findings, the logs, the dataset
            # summaries and the heartbeat. A sign-out reads the job's status and family,
            # which only record_family sets before the import writes anything, and this
            # leaves as they are.
            nonlocal progress_lost
            try:
                async with session_factory() as progress_session:
                    recorded = await _update_job_progress(
                        progress_session,
                        job_id=job_id,
                        worker_id=worker_id,
                        family_id=family_id,
                        validation=validation,
                        datasets=datasets,
                        logs=[*earlier_logs, *logs],
                    )
            except Exception:
                logger.exception("Family package import progress update failed")
                return
            if not recorded and not progress_lost:
                # Said once: the import reports often, and its heartbeat stops it soon.
                progress_lost = True
                logger.warning(
                    "Family package import job %s: a progress update matched no row, as the "
                    "job is no longer this worker's (another worker ended it as interrupted, "
                    "or claimed it again). The job's record no longer follows the import.",
                    job_id,
                )

        async def record_end(outcome: str, **fields: Any) -> None:
            # The import has ended: only this update is left, which the heartbeat lets be.
            ending.set()
            if not await _update_job_progress(
                session,
                job_id=job_id,
                worker_id=worker_id,
                status=outcome,
                completed=True,
                **fields,
            ):
                _log_unrecorded_job_end(job_id, outcome)

        async def run_import() -> None:
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
                record_family=record_family,
            )
            if result.error:
                await record_end(
                    "failed",
                    family_id=result.family_id,
                    validation=result.validation,
                    datasets=result.datasets,
                    logs=[*earlier_logs, *result.logs],
                    error=result.error,
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
            await record_end(
                "completed",
                family_id=result.family_id,
                validation=result.validation,
                datasets=result.datasets,
                logs=[*earlier_logs, *result.logs],
            )

        # The import runs as a task of its own, so that its heartbeat can stop it without
        # stopping this worker; the heartbeat checks the locks the import holds.
        with watch_family_variant_holds() as check_holds:
            run = asyncio.create_task(run_import())
            heartbeat = asyncio.create_task(keep_alive(run, check_holds))
        try:
            await run
        except asyncio.CancelledError:
            this_worker = asyncio.current_task()
            if this_worker is None or this_worker.cancelling():
                raise
            # Stopped by its heartbeat, not by this worker's own stop.
            if job_lost.is_set():
                logger.warning(
                    "Family package import job %s was stopped where it was: the job is no "
                    "longer this worker's (another worker ended it as interrupted, or claimed "
                    "it again), so the import wrote nothing more and left its family as a "
                    "stopped process does.",
                    job_id,
                )
                return
            if beat_lost.is_set():
                logger.warning(
                    "Family package import job %s stopped itself where it was: its heartbeat "
                    "had not reached the database for %d minutes, and a worker takes a job "
                    "whose heartbeat is %d minutes old for stopped. The import wrote nothing "
                    "more and left its family as a stopped process does; the job is ended as "
                    "interrupted once a worker finds its heartbeat stale.",
                    job_id,
                    FAMILY_IMPORT_SELF_STOP.total_seconds() // 60,
                    FAMILY_IMPORT_STALE_HEARTBEAT.total_seconds() // 60,
                )
                return
            if locks_lost.is_set():
                await _end_job_after_lost_locks(session_factory, job_id=job_id, worker_id=worker_id)
                return
            raise
        except Exception as exc:
            logger.exception("Family package import job failed")
            await session.rollback()
            # Ends the job only while it is this worker's: the update names the worker.
            if not await _update_job_progress(
                session,
                job_id=job_id,
                worker_id=worker_id,
                status="failed",
                error=str(exc),
                completed=True,
            ):
                _log_unrecorded_job_end(job_id, "failed")
            raise
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat


async def handle_claimed_family_import_job(job_row: Mapping[str, Any], *, worker_id: str) -> None:
    """Do what a claimed job needs: run it, or, when the claim ended it as interrupted (its
    heartbeat went stale while its import was running on the family), drop the backup
    tables that import made of the family and could not drop itself: its process stopped,
    or its heartbeat stops it once it finds the job ended, and a restore it reaches before
    then deletes nothing without them."""
    if job_row["status"] == "validating":
        await run_family_import_job(job_id=str(job_row["id"]), worker_id=worker_id)
        return
    logger.warning(
        "Family package import job %s stopped part-way; ended it as interrupted",
        job_row["id"],
    )
    try:
        dropped = await drop_import_backup_tables(
            await list_import_backup_tables(owner=str(job_row["id"]))
        )
    except Exception:  # left for the startup sweep (drop_orphaned_import_backups)
        logger.warning(
            "Could not drop the backup tables of stopped import job %s", job_row["id"], exc_info=True
        )
        return
    if dropped:
        logger.info(
            "Dropped %d backup table(s) of stopped import job %s", len(dropped), job_row["id"]
        )


async def drop_orphaned_import_backups(*, grace: timedelta = IMPORT_BACKUP_GRACE) -> list[str]:
    """Drop the import backup tables no running import owns; returns those dropped.

    Run at startup. The worker that ends a stopped job drops its backups, so this finds
    those left when that failed, or when the import ran outside a job (kept for ``grace``,
    as nothing records whether it still runs): see ``orphaned_import_backups``.
    Best-effort: a failure is logged and the tables are left for the next start.
    """
    try:
        tables = await list_import_backup_tables()
        job_owners = {table.owner for table in tables if is_job_owned(table)}
        running: set[str] = set()
        if job_owners:
            async with get_postgres_sessionmaker()() as session:
                running = await running_family_import_job_ids(session, job_owners)
        orphaned = orphaned_import_backups(
            tables, running_jobs=running, now=datetime.now(timezone.utc), grace=grace
        )
        dropped = await drop_import_backup_tables(orphaned)
    except Exception:  # never stops the start
        logger.warning("Could not drop the orphaned import backup tables", exc_info=True)
        return []
    if dropped:
        logger.info("Dropped %d orphaned import backup table(s)", len(dropped))
    return dropped


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
            await handle_claimed_family_import_job(job_row, worker_id=worker_id)
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
