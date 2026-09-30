# Data import — reference

Data import is how a case enters CoGA: you create the family and its samples, then load the assay data
that the rest of CoGA interprets. There are two ways in — building a pedigree by hand, and importing a
prepared folder package — and everything you load belongs to a genome assembly whose reference data must
already be present.

The short version is in the [user guide](/docs), section *Case setup and data import*. The technical
detail (file formats, manifest fields, configuration) is kept by the bioinformatics team.

---

## The two ways in

| Page | Who | What it does |
| --- | --- | --- |
| **Family Builder** | any user; PED upload for administrators | Build a new family by hand: add samples, set sex and roles, link parents and couples, set affected and carrier status. An administrator can also upload a PED file. Metadata only: no data files. |
| **Package Import** | administrators | Point at a family folder on the CoGA server, generate its manifest, validate it, then import the family **and all its data** in one job. |

**What needs an administrator.** Any user can build a new family by hand. Uploading a PED file,
replacing an existing family, editing an existing family's members or structure, adding HPO terms,
setting the region of interest, and loading or replacing data are for administrators.

The PED carries only affected or unaffected. Detailed phenotypes are HPO terms on each person, added in
the family member dialog.

---

## Reference data comes first

Genes, cytobands and the clinical annotation layers belong to an **assembly**. A viewer that cannot
place coordinates, or a gene lookup that comes back empty, almost always means that the reference data
for the assembly is missing. Load it before you load a family.

- **At first start** CoGA creates *Homo sapiens / GRCh38* and loads its cytobands (from UCSC) and genes
  (from GENCODE, with the UCSC gene table as a fallback) when they are missing. If the download fails,
  the assembly is created empty, to be filled later.
- **Clinical CNVs and segmental duplications** are loaded at start-up only when their source files are
  present on the server. Otherwise an administrator loads them. An empty *Clinical CNV explorer* means
  they are not loaded for that assembly.
- In the **Reference catalogue** an administrator can set up another assembly (cytobands and genes are
  downloaded automatically) and upload files for cytobands, genes, the blacklist, clinical CNVs and
  segmental duplications. Everyone else sees the catalogue read-only.

**Clinical CNV knowledgebase.** The clinical CNVs can come from a knowledgebase that CoGA builds from
ClinGen, UCSC and ClinVar. An administrator rebuilds it with the ↻ button next to the clinical-CNV count
in the Reference catalogue. The build also counts, for each clinical CNV, the pathogenic or likely
pathogenic ClinVar copy-number variants (losses and gains) that overlap it by at least 30% both ways —
the **ClinVar P/LP** column of the Clinical CNV explorer. A knowledgebase built without ClinVar shows
"—" there, which means *not recorded*, not zero.

**Gene reference refresh.** From the Reference catalogue an administrator can refresh the cached gene
information for all human genes. The refresh reads a local dbNSFP gene file and the downloads of HGNC,
ClinGen, GenCC and ClinVar for every gene, and asks NCBI Gene for a summary of the genes that dbNSFP does
not cover. It adds identifiers, names and aliases, disease
context (OMIM, Orphanet, GenCC), ClinGen dosage, HPO and pathway terms, expression and constraint
metrics.

---

## What each kind of data unlocks

Each layer is optional and switches on part of CoGA.

| Data | Unlocks |
| --- | --- |
| **Small variants (SNV/indel)** | the small-variant page, its review, and the Sample QC page |
| **Structural variants** | the structural-variant page, its review, CNV classification and the variant summary |
| **Repeat expansions (TRGT)** | the repeat-expansion page, scored against the STRchive loci |
| **Paraphase** | resolution of paralogous genes and segmental duplications |
| **Mitochondrial calls** | the mtDNA analysis (homoplasmy and heteroplasmy) |
| **Coverage, segments, APCAD, haplotypes** | the tracks in the genome overview and chromosome view |

Several of these also feed the [Sample-integrity QC](/docs/reference/sample-qc) and the versions
recorded for the [clinical report](/docs/reference/clinical-traceability).

---

## Package import

A package is one family folder on the CoGA server, holding the PED, the data files and a manifest that
maps them. The folder must sit under an import folder the server is configured for; it is **not** a
folder on your own computer. On a cloud installation the import folder is a storage bucket. CoGA then
copies the package for the import, except the aligned reads (CRAM/BAM), which stay in the bucket; the
genome browser reads them there. A bucket package must already hold its manifest.

A standard package looks like this. Only the PED is needed to start: CoGA writes `manifest.yaml` for
you. Every data folder is optional.

```text
FAM001/
  manifest.yaml
  family.ped
  snv/        family.annotated.vcf.gz (+ .tbi)
  needlr/     family.sv.annotated.vcf.gz (+ .tbi)
  repeats/    family.trgt.vcf.gz (+ .tbi) | FAM001_tr.vcf
  wisecondorx/SAMPLE1/{bins,segments}.bed
  QDNAseq/    EMBRYO1/{bins,segments}.csv
  apcad/ APCAD/ PCF/   APCAD tracks and PCF segments
  GLIMPSE2/   FAM001.vcf.gz          (haplotypes)
  paraphase/  SAMPLE1.paraphase.json
```

Long-read packages (nf-core/lrsvar) are laid out per sample and are recognised by the same scheme:

```text
pacbio/
  manifest.yaml
  pacbio.ped
  bams/       HG002.cram (+ .crai)                          alignments
  snv/        HG002/annotation/HG002_annot.vcf.gz (+ .tbi)  VEP-annotated
  sv/         HG002/annotation/HG002_sv_phased.needLR.4.0.vcf.gz
  cnv/        HG002/annotation/HG002_annot.vcf.gz + HG002.*.copynum.bedgraph
  mito/       HG002/HG002.vcf.gz + annotation/HG002/HG002_snv_annot.txt
  repeats/    HG002/HG002_tr.vcf.gz (+ .csi)
  paraphase/  HG002/HG002.paraphase.json
  qc/         nanoplot/HG002/*NanoPlot-report.html + *NanoStats.txt, depth/HG002/*.mosdepth.summary.txt
  pipeline_info/  software_versions.yaml, params_*.json
```

