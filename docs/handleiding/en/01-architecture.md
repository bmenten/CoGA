# 1. General architecture & structure

This chapter is the map for the rest of the manual. It describes the three layers of CoGA (frontend, backend and two databases) and follows one request from a click in the browser to the data and back. It points out where the folders, the configuration and the main security measures are. It summarises `docs/application-scheme.md` and the technical file (`docs/regulatory/TF-02-device-description.md`).

## CoGA in brief

CoGA (*Comprehensive Genomic Analysis*) is a platform for variant interpretation, genome visualisation and clinical review at family level. It runs as an **in-house IVD under IVDR Article 5(5)** at CMGG, an ISO 15189-accredited laboratory. *IVD* stands for in-vitro diagnostics; the *IVDR* is the European regulation for it. The regulated part (the "device boundary") runs from the **annotated VCF file** to the **closed, frozen analysis in CoGA**. A supervisor (or a user with a higher role) closes the analysis with *Sign out*: the analytical sign-out, which freezes the analysis. The clinical report is drafted and medically signed off in the EPD (the hospital's electronic patient record) and refers 1:1 to that analysis. The EPD is outside the device. The data in the system is synthetic.

## The three layers

| Layer | Technology | Role | Where in the code |
| --- | --- | --- | --- |
| Frontend | React and TypeScript (built with Vite, styled with Tailwind) | The user interface: login, dashboard, family workspace, filter pages, visualisations | `frontend/src/` |
| Backend | FastAPI (Python) | The API server: authentication, access control, clinical logic, database queries | `backend/app/` |
| Databases | Postgres and ClickHouse | Metadata and review state (Postgres) alongside the large variant store (ClickHouse) | `backend/db/schema/` and the ClickHouse services in `backend/app/services/` |

Each layer has one task. This makes it clear where access control, validation and storage happen, which makes auditing and debugging simpler.

### Why two databases?

- **Postgres** is a relational database and the *source of truth* for metadata and state: users and project access, families, samples and pedigree, reviews and classifications, panels, reference data and the audit trail. That data is relatively small, highly structured and changes often.
- **ClickHouse** is a column-oriented database for very large datasets. It stores the variant rows (small variants and structural variants), the genotypes per sample and the large interval tracks (coverage, CNV segments, APCAD, haplotypes). One family can have millions of rows.

For every request, Postgres first decides who may see what. Then the backend fetches the variants from ClickHouse and attaches the review state from Postgres to them, e.g. a tag or an ACMG class. Chapter 3 describes the tables.

**Where in the code:** `backend/app/core/postgres.py` and `backend/app/core/clickhouse.py` (the connections). The division of tasks is in `docs/application-scheme.md`, section *Storage boundary*: "Postgres is authoritative for metadata and state. ClickHouse is authoritative for variant payloads."

## From click to data: the path of one request

Example: an analyst opens the small variants of a family.

1. **Click in the browser.** The page (e.g. `frontend/src/pages/families/FamilySmallVariantsPage.tsx`) requests data through the shared API client.
2. **The API client sends the request.** Almost all requests go through one *axios* instance (axios is a JavaScript library for HTTP requests) with the base address `/api`. An *interceptor* (code that intercepts every outgoing request) adds the JWT token as `Authorization: Bearer <token>`, except when logging in, registering and requesting the sign-in method. A JWT (*JSON Web Token*) is a signed access credential; [chapter 5](05-login-authentication.md) explains it. Two parts send the token themselves. The embedded IGV browser fetches its alignments and tracks itself and requests the token for every request, as the interceptor does. The last UI events when leaving a page carry the session's token, which the telemetry refreshes in time ([chapter 15](15-other-modules-and-admin.md)).
3. **The request reaches the backend.** In development the Vite development server forwards `/api` to the backend; in the Docker setup the frontend container does this (`frontend/server.mjs`). All routers are mounted under `/api`, except `GET /metrics` for monitoring, which deliberately sits outside it (chapter 7). First the request passes through a series of *middleware* (intermediate layers that process every request and response): client-IP determination behind proxies (`TRUSTED_PROXY_HOPS`), security headers, trailing-slash normalisation and request logging/audit. [Chapter 7](07-backend-routers-and-services.md) describes the order.
4. **The router checks access.** Every protected endpoint requires the *dependency* `get_current_user` (a function that FastAPI runs before the endpoint). It checks the token and loads the user fresh from Postgres. Access is **project-scoped** (RBAC: *Role-Based Access Control*); [chapter 2](02-security-roles-permissions.md) explains how.
5. **A service does the actual work.** Routers stay thin; the clinical logic is in `backend/app/services/`, for family variants e.g. in `clickhouse_family_variants.py`.
6. **The service queries the right database.** Metadata comes from Postgres (through SQLAlchemy, asynchronously), variants from ClickHouse (through a direct client). All values are passed as parameters, never as text in the query (chapter 7).
7. **The response goes back.** FastAPI converts the result to JSON, the middleware adds the security headers and the browser shows it. If the client gets a `401` (not signed in), it clears the session and returns to `/login`.

**Where in the code:** `frontend/src/lib/api.ts` (API client and interceptors), `frontend/vite.config.mts` (development proxy), `backend/app/main.py` (app, middleware and routers), `backend/app/dependencies.py` (`get_current_user`). The same flow is described in `docs/application-scheme.md`, section *Runtime flow*.

Frontend and backend are separate processes that talk to each other only through HTTP and JSON. That separation is deliberate: *all* access control happens on the server, not in the browser.

**CORS** (*Cross-Origin Resource Sharing*: which websites may call the API) is configured strictly:

- An origin pattern must be fully anchored: start with `^` and end with `$`. Otherwise the configuration refuses it, because a pattern that is too broad, together with credentials sent along, opens a way around.
- Outside development the backend does not start as long as `CORS_ORIGINS` or `CORS_ORIGIN_REGEX` allows a local origin (`localhost`, `127.0.0.1`, `::1`, `0.0.0.0`) or any site at all. The development settings allow localhost; in that case a page on the user's own machine could call the API as that signed-in user.
- A deployment serves the interface from its own origin and needs no cross-origin access. Terraform sets that own origin and an empty pattern.

**Where in the code:** the `CORSMiddleware` in `backend/app/main.py`; in `backend/app/core/config.py`, `validate_cors_origin_regex` (the anchoring) and `_cross_origin_exposures`, which `validate_security_defaults` calls outside development.

## The folders at a glance

The repository holds two application folders (`backend/`, `frontend/`), the documentation (`docs/`), the infrastructure as code for Google Cloud (`terraform/`) and the Docker Compose files.

| Folder or file (backend) | Role |
| --- | --- |
| `backend/app/core/` | Connections and runtime: settings (`config.py`), Postgres, ClickHouse, Azure, logging, a family's folders |
| `backend/app/routers/` | The API: one file per domain, bundled in `routers/__init__.py` |
| `backend/app/services/` | The clinical and business logic, including the traceability stack |
| `backend/app/middleware/` | Intermediate layers for client IP, security headers and request logging |
| `backend/app/main.py` | Builds the app: routers, CORS, middleware and the startup routine |
| `backend/app/db_migrate.py` | Schema migration and first administrator, also runnable as a separate step |
| `backend/db/schema/` | The five Postgres baselines (`01_access` to `05_grants`) and the ClickHouse bootstrap |
| `backend/db/migrations/` | The Alembic revision `0001`, which sets up an empty database from the five baselines; the migration ledger `alembic_version` records the revision |

| Folder (frontend) | Role |
| --- | --- |
| `frontend/src/pages/` | The screens, grouped by domain |
| `frontend/src/components/` | Reusable building blocks, including the route guards and `visualizations/` |
| `frontend/src/lib/` | Non-visual helper code: API client, session, genotypes, ACMG logic |
| `frontend/src/content/docs/` | The in-app documentation (user guide and reference docs), shown at `/docs` |

Routing uses `react-router`. Only the sign-in pages (`/login` and `/login/emergency`) and `/signup` are public. All other screens sit behind the guard `RequireAuth`. The administration screens (among others `/admin/...`, with the projects under `/admin/access/projects`) also sit behind `RequireAdmin`; the administration dashboard `/admin` and the workspaces of a superuser (among others `/package-import`) behind `<RequireAdmin superuser />`, which also lets a superuser through. Those guards are a convenience, not security (chapter 2).

There is no navigation bar. The main sections (*Projects*, *Variant explorer*, *Gene explorer*, *CNV explorer*, *Panels*, *Report codes*, for a superuser or administrator *Admin*, and *User guide*) are in a menu behind a small arrow on the right of the header bar. Below the header bar, a breadcrumb trail follows the route. On a family, the project the workspace works in sits between *Families* and the family; it leads to the dashboard with that project expanded.

**Where in the code:** the route tree in `frontend/src/index.tsx`; the menu in `frontend/src/components/HeaderMenu.tsx` and the breadcrumb trail in `Breadcrumbs.tsx`.

## Configuration

All settings come in as environment variables and are bundled in one `Settings` object, which the rest of the code reads. `.env.example` lists every setting with its default value. The configuration is also a security measure: outside development the backend refuses to start with weak or missing secrets. The full rule is in [chapter 2](02-security-roles-permissions.md#refusal-to-start-with-weak-secrets).

**Where in the code:** `backend/app/core/config.py` (`Settings` and `validate_security_defaults`). The API prefix `/api` is also there, as `API_PATH_PREFIX`.

## Containers and startup

The stack runs through Docker Compose as five services: `postgres`, `clickhouse`, `backend`, `import-worker` (the package imports, chapter 4) and `frontend`. The database images are pinned to an exact digest, for reproducibility. The backend starts only once both databases are healthy; the frontend and the import worker only once the backend is healthy. ClickHouse gets a generous shutdown grace period, because a write that is cut off too early can damage data parts. The development variant `docker-compose.dev.yml` sets `APP_ENV=development`, reloads changed code immediately and uses the Vite development server.

What the backend does at startup (schema, first administrator, reference data, background tasks) is in [chapter 4](04-deployment-and-seeding.md).

**Where in the code:** `docker-compose.yml`, `docker-compose.dev.yml` and the `lifespan` function in `backend/app/main.py`.

## Security and traceability in the architecture

- **Access control on the server.** All authorisation happens in the backend (`get_current_user`, `get_current_superuser`, `get_current_admin_user` and the project scoping). The browser decides nothing.
- **Every request leaves a trace.** The request-logging middleware writes every HTTP request to the append-only table `audit_log_events`. The clinical audit trail and the report sign-out records are, in addition, append-only and hash-chained: each row refers cryptographically to the previous one, so that changing or deleting becomes visible ([chapter 11](11-report-and-traceability.md)).
- **No schema disclosure.** Outside development, `/docs`, `/redoc` and `/openapi.json` are switched off.
- **Fail-closed configuration.** The app does not start with weak secrets or unsafe production settings (chapter 2).
- **One clear source of truth.** Metadata is in Postgres, variants in ClickHouse: for each piece of data, one database is authoritative.

**Where in the code:** `backend/app/dependencies.py`, `backend/app/middleware/`, `_docs_kwargs` in `backend/app/main.py`, and the traceability services in `backend/app/services/` (among others `clinical_audit_service.py`, `hash_chain.py` and `integrity_anchor_service.py`).

## Technology

- **Frontend:** React, TypeScript, Vite, Tailwind CSS, react-router, TanStack Query (server state and caching), axios, D3 (visualisations) and igv (the embedded IGV genome browser); tests with Vitest and Playwright.
- **Backend:** FastAPI with Uvicorn, SQLAlchemy (asynchronous) with asyncpg for Postgres, clickhouse-connect for ClickHouse, Pydantic (validation and settings), PyJWT (tokens), bcrypt (passwords) and cryptography (among others the signature of the integrity anchors).

The exact versions are in `frontend/package-lock.json` and `backend/requirements.txt`; the SOUP register (`docs/regulatory/TF-08-soup-register.md`) tracks them for the technical file. The product version is in `VERSION`.

The field `app_version` in the backend is the build identity. It is filled in when the image is built and frozen in every signed report (chapter 11). Only the release build gets the version from `VERSION` itself. Every other build (from `main`, manual or local) gets that version with `dev` added (`0.1.0-dev`, or `0.1.0-rc.1.dev` after the release candidate), so that it can never pass for a release (`scripts/build-version.sh`).

## Key files

| File | Role |
| --- | --- |
| `docs/application-scheme.md` | Architecture and storage boundaries |
| `backend/app/main.py` | Builds the app: routers, CORS, middleware, startup routine |
| `backend/app/routers/__init__.py` | Bundles all routers |
| `backend/app/dependencies.py` | `get_current_user`, `get_current_superuser`, `get_current_admin_user` and token issuance |
| `backend/app/core/config.py` | All settings and the fail-closed checks |
| `frontend/src/index.tsx` | Frontend entry point and route tree with guards |
| `frontend/src/lib/api.ts` | The shared API client: `/api` base, token and 401 handling |
| `docker-compose.yml` / `docker-compose.dev.yml` | The five services (Docker setup and development) |
