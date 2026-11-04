# 4. Initiële deployment & seeding

Dit hoofdstuk beschrijft hoe CoGA van een leeg systeem tot een draaiend platform komt, in de Docker-opstelling en op Google Cloud met Terraform: hoe de databankstructuren ontstaan, hoe de eerste beheerder en de referentiedata worden *geseed* (voor het eerst gevuld), en in welke volgorde de backend dat bij het opstarten doet. De stap-voor-stapgidsen staan in het Engels: `docs/development.md` (lokaal) en `docs/deployment-gcp.md` (Google Cloud).

Een paar begrippen. Een **container** is een afgeschermd draaiend softwarepakket; **Docker Compose** start meerdere containers samen vanuit één bestand. **DDL** is SQL die tabellen aanmaakt of wijzigt. **Idempotent** betekent: veilig meermaals uit te voeren zonder extra effect.

## Van nul naar een draaiende stack

CoGA bestaat uit vijf containers: Postgres (metadata en reviewtoestand), ClickHouse (de variantopslag), de FastAPI-backend, de importworker (dezelfde backend-image, die de pakketimports uitvoert) en de React-frontend.

### De Docker-opstelling

`docker-compose.yml` beschrijft de vijf diensten. Enkele keuzes zijn belangrijk voor traceerbaarheid en robuustheid:

- **Vastgezette images.** De databank-images zijn niet alleen met een tag maar met een **digest** (`@sha256:…`) vastgelegd. Zo gebruikt elke machine exact hetzelfde image.
- **Gezondheid en volgorde.** De backend start pas als Postgres en ClickHouse gezond zijn; de frontend en de importworker pas als de backend gezond is. De backend krijgt ruim de tijd, omdat hij bij het opstarten het schema toepast en referentiedata laadt. De importworker migreert zelf niets en controleert alleen de schemarevisie.
- **Rustig afsluiten.** ClickHouse krijgt een ruime afsluittermijn: een schrijfactie die midden in het werk wordt afgebroken, kan dataonderdelen beschadigen.
- **Build-identiteit.** De backend-image krijgt `APP_VERSION` en `GIT_SHA` mee bij het bouwen. Een lokale build is geen release: zonder `APP_VERSION` meldt de image de versie uit `VERSION` met `dev` erbij (`0.1.0-dev`). `.env.example` waarschuwt: zet die niet in `.env`, anders overschrijft de runtime de ingebakken waarde en vervalst hij de versie die in elk ondertekend rapport wordt bevroren. Buiten ontwikkeling start de backend niet zonder `GIT_SHA` (7 tot 40 hexadecimale tekens), omdat elk ondertekend rapport die commit vermeldt. Van `scripts/` bevat de image alleen wat de backend zelf draait: `clinical_cnv_knowledgebase.py` (de heropbouw vanuit Admin) en `import_dgv.py` (een import door de beheerder). De testseeders blijven erbuiten; één ervan maakt een aanmelding met een standaardwachtwoord aan.

**Waar in de code:** `docker-compose.yml`; de waarschuwing in `.env.example`.

### De ontwikkelopstelling

