# Semi-automatic ACMG classification — developer notes

CoGA pre-evaluates the ACMG/AMP 2015 criteria for small variants and the ClinGen / ACMG criteria for
copy-number variants, and scores them on a points scale. The design stance:

> **Decision support, not an autoclassifier — every criterion is overridable.** Suggestions are
> pre-set from the data; the analyst confirms strengths, adds a rationale note and saves. The class
> and points are **recomputed on the server** on save, so a stored classification never depends on
> the browser.

The clinical rules (points and class bands, VUS tiers, every pre-evaluation and exclusion rule, the
mtDNA rule set and the CNV classifier) are documented for lab users in the in-app reference
[`frontend/src/content/docs/acmg-classification.md`](../frontend/src/content/docs/acmg-classification.md)
(at `/docs/reference/acmg-classification`). That page is canonical; keep it in step with the code.
This note covers the implementation.

## Where the code lives

| Part | Files |
| --- | --- |
| Small-variant engine (pure, unit-tested) | `frontend/src/lib/acmg/` — `criteria.ts` (criterion catalogue, `EXCLUSIVE_CRITERIA`, `CAPPED_CRITERIA`), `evaluate.ts` (nuclear pre-evaluation, the de novo points), `evaluateMito.ts` (mtDNA rules), `pedigree.ts` (the proband's parents from the pedigree links, their parentage from the Sample QC), `score.ts` (points, class, VUS tier), `index.ts` (initial selections, one criterion of each exclusive pair, `fitCappedCriteria`, `criteriaBlocking`); `frontend/src/lib/clinvar.ts` (the ClinVar reading for PP5 / BP6) |
| Small-variant dialog | `frontend/src/pages/families/AcmgClassificationModal.tsx`, `AcmgEvidencePanel.tsx`, `AcmgScaleBar.tsx`; `PredictorMark.tsx` and `inSilicoTones.ts` (the in silico dots it shares with the variant card) |
| Small-variant server scoring | `backend/app/services/acmg_points.py` (codes, strengths, the strengths each criterion takes, `EXCLUSIVE_CRITERIA`, `CAPPED_CRITERIA`, class bands, `vus_tier_for_points`), `small_variant_review_acmg.py` (payload normalisation, evidence snapshot) |
| The gene's phenotype score in a family | `GET /families/{family_id}/gene-phenotype-score?symbol=` (`family_gene_phenotype_score` in `clickhouse_family_variants.py`): the score the phenotype-prioritised ranking gives the gene, for a variant no ranking scored |
| CNV engine | `frontend/src/lib/cnvAcmg/` (criteria for loss and gain, auto-suggestions, scorer) |
| CNV dialog | `frontend/src/pages/families/CnvAcmgClassificationModal.tsx`, `CnvScaleBar.tsx` |
| CNV server scoring | `backend/app/services/cnv_acmg_points.py` (criterion catalogues with allowed point ranges, clamping, class bands), `structural_variant_review_pg.py` |

The frontend and backend scorers must agree. Each side has its own tests (listed in
[testing.md](testing.md)); change both together. The criterion tables exist twice: the codes,
directions, points, `allowedStrengths` and exclusive pairs of `lib/acmg/criteria.ts` are mirrored in
`acmg_points.py` (`CRITERION_DIRECTION`, `STRENGTH_POINTS`, `ALLOWED_STRENGTHS`,
`EXCLUSIVE_CRITERIA`, `CAPPED_CRITERIA`), and the loss and
gain catalogues of `lib/cnvAcmg/criteria.ts` in `cnv_acmg_points.py`.
`backend/tests/test_acmg_frontend_parity.py` reads the two TypeScript files and fails when a table
differs.

## Persistence

- Small variants: `small_variant_reviews.acmg` (JSONB, the per-criterion record), `acmg_point_total`,
  `acmg_class` and `acmg_evidence_snapshot` (baseline `backend/db/schema/postgres/03_assay.sql`). The
  save reuses `PUT /families/{family_id}/small-variants/{variant_id}/review`. The server refuses
  (`400`):
  - an unknown criterion or strength;
  - a criterion listed twice (every entry is scored, so PVS1 sent twice would score +16);
  - a strength the criterion does not take (`ALLOWED_STRENGTHS`, the dialog's `allowedStrengths`:
    BS1 at very strong would score −8);
  - two accepted criteria of one exclusive pair (`EXCLUSIVE_CRITERIA`: PS2 and PM6 together would
    score one de novo observation 6 points). The dialog unchecks one when the other is checked, and
    `resolveExclusiveCriteria` keeps one of each pair in the initial selections;
  - a capped group over its cap when two or more of it are accepted (`CAPPED_CRITERIA`: PM1 and PP3
    together at most 4 points, ClinGen SVI). The dialog lowers the others of the group to fit, or
    unchecks them (`fitCappedCriteria`); either alone keeps its strength.

  The route takes no `cnv_acmg`, a structural variant's classification: a request that sends it,
  or any other field the save does not take, is refused (`422`) naming the field
  (`SmallVariantReviewUpdate`).

  It then recomputes the points and class, stores the VUS tier in the JSON record, and freezes the
  evidence snapshot (`build_evidence_snapshot`) that the report's drift check later compares
  against. The snapshot holds, as the dialog reads them from the variant's primary transcript
  (`small_variant_evidence`):
  - the annotation version and set hash, and the ClinVar significance;
  - the consequence, impact and LOFTEE call (read by PVS1, PM4, PP2, BP7 and the consequence's
    not-applicable criteria);
  - the population frequencies and in-silico scores the criteria read: gnomAD and the other
    frequencies and counts the filters read, the gnomAD datasets the callset was annotated with
    (`gnomad_annotated`, which PM2 reads for a variant without a gnomAD frequency), CADD, REVEL,
    SpliceAI, SIFT, PolyPhen and AlphaMissense;
  - for a missense variant only, the gene's gnomAD missense Z that PP2 reads, with its source
    (`gene_missense_z_source`: `gene_profile` or `vcf`; `pp2_gene_missense_z`). It comes
    from the gene's profile through the variant list's own lookup (`evidence_gene_missense_z`),
    else from the VCF's own `MISSENSE_Z`. `reads_gene_missense_z` tells a missense variant as the
    dialog's `isMissense` does.

  A later change of any of these is drift that names the value
  ([clinical-traceability.md](clinical-traceability.md#evidence-drift)). A refresh of the gene
  profiles is never drift for a classification of a variant that is not a missense variant.
- CNVs: `structural_variant_reviews.cnv_acmg` (JSONB), `cnv_point_total`, `cnv_class` and
  `cnv_evidence_snapshot`. The save reuses
  `PUT /families/{family_id}/structural-variants/{variant_id}/review`. The server refuses a
  criterion listed twice (`400`; the range caps one entry, and a repeat would count past it),
  clamps each submitted point value to the criterion's allowed range before summing, and freezes
  the evidence the classification reads (`build_structural_evidence_snapshot` in
  `backend/app/services/structural_variant_evidence.py`) for the same drift check. The save names
  in `source` the caller of the row the classification was made on: callers that call one event
  under one id share its review, and the frozen evidence is that caller's row. A save that names no
  caller takes the SV's one row; it is refused (`422`) when several callers call the SV. The route
  takes no `acmg` or `compound_het` (a small variant's fields; `StructuralVariantReviewUpdate`): a
  save that sends one is refused (`422`), naming it.
- Only a save is checked: a save that leaves `acmg` or `cnv_acmg` out keeps the stored
  classification.
- Every list of small variants serves each review with the columns of a single-review read
  (`_fetch_review_rows_for_variants` in `small_variant_review_repository.py`), the ACMG record
  included. The dialog opened from a list is seeded from that record. Without it, a classified
  variant would reopen with the pre-evaluation alone, and saving would replace the stored criteria
  with it.
- The class and VUS-tier tags (`acmg_class_*`, `acmg_vus_*`) are built-in tags defined in
  `DEFAULT_SMALL_VARIANT_TAGS` (`backend/app/services/small_variant_review_tags.py`). The dialog sets
  them from the selected criteria; the analyst cannot toggle them by hand.

## Scope

The small-variant dialog runs on the family small-variant page, the NIPT variant list and the family
mtDNA page (with the mt rule set, chosen for chromosome MT variants that carry mt context). The CNV
dialog runs on the family structural-variant page. The global Variant Explorer has no classifier.

## Rules shared with the backend

Some pre-evaluation rules repeat a backend rule. Change both sides together:

- **ClinVar.** `lib/clinvar.ts` is the one frontend reading of a ClinVar significance, for PP5 / BP6
  (nuclear and mtDNA), the colours of the ClinVar chip, card and table row, and the small-variant
  track marks. A *Conflicting classifications of pathogenicity* record is neither pathogenic nor
  benign, as in the prioritiser (`variant_prioritization.py`) and the mtDNA status
  (`mitochondrial_analysis.py`). So is a list of classifications that disagree; those two read a list
  by its words, so one that names pathogenic counts as pathogenic there. The track's salient set
  (`_small_salient_condition` in `clickhouse_variant_queries.py`) reads a list as `clinvarClass` does.
- **De novo (PM6 / PS2).** `evaluateFamily` in `evaluate.ts` follows the de novo filter in
  `clickhouse_variant_queries.py`:
  - the parents come from the pedigree links (`pedigree.ts` reads them as `_parent_child_links`
    does), never from the flat `father` / `mother` role, which a grandparent also holds;
  - a reference parent needs `_DE_NOVO_MIN_PARENT_DP` reads;
  - the proband must be heterozygous, except on a son's hemizygous X or Y.

  The dialog offers a candidate the filter drops for depth or zygosity as Consider.

  Whether the observation is PS2 or PM6 reads the Sample QC (`GET /families/{id}/qc/sample-integrity`,
  `sample_integrity_qc.py`): `parentageFromSampleQc` in `pedigree.ts` takes a proband–parent pair
  whose relatedness check passes as parent-child, with no warning or failing Mendelian check beside
  it, as confirmed, and a failing pair as refuted. The strength follows ClinGen's de novo points
  (`DE_NOVO_POINTS`, `deNovoStrength`), the phenotype tier read from the gene–phenotype match on
  PP4's cut-offs (`deNovoPhenotypeTier`).
- **Cosegregation counts people.** PP1 and BS4 in `evaluate.ts` count each person once:
  `personOfSamples` in `pedigree.ts` reads the family's same-individual links, and two samples of
  one person are one family member, a carrier when one of them carries the variant. The mtDNA
  evaluator counts its calls by sample.
- **mtDNA maternal transmission.** `_maternal_transmission` in `mitochondrial_analysis.py` reads the
  proband's mother and father from the same links, per person (`same_individual.links_per_person`): a link
  one sample of the proband records to one sample of the mother holds for every sample of each, so
  the mother sampled twice (two tissues) is looked at in both. The mtDNA PP1 needs
  `maternal_shared`.
- **The missense Z PP2 reads.** `pp2MissenseZ` in `evaluate.ts` reads the served `gene_missense_z`
  (the gene profile's) first, else `annotation_extra.gene_missense_z` (the VCF's own `MISSENSE_Z`);
  only a finite number counts. `pp2_gene_missense_z` in `small_variant_review_acmg.py` freezes the
  same value and its source with the classification (the owner's decision of 2026-10-28);
  `test_acmg_frontend_parity.py` checks the source names and their order. PP2 is offered as
  Consider at a Z of 3.09 or more (`PP2_MISSENSE_Z`) and never applied: whether missense variants
  are a common mechanism of disease in the gene is the analyst's judgement.
