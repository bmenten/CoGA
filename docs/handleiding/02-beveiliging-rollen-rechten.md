# 2. Gebruikersrollen, machtigingen & afscherming

Dit hoofdstuk beschrijft hoe CoGA bepaalt *wie* iets mag zien of doen: welke rollen er zijn, hoe toegang tot data per *project* wordt afgeschermd, welk toegangsniveau (analist of lezer) een lid per project heeft, hoe de backend dat afdwingt en waarom de bewaking in de browser geen echte poort is. Daarna volgt de verdediging in de diepte: een beperkte databankrol, security-headers en CORS, de weigering om met zwakke geheimen te starten, en de bescherming van uploads. De Engelse bron is `docs/security-posture.md`; de cybersecurity-analyse voor het dossier staat in `docs/regulatory/TF-13-cybersecurity.md`.

Een paar begrippen vooraf. **RBAC** (*Role-Based Access Control*) is toegang op basis van rollen. Een **JWT** (*JSON Web Token*) is een ondertekend toegangsbewijs dat de browser bij elk verzoek meestuurt. Een **dependency** is in FastAPI (het Python-framework van de backend) een functie die vóór een endpoint draait en het verzoek kan weigeren. Een **endpoint** is één API-adres, bijvoorbeeld `GET /api/families/{family_id}`.

## Welke rollen bestaan er

CoGA kent precies drie rollen. De databank zelf laat geen andere waarde toe.

| Rol | Betekenis | Wat mag deze rol |
| --- | --- | --- |
| `viewer` | Gewone (klinische) gebruiker | Alleen de families, samples en varianten van de **projecten waarvan hij lid is**. Wat hij er mag, hangt af van zijn **toegangsniveau in dat project** (zie *Toegangsniveau per project* verderop): als **analist** kan hij er taggen, reviewen, fenotypes wijzigen en een analyse afsluiten met *Sign out*, de analytische ondertekening; als **lezer** kan hij er alleen lezen. Welke analist mag ondertekenen, beperkt de software niet: alleen wie het labo daartoe machtigt, mag dat; dat is een procedurele maatregel (`docs/regulatory/TF-06-risk-management-plan.md`, gevaar H15). De medische ondertekening van het klinische verslag gebeurt in het EPD, niet in CoGA. |
| `admin` | Beheerder | Alles wat een viewer mag, over alle projecten heen, plus alle beheeracties (projecttoewijzing, verwijderen, referentiedata, auditlogs) |
| `superuser` | Beheerder | Wordt overal als `admin` behandeld |

**Waar in de code:** de toegestane rollen staan als `CHECK`-regel op de tabel `users` in `backend/db/schema/postgres/01_access.sql`. In de backend vormen `admin` en `superuser` samen de beheerders, via `ADMIN_ROLES` en `is_admin_user` in `backend/app/services/access_control.py`.

**Een rol toekennen.** Een beheerder zet de rol van een account op de pagina *Users* (`PATCH /api/auth/users/{id}` met `role`), na een bevestiging. Niemand wijzigt zijn eigen rol of deactiveert zijn eigen account, en de noodbeheerder (hoofdstuk 5) houdt zijn rol en blijft actief. Elke wijziging staat in de auditregel van het verzoek, met de rol ervoor en erna (`request_meta.account_change`). Wie zich met Microsoft voor het eerst aanmeldt, krijgt een account met de rol `viewer` en zonder project: hij ziet geen patiëntgegevens tot een beheerder hem aan een project toevoegt, net zoals een lokaal account (hoofdstuk 5).

Er is geen aparte rechtentabel per gebruiker. Wat iemand mag zien, volgt volledig uit **projectlidmaatschap**: de koppeltabel `project_users`, die per lid ook zijn toegangsniveau in dat project bewaart. Een familie hangt via `family_projects` aan een of meer projecten, en een sample is zichtbaar via de projecten van zijn familie (`sample_projects` koppelt een sample daarnaast rechtstreeks aan projecten, o.a. voor de samplekeuzelijst van de Variant Explorer). Een viewer ziet dus precies de families waarvan minstens één project ook een van zijn projecten is.

