"""A package import that stops part-way leaves its family marked (TF-06 H16).

An import writes a family over minutes, committing as it goes. Its fail-clean logic --
remove a new family nothing imported into, put back a failed overwrite, flag the rest
``import_incomplete`` -- runs in the import's own process, so when that process ends
part-way (a restart, a crash, running out of memory) none of it runs. The family was left
partly imported and unflagged; the worker that later claimed the stale job ran it again
from the start, where a cancel refused the family it had created ("Package validation
failed") and an update skipped the dataset it had left half-written, and the re-run
replaced the job's record of what the first attempt did.

Now an import records its entry in the family's ``import_unfinished`` before its first
write (a new family is created with it), records each dataset there once it has finished,
and removes the entry when it ends, whichever way. These tests model that map as each
statement documents it (the SQL runs against Postgres in
integration/test_import_crash_bookkeeping_integration.py, and the whole path in
e2e/test_e2e_import_crash_leaves_family_marked.py), stop an import inside a dataset the
way a dying process does (the task is cancelled: no ``except Exception`` runs), and run
what comes after: the worker, a re-import by update, a re-import by overwrite.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.core.config import settings
from backend.app.schemas import FamilyImportDatasetSummary
from backend.app.services import family_package_import as package_import
from backend.app.services import family_package_registration as registration
from backend.app.services import sv_gene_index_service, variant_ranking_cache
from backend.app.services.access_control import CurrentUser
from backend.app.services.family_metadata_context import FamilyMetadataContext

FAMILY = "FAM001"


def _admin() -> CurrentUser:
    return CurrentUser(
        id="00000000-0000-0000-0000-000000000001",
        username="admin@example.com",
        email="admin@example.com",
        role="admin",
        created_at=datetime.now(timezone.utc),
    )


def _context() -> FamilyMetadataContext:
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id=FAMILY,
        project_ids=["project-uuid"],
        sample_rows=[
            {"sample_uuid": "uuid-S1", "sample_id": "S1", "sex": "male", "role": "proband", "affected": True},
            {"sample_uuid": "uuid-S2", "sample_id": "S2", "sex": "female", "role": "mother", "affected": False},
        ],
        sample_uuid_to_name={"uuid-S1": "S1", "uuid-S2": "S2"},
        sample_name_to_uuid={"S1": "uuid-S1", "S2": "uuid-S2"},
        affected_sample_names=["S1"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


class _Family:
    """The family row's import state, changed as each statement documents.

    ``unfinished`` is ``metadata.import_unfinished`` and ``incomplete`` is
    ``metadata.import_incomplete``. A statement is told apart by what it binds: the
    mark binds an ``entry``, the failure flag a ``payload``, the success path's clear the
    ``remaining`` map it computed under the row's lock, and the end of a put-back import
    only its ``import_key``.
    """

    def __init__(self) -> None:
        self.unfinished: dict[str, Any] = {}
        self.incomplete: dict[str, Any] | None = None
        self.events: list[tuple[Any, ...]] = []

    def session(self) -> "_FamilySession":
        return _FamilySession(self)


class _FamilySession:
    def __init__(self, family: _Family) -> None:
        self.family = family

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
        sql = " ".join(str(statement).split())
        params = params or {}
        family = self.family
        if sql.startswith("SELECT metadata -> 'import_unfinished'"):
            stored = json.loads(json.dumps(family.unfinished)) or None
            return SimpleNamespace(scalar_one_or_none=lambda: stored)
        assert sql.startswith("UPDATE families"), f"unmodelled statement: {sql}"
        if "entry" in params:
            family.unfinished[params["import_key"]] = json.loads(params["entry"])
            family.events.append(("marked", sorted(family.unfinished[params["import_key"]]["finished_datasets"])))
        elif "payload" in params and "family_id" in params:
            # An import that failed before its first dataset: only where its entry is.
            if params["import_key"] not in family.unfinished:
                return SimpleNamespace(rowcount=0)
            family.unfinished.pop(params["import_key"])
            family.incomplete = json.loads(params["payload"])
            family.events.append(("failed before its datasets",))
        elif "payload" in params:
            family.unfinished.pop(params.get("import_key"), None)
            family.incomplete = json.loads(params["payload"])
            family.events.append(("flagged",))
        elif "remaining" in params:
            family.unfinished = json.loads(params["remaining"])
            family.incomplete = None
            family.events.append(("cleared",))
        else:
            family.unfinished.pop(params["import_key"], None)
            family.events.append(("ended",))
        return SimpleNamespace(rowcount=1)

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


@pytest.fixture
def family(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Family:
    """FAM001 and its package. Registration writes the import's mark as the real one does
    (the statement ``_mark_family_import_unfinished`` makes), the datasets and the
    snapshot are stubbed per test, and the family's import state is a ``_Family``."""
    root = tmp_path / FAMILY
    root.mkdir()
    (root / "family.ped").write_text(f"{FAMILY} S1 0 0 1 2\n{FAMILY} S2 0 0 2 1\n", encoding="utf-8")
    (root / "manifest.yaml").write_text(
        f"schema_version: 1\nfamily_id: {FAMILY}\nped: family.ped\n", encoding="utf-8"
    )
    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path)])
    state = _Family()
    state.root = root  # type: ignore[attr-defined]
    state.created = False  # type: ignore[attr-defined]
    state.marks = []  # type: ignore[attr-defined]

    async def register(session: Any, *, import_mark: Any = None, **_kwargs: Any):
        state.marks.append(import_mark)  # type: ignore[attr-defined]
        await registration._mark_family_import_unfinished(
            session, family_uuid="family-uuid", mark=import_mark
        )
        state.events.append(("registered",))
        return _context(), state.created  # type: ignore[attr-defined]

    @asynccontextmanager
    async def hold(_family_uuid: str, **_kwargs: Any):
        yield

    async def no_warnings(*_args: Any, **_kwargs: Any) -> list[Any]:
        return []

    async def nothing(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(package_import, "_ensure_family_from_ped", register)
    monkeypatch.setattr(package_import, "hold_family_variant_writes", hold)
    monkeypatch.setattr(package_import, "_existing_package_entity_warnings", no_warnings)
    monkeypatch.setattr(variant_ranking_cache, "clear_family_ranking_cache", nothing)
    monkeypatch.setattr(sv_gene_index_service, "clear_family_sv_gene_index", nothing)
    return state


def _datasets(monkeypatch: pytest.MonkeyPatch, outcomes: dict[str, Any]) -> list[str]:
    """Run the datasets in ``outcomes`` order: a status ends it so, an exception fails it,
    and an ``asyncio.Event`` holds it until the test stops the import there."""
    started: list[str] = []
    monkeypatch.setattr(
        package_import,
        "_enabled_dataset_summaries",
        lambda _validation: [
            FamilyImportDatasetSummary(dataset_type=name, status="valid") for name in outcomes
        ],
    )

    async def import_dataset(_session: Any, *, summary: Any, **_kwargs: Any) -> Any:
        started.append(summary.dataset_type)
        outcome = outcomes[summary.dataset_type]
        if isinstance(outcome, asyncio.Event):
            outcome.set()
            await asyncio.Event().wait()  # never returns: the import stops in here
        if isinstance(outcome, Exception):
            raise outcome
        return summary.model_copy(update={"status": outcome, "message": outcome})

    monkeypatch.setattr(package_import, "_import_dataset", import_dataset)
    return started


async def _import(family: _Family, *, conflict_mode: str = "update", job_id: str | None = "job-1"):
    return await package_import.execute_family_package_import(
        family.session(),  # type: ignore[arg-type]
        folder_path=family.root,  # type: ignore[attr-defined]
        project_id="project-uuid",
        dry_run=False,
        user=_admin(),
        conflict_mode=conflict_mode,
        job_id=job_id,
    )


async def _stopped_in(family: _Family, held: asyncio.Event, **kwargs: Any) -> None:
    """Run an import until it is inside the held dataset, then stop it there as a dying
    process stops: the task is cancelled, so none of its fail-clean code runs."""
    run = asyncio.create_task(_import(family, **kwargs))
    await asyncio.wait_for(held.wait(), timeout=5)
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run


# --- the mark ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_import_marks_the_family_before_it_writes_anything_of_it(family, monkeypatch) -> None:
    _datasets(monkeypatch, {"snv": "imported", "coverage": "imported"})

    await _import(family)

    (mark,) = family.marks
    assert (mark.key, mark.job_id, mark.datasets) == ("job-1", "job-1", ("coverage", "snv"))
    # The mark is the first thing written, and written again once the import holds the
    # family's variant writes; each dataset is recorded once it has finished.
    assert family.events[:3] == [("marked", []), ("registered",), ("marked", [])]
    assert family.events[3:5] == [("marked", ["snv"]), ("marked", ["coverage", "snv"])]


@pytest.mark.asyncio
async def test_an_import_stopped_inside_a_dataset_leaves_its_entry_naming_what_it_had_not_finished(
    family, monkeypatch
) -> None:
    held = asyncio.Event()
    started = _datasets(monkeypatch, {"snv": "imported", "coverage": held, "repeats_trgt": "imported"})

    await _stopped_in(family, held)

    assert started == ["snv", "coverage"]
    # Before: nothing marked the family, and nothing of the import's fail-clean ran.
    entry = family.unfinished["job-1"]
    assert entry["datasets"] == ["coverage", "repeats_trgt", "snv"]
    assert entry["finished_datasets"] == ["snv"]
    assert registration.pending_datasets(entry) == {"coverage", "repeats_trgt"}
    assert family.incomplete is None
    assert not any(event[0] in {"flagged", "cleared", "ended"} for event in family.events)


@pytest.mark.asyncio
async def test_a_dataset_that_fails_stays_pending_until_the_import_ends(family, monkeypatch) -> None:
    held = asyncio.Event()
    _datasets(
        monkeypatch,
        {"snv": RuntimeError("ClickHouse insert failed"), "sv_needlr": "skipped", "coverage": held},
    )

    await _stopped_in(family, held)

    # A failed dataset's own clean-up is best-effort, so it is not recorded as finished;
    # a skipped one wrote nothing.
    assert registration.pending_datasets(family.unfinished["job-1"]) == {"coverage", "snv"}


@pytest.mark.asyncio
async def test_an_entry_another_import_removed_while_this_one_waited_is_written_again(
    family, monkeypatch
) -> None:
    # Two imports of the family: while this one waited for the variant-write locks, the
    # other completed, overwriting everything this one lists, and its clear removed this
    # one's entry. This one then holds the locks and starts its first dataset.
    held = asyncio.Event()
    _datasets(monkeypatch, {"snv": held})

    @asynccontextmanager
    async def hold_after_the_other_import(_family_uuid: str, **_kwargs: Any):
        family.unfinished.clear()  # the other import's clear
        yield

    monkeypatch.setattr(package_import, "hold_family_variant_writes", hold_after_the_other_import)

    await _stopped_in(family, held)

    # Before: written again only once a dataset had finished, so a stop in the first one
    # left the family with neither an entry nor a flag.
    assert registration.pending_datasets(family.unfinished["job-1"]) == {"snv"}


@pytest.mark.asyncio
async def test_a_stop_while_a_failed_overwrite_is_put_back_leaves_every_dataset_pending(
    family, monkeypatch
) -> None:
    # snv finished, coverage failed: the restore rewrites every table, snv's included.
    _datasets(monkeypatch, {"snv": "imported", "coverage": RuntimeError("bad BED")})
    in_restore = asyncio.Event()

    async def snapshot(*_args: Any, **_kwargs: Any) -> str:
        return "snapshot"

    async def restore_until_the_process_dies(*_args: Any, **_kwargs: Any) -> None:
        in_restore.set()
        await asyncio.Event().wait()

    for name, value in {
        "snapshot_family_postgres_state": snapshot,
        "snapshot_family_clickhouse_state": snapshot,
        "restore_family_clickhouse_state": restore_until_the_process_dies,
    }.items():
        monkeypatch.setattr(package_import, name, value)

    await _stopped_in(family, in_restore, conflict_mode="overwrite")

    # Before: the entry still said snv had finished, though the restore had deleted it,
    # so an overwrite of coverage alone would have cleared it.
    entry = family.unfinished["job-1"]
    assert entry["finished_datasets"] == []
    assert registration.pending_datasets(entry) == {"coverage", "snv"}


@pytest.mark.asyncio
async def test_an_import_that_fails_before_its_first_dataset_leaves_the_failure_flag(
    family, monkeypatch
) -> None:
    from fastapi import HTTPException

    _datasets(monkeypatch, {"snv": "imported", "coverage": "imported"})

    @asynccontextmanager
    async def hold_a_sample_was_deleted(_family_uuid: str, **_kwargs: Any):
        raise HTTPException(status_code=404, detail="Sample not found")
        yield  # pragma: no cover

    monkeypatch.setattr(package_import, "hold_family_variant_writes", hold_a_sample_was_deleted)

    with pytest.raises(HTTPException):
        await _import(family)

    # It wrote none of its datasets: it failed, it did not stop part-way.
    assert family.unfinished == {}
    assert family.incomplete is not None
    assert family.incomplete["failed_datasets"] == ["coverage", "snv"]
    assert (family.incomplete["imported_datasets"], family.incomplete["job_id"]) == ([], "job-1")


@pytest.mark.asyncio
async def test_an_import_that_fails_before_marking_the_family_leaves_nothing(family, monkeypatch) -> None:
    _datasets(monkeypatch, {"snv": "imported"})

    async def register_refused(_session: Any, **_kwargs: Any):
        raise RuntimeError("Family 'FAM001' already exists; choose update or overwrite to import data.")

    monkeypatch.setattr(package_import, "_ensure_family_from_ped", register_refused)

    with pytest.raises(RuntimeError):
        await _import(family, conflict_mode="cancel")

    assert family.unfinished == {} and family.incomplete is None


# --- how an import that runs to its end leaves the family -------------------------------


@pytest.mark.asyncio
async def test_a_completed_import_removes_its_entry(family, monkeypatch) -> None:
    _datasets(monkeypatch, {"snv": "imported"})

    result = await _import(family)

    assert result.completed is True
    assert family.unfinished == {} and family.incomplete is None


@pytest.mark.asyncio
async def test_a_failed_import_swaps_its_entry_for_the_import_incomplete_flag(family, monkeypatch) -> None:
    _datasets(monkeypatch, {"snv": "imported", "coverage": RuntimeError("bad BED")})

    result = await _import(family)

    assert result.completed is False
    assert family.unfinished == {}
    assert family.incomplete is not None
    assert (family.incomplete["failed_datasets"], family.incomplete["job_id"]) == (["coverage"], "job-1")


@pytest.mark.asyncio
async def test_a_failed_overwrite_put_back_removes_its_entry(family, monkeypatch) -> None:
    _datasets(monkeypatch, {"snv": "imported", "coverage": RuntimeError("bad BED")})
    restored: list[str] = []

    async def snapshot(*_args: Any, **_kwargs: Any) -> str:
        return "snapshot"

    async def restore(*_args: Any, **_kwargs: Any) -> None:
        restored.append("restored")

    async def discard(*_args: Any, **_kwargs: Any) -> None:
        return None

    for name, value in {
        "snapshot_family_postgres_state": snapshot,
        "snapshot_family_clickhouse_state": snapshot,
        "restore_family_clickhouse_state": restore,
        "restore_family_postgres_state": restore,
        "discard_family_clickhouse_snapshot": discard,
    }.items():
        monkeypatch.setattr(package_import, name, value)

    result = await _import(family, conflict_mode="overwrite")

    assert result.completed is False and restored == ["restored", "restored"]
    # Put back as it was before the import: no entry of its own, and no flag.
    assert family.unfinished == {} and family.incomplete is None
    assert ("ended",) in family.events


@pytest.mark.asyncio
async def test_a_new_family_nothing_imported_into_goes_with_its_entry(family, monkeypatch) -> None:
    family.created = True  # type: ignore[attr-defined]
    _datasets(monkeypatch, {"snv": RuntimeError("ClickHouse insert failed")})
    deleted: list[str] = []

    async def delete_shell(_session: Any, context: Any) -> None:
        deleted.append(context.family_uuid)
        family.unfinished = {}  # the row, and its metadata, are gone

    monkeypatch.setattr(package_import, "_delete_family_shell", delete_shell)

    await _import(family, conflict_mode="cancel")

    assert deleted == ["family-uuid"]
    assert not any(event[0] in {"flagged", "cleared", "ended"} for event in family.events)


# --- after a stop: the re-imports -------------------------------------------------------


@pytest.mark.asyncio
async def test_an_update_after_a_stop_completes_but_leaves_the_family_marked(family, monkeypatch) -> None:
    held = asyncio.Event()
    _datasets(monkeypatch, {"snv": "imported", "coverage": held})
    await _stopped_in(family, held)

    # The re-import by update: coverage, half written, now "already exists" and is skipped.
    _datasets(monkeypatch, {"snv": "skipped", "coverage": "skipped"})
    result = await _import(family, job_id="job-2")

    assert result.completed is True
    # Before: the update ended "completed" over the half-written coverage, and nothing said so.
    assert set(family.unfinished) == {"job-1"}
    assert registration.pending_datasets(family.unfinished["job-1"]) == {"coverage"}
    note = next(line for line in result.logs if "stays marked import-incomplete" in line)
    assert "import job job-1" in note and "coverage" in note and "overwrite" in note


@pytest.mark.asyncio
async def test_an_overwrite_that_imports_what_the_stopped_import_had_not_finished_clears_it(
    family, monkeypatch
) -> None:
    held = asyncio.Event()
    _datasets(monkeypatch, {"snv": "imported", "coverage": held})
    await _stopped_in(family, held)

    # An overwrite without coverage leaves the half-written coverage as it is.
    _datasets(monkeypatch, {"snv": "imported"})
    await _import(family, conflict_mode="overwrite", job_id="job-2")
    assert set(family.unfinished) == {"job-1"}

    # One that imports coverage again, replacing it, completes the family.
    _datasets(monkeypatch, {"coverage": "imported"})
    result = await _import(family, conflict_mode="overwrite", job_id="job-3")
    assert result.completed is True
    assert family.unfinished == {} and family.incomplete is None
    assert not any("stays marked" in line for line in result.logs)


# --- the job: its heartbeat, its record, and a worker that finds it stopped -------------


class _JobSession:
    """The job's own row, read by ``run_family_import_job``."""

    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row

    async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: self.row))

    async def rollback(self) -> None:
        return None

    async def __aenter__(self) -> "_JobSession":
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False


