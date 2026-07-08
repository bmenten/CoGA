# 3. Databankstructuren (Postgres & ClickHouse)

In dit hoofdstuk leer je hoe CoGA zijn gegevens over twee databanken verdeelt: **Postgres** voor metadata, toegangsrechten, review-toestand en de klinische "papiersporen" (traceerbaarheid), en **ClickHouse** voor de enorme aantallen variant- en trackrijen. Je krijgt een volledige, per-bestand geverifieerde inventaris van alle Postgres-schemabestanden (`001` t/m `042`), een beschrijving van het ClickHouse-schema en de variant-tabellen, en je ziet exact via welke sleutels een ClickHouse-variantrij bij het beantwoorden van een verzoek weer aan de Postgres-metadata wordt gekoppeld. Tot slot leggen we uit hoe de schema's worden toegepast en beheerd, en welke veiligheids- en traceerbaarheidsgaranties in de databanklaag zelf zijn ingebakken.

Enkele begrippen die verderop terugkomen: een *DSN* is de verbindingsstring naar een databank; *DDL* (Data Definition Language) zijn de SQL-commando's die tabellen aanmaken of wijzigen (`CREATE TABLE`, `ALTER TABLE`); een *foreign key* (FK, "vreemde sleutel") is een verwijzing van een rij naar een rij in een andere tabel; en *idempotent* betekent dat een bewerking veilig meermaals kan draaien met exact hetzelfde eindresultaat.

## Waarom twee databanken?

CoGA gebruikt bewust een **gesplitst opslagmodel**. De motivatie staat in `docs/storage-architecture.md` en `docs/database.md`.

- **Postgres** is de *bron van waarheid* voor alles wat relationeel, transactioneel en veiligheidskritisch is: wie is welke gebruiker, welke projecten mag die zien, welke families/samples/pedigrees bestaan, welke reviews en classificaties er zijn gemaakt, plus alle referentiedata (soorten, assemblies, genen, panels, HPO) en de traceerbaarheidssporen. Postgres biedt sterke consistentie, FK-integriteit, `CHECK`-constraints en triggers - precies wat je nodig hebt voor toegangscontrole en een onwrikbaar audittrail.
- **ClickHouse** is een kolomgeorienteerde analytische databank die is gebouwd om over honderden miljoenen rijen razendsnel te aggregeren en filteren. Daarin leven de eigenlijke *varianten* (small variants / SNV+indel, structurele varianten) en de hoog-volume *interval-tracks* (coverage, WisecondorX-segmenten, APCAD, haplotypes). Deze data is te groot en te "read-heavy" (leesintensief) voor Postgres.

De taakverdeling bij het afhandelen van een verzoek (uit `docs/storage-architecture.md`, sectie "Runtime Flow"):

1. FastAPI bepaalt de gebruiker en diens familie-/sample-scope uit **Postgres**.
2. Metadata-endpoints lezen volledig uit **Postgres**.
3. Variant-lijst- en query-endpoints lezen familie-gescoopte records uit **ClickHouse**.
4. Review-annotaties worden vanuit **Postgres** teruggekoppeld ("joined back") op de ClickHouse-resultaten.
5. Upload-endpoints schrijven metadata naar **Postgres** en de zware variant/track-payloads naar **ClickHouse**.

Een belangrijke identifier-afspraak (`docs/database.md`, "Identifier Rules"): metadata-rijen gebruiken **UUID**-sleutels; de variant-ID's die de API naar buiten toont zijn stabiele *strings* die losstaan van de opslag; en de mensvriendelijke identifiers blijven `family_id` en `sample_id`.

## De genummerde Postgres-schemabestanden (001–042)

Het schema is *niet* een enkel groot bestand, maar een reeks genummerde `.sql`-bestanden in `backend/db/schema/postgres/`. Ze worden in gesorteerde (numerieke/alfabetische) volgorde toegepast (zie "Hoe de schema's worden beheerd" verderop). Elk bestand is ofwel een `CREATE TABLE`-bestand (nieuwe tabellen) ofwel een `ALTER TABLE`-bestand (kolommen/constraints toevoegen aan bestaande tabellen) - dat laatste is de manier waarop het schema historisch is gegroeid zonder ooit een bestaand bestand destructief te herschrijven.

