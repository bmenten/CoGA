# 3. Databankstructuren (Postgres & ClickHouse)

Dit hoofdstuk beschrijft hoe CoGA zijn gegevens over twee databanken verdeelt: **Postgres** voor metadata, toegangsrechten, reviewtoestand en de klinische papiersporen, en **ClickHouse** voor de enorme aantallen variant- en trackrijen. Het toont hoe de tabellen gegroepeerd zijn, hoe een ClickHouse-rij weer aan de Postgres-metadata wordt gekoppeld, en welke garanties in de databanklaag zelf zitten. De tabel-per-tabelreferentie is `docs/database.md`; die geldt als bron van waarheid.

Enkele begrippen: **DDL** (*Data Definition Language*) zijn de SQL-commando's die tabellen aanmaken of wijzigen; een **foreign key** is een verwijzing van een rij naar een rij in een andere tabel; **idempotent** betekent dat een bewerking veilig meermaals kan draaien met hetzelfde resultaat.

## Twee databanken, één afspraak

Postgres is de bron van waarheid voor alles wat relationeel, transactioneel en veiligheidskritisch is. Het biedt foreign keys, `CHECK`-regels en triggers: precies wat toegangscontrole en een onwrikbaar auditspoor nodig hebben. ClickHouse is gebouwd om over honderden miljoenen rijen snel te filteren en te tellen; daar staan de varianten en de grote interval-tracks (hoofdstuk 1 legt de keuze uit).

Metadatarijen gebruiken UUID's als sleutel. De variant-id's die de API toont, zijn stabiele tekenreeksen (bv. `1-1000-A-T`) die losstaan van de opslag. Voor mensen blijven `family_id` en `sample_id` de herkenbare namen.

## Postgres: vijf baseline-bestanden

Het Postgres-schema staat in vijf SQL-bestanden in `backend/db/schema/postgres/`, gegroepeerd per domein. Elke tabel wordt er één keer aangemaakt, in haar eindvorm.

| Bestand | Domein | Belangrijkste tabellen |
| --- | --- | --- |
| `01_access.sql` | Genoombasis en toegang | `species`, `assemblies`, `chromosomes` (met de cytobanden), `users` (met de rolregel), `projects`, `project_users`, `auth_login_attempts` |
| `02_reference.sql` | Referentie- en kennisdata | `genes`, `gene_info`, `blacklist`, `clinical_cnvs`, `dgv_variants`, `segmental_duplications`, de panels (`gene_panels`, `gene_panel_versions`, …), de HPO-tabellen, de Monarch-tabellen, `repeat_loci`, `reference_dataset_imports`, en de jobtabellen `gene_info_refresh_jobs` en `clinical_cnv_kb_jobs` |
| `03_assay.sql` | Families, samples en reviewtoestand | `families`, `samples`, `family_members`, `family_projects`, `sample_projects`, `family_relationships`, `individual_hpo`, de assaydata (`repeat_expansions`, `sample_paraphase_results`, `nipt_artifact_variants`, `sample_interval_track_sources`), de reviews (`small_variant_reviews`, `structural_variant_reviews`), tags en filterpresets, `family_import_jobs`, de caches (`family_variant_ranking_cache`, `family_sv_gene_index`) en de sequencing-QC-grenzen (`qc_threshold_profiles`, `qc_thresholds` en hun append-only geschiedenis `qc_threshold_changes`) |
| `04_traceability.sql` | Herkomst en klinisch auditspoor | `raw_import_files`, `family_annotation_manifest`, `audit_log_events`, `clinical_audit_events`, `report_signouts`, `integrity_anchors`, `ui_events`, plus de append-only triggers (ook die op `qc_threshold_changes`) |
| `05_grants.sql` | Rechtenscheiding | De beperkte runtime-rol `coga_app` en de `REVOKE` op de append-only tabellen (hoofdstuk 2) |