Voor ontwikkeling komt een tweede bestand bovenop de basis:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build -d
```

`docker-compose.dev.yml` zet `APP_ENV=development` en koppelt de broncode in de containers, zodat wijzigingen meteen doorwerken. Het start de backend met automatisch herladen en de frontend met de Vite-ontwikkelserver. `APP_ENV=development` schakelt ook de controle op zwakke geheimen uit, zodat je lokaal met de placeholders van `.env.example` kunt werken. Buiten ontwikkeling weigert de backend dan te starten; de volledige regel staat in [hoofdstuk 2](02-beveiliging-rollen-rechten.md#weigering-te-starten-met-zwakke-geheimen).

**Waar in de code:** `docker-compose.dev.yml`; de controle in `validate_security_defaults` in `backend/app/core/config.py`.

## De databankstructuren aanmaken

### Postgres

Het Postgres-schema staat in vijf SQL-bestanden (`01_access.sql` t/m `05_grants.sql`, hoofdstuk 3). De backend leest ze in naamvolgorde, splitst elk bestand in losse statements en voert alles uit in één transactie. De splitser houdt rekening met *dollar-quotes* (`$$ … $$`), zodat een puntkomma in een triggerfunctie een statement niet halverwege afbreekt. Alle statements zijn idempotent: het schema opnieuw toepassen is veilig. In dezelfde transactie volgen de Alembic-revisies die het migratiegrootboek `alembic_version` nog niet vermeldt. De log meldt van welke revisie de databank kwam en waar ze nu staat (hoofdstuk 3).

**Waar in de code:** `init_postgres_schema` in `backend/app/core/postgres.py`; de migratiestap `apply_postgres_schema` in `backend/app/core/postgres_migrations.py`; de revisies in `backend/db/migrations/versions/`.

### ClickHouse

Het ClickHouse-bestand maakt alleen de databank aan; de databanknaam komt uit de instelling `CLICKHOUSE_DATABASE`. De variant- en track-tabellen per assembly maakt de backend pas aan wanneer ze voor het eerst nodig zijn (hoofdstuk 3).

**Waar in de code:** `init_clickhouse_schema` in `backend/app/core/clickhouse.py`.

### Twee manieren om het schema toe te passen

Wie het schema mag aanmaken, is bewust gescheiden van wie de app draait (de databankrechten uit hoofdstuk 2):

| Instelling | Wie voert de DDL uit | Wanneer |
| --- | --- | --- |
| `POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP=true` (standaard) | De app zelf, bij het opstarten, als eigenaar van de tabellen | De huidige opstelling met één databanklogin |
| `POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP=false` | Een aparte stap (`backend/app/db_migrate.py`) als eigenaar; de app draait daarna als de beperkte rol `coga_app` | Wanneer de runtime geen DDL mag uitvoeren |

Beide paden gebruiken dezelfde functies: er is één bron van waarheid voor het schema. De migratiestap weigert te draaien als `coga_app`, nog voor ze iets wijzigt. Is `POSTGRES_APP_PASSWORD` ingesteld, dan zet de migratiestap ook de login van `coga_app` aan. Het wachtwoord gaat daarbij niet in klare tekst naar de databank: de stap stuurt een SCRAM-SHA-256-afgeleide, zoals `\password` in `psql` doet, en het wachtwoord komt in geen enkele logregel. Op Google Cloud draait deze stap als de Cloud Run-job `coga-db-migrate` (`terraform/migrate.tf`), zodra `db_runtime_role = "coga_app"`.

Een app die zelf niet migreert, leest bij het opstarten alleen het migratiegrootboek. Hij start alleen als de databank precies op zijn eigen schemarevisie staat. Staat ze op een oudere revisie of heeft ze geen grootboek, dan meldt hij dat eerst de migratiestap moet draaien. Staat ze op een revisie die hij niet kent, dan is hij ouder dan de databank (hoofdstuk 3). ClickHouse volgt altijd het opstartpad van de app.

**Waar in de code:** `backend/app/db_migrate.py`; de instellingen in `backend/app/core/config.py`; de procedure in `docs/db-runtime-role-runbook.md`.

## Seeding: beheerder en referentiedata

### De eerste beheerder

`init_postgres_admin_user` maakt de eerste beheerder aan uit `ADMIN_USERNAME`, `ADMIN_PASSWORD` en `ADMIN_EMAIL`, met de rol `admin`. Bestaat die gebruiker al, dan vervangt de functie alleen de opgeslagen hash als die niet meer bij `ADMIN_PASSWORD` past: wie het geheim roteert, roteert zo het wachtwoord (hoofdstuk 5). Zijn rol laat ze staan; alleen met de rol `admin` is hij de noodbeheerder (hoofdstuk 5). Een account met die naam dat aan een Microsoft-account gekoppeld is, blijft ongemoeid. Het wachtwoord wordt nooit in klare tekst bewaard: de backend hasht het met bcrypt, dat hij rechtstreeks aanroept (hoofdstuk 5). De functie werkt ook onder de beperkte rol `coga_app`, die bewust `INSERT`- en `UPDATE`-recht op `users` houdt.

**Waar in de code:** `init_postgres_admin_user` in `backend/app/db_migrate.py`.

### Het referentiegenoom GRCh38

Zonder soort en assembly is er geen coördinatenstelsel voor varianten. Bij het opstarten zorgt de backend dat *Homo sapiens* met assembly **GRCh38** bestaat, met cytobanden en genen. Ontbreekt een van beide, dan haalt hij ze op: cytobanden bij UCSC, genen uit de GENCODE-annotatie (een GTF-bestand, via `REFERENCE_GENCODE_GTF_URL`). Lukt GENCODE niet, dan valt hij terug op een UCSC-gentabel en legt hij dat vast. Mislukt de hele download, dan maakt hij alleen de soort en de assembly aan: het platform blijft bruikbaar, en de genen kunnen later worden geïmporteerd. Een strikte controle op de genoomnaam voorkomt dat die in een download-URL kan worden misbruikt (*server-side request forgery*). `REFERENCE_BOOTSTRAP_ENABLED=false` zet dit alles uit.

**T2T-CHM13v2.0** komt er optioneel bij als tweede assembly (`REFERENCE_BOOTSTRAP_T2T=true`; standaard uit). Die annotatie is armer: UCSC's RefSeq-afgeleide GTF levert coördinaten, maar geen biotypes, Ensembl-id's of MANE-labels, en er is geen cytobandtabel. Mislukt die import, dan is dat nooit fataal.

De import noemt de assembly zoals UCSC's beschrijving dat doet: `T2T CHM13v2.0`, met een spatie (`hs1` als die beschrijving geen naam geeft). Haar ClickHouse-tabellen heten `T2T_CHM13v2.0`; NCBI schrijft `T2T-CHM13v2.0`. CoGA herkent T2T-CHM13v2.0 onder elk van die namen, met één regel in de backend en één in de frontend, in hoofd- of kleine letters: `T2T-CHM13`, `T2T CHM13` of `T2T_CHM13`, met of zonder `v2.0`, `CHM13v2.0` en `hs1` (`is_t2t_chm13` in `backend/app/services/sex_chromosomes.py`, naast `grc_build`; `isT2tChm13` in `frontend/src/lib/reference.ts`). Een andere CHM13-release (v1.0, v1.1) of een ander telomeer-tot-telomeergenoom telt niet: hs1 is v2.0. Die regel kiest de UCSC-link (`hs1`) en het IGV-genoom, zet T2T na GRCh38 bij de loci van een gen en de regio's van een panel, en geeft de STRchive-coördinaten waarop een repeatcall wordt geplaatst. Vroeger herkenden de meeste plaatsen alleen `T2T-CHM13…` met een koppelteken, dus niet de naam die de import zelf geeft. T2T valt buiten de gevalideerde scope (alleen GRCh38).

GRCh38 en GRCh37 herkent CoGA ook met één regel, `grc_build` (`grcBuild` in de frontend): GRCh38 of hg38, GRCh37 of hg19, in hoofd- of kleine letters, met of zonder patch-achtervoegsel zoals `.p14`. De UCSC-import noemt een assembly `hg38` als UCSC's beschrijving geen naam geeft. Die regel kiest de links van de genpagina naar UCSC en gnomAD (`hg38` en gnomAD v4 voor GRCh38, `hg19` en gnomAD v2.1 voor GRCh37). De UCSC- en gnomAD-links van de variantenlijst en de hg38-locus van de genpagina lezen de assembly bij haar naam, en anders bij haar versie, telkens als geheel. Een naam die de regels niet kennen, zoals `GRCh38_no_alt`, krijgt geen UCSC-link en geen gnomAD-release, net zoals IGV er geen genoom voor opent.

De loci van een gen, de regio's van een panel en de groottes in de panellijst staan in één volgorde: GRCh38, T2T-CHM13, GRCh37, en dan de andere assemblies op naam (`assembly_display_rank`; `compareAssemblyNames` in de frontend). De genpagina opent op de eerste die het gen heeft. Vroeger lazen de links van de genpagina en die volgorde alleen de exacte naam GRCh38 (GRCh37 of hg19 voor GRCh37): een GRCh38-assembly met de naam `hg38` of `GRCh38.p14` kreeg op de genpagina geen link naar UCSC of gnomAD en kwam achteraan. De variantenlijst nam toen elke naam of versie met `hg38` voor GRCh38, en de panellijst zette GRCh37 tussen de andere assemblies.

**Waar in de code:** `backend/app/services/reference_source_service.py` (`ensure_human_grch38_reference_on_startup`, `ensure_human_t2t_reference_on_startup`); de naamregels en de volgorde in `backend/app/services/sex_chromosomes.py` (`grc_build`, `is_t2t_chm13`, `assembly_display_rank`) en `frontend/src/lib/reference.ts`.

### Ingebouwde tracks en de repeatcatalogus

- **Segmentale duplicaties** worden geladen uit een bestand (`REFERENCE_SEGMENTAL_DUPLICATIONS_PATH`), alleen als die dataset voor de assembly nog leeg is. Het bestand moet byte voor byte het meegeleverde ClinGen-bestand zijn (release V2.1 voor GRCh38; `SEGMENTAL_DUPLICATIONS_PINNED_SHA256`, TRACE-14). Een ander bestand laadt de start niet: ze logt één fout met het bestand, beide SHA-256-waarden en de oplossing, en gaat verder zonder die track. Het bestand bevat GRCh38-coördinaten, dus de start laadt het alleen in een GRCh38-assembly (OPS-17). Dat is de assembly die `REFERENCE_BOOTSTRAP_ASSEMBLY_NAME` noemt (standaard GRCh38), als haar naam GRCh38 of hg38 is, in hoofd- of kleine letters en met of zonder patch-achtervoegsel zoals `.p14` (dezelfde regel als voor de pseudo-autosomale regio's van GRCh38 en GRCh37; die van T2T-CHM13v2.0 herkent CoGA onder elk van zijn namen). Noemt de instelling een andere assembly, zoals T2T-CHM13 of GRCh37, dan laadt de start niets en logt ze één waarschuwing met die assembly en de oplossing: een beheerder laadt het eigen bestand van die assembly op via de pagina Referentiedata (hoofdstuk 15). Vroeger laadde de start het GRCh38-bestand in elke assembly die de instelling noemde.
- **Klinische CNV's** laadt de start niet (OPS-17). CoGA start zonder, en meldt dat niet apart; een beheerder bouwt ze op met de kennisbank op de pagina Referentiedata (hoofdstuk 15). Vroeger las de start ze uit elk bestand op `REFERENCE_CLINICAL_CNVS_PATH`, zonder pin, en het standaardbestand werd nooit meegeleverd. Die instelling is weg en wordt genegeerd als ze nog gezet is.
- **De repeatcatalogus** combineert een ingebouwde lijst loci met de STRchive-loci (`data/ref-data/STRchive-loci.json`). De ingebouwde loci dragen de drempels van hun STRchive-rij, zodat de status van een call niet afhangt van welke rij zijn TRID vindt. Elke start schrijft beide opnieuw weg (bijgewerkt per `locus_id`), dus een gewijzigde drempel geldt vanaf de volgende start. Al geïmporteerde calls behouden op de genoomtracks hun opgeslagen status; de repeatpagina beoordeelt ze meteen opnieuw (hoofdstuk 8). Het register van QA over de telwijze van TRGT per locus (`repeat_locus_count_units`, hoofdstuk 8) schrijft de seed nooit: een start behoudt het.
- **De STRchive-release is vastgezet** (CLIN-31, beslissing van de eigenaar, 2026-10-14). De bron is de meegeleverde, gevalideerde release: het bestand in de repository, met SHA-256 `a0295e1fe3e2…` (75 loci). Het is STRchive-release v2.18.0 (28 april 2026), byte voor byte gelijk aan het `STRchive-loci-v2.18.0.json` dat STRchive bij die release publiceerde. Dat is op 20 oktober 2026 vergeleken met het locibestand van elke gepubliceerde release (`STRCHIVE_PINNED_RELEASE`). Het bestand noemt zelf geen release, dus de SHA-256 is zijn identiteit. `STRCHIVE_PINNED_LOCI_SHA256` legt die volledig vast; geen instelling kan ze vervangen. De start leest het bestand van `TRGT_STRCHIVE_LOCI_PATH` (op Cloud Run de referentiedata-bucket), of de kopie in de repository als dat bestand er niet is. Is het gelezen bestand niet byte voor byte de meegeleverde release, of is er geen bestand, dan start de backend niet. Hij schrijft dan niets en logt één fout met het bestand, beide SHA-256-waarden en de oplossing (`data/ref-data/STRchive-loci.json` ongewijzigd kopiëren). Een identieke kopie wordt aanvaard, waar ze ook staat. Elke start verwijdert ook de STRchive-rijen van `repeat_loci` die de release niet kent, zoals de loci van een andere release die een eerdere start schreef. Naar een andere release gaan is een gereviewde wijziging: het bestand, de pin en de drempels van de ingebouwde loci veranderen samen (`docs/data-import.md`, sectie *The STRchive release*).

**Waar in de code:** `seed_builtin_reference_tracks` in `backend/app/services/reference_metadata_service.py`, met `grc_build` in `backend/app/services/sex_chromosomes.py` (welke assembly GRCh38 is); `seed_builtin_repeat_catalog` en `read_shipped_strchive_release` in `backend/app/services/repeat_expansion_pg.py`; de pin (`STRCHIVE_PINNED_LOCI_SHA256`) in `backend/app/core/config.py`.

### De HPO-ontologie

Staan er nog geen HPO-termen in de databank, dan importeert de backend de ontologie uit het bestand dat in de repository is vastgezet (`data/ref-data/hpo/hp.obo`). Hij importeert een bestand alleen als zijn SHA-256 die van het vastgezette bestand is (TRACE-14):

- **Een bestand dat al klaarstaat** (`HPO_ONTOLOGY_PATH`, op Cloud Run de bucket) met een andere SHA-256 weigert hij met één fout in de log (het bestand, beide SHA-256-waarden en de oplossing). Hij importeert dan niets en downloadt ook niets in de plaats. De start gaat door; de HPO-terminologie blijft leeg tot het juiste bestand er staat.
- **Alleen als er geen bestand is,** downloadt hij dezelfde release van een vaste URL die niet meeschuift met nieuwe releases (`HPO_DOWNLOAD_IF_MISSING`). Een ander bestand, bv. een latere release, weigert hij voordat er iets wordt weggeschreven: hij logt een fout en importeert niets.

De URL en de SHA-256 liggen vast in de code; geen instelling verandert ze. Een andere release komt er alleen via een nagekeken codewijziging. De vroegere instellingen `HPO_ONTOLOGY_URL` en `HPO_ONTOLOGY_SHA256` bestaan niet meer; staan ze nog in een omgeving, dan negeert de backend ze. De release en de datum van de ontologie worden mee opgeslagen, zodat later te zien is welke versie een analyse gebruikte (hoofdstuk 12).

**Waar in de code:** `ensure_hpo_ontology_on_startup` in `backend/app/services/hpo_service.py`; de vastgezette release en SHA-256 (`HPO_PINNED_RELEASE`, `HPO_PINNED_ONTOLOGY_SHA256`) in `backend/app/core/config.py`.

### De gen-referentie (dbNSFP)

De verrijkte geninformatie (aliassen, id's, constraint, ziekteassociaties) laadt een achtergrondjob, niet de start. Bij het opstarten zet de backend alleen een eerste job in de wachtrij, als dat aanstaat (`GENE_REFERENCE_BOOTSTRAP_ON_STARTUP`), het lokale dbNSFP-genbestand aanwezig is, er GRCh38-genen zijn en de cache `gene_info` nog leeg is. Een aparte worker voert de job uit; hoofdstuk 13 beschrijft hoe.

**Waar in de code:** `backend/app/services/gene_info_jobs_pg.py`.

## De opstartvolgorde

FastAPI kent een *lifespan*: een functie die één keer draait bij het opstarten en één keer bij het afsluiten. In `backend/app/main.py` doet die, in deze volgorde:

1. Wachten tot Postgres bereikbaar is.
2. **Alleen als** `POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP` aanstaat: het Postgres-schema migreren (de baselines, dan de revisies die het grootboek mist) en de eerste beheerder aanmaken. Staat ze uit (de beperkte rol `coga_app`), dan controleert de backend alleen dat het grootboek precies zijn eigen schemarevisie noemt. Anders start hij niet; de melding staat ook in de log.
3. De achtergrondschrijvers voor de auditlog en de UI-events starten, zodat alles daarna al gelogd wordt.
4. In één Postgres-sessie: de repeatcatalogus seeden, GRCh38 verzekeren, optioneel T2T-CHM13 importeren, de HPO-ontologie laden, de ingebouwde tracks seeden en zo nodig de eerste gen-referentiejob in de wachtrij zetten.
5. Wachten tot ClickHouse bereikbaar is en het ClickHouse-schema toepassen. Heeft een varianttabel een sorteersleutel zonder de callset, of mist `SNV_INDEL/entries` de kolommen per call `calls.filters` en `calls.metrics`, dan start de backend niet (hoofdstuk 3).
6. De back-uptabellen verwijderen van `overwrite`-imports die geen lopende import meer bezit, achtergelaten door een import waarvan het proces stopte (hoofdstuk 6). Een fout hierbij houdt de start niet tegen.
7. De integriteitsbewaking van ClickHouse starten. Die controleert de varianttabellen kort na het opstarten en daarna op een vast interval, en logt een fout bij beschadiging (hoofdstuk 11).
8. De worker voor de gen-referentie en de workers voor de pakketimport starten (aantal via `FAMILY_IMPORT_WORKER_COUNT`). Met `0` start de backend geen importworkers en laat hij de jobs aan importworkerprocessen over, zoals onder Docker Compose (hieronder). Alleen met importworkers start hij ook de worker van de verkennende ancestry-PCA.

Bij het afsluiten stopt de backend al deze workers netjes en sluit hij de databankverbindingen.

**Waar in de code:** de functie `lifespan` in `backend/app/main.py`; in het Engels beschreven in `docs/application-scheme.md`, sectie *Startup and background work*.

### De importworker in een eigen proces

Een Python-proces gebruikt één processorkern voor al zijn werk, hoeveel workers het ook draait. Draaiden alle importworkers in de backend, dan deelden alle imports van een grote reeks families die ene kern, met de event loop die de verzoeken van de gebruikers beantwoordt. Daarom kan `python -m app.import_worker` de importworkers in een eigen proces draaien, zonder API: het beantwoordt geen verzoeken en luistert op geen poort. Docker Compose draait het als de dienst `import-worker`, uit dezelfde image en met dezelfde instellingen en dezelfde map `/data` als de backend. De backend zelf krijgt daar geen importworker (`FAMILY_IMPORT_WORKER_COUNT=0`). Elke replica is één proces, dat `FAMILY_IMPORT_WORKER_COUNT` jobs tegelijk draait (standaard 1). `IMPORT_WORKER_REPLICAS` (standaard 1) bepaalt hoeveel replica's er draaien, en zo hoeveel families tegelijk worden geïmporteerd, elk op een eigen kern. Op Google Cloud draaien de imports nog in de backend: `terraform/` heeft geen aparte workerdienst.

Bij het opstarten controleert het proces wat de backend controleert voor zijn workers starten, en niets anders:

- het wacht op Postgres en start niet als de databank een andere schemarevisie heeft (het migreert nooit);
- het wacht op ClickHouse, maakt de databank aan als die ontbreekt en weigert varianttabellen met een oudere rij-identiteit;
- het verwijdert de back-uptabellen van `overwrite`-imports waarvan geen lopende import eigenaar is;
- dan start het zijn workers, ook die van de verkennende ancestry-PCA.

Op SIGTERM (`docker compose stop`) of SIGINT stopt het zijn workers zoals de backend dat bij het afsluiten doet, sluit het de databankverbindingen en eindigt het met code 0. Een import die liep, stopt waar hij is; de volgende worker neemt de job over zodra zijn heartbeat tien minuten oud is (hoofdstuk 6). Een start die mislukt, of een worker die vanzelf stopt, eindigt met code 1, en Compose start het proces opnieuw.

Wat de workers van alle processen delen, loopt via Postgres en geldt dus over processen en hosts heen: het opnemen van een job (`FOR UPDATE SKIP LOCKED`), zijn heartbeat (die de klok van de databank zet en beoordeelt), het beëindigen van een job met een verouderde heartbeat, en de schrijfsloten op de varianten van een familie (advisory locks op de eigen verbinding van de import). Een worker logt elke job die hij opneemt, met zijn worker-id, dat met het proces-id begint: het record van een job noemt zijn worker alleen zolang de job loopt. In de ontwikkelopstelling herlaadt de importworker niet bij een codewijziging, zodat een bewerking geen lopende import stopt. `docker compose restart import-worker` neemt een wijziging over.

**Waar in de code:** `backend/app/import_worker.py`; de dienst `import-worker` in `docker-compose.yml` en `docker-compose.dev.yml`; in het Engels beschreven in `docs/application-scheme.md`, sectie *Import-worker processes*.

## Google Cloud met Terraform

De map `terraform/` bouwt één CoGA-omgeving op in een Google Cloud-project. Op hoofdlijnen:

- **Netwerk en opslag.** Een privé-netwerk zonder publieke IP-adressen voor de databanken. Cloud SQL (Postgres) en een ClickHouse-VM met versleutelde schijven en dagelijkse snapshots. Opslagbuckets voor de familiedata en de referentiedata, beide alleen-lezen voor de app. De browser mag de familiedata alleen vanaf het domein van de app lezen (voor IGV, via kortlevende ondertekende URL's). Alles is versleuteld met een door de klant beheerde sleutel (CMEK). Een optionele blokkade van uitgaand verkeer voor de ClickHouse-VM (`clickhouse_restrict_egress`, standaard uit) staat in `terraform/egress.tf`. De ClickHouse-VM krijgt dezelfde geheugengrenzen per query als de Docker-opstelling, uit hetzelfde bestand (`clickhouse/users.d/memory-guards.xml`, OPS-14): hoogstens 4 GB per query, en GROUP BY en ORDER BY schrijven vanaf 2 GB naar schijf.
- **Toepassing.** Backend en frontend draaien op Cloud Run en zijn alleen bereikbaar via de externe load balancer. Die handelt TLS af en stuurt `/api` naar de backend. Cloud Armor (standaard aan) voegt DDoS-bescherming, rate limiting per IP, een optionele lijst van toegelaten IP-bereiken en een webfirewall (OWASP CRS) toe. Die webfirewall logt standaard alleen en blokkeert pas met `cloud_armor_waf_enforce = true`. De job `coga-db-migrate` (`terraform/migrate.tf`) past het schema toe wanneer de app als `coga_app` draait. Vóór elke schemamigratie die een nieuwe backend-image meebrengt (die job, of in eigenaarsmodus de eerste start van de nieuwe revisie), neemt `terraform apply` met gcloud een on-demand back-up van de Cloud SQL-databank en wacht tot die klaar is. Dat is het herstelpunt als de migratie terug moet. Mislukt de back-up, dan stopt de uitrol vóór de migratie en blijft de draaiende versie werken. Het deploy-account heeft daarvoor een eigen rol uit de centrale infra-repo nodig (`cogaPreMigrationBackup`).
- **Wat Terraform niet doet.** De CMEK-sleutel, de serviceaccounts en het inschakelen van de Google-API's maakt deze configuratie niet zelf aan; ze verwijst er alleen naar. Bij CMGG levert de centrale infra-repo ze (sjabloon: `terraform/main-repo-reference/coga-prerequisites.tf.example`). Zo kan de CoGA-uitrol zichzelf geen extra rechten geven. Voor een losstaand project beschrijft `docs/deployment-gcp.md` die stappen (§5.2 en §5.4).
- **Geheimen.** Terraform maakt alleen de *containers* in Secret Manager aan (`coga-secret-key`, `coga-integrity-anchor-key`, `coga-admin-password`, `coga-postgres-password`, `coga-clickhouse-password`, `coga-postgres-app-password`, en voor de monitoring `coga-metrics-token`). De waarden voeg je apart toe (`docs/deployment-gcp.md` §5.5), zodat ze nooit in Terraform-variabelen staan. Geen ervan belandt in de Terraform-state of het plan. Terraform leest `coga-postgres-password` wel bij elke plan en apply, om het wachtwoord van de eigenaar in Cloud SQL in te stellen. Het geeft het door via een write-only argument: de state bewaart alleen het versienummer van het geheim, zodat een nieuwe versie het wachtwoord bij de volgende apply verandert. Houd de state-bucket toch privé: hij beschrijft de hele omgeving. De waarden moeten de regel uit hoofdstuk 2 halen: `SECRET_KEY` telt minstens 32 tekens, en de ankersleutel is de base64 van precies 32 willekeurige bytes (`openssl rand -base64 32 | tr -d '\n'`) en verschilt van `SECRET_KEY`.
- **TLS van ClickHouse.** Die maakt Terraform zelf aan: een eigen certificaatautoriteit en het servercertificaat, in `coga-clickhouse-tls-key` en `coga-clickhouse-tls-cert`. De privésleutels maakt elke run opnieuw, en ze worden nergens bewaard. Omdat de sleutel van de certificaatautoriteit na die run weg is, geeft Terraform de certificaatautoriteit en het servercertificaat altijd samen opnieuw uit; de ClickHouse-VM moet daarna herstarten (`docs/deployment-gcp.md` §12.4). Een nieuwe versie van die twee geheimen maakt Terraform aan vóór hij de oude vernietigt, zodat een VM die tijdens de apply opstart altijd een sleutel en een certificaat vindt (SEC-19). Dat vraagt Terraform 1.11 of later en de google-provider 8.x.
- **Monitoring en alarmen.** `terraform/monitoring.tf` zet naast de backend een collector die elke minuut `/metrics` leest, met een token uit `coga-metrics-token`, en de cijfers naar Google Cloud Managed Service for Prometheus stuurt. Alarmregels melden per e-mail aan `alert_notification_emails` onder meer een beschadigde variantendatabank, verloren auditregels, een vastgelopen import en een onbereikbare app. Een uptime-check haalt elke minuut `/api/health` op via de load balancer. Het deploy-account heeft daarvoor de Monitoring-rollen uit `terraform/main-repo-reference/rollout-checklist.md` nodig. Wat elke metric en elk alarm betekent, staat in `docs/monitoring.md`.
- **De weg van een verzoek.** Een gebruiker opent `https://coga.cmgg.be` → de load balancer (TLS en Cloud Armor) → Cloud Run → de backend. Die bereikt Cloud SQL via de Cloud SQL-connector (versleuteld, over het privé-netwerk) en ClickHouse over HTTPS met een eigen certificaatautoriteit. De eerste login gebruikt het e-mailadres van de eerste beheerder (`ADMIN_EMAIL`, door Terraform gezet op `admin@<app_domain>`; aanmelden gaat altijd met een e-mailadres) en het wachtwoord uit `coga-admin-password`. Met Microsoft-aanmelding kan dat alleen op `/login/emergency`, zolang `AZURE_ADMIN_OVERRIDE` aan staat (hoofdstuk 5).

