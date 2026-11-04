# 2. Gebruikersrollen, machtigingen & afscherming

Dit hoofdstuk gaat over *wie* iets mag zien of doen: de vijf globale rollen, afscherming per *project* en het toegangsniveau per project (analist of lezer). Daarna volgt de verdediging in de diepte: een beperkte databankrol, security-headers en CORS, de weigering van zwakke geheimen en de bescherming van uploads. De Engelse bron is `docs/security-posture.md`; de cybersecurity-analyse voor het dossier staat in `docs/regulatory/TF-13-cybersecurity.md`.

Een paar begrippen vooraf. **RBAC** (*Role-Based Access Control*) is toegang op basis van rollen. Een **JWT** (*JSON Web Token*) is een ondertekend toegangsbewijs dat de browser bij elk verzoek meestuurt. Een **dependency** is in FastAPI (het Python-framework van de backend) een functie die vóór een endpoint draait en het verzoek kan weigeren. Een **endpoint** is één API-adres, bv. `GET /api/families/{family_id}`.

## Welke rollen bestaan er

CoGA kent vijf globale rollen (`users.role`), hier van laag naar hoog. Elke rol heeft alle rechten van de rollen die ervoor staan. De databank laat geen andere waarde toe. Zo besliste de eigenaar op 2026-11-04; vroeger waren er drie rollen (`viewer`, `superuser`, `admin`), en telde een superuser als beheerder.

| Rol | Wat mag deze rol |
| --- | --- |
| `viewer` | **Lezen**, in de **projecten waarvan hij lid is**: de families, samples, varianten, tracks, rapporten en ondertekende versies bekijken en doorzoeken, CSV exporteren en afdrukken. Hij wijzigt niets, welk toegangsniveau hij in een project ook heeft. Alleen zijn eigen filterpresets en de gebeurtenissen van zijn eigen gebruik van de interface bewaart hij. Het klinische auditspoor ziet hij niet. Elk nieuw account begint als viewer, zonder project. |
| `analyst` | Ook **analyseren**, in de projecten waarin zijn toegangsniveau analist is (zie *Toegangsniveau per project*): reviewen en taggen, ACMG- en CNV-classificaties, fenotypes, de workflowvelden (status, toegewezen aan, nagekeken door) en nieuwe families (Family Builder). Hij leest er ook het klinische auditspoor. |
| `supervisor` | Ook een analyse **afsluiten met *Sign out***, de analytische ondertekening, in de projecten waarin hij analist is (zie *Ondertekenen: supervisor of hoger*). De medische ondertekening van het klinische verslag gebeurt in het EPD, niet in CoGA. |
| `superuser` | Ook **elk project**, lid of niet: hij ziet en wijzigt elk record, ondertekent overal, en staat in de keuzelijsten *Assigned to* en *Reviewed by* van elke familie. Daarbij de **configuratie die de analyses lezen**: genpanels, varianttags, presetfilters en de grenzen voor sequencing-QC, en de **pakketimport**, behalve een bestaande familie overschrijven. |
| `admin` | De **beheerder**: ook het hele beheer (zie *Superuser of beheerder* hieronder). |

In deze handleiding is een **beheerder** iemand met de rol `admin`. Een superuser telt nergens als beheerder.

**Waar in de code:** de toegestane rollen staan als `CHECK`-regel op de tabel `users` in `backend/db/schema/postgres/01_access.sql`. In de backend staan ze in `backend/app/services/access_control.py`: de volgorde in `USER_ROLES`, met `has_role` (deze rol of een hogere), en per recht de rollen die het hebben: `CHANGING_ROLES` (`analyst` en hoger), `SIGN_OUT_ROLES` (`supervisor` en hoger), `SUPERUSER_ROLES` (`superuser` en `admin`) en `ADMIN_ROLES` (alleen `admin`). De vragen erop zijn `role_changes_records`, `may_sign_out`, `has_superuser_rights` en `is_admin_user` (alleen `admin`). In de frontend: `USER_ROLES`, `hasRole`, `isAdmin` (alleen `admin`) en `hasSuperuserRights` in `frontend/src/lib/auth.ts`.

**Superuser of beheerder.** Een superuser heeft, naast elk project:

- de genpanels: aanmaken, wijzigen en verwijderen, een PanelApp-panel importeren en het Mendeliome opnieuw opbouwen (hoofdstuk 15);
- de varianttags: de eigen tags aanmaken, wijzigen en verwijderen, op de beheerpagina en op de routes van een familie (hoofdstuk 10);
- het overzicht van de presetfilters van iedereen (hoofdstuk 15);
- de grenzen voor sequencing-QC (hoofdstuk 15);
- de pakketimport: de routes `/api/family-imports`, met de importjobs van iedereen (hoofdstuk 6). Een bestaande familie overschrijven (`conflict_mode: "overwrite"`, geen dry run) mag hij niet; als dry run wel, en `cancel` en `update` ook.

Op het beheerdashboard ziet hij alleen die vijf werkruimtes: *Gene panels*, *Sequencing QC thresholds*, *Package import*, *Variant tags* en *Preset filters*. Al de rest van het beheer is alleen voor een beheerder: accounts en rollen (de pagina *Users*, `PATCH /api/auth/users/{id}`) en de accountlijst `GET /api/users` (SEC-33); projecten en hun toegang; de familie- en sampledata en hun verwijdering; de familiestatussen; soorten en assemblies; HPO, Monarch en de repeatcatalogus; het ClickHouse-onderhoud; de auditlogs en de integriteitscontroles; de NIPT-artefacten; het annotatiemanifest van een familie vervangen; de genreferentie verversen; de klinische CNV-kennisbank herbouwen; een PED-upload en de losse uploads per bestand; de familiestructuur bewerken; en een bestaande familie overschrijven, met een PED-upload, de Family Builder of een pakketimport (REQ-SEC-002).

**Een rol toekennen.** Een beheerder zet de rol van een account op de pagina *Users* (`PATCH /api/auth/users/{id}` met `role`), na een bevestiging. De keuzelijst toont de vijf rollen van laag naar hoog, elk met een tooltip die zegt wat de rol mag. Niemand wijzigt zijn eigen rol of deactiveert zijn eigen account. De noodbeheerder (hoofdstuk 5) houdt de rol `admin` en blijft actief. Elke wijziging staat in de auditregel van het verzoek, met de rol ervoor en erna (`request_meta.account_change`). Wie zich registreert, of zich voor het eerst met Microsoft of via CAS aanmeldt, krijgt een account met de rol `viewer` en zonder project. Hij ziet geen patiëntgegevens tot een beheerder hem aan een project toevoegt, en wijzigt niets tot een beheerder hem een hogere rol geeft (hoofdstuk 5).

**Bestaande accounts.** Revisie `0015` (`backend/db/migrations/versions/0015_analyst_and_supervisor_roles.py`) brengt een bestaande databank naar de nieuwe `CHECK` en maakt van elke bestaande viewer een `analyst`. Een viewer kon vroeger de records wijzigen van de projecten waarin hij analist was, en houdt dat zo. Ondertekenen kan hij daarna niet meer: een beheerder geeft wie het labo daartoe machtigt de rol `supervisor`. Een superuser blijft superuser en verliest de delen van het beheer die nu alleen voor een beheerder zijn; een admin blijft admin. Die wijzigingen van rijen hebben geen auditregel van een verzoek: de revisie, in de release, is hun record (hoofdstuk 3).

Er is geen aparte rechtentabel per gebruiker. Wat iemand mag zien, volgt uit zijn rol en zijn **projectlidmaatschap**: de koppeltabel `project_users`, die per lid ook zijn toegangsniveau in dat project bewaart. Een familie hangt via `family_projects` aan een of meer projecten. Een sample is zichtbaar via de projecten van zijn familie. `sample_projects` koppelt een sample daarnaast rechtstreeks aan projecten, o.a. voor de samplekeuzelijst van de Variant Explorer. Een viewer, analyst of supervisor ziet dus precies de families waarvan minstens één project ook een van zijn projecten is; een superuser en een beheerder zien ze alle.

Het token draagt alleen de identiteit: het object-ID van de gebruiker bij een Microsoft-token, het e-mailadres bij een lokaal token. De rol en de projectenlijst zitten er **niet** in. Bij elk verzoek laadt de backend de gebruiker, zijn rol, zijn projecten en zijn toegangsniveau in elk ervan vers uit Postgres. Intrekken werkt daardoor meteen. Wie gedeactiveerd wordt, uit een project wordt gehaald, er lezer wordt of een lagere rol krijgt, verliest de toegang of het recht bij het volgende verzoek, zonder te wachten tot het token verloopt. Een beheerder die superuser wordt, verliest zo het beheer; een supervisor die `analyst` wordt, het ondertekenen.

