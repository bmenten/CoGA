# Phenotype matching with Monarch — reference

CoGA links a patient's recorded **HPO phenotypes** to genes and diseases through the
[Monarch Initiative](https://monarchinitiative.org/) knowledge graph. This tells you which genes and
diseases fit the case, and it feeds the variant ranking and the ACMG criterion PP4.

How to use it in a case is in the [user guide](/docs), section *Phenotypes, panels and phenotype matching*.
This page explains what the graph links, how the ranking works, and why the phenotypes you record change
the answer.

---

## What Monarch links

Monarch joins three kinds of entity: **gene ↔ disease ↔ HPO phenotype**. It brings many curated
sources together under stable identifiers (`HGNC:` for genes, `MONDO:` for diseases, `HP:` for
phenotypes). The links CoGA uses come from:

| Link | Sources in the files CoGA loads |
| --- | --- |
| Gene → disease | OMIM, ClinGen, Orphanet |
| Disease → phenotype | HPO annotations (HPOA) |

Every association CoGA shows names its relationship and its source.

---

## On the gene profile

Open a gene in the **Gene Explorer**. The **Monarch gene–disease associations** list shows each disease
linked to the gene, with:

- the **relationship**: *Causes*, *Associated*, *Contributes to* or *Increased likelihood*, so a
  definitive disease gene reads differently from a weak or risk-modifying link;
- the **source(s)** that asserted it;
- the number of **phenotypes** expected for the disease, as a caption such as *"106 phenotypes"*;
- a link to the disease page on the Monarch website.

Diseases that the gene *causes* are listed first.

**Patient overlap.** When you open the gene from a family (for example from a variant), the caption
also says how many of the expected phenotypes the family shows (*"3 observed in family"*), with the
matched terms as chips. Matching follows the HPO hierarchy: a patient's specific term matches a
disease's more general expected term. A phenotype that a disease is recorded as *not* having never
counts as a match.

---

## In the family: ranked candidate genes

The family page has a **Phenotype match (Monarch)** panel. **Find candidate genes** sends the family's
observed HPO terms — every member's, not only the affected members' — to Monarch's similarity service.
Monarch returns up to 50 genes, ranked by how well each gene's phenotype profile matches those terms.

- Genes that exist in CoGA link straight to their gene profile.
- The panel calls Monarch only when you press the button, so it never slows down opening a family.
  Each press, **Re-run match** included, runs the match again on the phenotypes recorded now. Monarch's
  answer for the same terms is reused for a while, so a re-run on unchanged terms gives the same
  ranking.
- A match that fails, for example because Monarch cannot be reached, is said as a failure with its
  reason, and no ranking is shown; **Re-run match** tries again.
- HPO terms recorded on unaffected relatives are part of the query too. Keep that in mind when you read
  the ranking.

---

## How the ranking works

The ranking is not a yes/no "is this gene linked to this term" lookup. CoGA and Monarch compare the
gene's expected phenotypes (the HPO terms of all the diseases linked to it) with the patient's whole
set of observed terms, following the HPO hierarchy.

The key is **information content**: a phenotype that occurs in only a few diseases is very informative
and counts heavily; a phenotype shared by many diseases barely counts. *Intellectual disability* is one
of the broadest terms in the ontology and is linked to over a thousand genes, so on its own it hardly
separates them: the scores are close and the order is close to arbitrary. That is expected, not a
fault.

The ranking sharpens as you add the patient's more specific features — a particular seizure type, a
dysmorphic feature, a metabolic finding, an MRI abnormality. The genes whose diseases explain those
features rise; the genes that share only the broad term fall away.

> **Practical takeaway.** One broad term gives a weak ranking by design. Record the affected
> individual's distinctive phenotypes next to the umbrella term: the more specific the HPO terms, the
> more meaningful the ranking.

---

## How it feeds variant ranking and PP4

The **Phenotype priority** preset on the small-variant page scores every gene with a candidate variant
against the **affected** members' HPO terms (all members' terms if nobody is marked affected). This runs
inside CoGA, without a call to Monarch, using the same information-content weighting. The result is the
gene's phenotype score (0 to 1), which is combined with the variant's own score into the ranking.

The same phenotype score sets the strength of the ACMG criterion **PP4** (0.6 or more Moderate, 0.3 or
more Supporting). The matched phenotypes shown in the score breakdown are what you cite when you apply
PP4. See the [ACMG classification reference](/docs/reference/acmg-classification).

---

## Where the data comes from

| Surface | Source | Freshness |
| --- | --- | --- |
| Gene profile associations, Mendeliome panel, variant ranking, PP4 | Monarch release loaded into CoGA by an administrator | as of the last load |
| Candidate-gene panel (family page) | a live call to Monarch's similarity service | at the time you press the button |

An administrator loads the Monarch release from **Admin → Monarch Data**, about monthly. Loading it
also rebuilds the Mendeliome panel. The release is recorded, so the report's provenance footer shows
which Monarch release backed an interpretation.

Until the release is loaded, the gene profile shows no Monarch associations and the ranking has no
phenotype signal. The candidate-gene panel does not depend on the load; when Monarch cannot be reached
it says the service is unavailable.
