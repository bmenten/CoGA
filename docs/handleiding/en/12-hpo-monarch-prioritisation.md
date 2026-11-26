# 12. HPO, Monarch & variant prioritisation

This chapter describes how CoGA turns a patient's *phenotype* (the observed clinical features) into help for the analyst: a list of candidate genes and a ranking of the variants. The chain runs from the **HPO terminology**, through the knowledge base of the **Monarch Initiative** and **semantic similarity**, to **variant prioritisation**. The **ranking cache** keeps that fast and reproducible. An explanation for users is in the app (*Docs → Phenotype matching with Monarch*, `frontend/src/content/docs/monarch-integration.md`); the design is in `docs/monarch-integration.md` and `docs/variant-ranking-cache.md`.

A few terms:

- **HPO** (*Human Phenotype Ontology*): a standardised, hierarchical list of clinical features. Each feature has a fixed code, such as `HP:0001250` (seizure). The terms are related as parent and child: a specific term is a subtype of a more general one.
- **CURIE:** a short identifier with a prefix, e.g. `HP:0001250` (phenotype), `MONDO:0007739` (disease), `HGNC:1100` (gene). CoGA uses the same identifiers as the sources.
- **Phenotype score:** a number between 0 and 1: how well does a gene's phenotype profile fit the patient's features?

## HPO: the dictionary of phenotypes

The ontology is in four Postgres tables:

- `hpo_term`: one row per term, with the release;
- `hpo_synonym`: synonyms, for searching on free text;
- `hpo_edge`: the parent-child relations;
- `hpo_closure`: per term all its ancestors with their distance. That way "does the patient have this term or a more specific subtype?" is one fast query.

The table `individual_hpo` links phenotypes to a family member, with the status `present`, `absent` or `unknown`. Only `present` counts for the prioritisation.

**Loading and versions.** With an empty database the backend downloads the newest HPO release from the release's own fixed PURL and loads it, if the file states that release. If that fails, it loads the fallback: the file fixed in the repository (`data/ref-data/hpo/hp.obo`), only with that file's SHA-256; it refuses any other. The sources and the SHA-256 of the fallback are fixed in the code, not in a setting (chapter 4). The release is read from the file itself and stored with each term. That way an analysis can later be traced back to the ontology version that applied.

