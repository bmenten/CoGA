# 7. Backend: routers & services in detail

CoGA's API layer follows a fixed pattern: **router → service → storage**. FastAPI gives each endpoint the database session and the logged-in user. A few **security rules apply everywhere**: every database query is parameterised, every `ORDER BY` comes from a fixed list, and every `LIMIT`/`OFFSET` becomes an integer. Further on: the overview of all routers, and the request logging, which records every request in the audit log.

A few terms:

- **Router:** a group of HTTP endpoints (e.g. `GET /api/families/...`), in FastAPI an `APIRouter`.
- **Service:** a Python module with the actual logic (database queries, clinical calculations). Routers deliberately do not contain that logic.
- **Dependency injection:** in an endpoint's parameter list you say "I need X" (e.g. a database session), and FastAPI creates X and passes it in.
- **Pydantic model (schema):** a Python class that describes and checks the shape of incoming and outgoing JSON.
- **Parameterised query:** a database query in which values are passed separately instead of pasted into the text; the standard defence against SQL injection.

## Thin routers, thick services

CoGA keeps three layers strictly separate:

1. **Router (thin):** reads the path and the parameters, has them validated, enforces access and returns the response as JSON.
2. **Service (thick):** the clinical and business logic, such as filters, prioritisation, ACMG rules and hash chains. Only services talk to the storage, apart from a few small, parameterised lookups in routers (in `cram.py`, `signal_tracks.py`, `family_qc_reports.py`, `repeat_expansions.py`, `families.py` and `health.py`).
3. **Storage:** Postgres through SQLAlchemy (asynchronous) and ClickHouse through a direct client.

An example: `GET /api/families/{family_id}/small-variants`.

- **The router** (`backend/app/routers/families_small_variants.py`) receives the page size as an integer with a lower and an upper bound. A value that is too large is refused with `422` before anything runs. A separate dependency reads in the many filter parameters, so that the ordinary endpoint and the CSV export use exactly the same filters.
- **The access checkpoint** `build_family_metadata_context` loads the family only if the user may see it, and restricts the projects to those of the user (chapter 2). That happens before any variant query goes to ClickHouse.
- **The service** (`backend/app/services/clickhouse_family_variants.py`) builds the ClickHouse query, with the scoping in it (`e.family_guid = %(family_guid)s`, `e.project_guid IN %(project_ids)s`) and the values as parameters.
- **The response** is checked against a Pydantic model and returned as JSON.

The router touches no SQL here, the service no HTTP or tokens. That way a reviewer sees at once where the access control, the validation and the query construction sit.

## Dependencies: session, user, scoping

Two dependencies recur in almost every protected endpoint:

- `get_postgres_session` gives a fresh database session per request and closes it afterwards.
- `get_current_user` checks the token and loads the user (chapter 5); `get_current_superuser` additionally requires the role `superuser` or `admin`, `get_current_admin_user` the role `admin` (chapter 2).

A router can also put a dependency on all its endpoints at once. That is how the routers for DGV, CNVs, chromosomes, blacklist and segmental duplications require a valid token for every endpoint, including a future one.

**Where in the code:** `backend/app/dependencies.py` and `backend/app/core/postgres.py`.

## Schemas: validation and documentation

All shapes of requests and responses are in `backend/app/schemas/`, one module per domain. They do three things:

- they **check input**: invalid JSON gives `422` before the router runs;
- they **fix the shape of the response** (`response_model=`), so that unintended fields drop out;
- they provide the **OpenAPI description** of the API.

Every route that answers with JSON names its model, never a bare `dict`. The frontend reads the response through the type generated from it (`frontend/src/lib/apiSchema.generated.ts`), and `test_api_response_models.py` fails on a route without a model. Only the BED read routes return the JSON their service builds. Their model describes it without validating the up to a million rows again; tests compare each track type with the model. Outside development `/docs`, `/redoc` and `/openapi.json` are disabled. Because all schemas are in one place, a reviewer can check there which data go into and out of the system.

## Security rules that apply everywhere

These rules sit in a few shared helpers, not separately in each function.

### 1. All queries are parameterised

Values never go into a query as text, always as a separate parameter.

