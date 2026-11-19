# 2. User roles, permissions & isolation

This chapter is about *who* may see or do something: the five global roles, isolation per *project* and the access level per project (analyst or reader). Then comes defence in depth: a restricted database role, security headers and CORS, the refusal of weak secrets and the protection of uploads. The source is `docs/security-posture.md`; the cybersecurity analysis for the technical file is in `docs/regulatory/TF-13-cybersecurity.md`.

A few terms first. **RBAC** (*Role-Based Access Control*) is access based on roles. A **JWT** (*JSON Web Token*) is a signed access credential that the browser sends along with every request. In FastAPI (the Python framework of the backend), a **dependency** is a function that runs before an endpoint and can refuse the request. An **endpoint** is one API address, e.g. `GET /api/families/{family_id}`.

## Which roles exist

CoGA has five global roles (`users.role`), listed here from low to high. Each role has all the rights of the roles before it. The database allows no other value. The owner decided this on 2026-11-04.

| Role | What this role may do |
| --- | --- |
| `viewer` | **Read**, in the **projects they are a member of**: view and search the families, samples, variants, tracks, reports and signed versions, export CSV and print. They change nothing, whatever access level they have in a project. The only things they save are their own filter presets and the events of their own use of the interface. They do not see the clinical audit trail. Every new account starts as viewer, without a project. |
| `analyst` | Also **analyse**, in the projects where their access level is analyst (see *Access level per project*): review and tag, ACMG and CNV classifications, phenotypes, the workflow fields (status, assigned to, reviewed by) and new families (Family Builder). There they also read the clinical audit trail. |
| `supervisor` | Also **close an analysis with *Sign out***, the analytical sign-out, in the projects where they are analyst (see *Signing out: supervisor or higher*). The medical sign-off of the clinical report takes place in the EPD, not in CoGA. |
| `superuser` | Also **every project**, member or not: they see and change every record, sign out everywhere, and appear in the *Assigned to* and *Reviewed by* drop-down lists of every family. In addition, the **configuration the analyses read**: gene panels, variant tags, preset filters and the sequencing QC thresholds, and the **package import**, except overwriting an existing family. |
| `admin` | The **administrator**: also all of the administration (see *Superuser or administrator* below). |

In this manual an **administrator** is someone with the role `admin`. A superuser never counts as an administrator.

**Where in the code:** the allowed roles are a `CHECK` rule on the table `users` in `backend/db/schema/postgres/01_access.sql`. In the backend they are in `backend/app/services/access_control.py`: the order in `USER_ROLES`, with `has_role` (this role or a higher one), and for each right the roles that have it: `CHANGING_ROLES` (`analyst` and higher), `SIGN_OUT_ROLES` (`supervisor` and higher), `SUPERUSER_ROLES` (`superuser` and `admin`) and `ADMIN_ROLES` (only `admin`). The functions that query them are `role_changes_records`, `may_sign_out`, `has_superuser_rights` and `is_admin_user` (only `admin`). In the frontend: `USER_ROLES`, `hasRole`, `isAdmin` (only `admin`) and `hasSuperuserRights` in `frontend/src/lib/auth.ts`.

**Superuser or administrator.** Besides every project, a superuser has:

- the gene panels: create, change and delete them, import a PanelApp panel and rebuild the Mendeliome (chapter 15);
- the variant tags: create, change and delete the custom tags, on the administration page and on the routes of a family (chapter 10);
- the overview of everyone's preset filters (chapter 15);
- the sequencing QC thresholds (chapter 15);
- the package import: the routes `/api/family-imports`, with everyone's import jobs (chapter 6). They may not overwrite an existing family (`conflict_mode: "overwrite"`, not a dry run); as a dry run they may, and `cancel` and `update` too.

On the administration dashboard they see only those five workspaces: *Gene panels*, *Sequencing QC thresholds*, *Package import*, *Variant tags* and *Preset filters*. All the rest of the administration is for an administrator only: accounts and roles (the *Users* page, `PATCH /api/auth/users/{id}`) and the account list `GET /api/users`; projects and their access; the family and sample data and their deletion; the family statuses; organisms and assemblies; HPO, Monarch and the repeat catalogue; ClickHouse maintenance; the audit logs and the integrity checks; the NIPT artefacts; replacing a family's annotation manifest; refreshing the gene reference; rebuilding the clinical CNV knowledge base; a PED upload and the separate per-file uploads; editing the family structure; and overwriting an existing family, with a PED upload, the Family Builder or a package import (REQ-SEC-002).

