"""The newest HPO release, and an HPO release's ontology file fetched from its own PURL.

The HPO admin page shows the newest release HPO has published beside the loaded one (the release
check), and its sync can take a release's file from HPO instead of from a folder on the server. A
downloaded file goes through the sync as a file on disk does (``hpo_service.sync_hpo_ontology``):
it must state the release asked for, its changes are previewed, and it is applied only with the
SHA-256 the preview showed. A start on an empty database imports the newest release
the same way, without the preview: the file must state the release, and its SHA-256 is kept with
the terms (``hpo_service.ensure_hpo_ontology_on_startup``); the vendored release is its backup.
Nothing here imports anything.

The newest release is read from HPO's GitHub releases (``HPO_LATEST_RELEASE_API_URL``); a release's
file from ``https://purl.obolibrary.org/obo/hp/releases/<date>/hp.obo``, which never floats and
redirects to the file GitHub serves. Every request, each redirect included, goes over HTTPS, and
each answer is read within a size cap.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import httpx

from ..core.config import (
    HPO_LATEST_RELEASE_API_URL,
    HPO_PINNED_RELEASE,
    HPO_RELEASE_URL_TEMPLATE,
)
from .bounded_download import download_bounded_bytes

# An HPO release is named by its date: 2026-09-01, as HPO's tag (v2026-09-01) or as an ontology
# file's data-version (hp/releases/2026-09-01) names it.
_RELEASE_NAME = re.compile(r"(?:hp/releases/|v)?(\d{4}-\d{2}-\d{2})")
# How long the newest release, once read, is shown before it is read again: GitHub answers 60
# unauthenticated requests an hour per address.
LATEST_RELEASE_CACHE_SECONDS = 900.0
# The releases answer is a few kilobytes; the ontology file about 11 MB.
_MAX_RELEASE_ANSWER_BYTES = 1 << 20
MAX_HPO_RELEASE_BYTES = 256 * 1024 * 1024
_HTTP_HEADERS = {"User-Agent": "CoGA-HPO-release-check/1.0", "Accept": "application/vnd.github+json"}
_HTTP_TIMEOUT = httpx.Timeout(30.0, read=120.0)


class HpoReleaseUnavailable(RuntimeError):
    """HPO could not be reached, or did not answer as expected: the check or download failed."""


@dataclass(frozen=True)
class LatestHpoRelease:
    release_version: str  # hp/releases/2026-09-01, as an ontology file states it
    release_date: date
    published_at: datetime | None
    download_url: str


def hpo_release_date(value: str) -> date:
    """The date that names an HPO release: ``2026-09-01``, ``v2026-09-01`` or
    ``hp/releases/2026-09-01``. ValueError for anything else."""
    match = _RELEASE_NAME.fullmatch((value or "").strip())
    if match is None:
        raise ValueError("An HPO release is named by its date, such as 2026-09-01.")
    try:
        return date.fromisoformat(match.group(1))
    except ValueError as exc:
        raise ValueError(f"{match.group(1)} is not a date, so it names no HPO release.") from exc


def hpo_release_version(release: date) -> str:
    """The release as an ontology file's ``data-version`` states it: ``hp/releases/2026-09-01``."""
    return f"hp/releases/{release.isoformat()}"


def hpo_release_url(release: date) -> str:
    """The release's own PURL, which never floats."""
    return HPO_RELEASE_URL_TEMPLATE.format(release=release.isoformat())


def pinned_release_date() -> date:
    return hpo_release_date(HPO_PINNED_RELEASE)


async def _https_only(request: httpx.Request) -> None:
    if request.url.scheme != "https":
        raise HpoReleaseUnavailable(f"Refused to read HPO over a non-HTTPS URL: {request.url}")


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=_HTTP_TIMEOUT,
        follow_redirects=True,
        headers=_HTTP_HEADERS,
        event_hooks={"request": [_https_only]},
    )


def _parse_published_at(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def latest_release_from_answer(payload: bytes) -> LatestHpoRelease:
    """The newest release, from GitHub's answer for HPO's latest release (its ``tag_name``,
    ``v2026-09-01``, and ``published_at``). HpoReleaseUnavailable when the answer names none."""
    try:
        answer = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HpoReleaseUnavailable("HPO's release list did not answer in JSON.") from exc
    tag = answer.get("tag_name") if isinstance(answer, dict) else None
    try:
        release = hpo_release_date(str(tag or ""))
    except ValueError as exc:
        raise HpoReleaseUnavailable("HPO's latest release names no release date.") from exc
    return LatestHpoRelease(
        release_version=hpo_release_version(release),
        release_date=release,
        published_at=_parse_published_at(answer.get("published_at")),
        download_url=hpo_release_url(release),
    )


_latest_cache: tuple[float, LatestHpoRelease] | None = None


def clear_latest_release_cache() -> None:
    global _latest_cache
    _latest_cache = None


async def fetch_latest_hpo_release(client: httpx.AsyncClient | None = None) -> LatestHpoRelease:
    """The newest release HPO has published, read at most every LATEST_RELEASE_CACHE_SECONDS.
    HpoReleaseUnavailable when it cannot be read."""
    global _latest_cache
    now = time.monotonic()
    if _latest_cache is not None and now - _latest_cache[0] < LATEST_RELEASE_CACHE_SECONDS:
        return _latest_cache[1]
    http = client or _client()
    try:
        payload = await download_bounded_bytes(
            http, HPO_LATEST_RELEASE_API_URL, max_bytes=_MAX_RELEASE_ANSWER_BYTES, source="HPO's release list"
        )
    except (httpx.HTTPError, ValueError) as exc:
        raise HpoReleaseUnavailable(f"HPO's release list could not be read: {exc}") from exc
    finally:
        if client is None:
            await http.aclose()
    latest = latest_release_from_answer(payload)
    _latest_cache = (now, latest)
    return latest


async def download_hpo_release(release: date, client: httpx.AsyncClient | None = None) -> bytes:
    """The ontology file of the HPO release, as HPO serves it from the release's PURL.

    ValueError when HPO has no such release (404), HpoReleaseUnavailable when it cannot be
    downloaded or what it serves is not an OBO ontology file. The bytes are returned as served:
    the sync checks the release they state and records their SHA-256."""
    url = hpo_release_url(release)
    http = client or _client()
    try:
        payload = await download_bounded_bytes(
            http,
            url,
            max_bytes=MAX_HPO_RELEASE_BYTES,
            source=f"HPO release {release.isoformat()}",
            none_on_404=True,
        )
    except (httpx.HTTPError, ValueError) as exc:
        raise HpoReleaseUnavailable(f"HPO release {release.isoformat()} could not be downloaded: {exc}") from exc
    finally:
        if client is None:
            await http.aclose()
    if payload is None:
        raise ValueError(f"HPO has published no release {release.isoformat()} ({url}).")
    if not payload.lstrip().startswith(b"format-version:"):
        raise HpoReleaseUnavailable(f"What {url} served is not an OBO ontology file.")
    return payload
