"""Phenotype-driven prioritization via the Monarch Initiative semsim API.

Given a set of observed HPO terms, ask Monarch's semantic-similarity service for the
best-matching human genes (or diseases) ranked by phenotypic similarity. This is a
pure live-API feature — no bulk ingest — and complements the gene profile's
gene->disease (Phase 1) and disease->phenotype overlap (Phase 2) by going the other
direction: phenotypes -> candidate genes. See docs/monarch-integration.md.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Iterable

import httpx

from ..core.coga_logging import describe_error

logger = logging.getLogger(__name__)

MONARCH_SEMSIM_SEARCH_URL = "https://api-v3.monarchinitiative.org/v3/api/semsim/search"

# Groups the Monarch semsim service can rank against. We expose the two human ones.
SEMSIM_GROUPS = ("Human Genes", "Human Diseases")
DEFAULT_GROUP = "Human Genes"
DEFAULT_LIMIT = 20
MAX_LIMIT = 50  # Monarch caps the request at 50

_REQUEST_TIMEOUT_SECONDS = 25.0
_CACHE_TTL_SECONDS = 3600.0
_CACHE_MAX_ENTRIES = 256

# Deterministic-enough per Monarch release; cache to avoid re-hitting the API for the
# same phenotype profile. Key: (group, sorted termset, limit) -> (expires_at, results).
_cache: dict[tuple[str, tuple[str, ...], int], tuple[float, list[dict[str, Any]]]] = {}


class MonarchSemsimError(RuntimeError):
    """Raised when the Monarch semsim service is unavailable or returns an error."""


def _cache_get(key: tuple[str, tuple[str, ...], int]) -> list[dict[str, Any]] | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    expires_at, results = entry
    if time.monotonic() >= expires_at:
        _cache.pop(key, None)
        return None
    return results


def _cache_put(key: tuple[str, tuple[str, ...], int], results: list[dict[str, Any]]) -> None:
    if len(_cache) >= _CACHE_MAX_ENTRIES:
        # Drop the soonest-to-expire entry to bound memory.
        oldest = min(_cache, key=lambda k: _cache[k][0])
        _cache.pop(oldest, None)
    _cache[key] = (time.monotonic() + _CACHE_TTL_SECONDS, results)


def _normalize_results(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise MonarchSemsimError("Unexpected response shape from Monarch semsim")
    results: list[dict[str, Any]] = []
    for item in payload:
        subject = item.get("subject") or {}
        identifier = subject.get("id")
        if not identifier:
            continue
        results.append(
            {
                "rank": len(results) + 1,
                "score": item.get("score"),
                "id": identifier,
                "name": subject.get("name") or identifier,
                "category": subject.get("category"),
            }
        )
    return results


async def semsim_search(
    termset: Iterable[str],
    *,
    group: str = DEFAULT_GROUP,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Rank entities in ``group`` by phenotypic similarity to ``termset``.

    Returns ``[{rank, score, id, name, category}]``. Raises MonarchSemsimError on a
    network/HTTP failure so the caller can surface a clear "service unavailable".
    """
    if group not in SEMSIM_GROUPS:
        raise ValueError(f"Unsupported semsim group: {group}")
    terms = sorted({term.strip() for term in termset if term and term.strip()})
    bounded_limit = max(1, min(int(limit), MAX_LIMIT))
    if not terms:
        return []

    key = (group, tuple(terms), bounded_limit)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    body = {"termset": terms, "group": group, "limit": bounded_limit}
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(MONARCH_SEMSIM_SEARCH_URL, json=body)
            response.raise_for_status()
        results = _normalize_results(response.json())
    except (httpx.HTTPError, asyncio.TimeoutError, ValueError) as exc:
        # Named by its kind: an HTTP error's text quotes the request, the terms sent included.
        logger.warning("Monarch semsim search failed (%s)", describe_error(exc))
        raise MonarchSemsimError(f"Monarch semsim search failed ({describe_error(exc)})") from exc

    _cache_put(key, results)
    return results
