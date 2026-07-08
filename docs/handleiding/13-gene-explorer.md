# 13. Gene Explorer & versiecontrole

Dit hoofdstuk beschrijft hoe CoGA een compleet gen-profiel opbouwt en toont: van een gen-symbool (bv. `BRCA1`) naar een pagina met transcript-overzicht, constraint-metrieken (maten voor hoe "gevoelig" een gen is voor mutaties), ziekte- en fenotype-associaties en externe link-outs. Het behandelt uit welke externe bronnen die informatie komt, waarom en hoe CoGA die informatie in Postgres cachet in plaats van elke keer live op te vragen, hoe een refresh-taak (job) wordt gepland en uitgevoerd door een achtergrond-worker, en — het belangrijkste voor een auditor — hoe elke geïmporteerde referentiedataset en elke paneelversie met bron, versie en tijdstip wordt vastgelegd zodat elk rapport reproduceerbaar aan een concrete dataset-versie hangt.

Enkele begrippen die verderop terugkomen: een **endpoint** is een URL waarop de backend reageert; een **router** is het Python-bestand dat die endpoints definieert; een **service** is de laag met de eigenlijke logica; **cachen** betekent een kopie lokaal bewaren zodat je die niet telkens opnieuw hoeft op te halen; een **worker** is een achtergrondproces dat langlopend werk uitvoert los van het webverzoek.

## Wat de reviewer ziet: de Gene Explorer-pagina

De Gene Explorer is één pagina, `frontend/src/pages/genes/GeneInfoPage.tsx`, bereikbaar via de route `/genes` (geregistreerd in `frontend/src/index.tsx`). De werkwijze is **locus-eerst** ("locus" = de plaats van een gen op het genoom): de gebruiker typt een symbool, kiest een suggestie, en de pagina toont één samengevoegd profiel.

De pagina haalt haar data van twee endpoints in `backend/app/routers/genes.py`:

| Endpoint | Router-functie | Levert |
|---|---|---|
| `GET /genes/search?q=` | `search_gene_symbols` | Autocomplete: symbolen die met de query beginnen, met transcript- en assembly-telling (`transcript_count`, `assembly_count`) |
| `GET /genes/profile?symbol=…` | `get_gene_profile` | Het volledige gen-profiel (overzicht, transcripten, constraint, ziekten, links) |

Beide endpoints vereisen een ingelogde gebruiker via `Depends(get_current_user)`; de zoek-query moet minstens 2 tekens lang zijn (`min_length=2`). De profiel-call accepteert optioneel `family_id`/`project_id` zodat fenotype-matching binnen een familiecontext kan gebeuren.

Op de pagina ziet de reviewer onder meer:

- **Kop met locus en assembly-locaties.** Het gen wordt getoond op zijn plaats in GRCh38, en waar beschikbaar ook T2T-CHM13 en GRCh37 (`assembly_locations` in het profiel). De "primaire" assembly wordt gemarkeerd (`is_primary`) en de familie-context apart (`is_family_context`).
- **Transcript-overzicht** met MANE/RefSeq/Canonical-badges (zie de laatste sectie).
- **Constraint-metrieken** zoals gnomAD pLI, LOEUF, pHaplo/pTriplo en missense z-score (zie de tabel `ADVANCED_CONSTRAINT_METRICS` in de pagina).
- **Ziekte- en fenotype-associaties**: OMIM-, GenCC-, ClinGen-, Orphanet- en ClinVar-gene-condition-relaties, plus Monarch gen–ziekte-associaties met fenotype-matches tegen de familie.
- **Panel-lidmaatschap**: in welke genpanels dit gen zit.
- **Externe link-outs** naar Ensembl, NCBI, OMIM, PubMed, ClinGen, GenCC, DECIPHER, GeneCards, Open Targets, GTEx, ClinVar, UniProt, GeneReviews, PanelApp, en waar mogelijk UCSC en gnomAD.
- Een **"laatst vernieuwd"-tijdstip** (`updated_at`) en een **bronstatus-tabel** (`source_status`) die per externe bron toont of de laatste ophaalpoging gelukt, leeg of foutief was — dit is traceerbaarheid rechtstreeks op de pagina.