**Waar in de code:** de lijst met bestanden wordt opgebouwd door `_schema_files()` in `backend/app/core/postgres.py` (`schema_dir.glob("*.sql")` + `sorted(...)`).

De volgende tabel koppelt **elk** bestand aan de tabellen die het aanmaakt/wijzigt en aan het doel. (Merk op: er zijn twee bestanden met nummer `026`.)

| Bestand | Tabellen (aangemaakt / gewijzigd) | Doel |
|---|---|---|
| `001_metadata.sql` | `users`, `species`, `assemblies`, `projects`, `project_users`, `families`, `family_projects`, `samples`, `sample_projects`, `family_members`, `family_relationships`, `family_structure_versions`, `chromosomes`, `genes`, `blacklist`, `clinical_cnvs`, `repeat_loci`, `gene_info`, `gene_info_refresh_jobs`, `small_variant_reviews`, `small_variant_filter_presets`, `small_variant_tag_definitions`, `small_variant_tag_definition_project_links`, `gene_panels`, `gene_panel_genes`, `gene_panel_regions` | Het kernschema: gebruikers/toegang, projecten, families/samples/pedigree, referentiedata, review- en tag-toestand, panels. Activeert ook de `pgcrypto`-extensie (voor `gen_random_uuid()`). |
| `002_repeat_expansions.sql` | `repeat_expansions` | Per-sample repeat-expansie-calls (TRGT): motief, allel-lengtes, pathogeniciteitsstatus. |
| `003_interval_tracks.sql` | `sample_interval_track_sources` | Bron-metadata per interval-track (coverage/apcad/segments/haplotype); de eigenlijke rijen staan in ClickHouse. |
| `004_audit_logs.sql` | `audit_log_events` | HTTP-toegangs-audittrail (wie deed welk verzoek, status, duur, IP). |
| `005_gene_panel_description.sql` | `gene_panels` (+`description`) | Voegt een beschrijvingskolom toe aan panels. |
| `006_project_scoped_variant_tags.sql` | `small_variant_tag_definitions` (+`scope`,`project_id`), `small_variant_tag_definition_project_links` | Maakt variant-tags project-scoped (naast global). |
| `007_family_import_jobs.sql` | `family_import_jobs` | Achtergrond-jobtracking voor package-imports (status, validatiefouten, logs). |
| `008_paraphase_results.sql` | `sample_paraphase_results` | Paraphase copy-number/fasering-resultaten per sample+gen. |
| `009_structural_variant_reviews.sql` | `structural_variant_reviews`, `structural_variant_filter_presets` | Review-toestand en filter-presets voor structurele varianten. |
| `010_auth_login_attempts.sql` | `auth_login_attempts` | Teller/lockout-toestand voor mislukte logins (brute-force-bescherming). |
| `011_pgt_family_roles.sql` | `family_members` (role-`CHECK`) | Breidt toegestane pedigree-rollen uit met `embryo` en `relative` (PGT). |
| `012_segmental_duplications.sql` | `segmental_duplications` | Referentietrack van segmentale duplicaties/LCR's per assembly. |
| `013_gene_panel_sources.sql` | `gene_panels` (+`source`,`external_*`,`source_metadata`) | Herkomst van externe panels (bv. PanelApp). |
| `014_family_structure_relationships.sql` | `family_members` (carrier/clinical-kolommen), `family_relationships`, `family_structure_versions` | Rijkere pedigree: carrier-/klinische status, expliciete relaties, geversioneerde structuur-snapshots. |
| `015_hpo.sql` | `hpo_term`, `hpo_synonym`, `hpo_edge`, `hpo_closure`, `individual_hpo` | HPO-ontologie (termen, synoniemen, is_a-graaf, voorbereken-closure) + fenotypes per individu. |
| `016_superuser_role.sql` | `users` (role-`CHECK`) | Voegt de `superuser`-rol toe aan de toegestane rollen. |
| `017_raw_import_files.sql` | `raw_import_files` | Provenance van elk ruw bronbestand van een import (pad, grootte, SHA-256). |
| `018_clinical_cnv_details.sql` | `clinical_cnvs` (+`omim_id`,`decipher_id`,`description`) | Gecureerde syndroom-detailvelden voor klinische CNV's. |
| `019_clinical_cnv_kb_jobs.sql` | `clinical_cnv_kb_jobs` | Jobtracking voor het herbouwen van de klinische-CNV-kennisbank. |
| `020_clinical_cnv_enrichment.sql` | `clinical_cnvs` (+`cytoband`,`source_id`,`omim_title`,`orpha_id`,`orpha_name`) | Extra gecureerde detailkolommen voor klinische CNV's. |
| `021_reference_dataset_imports.sql` | `reference_dataset_imports` | Per-(assembly, dataset) import-tracking (wanneer/door wie/hoeveel rijen). |
| `022_dgv_variants.sql` | `dgv_variants` | Database of Genomic Variants (DGV) SV-intervallen per assembly. |
| `023_ui_events.sql` | `ui_events` | Client-side UI-interacties (clicks, navigatie) die de backend anders niet ziet. |
| `024_family_metadata.sql` | `family_statuses` (+ seed), `families` (+`status_id`,`assigned_to_id`,`reviewed_by_id`) | Workflow-status-catalogus en per-familie status/toewijzing/reviewer. |
| `025_acmg_classification.sql` | `small_variant_reviews` (+`acmg`,`acmg_point_total`,`acmg_class`) | Semi-automatische ACMG/AMP-classificatie voor small variants. |
| `026_monarch_associations.sql` | `monarch_gene_disease` | Monarch gen→ziekte-associaties (HGNC/MONDO), assembly-onafhankelijk. |
| `026_structural_acmg_classification.sql` | `structural_variant_reviews` (+`cnv_acmg`,`cnv_point_total`,`cnv_class`) | Semi-automatische ClinGen CNV-classificatie voor SV's. |
| `027_monarch_disease_phenotype.sql` | `monarch_disease_phenotype` | Monarch ziekte→fenotype (HPO)-associaties voor verwacht-vs-waargenomen. |
| `028_nipt_artifacts.sql` | `nipt_artifact_variants` | Recurrente-artefactenlijst (panel-of-normals) voor monogene NIPT. |
| `029_audit_log_immutable.sql` | trigger + functie op `audit_log_events` | Maakt het toegangs-audittrail *append-only* op databankniveau. |
| `030_family_annotation_manifest.sql` | `family_annotation_manifest`, `reference_dataset_imports` (+`source_version`,`source_release_date`,`source_url`) | Traceerbaarheid fase 0: welke annotatiemodules/-versies produceerden de input per familie. |
| `031_classification_evidence_snapshot.sql` | `small_variant_reviews` (+`acmg_evidence_snapshot`) | Traceerbaarheid fase 1: bevriest het bewijs waarop een classificatie berustte (drift-detectie). |
| `032_clinical_audit_events.sql` | `clinical_audit_events` (+ trigger/functie) | Traceerbaarheid fase 2: onwrikbaar log van klinische beslissingen (voor→na). |
| `033_report_signouts.sql` | `report_signouts` (+ trigger/functie) | Traceerbaarheid fase 3: geversioneerde, content-gehashte case-sign-out. |
| `034_gene_panel_versions.sql` | `gene_panel_versions`, `gene_panels` (+`version`) | Onveranderlijke versie-snapshots van elk genpaneel (bv. Mendeliome). |
| `035_family_variant_ranking_cache.sql` | `family_variant_ranking_cache` | Cache van de fenotype-geprioriteerde small-variant-ranking per familie. |
| `036_ranking_cache_superset.sql` | `family_variant_ranking_cache` (+`base_hash`,`panel_id`) | Maakt de ranking-cache paneel-agnostisch (smaller paneel uit brede cache). |
| `037_family_sv_gene_index.sql` | `family_sv_gene_index`, `family_sv_gene_index_status` | Per-familie index van genen die door een SV geraakt worden (SNV+SV compound het). |
| `038_clinical_audit_hash_chain.sql` | `clinical_audit_events` (+`row_hash`,`prev_hash`) | Per-familie tamper-evidence hash-ketting op het klinische audittrail. |
| `039_report_signouts_hash_chain.sql` | `report_signouts` (+`row_hash`,`prev_hash`) | Per-familie hash-ketting over ondertekende rapportversies. |
| `040_app_runtime_role_privileges.sql` | rol `coga_app` (GRANT/REVOKE) | DB-privilegescheiding: beperkte runtime-rol die audit/sign-out-rijen niet mag wijzigen/verwijderen. |
| `041_integrity_anchors.sql` | `integrity_anchors` (+ trigger, GRANT/REVOKE) | Extern-ondertekende chain-head-anchor die her-ketting/truncatie detecteerbaar maakt. |
| `042_gene_search_indexes.sql` | indexen op `genes` en `gene_info` | Prestatie-indexen voor gen-autocomplete, paneel/regio-resolve en gen-lookup. |

