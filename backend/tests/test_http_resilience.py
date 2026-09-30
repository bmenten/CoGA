"""Outbound HTTP resilience (#265) (timeouts + capped retry/backoff) and S3 Config.

The retry behaviour is exercised with ``httpx.MockTransport`` (no network) and backoff
zeroed so the tests are fast: idempotent requests retry transient failures (transport
errors + 429/5xx), non-idempotent requests and 4xx never retry, and the worst case is
bounded. The S3 client is asserted to carry bounded timeouts + adaptive retries.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from backend.app.core import http_resilience
from backend.app.core.config import settings
from backend.app.core.http_resilience import (
    _backoff_seconds,
    default_timeout,
    resilient_request,
)


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch):
    monkeypatch.setattr(settings, "external_http_backoff_base_seconds", 0.0)
    monkeypatch.setattr(settings, "external_http_backoff_max_seconds", 0.0)


def _scripted(steps):
    """A MockTransport handler that yields ``steps`` in order (an int status or an
    ``httpx.RequestError`` to raise); the last step repeats. Counts invocations."""
    state = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        step = steps[min(state["count"], len(steps) - 1)]
        state["count"] += 1
        if isinstance(step, httpx.RequestError):
            step.request = request
            raise step
        return httpx.Response(step, json={"ok": step})

    return handler, state


def _run(handler, method="GET", **kwargs):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await resilient_request(method, "http://svc/x", client=client, **kwargs)

    return asyncio.run(go())


def test_retries_idempotent_get_on_5xx_then_succeeds():
    handler, state = _scripted([503, 503, 200])
    resp = _run(handler, max_attempts=3)
    assert resp.status_code == 200 and state["count"] == 3


def test_returns_final_retryable_status_after_max_attempts():
    handler, state = _scripted([503])
    resp = _run(handler, max_attempts=2)
    assert resp.status_code == 503 and state["count"] == 2  # exhausted, not infinite


def test_does_not_retry_non_idempotent_post():
    handler, state = _scripted([503])
    resp = _run(handler, method="POST", max_attempts=3)
    assert resp.status_code == 503 and state["count"] == 1  # POST is never retried


def test_does_not_retry_client_error():
    handler, state = _scripted([404])
    resp = _run(handler, max_attempts=3)
    assert resp.status_code == 404 and state["count"] == 1  # 4xx won't change on retry


def test_retries_transport_error_then_succeeds():
    handler, state = _scripted([httpx.ConnectError("boom"), 200])
    resp = _run(handler, max_attempts=3)
    assert resp.status_code == 200 and state["count"] == 2


def test_raises_after_max_transport_errors():
    handler, state = _scripted([httpx.ConnectError("down")])
    with pytest.raises(httpx.ConnectError):
        _run(handler, max_attempts=2)
    assert state["count"] == 2  # bounded, not unbounded


def test_creates_and_closes_own_client_forwards_config_and_body_readable(monkeypatch):
    real = httpx.AsyncClient
    captured: dict = {}
    created: list = []

    def factory(*args, **kwargs):
        captured.update(kwargs)
        client = real(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": 1})))
        created.append(client)
        return client

    monkeypatch.setattr(http_resilience.httpx, "AsyncClient", factory)
    # client=None → own client created here, configured, used, and closed in finally.
    resp = asyncio.run(resilient_request("GET", "http://svc/x", follow_redirects=True))
    assert resp.json() == {"ok": 1}                       # body buffered → readable after close
    assert isinstance(captured["timeout"], httpx.Timeout)  # standard timeout forwarded
    assert captured["follow_redirects"] is True            # follow_redirects forwarded
    assert created and created[0].is_closed                # own client was closed


def test_backoff_is_capped_and_honours_retry_after(monkeypatch):
    monkeypatch.setattr(settings, "external_http_backoff_base_seconds", 1.0)
    monkeypatch.setattr(settings, "external_http_backoff_max_seconds", 4.0)
    for attempt in range(1, 7):
        assert 0.0 <= _backoff_seconds(attempt, None) <= 4.0  # capped
    assert _backoff_seconds(1, 2.0) == 2.0  # Retry-After honoured
    assert _backoff_seconds(1, 100.0) == 4.0  # ...but capped


def test_default_timeout_reflects_settings(monkeypatch):
    monkeypatch.setattr(settings, "external_http_connect_timeout_seconds", 3.0)
    monkeypatch.setattr(settings, "external_http_read_timeout_seconds", 7.0)
    timeout = default_timeout()
    assert timeout.connect == 3.0 and timeout.read == 7.0


def test_s3_client_has_bounded_timeouts_and_retries():
    pytest.importorskip("boto3")
    from backend.app.core import object_storage

    object_storage._s3_client.cache_clear()
    config = object_storage._s3_client().meta.config
    assert config.connect_timeout == settings.s3_connect_timeout_seconds
    assert config.read_timeout == settings.s3_read_timeout_seconds
    # botocore resolves max_attempts -> total_max_attempts (counting the initial attempt,
    # so total == setting or setting+1 depending on version); assert adaptive + bounded.
    retries = config.retries
    assert retries["mode"] == "adaptive"
    total = retries.get("total_max_attempts", retries.get("max_attempts"))
    assert settings.s3_max_attempts <= total <= settings.s3_max_attempts + 1
