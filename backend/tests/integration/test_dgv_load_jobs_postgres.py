"""A DGV load against real Postgres: one at a time, committed whole or not at all.

``dgv_load_jobs`` and its run are SQL the unit tests (``test_dgv_load_jobs.py``) stand in for.
Against the real schema, with DGV served by a stand-in, this checks that

* a load is refused (409) while another is queued or running, and accepted once none is; a
  load a start queues is no account's;
* a load run to its end replaces the assembly's DGV variants with the file's rows, records the
  import with the release, the file and its SHA-256, and ends ``completed``;
* a load whose file is not the pinned one ends ``failed``, saying why, with the DGV variants
  from before untouched: the rows it had inserted are rolled back with the deletion.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import text

from backend.app.core.config import DgvPinnedRelease
from backend.tests.integration._datastores import fresh_assembly, fresh_species

pytestmark = pytest.mark.integration

_HEADER = (
    "variantaccession\tchr\tstart\tend\tvarianttype\tvariantsubtype\treference\tpubmedid\tmethod\t"
    "platform\tmergedvariants\tsupportingvariants\tmergedorsample\tfrequency\tsamplesize\t"
    "observedgains\tobservedlosses\tcohortdescription\tgenes\tsamples"
)
_DONE = "status = 'completed', worker_id = NULL, completed_at = now()"


def _dgv_file(rows: int) -> bytes:
    lines = [_HEADER] + [
        f"nsv{index:07d}\t{1 + index % 22}\t{10_000 + index}\t{20_000 + index}\tCNV\tdeletion\tStudy\t1\t"
        f"Sequencing\t\t\tnssv{index}\tM\t\t100\t0\t1\t\t\"\"\t"
        for index in range(rows)
    ]
    return ("\n".join(lines) + "\n").encode()


@pytest.fixture()
def dgv(monkeypatch: pytest.MonkeyPatch):
    from backend.app.services import dgv_load_jobs as dgv

    return dgv


def _with_assembly(dgv, monkeypatch: pytest.MonkeyPatch, payload: bytes, test) -> None:
    """Run ``test(sm, assembly, assembly_id)`` against the loaded schema, on an assembly of its
    own with a pinned DGV release of ``payload``, served by a stand-in for DGV."""
    from backend.app.core.postgres import close_postgres_engine, get_postgres_sessionmaker, init_postgres_schema

    name = f"dgv-{uuid4()}"
    url = f"https://dgv.example/dgv/docs/{name}_variants_2025-12-01.txt"
    pin = DgvPinnedRelease("2025-12-01", url, hashlib.sha256(payload).hexdigest(), len(payload))
    monkeypatch.setattr(dgv, "DGV_PINNED_RELEASES", {name: pin})
    monkeypatch.setattr(dgv, "_start", lambda job_id: None)  # each test runs the job itself
    real_load = dgv.load_dgv_release
    served = {"payload": payload}

    async def load_from_stand_in(session: Any, **kwargs: Any) -> tuple[int, str]:
        transport = httpx.MockTransport(lambda request: httpx.Response(200, content=served["payload"]))
        async with httpx.AsyncClient(transport=transport, event_hooks={"request": [dgv._https_only]}) as client:
            return await real_load(session, client=client, **kwargs)

    monkeypatch.setattr(dgv, "load_dgv_release", load_from_stand_in)

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            async with sm() as s:
                species = await fresh_species(s, name)
                assembly_id = await fresh_assembly(s, species, name)
                # The rule is global and the integration database is shared: no active load.
                await s.execute(text("DELETE FROM dgv_load_jobs WHERE status IN ('queued', 'running')"))
                await s.commit()
            try:
                await test(sm, name, assembly_id, served)
            finally:
                async with sm() as s:
                    await s.execute(text("DELETE FROM species WHERE id = CAST(:s AS uuid)"), {"s": species})
                    await s.commit()
        finally:
            await close_postgres_engine()

    asyncio.run(_run())


async def _queue(dgv, sm, assembly: str):
    async with sm() as s:
        return await dgv.queue_dgv_load(s, assembly=assembly, requested_by=None)


async def _job(sm, job_id: str) -> dict[str, Any]:
    async with sm() as s:
        row = (
            await s.execute(
                text("SELECT status, error, inserted, requested_by, requested_by_id FROM dgv_load_jobs WHERE id = CAST(:j AS uuid)"),
                {"j": job_id},
            )
        ).mappings().one()
    return dict(row)


async def _dgv_rows(sm, assembly_id: str) -> list[str]:
    async with sm() as s:
        return list(
            (
                await s.execute(
                    text("SELECT accession FROM dgv_variants WHERE assembly_id = CAST(:a AS uuid) ORDER BY accession"),
                    {"a": assembly_id},
                )
            ).scalars()
        )


def test_one_load_at_a_time(dgv, monkeypatch: pytest.MonkeyPatch) -> None:
    async def test(sm, assembly: str, _assembly_id: str, _served: dict) -> None:
        first = await _queue(dgv, sm, assembly)
        with pytest.raises(HTTPException) as refused:
            await _queue(dgv, sm, assembly)
        assert refused.value.status_code == 409

        claimed = await dgv._claim_job(str(first.id), "worker-a")
        assert claimed is not None
        # A start's load is no account's.
        assert (claimed["requested_by"], claimed["requested_by_id"]) == (dgv.STARTUP_REQUESTER, None)
        with pytest.raises(HTTPException):
            await _queue(dgv, sm, assembly)

        assert await dgv._update_job(str(first.id), "worker-a", _DONE, {}) is True
        second = await _queue(dgv, sm, assembly)
        assert second.status == "queued" and second.id != first.id

    _with_assembly(dgv, monkeypatch, _dgv_file(3), test)


def test_a_complete_load_replaces_the_dgv_variants_and_is_recorded(dgv, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _dgv_file(45)

    async def test(sm, assembly: str, assembly_id: str, _served: dict) -> None:
        async with sm() as s:  # the DGV variants from before, which the load replaces
            await s.execute(
                text("INSERT INTO dgv_variants (assembly_id, chr, start, \"end\", accession) VALUES (CAST(:a AS uuid), '1', 1, 2, 'old')"),
                {"a": assembly_id},
            )
            await s.commit()
        job = await _queue(dgv, sm, assembly)
        await dgv._run_job(str(job.id))

        row = await _job(sm, str(job.id))
        assert (row["status"], row["inserted"], row["error"]) == ("completed", 45, None)
        assert await _dgv_rows(sm, assembly_id) == [f"nsv{index:07d}" for index in range(45)]
        async with sm() as s:
            record = (
                await s.execute(
                    text(
                        "SELECT inserted, replaced, source, source_url, source_version, source_release_date, performed_by "
                        "FROM reference_dataset_imports WHERE assembly_id = CAST(:a AS uuid) AND dataset_type = 'dgv'"
                    ),
                    {"a": assembly_id},
                )
            ).mappings().one()
        digest = hashlib.sha256(payload).hexdigest()
        assert (record["inserted"], record["replaced"], record["source"]) == (45, True, "dgv")
        assert record["source_version"] == f"2025-12-01 ({assembly}_variants_2025-12-01.txt, SHA-256 {digest})"
        assert str(record["source_release_date"]) == "2025-12-01"
        assert record["performed_by"] == dgv.STARTUP_REQUESTER

    _with_assembly(dgv, monkeypatch, payload, test)


def test_a_file_that_is_not_the_pinned_one_leaves_the_dgv_variants_untouched(dgv, monkeypatch: pytest.MonkeyPatch) -> None:
    pinned = _dgv_file(20)

    async def test(sm, assembly: str, assembly_id: str, served: dict) -> None:
        job = await _queue(dgv, sm, assembly)
        await dgv._run_job(str(job.id))  # the pinned file: 20 rows
        assert len(await _dgv_rows(sm, assembly_id)) == 20

        # DGV now serves another file of the same size: its rows are inserted as it arrives,
        # then refused with the deletion before them.
        served["payload"] = pinned.replace(b"Study", b"Stuff")
        again = await _queue(dgv, sm, assembly)
        await dgv._run_job(str(again.id))

        row = await _job(sm, str(again.id))
        assert row["status"] == "failed" and "not the pinned release 2025-12-01" in row["error"]
        assert "Nothing was loaded" in row["error"]
        assert await _dgv_rows(sm, assembly_id) == [f"nsv{index:07d}" for index in range(20)]
        async with sm() as s:
            sources = (
                await s.execute(
                    text("SELECT DISTINCT source FROM dgv_variants WHERE assembly_id = CAST(:a AS uuid)"),
                    {"a": assembly_id},
                )
            ).scalars().all()
        assert list(sources) == ["Study"]

    _with_assembly(dgv, monkeypatch, pinned, test)
