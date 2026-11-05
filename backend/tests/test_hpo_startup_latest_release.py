"""A start on an empty database imports HPO's newest release; the vendored release is the backup.

The start imported the vendored data/ref-data/hpo/hp.obo, so a new instance began on a release
that could be months old (owner decision, 2026-11-05: download the newest by default, the local
file only as a backup). It now reads which release HPO published last and downloads it from the
release's own PURL, as the admin sync does. The file is imported only if it states that release;
the terms keep the release and the SHA-256 of the bytes imported (TRACE-18). When HPO cannot be
reached, or what it serves is refused, or HPO_DOWNLOAD_LATEST_ON_STARTUP is off, the start imports
the vendored file, held to its pinned SHA-256 (TRACE-14, ``test_hpo_download_pin.py``).

Nothing here goes to the network: HPO's servers are an httpx mock transport, and the backup
download's urlopen is replaced.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import shutil
from datetime import date
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

from backend.app import main as app_main
from backend.app.core import config
from backend.app.core.config import Settings
from backend.app.services import hpo_release_download as release_download
from backend.app.services import hpo_service
from backend.tests._lifespan import LifespanApp, stub_lifespan_steps

_VENDORED_HPO = Path(__file__).resolve().parents[2] / "data" / "ref-data" / "hpo" / "hp.obo"
_NEWEST = date(2026, 9, 1)
_NEWEST_URL = "https://purl.obolibrary.org/obo/hp/releases/2026-09-01/hp.obo"
_NEWEST_OBO = (
    b"format-version: 1.2\ndata-version: hp/releases/2026-09-01\nontology: hp\n\n"
    b"[Term]\nid: HP:0000001\nname: All\n\n"
    b"[Term]\nid: HP:0000118\nname: Phenotypic abnormality\nis_a: HP:0000001 ! All\n"
)


def _latest_answer(tag: str = "v2026-09-01") -> bytes:
    return json.dumps({"tag_name": tag, "published_at": "2026-09-03T06:08:39Z", "assets": []}).encode()


@pytest.fixture(autouse=True)
def _fresh_cache():
    release_download.clear_latest_release_cache()
    yield
    release_download.clear_latest_release_cache()


def _hpo_serves(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response] | None = None,
) -> list[str]:
    """HPO's servers, as the start reaches them: its latest release on GitHub, then the release's
    PURL. ``handler`` replaces them; the list collects the URLs requested."""
    asked: list[str] = []

    def serve(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        if handler is not None:
            return handler(request)
        if str(request.url) == config.HPO_LATEST_RELEASE_API_URL:
            return httpx.Response(200, content=_latest_answer())
        if str(request.url) == _NEWEST_URL:
            return httpx.Response(200, content=_NEWEST_OBO)
        return httpx.Response(404)

    def client() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(serve),
            follow_redirects=True,
            event_hooks={"request": [release_download._https_only]},
        )

    monkeypatch.setattr(release_download, "_client", client)
    return asked


class _Session:
    def __init__(self) -> None:
        self.rollbacks = 0

    async def rollback(self) -> None:
        self.rollbacks += 1


def _empty_database(
    monkeypatch: pytest.MonkeyPatch, backup: Path, *, fail_import: bool = False
) -> tuple[list[dict[str, Any]], list[str]]:
    """No HPO terms loaded, and ``backup`` the only place an ontology file may be. The first list
    collects each import's arguments, the second each backup download requested."""
    imported: list[dict[str, Any]] = []
    backup_downloads: list[str] = []

    async def no_terms(_session: Any) -> int:
        return 0

    async def record_import(_session: Any, **kwargs: Any) -> dict[str, int]:
        imported.append(kwargs)
        if fail_import:
            raise RuntimeError("the database refused the rows")
        return {"terms": 2, "synonyms": 0, "edges": 1, "closure_rows": 3}

    def backup_download(request: Any, timeout: float = 60) -> io.BytesIO:
        backup_downloads.append(request.full_url)
        return io.BytesIO(_VENDORED_HPO.read_bytes())

    monkeypatch.setattr(hpo_service, "_hpo_term_count", no_terms)
    monkeypatch.setattr(hpo_service, "import_hpo_ontology", record_import)
    monkeypatch.setattr(hpo_service, "hpo_ontology_candidate_paths", lambda _path=None: [backup])
    monkeypatch.setattr(hpo_service, "urlopen", backup_download)
    return imported, backup_downloads


def _backup_in_place(tmp_path: Path) -> Path:
    in_place = tmp_path / "ref-data" / "hpo" / "hp.obo"
    in_place.parent.mkdir(parents=True)
    shutil.copyfile(_VENDORED_HPO, in_place)
    return in_place


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]


