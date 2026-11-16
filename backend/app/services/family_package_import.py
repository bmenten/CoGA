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

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.coga_logging import describe_error, own_error_message
from ..core.postgres import get_postgres_sessionmaker
from ..schemas import (
    FamilyImportDatasetSummary,
    FamilyImportValidationIssue,
    FamilyPackageValidationOut,
)
from .ancestry_files import register_declared_files
from .family_metadata_context import (
    FamilyMetadataContext,
    SampleMetadataContext,
    build_family_metadata_context,
    family_sample_contexts,
)
from .family_project_variants import copy_variants_to_linked_projects
from .family_variant_write_lock import (
    VariantWriteHoldLost,
    hold_family_variant_writes,
    watch_family_variant_holds,
)
from .hpo_service import PACKAGE_IMPORT, PhenotypeImport
from .metadata_service import get_current_user_by_id
from .access_control import CurrentUser, has_superuser_rights, is_admin_user
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
    ImportRefused,
    PackageExecutionResult,
    ProgressCallback,
    _issue,
    _json_list,
)
from .family_package_fingerprint import PackageFolderTooLarge, package_fingerprint
from .family_package_source import package_folder_path
from .raw_import_files_pg import unlink_managed_files
from .stored_values import json_object
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
    put_back_family_package_record,
    FailuresLeft,
    ImportMark,
    _FINISHED_DATASET_STATUSES,
    _IMPORTED_DATASET_STATUSES,
    _end_import_failed_before_datasets,
    _mark_family_import_unfinished,
    dataset_scopes,
    _package_family_id,
    _ensure_family_from_ped,
    _delete_family_shell,
    _end_family_import_unfinished,
    _flag_family_import_incomplete,
    _flag_family_restore_failed,
    _clear_family_import_incomplete,
    _mark_family_import_restoring,
    _record_family_import_finished,
    _record_package_reference,
    _enabled_dataset_summaries,
    datasets_brought_again,
    imported_dataset_scopes,
    _normalized_conflict_mode,
    _execution_metadata,
    _merge_validation_metadata,
    _existing_package_entity_warnings,
    family_sample_ids,
    is_whole_family_entry,
    manifest_roi_issue,
    pending_datasets,
    scope_with_samples,
    unfinished_entry_writer,
)
from .family_package_datasets import _import_dataset
from .family_package_manifest import _manifest_roi_value
from .import_progress import DatasetTimer
from .worker_tasks import stop_worker

logger = logging.getLogger(__name__)

FAMILY_IMPORT_WORKER_POLL_SECONDS = 2.0
# How often a running job's heartbeat is written, whatever its import is doing (waiting
# for another writer's locks, copying a snapshot): a heartbeat older than
# FAMILY_IMPORT_STALE_HEARTBEAT means the process running it has stopped, or could not
# write it; such an import is stopped when its heartbeat finds the job ended.
FAMILY_IMPORT_HEARTBEAT_SECONDS = 60.0
# An import whose heartbeat has not reached the database for this long stops itself where it
# is, as a stopped process does: half the stale window, so it has stopped well before a
# worker can take its job for stopped and another write of the family can start. Without it,
# an import would go on for as long as its heartbeat could not be written.
FAMILY_IMPORT_SELF_STOP = FAMILY_IMPORT_STALE_HEARTBEAT / 2
# How often a running import checks the connection that holds its family's variant-write
# locks: Postgres releases them with a lost connection, and an import that did not check
# would go on writing beside the next writer, unaware.
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
# Why a job is not run, as its record says: the import runs as the account that requested
# it, and that account no longer exists, or has been deactivated, which ends its
# access at once (docs/security-posture.md), or is no longer an administrator, as only an
# administrator may queue an import (the import routes). None names the account: the
# job does.
REQUESTER_GONE_ERROR = "Requesting user no longer exists"
REQUESTER_INACTIVE_ERROR = (
    "Not run: the account that requested this import has been deactivated, and an import runs "
    "as the account that requested it. Nothing of the family was written. To import the "
    "package, an active superuser or administrator queues it again."
)
REQUESTER_NOT_ADMIN_ERROR = (
    "Not run: the account that requested this import is no longer a superuser or an "
    "administrator, and an import runs only for one of them, as the account that requested it. "
    "Nothing of the family was written. To import the package, an active superuser or "
    "administrator queues it again."
)
# An overwrite queued by an account that is no longer an admin (REQ-SEC-002).
REQUESTER_CANNOT_OVERWRITE_ERROR = (
    "Not run: the account that requested this import is no longer an administrator, and only "
    "an administrator overwrites an existing family. Nothing of the family was written. To "
    "import the package, import it with update, or an active administrator queues the "
    "overwrite again."
)
# How long a backup table of an import run outside a job is kept: nothing records whether
# that import still runs (drop_orphaned_import_backups).
IMPORT_BACKUP_GRACE = timedelta(days=1)


