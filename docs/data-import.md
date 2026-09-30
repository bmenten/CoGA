# Data import

This is the reference for what CoGA loads and in which format: reference data, the gene
reference, family folder packages and single-file uploads. It is written for admins and
developers.

The step-by-step use of the Package Import page, and the common mistakes, are in the in-app
guide ([Data import](../frontend/src/content/docs/data-import.md), shown in the app at
`/docs/reference/data-import`).

A family gets into CoGA in one of two ways:

- **Family Builder** (`/family-builder`): a pedigree typed in by hand. Metadata only.
- **Package Import** (`/package-import`, admins): one folder with the PED, the assay files and
  a manifest. One job imports the family and all its data.

Loading data files, replacing an existing family and every reference-data upload are
admin-only. All API paths below sit under `/api`. Every setting named here is listed with
its default in [.env.example](../.env.example).

For a new family: check that its assembly has its reference data (section 1), make sure its
project exists (**Admin → Projects & Access**), then import the package (section 3).

## 1. Reference data

Genes, cytobands and the annotation layers belong to one genome assembly. A viewer that
stays empty, or a gene search that finds nothing, usually means the assembly's reference
data is missing.

### What startup loads

Each time the backend starts it fills in what is missing. A table that already holds rows
for the assembly is left alone.

| What | Where it comes from | Settings |
| --- | --- | --- |
| Homo sapiens / GRCh38 | Created if absent | `REFERENCE_BOOTSTRAP_ENABLED` |
| GRCh38 cytobands | UCSC `hg38` download | |
| GRCh38 gene loci | GENCODE GTF (see below); the UCSC gene table if GENCODE fails | `REFERENCE_GENCODE_GTF_URL`, `REFERENCE_GENCODE_REFSEQ_METADATA_URL` |
| T2T-CHM13v2.0 (off by default) | UCSC `hs1` RefSeq GTF | `REFERENCE_BOOTSTRAP_T2T`, `REFERENCE_T2T_GTF_URL` |
| Segmental duplications / LCRs | The ClinGen recurrent-CNV BED in `data/ref-data/` | `REFERENCE_SEGMENTAL_DUPLICATIONS_PATH` |
| Clinical CNV syndromes | Only if the file exists; it is not shipped | `REFERENCE_CLINICAL_CNVS_PATH` |
| HPO ontology | `data/ref-data/hpo/hp.obo`, else downloaded over HTTPS | `HPO_BOOTSTRAP_ON_STARTUP`, `HPO_ONTOLOGY_PATH`, `HPO_ONTOLOGY_URL`, `HPO_ONTOLOGY_SHA256` |
| TRGT repeat catalogue | `data/ref-data/STRchive-loci.json`, refreshed on every start | `TRGT_STRCHIVE_LOCI_PATH` |
| First gene reference sync | Queued only when the dbNSFP gene file is present and no gene has been synced yet | `GENE_REFERENCE_BOOTSTRAP_ON_STARTUP`, `GENE_REFERENCE_DBNSFP_GENE_PATH` |

The segmental duplications and clinical CNVs are loaded into the assembly named by
`REFERENCE_BOOTSTRAP_ASSEMBLY_NAME` (GRCh38). If UCSC cannot be reached, startup still
creates the empty GRCh38 assembly and carries on.

The default clinical-CNV file is not in the repository, so a fresh install has no clinical
CNVs, and nothing warns about it. Build them with the clinical CNV knowledgebase rebuild
(the ↻ button next to the clinical-CNV count on **Admin → Species & Assemblies**;
`POST /admin/clinical-cnv-kb/rebuild`), or upload a file (below).

**Gene loci.** GRCh38 gene loci come from the GENCODE basic annotation, pinned by the release
in `REFERENCE_GENCODE_GTF_URL`. That is the annotation the variant pipeline uses, so the
coordinates agree with the variant annotation. Each row is one transcript, with its biotype,
Ensembl and HGNC identifiers, MANE tags and RefSeq accessions (from GENCODE's
`metadata.RefSeq` file). The import records its release (for example
`gencode v50 (Ensembl 116)`). If GENCODE cannot be fetched, the UCSC gene table is used
instead. The import result and the log then carry a warning, and the import record names
the UCSC table. The report's annotation manifest shows which source was used, as
`gene_loci`. To move to a newer GENCODE release, change both GENCODE URLs together and
re-import the genes with overwrite.

