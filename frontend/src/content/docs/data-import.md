# Data import — reference

Data import is how a case enters CoGA: you create the family and its samples, then load the assay layers that the rest of the platform interprets. Intake is split into two focused paths — building a pedigree by hand and importing a prepared folder package — and everything you load is scoped to a genome assembly whose reference data must already be present.

This is the in-depth reference. For the workflow-level overview see the [in-app user guide](/docs) section *Case setup and data import*.

---

## The two intake paths

CoGA gives you two ways in. Pick the one that matches how your data arrives.

| Path | Page | Who | What it does |
| --- | --- | --- | --- |
| **Family Builder** | `/family-builder` | All users | Build a pedigree by hand or from a PED file — add samples, set sex and roles, assign parents and couples, set phenotype and carrier status. Metadata only. |
| **Package Import** | `/package-import` | Admins | Point at a backend-visible folder, discover a manifest, dry-run it, then import the family **and all of its assay layers** in one job. |

The split mirrors the permission model. A regular user can create families and samples and edit pedigree structure, but **cannot upload data files** or overwrite an existing family. Loading genomic data — and replacing existing records — is admin-only.

Phenotype detail is kept separate from pedigree structure. The PED carries only coarse affected/unaffected labels so it stays compatible with standard PED tooling; detailed observations live as HPO annotations on each individual (see *Phenotypes and gene panels* in the user guide).

---

## Reference data comes first

Genes, cytobands, and the clinical annotation layers are **per assembly**. A viewer that cannot resolve coordinates, or a gene lookup that comes back empty, almost always means the reference layers for that assembly are missing — load them before you load a family.

CoGA bootstraps **Homo sapiens / GRCh38** as the default on startup, importing the core reference tables (cytobands, genes) from UCSC `hg38` when they are absent. If UCSC is unreachable, startup still proceeds with an empty assembly shell that you can fill later. Clinical CNV syndromes, segmental duplications / LCRs, and the dbNSFP-backed gene reference can also be seeded automatically when their tables are empty.

From the **Reference Catalog** in the web UI, an admin can bootstrap any UCSC-backed organism and assembly: pick an organism, pick an assembly, and trigger an automatic download of cytobands and genes (`POST /assemblies/reference-import`). Per-assembly manual uploads are also available for cytobands, genes, the blacklist, clinical CNVs, and segmental duplications.

The expected file shapes are:

| Layer | Format |
| --- | --- |
| `cytobands` | UCSC-style cytoband TSV |
| `genes` | transcript/gene BED-style export matching the loader contract |
| `blacklist` | BED-like interval list |
| `clinical_cnvs` | BED-like interval list with label/detail columns; 9-column ClinGen TSV bundles are also accepted |
| `segmental_duplications` | BED-like interval list for SegDups / LCRs (ClinGen recurrent-CNV BEDs supported) |

**Gene reference sync.** From the Administration section an admin can refresh cached human gene data. The sync prefers a local dbNSFP gene file and falls back to public ClinGen, GenCC, ClinVar, HGNC, Ensembl, and NCBI sources only for genes the local file does not cover. It enriches each gene with identifiers, names and aliases, disease context (OMIM / Orphanet / GenCC), ClinGen dosage, HPO/GO/pathway terms, expression, and constraint metrics.

---

## What each assay layer unlocks

A family is only as capable as the layers you load into it. Each layer is optional and lights up a specific part of the platform.

| Layer | Unlocks |
| --- | --- |
| **Small variants (SNV/indel)** | the small-variant workbench and review |
| **Structural variants** | the SV table, SV review, and CNV detail |
| **Repeat expansions (TRGT)** | the repeat-expansion view, scored against STRchive loci |
| **Paraphase** | segmental-duplication / paralogue resolution |
| **Mitochondrial calls** | mtDNA homo- and heteroplasmy analysis |
| **Coverage, segments, APCAD, haplotypes, recombination** | the genome and chromosome track viewers |

Several of these also feed the automated **sample-integrity QC** (`/docs/reference/sample-qc`) and the provenance captured for **clinical traceability** (`/docs/reference/clinical-traceability`).

---

## The package import flow

Package import is the one-job path: a single backend-visible folder containing the PED, the assay files, and a manifest that maps them. The folder path is resolved **on the backend host**, not uploaded from your browser.

A `standard_v1` package looks like this — only `manifest.yaml` and the PED are required; every dataset folder is optional:

```text
FAM001/
  manifest.yaml
  family.ped
  phenotypes.tsv
  snv/        family.annotated.vcf.gz (+ .tbi)
  needlr/     family.sv.annotated.vcf.gz (+ .tbi)
  repeats/    family.trgt.vcf.gz (+ .tbi) | FAM001_tr.vcf
  wisecondorx/SAMPLE1/{bins,segments}.bed
  QDNAseq/    EMBRYO1/{bins,segments}.csv
  apcad/ APCAD/ PCF/   APCAD interval tracks + PCF segments
  GLIMPSE2/   FAM001.vcf.gz          (haplotypes)
  paraphase/  SAMPLE1.paraphase.json
```

The `standard_v1` naming scheme knows several fallback names per layer (for example a `{family_id}`-prefixed VCF or a generic `family.*` name), so packages from slightly different pipelines still resolve.

### Manifest, dry-run, import

The Package Import UI walks you through it:

1. **Pick the family folder.** A dropdown lists folders discovered under the configured import roots; the PED is auto-detected inside each.
2. **Choose new vs existing family**, and the existing-data policy (below).
3. **Add phenotypes** — an HPO TSV or inline annotations — plus optional notes.
4. **Discover.** CoGA parses the PED, scans the expected dataset paths, and returns a generated manifest preview with an availability table.
5. **Write `manifest.yaml`** (edit the YAML first if needed).
6. **Dry-run, then import.** The dry run validates the package **without writing** any family or dataset records. When it is clean, start the real import.
7. **Track it.** Imports run on background workers; re-open the page and use *Recent family imports* to poll job status, logs, validation errors, warnings, and per-dataset summaries.

The same flow is available over the API — `POST /family-imports/manifest/discover`, `…/manifest/write`, `POST /family-imports` (with `dry_run: true`), and `GET /family-imports/{job_id}` for status. A CLI dry-run validator is also available for a folder on the backend host.

**Existing-data policy** (`conflict_mode`) decides what happens when the family or sample IDs already exist:

| Mode | Behaviour |
| --- | --- |
| `cancel` | fail if the family or any sample ID already exists |
| `update` | attach to the existing family; skip dataset tables that already hold data |
| `overwrite` | attach to the existing family; replace imported rows for the enabled datasets |

### Validation rules

Discovery and dry-run enforce, among others:

- the folder and `manifest.yaml` must exist; `schema_version` must be `1`; `family_id` defaults to the folder name.
- the PED must parse as six-column PED, contain **a single family**, and match `family_id`; sample IDs must be unique and every non-zero parent ID must refer to a sample in the PED.
- manifest `samples`, per-sample datasets, and any phenotype rows must reference PED sample IDs.
- every referenced file must exist. Compressed VCF/BCF datasets need an index (`.tbi`, `.csi`, or `.idx`, or a declared `index:`); an uncompressed TRGT `.vcf` is accepted without one.
- an **unsupported** dataset key is an error; an **omitted** supported dataset is a warning, because all datasets are optional.

Malformed phenotype rows are reported as warnings and skipped — they never block the genomic import.

---

## Direct assay uploads

Outside the package flow, admins can upload individual layers (the Family Builder is metadata-only for non-admins):

- **Small variants** — `POST /families/{family_id}/small-variants/upload` (stored in ClickHouse)
- **Structural variants** — `POST /structural-variants/upload/{sample_id}` (ClickHouse)
- **Repeat expansions** — `POST /repeat-expansions/upload/{sample_id}` (Postgres)
- **Interval tracks** — `POST /bed/upload/{sample_id}/{bed_type}`, where `bed_type` is coverage, APCAD, segments, or haplotypes

---

## Recommended order for a fresh load

1. Create the species, assembly, and project metadata.
2. Import or upload the reference datasets for that assembly.
3. Create the family — Family Builder, PED upload, or a package import.
4. Load small and structural variants.
5. Load repeat expansions and interval tracks.

---

## Common pitfalls

- **Empty viewers or gene lookups** are usually a reference-data gap, not a data-import failure. Confirm the assembly's reference layers are loaded before re-importing the family.
- **Browser path confusion.** The package folder path is resolved on the backend host and must sit under an allowed import root; it is not a path from your own machine.
- **A non-admin cannot upload data.** Regular users build pedigrees and edit structure only; assay uploads and overwrites need admin credentials.
- **Missing index.** A compressed VCF/BCF without a sibling index (or a declared `index:`) fails validation. Uncompressed TRGT VCFs are the only exception.
- **Restructuring a family with data.** Once genomic data exists, membership or relationship edits are blocked unless you explicitly confirm clearing the imported datasets — the family is then reloaded under the new structure. Phenotype and carrier-label changes apply immediately, but saved interpretations should be re-reviewed.
- **Phenotype rows that reference unknown sample IDs or unloaded HPO terms** are skipped with a warning rather than failing the import — check the job summary if an expected annotation is missing.
