# 1. Algemene architectuur & structuur

Dit hoofdstuk beschrijft de "kaart" van het hele CoGA-platform: uit welke drie lagen het systeem bestaat (frontend, backend en twee databanken), waarom die scheiding er is, en hoe een enkel verzoek van een muisklik in de browser tot aan de data en weer terug loopt. Verder komt de mappenstructuur aan bod, wordt getoond hoe frontend en backend tegelijk *gescheiden* én *verbonden* zijn (via de axios-client en JWT), waar de centrale configuratie leeft en hoe de applicatie opstart. Tot slot worden de gebruikte technologieën met hun versies opgesomd. Deze basis vormt de context voor de rest van de handleiding.

## Wat is CoGA in één alinea

CoGA (*Comprehensive Genomic Analysis*) is een klinisch-genomicaplatform voor variant-interpretatie, genoomvisualisatie en klinische review op familieniveau. Het draait als **in-house IVD onder IVDR Artikel 5(5)** bij CMGG (ISO 15189-geaccrediteerd laboratorium). "IVD" staat voor *in-vitro diagnostiek*; "IVDR" is de Europese verordening die zulke diagnostiek reguleert. De grens van het gereguleerde apparaat ("device boundary") loopt van *geannoteerde VCF-bestand* tot *ondertekend klinisch rapport*. De data in dit systeem is synthetisch: er is geen echte patiëntdata. Dat staat beschreven in `README.md` en `AGENTS.md`.

## De drie-lagen-architectuur

CoGA is opgebouwd uit drie duidelijk gescheiden lagen. Elke laag heeft één verantwoordelijkheid, wat de veiligheid en de traceerbaarheid ten goede komt: fouten en toegangscontrole zijn zo gemakkelijker te lokaliseren en te auditen.

| Laag | Technologie | Rol | Waar in de code |
| --- | --- | --- | --- |
| Frontend (presentatie) | React + TypeScript + Vite + Tailwind | De gebruikersinterface in de browser: login, dashboards, familiewerkruimte, filterpagina's en visualisaties | `frontend/src/` |
| Backend (logica & API) | FastAPI (Python) | De API-server: authenticatie, toegangscontrole, klinische logica, databankquery's | `backend/app/` |
| Databanken (opslag) | Postgres + ClickHouse | Twee gescheiden databanken: metadata/review-toestand vs. grootschalige variantopslag | `backend/db/schema/postgres/` en `backend/db/schema/clickhouse/` |

### Waarom twee databanken?

Een centraal ontwerpprincipe van CoGA is de **gesplitste opslag** ("split storage model"). Er zijn twee soorten data met heel andere eigenschappen:

- **Postgres** is een klassieke relationele databank. Ze is *gezaghebbend* (de bron van waarheid) voor metadata en toestand: gebruikers, projecttoegang, families, samples, pedigree-structuur, review-toestand, panels, gene-cache, de repeat-catalogus, interval-track-metadata en het auditspoor. Dit zijn relatief kleine, sterk gestructureerde en vaak-gewijzigde gegevens.
- **ClickHouse** is een kolomgeoriënteerde databank die gebouwd is voor enorme hoeveelheden data en snelle analytische query's. Ze bewaart de eigenlijke variant-payloads: Small Variants (SNV's/indels), structurele varianten, familie/sample-genotypes, cross-project-aggregaten en high-volume interval-tracks (dekking, WisecondorX-segmenten, APCAD, haplotypes). Een enkele familie kan miljoenen variantrijen bevatten.

De verantwoordelijkheden van elke databank staan opgesomd in `docs/storage-architecture.md` (sectie "Split storage model"). De gezaghebbendheids-regel is expliciet vastgelegd in `docs/application-scheme.md`, sectie "Storage Boundary": *"Postgres is authoritative for metadata and state. ClickHouse is authoritative for variant payloads."* Bij het beantwoorden van een verzoek worden ClickHouse-variantrijen op verzoek "teruggekoppeld" (gejoind) aan de Postgres-metadata — bijvoorbeeld om een tag of ACMG-classificatie op een variant te tonen.

**Waar in de code:** de opslagverantwoordelijkheden staan in `docs/storage-architecture.md`; de gezaghebbendheids-grens en de runtime-flow in `docs/application-scheme.md`. De verbindingshulpmiddelen leven in `backend/app/core/postgres.py` en `backend/app/core/clickhouse.py`.

