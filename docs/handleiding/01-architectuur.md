# 1. Algemene architectuur & structuur

Dit hoofdstuk is de kaart voor de rest van de handleiding. Het beschrijft de drie lagen van CoGA (frontend, backend en twee databanken), volgt één verzoek van een klik in de browser tot de data en terug, en wijst aan waar de mappen, de configuratie en de belangrijkste veiligheidsmaatregelen zitten. De architectuur staat in het Engels in `docs/application-scheme.md` en in de technical file (`docs/regulatory/TF-02-device-description.md`); dit hoofdstuk vat ze samen.

## CoGA in het kort

CoGA (*Comprehensive Genomic Analysis*) is een platform voor variantinterpretatie, genoomvisualisatie en klinische review op familieniveau. Het draait als **in-house IVD onder IVDR Artikel 5(5)** bij CMGG, een ISO 15189-geaccrediteerd labo. *IVD* staat voor in-vitrodiagnostiek; de *IVDR* is de Europese verordening daarvoor. Het gereguleerde deel (de "device boundary") loopt van het **geannoteerde VCF-bestand** tot het **ondertekende klinische rapport**. De data in het systeem is synthetisch.

## De drie lagen

| Laag | Technologie | Rol | Waar in de code |
| --- | --- | --- | --- |
| Frontend | React en TypeScript (gebouwd met Vite, opgemaakt met Tailwind) | De gebruikersinterface: login, dashboard, familiewerkruimte, filterpagina's, visualisaties | `frontend/src/` |
| Backend | FastAPI (Python) | De API-server: authenticatie, toegangscontrole, klinische logica, databankvragen | `backend/app/` |
| Databanken | Postgres en ClickHouse | Metadata en reviewtoestand (Postgres) naast de grote variantopslag (ClickHouse) | `backend/db/schema/` en de ClickHouse-diensten in `backend/app/services/` |

Elke laag heeft één taak. Zo is duidelijk waar toegangscontrole, validatie en opslag gebeuren, wat audit en foutopsporing eenvoudiger maakt.

### Waarom twee databanken?

- **Postgres** is een relationele databank en de *bron van waarheid* voor metadata en toestand: gebruikers en projecttoegang, families, samples en stamboom, reviews en classificaties, panels, referentiedata en het auditspoor. Die gegevens zijn relatief klein, sterk gestructureerd en worden vaak gewijzigd.
- **ClickHouse** is een kolomgeoriënteerde databank voor zeer grote datasets. Ze bewaart de variantrijen (small variants en structurele varianten), de genotypes per sample en de grote interval-tracks (dekking, CNV-segmenten, APCAD, haplotypes). Eén familie kan miljoenen rijen hebben.

Bij elk verzoek beslist Postgres eerst wie wat mag zien. Daarna haalt de backend de varianten uit ClickHouse en koppelt er de reviewtoestand uit Postgres aan, bijvoorbeeld een tag of een ACMG-klasse. Hoofdstuk 3 beschrijft de tabellen.

**Waar in de code:** `backend/app/core/postgres.py` en `backend/app/core/clickhouse.py` (de verbindingen). De taakverdeling staat in `docs/application-scheme.md`, sectie *Storage boundary*: "Postgres is authoritative for metadata and state. ClickHouse is authoritative for variant payloads."

## Van klik tot data: de weg van één verzoek

Voorbeeld: een analist opent de small variants van een familie.

1. **Klik in de browser.** De pagina (bv. `frontend/src/pages/families/FamilySmallVariantsPage.tsx`) vraagt data op via de gedeelde API-client.
2. **De API-client verstuurt het verzoek.** Vrijwel alle verzoeken gaan via één *axios*-instantie (axios is een JavaScript-bibliotheek voor HTTP-verzoeken) met het basisadres `/api`. Een *interceptor* (code die elk uitgaand verzoek onderschept) voegt het JWT-token toe als `Authorization: Bearer <token>`, behalve bij inloggen en registreren. Alleen de ingebedde IGV-browser, die zijn alignments en tracks zelf ophaalt, en de laatste UI-events bij het verlaten van een pagina sturen het token zelf mee. Een JWT (*JSON Web Token*) is een ondertekend toegangsbewijs; [hoofdstuk 5](05-login-authenticatie.md) legt het uit.
3. **Het verzoek bereikt de backend.** In ontwikkeling stuurt de Vite-ontwikkelserver `/api` door naar de backend; in de Docker-opstelling doet de frontendcontainer dat (`frontend/server.mjs`). Alle routers hangen onder `/api`, behalve `GET /metrics` voor de monitoring, dat er bewust buiten ligt (hoofdstuk 7). Eerst passeert het verzoek een reeks *middleware* (tussenlagen die elk verzoek en antwoord bewerken): client-IP-bepaling achter proxies (`TRUSTED_PROXY_HOPS`), security-headers, trailing-slash-normalisatie en request-logging/audit. [Hoofdstuk 7](07-backend-routers-en-services.md) beschrijft de volgorde.
4. **De router controleert de toegang.** Elk beschermd endpoint vraagt de *dependency* `get_current_user` (een functie die FastAPI vóór het endpoint uitvoert). Die controleert het token en laadt de gebruiker vers uit Postgres. De toegang is **projectgebonden** (project-scoped RBAC, *Role-Based Access Control*); [hoofdstuk 2](02-beveiliging-rollen-rechten.md) legt uit hoe.
5. **Een service doet het eigenlijke werk.** Routers blijven dun; de klinische logica zit in `backend/app/services/`, voor familievarianten bijvoorbeeld in `clickhouse_family_variants.py`.
6. **De service bevraagt de juiste databank.** Metadata komt uit Postgres (via SQLAlchemy, asynchroon), varianten uit ClickHouse (via een directe client). Alle waarden gaan als parameter mee, nooit als tekst in de query (hoofdstuk 7).
7. **Het antwoord gaat terug.** FastAPI zet het resultaat om naar JSON, de middleware voegt de security-headers toe en de browser toont het. Krijgt de client een `401` (niet aangemeld), dan wist hij de sessie en keert hij terug naar `/login`.

