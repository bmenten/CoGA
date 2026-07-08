# 2. Gebruikersrollen, machtigingen & afscherming

Dit hoofdstuk behandelt hoe CoGA bepaalt *wie* iets mag zien of doen. Het beschrijft het volledige rechtenmodel: welke rollen bestaan en waar ze in de databank en in de tokens vastliggen, hoe toegang tot data per *project* wordt afgeschermd (project-scoped RBAC), hoe de backend dit onverbiddelijk afdwingt met FastAPI-*dependencies* (herbruikbare stukjes code die vóór elk endpoint draaien), en waarom de frontend-bewaking uitdrukkelijk *geen* echte poort is maar enkel gebruikersgemak. Daarna komt de defensieve onderbouw aan bod: een least-privilege databankrol, rate limiting tegen brute-force, security-headers en CORS, de weigering om met zwakke geheimen te starten, upload-bescherming en HTML-sanitatie. Telkens wordt exact aangewezen in welk bestand het is afgedwongen.

Een paar begrippen vooraf: **RBAC** = Role-Based Access Control, toegang op basis van rollen. Een **JWT** (JSON Web Token) is een ondertekend "toegangsbewijs" dat de browser bij elk verzoek meestuurt. Een **dependency** in FastAPI (het Python-webframework waarmee de backend is gebouwd) is een functie die automatisch vóór een endpoint draait en het verzoek kan blokkeren. **Endpoint** = één API-adres (bijv. `GET /api/families/{family_id}`).

## Welke rollen bestaan er

CoGA kent exact drie rollen. Ze liggen als *databank-constraint* vast (een regel die de databank zelf afdwingt), zodat een ongeldige rol simpelweg niet kan bestaan:

| Rol | Betekenis | Wat mag deze rol |
| --- | --- | --- |
| `viewer` | Gewone (klinische) gebruiker | Enkel de families/samples/varianten van de **projecten waartoe hij/zij behoort**; kan taggen/reviewen binnen die scope |
| `admin` | Beheerder | Alles wat een viewer mag, plus alle scoping-*bypass* en alle beheeracties (projecttoewijzing, verwijderen, referentiedata, audit-logs) |
| `superuser` | Beheerder (gelijkgesteld) | Idem als `admin` in de generieke admin-poort `get_current_admin_user` |

**Waar in de code:** de toegestane rollen staan als CHECK-constraint in `backend/db/schema/postgres/001_metadata.sql` (`role TEXT NOT NULL CHECK (role IN ('admin', 'superuser', 'viewer'))`, in de `users`-tabel) en worden in `backend/db/schema/postgres/016_superuser_role.sql` herbevestigd (die migratie dropt en herzet de constraint met dezelfde drie waarden). In de backend zijn `admin` en `superuser` samengevoegd tot één begrip "beheerder" via de constante `ADMIN_ROLES = {"admin", "superuser"}` in `backend/app/dependencies.py` en in `backend/app/services/metadata_service.py` (helper `_is_admin_user`).

Er is geen aparte rechten-tabel per gebruiker: het onderscheid "mag deze data zien" volgt volledig uit **projectlidmaatschap**. Dat lidmaatschap is de koppeltabel `project_users` (kolommen `project_id`, `user_id`) in `001_metadata.sql`. Een family hangt via `family_projects` aan een of meer projecten, een sample via `sample_projects`. Een `viewer` ziet dus precies de families waarvan minstens één project ook in zijn `project_users`-rijen staat.

**Waar in de code:** de rol staat óók in het JWT-token — bij het inloggen zet `_authenticate_and_issue_token` in `backend/app/routers/auth.py` de rol in het `Token`-antwoord (`Token(access_token=..., role=user["role"])`), en `create_access_token` (in `dependencies.py`) codeert de gebruiker — het e-mailadres, als veld `sub` — in het ondertekende token. Belangrijk: de **projectenlijst zit niet in het token**. Bij elk verzoek wordt de actuele gebruiker vers uit Postgres geladen door `get_current_user_by_email` (in `metadata_service.py`), inclusief zijn `metadata_project_ids` (de projecten waartoe hij behoort). Zo werkt intrekken onmiddellijk: een gedeactiveerde of uit een project verwijderde gebruiker verliest toegang bij het eerstvolgende verzoek, zonder op tokenverval te wachten.

## Project-scoped RBAC: hoe elk data-verzoek wordt ingeperkt

