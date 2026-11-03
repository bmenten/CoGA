"""The import-worker process as test_e2e_import_worker_process.py starts it when it needs it
faster or held: ``python -m backend.tests.e2e._import_worker_process``.

It runs the process's own ``main()`` (``backend/app/import_worker.py``) after changing, in
this process only:

* the worker's poll and the import's heartbeat, from 2 s and a minute to a fraction of a
  second, so that a queued job is taken at once and a job another worker ended is noticed at
  once;
* with ``E2E_IMPORT_PAUSE_DIR`` set, a pause inside each SNV dataset. The loader flushes every
  2 variants; once its first flush is stored, it creates ``inside`` in that folder and waits
  until ``go`` is there. Meanwhile the import holds its family's variant-write locks, and its
  heartbeat runs, as while it reads a large file.

Synthetic data only, as every e2e test.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from backend.app import import_worker
from backend.app.services import family_package_import as package_import
from backend.app.services import variant_upload_service

package_import.FAMILY_IMPORT_WORKER_POLL_SECONDS = 0.1
package_import.FAMILY_IMPORT_HEARTBEAT_SECONDS = 0.2

_pause = os.environ.get("E2E_IMPORT_PAUSE_DIR")
if _pause:
    pause = Path(_pause)
    real_insert = variant_upload_service.insert_small_variant_records
    flushed: list[int] = []

    async def insert(*args: Any, **kwargs: Any) -> Any:
        if flushed:
            (pause / "inside").touch()
            while not (pause / "go").exists():
                await asyncio.sleep(0.05)
        result = await real_insert(*args, **kwargs)
        flushed.append(1)
        return result

    variant_upload_service.SMALL_VARIANT_UPLOAD_BATCH_SIZE = 2
    variant_upload_service.insert_small_variant_records = insert  # type: ignore[assignment]

raise SystemExit(import_worker.main())
