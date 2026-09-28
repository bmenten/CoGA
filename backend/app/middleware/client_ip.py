"""The client address behind a known number of trusted reverse proxies (#520).

uvicorn's ``--proxy-headers`` with ``FORWARDED_ALLOW_IPS="*"`` (needed on Cloud Run,
where the immediate peer is a varying Google address) takes the *left-most*
``X-Forwarded-For`` entry — which a client sets itself. Behind Google's external
Application Load Balancer the header reads ``<client-supplied…>, <client-ip>, <lb-ip>``:
each trusted hop appends what it saw, so the real client is ``TRUSTED_PROXY_HOPS`` entries
from the right. The audit log's ``remoteIp`` and the signup/login throttles use this
address, so a client can no longer choose it.

With ``TRUSTED_PROXY_HOPS=0`` (the default) the request is left as uvicorn resolved it.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

Scope = dict[str, Any]
ASGIApp = Callable[[Scope, Callable[..., Awaitable[Any]], Callable[..., Awaitable[Any]]], Awaitable[None]]


def client_from_forwarded_for(header_values: list[str], hops: int) -> str | None:
    """The entry ``hops`` from the right of the combined X-Forwarded-For chain."""
    if hops <= 0:
        return None
    chain = [entry.strip() for value in header_values for entry in value.split(",") if entry.strip()]
    if len(chain) < hops:
        return None
    return chain[-hops]


class TrustedProxyClientMiddleware:
    def __init__(self, app: ASGIApp, *, hops: int) -> None:
        self.app = app
        self.hops = max(0, int(hops))

    async def __call__(self, scope: Scope, receive, send) -> None:
        if self.hops and scope.get("type") in {"http", "websocket"}:
            values = [
                value.decode("latin-1")
                for name, value in scope.get("headers") or []
                if name.lower() == b"x-forwarded-for"
            ]
            client = client_from_forwarded_for(values, self.hops)
            if client:
                port = (scope.get("client") or (None, 0))[1]
                scope = {**scope, "client": (client, port)}
        await self.app(scope, receive, send)