De kern van het model is dat *elk* verzoek naar een family, sample of variant door één afschermings-checkpoint gaat. Er zijn twee complementaire mechanismen, afhankelijk van of het om een lijst of om één specifiek object gaat.

**Lijst-endpoints filteren in SQL, niet achteraf.** Wanneer een viewer de families opvraagt, wordt de projectfilter direct in de databank-query gelegd. `list_family_records` (in `metadata_service.py`) roept `_fetch_family_rows` aan met `metadata_project_ids = None` voor een admin (geen filter) of met de projectlijst van de viewer. In dat laatste geval krijgt de query een `EXISTS`-clausule op `family_projects`:

```sql
EXISTS (
    SELECT 1 FROM family_projects afp
    WHERE afp.family_id = f.id
      AND afp.project_id IN :metadata_project_ids
)
```

Families buiten de scope worden dus nooit uit de databank gehaald — ze kunnen niet "per ongeluk" lekken door een vergeten filter in de applicatiecode. Merk ook op: als de projectlijst leeg is, geeft `_fetch_family_rows` meteen een lege lijst terug (een viewer zonder projecten ziet niets).

**Object-endpoints controleren bij het ophalen.** Vraagt iemand één specifieke family op via id, dan draait `get_accessible_family_mapping` (in `metadata_service.py`). Die haalt de family op en roept meteen `_ensure_user_can_access_metadata_projects(project_ids, user)` aan. Die helper laat admins door en gooit voor een viewer een `HTTP 403` als er géén overlap is tussen de projecten van de family en de projecten van de gebruiker:

```python
def _ensure_user_can_access_metadata_projects(project_ids, user):
    if _is_admin_user(user):
        return
    if not set(project_ids).intersection(_user_metadata_project_ids(user)):
        raise HTTPException(status_code=403, detail="Not authorized")
```

Deze twee lagen komen samen in `build_family_metadata_context` (in `backend/app/services/family_metadata_context.py`), het gedeelde "toegangspoortje" dat vrijwel elke family-gebonden view (varianten, tracks, rapport) eerst passeert: het roept `get_accessible_family_mapping` aan en reduceert vervolgens de zichtbare projecten met `_visible_project_ids` tot enkel wat de gebruiker mag zien. De sample-tegenhanger hiervan is `build_sample_metadata_context` / `get_accessible_sample_mapping`.

**Waar in de code:** `backend/app/services/metadata_service.py` (functies `get_accessible_family_mapping`, `_ensure_user_can_access_metadata_projects`, `_visible_metadata_project_ids`, `list_family_records`, `_fetch_family_rows`) en `backend/app/services/family_metadata_context.py` (`build_family_metadata_context`, `build_sample_metadata_context`, `_visible_project_ids`).

> Let op: het bestand `backend/app/services/data_scope.py` gaat *niet* over toegangsscoping (ondanks de naam) maar over chromosoom-normalisatie (`normalize_chromosome`, `is_primary_chromosome`). De echte data-scoping zit in `metadata_service.py` en `family_metadata_context.py` zoals hierboven.

## Backend-afdwinging: de echte poort

De backend is de enige plek waar autorisatie echt wordt afgedwongen. Dat gebeurt via twee FastAPI-dependencies in `backend/app/dependencies.py`:

- **`get_current_user`** — valideert het JWT (of, bij Azure-AD-configuratie, het Azure-token), laadt de gebruiker vers uit Postgres via `get_current_user_by_email`, en weigert (`HTTP 401`) als het token ongeldig is, de gebruiker niet bestaat, of `is_active` False is. Bij succes hangt het de gebruiker aan `request.state.current_user` (zodat de audit-middleware weet wie het verzoek deed). Omdat `is_active` bij elk verzoek opnieuw wordt gecontroleerd, werkt onmiddellijke deactivatie.
- **`get_current_admin_user`** — bouwt voort op `get_current_user` en gooit `HTTP 403 "Admin access required"` als de rol niet in `ADMIN_ROLES` zit. Dit is de poort voor álle beheer- en destructieve acties.

Een concreet, project-gescoped endpoint (leesbaar door een gewone viewer), letterlijk uit `backend/app/routers/families.py`:

```python
@router.get("/{family_id}", response_model=FamilyOut)
async def get_family(
    family_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),   # <- authenticatie
) -> FamilyOut:
    return await get_family_for_user(session, family_id, user)  # <- scoping binnenin
```