Het token draagt alleen de identiteit: het object-ID van de gebruiker bij een Microsoft-token, het e-mailadres bij een lokaal token. De projectenlijst zit er **niet** in: bij elk verzoek laadt de backend de gebruiker, zijn projecten en zijn toegangsniveau in elk ervan vers uit Postgres. Intrekken werkt daardoor meteen. Een gedeactiveerde gebruiker, of iemand die uit een project wordt gehaald of er lezer wordt, verliest de toegang of het recht om te wijzigen bij het volgende verzoek, zonder te wachten tot het token verloopt. Een beheerder die `viewer` wordt, verliest zo het beheer. Een job die een beheerder vroeg en die nog niet liep, loopt niet meer als dat account intussen gedeactiveerd werd of geen beheerder meer is (SEC-35, beslissing van de eigenaar van 2026-10-28): een importjob (hoofdstuk 6), een verversing van de genreferentie (hoofdstuk 13) en een herbouw van de klinische CNV-kennisbank, die bovendien niets laadt als dat pas tijdens zijn build gebeurde (hoofdstuk 15). De job eindigt `failed` met de reden, en er wordt niets geschreven.

## Projectgebonden toegang: hoe elk dataverzoek wordt ingeperkt

Elk verzoek naar een familie, sample of variant gaat door hetzelfde checkpoint. Er zijn twee mechanismen, voor lijsten en voor losse objecten.

**Lijsten filteren in de databank, niet achteraf.** Vraagt een viewer de families op, dan zit de projectfilter in de SQL-query zelf: een `EXISTS`-voorwaarde op `family_projects` laat alleen families door die aan minstens één van zijn projecten hangen. Families buiten zijn projecten komen dus nooit uit de databank; een vergeten filter in de applicatiecode kan ze niet laten lekken. Een viewer zonder projecten krijgt meteen een lege lijst. Voor een beheerder valt de filter weg.

**Losse objecten worden gecontroleerd bij het ophalen.** Vraagt iemand één familie op via haar id, dan controleert de backend of er overlap is tussen de projecten van die familie en die van de gebruiker. Zo niet, dan antwoordt de backend precies zoals voor een id dat niet bestaat: `HTTP 404` met `Family not found`, of voor een sample `Sample not found`. Een project buiten zijn projecten, opgegeven in de Family Builder of bij een genprofiel, krijgt zo `404 Project not found`. Beheerders passeren. Zo kan niemand een familie openen door haar id te raden (bescherming tegen *IDOR*, *Insecure Direct Object Reference*), en verraadt het antwoord ook niet of een familie of sample van een ander project bestaat. De auditlog houdt het verschil wel bij (`request_meta.record_hidden`, hoofdstuk 7).

Beide controles komen samen in `build_family_metadata_context`: het gedeelde checkpoint dat vrijwel elke familiegebonden view (varianten, tracks, rapport) eerst doorloopt. Het laadt de familie alleen als de gebruiker ze mag zien, en beperkt de zichtbare projecten tot die van de gebruiker. `build_sample_metadata_context` doet hetzelfde voor één sample.

**Waar in de code:** `backend/app/services/access_control.py` (de toegangsregels, o.a. `user_can_access_metadata_projects`), `backend/app/services/metadata_service.py` (de gefilterde queries en `get_accessible_family_mapping`) en `backend/app/services/family_metadata_context.py` (het checkpoint).

## De backend is de echte poort

Autorisatie wordt alleen in de backend afgedwongen, met twee dependencies:

