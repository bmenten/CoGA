"""The family-package import workers in a process of their own, without the API.

Run from the backend image as ``python -m app.import_worker`` (from the repository root,
``python -m backend.app.import_worker``). It runs ``FAMILY_IMPORT_WORKER_COUNT`` workers
(``family_package_import_worker``), each taking one queued import job at a time, until it is
sent SIGTERM or SIGINT. It serves nothing and listens on no port.

The API's process runs its own workers on the event loop that answers requests, and a Python
process uses one core however many workers it runs: every import shared that core with the
other imports and with the requests (docs/application-scheme.md, "Performance"). Several of
these processes beside an API that runs none (``FAMILY_IMPORT_WORKER_COUNT=0``) import that
many families at once, each on a core of its own, and leave the API's loop to the requests.

What the workers of all processes share runs through Postgres, so it holds across processes
and hosts as it does across the workers of one: the claim of a job (``FOR UPDATE SKIP
LOCKED``), its heartbeat, stamped and judged stale by the database's clock, the end of a job
whose heartbeat went stale, and the family's variant-write locks, which are advisory locks of
the import's own connection (``family_variant_write_lock``; the ContextVar there only spares
the import's own tasks from taking the locks they hold already).

It also runs the worker of the exploratory ancestry PCA (``ancestry_service``), whose jobs parse
a whole-genome VCF in Python: work of the same kind, kept off the API's loop for the same reason.

Its start checks what the API's start checks before a worker runs: the database is at this
version's schema revision (it never migrates: the API or the migration job does that, as the
tables' owner), and no variant table has an older row identity. It then drops the backup
tables of overwrite imports that no running import owns, left by an import whose process
stopped. On SIGTERM or SIGINT it stops its workers as the API's shutdown does: an import that
is running stops where it is, and once its heartbeat is stale the next worker to look ends its
job as interrupted, or runs it again when it had written nothing (docs/data-import.md, "The
import job"). It then closes its database clients and exits 0.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
import signal
import sys

from .core.clickhouse import close_clickhouse_client, init_clickhouse_schema, wait_for_clickhouse
from .core.coga_logging import configure_json_logging, install_event_loop_exception_handler
from .core.config import settings
from .core.postgres import close_postgres_engine, verify_postgres_schema_revision, wait_for_postgres
from .services.ancestry_service import ancestry_worker, stop_ancestry_worker
from .services.clickhouse_variant_storage import verify_clickhouse_variant_storage_identity
from .services.family_package_import import (
    drop_orphaned_import_backups,
    family_package_import_worker,
    stop_family_package_import_worker,
)

logger = logging.getLogger(__name__)

# docker stop sends SIGTERM; Ctrl-C in a terminal, SIGINT.
STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT)
# The exit codes: stopped by a signal; stopped on an error (a failed start, or a worker that
# ended by itself), so that the process is started again; refused its configuration.
EXIT_STOPPED = 0
EXIT_FAILED = 1
EXIT_REFUSED = 2


async def start_import_worker_process() -> None:
    """What must hold before a worker claims a job, checked as the API's start checks it."""
    await wait_for_postgres()
    await verify_postgres_schema_revision()
    await wait_for_clickhouse()
    await init_clickhouse_schema()
    await verify_clickhouse_variant_storage_identity()
    await drop_orphaned_import_backups()


async def run_import_workers(count: int, stop: asyncio.Event) -> int:
    """Start, then run ``count`` workers until ``stop`` is set; stop them and close the
    database clients either way.

    Returns ``EXIT_STOPPED`` once stopped by ``stop``, also during the start, and
    ``EXIT_FAILED`` when a worker ended by itself, which only an error its loop does not catch
    makes it do. A start that fails raises.
    """
    install_event_loop_exception_handler()
    stopping = asyncio.create_task(stop.wait())
    workers: list[asyncio.Task[None]] = []
    ancestry: asyncio.Task[None] | None = None
    try:
        start = asyncio.create_task(start_import_worker_process())
        await asyncio.wait({start, stopping}, return_when=asyncio.FIRST_COMPLETED)
        if not start.done():
            # Stopped while it waited for a database: nothing has been claimed yet.
            start.cancel()
            with suppress(asyncio.CancelledError):
                await start
            logger.info("Import worker process stopped before its start completed")
            return EXIT_STOPPED
        start.result()
        workers = [asyncio.create_task(family_package_import_worker(stop)) for _ in range(count)]
        ancestry = asyncio.create_task(ancestry_worker(stop))
        logger.info("Import worker process started with %d package-import worker(s)", count)
        done, _ = await asyncio.wait({stopping, ancestry, *workers}, return_when=asyncio.FIRST_COMPLETED)
        if stopping in done:
            logger.info("Import worker process stopping")
            return EXIT_STOPPED
        if ancestry in done:
            problem = None if ancestry.cancelled() else ancestry.exception()
            logger.error("The ancestry worker ended by itself; the import worker process stops", exc_info=problem)
        for ended in [worker for worker in workers if worker in done]:
            workers.remove(ended)
            problem = None if ended.cancelled() else ended.exception()
            logger.error(
                "A package-import worker ended by itself; the import worker process stops",
                exc_info=problem,
            )
        return EXIT_FAILED
    finally:
        stopping.cancel()
        if ancestry is not None:
            await stop_ancestry_worker(ancestry, stop)
        for worker in workers:
            await stop_family_package_import_worker(worker, stop)
        await close_clickhouse_client()
        await close_postgres_engine()


async def _serve(count: int) -> int:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in STOP_SIGNALS:
        loop.add_signal_handler(signum, stop.set)
    try:
        return await run_import_workers(count, stop)
    except Exception:
        # Written by its kind and frames, without its text (core/coga_logging.py); a refused
        # schema revision has logged its message itself.
        logger.exception("Import worker process could not start")
        return EXIT_FAILED
    finally:
        for signum in STOP_SIGNALS:
            loop.remove_signal_handler(signum)


def main() -> int:
    configure_json_logging()
    count = settings.family_import_worker_count
    if count < 1:
        logger.error(
            "FAMILY_IMPORT_WORKER_COUNT is 0: an import worker process runs 1 to 8 "
            "package-import workers"
        )
        return EXIT_REFUSED
    return asyncio.run(_serve(count))


if __name__ == "__main__":
    sys.exit(main())
