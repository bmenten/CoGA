Tag a variant with **Report** and it joins the family's clinical report (the **Report** button on the
family page). The report drafts readable text per reported variant — description, ACMG reasoning, gene
context and phenotype overlap — and is where the case is signed out.

### What the report shows

- A **provenance footer** with the versions behind the data: the pipeline tools, the assembly, the gene
  loci and the Monarch release. It prints with the report.
- An **evidence-drift** banner when a classification's evidence changed after it was made (for example
  a new ClinVar significance). Re-review those variants.
- The **Classification audit trail**: who classified, tagged or annotated what, and when. Entries can
  never be changed or deleted.
- The **Analysis pipeline settings** recorded at import.

### Signing out

**Sign out report** freezes the reported result into a numbered version with a unique fingerprint. It
stops for three things:

1. **An assembly outside the validated scope** (the page says *Not validated for clinical use*): the
   report cannot be signed out.
2. **Evidence drift**, including a reported variant that was never saved through **ACMG classify** (its
   evidence cannot be checked): re-review, or acknowledge with a reason.
3. **Sample QC** that failed, or could not confirm the pedigree: acknowledge with a reason.

A reason you give is frozen into the signed version and written to the audit trail. Signing out again
creates a new version (**Amend sign-out**); earlier versions are never overwritten.

> **Only authorised signatories sign out.** CoGA lets any project member press **Sign out report** and
> does not check signing authority. The laboratory decides who may sign; CoGA records who did.

### Is this the signed report?

The page always shows the **current** data and compares it with the latest signed version: green when
they match, amber when something changed since (the record names what), grey when the check could not
be made (*treat it as unsigned*). **Download signed version N (JSON)** returns the frozen version.

[Report traceability and sign-out reference (footer, drift, audit, the three checks)](/docs/reference/clinical-traceability "further-reading")