**T2T-CHM13v2.0** is an optional second assembly (`REFERENCE_BOOTSTRAP_T2T=true`). It
roughly doubles the reference data, and the gene page's T2T rows stay empty until it is on.
Its annotation is poorer than GRCh38's: GENCODE publishes none, so the genes come from UCSC's
RefSeq GTF (labelled `ucsc ncbiRefSeq`). These rows have coordinates but no biotypes, Ensembl
identifiers or MANE tags, and the cytobands are one band per chromosome.

### Loading or replacing reference data

Admins manage reference data on **Admin → Species & Assemblies**
(`/admin/reference/assemblies`; everyone can view it at `/reference-data`):

- **Add organism / assembly (from UCSC)** downloads cytobands and genes for any UCSC genome
  (`POST /assemblies/reference-import`). Human GRCh38 and T2T genes come from their GTFs as
  above; other genomes use a UCSC gene table.
- **Upload** loads one file into one assembly
  (`POST /assemblies/{assembly_id}/reference-upload/{dataset_type}`). If the assembly already
  holds that dataset, the upload is refused unless `overwrite=true`, which replaces it.

Every import and upload is recorded with who ran it and the source, and shows under
"Recent reference activity".

| `dataset_type` | File format |
| --- | --- |
| `cytobands` | Tab-separated: chrom, start, end, band, stain (UCSC cytoband format) |
| `genes` | The 12-column tab-separated transcript file that `scripts/gtf_to_ccds_gene_bed.py` writes (with exon and intron columns) |
| `blacklist` | BED-like: chrom, start, end, label |
| `clinical_cnvs` | UCSC bedDetail-style (at least 11 columns), or a TSV with a header such as the knowledgebase output |
| `segmental_duplications` | BED-like intervals; the ClinGen recurrent-CNV BED is supported |
| `dgv` | The Database of Genomic Variants TSV (variantaccession, chr, start, end, varianttype, variantsubtype, …) |

The full DGV file (about 2 million rows) is too large for the upload. Load it inside the
backend container with `scripts/import_dgv.py`, which inserts in batches (see
[scripts/README.md](../scripts/README.md)).

### Updating the HPO ontology

To move to a newer HPO release, put the new `hp.obo` in `/data/ref-data/hpo/` and run the sync
on **Admin → HPO Terminology** (`POST /admin/hpo/sync`). The page starts from the file the
backend is configured to load (`HPO_ONTOLOGY_PATH`). The sync previews the changes first.
It only reads files inside the configured ontology folders.

## 2. Gene reference sync

The gene reference is the cached information per human gene: names and identifiers, disease
links, dosage, constraint, expression and more (`gene_info`). An admin refreshes it from
**Admin → Species & Assemblies** (↻ next to the gene count) or the gene reference page
(`/admin/reference/gene-reference`; `POST /admin/gene-reference/refresh-all`).

HGNC decides which human genes exist. The sync takes the HGNC complete set as its list of
genes. A symbol that HGNC has renamed is moved onto the current symbol, keeping its locus. A
gene HGNC knows but the assembly does not place still gets a record, without coordinates. A
historic symbol claimed by two genes is dropped rather than guessed.

Every source below is consulted for every gene:

| Source | Contributes |
| --- | --- |
| HGNC complete set | identity: HGNC ID, current, previous and alias symbols, Ensembl, Entrez, RefSeq, CCDS, UniProt and MANE ids, locus group, band |
| dbNSFP gene file (`GENE_REFERENCE_DBNSFP_GENE_PATH`) | constraint metrics, HPO/GO/pathway terms, OMIM/Orphanet/GenCC disease context, model organisms, tissue expression |
| ClinGen gene validity and dosage | gene-disease validity, haploinsufficiency and triplosensitivity |
| GenCC | submitted gene-disease assertions and their classifications |
| ClinVar `gene_condition_source_id` | gene-disease relationships and OMIM disease links |
| NCBI Gene (one lookup per gene) | a gene summary for genes that are not in dbNSFP |

Each gene stores a status per source: `success` (the source had a record), `missing` (it was
asked and had nothing), `not_consulted` (never asked, so it says nothing about the source) or
`error` (the download or parse failed). Each status also records which release of the source
answered: `release` (the version the file states, where it states one),
`release_detail.checksum` (the sha256 of the bytes that were parsed), `release_detail.size_bytes`
and `fetched_at`. The checksum always exists, so two caches have the same provenance exactly
when their checksums match. The admin page flags a source whose cached genes came from more
than one release, which is the sign of a partial refresh; a full sync fixes it.

