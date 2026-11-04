# Ancestry PCA (exploratory) — reference

> **Exploratory — not for clinical use.** The ancestry PCA is outside CoGA's intended purpose. It
> has not been validated, it is not read clinically, and it is never part of the sign-out or of
> the signed record.

The **Ancestry (exploratory)** section, at the end of a family's **Sample QC**, places its
individuals beside public reference populations on four principal components (PCs). It shows
**genetic similarity**: how close an individual's genotypes at a fixed set of common SNPs are to
those of reference genomes. It is not an ancestry percentage, an admixture estimate or an ethnic
identity, and nearness on a plot is no statement about a person's origin.

---

## What it reads

The PCA reads the family's **stored small variants** at the reference SNPs (the markers), the
calls CoGA imported: direct calls, a NIPT family's per-sample calls and imputed genotypes. It does
not read the VCF again, and you do not register anything. Each individual is read from each of
these callsets that holds it.

CoGA cannot know from a file's name whether it is a genome, an exome or a gene panel, so it
**measures** what each callset covers. A sequenced genome carries the ALT allele at about two in
five of the markers. The **coverage** of a callset is its non-reference calls at the markers
against that expectation, counted inside the exome (the coding sequence of protein-coding genes,
about 88 % of the markers) and outside it:

| A callset that… | is read as | an absent record counts as |
|---|---|---|
| has a genotype call at nearly every marker (95 % or more) | **a call at every marker** | nothing: the marker is left out |
| covers the expected share inside and outside the exome (75 % or more) | **genome** | homozygous reference |
| covers the expected share inside the exome only | **exome** | homozygous reference inside the exome; outside it the marker is left out |
| covers less, even inside the exome: a gene panel, a subset of sites, a filtered imputation | **part of the markers** | nothing: the callset is not projected |

When an individual has more than one callset that can be projected, CoGA reads its **direct
calls** first, then NIPT per-sample calls, then imputed genotypes. A maternal-plasma cfDNA sample
(a mixture of mother and fetus) and an embryo are left out.

## Choosing how an individual is read

Under **Choose how each individual is read** you can, for one individual:

- read **one callset** instead of CoGA's choice. This is also how you include an embryo;
- give the **regions** that callset covers: a **gene panel** (the coding markers of its genes) or
  a **BED file** of a capture kit. The callset is then read inside them, an absent record counting
  as reference there, if it holds the non-reference calls a sequenced genome has in them; if not,
  the table says the regions are not what it covers. Only the markers inside a BED file and its
  checksum are kept, not the file;
- **leave it out**, or go back to **Automatic**.

**Save** calculates the projection again. Each projection records the choices it read.

## When it is calculated

**Automatically.** Every half minute CoGA looks for families whose stored genotypes changed since
their last projection, and calculates a new one once no import of the family is running. A
calculation reads a few thousand stored rows and takes about a second. **Calculate PCA
projection** calculates one again by hand. Under **Projection** each earlier projection is listed,
*automatic* or *requested*.

## The projection

For each individual and each marker, CoGA counts the ALT alleles of a diploid call whose REF and
ALT are exactly the marker's. Calls must pass their filter; calls without one (`FILTER=.`) count
for imputed genotypes, and for a callset that filtered none of its calls. A no-call, a failed
filter, other alleles, an invalid genotype or two records at one position leave the marker out.
An absent record counts as reference only as the table above says.

The PCs are the **least-squares fit** of the reference axes to the markers the individual has a
usable genotype at, so a marker left out neither counts as reference nor pulls the point towards
the centre. An individual is **projected** when its callset is a genome, an exome or has a call at
every marker, and:

- at least **1,000** markers are usable, and
- at least **95 %** of the markers the callset covers are usable.

Otherwise it is *Not plotted*, and the table says why. The table under the plot gives each
individual's callset, how it was read, its coverage, its usable markers and the markers left out,
by reason; **Callsets per individual** lists every callset that holds the individual, with its
coverage inside and outside the exome and whether it was used.

The axes never change: they were computed from the reference genomes alone, and family data never
trains or turns them. A projected individual lies slightly closer to the centre than the reference
genomes do (projection shrinkage). Five continental reference groups cannot resolve finer structure.

## The reference

The bundled model holds **2,504 individuals of the 1000 Genomes Project phase 3** in five
super-populations (AFR, AMR, EAS, EUR, SAS) and **9,569 autosomal GRCh38 SNPs**: minor allele
frequency at least 5 %, at most 2 % missing in the reference, LD-pruned (r² ≤ 0.2 in 500 kb windows)
and outside long-range LD regions. It is derived from the public files of the peddy project, whose
SNPs were chosen in exome capture regions. The exome mask marks the SNPs in the coding sequence of
a protein-coding transcript of GENCODE v50, or within 20 bp of it. The checksums of the model and
the mask are pinned in CoGA. Under **Reference and calculation** the section names the model, its
checksum and the rules of the projection shown.

Each projection keeps the reference it was calculated with. An earlier projection stays available
under **Projection**, on its own axes, marked when the family's genotypes or the reference have
changed since.

## Who may do what

A reader of the project can view the section. Calculating by hand and choosing how an individual
is read need permission to change the project. A family outside your projects is not found.
