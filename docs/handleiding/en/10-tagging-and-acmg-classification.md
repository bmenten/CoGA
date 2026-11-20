# 10. Variant tagging & semi-automatic ACMG classification

This chapter describes how an analyst *labels* (tags) and *classifies* variants, with the help of a **semi-automatic ACMG/AMP classifier**. CoGA does not classify on its own. The software pre-assesses each criterion from the variant, family and gene data and places it on a points scale; the reviewer can adjust *any* criterion. The server always **recalculates** the class and the points total, and the evidence used is **frozen**. If that evidence changes later, CoGA reports **drift**, and that blocks the sign-out (chapter 11). The rules per criterion are in the app: *Docs → Semi-automatic ACMG classification* (`frontend/src/content/docs/acmg-classification.md`).

A few terms:

- **ACMG/AMP criteria:** a standardised set of evidence rules (Richards et al., 2015) with codes such as `PVS1`, `PM2` or `BA1`. A `P` points to pathogenic evidence, a `B` to benign evidence.
- **Tag:** a label on a variant (e.g. "Report" or "Send for validation").
- **Snapshot:** a frozen copy of the data as they were at a given moment.

## Two separate properties: phenotype and carrier status

CoGA keeps two separate properties for each family member:

- **Clinical status** (`clinical_status`): `unknown`, `unaffected` or `affected`: does the person have the disorder?
- **Carrier status** (`carrier_status`): `unknown`, `not_carrier` or `carrier`: does the person carry the risk allele, affected or not?

This separation matters clinically: in a recessive disorder a parent is often a carrier but not affected. The segregation rules of the ACMG evaluator read only the clinical status and the genotypes. The carrier status serves the haplotype segregation of PGT (chapter 8).

## Part 1 — Tagging

### What is recorded

A review records, per variant and per family: the **class** (e.g. "VUS - class 3"), a list of **tags** and a **note**. For each tag CoGA also keeps *who* set it and *when*.