Een job die nog niet liep, loopt niet meer als het account dat hem vroeg intussen gedeactiveerd werd of de rol niet meer heeft die de job vraagt (SEC-35, beslissing van de eigenaar van 2026-10-28). Voor een importjob (hoofdstuk 6) is dat superuser of beheerder, en beheerder voor een import die een bestaande familie overschrijft; voor een verversing van de genreferentie (hoofdstuk 13) en een herbouw van de klinische CNV-kennisbank (hoofdstuk 15) beheerder. Die herbouw laadt ook niets als de deactivatie of het rolverlies pas tijdens zijn build gebeurde. De job eindigt `failed` met de reden, en er wordt niets geschreven.

## Projectgebonden toegang: hoe elk dataverzoek wordt ingeperkt

Elk verzoek naar een familie, sample of variant gaat door hetzelfde checkpoint. Er zijn twee mechanismen: één voor lijsten, één voor losse objecten.

**Lijsten filteren in de databank, niet achteraf.** Vraagt een gebruiker de families op, dan zit de projectfilter in de SQL-query zelf: een `EXISTS`-voorwaarde op `family_projects` laat alleen families door die aan minstens één van zijn projecten hangen. Families buiten zijn projecten komen dus nooit uit de databank; een vergeten filter in de applicatiecode kan ze niet laten lekken. Een gebruiker zonder projecten krijgt meteen een lege lijst. Voor een superuser en een beheerder valt de filter weg (`has_superuser_rights`).

**Losse objecten worden gecontroleerd bij het ophalen.** Vraagt iemand één familie op via haar id, dan controleert de backend of de projecten van die familie en die van de gebruiker overlappen. Zo niet, dan antwoordt de backend precies zoals voor een onbestaand id: `HTTP 404` met `Family not found`, of voor een sample `Sample not found`. Een project buiten zijn projecten, opgegeven in de Family Builder of bij een genprofiel, krijgt zo `404 Project not found`. Een superuser en een beheerder passeren. Zo kan niemand een familie openen door haar id te raden (bescherming tegen *IDOR*, *Insecure Direct Object Reference*). Het antwoord verraadt ook niet of een familie of sample van een ander project bestaat. De auditlog houdt het verschil wel bij (`request_meta.record_hidden`, hoofdstuk 7).

Beide controles komen samen in `build_family_metadata_context`: het gedeelde checkpoint dat vrijwel elke familiegebonden view (varianten, tracks, rapport) eerst doorloopt. Het laadt de familie alleen als de gebruiker ze mag zien, en beperkt de zichtbare projecten tot die van de gebruiker. `build_sample_metadata_context` doet hetzelfde voor één sample.

**Waar in de code:** `backend/app/services/access_control.py` (de toegangsregels, o.a. `user_can_access_metadata_projects`), `backend/app/services/metadata_service.py` (de gefilterde queries en `get_accessible_family_mapping`) en `backend/app/services/family_metadata_context.py` (het checkpoint).

## De backend is de echte poort

Autorisatie wordt alleen in de backend afgedwongen, met drie dependencies:

- **`get_current_user`** controleert het token en laadt de gebruiker vers uit Postgres. Het weigert (`HTTP 401`) als het token ongeldig is of de gebruiker niet bestaat of niet actief is. Hoe het token wordt gecontroleerd, staat in [hoofdstuk 5](05-login-authenticatie.md).
- **`get_current_superuser`** bouwt daarop voort en geeft `HTTP 403 Superuser access required` als de rol geen `superuser` of `admin` is (`has_superuser_rights`). Dit is de poort voor de configuratie die de analyses lezen en voor de pakketimport.
- **`get_current_admin_user`** bouwt ook op `get_current_user` voort en geeft `HTTP 403 Admin access required` als de rol niet `admin` is (`is_admin_user`). Dit is de poort voor de rest van het beheer en voor de destructieve acties. Een superuser komt er niet door.

Een gewoon endpoint zoals `GET /api/families/{family_id}` vraagt `get_current_user` (authenticatie) en laat de *autorisatie* over aan de servicelaag, via het checkpoint hierboven. Een onbekend en een vreemd `family_id` krijgen dus hetzelfde antwoord: `404 Family not found`. Een `family_id` met een stuurteken krijgt al bij de aanmelding `400`, voor iedereen hetzelfde ([hoofdstuk 5](05-login-authenticatie.md)). De beheerrouter (`/api/admin/...`) hangt achter `get_current_admin_user`, behalve de varianttags, het overzicht van de presetfilters en de grenzen voor sequencing-QC: die vragen `get_current_superuser`, net als de genpanels schrijven (`/api/panels`), de varianttags op de routes van een familie en de hele pakketimport (`/api/family-imports`). Het aanmaken, wijzigen en verwijderen van projecten is voor een beheerder. De projectenlijst zelf toont een viewer, analyst of supervisor alleen zijn eigen projecten.