**Waar in de code:** de datasamenstelling gebeurt volledig in de functie `build_gene_profile` in `backend/app/services/gene_metadata_service.py`; de externe link-lijst in `_build_external_links` in datzelfde bestand.

### Hoe het profiel wordt samengesteld

`build_gene_profile` combineert twee databronnen uit Postgres:

1. De **`genes`-tabel** (de geïmporteerde referentie-gentabel per assembly) levert de "harde" locus-gegevens: chromosoom, start/eind, exonen, strand, transcript-id. Via `_lookup_gene_documents` en `_pick_primary_gene_doc` wordt het juiste locus gekozen — bij een gen dat zowel op een primair chromosoom als op een ALT/scaffold-contig voorkomt, wint altijd het primaire chromosoom (`is_primary_chromosome`), zodat de weergave nooit naar een alt-contig "springt".
2. De **`gene_info`-tabel** (de gecachte externe verrijking) levert de "zachte" gegevens: display-naam, samenvatting, aliassen, Ensembl/NCBI/HGNC/OMIM-ids, constraint-metrieken en ziekte-associaties (in de `extra`-kolom).

Belangrijk voor de auditor: als er nog geen `gene_info`-cache bestaat voor de gekozen assembly, valt de code terug op de meest recent bijgewerkte `gene_info`-rij voor hetzelfde symbool over alle humane assemblies (de `fallback_result`-query, gesorteerd op `updated_at DESC`). De pagina degradeert dus netjes: de locus-gegevens komen altijd uit `genes`, en de verrijking wordt aangevuld zodra een sync heeft gedraaid.

**Veiligheid & toegangscontrole in het profiel.** Wanneer een `project_id` of `family_id` wordt meegegeven, wordt de toegang eerst gecontroleerd: `build_gene_profile` roept `get_accessible_family_mapping` aan voor de familie, en `_ensure_project_access` weigert (HTTP 403) als een niet-admin het project niet in `metadata_project_ids` heeft. Zo lekt de fenotype-matching geen familie- of projectcontext waar de gebruiker geen recht op heeft (zie ook hoofdstuk [Gebruikersrollen, machtigingen & afscherming](02-beveiliging-rollen-rechten.md)).

## Externe bronnen: welke service haalt wat op

CoGA verrijkt genen uit meerdere gezaghebbende bronnen. De ophaal-logica staat in twee services.

`backend/app/services/gene_info_external.py` doet de **per-gen, live REST-bronnen** (alleen tijdens een sync, niet per paginabezoek):

| Bron | Functie | Levert |
|---|---|---|
| HGNC (genenames.org) | `fetch_hgnc_gene` | Officiële naam, aliassen, vorige symbolen, HGNC-id, RefSeq-accessies, Ensembl/Entrez-id, OMIM-ids |
| Ensembl REST | `fetch_ensembl_gene` | Ensembl-gen-id, biotype, canonical transcript, beschrijving |
| Ensembl homology | `fetch_ensembl_homologies` + `normalize_homologs` | Orthologen in andere soorten |
| NCBI Gene (E-utilities) | `fetch_ncbi_gene` | Samenvatting, aliassen, maplocation |
| ClinGen (kennisbank-pagina) | `fetch_clingen_gene` + `parse_clingen_gene_page` | Curatie-tellingen (gen–ziekte-validiteit, dosage-sensitiviteit), %HI, pLI, LOEUF, MANE Select-transcript, ACMG SF-status |

`backend/app/services/gene_info_bulk_sources.py` doet de **bulk-bronnen** — hele bestanden ineens ingelezen en per symbool geïndexeerd:

| Bron | Parser | Levert |
|---|---|---|
| dbNSFP gene (lokaal `.gz`-bestand) | `parse_dbnsfp_gene_rows` | De rijkste bron: constraint-metrieken (`_dbnsfp_constraint_metrics` — gnomAD pLI/LOEUF, ExAC-scores, RVIS, pHaplo/pTriplo, GDI, s-het…), OMIM/Orphanet-ziekteassociaties, GO-termen, pathways, HPO-termen, weefsel-expressie, model-organisme-orthologen |
| ClinGen gene validity (CSV) | `parse_clingen_validity_rows` | Gen–ziekte-validiteitsclassificaties (Definitive/Strong/…) met MONDO-id, overervingswijze, SOP, datum |
| ClinGen dosage (CSV) | `parse_clingen_dosage_rows` | Haploinsufficiëntie/triplosensitiviteit-scores |
| GenCC (CSV) | `parse_gencc_rows` | Gen–ziekte-assertions van meerdere submitters met classificatie en overervingswijze |
| ClinVar gene-condition (TSV) | `parse_clinvar_gene_condition_rows` | Gen↔aandoening-relaties met OMIM-mim en bron |

