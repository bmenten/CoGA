# Monogenic NIPT Analysis

> **Status: implemented (phases 0–7); this remains the design reference.** Phases
> 0–7 below have shipped — the family model, ingestion, fetal-fraction estimation
> and classification, the summary/variants/coverage endpoints, the artifact list
> (with auto-seed), and the dashboard. The gap analysis and per-phase plan are kept
> as the design record; deviations from the original plan are noted inline (e.g.
> QUAL is stored site-level, not per-call).

Monogenic NIPT (non-invasive prenatal testing) screens a pregnancy for single-gene
disorders from **cell-free DNA (cfDNA) in maternal plasma**, cross-referenced with a
paternal sample. The plasma cfDNA is a **mixture**: mostly maternal DNA with a minor
**fetal fraction (FF)**. CoGA's job is to (1) estimate the fetal fraction, (2) classify
every cfDNA variant by the maternal/fetal zygosity that explains its observed
allele fraction, and (3) let an analyst hunt for de novo, paternal/maternal dominant,
and recessive risks — reading the fetal genotype off the VAF rather than observing it
directly.

---

## The clinical question

Two samples are sequenced over the same target (≈5,000 genes today; potentially the
whole exome later, or a smaller panel):

- **Father** — a normal germline VCF.
- **Maternal plasma cfDNA** — a VCF whose signal is `maternal · (1 − FF) + fetal · FF`.

The fetus is **never sequenced directly**. Its genotype is **inferred** from the
deviation of the cfDNA allele fraction (VAF) away from the clean maternal expectations
(0%, 50%, 100%), in proportion to the fetal fraction. We model the family as a **trio**
(father, mother, fetus) backed by only **two physical samples**.

The questions an analyst must be able to answer:

- **What is the fetal fraction?** Everything downstream depends on it.
- **How many variants fall in each maternal/fetal category**, and how many were
  dropped by quality and artifact filters?
- **Is on-target coverage adequate** across the investigated regions (per region and
  overall median)?
- **De novo dominant** — is there a plausible causal de novo variant in the fetus
  (distinguished from maternal mosaicism and noise)?
- **Paternal / maternal dominant** — did the fetus inherit a causal parental variant?
- **Recessive** — do father and mother each carry a causal variant in the *same* gene,
  and did the fetus inherit **both**?

> **Derived, not entered.** The fetal fraction and every per-variant category are
> **computed** from the two samples' allele fractions, depths, qualities, and the
> father's genotype. The only inputs are the two VCFs, the target regions / coverage,
> the pedigree, and the analyst's filter choices.

---

## Data model: a 2-sample trio

Three pedigree nodes, two physical samples:

| Pedigree node | Role | Physical sample | Variant data |
| --- | --- | --- | --- |
| Father | `father` | Father germline VCF | observed genotypes |
| Mother | `mother` | **cfDNA plasma VCF** (the mixture) | observed allele fractions |
| Fetus | `embryo` | placeholder sample, no VCF | **inferred**, never observed |

This fits the existing schema without new "sampleless member" machinery:
`family_members.sample_id` is `NOT NULL`, so the fetus gets a lightweight placeholder
sample with no uploaded variants; the cfDNA VCF is uploaded **as the mother's sample**
because it is literally maternal-plus-fetal signal. The single-parent / embryo-anchored
pedigree core already supported by the haplotype lineage service
(`backend/app/services/haplotype_lineage_service.py`, `identify_core`) carries over.

Tagging that drives the feature:

- cfDNA sample: `samples.metadata.assay = "nipt_cfdna"`.
- Family: `families.metadata.analysis_type = "monogenic_nipt"` — this is what surfaces
  the NIPT section in the UI (mirroring the "shown only when that data is present"
  pattern used for the other per-data-type views).

**Input format (decided).** The input is a **single combined two-sample VCF** (father
column + cfDNA column). The existing family-wide upload
(`backend/app/services/variant_upload_service.py`, `upload_family_small_variant_file`)
already parses N sample columns into the parallel `calls.*` nested arrays of the
ClickHouse `entries` table in one pass, so no new upload path is needed. Two *separate*
VCFs (father, then cfDNA) would need incremental/merge upload — today `overwrite=True`
deletes the whole family — and are **out of scope**; the lab will supply the combined VCF.

---

## The classification maths

Let **FF** be the fetal fraction. For a biallelic site, write the maternal alt-copy
fraction as `m ∈ {0, ½, 1}` (hom-ref / het / hom-alt) and the fetal alt-copy fraction
as `f ∈ {0, ½, 1}`. The expected cfDNA allele fraction is

