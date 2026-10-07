# 7. Backend: routers & services in detail

Dit hoofdstuk beschrijft hoe de API-laag van CoGA is opgebouwd: het vaste patroon **router → service → opslag**, hoe FastAPI de databanksessie en de ingelogde gebruiker aan elk endpoint geeft, en vooral welke **veiligheidsregels overal gelden**: elke databankvraag is geparametriseerd, elke `ORDER BY` komt uit een vaste lijst, en elke `LIMIT`/`OFFSET` wordt een geheel getal. Het hoofdstuk bevat ook het overzicht van alle routers en de request-logging, die elk verzoek in de auditlog vastlegt.

Enkele begrippen:

- **Router:** een groep HTTP-endpoints (bv. `GET /api/families/...`), in FastAPI een `APIRouter`.
- **Service:** een Python-module met de eigenlijke logica (databankvragen, klinische berekeningen). Routers bevatten die logica bewust niet.
- **Dependency injection:** in de parameterlijst van een endpoint zeg je "ik heb X nodig" (bv. een databanksessie), en FastAPI maakt X aan en geeft het mee.
- **Pydantic-model (schema):** een Python-klasse die de vorm van inkomende en uitgaande JSON beschrijft en controleert.
- **Geparametriseerde query:** een databankvraag waarin waarden apart worden meegegeven in plaats van in de tekst geplakt; de standaardverdediging tegen SQL-injectie.

## Dunne routers, dikke services

CoGA houdt drie lagen strikt gescheiden:

1. **Router (dun):** leest het pad en de parameters, laat ze valideren, dwingt de toegang af en geeft het antwoord als JSON terug.
2. **Service (dik):** de klinische en bedrijfslogica, zoals filters, prioritering, ACMG-regels en hash-ketens. Alleen services praten met de opslag.
3. **Opslag:** Postgres via SQLAlchemy (asynchroon) en ClickHouse via een directe client.

Een voorbeeld: `GET /api/families/{family_id}/small-variants`.

- **De router** (`backend/app/routers/families_small_variants.py`) krijgt de paginagrootte als een geheel getal met een ondergrens en een bovengrens; een te grote waarde wordt met `422` geweigerd voor er iets draait. De vele filterparameters leest een aparte dependency in, zodat het gewone endpoint en de CSV-export precies dezelfde filters gebruiken.
- **Het toegangscheckpoint** `build_family_metadata_context` laadt de familie alleen als de gebruiker ze mag zien, en beperkt de projecten tot die van de gebruiker (hoofdstuk 2). Dat gebeurt vóór er een variantvraag naar ClickHouse gaat.
- **De service** (`backend/app/services/clickhouse_family_variants.py`) bouwt de ClickHouse-query, met de scoping erin (`e.family_guid = %(family_guid)s`, `e.project_guid IN %(project_ids)s`) en de waarden als parameters.
- **Het antwoord** wordt tegen een Pydantic-model gecontroleerd en als JSON teruggegeven.

De router raakt dus nooit rechtstreeks SQL aan, en de service bemoeit zich niet met HTTP of tokens. Zo is voor een reviewer duidelijk waar de toegangscontrole, de validatie en de query-opbouw zitten.

## Dependencies: sessie, gebruiker, scoping

Twee dependencies komen in vrijwel elk beschermd endpoint terug. `get_postgres_session` geeft per verzoek een verse databanksessie en sluit ze achteraf. `get_current_user` controleert het token en laadt de gebruiker (hoofdstuk 5); `get_current_admin_user` eist daarbovenop een beheerdersrol. Een router kan een dependency ook op al zijn endpoints tegelijk leggen: de routers voor DGV, CNV's, chromosomen, blacklist en segmentale duplicaties vragen zo voor elk endpoint, ook een toekomstig, een geldig token.

**Waar in de code:** `backend/app/dependencies.py` en `backend/app/core/postgres.py`.

## Schemas: validatie en documentatie

Alle vormen van verzoeken en antwoorden staan in `backend/app/schemas/`, één module per domein. Ze doen drie dingen: ze **controleren invoer** (ongeldige JSON geeft `422` voordat de router draait), ze **leggen de vorm van het antwoord vast** (`response_model=`, zodat onbedoelde velden wegvallen) en ze leveren de **OpenAPI-beschrijving** van de API. Buiten ontwikkeling zijn `/docs`, `/redoc` en `/openapi.json` uitgeschakeld. Omdat alle schemas op één plek staan, kan een reviewer daar nagaan welke gegevens het systeem in- en uitgaan.