**Waar in de code:** `backend/app/routers/families.py` (`get_family`, `list_families`). De authenticatie zit in de `Depends(get_current_user)`; de *autorisatie* (scoping) zit onlosmakelijk in de service-laag: `get_family_for_user` (in `family_service.py`) roept `get_family_record` aan, en die roept op zijn beurt `get_accessible_family_mapping` aan. De viewer geeft dus wel een `family_id` mee, maar krijgt `403`/`404` als die family niet in zijn projecten zit — een klassieke IDOR-afscherming (Insecure Direct Object Reference: het raden van andermans id).

Een admin-endpoint gebruikt in plaats daarvan `get_current_admin_user`. Zo vereist bijvoorbeeld het toewijzen van een family aan projecten (`PUT /api/admin/families/{family_id}/projects` → `update_family_projects`) en het verwijderen van een family (`delete_family`) beheerdersrechten. In `backend/app/routers/admin.py` draagt élk endpoint `Depends(get_current_admin_user)`; de destructieve endpoints (`delete_family`, `delete_sample`, `delete_family_data`, `delete_sample_data`, referentiedata- en integrity-endpoints) staan zo consequent achter de admin-poort.

**Waar in de code:** `backend/app/routers/admin.py` (alle endpoints), `backend/app/routers/projects.py` (`create_project`, `update_project`, `delete_project` zijn admin-only; enkel `list_projects` gebruikt `get_current_user` en scoopt binnenin via `list_project_dashboards`, dat voor een viewer alleen zijn eigen `metadata_project_ids` opvraagt).

Twee nuances die een auditor moet kennen:

1. In `backend/app/routers/auth.py` gebruikt `list_users` (`GET /api/auth/users`) een striktere controle `current.role != "admin"` — dus enkel de letterlijke `admin`-rol, niet `superuser`. De mutatie ernaast (`update_user`, `PATCH /api/auth/users/{user_id}`) gebruikt wél de gebruikelijke `get_current_admin_user`. Dit is een bewust smallere poort voor de accountlijst; het is geen zwakte maar wel een afwijking van het `ADMIN_ROLES`-patroon.
2. Een aparte, *minimale* gebruikerslijst (`GET /api/users`, enkel naam/e-mail, in `backend/app/routers/lookups.py` via `get_assignable_users`) is voor élke ingelogde gebruiker leesbaar — dit voedt de reviewer-kiezer op de family-pagina. Dit is in `docs/security-posture.md` §1 uitdrukkelijk als **aanvaard restrisico** gedocumenteerd ("Accepted residual — staff roster"): CoGA draait als lokale installatie in één lab waar alle gebruikers collega's zijn.

## Frontend-guards: gemak, geen slot

De React-frontend heeft eigen "poortwachters", maar die zijn puur voor de gebruikerservaring en *defense-in-depth* (een extra laag). Ze zijn geen echte beveiliging: de browser houdt niets tegen wat de backend niet ook zelf weigert.

- **`RequireAuth`** — als er geen token in de opslag zit, stuurt het door naar `/login?next=...`. Anders toont het de onderliggende route (`<Outlet/>`).
- **`RequireAdmin`** — stuurt niet-ingelogden naar `/login`, en ingelogde niet-admins naar `/dashboard`. Enkel admins zien de admin-routes.
- **`SessionRedirect`** — kiest een bestemming afhankelijk van "ingelogd of niet" (via de props `authenticatedTo` / `unauthenticatedTo`, bijv. root → dashboard of login).

**Waar in de code:** `frontend/src/components/RequireAuth.tsx`, `RequireAdmin.tsx`, `SessionRedirect.tsx`. De onderliggende checks staan in `frontend/src/lib/auth.ts`: `isAuthenticated()` kijkt enkel of er een token bestaat (`Boolean(getAuthToken())`), en `isAdmin()` leest de **in de browser opgeslagen rol** (`admin` of `superuser`) uit `localStorage`.

Waarom dit géén poort is: `isAdmin()` vertrouwt op een waarde in `localStorage` die een gebruiker technisch zelf kan aanpassen. Dat zou hem hooguit de admin-*schermen* tonen — maar élk data- of actieverzoek gaat alsnog naar de backend, die `get_current_admin_user`/scoping opnieuw en gezaghebbend uitvoert. De frontend-guard voorkomt dus enkel een lelijke of verwarrende UI, niet ongeoorloofde toegang.