- **`get_current_user`** controleert het token, laadt de gebruiker vers uit Postgres en weigert (`HTTP 401`) als het token ongeldig is, de gebruiker niet bestaat of niet actief is. Hoe het token wordt gecontroleerd, staat in [hoofdstuk 5](05-login-authenticatie.md).
- **`get_current_admin_user`** bouwt daarop voort en geeft `HTTP 403` als de rol niet in `ADMIN_ROLES` zit. Dit is de poort voor alle beheer- en destructieve acties.

Een gewoon endpoint zoals `GET /api/families/{family_id}` vraagt `get_current_user` (authenticatie) en laat de *autorisatie* over aan de servicelaag, die via het checkpoint hierboven loopt. Een viewer die een onbekend of vreemd `family_id` meegeeft, krijgt in beide gevallen hetzelfde antwoord: `404 Family not found`. Een `family_id` met een stuurteken krijgt al bij de aanmelding `400`, voor iedereen hetzelfde ([hoofdstuk 5](05-login-authenticatie.md)). De hele beheerrouter (`/api/admin/...`) hangt achter `get_current_admin_user`, net als het aanmaken, wijzigen en verwijderen van projecten; de projectenlijst zelf toont een viewer alleen zijn eigen projecten.

Twee nuances voor de auditor:

1. Elke beheercontrole gebruikt `ADMIN_ROLES`, dus een `superuser` telt overal als beheerder. Een test weigert elke nieuwe vergelijking van een rol met de letterlijke tekst `"admin"` (`backend/tests/test_admin_role_checks.py`).
2. Een beperkte gebruikerslijst (`GET /api/users`, alleen naam en e-mail van elk actief account) is leesbaar voor elke ingelogde gebruiker. `docs/security-posture.md` (§1) documenteert dat als **aanvaard restrisico**: CoGA draait in één labo waar alle gebruikers collega's zijn. De keuzelijsten *Assigned to* en *Reviewed by* van een familie lezen ze niet meer, maar de eigen lijst van de familie (hieronder).

**Waar in de code:** `backend/app/dependencies.py` (beide dependencies), `backend/app/routers/admin.py` en `backend/app/routers/projects.py`.

## Toegangsniveau per project: analist of lezer

Elk projectlid heeft in dat project een **toegangsniveau** (SEC-1, beslissing van de eigenaar van 2026-10-14):

| Niveau | Mag in het project |
| --- | --- |
| **analist** (`analyst`) | Lezen én wijzigen: alles wat hierboven voor een `viewer` staat. |
| **lezer** (`reader`), bv. een student | Alleen lezen: de families, varianten, tracks, rapporten en ondertekende versies bekijken en doorzoeken, CSV exporteren, het rapport en een ondertekende versie afdrukken, en eigen filterpresets bewaren. Niet: het klinische auditspoor zien, en niets klinisch wijzigen: geen fenotypes, reviews, tags, ACMG- of CNV-classificaties, *Sign out*, workflowvelden (status, toegewezen aan, nagekeken door), nieuwe families, uploads, imports, bewerkingen of verwijderingen. |

Dezelfde persoon kan analist zijn in het ene project en lezer in het andere. Hangt een familie aan meerdere projecten, dan mag een analist van eender welk ervan ze wijzigen, net zoals een lid van eender welk ervan ze mag zien. Een beheerder (`admin`, `superuser`) heeft alle rechten in elk project, lid of niet.

**Instellen.** Een beheerder kiest het niveau per lid op de pagina *Projects & access* (een keuzelijst naast elk aangevinkt lid); de pagina *Users* toont per project het niveau. In de API is het het veld `reader_user_ids` van een project: de leden die lezer zijn, de andere leden zijn analist. Een lid zonder niveau is analist (de standaardwaarde van de kolom). Een update zonder `reader_user_ids` laat elk blijvend lid zijn niveau houden; een lezer die geen lid is, wordt geweigerd (`400`). Toen de kolom kwam, werd elk bestaand lidmaatschap analist, zodat er voor bestaande gebruikers niets veranderde.