### Vier logische groepen

Al deze tabellen vallen uiteen in vier functionele groepen. Deze indeling helpt een reviewer om snel te zien "waar hoort iets thuis".

**(a) Toegang & projecten.** `users` (met een `role`-`CHECK` die uitsluitend `admin`/`superuser`/`viewer` toestaat, uitgebreid in `016`), `projects`, `project_users` (koppeltabel gebruiker↔project), `families`, `family_projects`, `samples`, `sample_projects`, `family_members` (pedigree-rol + aangedaan/carrier-status; de toegestane rollen worden in `011` uitgebreid), `family_relationships` en `family_structure_versions`. Deze groep bepaalt *wie wat mag zien* en vormt de scope waarbinnen alle variantqueries draaien. **Waar in de code:** `001_metadata.sql` + `014_family_structure_relationships.sql`.

**(b) Referentiedata.** Alles wat niet aan een patient hangt maar aan een genoom/kennisbank: `species`, `assemblies`, `chromosomes` (met cytobanden in de `bands`-kolom), `genes`, `gene_info` (verrijkt gen-profiel, per assembly), `repeat_loci` (repeat-catalogus), `blacklist`, `clinical_cnvs` (+ detailkolommen uit `018`/`020`), `dgv_variants`, `segmental_duplications`, de HPO-tabellen (`hpo_term`/`hpo_synonym`/`hpo_edge`/`hpo_closure`), de Monarch-tabellen (`monarch_gene_disease`, `monarch_disease_phenotype`) en de panelinfrastructuur (`gene_panels`, `gene_panel_genes`, `gene_panel_regions`, `gene_panel_versions`). Kenmerkend: bijna alle assembly-gebonden referentiedata heeft een FK `assembly_id → assemblies(id)` met `ON DELETE CASCADE`. De Monarch-tabellen zijn juist bewust *assembly-onafhankelijk* (gekoppeld op HGNC/MONDO).

