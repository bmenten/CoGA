# Phenotype matching with Monarch — reference

CoGA connects a patient's recorded **HPO phenotypes** to genes and diseases through the
[Monarch Initiative](https://monarchinitiative.org/) knowledge graph. This turns the
phenotype list from passive metadata into an active signal: it tells you which genes and
diseases are phenotypically relevant to your case, and it feeds the automatic variant
ranking. This page explains the mechanics — what the graph links, how the ranking is
computed, and why the phenotypes you record change the answer.

This is the in-depth reference. For the workflow-level overview see the [in-app user guide](/docs) section *Phenotype matching (Monarch Initiative)*.

---

## What Monarch links, and from where

The Monarch knowledge graph is a single, curated network of three entity types joined by
typed relationships:

```
gene  ↔  disease  ↔  HPO phenotype
```

It normalises roughly **30 upstream sources** into one graph keyed by stable identifiers,
so the genes, diseases and phenotypes line up directly with the identifiers CoGA already
stores (`HGNC:` for genes, `MONDO:` for diseases, `HP:` for phenotypes). The sources that
matter for interpretation are:

| Link | Curated source(s) |
| --- | --- |
| Gene → disease | OMIM, ClinGen, Orphanet, GenCC |
| Disease → phenotype | Human Phenotype Ontology Annotations (HPOA) |

Because the data is curated and provenance-bearing, every association CoGA shows you names
the relationship and the source it came from — you are never looking at an undifferentiated
"match".

---

## On the gene profile

Open any gene in the **Gene Explorer** and the disease panel gains two Monarch-backed
blocks.

### Gene–disease associations

The curated diseases linked to the gene, each carrying:

- the **relationship** — *causes*, *associated with*, or *contributes to* — so a
  definitive disease gene reads differently from a weak or contributory link;
- the **source(s)** that asserted it (OMIM, ClinGen, Orphanet, GenCC), shown as a caption;
- a link out to the Monarch page for that disease.

**Causal associations are listed first.** When a gene is asserted as *causing* a disease by
any source, that pair sorts above merely associated or contributory ones, so the strongest
relationship is the one you see first.

### Expected phenotypes and patient overlap

Each linked disease carries the **HPO phenotypes expected for it** (from HPOA), shown as a
count. When you open a gene **in the context of a family** — for example by clicking through
from a variant — CoGA additionally highlights which of those expected phenotypes the family
actually exhibits, rendered as a caption such as *"106 phenotypes · 3 observed in family"*
with the matched terms as chips.

Matching is **ancestor-aware**. A patient's specific observed term still matches a disease's
more general expected term through the HPO hierarchy: if the disease expects *Seizure* and
the patient has a particular seizure subtype, that counts as a match. CoGA expands the
family's observed terms up the ontology and treats an expected phenotype as observed when
the family has that exact term *or any more specific descendant* of it. Phenotypes a disease
is explicitly recorded as **not** presenting are kept distinct, never counted as a match.

---

## In the family: ranked candidate genes

The family workspace has a **Phenotype match (Monarch)** panel. Press **Find candidate
genes** and CoGA collects the affected individuals' observed HPO terms (or a single member's,
if you scope it that way) and sends them to Monarch's semantic-similarity service, which
returns a list of **genes ranked by how well their phenotype profile matches the patient**.

- Genes that already exist in your platform are flagged and link straight to the gene
  profile — where the gene–disease and overlap blocks above are waiting — so you can move
  from *these phenotypes* to *this gene* to *this variant* without leaving the case.
- The panel runs **on demand**: Monarch is called only when you press the button, so it
  never slows down opening a family. Results are cached briefly.

This closes the loop: **observed phenotypes → candidate genes → each gene's diseases → which
of the patient's phenotypes those diseases explain → ranked variants.**

---

## How the ranking works — and why specific phenotypes matter

