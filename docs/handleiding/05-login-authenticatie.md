# 5. Login & authenticatie

Dit hoofdstuk beschrijft hoe een gebruiker toegang krijgt tot CoGA: hoe het inlogformulier zijn gegevens naar de backend stuurt, hoe wachtwoorden veilig worden bewaard, hoe een JWT-token (een ondertekend "toegangsbewijs", zie hieronder) wordt uitgegeven en bij elke volgende aanvraag gecontroleerd, hoe de sessie in de browser leeft, en hoe brute-force-aanvallen (het systematisch uitproberen van wachtwoorden) worden afgeremd. Ook de optionele Azure AD single-sign-on (SSO) komt aan bod, en het hoofdstuk sluit af met wat een ingelogde gebruiker mag doen. Voor het volledige rechtenmodel verwijzen we naar [hoofdstuk 2 — Gebruikersrollen, machtigingen & afscherming](02-beveiliging-rollen-rechten.md).

## Begrippen die in dit hoofdstuk terugkomen

- **JWT (JSON Web Token):** een klein, digitaal ondertekend tekstbestandje dat de server aan de client meegeeft na een geslaagde login. Het bevat een paar gegevens ("claims", bv. wie je bent en tot wanneer het geldig is). Omdat het ondertekend is met een geheime sleutel, kan de client het niet vervalsen.
- **Hashen:** een wachtwoord onomkeerbaar versleutelen tot een reeks tekens. De server bewaart nooit het echte wachtwoord, alleen de hash; bij het inloggen wordt de ingevoerde tekst opnieuw gehasht en vergeleken.
- **Endpoint:** een concreet API-adres (bv. `POST /api/auth/login`) waar de frontend een aanvraag naartoe stuurt.
- **Rate limiting / lockout:** het tijdelijk blokkeren van te veel pogingen achter elkaar.

## De login-flow in vogelvlucht

1. De gebruiker vult e-mail en wachtwoord in op de inlogpagina in de browser.
2. De frontend stuurt die naar het login-endpoint van de backend.
3. De backend controleert eerst of het adres/IP niet geblokkeerd is (rate limiting), verifieert het wachtwoord tegen de opgeslagen hash, en geeft bij succes een JWT-token terug.
4. De browser bewaart het token en stuurt het bij elke volgende aanvraag mee in een `Authorization`-header.
5. De backend valideert dat token bij elk beschermd endpoint en leidt daaruit af wie de gebruiker is en wat die mag.

## De login-endpoints in de backend

De authenticatie-endpoints staan in `backend/app/routers/auth.py`, gemonteerd onder het pad `/auth` (dat samen met de globale API-prefix `/api` het volledige pad `/api/auth/...` oplevert).

Er zijn twee ingangen die naar exact dezelfde logica leiden:

| Endpoint | Invoer | Bedoeld voor |
|---|---|---|
| `POST /auth/login` | JSON met `email` + `password` (schema `UserLogin`) | De echte frontend (inlogpagina) |
| `POST /auth/token` | OAuth2-formulier met `username` + `password` | De Swagger-testinterface; het e-mailadres gaat in het veld `username` |

Beide roepen de gedeelde functie `_authenticate_and_issue_token` aan. Die functie doet, in volgorde:

1. **Rate-limit-controle** via `get_login_throttle_state`. Is het adres of IP momenteel geblokkeerd, dan volgt onmiddellijk een `429`-fout ("Too many login attempts. Try again later.") met een `Retry-After`-header (hoeveel seconden wachten).
2. **Gebruiker opzoeken** via `get_auth_user_mapping_by_email` (in `backend/app/services/metadata_service.py`).
3. **Wachtwoord verifiëren** met `verify_password`.
4. **Actief-controle:** een gevonden gebruiker die nog niet is geactiveerd (`is_active = false`) krijgt `403 User not active` (en die poging wordt, net als een verkeerd wachtwoord, meegeteld door de rate limiter via `record_failed_login`).
5. Bij succes: de tellers van mislukte pogingen wissen (`clear_login_failures`) en een token uitgeven met `create_access_token`.

Het antwoord is een `Token`-object met drie velden: `access_token`, `token_type` (`"bearer"`) en `role` (de rol van de gebruiker, zodat de frontend meteen weet wat te tonen).

**Waar in de code:** functie `_authenticate_and_issue_token` en de endpoints `login` / `token` in `backend/app/routers/auth.py`.

### Bescherming tegen account-enumeratie (timing-side-channel)

