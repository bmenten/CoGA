```cards
Structural variants | Filter and review CNVs and other SVs with their genotypes, rank them by phenotype, classify copy-number changes, and tag them for the report.
Repeat expansions (TRGT) | Per-sample repeat calls, scored against the STRchive catalogue of disease loci.
Paraphase | Genes in paralogous or duplicated regions, where standard variant calling is unreliable.
Mitochondrial DNA | mtDNA variants and large chrM deletions and duplications, each with its heteroplasmy per member, and each sample's mtDNA coverage.
```

### Structural variants

The **Structural variants** page opens on the Mendeliome panel, with a population frequency below 1% and
the affected members as carriers. Its filters are **Phenotype**, **Inheritance and Support**,
**Locations**, **Class and Breakpoints**, **Needlr Annotations** and **Review**; the presets are
*Mendeliome*, *Dominant*, *Recessive-like* and *Any affected*. **Phenotype prioritization** ranks SVs by
the same phenotype score as small variants. **Clear all filters** removes the default.

As on the small-variant page, a gene panel of any size keeps an SV whose annotation names one of its
genes or that overlaps one of its regions. So the Mendeliome also keeps an SV in one of its genes whose
annotation names that gene otherwise, and a small panel an SV annotated with one of its genes that does
not overlap it, such as a gene a few kilobases away. With both a gene panel and a **Gene symbol**
filled in, an SV must meet both.

**Min length** and **Exact length**, under **Class and Breakpoints**, measure an SV by the **Length**
the list shows, without a deletion's minus sign. That length is:

- the caller's SVLEN;
- for a call written as its sequence without an SVLEN (as VCF 4.4 writes one), the bases it inserts or
  deletes;
- otherwise, the end minus the start.

An insertion is as long as the sequence it inserts, although most callers write its end at its start.
A length typed with a minus sign, as the list writes a deletion's, is read without it. A translocation
(BND, TRA) has no length: the list shows none, every minimum keeps it, and no exact length finds it.

A filtered search reads at most 50,000 SVs of the callset before it applies the filters. With
**Phenotype prioritization** on (the default) it ranks at most 5,000 of the SVs that match. When a
search reaches either limit, a warning says *"Results may be incomplete"* (*"Ranking may be incomplete"*
when the list is ranked), the count shows a `+`, and an SV beyond the limit is not shown. Narrow the
search with a region, a gene panel or a gene. A gene panel of any size (the Mendeliome included) and a
gene are applied before the 50,000 are read, so the limit counts only the SVs they keep. The family report's list of
reported SVs is not cut by this limit: it reads the reported SVs themselves, not the first 50,000.

**ACMG (CNV)** on a row opens the ClinGen copy-number classifier (Riggs et al. 2020): choose
*Copy-number loss* or *Copy-number gain*, check the pre-selected criteria and add the rest.

**Variant summary** on the family page summarises the structural variants of every caller, HiFiCNV's
copy-number calls included: counts per chromosome and type, sharing between members, and size distributions. The button appears when the family has structural variants. Small variants are not in
it, nor are the WisecondorX and QDNAseq segments, which are coverage tracks rather than variants.

### Repeat expansions

The **Repeat expansions** page lists each repeat locus with every member's call: the repeat count of
each allele, and a status read against the locus's thresholds in the repeat catalogue (STRchive):

- *Normal*, *Grey zone* (the intermediate range), *Premutation* (an FMR1 allele of 55–200 repeats,
  which can expand in a child) and *Pathogenic*. Where the catalogue gives a locus a benign range
  and a pathogenic threshold but no grey zone, a count between the two reads *Grey zone*: it is
  neither benign nor pathogenic.
- *Review: outside catalogued ranges*: a count the catalogue cannot classify at a locus that is
  pathogenic by contraction.
- *Review: two chrX alleles in a male*: a male's call with two different chrX alleles, where he
  carries one; a note explains it.