```text
VAF = m · (1 − FF) + f · FF
```

Enumerating the maternal/fetal states (and using the father's genotype to resolve the
two cases that need it) gives the eight categories:

| Cat | Maternal / fetal state | Expected cfDNA VAF | Distinguishing rule |
| --- | --- | --- | --- |
| 1 | **de novo in fetus** (absent in mother and father) | **FF / 2** (low) | low VAF, father hom-ref → de novo *vs* maternal mosaicism *vs* noise |
| 2 | het mother, fetus did **not** inherit | **50% − FF/2** | |
| 3 | het mother **and** het fetus | **50%** | |
| 4 | het mother, **hom** fetus | **50% + FF/2** | |
| 5 | **hom** mother, het fetus | **100% − FF/2** | |
| 6 | hom mother **and** hom fetus | **100%** | |
| 7 | absent in mother, present in father → transmitted to fetus (het) | **FF / 2** | father carries, mother absent, **present** in cfDNA |
| 8 | father **hom-alt**, **absent** in cfDNA | (expected FF/2, observed ≈ 0) | **false negative** — QC metric |

The expected VAFs are deterministic functions of FF, so the classifier's cluster
centres are fixed once FF is known.

### Estimating the fetal fraction

**FF = 2 × median(VAF) over category-7 sites** — sites where the mother is hom-ref,
the father carries the allele, and the variant is present in the cfDNA (the fetus
obligately inherited a paternal alt allele, so it sits at a clean `FF/2`). Restrict to
well-covered, high-quality autosomal sites; use a robust median with an inter-quartile
sanity band, and report `N(sites)` and a confidence interval so the estimate can be
trusted or distrusted. Category 8 (paternal hom-alt absent from cfDNA) is the
complementary **false-negative QC signal**: those sites *should* appear at `FF/2`, so a
high category-8 rate flags dropout, insufficient FF, or coverage gaps.

When the run also supplies an **external FF** (from an upstream caller), it is recorded
alongside the cat-7 estimate, which remains the computed default; the two are surfaced
together and a **disagreement is flagged** rather than silently overridden.

### Assigning categories

As FF → 0 the band centres collapse (category 2 at `50 − FF/2` and category 3 at `50`
nearly coincide), so classification is a **1-D mixture-assignment problem with known
centres**, not a set of hard VAF cut-offs. Given FF, assign each cfDNA variant to the
category whose expected VAF maximises a **binomial likelihood**
`Binom(alt_reads | DP, expected_VAF)`, and attach a confidence. Low-depth sites get low
confidence rather than a forced call. This is also what separates a true de novo
(category 1: a tight `FF/2` peak with adequate depth) from diffuse low-VAF noise;
recurrent-artifact sites are removed upstream by the artifact filter.

### Reading inheritance off the categories

The clinical filters are direct reads of the category assignment:

- **De novo dominant candidate** → category 1.
- **Paternal dominant transmission** → category 7 present (vs. absent = not transmitted).
- **Maternal dominant transmission** → a maternal-het variant landing in category 3/4
  (inherited) vs. category 2 (not inherited).
- **Recessive at-risk** → father carries a causal variant in gene X **and** mother
  carries a (different) causal variant in gene X, **and** the fetus inherited **both**:
  the paternal allele via category 7 (present), the maternal allele via category 3/4
  (VAF ≥ 50%). Inherited-from-neither/one resolves to not-at-risk / carrier.

---

## Gap analysis: reuse vs. build

The headline decision is to **reuse the small-variant plumbing and build a new analysis
section on top of it** — *not* to overload the existing trio filter. The existing
small-variant inheritance engine (`backend/app/services/clickhouse_family_variants.py`)
matches **observed, discrete genotypes** with `arrayExists()`; NIPT needs **VAF-band
inference against a fetal fraction**. Mixing the two would distort both.