**(c) Assay- & applicatiedata.** De feitelijke uitkomsten en werk-toestand: `small_variant_reviews` en `structural_variant_reviews` (classificatie, tags, notities, ACMG/CNV-classificatie), de filter-presets, `repeat_expansions`, `sample_paraphase_results`, `nipt_artifact_variants`, `individual_hpo` (fenotypes per individu), `sample_interval_track_sources`, en de caches/indexen `family_variant_ranking_cache`, `family_sv_gene_index(_status)`. Ook de jobtabellen (`family_import_jobs`, `gene_info_refresh_jobs`, `clinical_cnv_kb_jobs`, `reference_dataset_imports`) horen hier.

**(d) Traceability & klinisch.** De IVDR-kritische papiersporen: `family_annotation_manifest` (welke annotatieversies), het bevroren bewijs (`small_variant_reviews.acmg_evidence_snapshot`), de *hash-ketted append-only* audit- en sign-out-tabellen `audit_log_events`, `clinical_audit_events`, `report_signouts`, de `integrity_anchors`, en `ui_events`. Deze groep bespreken we hieronder apart, want daar zit de meeste veiligheidslogica.

## Het ClickHouse-schema

Het gebootstrapte ClickHouse-schemabestand is minimaal: `backend/db/schema/clickhouse/001_coga_variant_storage.sql` bevat één statement, `CREATE DATABASE IF NOT EXISTS coga;`. Alle eigenlijke variant-tabellen worden **at runtime, per assembly** aangemaakt - want hun namen bevatten een assembly-prefix (de "dataset key").

