Every case starts on its family page.

### The header

The family page, every analysis page (small and structural variants, repeat expansions, Paraphase,
mtDNA, monogenic NIPT, the variant summary), the Sample QC and the report share one header. From any
of them you can work on the case and move between analyses.

- One line gives the members, the assembly and the projects, then the page's own figures.
- **Status**, **Assigned to** and **Reviewed by** track the case's workflow. An analyst of the family's
  project (role analyst or higher), a superuser or an administrator can change them; a viewer and a
  reader only see them.
- **Assigned to** and **Reviewed by** offer the active analysts of the family's projects whose role is
  not viewer, the superusers and the administrators; never a reader or a viewer. Someone chosen before
  they became a reader or a viewer (or left the family's projects) stays shown, marked
  *(not eligible)*, until you change it.
- *Read-only access* in the header means you only read this family: your role is viewer, or you are a
  reader in its project.
- A *Not validated for clinical use* banner shows when the family is outside the validated scope.

A row of links at the foot of the header leads to the family's analyses: **Small variants**,
**Structural variants**, **Repeat expansions**, **Paraphase**, **mtDNA analysis**, **Monogenic NIPT**
and, last, **Variant summary** (of the structural variants). At the far right of the same row, under
the pedigree, are the family-level functions: **Sample QC** (when the family has small variants) and
**Report** (when it has any variant data: small or structural variants, repeat expansions, Paraphase
results or mtDNA data). A link appears only when the family has that kind of data. The page you are on
is marked.

### The analyses and views

- **Curation** gives one line each for the small variants, the structural variants, the repeat loci
  and the Paraphase regions: how many are reviewed, how many carry notes, and the count per review
  tag. For the repeat loci and the Paraphase regions it also counts how many carry your
  classification.
- **Visualization** — **Genome view**, **Chromosome view**, **Circos plot** and **IGV viewer**.

### Region of interest (ROI)

The ROI is the gene or locus the case is about; PGT uses it for the embryo calls. An administrator sets
it (**Save**, **Clear**). Anyone can click it to open the chromosome view with 1 Mb on each side. In a PGT
family, **Review ROI markers →** opens the marker review.

### Family members

One row per person: role, parents, partner, **Related to**, status, HPO terms and a **Seq. QC** chip.
The chip shows the sequencing QC verdict and mean depth; hover or focus it for the metrics.

- A passing chip shows the number alone. Any other state is a word on the chip.
- *Warning* or *Fail*: a metric crossed its limit, which the tooltip names.
- *Not run*: the sample's QC was not assessed (no limit set for any of its metrics, or no verdict
  returned). This is never a pass.
- An embryo whose QC holds only the PGT pipeline's embryo metrics has no depth to show. Its chip gives
  the value of the metric that decided the verdict (*Fail ADO 14.2%*), or *Not run* alone.

In a PGT family each embryo also shows its derived call (see
[Haplotype segregation](#haplotype-segregation)).

**Related to** names the member through whom a person is related by an unknown degree: for example a
PGT index known to be on the mother's side, or both parents. The pedigree draws the link as a dotted
arc with a **?**. The haplotype track colours such a person:

- along the genome, when they turn out to be that member's own parent or child (they share one of the
  member's haplotypes along nearly every chromosome);
- around the ROI only, when they are a more distant relative: there the markers on both sides show the
  haplotype they share. Elsewhere such a relative stays grey.

Only an administrator edits a member:

1. Click the sample name to open **Family member details**.
2. Change the fields and press **Apply to pending**.
3. Press **Save pending updates** on the family page.

In the same dialog, an analyst of the family's project (role analyst or higher), a superuser or an
administrator adds and removes HPO terms.
Affected status and carrier status are separate: an unaffected person can be a carrier. **Remove
member** in the dialog takes the person out of the family at once, after you confirm. Members are
added in **Admin → Family & sample data**.

Further down are the **Phenotype match (Monarch)** panel and, closed by default, the **Analysis
pipeline settings** recorded at import.
