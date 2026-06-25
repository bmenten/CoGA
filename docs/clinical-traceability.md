# Clinical-grade traceability, sign-out & audit

**Status: ✅ Implemented** (all four phases shipped). This document began as the design
plan and is retained as the design record; the **"Phased delivery"** section below maps
each phase to the PR and the code that shipped it. The end-user description lives in the
[in-app reference doc](../frontend/src/content/docs/clinical-traceability.md) and the
**User Guide → "Clinical report, traceability & sign-out"** section.

**Goal:** make a reported result *reproducible* and *medico-legally defensible* by locking
it to exactly what produced it — the annotation/reference module versions, the filter
settings, the variant list, and the classifier inputs — backed by an immutable record of
who classified/tagged/edited what and when, and a frozen, versioned case sign-out.

| Phase | Ships | PR |
| --- | --- | --- |
| 0 | Annotation/reference version **manifest** + report **footer** | #220 |
| 1 | Per-classification **evidence snapshot** + **drift** surfacing | #221 |
| 2 | Immutable **clinical audit trail** | #222 |
| 3 | Case **sign-out** + frozen, versioned, content-hashed snapshot | #223 |

This document maps what existed before, the gaps it closed, and how it was delivered. It is
grounded in the current code (file references inline).

---

## 1. Clinical requirements (what "done" means)

1. **Provenance of the data.** A report states which modules/versions produced its inputs:
   reference assembly, VEP, ClinVar, gnomAD, dbNSFP, SpliceAI, GenCC, PanelApp, Monarch, HPO
   — with a clear **timestamp** in a report **footer**.
2. **Provenance of the interpretation.** Each classification records the *evidence it was
   based on* (ClinVar significance, gnomAD AF, in-silico scores, gene constraint, annotation
   version), frozen at classification time.
3. **Drift surfacing.** When the underlying evidence changes after a classification (a new
   ClinVar release, a new annotation version), the platform flags *"evidence has changed since
   you classified this"* so stale interpretations don't silently persist.
4. **Immutable audit trail.** An append-only log of who classified/tagged/edited/signed what,
   and when, queryable by case and variant.
5. **Case sign-out.** A frozen, versioned snapshot of the report at sign-out — the variant
   list, the filter settings that produced it, the classifications + their evidence snapshots,
   the pedigree version, and the module-version manifest — content-hashed and tamper-evident.
   Amendments create a new version; the signed snapshot is never mutated.

---

## 2. Current state (what already exists)

| Capability | Status | Where |
| --- | --- | --- |
| Append-only HTTP audit log | ✅ | `audit_log_events` (`004_audit_logs.sql`); immutability trigger (`029_audit_log_immutable.sql`); writer `services/audit_log_pg.py`; middleware `middleware/request_logging.py`; admin UI `AdminAuditLogsPage.tsx` |
| Per-variant annotation version + set hash | ✅ (label only) | ClickHouse `annotation_version` (default `"current"`) + `annotationSetHash` — `clickhouse_variant_storage.py` |
| Reference import bookkeeping | ◑ partial | `reference_dataset_imports` (dataset, `performed_at/by`, `source`); `assemblies` (`version`, `release_date`); `monarch_gene_disease.release_version` (the **only** explicit source version); `raw_import_files` (input file name/path/sha256/source/metadata); `gene_info_refresh_jobs` (sync history, no source version) |
| Review state | ✅ (decision only) | `small_variant_reviews` / `structural_variant_reviews`: `acmg` JSONB {criteria, point_total, classification, vus_tier}, `acmg_class`, `tags`, `tag_metadata` {per-tag who/when}, `note`, `updated_by/at`, `created_at`. Score recomputed server-side (`acmg_points.py`, `small_variant_review_pg.py`) |
| Family workflow status | ✅ (mutable) | `family_statuses` + `families.status_id/assigned_to_id/reviewed_by_id` (`024_family_metadata.sql`) |
| Versioned snapshot pattern | ✅ (reusable) | `family_structure_versions` (id, family_id, **version**, **structure_hash**, **snapshot JSONB**, metadata, created_by, created_at, UNIQUE(family_id, version)) — `ped_service.py`. **This is the exact pattern to reuse for report snapshots.** |
| Report generation | ✅ (live) | `FamilyReportPage.tsx` / `FamilyNiptReportPage.tsx` fetch variants tagged `report`, build prose via `reportNarrative.ts`, browser-print. **No frozen snapshot, no footer, no sign-out.** |

