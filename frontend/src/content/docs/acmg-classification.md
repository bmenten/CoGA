# Semi-automatic ACMG classification — reference

The **ACMG classify** dialog pre-evaluates the ACMG/AMP 2015 criteria for a small variant from the
data CoGA already holds. It scores them on the ClinGen points scale and lets you confirm or change
every criterion before you save. It is decision support, not an autoclassifier: nothing counts until
you accept it, and the server recomputes the class when you save.

How to use the dialog is in the [user guide](/docs), section *Interpretation and review*. This page
holds the rules. The ClinGen classifier for copy-number variants is at the end.

---

## From points to a class

Each criterion you accept adds points by its strength. Benign criteria subtract them.

| Strength | Pathogenic | Benign |
| --- | ---: | ---: |
| Supporting | +1 | −1 |
| Moderate | +2 | −2 |
| Strong | +4 | −4 |
| Very strong | +8 | — |

| Points | Class (review tag) |
| --- | --- |
| 10 or more | Pathogenic - class 5 |
| 6 to 9 | Likely Pathogenic - class 4 |
| 0 to 5 | VUS - class 3 |
| −1 to −6 | Likely benign - class 2 |
| −7 or less | Benign - class 1 |

**BA1 overrides the points.** An accepted BA1 (allele frequency 5% or more) makes the variant Benign,
whatever the other criteria say.

When you save, CoGA writes the class to the variant as its classification and as the matching review
tag. It also stores every criterion with its strength and your note, for the audit trail (see the
[traceability reference](/docs/reference/clinical-traceability)). If you accept no criterion, no class
is saved.

### VUS tiers: hot, warm, cold

The VUS band is split by how close the points are to Likely Pathogenic (6), following the MAGI-ACMG
approach.

| Points | Tier (review tag) | Reading |
| --- | --- | --- |
| 4 or 5 | VUS — Hot | 1–2 points short of Likely Pathogenic. Worth chasing more evidence. |
| 2 or 3 | VUS — Warm | Mixed or partial evidence. |
| 0 or 1 | VUS — Cold | Little pathogenic support. |

The tier shows on the score bar and is saved as a review tag next to *VUS - class 3*, so you can filter
on it. A variant that leaves the VUS band loses its tier tag.

---

## The four states of a criterion

| State | On screen | Meaning |
| --- | --- | --- |
| Applied | checked | The data supports it. It counts toward the score. |
| Consider | ● | A relevant but not decisive signal. Left unchecked for you to decide. |
| Argues against | ✕ | The data points the other way. Left unchecked. |
| Not applicable | n/a, greyed | Cannot apply to this variant. Still clickable. |

Hover a criterion to see the evidence behind its state. You can change every state and every strength.

**When a lookup fails.** If the gene profile or the family's HPO terms cannot be loaded, the dialog
says so and offers **Retry**. PVS1, BS2 and PP4 then say *not assessed* and are offered as Consider.
They are never scored as negative evidence.

---

## What CoGA pre-evaluates