| Capability | Status | Plan |
| --- | --- | --- |
| Multi-sample VCF → per-sample `calls.{gt,dp,ab,af,ad}` in ClickHouse `entries` | exists | reuse |
| Annotation parsing (gene, consequence, gnomAD, CADD/REVEL/SpliceAI) | exists | reuse |
| Gene / panel / frequency / consequence / ROI filters (`SmallVariantQueryFilters`) | exists | reuse |
| Embryo role + single-parent (donor) pedigree core | exists | reuse for the family model |
| Internal cohort recurrence counts (`project_gt_stats`, `_fetch_internal_cohort_map`) | exists (display only) | leverage to seed the artifact list |
| **Site-level QUAL** | was missing — QUAL parsed then discarded | **done** — a variant-level `qual` column on `entries` (QUAL is a site-level VCF field, so not a per-call array), read into `SmallVariantRecord.qual` → the NIPT quality filter |
| **Quality filter (VAF / QUAL / DP) with drop counts** | **missing** | new NIPT quality step + counts |
| **Recurrent-artifact (panel-of-normals) variant filter** | **done** | per-assay `nipt_artifact_variants` table + query-time exclusion + funnel counts + admin CRUD and cohort auto-seed |
| **Fetal-fraction estimation** | **missing** | new |
| **8-category VAF classification** | **missing** | new |
| **Per-region + overall median on-target coverage** | **missing** — coverage stored per interval; only a transient windowed mean; no median | new aggregation; target = existing gene panel / family ROI |
| NIPT page / dashboard | **missing** | new |

---

## Implementation plan (phased)

Each phase is independently shippable and testable. Backend tests run via
`backend/.venv/bin/python -m pytest`; the frontend gate is `npx vitest run`
(`tsc`/`eslint`/`build` do not catch component-test regressions). All endpoints must
emit audit / UI-event logs (per `AGENTS.md`), and frontend components must use the
shared styles in `frontend/src/styles/theme.css`.

**Critical path: Phase 0 → 1 → 3.** Phase 3 (FF estimation + classification) is the
whole feature's value and should be built test-first against synthetic data before any
UI. Phases 2 and 4 can proceed in parallel with 3.

### Phase 0 — Family model & scaffolding

Extend ped/family creation to accept a NIPT trio: father sample, cfDNA sample
(`role: mother`, `metadata.assay = "nipt_cfdna"`), placeholder fetus (`role: embryo`),
and `family.metadata.analysis_type = "monogenic_nipt"`. Touch `ped_service.py` and
`family_member_management_service.py`.
*Deliverable:* a NIPT family can be created and shows an (empty) NIPT tab.

### Phase 1 — Ingestion extension (capture quality) — **shipped**

VCF `QUAL` is a **site-level** field (one value per variant line), so it is stored
as a nullable, variant-level `qual Nullable(Float32)` column on the `entries` table
— **not** a per-call `calls.qual` array (which would only duplicate one value across
samples). `SmallVariantRecord` gained an optional `qual`, populated in
`variant_upload_service.py` where QUAL was being dropped; the column was added with an
idempotent `ALTER TABLE … ADD COLUMN IF NOT EXISTS` through the `ensure` path. Per-sample
quality (DP / VAF / GQ) already came from the existing per-call fields. The shared entries
read selects `qual`, so it reaches the NIPT quality filter via
`NiptSiteObservation.cf_qual`. The combined two-sample (father + cfDNA) VCF is the
canonical NIPT input.
*Deliverable (met):* a father+cfDNA VCF stores both samples with per-call VAF/DP and the
site QUAL, which the NIPT quality filter uses.

### Phase 2 — Recurrent-artifact (panel-of-normals) filter

New Postgres table `nipt_artifact_variants`, keyed **per assay/panel** (`assay_key`,
`assembly_id`, `variant_id` = `chr-pos-ref-alt`, recurrence_count, source `curated|auto`,
label) — recurrent artifacts are capture/chemistry-specific, so a new panel starts with a
clean list. Seed automatically from `project_gt_stats` recurrence **within the same
assay** and allow manual curation via admin. Apply at analysis time as an exclusion that
**returns counts** (so the UI can report "N filtered as artifacts").
*Deliverable:* artifact-list CRUD (scoped by assay/panel) + an exclusion helper that
reports counts.

### Phase 3 — NIPT core analysis service *(the heart)*

> Full algorithm reference: **[Fetal Fraction & Classification Algorithm](monogenic-nipt-classification.md)**
> — data structures, the FF estimator, the beta-binomial classifier, edge cases, worked
> examples, and the test plan. Build this section test-first against synthetic data.

New `backend/app/services/nipt_analysis.py`: load father + cfDNA `calls.*` from the
`entries` table (reuse the `clickhouse_family_variants` read helpers), apply the quality
filter (configurable VAF / QUAL / DP) and the artifact filter, then estimate FF from
category-7 sites. When an **external FF** accompanies the run, record it alongside the
cat-7 estimate and flag any disagreement (the cat-7 estimate stays the computed default).
Binomial-likelihood classify every cfDNA variant into categories 1–8. Returns FF (cat-7
estimate + CI + N sites, plus any external value and disagreement flag), per-category
counts, filter counts (low-quality, artifact, total in), and per-variant
`{category, confidence, expected_vaf, observed_vaf}`.
*Deliverable:* a pure-Python service with thorough unit tests over synthetic VAF
distributions at several FF values, including edge cases (FF → 0, low DP, category-8
dropouts).

