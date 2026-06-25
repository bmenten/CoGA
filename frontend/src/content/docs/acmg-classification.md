# Semi-automatic ACMG classification — reference

CoGA's **ACMG classify** modal pre-evaluates the ACMG/AMP (2015) criteria for a
small variant from data already attached to it, scores them on a Bayesian points
scale, and lets you confirm or override every call before you save. It is decision
support, not an autoclassifier: nothing is final until you grade it, and the class
is recomputed on the server on save so a stored result never depends on the browser.

This is the in-depth reference. For the workflow-level overview see the [in-app user guide](/docs) section *Semi-automatic ACMG classification*.

---

## How the score becomes a class

Scoring uses the Tavtigian/ClinGen Bayesian **points** system: each *applied*
criterion contributes points by its applied strength (benign strengths are
negative), and the signed total maps onto the five ACMG classes.

| Strength | Pathogenic | Benign |
| --- | ---: | ---: |
| Supporting (PP / BP) | +1 | −1 |
| Moderate (PM) | +2 | −2 |
| Strong (PS / BS) | +4 | −4 |
| Very strong (PVS1) | +8 | — |

| Points | Class | Tag |
| --- | --- | --- |
| ≥ 10 | Pathogenic (class 5) | `acmg_class_5` |
| 6 … 9 | Likely Pathogenic (4) | `acmg_class_4` |
| 0 … 5 | VUS (class 3) | `acmg_class_3` |
| −1 … −6 | Likely benign (2) | `acmg_class_2` |
| ≤ −7 | Benign (class 1) | `acmg_class_1` |

`BA1` (allele frequency ≥ 5%) is a **stand-alone override**: an applied BA1
classifies the variant Benign regardless of any other evidence. On save the
computed class is written to the variant's classification and the matching
`acmg_class_N` review tag, and the full per-criterion blob — each criterion's
applied strength and your rationale note — is persisted for audit and reuse (see
the [clinical-traceability reference](/docs/reference/clinical-traceability)).

### VUS sub-tiers: hot, warm, cold

The VUS band (points 0–5) is split into three tiers by proximity to the
Likely-Pathogenic threshold (6), following the MAGI-ACMG approach, so a clinically
interesting "hot" VUS stands apart from the rest:

| Points | Sub-tier | Tag | Reading |
| --- | --- | --- | --- |
| 4 … 5 | **Hot** | `acmg_vus_hot` | One supporting line of evidence from Likely Pathogenic — chase more evidence. |
| 2 … 3 | **Warm** | `acmg_vus_warm` | Intermediate / mixed evidence. |
| 0 … 1 | **Cold** | `acmg_vus_cold` | Little pathogenic support; closest to likely benign. |

The tier is shown as a chip on the scale bar and, on save, written back as an
`acmg_vus_<tier>` review tag next to `acmg_class_3`, so a "hot VUS" is filterable
through the ordinary review-tag pipeline. It is `null` for any non-VUS class;
reclassifying out of the VUS band clears the tier and its tag.

---

## Pre-check rules (positive evidence)

Auto-evaluation positions each criterion into one of four states — **Applied**
(green, counts toward the score), **Consider** (amber, a relevant but not decisive
signal), **Argues against** (red, the data points the other way), or **Not
applicable** (greyed). All four are overridable; clicking always toggles. Hover any
criterion for the exact evidence string behind its state.

The data sources are the `SmallVariant` record (consequence, gnomAD, in-silico,
ClinVar), the gene profile (ClinGen dosage, GenCC inheritance, gene–phenotype HPO),
the family genotypes, and the proband's present HPO terms.

