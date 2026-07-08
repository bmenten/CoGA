# 7. Backend: routers & services in detail

Dit hoofdstuk beschrijft hoe de API-laag van CoGA is opgebouwd: het vaste patroon **router → service → opslag**, hoe FastAPI afhankelijkheden (sessies, ingelogde gebruiker) automatisch aan elke endpoint doorgeeft, welke rol de Pydantic-schemas spelen, en — het belangrijkst voor een auditor — welke **veiligheids-invarianten overal gelden**: elke databankvraag is geparametriseerd, elke `ORDER BY` komt uit een vaste allowlist, en elke `LIMIT`/`OFFSET` wordt naar een geheel getal geforceerd. Dit hoofdstuk is de "kaart" die de losse feature-hoofdstukken (8 t/m 15) met elkaar verbindt: het legt uit wat ze gemeen hebben, zodat die hoofdstukken zich op hun eigen inhoud kunnen richten.

Een paar begrippen die vaak terugkomen, kort uitgelegd:
- **Router**: een verzameling HTTP-endpoints (URL's zoals `GET /api/families/...`). In FastAPI is dat een `APIRouter`-object.
- **Service**: een gewone Python-module met functies die de eigenlijke logica bevatten (databank bevragen, klinische berekeningen). Routers bevatten die logica bewust *niet*.
- **Dependency injection (DI)**: FastAPI-mechanisme waarbij u in de "handtekening" (de parameterlijst) van een endpoint zegt "ik heb X nodig" (bv. een databanksessie), en het framework X automatisch aanmaakt en meegeeft.
- **Pydantic-model / schema**: een Python-klasse die de vorm van inkomende en uitgaande JSON beschrijft en valideert.
- **Geparametriseerde query**: een databankvraag waarbij waarden apart worden meegegeven (als parameters) in plaats van in de tekst geplakt — de standaardverdediging tegen SQL-injectie.

## Het architecturale patroon: dunne routers, dikke services

CoGA hanteert een strikte scheiding in drie lagen:

1. **Router (dun).** Doet uitsluitend HTTP-werk: het pad en de query-parameters uitlezen, valideren, de toegangscontrole afdwingen (welke gebruiker, welke rol) en het antwoord als JSON teruggeven. Een router bevat vrijwel geen bedrijfslogica.
2. **Service (dik).** Bevat de eigenlijke business- en klinische logica: filters bouwen, variantprioritering, ACMG-regels, hash-ketens enzovoort. De services praten met de opslag.
3. **Opslag.** Twee bronnen: **Postgres** (metadata, review-toestand, audit) via SQLAlchemy in async-modus, en **ClickHouse** (grootschalige variantopslag) via een directe client.

**Waar in de code:** de map `backend/app/routers/` bevat laag 1, `backend/app/services/` laag 2, en `backend/app/core/postgres.py` + `backend/app/core/clickhouse.py` vormen de toegangspoorten tot laag 3.

### Eén voorbeeld end-to-end

Neem de endpoint die één pagina Small Variants van een familie ophaalt.

**Stap 1 — router.** In `backend/app/routers/families_small_variants.py` staat de functie `get_family_small_variants`, gekoppeld aan `GET /api/families/{family_id}/small-variants`. Sterk ingekort ziet die er zo uit:

```python
@router.get("/{family_id}/small-variants", response_model=VariantPage)
async def get_family_small_variants(
    family_id: str,
    page: int = 1,
    page_size: int = Query(default=100, ge=0, le=MAX_VARIANT_PAGE_SIZE),
    # ... (overige filter- en modusparameters weggelaten)
    filters: Dict[str, Any] = Depends(_family_small_variant_filters),
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> VariantPage:
```

Let op vier dingen die de router doet en verder niets:
- `page_size: int = Query(..., ge=0, le=MAX_VARIANT_PAGE_SIZE)` — FastAPI **valideert** dat de paginagrootte een geheel getal is tussen 0 en een bovengrens; te grote waarden worden met een 422-fout geweigerd vóór er ook maar iets draait.
- `session = Depends(get_postgres_session)` en `user = Depends(get_current_user)` — de databanksessie en de ingelogde gebruiker worden **geïnjecteerd** (zie volgende sectie).
- `filters = Depends(_family_small_variant_filters)` — de vele filterparameters worden door een aparte hulp-dependency uitgelezen en gevalideerd, zodat de endpointfunctie overzichtelijk blijft.
- `response_model=VariantPage` — het antwoord wordt tegen een Pydantic-schema gevalideerd.

**Stap 2 — toegangscontrole + context.** De router roept `build_family_metadata_context` aan (in `backend/app/services/family_metadata_context.py`). Die functie zoekt de familie op **die deze gebruiker mag zien** (via `get_accessible_family_mapping`) en beperkt de zichtbare projecten via `_visible_project_ids`: een admin ziet alles, een gewone gebruiker alleen de projecten in `user.metadata_project_ids`. Vraagt de gebruiker een project op dat niet aan de familie is gekoppeld, dan volgt een `HTTPException` (400). Dit is het punt waar de **rij-scoping** (welke data een gebruiker mag benaderen — zie ook [hoofdstuk 2](02-beveiliging-rollen-rechten.md)) wordt afgedwongen, nog vóór er een variant-query naar ClickHouse gaat.

**Stap 3 — service + opslag.** De router geeft die context door aan `get_family_small_variants_page` (in `backend/app/services/clickhouse_family_variants.py`). Die servicelaag bouwt de ClickHouse-query op met de scoping-clausules erin verweven — bijvoorbeeld `e.family_guid = %(family_guid)s` en `e.project_guid IN %(project_ids)s`, met de waarden als **parameters** — en voert hem uit via `execute_clickhouse` in `backend/app/core/clickhouse.py`.

**Stap 4 — antwoord.** Het resultaat wordt teruggegeven als een `VariantPage`-Pydantic-model, dat FastAPI naar JSON serialiseert.

De rode draad: de router raakt **nooit** rechtstreeks SQL of ClickHouse aan, en de service bemoeit zich **nooit** met HTTP-statuscodes of tokens. Elke laag heeft één verantwoordelijkheid, wat het voor een reviewer eenvoudig maakt om te controleren waar toegangscontrole, waar validatie en waar de query-opbouw gebeurt.

## Dependency injection: sessies, gebruiker en scoping

FastAPI-DI is het mechanisme dat elke router "gratis" de bouwstenen geeft die hij nodig heeft. In CoGA zijn er twee kern-dependencies die in vrijwel elke beschermde endpoint terugkomen.

**De databanksessie.** `get_postgres_session` (in `backend/app/core/postgres.py`) levert per verzoek een verse async SQLAlchemy-sessie uit een gedeelde `sessionmaker` en sluit die netjes af als het verzoek klaar is (`async with session_factory() as session: yield session`). Elke router die Postgres nodig heeft, schrijft simpelweg `session: AsyncSession = Depends(get_postgres_session)`.

**De ingelogde gebruiker.** `get_current_user` (in `backend/app/dependencies.py`) is de authenticatie-poortwachter. De functie:
- haalt het bearer-token uit de `Authorization`-header (`oauth2_scheme`);
- valideert het — ofwel een Azure-SSO-token (`verify_azure_token`) wanneer Azure is geconfigureerd, ofwel een lokaal HS256-JWT (`jwt.decode` met `settings.secret_key`);
- zoekt de gebruiker op via `get_current_user_by_email` en weigert onbekende of inactieve accounts;
- kent een lokaal "break-glass"-token (het pad `azure_admin_override`, standaard uitgeschakeld) alleen toe aan een admin, en **logt elk gebruik** van dat noodpad als waarschuwing — een expliciet audit-spoor;
- zet de gebruiker op `request.state.current_user` — waardoor de logging-middleware (verderop) later weet wie het verzoek deed.

Faalt de validatie ook maar ergens, dan volgt steeds dezelfde `credentials_exception` (HTTP 401). Voor endpoints die admin-rechten vereisen, is er de afgeleide dependency `get_current_admin_user`, die bovenop `get_current_user` controleert of de rol in `ADMIN_ROLES = {"admin", "superuser"}` zit en anders een 403 gooit.

**Scoping op router-niveau.** Een router kan een dependency op *alle* endpoints tegelijk leggen. Zo staat er in `backend/app/routers/dgv.py`: `APIRouter(prefix="/dgv", tags=["dgv"], dependencies=[Depends(get_current_user)])` — elke DGV-endpoint vereist dan automatisch een geldig token, zonder dat het per functie herhaald hoeft te worden.

**Waar in de code:** `backend/app/dependencies.py` (`get_current_user`, `get_current_admin_user`, `ADMIN_ROLES`) en `backend/app/core/postgres.py` (`get_postgres_session`).

## Schemas: validatie en levende documentatie

Alle request- en response-vormen staan in één centraal bestand, `backend/app/schemas.py` (ruim 260 Pydantic-modellen). Deze modellen doen drie dingen tegelijk:

- **Inkomende validatie.** Een `...Update`- of `...Request`-model (bv. `FamilyMetadataUpdate`, `SmallVariantReviewUpdate`, `ReportSignoutRequest`) beschrijft precies welke velden mogen binnenkomen en van welk type. Ongeldige JSON wordt met een 422-fout geweigerd nog vóór de router-code draait.
- **Uitgaande vorm.** Een `...Out`-model (bv. `FamilyOut`, `VariantPage`, `SmallVariantReviewOut`, `IntegrityAnchorOut`) beschrijft wat de API teruggeeft; via `response_model=` in de decorator dwingt FastAPI die vorm af en filtert het onbedoelde velden weg.
- **OpenAPI-documentatie.** Uit dezelfde modellen genereert FastAPI de interactieve `/docs` (OpenAPI/Swagger). In productie zijn `/docs`, `/redoc` en `/openapi.json` bewust uitgeschakeld (`_docs_kwargs` in `backend/app/main.py`) om schema-onthulling te beperken; de in-process schema-generatie (`app.openapi()`) blijft wel werken.

Doordat alle schemas op één plek staan, kan een reviewer in één bestand nagaan welke gegevens het systeem in- en uitgaan — nuttig voor de dataflow-analyse die bij een IVDR-dossier hoort.

**Waar in de code:** `backend/app/schemas.py`; de koppeling gebeurt in elke router via `response_model=...` en getypeerde parameters.

## Veiligheids-invarianten die overal gelden

Dit is voor de auditor het kernstuk van het hoofdstuk. Ongeacht welke endpoint of service u bekijkt, gelden onderstaande regels. Ze zijn niet per feature opnieuw bedacht, maar afgedwongen in enkele gedeelde helpers.

### 1. Alle queries zijn geparametriseerd — geen string-interpolatie

Waarden gaan **nooit** als tekst in een query, maar altijd als losse parameter.

- **Postgres.** SQLAlchemy-queries gebruiken benoemde bindparameters (`:naam`) en de sessie geeft de waarden apart mee. Voor lijsten van UUID's is er een speciale helper `uuid_list_bindparam` in `backend/app/core/sql.py`, die een `expanding` bindparameter met UUID-type oplevert — zo kan een `IN :project_ids` veilig een variabel aantal UUID's aan zonder tekstopbouw. Voorbeeld uit `family_metadata_context.py`: `... fp.project_id IN :project_ids`, met de query voorzien van `.bindparams(uuid_list_bindparam("project_ids"))` en de waarden via `uuid_values(project_ids)`.
- **ClickHouse.** Queries gebruiken de `%(naam)s`-parameterstijl en geven de waarden mee via het `parameters=`-argument van `execute_clickhouse`. Zo staat in `backend/app/services/clickhouse_variant_queries.py` bijvoorbeeld `where_clauses.append("e.project_guid IN %(project_ids)s")` met `params["project_ids"] = tuple(context.project_ids)`. De query-tekst bevat de waarde nooit letterlijk.

**Waar in de code:** `backend/app/core/sql.py` (bindparam-helpers), `backend/app/core/clickhouse.py` (`execute_clickhouse`, dat `parameters` doorgeeft aan de driver).

### 2. `ORDER BY` komt uit een vaste allowlist

De enige plek waar een kolomnaam per definitie niet als parameter kan (SQL laat geen geparametriseerde kolomnamen toe), is de sorteervolgorde. CoGA lost dit op door **nooit** door de gebruiker aangeleverde tekst in een `ORDER BY` te zetten, maar die te vertalen via een vaste tabel:

- In de Variant Explorer (`backend/app/services/variant_explorer_service.py`) mapt `_SORT_EXPR` een handvol toegestane sorteersleutels (`total_samples`, `het_samples`, `position`, ...) naar hun kolom-expressie. Onbekende invoer valt terug op `_DEFAULT_SORT` (`"total_samples"`) via `sort = sort if sort in _SORT_EXPR else _DEFAULT_SORT`. Er kan dus alleen op een vooraf goedgekeurde kolom worden gesorteerd.
- In de integriteits-hashketen (`backend/app/services/integrity_anchor_service.py`) beperkt `_CHAIN_ORDER_COLS` de sorteerkolommen tot een vaste set per tabel; de helper `_order_by` bouwt de clausule alleen daaruit op. De code merkt daar expliciet bij op: *"Fixed set — never interpolate untrusted table names."*

### 3. `LIMIT`/`OFFSET` worden naar gehele getallen geforceerd

Paginatie-grenzen worden altijd door `int(...)` gehaald en naar minimaal 0 geklemd, zodat er geen willekeurige tekst in kan sluipen. In `clickhouse_variant_queries.py`:

```python
params["limit"] = max(int(limit), 0)
params["offset"] = max(int(offset), 0)
return f"{query}\n        LIMIT %(limit)s OFFSET %(offset)s"
```

De `LIMIT`/`OFFSET`-waarden staan bovendien zélf als parameter in de query. Aan de router-kant vangt FastAPI het al eerder af met typering en grenzen, bijvoorbeeld `page_size: int = Query(default=100, ge=0, le=MAX_VARIANT_PAGE_SIZE)`.

### 4. Extra hardening rond ClickHouse

Naast bovenstaande gelden in `backend/app/core/clickhouse.py` nog enkele beschermingen die de opslaglaag robuust én veilig houden:
- **Dataset-sleutel gesaneerd.** `clickhouse_dataset_key` is de enige plek waar de assembly-naam (bv. `GRCh38`) in een tabelpad terechtkomt; alles buiten `[A-Za-z0-9._-]` wordt vervangen door `_`, zodat de naam veilig te interpoleren is en ingestie en leespad gegarandeerd hetzelfde pad gebruiken.
- **Per-query begrenzing.** `_clickhouse_query_settings` legt `max_execution_time`, `max_query_size` en geheugen-/spill-grenzen op, zodat één brede filter de request-worker niet eindeloos kan bezetten.
- **Transiënte fouten.** `execute_clickhouse` en `insert_clickhouse` herstellen de gedeelde client en proberen één keer opnieuw bij een verbroken socket of gelockte sessie, wat sporadische 500-fouten voorkomt zonder de query-inhoud te wijzigen.

## Overzicht: alle routers

Alle routers worden verzameld in `backend/app/routers/__init__.py` (de lijst `all_routers`) en in `backend/app/main.py` onder het pad-voorvoegsel `/api` gemonteerd. De `families`-router bindt daarnaast nog **vijf sub-routers** in (Small Variants, structurele varianten, NIPT, rapporten, tracks) onder hetzelfde `/families`-pad; die sub-routers hebben daarom zelf géén eigen voorvoegsel.

| Router (bestand) | Pad onder `/api` | Doel |
|---|---|---|
| `health.py` | `/health`, `/version`, `/health/ready` | Liveness/readiness-checks; geen auth. |
| `auth.py` | `/auth` | Login, token-uitgifte (`/auth/token`), self-service accountacties. Zie [hoofdstuk 5](05-login-authenticatie.md). |
| `ped.py` | `/ped` | Pedigree (stamboom) uploaden/uitlezen. |
| `families.py` | `/families` | Familie-metadata, leden, structuurversies, HPO-annotaties, Monarch-fenotypescores, region-of-interest. Kern-router; bindt de sub-routers hieronder in. |
| `families_small_variants.py` | `/families` (sub) | Small Variants: paginering, export (CSV), compound-het, filter-presets, tags, review/ACMG. Zie [hoofdstuk 8](08-filterpaginas-en-api.md). |
| `families_structural_variants.py` | `/families` (sub) | Structurele varianten van een familie. |
| `families_nipt.py` | `/families` (sub) | NIPT-resultaten per familie (samenvatting, varianten, coverage). |
| `families_reports.py` | `/families` (sub) | Annotatie-manifest, classification-drift, klinische audit, rapporten en sign-out. Zie [hoofdstuk 11](11-rapport-en-traceerbaarheid.md). |
| `families_tracks.py` | `/families` (sub) | Visualisatie-tracks (haplotypes, gefaseerde markers, repeat-expansies) per familie. Zie [hoofdstuk 9](09-visualisaties.md). |
| `structural_variants.py` | `/structural-variants` | Structurele varianten buiten de familiecontext. |
| `cnvs.py` | *(geen eigen voorvoegsel)* `/{assembly}/{chrom}`, `/{assembly}/catalog`, `/entry/{cnv_id}` | Copy-number-varianten en hun catalogus/scoring. |
| `variant_explorer.py` | `/variant-explorer` | Cohort-brede variantverkenning. Zie [hoofdstuk 14](14-variant-explorer.md). |
| `genes.py` | `/genes` | Gene Explorer, genreferentie/versies. Zie [hoofdstuk 13](13-gene-explorer.md). |
| `hpo.py` | `/hpo` | HPO-ontologie: termen, zoeken, prioritering. Zie [hoofdstuk 12](12-hpo-monarch-prioritisatie.md). |
| `panels.py` | `/panels` | Genpanels (o.a. PanelApp-integratie). |
| `bed.py` | `/bed` | BED-regio's en interval-berekeningen. |
| `chromosomes.py` | *(geen eigen voorvoegsel)* `/{assembly}`, `/{assembly}/details`, `/{assembly}/{chrom}` | Chromosoom-metadata voor visualisaties. |
| `blacklist.py` | *(geen eigen voorvoegsel)* `/{assembly}/{chrom}` | Blacklist-regio's. |
| `segmental_duplications.py` | *(geen eigen voorvoegsel)* `/{assembly}/{chrom}` | Segmentale duplicaties (referentietrack). |
| `dgv.py` | `/dgv` | Database of Genomic Variants; volledige router achter `get_current_user`. |
| `repeat_expansions.py` | `/repeat-expansions` | TRGT/repeat-expansie-analyse en -catalogus. |
| `projects.py` | `/projects` | Projecten (de scoping-eenheid voor toegang). |
| `species.py` | `/species` | Soorten. |
| `assemblies.py` | `/assemblies` | Genoom-assemblies (GRCh38, T2T, ...). |
| `reference.py` | `/reference` | Referentiegenoom en referentiebronnen. |
| `cram.py` | `/cram` | CRAM/BAM-uitlevering voor IGV-achtige weergave. |
| `family_imports.py` | `/family-imports` | Import van familie-pakketten (manifest, voortgang, status). Zie [hoofdstuk 6](06-import-pipeline.md). |
| `product.py` | `/product` | Productinfo, o.a. GitHub-release-catalogus (`/product/releases`, versie/traceerbaarheid). |
| `admin.py` | `/admin` | Adminfunctionaliteit; achter `get_current_admin_user`. Zie [hoofdstuk 15](15-overige-modules-en-admin.md). |
| `ui_events.py` | `/ui-events` | Front-end telemetrie/gebruikersgebeurtenissen (audit van UI-acties). |
| `lookups.py` | *(geen eigen voorvoegsel)* `/family-statuses`, `/users` | Kleine keuzelijsten (familie-statussen, gebruikersreferenties) voor de UI. |

## Overzicht: de servicegroepen

De servicelaag is groot; onderstaande tabel groepeert de modules in `backend/app/services/` naar functie. De namen zijn representatief, niet uitputtend.

| Servicegroep | Kernbestanden | Doel |
|---|---|---|
| **ClickHouse-variantlaag** | `clickhouse_variant_storage.py`, `clickhouse_variant_queries.py`, `clickhouse_variant_records.py`, `clickhouse_variant_rows.py`, `clickhouse_variant_ids.py`, `clickhouse_small_variants.py`, `clickhouse_family_variants.py`, `clickhouse_interval_tracks.py`, `clickhouse_integrity_monitor.py` | Opbouw en uitvoering van variant-queries tegen ClickHouse. De "leaf"-modules `clickhouse_variant_queries.py` (het *bouwen* van de SQL-tekst + parameters, met allowlist- en int-coercie) en `clickhouse_variant_records.py` (het *parsen* van ruwe rijen naar records) scheiden query-opbouw van resultaatverwerking. |
| **Familie-metadata & context** | `family_metadata_context.py`, `family_service.py`, `family_member_management_service.py`, `family_structure_service.py`, `family_status_service.py`, `metadata_service.py`, `data_scope.py` | Familie/lid/structuur opzoeken met **toegangs-scoping**; `data_scope.py` normaliseert chromosoomnamen (bv. `chr1` → `1`) en scheidt primaire chromosomen van ALT/scaffold-contigs. |
| **Import-pipeline** | `family_package_*.py` (o.a. `_manifest`, `_validation`, `_import`, `_registration`, `_datasets`, `_variants`), `variant_upload_service.py`, `raw_import_files_pg.py`, `vcf_header_provenance.py` | Pakket-import: manifest lezen, valideren, registreren, varianten laden; provenance van VCF-headers. Zie [hoofdstuk 6](06-import-pipeline.md). |
| **Filters & prioritisatie** | `family_variant_filters.py`, `variant_prioritization.py`, `variant_ranking_cache.py`, `variant_explorer_service.py`, `variant_annotation_parser.py` | Filterlogica, variant-scoring/-ranking (incl. de sorteer-allowlist `_SORT_EXPR`), annotatie parsen. |
| **ACMG & review** | `acmg_points.py`, `cnv_acmg_points.py`, `small_variant_review_*.py`, `structural_variant_review_pg.py`, `classification_drift_service.py` | Semi-automatische ACMG-classificatie, tags/presets, review-toestand, drift-detectie. Zie [hoofdstuk 10](10-tagging-en-acmg-classificatie.md). |
| **NIPT** | `nipt.py`, `nipt_analysis.py`, `nipt_coverage.py`, `nipt_service.py`, `nipt_artifact_pg.py` | Niet-invasieve prenatale test: fetale fractie, classificaties, coverage, artefacten. |
| **Haplotype & lineage** | `haplotype_lineage_service.py`, `phased_marker_service.py`, map `haplotype_interpretation/`, `paraphase_pg.py` | Haplotype-blokken, gefaseerde markers, lineage/IBD, Paraphase. |
| **HPO / Monarch** | `hpo_service.py`, `monarch_ingest.py`, `monarch_phenotype_score.py`, `monarch_semsim.py` | HPO-ontologie beheren, Monarch-fenotype-scoring en semantische similariteit. Zie [hoofdstuk 12](12-hpo-monarch-prioritisatie.md). |
| **Gen-/referentie-metadata** | `gene_metadata_service.py`, `gene_info_external.py`, `gene_info_bulk_sources.py`, `gene_info_jobs_pg.py`, `reference_metadata_service.py`, `reference_source_service.py`, `panel_metadata_service.py`, `panelapp_service.py`, `github_releases_service.py` | Genreferentie verversen (achtergrond-job), referentietracks, panels, externe bron-lookups. |
| **Traceerbaarheid & integriteit** | `audit_log_pg.py`, `clinical_audit_service.py`, `report_signout_service.py`, `hash_chain.py`, `integrity_anchor_service.py`, `sample_integrity_service.py`, `sample_integrity_qc.py`, `ui_event_pg.py`, `event_pipeline.py` | Append-only audit-log, klinische audit, rapport-sign-out, hash-ketens en verankering, sample-integriteit-QC. Zie [hoofdstuk 11](11-rapport-en-traceerbaarheid.md). |
| **Infrastructuur/robuustheid** | `bounded_download.py`, `upload_safety.py`, `auth_rate_limit_pg.py`, `review_pg_utils.py` | Begrensde downloads, veilige uploads, login-rate-limiting, gedeelde DB-hulpjes. |

## Logging en audit: elk verzoek laat een spoor na

Traceerbaarheid begint bij de vaststelling dat **elk** HTTP-verzoek gelogd én geaudit wordt. Twee samenwerkende onderdelen zorgen daarvoor.

**Gestructureerde JSON-logging.** `configure_json_logging` in `backend/app/core/coga_logging.py` installeert één `JsonLogFormatter` op de root-logger, zodat alle backend-logs als JSON-regels verschijnen (met `timestamp`, `severity`, `message`, en optioneel `user`, `httpRequest`, `dbUpdate`, `traceback`). Cruciaal voor veiligheid is `scrub_log`: die vervangt stuurtekens (inclusief CR/LF) in waarden vóór ze in een logregel komen, wat **log-forging** (CWE-117) tegengaat.

**De request-logging-middleware.** `log_request_response` in `backend/app/middleware/request_logging.py` wikkelt elk verzoek en doet, samengevat:
- **De aanvrager identificeren.** Via `request.state.current_user` (door `get_current_user` gezet) weet de middleware wie het verzoek deed — gebruikers-id, e-mail en rol komen in het spoor (`_get_request_user`).
- **Het verzoek-lichaam vastleggen, maar veilig.** Voor muterende methodes (POST/PUT/PATCH/DELETE) wordt de body vastgelegd, maar `_sanitize_for_logging` maskeert gevoelige sleutels (`password`, `secret`, `token`, ...) tot `***`. Form-encoded logins (`/api/auth/token`) worden expliciet ontleed en gemaskeerd, zodat een wachtwoord nooit in klare tekst wordt opgeslagen; onparseerbare bodies worden niet ruw bewaard maar vervangen door een placeholder ("fail closed"). Query-parameters worden gesaneerd via `_sanitize_query_param` (patiënt-/familie-/projectidentificatoren worden gemaskeerd; de sterkte is instelbaar via `audit_log_query_string_mode`).
- **Scheiding van PHI en applicatielog.** Het verzoek-lichaam (mogelijk klinische PHI, tot ~25 KB) wordt **niet** naar de stdout-applicatielog geschreven; het gaat uitsluitend naar de toegang-gecontroleerde audit-databank (kolom `audit_log_events.request_body`).
- **De mutatie afleiden.** `_derive_db_update` herleidt uit pad en methode welke entiteit werd aangemaakt/gewijzigd/verwijderd (het API-voorvoegsel wordt weggestript, zodat er bv. `families` staat en niet `api`) en welke velden — zodat het audit-spoor "wie wijzigde wat" bevat, zonder de volledige waarden.
- **Naar de audit-DB schrijven.** Via `write_audit_log_event` (uit `services/audit_log_pg.py`) wordt een volledig `AuditLogEventPayload` weggeschreven: gebruiker, methode, route, status, duur, IP, user-agent, de mutatie en eventuele fout. Faalt dat wegschrijven, dan wordt dat zelf als waarschuwing gelogd — het verzoek zelf wordt niet stukgemaakt.

De statuscode bepaalt het log-niveau: ≥500 → `error` (met traceback), ≥400 → `warning`, overig → `info`. Zo is elke fout en elke mutatie achteraf reconstrueerbaar.

**Waar in de code:** `backend/app/core/coga_logging.py` (formatter + `scrub_log`), `backend/app/middleware/request_logging.py` (`log_request_response`), `backend/app/services/audit_log_pg.py` (`write_audit_log_event`).

### Waar de middlewares worden aangehaakt

De volgorde van de middlewares is bewust en staat in `backend/app/main.py`. Ze worden in deze vololgorde geregistreerd; in Starlette is de **laatst geregistreerde de buitenste**, dus van binnen (dichtst bij de route) naar buiten (dichtst bij de client):
1. `CORSMiddleware` — alleen toegestane origins (`settings.cors_origins`, eventueel `cors_origin_regex`) mogen de API met credentials aanroepen.
2. `log_request_response` — de audit-/loglaag hierboven.
3. `normalize_api_collection_root_paths` — accepteert collectiepaden met én zonder afsluitende slash.
4. `security_headers_middleware` — als **laatst geregistreerd, dus buitenste**, stempelt het de hardening-headers op elk antwoord.

De beveiligingsheaders in `backend/app/middleware/security_headers.py` zetten op elk antwoord onder meer `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, een maximaal strikte `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'` (de API levert immers alleen JSON), `Referrer-Policy: no-referrer` en cross-origin-isolatie (`Cross-Origin-Opener-Policy: same-origin`, `Cross-Origin-Resource-Policy: same-site`). HSTS is opt-in (`settings.enable_hsts`) zodat het nooit over plein HTTP verschijnt. `setdefault` zorgt dat een route die bewust zelf een header zette, niet wordt overschreven.

## Belangrijkste bestanden

| Bestand | Rol |
|---|---|
| `backend/app/main.py` | Bouwt de FastAPI-app: monteert alle routers onder `/api`, hangt de middleware-keten op, regelt lifespan, schakelt `/docs` uit in productie. |
| `backend/app/routers/__init__.py` | Verzamelt alle routers in `all_routers`. |
| `backend/app/dependencies.py` | Authenticatie-dependencies `get_current_user` / `get_current_admin_user`, wachtwoord- en token-helpers, `ADMIN_ROLES`. |
| `backend/app/schemas.py` | Alle Pydantic-request/response-modellen; validatie en OpenAPI-documentatie. |
| `backend/app/core/sql.py` | SQL-veiligheidshelpers: UUID-bindparameters (`uuid_list_bindparam`), schema-fout-detectie. |
| `backend/app/core/postgres.py` | Postgres-engine/sessie (`get_postgres_session`), schema-initialisatie. |
| `backend/app/core/clickhouse.py` | ClickHouse-client, `execute_clickhouse`/`insert_clickhouse` (geparametriseerd), dataset-sleutel-sanitisatie, per-query-begrenzing. |
| `backend/app/services/clickhouse_variant_queries.py` | Bouwt de variant-SQL: geparametriseerde clausules, int-coerced `LIMIT`/`OFFSET`. |
| `backend/app/services/clickhouse_variant_records.py` | Parseert ruwe ClickHouse-rijen naar variant-records/annotaties. |
| `backend/app/services/variant_explorer_service.py` | Voorbeeld van de `ORDER BY`-allowlist (`_SORT_EXPR`, `_DEFAULT_SORT`). |
| `backend/app/services/family_metadata_context.py` | Dwingt per-gebruiker project-/familiescoping af (`_visible_project_ids`, `build_family_metadata_context`). |
| `backend/app/core/coga_logging.py` | JSON-logformatter en `scrub_log` (anti-log-forging). |
| `backend/app/middleware/request_logging.py` | Logt en audit elk verzoek; maskeert gevoelige velden; scheidt PHI van applicatielog. |
| `backend/app/middleware/security_headers.py` | Zet hardening-response-headers op elk antwoord. |
| `backend/app/core/http_resilience.py` | Bounded retry/backoff voor uitgaande calls naar externe referentie-API's (HGNC/Ensembl/NCBI/ClinGen/PanelApp) in achtergrond-jobs. |
