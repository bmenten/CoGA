# Clinical traceability, sign-out and audit

A signed report must be reproducible and defensible: it has to say exactly what produced it,
and show who decided what and when. This page describes what CoGA records for that, how
sign-out works, how tampering is detected, and what is not covered. It is written for
developers and auditors. Lab users find the same in plain words in the in-app reference
([clinical-traceability](../frontend/src/content/docs/clinical-traceability.md), shown at
`/docs/reference/clinical-traceability`).

Code comments refer to four parts by number: Phase 0 (the annotation manifest), Phase 1
(evidence snapshots and drift), Phase 2 (the clinical audit trail) and Phase 3 (sign-out).

## What is recorded

| Record | Where | What it holds |
| --- | --- | --- |
| Annotation manifest | `family_annotation_manifest` | per family, the versions of the tools and databases that produced its annotated input (VEP, ClinVar, gnomAD, dbNSFP, SpliceAI, callers, pipeline) |
| Evidence snapshot | `small_variant_reviews.acmg_evidence_snapshot` | for each ACMG classification of a small variant, what the classifier saw |
| Clinical audit trail | `clinical_audit_events` | who changed a classification, tags or note, or signed out, when, with before and after |
| Signed reports | `report_signouts` | each sign-out as a frozen, versioned, content-hashed snapshot |
| HTTP audit log | `audit_log_events` | every API request, with the user and a masked body |

**The manifest** is filled from three sources: the VCF headers read at import
(`vcf_header`, see [annotation-provenance.md](annotation-provenance.md)), the pipeline's own
record in a package (`manifest`), and an edit through
`PUT /families/{family_id}/annotation-manifest` (`manual`; later imports then leave the
manifest alone). `GET /families/{family_id}/annotation-manifest` merges it with what CoGA itself
loaded: the reference assembly, the source of the gene loci (`gene_loci`: GENCODE, or the
UCSC table used when GENCODE could not be fetched) and the Monarch release. The report footer
shows the merged list, and sign-out freezes it.

**The evidence snapshot** is taken on every ACMG save. It holds the variant's annotation
version, its annotation-set hash and its ClinVar significance, with the time. The hash
changes whenever any annotation of the variant changes, so it is the drift key.

**The clinical audit trail** is written in the same transaction as the change it describes,
so it cannot drift from the data. Its actions are `classification` (with the ACMG class and
criteria), `tags`, `note` and `sign_out`. `GET /families/{family_id}/clinical-audit` returns a
family's trail, and the report shows it as the classification audit trail. Two people saving
the same review cannot silently overwrite each other: a save that carries the
`expected_updated_at` it loaded is refused with 409 `review_conflict` when the review changed
in between.

## Evidence drift

`GET /families/{family_id}/classification-drift` compares each classification's snapshot with
the variant's current annotation:

- `current`: the annotation-set hash is unchanged;
- `drifted`: the hash changed (a new annotation release, ClinVar and version shown from → to);
- `unknown`: a hash is missing, so the match cannot be verified;
- `variant_missing`: the variant is no longer in the data.

At sign-out, a reported classification that has no snapshot counts as drift too
(`no_snapshot`).

## Sign-out

`POST /families/{family_id}/report/sign-out` freezes:

- the merged manifest and the reference assembly;
- the software version and git commit that produced the snapshot;
- the small variants tagged `report`, each with its classification, ACMG criteria and
  evidence snapshot, and the reported structural variants and CNVs with their classification;
- the drift state, the sample-integrity QC result, and the sequencing QC with the cut-offs it
  was judged against;
- the signer, the time, and any acknowledgement with its reason.

The snapshot is hashed with SHA-256 over a canonical encoding and stored as the next version.
An amendment is a new sign-out; no version is ever changed. The sign-out is also written to the
clinical audit trail.

Three gates run first, in this order:

1. **Assembly scope.** A family whose assembly is not in `VALIDATED_ASSEMBLIES` (default
   GRCh38), or that has none, is refused with 409 (`gate: "assembly_scope"`). This cannot be
   acknowledged. The family and report pages say such a family is not validated for clinical
   use, and the report page offers no sign-out.
2. **Evidence drift.** Any drifted, unknown, missing or unsnapshotted classification gives 409,
   unless the request sets `acknowledge_drift` with a `drift_acknowledgement_reason` (422
   without a reason).
3. **Sample-integrity QC.** A QC fail (a detected sample or pedigree swap), or a swap check that
   could not run for a relationship the pedigree asserts, gives 409 (`gate: "sample_qc"`),
   unless the request sets `acknowledge_qc` with a `qc_acknowledgement_reason` (422 without).

