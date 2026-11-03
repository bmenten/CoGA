# Ancestry PCA (exploratory) — reference

> **Exploratory — not for clinical use.** The ancestry PCA is outside CoGA's intended purpose. It
> has not been validated, it is not read clinically, and it is never part of the sign-out or of
> the signed record.

The **Ancestry (exploratory)** page of a family places its individuals beside public reference
populations on four principal components (PCs). It shows **genetic similarity**: how close an
individual's genotypes at a fixed set of common SNPs are to those of reference genomes. It is not
an ancestry percentage, an admixture estimate or an ethnic identity, and nearness on a plot is no
statement about a person's origin.

---

## The WGS source

The PCA reads one VCF of the family, which must be a complete, joint, directly called **GRCh38
whole-genome** callset with diploid GT calls. CoGA cannot tell a whole genome from a subset by
looking at its variants, so that it is one is your declaration:

- **A retained upload.** On the page, choose one of the family's retained small-variant uploads,
  confirm it is a complete whole-genome callset, and choose the two policies below. CoGA checks
  the file's checksum, that its sample columns are exactly the family's samples, that it declares
  no other build and holds no imputed or gVCF records, and that its calls are the family's stored
  genotypes. It does not import the file again.
- **A family package.** The package's manifest declares its family VCF, and the import registers
  it once the import has completed:

  ```json
  {"metadata": {"ancestry": {"full_wgs": true, "genome_build": "GRCh38",
                             "absent_record_is_hom_ref": true, "allow_unfiltered": false}}}
  ```

The two policies are recorded with the source and with every projection:

- **Absent positions.** *Keep unknown*, or *assume homozygous reference*: in a whole-genome VCF a
  position without a record is usually reference, but that is an assumption.
- **Unfiltered calls.** Calls with `FILTER=.` are not PASS calls; accept them only on purpose.

A source stops being current when the family's members, its stored genotypes or the file change,
or when its import did not complete. The page then says why, and a new source is needed.

A family with an embryo or a maternal-plasma cfDNA sample, and a monogenic NIPT family, cannot
have a source: their samples are not individuals' own diploid genotypes.

## The projection

Choose **Calculate PCA projection**. The calculation runs in the background and takes minutes for
a whole genome; the page shows when it ends. For each individual and each reference SNP, CoGA
counts the ALT alleles of a PASS (or accepted unfiltered) diploid call whose REF and ALT are exactly
the reference SNP's. A no-call, a failed filter, other alleles, an invalid genotype or two records at
one position leave the marker out. An absent record counts as reference only under that policy.

An individual is **projected** when:

- at least **1,000** markers are usable,
- at least **95 %** of the markers are usable, and
- when absent records are taken as reference, at least **20 %** of the markers are observed as
  calls. A whole genome carries a non-reference allele at about two in five of them; fewer means the
  VCF is not a whole genome, and its absent markers would project as all reference.

Otherwise it is *Insufficient data — not plotted*. The table under the plot gives each individual's
observed calls, assumed reference calls and the markers left out, by reason.

The axes never change: they were computed from the reference genomes alone, and family data never
trains or turns them. A projected individual lies slightly closer to the centre than the reference
genomes do (projection shrinkage), and five continental reference groups cannot resolve finer
structure.

## The reference

The bundled model holds **2,504 individuals of the 1000 Genomes Project phase 3** in five
super-populations (AFR, AMR, EAS, EUR, SAS) and **9,569 autosomal GRCh38 SNPs**: minor allele
frequency at least 5 %, at most 2 % missing in the reference, LD-pruned (r² ≤ 0.2 in 500 kb windows)
and outside long-range LD regions. It is derived from the public files of the peddy project, and its
checksum is pinned in CoGA. Under **Reference and calculation** the page names the model, its
checksum and the policies of the projection shown.

Each projection keeps the reference it was calculated with. An earlier projection stays available
under **Projection**, on its own axes, marked when its source or its reference is no longer the
current one.

## Who may do what

A reader of the project can view the page. Registering a source and calculating need permission to
change the project. A family outside your projects is not found.