---

## 3. The gaps

1. **Upstream annotation versions are lost at import.** VEP / ClinVar / gnomAD / dbNSFP /
   SpliceAI versions live in the source VCF header but are never extracted or stored. There is
   no per-family annotation manifest.
2. **Reference versions are partially tracked.** Only Monarch records a release; gnomAD/ClinVar
   (as loaded into the platform), GenCC, PanelApp, HPO, dbNSFP-gene, ClinGen have no consistent
   version/release column.
3. **Classifications store the human decision, not the evidence.** The `acmg` blob has the
   criteria and score but *not* the ClinVar/gnomAD/in-silico/annotation-version inputs the
   classifier saw — these are recomputed live, so they silently change under the classification.
4. **No drift detection.** Nothing compares a classification's basis against current evidence.
5. **Audit is HTTP-only.** Events are inferred from method+path+body; there is no semantic
   "classification PVS1 added", no field-level before/after, no fast query by variant/family,
   and no "report signed out" event.
6. **No sign-out / immutability.** Reports are regenerated live and fully mutable; there is no
   frozen record, no content hash, no footer, no lock.

---

## 4. Target architecture — five building blocks

```
            ┌────────────────────────────────────────────────────────────┐
  import →  │ (A) Annotation/reference VERSION MANIFEST  (per family +    │
            │     platform reference layer)                              │
            └───────────────┬────────────────────────────────────────────┘
                            │ stamped into
  classify →  ┌─────────────▼───────────────┐   compare    ┌──────────────┐
              │ (B) EVIDENCE SNAPSHOT on each│─────────────▶│ (C) DRIFT    │
              │     classification (frozen)  │  vs current  │  surfacing   │
              └─────────────┬───────────────┘              └──────────────┘
                            │ included in
  sign-out →  ┌─────────────▼───────────────────────────────┐
              │ (D) FROZEN REPORT SNAPSHOT (versioned,       │
              │     content-hashed, append-only) + footer    │
              └─────────────┬───────────────────────────────┘
                            │ every step writes to
              ┌─────────────▼───────────────────────────────┐
              │ (E) CLINICAL AUDIT TRAIL (append-only,       │
              │     keyed by family + variant, with deltas)  │
              └──────────────────────────────────────────────┘
```

### (A) Annotation / reference version manifest

Two layers, merged by one service:

- **Platform reference layer** — versions of what CoGA loaded (assembly, Monarch, HPO,
  gene reference [ClinGen/GenCC/ClinVar-gene-condition/dbNSFP-gene], PanelApp, clinical CNVs).
  Mostly already in Postgres; needs a consistent `source_version` / `source_release_date`.
- **Per-family pipeline layer** — versions of the upstream tools that produced *this family's*
  annotated VCF (VEP+cache, ClinVar, gnomAD, dbNSFP, SpliceAI). Captured at import.

```sql
-- New: per-family upstream annotation provenance.
CREATE TABLE family_annotation_manifest (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    family_id     UUID NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    assembly_id   UUID REFERENCES assemblies(id) ON DELETE SET NULL,
    modules       JSONB NOT NULL DEFAULT '{}'::jsonb,  -- {vep:{version,cache,assembly},
                                                       --  clinvar:{release}, gnomad:{version},
                                                       --  dbnsfp:{version}, spliceai:{model}, …}
    source        TEXT NOT NULL DEFAULT 'manifest',    -- 'manifest' | 'vcf_header' | 'manual'
    recorded_by   TEXT,
    recorded_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (family_id)
);

-- Extend reference import bookkeeping with source release.
ALTER TABLE reference_dataset_imports
    ADD COLUMN source_version      TEXT,
    ADD COLUMN source_release_date DATE,
    ADD COLUMN source_url          TEXT;
```

