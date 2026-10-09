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
| TRGT repeat catalogue | The built-in loci (`repeat_expansion_catalog.py`) and `data/ref-data/STRchive-loci.json`, both refreshed on every start. The repeat page classifies imported calls again against it; the genome tracks show the status stored at import until the calls are imported again | `TRGT_STRCHIVE_LOCI_PATH` |
| First gene reference sync | Queued only when the dbNSFP gene file is present and no gene has been synced yet | `GENE_REFERENCE_BOOTSTRAP_ON_STARTUP`, `GENE_REFERENCE_DBNSFP_GENE_PATH` |

The segmental duplications and clinical CNVs are loaded into the assembly named by
`REFERENCE_BOOTSTRAP_ASSEMBLY_NAME` (GRCh38). If UCSC cannot be reached, startup still
creates the empty GRCh38 assembly and carries on.

The default clinical-CNV file is not in the repository, so a fresh install has no clinical
CNVs, and nothing warns about it. Build them with the clinical CNV knowledgebase rebuild
(the ↻ button next to the clinical-CNV count on **Admin → Species & Assemblies**;
`POST /admin/clinical-cnv-kb/rebuild`), or upload a file (below). A rebuild whose ClinGen dosage
curation or recurrent CNV regions cannot be loaded fails, says why on that page, and leaves the
clinical CNVs as they were.

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

Every import and upload is recorded with who ran it, the source, and the release the source
states: a GENCODE import its version and date, every other import `not stated` (see
[database.md](database.md)). The imports show under "Recent reference activity".

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
| `POST /family-imports` | queues a job: `folder_path`, `project_id`, `dry_run`, `family_id` (for an existing family; one that is not an ID is refused with 400 before the job is written), `conflict_mode` |
| `GET /family-imports`, `GET /family-imports/{job_id}` | job status, logs, validation errors and warnings, and a summary per dataset |

`conflict_mode` decides what happens when the family or its samples already exist:

- `cancel` (default): the import fails (`existing_family_or_samples`). A dry run only warns.
- `update`: import into the existing family, and skip each dataset that already has data.
- `overwrite`: import into the existing family, and replace each imported dataset.

While a job runs, the page shows each dataset's progress (`progress` in its summary): how
long a finished dataset took and, for the one running, the share of its files read and about
how long it should still take, at the pace it has read them so far. The loaders that count
what they read are the small variants (the SNV VCF, the NIPT pair's files, the imputed
genotypes) and APCAD, which take most of an import's time; another dataset shows how long it
has run. The time left is the running dataset's: the datasets after it are not estimated, and
the page says how many follow. It is first given after half a minute of reading.

Jobs run on background workers: one job at a time per backend process, or more with
`FAMILY_IMPORT_WORKER_COUNT` (up to 8). A running job writes a heartbeat every minute. A job
whose heartbeat is ten minutes old belongs to a process that has stopped (a restart, a
crash, running out of memory), and the next worker to look takes it over:

- If the import had not begun writing the family (the job was still `validating`), the
  worker runs it again from the start. The job keeps the earlier attempt's log lines.
- If it had (the job was `running`), the job ends as `failed`, interrupted, and is not run
  again: a run from the start would not undo what it wrote. Its log and dataset summaries
  stay as the import left them.

If a dataset fails, the job ends as `failed` and CoGA does not leave a half-loaded family
that looks complete:

- A new family where nothing imported is removed again.
- A failed `overwrite` of an existing family is put back to its state before the import.
- In any other case the datasets that did import are kept, and the family is flagged as
  import-incomplete (`families.metadata.import_incomplete`). The flag holds the datasets that
  failed and those that imported, the time and the import job's id; the job's record holds
  each dataset's error, which the flag does not copy. It stays until an import has imported
  each failed dataset again, for the same samples and (for the small variants) the same
  `source_format`; an import that completes without one leaves it flagged, and says so in its
  log. An update will do: the failing loader's rows were rolled back or cleaned up, so the
  update finds none and imports the dataset whole (one whose rows stayed is skipped, and a
  skipped dataset does not count). A later failure keeps an earlier one's failed datasets in
  the flag, each with the job that holds its error (`failed_jobs`).

An import whose process stops part-way does none of this: nothing runs in a process that
has ended. So an import marks the family before it writes anything of it, with an entry in
`families.metadata.import_unfinished` (its job, when it began, and its datasets), records
there each dataset it finishes, and removes the entry when it ends. An entry that stays
names an import that stopped, and the datasets it had not finished, which may be partly
written or missing. Only an import that completes with `overwrite` and imports those
datasets again, for the same samples and (for the small variants) the same `source_format`,
removes it: an overwrite replaces only that. An `update` cannot: it skips a dataset that
already has data, partly written data too. An import that fails after marking the family
and before its first dataset (while registering it, say) wrote none of its datasets: its
entry becomes the import-incomplete flag, naming them all as failed.

