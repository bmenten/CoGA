import os
from pathlib import Path
import sys

import pytest

os.environ.setdefault("APP_ENV", "test")

# A unit run reaches no database (ENG-16). The suite mocks Postgres and ClickHouse, but a
# route test that leaves the request audit on writes its row itself when no worker runs,
# to whatever Postgres listens on localhost:5432: the dev stack publishes its database
# there, whose request audit is append-only. Without RUN_INTEGRATION=1 both datastores
# are a closed loopback port, so such a write fails at once. The environment outranks the
# repository's .env, which holds the dev stack's credentials.
UNIT_RUN_DATASTORE = ("127.0.0.1", "1")
if os.environ.get("RUN_INTEGRATION") != "1":
    os.environ["POSTGRES_HOST"], os.environ["POSTGRES_PORT"] = UNIT_RUN_DATASTORE
    os.environ["CLICKHOUSE_HOST"], os.environ["CLICKHOUSE_HTTP_PORT"] = UNIT_RUN_DATASTORE
    os.environ["POSTGRES_USE_CLOUD_SQL_CONNECTOR"] = "false"

ROOT_DIR = Path(__file__).resolve().parent

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


@pytest.fixture(autouse=True)
def _reset_process_memoization_caches():
    """Reset per-process schema/probe memoization caches between tests so their
    module-level state does not leak. These caches make DDL/probes run once per
    process in production, but several tests assert the first-call behaviour."""
    from backend.app.services import clickhouse_interval_tracks, hpo_service

    hpo_service._pg_tables_confirmed_available.clear()
    clickhouse_interval_tracks._ensured_interval_table_assemblies.clear()
    yield