**A review per analysis.** When a family holds several analyses (short-read and then long-read WGS of the same DNA), a review belongs to one analysis (the owner's decision of 2026-11-20): a variant reviewed in the short-read analysis is not in the long-read analysis's record unless it is reviewed there too. Each row names its analysis (`analysis`, empty for a review of a family without an analysis). A reading for analysis X reads, per variant, X's own review, else the shared one (stored under no analysis the family holds), never another analysis's. A save in X always writes X's own review, made from the review it read and the save's values; the shared review stays as it is. A save that empties X's review keeps it, empty, while a shared review of the variant holds something, so X reads the variant as not reviewed. A family with several analyses with small variants of their own names the analysis on every save (`analysis`), otherwise `422`. Each audit event of a review names the analysis (`metadata.analysis`). The Variant Explorer shows, per family, the tags and classes of every analysis's review.

**Where in the code:** `backend/app/services/analysis_reviews.py`; `upsert_small_variant_review` in `small_variant_review_pg.py`.

**Class labels in sentence case.** The class tags and the VUS levels are in sentence case: "Likely pathogenic - class 4", "VUS — hot" (the owner's decision). A tag is stored on a review as a key. The class text of a review is free text: a save stores it as sent, without leading or trailing whitespace (`_stored_classification` in `review_pg_utils.py`). The filters and the Variant Explorer read a class label regardless of case.

**Which class a review shows.** One rule determines the class a review shows (`review_class_label` in `review_pg_utils.py`; in the frontend `normalizeReviewClassification`):

1. a bare class word in the text ("VUS") gives the label of that class;
2. otherwise the most severe class tag (`acmg_class_5` … `acmg_class_1`);
3. otherwise the text as stored.

Both sides read the cases from the shared table `review-class-cases.json`. Every place that shows a class or filters on it follows this rule (the owner's decisions of 10 November 2026):

- **Display.** The table, the cards, the compound-het pairs, the mtDNA cards and the light review dialog of the family page, and the table and the cards of the SV page; the tables and cards via `reviewClassChips` in `smallVariantResultUtils.ts`. A review shows one class: a class tag of another class does not appear next to it. The VUS levels and *Secondary finding* remain tags, never the class.
- **Filters.** The classification filter of the family page, of the SV search and of the Variant Explorer keeps a variant by the class its review shows, or for a small variant the review of its compound-het pair, regardless of case. So *Likely pathogenic - class 4* also finds a review with only the class tag `acmg_class_4`, and not a review with the text "VUS - class 3" next to the class tag `acmg_class_5`: that one shows class 5. A filter value is read like the text of a review: "VUS" asks for class 3, free text for the reviews that show that text (so without a class tag). The family page and the SV search filter in Python (`review_shows_class`), the Explorer in SQL (`review_class_label_sql`, which binds the class words, the class tags and the labels from the Python tables). An integration test on Postgres sets the SQL beside the Python rule for every case of `review-class-cases.json`.
- **Exports.** The CSV exports of small variants, of SVs and of the Variant Explorer write in the column *Classification* the class the review shows (`review_class_label`), and in *Classification text* next to it the classification as the reviewer wrote it and as it is stored. So "VUS - class 3" next to the tag `acmg_class_4` stays in the file, even though the review shows class 4. A review with only a class tag has an empty *Classification text*. The Explorer writes the text of the review whose class it shows: its own review or that of the compound-het pair, in the family that gives the most severe class (chapter 14).
- **CNVs.** A CNV classification counts through the class label that the CNV dialog stores as text; in doing so the dialog removes every ACMG class tag from the review. The page does not show its class in `cnv_class` alone, and so the filter does not read it.

**A quick tag does not change the classification.** *Review*, *Exclude* and *Report* send the classification text back as it is stored (`quickTagTogglePayload` in `smallVariantReview.ts`, also on the SV page). Such a save writes only the tag to the audit trail. The light review dialog opens on the class shown. If the class is left alone, the dialog keeps the text and the class tags as they were. If the user picks a different class, or none, it replaces the text and every class tag; otherwise a more severe class tag next to it would keep hiding the chosen class. The report and a signed version show the same class as the family page: the report reads it with this rule, a signed version from the frozen record (`class_shown`, chapter 11), with the ACMG or CNV score next to it when that gives a different class.

**Where in the code:** the labels in `acmg_points.py`, `cnv_acmg_points.py` and `small_variant_review_tags.py`; the filters in `small_variant_review_pg.py`, `structural_variant_review_pg.py` and `variant_explorer_service.py`.

**Concurrent editing.** If a review (of a small variant or a structural variant) was changed by someone else in the meantime, the server refuses the save (`409`) and sends the current version along. That way nobody overwrites someone else's work unnoticed.

Whoever saves a compound heterozygous pair also writes the review of the partner variant. If a pair falls apart in the process, CoGA rewrites the review of each of its variants. CoGA first locks all those reviews, in one fixed order, as with an ordinary save, and only then reads them. A partner review that was saved in the meantime (e.g. as class 5 with a new note) is therefore kept and is never reset to a copy read earlier. The server checks the partner's version just like that of the variant itself (`compound_het.partner_expected_updated_at`). If the partner was changed in the meantime, the same `409` follows, naming the partner. `updated_at` never goes back in time.

**Where in the code:** `backend/app/services/small_variant_review_pg.py` (the pair in `_lock_pair_save_reviews`); the check for concurrent changes in `_raise_on_stale_review` (`backend/app/services/review_pg_utils.py`).

### Which tags exist

- **System tags** are built in and cannot be changed: collaboration tags (e.g. `review`, `send_for_validation`, `validated`, `report`, `excluded`) and the class tags (`acmg_class_1` to `acmg_class_5`, the VUS levels `acmg_vus_hot`, `acmg_vus_warm` and `acmg_vus_cold`, and `secondary_finding`).
- **Custom tags** are created by a superuser or an administrator. They apply everywhere (`global`) or to one project (`project`); a project tag can be shared with additional projects.

On save, the backend checks that every tag the review *adds* exists and may be used in this family; an unknown tag gives `400`. A tag the review already carries stays, even if it has been deleted in the meantime. After all, the quick buttons and the review dialog send the stored tags back; otherwise every following save of that variant would fail, including the one that removes the tag.

Reviews, saved filter presets and the audit trail refer to a tag by its **key** (`key`). The key is derived from the label when the tag is created and is then fixed. Renaming changes only the label; the tag therefore stays on every review and in every filter. A label that reads like that of another active tag or of a system tag is refused (`409`). If a new tag gets the label of a renamed or deleted tag, it gets a numbered key (e.g. `probe_x_2`). It takes over no reviews of the old tag.

Only a superuser or an administrator (`get_current_superuser`, chapter 2) creates, changes or deletes custom tags, on the administration page and on the routes of a family; anyone else gets `403 Superuser access required`. Deleting does not erase a tag but sets it to inactive. Reviews that carry it still show it with its label, marked as *(deleted)*. It can be removed from them, but no longer added to any review. The management screen is under Administration (`frontend/src/pages/admin/AdminVariantTagsPage.tsx`).

If a project is deleted, its tags go the same way. `delete_project_record` first sets them to inactive and deletes the project in the same transaction; the tags continue to exist, without a project (`project_id` becomes `NULL`). They remain project tags, so no list shows them as a global tag, and they keep their key: a new tag with the same label gets a numbered key and takes over no reviews, signed records or audit rows. In addition, a family's tag list shows, as inactive, every tag that its own reviews carry but that its projects do not (or no longer) know, such as a tag of a deleted project or of a project the family left. A tag of another project that none of its reviews carries stays invisible.

**Where in the code:** `backend/app/services/small_variant_review_tags.py`; the tables `small_variant_tag_definitions` and `small_variant_tag_definition_project_links` in `03_assay.sql`.

### Filter presets and the light review dialog

An analyst can save their filter settings as a **preset**; a viewer or a reader can do so too, because these are their own data (chapter 2). Only the person who saved the preset is offered it and can delete it; a superuser or administrator sees everyone's small-variant presets (chapter 15). The owner is the account (`owner_id`), not the username: if the username changes on a sign-in with Microsoft, the preset stays theirs. The preset shows the username under which it was last saved (`owner`). A small-variant preset applies in every family the owner can open. A structural-variant preset applies to one family, or, as a reusable preset, to all their families.

Next to the full ACMG classifier there is a **light review dialog**: pick a class, switch tags on or off and write a note, with the priority score (chapter 12) as an aid.

**Where in the code:** `backend/app/services/small_variant_review_presets.py` and, for the SV presets, `structural_variant_review_pg.py`; `frontend/src/pages/families/SmallVariantReviewDialog.tsx`.

### Repeat loci and Paraphase regions

A repeat locus and a Paraphase region get a review of their own, per family and assembly. It uses the same tag catalogue, but classifies on the type's own scale instead of with an ACMG class (REQ-DIAG-009; `docs/repeat-paraphase-review-design.md`).

- The card on the repeat page and on the Paraphase page has the buttons of every variant card. *Review*, *Exclude* and *Report* switch their tag on or off; *Tags & notes* opens the dialog (`FindingReviewDialog.tsx`).
- The dialog does not offer the class tags with key `acmg_*` (the ACMG classes and the VUS levels). If a review carries one anyway, the dialog shows it marked, so that it can be removed (decision B14). *Secondary finding* remains available.
- The card shows the classification (*Review: …*), the tags and the note. *Differs from CoGA's status* appears when the classification fits none of the members' calls. *Changed since review* reports drift; the tooltip says what changed. *Exclude* collapses the card to its header.
- A save writes the review straight into the loaded table. The server's response, with the drift, replaces it; a failed save puts the card back (`useFindingReviewSave` in `findingReview.ts`).
- The family page counts the reviews per type (`GET /families/{id}/finding-review-summary`).

## Part 2 — The ACMG classifier for small variants

### Decision support, not an automaton

From a variant, **ACMG classify** opens a dialog that:

1. pre-assesses the criteria from the data CoGA already holds, and shows those data next to the criteria: one card per kind (consequence and transcript, population frequencies, in-silico scores, the gene, ClinVar, the proband's phenotype and the family's genotypes), each with the criteria it supports; a value the annotation does not have is shown as — (`AcmgEvidencePanel.tsx`);
2. puts each criterion in one of four states;
3. lets the analyst confirm, adjust or reject each criterion;
4. places the accepted criteria on a points scale;
5. recalculates everything **on the server** on save.

### The four states

| State | Display | Meaning |
| --- | --- | --- |
| **Applies** | ticked, green (pathogenic) or grey-blue (benign) | The data clearly support the criterion; it counts |
| **Consider** | orange dot, not ticked | Relevant signal, not decisive |
| **Argues against** | red cross, not ticked | The data point the other way (e.g. a benign prediction for PP3) |
| **Not applicable** | grey, struck through | Cannot apply to this variant type, but remains clickable |

All four can be adjusted: a click on a criterion always switches it on or off. An unticked criterion that never applies together with a ticked criterion gets **⊘** and a dotted border; ticking it swaps the two (see *Criteria that never apply together*).

**Reopening a classified variant.** The dialog shows each stored criterion with its state, strength and evidence. The evaluator only fills in missing evidence, and gives criteria that were never stored their own state. Saving again without a change therefore leaves the criteria as they were. Every list of small variants (the table and the cards, the report, the NIPT candidates, the mtDNA analysis) delivers the review with the same ACMG record as reading that single review (`_fetch_review_rows_for_variants` in `small_variant_review_repository.py`).

### What the evaluator pre-assesses

The evaluator reads the variant and, where available, the gene profile (ClinGen dosage, mode of inheritance from GenCC, the gene's phenotypes), the proband's HPO terms, the genotypes in the family and the relatedness checks of the Sample QC. It only proposes and changes nothing. The main rules:

| Criterion | Source | Rule (summarised) |
| --- | --- | --- |
| **PVS1** | consequence, LOFTEE, ClinGen dosage | Predicted loss of function (stop codon, frameshift, canonical splicing, …). *Very strong* if LOFTEE says `HC` and the loss-of-function mechanism is proven; otherwise *strong*, or *consider* if the mechanism is not confirmed. With a LOFTEE flag (e.g. `SINGLE_EXON`, `NAGNAG_SITE`) or a variant that, according to VEP's NMD plugin, escapes nonsense-mediated decay: *consider* (*strong*), with the reason in the evidence |
| **PM2**, **BA1**, **BS1**, **BS2** | gnomAD | Very rare → PM2 (*supporting*); without a gnomAD frequency, see *Absent from gnomAD* below. BA1 and BS1 read gnomAD's *filtering allele frequency* (FAF95, the highest across the genetic ancestry groups) where the annotation has it, as ClinGen SVI recommends, otherwise the allele frequency. Very frequent → BA1, which makes the variant benign on its own. Frequent → BS1. Homozygotes present → BS2: *strong* for a gene with a recessive mode of inheritance, otherwise *consider* at *supporting*. Without a homozygote count in the annotation, BS2 stays unmarked: check gnomAD yourself |
| **PP2** | gnomAD missense Z: from the gene profile, otherwise the VCF's own `MISSENSE_Z` | Missense in a gene with a missense Z of 3.09 or more (the threshold the ClinGen Sequence Variant Interpretation working group discusses for PP2) → *consider* (*supporting*), never applied: the analyst judges whether missense in that gene is a common disease mechanism. The evidence says which missense Z PP2 read. A gene profile below 3.09 decides, even if the VCF has a higher value (the owner's decision of 2026-10-28) |
| **PP3** / **BP4** | REVEL, SpliceAI, AlphaMissense | REVEL decides first, then SpliceAI, then AlphaMissense; whichever decides also sets the strength. Only a verdict on REVEL marks the other criterion as an argument against. BP4 requires that SpliceAI predicts no splice impact: without a SpliceAI score BP4 becomes *consider*. Without any prediction neither applies. A REVEL between the thresholds or an *ambiguous* AlphaMissense class leaves both open |
| **BP7** | consequence and SpliceAI | Synonymous without predicted splice impact (SpliceAI below 0.1). Without a SpliceAI score *consider*: check the splice impact yourself |
| **PP5** / **BP6** | ClinVar | ClinVar says pathogenic → PP5; benign → BP6, both *consider*, never applied: ClinGen SVI advises against counting another laboratory's classification as evidence. The evidence names ClinVar's review status, the stars and the condition(s). *Conflicting classifications of pathogenicity* gives neither. ClinVar is the ClinVar file the pipeline annotated; the older classification from the VEP cache counts only if no ClinVar file was annotated |
| **PP4** | Monarch phenotype score or HPO overlap | How specifically the phenotype fits the gene. The score comes from the phenotype ranking; if the variant is not ranked, the dialog asks the server for the gene's score in the family (`GET /families/{id}/gene-phenotype-score`), the same number |
| **PS2**, **PM6**, **PP1**, **BS4** | genotypes in the family, Sample QC, phenotype score | De novo (absent in both parents) → PS2 or PM6, at a strength from the ClinGen SVI de novo points (see *De novo: PS2 or PM6* below). Several affected carriers → PP1 (*consider*); an affected family member without the variant → BS4 (*consider*). Two samples of one person count as one family member |

The REVEL thresholds follow the 2022 ClinGen calibration; all thresholds are named constants in the evaluator. If the gene profile or the HPO list could not be loaded, the evidence says that the criterion was *not assessed*, not that it does not apply. The evaluator never proposes as applying the criteria that need a human judgement CoGA cannot derive from the annotation (among others PS1, PS3, PS4, PM1, PM3, PM5 and BP2).

**Absent from gnomAD (PM2).** A variant without a gnomAD frequency counts as absent from gnomAD if all three hold (the owner's decision of 2026-11-13):

- its callset was annotated with gnomAD genomes or the joint set, from the VEP cache or from a gnomAD file (`gnomad_annotated`);
- it lies on chromosome 1–22, X or Y;
- the annotation has no gnomAD FAF95 or popmax.

Then PM2 is applied and BA1 and BS1 drop out. Otherwise (gnomAD not annotated, only gnomAD exomes, a chrM variant, a contig gnomAD does not have) PM2 becomes *consider* and BA1 and BS1 stay open: check gnomAD yourself. CoGA does not read gnomAD's coverage at the position (`gnomadAbsence` in `evaluate.ts`).

**De novo: PS2 or PM6.**

- **The parents** are the father and mother the pedigree links to the proband, not the members with that role: a grandparent also has the role father or mother. For a son, on X or Y outside the pseudoautosomal regions, only the parent who passes on that chromosome decides.
- **PS2 or PM6.** If the Sample QC confirms the parents (KING kinship and IBS0 as parent and child, and no Mendel check of the proband that warns or fails), then **PS2**; otherwise **PM6**, with the reason. If a parent's check fails (the genotypes are not those of parent and child), the data argue against both.
- ***Consider* instead of applied:** a parent with fewer than 8 reads, a homozygous proband (outside a son's X or Y), a proband not registered as affected, a parent registered as affected, no phenotype score, or less than 0.5 point.

The strength follows the ClinGen SVI de novo points, depending on how well the phenotype fits the gene (the phenotype score, chapter 12):

| The phenotype | Phenotype score | Confirmed (PS2) | Assumed (PM6) |
| --- | --- | ---: | ---: |
| highly specific for the gene | 0.6 or more | 2 | 1 |
| fits, but not highly specific | 0.3 or more | 1 | 0.5 |
| fits only in general terms | above 0 | 0.5 | 0.25 |

0.5 point is *supporting*, 1 *moderate*, 2 *strong*. One proband reaches *strong* at most; the analyst adds extra de novo cases from the literature themselves.

**The coloured dots by the predictors on the card.** In the *In silico* column of a variant card there is a coloured dot for each prediction, in seqr's six steps: green and olive benign, grey undecided, yellow, red and dark red pathogenic (*supporting*, *moderate*, *strong*).

- **CADD, REVEL, AlphaMissense, SIFT and PolyPhen** are coloured on seqr's boundaries, which rest on ClinGen's calibration for PP3/BP4; a value on a boundary belongs to the step above. Those of REVEL coincide, to three decimals, with the evaluator's REVEL thresholds. AlphaMissense without a score is coloured by its class, as the evaluator reads it.
- **SpliceAI** is coloured according to the evaluator's constants (below 0.1 benign, from 0.2 *supporting*, from 0.5 *moderate*), so that the dot and the proposed PP3/BP4/BP7 never contradict each other; seqr reads 0.5 and 0.8.
- **pLI and missense Z** describe the gene and get no dot.

The dot is a display: the evaluator does not compute with it. The **ACMG classify** dialog shows the same dots by the in-silico scores on its evidence card. Code: `frontend/src/pages/families/inSilicoTones.ts` and `PredictorMark.tsx`.

**Criteria that never apply together.** Some criteria, taken together, would count one piece of evidence twice or read it in two directions. If the analyst ticks one, the dialog switches the other off; the server refuses a classification that applies both. The pairs, with their reason in the tooltip:

- PS2/PM6: one de novo observation is either confirmed or assumed (ClinGen SVI);
- PVS1/PP3: in-silico predictions add nothing to a predicted null allele;
- PVS1/PM4: the length change of a null allele already counts in PVS1;
- PS1/PM5, PS3/BS3, BA1/BS1, BA1/PM2, BS1/PM2, PM4/BP3, PP2/BP1, PP3/BP4 and PP5/BP6.

If the evaluator would tick both, it keeps the first-named, unless the other gives more points; a stored criterion takes precedence over a new proposal. PP1 and BS4 may go together (they can come from different family members), as may PP4 and a de novo criterion, even though the de novo points read the same phenotype.

**PM1 and PP3 together count at most *strong*** (4 points; ClinGen SVI, Pejaver et al. 2022), because a hotspot or critical domain and the in-silico predictors read overlapping evidence. Ticking one or making it stronger lowers the other to the strongest strength that still fits, or unticks it (⊘) if none fits: PM1 *moderate* brings PP3 *strong* down to *moderate*, PM1 *strong* leaves no room for PP3. Each on its own keeps its strength. The server refuses a save above the cap.

Code: `EXCLUSIVE_CRITERIA` and `CAPPED_CRITERIA` in `frontend/src/lib/acmg/criteria.ts` and `backend/app/services/acmg_points.py`.

**Where in the code:** `frontend/src/lib/acmg/evaluate.ts` (the evaluator, with the de novo points), `pedigree.ts` (the parents from the pedigree, their relatedness from the Sample QC) and `criteria.ts` (the catalogue of the 28 criteria and the pairs that never apply together); the ClinVar reading in `frontend/src/lib/clinvar.ts`; fetching the context in `frontend/src/pages/families/AcmgClassificationModal.tsx`.

### The points scale and the five classes

CoGA uses the Bayesian points system of Tavtigian and ClinGen. Each accepted criterion counts points according to its strength; benign criteria count negatively.

| Strength | Pathogenic | Benign |
| --- | ---: | ---: |
| Supporting | +1 | −1 |
| Moderate | +2 | −2 |
| Strong | +4 | −4 |
| Very strong | +8 | — |

The total gives the class: **≥ 10** pathogenic (class 5), **6 to 9** likely pathogenic (4), **0 to 5** VUS (3), **−1 to −6** likely benign (2), **≤ −7** benign (1). An accepted **BA1** makes the variant benign, regardless of the rest. A VUS also gets a level according to its distance from the class 4 boundary: **4–5 hot**, **2–3 warm**, **0–1 cold**.

The dialog shows the total on a bar following the Bayesian reading of the scale (prior probability 10%, odds of pathogenicity 350^(points/8)). Each VUS point has its own box, the ticks carry the posterior probability (0.1%, 10%, 18.8%, 32.5%, 50%, 67.5%, 81.2%, 90% and 99%), and the class boundaries are marked more strongly. The bar is only a display; the class follows from the thresholds above (`frontend/src/pages/families/AcmgScaleBar.tsx`).

**The server always recalculates.** The frontend sends its own total along, but the backend ignores it. It checks every criterion and refuses (`400`):

- an unknown code or strength;
- a criterion that occurs twice;
- a strength the criterion does not allow;
- two accepted criteria that never apply together (`EXCLUSIVE_CRITERIA`);
- PM1 and PP3 together above 4 points (`CAPPED_CRITERIA`).

Each criterion therefore counts once, at a strength the dialog offers for it. Otherwise a PVS1 sent twice would count +16 (pathogenic), and BS1 at *very strong* −8. The backend then recalculates class, total and VUS level. A stored classification therefore never depends on the browser. Only the save is checked: a classification saved earlier is read as it was written. Frontend and backend use the same thresholds, and both sides are tested against those thresholds. The dialog also writes the class back as a tag (`acmg_class_N`, and for a VUS `acmg_vus_<level>`), so that cards and summaries show it.

**Allowed strengths.** The pathogenic criteria allow *very strong* to *supporting*, except PM2 (*moderate* or *supporting*). BA1 is *stand-alone* only, and *stand-alone* is only for BA1. BS1 to BS4 and BP4 allow *strong* to *supporting*, the other BP criteria *moderate* or *supporting*. The list exists twice: as `allowedStrengths` in `frontend/src/lib/acmg/criteria.ts` (the choices in the dialog) and as `ALLOWED_STRENGTHS` in `acmg_points.py` (the check on the server). `backend/tests/test_acmg_frontend_parity.py` reads `criteria.ts` and fails if the two differ.

**Where in the code:** `frontend/src/lib/acmg/score.ts` and `backend/app/services/acmg_points.py`; the check and the recalculation in `backend/app/services/small_variant_review_acmg.py`.

### Mitochondrial variants

A variant from the mtDNA analysis goes to a **separate evaluator** following the ClinGen specifications for mtDNA (McCormick 2020). mtDNA is inherited through the mother and is haploid. Hence: no de novo rules, stricter frequency thresholds, PVS1 only in protein-coding loci, no in-silico predictors, and segregation through the maternal line and the heteroplasmy. With gnomAD-MT or HelixMTdb in the annotation, BA1 and BS1 read the highest homoplasmic population frequency; PM2 is applied only if the heteroplasmic one is also below 0.002%, otherwise *consider*. Where two mtDNA genes overlap (MT-ATP8/MT-ATP6, MT-ND4L/MT-ND4), the card shows the consequence per gene. The maternal line starts at the mother the pedigree links to the proband; PP1 requires that she carries the variant. The points scale and the classes are the same.

**Where in the code:** `frontend/src/lib/acmg/evaluateMito.ts`.

## Part 3 — ACMG for CNVs

Copy-number variants are classified according to the **2019 ClinGen CNV standard** (Riggs et al., 2020), a different points system:

- The evidence sections and weights differ between **loss** (deletion) and **gain** (duplication): there are two catalogues.
- Points are **continuous** (e.g. +0.90 or −0.60). Many criteria have an allowed range within which the reviewer picks a value.
- The classes: **≥ 0.99** pathogenic, **0.90 to 0.98** likely pathogenic, **−0.89 to 0.89** VUS, **−0.98 to −0.90** likely benign, **≤ −0.99** benign.

The evaluator is cautious. It proposes only on the basis of what the SV reliably carries: overlapping genes, gene constraint, annotated inheritance and the number of genes. As with small variants, the server **caps and recalculates**: it keeps every value within the range of its criterion before adding up. A criterion that occurs twice is refused (`400`). Otherwise it would count twice, beyond its range: 2B at 0.90 twice would give 1.80, pathogenic instead of likely pathogenic. The catalogues are in the frontend and in `cnv_acmg_points.py`; `test_acmg_frontend_parity.py` fails if their codes, sections or ranges differ. Unlike for small variants, there is no analogue of BA1.

**Separate proposals per kind.** The dialog keeps a separate form for loss and for gain. Each gets the proposals the evaluator computes for that kind: the section 3 ladder according to that kind's thresholds, and 2H (from the pLI) only for loss. Switching kind therefore never brings in the criteria, points or notes of the other kind. A manual change stays with the kind in which it was made, and is still there after switching back. Only the kind shown is saved.

**Where in the code:** `frontend/src/lib/cnvAcmg/` (catalogues, evaluator, score) and `frontend/src/pages/families/CnvAcmgClassificationModal.tsx`; `backend/app/services/cnv_acmg_points.py` and `structural_variant_review_pg.py`.

## Part 4 — Storage and the frozen evidence

The full assessment per criterion is stored as JSON. The recalculated total and the class are stored next to it, for filters and summaries: `acmg`, `acmg_point_total` and `acmg_class` on `small_variant_reviews`, and `cnv_acmg`, `cnv_point_total` and `cnv_class` on `structural_variant_reviews`. That makes it possible to see afterwards which criteria were applied with which strength and which evidence.

**The evidence snapshot.** When a small variant is classified, CoGA freezes the evidence in `acmg_evidence_snapshot`: the annotation version, the **hash of the annotation set** (which changes as soon as any annotation changes), the ClinVar value and the time. In addition, the consequence, population frequencies and in-silico scores the classification rests on, as the ACMG dialog reads them: from the variant's primary transcript (MANE Select, then canonical, then the most severe impact; its id is in `transcript_id`), through the same helper functions as the variant list (`small_variant_evidence`). Per group:

- `consequence`: the consequence term(s) (`effect`) that the criteria read for a null, missense, synonymous or length-changing variant (PVS1, PM4, PP2, BP7 and the criteria a consequence excludes); the `impact`; LOFTEE's loss-of-function call (`lof`), on which the strength of PVS1 depends, with LOFTEE's flags (`lof_flags`) and VEP's NMD prediction (`nmd`), which make PVS1 *consider*. Only for a multiallelic record (ALT `G,T`) also the ALT allele these values belong to (`alt_allele`).
- `frequencies`: the gnomAD allele frequency the frequency criteria read; the gnomAD datasets the callset was annotated with (`gnomad_annotated`), which say whether a variant without a frequency is absent from gnomAD (PM2); the exome, genome, gnomAD popmax, VEP `MAX_AF` (`max_af`) and TOPMed frequencies and the gnomAD counts (alleles, homozygotes, hemizygotes) the filters read; gnomAD's FAF95 (`gnomad_faf95_af`), which BA1 and BS1 read; only for a chrM variant the homoplasmic and heteroplasmic frequencies of gnomAD-MT and HelixMTdb. Next to the ClinVar classification, also the review status of that ClinVar record (`clinvar_review_status`).
- `in_silico`: CADD, REVEL, SpliceAI, SIFT, PolyPhen and the AlphaMissense class and score.
- `gene_constraint`, only for a missense variant: the gene's gnomAD missense Z (`gene_missense_z`) that PP2 reads, and where it came from (`gene_missense_z_source`).

A missense variant has `missense_variant` in its consequence; that is how the dialog recognises one for PP2 (`reads_gene_missense_z`). For every other variant PP2 reads no missense Z: the group is absent and the gene profile is not read (the owner's decision, 2026-10-20). The missense Z comes from:

1. **The gene profile** (`gene_info`), not the variant's annotation. PP2 reads this first, with the variant list's lookup (`fetch_gene_constraint_metrics`): in the gene profiles of the family's assembly, for the gene the variant is shown with. That is the gene id of the primary transcript, otherwise its symbol (in upper or lower case), otherwise the variant's first gene.
2. **The VCF**, if the gene profile has no missense Z: the VCF's own `MISSENSE_Z` (`pp2_gene_missense_z`, the owner's decision of 2026-10-28). The import stores it as `gene_missense_z` in the annotation of the primary transcript. The variant card shows it under *Other scores & annotations* as *Missense Z (gnomAD, from the VCF)*.

The source is `gene_profile` or `vcf`. If neither has a value, the source and the value are `null`.

Every field is frozen, even without a value (`null`), so that a value that appears later counts as a change. A value JSONB cannot hold (NaN, infinity) becomes `null`.

Every change to a review, of a small variant, an SV or a CNV, writes a before-and-after row, in the same transaction, to the append-only, hash-chained **clinical audit trail** (chapter 11): who, when and what changed (class, criteria, tags, note). The row for the class holds the whole stored record: per criterion the strength (for a CNV the points), whether it is accepted, the evidence and whether it was an automatic suggestion, with the points total (and for a CNV the kind). Every change to it goes into the trail, even if the class stays the same; an unchanged re-save writes nothing. Clearing or deleting a review is also recorded, and the review of a compound heterozygous pair gets a row of its own (action `compound_het_pair`, chapter 11).

**The evidence snapshot of a CNV.** An SV has no annotation-set hash in ClickHouse. When a CNV classification is saved, CoGA therefore freezes in `cnv_evidence_snapshot` (on `structural_variant_reviews`):

- the values the CNV evaluator reads: the type, the overlapping genes and their number, the pLI and the annotated inheritance;
- the event itself: the caller, the chromosome, the start, the end, the length and the breakpoint partner;
- a hash of the SV's full annotation, and the time;
- the versions the evidence comes from: that of the SV callset in the family's annotation manifest, the reference assembly and the gene loci.

The evaluator does not read clinical CNVs, dosage scores or DGV; the analyst scores those criteria themselves. The genotypes are not in the snapshot. The drift key is `evidence_hash`, a SHA-256 over the evidence; the versions are recorded, not compared. The server refuses a CNV classification of an SV that is not in the family's data (`404`). If the family has no assembly, there is no SV store to read, and the classification is stored without a snapshot.

**Which row is frozen.** Several callers can call the same event under one SV id (the id of an uploaded SV names no caller, and that of a NeedlR SV has the same form). They then share one review, but each caller has its own row on the SV page, with its own genes, pLI and inheritance. The snapshot therefore comes from the row of the caller on which the classification was made: the CNV dialog sends that caller along in `source`.

- If the save names no caller, CoGA takes the SV's only row.
- If several callers then call the SV, it cannot be told on which row the classification was made. The server then refuses (`422`) instead of freezing another caller's evidence. (On this route `409` already means that someone else saved the review in the meantime.)
- A caller that does not call the SV gives `404`.

The snapshot names the caller it comes from (`evidence.source`) and holds that caller's genes, pLI and inheritance; the drift check compares it with that caller's row.

**Where in the code:** `build_evidence_snapshot` in `backend/app/services/small_variant_review_acmg.py`; `build_structural_evidence_snapshot` and `fetch_structural_variant_records` in `backend/app/services/structural_variant_evidence.py`, the frozen row in `_classified_row` (`structural_variant_review_pg.py`); the audit trail in `backend/app/services/clinical_audit_service.py` (`record_review_changes` for small variants, `record_structural_review_changes` for SVs and CNVs, `record_compound_het_pair_changes` for a pair).

## Part 5 — Classification drift

Annotations change: a new ClinVar or gnomAD release can shift the evidence under a classification, a re-import the genes under a CNV. That is why CoGA compares, for each classified small variant and for each SV or CNV with a CNV classification, the frozen snapshot with the current data, using the same function that built the snapshot:

| Status | Meaning |
| --- | --- |
| `current` | Unchanged (the hashes and every frozen value are equal) |
| `drifted` | The evidence changed since the classification |
| `variant_missing` | The variant is no longer in the data |
| `unknown` | Nothing has visibly shifted, but one of the two hashes is missing, or the frozen evidence cannot be read; the link cannot be checked |

If a hash is missing, the check does **not** report `current` but `unknown`; at sign-out `unknown` counts as drift. A stored snapshot that is not a JSON object (the writer never stores one) is compared as a snapshot that froze nothing. It therefore also reports `unknown` (or `variant_missing`). A drift must be re-reviewed, or acknowledged with a reason, before a report can be signed out (chapter 11). Whoever saves the classification again freezes the evidence as it currently is.

For a small variant, `drifted` also says what shifted (`changed`):

- the ClinVar value (`clinvar`);
- the primary transcript (`transcript_id`);
- every frozen consequence, frequency or in-silico score, by its name (`effect`, `impact`, `lof`, `gnomad_af`, `revel`, …);
- the gene's missense Z (`gene_missense_z`);
- its source (`gene_missense_z_source`), if PP2 now reads the missense Z from the other source because the gene profile gained or lost one (*gnomAD missense Z 4.20 (VCF) → 3.52 (gene profile)*);
- `annotations`, if the hash changed.

`evidence_from` and `evidence_to` give the transcript, the consequence, the frequencies, the scores and the missense Z with its source, as frozen and as they currently are. Rules:

- A frozen value that shifted is drift, even under the same hash.
- The missense Z of a missense variant does not fall under that hash: a refresh of the gene profiles can shift it while the hash stays the same. A classification of another variant (e.g. a stop codon) froze none, and as a result never shifts.
- The check compares every value the snapshot froze, and numbers by their value (JSONB stores the digits of a number, not whether it was an integer).
- The current values are read as a classification saved at that moment would freeze them. A variant that was classified as a missense variant and no longer is one names its consequence and its missense Z as gone (*gnomAD missense Z 2.95 → n/a*).
- The missense Z of the family's missense variants is read in one query.

For an SV or CNV, the check compares the snapshot with the row of the caller from which it was frozen (`evidence.source`): the evidence as a whole, by its hash. Another caller's call is different evidence, not a change to this evidence.

- `drifted` also says what shifted (`changed`): the genes, the pLI, the inheritance, the type, the position or the remaining annotation, with the value before and after.
- `variant_missing` means that that caller's call is no longer in the data, even if another caller still calls the SV. The drift banner and the signed version name that caller (*its needlr call is no longer present*).
- Frozen evidence that names no caller counts as `unknown`.

The check compares only the fields the snapshot froze, and reads the SV as the SV page reads it. The header of the CNV dialog names the caller of the row on which it was opened; the SV table shows the *Source* column by default.

**Where in the code:** `backend/app/services/classification_drift_service.py` (the status logic in `_diff`; for SVs and CNVs `evaluate_structural_classification_drift`, with `fetch_structural_variant_records`, `frozen_evidence_source` and `diff_structural_evidence` from `structural_variant_evidence.py`).

## Key files

| File | Role |
| --- | --- |
| `frontend/src/content/docs/acmg-classification.md` | The rules per criterion, as the laboratory reads them |
| `frontend/src/lib/acmg/` | Catalogue, evaluator (mtDNA included) and points scale |
| `frontend/src/lib/cnvAcmg/` | CNV catalogues, evaluator and points scale |
| `frontend/src/pages/families/AcmgClassificationModal.tsx` · `CnvAcmgClassificationModal.tsx` | The classification dialogs |
| `backend/app/services/acmg_points.py` · `cnv_acmg_points.py` | Allowed strengths and ranges, recalculation on the server |
| `backend/app/services/small_variant_review_pg.py` · `small_variant_review_acmg.py` | Saving reviews, checking ACMG, the evidence snapshot |
| `backend/app/services/structural_variant_review_pg.py` | CNV/SV reviews and CNV ACMG |
| `backend/app/services/structural_variant_evidence.py` | The evidence snapshot of a CNV classification and its comparison |
| `backend/app/services/small_variant_review_tags.py` | System tags, custom tags, permissions |
| `backend/app/services/classification_drift_service.py` | Drift |
| `backend/app/services/clinical_audit_service.py` | The clinical audit trail |