Een subtiel maar belangrijk beveiligingsdetail: als de server bij een onbekend e-mailadres meteen "nee" zou zeggen zonder een wachtwoordcontrole uit te voeren, zou een aanvaller aan de *responstijd* kunnen aflezen welke adressen wél bestaan (bekende adressen zijn trager omdat ze de dure bcrypt-verificatie doorlopen). Om dat te voorkomen draait de code bij een onbekend adres tóch een "wegwerp"-verificatie tegen een vaste dummy-hash:

```python
_DUMMY_LOGIN_PASSWORD_HASH = get_password_hash("coga-login-timing-equalizer")
```

Zo kost de "geen-zulke-account"-tak ongeveer evenveel tijd als de "verkeerd-wachtwoord"-tak. Bovendien is de foutmelding in beide gevallen identiek: `400 Incorrect email or password`. De client kan dus niet onderscheiden of het adres bestaat.

**Waar in de code:** de constante `_DUMMY_LOGIN_PASSWORD_HASH` en de `if user is None`-tak in `_authenticate_and_issue_token` (`backend/app/routers/auth.py`).

## Wachtwoord-hashing: bcrypt via passlib

Wachtwoorden worden nooit in leesbare vorm bewaard. De hashing gebeurt centraal met de bibliotheek **passlib** en het algoritme **bcrypt**:

```python
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)
```

Bcrypt is een bewust *trage* hash (met een "work factor") zodat een aanvaller die de databank buitmaakt niet snel miljoenen wachtwoorden kan raden. De hash wordt opgeslagen in de kolom `hashed_password` van de tabel `users` (zie `backend/db/schema/postgres/001_metadata.sql`); het echte wachtwoord verlaat het geheugen van de server nooit.

**Waar in de code:** `pwd_context`, `get_password_hash`, `verify_password` in `backend/app/dependencies.py`; kolom `hashed_password` in `backend/db/schema/postgres/001_metadata.sql`.

## JWT: token maken en controleren

### Token maken

Een geslaagde login levert een JWT op via `create_access_token`:

```python
def create_access_token(data: dict, expires_delta=None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)
```

De belangrijkste eigenschappen:

| Aspect | Waarde | Herkomst |
|---|---|---|
| Claim `sub` | het e-mailadres van de gebruiker | `data={"sub": user["email"]}` in `_authenticate_and_issue_token` |
| Claim `exp` | verloopmoment | berekend uit `access_token_expire_minutes` |
| Levensduur | standaard **120 minuten (2 uur)** | `ACCESS_TOKEN_EXPIRE_MINUTES` in config |
| Algoritme | **HS256** (symmetrisch ondertekend) | `settings.algorithm` |
| Ondertekensleutel | `SECRET_KEY` | `settings.secret_key` |

De korte levensduur van 2 uur is een bewuste keuze: het token leeft in de browser in `localStorage` (niet in een beveiligde HttpOnly-cookie), dus als het lekt, is de "straal van de explosie" beperkt tot maximaal twee uur.

**Waar in de code:** `create_access_token` in `backend/app/dependencies.py`; instellingen `algorithm`, `secret_key` en `access_token_expire_minutes` in `backend/app/core/config.py`.

### SECRET_KEY-beveiliging bij opstart

De `SECRET_KEY` is het hart van de tokenbeveiliging: wie die kent, kan geldige tokens vervalsen. De standaardwaarde is `"change-me"`, puur voor lokaal werk. De config weigert echter te starten buiten een ontwikkel-/testomgeving als de sleutel nog op zo'n onveilige standaard staat. De `model_validator` `validate_security_defaults` gooit een fout wanneer, terwijl `APP_ENV` niet op een ontwikkel-/testomgeving staat:

- `SECRET_KEY` een onveilige waarde is (`change-me` of `secret`, uit de set `_INSECURE_SECRET_VALUES`), of
- `POSTGRES_PASSWORD` of `ADMIN_PASSWORD` een onveilige waarde is (`change-me` of `admin`, uit de set `_INSECURE_PASSWORD_VALUES`), of de standaard-`ADMIN_USERNAME` (`admin`) met zo'n zwak wachtwoord wordt gecombineerd.

Dezelfde validator dwingt nog andere productie-eisen af (bv. dat `AUDIT_LOG_DROP_ALLOWED` niet aan mag staan in productie, zie de sectie over traceerbaarheid).

