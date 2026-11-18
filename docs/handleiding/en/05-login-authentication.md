# 5. Login & authentication

This chapter describes how a user gets access to CoGA. Once CoGA is in service, everyone signs in with their Microsoft account of the hospital or the university (Microsoft Entra ID, formerly Azure AD; see *Signing in with Microsoft*), or via the organisation's CAS server, such as `login.ugent.be` (see *Signing in via CAS*). Besides these there is local sign-in with an e-mail address and password: in development for everyone, alongside Microsoft only for the emergency administrator. The chapter first follows that local sign-in, from form to token and session, and registration. What a signed-in user may then see is in [chapter 2](02-security-roles-permissions.md).

## Concepts

- **JWT** (*JSON Web Token*): a small, digitally signed proof that the server hands out after a successful login. It contains a few pieces of data (*claims*), such as who you are and until when it is valid. Because it is signed with a secret key, the client cannot forge it.
- **Hashing:** irreversibly converting a password into a string of characters. The server stores only that hash; at login the text entered is hashed again and compared.
- **Rate limiting / lockout:** blocking temporarily after too many failed attempts.

## The login at a glance

1. The user enters an e-mail address and password on the login page.
2. The frontend sends them to the backend's login endpoint.
3. The backend checks that the address or the IP is not blocked, compares the password with the stored hash and, on success, returns a JWT.
4. The browser keeps the token and sends it with every following request in the `Authorization` header.
5. The backend checks the token at every protected endpoint and derives from it who the user is.

## The login endpoints

The endpoints are in `backend/app/routers/auth.py`, under `/api/auth/...`. Two entry points lead to the same logic:

| Endpoint | Input | Intended for |
| --- | --- | --- |
| `POST /auth/login` | JSON with `email` and `password` | The login page |
| `POST /auth/token` | An OAuth2 form with `username` and `password` | The interactive API documentation, which is reachable only in development; the e-mail address goes in `username` |

Both call `_authenticate_and_issue_token`. It does, in order:

1. **Check the block.** If the e-mail address or the IP is blocked, the answer is `429` ("Too many login attempts") with a `Retry-After` header.
2. **Look up the user and check the password.** An unknown address and a wrong password give the same message: `400 Incorrect email or password`.
3. **Check that the account is active.** An account that is not yet activated gets `403 User not active`. That attempt also counts as failed.
4. **On success:** clear the counter of failed attempts and issue a token.

The response contains the token, the type (`bearer`) and the user's role, so that the frontend knows what to show.

**Protection against account enumeration.** If the server said "no" straight away for an unknown address, an attacker could tell from the *response time* which addresses exist: the bcrypt check is deliberately slow. That is why, for an unknown address, the code still runs the check, against a fixed dummy hash. Both cases thus take about as long and give the same message. The hashing runs in a separate thread, so that a slow check does not hold up the server.

**Where in the code:** `_authenticate_and_issue_token` in `backend/app/routers/auth.py`.

## Passwords: bcrypt

Passwords are never stored in readable form. The backend hashes them with **bcrypt**, which it calls directly. Bcrypt is deliberately slow, so that an attacker who steals the database cannot quickly try millions of passwords. The hash is in the `hashed_password` column of `users`.

Bcrypt reads only the first 72 bytes of a password. The backend therefore truncates a longer password to 72 bytes itself, both when creating the hash and when checking it, so that both always compare the same part. A stored value that is not a bcrypt hash (e.g. from an account without a local password) simply gives no match.

**Where in the code:** `get_password_hash` and `verify_password` in `backend/app/dependencies.py`; the tests in `backend/tests/test_password_hashing.py`.

## The token

A successful login yields a JWT:

| Property | Value |
| --- | --- |
| Claim `sub` | The user's e-mail address |
| Claim `exp` | The expiry time |
| Lifetime | Short: two hours by default (`ACCESS_TOKEN_EXPIRE_MINUTES`) |
| Signature | HS256 (symmetric), with `SECRET_KEY` |