## Veiligheidsregels die overal gelden

Deze regels zijn niet per functie opnieuw bedacht, maar zitten in enkele gedeelde hulpmiddelen.

### 1. Alle queries zijn geparametriseerd

Waarden gaan nooit als tekst in een query, altijd als losse parameter.

- **Postgres:** SQLAlchemy met benoemde parameters (`:naam`). Voor een lijst UUID's (bv. `IN :project_ids`) bestaat een hulpfunctie die een veilige, variabele lijst oplevert zonder tekst te plakken.
- **ClickHouse:** parameters in de vorm `%(naam)s`, die de client bij de server bindt. De querytekst bevat de waarde nooit letterlijk.

Het enige dat wél in de tekst staat, zijn tabelnamen; die worden afgeleid van de assemblynaam via één functie die alleen veilige tekens toelaat (hoofdstuk 3).

**Waar in de code:** `backend/app/core/sql.py` (`uuid_list_bindparam`) en `execute_clickhouse` in `backend/app/core/clickhouse.py`.

### 2. `ORDER BY` komt uit een vaste lijst

Een kolomnaam kan in SQL geen parameter zijn. Daarom zet CoGA nooit door de gebruiker aangeleverde tekst in een `ORDER BY`, maar vertaalt het een sorteersleutel via een vaste tabel. In de Variant Explorer valt een onbekende sleutel terug op de standaard (`total_samples`); in de integriteitscontrole van de hash-ketens komen de sorteerkolommen uit een vaste lijst per tabel.

**Waar in de code:** `_SORT_EXPR` in `backend/app/services/variant_explorer_service.py` en `_CHAIN_ORDER_COLS` in `backend/app/services/integrity_anchor_service.py`.

### 3. `LIMIT` en `OFFSET` worden gehele getallen

Paginagrenzen gaan altijd door `int(...)`, worden minstens 0 en staan zelf als parameter in de query. Een hoog paginanummer kan bovendien geen onbegrensde scan uitlokken: de pagina wordt geklemd.

**Waar in de code:** `backend/app/services/clickhouse_variant_queries.py`.

### 4. Extra bescherming rond ClickHouse

- **Grenzen per query:** een maximale uitvoeringstijd, querygrootte en geheugengrenzen, zodat één brede filter de server niet onbeperkt bezet. Een query die te zwaar is, geeft een `422` met de vraag de zoekopdracht te verfijnen, geen onduidelijke `500`.
- **Tijdelijke fouten:** bij een verbroken verbinding herstelt de client zich en probeert hij één keer opnieuw, zonder de query te wijzigen.

**Waar in de code:** `backend/app/core/clickhouse.py` (grenzen en herstel); de vertaling van een te zware query naar `422` in `backend/app/services/clickhouse_family_variants.py`.

## Alle routers

Alle routers staan in `backend/app/routers/__init__.py` en hangen onder `/api`. De router `families.py` bindt vijf deelrouters in onder hetzelfde pad `/families`; die hebben daarom geen eigen voorvoegsel. Alleen `health.py` en `lookups.py` hebben helemaal geen voorvoegsel.

