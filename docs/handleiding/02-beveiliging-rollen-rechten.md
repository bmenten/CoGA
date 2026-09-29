# 2. Gebruikersrollen, machtigingen & afscherming

Dit hoofdstuk beschrijft hoe CoGA bepaalt *wie* iets mag zien of doen: welke rollen er zijn, hoe toegang tot data per *project* wordt afgeschermd, hoe de backend dat afdwingt en waarom de bewaking in de browser geen echte poort is. Daarna volgt de verdediging in de diepte: een beperkte databankrol, security-headers en CORS, de weigering om met zwakke geheimen te starten, en de bescherming van uploads. De Engelse bron is `docs/security-posture.md`; de cybersecurity-analyse voor het dossier staat in `docs/regulatory/TF-13-cybersecurity.md`.

Een paar begrippen vooraf. **RBAC** (*Role-Based Access Control*) is toegang op basis van rollen. Een **JWT** (*JSON Web Token*) is een ondertekend toegangsbewijs dat de browser bij elk verzoek meestuurt. Een **dependency** is in FastAPI (het Python-framework van de backend) een functie die vóór een endpoint draait en het verzoek kan weigeren. Een **endpoint** is één API-adres, bijvoorbeeld `GET /api/families/{family_id}`.

## Welke rollen bestaan er

CoGA kent precies drie rollen. De databank zelf laat geen andere waarde toe.

| Rol | Betekenis | Wat mag deze rol |
| --- | --- | --- |
| `viewer` | Gewone (klinische) gebruiker | Alleen de families, samples en varianten van de **projecten waarvan hij lid is**; kan binnen die projecten taggen en reviewen. Kan ook een rapport ondertekenen: de software beperkt dat niet. Alleen door het labo gemachtigde ondertekenaars mogen dat doen; dat is een procedurele maatregel (`docs/regulatory/TF-06-risk-management-plan.md`, gevaar H15). |
| `admin` | Beheerder | Alles wat een viewer mag, over alle projecten heen, plus alle beheeracties (projecttoewijzing, verwijderen, referentiedata, auditlogs) |
| `superuser` | Beheerder | Wordt overal als `admin` behandeld |

**Waar in de code:** de toegestane rollen staan als `CHECK`-regel op de tabel `users` in `backend/db/schema/postgres/01_access.sql`. In de backend vormen `admin` en `superuser` samen de beheerders, via `ADMIN_ROLES` en `is_admin_user` in `backend/app/services/access_control.py`.

Er is geen aparte rechtentabel per gebruiker. Wat iemand mag zien, volgt volledig uit **projectlidmaatschap**: de koppeltabel `project_users`. Een familie hangt via `family_projects` aan een of meer projecten, een sample via `sample_projects`. Een viewer ziet dus precies de families waarvan minstens één project ook een van zijn projecten is.

Het token draagt alleen de identiteit (het e-mailadres). De projectenlijst zit er **niet** in: bij elk verzoek laadt de backend de gebruiker en zijn projecten vers uit Postgres. Intrekken werkt daardoor meteen. Een gedeactiveerde gebruiker, of iemand die uit een project wordt gehaald, verliest de toegang bij het volgende verzoek, zonder te wachten tot het token verloopt.

## Projectgebonden toegang: hoe elk dataverzoek wordt ingeperkt

Elk verzoek naar een familie, sample of variant gaat door hetzelfde checkpoint. Er zijn twee mechanismen, voor lijsten en voor losse objecten.

**Lijsten filteren in de databank, niet achteraf.** Vraagt een viewer de families op, dan zit de projectfilter in de SQL-query zelf:

```sql
EXISTS (
    SELECT 1 FROM family_projects afp
    WHERE afp.family_id = f.id
      AND afp.project_id IN :metadata_project_ids
)
```

Families buiten zijn projecten komen dus nooit uit de databank; een vergeten filter in de applicatiecode kan ze niet laten lekken. Een viewer zonder projecten krijgt meteen een lege lijst. Voor een beheerder valt de filter weg.

