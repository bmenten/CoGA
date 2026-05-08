from __future__ import annotations

import pytest

from backend.app.core.config import settings
from backend.app.services import github_releases_service


def test_summarize_release_body_keeps_human_readable_feature_lines() -> None:
    summary = github_releases_service.summarize_release_body(
        """
        ## What's changed

        - Added compound-het pair-level search results
        - Improved dashboard family table alignment
        - Linked the in-app user guide to deeper workflow docs

        **Full Changelog**: https://example.invalid/changelog
        """
    )

    assert "compound-het pair-level search results" in summary
    assert "dashboard family table alignment" in summary
    assert "Full Changelog" not in summary


@pytest.mark.asyncio
async def test_get_github_release_catalog_returns_release_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _FakeResponse:
        status_code = 200

        def json(self):
            return [
                {
                    "tag_name": "v1.4.0",
                    "name": "Pair-level compound het results",
                    "published_at": "2026-04-20T09:30:00Z",
                    "html_url": "https://github.com/bmenten/coga/releases/tag/v1.4.0",
                    "prerelease": False,
                    "body": """
                    - Added pair-level compound-het search results
                    - Expanded operational tooling for ClickHouse variants
                    """,
                }
            ]

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None, params=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["params"] = params
            return _FakeResponse()

    monkeypatch.setattr(github_releases_service.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(settings, "github_repository", "bmenten/coga")
    monkeypatch.setattr(settings, "github_api_token", "secret-token")
    monkeypatch.setattr(settings, "github_release_cache_ttl_seconds", 0)
    github_releases_service._cache.payload = None
    github_releases_service._cache.fetched_monotonic = 0.0

    catalog = await github_releases_service.get_github_release_catalog(refresh=True)

    assert catalog.sync_status == "ok"
    assert catalog.repository == "bmenten/coga"
    assert catalog.releases[0].version == "v1.4.0"
    assert "pair-level compound-het" in catalog.releases[0].summary
    assert captured["url"] == "https://api.github.com/repos/bmenten/coga/releases"
    assert captured["params"] == {"per_page": 20}
    assert captured["timeout"] == 20.0
    assert captured["headers"]["Authorization"] == "Bearer secret-token"


@pytest.mark.asyncio
async def test_get_github_release_catalog_reports_private_repo_sync_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeResponse:
        status_code = 404

        def json(self):
            return {}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None, params=None):
            return _FakeResponse()

    monkeypatch.setattr(github_releases_service.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(settings, "github_repository", "bmenten/coga")
    monkeypatch.setattr(settings, "github_api_token", None)
    monkeypatch.setattr(settings, "github_release_cache_ttl_seconds", 0)
    github_releases_service._cache.payload = None
    github_releases_service._cache.fetched_monotonic = 0.0

    catalog = await github_releases_service.get_github_release_catalog(refresh=True)

    assert catalog.sync_status == "unavailable"
    assert catalog.releases == []
    assert catalog.sync_error is not None
    assert "GITHUB_API_TOKEN" in catalog.sync_error