The pinned release is **dbNSFP 5.4** (August 2026, GENCODE 50 / Ensembl 116). The gene table ships
inside the full dbNSFP archive from <https://www.dbnsfp.org/download> (registration required); place
its gene file at the configured path, renamed to `dbNSFP5.4_gene.gz`. Bumping to a newer dbNSFP
release means: drop the new gene file next to (or over) the old one, update
`GENE_REFERENCE_DBNSFP_GENE_PATH` and the defaults that reference the filename, and run a gene
reference sync — cached `gene_info` rows are **not** refreshed automatically, they keep the values
from the release they were synced with. Columns are read by header name, so dbNSFP adding or
reordering gene columns is safe; a renamed or removed column silently empties that field.

Until the file is in place, dbNSFP reports `missing` for every gene and the other sources
still answer. Its checksum is taken over the file as it sits on disk, so `sha256sum` on the
deployed file reproduces it.

## 3. Package import

### Where packages live

`FAMILY_IMPORT_ROOTS` lists the places Package Import may read (comma-separated). The
default is the local folder `/data/families`, which Docker Compose mounts from `./data`. A
cloud deployment points it at a bucket prefix:

```env
FAMILY_IMPORT_ROOTS=/data/families                # local (default)
FAMILY_IMPORT_ROOTS=gs://my-coga-bucket/imports   # a bucket; s3:// also works
```

On Google Cloud, Terraform sets `gs://<phi bucket>/imports` when `storage_backend` is `gcs`
(see [deployment-gcp.md](deployment-gcp.md) §11). The backend's cloud identity needs read
access to the bucket. A path outside these roots is refused (`package_folder_not_allowed`).

A package in a bucket is downloaded to a temporary folder for validation and import,
except its aligned reads: `.cram`, `.crai`, `.bam` and `.bai` files, and a `.csi` named after
an alignment (`<sample>.bam.csi`), stay in the bucket. No importer reads them, and a
whole-genome CRAM is tens of GB. Only the `alignments` dataset may name such a file. Its
importer checks that the object exists and records its URI, and the genome browser reads it
from there. Every other file must be downloaded. On Cloud Run the temporary folder is held in
memory; see [deployment-gcp.md](deployment-gcp.md) §11 for sizing.

The temporary folder is deleted after the import, so what the import records and reports names
the bucket folder and its objects: the job's log and validation report, the family's package
record and the raw-file provenance ([database.md](database.md), `raw_import_files`). A manifest
without `family_id` takes the bucket folder's name, the last part of its URI, as a local
package takes its folder's name.

The **Discover** and **Write manifest** steps only work on local folders, so a package in a
bucket must already contain its manifest.

### The import job

The Package Import page lists the packages it finds under the roots (**Rescan** to refresh);
you can also type a folder path. You choose a project, a new or existing family, and what to
do with data that already exists. **Discover manifest** drafts a manifest, which you can
edit and save with **Write manifest.yaml**. **Dry run** is on by default: it validates
without writing anything. Untick it and **Start import** to load the data. Jobs show under
"Recent family imports". The in-app guide walks through these steps.

The API behind the page:

| Call | Does |
| --- | --- |
| `GET /family-imports/packages` | lists the packages under `FAMILY_IMPORT_ROOTS` |
| `POST /family-imports/manifest/discover` | reads the PED, looks for the expected files and returns a draft manifest |
| `POST /family-imports/manifest/write` | saves the manifest into the folder |
| `POST /family-imports/validate` | validates at once, without creating a job |
| `POST /family-imports` | queues a job: `folder_path`, `project_id`, `dry_run`, `family_id` (for an existing family), `conflict_mode` |
| `GET /family-imports`, `GET /family-imports/{job_id}` | job status, logs, validation errors and warnings, and a summary per dataset |

`conflict_mode` decides what happens when the family or its samples already exist:

- `cancel` (default): the import fails (`existing_family_or_samples`). A dry run only warns.
- `update`: import into the existing family, and skip each dataset that already has data.
- `overwrite`: import into the existing family, and replace each imported dataset.

Jobs run on background workers: one job at a time per backend process, or more with
`FAMILY_IMPORT_WORKER_COUNT` (up to 8).

If a dataset fails, the job ends as `failed` and CoGA does not leave a half-loaded family
that looks complete:

- A new family where nothing imported is removed again.
- A failed `overwrite` of an existing family is put back to its state before the import.
- In any other case the datasets that did import are kept, and the family is flagged as
  import-incomplete (`families.metadata.import_incomplete`) until a later import succeeds.
  The flag holds the datasets that failed and those that imported, the time and the import
  job's id; the job's record holds each dataset's error, which the flag does not copy.

