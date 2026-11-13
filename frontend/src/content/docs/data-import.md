# Data import — reference

Data import is how a case enters CoGA: you create the family and its samples, then load the assay data
the rest of CoGA interprets. There are two ways in: build a pedigree by hand, or import a prepared
folder package. Everything you load belongs to a genome assembly whose reference data must already be
present.

The short version is in the [user guide](/docs), section *Case setup and data import*. The
bioinformatics team keeps the technical detail (file formats, manifest fields, configuration).

---

## The two ways in

| Page | Who | What it does |
| --- | --- | --- |
| **Family builder** | the analysts of a project (role analyst or higher), superusers and administrators; PED upload for administrators | Build a new family by hand: add samples, set sex and roles, link parents and couples, set affected and carrier status. An administrator can also upload a PED file. Metadata only: no data files. |
| **Package import** | superusers and administrators; overwriting an existing family for administrators | Check, correct and approve the family folders the pipelines deliver, or add a folder by hand; the family **and all its data** are then imported in one job. |

**What needs an administrator.** An analyst of a project (role analyst or higher) can build a new
family in it by hand, and add, change and remove a family's HPO terms; a viewer, or a reader of the
project, can do neither. A superuser can do both in every project, and imports packages: a new family,
or the missing datasets of an existing one (*Update*). Only administrators upload a PED file, replace an
existing family (with a PED file, the family builder or a package import with *Overwrite*), edit an
existing family's members or structure, set the region of interest, and upload single data files.