class FamilyImportNotRecorded(RuntimeError):
    """The import job could not record the family it imports, so the import stopped
    before writing anything of it."""


def _failure_text(exc: BaseException, *, failed: str) -> str:
    """What a failed dataset (``failed``: "The dataset") or import ("The import") says on its
    job's record, which the import pages show: CoGA's own refusal as it is written (an
    ``ImportRefused``, an ``HTTPException`` of a loader, any error of a class CoGA defines;
    ``own_error_message``), any other error by its kind. Such an error's text is not CoGA's
    sentence and can quote what was being read: a failed statement's parameters, a line of a
    package file. The server log has the error's frames."""
    return own_error_message(exc) or (
        f"{failed} failed on an unexpected error ({describe_error(exc)}); the server log shows "
        "where it failed."
    )


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
    can have more than one. A failure no job records is a single-file upload's,
    or an import's run outside a job, and is said so."""
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
    single-file upload, or a deletion of the family's data, that did not
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
    flag names every dataset of the import as failed, and its entry, kept,
    every dataset of the family."""
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
    ended."""
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


# Why a job imports nothing when the package folder's fingerprint differs from the one it was
# queued with (family_package_jobs.queue_family_import_job).
PACKAGE_CHANGED_SINCE_QUEUED = (
    "The package changed after the import was queued: a file in the folder was added, removed or "
    "changed (by name, size or modification time) since, or the manifest the import reads is "
    "another. Nothing was imported. Run the dry run again, check its report, then import."
)

# Why a job ends failed, and its import is undone, when the package folder's fingerprint, taken
# again after a dataset, differs from the one its validation took before reading the folder.
PACKAGE_CHANGED_DURING_IMPORT = (
    "The package changed while it was imported: a file in the folder was added, removed or "
    "changed (by name, size or modification time) after the import's validation began reading "
    "the folder, or the folder could no longer be read. What the import read may be partly the "
    "package as it was and partly as it is now, so the import was undone: a family it created "
    "is removed, and an existing family's data are put back as they were. Import the package "
    "again once it is complete: run the dry run, check its report, then import."
)


def _check_queued_package_fingerprint(
    validation: FamilyPackageValidationOut,
    logs: list[str],
    *,
    queued: str | None,
) -> FamilyPackageValidationOut:
    """The validation, refused (``package_changed_since_queued``) when the fingerprint its
    validation took of the local package folder, before reading it, differs from ``queued``, the
    one the job was queued with: the import then writes nothing. The log says which fingerprint
    the import read the folder with, and what it was compared with.

    A folder the validation refuses for another reason (gone, outside the import folders) has
    none: that refusal says why.
    """
    current = validation.package_fingerprint
    if current is not None and queued is None:
        logs.append(f"Package fingerprint {current[:12]}; there is no earlier fingerprint to compare it with.")
        return validation
    if queued is None or current == queued:
        if current is not None:
            logs.append(f"Package fingerprint {current[:12]}: the folder is as it was when the job was queued.")
        return validation
    if current is None and validation.errors:
        return validation
    logs.append(
        f"Package fingerprint {current[:12] if current else 'none'} differs from {queued[:12]}, "
        "taken when the job was queued."
    )
    return validation.model_copy(
        update={
            "valid": False,
            "errors": [*validation.errors, _issue("package_changed_since_queued", PACKAGE_CHANGED_SINCE_QUEUED)],
        }
    )