**Losse objecten worden gecontroleerd bij het ophalen.** Vraagt iemand één familie op via haar id, dan controleert de backend of er overlap is tussen de projecten van die familie en die van de gebruiker. Zo niet, dan volgt `HTTP 403`. Beheerders passeren. Zo kan niemand een familie openen door haar id te raden (bescherming tegen *IDOR*, *Insecure Direct Object Reference*).

Beide controles komen samen in `build_family_metadata_context`: het gedeelde checkpoint dat vrijwel elke familiegebonden view (varianten, tracks, rapport) eerst doorloopt. Het laadt de familie alleen als de gebruiker ze mag zien, en beperkt de zichtbare projecten tot die van de gebruiker. `build_sample_metadata_context` doet hetzelfde voor één sample.

**Waar in de code:** `backend/app/services/access_control.py` (de toegangsregels, o.a. `ensure_user_can_access_metadata_projects`), `backend/app/services/metadata_service.py` (de gefilterde queries en `get_accessible_family_mapping`) en `backend/app/services/family_metadata_context.py` (het checkpoint).

## De backend is de echte poort

Autorisatie wordt alleen in de backend afgedwongen, met twee dependencies:

- **`get_current_user`** controleert het token, laadt de gebruiker vers uit Postgres en weigert (`HTTP 401`) als het token ongeldig is, de gebruiker niet bestaat of niet actief is. Hoe het token wordt gecontroleerd, staat in [hoofdstuk 5](05-login-authenticatie.md).
- **`get_current_admin_user`** bouwt daarop voort en geeft `HTTP 403` als de rol niet in `ADMIN_ROLES` zit. Dit is de poort voor alle beheer- en destructieve acties.

Een gewoon endpoint zoals `GET /api/families/{family_id}` vraagt `get_current_user` (authenticatie) en laat de *autorisatie* over aan de servicelaag, die via het checkpoint hierboven loopt. Een viewer die een onbekend of vreemd `family_id` meegeeft, krijgt `403` of `404`. De hele beheerrouter (`/api/admin/...`) hangt achter `get_current_admin_user`, net als het aanmaken, wijzigen en verwijderen van projecten; de projectenlijst zelf toont een viewer alleen zijn eigen projecten.

Twee nuances voor de auditor:

1. Elke beheercontrole gebruikt `ADMIN_ROLES`, dus een `superuser` telt overal als beheerder. Een test weigert elke nieuwe vergelijking van een rol met de letterlijke tekst `"admin"` (`backend/tests/test_admin_role_checks.py`).
2. Een beperkte gebruikerslijst (`GET /api/users`, alleen naam en e-mail) is leesbaar voor elke ingelogde gebruiker; ze voedt de keuzelijst voor een reviewer. `docs/security-posture.md` (§1) documenteert dat als **aanvaard restrisico**: CoGA draait in één labo waar alle gebruikers collega's zijn.

**Waar in de code:** `backend/app/dependencies.py` (beide dependencies), `backend/app/routers/admin.py` en `backend/app/routers/projects.py`.

## Bewaking in de browser: gemak, geen slot

De frontend heeft eigen bewakers, maar die dienen alleen de gebruikerservaring:

- **`RequireAuth`** stuurt wie geen token heeft naar `/login?next=...`.
- **`RequireAdmin`** stuurt niet-ingelogden naar `/login` en ingelogde niet-beheerders naar `/dashboard`.
- **`SessionRedirect`** kiest een bestemming afhankelijk van "ingelogd of niet".

Waarom dit geen poort is: de rol die de browser gebruikt, staat in de browseropslag, en een gebruiker kan die zelf aanpassen. Dat toont hem hooguit de beheer*schermen*. Elk data- of actieverzoek gaat alsnog naar de backend, die de rol en de projecten opnieuw en gezaghebbend controleert.

**Waar in de code:** `frontend/src/components/RequireAuth.tsx`, `RequireAdmin.tsx` en `SessionRedirect.tsx`; de sessiehulpen in `frontend/src/lib/auth.ts`.

## Least privilege in de databank: de runtime-rol `coga_app`

Ook een gecompromitteerde applicatie mag de *append-only* bewijstabellen niet kunnen herschrijven. "Append-only" betekent: alleen bijschrijven, nooit wijzigen of wissen. Daarvoor bestaat een aparte, beperkte databankrol.