| Router | Pad onder `/api` | Doel |
| --- | --- | --- |
| `health.py` | `/health`, `/health/ready`, `/version` | Beschikbaarheid en versie; zonder login |
| `auth.py` | `/auth` | Login, token, registratie, profiel, gebruikersbeheer (hoofdstuk 5) |
| `ped.py` | `/ped` | Stamboom uploaden of met de hand invoeren |
| `families.py` | `/families` | Familie, leden, structuur, HPO, fenotype-matching, regio van interesse |
| `families_small_variants.py` | `/families` (deel) | Small variants: pagina's, export, compound-het, presets, tags, review en ACMG (hoofdstuk 8) |
| `families_structural_variants.py` | `/families` (deel) | Structurele varianten van een familie |
| `families_nipt.py` | `/families` (deel) | Monogene NIPT: samenvatting met de kwaliteitscontroles, varianten met de overervingsweergaven, dekking (ook per capture-target) (hoofdstuk 8) |
| `families_reports.py` | `/families` (deel) | Annotatiemanifest, drift, klinische audit, sample-QC, rapport en ondertekening (hoofdstuk 11) |
| `families_tracks.py` | `/families` (deel) | Tracks: haplotypes, gefaseerde markers, repeats, mtDNA, Paraphase (hoofdstuk 9) |
| `family_qc_reports.py` | `/families` | Het QC-rapport van de pipeline, via een kortlevende link en afgeschermd (hoofdstuk 2) |
| `structural_variants.py` | `/structural-variants` | Structurele varianten van één sample |
| `cnvs.py` | `/cnvs` | De klinische-CNV-catalogus en CNV's per regio |
| `variant_explorer.py` | `/variant-explorer` | Varianten over alle toegankelijke projecten heen (hoofdstuk 14) |
| `genes.py` | `/genes` | Gene Explorer en genen per regio (hoofdstuk 13) |
| `hpo.py` | `/hpo` | HPO-termen zoeken en importeren (hoofdstuk 12) |
| `panels.py` | `/panels` | Genpanels, versies, PanelApp |
| `bed.py` | `/bed` | Interval-tracks ophalen en uploaden |
| `chromosomes.py` | `/chromosomes` | Chromosoomgroottes en cytobanden |
| `blacklist.py` · `segmental_duplications.py` · `dgv.py` | `/blacklist` · `/segmental-duplications` · `/dgv` | Referentietracks |
| `repeat_expansions.py` | `/repeat-expansions` | Repeat-expansies uploaden, catalogus |
| `projects.py` | `/projects` | Projecten, de eenheid van toegang |
| `species.py` · `assemblies.py` | `/species` · `/assemblies` | Soorten en assemblies, met hun referentiestatus |
| `reference.py` | `/reference` | Referentiesequentie en reads rond een positie |
| `cram.py` | `/cram` | CRAM/BAM voor de genoombrowser, met toegangscontrole |
| `signal_tracks.py` | `/signal-tracks` | De signaalbestanden van de CNV-caller (bigWig, bedGraph) voor IGV |
| `family_imports.py` | `/family-imports` | Pakketimport (hoofdstuk 6) |
| `product.py` | `/product` | De releasecatalogus |
| `admin.py` | `/admin` | Alle beheerfuncties, elk achter `get_current_admin_user` (hoofdstuk 15) |
| `ui_events.py` | `/ui-events` | UI-telemetrie (hoofdstuk 15) |
| `lookups.py` | `/family-statuses`, `/users` | Kleine keuzelijsten voor de UI |

## De servicegroepen

`backend/app/services/` is groot. De tabel groepeert de modules naar functie; de namen zijn voorbeelden. Een Engelse indeling staat in `docs/application-scheme.md`, sectie *Main code areas*.

| Groep | Voorbeelden | Doel |
| --- | --- | --- |
| ClickHouse-variantlaag | `clickhouse_variant_storage.py`, `clickhouse_variant_queries.py`, `clickhouse_family_variants.py`, `clickhouse_interval_tracks.py` | Tabellen, query-opbouw en het uitvoeren van variantvragen |
| Familie en toegang | `family_metadata_context.py`, `metadata_service.py`, `access_control.py`, `family_structure_service.py` | Families, leden en structuur, met toegangsscoping. (`data_scope.py` gaat ondanks de naam over chromosoomnamen, niet over toegang.) |
| Import | `family_package_*.py`, `variant_upload_service.py`, `raw_import_files_pg.py`, `vcf_header_provenance.py` | Pakketimport en herkomst (hoofdstuk 6) |
| Filters en prioritering | `family_variant_filters.py`, `variant_prioritization.py`, `variant_ranking_cache.py`, `variant_explorer_service.py` | Filters, scoring, ranking en de ranking-cache (hoofdstukken 8 en 12) |
| ACMG en review | `acmg_points.py`, `cnv_acmg_points.py`, `small_variant_review_*.py`, `structural_variant_review_pg.py`, `classification_drift_service.py` | Classificatie, tags, reviewtoestand, drift (hoofdstuk 10) |
| Gespecialiseerde analyses | `nipt_*.py`, `haplotype_lineage_service.py`, `phased_marker_service.py`, `mitochondrial_analysis.py`, `paraphase_pg.py`, `sample_integrity_*.py` | NIPT, PGT, mtDNA, Paraphase, sample-QC (hoofdstuk 8) |
| Fenotype | `hpo_service.py`, `monarch_*.py` | HPO en Monarch (hoofdstuk 12) |
| Referentiedata | `gene_info_*.py`, `reference_*_service.py`, `panel_metadata_service.py`, `panelapp_service.py` | Genreferentie, referentietracks, panels (hoofdstuk 13) |
| Traceerbaarheid | `audit_log_pg.py`, `clinical_audit_service.py`, `report_signout_service.py`, `hash_chain.py`, `integrity_anchor_service.py`, `event_pipeline.py` | Auditlog, klinische audit, ondertekening, hash-ketens, ankers (hoofdstuk 11) |
| Robuustheid | `upload_safety.py`, `bounded_download.py`, `auth_rate_limit_pg.py` | Begrensde uploads en downloads, rate limiting |

