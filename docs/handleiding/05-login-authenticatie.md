# 5. Login & authenticatie

Dit hoofdstuk beschrijft hoe een gebruiker toegang krijgt tot CoGA. Bij de ingebruikname meldt iedereen zich aan met zijn Microsoft-account van het ziekenhuis of de universiteit (Microsoft Entra ID, vroeger Azure AD); dat staat verderop, in *Aanmelden met Microsoft*. Daarnaast bestaat het lokale aanmelden met e-mailadres en wachtwoord: in ontwikkeling voor iedereen, en naast Microsoft alleen voor de noodbeheerder. Voor dat lokale aanmelden beschrijft het hoofdstuk hoe het inlogformulier zijn gegevens naar de backend stuurt, hoe wachtwoorden veilig worden bewaard, hoe een toegangstoken wordt uitgegeven en bij elk volgend verzoek gecontroleerd, hoe de sessie in de browser leeft en hoe het raden van wachtwoorden wordt afgeremd. Ook het registreren van een nieuw account komt aan bod. Wat een ingelogde gebruiker daarna mag zien, staat in [hoofdstuk 2](02-beveiliging-rollen-rechten.md).

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

De inlogpagina (`frontend/src/pages/auth/LoginPage.tsx`) stuurt `POST /auth/login`, vraagt met het verse token het profiel op (`GET /auth/me`) en bewaart dan de sessie: het token, het e-mailadres en de rol. Daarna gaat ze naar de gevraagde pagina uit de parameter `next`. Die wordt eerst gefilterd: alleen een pad binnen de app mag, in de vorm die de routebewakers zelf schrijven (het pad, de querystring en de hash van de pagina, zoals de browser ze procent-gecodeerd bijhoudt). Dat is een `/`, dan een letter of cijfer, en verder alleen zichtbare ASCII-tekens zonder backslash. Een browser leest zo'n waarde altijd als een pad op deze site. Al het andere opent het dashboard, net als een ontbrekende `next`: een externe URL, `//`, een backslash, een spatie of een stuurteken, en een pad dat met een procent-gecodeerd teken begint, zoals `/%2F/host`. Een browser leest een backslash als een slash en laat een tab of regeleinde weg, dus `next=/%5Chost` of `next=/%09/host` in de adresbalk is voor hem `//host`, een andere site. Zo kan een aanvaller je na het inloggen niet naar een andere site sturen (*open redirect*), en loopt de pagina na het inloggen niet vast op "This view could not be displayed" (React Router weigert zo'n adres).

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

Een gebruiker kan pas inloggen nadat een beheerder het account activeert (`PATCH /auth/users/{user_id}`, alleen voor beheerders). Hetzelfde endpoint zet ook de globale rol (`viewer`, `superuser` of `admin`), op de pagina *Users* via een keuzelijst die eerst om bevestiging vraagt. Niemand wijzigt zijn eigen rol, en de rol van de noodbeheerder verandert niet; hij kan ook niet worden gedeactiveerd. De auditregel van het verzoek legt vast wat er veranderde, elk veld van en naar (`request_meta.account_change`). Projecttoegang regelt een beheerder in de projectinstellingen.

Met Microsoft-aanmelding bestaat registreren niet: `POST /auth/signup` antwoordt `403`, en de eerste aanmelding met Microsoft maakt het account (hieronder).

**Waar in de code:** `signup` en `update_user` in `backend/app/routers/auth.py`; het aanmaken van het account in `backend/app/services/metadata_service.py`; de minimale lengte in `backend/app/schemas/auth.py`.

## Aanmelden met Microsoft (Azure AD / Entra ID)

