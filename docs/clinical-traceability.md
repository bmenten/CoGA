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
| Evidence snapshot | `small_variant_reviews.acmg_evidence_snapshot`, `structural_variant_reviews.cnv_evidence_snapshot` | for each ACMG classification of a small variant, and each CNV (ClinGen) classification of a structural variant or CNV, what the classifier saw |
| Clinical audit trail | `clinical_audit_events` | who changed the classification, tags or note of a small variant, structural variant or CNV, replaced the annotation manifest, changed the NIPT artifact list or signed out, when, with before and after |
| Signed reports | `report_signouts` | each sign-out as a frozen, versioned, content-hashed snapshot |
| HTTP audit log | `audit_log_events` | every API request, with the user and a masked body |

**The manifest** is filled from three sources: the VCF headers read at import
(`vcf_header`, see [annotation-provenance.md](annotation-provenance.md)), the pipeline's own
record in a package (`manifest`), and a replacement by an admin through
`PUT /families/{family_id}/annotation-manifest`. A replacement is always recorded as `manual`,
and later imports leave a manual manifest alone. An import and a replacement of the same
family take turns on one lock, so neither overwrites the other halfway.
`GET /families/{family_id}/annotation-manifest` merges it with what CoGA itself
loaded: the reference assembly, the source of the gene loci (`gene_loci`: GENCODE, or the
UCSC table used when GENCODE could not be fetched), the Monarch release and the HPO release
(how each is read: [annotation-provenance.md](annotation-provenance.md#the-reference-modules)).
The report footer shows the merged list, and sign-out freezes it.

**The evidence snapshot** is taken on every ACMG save. It holds the variant's annotation
version, its annotation-set hash and its ClinVar significance, with the time. The hash
changes whenever any annotation of the variant changes, so it is the drift key.

A structural variant or CNV has no annotation-set hash in ClickHouse, so its snapshot
(`services/structural_variant_evidence.py`, taken on every save of a CNV scoring) holds the
values the CNV classifier reads instead: the SV's type, the genes it overlaps and their count,
the pLI and the annotated inheritance. The classifier reads no clinical-CNV, dosage-score or
DGV data; the analyst scores those criteria by hand. The snapshot also holds the event (its
caller, chromosome, start, end, length and breakend partner), a hash of its whole annotation
record, the time, and the versions the evidence comes from: those the family's manifest records
for its SV callset (`by_modality.sv`, else `pipeline`) and the reference assembly and gene loci.
The drift key is `evidence_hash`, SHA-256 over the evidence; the versions are recorded, not
compared. It does not hold the genotype calls. A scoring for an SV that is not in the family's
data is refused with 404; one saved for a family without an assembly (no SV storage to read) is
stored without a snapshot.

**The clinical audit trail** is written in the same transaction as the change it describes,
so it cannot drift from the data. Small-variant, structural-variant and CNV review saves write
to it, and so does sign-out. An admin's replacement of the annotation manifest writes to it too, and so does every change to the NIPT artifact list, on a chain of its own (`system:nipt-artifacts`, not a family). Its actions are `classification`, `tags`, `note`, `annotation_manifest` (the replacement, with the manifest it replaced and the new one), `sign_out`, and `nipt_artifact_added`, `nipt_artifact_updated`, `nipt_artifact_removed` and `nipt_artifacts_auto_seeded`.
For a small variant, `classification` holds the ACMG class, the accepted criteria, the point
total, the VUS tier and every stored criterion: its strength, whether it was accepted, its
evidence text and whether it was an automatic suggestion. For a structural variant or CNV, it
holds the reviewer's classification and the CNV (ClinGen) scoring: the class, the kind, the
point total, the accepted criteria with their points, and every stored criterion in the same
detail. Any change to that record writes an event, even when the class stays the same, and its
summary names what changed (*ACMG criteria updated (…): PM2 moderate → supporting*). An
unchanged re-save writes none.
Points are recorded as the database reads them back, so a total of -0.0 is stored as 0 and the
chain still verifies. These events carry `metadata.modality = "sv"`, so an SV id is never read
as a small variant's. A save that clears or deletes a review is recorded like any other
change. `GET /families/{family_id}/clinical-audit` returns a
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

The structural variants and CNVs are under `structural`: each CNV scoring's snapshot compared
with the SV as the SV page reads it now. `drifted` names what moved in `changed` (`gene_symbols`,
`pli`, `inheritance`, `sv_type`, `locus`, `source`, or `annotations` for any other annotation),
with the evidence from → to. Only the fields a snapshot froze are compared. Frozen evidence that
cannot be read is `unknown`, never `current`.

At sign-out, a reported classification that has no snapshot counts as drift too
(`no_snapshot`), whether it is a small variant's or a structural variant's.

## Sign-out

`POST /families/{family_id}/report/sign-out` freezes:

- the merged manifest and the reference assembly;
- the software version and git commit that produced the snapshot;
- the small variants tagged `report`, each with its classification, ACMG criteria and
  evidence snapshot, and the reported structural variants and CNVs with their classification,
  CNV criteria and evidence snapshot;
- the drift state (`drift` for the small variants, `structural_drift` for the structural
  variants and CNVs), the sample-integrity QC result, and the sequencing QC with the cut-offs it
  was judged against;
- the import state (`import_incomplete`): null when the family's data imported completely,
  otherwise the datasets that failed and those that imported, when, and the import job;
- the signer, the time, and any acknowledgement with its reason.

The snapshot is hashed with SHA-256 over a canonical encoding and stored as the next version.
An amendment is a new sign-out; no version is ever changed. The sign-out is also written to the
clinical audit trail. A stored hash is always checked against the snapshot as it was stored, so
a version signed before a field was added still verifies; the sign-out check reports that
section as `not_compared`.

Five gates run first, in this order:

1. **Assembly scope.** A family whose assembly is not in `VALIDATED_ASSEMBLIES` (default
   GRCh38), or that has none, is refused with 409 (`gate: "assembly_scope"`). This cannot be
   acknowledged. The family and report pages say such a family is not validated for clinical
   use, and the report page offers no sign-out.
2. **Data being written.** A package import writes a family over minutes and commits as it
   goes, and flags it `import_incomplete` only once it has failed, so a snapshot read while it
   runs can hold a half-imported family. A family with a package-import job (not a dry run)
   that is `queued` (naming the family in its request), `validating` or `running` is refused
   with 409 (`gate: "import_in_progress"`, naming the job), and so is a family whose
   variant-write locks a writer holds (`gate: "variant_writes_in_progress"`: an import, an
   upload, an admin delete). Neither can be acknowledged; the sign-out does not wait. Past
   this gate the sign-out shares the family's variant-write locks
   (`pg_try_advisory_xact_lock_shared`) until it commits, so no write of the family's variants
   starts while it reads, and it checks the import jobs once more after the snapshot: an
   import claimed meanwhile commits its job as `running` before it writes, and refuses the
   sign-out. A job whose worker stopped keeps refusing until a worker claims it again.
3. **Evidence drift.** Any drifted, unknown, missing or unsnapshotted classification, of a small
   variant or of a structural variant or CNV, gives 409, unless the request sets
   `acknowledge_drift` with a `drift_acknowledgement_reason` (422 without a reason). One
   acknowledgement covers both; the message says how many are structural variants or CNVs.
4. **Sample-integrity QC.** A QC fail (a detected sample or pedigree swap), or a swap check that
   could not run for a relationship the pedigree asserts, gives 409 (`gate: "sample_qc"`),
   unless the request sets `acknowledge_qc` with a `qc_acknowledgement_reason` (422 without).
5. **Incomplete import.** A family flagged `metadata.import_incomplete` by a package import
   that left it partly loaded ([data-import.md](data-import.md)) gives 409
   (`gate: "import_incomplete"`, naming the failed datasets and the import job), unless the
   request sets `acknowledge_import_incomplete` with an
   `import_incomplete_acknowledgement_reason` (422 without). Any set flag counts, whatever its
   shape. Every family page shows *Import incomplete* while the flag is set.

The acknowledgements and their reasons are part of the hashed snapshot and the audit event.

A lookup that fails while the snapshot is built is frozen as an explicit marker, never as an
empty value: missing QC cut-offs as `sequencing_qc.unavailable`, and a failed lookup of a
reference module, or an HPO ontology that recorded no release, as module version `unavailable`.
The audit event lists these as `not_captured`, and the report page names them.

The other sign-out endpoints:

| Endpoint | Returns |
| --- | --- |
| `GET /families/{family_id}/report/sign-outs` | the list of signed versions |
| `GET /families/{family_id}/report/sign-outs/{version}` | one signed version, with `verified` (its content hash recomputed on read) and `not_captured` (what its record could not capture) |
| `GET /families/{family_id}/report/sign-out-check` | whether the report as it would be signed now matches the latest version: `matches`, `changed_sections`, `not_compared`, `not_captured` |

## The report page and the signed record

`/families/{family_id}/report` has two views. A family that has been signed out opens on its
latest signed version; one that never was opens on the live report.

**A signed version** (`?version=N`) is rendered from the snapshot that
`GET …/report/sign-outs/{version}` returns, and from nothing else
(`frontend/src/pages/families/signedReportRecord.ts` reads it). It shows the version, signer,
time, content hash, `verified` and the frozen build; each reported variant's classification,
accepted criteria, evidence snapshot, tags and note; the reported SVs with their ClinGen CNV
scoring; the drift, Sample QC, sequencing QC and import state, with the acknowledgements; the
frozen modules; and `not_captured`. A section the snapshot lacks is shown as not in the record,
never as empty. What no snapshot holds is said on the page: the
variant description (gene, HGVS, consequence, genotypes, frequencies, predictions), the
segregation, the gene and phenotype context, the audit trail and the pipeline settings. Print
prints this view. A printout starts with a notice when the record fails its content hash, when
a later version supersedes it, or when the list of versions could not be loaded. On the latest
version the page also runs `sign-out-check` and says, on screen only, whether the family's data
has changed since.

**The live report** (`?view=live`) draws live data. It is where a case is signed out, and it is
never presented as the signed record. It checks itself against the latest sign-out
(`sign-out-check`):

- the content matches the signed version: it says so, and is still not the signed report;
- the content changed after sign-out: the changed sections are named;
- the check could not be made: treat the page as unsigned.

Every printout of the live report starts with a notice ("Draft …", "Not the signed report …",
"Not verified …", or that the sign-out record is loading or could not be loaded). While the
sign-out record is loading or could not be loaded, sign-out is not offered. After a sign-out the
page shows the new version.

Each snapshot also records the reference modules its build looked up (`reference_modules`). A
reference module CoGA adds later is not in that list: for a record signed before it, the check
lists `modules.<key>` under `not_compared` instead of calling the record changed, and
`not_captured` names the missing version. A module in the list but not held was not loaded at
sign-out, so one loaded since is a change. A snapshot without the list names no module, so every
reference module it does not hold is listed as missing.

A signed version lists the SV/CNV classifications whose evidence had moved at sign-out with the
small variants', under *Evidence drift at sign-out*, and says when its record holds no SV/CNV drift
check.

The release candidate's snapshot format is the first one CoGA reads: records signed by earlier
development builds get no reading of their own (#681). They still verify, since verification
re-hashes the record as stored.

Both views download the frozen record as JSON.

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

- The snapshot holds no variant description (gene, HGVS, consequence, genotypes, frequencies,
  predictions) and no gene or phenotype context. A signed version therefore names each variant
  by its ID and says what its record lacks; only the live report shows the rest, from current
  data. There is no byte-stable PDF: a signed version is printed from the browser, laid out by
  the build that renders it (named in its footer).
- The review of a compound-heterozygous pair (its classification, tags and note) writes no
  clinical audit event. Nor do pedigree, member and HPO edits: they appear in the HTTP audit log,
  and pedigree and member edits also as structure versions (`family_structure_versions`, which is
  neither append-only nor chained).
- Anchors are made by hand: nothing in the code, CI or Terraform calls
  `POST /admin/integrity/anchor` on a schedule.
- The export of each anchor to a store outside the database is not implemented (the
  `export_anchor` hook does nothing), so deleting the latest anchors is not detected.
- Verification trusts only the configured signing key. After a key rotation, anchors signed with
  the old key report `unknown_key`; keep the old public key for audits.
- `coga_app` keeps INSERT on the chained tables, so it could append a forged but
  self-consistent row; it cannot rewrite or delete existing ones.
- The manifest's platform layer covers the assembly, the gene loci, Monarch and HPO. The
  releases of the gene reference, PanelApp and the clinical CNVs are not in it. Each
  reference import records the release its source states in `reference_dataset_imports`,
  but the manifest does not read it; the clinical-CNV knowledgebase states none.
- Any user who can open the family can sign out; sign-out is not limited to a role.
- Sign-out exists for the family report only; the monogenic NIPT report page has none.