## Metrics voor de monitoring

`GET /metrics` geeft de operationele cijfers van de backend in het Prometheus-formaat: verzoeken, fouten en duur per route, de laatste geplande integriteitscontrole van ClickHouse per assembly, de wachtrij en de niet-opgeslagen gebeurtenissen van de auditpijplijnen, de actieve importjobs en hoelang die geen teken van leven gaven, en de draaiende build. Er zitten geen klinische gegevens in: een verzoek krijgt het sjabloon van zijn route als label (`/families/{family_id}`, zoals ook de auditlog het bewaart), nooit het pad met zijn identificaties. Het endpoint staat uit zolang `METRICS_TOKEN` niet gezet is, en antwoordt dan alleen een verzoek met dat token als bearer-token. Het ligt buiten `/api`, en de load balancer en de frontendserver sturen alleen `/api` naar de backend door, dus het internet bereikt het nooit. In de deployment leest een collector (de Google-Built OpenTelemetry Collector), als tweede container in dezelfde Cloud Run-dienst, het elke minuut met het token, en schrijft de cijfers naar Google Cloud Managed Service for Prometheus. Alarmregels in Cloud Monitoring lezen ze daar en mailen de adressen uit `alert_notification_emails` (`terraform/monitoring.tf`). `docs/monitoring.md` beschrijft de metrics en voorgestelde alarmregels.

**Waar in de code:** `backend/app/services/operational_metrics.py` en `backend/app/routers/metrics.py`; het tellen van elk verzoek in de request-logging-middleware.

## Request-logging: elk verzoek laat een spoor na

Twee onderdelen zorgen dat elk HTTP-verzoek wordt gelogd en geaudit.

**Gestructureerde logging.** Alle backendlogs verschijnen als JSON-regels. Stuurtekens (ook regeleinden) in waarden worden vervangen voordat ze in een logregel komen, zodat niemand valse logregels kan invoegen (*log forging*).

**De request-logging-middleware** legt elk verzoek vast in de append-only tabel `audit_log_events`:

- **Wie:** gebruiker, e-mailadres en rol, uit de gebruiker die `get_current_user` aan het verzoek hing.
- **Wat:** methode, route, status, duur, IP en user-agent. Van een querystring worden standaard alleen de sleutels bewaard (`AUDIT_LOG_QUERY_STRING_MODE`), zodat te zien is welke filters een zoekopdracht gebruikte, zonder hun waarden.
- **De inhoud van een wijziging:** bij `POST`, `PUT`, `PATCH` en `DELETE` wordt de body bewaard, met gevoelige velden (wachtwoord, token, geheim, …) gemaskeerd, ook in het formulier van `/auth/token`. Een body die niet te lezen is, wordt niet ruw bewaard. De middleware leidt ook af welke entiteit en welke velden werden gewijzigd.
- **Scheiding van klinische gegevens en applicatielog:** de body (mogelijk klinische gegevens) komt alleen in de afgeschermde auditdatabank, niet in de gewone applicatielog. Dat geldt ook voor de tekst van een fout: bij een mislukte query citeert die de SQL en de parameters, waarden uit het verzoek of die ervoor gelezen werden. De 500-regel van een onafgehandelde fout noemt het soort fout (het type met SQLSTATE, of de foutcode van ClickHouse), de route en de frames van elke exception in de keten, zonder hun meldingen. uvicorn logt dezelfde fout nog eens, want Starlette geeft ze na het 500-antwoord door aan de server; een filter (`RedactServerErrorFilter`) schrijft die traceback op dezelfde manier. De volledige tekst staat alleen in de auditrij (`audit_log_events.error`). De enige uitzondering is een auditrij die de databank niet kan opslaan: die wordt met inhoud gelogd, body en fouttekst inbegrepen, zodat ze te herstellen is (zie hieronder).
- **Wat Postgres niet kan opslaan:** een NUL-teken (een `%00` in het pad of de querystring, `\u0000` in een JSON-body), de helft van een UTF-16-surrogaatpaar en de getallen `NaN` en `Infinity`. Zo'n waarde wordt als zichtbare escape bewaard: een NUL als de vier tekens `\x00`, een half paar als `\udXXX`, een getal als de tekst `"NaN"`, `"Infinity"` of `"-Infinity"`. `request_meta._escaped` noemt de kolommen waarin dat gebeurde (`core/pg_storable.py`). Vroeger liet zo'n waarde de rij mislukken, in de async-modus met de rest van de batch; een gebruiker kon zo een actie uitvoeren zonder rij in `audit_log_events`. De UI-eventlog doet hetzelfde, met `_escaped` in `detail`.

