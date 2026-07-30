# Data Import

Data is loaded through the current FastAPI upload endpoints.

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
- `POST /assemblies/{assembly_id}/reference-upload/segmental_duplications`

Startup bootstrap:

- On backend startup, CoGA ensures Homo sapiens GRCh38 is present as the default human
  organism/assembly.
- When GRCh38 cytobands or genes are missing, CoGA imports the missing core reference tables from
  UCSC `hg38`. If UCSC is unavailable, startup continues after creating the species/assembly shell.
- CoGA can automatically seed `clinical_cnvs` and `segmental_duplications` for GRCh38 when those
  tables are empty.
- When `/data/ref-data/dbNSFP5.3_gene.gz` or `GENE_REFERENCE_DBNSFP_GENE_PATH` is present and
  `gene_info` is empty, startup queues the first dbNSFP-backed human gene reference sync.
- Defaults:
  - assembly: `GRCh38`
  - CNV file: `/data/ref-data/clinical_cnv_syndromes_hg38_combined.tsv`
  - SegDup/LCR file:
    `/data/ref-data/clinical_cnv_syndromes_hg38_bundle/ClinGen_recurrent_CNV_V2.1-hg38.bed`
  - dbNSFP gene file: `/data/ref-data/dbNSFP5.3_gene.gz`
- Controls:
  - `REFERENCE_BOOTSTRAP_ENABLED=true|false`
  - `REFERENCE_BOOTSTRAP_ASSEMBLY_NAME=GRCh38`
  - `REFERENCE_CLINICAL_CNVS_PATH=...`
  - `REFERENCE_SEGMENTAL_DUPLICATIONS_PATH=...`
  - `GENE_REFERENCE_BOOTSTRAP_ON_STARTUP=true|false`
  - `GENE_REFERENCE_DBNSFP_GENE_PATH=...`

Expected content:

- `cytobands`: UCSC-style cytoband TSV
- `genes`: transcript/gene BED-style export matching the backend loader contract
- `blacklist`: BED-like interval list
- `clinical_cnvs`: BED-like interval list with label/details columns; also accepts 9-column TSV bundles with `chrom/start/end/name/source/source_detail/...`
- `segmental_duplications`: BED-like interval list for segmental duplications/LCRs (ClinGen recurrent-CNV BED black bars are supported)

## Gene Reference Sync

Admin users can refresh cached human gene reference data from the Administration section.

The sync now combines:

- per-gene HGNC, Ensembl, NCBI Gene, and ClinGen lookups
- bulk ClinGen gene-validity and dosage downloads
- the GenCC submissions export
- ClinVar `gene_condition_source_id` for gene-disease relationships
- local `dbNSFP_gene` raw files through `GENE_REFERENCE_DBNSFP_GENE_PATH`

CoGA prefers the local dbNSFP gene file, defaulting to
`/data/ref-data/dbNSFP5.3_gene.gz` in Docker. During gene reference sync, public ClinGen, GenCC,
ClinVar, HGNC, Ensembl, and NCBI sources are used as fallback sources only when the local dbNSFP
file is unavailable or does not contain the requested gene. The dbNSFP file enriches cached gene
records with identifiers, names and aliases, OMIM/Orphanet/GenCC disease context, ClinGen dosage
fields, HPO/GO/pathway terms, model organism context, tissue expression, and constraint metrics.

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

PED phenotype codes remain coarse affected/unaffected labels. Detailed phenotype observations are
stored separately as HPO annotations so the pedigree structure can stay compatible with standard
PED tooling.

## HPO Phenotypes

Load the HPO ontology into the global HPO tables before importing per-individual annotations:

- `hpo_term`
- `hpo_synonym`
- `hpo_edge`
- `hpo_closure`

The backend HPO service accepts `hp.obo` or `hp.json`, stores terms/synonyms/edges, and computes
the ancestor closure used for descendant queries. The API exposes:

- `GET /hpo/search?q=seizure`
- `GET /hpo/{hpo_id}`
- `POST /hpo/import` for admins to load an ontology file visible to the backend host

Per-family HPO annotation APIs:

- `GET /families/{family_id}/hpo`
- `POST /families/{family_id}/members/{sample_id}/hpo`
- `PUT /families/{family_id}/hpo/{annotation_id}`
- `DELETE /families/{family_id}/hpo/{annotation_id}`
- `GET /families/{family_id}/hpo/query?hpo_id=HP:0001250&include_descendants=true`

Phenotype TSV files use one tab-delimited row per individual term:

```text
family_id	individual_id	hpo_id	label	status	onset	evidence	source	note
FAM001	PROBAND	HP:0001250	Seizure	present	childhood	clinical	imported	Observed in chart
FAM001	MOTHER	HP:0004322	Short stature	absent			imported	Specifically excluded
```

Assumptions:

- `status` is one of `present`, `absent`, or `unknown`.
- `individual_id` must match a PED/sample ID in the family.
- HPO IDs must already exist in the loaded ontology for database import; unknown terms are reported
  as row-level import errors and skipped.
- Exact and descendant family queries match `present` annotations only. `absent` annotations are
  stored for review and exclusion context but do not highlight an individual as matching a
  phenotype query.
- Inline manifest HPO annotations are accepted under `individuals.{sample_id}.hpo.present`,
  `.absent`, and `.unknown`.

## Family Folder Packages

Admins can start a backend-driven package import from the Package Import page (`/package-import`,
also reachable from Admin → data upload) or through the API. The package path is resolved on the
backend host. Use dry-run mode first to validate the package without writing family or dataset records.

API:

- `GET /family-imports/packages` to list family folders discovered under `FAMILY_IMPORT_ROOTS` (local dirs or an S3 bucket) — the Package Import UI uses this to populate the **Family folder** dropdown instead of a typed path; the PED is auto-discovered within each folder
- `POST /family-imports/manifest/discover` to parse the PED, scan expected dataset paths, and return a generated manifest preview
- `POST /family-imports/manifest/write` to write `manifest.yaml` into the package folder
- `POST /family-imports` with JSON body `{"folder_path": "/data/families/FAM001", "project_id": "...", "dry_run": true, "conflict_mode": "cancel"}`
- `GET /family-imports` to list recent import jobs
- `GET /family-imports/{job_id}` to poll job status, logs, validation errors, warnings, and dataset summaries
- `POST /family-imports/validate` for immediate validation without creating a job

`FAMILY_IMPORT_ROOTS` is a comma-separated allowlist of locations that package import may scan or
write. It defaults to the local `/data/families` (mounted into the backend container via the
`./data:/data` volume). Cloud deployments (Terraform, etc.) override it with an `s3://` bucket
prefix, and the scan + import endpoints then list and stage packages from the bucket:

```env
# local (default)
FAMILY_IMPORT_ROOTS=/data/families
# cloud / Terraform
FAMILY_IMPORT_ROOTS=s3://my-coga-bucket/families
```

Multiple roots (and a mix of local dirs and S3 prefixes) can be comma-separated. S3 roots require
the backend to run with S3 access (credentials/role).

Package imports run through background workers. By default one import job runs at a time per backend
process. Set `FAMILY_IMPORT_WORKER_COUNT=2` or higher to process separate queued family imports in
parallel. Long WisecondorX imports update job heartbeat/progress during batch inserts, so a running
job should keep its "last update" timestamp moving.

`POST /family-imports` also accepts `family_id` when the package should be checked against an
existing family selected in the UI. The `conflict_mode` values are:

- `cancel`: fail the import if the family or sample IDs already exist
- `update`: attach to the existing family and skip dataset tables that already contain data
- `overwrite`: attach to the existing family and replace imported dataset rows for enabled datasets

The Package Import UI exposes this flow for admins:

1. Enter the backend-visible family folder path.
2. Choose whether the package creates a new family or imports against an existing family.
3. Choose the existing-data policy: cancel, update, or overwrite.
4. Enter or auto-detect the PED path.
5. Choose the naming scheme, currently `standard_v1`.
6. Add optional phenotype TSV or inline HPO annotations, plus optional notes.
7. Discover the package to generate a manifest preview and availability table.
8. Edit the YAML if needed, write `manifest.yaml`, then run dry-run validation or start import.
9. Re-open the Package Import page later and use "Recent family imports" to inspect job status.

CLI dry run:

```bash
backend/.venv/bin/python scripts/validate_family_package.py /data/families/FAM001
```

Expected layout:

```text
FAM001/
  manifest.yaml
  family.ped
  phenotypes.tsv
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
  QDNAseq/
    EMBRYO1/
      bins.csv
      segments.csv
  apcad/
    SAMPLE1.apcad.bed
  APCAD/
    EMBRYO1.apcad.vcf
  PCF/
    EMBRYO1_pcf_mat_data.csv
    EMBRYO1_pcf_pat_data.csv
  GLIMPSE2/
    FAM001.vcf.gz
  haplotypes/
    SAMPLE1.glimpse2.bcf
    SAMPLE1.glimpse2.bcf.csi
  paraphase/
    SAMPLE1.paraphase.json
```

