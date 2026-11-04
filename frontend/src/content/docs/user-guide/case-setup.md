Choose the way in that matches how your data arrives.

```cards
Family builder | Build a new family by hand: add samples, set sex and roles, link parents and couples, and set affected and carrier status. Open to the analysts of a project whose role is analyst or higher, who add families to it, and to superusers and administrators; uploading a PED file here is for administrators.
Package import | A superuser or an administrator points at a family folder on the CoGA server, lets CoGA write its manifest, validates it, and imports the family with all its data in one job.
```

- **Package import is for superusers and administrators.** The rest of loading data is for
  administrators: single uploads on the **Upload family and sample data** page, and replacing an
  existing family, by a package import with *Overwrite* too. So is editing an existing family's members
  or structure. HPO terms are added, changed and removed by the analysts of the family's project (role
  analyst or higher), by superusers and by administrators.
- For a **monogenic NIPT** case, tick *Monogenic NIPT (cfDNA from maternal plasma)* in the family builder:
  see [Monogenic NIPT](#monogenic-nipt).
- Each kind of data switches on part of CoGA:
  - small variants: the small-variant page and Sample QC;
  - structural variants: the SV page and the variant summary;
  - TRGT calls: the repeat page;
  - Paraphase: the paralogue page;
  - mitochondrial calls: the mtDNA analysis;
  - coverage, segments, APCAD and haplotypes: the viewer tracks.

> **Reference data first.** Genes, cytobands, ClinVar and the other annotation layers are loaded per
> assembly. If a viewer cannot place coordinates or a gene search is empty, check that the reference
> data for the family's assembly is loaded.

[Data import reference (who may do what, reference data, package steps and checks)](/docs/reference/data-import "further-reading")