De coördinatie zit in `load_human_gene_bulk_context` (in `gene_info_bulk_sources.py`): dbNSFP is de primaire bron. Voor symbolen die **wel** in dbNSFP zitten, worden de online CSV/TSV-bronnen overgeslagen (dbNSFP bevat die informatie al); alleen voor de **overige** symbolen (`fallback_symbols`), of wanneer dbNSFP ontbreekt, worden de ClinGen/GenCC/ClinVar-bestanden gedownload. De URL's van die bulk-bronnen zijn configureerbaar (`gene_reference_clingen_validity_url`, `gene_reference_clingen_dosage_url`, `gene_reference_gencc_url`, `gene_reference_clinvar_gene_condition_url` in `backend/app/core/config.py`); het lokale dbNSFP-pad via `gene_reference_dbnsfp_gene_path`.

`fetch_external_gene_bundle` (in `gene_info_external.py`) voegt alles samen tot één "bundle": als dbNSFP het gen dekt (`primary_source == "dbnsfp_gene"`), wordt dat als "fast path" gebruikt en worden de trage REST-calls vermeden; anders worden HGNC/Ensembl/NCBI/ClinGen live opgehaald en met de bulk-data samengevoegd via `merge_gene_extra`. Elke deelbron krijgt een `source_status`-record (status, `fetched_at`, `source_url`, eventueel foutmelding) — dit is de provenance die later in de admin-pagina en op het gen-profiel zichtbaar is.

**PanelApp** is een aparte bron met een eigen service, `backend/app/services/panelapp_service.py`. Die wordt niet gebruikt voor gen-verrijking maar voor het **importeren van genpanels** (Genomics England PanelApp) — `search_panelapp_panels`, `fetch_panelapp_panel` en `extract_panelapp_import_content`. Belangrijk voor traceerbaarheid: de import-metadata bevat expliciet `panelapp_id`, `version`, `version_created`, `status` en een `source_url` (zie het `metadata`-blok in `extract_panelapp_import_content`). Deze panels verschijnen daarna als "Panel-lidmaatschap" op het gen-profiel. De aanroepende endpoints staan in `backend/app/routers/panels.py` (`GET /panels/panelapp/search`, `POST /panels/import/panelapp`).

## Caching in Postgres: waarom en waar

De externe bronnen worden **niet live per paginabezoek** bevraagd. Dat zou traag, fragiel (afhankelijk van externe uptime) en niet-reproduceerbaar zijn — twee reviewers zouden verschillende data zien. In plaats daarvan cachet CoGA de verrijking in Postgres, en leest de pagina uitsluitend uit die cache.

De centrale cachetabel is **`gene_info`**, gedefinieerd in `backend/db/schema/postgres/001_metadata.sql`. Één rij per `(assembly_id, hgnc_symbol)` (uniek), met kolommen voor `display_name`, `summary`, `aliases`, ids (`ensembl_gene_id`, `ncbi_gene_id`, `hgnc_id`, `omim_gene_id`), `homologs`, de JSONB-kolom `source_status` (de provenance-status per bron) en de JSONB-kolom `extra` (constraint-metrieken en ziekte-associaties). `updated_at` legt vast wanneer de rij voor het laatst is ververst.

Voor performante zoek- en lookup-queries zijn in **`backend/db/schema/postgres/042_gene_search_indexes.sql`** expressie-indexen aangelegd op de `genes`- en `gene_info`-tabellen. Zonder deze indexen zou elke toetsaanslag in de autocomplete (`upper(hgnc_symbol) LIKE 'PREFIX%'`) en elke panel-/regio-resolve een sequentiële scan over 120.000+ rijen veroorzaken. De indexen dekken exact de query-predicaten: `text_pattern_ops` voor de prefix-`LIKE` van `search_genes`, en losse `upper()`/`lower()`-indexen op symbool, gene-id en transcript-id zodat de query-planner een BitmapOr kan doen in plaats van te scannen.

