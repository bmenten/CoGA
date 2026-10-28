# Security & PHI posture

CoGA is built to hold patient genomes, so it is treated as a system that handles protected
health information (PHI). The data it holds today is synthetic. This page describes the
security controls in the application and the state of the deployment items. The deployment
items S-1 to S-8 are tracked in [TF-13 §3](regulatory/TF-13-cybersecurity.md); the steps
that put them in place are in [deployment-gcp.md](deployment-gcp.md).

Legend: ✅ enforced in code · 🟡 partial, or depends on configuration or a first deployment
· ⛔ not done.

## 1. Authentication & RBAC

- ✅ **Sign-in with Microsoft (SEC-4).** With Azure AD (Microsoft Entra ID) configured
  (`AZURE_TENANT_ID`, `AZURE_CLIENT_ID`), users sign in with their hospital or university
  Microsoft account: Entra ID checks the password and MFA, and IT handles password resets
  and leavers. The browser (MSAL, `frontend/src/lib/microsoftSignIn.ts`) uses the
  authorization code flow with PKCE and sends Microsoft's access token for CoGA's API scope
  as the bearer. `core/azure.py` accepts it only when it is signed (RS256) with one of the
  directory's published keys (fetched from its OpenID configuration, kept a day, fetched
  again for an unknown key at most every five minutes), is a version 2.0 token of CoGA's own
  directory (`iss`, `tid`), is for CoGA's API and asked for by CoGA itself (`aud` and `azp`,
  the application ID), carries CoGA's scope (`scp`: an application's own token has none) and
  is within its lifetime (one minute's leeway). The account is the one bound to the token's
  object ID (`oid`, `users.azure_object_id`), never one found by an e-mail address: a first
  sign-in makes it, a viewer in no project who sees no patient data until an administrator
  adds them to a project, and a first sign-in whose e-mail address or user name another
  account has is refused (403) rather than linked (`services/user_accounts.py`). A
  deactivated account is refused with a 401 that says so. With Microsoft sign-in the
  password sign-in and the sign-up are refused, except for the emergency administrator
  (below). A half configuration (a tenant without a client ID, a tenant that is no GUID such
  as `common`, a scope or the override without Azure AD) is refused at startup, in every
  environment.
- ✅ **A user's own records stay with the account (SEC-32).** A later Microsoft sign-in follows
  the token's e-mail address and user name, so nothing a user keeps as their own is found by
  either. An import job keeps its requester by the account's id
  (`family_import_jobs.requested_by_id`), and the worker runs the import as that account while
  it is active (*Session tokens*, below); a user's filter presets are the account's
  (`owner_id`). Their address or user name changing keeps both theirs, and an account that
  later signs in with an address or user name another left behind gets neither. The address
  and the user name stay with the record as it was made, shown with it.
- ✅ **Without Microsoft sign-in** (development), JWT bearer tokens (HS256) that the
  password sign-in issues work for every account.
  See `backend/app/dependencies.py` (`get_current_user`, `get_current_admin_user`). Roles are
  `admin`, `superuser` and `viewer`; every admin check goes through `ADMIN_ROLES`
  (`services/access_control.py`), so a superuser is an admin everywhere, and
  `test_admin_role_checks.py` fails on any comparison with the literal `"admin"`. A `viewer` is
  an analyst or a reader in each of their projects (below).
- ✅ **Roles are set in the UI, and audited.** An administrator sets an account's global role
  (viewer, superuser, admin) on the *Users* page (`PATCH /api/auth/users/{id}` with `role`),
  asked to confirm each change. Nobody changes their own role, and the emergency
  administrator keeps its role and cannot be deactivated. The request's audit row records what changed,
  each field from and to (`request_meta.account_change`), and so does an activation change
  and an account a first Microsoft sign-in made. Project access stays on the project pages.
- ✅ **Access is scoped by project.** Every family, sample and variant endpoint resolves
  access through one checkpoint: `build_family_metadata_context`
  (`services/family_metadata_context.py`) → `get_accessible_family_mapping`
  (`services/metadata_service.py`), which asks `user_can_access_metadata_projects`
  (`services/access_control.py`), and the sample equivalent `get_accessible_sample_mapping`. A
  non-admin reaches only the families and samples of their own projects; admins see all. List
  endpoints filter in SQL, not after the fact. `backend/tests/test_access_control.py` covers
  the cross-user and multi-project cases.
- ✅ **A record outside your projects looks unknown.** A family or sample of another project
  answers exactly like an ID nobody uses: `404 {"detail":"Family not found"}` or
  `404 {"detail":"Sample not found"}`, raised at the checkpoint for both cases. A viewer
  therefore cannot find out which family and sample IDs exist in other projects. A project ID
  given to the Family Builder or the gene profile is treated the same way
  (`404 Project not found`). The request audit still tells the two apart: the
  `audit_log_events` row of such a request names the kind in `request_meta.record_hidden`
  (`family`, `sample` or `project`), which the response never carries
  (`main._answer_record_not_visible`), and the admin *Audit logs* page shows it in its
  *Flags* column (§2). Another user's filter preset, on delete, and import job, on read,
  answer the same way (`404 Preset not found`, `404 Family import job not found`), their rows
  naming `filter_preset` or `import_job` (SEC-9).
  `backend/tests/e2e/test_e2e_inaccessible_records_look_unknown.py` asks every route with a
  family or sample ID in its path, on real Postgres, for a family of another project and for
  an unknown one, and requires the same status and body. Refusals because of the caller's
  role (`403 Admin access required`) are unchanged: they say nothing about the record.
- ✅ **Read-only project access: analyst or reader (SEC-1).** Each project membership has an
  access level (`project_users.access_level`): an **analyst** reads and changes the project's
  records, a **reader** (a student, say) only reads them. An admin has every right in every
  project, member or not. One person may be an analyst in one project and a reader in another,
  and a record of several projects may be changed by an analyst of any one of them. An admin
  sets each member's level on the *Projects & access* page (`reader_user_ids` in the project
  API); a membership made without one is an analyst's, and each membership made before the
  levels became one. `get_current_user` records whether a request changes something: any
  method but GET, HEAD and OPTIONS, unless its route is marked `changes_only_own_data`
  (`services/access_control.py`: the caller's own filter presets and interface events). The
  family, sample and project checkpoints (`get_accessible_family_mapping`,
  `get_accessible_sample_mapping`, and the Family Builder's `_resolve_accessible_project_id`)
  then refuse a reader's change with `403` and the reason (`require_change_access`), after the
  visibility check, so a record outside the caller's projects still answers like an unknown
  one: every route that names a record passes there, and none can leave the check out. A
  reader is also refused a family's clinical audit trail (`403`). A reader may view and search
  their projects' families, variants, tracks, reports and signed versions, export CSV, print
  the report and a signed version, and save their own filter presets; a change to anything
  clinical is refused: phenotypes, reviews, tags, ACMG and CNV classifications, sign-out, the
  case's workflow fields, a new family, and every upload, import, edit and deletion an admin's
  route guards. The family and project records the API serves say whether they are read-only
  for the caller (`read_only`), so the screens hide or disable what a reader may not use; the
  server's refusal is the guard. `test_project_access_levels.py` keeps the catalogue of every
  route that changes something (an admin's, the caller's own data, or refused at a
  checkpoint), and `e2e/test_e2e_project_reader_role.py` asks each of them as a reader on real
  Postgres, then every route that reads a family or sample as a reader and as an analyst, and
  requires the same answer but for the audit trail. A case is assigned to, and recorded as
  reviewed by, an active analyst of one of the family's projects or an active admin: the
  family page's pickers read the family's own list (`GET /api/families/{family_id}/assignable-users`,
  answered like the family, 404 outside the caller's projects), and a save that names a reader,
  a user outside the family's projects or an inactive account is refused (`422`, saying why)
  and changes nothing. A value the family already holds is kept until it is changed, so a
  member made a reader stays named, marked *(not eligible)* on the page
  (`test_case_assignee_eligibility.py`, `e2e/test_e2e_case_assignee_eligibility.py`).
- ✅ **No control characters in what a request looks up.** A `%00` in a URL arrives decoded,
  and Postgres cannot compare a NUL, so a request that looked up a family's path
  (`/api/families/FAM%00X`) or any other path or query value with one failed with a 500.
  `get_current_user` answers 400, naming the parameter, when a path parameter, a query
  parameter's name or a `family_id` or `sample_id` query value holds a C0 control character
  or DEL, which no family or sample ID may hold (`services/family_identifiers.py`), and when
  any other query value holds a NUL. A gene or interval list typed one per line keeps its line
  breaks. The check runs once the caller is signed in, so the 401 comes first and the
  refusal's audit row names the caller, and before any route looks a value up, with the same
  answer for every caller: it reveals nothing about which families exist or who may see them.
  Every route that takes a path or query value signs its caller in, except the QC-report
  download, which checks its own signed link first (`test_request_control_characters.py`).
- ✅ **Admin-only changes.** Structure changes and deletions of families and data (member and
  structure edits, region of interest, project assignment, family and sample deletions),
  replacing a family's annotation manifest, reference data, imports and uploads require
  `get_current_admin_user`. An analyst of a family's project can edit its phenotypes (each
  change an event on its clinical audit trail, as is each change an import makes to them), its
  reviews and its workflow fields, and sign its report out; a reader can do none of these.
  Every member saves their own filter presets.
- ✅ **A UUID a request names a record by is read once.** asyncpg's uuid codec takes nothing
  but hex digits and hyphens, so a path or query value that was no UUID
  (`/api/admin/data/files/PROBEX/download`), or a UUID in braces or after `urn:uuid:` that a
  route had checked with `uuid.UUID` and then bound as received, failed the request with a
  500. Every such value (a raw file, an import job, an HPO annotation, a NIPT artifact, a
  panel, a filter preset, a user, a project, a species, an assembly, a clinical CNV, the
  `panel_id` filter) is now read by `core/sql.py` (`canonical_uuid`, `require_uuid`): one that
  spells no UUID is answered as an unknown record is, with the route's own 404 or the 400 it
  answers an invalid id with, before any query; one that does is bound as its canonical text,
  so each spelling of a record's id names that record. What `uuid.UUID` merely tolerates (an
  underscore, a space or a sign among the digits, `0x`, digits of another script) spells no
  UUID: read as a number, it would name a different-looking record. The value is read where
  the record is looked up, after the sign-in, the role and the family's project checks, so the
  answer is the one an unknown record gets, whether or not a record of that id exists in a
  project the caller cannot see (`test_request_uuid.py`,
  `e2e/test_e2e_request_malformed_uuid.py`). The ids a project, assembly, family-project or
  family-status body carries are read the same way, and so are the `assembly_id` of a NIPT
  artifact (its add, auto-seed and table import) and the `project_id` of an import request:
  one that spells no UUID or names no record is refused as an unknown assembly or project
  (404) before anything is written, so the artifact list's audit chain holds no event for it,
  and each audit event records the canonical id (`test_request_body_uuid.py`,
  `e2e/test_e2e_request_body_uuid.py`, which also sweeps every UUID a request body carries).
- ✅ **Scoped downloads.** The CRAM/BAM and signal-track endpoints check family and sample
  access before they hand out a signed URL (`routers/cram.py`, `routers/signal_tracks.py`).
  They sign a location the import recorded only when it names an object in the configured
  bucket below `FAMILY_IMPORT_ROOTS`, so a changed database row cannot point them at any
  other object. With local storage the CRAM/BAM, signal-track and QC-report endpoints
  (`routers/family_qc_reports.py`) look a file up only in the family's own folders,
  `data/<family>/` and `data/families/<family>/` (`core/family_folders.py`): a sample's reads
  by the layouts, its signal files and QC report at the path the import recorded on the
  sample. A path built from the IDs or read back from `samples.metadata` is normalised and
  dropped before any look-up when it leaves the family's folder (a sample ID such as
  `../F2/S2`, a recorded path holding `..` or naming an absolute path elsewhere, a family ID
  that is no single folder name), a file whose real path lies outside the family's folders (a
  linked folder leading into another family's folder or out of the data directory) is neither
  listed in a manifest nor served (404), and nothing outside the layouts is tried. A QC report
  must also lie, symlinks resolved, inside the folder it is found in, below
  `FAMILY_IMPORT_ROOTS`. The admin page's raw-file download and **Verify** read the path
  recorded in `raw_import_files` only when its real path, symlinks resolved, lies inside a
  local `FAMILY_IMPORT_ROOTS` folder, the staging folder of a bucket package or the upload
  folder `data/raw_imports` (`services/raw_import_files_pg.py`); any other path answers 403
  and is logged, and the file list shows it unavailable without looking it up. Verify asks
  the object store only about an object below a remote `FAMILY_IMPORT_ROOTS` entry. The list
  marks a row of either kind as recorded outside what CoGA reads (`outside_read_folders`),
  and the page gives the reason a Verify was refused; it listed such an object as kept in the
  object store, and said only that Verify failed (SEC-27). A family or sample delete unlinks
  a managed copy only inside the upload folder, and skips any other path a managed row names
  with a log line. An edited row could download any file the backend can read, and make a
  delete remove any file it may write (SEC-29).