De auditregels lopen via een wachtrij die in productie nooit stil iets laat vallen: bij een volle wachtrij schrijft de backend synchroon. Zonder wachtrij (`AUDIT_LOG_MODE=sync`, of zolang er geen worker draait, zoals tijdens het opstarten en afsluiten) schrijft elk verzoek zijn regel zelf weg. Een regel die echt niet op te slaan is, wordt in elke modus met inhoud gelogd en geteld: de alert op `coga_audit_events_not_persisted_total` gaat af, en de regel is uit de log te herstellen. Van de fout bij het wegschrijven wordt alleen het soort gelogd (type en SQLSTATE), niet haar tekst, die de query en de rij citeert. Het verzoek van de gebruiker faalt er niet door, en een batch UI-events krijgt ook dan 202. Alleen in ontwikkeling mag een volle wachtrij regels laten vallen (`AUDIT_LOG_DROP_ALLOWED`) of mag de auditlog helemaal uit (`AUDIT_LOG_MODE=off`); daarbuiten weigert de backend met die waarden te starten. De standaard is `async`.

**Waar in de code:** `backend/app/core/coga_logging.py` (JSON-logging, fouten benoemen zonder hun tekst), `backend/app/middleware/request_logging.py` (de middleware), `backend/app/services/audit_log_pg.py` en `event_pipeline.py` (wegschrijven zonder verlies), `backend/app/core/pg_storable.py` (de escapes).

### De volgorde van de middleware

In Starlette (waarop FastAPI draait) is de laatst geregistreerde middleware de buitenste. Van binnen (dicht bij de route) naar buiten (dicht bij de client):

1. `CORSMiddleware` — alleen toegelaten origins mogen de API met credentials aanroepen.
2. `log_request_response` — de request-logging hierboven.
3. `normalize_api_collection_root_paths` — aanvaardt collectiepaden met én zonder slash op het einde.
4. `security_headers_middleware` — zet de security-headers op elk antwoord (hoofdstuk 2).
5. `TrustedProxyClientMiddleware` — als laatste geregistreerd, dus de buitenste: bepaalt het echte client-IP (`TRUSTED_PROXY_HOPS`) voordat logging, rate limiting en audit het lezen.

**Waar in de code:** het einde van `backend/app/main.py`; `backend/app/middleware/`.

## Belangrijkste bestanden

| Bestand | Rol |
| --- | --- |
| `backend/app/main.py` | Hangt de routers onder `/api` en zet de middlewareketen op |
| `backend/app/routers/__init__.py` | De lijst van alle routers |
| `backend/app/dependencies.py` | `get_current_user` en `get_current_admin_user` |
| `backend/app/schemas/` | Alle request- en response-modellen |
| `backend/app/core/sql.py` · `backend/app/core/clickhouse.py` | Geparametriseerde queries in Postgres en ClickHouse, grenzen per query |
| `backend/app/services/clickhouse_variant_queries.py` | De opbouw van de variantqueries, met gehele `LIMIT`/`OFFSET` |
| `backend/app/services/family_metadata_context.py` | Het toegangscheckpoint voor familiedata |
| `backend/app/middleware/request_logging.py` | Logt en audit elk verzoek, met maskering |
| `backend/app/core/coga_logging.py` | JSON-logging en bescherming tegen valse logregels |
