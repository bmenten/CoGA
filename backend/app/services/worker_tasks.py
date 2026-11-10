"""Stopping a background worker: the loops the API and the import-worker process run beside
their requests (the gene-reference refresh, the package imports, the ancestry PCA, the import
watcher; ``main.lifespan`` and ``import_worker``).

Each module names its own stop (``stop_gene_reference_worker``, ...), which is this one: the
start and its tests call each worker's by that name.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any


async def stop_worker(task: asyncio.Task[Any] | None, stop_event: asyncio.Event | None) -> None:
    """Stop a worker: set its stop event, cancel its task and wait until the task has ended,
    its cancellation swallowed. Either may be None, for a worker that never started."""
    if stop_event is not None:
        stop_event.set()
    if task is None:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
