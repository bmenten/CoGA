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

A gene panel keeps an SV whose annotation names one of its genes or that overlaps one of its regions,
whatever the panel's size, as on the small-variant page: the Mendeliome also keeps an SV in one of its
genes whose annotation names that gene otherwise, and a small panel also keeps an SV whose annotation
names one of its genes without overlapping it, such as a gene a few kilobases away. With a gene panel
and a **Gene symbol** both filled in, an SV must meet both.

**Min length** and **Exact length**, under **Class and Breakpoints**, measure an SV by the **Length**
the list shows, without the minus sign of a deletion: the caller's SVLEN; for a call written as its
sequence without an SVLEN (as VCF 4.4 writes one), the bases it inserts or deletes; otherwise the end
minus the start. An insertion is as long as the sequence it inserts, although most callers write its end
at its start. A translocation (BND, TRA) has no length: every minimum keeps it, and no exact length
finds it.

A filtered search reads at most 50,000 SVs of the callset before it applies the filters, and with
**Phenotype prioritization** on (the default) it ranks at most 5,000 of the SVs that match. When a
search reaches either limit, a warning says *"Results may be incomplete"* (*"Ranking may be incomplete"*
when the list is ranked), the count shows a `+`, and an SV beyond the limit is not shown: narrow the
search with a region, a gene panel or a gene. A gene panel of any size, the Mendeliome included, and a
gene are applied before the 50,000 are read, so the limit counts only the SVs they keep. The family
report's list of reported SVs is not cut by this cap: it reads the reported SVs themselves, not the
first 50,000.

**ACMG (CNV)** on a row opens the ClinGen copy-number classifier (Riggs et al. 2020): choose
*Copy-number loss* or *Copy-number gain*, check the pre-selected criteria and add the rest.

**Variant summary** on the family page summarises the structural variants of every caller, HiFiCNV's
copy-number calls included: counts per chromosome and type, sharing between members, and size
distributions. The button appears when the family has structural variants. Small variants are not in
it, nor are the WisecondorX and QDNAseq segments, which are coverage tracks rather than variants.

### Repeat expansions

The **Repeat expansions** page lists each repeat locus with every member's call: the repeat count of
each allele, and a status read against the locus's thresholds in the repeat catalogue (STRchive):

- *Normal*, *Grey zone* (the intermediate range), *Premutation* (an FMR1 allele of 55–200 repeats,
  which can expand in a child) and *Pathogenic*.
- *Review: outside catalogued ranges*: a count the catalogue cannot classify at a locus that is
  pathogenic by contraction. A male's call with two different chrX alleles also asks for review, in
  a note that explains it.
- *Not assessed*: a call at a locus without thresholds. No catalogued locus matches its TRID (a locus
  the catalogue lacks, or a TRID spelled differently), or the catalogue gives the locus no threshold.
  CoGA cannot say whether such a count is normal; its cutoff reads *No reference cutoff*. Where the
  catalogue gives a locus a benign range but no threshold (STRchive's POLG: 10 repeats), a count
  inside the range reads *Normal* and any other count *Not assessed*; the cutoff reads
  *normal 10 · no threshold*. The count is the one TRGT reports for the catalogued motif across
  the whole locus, flanking copies included, so it can run higher than the count the range
  describes: in the TRGT catalogue in use (TRID `CPEO_POLG`), POLG's common allele of 10 repeats
  counts 14 and stays *Not assessed*.
- A gene with several repeat tracts, each a locus of its own in STRchive (ARX, HOXA13), has
  thresholds per tract. A call whose TRID is the gene's name is read against the tract its region
  overlaps. If the region overlaps none of the tracts, or more than one, the call reads *Not
  assessed*, and its note names the tracts and says why.
- *Unknown*: no call.

**Aberrant only** keeps every locus whose status is not *Normal* or *Unknown*, *Not assessed*
included, and the page header counts the calls not assessed. The genome tracks colour each locus by
the status its call was imported with, and keep it until the call is imported again; this page
classifies every call again, against the catalogue as it is now.

**Near a boundary.** TRGT counts the motif across the region its own catalogue gives a locus, which need
not be the clinical repeat number the locus's ranges count. At FMR1 it leaves out the AGG
interruptions, so an allele reads 1 to 3 below its clinical number (a premutation of 55 repeats can
read 53, in the grey zone); at HTT it also counts the CAG after the CAA interruption, one above the
usual count; at POLG it counts four flanking GCT copies, so the common 10-repeat allele reads 14. Until
QA and the laboratory director have validated a locus's counting units, a count within 3 repeats of a
boundary the locus's classification uses is marked *Near a boundary* beside its call. Hover over or
focus the mark for the count, the nearest count on each side that reads another status, and what is
known of TRGT's count at the gene. The status itself is not changed: read the call with the mark in
mind. **Aberrant only** keeps such a locus whatever its status, the page header counts the calls near a
boundary, and a genome track says it in the locus's tooltip. An administrator records each locus's
validation, with its reason, on the admin page **Repeat Catalogue**; a validated locus is no longer
marked.

### mtDNA

Each sample's card on the **mtDNA analysis** page gives its mean chrM depth and the share of chrM
covered, and a QC status judged against the lab's limits (**Sequencing QC Thresholds**). The depth
comes from one coverage track that holds read depth: the sample's coverage BED when it was declared as
read depth, otherwise HiFiCNV's depth where the import stored it as read. WisecondorX's and QDNAseq's
bins, HiFiCNV's normalised depth and a coverage BED declared as a log2 ratio hold log2 ratios, not
depths, and never count, nor does a coverage BED that does not say what it holds. A sample without a
track of read depth shows the depth of its chrM calls, and the page's QC notes name each track left
out and why.

**ACMG classify** on a variant from the **mtDNA analysis** page uses the mitochondrial rule set.

[ACMG classification reference (including the CNV classifier)](/docs/reference/acmg-classification "further-reading")
