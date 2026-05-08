# Development

## Services

Local Docker services:

- `postgres`
- `clickhouse`
- `backend`
- `frontend`

Start everything:

```bash
docker compose up --build -d
```

Stop everything:

```bash
docker compose down
```

Full reset:

```bash
docker compose down -v
docker compose up --build -d
```

## Backend

```bash
cd backend
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Run tests:

```bash
backend/.venv/bin/python -m pytest
```

## Frontend

```bash
cd frontend
npm install
npm run dev
```

Run checks:

```bash
cd frontend
npm run tsc
npm run lint
npm test
npm run build
```

The frontend is the only Node package in the repository. Run npm commands from `frontend/`.

## Key Environment Variables

| Variable | Purpose |
| --- | --- |
| `POSTGRES_HOST` | Postgres hostname |
| `POSTGRES_PORT` | Postgres port |
| `POSTGRES_DB` | Postgres database |
| `POSTGRES_USER` | Postgres user |
| `POSTGRES_PASSWORD` | Postgres password |
| `CLICKHOUSE_HOST` | ClickHouse hostname |
| `CLICKHOUSE_PORT` | ClickHouse native port |
| `CLICKHOUSE_HTTP_PORT` | ClickHouse HTTP port |
| `CLICKHOUSE_DATABASE` | ClickHouse database |
| `CLICKHOUSE_USER` | ClickHouse user |
| `CLICKHOUSE_PASSWORD` | ClickHouse password |
| `SECRET_KEY` | JWT signing secret |
| `CORS_ORIGINS` | Allowed frontend origins |
| `CORS_ORIGIN_REGEX` | Optional regex for local/dev frontend origins |
| `READS_PATH` | BAM/CRAM directory |
| `REFERENCE_FASTA_PATH` | Reference FASTA for sequence/CRAM lookups |
| `REFERENCE_ALIAS_PATH` | Optional chromosome alias map |
| `REFERENCE_CYTOBAND_PATH` | Optional cytoband fallback file |
| `GENE_REFERENCE_DBNSFP_GENE_PATH` | Optional local dbNSFP gene file for gene reference sync |
| `VITE_API_BASE_URL` | Frontend API base URL when not using `http://localhost:8000` |

## Troubleshooting

Check containers:

```bash
docker compose ps
docker compose logs backend --tail=100
docker compose logs postgres --tail=100
docker compose logs clickhouse --tail=100
```

Check backend env inside the container:

```bash
docker exec coga-backend-1 printenv | egrep '^(POSTGRES_|CLICKHOUSE_|SECRET_KEY|READS_PATH|REFERENCE_)'
```

## Storage Notes

- Metadata issues usually come from Postgres schema or bad UUID references.
- Variant ingestion/listing issues usually come from ClickHouse schema or assembly table creation.
- The Administration data-management page now includes a ClickHouse variant operations section for inspecting per-assembly table status and running ensure/optimize actions.
- The same maintenance API is available through:
  - `GET /admin/clickhouse/variants`
  - `POST /admin/clickhouse/variants/{assembly_name}/ensure`
  - `POST /admin/clickhouse/variants/{assembly_name}/optimize`
- There is no MongoDB service or compatibility path in the development stack.