**Waar in de code:** `ensure_clickhouse_variant_tables(assembly_name)` in `backend/app/services/clickhouse_variant_storage.py` bouwt de tabellen; `ensure_clickhouse_interval_track_table(...)` in `backend/app/services/clickhouse_interval_tracks.py` doet dat voor de tracks. De assembly-naam wordt eerst door `clickhouse_dataset_key()` in `backend/app/core/clickhouse.py` naar een veilige prefix gemapt (identiek voor namen die al geldig zijn zoals `GRCh38`; verboden tekens - spaties, punten - worden `_`, zodat bv. `T2T CHM13v2.0` toch importeerbaar wordt), zodat ingestie en lezen altijd op dezelfde tabelnaam uitkomen.

De belangrijkste tabellen (tabelnamen zijn back-tick-paden binnen de `coga`-database, bv. `` `GRCh38/SNV_INDEL/entries` ``):

| Logische tabel | Engine | Partitionering / sortering (sleutel) | Rol |
|---|---|---|---|
| `.../SNV_INDEL/variants/details` | `ReplacingMergeTree(updatedAt)` | `ORDER BY (key, annotation_version, annotationSetHash)` | Kern-variantrecord (locus/allel), gedeeld tussen families. |
| `.../SNV_INDEL/variants/annotations` | `ReplacingMergeTree(updatedAt)` | `PARTITION BY annotation_version`, `ORDER BY (annotation_version, chrom, pos, key, …)` | Volledige annotatie-payload per annotatieversie. |
| `.../SNV_INDEL/variants/annotation_index` | `ReplacingMergeTree(updatedAt)` | `PARTITION BY annotation_version`, `ORDER BY (annotation_version, chrom, pos, key, annotationSetHash)` | Filter-index (de "platte" kolommen waarop de small-variant-filterpagina zoekt). |
| `.../SNV_INDEL/variants/gene_index` | `ReplacingMergeTree(updatedAt)` | `PARTITION BY annotation_version`, `ORDER BY (annotation_version, gene_term, chrom, pos, key, …)` | Gen→variant-index voor snelle paneel-/gen-filters. |
| `.../SNV_INDEL/entries` | `CollapsingMergeTree(sign)` | `PARTITION BY project_guid`, `ORDER BY (project_guid, family_guid, xpos, key)` | De *genotypes/calls* per familie: geneste `calls.*`-arrays (sampleId, gt, gq, dp, ab, af, ad, ps). Dit is de tabel die het meest gelezen wordt. |
| `.../SNV_INDEL/family_variant_summary` | `ReplacingMergeTree(updated_at)` | `ORDER BY (family_guid, project_guid)` | Per-familie aggregaat (variant-tellingen) voor snelle samenvattingen. |
| `.../SNV_INDEL/family_sample_variant_summary` | `ReplacingMergeTree(updated_at)` | `ORDER BY (family_guid, project_guid, sample_id)` | Per-familie-per-sample aggregaat; bevat bewust `project_guid` om cross-project lek te vermijden (zie hieronder). |
| `.../SV/variants/details` | `ReplacingMergeTree(updatedAt)` | `ORDER BY key` | Kernrecord van een structurele variant. |
| `.../SV/key_lookup` | `ReplacingMergeTree` | `ORDER BY (family_guid, variantId)` | Vertaalt stabiele SV-string-id ↔ interne key per familie. |
| `.../SV/entries` | `CollapsingMergeTree(sign)` | `PARTITION BY project_guid`, `ORDER BY (project_guid, family_guid, svType, chrom, start, key)` | SV-calls per familie (geneste `calls.*`-arrays). |
| interval-track-tabel | `MergeTree` | `PARTITION BY track_type`, `ORDER BY (family_guid, sample_guid, track_type, chrom, start, end, source)` | Coverage/APCAD/segments/haplotype-rijen. **Waar in de code:** `clickhouse_interval_tracks.py`. |