De frontend versterkt dit nog met twee gewoontes in `frontend/src/lib/api.ts`: een *request-interceptor* hangt automatisch de `Authorization: Bearer <token>`-header aan elk verzoek (behalve `/auth/login` en `/auth/signup`), en een *response-interceptor* wist bij een `HTTP 401` de sessie en stuurt terug naar `/login`. Zo leidt een verlopen of ingetrokken token meteen tot uitloggen.

## Databank least-privilege: de runtime-rol `coga_app`

Zelfs áls de applicatie ooit gecompromitteerd raakt, mag ze niet in staat zijn om de *append-only* bewijstabellen (audit-log, klinische audit, ondertekende rapporten) te herschrijven. "Append-only" betekent: er mag enkel bijgeschreven worden, nooit gewijzigd of verwijderd. Daarom bestaat er een aparte, sterk beperkte databankrol.

**Waar in de code:** `backend/db/schema/postgres/040_app_runtime_role_privileges.sql`. Deze migratie maakt de rol `coga_app` aan met alleen de nodige rechten: `SELECT, INSERT, UPDATE, DELETE` op de gewone tabellen, maar voor de drie append-only tabellen `audit_log_events`, `clinical_audit_events` en `report_signouts` worden `UPDATE, DELETE, TRUNCATE` expliciet ingetrokken (`REVOKE`). Als niet-eigenaar kan `coga_app` bovendien geen `ALTER TABLE ... DISABLE TRIGGER` doen en geen `SET session_replication_role` (superuser-only). Gevolg: de runtime kan wél nieuwe auditregels toevoegen, maar geen bestaande audit-rij of ondertekend rapport wijzigen, verwijderen of "her-ketenen".

De rol wordt *in fallback-modus* geleverd: hij bestaat als `NOLOGIN` en de applicatie draait voorlopig nog als de tabel-eigenaar. De echte omschakeling (de "DSN-flip", waarbij DSN staat voor de databank-connectiestring) is een louter operationele configuratiewijziging, geen codewijziging: `POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP=false` zetten (zodat de app geen eigenaar-only DDL — tabellen aanmaken/wijzigen — meer probeert), de schema-migraties apart draaien als eigenaar (`python -m backend.app.db_migrate`), en de app laten inloggen als `coga_app`.

**Waar in de code:** de stap-voor-stap-procedure, de verificatie en de rollback staan in `docs/db-runtime-role-runbook.md`; de bijhorende instelling in `backend/app/core/config.py` (`postgres_run_schema_migrations_on_startup`). Belangrijk voor toekomstige schema's: de `ALTER DEFAULT PRIVILEGES` uit 040 geeft elke *nieuwe* tabel automatisch volledige CRUD aan `coga_app`; daarom moet elke nieuwe append-only tabel de `REVOKE UPDATE, DELETE, TRUNCATE ... FROM coga_app` herhalen (dit staat ook als expliciete waarschuwing in het bestand zelf).

## Overige hardening

### Rate limiting & login-lockout

Brute-force op wachtwoorden en accountopsomming worden geremd met een oplopende (exponentiële) backoff, opgeslagen in Postgres.

**Waar in de code:** `backend/app/services/auth_rate_limit_pg.py`, met de tabel `auth_login_attempts` uit `backend/db/schema/postgres/010_auth_login_attempts.sql`. Elke mislukte login wordt geteld per scope (`email` én `remote_ip`); vanaf een drempel (`login_rate_limit_threshold`, standaard 5) volgt een lockout die exponentieel oploopt tot een maximum (`_backoff_seconds`). Login zelf zit in `backend/app/routers/auth.py`: `_authenticate_and_issue_token` weigert bij een actieve throttle met `HTTP 429 + Retry-After`, en gebruikt een *dummy* bcrypt-verificatie (`_DUMMY_LOGIN_PASSWORD_HASH`) voor onbestaande accounts zodat de responstijd niet verraadt welke e-mails bestaan (bescherming tegen account-enumeratie via timing). Signup wordt apart per bron-IP gethrottled (`get_signup_throttle_state` / `record_signup_attempt`); ook de `signup`-flow geeft bewust een identieke bevestiging voor nieuwe én bestaande e-mails, zodat het endpoint geen accounts kan opsommen.