**Assigning a role.** An administrator sets an account's role on the *Users* page (`PATCH /api/auth/users/{id}` with `role`), after a confirmation in a dialog. The drop-down list shows the five roles from low to high, each with a tooltip that says what the role may do. Nobody changes their own role or deactivates their own account. The emergency administrator (chapter 5) keeps the role `admin` and stays active. Every change is in the request's audit row, with the role before and after (`request_meta.account_change`). Anyone who registers, or signs in for the first time with Microsoft or via CAS, gets an account with the role `viewer` and without a project. They see no patient data until an administrator adds them to a project, and change nothing until an administrator gives them a higher role (chapter 5).

**Who signs out.** Only a `supervisor` or higher can sign out: an administrator gives the role `supervisor` to whoever the laboratory authorises to do so. An `analyst` changes the records of the projects in which they are analyst, but does not sign out.

There is no separate permissions table per user. What someone may see follows from their role and their **project membership**: the link table `project_users`, which for each member also stores their access level in that project. A family is attached to one or more projects through `family_projects`. A sample is visible through the projects of its family. `sample_projects` additionally links a sample directly to projects, among other things for the sample drop-down list of the Variant Explorer. A viewer, analyst or supervisor therefore sees exactly the families of which at least one project is also one of their projects; a superuser and an administrator see them all.

The token carries only the identity: the user's object ID for a Microsoft token, the email address for a local token. The role and the project list are **not** in it. On every request the backend loads the user, their role, their projects and their access level in each of them fresh from Postgres. Revoking therefore takes effect immediately. Anyone who is deactivated, removed from a project, made a reader there or given a lower role loses the access or the right at the next request, without waiting for the token to expire. An administrator who becomes superuser thus loses the administration; a supervisor who becomes `analyst` loses signing out.

A job that has not yet run does not run if the account that requested it has since been deactivated or has lost the role the job requires (decision of the owner of 2026-10-28). For an import job (chapter 6) that role is superuser or administrator, and administrator for an import that overwrites an existing family; for a refresh of the gene reference (chapter 13) and a rebuild of the clinical CNV knowledge base (chapter 15) it is administrator. That rebuild also loads nothing if the deactivation or the loss of the role only happened during its build. The job ends `failed` with the reason, and nothing is written.

## Project-scoped access: how every data request is restricted

Every request for a family, sample or variant passes through the same checkpoint. There are two mechanisms: one for lists, one for single objects.

**Lists are filtered in the database, not afterwards.** When a user requests the families, the project filter is in the SQL query itself: an `EXISTS` condition on `family_projects` lets through only families attached to at least one of their projects. Families outside their projects therefore never leave the database; a forgotten filter in the application code cannot let them leak. A user without projects immediately gets an empty list. For a superuser and an administrator the filter is dropped (`has_superuser_rights`).

**Single objects are checked when they are fetched.** When someone requests one family by its id, the backend checks whether the projects of that family and those of the user overlap. If not, the backend answers exactly as for a non-existent id: `HTTP 404` with `Family not found`, or for a sample `Sample not found`. A project outside the user's projects, given in the Family Builder or for a gene profile, likewise gets `404 Project not found`. A superuser and an administrator pass. So nobody can open a family by guessing its id (protection against *IDOR*, *Insecure Direct Object Reference*). Nor does the answer reveal whether a family or sample of another project exists. The audit log does record the difference (`request_meta.record_hidden`, chapter 7).

Both checks come together in `build_family_metadata_context`: the shared checkpoint that almost every family-bound view (variants, tracks, report) goes through first. It loads the family only if the user may see it, and restricts the visible projects to those of the user. `build_sample_metadata_context` does the same for one sample.

**Where in the code:** `backend/app/services/access_control.py` (the access rules, among them `user_can_access_metadata_projects`), `backend/app/services/metadata_service.py` (the filtered queries and `get_accessible_family_mapping`) and `backend/app/services/family_metadata_context.py` (the checkpoint).

