Tag a variant with **Report** to add it to the family report (the **Report** button on the family page).
The live report drafts text per reported variant — description, ACMG reasoning, gene context and
phenotype overlap — and is where you sign out the analysis. After sign-out, the **Report** button opens
the signed version.

**Sign-out closes the analysis; the medical sign-off is in the EPD.** **Sign out report** is the
analytical sign-off: it closes the analysis and freezes it into a signed version. The clinical
report is drafted and medically signed in the hospital's electronic patient record (EPD), not in CoGA.
It refers to exactly one signed version: the one it rests on.

### What the live report shows

- Every **reported variant**, small and structural. If a list cannot hold them all, an *Incomplete*
  warning says so, on screen and in print, so a printout cannot pass for the whole list.
- Each variant's **class** as the family page shows it: a class word in the classification (*VUS*),
  else its class tag, else the text. When the ACMG classification, or a structural variant's CNV
  scoring, gives another class, that score stands beside it: *VUS - class 3 (ACMG score: Likely
  pathogenic - class 4 · 8 pts)*. A structural variant whose review shows no class reads *Not
  classified*, even with a CNV score.
- The **repeat expansions and Paraphase findings** marked **Report**: gene and disease, every member's
  call with CoGA's status, your classification and note, and the marks *differs from CoGA's status* and
  *Changed since review* where they apply. Without any, the section says so.
- A **provenance footer**, printed with the report: the CoGA version that produced the page, the
  in-house IVD statement and the manufacturer, and the versions behind the data (the pipeline tools,
  the assembly, the gene loci, the Monarch release and the HPO release).
- An **evidence-drift** banner when a classification's evidence changed after it was made: for example
  a new ClinVar significance, REVEL score or gnomAD frequency (*REVEL 0.412 → 0.951*), consequence or
  LoF call (*LoF LC → HC*), missense Z for a missense variant's gene, or a structural variant that now
  overlaps other genes. Re-review those variants.
- The **Clinical audit trail**: who classified, tagged or annotated what, who reviewed a
  compound-heterozygous pair, who changed the pedigree or the family's phenotypes, and when. Entries
  can never be changed or deleted. Only analysts, superusers and administrators see it; a viewer, or a
  reader of the family's project, sees on screen and in print that it is not shown. It opens on the
  200 most recent entries; when there are more, it says how many it shows, and **Show older events**
  loads the next 200. A printout of only part of the trail says so.
- The **Analysis pipeline settings** recorded at import.
- An **Import incomplete** warning at the top when a data import left the family partly loaded or has
  not finished (still running, or stopped part-way, for example by a server restart). It names the
  import job and the datasets that failed or were not finished. It also names:
  - a single upload that has not finished, or that failed and could not put back what it deleted, as a
    single-file upload;
  - an unfinished deletion of the family's data, as a deletion of this family's data: an
    administrator's deletion of a sample or of the family, a PED upload that replaces the family, or a
    structure change that clears its data.

  The warning shows on every family page until an import or an upload has brought those datasets again.

### Signing out

**Sign out report** freezes the reported result into a numbered version with a unique fingerprint. The
version also holds what the analysis looked at:

- each reported variant's description as you reviewed it;
- the gene panels the family's searches applied;
- the reference-data releases;
- the repeat-expansion and Paraphase results, with each repeat count near a boundary marked, TRGT's own
  count beside a count QA's correction changed, and each locus's correction and whether QA had
  validated TRGT's counting units there;
- what each coverage track held (read depth, a log2 ratio or unknown), and whether an upload or a
  package loaded it;
- in a PGT family, each embryo's call at the region of interest as the family page shows it, with its
  phasing QC and that QC's verdict against the thresholds;
- in a monogenic NIPT family, its NIPT results (the NIPT report uses this same sign-out; see the NIPT
  chapter).

The EPD refers to the signed version by its **report code** (for example `7KQM2X-2-4PHT`). Sign-out
stops for six things:

1. **An assembly outside the validated scope** (*Not validated for clinical use*): the report cannot be
   signed out.
2. **Data being written**: a data import of the family is queued or running, or an upload or a deletion
   of its variants is changing its data. Sign out once it has finished; there is no override.