**Replacing a family** with a PED file or the family builder deletes its small and structural variants,
once the family has been saved again. A replacement refused before then leaves the family and its
variants as they were (for example a sample ID another family has, or a PED phenotype other than 0, -9,
1 or 2, such as a header line's). Its HPO terms go too: before the family is deleted, each is recorded
as removed in the clinical audit trail, by whoever replaced the family.

**Family and sample IDs** are printable text without spaces, however a family comes in or is edited.
Spaces around an ID are dropped. An ID is refused before anything is saved when it holds:

- a space or a control character (a line break, a tab, an escape);
- another character a copied ID can carry without showing it (one that turns the text's direction, a
  zero-width one);
- a `/` or a `\`, or when it is `.` or `..`: an ID is no path. A `..` inside a longer ID, such as
  `F..1`, is fine.

Such a character would reach the pedigree, the report and the audit trail, and two IDs that look the
same would name two families. The message shows each character you cannot see as a code such as
`\u200b`.

The PED carries only affected or unaffected. Detailed phenotypes are HPO terms on each person, added in
the family member dialog or brought in by a package import. Either way, each change is in the family's
audit trail (on its report), naming who made it.

---

## Reference data comes first

Genes, cytobands and the clinical annotation layers belong to an **assembly**. A viewer that cannot
place coordinates, or an empty gene lookup, almost always means the assembly's reference data is
missing. Load it before you load a family.

- **At first start** CoGA creates *Homo sapiens / GRCh38* and loads its cytobands (from UCSC, or the
  copy CoGA ships when UCSC cannot be reached) and genes (from GENCODE, with the UCSC gene table as
  fallback) when they are missing. If the download fails, the assembly is created empty, to be filled
  later.
- **Segmental duplications** load at start-up when their source file is on the server, into GRCh38
  only: the file CoGA ships holds GRCh38 coordinates. Another assembly gets them from an upload.
- **Clinical CNVs** are built in the background after the first start, for a GRCh37 or GRCh38
  assembly that has none: the clinical CNV knowledgebase rebuild, which an administrator can also run
  (*Species & assemblies*), or upload a file. An empty *Clinical CNV explorer* means they are not
  loaded for that assembly yet.
- The segmental-duplication file and the HPO ontology must be the files CoGA ships, recognized by their
  SHA-256. Another file is not loaded, and the server log says which file and how to fix it.
- Projects, families and genes name an assembly by its build (GRCh38). The version the *Reference
  catalogue* shows (GRCh38 *p14*) is the release of the reference data CoGA loaded, not the reference
  a family's data was aligned to: that one is among the family's annotation versions, as its
  *Reference assembly*.
- In the **Reference catalogue** an administrator can set up another assembly (cytobands and genes are
  downloaded automatically) and upload files for cytobands, genes, the blacklist, clinical CNVs,
  segmental duplications and DGV variants. Everyone else sees the catalogue read-only.

**Clinical CNV knowledgebase.** CoGA can build the clinical CNVs from ClinGen, UCSC and ClinVar. An
administrator rebuilds it with the ↻ button next to the clinical-CNV count in the Reference catalogue.

- The build counts, for each clinical CNV, the pathogenic or likely pathogenic ClinVar copy-number
  variants (losses and gains) that overlap it by at least 30% both ways: the **ClinVar P/LP** column of
  the Clinical CNV explorer. A knowledgebase built without ClinVar shows "—" there, meaning *not
  recorded*, not zero.
- A rebuild fails when its ClinGen dosage curation or recurrent CNV regions cannot be loaded. The
  Reference catalogue says why, and the clinical CNVs stay as they were.
- The recurrent CNV regions come from ClinGen's recurrent CNV file, release V2.1, recognized by its
  SHA-256 for each assembly. Another file fails the rebuild the same way. The other sources are read as
  published on the day of the rebuild.
- A rebuild records the files it read as the knowledgebase's release, which a signed analysis freezes:
  the recurrent CNV file by release and SHA-256, and each other file (the ClinGen dosage curation, the
  cytobands, ClinVar, Orphanet, a DECIPHER map) by name and SHA-256.

**Gene reference refresh.** A new installation refreshes the cached gene information for all human
genes in the background after its first start, and again once the dbNSFP gene file is added, if
its first refresh ran without it. From the Reference catalogue an administrator can refresh it at
any time. The refresh reads a local dbNSFP gene file, the ACMG
secondary-findings list that ships with CoGA (ACMG SF v3.3), and the HGNC, ClinGen, GenCC and ClinVar
downloads for every gene. It asks NCBI Gene for a summary of the genes dbNSFP does not cover. It adds
identifiers, names and aliases, disease context (OMIM, Orphanet, GenCC), ClinGen dosage, HPO and pathway
terms, expression and constraint metrics, and whether the gene is on the ACMG secondary-findings list.

---

## What each kind of data unlocks

Each layer is optional and switches on part of CoGA.

| Data | Unlocks |
| --- | --- |
| **Small variants (SNV/indel)** | the small-variant page, its review, and the Sample QC page |
| **Structural variants** | the structural-variant page, its review and CNV classification |
| **Repeat expansions (TRGT)** | the repeat-expansion page. A call is scored against STRchive's thresholds for the locus its TRID names: STRchive's locus ID (`HD_HTT`) or, for a gene with one STRchive locus, the gene (`HTT`). For a gene with several STRchive loci, one per repeat tract (ARX, HOXA13), the gene's name picks the locus the call's region overlaps. A call reads *Not assessed*, never normal, when its TRID names no catalogued locus, its locus has no thresholds, or its region overlaps none of its gene's loci, or several. Exception: a count inside the benign range STRchive states for a locus without thresholds (POLG: 10) reads normal |
| **Paraphase** | resolution of paralogous genes and segmental duplications |
| **Mitochondrial calls** | the mtDNA analysis (homoplasmy and heteroplasmy, and chrM deletions and duplications) |
| **Coverage, segments, APCAD, haplotypes** | the tracks in the genome overview and chromosome view |

Any variant data (small or structural variants, repeat expansions, Paraphase or mitochondrial calls)
also switches on the family's **Variant summary**, with a tab for each kind the family holds.

TRGT's repeat count need not be the clinical repeat number the catalogue's ranges count. TRGT counts
the motif across the region its own catalogue gives a locus:

- at FMR1 it leaves out the AGG interruptions (an allele reads 1 to 3 below its clinical number);
- at HTT it counts the CAG after the CAA interruption (one above);
- at POLG it counts four flanking GCT copies (the common 10-repeat allele reads 14).

QA can record a count correction per locus (admin page **Repeat catalogue**): it is added to TRGT's
count before the count is classified, and TRGT's own count stays shown beside the corrected one. None is
recorded in advance. Until QA validates a locus's counting units, the repeat page and the genome tracks
mark a count within 3 repeats of a boundary there as *Near a boundary*, without changing its status. See
the [user guide](/docs), section *Structural variants, repeats, Paraphase and mtDNA*.

A small variant's population frequencies and gnomAD counts come only from annotation fields that name
their source (gnomAD's, TOPMed's, and VEP's `MAX_AF`), never from the callset's own allele count, the
VCF's `AC`. gnomAD's popmax (grpmax in gnomAD v4) and VEP's `MAX_AF` (the highest frequency over the
1000 Genomes and gnomAD populations) are kept apart; the popmax filter reads gnomAD's, else `MAX_AF`.
A field that lists several different numbers (one per transcript, record or allele) is read as no value,
and its text is shown under *More annotations*. A record with two ALT alleles is stored as one variant,
and each transcript of it names the ALT its annotation is of; split such records upstream
(`bcftools norm -m -any`) to filter and classify each allele on its own. A gnomAD count is gnomAD's total: its combined count, or else its exome and genome counts
(`gnomADe_…`, `gnomADg_…`) added. A variant whose annotation gives no gnomAD count has none. A maximum
gnomAD count filter keeps it, as absent from gnomAD; the variant list then says how many of the
variants it shows were kept that way.

Several of these also feed the [Sample-integrity QC](/docs/reference/sample-qc) and the versions
recorded for the [report](/docs/reference/clinical-traceability).

---

## Package import

A package is one family folder on the CoGA server, holding the data files and the pipeline's package
file (`coga-package.json`), which names the family, its members and its datasets. The folder must sit
under an import folder the server is configured for; it is **not** a folder on your own computer. CoGA
reads a package where it lies, and writes nothing into it; the genome browser reads its aligned reads
(CRAM/BAM) from the package folder. A folder without a package file is added by hand: CoGA then makes
its package from the folder's PED and layout (*Add a folder by hand*, below).

A standard package looks like this. Every data folder is optional.

```text
FAM001/
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

Long-read packages (nf-core/lrsvar) are laid out per sample and recognised by the same scheme:

```text
pacbio/
  pacbio.ped
  bams/       HG002.cram (+ .crai)                          alignments
  snv/        HG002/annotation/HG002_annot.vcf.gz (+ .tbi)  VEP-annotated
  sv/         HG002/annotation/HG002_sv_phased.needLR.4.0.vcf.gz
  cnv/        HG002/annotation/HG002_annot.vcf.gz + HG002.*.copynum.bedgraph
  mito/       HG002/HG002.vcf.gz + annotation/HG002/HG002_snv_annot.txt, HG002/HG002_sv.vcf.gz
  repeats/    HG002/HG002_tr.vcf.gz (+ .csi)
  paraphase/  HG002/HG002.paraphase.json
  qc/         nanoplot/HG002/*NanoPlot-report.html + *NanoStats.txt, depth/HG002/*.mosdepth.summary.txt
  pipeline_info/  software_versions.yaml, params_*.json
```

A couple screened for carriership on the long-read pipeline has **no PED** and no joint call set. Each
partner has their own files (`snv/A/annotation/A_annot.vcf.gz`, `sv/A/needlr/A_sv_phased.needLR.4.0.vcf.gz`,
`repeats/A/A_tr.vcf.gz`, `paraphase/A/A.paraphase.json`). *Add a folder by hand* lists such a
folder. Added by hand, CoGA:

- takes the members from the per-sample folders, each with the sex TRGT genotyped its repeats with;
- proposes two members of opposite sex as a couple (the female partner as mother, the male as father);
- names them as the package's members, so no PED is needed.

The delivery says what CoGA proposed: check both partners, and their sex, before you approve it
(**Correct…** changes them). The two SNV files become one call set. A site both partners
carry is one variant with both calls. A partner whose file has no record at the other's variant is read
as reference, as a joint VCF would call a covered site. DeepVariant's reference and no-call records are
kept only where the other partner has a variant.

A trio on the long-read pipeline (nf-LRSvar) has a PED, one family call set and a folder per sample for
its alignment:

```text
FAM001/
  family.ped
  snv/        FAM001_phased.vcf.gz (+ .tbi), FAM001_annot.tsv.gz   DeepVariant, phased with whatshap
  needlr/     family.sv.annotated.vcf.gz (+ .tbi)
  repeats/    FAM001_tr.vcf
  bams/       PROBAND/PROBAND.cram (+ .crai)                       alignments, one folder per sample
  qc/         nanoplot/PROBAND/…, depth/PROBAND/…
  pipeline_info/  software_versions.yaml, params_*.json, execution_report_*.html
```

CoGA reads the family call set as DeepVariant's own calls: not as imputed genotypes, and
without DeepVariant's reference and no-call records. It takes only `bams/<sample>/<sample>.cram` as a
sample's alignment, not a `<sample>_phased.bam` beside it. When the run was started several times, it names
the pipeline records of the last run that completed.

A family on conventional short-read WGS (Illumina, the SR-WGS analysis) has its PED, its joint GATK call
set twice at the top of the folder, and a folder per sample for its alignment:

```text
FAM001/
  FAM001.ped
  FAM001.GRCh38.annotated.vcf.gz (+ .tbi)   the call set, VEP-annotated: the one CoGA reads
  FAM001.vcf.gz (+ .tbi)                    the same call set unannotated: not read
  PROBAND/    PROBAND.cram (+ .crai)        alignments, one folder per sample; optional
```

CoGA reads the annotated call set as GATK's own calls, never as imputed genotypes (its header speaks of
phasing, and some of its genotypes are phased). A sample without a CRAM has calls but no reads in the genome
browser. Such a family goes into the folder of its project like any delivery; without a `coga-package.json`
it is added by hand (below).