- **Capture at import:** extend `PackageManifest` (`family_package_import.py`) with an
  `annotation_manifest` field that the preprocessing pipeline populates; best-effort parse the
  VCF `##VEP=` / `##source` / INFO descriptions as a fallback; allow admin override.
- **Service:** `get_annotation_version_manifest(family) -> ManifestOut` merges the per-family
  manifest with the current platform reference versions (read from `reference_dataset_imports`,
  `monarch_gene_disease`, `assemblies`, HPO/gene/panel sync records). Used by the footer and
  frozen into the sign-out snapshot.

> **Decision needed:** the authoritative source of the upstream versions. Recommended:
> the preprocessing pipeline declares them in the import manifest (deterministic), with
> VCF-header parsing as a fallback and an admin override. This is a small **process** change
> upstream of CoGA.

### (B) Evidence snapshot per classification

Freeze what the classifier saw. The save path already fetches the ClickHouse record
(`get_small_variant_family_record`) and the frontend ACMG modal already reads the evidence —
so the inputs are in hand at save time.

```sql
ALTER TABLE small_variant_reviews     ADD COLUMN acmg_evidence_snapshot JSONB;
ALTER TABLE structural_variant_reviews ADD COLUMN cnv_evidence_snapshot  JSONB;
```

```jsonc
// acmg_evidence_snapshot
{
  "annotation_version": "current",
  "annotation_set_hash": "1734...8821",        // the cheap drift key (ClickHouse annotationSetHash)
  "module_versions": { /* manifest stamp at classification time */ },
  "evidence": {
    "clinvar": "Pathogenic",
    "gnomad_af_popmax": 0.00012, "gnomad_hom": 0,
    "revel": 0.93, "cadd_phred": 28.1, "spliceai_max": 0.02,
    "gene_constraint": { "pli": 0.99, "mis_z": 3.4 }
  },
  "captured_at": "2026-06-25T14:30:00Z"
}
```

### (C) Drift surfacing

- **Cheap primary signal:** the stored `annotation_set_hash` vs the variant's *current*
  `annotationSetHash` in ClickHouse — a single mismatch means "annotations changed".
- **Human-readable diff:** compare the snapshot's stored `evidence` fields against current to
  produce e.g. *"ClinVar: VUS → Pathogenic", "gnomAD AF 0.001 → 0.0005",
  "annotation v2024-06 → v2024-12"*.
- **Surfaces:** a `⚠ evidence changed` badge on the variant row / review card; a per-family and
  cohort **"stale classifications"** list (drift since classification). Service
  `evaluate_classification_drift(family)`.

### (D) Frozen report snapshot + sign-out

Reuse the `family_structure_versions` pattern; make it append-only like `audit_log_events`.

```sql
CREATE TABLE family_report_snapshots (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    family_id       UUID NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    version         INTEGER NOT NULL,
    state           TEXT NOT NULL DEFAULT 'signed_out',  -- 'signed_out' | 'amended' | 'withdrawn'
    content_hash    TEXT NOT NULL,                       -- sha256 of canonical snapshot (tamper-evident)
    snapshot        JSONB NOT NULL,                      -- frozen variants + reviews + ACMG + evidence
                                                         --   snapshots + pedigree version + ROI + narrative inputs
    filter_settings JSONB NOT NULL DEFAULT '{}'::jsonb,  -- the search/preset that produced the candidate list
    module_versions JSONB NOT NULL DEFAULT '{}'::jsonb,  -- the version manifest at sign-out (A)
    signed_out_by   UUID REFERENCES users(id) ON DELETE SET NULL,
    signed_out_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (family_id, version)
);
-- + an append-only trigger mirroring audit_log_events_block_mutation (no UPDATE/DELETE).
```

**Sign-out flow** (`POST /families/{id}/report/sign-out`):
1. Assemble the snapshot: `report`-tagged variants with full review/ACMG/evidence-snapshot, the
   current `family_structure_versions` version, the ROI, the filter settings used, the module
   manifest (A).
