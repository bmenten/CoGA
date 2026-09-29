The family page is the hub of interpretation. It shows the pedigree, curation summaries, and a set
of analysis buttons that link out to each review surface.

### What you will find there

- **Analysis buttons that reflect the data.** Small variants, structural variants, variant summary,
  repeat expansions, Paraphase, and mtDNA only appear when that data type is actually loaded for the
  family — so an empty button never sends you to an empty page.
- **Visualisation buttons** for the genome overview, chromosome view, Circos plot, and IGV.
- **Review summaries** for small and structural variants: how many are reviewed, noted, and tagged.
- **Region of interest (ROI).** Users can set a gene or locus of interest directly from the family
  dashboard and open it in the chromosome view.

### Pedigree and member management

Sex, role, parentage, phenotype, and carrier status are edited per member. Phenotype (clinical
status: unknown / unaffected / affected) and carrier status (unknown / carrier / non-carrier) are
**independent axes** — an individual can be unaffected but still a carrier — and are set with
separate controls. These edits are metadata-only: they update the pedigree and mark
phenotype-dependent views as needing recomputation, but they never delete or reimport raw data.