PGT packages from the PGT pipeline (nf-cmgg/copgtm) are laid out per tool:

```text
FAM001/
  FAM001.ped                                                   the family's pedigree (you put it there)
  ped/            combined.ped                                 the pipeline's own PED: not read
  dashboard/      samplesheet*.csv, pedigree.csv               the pipeline's input: not read
  qdnaseq/        EMBRYO1_cnv.csv                              CNV bins and segments
  apcad/          EMBRYO1_APCAD_parental_data.csv              APCAD: the sites the run read, when it writes them
  apcad/          EMBRYO1_APCAD_segregation_data.csv           the sites where one parent is heterozygous
  split_trio/     filter_trio/EMBRYO1.trio_filtered.vcf.gz     APCAD otherwise, one trio per embryo
  apcad/          EMBRYO1_pcf_{mat,pat}_data.csv               PCF segments, when the run writes them
  mito/           variants.annotated.txt                       mtDNA-Server calls of every sample
  phasing/        shapeit_filter/FAM001_shapeit_rephased_final.vcf.gz   haplotypes
  cram/           EMBRYO1.cram (+ .crai)                       alignments
  mean/ qualimap/ ngsbits/ picard/ rtgtools/ king/             QC
  pipeline_info/  params_*.json, copgtm_software_mqc_versions.yml
```

The family's pedigree is the PED at the top of the folder (`FAM001.ped`): CoGA reads no other. The
pipeline's own PED in `ped/` lacks the index and gives the embryos a sex, so CoGA cannot tell them from
born children; its samplesheet is not read either. A folder with only `ped/combined.ped` says
*No PED file was found at the top of the family folder*. Write the family's PED with the six standard
columns, and what a standard PED cannot say in a column after the sixth:

```text
FAM001  FATHER1  0        0        1  -9  role=father   carrier=1
FAM001  MOTHER1  0        0        2  -9  role=mother   carrier=1
FAM001  INDEX1   FATHER1  MOTHER1  1  2   role=proband
FAM001  EMB1     FATHER1  MOTHER1  0  -9  role=embryo
FAM001  EMB2     FATHER1  MOTHER1  0  -9  role=embryo
```

- `role=` gives a member's role (`father`, `mother`, `proband`, `embryo`, `relative`); without it, the
  pedigree gives it. The sixth column is the clinical status (`2` affected), and `carrier=1` marks a
  carrier.
- An embryo has sex `0`, unknown before the analysis, and `role=embryo`.
- The index is every member but the couple and the embryos who is no one's parent. The couple's child,
  as above, is the proband.
- An index that is not the couple's child is a relative: linked through its parents in the PED, or,
  when you do not know the degree, through the member `related_to=` names:
  `FAM001 INDEX1 0 0 2 -9 related_to=MOTHER1` (**Related to**, a dotted arc in the pedigree).

The warning says how CoGA read each index. An index linked neither way stays grey and does not help find
the risk haplotype: link it with **Correct…** before you approve the delivery, or afterwards in
**Admin → Family & sample data**.

The parent the pipeline traced is recorded as the affected parent (`metadata.pgt.affected_parents`), and
its index as the index (`metadata.pgt.indexes`). Set the inheritance model
(`metadata.pgt.inheritance_model`) and both get the status it asks:

- the parent: affected under AD or XLD, or as a father under XLR; a proven carrier under AR, or as a
  mother under XLR;
- the index: affected under AD, XLD or AR, or as a male index under XLR; a proven carrier as a female
  index under XLR.

Under AR, when the index is the couple's affected child, the other parent is recorded as a proven
carrier too: both parents of an affected child are obligate carriers. Validation lists each status it
derived. A status you record for the member under `family.members` (or, for the added index, under
`family.add_members`) wins.

The rest of a PGT package:

- The ROI comes from the pipeline run.
- PCF segments are read from their CSV table only. A run that only draws them in an HTML plot gets no
  PCF track.
- APCAD is read from the run's table of the sites it read for each embryo (`_APCAD_parental_data.csv`:
  the embryo's `raw_AF` and the parent in `source`), the points the laboratory's own plots rest on. An
  embryo without that table gets APCAD from its trio VCF.
- The run's segregation table (`_APCAD_segregation_data.csv`, the sites where one parent is
  heterozygous) goes into the embryo's *Parental origin* track of the chromosome view.
- The run's mtDNA-Server table (`mito/variants.annotated.txt`) gives each member it holds rows for its
  chrM calls. CoGA matches the table's `ID` (the sample's chrM BAM) to the member.

CoGA knows several usual file names per layer, so packages from slightly different pipelines are still
found; a folder added by hand shows what CoGA found. When a caller names a VCF's sample column after its
input file (`Sample0`, `HG002_sort`) rather than the sample, CoGA binds a single-sample VCF to the sample
it was declared for. A column that matches no sample in the family stops the import instead of being
dropped silently.

CoGA looks only inside the family folder, and reads each ID as written: a `*`, `?` or `[` in a family
or sample ID matches only itself, so one sample never takes another's file. A family or sample ID that
is a path out of the folder (an absolute path, or one that climbs out with `..`) names no file CoGA
looks for, and is refused.

**CoGA reads no manifest and no PED in the family folder**: the import reads the one CoGA keeps, made
from the delivery's package, and the family's files hold a copy of it. Every path the package names is relative to the family folder. A path is refused when it leads out of the folder, starts with `~` (CoGA reads no home folder)
or runs through a link that loops.

### Deliveries from the pipelines

A pipeline can deliver a package by itself: it copies the family folder into a **watched import
folder**, with its **package file** `coga-package.json`. That one file describes the package: the
run, the project, the family and its members, the data, every file with its size (and, optionally, its checksum). It takes the
place of the PED and the manifest. CoGA looks into the watched folders every
few minutes (5 by default), checks each new delivery once and lists it under **Deliveries** at the
top of the page, with how many wait. Nothing is imported until a superuser approves it.