`05_grants.sql` maakt de rol `coga_app` aan met `SELECT`, `INSERT`, `UPDATE` en `DELETE` op de gewone tabellen. Voor de vijf append-only tabellen trekt het `UPDATE`, `DELETE` en `TRUNCATE` weer in: `audit_log_events`, `clinical_audit_events`, `report_signouts`, `integrity_anchors` en `qc_threshold_changes`. Als niet-eigenaar kan `coga_app` bovendien geen trigger uitschakelen. De runtime kan dus wel nieuwe regels toevoegen, maar geen bestaande auditregel, ondertekend rapport, integriteitsanker of eerder vastgelegde QC-grens wijzigen of wissen.

De rol wordt uitgeleverd **zonder login**: standaard draait de applicatie nog als eigenaar van de tabellen. Overschakelen is een operationele stap, geen codewijziging. Het schema wordt dan apart toegepast als eigenaar (`backend/app/db_migrate.py`, met `POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP=false` voor de app), en de app logt in als `coga_app`. Op Google Cloud is dat één Terraform-variabele, `db_runtime_role = "coga_app"` (standaard `"owner"`): een aparte Cloud Run-job (`terraform/migrate.tf`) past dan vóór elke uitrol het schema toe als eigenaar en zet de login van `coga_app` aan met `POSTGRES_APP_PASSWORD`. Die job heeft een eigen serviceaccount, zodat de API het wachtwoord van de eigenaar niet meer kan lezen.

Elke nieuwe append-only tabel moet in `05_grants.sql` dezelfde `REVOKE` krijgen, want de brede `GRANT` geeft nieuwe tabellen automatisch alle rechten. De integratietest `backend/tests/integration/test_app_role_privileges.py` bewaakt dat: hij zoekt in de databank elke tabel met een blokkeertrigger (`*_block_mutation`) en faalt als `coga_app` er nog `UPDATE` of `DELETE` op heeft.

**Waar in de code:** `backend/db/schema/postgres/05_grants.sql`; de procedure, verificatie en rollback staan in `docs/db-runtime-role-runbook.md`.

## Overige verdediging

### Rate limiting tegen brute force

Mislukte logins worden per e-mailadres én per bron-IP geteld; na een drempel volgt een oplopende wachttijd (`HTTP 429`). Registreren wordt apart per bron-IP afgeremd. Achter een proxy haalt CoGA het client-IP uit de `X-Forwarded-For`-header, zoveel stappen van rechts als `TRUSTED_PROXY_HOPS` aangeeft; zo kan een client zijn eigen IP niet kiezen. Dat IP gebruiken ook de auditregels. De werking staat in [hoofdstuk 5](05-login-authenticatie.md).

**Waar in de code:** `backend/app/services/auth_rate_limit_pg.py` en `backend/app/middleware/client_ip.py`.

### Security-headers en CORS

Elk API-antwoord krijgt strikte headers: een Content-Security-Policy die niets toelaat (`default-src 'none'; frame-ancestors 'none'`), `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` en cross-origin-isolatie. HSTS staat alleen aan waar TLS vóór de app eindigt (`ENABLE_HSTS`). CORS laat alleen de ingestelde origins toe, en een origin-patroon moet volledig verankerd zijn (hoofdstuk 1).

**Waar in de code:** `backend/app/middleware/security_headers.py`; CORS in `backend/app/core/config.py`.

### Weigering te starten met zwakke geheimen

Buiten ontwikkeling en test (dus als `APP_ENV` niet `dev`, `development`, `local` of `test` is) weigert de backend te starten als:

- `SECRET_KEY` korter is dan 32 tekens of `secret`/`change-me` is;
- `CLICKHOUSE_PASSWORD` leeg is of `admin`/`change-me` is;
- `INTEGRITY_ANCHOR_SIGNING_KEY` niet de base64 van een 32-byte Ed25519-sleutel is;
- `POSTGRES_PASSWORD`, `ADMIN_PASSWORD` of een ingesteld `POSTGRES_APP_PASSWORD` `admin`/`change-me` is.