## De levensloop van een verzoek: van klik tot data en terug

Het beste mentale model is een "estafette" waarbij elke laag het stokje doorgeeft. Neem als voorbeeld een gebruiker die de Small Variants van een familie opvraagt.

1. **De klik in de browser.** De gebruiker navigeert in de React-frontend naar een familiepagina. React-componenten roepen de gedeelde API-client aan.
   **Waar in de code:** de pagina's in `frontend/src/pages/` (bv. `FamilySmallVariantsPage` in `frontend/src/pages/families/FamilySmallVariantsPage.tsx`), die data ophalen via de client in `frontend/src/lib/api.ts`.

2. **De axios-client verstuurt het HTTP-verzoek.** CoGA gebruikt *axios* (een populaire JavaScript-bibliotheek om HTTP-verzoeken te doen). Alle verzoeken vertrekken via één gedeelde instantie met basis-URL `/api`. Een *interceptor* (een stukje code dat elk uitgaand verzoek onderschept) plakt automatisch het JWT-token in de `Authorization`-header.
   **Waar in de code:** `frontend/src/lib/api.ts` — de `axios.create({ baseURL: apiBaseUrl })` en de `api.interceptors.request.use(...)`.

3. **Het verzoek komt binnen op de FastAPI-backend, onder `/api`.** Alle routers zitten achter het prefix `/api`. Voordat de eigenlijke logica draait, passeert het verzoek een keten van *middleware* (tussenlagen die elk verzoek en antwoord bewerken): request-logging/audit, trailing-slash-normalisatie en security-headers.
   **Waar in de code:** `backend/app/main.py`, waar `api_router = APIRouter(prefix=API_PATH_PREFIX)` gemaakt wordt en alle routers uit `all_routers` erin worden opgenomen.

4. **De router handelt het endpoint af en controleert de toegang.** Elk beschermd router-eindpunt vereist een geauthenticeerde gebruiker via de *dependency* `get_current_user` (een FastAPI-mechanisme dat vóór de eigenlijke functie draait). Deze functie valideert het JWT-token en laadt de gebruiker uit Postgres. Toegang is bovendien **project-gebonden** (project-scoped RBAC — *Role-Based Access Control*).
   **Waar in de code:** `backend/app/dependencies.py`, functie `get_current_user` (en `get_current_admin_user` voor admin-endpoints); de routers in `backend/app/routers/`.

5. **De router roept een service aan (de eigenlijke klinische/bedrijfslogica).** Routers blijven dun; de echte logica leeft in `backend/app/services/`. Voor familie-varianten is dat bijvoorbeeld `clickhouse_family_variants.py`.
   **Waar in de code:** `backend/app/services/` (zie `docs/application-scheme.md`, "Main Code Areas").

6. **De service bevraagt de juiste databank.** Metadata-query's gaan naar Postgres (via SQLAlchemy async sessies); variant-query's gaan naar ClickHouse (via een directe ClickHouse-client). Vaak worden beide gecombineerd: de variantrijen uit ClickHouse worden verrijkt met review-annotaties uit Postgres.
   **Waar in de code:** de query-services in `backend/app/services/`, met verbindingshulp uit `backend/app/core/postgres.py` en `backend/app/core/clickhouse.py`.

7. **Het antwoord reist terug.** De service geeft data terug aan de router, FastAPI serialiseert het naar JSON, de middleware stempelt de veiligheids-headers erop, en axios levert het antwoord af bij het React-component, dat het rendert.
   **Waar in de code:** de response-interceptor in `frontend/src/lib/api.ts` (die o.a. bij een `401`-fout de sessie wist via `clearSession()` en terugstuurt naar `/login`).

Dit hele patroon is samengevat in `docs/application-scheme.md` onder "Flow Summary" en in `docs/storage-architecture.md` onder "Runtime Flow".

## De mappenstructuur op hoog niveau

De repository-root bevat de twee applicatiemappen (`backend/`, `frontend/`), de documentatie (`docs/`), infrastructuur-as-code (`terraform/`) en de Docker-orchestratie.

### Backend — `backend/app/`

