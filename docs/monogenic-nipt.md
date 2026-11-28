# Monogenic NIPT — developer notes

Monogenic NIPT screens a pregnancy for single-gene disorders from maternal-plasma cfDNA and a paternal
sample. CoGA estimates the fetal fraction (FF), assigns each cfDNA variant to one of eight
maternal/fetal categories and gives the fetal-inheritance probabilities. It offers four views (de
novo, paternal, maternal, recessive) and a quality panel. The in-app reference
[`frontend/src/content/docs/monogenic-nipt.md`](../frontend/src/content/docs/monogenic-nipt.md) (shown
at `/docs/reference/monogenic-nipt`) documents the model, the rules and their limits for lab users.
That page is the canonical home; keep it in step with the code. This note covers the implementation.

The model is the lab's R pipeline NIPT-M, version 0.7.3, validated on 22 families with trio exomes
(`NIPT_MODEL_REFERENCE`). CoGA reimplements its frozen constants and rules, not its validation: the
validation against invasive exomes stays in the R project.

## Family model and tags

A NIPT case is a trio backed by two samples (`backend/app/services/nipt.py`, `resolve_nipt_trio`),
one per pregnancy: a family may hold several pregnancies of one woman, each pregnancy's monogenic
NIPT an analysis of its own (the owner's decision of 2026-11-21).

- A monogenic NIPT among the family's analyses (`family.metadata.analyses`) marks the family and
  shows the NIPT page. Its key names its pregnancy (`monogenic_nipt:ZW1`): a package import adds the
  key of its `analysis_type` and `pregnancy_id` (required for a NIPT, [coga-package.md](coga-package.md)),
  beside any other (the conventional NIPT of the same pregnancy, another pregnancy's NIPTs); Family
  Builder sets it with the pregnancy ID it asks for.
- A reading names its pregnancy (`analysis=`) when the family holds the monogenic NIPT of several:
  the NIPT summary, statistics, variants and coverage, the Sample QC and the sign-out read one
  pregnancy's (`nipt_analysis_of`, 422 without one). The pages offer the pregnancy beside the
  family's name, the last imported first. Each pregnancy's calls, coverage tables and ccnv output
  are stored under sources of its own (`family_package_records.suffixed_source`: `nipt_p…`,
  `coverage_table_p…`), the father's among them: an import of one pregnancy never replaces another's.
- The cfDNA sample's assays (`sample.metadata.assays`) hold `"nipt_cfdna"`, and it has the `mother`
  role. Without the tag, the active `mother` member's sample is used.
- Of two samples that could be the plasma (a mother sampled twice, linked as the same individual,
  or the plasma of each pregnancy), the plasma is the one the pregnancy's monogenic NIPT's package
  lists (`package_imports["monogenic_nipt:ZW1"].members`, `analysis_members`), else, without the
  tag, the one that is no conventional NIPT's plasma alone (`cnipt_cfdna`). The father is chosen the same way of two `father` members. When that leaves none
  or several, the trio is not resolved rather than guessed: the NIPT page answers 400 and the signed
  record freezes `NIPT_TRIO_UNRESOLVED`, which also holds the sign-out.
- The capture panel the monogenic NIPT's package names for the cfDNA sample (its `assay_panel`,
  optional; kept as the analysis's in `sample.metadata.assay_panels["monogenic_nipt:ZW1"]`) scopes
  the artifact list. Without it, every NIPT family on the assembly shares the `nipt_cfdna` list. A
  conventional NIPT of the same plasma keeps its own panel beside it, which no artifact list reads.
- The father is the active `father` member. The fetuses are the `fetus` members the pregnancy's
  package lists (`pregnancy_fetuses`): placeholders without data, none when the fetus is purely
  inferred, two or more in a multiple pregnancy. A fetus is a role of its own, no proband and no PGT
  embryo (the owner's decision of 2026-11-21). A NIPT pedigree usually gives the fetus an unknown
  sex and status; in a NIPT package the import reads such a child, with both parents and no role
  given, as a `fetus`. It has no haplotype blocks, and the signed record holds no PGT section for it.
- A maternal plasma and a fetus are one pregnancy's: an import of another pregnancy whose package
  lists one of them is refused before anything is written (`_shared_pregnancy_sample_message`).
- A pregnancy whose package lists two or more fetuses is a multiple pregnancy. Its summary, its
  NIPT report and its signed record carry `multiple_pregnancy_note`: a fetal result may be present
  in one fetus or in more than one, which does not apply to a monozygotic twin; the parents'
  carrier status is read as in a singleton pregnancy. CoGA is not told the zygosity, so the note is
  shown for every multiple pregnancy (the owner's decision of 2026-11-21).

## Input

Two inputs, one model:

- **The pair** (the NIPT-M pipeline's output): a single-sample VCF of the plasma (Mutect2 tumour-only)
  and one of the father's gDNA (GATK HaplotypeCaller), each with a per-target coverage table.
  - `family_package_nipt.py` discovers the pair (`discover_nipt_package_files`), and
    `family_package_datasets.py` imports each VCF into the pregnancy's per-sample small-variant
    callset (`nipt` with the pregnancy's suffix, `NIPT_SMALL_VARIANT_SOURCE`, merged per sample as
    the mtDNA callset is).
  - The plasma goes first, then the father's file through `paternal_record_filter`, which keeps a
    record whose call reaches 15% alt reads, whose caller genotyped it as carrying the allele
    (`is_genotyped_call`), or that lies over a plasma call position (an MNV over any of its bases).
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

Every NIPT view reads the family's analysis (`nipt_service._FamilyAnalysis`): the fetal fraction
off the family's every record (`_load_family_records`), what the artifact list removed and what
stayed in, and what the records give each other's observations (`NiptCounterparts`). The plasma's
Mutect2 callset holds every low-level call, a million rows or more, so:

- The load reads the records' calls without their annotation
  (`_fetch_small_variant_rows(annotated=False)`). The family's alleles on the artifact list, whose
  protection reads their annotation, are annotated apart (`annotate_small_variant_records`). The
  stored details are read 2,000 annotation sets per query (`_ANNOTATION_SET_CHUNK`), so no query
  passes ClickHouse's `max_query_size`.
- The records are observed one at a time (`build_nipt_observations`), and the analysis is kept
  without them. Each worker process keeps its last 8 analyses (`nipt_analysis_cache`), each served
  only for the same analysis (`NiptAnalysisKey`: the family, assembly, projects, trio, fetuses,
  assay and thresholds) from inputs equal to those it was computed from (`NiptAnalysisInputs`): the
  family's small-variant data version, which every write of its variants moves
  (`get_family_small_variant_data_version`), the analysis's scope and samples, the artifact list,
  the QC gate's bound and the per-target coverage, all read again for each request. Concurrent
  requests share one computation; one during which the data version moved is answered but not
  kept.
- The variant list, the report's candidates and the signed record classify their own variants,
  read with their annotation, against the summary's fetal fraction, each observed with the family's
  counterparts. The selected and reported variants are read by their ids (`_classify_selected`).

`build_nipt_observations` (`nipt_service.py`) turns the records into one `NiptSiteObservation` per
allele, one at a time:

- Reads: alt reads = `AD[1]`; depth = the sum of the AD (DP without it), restored over the siblings
  of a record split from a multi-allelic one (`restored_site_depths`); VAF = alt reads ÷ depth, AF
  only without AD.
- The father's class (`derive_father_state`) follows the R pipeline's paternal classes. A call its
  caller genotyped (`is_genotyped_call`: a GQ and no TLOD, as HaplotypeCaller writes) is read from
  its GT: `het`, `hom_alt` (a haploid `1` too), `hom_ref`, or `low_support` below 20 reads, 5 alt
  reads, a QUAL of 50 or a GQ of 20, or without a called genotype. Any other call (Mutect2,
  VarDict) is read off his allele depths: `low_support`, `hom_alt`, `het`, `low_vaf`, `hom_ref`;
  the GT only without AD. `absent` is no call in a per-sample callset, `missing` a joint VCF's
  no-call.
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
  (`_BaseCallIndex`) among that sample's calls of every record of the family (`NiptCounterparts`,
  read from the family's every record by `nipt_counterparts`, so a counterpart that fails the
  variant list's filters is found):
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
| `min_qual`, `min_cf_alt_reads`, `min_vaf`, `max_fs` | 15, 5, 0.01, 20 | quality filter (TLOD, else call QUAL, else site QUAL); presence in the cfDNA (5 alt reads) |
| `vaf_ff_fraction` | 0.25 | quality filter once FF is known: VAF ≥ 0.25 × FF/2 |
| `min_cf_dp` | 20 | FF sites, `low_depth`, `low_depth_dropout` |
| `min_father_dp`, `min_father_alt_reads` | 20, 5 | the father's classes and a usable call |
| `min_father_genotyped_qual`, `min_father_gq` | 50, 20 | a genotyped (HaplotypeCaller) father call, read from its GT |
| `min_father_qual`, `father_het_min_vaf`, `father_hom_alt_min_vaf` | 20, 0.20, 0.80 | any other father call, read from its allele fraction |
| `MATERNAL_ALLELE_VAF_BOUNDARY` | 0.25 | FF-site ceiling, paternity, the paternal view, and the plasma call from which the mother carries an allele in the recessive pairing (midway between FF/2 and 0.5 − FF/2; the R pipeline's 0.35 let shared sites into the FF at a high FF) |
| `min_sites` / `hard_floor` / `max_ci_halfwidth` | 30 / 5 / 0.03 | low-confidence FF / no estimate |
| `OVERDISPERSION`, `MATERNAL_HET_BIAS` | 0.003684, −0.010627 | beta-binomial likelihood; the maternal-het band (categories 2–4) |
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
- **The QC gate** — `nipt_qc_gate.py` (REQ-NIPT-021), the R v0.7.3 review's per-sample rule:
  - The expected fetal reads, median target depth (`target_coverage.median_mean`) × `ff_computed`
    / 2, must reach the error bound. Below it the check `fail`s, and so it does without an estimate,
    a per-target table or an error bound; below a warning bound it `warn`s.
  - The bound is QA's, metric `mnipt.expected_fetal_reads` (source `mnipt`) in the profile
    `nipt_monogenic` (`qc_threshold_service`). `_analyse_family` reads it once per analysis
    (`load_nipt_gate_bound`), and the summary carries it (`NiptFamilySummary.gate_bound`). Until QA
    sets it, `PROVISIONAL_EXPECTED_FETAL_READS` (error 40) applies, with `provisional` true.
  - The same bound gives `fetal_read_depth` (2 × bound / FF). The summary's and the record's target
    coverage count the targets below it (`below_fetal_read_depth`, overall, per gene and over the
    panel). `GET /nipt/coverage`, which runs no analysis, counts them against the depth it is
    given (`fetal_read_depth`): the report passes the summary's; the coverage page none.
  - The summary serves `qc.expected_fetal_reads`, and the record freezes
    `checks.expected_fetal_reads` with the bound it applied.
- **Sign-out** — a NIPT family is signed out through the family sign-out, from the NIPT report or
  the family report page (the NIPT page has none).
  - Its record holds the section `nipt` (`signed_record_nipt.py`): the summary, the checks, each
    reported variant as the variant list classifies it (from `nipt_service.get_family_nipt_record`),
    the recessive fetal status over them, the inputs, and, without a per-target table, the coverage
    check's notes over the coverage tracks (`coverage_notes`, from
    `nipt_service.nipt_coverage_notes`).
  - A failed fetal-fraction, QC-gate, fetal-sex or plasma check gates it under the Sample QC's
    acknowledgement ([clinical-traceability.md](clinical-traceability.md#sign-out)), and so do a
    QC gate on the provisional bound and a section that could not be read (`{"unavailable": …}`:
    the record would verify none of the checks).
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
  pregnancies. ClinVar is the allele's classification in the ClinVar release CoGA loads
  (`nipt_service.clinvar_read`, `clinvar_coga.clinvar_classification`), as everywhere for small
  variants; the callsets' annotated ClinVar is not read, and an allele the release does not hold
  has none. The release is the one the analysis reads (`NiptAnalysisInputs.clinvar_coga`), so a new
  release recomputes the kept analysis; each classified variant carries its value (`clinvar_coga`),
  and the page shows it from that release (see *Two paths*, step 9).
- **Recessive fetal status** — `nipt_triage.recessive_gene_risks` (the R v0.7.3 gene pairing, with
  CoGA's probabilities): heterozygous and homozygous parents (a homozygous one, or a father on his X,
  always passes the allele). The mother carries an allele where the model places it in her band
  (categories 2–6) on a plasma call of `MATERNAL_ALLELE_VAF_BOUNDARY` or more
  (`nipt_triage.mother_carries`, the owner's decision of 2026-11-27): her het allele sits near 50%,
  and a lower call the likelihood places in her band (from about 15% at FF 0.1) is a low-level
  artefact or the fetus's allele. The maternal and recessive views, the locked diagnostic
  candidates and the signed record read the mother's alleles by this one rule. A variant counts in
  its card's gene, as the expanded carrier screen reads it (`nipt_service._recessive_view`:
  `_gene_match_keys`, the gene id and symbol of its primary annotation, joined by `_joined_genes`):
  a gene its annotation names otherwise (up- or downstream) pairs nothing. Each variant is read by
  the model that classified its site (`recessive_mode`), and a gene's alleles once per reading: a
  gene across a pseudo-autosomal boundary is read on either side. Per gene the pair most
  likely to make the fetus affected, with P(affected), P(carrier) and P(unaffected): from the two
  independent inheritances at two sites (`_two_site_pair`), the fetal genotype posterior at a shared
  site (`_shared_site_pair`), or a son's maternal X inheritance alone (`_son_x_pair`, `mode`
  `x_linked_son`, never a carrier). The status (`_pair_status`) counts the inheritances of 0.8 or
  more when both are told outside `UNDETERMINED_BAND` (0.2–0.8): `affected` (both), `carrier` (one),
  `unaffected` (neither). Otherwise it is `carrier_or_unaffected` when one allele was surely not
  inherited (0.2 or less), since the fetus cannot inherit both, and `undetermined` else (an untold
  inheritance is read at the ½ prior). As R reads it, a homozygous father always passes his allele;
  a heterozygous father's allele the mother carries too is not told (`maternal_also_carrier`); a
  shared site with a homozygous mother is undetermined, unless the father is homozygous too. Notes
  for a missing population frequency. The genes are listed by status (affected, undetermined,
  carrier, `carrier_or_unaffected`, unaffected), then by risk (`recessive_listing_order`): a gene
  the cfDNA did not tell comes before a carrier's.
- **X-linked recessive** — on chrX outside the pseudo-autosomal regions, by the fetal sex the
  classification read (`mode`): a son's gene is his mother's allele alone (`x_linked_son`); a
  daughter's is paired as an autosome, her father's X always passed, and one parent's allele alone
  makes her at most a carrier (`x_linked_daughter`, `_one_allele_pair`: her father's always, her
  mother's by its inheritance, `carrier_or_unaffected` when not told). Of a fetus whose sex is not
  known (indeterminate, discordant, or more than one fetus: `model_fetal_sex`) the classification
  reads no chrX outside the PARs, so every allele the mother carries there (a plasma call of
  `MATERNAL_ALLELE_VAF_BOUNDARY` or more) and every hemizygous allele of the father's is listed by
  gene, nothing told or paired, `undetermined` (`x_linked_sex_unknown`, risk none, no pair): a son
  inherits his mother's X alone, a daughter her father's.
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
     the page sets `total_is_estimated` and `count_limit`): the small-variant search's SQL, the
     callset recurrence (`max_callset_families`, the *Recessive (both parents carrier)* preset's 1)
     among its filters, the plasma and father calls of every pregnancy one callset (`_row_callset`);
  3. classified without the quality filter (failures flagged `quality:<reason>`), skipping
     artifact-list sites;
  4. `min_confidence`;
  5. on request (`exclude_cohort_common`, set by the *Recessive (both parents carrier)* preset), the
     locked criteria's cohort rule (`_cohort_common`): an allele without a population frequency that
     `cohort_common_min_samples` (3) families of the assay carry, counted as the report counts them
     (*The locked diagnostic candidates*), is an unannotated common allele and is left out, unless
     ClinVar calls it pathogenic (`clinvar_override`); one common only by the count of a father's
     allele stays, and the recessive view never counts it as the father's (`not_paternal`);
  6. the view (`inheritance`: `de_novo`, `paternal_dominant`, `maternal_dominant`,
     `recessive_at_risk`, with `include_not_inherited` and `de_novo_priority`);
  7. the category filter;
  8. the recurrence filter (`max_unexplained_recurrence`, see *Recurrence in other cfDNA samples*);
  9. paging, and each listed variant's recurrence and payload (`_serve_variant_outs`): the
     small-variant list's, hydrated as there, with its ClinVar per ALT from the release the
     analysis read, its own ClinVar fields the shown ALT's (`clinvar_coga.attach_clinvar_coga`).
     The page names that release (`clinvar_coga_release`), so its ClinVar chip, the export's
     *ClinVar* and *ClinVar stars* columns and the ACMG modal's PP5 / BP6 show the value the
     analysis's decisions read. Without a page size (`page_size=None`) the list holds every
     classified variant: the CSV export.

  The recessive view pairs every carrier variant steps 4 and 5 keep, as the report and the signed
  record pair them, so that a gene's status does not depend on the filters that follow; steps 7 and 8
  then keep the genes with a carrier variant they keep. Its results are genes (`NiptRecessiveGene`): a page holds genes, each with every carrier variant of its own
  (`variant_groups`, `group_type` `nipt_recessive_gene`), `total` counts the genes and `gene_total` and
  `variant_total` the genes and their variants on every page; the export lists their variants gene by
  gene. The NIPT page shows each gene as a card (`SmallVariantGroupCards`, as the expanded carrier
  screen's): the fetal status, which of the variants the fetus inherited (both, one of the two,
  neither; a son's mother's; nothing told of a fetus whose sex is not known), the pair that status
  rests on, and what each parent carries, with each allele's category and the probability that the
  fetus inherited it. The page's *Recessive (both parents
  carrier)* preset reads the parents as the expanded carrier screen reads a couple: the gene panel
  named *BeGECS v3* (every gene without one), HIGH/MODERATE impact, gnomAD AF and popmax at most 5%,
  ClinVar P/LP overriding the impact and frequency cut-offs, ClinVar benign and likely benign left out,
  and the cohort rule (step 5): a frequency ceiling lets an allele without a population frequency
  through, and in a capture assay such an allele is often one every pregnancy carries (an MNV gnomAD
  lists as its SNVs, a homopolymer or repeat-length indel, a paralogue).
  The maternal and recessive views read the parents'
  alleles alone (`_CARRIER_ROW_VIEWS`), so their load (step 2) keeps only the rows such an allele
  can be read from (`CarrierCallRows`, `clickhouse_family_variants._carrier_call_condition`): the
  plasma's call at `MATERNAL_ALLELE_VAF_BOUNDARY` or more over its own allele depths, or without
  them, or an alt call of the father's. A per-sample cfDNA callset is mostly low-level calls of
  neither parent; read whole, they would fill the 5,000 limit early in the genome, and the views
  would never see the rest. The condition keeps every row the views read: a split record's
  fraction over its restored site depth is never above its own, and a father's call read off
  another record (an MNV over SNVs) is read through that record, which holds it
  (`e2e/test_e2e_nipt_carrier_rows.py`). The de novo and paternal views read the low-level calls:
  a fetal allele sits at FF/2. The
  review tags (`review_tag`, `exclude_review_tag`)
  select as on the family small-variant list (`clickhouse_family_variants.resolve_review_selection`:
  a variant's own tags or its compound-het pair's). They go into the load as variant ids, so the
  5,000 limit never cuts a tagged variant off. A selected variant the analysis does not classify
  comes back in `not_classified`, with the reason the signed record gives a reported one
  (`not_in_data`, `artifact_list`, `no_plasma_call`), from the same code (`_classify_selected`,
  which `get_family_nipt_record` uses). `review_tag=report` with no other filter is the page's
  **Show reported**: what a sign-out freezes. The sign-out selects the reported variants by
  the same rule, a variant's own report tag or its compound-het pair's
  (`report_signout_service._reported_reviews`). So a pair tagged `report` is listed,
  counted, frozen and in the record's recessive fetal status alike.

Endpoints (`routers/families_nipt.py`, under `/api`):

- `GET /families/{id}/nipt/summary`;
- `/nipt/variants` (with `trio`: the samples read as the plasma and the father, and the
  placeholders of the pregnancy's fetuses, by which the list's genotypes show each sample's reads
  and leave out the fetuses and another pregnancy's plasma);
- `/nipt/variants/export` (the list as CSV: the same search, read through one dependency,
  `_nipt_variant_search`; the small-variant export's columns but the genotypes, then the NIPT
  classification; the file is named after the pregnancy it reads,
  `family-{family_id}-{pregnancy}-nipt-variants.csv`, and a list cut at the 5,000 limit is named
  `TRUNCATED-partial-search`, reason `candidate-limit`);
- `/nipt/coverage` (with `targets` from the target table; `all_genes=true` also lists the genes
  whose targets all pass, for the coverage page);
- `/nipt/coverage/targets?gene=` (one gene's targets, each with `weak`).

The fetal fraction is always CoGA's own estimate; it takes no external value.

## The diagnostic gene panel

Each monogenic NIPT analysis (one per pregnancy) has a diagnostic gene panel (REQ-NIPT-019), stored in
`family_analysis_gene_panels` (one row per family and analysis key; `nipt_service.nipt_analysis_key`,
the analysis type for a Family Builder family that names no pregnancy). `analysis_gene_panel.py`
reads it (`load_analysis_gene_panel`: the panel's `panel_use_audit.panel_identity`, its genes as
stored and each gene's symbol) and writes it (`set_analysis_gene_panel`): under the family's
audit-chain lock it upserts or deletes the row and records a `diagnostic_panel_set` clinical audit
event with the panel before and after (`AnalysisGenePanel.frozen`: identity plus `genes_digest`);
setting the panel the analysis already has records nothing. `GET`/`PUT
/families/{id}/nipt/diagnostic-panel` (`families_nipt.py`) serve it; the family checkpoint refuses a
reader's `PUT`. `panel_metadata_service.delete_panel_data` refuses (409) a panel that is a
diagnostic panel; the foreign key is `RESTRICT`.

The NIPT page (`NiptDiagnosticPanel.tsx`) sets it and opens on it (else the Mendeliome); the report
uses it when its link names no panel and says whether its scope is it. The sign-out
(`signed_record_nipt.nipt_results`) reads it again and freezes `diagnostic_panel` and
`panel_target_coverage` (`_panel_target_coverage`: `summarize_target_coverage` over the panel's gene
symbols, the plasma's per-target rows carried on `NiptFamilyRecord.plasma_targets`; only the genes
with a weak target or without a target are listed). Without a per-target table the coverage notes
run over the panel's regions (`nipt_panel_target_regions`). `nipt_signout_concerns` adds "NIPT
diagnostic panel" when the section's `diagnostic_panel` is null.

**Capture target.** `build_nipt_observations` sets `NiptSiteObservation.in_target` from the plasma's
per-target coverage table (`_in_capture_target`: the record's REF span overlaps a target, the R
pipeline's rule); None without a table or in a joint VCF. `_is_ff_site` and
`nipt_triage.is_de_novo_candidate` skip `in_target is False`; `classify_site` flags it
`off_target`. Paternity and the fetal sex already count no per-sample site without the plasma's
target depth. The category counts and the funnel still count off-target sites.

## chrX by the fetal sex

`run_nipt_analysis` reads the fetal sex before it classifies (`combined_fetal_sex` of
`infer_fetal_sex` and the plasma's chrY profile, `chry_profile`; `NiptAnalysisResult.fetal_sex_call`).
Every other classification of the family's variants (the variant list, the report's candidates and
the signed record's reported variants) reads the same, beside the summary's FF
(`_FamilyAnalysis.fetal_sex`). `chromosome_model` picks the model: autosomal for an
autosome and for chrX's pseudo-autosomal regions (`_is_nonpar_x`, the conservative bounds of the fetal-sex check); `x_female`
and `x_male` for chrX outside them; none for chrX of a fetus whose sex is not known, chrY and the
mitochondrion; `model_fetal_sex` leaves chrX outside the PARs unclassified in a multiple pregnancy
(the trio's `fetus_sample_ids`), whatever the call. A daughter's chrX is classified as an autosome, a het call of the father there read as
no call (`father_het_call_on_x`). A son's uses `_MALE_X_PRIORS` (1, 2, 4, 6) and the hemizygous
expected fractions (`_expected_vaf(..., hemizygous=True)`: `(1 - FF) / (2 - FF)`, `1 / (2 - FF)`,
`FF / (2 - FF)`), the maternal probabilities of `_male_x_maternal_probabilities`, a paternal
transmission probability of 0, and `paternal_x_allele_in_male_fetus` on a category-1 call his father
also has. `nipt_triage.is_de_novo_candidate` admits a classified chrX site (a son's whatever the
father calls); the diagnostic candidates mark a son's paternal-X call low confidence.

## The locked diagnostic candidates

`nipt_diagnostic_filters.py` holds the R validation's blinded-analysis criteria (REQ-NIPT-020),
frozen with their version (`NiptDiagnosticCriteria`, `NIPT_DIAGNOSTIC_CRITERIA_REFERENCE`): the
rarity ceilings (0.1 % dominant, 1 % recessive; an allele without a frequency that 3 families of the
assay or more carry is not rare, below), the protein effect, ClinVar read strictly (`clinvar_strictly_pathogenic`:
P/LP with no benign or conflicting assertion, stricter than `clinvar_may_assert_pathogenic`) with
its 5 % override for the recessive pairing alone, and the repeat-context indel. ClinVar is the
classification in CoGA's release, never the annotation's: R reads the annotation, so the
candidates differ from R's where the release does, and are validated again against the R pipeline
with it ([TF-10 §8](regulatory/TF-10-performance-evaluation-plan.md)).
`nipt_service.diagnostic_candidates` applies them to classified variants (`diagnostic_annotation`
reads `triage_annotation`, the strict ClinVar call and the recurrence's `carriers`): de novo
candidates in the strict or loose window and at most `MATERNAL_ALLELE_VAF_BOUNDARY`, in no other
cfDNA sample unless ClinVar overrides; the paternal view's inherited alleles; every maternal allele
(`include_not_inherited`, the owner's decision of 2026-11-25: R's candidate table, the per-site
maternal inference not narrowing it); the recessive view over the qualifying alleles. As in R, the
paternal and maternal lists and the mother's alleles of the recessive pairing hold only calls past the
technical filter (`_technically_retained`: no quality failure, inside a capture target); the father's
alleles stay in the pairing with their inheritance not told (`RecessiveCandidate.plasma_call_retained`).
Rarity reads VEP's MAX_AF (`max_af`) beside gnomAD's and TOPMed's (`_POPULATION_AF_KEYS`). `_scoped_diagnostic_candidates` loads a panel's or
gene's variants and classifies them as the variant list does; `GET
/families/{id}/nipt/diagnostic-candidates` (the report, `NiptDiagnosticCandidates.tsx`) and
`get_family_nipt_record` (the record's `diagnostic_candidates`, over the diagnostic panel) both use
it.

**The cohort count** (`cohort_count`, as the R validation counts an unannotated allele). An allele
read off the plasma (a de novo, paternal or maternal candidate, the mother's allele of the recessive
pairing) counts the assay's plasmas that show it: `other_cfdna_carriers`, plus this plasma when its
call carries the allele by the recurrence count's own reading (`_plasma_carries`). The father's
allele of the recessive pairing counts those plasmas or the assay's fathers that carry it, whichever
are more: `other_fathers` (`count_cfdna_recurrence` over `assay_father_samples`, each other family's
active father read by `_father_carries`), plus this father when he carries it. The father's count is
never lower, so an allele can qualify as the mother's and not as the father's (`not_paternal`, a
site both parents carry then paired as the mother's alone), never the other way round. The record
freezes `other_fathers` beside `other_cfdna_carriers` for each candidate.

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
- variants whose ClinVar passes `clinvar_may_assert_pathogenic` (`variant_prioritization.py`: a
  whole-word "pathogenic", or any conflicting record, because the per-submission `CLNSIGCONF`
  breakdown is not imported), read in CoGA's current release (`_clinvar_protected`); an allele the
  release does not hold has no ClinVar.

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
(`clickhouse_family_variants.py`: a population frequency above 5%, or a ClinVar classification in
CoGA's release that may assert pathogenic). The frequency is read from every annotation of the
allele on the assembly, whichever family's import wrote it, from `annotation_index` at the allele's
chromosome and position (the index's sort key), so an allele no family carried has no frequency
to check; its ClinVar is checked in the release all the same. Each family's analysis therefore
checks its listed alleles again (`nipt_service.artifact_protection`: `clinvar` in the release the
analysis read, `common` by its own annotation) and keeps a protected one, flagged
`artifact_list_protected`.

It also keeps a listed allele the family's father carries (`paternal_carrier_protection`, reason
`paternal_carrier`; the R pipeline's v0.7.3 diagnostic rule): his call of it is het or hom-alt by
`derive_father_state`, the plasma has no call of it or one at an allele fraction of at most 0.30
(`PATERNAL_CARRIER_PROTECTION_MAX_VAF`), and the list records it in at most 3 cfDNA samples
(`recurrence_count`, `PATERNAL_CARRIER_PROTECTION_MAX_RECURRENCE`). Such an allele is a founder
allele of his; one in many plasmas is a paralogue or an unannotated common variant and stays an
artefact. `load_nipt_artifacts` reads each listed allele with its count;
`listed_artifact_protection` gives the reason; `family_artifact_ids` judges the family's records,
and the variant list each record it classifies (`_classify_candidates`). The signed record's
`artifact_list.digest` covers each allele with its count, and `kept` names each reason.

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
  het or hom-alt by `derive_father_state`'s classes (20 reads and 5 alt reads; a genotyped call by
  its GT with a QUAL of 50 and a GQ of 20, any other with 20% of the reads and a TLOD, else QUAL,
  of 20): the fetus's paternal allele;
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

- The model constants were fitted on the 22 families the R pipeline also chose its filter on (an
  in-sample validation); the maternal model is weaker on indels (flagged).
- An indel written differently in the two VCFs (trimming, alignment) is not matched; an MNV is
  matched base by base.
- The frequency filters let a variant without a population frequency through (CoGA-wide); an MNV
  never has one. The recessive gene card notes it.
- The artifact-list protections need a population frequency in the annotation or a ClinVar record
  in CoGA's release: a site with neither is not protected.
- The R validation counts the families of its cohort; CoGA counts those of the assay, in every
  project, so the count grows as pregnancies are imported.
- The recurrence count does not look up a father's call in another representation of the allele (an
  MNV over it, or its SNVs), so such a paternal allele counts as unexplained. `other_samples` counts
  the samples with any call on the assembly, not those that covered the site.

The in-app reference describes these for lab users.

## Where the code lives

- Backend: `backend/app/services/nipt.py` (tags, trio), `nipt_analysis.py` (the model, pure),
  `nipt_triage.py` (de novo triage, recessive risk, pure), `nipt_target_coverage.py` (coverage
  tables, pure), `nipt_service.py` (wiring to family data, the views), `nipt_quality_checks.py` (the
  checks' verdicts, pure), `nipt_qc_gate.py` (the QC gate and its bound), `signed_record_nipt.py` (the signed record's NIPT section),
  `family_package_nipt.py` (the pair import), `vcf_call_metrics.py`, `nipt_artifact_pg.py`,
  `nipt_coverage.py` (the ROI fallback), `coverage_read_depth.py` (the coverage track the fallback
  reads as depth), `nipt_variant_statistics.py` (the variant summary's NIPT-M tab, pure),
  `routers/families_nipt.py`, `schemas/nipt.py`.
- Frontend: `frontend/src/pages/families/FamilyNiptPage.tsx`, `FamilyNiptReportPage.tsx`,
  `FamilyNiptCoveragePage.tsx` (the coverage page, `/families/{id}/nipt/coverage`),
  `NiptQcPanel.tsx`, `NiptRecessiveGeneView.tsx` (the recessive gene cards), `NiptTargetCoverage.tsx`,
  `NiptClassificationBlock.tsx`, `niptClassification.ts`, `VariantSummaryNipt.tsx` (the variant
  summary's NIPT-M tab); views and presets in
  `smallVariantSearch.ts` and `smallVariantFilterSections.tsx`.
- The synthetic demo family: `demo/nipt_family/`, a package folder delivered as a pipeline delivers one (`scripts/generate_nipt_demo.py`).
- Tests: listed in [testing.md](testing.md).

Related: [haplotype-segregation-analysis.md](haplotype-segregation-analysis.md) (shared pedigree
machinery), [application-scheme.md](application-scheme.md) (storage boundary),
[data-import.md](data-import.md).
