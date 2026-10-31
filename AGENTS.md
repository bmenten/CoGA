# AGENTS

## Repository Overview

- CoGA is a family-based genome browser: a FastAPI backend, a React/TypeScript frontend,
  Postgres for metadata and state, and ClickHouse for variants, run with Docker Compose. The
  architecture is in `docs/application-scheme.md`.
- It is operated as an **in-house IVD under IVDR Article 5(5)** at CMGG (ISO 15189); the
  device boundary is _annotated VCF → closed, frozen analysis in CoGA_ (see `README.md`). The
  clinical report is drafted and medically signed in the hospital's EPD, outside the device, and
  refers 1:1 to that analysis; CoGA's sign-out is the analyst's analytical sign-off, never the
  medical one. The technical file is in `docs/regulatory/`. Weigh every change for its
  clinical-safety, traceability and security consequences, not engineering merit alone.
- The **repository's data is synthetic** — there is no production PHI and no legacy code or
  data to preserve. Build cleanly; ignore old datasets.
- The folders under `data/` that `.gitignore` excludes (`data/families/` and the like) may
  hold **real patient data** on a developer's machine. Nothing from them reaches git, GitHub or
  any outside service: no file, family or sample ID, gene, region or value, not in a test, a
  doc, a commit message or a branch name. Tests and docs use synthetic IDs (`PROBAND`,
  `MOTHER`, `FATHER1`, `EMB1`).
- Anything that reaches GitHub (files, commits, PRs, issues) names roles, never people: "the
  owner", "QA". The register of who holds each role is kept in the CMGG quality system.
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
- A record outside the caller's projects answers 404 exactly like an unknown one
  (`RecordNotVisible`), never 403. A UUID from a path, query or body is read with
  `require_uuid` / `canonical_uuid` (`core/sql.py`); a family or sample ID follows
  `services/family_identifiers.py` (printable, no whitespace).
- Logs carry no values: log an exception with `describe_error` / `describe_traceback`
  (`core/coga_logging.py`), never its text, which can repeat SQL parameters and patient data.
  A clinical note or a signed-record section that could not be built holds a fixed sentence,
  never the error's text.
- Clinical and business logic lives in `backend/app/services/`. The clinical-critical
  modules (scoring, variant search, storage and review, data and knowledgebase import,
  traceability and sign-out, sample integrity) are the ones listed under `[tool.mypy]` in
  `pyproject.toml` and floored in `scripts/check-coverage-floor.py`.
- After changing a Pydantic model the API serves, regenerate the frontend types with
  `python scripts/generate-api-types.py`; CI runs it with `--check`.

## Database Schema

- Postgres: five idempotent baseline files in `backend/db/schema/postgres/` (`01_access` …
  `05_grants`), then the Alembic revisions in `backend/db/migrations/versions/`, recorded in
  the ledger table `alembic_version` (`core/postgres_migrations.py`). Until the release
  candidate every migration run re-applies the baselines first, so a baseline edit reaches
  existing databases; a change to a table that already exists (a `CHECK`, a column) also
  needs a revision, which must end at the baseline's definition. Migrations run as the table
  owner, never as `coga_app`. ClickHouse: the SQL
  file only creates the database; the per-assembly tables are created at runtime
  (`clickhouse_variant_storage.py`, `clickhouse_interval_tracks.py`). `docs/database.md` is
  the table-by-table reference — treat it as the source of truth and keep it current.
- The traceability tables (annotation manifest, evidence snapshots, the hash-chained clinical
  audit, sign-out and pedigree structure-version tables, integrity anchors, and the
  append-only `audit_log_events`, `ui_events` and `qc_threshold_changes`) carry IVDR
  data-integrity guarantees. A new append-only table needs its `*_block_mutation` trigger, the
  `REVOKE` in `05_grants.sql`, and its entry in
  `backend/tests/integration/test_app_role_privileges.py`.
- ClickHouse has no transactions, so every writer of a family's variants holds
  `lock_family_variant_writes` (`services/family_variant_write_lock.py`) from before its first
  read of the rows until its transaction ends (`docs/database.md`, "One write at a time per
  family"). It deletes with `ALTER TABLE … DELETE … SETTINGS mutations_sync = 1` and never stores
  a `sign = -1` row: the `entries` readers filter `sign = 1` without `FINAL` and rely on it
  ("Deletes are synchronous").

## Frontend & Integration

- React/TypeScript + Tailwind + Vite; the tracks and plots are in
  `frontend/src/components/visualizations/`.
- Call the API through `frontend/src/lib/api.ts`, and build every path with ``apiPath`…` ``
  from `lib/apiPath.ts`, which encodes each interpolated segment (use `raw()` for a query
  string). Build an in-app route that carries an ID with ``routePath`…` `` from
  `lib/routePath.ts`, which encodes the same way.
- Reuse the shared styles for buttons, links, tables and layout (`base.css`, `controls.css`,
  `tables.css`, `layout.css` under `frontend/src/styles/theme/`). `theme.css` imports the modules in
  cascade order: put a rule in the module of the feature it styles, and never reorder the imports.
- The in-app docs are Markdown under `frontend/src/content/docs/` and render at `/docs`; the
  user-guide text is pinned by `UserGuideContent.test.tsx`.
- A new section of the signed record is also parsed in
  `frontend/src/pages/families/signedReportRecord.ts` and rendered in `SignedFamilyReport.tsx`;
  otherwise the signed view silently leaves it out. The steps are in
  `docs/clinical-traceability.md`, "Adding a section to the signed record".

## Security & Testing

- Follow `docs/security-posture.md`: project-scoped RBAC, append-only audit, encryption and
  TLS, rate limiting.
- The secret scan (gitleaks) reads every commit of every pushed branch, so a key- or
  token-shaped literal in a test fails it even after a later commit removes it. Build such
  values at runtime (`base64.b64encode(bytes(range(32))).decode()`).
- Before you finish, run the checks in `CONTRIBUTING.md`, section "Before you open a PR". In
  short: `ruff check .`, `mypy` and `python -m pytest` from the repository root; in
  `frontend/`, `npm run tsc`, `npm run lint` and `npx vitest run` — run both tsc and vitest,
  because each misses what the other catches.
- `python -m pytest` alone runs the unit suite: without `RUN_INTEGRATION=1` and a running
  Postgres and ClickHouse the integration and end-to-end tests are skipped. Run them as
  `docs/testing.md` ("How to run") shows, integration and end-to-end as separate runs (in one
  process they hit event-loop errors), and say which suites you ran. Run the backend and
  frontend suites one after the other, not at the same time: timing-sensitive tests fail under
  the combined load.
- Branch from `main` as `type/short-description` and write Conventional Commits
  (`CONTRIBUTING.md`, "Branches and commits"). The PR description states the change's level
  (patch, minor or major) and why ("Change classification").
- A new or deleted test file needs its row in `docs/testing.md`
  (`./scripts/check-test-catalogue.sh`). After editing a handleiding chapter, run
  `python docs/handleiding/build_site.py` and commit the rebuilt HTML.
- Until the first release candidate a PR adds no `CHANGELOG.md` entry, no TF-10 §8 line and
  no TF-18 §8 row (TF-18 §3a): the changelog holds one summary of the pre-release work, and
  TF-10 §8 lists the clinical behaviour to validate by area — update it only when a PR adds
  clinical behaviour no area covers. From the release candidate on, every PR adds a
  `CHANGELOG.md` entry with its TF-18 level and a TF-18 §8 row.
- The end-to-end harness (API contract, import, sign-out, Playwright journeys) backs the IVDR
  verification records — see `docs/regulatory/TF-09c` and `TF-09d`.
