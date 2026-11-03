"""Standalone ancestry worker and removal of the PGS HTTP interface."""
import asyncio

from fastapi.testclient import TestClient


def test_ancestry_worker_processes_jobs_and_stops_cleanly(monkeypatch):
    from backend.app.services import ancestry_service as service

    calls = []

    async def exercise():
        stop = asyncio.Event()

        async def process():
            calls.append('ancestry')
            stop.set()
            return True

        monkeypatch.setattr(service, 'process_next_run', process)
        await service.ancestry_worker(stop)

    asyncio.run(exercise())
    assert calls == ['ancestry']


def test_local_pgs_api_and_contracts_are_absent():
    from backend.app.main import app

    schema = app.openapi()
    assert not any('pgs' in path for path in schema['paths'])
    assert not any(name.startswith('Pgs') or name == 'FamilyPgsOut' for name in schema['components']['schemas'])
    # No lifespan: this read-only route-removal test needs no datastores or jobs.
    client = TestClient(app)
    for path in ('/api/pgs/models', '/api/admin/pgs/catalog-imports', '/api/families/SYNTHETIC/pgs'):
        assert client.get(path).status_code == 404
