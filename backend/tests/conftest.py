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