**Afdwingen op één plaats.** `get_current_user` legt bij elk verzoek vast of het iets wijzigt: elke methode behalve GET, HEAD en OPTIONS, tenzij de route gemarkeerd is met `changes_only_own_data` (alleen de eigen filterpresets van de gebruiker en de gebeurtenissen van zijn eigen gebruik van de interface). De checkpoints van familie, sample en project (`get_accessible_family_mapping`, `get_accessible_sample_mapping` en `_resolve_accessible_project_id` van de Family Builder) weigeren dan de wijziging van een lezer met `403` en de reden (`require_change_access`), **na** de zichtbaarheidscontrole: een familie buiten zijn projecten blijft dus `404` antwoorden, zoals een onbekende. Omdat elke route die een familie, sample of project noemt langs die checkpoints moet, kan geen route de controle vergeten. Het klinische auditspoor (`GET /api/families/{id}/clinical-audit`) weigert een lezer ook (`403`). De beheerroutes blijven achter `get_current_admin_user`.

**In de schermen.** De familie en de projecten die de API teruggeeft, zeggen of ze voor de gebruiker alleen-lezen zijn (`read_only`). Een familiepagina toont dan *Read-only access (reader)*, en de knoppen die een lezer niet mag gebruiken, zijn uitgeschakeld of verborgen: de review- en ACMG-dialogen bewaren niet, de snelle tags staan uit, *Sign out report* is uitgeschakeld, het auditspoor wordt niet opgevraagd (het rapport zegt op het scherm en op papier waarom het er niet staat), de fenotypes en workflowvelden zijn niet te wijzigen en de Family Builder biedt alleen de projecten aan waarin de gebruiker analist is. Dat is gemak; de weigering van de server is de poort.

**Toegewezen aan en nagekeken door.** Een familie wordt toegewezen aan (*Assigned to*), en nagekeken door (*Reviewed by*), een actieve analist van een van haar projecten of een actieve beheerder, nooit een lezer. De twee keuzelijsten op de familiepagina lezen de eigen lijst van de familie (`GET /api/families/{family_id}/assignable-users`), die net als de familie zelf `404` antwoordt buiten de projecten van de gebruiker. Een bewaring die iemand anders noemt (een lezer, iemand buiten de projecten van de familie of een gedeactiveerd account), weigert de server met `422` en de reden, en ze verandert niets. Een waarde die de familie al heeft, blijft staan tot iemand ze wijzigt: wie een lezer werd, blijft genoemd en de keuzelijst toont die persoon met *(not eligible)*. **Waar in de code:** `backend/app/services/family_status_service.py` (`list_family_assignable_users`, `update_family_metadata_for_user`).

**Testen.** `backend/tests/test_project_access_levels.py` houdt de catalogus bij van elke route die iets wijzigt (van een beheerder, eigen data, of geweigerd aan een checkpoint): een nieuwe route faalt tot ze ingedeeld is. `backend/tests/e2e/test_e2e_project_reader_role.py` stelt elke zo'n route op echte Postgres als lezer en eist `403` met de reden, en stelt elke route die een familie of sample leest als lezer en als analist en eist hetzelfde antwoord, behalve het auditspoor.

**Waar in de code:** `backend/app/services/access_control.py` (`user_can_change_metadata_projects`, `require_change_access`, `require_audit_trail_access`, `changes_only_own_data`, `request_changes_records`), `backend/app/dependencies.py` (`get_current_user`), `backend/app/services/metadata_service.py` (de checkpoints en `_set_project_user_links`), de kolom `access_level` in `backend/db/schema/postgres/01_access.sql`; in de frontend `frontend/src/lib/familyAccess.ts`.

## Bewaking in de browser: gemak, geen slot

De frontend heeft eigen bewakers, maar die dienen alleen de gebruikerservaring:

- **`RequireAuth`** stuurt wie geen token heeft naar `/login?next=...`.
- **`RequireAdmin`** stuurt niet-ingelogden naar `/login` en ingelogde niet-beheerders naar `/dashboard`.
- **`SessionRedirect`** kiest een bestemming afhankelijk van "ingelogd of niet".