Twee nuances voor de auditor:

1. Geen controle buiten `access_control.py` vergelijkt een rol met een letterlijke rolnaam: elke controle vraagt de verzamelingen en de functies daar (`is_admin_user`, `has_superuser_rights`, `role_changes_records`, `may_sign_out`). Een test weigert elke vergelijking van een rol met een letterlijke rolnaam elders (`backend/tests/test_admin_role_checks.py`; vroeger weigerde hij alleen `"admin"`). Een superuser telt nergens als beheerder: `backend/tests/test_admin_route_gating.py` eist dat elke beheerroute een superuser weigert, en dat elke superuserroute een viewer, een analyst en een supervisor weigert.
2. De beperkte gebruikerslijst (`GET /api/users`: naam en e-mail van elk actief account) is alleen voor beheerders (`get_current_admin_user`, SEC-33). Wie geen beheerder is, ook een superuser, krijgt `403`, zoals op de andere beheerroutes; een gedeactiveerde beheerder krijgt `401`. Geen scherm leest die lijst: de keuzelijsten *Assigned to* en *Reviewed by* van een familie lezen de eigen lijst van de familie (hieronder), de pagina *Users* leest `GET /api/auth/users`. Vroeger kon elke ingelogde gebruiker ze lezen, een **aanvaard restrisico** in `docs/security-posture.md` (§1). De eigenaar beëindigde dat op 2026-10-28.

**Waar in de code:** `backend/app/dependencies.py` (de drie dependencies), `backend/app/routers/admin.py`, `backend/app/routers/panels.py`, `backend/app/routers/family_imports.py` en `backend/app/routers/projects.py`.

## Toegangsniveau per project: analist of lezer

Naast zijn rol heeft elk projectlid in dat project een **toegangsniveau** (SEC-1, beslissing van de eigenaar van 2026-10-14). Het niveau neemt alleen rechten af, nooit toe:

| Niveau | Mag in het project |
| --- | --- |
| **analist** (`analyst`) | Lezen, en wijzigen zo ver zijn rol gaat: analyseren met de rol `analyst`, ook ondertekenen met de rol `supervisor`. Een viewer met dit niveau wijzigt niets. |
| **lezer** (`reader`), bv. een student | Alleen lezen, welke rol hij ook heeft (onder superuser): de families, varianten, tracks, rapporten en ondertekende versies bekijken en doorzoeken, CSV exporteren, het rapport en een ondertekende versie afdrukken, en eigen filterpresets bewaren. Niet: het klinische auditspoor zien, en niets klinisch wijzigen: geen fenotypes, reviews, tags, ACMG- of CNV-classificaties, *Sign out*, workflowvelden (status, toegewezen aan, nagekeken door), nieuwe families, uploads, imports, bewerkingen of verwijderingen. |

Dezelfde persoon kan analist zijn in het ene project en lezer in het andere. Hangt een familie aan meerdere projecten, dan mag een analist van eender welk ervan ze wijzigen, net zoals een lid van eender welk ervan ze mag zien. Een superuser en een beheerder hebben alle rechten in elk project, lid of niet; hun niveau in een project telt niet.

**Instellen.** Een beheerder kiest het niveau per lid op de pagina *Projects & access*, in een keuzelijst naast elk aangevinkt lid. De pagina *Users* toont het niveau per project. In de API is het het veld `reader_user_ids` van een project: de leden die lezer zijn; de andere leden zijn analist. Een lid zonder niveau is analist (de standaardwaarde van de kolom). Een update zonder `reader_user_ids` laat elk blijvend lid zijn niveau houden. Een lezer die geen lid is, wordt geweigerd (`400`). Toen de kolom kwam, werd elk bestaand lidmaatschap analist; voor bestaande gebruikers veranderde er dus niets.

