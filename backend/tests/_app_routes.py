"""Every route of the app, and the dependencies each one runs, for the route sweeps.

The sweeps that hold every route to a guard walk the app here: the sign-in
(``test_routes_require_sign_in.py``), the admin role (``test_admin_route_gating.py``) and the
project access levels (``test_project_access_levels.py``). A route is a guard's when the guard
is among the dependencies its request resolves, at any depth (``get_current_admin_user`` runs
on top of ``get_current_user``). The e2e sweep (``e2e/test_e2e_project_reader_role.py``) walks
the app the same way on real Postgres.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Iterator

from fastapi.routing import APIRoute

from backend.app.main import app


def app_routes(routes: Iterable[Any] | None = None) -> Iterator[Any]:
    """Every route of the app. FastAPI wraps an included router in an ``_IncludedRouter``,
    whose ``effective_candidates`` are its routes with their full paths and dependencies."""
    for route in app.routes if routes is None else routes:
        if hasattr(route, "effective_candidates"):
            yield from app_routes(route.effective_candidates())
        elif isinstance(route, APIRoute) or getattr(route, "dependant", None) is not None:
            yield route


def dependency_calls(dependant: Any) -> set[Any]:
    """Every dependency a request of the route resolves, at any depth."""
    found: set[Any] = set()
    for dependency in dependant.dependencies:
        found.add(dependency.call)
        found |= dependency_calls(dependency)
    return found


def route_operations() -> dict[tuple[str, str], Any]:
    """Each operation of the app, ``(method, path template)``, with its route. HEAD, which
    Starlette answers for every GET, is left out."""
    return {
        (method, route.path_format): route
        for route in app_routes()
        for method in sorted(route.methods or ())
        if method != "HEAD"
    }


def filled_path(path_format: str, value: str = "PLACEHOLDER1") -> str:
    """The path template with ``value`` in each of its parameters."""
    return re.sub(r"\{[^}]+\}", value, path_format)
