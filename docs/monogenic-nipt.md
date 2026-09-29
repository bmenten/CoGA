# Monogenic NIPT — developer notes

Monogenic NIPT screens a pregnancy for single-gene disorders from maternal-plasma cfDNA and a paternal
sample. CoGA estimates the fetal fraction (FF) and assigns each cfDNA variant to one of eight
maternal/fetal categories. The model, the categories, the flags, the presets and their limits are
documented for lab users in the in-app reference
[`frontend/src/content/docs/monogenic-nipt.md`](../frontend/src/content/docs/monogenic-nipt.md) (shown
at `/docs/reference/monogenic-nipt`). That page is the canonical home; keep it in step with the code.
This note covers the implementation.

## Family model and tags

A NIPT case is a trio backed by two samples (`backend/app/services/nipt.py`, `resolve_nipt_trio`):

- `family.metadata.analysis_type = "monogenic_nipt"` marks the family and shows the NIPT page. Family
  Builder sets it, and a package manifest's `analysis_type` is promoted to it.
- The cfDNA sample carries `sample.metadata.assay = "nipt_cfdna"` and the `mother` role; without the tag
  the active `mother` member's sample is used.
- `sample.metadata.assay_panel` (optional, on the cfDNA sample) names the capture panel and scopes the
  artifact list; without it every NIPT family on the assembly shares the `nipt_cfdna` list.
- The father is the active `father` member; the fetus (`embryo`, else `proband`) is an optional
  placeholder without data.

## Input

One combined VCF with a father and a cfDNA column, loaded through the family small-variant upload or a
package import. The analysis reads each call's GT, DP, AD and AF and the site-level QUAL
(`entries.qual`) from ClickHouse (`build_nipt_observations`, `nipt_service.py`):

- cfDNA alt reads = `AD[1]`; VAF = `AF[0]`, else alt reads ÷ DP. Without AD no site counts as present,
  so no FF can be estimated.
- The father's state (`hom_ref` / `het` / `hom_alt` / `missing`) is the shared genotype class of his GT
  (`derive_father_state` → `classify_genotype`): a haploid `1` is `hom_alt`, `0` is `hom_ref`, and a
  no-call or half reference call (`./0`) is `missing`.
- The analysis reads a father call below `min_father_dp` as `missing` (`_trusted_father_state`), for the
  candidate categories, category 8, the FF sites, fetal sex and the paternity evidence.

## Defaults

`NiptQualityThresholds` and the function defaults in `backend/app/services/nipt_analysis.py`:

| Setting | Default | Used for |
| --- | --- | --- |
| `min_cf_dp` | 20 | quality filter, FF sites, `low_depth`, `low_depth_dropout` |
| `min_cf_alt_reads` | 3 | presence in the cfDNA, FF sites |
| `min_qual` | 20 | quality filter, FF sites |
| `min_father_dp` | 10 | a usable father call (see above) |
| `min_father_qual`, `min_vaf` | 20, 0 | not effective: the father's QUAL is never set, and there is no VAF floor |
| `vaf_ceiling` | 0.25 | FF sites |
| `min_sites` / `hard_floor` | 30 / 5 | low-confidence FF / no estimate |
| `max_ci_halfwidth`, `disagreement_tol` | 0.03, 0.03 | low-confidence FF, external-FF disagreement |
| `overdispersion` | 0.005 | beta-binomial likelihood |
| de novo prior weight | 0.02 | category 1 |
| `min_separation`, `detect_min`, `ff_too_low` | 0.90, 3, 0.01 | `ambiguous`, category 8 versus `undetectable_at_ff`, withholding the fetal call |

The FF estimate is `2 × Σ alt ÷ Σ depth` over the selected category-7 sites, with a Wilson interval
scaled by 2. The per-site median (`ff_median`) is returned by the API but not shown.

`_candidate_categories` bounds the categories by the father's state: `hom_ref` 1, 2, 3, 5; `het` 2–7;
`hom_alt` 3, 4, 6, 7; `missing` 1–7.