### Phase 4 — Coverage summary

The assay's **target is the existing gene panel / family ROI** definition — there is no
separate target-BED upload. Reuse the interval-track coverage upload, intersect the
uploaded coverage intervals with the panel/ROI regions, and add per-region and overall
**median on-target coverage** via ClickHouse `quantile`/`median` aggregation in
`clickhouse_interval_tracks.py` + `bed_service.py`, exposed through a new endpoint
returning `{overall_median_on_target, per_region: [...]}`.
*Deliverable:* a coverage-summary endpoint + tests.

### Phase 5 — NIPT API endpoints

- `GET /families/{id}/nipt/summary` → FF (cat-7 estimate + any external value with a
  disagreement flag), category counts, filter counts, coverage summary.
- `GET /families/{id}/nipt/variants` → classified variants, accepting the **reused**
  `SmallVariantQueryFilters` (gene, panel, gnomAD, consequence, ROI) **plus** NIPT-specific
  filters: `category`, `min_confidence`, and an inheritance preset
  (`de_novo` / `paternal_dominant` / `maternal_dominant` / `recessive_at_risk`).

*Deliverable:* documented, audited endpoints.

### Phase 6 — Frontend NIPT section

New page under `frontend/src/pages/families/`, shown when
`analysis_type = monogenic_nipt`. Top of page: **FF gauge**, **category-count bar**
(categories 1–8), **filter funnel** (total → quality-filtered → artifact-filtered →
analysed), and a **coverage summary** (median on-target + per-region table, reusing the
`CoverageSegmentsChart` patterns). Variant table reuses the `SmallVariantTable`
presentation with added **category**, **confidence**, and **fetal-inheritance** columns.
*Deliverable:* a working NIPT dashboard with vitest coverage.

### Phase 7 — Clinical inheritance filters — **shipped**

The four presets are implemented on `/families/{id}/nipt/variants`. `de_novo` →
category 1, `paternal_dominant` → category 7, `maternal_dominant` → categories 3/4 are
simple category filters. `recessive_at_risk` needs cross-variant gene pairing, so the
category filter is applied *after* classifying the whole candidate set: a fetus is at
risk in a gene when it is homozygous-alt for a variant (category 4 or 6), or when the
gene carries a transmitted paternal allele (category 7) **and** a transmitted maternal
allele (category 3) at different loci (compound het) — both members of each compound
pair are kept. Apply the gene/consequence/frequency filters and `min_confidence` first.
*Deliverable (met):* one-click "recessive at-risk", "paternal/maternal dominant
transmission", and "de novo candidates".

### Phase 8 — Docs & integration tests

Finalise this document (mark phases as shipped as they land), add an end-to-end test with
a synthetic father+cfDNA fixture, and link the feature from the README and ROADMAP.

---

## Resolved decisions

Settled 2026-06-22:

1. **Input format** — **combined two-sample VCF** (father + cfDNA columns). Reuses the
   existing family-wide upload; separate VCFs are out of scope.
2. **Coverage input** — the lab uploads a **coverage BED**, and the **target is the
   existing gene panel / family ROI** (no separate target-BED upload; no CRAM-derived
   coverage).
3. **Fetal fraction** — **computed from category-7 sites**, with an **optional external
   FF accepted as an override/cross-check**; the computed estimate stays the default and
   disagreement is flagged.
4. **Artifact-list scope** — **per assay/panel** (recurrent artifacts are
   capture/chemistry-specific; a new panel starts with a clean list).

---

## Related documentation

- [Fetal Fraction & Classification Algorithm](monogenic-nipt-classification.md) — the
  detailed Phase 3 algorithm reference (FF estimator, beta-binomial classifier, edge
  cases, signatures, test plan).
- [Haplotype Segregation Analysis](haplotype-segregation-analysis.md) — the PGT
  haplotype track; shares the embryo/single-parent pedigree machinery reused here.
- [Application scheme](application-scheme.md) — what Postgres and ClickHouse each hold.
- [Database Schema](database.md) — table-level reference.
- [Data Import Guide](data-import.md) — upload flows extended by Phase 1.
