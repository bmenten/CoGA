# Demo family dataset

This folder contains a deterministic synthetic family dataset for exercising CoGA end-to-end.

## Family

- family id: `demo_family`
- project label suggestion: `CoGA demo family`
- species: `Homo sapiens`
- assembly: `GRCh38`
- father: `father`
- mother: `mother`
- affected child / proband: `son`
- unaffected sibling: `daughter`

This dataset is tuned to behave more like a family-based review case:

- 15 kb coverage bins with noisier log2 ratios centred around `0`
- no coverage, APCAD, or haplotype data in centromeric or heterochromatic gaps
- deletions near `-1.0` and duplications near `+0.58`, mirrored in the SV callsets
- APCAD clusters around `0`, `0.5`, and `1.0` in diploid regions, with deletion and duplication-specific cluster behaviour
- parent haplotypes act as the reference, while both children show about 2 to 3 recombinations per chromosome
- VEP-style annotated Clair3 and GLIMPSE2 VCFs with mostly exonic consequences and rich transcript / ClinVar / gnomAD metadata
- TRGT repeat-expansion VCFs for all four samples, spanning normal, grey-zone, and pathogenic loci

## Folder map

- `pedigree/demo_family.ped`
- `metadata/family_manual.json`
- `metadata/samples.csv`
- `metadata/manifest.json`
- `uploads/bed/coverage/*.coverage.bed`
- `uploads/bed/segments/*.segments.bed`
- `uploads/bed/apcad/*.apcad.bed`
- `uploads/structural_variants/*.structural.tsv`
- `imports/apcad/*.apcad.tsv`
- `imports/structural_variants/sniffles/*.sniffles.vcf`
- `imports/structural_variants/spectre/*.spectre.vcf`
- `imports/small_variants/demo_family.clair3.vcf`
- `imports/small_variants/demo_family.glimpse2.vcf`
- `imports/haplotypes/*.recombination.tsv`
- `imports/repeat_expansions/trgt/*.trgt.vcf`
- `uploads/repeat_expansions/*.trgt.vcf`

## Density summary

{
  "coverage_bin_size": 15000,
  "apcad_target_points_per_sample": 50000,
  "coverage_bins_per_sample": {
    "father": 190402,
    "mother": 190402,
    "son": 190402,
    "daughter": 190402
  },
  "segments_per_sample": {
    "father": 49,
    "mother": 48,
    "son": 54,
    "daughter": 54
  },
  "apcad_upload_rows_per_sample": {
    "father": 50000,
    "mother": 50000,
    "son": 50000,
    "daughter": 50000
  },
  "apcad_import_rows_per_sample": {
    "father": 50000,
    "mother": 50000,
    "son": 50000,
    "daughter": 50000
  },
  "manual_structural_rows_per_sample": {
    "father": 5010,
    "mother": 5068,
    "son": 5043,
    "daughter": 5028
  },
  "sniffles_records_per_sample": {
    "father": 5010,
    "mother": 5068,
    "son": 5043,
    "daughter": 5028
  },
  "spectre_records_per_sample": {
    "father": 5010,
    "mother": 5068,
    "son": 5043,
    "daughter": 5028
  },
  "trgt_records_per_sample": {
    "father": 21,
    "mother": 21,
    "son": 21,
    "daughter": 21
  },
  "clair3_family_variants": 5184,
  "glimpse_family_variants": 5184,
  "recombination_blocks_per_child": {
    "son": 81,
    "daughter": 80
  }
}

## Regeneration

```bash
python scripts/generate_demo_quartet_dataset.py
```
