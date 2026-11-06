The patient's phenotype drives the ranking. Record it as HPO terms, and use gene panels to focus a
search.

### Recording phenotypes

HPO terms are kept per person. An analyst of the family's project (role analyst or higher), a
superuser or an administrator adds them:

1. Click the person's sample name on the family page.
2. Under **HPO Phenotypes**, search a term and choose *present*, *absent* or *unknown*.
3. Press **Add phenotype**. **Edit** changes a term's status or note: choose the new ones and press
   **Save change**. **Remove** takes a term off; to use another term, remove it and add that one.

Only *present* terms are used for matching. Each term added, changed or removed is an entry on the
family's **Clinical audit trail**. A package import can bring terms in too: what it adds or changes is
one entry per person, naming who ran the import and the package. A viewer or a reader sees the terms
but cannot change them.

### Gene panels

The **Panel catalog** holds reusable gene lists. Superusers and administrators create them or import
them from PanelApp. To limit a search to a panel's genes, choose the panel in the **Locations** filter of the
small-variant or structural-variant page.

The **Mendeliome** is the default scope of both pages: every gene that Monarch says causes a disease
or is associated with one. A gene that Monarch links to a disease only as contributing to it or
raising its likelihood is not in it.

On the small-variant and structural-variant pages and in the variant explorer, a panel of any size
keeps:

- the variants annotated with one of its genes;
- the variants inside one of its regions: PanelApp's regions for the assembly, and each gene's
  position in the gene reference. A structural variant counts when it overlaps one.

So the Mendeliome, too, keeps a variant inside one of its genes whose annotation names another gene.

A gene name that no loaded assembly knows, such as a typo or an old symbol, gets no position. The
panel's page lists it as *not imported*: a filter on the panel finds no variant in it.

Superusers and administrators change a local panel's genes on its page, in three ways that can be combined: the bin
on a gene's row, **Add to changes** for new names, and **Edit the whole list**. Each change is listed
under **Changes for version N**, where a name no assembly knows is flagged. Nothing is saved until you
press **Confirm and save version N**; the earlier version stays in the version history. If someone
saved another version in the meantime, CoGA refuses the save, reloads the panel and keeps your changes
listed, to confirm again.

### Phenotype matching

CoGA uses the [Monarch Initiative](https://monarchinitiative.org/) knowledge graph, which links genes,
diseases and HPO phenotypes.

- **On the gene profile** (gene explorer), *Monarch gene–disease associations* lists each linked
  disease with its relationship, source and number of expected phenotypes. Opened from a family, it also
  says how many the family shows (*"3 observed in family"*).
- **On the family page**, the **Phenotype match (Monarch)** panel ranks candidate genes when you press
  **Find candidate genes**. It sends the HPO terms of every family member, not only the affected ones.
  **Re-run match** runs it again on the phenotypes recorded now, for example after you add one.
- **In the variant list**, the *Phenotype priority* preset ranks variants partly by how well their gene
  matches the affected members' phenotypes (see [Small-variant prioritisation](#small-variant-filtering)).

> **Record specific phenotypes.** A broad term such as *Intellectual disability* is linked to over a
> thousand genes and gives a weak ranking by design. Add the patient's distinctive features — a
> particular seizure type, a dysmorphic feature, a metabolic finding — and the ranking sharpens.

An administrator loads the Monarch data about monthly (**Admin → Monarch data**). Until it is loaded, the
gene profile shows no associations and the ranking has no phenotype signal.

[Phenotype matching with Monarch reference (the graph, the ranking, PP4)](/docs/reference/monarch-integration "further-reading")