### Security-headers, CORS en geheimen

**Security-headers.** Elk API-antwoord krijgt strikte hardening-headers, waaronder een maximaal-strikte Content-Security-Policy (`default-src 'none'; frame-ancestors 'none'`), `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` en Cross-Origin-Opener/Resource-Policy. HSTS is opt-in (enkel waar TLS ervoor staat).
**Waar in de code:** `backend/app/middleware/security_headers.py` (`security_headers_middleware`, `_STATIC_HEADERS`).

**CORS** (Cross-Origin Resource Sharing — welke andere webdomeinen de API mogen aanroepen). De toegestane origins en de origin-regex staan in `backend/app/core/config.py`. Een `field_validator` (`validate_cors_origin_regex`) weigert een niet-verankerde regex: omdat CoGA credentials meestuurt, zou een te breed patroon een CORS-bypass zijn, dus de regex moet met `^` beginnen en op `$` eindigen.

**Weigering te starten met zwakke geheimen.** De `model_validator` `validate_security_defaults` in `config.py` weigert buiten dev/test op te starten als `SECRET_KEY`, `POSTGRES_PASSWORD` of `ADMIN_PASSWORD` nog placeholder-waarden bevatten (`change-me`/`secret` voor het secret; `change-me`/`admin` voor de wachtwoorden), en verbiedt daar ook `AUDIT_LOG_DROP_ALLOWED=true`. Dit voorkomt een productiestart met fabrieksgeheimen of met stilzwijgend weggegooide audit-events.
**Waar in de code:** `backend/app/core/config.py` (`validate_security_defaults`, constanten `_INSECURE_SECRET_VALUES` / `_INSECURE_PASSWORD_VALUES`).

### Upload-veiligheid

Admin-uploads (SV, referentie, BED, repeat-expansion, PED) worden begrensd gelezen én begrensd gedecomprimeerd, zodat een kleine maar sterk samendrukbare `.gz` ("decompressie-bom") het werkgeheugen niet kan uitputten (DoS — Denial of Service). Overschrijding levert `HTTP 413`.
**Waar in de code:** `backend/app/services/upload_safety.py` (`decode_upload_text`, `_read_upload_bounded`, `_gunzip_bounded`, `read_path_text_bounded`); de caps `MAX_UPLOAD_BYTES` / `MAX_DECOMPRESSED_UPLOAD_BYTES` in `config.py`. De functie behandelt bovendien correct de meervoudige gzip-blokken van BGZF-`.vcf.gz`-bestanden.

### HTML-sanitatie (stored-XSS)

De klinische-CNV-knowledgebase importeert kleine `details_html`-fragmenten die de UI met `dangerouslySetInnerHTML` toont — dat is een opgeslagen-XSS-risico (Cross-Site Scripting: kwaadaardige HTML/JavaScript die in de databank staat en later bij een andere gebruiker uitvoert). Een strikte allowlist-sanitizer reduceert de HTML tot een veilige set opmaaktags en weigert elk element/attribuut/URL-schema dat niet expliciet is toegelaten (`script`, `img`, `onerror`, `javascript:`, `data:` …). De sanitatie draait zowel bij het inlezen (schoon op schijf) als bij het uitlezen (schoon voor oude rijen).
**Waar in de code:** `backend/app/core/html_sanitize.py` (`sanitize_reference_html`, `_AllowlistSanitizer`, `_href_is_safe`). De sanitizer is op de standaardbibliotheek `html.parser` gebouwd, dus geen externe afhankelijkheid — een bewuste keuze zodat een beveiligingscontrole niet van een optioneel pakket afhangt.

## Auditing & traceerbaarheid (kort)

Elke geauthenticeerde HTTP-actie laat een spoor na. Een middleware registreert wie-deed-wat-wanneer (actor-id/e-mail/rol, methode, pad, statuscode, tijd, client-IP) in de append-only tabel `audit_log_events`, met minimalisatie van gevoelige gegevens: query-strings worden standaard tot enkel hun *sleutels* herleid en geheim-achtige body-velden gemaskeerd.
**Waar in de code:** `backend/app/middleware/request_logging.py` (`log_request_response`, `_sanitize_for_logging`, `_query_string_for_logging`); de query-modus (`audit_log_query_string_mode`, standaard `keys`) in `config.py`. De onveranderlijkheid van deze tabellen wordt op databankniveau afgedwongen (zie de `coga_app`-rol hierboven) en met hash-ketens/handtekeningen. Voor de volledige uitwerking van audit, hash-ketens en de rapport-provenance, zie [hoofdstuk 11 — Rapport & volledige traceerbaarheid](11-rapport-en-traceerbaarheid.md).