Long-read packages produced by the Nextflow pipeline (nf-core/lrsvar) use a per-sample
layout instead, which the same `standard_v1` scheme also detects:

```text
pacbio/
  manifest.yaml
  pacbio.ped
  bams/
    HG002.cram
    HG002.cram.crai
  snv/
    HG002/
      annotation/
        HG002_annot.vcf.gz          # VEP-annotated (CSQ in INFO)
        HG002_annot.vcf.gz.tbi
  sv/
    HG002/
      annotation/
        HG002_sv_phased.needLR.4.0.vcf.gz    # tool version in the filename
        HG002_sv_phased.needLR.4.0.vcf.gz.tbi
  cnv/
    HG002/
      HG002.Sample0.copynum.bedgraph
      annotation/
        HG002_annot.vcf.gz
        HG002_annot.vcf.gz.tbi
  mito/
    HG002/
      HG002.vcf.gz
      HG002_sv.vcf.gz
    annotation/
      HG002/
        HG002_snv_annot.txt         # mutserve TSV
        HG002_sv_annot.txt
  repeats/
    HG002/
      HG002_tr.vcf.gz
      HG002_tr.vcf.gz.csi
  paraphase/
    HG002/
      HG002.paraphase.json
  qc/
    nanoplot/
      HG002/
        HG002NanoPlot-report.html
        HG002NanoStats.txt
    depth/
      HG002/
        HG002.mosdepth.summary.txt
        HG002.regions.bed.gz
  pipeline_info/
    params_2026-07-28_11-18-09.json
    software_versions.yaml
```

Two properties of that layout need explaining:

- **Versioned filenames.** The annotation release is part of the SV filename
  (`…needLR.4.0.vcf.gz`), so discovery patterns may contain a `*` wildcard. Globs are
  resolved by the scanner only — the written `manifest.yaml` always holds the concrete
  path a glob matched, and validation and import continue to treat manifest values as
  literal paths. Matches are ordered with embedded numbers compared numerically
  (`needLR.10.0` sorts after `needLR.4.0`) and every match is re-checked for containment
  inside the package root.
- **Sample columns named after files, not samples.** The CNV caller writes `Sample0` and
  TRGT writes `<sample>_sort` into the VCF `#CHROM` line. A per-sample manifest entry
  binds a single-column VCF to the sample it was declared for; otherwise known tool
  suffixes are stripped and a `<sample_id>_…` prefix is matched. A column that resolves
  to nothing fails the import rather than being dropped silently. Declare
  `vcf_sample: <column>` on the entry to override the resolution explicitly.

**Single-sample families.** The pipeline emits *per-sample* annotated SNV callsets and
only an unannotated GLnexus joint VCF, so `snv/{sample_id}/annotation/…` is accepted as
the family callset only when the family has exactly one sample. A multi-sample long-read
package needs an annotated joint callset; without one its SNV dataset is reported as not
detected rather than one member's genotypes being imported as the family's.

The built-in `standard_v1` naming scheme checks these paths:

