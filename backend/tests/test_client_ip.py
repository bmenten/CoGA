"""The client address behind trusted proxies (#520).

With FORWARDED_ALLOW_IPS="*" uvicorn takes the left-most X-Forwarded-For entry, which a
client sets itself; the audit log and the login/signup throttles would use it.
"""

from __future__ import annotations

import asyncio

from backend.app.middleware.client_ip import TrustedProxyClientMiddleware, client_from_forwarded_for


def _run(hops: int, headers: list[tuple[bytes, bytes]], client=("10.0.0.1", 5555)):
    seen: dict = {}

    async def app(scope, receive, send):
        seen.update(scope)

    middleware = TrustedProxyClientMiddleware(app, hops=hops)
    asyncio.run(middleware({"type": "http", "headers": headers, "client": client}, None, None))
    return seen["client"]


def test_the_client_is_taken_past_the_load_balancer_not_from_the_left() -> None:
    # Google's external ALB appends "<client-ip>,<lb-ip>" after whatever the client sent.
    headers = [(b"x-forwarded-for", b"6.6.6.6, 203.0.113.9, 34.120.0.1")]
    assert _run(2, headers) == ("203.0.113.9", 5555)


def test_repeated_headers_form_one_chain() -> None:
    headers = [(b"x-forwarded-for", b"6.6.6.6"), (b"x-forwarded-for", b"203.0.113.9, 34.120.0.1")]
    assert _run(2, headers)[0] == "203.0.113.9"


def test_a_short_chain_or_no_trust_leaves_the_request_as_it_was() -> None:
    assert _run(2, [(b"x-forwarded-for", b"203.0.113.9")]) == ("10.0.0.1", 5555)
    assert _run(0, [(b"x-forwarded-for", b"6.6.6.6, 203.0.113.9")]) == ("10.0.0.1", 5555)
    assert _run(1, []) == ("10.0.0.1", 5555)


def test_the_chain_helper() -> None:
    assert client_from_forwarded_for(["a, b, c"], 1) == "c"
    assert client_from_forwarded_for(["a, b, c"], 3) == "a"
    assert client_from_forwarded_for(["a"], 2) is None
    assert client_from_forwarded_for(["a"], 0) is None
