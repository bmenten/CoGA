# Development

## Node version

Use **Node 22** — the same major CI pins (`node-version: "22"` in `ci.yml` and
`security.yml`, `node:22-alpine` in `frontend/Dockerfile`). With `nvm`:

```bash
nvm use          # reads .nvmrc
nvm install 22   # if you don't have a 22.x yet
```

The hard floor is **22.22.0**, declared as `engines.node` in `frontend/package.json`:
`react-router` 8 requires it, and `npm` warns below it. Note that `.nvmrc` alone will not
catch this — it pins the major, so `nvm use` happily selects an older 22.x (e.g. 22.17.0)
if that is the newest 22 you have installed. If npm warns about the engine, run
`nvm install 22` to pick up the current 22.x.

## Services

Local Docker services:

- `postgres`
- `clickhouse`
- `backend`
- `frontend`

Start the production-style local stack:

```bash
docker compose up --build -d
```

Start the development stack with backend reload and the Vite dev server:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build -d
```

Stop everything:

```bash
docker compose down
```

Full reset:

```bash
docker compose down -v
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build -d
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
| `CLICKHOUSE_HTTP_PORT` | ClickHouse HTTP port used by the backend |
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
| `GENE_REFERENCE_DBNSFP_GENE_PATH` | Local dbNSFP gene file for gene reference sync; defaults to `/data/ref-data/dbNSFP5.4_gene.gz` and online sources are used as fallback |
| `GENE_REFERENCE_HGNC_COMPLETE_SET_URL` | HGNC complete set; defines which human genes the reference sync caches and resolves renamed symbols onto their current name |
| `REFERENCE_GENCODE_GTF_URL` | GENCODE GTF supplying human GRCh38 gene loci, biotypes, Ensembl/HGNC ids and MANE tags; pinned by release, falls back to the UCSC track when unset or unreachable |
| `REFERENCE_GENCODE_REFSEQ_METADATA_URL` | GENCODE transcript → RefSeq accession map, so lookups naming an `NM_`/`NR_` accession keep resolving |
| `REFERENCE_BOOTSTRAP_T2T` | Import T2T-CHM13v2.0 as a second human assembly on startup; off by default, since a second assembly roughly doubles the reference footprint |
| `REFERENCE_T2T_GTF_URL` | T2T gene loci (UCSC `hs1.ncbiRefSeq.gtf.gz`); RefSeq-derived, so coordinates but no biotypes, Ensembl ids or MANE tags |
| `GENE_REFERENCE_BOOTSTRAP_ON_STARTUP` | Queue the first dbNSFP-backed human gene reference sync on startup when GRCh38 genes exist and `gene_info` is still empty; defaults to `true` |
| `VITE_API_BASE_URL` | Frontend API base URL; defaults to same-origin `/api`, proxied to the backend |

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