## The backend is the real gate

Authorisation is enforced only in the backend, with three dependencies:

- **`get_current_user`** checks the token and loads the user fresh from Postgres. It refuses (`HTTP 401`) if the token is invalid or the user does not exist or is not active. How the token is checked is described in [chapter 5](05-login-authentication.md).
- **`get_current_superuser`** builds on it and gives `HTTP 403 Superuser access required` if the role is not `superuser` or `admin` (`has_superuser_rights`). This is the gate for the configuration the analyses read and for the package import.
- **`get_current_admin_user`** also builds on `get_current_user` and gives `HTTP 403 Admin access required` if the role is not `admin` (`is_admin_user`). This is the gate for the rest of the administration and for the destructive actions. A superuser does not get through.

A plain endpoint such as `GET /api/families/{family_id}` asks for `get_current_user` (authentication) and leaves *authorisation* to the service layer, through the checkpoint above. An unknown `family_id` and one from another project therefore get the same answer: `404 Family not found`. A `family_id` with a control character already gets `400` during authentication, the same for everyone ([chapter 5](05-login-authentication.md)). The administration router (`/api/admin/...`) sits behind `get_current_admin_user`, except the variant tags, the overview of the preset filters and the sequencing QC thresholds: those ask for `get_current_superuser`, as do writing the gene panels (`/api/panels`), the variant tags on the routes of a family and the whole package import (`/api/family-imports`). Creating, changing and deleting projects is for an administrator. The project list itself shows a viewer, analyst or supervisor only their own projects.

Two nuances for the auditor:

1. No check outside `access_control.py` compares a role with a literal role name: every check asks the sets and the functions there (`is_admin_user`, `has_superuser_rights`, `role_changes_records`, `may_sign_out`). A test refuses any comparison of a role with a literal role name elsewhere (`backend/tests/test_admin_role_checks.py`). A superuser never counts as an administrator: `backend/tests/test_admin_route_gating.py` requires every administration route to refuse a superuser, and every superuser route to refuse a viewer, an analyst and a supervisor.
2. The restricted user list (`GET /api/users`: name and email address of every active account) is for administrators only (`get_current_admin_user`). Anyone who is not an administrator, a superuser included, gets `403`, as on the other administration routes; a deactivated administrator gets `401`. No screen reads that list: the *Assigned to* and *Reviewed by* drop-down lists of a family read the family's own list (below), and the *Users* page reads `GET /api/auth/users`. The restriction to administrators is a decision of the owner of 2026-10-28.

**Where in the code:** `backend/app/dependencies.py` (the three dependencies), `backend/app/routers/admin.py`, `backend/app/routers/panels.py`, `backend/app/routers/family_imports.py` and `backend/app/routers/projects.py`.

## Access level per project: analyst or reader

Besides their role, every project member has an **access level** in that project (decision of the owner of 2026-10-14). The level only takes rights away, never adds them:

| Level | May do in the project |
| --- | --- |
| **analyst** (`analyst`) | Read, and change as far as their role allows: analyse with the role `analyst`, and also sign out with the role `supervisor`. A viewer with this level changes nothing. |
| **reader** (`reader`), e.g. a student | Only read, whatever role they have (below superuser): view and search the families, variants, tracks, reports and signed versions, export CSV, print the report and a signed version, and save their own filter presets. Not: see the clinical audit trail, or change anything clinical: no phenotypes, reviews, tags, ACMG or CNV classifications, *Sign out*, workflow fields (status, assigned to, reviewed by), new families, uploads, imports, edits or deletions. |

The same person can be analyst in one project and reader in another. If a family is attached to several projects, an analyst of any one of them may change it, just as a member of any one of them may see it. A superuser and an administrator have all rights in every project, member or not; their level in a project does not count.

