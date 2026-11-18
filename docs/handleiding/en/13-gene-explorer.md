# 13. Gene Explorer & version control

This chapter describes the gene profile: from a gene symbol (e.g. `BRCA1`) to a page with transcripts, constraint metrics (measures of how sensitive a gene is to mutations), disease and phenotype associations and external links. Further: the sources, why CoGA stores them in Postgres instead of querying them live each time, the refresh as a background job, and how each import of reference data is recorded with source, time and the person who ran it. Reference for gene synchronisation: `docs/data-import.md`.

A **cache** is a local copy that you do not have to fetch again each time; a **worker** is a background process that does long-running work, separate from the web request.

## The Gene Explorer

The Gene Explorer is the page `/genes` (`frontend/src/pages/genes/GeneInfoPage.tsx`). The user types a symbol, chooses a suggestion and sees one merged profile. The page uses two endpoints, both behind a login:

| Endpoint | Supplies |
| --- | --- |
| `GET /api/genes/search?q=` | Suggestions: symbols that start with the input (at least two characters) |
| `GET /api/genes/profile?symbol=…` | The full gene profile |

The profile shows, among other things:

- the gene's location on GRCh38 and, where available, on T2T-CHM13 and GRCh37;
- the transcripts with their badges (MANE, Canonical, CCDS, …);
- constraint metrics (gnomAD pLI and LOEUF, pHaplo/pTriplo, missense Z);
- disease and phenotype associations (OMIM, GenCC, ClinGen, Orphanet, ClinVar, and Monarch with the phenotypes the family shares);
- whether the gene is on the ACMG list of secondary findings (below);
- the panels the gene is in;
- external links (Ensembl, NCBI, OMIM, ClinGen, gnomAD, …);
- for each source a status with the time of the last refresh. This makes the provenance visible on the page itself.

**ACMG secondary findings.** The row *ACMG secondary finding* says whether the gene is on the ACMG SF list: the genes that the ACMG recommends reporting as a *secondary finding* in exome and genome sequencing. CoGA ships that list (ACMG SF v3.3, below).

- If the gene is on it, the row shows the version (with a link to the publication), the phenotype, the category, the inheritance, the variants to report and the list's footnotes for the gene (e.g. for *TTN*: only truncating variants).
- Otherwise it says *Not on ACMG SF v3.3*.
- *Not checked* means that the list was not read for this gene: the gene information has no status for the list, or the list could not be read.

**ClinGen dosage.** Under *ClinGen dosage assertions* there are two kinds of items. *ClinGen dosage curation* comes from ClinGen's own dosage file: the haploinsufficiency and the triplosensitivity in words, the date of the curation and a link to ClinGen's report. The item with an *HI score* is the same haploinsufficiency score as dbNSFP supplies it.

The profile combines two Postgres tables: **`genes`** (reference genes per assembly, with chromosome, position, exons and transcript) and **`gene_info`** (the stored enrichment: name, summary, aliases, IDs, constraint, disease associations). If a gene is on both an ordinary chromosome and an alternative sequence, the ordinary chromosome wins. If the chosen assembly has no enrichment yet, the profile takes the most recent enrichment of the same symbol on another human assembly.

**Access.** If a family or project is passed (for phenotype matching), the backend first checks access. A family or project outside the user's projects gets the same answer as an ID that does not exist (`404 Family not found` or `404 Project not found`). This way the profile leaks no family context, nor which families or projects exist.

**Where in the code:** `backend/app/routers/genes.py` and `build_gene_profile` in `backend/app/services/gene_metadata_service.py`.

**Diseases on the variant cards.** The cards of the small variants and of the monogenic NIPT fetch the disease links of all genes on the page in one request: `GET /api/genes/disease-links?symbol=…&symbol=…` (at most 200 genes, behind a login). For each gene come the links from Monarch (causal first, then by name, as in the profile) and the OMIM diseases from `gene_info` of the first human assembly that has the gene (GRCh38 first). Under the card's header there are up to three diseases: Monarch's causal links, otherwise the OMIM diseases, otherwise Monarch's associations, marked as *not causal*. Each name links to the disease in Monarch, or in OMIM for an OMIM disease (which has no MONDO ID). *More annotations & detail* shows all links, from the same response. If the request fails, the card says so, so that a gene does not appear to have no disease.

**Inheritance, dosage sensitivity and ACMG SF on the card.** The same response gives three more things per gene, from the same `gene_info`. The card shows them in a line under the diseases.