**The watched folder.** The server's administrator names the watched folders
(`FAMILY_IMPORT_WATCH_ROOTS`). They lie under the import folder, on the server's disk, a mounted
share or a mounted storage bucket. The watched folder holds a folder per project, named exactly
as the project in CoGA, and each delivery is one family folder in the folder of its project:

```text
<watched folder>/
  PGT/
    FAM001/
      coga-package.json   the package file
      snv/ needlr/ …      the data
  BeGECS/
    FAM002/
      coga-package.json
      …
```

- A family folder counts once it holds `coga-package.json`. A folder whose name starts with `.` is
  skipped.
- **A delivery lies in the folder of its project.** One whose package file names another project
  than its folder needs attention: correct the project with **Correct…**, or move the folder into
  the folder of its project. A family folder directly in the watched folder needs attention too:
  move it into the folder of its project.
- **The package file says when the package is complete**: its `status` is `running` while the
  pipeline runs, and `complete` as its last step. CoGA offers only a complete one. A pipeline that
  runs again in a folder writes `running` first: a delivery of that folder no one has decided on is
  then set aside (*Superseded*), until the file says `complete` again.
- A folder that changes after it was checked is a new delivery; an open one before it becomes
  *Superseded*. To correct a delivery, correct the folder and write its package file again.

**The package file.** Its format, `coga-package/1`, and an example of each kind of package are in
the repository (`docs/coga-package.md`, `docs/schemas/`). A short one:

```json
{
  "schema": "coga-package/1",
  "status": "complete",
  "written_at": "2026-11-10T14:02:11+01:00",
  "pipeline": { "name": "example-pipeline", "version": "1.4.0", "run_id": "RUN-0042" },
  "project": "Rare disease WGS",
  "assembly": "GRCh38",
  "analysis_type": "long_read_wgs",
  "family": { "family_id": "FAM001" },
  "members": [
    { "sample_id": "PROBAND", "sex": "male", "father": "FATHER1", "mother": "MOTHER", "clinical_status": "affected" },
    { "sample_id": "FATHER1", "sex": "male" },
    { "sample_id": "MOTHER", "sex": "female" }
  ],
  "datasets": { "snv": { "family_vcf": "snv/FAM001.annotated.vcf.gz" } },
  "files": [
    { "path": "snv/FAM001.annotated.vcf.gz", "size": 48213377, "sha256": "<64 hex characters>" }
  ],
  "qc": { "status": "pass" }
}
```

- `members` is the pedigree: the import reads it, not a PED. A package file without `members` or
  `datasets` has them proposed from the folder (from its PED and its layout); the delivery then warns
  you to check them.
- `reference`, the reference genome the run used (its FASTA's name), is optional. CoGA records it
  with the family's annotation versions as its *Reference assembly*.
- A field the format does not know is refused, so a misspelt one is never ignored.

**What CoGA checks.** The package file follows the format. Every listed file is there at its size,
with its checksum where the package file gives one; a file listed without one has its SHA-256
computed by CoGA, and the delivery warns that the copy was not checked against what the pipeline
made (acknowledge it to approve). Every file the import reads is listed; a file in the folder the package file
does not list is a warning. The project exists with the package file's assembly (by its build:
`GRCh38.p14` and `hg38` are a GRCh38 project's), and the package
passes validation as a dry run runs it. A PED in the folder that says otherwise than the
package file's members is a warning: the pipeline may have used it. Each check that fails is listed
with the delivery.

- **Ready**: every check passed. **Dry run** checks it as an import would; **Approve…** imports it
  into the project the package file names. Tick several ready deliveries to approve them together.
- **Needs attention**: something stands in the way, and the reasons are listed. When the only reason
  is that the family exists already, **Approve…** asks for *Update* (add the missing datasets) or
  *Overwrite* (administrators). A project CoGA does not have is fixed in CoGA: add the project, and
  CoGA checks the delivery again in its next look at the folders. The same happens when the
  delivery's family is imported or deleted meanwhile. What the package says can be corrected here
  (**Correct…**, below); any other reason is fixed in the package, and the corrected folder comes
  back as a new delivery.
- **Still copying**: a listed file is not there yet, or not complete. It is checked again when the
  copy has finished.
- A delivery with warnings is approved only once you tick *I checked the warnings*.
- **Correct…** completes or corrects what the package says, before you approve it: the project,
  the analysis, the region of interest, the PGT inheritance model, affected parent and index, each
  member's sex, parents, role, clinical status, carrier status and HPO terms, and which datasets
  are in the import. Give a reason and save: the pipeline's file stays as it was, the correction is
  kept beside it on the audit trail, and the delivery is checked again at once. The list then says
  *Corrected (revision …)*, and an approval imports the package as corrected; the family's record
  names every correction.
  - The run, the assembly, the family and sample IDs and the files are the pipeline's and are not
    offered: a wrong one is refused, and the pipeline delivers again.
  - A sample the package's files name and no member is, such as an affected relative in the phased
    family VCF read for segregation, or a PGT index the PED lacks, is offered under
    **Add a member**: add it, give its sex, role and status, and how it is related (its parents,
    or under **Related to** the member it is related to; a PGT index without a link stays grey).
    A member added here can be removed again; a member the package delivered cannot.
  - The package is shown as `coga-package.json` under the form, and **Download coga-package.json**
    saves it. **Package…** shows it for a delivery that is no longer corrected, with its
    corrections. CoGA writes it into no folder.
  - A folder added by hand has no pipeline: CoGA made its package, so you add any member there,
    remove one, or edit the whole package with **Edit as JSON**. Each save is a correction with
    its reason, like any other.
  - A member's sex and parents, which the pipeline used to compute some datasets (the form names
    them), can be changed, but the delivery then needs attention until those datasets are out of
    the import or the value is put back: the pipeline computed them with the pedigree it was
    given.
  - What CoGA proposed from the folder, for a package file without members or datasets, is
    corrected the same way.
  - Someone else may have corrected the delivery since you opened it: the save is refused, and you
    open it again.
- **Add a folder by hand** (above the list) adds a folder no pipeline delivered: choose it (or type
  its path), its project and its analysis (LR-WGS, LR-AS, SR-WGS, PGT, Expanded Carrier Screening or mNIPT),
  and, when it is not the folder's name, its family ID: left empty, the family ID is the folder's
  name, and it cannot be changed once the folder is added. CoGA
  makes its package from the folder, with the members and datasets it proposes from the folder's PED
  and layout, and checks it as a pipeline's delivery; it is listed as *Added by hand*. Check and correct
  it with **Correct…**, then approve it. CoGA writes nothing in the folder and computes no checksums:
  the import records each file it reads. A folder that holds its own `coga-package.json` is delivered
  through a watched folder instead, and a folder whose delivery waits already is not added twice.
