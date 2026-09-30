"""The ClickHouse integrity check on real variant data (golden trio).

The check runs ``CHECK TABLE`` on each variant table and must judge every active part.
clickhouse-connect runs ``CHECK`` as a command and returns its tab-separated output split
on tabs only: a table with several parts comes back as one flattened row, and a table
with no parts as one empty value. Read row by row, a table was judged on its first part
alone, and a table without rows raised ``IndexError``, so the admin endpoint answered 500
and the scheduled sweep logged a failure instead of a status for that assembly.

This imports the golden trio through the real pipeline (every variant table then holds
several parts) and creates the tables of a second assembly that holds no data yet, the
state of an assembly set up before its first import. Then:

* the check's part-by-part reading covers every active part of each imported table;
* the admin endpoint reports the imported assembly as ``ok``, each table passed;
* the assembly without data is checked too, each table passed, not a 500;
* the scheduled sweep reports a status for both, and logs no failed check.

The second assembly's tables are dropped at the end. Everything runs in one event loop
through an in-process ``httpx.ASGITransport`` client (see test_e2e_api_contract.py).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_FIXTURE = Path(__file__).parent / "fixtures" / "golden_trio"
_ASSEMBLY = "GRCh38"
# An assembly no other test uses: its tables are created, and nothing is imported into it.
_NO_DATA_ASSEMBLY = "E2EINTEGRITYNODATA"
_MONITOR_LOGGER = "backend.app.services.clickhouse_integrity_monitor"


def _cap(resp) -> dict:
    out = {"status": resp.status_code, "text": resp.text}
    if resp.status_code < 400:
        out["json"] = resp.json()
    return out


class _Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


async def _exercise(root: Path) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.core.config import settings
    from backend.app.main import app
    from backend.app.services import clickhouse_variant_storage as cvs
    from backend.app.services.clickhouse_integrity_monitor import run_integrity_sweep
    from backend.tests.e2e import _harness

    facts = await _harness.import_golden_trio(root)
    await cvs.ensure_clickhouse_variant_tables(_NO_DATA_ASSEMBLY)
    database = settings.clickhouse_database
    try:
        tables = [
            name for _type, kind, name in cvs._expected_clickhouse_variant_tables(_ASSEMBLY) if kind == "table"
        ]
        active = dict(
            await execute_clickhouse(
                "SELECT table, count() FROM system.parts WHERE database = %(db)s AND active "
                "GROUP BY table",
                {"db": database},
            )
        )
        # The check's own reading of the real CHECK TABLE output, table by table.
        read = {
            name: await cvs._check_table_parts(f"{database}.`{name}`") for name in tables
        }

        # raise_app_exceptions=False: a crash in the check must show as the 500 an admin
        # would get, not abort the fixture.
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
            token = await _harness.login_admin_token(ac)
            ac.headers["Authorization"] = f"Bearer {token}"
            imported = _cap(await ac.get(f"/api/admin/clickhouse/variants/{_ASSEMBLY}/integrity"))
            no_data = _cap(
                await ac.get(f"/api/admin/clickhouse/variants/{_NO_DATA_ASSEMBLY}/integrity")
            )

        handler = _Records()
        monitor_logger = logging.getLogger(_MONITOR_LOGGER)
        previous_level = monitor_logger.level
        monitor_logger.addHandler(handler)
        monitor_logger.setLevel(logging.DEBUG)
        try:
            await run_integrity_sweep()
        finally:
            monitor_logger.removeHandler(handler)
            monitor_logger.setLevel(previous_level)
        sweep = [(record.levelname, record.getMessage()) for record in handler.records]
    finally:
        for (name,) in await execute_clickhouse(
            "SELECT name FROM system.tables WHERE database = %(db)s AND name LIKE %(prefix)s",
            {"db": database, "prefix": f"{_NO_DATA_ASSEMBLY}/%"},
        ):
            await execute_clickhouse(f"DROP TABLE IF EXISTS {database}.`{name}` SYNC")
        cvs._ensured_variant_table_assemblies.discard(_NO_DATA_ASSEMBLY)

    return {
        "facts": facts,
        "tables": tables,
        "active": {name: int(active.get(name, 0)) for name in tables},
        "read": read,
        "imported": imported,
        "no_data": no_data,
        "sweep": sweep,
    }


@pytest.fixture(scope="module")
def run(tmp_path_factory, request) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    if not (_FIXTURE / "manifest.yaml").exists():
        pytest.fail("golden_trio fixture missing: it is committed, so restore it (scripts/generate_golden_trio.py rebuilds it)")

    root = tmp_path_factory.mktemp("golden_integrity") / "FAM_TRIO"
    shutil.copytree(_FIXTURE, root)
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(root.parent)])
    request.addfinalizer(mp.undo)

    out = _harness.run_async(lambda: _exercise(root))
    assert out["facts"]["completed"] is True, out["facts"]
    return out


def test_the_check_reads_every_part_of_the_imported_tables(run) -> None:
    # The import leaves several parts in a table: the shape the driver flattens.
    assert max(run["active"].values()) >= 2, run["active"]
    for name in run["tables"]:
        parts = run["read"][name]
        assert parts is not None, f"{name}: CHECK TABLE output could not be read"
        assert len(parts) == run["active"][name], (name, parts, run["active"][name])
        assert all(ok for _path, ok, _message in parts), (name, parts)


def test_the_imported_assembly_passes_the_check(run) -> None:
    resp = run["imported"]
    assert resp["status"] == 200, resp["text"]
    report = resp["json"]
    assert report["status"] == "ok", report
    checks = {check["name"]: check for check in report["table_checks"]}
    assert set(checks) == set(run["tables"])
    for name, check in checks.items():
        assert check["exists"] is True, check
        assert (check["passed"], check["failed_parts"], check["messages"]) == (True, 0, []), check


def test_an_assembly_without_data_is_checked_not_crashed(run) -> None:
    resp = run["no_data"]
    assert resp["status"] == 200, resp["text"]
    report = resp["json"]
    assert report["status"] == "ok", report
    assert report["table_checks"], report
    for check in report["table_checks"]:
        assert (check["exists"], check["passed"], check["failed_parts"]) == (True, True, 0), check


def test_the_scheduled_sweep_reports_both_assemblies(run) -> None:
    messages = [message for _level, message in run["sweep"]]
    failed = [message for level, message in run["sweep"] if "could not" in message or "failed" in message]
    assert failed == [], run["sweep"]
    for assembly in (_ASSEMBLY, _NO_DATA_ASSEMBLY):
        assert f"ClickHouse variant integrity ok for {assembly}" in messages, run["sweep"]
