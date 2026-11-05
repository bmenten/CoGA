# 5. Login & authenticatie

Dit hoofdstuk beschrijft hoe een gebruiker toegang krijgt tot CoGA. Bij de ingebruikname meldt iedereen zich aan met zijn Microsoft-account van het ziekenhuis of de universiteit (Microsoft Entra ID, vroeger Azure AD; zie *Aanmelden met Microsoft*), of via de aanmeldserver van de organisatie, zoals `login.ugent.be`, met CAS (zie *Aanmelden via CAS*) of OAuth 2.0 (zie *Aanmelden via OAuth*). Daarnaast bestaat het lokale aanmelden met e-mailadres en wachtwoord: in ontwikkeling voor iedereen, naast Microsoft alleen voor de noodbeheerder. Het hoofdstuk volgt eerst dat lokale aanmelden, van formulier tot token en sessie, en het registreren. Wat een ingelogde gebruiker daarna mag zien, staat in [hoofdstuk 2](02-beveiliging-rollen-rechten.md).

## Begrippen

- **JWT** (*JSON Web Token*): een klein, digitaal ondertekend bewijs dat de server na een geslaagde login meegeeft. Het bevat enkele gegevens (*claims*), zoals wie je bent en tot wanneer het geldt. Omdat het ondertekend is met een geheime sleutel, kan de client het niet vervalsen.
- **Hashen:** een wachtwoord onomkeerbaar omzetten in een reeks tekens. De server bewaart alleen die hash; bij het inloggen wordt de ingevoerde tekst opnieuw gehasht en vergeleken.
- **Rate limiting / lockout:** het tijdelijk blokkeren na te veel mislukte pogingen.

## De login in vogelvlucht

1. De gebruiker vult e-mailadres en wachtwoord in op de inlogpagina.
2. De frontend stuurt die naar het login-endpoint van de backend.
3. De backend controleert of het adres of het IP niet geblokkeerd is, vergelijkt het wachtwoord met de opgeslagen hash en geeft bij succes een JWT terug.
4. De browser bewaart het token en stuurt het bij elk volgend verzoek mee in de `Authorization`-header.
5. De backend controleert het token bij elk beschermd endpoint en leidt daaruit af wie de gebruiker is.

## De login-endpoints

De endpoints staan in `backend/app/routers/auth.py`, onder `/api/auth/...`. Twee ingangen leiden naar dezelfde logica:

| Endpoint | Invoer | Bedoeld voor |
| --- | --- | --- |
| `POST /auth/login` | JSON met `email` en `password` | De inlogpagina |
| `POST /auth/token` | Een OAuth2-formulier met `username` en `password` | De interactieve API-documentatie, die alleen in ontwikkeling bereikbaar is; het e-mailadres gaat in `username` |

Beide roepen `_authenticate_and_issue_token` aan. Die doet, in volgorde:

1. **De blokkering controleren.** Is het e-mailadres of het IP geblokkeerd, dan volgt `429` ("Too many login attempts") met een `Retry-After`-header.
2. **De gebruiker opzoeken en het wachtwoord controleren.** Een onbekend adres en een verkeerd wachtwoord geven dezelfde melding: `400 Incorrect email or password`.
3. **Controleren of het account actief is.** Een nog niet geactiveerd account krijgt `403 User not active`. Ook die poging telt als mislukt.
4. **Bij succes:** de teller van mislukte pogingen wissen en een token uitgeven.

Het antwoord bevat het token, het type (`bearer`) en de rol van de gebruiker, zodat de frontend weet wat hij moet tonen.

**Bescherming tegen accountopsomming.** Zei de server bij een onbekend adres meteen "nee", dan kon een aanvaller aan de *responstijd* zien welke adressen bestaan: de bcrypt-controle is bewust traag. Daarom controleert de code bij een onbekend adres toch, tegen een vaste dummy-hash. Beide gevallen duren zo ongeveer even lang en geven dezelfde melding. Het hashen draait in een aparte thread, zodat een trage controle de server niet ophoudt.

**Waar in de code:** `_authenticate_and_issue_token` in `backend/app/routers/auth.py`.

## Wachtwoorden: bcrypt

Wachtwoorden worden nooit leesbaar bewaard. De backend hasht ze met **bcrypt**, dat hij rechtstreeks aanroept. Bcrypt is bewust traag, zodat een aanvaller die de databank buitmaakt niet snel miljoenen wachtwoorden kan uitproberen. De hash staat in de kolom `hashed_password` van `users`.

