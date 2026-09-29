Choose the way in that matches how your data arrives.

```cards
Family Builder | Build a new family by hand: add samples, set sex and roles, link parents and couples, and set affected and carrier status. Open to every user; uploading a PED file here is for administrators.
Package Import | An administrator points at a family folder on the CoGA server, lets CoGA write its manifest, validates it, and imports the family with all its data in one job.
```

- **Loading data is for administrators**: Package Import, single uploads on the **Upload family and
  sample data** page, and replacing an existing family. Editing an existing family's members or
  structure, and adding HPO terms, are for administrators too.
- For a **monogenic NIPT** case, tick *Monogenic NIPT (cfDNA from maternal plasma)* in Family Builder:
  see [Monogenic NIPT](#monogenic-nipt).
- Each kind of data switches on part of CoGA: small variants the small-variant page and Sample QC;
  structural variants the SV page and the variant summary; TRGT calls the repeat page; Paraphase the
  paralogue page; mitochondrial calls the mtDNA analysis; coverage, segments, APCAD and haplotypes the
  viewer tracks.

> **Reference data first.** Genes, cytobands, ClinVar and the other annotation layers are loaded per
> assembly. If a viewer cannot place coordinates or a gene search is empty, check that the reference
> data for the family's assembly is loaded.

[Data import reference (who may do what, reference data, package steps and checks)](/docs/reference/data-import "further-reading")
