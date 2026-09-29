"""Security response headers for the API (defense-in-depth).

The browser-facing clickjacking/XSS surface is the SPA (served by the frontend
container, which sets its own page-level CSP — see frontend/securityHeaders.mjs).
These headers harden the JSON API responses too. The API serves JSON only, so a
maximally-strict CSP (`default-src 'none'`) is appropriate — nothing should ever be
loaded from an API origin.

HSTS is opt-in (``settings.enable_hsts``) so it is never emitted over plain HTTP and
is safe to enable only where TLS terminates in front of the app (e.g. a reverse proxy).
"""

from __future__ import annotations

from fastapi import Request, Response

from ..core.config import settings

# Applied to every API response. setdefault() is used at call time so a route that
# deliberately set one of these (rare) is never clobbered.
_STATIC_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-site",
}


async def security_headers_middleware(request: Request, call_next):
    """Set hardening headers on every response the application returns.

    Registered in main.py just inside TrustedProxyClientMiddleware, which is the
    outermost, so it wraps every route and every other middleware. The bare 500 that
    Starlette's ServerErrorMiddleware sends for an unhandled exception is produced
    outside it and carries none of these headers.
    """
    response: Response = await call_next(request)
    for name, value in _STATIC_HEADERS.items():
        response.headers.setdefault(name, value)
    if settings.enable_hsts:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
        )
    return response
