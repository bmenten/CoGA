Tag a variant with **report** and it joins the family's clinical report (the **Report** link in the
workspace *Variants* section). The report drafts readable prose for each reported variant —
description, ACMG motivation, gene context and phenotype overlap — and is also the case's
**provenance and sign-out** surface: it locks the result to exactly what produced it.

> **Why this matters.** Annotation sources move — a new ClinVar or gnomAD release — so a
> classification made last month may rest on evidence that has since changed. CoGA makes the
> versions, the changes, and the decisions explicit and permanent, so a signed-out report can be
> reproduced and audited.

### 1 · Provenance footer — which versions

A footer states the generation timestamp and the annotation/reference **module versions** behind the
data — the pipeline layer (VEP, ClinVar, gnomAD, dbNSFP, SpliceAI, GenCC, PanelApp) and the
reference layer (genome assembly + release, Monarch). Pipeline versions are captured automatically
at import (declared in the import manifest) and can be recorded or overridden by an admin. The
footer prints with the report — it is part of the signed artifact.

### 2 · Evidence-drift banner — has anything changed

Every ACMG save **freezes the evidence it was based on** (the annotation identity + the ClinVar
significance at that moment). When you open the report, each classification is compared against the
current annotation; if the backing evidence has changed, an amber banner flags it — e.g. *“ClinVar
Uncertain significance → Pathogenic”*. Re-review a flagged variant before sign-out. Classifications
made before this feature existed have no frozen evidence and are simply not checked.

### 3 · Classification audit trail — who did what, when

An immutable **“Classification audit trail”** lists every clinical action on the family's variants —
who classified, tagged or annotated which variant, when, and what changed (before → after),
including each sign-out. The events are written in the same transaction as the change and the
underlying table is **append-only at the database level**, so the record can never be quietly
altered. (This is the clinical *action* log; the admin [audit log](#administration) records HTTP
*access* separately.)

### 4 · Case sign-out — freeze the result

**Sign out report** freezes the manifest, the reported small variants (each classification with its
frozen evidence snapshot), the reported structural variants and CNVs with their classification, and
the drift state into a **versioned, SHA-256 content-hashed** snapshot, stored append-only — a signed
record can never change.

**Only authorised signatories sign out.** CoGA lets any member of the project sign out a report and
does not check signing authority itself; the laboratory decides who may sign, and CoGA records who
signed each version in the signed record and the audit trail.

The report page itself always shows the *current* data, and checks it against the latest signed
version. The record is green — *“✓ Signed out — version 2 by … · This page matches signed version
2”* — only while the two match. If anything changed after sign-out (a review, a report tag, a
re-import, a QC cut-off), the record turns amber and names what changed: the page is then **not**
the signed report, and it prints with a notice saying so. Sign out again to issue a new version, or
download the frozen signed version from the record.

> **The drift gate.** If any reported classification has drifted, sign-out is blocked until you
> re-review or **acknowledge** the drift with a reason — the acknowledgement and its reason are
> recorded in the snapshot and the audit trail, as for a Sample-QC override. Signing out again
> creates a new version (the button reads *Amend sign-out*); earlier versions are never overwritten.

[Report traceability \& sign-out reference (footer, drift, audit, sign-out)](/docs/reference/clinical-traceability "further-reading")