Fetal sex (`infer_fetal_sex`) uses non-PAR chrX sites (2.8 Mb < position < 154.9 Mb) where the father's
usable call is `hom_alt` (`1/1` or haploid `1`): a present allele below VAF 0.30 counts as transmitted,
an absent one with at least 3 expected alt reads as not transmitted. At least 8 informative sites; 3 or
more transmitted → female, none → male. The on-target coverage check (`nipt_coverage.py`) flags a
region with no coverage, a median below 20×, or less than 90% covered.

Flags that `classify_site` emits: `sex_chromosome_unsupported`, `ff_low_confidence`,
`low_depth_dropout`, `false_negative`, `undetectable_at_ff`, `no_alt_signal`, `father_no_coverage`,
`low_depth`, `ambiguous`, `ff_too_low`. Multi-allelic sites are not special-cased.

## Two paths

Both paths take FF from `filter_sites_and_estimate_ff`: all family records, the quality filter (cfDNA
DP and QUAL) and the artifact list, then the estimate.

- **Summary** — `run_family_nipt_analysis` → `run_nipt_analysis`: that FF, then classification of the
  filtered sites, the category and filter counts, fetal sex, and `paternal_evidence` (the category-7
  and -8 sites with a usable father call, which the Sample QC paternity check reads instead of the raw
  counts). Serves the FF badge, the funnel and the counts, and the Sample QC NIPT checks.
- **Variant list** — `get_family_nipt_variants`: that FF, then the filtered records (at most 5,000),
  classified without the quality filter and skipping artifact-list sites, then `min_confidence`, the
  category filter or inheritance preset, and paging. `recessive_at_risk` keeps every carrier variant in
  genes with a maternal carrier (categories 2–6) and a paternal carrier (het or hom-alt GT).

Endpoints (`routers/families_nipt.py`, under `/api`): `GET /families/{id}/nipt/summary`,
`/nipt/variants` and `/nipt/coverage`. Summary and variants accept an `external_ff` query parameter
that the UI never sends. The artifact list has admin-only endpoints in `routers/admin.py`:
`GET` / `POST /admin/nipt/artifacts`, `DELETE /admin/nipt/artifacts/{id}` and
`POST /admin/nipt/artifacts/auto-seed`; the table is `nipt_artifact_variants` (see
[database.md](database.md)).

Auto-seed (`auto_seed_nipt_artifacts` → `fetch_recurrent_small_variant_ids`) counts carriers only among
the samples tagged `assay: nipt_cfdna` whose `assay_panel` resolves to the scope, once per sample
whether ClickHouse stores its calls by name or UUID. It drops variants with `is_gnomad_gt_5_percent`,
and variants whose pooled `annotation_index.clinvar_terms` pass `clinvar_may_assert_pathogenic`
(`variant_prioritization.py`: a whole-word "pathogenic", or any conflicting record, because the
per-submission `CLNSIGCONF` breakdown is not imported).

## Known defects

- A de novo call has no check that the VAF lies within the FF interval: only the category-1 prior, the
  3-alt-read presence rule and a usable `hom_ref` father call.
- The variant list classifies sites that fail the quality filter; a site that fails on QUAL alone
  carries no flag.
- Category 3 is labelled `maternal_inherited_het` and counts as inherited in the maternal-dominant
  preset and the maternal-transmission check, but with a `hom_alt` father the fetus's alt is his, and
  with a `het` father it is unknown whose.
- The auto-seed protections need annotation: a site with no population frequency or ClinVar record is
  not protected.

These and the preset caveats are described for lab users in the in-app reference.

## Where the code lives

- Backend: `backend/app/services/nipt.py` (tags, trio), `nipt_analysis.py` (pure maths),
  `nipt_service.py` (wiring to family data), `nipt_artifact_pg.py`, `nipt_coverage.py`,
  `routers/families_nipt.py`.
- Frontend: `frontend/src/pages/families/FamilyNiptPage.tsx`, `FamilyNiptReportPage.tsx`,
  `NiptClassificationBlock.tsx`, `niptClassification.ts`; presets in `smallVariantSearch.ts`.
- Tests: listed in [testing.md](testing.md).

Related: [haplotype-segregation-analysis.md](haplotype-segregation-analysis.md) (shared pedigree
machinery), [application-scheme.md](application-scheme.md) (storage boundary),
[data-import.md](data-import.md).
