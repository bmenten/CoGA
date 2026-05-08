# AGENTS

## Repository Overview
- CoGA is a family-based genome browser with a FastAPI backend, React frontend, Postgres metadata storage, and ClickHouse variant storage orchestrated via Docker Compose.
- The frontend already ships canvas/SVG/D3 visualizations for coverage with segments, APCAD, structural variant and variant tracks, ideograms, and pedigrees.

## Environment & Setup
- Requires Docker & Docker Compose, Python 3.10+, and Node.js 20+.
- Populate secrets such as `SECRET_KEY`, `POSTGRES_PASSWORD`, and any optional Azure settings in `.env`.
- Start services with `docker compose up --build -d` and access:
  - Backend API: `http://localhost:8000/docs`
  - Frontend UI: `http://localhost:3000`
- Load reference data and assay imports through the FastAPI upload endpoints described in `docs/data-import.md`.

## Local Development
- Backend: `cd backend && pip install -r requirements.txt && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`.
- Frontend: `cd frontend && npm install && npm run dev`.

## Backend Guidelines
- Postgres and ClickHouse connection utilities live under `backend/app/core/`.
- The backend uses SQLAlchemy async sessions for Postgres metadata and direct ClickHouse clients for variant storage.
- Routers exist for auth, families, variants, pedigree (`ped`), BED, repeat expansions, and reference data; new endpoints should follow this pattern and use the appropriate storage dependency.
- Mount routers in `main.py`, configure CORS, and include security utilities (password hashing, JWT verification, `get_current_user`).

## Database Schema
- Project data: `users`, `projects`, `project_users`.
- Reference data: `species` (unique `name`, `tax_id`), `assemblies` (unique `species_id` + `assembly_name` + `version`), `chromosomes` with band info, `genes`, `blacklist`, and `clinical_cnvs`.
- Application data: `samples`, `families`, `family_members`, `family_projects`, `sample_projects`, `small_variant_reviews`, `repeat_expansions`, `sample_interval_tracks`, and gene-panel tables in Postgres; `small_variants` and `structural_variants` live in ClickHouse.
- Relationships: `species` → `assemblies` → `chromosomes` / `genes` / reference intervals; `projects` bind access and assembly scope; `families` → `samples`; ClickHouse variant rows are joined back to Postgres metadata at request time.

## Frontend & Integration
- React/TypeScript frontend with Tailwind and Vite includes login flow, dashboard, and interactive charts (`CoverageSegmentsChart`, `ApcadChart`, `VariantTrack`, `SvTrack`, `Ideogram`, `Pedigree`) grouped under `frontend/src/components/visualizations/`. Create new components, configure Axios with JWT, and extend routing as needed.
- Test auth flow, file uploads via `FormData`, and the existing visualizations.
- Write appropriate unit tests.
- Buttons, links, tables and other layout should use the shared styles defined in `frontend/src/styles/theme.css` to maintain a consistent appearance across the application.

## Security & Testing
- Follow best practices: HTTPS behind a reverse proxy, secure secret management, rate limiting, and unit/integration tests (pytest & vitest).
- Run `pytest` for the backend and, when present, `npm test` in `frontend/` before committing.