| Criterion | State | Rule |
| --- | --- | --- |
| **PVS1** | Applied (Very strong / Strong), else Consider | Predicted-null consequence (`stop_gained`, `frameshift_variant`, `splice_acceptor/donor_variant`, `start_lost`, `transcript_ablation`). **Very strong** when LOFTEE = `HC` **and** ClinGen haploinsufficiency = "Sufficient evidence"; **Strong** when the mechanism is otherwise supported; **Consider** when the LOF disease mechanism is unconfirmed. |
| **PM2** | Applied (Supporting) | Absent from gnomAD, or allele frequency < 1×10⁻⁴. |
| **BA1** | Applied (Stand-alone) | gnomAD allele frequency ≥ 5%. Stand-alone benign override. |
| **BS1** | Applied (Strong) | gnomAD allele frequency ≥ 1% and < 5%. |
| **BS2** | Applied (Strong) / Consider | Homozygotes present in gnomAD. **Strong** when GenCC marks the gene recessive; otherwise **Consider**. |
| **PM4** | Consider (Moderate) | In-frame indel / stop-loss (`inframe_insertion/deletion`, `stop_lost`, `protein_altering_variant`). Confirm outside a repeat region. |
| **PP2** | Applied (Supporting) | Missense in a missense-constrained gene (gnomAD missense Z ≥ 3.09). |
| **PP3** | Applied (Supporting / Moderate / Strong) | REVEL ≥ 0.644 / 0.773 / 0.932; or SpliceAI max Δ ≥ 0.2 (Supporting) / ≥ 0.5 (Moderate); or AlphaMissense = likely pathogenic. Flags **BP4** *argues against*. |
| **BP4** | Applied (Supporting / Moderate / Strong) | REVEL ≤ 0.290 / 0.183 / 0.016 (with SpliceAI < 0.1); or AlphaMissense = likely benign. Flags **PP3** *argues against*. |
| **BP7** | Applied (Supporting) | Synonymous variant with SpliceAI max Δ < 0.1 (no predicted splice impact). |
| **PP5** | Applied (Supporting) | ClinVar reports this exact variant pathogenic / likely pathogenic. Flags **BP6** *argues against*. |
| **BP6** | Applied (Supporting) | ClinVar reports this exact variant benign / likely benign. Flags **PP5** *argues against*. |
| **PP4** | Applied (Supporting / Moderate) | Phenotype specific for the gene. With a Monarch gene↔proband match score: ≥ 0.6 Moderate, ≥ 0.3 Supporting. Without a score, falls back to direct proband-HPO ∩ gene-HPO overlap at Supporting. Auto-capped at Moderate; raise to Strong manually for a highly specific single-gene phenotype. |
| **PM6** | Applied (Moderate) | Trio: present in the proband, absent in **both** sequenced parents (assumed de novo; parentage not molecularly confirmed — upgrade to PS2 manually if it is). |
| **PP1** | Consider (Supporting) | Variant carried by ≥ 2 affected family members (cosegregation). |
| **BS4** | Consider (Strong) | An affected relative does **not** carry the variant (lack of segregation). |

The **REVEL → strength** thresholds follow the ClinGen Sequence Variant
Interpretation calibration (2022) — PP3 applies at the `≥` cuts above and BP4 at
the `≤` cuts. Note that PP3 and BP4 are mutually exclusive by construction: applying
one flags the other *argues against*, and the same mutual-contradiction holds for
the PP5/BP6 ClinVar pair.

---

## Exclusion rules (greyed as "not applicable")

Criteria that cannot apply to the variant in front of you are greyed so the working
set stays honest. They remain clickable for manual override.

**By molecular consequence** — only the criteria relevant to the variant's class
stay active:

| Consequence class | Greyed (not applicable) |
| --- | --- |
| Missense | PVS1, PM4, BP3, BP7 |
| Loss-of-function | PP2, PM5, BP1, BP7, BP3 |
| Synonymous | PVS1, PM4, PP2, PM5, BP1, BP3 |
| In-frame / length-changing | PVS1, PP2, PM5, BP1, BP7 |
| Splice-region | PVS1, PP2, PM5, BP1, PM4 |

**By population frequency** — only the frequency criterion matching the observed
gnomAD band stays active; a borderline band leaves all three greyed:

| Allele frequency | Active | Greyed |
| --- | --- | --- |
| ≥ 5% | BA1 | PM2, BS1 |
| 1% … 5% | BS1 | BA1, PM2 |
| < 1×10⁻⁴ or absent | PM2 | BA1, BS1 |
| 1×10⁻⁴ … 1% (borderline) | — | BA1, BS1, PM2 |
| No homozygotes in gnomAD | — | BS2 |

**By in-silico and family availability:**

| Condition | Greyed |
| --- | --- |
| No REVEL / SpliceAI / AlphaMissense prediction | PP3, BP4 |
| No complete trio (missing parental genotypes) | PS2, PM6 |
| Variant inherited from a parent | PS2, PM6 |
| No additional affected carrier | PP1 |
| No affected relative lacking the variant | BS4 |

---

## Left for manual review

Some criteria need information CoGA does not hold and are **always** left for you to
apply by hand — they are never auto-positioned:

- **PS1 / PM5** — same / different change at an amino-acid residue already
  established pathogenic. Needs a residue-level ClinVar index, which CoGA does not
  hold; it only stores the variant's own ClinVar significance.
- **PS3 / BS3** — functional studies.
- **PS4** — case–control prevalence / enrichment in affecteds.
- **PM1** — mutational hotspot / functional domain.
- **PM3 / BP2** — in-trans / in-cis phasing with a known pathogenic variant.

These are the deliberate gaps where the evidence is real but not machine-available,
and where over-eager automation would be wrong. The de-novo criteria (PS2, PM6) are
*conditionally* manual: PM6 auto-applies for an apparent trio de novo, but PS2 (the
*confirmed* upgrade) is left to you because parentage is not molecularly verified.

---

## Mitochondrial (mtDNA) variants

Opening **ACMG classify** on a variant from the family **mtDNA analysis** routes to
a dedicated mt-specific evaluator (after the ClinGen/Wong–McCormick 2020 mtDNA
specifications) instead of the nuclear one. Selection is by `chr === 'MT'`. The
points scale, the five classes and the VUS sub-tiers are identical — only the
pre-evaluation changes, because mtDNA is haploid and maternally inherited and the
nuclear in-silico predictors are not computed for it.

| Criterion | mtDNA behaviour |
| --- | --- |
| **PVS1** | Applies only to predicted-null changes in a **protein-coding** mt gene; *not applicable* for tRNA / rRNA / control-region loci. |
| **PM2 / BS1 / BA1** | gnomAD-MT thresholds: BA1 ≥ 0.5% (stand-alone), BS1 ≥ 0.02%, PM2 below 0.002% / absent. A MITOMAP common polymorphism or haplogroup marker is routed to **BS1**. |
| **PP5 / BP6** | From MITOMAP / ClinVar significance (pathogenic → PP5; benign / polymorphism → BP6), mutually contraindicating. |
| **PP3 / BP4** | *Not applicable* — the mt predictors (MitoTIP / APOGEE / HmtVar) are not yet loaded, so nothing is auto-applied. |
| **PM1** | *Consider* (Moderate) for tRNA loci. |
| **PS2 / PM6** | *Not applicable* — maternally inherited, so de novo does not apply. |
| **PP1 / BS4** | Maternal segregation from the maternal-line calls and heteroplasmy/zygosity (≥ 2 affected maternal carriers → PP1; an affected relative lacking it → BS4). |
| **PP4** | Proband HPO ∩ gene HPO, noting the proband's heteroplasmy level. |
| Nuclear-only (PP2, PM5, PM3, PM4, BP1–3, BP7, BS2) | *Not applicable*. |

The mt context that drives these rules — locus category, MITOMAP status,
disorders, maternal transmission, haplogroup, and per-call heteroplasmy — is
carried alongside the variant and assembled from the mtDNA analysis page.

---

## External evidence links

The modal header carries quick links for the variant — **gnomAD**, **ClinVar** and
**DECIPHER** (the same URLs as the variant card), plus a **Smart PubMed** search
built as `gene (OR protein change) AND ("HPO term 1" OR "HPO term 2" …)` from the
proband's present HPO terms, to check whether the gene–phenotype association has
been published.