3. **Evidence drift** of a small variant, a structural variant or a CNV, including a reported variant
   never saved through **ACMG classify** or **ACMG (CNV)** (its evidence cannot be checked), and of a
   repeat or Paraphase review with a classification or the **Report** tag whose calls changed since.
   Re-review, or acknowledge with a reason. The dialog lists each classification and what moved.
4. **Sample QC** that failed or could not confirm the pedigree or a sample's identity, and, for a NIPT
   family, a failed NIPT quality check: acknowledge with a reason. Before you write it, the dialog lists
   every concern under its own heading: failed checks, checks that could not run, failed NIPT quality
   checks, and warnings (which do not stop sign-out on their own). Each concern names the sample, pair
   or child it is about; your reason covers all of them.
5. **An incomplete import** (*Import incomplete*): import what failed again, or acknowledge with a
   reason.
   - After an import that stopped part-way, re-run it with **overwrite**: an update skips data that is
     already there, partly written data too.
   - After a failed overwrite that could not put the family's data back, import the whole family again
     with **overwrite** (every dataset the warning names, not only the failed ones), and upload again
     with **overwrite** what an upload brought.
   - After an unfinished single upload or deletion, upload or import again with **overwrite** what the
     warning names.
   - After an administrator's unfinished deletion of a sample, deleting the sample again also removes
     what the warning names of it.
6. **Repeat and Paraphase calls without a review** (no tag, note or classification at the locus or
   region): a repeat call reading *Pathogenic*, *Premutation*, *Review* or *Not assessed*, a count *Near
   a boundary*, or a Paraphase call reading *pathogenic*, *carrier* or *review*. Review it, or acknowledge
   with a reason. A grey-zone call alone does not stop sign-out.

Your acknowledgement covers exactly what its dialog lists. If the concerns change before you click
**Sign out anyway**, nothing is signed out: the dialog shows the current concerns, says they changed,
and asks for your reason again.

A PGT embryo call whose phasing QC does not meet its thresholds (*⚠ phasing QC* on the family page)
does not stop sign-out: the call stands, you decide on it, and the signed version records the warning.

Every reason you give is frozen into the signed version and written to the audit trail. Signing out
again (**Amend sign-out**) creates a new version; earlier versions are never overwritten. A clinical
report that rests on the amended analysis refers to the new version. After sign-out the page shows the
signed version and its report code: quote that code in the EPD report. A mistyped code is refused as a
typo, never taken for another record's. **Report codes** in the main menu finds the signed version a
code names.

> **Only a supervisor signs out.** **Sign out report** is for a supervisor of the family's project, a
> superuser or an administrator. For an analyst, a viewer or a reader the button is disabled and says
> why, and CoGA refuses the sign-out. The laboratory decides who gets the supervisor role; CoGA records
> who signed. The medical sign-off is given in the EPD.

### Is this the signed report?

Only the signed version is. A signed case opens on its **latest signed version**, drawn from the frozen
record: *Signed version N — signed out by … on …*, with its report code and fingerprint. It shows what
was signed:

- each reported variant's description, classification, criteria, evidence and note. The class is the
  one its review showed when you signed, with the ACMG or CNV score beside it as on the live report;
- in a PGT family, the embryo calls (*PGT embryo results*, with each call's QC verdict and the
  thresholds);
- in a monogenic NIPT family, its NIPT results (*NIPT results*);
- the gene panels applied;
- the repeat expansions (each count near a boundary marked beside its call, *⚠ Near a boundary*; a
  corrected count with TRGT's own in brackets, *18 (TRGT 19)*; and each locus's counting units and
  correction as QA had recorded them) and Paraphase results;
- the coverage tracks (*Coverage tracks*: what each held, and from which file);
- the reference-data releases and the checks at sign-out.

It says what the record does not hold — the population frequencies and predictions, the gene and
phenotype context, the audit trail — and never fills that in from current data.
**Print signed version N** prints it; **Download signed version N (JSON)** returns the record itself.
*Signed versions* lists the earlier ones.

**Open the live report** shows the current data. It says *This is the live report, not signed version
N*, and whether it still matches: amber when something changed since (it names what), grey when the
check could not be made (*treat it as unsigned*). It also names what version N does not hold and so
could not compare (*Not in signed version N, so not compared: …*). Its printout starts with *Not the
signed report*.

[Report traceability and sign-out reference (footer, drift, audit, the sign-out checks)](/docs/reference/clinical-traceability "further-reading")
