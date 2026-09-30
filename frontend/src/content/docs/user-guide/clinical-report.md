Tag a variant with **Report** and it joins the family's clinical report (the **Report** button on the
family page). The live report drafts readable text per reported variant — description, ACMG reasoning,
gene context and phenotype overlap — and is where the case is signed out. Once a case is signed out, the
**Report** button opens its signed version.

### What the live report shows

- Every **reported variant**, small variants and structural variants alike. If a list cannot hold them
  all, an *Incomplete* warning says so, on screen and in print, so a printout cannot pass for the whole
  list.
- A **provenance footer** with the CoGA version that produced the page, the in-house IVD statement and
  the manufacturer, and the versions behind the data: the pipeline tools, the assembly, the gene loci,
  the Monarch release and the HPO release. It prints with the report.
- An **evidence-drift** banner when a classification's evidence changed after it was made: for example
  a new ClinVar significance, or a structural variant that now overlaps other genes. Re-review those
  variants.
- The **Classification audit trail**: who classified, tagged or annotated what, and when. Entries can
  never be changed or deleted.
- The **Analysis pipeline settings** recorded at import.
- An **Import incomplete** warning at the top when a data import left the family partly loaded. It
  names the datasets that failed and the import job that holds their errors. The same warning shows
  on every family page until an import completes the family.

### Signing out

**Sign out report** freezes the reported result into a numbered version with a unique fingerprint. It
stops for five things:

1. **An assembly outside the validated scope** (the page says *Not validated for clinical use*): the
   report cannot be signed out.
2. **Data being written**: a data import of the family is queued or running, or an upload or deletion
   is changing its variants. Sign out once it has finished; there is no override.
3. **Evidence drift**, of a small variant, a structural variant or a CNV, including a reported variant
   that was never saved through **ACMG classify** or **ACMG (CNV)** (its evidence cannot be checked):
   re-review, or acknowledge with a reason.
4. **Sample QC** that failed, or could not confirm the pedigree: acknowledge with a reason.
5. **An incomplete import** (the page says *Import incomplete*): re-run the import, or acknowledge with
   a reason.

A reason you give is frozen into the signed version and written to the audit trail. Signing out again
creates a new version (**Amend sign-out**); earlier versions are never overwritten. After signing out,
the page shows the version you signed.

> **Only authorised signatories sign out.** CoGA lets any project member press **Sign out report** and
> does not check signing authority. The laboratory decides who may sign; CoGA records who did.

### Is this the signed report?

Only the signed version is. A signed case opens on its **latest signed version**, drawn from the frozen
record: *Signed version N — signed out by … on …*, with its fingerprint. It shows what was signed: each
reported variant's classification, criteria, evidence and note, and the checks at sign-out. It says what
the record does not hold — the variant description, the gene and phenotype context, the audit trail —
and never fills that in from current data. **Print signed version N** prints it, and **Download signed
version N (JSON)** returns the record itself. *Signed versions* lists the earlier ones.

**Open the live report** shows the current data. It says *This is the live report, not signed version
N*, and whether it still matches: amber when something changed since (it names what), grey when the
check could not be made (*treat it as unsigned*). Its printout starts with *Not the signed report*.

[Report traceability and sign-out reference (footer, drift, audit, the sign-out checks)](/docs/reference/clinical-traceability "further-reading")