- SNV: `snv/{family_id}.annotated.vcf.gz` plus `.tbi`, `snv/{family_id}/{family_id}_phased.vcf.gz` plus `.tbi`/`.csi`, with `snv/family.annotated.vcf.gz` fallback. Optional VEP TSV annotation files are detected at `snv/annotation/{family_id}_annot.tsv.gz`, `snv/annotation/{family_id}.annot.tsv.gz`, `snv/{family_id}_annot.tsv.gz`, or `snv/{family_id}.annot.tsv.gz`.
- SV Needlr: `needlr/{family_id}.sv.annotated.vcf.gz` plus `.tbi`, with `needlr/family.sv.annotated.vcf.gz` and `sv_needlr/...` fallbacks
- TRGT family VCF: `repeats/{family_id}.trgt.vcf.gz` plus `.tbi`/`.csi`, `repeats/{family_id}_tr.vcf`, or `repeats/family.trgt.vcf.gz`/`.vcf` fallbacks. Plain uncompressed `.vcf` files do not require an index.
- WisecondorX: `wisecondorx/{sample_id}/bins.bed` and `segments.bed`, with `sample_bins.bed`, `{sample_id}_bins.bed`, `sample_segments.bed`, and `{sample_id}_segments.bed` fallbacks
- QDNAseq: `cnv/{sample_id}/{sample_id}_cnv_qdnaseq_bins.bed` and `_cnv_qdnaseq_segs.bed` (the nf-core/lrsvar layout — BED beside the HiFiCNV output, named after the caller), or the older CSV layout `QDNAseq/{sample_id}/bins.csv`, `{sample_id}.bins.csv`, `{sample_id}.csv`, or `{sample_id}_cnv_results.csv` with optional `segments.csv`/`{sample_id}.segments.csv`; lower-case `qdnaseq` is also detected. If a QDNAseq CSV contains both `copynumber` and `segmented`, the same file can be used for both bins and segments. The caller also writes `_cnv_qdnaseq_raw_bins.bed` (uncorrected read counts) and `_cnv_qdnaseq_calls.bed` (discrete calls); neither is ingested, because the interval tracks have no axis for them.
- APCAD: family VCFs at `APCAD/{family_id}.apcad.vcf[.gz]`, `APCAD/{family_id}_embryo_filtered_imp_parent.vcf.gz`, or per-sample `APCAD/{sample_id}.apcad.vcf`, with BED and `.apcad.tsv` fallbacks; lower-case `apcad` is also detected
- PCF APCAD segments: per-sample files at `PCF/{sample_id}_pcf_mat_data.csv` and `PCF/{sample_id}_pcf_pat_data.csv`; lower-case `pcf` and dotted fallback names `PCF/{sample_id}.pcf.mat_data.csv` / `PCF/{sample_id}.pcf.pat_data.csv` are also detected. The verified CSV header is `"sampleID","CHROM","arm","start.pos","end.pos","n.probes","mean"`.
- Haplotypes: family GLIMPSE2 VCFs at `GLIMPSE2/{family_id}.vcf[.gz]`, `GLIMPSE2/{family_id}_phased_final.vcf.gz`, or `GLIMPSE2/family.vcf[.gz]`; legacy per-sample `haplotypes/{sample_id}.glimpse2.bcf` plus `.csi` is still registered as provenance
- Paraphase: `paraphase/{sample_id}.paraphase.json`, with nested `{sample_id}/{sample_id}.paraphase.json`, `{sample_id}.json`, and `paraphase/{sample_id}*/{sample_id}.paraphase.json` (suffixed directory) fallbacks
- CNV (HiFiCNV): per-sample `cnv/{sample_id}/annotation/{sample_id}_annot.vcf.gz` plus `.tbi`, with optional `cnv/{sample_id}/{sample_id}*.copynum.bedgraph`, `{sample_id}*.depth.bw`, `{sample_id}*.maf.bw` and the VEP `_summary.html`
- Mitochondrial: per-sample `mito/{sample_id}/{sample_id}.vcf.gz` plus `.tbi`, the mutserve annotation TSV at `mito/annotation/{sample_id}/{sample_id}_snv_annot.txt` (note `annotation/` sits *above* the sample directory here), and optional `mito/{sample_id}/{sample_id}_sv.vcf.gz` + `mito/annotation/{sample_id}/{sample_id}_sv_annot.txt`
- Alignments: `bams/{sample_id}.cram` plus `.crai` (or `.bam`/`.bam.bai`), with `alignments/` and package-root fallbacks
- QC: `qc/multiqc/{sample_id}/multiqc_report.html` or `qc/nanoplot/{sample_id}/{sample_id}NanoPlot-report.html` (no separator before `NanoPlot`), plus `{sample_id}NanoStats.txt` and `qc/depth/{sample_id}/{sample_id}.mosdepth.summary.txt`, `.regions.bed.gz`, `.mosdepth.global.dist.txt`. Every role is optional; a sample with at least one artefact is detected.
- Pipeline info: `pipeline_info/software_versions.yaml`, `pipeline_info/params_*.json`, and optional `execution_trace_*.txt` / `execution_report_*.html`. Family-level, not per sample.

Where each dataset lands:

| Dataset | Storage | Source tag | Surfaced in |
| --- | --- | --- | --- |
| `cnv` | ClickHouse structural variants; three signal files as interval tracks (see below) | `hificnv` | SV workspace, CNV ACMG scoring, coverage / segments / APCAD tracks |
| `mito` | ClickHouse small variants (chrM) | `mito` | mtDNA analysis workspace |
| `qc` | `samples.metadata["sequencing_qc"]` | — | Family detail members table: a compact chip showing the QC verdict and mean depth, coloured amber/red when a configured cut-off is crossed, opening the pipeline QC report on click. Cut-offs are set per assay profile in **Admin → Sequencing QC Thresholds**; a metric with no cut-off is reported as not assessed, never as a pass. |
| `alignments` | `samples.metadata["alignment"]` | — | IGV / genome browser via `/cram/...` |
| `pipeline_info` | `family_annotation_manifest` (tool versions, `source='manifest'`) and `families.metadata["pipeline"]` (run parameters) | — | **Analysis pipeline** card on the family workspace; **Analysis pipeline settings** section on the clinical report; tool versions in the annotation-provenance footer and the report's Modules & versions line |

