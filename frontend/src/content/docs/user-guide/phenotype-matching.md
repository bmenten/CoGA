The patient's phenotype drives the ranking. Record it as HPO terms, and use gene panels to focus a
search.

### Recording phenotypes

HPO terms are kept per person. An administrator adds them: click the person's sample name on the family
page, and under **HPO Phenotypes** search a term, choose *present*, *absent* or *unknown*, and press
**Add phenotype**. Only *present* terms are used for matching.

### Gene panels

The **Panel catalog** holds reusable gene lists; administrators create them or import them from
PanelApp. Choose a panel in the **Locations** filter of the small-variant or structural-variant page to
limit a search to its genes. The **Mendeliome** — every gene Monarch links to a disease — is the default
scope of both pages.

### Phenotype matching

CoGA uses the [Monarch Initiative](https://monarchinitiative.org/) knowledge graph, which links genes,
diseases and HPO phenotypes.

- **On the gene profile** (Gene Explorer), *Monarch gene–disease associations* lists each linked
  disease with its relationship, source and number of expected phenotypes. Opened from a family, it also
  says how many the family shows (*"3 observed in family"*).
- **On the family page**, the **Phenotype match (Monarch)** panel ranks candidate genes when you press
  **Find candidate genes**. It sends the HPO terms of every family member, not only the affected ones.
  **Re-run match** runs it again on the phenotypes recorded now, for example after you added one.
- **In the variant list**, the *Phenotype priority* preset ranks variants partly by how well their gene
  matches the affected members' phenotypes (see [Small-variant prioritisation](#small-variant-filtering)).

> **Record specific phenotypes.** A broad term such as *Intellectual disability* is linked to over a
> thousand genes and gives a weak ranking by design. Add the patient's distinctive features — a
> particular seizure type, a dysmorphic feature, a metabolic finding — and the ranking sharpens.

An administrator loads the Monarch data about monthly (**Admin → Monarch Data**). Until it is loaded, the
gene profile shows no associations and the ranking has no phenotype signal.

[Phenotype matching with Monarch reference (the graph, the ranking, PP4)](/docs/reference/monarch-integration "further-reading")
