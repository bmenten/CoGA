# Family ancestry PCA

Open **Ancestry** in the family workspace. CoGA compares family individuals with a frozen
public reference using four principal components. Reference populations are colored;
family individuals have small labelled diamonds. Switch axes, toggle populations, center
on an individual and zoom. Axis percentages describe reference variance explained, not
an individual's ancestry proportions. This is exploratory genetic similarity, not an
ethnicity classifier, admixture estimate or clinical result.

## Verified input

Use a complete, joint, directly called GRCh38 WGS VCF with diploid GT calls. Register an
existing retained upload from the ancestry page, or explicitly declare a WGS upload at
import. Source verification checks the managed file's checksum, samples, build and primary
callset genotype/filter fingerprint without reimporting or changing variant storage.
Reference at absent positions and acceptance of unfiltered `FILTER=.` calls are separate,
explicit choices. A variant-only VCF's distribution does not establish callable coverage.
No-calls, failed filters, malformed genotypes and ambiguous or incompatible records remain
excluded. Genotypes used for ancestry do not come from the filtered clinical display.

Family packages can opt into source registration with:

```json
{"metadata":{"ancestry":{"full_wgs":true,"genome_build":"GRCh38","absent_record_is_hom_ref":true,"allow_unfiltered":false}}}
```

This saves the verified source; calculating ancestry remains a separate manual action.
Readers can view ancestry but cannot register sources or calculate. Current family/project
scope is checked for every operation; outside-project records return 404.

## Projection and QC

Each individual requires at least **1,000 usable markers and 95% usable calls**. Called
genotypes and expressly allowed absent-record REF assumptions count as usable. Above the
threshold, uncalled loci contribute zero after reference centering (reference-mean
substitution for PCA). Below it, the individual has **Insufficient data — not plotted**.
QC retains each exclusion category. No strand guessing, liftover or dosage inference is
performed.

The bundled model contains **2,504 1000 Genomes phase 3 reference individuals and 9,569
autosomal GRCh38 SNPs**. It uses reference MAF ≥5%, missingness ≤2%, deterministic LD
pruning at r²≤0.2 in 500-kb windows and excludes the specified MHC/common inversion regions.
Mean/variance scales and four loading columns are frozen. Family data never trains or
rotates the axes. Projection shrinkage and limited reference representation constrain
fine-scale interpretation.

The checksummed NPZ, provenance JSON and peddy MIT attribution are under
`backend/app/data/ancestry/`, derived from
[peddy's pinned public resources](https://github.com/brentp/peddy/tree/1d340cd3e87623c8c58b4a82b18f33ba532101aa/peddy).
`1KG-NNNN` denotes a public matrix row, not an original participant identifier. Loading is
bounded and disables pickle. No family data leaves CoGA. To rebuild, obtain the four
pinned public peddy input files and licence, then run:

```sh
python scripts/prepare_ancestry_reference.py --source /path/to/public-peddy-files
```

`ANCESTRY_REFERENCE_PATH` optionally selects an operator-prepared model with a matching
JSON sidecar. Invalid or missing resources block new calculations while preserving history.

## Reproducibility and history

Independent ancestry jobs pin the WGS source and public basis checksum. Completed results
are append-only, retain their original reference coordinates and record calculation version
and input policies. A changed family, source, genotype version/fingerprint, reference or
requester permission prevents publication. Cancellation publishes no partial result; stale
unlocked jobs become interrupted. Historical projections remain selectable with clear
source/reference warnings. The current calculation version remains `coga-ancestry-pca-v1`:
removing PGS did not change its arithmetic or public reference.

## Local PGS removal

Local PGS scoring, Catalog/model management, raw-score history, compatibility settings,
FASTA preparation and their HTTP/UI endpoints are removed. Migration **0019** preserves
all former shared WGS source IDs and fields in independent `wgs_sources`, repoints ancestry
foreign keys and removes retired PGS tables/functions. It retains ancestry and clinical
records and existing audit history. New installations create only the final schema.

Importing and visualizing results from an external pgsc_calc workflow is a future,
separate module. No external-result importer, score interpretation or PGS calculation is
implemented in this ancestry module.