Each source tag scopes its own delete and re-import, so a family can hold NeedlR SVs,
HiFiCNV calls and chrM variants at once and re-importing one never removes another.

#### Coverage tracks are per caller, on one shared axis

A long-read package can carry three CNV callers for the same sample, each writing its
own `coverage` and `segments` rows under its own `source` tag:

| Source | Native output | Stored as | Reference HG002 rows |
| --- | --- | --- | --- |
| `hificnv` | read depth per 2 kb bin (0.001–1681.7x) | log2 ratio, normalised at import | 1,105,778 |
| `wisecondorx` | log2 ratio per 10 kb bin | as-is | 283,188 |
| `qdnaseq` | log2 ratio per 100 kb bin | as-is | 26,367 |

HiFiCNV's depth is divided by the sample's own **autosomal median** and stored as
log2 of that ratio, so all three tracks mean the same thing and a single-copy loss
sits at −1 in each. Without it a depth track and a ratio track cannot be read
against one another: "18x" only means something if you already know the sample's
baseline. The normaliser is recorded on the track source
(`metadata.autosomal_median_depth`, 19.96x for the reference package) so the stored
numbers can be traced back to the file.

Autosomes only — chrX and chrY sit at half depth in a male sample and would move the
normaliser by a sex-dependent amount. Values are floored at −10 (2⁻¹⁰ of baseline):
log2(0) is −inf, and the near-zero depths left by a smoothed assembly gap would
otherwise stretch the plotted range by an order of magnitude to show nothing.

The raw depth bigWig is not modified and is what IGV is given, where absolute depth
is the useful quantity.

All three agree on chromosome naming (unprefixed, so `1` not `chr1`), which is what
makes them alignable along x.

WisecondorX bins with a `nan` ratio (empty bins) are skipped rather than stored as
zero: 25,649 of 308,837 in the reference package. A stored zero would read as a
complete loss.

**Reading them.** `/bed/{sample}/coverage` takes a `source` parameter, and
`/families/{id}/track-availability` reports `coverage_sources` / `segments_sources`
per sample. The genome and chromosome views draw one track per available caller,
labelled with the caller when there is more than one. **Omitting `source` returns
every caller's rows merged** — for a windowed coverage read that means averaging
three independent measurements into one that is none of them, so the views always
pass it.

#### HiFiCNV signal files → interval tracks

HiFiCNV ships three per-sample signal files that measure different things, so each
lands on the interval track whose axis means the same thing:

| Manifest key | File | Track | Notes |
| --- | --- | --- | --- |
| `depth_bigwig` | `{sample_id}*.depth.bw` | `coverage` | Read depth per 2 kb bin. Zero-valued bins (the assembly gaps a whole-genome bigWig also covers) are dropped — they plot as a flat line on the axis. |
| `copy_number_bedgraph` | `{sample_id}*.copynum.bedgraph` | `segments` | Called integer copy number, drawn as step segments. |
| `maf_bigwig` | `{sample_id}*.maf.bw` | `apcad` | Minor allele fraction (0–0.5), BAF-like. Written with `origin = und`: bigWig has nowhere to record a parent of origin. |

Only primary chromosomes are read from a bigWig — an aligner's bigWig carries every
contig it saw (195 in the reference package, of which 170 are ALT/random/decoy
scaffolds that no view can plot).

The files themselves are also served to the genome browser. The import records where
each one sits under `samples.metadata["signal_tracks"][<source>]`, and
`/signal-tracks/{family}/manifest` turns that into IGV track configs, with the files
streamed by `/signal-tracks/{family}/{sample}/{source}/{kind}` (Range-capable, which
is what makes a 143 MB MAF bigWig usable).

The paths are recorded rather than re-derived at serve time: unlike a CRAM, which is
always `<sample>.cram`, HiFiCNV names these after its own run
(`HG002.Sample0.depth.bw`, `HG002.HG002.maf.bw`) and no fixed pattern predicts them.
Recorded paths are package-relative, resolved against the family package root and
containment-checked before anything is served.

IGV gets the **raw depth**, not the log2 ratio stored in ClickHouse — absolute depth
is the useful quantity when you are looking at reads. Read depth autoscales; MAF is
pinned to 0–0.5 so its band structure does not move as you pan.


