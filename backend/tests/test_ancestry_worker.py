"""The ancestry worker's loop, and what the ancestry page says when a family cannot be calculated."""
import ast
import asyncio
import inspect
import textwrap


def test_ancestry_worker_sweeps_for_changed_families_then_processes_jobs_and_stops_cleanly(monkeypatch):
    from backend.app.services import ancestry_service as service

    calls = []

    async def exercise():
        stop = asyncio.Event()

        async def sweep():
            calls.append("sweep")
            return 1

        async def process():
            calls.append("run")
            if calls.count("run") == 2:
                stop.set()
            return True

        monkeypatch.setattr(service, "sweep_changed_families", sweep)
        monkeypatch.setattr(service, "process_next_run", process)
        await service.ancestry_worker(stop)

    asyncio.run(exercise())
    # One sweep per interval, not one per job.
    assert calls == ["sweep", "run", "run"]


def test_a_failing_sweep_does_not_stop_the_worker(monkeypatch):
    from backend.app.services import ancestry_service as service

    calls = []

    async def exercise():
        stop = asyncio.Event()

        async def sweep():
            calls.append("sweep")
            raise RuntimeError("storage unavailable")

        async def process():
            calls.append("run")
            stop.set()
            return False

        monkeypatch.setattr(service, "sweep_changed_families", sweep)
        monkeypatch.setattr(service, "process_next_run", process)
        await service.ancestry_worker(stop)

    asyncio.run(exercise())
    assert calls == ["sweep", "run"]


def test_every_reason_a_family_is_not_ready_has_a_sentence_for_the_page():
    from backend.app.services import ancestry_service

    tree = ast.parse(textwrap.dedent(inspect.getsource(ancestry_service.family_state)))
    returned = {
        node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant)
    }
    assert "ready" in returned
    assert returned - {"ready"} == set(ancestry_service.STATE_NOTICES)