Twee kenmerken vallen op. Bijna alle referentiedata per assembly verwijst naar `assemblies` met `ON DELETE CASCADE`; de Monarch-tabellen zijn bewust assembly-onafhankelijk (gekoppeld op HGNC- en MONDO-id's). En het bevroren ACMG-bewijs per classificatie staat in de kolom `small_variant_reviews.acmg_evidence_snapshot`, dat van een CNV-classificatie in `structural_variant_reviews.cnv_evidence_snapshot` (hoofdstuk 10).

**Waar in de code:** de bestanden in `backend/db/schema/postgres/`; de volledige kolomreferentie in `docs/database.md`.

## ClickHouse: tabellen per assembly

Het ClickHouse-schemabestand `backend/db/schema/clickhouse/001_coga_variant_storage.sql` maakt alleen de databank aan. De eigenlijke tabellen maakt de backend tijdens het draaien aan, **per assembly**, omdat hun namen met de assembly beginnen (bv. `` `GRCh38/SNV_INDEL/entries` ``). Eerst wordt de assemblynaam omgezet naar een veilige sleutel: tekens buiten letters, cijfers, punt, underscore en koppelteken (bv. een spatie) worden `_`, dus `T2T CHM13v2.0` wordt `T2T_CHM13v2.0`. Import en lezen gebruiken dezelfde functie, zodat ze altijd dezelfde tabel vinden.

| Tabellen (per assembly) | Wat ze bevatten |
| --- | --- |
| `SNV_INDEL/variants/details`, `…/annotations`, `…/annotation_index`, `…/gene_index` | Het variantrecord, de volledige annotatie per annotatieversie, en de indexen waarop de filterpagina's zoeken (op annotatievelden en op gen) |
| `SNV_INDEL/entries` | De calls per familie: per variant de genotypes, diepte en kwaliteit van elk sample. De meest gelezen tabel |
| `SNV_INDEL/family_variant_summary`, `…/family_sample_variant_summary`, `…/family_data_version` | Samenvattingen per familie en per sample, en een versie die bij elke wijziging van de variantdata van een familie verandert (de ranking-cache gebruikt die, hoofdstuk 12) |
| `SV/variants/details`, `SV/key_lookup`, `SV/entries`, `…/family_data_version` | Hetzelfde voor structurele varianten, met een eigen versie die bij elke wijziging van de SV's van een familie verandert (de SV-index voor de tweede hit gebruikt die, hoofdstuk 8) |
| `INTERVAL/entries` | Interval-tracks: dekking, CNV-segmenten, APCAD en haplotypeblokken, met per rij de bron en de bestandsnaam |

Voor een reviewer zijn vier dingen belangrijk:

- De `entries`-tabellen trekken een rij bij een nieuwe import logisch in met een `sign`-kolom (+1/−1). Correcte tellingen vragen dus altijd `sign = 1`.
- De `entries`-tabellen zijn verdeeld per project (`PARTITION BY project_guid`). Dat maakt de projectfilter goedkoop en houdt vragen over projecten heen afgebakend.
- Een rij in `entries` is de call van één callset (`source`) voor één variant, in één familie en één project. Wanneer ClickHouse de onderdelen van een tabel samenvoegt, houdt het één rij per sorteersleutel over; daarom eindigt de sorteersleutel op `key, source`. Zo blijven de rechtstreekse call (clair3) en de geïmputeerde call (GLIMPSE2) van één variant twee rijen, en de diagnostische lijsten tonen de rechtstreekse. Bij een SV bevat ook de sleutel zelf de bron, omdat de id van een SV uit een upload per sample de caller niet noemt: twee callers op dezelfde breekpunten blijven twee rijen. De variant-id hangt niet van de callset af, en reviews, classificatiesnapshots, de ranking-cache en de SV-index voor de tweede hit hangen eraan vast.
- Er is geen aparte, vooraf berekende telling over het cohort: de Variant Explorer telt dragers rechtstreeks uit `entries`, met `sign = 1` en de projectfilter (hoofdstuk 14).

**Waar in de code:** `ensure_clickhouse_variant_tables(assembly_name)` in `backend/app/services/clickhouse_variant_storage.py`, `ensure_clickhouse_interval_table(assembly_name)` in `backend/app/services/clickhouse_interval_tracks.py`, en `clickhouse_dataset_key` in `backend/app/core/clickhouse.py`. De sleutels worden gebouwd in `backend/app/services/clickhouse_variant_ids.py`.

## Hoe een ClickHouse-rij aan Postgres gekoppeld wordt

De twee databanken delen geen fysieke join; de koppeling gebeurt in de applicatie via gedeelde sleutels. De ClickHouse-kolom `project_guid` bevat de Postgres-project-UUID (`projects.id`) als tekst, en `family_guid` de familie-UUID (`families.id`).

1. De backend bepaalt uit **Postgres** welke familie, samples en projecten de gebruiker mag zien.
2. Die UUID's gaan als parameter in de **ClickHouse**-query (`family_guid`, `project_guid IN …`), zodat alleen toegestane rijen terugkomen.
3. De varianten die terugkomen, worden aangevuld met de reviewtoestand uit **Postgres**. De reviewtabellen zijn uniek per familie en variant-id, zodat precies één review bij één variant in één familie hoort.

De toegangsbeslissing valt dus altijd eerst in Postgres; ClickHouse ziet alleen de al gefilterde sleutels. Wie wil nagaan dat gegevens niet tussen projecten lekken, controleert twee dingen: dat de scope in Postgres juist wordt bepaald (hoofdstuk 2), en dat elke ClickHouse-query de projectfilter meekrijgt (hoofdstukken 8 en 14).

**Waar in de code:** `backend/app/services/clickhouse_family_variants.py` (familievarianten) en `backend/app/services/variant_explorer_service.py` (varianten over projecten heen).

## Hoe de schema's worden toegepast

CoGA houdt geen migratiegrootboek bij (zoals Alembic met een versietabel). Telkens wanneer het schema wordt toegepast (standaard bij elke start van de backend), worden **alle** Postgres-bestanden opnieuw uitgevoerd, in naamvolgorde en binnen één transactie. Daarom is elk statement idempotent geschreven (`CREATE TABLE IF NOT EXISTS`, `ON CONFLICT DO NOTHING` voor startgegevens, `GRANT`/`REVOKE`). Een destructieve `UPDATE` hoort daardoor nooit in een schemabestand thuis: ze zou bij elke herstart opnieuw draaien.

De ClickHouse-tabellen maakt de backend met `CREATE TABLE IF NOT EXISTS` aan; een ontbrekende kolom komt er met `ADD COLUMN IF NOT EXISTS` bij. De sorteersleutel van een tabel ligt echter vast zodra ClickHouse de tabel aanmaakt. Daarom vergelijkt de backend bij het opstarten, en telkens voordat hij de tabellen van een assembly aanmaakt, de sorteersleutel van `SNV_INDEL/entries`, `SV/entries` en `SV/key_lookup` met de verwachte. Heeft een tabel een andere sleutel, dan weigert de backend te starten; de melding noemt de tabel, haar sleutel en de sleutel die nodig is. De rijen worden niet naar een nieuwe tabel gekopieerd: onder de andere sleutel kunnen samenvoegingen al calls hebben laten vallen, en alleen een nieuwe import brengt ze terug. Het herstel (de small-variant- en SV-tabellen van die assembly verwijderen, opnieuw starten en elke familie van de assembly opnieuw importeren) staat in `docs/database.md`, onder "Row identity".

Wie het schema toepast (de app zelf als eigenaar, of een aparte migratiestap) en in welke volgorde de backend opstart, staat in [hoofdstuk 4](04-deployment-en-seeding.md).

**Waar in de code:** `init_postgres_schema` in `backend/app/core/postgres.py`, `init_clickhouse_schema` in `backend/app/core/clickhouse.py` en `verify_clickhouse_variant_storage_identity` in `backend/app/services/clickhouse_variant_storage.py`.

## Garanties in de databanklaag

Veel IVDR-garanties zijn in de databank zelf afgedwongen, zodat ook een fout in de applicatie of een misbruikt API-verzoek ze niet kan omzeilen.

- **Append-only via triggers.** `audit_log_events`, `clinical_audit_events`, `report_signouts`, `integrity_anchors` en `qc_threshold_changes` hebben elk een trigger die `DELETE` blokkeert en `UPDATE` weigert. De enige uitzondering is het op `NULL` zetten van een verwijzing naar een gebruiker of familie wanneer die wordt verwijderd; gedenormaliseerde kolommen (bv. `user_email`, `family_identifier`) bewaren dan wie het was. De triggers staan in `04_traceability.sql`.
- **Hash-keten.** `clinical_audit_events` en `report_signouts` dragen `row_hash` en `prev_hash`: elke rij bindt haar inhoud aan de vorige rij van dezelfde familie. Wijzigen, wissen of herschikken wordt zo zichtbaar. `report_signouts` draagt daarnaast een hash van het bevroren rapport. De HTTP-toegangslog `audit_log_events` is append-only via zijn trigger, maar niet hash-geketend.
- **Integriteitsankers.** Telkens wanneer een beheerder of een externe planner `POST /api/admin/integrity/anchor` aanroept, legt `integrity_anchors` de koppen van alle ketens vast, ondertekend met een Ed25519-sleutel die buiten de databank staat. Buiten ontwikkeling wordt een niet-ondertekend anker geweigerd. CoGA plant dit niet zelf in: hoe vaak het gebeurt, is een procedureafspraak (hoofdstuk 11).
- **Rechtenscheiding.** `05_grants.sql` maakt de rol `coga_app` aan zonder login en trekt `UPDATE`, `DELETE` en `TRUNCATE` in op de vijf append-only tabellen. De migratiestap zet die login aan met `POSTGRES_APP_PASSWORD` (op Google Cloud met `db_runtime_role = "coga_app"`). Standaard draait de app nog als eigenaar (hoofdstuk 2).
- **Toegangsregels in het schema.** De `CHECK` op `users.role` laat alleen `admin`, `superuser` en `viewer` toe. Foreign keys met `ON DELETE CASCADE` ruimen bij het verwijderen van een familie of sample de bijbehorende rijen mee op.
- **Herkomst.** `raw_import_files` (met de SHA-256 van elk bronbestand; voor een alignment die in een bucket blijft, het record van de opslag), `family_annotation_manifest` (de tool- en databankversies per familie) en `reference_dataset_imports` (elke import van referentiedata) leggen vast welke bestanden en versies een resultaat produceerden: de basis voor de traceerbaarheid in [hoofdstuk 11](11-rapport-en-traceerbaarheid.md).

## Belangrijkste bestanden

| Bestand | Rol |
| --- | --- |
| `docs/database.md` | Referentie van alle tabellen en kolommen (bron van waarheid) |
| `backend/db/schema/postgres/01_access.sql` … `05_grants.sql` | De vijf idempotente Postgres-baselines |
| `backend/db/schema/clickhouse/001_coga_variant_storage.sql` | Maakt de ClickHouse-databank aan |
| `backend/app/core/postgres.py` | Postgres-verbinding en het toepassen van het schema |
| `backend/app/core/clickhouse.py` | ClickHouse-client en de veilige assemblysleutel |
| `backend/app/services/clickhouse_variant_storage.py` | Maakt de variant-tabellen per assembly aan |
| `backend/app/services/clickhouse_interval_tracks.py` | Maakt de interval-tabel per assembly aan |
| `backend/app/services/variant_explorer_service.py` | De koppeling tussen Postgres-UUID's en ClickHouse-sleutels, over projecten heen |