Bij de ingebruikname meldt iedereen zich aan met zijn Microsoft-account van het ziekenhuis of de universiteit (SEC-4). Microsoft Entra ID (vroeger Azure AD) controleert dan het wachtwoord en de tweede factor (MFA); IT regelt het opnieuw instellen van een wachtwoord en het afsluiten van wie vertrekt. De backend zet het aan zodra `AZURE_TENANT_ID` (het ID van de organisatie) en `AZURE_CLIENT_ID` (het ID van de app-registratie van CoGA) zijn ingesteld. Beide of geen van beide: de helft ervan, een tenant die geen GUID is (zoals `common`) of de noodoverride zonder Microsoft laat de backend niet starten. Wat IT daarvoor aanmaakt, staat in `docs/deployment-gcp.md` §12.12.

**De aanmelding in vogelvlucht.**

1. De inlogpagina vraagt de backend hoe gebruikers zich aanmelden (`GET /auth/config`: het client-ID, de *authority* van de organisatie en de API-scope van CoGA; niets daarvan is geheim). Met Microsoft toont ze alleen de knop *Sign in with Microsoft*, zonder wachtwoordformulier en zonder registratie.
2. De browser gaat met **MSAL** (`@azure/msal-browser`) naar Microsoft, met de *authorization code flow* met **PKCE**, en vraagt een toegangstoken voor de scope van CoGA (`api://<client-id>/access_as_user`). Microsoft vraagt altijd welk account verdergaat, zodat op een gedeelde pc niemand ongevraagd als zijn voorganger binnenkomt. De pagina die de gebruiker vroeg, gaat niet mee naar Microsoft: alleen een willekeurige sleutel, waaronder het pad in de sessieopslag van het tabblad wacht.
3. Microsoft stuurt de browser terug naar `/login`. De pagina rondt de aanmelding af, vraagt met het token het profiel op (`GET /auth/me`), bewaart de sessie en opent de gevraagde pagina, als die van deze site is.
4. Elk volgend verzoek draagt het Microsoft-token. Dat leeft ongeveer een uur: voor elk verzoek vraagt de API-client MSAL om het token, en MSAL vernieuwt het stil vóór het verloopt. MSAL bewaart zijn tokens in de sessieopslag van het tabblad; die verdwijnen als het tabblad sluit.

**Het token controleren.** `get_current_user` kiest de controle op het algoritme in de kop van het token: RS256 is een Microsoft-token, HS256 een lokaal token. Een Microsoft-token wordt aanvaard als het ondertekend is met een van de gepubliceerde sleutels van de organisatie (opgehaald uit haar OpenID-configuratie, een dag bewaard, en voor een onbekende sleutel hoogstens om de vijf minuten opnieuw opgehaald), een token van versie 2.0 is van de eigen organisatie (`iss`, `tid`), voor de API van CoGA is en door CoGA zelf gevraagd (`aud` en `azp`: het client-ID), de scope van CoGA draagt (`scp`; het token van een applicatie zelf heeft er geen) en niet verlopen is (met een minuut speling). Een geweigerd token krijgt `401`; de reden (nooit het token) gaat naar de log, behalve bij een verlopen token.

**Het account.** De backend zoekt het account op het object-ID van de gebruiker in Entra ID (`oid`, kolom `users.azure_object_id`), nooit op het e-mailadres. Bij de eerste aanmelding maakt hij het: rol `viewer`, actief, lid van geen enkel project, en dus zonder toegang tot patiëntgegevens tot een beheerder het aan een project toevoegt. Het account heeft geen wachtwoord (`hashed_password` bevat een merkteken waar geen wachtwoord bij past). E-mailadres en naam komen uit het token (`email`, anders `preferred_username`; `given_name` en `family_name`) en volgen het token als ze veranderen. Heeft een ander account dat e-mailadres of die gebruikersnaam al, dan weigert de backend de eerste aanmelding (`403`) en koppelt hij niets: een bestaand account wordt nooit via een e-mailadres overgenomen. Een gedeactiveerd account krijgt `401` met de melding dat het niet actief is. De auditregel van het verzoek dat het account maakte, zegt dat (`request_meta.account_change`).