Ook `AUDIT_LOG_DROP_ALLOWED=true` (auditregels laten vallen bij een volle wachtrij) wordt daar geweigerd. De foutmelding noemt de velden die niet voldoen. Dit is een bewuste *fail-closed*-keuze: liever niet starten dan onveilig starten. `.env.example` bevat alleen placeholders, precies om te dwingen dat ze vóór een echte uitrol worden vervangen.

**Waar in de code:** `validate_security_defaults` in `backend/app/core/config.py`.

### Uploads en externe HTML

- **Begrensde uploads.** Uploads (SV, referentie, BED, repeats, PED) worden begrensd gelezen én begrensd uitgepakt, zodat een klein maar sterk samengedrukt `.gz`-bestand (een "decompressiebom") het geheugen niet kan uitputten. Te groot geeft `HTTP 413`. Ook de vele gzip-blokken van een BGZF-`.vcf.gz` worden correct doorlopen. **Waar in de code:** `backend/app/services/upload_safety.py`; de grenzen `MAX_UPLOAD_BYTES` en `MAX_DECOMPRESSED_UPLOAD_BYTES` in `config.py`.
- **HTML-sanitatie.** De klinische-CNV-kennisbank bevat kleine HTML-fragmenten die de UI als HTML toont. Een strikte allowlist beperkt ze tot veilige opmaak en weigert al de rest (`script`, `img`, event-attributen, `javascript:`- en `data:`-links). Dat gebeurt zowel bij het inlezen als bij het uitlezen, tegen opgeslagen *cross-site scripting*. **Waar in de code:** `backend/app/core/html_sanitize.py`.
- **Het QC-rapport van de pipeline.** Dat HTML-rapport bevat eigen scripts en geldt als onbetrouwbaar. Het opent via een kortlevende link die pas na de toegangscontrole wordt uitgegeven, en wordt geserveerd met `Content-Security-Policy: sandbox`, zodat het de sessie van de gebruiker niet kan lezen. **Waar in de code:** `backend/app/routers/family_qc_reports.py`.

## Auditing (kort)

Elke HTTP-actie laat een spoor na in de append-only tabel `audit_log_events`: wie (gebruiker en rol), wat (methode, pad, status), wanneer en vanaf welk IP. Gevoelige gegevens worden beperkt: van een querystring worden standaard alleen de sleutels bewaard, en wachtwoord- of tokenvelden worden gemaskeerd. Deze tabel is append-only via een databanktrigger; de hash-keten en de ondertekende ankers gelden voor het klinische auditspoor en de ondertekende rapporten (hoofdstuk 11). De middleware zelf staat in [hoofdstuk 7](07-backend-routers-en-services.md).

Elke onderdrukking van een beveiligingsscan (afhankelijkheden, geheimen, SAST) is gedocumenteerd en gedateerd in `SECURITY-AUDIT-ALLOWLIST.md`.

## Belangrijkste bestanden

| Bestand | Rol |
| --- | --- |
| `backend/app/dependencies.py` | `get_current_user` en `get_current_admin_user` |
| `backend/app/services/access_control.py` | Rollen (`ADMIN_ROLES`) en de toegangsregels |
| `backend/app/services/metadata_service.py` | Projectfilter in de queries |
| `backend/app/services/family_metadata_context.py` | Het gedeelde toegangscheckpoint voor familie- en sampledata |
| `backend/app/core/config.py` | Weigering van zwakke geheimen, CORS-controle, uploadgrenzen |
| `backend/app/middleware/` | Client-IP, security-headers en request-logging |
| `backend/db/schema/postgres/01_access.sql` | `users` met de rolregel, `project_users`, `auth_login_attempts` |
| `backend/db/schema/postgres/05_grants.sql` | De runtime-rol `coga_app` en de `REVOKE` op de append-only tabellen |
| `frontend/src/components/RequireAuth.tsx` · `RequireAdmin.tsx` | Bewakers in de browser (gemak, geen poort) |
| `docs/security-posture.md` · `docs/db-runtime-role-runbook.md` | Beveiligingsoverzicht en het draaiboek voor `coga_app` |
