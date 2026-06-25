# Clinical report traceability & sign-out — reference

The **clinical report** (per family, *Report* link in the family workspace) is also
the **provenance and sign-out surface** for the case. It locks a reported result to
exactly what produced it — the annotation/reference versions, the variant list, each
classification and its evidence — and lets you freeze that into an immutable,
content-hashed sign-out record.

This is the in-depth reference. For the workflow-level overview see the
[in-app user guide](/docs) section *Clinical report, traceability & sign-out*.

---

## Why traceability matters

A reported result is only defensible if you can answer, later and exactly: *which
data, which versions, and whose decisions produced this?* Annotation sources move —
a new ClinVar release, a new gnomAD version — and a classification made last month
may rest on evidence that has since changed. CoGA makes all of that explicit and
permanent, so a signed-out report can be reproduced and audited.

The report carries four things, top to bottom.

## 1. Provenance footer — *which versions*

A footer at the end of the report states the **generation timestamp** and the full
list of annotation/reference **modules and versions** that backed the data:

- the **pipeline** layer — the upstream tools that produced the family's annotated
  input: VEP, ClinVar, gnomAD, dbNSFP, SpliceAI, GenCC, PanelApp;
- the **reference** layer — what CoGA itself loaded: the genome assembly (with its
  release date) and the Monarch release.

The pipeline versions are captured automatically when the family's data is imported
(declared in the import manifest), and can be recorded or overridden by an admin.
Where a version is unknown it is simply omitted, never guessed.

> **Reading it.** The footer prints with the report — it is part of the signed
> artifact. `Reference assembly GRCh38 (2013-12-01) · VEP 110 · ClinVar 2026-05 · …`

## 2. Evidence-drift banner — *has anything changed since I classified*

Every time you save an ACMG classification, CoGA **freezes the evidence it was based
on**: the annotation-set identity (a hash that changes whenever any annotation
changes) plus the ClinVar significance at that moment.

When you open the report, each classification is compared against the **current**
annotation. If the backing evidence has changed, an amber banner lists the affected
variants:

> ⚠ **1 classification has evidence changes since being made.** `1-100-A-G` — ClinVar
> Uncertain significance → Pathogenic *(classified by alice)*

This is the guardrail against stale interpretations silently persisting. Re-review a
flagged variant before sign-out. A classification made *before* this feature existed
has no frozen evidence and is simply not checked (it can't drift retroactively).

> **No false alarms.** Drift is only declared when both the old and new annotation
> hashes are known and differ — a missing hash on either side reads as "unknown",
> not "changed".

## 3. Classification audit trail — *who did what, when*

An immutable **"Classification audit trail"** section lists, most-recent first, every
clinical action on the family's variants: who **classified**, **tagged** or
**annotated** which variant, when, and what changed (before → after).

- *Classification VUS (class 3) → Likely pathogenic (class 4)*
- *Tags added report*
- *Note added*
- *Report signed out (v2) — 3 reported variant(s)*

These events are written in the **same transaction** as the change itself, so the
trail can never drift from the data, and the underlying table is **append-only at the
database level** — `UPDATE` and `DELETE` are rejected outright. (This is the clinical
*action* log; the admin *access* log under **Admin → Audit logs** is separate.)

## 4. Case sign-out — *freeze the result*

**Sign out report** freezes the reported result into a **versioned, content-hashed
snapshot**:

- the annotation/reference **manifest** (the footer versions),
- the **reported variant list** (every variant tagged `report`) with each
  classification, its ACMG criteria, tags, note, and its frozen **evidence snapshot**,
- the **drift state** at the moment of sign-out.

The snapshot is hashed with **SHA-256** over a canonical encoding, so any later
tampering is detectable, and stored **append-only** — a signed-out report can never
change. A green record appears on the report:

> ✓ **Signed out — version 2 by bjorn on 2026-06-25 10:00 UTC** · Content hash
> `a1b2c3…`

**The drift gate.** If any reported classification has drifted, sign-out is **blocked**
and you are asked to re-review or explicitly **acknowledge** the drift. Acknowledging
is recorded in both the snapshot and the audit trail, so "signed out over known drift"
is itself part of the permanent record.

**Amendments.** Signing out again creates a **new version** (v2, v3, …) — the previous
versions are never overwritten. The button reads *Amend sign-out* once a case has been
signed out.

---

## Where each piece lives

| Surface | Endpoint |
| --- | --- |
| Provenance footer | `GET /families/{id}/annotation-manifest` |
| Evidence drift | `GET /families/{id}/classification-drift` |
| Audit trail | `GET /families/{id}/clinical-audit` |
| Sign-out | `POST /families/{id}/report/sign-out` · `GET …/report/sign-outs[/{version}]` |

The design record (schema, immutability triggers, phasing) is in
`docs/clinical-traceability.md`.
