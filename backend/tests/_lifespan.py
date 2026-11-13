"""The backend's start (``backend.app.main.lifespan``) with its steps replaced, for a test that
runs one or two of them for real and touches no database.

``stub_lifespan_steps`` replaces every step but those a test keeps with one that records its
name, and refuses to run when the lifespan awaits a step this list does not name: a step added
to the start would otherwise run for real in every such test, against the closed datastore
port of a unit run.
"""

from __future__ import annotations

import inspect
import re
from typing import Any

import pytest

from backend.app import main as app_main

# Each step the lifespan calls by its name in ``backend.app.main``, the HPO bootstrap apart
# (``ensure_hpo_ontology_on_startup``, which the tests that use this run for real).
LIFESPAN_STEPS = (
    "wait_for_postgres",
    "init_postgres_schema",
    "init_postgres_admin_user",
    "verify_postgres_schema_revision",
    "start_audit_log_worker",
    "start_ui_event_worker",
    "seed_builtin_repeat_catalog",
    "ensure_human_grch38_reference_on_startup",
    "ensure_human_t2t_reference_on_startup",
    "queue_startup_reference_loads",
    "ensure_monarch_on_startup",
    "seed_builtin_reference_tracks",
    "queue_startup_gene_reference_refresh_if_needed",
    "wait_for_clickhouse",
    "init_clickhouse_schema",
    "drop_orphaned_import_backups",
    "start_clickhouse_integrity_monitor",
    "gene_reference_refresh_worker",
    "family_package_import_worker",
    "ancestry_worker",
    "variant_carrier_worker",
    "import_watcher",
    "stop_gene_reference_worker",
    "stop_family_package_import_worker",
    "stop_import_watcher",
    "stop_ancestry_worker",
    "stop_variant_carrier_worker",
    "stop_clickhouse_integrity_monitor",
    "stop_audit_log_worker",
    "stop_ui_event_worker",
    "close_clickhouse_client",
    "close_postgres_engine",
)
_RUN_FOR_REAL = {"ensure_hpo_ontology_on_startup"}


class LifespanApp:
    """The app the lifespan is given: it reads ``app.state.skip_startup_tasks`` alone."""

    class state:  # FastAPI's app.state, as the lifespan reads it
        skip_startup_tasks = False


def stub_lifespan_steps(monkeypatch: pytest.MonkeyPatch, *, keep: tuple[str, ...] = ()) -> list[str]:
    """Replace each step of the start but those in ``keep`` with one that records its name, in
    the list returned."""
    awaited = set(re.findall(r"await (\w+)\(", inspect.getsource(app_main.lifespan)))
    unnamed = sorted(awaited - set(LIFESPAN_STEPS) - _RUN_FOR_REAL)
    assert not unnamed, f"the start awaits steps backend/tests/_lifespan.py does not name: {unnamed}"
    calls: list[str] = []

    def _step(name: str):
        async def _run(*_args: Any, **_kwargs: Any) -> None:
            calls.append(name)

        return _run

    for name in LIFESPAN_STEPS:
        if name not in keep:
            monkeypatch.setattr(app_main, name, _step(name))
    return calls