- *Not assessed*: a call at a locus without thresholds. No catalogued locus matches its TRID (a locus
  the catalogue lacks, or a TRID spelled differently), or the catalogue gives the locus no threshold.
  CoGA cannot say whether such a count is normal; its cutoff reads *No reference cutoff*. Where the
  catalogue gives a locus a benign range but no threshold (STRchive's POLG: 10 repeats), a count
  inside the range reads *Normal* and any other count *Not assessed*; the cutoff reads
  *normal 10 · no threshold*. The count is the one TRGT reports for the catalogued motif across
  the whole locus, flanking copies included, so it can run higher than the count the range
  describes. In the TRGT catalogue in use (TRID `CPEO_POLG`), POLG's common allele of 10 repeats
  counts 14 and stays *Not assessed*.
- A gene with several repeat tracts, each a locus of its own in STRchive (ARX, HOXA13), has
  thresholds per tract. A call whose TRID is the gene's name is read against the tract its region
  overlaps. If the region overlaps none of the tracts, or more than one, the call reads *Not
  assessed*, and its note names the tracts and says why.
- *Unknown*: no call.

**Aberrant only**, ticked when the page opens, keeps every locus whose status is not *Normal* or
*Unknown*, *Not assessed* included. The page header counts the calls not assessed. This page and the
genome tracks classify every call again, against the catalogue as it is now, and the tracks colour
each call by the status the repeat page gives it.

**Near a boundary.** TRGT counts the motif across the region its own catalogue gives a locus, which need
not be the clinical repeat number the locus's ranges count:

- at FMR1 it leaves out the AGG interruptions, so an allele reads 1 to 3 below its clinical number (a
  premutation of 55 repeats can read 53, in the grey zone);
- at HTT it also counts the CAG after the CAA interruption, one above the usual count;
- at POLG it counts four flanking GCT copies, so the common 10-repeat allele reads 14.

Until QA and the laboratory director have validated a locus's counting units, a count within 3 repeats
of a boundary the locus's classification uses is marked *Near a boundary* beside its call. Hover over
or focus the mark for the count, the nearest count on each side that reads another status, and what is
known of TRGT's count at the gene. The status itself is not changed: read the call with the mark in
mind. **Aberrant only** keeps such a locus whatever its status, the page header counts the calls near a
boundary, and a genome track says it in the locus's tooltip.

**Count correction.** QA can record a correction for a locus, in repeats, on the admin page **Repeat
catalogue**. CoGA adds it to TRGT's count before the count is classified, whether or not the locus is
validated. The call then shows the corrected count with TRGT's own in brackets, *17 / 36 (TRGT 18 /
37)*, and the card's **Count correction** line says the correction and its reason. The status, the mark,
the genome tracks and the signed record all use the corrected count. No locus has a correction until QA
enters one.

An administrator records each locus's validation and correction, with a reason, on the admin page
**Repeat catalogue**; a validated locus is no longer marked. Signing out freezes, beside each call, the
corrected count, TRGT's own count and the mark, and for each locus QA's record: the correction, whether
QA had validated its counting units, the reason, by whom and when. A locus validated, withdrawn or
corrected after sign-out makes the report say the repeat expansions changed since.

### Paraphase

Some genes lie in regions with nearly identical copies, such as *SMN1/SMN2* or *PMS2*, where standard
variant calling fails. The **Paraphase** page shows, per locus, what Paraphase found there for each
member: the copy number, and a status read from a catalogue of medical regions.

- Where the catalogue has a rule for the locus, the copy number reads as *normal*, *carrier* or
  *pathogenic*, or as *review* when it fits none of them.
- At a locus without a rule, a copy-number change or a fusion reads as *review*. A change is a copy
  number outside the normal range the catalogue records for the locus (6 for *NEB*, 4 for a gene with
  its pseudogene).
- On chrX a man has one copy and a woman two, by the sex Paraphase infers, else the pedigree's. Without
  a sex, a chrX value is not called normal.
