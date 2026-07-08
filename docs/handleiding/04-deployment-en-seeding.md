# 4. Initiële deployment & seeding

Dit hoofdstuk beschrijft hoe CoGA vanaf een leeg systeem naar een draaiend platform komt: hoe de databankstructuren worden aangemaakt, hoe de eerste beheerder (admin) en alle referentiedata (soort/assembly GRCh38, cytobanden, genen, ingebouwde tracks, HPO) worden "geseed" (initieel gevuld), en in welke volgorde `backend/app/main.py` dat allemaal orchestreert bij het opstarten. Zowel de lokale Docker-stack als de productie-uitrol op Google Cloud met Terraform komen aan bod. De rode draad blijft veiligheid en traceerbaarheid: welke stap wordt afgedwongen, waar in de code, en hoe reproduceerbaarheid gewaarborgd is.

> Kort woordenlijstje dat hieronder terugkomt: een **container** is een geïsoleerd draaiend softwarepakket; **Docker Compose** start meerdere containers samen vanuit één beschrijvingsbestand; een **DSN** (Data Source Name) is de verbindingsstring naar een databank; **DDL** (Data Definition Language) is SQL die tabellen *aanmaakt/wijzigt* (`CREATE TABLE`, `ALTER TABLE`); **idempotent** betekent dat een handeling veilig meermaals kan draaien zonder extra effect; **seeding** is het initieel vullen van een lege databank met basis- of referentiegegevens.

## Van nul naar een draaiende stack

CoGA bestaat uit vier onderdelen die als aparte containers draaien: een PostgreSQL-databank (metadata en reviewstatus), een ClickHouse-databank (de grootschalige variantopslag), de FastAPI-backend en de React-frontend. Lokaal worden die samengebracht door `docker-compose.yml`.

### De productie-stijl lokale stack

`docker-compose.yml` definieert de vier `services` (`postgres`, `clickhouse`, `backend`, `frontend`). Een paar bewuste keuzes zijn hier relevant voor traceerbaarheid en robuustheid:

- **Vastgepinde image-versies.** Zowel `postgres:16` als `clickhouse/clickhouse-server:25.3` zijn niet alleen met een tag maar met een **digest** (`@sha256:...`) vastgelegd. Zo haalt elke machine exact hetzelfde image binnen — een pijler onder reproduceerbaarheid.
- **Health checks + startvolgorde.** De `backend` start pas nadat Postgres en ClickHouse `service_healthy` zijn (`depends_on`), en de `frontend` pas nadat de backend gezond is. De backend-healthcheck (een `python`-oproep naar `/api/health`, want er zit geen `curl` in het image) krijgt een ruime `start_period: 90s` omdat het opstarten het schema aanmaakt en de referentiedata seedt (zie verderop).
- **Nette afsluiting van ClickHouse.** De `clickhouse`-service krijgt `stop_grace_period: 5m`. Een commentaarregel in het bestand legt uit waarom: bij een te korte afsluittermijn kan ClickHouse midden in een flush/merge worden gedood (`SIGKILL`), wat tot corrupte data-onderdelen ("parts") leidt bij de volgende boot.
- **Build-identiteit als build-arg.** De backend-image wordt gebouwd met `APP_VERSION` en `GIT_SHA` als build-args (onder `backend.build.args`). `.env.example` waarschuwt expliciet dat je die *niet* in `.env` mag zetten: `docker-compose`'s `env_file: .env` zou dan de in het image ingebakken versie overschrijven en zo de versie *vervalsen* die in elk ondertekend rapport wordt bevroren.

**Waar in de code:** `docker-compose.yml`; build-args gedefinieerd onder `backend.build.args`; de waarschuwing staat in `.env.example`.

### De ontwikkel-stack (dev overlay)

