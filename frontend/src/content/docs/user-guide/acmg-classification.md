From any small-variant card or table row, **ACMG classify** opens a dedicated modal that helps you
apply the ACMG/AMP 2015 criteria. It reads the data already attached to the variant — molecular
consequence, gnomAD frequency, in-silico predictions, ClinVar — plus the gene profile (ClinGen
dosage, GenCC inheritance, gene–phenotype HPO terms) and the family genotypes, and pre-positions
each criterion accordingly. Nothing is final: every criterion can be toggled and re-graded by you.

### How the score and class are computed

Scoring follows the Tavtigian/ClinGen Bayesian **points** system. Each *applied* criterion
contributes points by its strength; the signed total maps onto the five ACMG classes and drives the
green→red scale bar and arrow at the top of the modal.

| Strength | Pathogenic | Benign |
| --- | --- | --- |
| Supporting (PP/BP) | +1 | −1 |
| Moderate (PM) | +2 | −2 |
| Strong (PS/BS) | +4 | −4 |
| Very strong (PVS1) | +8 | — |

Class bands over the point total: **≥ 10** Pathogenic · **6–9** Likely Pathogenic · **0–5** VUS ·
**−1…−6** Likely Benign · **≤ −7** Benign. `BA1` (allele frequency ≥ 5%) is a stand-alone override
that classifies the variant Benign regardless of any other evidence. The class is also recomputed on
the server when you save, so a stored classification never depends on the browser.

### VUS sub-tiers: hot, warm, cold

A variant of uncertain significance is not a single bucket. Following the MAGI-ACMG approach, CoGA
splits the VUS point band (0–5) into three tiers by how close the evidence sits to the
Likely-Pathogenic threshold, so the clinically interesting “hot” VUS stand apart from the rest:

| VUS sub-tier | Points | Reading |
| --- | --- | --- |
| **Hot** | 4–5 | Leans pathogenic — one supporting line of evidence from Likely Pathogenic. Worth chasing extra evidence (segregation, functional, parental testing). |
| **Warm** | 2–3 | Intermediate — mixed or partial evidence. |
| **Cold** | 0–1 | Little pathogenic support — closest to likely benign. |

The tier is shown as a coloured chip on the scale-bar readout (“VUS · Hot”) and, on save, is written
back as a `VUS — Hot/Warm/Cold` review tag alongside the `VUS - class 3` class tag. Because it is an
ordinary review tag, you can filter or exclude on it like any other — e.g. surface only *hot* VUS
for follow-up. The tier only exists while the variant is a VUS; reclassifying it out of the VUS band
clears it.

### The four states a criterion can be in

Auto-evaluation positions every criterion into one of four states (all overridable):

- **Applied (checked, green)** — the data clearly supports the criterion, so it is pre-checked and
  already counts toward the score.
- **Consider (●, amber)** — there is a relevant but not decisive signal. The criterion is surfaced
  unchecked; you confirm it if appropriate.
- **Argues against (✕, red)** — the data points the other way (e.g. an in-silico tool calls it
  benign when you are looking at PP3). Left unchecked and flagged.
- **Not applicable (greyed, struck-through)** — the criterion cannot apply to this variant given its
  type, frequency band or family configuration. Greyed as a clear hint, but still clickable so you
  can override it.

Hover any criterion for the exact evidence string behind its state. Criterion families are laid out
benign-left to pathogenic-right to mirror the scale bar.

### Pre-check rules (positive evidence)

These rules decide which criteria are pre-checked or flagged *consider* from the variant, gene and
family data:

| Criterion | State | Rule / data source |
| --- | --- | --- |
| **PVS1** | Applied (Very strong / Strong) or Consider | Predicted-null consequence (nonsense, frameshift, canonical ±1,2 splice, start-loss, transcript ablation). Applied at *Very strong* when LOFTEE is high-confidence and ClinGen lists the gene as haploinsufficient (“Sufficient evidence”); at *Strong* otherwise. If the LOF disease mechanism is unconfirmed it is shown as *Consider* rather than auto-applied. |
| **PM2** | Applied (Supporting) | Absent from gnomAD, or popmax allele frequency \< 1×10⁻⁴. |
| **BA1** | Applied (Stand-alone) | gnomAD allele frequency ≥ 5% — stand-alone benign override. |
| **BS1** | Applied (Strong) | gnomAD allele frequency ≥ 1% (and \< 5%) — higher than expected for a Mendelian disorder. |
| **BS2** | Applied (Strong) / Consider | Homozygotes observed in gnomAD. Strong when the gene is recessive-associated (GenCC); otherwise *Consider* pending inheritance-mode confirmation. |
| **PM4** | Consider (Moderate) | In-frame indel or stop-loss (protein-length change) — confirm it is outside a repeat region. |
| **PP2** | Applied (Supporting) | Missense in a missense-constrained gene (gnomAD missense Z ≥ 3.09). |
| **PP3 / BP4** | Applied (strength-scaled) | In-silico predictions. REVEL drives the call with ClinGen-calibrated thresholds — PP3: ≥ 0.644 Supporting, ≥ 0.773 Moderate, ≥ 0.932 Strong; BP4: ≤ 0.290 Supporting, ≤ 0.183 Moderate, ≤ 0.016 Strong. SpliceAI (max Δ ≥ 0.2 / ≥ 0.5) and AlphaMissense class are also used. The opposing criterion is flagged *argues against*. |
| **BP7** | Applied (Supporting) | Synonymous variant with no predicted splice impact (SpliceAI max Δ \< 0.1). |
| **PP5 / BP6** | Applied (Supporting) | ClinVar reports this exact variant pathogenic (→ PP5) or benign (→ BP6). |
| **PP4** | Applied (Supporting → Moderate) | Phenotype specific for the gene. When the family ran with [phenotype prioritisation](#variant-prioritisation), the strength is scaled by the Monarch gene↔proband phenotype-match score — **≥ 0.6** Moderate, **≥ 0.3** (or a direct HPO-term overlap) Supporting, below that not suggested. Without a match score it falls back to a plain HPO-term overlap at Supporting. The evaluator caps PP4 at Moderate; raise it to Strong by hand for a highly specific, single-gene phenotype. |
| **PM6** | Applied (Moderate) | Trio: variant present in the proband and absent in both sequenced parents (de novo). Applied as *assumed* de novo (PM6); upgrade to PS2 manually if parentage is molecularly confirmed. |
| **PP1** | Consider (Supporting) | Cosegregation: the variant is carried by ≥ 2 affected family members. |
| **BS4** | Consider (Strong) | Lack of segregation: an affected relative does not carry the variant. |

### Exclusion rules (greyed as “not applicable”)

Criteria that cannot apply to the variant in front of you are greyed out, so the working set stays
honest. They remain clickable for the rare case where you need to override.

| When… | Greyed as not applicable |
| --- | --- |
| Missense variant | PVS1, PM4, BP3, BP7 |
| Loss-of-function variant | PP2, PM5, BP1, BP7, BP3 |
| Synonymous variant | PVS1, PM4, PP2, PM5, BP1, BP3 |
| In-frame / length-changing variant | PVS1, PP2, PM5, BP1, BP7 |
| Splice-region variant | PVS1, PP2, PM5, BP1, PM4 |
| Allele frequency does not match a band | The frequency criteria that do not fit — only the matching one of PM2 / BS1 / BA1 stays active |
| No homozygotes in gnomAD | BS2 |
| No in-silico prediction available | PP3, BP4 |
| No complete trio / parental genotypes | PS2, PM6 |
| Variant inherited from a parent | PS2, PM6 (it is not de novo) |
| No additional affected carrier | PP1 (cosegregation cannot be shown) |
| No affected relative lacking the variant | BS4 (lack of segregation cannot be shown) |

### What is not auto-evaluated

Criteria that need information CoGA does not hold are always left for you to apply manually: **PS1**
and **PM5** (a different/known variant at the same amino-acid residue — needs a residue-level
ClinVar index), **PS3 / BS3** (functional studies), **PS4** (case–control prevalence), **PM1**
(hotspot / functional domain) and **PM3 / BP2** (in-trans phasing).

### Mitochondrial (mtDNA) variants

Opening **ACMG classify** on a variant from the [mtDNA analysis](#specialised-analyses) switches the
evaluator to an mtDNA-specific rule set (after the ClinGen/Wong–McCormick 2020 specifications),
because the mitochondrial genome is haploid and maternally inherited and the nuclear assumptions do
not hold. The points scale, classes and VUS sub-tiers are unchanged; only the pre-evaluation
differs:

- **PVS1** applies only to predicted-null changes in a protein-coding mt gene; it is greyed out for
  tRNA, rRNA and control-region loci (no protein product).
- **Frequency (PM2 / BS1 / BA1)** uses stricter mtDNA thresholds against gnomAD-MT — BA1 ≥ 0.5%, BS1
  ≥ 0.02%, PM2 below 0.002% / absent. A MITOMAP common polymorphism or haplogroup marker is routed
  to BS1.
- **PP5 / BP6** read MITOMAP / ClinVar status (confirmed pathogenic → PP5; benign / polymorphism →
  BP6).
- **PP3 / BP4** are left for manual review — the mt-specific predictors (MitoTIP / APOGEE / HmtVar)
  are not yet wired in, so no in-silico call is auto-applied.
- **De novo (PS2 / PM6)** does not apply; instead the evaluator assesses **maternal segregation**
  from the maternal-line calls and heteroplasmy (PP1 / BS4). **PM1** is offered as *consider* for
  tRNA loci, and PP4 notes the proband’s heteroplasmy level. Nuclear-only criteria (PP2, PM5, PM3,
  BP1–3, …) are greyed out.

### External evidence links

The modal header carries quick links for the variant — **gnomAD**, **ClinVar**, **DECIPHER** — plus
a smart **PubMed** search that combines the gene/variant with the patient’s HPO terms, to check
whether the gene–phenotype association has been published.

> **Decision support, not an autoclassifier.** Suggestions are pre-positioned from the data, but you
> confirm, adjust strengths, and add a rationale note. On save, the resulting class is written back
> as the variant’s ACMG classification (and class tag), and the full per-criterion rationale is
> stored for audit and reuse.

[ACMG classification reference (pre-check rules, points \& class bands, mtDNA rules)](/docs/reference/acmg-classification "further-reading")
