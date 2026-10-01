"""``GET /metrics``: the operational metrics a monitoring scraper reads.

Deliberately outside ``/api`` and outside ``routers/__init__.py``'s list: the load balancer
routes only ``/api/*`` to the backend, so this path is never served to the internet, and the
frontend server proxies ``/api`` only. It is off unless ``METRICS_TOKEN`` is set, and then
answers only a request that sends that token as a bearer token. It is left out of the
OpenAPI schema. What it exposes is described in ``services/operational_metrics.py`` and
``docs/monitoring.md``; none of it is clinical data.
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, HTTPException, Request, Response, status

from ..core.config import settings
from ..services.operational_metrics import CONTENT_TYPE, render_metrics

router = APIRouter(tags=["metrics"])


def _require_metrics_token(request: Request) -> None:
    token = settings.metrics_token.strip()
    if not token:
        # Off: answer as if there were no such endpoint.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    scheme, _, credentials = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(
        credentials.strip().encode("utf-8"), token.encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid metrics token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    _require_metrics_token(request)
    return Response(
        content=await render_metrics(),
        media_type=CONTENT_TYPE,
        headers={"Cache-Control": "no-store"},
    )