**Afdwingen op één plaats.** `get_current_user` legt bij elk verzoek vast of het iets wijzigt: elke methode behalve GET, HEAD en OPTIONS, tenzij de route gemarkeerd is met `changes_only_own_data`. Die markering dragen alleen de routes voor de eigen filterpresets van de gebruiker en voor de gebeurtenissen van zijn eigen gebruik van de interface; ook een viewer en een lezer gebruiken die. De checkpoints van familie, sample en project (`get_accessible_family_mapping`, `get_accessible_sample_mapping` en `_resolve_accessible_project_id` van de Family Builder) weigeren dan de wijziging van een viewer of een lezer met `403` en de reden (`require_change_access`). Een viewer krijgt *Read-only access: your role is viewer, so you can view and export records but not change them.* (`VIEWER_READ_ONLY_DETAIL`), een lezer de zin die zijn niveau in het project noemt (`READ_ONLY_DETAIL`). Dat gebeurt **na** de zichtbaarheidscontrole: een familie buiten zijn projecten blijft dus `404` antwoorden, zoals een onbekende. Elke route die een familie, sample of project noemt, moet langs die checkpoints; geen route kan de controle dus vergeten. Het klinische auditspoor (`GET /api/families/{id}/clinical-audit`) weigert een viewer en een lezer ook (`403`, met `VIEWER_AUDIT_TRAIL_DETAIL` of `AUDIT_TRAIL_DETAIL`). De beheerroutes blijven achter `get_current_admin_user` of `get_current_superuser`.

**Ondertekenen: supervisor of hoger.** Een analyse afsluiten met *Sign out* vraagt bovendien de rol `supervisor`, `superuser` of `admin` (`TF-06`, gevaar H15: een analyse ondertekend door iemand zonder ondertekeningsbevoegdheid). `sign_out_report` roept `require_sign_out_authority` op na het checkpoint van de familie, zodat een familie buiten de projecten van de gebruiker `404` blijft antwoorden en een viewer of lezer eerst hoort dat hij alleen leest, en vóór elke poort, zodat een analyst geen erkenningen doorloopt die hij niet kan gebruiken. Een analyst krijgt `403` met *Signing out needs the supervisor role: ask a supervisor of this family's project to sign out the analysis.* (`SIGN_OUT_ROLE_DETAIL`), en er wordt niets geschreven. Vroeger kon elke analist in het project ondertekenen, en was H15 alleen een procedurele maatregel; nu is het een controle in de software. Wie de rol `supervisor` krijgt, blijft een beslissing van het labo (hoofdstuk 11).

**In de schermen.** De familie en de projecten die de API teruggeeft, zeggen of ze voor de gebruiker alleen-lezen zijn (`read_only`: zijn rol is viewer, of hij is lezer in elk project van de familie waarvan hij lid is). De familie zegt ook of hij haar analyse mag ondertekenen (`can_sign_out`: zijn rol is supervisor of hoger, en hij mag de familie wijzigen). Een familiepagina die alleen-lezen is, toont *Read-only access*. De knoppen die de gebruiker niet mag gebruiken, zijn uitgeschakeld of verborgen:

- de review- en ACMG-dialogen bewaren niet;
- de snelle tags staan uit;
- *Sign out report* is uitgeschakeld, ook voor een analyst zonder `can_sign_out`, met de reden *Signing out needs the supervisor role …*;
- het auditspoor wordt niet opgevraagd; het rapport zegt op het scherm en op papier waarom het er niet staat;
- de fenotypes en workflowvelden zijn niet te wijzigen;
- de Family Builder biedt alleen de projecten aan waarin de gebruiker analist is.

Dat is gemak; de weigering van de server is de poort.

**Toegewezen aan en nagekeken door.** Een familie wordt toegewezen aan (*Assigned to*) en nagekeken door (*Reviewed by*) een actieve superuser of beheerder, of een actieve analist van een van haar projecten wiens rol geen viewer is; nooit een lezer of een viewer. De twee keuzelijsten op de familiepagina lezen de eigen lijst van de familie (`GET /api/families/{family_id}/assignable-users`). Die antwoordt, net als de familie zelf, `404` buiten de projecten van de gebruiker. Noemt een bewaring iemand anders (een lezer, een viewer, iemand buiten de projecten van de familie of een gedeactiveerd account), dan weigert de server ze met `422` en de reden (bv. *their role is viewer*), met de hint *Choose an analyst of one of the family's projects, a superuser or an administrator.*, en verandert er niets. Een waarde die de familie al heeft, blijft staan tot iemand ze wijzigt: wie lezer of viewer werd, blijft genoemd, en de keuzelijst toont die persoon met *(not eligible)*. **Waar in de code:** `backend/app/services/family_status_service.py` (`list_family_assignable_users`, `update_family_metadata_for_user`).