While the flag is set, every family page shows *Import incomplete*, and sign-out needs the
signer to acknowledge it with a reason ([clinical-traceability.md](clinical-traceability.md)).

## 4. Package layout and manifest

A short-read package, with every dataset folder optional:

```text
FAM001/
  manifest.yaml            (or manifest.yml / manifest.json)
  family.ped
  phenotypes.tsv
  snv/family.annotated.vcf.gz (+ .tbi)
  needlr/family.sv.annotated.vcf.gz (+ .tbi)
  repeats/FAM001_tr.vcf
  wisecondorx/SAMPLE1/{bins,segments}.bed
  QDNAseq/EMBRYO1/{bins,segments}.csv
  apcad/SAMPLE1.apcad.bed
  PCF/EMBRYO1_pcf_{mat,pat}_data.csv
  GLIMPSE2/FAM001.vcf.gz
  paraphase/SAMPLE1.paraphase.json
```

A long-read package (nf-core/lrsvar) is laid out per sample:

```text
pacbio/
  manifest.yaml
  pacbio.ped
  bams/HG002.cram (+ .crai)
  snv/HG002/annotation/HG002_annot.vcf.gz (+ .tbi)
  sv/HG002/annotation/HG002_sv_phased.needLR.4.0.vcf.gz (+ .tbi)
  cnv/HG002/annotation/HG002_annot.vcf.gz, cnv/HG002/HG002.*.{copynum.bedgraph,depth.bw,maf.bw}
  mito/HG002/HG002.vcf.gz, mito/annotation/HG002/HG002_snv_annot.txt
  repeats/HG002/HG002_tr.vcf.gz (+ .csi)
  paraphase/HG002/HG002.paraphase.json
  qc/nanoplot/HG002/…, qc/depth/HG002/HG002.mosdepth.summary.txt
  pipeline_info/software_versions.yaml, params_*.json
```

Discover knows several file names per dataset, and shows what it found. The exact patterns
are the `standard_v1` entry of `NAMING_SCHEMES` in
`backend/app/services/family_package_discovery.py`. A pattern may contain `*` (the SV
annotator puts its version in the file name); the saved manifest always holds the real path.
A long-read package counts a per-sample SNV file as the family's callset only when the
family has one sample.

### The manifest

```yaml
schema_version: 1          # must be 1
family_id: FAM001          # defaults to the folder name
ped: family.ped
analysis_type: monogenic_nipt   # optional
roi: CFTR                  # optional region of interest

metadata:
  pgt:
    inheritance_model: AR  # AD, AR, XLD, XLR or mitochondrial
    obligate_carriers: [FATHER]
    proven_carriers: [MOTHER]

family:                    # optional: member states and extra relationships
  members:
    FATHER: {clinical_status: unaffected, carrier_status: carrier, carrier_type: proven}
  relationships:
    couples:
      - partners: [FATHER, MOTHER]
        context: reproductive
    parent_child:
      - child: PROBAND
        parents: [FATHER, MOTHER]

phenotypes:
  file: phenotypes.tsv
  format: hpo_tsv

individuals:
  PROBAND:
    hpo:
      present: [HP:0001250]
      absent: [HP:0004322]

datasets:
  snv:
    family_vcf: snv/family.annotated.vcf.gz
    index: snv/family.annotated.vcf.gz.tbi
    annotation_tsv: snv/annotation/FAM001_annot.tsv.gz   # optional VEP table
  sv_needlr:
    family_vcf: needlr/family.sv.annotated.vcf.gz
  wisecondorx:
    per_sample:
      SAMPLE1: {bins: wisecondorx/SAMPLE1/bins.bed, segments: wisecondorx/SAMPLE1/segments.bed}
  cnv:
    per_sample:
      HG002:
        vcf: cnv/HG002/annotation/HG002_annot.vcf.gz
        copy_number_bedgraph: cnv/HG002/HG002.Sample0.copynum.bedgraph
        maf_bigwig: cnv/HG002/HG002.HG002.maf.bw
```

`analysis_type: monogenic_nipt` marks a monogenic NIPT family (see
[monogenic-nipt.md](monogenic-nipt.md)). A dataset can be switched off with `enabled: false`.
The `snv` dataset takes three optional settings:

- `source_format`: `clair3` for a directly called callset, `glimpse2` for imputed genotypes.
  Set it for long-read packages. Without it the importer guesses from the first record, and
  a phased primary callset can be taken for an imputed one and hidden from the diagnostic
  views.
- `exclude_filters`: FILTER values to drop at import, such as `[RefCall, NoCall]` for a
  DeepVariant whole-genome callset. A record is dropped only when all its FILTER values are
  in the list.
- `vcf_sample`: the VCF column to use, when the caller named the sample column after its
  input file. CoGA already strips known tool suffixes; a column that matches no family
  sample fails the import.

### Where each dataset lands

| Dataset | Stored as |
| --- | --- |
| `snv` | small variants (ClickHouse), with the VEP table if given |
| `sv_needlr` | structural variants (ClickHouse), source `needlr` |
| `repeats_trgt` | repeat expansions (Postgres), scored against the TRGT catalogue |
| `wisecondorx`, `qdnaseq` | bins as the `coverage` track, segments as the `segments` track |
| `coverage` | the `coverage` track (a BED per sample) |
| `apcad` | the `apcad` track |
| `pcf` | the `apcad_pcf` track (maternal and paternal segment files) |
| `haplotypes` | a family GLIMPSE2 VCF becomes small variants plus haplotype blocks; a per-sample BCF is only registered, not imported |
| `paraphase` | Paraphase results (Postgres), shown on the family's Paraphase page |
| `cnv` (HiFiCNV) | structural variants, source `hificnv`; the depth bigWig as `coverage` and the copy-number bedGraph as `segments` (both stored as log2 ratios, like the other callers), and the MAF bigWig as `apcad` |
| `mito` | chrM small variants, source `mito`, annotated from the mutserve table |
| `qc` | the sample's sequencing QC, shown as a chip in the family members table |
| `alignments` | the CRAM/BAM location, used by IGV: the package-relative path and, for a package in a bucket, the object's URI |
| `pipeline_info` | tool versions in the annotation manifest; run parameters on the family |

