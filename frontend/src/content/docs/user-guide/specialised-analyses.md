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
search with a region, a gene panel or a gene. The family report's list of reported SVs is not cut by
this cap: it reads the reported SVs themselves, not the first 50,000.

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
- *Unknown*: no call.

**Aberrant only** keeps every locus whose status is not *Normal* or *Unknown*, *Not assessed*
included, and the page header counts the calls not assessed. The genome tracks colour each locus by
the status its call was imported with, and keep it until the call is imported again; this page
classifies every call again, against the catalogue as it is now.

### mtDNA

Each sample's card on the **mtDNA analysis** page gives its mean chrM depth and the share of chrM
covered, and a QC status judged against the lab's limits (**Sequencing QC Thresholds**). The depth
comes from one coverage track that holds read depth: the sample's coverage BED, otherwise HiFiCNV's
depth where the import stored it as read. WisecondorX's and QDNAseq's bins and HiFiCNV's normalised
depth hold log2 ratios, not depths, and never count. A sample without a track of read depth shows the
depth of its chrM calls.

**ACMG classify** on a variant from the **mtDNA analysis** page uses the mitochondrial rule set.

[ACMG classification reference (including the CNV classifier)](/docs/reference/acmg-classification "further-reading")