The acknowledgements and their reasons are part of the hashed snapshot and the audit event.

A lookup that fails while the snapshot is built is frozen as an explicit marker, never as an
empty value: missing QC cut-offs as `sequencing_qc.unavailable`, a failed assembly or Monarch
lookup as module version `unavailable`. The audit event lists these as `not_captured`, and the
report page names them.

The other sign-out endpoints:

| Endpoint | Returns |
| --- | --- |
| `GET /families/{family_id}/report/sign-outs` | the list of signed versions |
| `GET /families/{family_id}/report/sign-outs/{version}` | one signed version, with `verified`: its content hash recomputed on read |
| `GET /families/{family_id}/report/sign-out-check` | whether the report as it would be signed now matches the latest version: `matches`, `changed_sections`, `not_compared`, `not_captured` |

## The report page and the signed record

The report page draws live data, not the frozen snapshot. It checks itself against the latest
sign-out (`sign-out-check`):

- green: the content matches the signed version;
- amber: the content changed after sign-out, and the changed sections are named;
- grey: the check could not be made, so treat the page as unsigned.

A page that is not the verified signed record prints with a notice at the top ("Draft …",
"Not the signed report …" or "Not verified …"). The frozen record itself can be downloaded as
JSON from the report page.

## Tamper evidence and its limits

- **Append-only tables.** Database triggers refuse UPDATE and DELETE on `audit_log_events`,
  `clinical_audit_events`, `report_signouts`, `integrity_anchors` and `qc_threshold_changes`
  (see [database.md](database.md)). The restricted runtime role `coga_app` also lacks those
  rights, but only once the deployment has switched to it
  ([db-runtime-role-runbook.md](db-runtime-role-runbook.md)). Until then the application
  connects as the table owner, who can disable a trigger.
- **Hash chains.** Each row of `clinical_audit_events` and `report_signouts` carries a
  `row_hash` over its content and the previous row's hash, per family. A deleted, reordered or
  edited row breaks the chain. `GET /admin/integrity/verify?table=…&family_id=…` walks one
  family's chain and names the first bad row; for sign-outs it also recomputes each content
  hash.
- **Signed anchors.** `POST /admin/integrity/anchor` records the head of every chain and signs
  that snapshot with the Ed25519 key in `INTEGRITY_ANCHOR_SIGNING_KEY`, which the database does
  not hold. Outside development the backend refuses to start without the key and never writes
  an unsigned anchor. `GET /admin/integrity/anchor/verify` checks the live chains against the
  latest anchor; `GET /admin/integrity/anchor/verify-chain` checks the anchors themselves. An
  owner who rebuilds or shortens a chain after an anchor was made is detected by that check.

This is tamper-evident against someone who has only the database, between retained anchors.
It is not tamper-proof: someone who holds the signing key can forge anchors, and the most recent
anchors can be deleted without trace unless a copy is kept outside the database.

## Known limitations

- The report page renders live data. The snapshot does not hold everything the page draws (gene
  profiles, HGVS, frequencies), so the page cannot be rebuilt from it; the sign-out check above
  guards the difference. There is no byte-stable PDF; the report is printed from the browser.
- Structural-variant and CNV review saves write no clinical audit events, and their
  classifications have no evidence snapshot, so they are not drift-checked. Pedigree, member
  and HPO edits appear only in the HTTP audit log.
- Anchors are made by hand: nothing in the code, CI or Terraform calls
  `POST /admin/integrity/anchor` on a schedule.
- The export of each anchor to a store outside the database is not implemented (the
  `export_anchor` hook does nothing), so deleting the latest anchors is not detected.
- Verification trusts only the configured signing key. After a key rotation, anchors signed with
  the old key report `unknown_key`; keep the old public key for audits.
- `coga_app` keeps INSERT on the chained tables, so it could append a forged but
  self-consistent row; it cannot rewrite or delete existing ones.
- The manifest's platform layer covers only the assembly, the gene loci and Monarch. The
  releases of HPO, the gene reference, PanelApp and the clinical CNVs are not in it, and
  `reference_dataset_imports.source_version` and `source_release_date` are never filled.
- Any user who can open the family can sign out; sign-out is not limited to a role. The same
  users can replace the family's annotation manifest (`PUT …/annotation-manifest`), which is
  then frozen into later sign-outs; that change appears only in the HTTP audit log.
- Sign-out exists for the family report only; the monogenic NIPT report page has none.