Enkele IVDR-verplichtingen blijven procesmatig en vallen buiten de code, zoals change control en een bijgewerkte DPIA nu Google de gegevens host. Of Google verwerker of subverwerker is, beslist de eigenaar (`docs/deployment-gcp.md` §13).

**Waar in de code:** de `.tf`-bestanden in `terraform/`; de beknopte referentie in `terraform/README.md` en de volledige gids in `docs/deployment-gcp.md`.

## Veiligheid en traceerbaarheid bij de uitrol

- **Geheimen.** Buiten ontwikkeling start de backend niet met placeholder- of zwakke geheimen (hoofdstuk 2). Op Google Cloud komen ze uit Secret Manager en worden ze pas bij het starten in de container gezet, niet in images. De Terraform-state bevat er alleen een verwijzing naar (hierboven).
- **Versleuteling onderweg.** Postgres via de Cloud SQL-connector; ClickHouse over HTTPS, met een certificaat dat de backend controleert (`CLICKHOUSE_SECURE`, `CLICKHOUSE_VERIFY`, `CLICKHOUSE_CA_CERT`). In rust versleutelt CMEK Cloud SQL, de schijven en de buckets.
- **Beperkte databankrol.** `coga_app` heeft geen DDL-rechten en geen `UPDATE`/`DELETE` op de append-only tabellen. Standaard draait de app nog als eigenaar, tot de omschakeling uit hoofdstuk 2.
- **Reproduceerbaarheid.** De basis-, databank- en collector-images zijn op digest vastgezet. De eigen images van CoGA krijgen een tag met de release of de commit, en worden uitgerold op de digest die hun build pushte: de draaiende revisie is precies de gebouwde image. Een release wordt alleen gebouwd van een commit op `main`, en haar SBOM's worden met hun SHA-256 aan de release gehecht. `APP_VERSION` en `GIT_SHA` worden bij het bouwen ingebakken en in elk ondertekend rapport bevroren. De HPO-release, het dbNSFP-bestand, de ACMG SF-lijst en de GENCODE-uitgave zijn vastgezette referentieversies, en elk gecachet genrecord noteert per bron welke uitgave het leverde (hoofdstuk 13).
- **Audit vanaf de start.** De auditschrijver start vóór het seeden, en de ClickHouse-bewaking draait vanaf het opstarten.