- **Refuse…** sets a delivery aside, with a reason: CoGA leaves the folder alone until it changes.
- After the import CoGA keeps watching the folder, also one added by hand: the family's read files
  (IGV), signal tracks and QC report are shown from it. A folder that changes (a file added, removed,
  or changed in size or time), or is gone for two looks while the folder around it can be read, is
  listed as *Changed after import*, with the files that changed, and every page of the family shows
  *Package folder changed after import* (or *Package folder gone*); signing out its report asks for a
  reason. It ends when the folder is as it was imported again, or when its new delivery is imported
  with overwrite or refused with a reason: a refused folder state is not raised again. Importing it
  with update keeps the notice, as the approval says: an update skips the datasets the family holds.
  Such a delivery stays listed, and an administrator can **Approve…** it again, with *Overwrite*
  only, to end the notice.
- **Confirm archived…** (a superuser's) on a delivery whose folder is gone since its import, with a
  reason: the folder was archived on purpose. Signing out then needs no reason, and every page of the
  family says *Source files archived*: the read files, signal tracks and QC report are no longer
  available. A folder that comes back changed is a change again.
- A delivery whose folder is moved out of the watched folder leaves the list after two looks at the
  folders (it is *gone*). A watched folder that holds nothing at all is taken for a share that is not
  mounted: its deliveries stay listed until something is back in it.

An approved delivery shows its import job, and becomes *Imported* or *Import failed* when the job
ends. The decided and superseded deliveries are listed on request.

### The steps

1. **A delivery arrives.** A pipeline writes its package into a watched import folder, and the
   delivery is listed under *Deliveries* once its package file says the run is complete. A folder no
   pipeline delivered is added with **Add a folder by hand**: choose it (or type its path), its
   **Project** and its analysis. A folder that is a link leading out of the import folder, or a link
   that loops, is not listed, and CoGA reads nothing in it; typed, it is refused with the reason. A
   folder whose families were all imported is left out until you tick **Show imported folders**,
   and one whose import did not complete is marked *import incomplete* (*import undone* when its
   latest import was undone).
2. **Check and correct.** Read the delivery's issues and warnings, and open **Correct…** to complete or
   correct what the package says (the project, the analysis, the members, the datasets), with a reason.
3. **Dry run.** **Dry run** checks the delivery as an import would, without writing anything; its job
   shows under *Recent family imports*.
4. **Approve.** **Approve…** queues the import. When the family exists already, choose the **Existing
   data policy** (table below); only an administrator overwrites an existing family. An approval of a
   delivery whose folder changed since it was checked is refused. A job also imports nothing when the
   folder changed after it was queued, and is undone when it changed while it was read: a new family
   is removed, an existing family put back as it was (*package fingerprint*, shown with the job).
5. **Follow the job** under *Recent family imports*, which lists the latest 10 jobs (**Show all** lists
   up to 100, in a list that scrolls): status, log, validation errors, warnings and a
   summary per dataset. While a dataset imports, the page shows how much of its files has been read and
   about how long it should still take, at the pace so far. A finished dataset shows how long it took.
   The estimate covers the running dataset only; the page says how many datasets follow. A dataset
   or a job that failed says why in CoGA's own words. One that failed on an unexpected error (a
   database or file error) says only the error's kind: its text can quote the values it was
   reading, and the server log shows where it failed.

| Existing data policy | What happens when the family or a sample already exists |
| --- | --- |
| Cancel if family or samples exist | the import stops |
| Update: add missing datasets only | the data is added to the existing family; datasets that already hold data are skipped (tracks and Paraphase results sample by sample), and a dataset skipped whole shows *skipped*. The package's new members are added |
| Overwrite imported dataset rows | the data is added to the existing family; the selected datasets are replaced, and the package's members become the family's pedigree. For administrators only; a superuser may run it as a dry run |

**An existing family.** The package's members are compared with the family as CoGA holds it.
*Update* adds the members the family lacks (the delivery warns of them) and changes none it holds.
A package that gives a member another sex or other parents, or lacks one, needs attention: correct
it with **Correct…** to the family's members, or have an administrator approve it with
*Overwrite*, which makes the package's members the family's pedigree (a member it lacks is made
inactive). Either change is a new version of the family's structure, on its audit trail, and
marks its analyses to be redone.

**An existing family imported into another project.** The family is linked to that **Project**
too, and before its datasets the import gives the project the family's small and structural
variants it does not hold yet. Whoever sees only that project sees all of them, not just what the
import brought, also when the import fails and the family is put back. A project on another
assembly than the family's variants is refused, and nothing of the family is written.

**Mitochondrial calls are replaced per sample.** A package holds one mitochondrial file per sample, and
each file replaces only that sample's calls. The other members' calls stay, so the mtDNA analysis keeps
the mother's calls beside the children's for the maternal transmission. A mitochondrial file without a
variant is a failed analysis, not a sample without mitochondrial variants (see below). The chrM
structural-variant files (large deletions and duplications) are replaced per sample the same way; the
mtDNA analysis lists them with each member's heteroplasmy.

**Each caller's tracks are its own.** A sample can carry coverage from a BED, WisecondorX, QDNAseq and
HiFiCNV at once, segments from the same callers, and on the APCAD track a package's APCAD beside
HiFiCNV's minor allele fraction. The viewers draw each caller's coverage as its own track. An import or
an upload replaces only its own caller's tracks: a coverage BED leaves the WisecondorX and QDNAseq bins
and HiFiCNV's depth, and an APCAD import leaves HiFiCNV's minor allele fraction.

- **Update** skips a sample only when its own caller's track is there.
- A package's coverage BED and an uploaded coverage BED count as one caller: each replaces the other.
- An APCAD family file replaces the track of each sample it holds values for; a sample it holds none for
  keeps its track.

**A coverage BED says what it holds.** Each coverage BED declares read depth or a log2 ratio: in the
upload form (*Coverage BED holds*), and in the manifest with `content: read_depth` or
`content: log2_ratio` on the sample's coverage entry.

- The viewers draw read depth on a depth axis and a log2 ratio on the log2-ratio axis.
- The mtDNA analysis, and the NIPT coverage check without a per-target table, read as depth only a BED
  declared as read depth, or HiFiCNV's depth where the import stored it as read. Never the WisecondorX
  or QDNAseq bins or HiFiCNV's normalised depth, which are log2 ratios, nor a BED declared as a log2
  ratio. Each track these checks leave out is named, with the reason, on the mtDNA page and with the
  NIPT coverage.
- A manifest entry without `content` imports as *unknown*, and validation warns. Such a track is drawn
  on its own range without an axis, with a note, and never read as depth.
- A sign-out freezes what each coverage track held, and whether an upload or a package loaded it, in
  the signed record (*Coverage tracks*).

