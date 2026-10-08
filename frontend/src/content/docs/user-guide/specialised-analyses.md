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

A filtered search reads at most 50,000 SVs of the callset before it applies the filters, and with
**Phenotype prioritization** on (the default) it ranks at most 5,000 of the SVs that match. When a
search reaches either limit, a warning says *"Results may be incomplete"* (*"Ranking may be incomplete"*
when the list is ranked), the count shows a `+`, and an SV beyond the limit is not shown: narrow the
search with a region, a gene panel or a gene. The clinical report's list of reported SVs is not cut by
this cap: it reads the reported SVs themselves, not the first 50,000.

**ACMG (CNV)** on a row opens the ClinGen copy-number classifier (Riggs et al. 2020): choose
*Copy-number loss* or *Copy-number gain*, check the pre-selected criteria and add the rest.

**Variant summary** on the family page summarises the structural variants of every caller, HiFiCNV's
copy-number calls included: counts per chromosome and type, sharing between members, and size
distributions. The button appears when the family has structural variants. Small variants are not in
it, nor are the WisecondorX and QDNAseq segments, which are coverage tracks rather than variants.

### mtDNA

**ACMG classify** on a variant from the **mtDNA analysis** page uses the mitochondrial rule set.

[ACMG classification reference (including the CNV classifier)](/docs/reference/acmg-classification "further-reading")
