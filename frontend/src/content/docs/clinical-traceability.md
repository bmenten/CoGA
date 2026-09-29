# Clinical report traceability & sign-out — reference

The **clinical report** (the **Report** button on the family page) is also where a case is traced and
signed out. It ties the reported result to what produced it — the annotation and reference versions,
the reported variants, each classification and its evidence — and freezes that into a signed record
that can never change.

How to prepare and sign out a report is in the [user guide](/docs), section *Clinical report and
sign-out*. This page holds the rules.

---

## Why traceability matters

A reported result is only defensible if you can answer later, exactly: *which data, which versions and
whose decisions produced this?* Annotation sources move — a new ClinVar or gnomAD release — so a
classification made last month may rest on evidence that has since changed. CoGA makes the versions,
the changes and the decisions explicit and permanent.

The report carries four things.

## 1. Provenance footer — which versions

A footer at the end of the report states when it was generated and which versions backed the data:

- the **pipeline** layer: the tools that produced the family's annotated input (for example VEP,
  ClinVar, gnomAD, dbNSFP, SpliceAI, GenCC, PanelApp);
- the **reference** layer: what CoGA itself loaded — the genome assembly (with its release date), the
  gene loci (their source and import date), the Monarch release and the HPO release (with its date).

The pipeline versions are taken from the family's import manifest; there is no screen to edit them. A
version that is not known is left out, never guessed. The footer prints with the report and is part of
the signed record.

The footer also names the software: *Software: CoGA X.Y.Z (commit)*, the build that produced the page,
followed by the in-house IVD statement and the manufacturer. The report asks for the build each time it
opens. The build that signed a version is named in its sign-out record; after a CoGA update the two can
differ.

## 2. Evidence-drift banner — has anything changed

When you save a classification with **ACMG classify**, CoGA freezes the evidence it rests on: a
fingerprint of the variant's annotation and its ClinVar significance at that moment.

When you open the report, each frozen classification is compared with the current annotation. An amber
banner lists every classification whose evidence changed:

- the ClinVar significance changed (for example *ClinVar Uncertain significance → Pathogenic*);
- the annotation changed in another way, or cannot be compared because a fingerprint is missing
  (*annotation set changed*);
- the variant is no longer in the data.

Re-review a listed variant before sign-out.

**Reported variants without frozen evidence.** A variant tagged **Report** that was never saved through
**ACMG classify** has no frozen evidence, so its evidence cannot be checked. The banner does not list
it, but sign-out does: it counts as drift and needs an acknowledgement (see *The sign-out checks*).

## 3. Classification audit trail — who did what, when

The **Classification audit trail** lists, most recent first, every clinical action on the family's
variants: who classified, tagged or annotated which variant, when, and what changed (before → after),
including each sign-out.

- *Classification VUS (class 3) → Likely pathogenic (class 4)*
- *Tags added report*
- *Report signed out (v2) — 3 reported variant(s)*

Each entry is written together with the change itself, and entries can never be changed or deleted.
This is the record of clinical actions; the record of who opened what (**Admin → Audit Logs**) is
separate.

## 4. Case sign-out — freeze the result

**Sign out report** freezes the reported result into a numbered version with a unique fingerprint (a
SHA-256 content hash):

- the provenance footer (the versions);
- the reported small variants, each with its classification, ACMG criteria, tags, note and frozen
  evidence;
- the reported structural variants and CNVs, with their classification, CNV criteria, tags and note;
- the drift state, the Sample QC verdict, and the sequencing-QC verdicts with the cut-offs they were
  judged against;
- whether the family's data imported completely, and if not, which datasets failed;
- the CoGA software version that produced it.

A signed version can never change; any later tampering would show as a fingerprint mismatch.

### The sign-out checks

Sign-out stops at each of these, in this order:

| Check | Stops when | To go on |
| --- | --- | --- |
| **Assembly scope** | The family is on an assembly outside the validated scope (GRCh38 unless the laboratory set otherwise). The pages carry *Not validated for clinical use*. | No override: the report cannot be signed out. |
| **Evidence drift** | A reported classification drifted (banner above), or has no frozen evidence. | Re-review, or acknowledge with a reason (*Evidence drift — acknowledgement required*). |
| **Sample QC** | Sample QC failed, or a check that confirms the pedigree could not run for lack of data (a parent–child or sibling relationship, a Mendelian check, NIPT paternity or maternal lineage). | Acknowledge with a reason (*Sample-integrity QC — acknowledgement required*). |
| **Incomplete import** | A data import for the family partly failed, so some of its data is missing. The pages carry *Import incomplete* (below). | Re-run the import, or acknowledge with a reason (*Incomplete import — acknowledgement required*). |

An acknowledgement and its reason are frozen into the signed version and written to the audit trail,
so "signed out over a known problem, and why" is part of the permanent record.

If the family's project cannot be loaded, the assembly — and so the scope — is not known: the pages say
*Validated scope not confirmed*, with **Retry**, and the report waits until it loads.

### Import incomplete

If a data import fails for some datasets and leaves the family partly loaded, CoGA keeps what did
load and marks the family as incomplete. Every family page, the report included, then shows
*Import incomplete*: the datasets that failed and those that did import, when, and the import job
whose record holds each dataset's error. The warning prints with the report.

Results on such a family can lack whole datasets, for example all its structural variants. Re-run
the import to complete the family; a complete import removes the warning. Signing out before then
needs an acknowledgement with a reason, and the signed version records which datasets were missing.

### Who may sign out

Only people the laboratory has authorised as signatories. CoGA lets any member of the project press
**Sign out report** and does not check signing authority itself. It records who signed each version, in
the signed record and in the audit trail.

### Amendments

Signing out again creates a new version (v2, v3, …); earlier versions are never overwritten. Once a
case is signed out, the button reads **Amend sign-out**.

---

## Is this page the signed report?

The report page always shows the **current** data and checks it against the latest signed version.

- **Green** — *✓ Signed out — version 2 by … · This page matches signed version 2.*
- **Amber** — something changed after sign-out (a review, a report tag, a re-import, a QC cut-off). The
  record names what changed; the page is then *not* the signed report, and a printout says so at the
  top. Sign out again to issue a new version.
- **Grey** — *This page could not be checked against signed version N — treat it as unsigned.*

**Download signed version N (JSON)** returns the frozen version itself.

**What a signed version could not capture.** If a lookup fails while the version is frozen — the QC
cut-offs, or the version of the assembly, the gene loci, Monarch or HPO — sign-out still goes ahead, but
that part is recorded as unavailable, not as empty, and the record says so: *Not captured in signed
version 2: …*. The audit trail lists the same parts. An HPO ontology imported from a file that recorded
no release is marked the same way (*release not recorded*).

A version signed before CoGA recorded the HPO release does not name it. The record says so (*HPO
(signed before CoGA recorded its version)*), and that alone does not turn the page amber.

**When part of the report cannot be loaded.** The page never shows a part it could not load as empty.
If the family or a list of reported variants cannot be loaded, the page shows only *Report could not be
loaded*, with **Retry**. If the sign-out record cannot be loaded, the page says it is not known whether
the case is signed and does not offer sign-out. Any other part that failed (a gene description, the HPO
terms, the drift check, the audit trail, the annotation versions, the CoGA version) is marked where it
belongs, and a printout starts with *Incomplete — … could not be loaded*. The NIPT report does the same
for the fetal fraction, the coverage check and the CoGA version.
