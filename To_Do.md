# QUESTIONS to be resolved / TO DO

Name:
- Comprehensive Genome Analysis (CoGA / CGA)
- Family based (comprehensive) Genome Analyis (FaGa)


- SNV import!
- APCAD import (Roxana)
- haplotype import (Roxana)

- mitoDNA module: https://github.com/seppinho/mutserve  (Talk to Latoya/shirin/roxana)

- Exomiser ranking implementation on SNVs
- annotate segements from WisecondorX (Shirin)
- add link to MultiQC.html (when available from trio)

- SNVs color-coded in chromosome view according to impact and Clinvar pathogenicity

- User settings
    - which info (annotation) needs to be displayed (table vs cards)
    - which tracks are displayed at genome view / chromosome view
    - which SNVs to be displayed? Quality / colorcoded?

- admin settings: thresholds (colored) for certain in silico annotations


- dbnsfp file toevoegen voor generef sync
- add GENCODE info for transcripts in gene explorer to see which transcripts are labeled canonical, MANE select, or MANE Plus Clinical
- check haplotype coloring
- combine all filters (SNVs, SVs, TRGT, Paraphase) --> visualize
- SNVs color-coded in chromosome view according to impact and Clinvar pathogenicity
- SMART a. pre-filters for SNVs and SVs for visualization before b. custom filters

- add entire HPO cataloque: filter families/individuals on HPO
- PGS scores: Promethease en PGScalc

- **AZURE authentication**

- **methylations**
quid methylation? --> clinically imprinted regions in table: define average methylation per phase
episign?

- **pseudogenes**
--> how represent paraphase

- **BAM/CRAM support**
link to S3 bucket --> IGV integration


- **LUCID**
-TADs are displayed + much extra data: epigenetic encode tracks (organization of tracks)
-HPO: standard: Highest Ranked Variants: Tier 1, Tier 2
-tabs for Common filters, SNVs/InDels specific, SVs specific, TR specific, DMR specific --> results in one table


External References to use:


Needlr:
https://github.com/millerlaboratory/needLR

Paraphase:
https://github.com/PacificBiosciences/paraphase

TRGT:
https://github.com/PacificBiosciences/trgt
