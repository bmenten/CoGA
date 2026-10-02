Every case starts on its family page.

### The header

**Status**, **Assigned to** and **Reviewed by** track the case's workflow; any user can change them. The
header also shows the assembly and the projects, and a *Not validated for clinical use* banner when the
family is outside the validated scope.

### The buttons

- **Variants** — **Small variants** and **Structural variants** (each with its curation counts); then
  **Repeat expansions**, **Paraphase**, **mtDNA analysis** and **Monogenic NIPT**; and finally **Variant
  summary**, **Sample QC** and **Report**. A button appears only when the family has that kind of data.
- **Visualization** — **Genome view**, **Chromosome view**, **Circos plot** and **IGV viewer**.

### Region of interest (ROI)

The ROI is the gene or locus the case is about; PGT uses it for the embryo calls. An administrator sets
it (**Save**, **Clear**). Anyone can click it to open the chromosome view with 1 Mb on each side. In a PGT
family, **Review ROI markers →** opens the marker review.

### Family members

One row per person: role, parents, partner, **Related to**, status, HPO terms and a **Seq. QC** chip
with the sequencing QC verdict and mean depth (hover for the metrics). In a PGT family each embryo also
shows its derived call (see [Haplotype segregation](#haplotype-segregation)).

**Related to** names the member through whom a person is related by an unknown degree, such as a PGT
index known to be on the mother's side, or both parents. The pedigree draws the link as a dotted arc
with a **?**. The haplotype track colours that person along the genome when they turn out to be that
member's own parent or child (they share one of the member's haplotypes along nearly every
chromosome), and a more distant relative around the ROI, where the markers on both sides show the
haplotype they share; elsewhere such a relative stays grey.

Only an administrator edits a member: click the sample name to open **Family member details**, change
the fields, press **Apply to pending**, then **Save pending updates** on the family page. HPO terms are
added in the same dialog. Affected status and carrier status are separate: an unaffected person can be a
carrier. Adding or removing members is done in **Admin → Family & Sample Data**.

Further down are the **Phenotype match (Monarch)** panel and, closed by default, the **Analysis
pipeline settings** recorded at import.
