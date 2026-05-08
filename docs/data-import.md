# Data Import

Data is loaded through the current FastAPI upload endpoints. The old Mongo import scripts have been removed.

## Reference Data

Admin users can now bootstrap UCSC-backed organisms and assemblies from the Reference Catalog in
the web UI:

- pick an organism
- pick an assembly
- trigger the automatic download of cytobands and genes into the local catalog

The underlying API path for the automatic flow is:

- `POST /assemblies/reference-import`

Manual uploads remain available per assembly through:

- `POST /assemblies/{assembly_id}/reference-upload/cytobands`
- `POST /assemblies/{assembly_id}/reference-upload/genes`
- `POST /assemblies/{assembly_id}/reference-upload/blacklist`
- `POST /assemblies/{assembly_id}/reference-upload/clinical_cnvs`

Expected content:

- `cytobands`: UCSC-style cytoband TSV
- `genes`: transcript/gene BED-style export matching the backend loader contract
- `blacklist`: BED-like interval list
- `clinical_cnvs`: BED-like interval list with label/details columns

## Gene Reference Sync

Admin users can refresh cached human gene reference data from the Administration section.

The sync now combines:

- per-gene HGNC, Ensembl, NCBI Gene, and ClinGen lookups
- bulk ClinGen gene-validity and dosage downloads
- the GenCC submissions export
- ClinVar `gene_condition_source_id` for gene-disease relationships
- optional local `dbNSFP_gene` raw files through `GENE_REFERENCE_DBNSFP_GENE_PATH`

The public ClinGen, GenCC, and ClinVar sources work without extra setup. `dbNSFP` is treated as an
optional local raw download because it is a large external dataset; when configured, the gene sync
adds extra OMIM-style disease context and constraint metrics from that file.

## Pedigrees

Create family/sample metadata through:

- `POST /ped/manual` for regular authenticated users
- `POST /ped/upload` for admins only
- `POST /family-imports` for admin-only server-side folder packages

Only admins can overwrite existing families/samples or upload assay data. Regular users can create
families and samples, but cannot upload data files or remove/replace existing family records.

The PED/manual intake populates Postgres metadata tables:

- `families`
- `samples`
- `family_members`
- `family_projects`
- `sample_projects`

## Family Folder Packages

Admins can start a backend-driven package import from the Family Intake page or through the API.
The package path is resolved on the backend host. Use dry-run mode first to validate the package
without writing family or dataset records.

API:

- `POST /family-imports/manifest/discover` to parse the PED, scan expected dataset paths, and return a generated manifest preview
- `POST /family-imports/manifest/write` to write `manifest.yaml` into the package folder
- `POST /family-imports` with JSON body `{"folder_path": "/data/families/FAM001", "project_id": "...", "dry_run": true, "conflict_mode": "cancel"}`
- `GET /family-imports` to list recent import jobs
- `GET /family-imports/{job_id}` to poll job status, logs, validation errors, warnings, and dataset summaries
- `POST /family-imports/validate` for immediate validation without creating a job

For production deployments, set `FAMILY_IMPORT_ROOTS` to a comma-separated allowlist of directories
that may be scanned or written by package import endpoints, for example:

```env
FAMILY_IMPORT_ROOTS=/data/families,/mnt/imports
```

Package imports run through background workers. By default one import job runs at a time per backend
process. Set `FAMILY_IMPORT_WORKER_COUNT=2` or higher to process separate queued family imports in
parallel. Long WisecondorX imports update job heartbeat/progress during batch inserts, so a running
job should keep its "last update" timestamp moving.

`POST /family-imports` also accepts `family_id` when the package should be checked against an
existing family selected in the UI. The `conflict_mode` values are:

- `cancel`: fail the import if the family or sample IDs already exist
- `update`: attach to the existing family and skip dataset tables that already contain data
- `overwrite`: attach to the existing family and replace imported dataset rows for enabled datasets

The Family Intake UI exposes this flow for admins:

1. Enter the backend-visible family folder path.
2. Choose whether the package creates a new family or imports against an existing family.
3. Choose the existing-data policy: cancel, update, or overwrite.
4. Enter or auto-detect the PED path.
5. Choose the naming scheme, currently `standard_v1`.
6. Add HPO terms and optional notes.
7. Discover the package to generate a manifest preview and availability table.
8. Edit the YAML if needed, write `manifest.yaml`, then run dry-run validation or start import.
9. Re-open the Family Intake page later and use "Recent family imports" to inspect job status.

