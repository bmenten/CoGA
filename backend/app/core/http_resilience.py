"""Resilient outbound HTTP for the gene-info refresh's per-gene NCBI E-utilities requests
(``services/gene_info_external.py``), the only caller. Every other outbound call (the bulk
HGNC/ClinGen/GenCC/GENCODE/HPO downloads, Monarch, PanelApp, UCSC, GitHub releases) uses
its own client with a fixed timeout and no retry; the ``EXTERNAL_HTTP_*`` settings do not
apply to them.

Standardised connect/read timeouts plus capped exponential-backoff retry with jitter on
TRANSIENT failures (connection/timeout errors and 429/5xx). Retries are limited to
IDEMPOTENT methods — a non-idempotent request is never retried, since a retry could
double-execute it. The worst case is BOUNDED — roughly ``max_attempts × (connect + read)``
(per-attempt request time dominates) plus the inter-attempt backoffs — so a slow or
flapping upstream can neither stall a request indefinitely nor be hammered. The caller is
the background gene-info refresh job (not a user-blocking request handler), where that
bound is comfortably acceptable.

Callers still call ``response.raise_for_status()`` — this layer only retries; it does not
decide what a non-transient HTTP error means.
"""

from __future__ import annotations

import asyncio
import random

import httpx

from .config import settings

# 429 (rate limited) + the transient 5xx; 501/505 etc. are not retried (won't change).
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def default_timeout() -> httpx.Timeout:
    """Standard per-phase timeout for outbound reference calls (from settings)."""
    return httpx.Timeout(
        connect=settings.external_http_connect_timeout_seconds,
        read=settings.external_http_read_timeout_seconds,
        write=settings.external_http_read_timeout_seconds,
        pool=settings.external_http_connect_timeout_seconds,
    )


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Parse a numeric ``Retry-After`` header (seconds). HTTP-date form is ignored."""
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def _backoff_seconds(attempt: int, retry_after: float | None) -> float:
    """Capped exponential backoff with partial jitter; honours Retry-After (capped)."""
    cap = settings.external_http_backoff_max_seconds
    if retry_after is not None:
        return min(retry_after, cap)
    exponential = settings.external_http_backoff_base_seconds * (2 ** (attempt - 1))
    # Partial jitter in [0.5, 1.0] × the capped delay — spreads retries without dropping
    # the floor to ~0 (which would defeat the backoff).
    return min(exponential, cap) * (0.5 + random.random() * 0.5)


async def resilient_request(
    method: str,
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
    max_attempts: int | None = None,
    follow_redirects: bool = False,
    **kwargs,
) -> httpx.Response:
    """Issue an HTTP request with bounded retry/backoff on transient failures.

    Retries only idempotent methods (GET/HEAD/OPTIONS) on connection/timeout errors and
    429/5xx responses. Returns the final ``httpx.Response`` (caller calls
    ``raise_for_status()``); re-raises the last transport error if every attempt fails.
    If ``client`` is omitted, a client with the standard timeout is created and closed here.
    """
    method_upper = method.upper()
    retryable = method_upper in _IDEMPOTENT_METHODS
    attempts = max_attempts if max_attempts is not None else settings.external_http_max_attempts
    attempts = max(1, attempts)

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=default_timeout(), follow_redirects=follow_redirects)
    try:
        response: httpx.Response | None = None
        for attempt in range(1, attempts + 1):
            try:
                response = await client.request(method_upper, url, **kwargs)
            except (httpx.TimeoutException, httpx.TransportError):
                # TimeoutException is itself a TransportError; both listed for clarity.
                if not retryable or attempt >= attempts:
                    raise
                await asyncio.sleep(_backoff_seconds(attempt, None))
                continue
            if retryable and attempt < attempts and response.status_code in _RETRYABLE_STATUS:
                await asyncio.sleep(_backoff_seconds(attempt, _retry_after_seconds(response)))
                continue
            return response
        # Unreachable safety net: every attempt either returns or raises above (the final
        # attempt never `continue`s). Kept for the type checker / defensive completeness.
        assert response is not None
        return response
    finally:
        if own_client:
            await client.aclose()
