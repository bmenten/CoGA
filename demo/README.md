# Demo Data

This directory contains reusable synthetic datasets for exercising CoGA without real patient data.

Current bundle:

- [quartet_family](quartet_family)

This bundle includes a realistic family with two parents and two children plus:

- a PED file
- dense coverage and segmented CNV data
- APCAD tracks
- structural-variant upload files and Sniffles/Spectre import files
- family small-variant VCFs
- recombination summaries for haplotypes

Regenerate it from the repository root with:

```bash
python scripts/generate_demo_quartet_dataset.py
```

Load it into the current application schema with:

```bash
python scripts/load_demo_quartet.py --overwrite
```