2. Compute `content_hash`; insert the immutable snapshot row (`version = max+1`).
3. Set family status to a locked `signed_out` status; write a clinical-audit event (E).
4. The signed snapshot is the legal record; the live review state remains the working copy.
   An **amend** produces a *new* version (never mutates the prior one).

**Report rendering:** the report page renders **from the frozen snapshot** for a signed-out
case (reproducible) and **live** for a draft. Both gain a **footer**:

> *Report generated 2026-06-25 14:30 UTC · signed out by Dr X · v2 · hash 4f9c… ·
> GRCh38 · VEP 110 (cache 110_GRCh38) · ClinVar 2026-05 · gnomAD 4.1 · dbNSFP 4.7 ·
> SpliceAI 1.3 · GenCC 2026-05 · PanelApp 2026-06 · Monarch 2026-05 · HPO 2026-04*

### (E) Clinical audit trail

A dedicated append-only table for semantic clinical actions (distinct from the HTTP audit log),
keyed for fast case/variant queries, with field-level before/after.

```sql
CREATE TABLE clinical_audit_events (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor_id    UUID REFERENCES users(id) ON DELETE SET NULL,
    actor_email TEXT, actor_role TEXT,
    family_id   UUID, variant_id TEXT,
    action      TEXT NOT NULL,            -- 'classify' | 'tag.add' | 'tag.remove' | 'note.edit'
                                          -- | 'structure.edit' | 'report.sign_out' | 'report.amend'
    summary     TEXT,
    before      JSONB, after JSONB,       -- field-level delta
    context     JSONB NOT NULL DEFAULT '{}'::jsonb
);
-- + the same append-only trigger.
```

Emit from the classify / tag / note / structure / sign-out service paths. Add a per-family
**audit timeline** UI; the sign-out snapshot embeds (or links) the relevant slice.

> **Why a second table, not the HTTP `audit_log_events`?** The HTTP log is infra-level and
> inferred; clinical defensibility needs intentional, semantic, before/after, fast-by-case
> events. Both stay append-only; they serve different questions.

---

## 5. Phased delivery — as shipped

### Phase 0 — Version manifest & report footer  *(foundation)* — ✅ #220

- `family_annotation_manifest` table + `source_version`/`source_release_date`/`source_url` on
  `reference_dataset_imports` (`030_family_annotation_manifest.sql`). Capture is free: the import
  already flows `manifest.metadata` into `family.metadata`, so a pipeline-declared
  `annotation_manifest` is read as a fallback; `PUT /families/{id}/annotation-manifest` records
  or overrides it.
- `annotation_manifest_service.py` merges the per-family **pipeline** layer with the platform
  **reference** layer (assembly + release date, Monarch release, read live); pipeline wins,
  unknown modules pass through. `GET /families/{id}/annotation-manifest`.
- **Report footer** (`FamilyReportPage.tsx`): generation timestamp + the full module/version list.
- **Tests:** `backend/tests/test_annotation_manifest.py` (merge precedence + order; metadata
  fallback) + the footer test in `FamilyReportPage.test.tsx`.

### Phase 1 — Evidence snapshot + drift surfacing — ✅ #221

- `acmg_evidence_snapshot` JSONB on `small_variant_reviews` (`031_…`). Captured on every ACMG
  save from the ClickHouse record the save path already fetches: `annotation_version` +
  `annotationSetHash` (the drift key) + ClinVar significance. `get_small_variant_family_record`
  was extended to return the annotation identity.
- `classification_drift_service.py` compares each classification's frozen snapshot against the
  current annotation → `current` / `drifted` / `variant_missing`, with ClinVar + version
  from→to (drift only when both hashes are known and differ). `GET /families/{id}/classification-drift`;
  an amber drift banner on the report.
- **Tests:** `backend/tests/test_classification_drift.py` (snapshot extraction, dash handling,
  diff states, service) + the drift-banner test.

### Phase 2 — Clinical audit trail — ✅ #222

