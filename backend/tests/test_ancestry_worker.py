"""The ancestry worker's loop, and what the ancestry page says when a family cannot be calculated."""
import ast
import asyncio
import inspect
import textwrap

import pytest


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


def test_every_reason_a_source_is_not_ready_has_a_sentence_for_the_page():
    from backend.app.services import ancestry_service, wgs_source_storage

    tree = ast.parse(textwrap.dedent(inspect.getsource(wgs_source_storage.source_state)))
    returned = {
        node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant)
    }
    assert "ready" in returned and len(returned) > 5
    assert returned - {"ready"} == set(ancestry_service.STATE_NOTICES)


@pytest.mark.parametrize("family_metadata,rows,refused", [
    ({}, [{"role": "proband", "sample_metadata": {}}, {"role": "mother", "sample_metadata": None}], False),
    ({"analysis_type": "monogenic_nipt"}, [{"role": "father", "sample_metadata": {}}], True),
    ({}, [{"role": "mother", "sample_metadata": {}}, {"role": "Embryo", "sample_metadata": {}}], True),
    ({}, [{"role": "mother", "sample_metadata": {"assay": "nipt_cfdna"}}], True),
])
def test_a_projection_reads_individuals_only(family_metadata, rows, refused):
    from types import SimpleNamespace

    from backend.app.services.wgs_source_storage import _has_non_individual_samples

    assert _has_non_individual_samples(family_metadata, SimpleNamespace(sample_rows=rows)) is refused
