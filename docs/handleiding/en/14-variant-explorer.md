# 14. Variant Explorer

This chapter describes the *Global Small Variant Explorer*: a search function that summarises small variants (SNVs and indels) across **all projects a user has access to**, not within one family. Results never leak outside the user's projects (see *Isolation between projects*). An explanation for users is in the in-app user guide (section `explorers`).

A few terms:

- **Carrier:** a sample that carries the alternative allele, heterozygous or homozygous.
- **Aggregation:** counting, per unique variant, how many samples and families carry it.
- **Assembly:** the version of the reference genome (e.g. GRCh38); variants of different assemblies are never counted together.
- **Keyset pagination:** paging through a large result page by page without costly jumps (`OFFSET`), by remembering where the previous page ended.

## Purpose and screen

A family's filter page (chapter 8) answers "which variants are in *this* family?". The Variant Explorer turns the question around: "in how many samples and families of my accessible cohort does *this* variant occur?". Each row is one unique variant, with the counters *Total samples*, *Het*, *Hom* and *Families*.

The screen (`/variant-explorer`) has a drop-down list for the assembly, a filter on genotype per sample, the usual annotation filter form and the results, with tabs for four views of the same filtered variants: by variant, by gene, by transcript and by cohort frequency (see *The views by gene, transcript and cohort frequency*). A click on a counter opens a window with the carriers, grouped by family, with a link to each family. The form is that of the family page, without the fields for family and samples. Filters the Explorer does not apply are not shown, and their parameters are not sent: an interval list, transcript, callset, genes and intervals to exclude, tags to exclude, "has notes" and *Pass variants only*. That last one remains a filter of the family page, which reads only the family members shown (the owner's decision of 6 November 2026).

**Where in the code:** `frontend/src/pages/variant-explorer/` (`GlobalSmallVariantExplorerPage.tsx`, `globalSmallVariantSearch.ts`, `GlobalSmallVariantTable.tsx`, `ExplorerViews.tsx`, `VariantCarrierModal.tsx`).

## Endpoints

All endpoints are under `/api/variant-explorer` and require a login.

| Endpoint | Purpose |
| --- | --- |
| `GET /variant-explorer/assemblies` | The accessible assemblies, with the number of projects per assembly |
| `GET /variant-explorer/samples` | The samples the user's families show, their active members (for the genotype filter) |
| `GET /variant-explorer/small-variants` | The paginated, aggregated variant list |
| `GET /variant-explorer/small-variants/export` | The same results as CSV, up to 50,000 rows; a file that does not hold every hit says so (see *The CSV export*) |
| `GET /variant-explorer/small-variants/genes` | The view by gene: per gene the variants and the summed carriers |
| `GET /variant-explorer/small-variants/transcripts` | The view by transcript: per transcript the variants per consequence class and the summed carriers |
| `GET /variant-explorer/small-variants/frequency` | The view by cohort frequency: the variants per class of carriers and of gnomAD frequency |
| `GET /variant-explorer/small-variants/{variant_key}/carriers` | The carriers of one variant, per family |
| `GET /variant-explorer/small-variant-tags` | The tag definitions of the accessible projects |

The filters are parsed in one place. That guarantees that the list, the CSV export and the three views use the same filters.

**Where in the code:** `backend/app/routers/variant_explorer.py`; the logic in `backend/app/services/variant_explorer_service.py` and `variant_explorer_views.py`.

## How the carriers are counted

The Explorer counts from the **carrier summary** (`SNV_INDEL/carriers`, `backend/app/services/variant_carrier_summary.py`), which CoGA maintains while the families' variants are written. Counting directly from `entries` would read every row of every family for each page: from three or four whole genomes on, that goes over the limit of 110 s (`docs/clickhouse-scale-benchmark.md`). The summary is derived data: every row can be recomputed from `entries` and the families' members and projects in Postgres.

**What a family contributes.** A family's contribution reads its rows of `entries` under one project it is linked to (every write writes the rows under every project of the family, so every copy holds the same rows), with `FINAL`. Per variant it counts the carriers among the samples the family shows, and keeps the rows apart per *class*: annotation set, rsID, gene symbols and whether the callset is imputed, the columns the filters read per row.

- A variant whose rows in the family all fall in one class, and that is carried by no sample another family also shows, gives one **sum row**: one family, its carriers, heterozygous and homozygous carriers, deduplicated across its callsets. Sum rows of the same variant, the same projects and the same class are added up across the families.
- Otherwise the variant gives one **carrier row** per class and carrier, with family and person: two callsets with a different annotation, an ordinary and an imputed callset, or a person two families show. That way the Explorer counts each person once across the classes and families its filters keep. Two samples linked as the same individual (a person sampled twice) are one carrier under the first of their sample IDs, over the links of every family of the assembly. A carrier counts as homozygous when any of the person's calls of the variant is homozygous, and as heterozygous otherwise: the heterozygous and homozygous carriers add up to the carriers, in a sum row too.
- A **marker** per family (`xpos = key = 0`) says which generation of its contribution the table holds. Contribution and marker arrive together in one part of the table (`ATTACH PARTITION … FROM` from a staging table, merged into one part): the table holds both or neither. A contribution disappears by writing the same rows negatively; the sums then cancel out, even before the parts are merged.

**Who maintains the summary.** Every function that writes a family's rows of `entries` first removes its contribution (`detach_family_variant_carriers`), under the family's write lock (chapter 3). It recomputes that contribution from the rows it was computed from; the data version proves that those have not changed. The *variant-carrier worker* (`reconcile_variant_carriers`) adds, every 5 s, the contribution of each family whose rows, shown members, their same-individual links or projects differ from what the table holds. It too works under the write lock, but only tries to take it: a family an import is holding comes up in the next round. If a contribution cannot be removed exactly, the summary is emptied and rebuilt. A family that the table does not hold as it currently is, is called *pending*: the page says how many families of the scope are pending, and counts them as the table holds them (during an import not at all; after a change of members or projects, for a few seconds as before the change).

Per variant:

- only real carriers count. *Het*, *Hom* and carrier follow the genotype classes of `backend/app/services/genotypes.py` (chapter 8), so that e.g. a haploid `1` counts as homozygous;
- each sample and each family counts once, even if it occurs under several accessible projects or in two callsets;
- imputed and phased calls (source `glimpse2` or `shapeit`) do not count, unless the user ticks *Include imputed variants (GLIMPSE2 / SHAPEIT)*. That applies to the counters, the per-sample conditions and the carrier window;
- only the calls of the samples their family shows count (below).

**Only the samples their family shows.** A row of `entries` holds a call of every sample of the VCF it was imported from. If a member is removed from the family (`remove_members` on a change of the family structure, without erasing the data), their calls stay in those rows. The family page no longer shows the member: it reads only the calls of the active members (`_visible_clickhouse_sample_ids`). The Explorer reads every row the same way (the owner's decision of 6 November 2026):

- A family's contribution to the summary counts only the calls of its active members; if the list changes, the worker recomputes the contribution.
- The carrier window and the per-sample genotype filter read in Postgres the active members of each family of the accessible projects (`_shown_members` in `variant_explorer_service.py`). From those they make the pairs (family, sample), each sample under its name and its UUID (`_shown_calls`): a call counts only if its pair is among them.

This therefore applies to the count, the counters, the CSV export, the carrier window and the per-sample genotype filter. A variant that only such a removed member carries does not appear. A sample that is a member of another family does count in that family's rows, not in those of the old one.

**The carriers per family.** The carrier window fetches the carriers of one variant (capped, with a notice if there are more), only the members their family shows. It keeps one record per family and sample (homozygous takes precedence) and links it to the names of family and project from Postgres, and to the name, role and phenotype of the member in that family. A family the user may not see is missing from that link; its carrier is then skipped. The link is therefore also a second access check.

**Where tested:** `backend/tests/integration/test_variant_explorer_visible_members_clickhouse.py` (on real ClickHouse and Postgres: a family of four from which father and brother were removed, the father as a member of a family of his own; the count, the counters, the export, the carrier window, the genotype filter and the drop-down list of samples); `backend/tests/integration/test_variant_carrier_summary_clickhouse.py` (on real ClickHouse and Postgres: the list and the export from the summary count the same variants and carriers as the count over `entries`, under five filters and three scopes, with a family under two projects, two callsets with a different annotation, an imputed callset, a removed member and a sample two families show; and again after a change of members, a deletion and a new write, which are *pending* until the next round; a broad filter, read window by window, gives the same list, export and count as one query, in four sort orders, also for a variant on a contig outside the chromosomes); `backend/tests/test_variant_carrier_summary.py`.

## Filters

On top of the family page's filters (location, type, gene, rsID, panel, impact, ClinVar, gnomAD, CADD, REVEL, SpliceAI, …) the Explorer knows two kinds of filters:

1. **Tags and classifications.** These are stored per family in Postgres. If the user filters on them, the Explorer first looks up in Postgres the matching variant ids within the accessible projects, and passes that list to the ClickHouse query. An empty list gives an empty result straight away. The list is written literally into the query and therefore holds at most 200,000 ids: the first in variant id order, so that the same filter always gives the same list. Postgres reads one more, to know whether more matched; in that case the search can miss hits. The CSV export reports that, the list on screen does not.
2. **Genotype per sample.** The user adds rules of the form sample with *Het*, *Hom* or *Het + Hom*. Each rule is a separate condition; a variant must satisfy all rules. The Explorer reads the sample's call with the sample filter of the family page (`_small_sample_call_condition` in `clickhouse_variant_queries.py`), so with the same genotype classes: a haploid `1` is homozygous, a `1/2` heterozygous. It does read per variant, though, not per row: it keeps a variant that the sample carries with that genotype in one of its rows, and then counts all carriers of the cohort. Reading per row would count only the carriers of that sample's family; the family page keeps only the rows of its own family. This is the only documented difference between the two (the owner's decision of 6 November 2026). It reads the sample's call only in the rows of a family that shows it, under its name or its UUID. A sample no family shows, such as a member removed from their family, keeps no variant, and the drop-down list does not offer it.

**One definition: the building blocks of the family search.** Every filter the Explorer shares with the family page, it builds with the function of the family search in `clickhouse_variant_queries.py` (`_carriers_where` in `variant_explorer_service.py` calls them, on the rows of the summary, which have the same columns):

- `_small_row_filter_clauses`: the variant type, the location and the rsID (it reads the imputed callsets from the summary's own column `imputed`);
- `_small_annotation_filter_clauses`: the gene, the annotation filters per transcript and the ClinVar statuses to exclude;
- `_small_panel_filter_condition`: the panel (below).

It reads each row of the summary the way the family page reads the rows of that class, with their own annotation: the variant key and the annotation set (chapter 8). A row is therefore kept exactly when the family page of its family shows the variant, and a row of the Explorer adds up the families and carriers of those family pages. This only makes a difference if the same variant was imported in two families with a different annotation (two VEP runs). An example: family A says *stop gained* (HIGH), family B *missense* (MODERATE). With impact HIGH, A's family page shows the variant, B's does not; the Explorer shows it with only A's carriers (the owner's decision of 6 November 2026).

- **The location.** The chromosome applies with or without `chr`, and the mitochondrion as `chrM`, `M` or `MT` (`_chromosome_options`). A variant falls within the location if it starts there (its position on or after the start, on or before the end).
- **The gene.** In the row's gene symbols, or in the gene index of its annotation (gene symbols and Ensembl gene ids), however it is written.
- **The rsID.** In the row's rsID or in that of its annotation, as typed but without surrounding spaces.
- **The ClinVar statuses to exclude.** No transcript of the row's annotation has that status.
- **The annotation filters per transcript** must hold together for one transcript (one row of the table `annotations`), with the condition `_small_annotation_filter_condition`: impact and consequence, HGVS, the ClinVar status, the frequency limits, the predictors, and *Canonical only*, *MANE only* and *LoF only*. The family page reads that condition on its rows' own transcripts (`_small_own_transcripts_condition`), except with a transcript or HGVS text. The rows of the summary have no transcripts: the Explorer reads the condition on the shared table `annotations` (`_small_annotation_row_membership_condition`). Both keep the same rows. The consequence, in-silico and frequency filters have the exception "a (likely) pathogenic ClinVar variant passes the frequency, the consequence and the in-silico filter", on the same ClinVar terms. With *MANE only* and impact HIGH, a variant whose HIGH transcript is not the MANE transcript thus drops out. Of the predictors (CADD, REVEL, SpliceAI, SIFT, PolyPhen), one is enough. A SpliceAI score at or above the threshold also counts as a hit for impact and consequence.

**Where tested:** `backend/tests/integration/test_variant_explorer_filter_rule_clickhouse.py` (on real ClickHouse: two families, one variant under two annotations, the mitochondrion as `M` and as `MT`; for each filter, each family page keeps what the rule keeps, and each row of the Explorer adds up the families and carriers of those pages); `backend/tests/test_variant_explorer_service.py` (each clause of the Explorer is that of the family search, with the same parameters, the per-transcript conditions read on the shared table).

**The gene panel, as on the family page.** The Explorer reads a panel the way the family page reads it (`_fetch_panel_constraints` in `clickhouse_family_variants.py`), for the search's assembly:

- the panel's genes and the names of its stored regions;
- the regions stored for that assembly (PanelApp's coordinates);
- the locus of each gene in that assembly's gene reference.

It applies it with the family page's condition (`_small_panel_filter_condition` in `clickhouse_variant_queries.py`): a variant in one of the regions, or with one of the genes in its row's gene symbols or in the gene index, however large the panel. A gene panel with more than 1000 regions, such as the Mendeliome, it also compares by position (as the family page does) (the owner's decision of 14 October 2026). A panel without genes and without regions for the assembly keeps nothing. A panel that does not exist gives `404`, an id that is not a UUID `400`, as on the family page.

Only the regions does the Explorer look up differently, with the same answer. Testing every row against every region (`arrayExists`, the rule of `_small_region_filter_condition`) would take minutes across all families of the cohort for a panel of a thousand genes. The Explorer (`_region_key_condition` in `variant_explorer_service.py`) therefore makes a join on windows of 64 kb:

- each region goes under every 64 kb window it touches;
- each variant goes, at its position in the annotation index, under every window its REF touches;
- a pair that shares a window is kept if the two overlap according to the family page's rule: the same chromosome (`chr1` and `1`, `M` and `MT` count as the same), the first base of the REF on or before the end of the region, and the last on or after its start.

Two that overlap share the window of a base they have in common; none therefore drops out. It reads the regions as the family page reads them (`_small_region_bounds`). The family page makes the same join, over the family's own rows (`_small_family_region_condition` in `clickhouse_variant_queries.py`). Testing every row against every region costs 1.3 s there for 500 regions and 2.1 s for 1000, for a family of about 190,000 rows.

*MANE only* also follows the family page: a transcript counts if it is MANE Select or MANE Plus Clinical. Both read those flags from one list, `MANE_ONLY_TRANSCRIPT_FLAGS` in `backend/app/services/clickhouse_variant_records.py`. VEP sets those flags only on GRCh38: on GRCh37 and T2T, *MANE only* keeps nothing on either page.

Every value the user supplies goes as a named parameter (`%(naam)s`) to the ClickHouse client, which puts it escaped into the query. CoGA's code never pastes it into the text itself (chapter 7).

## The views by gene, transcript and cohort frequency

Besides the list by variant, the Explorer shows the same filtered variants in three other ways (the owner's choices of 12 November 2026). Each view reads the same rows of the summary with the same filters (`_carriers_where`), linked to their own annotation (variant key and annotation set), per genome window of 50 Mb, the same windows as those of the list for a broad filter (see *Speed*): that way the query of a window holds only the variants of that window, however large the cohort. The windows are then added up. Each variant lies in exactly one window, including a variant on a contig outside the chromosomes (`chrUn_…` and the like, to which `xpos` jointly gives rank 99).

- **By gene.** One row per gene that the annotation of a filtered variant names (VEP's gene symbol): the number of variants, also per most severe impact on that gene, and the carriers **summed across those variants**: a sample with two variants in the gene counts twice. A variant counts for a gene over the rows whose annotation names that gene, as the list with that gene filter counts it; a gene's row is therefore the sum of that list. A click on the gene opens that list.
- **By transcript.** One row per transcript that the annotation of a filtered variant names, all transcripts or only the MANE (Select or Plus Clinical) or canonical transcripts, with the gene, the variants per most severe consequence class on that transcript (loss of function, missense or in-frame, splice region, synonymous, other) and the summed carriers.
- **By cohort frequency.** A cross table of the filtered variants: their number of carriers in the user's projects (1, 2–5, 6–20, 21–100, more than 100) against the class of the gnomAD allele frequency of their own annotation (not in gnomAD, below 0.1 %, 0.1–1 %, 1–5 %, 5 % and more). What is frequent here and rare in gnomAD can be an artefact or a founder variant. A click on a cell opens the list with two filters only the Explorer knows: the number of carriers (`min_total_samples`, `max_total_samples`, after the count) and the gnomAD class (`gnomad_af_class`, on each row's annotation).

**Where tested:** `backend/tests/integration/test_variant_carrier_summary_clickhouse.py` (the row of a gene and of a transcript is the sum of the list with that gene filter; the cells count each variant once); `frontend/src/pages/variant-explorer/__tests__/ExplorerViews.test.tsx`.

## The CSV export

The export uses the same filters and sort order as the list, without pagination, and writes at most 50,000 rows. It asks ClickHouse for one row more: if that row comes back, more variants matched than the file holds. Such a file reports that, just like the exports of the family page:

- the file name says `TRUNCATED`: `variant-explorer-…-TRUNCATED-first-50000.csv`;
- the headers say how many rows were written (`X-CoGA-Export-Rows`), that more matched (`X-CoGA-Export-Truncated`), the limit (`X-CoGA-Export-Limit`) and why (`X-CoGA-Export-Truncated-Reason`: `row-limit`);
- the screen shows a warning asking to refine the filters.

If the list of the tag or classification filter was cut off (see *Filters*), a hit can be missing anywhere in the file, however few rows it has. The reason is then `review-filter-limit`, the name `…-TRUNCATED-partial-search.csv`, and the warning asks to filter on fewer tags or classifications. That reason takes precedence over `row-limit`.

The column *Classification* holds the most severe class the variant's reviews show (see *Traceability*), *Classification text* next to it the classification text of the review that shows that class, as stored: its own text or that of the compound-het pair, empty for a review with only a class tag (`classification_text` in `GlobalVariantRowOut`). The tags come from all reviews.

The export looks up the rows' reviews (tags, classification, last review) in Postgres, with the variant ids as one array parameter. With one parameter per id, every export of more than 32,767 rows would fail: asyncpg refuses a query with more parameters.

**Where in the code:** `export_global_small_variants` and `_review_filter_variant_ids` in `backend/app/services/variant_explorer_service.py`; `export_response_headers` in `backend/app/core/csv_export.py`; `describeCsvExport` and `truncatedExportMessage` in `frontend/src/lib/csvExport.ts`.

## Isolation between projects

For an auditor this is the most important part. Every query is strictly limited to the user's projects:

- The scope is determined in one place. **A superuser and an administrator** (`superuser`, `admin`; `has_superuser_rights`) see all projects, **other users** only their own projects. Without projects the result is guaranteed to be empty.
- Every query is given those projects as a mandatory argument: the list query, the capped count and the views read only the summary rows of families linked to one of those projects (`hasAny(projects, …)`); the per-sample conditions and the carrier query only the rows of `entries` of those projects (`project_guid IN …`). Postgres reads the list of members the families show only for the families of those projects. `project_guid` and the projects of a summary row are the project UUIDs in Postgres. Rows of other projects therefore drop out before anything is counted. The annotation filters and the panel read, per row (variant key and annotation set), the tables `annotations`, `annotation_index` and `gene_index`; the annotation of the visible page then comes from the annotation index. None of those tables holds project, family or sample data.
- The frontend only shows a message when there is nothing ("… in your accessible projects"); the isolation lies entirely on the server.

The file `backend/app/services/data_scope.py`, despite its name, is not about this isolation but about chromosome names. The access scope is in `variant_explorer_service.py` (`resolve_scope`).

**Traceability.** Every carrier can be traced back to its family and project. The review (tags, classification, last review) comes from `small_variant_reviews` (chapter 10). If a variant was reviewed in several families or in several analyses of a family, the row shows the tags of all reviews and the most severe classification (`_classification_rank`); a shared review that every analysis of the family replaced with its own does not count (`review_in_effect_sql`). That ranking reads the class from the stored text, in any form: the label of the review window ("Pathogenic - class 5"), a class key (`acmg_class_5`, `cnv_class_5`) or the class word, however it is written. Text that names no class ranks lowest.

Each review counts with the class the family page shows for it (`review_class_label` in `review_pg_utils.py`, the same rule as `normalizeReviewClassification` in the frontend): a bare class word ("VUS") as its label, otherwise the review's most severe class tag (`acmg_class_5` … `acmg_class_1`) as its label, otherwise the text as stored. So a review that has only a class tag, without classification text, also shows its class. The variant's own review takes precedence over that of the compound-het pair. Postgres reads the reviews in a fixed order (last saved first, then by family id), and the server also imposes that order itself: of two reviews with the same class, the row shows that of the last saved, at equal time that of the lowest family id. The classification filter, like that of the family page (`list_matching_small_variant_review_ids`), keeps a variant by the class the review or the pair's review shows, with the same rule in SQL (`review_class_label_sql`, chapter 10); that way it also finds a review with only a class tag. It keeps a variant for which one review shows that class, as the family page of that family does; the row can show the more severe class of another family.

**Where in the code:** `resolve_scope` in `backend/app/services/variant_explorer_service.py`; the role rules in `backend/app/services/access_control.py`.

## Speed

The Explorer may query many projects and millions of genotypes. A few choices keep that workable:

- **The carrier summary.** A page reads one row per variant, project set and class, not one per family: what it reads grows with the number of distinct variants, not with the number of genomes. The table is sorted on `xpos, key` and the Explorer groups in that order (`optimize_aggregation_in_order`), so that a query holds the sums of one variant at a time. On the synthetic cohort of 250 genomes (a quarter of the variants per genome, 150 million rows in `entries`) the unfiltered page directly from `entries` takes more than 110 s; the times from the summary are in `docs/clickhouse-scale-benchmark.md`. On a synthetic summary the size of 1000 genomes (61 million variants) it takes about 7 s.
- **A broad filter, window by window.** A filter that reads a subquery (an annotation filter, the panel, the rsID, the per-sample genotype filter) collects the variants it keeps, from the whole cohort, in a set that the query holds in its entirety.

    - The Explorer first asks in a single query, in which each set may hold at most two million variants (`max_rows_in_set`). Above that, ClickHouse refuses the query, without ever cutting the set short (`set_overflow_mode = 'throw'`).
    - Then the Explorer reads the summary per genome window of 50 Mb (`ExplorerWindow` in `variant_explorer_service.py`), four at a time. Each variant lies in exactly one window, under every name of its chromosome the summary holds (`1` and `chr1`, every contig outside the chromosomes). The subqueries read only that window's annotation, via the sort key of the annotation tables.
    - The count adds up the windows until it passes the limit. A page sorted by position reads the windows in order from the cursor until it is full. A page sorted by a count takes the first rows of each window and keeps the first of those. That way it gives the same variants, numbers and order as one query.
    - The Explorer reads the columns of a page (ID, rsID, gene symbols) for its variants only: each subquery is given their keys (`annotation_keys`).

    On a cohort the size of 1000 genomes (61 million variants), *rare only* keeps 52 million variants. In one query that goes over the 4 GB a query may use; window by window the page took 25 to 35 s with 320 MiB (6 to 9 s sorted by position). The panel of 5300 genes held 1.2 GiB in one query, 150 MiB window by window, in about the same time (`docs/clickhouse-scale-benchmark.md`).

- **Capped count.** Above a fixed limit the Explorer reports the number as "N+" (`total_is_estimated`), instead of counting everything exactly. Grouped in the order of the table, the count stops at that limit.
- **The pairs (family, sample).** For the carrier window and the genotype filter, the pairs of the members the families show are written literally into the query: two per member (name and UUID), each about 60 bytes. Ten thousand members give about 1.2 MB, within the 16 MiB of `CLICKHOUSE_MAX_QUERY_SIZE`. The carrier window looks up the rows of its variant by position in each family's rows (the position from the annotation index), not by reading the whole of `entries`.
- **Keyset pagination.** The cursor remembers the last row, the sort order and a fingerprint of the filters. A cursor of a different sort order or of different filters is ignored, so that nobody pages wrongly without noticing.
- **Annotation filters per transcript** (see *Filters*). The table `annotations` has one row per transcript, the smaller annotation index one per variant. A locus (chromosome, start, end) also bounds the transcript rows read, the gene index and the rows of the per-sample conditions, as on the family page. The display fields come only for the visible page.
- **A row's annotation by key and set.** Each annotation filter builds a set of (variant key, annotation set) over the whole cohort (or over one window, see above), without the annotation version. In a measurement with the version included, a search with a frequency, impact or CADD filter on 4 million rows of 2 million variants took 0.6 to 1.0 s, without it 0.23 to 0.47 s, with about 10 % less memory (chapter 8).
- **A panel's regions as a join** (see *Filters*). On a synthetic cohort of 2 million variants and 7.6 million rows in 40 families, that join found the variants of a panel of 1000 regions in a twentieth of a second. The per-row test took almost three minutes over the same cohort. With the Mendeliome by position (about 42,000 loci, merged into roughly one region per gene), the capped count on a synthetic cohort of 1.19 million variants took 0.16 s, against 0.10 s on gene symbol alone (`docs/variant-ranking-cache.md`).

## Key files

| File | Role |
| --- | --- |
| `backend/app/routers/variant_explorer.py` | The endpoints and the parsing of the filters |
| `backend/app/services/variant_explorer_service.py` | Scope, counts, pagination, carriers and the bridge to the reviews |
| `backend/app/services/variant_carrier_summary.py` | The carrier summary: each family's contribution, the removal before a write, the worker |
| `backend/app/services/variant_explorer_views.py` | The views by gene, transcript and cohort frequency |
| `backend/app/services/clickhouse_variant_queries.py` | The filters, shared with the family search |
| `backend/app/services/genotypes.py` | The genotype classes |
| `backend/app/services/access_control.py` | The role rules that feed the scope |
| `backend/app/services/clickhouse_variant_storage.py` | The table `entries`, divided per project |
| `frontend/src/pages/variant-explorer/GlobalSmallVariantExplorerPage.tsx` | The screen |
| `frontend/src/pages/variant-explorer/globalSmallVariantSearch.ts` | Search state, cursor, query string |
| `frontend/src/pages/variant-explorer/VariantCarrierModal.tsx` | The carrier window per family |
| `frontend/src/pages/variant-explorer/ExplorerViews.tsx` | The views by gene, transcript and cohort frequency |