**De noodbeheerder.** Naast Microsoft bestaat één lokaal account: de noodbeheerder (*break-glass*), het account dat `ADMIN_USERNAME` noemt (`coga-admin` op Google Cloud), met een beheerdersrol die CoGA niet laat wijzigen, en actief: CoGA laat hem niet deactiveren, zodat de noodaanmelding werkt zodra de override aan staat. Zijn wachtwoord is `ADMIN_PASSWORD`: de seed vervangt de opgeslagen hash zodra die er niet meer bij past, dus wie het geheim roteert, roteert het wachtwoord. Hij meldt zich aan op `/login/emergency`, een pagina waarnaar de inlogpagina niet verwijst, en alleen zolang `AZURE_ADMIN_OVERRIDE` aan staat (standaard uit). Elk ander account, ook een andere beheerder, krijgt daar `403`, en een lokaal token dat niet van de noodbeheerder is, `401`. Elke noodaanmelding en elk verzoek dat ermee gebeurt, schrijft een waarschuwing (`azure_admin_override`) naar de log en wordt in de auditregel gemarkeerd (`request_meta.emergency_sign_in`, *Emergency sign-in* op de pagina *Audit logs*). Zet de override alleen aan voor een noodgeval en daarna weer uit.

**Afmelden.** *Logout* wist de sessie van CoGA en de tokens van MSAL in het tabblad. De aanmelding bij Microsoft in de browser blijft; de inlogpagina biedt daarna *Sign out of Microsoft* aan, wat ook die beëindigt (aan te raden op een gedeelde pc).

**Waar in de code:** `backend/app/core/azure.py` (de controle van het token), `backend/app/services/user_accounts.py` (het account en de noodbeheerder), de Microsoft-tak in `get_current_user` (`backend/app/dependencies.py`), `GET /auth/config` in `backend/app/routers/auth.py`, en in de browser `frontend/src/lib/microsoftSignIn.ts` en `frontend/src/pages/auth/LoginPage.tsx`.

## Veiligheid en traceerbaarheid

- **Elk verzoek wordt gelogd,** ook elke loginpoging, met statuscode, tijdstip, bron-IP en user-agent (hoofdstuk 7). Een mislukte login is herkenbaar aan `400`, `403` of `429`.
- **Wachtwoorden en tokens komen niet in de logs.** De auditlog maskeert gevoelige velden, ook in het formulier van `/auth/token`.
- **De tabel `auth_login_attempts`** toont per e-mailadres en IP de mislukkingen en blokkeringen: bruikbaar om een aanval te herkennen.
- **De noodaanmelding laat altijd een spoor na** in de log en in de auditregel van elk verzoek (`request_meta.emergency_sign_in`).
- **Een account dat de eerste Microsoft-aanmelding maakte, en elke wijziging van rol of activatie,** staan in de auditregel van het verzoek, elk veld van en naar (`request_meta.account_change`).
- **Korte tokens en uitloggen bij `401`** beperken de gevolgen van een gelekt token.

## Belangrijkste bestanden

| Bestand | Rol |
| --- | --- |
| `backend/app/routers/auth.py` | Login, token, registratie, profiel en gebruikersbeheer |
| `backend/app/dependencies.py` | Wachtwoorden (bcrypt), tokens maken en controleren, Microsoft-tak en noodbeheerder |
| `backend/app/core/azure.py` | Controle van Microsoft-tokens |
| `backend/app/services/user_accounts.py` | Het account van een Microsoft-aanmelding; de noodbeheerder |
| `backend/app/services/auth_rate_limit_pg.py` | Rate limiting en lockout |
| `backend/app/schemas/auth.py` | Invoer van login en registratie (minimale wachtwoordlengte) |
| `backend/db/schema/postgres/01_access.sql` | De tabellen `users` en `auth_login_attempts` |
| `frontend/src/pages/auth/LoginPage.tsx` · `SignupPage.tsx` | Inloggen (ook met Microsoft en op `/login/emergency`) en registreren |
| `frontend/src/lib/microsoftSignIn.ts` | De aanmelding met Microsoft in de browser (MSAL) |
| `frontend/src/lib/auth.ts` · `frontend/src/lib/storage.ts` | De sessie in de browser |