Elke onderdrukking van een security-scan (dependency-audit, secret-scan, SAST) is bovendien gedocumenteerd en gedateerd in `SECURITY-AUDIT-ALLOWLIST.md`, zodat elke uitzondering code-gereviewd en verantwoord is — direct bewijsmateriaal voor het cybersecurity-technisch dossier (TF-13).

## Belangrijkste bestanden

| Bestand | Rol |
| --- | --- |
| `backend/app/dependencies.py` | Authenticatie-dependencies `get_current_user` / `get_current_admin_user`, JWT-uitgifte, `ADMIN_ROLES` |
| `backend/app/services/metadata_service.py` | Project-scoped RBAC: `get_accessible_family_mapping`, `_ensure_user_can_access_metadata_projects`, SQL-filter in `list_family_records`/`_fetch_family_rows` |
| `backend/app/services/family_metadata_context.py` | Gedeeld toegangs-checkpoint `build_family_metadata_context` / `build_sample_metadata_context`, `_visible_project_ids` |
| `backend/app/services/family_service.py` | `get_family_for_user` / `list_families_for_user` — dunne laag die de scoping in `metadata_service` aanroept |
| `backend/app/routers/families.py` | Family-endpoints (`get_family`, `list_families`) achter `get_current_user`, scoping in de service-laag |
| `backend/app/routers/auth.py` | Login/signup/token, throttling-integratie, timing-hardening, gebruikersbeheer (`list_users`, `update_user`) |
| `backend/app/routers/admin.py` | Alle beheer- en destructieve endpoints achter `get_current_admin_user` |
| `backend/app/routers/projects.py` | Projectbeheer (admin) + gescoopte project-dashboardlijst (`list_projects`) |
| `backend/app/routers/lookups.py` | Minimale gebruikers-/statuslijsten voor pickers (`GET /api/users`), leesbaar voor elke ingelogde gebruiker |
| `backend/app/services/auth_rate_limit_pg.py` | Login-/signup-throttling met exponentiële backoff |
| `backend/app/core/config.py` | Instellingen, `validate_security_defaults` (weigert zwakke geheimen), CORS-regexvalidatie, upload-caps, token-levensduur |
| `backend/app/middleware/security_headers.py` | Strikte security-response-headers (CSP, X-Frame-Options, …) |
| `backend/app/middleware/request_logging.py` | Audit-trail naar `audit_log_events` met PII-minimalisatie |
| `backend/app/services/upload_safety.py` | Begrensd lezen/decomprimeren van uploads (anti-DoS) |
| `backend/app/core/html_sanitize.py` | Allowlist-HTML-sanitizer tegen opgeslagen XSS |
| `backend/db/schema/postgres/001_metadata.sql` | `users`-tabel + rol-constraint, koppeltabellen `project_users` / `family_projects` / `sample_projects` |
| `backend/db/schema/postgres/016_superuser_role.sql` | Bevestigt de toegestane rollen (`admin`/`superuser`/`viewer`) |
| `backend/db/schema/postgres/040_app_runtime_role_privileges.sql` | Least-privilege runtime-rol `coga_app` + revoke op append-only tabellen |
| `backend/db/schema/postgres/010_auth_login_attempts.sql` | Tabel achter de login-/signup-throttling |
| `frontend/src/components/RequireAuth.tsx` · `RequireAdmin.tsx` · `SessionRedirect.tsx` | Frontend-guards (UX/defense-in-depth, geen echte poort) |
| `frontend/src/lib/auth.ts` | `isAuthenticated()` / `isAdmin()` en sessie-opslag in de browser |
| `frontend/src/lib/api.ts` | Bearer-token-interceptor + auto-logout bij `HTTP 401` |
| `docs/db-runtime-role-runbook.md` | Runbook voor de DSN-flip naar `coga_app` |
| `docs/security-posture.md` | Point-in-time beveiligingsreview (AuthN/RBAC, audit, encryptie) |
| `SECURITY-AUDIT-ALLOWLIST.md` | Gedateerd register van security-scan-onderdrukkingen (TF-13) |