- A field without a fixed normal value (*highest_total_cn*) and a no-call are no change.

**Copy-number changes and abnormal calls only**, ticked when the page opens, shows the regions with such
a change or with a member's call that reads *Pathogenic*, *Carrier* or *Review*: a fusion is reviewed
without any change in copy number. Untick it to see every region.

### Reviewing a repeat locus or a Paraphase region

Each repeat locus and Paraphase region has its own review, kept per family and reference assembly. Its card has the controls of every variant card: **Review**, **Exclude** and **Report**
toggle their tag, and **Tags & notes** opens the review. There you give your classification on the
type's own scale, beside CoGA's status, which it never changes:

- a repeat locus: *Pathogenic expansion*, *Reduced penetrance*, *Premutation*, *Intermediate (grey
  zone)*, *Normal* or *Uncertain significance*;
- a Paraphase region: *Affected*, *Carrier*, *Risk allele*, *Normal* or *Uncertain significance*.

The ACMG class tags and VUS tiers are for small variants and are not offered here. A review that
already holds one lists it, marked, so you can untick it. The card shows your
classification as *Review: …*. It adds *Differs from CoGA's status* when the classification agrees
with none of the members' calls (a *Normal* where every call reads *Pathogenic*, say). A review is of
the locus or region, so a classification that fits one member's call is not marked. **Exclude** folds
the card to its heading; the card stays in the list, **Aberrant only** included.

CoGA freezes the calls a classification, or the **Report** tag, rests on when you save it. When the
calls change after that (a new import, a locus whose counting units QA validated), the card says
*Changed since review*, and the tooltip names what changed. It says *No call since review* when the
family holds no call there any more. A save of tags or a note alone keeps the calls your classification
rests on. Sign-out checks them again (see *Clinical report*).

### mtDNA

On the **mtDNA analysis** page, each sample's row in the family summary gives its mean chrM depth, the
share of chrM covered, and a QC status judged against the lab's limits (**Sequencing QC thresholds**).
The depth comes from one coverage track that holds read depth: the sample's coverage BED when it was
declared as read depth, otherwise HiFiCNV's depth where the import stored it as read. These never
count: WisecondorX's and QDNAseq's bins, HiFiCNV's normalised depth and a coverage BED declared as a
log2 ratio (they hold log2 ratios, not depths), and a coverage BED that does not say what it holds. A
sample without a track of read depth shows the depth of its chrM calls, and the page's QC notes name
each track left out and why.

Each mtDNA variant has the same review as a small variant, and its card the same controls:
**Review**, **Exclude**, **Report**, **Tags & notes** and **ACMG classify**. It is one review: the
same variant on the **Small variants** page shows the same tags and note, and **Report** puts it on
the report with the other reported small variants. **ACMG classify** on a variant from the **mtDNA
analysis** page uses the mitochondrial rule set.

A card links to IGV, the chromosome view, OMIM (for a variant in a gene), MITOMAP and VarSome. VarSome
opens the variant on the revised Cambridge Reference Sequence, whatever the family's assembly. A
single-base change that alters the protein of one of the 13 protein-coding genes also links to
MitImpact, which holds only such changes.

[ACMG classification reference (including the CNV classifier)](/docs/reference/acmg-classification "further-reading")

### Ancestry PCA (exploratory)

**Exploratory — not for clinical use.** The **Ancestry (exploratory)** section, at the end of a
family's **Sample QC**, places its individuals beside public reference populations on four principal
components: genetic similarity, not an ancestry percentage or an ethnic identity. It is outside
CoGA's intended purpose, has not been validated, and is never part of the sign-out or of the signed
record. It reads the family's stored GRCh38 callsets, measures whether each covers a genome, an
exome or only part of the markers, and is calculated again automatically when the genotypes change.
For one individual you can choose another callset, give the regions of a gene panel or a BED file,
include an embryo or leave the individual out.

[Ancestry PCA reference](/docs/reference/ancestry-pca "further-reading")