**A file without a record fails its dataset, and nothing is replaced.** A file that holds nothing CoGA
can store (a header alone, a file cut short before its first record, an empty file) fails its dataset
before anything is deleted. The job's log names the file (for a Paraphase result, the sample). This
holds for a TRGT file, WisecondorX or QDNAseq bins or segments, HiFiCNV's copy-number and signal files,
an APCAD file, a PCF table, a Paraphase result, a sample's NeedlR file, a long-read sample's SNV file or
a sample's mitochondrial SNV file.

- Every sample's mitochondrial file is read before any sample's calls are replaced. So one without a
  variant keeps every sample's earlier mitochondrial calls, and the family is put back or marked
  import-incomplete for the mitochondrial calls.
- A file cut short after its first records still fails its dataset when the import reaches the cut, and
  the family is put back or marked as for any failed dataset.
- A mitochondrial structural-variant file without a call is a result, not an empty file (most samples
  carry no mtDNA deletion): it removes that sample's earlier chrM deletions.

**A bgzip file must end with its end-of-file block.** bgzip, which compresses the pipelines' `.vcf.gz`
files, ends every file with an empty block. A file cut exactly where one of its blocks ends is still
whole gzip, so without this check its later records would be lost without a word. Such a file is refused
as cut short (*… is not valid gzip: it is truncated (it does not end with BGZF's end-of-file block)*):
an upload replaces nothing, and a dataset fails before anything is replaced. Copy the file again from
the pipeline's output. A gzip file that bgzip did not write has no such block and is read without this check.

**Every sample's file is checked before any sample's data is replaced.** A dataset of one file per
sample reads or checks every sample's file first: the TRGT files, HiFiCNV's VCFs and signal files, the
WisecondorX and QDNAseq bins and segments, the PCF tables, the Paraphase results, the APCAD file of each
embryo, the coverage BEDs and per-target coverage tables, a monogenic NIPT's SNV files and the
mitochondrial structural-variant files. A later sample's refused file then fails the dataset with every
sample's data as it was.

- A mitochondrial structural-variant file whose records all lie on other chromosomes is refused as no
  chrM callset.
- A long-read sample's SNV file is checked up to its first record: a record out of position order is
  still found only as the callset is written.

**A Paraphase copy number the database cannot hold fails its dataset.** A copy number beyond
2,147,483,647 (or below −2,147,483,648) in a sample's Paraphase result is refused like a TRGT value that
cannot be read as a number. The job's log names the sample, the field and the gene, and no sample's
Paraphase results are replaced.

### What validation checks

- The folder exists.
- Every family and sample ID follows the ID rule above (*The two ways in*): the manifest's family ID or
  the folder name, the PED's IDs, and, as CoGA proposes members, a parent the PED names without a row of
  its own, the members the manifest adds and a long-read package's per-sample folders. An ID that fails
  is refused (`family_id_invalid`, `sample_id_invalid`), and nothing else is checked until it is
  corrected: CoGA proposes no members for it.
- The package's members are the pedigree the import reads: each member listed once, every parent a
  member. CoGA reads no PED: a folder's PED only tells CoGA which members to propose when the folder
  is added by hand.
- Every per-sample dataset names a member.
- Every file exists, in the family folder. A compressed family VCF for small variants, structural
  variants or repeats needs its index (`.tbi`, `.csi` or `.idx`); a plain `.vcf` does not. Other index
  files are checked only when the manifest names one.
- A `ped/` folder or a long-read folder (`snv/`, `repeats/`, a sample's folder in one) that is a link to
  another folder is a warning (`package_folder_link`). It says where the link leads: within the import
  folders or outside them. CoGA still follows it, and may take a member from it, so check that what it
  holds belongs to this family.
- A monogenic NIPT pair's two SNV files are not refused for their column: each file's one column is
  read as the sample of its entry, whatever its name, because the laboratory maps its verified tubes to
  files that can be named after another tube. When the column is named after neither the sample nor the
  entry's `vcf_sample`, the delivery, the dry run and the import warn (`nipt_vcf_column_named_otherwise`),
  naming the sample, the file and the column. Check that the file holds that sample's tube: if it does,
  record the column as the entry's `vcf_sample`, which ends the warning; if not, correct the manifest.
- A sample's SNV file that cannot be read (a `.vcf.gz` cut short or damaged in a copy, or a plain-text VCF
  named `.gz`) is an error that names the sample and the file (`dataset_vcf_unreadable`), as CoGA proposes members too
  for a NIPT pair's parent file. Copy the file again from where the pipeline wrote it.
- So is an embryo's or the family's APCAD VCF, or the phased family VCF, that cannot be read, a BGZF file
  that lacks its end-of-file block (cut short anywhere) included: the error names the file, and the embryo
  for an embryo's (`dataset_vcf_unreadable`). A phased family VCF without a `#CHROM` line naming its
  samples is an error too (`dataset_vcf_no_sample_columns`): no member's genotypes can be read from it.
- A manifest's `roi` (a gene, or a region such as `chr7:117480025-117668665`) is looked up in the
  assembly of the import's project, or of the existing family. One the import does not find clears the
  family's region of interest, so no embryo is called at a region the package does not name. The import
  warns (`manifest_roi_unresolved`) and names the region it cleared: set it on the family page. The dry
  run gives the same warning ahead; with no project and no existing family to look it up in, it says
  the region was not checked (`manifest_roi_not_checked`).
- A dataset type CoGA does not know is an error. A missing small-variant or structural-variant dataset is
  a warning; other missing datasets are simply absent.
- A data file with nothing after its header, that the import would refuse for holding no record, is a
  warning: the import will fail that dataset and keep its stored data. This covers a family VCF of small
  variants, structural variants, repeats or APCAD; a sample's SNV, NeedlR, TRGT or mitochondrial VCF; an
  embryo's APCAD file; a sample's coverage BED. A HiFiCNV or mitochondrial structural-variant VCF without
  a call is a result, and gives no warning.
- A phenotype row that names an unknown sample or HPO term is skipped with a warning; it never blocks
  the import.

### After the import

- **Sequencing QC** appears per sample in the **Family members** table as a chip with the QC verdict
  and the mean depth.
  - Hover it, or move the keyboard focus to it, for every metric and the cut-offs. Click it to open the
    pipeline's QC report.
  - The chip reads *Warning* (amber) or *Fail* (red) when a metric crosses a warning or error cut-off.
    The tooltip names each such metric with its value (rounded as on the chip) and the cut-off it
    crossed, in the metric's unit (*Mean depth 18.6x below 20x*).
  - Cut-offs are set per assay under **Admin → Sequencing QC thresholds**. None ship by default. A metric
    without a cut-off reads as *not assessed*, not as a pass.
  - A sample with no cut-off for any of its metrics, or with no verdict from the server, reads *Not run*
    in grey, and the tooltip says which.
  - A sample with no mean depth or N50, such as an embryo that appears only in the PGT pipeline's family
    tables, shows the value of the metric that decided its verdict instead: its allele drop-out or
    drop-in (*Fail ADO 14.2%*) or its Mendelian concordance, in %. One that was not assessed reads *Not
    run* alone.
  - The cell shows **-** only when the sample has no sequencing QC to show.
- **Phenotypes** the package brought in are in the audit trail on the family's report: one entry per
  person whose HPO terms the import added or changed, naming who ran it and the package (*Phenotypes
  imported for S1 by a package import of FAM1 (phenotypes.tsv): added HP:0001250 Seizure (present)*).
  Importing the same phenotypes again adds no entry. A failed overwrite that puts the family back adds
  one for the phenotypes it put back.
- **Analysis pipeline settings**: the pipeline's configuration (genome build, which caller produced
  what, VEP cache, repeat catalogue, stages run) is recorded at import. It shows in a panel on the family
  page and in the same section of the family report. Tool versions are in the report's provenance
  footer.
- **A dataset that failed** ends the job as *failed*. If the family is left partly loaded, every family
  page shows *Import incomplete*, and its report can be signed out only with an acknowledgement
  ([Report traceability & sign-out](/docs/reference/clinical-traceability)). The warning stays until an
  import has imported each failed dataset again, or an upload has brought it again, for each callset and
  each set of samples it failed for.
  - An **update** will do. A dataset or sample it skips because its data is there is not imported again,
    and stays named.
  - A dataset imported with warnings counts. So does one the package carries nothing for that CoGA
    imports (phenotypes without an HPO term, say), which the import only registers.
  - An import that completes without them leaves the warning, and says so in its log.
  - Deleting a sample with its data takes that sample out of the warning: what it named of that sample
    alone goes, as nothing can bring the sample's data again.
- **A failed overwrite** puts the family back as it was before the import. If putting it back fails too,
  part of the family's data may be gone, whichever import or upload it came from. The warning then names
  every dataset of that import as failed (the ones that imported too), and every dataset the family
  holds as possibly partly written or missing, those of its other packages and its uploads too. It stays
  until an administrator has brought each of these again with **overwrite**:
  - the whole family, every package of it, imported again, not only the datasets that failed;
  - what an upload brought, uploaded again (an uploaded caller's SV calls, `sv_sniffles` say, or a BED
    track, `bed_coverage`);
  - **update** skips a dataset whose data is there.
- **An import that stopped part-way** (the server restarted, or ran out of memory, while it ran) is not
  run again. About ten minutes later its job ends as *failed*, interrupted, with its log as the import
  left it. An import still running that could not record so for those ten minutes is then stopped too,
  where it is. The family shows *Import incomplete*, naming the datasets the import had not finished:
  they may be partly written. Import them again with **overwrite** to complete the family; **update**
  skips a dataset that already has data, partly written data too.
- **While an import is queued or runs**, the family's report cannot be signed out: the data is not yet
  complete. Sign out once the job has finished.

---

## Single uploads

Outside the package flow, an administrator can upload one layer at a time on the **Upload family and
sample data** page: family small variants (including GLIMPSE2 haplotype VCFs), structural variants,
repeat expansions, and interval tracks (coverage, APCAD, APCAD PCF segments, segments).

A family small-variant upload that replaces stored calls (**overwrite**) reads the whole file before it
replaces anything. A file it refuses leaves the stored calls, and for imputed genotypes the haplotype
blocks, as they were. It refuses a file that:

- names a sample the family does not have;
- holds no record it can store;
- was cut short in a copy;
- has a line over the length limit;
- writes a genotype as a word (*HET*, *HOM*, *REF*) instead of VCF's allele notation (*0/1*, *1|1*,
  *./.*). The refusal names the file, the line and the sample. Only a malformed VCF holds one. Stored,
  it would be read as a no-call by the filters but shown as a genotype on the variant list. A package
  import's small-variant files are refused the same way.