**Waar in de code:** `validate_security_defaults` en de sets `_INSECURE_SECRET_VALUES` / `_INSECURE_PASSWORD_VALUES` in `backend/app/core/config.py`.

### Token controleren: `get_current_user`

Elk beschermd endpoint hangt af van `get_current_user`. Die functie:

1. Haalt het token uit de `Authorization: Bearer ...`-header (via `oauth2_scheme`).
2. Decodeert en verifieert het met `jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])`. Een ongeldige of verlopen handtekening levert een `401 Could not validate credentials`.
3. Leest het e-mailadres uit de `sub`-claim.
4. Zoekt de gebruiker op met `get_current_user_by_email` en controleert dat die nog bestaat en `is_active` is; zo niet, opnieuw `401`.
5. Slaat het gebruikersobject op in `request.state.current_user`, zodat de audit-middleware (zie verderop) weet wie de aanvraag deed.

De strengere variant `get_current_admin_user` bouwt hierop voort en eist dat de rol in `ADMIN_ROLES = {"admin", "superuser"}` zit; anders `403 Admin access required`.

**Waar in de code:** `get_current_user` en `get_current_admin_user` in `backend/app/dependencies.py`.

## De frontend: van formulier tot bewaarde sessie

### Inlogpagina

De inlogpagina is `frontend/src/pages/auth/LoginPage.tsx`. Bij verzenden (`handleSubmit`):

1. Stuurt `POST /auth/login` met e-mail en wachtwoord.
2. Haalt daarna met het verse token het profiel op via `GET /auth/me` (het token wordt hier expliciet als `Authorization`-header meegegeven).
3. Bewaart de sessie met `persistSession(accessToken, me.data.email ?? email, me.data.role)` — dus het token, het e-mailadres uit het profiel (met de ingetypte waarde als terugval) en de rol.
4. Navigeert naar de doelpagina.