Each caller's rows carry their own source tag, so re-importing one callset never removes
another, and two callers' rows of one variant stay two rows in storage
([database.md](database.md#row-identity)). The `mito` dataset has one file per sample, and
each file replaces only that sample's calls. The other samples' mitochondrial calls stay as
they are, one row per variant with every sample's call, so the mtDNA analysis can set the
mother's calls beside the children's. A file without chrM variants removes that sample's calls.

The track viewers draw one track per caller (`GET /families/{family_id}/track-availability`
lists them). The three HiFiCNV files are also served unchanged to the genome browser (IGV),
from the bucket for a package in a bucket.

### Validation

Discover, dry run and import check that:

- the folder and the manifest exist, and `schema_version` is 1;
- the PED parses as six-column PED, holds one family and matches `family_id`; sample IDs are
  unique and every parent is in the PED. For an import into an existing family the PED file
  may be missing: the family's stored pedigree is used;
- the manifest's samples and per-sample entries name samples in the PED;
- a phenotype file named in the manifest exists and uses `format: hpo_tsv`;
- every file the manifest names exists (for a package in a bucket, an alignment exists in the
  bucket), and a compressed family VCF has its index (`.tbi`, `.csi` or `.idx`, next to it or
  given as `index:`); a plain `.vcf` needs none;
- every dataset key is a known one.

A missing `snv` or `sv_needlr` dataset is a warning; other datasets are optional without a
warning. A bad phenotype row (an unknown person, HPO ID or status) is a warning: the row is
skipped and never blocks the import.

## 5. Pedigrees and phenotypes

- `POST /ped/manual`: any signed-in user creates a family from typed-in members.
- `POST /ped/upload`: admins upload a PED file, with an optional region of interest,
  inheritance model and carrier lists.
- Only admins can replace an existing family or sample (`overwrite=true`).

PED column 6 is the phenotype: `0` or `-9` unknown, `1` unaffected, `2` affected. CoGA stores it
as the member's clinical status and keeps carrier state apart (`carrier_status`: unknown,
not_carrier or carrier; `carrier_type`: obligate, proven, reported or inferred). A package
PED may add columns after the sixth, such as `role=embryo`, `role=relative`, `carrier=true` or
`carrier_type=obligate`.

The PED stays coarse. Detailed phenotypes are HPO terms per person, stored apart from the
pedigree. A package brings them in two ways:

- `phenotypes.file` with `format: hpo_tsv`: a tab-separated file with the columns
  `family_id`, `individual_id`, `hpo_id` and `status` (`present`, `absent` or `unknown`), and
  optionally `label`, `onset`, `evidence`, `source` and `note`;
- `individuals.<sample>.hpo.present`, `.absent` and `.unknown` in the manifest.

A row naming an unknown person, or an HPO term that is not loaded, is skipped with a warning.
The **HPO terms** and **Notes** fields on the Package Import page go into the manifest's
`metadata` and are kept with the import record; they are not per-person phenotypes.

After import, phenotypes are edited per person
(`/families/{family_id}/hpo`, `/families/{family_id}/members/{sample_id}/hpo`). Family and
member edits are described in [family-member-management.md](family-member-management.md); how
the haplotype risk colours are derived is in
[haplotype-segregation-analysis.md](haplotype-segregation-analysis.md).

## 6. Single-file uploads

Admins can also load one file at a time, on the **Upload family and sample data** page
(`/upload-data`). Each upload refuses to replace existing data unless `overwrite=true`; the
page asks before it sends that.

| Upload | Endpoint | Stored in |
| --- | --- | --- |
| Family small variants (VCF) | `POST /families/{family_id}/small-variants/upload` | ClickHouse |
| Structural variants (VCF) | `POST /structural-variants/upload/{sample_id}` | ClickHouse |
| Repeat expansions (TRGT VCF) | `POST /repeat-expansions/upload/{sample_id}` | Postgres |
| Interval tracks (BED) | `POST /bed/upload/{sample_id}/{bed_type}` | ClickHouse |

`bed_type` is `coverage`, `segments`, `apcad` or `apcad_pcf`. Haplotype blocks cannot be
uploaded as a BED; they come from a GLIMPSE2 small-variant upload (`source_format=glimpse2`)
or package.

A family small-variant upload is one family VCF from one callset:

- `source_format` is `clair3` (a directly called callset), `glimpse2` (imputed genotypes, which
  also make the haplotype blocks), `mito` (chrM calls) or `auto`, the default, which tells
  `clair3` and `glimpse2` apart from the first record. Any other value is refused (422) before
  anything is stored.
- The upload is refused (409) when the family already has calls from that callset.
  `overwrite=true` replaces them; every other callset stays as it is.
- The answer says what was stored: the records loaded and skipped, the callset, the haplotype
  blocks made, and the tool versions the VCF header names, which also go into the family's
  annotation manifest.

A structural-variant upload loads one sample's calls from one caller, and only that caller's
calls are checked and replaced:

- `source_format` is `sniffles`, `spectre`, `manual` (a TSV, stored as source `manual_upload`)
  or `auto`, the default, which works the caller out from the file. Any other value is
  refused (400).
- The upload is refused (409) only when the sample already has calls from the same source.
- `overwrite=true` replaces that sample's calls from that source. The other samples' calls
  from it, and the SVs of every other source (a package's `needlr` or `hificnv` calls, another
  caller's upload), stay as they are.
- The caller the VCF header names is recorded in the family's annotation manifest
  ([annotation-provenance.md](annotation-provenance.md)).

Deleting a sample's structural variants on **Admin → Family & Sample Data** removes its calls
from every source. Both this delete and an upload rewrite the stored rows, and write every
call they do not change back as stored: with its phase set and breakend end, in its project,
an inactive member's included.

## 7. Troubleshooting

| Message or symptom | Cause |
| --- | --- |
| `package_folder_not_allowed` | The folder is not under `FAMILY_IMPORT_ROOTS`. |
| `manifest_missing` | The folder has no `manifest.yaml`, `.yml` or `.json`. Run Discover and Write manifest (local folders only). |
| `manifest_schema_version_unsupported` | `schema_version` is not 1. |
| `ped_family_mismatch`, `ped_multiple_families` | The PED's family ID differs from `family_id`, or it holds more than one family. |
| `dataset_vcf_index_missing` | A compressed VCF has no `.tbi`, `.csi` or `.idx`. |
| `existing_family_or_samples` | The family or a sample exists; choose `update` or `overwrite`. |
| "sample column(s) … match no sample in the family" | Set `vcf_sample` on the dataset. |
| Empty viewers or gene search | The assembly's reference data is missing (section 1). |
| dbNSFP `missing` on every gene | The dbNSFP gene file is not at `GENE_REFERENCE_DBNSFP_GENE_PATH`. |

## 8. Demo data and helper scripts

Synthetic demo families and their loader are described in [demo/README.md](../demo/README.md);
the helper scripts are listed in [scripts/README.md](../scripts/README.md).