# --- the newest release ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_empty_database_imports_hpos_newest_release(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    backup = _backup_in_place(tmp_path)
    asked = _hpo_serves(monkeypatch)
    imported, backup_downloads = _empty_database(monkeypatch, backup)

    result = await hpo_service.ensure_hpo_ontology_on_startup(_Session(), ontology_path=backup)

    # Which release is the newest, then that release from its own PURL, which never floats.
    assert asked == [config.HPO_LATEST_RELEASE_API_URL, _NEWEST_URL]
    [call] = imported
    # The bytes HPO served, imported as they were checked: not the vendored file, not a file on disk.
    assert call["path"] is None
    assert call["ontology_text"] == _NEWEST_OBO.decode("utf-8")
    assert call["source_format"] == "obo"
    # The release the file states, and the SHA-256 of those bytes, are kept with the terms.
    assert call["release_version"] == "hp/releases/2026-09-01"
    assert call["release_date"] == _NEWEST
    assert call["release_sha256"] == hashlib.sha256(_NEWEST_OBO).hexdigest()
    assert result is not None
    assert result["source"] == _NEWEST_URL
    assert result["release_version"] == "hp/releases/2026-09-01"
    assert result["release_sha256"] == hashlib.sha256(_NEWEST_OBO).hexdigest()
    # The backup was neither downloaded nor changed.
    assert backup_downloads == []
    assert backup.read_bytes() == _VENDORED_HPO.read_bytes()


@pytest.mark.asyncio
async def test_a_loaded_database_downloads_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    asked = _hpo_serves(monkeypatch)
    imported, backup_downloads = _empty_database(monkeypatch, _backup_in_place(tmp_path))

    async def some_terms(_session: Any) -> int:
        return 19_944

    monkeypatch.setattr(hpo_service, "_hpo_term_count", some_terms)

    assert await hpo_service.ensure_hpo_ontology_on_startup(_Session()) is None
    # A newer release is applied under Administration, HPO terminology, not by a start.
    assert asked == [] and imported == [] and backup_downloads == []


# --- the backup --------------------------------------------------------------------------------------


