import asyncio
from contextlib import asynccontextmanager
from typing import cast

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .core.clickhouse import (
    close_clickhouse_client,
    init_clickhouse_schema,
    wait_for_clickhouse,
)
from .core.config import API_PATH_PREFIX, settings
from .core.postgres import (
    close_postgres_engine,
    get_postgres_sessionmaker,
    init_postgres_schema,
    wait_for_postgres,
)
from .core.coga_logging import (
    configure_json_logging,
    install_access_log_redaction,
    install_server_error_redaction,
)
from .core.csv_export import EXPORT_HEADERS
from .db_migrate import init_postgres_admin_user
from .middleware.client_ip import TrustedProxyClientMiddleware
from .middleware.request_logging import log_request_response
from .middleware.security_headers import security_headers_middleware
from .routers import all_routers
from .routers.metrics import router as metrics_router
from .services.gene_info_jobs_pg import (
    gene_reference_refresh_worker,
    queue_startup_gene_reference_refresh_if_needed,
    stop_gene_reference_worker,
)
from .services.access_control import RecordNotVisible
from .services.family_variant_filters import SampleFilterError
from .services.family_package_import import (
    drop_orphaned_import_backups,
    family_package_import_worker,
    stop_family_package_import_worker,
)
from .services.repeat_expansion_pg import seed_builtin_repeat_catalog
from .services.reference_metadata_service import seed_builtin_reference_tracks
from .services.reference_source_service import (
    ensure_human_grch38_reference_on_startup,
    ensure_human_t2t_reference_on_startup,
)
from .services.hpo_service import ensure_hpo_ontology_on_startup
from .services.audit_log_pg import start_audit_log_worker, stop_audit_log_worker
from .services.clickhouse_integrity_monitor import (
    start_clickhouse_integrity_monitor,
    stop_clickhouse_integrity_monitor,
)
from .services.clickhouse_variant_storage import verify_clickhouse_variant_storage_identity
from .services.ui_event_pg import start_ui_event_worker, stop_ui_event_worker


@asynccontextmanager
async def lifespan(app: FastAPI):
    if getattr(app.state, "skip_startup_tasks", False):
        yield
        return

    worker_stop = asyncio.Event()
    worker_task = None
    family_import_worker_stop = asyncio.Event()
    family_import_worker_tasks: list[asyncio.Task] = []
    await wait_for_postgres()
    # Schema DDL + admin seed require the table OWNER. When migrations are run out-of-band
    # (as the owner) the app boots as the restricted runtime role ``coga_app`` and must NOT
    # attempt DDL — ``coga_app`` cannot run it and startup would crash-loop. See
    # docs/db-runtime-role-runbook.md and backend/app/db_migrate.py.
    if settings.postgres_run_schema_migrations_on_startup:
        await init_postgres_schema()
        await init_postgres_admin_user()
    await start_audit_log_worker()
    await start_ui_event_worker()
    session_factory = get_postgres_sessionmaker()
    async with session_factory() as session:
        await seed_builtin_repeat_catalog(session)
        await ensure_human_grch38_reference_on_startup(session)
        # Opt-in second assembly; never blocks startup if it fails.
        await ensure_human_t2t_reference_on_startup(session)
        await ensure_hpo_ontology_on_startup(
            session,
            ontology_path=settings.hpo_ontology_path,
            ontology_url=settings.hpo_ontology_url,
            download_if_missing=settings.hpo_download_if_missing,
            enabled=settings.hpo_bootstrap_on_startup,
        )
        await seed_builtin_reference_tracks(session)
        await queue_startup_gene_reference_refresh_if_needed(session)

    await wait_for_clickhouse()
    await init_clickhouse_schema()
    # Refuse to start on variant tables whose rows are not identified by their callset
    # (created by an earlier version): their merges lose calls. See docs/database.md.
    await verify_clickhouse_variant_storage_identity()
    # An overwrite import's backup tables (a copy of the family's earlier variant rows)
    # that no running import owns: left by an import whose process stopped. Best-effort.
    await drop_orphaned_import_backups()
    await start_clickhouse_integrity_monitor()
    worker_task = asyncio.create_task(gene_reference_refresh_worker(worker_stop))
    family_import_worker_tasks = [
        asyncio.create_task(family_package_import_worker(family_import_worker_stop))
        for _ in range(settings.family_import_worker_count)
    ]

    try:
        yield
    finally:
        await stop_gene_reference_worker(worker_task, worker_stop)
        for family_import_worker_task in family_import_worker_tasks:
            await stop_family_package_import_worker(
                family_import_worker_task,
                family_import_worker_stop,
            )
        await stop_clickhouse_integrity_monitor()
        await stop_audit_log_worker()
        await stop_ui_event_worker()
        await close_clickhouse_client()
        await close_postgres_engine()