Die doelpagina komt uit de `next`-parameter in de URL, maar wordt eerst gefilterd door `resolveNextPath`: alleen paden die met één `/` beginnen zijn toegestaan (geen `//` en geen externe URL's), en `/login`/`/signup` worden teruggestuurd naar `/dashboard`. Dat voorkomt een **open-redirect** (een aanvaller die je na login naar een kwaadaardige site stuurt).

**Waar in de code:** `handleSubmit` en `resolveNextPath` in `frontend/src/pages/auth/LoginPage.tsx`.

### Waar het token wordt bewaard

De sessie leeft in drie sleutels in de browseropslag: `token`, `username` en `role` (gedefinieerd in `AUTH_STORAGE_KEYS`).

```ts
export function persistSession(token, username, role) {
  storage.setItem('token', token);
  storage.setItem('username', username);
  storage.setItem('role', role);
}
```

De opslaglaag `storage` (`frontend/src/lib/storage.ts`) gebruikt bij voorkeur de browser-`localStorage`, maar valt terug op een in-geheugen-implementatie (`createMemoryStorage`) wanneer `localStorage` niet beschikbaar is (bv. in bepaalde testomgevingen of privacymodi). Zo blijft de app werken zonder crash.

Helperfuncties zoals `isAuthenticated()` (is er een token?), `getStoredRole()` en `isAdmin()` lezen deze sleutels uit. `isAdmin()` geeft `true` voor de rollen `admin` en `superuser`.

**Waar in de code:** `persistSession`, `clearSession`, `isAuthenticated`, `isAdmin`, `getStoredRole` in `frontend/src/lib/auth.ts`; de opslaglaag in `frontend/src/lib/storage.ts`.

### De axios-client: token meesturen en 401 afhandelen

Alle API-aanvragen lopen via één gedeelde axios-client in `frontend/src/lib/api.ts`. Twee "interceptors" (tussenlagen) regelen de authenticatie:

- **Request-interceptor:** hangt automatisch `Authorization: Bearer <token>` aan elke uitgaande aanvraag, behalve aan `/auth/login` en `/auth/signup` (die staan in `AUTH_EXCLUDED_PATHS`), en behalve wanneer de aanroeper al zelf een `Authorization`-header meegaf.
- **Response-interceptor:** vangt elke `401`-respons op, wist de sessie met `clearSession()` en stuurt de browser hard naar `/login` (tenzij die zich al op `/login` bevindt). Zo wordt een verlopen of ongeldig token overal in de app consequent afgehandeld: de gebruiker belandt terug op de inlogpagina.

**Waar in de code:** de twee `api.interceptors`-blokken in `frontend/src/lib/api.ts`; hulpfuncties `shouldAttachStoredToken` en `hasAuthorizationHeader` in hetzelfde bestand.

### Routebescherming: RequireAuth en SessionRedirect

- `frontend/src/components/RequireAuth.tsx` bewaakt beschermde routes. Is er geen sessie, dan stuurt het door naar `/login?next=<huidige pad>` (het opgeslagen `next` bevat pad + query + hash), zodat de gebruiker na inloggen terugkeert waar die wilde zijn.
- `frontend/src/components/SessionRedirect.tsx` is een eenvoudige schakelaar die, afhankelijk van `isAuthenticated()`, naar een `authenticatedTo`- of `unauthenticatedTo`-bestemming leidt (bv. de root-route die naar dashboard of login gaat).

**Waar in de code:** `RequireAuth` en `SessionRedirect` in `frontend/src/components/`.

## Rate limiting & lockout tegen brute force

Om te voorkomen dat iemand wachtwoorden blijft raden, houdt de backend mislukte pogingen bij in de Postgres-tabel `auth_login_attempts`. Elke rij is een "scope" (bereik) met een teller:

| Kolom | Betekenis |
|---|---|
| `scope_type` / `scope_value` | het bereik: `email`, `remote_ip`, of `signup_ip` |
| `failure_count` | aantal opeenvolgende mislukte pogingen |
| `last_failure_at` | tijdstip van de laatste mislukking |
| `locked_until` | tot wanneer dit bereik geblokkeerd is |

**Waar in de code:** het schema in `backend/db/schema/postgres/010_auth_login_attempts.sql`; de logica in `backend/app/services/auth_rate_limit_pg.py`.

De werking (functies `record_failed_login`, `get_login_throttle_state`, `clear_login_failures`):

- Elke mislukte login verhoogt de teller voor **twee** bereiken tegelijk: het ingevoerde e-mailadres én het bron-IP (`_scope_rows`). Dat maakt zowel het bestoken van één account als het rondstrooien over vele accounts moeilijk.
- Zodra de teller de drempel (`LOGIN_RATE_LIMIT_THRESHOLD`, standaard **5**) bereikt, treedt een **exponentiële back-off** in werking: de wachttijd verdubbelt per extra poging, vanaf `LOGIN_RATE_LIMIT_BASE_BACKOFF_SECONDS` (standaard 30 s) tot maximaal `LOGIN_RATE_LIMIT_MAX_BACKOFF_SECONDS` (standaard 900 s = 15 min). Dit staat in `_backoff_seconds`.
- De teller "vergeet" oude mislukkingen: is de laatste poging langer dan `LOGIN_RATE_LIMIT_WINDOW_SECONDS` geleden (standaard 900 s), dan begint het tellen opnieuw bij 1 (`_next_failure_count`).
- Een **geslaagde** login wist de tellers (`clear_login_failures`), zodat een legitieme gebruiker die zich een keer vergist geen last houdt.

Een geblokkeerde poging levert `429` met een `Retry-After`-header op, zodat de client weet hoelang te wachten.

**Signup-throttling** werkt apart en enkel per bron-IP (`_signup_scope_rows` gebruikt `signup_ip`). E-mail-scoping heeft daar geen zin, omdat een aanvaller bij enumeratie juist telkens een ander adres probeert. De relevante instellingen staan in `backend/app/core/config.py` (de `login_rate_limit_*`-velden).

## Optioneel: Azure AD (SSO) en de admin-override

CoGA kan optioneel inloggen via **Azure AD** (Microsofts identiteitsdienst). Dit wordt geactiveerd zodra zowel `AZURE_TENANT_ID` als `AZURE_CLIENT_ID` zijn ingesteld.

Wanneer dat het geval is, verandert het gedrag van `get_current_user`: het inkomende token wordt eerst gevalideerd als een Azure-token via `verify_azure_token`. Die functie:

1. Leest het `kid` (key-id) uit de token-header.
2. Haalt de publieke sleutels (JWKS) op bij Microsoft, met caching (`lru_cache`) en één automatische her-ophaling (`cache_clear`) als Azure zijn sleutels heeft geroteerd.
3. Verifieert het token met **RS256** (asymmetrische handtekening) en controleert de `audience` (client-id) en `issuer`.

Het e-mailadres komt dan uit de claim `preferred_username` of `email`.

**Waar in de code:** `verify_azure_token` in `backend/app/core/azure.py`; de Azure-tak in `get_current_user` in `backend/app/dependencies.py`; de `azure_*`-instellingen in `backend/app/core/config.py`.

### De "break-glass" admin-override

Er is een noodmechanisme: als Azure-validatie faalt én `AZURE_ADMIN_OVERRIDE` aan staat (standaard **uit**), probeert de server het token alsnog als een lokaal uitgegeven HS256-token te lezen. Dit is streng afgeschermd:

- Het werkt **alleen** voor gebruikers met een admin-rol (`user.role in ADMIN_ROLES`); anders `401`.
- Elk gebruik schrijft een **waarschuwing** naar het log (`logger.warning("azure_admin_override: ...")`) met de tekst dat `AZURE_ADMIN_OVERRIDE` in productie uitgeschakeld hoort te zijn — zodat een per ongeluk ingeschakelde override opvalt in de logs.

Dit is bedoeld als "glas breken bij nood": een manier om binnen te komen als de SSO-koppeling stuk is, zonder de deur voor gewone gebruikers open te zetten.

**Waar in de code:** de `local_override`-tak en het `logger.warning(...)` in `get_current_user` (`backend/app/dependencies.py`); de instelling `azure_admin_override` in `backend/app/core/config.py`.

## Registratie (signup) en het goedkeuringsbeleid

Registreren kan iedereen via `POST /auth/signup` (endpoint `signup` in `backend/app/routers/auth.py`, frontend `frontend/src/pages/auth/SignupPage.tsx`), maar dat geeft **geen** directe toegang. Het beleid:

- De signup is per bron-IP gethrottled (`record_signup_attempt` / `get_signup_throttle_state`), zodat het niet misbruikt kan worden om accounts te enumereren of de admin-mailbox te overspoelen. De endpoint antwoordt met `202 Accepted`.
- Een nieuw account wordt aangemaakt met rol **`viewer`** en `is_active = false` — dus **niet actief**. Zie de `INSERT` in `create_user_account` (`backend/app/services/metadata_service.py`), die de rol hardcodeert op `'viewer'` en `is_active` op `false`.
- De response is **altijd identiek** — "Registration received. An administrator will review the request..." — of het e-mailadres nu nieuw is of al bestaat. Bij een bestaand adres geeft `create_user_account` stilletjes `None` terug en wordt de insert overgeslagen. Ook dit voorkomt account-enumeratie.
- Bij een écht nieuw adres wordt op de achtergrond een admin genotificeerd (`notify_admin`, via een `BackgroundTasks`-taak).

Een gebruiker kan pas inloggen nadat een **admin** het account activeert via `PATCH /auth/users/{user_id}` (endpoint `update_user`, beschermd door `get_current_admin_user`), dat `is_active` op `true` zet. Rollen en projecttoegang worden dus niet door de gebruiker zelf, maar door beheerders bepaald; project-toegang wordt zelfs expliciet geweigerd via dit endpoint (dat verloopt via de projectinstellingen).

**Waar in de code:** `signup`, `notify_admin`, `update_user` in `backend/app/routers/auth.py`; `create_user_account`, `update_user_account` in `backend/app/services/metadata_service.py`.

## Wat mag een ingelogde gebruiker?

Na een geslaagde login draagt de gebruiker een rol (`admin`, `superuser` of `viewer`) en een lijst van projecten waartoe die toegang heeft (`metadata_project_ids`), afgeleid uit de tabellen `users` en `project_users`. Kort samengevat:

- **viewer:** kan de families/projecten bekijken en beoordelen waar die aan gekoppeld is; ziet niets buiten die projecten.
- **admin / superuser:** beheerdersrechten (o.a. gebruikers activeren via `/auth/users`, projectbeheer) en toegang die niet tot specifieke projecten beperkt is.

De feitelijke afscherming gebeurt niet in dit hoofdstuk maar in de service-laag, die queries filtert op de projecten van de gebruiker (zie o.a. de `metadata_project_ids`-logica en `_visible_metadata_project_ids` in `backend/app/services/metadata_service.py`). Het volledige rollen- en rechtenmodel, inclusief hoe project-afscherming wordt afgedwongen, staat in [hoofdstuk 2 — Gebruikersrollen, machtigingen & afscherming](02-beveiliging-rollen-rechten.md).

## Veiligheid & traceerbaarheid

Voor een IVD-platform moet elke poging tot toegang navolgbaar zijn. De maatregelen rond authenticatie:

- **Volledige audit van elke aanvraag.** De middleware in `backend/app/middleware/request_logging.py` schrijft voor élke HTTP-aanvraag een auditgebeurtenis naar de databank (`write_audit_log_event`), inclusief de HTTP-status. Een mislukte login is dus herkenbaar aan de statuscode (`400`, `403` of `429`), een geslaagde aan `200`, samen met bron-IP, tijdstip en user-agent. Bij aanvragen op beschermde endpoints wordt ook de actor (`user_id`, `user_email`, `role`) meegeschreven, afgeleid uit `request.state.current_user` dat `get_current_user` heeft gezet (functie `_get_request_user`).
- **Wachtwoorden lekken niet in de logs.** De middleware maskeert gevoelige velden. Sleutels waarvan de naam met een van de `_SENSITIVE_PREFIXES` begint (o.a. `password`, `secret`, `token`, `authorization`, `api_key`, `access_key`) worden vervangen door `***` — ook voor het formulier-gecodeerde body van `/auth/token`, die in `_parse_request_body` expliciet wordt ontleed en gemaskeerd zodat `password=<plaintext>` nooit in de audit-DB belandt.
- **Mislukte-loginteller als aparte, doorzoekbare bron.** Naast de audittrail houdt de tabel `auth_login_attempts` per e-mail/IP het aantal mislukkingen en het lockout-moment bij — direct bruikbaar om een aanval te detecteren.
- **De noodoverride laat altijd een spoor na.** Elk gebruik van de Azure-admin-override schrijft een waarschuwing naar het log (zie hierboven).
- **Accountability wordt niet stilletjes weggegooid.** In productie weigert de config om `AUDIT_LOG_DROP_ALLOWED=true` te accepteren (`validate_security_defaults` in `backend/app/core/config.py`), zodat auditgebeurtenissen bij een volle wachtrij niet zomaar verloren gaan.
- **Korte tokenlevensduur + harde 401-afhandeling** beperken de gevolgen van een gelekt token: het verloopt na 2 uur en elke `401` wist automatisch de clientsessie.

**Waar in de code:** `log_request_response`, `_get_request_user`, `_sanitize_for_logging`, `_parse_request_body` in `backend/app/middleware/request_logging.py`; `auth_login_attempts` in `backend/db/schema/postgres/010_auth_login_attempts.sql`.

## Belangrijkste bestanden

| Bestand | Rol |
|---|---|
| `backend/app/routers/auth.py` | Login-, token-, signup-, `me`- en gebruikersbeheer-endpoints; enumeratie-beschermingen |
| `backend/app/dependencies.py` | Wachtwoord-hashing (bcrypt), JWT maken/valideren, `get_current_user` / `get_current_admin_user`, Azure-tak |
| `backend/app/core/config.py` | Instellingen: `SECRET_KEY`, tokenlevensduur, rate-limit-parameters, Azure-config; weigert onveilige defaults in productie |
| `backend/app/core/azure.py` | Azure AD-tokenvalidatie (JWKS ophalen, RS256, issuer/audience-controle) |
| `backend/app/services/auth_rate_limit_pg.py` | Rate limiting / lockout-logica (back-off, scopes email/IP/signup) |
| `backend/db/schema/postgres/010_auth_login_attempts.sql` | Tabel `auth_login_attempts` voor mislukte-pogingtellers en lockouts |
| `backend/db/schema/postgres/001_metadata.sql` | Tabel `users` (o.a. `hashed_password`, `role`, `is_active`) |
| `backend/app/services/metadata_service.py` | Gebruiker opzoeken, aanmaken (viewer/inactief) en activeren; project-scoping |
| `backend/app/middleware/request_logging.py` | Audittrail van elke aanvraag, met maskering van wachtwoorden/tokens |
| `frontend/src/pages/auth/LoginPage.tsx` | Inlogformulier, tokenopslag, veilige `next`-redirect |
| `frontend/src/pages/auth/SignupPage.tsx` | Registratieformulier |
| `frontend/src/lib/auth.ts` | Sessiehelpers (`persistSession`, `isAuthenticated`, rollen) |
| `frontend/src/lib/api.ts` | Axios-client: token meesturen + centrale 401-afhandeling |
| `frontend/src/lib/storage.ts` | Opslaglaag met terugval van `localStorage` naar geheugen |
| `frontend/src/components/RequireAuth.tsx` | Routebescherming met terugkeer naar bedoelde pagina |
| `frontend/src/components/SessionRedirect.tsx` | Redirect op basis van sessiestatus |
