import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from .core.clickhouse import (
    close_clickhouse_client,
    init_clickhouse_schema,
    wait_for_clickhouse,
)
from .core.config import settings
from .core.postgres import (
    close_postgres_engine,
    get_postgres_engine,
    get_postgres_sessionmaker,
    init_postgres_schema,
    wait_for_postgres,
)
from .core.coga_logging import configure_json_logging
from .dependencies import get_password_hash
from .middleware.request_logging import log_request_response
from .routers import all_routers
from .services.gene_info_jobs_pg import (
    gene_reference_refresh_worker,
    stop_gene_reference_worker,
)
from .services.family_package_import import (
    family_package_import_worker,
    stop_family_package_import_worker,
)
from .services.repeat_expansion_pg import seed_builtin_repeat_catalog
from .services.audit_log_pg import start_audit_log_worker, stop_audit_log_worker


async def init_postgres_admin_user() -> None:
    """Ensure a metadata admin user exists in Postgres."""

    engine = get_postgres_engine()
    async with engine.begin() as conn:
        existing = await conn.execute(
            text("SELECT id FROM users WHERE username = :username"),
            {"username": settings.admin_username},
        )
        if existing.scalar_one_or_none() is not None:
            return

        await conn.execute(
            text(
                """
                INSERT INTO users (
                    username,
                    hashed_password,
                    role,
                    email,
                    metadata,
                    created_at
                )
                VALUES (
                    :username,
                    :hashed_password,
                    'admin',
                    :email,
                    '{}'::jsonb,
                    :created_at
                )
                """
            ),
            {
                "username": settings.admin_username,
                "hashed_password": get_password_hash(settings.admin_password),
                "email": settings.admin_email,
                "created_at": datetime.now(timezone.utc),
            },
        )


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
    await init_postgres_schema()
    await init_postgres_admin_user()
    await start_audit_log_worker()
    session_factory = get_postgres_sessionmaker()
    async with session_factory() as session:
        await seed_builtin_repeat_catalog(session)

    await wait_for_clickhouse()
    await init_clickhouse_schema()
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
        await stop_audit_log_worker()
        await close_clickhouse_client()
        await close_postgres_engine()


app = FastAPI(lifespan=lifespan)
configure_json_logging()

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.middleware("http")(log_request_response)

for router in all_routers:
    app.include_router(router)