- ✅ **A package import reads only its own folder.** Package Import takes a folder only below
  `FAMILY_IMPORT_ROOTS` (`package_folder_not_allowed`), and the package list leaves out, and
  reads nothing in, a folder that is a symlink leading out of the roots. Every path a
  manifest names must lie inside the package folder (`_resolve_package_path` in
  `services/family_package_common.py`): validation refuses with 400, before anything is read,
  an absolute path elsewhere, `..` that climbs out, a symlink that leads out, a path that
  starts with `~` (never expanded to a home folder) and one that runs through a symlink loop.
  A `manifest.yaml`, `.yml` or `.json` that is a symlink leading out of the folder, or one
  that loops, is refused (`manifest_outside_package`): Discover and validation do not read it,
  and **Write manifest** does not write through it. A linked manifest gave a package the
  family of any YAML or JSON file the backend could read, and Write manifest replaced the
  file it pointed at (SEC-21, #842; [data-import.md](data-import.md)). A package staged from
  a bucket is read only by the import or validation that staged it: a typed folder in the
  staging folder, or an import root linked there, is refused (`package_folder_not_allowed`),
  where it read another import's staged copy (#850). The staging folder,
  `coga-family-imports` in the temp folder, is made private to the backend (mode 0700); one
  found there that is a symlink, no folder or another user's is refused rather than followed,
  so a link planted on a shared temp folder cannot widen the rule (SEC-27). A package's
  `ped/` folder or long-read folder (a dataset's, a sample's) that is a symlink is followed
  wherever it leads, by the package list and by Discover, which may propose a member from
  it; the owner chose a warning over a refusal. Discover and validation report each such
  link (`package_folder_link`), saying where it leads relative to the import roots, or that
  it leads outside them, and never as an absolute path (SEC-27). A file read through such a
  link must still lie in the package.
- ✅ **No weak secrets outside development.** `Settings.validate_security_defaults` refuses to
  start when `SECRET_KEY` is a placeholder or shorter than 32 characters, when
  `CLICKHOUSE_PASSWORD` is empty or a placeholder, when `POSTGRES_PASSWORD` or
  `ADMIN_PASSWORD` is a placeholder, or when `INTEGRITY_ANCHOR_SIGNING_KEY` is not the base64
  of a 32-byte Ed25519 seed or is the same value as `SECRET_KEY` (then whoever can mint a
  session token could also sign an integrity anchor). An unsigned integrity anchor is refused
  as well. Passwords are stored as bcrypt hashes.
- ✅ **No cross-origin access outside development.** The deployed UI is served from the API's
  own origin, so the backend refuses to start while `CORS_ORIGINS` or `CORS_ORIGIN_REGEX`
  admits a loopback origin (`localhost`, `127.0.0.1`, `::1`, `0.0.0.0`) or any site (`*`, or a
  pattern matching an unrelated origin). The development defaults admit localhost with
  credentials, which would let a page served on the user's own machine call the API as the
  signed-in user. Terraform sets the app's origin and an empty pattern for every container
  of the backend image: the API and the schema-migration job.
- ✅ **Every running build names its commit.** Outside development the backend refuses to
  start without a `GIT_SHA` (7–40 hex characters), which every signed report records
  (TF-18 §2). `build.yml` and `ci/cloudbuild.backend.yaml` stamp it.
- ✅ **Metrics without identifiers, behind a token.** `GET /metrics` is off unless
  `METRICS_TOKEN` is set, then answers only that bearer token (32+ characters outside
  development, distinct from the other secrets). It sits outside `/api`, which is all the load
  balancer and the frontend server pass to the backend, so it is never served to the internet.
  Requests are labelled by route template, never by path, so no family, sample or variant
  identifier reaches a metric ([monitoring.md](monitoring.md)). In the deployment a collector
  in the backend's own Cloud Run service scrapes it over the instance's loopback, with the token
  from Secret Manager, which only the backend's account may read (`terraform/monitoring.tf`).
- 🟡 **Alerts on what matters, pending the first apply.** Cloud Monitoring policies page on a
  damaged variant store, lost accountability events and an unreachable app, and warn on a stalled
  integrity check, a backed-up audit queue, server errors, a stuck import and missing metrics; an
  uptime check fetches `/api/health` through the load balancer ([monitoring.md](monitoring.md)).
- ✅ **The image ships only the scripts it runs.** `clinical_cnv_knowledgebase.py` (the admin
  rebuild) and `import_dgv.py` (an operator import); the test seeders, one of which creates a
  sign-in with a default password, the demo loaders and the CI checks stay out of it.
- ✅ **Passwords at sign-up** need at least 15 characters (`SIGNUP_PASSWORD_MIN_LENGTH`,
  following NIST SP 800-63B-4 for a single-factor password); a shorter one gets a 422 before
  any throttling or hashing. The device owner confirmed this policy (#626).
- ✅ **Reference routers need a signed-in user**, the species list included. Reference data
  (genes, assemblies, the CNV catalogue) is not project-scoped: it is public and not PHI.
- ✅ **Outbound downloads.** The HPO bootstrap downloads only when it finds no ontology file:
  over HTTPS only, capped in size and checked to be an OBO file. It fetches the vendored
  file's release from its versioned URL and uses the file only if its SHA-256 is the vendored
  file's; another file is refused and logged before anything is written. The URL and the
  SHA-256 are fixed in the code, and no setting changes them: the removed `HPO_ONTOLOGY_URL`
  and `HPO_ONTOLOGY_SHA256` are ignored if still set. An accepted file is written atomically
  ([data/ref-data/hpo/README.md](../data/ref-data/hpo/README.md#the-download-fallback)).
  The clinical-CNV knowledgebase script runs with an allowlisted environment: no database
  passwords, signing keys or cloud credentials; `OMIM_API_KEY` is its one credential. It reads
  ClinGen's recurrent CNV file only if its SHA-256 is pinned for the assembly, and stops
  otherwise (TRACE-14, [data-import.md](data-import.md)).
- 🟡 **QC-report links** carry a 5-minute token, scoped to the family and sample, in the query
  string, because a browser navigation cannot send a bearer token. The report is served with
  `Referrer-Policy: no-referrer`, `Cache-Control: no-store` and a sandbox CSP; the token is
  masked in the access log, and the request audit keeps query keys only. It can still sit in
  the browser history until it expires.

**IDOR review.** Every endpoint that takes a `family_id`, `sample_id` or `project_id` goes
through the checkpoint above. No unscoped PHI endpoint was found.

**Residual: IDs that are taken.** Family and sample IDs are unique across all projects, so
creating a family refuses an ID that another project already uses (`409 Family id already
exists`, `409 Sample id already exists`). A user who may create families (the Family Builder
is open to an analyst of a project) can learn from that that the ID exists, but nothing else
about it. Closing that would take IDs scoped per project.

**Session tokens.** Tokens are bearer JWTs kept in `localStorage`. Moving them to HttpOnly
cookies would add CSRF surface and rework the Microsoft sign-in and telemetry paths, so the
damage an XSS could do is bounded instead by the app's CSP, which allows no inline or
outside scripts (§4a), and by the token lifetime: about an hour for Microsoft's access
token, which MSAL renews silently with its refresh token (kept in the tab's
`sessionStorage`, gone when the tab closes) before each request, the API client's and the
IGV viewer's alike (IGV asks for the session's token at each of its requests, SEC-32), and
2 hours for a local token (`ACCESS_TOKEN_EXPIRE_MINUTES`). Every request re-checks
`is_active`, so deactivating a user ends their access at once. So does the import worker,
which runs a queued import job as the account that requested it: a job whose account has
been deactivated, or no longer exists, when a worker takes it up is not run. It ends failed,
saying why, before anything of the family is written, and the log names neither the account
nor its address (`family_package_import.run_family_import_job`; before, the import ran as the
deactivated account). An import already running when its account is deactivated runs to its
end: the account is checked as the import starts, and stopping the import part-way would
leave its family to the import's fail-clean restore. A leaked token of a
still-active user stays valid until it expires; there is no revocation list, and CoGA's
sign-out does not end the Microsoft session in the browser (the sign-in page offers *Sign out
of Microsoft*). For a single-lab internal deployment this is an **accepted residual**.

The UI telemetry sends its last events as the page is hidden or closed, with a `keepalive`
request that cannot wait for a renewal: it sends the token the session holds. Its flush timer
therefore renews a Microsoft session's token that expires within five minutes, also when
nothing is queued, and again when the page is hidden or shown (`lib/telemetry.ts`). Before, a
page closed after an idle hour sent its last events with an expired token, and the API refused
them (found while fixing SEC-32). Events that would go with an expired token anyway (the
device slept, or MSAL needs the user), a batch the API refuses for its session (401), and the
events queued when another request's 401 ended the session are neither sent to be refused nor
dropped: they wait in the browser's `localStorage` under the account they were queued under
(the token's Microsoft object ID, else its subject), at most 200 per account, and go out with
that account's next flush the API takes, after a renewal or its next sign-in. They are never
sent under another account. A batch stays queued until the API acknowledges it, so the flush
at page close sends one still in flight with the rest. The browser gives each event its id
(`event_id`), under which the API stores it once (SEC-17, §2), so a batch sent again stores
nothing twice. Before, the events of a session another request's 401 ended were dropped, as
no token was left to send them with, and a batch in flight as the page closed was lost (both
found while fixing the page-close flush, #1018).

The page-close `keepalive` keeps its events the same way before it is sent, and they leave
the browser only once the API has taken them (a 2xx answer): a keepalive that fails, is
refused, or whose answer the closing page never reads goes out again with the account's next
flush the API takes, and the API stores an event that did arrive once. Each kept event is
stored under a key of its own (`coga.ui-events.kept.<event_id>`), so tabs keeping or sending
events at the same moment cannot undo each other's writes; at worst two tabs both send an
event, which the API stores once. Only the limit of 200 per account (the oldest go first)
removes a kept event the API has not answered. An event made with no session (on the sign-in
page, or once the session has ended) is not recorded: the API records every event of a batch as
the act of the user whose token sends it, and no account made this one. Before, nothing read
the keepalive's answer, so one that failed lost its events; tabs read, changed and wrote back
one shared value, so one tab's write could undo another's; and an event made with no session
went out with the session the next flush found, if any, as that user's (SEC-36).

**Emergency (break-glass) administrator.** With Microsoft sign-in there is one local
account, the emergency administrator: the account `ADMIN_USERNAME` names (`coga-admin` on
Google Cloud), seeded by the deployment, bound to no Microsoft object ID, with an
administrator's role that CoGA will not change, and active: CoGA will not deactivate it, so
the break-glass sign-in works whenever the override is switched on. Its password is `ADMIN_PASSWORD` (Secret
Manager): the seed replaces the stored hash whenever it no longer matches, so rotating the
secret rotates the password. It signs in with that password at `/login/emergency`, and only
while `AZURE_ADMIN_OVERRIDE` is on (default off, deployment-gcp.md §12.12); any other
account's password, and any local token not its own, is refused. Every such sign-in and every
request made with it is logged (`azure_admin_override`, WARNING) and flagged in its audit row
(`request_meta.emergency_sign_in`, *Emergency sign-in* on the *Audit logs* page). While the
override is on, the admin trust boundary includes anyone holding `SECRET_KEY` or the
emergency password, without MFA, so switch it on only for an emergency and off afterwards.
The page is not linked from the sign-in page, so nobody is led to type a Microsoft password
into CoGA.

**Accepted residual: staff roster.** `GET /api/users` returns the names and e-mail addresses
of every active account to any signed-in user. The family page's assignee and reviewer pickers
no longer read it: they read the family's own list, its projects' analysts and the admins
(§1). CoGA is installed for one lab, where all users
are colleagues, so there is no tenant boundary to protect. With Microsoft sign-in this holds
only while the app registration requires assignment to the lab's group (deployment-gcp.md
§12.12): any other member of the directory who could sign in would get an account, and so
see the roster, though no patient data.

## 2. Access / audit logging

- ✅ **Who accessed what, and when.** The request middleware
  (`middleware/request_logging.py`) records every API request in `audit_log_events`: the user
  (when signed in), method, path, status, time and client address. Failed logins are
  counted separately (`auth_login_attempts`).
- ✅ **Requests for other projects' records stay visible in the audit.** Such a request is
  answered as for an unknown ID (§1), but its row carries `request_meta.record_hidden`, so the
  audit shows who asked for families, samples or projects outside their own. The admin
  *Audit logs* page flags the row in its *Flags* column (*Family outside the user's projects*),
  and shows nothing else of `request_meta`, which also holds two request headers
  (`AdminAuditLogsPage.tsx`).
- ✅ **Accounts made and changed, and the emergency sign-in, stay visible in the audit
  (SEC-4).** The row of the request that made an account at a first Microsoft sign-in, or
  changed an account's role or activation, carries `request_meta.account_change`: the account,
  and each field from and to. The emergency administrator's password sign-in, and every
  request made with it, carries `request_meta.emergency_sign_in`. The *Audit logs* page flags
  both in its *Flags* column (*Account …: role viewer → admin*, *Emergency sign-in*).
- ✅ **Minimal personal data.** Query strings are reduced to their keys by default
  (`AUDIT_LOG_QUERY_STRING_MODE=keys`), and secret-like body fields are masked.
- ✅ **Append-only.** Triggers in `04_traceability.sql` block DELETE and UPDATE on
  `audit_log_events` and on the UI-event log `ui_events` (SEC-7), and `05_grants.sql` revokes
  UPDATE, DELETE and TRUNCATE on both from the runtime role `coga_app`. The one exception is
  the `ON DELETE SET NULL` unlink when a user account is deleted; the copied `user_email` and
  `user_role` keep the actor. Neither is hash-chained: a request row and a UI event record
  access and use, not a clinical decision, and a UI event is what the browser reports of itself.
- ✅ **No silent loss (S-5).** A full queue applies backpressure for up to
  `AUDIT_LOG_BACKPRESSURE_TIMEOUT_SECONDS` and then writes the event directly; the worker
  retries failed writes (`AUDIT_LOG_MAX_WRITE_ATTEMPTS`) and then writes the batch's events one
  at a time; an event that still cannot be stored is logged at ERROR with its (already masked)
  payload and counted for alerting. The same holds without the queue, with `AUDIT_LOG_MODE=sync`
  or while no worker runs (before startup completes, after shutdown): each event is written as
  its request runs, and a write that fails is logged and counted the same way, where it used to
  leave a warning the alert did not count. Neither the request nor a batch of UI events fails
  because of it: the UI-event endpoint still answers 202, so the browser does not resend the
  events already stored. One event the database refuses for its own sake (a constraint, or a
  value its driver cannot encode, such as a UI event's time before the year 1 in UTC) fails
  every attempt of its batch's INSERT, and used to take the batch's other events, up to
  `AUDIT_LOG_BATCH_SIZE` of any user's, with it. Written one at a time, each once and without
  waiting, it now costs only itself. A write that cannot reach the database at all (no
  connection) ends that pass, and the events not yet written are recorded without being tried,
  as the whole batch used to be. Outside
  development the backend refuses to start with `AUDIT_LOG_DROP_ALLOWED=true`, which drops
  events (`services/event_pipeline.py`), or with `AUDIT_LOG_MODE=off`, which writes no
  request or UI-event log at all (`core/config.py`).
- ✅ **Each event stored once, and a deleted user's row kept (SEC-17).** A commit whose answer
  never reached the worker (the connection dropped as Postgres committed) looked like a failed
  write, and the batch written again stored each of its rows twice. Each event now carries an
  id of its own, `event_id`, given when the event is made, before it is queued; the writers'
  INSERT does nothing for an id the table holds (`ON CONFLICT (event_id) DO NOTHING`, unique in
  both tables), so every write of an event stores it once. A UI event's id is the one the
  browser gave it (a UUID; an event without one gets the server's), so a batch the browser
  sends again, one in flight as its page closed, is stored once too. An id the table holds
  stores nothing and changes nothing: a client can keep only its own event out with one, as it
  could by not sending it (`routers/ui_events.py`). A row naming a user whose account
  was deleted while the row waited in the queue (operator SQL only) failed the foreign key on
  `user_id` and was lost. Written alone, it is now stored without the link: the user's id is
  kept in `request_meta._deleted_user_id` (`detail._deleted_user_id` for a UI event), and
  `user_email` and `user_role` keep the actor. The log says so by the error's kind, without
  values; the event is stored, so it is not counted as lost
  ([database.md](database.md)).
- ✅ **No request escapes the audit table.** Postgres refuses a NUL, half a surrogate pair,
  `NaN` and `Infinity` in TEXT or JSONB. A signed-in user could put one in a JSON field the
  endpoint ignores, or `%00` in the URL: the action was carried out, and its row, with every
  other row of its async batch, reached only the log. Such a value is now stored as a visible
  escape, with the columns named in `request_meta._escaped` ([database.md](database.md)); the
  UI-event log does the same, in `detail._escaped`. The *Audit logs* page shows the flag,
  with the columns, in the *Flags* column of both views. A NUL in the URL is now refused at
  sign-in (§1), and the refusal's row is kept the same way, under the caller's name. A UI
  event's `occurred_at`, the browser's clock, is kept only from a day before to five minutes
  after the server received the event; any other value is stored as the receive time, with
  the client's value in `detail._client_occurred_at`, which a client cannot set. Before, a
  value the database driver cannot convert to UTC, such as `0001-01-01T00:00:00+05:00`, lost
  its event, and in the async mode every other event of its batch, any user's
  (`routers/ui_events.py`).
- ✅ **A failed audit write is logged by its kind.** The log line names the exception by its
  type and SQLSTATE (`describe_error` in `core/coga_logging.py`), never by the error's text,
  which quotes the statement and the row it could not insert. The row itself is logged once,
  as its masked payload (S-5).
- ✅ **No request body and no error text in the application log.** The request body is written
  only to `audit_log_events`, and so is the text of an error, which for a failed statement
  quotes its SQL and parameters: values from the request, or read for it. An unhandled error's
  500 line gives the error's kind (`describe_error`: the exception type with the driver's error
  and SQLSTATE, or ClickHouse's error code and name), the route template and the frames of
  every exception in its chain, without their messages (`describe_traceback`). Starlette raises
  the error again to uvicorn once it has answered the 500, and uvicorn logs it with its
  traceback; a filter writes that traceback the same way (`RedactServerErrorFilter`, installed
  by `main.py`). The full text stays in the request's audit row (`audit_log_events.error`),
  which only an admin reads. The other lines that log a failed query on a request path (Sample
  QC, the ClickHouse variant query and its retry) name it the same way. Every other line that
  carries an exception (`logger.exception()`, `exc_info=True`), from any logger, CoGA's or a
  library's, and at any level, is written the same way by the JSON formatter
  (`JsonLogFormatter`): the exception's kind in `error`, the frames of its chain in
  `traceback`, never its message. If the traceback cannot be written, the line still goes out
  with the kind, because a formatter that fails makes `logging` print the logged exception's
  full text to stderr. The one exception is a row the audit table cannot store: it is logged at
  ERROR with its masked payload, body and error text included, so it can be restored (S-5).
  What an event loop reports to its exception handler (a task's exception that nobody
  retrieved, a callback that raised, a transport's error) is logged by
  `log_event_loop_exception`, which the lifespan installs on the loop uvicorn runs the app on;
  every background worker runs there as a task. asyncio's own handler, and uvloop's, wrote
  each object's repr into the message: a task's holds its exception's message, and the
  callback's line quotes its arguments (uvloop; asyncio on Python 3.12). The line now gives
  asyncio's fixed message up to the first object it quotes (a message it does not know is not
  written), the task by its name and coroutine, other objects by their type, and the exception
  by its kind and frames. asyncio's debug mode, which writes reprs in lines of its own, is never
  turned on.
- 🟡 **Request bodies are logged with their clinical content**; only secret-like keys are
  masked. Consider masking PHI fields if bodies are kept long-term.
- ⛔ **Byte-level downloads (S-4).** The backend logs that it issued a signed URL, but the
  browser fetches the bytes from the bucket directly. The bucket's data-access audit log is
  configured only in the central infra template, and no PHI is served from a bucket yet
  (§4).

The clinical audit trail is separate: classifications, tags and notes, phenotype changes,
compound-het pair reviews, pedigree and member changes, a family's deletion and sign-outs,
append-only and hash-chained per family, with the pedigree's structure versions on a chain of
their own; see
[clinical-traceability.md](clinical-traceability.md).

## 3. Encryption

- ✅ **Passwords** are bcrypt-hashed. Signed URLs are HTTPS.
- 🟡 **To the datastores (S-2).** The backend supports TLS to both: `POSTGRES_SSLMODE`
  (for example `require` or `verify-full`) and `CLICKHOUSE_SECURE=true` with
  `CLICKHOUSE_HTTP_PORT=8443` and `CLICKHOUSE_VERIFY=true`. The defaults stay plain for local
  work. The Terraform target reaches Cloud SQL through the Cloud SQL connector, on an
  instance that accepts encrypted connections only, and serves ClickHouse over HTTPS on 8443
  with a private CA the backend verifies.
- 🟡 **At rest (S-1).** Docker Compose, for local work, keeps Postgres and ClickHouse on plain
  Docker volumes. The Terraform target encrypts Cloud SQL, both ClickHouse disks and both
  buckets with a customer-managed key (CMEK), which is a required variable with no fallback.
- ✅ **In transit at the edge.** The HTTPS load balancer accepts TLS 1.2 and later, with the
  `MODERN` profile. Behind it the app page and the API both send
  `Strict-Transport-Security: max-age=63072000; includeSubDomains`: Terraform sets
  `ENABLE_HSTS=true` on both Cloud Run services, so a browser that has opened the app keeps
  to HTTPS for that host for two years. The API's 500 for an exception that nothing else
  answers carries it too (SEC-23). The load balancer's HTTPS URL map sets the same header on
  every answer it relays, in place of the services' own, so Cloud Run's own error pages carry
  it as well. Whether it reaches the answers the load balancer and Cloud Armor give
  themselves (a refusal, a rate limit) is to be checked on the deployed load balancer.
  Docker Compose serves plain HTTP and leaves it off.

## 4. Object storage / deployment path & PHI scoping

The deployment is written as Terraform in [`terraform/`](../terraform/) (Cloud Run, Cloud SQL,
a ClickHouse VM, buckets, Cloud Armor), but it has never been applied: there is no GCP
project yet. Each item below is therefore written and reviewable, without deployed evidence.

| Item | State |
| --- | --- |
| S-1 encryption at rest | 🟡 CMEK on the database, the disks and both buckets, in Terraform |
| S-2 TLS to the datastores | 🟡 supported by the app; set in Terraform |
| S-3 secrets | 🟡 seven application secrets in Secret Manager (the metrics token among them), their values added by hand, plus the ClickHouse TLS key and certificate that Terraform generates; Cloud Run reads them by reference; the Terraform state and plan hold no password or private key (the owner's password and the TLS keys pass through write-only arguments); no rotation automation |
| S-4 byte-level download audit | ⛔ open: configured only in the central infra template, and PHI is not yet served from a bucket |
| S-8 network | 🟡 private database and ClickHouse, no SSH, Cloud Run reachable only through the load balancer, Cloud Armor; the WAF enforcement, the institutional IP allowlist and the ClickHouse egress lockdown are go-live switches |

What the application relies on there:

- **Buckets.** A PHI bucket (family data and packages) and a reference-data bucket, both with
  uniform bucket-level access, public access prevention and CMEK.
- **Least privilege.** The backend's service account holds `roles/storage.objectViewer`
  (read-only) on both buckets. The reference-data bucket is mounted read-only at
  `/data/ref-data`, and nothing in the app writes to either bucket; reference files are loaded
  by the operator. The grant covers the whole bucket, not a prefix.
- **Signed URLs** (`S3_PRESIGN_EXPIRY_SECONDS`, default 1 hour; GCS uses the same setting)
  are bearer links: anyone who has one can fetch the object until it expires. Keep the time
  short; access is checked before a URL is issued.
- **Client address.** Behind the load balancer the backend takes the client address
  `TRUSTED_PROXY_HOPS` entries from the right of `X-Forwarded-For` (Terraform sets 2), never
  the left-most entry, which a client can set. The audit log and the sign-up and login
  throttles use it.

### 4a. Web tier: the single-page app and its `/api` proxy

- ✅ **Logout leaves nothing behind.** It flushes the pending UI telemetry, clears the query
  cache (which holds family data) and only then the session, and after a Microsoft sign-in
  MSAL's accounts and tokens in the tab. The one exception: UI events the API has not taken
  (their token had expired by the logout, or the logout's keepalive did not arrive) wait in the
  browser under their account for its next sign-in, rather than being lost (*Session tokens*,
  §1). Login starts with an empty cache. The Microsoft
  sign-in always asks which account continues, so a shared workstation never signs the next
  person in as the previous one silently, and the page CoGA asked for before it travels to
  Microsoft only as a random key (the path, with any family ID in it, stays in the tab).
- ✅ **A sign-in returns only to a page of this site.** The login page follows a `next` only
  in the form the route guards and the expired-session handler write (`signInPath` in
  `lib/auth.ts`): a `/`, a letter or a digit, then printable ASCII without a backslash
  (`SAME_SITE_PATH` in `pages/auth/LoginPage.tsx`, REQ-UI-006). Any other value opens the
  dashboard, as an absent one does: a scheme, `//`, a backslash, whitespace or a control
  character, and a path that starts with a percent-encoded character, such as `/%09/host`,
  which a browser reads as `//host` once the query string is decoded (#833). The backend reads
  no `next` and builds no redirect from user input. The proxy rewrites only a redirect on the
  backend's own internal origin, into a path on this site with one leading slash, so it never
  turns one into `//host` or `/\host` (`frontend/proxyLocation.mjs`, #948).
- ✅ **Encoded path segments.** API paths are built with `apiPath` (`lib/apiPath.ts`), which
  percent-encodes every identifier, so an imported id such as `../families/F1` cannot
  redirect a call. Query strings go through `raw()`. The URLs the backend hands the browser
  to follow (the alignment and signal-track manifests IGV reads, the QC-report link) encode
  each ID the same way (`backend/app/core/url_path.py`), so an ID holding `?`, `#` or `%`
  reaches its own file and one holding `../` cannot reach another family's. Two kinds of ID
  still cannot be reached through a path segment: one that is exactly `.` or `..` (a browser
  resolves it whatever its encoding) and one holding `/` (the backend matches its routes on
  the decoded path); such a request answers 404. Whether the ID rule should refuse them is
  open. In-app routes are built the same way, with `routePath` (`lib/routePath.ts`), so a
  link or a navigation to a family, panel, CNV or report code keeps an ID holding `?`, `#`,
  `%` or `/` whole; raw, `?` and `#` cut the path short and `/` added a segment no route
  matched (SEC-15). A guard test finds any route template left raw. The IGV page's Back link
  takes its `back_path` only when it is one of the family pages, as the HPO page's (#526)
  does: a link could send it to another site. A file named after an ID, the PED download, a
  variant export or a signed URL's file, carries its name in `Content-Disposition` encoded
  as RFC 6266 has it (`backend/app/core/content_disposition.py`): a name with a quote, a
  backslash, a percent sign or a character outside ASCII goes in `filename*`, with an ASCII
  stand-in in `filename`. Raw, a quote ended the name early, and an ID beyond Latin-1 failed
  the PED download with a 500.
- ✅ **Escaped tooltips.** The d3 tooltips built as HTML escape their values
  (`lib/escapeHtml.ts`).
- ✅ **Proxy robustness** (`frontend/server.mjs`, `proxyRequest.mjs`). A reset mid-stream ends
  that response instead of crashing; the backend must start answering within
  `API_PROXY_TIMEOUT_MS` (else 504); a client that goes away aborts the upstream request; the
  502/504 body does not name the internal backend. That 502 or 504 is the server's own answer
  and carries the server's security headers (below), HSTS as `ENABLE_HSTS` says; it was sent
  before the middleware that sets them, and carried none. An answer the proxy relays carries
  the backend's own.
- ✅ **Forwarded headers.** The proxy sends the backend one clean `X-Forwarded-For` (its peer,
  or what the trusted proxy in front of it added) plus `X-Forwarded-Proto` and `-Host`, so a
  browser cannot choose the address the backend throttles and audits. On Cloud Run the load
  balancer sends `/api` straight to the backend; this proxy is the path for Docker Compose.
  Terraform still gives the frontend service the backend service's address as `BACKEND_URL`
  (SEC-23), where the proxy used to aim at Compose's `http://backend:8000`. The backend's
  ingress admits only the load balancer and internal traffic, and the frontend's traffic does
  not go through the VPC, so on Cloud Run the proxy would be refused there all the same.
- ✅ **Headers** (`frontend/securityHeaders.mjs`): a CSP that allows scripts only from the app
  itself (`script-src 'self'`), no framing and no plugins, and a `Permissions-Policy` that
  denies the camera, microphone, geolocation, payment, USB and the other device APIs. API
  responses carry `default-src 'none'` (`backend/app/middleware/security_headers.py`), the
  500 for an exception that nothing else answers included (SEC-23).
  Where TLS ends in front of the app, `ENABLE_HSTS=true` adds the HSTS header the API sends
  (§3). The frontend server reads the setting as the backend does, so `1`, `True` or `yes`
  turn on both headers (SEC-23).
- 🟡 **CSP `connect-src`** is `'self' https:` by default, because IGV loads genomes from a
  changing set of hosts and a signed CRAM URL points at the object store. A deployment that
  knows its hosts narrows it with `CSP_CONNECT_SRC`, keeping
  `https://login.microsoftonline.com` with Microsoft sign-in (the browser fetches its tokens
  there). `style-src` keeps `'unsafe-inline'`
  because IGV injects inline styles.
- ✅ **Docker Compose** publishes Postgres, ClickHouse and the backend on loopback only, and
  the frontend container gets only the settings its server reads.

## 5. CI enforcement of the gates

Every pull request and push to `main` runs the gates in `ci.yml` (backend, frontend, smoke,
e2e, e2e-playwright, catalogue, plus coverage, the SBOM and a build of both production images
that pushes nothing) and `security.yml` (`deps`: blocking `pip-audit --require-hashes` and
production `npm audit`; `secret-scan`: gitleaks over the full history; `codeql` for Python and
JavaScript/TypeScript). All of these but the SBOM and the image build, and GitHub's `CodeQL`
code-scanning result, are the twelve required status checks on `main` (S-6, the ruleset
`CoGA_branch_protect`); a branch need not be up to date with `main` before it merges.
No approving review is required: pull requests need no approval, and the four-eyes review is
QA's, per release, signed before the tag. The list is in [docs/testing.md](testing.md); the
policy, QA's review and the open gaps are in
[TF-18 §6](regulatory/TF-18-change-configuration-management.md).
`build.yml` checks the Terraform (`fmt`, `validate`) on pull requests and deploys from `main`.
Every suppressed advisory is recorded in
[SECURITY-AUDIT-ALLOWLIST.md](../SECURITY-AUDIT-ALLOWLIST.md).

## Summary

The application layer applies project-scoped access consistently, with no cross-project IDOR
found, refuses every change to a reader (a member with read-only access to a project), keeps a
durable append-only audit trail, needs a signed-in user for reference data,
throttles sign-ups and logins, bounds input sizes, decompression and paths, refuses a control
character in what a request looks up, and refuses to
start on weak or shared secrets or with the request audit log switched off. S-5 (audit
durability), S-6 (required checks) and S-7 (dependency pinning) are closed. The open items are deployment-level: S-1, S-2, S-3 and S-8 are written
in Terraform and wait for the first deployment and its evidence; S-4 is open. See
[TF-13 §3](regulatory/TF-13-cybersecurity.md).
