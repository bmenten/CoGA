Record your interpretation as you go. Review state is kept per family: the same variant in two families
has two separate records.

### On each variant

Each row or card has **Review**, **Exclude** and **Report** (one-click tags), **Tags & notes** and **ACMG
classify**. An excluded variant stays in the list. Its card folds to its heading and a bar marked
*Excluded*: **Show card** unfolds it, and **Undo exclude** takes the tag off again. In the table view
its row is dimmed.

- **Tags.** The built-in tags are *Review*, *Send for validation*, *Validated*, *Validation did not
  confirm*, *Confident AR single hit*, *Excluded* and *Report*, the ACMG class tags, the VUS tiers and
  *Secondary finding*. A superuser or an administrator can add custom tags for one project or for
  everyone. A renamed custom tag stays on every review that holds it. A deleted one stays too, marked
  *(deleted)*: untick it to remove it from a review. It can no longer be added.
- **Class.** A row or card shows the review's class once. A class word typed as the classification
  (*VUS*) gives its class; otherwise the review's class tag gives it, the most severe of two;
  otherwise the classification as typed. A class tag of another class is not shown beside it. The VUS
  tiers and *Secondary finding* are tags, never the class. The filters, the Variant Explorer and
  **Download CSV** read the class the same way; the CSV also gives the classification as typed, in
  *Classification text*.
- **Review**, **Exclude** and **Report** change the tags only: the classification stays as stored.
  **Tags & notes** opens on the class shown. Choosing another class there, or none, replaces the
  classification and every class tag; left alone, the save keeps both as they are.
- **Notes** keep your reasoning with the variant.
- **Report** puts the variant in the family report.

### ACMG classify

**ACMG classify** opens a dialog that pre-evaluates the ACMG/AMP criteria from the variant, the gene and
the family genotypes, and scores them on the ClinGen points scale.

- Each criterion shows as **checked** (the data supports it), **●** (consider), **✕** (the data argues
  against) or **n/a** (cannot apply). Hover it for the evidence; click to change it, and pick its
  strength. Only checked criteria count.
- The bar at the top shows the points, the class and, for a VUS, the tier (*hot*, *warm* or *cold*).
- The header links to gnomAD, ClinVar, DECIPHER and a PubMed search on the gene and the patient's HPO
  terms.
- **Save classification** stores the class as the variant's classification and review tag, with every
  criterion and your note; the server recomputes the class. It also freezes the evidence the dialog
  read — the ClinVar significance, the consequence and LoF call, the population frequencies, the
  in-silico scores and, for a missense variant, the gene's missense Z — so the report can later tell
  whether it changed, and what.
- Opening a classified variant again shows the criteria as you saved them.

> **Decision support, not an autoclassifier.** The dialog pre-positions the criteria; you confirm them,
> adjust the strengths and add a note.

A variant opened from the **mtDNA analysis** page gets a mitochondrial rule set.

### Structural variants

The structural-variant page works the same way: **Review**, **Exclude**, **Report**, tags and notes, and
**ACMG (CNV)** for the ClinGen copy-number classifier. Saving a CNV classification also freezes its
evidence, so the report can tell whether it changed. An event called by more than one caller is listed
once per caller, with one shared review; the classification freezes the evidence of the row you opened
it from. Small-variant and SV review are counted separately on the family page.

[ACMG classification reference (rules, points, mtDNA, CNV)](/docs/reference/acmg-classification "further-reading")
