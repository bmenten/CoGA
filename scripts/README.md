# Scripts

Helper scripts, run from the repository root. Application data is loaded through the API
flows in [docs/data-import.md](../docs/data-import.md), not with these scripts. The backend
image ships two of them, the ones it runs: `clinical_cnv_knowledgebase.py` and `import_dgv.py`. The Python
scripts that import backend code need the backend's environment, installed from
`backend/requirements-dev.txt`.

| Script | What it does |
| --- | --- |
| **Checks (run in CI)** | |
| [check-test-catalogue.sh](check-test-catalogue.sh) | Fails unless `docs/testing.md` lists exactly the test files in the tree. |
| [check-handleiding-sync.sh](check-handleiding-sync.sh) | Rebuilds the handleiding HTML and fails if that changed it; needs the `markdown` package. |
| [check-coverage-floor.py](check-coverage-floor.py) | Fails when a clinical-critical backend module, or the backend as a whole, drops below its coverage floor. |
| [check-release-version.sh](check-release-version.sh) | Checks that `VERSION` is valid SemVer without the identifier `dev` or build metadata, that `frontend/package.json` carries the same version and, given a tag, that the tag is `v<VERSION>` ([RELEASING.md](../RELEASING.md)). |
| [check-release-on-main.sh](check-release-on-main.sh) | Checks that a release tag names a commit on `main`, with `main`'s whole history; `build.yml` runs it before it builds a release ([RELEASING.md](../RELEASING.md)). |
| [build-version.sh](build-version.sh) | Prints the version a build stamps into the backend image: `VERSION` for the release build of tag `v<VERSION>` (`--release <tag>`), otherwise `VERSION` with the pre-release identifier `dev` added (`0.1.0-dev`, `0.1.0-rc.1.dev`), so only a release reports a release's version. |
| [audit-frontend-prod.mjs](audit-frontend-prod.mjs) | Audits the frontend's production dependencies; its exceptions are in [frontend-audit-allowlist.json](frontend-audit-allowlist.json) and justified in [SECURITY-AUDIT-ALLOWLIST.md](../SECURITY-AUDIT-ALLOWLIST.md). |
| [generate-api-types.py](generate-api-types.py) | Writes the frontend's API types (`frontend/src/lib/apiSchema.generated.ts`) from the backend's OpenAPI schema; run it after changing a Pydantic model. `--check` fails when the file is stale. |
| **Dependencies and SBOM** | |
| [compile-requirements.sh](compile-requirements.sh) | Recompiles the hash-locked backend requirements from the `.in` files, in Docker with Python 3.12. CI's `lock` job runs it and fails when a committed lock differs from what it compiles. |
| [verify-requirements.sh](verify-requirements.sh) | Checks that a compiled lock installs, hashes and all, in a clean Python 3.12. |
| [generate-sbom.sh](generate-sbom.sh) | Writes the CycloneDX SBOMs to `sbom/`, in Docker, or with `--native` as CI runs it ([sbom/README.md](../sbom/README.md)). |
| **Reference data** | |
| [import_dgv.py](import_dgv.py) | Streams the full DGV file (about 2 million rows) into `dgv_variants` in batches. Run it in the backend container: `PYTHONPATH=/app python /app/scripts/import_dgv.py --assembly GRCh38 --file /data/ref-data/<dgv-file>.txt`. |
| [gtf_to_ccds_gene_bed.py](gtf_to_ccds_gene_bed.py) | Turns a GENCODE GTF into a BED with one row per gene: the exons and introns of its largest CCDS transcript, for the gene reference upload. |
| [clinical_cnv_knowledgebase.py](clinical_cnv_knowledgebase.py) | Builds the clinical CNV knowledgebase from ClinGen, ClinVar and the cytobands. The admin rebuild of the knowledgebase runs it. It stops, rather than build without them, when ClinGen's dosage curation or recurrent CNV regions cannot be loaded. It reads ClinGen's recurrent CNV file only if its SHA-256 is pinned for the assembly (`CLINGEN_RECURRENT_CNV_SHA256`; GRCh37 has no pin yet, so a GRCh37 build stops), and `--sources-out` writes the file it checked, which the admin rebuild records with the load. |
| **Demo and test data** | |
| [generate_demo_quartet_dataset.py](generate_demo_quartet_dataset.py) | Regenerates the synthetic quartet in `demo/quartet_family/`. |
| [load_demo_quartet.py](load_demo_quartet.py) | Loads that quartet into Postgres and ClickHouse ([demo/README.md](../demo/README.md)). |
| [generate_nipt_demo.py](generate_nipt_demo.py) | Regenerates the synthetic NIPT trio in `demo/nipt_family/`. |
| [generate_golden_trio.py](generate_golden_trio.py) | Regenerates the golden-trio fixture, with its expected results, for the end-to-end tests. |
| [seed_playwright_e2e.py](seed_playwright_e2e.py) | Imports the golden trio and creates the user the Playwright journeys sign in as. |
| [seed_style_diff_demo.py](seed_style_diff_demo.py) | Adds the NIPT demo and the demo quartet on top of that seed, for the frontend style diff (`frontend/scripts/stylediff`). |
| **Other tools** | |
| [validate_family_package.py](validate_family_package.py) | Validates a family-package folder without importing it, and prints the result as JSON. |