An `overwrite` of an existing family first copies the family's variant and track rows into
backup tables in ClickHouse, to put the family back if a dataset fails, and drops them when
it ends. One whose process stopped could not: the worker that ends its job drops them, and
every start drops the backups no running import owns. The family is not put back from such a
backup: the part held in Postgres was lost with the process, and the family may have been
written since.

While the flag or an entry is set, every family page shows *Import incomplete*, and sign-out
needs the signer to acknowledge it with a reason ([clinical-traceability.md](clinical-traceability.md)).

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

A couple screened for carriership on the long-read pipeline has no PED and no joint callset:
each partner has their own files (the NeedlR VCF in `sv/<sample>/needlr/` in later runs):

```text
COUPLE1/
  snv/MOTHER1/annotation/MOTHER1_annot.vcf.gz (+ .tbi)   and FATHER1's
  sv/MOTHER1/needlr/MOTHER1_sv_phased.needLR.4.0.vcf.gz (+ .tbi)
  repeats/MOTHER1/MOTHER1_tr.vcf.gz (+ .csi)
  paraphase/MOTHER1/MOTHER1.paraphase.json
```

How Discover reads such a folder and how its per-sample files are imported is under
[Per-sample callsets of a long-read couple](#per-sample-callsets-of-a-long-read-couple).

A PGT package from nf-cmgg/copgtm is laid out per tool, and holds the files Discover reads
for each dataset:

```text
FAM001/
  manifest.yaml
  ped/combined.ped                       the couple and the embryos
  dashboard/samplesheet.csv              the pipeline's input: each sample's role
  dashboard/pedigree.csv                 father, mother, index and embryo per row
  qdnaseq/SAMPLE_cnv.csv                 QDNAseq bins (copynumber) and segments (segmented)
  split_trio/filter_trio/EMBRYO1.trio_filtered.vcf.gz (+ .tbi)          APCAD
  apcad/EMBRYO1_pcf_{mat,pat}_data.csv   PCF segments, when the run writes them
  phasing/shapeit_filter/FAM001_shapeit_rephased_final.vcf.gz (+ .tbi)  haplotypes
  phasing/haplotype_origin/PARENT_affected_normal_haplotype.csv, PARENT_haplotype_conclusion.txt
  cram/SAMPLE.cram (+ .crai)
  mean/SAMPLE_coverage.csv, ngsbits/SAMPLE.tsv, qualimap/SAMPLE/genome_results.txt + qualimapReport.html
  picard/FAM001_ADO_ADI.csv, rtgtools/FAM001_{cohort,imputed}_concordance.csv, king/FAM001.kin0
  pipeline_info/params_*.json, copgtm_software_mqc_versions.yml
```

Its PED gives the embryos the sex ngs-bits read, so CoGA cannot tell them apart from the
couple's other children, and it lacks the index, the relative whose haplotypes tell the
affected parent's two haplotypes apart. Discover takes both from the samplesheet: each
embryo gets the embryo role under `family.members`, and an index the PED lacks is added
under `family.add_members` with the sex ngs-bits read. How the index is related is in none
of the pipeline's files, so Discover proposes a link from what KING measured against the
couple: the couple's child (the proband) when KING measures it first-degree to both
parents; otherwise a relative of unknown degree (`family.relationships.relatives`) of the
parent or parents KING measures it related to, or, when KING sees no relationship, of the
affected parent the run traced. The warning quotes KING and says which link it proposed;
the link can be changed before the manifest is written, or on the family page after the
import. Without a link the index's haplotype stays grey and is not used to find the risk
haplotype.

Discover also sets `roi` to the region of the newest `params_*.json`, records the parent the
run traced (`affected_parent`) under `metadata.pgt.affected_parents`, and the samplesheet's
index under `metadata.pgt.indexes`. The run does not record the inheritance model: set
`metadata.pgt.inheritance_model`, and the import records that parent and the index as
affected or as proven carriers, as the model asks (see the manifest below).
The embryo-only VCFs (`split_trio/extract_embryo/`), the pipeline's HTML plots and the
`dashboard/` copies are not imported. The PCF segmentation is read from its table only: a
run that draws it in `apcad/EMBRYO1_pcf_apcad_plot.html` alone gets no PCF track, and
Discover leaves the `pcf` dataset off.

A monogenic NIPT package from the NIPT-M pipeline holds one VCF per parent, each with one
sample: the maternal plasma's (cfDNA) and the father's, both called by Mutect2 in tumour-only
mode. A per-target coverage table per sample gives each capture target's depth:

```text
NIPTFAM1/
  manifest.yaml
  nipt_trio.ped              the father, the mother (her sample is the plasma) and the fetus
  CFDNA1.mutect2.vcf.gz      the plasma's calls
  FATHER1.mutect2.vcf.gz     the father's calls
  coverage_CFDNA1.txt        per-target coverage of the plasma
  coverage_FATHER1.txt       per-target coverage of the father
```

When Discover finds no joint SNV VCF, it looks for such a pair: a PED child without a VCF of
its own (the fetus) whose mother and father each have a one-sample VCF named after them,
`<sample>.vcf.gz` or `<sample>.<anything>.vcf.gz`, in the package folder or one folder down.
It takes the mother's sample as the maternal-plasma cfDNA and drafts a NIPT manifest:
`analysis_type: monogenic_nipt`, `assay: nipt_cfdna` on the plasma's sample, the two VCFs
under `datasets.snv.per_sample`, and the coverage tables it finds (`coverage_<sample>.txt` or
`.tsv`, in the same places) under `datasets.coverage.per_sample` as `target_table`. Its
warning (`nipt_pair_detected`) names the sample it took for the plasma and the one it took for
the father: check both before the import. How the pair is imported is under
[Monogenic NIPT pairs](#monogenic-nipt-pairs).

Discover knows several file names per dataset, and shows what it found. The exact patterns
are the `standard_v1` entry of `NAMING_SCHEMES` in
`backend/app/services/family_package_discovery.py`. A pattern may contain `*` (the SV
annotator puts its version in the file name); the saved manifest always holds the real path.
A long-read package counts a per-sample SNV or NeedlR file as the family's callset only when the
family has one sample; with two or more samples and no joint VCF, Discover proposes the samples'
files as one VCF per sample (`per_sample`), and names a sample that has none. For the long-read
pipeline's annotated SNV files it also sets `source_format: clair3` and, when the header
declares them, `exclude_filters: [RefCall, NoCall]`.

### The manifest

```yaml
schema_version: 1          # must be 1
family_id: FAM001          # defaults to the folder name
ped: family.ped            # optional when family.add_members names every member
analysis_type: monogenic_nipt   # optional
roi: CFTR                  # optional region of interest

metadata:
  pgt:
    inheritance_model: AR  # AD, AR, XLD, XLR or mitochondrial
    obligate_carriers: [FATHER]
    proven_carriers: [MOTHER]
    affected_parents: [MOTHER]   # the parent(s) whose condition the PGT tests for
    indexes: [INDEX1]            # the relative(s) the risk haplotype is read from

family:                    # optional: member states, extra members and relationships
  members:
    FATHER: {clinical_status: unaffected, carrier_status: carrier, carrier_type: proven}
  add_members:             # members the PED lacks, read as extra PED rows
    - {sample_id: INDEX1, sex: female, role: relative}
    - {sample_id: CHILD1, sex: male, father: FATHER, mother: MOTHER, role: proband}
  relationships:
    couples:
      - partners: [FATHER, MOTHER]
        context: reproductive
    parent_child:
      - child: PROBAND
        parents: [FATHER, MOTHER]
    relatives:             # related through a member by an unknown degree
      - member: INDEX1
        related_to: [MOTHER]

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

`metadata.pgt.affected_parents` names the parent whose condition the PGT tests for (the
PGT pipeline's `affected_parent`, which Discover copies), and gives that parent the status
the inheritance model asks: affected under AD and XLD, a proven carrier under AR, and under
XLR a proven carrier for a mother and affected for a father. The haplotype analysis reads
these statuses to find the risk haplotype. Mitochondrial inheritance gives no status. A
status recorded for the parent wins: under `family.members`, in the carrier lists, or in
the PED; where it contradicts the model, validation warns. Without an inheritance model the
parent keeps its status, and validation says the model is missing. Validation also lists
each status it derives, as a warning; a derived carrier status names the model in the
member's `carrier_evidence`. The family keeps the model, the carriers, the affected parents
and the indexes under its own `metadata.pgt`.

`metadata.pgt.indexes` names the index, the relative whose haplotypes the risk haplotype is
read from (the samplesheet's `index`, which Discover copies), and gives it the status the
model asks: affected under AD, XLD and AR (under a recessive model the index is the affected
child or relative), and under XLR affected if male and a proven carrier if female. Under XLR
an index of unrecorded sex gets none, as does every index under mitochondrial inheritance;
validation says so. A status recorded for the index wins, as for the parent, and also where
`family.add_members` adds it with a `clinical_status`. The index must be a member, and not
the affected parent.

`family.add_members` adds a member the PED lacks: it becomes one more PED row, with its
`role` (default `relative`), `sex` (default unknown), `clinical_status` (default unknown) and,
when given, its `father` and `mother`, so every check and the stored pedigree treat it like
the PED's members. A member already in the PED is an error; its states go under
`family.members`. Other links go under `family.relationships`, which may only name members.
A manifest without `ped` names all its members there: its added rows are then the whole PED
(the long-read couple's manifest, which Discover drafts).

`family.relationships.relatives` links a member to the family through another member by an
unknown degree: a PGT index known only to be on the mother's side, say, or related to both
parents. The pedigree draws the link as a dotted arc with a question mark, and the haplotype
track colours the member along the genome when it turns out to be the linked member's parent
or child: when it shares one of that member's haplotypes along nearly every chromosome. A more
distant relative is coloured across the ROI and 3 Mb on each side when the markers on both
sides show it carrying the same one of the linked member's haplotypes, and is grey elsewhere
(see [haplotype-segregation-analysis.md](haplotype-segregation-analysis.md)). A link of a
member to itself, or between two members the PED or the manifest already records as parent
and child or as a couple (the parents of a child are recorded as one), or that links the same pair
twice, is an error: the family editor would refuse the family's every later edit.

`analysis_type: monogenic_nipt` marks a monogenic NIPT family (see
[monogenic-nipt.md](monogenic-nipt.md)). Its `samples` entry for the maternal plasma says
`assay: nipt_cfdna` (without one, CoGA takes the mother's sample), and may name the capture
panel as `assay_panel`, which scopes the NIPT artifact list; both are copied into the sample's
metadata. A dataset can be switched off with `enabled: false`.
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
| `snv` | small variants (ClickHouse), with the VEP table if given; a monogenic NIPT pair's one-sample VCFs (`per_sample`) as one callset, source `nipt` ([below](#monogenic-nipt-pairs)); a long-read couple's one-sample VCFs (`per_sample`, `source_format: clair3`) as the primary callset, source `clair3` ([below](#per-sample-callsets-of-a-long-read-couple)) |
| `sv_needlr` | structural variants (ClickHouse), source `needlr`: one family VCF, or one VCF per sample (`per_sample`), each file's calls bound to its sample |
| `repeats_trgt` | repeat expansions (Postgres), scored against the TRGT catalogue |
| `wisecondorx`, `qdnaseq` | bins as the `coverage` track, segments as the `segments` track |
| `coverage` | the `coverage` track (a BED per sample, `bed:`), or the `target_coverage` track (a capture panel's per-target coverage table, `target_table:`, [below](#monogenic-nipt-pairs)) |
| `apcad` | the `apcad` track: per SNV the embryo's alternate-allele fraction, tagged with the parent the alternate allele can only have come from. From a family VCF, or one trio VCF per embryo (`vcf:` under `per_sample`) |
| `pcf` | the `apcad_pcf` track (maternal and paternal segment files) |
| `haplotypes` | a phased family VCF (GLIMPSE2, or SHAPEIT5 from the PGT pipeline) becomes small variants plus haplotype blocks, with a switch in a parent's phasing undone where the couple's children all switch together (kept in `families.metadata.haplotype_phase_corrections`); a per-sample BCF is only registered, not imported. The PGT pipeline's haplotype-origin files (`haplotype_origin`, `haplotype_conclusion`) are kept on the family as its reading of the affected haplotype; CoGA's own risk call does not use them |
| `paraphase` | Paraphase results (Postgres), shown on the family's Paraphase page |
| `cnv` (HiFiCNV) | structural variants, source `hificnv`; the depth bigWig as `coverage` and the copy-number bedGraph as `segments` (both stored as log2 ratios, like the other callers), and the MAF bigWig as `apcad` |
| `mito` | chrM small variants, source `mito`, annotated from the mutserve table, and each sample's mtDNA haplogroup, the one most of its annotated variants name; the chrM SV file (`sv_vcf`, Sniffles2 `--mosaic`) as structural variants, source `mito_sv`, with each sample's heteroplasmy (`INFO/VAF`) and the mtDNA genes it overlaps, shown on the mtDNA page |
| `qc` | the sample's sequencing QC, shown as a chip in the family members table: NanoPlot and mosdepth, or the PGT pipeline's mean coverage, Qualimap summary and report, and ngs-bits sex. The PGT pipeline's family tables (`ado_adi`, `concordance`, `imputed_concordance`) go onto each embryo, its KING table (`kinship`) onto the family |
| `alignments` | the CRAM/BAM location, used by IGV: the package-relative path and, for a package in a bucket, the object's URI |
| `pipeline_info` | tool versions in the annotation manifest; run parameters on the family (for the PGT pipeline also the affected parent, the ROI, the QDNAseq bin size, and the callset and phasing panel it started from) |

Each caller's rows carry their own source tag, so re-importing one callset never removes
another, and two callers' rows of one variant stay two rows in storage
([database.md](database.md#row-identity)). The `mito` dataset has one file per sample, and
each file replaces only that sample's calls. The other samples' mitochondrial calls stay as
they are, one row per variant with every sample's call, so the mtDNA analysis can set the
mother's calls beside the children's. A file without chrM variants removes that sample's calls.
The chrM SV files follow the same rule: one deletion called in the mother and the child is one
SV with both calls, and a sample's new SV file replaces only that sample's calls.

The track viewers draw one track per caller (`GET /families/{family_id}/track-availability`
lists them). The three HiFiCNV files are also served unchanged to the genome browser (IGV),
from the bucket for a package in a bucket.

### Monogenic NIPT pairs

A NIPT pair's manifest, as Discover drafts it (`assay_panel` added by hand):

```yaml
analysis_type: monogenic_nipt
samples:
  CFDNA1: {assay: nipt_cfdna, assay_panel: PANEL1}
datasets:
  snv:
    per_sample:
      CFDNA1: {vcf: CFDNA1.mutect2.vcf.gz}
      FATHER1: {vcf: FATHER1.mutect2.vcf.gz}
  coverage:
    per_sample:
      CFDNA1: {target_table: coverage_CFDNA1.txt}
      FATHER1: {target_table: coverage_FATHER1.txt}
```

The VCFs under `snv.per_sample` become one small-variant callset, source `nipt`
([family_package_nipt.py](../backend/app/services/family_package_nipt.py)):

- The plasma's file goes first, whatever the manifest's order: the sample tagged
  `assay: nipt_cfdna`, else the mother of the PED's child. Each other file is then merged into
  the stored rows for its own sample, as the `mito` files are: a variant is one row holding each
  sample's call where its file has one, and a file replaces only its own sample's calls. A
  sample without a call at a variant had no alt read there that its caller reported; the
  analysis reads its depth there from the sample's coverage table.
- The father's file is mostly low-level noise: nearly all of its calls sit below 20% alt
  reads. The importer keeps a paternal record when one of its calls reaches 15% alt reads
  (`NIPT_PATERNAL_KEEP_MIN_VAF`, below the 20% of a heterozygous call, so no genotype is lost;
  the alt reads over all the record's reads, or the caller's AF without allele depths), or
  when it lies on a position where the plasma has a call (an MNV over any of its bases). The
  dataset summary counts, per sample, the records it left out (`skipped_by_filter`).
- Each file holds one sample, so a record's FILTER values and caller metrics (Mutect2's TLOD,
  FS, mapping quality, mismatches, repeat units and so on) are that one call's: they are kept
  with the call ([database.md](database.md), `calls.filters` and `calls.metrics`) for the
  NIPT quality filter and the de novo triage.
- The `snv` settings `source_format`, `exclude_filters`, `vcf_sample` and `annotation_tsv` do
  not apply: the callset is always `nipt`, each file's one column is bound to the sample of its
  entry, and each file's annotation is read from its own records. With `update`, the dataset is
  skipped when the family already holds `nipt` calls.

A per-target coverage table (`target_table`) is tab-separated with a header line, one row
per capture target. It needs the columns `chromosome`, `start` and `end` (BED coordinates),
`attribute` (`GENE;transcript;…`: the gene comes first) and `mean`, and reads `median`, `min`,
`max`, `proportion_covered` and `zero_coverage_bases` when they are there; a number may have a
decimal comma. The table becomes the sample's `target_coverage` track, in place of the one
stored before ([database.md](database.md)); with `update`, a sample that has one is skipped.
The NIPT coverage check and the plasma's sex profile read the plasma's table, and the analysis
reads each sample's depth from its table where its VCF has no call
([monogenic-nipt.md](monogenic-nipt.md)).

One combined VCF with a father column and a plasma column, genotyped jointly, still imports as
an ordinary `snv` `family_vcf`, as the demo family does
([demo/nipt_family](../demo/nipt_family/README.md)); the analysis is the same. Without coverage
tables, the NIPT coverage check reads the plasma's `coverage` track (a BED) instead.

### Per-sample callsets of a long-read couple

The long-read pipeline calls and annotates each sample on its own and writes neither a PED
nor a joint callset for a couple screened for carriership. Such a folder is listed on the
Package Import page all the same: a folder counts as a package when it holds a manifest, a
PED, or the pipeline's per-sample folders (`snv/<sample>/`, `sv/<sample>/`, `repeats/<sample>/`,
`paraphase/<sample>/`, `cnv/<sample>/`, `mito/<sample>/`, each holding a file named after the
sample).

**Discover without a PED.** The members are those per-sample folders, named under
`family.add_members`, so the manifest needs no `ped`. A folder whose name cannot be a sample ID
(with a space or a control character) is listed too, and Discover refuses it
(`sample_id_invalid`) rather than leave a member out. Each member's sex is the karyotype TRGT
genotyped its repeats with (`--karyotype XX` or `XY` in the TRGT VCF's `##trgtCommand`): the
pipeline sets it from its samplesheet, so it is the recorded sex, which the Sample QC checks
against the reads. Two members of opposite sex are proposed as a couple
(`family.relationships.couples`, context `carrier screening`), the female partner with the
mother role and the male with the father role; their clinical status is left unknown. Any
other set of members gets no link, and a member without a TRGT VCF no sex. The warning
`ped_proposed_from_folders` says what was proposed: check it before writing the manifest.

**The SNV files are one callset**, the family's primary callset (source `clair3`), declared as
`snv.per_sample` with `source_format: clair3`
([per_sample_small_variants.py](../backend/app/services/per_sample_small_variants.py)):

- The files are read side by side, each sorted in the order of its `##contig` lines. A file
  whose contigs are listed in another order than the others' fails the dataset before
  anything is written. A record out of that order is found only as the files are read, once
  writing has begun (an `overwrite` has then already deleted the stored callset): it fails
  the dataset there, the rows already written are removed, and the family is put back or
  flagged as for any failed dataset ([The import job](#the-import-job)). Each file must hold
  one sample column, and that column must be the entry's sample (`per_sample_vcf_column`):
  the partner's file under an entry is refused.
- The records of one site (chromosome, position, REF and ALT) become one row holding the call
  of each sample whose file has a record there. Two records of one position whose alleles
  differ (`A>G`, `A>G,T`) are two rows; a multi-allelic record is not split.
- `exclude_filters` (DeepVariant's `RefCall` and `NoCall`) leaves out a site where every record
  is excluded. Where one partner has a variant, the other partner's excluded record is kept as
  their call: a `RefCall` (0/0) says the caller read them as reference, a `NoCall` (./.) that it
  could not. The dataset summary counts them (`excluded_calls_kept`).
- A partner whose file has no record at a site had no read evidence of the variant that the
  caller reported. The genotype filters (a genotype class including reference) and the Sample QC
  read it as reference, as a joint VCF calls a covered site; a site without reads is read the
  same way, which the Sample QC notes.
- Each record's QUAL, FILTER and caller metrics are the call's own (`calls.filters`,
  `calls.metrics`). The row's FILTER holds every record's; its annotation is the first file's
  with a kept record there.
- The callset replaces the family's primary callset, joint or per-sample: the import must bring
  the file of every sample with calls in it, else it is refused before anything is deleted
  (*"The family's callset also holds the calls of …"*). With `update` the dataset is skipped
  when the family holds a primary callset.

A monogenic NIPT pair's per-sample files are read differently ([below](#monogenic-nipt-pairs)):
without `analysis_type: monogenic_nipt` and without `source_format: clair3`, a per-sample SNV
callset is refused (`dataset_per_sample_unsupported`), so a NIPT pair whose analysis type was
left out is never read as germline calls.

**The NeedlR files** go under `sv_needlr.per_sample`. NeedlR has no sample column; each record
names its query in `Query_ID`, after the caller's input (`<sample>_sv_phased`). A file whose query
names another sample is refused before anything is written; a query naming no known sample is
the entry's. One SV called in both partners with the same alleles is one SV with both calls.

### Validation

Discover, dry run and import check that:

- the folder and the manifest exist, and `schema_version` is 1;
- the family ID (the manifest's `family_id`, else the folder name; for Discover, the request's
  `family_id` first) and every sample ID (in the PED, under `family.add_members` as a member or
  a parent, a long-read package's per-sample folder) is printable text without spaces. The
  whitespace around an ID is stripped. An ID that holds a control character (C0, `\x00`–`\x1f`:
  a line break, a tab, an escape; or DEL, `\x7f`) or whitespace is refused (`family_id_invalid`,
  `sample_id_invalid`), and nothing else of the package is read until it is corrected: Discover
  drafts no manifest. Such an ID would reach the pedigree, the report, the audit trail, the logs
  and file paths, and a NUL cannot be stored at all. The message writes the character as an
  escape (`\n`, `\x1b`) and says where the ID comes from
  ([family_identifiers.py](../backend/app/services/family_identifiers.py));
- the PED parses as six-column PED, holds one family and matches `family_id`; sample IDs are
  unique and every parent is in the PED. For an import into an existing family the PED file
  may be missing: the family's stored pedigree is used. A manifest without `ped` must name its
  members under `family.add_members` (`ped_missing_path`);
- the manifest's samples and per-sample entries name samples in the PED or in
  `family.add_members`, an added member is not already in the PED, and every
  `family.relationships` entry names members;
- a phenotype file named in the manifest exists and uses `format: hpo_tsv`;
- every file the manifest names exists (for a package in a bucket, an alignment exists in the
  bucket), and a compressed family VCF has its index (`.tbi`, `.csi` or `.idx`, next to it or
  given as `index:`); a plain `.vcf` needs none;
- a per-sample SNV callset (`snv.per_sample` without `family_vcf`) is read for a family with
  `analysis_type: monogenic_nipt`, or as the primary callset of germline calls made one sample at
  a time when the dataset says `source_format: clair3` (`dataset_per_sample_unsupported`
  otherwise). Each of its VCFs holds exactly one sample (`dataset_vcf_not_single_sample`) and
  needs no index;
- a per-target coverage table's header names `chromosome`, `start`, `end`, `attribute` and
  `mean` (`coverage_target_table_columns`);
- every dataset key is a known one.

A missing `snv` or `sv_needlr` dataset is a warning; other datasets are optional without a
warning. A bad phenotype row (an unknown person, HPO ID or status) is a warning: the row is
skipped and never blocks the import.

The API applies the control-character half of the ID rule to every request that looks an ID
up. A family or sample ID in a request's path or in its `family_id` or `sample_id` query
parameter that holds a control character is refused with 400 once the caller is signed in,
before any lookup, with the same answer for every caller; so is any other path parameter
with one, and a NUL in any query value. A `%00` in a URL arrives as a NUL, and Postgres cannot
compare one ([security-posture.md](security-posture.md#1-authentication--rbac)).

## 5. Pedigrees and phenotypes

- `POST /ped/manual`: any signed-in user creates a family from typed-in members.
- `POST /ped/upload`: admins upload a PED file, with an optional region of interest,
  inheritance model and carrier lists.
- Only admins can replace an existing family or sample (`overwrite=true`).
- Both refuse (400), before anything is written, a family or sample ID (a PED's parent IDs
  too) that is not printable text without spaces, as a package import does
  ([Validation](#validation)); the whitespace around a typed-in ID is stripped.
- The edits of an existing family refuse such an ID the same way: a member's new sample ID,
  the father or mother a member edit names, and a member the structure edit adds
  ([family-member-management.md](family-member-management.md)).

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

A file stored as one sample's -- a TRGT upload, and a package's per-sample `repeats_trgt`,
`mito` (its chrM VCF and its chrM SV VCF) and `cnv` (HiFiCNV) files -- is read from that
sample's own column (`per_sample_vcf_column`; a `mito` dataset checks all its files before it
writes any):

- the column the shared sample-name rules resolve to the sample (`<sample>_sort`,
  `<sample>_sv_phased`) is read, at any position, so a family TRGT or chrM VCF can serve each
  member in turn;
- a single column that names no stored sample (a caller's placeholder, such as HiFiCNV's
  `Sample0`) belongs to the sample;
- a file whose columns name other samples and none this one (a family member, or a sample
  of another family: sample ids are unique) is refused before anything is written, as is a
  file with two columns for it: the upload with 400, a package dataset as failed;
- a package entry's `vcf_sample` is the operator's recorded word: the column it names is
  read, or, set to the entry's own sample, the file's one column. A lab that verified its
  tubes and maps a sample to a file named after another tube sets it.

Each stored TRGT call records its column (`metadata.vcf_sample`).

`bed_type` is `coverage`, `segments`, `apcad` or `apcad_pcf`. Haplotype blocks cannot be
uploaded as a BED; they come from a GLIMPSE2 small-variant upload (`source_format=glimpse2`)
or package.

A family small-variant upload is one family VCF from one callset:

- `source_format` is `clair3` (a directly called callset), `glimpse2` (imputed genotypes, which
  also make the haplotype blocks), `mito` (chrM calls) or `auto`, the default, which tells
  `clair3` and `glimpse2` apart from the first record. Any other value is refused (422) before
  anything is stored. Only Package Import writes the `nipt` callset of a monogenic NIPT pair,
  one file per sample ([Monogenic NIPT pairs](#monogenic-nipt-pairs)); a combined father and
  plasma VCF uploads as an ordinary callset.
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

### The NIPT artifact list

The recurrent-artifact list of monogenic NIPT is kept per assembly and assay
(`nipt_artifact_variants`; see [monogenic-nipt.md](monogenic-nipt.md)). Admins maintain it
through the API; there is no screen for it. Besides adding or removing one allele
(`POST /admin/nipt/artifacts`, `DELETE /admin/nipt/artifacts/{artifact_id}`) and seeding it
from the cohort's cfDNA samples (`POST /admin/nipt/artifacts/auto-seed`), an admin can import
the NIPT-M pipeline's recurrent table with `POST /admin/nipt/artifacts/import`: a multipart
form with the table as `file`, the `assembly_id`, and the `assay_key` (default `nipt_cfdna`;
a list for a capture panel takes the panel's key, the `assay_panel` of its cfDNA samples).

- The table is tab-separated with a header line. It needs a `variant_key` column
  (`CHROM:POS:REF:ALT`) or the columns `CHROM`, `POS`, `REF` and `ALT`; without them it is
  refused (400). With a `filter_as_recurrent_artifact` column, as the pipeline's
  `recurrent_cfdna_artifact_filter_*.tsv` has, only the rows it marks TRUE are read; without
  it, every row is an artifact.
- Each allele is listed as `curated`, with `n_cfdna_families` as its recurrence count and the
  label `recurrent (<recurrent_filter_profile>)`. An allele already on the list keeps its
  source and label and takes the table's count.
- The auto-seed's protections hold. An allele whose annotation on the assembly has a gnomAD
  or TopMed frequency above 5%, or a ClinVar record that may assert pathogenic (pathogenic,
  likely pathogenic or conflicting), is not listed. An allele that no family on the assembly
  carries has no annotation there to check, and is listed. Each family's analysis checks its
  listed alleles again with the family's own annotation, and keeps a common or
  ClinVar-pathogenic one, flagged `artifact_list_protected`. An allele without any annotation is
  not protected: review the list.
- The answer counts the rows read, those the table does not flag (`not_flagged`), those it
  could not read (`invalid`, such as a multi-allelic `ALT`), the alleles listed (`imported`),
  and those refused as common (`protected_common`) or for ClinVar (`protected_clinvar`).
- An import that lists alleles is one clinical audit event, `nipt_artifacts_imported`, on the
  artifact list's own chain: it names the file and every allele it listed
  ([clinical-traceability.md](clinical-traceability.md)). An import that lists none records
  no event.

## 7. Troubleshooting

| Message or symptom | Cause |
| --- | --- |
| `package_folder_not_allowed` | The folder is not under `FAMILY_IMPORT_ROOTS`. |
| `family_id_invalid` | The family ID holds a control character or whitespace, or is empty. The message says where it comes from (the manifest's `family_id`, the folder name, the request's `family_id`, the PED): correct it there. |
| `sample_id_invalid` | A sample ID holds a control character or whitespace. The message says where it comes from (the PED, `family.add_members`, a per-sample folder's name, the existing family): correct it there. |
| `manifest_missing` | The folder has no `manifest.yaml`, `.yml` or `.json`. Run Discover and Write manifest (local folders only). |
| `manifest_schema_version_unsupported` | `schema_version` is not 1. |
| `ped_family_mismatch`, `ped_multiple_families` | The PED's family ID differs from `family_id`, or it holds more than one family. |
| `dataset_vcf_index_missing` | A compressed VCF has no `.tbi`, `.csi` or `.idx`. |
| `manifest_added_member_in_ped` | A member under `family.add_members` is in the PED already; set its states under `family.members`. |
| `manifest_relationship_unknown_member` | A `family.relationships` entry names someone who is neither in the PED nor in `family.add_members`. |
| "Sample '…' not found in family" (haplotypes) | The phased VCF has a sample the family lacks, such as a PGT index the PED does not hold; add it under `family.add_members`. |
| `existing_family_or_samples` | The family or a sample exists; choose `update` or `overwrite`. |
| "sample column(s) … match no sample in the family" | Set `vcf_sample` on the dataset. |
| "… has no sample column for X: '…' is Y" | A per-sample file names another sample: point the entry at X's own file, or, if the file is X's after all, set `vcf_sample` on X's entry. |
| `nipt_pair_detected` (warning) | Discover took the folder for a monogenic NIPT pair. Check the sample it took for the maternal plasma and the one it took for the father. |
| `dataset_per_sample_unsupported` | `snv.per_sample` in a family that is not `analysis_type: monogenic_nipt` and does not say `source_format: clair3`; for the long-read pipeline's per-sample calls set `source_format: clair3`, else give a joint VCF as `snv.family_vcf`. |
| `ped_missing_path` | The manifest names no PED and no members under `family.add_members`. |
| `ped_proposed_from_folders` (warning) | Discover found no PED and took the members from the long-read per-sample folders (a couple when they are of opposite sex). Check them before writing the manifest. |
| "The family's callset also holds the calls of …" | A per-sample SNV import that does not bring every sample's file would drop the others' calls: import every sample's SNV file together. |
| `dataset_vcf_not_single_sample` | A VCF under `snv.per_sample` (a NIPT pair's, or a long-read sample's) holds no sample column or more than one. |
| `coverage_target_table_columns` | A per-target coverage table's header lacks `chromosome`, `start`, `end`, `attribute` or `mean`. |
| A per-target coverage import fails on `sample_interval_track_sources_track_type_check` | The Postgres database is older than the `target_coverage` track type; reset it ([database.md](database.md)). |
| Empty viewers or gene search | The assembly's reference data is missing (section 1). |
| dbNSFP `missing` on every gene | The dbNSFP gene file is not at `GENE_REFERENCE_DBNSFP_GENE_PATH`. |

## 8. Demo data and helper scripts

Synthetic demo families and their loader are described in [demo/README.md](../demo/README.md);
the helper scripts are listed in [scripts/README.md](../scripts/README.md).
