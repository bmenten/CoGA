- **Assembly** — the reference genome build (e.g. GRCh38) a project uses.
- **ROI** — region of interest: a gene or locus pinned to a family.
- **ACMG class** — five-tier variant classification: benign, likely benign, VUS, likely pathogenic,
  pathogenic.
- **VUS sub-tier (hot / warm / cold)** — a finer split of the VUS band by ACMG points (hot 4–5, warm
  2–3, cold 0–1): how close an uncertain variant sits to Likely Pathogenic. Hot VUS are the ones
  worth chasing more evidence for.
- **ClinVar** — public archive of variant–condition interpretations.
- **gnomAD / TOPMed** — population allele-frequency references.
- **CADD / REVEL / SpliceAI / SIFT / PolyPhen / AlphaMissense** — in-silico predictors of
  deleteriousness or splicing impact (AlphaMissense scores missense variants).
- **HPO** — Human Phenotype Ontology: a structured vocabulary of clinical phenotypes, organised as a
  hierarchy (specific terms inherit from more general ones).
- **Monarch Initiative** — a knowledge graph linking genes, diseases, and HPO phenotypes from
  curated sources; CoGA uses it for phenotype matching and ranking.
- **Phenotype similarity (Phenomizer / semantic similarity)** — a score for how well two sets of HPO
  terms match, weighting rarer, more specific shared phenotypes (information content) more heavily.
- **pLI / LOEUF** — gene-level constraint metrics: how intolerant a gene is to loss-of-function
  variation (high pLI / low LOEUF = constrained).
- **De novo (trio)** — a variant present in an affected child but absent in both parents; confirming
  it needs a genotyped, well-covered trio.
- **Priority score** — the Exomiser-style ranking score blending phenotype fit, impact, rarity, and
  segregation; orders candidates within a family rather than giving an absolute probability.
- **MANE Select / MANE Plus Clinical** — agreed reference transcripts for clinical reporting;
  **RefSeq Select** and **Ensembl Canonical** are the representative transcripts from each database.
- **Compound heterozygous** — two different variants in one gene, one per allele.
- **TRGT** — tandem-repeat genotyping used for repeat-expansion calls.
- **Paraphase** — caller for paralogous / segmental-duplication regions.
- **Heteroplasmy** — the fraction of mitochondrial genomes carrying a variant.
- **Carrier vs phenotype** — carrier status describes genotype; phenotype (clinical status)
  describes the individual. They are tracked independently.