**Setting the level.** An administrator chooses the level of each member on the *Projects & access* page, in a drop-down list next to each member of the project. On the *Users* page they set the same memberships from the account's side: *Edit* under *Project access* opens the account's projects, each with its level. The dialog lists what saving will change, and *Save project access* saves everything in one request. In the API it is the field `reader_user_ids` of a project: the members who are readers; the other members are analysts. From the account's side it is the fields `project_ids` and `reader_project_ids` of `PATCH /api/auth/users/{id}`: the same rows in `project_users`. A member without a level is analyst (the column's default value). An update without `reader_user_ids` (or without `reader_project_ids`) lets every membership that remains keep its level. A reader who is not a member is refused (`400`); so is a project that does not exist (`404`), and then nothing changes, not even a role that was in the same request. Every change from the account's side is in the request's audit row, from and to (`request_meta.account_change`). Whoever writes the memberships, from the project or from the account, first takes the same lock (`_lock_project_memberships`): two saves at the same time wait for each other, without deadlock, and the audit row names the memberships the change actually replaced.

**Enforced in one place.** On every request, `get_current_user` records whether it changes something: every method except GET, HEAD and OPTIONS, unless the route is marked with `changes_only_own_data`. Only the routes for the user's own filter presets and for the events of their own use of the interface carry that mark; a viewer and a reader use those too. The checkpoints of family, sample and project (`get_accessible_family_mapping`, `get_accessible_sample_mapping` and `_resolve_accessible_project_id` of the Family Builder) then refuse a change by a viewer or a reader with `403` and the reason (`require_change_access`). A viewer gets *Read-only access: your role is viewer, so you can view and export records but not change them.* (`VIEWER_READ_ONLY_DETAIL`), a reader the sentence that names their level in the project (`READ_ONLY_DETAIL`). That happens **after** the visibility check: a family outside their projects therefore still answers `404`, like an unknown one. Every route that names a family, sample or project must pass through those checkpoints, so no route can forget the check. The clinical audit trail (`GET /api/families/{id}/clinical-audit`) also refuses a viewer and a reader (`403`, with `VIEWER_AUDIT_TRAIL_DETAIL` or `AUDIT_TRAIL_DETAIL`). The administration routes stay behind `get_current_admin_user` or `get_current_superuser`.

**Signing out: supervisor or higher.** Closing an analysis with *Sign out* additionally requires the role `supervisor`, `superuser` or `admin` (`TF-06`, hazard H15: an analysis signed out by someone without signing authority). `sign_out_report` calls `require_sign_out_authority` after the family checkpoint, so that a family outside the user's projects still answers `404` and a viewer or reader is first told that they can only read, and before every gate, so that an analyst does not go through acknowledgements they cannot use. An analyst gets `403` with *Signing out needs the supervisor role: ask a supervisor of this family's project to sign out the analysis.* (`SIGN_OUT_ROLE_DETAIL`), and nothing is written. The measure against H15 is thus a check in the software. Who gets the role `supervisor` remains a decision of the laboratory (chapter 11).

**In the screens.** The family and the projects the API returns say whether they are read-only for the user (`read_only`: their role is viewer, or they are reader in every project of the family of which they are a member). The family also says whether they may sign out its analysis (`can_sign_out`: their role is supervisor or higher, and they may change the family). A family page that is read-only shows *Read-only access*. The buttons the user may not use are disabled or hidden:

- the review and ACMG dialogs do not save;
- the quick tags are off;
- *Sign out report* is disabled, also for an analyst without `can_sign_out`, with the reason *Signing out needs the supervisor role …*;
- the audit trail is not requested; the report says on screen and on paper why it is not there;
- the phenotypes and workflow fields cannot be changed;
- the Family Builder offers only the projects in which the user is analyst.

That is convenience; the server's refusal is the gate.

**Assigned to and Reviewed by.** A family's *Assigned to* and *Reviewed by* are an active superuser or administrator, or an active analyst of one of its projects whose role is not viewer; never a reader or a viewer. The two drop-down lists on the family page read the family's own list (`GET /api/families/{family_id}/assignable-users`). Like the family itself, that list answers `404` outside the user's projects. If a save names someone else (a reader, a viewer, someone outside the family's projects or a deactivated account), the server refuses it with `422` and the reason (e.g. *their role is viewer*), with the hint *Choose an analyst of one of the family's projects, a superuser or an administrator.*, and nothing changes. A value the family already has stays until someone changes it: a person who became reader or viewer stays named, and the drop-down list shows that person with *(not eligible)*. **Where in the code:** `backend/app/services/family_status_service.py` (`list_family_assignable_users`, `update_family_metadata_for_user`).

