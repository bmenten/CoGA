# Family ancestry PCA (exploratory)

**Outside the intended purpose.** The ancestry PCA shows a family's genetic similarity to public
reference populations. It is not validated, not for clinical interpretation, and never part of
the sign-out or of the signed record ([TF-01 §4](regulatory/TF-01-intended-purpose.md),
[TF-15 §3](regulatory/TF-15-instructions-for-use.md)). The section and its plot say so:
*Exploratory — not for clinical use*. Users read the in-app reference
([frontend/src/content/docs/ancestry-pca.md](../frontend/src/content/docs/ancestry-pca.md),
`/docs/reference/ancestry-pca`); this page explains how it works.

The **Ancestry (exploratory)** section is the last card of the family's Sample QC page
(`FamilyAncestryPanel.tsx` in `FamilySampleQcPage.tsx`). It projects each individual onto four
fixed principal components of a public reference and plots them beside the reference
individuals, with axis choice, population toggles, centring on an individual and zoom.

## What a projection reads

A projection reads the family's **stored small variants** in ClickHouse at the reference markers
(`ancestry_callsets.read_marker_rows`): the rows of the family's project in `entries` whose
`xpos` is a marker's, of the callsets `clair3` (direct calls), `nipt` (a NIPT family's per-sample
calls) and `glimpse2` (imputed genotypes); never `mito`. About ten thousand positions, asked in
bound chunks of 5,000; it takes well under a second, where reading the VCF took minutes. Nothing
is registered: there is no file to point at, no checksum to keep and no declaration to make.

Each individual is read from each callset that holds it (`read_callsets`), marker by marker:

- `genotype_dosage`: the count of the marker's ALT allele in a diploid GT whose record's REF is the
  marker's and whose alleles are all the marker's two bases. `no_call`, `invalid_gt` and
  `allele_mismatch` otherwise; a third allele at the position leaves it out. No strand flips, no
  dosage inference.
- The call's filter: a row merged from one-sample VCFs carries each sample's own FILTER in
  `calls.filters`; a row of a multi-sample VCF carries the record's in `filters`. `PASS` is
  `called`; `FILTER=.` is `called_unfiltered`, counted for imputed genotypes and for a callset
  with no PASS call at all, and left out (`unfiltered`) beside PASS calls; anything else is
  `filtered`.
- Two rows of one callset at one position for one individual: `ambiguous_record`, left out.
- No row: `absent`.

## What a callset covers: measured, not declared

At a marker with reference allele frequency *p*, a sequenced genome carries the ALT allele with
probability 1 − (1 − *p*)² (Hardy-Weinberg; `AncestryReference.expected_carrier`), about two in
five markers over the whole set. A callset's **coverage ratio** is the markers where the
individual has a non-reference call (any filter: it shows the position was sequenced) over that
expectation, counted over all markers, inside the **exome mask** and outside it
(`_classify`, thresholds in `ancestry_callsets`):

| Kind | When | Covered markers (an absent record is homozygous reference) |
|---|---|---|
| `complete` | an explicit genotype call at ≥ 95 % of the markers (`COMPLETE_SHARE`) | none needed: absent is missing; the call rate is over all markers |
| `genome` | ratio ≥ 0.75 (`MIN_COVERAGE_RATIO`) inside and outside the exome | all markers |
| `exome` | ratio ≥ 0.75 inside the exome, below it outside | the exome mask; outside it a marker is left out (`outside_covered`) |
| `partial` | ratio < 0.75 inside the exome: a gene panel, a subset of sites, a filtered imputation | none: not projected |

A model without an exome mask reads no callset as an exome (`genome` on the overall ratio, else
`partial`). An individual is projectable from a callset of the first three kinds with at least
`MIN_MARKERS` (1,000) usable markers and `MIN_CALL_RATE` (95 %) of its covered markers usable.
Of the callsets it can be projected from, `project_family` takes direct calls, then NIPT per-sample
calls, then imputed genotypes (`CALLSET_PREFERENCE`). Every callset's scan is kept with the result
(`candidates`), so the section shows what each held and why it was or was not used.

A sample whose metadata marks a maternal-plasma cfDNA sample, and an embryo, are left out
(`excluded`): a mixture, and a few amplified cells carrying their parents' alleles. A family with
such samples is still projected for its other individuals. An embryo's callsets are still read
and shown, so a user can choose one.

## A user's choice per individual

A user who may change the project can choose, for one individual, otherwise than CoGA's own
reading (`ancestry_choices`, `PUT /api/families/{family_id}/ancestry/choices/{sample_id}`):

- **one callset** (`mode = 'callset'`): one of the stored callsets that hold the individual, even
  where another is preferred. This is also how an embryo is included;
- with that callset, **the regions it covers**: a gene panel's (`panel_id`: the markers inside its
  genes' GRCh38 regions that are also in the exome mask, the coding ones a panel captures) or a
  BED file's (`…/choices/{sample_id}/bed`, multipart: every marker inside its intervals, BED being
  0-based and half-open). The callset is then read as **regions** if it carries at least 75 % of
  the expected non-reference calls inside them, an absent record counting as reference there;
  otherwise as partial, with why. A callset with a call at nearly every marker stays complete;