- **Modes of inheritance** (`inheritance`). A mode counts if a curation from GenCC (`gencc_assertions`, `moi_title`) or from ClinGen gene–disease validity (`clingen_validity_assertions`, `moi`) names it with the classification Definitive, Strong, Moderate or Supportive. Limited, Disputed, Refuted, *No known disease relationship*, *Animal model only* or no classification do not count, nor does an unknown or other mode. The modes are AD, AR, SD, XLD, XLR, XL, MT and YL, each with the curations that name it (e.g. *GenCC Definitive*), the strongest first. Dominant and recessive each have their own colour. If the gene has a disease but no counted curation, it says *None curated in GenCC or ClinGen*.
- **ClinGen's dosage curation** (`dosage`): from ClinGen's own file (both scores, the curation's page), otherwise from the copy of the HI score in dbNSFP. The scores are ClinGen's: 3 sufficient, 2 emerging, 1 little, 0 no evidence, 30 autosomal recessive phenotype, 40 dosage sensitivity unlikely. The card shows them only if there is a curation.
- **The ACMG SF list** (`acmg_secondary_finding`). If the gene is on it, the line shows a chip such as *ACMG SF v3.3*, with the list's phenotype, category, inheritance and variants to report on hover. The status follows `source_status.acmg_sf`: `listed`, `not_listed` (the list was read and does not name the gene), `unread` (the list could not be read) or `not_consulted`. With `unread` the card says *The ACMG SF list could not be read*, so that a missing chip does not read as "not on the list"; with the other two it shows nothing.

**Where in the code:** `list_gene_disease_links`, `_inheritance_entries`, `_dosage_entry` and `_acmg_secondary_finding_entry` in `backend/app/services/gene_metadata_service.py`; `frontend/src/pages/families/geneDiseaseLinks.ts` and `VariantGeneDiseases.tsx`.

## Transcript badges

Not every transcript is clinically equivalent. **MANE Select** is the recommended reference transcript, **MANE Plus Clinical** a clinically important additional transcript; **Ensembl Canonical** and **CCDS** are supplementary references. The badges come first from the per-transcript tags in the gene annotation: GENCODE labels MANE and Canonical, and gives the CCDS ID. Only if those are missing does the page compare the transcript ID with the reference transcripts in the profile, without the version number (`NM_007294.4` = `NM_007294`). Annotated transcripts are at the top, MANE first.

In a genome window the backend chooses the best transcript per gene in the same way: MANE Select, then Ensembl Canonical, then the rest.

**Where in the code:** `GeneInfoPage.tsx`; the choice per gene in `backend/app/services/reference_metadata_service.py`.

## Where the data come from

**Reference genes.** For GRCh38 the genes come from the GENCODE annotation, for T2T-CHM13 from UCSC's RefSeq-derived annotation. If that fails, CoGA falls back on a UCSC gene table and records that (chapter 4).

**The enrichment** comes from two kinds of sources:

- **Bulk files**, read once per job and looked up per symbol: the **HGNC complete set** (the register of which genes exist, with previous symbols, aliases and IDs), the local **dbNSFP** gene file (the richest source: constraint, OMIM and Orphanet associations, GO terms, pathways, HPO terms, expression, orthologues), **ClinGen** gene validity and dosage, **GenCC**, **ClinVar** gene-condition and the **ACMG SF list**. Each bulk source is consulted for every gene.
- **The ACMG SF list** ships with CoGA in `data/ref-data/acmg-sf/`: ACMG SF v3.3 (*Genetics in Medicine*, 2025), 84 genes. The README there names the publication, how the list was transcribed and checked, and how to update it (a controlled change). CoGA looks up a gene on the list by its HGNC ID. This way a gene that HGNC renamed after the publication is still on it.
- **One live source per gene:** **NCBI Gene**, for a summary of genes that dbNSFP does not cover. Only during a refresh, never when a page is opened. Across the whole process one request goes to NCBI every 0.4 seconds, under the limit of three per second that NCBI applies without an API key. If the HGNC complete set gives the NCBI Gene ID, one request suffices instead of two.

**HGNC**, not the annotation, determines which genes exist. A locus with a symbol that HGNC does not know is not a gene and gets no enrichment. A renamed symbol is folded into the current one via the previous symbols and aliases. An old symbol that two genes lay claim to is left out rather than guessed. If the HGNC complete set could not be loaded, HGNC is `error` for every gene, and the refresh takes the annotation's symbols as they are, without folding renamed symbols.

**Provenance per source.** Each source gets a status per gene:

- `success`: the source had a record;
- `missing`: queried, but nothing for this gene;
- `not_consulted`: never queried, so *no* statement about coverage;
- `error`: downloading or reading failed.

If the local dbNSFP file is missing, dbNSFP is `missing` for every gene, without a release and with the message that the file was not found. A new installation synchronises its genes even without the file; the first start that finds the file while no gene has been synchronised with it queues a synchronisation that adds it (chapter 4). For the ACMG SF list, `missing` means "not on the list". That is why CoGA refuses, as a whole, a list that is missing, unreadable or incomplete. Incomplete means: no version, a gene without a symbol, HGNC ID, phenotype or category, a gene that appears twice, or a number of genes other than the list itself states. Each gene then gets `error`, and the gene page says that the list could not be read, never that the gene is not on it.

Each status comes with the time, the URL and, if the source states it, the **release** that the response gave, with the SHA-256 of the bytes read. A source that states no release (ClinVar) deliberately gets none, instead of an invented version. The URLs of the bulk sources and the path to dbNSFP are settings (`.env.example`).

**Gene panels from PanelApp** are not used for the enrichment, but are imported as panels (chapter 15).

**Where in the code:** `backend/app/services/gene_info_bulk_sources.py` (the bulk sources) and `gene_info_external.py` (NCBI and the merging).

## Why and how CoGA stores this

The external sources are **not** queried at every page visit. That would be slow, fragile and not reproducible: two reviewers could see different data. The enrichment is therefore in the Postgres table **`gene_info`**, one row per assembly and symbol, with the source status and the time of the last refresh. The page reads only from that table.

**Refreshing as a background job.** A full refresh covers thousands of genes and therefore runs as a job in `gene_info_refresh_jobs`, for one gene or all human genes.

- There can be only **one active job** at a time. The database enforces that; a second request gets `409`.
- Each job stores who requested it: the administrator's account (`requested_by_id`) and the e-mail address under which it was requested (`requested_by`). The first job at installation belongs to no account and gets `startup-bootstrap`.
- A worker runs the job only while that account is an active administrator. If the account has been deactivated, is no longer an administrator or no longer exists, the job ends `failed` with the reason, without writing a gene record. The slot then becomes free for the next refresh.
- A worker takes the job, processes the genes and regularly writes the progress. A job whose worker stopped is picked up again after a while.

**Administration.** On the *Gene reference sync* page (`GeneReferenceAdminPage.tsx`) an administrator can have one gene or all genes refreshed, follow the active job, view the coverage per source (with the columns *Release*, *No record* and *Not consulted*) and see the recent jobs. All endpoints for this (`/api/admin/gene-reference/...`) are for administrators only.

**Where in the code:** `backend/app/services/gene_info_jobs_pg.py`; the worker starts and stops in the `lifespan` of `backend/app/main.py`.

## Version control of reference data

For reproducibility under the IVDR, every import of reference data is recorded. That applies to genes, cytobands, blacklist, clinical CNVs, segmental duplications and DGV, via an upload, the automatic import, a rebuild of the CNV knowledge base, a DGV load or the DGV import script (`scripts/import_dgv.py`). Every import writes a row in **`reference_dataset_imports`**: the assembly, the kind of dataset, the number of rows, whether existing data were replaced, the source and its URL, the release that the source states about itself, who did the import and when.

Only GENCODE states a release: the import takes the version and the date from the GTF header. A rebuild of the CNV knowledge base records as its release the files it was built from, without a date (chapter 15). A DGV load records the pinned release, the file and its SHA-256, with the date of the release. For the other sources (the UCSC tables and UCSC's T2T GTF, uploads, the built-in reference files and the DGV script) it says `not stated`, without a date. The *Organisms and assemblies* page (`/reference-data`) shows that history (*Recent reference activity*) and the status per assembly and dataset.

**Gene panels** get a new version whenever their genes or regions change, also through a new import from PanelApp (a local panel also on a new description). Each version is kept with the full gene list; deleting a panel also deletes its versions (chapter 15).

Together, `reference_dataset_imports` (which reference data were active, and since when), the panel versions (which gene set a panel had) and the source status in `gene_info` (which sources and releases supplied the enrichment) thus answer an auditor's question: which reference data lay under this result? In addition, the signed report freezes the source of the gene loci and the Monarch release (chapter 11).

**Where in the code:** `backend/app/services/reference_metadata_service.py` (the imports and their registration) and `reference_source_service.py` (the automatic import); the page `frontend/src/pages/reference/ReferenceCatalogPage.tsx`.

## Key files

| File | Role |
| --- | --- |
| `backend/app/routers/genes.py` | Search, gene profile, genes per region |
| `backend/app/services/gene_metadata_service.py` | The gene profile from `genes` and `gene_info`, with access control |
| `backend/app/services/gene_info_bulk_sources.py` · `gene_info_external.py` | The bulk sources, the ACMG SF list included; NCBI and the merging |
| `data/ref-data/acmg-sf/` | The ACMG SF list shipped with CoGA (v3.3) and its README |
| `backend/app/services/gene_info_jobs_pg.py` | The refresh jobs and the worker |
| `backend/app/services/reference_metadata_service.py` | Imports of reference data and their registration; transcript choice |
| `backend/app/services/reference_source_service.py` | The automatic import of GRCh38 and T2T |
| `backend/db/schema/postgres/02_reference.sql` | `genes`, `gene_info`, `gene_info_refresh_jobs`, `reference_dataset_imports` |
| `frontend/src/pages/genes/GeneInfoPage.tsx` | The Gene Explorer, with the transcript badges |
| `frontend/src/pages/admin/GeneReferenceAdminPage.tsx` | Administration of the synchronisation |
| `frontend/src/pages/reference/ReferenceCatalogPage.tsx` | Assemblies, reference data and their history |