Enkele opmerkingen die voor een reviewer relevant zijn:

- De `entries`-tabellen gebruiken `CollapsingMergeTree(sign)`: rijen dragen een `sign`-kolom (+1/-1) zodat een her-import de oude rij logisch kan intrekken. Correcte tellingen vereisen daarom altijd een `WHERE sign = 1` (of een `SUM(sign)`).
- `PARTITION BY project_guid` op de `entries`-tabellen is niet alleen prestatie: het maakt project-scoping goedkoop en houdt cross-project queries afgebakend.
- De **cross-project aggregaten** die de *Global Small Variant Explorer* voeden (per-project en globale allel-/carrier-tellingen) worden *direct uit `entries`* berekend, niet uit een aparte voorge-aggregeerde tabel. **Waar in de code:** `backend/app/services/variant_explorer_service.py` telt carriers rechtstreeks uit `entries` met `sign = 1` en een `project_guid`-scope. Een eerdere `project_gt_stats`/`gt_stats`-cascade (twee `SummingMergeTree`-tabellen + materialized views) is verwijderd (`_drop_legacy_gt_stats_aggregates` in `clickhouse_variant_storage.py`), omdat niets die las en ze de `sign`-collapse negeerden - een bekende bron van foute tellingen.

## Hoe ClickHouse-rijen aan Postgres-metadata worden gekoppeld

De twee databanken delen geen fysieke join; de koppeling gebeurt in applicatiecode via **gedeelde string-sleutels**. De afspraak (gedocumenteerd bovenaan `backend/app/services/variant_explorer_service.py`): de ClickHouse-kolom `project_guid` bevat exact de Postgres-project-UUID als string (`projects.id`), en `family_guid` de familie-UUID als string (`families.id`).

De runtime-flow bij een variant-verzoek:

1. FastAPI resolvet uit **Postgres** welke familie en welke samples de gebruiker mag zien, en welke projecten in scope zijn - dit levert de UUID's op.
2. Die UUID's worden als `family_guid` / `project_guid` in de **ClickHouse**-query gestopt (met parameterbinding), zodat alleen toegestane rijen terugkomen. **Waar in de code:** `backend/app/services/clickhouse_family_variants.py`.
3. De teruggekomen ClickHouse-variantrijen worden weer verrijkt met de **Postgres**-review-toestand. `small_variant_reviews` / `structural_variant_reviews` hebben `family_id` (UUID-FK) plus zowel `variant_key` (BIGINT) als `variant_id` (TEXT), met unieke indexen `(family_id, variant_key)` én `(family_id, variant_id)`. De koppeling gebeurt in de praktijk op de stabiele stringsleutel `variant_id` (bv. `1-1000-A-T`), omdat `variant_key` alleen wordt bewaard als hij in een signed BIGINT past. Zo hangt precies één review aan één variant binnen één familie.

Deze scheiding betekent dat toegangscontrole *altijd* eerst in Postgres wordt beslist; ClickHouse ziet enkel de al-gefilterde set sleutels. Een reviewer die wil nagaan of cross-project-lek onmogelijk is, moet dus twee dingen controleren: (1) dat de scope-resolutie in Postgres correct is, en (2) dat elke ClickHouse-query de `project_guid`-filter meekrijgt (zie de project-scoping in `variant_explorer_service.py`, waar admins alle projecten en viewers enkel hun `metadata_project_ids` zien).

## Hoe de schema's worden toegepast en beheerd