- **Postgres:** SQLAlchemy with named parameters (`:naam`). For a list of UUIDs (e.g. `IN :project_ids`) a helper function makes a safe list of variable length, without pasting text.
- **ClickHouse:** parameters in the form `%(naam)s`, which CoGA passes to the client (clickhouse-connect) separately from the query text. The client puts each value, escaped, into the query that goes to the server (client-side binding). The query text that CoGA builds itself never contains the value.
- **A UUID from the request** (in the path, the query string or the body: a file, an import job, an HPO annotation, a panel, a project, the assembly of a NIPT artefact, …):
    - is read once, where the record is looked up, so after the access checks;
    - gets, if the value is not a UUID, the same response as a UUID without a record (the route's 404, or the 400 the route gives for an invalid ID), and then never reaches a query;
    - is bound in its canonical form (lower case, with hyphens), so that `{…}` or `urn:uuid:…` finds the same record.

    A write action that refers to a record looks that record up first. A NIPT artefact for a non-existent assembly or an import job for a non-existent project gets a 404 before anything is written, and the audit trail of the artefact list gets no event for it.

Only table names do appear in the text. They are derived from the assembly name through one function that allows only safe characters (chapter 3).

**Where in the code:** `backend/app/core/sql.py` (`uuid_list_bindparam`, `canonical_uuid`, `require_uuid`) and `execute_clickhouse` in `backend/app/core/clickhouse.py`.

### 2. `ORDER BY` comes from a fixed list

A column name cannot be a parameter in SQL. That is why CoGA never puts text from the user into an `ORDER BY`, but translates a sort key through a fixed table. In the Variant Explorer an unknown key falls back to the default (`total_samples`). In the integrity check of the hash chains the sort columns come from a fixed list per table.

**Where in the code:** `_SORT_EXPR` in `backend/app/services/variant_explorer_service.py` and `_CHAIN_ORDER_COLS` in `backend/app/services/integrity_anchor_service.py`.

### 3. `LIMIT` and `OFFSET` become integers

Page limits always go through `int(...)`, become at least 0 and are themselves parameters in the query. A high page number cannot provoke an unbounded scan: the page is clamped.

**Where in the code:** `backend/app/services/clickhouse_variant_queries.py`.

### 4. Extra protection around ClickHouse

- **Limits per query:** a maximum execution time, query size and memory limits, so that one broad filter does not occupy the server without limit. A query that is too heavy gives a `422` asking the user to refine the search, not an unclear `500`.
- **Transient errors:** on a broken connection the client recovers and tries once more, without changing the query.

**Where in the code:** `backend/app/core/clickhouse.py` (limits and recovery); the translation of a query that is too heavy into `422` in `backend/app/services/clickhouse_family_variants.py`.

## All routers

All routers are listed in `backend/app/routers/__init__.py` and hang under `/api`. Only `metrics.py` is deliberately mounted by `main.py` outside `/api` (see *Metrics for monitoring*). The router `families.py` includes six sub-routers under the same path `/families`; they therefore have no prefix of their own. Only `health.py`, `lookups.py` and `ancestry.py` have no prefix at all; `ancestry.py` writes `/families/…` in each path.

| Router | Path under `/api` | Purpose |
| --- | --- | --- |
| `health.py` | `/health`, `/health/ready`, `/version` | Availability and version; without login |
| `auth.py` | `/auth` | Login, token, registration, profile, user management (chapter 5) |
| `ped.py` | `/ped` | Upload a pedigree or enter it by hand |
| `families.py` | `/families` | Family, members, structure, HPO, phenotype matching, region of interest, who can be assigned a family or review it (`/families/{id}/assignable-users`) |
| `families_small_variants.py` | `/families` (part) | Small variants: pages, export, compound het, presets, tags, review and ACMG, and the statistics of the variant summary (chapter 8) |
| `families_structural_variants.py` | `/families` (part) | Structural variants of a family |
| `families_nipt.py` | `/families` (part) | Monogenic NIPT: summary with the quality checks, variants with the inheritance views, coverage (also per capture target), one pregnancy at a time (`analysis`) (chapter 8) |
| `families_cnipt.py` | `/families` (part) | Conventional NIPT: the automatic result of each maternal plasma, one pregnancy at a time (`analysis`) (chapter 8) |
| `families_reports.py` | `/families` (part) | Annotation manifest, drift, clinical audit, sample QC, report and sign-out (chapter 11) |
| `families_tracks.py` | `/families` (part) | Tracks: haplotypes, phased markers, repeats, mtDNA, Paraphase (chapter 9); for the variant summary, the SV lengths and the SV counts per member and per pair of members (chapter 8) |
| `family_qc_reports.py` | `/families` | The pipeline's QC report, through a short-lived link and isolated (chapter 2) |
| `ancestry.py` | `/families/{family_id}/ancestry` | The exploratory ancestry PCA of a family, from its stored callsets; not clinical, outside the sign-out |
| `report_codes.py` | `/report-codes` | Look up a signed analysis record with the report code that the EPD report cites (chapter 11) |
| `structural_variants.py` | `/structural-variants` | Upload the SV file of one sample (administrators only); the SVs of one sample are read through the family's SV list with the parameter `sample` |
| `cnvs.py` | `/cnvs` | The clinical CNV catalogue and CNVs per region |
| `variant_explorer.py` | `/variant-explorer` | Variants across all accessible projects, with the views per gene, per transcript and per cohort frequency (chapter 14) |
| `genes.py` | `/genes` | Gene Explorer and genes per region (chapter 13) |
| `hpo.py` | `/hpo` | Search HPO terms (chapter 12); importing the ontology goes through `POST /api/admin/hpo/sync` |
| `panels.py` | `/panels` | Gene panels, versions, PanelApp; writing only for a superuser or administrator |
| `bed.py` | `/bed` | Fetch and upload interval tracks |
| `chromosomes.py` | `/chromosomes` | Chromosome sizes and cytobands |
| `blacklist.py` · `segmental_duplications.py` · `dgv.py` | `/blacklist` · `/segmental-duplications` · `/dgv` | Reference tracks |
| `repeat_expansions.py` | `/repeat-expansions` | Upload the TRGT repeats of one sample (administrators only); the backend loads the repeat catalogue itself at start-up |
| `projects.py` | `/projects` | Projects, the unit of access; the ClinVar reclassifications after sign-out (chapter 11) |
| `species.py` · `assemblies.py` | `/species` · `/assemblies` | Species and assemblies, with their reference status |
| `cram.py` | `/cram` | CRAM/BAM for the genome browser, with access control |
| `signal_tracks.py` | `/signal-tracks` | The signal files of the CNV caller (bigWig, bedGraph) for IGV |
| `family_imports.py` | `/family-imports` | Package import: the deliveries (from an import watch folder or added by hand), their correction, approval or refusal, and the import jobs. Only for a superuser or administrator; overwriting an existing family only for an administrator (chapter 6) |
| `product.py` | `/product` | The release catalogue |
| `admin.py` | `/admin` | All administration functions, each behind `get_current_admin_user`; the variant tags, the overview of the preset filters and the limits for sequencing QC behind `get_current_superuser` (chapter 15) |
| `ui_events.py` | `/ui-events` | UI telemetry (chapter 15) |
| `lookups.py` | `/family-statuses`, `/users` | The status list for the drop-down list on the family page, and the list of active accounts (administrators only) |

## The service groups

`backend/app/services/` is large. The table groups the modules by function; the names are examples. A grouping of the modules is also given in `docs/application-scheme.md`, section *Main code areas*.

| Group | Examples | Purpose |
| --- | --- | --- |
| ClickHouse variant layer | `clickhouse_variant_storage.py`, `clickhouse_variant_queries.py`, `clickhouse_family_variants.py`, `clickhouse_interval_tracks.py` | Tables, query construction and running variant queries |
| Family and access | `family_metadata_context.py`, `metadata_service.py`, `access_control.py`, `family_structure_service.py` | Families, members and structure, with access scoping. (`data_scope.py` is, despite its name, about chromosome names, not about access.) |
| Import | `family_package_*.py`, `import_watcher.py`, `coga_package*.py`, `import_delivery_*.py`, `variant_upload_service.py`, `raw_import_files_pg.py`, `vcf_header_provenance.py` | Deliveries, package import and provenance (chapter 6) |
| Filters and prioritisation | `family_variant_filters.py`, `variant_prioritization.py`, `variant_ranking_cache.py`, `variant_explorer_service.py`, `variant_carrier_summary.py` | Filters, scoring, ranking, the ranking cache and the carrier summary of the Variant Explorer (chapters 3, 8, 12 and 14) |
| ACMG and review | `acmg_points.py`, `cnv_acmg_points.py`, `small_variant_review_*.py`, `structural_variant_review_pg.py`, `classification_drift_service.py` | Classification, tags, review state, drift (chapter 10) |
| Specialised analyses | `nipt_*.py`, `haplotype_lineage_service.py`, `phased_marker_service.py`, `mitochondrial_analysis.py`, `paraphase_pg.py`, `sample_integrity_*.py`, `family_variant_statistics.py` | NIPT, PGT, mtDNA, Paraphase, sample QC, the variant summary (chapter 8) |
| Phenotype | `hpo_service.py`, `monarch_*.py` | HPO and Monarch (chapter 12) |
| Reference data | `gene_info_*.py`, `reference_*_service.py`, `panel_metadata_service.py`, `panelapp_service.py` | Gene reference, reference tracks, panels (chapter 13) |
| Traceability | `audit_log_pg.py`, `clinical_audit_service.py`, `report_signout_service.py`, `hash_chain.py`, `integrity_anchor_service.py`, `event_pipeline.py` | Audit log, clinical audit, sign-out, hash chains, anchors (chapter 11) |
| Robustness | `upload_safety.py`, `bounded_download.py`, `auth_rate_limit_pg.py` | Bounded uploads and downloads, rate limiting |
| Mail | `outgoing_mail.py`, `account_notifications.py`, `account_activation_mail.py`, `family_assignment_mail.py` | The mail to the administrators about a pending account, to an activated account and to whoever is assigned a family (chapters 2 and 5) |

## Metrics for monitoring

`GET /metrics` gives the backend's operational figures in the Prometheus format:

- requests, errors and duration per route;
- the last scheduled integrity check of ClickHouse per assembly;
- the queue and the unsaved events of the audit pipelines;
- the active import jobs, and how long they have given no sign of life;
- the running build.

It holds no clinical data. A request gets the template of its route as its label (`/families/{family_id}`, as the audit log also stores it), never the path with its identifiers.

The endpoint is off as long as `METRICS_TOKEN` is not set. If it is set, it answers only a request that carries that token as a bearer token. It lies outside `/api`, and the load balancer and the frontend server forward only `/api` to the backend: the internet therefore never reaches it. In the deployment a collector (the Google-Built OpenTelemetry Collector) reads it every minute with the token. That collector runs as a second container in the same Cloud Run service and writes the figures to Google Cloud Managed Service for Prometheus. Alert rules in Cloud Monitoring read them there and mail the addresses from `alert_notification_emails` (`terraform/monitoring.tf`). `docs/monitoring.md` describes the metrics and proposed alert rules.

**Where in the code:** `backend/app/services/operational_metrics.py` and `backend/app/routers/metrics.py`; the counting of each request in the request-logging middleware.

## Request logging: every request leaves a trace

Two components make sure that every HTTP request is logged and audited.

**Structured logging.** The backend writes its own log lines as JSON lines on stderr. uvicorn's own lines (the start-up, the access log on stdout, the traceback of an unhandled error) keep uvicorn's text format. Control characters (including line breaks) in values are replaced before they reach a log line, so that nobody can insert false log lines (*log forging*).

**The request-logging middleware** records every request in the append-only table `audit_log_events`:

- **Who:** user, email address and role, from the user that `get_current_user` attached to the request.
- **What:** method, route, status, duration, IP and user agent. Of a query string, by default only the keys are kept (`AUDIT_LOG_QUERY_STRING_MODE`): this shows which filters a search used, without their values.
- **The content of a change:** for `POST`, `PUT`, `PATCH` and `DELETE` the body is kept, with sensitive fields (password, token, secret, …) masked, also in the form of `/auth/token`. A body that cannot be read is not kept raw. The middleware also derives which entity and which fields were changed (`db_update`): the fields are the keys of the body that the route's model reads. A key that the model ignores changes nothing and is not listed; the body does keep it.
- **Requests for records of other projects:** a family, sample or project outside the user's projects gets the same response as an ID that does not exist (`404`, chapter 2). Only the audit row keeps track of the difference: `request_meta.record_hidden` names the kind of record (`family`, `sample` or `project`). That way it stays visible who requested records of other projects; the Audit logs administration page shows it in the column *Flags* (chapter 15).
- **Separation of clinical data and application log:** the body (possibly clinical data) goes only into the protected audit database, never into the ordinary application log. That also holds for the text of an error: with a failed query, that text quotes the SQL and the parameters, with values from the request or values that were read for it.
    - The 500 line of an unhandled error names the kind of error (the type with SQLSTATE, or ClickHouse's error code), the route and the frames of each exception in the chain, without their messages.
    - uvicorn logs the same error once more, because Starlette passes it on to the server after the 500 response. A filter (`RedactServerErrorFilter`) writes that traceback in the same way.
    - Every other log line with an exception (`logger.exception()`, `exc_info=True`) is also written that way by the JSON formatter (`JsonLogFormatter`), from every logger (including a library's) and at every level: the kind of error in `error`, the frames of the chain in `traceback`, never the message.
    - The full text is only in the audit row (`audit_log_events.error`).

    The only exception is an audit row that the database cannot store. That row is logged with its content, body and error text included, so that it can be recovered (see below).

- **What Postgres cannot store:** a NUL character (a `%00` in the path or the query string, `\u0000` in a JSON body), half of a UTF-16 surrogate pair and the numbers `NaN` and `Infinity`. Such a value is kept as a visible escape: a NUL as the four characters `\x00`, a half pair as `\udXXX`, a number as the text `"NaN"`, `"Infinity"` or `"-Infinity"`. `request_meta._escaped` names the columns in which that happened (`core/pg_storable.py`). The UI event log does the same, with `_escaped` in `detail`. The Audit logs administration page shows both in the column *Flags*, with the columns listed (chapter 15).

The audit lines go through a queue that in production never silently drops anything:

- With a full queue the backend writes synchronously.
- Without a queue (`AUDIT_LOG_MODE=sync`, or as long as no worker is running, such as during start-up and shutdown) each request writes its own line.
- The worker writes a batch in one INSERT. If that also fails at the last attempt, it writes the lines one by one, each once and without waiting. A line that the database refuses on its own account (a constraint, or a value the driver cannot encode, such as a timestamp before the year 1 in UTC) thus costs only itself, not the whole batch (up to `AUDIT_LOG_BATCH_SIZE` lines from any user).
- A line of a user whose account was deleted while the line was waiting (only possible with SQL by an administrator) is stored on its own at write time, without the link to `users`: the user's ID is in `request_meta._deleted_user_id` (for a UI event in `detail._deleted_user_id`), and `user_email` and `user_role` stay as they were. The log reports it with the kind of error, without values.
- Each event gets its own ID (`event_id`) as soon as it is made, before it goes into the queue. The INSERT does nothing for an ID that the table already has (`ON CONFLICT (event_id) DO NOTHING`). If the response to a successful commit was lost, the worker writes the batch again, and each line is still there once.
- If the database cannot be reached, the one-by-one writing stops after the first line: the remaining lines are logged and counted without a new attempt.
- A line that truly cannot be stored is logged with its content and counted, in every mode. The alert on `coga_audit_events_not_persisted_total` fires, and the line can be recovered from the log. Of the error when writing, only the kind is logged (type and SQLSTATE), not its text, which quotes the query and the row. The user's request does not fail because of it, and a batch of UI events still gets 202.
- Only in development may a full queue drop lines (`AUDIT_LOG_DROP_ALLOWED`) or may the audit log be switched off entirely (`AUDIT_LOG_MODE=off`). Outside development the backend refuses to start with those values. The default is `async`.

**Where in the code:** `backend/app/core/coga_logging.py` (JSON logging, naming errors without their text), `backend/app/middleware/request_logging.py` (the middleware), `backend/app/services/audit_log_pg.py` and `event_pipeline.py` (writing without loss), `backend/app/core/pg_storable.py` (the escapes).

### The order of the middleware

In Starlette (on which FastAPI runs) the last registered middleware is the outermost. From inside (close to the route) to outside (close to the client):

1. `CORSMiddleware` — only allowed origins may call the API with credentials.
2. `log_request_response` — the request logging above.
3. `normalize_api_collection_root_paths` — accepts collection paths both with and without a trailing slash.
4. `security_headers_middleware` — sets the security headers on every response (chapter 2).
5. `TrustedProxyClientMiddleware` — registered last, so the outermost: determines the real client IP (`TRUSTED_PROXY_HOPS`) before logging, rate limiting and audit read it.

Outside that chain there is also Starlette's `ServerErrorMiddleware`. It answers an error that nothing else handles with a `500`, through the handler `_answer_server_error` from `main.py`: the same text as Starlette's own response, but with the same security headers, HSTS included. It then raises the error again, so that uvicorn still logs it; by then the request logging has already logged the request and put it in the audit log.

**Where in the code:** the end of `backend/app/main.py`; `backend/app/middleware/`.

## Key files

| File | Role |
| --- | --- |
| `backend/app/main.py` | Mounts the routers under `/api` and sets up the middleware chain |
| `backend/app/routers/__init__.py` | The list of all routers |
| `backend/app/dependencies.py` | `get_current_user`, `get_current_superuser` and `get_current_admin_user` |
| `backend/app/schemas/` | All request and response models |
| `backend/app/core/sql.py` · `backend/app/core/clickhouse.py` | Parameterised queries in Postgres and ClickHouse, limits per query |
| `backend/app/services/clickhouse_variant_queries.py` | The construction of the variant queries, with integer `LIMIT`/`OFFSET` |
| `backend/app/services/family_metadata_context.py` | The access checkpoint for family data |
| `backend/app/middleware/request_logging.py` | Logs and audits every request, with masking |
| `backend/app/core/coga_logging.py` | JSON logging and protection against false log lines |
