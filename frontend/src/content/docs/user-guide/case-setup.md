Intake is split into two focused pages. Use whichever matches how your data arrives.

```cards
Family Builder | Build a pedigree by hand or from a PED file: add samples, set sex and roles, assign parents and couples, and set phenotype and carrier status. Available to all users.
Package Import | Admins point at a backend-visible folder, discover a manifest, run a dry-run validation, then import the family and all of its assay layers in one job.
```

### What each data type unlocks

- **Small variants (SNV/indel)** enable the small-variant workbench and review.
- **Structural variants** enable the SV table, review, and CNV detail.
- **Repeat expansions (TRGT)** enable the repeat-expansion view.
- **Paraphase** enables segmental-duplication / paralogue resolution.
- **Mitochondrial calls** enable the mtDNA homo- and heteroplasmy analysis.
- **Coverage, segments, APCAD, haplotypes, and recombination** populate the genome and chromosome
  track viewers.

> **Assembly and reference first.** Genes, cytobands, ClinVar, DGV, and gnomAD annotations are
> loaded per assembly. If a viewer cannot resolve coordinates or a gene lookup is empty, check that
> the reference layers for that assembly are present.

[Data import reference (intake paths, assay layers, the manifest/dry-run flow)](/docs/reference/data-import "further-reading")