def _job_row(logs: list[str]) -> dict[str, Any]:
    return {
        "id": "job-1",
        "submitted_path": "/data/families/FAM001",
        "project_id": "project-uuid",
        "dry_run": False,
        "requested_by": "admin@example.com",
        "metadata": {"conflict_mode": "update", "requested_family_id": FAMILY},
        "logs": json.dumps(logs),
    }


def _run_job_with(monkeypatch: pytest.MonkeyPatch, row: dict[str, Any], execute: Any) -> list[dict]:
    updates: list[dict] = []

    async def user(_session: Any, _email: str) -> CurrentUser:
        return _admin()

    async def update_job_progress(_session: Any, **kwargs: Any) -> None:
        updates.append(kwargs)

    monkeypatch.setattr(package_import, "get_postgres_sessionmaker", lambda: lambda: _JobSession(row))
    monkeypatch.setattr(package_import, "get_current_user_by_email", user)
    monkeypatch.setattr(package_import, "execute_family_package_import", execute)
    monkeypatch.setattr(package_import, "_update_job_progress", update_job_progress)
    monkeypatch.setattr(package_import, "precompute_family_haplotype_lineage", lambda _context: asyncio.sleep(0))
    monkeypatch.setattr(
        package_import, "build_family_metadata_context", lambda *_args, **_kwargs: asyncio.sleep(0)
    )
    return updates