Waarom dit geen poort is: de rol die de browser gebruikt, staat in de browseropslag, en een gebruiker kan die zelf aanpassen. Dat toont hem hooguit de beheer*schermen*. Elk data- of actieverzoek gaat alsnog naar de backend, die de rol en de projecten opnieuw en gezaghebbend controleert.

**Waar in de code:** `frontend/src/components/RequireAuth.tsx`, `RequireAdmin.tsx` en `SessionRedirect.tsx`; de sessiehulpen in `frontend/src/lib/auth.ts`.

## Least privilege in de databank: de runtime-rol `coga_app`

Ook een gecompromitteerde applicatie mag de *append-only* bewijstabellen niet kunnen herschrijven. "Append-only" betekent: alleen bijschrijven, nooit wijzigen of wissen. Daarvoor bestaat een aparte, beperkte databankrol.

`05_grants.sql` maakt de rol `coga_app` aan met `SELECT`, `INSERT`, `UPDATE` en `DELETE` op de gewone tabellen. Voor de zeven append-only tabellen trekt het `UPDATE`, `DELETE` en `TRUNCATE` weer in: `audit_log_events`, `clinical_audit_events`, `report_signouts`, `integrity_anchors`, `qc_threshold_changes`, `family_structure_versions` en `ui_events` (de laatste twee sinds SEC-7: de stamboomversies en het logboek van de UI-gebeurtenissen). Het migratiegrootboek `alembic_version` mag `coga_app` alleen lezen: alleen de migratiestap, als eigenaar, schrijft er de revisie in waarop de databank staat. Als niet-eigenaar kan `coga_app` bovendien geen trigger uitschakelen. De runtime kan dus wel nieuwe regels toevoegen, maar geen bestaande auditregel, ondertekend rapport, integriteitsanker, eerder vastgelegde QC-grens, stamboomversie of UI-gebeurtenis wijzigen of wissen. De enige toegelaten wijziging is die van de databank zelf bij het verwijderen van een gebruiker of familie: de verwijzing ernaar gaat op `NULL` (`ON DELETE SET NULL`), terwijl de rij in een eigen kolom bewaart wie of welke familie het was (hoofdstuk 11).

De rol wordt uitgeleverd **zonder login**: standaard draait de applicatie nog als eigenaar van de tabellen. Overschakelen is een operationele stap, geen codewijziging. Het schema wordt dan apart toegepast als eigenaar (`backend/app/db_migrate.py`, met `POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP=false` voor de app), en de app logt in als `coga_app`. Op Google Cloud is dat één Terraform-variabele, `db_runtime_role = "coga_app"` (standaard `"owner"`): een aparte Cloud Run-job (`terraform/migrate.tf`) past dan vóór elke uitrol het schema toe als eigenaar en zet de login van `coga_app` aan met `POSTGRES_APP_PASSWORD`. Die job heeft een eigen serviceaccount, zodat de API het wachtwoord van de eigenaar niet meer kan lezen.

Elke nieuwe append-only tabel moet in `05_grants.sql` dezelfde `REVOKE` krijgen, want de brede `GRANT` geeft nieuwe tabellen automatisch alle rechten. De integratietest `backend/tests/integration/test_app_role_privileges.py` bewaakt dat: hij zoekt in de databank elke tabel met een blokkeertrigger (`*_block_mutation`) en faalt als `coga_app` er nog `UPDATE` of `DELETE` op heeft.

**Waar in de code:** `backend/db/schema/postgres/05_grants.sql`; de procedure, verificatie en rollback staan in `docs/db-runtime-role-runbook.md`.

## Overige verdediging

### Rate limiting tegen brute force

