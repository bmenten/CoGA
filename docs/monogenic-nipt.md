# Monogenic NIPT — developer notes

Monogenic NIPT screens a pregnancy for single-gene disorders from maternal-plasma cfDNA and a paternal
sample. CoGA estimates the fetal fraction (FF), assigns each cfDNA variant to one of eight
maternal/fetal categories and gives the fetal-inheritance probabilities. It offers four views (de
novo, paternal, maternal, recessive) and a quality panel. The in-app reference
[`frontend/src/content/docs/monogenic-nipt.md`](../frontend/src/content/docs/monogenic-nipt.md) (shown
at `/docs/reference/monogenic-nipt`) documents the model, the rules and their limits for lab users.
That page is the canonical home; keep it in step with the code. This note covers the implementation.

The model is the lab's R pipeline NIPT-M, version 0.5.1, validated on six families with trio exomes
(`NIPT_MODEL_REFERENCE`). CoGA reimplements its frozen constants and rules, not its validation: the
validation against invasive exomes stays in the R project.

## Family model and tags

A NIPT case is a trio backed by two samples (`backend/app/services/nipt.py`, `resolve_nipt_trio`):

- `"monogenic_nipt"` among the family's analysis types (`family.metadata.analysis_types`) marks the
  family and shows the NIPT page. Family Builder sets it; a package import adds its `analysis_type`
  to the list, beside any other (a conventional NIPT of the same pregnancy).
- The cfDNA sample's assays (`sample.metadata.assays`) hold `"nipt_cfdna"`, and it has the `mother`
  role. Without the tag, the active `mother` member's sample is used.
- Of two samples that could be the plasma (a mother sampled twice, linked as the same individual),
  the plasma is the one the monogenic NIPT's package lists (`package_imports.monogenic_nipt.members`,
  `analysis_members`), else, without the tag, the one that is no conventional NIPT's plasma alone
  (`cnipt_cfdna`). The father is chosen the same way of two `father` members. When that leaves none
  or several, the trio is not resolved rather than guessed: the NIPT page answers 400 and the signed
  record freezes `NIPT_TRIO_UNRESOLVED`, which also holds the sign-out.
- The capture panel the monogenic NIPT's package names for the cfDNA sample (its `assay_panel`,
  optional; kept as the analysis's in `sample.metadata.assay_panels.monogenic_nipt`) scopes the
  artifact list. Without it, every NIPT family on the assembly shares the `nipt_cfdna` list. A
  conventional NIPT of the same plasma keeps its own panel beside it, which no artifact list reads.
- The father is the active `father` member. The fetus (`embryo`, else `proband`) is an optional
  placeholder without data. A NIPT pedigree usually gives the fetus an unknown sex and status; the
  import reads such a child, with both parents and no role given, as an `embryo`. It has no
  haplotype blocks, so the signed record holds no PGT section for it: `pgt_analysis` only where an
  embryo has blocks of its own.

## Input

Two inputs, one model:

- **The pair** (the NIPT-M pipeline's output): a single-sample VCF of the plasma (Mutect2 tumour-only)
  and one of the father, each with a per-target coverage table.
  - `family_package_nipt.py` discovers the pair (`discover_nipt_package_files`), and
    `family_package_datasets.py` imports each VCF into the per-sample small-variant callset `nipt`
    (`NIPT_SMALL_VARIANT_SOURCE`, merged per sample as the mtDNA callset is).
  - The plasma goes first, then the father's file through `paternal_record_filter`, which keeps a
    record whose call reaches 15% alt reads or that lies over a plasma call position (an MNV over
    any of its bases).
  - A one-sample VCF's FILTER and caller metrics go with the call (`calls.filters`, `calls.metrics`;
    `vcf_call_metrics.py`).
  - The coverage tables become `target_coverage` interval tracks (`nipt_target_coverage.py`:
    value = mean, record_id = gene, the rest in `metadata_json`).
  - Each file's one sample column is read as its entry's sample, whatever it is named: the
    laboratory maps its verified tubes to files named after other tubes. A column named neither as
    the member nor as the entry's `vcf_sample` gives a warning in Discover, the dry run and the
    import, never a refusal (`nipt_vcf_column_warning`, code `nipt_vcf_column_named_otherwise`; the
    owner's decision of 2026-10-28). The dataset's summary records each sample's column
    (`vcf_column`).
- **One combined VCF** with a father and a cfDNA column, through the family small-variant upload or a
  package import, stored as the callset of its caller (`mutect2`, as the header or the package's
  `source_format` names it; [data-import.md](data-import.md#small-variant-callsets)). Its quality is
  the site's QUAL (`entries.qual`).

`build_nipt_observations` (`nipt_service.py`) turns the records into one `NiptSiteObservation` per
allele:

- Reads: alt reads = `AD[1]`; depth = the sum of the AD (DP without it), restored over the siblings
  of a record split from a multi-allelic one (`restored_site_depths`); VAF = alt reads ÷ depth, AF
  only without AD.
- The father's class (`derive_father_state`) is read off his allele depths, as in the R pipeline's
  paternal classes: `low_support`, `hom_alt`, `het`, `low_vaf`, `hom_ref`; `absent` for no call in a
  per-sample callset, `missing` for a joint VCF's no-call; the GT only without AD.
  `trusted_father_state` (`nipt_analysis.py`) maps them onto what the candidates read: `absent` is
  `hom_ref` unless his depth there is below `min_father_dp`; `low_vaf`, `low_support` and a thin
  call are `missing`.
- A per-sample record with only the father's call is kept. The plasma's depth there comes from its
  per-target coverage (`TargetDepthLookup`: the lower of the target's mean and median), marked
  `cf_depth_estimated`. Every per-sample site, called by the plasma or not, carries that coverage
  depth as `cf_coverage_dp`; a joint VCF's site carries its plasma call's depth. A record with only
  the plasma's call takes the father's depth from his table.
- **Representations.** Mutect2 writes adjacent phased SNVs as one MNV, so the two VCFs can hold one
  allele differently. A per-sample record missing the other sample's call looks it up base by base
  (`_BaseCallIndex`) among that sample's calls of every record of the family (`counterpart_records`;
  the variant list passes its unfiltered records, so a counterpart that fails the filters is found):
  - every base carried → the other call is read off the call that best carries the weakest base
    (`*_call_other_representation`);
  - some bases carried → `*_call_overlaps`.

  A father-only site that reads the plasma's calls is no FF, paternity or fetal-sex evidence
  (`has_own_plasma_call`), because those calls are sites of their own. Indels are not matched.

  The variant list names the record a whole call was read off (`represented_by` on the site;
  `father_call_variant_id`, `cf_call_variant_id` on the classification), so the genotypes can show it
  where the variant's own record holds none (`frontend/src/pages/families/niptGenotypes.ts`).

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

FF = `2 × Σ alt ÷ Σ depth` over the FF sites, with a Wilson interval scaled by 2. An FF site is
autosomal, with its own plasma call, a trusted father het or hom-alt, plasma depth ≥ 20, passing the
quality filter, and VAF 0.005–0.25. `ff_median` and `vaf_q05`/`vaf_q95` (the de novo window) come with
it.

`_CANDIDATE_PRIORS` holds the categories each trusted father state allows, with Mendelian priors:
`hom_ref` 1, 2, 3, 5; `het` 2–7; `hom_alt` 3, 4, 6, 7; `missing` 1–7.

## The pieces

- **Filter and FF** — `filter_sites_and_estimate_ff`: the quality filter (`quality_failures`, with
  the reasons counted) and the artifact list over the plasma's own calls, father-only sites counted
  as `paternal_only`; then the FF; then the FF-scaled VAF floor.
- **Classification** — `classify_site`: the category, its confidence and runner-up, the flags, and
  the probabilities: `paternal_transmission_probability` (category 7 and absent paternal alleles),
  and `_maternal_probabilities` for the maternal bands (maternal allele inherited, fetus hom-alt,
  the fetal genotype posterior).
- **Fetal sex** — `infer_fetal_sex` (non-PAR chrX sites where the father is hom-alt and
  `cf_coverage_dp × FF/2 ≥ 3`, admitted like paternity's: ≥ 8 informative; a transmitted share ≥ 0.80
  is female, ≤ 0.10 male), `sex_chromosome_coverage` (the
  plasma's chrX and chrY depth over the autosomes, from the target table) and `combined_fetal_sex`.
- **Paternity** — `paternal_transmission_evidence` counts his hom-alt and het alleles seen and not
  seen, where the mother does not carry them and `cf_coverage_dp × FF/2 ≥ 10`. That depth does not
  depend on whether the plasma called the allele, so a seen and a missed allele are admitted alike;
  on the call's own depth only the seen ones would be, and every rate would read near 100%. A site
  without it counts in `without_plasma_depth`, which an unverifiable verdict names.
  `sample_integrity_qc.evaluate_paternity` turns the rates into a verdict, worded by the side they
  miss on: too few seen (another father) or too many het ones (his own DNA in the plasma).
- **The verdicts** — `nipt_quality_checks.py` makes every verdict of the page's quality checks,
  once: the fetal fraction (`fail` without an estimate, `warn` low-confidence), the fetal sex (`fail`
  when the two signals disagree, `warn` when neither tells), paternity and the plasma sample's sex
  profile. The summary endpoint serves them (`fetal_fraction_status`, `fetal_sex.status`), and the
  signed record freezes them.
- **Sign-out** — a NIPT family is signed out through the family sign-out, from the NIPT report or
  the family report page (the NIPT page has none).
  - Its record holds the section `nipt` (`signed_record_nipt.py`): the summary, the checks, each
    reported variant as the variant list classifies it (from `nipt_service.get_family_nipt_record`),
    the recessive fetal risk over them, the inputs, and, without a per-target table, the coverage
    check's notes over the coverage tracks (`coverage_notes`, from
    `nipt_service.nipt_coverage_notes`).
  - A failed fetal-fraction, fetal-sex or plasma check gates it under the Sample QC's
    acknowledgement ([clinical-traceability.md](clinical-traceability.md#sign-out)).
  - The sign-out runs the analysis once: its Sample QC and the section read the same run
    (`SharedNiptAnalysis`). The Sample QC runs its NIPT checks in a savepoint, so a database error
    there is noted and gated as a check that could not run, and the rest of the sign-out keeps
    running.
- **Variant summary** — `nipt_variant_statistics.py` groups one analysis run for the NIPT-M tab of
  the family's *Variant summary* (`GET /families/{id}/nipt/statistics`). Descriptive: the signed
  record does not hold it.
  - The run keeps its sites in the order of their classifications (`NiptAnalysisResult.sites`) and
    the plasma calls' own FILTER (`caller_filters`: PASS, or each other value), which no rule reads.
  - **Per group.** Each category is a mother × fetus group, and so are the father's het alleles the
    fetus did not inherit (no category, and not merely undetectable at the FF). Each group shows the
    expected plasma VAF (`expected_plasma_vaf`) beside the observed median, the confidence, the
    father's trusted genotype, the variant class and a VAF histogram in bins of 0.01. The page draws
    the allele fractions per group (each row on its own scale) or stacked, with a hom/hom peak above
    1.5 times the tallest other bin cut.
  - **Per autosome.** The paternity check's counts (`paternal_transmission_evidence` per
    chromosome), the mother's het alleles inherited beside a confident hom-ref or hom-alt father
    (`maternal_allele_probability` ≥ 0.5, not ambiguous), and the fetus's hom-alt sites. An
    autosome where none of a parent's alleles reached the fetus over at least 20 informative sites
    is marked (`CHROMOSOME_MARK_MIN_SITES`, the owner's choice of 2026-11-09; the minimum is QA's to
    set): a hint of UPD or a deletion, not a call.
  - **The artifact list** (`get_family_nipt_summary_with_artifacts`): the alleles the assay's list
    holds, the family's alleles among them, those removed and those kept in by protection. The
    funnel's `failed_artifact` counts only the removed plasma calls that passed the quality filter.
  - The small-variant statistics name the family's plasma and father (`nipt_samples`), whose GT is
    not a genotype, and the page leaves their het/hom counts out.
- **De novo triage** — `nipt_triage.py`: `is_de_novo_candidate`, `de_novo_window`, `triage_de_novo`
  (the R pipeline's score and priorities). Other cfDNA carriers of the assay are the candidate's
  recurrence `carriers` (see *Recurrence in other cfDNA samples*): a call of at least 5 alt reads and
  1% of the reads, so crossed-over reads do not count. Unlike the R
  pipeline, a candidate a ClinVar record may call pathogenic (`triage_annotation`,
  `clinvar_may_assert_pathogenic`) is never `excluded_recurrent`: a de novo hotspot recurs between
  pregnancies.
- **Recessive risk** — `nipt_triage.recessive_gene_risks` (CoGA's own): heterozygous carriers only;
  per gene the highest maternal × paternal inheritance product; P(fetus hom-alt) at a shared site;
  the ½ prior for an untold inheritance; notes for a missing population frequency.
- **Target coverage** — `summarize_target_coverage` (weak: mean < 300× or uncovered bases; 1000×
  advisory). The NIPT page's card counts the genes with a weak target. The coverage page lists every
  gene in scope (`include_passing`) and reads one gene's targets on demand
  (`get_family_nipt_gene_targets`).
  Without the plasma's table, `nipt_coverage.py` takes the median depth over the genes or the family
  ROI from the plasma's one coverage track of read depth (`_load_coverage_intervals`,
  `coverage_read_depth.py`). That is its coverage BED when declared as read depth (the upload's or
  the manifest entry's `content: read_depth`), otherwise HiFiCNV's depth stored as read.
  Never read as depth:
  - a log2-ratio track (WisecondorX's or QDNAseq's bins, HiFiCNV's normalised depth, a BED declared
    as a log2 ratio): merged with the BED, it would pull the median down, and its rows would cover the BED's
    gaps;
  - a coverage BED that declares nothing (`unknown`).

  Each track left out is named, with the reason, in the check's `coverage_notes`, which the NIPT
  page, the coverage page and the printed report show. Without a depth track every region reads
  `no_coverage`. A sign-out freezes the notes word for word in `nipt.coverage_notes`, with what the
  check ran for: the sign-out is not told the page's search, so it runs the check without a gene or
  panel, over the family ROI.

## Two paths

Both take FF from `filter_sites_and_estimate_ff` over all family records.

- **Summary** — `get_family_nipt_summary` → `run_nipt_analysis`, plus the target coverage and the sex
  profile: the FF badge, the funnel, the category counts and the quality panel (`qc` in the summary
  payload, `routers/families_nipt.py::_qc_out`). The Sample QC NIPT checks read the same analysis.
- **Variant list** — `get_family_nipt_variants`, in order:
  1. that FF;
  2. the filtered records, at most 5,000, in genomic order (one more row is fetched; past the limit
     the page sets `total_is_estimated` and `count_limit`);
  3. classified without the quality filter (failures flagged `quality:<reason>`), skipping
     artifact-list sites;
  4. `min_confidence`;
  5. the view (`inheritance`: `de_novo`, `paternal_dominant`, `maternal_dominant`,
     `recessive_at_risk`, with `include_not_inherited` and `de_novo_priority`);
  6. the category filter;
  7. the recurrence filter (`max_unexplained_recurrence`, see *Recurrence in other cfDNA samples*);
  8. paging, and each listed variant's recurrence. Without a page size (`page_size=None`) the
     list holds every classified variant: the CSV export.

  The recessive view returns `recessive_genes`. The review tags (`review_tag`, `exclude_review_tag`)
  select as on the family small-variant list (`clickhouse_family_variants.resolve_review_selection`:
  a variant's own tags or its compound-het pair's). They go into the load as variant ids, so the
  5,000 limit never cuts a tagged variant off. A selected variant the analysis does not classify
  comes back in `not_classified`, with the reason the signed record gives a reported one
  (`not_in_data`, `artifact_list`, `no_plasma_call`), from the same code (`_classify_selected`,
  which `get_family_nipt_record` uses). `review_tag=report` with no other filter is the page's
  **Show reported**: what a sign-out freezes. The sign-out selects the reported variants by
  the same rule, a variant's own report tag or its compound-het pair's
  (`report_signout_service._reported_reviews`). So a pair tagged `report` is listed,
  counted, frozen and in the record's recessive fetal risk alike.

Endpoints (`routers/families_nipt.py`, under `/api`):

- `GET /families/{id}/nipt/summary`;
- `/nipt/variants` (with `trio`: the samples read as the plasma and the father, and the fetus's
  placeholder, by which the list's genotypes show each sample's reads);
- `/nipt/variants/export` (the list as CSV: the same search, read through one dependency,
  `_nipt_variant_search`; the small-variant export's columns but the genotypes, then the NIPT
  classification; a list cut at the 5,000 limit is named `TRUNCATED-partial-search`, reason
  `candidate-limit`);
- `/nipt/coverage` (with `targets` from the target table; `all_genes=true` also lists the genes
  whose targets all pass, for the coverage page);
- `/nipt/coverage/targets?gene=` (one gene's targets, each with `weak`).

The fetal fraction is always CoGA's own estimate; it takes no external value.

## The artifact list

The admin page *NIPT artefact list* (`AdminNiptArtifactsPage.tsx`, `/admin/variants/nipt-artifacts`,
an admin's alone) manages it through admin-only endpoints in `routers/admin.py`:

- `GET /admin/nipt/artifacts/scopes?assembly_id=` (`list_nipt_artifact_scopes`): the scopes to choose
  from, the default `nipt_cfdna`, each scope a cfDNA sample's monogenic NIPT panel resolves to and each scope
  the assembly's list holds entries for, each with its cfDNA samples (in every project) and entries;
- `GET` / `POST /admin/nipt/artifacts` and `DELETE /admin/nipt/artifacts/{id}`: the entries, an entry
  added or updated, an entry removed (no reason: the audit event holds the entry as it was);
- `GET /admin/nipt/artifacts/auto-seed/preview` and `POST /admin/nipt/artifacts/auto-seed` (below);
- `POST /admin/nipt/artifacts/import`: the R pipeline's recurrent table (`parse_artifact_table`,
  `import_nipt_artifact_table`).

The page asks a confirmation before each write. It reads a typed allele (`7-1000-A-G`,
`chr7:1000:A:G`, `chr7:1000 A>G`) into the stored form (`build_small_variant_id`:
`parseArtifactVariant`); the endpoint stores `variant_id` as given. An allele already listed keeps its
recurrence count when it is added again as curated. The table is `nipt_artifact_variants` (see
[database.md](database.md)). Each add, update, removal, auto-seed and import is a clinical audit
event, with the actor and the variants, on a chain of its own
(`family_identifier = system:nipt-artifacts`);
`GET /admin/integrity/verify?table=clinical_audit_events&family_id=system:nipt-artifacts` checks it.

Auto-seed (`auto_seed_nipt_artifacts` → `fetch_recurrent_artifact_candidates`) reads the calls of
the samples tagged `assay: nipt_cfdna` whose monogenic NIPT panel resolves to the scope
(`assay_cfdna_carrier_samples`), in every family and project.

- It counts the mark of a recurrent artefact, not the carriers: an allele is listed when it is an
  unexplained low-VAF call in at least `min_unexplained_samples` of them (3 by default, for a start
  with about 20 families), and that count is its `recurrence_count`.
- Each call is read as the list's recurrence count reads it (`_cfdna_sample_calls`, by
  `recurrence_rules` with the default thresholds; see
  [Recurrence in other cfDNA samples](#recurrence-in-other-cfdna-samples)): a call at 0.25 or more
  (maternal), or a lower one the family's father explains (paternal), does not count.
- Why not every carrier: such a count would reach any threshold for each 1–5% polymorphism once the
  database holds about a hundred plasmas, and list the paternal sites the fetal fraction is read
  from.

It drops:

- variants with `is_gnomad_gt_5_percent`;
- variants whose pooled `annotation_index.clinvar_terms` pass `clinvar_may_assert_pathogenic`
  (`variant_prioritization.py`: a whole-word "pathogenic", or any conflicting record, because the
  per-submission `CLNSIGCONF` breakdown is not imported).

The preview (`preview_auto_seed_nipt_artifacts`) reads what the seed would write now, by the same
reading (`_auto_seed_candidates`: the same samples, rules, threshold and protections), and writes
nothing: no entry, no audit event. Each allele carries `listed_source`, the source of its entry already
on the list (the seed refreshes that entry's count and keeps its source and label), or `null` for an
allele the seed adds. The seed answers the same reading with the number it wrote (`seeded`), so the
page shows what it wrote; it reads the samples again, so an import between the preview and the seed
shows in its answer. The audit event `nipt_artifacts_auto_seeded` names each seeded allele with its
count, the threshold and the rules the calls were read by. A family without a father's sample, or whose father's call
fails the quality floors, cannot explain its paternal alleles, so in a large cohort of such families
a polymorphism can still reach the threshold: review the auto entries.

The import applies the same two protections through `fetch_artifact_protection_flags`
(`clickhouse_family_variants.py`: a population frequency above 5%, or those ClinVar terms). Both read
every annotation of the allele on the assembly, whichever family's import wrote it, from
`annotation_index` at the allele's chromosome and position (the index's sort key). So an allele no
family carried is listed unchecked.
Each family's analysis therefore checks its listed alleles again with its own annotation
(`nipt_service.artifact_protection`, `family_artifact_ids`; the variant list judges each listed
record in `_classify_candidates`) and keeps a protected one, flagged `artifact_list_protected`.

## Recurrence in other cfDNA samples

Recurrent artefacts sit at a low allele fraction in many plasmas. Every listed variant carries its
recurrence in the other cfDNA samples of the family's assay, every project's
(`nipt_service.NiptRecurrence`, served as `nipt.recurrence`): counts only, never which family.
`count_cfdna_recurrence` (`clickhouse_family_variants.py`) reads them over
`assay_cfdna_carrier_samples`, outside the family, by `recurrence_rules`:

- `carriers`: the samples with a call of the allele of at least 5 alt reads and 1% of the reads (the
  quality filter's floors), or, without allele depths, an alt genotype;
- `maternal`: those at an allele fraction of 0.25 or more (`MATERNAL_ALLELE_VAF_BOUNDARY`): that
  mother carries the allele;
- `paternal_low_vaf`: those lower where another sample of that family's row, the father, calls it
  het or hom-alt by `derive_father_state`'s classes (20 reads, 5 alt reads, 20% of the reads, a
  TLOD, else QUAL, of 20): the fetus's paternal allele;
- `unexplained_low_vaf`: those lower that no parent explains, with their median fraction: the mark
  of a recurrent artefact. A polymorphism scores about 0 here, however many pregnancies carry it;
- `other_samples` (`count_other_cfdna_samples`): the cfDNA samples of the assay outside the family
  with a call on the assembly, from `family_sample_variant_summary`, which every import refreshes.

A family stored under two projects counts once. The page slice is counted in one query; with the
filter, every classified variant is counted once before the page is cut.

**The recurrence filter** (`max_unexplained_recurrence`, `hidden_as_recurrent`) leaves out a variant
that is an unexplained low-VAF call in more than that many other samples, unless
`artifact_protection` keeps it: a ClinVar record that may call it pathogenic (a de novo hotspot recurs
between pregnancies) or a population frequency above 5%. `recurrence.protection` says which. It
narrows the list as the category filter does; the analysis, its FF and its funnel are the artifact
list's alone, so the filter never changes a classification.

**The signed record** holds each reported variant's recurrence (`signed_record_nipt._recurrence`):
the counts, the median and the protection, not `other_samples`, so that a pregnancy imported since
changes the record only when it carries a reported allele. `get_family_nipt_record` counts every
reported variant once; the de novo triage reads that count.

## Known limits

- The model constants were fitted on six families; the maternal model is weaker on indels (flagged).
- An indel written differently in the two VCFs (trimming, alignment) is not matched; an MNV is
  matched base by base.
- The frequency filters let a variant without a population frequency through (CoGA-wide); an MNV
  never has one. The recessive table notes it.
- The artifact-list protections need annotation: a site with no population frequency or ClinVar
  record is not protected.
- The R recurrent table counts the cfDNA families of the R cohort, the index family included.
- The recurrence count does not look up a father's call in another representation of the allele (an
  MNV over it, or its SNVs), so such a paternal allele counts as unexplained. `other_samples` counts
  the samples with any call on the assembly, not those that covered the site.

The in-app reference describes these for lab users.

## Where the code lives

- Backend: `backend/app/services/nipt.py` (tags, trio), `nipt_analysis.py` (the model, pure),
  `nipt_triage.py` (de novo triage, recessive risk, pure), `nipt_target_coverage.py` (coverage
  tables, pure), `nipt_service.py` (wiring to family data, the views), `nipt_quality_checks.py` (the
  checks' verdicts, pure), `signed_record_nipt.py` (the signed record's NIPT section),
  `family_package_nipt.py` (the pair import), `vcf_call_metrics.py`, `nipt_artifact_pg.py`,
  `nipt_coverage.py` (the ROI fallback), `coverage_read_depth.py` (the coverage track the fallback
  reads as depth), `nipt_variant_statistics.py` (the variant summary's NIPT-M tab, pure),
  `routers/families_nipt.py`, `schemas/nipt.py`.
- Frontend: `frontend/src/pages/families/FamilyNiptPage.tsx`, `FamilyNiptReportPage.tsx`,
  `FamilyNiptCoveragePage.tsx` (the coverage page, `/families/{id}/nipt/coverage`),
  `NiptQcPanel.tsx`, `NiptRecessiveGenes.tsx`, `NiptTargetCoverage.tsx`,
  `NiptClassificationBlock.tsx`, `niptClassification.ts`, `VariantSummaryNipt.tsx` (the variant
  summary's NIPT-M tab); views and presets in
  `smallVariantSearch.ts` and `smallVariantFilterSections.tsx`.
- The synthetic demo family: `demo/nipt_family/`, a package folder delivered as a pipeline delivers one (`scripts/generate_nipt_demo.py`).
- Tests: listed in [testing.md](testing.md).

Related: [haplotype-segregation-analysis.md](haplotype-segregation-analysis.md) (shared pedigree
machinery), [application-scheme.md](application-scheme.md) (storage boundary),
[data-import.md](data-import.md).