Because the MAF track has no parent-of-origin calls, the APCAD reader treats its
`paternal`/`maternal` preference as a preference rather than a filter: a track with no
phased markers **at all** falls back to its unphased points instead of rendering blank.
The test is per track, not per window — on a genuinely phased track a stretch with no
informative markers stays empty, because that emptiness is the autozygosity signal.

> **Track ownership.** A `(track_type, source)` pair belongs wholly to the import that
> writes it: re-importing clears the pair before loading, rather than replacing only the
> incoming filename. Otherwise changing which file feeds a track leaves the previous
> file's rows behind to be averaged in — which is exactly what would have happened when
> `coverage`/`hificnv` moved from the copy-number bedGraph to the depth bigWig.

Optional `snv` dataset settings:

- `source_format` — pins the callset's source tag instead of letting it be auto-detected
  (`clair3` for a directly-called nuclear callset, `glimpse2` for imputed genotypes).
  Long-read manifests should set it explicitly: the detector reads the first data record,
  and a header mentioning phasing plus a phased first record would classify the primary
  callset as imputed and hide it from every diagnostic view.
- `exclude_filters` — FILTER values whose records are dropped at ingest. A whole-genome
  DeepVariant callset marks reference blocks `RefCall` and zero-depth sites `NoCall`;
  those are half the records and carry no variant to review. A record is dropped only when
  *all* of its FILTER values are excluded.

PED phenotype codes follow the GATK PED convention: `0` or `-9` means missing,
`1` means unaffected, and `2` means affected. CoGA stores phenotype as canonical
`clinical_status` on the family member (`unknown`, `unaffected`, or `affected`) and stores
carrier state separately as `carrier_status` (`unknown`, `not_carrier`, or `carrier`) with an
optional `carrier_type` (`obligate`, `proven`, `reported`, or `inferred`). Extra PED tokens
such as `role=embryo`, `role=relative`, `carrier=true`, `carrier_type=obligate`, or
`carrier_type=proven` are accepted.

Package manifests can provide family-member state and explicit childless couples under
`family`:

```yaml
schema_version: 2
family_id: FAM001
ped: family.ped

family:
  members:
    FATHER:
      clinical_status: unaffected
      carrier_status: carrier
      carrier_type: proven
    MOTHER:
      clinical_status: unaffected
      carrier_status: carrier
      carrier_type: obligate
  relationships:
    couples:
      - partners: [FATHER, MOTHER]
        context: reproductive
      - partners: [D7, D10]
        context: future_embryo_planning
    parent_child:
      - child: PROBAND
        parents: [FATHER, MOTHER]
```

Imported families can be modified by admins through `PUT /families/{family_id}/structure`.
The endpoint records a new family-structure version and regenerates the PED text from active
members and active relationships. Because family relationships are now authoritative, membership
or relationship changes are blocked when imported genomic data already exists unless the request
sets `clear_existing_genomic_data: true`. Confirmed edits clear the imported family datasets so
the family can be reloaded under the new structure. Removing a member is a soft metadata removal:
the member becomes inactive, and any old genomic rows are cleared only through the explicit data
clear confirmation.

```yaml
expected_structure_version: 3
change_reason: family_detail_page
clear_existing_genomic_data: true
add_members:
  - sample_id: FUTURE_EMBRYO_1
    sex: und
    role: embryo
    clinical_status: unknown
members:
  - sample_id: FATHER
    clinical_status: unaffected
    carrier_status: carrier
    carrier_type: proven
  - sample_id: MOTHER
    clinical_status: unaffected
    carrier_status: carrier
    carrier_type: obligate
remove_members: []
relationships:
  parent_child:
    - parent: FATHER
      child: PROBAND
      parent_role: father
    - parent: MOTHER
      child: PROBAND
      parent_role: mother
  couples:
    - partners: [FATHER, MOTHER]
      context: reproductive
```

Relationship edits are validated before saving: members must be active, parent-child edges
cannot form cycles, duplicate couple edges are rejected, and a child can have at most two
parents. Confirmed relationship changes clear small variants, structural variants, interval
tracks (`coverage`, `segments`, `apcad`, `apcad_pcf`, `haplotype`), repeat expansions, Paraphase rows, and
variant review rows for the family. Phenotype and carrier-label changes are applied immediately
to filters and pedigree displays; saved interpretations should still be reviewed before reuse.

Pedigree visualization layout assumes that parent-child relationships form a directed acyclic
graph. The renderer first assigns generations from this graph so every child is at least one row
below every recorded parent. Unrelated partners are then aligned to the same generation when that
does not violate ancestry, which keeps married-in spouses and embryo/proband partners on the
correct horizontal level. Within a generation, same-row couple edges are treated as fixed partner
blocks, ordered deterministically by parent/child barycenters, and spaced so offspring can be
centered below the corresponding parent unit where possible. The SVG exposes
`data-generation-count` and per-node `data-generation` attributes to make complex layouts
testable.

