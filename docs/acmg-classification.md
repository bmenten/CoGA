# Semi-automatic ACMG classification — developer notes

CoGA pre-evaluates the ACMG/AMP 2015 criteria for small variants, and the ClinGen / ACMG criteria for
copy-number variants, and scores them on a points scale. The design stance:

> **Decision support, not an autoclassifier — every criterion is overridable.** Suggestions are
> pre-positioned from the data; the analyst confirms strengths, adds a rationale note and saves. The
> class and points are **recomputed on the server** on save, so a stored classification never depends
> on the browser.

The clinical rules — the points and class bands, the VUS tiers, every pre-evaluation and exclusion
rule, the mtDNA rule set and the CNV classifier — are documented for lab users in the in-app reference
[`frontend/src/content/docs/acmg-classification.md`](../frontend/src/content/docs/acmg-classification.md)
(shown at `/docs/reference/acmg-classification`). That page is the canonical home; keep it in step with
the code. This note covers the implementation.

## Where the code lives

| Part | Files |
| --- | --- |
| Small-variant engine (pure, unit-tested) | `frontend/src/lib/acmg/` — `criteria.ts` (criterion catalogue), `evaluate.ts` (nuclear pre-evaluation), `evaluateMito.ts` (mtDNA rules), `pedigree.ts` (the proband's parents from the pedigree links), `score.ts` (points, class, VUS tier), `index.ts` (initial selections); `frontend/src/lib/clinvar.ts` (the ClinVar reading for PP5 / BP6) |
| Small-variant dialog | `frontend/src/pages/families/AcmgClassificationModal.tsx`, `AcmgScaleBar.tsx` |
| Small-variant server scoring | `backend/app/services/acmg_points.py` (codes, strengths, class bands, `vus_tier_for_points`), `small_variant_review_acmg.py` (payload normalisation, evidence snapshot) |
| CNV engine | `frontend/src/lib/cnvAcmg/` (criteria for loss and gain, auto-suggestions, scorer) |
| CNV dialog | `frontend/src/pages/families/CnvAcmgClassificationModal.tsx`, `CnvScaleBar.tsx` |
| CNV server scoring | `backend/app/services/cnv_acmg_points.py` (criterion catalogues with allowed point ranges, clamping, class bands), `structural_variant_review_pg.py` |

The frontend and backend scorers must agree. Each side has its own tests (listed in
[testing.md](testing.md)); change both together.

## Persistence

- Small variants: `small_variant_reviews.acmg` (JSONB, the per-criterion record), `acmg_point_total`,
  `acmg_class` and `acmg_evidence_snapshot` (baseline `backend/db/schema/postgres/03_assay.sql`). The
  save reuses `PUT /families/{family_id}/small-variants/{variant_id}/review`. The server validates the
  criterion codes and strengths, recomputes the points and class, stores the VUS tier in the JSON
  record, and freezes the evidence snapshot (`build_evidence_snapshot`) that the report's drift check
  compares against later.
- CNVs: `structural_variant_reviews.cnv_acmg` (JSONB), `cnv_point_total` and `cnv_class`. The server
  clamps each submitted point value to the criterion's allowed range before summing.
- The class and VUS-tier tags (`acmg_class_*`, `acmg_vus_*`) are built-in tags defined in
  `DEFAULT_SMALL_VARIANT_TAGS` (`backend/app/services/small_variant_review_tags.py`). The dialog manages
  them from the selected criteria; the analyst cannot toggle them by hand.

## Scope

The small-variant dialog runs on the family small-variant page, the NIPT variant list and the family
mtDNA page (mt rule set, chosen for chromosome MT variants that carry mt context). The CNV dialog runs
on the family structural-variant page. The global Variant Explorer has no classifier.

## Rules shared with the backend

Some pre-evaluation rules repeat a backend rule. Change both sides together:

- **ClinVar.** `lib/clinvar.ts` is the one frontend reading of a ClinVar significance, for PP5 / BP6
  (nuclear and mtDNA) and the small-variant track marks. A *Conflicting classifications of
  pathogenicity* record is neither pathogenic nor benign, as in the prioritiser
  (`variant_prioritization.py`) and the mtDNA status (`mitochondrial_analysis.py`).
- **De novo (PM6 / PS2).** `evaluateFamily` in `evaluate.ts` follows the de novo filter in
  `clickhouse_variant_queries.py`: the parents come from the pedigree links (`pedigree.ts` reads them as
  `_parent_child_links` does, never from the flat `father` / `mother` role, which a grandparent also
  holds), a reference parent needs `_DE_NOVO_MIN_PARENT_DP` reads, and the proband must be heterozygous
  outside a son's hemizygous X or Y. The dialog offers a candidate the filter drops for depth or
  zygosity as Consider.
- **mtDNA maternal transmission.** `_maternal_transmission` in `mitochondrial_analysis.py` reads the
  proband's mother and father from the same links. The mtDNA PP1 needs `maternal_shared`.
