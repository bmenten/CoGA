# 5. Login & authenticatie

Dit hoofdstuk beschrijft hoe een gebruiker toegang krijgt tot CoGA: hoe het inlogformulier zijn gegevens naar de backend stuurt, hoe wachtwoorden veilig worden bewaard, hoe een toegangstoken wordt uitgegeven en bij elk volgend verzoek gecontroleerd, hoe de sessie in de browser leeft en hoe het raden van wachtwoorden wordt afgeremd. Ook de optionele aanmelding via Azure AD en het registreren van een nieuw account komen aan bod. Wat een ingelogde gebruiker daarna mag zien, staat in [hoofdstuk 2](02-beveiliging-rollen-rechten.md).

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
2. **De gebruiker opzoeken en het wachtwoord controleren.** Een onbekend adres of een verkeerd wachtwoord geeft dezelfde melding: `400 Incorrect email or password`.
3. **Controleren of het account actief is.** Een nog niet geactiveerd account krijgt `403 User not active`. Ook die poging telt als mislukt.
4. **Bij succes:** de teller van mislukte pogingen wissen en een token uitgeven.

Het antwoord bevat het token, het type (`bearer`) en de rol van de gebruiker, zodat de frontend weet wat hij moet tonen.

**Bescherming tegen accountopsomming.** Zou de server bij een onbekend adres meteen "nee" zeggen, dan kon een aanvaller aan de *responstijd* zien welke adressen bestaan: de bcrypt-controle is bewust traag. Daarom voert de code bij een onbekend adres toch een controle uit tegen een vaste dummy-hash. Beide gevallen duren dus ongeveer even lang en geven dezelfde melding. Het hashen draait in een aparte thread, zodat een trage controle de server niet ophoudt.

**Waar in de code:** `_authenticate_and_issue_token` in `backend/app/routers/auth.py`.

## Wachtwoorden: bcrypt

Wachtwoorden worden nooit leesbaar bewaard. De backend hasht ze met **bcrypt**, dat hij rechtstreeks aanroept. Bcrypt is bewust traag, zodat een aanvaller die de databank buitmaakt niet snel miljoenen wachtwoorden kan uitproberen. De hash staat in de kolom `hashed_password` van `users`.

Bcrypt leest alleen de eerste 72 bytes van een wachtwoord. De backend kapt een langer wachtwoord daarom zelf af op 72 bytes, bij het aanmaken van de hash én bij de controle, zodat beide altijd hetzelfde deel vergelijken. Een opgeslagen waarde die geen bcrypt-hash is (bv. een account zonder lokaal wachtwoord), geeft gewoon geen match.

**Waar in de code:** `get_password_hash` en `verify_password` in `backend/app/dependencies.py`; de tests in `backend/tests/test_password_hashing.py`.

## Het token

Een geslaagde login levert een JWT op:

| Eigenschap | Waarde |
| --- | --- |
| Claim `sub` | Het e-mailadres van de gebruiker |
| Claim `exp` | Het verloopmoment |
| Levensduur | Kort: standaard twee uur (`ACCESS_TOKEN_EXPIRE_MINUTES`) |
| Handtekening | HS256 (symmetrisch), met `SECRET_KEY` |