Current visualization limits follow the stored relationship model: classic PED rows encode only
father and mother IDs, and the structure editor allows at most two parents per child. Explicit
`couple` relationships can represent childless partnerships and consanguinity context, but
divorce, adoption, twins, donor gametes, deceased symbols, and proband arrows are not yet modeled.
Ancestor-descendant or otherwise cross-generation couples are drawn without forcing both people
onto the same row, because preserving chronological generations takes priority. For individuals
with more than two partners, the layout keeps the partner component deterministic and compact, but
not every partner can be immediately adjacent at the same time.

The PED upload form also accepts an ROI gene/region and inheritance model (`AD`, `AR`, `XLD`,
`XLR`, or `mitochondrial`). The PGT context can still be provided under `metadata.pgt`:

```yaml
metadata:
  pgt:
    inheritance_model: AR
    obligate_carriers: [FATHER]
    proven_carriers: [MOTHER]
```

Haplotype risk coloring is inferred from imported haplotype blocks and the family structure
metadata. CoGA treats `hap1` as the paternal inherited haplotype and `hap2` as the maternal
inherited haplotype for children and embryos; parental rows use their own two haplotypes on the
corresponding parent side. Disease colors are applied only when the inheritance model and
affected/carrier labels provide a deterministic shared block:

- `AD` / dominant: a single haplotype shared by affected individuals and obligate carriers is
  colored dark red. Ambiguous single-sample or multi-candidate cases remain neutral.
- `AR` / recessive: affected individuals define one paternal and one maternal risk haplotype;
  paternal risk is orange-red and maternal risk is pink-red. Carriers show only the inherited
  risk side when the shared block is informative.
- `XLR`: males are treated as hemizygous on chromosome X and display one X haplotype. Affected
  males define the disease-associated X haplotype; carrier females show one red X haplotype and
  affected females show two when both X risk blocks are inferable.

If the ROI is available, inference is anchored to the ROI-overlapping haplotype block; otherwise
the current viewed interval is used. Non-informative or unrelated haplotypes keep the neutral
parental colors.

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
roi: CFTR

metadata:
  hpo:
    - HP:0001250
    - HP:0004322
  notes: Example family import

phenotypes:
  file: phenotypes.tsv
  format: hpo_tsv

individuals:
  PROBAND:
    hpo:
      present:
        - HP:0001250
      absent:
        - HP:0004322

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

  qdnaseq:
    enabled: true
    per_sample:
      EMBRYO1:
        bins: QDNAseq/EMBRYO1/bins.csv
        segments: QDNAseq/EMBRYO1/segments.csv

  apcad:
    enabled: true
    per_sample:
      SAMPLE1:
        bed: apcad/SAMPLE1.apcad.bed

  pcf:
    enabled: true
    per_sample:
      SAMPLE1:
        maternal: PCF/SAMPLE1_pcf_mat_data.csv
        paternal: PCF/SAMPLE1_pcf_pat_data.csv

  haplotypes:
    enabled: true
    family_vcf: GLIMPSE2/FAM001.vcf.gz
    source_format: glimpse2

  paraphase:
    enabled: true
    per_sample:
      SAMPLE1:
        json: paraphase/SAMPLE1.paraphase.json

  coverage:
    enabled: true
    per_sample:
      SAMPLE1:
        bed: coverage/SAMPLE1.coverage.bed
```

Long-read manifest example (single-sample nf-core/lrsvar package):

```yaml
schema_version: 1
family_id: pacbio
ped: pacbio.ped

samples:
  HG002: {}

