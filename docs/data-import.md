# Data import

The reference for admins and developers on what CoGA loads and in which format: reference
data, the gene reference, family folder packages and single-file uploads. The step-by-step use
of the Package Import page, and the common mistakes, are in the in-app guide
([Data import](../frontend/src/content/docs/data-import.md), at `/docs/reference/data-import`).

A family gets into CoGA in one of two ways:

- **Family builder** (`/family-builder`): a pedigree typed in by hand, metadata only. Its
  request may mark the family as monogenic NIPT (`analysis_type: monogenic_nipt`) and a sample
  as the mother's cfDNA (`assay: nipt_cfdna`), and nothing else. The keys an import or an
  audited edit writes (the annotation manifest, a sample's sequencing QC, the import state) are
  refused with 400 (SAFE-12).
- **Package import** (`/package-import`, admins): one folder with the PED, the assay files and
  a manifest. One job imports the family and all its data.

Loading data files, replacing an existing family and every reference-data upload are
admin-only. All API paths below sit under `/api`. Every setting named here is listed with its
default in [.env.example](../.env.example).

For a new family: check that its assembly has its reference data (section 1), make sure its
project exists (**Admin → Projects & access**), then import the package (section 3).

## 1. Reference data

Genes, cytobands and the annotation layers belong to one genome assembly. An empty viewer, or a
gene search that finds nothing, usually means the assembly's reference data is missing.

### What startup loads

Each start fills in what is missing. A table that already holds rows for the assembly is left
alone.

| What | Where it comes from | Settings |
| --- | --- | --- |
| Homo sapiens / GRCh38 | Created if absent | `REFERENCE_BOOTSTRAP_ENABLED` |
| GRCh38 cytobands | UCSC `hg38` download (`cytoBandIdeo`, else `cytoBand`). Without UCSC, GRCh38 has none, and each later start tries again | |
| GRCh38 gene loci | GENCODE GTF (see below); the UCSC gene table if GENCODE fails | `REFERENCE_GENCODE_GTF_URL`, `REFERENCE_GENCODE_REFSEQ_METADATA_URL` |
| T2T-CHM13v2.0 (off by default) | UCSC `hs1` RefSeq GTF. UCSC has no `hs1` cytoband table, so each chromosome gets one band spanning it, from UCSC's chromosome sizes; without UCSC, none | `REFERENCE_BOOTSTRAP_T2T`, `REFERENCE_T2T_GTF_URL` |
| Segmental duplications / LCRs | The ClinGen recurrent-CNV BED in `data/ref-data/`: ClinGen's V2.1 GRCh38 file, unchanged ([checked against ClinGen by SHA-256](../data/ref-data/clinical_cnv_syndromes_hg38_bundle/COGA-PROVENANCE.md)). The file read (the setting's, else the repository's) must be that file byte for byte, SHA-256 `e6a7428f58b0…` (`SEGMENTAL_DUPLICATIONS_PINNED_SHA256`; no setting overrides it). Another file is not loaded: the log names it, both digests and the fix (copy the repository's file there unchanged), and the start goes on without the track. GRCh38 only (below) | `REFERENCE_SEGMENTAL_DUPLICATIONS_PATH`, `REFERENCE_BOOTSTRAP_ASSEMBLY_NAME` |
| Clinical CNV syndromes | Not loaded on startup: the knowledgebase rebuild loads them (below). The removed `REFERENCE_CLINICAL_CNVS_PATH`, read unpinned and whose default file was never shipped, is ignored if still set (OPS-17) | |
| HPO ontology | `data/ref-data/hpo/hp.obo`, imported only if its SHA-256 is the vendored file's. Another file in place (another release, say) is refused and logged (the file, both digests, the fix), and nothing is imported or downloaded instead. With no ontology file, the same release is downloaded over HTTPS from its versioned URL, under the same check. URL and SHA-256 are fixed in code ([the pin](../data/ref-data/hpo/README.md#the-download-fallback)): the removed `HPO_ONTOLOGY_URL` and `HPO_ONTOLOGY_SHA256` are ignored if still set | `HPO_BOOTSTRAP_ON_STARTUP`, `HPO_ONTOLOGY_PATH`, `HPO_DOWNLOAD_IF_MISSING` |
| TRGT repeat catalogue | The built-in loci (`repeat_expansion_catalog.py`) and the shipped STRchive release, `data/ref-data/STRchive-loci.json`, both refreshed on every start; any other STRchive file stops the start ([The STRchive release](#the-strchive-release)). A built-in locus carries its STRchive row's thresholds, so a call classifies the same whether its TRID is the gene name or STRchive's locus ID; for ARX and HOXA13 the call's region picks the locus. A call whose TRID no row names, or whose row states no intermediate, premutation or pathogenic threshold (STRchive's MUC1 and POLG), is not assessed (`not_assessed`), never normal, except a count inside the benign range such a row states (POLG: exactly 10; MUC1 states none). TRGT counts the catalogue motif across the locus, flanking copies included: in the TRGT catalogue in use (TRID `CPEO_POLG`, chr15:89333579-89333629, motifs GCT and GTT), POLG's common allele of 10 repeats counts 14 and stays not assessed. In a row without a grey zone of its own (24 of STRchive's 75 loci, ABCD3 and PRTS_ARX among them), a count between the benign range and the pathogenic threshold is grey zone (`intermediate`, where it read normal; the owner's decision of 2026-10-27, CLIN-14). The repeat page and the genome tracks both classify imported calls against the current catalogue (CLIN-14) | `TRGT_STRCHIVE_LOCI_PATH` |
| First gene reference sync | Queued only when the dbNSFP gene file is present and no gene has been synced yet | `GENE_REFERENCE_BOOTSTRAP_ON_STARTUP`, `GENE_REFERENCE_DBNSFP_GENE_PATH` |

**TRGT's counting units.** TRGT counts the motif across the region its own catalogue gives a
locus. That need not be the stretch the clinical repeat number, and so the catalogue's ranges,
count. Checked in the public HG002 TRGT output and the laboratory's long-read files:

- FMR1: TRGT counts the CGG copies only, not the AGG interruptions, so an allele of about 31
  triplets reads 29 (1 to 3 below; a premutation of 55 can read 53, in the grey zone).
- HTT: it also counts the CAG after the CAA interruption, so HG002's pure 17-repeat allele
  reads 18.
- POLG: it counts four flanking GCT copies, so the common 10-repeat allele reads 14.

QA and the laboratory director check per catalogue locus that TRGT's count is the clinical
repeat number. An administrator records the result on the admin page **Repeat catalogue**
(`repeat_locus_count_units`, which no start rewrites; [database.md](database.md)):

- a count correction, in repeats, added to TRGT's count before the count is classified (−1 turns
  TRGT's 18 into 17). It applies at import, on the repeat page, in the genome tracks and in the
  signed record, validated or not. TRGT's own count stays shown beside the corrected one;
- whether the locus is validated: TRGT's count, with its correction, is the clinical repeat
  number. Until it is, the repeat page and the genome tracks mark a count within 3 repeats of a
  boundary its classification uses (`services/repeat_count_units.py`); the mark changes no status.

No correction is recorded in advance, for HTT, POLG or any other locus. FMR1 has no constant
correction (its count is 1 to 3 low, by the number of AGG interruptions), so it stays at 0 and
not validated.

Segmental duplications load only into the assembly `REFERENCE_BOOTSTRAP_ASSEMBLY_NAME` names
(GRCh38), and only if it is GRCh38, as the shipped file holds GRCh38 coordinates. An assembly is
GRCh38 when its name is GRCh38 or UCSC's hg38, in any case, with or without a patch suffix such
as `.p14` (`grc_build` in `services/sex_chromosomes.py`, the rule the PAR lookup uses). For
another assembly (T2T-CHM13, GRCh37) the start loads nothing and logs one warning naming the
assembly and the fix: upload that assembly's own file on **Admin → Species & assemblies**
(OPS-17). Without UCSC, startup still creates the empty GRCh38 assembly and carries on.

A start loads no clinical CNVs, so a fresh install has none, without a warning. Build them with the clinical CNV knowledgebase rebuild (↻ next to the clinical-CNV count on
**Admin → Species & assemblies**; `POST /admin/clinical-cnv-kb/rebuild`), or upload a file
(below). A rebuild that cannot load the ClinGen dosage curation or the recurrent CNV regions
fails, says why on that page, and leaves the clinical CNVs as they were. So does a rebuild whose
administrator is no longer an active administrator (deactivated, given another role or deleted;
SEC-35): it keeps that account by its id and checks it as it starts and again before the load,
as the build runs for up to two hours before it.

ClinGen's recurrent CNV file is pinned per assembly by its SHA-256
(`CLINGEN_RECURRENT_CNV_SHA256` in `scripts/clinical_cnv_knowledgebase.py`). The rebuild fails
on any other file, naming it, both digests and the fix:

- GRCh38: ClinGen's V2.1 file, which the repository ships as the segmental-duplication file
  (`e6a7428f58b0…`).
- GRCh37: ClinGen's V2.1 GRCh37 file, downloaded from ftp.clinicalgenome.org on 2026-10-27 with
  the owner's permission (`92f331fe6dd2…`, 16,772 bytes; TRACE-18).

Read as published on the day of the rebuild, unpinned: the ClinGen dosage curation, the UCSC
cytobands, ClinVar's variant summary, Orphanet's nomenclature and a DECIPHER map. The build
records each file it read into the knowledgebase by SHA-256 and size (`recorded_files`; a local
file by its name only; TRACE-18). Not recorded: a
file it could not read (an Orphanet download that does not parse, say) and OMIM's API answers
(only with `OMIM_API_KEY`). The rebuild's import record names the files as its release, every
digest in full, and the signed analysis record freezes it: first the pinned file
(`ClinGen recurrent CNV V2.1: ClinGen_recurrent_CNV_V2.1-hg38.bed, SHA-256 e6a7428f…`), then
`read as published: ClinGen dosage curation: …, SHA-256 …; UCSC cytobands: cytoBand.txt.gz,
SHA-256 …`. A build that does not record each file it read, with its source, URL and full
SHA-256, is not loaded.

**Gene loci.** GRCh38 gene loci come from the GENCODE basic annotation, pinned by the release in
`REFERENCE_GENCODE_GTF_URL`. The variant pipeline uses the same annotation, so the coordinates
agree. Each row is one transcript, with its biotype, Ensembl and HGNC identifiers, MANE tags and
RefSeq accessions (from GENCODE's `metadata.RefSeq` file). The import records its release (for
example `gencode v50 (Ensembl 116)`). If GENCODE cannot be fetched, the UCSC gene table is used:
the import result and the log carry a warning, and the import record names the UCSC table. The
report's annotation manifest shows the source as `gene_loci`. For a newer GENCODE release,
change both GENCODE URLs together and re-import the genes with overwrite.

**T2T-CHM13v2.0** is an optional second assembly (`REFERENCE_BOOTSTRAP_T2T=true`), outside the
validated scope (GRCh38 only). It roughly doubles the reference data; the gene page's T2T rows
stay empty until it is on. GENCODE publishes no T2T annotation, so the genes come from UCSC's
RefSeq GTF (labelled `ucsc ncbiRefSeq`): coordinates, but no biotypes, Ensembl identifiers or
MANE tags, and one cytoband per chromosome.

The import names the assembly as UCSC's description does, `T2T CHM13v2.0` with a space (`hs1`
where the description gives no name). Its ClickHouse tables are keyed `T2T_CHM13v2.0`, and NCBI
writes `T2T-CHM13v2.0`. CoGA reads T2T-CHM13v2.0 under all these names, in any case:
`T2T-CHM13`, `T2T CHM13` or `T2T_CHM13`, with or without `v2.0`, and `CHM13v2.0` and `hs1`
(`is_t2t_chm13` in `services/sex_chromosomes.py`; `isT2tChm13` in the frontend's
`lib/reference.ts`). Another CHM13 release (v1.0, v1.1) or telomere-to-telomere genome is not:
`hs1` is v2.0. By this rule IGV opens the `hs1` genome for a T2T family, the gene page links to
UCSC's `hs1`, a gene's loci and a panel's regions list T2T after GRCh38, and a repeat call is
placed by STRchive's T2T coordinates (most read only `T2T-CHM13…` before). IGV opens no genome
for an assembly it has none for, and says so.

GRCh38 and GRCh37 follow one rule too, `grc_build` (`grcBuild` in the frontend): GRCh38 or hg38,
GRCh37 or hg19, in any case, with or without a patch suffix such as `.p14`. The UCSC import names
an assembly `hg38` where UCSC's description gives no name. The gene page links GRCh38 to UCSC's
`hg38` and gnomAD v4, and GRCh37 to `hg19` and gnomAD v2.1. The variant list's UCSC and gnomAD
links and the gene page's hg38 locus read the assembly's name, else its version, each as a
whole. A name the rules do not read, such as `GRCh38_no_alt`, gets no UCSC link, no gnomAD
release and no IGV genome. A gene's loci, a panel's regions and the panel list's sizes are
ordered GRCh38, T2T-CHM13, GRCh37, then any other assembly by name (`assembly_display_rank` in
`services/sex_chromosomes.py`; `compareAssemblyNames` in the frontend); the gene page opens on
the first that has the gene. (These used to match exact names only, so `GRCh38.p14` had no links
and came last.)

### Loading or replacing reference data

Admins manage reference data on **Admin → Species & assemblies**
(`/admin/reference/assemblies`; everyone can view it at `/reference-data`):

- **Add organism / assembly (from UCSC)** downloads cytobands and genes for any UCSC genome
  (`POST /assemblies/reference-import`). Human GRCh38 and T2T genes come from their GTFs as
  above; other genomes use a UCSC gene table.
- **Upload** loads one file into one assembly
  (`POST /assemblies/{assembly_id}/reference-upload/{dataset_type}`). If the assembly already
  holds that dataset, the upload is refused unless `overwrite=true`, which replaces it.

Every import and upload is recorded with who ran it, the source, and the release the source
states: for a GENCODE import its version and date, for a clinical CNV knowledgebase rebuild the
files it read (above), for every other import `not stated` (see [database.md](database.md)).
The imports show under "Recent reference activity".

| `dataset_type` | File format |
| --- | --- |
| `cytobands` | Tab-separated: chrom, start, end, band, stain (UCSC cytoband format) |
| `genes` | The 12-column tab-separated transcript file that `scripts/gtf_to_ccds_gene_bed.py` writes (with exon and intron columns) |
| `blacklist` | BED-like: chrom, start, end, label |
| `clinical_cnvs` | UCSC bedDetail-style (at least 11 columns), or a TSV with a header such as the knowledgebase output |
| `segmental_duplications` | BED-like intervals; the ClinGen recurrent-CNV BED is supported |
| `dgv` | The Database of Genomic Variants TSV (variantaccession, chr, start, end, varianttype, variantsubtype, …) |

The full DGV file (about 2 million rows) is too large for the upload. Load it inside the backend
container with `scripts/import_dgv.py`, which inserts in batches (see
[scripts/README.md](../scripts/README.md)).

### Updating the HPO ontology

To move to a newer HPO release, put the new `hp.obo` in `/data/ref-data/hpo/` and run the sync on
**Admin → HPO terminology** (`POST /admin/hpo/sync`). The page starts from the file the backend
is configured to load (`HPO_ONTOLOGY_PATH`). The sync previews the changes first, and reads only
files inside the configured ontology folders.

- The release is the one the file states about itself (its `data-version`). The terms keep the
  SHA-256 of the imported bytes (`metadata.release_sha256`), which the admin page, the loaded
  release and every signed record's HPO entry name (TRACE-18; the owner's choice of 2026-10-27).