| Map/bestand | Rol |
| --- | --- |
| `core/` | Verbindings- en runtime-hulpmiddelen: `config.py` (instellingen), `postgres.py`, `clickhouse.py`, `azure.py`, `coga_logging.py`, `object_storage.py`, e.a. |
| `routers/` | De API-oppervlakte: één bestand per domein (`auth`, `families`, `structural_variants`, `genes`, `admin`, …), gebundeld in `routers/__init__.py` tot de lijst `all_routers` |
| `services/` | De klinische en bedrijfslogica: variant-query's, ACMG-scoring, NIPT, haplotypes, HPO/Monarch, en de traceerbaarheidsstack (audit, hash-chain, integrity anchors) |
| `middleware/` | Tussenlagen die op elk verzoek draaien: `request_logging.py`, `security_headers.py` |
| `db_migrate.py` | Out-of-band schema-migratie en de admin-seed (`init_postgres_admin_user`), bruikbaar als los CLI-commando of vanuit de opstartroutine |
| `main.py` | Het startpunt: bouwt de FastAPI-app, mount de routers, configureert CORS/middleware en de `lifespan`-opstartroutine |
| `dependencies.py` | Authenticatie- en autorisatiehulp (`create_access_token`, `get_current_user`, `get_current_admin_user`) |

Het databank-schema staat naast `app/` in `backend/db/schema/`: `postgres/` (genummerde `.sql`-bestanden, bv. `001_metadata.sql`) en `clickhouse/` (`001_coga_variant_storage.sql`).

### Frontend — `frontend/src/`

| Map | Rol |
| --- | --- |
| `pages/` | De schermen, per domein gegroepeerd (`auth/`, `families/`, `genome/`, `admin/`, …); lazy-geladen in `index.tsx` |
| `components/` | Herbruikbare UI-bouwstenen, waaronder `Layout.tsx`, `RequireAuth` en `RequireAdmin`, en de `visualizations/`-map (canvas/SVG/D3) |
| `lib/` | Niet-visuele hulpcode: de axios-client (`api.ts`), authenticatie (`auth.ts`), foutmeldingen (`errorMessage.ts`) en meer |
| `content/docs/` | In-app referentiedocumentatie, die op `/docs` gerenderd wordt |

**Waar in de code:** deze indeling is beschreven in `AGENTS.md` (secties "Backend Guidelines", "Frontend & Integration") en zichtbaar via de mappen zelf.

## Hoe frontend en backend gescheiden én verbonden zijn

De frontend en backend zijn twee aparte processen (en aparte Docker-containers). Ze delen geen code en geen geheugen — ze praten uitsluitend via HTTP-JSON over de `/api`-interface. Die scheiding is bewust: ze bewaakt de *device boundary* en maakt duidelijk dat álle toegangscontrole aan de serverzijde gebeurt, niet in de browser.

De verbinding zelf loopt via drie mechanismen:

- **De gedeelde axios-client.** Eén axios-instantie met `baseURL = '/api'` (of de waarde van `VITE_API_BASE_URL`) verzorgt alle verkeer. Zo is er één plek waar tokens worden toegevoegd en fouten centraal worden afgehandeld.
  **Waar in de code:** `frontend/src/lib/api.ts` (`DEFAULT_API_BASE_URL = '/api'` en `apiBaseUrl`).

- **JWT in de header.** Na login bewaart de frontend een JWT (*JSON Web Token* — een ondertekend token dat de identiteit draagt). Bij elk verzoek voegt de request-interceptor de header `Authorization: Bearer <token>` toe, behalve op de login/signup-paden (`/auth/login`, `/auth/signup`). De backend valideert dit token in `get_current_user`. De details van login en tokens staan in [hoofdstuk 5](05-login-authenticatie.md) en het rollen/rechten-model in [hoofdstuk 2](02-beveiliging-rollen-rechten.md).
  **Waar in de code:** `frontend/src/lib/api.ts` (`shouldAttachStoredToken`) en `backend/app/dependencies.py` (`get_current_user`).

- **De `/api`-basis en de proxy.** In de browser is `/api` een *relatief* pad. In de productie-opstelling serveert de frontend-container de statische React-bundel en wordt `/api` naar de backend geleid. In ontwikkeling draait de Vite-dev-server een *proxy*: alle `/api`-verzoeken worden doorgestuurd naar de backend (standaard `http://localhost:8000`, in de dev-stack naar `http://backend:8000`).
  **Waar in de code:** `frontend/vite.config.mts` (de `proxy: { '/api': … }`-regel, met doel `apiProxyTarget` uit `VITE_DEV_API_PROXY_TARGET` of `BACKEND_URL`); dat doel wordt in de dev-stack gezet in `docker-compose.dev.yml` (`VITE_DEV_API_PROXY_TARGET: http://backend:8000`).

