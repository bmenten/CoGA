from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from time import monotonic
from typing import Any

import httpx

from ..core.config import settings
from ..schemas import GithubReleaseCatalogOut, GithubReleaseOut

_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_INLINE_CODE_RE = re.compile(r"`([^`]*)`")


@dataclass
class _ReleaseCatalogCache:
    fetched_monotonic: float = 0.0
    payload: GithubReleaseCatalogOut | None = None


_cache = _ReleaseCatalogCache()
_cache_lock = asyncio.Lock()


def _normalized_repository_url() -> str:
    return settings.github_repository_url.rstrip("/")


def _normalized_releases_url() -> str:
    return settings.github_releases_url.rstrip("/")


def _normalized_issues_url() -> str:
    return settings.github_issues_url.rstrip("/")


def _normalized_repo_visibility() -> str:
    normalized = settings.github_repo_visibility.strip().lower()
    if normalized in {"private", "public"}:
        return normalized
    return "unknown"


def _base_catalog() -> GithubReleaseCatalogOut:
    return GithubReleaseCatalogOut(
        repository=settings.github_repository.strip(),
        repository_url=_normalized_repository_url(),
        releases_url=_normalized_releases_url(),
        issues_url=_normalized_issues_url(),
        repo_visibility=_normalized_repo_visibility(),
    )


def _truncate_summary(value: str, *, max_length: int = 420) -> str:
    if len(value) <= max_length:
        return value
    return value[: max_length - 1].rstrip() + "…"


def _clean_release_line(raw_line: str) -> str:
    stripped = raw_line.strip()
    if not stripped:
        return ""
    stripped = _MARKDOWN_LINK_RE.sub(r"\1", stripped)
    stripped = _INLINE_CODE_RE.sub(r"\1", stripped)
    stripped = stripped.replace("**", "").replace("__", "")
    stripped = _HTML_TAG_RE.sub(" ", stripped)
    stripped = re.sub(r"^\d+\.\s+", "", stripped)
    if stripped.startswith(("-", "*", "+")):
        stripped = f"• {stripped[1:].strip()}"
    elif stripped.startswith(">"):
        stripped = stripped[1:].strip()
    stripped = re.sub(r"\s+", " ", stripped).strip()
    return stripped


def summarize_release_body(body: str | None) -> str:
    if not body or not body.strip():
        return "Release notes are available on GitHub."

    lines: list[str] = []
    in_code_block = False
    for raw_line in body.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block or not stripped:
            continue
        if stripped.startswith("#"):
            continue

        cleaned = _clean_release_line(raw_line)
        lowered = cleaned.lower()
        if not cleaned or lowered in {"what's changed", "whats changed"}:
            continue
        if lowered.startswith("full changelog"):
            continue
        lines.append(cleaned)
        if len(lines) == 4:
            break

    if not lines:
        return "Release notes are available on GitHub."

    return _truncate_summary("\n".join(lines))


def _release_from_payload(payload: dict[str, Any]) -> GithubReleaseOut | None:
    published_at = payload.get("published_at") or payload.get("created_at")
    tag_name = str(payload.get("tag_name") or "").strip()
    html_url = str(payload.get("html_url") or "").strip()
    if not published_at or not tag_name or not html_url:
        return None

    normalized_timestamp = str(published_at).replace("Z", "+00:00")
    try:
        published_datetime = datetime.fromisoformat(normalized_timestamp)
    except ValueError:
        return None

    if published_datetime.tzinfo is None:
        published_datetime = published_datetime.replace(tzinfo=timezone.utc)

    return GithubReleaseOut(
        version=tag_name,
        name=str(payload.get("name") or "").strip() or None,
        published_at=published_datetime,
        summary=summarize_release_body(str(payload.get("body") or "")),
        url=html_url,
        prerelease=bool(payload.get("prerelease")),
    )


async def _fetch_release_catalog() -> GithubReleaseCatalogOut:
    catalog = _base_catalog()
    repository = catalog.repository.strip()
    if not repository:
        return catalog.model_copy(
            update={
                "sync_error": "GitHub repository settings are not configured for this deployment.",
            }
        )

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "coga-release-sync",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if settings.github_api_token:
        headers["Authorization"] = f"Bearer {settings.github_api_token}"

    url = f"https://api.github.com/repos/{repository}/releases"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(url, headers=headers, params={"per_page": 20})
    except httpx.HTTPError:
        return catalog.model_copy(
            update={
                "sync_error": "Could not reach GitHub while loading release history.",
            }
        )

    if response.status_code != 200:
        if response.status_code in {401, 403, 404}:
            message = (
                "Release sync is unavailable. Configure GITHUB_API_TOKEN for a private repository "
                "or make the repository public."
            )
        else:
            message = f"GitHub returned {response.status_code} while loading release history."
        return catalog.model_copy(update={"sync_error": message})

    try:
        payload = response.json()
    except ValueError:
        return catalog.model_copy(
            update={
                "sync_error": "GitHub returned an unreadable release payload.",
            }
        )

    if not isinstance(payload, list):
        return catalog.model_copy(
            update={
                "sync_error": "GitHub returned an unexpected release payload.",
            }
        )

    releases = [
        release
        for item in payload
        if isinstance(item, dict)
        and not bool(item.get("draft"))
        and (release := _release_from_payload(item)) is not None
    ]

    return catalog.model_copy(
        update={
            "sync_status": "ok",
            "sync_error": None,
            "fetched_at": datetime.now(timezone.utc),
            "releases": releases,
        }
    )


async def get_github_release_catalog(*, refresh: bool = False) -> GithubReleaseCatalogOut:
    ttl_seconds = max(settings.github_release_cache_ttl_seconds, 0)
    if not refresh and ttl_seconds > 0:
        cached_payload = _cache.payload
        if cached_payload and monotonic() - _cache.fetched_monotonic < ttl_seconds:
            return cached_payload

    async with _cache_lock:
        if not refresh and ttl_seconds > 0:
            cached_payload = _cache.payload
            if cached_payload and monotonic() - _cache.fetched_monotonic < ttl_seconds:
                return cached_payload

        payload = await _fetch_release_catalog()
        _cache.payload = payload
        _cache.fetched_monotonic = monotonic()
        return payload