- `clinical_audit_events` append-only table + DB-level immutability trigger (`032_…`, mirroring
  `029`). `clinical_audit_service.py` derives granular before→after events (classification with
  ACMG class + criteria, tags added/removed, note lifecycle) and writes them **in the same
  transaction** as the review save (`upsert_small_variant_review`).
  `GET /families/{id}/clinical-audit`; a "Classification audit trail" section on the report.
- **Tests:** `backend/tests/test_clinical_audit.py` (diff logic, one insert per change) + the
  audit-timeline test; immutability proven live (raw UPDATE/DELETE rejected).

### Phase 3 — Case sign-out & frozen report snapshot — ✅ #223

- `report_signouts` append-only table + immutability trigger (`033_…`). `report_signout_service.py`
  freezes the manifest + reported variant list + each classification & its evidence snapshot +
  the drift state, SHA-256 content-hashes a canonical encoding, and stores it as the next
  **version**; the sign-out is recorded in the audit trail.
- **Drift gate:** sign-out returns `409` if any classification has drifted, unless
  `acknowledge_drift` is set (baked into the snapshot + audit event).
- `POST /families/{id}/report/sign-out`, `GET .../report/sign-outs`,
  `GET .../report/sign-outs/{version}`. The report carries a green frozen sign-out record
  (version · who · when · content hash) and a "Sign out / Amend sign-out" action.
- **Tests:** `backend/tests/test_report_signout.py` (canonical hash stable + order-independent;
  drift gate 409 / acknowledged; clean sign-out) + the sign-out-record test; immutability proven
  live.

The four phases shipped as independent, CI-green PRs in order; each migration applies on startup
via the dollar-quote-aware loader and is exercised by the CI smoke job.

---

## 6. Key decisions & risks

- **Upstream version source (Phase 0).** Manifest-declared (recommended) vs VCF-header-parsed vs
  manual. Header formats vary; the manifest is deterministic but needs an upstream process change.
- **Sign-out lock semantics (Phase 3).** *Immutable snapshot + editable live state* (recommended:
  the signed record is frozen, the working copy can keep moving and shows as "amended pending")
  vs *hard-lock the case* (no edits until explicitly reopened). Affects clinical workflow.
- **Snapshot ↔ ClickHouse coupling.** The snapshot must embed the variant/annotation values it
  needs (it already will, via the evidence snapshots), so a later ClickHouse rebuild/`annotation_version`
  change can't alter a signed report. Do **not** re-query ClickHouse when rendering a signed report.
- **PDF stability.** v1 = browser print of the frozen render (good enough, reproducible from the
  snapshot). A byte-stable server-side PDF/archive is a later hardening.
- **Backfill.** Existing classifications have no evidence snapshot and existing families no
  manifest; treat as "unknown / pre-traceability" rather than blocking, and capture going forward.
- **PHI.** Snapshots and audit rows carry interpretation data; they inherit existing project
  scoping and the audit masking rules (`request_logging.py`). The clinical audit `before/after`
  must not leak identifiers beyond what the variant review already exposes.

---

## 7. Code touch-points (per phase)

- **Schema:** new `backend/db/schema/postgres/0NN_*.sql` migrations (append-only triggers reuse
  the `029` pattern). Loader is dollar-quote-aware (`core/postgres.py`).
- **Services:** `services/annotation_manifest_service.py` (new), `small_variant_review_pg.py` /
  `structural_variant_review_pg.py` (evidence snapshot + audit emit), `services/clinical_audit_pg.py`
  (new), `services/report_snapshot_service.py` (new), `family_package_import.py` (manifest capture).
- **Routers:** `routers/families.py` (manifest, drift, sign-out endpoints), `routers/admin.py`
  (reference source versions, clinical audit read, expose date filters).
- **Frontend:** `FamilyReportPage.tsx` (footer + render-from-snapshot), a Provenance panel, a
  drift badge in the variant review, a sign-out action, an audit timeline, a "stale
  classifications" view.
- **Tests:** backend pytest per service + the integration/smoke suite; frontend vitest per UI;
  reuse the append-only-trigger test pattern from `029`.
