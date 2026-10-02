"""What a package import that stops part-way leaves behind, in real Postgres.

An import records its entry in ``families.metadata.import_unfinished`` before its first
write of the family, records there each dataset it finishes, and removes the entry when it
ends: with the ``import_incomplete`` flag (failed), alone (put back), or with the flag's
clear (completed), which also removes an earlier import's entry it completed. A job whose
worker stopped is claimed again only if its import had written nothing (``validating``);
one that was ``running`` is ended ``failed``, interrupted, and keeps its record. This
drives the real statements against the real schema:

- the entry is written and rewritten without touching the family's other metadata or
  another import's entry, and a value that is set but is not a map is kept, not dropped;
- the flag, the end of a put-back import and the clear remove what they should and keep
  the rest; the map goes once it is empty; the sign-out reader reads what they leave;
- the pipeline parameters an import records are set without writing back a stale copy of
  the metadata;
- of three stale jobs, the one that was running is ended (its log kept, a line added), the
  validating one is claimed again (its log kept, a line added), the queued one is claimed
  as before; a running job whose heartbeat is fresh is left alone, and only the job's
  worker can beat its heartbeat.

The e2e test (test_e2e_import_crash_leaves_family_marked.py) runs the whole path. Skipped
unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


def _run(make_coro) -> None:
    from backend.app.core.postgres import close_postgres_engine, init_postgres_schema

    async def _wrapped() -> None:
        try:
            await init_postgres_schema()
            await make_coro()
        finally:
            await close_postgres_engine()

    asyncio.run(_wrapped())


async def _metadata(session, family_uuid: str) -> dict:
    return (
        await session.execute(
            text("SELECT metadata FROM families WHERE id = CAST(:f AS uuid)"), {"f": family_uuid}
        )
    ).scalar_one()


def test_the_unfinished_import_entries_are_written_and_removed_as_documented() -> None:
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services import report_signout_service as rss
    from backend.app.services.family_package_qc import record_family_pipeline_metadata
    from backend.app.services.family_package_registration import (
        ImportMark,
        _clear_family_import_incomplete,
        _end_family_import_unfinished,
        _flag_family_import_incomplete,
        _mark_family_import_unfinished,
    )

    label = f"import-crash-{uuid4()}"

    async def scenario() -> None:
        sm = get_postgres_sessionmaker()
        async with sm() as s:
            family_uuid = (
                await s.execute(
                    text(
                        "INSERT INTO families (family_id, metadata) "
                        "VALUES (:f, CAST(:m AS jsonb)) RETURNING id::text"
                    ),
                    {"f": label, "m": json.dumps({"qc_profile": "short_read_wgs"})},
                )
            ).scalar_one()
            await s.commit()
        context = SimpleNamespace(family_uuid=family_uuid, family_id=label)
        first = ImportMark.begin(job_id=str(uuid4()), datasets=["snv", "coverage"])
        second = ImportMark.begin(job_id=str(uuid4()), datasets=["haplotypes"])

        async with sm() as s:
            await _mark_family_import_unfinished(s, family_uuid=family_uuid, mark=first)
            await _mark_family_import_unfinished(s, family_uuid=family_uuid, mark=second)
            await _mark_family_import_unfinished(
                s, family_uuid=family_uuid, mark=first, finished=["snv"]
            )
            metadata = await _metadata(s, family_uuid)
        assert metadata["qc_profile"] == "short_read_wgs", "the other keys stay"
        assert metadata["import_unfinished"] == {
            first.key: first.entry(["snv"]),
            second.key: second.entry(),
        }

        # The pipeline parameters an import records leave the import state as it is.
        async with sm() as s:
            await record_family_pipeline_metadata(
                s, family_uuid=family_uuid, parameters={"genome": "GRCh38"}
            )
            metadata = await _metadata(s, family_uuid)
        assert metadata["pipeline"] == {"genome": "GRCh38"}
        assert set(metadata["import_unfinished"]) == {first.key, second.key}

        # The sign-out reads them, sorted and normalized.
        async with sm() as s:
            state = await rss._import_unfinished_state(s, family_uuid)
        assert state[first.key] == {
            "job_id": first.job_id,
            "at": first.at,
            "datasets": ["coverage", "snv"],
            "finished_datasets": ["snv"],
        }

        # A failed import: the flag set and its own entry gone, in one statement.
        async with sm() as s:
            await _flag_family_import_incomplete(
                s,
                context,
                failed_datasets=["coverage"],
                imported_datasets=["snv"],
                job_id=first.job_id,
                import_key=first.key,
            )
            metadata = await _metadata(s, family_uuid)
        assert metadata["import_incomplete"]["failed_datasets"] == ["coverage"]
        assert set(metadata["import_unfinished"]) == {second.key}

        # Without a key the flag leaves the entries alone; the end of an import with no
        # entry changes nothing.
        async with sm() as s:
            await _flag_family_import_incomplete(
                s, context, failed_datasets=["coverage"], imported_datasets=[]
            )
            await _end_family_import_unfinished(s, context, import_key="no-such-import")
            metadata = await _metadata(s, family_uuid)
        assert set(metadata["import_unfinished"]) == {second.key}

        # The last entry gone, the map goes with it.
        async with sm() as s:
            await _end_family_import_unfinished(s, context, import_key=second.key)
            metadata = await _metadata(s, family_uuid)
        assert "import_unfinished" not in metadata
        assert metadata["import_incomplete"], "the end of a put-back import keeps the flag"

        # A value that is set but is not a map is kept, under a key of its own.
        async with sm() as s:
            await s.execute(
                text(
                    "UPDATE families SET metadata = jsonb_set(metadata, '{import_unfinished}', "
                    "'\"garbage\"') WHERE id = CAST(:f AS uuid)"
                ),
                {"f": family_uuid},
            )
            await s.commit()
            own = ImportMark.begin(job_id=str(uuid4()), datasets=["snv"])
            await _mark_family_import_unfinished(s, family_uuid=family_uuid, mark=own)
            metadata = await _metadata(s, family_uuid)
        assert metadata["import_unfinished"] == {"unreadable": "garbage", own.key: own.entry()}

        # A completed overwrite of snv and coverage: its own entry goes, and so does the
        # stopped import it completed; one it did not (haplotypes) and the unreadable value
        # stay, and the flag is cleared.
        covered = ImportMark.begin(job_id=str(uuid4()), datasets=["snv", "coverage"])
        other = ImportMark.begin(job_id=str(uuid4()), datasets=["haplotypes", "snv"])
        async with sm() as s:
            await _mark_family_import_unfinished(
                s, family_uuid=family_uuid, mark=covered, finished=["snv"]
            )
            await _mark_family_import_unfinished(s, family_uuid=family_uuid, mark=other)
            remaining = await _clear_family_import_incomplete(
                s, context, import_key=own.key, rewritten=["snv", "coverage"]
            )
            metadata = await _metadata(s, family_uuid)
        assert set(remaining or {}) == {"unreadable", other.key}
        assert metadata["import_unfinished"] == {"unreadable": "garbage", other.key: other.entry()}
        assert "import_incomplete" not in metadata

        # One that imports haplotypes and snv completes the other stopped import; the value
        # that is not an entry stays, as nothing can show it complete.
        async with sm() as s:
            remaining = await _clear_family_import_incomplete(
                s, context, rewritten=["snv", "haplotypes"]
            )
            metadata = await _metadata(s, family_uuid)
        assert remaining == {"unreadable": "garbage"}
        assert metadata["import_unfinished"] == {"unreadable": "garbage"}

        # Removed by hand, nothing is left: the next clear drops the empty map.
        async with sm() as s:
            await s.execute(
                text(
                    "UPDATE families SET metadata = metadata #- '{import_unfinished,unreadable}' "
                    "WHERE id = CAST(:f AS uuid)"
                ),
                {"f": family_uuid},
            )
            await s.commit()
            remaining = await _clear_family_import_incomplete(s, context)
            metadata = await _metadata(s, family_uuid)
            state = await rss._import_unfinished_state(s, family_uuid)
        assert remaining == {}
        assert "import_unfinished" not in metadata and state == {}
        assert metadata["qc_profile"] == "short_read_wgs"

        async with sm() as s:
            await s.execute(text("DELETE FROM families WHERE id = CAST(:f AS uuid)"), {"f": family_uuid})
            await s.commit()

    _run(scenario)


def test_a_stale_job_is_run_again_only_if_its_import_had_written_nothing() -> None:
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services.family_package_jobs import (
        FAMILY_IMPORT_INTERRUPTED_ERROR,
        _beat_family_import_job,
        claim_next_family_import_job,
    )

    now = datetime.now(timezone.utc)
    stale = now - timedelta(hours=1)
    # Older than any job a real run queues, so the claim reaches these first.
    long_ago = datetime(1990, 1, 1, tzinfo=timezone.utc)
    jobs = {
        "running": {"status": "running", "heartbeat_at": stale, "logs": ["Registering family metadata."]},
        "validating": {"status": "validating", "heartbeat_at": stale, "logs": ["Validated package path."]},
        "queued": {"status": "queued", "heartbeat_at": None, "logs": []},
        "live": {"status": "running", "heartbeat_at": now, "logs": ["Dataset snv: imported."]},
    }
    worker = f"w-{uuid4().hex[:8]}"

    async def scenario() -> None:
        sm = get_postgres_sessionmaker()
        async with sm() as s:
            for offset, (name, job) in enumerate(jobs.items()):
                job["id"] = (
                    await s.execute(
                        text(
                            """
                            INSERT INTO family_import_jobs (
                                submitted_path, family_id, status, worker_id, requested_by,
                                requested_at, started_at, heartbeat_at, logs, dataset_summaries
                            )
                            VALUES (
                                :path, :family, :status, :worker, 'admin@example.com',
                                :requested_at, :requested_at, :heartbeat_at,
                                CAST(:logs AS jsonb), CAST(:summaries AS jsonb)
                            )
                            RETURNING id::text
                            """
                        ),
                        {
                            "path": f"/data/families/IMPORT_CRASH_{name}",
                            "family": "IMPORT_CRASH" if job["status"] == "running" else None,
                            "status": job["status"],
                            "worker": None if job["status"] == "queued" else "w-gone",
                            "requested_at": long_ago + timedelta(seconds=offset),
                            "heartbeat_at": job["heartbeat_at"],
                            "logs": json.dumps(job["logs"]),
                            "summaries": json.dumps([{"dataset_type": "snv", "status": "running"}]),
                        },
                    )
                ).scalar_one()
            await s.commit()

        mine = {job["id"]: name for name, job in jobs.items()}
        claimed: dict[str, dict] = {}
        for _ in range(20):
            async with sm() as s:
                row = await claim_next_family_import_job(s, worker_id=worker)
            if row is None:
                break
            if row["id"] in mine:
                claimed[mine[row["id"]]] = row
            if len(claimed) == 3:
                break

        assert set(claimed) == {"running", "validating", "queued"}

        ended = claimed["running"]
        assert ended["claimed_from"] == "running"
        assert (ended["status"], ended["worker_id"], ended["error"]) == (
            "failed",
            None,
            FAMILY_IMPORT_INTERRUPTED_ERROR,
        )
        assert ended["completed_at"] is not None
        # Its record stays: the earlier lines, then why it ended.
        assert ended["logs"][0] == "Registering family metadata."
        assert ended["logs"][1].startswith("The import stopped part-way")
        assert ended["dataset_summaries"] == [{"dataset_type": "snv", "status": "running"}]

        again = claimed["validating"]
        assert (again["claimed_from"], again["status"], again["worker_id"]) == (
            "validating",
            "validating",
            worker,
        )
        assert again["error"] is None and again["completed_at"] is None
        assert again["logs"][0] == "Validated package path."
        assert again["logs"][1].startswith("The worker running this job stopped")

        fresh = claimed["queued"]
        assert (fresh["claimed_from"], fresh["status"], fresh["worker_id"]) == ("queued", "validating", worker)
        assert fresh["logs"] == []

        async with sm() as s:
            live = (
                await s.execute(
                    text("SELECT status, worker_id FROM family_import_jobs WHERE id = CAST(:j AS uuid)"),
                    {"j": jobs["live"]["id"]},
                )
            ).one()
            assert tuple(live) == ("running", "w-gone"), "a live import is left alone"
            # The heartbeat is the job's worker's to beat, while the job has not ended.
            assert await _beat_family_import_job(s, job_id=again["id"], worker_id=worker) is True
            assert await _beat_family_import_job(s, job_id=again["id"], worker_id="w-other") is False
            assert await _beat_family_import_job(s, job_id=ended["id"], worker_id=worker) is False
            await s.execute(
                text("DELETE FROM family_import_jobs WHERE id::text = ANY(:ids)"),
                {"ids": list(mine)},
            )
            await s.commit()

    _run(scenario)