**Waar in de code:** `frontend/src/lib/api.ts` (API-client en interceptors), `frontend/vite.config.mts` (ontwikkelproxy), `backend/app/main.py` (app, middleware en routers), `backend/app/dependencies.py` (`get_current_user`). Dezelfde weg staat in het Engels in `docs/application-scheme.md`, sectie *Runtime flow*.

Frontend en backend zijn aparte processen die alleen via HTTP en JSON met elkaar praten. Die scheiding is bewust: álle toegangscontrole gebeurt op de server, niet in de browser.

**CORS** (*Cross-Origin Resource Sharing*: welke websites de API mogen aanroepen) staat streng ingesteld. Een origin-patroon moet volledig verankerd zijn (beginnen met `^` en eindigen met `$`); anders weigert de configuratie het, omdat een te breed patroon samen met meegestuurde credentials een omweg zou openen. Buiten ontwikkeling start de backend niet zolang `CORS_ORIGINS` of `CORS_ORIGIN_REGEX` een lokale origin (`localhost`, `127.0.0.1`, `::1`, `0.0.0.0`) of eender welke site toelaat: de ontwikkelinstellingen laten localhost toe, en een pagina op de eigen machine van de gebruiker zou dan als die aangemelde gebruiker de API kunnen aanroepen. Een deployment bedient de interface vanaf zijn eigen origin en heeft geen cross-origin toegang nodig; Terraform zet de eigen origin en een leeg patroon.

**Waar in de code:** de `CORSMiddleware` in `backend/app/main.py`; in `backend/app/core/config.py` `validate_cors_origin_regex` (de verankering) en `_cross_origin_exposures`, dat `validate_security_defaults` buiten ontwikkeling aanroept.

## De mappen op hoofdlijnen

De repository bevat twee applicatiemappen (`backend/`, `frontend/`), de documentatie (`docs/`), de infrastructuur-als-code voor Google Cloud (`terraform/`) en de Docker Compose-bestanden.

| Map of bestand (backend) | Rol |
| --- | --- |
| `backend/app/core/` | Verbindingen en runtime: instellingen (`config.py`), Postgres, ClickHouse, Azure, logging, objectopslag |
| `backend/app/routers/` | De API: één bestand per domein, gebundeld in `routers/__init__.py` |
| `backend/app/services/` | De klinische en bedrijfslogica, inclusief de traceerbaarheidsstack |
| `backend/app/middleware/` | Tussenlagen voor client-IP, security-headers en request-logging |
| `backend/app/main.py` | Bouwt de app: routers, CORS, middleware en de opstartroutine |
| `backend/app/db_migrate.py` | Schema-migratie en eerste beheerder, ook als losse stap uit te voeren |
| `backend/db/schema/` | De vijf Postgres-baselines (`01_access` t/m `05_grants`) en de ClickHouse-bootstrap |

| Map (frontend) | Rol |
| --- | --- |
| `frontend/src/pages/` | De schermen, per domein gegroepeerd |
| `frontend/src/components/` | Herbruikbare bouwstenen, waaronder de routebewakers en `visualizations/` |
| `frontend/src/lib/` | Niet-visuele hulpcode: API-client, sessie, genotypes, ACMG-logica |
| `frontend/src/content/docs/` | De in-app documentatie (gebruikersgids en referentiedocs), getoond op `/docs` |

De routering gebruikt `react-router`. Alleen `/login` en `/signup` zijn publiek. Alle andere schermen zitten achter de bewaker `RequireAuth`, en de beheerschermen (o.a. `/admin/...`, `/package-import` en `/projects`) daarbovenop achter `RequireAdmin`. Die bewakers zijn gebruiksgemak, geen beveiliging (hoofdstuk 2).

Er is geen navigatiebalk: de hoofdonderdelen (*Projects*, *Variant explorer*, *Gene explorer*, *CNV explorer*, *Panels*, voor een beheerder *Admin*, en *User guide*) staan in een menu achter een pijltje rechts in de kopbalk, en onder de kopbalk volgt een kruimelpad de route.