CoGA gebruikt **geen** migratie-ledger (zoals Alembic met versietabel). In plaats daarvan is de migratiestrategie: *alle genummerde bestanden worden bij elke start opnieuw afgespeeld*, en elk statement is idempotent geschreven (`CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`, `ON CONFLICT DO NOTHING` bij seeds, `GRANT`/`REVOKE` die van nature idempotent zijn).

**Postgres.** `init_postgres_schema()` in `backend/app/core/postgres.py` leest de bestanden in gesorteerde volgorde, splitst elk bestand in losse statements met `_split_sql_script()` (dat expliciet `--`-commentaar en `$$ ... $$`- / `$tag$ ... $tag$`-dollar-quotes respecteert, zodat een puntkomma binnen een PL/pgSQL-triggerbody het statement niet afkapt) en voert ze uit binnen **één transactie** (`engine.begin()`). Deze functie wordt langs twee paden aangeroepen:
- **Standaard (huidige deployment):** de app self-migreert bij startup als table-owner (`POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP=true`).
- **Owner-privileged out-of-band:** `python -m backend.app.db_migrate` draait `run_schema_migrations()` als aparte deploy-stap, zodat de app zelf later als de beperkte rol `coga_app` kan booten zonder op DDL te crashen. Beide paden roepen dezelfde `init_postgres_schema()` (en `init_postgres_admin_user()`) aan - één bron van waarheid. **Waar in de code:** `backend/app/db_migrate.py`.

Omdat elk bestand *elke boot* opnieuw draait, is een destructieve `UPDATE` in een schemabestand levensgevaarlijk: hij zou bij elke herstart data resetten. Bestand `006` bevat hierover een expliciete waarschuwing (een verwijderde backfill-`UPDATE` die project-scoped tags telkens terug naar global zette) - een geverifieerd voorbeeld van deze valkuil.

**ClickHouse.** `init_clickhouse_schema()` in `backend/app/core/clickhouse.py` doet hetzelfde voor de ClickHouse-bestanden, maar met een render-stap: `_render_sql()` vervangt de hardgecodeerde `coga`-databasenaam (zowel `CREATE DATABASE ... coga` als de `coga.\``-tabelprefix) door de geconfigureerde `settings.clickhouse_database`. De per-assembly variant-tabellen worden echter niet hier, maar lazy bij ingestie/eerste gebruik aangemaakt via `ensure_clickhouse_variant_tables()`.

De opstartvolgorde (uit `docs/database.md`, "Startup Behavior") is: wacht op Postgres → pas Postgres-schema toe → zorg dat de admin-gebruiker bestaat → seed de repeat-catalogus → zorg dat Homo sapiens GRCh38 bestaat en importeer ontbrekende cytobanden/genen → seed ingebouwde hg38-referentietracks → wacht op ClickHouse → pas ClickHouse-schema toe → start de gen-refresh-worker.

## Veiligheid & traceerbaarheid in de databanklaag

Veel van CoGA's IVDR-garanties zijn niet slechts applicatielogica maar *op databankniveau afgedwongen*, wat betekent dat zelfs een bug of een misbruikte API-call ze niet kan omzeilen.

