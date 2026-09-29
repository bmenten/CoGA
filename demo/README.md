# Demo Data

Synthetic datasets for trying CoGA without real patient data. Load them into a running local
stack ([docs/development.md](../docs/development.md)).

| Bundle | What it is | How to load it |
| --- | --- | --- |
| [quartet_family/](quartet_family/README.md) | A family of four on GRCh38 — parents, an affected son and an unaffected daughter — with small variants, structural variants, coverage, CNV segments, APCAD, haplotype recombinations and TRGT repeat expansions. | `backend/.venv/bin/python scripts/load_demo_quartet.py --overwrite` |
| [nipt_family/](nipt_family/README.md) | A monogenic NIPT trio with a documented ground truth: one VCF with the father and the maternal cfDNA at a 12% fetal fraction, and the cfDNA coverage. | Package Import in the app, or the API; see its README. |

Each bundle's README says what it contains and how to regenerate it.
