# Scripts

The `scripts/` directory now contains helper utilities and one supported demo-data loader for the current Postgres/ClickHouse stack.

Available helpers:

- [generate_demo_quartet_dataset.py](generate_demo_quartet_dataset.py)
  - generates a local demo dataset bundle
- [load_demo_quartet.py](load_demo_quartet.py)
  - loads the bundled demo family into Postgres and ClickHouse using the current backend services
- [gtf_to_ccds_gene_bed.py](gtf_to_ccds_gene_bed.py)
  - prepares transcript/gene reference files for the assembly reference upload flow
- [import_dgv.py](import_dgv.py)
  - server-side streaming bulk-loader for the large DGV (Database of Genomic Variants) reference file (~360 MB / ~2M rows) into the `dgv_variants` table, inserting in bounded batches so memory stays flat regardless of file size. Prefer this over the `POST /assemblies/{id}/reference-upload/dgv` endpoint for the full DGV file (that endpoint reads the whole upload into memory). Run inside the backend container, e.g.:
    - `PYTHONPATH=/app python /app/scripts/import_dgv.py --assembly GRCh38 --file /data/ref-data/<dgv-file>.txt`

- [generate-api-types.py](generate-api-types.py)
  - writes the frontend's API types (`frontend/src/lib/apiSchema.generated.ts`) from the backend's OpenAPI schema. Run it after changing a backend Pydantic model, in the backend's environment (installed from `backend/requirements-dev.txt`): the schema depends on the FastAPI and Pydantic versions, and the script warns when they differ from the pinned ones. CI runs it with `--check` and fails when the committed file is out of date (#528):
    - `python scripts/generate-api-types.py`

For normal application data loading, use the API flows documented in [docs/data-import.md](../docs/data-import.md).

Run the demo loader with the backend virtualenv so the FastAPI/SQLAlchemy dependencies are available:

```bash
backend/.venv/bin/python scripts/load_demo_quartet.py --overwrite
```