Bcrypt leest alleen de eerste 72 bytes van een wachtwoord. De backend kapt een langer wachtwoord daarom zelf af op 72 bytes, bij het aanmaken van de hash én bij de controle, zodat beide altijd hetzelfde deel vergelijken. Een opgeslagen waarde die geen bcrypt-hash is (bv. van een account zonder lokaal wachtwoord), geeft gewoon geen match.

**Waar in de code:** `get_password_hash` en `verify_password` in `backend/app/dependencies.py`; de tests in `backend/tests/test_password_hashing.py`.

## Het token

Een geslaagde login levert een JWT op:

| Eigenschap | Waarde |
| --- | --- |
| Claim `sub` | Het e-mailadres van de gebruiker |
| Claim `exp` | Het verloopmoment |
| Levensduur | Kort: standaard twee uur (`ACCESS_TOKEN_EXPIRE_MINUTES`) |
| Handtekening | HS256 (symmetrisch), met `SECRET_KEY` |

De korte levensduur is een bewuste keuze. Het token staat in de browseropslag, niet in een afgeschermde HttpOnly-cookie; lekt het, dan is de schade beperkt tot die periode. Wie `SECRET_KEY` kent, kan geldige tokens maken; daarom weigert de backend buiten ontwikkeling een zwakke sleutel ([hoofdstuk 2](02-beveiliging-rollen-rechten.md#weigering-te-starten-met-zwakke-geheimen)).

**Waar in de code:** `create_access_token` in `backend/app/dependencies.py`; de instellingen in `backend/app/core/config.py`.

### Het token controleren: `get_current_user`

Elk beschermd endpoint hangt af van `get_current_user`, op twee na: de download van het QC-rapport, die alleen zijn eigen ondertekende link van vijf minuten controleert (hoofdstuk 2), en `GET /metrics`, dat een eigen token vraagt (hoofdstuk 7). Die functie:

1. haalt het token uit de header `Authorization: Bearer …`;
2. controleert de handtekening en de geldigheid; een ongeldig of verlopen token geeft `401 Could not validate credentials`;
3. leest het e-mailadres uit de claim `sub`;
4. laadt de gebruiker vers uit Postgres en controleert dat die bestaat en actief is; zo niet, opnieuw `401`;
5. hangt de gebruiker aan het verzoek (`request.state.current_user`), zodat de auditlog weet wie het verzoek deed;
6. weigert (`400`) een verzoek met een stuurteken (C0 of DEL, zoals een NUL uit `%00`) in een padparameter, in de naam van een queryparameter of in de waarde van `family_id` of `sample_id` in de querystring, en een verzoek met een NUL in eender welke querywaarde. Een familie- of staal-ID mag zo'n teken niet bevatten, en Postgres kan een NUL niet vergelijken: zo'n verzoek liep vroeger vast op een `500`. Een gen- of intervallijst, één per regel getypt, behoudt haar regeleinden. De weigering komt na de aanmelding, zodat de auditlog de gebruiker noemt, en vóór een route iets opzoekt, met hetzelfde antwoord voor iedereen (`request_value_problem` in `backend/app/services/family_identifiers.py`).

Twee dependencies bouwen daarop voort. `get_current_superuser` eist de rol `superuser` of `admin`; anders volgt `403 Superuser access required`. `get_current_admin_user` eist de rol `admin`, die van de beheerder; anders volgt `403 Admin access required`, ook voor een superuser. Welke routes welke dependency vragen, staat in hoofdstuk 2.

**Waar in de code:** `get_current_user`, `get_current_superuser` en `get_current_admin_user` in `backend/app/dependencies.py`.

## De frontend: van formulier tot sessie

De inlogpagina (`frontend/src/pages/auth/LoginPage.tsx`) stuurt `POST /auth/login`, vraagt met het verse token het profiel op (`GET /auth/me`) en bewaart dan de sessie: het token, het e-mailadres en de rol. Daarna opent ze de gevraagde pagina uit de parameter `next`. Die wordt eerst gefilterd:

- **Toegelaten** is alleen een pad binnen de app, in de vorm die de routebewakers zelf schrijven: het pad, de querystring en de hash van de pagina, procent-gecodeerd zoals de browser ze bijhoudt. Dat is een `/`, dan een letter of cijfer, en verder alleen zichtbare ASCII-tekens zonder backslash. Een browser leest zo'n waarde altijd als een pad op deze site.
- **Al het andere** opent het dashboard, net als een ontbrekende `next`: een externe URL, `//`, een backslash, een spatie of een stuurteken, en een pad dat met een procent-gecodeerd teken begint, zoals `/%2F/host`. Een browser leest een backslash als een slash en laat een tab of regeleinde weg: `next=/%5Chost` of `next=/%09/host` in de adresbalk is voor hem `//host`, een andere site.

Zo kan een aanvaller je na het inloggen niet naar een andere site sturen (*open redirect*), en loopt de pagina na het inloggen niet vast op "This view could not be displayed" (React Router weigert zo'n adres).

De sessie staat in de browseropslag (`localStorage`), die de tabbladen van de browser delen. Is die niet beschikbaar of weigert ze een waarde, dan houdt de pagina die waarde zelf bij, in het geheugen. Een waarde die de browseropslag wel aannam, houdt de pagina niet bij. Vroeger hield ze elke waarde bij, en las een tabblad zo een sessie terug die een ander tabblad had beëindigd (gevonden bij SEC-36). Hoe de API-client het token meestuurt en bij een `401` uitlogt, staat in [hoofdstuk 1](01-architectuur.md); de routebewakers in hoofdstuk 2.

**Waar in de code:** `frontend/src/pages/auth/LoginPage.tsx`, `frontend/src/lib/auth.ts` en `frontend/src/lib/storage.ts`.

## Rate limiting en lockout

Mislukte pogingen worden bijgehouden in de Postgres-tabel `auth_login_attempts`. Elke rij is een *bereik* met een teller:

| Kolom | Betekenis |
| --- | --- |
| `scope_type` / `scope_value` | Het bereik: `email`, `remote_ip` of `signup_ip` |
| `failure_count` | Het aantal opeenvolgende mislukte pogingen |
| `last_failure_at` | Het tijdstip van de laatste mislukking |
| `locked_until` | Tot wanneer dit bereik geblokkeerd is |

De werking:

- Elke mislukte login verhoogt de teller voor **twee** bereiken tegelijk: het ingevoerde e-mailadres en het bron-IP. Zo wordt zowel het bestoken van één account als het uitproberen over vele accounts afgeremd.
- Vanaf een drempel volgt een wachttijd die per extra poging verdubbelt, tot een maximum. Is de laatste mislukking lang genoeg geleden, dan begint de telling opnieuw.
- Een geslaagde login wist de tellers.
- Drempel, venster en wachttijden zijn instelbaar (`LOGIN_RATE_LIMIT_*`, met hun standaardwaarde in `.env.example`).

Het bron-IP is het IP dat de middleware achter de proxies vaststelt (`TRUSTED_PROXY_HOPS`, hoofdstuk 2), zodat een client het niet zelf kan kiezen. Registreren wordt apart en alleen per bron-IP afgeremd: wie accounts opsomt, probeert juist telkens een ander adres.

**Waar in de code:** `backend/app/services/auth_rate_limit_pg.py`; de tabel in `backend/db/schema/postgres/01_access.sql`.

## Registreren en goedkeuren

Iedereen kan zich registreren via `POST /auth/signup` (pagina `frontend/src/pages/auth/SignupPage.tsx`), maar dat geeft **geen** toegang:

- Het wachtwoord moet minstens 15 tekens lang zijn (naar NIST SP 800-63B-4, voor een wachtwoord dat de enige factor is). Een korter wachtwoord krijgt `422`, nog vóór er iets wordt aangemaakt.
- Registreren wordt per bron-IP afgeremd.
- Een nieuw account krijgt de rol `viewer` en is **niet actief**. Een viewer leest alleen, en wijzigt niets tot een beheerder hem een hogere rol geeft (hoofdstuk 2).
- Het antwoord is altijd hetzelfde ("Registration received …"), of het adres nu nieuw is of al bestaat. Ook dat voorkomt het opsommen van accounts. Alleen bij een echt nieuw adres krijgt elke actieve beheerder een e-mail (zie *De mail aan de beheerders* hieronder).
- De pagina toont die bevestiging: het account wacht op activatie.

Een gebruiker kan pas inloggen nadat een beheerder het account activeert (`PATCH /auth/users/{user_id}`, alleen voor beheerders). Hetzelfde endpoint zet ook de globale rol (`viewer`, `analyst`, `supervisor`, `superuser` of `admin`; een andere waarde krijgt `422`); op de pagina *Users* gebeurt dat met een keuzelijst die de vijf rollen van laag naar hoog toont, elk met een tooltip. Niemand wijzigt zijn eigen rol of deactiveert zijn eigen account. De rol van de noodbeheerder verandert niet, en hij kan niet worden gedeactiveerd. De auditregel van het verzoek legt vast wat er veranderde, elk veld van en naar (`request_meta.account_change`). Projecttoegang regelt een beheerder in de projectinstellingen.

**Wachten op goedkeuring, archiveren en verwijderen.** Een account dat nog nooit actief was (een registratie, een eerste aanmelding via CAS of OAuth), *wacht op goedkeuring*: `users.activated_at` is leeg tot een beheerder het voor het eerst activeert. De pagina *Users* toont per account de datum waarop het werd toegevoegd en zijn status (*Waiting for approval*, *Active*, *Inactive*, *Archived*), met de wachtende accounts bovenaan. Elke wijziging (activeren, deactiveren, rol, archiveren, herstellen, verwijderen) vraagt eerst om bevestiging in een dialoogvenster; aanvinken alleen schrijft niets weg.

- **Verwijderen** (`DELETE /auth/users/{user_id}`) kan alleen voor een account dat nooit actief was. Elk ander antwoordt `409` en wordt gearchiveerd. Noemt een record het account toch (zoals een genpanel dat het maakte), dan weigert de databank de verwijdering en antwoordt de backend ook `409`. De auditregel bewaart wie het was (id, e-mailadres, naam, aanmeldwijze). Meldt dezelfde persoon zich later opnieuw aan, dan wacht een nieuw account op goedkeuring.
- **Archiveren** (`PATCH` met `archived: true`) deactiveert het account en haalt het uit de standaardlijst (*Show archived accounts* toont het). De rij blijft, zodat elk record dat het account noemt dat blijft doen, en een nieuwe aanmelding van dezelfde persoon geen nieuwe aanvraag maakt. Een gearchiveerd account wordt eerst hersteld (`archived: false`, het blijft inactief) voor het weer geactiveerd kan worden (anders `409`); de databank laat geen actief gearchiveerd account toe (`users_archived_inactive_check`).
- Niemand archiveert of verwijdert zijn eigen account of dat van de noodbeheerder (`403`).

**De mail aan de beheerders.** Een account dat op goedkeuring wacht, wordt per e-mail gemeld aan elke actieve beheerder, via de mailserver in `SMTP_HOST` (poort `SMTP_PORT`, afzender `SMTP_FROM` of anders `ADMIN_EMAIL`; met `SMTP_USERNAME` en `SMTP_PASSWORD` meldt CoGA zich aan, na `SMTP_STARTTLS`, wat buiten ontwikkeling verplicht is). Zonder `SMTP_HOST` gaat er geen mail uit. De mail noemt naam, e-mailadres, affiliatie, aanmeldwijze en tijdstip, en linkt naar *Users* als CoGA zijn eigen adres kent (dat van `CAS_SERVICE_URL` of `OAUTH_REDIRECT_URL`); ze bevat geen patiëntgegevens. De pagina *Users* toont naar wie de mail gaat en stuurt een testmail (`GET /api/auth/account-request-mail`, `POST /api/auth/account-request-mail/test`). Een mail die niet vertrekt, laat de registratie of aanmelding ongemoeid; de log noemt alleen het type van de fout, nooit een adres of de tekst van de server (SEC-11).

Met Microsoft-aanmelding of aanmelding via CAS of OAuth bestaat registreren niet: `POST /auth/signup` antwoordt `403`, en de eerste aanmelding maakt het account (hieronder).

**Waar in de code:** `signup`, `update_user` en `delete_user` in `backend/app/routers/auth.py`; het aanmaken, wijzigen en verwijderen van het account in `backend/app/services/metadata_service.py`; de mail in `backend/app/services/account_notifications.py`; de minimale lengte in `backend/app/schemas/auth.py`; de pagina in `frontend/src/pages/admin/UserListPage.tsx`.

## Aanmelden met Microsoft (Azure AD / Entra ID)

Bij de ingebruikname meldt iedereen zich met zijn Microsoft-account aan (SEC-4). Microsoft Entra ID controleert dan het wachtwoord en de tweede factor (MFA); IT regelt het opnieuw instellen van een wachtwoord en het afsluiten van wie vertrekt. De backend zet het aan zodra `AZURE_TENANT_ID` (het ID van de organisatie) en `AZURE_CLIENT_ID` (het ID van de app-registratie van CoGA) zijn ingesteld. Het zijn beide of geen van beide: met de helft ervan, een tenant die geen GUID is (zoals `common`) of de noodoverride zonder Microsoft start de backend niet. Wat IT daarvoor aanmaakt, staat in `docs/deployment-gcp.md` §12.12.

**De aanmelding in vogelvlucht.**

1. De inlogpagina vraagt de backend hoe gebruikers zich aanmelden (`GET /auth/config`: het client-ID, de *authority* van de organisatie en de API-scope van CoGA; niets daarvan is geheim). Met Microsoft toont ze alleen de knop *Sign in with Microsoft*, zonder wachtwoordformulier en zonder registratie.
2. De browser gaat met **MSAL** (`@azure/msal-browser`) naar Microsoft, met de *authorization code flow* met **PKCE**, en vraagt een toegangstoken voor de scope van CoGA (`api://<client-id>/access_as_user`). Microsoft vraagt altijd welk account verdergaat, zodat op een gedeelde pc niemand ongevraagd als zijn voorganger binnenkomt. De pagina die de gebruiker vroeg, gaat niet mee naar Microsoft: alleen een willekeurige sleutel, waaronder het pad in de sessieopslag van het tabblad wacht.
3. Microsoft stuurt de browser terug naar `/login`. De pagina rondt de aanmelding af, vraagt met het token het profiel op (`GET /auth/me`), bewaart de sessie en opent de gevraagde pagina, als die van deze site is.
4. Elk volgend verzoek draagt het Microsoft-token. Dat leeft ongeveer een uur. Voor elk verzoek vraagt de API-client MSAL om het token, en MSAL vernieuwt het stil vóór het verloopt. De IGV-browser, die zijn bestanden zelf ophaalt, vraagt het token op dezelfde manier voor elk verzoek op (`sessionAccessToken` in `lib/api.ts`, SEC-32). Vroeger las hij het één keer, bij het openen, en verloor een lange IGV-sessie na een uur haar tracks. MSAL bewaart zijn tokens in de sessieopslag van het tabblad; die verdwijnen als het tabblad sluit.

**Het token controleren.** `get_current_user` kiest de controle op het algoritme in de kop van het token: RS256 is een Microsoft-token, HS256 een lokaal token. Een Microsoft-token wordt aanvaard als het:

- ondertekend is met een van de gepubliceerde sleutels van de organisatie (opgehaald uit haar OpenID-configuratie, een dag bewaard, en voor een onbekende sleutel hoogstens om de vijf minuten opnieuw opgehaald);
- een token van versie 2.0 is van de eigen organisatie (`iss`, `tid`);
- voor de API van CoGA is en door CoGA zelf gevraagd (`aud` en `azp`: het client-ID);
- de scope van CoGA draagt (`scp`; het token van een applicatie zelf heeft er geen);
- niet verlopen is (met een minuut speling).

Een geweigerd token krijgt `401`. De reden (nooit het token) gaat naar de log, behalve bij een verlopen token.

**Het account.** De backend zoekt het account op het object-ID van de gebruiker in Entra ID (`oid`, kolom `users.azure_object_id`), nooit op het e-mailadres. Bij de eerste aanmelding maakt hij het: rol `viewer`, actief, lid van geen enkel project, en dus zonder toegang tot patiëntgegevens tot een beheerder het aan een project toevoegt. Wijzigen kan het pas als een beheerder het een hogere rol geeft (hoofdstuk 2). Het account heeft geen wachtwoord (`hashed_password` bevat een merkteken waar geen wachtwoord bij past). E-mailadres en naam komen uit het token (`email`, anders `preferred_username`; `given_name` en `family_name`) en volgen het token als ze veranderen. Wat een gebruiker als het zijne bewaart, hangt daarom aan het account (zijn id), nooit aan het e-mailadres of de gebruikersnaam (SEC-32). De aanvrager van een importjob (hoofdstuk 6) en zijn filterpresets (hoofdstuk 10) blijven van hem als die veranderen. Een account dat zich later aanmeldt met het adres of de naam die een ander achterliet, krijgt er niets van. Heeft een ander account dat e-mailadres of die gebruikersnaam al, dan weigert de backend de eerste aanmelding (`403`) en koppelt hij niets: een bestaand account wordt nooit via een e-mailadres overgenomen. Een gedeactiveerd account krijgt `401` met de melding dat het niet actief is. De auditregel van het verzoek dat het account maakte, zegt dat (`request_meta.account_change`).

**De noodbeheerder.** Naast Microsoft bestaat één lokaal account: de noodbeheerder (*break-glass*). Dat is het account dat `ADMIN_USERNAME` noemt (`coga-admin` op Google Cloud), met de rol `admin`: een account met die naam en een andere rol, ook `superuser`, is geen noodbeheerder. CoGA laat zijn rol niet wijzigen en laat hem niet deactiveren, zodat de noodaanmelding werkt zodra de override aan staat. Zijn wachtwoord is `ADMIN_PASSWORD`: de seed vervangt de opgeslagen hash zodra die er niet meer bij past, dus wie het geheim roteert, roteert het wachtwoord. Hij meldt zich aan op `/login/emergency`, een pagina waarnaar de inlogpagina niet verwijst, en alleen zolang `AZURE_ADMIN_OVERRIDE` aan staat (standaard uit). Elk ander account, ook een andere beheerder, krijgt daar `403`; een lokaal token dat niet van de noodbeheerder is, krijgt `401`. Elke noodaanmelding en elk verzoek dat ermee gebeurt, schrijft een waarschuwing (`azure_admin_override`) naar de log en wordt in de auditregel gemarkeerd (`request_meta.emergency_sign_in`, *Emergency sign-in* op de pagina *Audit logs*). Zet de override alleen aan voor een noodgeval en daarna weer uit.

**Afmelden.** *Logout* wist de sessie van CoGA en de tokens van MSAL in het tabblad. UI-events die de API bij het afmelden niet aannam (het token was al verlopen, of het laatste verzoek kwam niet aan), gaan niet verloren: ze wachten in de browser onder het account tot dat zich opnieuw aanmeldt (hoofdstuk 15). De aanmelding bij Microsoft in de browser blijft; de inlogpagina biedt daarna *Sign out of Microsoft* aan, wat ook die beëindigt (aan te raden op een gedeelde pc).

Afmelden in één tabblad beëindigt de sessie in elk tabblad van de browser. De tabbladen delen de sessie, en de browser meldt elk ander tabblad dat het token weg is. Dat tabblad wist dan zijn gegevens in de cache en de tokens van MSAL, en toont de inlogpagina (`Layout.tsx`). Vroeger bleef een tabblad dat het token zelf had geschreven (bij een aanmelding of een vernieuwd Microsoft-token) na zo'n afmelding aangemeld: het toonde de gegevens van de gebruiker, en zijn volgende vernieuwing meldde elk tabblad weer aan (gevonden bij SEC-36).

Meldt in een ander tabblad een ander account zich aan (op een inlogpagina die daar nog openstond), dan eindigt de sessie op dezelfde manier. De andere tabbladen zien de gebruikersnaam, het adres van het account, veranderen. Ook de sessie van dat andere account wordt gewist, zodat geen tabblad verdergaat als een account dat het niet toont; dat account meldt zich opnieuw aan. Een vernieuwd token, en een nieuwe aanmelding van hetzelfde account, laten de gebruikersnaam staan, en de sessie loopt door. Vroeger verving zo'n aanmelding de sessie zonder afmelden: een tabblad toonde dan nog de pagina's van het eerste account, terwijl zijn verzoeken, en dus wat de audit vastlegt over wie wat deed, als het andere account vertrokken (gevonden bij SEC-36).

**Waar in de code:** `backend/app/core/azure.py` (de controle van het token), `backend/app/services/user_accounts.py` (het account en de noodbeheerder), de Microsoft-tak in `get_current_user` (`backend/app/dependencies.py`), `GET /auth/config` in `backend/app/routers/auth.py`, en in de browser `frontend/src/lib/microsoftSignIn.ts` en `frontend/src/pages/auth/LoginPage.tsx`.

## Aanmelden via CAS (login.ugent.be)

Biedt de organisatie geen app-registratie in Entra ID aan maar een CAS-server, dan meldt iedereen zich daar aan (SEC-4). Aan de UGent is dat `login.ugent.be`, dat zelf via Microsoft Entra ID aanmeldt. De backend zet het aan met `CAS_SERVER_URL` (de CAS-server, `https://login.ugent.be`) en `CAS_SERVICE_URL` (de inlogpagina van CoGA zelf, `https://<host>/login`). Het zijn beide of geen van beide; naast Microsoft-aanmelding, met een adres dat geen HTTPS is (buiten ontwikkeling) of met een andere service dan `/login` start de backend niet. `CAS_DISPLAY_NAME` (standaard `UGent`) is de naam op de knop.

De CAS-server geeft CoGA de attributen die bij de registratie van de applicatie gekozen zijn. CoGA heeft er vier nodig: `azureObjectId` (het object-ID van de gebruiker in Entra ID), `mail`, `givenName` en `surname` (de namen zijn instelbaar met `CAS_*_ATTRIBUTE`). Zonder object-ID weigert CoGA de aanmelding.

1. De inlogpagina vraagt de backend hoe gebruikers zich aanmelden (`GET /auth/config`: de inlogpagina van de CAS-server voor de service van CoGA, en zijn afmeldpagina). Met CAS toont ze alleen de knop *Sign in with UGent*, zonder wachtwoordformulier en zonder registratie.
2. De browser gaat naar de CAS-server. De pagina die de gebruiker vroeg, wacht in de sessieopslag van het tabblad: de CAS-server stuurt de browser terug naar precies `/login`.
3. De CAS-server stuurt de browser terug naar `/login` met een *service ticket*, dat één keer bruikbaar is. De pagina haalt het uit de adresbalk en geeft het aan de API (`POST /auth/cas/session`).
4. De backend controleert het ticket bij de CAS-server (`/serviceValidate`, over HTTPS, voor de service van CoGA). Een waarde die geen service ticket is, gaat niet naar de CAS-server. Het antwoord leest hij alleen als het een CAS-antwoord is van hoogstens 64 KiB, zonder documenttype, zodat er geen entiteit wordt uitgevouwen.
5. Het account hoort bij het object-ID, nooit bij een e-mailadres: het is hetzelfde account dat een latere Microsoft-aanmelding vindt. Is er nog geen, dan maakt de eerste aanmelding het aan als viewer zonder project, **inactief** en wachtend op goedkeuring, en krijgt elke actieve beheerder een mail. CAS laat elk account van de organisatie binnen, ook studenten: daarom wacht het nieuwe account op een beheerder, die het op de pagina *Users* activeert. Tot dan antwoordt de aanmelding `403` met die boodschap.
6. Is het account actief, dan krijgt de browser het eigen token van CoGA (HS256), zoals bij een wachtwoord. Met CAS meldt zo'n token alleen een account aan dat aan een object-ID gebonden is, of de noodbeheerder zolang `AZURE_ADMIN_OVERRIDE` aan staat.

Een geweigerd ticket antwoordt `401` en telt mee voor het bron-IP (`auth_login_attempts`, scope `cas_ip`); na te veel weigeringen antwoordt de aanmelding `429`. Een geslaagde aanmelding telt niet, zodat collega's achter hetzelfde adres elkaar niet ophouden. Het ticket en het antwoord van de CAS-server komen niet in de log. **Afmelden** wist de sessie van CoGA; de inlogpagina biedt daarna *Sign out of UGent* aan, dat de sessie op de CAS-server beëindigt.

**Waar in de code:** `backend/app/core/cas.py` (het ticket en het antwoord), `cas_session` in `backend/app/routers/auth.py`, `_redirect_session_user` in `backend/app/dependencies.py`, `backend/app/services/user_accounts.py` (het account), en in de browser `frontend/src/lib/casSignIn.ts` en `frontend/src/pages/auth/LoginPage.tsx`.

## Aanmelden via OAuth (OAuth-applicatie van de UGent)

Registreert de organisatie CoGA als OAuth-applicatie, zoals de ICT van de UGent via haar formulier doet, dan meldt iedereen zich aan met de *authorization code flow* met PKCE (SEC-4). De backend zet het aan met zes instellingen, alle zes of geen: `OAUTH_AUTHORIZE_URL`, `OAUTH_TOKEN_URL` en `OAUTH_USERINFO_URL` (de adressen van de server), `OAUTH_REDIRECT_URL` (de inlogpagina van CoGA zelf, `https://<host>/login`), `OAUTH_CLIENT_ID` en `OAUTH_CLIENT_SECRET`. De adressen en de client-gegevens staan op de pagina waar ICT de credentials geeft. Naast Microsoft- of CAS-aanmelding, met een adres dat geen HTTPS is (buiten ontwikkeling), met een andere redirect dan `/login` of met een plaatshouder als secret start de backend niet. `OAUTH_DISPLAY_NAME` (standaard `UGent`) is de naam op de knop; `OAUTH_SCOPE`, `OAUTH_TOKEN_AUTH_METHOD` (het secret in de Basic-header, standaard, of in het formulier) en `OAUTH_LOGOUT_URL` zijn optioneel.

De gebruikersinfo moet dezelfde vier velden bevatten als bij CAS: `azureObjectId`, `mail`, `givenName` en `surname` (instelbaar met `OAUTH_*_CLAIM`). Een veld mag bovenaan staan of in een object `attributes`, met één waarde of een lijst.

1. De inlogpagina vraagt de backend hoe gebruikers zich aanmelden (`GET /auth/config`: de autorisatiepagina voor de client van CoGA, en de afmeldpagina als die er is). Ze toont alleen de knop *Sign in with UGent*.
2. Het tabblad maakt een eenmalige `state` en een *code verifier*, en bewaart ze met de gevraagde pagina in zijn sessieopslag. De browser gaat naar de autorisatiepagina met de `state` en de S256-*challenge* van de verifier; de verifier zelf gaat niet mee.
3. De server stuurt de browser terug naar `/login` met een eenmalige *code* en dezelfde `state`. De pagina haalt beide uit de adresbalk en neemt de code alleen aan als de `state` die van dit tabblad is: zo kan niemand een tabblad aanmelden met zijn eigen code. Ze geeft de code en de verifier aan de API (`POST /auth/oauth/session`). Weigert de server, dan toont de pagina alleen de foutcode (zoals `access_denied`), nooit de beschrijving.
4. De backend wisselt de code in bij `OAUTH_TOKEN_URL` (over HTTPS, met client-ID, secret, redirect en verifier) en haalt met het access token de gebruikersinfo op bij `OAUTH_USERINFO_URL`, die hij alleen leest als JSON-object van hoogstens 64 KiB. Een waarde die geen code of verifier is, gaat niet naar de server. De server geeft een code maar één keer, alleen aan de client van CoGA en alleen met de juiste verifier: een code uit de adresbalk is voor een ander niets waard.
5. Het account hoort bij het object-ID, zoals bij CAS: een eerste aanmelding maakt een **inactief** account dat wacht op goedkeuring, en elke actieve beheerder krijgt een mail.
6. Is het account actief, dan krijgt de browser het eigen token van CoGA (HS256), met dezelfde regel als bij CAS.

Een geweigerde code antwoordt `401` en telt mee voor het bron-IP (scope `oauth_ip`). Het secret staat alleen in `.env`: nooit in de log, in een antwoord of in de browser. De code, de verifier, het token en het antwoord van de server komen niet in de log; bij een weigering logt de backend alleen de foutcode van de server. De auditregel van het verzoek maskeert `authorization_code` en `code_verifier` (`SENSITIVE_KEY_PREFIXES`). **Afmelden** wist de sessie van CoGA; met `OAUTH_LOGOUT_URL` biedt de inlogpagina daarna *Sign out of UGent* aan.

**Waar in de code:** `backend/app/core/oauth.py` (de code inwisselen en de gebruikersinfo lezen), `oauth_session` in `backend/app/routers/auth.py`, en in de browser `frontend/src/lib/oauthSignIn.ts` en `frontend/src/pages/auth/LoginPage.tsx`.

## Veiligheid en traceerbaarheid

- **Elk verzoek wordt gelogd,** ook elke loginpoging, met statuscode, tijdstip, bron-IP en user-agent (hoofdstuk 7). Een mislukte login is herkenbaar aan `400`, `403` of `429`.
- **Wachtwoorden en tokens komen niet in de logs.** De auditlog maskeert gevoelige velden, ook in het formulier van `/auth/token`.
- **De tabel `auth_login_attempts`** toont per e-mailadres en IP de mislukkingen en blokkeringen: bruikbaar om een aanval te herkennen.
- **De noodaanmelding laat altijd een spoor na** in de log en in de auditregel van elk verzoek (`request_meta.emergency_sign_in`).
- **Een account dat de eerste Microsoft-, CAS- of OAuth-aanmelding maakte, en elke wijziging van rol of activatie,** staan in de auditregel van het verzoek, elk veld van en naar (`request_meta.account_change`).
- **Korte tokens en uitloggen bij `401`** beperken de gevolgen van een gelekt token.

## Belangrijkste bestanden

| Bestand | Rol |
| --- | --- |
| `backend/app/routers/auth.py` | Login, token, registratie, profiel en gebruikersbeheer |
| `backend/app/dependencies.py` | Wachtwoorden (bcrypt), tokens maken en controleren, Microsoft-tak en noodbeheerder |
| `backend/app/core/azure.py` | Controle van Microsoft-tokens |
| `backend/app/core/cas.py` | Controle van een CAS-ticket bij de CAS-server |
| `backend/app/core/oauth.py` | Een OAuth-code inwisselen en de gebruikersinfo lezen |
| `backend/app/services/user_accounts.py` | Het account van een Microsoft-aanmelding; de noodbeheerder |
| `backend/app/services/auth_rate_limit_pg.py` | Rate limiting en lockout |
| `backend/app/schemas/auth.py` | Invoer van login en registratie (minimale wachtwoordlengte) |
| `backend/db/schema/postgres/01_access.sql` | De tabellen `users` en `auth_login_attempts` |
| `frontend/src/pages/auth/LoginPage.tsx` · `SignupPage.tsx` | Inloggen (ook met Microsoft en op `/login/emergency`) en registreren |
| `frontend/src/lib/microsoftSignIn.ts` | De aanmelding met Microsoft in de browser (MSAL) |
| `frontend/src/lib/casSignIn.ts` | De aanmelding via CAS in de browser |
| `frontend/src/lib/oauthSignIn.ts` | De aanmelding via OAuth in de browser (state en PKCE) |
| `frontend/src/lib/auth.ts` · `frontend/src/lib/storage.ts` | De sessie in de browser |
