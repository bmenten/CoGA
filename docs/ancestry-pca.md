# Family ancestry PCA (exploratory)

**Outside the intended purpose.** The ancestry PCA shows a family's genetic similarity to public
reference populations. It is not validated, not for clinical interpretation, and never part of
the sign-out or of the signed record ([TF-01 §4](regulatory/TF-01-intended-purpose.md),
[TF-15 §3](regulatory/TF-15-instructions-for-use.md)). The page and its plot say so:
*Exploratory — not for clinical use*. Users read the in-app reference
([frontend/src/content/docs/ancestry-pca.md](../frontend/src/content/docs/ancestry-pca.md),
`/docs/reference/ancestry-pca`); this page explains how it works.

The family page **Ancestry (exploratory)** (`FamilyAncestryPage.tsx`) is offered for a family
with small variants that is not a monogenic NIPT family. It projects each individual onto four
fixed principal components of a public reference and plots them beside the reference
individuals, with axis choice, population toggles, centring on an individual and zoom.

## The WGS source

A projection reads one **WGS source** (`wgs_sources`): a complete, joint, directly called GRCh38
whole-genome VCF with diploid GT calls. The user declares that the VCF is a whole genome; a
variant distribution cannot prove callable coverage. A source records:

- the file's location, checksum, size and modification time;
- the family's samples;
- the data-version token and genotype fingerprint of the family's stored small variants;
- two policies: absent records taken as homozygous reference, and `FILTER=.` calls accepted.

It is *ready* only while all of these still hold (`wgs_source_storage.source_state`); the page
names the first that does not.

Two ways to register one:

- **A retained upload** (`POST /api/families/{family_id}/wgs-source`, `wgs_source_service`). The
  user picks the policies and one of the family's retained small-variant uploads that is a
  directly called nuclear callset (the raw file's metadata records the callset). CoGA reads the
  whole file and checks its checksum, its sample columns (exactly the family's samples), its
  build, that it has no imputed or gVCF records, and a fingerprint of every call (`gt_hash`).
  That fingerprint must equal the same fingerprint over the stored calls in ClickHouse. Then it
  checks the checksum again. Nothing is imported again.
- **A family package** whose manifest declares its family VCF:

  ```json
  {"metadata":{"ancestry":{"full_wgs":true,"genome_build":"GRCh38","absent_record_is_hom_ref":true,"allow_unfiltered":false}}}
  ```

  The import takes the VCF's checksum before any dataset reads it and notes the family's
  data-version token once its datasets are written. After it releases the family's variant
  writes, it registers the source if the file and the token are unchanged and the VCF's columns
  are exactly the family's samples (`PackageAncestrySource`). None of this can fail the import: a
  source that cannot be registered leaves a line in the import's log.

A family with an embryo or a maternal-plasma cfDNA sample, or a monogenic NIPT family, has no
source (`unsupported_samples`): its samples are not individuals' own diploid genotypes.

## Locks: an import or a sign-out never waits for the PCA

Reading a whole-genome VCF takes minutes, so neither the registration nor the calculation holds
the family's variant-write locks while it reads. Each checks the source under those locks
(`try_share_family_variant_writes`), ends that transaction and reads the file. It takes the locks
again only to store its row, after checking that nothing was written in between (the
data-version token; for a calculation also the full genotype fingerprint). A write in between
makes it refuse. The e2e tests take a writer's locks while the file is being read
(`writer_could_lock`).

## The calculation

**Calculate PCA projection** queues a job (`ancestry_runs`) that pins the source and the
reference checksum; nothing queues one automatically. The job runs where package imports run:
in the import-worker processes ([application-scheme.md](application-scheme.md#import-worker-processes)),
or in the API when it runs import workers itself. A running job holds a row lock on its own
connection until it ends. A job whose process stopped loses the lock, and the next worker to
look ends it as interrupted. A cancelled or failed job publishes nothing. A completed one
writes one append-only snapshot (`ancestry_results`): each individual's four PCs or
*insufficient data*, the marker QC by category, the calculation version and the policies.

Per individual and reference SNP (`ancestry_projection.project_vcf`), the dosage is the number
of ALT alleles of a PASS (or accepted unfiltered) diploid call whose REF and ALT are exactly the
SNP's. A no-call, a failed filter, another allele, an invalid genotype or two records at one
position leave the marker out; an absent record is reference only under that policy. The
PCs are the standardised dosages `(dosage − mean) / scale` (0 for unusable markers) times the
loadings. An individual is projected only with at least `MIN_MARKERS` (1,000) usable
markers, `MIN_CALL_RATE` (95 %) of the markers usable and, under the hom-ref policy,
`MIN_OBSERVED_FRACTION` (20 %) of them observed as calls. A whole genome carries a non-reference
allele at about two in five of these SNPs; without the last threshold an exome or a one-record
VCF would project as an all-reference point. There are no strand flips, liftover or dosage
inference.

## The reference

`backend/app/data/ancestry/1000g-grch38-v1.npz`, with its JSON sidecar and peddy's MIT licence
(`PEDDY-LICENSE.txt`): **2,504 individuals of the 1000 Genomes Project phase 3** (five
super-populations) and **9,569 autosomal GRCh38 SNPs**, derived from
[peddy's pinned public files](https://github.com/brentp/peddy/tree/1d340cd3e87623c8c58b4a82b18f33ba532101aa/peddy).
Reference MAF ≥ 5 %, ≤ 2 % missing, LD-pruned at r² ≤ 0.2 in 500 kb windows, long-range LD regions
excluded; `scale = sqrt(2p(1 − p))`; four PCs of the reference alone, so family data never trains
or turns the axes. The NPZ's SHA-256 is pinned (`DEFAULT_REFERENCE_SHA256`); loading it is
bounded and never unpickles. `1KG-NNNN` numbers a row of the public matrix. The 1000 Genomes data
are open-access (IGSR); cite The 1000 Genomes Project Consortium, *Nature* 526, 68–74 (2015).

To rebuild it, obtain the four pinned public peddy files and their licence, then run:

```sh
python scripts/prepare_ancestry_reference.py --source /path/to/public-peddy-files
```

`ANCESTRY_REFERENCE_PATH` selects an operator's own model, checked against its own JSON sidecar.
A missing or invalid model blocks new calculations. Every earlier projection keeps the reference
snapshot it was calculated with (`ancestry_reference_panels`) and stays readable on its own axes.

## Access

A project reader can view the page; registering and calculating need permission to change the
project (`ReadOnlyAccess`). A family or project outside the caller's projects answers 404, like an
unknown one. The tables are described in [database.md](database.md#exploratory-ancestry-pca).