Mislukte logins worden per e-mailadres én per bron-IP geteld; na een drempel volgt een oplopende wachttijd (`HTTP 429`). Registreren wordt apart per bron-IP afgeremd. Achter een proxy haalt CoGA het client-IP uit de `X-Forwarded-For`-header, zoveel stappen van rechts als `TRUSTED_PROXY_HOPS` aangeeft; zo kan een client zijn eigen IP niet kiezen. Dat IP gebruiken ook de auditregels. De werking staat in [hoofdstuk 5](05-login-authenticatie.md).

**Waar in de code:** `backend/app/services/auth_rate_limit_pg.py` en `backend/app/middleware/client_ip.py`.

### Security-headers en CORS

Elk API-antwoord krijgt strikte headers, ook de `500` bij een fout die niets anders afhandelt (SEC-23): een Content-Security-Policy die niets toelaat (`default-src 'none'; frame-ancestors 'none'`), `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` en cross-origin-isolatie. HSTS staat alleen aan waar TLS vóór de app eindigt (`ENABLE_HSTS`). Op Google Cloud zet ook de load balancer die HSTS-header op elk antwoord dat hij over HTTPS doorgeeft, ook op de foutpagina's van Cloud Run zelf. CORS laat alleen de ingestelde origins toe, en een origin-patroon moet volledig verankerd zijn (hoofdstuk 1).

**Waar in de code:** `backend/app/middleware/security_headers.py` en de 500-handler in `backend/app/main.py`; CORS in `backend/app/core/config.py`; de header van de load balancer in `terraform/loadbalancer.tf`.

### Weigering te starten met zwakke geheimen

Buiten ontwikkeling en test (dus als `APP_ENV` niet `dev`, `development`, `local` of `test` is) weigert de backend te starten als:

- `SECRET_KEY` korter is dan 32 tekens of `secret`/`change-me` is;
- `CLICKHOUSE_PASSWORD` leeg is of `admin`/`change-me` is;
- `INTEGRITY_ANCHOR_SIGNING_KEY` niet de base64 van een 32-byte Ed25519-sleutel is, of gelijk is aan `SECRET_KEY` (dan kan wie sessietokens kan maken ook integriteitsankers ondertekenen);
- `POSTGRES_PASSWORD`, `ADMIN_PASSWORD` of een ingesteld `POSTGRES_APP_PASSWORD` `admin`/`change-me` is;
- een ingesteld `METRICS_TOKEN` korter is dan 32 tekens, `secret`/`change-me` is, of gelijk is aan `SECRET_KEY` of `INTEGRITY_ANCHOR_SIGNING_KEY` (wie de metrics leest, mag geen sessietokens of ankers kunnen maken).

Ook `AUDIT_LOG_DROP_ALLOWED=true` (auditregels laten vallen bij een volle wachtrij) en `AUDIT_LOG_MODE=off` (helemaal geen auditregels schrijven) worden daar geweigerd, net als open CORS-instellingen (hoofdstuk 1) en een build zonder `GIT_SHA` (hoofdstuk 4). De foutmelding noemt de velden die niet voldoen. Dit is een bewuste *fail-closed*-keuze: liever niet starten dan onveilig starten. `.env.example` bevat alleen placeholders, precies om te dwingen dat ze vóór een echte uitrol worden vervangen.

**Waar in de code:** `validate_security_defaults` in `backend/app/core/config.py`.

### Uploads en externe HTML