**The email to the person a family is assigned to.** A person whom a save newly names under *Assigned to* gets an email after the save, through the mail server in `SMTP_HOST` (chapter 5). The email names the family ID (the pseudonymous lab ID; a decision of the owner), who assigned the family and when, and links to the family if CoGA knows its own address (that of `CAS_SERVICE_URL`); the subject names no family and the email contains no other patient data. No email goes out if the family keeps the same person, if the field is cleared, for *Reviewed by*, or if someone assigns a family to themselves. An email that fails to send leaves the save untouched; the log names no address, no family and not the server's text. **Where in the code:** `backend/app/services/family_assignment_mail.py`, `backend/app/routers/families.py` (`update_family_metadata`).

**Tests.**

- `backend/tests/test_project_access_levels.py` keeps the catalogue of every route that changes something: an administrator's, a superuser's (`SUPERUSER_CHANGE_ROUTES`), own data, or refused at a checkpoint. A new route fails until it is classified.
- `backend/tests/e2e/test_e2e_project_reader_role.py` calls every such route on real Postgres as a reader and requires `403` with the reason. It calls every route that reads a family or sample as a reader and as an analyst, and requires the same answer, except for the audit trail.
- `backend/tests/test_access_rules.py` pins down the rules of `access_control.py` per role and level; `backend/tests/test_report_signout.py` starts with the role of the person signing out (a viewer and an analyst are refused before every gate; a supervisor, superuser and administrator sign out).
- `backend/tests/e2e/test_e2e_global_roles.py` goes through the five roles on real Postgres, with real sign-ins: the viewer reads and is refused a change and the audit trail, the analyst changes and is refused the sign-out, the supervisor signs out, and the superuser, in no project at all, reads the family, opens their workspaces and is refused those of the administrator.

**Where in the code:** `backend/app/services/access_control.py` (`user_can_change_metadata_projects`, `user_can_sign_out_metadata_projects`, `require_change_access`, `require_sign_out_authority`, `require_audit_trail_access`, `changes_only_own_data`, `request_changes_records`), `backend/app/dependencies.py` (`get_current_user`), `backend/app/services/metadata_service.py` (the checkpoints, `read_only`, `can_sign_out` and `_set_project_user_links`), `backend/app/services/report_signout_service.py` (`sign_out_report`), the column `access_level` in `backend/db/schema/postgres/01_access.sql`; in the frontend `frontend/src/lib/familyAccess.ts` and `frontend/src/pages/families/reportSignOut.tsx`.

## Guards in the browser: convenience, not a lock

The frontend has its own guards, but they serve only the user experience:

- **`RequireAuth`** sends anyone without a token to `/login?next=...`.
- **`RequireAdmin`** sends users who are not logged in to `/login` and logged-in non-administrators to `/dashboard`. With `<RequireAdmin superuser />` it also lets a superuser through: that is how it guards the administration dashboard and the five workspaces of a superuser. The menu and the dashboard show *Admin* to a superuser and an administrator.
- **`SessionRedirect`** chooses a destination depending on "logged in or not".

Why this is not a gate: the role the browser uses is in browser storage, and users can change it themselves. At most, that shows them the administration *screens*. Every data or action request still goes to the backend, which checks the role and the projects again, authoritatively.

**Where in the code:** `frontend/src/components/RequireAuth.tsx`, `RequireAdmin.tsx` and `SessionRedirect.tsx`; the session helpers in `frontend/src/lib/auth.ts`.

## Least privilege in the database: the runtime role `coga_app`

Even a compromised application must not be able to rewrite the *append-only* evidence tables. "Append-only" means: only add, never change or delete. A separate, restricted database role exists for that purpose.

`05_grants.sql` creates the role `coga_app` with `SELECT`, `INSERT`, `UPDATE` and `DELETE` on the ordinary tables. On the append-only tables it revokes `UPDATE`, `DELETE` and `TRUNCATE` again:

- `audit_log_events`, `clinical_audit_events`, `report_signouts`, `integrity_anchors` and `qc_threshold_changes`;
- `family_structure_versions`, `ui_events` and `import_delivery_revisions` (the pedigree versions, the log of the UI events and the corrections of a delivered package, chapter 6);
- `ancestry_reference_panels`, `ancestry_results` and `ancestry_sample_choices`, the tables of the exploratory ancestry PCA.