The short lifetime is a deliberate choice. The token is in browser storage, not in a protected HttpOnly cookie; if it leaks, the damage is limited to that period. Anyone who knows `SECRET_KEY` can make valid tokens; that is why, outside development, the backend refuses a weak key ([chapter 2](02-security-roles-permissions.md#refusal-to-start-with-weak-secrets)).

**Where in the code:** `create_access_token` in `backend/app/dependencies.py`; the settings in `backend/app/core/config.py`.

### Checking the token: `get_current_user`

Every protected endpoint depends on `get_current_user`, with two exceptions: the download of the QC report, which checks only its own signed five-minute link (chapter 2), and `GET /metrics`, which asks for its own token (chapter 7). That function:

1. takes the token from the header `Authorization: Bearer …`;
2. checks the signature and the validity; an invalid or expired token gives `401 Could not validate credentials`;
3. reads the e-mail address from the claim `sub`;
4. loads the user fresh from Postgres and checks that the user exists and is active; if not, again `401`;
5. attaches the user to the request (`request.state.current_user`), so that the audit log knows who made the request;
6. refuses (`400`) a request with a control character (C0 or DEL, such as a NUL from `%00`) in a path parameter, in the name of a query parameter or in the value of `family_id` or `sample_id` in the query string, and a request with a NUL in any query value. A family or sample ID may not contain such a character, and Postgres cannot compare a NUL. A gene or interval list, typed one per line, keeps its line breaks. The refusal comes after authentication, so that the audit log names the user, and before a route looks anything up, with the same answer for everyone (`request_value_problem` in `backend/app/services/family_identifiers.py`).

Two dependencies build on it. `get_current_superuser` requires the role `superuser` or `admin`; otherwise the answer is `403 Superuser access required`. `get_current_admin_user` requires the role `admin`, that of the administrator; otherwise the answer is `403 Admin access required`, for a superuser too. Which routes ask for which dependency is in chapter 2.

**Where in the code:** `get_current_user`, `get_current_superuser` and `get_current_admin_user` in `backend/app/dependencies.py`.

## The frontend: from form to session

The login page (`frontend/src/pages/auth/LoginPage.tsx`) sends `POST /auth/login`, requests the profile with the fresh token (`GET /auth/me`) and then stores the session: the token, the e-mail address and the role. It then opens the requested page from the `next` parameter. That parameter is filtered first:

- **Allowed** is only a path within the app, in the form the route guards write themselves: the page's path, query string and hash, percent-encoded as the browser keeps them. That is a `/`, then a letter or digit, and after that only visible ASCII characters without a backslash. A browser always reads such a value as a path on this site.
- **Everything else** opens the dashboard, just like a missing `next`: an external URL, `//`, a backslash, a space or a control character, and a path that starts with a percent-encoded character, such as `/%2F/host`. A browser reads a backslash as a slash and drops a tab or line break: `next=/%5Chost` or `next=/%09/host` in the address bar is `//host` to the browser, a different site.

This way an attacker cannot send you to a different site after login (*open redirect*), and the page after login does not get stuck on "This view could not be displayed" (React Router refuses such an address).

The session is in browser storage (`localStorage`), which the browser's tabs share. If that storage is not available or refuses a value, the page keeps that value itself, in memory. A value that browser storage did accept is not kept by the page, so that a tab does not read back a session that another tab ended. How the API client sends the token and logs out on a `401` is in [chapter 1](01-architecture.md); the route guards are in chapter 2.

**Where in the code:** `frontend/src/pages/auth/LoginPage.tsx`, `frontend/src/lib/auth.ts` and `frontend/src/lib/storage.ts`.

## Rate limiting and lockout

Failed attempts are tracked in the Postgres table `auth_login_attempts`. Each row is a *scope* with a counter:

| Column | Meaning |
| --- | --- |
| `scope_type` / `scope_value` | The scope: `email`, `remote_ip`, `signup_ip` or `cas_ip` (below, *Signing in via CAS*) |
| `failure_count` | The number of consecutive failed attempts |
| `last_failure_at` | The time of the last failure |
| `locked_until` | Until when this scope is blocked |

How it works:

- Every failed login increases the counter for **two** scopes at once: the e-mail address entered and the source IP. This slows down both hammering one account and trying across many accounts.
- From a threshold on, a waiting time follows that doubles with each extra attempt, up to a maximum. If the last failure was long enough ago, the count starts again.
- A successful login clears the counters.
- Threshold, window and waiting times are configurable (`LOGIN_RATE_LIMIT_*`, with their defaults in `.env.example`).

The source IP is the IP that the middleware establishes behind the proxies (`TRUSTED_PROXY_HOPS`, chapter 2), so that a client cannot choose it itself. Registration is throttled separately and only per source IP: someone enumerating accounts tries a different address each time.

**Where in the code:** `backend/app/services/auth_rate_limit_pg.py`; the table in `backend/db/schema/postgres/01_access.sql`.

## Registering and approving

Anyone can register via `POST /auth/signup` (page `frontend/src/pages/auth/SignupPage.tsx`), but that gives **no** access:

- The password must be at least 15 characters long (following NIST SP 800-63B-4, for a password that is the only factor). A shorter password gets `422`, before anything is created.
- Registration is throttled per source IP.
- A new account gets the role `viewer` and is **not active**. A viewer only reads, and changes nothing until an administrator gives them a higher role (chapter 2).
- The response is always the same ("Registration received …"), whether the address is new or already exists. That too prevents enumerating accounts. Only for a genuinely new address does every active administrator get an e-mail (see *The mail to the administrators* below).
- The page shows that confirmation: the account is waiting for activation.

A user can log in only after an administrator activates the account (`PATCH /auth/users/{user_id}`, for administrators only). The same endpoint also sets the global role (`viewer`, `analyst`, `supervisor`, `superuser` or `admin`; any other value gets `422`); on the *Users* page this is done with a drop-down list that shows the five roles from low to high, each with a tooltip. No one changes their own role or deactivates their own account. The emergency administrator's role does not change, and the emergency administrator cannot be deactivated. The endpoint also sets the account's projects and its level in each (`project_ids`, `reader_project_ids`; chapter 2), as the project settings do per project. A field it does not know, such as `projects` from the account list, gets `422`. The audit entry of the request records what changed, each field from and to (`request_meta.account_change`).

**Waiting for approval, archiving and deleting.** An account that has never been active (a registration, a first sign-in via CAS) is *waiting for approval*: `users.activated_at` is empty until an administrator activates it for the first time. The *Users* page shows for each account the date it was added and its status (*Waiting for approval*, *Active*, *Inactive*, *Archived*), with the waiting accounts at the top. Each change (activating, deactivating, role, archiving, restoring, deleting) first asks for confirmation in a dialog; ticking a box alone writes nothing.

- **Deleting** (`DELETE /auth/users/{user_id}`) is possible only for an account that was never active. Any other account answers `409` and is archived instead. If a record still names the account (such as a gene panel it created), the database refuses the deletion and the backend also answers `409`. The audit entry keeps who it was (id, e-mail address, name, sign-in method). If the same person signs in again later, a new account waits for approval.
- **Archiving** (`PATCH` with `archived: true`) deactivates the account and removes it from the default list (*Show archived accounts* shows it). The row stays, so that every record that names the account continues to do so, and a new sign-in by the same person does not create a new request. An archived account is first restored (`archived: false`, it stays inactive) before it can be activated again (otherwise `409`); the database does not allow an active archived account (`users_archived_inactive_check`).
- No one archives or deletes their own account or that of the emergency administrator (`403`).

**The mail server.** CoGA sends mail via the mail server in `SMTP_HOST` (port `SMTP_PORT`, sender `SMTP_FROM` or otherwise `ADMIN_EMAIL`; with `SMTP_USERNAME` and `SMTP_PASSWORD` CoGA authenticates, after `SMTP_STARTTLS`, which is mandatory outside development). Without `SMTP_HOST` no mail goes out. A mail links to a CoGA page if CoGA knows its own address (that of `CAS_SERVICE_URL`). A mail that fails to send leaves the action that sent it untouched; the log names only the type of the error, never an address or the server's text.

**The mail to the administrators.** An account waiting for approval is reported by e-mail to every active administrator. The mail gives the name, e-mail address, affiliation, sign-in method and time, and links to *Users*; it contains no patient data. The *Users* page shows to whom the mail goes and sends a test mail (`GET /api/auth/account-request-mail`, `POST /api/auth/account-request-mail/test`).

**The mail to the person being activated.** If an administrator makes an inactive account active (the approval of a waiting account, or a new activation after a deactivation), the user gets an e-mail after the response, at that account's address: the account can now log in, who activated it, how it logs in, and a link to the sign-in page. It contains no patient data, and does not mention role and projects: an administrator can still change those afterwards. Any other change (role, projects, deactivating, archiving, restoring) sends no mail. **Where in the code:** `backend/app/services/account_activation_mail.py`.

**The mail server at UGent.** A UGent server sends mail only via ICT's central relay, `smtprelay.ugent.be`: `SMTP_HOST=smtprelay.ugent.be`, `SMTP_STARTTLS=true` and as `SMTP_FROM` a functional UGent mailbox that ICT accepts as sender. ICT lets the VM send via the relay, or provides a system account for port 587 (`SMTP_PORT=587`, `SMTP_USERNAME`, `SMTP_PASSWORD`), never a personal account. The same mail server also sends the mail to whoever is assigned a family (chapter 2). The steps are in `docs/deployment-vm.md` (step 1, *At Ghent University*). **Where in the code:** `backend/app/services/outgoing_mail.py`.

With Microsoft sign-in or sign-in via CAS there is no registration: `POST /auth/signup` answers `403`, and the first sign-in creates the account (below).

**Where in the code:** `signup`, `update_user` and `delete_user` in `backend/app/routers/auth.py`; creating, changing and deleting the account in `backend/app/services/metadata_service.py`; the mail in `backend/app/services/account_notifications.py`; the minimum length in `backend/app/schemas/auth.py`; the page in `frontend/src/pages/admin/UserListPage.tsx`.

## Signing in with Microsoft (Azure AD / Entra ID)

Once CoGA is in service, everyone signs in with their Microsoft account. Microsoft Entra ID then checks the password and the second factor (MFA); IT handles password resets and closing the accounts of people who leave. The backend turns it on as soon as `AZURE_TENANT_ID` (the organisation's ID) and `AZURE_CLIENT_ID` (the ID of CoGA's app registration) are set. It is both or neither: with only one of the two, with a tenant that is not a GUID (such as `common`) or with the emergency override without Microsoft, the backend does not start. What IT creates for this is in `docs/deployment-gcp.md` §12.11.

**The sign-in at a glance.**

1. The login page asks the backend how users sign in (`GET /auth/config`: the client ID, the organisation's *authority* and CoGA's API scope; none of it is secret). With Microsoft it shows only the button *Sign in with Microsoft*, without a password form and without registration.
2. The browser goes to Microsoft with **MSAL** (`@azure/msal-browser`), using the *authorization code flow* with **PKCE**, and requests an access token for CoGA's scope (`api://<client-id>/access_as_user`). Microsoft always asks which account continues, so that on a shared PC no one gets in as their predecessor without being asked. The page the user requested does not go along to Microsoft: only a random key does, under which the path waits in the tab's session storage.
3. Microsoft sends the browser back to `/login`. The page completes the sign-in, requests the profile with the token (`GET /auth/me`), stores the session and opens the requested page, if it belongs to this site.
4. Every following request carries the Microsoft token. It lives for about an hour. For each request the API client asks MSAL for the token, and MSAL renews it silently before it expires. The IGV browser, which fetches its files itself, requests the token in the same way for each request (`sessionAccessToken` in `lib/api.ts`). MSAL keeps its tokens in the tab's session storage; they disappear when the tab closes.

**Checking the token.** `get_current_user` chooses the check by the algorithm in the token's header: RS256 is a Microsoft token, HS256 a local token. A Microsoft token is accepted if it:

- is signed with one of the organisation's published keys (fetched from its OpenID configuration, kept for a day, and for an unknown key fetched again at most once every five minutes);
- is a version 2.0 token of the organisation itself (`iss`, `tid`);
- is meant for CoGA's API and was requested by CoGA itself (`aud` and `azp`: the client ID);
- carries CoGA's scope (`scp`; an application's own token has none);
- has not expired (with one minute's leeway).

A refused token gets `401`. The reason (never the token) goes to the log, except for an expired token.

**The account.** The backend looks up the account by the user's object ID in Entra ID (`oid`, column `users.azure_object_id`), never by the e-mail address. At the first sign-in it creates the account: role `viewer`, active, member of no project, and therefore without access to patient data until an administrator adds it to a project. It can make changes only once an administrator gives it a higher role (chapter 2). The account has no password (`hashed_password` holds a marker that no password matches). E-mail address and name come from the token (`email`, otherwise `preferred_username`; `given_name` and `family_name`) and follow the token when they change. What a user stores as their own is therefore tied to the account (its id), never to the e-mail address or the username. The requester of an import job (chapter 6) and their filter presets (chapter 10) remain theirs when those change. An account that later signs in with the address or name that someone else left behind gets none of it. If another account already has that e-mail address or that username, the backend refuses the first sign-in (`403`) and links nothing: an existing account is never taken over via an e-mail address. A deactivated account gets `401` with the message that it is not active. The audit entry of the request that created the account says so (`request_meta.account_change`).

**The emergency administrator.** Besides Microsoft there is one local account: the emergency administrator (*break-glass*). That is the account that `ADMIN_USERNAME` names (`coga-admin` on Google Cloud), with the role `admin`: an account with that name and a different role, `superuser` included, is not an emergency administrator. CoGA does not let its role be changed and does not let it be deactivated, so that the emergency sign-in works as soon as the override is on. Its password is `ADMIN_PASSWORD`: the seed replaces the stored hash as soon as it no longer matches, so whoever rotates the secret rotates the password. The emergency administrator signs in on `/login/emergency`, a page the login page does not link to, and only while `AZURE_ADMIN_OVERRIDE` is on (off by default). Any other account, another administrator included, gets `403` there; a local token that is not the emergency administrator's gets `401`. Every emergency sign-in and every request made with it writes a warning (`azure_admin_override`) to the log and is marked in the audit entry (`request_meta.emergency_sign_in`, *Emergency sign-in* on the *Audit logs* page). Turn the override on only for an emergency, and off again afterwards.

**Logging out.** *Logout* clears CoGA's session and MSAL's tokens in the tab. UI events that the API did not accept at logout (the token had already expired, or the last request did not arrive) are not lost: they wait in the browser under the account until it signs in again (chapter 15). The sign-in with Microsoft in the browser remains; the login page then offers *Sign out of Microsoft*, which ends that too (advisable on a shared PC).

Logging out also ends the session on the server, on every device. The browser sends `POST /api/auth/logout` (`endServerSession` in `lib/api.ts`) as soon as the API has answered the last UI events, or after two seconds, and immediately if the page closes in the meantime. The server records when the account logged out (`users.sessions_ended_at`) and from then on refuses every token the account received before that moment, CoGA's own tokens and Microsoft's (`_refuse_if_signed_out` in `dependencies.py`). A copied token therefore stops working when the user logs out, instead of when it expires. CoGA's own tokens state to the microsecond when they were issued (`iat`), so that a sign-in just after logging out works normally; a token without that time is refused, because no logout could refuse it. Entra ID dates its tokens about five minutes too early: a Microsoft token therefore counts as issued at its `iat` plus five minutes, the latest possible moment (`TOKEN_DATED_EARLY_SECONDS` in `core/azure.py`). Logging out ends the sessions at least just after the issue time of the token used to log out, so that this token too is refused, however early it is dated. If the request does not arrive (offline, or the token had already expired), the tokens stay valid until they expire.

Logging out in one tab ends the session in every tab of the browser. The tabs share the session, and the browser notifies every other tab that the token is gone. That tab then clears its cached data and MSAL's tokens, and shows the login page (`Layout.tsx`).

If a different account signs in in another tab (on a login page that was still open there), the session ends in the same way. The other tabs see the username, the account's address, change. The session of that other account is cleared too, so that no tab carries on as an account it does not show; that account signs in again. A renewed token, and a new sign-in by the same account, leave the username unchanged, and the session continues.

**Where in the code:** `backend/app/core/azure.py` (checking the token), `backend/app/services/user_accounts.py` (the account and the emergency administrator), the Microsoft branch in `get_current_user` (`backend/app/dependencies.py`), `GET /auth/config` in `backend/app/routers/auth.py`, and in the browser `frontend/src/lib/microsoftSignIn.ts` and `frontend/src/pages/auth/LoginPage.tsx`.

## Signing in via CAS (login.ugent.be)

If the organisation offers not an app registration in Entra ID but a CAS server, everyone signs in there. At UGent that is `login.ugent.be`, which itself signs in via Microsoft Entra ID. The backend turns it on with `CAS_SERVER_URL` (the CAS server, `https://login.ugent.be`) and `CAS_SERVICE_URL` (CoGA's own login page, `https://<host>/login`). It is both or neither; alongside Microsoft sign-in, with an address that is not HTTPS (outside development) or with a service other than `/login`, the backend does not start. `CAS_DISPLAY_NAME` (default `UGent`) is the name on the button.

The CAS server gives CoGA the attributes chosen when the application was registered. CoGA needs four: `azureObjectId` (the user's object ID in Entra ID), `mail`, `givenName` and `surname` (the names are configurable with `CAS_*_ATTRIBUTE`). Without an object ID CoGA refuses the sign-in. If `mail`, `givenName` or `surname` is missing, the backend writes a warning to the log (`cas sign-in`) with the names of the attributes the response does carry, never their values. Without `givenName` CoGA shows only the surname; if the CAS server releases the attribute later, the next sign-in fills in the first name.

1. The login page asks the backend how users sign in (`GET /auth/config`: the CAS server's login page for CoGA's service, and its logout page). With CAS it shows only the button *Sign in with UGent*, without a password form and without registration.
2. The browser goes to the CAS server. The page the user requested waits in the tab's session storage: the CAS server sends the browser back to exactly `/login`.
3. The CAS server sends the browser back to `/login` with a *service ticket*, which can be used once. The page takes it from the address bar and gives it to the API (`POST /auth/cas/session`).
4. The backend validates the ticket with the CAS server (`/serviceValidate`, over HTTPS, for CoGA's service). A value that is not a service ticket does not go to the CAS server. The backend reads the response only if it is a CAS response of at most 64 KiB, without a document type, so that no entity is expanded.
5. The account belongs to the object ID, never to an e-mail address: it is the same account that a later Microsoft sign-in finds. If there is none yet, the first sign-in creates it as a viewer without a project, **inactive** and waiting for approval, and every active administrator gets a mail. CAS lets in every account of the organisation, students included: that is why the new account waits for an administrator, who activates it on the *Users* page. Until then the sign-in answers `403` with that message.
6. If the account is active, the browser gets CoGA's own token (HS256), as with a password. With CAS such a token signs in only an account that is bound to an object ID, or the emergency administrator while `AZURE_ADMIN_OVERRIDE` is on.

A refused ticket answers `401` and counts towards the source IP (`auth_login_attempts`, scope `cas_ip`); after too many refusals the sign-in answers `429`. A successful sign-in does not count, so that colleagues behind the same address do not hold each other up. The ticket and the CAS server's response do not go into the log. **Logging out** clears CoGA's session; the login page then offers *Sign out of UGent*, which ends the session on the CAS server.

**Where in the code:** `backend/app/core/cas.py` (the ticket and the response), `cas_session` in `backend/app/routers/auth.py`, `_cas_session_user` in `backend/app/dependencies.py`, `backend/app/services/user_accounts.py` (the account), and in the browser `frontend/src/lib/casSignIn.ts` and `frontend/src/pages/auth/LoginPage.tsx`.

## Security and traceability

- **Every request is logged,** including every login attempt, with status code, time, source IP and user agent (chapter 7). A failed login can be recognised by `400`, `403` or `429`.
- **Passwords and tokens do not go into the logs.** The audit log masks sensitive fields, also in the form of `/auth/token`.
- **The table `auth_login_attempts`** shows the failures and blocks per e-mail address and IP: useful for recognising an attack.
- **The emergency sign-in always leaves a trace** in the log and in the audit entry of every request (`request_meta.emergency_sign_in`).
- **An account created by a first Microsoft or CAS sign-in, and every change of role, activation or project access,** are in the audit entry of the request, each field from and to (`request_meta.account_change`).
- **Short tokens and logging out on `401`** limit the consequences of a leaked token.

## Key files

| File | Role |
| --- | --- |
| `backend/app/routers/auth.py` | Login, token, registration, profile and user management |
| `backend/app/dependencies.py` | Passwords (bcrypt), making and checking tokens, Microsoft branch and emergency administrator |
| `backend/app/core/azure.py` | Checking Microsoft tokens |
| `backend/app/core/cas.py` | Validating a CAS ticket with the CAS server |
| `backend/app/services/user_accounts.py` | The account of a Microsoft sign-in; the emergency administrator |
| `backend/app/services/auth_rate_limit_pg.py` | Rate limiting and lockout |
| `backend/app/schemas/auth.py` | Login and registration input (minimum password length) |
| `backend/db/schema/postgres/01_access.sql` | The tables `users` and `auth_login_attempts` |
| `frontend/src/pages/auth/LoginPage.tsx` · `SignupPage.tsx` | Logging in (also with Microsoft and on `/login/emergency`) and registering |
| `frontend/src/lib/microsoftSignIn.ts` | Signing in with Microsoft in the browser (MSAL) |
| `frontend/src/lib/casSignIn.ts` | Signing in via CAS in the browser |
| `frontend/src/lib/auth.ts` · `frontend/src/lib/storage.ts` | The session in the browser |
