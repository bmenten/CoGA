Record your interpretation as you go. Review state is kept per family: the same variant in two families
has two separate records.

### On each variant

Each row or card has **Review**, **Exclude** and **Report** (one-click tags), **Tags & notes** and **ACMG
classify**.

- **Tags.** The built-in tags are *Review*, *Send for validation*, *Validated*, *Validation did not
  confirm*, *Confident AR single hit*, *Excluded* and *Report*, the ACMG class tags, the VUS tiers and
  *Secondary finding*. An administrator can add custom tags for one project or for everyone.
- **Notes** keep your reasoning with the variant.
- **Report** puts the variant in the family's clinical report.

### ACMG classify

**ACMG classify** opens a dialog that pre-evaluates the ACMG/AMP criteria from the variant, the gene and
the family genotypes, and scores them on the ClinGen points scale.

- Each criterion shows as **checked** (the data supports it), **●** (consider), **✕** (the data argues
  against) or **n/a** (cannot apply). Hover it for the evidence; click to change it, and pick its
  strength. Only checked criteria count.
- The bar at the top shows the points, the class and, for a VUS, the tier (*Hot*, *Warm* or *Cold*).
- The header links to gnomAD, ClinVar, DECIPHER and a PubMed search on the gene and the patient's HPO
  terms.
- **Save classification** stores the class as the variant's classification and review tag, together
  with every criterion and your note; the server recomputes the class. It also freezes the evidence, so
  the report can later tell whether it changed.
- Opening a classified variant again shows the criteria as you saved them.

> **Decision support, not an autoclassifier.** The dialog pre-positions the criteria; you confirm them,
> adjust the strengths and add a note.

A variant opened from the **mtDNA analysis** page gets a mitochondrial rule set.

### Structural variants

The structural-variant page works the same way: **Review**, **Exclude**, **Report**, tags and notes, and
**ACMG (CNV)** for the ClinGen copy-number classifier. Small-variant and SV review are counted separately
on the family page.

[ACMG classification reference (rules, points, mtDNA, CNV)](/docs/reference/acmg-classification "further-reading")