Voor lokale ontwikkeling wordt een overlay-bestand toegevoegd bovenop de basis:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build -d
```

`docker-compose.dev.yml` zet `APP_ENV: development`, bouwt de images met het `dev`-doel (`target: dev`), koppelt de broncode als "bind mount" (`./backend:/app`, `./frontend:/app`, zodat wijzigingen live doorwerken) en start de backend met `uvicorn app.main:app --reload` en de frontend met de Vite-dev-server (`npm run dev`). Belangrijk detail: `APP_ENV: development` schakelt de strenge secret-controle uit (zie hieronder), zodat je lokaal met de placeholder-waarden mag werken.

**Waar in de code:** `docker-compose.dev.yml`.

### De backend weigert te starten met zwakke secrets

Dit is een centrale veiligheidsmaatregel. Alle instellingen worden geladen uit omgevingsvariabelen door de klasse `Settings` in `backend/app/core/config.py`. Na het laden draait de validator `validate_security_defaults`. Buiten ontwikkeling/test (dus overal waar `APP_ENV` niet in `{dev, development, local, test}` valt — bepaald door de property `is_development`) **weigert de applicatie te starten** wanneer nog placeholder-waarden actief zijn:

- `SECRET_KEY` gelijk aan `secret`/`change-me`
- `POSTGRES_PASSWORD` of `ADMIN_PASSWORD` gelijk aan `admin`/`change-me`
- de combinatie `ADMIN_USERNAME=admin` met een zwak `ADMIN_PASSWORD`

De validator gooit dan een `ValueError` met de boodschap *"Refusing to start outside development/test with insecure default credentials"*. Dezelfde validator verbiedt ook `AUDIT_LOG_DROP_ALLOWED=true` in productie (accountability-events mogen nooit stilletjes wegvallen). Een tweede, apart draaiende validator, `validate_cors_origin_regex`, controleert dat `CORS_ORIGIN_REGEX` volledig verankerd is (begint met `^` en eindigt met `$`), zodat een kwaadaardige origin niet als deelstring kan matchen — belangrijk omdat de CORS-configuratie `allow_credentials=True` gebruikt.

Het `.env.example`-bestand levert de sjabloonwaarden (met `APP_ENV=production` en overal `change-me`), precies om af te dwingen dat je die vóór een echte uitrol vervangt.

**Waar in de code:** `Settings.validate_security_defaults` en `Settings.validate_cors_origin_regex` in `backend/app/core/config.py`; sjabloon in `.env.example`.

## De databankstructuren aanmaken bij opstart

### Postgres: genummerde schemabestanden, in volgorde

De Postgres-structuur zit niet in code maar in losse SQL-bestanden onder `backend/db/schema/postgres/`, genummerd van `001_metadata.sql` tot en met `042_gene_search_indexes.sql`. De functie `init_postgres_schema` (in `backend/app/core/postgres.py`) haalt die bestanden op via de helper `_schema_files`, die ze **numeriek/alfabetisch sorteert** (`sorted(schema_dir.glob("*.sql"))`), splitst elk bestand in losse statements en voert ze uit binnen één transactie.

Twee subtiliteiten:

- De splitser `_split_sql_script` is bewust "dollar-quote-bewust": een puntkomma binnen een PL/pgSQL-functielichaam (`$$ ... $$` of `$tag$ ... $tag$`, bijvoorbeeld in de append-only audit-trigger) breekt een statement niet voortijdig af.
- Alle DDL in `001_metadata.sql` gebruikt `CREATE TABLE IF NOT EXISTS`, dus het opnieuw draaien is idempotent — het schema wordt bij élke opstart opnieuw toegepast en dat is veilig.

Het eerste bestand `001_metadata.sql` legt de kern vast: onder meer de tabellen `users`, `species`, `assemblies`, `projects`, `families`, `samples`, `chromosomes`, `genes` en `gene_info`. (De databankstructuren zelf worden in detail behandeld in [hoofdstuk 3](03-databankstructuren.md).)

**Waar in de code:** `init_postgres_schema`, `_schema_files` en `_split_sql_script` in `backend/app/core/postgres.py`; de SQL-bronbestanden in `backend/db/schema/postgres/`.

### ClickHouse: het variant-schema

De ClickHouse-structuur werkt analoog via `init_clickhouse_schema` in `backend/app/core/clickhouse.py`, die het bestand onder `backend/db/schema/clickhouse/` (momenteel enkel `001_coga_variant_storage.sql`) inleest. Twee verschillen met het Postgres-pad: de splitser hier is eenvoudig (splitsen op `;`, geen dollar-quote-logica nodig), en de databanknaam is niet hard gecodeerd. De helper `_render_sql` vervangt `CREATE DATABASE IF NOT EXISTS coga` en de `coga.`-tabelverwijzingen door de geconfigureerde `CLICKHOUSE_DATABASE`, zodat dezelfde SQL tegen een aangepaste databanknaam kan draaien.

**Waar in de code:** `init_clickhouse_schema` en `_render_sql` in `backend/app/core/clickhouse.py`.

### Twee migratiepaden: in-proces of out-of-band

Er is een bewuste scheiding tussen wie het schema mag aanmaken en wie de app draait, in het kader van de databank-privilegescheiding (intern aangeduid als P1-3/P1-4):

| Instelling | Wie draait de DDL | Wanneer gebruiken |
|---|---|---|
| `POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP=true` (standaard) | De app zelf, bootend als de tabel-**eigenaar** | Huidige single-DSN-uitrol |
| `POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP=false` | Een aparte deploy-stap `python -m backend.app.db_migrate` als eigenaar; de app boot daarna als de beperkte rol `coga_app` | Wanneer de runtime-rol geen DDL mag draaien |

Het bestand `backend/app/db_migrate.py` bevat de eigenaar-bevoorrechte helft: `run_schema_migrations` roept `wait_for_postgres`, `init_postgres_schema` en `init_postgres_admin_user` aan. Beide paden gebruiken **dezelfde** helperfuncties, dus er is één bron van waarheid voor het schema. ClickHouse blijft altijd bij het app-opstartpad, omdat het met eigen admin-credentials verbindt en geen `coga_app`-equivalent kent.

De docstring bovenaan `db_migrate.py` en de commentaren in `main.py` verwijzen voor de gecoördineerde "flip" naar `docs/db-runtime-role-runbook.md`.

**Waar in de code:** `run_schema_migrations` en `main()` in `backend/app/db_migrate.py`; de schakelaar `postgres_run_schema_migrations_on_startup` in `backend/app/core/config.py`; de bewaking bij opstart in de `lifespan`-functie van `backend/app/main.py`.

## Seeding: admin, referentiegenoom en referentiedata

### De eerste admin-gebruiker

De functie `init_postgres_admin_user` (in `backend/app/db_migrate.py`) maakt de eerste beheerder aan. Ze is idempotent: eerst een `SELECT` op de tabel `users` met `settings.admin_username`, en als die al bestaat keert ze meteen terug. Anders voegt ze een rij toe met rol `'admin'`, uit de instellingen `ADMIN_USERNAME`, `ADMIN_PASSWORD` en `ADMIN_EMAIL`. Het wachtwoord wordt nooit in klare tekst opgeslagen: `get_password_hash` (uit `backend/app/dependencies.py`) hasht het met **bcrypt** via `passlib` (`CryptContext(schemes=["bcrypt"])`) vóór de insert.

Deze functie is zo geschreven dat ze ook werkt onder de beperkte rol `coga_app`, want die behoudt bewust `INSERT`-recht op `users` — de admin-seed werkt dus ook wanneer de app niet als eigenaar boot.

**Waar in de code:** `init_postgres_admin_user` in `backend/app/db_migrate.py`; hashing in `get_password_hash` (`backend/app/dependencies.py`); instellingen `admin_username/password/email` in `backend/app/core/config.py`.

### Soort *Homo sapiens* + assembly GRCh38 verzekeren

Zonder een soort en een assembly is er geen coördinatenstelsel voor varianten. `ensure_human_grch38_reference_on_startup` (in `backend/app/services/reference_source_service.py`) garandeert dat *Homo sapiens* (taxon-id 9606, constante `HUMAN_GRCH38_TAX_ID`) met assembly **GRCh38 / hg38** bestaat, inclusief cytobanden en genen. De logica:

1. Bestaat de GRCh38-assembly al mét cytobanden én genen? Dan niets doen (`_find_human_grch38_assembly` + `_assembly_dataset_count`).
2. Anders probeert `import_reference_from_ucsc` de data van **UCSC** te halen: cytobanden (`_download_cytobands`) en genen (`_download_genes`, dat achtereenvolgens tabellen als `ncbiRefSeqCurated`, `ncbiRefSeq`, `refGene` en `ensGene` probeert). De soort en assembly worden aangemaakt via `_get_or_create_species` en `_get_or_create_assembly`.
3. Mislukt de download, dan valt de code terug op `ensure_human_grch38_species_assembly`: een lege "schaal" (soort + assembly zonder data), zodat het platform toch bruikbaar blijft en genen later handmatig geïmporteerd kunnen worden.

Er zit een SSRF-hardening (server-side request forgery: voorkomen dat een aanvaller de server ongewenste URL's laat oproepen) in `_safe_ucsc_genome`: de genoom-identifier wordt tegen een strikte regex (`_UCSC_GENOME_RE.fullmatch`) gevalideerd voordat hij in een download-URL wordt geïnterpoleerd; bij een ongeldige waarde volgt een `HTTPException`. De hele bootstrap kan uitgezet worden met `REFERENCE_BOOTSTRAP_ENABLED=false` (instelling `reference_bootstrap_enabled`).

**Waar in de code:** `ensure_human_grch38_reference_on_startup`, `import_reference_from_ucsc`, `_download_cytobands`, `_download_genes`, `_safe_ucsc_genome` in `backend/app/services/reference_source_service.py`.

### Ingebouwde hg38-tracks en de repeat-catalogus

Twee andere seed-stappen vullen referentietracks die op de assembly hangen:

- `seed_builtin_reference_tracks` (in `backend/app/services/reference_metadata_service.py`) laadt klinische CNV-syndromen en segmentale duplicaties uit meegeleverde bestanden (paden uit `REFERENCE_CLINICAL_CNVS_PATH` en `REFERENCE_SEGMENTAL_DUPLICATIONS_PATH`, standaard onder `/data/ref-data`, met een repo-fallbackpad). Per datasettype wordt eerst gecontroleerd of er al rijen bestaan (`_assembly_dataset_count`), zodat het idempotent is.
- `seed_builtin_repeat_catalog` (in `backend/app/services/repeat_expansion_pg.py`) seedt de repeat-loci-catalogus: eerst een ingebakken lijst (`BUILTIN_REPEAT_LOCI`), daarna optioneel de STRchive-loci uit `TRGT_STRCHIVE_LOCI_PATH`.

**Waar in de code:** `seed_builtin_reference_tracks` in `backend/app/services/reference_metadata_service.py`; `seed_builtin_repeat_catalog` in `backend/app/services/repeat_expansion_pg.py`.

### HPO-ontologie

`ensure_hpo_ontology_on_startup` (in `backend/app/services/hpo_service.py`) importeert de HPO-ontologie (Human Phenotype Ontology, gebruikt voor fenotype-gedreven prioritisatie — zie [hoofdstuk 12](12-hpo-monarch-prioritisatie.md)). Ze slaat over als er al termen in de databank staan, zoekt anders het `hp.obo`-bestand (standaard `/data/ref-data/hpo/hp.obo`) en downloadt het alleen indien nodig en toegestaan (`HPO_DOWNLOAD_IF_MISSING`). Bij het importeren worden `release_version` en `release_date` mee vastgelegd — belangrijk voor traceerbaarheid van welke ontologieversie een analyse gebruikte.

**Waar in de code:** `ensure_hpo_ontology_on_startup` in `backend/app/services/hpo_service.py`; instellingen `hpo_ontology_path/url`, `hpo_download_if_missing`, `hpo_bootstrap_on_startup` in `backend/app/core/config.py`.

### dbNSFP-gebaseerde gene-reference: bootstrap + refresh-worker

De verrijkte gen-informatie (aliassen, Ensembl/NCBI-ids, samenvattingen) wordt niet synchroon bij opstart geladen, maar via een achtergrond-**job** (een taak die los van het verzoek draait). Bij opstart bekijkt `queue_startup_gene_reference_refresh_if_needed` (in `backend/app/services/gene_info_jobs_pg.py`) of een eerste synchronisatie nodig is: alleen wanneer `GENE_REFERENCE_BOOTSTRAP_ON_STARTUP=true`, het lokale dbNSFP-genbestand (`GENE_REFERENCE_DBNSFP_GENE_PATH`, standaard `/data/ref-data/dbNSFP5.3_gene.gz`) aanwezig is, er GRCh38-genen zijn, en de `gene_info`-tabel nog leeg is. Zo ja, wordt een job in de wachtrij (tabel `gene_info_refresh_jobs`) gezet.

Een aparte achtergrondtaak, `gene_reference_refresh_worker`, pikt die job op. Het claimen gebeurt concurrency-veilig met `FOR UPDATE SKIP LOCKED` en met een heartbeat, zodat een vastgelopen job na `GENE_REFERENCE_STALE_HEARTBEAT` (vijf minuten) opnieuw kan worden geclaimd. De worker verrijkt per gensymbool en commit voortgang periodiek (om de ~100 symbolen of ~30 s, constanten `GENE_REFERENCE_PROGRESS_COMMIT_SYMBOLS`/`_SECONDS`), wat de databank ontlast.

**Waar in de code:** `queue_startup_gene_reference_refresh_if_needed`, `gene_reference_refresh_worker`, `claim_next_gene_reference_refresh_job` in `backend/app/services/gene_info_jobs_pg.py`. (De Gene Explorer die deze data toont, staat in [hoofdstuk 13](13-gene-explorer.md).)

## De opstartsequentie in `main.py`, stap voor stap

FastAPI kent een "lifespan": een functie die precies één keer draait bij het opstarten (vóór de `yield`) en één keer bij het afsluiten (na de `yield`). In `backend/app/main.py` orchestreert de `lifespan`-functie de hele bootstrap in deze volgorde:

1. `wait_for_postgres()` — wacht (met herhaalpogingen) tot Postgres bereikbaar is.
2. **Alleen als** `POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP` waar is: `init_postgres_schema()` en `init_postgres_admin_user()`. Draait de app als beperkte rol `coga_app`, dan wordt deze DDL overgeslagen (die is dan al out-of-band gedraaid).
3. `start_audit_log_worker()` en `start_ui_event_worker()` — start de asynchrone schrijvers voor het audit-logboek en de UI-events.
4. Binnen één Postgres-sessie, op volgorde: `seed_builtin_repeat_catalog` → `ensure_human_grch38_reference_on_startup` → `ensure_hpo_ontology_on_startup` → `seed_builtin_reference_tracks` → `queue_startup_gene_reference_refresh_if_needed`. Merk op dat de referentietracks *na* het verzekeren van de assembly komen (ze hangen eraan) en de gene-refresh *na* het laden van de genen (die heeft ze nodig).
5. `wait_for_clickhouse()` en `init_clickhouse_schema()` — pas nu wordt ClickHouse geïnitialiseerd.
6. `start_clickhouse_integrity_monitor()` — start de periodieke `CHECK TABLE`-bewaking die corruptie proactief detecteert.
7. `gene_reference_refresh_worker` en één of meer `family_package_import_worker`-taken (aantal via `FAMILY_IMPORT_WORKER_COUNT`) worden als achtergrondtaken gestart.

Na de `yield` (bij afsluiten) worden al deze workers netjes gestopt en de Postgres- en ClickHouse-verbindingen gesloten.

Twee hardening-details in hetzelfde bestand: `_docs_kwargs` schakelt de interactieve API-docs (`/docs`, `/redoc`, `/openapi.json`) uit buiten ontwikkeling (schema-disclosure-hardening), en de `security_headers_middleware` wordt als laatste geregistreerd zodat de hardening-headers als buitenste laag op elke response terechtkomen.

**Waar in de code:** `lifespan` in `backend/app/main.py`.

## GCP/Terraform: de productie-uitrol op hoofdlijnen

Voor de productie op Google Cloud beschrijft `docs/deployment-gcp.md` een volledige, stapsgewijze handleiding; de `terraform/`-map bevat de infrastructuur-als-code. Terraform bouwt één zelfstandige CoGA-omgeving op in een GCP-project. De `.tf`-bestanden zijn per onderwerp opgesplitst:

| Bestand | Wat het opzet |
|---|---|
| `terraform/main.tf` | De Google-provider en de gedeelde `locals`: de namen van de Secret Manager-secrets, de *verwijzing* naar de CMEK-encryptiesleutel (`var.cmek_key_self_link`) en de e-mailadressen van de serviceaccounts |
| `terraform/network.tf` | Het private netwerk (VPC), serverless VPC-connector, Cloud NAT en Private Google Access — de databanken krijgen geen publiek IP |
| `terraform/database.tf` | Cloud SQL (PostgreSQL, `google_sql_database_instance`/`google_sql_user`) en de ClickHouse-VM (`google_compute_instance`) met CMEK-versleutelde boot- én data-disk en dagelijkse snapshots (`google_compute_resource_policy`) |
| `terraform/cloudrun.tf` | De twee staatloze containers (backend, frontend) op Cloud Run, met `ingress = INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER` zodat de `.run.app`-URL niet direct van buitenaf bereikbaar is |
| `terraform/loadbalancer.tf` | De externe HTTPS-loadbalancer met Google-beheerd TLS-certificaat (`google_compute_managed_ssl_certificate`); `/api` + `/api/*` → backend, de rest → frontend |
| `terraform/armor.tf` | Cloud Armor (edge-WAF, laag-7-DDoS-verdediging, per-IP rate-limiting en een CIDR-allowlist voor toegestane bronnen) |
| `terraform/secrets.tf` | De Secret Manager-*containers* (waarden worden out-of-band toegevoegd) en de IAM-toegang (`secret_manager_secret_iam_member`) van de backend en de ClickHouse-VM daartoe |
| `terraform/tls.tf` | Een lokaal gegenereerde private CA + ClickHouse-server-cert/key voor het backend↔ClickHouse-kanaal (HTTPS op 8443) |
| `terraform/storage.tf` | GCS-buckets voor PHI (ruwe familiedata, read-only voor de app) en referentiedata, beide CMEK-versleuteld |
| `terraform/scripts/` | Opstart-/certrotatie-/shutdown-scripts voor de ClickHouse-VM |

Belangrijk voor de auditor: de **CMEK-sleutel zelf**, de **serviceaccounts** en de **API-activering** worden *niet* door deze config aangemaakt. De serviceaccounts en hun IAM-rollen komen uit een centrale infra-repo (zie `terraform/main-repo-reference/`); deze config *refereert* ze alleen, zodat de CoGA-deploypijplijn geen rechten heeft om zichzelf privileges toe te kennen. De CMEK-sleutel en het inschakelen van de Google-API's zijn eenmalige bootstrap-stappen die in `docs/deployment-gcp.md` (§5.2 en §5.4) met `gcloud` gebeuren.

De secret-namen zijn `coga-secret-key`, `coga-integrity-anchor-key`, `coga-admin-password`, `coga-postgres-password` en `coga-clickhouse-password` (gedefinieerd in `terraform/main.tf`, `locals.secret_ids`). Terraform maakt bewust alleen de *containers*; de echte waarden voeg je apart toe met `gcloud secrets versions add` (zie `docs/deployment-gcp.md` §5.5), zodat geheimen nooit in de Terraform-state belanden.

De datastroom: een clinicus bereikt `https://coga.cmgg.be` → de loadbalancer termineert TLS + Cloud Armor → padrouting naar backend/frontend Cloud Run → de backend praat over het private VPC met Cloud SQL (via de Cloud SQL Connector) en met ClickHouse (HTTPS op poort 8443). De eerste login gebruikt gebruiker `coga-admin` met het `coga-admin-password`-secret.

**Waar in de code / docs:** `terraform/main.tf`, `terraform/secrets.tf`, `terraform/database.tf`, `terraform/network.tf` e.a.; volledige walkthrough in `docs/deployment-gcp.md`; beknopte referentie in `terraform/README.md`.

## Veiligheid & traceerbaarheid bij deployment

De uitrol is ontworpen rond een aantal expliciete waarborgen:

- **Secret-beheer.** Lokaal weigert de backend te starten met placeholder-secrets (`validate_security_defaults`). In GCP komen alle geheimen uit Secret Manager en worden ze pas bij runtime in de container geïnjecteerd, niet in images of Terraform-state ingebakken. `SECRET_KEY` (JWT-ondertekening) en `INTEGRITY_ANCHOR_SIGNING_KEY` (integriteitsankers) moeten verschillende waarden hebben — een gedocumenteerde eis, waarbij de deploygids (§5.5) ze met aparte `openssl rand`-oproepen genereert.
- **TLS naar de datastores.** Postgres via de Cloud SQL Python-Connector (mTLS, "verify-full"-graad over privé-IP; instelling `postgres_use_cloud_sql_connector`, connectorlogica `_cloud_sql_connect` in `backend/app/core/postgres.py`). ClickHouse over HTTPS met een private CA die de backend verifieert (`CLICKHOUSE_SECURE/VERIFY/CA_CERT`, afgehandeld in `_create_clickhouse_client` in `backend/app/core/clickhouse.py`). Encryptie-at-rest via CMEK op Cloud SQL, disks en buckets.
- **Beperkte runtime-DB-rol.** Het schemabestand `backend/db/schema/postgres/040_app_runtime_role_privileges.sql` maakt de rol `coga_app` aan die géén DDL kan draaien en géén `UPDATE`/`DELETE` op de append-only audit-, report-signout- en hash-chain-tabellen mag. Dat sluit een "owner-bypass" op de append-only- en hash-chain-controles. De rol wordt momenteel in "fallback"-modus (`NOLOGIN`) uitgeleverd tot een gecoördineerde DSN-flip; tot dan boot de app nog als eigenaar (zie `docs/db-runtime-role-runbook.md`).
- **Reproduceerbaarheid.** Container-images zijn per digest vastgepind; `APP_VERSION`/`GIT_SHA` worden bij build-time ingebakken en in elk ondertekend rapport bevroren; de HPO-release en het dbNSFP-bestand (`dbNSFP5.3_gene.gz`) zijn gepinde referentieversies. Zo is voor elke analyse achteraf exact te reconstrueren welke code én welke referentiedata gebruikt zijn.
- **Auditing vanaf boot.** De audit-log-worker start vóór de seeding, en de ClickHouse-integriteitsmonitor draait vanaf opstart, zodat gebeurtenissen en datacorruptie van meet af aan worden vastgelegd.

De uitrol-handleiding merkt tot slot op dat een aantal IVDR-verplichtingen procesmatig blijven (change control, een bijgewerkte DPIA nu Google een data-sub-processor is) en dus buiten de code vallen — zie `docs/deployment-gcp.md` §13.

## Belangrijkste bestanden

| Bestand | Rol |
|---|---|
| `backend/app/main.py` | Opstart-orchestratie (`lifespan`): schema, admin-seed, referentie-seeding, workers, hardening |
| `backend/app/db_migrate.py` | Eigenaar-bevoorrechte schemamigratie + admin-bootstrap (`init_postgres_admin_user`, `run_schema_migrations`) |
| `backend/app/core/config.py` | Alle instellingen + `validate_security_defaults` (weigert zwakke secrets) en `validate_cors_origin_regex` |
| `backend/app/core/postgres.py` | `init_postgres_schema`, dollar-quote-bewuste SQL-splitser, Cloud SQL-connector |
| `backend/app/core/clickhouse.py` | `init_clickhouse_schema`, databanknaam-rendering, TLS-clientopbouw |
| `backend/db/schema/postgres/*.sql` | Genummerde Postgres-schemabestanden (001–042), incl. `040_app_runtime_role_privileges.sql` |
| `backend/db/schema/clickhouse/001_coga_variant_storage.sql` | ClickHouse variant-schema |
| `backend/app/services/reference_source_service.py` | GRCh38-bootstrap: soort/assembly, cytobanden, genen (UCSC) |
| `backend/app/services/reference_metadata_service.py` | `seed_builtin_reference_tracks` (klinische CNV's, segmentale duplicaties) |
| `backend/app/services/repeat_expansion_pg.py` | `seed_builtin_repeat_catalog` (repeat-loci + STRchive) |
| `backend/app/services/hpo_service.py` | `ensure_hpo_ontology_on_startup` |
| `backend/app/services/gene_info_jobs_pg.py` | dbNSFP-gene-reference bootstrap + refresh-worker |
| `docker-compose.yml` / `docker-compose.dev.yml` | Lokale prod-stijl- en ontwikkel-stack |
| `.env.example` | Sjabloon voor omgevingsvariabelen (placeholder-secrets) |
| `terraform/` (`main.tf`, `secrets.tf`, `database.tf`, `network.tf`, `cloudrun.tf`, `loadbalancer.tf`, `armor.tf`, `storage.tf`, `tls.tf`, …) | GCP-infrastructuur-als-code |
| `docs/deployment-gcp.md` | Volledige stapsgewijze GCP/Terraform-uitrolgids |