**Testen.**

- `backend/tests/test_project_access_levels.py` houdt de catalogus bij van elke route die iets wijzigt: van een beheerder, van een superuser (`SUPERUSER_CHANGE_ROUTES`), eigen data, of geweigerd aan een checkpoint. Een nieuwe route faalt tot ze ingedeeld is.
- `backend/tests/e2e/test_e2e_project_reader_role.py` roept op echte Postgres elke zo'n route aan als lezer en eist `403` met de reden. Elke route die een familie of sample leest, roept hij aan als lezer en als analist; hij eist hetzelfde antwoord, behalve voor het auditspoor.
- `backend/tests/test_access_rules.py` legt de regels van `access_control.py` vast per rol en niveau; `backend/tests/test_report_signout.py` begint met de rol van de ondertekenaar (een viewer en een analyst worden geweigerd vóór elke poort, een supervisor, superuser en beheerder ondertekenen).
- `backend/tests/e2e/test_e2e_global_roles.py` loopt de vijf rollen af op echte Postgres, met echte aanmeldingen: de viewer leest en wordt een wijziging en het auditspoor geweigerd, de analyst wijzigt en wordt de ondertekening geweigerd, de supervisor ondertekent, en de superuser, in geen enkel project, leest de familie, opent zijn werkruimtes en wordt die van de beheerder geweigerd.

**Waar in de code:** `backend/app/services/access_control.py` (`user_can_change_metadata_projects`, `user_can_sign_out_metadata_projects`, `require_change_access`, `require_sign_out_authority`, `require_audit_trail_access`, `changes_only_own_data`, `request_changes_records`), `backend/app/dependencies.py` (`get_current_user`), `backend/app/services/metadata_service.py` (de checkpoints, `read_only`, `can_sign_out` en `_set_project_user_links`), `backend/app/services/report_signout_service.py` (`sign_out_report`), de kolom `access_level` in `backend/db/schema/postgres/01_access.sql`; in de frontend `frontend/src/lib/familyAccess.ts` en `frontend/src/pages/families/reportSignOut.tsx`.

## Bewaking in de browser: gemak, geen slot

De frontend heeft eigen bewakers, maar die dienen alleen de gebruikerservaring:

- **`RequireAuth`** stuurt wie geen token heeft naar `/login?next=...`.
- **`RequireAdmin`** stuurt niet-ingelogden naar `/login` en ingelogde niet-beheerders naar `/dashboard`. Met `<RequireAdmin superuser />` laat hij ook een superuser door: zo bewaakt hij het beheerdashboard en de vijf werkruimtes van een superuser. Het menu en het dashboard tonen *Admin* aan een superuser en een beheerder.
- **`SessionRedirect`** kiest een bestemming afhankelijk van "ingelogd of niet".

Waarom dit geen poort is: de rol die de browser gebruikt, staat in de browseropslag, en een gebruiker kan die zelf aanpassen. Dat toont hem hooguit de beheer*schermen*. Elk data- of actieverzoek gaat alsnog naar de backend, die de rol en de projecten opnieuw en gezaghebbend controleert.

**Waar in de code:** `frontend/src/components/RequireAuth.tsx`, `RequireAdmin.tsx` en `SessionRedirect.tsx`; de sessiehulpen in `frontend/src/lib/auth.ts`.

## Least privilege in de databank: de runtime-rol `coga_app`

Ook een gecompromitteerde applicatie mag de *append-only* bewijstabellen niet kunnen herschrijven. "Append-only" betekent: alleen bijschrijven, nooit wijzigen of wissen. Daarvoor bestaat een aparte, beperkte databankrol.

`05_grants.sql` maakt de rol `coga_app` aan met `SELECT`, `INSERT`, `UPDATE` en `DELETE` op de gewone tabellen. Op de append-only tabellen trekt het `UPDATE`, `DELETE` en `TRUNCATE` weer in:

- `audit_log_events`, `clinical_audit_events`, `report_signouts`, `integrity_anchors` en `qc_threshold_changes`;
- `family_structure_versions` en `ui_events`, sinds SEC-7 (de stamboomversies en het logboek van de UI-gebeurtenissen);
- `wgs_sources`, `ancestry_reference_panels` en `ancestry_results`, de tabellen van de verkennende ancestry-PCA (`wgs_sources` alleen voor projecties van vóór revisie 0016).

