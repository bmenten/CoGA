# Development

How to run CoGA on your own machine, reset it, and find out what went wrong. The data you
work with locally is synthetic. The checks to run before a pull request are in
[CONTRIBUTING.md](../CONTRIBUTING.md#before-you-open-a-pr); the architecture is in
[application-scheme.md](application-scheme.md).

## What you need

- Docker with Docker Compose.
- Python 3.12, the version CI and the backend image use.
- Node.js 22, at least the version in `engines` in `frontend/package.json`. `.nvmrc` pins
  only the major version, so `nvm use` can pick an older 22.x that you have installed; if npm
  warns about the engine, run `nvm install 22`.

## First-time setup

```bash
cp .env.example .env
```

`.env.example` lists every setting with its default. For local work, set
`APP_ENV=development` in `.env`. The backend then accepts the placeholder passwords and keys,
and serves the interactive API docs at <http://localhost:8000/docs>. With any other value it
refuses to start until `SECRET_KEY`, `INTEGRITY_ANCHOR_SIGNING_KEY`, `POSTGRES_PASSWORD`,
`CLICKHOUSE_PASSWORD` and `ADMIN_PASSWORD` hold real values; `.env.example` shows how to
generate the first two. The same check applies to anything that loads the backend settings
on your machine, such as the demo loader.

## Run everything in Docker

The development stack runs the backend with auto-reload and the frontend on the Vite dev
server:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build -d
```

- Web app: <http://localhost:3000>. Sign in with `ADMIN_EMAIL` and `ADMIN_PASSWORD` from
  `.env`.
- API: <http://localhost:8000/api/health>, with the API docs at <http://localhost:8000/docs>.
- Postgres on `localhost:5432`; ClickHouse on `localhost:8123` (HTTP) and `localhost:9000`
  (native). Only the web app is reachable from other machines.

The development stack mounts `backend/`, `frontend/`, `scripts/` and `data/` into the
containers, so code changes apply without a rebuild, and it sets `APP_ENV=development` for
the backend container. Without the second file, `docker compose up --build -d` runs the
production images instead: no reload, and the built frontend served by
`frontend/server.mjs`. That frontend takes the `VITE_…` settings in `.env` when it is built,
and its footer links **Report a problem** only when `VITE_PROBLEM_REPORT_URL` is set; the
Vite dev server links the GitHub issue form when it is not.

The first start downloads the GRCh38 cytobands (UCSC) and GENCODE gene annotations, so it
needs internet access. Everything the backend does at startup is listed in
[application-scheme.md](application-scheme.md#startup-and-background-work).

## Run the backend or frontend on your machine

Start only the databases in Docker:

```bash
docker compose up -d postgres clickhouse
```

Backend, from `backend/` (it reads the `.env` at the repository root, which points it at the
databases on `localhost`):

```bash
cd backend
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 8000
```

`requirements-dev.txt` is the runtime lock plus the test, lint and type-check tools.

Frontend, from `frontend/`:

```bash
cd frontend
npm ci
npm run dev
```

The Vite dev server listens on <http://localhost:5173> and forwards `/api` to the backend on
`localhost:8000` (set `VITE_DEV_API_PROXY_TARGET` to use another backend). Run npm commands
from `frontend/`; it is the only Node package in the repository.

## Load demo data

Two synthetic families come with the repository; [demo/README.md](../demo/README.md) says
what they contain and how to load them.

## Stop and reset

```bash
docker compose down      # stop; the data stays
docker compose down -v   # stop and delete the local Postgres and ClickHouse data
```

After `down -v`, the next start creates an empty database again and re-runs the reference
bootstrap.

### Upgrading the ClickHouse image

When the ClickHouse image changes, the next start upgrades the `clickhouse_data` volume in
place, and the older image may not read it afterwards. Stop the stack cleanly first
(`docker compose stop`; ClickHouse gets five minutes to finish its merges). The local data is
synthetic and can be imported again; to keep it anyway, copy the volume before upgrading.

## Troubleshooting

Check the containers and their logs:

```bash
docker compose ps
docker compose logs backend --tail=100
docker compose logs postgres --tail=100
docker compose logs clickhouse --tail=100
```

Check the backend's connection settings without printing any secret:

```bash
docker compose exec -T backend printenv | grep -E '^(APP_ENV|POSTGRES_(HOST|PORT|DB|USER)|CLICKHOUSE_(HOST|HTTP_PORT|DATABASE|USER))='
```

- **"Refusing to start … with missing or weak secrets"** — `APP_ENV` is not `development`
  and a secret still holds a placeholder; see [First-time setup](#first-time-setup).
- **The web app cannot reach the API** — the backend is still starting or keeps restarting;
  read its log.
- **Families, samples or users look wrong** — that data is in Postgres; look for schema
  errors near the start of the backend log.
- **Variants do not list or import** — this usually sits in an assembly's ClickHouse tables.
  An admin can check them under Administration → ClickHouse Tables & Operations
  (`/admin/operations/clickhouse`): table status, an integrity check, and buttons to create
  missing tables, rebuild the small-variant gene index and optimize the tables. The API
  offers the same: `GET /api/admin/clickhouse/variants` lists the assemblies, and under
  `/api/admin/clickhouse/variants/{assembly_name}/` are `ensure`, `optimize` and
  `rebuild-small-variant-gene-index` (POST) and `integrity` (GET).