`coga_app` may only read the migration ledger `alembic_version` and the baseline digest `schema_baselines`: only the migration step writes to them, as owner. As a non-owner, `coga_app` also cannot disable a trigger. The runtime can therefore add rows to those tables, but cannot change or delete any existing row: no audit row, signed report, integrity anchor, QC threshold recorded earlier, pedigree version, UI event or package correction. The only permitted change is made by the database itself, when a user or family is deleted: the reference to it is set to `NULL` (`ON DELETE SET NULL`), while a column of the row itself keeps who or which family it was (chapter 11).

The role is shipped **without a login**: by default the application still runs as the owner of the tables. Switching over is an operational step, not a code change. The schema is then applied separately as owner (`backend/app/db_migrate.py`, with `POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP=false` for the app), and the app logs in as `coga_app`. On Google Cloud that is one Terraform variable: `db_runtime_role = "coga_app"` (default `"owner"`). A separate Cloud Run job (`terraform/migrate.tf`) then applies the schema as owner before every rollout and enables the login of `coga_app` with `POSTGRES_APP_PASSWORD`. That job has its own service account, so the API cannot read the owner's password.

Every new append-only table must get the same `REVOKE` in `05_grants.sql`, because the broad `GRANT` automatically gives new tables all rights. The integration test `backend/tests/integration/test_app_role_privileges.py` guards this: it searches the database for every table with a blocking trigger (`*_block_mutation`) and fails if `coga_app` still has `UPDATE` or `DELETE` on it.

**Where in the code:** `backend/db/schema/postgres/05_grants.sql`; the procedure, verification and rollback are in `docs/db-runtime-role-runbook.md`.

## Other defences

### Rate limiting against brute force

Failed logins are counted per email address and per source IP; after a threshold an increasing waiting time follows (`HTTP 429`). Registration is throttled separately per source IP. Behind a proxy, CoGA takes the client IP from the `X-Forwarded-For` header, as many steps from the right as `TRUSTED_PROXY_HOPS` indicates. That way a client cannot choose its own IP. The audit rows use that IP too. How this works is described in [chapter 5](05-login-authentication.md).

**Where in the code:** `backend/app/services/auth_rate_limit_pg.py` and `backend/app/middleware/client_ip.py`.

### Security headers and CORS

Every API response gets strict headers, including the `500` for an error that nothing else handles: a Content-Security-Policy that allows nothing (`default-src 'none'; frame-ancestors 'none'`), `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` and cross-origin isolation. HSTS is on only where TLS terminates in front of the app (`ENABLE_HSTS`). On Google Cloud the load balancer also sets that HSTS header on every response it passes on over HTTPS, including Cloud Run's own error pages. CORS allows only the configured origins, and an origin pattern must be fully anchored (chapter 1).

**Where in the code:** `backend/app/middleware/security_headers.py` and the 500 handler in `backend/app/main.py`; CORS in `backend/app/core/config.py`; the load balancer's header in `terraform/loadbalancer.tf`.

### Refusal to start with weak secrets

Outside development and test (i.e. if `APP_ENV` is not `dev`, `development`, `local` or `test`) the backend refuses to start if:

- `SECRET_KEY` is shorter than 32 characters or is `secret`/`change-me`;
- `CLICKHOUSE_PASSWORD` is empty or is `admin`/`change-me`;
- `INTEGRITY_ANCHOR_SIGNING_KEY` is not the base64 of a 32-byte Ed25519 key, or is equal to `SECRET_KEY` (then anyone who can create session tokens can also sign integrity anchors);
- `POSTGRES_PASSWORD`, `ADMIN_PASSWORD` or a configured `POSTGRES_APP_PASSWORD` is `admin`/`change-me`;
- a configured `METRICS_TOKEN` is shorter than 32 characters, is `secret`/`change-me`, or is equal to `SECRET_KEY` or `INTEGRITY_ANCHOR_SIGNING_KEY` (whoever reads the metrics must not be able to create session tokens or anchors);
- `AUDIT_LOG_DROP_ALLOWED=true` (drop audit rows when the queue is full) or `AUDIT_LOG_MODE=off` (write no audit rows at all) is set;
- the CORS settings are open (chapter 1), or the build has no `GIT_SHA` (chapter 4).