If storing the new calls still fails after the stored ones were deleted (the variant store failing
part-way), the upload reports an error. The family is then marked *Import incomplete* for those calls
(see *After the import*) until they have been uploaded or imported again.

The small-variant, structural-variant and interval-track uploads mark the family before they first write
to the variant store, and remove the mark once they have finished. The mark stays if the server stops in
the middle of such an upload (it restarted, or ran out of memory), or the upload fails and cannot put
back what it had deleted. Every family page then shows *Import incomplete* ("A single-file upload …
began rewriting this family's data and has not finished"), naming what may be partly written or missing,
and sign-out needs an acknowledgement. Upload again with **overwrite** what it names: each sample's file
for a structural-variant caller (the upload rewrites every sample's calls from that caller), the
callset, or the sample's track. Each goes from the warning as it is uploaded, or imported, again. An
upload without **overwrite** removes nothing from it.

A deletion of the family's data marks the family the same way before it first writes to the variant
store. Such deletions are: an administrator's deletion of a sample with its data, of a sample's
structural variants or of the family; a PED upload or a family built by hand that replaces the family;
a structure change that clears its data. Its end removes the mark. If it fails, the warning ("A deletion
of this family's data … began and has not finished") names exactly what may be partly written or
missing; if the server stops in it, everything it could have reached. Deleting a sample again, once it
completes, removes that sample's part of the warning. An import whose SNV or imputed-genotype file is
refused before anything is written (a VEP table without its header row) leaves the callset the family
held.

A value the variant store cannot hold, or that is no number, never fails an upload or an import: it is
stored as missing.

- A genotype quality written as `inf` or above 65,535, a negative phase set, or a QUAL beyond the
  store's largest decimal reads as absent. So a sample filter with a minimum genotype quality leaves such
  a call out, as it does a call without one.
- A read depth is kept as the file has it, up to 4,294,967,295 reads, so a deep mitochondrial or
  amplicon call keeps its depth.
- A record whose position the store cannot hold (a negative POS, an END beyond 4,294,967,295) is skipped
  and counted with the malformed records.
- A track line with a negative start or end is left out.

A structural-variant file holds one sample's calls from one caller: **Sniffles VCF**, **Spectre VCF** or
**Manual TSV**, or **Auto detect**, which works the caller out from the file. CoGA keeps each caller's
calls apart:

- The upload stops only when the sample already has calls from the same caller, and the page asks
  before it replaces them.
- Replacing them changes only that sample's calls from that caller. The other samples' calls, and the
  calls of every other source (a package's NeedlR or HiFiCNV calls, another caller's upload), stay as
  they are. If storing them fails, every sample's calls from that caller are put back as they were.
- The caller and its version, as the file's header names them, are added to the family's annotation
  versions.
- Uploads from two callers that report an SV with the same type and breakpoints each keep their own
  call. The SV list shows the SV once per caller, with that caller's genotypes, and both rows share the
  SV's review, tags and note. A package's NeedlR call of that SV is one more such row.

A Sniffles or Spectre VCF, and a repeat-expansion (TRGT) file, are read for the sample you upload them
for:

- CoGA takes the column named after that sample (TRGT writes `<sample>_sort`), wherever it is in the
  file, so a family Sniffles or TRGT VCF can be uploaded member by member.
- A file with one column that names no sample (such as `Sample0`) is taken as that sample's.
- A file whose columns name other samples and not this one, of this family or another, or that has two
  columns for it, is refused, and nothing is replaced. So is a Sniffles or Spectre VCF without its
  `#CHROM` header line, once, before its records.
- A package's per-sample TRGT, mitochondrial and HiFiCNV files follow the same rule. When a file is that
  sample's after all (a lab that verified its tubes), the entry's `vcf_sample` says so. A monogenic NIPT
  pair's SNV files are the exception (*What validation checks*).

A manual TSV has no sample column: each of its rows is the sample's call. Nor has it an SVLEN, so the
structural-variant length filters measure a TSV call by its REF and ALT when they are sequences (see
below), and otherwise by its end minus its start: an insertion written `N`, `<INS>` whose end is its
start is 0 bp to them.

A repeat-expansion (TRGT) upload reads the whole file before it replaces anything. TRGT writes a record
for every locus of its catalogue, so a file without a record for the sample (a header alone, or a file
cut short in a copy) is refused (*No valid TRGT records found*), and the sample's stored calls stay as
they were. A file with a value that cannot be read as a number where TRGT writes one (a position, a
motif count, an allele length, a purity) is refused too: the answer names the line, and nothing is
replaced.

A Sniffles or Spectre record without an END ends where the VCF standard (4.4) puts it: a deletion,
duplication, inversion or copy-number change SVLEN bases after its start, any other call on its start.
The SV is listed with the genes that span overlaps, so a search for a gene a deletion removes finds it.

A record without an SVTYPE takes the type its ALT states, so a breakend written as `N]5:3000000]` is a
BND. A package's NeedlR, HiFiCNV and mitochondrial SV files take a missing type the same way.

A call written as its sequence (REF and ALT bases of other lengths) without an SVLEN, as VCF 4.4 writes
it, is as long as its alleles say: an insertion the bases it inserts, a deletion the bases it deletes,
shown with a minus sign as a deletion's SVLEN is. The structural-variant list shows that length and the
length filters measure it. Every caller's file, a package's included, is read this way. A call that
gives its SVLEN keeps it.

A breakend keeps its mate, the position its ALT names in brackets (5:3000000 here). The
structural-variant list and its CSV export show it in the **Remote chr** and **Remote start** columns,
beside the locus (empty for an SV without a mate), and the filters of those names find the breakend by
it. A package's NeedlR file is read the same way: a record with its mate in brackets keeps it, whatever
its SVTYPE says.

A package's NeedlR SVs are named by their breakpoints and type, and a breakend's mate, as an upload's
are. They are not named by the caller's record ID, which repeats from one run to the next
(`Sniffles2.DEL.1S0`). Two samples' calls that share an ID but lie apart stay two SVs, each with its own
sample's genotype. One SV in two samples' files, or repeated in the family file, is one SV with every
sample's genotype, each sample keeping its own call. The record ID is kept with the SV's annotations.

Deleting a sample's structural variants on **Admin → Family & sample data** removes its calls from every
source. The upload and this delete leave every other call as it was, with its phase, including the calls
of a member removed from the family.

An interval-track (BED) upload works the same way. A coverage BED upload is refused unless the form
says what it holds (read depth or a log2 ratio). The upload stops only when the sample already has that
track from a BED (an earlier upload, or a package's coverage BED), and the page asks before it replaces
it. The other callers' tracks of that type (WisecondorX, QDNAseq, HiFiCNV, a package's APCAD or PCF)
stay as they are. A file that holds no record of the track type is refused
before anything is replaced. Deleting a sample's track type on **Admin → Family & sample data** removes
every caller's.

The uploads, the deletes of a family's variants and the package imports of one family take turns. An
upload started while another of them is writing the family waits until it has finished (for a package
import, until the whole import has), then starts from what it left. If that write deleted the sample or
the family, the upload stores nothing and says *Sample not found* or *Family not found*.

Each upload records what it brought for the family, as a package import records what it imported. When
a failed overwrite could not put the family's data back, the warning names what the uploads brought too:

- small variants as `snv` (imputed genotypes as `haplotypes`);
- TRGT calls as `repeats_trgt`;
- a caller's SV calls as `sv_sniffles`, `sv_spectre` or `sv_manual`;
- a BED track as `bed_coverage`, `bed_segments`, `bed_apcad` or `bed_apcad_pcf`.

Upload each again with **overwrite** to complete the family; an upload without overwrite does not
count. A dataset
named as failed (a small-variant upload that failed after deleting the calls it replaced, or a dataset
an import failed for) is completed by an upload that brings it again, with or without overwrite.

---

## Recommended order for a new setup

1. The species, assembly and project.
2. The reference data for that assembly.
3. The family: the family builder, a PED upload, or a package import.
4. Small and structural variants.
5. Repeat expansions and the interval tracks.

---

## Common pitfalls

- **Empty viewers or gene lookups** are usually missing reference data, not a failed import. Check the
  assembly's reference data before re-importing the family.
- **The folder path** is a path on the CoGA server, under a configured import folder, not a path on
  your own machine.
- **Only superusers and administrators import packages**, and only administrators overwrite an existing
  family or upload single files. Analysts build new families by hand.
- **Missing index.** A compressed small-variant, structural-variant or repeat VCF without its index
  fails validation.
- **An ID with a hidden character.** An ID copied from a spreadsheet or an e-mail can carry a tab, a
  line break or a non-breaking space. The validation message writes the character as an escape (`\t`,
  `\n`, `\xa0`) and says where the ID comes from (the PED, the manifest, the folder name): correct it
  there.
- **Restructuring a family that has data.** Changes to members, relationships, affected or carrier
  status save even when data is loaded. The imported data is kept, and what depends on the changed
  facts is marked for re-checking. Re-review the saved interpretations.
- **A missing phenotype.** Phenotype rows with unknown samples or HPO terms are skipped with a warning:
  check the job summary if an expected term is missing.
