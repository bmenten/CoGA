# AGENTS

## Repository Overview

- CoGA is a family-based genome browser: a FastAPI backend, a React/TypeScript frontend,
  Postgres for metadata and state, and ClickHouse for variants, run with Docker Compose. The
  architecture is in `docs/application-scheme.md`.
- It is operated as an **in-house IVD under IVDR Article 5(5)** at CMGG (ISO 15189); the
  device boundary is _annotated VCF → signed clinical report_ (see `README.md`). The technical
  file is in `docs/regulatory/`. Weigh every change for its clinical-safety, traceability and
  security consequences, not engineering merit alone.
- The **data is synthetic** — there is no production PHI and no legacy code or data to
  preserve. Build cleanly; ignore old datasets.
- **All actions in the interface are auditable** — queries and significant UI events flow
  through the durable audit/telemetry pipeline. The clinical audit trail and the report
  sign-out records are append-only and hash-chained; do not weaken these guarantees.

## Environment & Setup

- Python 3.12, Node.js 22 (at least the `engines` floor in `frontend/package.json`), Docker
  Compose.
- `cp .env.example .env` (it lists every setting with its default) and set
  `APP_ENV=development` for local work. With any other value the backend refuses to start on
  placeholder or weak secrets.
- Dev stack: `docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build -d` —
  UI on `http://localhost:3000`, API docs on `http://localhost:8000/docs`.
- Outside Docker: `docker compose up -d postgres clickhouse`; then in `backend/`,
  `pip install -r requirements-dev.txt` and `uvicorn app.main:app --reload --port 8000`; in
  `frontend/`, `npm ci && npm run dev` (`http://localhost:5173`).
- Setup, reset and troubleshooting: `docs/development.md`. Loading data: `docs/data-import.md`.

## Backend Guidelines

- Runtime utilities live in `backend/app/core/` (e.g. `config.py`, `postgres.py`,
  `clickhouse.py`, `object_storage.py`, `http_resilience.py`, `sql.py`).
- Postgres through SQLAlchemy async sessions, ClickHouse through direct clients. **All
  queries are parameterized**; `ORDER BY` is allowlisted and `LIMIT`/`OFFSET` int-coerced —
  keep it that way (no string interpolation into SQL or ClickHouse).
- Routers are in `backend/app/routers/`, registered in `routers/__init__.py` and mounted
  under `/api`. New endpoints follow the same pattern, enforce **project-scoped RBAC**
  (`services/access_control.py`, `services/family_metadata_context.py`) and use the right
  storage dependency.
- Clinical and business logic lives in `backend/app/services/`. The clinical-critical
  modules (scoring, traceability and sign-out, sample integrity) are the ones listed under
  `[tool.mypy]` in `pyproject.toml` and floored in `scripts/check-coverage-floor.py`.
- After changing a Pydantic model the API serves, regenerate the frontend types with
  `python scripts/generate-api-types.py`; CI runs it with `--check`.

## Database Schema

- Postgres: five idempotent baseline files in `backend/db/schema/postgres/` (`01_access` …
  `05_grants`), re-applied on every boot; there is no migration ledger. ClickHouse: the SQL
  file only creates the database; the per-assembly tables are created at runtime
  (`clickhouse_variant_storage.py`, `clickhouse_interval_tracks.py`). `docs/database.md` is
  the table-by-table reference — treat it as the source of truth and keep it current.
- The traceability tables (annotation manifest, evidence snapshots, the hash-chained clinical
  audit and sign-out tables, integrity anchors, `ui_events`) carry IVDR data-integrity
  guarantees. A new append-only table needs its `*_block_mutation` trigger, the `REVOKE` in
  `05_grants.sql`, and its entry in `backend/tests/integration/test_app_role_privileges.py`.

## Frontend & Integration

- React/TypeScript + Tailwind + Vite; the tracks and plots are in
  `frontend/src/components/visualizations/`.
- Call the API through `frontend/src/lib/api.ts`, and build every path with ``apiPath`…` ``
  from `lib/apiPath.ts`, which encodes each interpolated segment (use `raw()` for a query
  string).
- Reuse the shared styles for buttons, links, tables and layout (`base.css`, `controls.css`,
  `tables.css`, `layout.css` under `frontend/src/styles/theme/`). `theme.css` imports the modules in
  cascade order: put a rule in the module of the feature it styles, and never reorder the imports.
- The in-app docs are Markdown under `frontend/src/content/docs/` and render at `/docs`; the
  user-guide text is pinned by `UserGuideContent.test.tsx`.

## Security & Testing

- Follow `docs/security-posture.md`: project-scoped RBAC, append-only audit, encryption and
  TLS, rate limiting.
- Before you finish, run the checks in `CONTRIBUTING.md`, section "Before you open a PR". In
  short: `ruff check .`, `mypy` and `python -m pytest` from the repository root; in
  `frontend/`, `npm run tsc`, `npm run lint` and `npx vitest run` — run both tsc and vitest,
  because each misses what the other catches.
- A new or deleted test file needs its row in `docs/testing.md`
  (`./scripts/check-test-catalogue.sh`). After editing a handleiding chapter, run
  `python docs/handleiding/build_site.py` and commit the rebuilt HTML.
- Every PR adds a `CHANGELOG.md` entry. Until the first release candidate TF-18 §8 keeps no
  per-change rows (TF-18 §3a); a PR that changes a clinical output also adds one line, with its
  proposed level, to TF-10 §8. From the release candidate on, each change adds a TF-18 §8 row.
- The end-to-end harness (API contract, import, sign-out, Playwright journeys) backs the IVDR
  verification records — see `docs/regulatory/TF-09c` and `TF-09d`.
