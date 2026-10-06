# Monogenic NIPT — developer notes

Monogenic NIPT screens a pregnancy for single-gene disorders from maternal-plasma cfDNA and a paternal
sample. CoGA estimates the fetal fraction (FF), assigns each cfDNA variant to one of eight
maternal/fetal categories, gives the fetal-inheritance probabilities, and offers four views (de novo,
paternal, maternal, recessive) and a quality panel. The model, the rules and their limits are documented
for lab users in the in-app reference
[`frontend/src/content/docs/monogenic-nipt.md`](../frontend/src/content/docs/monogenic-nipt.md) (shown
at `/docs/reference/monogenic-nipt`). That page is the canonical home; keep it in step with the code.
This note covers the implementation.

The model is the lab's R pipeline NIPT-M, version 0.5.1, validated on six families with trio exomes
(`NIPT_MODEL_REFERENCE`). CoGA reimplements its frozen constants and rules, not its validation: the
validation against invasive exomes stays in the R project.

## Family model and tags

A NIPT case is a trio backed by two samples (`backend/app/services/nipt.py`, `resolve_nipt_trio`):

- `family.metadata.analysis_type = "monogenic_nipt"` marks the family and shows the NIPT page. Family
  Builder sets it, and a package manifest's `analysis_type` is promoted to it.
- The cfDNA sample carries `sample.metadata.assay = "nipt_cfdna"` and the `mother` role; without the tag
  the active `mother` member's sample is used.