- **Begrensde uploads.** Uploads (SV, referentie, BED, repeats, PED, de NIPT-artefacttabel) en de tekstbestanden van een familiepakket worden begrensd gelezen én begrensd uitgepakt (een small-variant-VCF wordt regel per regel gestreamd), zodat een klein maar sterk samengedrukt `.gz`-bestand (een "decompressiebom") het geheugen niet kan uitputten. Te groot geeft `HTTP 413`. Ook de vele gzip-blokken van een BGZF-`.vcf.gz` worden correct doorlopen. Een gzip-bestand dat midden in een blok afbreekt, geeft `HTTP 400`, net als bytes na het laatste blok die zelf geen volledig blok zijn. **Waar in de code:** `backend/app/services/upload_safety.py`, dat uitpakt met `gunzip_bounded` uit `bounded_download.py`; de grenzen `MAX_UPLOAD_BYTES` en `MAX_DECOMPRESSED_UPLOAD_BYTES` in `config.py`.
- **HTML-sanitatie.** De klinische-CNV-kennisbank bevat kleine HTML-fragmenten die de UI als HTML toont. Een strikte allowlist beperkt ze tot veilige opmaak en weigert al de rest (`script`, `img`, event-attributen, `javascript:`- en `data:`-links). Dat gebeurt zowel bij het inlezen als bij het uitlezen, tegen opgeslagen *cross-site scripting*. **Waar in de code:** `backend/app/core/html_sanitize.py`.
- **Het QC-rapport van de pipeline.** Dat HTML-rapport bevat eigen scripts en geldt als onbetrouwbaar. Het opent via een kortlevende link die pas na de toegangscontrole wordt uitgegeven. Bij lokale opslag zoekt de backend het rapport alleen in de eigen mappen van de familie, op het pad dat de import bij het sample vastlegde: een pad dat daaruit leidt, of een symbolische koppeling naar de map van een andere familie, geeft `404`. Hij serveert het met `Content-Security-Policy: sandbox allow-scripts allow-popups`: de scripts van het rapport draaien, maar in een afgezonderde origin die de sessie van de gebruiker niet kan lezen. Bij objectopslag (`s3`, `gcs`) is de link een kortlevende presigned URL van de bucket, een andere origin. **Waar in de code:** `backend/app/routers/family_qc_reports.py`.

## Auditing (kort)

Elke HTTP-actie laat een spoor na in de append-only tabel `audit_log_events`: wie (gebruiker en rol), wat (methode, pad, status), wanneer en vanaf welk IP. Gevoelige gegevens worden beperkt: van een querystring worden standaard alleen de sleutels bewaard, en wachtwoord- of tokenvelden worden gemaskeerd, in de auditlog en in de UI-events volgens één lijst van sleutels (`SENSITIVE_KEY_PREFIXES` in `core/coga_logging.py`). Deze tabel is append-only via een databanktrigger; de hash-keten en de ondertekende ankers gelden voor het klinische auditspoor en de ondertekende rapporten (hoofdstuk 11). De middleware zelf staat in [hoofdstuk 7](07-backend-routers-en-services.md).

Elke onderdrukking van een beveiligingsscan (afhankelijkheden, geheimen, SAST) is gedocumenteerd en gedateerd in `SECURITY-AUDIT-ALLOWLIST.md`.

## Belangrijkste bestanden

| Bestand | Rol |
| --- | --- |
| `backend/app/dependencies.py` | `get_current_user` en `get_current_admin_user` |
| `backend/app/services/access_control.py` | Rollen (`ADMIN_ROLES`), de toegangsregels en het toegangsniveau per project (analist of lezer) |
| `backend/app/services/metadata_service.py` | Projectfilter in de queries |
| `backend/app/services/family_metadata_context.py` | Het gedeelde toegangscheckpoint voor familie- en sampledata |
| `backend/app/core/config.py` | Weigering van zwakke geheimen, CORS-controle, uploadgrenzen |
| `backend/app/middleware/` | Client-IP, security-headers en request-logging |
| `backend/db/schema/postgres/01_access.sql` | `users` met de rolregel, `project_users` met het toegangsniveau van elk lid, `auth_login_attempts` |
| `backend/db/schema/postgres/05_grants.sql` | De runtime-rol `coga_app` en de `REVOKE` op de append-only tabellen en op het migratiegrootboek |
| `frontend/src/components/RequireAuth.tsx` · `RequireAdmin.tsx` | Bewakers in de browser (gemak, geen poort) |
| `docs/security-posture.md` · `docs/db-runtime-role-runbook.md` | Beveiligingsoverzicht en het draaiboek voor `coga_app` |