async def _package_changed_since_validation(
    bundle: FamilyPackageBundle, logs: list[str], *, say_unchanged: bool = False
) -> bool:
    """Whether the local package folder's fingerprint, taken now, differs from the one the
    validation took before reading it, or the folder can no longer be read; said in ``logs``
    when it does, and with ``say_unchanged`` when it does not."""
    if bundle.package_fingerprint is None:
        return False
    try:
        now = await asyncio.to_thread(package_fingerprint, bundle.root, kept_manifest_sha256=bundle.manifest_sha256)
    except (OSError, PackageFolderTooLarge):
        now = None
    if now is not None and now.sha256 == bundle.package_fingerprint:
        if say_unchanged:
            logs.append(f"The package folder did not change while it was imported (fingerprint {now.short}).")
        return False
    logs.append(
        f"The package folder changed while it was imported: its fingerprint is "
        f"{now.short if now is not None else 'unreadable'}, not {bundle.package_fingerprint[:12]}."
    )
    return True


# What a dataset of an import undone for a changed package is recorded as.
_UNDONE_DATASET_MESSAGE = "Not kept: the package changed while it was imported, and the import was undone."


def _fail_datasets_of_changed_package(
    datasets: list[FamilyImportDatasetSummary], *, ran: list[FamilyImportDatasetSummary]
) -> None:
    """Record as failed every dataset the import undone for a changed package wrote or had still
    to run: one that imported, with warnings or not, registered its files, or did not run. One
    an update skipped (its data was there) wrote nothing and stays skipped; one that failed
    keeps its own message."""
    set_out = {summary.dataset_type for summary in ran}
    for index, dataset in enumerate(datasets):
        if dataset.dataset_type not in set_out or dataset.status in {"failed", "skipped"}:
            continue
        datasets[index] = dataset.model_copy(update={"status": "failed", "message": _UNDONE_DATASET_MESSAGE})


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
    manifest_text: str,
    queued_package_fingerprint: str | None = None,
    delivery: dict[str, Any] | None = None,
) -> PackageExecutionResult:
    """Run an import of the package in the local folder ``folder_path``, read where it lies.
    ``job_id`` is the import job running it, if any: an incomplete-import flag names it.
    ``record_family``, if given, is awaited with the family's identifier before anything
    of the family is written; if it raises, the import writes nothing. The job's runner
    records there the family a report sign-out finds the import by. ``progress`` is
    informational: what it fails to record changes nothing a sign-out reads.
    ``manifest_text`` is the manifest the job keeps, the one an approved delivery's package makes:
    the import reads no file of the folder as its manifest, and no PED.
    ``queued_package_fingerprint`` is the package fingerprint the job was queued with: the
    import writes nothing when the folder's differs (``_check_queued_package_fingerprint``).
    ``delivery`` is the delivery of a watched import folder the job imports, which the family's
    package record names."""
    # The existing family the request names, read as every ID is: without the whitespace
    # around it.
    requested_family_id = (requested_family_id or "").strip() or None
    validation, bundle = load_validated_family_package(folder_path, manifest_text=manifest_text)
    conflict_mode = _normalized_conflict_mode(conflict_mode)
    request_metadata = _execution_metadata(
        requested_family_id=requested_family_id,
        conflict_mode=conflict_mode,
    )
    # The job's record is written from the validation's metadata as the import goes, so the
    # delivery it imports is merged in too: the job keeps naming it once it has run.
    if delivery is not None:
        request_metadata["delivery"] = delivery
    validation = _merge_validation_metadata(validation, request_metadata)
    logs = [f"Validated package path {package_folder_path(folder_path)}."]
    validation = _check_queued_package_fingerprint(validation, logs, queued=queued_package_fingerprint)
    if delivery is not None:
        logs.append(
            f"The package was delivered into a watched import folder (delivery {str(delivery.get('id'))[:8]}, "
            f"run {delivery.get('run_id') or 'not named'})."
        )
        if bundle is not None:
            bundle.delivery = delivery
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
    if dry_run and session is not None and hasattr(session, "execute") and bundle is not None:
        # The import looks the manifest's region of interest up when it registers the family,
        # and leaves the family without one if it is not found: the dry run says so.
        roi_issue = await manifest_roi_issue(
            session,
            roi_query=_manifest_roi_value(bundle.manifest),
            project_id=project_id,
            family_id=_package_family_id(validation, bundle),
        )
        if roi_issue is not None:
            validation = validation.model_copy(update={"warnings": [*validation.warnings, roi_issue]})
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
    registration_warnings: list[FamilyImportValidationIssue] = []
    try:
        family_context, family_created = await _ensure_family_from_ped(
            session,
            bundle=bundle,
            project_id=project_id,
            user=user,
            validation=validation,
            conflict_mode=conflict_mode,
            import_mark=import_mark,
            warnings=registration_warnings,
        )
    except Exception:
        # Failed before its first dataset: if it had marked the family, the mark becomes
        # the import_incomplete flag, naming its datasets as failed (it wrote none).
        await _end_import_failed_before_datasets(
            session, family_id=_package_family_id(validation, bundle), mark=import_mark
        )
        raise
    sample_contexts = family_sample_contexts(family_context)
    logs.append(
        f"Family {family_context.family_id} is registered with {len(sample_contexts)} sample(s)."
    )
    if registration_warnings:
        # A region of interest the manifest names and the import did not find: in
        # the import's result, with the package's warnings.
        validation = validation.model_copy(
            update={"warnings": [*validation.warnings, *registration_warnings]}
        )
        logs.append("The manifest's region of interest was not found: see the warnings.")
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
            # Each project an existing family is linked to gets the callsets it holds none
            # of, before the snapshot (in the dataset loop), so a restore keeps them: the
            # import writes only its own datasets, under each of the family's projects, and
            # an update leaves out those stored. A project the import linked the family to
            # would otherwise show only what the import wrote (the owner's decisions of
            # 2026-11-10: before the snapshot, at every import).
            if not family_created and await copy_variants_to_linked_projects(
                session, family_context.family_uuid, family_context.assembly_name
            ):
                logs.append(
                    "Copied the family's stored variants to the projects it is linked to "
                    "that held none of them."
                )
        except Exception:
            await _end_import_failed_before_datasets(
                session, family_id=family_context.family_id, mark=import_mark
            )
            raise
        result = await _import_family_datasets(
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
    if result.completed and not result.error:
        # VCF files the manifest declares for the exploratory ancestry PCA (outside the intended
        # purpose): recorded after the family's variant writes are released, reading no file. It
        # cannot fail the import (ancestry_files.register_declared_files).
        await register_declared_files(
            session, bundle=bundle, family_uuid=family_context.family_uuid,
            family_identifier=family_context.family_id, project_ids=family_context.project_ids,
            project_id=project_id, job_id=job_id, logs=result.logs,
        )
    return result


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
    import's, who a compensating deletion of a new family is recorded as made by.

    ``import_mark`` is this import's entry in the family's ``import_unfinished``: each
    dataset it finishes is recorded there as it goes, and the entry is removed when the
    import ends, whichever way, but one: after a restore that failed part-way it stays,
    naming every dataset of the family. If the import's process stops first, the entry
    stays.

    Each change of the family's phenotypes it makes (the package's, a restore putting them
    back, the family it created removed again) is on the family's clinical audit trail as
    made by ``user``, who runs the import.

    After each dataset the local package folder's fingerprint is taken again. When it differs
    from the one the validation took before reading the folder, the package changed while it
    was imported, and what the import read may be partly the package as it was and partly as
    it is now: the import stops, and is undone (the owner's decision of 2026-11-05). Every
    dataset it wrote, or had still to run, counts as failed: a family it created is removed
    with everything of it; an existing family's datasets are put back from the snapshot taken
    before the loop (for an update as for an overwrite, when the package has a fingerprint),
    and its package record as it was (its ``previous_import``). Where the family cannot be
    put back, it is flagged import-incomplete, as for any failed import. The job ends failed
    with ``package_changed_during_import``, and the package list offers the folder again."""
    import_key = import_mark.key if import_mark is not None else None
    # The import as the phenotypes' audit events name it: who runs it, the package (by the
    # folder it was read from, as its log says) and its job.
    phenotype_import = PhenotypeImport(
        user=user,
        via=PACKAGE_IMPORT,
        source=package_folder_path(bundle.root),
        job_id=job_id,
    )
    # What the import records each dataset as replacing (dataset_scopes), from before its
    # first write: each importer is handed its dataset's (an SNV family VCF is stored as
    # the callset its scope names).
    mark_scopes: Mapping[str, Any] = import_mark.scopes if import_mark is not None else {}
    # The scope a failed dataset is flagged for where its failure may have removed the data
    # of samples it does not name (DatasetLeftMissing): its own, with theirs.
    failed_scopes: dict[str, Any] = {}
    finished: set[str] = set()
    # The samples of each dataset an update left as they were, their rows being there
    # (``DatasetImportJob.skipped_samples``).
    skipped_samples: dict[str, set[str]] = {}
    # Overwrite of a PRE-EXISTING family is delete-then-insert, so a mid-import failure
    # can destroy the family's prior data. Snapshot it first so a failed overwrite is
    # atomically rolled back to the pre-import state instead of only being
    # flagged import-incomplete. Two stores are captured: the ClickHouse variant/interval
    # rows, and the Postgres rows the dataset loop writes (repeats / paraphase / interval
    # sources / HPO / annotation manifest) — the importers commit those per-dataset, so a
    # later failure would otherwise leave them out of sync with the restored variants.
    # Only overwrite is destructive — update mode is skip-if-exists, so nothing prior is
    # lost and the flag path below suffices. Best-effort: if a snapshot can't be taken we
    # degrade to the flag rather than failing the import.
    # An update is snapshotted too when the package has a fingerprint: an import whose package
    # changed while it was read is undone, an update as much as an overwrite. An update that
    # fails otherwise is not restored: its imported datasets are kept, and the family flagged.
    clickhouse_snapshot = None
    postgres_snapshot = None
    if not family_created and (conflict_mode == "overwrite" or bundle.package_fingerprint is not None):
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
                family_context.family_uuid,
                exc_info=True,
            )
            clickhouse_snapshot = None
            postgres_snapshot = None

    package_changed = False
    reference_recorded = False
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
        except Exception as exc:  # the dataset is recorded as failed and fails the import
            await session.rollback()
            if isinstance(exc, DatasetLeftMissing):
                failed_scopes[summary.dataset_type] = scope_with_samples(
                    mark_scopes.get(summary.dataset_type), exc.samples
                )
            # The dataset's message and the job's log line are CoGA's own refusal, or else
            # name the error by its kind, its frames in the server log.
            message = _failure_text(exc, failed="The dataset")
            if own_error_message(exc) is None:
                logger.error(
                    "Dataset %s of import job %s failed on an unexpected error (%s)",
                    summary.dataset_type,
                    job_id,
                    describe_error(exc),
                    exc_info=exc,
                )
            datasets[index] = summary.model_copy(
                update={
                    "status": "failed",
                    "message": message,
                    "progress": timer.finish(),
                }
            )
            logs.append(f"Dataset {summary.dataset_type} failed: {message}")
            if progress is not None:
                await progress(validation, datasets, logs, family_context.family_id)
            continue
        # The reference genome the package names, with the first of its datasets the family
        # holds: inside the loop, so a failed import that is undone takes it away with its data.
        if not reference_recorded and datasets[index].status in _IMPORTED_DATASET_STATUSES:
            await _record_package_reference(session, bundle=bundle, context=family_context)
            reference_recorded = True
        # Recorded on the family once the dataset has finished, never before: one this
        # import stops inside, or fails, stays pending in its entry.
        if import_mark is not None and datasets[index].status in _FINISHED_DATASET_STATUSES:
            finished.add(summary.dataset_type)
            await _record_family_import_finished(session, family_context, import_mark, finished)
        if progress is not None:
            await progress(validation, datasets, logs, family_context.family_id)
        if await _package_changed_since_validation(bundle, logs):
            package_changed = True
            break

    # Once more after the last dataset, whichever way it ended: one that failed is not checked
    # in the loop, and the package may have changed while it ran.
    if not package_changed:
        package_changed = await _package_changed_since_validation(bundle, logs, say_unchanged=True)
    if package_changed:
        _fail_datasets_of_changed_package(datasets, ran=_enabled_dataset_summaries(validation))
        validation = validation.model_copy(
            update={
                "valid": False,
                "errors": [
                    *validation.errors,
                    _issue("package_changed_during_import", PACKAGE_CHANGED_DURING_IMPORT, path=bundle.root),
                ],
            }
        )
        if progress is not None:
            await progress(validation, datasets, logs, family_context.family_id)

    failed_datasets = [dataset.dataset_type for dataset in datasets if dataset.status == "failed"]
    # Imported in full, or with warnings (the rows its input got wrong left out, each named in
    # the job's record): either way its data is the family's now, so a new family holding it is
    # kept and flagged, never deleted with it, and the flag names it as imported.
    imported_datasets = [
        dataset.dataset_type for dataset in datasets if dataset.status in _IMPORTED_DATASET_STATUSES
    ]
    # What each dataset it brought again replaced (dataset_scopes): an earlier import's
    # failure or unfinished entry for the same scope is completed by it. Brought again:
    # imported, imported with warnings, or registered when the package carries nothing for
    # it. Not the samples an update skipped, nor a dataset it skipped whole
    # (`skipped`): a skip imports nothing again, so it completes nothing.
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
    if family_created and (package_changed or (failed_datasets and not imported_datasets)):
        await session.rollback()
        # Every dataset counts as pending again until the family is gone: a stop in the
        # middle leaves a family part-removed.
        if import_mark is not None:
            await _record_family_import_finished(session, family_context, import_mark, ())
        # The kept copies of its files (a manifest CoGA kept), unlinked once its rows have gone.
        managed_files = await _delete_family_shell(session, family_context, user, phenotype_import=phenotype_import)
        await session.commit()
        await unlink_managed_files(managed_files or [])
        compensated = True
        logs.append(
            "The package changed while it was imported; removed the newly-created family with "
            "everything the import wrote of it."
            if package_changed
            else "No dataset imported successfully; rolled back the newly-created family shell."
        )
    elif (clickhouse_snapshot is not None or postgres_snapshot is not None) and (
        package_changed or (failed_datasets and conflict_mode == "overwrite")
    ):
        # The restore rewrites every table's rows of the family, table by table, whichever
        # import wrote them: until it has put the family back, every dataset of the family
        # counts as pending, this import's finished ones too and those of other imports.
        # If the process stops in the restore, the entry says so.
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
            # The family's package record goes back with its data: it names the delivery the
            # family's data was imported from, which a sign-out compares the package folder with
            # (package_changed_after_import).
            await put_back_family_package_record(session, family_context.family_uuid)
            restored = True
            logs.append(
                "The package changed while it was imported; restored the family's data, and its "
                "package record, to their state before the import."
                if package_changed
                else "Import failed; atomically restored the family's data, and its package record, "
                "to their pre-import state."
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
            # owner's decision, 2026-10-13).
            untouched = isinstance(exc, FamilyRestoreRefused)
            logger.warning(
                "Failed to restore family %s after a failed overwrite (%s); flagging "
                "import-incomplete",
                family_context.family_uuid,
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
                # whose loader ended well too.
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

    if package_changed:
        error = PACKAGE_CHANGED_DURING_IMPORT
        logs.append(error)
    elif failed_datasets:
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
                # import's entry.
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
    # never served; dropping the rows here just frees entries that can no longer
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
                family_context.family_uuid,
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

    The import runs as the account that requested the job. A job whose account no longer
    exists, has been deactivated or is no longer an administrator is not run: it ends failed,
    saying why (``REQUESTER_GONE_ERROR``, ``REQUESTER_INACTIVE_ERROR``,
    ``REQUESTER_NOT_ADMIN_ERROR``), having written nothing.

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
    another write of the family may have run once the locks went.
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
                    requested_by_id::text AS requested_by_id,
                    metadata,
                    logs,
                    manifest_text,
                    package_fingerprint
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
        # variant-write locks is gone.
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
            # The import runs as the account that requested it, found by its id: its e-mail
            # address may have changed since, or be another account's by now. It
            # does not run once that account is gone or deactivated, which ends the account's
            # access at once, nor once it is no longer an administrator, which only the
            # import routes check (the import itself checks the project's access only):
            # the job ends failed, saying why, before anything of the family is
            # written. The account is checked here, as the import starts, and again when a
            # job still validating is claimed again: one deactivated or demoted while its
            # import runs has that import run to its end.
            requester = job_row.get("requested_by_id")
            user = await get_current_user_by_id(session, str(requester)) if requester else None
            if user is None:
                raise ImportRefused(REQUESTER_GONE_ERROR)
            if not user.is_active:
                raise ImportRefused(REQUESTER_INACTIVE_ERROR)
            if not has_superuser_rights(user):
                raise ImportRefused(REQUESTER_NOT_ADMIN_ERROR)
            job_metadata = json_object(job_row.get("metadata"))
            # The queue records the conflict mode with every job (queue_family_import_job).
            conflict_mode = str(job_metadata["conflict_mode"])
            # Overwriting an existing family replaces its data: an admin's, as every
            # replacement and deletion of a family's data is (REQ-SEC-002). A superuser imports
            # new families and adds missing datasets; the route refuses them the overwrite, and
            # so does the job of an admin demoted to superuser since it queued one.
            if (
                conflict_mode == "overwrite"
                and not bool(job_row["dry_run"])
                and not is_admin_user(user)
            ):
                raise ImportRefused(REQUESTER_CANNOT_OVERWRITE_ERROR)
            result = await execute_family_package_import(
                session,
                folder_path=str(job_row["submitted_path"]),
                project_id=str(job_row["project_id"]) if job_row.get("project_id") else None,
                dry_run=bool(job_row["dry_run"]),
                user=user,
                requested_family_id=job_metadata.get("requested_family_id"),
                conflict_mode=conflict_mode,
                progress=progress,
                job_id=job_id,
                record_family=record_family,
                # A job queued without one reads an empty manifest, which validation refuses.
                manifest_text=str(job_row.get("manifest_text") or ""),
                queued_package_fingerprint=job_row.get("package_fingerprint"),
                delivery=job_metadata.get("delivery") if isinstance(job_metadata.get("delivery"), dict) else None,
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
                        "Precomputed genome-wide haplotype lineage for the family of import "
                        "job %s: %s blocks",
                        job_id,
                        stored_blocks,
                    )
                except Exception:  # pragma: no cover - precompute is best-effort
                    logger.exception(
                        "Haplotype lineage precompute failed for the family of import job %s; "
                        "genome overview will use the fast grey-relatives fallback",
                        job_id,
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
            logger.exception("Family package import job %s failed", job_id)
            await session.rollback()
            # Ends the job only while it is this worker's: the update names the worker. Its
            # error is CoGA's own refusal, or names the error by its kind.
            if not await _update_job_progress(
                session,
                job_id=job_id,
                worker_id=worker_id,
                status="failed",
                error=_failure_text(exc, failed="The import"),
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
            # Which worker, of which process, took which job: the job's record names its
            # worker only until the job ends.
            logger.info(
                "Package-import worker %s claimed job %s (it was %s)",
                worker_id,
                job_row["id"],
                job_row.get("claimed_from"),
            )
            await handle_claimed_family_import_job(job_row, worker_id=worker_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover
            logger.exception("Family package import worker encountered an unexpected error")
            await asyncio.sleep(FAMILY_IMPORT_WORKER_POLL_SECONDS)


stop_family_package_import_worker = stop_worker
