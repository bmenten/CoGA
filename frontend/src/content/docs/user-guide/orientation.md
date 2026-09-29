CoGA is an integrated review environment for clinical genomic analysis. Like other family-based
platforms, it keeps the pedigree, the assay layers, and your interpretation together so a case is
reviewed as a whole rather than as a set of disconnected tests.

> **Mental model:** a *project* controls access and assembly, a *family* is the case, *samples*
> carry the assay data, and *review state* (classifications, tags, notes) is the interpretation
> layer on top.

### The objects you work with

- **Projects** define who can see the data and which reference assembly applies. Your access — and
  every cohort count you see — is scoped to the projects you belong to (administrators see
  everything).
- **Families** are the unit of case review. They group related samples and carry pedigree meaning
  (relationships, roles, affected status).
- **Samples** hold per-sample assay layers: genotypes, coverage, segments, repeat expansions,
  Paraphase, and more.
- **Reference data** (genes, transcripts, cytobands, ClinVar, gnomAD, blacklist, clinical CNVs, DGV)
  is assembly-scoped and shared across projects on that assembly.
- **Review state** — ACMG classifications, tags, notes, and saved filter presets — is layered on top
  of the raw data and is what survives between sessions.

### The analyst journey

1. Search for families, cases, or individuals through the dashboard.
2. Capture or review the pedigree and associated phenotype (HPO terms).
3. Examine small variants, structural variants, recombinations, mtDNA variants, (triplet) repeat
   expansions, and complex genomic regions through one family-based workspace.
4. Prioritise candidates with inheritance-aware, evidence-rich filtering.
5. Interpret each candidate and record an ACMG class, tags, and notes.
6. Put findings in cohort context and follow up visually (genome views, IGV).
7. Automatically report interesting variants.