- `sample.metadata.assay_panel` (optional, on the cfDNA sample; a manifest's is promoted) names the
  capture panel and scopes the artifact list; without it every NIPT family on the assembly shares the
  `nipt_cfdna` list.
- The father is the active `father` member; the fetus (`embryo`, else `proband`) is an optional
  placeholder without data.

## Input

Two inputs, one model:

- **The pair** (the NIPT-M pipeline's output): a single-sample VCF of the plasma (Mutect2 tumour-only)
  and one of the father, each with a per-target coverage table. `family_package_nipt.py` discovers the
  pair (`discover_nipt_package_files`), and `family_package_datasets.py` imports each VCF into the
  per-sample small-variant callset `nipt` (`NIPT_SMALL_VARIANT_SOURCE`, merged per sample as the mtDNA
  callset is): the plasma first, then the father's file through `paternal_record_filter`, which keeps a
  record whose call reaches 15% alt reads or that lies over a plasma call position (an MNV over any of
  its bases). A one-sample VCF's FILTER and caller metrics go with the call (`calls.filters`,
  `calls.metrics`; `vcf_call_metrics.py`). The coverage tables become `target_coverage` interval tracks
  (`nipt_target_coverage.py`: value = mean, record_id = gene, the rest in `metadata_json`).
- **One combined VCF** with a father and a cfDNA column, through the family small-variant upload or a
  package import. Its quality is the site's QUAL (`entries.qual`).

`build_nipt_observations` (`nipt_service.py`) turns the records into one `NiptSiteObservation` per
allele:

- Reads: alt reads = `AD[1]`; depth = the sum of the AD (DP without it), restored over the siblings of a
  record split from a multi-allelic one (`restored_site_depths`); VAF = alt reads ÷ depth, AF only
  without AD.
- The father's class (`derive_father_state`) is read off his allele depths, as the R pipeline's paternal
  classes: `low_support`, `hom_alt`, `het`, `low_vaf`, `hom_ref`; `absent` for no call in a per-sample
  callset, `missing` for a joint VCF's no-call; the GT only without AD. `trusted_father_state`
  (`nipt_analysis.py`) maps them onto what the candidates read: `absent` is `hom_ref` unless his depth
  there is below `min_father_dp`; `low_vaf`, `low_support` and a thin call are `missing`.
- A per-sample record with only the father's call is kept: the plasma's depth there comes from its
  per-target coverage (`TargetDepthLookup`: the lower of the target's mean and median), marked
  `cf_depth_estimated`. A record with only the plasma's call takes the father's depth from his table.
- **Representations.** Mutect2 writes adjacent phased SNVs as one MNV, so the two VCFs can hold one
  allele differently. A per-sample record missing the other sample's call looks it up base by base
  (`_BaseCallIndex`) among that sample's calls of the family's every record (`counterpart_records`;
  the variant list passes its unfiltered records, so a counterpart that fails the filters is found):
  every base carried → the other call is read off the call that best carries the weakest base
  (`*_call_other_representation`); some → `*_call_overlaps`. A father-only site that reads the plasma's
  calls is no FF, paternity or fetal-sex evidence (`has_own_plasma_call`), because those calls are sites
  of their own. Indels are not matched.

## Defaults

`NiptQualityThresholds` and the constants of `backend/app/services/nipt_analysis.py`:

| Setting | Default | Used for |
| --- | --- | --- |
| `min_qual`, `min_cf_alt_reads`, `min_vaf`, `max_fs` | 20, 5, 0.01, 20 | quality filter (TLOD, else call QUAL, else site QUAL); presence in the cfDNA (5 alt reads) |
| `vaf_ff_fraction` | 0.25 | quality filter once FF is known: VAF ≥ 0.25 × FF/2 |
| `min_cf_dp` | 20 | FF sites, `low_depth`, `low_depth_dropout` |
| `min_father_dp`, `min_father_alt_reads`, `min_father_qual` | 20, 5, 20 | the father's classes and a usable call |
| `father_het_min_vaf`, `father_hom_alt_min_vaf` | 0.20, 0.80 | the father's classes |
| `MATERNAL_ALLELE_VAF_BOUNDARY` | 0.25 | FF-site ceiling, paternity, the paternal view (midway between FF/2 and 0.5 − FF/2; the R pipeline's 0.35 let shared sites into the FF at a high FF) |
| `min_sites` / `hard_floor` / `max_ci_halfwidth` | 30 / 5 / 0.03 | low-confidence FF / no estimate |
| `OVERDISPERSION`, `MATERNAL_HET_BIAS` | 0.003721, −0.011230 | beta-binomial likelihood; the maternal-het band (categories 2–4) |
| `BACKGROUND_ERROR_RATE` | 0.002 | an untransmitted paternal allele's noise level |
| de novo prior weight, hom-alt transmission prior | 0.02, 0.99 | category 1; `paternal_transmission_probability` |
| `ALLELE_BALANCE_OUTLIER_Z` | 4 | `allele_balance_outlier` |
| `PATERNITY_MIN_EXPECTED_READS` | 10 | paternity evidence |
| `min_separation`, `detect_min`, `ff_too_low` | 0.90, 3, 0.01 | `ambiguous`, category 8 versus `undetectable_at_ff`, withholding the fetal call |

FF = `2 × Σ alt ÷ Σ depth` over the FF sites (autosomal own plasma call, trusted father het or hom-alt,
plasma depth ≥ 20, passing the quality filter, VAF 0.005–0.25), with a Wilson interval scaled by 2;
`ff_median` and `vaf_q05`/`vaf_q95` (the de novo window) come with it.

`_CANDIDATE_PRIORS` holds the categories each trusted father state allows, with Mendelian priors:
`hom_ref` 1, 2, 3, 5; `het` 2–7; `hom_alt` 3, 4, 6, 7; `missing` 1–7.

## The pieces

- **Filter and FF** — `filter_sites_and_estimate_ff`: the quality filter (`quality_failures`, with the
  reasons counted) and the artifact list over the plasma's own calls, father-only sites counted as
  `paternal_only`, then the FF, then the FF-scaled VAF floor.
- **Classification** — `classify_site`: the category, its confidence and runner-up, the flags, and the
  probabilities: `paternal_transmission_probability` (category 7 and absent paternal alleles),
  `_maternal_probabilities` (maternal allele inherited, fetus hom-alt, the fetal genotype posterior)
  for the maternal bands.
- **Fetal sex** — `infer_fetal_sex` (non-PAR chrX sites where the father is hom-alt: ≥ 8 informative,
  a transmitted share ≥ 0.80 female, ≤ 0.10 male), `sex_chromosome_coverage` (the plasma's chrX and
  chrY depth over the autosomes, from the target table) and `combined_fetal_sex`.
- **Paternity** — `paternal_transmission_evidence` counts his hom-alt and het alleles seen and not seen
  where the mother does not carry them and `n × FF/2 ≥ 10`; `sample_integrity_qc.evaluate_paternity`
  turns the rates into a verdict.
- **De novo triage** — `nipt_triage.py`: `is_de_novo_candidate`, `de_novo_window`, `triage_de_novo`
  (the R pipeline's score and priorities; other cfDNA carriers of the assay from
  `count_cfdna_carriers`, `clickhouse_family_variants.py`, over `assay_cfdna_carrier_samples`: a
  call of at least 5 alt reads and 1% of the reads, so crossed-over reads do not count). Unlike the R
  pipeline, a candidate a ClinVar record may call pathogenic (`triage_annotation`,
  `clinvar_may_assert_pathogenic`) is never `excluded_recurrent`: a de novo hotspot recurs between
  pregnancies.
- **Recessive risk** — `nipt_triage.recessive_gene_risks` (CoGA's own): heterozygous carriers only;
  per gene the highest maternal × paternal inheritance product, P(fetus hom-alt) at a shared site, the
  ½ prior for an untold inheritance, notes for a missing population frequency.
- **Target coverage** — `summarize_target_coverage` (weak: mean < 300× or uncovered bases; 1000×
  advisory). The NIPT page's card counts the genes with a weak target; the coverage page lists every
  gene in scope (`include_passing`) and reads one gene's targets on demand
  (`get_family_nipt_gene_targets`).

## Two paths

Both take FF from `filter_sites_and_estimate_ff` over all family records.

- **Summary** — `get_family_nipt_summary` → `run_nipt_analysis` plus the target coverage and the sex
  profile: the FF badge, the funnel, the category counts and the quality panel (`qc` in the summary
  payload, `routers/families_nipt.py::_qc_out`). The Sample QC NIPT checks read the same analysis.
- **Variant list** — `get_family_nipt_variants`: that FF, then the filtered records (at most 5,000, in
  genomic order; one more row is fetched, and past the limit the page sets `total_is_estimated` and
  `count_limit`), classified without the quality filter (failures flagged `quality:<reason>`) and
  skipping artifact-list sites, then `min_confidence`, the view (`inheritance`: `de_novo`,
  `paternal_dominant`, `maternal_dominant`, `recessive_at_risk`, with `include_not_inherited` and
  `de_novo_priority`), the category filter, and paging. The recessive view returns `recessive_genes`.

Endpoints (`routers/families_nipt.py`, under `/api`): `GET /families/{id}/nipt/summary`,
`/nipt/variants`, `/nipt/coverage` (with `targets` from the target table; `all_genes=true` lists the
genes whose targets all pass too, for the coverage page) and `/nipt/coverage/targets?gene=` (one gene's
targets, each with `weak`). The fetal fraction is always CoGA's own estimate; it takes no external
value.

## The artifact list

Admin-only endpoints in `routers/admin.py`, no screen: `GET` / `POST /admin/nipt/artifacts`,
`DELETE /admin/nipt/artifacts/{id}`, `POST /admin/nipt/artifacts/auto-seed` and
`POST /admin/nipt/artifacts/import` (the R pipeline's recurrent table: `parse_artifact_table`,
`import_nipt_artifact_table`); the table is `nipt_artifact_variants` (see [database.md](database.md)).
Each add, update, removal, auto-seed and import is a clinical audit event, with the actor and the
variants, on a chain of its own (`family_identifier = system:nipt-artifacts`);
`GET /admin/integrity/verify?table=clinical_audit_events&family_id=system:nipt-artifacts` checks it.

Auto-seed (`auto_seed_nipt_artifacts` → `fetch_recurrent_small_variant_ids`) counts carriers only among
the samples tagged `assay: nipt_cfdna` whose `assay_panel` resolves to the scope, and drops variants
with `is_gnomad_gt_5_percent` and variants whose pooled `annotation_index.clinvar_terms` pass
`clinvar_may_assert_pathogenic` (`variant_prioritization.py`: a whole-word "pathogenic", or any
conflicting record, because the per-submission `CLNSIGCONF` breakdown is not imported). The import
applies the same two protections through `fetch_artifact_protection_flags`
(`clickhouse_family_variants.py`: a population frequency above 5%, or those ClinVar terms). Both
read the annotation of the families on the assembly, so an allele no family carried is listed
unchecked: each family's analysis checks its listed alleles again with its own annotation
(`nipt_service.artifact_protection`, `family_artifact_ids`; the variant list judges each listed
record in `_classify_candidates`) and keeps a protected one, flagged `artifact_list_protected`.

## Known limits

- The model constants were fitted on six families; the maternal model is weaker on indels (flagged).
- An indel written differently in the two VCFs (trimming, alignment) is not matched; an MNV is
  matched base by base.
- The frequency filters let a variant without a population frequency through (CoGA-wide); an MNV never
  has one. The recessive table notes it.
- The artifact-list protections need annotation: a site with no population frequency or ClinVar
  record is not protected.
- The R recurrent table counts the cfDNA families of the R cohort, the index family included.

These are described for lab users in the in-app reference.

## Where the code lives

- Backend: `backend/app/services/nipt.py` (tags, trio), `nipt_analysis.py` (the model, pure),
  `nipt_triage.py` (de novo triage, recessive risk, pure), `nipt_target_coverage.py` (coverage tables,
  pure), `nipt_service.py` (wiring to family data, the views), `family_package_nipt.py` (the pair
  import), `vcf_call_metrics.py`, `nipt_artifact_pg.py`, `nipt_coverage.py` (the ROI fallback),
  `routers/families_nipt.py`, `schemas/nipt.py`.
- Frontend: `frontend/src/pages/families/FamilyNiptPage.tsx`, `FamilyNiptReportPage.tsx`,
  `FamilyNiptCoveragePage.tsx` (the coverage page, `/families/{id}/nipt/coverage`),
  `NiptQcPanel.tsx`, `NiptRecessiveGenes.tsx`, `NiptTargetCoverage.tsx`, `NiptClassificationBlock.tsx`,
  `niptClassification.ts`; views and presets in `smallVariantSearch.ts` and
  `smallVariantFilterSections.tsx`.
- The synthetic demo family: `demo/nipt_family/` (`scripts/generate_nipt_demo.py`).
- Tests: listed in [testing.md](testing.md).

Related: [haplotype-segregation-analysis.md](haplotype-segregation-analysis.md) (shared pedigree
machinery), [application-scheme.md](application-scheme.md) (storage boundary),
[data-import.md](data-import.md).