## Belangrijkste bestanden

| Bestand | Rol |
| --- | --- |
| `backend/app/main.py` | De opstartvolgorde (`lifespan`) |
| `backend/app/db_migrate.py` | Schema-migratie als eigenaar, eerste beheerder, login van `coga_app` |
| `backend/app/core/config.py` | Alle instellingen en de controle op zwakke geheimen |
| `backend/app/core/postgres.py` · `backend/app/core/clickhouse.py` | Het schema toepassen; verbindingen (ook de Cloud SQL-connector en TLS naar ClickHouse) |
| `backend/app/core/postgres_migrations.py` · `backend/db/migrations/` | De Postgres-migratiestap en de Alembic-revisies |
| `backend/app/services/reference_source_service.py` | GRCh38 (en optioneel T2T): soort, assembly, cytobanden, genen |
| `backend/app/services/reference_metadata_service.py` | Ingebouwde tracks bij de start (segmentale duplicaties; klinische CNV's niet, OPS-17) |
| `backend/app/services/hpo_service.py` · `gene_info_jobs_pg.py` | HPO-ontologie; de gen-referentiejob |
| `docker-compose.yml` · `docker-compose.dev.yml` · `.env.example` | De Docker-opstelling en alle instellingen met hun standaardwaarde |
| `terraform/` · `docs/deployment-gcp.md` | Google Cloud-infrastructuur en de uitrolgids |