def _docs_kwargs() -> dict[str, str | None]:
    """Disable the interactive API docs / OpenAPI route outside development.

    Returns the FastAPI kwargs that turn off /docs, /redoc and /openapi.json in
    production (schema-disclosure hardening). The in-process ``app.openapi()`` method
    still works regardless — only the public HTTP routes are gated — so the
    trailing-slash normaliser below (which reads ``app.openapi()``) is unaffected.
    """
    if settings.is_development:
        return {}
    return {"docs_url": None, "redoc_url": None, "openapi_url": None}


app = FastAPI(
    title="CoGA",
    version=settings.app_version,
    lifespan=lifespan,
    **_docs_kwargs(),
)
configure_json_logging()
install_access_log_redaction()
install_server_error_redaction()

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # The CSV exports report truncation in headers the UI reads (#512).
    expose_headers=list(EXPORT_HEADERS),
)

app.middleware("http")(log_request_response)

api_router = APIRouter(prefix=API_PATH_PREFIX)
for router in all_routers:
    api_router.include_router(router)

app.include_router(api_router)
# /metrics sits outside /api on purpose: the load balancer routes only /api/* to the
# backend, so a scraper reaches it inside the deployment and the internet never does.
app.include_router(metrics_router)


async def _refuse_unreadable_sample_filter(request: Request, exc: Exception) -> JSONResponse:
    # A sample filter value that cannot be read is the caller's error: answer 422 with the
    # value named, whichever search it reached (#686).
    return JSONResponse(status_code=422, content={"detail": str(exc)})


app.add_exception_handler(SampleFilterError, _refuse_unreadable_sample_filter)


async def _answer_record_not_visible(request: Request, exc: Exception) -> Response:
    # A record outside the user's projects is answered by the handler every other 404 goes
    # through, so the response cannot differ from an unknown record's. Only the request's
    # audit row names it (request_logging; REQ-SEC-001).
    hidden = cast(RecordNotVisible, exc)
    request.state.record_hidden = hidden.kind
    return await http_exception_handler(request, hidden)


app.add_exception_handler(RecordNotVisible, _answer_record_not_visible)

# The trailing-slash normaliser needs the set of `/api/...` collection-root paths
# (e.g. `/api/families/`). We read them from the OpenAPI schema rather than
# `app.routes`: Starlette 1.x no longer flattens included-router routes into
# `app.routes` (it wraps them in an opaque `_IncludedRouter`), so iterating
# `app.routes` only sees the top-level docs routes. The OpenAPI `paths` map is a
# stable public interface that exposes the full route paths on every version.
# Computed lazily on first request and cached, so schema generation does not run
# at import time.
_api_collection_alias_paths: frozenset[str] | None = None


def _collection_alias_paths() -> frozenset[str]:
    global _api_collection_alias_paths
    if _api_collection_alias_paths is None:
        try:
            openapi_paths = app.openapi().get("paths", {})
        except Exception:  # noqa: BLE001 - never let schema generation break request handling
            return frozenset()
        _api_collection_alias_paths = frozenset(
            path[:-1]
            for path in openapi_paths
            if path.startswith("/api/") and path.endswith("/") and "{" not in path
        )
    return _api_collection_alias_paths


@app.middleware("http")
async def normalize_api_collection_root_paths(request, call_next):
    """Accept collection roots with or without FastAPI's trailing slash."""

    path = request.scope.get("path")
    if path in _collection_alias_paths():
        request.scope["path"] = f"{path}/"
        raw_path = request.scope.get("raw_path")
        if isinstance(raw_path, bytes) and not raw_path.endswith(b"/"):
            request.scope["raw_path"] = raw_path + b"/"

    return await call_next(request)


# Registered second to last, so only TrustedProxyClientMiddleware (below) wraps it: stamp
# the hardening headers onto every response the routes and the other middleware return.
app.middleware("http")(security_headers_middleware)

# Outermost of all: resolve the client address before logging, throttling or audit
# read it (#520). A no-op unless TRUSTED_PROXY_HOPS is set.
app.add_middleware(TrustedProxyClientMiddleware, hops=settings.trusted_proxy_hops)