**Search and administration.** Analysts search for terms through `GET /api/hpo/search`: first exact match, then start of the label, then label and synonym. The administration page (`HpoTerminologyAdminPage.tsx`) shows the number of terms and the loaded release, and reports a newer HPO release (`GET /api/admin/hpo/release-check`, from HPO's latest GitHub release). A new release comes from a file on the server or from the release's own fixed PURL (`https://purl.obolibrary.org/obo/hp/releases/<date>/hp.obo`); a downloaded file must state that release. Nothing is read in without an administrator, because a new release changes the phenotype matching. The administrator first looks at a *preview* (how many terms are new, changed or gone) and only then applies it. Only the page enforces that order: it allows applying only after a successful preview of exactly the same file and sends along the SHA-256 from that preview, so that the backend reads in only those bytes. The endpoint `POST /api/admin/hpo/sync` itself also accepts an immediate application (`preview_only: false`).

The release is the one that the file itself states (its `data-version`). The terms keep the SHA-256 of the bytes read in (`metadata.release_sha256`); the administration page, the loaded release and every signed record name it. *Expected release* and *Expected release date* are what the administrator expects. If the file states a different release, or none, the sync refuses (`400`) and nothing is read in.

**Security.**

- Searching and reading require a login; importing and synchronising require an administrator.
- Adding, changing and deleting a family's phenotypes is allowed for an analyst of the family's project with the role `analyst` or higher, a superuser or an administrator; not for a reader and not for a viewer (chapter 2).
- Every change goes into the clinical audit trail (chapter 11). So does a change made by a package import: per member, in the name of whoever started the import.
- The file path in a sync request must lie within a permitted folder. That way a request cannot have an arbitrary file on the server read in.
- A phenotype cannot point to an unknown term (the reference to `hpo_term` is enforced). An import skips unknown terms and reports them.

**Where in the code:** `backend/app/services/hpo_service.py`; `backend/app/routers/hpo.py` and the HPO endpoints in `backend/app/routers/admin.py`; the tables in `02_reference.sql` and `03_assay.sql`.

## Monarch: connecting genes, diseases and phenotypes

The **Monarch Initiative** brings dozens of sources (OMIM, Orphanet, ClinGen, HPO annotations, …) together in one knowledge graph with fixed identifiers. CoGA uses two kinds of links, each in a table of its own:

- **gene → disease** (`monarch_gene_disease`: HGNC → MONDO, with the relations, the sources and whether there is a causal relation);
- **disease → phenotype** (`monarch_disease_phenotype`: MONDO → HPO, with a mark for a phenotype that explicitly does *not* occur with the disease).

Through the shared disease, gene → phenotype follows from these.

**Loading.** CoGA downloads small, pre-split files from Monarch, not the whole knowledge graph. It replaces both tables **in one transaction**, so that they are never on different releases. If the release cannot be determined, CoGA refuses to load: the provenance of the data behind a signed report must never be empty. A start on a database without Monarch data loads the newest release (`ensure_monarch_on_startup`, `REFERENCE_BOOTSTRAP_MONARCH`; decision of the owner, 2026-11-05). If that fails, the backend logs a warning and the start continues; the next start or the administration page tries again. The start does not build the Mendeliome panel, because a panel is created by an account: that happens on the Gene panels page. The administration page (`MonarchDataAdminPage.tsx`) shows the loaded release and the table sizes, refreshes the data and searches by disease or phenotype. All Monarch administration is for administrators only. After a new release the Mendeliome panel is also rebuilt (chapter 15).

**Where in the code:** `backend/app/services/monarch_ingest.py`; the endpoints `/api/admin/monarch/...` in `backend/app/routers/admin.py`.

## Semantic similarity

A patient rarely has exactly the terms from the textbook. *Semantic similarity* therefore measures how closely a gene's phenotype profile resembles what we see in the patient. A **specific shared feature weighs more** than a general one. That weight is the *information content* of a term: the more rarely the term (or a subtype of it) occurs with diseases, the higher it is. "Intellectual disability" occurs with very many diseases and says little. "Congenital hypothyroidism" is rare and makes a gene that explains it stand out. One broad term therefore gives a flat, almost arbitrary ranking.

CoGA computes similarity in two ways:

1. **Live through Monarch** (`monarch_semsim.py`), for the panel of candidate genes on the family page. CoGA sends only the HPO terms (no identity) to Monarch's public API and gets the best-matching genes back. The result is cached briefly; if Monarch cannot be reached, the UI says so.
2. **Locally** (`monarch_phenotype_score.py`), for variant prioritisation. There *every* gene with a candidate variant must get a score, not only the best ones. The method (Resnik, *best-match average*) computes the information content from the local Monarch tables. The selection of terms does not depend on their order: the same input always gives the same score.

## Variant prioritisation

The scoring is in `backend/app/services/variant_prioritization.py`: a pure function, without database or network. That makes testing and repeating simple. It follows the Exomiser model and combines four partial scores:

| Partial score | Measures |
| --- | --- |
| Pathogenicity | Predicted harmfulness: impact and loss of function, ClinVar, the predictors (CADD, REVEL, SpliceAI, AlphaMissense) and gene constraint. A structural variant does not have those predictors: it scores on the kind of event, the number of genes hit and the gene constraint. A copy loss scores as a deletion, in a gene with a pLI of 0.9 or more as haploinsufficient: a `DEL`, including `DEL:ME`, or a `CNV` whose stored copy number shows a loss (the same rule as for the second hit, chapter 8, in `sv_copy_change.py`). A copy gain scores as a duplication: a `DUP`, including `DUP:TANDEM`, or a `CNV` that shows a gain |
| Frequency | Rarity in gnomAD: the rarer, the higher |
| Segregation | The inheritance pattern in the pedigree (de novo, homozygous recessive, compound heterozygous, X-linked, dominant). For a son, on X or Y outside the pseudo-autosomal regions, the de novo weight follows the parent who passes on that chromosome (chapter 8). A structural variant carries NeedlR's *Inheritance* annotation for the query sample. *De novo* counts as de novo. An inherited SV counts as dominant only if the child and the parent it comes from (maternal: the mother, paternal: the father) are affected; otherwise it does not count |
| Phenotype | How well the gene fits the clinical picture |

The first three together form the variant score. The final score is a weighted combination of the variant score and the phenotype score. Two choices safeguard clinical reliability:

- **ClinVar comes first.** Only a pathogenic ClinVar assertion gives the full pathogenicity score; evidence from predictors alone stays below it. "Conflicting interpretations of pathogenicity" does not count as pathogenic.
- **New candidate genes do not disappear.** A gene without Monarch data scores 0 on phenotype, but the variant score stays visible separately in the breakdown of the score (*Variant*). By default the ranking follows the combined score, with the variant score as second key. With `rank_by=variant_score` (in the UI: **Rank by** → *Variant score* in the filters under **Phenotype**) it follows the variant score, with the combined score as second key: a strong variant in a gene without phenotype data then comes to the top. The scores stay the same; only the order and the rank change, across the whole ranking and not only the page shown. The cache keeps the combined order; the other order is applied to the ranking when it is served. The response names its order in `ranked_by`, and the CSV export of a ranked search writes both scores and the rank.

Prioritisation runs at `GET /api/families/{family_id}/small-variants?prioritize=true`. The page turns this on by default; in the API itself it is off by default. The service fetches the filtered candidates, determines the segregation and the phenotype scores, ranks, and returns a score block per variant. The candidate set is bounded. If it overflows, the response reports the real count, and the UI says that the ranking is incomplete and that the filters must be refined. The scores rank within one family; they are not calibrated probabilities.

**Prioritisation changes the order, not what a search finds.** That holds for an inheritance model that pairs variants (compound het, recessive) and for carrier screening. A ranked search then keeps exactly what the same search without prioritisation keeps: the same rules on the same candidates (the pairs, with their phase; the homozygous and X-linked variants, as a flag from the SQL; the couple's gene rule, chapter 8). Only those variants are ranked, with the segregation they have among themselves.

- A gene's pairs stay one result and stand at the place of the gene's best-ranked variant. If it shares that variant with another item, the next variant decides.
- Each variant shows its own rank.
- Such a search reads one window of 5,000 candidates, the largest that the search without prioritisation reads. If it overflows, the ranking and the candidates are both bounded (`ranking_truncated` and `candidates_capped`). The count is then what was found within the window: a lower bound.

**Where in the code:** `variant_prioritization.py`; the prioritisation in `clickhouse_family_variants.py`; the score breakdown in the review dialog.

## The ranking cache

Prioritising takes a few seconds, mainly because of the per-gene scoring. The outcome is **deterministic**: the same input gives the same outcome. That is why CoGA keeps the ranked order in the Postgres table `family_variant_ranking_cache`: only the order and the score per variant, never the annotation or the review state. Those are fetched fresh at every view. That way a cached ranking never shows an outdated annotation or review status.

Each cache row is tied to a SHA-256 over **everything that can change the ranking**:

| Input | Changes when … |
| --- | --- |
| The filters (without pagination), including the inheritance model and the carrier screening | the analyst changes a filter |
| The gene panel and its version | the panel is updated |
| The genes and regions that the panel filter applied (a digest; the regions contain the loci of each gene from the gene reference) | the panel gets other genes or regions, or a reloaded gene reference moves one of its genes |
| The regions to which the search was restricted or which it excluded (the intervals and the loci of the excluded genes), and the excluded genes | the analyst changes those filters, or a reloaded gene reference moves one of those genes |
| The HPO terms of the affected members | a phenotype is added or removed |
| The pedigree, the affected status and the sex of each member (including the couple of a carrier screening) | the family structure changes |
| The version of the family's variant data | something changes in the variants: import, upload, deletion by an administrator or restore |
| The review selections in the filters | a tag or review that the filter touches changes |
| The Monarch release, the HPO release and the time of the last gene constraint update | the reference data is refreshed |
| The assembly and the version of the algorithm | the scoring code changes, or which variants a filter keeps |

If one input changes, the hash changes: the cache misses and the ranking is recomputed. An exact hit is therefore never a ranking on different input. The loci of a gene come from the gene reference at every search, not from a version. That is why what the search applied counts, and not only the version of the panel.

A narrower panel can be served from a broader ranking of the same family (the Mendeliome covers its sub-panels), but only if:

- that ranking is complete;
- its panel still contains exactly the genes and regions on which it was computed. Each cache row records them (`provenance.panel_members`). After all, the narrower panel can only leave out variants from the broader ranking, never add them. A ranking from before a gene was added to its panel misses the variants of that gene and therefore serves no panel. A row whose panel has been deleted is not used either;
- it contains every variant that the narrower panel reaches, with the segregation that variant has within that panel. CoGA therefore fetches those variants first.

Each panel is compared by gene and by position, the Mendeliome included (the owner's decision of 14 October 2026). The Mendeliome ranking therefore also contains a variant that the smaller panel reaches through a gene's locus, e.g. because the annotation names the gene differently from the gene reference. Two cases can make the last condition fail; the narrower panel is then computed directly:

- It reaches a variant only through a region of its own that the broader panel does not have: coordinates that PanelApp gave for a gene, or a locus that was stored when the panel was created. The broader ranking can miss that variant.
- It leaves out the partner of a compound heterozygous pair. Within that panel it is then not a pair: the variant loses that mode of inheritance, and with it part of its segregation weight and score, unless another mode weighs as much.

A panel that comes from a broader ranking gets its own rank numbers, starting from 1. That way it always shows what a direct computation of that panel gives.

An exact hit reads no candidates, not even for a search with pairs or carrier screening. The cache keeps, for each variant, the results it is listed in: a gene's pairs, a couple's gene or the variant alone. The page places those results in the cached order, each at its best-ranked variant, and fetches only its own variants. From them it determines its pairs and genes again, with the same rules as a computation: a pair depends only on its own variants and the pedigree. If a result does not come out with the variants the cache kept, the ranking is computed again (`_serve_ranking_from_cache`).

After a change to phenotypes or pedigree, the backend recomputes the last ranking in advance, so that the next opening is fast. In doing so it does not apply that search's intervals and its excluded genes and intervals: a search with such filters is recomputed at the next opening. The UI reports whether the ranking comes from the cache or was computed.

**Looking up a panel.** CoGA does not read again at every search what a panel filter applies: the panel's genes, its stored regions for the family's assembly, and the loci of its genes in that assembly's gene reference. Each process keeps that, with its digest, per panel and assembly. It uses it only as long as its fingerprint is unchanged. CoGA reads that fingerprint at every use: the panel's version, a SHA-256 of its gene rows and of its region rows for the assembly, and the imports of the gene reference for the assembly. The panel is therefore read again after every change to the panel, by whatever route, and after every new load of the gene reference (each import registers itself in the transaction that writes its rows). A lookup is kept only if the fingerprint after reading is still the same as before.

Why: for the Mendeliome (5,300 genes and, with one row per transcript, some 40,000 loci) the lookup at every search would cost more than computing a small sub-panel, even on an exact hit of the default view and on the check that serves a sub-panel from the Mendeliome ranking. This way such a sub-panel costs about what its own computation costs. A row of `genes` that is written into the database outside the import is seen by a kept lookup only after the next import of the gene reference or a restart.

**Where in the code:** `backend/app/services/variant_ranking_cache.py`; the lookup of a panel in `backend/app/services/clickhouse_family_variants.py` (`_fetch_panel_constraints`); the design note `docs/variant-ranking-cache.md`.

## Candidate genes on the family page

The panel `MonarchPhenotypeMatchPanel.tsx` calls Monarch only after a click on *Find candidate genes* (`GET /api/families/{family_id}/phenotype-match`). That way the external service is not called at every page load. Every click, including one on *Re-run match*, asks the question again, with the terms of that moment. The backend reuses Monarch's response for the same terms for one hour (`monarch_semsim.py`). During a query the panel shows no previous result; a failed query appears as an error with the reason.

The endpoint collects the `present` terms of the family (or of one member) and enriches each gene found:

- Does the gene exist in CoGA? Then the UI links to the gene profile (chapter 13).
- Which of the gene's phenotypes does the family have, and which not? The ontology counts: a general gene phenotype also counts if the patient has a more specific subtype.

**The score of one gene.** For a variant that no ranking scored, the ACMG dialog reads the phenotype score of its gene at `GET /api/families/{family_id}/gene-phenotype-score?symbol=` (`family_gene_phenotype_score` in `clickhouse_family_variants.py`). That is the score the ranking gives the gene: the same `present` terms of the affected members, the same local scoring. If the family has no terms or the gene has no Monarch phenotypes, the score is empty ("not assessed"), never 0. PP4 and the phenotype step of the de novo criteria (PS2, PM6) read that score (chapter 10).

## Safety and traceability

- **Deterministic.** The same input always gives the same ranking: a precondition for an auditable, repeatable result.
- **Bound to versions.** The cache key contains the Monarch, HPO and panel versions and the genes and regions that the search applied. HPO terms carry their release. Monarch refuses to load without a known version.
- **Recompute rather than trust.** Every relevant change changes the hash, and when in doubt the code recomputes.
- **Access.** Phenotype matching requires a login; loading and refreshing HPO and Monarch data require an administrator.

## Key files

| File | Role |
| --- | --- |
| `backend/app/services/hpo_service.py` | Loading HPO, versions, search, path check |
| `backend/app/services/monarch_ingest.py` | Downloading and replacing Monarch, status, search |
| `backend/app/services/monarch_semsim.py` · `monarch_phenotype_score.py` | Similarity: live through Monarch; locally for the prioritisation |
| `backend/app/services/variant_prioritization.py` | The scoring |
| `backend/app/services/sv_copy_change.py` | Whether an SV removes or adds a copy: for the SV scoring, the second hit and the drawing of SVs (chapter 9) |
| `backend/app/services/variant_ranking_cache.py` | The ranking cache |
| `backend/app/routers/hpo.py` · `backend/app/routers/admin.py` | HPO endpoints; administration of HPO and Monarch |
| `backend/db/schema/postgres/02_reference.sql` · `03_assay.sql` | HPO and Monarch tables; `individual_hpo` and the cache |
| `frontend/src/pages/families/MonarchPhenotypeMatchPanel.tsx` | Candidate genes on the family page |
| `frontend/src/pages/admin/HpoTerminologyAdminPage.tsx` · `MonarchDataAdminPage.tsx` | Administration of HPO and Monarch |
