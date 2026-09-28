# AGENTS

## Repository Overview

- CoGA is a family-based genome browser with a FastAPI backend, React/TypeScript frontend, Postgres metadata storage, and ClickHouse variant storage, orchestrated via Docker Compose.
- It is operated as an **in-house IVD under IVDR Article 5(5)** at CMGG (ISO 15189). The device boundary is _annotated VCF → signed clinical report_. The technical file lives in `docs/regulatory/`; weigh changes for their clinical-safety / traceability / security consequences, not engineering merit alone.
- The codebase is mature, but the **data is synthetic** — there is no production PHI and no legacy code to preserve. Build cleanly; ignore old/legacy datasets.
- **All actions in the interface are auditable** — queries and significant UI events flow through the durable audit/telemetry pipeline. The clinical audit trail and report sign-out records are append-only and hash-chained; do not weaken these guarantees.

## Environment & Setup

- Requires Docker & Docker Compose, Python 3.12 (what CI and the backend image run), and Node.js 22 (at least 22.22.0, the `engines` floor in `frontend/package.json`).
- Copy `.env.example` (which lists every setting with its default) to `.env` and populate the secrets. Outside development the backend **refuses to start on placeholder or weak secrets**: `SECRET_KEY` shorter than 32 characters, a missing or malformed `INTEGRITY_ANCHOR_SIGNING_KEY`, or a placeholder Postgres, ClickHouse or admin password.
- Start services with `docker compose up --build -d` and access:
  - Backend API: `http://localhost:8000/docs` (served only with `APP_ENV=development`)
  - Frontend UI: `http://localhost:3000`
- Load reference data and assay imports through the FastAPI upload endpoints described in `docs/data-import.md`. See `docs/development.md` for setup, reset, and troubleshooting.

## Local Development

- Backend: `cd backend && pip install -r requirements.txt && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`.
- Frontend: `cd frontend && npm install && npm run dev`.

## Backend Guidelines

- Connection/runtime utilities live under `backend/app/core/` (`postgres.py`, `clickhouse.py`, `config.py`, `azure.py`, `coga_logging.py`, `object_storage.py`, `http_resilience.py`, `sql.py`).
- The backend uses SQLAlchemy async sessions for Postgres metadata and direct ClickHouse clients for variant storage. **All queries are parameterized**; `ORDER BY` is allowlisted and `LIMIT`/`OFFSET` int-coerced — keep it that way (no string interpolation into SQL/ClickHouse).
- Routers under `backend/app/routers/` (listed in `routers/__init__.py`, all mounted under `/api`) cover auth; families and their per-family routers (`families_small_variants`, `families_structural_variants`, `families_tracks`, `families_nipt`, `families_reports`, `family_qc_reports`); structural variants; CNVs; the cross-project `variant_explorer`; genes, HPO and panels; pedigree (`ped`); BED and signal tracks; blacklist, DGV and segmental duplications; repeat expansions; CRAM; family-package imports (`family_imports`); projects, assemblies, species and chromosomes; reference data; admin; lookups; `product`; `ui_events`; and `health`. New endpoints follow this pattern, enforce **project-scoped RBAC**, and use the appropriate storage dependency.
- Clinical/business logic lives in `backend/app/services/` — e.g. ACMG scoring (`acmg_points.py`, `cnv_acmg_points.py`), prioritization/filters (`clickhouse_family_variants.py`, `family_variant_filters.py`), NIPT (`nipt_*`), haplotype/lineage (`haplotype_lineage_service.py`, `phased_marker_service.py`), Monarch/HPO scoring (`monarch_*`, `hpo_service.py`), and the traceability stack (`annotation_manifest_service.py`, `classification_drift_service.py`, `clinical_audit_service.py`, `hash_chain.py`, `integrity_anchor_service.py`, report sign-out, `clickhouse_integrity_monitor.py`).
- Mount routers in `main.py`, configure CORS, and include security utilities (password hashing, JWT verification, `get_current_user`).

## Database Schema

- Postgres schema is applied from the five idempotent baseline files in `backend/db/schema/postgres/` (`01_access` … `05_grants`), re-run on every boot. The ClickHouse SQL file in `backend/db/schema/clickhouse/` only creates the database; the per-assembly tables are created at runtime in Python (`clickhouse_variant_storage.py`, `clickhouse_interval_tracks.py`). `docs/database.md` is the table-by-table reference — treat it as the source of truth.
- **Access/projects:** `users`, `projects`, `project_users`, login-attempt throttling, and the runtime app-role privilege grants.
- **Reference data:** `species`, `assemblies`, `chromosomes`, `genes`/`gene_info`, `blacklist`, `clinical_cnvs`, `dgv_variants`, `segmental_duplications`, HPO and gene-panel tables.
- **Assay/application data:** `samples`, `families`, `family_members`, `family_projects`, `sample_projects`, `small_variant_reviews`, `repeat_expansions`, Paraphase results, NIPT artifacts, and the variant-ranking cache in Postgres. In ClickHouse, one set of tables per assembly: `<assembly>/SNV_INDEL/…` (variant details, annotations and indexes, per-sample `entries`, family summaries, the family data version), `<assembly>/SV/…` and `<assembly>/INTERVAL/entries`.
- **Traceability/clinical-grade:** the annotation manifest, per-classification evidence snapshots, the append-only **hash-chained** clinical audit + report sign-out tables, integrity anchors, and `ui_events`. These carry IVDR data-integrity guarantees — changes here need care.
- ClickHouse variant rows are joined back to Postgres metadata at request time.

## Frontend & Integration

- React/TypeScript + Tailwind + Vite: login flow, dashboard, family workspace, gene/global explorers, and interactive canvas/SVG/D3 visualizations under `frontend/src/components/visualizations/` (coverage/APCAD, small-variant/SV/CNV/gene/segdup/DGV/blacklist tracks, ideograms, Circos, pedigree, and the PGT haplotype/lineage tracks).
- Configure Axios with the JWT, extend routing as needed, and reuse shared styles from `frontend/src/styles/theme.css` for buttons, links, tables, and layout to keep a consistent appearance.
- In-app reference docs are authored under `frontend/src/content/docs/` and render at `/docs`.

## Security & Testing

- Follow the posture in `docs/security-posture.md`: project-scoped RBAC, append-only audit, encryption/TLS, rate limiting, and the CI gates (dependency-audit, secret-scan, SAST, SBOM).
- **Backend:** run `ruff check .`, `mypy` (the clinical-critical modules in `mypy.ini`) and `python -m pytest` (from the repository root or `backend/`; both collect `backend/tests/`, the only test root). **Frontend:** run `npx vitest run` from `frontend/`. The catalogue gate enforces every test file is listed in `docs/testing.md` — keep that catalogue current. `tsc`/`eslint`/`build` do **not** catch component-test regressions, so run vitest for any frontend change.
- An end-to-end harness (API-contract, import, sign-out, and Playwright browser journeys) backs the IVDR verification records — see `docs/regulatory/TF-09c`/`TF-09d`.
- Run the relevant suites before committing.