The error message names the fields that do not comply. This is a deliberate *fail-closed* choice: better not to start than to start unsafely. `.env.example` contains only placeholders, precisely to force them to be replaced before a real rollout.

**Where in the code:** `validate_security_defaults` in `backend/app/core/config.py`.

### Uploads and external HTML

- **Bounded uploads.** Uploads (SV, reference, BED, repeats, PED, the NIPT artefact table) and the text files of a family package are read within a size limit and decompressed within a size limit; a small-variant VCF is streamed line by line. That way a small but highly compressed `.gz` file (a "decompression bomb") cannot exhaust the memory. Too large gives `HTTP 413`. The many gzip blocks of a BGZF `.vcf.gz` are read through correctly. `HTTP 400` follows for a gzip file that breaks off in the middle of a block, for bytes after the last block that are not themselves a complete block, and for a BGZF file that does not end with the BGZF end-of-file block (chapter 6). **Where in the code:** `backend/app/services/upload_safety.py`, which decompresses with `gunzip_bounded` from `bounded_download.py`; the limits `MAX_UPLOAD_BYTES` and `MAX_DECOMPRESSED_UPLOAD_BYTES` in `config.py`.
- **HTML sanitisation.** The clinical CNV knowledge base contains small HTML fragments that the UI shows as HTML. A strict allowlist restricts them to safe formatting and refuses everything else (`script`, `img`, event attributes, `javascript:` and `data:` links). This happens both when they are loaded and when they are read back, against stored *cross-site scripting*. **Where in the code:** `backend/app/core/html_sanitize.py`.
- **The pipeline's QC report.** That HTML report contains its own scripts and is treated as untrusted. It opens through a short-lived link to the backend, which is issued only after the access check. The link is valid for one report: that of the family, the sample and the analysis it names (a sample sequenced short-read and then long-read has a report of each). The backend looks for the report only in the package folder read by the import of the analysis that recorded it, and in the family's own folders, at the path the import recorded with the sample. A path that leads out of those, or a symbolic link to another family's folder, gives `404`. The backend serves the report with `Content-Security-Policy: sandbox allow-scripts allow-popups`: the report's scripts run, but in an isolated origin that cannot read the user's session. **Where in the code:** `backend/app/routers/family_qc_reports.py`.

## Auditing (in brief)

Every HTTP action leaves a trace in the append-only table `audit_log_events`: who (user and role), what (method, path, status), when and from which IP. Sensitive data are limited. Of a query string, by default only the keys are kept. Password and token fields are masked, in the audit log and in the UI events, according to one list of keys (`SENSITIVE_KEY_PREFIXES` in `core/coga_logging.py`). A database trigger makes this table append-only. The hash chain and the signed anchors apply to the clinical audit trail and the signed reports (chapter 11). The middleware itself is described in [chapter 7](07-backend-routers-and-services.md).

Every suppression of a security scan (dependencies, secrets, SAST) is documented and dated in `SECURITY-AUDIT-ALLOWLIST.md`.

## Key files

| File | Role |
| --- | --- |
| `backend/app/dependencies.py` | `get_current_user`, `get_current_superuser` and `get_current_admin_user` |
| `backend/app/services/access_control.py` | The roles (`USER_ROLES`, `has_role` and the sets per right), the access rules, the right to sign out and the access level per project (analyst or reader) |
| `backend/app/services/metadata_service.py` | Project filter in the queries |
| `backend/app/services/family_metadata_context.py` | The shared access checkpoint for family and sample data |
| `backend/app/core/config.py` | Refusal of weak secrets, CORS check, upload limits |
| `backend/app/middleware/` | Client IP, security headers and request logging |
| `backend/db/schema/postgres/01_access.sql` | `users` with the role rule, `project_users` with each member's access level, `auth_login_attempts` |
| `backend/db/schema/postgres/05_grants.sql` | The runtime role `coga_app` and the `REVOKE` on the append-only tables, the migration ledger and the baseline digest |
| `frontend/src/components/RequireAuth.tsx` · `RequireAdmin.tsx` | Guards in the browser (convenience, not a gate) |
| `docs/security-posture.md` · `docs/db-runtime-role-runbook.md` | Security overview and the runbook for `coga_app` |