Het migratiegrootboek `alembic_version` mag `coga_app` alleen lezen: alleen de migratiestap schrijft er, als eigenaar, de revisie in waarop de databank staat. Als niet-eigenaar kan `coga_app` ook geen trigger uitschakelen. De runtime kan dus rijen aan die tabellen toevoegen, maar geen bestaande rij wijzigen of wissen: geen auditregel, ondertekend rapport, integriteitsanker, eerder vastgelegde QC-grens, stamboomversie of UI-gebeurtenis. De enige toegelaten wijziging doet de databank zelf, bij het verwijderen van een gebruiker of familie: de verwijzing ernaar gaat op `NULL` (`ON DELETE SET NULL`), terwijl een eigen kolom van de rij bewaart wie of welke familie het was (hoofdstuk 11).

De rol wordt uitgeleverd **zonder login**: standaard draait de applicatie nog als eigenaar van de tabellen. Overschakelen is een operationele stap, geen codewijziging. Het schema wordt dan apart toegepast als eigenaar (`backend/app/db_migrate.py`, met `POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP=false` voor de app), en de app logt in als `coga_app`. Op Google Cloud is dat één Terraform-variabele: `db_runtime_role = "coga_app"` (standaard `"owner"`). Een aparte Cloud Run-job (`terraform/migrate.tf`) past dan vóór elke uitrol het schema toe als eigenaar en zet de login van `coga_app` aan met `POSTGRES_APP_PASSWORD`. Die job heeft een eigen serviceaccount, zodat de API het wachtwoord van de eigenaar niet meer kan lezen.

Elke nieuwe append-only tabel moet in `05_grants.sql` dezelfde `REVOKE` krijgen, want de brede `GRANT` geeft nieuwe tabellen automatisch alle rechten. De integratietest `backend/tests/integration/test_app_role_privileges.py` bewaakt dat: hij zoekt in de databank elke tabel met een blokkeertrigger (`*_block_mutation`) en faalt als `coga_app` er nog `UPDATE` of `DELETE` op heeft.

**Waar in de code:** `backend/db/schema/postgres/05_grants.sql`; de procedure, verificatie en rollback staan in `docs/db-runtime-role-runbook.md`.

## Overige verdediging

### Rate limiting tegen brute force

Mislukte logins worden per e-mailadres én per bron-IP geteld; na een drempel volgt een oplopende wachttijd (`HTTP 429`). Registreren wordt apart per bron-IP afgeremd. Achter een proxy haalt CoGA het client-IP uit de `X-Forwarded-For`-header, zoveel stappen van rechts als `TRUSTED_PROXY_HOPS` aangeeft. Zo kan een client zijn eigen IP niet kiezen. Ook de auditregels gebruiken dat IP. De werking staat in [hoofdstuk 5](05-login-authenticatie.md).

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
- een ingesteld `METRICS_TOKEN` korter is dan 32 tekens, `secret`/`change-me` is, of gelijk is aan `SECRET_KEY` of `INTEGRITY_ANCHOR_SIGNING_KEY` (wie de metrics leest, mag geen sessietokens of ankers kunnen maken);
- `AUDIT_LOG_DROP_ALLOWED=true` (auditregels laten vallen bij een volle wachtrij) of `AUDIT_LOG_MODE=off` (helemaal geen auditregels schrijven) is ingesteld;
- de CORS-instellingen open staan (hoofdstuk 1), of de build geen `GIT_SHA` heeft (hoofdstuk 4).

De foutmelding noemt de velden die niet voldoen. Dit is een bewuste *fail-closed*-keuze: liever niet starten dan onveilig starten. `.env.example` bevat alleen placeholders, precies om te dwingen dat ze vóór een echte uitrol worden vervangen.

**Waar in de code:** `validate_security_defaults` in `backend/app/core/config.py`.

### Uploads en externe HTML