- **Append-only audittrails via triggers.** `audit_log_events` (`029`), `clinical_audit_events` (`032`), `report_signouts` (`033`) en `integrity_anchors` (`041`) hebben elk een `BEFORE UPDATE OR DELETE`-trigger die `DELETE` volledig blokkeert en `UPDATE` weigert - met één zorgvuldig afgebakende uitzondering: de `ON DELETE SET NULL`-cascade die identiteits-FK's (bv. `user_id`/`actor_id`/`family_id`) nult wanneer een account of familie wordt verwijderd. De denormaliseerde identiteitskolommen (bv. `user_email`/`actor`/`family_identifier`) bewaren wie het was ook na accountverwijdering.
- **Hash-ketting (tamper-evidence).** `038`/`039` voegen `row_hash`/`prev_hash` toe aan `clinical_audit_events` en `report_signouts`: elke rij bindt zijn inhoud aan de vorige rij *per familie*. Verwijderen, herordenen of bewerken van een rij wordt zo detecteerbaar. `report_signouts` draagt bovendien een content-hash van de canonieke snapshot.
- **Externe anchor.** `041` legt periodiek elke ketting-*head* vast en ondertekent die (met een sleutel die de databankrol niet bezit) - zodat zelfs een owner die een trigger uitzet en een keten *herberekent* (self-consistent maakt) of *trunceert* alsnog betrapt wordt door een externe verifier. De eerlijke vertrouwensgrens staat beschreven in het bestandshoofd van `041_integrity_anchors.sql` en in `backend/app/services/integrity_anchor_service.py`.
- **Privilegescheiding.** `040` maakt de runtime-rol `coga_app` aan (voorlopig `NOLOGIN`, in fallback-modus - de app connecteert nog als owner) en `REVOKE`t `UPDATE, DELETE, TRUNCATE` op de append-only tabellen. Een runtime-aanvaller kan daardoor geen bestaande audit-/sign-out-rij herschrijven of verwijderen (wel nog *toevoegen*, want `INSERT` blijft nodig). Schema-DDL blijft voorbehouden aan de owner - vandaar het aparte `db_migrate.py`-pad.
- **Toegangscontrole in het schema.** De `role`-`CHECK` op `users` (`001`/`016`) beperkt rollen tot `admin`/`superuser`/`viewer`; FK's met `ON DELETE CASCADE` zorgen dat het verwijderen van een project/familie/sample geen wees-rijen achterlaat; en `auth_login_attempts` (`010`) draagt de brute-force-lockout-toestand.
- **Provenance.** `raw_import_files` (`017`, met `sha256`), `family_annotation_manifest` (`030`) en `reference_dataset_imports` (`021`/`030`) leggen vast *welke* bronbestanden en *welke* annotatie-/referentieversies een familie hebben geproduceerd - de basis voor de rapporttraceerbaarheid (zie [hoofdstuk 11](11-rapport-en-traceerbaarheid.md)).

Voor de rollen en rechten zelf, zie [hoofdstuk 2](02-beveiliging-rollen-rechten.md); voor de importpipeline die deze tabellen vult, zie [hoofdstuk 6](06-import-pipeline.md); voor de traceerbaarheids- en sign-out-flow in detail, zie [hoofdstuk 11](11-rapport-en-traceerbaarheid.md).

## Belangrijkste bestanden

| Bestand | Rol |
|---|---|
| `docs/database.md` | Overzicht van het gesplitste schema, identifier-regels, relaties en opstartgedrag. |
| `docs/storage-architecture.md` | Motivatie en runtime-taakverdeling Postgres vs ClickHouse. |
| `backend/db/schema/postgres/001_metadata.sql` … `042_gene_search_indexes.sql` | De genummerde, idempotente Postgres-schemabestanden (kern-metadata t/m traceerbaarheid en indexen). |
| `backend/db/schema/clickhouse/001_coga_variant_storage.sql` | ClickHouse-database-bootstrap (`CREATE DATABASE`). |
| `backend/app/core/postgres.py` | Postgres-verbinding, `_split_sql_script()` en `init_postgres_schema()`. |
| `backend/app/core/clickhouse.py` | ClickHouse-client, `clickhouse_dataset_key()`, `_render_sql()` en `init_clickhouse_schema()`. |
| `backend/app/db_migrate.py` | Owner-privileged out-of-band schema-migratiestap. |
| `backend/app/services/clickhouse_variant_storage.py` | Per-assembly aanmaak van de SNV/SV-tabellen (`ensure_clickhouse_variant_tables`). |
| `backend/app/services/clickhouse_interval_tracks.py` | Per-assembly aanmaak van de interval-track-tabel. |
| `backend/app/services/variant_explorer_service.py` | Cross-project carrier-aggregatie en de UUID↔`*_guid`-koppelingsafspraak. |
| `backend/app/services/clickhouse_family_variants.py` | Familie-gescoopte variantqueries; koppelt Postgres-scope aan ClickHouse-sleutels. |
