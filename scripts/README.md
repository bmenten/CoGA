# Scripts

The `scripts/` directory now contains helper utilities and one supported demo-data loader for the current Postgres/ClickHouse stack.

Available helpers:

- [generate_demo_quartet_dataset.py](generate_demo_quartet_dataset.py)
  - generates a local demo dataset bundle
- [load_demo_quartet.py](load_demo_quartet.py)
  - loads the bundled demo family into Postgres and ClickHouse using the current backend services
- [gtf_to_ccds_gene_bed.py](gtf_to_ccds_gene_bed.py)
  - prepares transcript/gene reference files for the assembly reference upload flow
- [plot_coverage_with_segments.py](plot_coverage_with_segments.py)
  - local analysis/visualization helper

For normal application data loading, use the API flows documented in [docs/data-import.md](../docs/data-import.md).

Run the demo loader with the backend virtualenv so the FastAPI/SQLAlchemy dependencies are available:

```bash
backend/.venv/bin/python scripts/load_demo_quartet.py --overwrite
```