@pytest.mark.asyncio
async def test_a_job_run_again_keeps_what_its_earlier_attempt_logged(monkeypatch) -> None:
    # Claimed again after its worker stopped while it validated (nothing written): the
    # earlier attempt's lines and the claim's own line stay, and this run's follow them.
    earlier = ["Validated package path /data/families/FAM001.", "The worker running this job stopped ..."]

    async def execute(_session: Any, *, progress: Any, **_kwargs: Any) -> Any:
        await progress(None, [], ["Validated package path /data/families/FAM001."], FAMILY)
        return SimpleNamespace(
            error=None,
            family_id=FAMILY,
            validation=None,
            datasets=[],
            logs=["Validated package path /data/families/FAM001.", "Family package import completed."],
        )

    updates = _run_job_with(monkeypatch, _job_row(earlier), execute)

    await package_import.run_family_import_job(job_id="job-1", worker_id="worker-1")

    progress_update, final = updates
    # Before: each update replaced the job's logs with this run's alone.
    assert progress_update["logs"] == [*earlier, "Validated package path /data/families/FAM001."]
    assert final["status"] == "completed"
    assert final["logs"][: len(earlier)] == earlier
    assert final["logs"][-1] == "Family package import completed."


@pytest.mark.asyncio
async def test_a_running_job_keeps_its_heartbeat_until_it_ends(monkeypatch) -> None:
    # Fresh while it runs, whatever the import does in between (here: waits), so a worker
    # never takes a live import for a stopped one; it stops with the job.
    beats: list[str] = []

    async def beat(_session: Any, *, job_id: str, worker_id: str) -> bool:
        beats.append(job_id)
        return True

    async def execute(_session: Any, **_kwargs: Any) -> Any:
        await asyncio.sleep(0.2)  # e.g. waiting for another writer's variant-write locks
        return SimpleNamespace(error=None, family_id=FAMILY, validation=None, datasets=[], logs=[])

    _run_job_with(monkeypatch, _job_row([]), execute)
    monkeypatch.setattr(package_import, "_beat_family_import_job", beat)
    monkeypatch.setattr(package_import, "FAMILY_IMPORT_HEARTBEAT_SECONDS", 0.02)

    await package_import.run_family_import_job(job_id="job-1", worker_id="worker-1")
    during = len(beats)
    await asyncio.sleep(0.1)

    assert during >= 3, beats
    assert len(beats) == during, "the heartbeat stops with the job"