The ranking is **not** a yes/no "is this gene linked to this term" lookup. If it were, a
broad term such as *Intellectual disability* — linked to well over a thousand genes — would
return a flat, unordered list. Instead, each candidate gene is given a **phenotypic-similarity
score**: CoGA compares the gene's expected phenotype profile (the HPO terms of every disease
Monarch links to it) against the patient's *whole* set of observed terms, walking the HPO
hierarchy in both directions so a specific patient term still matches a more general expected
one and vice versa.

The decisive ingredient is **information content (IC)**. Each phenotype is weighted by how
specific it is — formally `IC = −ln(p)`, where `p` is the fraction of diseases annotated to
that term or any of its descendants:

- A phenotype that occurs in only a handful of diseases has a small `p`, a high IC, and
  **counts heavily**.
- A phenotype shared by a large fraction of diseases has a large `p`, a near-zero IC, and
  **barely counts at all**.

This is why a **single broad term ranks poorly by design**. *Intellectual disability*
(HP:0001249) is one of the broadest terms in the ontology and is linked to over a thousand
genes; on its own it carries almost no information, so every linked gene scores about the
same, the differences are tiny, and the order you see is close to arbitrary (and capped at
the top ~50 genes Monarch returns). That is the expected behaviour of an IC-weighted
similarity, not a fault.

The ranking **sharpens as you add the patient's more specific, co-occurring features** — a
particular seizure type, a dysmorphic feature, a metabolic finding, an abnormal MRI. Each
specific term is highly informative, so the genes whose diseases actually explain those
features rise to the top while the genes that share only the generic umbrella term fall away.

> **Practical takeaway.** One broad term gives a weak ranking by design. Record the affected
> individual's distinctive phenotypes alongside the umbrella term — the more specific HPO you
> provide, the more meaningful and trustworthy the gene ranking becomes.

---

## How it feeds variant prioritisation and PP4

The same phenotype signal drives CoGA's automatic, Exomiser-style variant ranking. When you
apply the **Phenotype priority** preset (or pass the prioritise option) to a family's small
variants, every candidate gene with a variant gets a **local phenotype score** — a
best-match-average between the affected individuals' HPO terms and the gene's Monarch
phenotype profile, weighted by the same information content. Unlike the on-demand candidate
panel (which returns Monarch's top ~50 genes over a live call), this scores **every** gene in
the candidate set with no per-request network call, then folds phenotype relevance together
with variant impact, rarity, segregation and quality into a combined score and a sortable
**Score** column. Variants in genes whose diseases explain the patient's phenotypes rise;
genes with no phenotype link are pushed down without burying genuinely novel candidates.

This is also the evidence behind the **PP4** ACMG criterion — *the patient's phenotype is
highly specific for a disease with a single genetic aetiology*. A strong, specific phenotype
match (driven by high-IC shared terms, not a broad umbrella term) is exactly the supporting
evidence PP4 is meant to capture, and the matched phenotypes shown in the variant review
breakdown are what you cite when applying it. For how PP4 sits within the full classification
ruleset, see the [ACMG classification reference](/docs/reference/acmg-classification).

---

## Where the data comes from

There are two distinct data paths, and it helps to know which is which:

| Surface | How it is sourced | Freshness |
| --- | --- | --- |
| Gene–disease & disease–phenotype blocks (gene profile) | Loaded into CoGA by an administrator from the **monthly Monarch release** | As of the last load |
| Candidate-gene panel (family) | A **live call** to Monarch's similarity service when you press the button | Real-time |

The gene profile blocks read from tables an administrator refreshes once per Monarch release
(monthly cadence); the release version is recorded so the report's provenance footer can show
exactly which Monarch release backed an interpretation. **Until that load has been run, the
profile blocks show an empty state rather than an error** — if a gene you expect to have
associations looks blank, the most likely cause is that the Monarch tables have not yet been
loaded in your environment. The candidate-gene panel does not depend on that load; it queries
Monarch directly, and surfaces a clean "unavailable" message if the service cannot be reached.
