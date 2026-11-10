"""The HPO release check and an HPO release downloaded for the admin sync.

The HPO admin page shows the newest release beside the loaded one, read from HPO's GitHub
releases, and its sync can take a release's file from the release's own PURL, which never
floats, as well as a file an administrator put on the server. Nothing is imported without the
administrator: a downloaded file goes through the sync as a file on the server does, must
state the release asked for, and is applied only with the SHA-256 its preview showed. A start on an
empty database imports the newest release the same way (``test_hpo_startup_latest_release.py``),
with the vendored release as its backup (``test_hpo_download_pin.py``).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from backend.app.core import config
from backend.app.routers import admin as admin_router
from backend.app.schemas import HpoOntologySyncRequest
from backend.app.services import hpo_release_download as release_download
from backend.app.services import hpo_service

_OBO = """format-version: 1.2
data-version: hp/releases/2026-09-01
ontology: hp

[Term]
id: HP:0000001
name: All

[Term]
id: HP:0000118
name: Phenotypic abnormality
is_a: HP:0000001 ! All
"""


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


# --- naming a release ------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["2026-09-01", "v2026-09-01", "hp/releases/2026-09-01", " 2026-09-01 "])
def test_a_release_is_named_by_its_date(name: str) -> None:
    assert release_download.hpo_release_date(name) == date(2026, 9, 1)


@pytest.mark.parametrize("name", ["", "latest", "2026-9-1", "2026-13-01", "../2026-09-01", "2026-09-01/hp.obo"])
def test_anything_else_names_no_release(name: str) -> None:
    with pytest.raises(ValueError):
        release_download.hpo_release_date(name)


def test_a_release_is_downloaded_from_its_own_purl() -> None:
    assert release_download.hpo_release_url(date(2026, 9, 1)) == (
        "https://purl.obolibrary.org/obo/hp/releases/2026-09-01/hp.obo"
    )
    # The vendored backup's pinned URL follows the same form.
    assert config.HPO_PINNED_ONTOLOGY_URL == release_download.hpo_release_url(release_download.pinned_release_date())


# --- the newest release ----------------------------------------------------------------------------


def _answer(tag: str = "v2026-09-01", published: str = "2026-09-03T06:08:39Z") -> bytes:
    return json.dumps({"tag_name": tag, "published_at": published, "assets": []}).encode()


def test_the_newest_release_is_read_from_hpos_latest_release() -> None:
    latest = release_download.latest_release_from_answer(_answer())

    assert latest.release_version == "hp/releases/2026-09-01"
    assert latest.release_date == date(2026, 9, 1)
    assert latest.published_at == datetime(2026, 9, 3, 6, 8, 39, tzinfo=timezone.utc)
    assert latest.download_url.endswith("/hp/releases/2026-09-01/hp.obo")


@pytest.mark.parametrize("payload", [b"<html>rate limited</html>", json.dumps({"message": "Not Found"}).encode(), _answer(tag="nightly")])
def test_an_answer_that_names_no_release_is_said_so(payload: bytes) -> None:
    with pytest.raises(release_download.HpoReleaseUnavailable):
        release_download.latest_release_from_answer(payload)


@pytest.fixture(autouse=True)
def _fresh_cache():
    release_download.clear_latest_release_cache()
    yield
    release_download.clear_latest_release_cache()


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
        event_hooks={"request": [release_download._https_only]},
    )


def test_the_newest_release_is_read_once_per_cache_period() -> None:
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(200, content=_answer())

    async def run() -> list[Any]:
        async with _client(handler) as client:
            return [await release_download.fetch_latest_hpo_release(client) for _ in range(2)]

    first, second = asyncio.run(run())
    assert first == second and asked == [config.HPO_LATEST_RELEASE_API_URL]


def test_hpo_unreachable_is_said_so() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    async def run() -> None:
        async with _client(handler) as client:
            await release_download.fetch_latest_hpo_release(client)

    with pytest.raises(release_download.HpoReleaseUnavailable, match="could not be read"):
        asyncio.run(run())


# --- downloading a release -------------------------------------------------------------------------


def _download(handler, release: date = date(2026, 9, 1)) -> bytes:
    async def run() -> bytes:
        async with _client(handler) as client:
            return await release_download.download_hpo_release(release, client)

    return asyncio.run(run())


def test_a_release_is_downloaded_as_served_through_its_redirects() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "purl.obolibrary.org":
            return httpx.Response(302, headers={"location": "https://github.com/obophenotype/hp/releases/download/v2026-09-01/hp.obo"})
        return httpx.Response(200, content=_OBO.encode())

    assert _download(handler) == _OBO.encode()


def test_a_release_hpo_never_published_is_refused() -> None:
    with pytest.raises(ValueError, match="no release 2026-09-02"):
        _download(lambda request: httpx.Response(404), release=date(2026, 9, 2))


def test_what_is_not_an_ontology_file_is_refused() -> None:
    with pytest.raises(release_download.HpoReleaseUnavailable, match="not an OBO ontology file"):
        _download(lambda request: httpx.Response(200, content=b"<html>error</html>"))


def test_a_release_is_read_over_https_only() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://mirror.example/hp.obo"})

    with pytest.raises(release_download.HpoReleaseUnavailable, match="non-HTTPS"):
        _download(handler)


def test_a_download_larger_than_the_cap_is_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release_download, "MAX_HPO_RELEASE_BYTES", 10)
    with pytest.raises(release_download.HpoReleaseUnavailable, match="exceeds 10 bytes"):
        _download(lambda request: httpx.Response(200, content=_OBO.encode()))


# --- the release check -----------------------------------------------------------------------------


@pytest.fixture()
def loaded(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"release_version": "hp/releases/2026-02-16", "release_date": date(2026, 2, 16)}

    async def tables(*_args: Any, **_kwargs: Any) -> bool:
        return True

    async def loaded_release(_session: Any) -> dict[str, Any] | None:
        return state or None

    monkeypatch.setattr(hpo_service, "_postgres_tables_available", tables)
    monkeypatch.setattr(hpo_service, "get_loaded_hpo_release", loaded_release)
    return state


def _latest(monkeypatch: pytest.MonkeyPatch, release: date | Exception) -> None:
    async def fetch() -> release_download.LatestHpoRelease:
        if isinstance(release, Exception):
            raise release
        return release_download.LatestHpoRelease(
            release_version=release_download.hpo_release_version(release),
            release_date=release,
            published_at=None,
            download_url=release_download.hpo_release_url(release),
        )

    monkeypatch.setattr(release_download, "fetch_latest_hpo_release", fetch)


def test_a_newer_release_than_the_loaded_one_is_said_so(monkeypatch: pytest.MonkeyPatch, loaded: dict) -> None:
    _latest(monkeypatch, date(2026, 9, 1))
    check = asyncio.run(hpo_service.hpo_release_check(object()))

    assert check["newer_available"] is True
    assert (check["latest_release"], check["loaded_release"]) == ("hp/releases/2026-09-01", "hp/releases/2026-02-16")
    assert check["pinned_release"] == config.HPO_PINNED_RELEASE and check["error"] is None


def test_the_loaded_release_being_the_newest_is_said_so(monkeypatch: pytest.MonkeyPatch, loaded: dict) -> None:
    loaded.update(release_version="hp/releases/2026-09-01", release_date=date(2026, 9, 1))
    _latest(monkeypatch, date(2026, 9, 1))

    assert asyncio.run(hpo_service.hpo_release_check(object()))["newer_available"] is False


def test_with_no_ontology_loaded_the_newest_release_is_newer(monkeypatch: pytest.MonkeyPatch, loaded: dict) -> None:
    loaded.clear()
    _latest(monkeypatch, date(2026, 9, 1))
    check = asyncio.run(hpo_service.hpo_release_check(object()))

    assert check["newer_available"] is True and check["loaded_release"] is None


def test_hpo_unreachable_leaves_what_is_known_here(monkeypatch: pytest.MonkeyPatch, loaded: dict) -> None:
    _latest(monkeypatch, release_download.HpoReleaseUnavailable("HPO's release list could not be read: timeout"))
    check = asyncio.run(hpo_service.hpo_release_check(object()))

    assert check["error"] == "HPO's release list could not be read: timeout"
    assert check["latest_release"] is None and check["newer_available"] is False
    assert check["loaded_release"] == "hp/releases/2026-02-16"


# --- the sync of a downloaded release -------------------------------------------------------------


def test_a_sync_names_one_source() -> None:
    with pytest.raises(ValidationError, match="either an ontology file path or an HPO release"):
        HpoOntologySyncRequest()
    with pytest.raises(ValidationError, match="either an ontology file path or an HPO release"):
        HpoOntologySyncRequest(path="/data/ref-data/hpo/hp.obo", download_release="2026-09-01")
    with pytest.raises(ValidationError, match="must state the release asked for"):
        HpoOntologySyncRequest(download_release="2026-09-01", release_version="2026-02-16")
    with pytest.raises(ValidationError):
        HpoOntologySyncRequest(download_release="2026-09-01", expected_sha256="not-a-digest")
    assert HpoOntologySyncRequest(download_release="2026-09-01").preview_only is True


@pytest.fixture()
def sync_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    imported: list[dict[str, Any]] = []

    async def preview(_session: Any, **kwargs: Any) -> dict[str, int]:
        assert kwargs["ontology_text"] == _OBO and kwargs["source_format"] == "obo"
        return {"terms": 2}

    async def summary(_session: Any) -> dict[str, Any]:
        return {"total_terms": 0}

    async def tables_available(*_args: Any, **_kwargs: Any) -> bool:
        return True

    async def record_import(_session: Any, **kwargs: Any) -> dict[str, int]:
        imported.append(kwargs)
        return {"terms": 2, "synonyms": 0, "edges": 1, "closure_rows": 3}

    monkeypatch.setattr(hpo_service, "preview_hpo_ontology_sync", preview)
    monkeypatch.setattr(hpo_service, "get_hpo_admin_summary", summary)
    monkeypatch.setattr(hpo_service, "_postgres_tables_available", tables_available)
    monkeypatch.setattr(hpo_service, "import_hpo_ontology", record_import)
    return imported


@pytest.mark.asyncio
async def test_a_downloaded_release_is_previewed_with_its_release_and_sha256(sync_calls: list) -> None:
    result = await hpo_service.sync_hpo_ontology(
        object(), ontology_bytes=_OBO.encode(), release_version="2026-09-01", preview_only=True
    )

    assert (result["release_version"], result["release_sha256"]) == ("hp/releases/2026-09-01", _digest(_OBO.encode()))
    assert sync_calls == []


@pytest.mark.asyncio
async def test_a_downloaded_release_is_applied_with_the_previewed_sha256(sync_calls: list) -> None:
    await hpo_service.sync_hpo_ontology(
        object(),
        ontology_bytes=_OBO.encode(),
        release_version="2026-09-01",
        expected_sha256=_digest(_OBO.encode()),
        preview_only=False,
    )

    [call] = sync_calls
    assert (call["path"], call["source_format"], call["ontology_text"]) == (None, "obo", _OBO)
    assert call["release_sha256"] == _digest(_OBO.encode())


@pytest.mark.asyncio
async def test_a_file_that_changed_since_its_preview_is_not_imported(sync_calls: list) -> None:
    with pytest.raises(ValueError, match="not the one previewed"):
        await hpo_service.sync_hpo_ontology(
            object(), ontology_bytes=_OBO.encode(), expected_sha256="0" * 64, preview_only=False
        )
    assert sync_calls == []


@pytest.mark.asyncio
async def test_a_release_hpo_served_as_another_is_not_imported(sync_calls: list) -> None:
    with pytest.raises(ValueError, match="not the expected 2026-10-01"):
        await hpo_service.sync_hpo_ontology(
            object(), ontology_bytes=_OBO.encode(), release_version="2026-10-01", preview_only=False
        )
    assert sync_calls == []


def test_the_route_downloads_the_release_asked_for_and_expects_it(monkeypatch: pytest.MonkeyPatch) -> None:
    downloaded: list[date] = []
    synced: list[dict[str, Any]] = []

    async def download(release: date) -> bytes:
        downloaded.append(release)
        return _OBO.encode()

    async def sync(_session: Any, **kwargs: Any) -> dict[str, Any]:
        synced.append(kwargs)
        return {"preview_only": True}

    monkeypatch.setattr(admin_router, "download_hpo_release", download)
    monkeypatch.setattr(admin_router, "sync_hpo_ontology", sync)
    asyncio.run(
        admin_router.sync_hpo_terms(
            HpoOntologySyncRequest(download_release="v2026-09-01", expected_sha256="a" * 64, preview_only=False),
            session=object(),
            user=object(),
        )
    )

    assert downloaded == [date(2026, 9, 1)]
    [call] = synced
    assert call["release_version"] == "2026-09-01" and call["ontology_bytes"] == _OBO.encode()
    assert (call["expected_sha256"], call["preview_only"]) == ("a" * 64, False)


@pytest.mark.parametrize(
    ("error", "status"),
    [(release_download.HpoReleaseUnavailable("HPO unreachable"), 502), (ValueError("HPO has published no release"), 400)],
)
def test_the_route_answers_a_failed_download(monkeypatch: pytest.MonkeyPatch, error: Exception, status: int) -> None:
    from fastapi import HTTPException

    async def download(_release: date) -> bytes:
        raise error

    monkeypatch.setattr(admin_router, "download_hpo_release", download)
    with pytest.raises(HTTPException) as refused:
        asyncio.run(
            admin_router.sync_hpo_terms(HpoOntologySyncRequest(download_release="2026-09-01"), session=object(), user=object())
        )
    assert refused.value.status_code == status and refused.value.detail == str(error)
