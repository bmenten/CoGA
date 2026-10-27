"""A unit run reaches no database (ENG-16).

The suite mocks Postgres and ClickHouse. A route test that leaves the request audit on writes
its row itself when no worker runs, so it wrote to whatever Postgres listened on
localhost:5432, with the credentials of the repository's .env: the dev stack publishes its
database there, and its request audit is append-only. The repository conftest points a run
without RUN_INTEGRATION=1 at a closed loopback port for both datastores.
"""

from __future__ import annotations

import os
import socket

import pytest

from backend.app.core.config import settings


def _closed(host: str, port: int) -> bool:
    """Whether nothing accepts a connection at ``host``:``port``."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1)
        return probe.connect_ex((host, port)) != 0


def test_a_unit_run_reaches_no_database() -> None:
    if os.environ.get("RUN_INTEGRATION") == "1":
        pytest.skip("an integration run names its own datastores")
    for name, host, port in (
        ("Postgres", settings.postgres_host, settings.postgres_port),
        ("ClickHouse", settings.clickhouse_host, settings.clickhouse_http_port),
    ):
        assert host == "127.0.0.1", f"a unit run's {name} is {host}:{port}"
        assert _closed(host, port), f"something listens at a unit run's {name}, {host}:{port}"
    assert settings.postgres_use_cloud_sql_connector is False, "the Cloud SQL connector names its own host"