datasets:
  snv:
    enabled: true
    family_vcf: snv/HG002/annotation/HG002_annot.vcf.gz
    index: snv/HG002/annotation/HG002_annot.vcf.gz.tbi
    source_format: clair3
    exclude_filters: [RefCall, NoCall]

  sv_needlr:
    enabled: true
    family_vcf: sv/HG002/annotation/HG002_sv_phased.needLR.4.0.vcf.gz
    index: sv/HG002/annotation/HG002_sv_phased.needLR.4.0.vcf.gz.tbi

  repeats_trgt:
    enabled: true
    per_sample:
      HG002:
        file: repeats/HG002/HG002_tr.vcf.gz
        index: repeats/HG002/HG002_tr.vcf.gz.csi

  paraphase:
    enabled: true
    per_sample:
      HG002:
        json: paraphase/HG002/HG002.paraphase.json

  cnv:
    enabled: true
    per_sample:
      HG002:
        vcf: cnv/HG002/annotation/HG002_annot.vcf.gz
        index: cnv/HG002/annotation/HG002_annot.vcf.gz.tbi
        copy_number_bedgraph: cnv/HG002/HG002.Sample0.copynum.bedgraph
        maf_bigwig: cnv/HG002/HG002.HG002.maf.bw

  mito:
    enabled: true
    per_sample:
      HG002:
        vcf: mito/HG002/HG002.vcf.gz
        index: mito/HG002/HG002.vcf.gz.tbi
        annotation_tsv: mito/annotation/HG002/HG002_snv_annot.txt
        sv_vcf: mito/HG002/HG002_sv.vcf.gz

  alignments:
    enabled: true
    per_sample:
      HG002:
        file: bams/HG002.cram
        index: bams/HG002.cram.crai

  qc:
    enabled: true
    per_sample:
      HG002:
        report: qc/nanoplot/HG002/HG002NanoPlot-report.html
        read_stats: qc/nanoplot/HG002/HG002NanoStats.txt
        depth_summary: qc/depth/HG002/HG002.mosdepth.summary.txt

  pipeline_info:
    enabled: true
    params: pipeline_info/params_2026-07-28_11-18-09.json
    versions: pipeline_info/software_versions.yaml
```

Validation rules:

- the family folder and manifest must exist
- `schema_version` must be `1`
- `family_id` defaults to the folder name when omitted
- the PED file must exist, parse as six-column PED, contain a single family, and match `family_id`
- sample IDs must be unique; non-zero parent IDs must refer to samples in the PED
- manifest `samples` and per-sample datasets must reference PED sample IDs
- manifest `phenotypes.file` must exist when provided, use `format: hpo_tsv`, and reference PED
  sample IDs; malformed rows are reported as warnings and skipped during import
- referenced files must exist
- VCF/BCF datasets must include an index path or have a sibling `.tbi`, `.csi`, or `.idx`; uncompressed `repeats_trgt` `.vcf` files are accepted without an index
- unsupported dataset keys are validation errors
- omitted **core** datasets (`snv`, `sv_needlr`) are warnings; the assay-specific ones
  (`apcad`, `pcf`, `cnv`, `mito`, `qc`, …) are absent by design in most packages and are
  reported as skipped without a warning
- a VCF sample column that resolves to no family sample fails the import; declare
  `vcf_sample` on the dataset entry when the caller renamed it in a way the suffix/prefix
  rules do not cover

First-version import behavior is conservative. The importer always validates and registers package
provenance on the family/sample metadata. It deeply imports datasets where storage exists:
family SNV VCFs, WisecondorX `_bins.bed` as `coverage`, WisecondorX `_segments.bed` as `segments`,
QDNAseq CSV bins as `coverage`, QDNAseq segment CSVs as `segments`, Needlr family SV VCFs as
ClickHouse structural variants with source `needlr`, family or sample APCAD VCF/TSV/BED files as
APCAD interval tracks, sample-scoped TRGT VCFs, family TRGT VCFs into the repeat expansion table,
family GLIMPSE2 VCFs as small variants plus haplotype blocks, sample-scoped Paraphase JSON into
`sample_paraphase_results`, HiFiCNV VCFs as ClickHouse structural variants with source `hificnv`
plus their copy-number bedgraph as a `coverage` interval track, chrM VCFs as small variants with
source `mito` (annotated from the mutserve TSV), NanoPlot/mosdepth QC and the aligned-read location
onto `samples.metadata`, and the Nextflow run record into the family's annotation manifest. Valid HPO phenotype rows are upserted into `individual_hpo`; invalid
phenotype rows are recorded in the `phenotypes` package summary without blocking genomic dataset
imports. Direct per-sample GLIMPSE2 BCF haplotypes are still registered as provenance until a
dedicated BCF importer is added. Imported Paraphase results are
available from the family workspace Paraphase page and `GET /families/{family_id}/paraphase`.
The Paraphase page uses the curated medically relevant region catalog at
`/data/ref-data/paraphase-medical-regions.json`, with the bundled
`data/ref-data/paraphase-medical-regions.json` as a local-development fallback. This catalog controls
which regions are visible by default, the clinical copy-number fields emphasized on cards, and
OMIM disorder links.

## Variant Uploads

The upload endpoints in this section require admin credentials. Non-admin users should use the
Family Builder only for family/sample metadata.

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
