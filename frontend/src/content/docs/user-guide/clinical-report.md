Tag a variant with **Report** and it joins the family report (the **Report** button on the family
page). The live report drafts readable text per reported variant — description, ACMG reasoning, gene
context and phenotype overlap — and is where the analysis is signed out. Once it is signed out, the
**Report** button opens its signed version.

**Sign-out closes the analysis; the medical sign-off is in the EPD.** **Sign out report** is the
analyst's analytical sign-off: it closes the analysis and freezes it into a signed version. The clinical
report is drafted and medically signed in the hospital's electronic patient record (EPD), not in CoGA,
and refers to exactly one signed version: the one it rests on.

### What the live report shows

- Every **reported variant**, small variants and structural variants alike. If a list cannot hold them
  all, an *Incomplete* warning says so, on screen and in print, so a printout cannot pass for the whole
  list.
- A **provenance footer** with the CoGA version that produced the page, the in-house IVD statement and
  the manufacturer, and the versions behind the data: the pipeline tools, the assembly, the gene loci,
  the Monarch release and the HPO release. It prints with the report.
- An **evidence-drift** banner when a classification's evidence changed after it was made: for example
  a new ClinVar significance, a new REVEL score or gnomAD frequency (*REVEL 0.412 → 0.951*), or a
  structural variant that now overlaps other genes. Re-review those variants.
- The **Classification audit trail**: who classified, tagged or annotated what, and when. Entries can
  never be changed or deleted. It opens on the 200 most recent entries; when there are more, it says
  how many it shows, and **Show older events** loads the next 200. A printout that shows only part of
  the trail says so.
- The **Analysis pipeline settings** recorded at import.
- An **Import incomplete** warning at the top when a data import left the family partly loaded, or
  has not finished: it is still running, or it stopped part-way (for example the server restarted).
  It names the datasets that failed, or that the import had not finished, and the import job. A
  single upload that has not finished, or that failed and could not put back what it deleted, is
  named the same way, as a single-file upload. The same warning shows on every family page until an
  import, or an upload, has brought those datasets again.

### Signing out

**Sign out report** is your analytical sign-off: it closes the analysis and freezes the reported result
into a numbered version with a unique fingerprint, with what the analysis looked at: each reported
variant's description as you reviewed it, the gene panels the family's searches applied, the
reference-data releases, and the repeat-expansion and Paraphase results; for a monogenic NIPT family,
also its NIPT results (the NIPT page signs out through this same sign-out, see the NIPT chapter). The
EPD refers to the signed version by its **report code** (for example `7KQM2X-2-4PHT`). It stops for
five things:

1. **An assembly outside the validated scope** (the page says *Not validated for clinical use*): the
   report cannot be signed out.
2. **Data being written**: a data import of the family is queued or running, or an upload, or a
   deletion of its variants, is changing its data. Sign out once it has finished; there is no override.
3. **Evidence drift**, of a small variant, a structural variant or a CNV, including a reported variant
   that was never saved through **ACMG classify** or **ACMG (CNV)** (its evidence cannot be checked):
   re-review, or acknowledge with a reason.
4. **Sample QC** that failed, or could not confirm the pedigree or a sample's identity, and for a NIPT
   family a NIPT quality check that failed: acknowledge with a reason. Each concern names the sample,
   the pair or the child it is about.
5. **An incomplete import** (the page says *Import incomplete*): import what failed again, or
   acknowledge with a reason. After an import that stopped part-way, re-run it with **overwrite**: an
   update skips data that is already there, partly written data too. After a failed overwrite that
   could not put the family's data back, import the whole family again with **overwrite**, every
   dataset the warning names and not only those that failed, and upload again with **overwrite**
   what an upload brought. After a single upload that did not finish, upload again with
   **overwrite** what the warning names.

A reason you give is frozen into the signed version and written to the audit trail. Signing out again
creates a new version (**Amend sign-out**); earlier versions are never overwritten, and a clinical
report that rests on the amended analysis refers to the new version. After signing out, the page shows
the version you signed and its report code: quote that code in the EPD report. A mistyped code is
refused as a typo, never taken for another record's. **Report codes** in the main menu finds the
signed version a code names.

> **Only people the laboratory authorises sign out.** CoGA lets any project member press **Sign out
> report** and does not check that authority. The laboratory decides who may sign out an analysis;
> CoGA records who did. The medical sign-off is given in the EPD.

### Is this the signed report?

Only the signed version is. A signed case opens on its **latest signed version**, drawn from the frozen
record: *Signed version N — signed out by … on …*, with its report code and fingerprint. It shows what
was signed: each reported variant's description, classification, criteria, evidence and note, the gene
panels applied, the repeat expansions and Paraphase results, the reference-data releases, a NIPT
family's NIPT results, and the checks at sign-out. It says what the record does not hold — the population frequencies and predictions, the
gene and phenotype context, the audit trail — and never fills that in from current data. **Print
signed version N** prints it, and **Download signed version N (JSON)** returns the record itself.
*Signed versions* lists the earlier ones.

**Open the live report** shows the current data. It says *This is the live report, not signed version
N*, and whether it still matches: amber when something changed since (it names what), grey when the
check could not be made (*treat it as unsigned*). Its printout starts with *Not the signed report*.

[Report traceability and sign-out reference (footer, drift, audit, the sign-out checks)](/docs/reference/clinical-traceability "further-reading")