@pytest.mark.asyncio
async def test_the_heartbeat_stops_once_the_job_is_no_longer_this_workers(monkeypatch) -> None:
    beats: list[str] = []

    async def beat(_session: Any, *, job_id: str, worker_id: str) -> bool:
        beats.append(job_id)
        return False  # another worker has claimed it, or it has ended

    async def execute(_session: Any, **_kwargs: Any) -> Any:
        await asyncio.sleep(0.15)
        return SimpleNamespace(error=None, family_id=FAMILY, validation=None, datasets=[], logs=[])

    _run_job_with(monkeypatch, _job_row([]), execute)
    monkeypatch.setattr(package_import, "_beat_family_import_job", beat)
    monkeypatch.setattr(package_import, "FAMILY_IMPORT_HEARTBEAT_SECONDS", 0.02)

    await package_import.run_family_import_job(job_id="job-1", worker_id="worker-1")

    assert beats == ["job-1"]


@pytest.mark.asyncio
async def test_the_worker_runs_no_job_the_claim_ended_as_interrupted(monkeypatch) -> None:
    # The claim ends a stale job whose import had begun writing its family (it was
    # `running`) instead of handing it over to be run again from the start.
    stop = asyncio.Event()
    claims = iter(
        [
            {"id": "job-stopped", "status": "failed", "claimed_from": "running"},
            {"id": "job-queued", "status": "validating", "claimed_from": "queued"},
        ]
    )
    ran: list[str] = []

    class _Session:
        async def __aenter__(self) -> "_Session":
            return self

        async def __aexit__(self, *_exc: Any) -> bool:
            return False

    async def claim(_session: Any, *, worker_id: str) -> Any:
        row = next(claims, None)
        if row is None:
            stop.set()
        return row

    async def run(*, job_id: str, worker_id: str) -> None:
        ran.append(job_id)

    monkeypatch.setattr(package_import, "get_postgres_sessionmaker", lambda: _Session)
    monkeypatch.setattr(package_import, "claim_next_family_import_job", claim)
    monkeypatch.setattr(package_import, "run_family_import_job", run)
    monkeypatch.setattr(package_import, "FAMILY_IMPORT_WORKER_POLL_SECONDS", 0)

    await asyncio.wait_for(package_import.family_package_import_worker(stop), timeout=5)

    assert ran == ["job-queued"]