- **left out** (`mode = 'exclude'`); and back to CoGA's own reading (`mode = 'auto'`).

A cfDNA sample takes no choice. A BED file is read and dropped: the choice keeps the markers
inside it (`chrom:pos`), its interval count, name and SHA-256. A line that is no interval is
refused by its number, never its content. Choices are append-only (`ancestry_sample_choices`), the
latest of an individual holding; `keep_regions` keeps the current choice's regions when only the
callset changes. Saving queues a calculation unless one is queued; a choice made while a run was
running is picked up by the sweep, which also queues a run when a choice is newer than the latest
run. Each result records the choice it read with each individual (`choice`), and a calculation
publishes nothing if a choice was made while it read.

## The projection

The PCs are the **least-squares fit** of the reference loadings to the usable markers'
standardized dosages `(dosage − mean) / scale` (`ancestry_projection.project_least_squares`). With
every marker usable this is the plain projection (the loadings are orthonormal); with markers left
out it does not shrink the point towards the centre, as filling them with the reference mean did.
A set of usable markers that cannot determine four components (fewer than four, or an
ill-conditioned fit) gives no coordinates.

## When it runs

A run is a user's (**Calculate PCA projection** or a saved choice, `trigger = 'user'`) or the worker's own
(`trigger = 'automatic'`, no requester). The ancestry worker runs where package imports run: in
the import-worker processes ([application-scheme.md](application-scheme.md#import-worker-processes)),
or in the API when it runs import workers itself. Every `SWEEP_INTERVAL_SECONDS` (30 s) it
sweeps (`queue_changed_families`): it reads every family's small-variant data version from
ClickHouse's `family_data_version` in one query and queues an automatic run for each family and
project whose version no run has been queued for or read, or with a choice newer than its latest
run, once the family is on GRCh38, its last import completed and none is running. One sweep at a time across the worker processes (an
advisory lock); a failed sweep never stops the queued runs. Nothing in the import, the upload or
any other writer of variants calls it: every write moves the data version, and the sweep sees it.

A run that failed keeps its version, so the sweep does not queue it again until the genotypes
change; **Calculate PCA projection** asks again.

## Locks: an import or a sign-out never waits for the PCA

A calculation (`calculate_run`) checks the family under its variant-write locks
(`try_share_family_variant_writes`): a user's run that its requester may still change the project,
the family still in the project, on GRCh38, with a completed import and none running. It notes the
family's data version and members, ends that transaction (releasing the locks) and reads the
markers. It takes the locks again in the transaction that publishes, and publishes only if the
data version, the rows it read (`rows_digest`, read again) and the members are unchanged. A write
in between makes it refuse ("Genotypes were written during calculation"). The e2e test takes a
writer's locks while the rows are read (`writer_could_lock`).

A running job holds a row lock on its own connection until it ends. A job whose process stopped
loses the lock, and the next worker to look ends it as interrupted. A claim passes over a queued
run whose family has an import running. A cancelled or failed job publishes nothing. A completed
one writes one append-only snapshot (`ancestry_results`): each individual's four PCs or why there
are none, its callset and kind, every callset's scan, the data version read, the calculation
version (`coga-ancestry-pca-v2`) and the rules in words, with the digest of the rows read.

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

peddy chose its SNPs in exome capture regions. The **exome mask**
(`1000g-grch38-v1.exome.json`, SHA-256 pinned as `DEFAULT_EXOME_MASK_SHA256`) marks the 8,398
markers in the CDS of a protein-coding transcript of the GENCODE v50 basic annotation, or within
20 bp of it: what exome capture kits target, with their splice padding. It records the model's
checksum, the GTF's URL and checksum, and one bit per marker.

To rebuild the model, obtain the four pinned public peddy files and their licence; for the mask,
the GTF that `REFERENCE_GENCODE_GTF_URL` pins. Then run:

```sh
python scripts/prepare_ancestry_reference.py --source /path/to/public-peddy-files
python scripts/prepare_ancestry_exome_mask.py --gtf gencode.v50.basic.annotation.gtf.gz
```

`ANCESTRY_REFERENCE_PATH` selects an operator's own model, checked against its own JSON sidecar;
its mask, next to it as `<model>.exome.json`, is checked against the model's checksum, and a
model without one reads no callset as an exome. A missing or invalid model blocks new
calculations. Every earlier projection keeps the reference snapshot it was calculated with
(`ancestry_reference_panels`) and stays readable on its own axes.

## Earlier projections

Until revision 0016 a projection read a registered WGS file (`wgs_sources`): a retained upload or
a package's family VCF declared in its manifest (`metadata.ancestry`). Those runs and results keep
their `source_id` and stay readable; the section marks them as earlier projections. Nothing
registers a file any more, and a manifest's `metadata.ancestry` is not read.

## Access

A project reader can view the section; calculating by hand and choosing need permission to change the project
(`ReadOnlyAccess`). A family or project outside the caller's projects answers 404, like an
unknown one. The tables are described in [database.md](database.md#exploratory-ancestry-pca).