CLI dry run:

```bash
backend/.venv/bin/python scripts/validate_family_package.py /data/families/FAM001
```

Expected layout:

```text
FAM001/
  manifest.yaml
  family.ped
  snv/
    family.annotated.vcf.gz
    family.annotated.vcf.gz.tbi
  needlr/
    family.sv.annotated.vcf.gz
    family.sv.annotated.vcf.gz.tbi
  repeats/
    family.trgt.vcf.gz
    family.trgt.vcf.gz.tbi
    FAM001_tr.vcf
  wisecondorx/
    SAMPLE1/
      bins.bed
      segments.bed
  apcad/
    SAMPLE1.apcad.bed
  haplotypes/
    SAMPLE1.glimpse2.bcf
    SAMPLE1.glimpse2.bcf.csi
  paraphase/
    SAMPLE1.paraphase.json
```

The built-in `standard_v1` naming scheme checks these paths:

- SNV: `snv/{family_id}.annotated.vcf.gz` plus `.tbi`, `snv/{family_id}/{family_id}_phased.vcf.gz` plus `.tbi`/`.csi`, with `snv/family.annotated.vcf.gz` fallback. Optional VEP TSV annotation files are detected at `snv/annotation/{family_id}_annot.tsv.gz`, `snv/annotation/{family_id}.annot.tsv.gz`, `snv/{family_id}_annot.tsv.gz`, or `snv/{family_id}.annot.tsv.gz`.
- SV Needlr: `needlr/{family_id}.sv.annotated.vcf.gz` plus `.tbi`, with `needlr/family.sv.annotated.vcf.gz` and `sv_needlr/...` fallbacks
- TRGT family VCF: `repeats/{family_id}.trgt.vcf.gz` plus `.tbi`/`.csi`, `repeats/{family_id}_tr.vcf`, or `repeats/family.trgt.vcf.gz`/`.vcf` fallbacks. Plain uncompressed `.vcf` files do not require an index.
- WisecondorX: `wisecondorx/{sample_id}/bins.bed` and `segments.bed`, with `sample_bins.bed`, `{sample_id}_bins.bed`, `sample_segments.bed`, and `{sample_id}_segments.bed` fallbacks
- APCAD: `apcad/{sample_id}.apcad.bed`, with `.bed` and `.apcad.tsv` fallbacks
- Haplotypes: `haplotypes/{sample_id}.glimpse2.bcf` plus `.csi`
- Paraphase: `paraphase/{sample_id}.paraphase.json`, with nested `{sample_id}/{sample_id}.paraphase.json` and `{sample_id}.json` fallbacks

TRGT locus reference data is seeded from `TRGT_STRCHIVE_LOCI_PATH`, defaulting to
`/data/ref-data/STRchive-loci.json`. For local development, the bundled
`data/refdata/STRchive-loci.json` file is used as a fallback. STRchive thresholds,
gene/disease labels, HPO terms, and known interruption motifs are stored in the
`repeat_loci` catalog and used by TRGT imports.

Manifest example:

```yaml
schema_version: 1
family_id: FAM001
ped: family.ped

metadata:
  hpo:
    - HP:0001250
    - HP:0004322
  notes: Example family import

samples:
  SAMPLE1:
    external_id: lab-SAMPLE1

datasets:
  snv:
    enabled: true
    family_vcf: snv/family.annotated.vcf.gz
    index: snv/family.annotated.vcf.gz.tbi
    annotation_tsv: snv/annotation/FAM001_annot.tsv.gz

  sv_needlr:
    enabled: true
    family_vcf: needlr/family.sv.annotated.vcf.gz
    index: needlr/family.sv.annotated.vcf.gz.tbi

  repeats_trgt:
    enabled: true
    family_vcf: repeats/FAM001_tr.vcf

  wisecondorx:
    enabled: true
    per_sample:
      SAMPLE1:
        bins: wisecondorx/SAMPLE1/bins.bed
        segments: wisecondorx/SAMPLE1/segments.bed

  apcad:
    enabled: true
    per_sample:
      SAMPLE1:
        bed: apcad/SAMPLE1.apcad.bed

  haplotypes:
    enabled: true
    per_sample:
      SAMPLE1:
        file: haplotypes/SAMPLE1.glimpse2.bcf
        index: haplotypes/SAMPLE1.glimpse2.bcf.csi

  paraphase:
    enabled: true
    per_sample:
      SAMPLE1:
        json: paraphase/SAMPLE1.paraphase.json
```