Aan de backendzijde regelt **CORS** (*Cross-Origin Resource Sharing* — de browserbeveiliging die bepaalt welke websites de API mogen aanroepen) welke oorsprongen toegelaten zijn. Dit is streng geconfigureerd: een breed origin-patroon dat niet volledig verankerd is (`^…$`) wordt door een validator geweigerd, juist omdat het samen met credentials een CORS-bypass zou vormen.
**Waar in de code:** `app.add_middleware(CORSMiddleware, …)` in `backend/app/main.py`, met de instellingen `cors_origins`/`cors_origin_regex` en de validator `validate_cors_origin_regex` in `backend/app/core/config.py`.

## Centrale configuratie en het opstarten van de app

### Waar de configuratie leeft

Alle instellingen komen binnen als *omgevingsvariabelen* (environment variables) en worden gebundeld in één `Settings`-object. Dat object is de enige plek waar configuratie wordt gelezen; de rest van de code importeert `settings`. De variabelen worden geladen uit een `.env`-bestand in de repo-root (of `.env.example` als sjabloon).

**Waar in de code:** `backend/app/core/config.py` — de klasse `Settings` en het singleton `settings = Settings()`. Het prefix `/api` is hier als enige bron van waarheid gedefinieerd (`API_PATH_PREFIX = "/api"`).

Deze configuratie doet meteen ook aan **veiligheidsafdwinging**. In de `model_validator` `validate_security_defaults` weigert de applicatie op te starten buiten ontwikkeling wanneer onveilige standaardgeheimen worden gebruikt: `SECRET_KEY` mag niet `secret`/`change-me` zijn en `POSTGRES_PASSWORD`/`ADMIN_PASSWORD` niet `admin`/`change-me`. Dat is een expliciete *fail-closed*-maatregel: liever niet starten dan onveilig starten. Ook wordt bijvoorbeeld het stilzwijgend laten vallen van audit-events in productie geweigerd (`AUDIT_LOG_DROP_ALLOWED=true` is alleen in development/test toegestaan).

### De containers — `docker-compose.yml`

De volledige stack draait via Docker Compose als vier services: `postgres`, `clickhouse`, `backend` en `frontend`. Belangrijke details:

- De databank-images zijn *digest-pinned* (vastgezet op een exacte hash met `@sha256:…`, bv. `postgres:16` en `clickhouse/clickhouse-server:25.3`) voor reproduceerbaarheid — belangrijk voor IVDR.
- De `backend` start pas als `postgres` én `clickhouse` "healthy" zijn (`depends_on … condition: service_healthy`); de `frontend` start pas als de `backend` healthy is. Zo krijgt de gebruiker nooit een half-opgestarte API te zien.
- ClickHouse heeft een `stop_grace_period: 5m`, omdat een te vroege `SIGKILL` variant-parts kan beschadigen (een gedocumenteerd incident van 2026-06-11).

De **ontwikkelvariant** `docker-compose.dev.yml` overschrijft dit met `APP_ENV: development`, live herladen van de backend (`uvicorn … --reload`), de Vite-dev-server met proxy, en gemounte broncode-mappen.

**Waar in de code:** `docker-compose.yml` (productiestijl) en `docker-compose.dev.yml` (ontwikkeling).

### De opstartroutine — `main.py` lifespan

FastAPI kent een *lifespan* (een functie die code draait bij het opstarten en afsluiten van de server). In CoGA doet die op hoofdlijnen het volgende, in volgorde:

1. Wachten tot Postgres bereikbaar is (`wait_for_postgres`).
2. Optioneel het Postgres-schema aanmaken en de admingebruiker seeden (`init_postgres_schema`, `init_postgres_admin_user`) — enkel wanneer de app als tabel-*owner* draait; gestuurd door de instelling `POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP`. Draait de app als de beperkte rol `coga_app`, dan worden de migraties out-of-band uitgevoerd en slaat de app deze stap over.
3. De achtergrond-workers voor audit-log en UI-events starten (`start_audit_log_worker`, `start_ui_event_worker`).
4. In één Postgres-sessie: de repeat-catalogus seeden, de referentie voor *Homo sapiens* GRCh38 verzekeren, de HPO-ontologie laden, ingebouwde tracks seeden en eventueel de gene-reference-refresh in de wachtrij zetten.
5. Wachten tot ClickHouse bereikbaar is (`wait_for_clickhouse`), het ClickHouse-schema initialiseren (`init_clickhouse_schema`) en de integrity-monitor starten.
6. De achtergrond-workers voor gene-reference-refresh en family-package-import starten.

Bij het afsluiten worden al deze workers netjes gestopt en de databankverbindingen gesloten.

**Waar in de code:** de `lifespan`-functie bovenaan `backend/app/main.py`. De bootstrap-functies leven in `backend/app/services/`, `backend/app/core/` en `backend/app/db_migrate.py` (`init_postgres_admin_user`).

## Veiligheid & traceerbaarheid op architectuurniveau

Omdat CoGA een gereguleerd IVD is, zijn veiligheid en traceerbaarheid geen bijzaak maar structureel in de architectuur verweven. De belangrijkste architectuur-brede waarborgen:

- **Serverzijdige toegangscontrole.** Alle autorisatie gebeurt in de backend via `get_current_user`/`get_current_admin_user` en project-gebonden RBAC. De browser bepaalt niets zelf.
  **Waar in de code:** `backend/app/dependencies.py`.
- **Auditing van elke actie.** Volgens `AGENTS.md` stromen alle betekenisvolle acties door een duurzame audit/telemetrie-pijplijn; het klinische auditspoor en de rapport-ondertekeningen zijn *append-only* en *hash-chained* (elke record verwijst cryptografisch naar de vorige, zodat manipulatie detecteerbaar is). Aan de ingangskant wordt elk verzoek gelogd.
  **Waar in de code:** de request-logging-middleware `log_request_response` in `backend/app/middleware/request_logging.py`, en de traceerbaarheidsservices in `backend/app/services/` (o.a. `clinical_audit_service.py`, `hash_chain.py`, `integrity_anchor_service.py`). Details volgen in [hoofdstuk 11](11-rapport-en-traceerbaarheid.md).
- **Security-headers en schema-afscherming.** De buitenste middleware stempelt hardening-headers op elk antwoord; buiten ontwikkeling worden `/docs`, `/redoc` en `/openapi.json` uitgeschakeld om schema-onthulling te voorkomen.
  **Waar in de code:** `security_headers_middleware` en `_docs_kwargs()` in `backend/app/main.py`.
- **Fail-closed configuratie.** De app weigert te starten met zwakke geheimen of onveilige productie-instellingen (zie hierboven).
  **Waar in de code:** `validate_security_defaults` in `backend/app/core/config.py`.
- **Provenance van de opslag.** De strikte scheiding "metadata in Postgres, varianten in ClickHouse" maakt duidelijk welke databank gezaghebbend is voor welk gegeven — cruciaal bij audit en foutopsporing.
  **Waar in de code:** vastgelegd in `docs/storage-architecture.md` en `docs/application-scheme.md`.

De diepere uitwerking van rollen, rechten en afscherming volgt in [hoofdstuk 2](02-beveiliging-rollen-rechten.md); de databankstructuren in [hoofdstuk 3](03-databankstructuren.md).

## Tech stack en versies

De platformversie staat in `VERSION`: **`0.1.0`**. (Let op: dit is de productversie. Het interne `app_version`-veld in `config.py` is een aparte build-identiteit met de permissieve standaardwaarde `0.0.0+unknown`, die bij het bouwen van de image via de `APP_VERSION`-build-arg wordt ingespoten.)

**Frontend** (uit `frontend/package.json`):

| Technologie | Versie | Rol |
| --- | --- | --- |
| React + React-DOM | ^19.2 | UI-framework |
| TypeScript | ^6.0 | Getypeerde JavaScript |
| Vite | ^8.1 | Build-tool en dev-server |
| Tailwind CSS | ^4.3 | Styling |
| react-router-dom | ^7.18 | Client-side routing (`BrowserRouter`/`Routes`/`Route` in `index.tsx`) |
| @tanstack/react-query | ^5.101 | Server-state en caching |
| axios | ^1.18 | HTTP-client naar `/api` |
| d3 | ^7.9 | Datavisualisaties |
| igv | ^3.8 | Ingebedde genoombrowser |
| vitest / @playwright/test | ^4.1 / ^1.61 | Unit-tests / end-to-end tests |

