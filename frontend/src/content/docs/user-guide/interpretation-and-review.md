Once a candidate is in focus, open its review dialog to record an interpretation. Review state is
stored per family and is what makes a case auditable and resumable.

### What you can record

- **ACMG classification** — benign, likely benign, VUS, likely pathogenic, or pathogenic (classes
  1–5).
- **Tags** — collaboration tags (e.g. for review, send for validation, validated, excluded),
  classification tags, and project- or globally-defined custom tags. Tags are how you flag reported,
  candidate, research, or solved variants.
- **Notes** — free-text rationale that stays attached to the variant.

### Evidence at hand

Each variant row surfaces the context you need to classify: gene and consequence, the most relevant
transcript, ClinVar status, population frequency, and in-silico scores. Use the Gene Explorer for
deeper gene-level context and the Variant Explorer for cohort context.

### Structural-variant review

Structural variants have their own review surface with the same idea: classify, tag, and note, with
a dedicated tag for segmentation review. SV and small-variant review state are tracked separately
and summarised on the family page.

> **Review state is scoped to the family** and preserved through metadata edits. The same variant
> interpreted in two families carries two independent review records — which is exactly what the
> cohort views aggregate.