Validation rules:

- the family folder and manifest must exist
- `schema_version` must be `1`
- `family_id` defaults to the folder name when omitted
- the PED file must exist, parse as six-column PED, contain a single family, and match `family_id`
- sample IDs must be unique; non-zero parent IDs must refer to samples in the PED
- manifest `samples` and per-sample datasets must reference PED sample IDs
- referenced files must exist
- VCF/BCF datasets must include an index path or have a sibling `.tbi`, `.csi`, or `.idx`; uncompressed `repeats_trgt` `.vcf` files are accepted without an index
- unsupported dataset keys are validation errors
- omitted supported datasets are warnings because they are optional

First-version import behavior is conservative. The importer always validates and registers package
provenance on the family/sample metadata. It deeply imports datasets where storage exists:
family SNV VCFs, WisecondorX `_bins.bed` as `coverage`, WisecondorX `_segments.bed` as `segments`,
Needlr family SV VCFs as ClickHouse structural variants with source `needlr`, sample-scoped APCAD
BEDs, sample-scoped TRGT VCFs, family TRGT VCFs into the repeat expansion table, and
sample-scoped Paraphase JSON into `sample_paraphase_results`. Direct GLIMPSE2 BCF haplotypes are
still registered as provenance until a dedicated importer is added. Imported Paraphase results are
available from the family workspace Paraphase page and `GET /families/{family_id}/paraphase`.
The Paraphase page uses the curated medically relevant region catalog at
`/data/ref-data/paraphase-medical-regions.json`, with the bundled
`data/ref-data/paraphase-medical-regions.json` as a local-development fallback. This catalog controls
which regions are visible by default, the clinical copy-number fields emphasized on cards, and
OMIM disorder links.

## Variant Uploads

The upload endpoints in this section require admin credentials. Non-admin users should use the
family intake form only for family/sample metadata.

Small variants:

- `POST /families/{family_id}/small-variants/upload`
- Stored in ClickHouse

Structural variants:

- `POST /structural-variants/upload/{sample_id}`
- Stored in ClickHouse

Repeat expansions:

- `POST /repeat-expansions/upload/{sample_id}`
- Stored in Postgres

Interval tracks:

- `POST /bed/upload/{sample_id}/{bed_type}`
- `bed_type` includes coverage, APCAD, segments, and haplotypes
- High-volume rows are stored in ClickHouse `{assembly}/INTERVAL/entries`
- Postgres `sample_interval_track_sources` stores per sample/file source metadata and row counts

## Operational Order

Recommended order for a fresh assembly/family load:

1. Create species, assembly, and project metadata.
2. Import or upload reference datasets for the assembly.
3. Upload PED or create the family manually.
4. Upload small variants and structural variants.
5. Upload repeat expansions and interval tracks.

## Helper Scripts

The remaining `scripts/` directory mainly contains helper utilities. The supported direct loader is the demo importer:

- [load_demo_quartet.py](../scripts/load_demo_quartet.py)
  - bootstraps the bundled synthetic family into the current Postgres/ClickHouse schema
- [generate_demo_quartet_dataset.py](../scripts/generate_demo_quartet_dataset.py)
  - regenerates the source demo bundle
- [gtf_to_ccds_gene_bed.py](../scripts/gtf_to_ccds_gene_bed.py)
  - prepares transcript/gene reference files for the assembly upload flow

Example:

```bash
backend/.venv/bin/python scripts/load_demo_quartet.py --overwrite
```

That loader imports backend services directly, so it should be run from the backend virtualenv after installing `backend/requirements.txt`.

By default that loads:

- the demo family metadata
- one project bound to `Homo sapiens` / `GRCh38`
- `glimpse2` small variants, which also populate haplotype blocks
- `manual` structural variants
- coverage, segments, and APCAD tracks
- TRGT repeat expansions