**Backend** (uit `backend/requirements.txt`):

| Technologie | Versie | Rol |
| --- | --- | --- |
| FastAPI | 0.139.0 | Web-/API-framework |
| Uvicorn | 0.49.0 | ASGI-server die de app draait |
| SQLAlchemy | 2.0.51 | ORM/async databanktoegang tot Postgres |
| asyncpg | 0.31.0 | Async Postgres-driver |
| clickhouse-connect | 1.4.1 | ClickHouse-client |
| Pydantic / pydantic-settings | 2.13.4 / 2.14.2 | Datavalidatie en instellingen |
| PyJWT | 2.13.0 | JWT-tokens |
| passlib | 1.7.4 | Wachtwoord-hashing (API-laag) |
| bcrypt | 3.2.0 | Onderliggend hash-algoritme voor passlib |
| cryptography | 49.0.0 | Onderliggende crypto (o.a. integrity anchors) |

**Waar in de code:** `VERSION`, `frontend/package.json` en `backend/requirements.txt`.

## De frontend-router in het kort

De client-side routing gebruikt `react-router-dom` met `BrowserRouter` en geneste `Routes`/`Route`-declaraties (geen `createBrowserRouter`). Alle pagina's zijn *lazy-geladen* (pas ingeladen wanneer nodig, wat de eerste laadtijd verkort). De boom is opgebouwd rond bewakers:

- Alles zit binnen `Layout` (de gedeelde schil).
- Publieke routes: `/login`, `/signup`.
- Onder `RequireAuth`: het dashboard, de familiewerkruimte (`/families/:familyId` en alle subviews zoals Small Variants, structurele varianten, rapport en QC), de familybuilder, de explorers (`/genes`, de Global Small Variant Explorer, de Clinical CNV Explorer), panels, HPO en docs.
- Onder `RequireAdmin` (geneste bewaker): alle `/admin/...`-routes, `/package-import` en `/projects`.

**Waar in de code:** `frontend/src/index.tsx` (de `<Routes>`-boom met `<RequireAuth />` en `<RequireAdmin />`), en `frontend/src/components/Layout.tsx` (de schil met `<Outlet />`). De bewakers zelf worden behandeld in [hoofdstuk 2](02-beveiliging-rollen-rechten.md).

## Belangrijkste bestanden

| Bestand | Rol |
| --- | --- |
| `README.md` | Overzicht, capaciteiten, stack, quick-start en omgevingsvariabelen |
| `AGENTS.md` | Repository-overzicht met backend/frontend-richtlijnen en veiligheidsprincipes |
| `VERSION` | De platformversie (`0.1.0`) |
| `docs/application-scheme.md` | Flow Summary, Main Code Areas en de Storage Boundary (gezaghebbendheid) |
| `docs/storage-architecture.md` | Verantwoordelijkheden van Postgres vs. ClickHouse en de runtime-flow |
| `backend/app/main.py` | Bouwt de FastAPI-app: routers, CORS, middleware en de `lifespan`-opstartroutine |
| `backend/app/routers/__init__.py` | Bundelt alle routers in `all_routers` (de volledige API-oppervlakte) |
| `backend/app/dependencies.py` | Authenticatie/autorisatie: `create_access_token`, `get_current_user`, `get_current_admin_user` |
| `backend/app/core/config.py` | Centrale instellingen (`Settings`), `/api`-prefix en fail-closed veiligheidsvalidatie |
| `frontend/src/index.tsx` | Frontend-startpunt en de volledige route-boom met auth/admin-bewakers |
| `frontend/src/lib/api.ts` | De gedeelde axios-client: `/api`-basis, JWT-injectie en 401-afhandeling |
| `frontend/src/components/Layout.tsx` | De visuele schil rond alle pagina's |
| `frontend/vite.config.mts` | De dev-proxy die `/api` naar de backend doorstuurt |
| `docker-compose.yml` / `docker-compose.dev.yml` | Orchestratie van de vier services (productie- en ontwikkelvariant) |
| `frontend/package.json` / `backend/requirements.txt` | De tech stack met exacte versies |