**Waar in de code:** het lezen uit de cache gebeurt in `build_gene_profile` (`gene_metadata_service.py`); het schrijven in `_upsert_gene_info_row` (`gene_info_jobs_pg.py`), met een `INSERT … ON CONFLICT (assembly_id, hgnc_symbol) DO UPDATE` zodat een sync bestaande rijen bijwerkt zonder duplicaten.

## Refresh-jobs & worker

Omdat een volledige sync van alle humane genen duizenden externe verrijkingen omvat, draait dit als achtergrond-job, niet inline in een webverzoek. De job-tabel is **`gene_info_refresh_jobs`** in `001_metadata.sql`.

### De job-tabel

Kernkolommen: `scope` (`'symbol'` voor één gen, `'all_human'` voor de hele catalogus), `symbol`, `status` (`'queued' → 'running' → 'completed'/'failed'`), `active_slot`, `worker_id`, `requested_by`, tijdstempels (`requested_at`, `started_at`, `heartbeat_at`, `completed_at`) en voortgangstellers (`total_symbols`, `completed_symbols`, `updated_records`, `current_symbol`).

Cruciaal voor veiligheid: `active_slot TEXT UNIQUE`. Er bestaat één logische slot-waarde (`ACTIVE_GENE_REFERENCE_SLOT = "gene_reference"`), en omdat de kolom uniek is, kan er nooit meer dan één actieve job tegelijk bestaan. Een tweede insert botst op de unieke constraint en de service vertaalt dat naar een **HTTP 409** ("A gene reference refresh job is already active"). Dit is databank-afgedwongen wederzijdse uitsluiting, geen best-effort applicatielogica.

### Queuen

`queue_gene_reference_refresh_job` (`gene_info_jobs_pg.py`) voegt een `queued`-rij toe met de `active_slot`. Dit wordt aangeroepen door:

- de admin-endpoints `POST /admin/gene-reference/refresh-all` en `/refresh-gene` (zie volgende sectie), met `requested_by = user.email`;
- de startup-bootstrap `queue_startup_gene_reference_refresh_if_needed`, die alleen queuet als (a) bootstrap aanstaat (`gene_reference_bootstrap_on_startup`), (b) een lokaal dbNSFP-bestand bestaat (`find_local_dbnsfp_gene_path`), (c) er humane genen geladen zijn, (d) de `gene_info`-cache nog leeg is, en (e) er nog geen actieve job loopt. Dit voorkomt dat een verse installatie zonder gecachte gen-data blijft; de bootstrap-job krijgt `requested_by = "startup-bootstrap"`.

### Uitvoeren: de worker

De worker `gene_reference_refresh_worker` wordt bij het opstarten van de applicatie als achtergrondtaak gestart en bij afsluiten netjes gestopt (`backend/app/main.py`, regels 82 en 91 — zie ook hoofdstuk [Initiële deployment & seeding](04-deployment-en-seeding.md)). De worker pollt elke 2 seconden (`GENE_REFERENCE_WORKER_POLL_SECONDS = 2.0`). Zijn kern:

- `claim_next_gene_reference_refresh_job` claimt atomair de volgende job met `FOR UPDATE SKIP LOCKED` (voorkomt dat twee workers dezelfde job pakken) en zet `status = 'running'` met een `worker_id` en `heartbeat_at`. Het claimt ook **verweesde** jobs: een `running`-job waarvan de heartbeat ouder is dan 5 minuten (`GENE_REFERENCE_STALE_HEARTBEAT`) wordt als "stale" opnieuw opgepakt — zo blijft een gecrashte worker een job niet eeuwig blokkeren.
- `run_gene_reference_refresh_job` → `_refresh_grouped_human_gene_info` doorloopt de symbolen, roept per symbool `fetch_external_gene_bundle` aan en schrijft via `_upsert_gene_info_row` naar `gene_info`. De voortgang (`completed_symbols`, `current_symbol`, `updated_records`, `heartbeat_at`) wordt gethrottled weggeschreven: hooguit elke 100 symbolen of elke 30 seconden (`GENE_REFERENCE_PROGRESS_COMMIT_SYMBOLS`/`_SECONDS`), zodat een lange sync niet bij elk gen commits doet maar de heartbeat toch ruim binnen de 5-minuten-grens blijft.
- Bij succes: `status = 'completed'`, `active_slot = NULL` (de slot wordt vrijgegeven zodat een volgende job kan starten). Bij een exception: `status = 'failed'` met de foutmelding in `error`, en de slot wordt eveneens vrijgegeven.

