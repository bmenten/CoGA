from pathlib import Path
import sys


BACKEND_DIR = Path(__file__).resolve().parents[1]

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _managed_files_in_tmp(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """A unit test that stores a managed copy (the manifest an import keeps, an uploaded file)
    writes it under a temporary folder, never into the repository's ``data/``."""
    if {"e2e", "integration"} & set(Path(str(request.node.fspath)).parts):
        return
    from backend.app.services import raw_import_files_pg

    monkeypatch.setattr(raw_import_files_pg, "DATA_DIR", tmp_path_factory.mktemp("managed-data"))


@pytest.fixture(autouse=True)
def _variant_carrier_summary_left_alone(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """A unit test of a writer of a family's variant rows talks to no ClickHouse: the hook
    that first takes the family's contribution out of the Variant Explorer's carrier summary
    does nothing. The summary's own tests (``test_variant_carrier_summary.py``) call it."""
    path = Path(str(request.node.fspath))
    if {"e2e", "integration"} & set(path.parts) or path.name == "test_variant_carrier_summary.py":
        return
    from backend.app.services import clickhouse_family_snapshot, clickhouse_variant_storage

    async def left_alone(*_args: object, **_kwargs: object) -> None:
        return None

    # Some tests import the backend as ``app`` (the backend folder on sys.path): the same
    # modules under a second name.
    modules = [clickhouse_variant_storage, clickhouse_family_snapshot] + [
        sys.modules[name]
        for name in ("app.services.clickhouse_variant_storage", "app.services.clickhouse_family_snapshot")
        if name in sys.modules
    ]
    for module in modules:
        monkeypatch.setattr(module, "detach_family_variant_carriers", left_alone)