The rules read the variant's annotation (consequence, gnomAD, in-silico scores, ClinVar), the gene
profile (ClinGen dosage, GenCC inheritance modes, the gene's HPO terms), the family genotypes and the
proband's HPO terms. The proband is the member with the proband role, otherwise the first affected
member.

| Criterion | State | Rule |
| --- | --- | --- |
| **PVS1** | Applied or Consider | Predicted null: stop gained, frameshift, canonical splice acceptor or donor, start lost, transcript ablation. Applied only when ClinGen scores the gene's haploinsufficiency as *Sufficient evidence*: Very strong with a LOFTEE high-confidence call, Strong without. Otherwise offered as Consider. |
| **PM2** | Applied (Supporting) | gnomAD allele frequency below 0.01%, or no gnomAD frequency at all (see *Frequency* below). |
| **BA1** | Applied (Stand-alone) | gnomAD allele frequency 5% or more. |
| **BS1** | Applied (Strong) | gnomAD allele frequency from 1% up to 5%. |
| **BS2** | Applied (Strong) or Consider | Homozygotes in gnomAD. Applied when GenCC lists a recessive inheritance mode for the gene, otherwise Consider (Supporting). |
| **PM4** | Consider (Moderate) | In-frame insertion or deletion, stop lost, or another protein-length change. Check that it lies outside a repeat region. |
| **PP2** | Applied (Supporting) | Missense in a missense-constrained gene (gnomAD missense Z of 3.09 or more). |
| **PP3 / BP4** | Applied, strength scaled | See *In-silico evidence* below. |
| **BP7** | Applied (Supporting) | Synonymous, with SpliceAI below 0.1 or no SpliceAI score. A higher SpliceAI score marks BP7 as argues against. |
| **PP5** | Applied (Supporting) | ClinVar lists this variant as pathogenic or likely pathogenic. BP6 is then marked argues against. |
| **BP6** | Applied (Supporting) | ClinVar lists this variant as benign or likely benign. PP5 is then marked argues against. |
| **PP4** | Applied (Supporting or Moderate) | See *Phenotype (PP4)* below. |
| **PM6** | Applied or Consider (Moderate) | Present in the proband and absent in both parents (assumed de novo). See *De novo (PM6)* below. |
| **PP1** | Consider (Supporting) | Carried by 2 or more affected family members. |
| **BS4** | Consider (Strong) | An affected family member is genotyped and does not carry the variant. |

A ClinVar record with *Conflicting classifications of pathogenicity* is neither pathogenic nor benign,
so it pre-applies neither PP5 nor BP6. The variant marks and the ranking read ClinVar the same way.

### Frequency (PM2, BS1, BA1)

The frequency criteria read the overall gnomAD allele frequency from the annotated VCF. When the VCF
carries only the exome and genome frequencies, CoGA uses the higher of the two. They do not use the
highest sub-population frequency (popmax).

Only the criterion that fits the frequency stays active; the other two are greyed:

| gnomAD allele frequency | Active | Greyed |
| --- | --- | --- |
| 5% or more | BA1 | PM2, BS1 |
| 1% up to 5% | BS1 | BA1, PM2 |
| below 0.01%, or none | PM2 | BA1, BS1 |
| 0.01% up to 1% | none | BA1, BS1, PM2 |

A variant with no gnomAD frequency counts as absent from gnomAD, so PM2 is pre-applied. Check that the
VCF was annotated against gnomAD before relying on it.

### In-silico evidence (PP3, BP4)

REVEL decides first, with the ClinGen 2022 calibrated thresholds:

- **REVEL 0.644 or more** → PP3: Supporting from 0.644, Moderate from 0.773, Strong from 0.932 (or the
  SpliceAI strength below, if that is higher). BP4 is marked argues against.
- **REVEL 0.290 or less, with SpliceAI below 0.1** → BP4: Supporting up to 0.290, Moderate up to 0.183,
  Strong up to 0.016. PP3 is marked argues against.

When REVEL does not decide:

- **SpliceAI** 0.2 or more → PP3 Supporting; 0.5 or more → PP3 Moderate.
- Otherwise the **AlphaMissense** class: likely pathogenic → PP3 Supporting; likely benign → BP4
  Supporting.

Only a REVEL-based call marks the opposite criterion as argues against. With no REVEL, SpliceAI or
AlphaMissense value, PP3 and BP4 are not applicable. A REVEL value between the thresholds, with no other
signal, leaves both open.

### Phenotype (PP4)

When the variant list is ranked with the **Phenotype priority** preset, each variant carries a Monarch
phenotype-match score from 0 to 1 (see the [Monarch reference](/docs/reference/monarch-integration)):

- **0.6 or more** → PP4 Moderate.
- **0.3 or more** → PP4 Supporting.

Without a usable score (none, or below 0.3), CoGA looks for an HPO term that is on both the proband's
list and the gene's list, and applies PP4 Supporting when it finds one. CoGA never pre-applies PP4 above
Moderate: raise it to Strong yourself for a highly specific, single-gene phenotype.

### De novo (PM6)

PM6 compares the proband with the proband's own parents: the father and mother that the pedigree links
to the proband. The member roles do not decide it, because a grandparent can hold the father or mother
role too. The rules are those of the de novo inheritance filter.

- Both parents called reference with 8 reads or more, and the proband heterozygous → PM6 applied
  (assumed de novo). A call without a reported depth counts as enough. Upgrade to PS2 yourself if
  parentage is confirmed.
- Both parents reference, but a parent has fewer than 8 reads → PM6 Consider. At that depth a parent
  can carry the variant without it being called. Check the parents' reads.
- Both parents reference, but the proband is homozygous → PM6 Consider. A de novo event changes one
  copy, so something else explains the second: a deletion of the other allele, uniparental disomy or a
  genotyping error.
- A parent carries it → PS2 and PM6 not applicable (inherited).
- A parent's genotype is missing → PS2 and PM6 not applicable (cannot be assessed).
- The pedigree does not link the proband to both parents → PS2 and PM6 not applicable (cannot be
  assessed).
- For a son on chrX or chrY outside the pseudo-autosomal regions, only the parent who passes on that
  chromosome counts: the mother for the X, the father for the Y. The variant must be absent in that
  parent, with 8 reads or more, and not called in the other. The son's own call can be `1`, `1/1` or
  `0/1`.

---

## Greyed as not applicable

Criteria that cannot apply to the variant are greyed, so the working set stays honest. They stay
clickable.

| When… | Greyed |
| --- | --- |
| Missense | PVS1, PM4, BP3, BP7 |
| Loss of function | PP2, PM5, BP1, BP3, BP7 |
| Synonymous | PVS1, PM4, PP2, PM5, BP1, BP3 |
| In-frame or other length change | PVS1, PP2, PM5, BP1, BP7 |
| Splice region or intronic | PVS1, PM4, PP2, PM5, BP1 |
| Frequency outside a criterion's band | the frequency criteria that do not fit (table above) |
| No homozygotes in gnomAD | BS2 |
| No REVEL, SpliceAI or AlphaMissense value | PP3, BP4 |
| A parent's genotype is missing | PS2, PM6 |
| The pedigree does not link the proband to both parents | PS2, PM6 |
| Inherited from a parent | PS2, PM6 |
| Fewer than 2 affected carriers | PP1 |
| No genotyped affected member without the variant | BS4 |

---

## Left for you

CoGA never pre-evaluates these, because it does not hold the evidence. Apply them by hand:

- **PS1 / PM5** — the same or a different change at an amino-acid residue known to be pathogenic.
  CoGA stores only the variant's own ClinVar status, not a residue-level index.
- **PS2** — confirmed de novo (CoGA only offers the assumed de novo, PM6).
- **PS3 / BS3** — functional studies.
- **PS4** — prevalence in affected individuals versus controls.
- **PM1** — mutational hotspot or functional domain.
- **PM3 / BP2** — in trans or in cis with a known pathogenic variant.
- **BP1** — missense in a gene where truncating variants cause disease.
- **BP3** — in-frame change in a repeat region without known function.
- **BP5** — an alternative molecular cause of disease was found.

---

## Mitochondrial (mtDNA) variants

**ACMG classify** on a variant from the family's **mtDNA analysis** page uses an mtDNA rule set, after
the ClinGen mtDNA specifications (McCormick et al. 2020). The mitochondrial genome is haploid and
maternally inherited, so the nuclear rules do not fit. The points, classes and VUS tiers are the same;
only the pre-evaluation differs.

| Criterion | mtDNA rule |
| --- | --- |
| **PVS1** | Very strong for a predicted-null change in a protein-coding mt gene. Not applicable at tRNA, rRNA and control-region loci. Consider when the locus type is unknown. |
| **PM2 / BS1 / BA1** | gnomAD mtDNA thresholds: BA1 0.5% or more (stand-alone), BS1 0.02% or more, PM2 below 0.002% or absent. A MITOMAP polymorphism or haplogroup marker goes to BS1. |
| **PP5 / BP6** | From the MITOMAP or ClinVar status: pathogenic → PP5; benign or polymorphism → BP6. A conflicting ClinVar record reads as uncertain: neither. |
| **PP3 / BP4** | Not applicable: the mtDNA predictors (MitoTIP, APOGEE, HmtVar) are not loaded. Assess by hand. |
| **PM1** | Consider (Moderate) at tRNA loci. |
| **PS2 / PM6** | Not applicable: maternally inherited. |
| **PP1 / BS4** | Maternal segregation. Consider PP1 when 2 or more affected family members carry it and the mtDNA page reads *Maternal transmission*: the proband's mother, as the pedigree links her, carries it, and so does the proband or a sibling of the same mother. Consider BS4 when an affected member does not carry it. Both count every affected member, also relatives outside the maternal line: check that they share the proband's mtDNA. |
| **PP4** | Supporting when an HPO term is on both the proband's and the gene's list; the evidence text notes the proband's heteroplasmy. |
| PP2, PM3, PM4, PM5, BP1, BP2, BP3, BP7, BS2 | Not applicable. |

---

## External evidence links

The dialog header links the variant to **gnomAD**, **ClinVar** and **DECIPHER**, and to a **PubMed**
search that combines the gene (or protein change) with the proband's HPO terms. Use it to check whether
the gene–phenotype link has been published.

---

## Copy-number variants (ClinGen CNV classifier)

On the structural-variant page, **ACMG (CNV)** opens a separate classifier that follows the ClinGen /
ACMG technical standard for copy-number variants (Riggs et al. 2020). Choose **Copy-number loss** or
**Copy-number gain** at the top: the two have different criteria and point values. CoGA picks gain for a
duplication or insertion and loss otherwise.

Each criterion carries a point value, some with a range you can adjust. The server keeps each value
within its allowed range and recomputes the total when you save.

| Points | Class |
| --- | --- |
| 0.99 or more | Pathogenic - class 5 |
| 0.90 to 0.98 | Likely Pathogenic - class 4 |
| −0.89 to 0.89 | VUS - class 3 |
| −0.98 to −0.90 | Likely benign - class 2 |
| −0.99 or less | Benign - class 1 |

CoGA pre-selects only what the structural-variant record shows. Everything else is yours to add.

| Pre-selected | When |
| --- | --- |
| 1A (0) or 1B (−0.60) | The event does, or does not, overlap a protein-coding gene. |
| Section 3 gene count | For a deletion or duplication: losses 25–34 genes +0.45, 35 or more +0.90; gains 35–49 genes +0.45, 50 or more +0.90. For an inversion, translocation or insertion only the genes a breakpoint disrupts count, so CoGA leaves 3A for you to adjust. |
| 2H (+0.15) | A loss in a gene with pLI 0.9 or more. |
| 5B (+0.30) | The SV annotation says de novo (assumed; use 5A if confirmed). |
| 5F (0) | The SV annotation says inherited. |

The pre-selected criteria start accepted. Untick any that do not fit.
