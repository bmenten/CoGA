CoGA integrates the [Monarch Initiative](https://monarchinitiative.org/) knowledge graph, which
links **genes ↔ diseases ↔ HPO phenotypes** from curated sources (OMIM, ClinGen, Orphanet, and the
Human Phenotype Ontology Annotations). This turns the patient’s recorded phenotype into an active
signal: it tells you which genes and diseases are phenotypically relevant, and it powers the
automatic variant ranking described in the next section.

> **The loop in one sentence.** Observed phenotypes → candidate genes → each gene’s diseases → which
> of the patient’s phenotypes those diseases explain → ranked variants.

### On the gene profile

The Gene Explorer profile gains two phenotype-aware blocks:

- **Monarch gene–disease associations** — the curated diseases linked to the gene, each labelled
  with the relationship (*causes*, *associated*, *contributes to*) and its source(s), and linking
  out to the Monarch page for the disease. Causal associations are listed first.
- **Expected phenotypes and patient overlap** — each disease carries the HPO phenotypes expected for
  it. When you open a gene *in the context of a family* (for example by clicking a gene from a
  variant), CoGA highlights the expected phenotypes the family actually exhibits. Matching is
  **ancestor-aware**: a patient’s specific term (e.g. a particular seizure type) still matches a
  disease’s more general expected term (e.g. *Seizure*) through the HPO hierarchy.

### In the family: ranked candidate genes

The family workspace has a **Phenotype match (Monarch)** panel. Press *Find candidate genes* and
CoGA sends the affected individuals’ observed HPO terms to Monarch’s semantic-similarity service,
which returns a list of genes ranked by how well their phenotype profile matches the patient. Genes
that exist in your platform link straight to the gene profile (where the gene–disease and overlap
blocks above are waiting), so you can move from “these phenotypes” to “this gene” to “this variant”
without leaving the case.

> **It runs on demand.** The candidate-gene panel calls Monarch only when you press the button, so
> it never slows down opening a family. Results are cached briefly.

### How the ranking works — and why specific phenotypes matter

The ranking is **not** a yes/no “is this gene linked to this term” lookup — if it were, a broad term
such as *Intellectual disability* (linked to well over a thousand genes) would return a flat,
unordered list. Instead, each candidate gene gets a **phenotypic-similarity score**: CoGA compares
the gene’s expected phenotype profile (the HPO terms of all the diseases Monarch links to it)
against the patient’s *whole* set of observed terms, and walks the HPO hierarchy so a specific
patient term still matches a more general expected one (and vice versa).

The decisive ingredient is **information content**: a phenotype that occurs in only a handful of
diseases is highly informative and counts heavily, while a phenotype shared by a large fraction of
diseases carries almost no weight. *Intellectual disability* is one of the broadest terms in the
ontology, so on its own it barely separates those thousand-plus genes — they all look about equally
good, the score differences are tiny, and the order you see is close to arbitrary (and capped at the
top \~50 genes Monarch returns). This is expected, not a fault: a single very general term simply
does not carry enough information to rank on.

The ranking sharpens as you add the patient’s **more specific, co-occurring features** — a
particular seizure type, a dysmorphic feature, a metabolic finding, an abnormal MRI. Each specific
term is highly informative, so the genes whose diseases actually explain those features rise to the
top, while the genes that only share the generic *Intellectual disability* term fall away. The same
principle drives the local phenotype score used for [variant
prioritisation](#variant-prioritisation) (rarer, more specific shared phenotypes count more).

> **Practical takeaway.** One broad term gives a weak ranking by design. Record the affected
> individual’s distinctive phenotypes alongside the umbrella term — the more specific HPO you
> provide, the more meaningful (and trustworthy) the gene ranking becomes.

### Where the data comes from

The gene–disease and disease–phenotype tables are loaded by an administrator from the monthly
Monarch release (see [Administration](#administration)); the candidate-gene panel is a live call to
Monarch. If the tables have not been loaded yet, the profile blocks show an empty state rather than
an error.

[Phenotype matching with Monarch reference (the graph, ranking, and PP4)](/docs/reference/monarch-integration "further-reading")
