# Annotation & tool provenance (VCF-header capture)

> **What this gives you:** for every family, an automatically-captured record of
> *which versions of which tools and databases produced its data* — the variant
> caller (DeepVariant/GATK/Sniffles/Spectre/TRGT), the annotation engine
> (VEP/snpEff/bcftools) and the upstream reference databases embedded in the VEP
> header (gnomAD, ClinVar, dbNSFP, SpliceAI, dbSNP, …). It is parsed from the
> `##` header lines at import, stored per-family, refreshed on re-import, shown on
> the **filter page** and the **report**, and frozen into the **sign-out** record.

This is the implementation of the "harvest the VCF header" path that
[clinical-traceability.md](clinical-traceability.md) §(A) reserved as `source='vcf_header'`.
Version control of the annotation inputs is a clinical-traceability requirement: a
result must be re-linkable to exactly what produced it (IVDR Annex I §16.1; see
[TF-09 §7](regulatory/TF-09-verification-validation.md) "Traceerbaarheid").

---

## 1. Why

Variant calling and annotation are versioned, moving targets — a new gnomAD or
ClinVar release, a VEP bump, a new caller version can change a result. The
versions are stated in the source VCF headers, but historically CoGA discarded
them at import (only the *content* hash `annotationSetHash` was kept; see
[the storage model](#5-relationship-to-the-clickhouse-annotation-hash)). This
feature captures the **stated versions** so a report can name its provenance and
an auditor can reconstruct it.

## 2. What is captured, per modality

All modalities except Paraphase ingest VCFs; their `##` headers are parsed by
[`vcf_header_provenance.py`](../backend/app/services/vcf_header_provenance.py).

| Modality | Ingestion hook | Header sources parsed |
| --- | --- | --- |
| **Small variants** (SNV/INDEL) | [`variant_upload_service.upload_family_small_variant_file`](../backend/app/services/variant_upload_service.py) | **(a)** the VCF header — caller (`##source`, `##DeepVariant_version`, `##GATKCommandLine` Version=…), annotation engine (`##VEP=…` with its embedded gnomAD/ClinVar/dbNSFP/SpliceAI/dbSNP/COSMIC/SIFT/PolyPhen/assembly/GENCODE releases, `##SnpEffVersion`/`##SnpEffCmd`, `##bcftools_*Version`); **(b)** when annotations are supplied as a **separate VEP tab (TSV) file**, its `## … version …` header — the database releases live there for TSV-annotated families, parsed by `extract_vep_tab_provenance` and merged in |
| **Structural variants** | [`family_package_import._import_sv_needlr_dataset`](../backend/app/services/family_package_import.py) | SV caller (`##source=Sniffles2_2.2`, `##source=Spectre`, `##spectreVersion`, NeedlR), `##reference`, `##fileDate`; **plus** annotation-database releases mined from the `##INFO` descriptions (`extract_info_description_provenance` — GENCODE/OMIM/GenCC/gnomAD/GIAB), since the NeedlR SV annotator states them there rather than in header lines |
| **Repeat expansions (TRGT)** | [`repeat_expansion_pg.ingest_family_trgt_text`](../backend/app/services/repeat_expansion_pg.py) and `ingest_trgt_text` (the per-sample route long-read packages take, since that pipeline writes no joint TRGT VCF) | `##trgtVersion`, `##trgtCommand`, `##source=TRGT`, `##reference` |
| **Pipeline run record** | [`family_package_datasets._import_pipeline_info_dataset`](../backend/app/services/family_package_datasets.py) | Not a VCF header: the Nextflow `software_versions.yaml` shipped with a long-read package, parsed by `family_package_qc.extract_pipeline_versions` and recorded with `source='manifest'`. It names every tool behind the callset (caller, annotator, CNV/repeat/mtDNA callers, workflow + Nextflow version), including tools whose own outputs carry no version header. Run parameters (reference build, callers, annotation caches) go to `families.metadata["pipeline"]`. |
| **Paraphase** | (JSON, not a VCF) | No VCF header — Paraphase provenance is taken from the **import-package manifest** declaration (`source='manifest'`) when present. Harvesting a version from the Paraphase JSON is a noted extension point (§7). |

The parser is **best-effort and never raises**: an unrecognised or malformed
header simply yields less, never an error — provenance capture can never fail an
import.

## 3. The data model

Provenance is stored once per family in **`family_annotation_manifest`**
([04_traceability.sql](../backend/db/schema/postgres/04_traceability.sql)):

| Column | Meaning |
| --- | --- |
| `modules` (JSONB) | `{ moduleKey: { version, detail, by_modality?, … } }` — one canonical entry per tool/database. `version` is the flat representative; `by_modality` (e.g. `{ "snv": "49", "sv": "45" }`, issue #294) keeps the per-modality truth. Free-form so new sources need no schema change. |
| `source` | `'vcf_header'` (parsed from headers) · `'manifest'` (declared by the import package) · `'manual'` (admin-entered) |
| `recorded_by` / `recorded_at` | provenance of the provenance — who/what wrote it, when |
| `assembly_id` | the family's assembly (for the platform reference layer) |

Canonical module keys (`vep`, `deepvariant`, `gatk`, `sniffles`, `spectre`,
`trgt`, `gnomad`, `clinvar`, `dbnsfp`, `spliceai`, …) are normalised by the parser
so versions harvested from *different* modality VCFs merge into one family
manifest without clobbering — each modality contributes the tools it names. The
variant caller is tagged with its modality (`detail: "snv caller"` /
`"sv caller"` / `"repeats caller"`) so one family can list several callers.

The read service
[`annotation_manifest_service.get_family_annotation_manifest`](../backend/app/services/annotation_manifest_service.py)
merges this **per-family pipeline layer** with the **platform reference layer**
(assembly, Monarch, …) and renders a labelled, ordered module list.

## 4. Version-control lifecycle

```
 import / re-import ──▶ parse ## headers ──▶ merge_vcf_header_provenance()
                                                   │
        ┌──────────────────────────────────────────┤
        │ source == 'manual'?  ── yes ──▶ skip (admin curation wins)
        │ else: refresh-merge into the family manifest (source='vcf_header')
        └──────────────────────────────────────────┘
                                                   │
   shown live on ──▶ filter page + report footer   │
                                                   ▼
   case sign-out ──▶ FROZEN into the content-hashed, append-only report snapshot
```

The rules, all in
[`merge_vcf_header_provenance`](../backend/app/services/annotation_manifest_service.py):

- **Refresh on re-import.** Re-importing a family's (re-annotated) VCF overwrites
  the stale versions with the freshly-parsed ones, *per key*; modules the new
  input does not mention are preserved (re-importing only the SV file does not
  wipe the SNV-derived VEP/gnomAD versions). This is the version-control update
  path: the live manifest always reflects the latest import.
- **Manual override is never clobbered.** If an admin has curated the manifest
  (`source='manual'`, via the admin endpoint / `set_family_annotation_manifest`),
  import leaves it untouched.
- **Transaction-safe.** The write runs in a SAVEPOINT and joins the ingestion's
  transaction, so provenance persists **iff** the data it describes does, and a
  provenance failure can never break or roll back an import.
- **Frozen history.** The manifest table holds the *current* manifest (one row per
  family). Historical versions are preserved by the **sign-out snapshot**
  ([report_signout_service.py](../backend/app/services/report_signout_service.py)
  freezes the manifest into each content-hashed, append-only report) and by the
  drift surfacing below — consistent with the file's "history lives in the audit
  trail / report snapshots" model.

### Drift

Independently, [`classification_drift_service.py`](../backend/app/services/classification_drift_service.py)
flags any ACMG classification whose **annotation-set hash** changed since it was
made ("evidence changed since you classified this"). The hash is the authoritative
change key; the manifest versions are the human-readable *what* moved.

## 5. Relationship to the ClickHouse annotation hash

Two complementary records:

- **`annotationSetHash`** (ClickHouse, per variant) — a content fingerprint that
  detects *that* an annotation changed and pins each family to its own snapshot
  (see the annotation-storage model). It does not say *which version*.
- **`family_annotation_manifest`** (Postgres, per family) — the human-readable
  *versions* (VEP 110, gnomAD r2.1.1, …) captured here.

Together: the hash proves change; the manifest names the versions.

## 6. Where it is surfaced

- **Filter pages** — [`AnnotationProvenanceSummary.tsx`](../frontend/src/pages/families/AnnotationProvenanceSummary.tsx)
  renders an "Annotation versions" footer at the bottom of the **SNV** and **SV**
  filter pages. Each passes its `modality`, so it shows *that* modality's versions
  (plus shared platform/reference modules) — e.g. the SV page shows GENCODE 45 while
  the SNV page shows 49 (§8) — with the `source` (e.g. *from VCF headers*).
- **Report** — [`FamilyReportPage.tsx`](../frontend/src/pages/families/FamilyReportPage.tsx)
  renders the full provenance footer (`Modules & versions: …`), showing any
  per-modality divergence inline, frozen at sign-out.
- **API** — `GET /families/{id}/annotation-manifest`.
- **Admin override** — `PUT /families/{id}/annotation-manifest` (`source='manual'`).

## 7. Limitations & extension points

- **Paraphase** has no VCF header; its version comes from the import-package
  manifest if declared. Harvesting a version from the Paraphase JSON is a clean
  follow-up (the merge/render path already supports a `paraphase` module key).
- **Three header shapes are parsed:** the in-VCF `##VEP="…"` line, a separate VEP
  tab (TSV) file's `## … version …` header (`extract_vep_tab_provenance`), and —
  for annotators that bury releases in `##INFO=<…Description="…">` free text (the
  NeedlR SV pipeline) — a **conservative, allowlisted** INFO-description miner
  (`extract_info_description_provenance`: GENCODE/OMIM/GenCC/gnomAD/GIAB/ClinVar/
  dbSNP/COSMIC, each anchored to a version-shaped token). Free-text mining is more
  fragile than a clean `##VEP=` line, hence the allowlist.
- **Per-database divergence across modalities** (e.g. SNV vs SV citing different
  GENCODE releases) is now preserved via `by_modality` and surfaced per page — see
  §8. The flat representative `version` remains for back-compat and is the latest
  write.
- **Unknown tools** are still captured under their lower-cased token (they render
  with a title-cased label), so a new caller/annotator shows up without a code
  change.

## 8. Per-modality provenance (#294)

**Problem.** SNV, SV and TRGT inputs are annotated by *different* pipelines and can
cite **different releases of the same database** — observed on `f_18.16172`: the SNV
VEP TSV states **GENCODE 49** while the SV annotator states **GENCODE 45**. The flat
manifest (one entry per database per family) can hold only one, so the divergence is
silently collapsed. That is acceptable for an at-a-glance footer but **not for a
clinical-grade, fully-traceable record**.

**Goal.** Capture and display annotation provenance **per modality**, so the SNV
filter page shows the SNV annotation versions, the SV page shows the SV ones, and the
report/sign-out freezes the complete per-modality set — every result re-linkable to
exactly the versions that produced *it*.

**Proposed schema.** Extend `modules` to carry per-modality detail rather than a
single value (no breaking change to the read API if the flat view is derived):

```json
{
  "gencode": {
    "version": "49",                                   // representative (back-compat)
    "by_modality": { "snv": "49", "sv": "45" }         // new: the full per-modality truth
  }
}
```

(Alternative: a `family_modality_annotation` table keyed by `(family_id, modality)`.
The JSONB-extension above is lighter and keeps the single-row-per-family model.)

**Touch points.**

- *Capture* — `merge_vcf_header_provenance` gains a `modality` argument; each ingestion
  hook (SNV/SV/TRGT) records its versions under that modality. Refresh-on-re-import and
  manual-override-wins semantics carry over per modality.
- *Read/UI* — `get_family_annotation_manifest` can return both the flat view and the
  per-modality breakdown; `AnnotationProvenanceSummary` takes an optional `modality`
  prop so each filter page footer shows *its* modality (plus shared platform layer).
- *Traceability* — the per-modality manifest is what the sign-out snapshot freezes, and
  what drift compares against; this is the clinical-grade form
  ([clinical-traceability.md](clinical-traceability.md) §A).

**Status. ✅ Implemented** ([issue #294](https://github.com/bmenten/CoGA/issues/294)).
`merge_vcf_header_provenance` takes a `modality`; each module records
`by_modality[modality]` alongside the flat representative `version`. The SNV and SV
filter-page footers pass their modality and show only their own versions (shared
platform/reference modules always show); the report footer renders the per-modality
breakdown for any database that diverged (e.g. `GENCODE 49 (snv), 45 (sv)`), and that
full set is what sign-out freezes.

## 9. Tests

- [test_vcf_header_provenance.py](../backend/tests/test_vcf_header_provenance.py) —
  parser unit tests with realistic headers for every modality + merge semantics.
- [test_annotation_manifest.py](../backend/tests/test_annotation_manifest.py) —
  `_refresh_modules` (refresh-wins, preserve-untouched) + module rendering.
- [AnnotationProvenanceSummary.test.tsx](../frontend/src/pages/families/__tests__/AnnotationProvenanceSummary.test.tsx) —
  the filter-page surface.

## 10. References

- [clinical-traceability.md](clinical-traceability.md) — the broader traceability design (manifest §A, drift §B, sign-out §D).
- [TF-09 §7](regulatory/TF-09-verification-validation.md) / [TF-08 SOUP register](regulatory/TF-08-soup-register.md) — regulatory placement (Traceerbaarheid; reference/SOUP versioning).
- [TF-09c](regulatory/TF-09c-e2e-pipeline-verification.md) / [TF-09d](regulatory/TF-09d-browser-e2e-verification.md) — pipeline & browser verification.