## Versiecontrole van referentiedatasets

Dit is het hart van reproduceerbaarheid onder IVDR: elke geïmporteerde referentiedataset en elke paneelversie wordt vastgelegd met **bron + versie + tijdstip + wie**, zodat een ondertekend rapport altijd aan een concrete, terugvindbare dataset-staat gebonden kan worden.

### Referentiedataset-imports

Elke import van referentiedata (cytobanden, genen, blacklist, klinische CNV's, segmentale duplicaties, DGV) loopt via één enkel schrijfpad, `apply_reference_dataset_text` in `backend/app/services/reference_metadata_service.py`. Aan het eind van dat pad schrijft de functie — ongeacht of de import van een handmatige upload, een UCSC-import of een CNV-kennisbank-rebuild kwam — een rij naar **`reference_dataset_imports`** (schema `backend/db/schema/postgres/021_reference_dataset_imports.sql`):

| Kolom | Betekenis |
|---|---|
| `assembly_id` | Voor welke assembly de data geldt |
| `dataset_type` | Welk soort dataset (`genes`, `cytobands`, …) |
| `inserted` | Hoeveel rijen geladen |
| `replaced` | Of bestaande data vervangen werd |
| `source` | Herkomst (`upload`, `ucsc`, …) |
| `performed_by` | Wie de import deed |
| `performed_at` | Wanneer (standaard `now()`) |

Omdat álle imports door dit ene pad gaan, is er één uniforme, chronologische audit-feed. `list_recent_reference_imports` en `list_reference_statuses` (in hetzelfde bestand) lezen deze tabel voor het "Recent reference activity"- en het per-dataset "laatst bijgewerkt / door / aantal"-overzicht.

De **UCSC-import** zelf zit in `backend/app/services/reference_source_service.py` (`import_reference_from_ucsc`, `_download_genes`, `_download_cytobands`). Die haalt gentabellen en cytobanden op bij UCSC en roept per dataset `apply_reference_dataset_text(..., source="ucsc")` aan, waardoor de import automatisch in `reference_dataset_imports` belandt. Merk op: `_download_genes` probeert de tracks in volgorde `ncbiRefSeqCurated → ncbiRefSeq → refGene → ensGene`, wat de bron-track van de geïmporteerde genen bepaalt. Een veiligheidsdetail: `_safe_ucsc_genome` valideert het assembly-identifier tegen een strikt patroon (`_UCSC_GENOME_RE`) voordat het in een download-URL wordt geïnterpoleerd, wat een request-forgery (SSRF)-risico op de vaste UCSC-host afsluit.

### Genpaneelbronnen en -versies

Genpanels dragen hun herkomst in de kolommen die schema **`013_gene_panel_sources.sql`** aan `gene_panels` toevoegt: `source`, `external_id`, `external_version`, `external_url`, `source_updated_at`, `source_metadata`. Een uniek index op `(source, external_id)` voorkomt dat hetzelfde externe panel (bv. een PanelApp-panel) dubbel wordt geïmporteerd.

De echte versiecontrole komt van schema **`034_gene_panel_versions.sql`**. Dit voegt een `version`-teller aan `gene_panels` toe en introduceert de tabel **`gene_panel_versions`**: bij elke wijziging van een panel wordt een **onveranderlijke momentopname** gearchiveerd met `version`, `name`, `source`, `external_version` (bv. de PanelApp- of Monarch-release), `gene_count`, de volledige `genes`- en `regions`-JSONB, `source_metadata`, plus `created_by` én `created_by_email` (het e-mailadres wordt gedenormaliseerd opgeslagen zodat de auteur traceerbaar blijft, zelfs als het account later verdwijnt). Het commentaar in het schema vat het principe samen: het live panel is altijd gelijk aan de laatste versie-momentopname, en een update overschrijft of verwijdert nooit de vorige inhoud — die blijft getimestampeerd bewaard en opvraagbaar.

De schrijf- en leeslogica staat in `backend/app/services/panel_metadata_service.py` (`_snapshot_panel_version`, `list_panel_versions`, `get_panel_version`), ontsloten via `GET /panels/{panel_id}/versions` en `GET /panels/{panel_id}/versions/{version}` in `backend/app/routers/panels.py`. Zo kan een reviewer voor elk rapport terug naar de exacte paneelversie die op dat moment gold.

### Waarom dit reproduceerbaarheid en rapport-binding ondersteunt

De combinatie is bewust: `reference_dataset_imports` legt vast welke *referentie-genen/cytobanden/CNV's* actief waren en wanneer; `gene_panel_versions` legt vast welke exacte *genset* een panel op een moment had; `gene_info.source_status` legt per gen vast uit welke externe bronnen (met `fetched_at`) de verrijking kwam. Voor de externe applicatie-releasecontext biedt `backend/app/services/github_releases_service.py` bovendien een gecachte GitHub-release-catalogus (`get_github_release_catalog`), zodat de software-versie zelf ook zichtbaar is. Samen geven ze een auditor het volledige antwoord op "welke referentiedata-versie lag ten grondslag aan dit rapport?".

## Admin: syncs starten en versies inspecteren

De beheerderspagina is `frontend/src/pages/admin/GeneReferenceAdminPage.tsx`. Een beheerder kan daar:

- **één gen** opnieuw laten cachen (invoerveld + "Refresh gene" → `POST /admin/gene-reference/refresh-gene?symbol=…`);
- **alle geïmporteerde humane genen** opnieuw laten cachen ("Refresh all human genes" → `POST /admin/gene-reference/refresh-all`);
- de **actieve job** volgen met een voortgangsbalk (percentage, `completed/total` genen, gecachte records, huidig symbool) die elke 3 seconden ververst zolang een job loopt (en anders elke 15 seconden);
- de **bron-dekking** inspecteren: een tabel per bron (HGNC, Ensembl, NCBI, ClinGen, GenCC, ClinVar, dbNSFP) met laatste fetch-tijd en tellingen voor success/missing/error/records;
- de **jobgeschiedenis** bekijken (laatste 12 jobs met scope, symbool, status, voortgang, records, voltooiingstijd).

De statusdata komt van `GET /admin/gene-reference/status` → `list_gene_reference_admin_status` (`gene_info_jobs_pg.py`), dat de bronstatistieken aggregeert door de `source_status`-JSONB van alle `gene_info`-rijen te ontleden (`_aggregate_gene_info_source_summaries`, via een `jsonb_each`-LATERAL join).

**Veiligheid & toegangscontrole:** alle drie de gene-reference-endpoints in `backend/app/routers/admin.py` hangen aan `Depends(get_current_admin_user)` — alleen een admin mag syncs starten of de status zien. De `requested_by` wordt gezet op `user.email`, zodat elke gestarte sync herleidbaar is tot een concrete beheerder (zie ook hoofdstuk [Gebruikersrollen, machtigingen & afscherming](02-beveiliging-rollen-rechten.md)). De unieke `active_slot` (409 bij dubbele start) beschermt bovendien tegen dubbele of concurrente syncs.

## MANE/transcript-badging

Klinisch is niet elk transcript gelijkwaardig: het **MANE Select**-transcript (Matched Annotation from NCBI and EMBL-EBI) is het aanbevolen referentietranscript, met daarnaast **MANE Plus Clinical** voor klinisch belangrijke extra transcripten, **RefSeq Select** en **Ensembl Canonical** als aanvullende referenties. CoGA bepaalt en toont deze op twee plaatsen.

**In de UI** (`GeneInfoPage.tsx`): de functie `transcriptBadgesFor` vergelijkt elk transcript-id met de referenties in `TranscriptAnnotationContext` (`maneSelect`, `manePlusClinical`, `ensemblCanonical`, `refseqSelect`) en kent badges toe. De referenties worden gevuld uit het profiel: `clingenFacts.mane_select_transcript` en `mane_plus_clinical_transcript`, `profile.extra.ensembl_canonical_transcript` en `profile.extra.refseq_accessions`. De vergelijking is versie-tolerant via `normalizeTranscriptId`, dat het versiesuffix na de punt weglaat (bv. `NM_007294.4` matcht `NM_007294`). De transcriptenlijst wordt vervolgens gesorteerd zodat geannoteerde transcripten (MANE eerst) bovenaan staan; `classifyTranscript` labelt elk id nog als Ensembl/RefSeq/Imported op basis van het prefix (`ENS…` vs. `NM_/NR_/XM_/XR_`).

**In de backend** wordt hetzelfde principe gebruikt om, bij het tekenen van genen in een genomische regio, per gen het "beste" transcript te kiezen. `_gene_transcript_priority` en `_select_preferred_gene_rows` in `reference_metadata_service.py` rangschikken transcripten als MANE Select (rang 0) → Ensembl Canonical (rang 1) → overige (rang 2), en breken gelijke stand met transcriptlengte en aantal exonen. De MANE/canonical-referentie wordt daarbij uit meerdere mogelijke veldnamen gehaald (`mane_select_transcript`, `MANE_SELECT`, `ensembl_canonical_transcript`, `canonical_transcript`, …) om robuust te zijn tegen verschillen tussen bronnen. Deze verrijking komt binnen via de `LEFT JOIN gene_info gi` in `get_gene_region_records` (zelfde bestand), die de gecachte `gene_info.extra` aan elke gen-rij koppelt.

## Belangrijkste bestanden

| Bestand | Rol |
|---|---|
| `backend/app/routers/genes.py` | Endpoints `/genes/search`, `/genes/profile`, `/genes/{assembly}/{chrom}` |
| `backend/app/services/gene_metadata_service.py` | Bouwt het gen-profiel uit `genes` + `gene_info`; zoek-autocomplete; externe link-outs; toegangscontrole familie/project |
| `backend/app/services/gene_info_external.py` | Live per-gen REST-bronnen (HGNC, Ensembl, NCBI, ClinGen) + bundeling |
| `backend/app/services/gene_info_bulk_sources.py` | Bulk-parsers dbNSFP, ClinGen validity/dosage, GenCC, ClinVar gene-condition |
| `backend/app/services/gene_info_jobs_pg.py` | Refresh-jobs: queue, atomair claimen, worker, upsert naar `gene_info` |
| `backend/app/services/panelapp_service.py` | PanelApp-zoeken/-import met paneelversie-metadata |
| `backend/app/services/reference_source_service.py` | UCSC-import van gentabellen/cytobanden (gevalideerde host) |
| `backend/app/services/reference_metadata_service.py` | Eén schrijfpad `apply_reference_dataset_text`; transcript-prioriteit/MANE-selectie |
| `backend/app/services/panel_metadata_service.py` | Paneelversie-momentopnamen schrijven en lezen |
| `backend/app/services/github_releases_service.py` | Gecachte GitHub-release-catalogus (software-versiecontext) |
| `backend/db/schema/postgres/001_metadata.sql` | Tabellen `genes`, `gene_info`, `gene_info_refresh_jobs`, `gene_panels` |
| `backend/db/schema/postgres/013_gene_panel_sources.sql` | Paneel-herkomstkolommen (`source`, `external_id`, `external_version`, …) |
| `backend/db/schema/postgres/021_reference_dataset_imports.sql` | Audit-tabel voor elke referentiedataset-import |
| `backend/db/schema/postgres/034_gene_panel_versions.sql` | Onveranderlijke paneelversie-momentopnamen |
| `backend/db/schema/postgres/042_gene_search_indexes.sql` | Expressie-indexen voor gen-autocomplete en constraint-lookups |
| `backend/app/routers/admin.py` | Admin-endpoints `/admin/gene-reference/status`, `/refresh-all`, `/refresh-gene` |
| `backend/app/routers/panels.py` | PanelApp-import en paneelversie-endpoints (`/panels/{panel_id}/versions`) |
| `frontend/src/pages/genes/GeneInfoPage.tsx` | De Gene Explorer-pagina inclusief transcript-badging |
| `frontend/src/pages/admin/GeneReferenceAdminPage.tsx` | Beheerpagina om syncs te starten en bron-/jobstatus te inspecteren |