De korte levensduur is een bewuste keuze: het token staat in de browseropslag (niet in een afgeschermde HttpOnly-cookie), dus als het lekt, is de schade beperkt tot die periode. Wie `SECRET_KEY` kent, kan geldige tokens maken; daarom weigert de backend buiten ontwikkeling een zwakke sleutel ([hoofdstuk 2](02-beveiliging-rollen-rechten.md#weigering-te-starten-met-zwakke-geheimen)).

**Waar in de code:** `create_access_token` in `backend/app/dependencies.py`; de instellingen in `backend/app/core/config.py`.

### Het token controleren: `get_current_user`

Elk beschermd endpoint hangt af van `get_current_user`, op twee na: de download van het QC-rapport, die alleen zijn eigen ondertekende link van vijf minuten controleert (hoofdstuk 2), en `GET /metrics`, dat een eigen token vraagt (hoofdstuk 7). Die functie:

1. haalt het token uit de header `Authorization: Bearer …`;
2. controleert de handtekening en de geldigheid; een ongeldig of verlopen token geeft `401 Could not validate credentials`;
3. leest het e-mailadres uit de claim `sub`;
4. laadt de gebruiker vers uit Postgres en controleert dat die bestaat en actief is; zo niet, opnieuw `401`;
5. hangt de gebruiker aan het verzoek (`request.state.current_user`), zodat de auditlog weet wie het verzoek deed;
6. weigert (`400`) een verzoek met een stuurteken (C0 of DEL, zoals een NUL uit `%00`) in een padparameter, in de naam van een queryparameter of in de waarde van `family_id` of `sample_id` in de querystring, en een verzoek met een NUL in eender welke querywaarde. Een familie- of staal-ID mag zo'n teken niet bevatten, en Postgres kan een NUL niet vergelijken: zo'n verzoek liep vroeger vast op een `500`. Een gen- of intervallijst, één per regel getypt, behoudt haar regeleinden. De weigering komt na de aanmelding, zodat de auditlog de gebruiker noemt, en vóór een route iets opzoekt, met hetzelfde antwoord voor iedereen (`request_value_problem` in `backend/app/services/family_identifiers.py`).

`get_current_admin_user` bouwt daarop voort en eist een beheerdersrol (`admin` of `superuser`); anders volgt `403 Admin access required`.

**Waar in de code:** `get_current_user` en `get_current_admin_user` in `backend/app/dependencies.py`.

## De frontend: van formulier tot sessie

De inlogpagina (`frontend/src/pages/auth/LoginPage.tsx`) stuurt `POST /auth/login`, vraagt met het verse token het profiel op (`GET /auth/me`) en bewaart dan de sessie: het token, het e-mailadres en de rol. Daarna gaat ze naar de gevraagde pagina uit de parameter `next`. Die wordt eerst gefilterd: alleen een pad binnen de app mag (geen `//` of externe URL), zodat een aanvaller je na het inloggen niet naar een andere site kan sturen (*open redirect*).

De sessie staat in de browseropslag (`localStorage`); is die niet beschikbaar, dan valt de app terug op opslag in het geheugen. Hoe de API-client het token meestuurt en bij een `401` uitlogt, staat in [hoofdstuk 1](01-architectuur.md); de routebewakers in hoofdstuk 2.

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

Het bron-IP is het IP dat de middleware achter de proxies vaststelt (`TRUSTED_PROXY_HOPS`, hoofdstuk 2), zodat een client het zelf niet kan kiezen. Registreren wordt apart en alleen per bron-IP afgeremd: bij het opsommen van accounts probeert een aanvaller juist telkens een ander adres.

**Waar in de code:** `backend/app/services/auth_rate_limit_pg.py`; de tabel in `backend/db/schema/postgres/01_access.sql`.

## Registreren en goedkeuren

Iedereen kan zich registreren via `POST /auth/signup` (pagina `frontend/src/pages/auth/SignupPage.tsx`), maar dat geeft **geen** toegang:

- Het wachtwoord moet minstens 15 tekens lang zijn (naar NIST SP 800-63B-4, voor een wachtwoord dat de enige factor is). Een korter wachtwoord krijgt `422`, nog vóór er iets wordt aangemaakt.
- Registreren wordt per bron-IP afgeremd.
- Een nieuw account krijgt de rol `viewer` en is **niet actief**.
- Het antwoord is altijd hetzelfde ("Registration received …"), of het adres nu nieuw is of al bestaat. Ook dat voorkomt het opsommen van accounts. Alleen bij een echt nieuw adres krijgt de beheerder een e-mail, als `ADMIN_EMAIL` is ingesteld.
- De pagina toont die bevestiging: het account wacht op activatie.

Een gebruiker kan pas inloggen nadat een beheerder het account activeert (`PATCH /auth/users/{user_id}`, alleen voor beheerders); dat endpoint zet alleen actief of inactief. Een rol wijzigen kan in CoGA niet: er is geen endpoint of scherm voor, alleen een ingreep rechtstreeks in de databank. Projecttoegang regelt een beheerder in de projectinstellingen.

**Waar in de code:** `signup` en `update_user` in `backend/app/routers/auth.py`; het aanmaken van het account in `backend/app/services/metadata_service.py`; de minimale lengte in `backend/app/schemas/auth.py`.

## Optioneel: Azure AD

De backend kan tokens van **Azure AD** (Microsofts identiteitsdienst) aanvaarden zodra `AZURE_TENANT_ID` en `AZURE_CLIENT_ID` zijn ingesteld. De webinterface kent geen aanmelding via Azure: de inlogpagina vraagt altijd een lokaal token (`POST /auth/login`). Met Azure ingesteld raakt via de interface dus alleen een beheerder binnen, en dan enkel met de noodoverride hieronder. De backend controleert het token dan als een Azure-token: hij haalt Microsofts publieke sleutels op (met cache, en één keer opnieuw als Azure van sleutel wisselde), controleert de handtekening (RS256), de *audience* (de client-id) en de *issuer*, en leest het e-mailadres uit `preferred_username` of `email`. Is Azure ingesteld, dan aanvaardt de backend geen lokaal uitgegeven tokens meer, behalve via de noodoverride hieronder.

**Noodoverride.** Faalt de Azure-controle én staat `AZURE_ADMIN_OVERRIDE` aan (standaard uit), dan probeert de server het token als lokaal token te lezen. Dat werkt alleen voor beheerders, en elk gebruik schrijft een waarschuwing naar de log, zodat een per ongeluk ingeschakelde override opvalt. Het is bedoeld om binnen te raken als de koppeling met Azure stuk is.

**Waar in de code:** `backend/app/core/azure.py` en de Azure-tak in `get_current_user` (`backend/app/dependencies.py`).

## Veiligheid en traceerbaarheid

- **Elk verzoek wordt gelogd,** ook elke loginpoging, met statuscode, tijdstip, bron-IP en user-agent (hoofdstuk 7). Een mislukte login is herkenbaar aan `400`, `403` of `429`.
- **Wachtwoorden en tokens komen niet in de logs.** De auditlog maskeert gevoelige velden, ook in het formulier van `/auth/token`.
- **De tabel `auth_login_attempts`** toont per e-mailadres en IP de mislukkingen en blokkeringen: bruikbaar om een aanval te herkennen.
- **De noodoverride laat altijd een spoor na** in de log.
- **Korte tokens en uitloggen bij `401`** beperken de gevolgen van een gelekt token.

## Belangrijkste bestanden

| Bestand | Rol |
| --- | --- |
| `backend/app/routers/auth.py` | Login, token, registratie, profiel en gebruikersbeheer |
| `backend/app/dependencies.py` | Wachtwoorden (bcrypt), tokens maken en controleren, Azure-tak |
| `backend/app/core/azure.py` | Controle van Azure-tokens |
| `backend/app/services/auth_rate_limit_pg.py` | Rate limiting en lockout |
| `backend/app/schemas/auth.py` | Invoer van login en registratie (minimale wachtwoordlengte) |
| `backend/db/schema/postgres/01_access.sql` | De tabellen `users` en `auth_login_attempts` |
| `frontend/src/pages/auth/LoginPage.tsx` · `SignupPage.tsx` | Inloggen en registreren |
| `frontend/src/lib/auth.ts` · `frontend/src/lib/storage.ts` | De sessie in de browser |