- **Begrensde uploads.** Uploads (SV, referentie, BED, repeats, PED, de NIPT-artefacttabel) en de tekstbestanden van een familiepakket worden begrensd gelezen én begrensd uitgepakt; een small-variant-VCF wordt regel per regel gestreamd. Zo kan een klein maar sterk samengedrukt `.gz`-bestand (een "decompressiebom") het geheugen niet uitputten. Te groot geeft `HTTP 413`. De vele gzip-blokken van een BGZF-`.vcf.gz` worden correct doorlopen. `HTTP 400` volgt bij een gzip-bestand dat midden in een blok afbreekt, bij bytes na het laatste blok die zelf geen volledig blok zijn, en bij een BGZF-bestand dat niet op het eindblok van BGZF eindigt (hoofdstuk 6). **Waar in de code:** `backend/app/services/upload_safety.py`, dat uitpakt met `gunzip_bounded` uit `bounded_download.py`; de grenzen `MAX_UPLOAD_BYTES` en `MAX_DECOMPRESSED_UPLOAD_BYTES` in `config.py`.
- **HTML-sanitatie.** De klinische-CNV-kennisbank bevat kleine HTML-fragmenten die de UI als HTML toont. Een strikte allowlist beperkt ze tot veilige opmaak en weigert al de rest (`script`, `img`, event-attributen, `javascript:`- en `data:`-links). Dat gebeurt bij het inlezen én bij het uitlezen, tegen opgeslagen *cross-site scripting*. **Waar in de code:** `backend/app/core/html_sanitize.py`.
- **Het QC-rapport van de pipeline.** Dat HTML-rapport bevat eigen scripts en geldt als onbetrouwbaar. Het opent via een kortlevende link, die pas na de toegangscontrole wordt uitgegeven. Bij lokale opslag zoekt de backend het rapport alleen in de eigen mappen van de familie, op het pad dat de import bij het sample vastlegde. Een pad dat daaruit leidt, of een symbolische koppeling naar de map van een andere familie, geeft `404`. De backend serveert het met `Content-Security-Policy: sandbox allow-scripts allow-popups`: de scripts van het rapport draaien, maar in een afgezonderde origin die de sessie van de gebruiker niet kan lezen. Bij objectopslag (`s3`, `gcs`) is de link een kortlevende presigned URL van de bucket, een andere origin. **Waar in de code:** `backend/app/routers/family_qc_reports.py`.

## Auditing (kort)

Elke HTTP-actie laat een spoor na in de append-only tabel `audit_log_events`: wie (gebruiker en rol), wat (methode, pad, status), wanneer en vanaf welk IP. Gevoelige gegevens worden beperkt. Van een querystring worden standaard alleen de sleutels bewaard. Wachtwoord- of tokenvelden worden gemaskeerd, in de auditlog en in de UI-events, volgens één lijst van sleutels (`SENSITIVE_KEY_PREFIXES` in `core/coga_logging.py`). Een databanktrigger maakt deze tabel append-only. De hash-keten en de ondertekende ankers gelden voor het klinische auditspoor en de ondertekende rapporten (hoofdstuk 11). De middleware zelf staat in [hoofdstuk 7](07-backend-routers-en-services.md).

Elke onderdrukking van een beveiligingsscan (afhankelijkheden, geheimen, SAST) is gedocumenteerd en gedateerd in `SECURITY-AUDIT-ALLOWLIST.md`.

## Belangrijkste bestanden

| Bestand | Rol |
| --- | --- |
| `backend/app/dependencies.py` | `get_current_user`, `get_current_superuser` en `get_current_admin_user` |
| `backend/app/services/access_control.py` | De rollen (`USER_ROLES`, `has_role` en de verzamelingen per recht), de toegangsregels, het ondertekeningsrecht en het toegangsniveau per project (analist of lezer) |
| `backend/app/services/metadata_service.py` | Projectfilter in de queries |
| `backend/app/services/family_metadata_context.py` | Het gedeelde toegangscheckpoint voor familie- en sampledata |
| `backend/app/core/config.py` | Weigering van zwakke geheimen, CORS-controle, uploadgrenzen |
| `backend/app/middleware/` | Client-IP, security-headers en request-logging |
| `backend/db/schema/postgres/01_access.sql` | `users` met de rolregel, `project_users` met het toegangsniveau van elk lid, `auth_login_attempts` |
| `backend/db/migrations/versions/0015_analyst_and_supervisor_roles.py` | De vijf rollen in een bestaande databank; elke bestaande viewer wordt `analyst` |
| `backend/db/schema/postgres/05_grants.sql` | De runtime-rol `coga_app` en de `REVOKE` op de append-only tabellen en op het migratiegrootboek |
| `frontend/src/components/RequireAuth.tsx` · `RequireAdmin.tsx` | Bewakers in de browser (gemak, geen poort) |
| `docs/security-posture.md` · `docs/db-runtime-role-runbook.md` | Beveiligingsoverzicht en het draaiboek voor `coga_app` |