CoGA knows several usual file names per layer, so packages from slightly different pipelines are still
found; **Discover manifest** shows what it found. When a caller names a VCF's sample column after its
input file (`Sample0`, `HG002_sort`) rather than the sample, CoGA binds a single-sample VCF to the sample
it was declared for. A column that matches no sample in the family stops the import instead of being
dropped silently.

### The steps

1. **Family folder.** Pick the folder from the list of families found in the import folder (or type
   its path). The PED is found automatically.
2. **Family destination.** Choose *New family* or *Existing family*, and the **Existing data policy**
   (table below).
3. **HPO terms and notes** for the family (optional). To add a phenotype file or HPO terms per person,
   edit the manifest before you write it.
4. **Discover manifest.** CoGA reads the PED, looks for the expected files and shows a draft manifest
   with a table of the data it found.
5. **Write manifest.yaml** (edit it first if needed).
6. **Validate, then import.** With **Dry run** ticked, **Validate package** checks everything without
   writing anything. When it is clean, untick it and press **Start import**.
7. **Follow the job** under *Recent family imports*: status, log, validation errors, warnings and a
   summary per dataset.

| Existing data policy | What happens when the family or a sample already exists |
| --- | --- |
| Cancel if family or samples exist | the import stops |
| Update: add missing datasets only | the data is added to the existing family; datasets that already hold data are skipped |
| Overwrite imported dataset rows | the data is added to the existing family; the selected datasets are replaced |

### What validation checks

- The folder and the manifest exist.
- The PED is a six-column PED with **one family**, whose identifier matches the manifest's (by default
  the folder name). Sample identifiers are unique, and every parent is a sample in the same PED.
- Every sample and every per-sample dataset in the manifest is a sample in the PED.
- Every file exists. A compressed family VCF for small variants, structural variants or repeats needs
  its index (`.tbi`, `.csi` or `.idx`); a plain `.vcf` does not. Other index files are checked only when
  the manifest names one.
- A dataset type CoGA does not know is an error. A missing small-variant or structural-variant dataset
  is a warning; other missing datasets are simply absent.
- A phenotype row that names an unknown sample or HPO term is skipped with a warning; it never blocks the
  import.

### After the import

- **Sequencing QC** appears per sample in the **Family members** table as a chip with the QC verdict and
  the mean depth. Hover it for every metric and the cut-offs; click it to open the pipeline's QC report.
  The chip turns amber or red when a metric crosses a warning or error cut-off. Cut-offs are set per assay
  under **Admin → Sequencing QC Thresholds**; none ship by default, and a metric without a cut-off reads
  as *not assessed*, not as a pass.
- **Analysis pipeline settings** — the pipeline's configuration (genome build, which caller produced
  what, VEP cache, repeat catalogue, stages run) is recorded at import. It shows in a panel on the family
  page and in the same section of the clinical report. Tool versions are in the report's provenance
  footer.
- **A dataset that failed** ends the job as *failed*. If the family is left partly loaded, every family
  page shows *Import incomplete* until an import completes it, and its report can be signed out only
  with an acknowledgement ([Report traceability & sign-out](/docs/reference/clinical-traceability)).

---

## Single uploads

Outside the package flow, an administrator can upload one layer at a time on the **Upload family and
sample data** page: family small variants (including GLIMPSE2 haplotype VCFs), structural variants,
repeat expansions, and interval tracks (coverage, APCAD, APCAD PCF segments, segments).

A structural-variant file holds one sample's calls from one caller: **Sniffles VCF**, **Spectre VCF** or
**Manual TSV**, or **Auto detect**, which works the caller out from the file. CoGA keeps each caller's
calls apart:

- The upload stops only when the sample already has calls from the same caller, and the page asks
  before it replaces them.
- Replacing them changes only that sample's calls from that caller. The other samples' calls, and the
  calls of every other source (a package's NeedlR or HiFiCNV calls, another caller's upload), stay as
  they are.
- The caller and its version, as the file's header names them, are added to the family's annotation
  versions.
- Uploads from two callers that report an SV with the same type and breakpoints each keep their own
  call: the SV list shows the SV once per caller, with that caller's genotypes, and both rows share
  the SV's review, tags and note.

Deleting a sample's structural variants on **Admin → Family & Sample Data** removes its calls from every
source. The upload and this delete leave every other call as it was, with its phase, including the calls
of a member removed from the family.

---

## Recommended order for a new setup

1. The species, assembly and project.
2. The reference data for that assembly.
3. The family: Family Builder, a PED upload, or a package import.
4. Small and structural variants.
5. Repeat expansions and the interval tracks.

---

## Common pitfalls

- **Empty viewers or gene lookups** are usually missing reference data, not a failed import. Check the
  assembly's reference data before re-importing the family.
- **The folder path** is a path on the CoGA server, under a configured import folder — not a path on your
  own machine.
- **Only administrators load data.** Other users build new families by hand.
- **Missing index.** A compressed small-variant, structural-variant or repeat VCF without its index
  fails validation.
- **Restructuring a family that has data.** Once data is loaded, changes to members or relationships are
  blocked unless you confirm that the imported data may be cleared; the family is then reloaded with the
  new structure. Changes to affected or carrier status apply at once, but re-review the saved
  interpretations.
- **A missing phenotype.** Phenotype rows with unknown samples or HPO terms are skipped with a warning:
  check the job summary if an expected term is missing.