**Waar in de code:** de routeboom in `frontend/src/index.tsx`; het menu in `frontend/src/components/HeaderMenu.tsx` en het kruimelpad in `Breadcrumbs.tsx`.

## Configuratie

Alle instellingen komen binnen als omgevingsvariabelen en worden gebundeld in één `Settings`-object; de rest van de code leest dat object. `.env.example` somt elke instelling op met haar standaardwaarde. De configuratie is ook een veiligheidsmaatregel: buiten ontwikkeling weigert de backend te starten met zwakke of ontbrekende geheimen. De volledige regel staat in [hoofdstuk 2](02-beveiliging-rollen-rechten.md#weigering-te-starten-met-zwakke-geheimen).

**Waar in de code:** `backend/app/core/config.py` (`Settings` en `validate_security_defaults`). Het API-voorvoegsel `/api` staat daar ook, als `API_PATH_PREFIX`.

## Containers en opstarten

De stack draait via Docker Compose als vier diensten: `postgres`, `clickhouse`, `backend` en `frontend`. De databank-images zijn op een exacte digest vastgezet, voor reproduceerbaarheid. De backend start pas als beide databanken gezond zijn, de frontend pas als de backend gezond is. ClickHouse krijgt een ruime afsluittermijn, omdat een te vroeg afgebroken schrijfactie dataonderdelen kan beschadigen. De ontwikkelvariant `docker-compose.dev.yml` zet `APP_ENV=development`, herlaadt gewijzigde code meteen en gebruikt de Vite-ontwikkelserver.

Wat de backend bij het opstarten doet (schema, eerste beheerder, referentiedata, achtergrondtaken), staat in [hoofdstuk 4](04-deployment-en-seeding.md).

**Waar in de code:** `docker-compose.yml`, `docker-compose.dev.yml` en de `lifespan`-functie in `backend/app/main.py`.

## Veiligheid en traceerbaarheid in de architectuur

- **Toegangscontrole op de server.** Alle autorisatie gebeurt in de backend (`get_current_user`, `get_current_admin_user` en de projectscoping). De browser beslist niets.
- **Elk verzoek laat een spoor na.** De request-logging-middleware schrijft elk HTTP-verzoek naar de append-only tabel `audit_log_events`. Het klinische auditspoor en de rapportondertekeningen zijn daarnaast append-only én hash-geketend: elke rij verwijst cryptografisch naar de vorige, zodat wijzigen of wissen zichtbaar wordt ([hoofdstuk 11](11-rapport-en-traceerbaarheid.md)).
- **Geen schema-onthulling.** Buiten ontwikkeling staan `/docs`, `/redoc` en `/openapi.json` uit.
- **Fail-closed configuratie.** De app start niet met zwakke geheimen of onveilige productie-instellingen (hoofdstuk 2).
- **Een duidelijke bron van waarheid.** Metadata staat in Postgres, varianten in ClickHouse. Bij audit en foutopsporing is dus duidelijk welke databank voor welk gegeven gezaghebbend is.

**Waar in de code:** `backend/app/dependencies.py`, `backend/app/middleware/`, `_docs_kwargs` in `backend/app/main.py`, en de traceerbaarheidsservices in `backend/app/services/` (o.a. `clinical_audit_service.py`, `hash_chain.py` en `integrity_anchor_service.py`).

## Technologie

- **Frontend:** React, TypeScript, Vite, Tailwind CSS, react-router, TanStack Query (servertoestand en caching), axios, D3 (visualisaties) en igv (de ingebedde IGV-genoombrowser); tests met Vitest en Playwright.
- **Backend:** FastAPI met Uvicorn, SQLAlchemy (asynchroon) met asyncpg voor Postgres, clickhouse-connect voor ClickHouse, Pydantic (validatie en instellingen), PyJWT (tokens), bcrypt (wachtwoorden) en cryptography (o.a. de handtekening van de integriteitsankers).

De exacte versies staan in `frontend/package-lock.json` en `backend/requirements.txt`; het SOUP-register (`docs/regulatory/TF-08-soup-register.md`) houdt ze bij voor het technisch dossier. De productversie staat in `VERSION`. Het veld `app_version` in de backend is de build-identiteit: die wordt bij het bouwen van de image ingevuld en in elk ondertekend rapport bevroren (hoofdstuk 11).

## Belangrijkste bestanden

| Bestand | Rol |
| --- | --- |
| `docs/application-scheme.md` | Architectuur en opslaggrenzen (Engels) |
| `backend/app/main.py` | Bouwt de app: routers, CORS, middleware, opstartroutine |
| `backend/app/routers/__init__.py` | Bundelt alle routers |
| `backend/app/dependencies.py` | `get_current_user`, `get_current_admin_user` en tokenuitgifte |
| `backend/app/core/config.py` | Alle instellingen en de fail-closed controles |
| `frontend/src/index.tsx` | Frontend-startpunt en routeboom met bewakers |
| `frontend/src/lib/api.ts` | De gedeelde API-client: `/api`-basis, token en 401-afhandeling |
| `docker-compose.yml` / `docker-compose.dev.yml` | De vier diensten (Docker-opstelling en ontwikkeling) |