- The page's *Expected release* and *Expected release date* are what the admin expects the file
  to be: the whole release (`hp/releases/2026-02-16`) or its last part (`2026-02-16`). A file
  that states another release, or none, is refused (400) and nothing is imported. (A typed
  release used to replace the file's own, unchecked, and no checksum was kept.)
- The startup import, of a file in place or downloaded, stays pinned to the vendored release: on
  a database without HPO terms, a start refuses any other file. Moving the pin and the vendored
  file is a code change
  ([data/ref-data/hpo/README.md](../data/ref-data/hpo/README.md#updating-controlled-change)).

### The STRchive release

The repeat catalogue is built from one STRchive release: the one shipped as
`data/ref-data/STRchive-loci.json` and validated with the built-in loci, which carry its
thresholds. That is STRchive release **v2.18.0** (2026-04-28), `STRCHIVE_PINNED_RELEASE`. The
file is byte-identical to the `STRchive-loci-v2.18.0.json` STRchive published with that release
(found on 2026-10-20 by comparing the SHA-256 with the loci file of every published release).
The file states no release of its own, so its SHA-256 identifies it: `a0295e1fe3e2…` (75 loci),
held in full as `STRCHIVE_PINNED_LOCI_SHA256` in `backend/app/core/config.py`. No setting
overrides the pin, and `.gitattributes` keeps git from changing the file's line endings in a
checkout.

Every start reads the file `TRGT_STRCHIVE_LOCI_PATH` names (`/data/ref-data/STRchive-loci.json`;
on Cloud Run the reference-data bucket), or the repository's copy when that file does not exist.
If the file read is not the shipped release byte for byte, or there is no file at all, the
backend does not start. It writes nothing, and logs one error naming the file, both SHA-256
digests and the fix: copy the repository's `data/ref-data/STRchive-loci.json` there unchanged.
An identical copy is accepted wherever it lies; a copy with the same loci in other text (written
out again by another tool) is not. A start also removes the STRchive rows of `repeat_loci` that
the shipped release does not list (another release's loci an earlier start seeded, say), so no
TRID can find their thresholds.

A TRGT call's TRID chooses the catalogue row that classifies it: STRchive's locus ID
(`PRTS_ARX`), another identifier STRchive records for the locus (STRipy's `ARX_2`), or the gene's
name. A gene with several STRchive loci has a repeat tract per locus, each with its own
thresholds: ARX (`EIEE1_ARX`, `PRTS_ARX`) and HOXA13 (`HFG_HOXA13-I`, `-II`, `-III`). Its name
picks the locus whose STRchive coordinates on the family's assembly (GRCh38, GRCh37 or
T2T-CHM13) overlap the call's region. A region that overlaps none of them or more than one, or
an assembly STRchive gives no coordinates on, leaves the call without thresholds: it reads *not
assessed*, never another tract's status, and the call's note on the repeat page says why. Any
other name several STRchive loci share, such as a tag, is resolved the same way.

Moving to another STRchive release is a reviewed change, made in one pull request:

1. replace `data/ref-data/STRchive-loci.json` with the new release's file, byte for byte as
   published;
2. set `STRCHIVE_PINNED_LOCI_SHA256` to its digest (`shasum -a 256`), and give the shortened
   digest here and in TF-08;
3. give the built-in loci the new release's thresholds
   (`test_repeat_thresholds_follow_strchive.py` fails until they agree);
4. update the expected loci of the repeat tests.

A deployment then needs the new file in its reference data before the new version starts, or
the new version does not start. From the release candidate on, the change is also recorded in
`CHANGELOG.md` and TF-18 §8.

## 2. Gene reference sync

The gene reference is the cached information per human gene (`gene_info`): names and
identifiers, disease links, dosage, constraint, expression and more. An admin refreshes it from
**Admin → Species & assemblies** (↻ next to the gene count) or the gene reference page
(`/admin/reference/gene-reference`; `POST /admin/gene-reference/refresh-all`). The refresh runs
as a job that keeps the requesting administrator's account by its id. A worker runs it only
while that account is an active administrator (SEC-35): deactivated, given another role or
deleted by then, the job ends failed, says why and writes no gene record, also when it is taken
up again after its worker stopped. The refresh a start queues on an empty cache
(`startup-bootstrap`) belongs to no account, and runs.

HGNC decides which human genes exist: the sync takes the HGNC complete set as its gene list. A
symbol HGNC renamed moves onto the current symbol and keeps its locus. A gene HGNC knows but the
assembly does not place still gets a record, without coordinates. A historic symbol claimed by
two genes is dropped, not guessed.

Every source below is consulted for every gene:

| Source | Contributes |
| --- | --- |
| HGNC complete set | identity: HGNC ID, current, previous and alias symbols, Ensembl, Entrez, RefSeq, CCDS, UniProt and MANE ids, locus group, band |
| dbNSFP gene file (`GENE_REFERENCE_DBNSFP_GENE_PATH`) | constraint metrics, HPO/GO/pathway terms, OMIM/Orphanet/GenCC disease context, model organisms, tissue expression |
| ClinGen gene validity and dosage | gene-disease validity, haploinsufficiency and triplosensitivity |
| GenCC | submitted gene-disease assertions and their classifications |
| ClinVar `gene_condition_source_id` | gene-disease relationships and OMIM disease links |
| ACMG SF list (`data/ref-data/acmg-sf/`, shipped with CoGA) | whether the gene is on the ACMG secondary-findings list, with the phenotype, category, inheritance, variants to report and footnotes the list gives it |
| NCBI Gene (one lookup per gene) | a gene summary for genes that are not in dbNSFP |

Each gene stores a status per source:

- `success`: the source had a record;
- `missing`: asked, it had nothing;
- `not_consulted`: never asked, so it says nothing about the source;
- `error`: the download or parse failed.

Each status also records which release answered: `release` (the version the file states, if
any), `release_detail.checksum` (the sha256 of the parsed bytes), `release_detail.size_bytes` and
`fetched_at`. The checksum always exists, so two caches have the same provenance exactly when
their checksums match. The admin page flags a source whose cached genes came from more than one
release, the sign of a partial refresh; a full sync fixes it.

The pinned release is **dbNSFP 5.4** (August 2026, GENCODE 50 / Ensembl 116). The gene table ships
inside the full dbNSFP archive from <https://www.dbnsfp.org/download> (registration required);
place its gene file at the configured path, renamed to `dbNSFP5.4_gene.gz`. To move to a newer
dbNSFP release: put the new gene file next to (or over) the old one, update
`GENE_REFERENCE_DBNSFP_GENE_PATH` and the defaults that name the file, and run a gene reference
sync. Cached `gene_info` rows are **not** refreshed automatically: they keep the values of the
release they were synced with. Columns are read by header name, so new or reordered dbNSFP gene
columns are safe; a renamed or removed column silently empties that field.

Until the file is in place, dbNSFP reports `missing` for every gene and the other sources still
answer. Its checksum is taken over the file as it sits on disk, so `sha256sum` on the deployed
file reproduces it.

**The ACMG SF list** is **ACMG SF v3.3** (*Genet Med* 2025), vendored as
`data/ref-data/acmg-sf/acmg-sf-genes.json`. Its [README](../data/ref-data/acmg-sf/README.md)
names the publication, how the list was transcribed and checked, and how to update it.

- There is no setting: the backend reads `data/ref-data/acmg-sf/` (`/data/ref-data/acmg-sf/` in
  the containers, so a Cloud Run deployment needs it in its reference-data bucket).
- Every sync reads the whole list. A gene on it gets `success` and its entry, any other gene
  `missing`, both with the release `v3.3` and the file's checksum. Genes are matched by HGNC ID,
  so a gene HGNC renamed since publication still matches.
- A list file that is missing, unreadable or not whole (no version; a gene without its symbol,
  HGNC ID, phenotype or category; a gene listed twice; a gene count other than the one it states)
  is refused: every gene gets `error`, and the gene page says the list could not be read, never
  that a gene is not on it.
- A gene synced before CoGA had the list has no status for it, and the gene page says it was not
  checked; a full sync fills it in.
- The list is shown on the gene page only: no filter, ranking or report reads it.

## 3. Package import

### Where packages live

`FAMILY_IMPORT_ROOTS` lists the places Package Import may read (comma-separated). The default is
the local folder `/data/families`, which Docker Compose mounts from `./data`. A cloud deployment
points it at a bucket prefix:

```env
FAMILY_IMPORT_ROOTS=/data/families                # local (default)
FAMILY_IMPORT_ROOTS=gs://my-coga-bucket/imports   # a bucket; s3:// also works
```

On Google Cloud, Terraform sets `gs://<phi bucket>/imports` when `storage_backend` is `gcs` (see
[deployment-gcp.md](deployment-gcp.md) §11). The backend's cloud identity needs read access to
the bucket. A path outside these roots is refused (`package_folder_not_allowed`), and so is a
local path that cannot be followed: one through a symlink loop, or starting with `~` for a home
folder the server does not have (both used to fail with a 500).

A package in a bucket is downloaded to a temporary folder for validation and import, except its
aligned reads: `.cram`, `.crai`, `.bam` and `.bai` files, and a `.csi` named after an alignment
(`<sample>.bam.csi`), stay in the bucket. No importer reads them, and a whole-genome CRAM is tens
of GB. Only the `alignments` dataset may name such a file: its importer checks that the object
exists and records its URI, and the genome browser reads it from there. Every other file must be
downloaded. On Cloud Run the temporary folder is held in memory; see
[deployment-gcp.md](deployment-gcp.md) §11 for sizing.

The temporary folder is deleted after the import, so what the import records and reports names
the bucket folder and its objects: the job's log and validation report, the family's package
record and the raw-file provenance ([database.md](database.md), `raw_import_files`). A manifest
without `family_id` takes the bucket folder's name (the last part of its URI), as a local
package takes its folder's name.

Only the import or validation that downloaded a package reads its temporary folder. A typed
folder path into it, or an import root linked there, is refused (`package_folder_not_allowed`);
it used to read another import's staged copy. The temporary folders lie in
`coga-family-imports` in the server's temp folder, which the backend makes private to itself.
If it finds a symlink, a file or another user's folder there, it refuses to stage a bucket
package rather than follow it (SEC-27).

**Discover** and **Write manifest** work on local folders only, so a package in a bucket must
already contain its manifest.

### The import job

The Package Import page lists the packages under the roots (**Rescan** to refresh); you can also
type a folder path. The list shows only folders the import takes, and reads nothing in the
others. Left out: a package folder that is a symlink leading out of the roots, one that loops,
and one leading into a bucket package's temporary folder, which is no import root
(`_listed_package_folder` in
[family_package_source.py](../backend/app/services/family_package_source.py) checks each folder
with the import's own folder guard before looking into it). A symlink that stays under the roots
is followed, and its target listed. A package's `ped/` or long-read folder that is a symlink is
followed wherever it leads; Discover and validation report it
([A package folder that is a symbolic link](#4-package-layout-and-manifest)). A typed path to a
folder the list leaves out is refused with the reason (`package_folder_not_allowed`).

You choose a project, a new or existing family, and what to do with data that already exists.
**Discover manifest** drafts a manifest; edit it and save it with **Write manifest.yaml**. **Dry
run** is on by default: it validates without writing anything. Untick it and **Start import** to
load the data. Jobs show under "Recent family imports". The in-app guide walks through these
steps.

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
- `update`: import into the existing family, and skip each dataset that already has data. That
  means data as stored (small variants, structural variants and tracks in ClickHouse; repeats and
  Paraphase results in Postgres), not Postgres's record of a track
  (`sample_interval_track_sources`), which a failed overwrite's restore can leave behind its rows.
  - Track datasets and Paraphase are skipped per sample (the HiFiCNV tracks of `cnv` per track,
    its calls whole); the message names what was skipped. A dataset skipped whole ends `skipped`,
    not `imported`.
  - The SNV family VCF is skipped when the family holds small variants of the callset the file
    holds, which the message names ([below](#the-small-variant-callset-an-import-records)).
    Imputed genotypes and the `haplotypes` dataset are also skipped when the family holds
    haplotype blocks made from imputed genotypes, since the loader writes both.
  - A track dataset reads only its own source's track
    ([Where each dataset lands](#where-each-dataset-lands)): a coverage BED is imported though the
    sample holds WisecondorX bins, an APCAD though it holds HiFiCNV's minor allele fraction.
- `overwrite`: import into the existing family, and replace each imported dataset.

While a job runs, the page shows each dataset's progress (`progress` in its summary): how long a
finished dataset took and, for the running one, the share of its files read and the time left at
its pace so far. The loaders that count what they read are the small variants (the SNV VCF, the
NIPT pair's files, the imputed genotypes) and APCAD, which take most of an import's time; another
dataset shows how long it has run. The time left covers the running dataset only; the page says
how many follow. It first shows after half a minute of reading.

A job runs as the account that queued it, found by its id whatever its e-mail address is by then
(SEC-32), and only while that account is an active administrator, as queueing an import requires
(SEC-35). If the account has been deactivated, is no longer an administrator, or no longer exists
when a worker takes the job up (also a `validating` job taken up again after its worker stopped),
the job is not run. It ends `failed`, saying why, with nothing of the family written; an active
administrator queues the import again. An import already running when its account is deactivated
or demoted runs to its end.

Jobs run on background workers, one job at a time each; every worker takes the oldest job left.
Docker Compose runs them in the `import-worker` service, in their own processes
(`python -m app.import_worker`), and the API runs none
([application-scheme.md](application-scheme.md#import-worker-processes)). A Python process uses
one core, so `IMPORT_WORKER_REPLICAS` processes import that many families at once, each on its own
core. Each process runs `FAMILY_IMPORT_WORKER_COUNT` workers (1 by default, up to 8), which share
its core. Without that service, as on Google Cloud, the API runs `FAMILY_IMPORT_WORKER_COUNT`
workers itself, on its one core and the event loop that answers requests. With 0 in the API and
no import-worker process, a job stays `queued` until one starts. A running job's record names its
worker, whose ID starts with the process ID; the log line
`Package-import worker <worker> claimed job <job>` keeps that after the job ends. A process that
stops (`docker compose stop`, a restart, a crash) stops its import where it is; its job is taken
over as below.

A running job writes a heartbeat every minute. The database's clock stamps it and judges its age,
so workers in other processes and on hosts with other clocks agree. A job whose heartbeat is ten
minutes old counts as stopped (a restart, a crash, out of memory), and the next worker to look,
in any process, takes it over:

- Not yet writing the family (still `validating`): the worker runs it again from the start. The
  job keeps the earlier attempt's log lines.
- Writing (`running`): the job ends `failed`, interrupted, and is not run again, as a new run
  would not undo what it wrote. Its log and dataset summaries stay as the import left them.

A process that is alive but cannot write its heartbeat for those ten minutes (no database
connection for it, or an event loop held by other work) is stopped too. When its heartbeat next
reaches the database and finds the job no longer its worker's, the import stops where it is, as a
stopped process would: no clean-up below runs, nothing more is written, the family keeps the
import's `import_unfinished` entry (as the job's *Interrupted* record says), and the family's
variant-write locks go with their connection. Until then the import may go on: a job update that
matches no row (its progress, or how it ended) changes nothing, and the backend log says so,
naming the job. An import whose heartbeat has not reached the database for five minutes (half the
window) stops itself the same way, before any worker can take its job over; the job is ended as
interrupted once a worker finds its heartbeat stale (SAFE-15).

The family's variant-write locks are held on their own database connection, and Postgres releases
them with it. A running import checks that connection every ten seconds (`SELECT 1` in the
locks' transaction; no answer within thirty seconds counts as lost). Once it is lost, another
write of the family may have started, so the import stops where it is and restores nothing: the
job ends `failed`, saying why, its backups of the family are dropped, and the family keeps the
import's `import_unfinished` entry until it is imported again with overwrite (SAFE-15).

If a dataset fails, the job ends `failed`, and CoGA leaves no half-loaded family that looks
complete:

- A new family where nothing imported is removed again.
- A failed `overwrite` of an existing family is put back to its state before the import.
- Otherwise the imported datasets are kept, and the family is flagged import-incomplete
  (`families.metadata.import_incomplete`).

The flag holds the failed and the imported datasets, the time and the import job's id; the job's
record holds each dataset's error, which the flag does not copy. The flag stays until each failed
dataset has been brought again, by an import or an upload
([What an upload records](#what-an-upload-records)), for the same samples and (for small
variants) the same callset ([below](#the-small-variant-callset-an-import-records)). An import that
completes without that leaves the flag, and says so in its log.

- An `update` will do: the failing loader's rows were rolled back or cleaned up, so the update
  finds none and imports the dataset whole. A dataset or sample it skips (its rows stayed) does
  not count; a dataset imported for some samples and skipped for others counts for those it
  imported.
- A dataset counts as imported again when the import ends it imported, imported with warnings
  (the phenotype rows it refused left out, each named in the job), or registered because the
  package carries nothing CoGA imports for it (phenotypes without an HPO row, per-sample GLIMPSE2
  BCFs, a family-level APCAD BED, a pipeline run record without versions or parameters). Nothing of
  such a dataset can be missing, and its registration completes no failure of data another import
  brought. A dataset registered for want of an assembly, or of a file it could read, does not
  count.
- A later failure keeps an earlier one's failed datasets. A dataset that fails again for another
  callset or other samples keeps each: the flag lists every scope a dataset failed for, each with
  the job that holds its error (`failed_scopes`), and the dataset leaves only once each has been
  brought again. An admin's deletion of a sample removes the sample from each scope
  ([below](#the-mark-of-an-upload-that-did-not-finish)), as no import can bring its data again.
- `failed_jobs` names the job of each dataset's latest failure, which the family pages and a
  sign-out show. For a dataset it names with no job (it failed outside one: a single-file upload,
  or an import run outside a job), they say no import job records its error, and never name the
  flag's own job, another failure's, for it.
- A flag CoGA cannot read (`true`, say) stays as it is when a later import or upload fails or
  completes: nothing shows what would complete it.

An import whose process stops part-way does none of this: nothing runs in an ended process. So an
import marks the family before it writes anything of it: an entry in
`families.metadata.import_unfinished` (its job, its start time, its datasets). It records each
dataset it finishes there, and removes the entry when it ends, unless its restore failed part-way
(below). An entry that stays names an import that stopped, and the datasets it had not finished,
which may be partly written or missing. Only an import that completes with `overwrite` and
imports those datasets again, for the same samples and (for small variants) the same callset,
removes it, since an overwrite replaces just that. An `update` cannot: it skips a dataset that has
data, partly written data too. An import that fails after marking the family but before its first
dataset (while registering it, say) wrote none of its datasets: its entry becomes the
import-incomplete flag, naming them all as failed.

An `overwrite` of an existing family first copies the family's variant and track rows into backup
tables in ClickHouse, to put the family back if a dataset fails, and drops them when it ends. If
its process stopped, the worker that ends its job drops them, and every start drops the backups no
running import owns. The family is not put back from such a backup: its Postgres part was lost
with the process, and the family may have been written since.

An import still alive when its job was ended can reach its restore before its heartbeat stops it,
with its backups dropped. So a restore first checks that every backup table is there, and deletes
nothing if one is missing: the family keeps what the import left, flagged import-incomplete with
the failed and the imported datasets. A restore that fails otherwise (part-way, say) may have
removed rows of any of the family's datasets, whichever import wrote them, since table by table it
deletes the family's rows before putting the backup's back. Its flag therefore names every
dataset the import set out to import as failed, those it imported or only registered too, and
none as imported; the family stays flagged until an import has imported each again. The job's
record agrees: its dataset table shows each as failed, with how its loader had ended, and its
error names them all. Postgres is put back after ClickHouse, so it can still record a track whose
rows the restore removed. An `update` reads the rows: it imports again a dataset whose rows were
removed, which then leaves the flag, and skips one whose rows are there, which stays flagged.

Such a restore also removes rows of datasets the import did not carry (another small-variant
source, another package's SV calls, an upload's data), which its flag cannot name. So the import's
`import_unfinished` entry stays too. Once the restore begins, the entry names every dataset of
the import and every dataset the family holds in ClickHouse (as its imports and single-file
uploads recorded them in `families.metadata.imported_scopes`;
[What an upload records](#what-an-upload-records)), each with its samples and (for small variants)
its callset. Datasets of another import or upload held in Postgres alone (repeat calls, Paraphase
results, QC, phenotypes) are no longer named: the restore puts Postgres back in one transaction,
which a failure rolls back whole, so none is left partly written. A sign-out refused for such an entry, its dialog and the signed
record call it a restore that did not finish, not an import that stopped: the record freezes the
entry with `whole_family: true`.

The family stays marked import-incomplete until each dataset has been brought again with
`overwrite`: import the whole family again, every package of it, not only the failed datasets,
and upload again with `overwrite` what an upload brought. Each import that completes with
`overwrite`, and each upload with `overwrite`, removes from the entry what it brought again; the
entry goes with the last. An `update` removes nothing (it skips a dataset whose rows are there),
nor does an upload without `overwrite`. The job's log says so and names the datasets, as does the
log of each import that leaves some still to import. An import whose process stops during its
restore leaves the same entry. A restore that puts the family back, or refuses to start, removes
the entry as before.

A single-file upload marks the family the same way before its first write to ClickHouse, and so
does each deletion of the family's data that deletes or rewrites rows there: an admin's deletion
of a sample, of a sample's structural variants or of the family; a PED upload or manual family
that replaces the family; a structure change that clears its data
([below](#the-mark-of-an-upload-that-did-not-finish)).

While the flag or an entry is set, every family page shows *Import incomplete*, and sign-out needs
the signer to acknowledge it with a reason ([clinical-traceability.md](clinical-traceability.md)).

#### The small-variant callset an import records

An import records its small variants (in the flag, its entry and `imported_scopes`) as the
callset it stores them as: the `source_format` the SNV dataset declares or, with `auto` (the
default), the callset its family VCF holds. The import reads that from the file before it writes
anything of the family, as the small-variant loader reads it: `glimpse2` for imputed genotypes
(genotype posteriors in the first record, or phased genotypes in a file whose header or name says
it was phased), else `clair3`. A monogenic NIPT pair's files are `nipt`, a long-read couple's
`clair3`. So two imports that both leave `source_format` at `auto` complete each other's failure
or entry only when their files hold the same callset: direct calls do not bring again imputed
genotypes the family held, nor the other way round. A file that reads as another callset when its
dataset's turn comes changed while the import ran, and is refused before anything of it is
written.

The SNV dataset's update check and clean-up read that callset too, never an assumed one:

- An `update` skips the dataset when the family holds small variants of the file's callset
  (imputed genotypes are imported beside direct calls the family holds).
- After a failure the clean-up removes that callset's rows only, the callset the flag names (a
  failed import of imputed genotypes leaves the direct calls, and the other way round).
- An import that could not read the file when it began reads it when the dataset's turn comes;
  one it still cannot read is refused, as the loader refuses it, before anything is counted,
  written or removed.

(Both used to read the direct calls, `clair3`, whatever the file held: an update of imputed
genotypes was skipped when the family held direct calls, and a failed import of imputed genotypes
whose overwrite had no backup removed the direct calls.)

The clean-up of the `snv` and `haplotypes` datasets (for the latter, the imputed genotypes and
their haplotype blocks) runs only once the loader began writing ClickHouse: its delete of the
callset it replaces or, with none stored, its first insert (SAFE-32). A file the loader refuses
before that (a VEP table without its header row, a lock it cannot take) replaced nothing, and the
family's callset stays; the dataset still fails, and the flag names it. (The clean-up used to run
after any failure, so such a refusal, in an overwrite whose backup could not be taken, cost the
family its callset.)

A record whose callset reads `auto` names no callset, and no import or upload completes it,
whatever callset it stores. Such a record was written before imports recorded their callset
(these were not rewritten), or by an import that could not read its family VCF when it began (it
then fails on the file, or registers it only). A family such a record marks stays marked, and its
sign-out needs the acknowledgement.

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

A couple screened for carriership on the long-read pipeline has no PED and no joint callset, only
each partner's own files (the NeedlR VCF in `sv/<sample>/needlr/` in later runs):

```text
COUPLE1/
  snv/MOTHER1/annotation/MOTHER1_annot.vcf.gz (+ .tbi)   and FATHER1's
  sv/MOTHER1/needlr/MOTHER1_sv_phased.needLR.4.0.vcf.gz (+ .tbi)
  repeats/MOTHER1/MOTHER1_tr.vcf.gz (+ .csi)
  paraphase/MOTHER1/MOTHER1.paraphase.json
```

How Discover reads such a folder and imports its per-sample files:
[Per-sample callsets of a long-read couple](#per-sample-callsets-of-a-long-read-couple).

A PGT package from nf-cmgg/copgtm is laid out per tool, and holds the files Discover reads for
each dataset:

```text
FAM001/
  manifest.yaml
  ped/combined.ped                       the couple and the embryos
  dashboard/samplesheet.csv              the pipeline's input: each sample's role (newer runs
                                         keep the run's own name, samplesheet_FAM001_10x.csv)
  dashboard/pedigree.csv                 father, mother, index and embryo per row
  qdnaseq/SAMPLE_cnv.csv                 QDNAseq bins (copynumber) and segments (segmented)
  apcad/EMBRYO1_APCAD_parental_data.csv APCAD: the sites the run's APCAD read, when it writes them
  split_trio/filter_trio/EMBRYO1.trio_filtered.vcf.gz (+ .tbi)          APCAD otherwise
  apcad/EMBRYO1_pcf_{mat,pat}_data.csv   PCF segments, when the run writes them
  mito/variants.annotated.txt            mtDNA-Server calls of every sample, when the run writes them
  phasing/shapeit_filter/FAM001_shapeit_rephased_final.vcf.gz (+ .tbi)  haplotypes
  phasing/haplotype_origin/PARENT_haplotype_conclusion.txt (+ PARENT_affected_normal_haplotype.csv,
                                         which a run with a sibling as its index does not write)
  cram/SAMPLE.cram (+ .crai)
  mean/SAMPLE_coverage.csv, ngsbits/SAMPLE.tsv, qualimap/SAMPLE/genome_results.txt + qualimapReport.html
  picard/FAM001_ADO_ADI.csv, rtgtools/FAM001_{cohort,imputed}_concordance.csv, king/FAM001.kin0
  pipeline_info/params_*.json, copgtm_software_mqc_versions.yml
```

Its PED gives the embryos the sex ngs-bits read, so CoGA cannot tell them from the couple's other
children. It also lacks the index: the relative whose haplotypes tell the affected parent's two
haplotypes apart. Discover takes both from the samplesheet (the one the newest `params_*.json`
names as its `input`, when the run kept it under that name): each embryo gets the embryo role
under `family.members`, and an index the PED lacks is added under `family.add_members` with the
sex ngs-bits read. No pipeline file says how the index is related, so Discover proposes a link
from what KING measured against the couple:

- first-degree to both parents: the couple's child (the proband);
- otherwise a relative of unknown degree (`family.relationships.relatives`) of the parent or
  parents KING measures it related to or, when KING sees no relationship, of the affected parent
  the run traced.

The warning quotes KING and says which link it proposed. Change the link before the manifest is
written, or on the family page after the import. Without a link the index's haplotype stays grey
and is not used to find the risk haplotype.

Discover also sets `roi` to the region of the newest `params_*.json`, and records the parent the
run traced (`affected_parent`) under `metadata.pgt.affected_parents` and the samplesheet's index
under `metadata.pgt.indexes`. The run does not record the inheritance model: set
`metadata.pgt.inheritance_model`, and the import records that parent and the index as affected
or as proven carriers, as the model asks (see the manifest below). Not imported: the embryo-only
VCFs (`split_trio/extract_embryo/`), the pipeline's HTML plots and the `dashboard/` copies. The
PCF segmentation is read from its table only: a run that only draws it in
`apcad/EMBRYO1_pcf_apcad_plot.html` gets no PCF track, and Discover leaves the `pcf` dataset off.

APCAD is read from the run's own table of the sites it read for each embryo, where the run writes
one (`apcad/EMBRYO1_APCAD_parental_data.csv`: parents homozygous for opposite alleles, embryo
read deep enough; the owner's decision of 2026-10-30). It takes from `raw_AF` the embryo's allele
fraction (its `AF` column is the cohort's), from `source` the parent the alternate allele came
from (`mat`, `pat`), and its FILTER and QUAL. These are the points the laboratory's own APCAD
plots and the PCF segments rest on, and the table imports in seconds. An embryo without one is
read from its trio VCF, as before: every site at which only one parent carries the alternate
allele. A table whose `Indiv` column names another sample than its entry's fails validation
(`apcad_table_sample_mismatch`).

Beside that table, an embryo's entry names its segregation table (`segregation:`,
`apcad/EMBRYO1_APCAD_segregation_data.csv`). Discover writes the line where the run has one, but
not into a manifest that already declares the `apcad` dataset, whose entries it keeps as they
are: there, add it to each embryo's entry by hand. The table holds the sites where one parent is
heterozygous and the other homozygous (`source` 2A/2B: the mother heterozygous; 3A/3B: the
father; A: the homozygous parent carries the alternate allele), read into the embryo's
`apcad_segregation` track. Each site is stored at the embryo's fraction of the allele the
homozygous parent does not carry (`raw_AF` for B, one minus it for A), with the heterozygous
parent as its origin; a read serves each site's group beside it. The genome and chromosome views
draw them as the *Parental origin* track, which tells heterodisomy from isodisomy on an
aneuploid chromosome. The table is checked with the APCAD files, before any track is replaced.
One without a site of group 2 or 3 is refused; one whose `Indiv` names another embryo fails
validation like the APCAD table. The track is read only: a BED upload of it is refused.

The run's mtDNA-Server table (`mito/variants.annotated.txt`) holds the chrM calls of every sample
it analysed, one row per variant per sample; its `ID` column names the sample's chrM BAM. The run
writes no chrM VCF. Discover gives each member with rows in the table a `mito` entry naming the
table and that ID (`table`, `table_id`: `EMBRYO1.depth_10x_chrM.bam` is EMBRYO1's), and says
which IDs name no member. The import writes each member's rows as its chrM VCF (the genotype,
`Coverage` as depth, `VariantLevel` as heteroplasmy level) with their own annotation, and reads
them as a chrM VCF and its mutserve annotation. A row whose allele is not one base (mutserve's
`D` for a deletion) is not read as a call. An entry whose ID holds no call fails validation
(`mito_table_rows_missing`). The `Phylotree17_haplogroups` and `Phylotree17_clades` columns count
the PhyloTree 17 haplogroups and clades that carry a variant. Such a number is kept as that count
(`haplogroup_count`, `haplogroup_clade_count`), never read as a haplogroup, so the table gives no
sample a haplogroup (it used to read the most frequent count as one).

A monogenic NIPT package from the NIPT-M pipeline holds a one-sample VCF per parent: the
maternal plasma's (cfDNA) and the father's, both called by Mutect2 in tumour-only mode. A
per-target coverage table per sample gives each capture target's depth:

```text
NIPTFAM1/
  manifest.yaml
  nipt_trio.ped              the father, the mother (her sample is the plasma) and the fetus
  CFDNA1.mutect2.vcf.gz      the plasma's calls
  FATHER1.mutect2.vcf.gz     the father's calls
  coverage_CFDNA1.txt        per-target coverage of the plasma
  coverage_FATHER1.txt       per-target coverage of the father
```

When Discover finds no joint SNV VCF, it looks for such a pair: a PED child without a VCF of its
own (the fetus) whose mother and father each have a one-sample VCF named after them
(`<sample>.vcf.gz` or `<sample>.<anything>.vcf.gz`), in the package folder or one folder down. It
takes the mother's sample as the maternal-plasma cfDNA and drafts a NIPT manifest:
`analysis_type: monogenic_nipt`, `assay: nipt_cfdna` on the plasma's sample, the two VCFs under
`datasets.snv.per_sample`, and the coverage tables it finds (`coverage_<sample>.txt` or `.tsv`,
in the same places) under `datasets.coverage.per_sample` as `target_table`. Its warning
(`nipt_pair_detected`) names the samples it took for the plasma and for the father: check both
before the import. A VCF whose one column is named neither after its sample nor after its entry's
`vcf_sample` (another tube's name, say) is warned of too (`nipt_vcf_column_named_otherwise`), as
in validation. A parent's VCF that Discover cannot read (cut short, damaged, not gzip) is refused
by name (`dataset_vcf_unreadable`), and no pair is drafted from it. How the pair is imported:
[Monogenic NIPT pairs](#monogenic-nipt-pairs).

Discover knows several file names per dataset and shows what it found. The exact patterns are the
`standard_v1` entry of `NAMING_SCHEMES` in `backend/app/services/family_package_discovery.py`. A
pattern may contain `*` (the SV annotator puts its version in the file name); the saved manifest
always holds the real path. A long-read package's per-sample SNV or NeedlR file counts as the
family's callset only when the family has one sample. With two or more samples and no joint VCF,
Discover proposes one VCF per sample (`per_sample`) and names a sample that has none. For the
long-read pipeline's annotated SNV files it also sets `source_format: clair3` and, when the header
declares them, `exclude_filters: [RefCall, NoCall]`.

Discover names the files it looks for after the family and sample IDs, and looks for them in the
package folder only (`package_path_within` in
[family_package_common.py](../backend/app/services/family_package_common.py)). An ID is read as
written: a `*`, `?` or `[` in it matches only itself, so one sample never takes another's file,
and a leading `~` is part of the name, not the home folder. An ID is no path (the ID rule,
[Validation](#validation)): one holding a `/` or a `\`, or that is `.` or `..`, is refused
(`family_id_invalid`, `sample_id_invalid`) before Discover looks anything up under it, so no file
named after an ID lies outside the package. (Discover used to warn about such an ID,
`family_id_leaves_package` or `sample_id_leaves_package`, and draft a manifest for it: SEC-15.)

These count as absent, as validation refuses them: a PED, a NIPT VCF or a dataset's file that is
a symlink leading out of the package, or that loops; and a long-read per-sample entry that loops
(it used to fail Discover and the package list with a 500). A folder named like a PED
(`FAM1.ped/`) is no PED to Discover or the package list (Discover read it as the PED and failed
with a 500).

**A package folder that is a symbolic link is reported.** The package list and Discover read some
of a package's folders by their layout: `ped/`, a long-read dataset folder (`snv/`, `sv/`, `cnv/`,
`repeats/`, `paraphase/`, `mito/`), a folder in one (a sample's, `snv/HG002/`) and a folder in
that. When one of them is a symbolic link they follow it, wherever it leads: a PED or per-sample
folder behind it lists the package, and Discover proposes a member from a linked per-sample
folder, though what it holds may be another family's. Discover and validation (a dry run, and the
import, which runs it) report each such link as a warning (`package_folder_link`): the folder in
the package, and where it leads, as a path relative to the import folder that holds it
(`COUPLE2/snv/HG003`) or as "a folder outside the import folders". A message never names an
absolute path. Nothing under a link is looked into, and a link to no folder is not reported. The
owner chose a warning over a refusal (SEC-27, `package_folder_link_issues` in
[family_package_source.py](../backend/app/services/family_package_source.py)). The guards above
still hold: a file the manifest names, or Discover looks for, through a link leading out of the
package is not read.

### The manifest

The manifest is a file in the package folder. A `manifest.yaml`, `.yml` or `.json` that is a
symlink leading out of the folder, or that loops, is refused (`manifest_outside_package`,
`manifest_link_out_of_package` in
[family_package_source.py](../backend/app/services/family_package_source.py)): Discover drafts
nothing, validation reads nothing more of the package, the package list shows it under its
folder's name, and **Write manifest.yaml** answers 400. (Discover and validation used to read the
link's target as the manifest, and Write manifest wrote through it, replacing that file or
creating the one a dangling link named.) A symlink that stays in the package is followed when
the manifest is read.

**Write manifest.yaml** follows no link: it writes the text into a new file in the package
folder and renames that file over `manifest.yaml` (SEC-27). The check for a link out of the
package runs before the write, and a link put in the manifest's place between the two was
written through; now the rename replaces it. A `manifest.yaml` that is a link within the
package is replaced by the written file too, and the file it pointed at is left as it was.

Every path the manifest names is relative to the package folder, or an absolute path inside it.
Validation refuses with 400 a path that leads out of the package (an absolute path elsewhere,
`..` that climbs out, a symlink that leads out), one that starts with `~`, and one through a
symlink loop (`_resolve_package_path` in
[family_package_common.py](../backend/app/services/family_package_common.py)). CoGA reads no home
folder: a leading `~` used to be expanded, so `~/x.vcf` named a file in the backend's home folder
and `~nouser/x.vcf` failed with a 500. A `~` further on in a path is part of a name. The guard
finds a loop by looking the resolved path up, so Python 3.12 (whose `Path.resolve` raises on a
loop) and 3.13 (whose does not) refuse it alike.

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
  coverage:
    per_sample:
      SAMPLE1: {bed: coverage/SAMPLE1.coverage.bed, content: read_depth}   # or log2_ratio
  cnv:
    per_sample:
      HG002:
        vcf: cnv/HG002/annotation/HG002_annot.vcf.gz
        copy_number_bedgraph: cnv/HG002/HG002.Sample0.copynum.bedgraph
        maf_bigwig: cnv/HG002/HG002.HG002.maf.bw
```

`roi` is the family's region of interest: a gene (its symbol, an Ensembl gene or transcript ID,
or a RefSeq accession) or a region such as `chr7:117480025-117668665`. The import looks a gene up
in the assembly of the import's project, or else of the existing family, on its chromosome before
a copy on an ALT contig, as the family page does. The PGT embryo calls and the NIPT coverage
check read this region, so after an import whose manifest names one, the family has that region
or none:

- An `roi` the import does not find clears the family's region of interest. The import warns
  (`manifest_roi_unresolved`) and names the region it cleared. No embryo is called until the
  region is set again, on the family page or by a corrected manifest. (The import used to keep
  the family's earlier region without a word, and the embryos were called at it.)
- The dry run and `POST /family-imports/validate` look the region up the same way and give the
  same warning ahead of the import. With no project and no existing family to look it up in,
  they say it was not checked (`manifest_roi_not_checked`).
- A manifest without `roi` leaves the family's region as it is.

`metadata.pgt.affected_parents` names the parent whose condition the PGT tests for (the PGT
pipeline's `affected_parent`, which Discover copies). `metadata.pgt.indexes` names the index, the
relative whose haplotypes the risk haplotype is read from (the samplesheet's `index`, which
Discover copies). Each gets the status the inheritance model asks, which the haplotype analysis
reads to find the risk haplotype:

| Model | Affected parent | Index |
| --- | --- | --- |
| AD, XLD | affected | affected |
| AR | proven carrier | affected (under a recessive model the index is the affected child or relative) |
| XLR | mother: proven carrier; father: affected | male: affected; female: proven carrier; sex not recorded: none |
| mitochondrial | none | none |

- A status recorded for the parent or the index wins: under `family.members`, in the carrier
  lists or in the PED, and for the index also a `clinical_status` given in `family.add_members`.
  Where it contradicts the model, validation warns.
- Without an inheritance model the parent keeps its status, and validation says the model is
  missing.
- Validation lists each status it derives as a warning, and says when an index gets none. A
  derived carrier status names the model in the member's `carrier_evidence`.
- The index must be a member, and not the affected parent.
- The family keeps the model, the carriers, the affected parents and the indexes under its own
  `metadata.pgt`.

Under AR both parents of an affected child are obligate carriers. Where the index is the
embryos' parents' child and affected (derived or recorded), the parent the run did not trace is
recorded as a proven carrier too (the owner's decision of 2026-10-30), its `carrier_evidence`
naming `metadata.pgt.indexes`, and validation lists it (`pgt_index_parent_status`). A status
recorded for that parent wins, as above. An index that is not the couple's child says nothing of
the other parent.

`family.add_members` adds a member the PED lacks as one more PED row, with its `role` (default
`relative`), `sex` (default unknown), `clinical_status` (default unknown) and, when given, its
`father` and `mother`. Every check and the stored pedigree treat it like the PED's members. A
member already in the PED is an error; its states go under `family.members`. Other links go under
`family.relationships`, which may only name members. A manifest without `ped` names all its
members under `family.add_members`, whose rows are then the whole PED (the long-read couple's
manifest, which Discover drafts).

`family.relationships.relatives` links a member to the family through another member by an
unknown degree: a PGT index known only to be on the mother's side, say, or related to both
parents. The pedigree draws the link as a dotted arc with a question mark. The haplotype track
colours the member along the genome when it turns out to be the linked member's parent or child,
sharing one of that member's haplotypes along nearly every chromosome. A more distant relative is
coloured across the ROI and 3 Mb on each side when the markers on both sides show it carrying the
same one of the linked member's haplotypes, and is grey elsewhere (see
[haplotype-segregation-analysis.md](haplotype-segregation-analysis.md)). These links are errors,
since the family editor would refuse the family's every later edit: a member linked to itself;
two members the PED or the manifest already records as parent and child or as a couple (a
child's parents count as one); the same pair linked twice.

`analysis_type: monogenic_nipt` marks a monogenic NIPT family (see
[monogenic-nipt.md](monogenic-nipt.md)). Its `samples` entry for the maternal plasma says
`assay: nipt_cfdna` (without one, CoGA takes the mother's sample), and may name the capture panel
as `assay_panel`, which scopes the NIPT artifact list; both are copied into the sample's
metadata. A dataset can be switched off with `enabled: false`.

The `snv` dataset takes three optional settings:

- `source_format`: `clair3` for a directly called callset, `glimpse2` for imputed genotypes. Set
  it for long-read packages: without it the importer guesses from the first record, and can take
  a phased primary callset for an imputed one and hide it from the diagnostic views.
- `exclude_filters`: FILTER values to drop at import, such as `[RefCall, NoCall]` for a
  DeepVariant whole-genome callset. A record is dropped only when all its FILTER values are in
  the list.
- `vcf_sample`: the VCF column to use, when the caller named the sample column after its input
  file. CoGA already strips known tool suffixes; a column that matches no family sample fails the
  import.

A `coverage` entry that names a BED (`bed:`) declares what it holds: `content: read_depth` for
read depths, `content: log2_ratio` for log2 ratios against the sample's baseline. The import
keeps it with the track (`sample_interval_track_sources.metadata.content`,
[database.md](database.md)), and it decides how the track is read: the coverage chart draws read
depth on a depth axis and a log2 ratio on its log2-ratio axis, and the readers that take coverage
as depth read only a BED declared as read depth ([below](#where-each-dataset-lands)). An entry
without `content` imports as `unknown`, and validation warns (`coverage_content_undeclared`): the
track is drawn on its own range without an axis, with a note, and never read as depth. Any other
`content` is an error (`coverage_content_invalid`). A per-target coverage table (`target_table:`)
declares nothing: it holds each target's depth. (Before TRACE-9 a coverage BED declared and
recorded nothing: the importer and the depth readers took it for depths, while the coverage chart
drew it on its log2-ratio axis.) Coverage BEDs stored before declarations read as `unknown`; the
data is synthetic, so none is migrated: import or upload such a BED again with what it holds.

### Where each dataset lands

| Dataset | Stored as |
| --- | --- |
| `snv` | small variants (ClickHouse), with the VEP table if given (VEP's `--tab` output, or its default output, whose `Extra` column's `KEY=VALUE` fields are read as the `--tab` columns, CLIN-52). A monogenic NIPT pair's one-sample VCFs (`per_sample`) as one callset, source `nipt` ([below](#monogenic-nipt-pairs)); a long-read couple's one-sample VCFs (`per_sample`, `source_format: clair3`) as the primary callset, source `clair3` ([below](#per-sample-callsets-of-a-long-read-couple)) |
| `sv_needlr` | structural variants (ClickHouse), source `needlr`, from one family VCF or one VCF per sample (`per_sample`), each file's calls bound to its sample. A breakend keeps its mate, the position its ALT names in brackets, as a structural-variant upload reads it ([below](#6-single-file-uploads)). Each SV takes the id its coordinates give it, never the record's ID, which its annotation keeps (below this table) |
| `repeats_trgt` | repeat expansions (Postgres), scored against the TRGT catalogue |
| `wisecondorx`, `qdnaseq` | bins as the `coverage` track, segments as the `segments` track |
| `coverage` | the `coverage` track (a BED per sample, `bed:`, with what it holds, `content:`, [above](#the-manifest)), or the `target_coverage` track (a capture panel's per-target coverage table, `target_table:`, [below](#monogenic-nipt-pairs)) |
| `apcad` | the `apcad` track: per SNV the embryo's alternate-allele fraction, tagged with the parent the alternate allele can only have come from. From a family VCF, one trio VCF per embryo (`vcf:` under `per_sample`), or the PGT pipeline's table of the sites its APCAD read (`file:`; the embryo's `raw_AF`, the parent in `source`). An embryo's segregation table (`segregation:`) gives its `apcad_segregation` track |
| `pcf` | the `apcad_pcf` track (maternal and paternal segment files) |
| `haplotypes` | a phased family VCF (GLIMPSE2, or SHAPEIT5 from the PGT pipeline) becomes small variants plus haplotype blocks; a switch in a parent's phasing is undone where the couple's children all switch together (kept in `families.metadata.haplotype_phase_corrections`). A per-sample BCF is only registered, not imported. The PGT pipeline's haplotype-origin files (`haplotype_origin`, `haplotype_conclusion`) are kept on the family as its reading of the affected haplotype; CoGA's own risk call does not use them |
| `paraphase` | Paraphase results (Postgres), shown on the family's Paraphase page |
| `cnv` (HiFiCNV) | structural variants, source `hificnv`, each named by its coordinates, type and source, never by the record's ID (its annotation keeps it); one event in two samples' VCFs is one SV with both calls (CLIN-39). The depth bigWig as `coverage` and the copy-number bedGraph as `segments`, both stored as log2 ratios like the other callers, each with its transform recorded in its `sample_interval_track_sources` row ([database.md](database.md)); a depth file without autosomal depth gives no median to normalise by, so its depths are stored as read, recorded as `none`. The MAF bigWig as `apcad` |
| `mito` | chrM small variants, source `mito`, annotated from the mutserve table, from a chrM VCF per sample or a sample's rows of one mtDNA-Server table for all samples (`table`, `table_id`); each sample's mtDNA haplogroup is the one most of its annotated variants name. The chrM SV file (`sv_vcf`, Sniffles2 `--mosaic`) as structural variants, source `mito_sv`, with each sample's heteroplasmy (`INFO/VAF`) and the mtDNA genes it overlaps, shown on the mtDNA page |
| `qc` | the sample's sequencing QC, shown as a chip in the family members table: NanoPlot and mosdepth, or the PGT pipeline's mean coverage, Qualimap summary and report, and ngs-bits sex. The PGT pipeline's family tables (`ado_adi`, `concordance`, `imputed_concordance`) go onto each embryo, its KING table (`kinship`) onto the family |
| `alignments` | the CRAM/BAM location, used by IGV: the package-relative path and, for a package in a bucket, the object's URI |
| `pipeline_info` | tool versions in the annotation manifest; run parameters on the family (for the PGT pipeline also the affected parent, the ROI, the QDNAseq bin size, and the callset and phasing panel it started from) |

Each caller's rows carry their own source tag, so re-importing one callset never removes another,
and two callers' rows of one variant stay two rows in storage
([database.md](database.md#row-identity)).

The `mito` dataset has one file per sample, and each file replaces only that sample's calls. The
other samples' mitochondrial calls stay: one row per variant holds every sample's call, so the
mtDNA analysis can set the mother's calls beside the children's. To merge a file, the import
rewrites the callset's rows of every sample. A write that fails after the rewrite's delete puts
the stored rows back; if it cannot, the dataset is flagged failed for every sample whose calls
the rows held, not only for those it names (SAFE-31; a monogenic NIPT pair's files merge the same
way). A chrM VCF without a variant record fails the dataset before any sample's calls are
replaced ([below](#where-each-dataset-lands)). The chrM SV files follow the same rules: one
deletion called in the mother and the child is one SV with both calls, and a sample's new SV file
replaces only its own calls. For this the import rewrites the `mito_sv` rows of every sample, and
an `update` takes no snapshot of the family: a write that fails after the delete puts the stored
rows back, and if it cannot, `mito` is flagged failed for every sample whose chrM SVs the rows
held.

A NeedlR SV is named by its coordinates and type, a breakend's with its mate
(`1-7000-12000-DEL---`, `1-20000-20001-BND-5-3000000-3000000`), as a structural-variant upload
names its call; never by the record's ID, which its annotation keeps. A caller numbers its calls
per run (`Sniffles2.DEL.1S0`), so two samples' files, or two runs in one family VCF, give one ID
to different calls. Named by it, they became one SV at the first call's position, the other call
attached to it and its own deletion lost.

- One event (the same chromosome, start, end, type, mate, REF, ALT and SVLEN), called in two
  samples' files or repeated in the family VCF, is one SV with every call. A sample keeps a
  genotype over a no-call (`./.`, which a line without `Maternal_GT` or `Paternal_GT` gives a
  parent), then its own call (its line's query call) over the genotype another sample's line
  gives it as a parent, whichever file comes first.
- Two calls on one position, type and mate with other alleles, or a symbolic allele (`<INS>`) of
  another length, are two SVs. The shortest keeps the id, then by REF and ALT; each other one gets
  its length as a suffix (`1-20000-20000-INS----7`), then `.2`. The events' order decides, not the
  lines' (the first line's call used to keep the id, so reading the files in another order moved
  a review to the other event: CLIN-39).
- The same event uploaded from Sniffles or Spectre has the same id, and so one review, as two
  uploads' calls of one event have; each caller's call stays a row of its own
  ([database.md](database.md#row-identity)). A NeedlR record without END ends where VCF 4.4 puts
  it, as in an upload ([below](#6-single-file-uploads)), so one call has one id from either. (It
  ended at POS + SVLEN for any type, or at POS + 1 without SVLEN, so an insertion or a breakend got
  another id from NeedlR than from an upload: CLIN-39.)

Tracks carry their caller's source tag too. A sample's track of one type can come from several
callers, each under its own source:

| Track | Sources |
| --- | --- |
| `coverage` | a BED (`web`), WisecondorX, QDNAseq, HiFiCNV |
| `segments` | the same callers |
| `apcad` | the `apcad` dataset (`apcad`), HiFiCNV's minor allele fraction (`hificnv`), a BED upload (`web`) |
| `apcad_pcf` | the `pcf` dataset (`pcf`), a BED upload |

A dataset replaces only its own source's track of a sample: its rows and its record in
`sample_interval_track_sources`. The `coverage` dataset's BED leaves the WisecondorX and QDNAseq
bins (which import before it) and HiFiCNV's depth; the `apcad` dataset leaves HiFiCNV's minor
allele fraction and an uploaded APCAD. A dataset replaces the track of each sample its file holds
a value for, and leaves the track of a sample it holds none for (a family VCF without that
sample's column). An embryo's VCF in which its reader finds no record is read as a BED, into the
dataset's own track. A package's coverage BED and a BED upload share the source `web`, and each
replaces the other ([below](#6-single-file-uploads)).

**A file without a record.** A dataset that replaces stored data reads its file before it deletes
anything, and fails, with the stored data as it was, when the file holds no record its loader can
store: a header alone, a file cut short before its first record, or nothing at all. This holds
for:

- a TRGT VCF (a sample's, or a family VCF, which must hold a record for each sample it names);
- WisecondorX and QDNAseq bins and segments;
- HiFiCNV's copy-number bedGraph and its depth and minor-allele-fraction bigWigs (a depth file
  without a bin above zero holds none);
- an APCAD file, and a PCF table (both of a sample's tables are read before its track is
  deleted);
- a Paraphase JSON without a gene;
- a sample's NeedlR file, and a long-read sample's SNV file;
- a sample's chrM VCF (the `mito` dataset checks every sample's chrM VCF for a record before it
  replaces any sample's calls).

Each measures its sample across the genome, a catalogue or the mitochondrial genome, so a file
without a record is no result: a chrM caller reports every variant against the reference, and
practically every person carries some (the owner's decision, SAFE-29). Such a file used to
replace the data with none and end `imported`, which counts as importing the dataset again: it
completed an earlier failure, or a whole-family entry, while the data was missing
([The import job](#the-import-job)). A track reader reads its file up to the first record before
it deletes the track; a file cut short after that still fails the dataset where the read stops,
after the delete, and the family is put back or flagged as for any failed dataset. After a chrM
VCF without a variant record, every sample's mitochondrial calls are as they were, and an import
that keeps the family flags it import-incomplete for `mito`.

A file without a record is a result where a sample can have no call:

- a chrM SV file with its `#CHROM` line and no call (most samples carry no mtDNA deletion)
  replaces the sample's earlier chrM SVs with none;
- a HiFiCNV VCF without a call leaves the sample without `hificnv` calls, whether or not another
  sample's VCF holds one, and the `cnv` dataset ends `imported` with its tracks. (When no sample's
  VCF held one, the calls used to stay as they were and the dataset was only registered, its
  tracks not recorded as imported: SAFE-18.)

A chrM SV file without its `#CHROM` line is refused before anything of the `mito` dataset is
written.

**A BGZF file cut where one of its blocks ends.** gzip has no end mark: a gzip file cut exactly
where one of its members ends reads as a whole file, short of the rest. bgzip, which writes the
pipelines' `.vcf.gz` files and their tabix-indexed BEDs and tables, writes BGZF: blocks of at most
64 KiB, each a gzip member whose header names it BGZF (the extra subfield `BC`), and last an empty
28-byte block, BGZF's end-of-file block. A BGZF file whose last block is not that block is refused
as cut short, like a gzip file cut inside a block (the owner's decision of 2026-10-28, DATA-4):
*… is not valid gzip: it is truncated (it does not end with BGZF's end-of-file block)*.

- An upload answers 400 and a package dataset fails, before anything is replaced. The readers
  that stream a file (the small-variant VCFs, a long-read or NIPT sample's SNV file, the track
  files) read its end before its first record. A dataset that checks every sample's file before
  it replaces any sample's data (below) checks each file's end in that check, a NIPT's per-sample
  SNV files included.
- Such a file used to be imported as its first blocks: a VCF cut where a block ends silently lost
  the records after the cut, and `gzip -t` passes it (htslib's tools warn that its EOF marker is
  absent).
- A plain gzip file, which no BGZF writer wrote, has no end-of-file block and is read as before:
  one cut exactly between two of its members still reads as whole.
- A dry run does not read a file's end, just as it does not find a gzip file cut short. The
  reference downloads' reader refuses a BGZF download without its end-of-file block too.

**Every sample's file before any sample's data.** A dataset of one file per sample reads every
sample's file, or checks it up to its first record, before it replaces any sample's data. A later
sample's refused file then fails the dataset with every sample's data as it was (SAFE-3: each
file used to be checked as it was written, after the earlier samples' data was replaced). Per
dataset:

- `repeats_trgt` reads each sample's TRGT VCF whole, and again to store it, so that one sample's
  calls are held at a time.
- `cnv` checks each sample's HiFiCNV VCF (its sample column, its records) and reads each signal
  file up to its first value before it writes a track.
- `wisecondorx` and `qdnaseq` read every sample's bins and segments, and `pcf` every sample's
  tables, up to the first row, before they clear a track.
- `apcad` reads every embryo's file up to the embryo's first value (a VCF in which its reader
  finds no record is read whole, as a BED) before it clears an embryo's track.
- `coverage` reads every sample's file before it replaces a sample's track: a BED decoded whole,
  as the BED loader decodes it, and read up to its first record; a per-target table parsed whole.
- A monogenic NIPT's per-sample SNV files are checked for their one sample column, a record and,
  for a BGZF file, its end-of-file block before the plasma's calls are written.
- `paraphase` reads every sample's JSON before it replaces any sample's results (DATA-7).
- `mito` reads every chrM SV file before it replaces a chrM call. One whose records all lie on
  other chromosomes is no chrM callset and fails the dataset (it used to read as a file without a
  call, which removed the sample's chrM SVs); one that cannot be read fails it before the chrM
  VCFs are written.

When the BED or TRGT loader refuses a sample's file (a coverage BED, an APCAD file read as a BED,
a TRGT VCF), the dataset's failure names the sample and the file, by its path in the package,
before the loader's words: *The coverage BED of S2 (coverage/S2.bed): No valid BED records
found*. The loaders' own words, which a single-file upload answers with, name neither. A
per-target coverage table's refusal names the sample and the table too (*The per-target coverage
table of S2 (coverage_S2.txt) holds no target*). A family TRGT VCF's refusal names the file before
the loader's words, which name the samples (*The family TRGT VCF (trgt/family.vcf): No valid TRGT
records found for S2*; DATA-9).

An update checks no file of a track it leaves as stored. A file the check passes is read again to
be stored; one cut short after the record the check stops at still fails the dataset where the
read stops, as above. A long-read sample's SNV file is checked up to its first record only: a
record out of position order is found as the callset is written
([below](#per-sample-callsets-of-a-long-read-couple)), since checking it first would read the
longest import's files twice.

A TRGT value that cannot be read as a number is refused before anything is replaced: a position
that is no whole number or exceeds 2³² − 1; a motif count, allele length or purity that is no
number; a purity or methylation of `nan` or `inf`. A TRGT upload answers 400 naming the line; a
package fails the dataset. (It answered 500, as Postgres holds no NaN in JSON: DATA-7.)

A Paraphase copy number (`total_cn`, `gene_cn`, `highest_total_cn`) that Postgres's `integer`
cannot hold (below −2,147,483,648 or above 2,147,483,647, however the JSON writes it) is refused
the same way: the `paraphase` dataset fails, naming the sample, the field and the gene, with every
sample's results as they were. (It failed the insert with the database's error, after the
sample's results were deleted: DATA-7.) A copy number that is no number is stored as missing, as
before.

The track viewers draw one track per caller. `GET /families/{family_id}/track-availability` lists
them, with the transform each coverage track's record names (`coverage_normalizations`) and what
each holds (`coverage_contents`: `read_depth`, `log2_ratio` or `unknown`, by the depth readers'
rule, below):

- A log2 ratio is drawn on the viewers' log2-ratio axis, with the gain/loss colouring and the
  caller's segments.
- Read depth (a coverage BED declared as read depth, HiFiCNV's depths stored as read) is drawn on
  a depth axis from 0 to a round number above the 99th percentile of the values shown, without
  the colouring or the segments, which are log2 ratios. HiFiCNV's track says why its depths are
  not a log2 ratio.
- Values of unknown kind (a coverage BED that declares nothing, an unknown transform) are drawn on
  their own range, without an axis, and the track's head says so in the warning tone.

(HiFiCNV's depths stored as read used to be left off the chart, and a coverage BED was drawn on
the log2-ratio axis whatever it held: TRACE-7, TRACE-9.) The three HiFiCNV files are also served
unchanged to the genome browser (IGV). With local storage they come from the family's package
folder, `data/families/<family>/`, at the path the import recorded, and never from outside the
family's own folders (a recorded path or a symlink that leads elsewhere names no file); for a
package in a bucket, from the bucket.

The readers that take coverage as read depth (the mtDNA coverage with its depth verdict, and the
NIPT coverage check without a per-target table) read one coverage track per sample whose values
are depth: the coverage BED (`web`) when declared as read depth, else HiFiCNV's depth when its
record says it is stored as read (`none`). Never read as depth: WisecondorX's and QDNAseq's bins
and HiFiCNV's normalised depth (log2 ratios), a coverage BED declared as a log2 ratio or declaring
nothing (`unknown`), and another caller's track whose record names no transform. Each track a
reader leaves out is named with the reason: in the family's mtDNA QC notes, and in the NIPT
coverage check's `coverage_notes`, shown with the coverage on the NIPT pages and in the printed
NIPT report (*"CFDNA1: the coverage BED does not declare whether it holds read depth or a log2
ratio; it is not read as depth."*). A sample without such a track has no coverage to these
readers: the mtDNA analysis reads its calls' depth, and the NIPT check reports its regions as
without coverage ([database.md](database.md)).

Every file a dataset names, per sample or for the whole family, is recorded in `raw_import_files`
with its size and SHA-256 ([database.md](database.md)), whichever of its names the entry uses: a
NIPT pair's coverage table under `target_table`, a PCF table under `maternal_file` as under
`maternal`. The admin page lists these files with the family's data, and **Verify** checks them
later. It reads a recorded file only where an import puts one: below `FAMILY_IMPORT_ROOTS`, in a
bucket package's staging folder or in the upload folder ([database.md](database.md)).

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
  `assay: nipt_cfdna`, else the mother of the PED's child. Each other file is merged into the
  stored rows for its own sample, as the `mito` files are: a variant is one row holding each
  sample's call where its file has one, and a file replaces only its own sample's calls. A sample
  without a call at a variant had no alt read there that its caller reported; the analysis reads
  its depth there from the sample's coverage table.
- The father's file is mostly low-level noise: nearly all its calls sit below 20% alt reads. The
  importer keeps a paternal record when one of its calls reaches 15% alt reads
  (`NIPT_PATERNAL_KEEP_MIN_VAF`, below the 20% of a heterozygous call, so no genotype is lost; the
  alt reads over all the record's reads, or the caller's AF without allele depths), or when it
  lies on a position where the plasma has a call (an MNV over any of its bases). The dataset
  summary counts, per sample, the records it left out (`skipped_by_filter`).
- Each file holds one sample, so a record's FILTER values and caller metrics (Mutect2's TLOD, FS,
  mapping quality, mismatches, repeat units and so on) are that one call's. They are kept with the
  call ([database.md](database.md), `calls.filters` and `calls.metrics`) for the NIPT quality
  filter and the de novo triage.
- The `snv` settings `source_format`, `exclude_filters` and `annotation_tsv` do not apply: the
  callset is always `nipt`, and each file's annotation is read from its own records. Nor does
  `vcf_sample` choose a column: each file's one column is bound to its entry's sample (below).
  With `update`, the dataset is skipped when the family already holds `nipt` calls.
- Each file's one column is read as its entry's sample whatever it is named, on purpose: the
  laboratory maps its genetically verified tubes to the files they are in, and a file and its
  column can be named after another tube. A column named neither as the member (its sample ID, or
  that ID with a caller's suffix such as `_sorted`) nor as the entry's own `vcf_sample` (or
  `sample_name`) is warned of by Discover, the dry run and the import alike, never refused
  (`nipt_vcf_column_named_otherwise`; the owner's decision of 2026-10-28, CLIN-13): *The NIPT SNV
  file of FATHER1 (FATHER1.mutect2.vcf.gz) names its one sample column 'TUBE9', not FATHER1: the
  import reads that column as FATHER1's calls. …* The warning names another member the column is
  named after (a swapped file reads as the other parent's calls), and an entry's `vcf_sample` the
  column differs from. A laboratory that verified the tube records the column in the entry
  (`FATHER1: {vcf: FATHER1.mutect2.vcf.gz, vcf_sample: TUBE9}`), which silences the warning; a
  `vcf_sample` of the member's own ID does not, as it names no column. The dataset's summary
  records the column each sample's calls were read from (`vcf_column`).

A per-target coverage table (`target_table`) is tab-separated with a header line, one row per
capture target. It needs the columns `chromosome`, `start` and `end` (BED coordinates),
`attribute` (`GENE;transcript;…`: the gene comes first) and `mean`, and reads `median`, `min`,
`max`, `proportion_covered` and `zero_coverage_bases` when present; a number may have a decimal
comma. The table becomes the sample's `target_coverage` track, replacing the one stored before
([database.md](database.md)); with `update`, a sample that has one is skipped. The NIPT coverage
check and the plasma's sex profile read the plasma's table, and the analysis reads each sample's
depth from its table where its VCF has no call ([monogenic-nipt.md](monogenic-nipt.md)).

One combined VCF with a father column and a plasma column, genotyped jointly, still imports as an
ordinary `snv` `family_vcf`, as the demo family does
([demo/nipt_family](../demo/nipt_family/README.md)); the analysis is the same. Without coverage
tables, the NIPT coverage check reads the plasma's `coverage` track (a BED) instead.

### Per-sample callsets of a long-read couple

The long-read pipeline calls and annotates each sample on its own, and writes neither a PED nor a
joint callset for a couple screened for carriership. The Package Import page lists such a folder
all the same: a folder counts as a package when it holds a manifest, a PED, or the pipeline's
per-sample folders (`snv/<sample>/`, `sv/<sample>/`, `repeats/<sample>/`, `paraphase/<sample>/`,
`cnv/<sample>/`, `mito/<sample>/`, each holding a file named after the sample).

**Discover without a PED.** The members are those per-sample folders, named under
`family.add_members`, so the manifest needs no `ped`.

- A folder whose name cannot be a sample ID (with a space, a control character, a `\` or another
  character the ID rule refuses) is listed too, and Discover refuses it (`sample_id_invalid`)
  rather than leave a member out.
- Each member's sex is the karyotype TRGT genotyped its repeats with (`--karyotype XX` or `XY` in
  the TRGT VCF's `##trgtCommand`). The pipeline sets it from its samplesheet, so it is the
  recorded sex, which the Sample QC checks against the reads. A member without a TRGT VCF gets no
  sex.
- Two members of opposite sex are proposed as a couple (`family.relationships.couples`, context
  `carrier screening`): the female partner with the mother role, the male with the father role,
  clinical status unknown. Any other set of members gets no link.

The warning `ped_proposed_from_folders` says what was proposed: check it before writing the
manifest.

**The SNV files are one callset**, the family's primary callset (source `clair3`), declared as
`snv.per_sample` with `source_format: clair3`
([per_sample_small_variants.py](../backend/app/services/per_sample_small_variants.py)):

- The files are read side by side, each sorted in the order of its `##contig` lines. A file whose
  contigs are listed in another order than the others' fails the dataset before anything is
  written. A record out of that order is found only as the files are read, once writing has begun
  (an `overwrite` has then already deleted the stored callset): it fails the dataset there, the
  rows already written are removed, and the family is put back or flagged as for any failed
  dataset ([The import job](#the-import-job)).
- Each file must hold one sample column, and that column must be the entry's sample
  (`per_sample_vcf_column`): the partner's file under an entry is refused. So is a file without a
  record (*"… holds no record"*), whose first record is read before anything is deleted (it used
  to be imported beside the other's, its sample left without a call in the callset).
- The records of one site (chromosome, position, REF and ALT) become one row holding the call of
  each sample whose file has a record there. Two records of one position with other alleles
  (`A>G`, `A>G,T`) are two rows; a multi-allelic record is not split.
- `exclude_filters` (DeepVariant's `RefCall` and `NoCall`) leaves out a site where every record is
  excluded. Where one partner has a variant, the other partner's excluded record is kept as their
  call: a `RefCall` (0/0) says the caller read them as reference, a `NoCall` (./.) that it could
  not. The dataset summary counts them (`excluded_calls_kept`).
- A partner whose file has no record at a site had no read evidence of the variant that the caller
  reported. The genotype filters (a genotype class including reference) and the Sample QC read it
  as reference, as a joint VCF calls a covered site; a site without reads is read the same way,
  which the Sample QC notes.
- Each record's QUAL, FILTER and caller metrics are the call's own (`calls.filters`,
  `calls.metrics`). The row's FILTER holds every record's; its annotation is that of the first
  file with a kept record there.
- The callset replaces the family's primary callset, joint or per-sample: the import must bring
  the file of every sample with calls in it, or it is refused before anything is deleted (*"The
  family's callset also holds the calls of …"*). With `update`, the dataset is skipped when the
  family holds a primary callset.

A monogenic NIPT pair's per-sample files are read differently ([below](#monogenic-nipt-pairs)).
Without `analysis_type: monogenic_nipt` and without `source_format: clair3`, a per-sample SNV
callset is refused (`dataset_per_sample_unsupported`), so a NIPT pair whose analysis type was left
out is never read as germline calls.

**The NeedlR files** go under `sv_needlr.per_sample`. NeedlR has no sample column; each record
names its query in `Query_ID`, after the caller's input (`<sample>_sv_phased`). A file whose query
names another sample is refused before anything is written, as is a file without a record; a query
naming no known sample is the entry's. One event called in both partners is one SV with both
calls, whatever IDs their files give it ([above](#where-each-dataset-lands)).

### Validation

Discover, dry run and import check:

- **Folder and manifest**: both exist; the manifest is a file in the folder, not a symlink out of
  it or one that loops (`manifest_outside_package`); `schema_version` is 1.
- **IDs**: the family ID (the manifest's `family_id`, else the folder name; for Discover, the
  request's `family_id` first) and every sample ID are printable text without spaces, and no
  path. Sample IDs are checked in the PED (for Discover also a parent the PED names but lacks as
  a member), under `family.add_members` (as a member or a parent), and in a long-read package's
  per-sample folder names. The whitespace around an ID is stripped. An ID is refused
  (`family_id_invalid`, `sample_id_invalid`), and nothing else of the package is read until it is
  corrected (Discover drafts no manifest), when it holds:
  - a control character (C0, `\x00`–`\x1f`: a line break, a tab, an escape; DEL, `\x7f`; or C1,
    `\x80`–`\x9f`) or whitespace;
  - another character a reader cannot see (SEC-15): a text-direction character (U+200E, U+200F,
    U+202A–U+202E, U+2066–U+2069, U+061C), with which `FAM\u202e100` shows as `FAM001`; a
    zero-width character (U+200B–U+200D, U+2060, and U+FEFF, also the byte-order mark a text file
    can start with); or any other character that is not printable (a soft hyphen, a private-use
    or unassigned code point, a file name that is not UTF-8);
  - a `/` or a `\` anywhere, or when it is `.` or `..` (SEC-15), which a folder named after the ID
    and a URL's path segment read as a separator, the folder itself or its parent. `..` inside a
    longer ID (`F..1`) is part of one name, and is kept.

  Such an ID would reach the pedigree, the report, the audit trail, the logs, file paths and the
  app's links; two IDs that look the same would name two records, and a NUL cannot be stored at
  all. The message shows the ID with each invisible character escaped (`\n`, `\x1b`, `\u200b`),
  says what is wrong and where the ID comes from, and states the rule
  ([family_identifiers.py](../backend/app/services/family_identifiers.py)). An ID stored before a
  part of the rule came in is not changed (the data are synthetic); an import or an edit that
  gives it is refused like any other. On a case-insensitive file system (a macOS bind mount in
  development) two family IDs that differ only in case share every folder named after an ID; the
  rule leaves case alone, since production storage is case-sensitive
  ([development.md](development.md)).
- **PED**: it parses as six-column PED, holds one family and matches `family_id`; sample IDs are
  unique and every parent is in the PED. An import into an existing family may lack the PED file:
  the family's stored pedigree is used. A manifest without `ped` must name its members under
  `family.add_members` (`ped_missing_path`).
- **Members**: the manifest's samples and per-sample entries name samples in the PED or in
  `family.add_members`; an added member is not already in the PED; every `family.relationships`
  entry names members.
- **Phenotypes**: a phenotype file named in the manifest exists and uses `format: hpo_tsv`.
- **Files**: every file the manifest names lies in the package (a path that leads out of it,
  starts with `~` or runs through a symlink loop is refused with 400, see
  [the manifest](#the-manifest)) and exists (for a package in a bucket, an alignment exists in the
  bucket). A compressed family VCF has its index (`.tbi`, `.csi` or `.idx`, next to it or given as
  `index:`); a plain `.vcf` needs none.
- **Per-sample SNV callsets** (`snv.per_sample` without `family_vcf`) are read for a family with
  `analysis_type: monogenic_nipt`, or as the primary callset of germline calls made one sample at
  a time when the dataset says `source_format: clair3` (otherwise
  `dataset_per_sample_unsupported`). Each VCF holds exactly one sample
  (`dataset_vcf_not_single_sample`) and needs no index. A VCF whose header cannot be read (a
  `.vcf.gz` cut short or damaged, or a plain-text file named `.gz`) is refused by its sample and
  file name (`dataset_vcf_unreadable`); the validation used to fail with a 500 (DATA-11). A
  NIPT pair's VCF whose one column is named neither as its member nor as its entry's
  `vcf_sample` is a warning (`nipt_vcf_column_named_otherwise`,
  [Monogenic NIPT pairs](#monogenic-nipt-pairs)).
- **Coverage**: a per-target coverage table's header names `chromosome`, `start`, `end`,
  `attribute` and `mean` (`coverage_target_table_columns`). A coverage BED's `content` is
  `read_depth` or `log2_ratio` (`coverage_content_invalid`); a BED without one is a warning
  (`coverage_content_undeclared`) and imports as `unknown`.
- **Records**: a data file the import refuses when it holds no record (a family VCF of `snv`,
  `sv_needlr`, `repeats_trgt` or `apcad`; a sample's SNV, NeedlR, TRGT or chrM VCF; an embryo's
  APCAD file; a sample's coverage BED) holds a line after its header. One with nothing after it is
  a warning (`dataset_file_without_record`): the import fails that dataset with the stored data as
  it was ([A file without a record](#where-each-dataset-lands)). The check reads each file up to
  its first line after the header, so a line the loader cannot read as a record passes it (a
  BED's `track` line, a column header). A HiFiCNV VCF or a chrM SV VCF without a call is a result
  and is not warned of; a per-target coverage table is checked for its columns instead. (A dry run
  used to report none of these files: SAFE-3; the APCAD files and coverage BEDs since its last
  part.)
- **Datasets**: every dataset key is a known one.

A missing `snv` or `sv_needlr` dataset is a warning; other datasets are optional, without a
warning. A bad phenotype row (an unknown person, HPO ID or status) is a warning: the row is
skipped and never blocks the import.

The API applies part of the ID rule to every request that looks an ID up. A family or sample ID
in a request's path, or in its `family_id` or `sample_id` query parameter, that holds a C0 control
character or DEL is refused with 400 once the caller is signed in, before any lookup, with the
same answer for every caller. So is any other path parameter with one, and a NUL in any query
value: a `%00` in a URL arrives as a NUL, which Postgres cannot compare
([security-posture.md](security-posture.md#1-authentication--rbac)).

## 5. Pedigrees and phenotypes

- `POST /ped/manual`: an analyst of the project, or an admin, creates a family from typed-in
  members; a reader of the project is refused (403).
- `POST /ped/upload`: admins upload a PED file, with an optional region of interest, inheritance
  model and carrier lists. Roles are read as in a package's PED: a member another member names as
  its father or mother takes that role; the first affected member no member names as a parent is
  the proband, wherever the parents' rows stand (with none affected, the first member not named
  as a parent); every other member is a sibling.
- Only admins can replace an existing family or sample (`overwrite=true`).
- Replacing a family deletes it and its small and structural variants.
  - Its phenotypes go with it, each recorded as removed on the family's clinical audit trail
    before the family is deleted ([clinical-traceability.md](clinical-traceability.md)).
  - The variants go last, once the family has been written again, just before the commit: a
    replacement refused before that leaves the family as it was, its variants included. What can
    still refuse it then: a sample ID another family has (409); a phenotype other than `0`, `-9`,
    `1` or `2`, carriers the PED does not name, the inheritance model or the region of interest
    (400 or 404); the database's own constraints (500). A six-field header line such as
    `#FID IID PAT MAT SEX PHENOTYPE` is read as a family, and refused for its phenotype.
  - Before its row is touched, the replaced family is marked for its small and structural
    variants (`deletion-` and an id, SAFE-31). A commit that fails, or a process that stops, after
    their delete brings the family back without them, and marked
    ([below](#the-mark-of-an-upload-that-did-not-finish)). The
    commit removes the replaced family and its mark; a replacement refused before the delete
    removes the mark.
- Both endpoints refuse (400), before anything is written, a family or sample ID (a PED's parent
  IDs too) that the ID rule refuses, as a package import does ([Validation](#validation)): one
  that is not printable text without spaces (a control character, whitespace, a text-direction or
  zero-width character), holds a `/` or a `\`, or is `.` or `..`. The whitespace around a typed-in
  ID is stripped.
- The edits of an existing family refuse such an ID the same way: a member's new sample ID, the
  father or mother a member edit names, and a member the structure edit adds
  ([family-member-management.md](family-member-management.md)).

PED column 6 is the phenotype: `0` or `-9` unknown, `1` unaffected, `2` affected. CoGA stores it as
the member's clinical status and keeps carrier state apart (`carrier_status`: unknown,
not_carrier or carrier; `carrier_type`: obligate, proven, reported or inferred). A package PED may
add columns after the sixth, such as `role=embryo`, `role=relative`, `carrier=true` or
`carrier_type=obligate`.

The PED stays coarse. Detailed phenotypes are HPO terms per person, stored apart from the
pedigree. A package brings them in two ways:

- `phenotypes.file` with `format: hpo_tsv`: a tab-separated file with the columns `family_id`,
  `individual_id`, `hpo_id` and `status` (`present`, `absent` or `unknown`), and optionally
  `label`, `onset`, `evidence`, `source` and `note`;
- `individuals.<sample>.hpo.present`, `.absent` and `.unknown` in the manifest.

A row naming an unknown person, or an HPO term that is not loaded, is skipped with a warning. The
**HPO terms** and **Notes** fields on the Package Import page go into the manifest's `metadata`
and are kept with the import record; they are not per-person phenotypes.

What an import changes of a family's phenotypes goes on the family's clinical audit trail, as made
by the user who ran the import: one `phenotype` event per member whose phenotypes it added or
changed, naming the package and its phenotype file, with what it added and what it changed
(before and after). An import that changes no phenotype (the same package again, say) writes
none. A failed overwrite whose restore puts the phenotypes back records that too, as does a failed
import that removes the family it created ([clinical-traceability.md](clinical-traceability.md)).

After import, phenotypes are edited per person (`/families/{family_id}/hpo`,
`/families/{family_id}/members/{sample_id}/hpo`), each edit a `phenotype` event on the trail too.
Family and member edits are in [family-member-management.md](family-member-management.md); how the
haplotype risk colours are derived is in
[haplotype-segregation-analysis.md](haplotype-segregation-analysis.md).

## 6. Single-file uploads

Admins can also load one file at a time on the **Upload family and sample data** page
(`/upload-data`). Each upload refuses to replace existing data unless `overwrite=true`; the page
asks before it sends that.

| Upload | Endpoint | Stored in |
| --- | --- | --- |
| Family small variants (VCF) | `POST /families/{family_id}/small-variants/upload` | ClickHouse |
| Structural variants (VCF) | `POST /structural-variants/upload/{sample_id}` | ClickHouse |
| Repeat expansions (TRGT VCF) | `POST /repeat-expansions/upload/{sample_id}` | Postgres |
| Interval tracks (BED) | `POST /bed/upload/{sample_id}/{bed_type}` | ClickHouse |

A file stored as one sample's is read from that sample's own column (`per_sample_vcf_column`): a
TRGT upload, a Sniffles or Spectre structural-variant upload, and a package's per-sample
`repeats_trgt`, `mito` (its chrM VCF and its chrM SV VCF) and `cnv` (HiFiCNV) files.

- The column the shared sample-name rules resolve to the sample (`<sample>_sort`,
  `<sample>_sv_phased`) is read, at any position, so a family TRGT, Sniffles or chrM VCF can serve
  each member in turn.
- A single column that names no stored sample (a caller's placeholder, such as HiFiCNV's
  `Sample0`) belongs to the sample.
- A file whose columns name other samples and none this one (a family member, or another
  family's sample: sample ids are unique), or that has two columns for it, is refused before
  anything is written: an upload with 400, a package dataset as failed.
- A package entry's `vcf_sample` is the operator's recorded word: the column it names is read or,
  set to the entry's own sample, the file's one column. A lab that verified its tubes and maps a
  sample to a file named after another tube sets it.

Each stored TRGT call records its column (`metadata.vcf_sample`).

A repeat-expansion upload loads one sample's TRGT calls. It is refused (409) when the sample has
TRGT calls; `overwrite=true` replaces them. The file is read, and each call built, before
anything is replaced or recorded. A file that is not text or whole gzip, has no column for the
sample, or holds no record for it (*No valid TRGT records found*: TRGT writes a record for every
locus of its catalogue, so a file without one is a header alone, or cut short) is refused (400),
with the stored calls and the family's records as they were. (A file without a record used to
replace the calls with none, answer 200, and count as bringing them again.)

`bed_type` is `coverage`, `segments`, `apcad` or `apcad_pcf`. Haplotype blocks cannot be uploaded
as a BED; they come from a GLIMPSE2 small-variant upload (`source_format=glimpse2`) or package.

An interval-track upload loads one sample's track of `bed_type` from a BED under the source `web`,
and checks and replaces only that source's track:

- A coverage BED declares what it holds in the `content` query parameter, `read_depth` or
  `log2_ratio` (the upload form asks, with no default). A coverage upload without it, or another
  track type with it, is refused (422) before anything is read. The track's record, the raw
  file's record (`raw_import_files.metadata`) and what the upload records for the family keep it,
  and it decides how the track is drawn and whether it is read as depth
  ([Where each dataset lands](#where-each-dataset-lands); TRACE-9). The request's audit row names
  the parameter (`AUDIT_LOG_QUERY_STRING_MODE=keys` keeps the key, `sanitized` its value too).
- The track's record also says what loaded it: `uploaded_from: web` for a single-file upload,
  `family_package` for a package's coverage BED. Both are stored under the source `web`, and a
  sign-out freezes, per coverage track, what it holds and where it came from (`coverage_tracks`,
  [clinical-traceability.md](clinical-traceability.md#sign-out)).
- The file is read and checked whole first: one that is not text, or holds no record of the track
  type, is refused (400), and no track or record is replaced. A line with a negative start or end
  is no record, and is left out, as a package's track readers do.
- The upload is refused (409) only when rows a BED brought of that track type are stored for the
  sample: a BED upload's, or a package's coverage BED's, which share the source. It reads the
  rows, not Postgres's record of the track, which a failed overwrite's restore can leave behind
  its rows; such a record is replaced with the upload's own.
- `overwrite=true` replaces those rows and their record. Other callers' tracks (WisecondorX,
  QDNAseq, HiFiCNV, a package's APCAD or PCF tables) stay.
- The upload marks the family before its first write to ClickHouse, for the sample's track, and
  its commit removes the mark. A write that fails after that (the delete of the replaced rows, or
  an insert), or a process that stops there, leaves the track missing or partly written, and the
  family marked until the track has been uploaded again with `overwrite`
  ([below](#the-mark-of-an-upload-that-did-not-finish)).

Deleting a sample's track type on **Admin → Family & sample data** removes every caller's track of
that type.

A family small-variant upload is one family VCF from one callset:

- `source_format` is `clair3` (a directly called callset), `glimpse2` (imputed genotypes, which
  also make the haplotype blocks), `mito` (chrM calls) or `auto`, the default, which tells
  `clair3` and `glimpse2` apart from the first record. Any other value is refused (422) before
  anything is stored. Only Package Import writes the `nipt` callset of a monogenic NIPT pair, one
  file per sample ([Monogenic NIPT pairs](#monogenic-nipt-pairs)); a combined father and plasma
  VCF uploads as an ordinary callset.
- The upload is refused (409) when the family already has calls from that callset or, for imputed
  genotypes, haplotype blocks. `overwrite=true` replaces them; every other callset stays.
- With `overwrite`, the file is read and checked whole before anything is replaced: each record
  is built as it would be stored and, for imputed genotypes, the haplotype blocks are made, but
  nothing is written. A file the upload refuses or cannot read leaves the stored callset, the
  haplotype blocks and their records untouched. Refused: a file naming a sample the family does
  not have, or holding no record it can store (400); a gzip file cut short or damaged (400; a BGZF
  file without its end-of-file block before any of it is read, [above](#where-each-dataset-lands));
  a line over the length limit (413). Only then is the stored data deleted and the file read again
  and written, streamed through both reads, never held in memory. With nothing stored to replace,
  the file is read once.
- A value its ClickHouse column cannot hold, or that is no number, is stored as missing and never
  fails the upload: a GQ above 65,535 or written `inf`, a negative phase set, a QUAL beyond the
  largest Float32. A read depth is kept as written up to 4,294,967,295 reads, so a mitochondrial
  or amplicon call keeps its depth (a depth above 65,535 used to fail the upload with a 500). A
  record whose POS is negative or beyond 4,294,967,295 has no position: it is skipped and counted
  with the malformed records (`skipped_malformed`), and a file of such records alone holds no
  record the upload can store (400). The rule for every column is in
  [database.md](database.md#clickhouse-tables).
- A genotype (`GT`) is stored as written, in VCF's allele notation: allele indices, `.` for a
  missing allele, `/` or `|` between them (`0/1`, `1|0`, `./.`, haploid `1`). A record whose `GT`
  holds anything else (a word such as `HET`, `HETEROZYGOUS`, `HOM` or `REF`, or bases such as
  `A/G`) is refused (400), naming the file, the line and the sample, with the value shortened:
  *The VCF family.vcf, line 1204: the genotype (GT) of S2 is 'HET', which is not VCF's allele
  notation (such as 0/1, 1|0 or ./.)*. Only a malformed VCF holds one (the owner's decision of
  2026-10-28, CLIN-16). A package's `snv`, `haplotypes` and `mito` datasets, a NIPT pair's files
  and a long-read couple's files fail their dataset the same way. A dataset that reads its file
  once, after the delete (an `overwrite`), is put back as for any failed dataset; a long-read
  sample's word in a file's first record is found before anything is deleted. (Such a word used
  to be stored and read three ways: as a no-call by every filter, count and inheritance check, as
  `0/1` on the variant list, and, `HET` only, as het by the compound-het check.) A record a FILTER
  value leaves out (`exclude_filters`) is not read, and nothing of it is stored.
- A record's annotation is read from its VEP `CSQ` (or snpEff `ANN`) entries, and from its INFO
  where an entry has no value (`variant_annotation_parser.py`). A population frequency or count is
  read only from a field that names its source: gnomAD's (`gnomAD_AF`, `gnomADe_AF`,
  `gnomADg_AF`, `gnomAD_AF_POPMAX`, `gnomAD_AC`, `gnomAD_nhomalt`, `gnomAD_Hom`, `gnomAD_Hemi`,
  and the exome and genome counts `gnomADe_…` and `gnomADg_…` of `AC`, `nhomalt`, `Hom` and
  `Hemi`, in the spellings the parser lists), TOPMed's (`TOPMed_AF`), and VEP's `MAX_AF` and
  `POPMAX_AF`.
  - A gnomAD count is gnomAD's total, as its browser shows it: the combined count, else the exome
    and genome counts added, either alone when the other is missing (CLIN-67; the owner's choice
    of 2026-10-27). Before, `gnomADe_nhomalt` and `gnomADg_nhomalt` were not read, and the allele
    count took the exome count alone.
  - The INFO's `AC`, the callset's own allele count, is never read as gnomAD's, nor are `AC_Adj`,
    `nhomalt` and `hemi` without a prefix. (Before CLIN-59, a record whose CSQ had no gnomAD count
    took them, so a maximum gnomAD AC filter filtered on the family's own count.)
  - A variant without a population value has none, and a maximum frequency or count filter keeps
    it, as absent from gnomAD. With a count filter applied, the variant list says how many of the
    variants it shows have no such count (`gnomadCountNotes.ts`): a VCF annotated with gnomAD's
    frequencies alone (VEP's `--af_gnomad…`) carries no counts, so a count filter excludes
    nothing.
  - A package's `snv` dataset, its VEP table and the per-sample callsets are read the same way. A
    callset stored before keeps the counts it was stored with until it is uploaded or imported
    again.
- A write that still fails after that delete (ClickHouse, which has no transactions, failing
  part-way) answers 500 with the callset gone, and the rows the write had stored are removed again. The family is then flagged import-incomplete for what the upload was to bring
  ([What an upload records](#what-an-upload-records)): every family page shows *Import
  incomplete*, and its report is signed out only with an acknowledgement, until the callset has
  been uploaded or imported again. Until then, after such a failed upload of imputed genotypes,
  Postgres still records the deleted haplotype blocks, and the parents' phase corrections stay.
- Before its first write to ClickHouse (that delete or, with nothing stored, its first insert) the
  upload marks the family for the callset; its commit removes the mark. A process that stops after
  that write runs none of the clean-up above: the mark stays, and the family reads as incomplete until the callset has been uploaded or imported again with
  `overwrite` ([below](#the-mark-of-an-upload-that-did-not-finish)).
  A write that fails after the delete turns the mark into the flag above; one with nothing stored
  to replace removes it once its own rows are removed again.
- A package's `snv` and `haplotypes` datasets load their family VCF through the same loader, but
  with `overwrite` read it once: the import puts an existing family back from its snapshot when a
  dataset fails ([The import job](#the-import-job)).
- The answer says what was stored: the records loaded and skipped, the callset, the haplotype
  blocks made, and the tool versions the VCF header names, which also go into the family's
  annotation manifest.

A structural-variant upload loads one sample's calls from one caller, and checks and replaces
only that caller's calls:

- `source_format` is `sniffles`, `spectre`, `manual` (a TSV, stored as source `manual_upload`) or
  `auto`, the default, which works the caller out from the file. Any other value is refused (400).
- A Sniffles or Spectre VCF is read from the sample's own column, by the rule above. Refused (400)
  before anything is read or replaced: a file with no column for the sample (another member's, or
  a joint VCF without it) or with two, and a VCF without exactly one `#CHROM` line before its
  records. A manual TSV has no sample column: each row is the sample's call. Nor has it an SVLEN,
  so the SV length filters measure a TSV call by its alleles when its REF and ALT are sequences
  (below), and otherwise by its END minus POS: an insertion written `N`, `<INS>` with its END at
  its POS is 0 bp to them.
- **End.** A Sniffles or Spectre record without END, or with END `.`, ends where VCF 4.4 puts it:
  at POS + SVLEN (its sign dropped) for a `<DEL>`, `<DUP>`, `<INV>` or `<CNV>` allele, subtypes
  included, and at POS + the length of REF − 1 for any other allele (an `<INS>`, a breakend, a
  sequence allele).
  - One of the four without an SVLEN (and without END) does not say how far it reaches: it is left
    out and counted (`skipped_without_extent`) in the upload's result, as in the import's dataset
    summary of a package's NeedlR, HiFiCNV or chrM Sniffles2 file. (It was stored on its POS, as
    0 bp, so a gene or panel search missed the genes it spans: CLIN-39, the owner's decision,
    2026-10-27.)
  - A file whose every record is so is refused: an upload answers 400 saying why, and a HiFiCNV
    or chrM SV file, where a file without a call is an answer, fails its dataset with the earlier
    calls as they were (a NeedlR file without a call fails it anyway).
  - An SV is listed with the genes its POS–END span overlaps, so a deletion written with SVLEN
    alone is found by a search for a gene it deletes.
  - A written END is kept as written, except a translocation's whose INFO/CHR2 names another
    chromosome: that END is its mate's position (below), and the record ends on its own
    breakpoint.
  - A package's NeedlR, HiFiCNV and chrM Sniffles2 files end a record by the same rule (CLIN-39);
    they ended it at POS + SVLEN for any type, or at POS + 1 (NeedlR, HiFiCNV) or POS (chrM)
    without SVLEN.
- **Type.** A record without SVTYPE, or with SVTYPE `.`, takes the type its ALT states, as VCF
  4.4 deprecates SVTYPE as redundant with ALT: a symbolic allele's name (`<DUP:TANDEM>` is
  `DUP:TANDEM`), BND for breakend notation (`N]5:3000000]`, or a single breakend such as `G.`),
  INS or DEL for a sequence longer or shorter than REF, and `SV` when the ALT states none. A
  package's NeedlR, HiFiCNV and chrM Sniffles2 files take a missing type the same way.
- **Length.** A record without SVLEN, or with SVLEN `.` (VCF 4.4 writes SVLEN for a symbolic
  allele only), written as a sequence allele (REF and ALT bases of other lengths) is stored with
  the length its alleles state: len(ALT) − len(REF), the bases an insertion inserts or, as a
  negative number, those a deletion deletes, as Sniffles2 and pbsv write a deletion's SVLEN
  (CLIN-41; the owner's decision, 2026-10-14).
  - The SV list, its CSV export and the report sentence show that length, and the length filters
    measure it. Measured by END − POS, such an insertion was 0 bp and a minimum length dropped it.
  - A written SVLEN is kept as written. A symbolic allele, a breakend, or alleles as long as each
    other without one are still measured by END − POS. The record's END and its SV id do not
    change.
  - A package's NeedlR, HiFiCNV and chrM Sniffles2 files are stored by the same rule, so a
    sequence deletion written without END or SVLEN is as long there as its alleles, where it was
    one base (NeedlR, HiFiCNV) or of no length (the mtDNA page). An SV stored before this rule
    keeps the length it was stored with until its file is uploaded or imported again.
  - A translocation (BND, TRA) has no length: the list, its export and the report show none (they
    showed 0 or 1 bp); the length filters give it none, as before.
  - The filters read a length typed with a minus sign without it, as they measure: an exact length
    copied from a deletion in the list (`-5000`) matched nothing (CLIN-39).
- **Mate.** A breakend keeps its mate, the position its ALT names in brackets: `N]5:3000000]` has
  its mate on 5:3000000. A record is read so when its ALT is written in breakend notation,
  whatever its SVTYPE says, and when its type is BND or TRA, in any capitals. A translocation
  without breakend notation (`<TRA>`, as Sniffles 1 writes one) has its mate in INFO/CHR2, where
  that names another chromosome, and its END.
  - The SV list and its CSV export show the mate in their *Remote chr* and *Remote start* columns,
    beside the locus (empty for an SV without a mate). The report sentence and the report's locus
    line under it name it as the remote breakpoint. The *Remote chr* and *Remote start ≥* filters
    find the breakend by it.
  - Every reader reads the mate so: the uploads, and a package's NeedlR, HiFiCNV and chrM Sniffles2
    files. (The uploads read a mate only for a type written `BND`, so a `bnd`, or a TRA in breakend
    notation, had none; the HiFiCNV and chrM files read none; and a TRA's END was stored as the SV's end on its own chromosome,
    where the tracks and Circos drew it: CLIN-39.)
  - A breakend's id holds its mate, an uploaded's and a NeedlR breakend's alike
    ([above](#where-each-dataset-lands)).
- **Copy number.** A call keeps its copy number (`FORMAT/CN`), which gives a CNV its gain or loss.
  (Without it an uploaded CNV counted as a possible deletion in the second-hit badge and ranked as
  a loss, whatever it called: CLIN-39.)
- **Two calls.** Two calls of one position, type and mate with other alleles or another length are
  two SVs, as in a NeedlR import ([above](#where-each-dataset-lands)): the SV this source already
  stores under the id keeps it, matched by its length (an upload stores no alleles). The same
  event twice in the file is one call of the sample.
- The upload is refused (409) only when the sample already has calls from the same source.
- `overwrite=true` replaces that sample's calls from that source. The other samples' calls from
  it, and every other source's SVs (a package's `needlr` or `hificnv` calls, another caller's
  upload), stay.
- To do so the upload rewrites that source's rows of every sample: it deletes them, then writes
  them back with the sample's calls changed. A write that fails after the delete (ClickHouse
  failing part-way) writes the deleted rows back and answers 500, with every sample's calls as
  they were. Before the rewrite the upload marks the family for that
  caller's calls of each sample the rows hold; its commit, or a failure whose rows were put back,
  removes the mark. A rewrite that cannot put them back either, or a process that stops in it,
  leaves the family marked until each of those samples' files has been uploaded again with
  `overwrite` ([below](#the-mark-of-an-upload-that-did-not-finish)).
- A record whose POS or END is negative or beyond 4,294,967,295 cannot be placed and is skipped,
  as is one whose POS is no number. An SVLEN, a read support, a phase set or a QUAL its column
  cannot hold is stored as missing, as in a package's NeedlR, HiFiCNV or chrM Sniffles2 file
  ([database.md](database.md#clickhouse-tables)). (Such a value used to fail the upload with a
  500.)
- The caller the VCF header names is recorded in the family's annotation manifest
  ([annotation-provenance.md](annotation-provenance.md)).

Deleting a sample's structural variants on **Admin → Family & sample data** removes its calls from
every source, as does deleting the sample with its data. These deletes and an upload rewrite the
stored rows, and write every call they do not change back as stored: with its phase set and
breakend end, in its project, an inactive member's included. A delete whose rewrite fails after
its delete writes the deleted rows back and answers 500. Each marks the family before its first
write to ClickHouse. One that cannot put the rows back either, or whose process stops, leaves the
family marked for each SV dataset it holds, the deleted sample's excepted (for the deletion of a
sample with its data, also for what else it reached:
[below](#the-mark-of-an-upload-that-did-not-finish)).

### What an upload records

Each upload records what it brought in the family's `imported_scopes`, as a package import
records what it imported, in the transaction that commits the upload. A failed overwrite whose
restore fails part-way then names it among what the family holds, and the family stays marked
import-incomplete until it has been brought again ([The import job](#the-import-job)). An upload
is recorded as the package dataset that brings the same data, so an overwrite of either brings
again what the other brought; where no package dataset brings it, under a name of its own:

| Upload | Recorded as |
| --- | --- |
| Family small variants | `snv`, with the callset it stored (`clair3` or `mito`; `auto` as it resolved): what a package `snv` dataset stored as that callset brings (its `source_format`, or `auto` as the import resolved it). Imputed genotypes (`glimpse2`), with their haplotype blocks: `haplotypes`, as a package's imputed family VCF |
| Structural variants | `sv_sniffles`, `sv_spectre` or `sv_manual`, for the sample: no package dataset brings them |
| Repeat expansions (TRGT) | `repeats_trgt`, for the sample: what a package's TRGT file of that sample brings |
| Interval tracks (BED) | `bed_coverage`, `bed_segments`, `bed_apcad` or `bed_apcad_pcf`, for the sample: a package dataset that writes such a track may read another kind of file into it, or write another track. A coverage BED's scope also says what it declared it holds (`content`), as the package `coverage` dataset's says each sample's (`unknown` without the manifest's `content`); a later record of the same sample's BED replaces it, and no overwrite compares it (TRACE-9) |

An upload with `overwrite` also removes from such an entry what it brought again: the same
dataset, for the same sample and (for small variants) the same callset. One without `overwrite`
removes nothing, as a package import's `update` removes nothing. A package `snv` dataset that
leaves `source_format` at `auto` is recorded as the callset its file holds
([The small-variant callset an import records](#the-small-variant-callset-an-import-records)), so
it and an upload bring again each other's callset when they stored the same one. One recorded as
`auto` names no callset: no upload brings it again.

An upload made before uploads were recorded is not in `imported_scopes`. A restore that fails
part-way does not name its data, so the gate opens once the rest of the family has been imported
again, though that data may be missing. Upload such data again to record it.

A small-variant upload that fails after deleting the callset it replaces
([above](#6-single-file-uploads)) records nothing of what it brought and strikes nothing off an
entry. It flags the family import-incomplete (`families.metadata.import_incomplete`) for what it
was to bring, as an import flags a failed dataset: under the dataset and scope of the table
above, with no import job, merged with a flag already there. Each upload that completes removes
from the flag what it brought again (the same sample and, for small variants, the same callset),
as an import that imports a failed dataset again does; the flag goes with the last. An upload
without `overwrite` counts too: it is refused while any of that data is stored, so it brought it
whole.

The repeat-expansion and interval-track uploads hold the family's write locks from their first
read until they commit, as the small-variant and structural-variant uploads do
([database.md](database.md#one-write-at-a-time-per-family)). Such an upload waits while a package
import of the family runs, which would otherwise undo it when it puts the family back, and a
sign-out is refused while one runs. An admin's delete of a sample's interval track or repeat calls
holds them too; such a restore used to undo it. The demo loader (`scripts/load_demo_quartet.py`)
loads through the same uploads and records what it loads as they do (it used to record nothing).

An interval-track upload with `overwrite` that fails after its delete also removes Postgres's
record of the track, in a transaction of its own: the record's delete was the upload's and rolled
back with it, so Postgres named rows that were gone. A package dataset that replaces a caller's
track clears that caller's rows whether or not Postgres records them (it used to clear them only
when it did, and such rows stayed beside the new ones).

### The mark of an upload that did not finish

ClickHouse has no transactions, and an upload that replaces stored data deletes it before writing
the file's: a process that stops in between (a restart, a crash, out of memory) runs none of the
upload's clean-up. So the small-variant, structural-variant and interval-track uploads
mark the family before their first write to ClickHouse, as an import does
([The import job](#the-import-job)). The mark is an entry in `families.metadata.import_unfinished`
under a key of its own (`upload-` and an id), with no import job, written and committed apart from
the upload (whose transaction holds the family's write locks until it commits). It lists what the
upload may leave partly written or missing, each dataset with its scope as the table above
records it, one sample at a time:

- a small-variant upload: its callset (`snv` with the callset, or `haplotypes`);
- a structural-variant upload: its caller's calls (`sv_sniffles`, `sv_spectre` or `sv_manual`) of
  every sample whose calls the rewrite deletes and writes back, its own sample's among them;
- an interval-track upload: the sample's track (`bed_coverage`, …).

The upload's commit removes the mark. A failure removes it when nothing is missing: the upload
failed before its first write, the structural-variant rewrite put the stored rows back, or a
small-variant upload with nothing stored removed its own rows again. A small-variant upload that
had deleted its callset turns the mark into the import-incomplete flag
([above](#what-an-upload-records)). Otherwise the mark stays: the upload stopped, or failed
leaving data missing or partly written. An upload that cannot write its mark writes nothing. The
repeat-expansion upload writes Postgres alone, in the transaction that deletes what it replaces,
so a stop rolls its delete back; it writes no mark.

A deletion of the family's data that deletes or rewrites rows in ClickHouse marks the family the
same way before its first write there (`deletion-` and an id, SAFE-31), under the family's write
locks and before its own transaction touches the family row. It lists each dataset the family
holds (`imported_scopes`) whose data the deletion may leave partly written or missing, one sample
at a time. A sample that is no longer the family's is left out, as nothing can bring its data
again. Which stores hold a dataset's data is fixed per dataset:

| Store | Datasets |
| --- | --- |
| Small variants | `snv`, `haplotypes`, `mito` |
| Structural variants | an SV upload's, `sv_needlr`, `cnv`, `mito` |
| Interval tracks | a BED upload's, `haplotypes`, `wisecondorx`, `qdnaseq`, `apcad`, `pcf`, `coverage`, `cnv` |
| Postgres alone (the deletion's transaction rolls them back) | the repeat calls, paraphase results, QC, run record and phenotypes |

- An admin's deletion of a sample with its data deletes the sample's tracks, then rewrites the
  family's small and structural variants without the sample's calls, before the sample's Postgres
  rows go. Its mark lists every small-variant and structural-variant dataset of the family, which
  it rewrites, and the sample's tracks. Each rewrite puts the rows it deleted back when it fails
  (the small variants' as the structural variants' do, [above](#6-single-file-uploads)).
- An admin's deletion of a sample's structural variants rewrites every source's SVs without the
  sample's calls: its mark lists each SV dataset of the family except the sample's own, whose calls
  were to go.
- An admin's deletion of a family, a PED upload or manual family that replaces it (with
  `overwrite`), and a structure change that clears its data delete its variants and tracks before
  their commit: their mark lists each dataset held in those stores. The replacement runs the
  structure change's deletes; it used to leave the replaced family's tracks in ClickHouse, under a
  family that was gone (SAFE-35). Postgres's records of the tracks go with the family.

The deletion's commit removes the mark; a deletion of the family, or a replacement, removes the
family and the mark with it. A deletion of a sample also removes the sample's items from every
mark and every entry a restore left, since nothing of a sample that is gone can be missing. It
removes the sample from the import-incomplete flag and from the entry of an import that stopped
too: a failure, or an entry's dataset, whose scope named that sample alone goes; one that named
other samples too keeps them; one of a family-level file stays, as the others' part of it may
still be missing. The flag, or the entry, goes with its last item. No import or upload can
bring a deleted sample's data again, so these used to stay for good (SAFE-35).

A deletion that fails rolls its transaction back, and leaves the mark listing exactly what its
writes may have left partly written or missing, given how far it got:

- what a delete that began may have removed;
- what a rewrite that could not put its rows back may have removed (every sample's);
- what a rewrite that completed removed (the sample's: its own items, and its part of a dataset
  held in one family-level file, such as the direct calls of a family VCF, as an item that names
  the sample).

A rewrite that put its rows back leaves nothing; a deletion that failed before its first
ClickHouse write removes the mark. A process that stops leaves the mark as written, listing all it
may reach. A deletion that cannot write its mark deletes nothing. The admin's deletions of a
sample's tracks of one type, and of a family's small variants, delete exactly what was asked and
touch nothing else: they write no mark.

A mark gates sign-out as an import's entry does, and is frozen into the signed record with its
key, which says what wrote it. It reads:

| Where | Single-file upload | Deletion |
| --- | --- | --- |
| *Import incomplete* banner, every family page | "A single-file upload (…) began rewriting this family's data and has not finished: it is still running, or it stopped or failed part-way. … may be partly written or missing" | "A deletion of this family's data (…) began and has not finished: it is still running, or it stopped or failed part-way" |
| Sign-out refusal | "a single-file upload (…) did not finish, so … may be partly written or missing", listed as *Not finished (single-file upload)* | "a deletion of this family's data (…) did not finish", listed as *Not finished (deletion of this family's data)* |
| Signed record | "A single-file upload (begun …) had not finished: …" | "A deletion of this family's data (begun …) had not finished: …" |

The banner asks to upload or import again, with overwrite, what the mark did not finish. Each
upload with `overwrite` removes from a mark what it brought again (the same dataset, for the same
sample and, for small variants, the same callset), as does an import that completes with
`overwrite`; the mark goes with the last. One without `overwrite` removes nothing, as an `update`
removes nothing. The import job's claim and its heartbeat read the jobs alone
(`family_import_jobs`), so a mark, which names no job, is never taken for an import that runs or
stopped.

### The NIPT artifact list

The recurrent-artifact list of monogenic NIPT is kept per assembly and assay
(`nipt_artifact_variants`; see [monogenic-nipt.md](monogenic-nipt.md)). Admins maintain it through
the API; there is no screen for it. Besides adding or removing one allele
(`POST /admin/nipt/artifacts`, `DELETE /admin/nipt/artifacts/{artifact_id}`) and seeding it from
the cohort's cfDNA samples (`POST /admin/nipt/artifacts/auto-seed`), an admin can import the
NIPT-M pipeline's recurrent table with `POST /admin/nipt/artifacts/import`: a multipart form with
the table as `file`, the `assembly_id`, and the `assay_key` (default `nipt_cfdna`; a list for a
capture panel takes the panel's key, the `assay_panel` of its cfDNA samples).

- The table is tab-separated with a header line. It needs a `variant_key` column
  (`CHROM:POS:REF:ALT`) or the columns `CHROM`, `POS`, `REF` and `ALT`; without them it is refused
  (400). With a `filter_as_recurrent_artifact` column, as in the pipeline's
  `recurrent_cfdna_artifact_filter_*.tsv`, only the rows it marks TRUE are read; without it, every
  row is an artifact.
- Each allele is listed as `curated`, with `n_cfdna_families` as its recurrence count and the
  label `recurrent (<recurrent_filter_profile>)`. An allele already on the list keeps its source
  and label and takes the table's count.
- The auto-seed's protections hold. Not listed: an allele whose annotation on the assembly has a
  gnomAD or TopMed frequency above 5%, or a ClinVar record that may assert pathogenic
  (pathogenic, likely pathogenic or conflicting). An allele no family on the assembly carries has
  no annotation there to check, and is listed. Each family's analysis checks its listed alleles
  again with its own annotation, and keeps a common or ClinVar-pathogenic one, flagged
  `artifact_list_protected`. An allele without any annotation is not protected: review the list.
- The answer counts the rows read, those the table does not flag (`not_flagged`), those it could
  not read (`invalid`, such as a multi-allelic `ALT`), the alleles listed (`imported`), and those
  refused as common (`protected_common`) or for ClinVar (`protected_clinvar`).
- An import that lists alleles is one clinical audit event, `nipt_artifacts_imported`, on the
  artifact list's own chain: it names the file and every allele it listed
  ([clinical-traceability.md](clinical-traceability.md)). An import that lists none records no
  event.

## 7. Troubleshooting

| Message or symptom | Cause |
| --- | --- |
| `package_folder_not_allowed` | The folder is not under `FAMILY_IMPORT_ROOTS`, or its path cannot be followed: it runs through a symlink loop, or starts with `~` for a home folder the server does not have. |
| A folder in the import folder is not in the package list | It holds no manifest, no PED (at its top or in `ped/`) and no long-read per-sample folders, or the import refuses it: a symlink leading out of `FAMILY_IMPORT_ROOTS`, or one that loops. Type its path and validate it to see the reason (`package_folder_not_allowed`). |
| `family_id_invalid` | The family ID holds a control character, whitespace, a text-direction, zero-width or other non-printable character, a `/` or a `\`, or is `.`, `..` or empty ([Validation](#validation)). The message says where it comes from (the manifest's `family_id`, the folder name, the request's `family_id`, the PED): correct it there. |
| `sample_id_invalid` | A sample ID breaks the same rule. The message says where it comes from (the PED, `family.add_members`, a per-sample folder's name, the existing family): correct it there. |
| `manifest_missing` | The folder has no `manifest.yaml`, `.yml` or `.json`. Run Discover and Write manifest (local folders only). |
| `manifest_outside_package` | The folder's `manifest.yaml` (`.yml`, `.json`) is a symlink leading out of the folder, or one that loops. Put the manifest itself in the folder. Write manifest answers 400 with the same message. |
| `package_folder_link` (warning) | The package's `ped/` folder, a long-read dataset folder, a sample's folder in one, or a folder in that is a symbolic link. The message says where it leads, relative to its import folder, or that it lies outside the import folders. The package list and Discover follow it (a member may come from it): check that what it holds belongs to this family, or put the files themselves in the package. |
| "Package manifest path '…' starts with '~'", "… runs through a symlink loop", "Package manifest paths must stay within the package directory" (400) | A path in the manifest, or the PED path given to Discover, names no file in the package. Write it relative to the package folder, without `~`, and replace a symlink on it that leads out of the package or loops. An import job of such a package ends failed with the message. |
| `manifest_schema_version_unsupported` | `schema_version` is not 1. |
| `ped_family_mismatch`, `ped_multiple_families` | The PED's family ID differs from `family_id`, or it holds more than one family. |
| `dataset_vcf_index_missing` | A compressed VCF has no `.tbi`, `.csi` or `.idx`. |
| `manifest_added_member_in_ped` | A member under `family.add_members` is in the PED already; set its states under `family.members`. |
| `manifest_relationship_unknown_member` | A `family.relationships` entry names someone in neither the PED nor `family.add_members`. |
| "Sample '…' not found in family" (haplotypes) | The phased VCF has a sample the family lacks, such as a PGT index the PED does not hold; add it under `family.add_members`. |
| `existing_family_or_samples` | The family or a sample exists; choose `update` or `overwrite`. |
| A job stays `queued` | No worker is running: the API runs none (`FAMILY_IMPORT_WORKER_COUNT=0`, as with Docker Compose) and no import-worker process is up. Check `docker compose ps import-worker` and `docker compose logs import-worker`: a process that refuses to start says why (another schema revision, variant tables of an older row identity, `FAMILY_IMPORT_WORKER_COUNT` 0 for the worker). |
| "Not run: the account that requested this import has been deactivated …", "Not run: the account that requested this import is no longer an administrator …", "Requesting user no longer exists" (a job) | The account that queued the job was deactivated, given another role than administrator, or deleted before a worker ran it; a job runs only as the active administrator who queued it. Nothing was written: queue the import again as an active administrator. |
| "sample column(s) … match no sample in the family" | Set `vcf_sample` on the dataset. |
| "… has no sample column for X: '…' is Y" | A per-sample file names another sample: point the entry at X's own file or, if the file is X's after all, set `vcf_sample` on X's entry. An upload (TRGT, Sniffles, Spectre) takes X's own file. |
| "… VCF has no #CHROM line", "… has a record before its #CHROM line", "… has more than one #CHROM line" | An uploaded Sniffles or Spectre VCF has no header line naming its sample columns before its records, or two files were joined into one: upload the sample's VCF as the caller wrote it. |
| `nipt_pair_detected` (warning) | Discover took the folder for a monogenic NIPT pair. Check the samples it took for the maternal plasma and for the father. |
| `dataset_per_sample_unsupported` | `snv.per_sample` in a family that is not `analysis_type: monogenic_nipt` and does not say `source_format: clair3`. For the long-read pipeline's per-sample calls set `source_format: clair3`; else give a joint VCF as `snv.family_vcf`. |
| `ped_missing_path` | The manifest names no PED and no members under `family.add_members`. |
| `ped_proposed_from_folders` (warning) | Discover found no PED and took the members from the long-read per-sample folders (a couple when of opposite sex). Check them before writing the manifest. |
| "The family's callset also holds the calls of …" | A per-sample SNV import that does not bring every sample's file would drop the others' calls: import every sample's SNV file together. |
| `dataset_vcf_not_single_sample` | A VCF under `snv.per_sample` (a NIPT pair's, or a long-read sample's) holds no sample column or more than one. |
| `dataset_vcf_unreadable` | A sample's VCF under `snv.per_sample` (or a NIPT pair's parent VCF, in Discover) could not be read: a `.vcf.gz` cut short or damaged in a copy, or a plain-text VCF named `.gz`. The message names the sample and the file. Copy the file again from where the pipeline wrote it. An import that meets such a file fails its dataset with "… could not be read: it is cut short or damaged, or is not gzip". |
| `nipt_vcf_column_named_otherwise` (warning) | A NIPT pair's VCF names its one sample column neither as the member nor as the entry's `vcf_sample`; the import reads it as the member's calls ([Monogenic NIPT pairs](#monogenic-nipt-pairs)). Check that the file holds the member's verified tube: if so, set the entry's `vcf_sample` to the column's name; if not, correct the manifest. |
| `coverage_target_table_columns` | A per-target coverage table's header lacks `chromosome`, `start`, `end`, `attribute` or `mean`. |
| `coverage_content_undeclared` (warning) | A coverage BED's manifest entry has no `content`: it imports as `unknown`, drawn without an axis and never read as depth. Add `content: read_depth` or `content: log2_ratio` and import again. |
| `coverage_content_invalid` | A coverage BED's `content` is neither `read_depth` nor `log2_ratio`. |
| "Declare what the coverage BED holds: content=read_depth or content=log2_ratio" (BED upload, 422) | A coverage BED was uploaded without `content`. Choose what it holds in the form, or pass `content`. |
| A coverage track drawn *without an axis*, or *"… is not read as depth"* in the mtDNA QC notes or the NIPT coverage check | The coverage BED declares nothing (`unknown`; so does a BED stored before BEDs declared what they hold), or holds log2 ratios. Upload or import it again with what it holds. |
| A per-target coverage import fails on `sample_interval_track_sources_track_type_check` | The Postgres database is older than the `target_coverage` track type; reset it ([database.md](database.md)). |
| "VCF file must be plain text or gzipped" (small-variant upload, 400) | The file is neither text nor whole gzip: a `.vcf.gz` cut short in a copy, or damaged. Copy it again; a refused upload with `overwrite` kept the stored callset. |
| "The VCF …, line …: the genotype (GT) of … is '…', which is not VCF's allele notation (such as 0/1, 1\|0 or ./.)" (small-variant upload, 400, or the `snv`, `haplotypes` or `mito` dataset; a long-read sample's file: "The SNV file of … (…), line …: the genotype (GT) is …") | A record's `GT` is a word (`HET`, `HOM`, `REF`) or anything else VCF's allele notation does not use ([section 6](#6-single-file-uploads)): the file is malformed. A refused upload with `overwrite` kept the stored callset; a package dataset failed, and the import put the family back as for any failed dataset. Correct the file, or the tool that wrote it, and import or upload it again. |
| "… is not valid gzip: it is truncated (it does not end with BGZF's end-of-file block)" (an upload, 400, or a package dataset) | The file is BGZF (bgzip's `.gz`) and its last block is not BGZF's end-of-file block: it was cut short, perhaps exactly where a block ends, which `gzip -t` passes ([A BGZF file cut where one of its blocks ends](#where-each-dataset-lands)). Nothing was replaced. Copy the file again from the pipeline's output. |
| *Import incomplete* for `snv` or `haplotypes`, with no import job | A small-variant upload with `overwrite` failed after deleting the callset it replaced ([section 6](#6-single-file-uploads)). Upload the callset again, or import it. |
| *Import incomplete*: "A single-file upload … began rewriting this family's data and has not finished" | An upload is still running, or one stopped (a restart, a crash, out of memory) or failed after its first write to ClickHouse and could not put back what it deleted ([the mark](#the-mark-of-an-upload-that-did-not-finish)). Once no upload of the family runs, upload again with `overwrite` each dataset and sample the banner names (or import it again with overwrite); each leaves the mark as it is brought again. |
| *Import incomplete*: "A deletion of this family's data … began and has not finished" | A deletion of the family's data (an admin's deletion of a sample, of its structural variants or of the family; a PED upload or manual family that replaced it; a structure change that cleared its data) is still running, or stopped or failed after its first write to ClickHouse ([the mark](#the-mark-of-an-upload-that-did-not-finish)). Once it no longer runs, upload or import again, with `overwrite`, each dataset and sample the banner names; or, for a sample's deletion, delete the sample again: once it is gone its own items go. |
| "No valid TRGT records found" (TRGT upload, 400, or the `repeats_trgt` dataset: "The TRGT VCF of … (…): No valid TRGT records found" for a sample's file, "The family TRGT VCF (…): No valid TRGT records found for …" for a family VCF) | The TRGT VCF holds no record for the sample (a family VCF: for the samples it names): a header alone, or a file cut short in a copy. Nothing was replaced. Use the sample's complete TRGT VCF. |
| "… holds no record of the … track", "… holds no interval on a primary chromosome …", "… holds no segment", "… holds no APCAD value for …" | A package's track file holds nothing its reader can store: an empty or truncated pipeline output, or another file than the manifest means. Its dataset failed before the track was deleted ([Where each dataset lands](#where-each-dataset-lands)). Correct the file and import again. |
| "Paraphase JSON for … holds no gene result", "The NeedlR file of … holds no structural variant", "The SNV file of … holds no record" | That sample's file holds no result; its dataset failed before the sample's data was replaced. |
| "No mitochondrial call was replaced: the chrM VCF of … holds no variant record" (the `mito` dataset) | A sample's chrM VCF holds no variant record: a failed analysis or a file cut short. Every sample's mitochondrial calls are as they were; rerun the analysis and import its VCF. |
| "No mitochondrial call was replaced: the chrM SV VCF of … holds … record(s), none on chrM" (the `mito` dataset) | The entry's `sv_vcf` is another SV callset than Sniffles2's chrM calls, such as the sample's genome-wide SV VCF. Point it at the chrM SV VCF. |
| `dataset_file_without_record` (warning) | A data file holds nothing after its header. The import will fail its dataset and keep the stored data as it was: use the complete file. |
| "No valid BED records found" (BED upload, 400; the `coverage` dataset, "The coverage BED of … (…): No valid BED records found"; the `apcad` dataset for an embryo's VCF without a record, read as a BED, "The APCAD file of … (…), read as a BED: No valid BED records found") | The BED holds no record: an empty or truncated pipeline output. A dataset's message names the sample and the file by its path in the package (it used to name neither: "400: No valid BED records found"). The dataset checks every sample's file before it replaces a track, so every sample's track is as it was; an upload replaced nothing. Correct the file and import or upload it again. |
| "TRGT VCF line … holds a value that cannot be read where a number is expected; nothing was stored" (TRGT upload, 400, or the `repeats_trgt` dataset) | A record of the TRGT VCF holds a position, motif count, allele length or purity that is no number, or a position beyond any chromosome: a damaged file or another format. Nothing was replaced. |
| "Paraphase JSON for … holds a value that cannot be read where a number is expected (total_cn of …); nothing was stored" (the `paraphase` dataset) | That gene's copy number in the sample's Paraphase JSON is beyond what Postgres's `integer` holds: a damaged file or another format. No sample's Paraphase results were replaced. |
| "The NIPT SNV file of … holds no record; no NIPT call was replaced" | A monogenic NIPT's per-sample SNV file holds nothing after its header. Neither file's calls were replaced. |
| Empty viewers or gene search | The assembly's reference data is missing (section 1). |
| dbNSFP `missing` on every gene | The dbNSFP gene file is not at `GENE_REFERENCE_DBNSFP_GENE_PATH`. |

## 8. Demo data and helper scripts

Synthetic demo families and their loader are described in [demo/README.md](../demo/README.md);
the helper scripts are listed in [scripts/README.md](../scripts/README.md).

## Optional ancestry PCA after a full-WGS import

The exploratory ancestry PCA is outside the intended purpose ([ancestry-pca.md](ancestry-pca.md)).
A GRCh38 package can declare its family VCF a complete, joint, directly called whole genome for
it:

```json
{"metadata":{"ancestry":{"full_wgs":true,"genome_build":"GRCh38","absent_record_is_hom_ref":true,"allow_unfiltered":false}}}
```

Once the import has completed, it registers the VCF as the family's WGS source; it never queues
a calculation, and a source it cannot register leaves a line in its log without failing the
import. Present no-calls, failed filters, split or duplicate loci and other alleles are never
taken as reference. A retained small-variant upload can be registered from the ancestry page
instead, without importing it again.