def _unreachable(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("no route to host", request=request)


def _states_another_release(request: httpx.Request) -> httpx.Response:
    if str(request.url) == config.HPO_LATEST_RELEASE_API_URL:
        return httpx.Response(200, content=_latest_answer())
    return httpx.Response(200, content=_NEWEST_OBO.replace(b"2026-09-01", b"2026-06-06"))


def _not_an_ontology(request: httpx.Request) -> httpx.Response:
    if str(request.url) == config.HPO_LATEST_RELEASE_API_URL:
        return httpx.Response(200, content=_latest_answer())
    return httpx.Response(200, content=b"<html>Service unavailable</html>")


def _rate_limited(request: httpx.Request) -> httpx.Response:
    return httpx.Response(403, content=b'{"message": "API rate limit exceeded"}')


@pytest.mark.parametrize(
    "hpo",
    [_unreachable, _states_another_release, _not_an_ontology, _rate_limited],
    ids=["unreachable", "states-another-release", "not-an-ontology", "rate-limited"],
)
@pytest.mark.asyncio
async def test_when_the_newest_release_cannot_be_used_the_vendored_file_is_imported(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    hpo: Callable[[httpx.Request], httpx.Response],
) -> None:
    backup = _backup_in_place(tmp_path)
    _hpo_serves(monkeypatch, hpo)
    imported, backup_downloads = _empty_database(monkeypatch, backup)
    caplog.set_level(logging.INFO, logger=hpo_service.logger.name)

    result = await hpo_service.ensure_hpo_ontology_on_startup(_Session(), ontology_path=backup)

    # Nothing of what HPO served is imported: the vendored file is, checked against its pin.
    [call] = imported
    assert call["path"] == backup
    assert call["ontology_text"] == _VENDORED_HPO.read_text(encoding="utf-8")
    assert call["release_version"] == config.HPO_PINNED_RELEASE
    assert call["release_sha256"] == config.HPO_PINNED_ONTOLOGY_SHA256
    assert result is not None and result["release_version"] == config.HPO_PINNED_RELEASE
    assert backup_downloads == []
    # One warning says the newest release was not used and names the backup imported instead.
    [warning] = _warnings(caplog)
    assert "newest release was not downloaded" in warning
    assert config.HPO_PINNED_RELEASE in warning
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


@pytest.mark.asyncio
async def test_a_defect_in_the_download_is_logged_and_the_vendored_file_imported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    backup = _backup_in_place(tmp_path)
    imported, _backup_downloads = _empty_database(monkeypatch, backup)

    async def defect() -> Any:
        raise RuntimeError("a bug in the release check")

    monkeypatch.setattr(release_download, "fetch_latest_hpo_release", defect)
    caplog.set_level(logging.INFO, logger=hpo_service.logger.name)

    result = await hpo_service.ensure_hpo_ontology_on_startup(_Session(), ontology_path=backup)

    # The start still gets the backup; the defect is logged with its traceback.
    assert [call["release_version"] for call in imported] == [config.HPO_PINNED_RELEASE]
    assert result is not None
    [error] = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert error.exc_info is not None and config.HPO_PINNED_RELEASE in error.getMessage()


@pytest.mark.asyncio
async def test_with_hpo_unreachable_and_no_file_in_place_the_vendored_release_is_downloaded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A deployment whose reference data lacks the file, on a start the GitHub check failed (its
    # 60 requests an hour, say): the vendored release from its own PURL, checked against its pin.
    target = tmp_path / "ref-data" / "hpo" / "hp.obo"
    _hpo_serves(monkeypatch, _rate_limited)
    imported, backup_downloads = _empty_database(monkeypatch, target)

    result = await hpo_service.ensure_hpo_ontology_on_startup(_Session(), ontology_path=target)

    assert backup_downloads == [config.HPO_PINNED_ONTOLOGY_URL]
    assert target.read_bytes() == _VENDORED_HPO.read_bytes()
    [call] = imported
    assert call["release_version"] == config.HPO_PINNED_RELEASE
    assert call["release_sha256"] == config.HPO_PINNED_ONTOLOGY_SHA256
    assert result is not None and result["source"] == str(target)


@pytest.mark.asyncio
async def test_with_the_download_off_nothing_is_requested_and_the_vendored_file_is_imported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backup = _backup_in_place(tmp_path)
    asked = _hpo_serves(monkeypatch)
    imported, backup_downloads = _empty_database(monkeypatch, backup)

    result = await hpo_service.ensure_hpo_ontology_on_startup(_Session(), ontology_path=backup, download_latest=False)

    assert asked == [] and backup_downloads == []
    [call] = imported
    assert call["path"] == backup and call["release_sha256"] == config.HPO_PINNED_ONTOLOGY_SHA256
    assert result is not None and result["release_version"] == config.HPO_PINNED_RELEASE


@pytest.mark.asyncio
async def test_with_the_download_off_and_no_file_in_place_nothing_is_imported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    asked = _hpo_serves(monkeypatch)
    imported, backup_downloads = _empty_database(monkeypatch, tmp_path / "hp.obo")

    result = await hpo_service.ensure_hpo_ontology_on_startup(
        _Session(), ontology_path=tmp_path / "hp.obo", download_latest=False
    )

    assert result is None
    assert asked == [] and backup_downloads == [] and imported == []


@pytest.mark.asyncio
async def test_a_failed_import_of_the_newest_release_is_rolled_back_and_nothing_else_is_imported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # The download was fine, so the backup would fail the same way: one import, rolled back.
    backup = _backup_in_place(tmp_path)
    _hpo_serves(monkeypatch)
    imported, backup_downloads = _empty_database(monkeypatch, backup, fail_import=True)
    session = _Session()
    caplog.set_level(logging.INFO, logger=hpo_service.logger.name)

    result = await hpo_service.ensure_hpo_ontology_on_startup(session, ontology_path=backup)

    assert result is None
    assert [call["release_version"] for call in imported] == ["hp/releases/2026-09-01"]
    assert session.rollbacks == 1 and backup_downloads == []
    [error] = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert _NEWEST_URL in error.getMessage()


# --- the setting, as the backend starts -------------------------------------------------------------


def test_the_download_is_on_by_default_and_the_old_setting_is_gone() -> None:
    loaded = Settings(_env_file=None, APP_ENV="test")
    assert loaded.hpo_download_latest_on_startup is True
    names = {name.upper() for key, field in Settings.model_fields.items() for name in (key, field.alias) if name}
    assert "HPO_DOWNLOAD_LATEST_ON_STARTUP" in names
    assert "HPO_DOWNLOAD_IF_MISSING" not in names


@pytest.mark.parametrize("download", [True, False])
@pytest.mark.asyncio
async def test_the_start_imports_the_release_the_setting_says(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, download: bool
) -> None:
    backup = _backup_in_place(tmp_path)
    deployment = Settings(
        _env_file=None,
        APP_ENV="test",
        HPO_BOOTSTRAP_ON_STARTUP=True,
        HPO_DOWNLOAD_LATEST_ON_STARTUP=download,
        HPO_ONTOLOGY_PATH=str(backup),
        FAMILY_IMPORT_WORKER_COUNT=1,
    )
    monkeypatch.setattr(app_main, "settings", deployment)
    monkeypatch.setattr(hpo_service, "settings", deployment)

    class _SessionFactory:
        async def __aenter__(self) -> _Session:
            return _Session()

        async def __aexit__(self, *_exc: Any) -> bool:
            return False

    # Every step of the start but the HPO bootstrap: replaced, so the start touches no database.
    stub_lifespan_steps(monkeypatch)
    monkeypatch.setattr(app_main, "get_postgres_sessionmaker", lambda: _SessionFactory)
    asked = _hpo_serves(monkeypatch)
    imported, _backup_downloads = _empty_database(monkeypatch, backup)

    async with app_main.lifespan(LifespanApp()):
        pass

    [call] = imported
    if download:
        assert asked == [config.HPO_LATEST_RELEASE_API_URL, _NEWEST_URL]
        assert call["release_version"] == "hp/releases/2026-09-01"
    else:
        assert asked == []
        assert call["release_version"] == config.HPO_PINNED_RELEASE
