# CoGA — Improvement Workplan (Performance · Traceability · Security · Regulatory)

_Generated 2026-06-26 from a forward-looking whole-codebase review. This is the
companion to the 2026-06-14 dead-code/bug/perf cleanup review (archived in git
history; most of its items are now **FIXED**) and [`docs/ROADMAP.md`](ROADMAP.md)
(product direction). Where this plan and those documents overlap, this one supersedes._

> **Status: DRAFT for internal review.** This is an engineering + regulatory action
> plan, not an approved QMS artefact. Owners, dates and effort estimates are proposals
> to be reconciled with the CMGG QMS (SOP **H11.1-OP5**) and the technical file in
> [`docs/regulatory/`](regulatory/README.md).

---

## 0. Status update (as of 2026-06-30)

_Progress since the 2026-06-26 draft. The phase tables in §4 record the original plan;
this section is the current truth. ✅ shipped · ◐ partial · ☐ open._

**Phase 0 — ✅ complete** (P0-1…P0-6, implemented 2026-06-26).

**Phase 1 — mostly shipped.**

- ✅ **P1-1** version + git-SHA identity: build-injected (`core/config.py`), exposed in `/health`, shown in the report footer, and **frozen into the sign-out snapshot** (`report_signout_service.py`).
- ✅ **P1-2** Sample-QC sign-out gate — _the top clinical-safety item_: a hard `fail` (sample/pedigree swap, TF-06 H4 S5) now **blocks sign-out** with acknowledge-with-reason (409), and QC is frozen into the snapshot + content hash.
- ✅ **P1-3** runtime app-role privilege separation (migration `040`, `test_app_role_privileges.py`, `db-runtime-role-runbook.md`).
- ✅ **P1-4** hash-chained audit + sign-out tables with a verifier and integrity anchors (migrations `038`/`039`/`041`, `hash_chain.py`, `integrity_anchor_service.py`).
- ✅ **P1-5** integration tests that exercise the privilege/trigger enforcement and the anchors.
- ✅ **P1-6** CI security scanning that fails the build (gitleaks + CodeQL v4).
- ✅ **P1-7** security-headers middleware + `/docs` disabled in prod _(confirm non-root container `USER`)_.
- ✅ **P1-9** bounded upload read/inflation + family-package manifest-path confinement.
- ✅ **P1-10** durable audit / UI-event pipeline (closes TF-13 S-5).
- ✅ **P1-14** per-modality annotation / SOUP provenance capture from VCF/VEP headers (see `annotation-provenance.md`).
- ✅ _Adjacent:_ TLS for Postgres + ClickHouse connections (Security S-2).
- ◐ **P1-8** session token shortened to 2h; full `token_version` revocation + `AZURE_ADMIN_OVERRIDE` lockdown still open (documented as accepted P3 residual).
- ◐ **P1-16** branch protection exists; `enforce_admins` + independent review / CODEOWNERS still to confirm.
- ☐ **P1-11** `/metrics`, **P1-12** versioned migration ledger, **P1-13** backups + tested restore, **P1-15** release tags + CHANGELOG + signed release records (still **0 tags / no CHANGELOG**).

**Phase 2 — perf/observability largely shipped.**

- ✅ **P2-1** track-availability aggregation · **P2-3** gene expression indexes · **P2-4** bounded count + keyset pagination · **P2-6** scheduled ClickHouse integrity monitor · **P2-7** backend + frontend coverage gates · **P2-9** external-call timeouts + backoff · **P2-10** perf long-tail (S3 offload, `keepPreviousData`, batched inserts, memoization).
- ◐ **P2-5** fail-clean import rollback landed; startup stuck-job reaper + terminal incomplete-import state still open.
- ☐ **P2-2** multi-worker uvicorn + import off the event loop · **P2-8** GIAB/GeT-RM truth set + concordance harness.

**Phase 3 — mostly open.**

- ✅ **P3-6** e2e harness shipped (API-contract, import, sign-out/hash-chain, and Playwright browser journeys; TF-09c/09d).
- ☐ **P3-1** performance evaluation (the long pole) · **P3-2/3/4/5/7** unchanged.

---

## 1. Scope, method & framing

CoGA is an in-house IVD under **IVDR Article 5(5)** at CMGG (ISO 15189 accredited). The
device boundary is *annotated VCF → signed clinical report*. This plan therefore weighs
every finding not only on engineering merit but on its **GSPR / ISO 14971 / IEC 62304 /
ISO 27001 / GDPR** consequences, so the work directly feeds the technical file.

**Method.** The full source tree (~46.6k LOC Python / ~42k LOC TS-React, 688 tracked
files) plus the regulatory dossier were reviewed by a fan-out of six specialised agents —
**security, traceability & clinical safety, performance & scalability, IVDR dossier
completeness, architecture & observability, and testing/CI/release-control**. Each was
given the existing review and TF documents up front so it would surface **gaps, not
already-solved work**. Findings cited below were verified against the actual code
(`file:line` references throughout); a non-trivial number of "open" items in the TF docs
turned out to be **already done** (see §9), and a few "done" claims turned out **not to be
implemented** (see the version-traceability theme).

**Legend.** Effort **S** (≤1 day / localized) · **M** (days / one subsystem) ·
**L** (weeks / cross-cutting). Priority **P0** (urgent: correctness or active data loss) ·
**P1** (next: safety, traceability, security, operability) · **P2** (scaling &
maintainability) · **P3** (longer-horizon / external-dependent).

---

## 2. Executive summary

The platform is **well above typical in-house-IVD maturity**: project-scoped RBAC is
real and consistent, ClickHouse/Postgres queries are fully parameterized (no injection
found), the audit/sign-out tables have append-only DB triggers, dependencies are
hash-locked, an SBOM is generated in CI, branch protection exists, and the clinical-logic
unit tests (ACMG, prioritization, de-novo, Sample QC, drift) assert real thresholds and
edge cases. The 2026-06-14 performance cleanup genuinely landed. **Don't re-litigate
those** (§9).

The gaps cluster into **five themes**, and the most important one is unanimous across
five of the six reviews:

1. **The signed report is not bound to the software that produced it.** There is **no
   software version, no git SHA, and no build identifier anywhere** in the codebase
   (verified: 0 git tags; no `__version__`/`APP_VERSION`/`GIT_SHA` in `backend/app` or
   `frontend/src`; report footer shows only reference-data module versions). Four TF
   documents *assert this as already solved* (TF-09 §6 release checklist, TF-16/TF-18
   per-sample→version linkage, TF-02 §8). This single gap breaks IVDR Art 5(5)(h)
   traceability, the PMS/vigilance backbone, and reproducibility — and it is **not a deep
   change**. This is the keystone item.

2. **"Immutable / tamper-evident" is asserted but not enforced or tested.** The
   append-only triggers are real, but the **application connects as the DB owner/superuser**,
   which can `DISABLE TRIGGER` and mutate freely; there is **no hash-chaining** (record
   deletion/reordering is undetectable); and **no test fires the triggers**. The control
   exists on paper but not against a realistic threat.

3. **A clinical-safety gate is advisory only.** A failing **Sample QC** (sample swap,
   sex mismatch, excess Mendelian errors — TF-06 rates sample swap **S5, catastrophic**)
   does **not** block sign-out and is **not frozen** into the signed record. The drift
   gate proves the pattern exists; it just wasn't applied to the higher-severity hazard.

4. **Operational-phase blindness.** No metrics/tracing, a **best-effort audit queue that
   silently drops on full and loses on crash**, **no backups/DR** (after a real ClickHouse
   corruption event on 2026-06-11), a **"re-run every SQL on every boot" schema model**
   (which already leaked one *destructive* statement — see P0-1), and corruption/stuck-job
   detection that only runs when an admin clicks. The system can be broken in ways an
   operator cannot see.

5. **Change-control & V&V evidence is thin.** No release tags / CHANGELOG / release
   records, branch protection not enforced (and a single-committer history with 0
   independent reviews), no coverage measurement, no controlled GIAB truth set for
   regression, and — the largest *substantive* regulatory gap — the **performance
   evaluation (TF-11) has not been executed** (needs clinical leads to set comparators,
   thresholds and N first).

**Bottom line:** no finding is an architectural dead-end, and the engineering substrate is
closer to CE-IVDR alignment than the documents claim in places. The work is to (a) bind
identity & provenance into the clinical record, (b) make the integrity controls actually
enforced and tested, (c) gate sign-out on the catastrophic-severity hazard, (d) gain
operational visibility and recoverability, and (e) operationalize change control and run
the performance studies.

---

## 3. Cross-cutting themes (fix once, satisfy many)

Several findings recur across workstreams. Treat these as shared foundations so they
aren't solved five times in five places:

| Theme | Surfaced by | Foundational fix |
| --- | --- | --- |
| **T1 · Software version & build identity** | Security, Traceability (H3), IVDR (F-1/F-2), Architecture (F9), Testing (F3) | Inject git SHA + semver at build → expose in `/health` + app chrome + **report footer**, freeze into the sign-out snapshot, and link to each analysed sample. Unblocks ~6 downstream items. |
| **T2 · Provenance capture (SOUP / reference-DB versions + input hashes)** | Traceability (H2/H3/H4), IVDR (F-3) | Capture VEP/ClinVar/gnomAD/dbNSFP/… versions (VCF-header parse + pipeline manifest) and the input-VCF sha256, and **freeze them into the sign-out snapshot**. Today only assembly/Monarch/HPO are captured. |
| **T3 · Enforced + verified immutability** | Traceability (C1/C2), Architecture (F2/F4), Testing (F1), IVDR (RTM REQ-TRACE-008) | DB privilege separation (non-owner app role) + hash-chaining + a verifier + integration tests that fire the triggers. |
| **T4 · Determinism guarantees** | Traceability (H1/H5/M1) | Add explicit, total tiebreakers everywhere ranking/hashing depends on order (phenotype-term cap, drift-list order, ranking sort). Same input → same output, provably. |
| **T5 · Observability & recoverability substrate** | Architecture (F1/F5/F6/F8), Security (#11), Testing (F10) | `/metrics`, durable audit, backups+tested restore, scheduled integrity checks, job reaper, alerting hookpoints. Prerequisite for IVDR PMS trending. |
| **T6 · Change-control evidence** | IVDR (F-5/F-6), Testing (F3/F4), Security (#1) | Tags + CHANGELOG + release records + enforced branch protection + independent review + coverage gate + CI security scanning + GIAB regression harness. |

---

## 4. Phased roadmap

### Phase 0 — Urgent correctness & determinism (this week · all S)

These are active-harm or near-zero-cost items; do them first.

| ID | Item | Area | Effort |
| --- | --- | --- | --- |
| **P0-1** | ✅ **Resolved / obsolete.** The destructive boot-time `UPDATE` that reset every `scope='project'` tag to global on **every restart** no longer exists — the one-shot backfill is gone and the project-scoped tag tables are now created in their final form in the consolidated `03_assay.sql` baseline. The `init_postgres_schema` loader still re-runs all SQL each boot (no ledger), but the idempotent `CREATE TABLE` baselines have no such side effect. | Architecture | S |
| **P0-2** | **Deterministic phenotype-term cap** — `monarch_phenotype_score.py:247` slices a `set` after `sorted(key=ic)`; IC ties break on `PYTHONHASHSEED`, changing ranking across workers. Add `(-ic, term)` tiebreaker. | Traceability | S |
| **P0-3** | **Stable sign-out content hash** — order the drift list by `variant_id` before hashing (`classification_drift_service.py:90` → snapshot `report_signout_service.py:96`); list order isn't canonicalized, so identical content can hash differently. | Traceability | S |
| **P0-4** | **Explicit ranking tiebreaker** — add `str(v.id)` as final sort key in `clickhouse_family_variants.py:4732` (small) & `:5547` (SV); determinism is currently incidental and load-bearing for the 5000-row truncation. | Traceability | S |
| **P0-5** | **Stop logging PHI to stdout** — drop `request_body` from the application log path (`request_logging.py:254`); keep it only in the controlled audit DB. | Security | S |
| **P0-6** | **Bound the compound-het partner scan** in `get_family_compound_het_candidates` (`clickhouse_family_variants.py`). Cap the gene-scoped scan defensively at `_SMALL_INHERITANCE_MAX_CANDIDATE_ROWS + 1`. _Adversarial-review correction:_ the `gene_id`-only whole-family **fallback is left UNBOUNDED on purpose** — a blind position-ordered row cap there could silently drop a genuine partner (a missed-partner clinical result). Safe bounding of the fallback needs `gene_id`-scoped fetching → deferred to Phase 2. | Performance | S |

> **Phase 0 status — IMPLEMENTED (2026-06-26, branch `docs/improvement-workplan`).** All six
> fixes applied with regression tests (each verified to fail pre-fix); full backend suite
> **670 passed / 2 skipped**, catalogue gate green. Each fix was designed and then adversarially
> re-reviewed by independent agents (5 sound, 1 concern → P0-6 resolved as above). No production
> behaviour changed beyond the stated bugs.

### Phase 1 — Traceability, safety, security & operability (next 4–6 weeks)

The core of the plan. Grouped by workstream; details in §5–§8.

| ID | Item | Area | Effort |
| --- | --- | --- | --- |
| **P1-1** | **T1** — Software version + git SHA: build injection → `/health` + footer + **frozen into sign-out snapshot** + per-sample linkage. | Traceability/IVDR | M |
| **P1-2** | **C3** — Sample-QC sign-out gate (block on `fail`, acknowledge-with-reason) + freeze QC status/metrics into the snapshot & content hash. | Traceability | M |
| **P1-3** | **T3** — DB privilege separation: app connects as non-owner role with no `UPDATE/DELETE/TRUNCATE/ALTER` on the three append-only tables. | Traceability/Arch | M |
| **P1-4** | **T3** — Hash-chain audit + sign-out tables (`row_hash=H(prev‖row)`) + a walk-and-verify endpoint; re-verify stored hash on read. | Traceability | M |
| **P1-5** | **T3** — Integration tests that fire the triggers (UPDATE/DELETE rejected; `user_id→NULL` carve-out allowed; signed row frozen). Close RTM REQ-TRACE-008. | Testing | S–M |
| **P1-6** | **#1** — CI security scanning that **fails the build**: dependency-audit (consume the SBOM you already build), secret-scan (gitleaks), SAST (CodeQL/semgrep/bandit). | Security | S–M |
| **P1-7** | **#3/#10** — Security response headers (HSTS, CSP/`frame-ancestors`, `nosniff`, Referrer-Policy); non-root container `USER`; disable `/docs` in prod. | Security | S |
| **P1-8** | **#4/#5** — Lock down `AZURE_ADMIN_OVERRIDE` parallel local-JWT path; add token revocation (`token_version`) + logout so deactivation/role-change isn't valid for 6h. | Security | M |
| **P1-9** | **#6/#7** — Enforce upload size limits + stream large files + bound gzip inflation; confine `_resolve_package_path` (`family_package_import.py:701`) with `.resolve()`/`is_relative_to`. | Security | M |
| **P1-10** | **F2** — Durable audit log: run the audit writer in `sync` (or bounded-block) mode + a dropped-event counter. Default `async` silently drops on full / loses on crash. | Architecture | S–M |
| **P1-11** | **F1** — Observability substrate: `/metrics` (request latency/status, queue depth, job success/failure, ClickHouse error counter) + defined alerting hookpoints. | Architecture | M |
| **P1-12** | **F4** — Versioned migration ledger (`applied_migrations` w/ checksum, run-once) replacing "re-run all SQL every boot"; separate idempotent DDL from one-shot data migrations. | Architecture | M |
| **P1-13** | **F5** — Backups + **tested restore** for Postgres + ClickHouse to off-host storage; close the open TF deployment item. | Architecture | M–L |
| **P1-14** | **T2** — Reference-DB / SOUP version capture (VCF-header parse + pipeline manifest) + input-VCF hash, frozen into sign-out; or precisely re-scope the TF-08 claim. | Traceability/IVDR | M–L |
| **P1-15** | **F3/F4 (release)** — Git tags + `CHANGELOG.md` + first signed release record → assign `Sxxxx` + bio-IT ingangsvalidatie (H11.1-F12.2). | IVDR/Testing | M |
| **P1-16** | **F4 (testing)** — ✅ required checks on `main` are enforced (ten, strict). **Still open:** enable `enforce_admins` (an admin can currently bypass), and set up genuine independent review — no `required_pull_request_reviews` and no CODEOWNERS for `docs/regulatory/` + clinical modules. | Testing/IVDR | S |

### Phase 2 — Scaling, resilience & maintainability (1–2 months)

| ID | Item | Area | Effort |
| --- | --- | --- | --- |
| **P2-1** | **N1** — Track-availability: replace full family-SV materialization with an aggregated per-sample presence probe (runs on every workspace load). | Performance | M |
| **P2-2** | **N2/N3** — Run multiple uvicorn workers + move the import job off the HTTP event loop (own process or `to_thread`); a whole-genome import currently stalls the single loop and can flap `/health`. | Performance | S + L |
| **P2-3** | **N5** — Expression indexes on `genes`/`gene_info` (`lower()`/`upper(...) text_pattern_ops`); panel-filter + autocomplete currently seq-scan a 120k+-row table. | Performance | S |
| **P2-4** | **N4** — Global explorer: bounded count + estimate flag + keyset (seek) pagination instead of full-cohort `uniqExact` + deep `OFFSET` every page. | Performance | M |
| **P2-5** | **F6/F7** — Startup stuck-job reaper + a terminal "incomplete import" state with cleanup, and gate report generation on a complete-import flag. | Architecture | S–M / M–L |
| **P2-6** | **F8** — Schedule the existing ClickHouse integrity check (+ at startup), emit result as metric, alert on `corrupt`. | Architecture | S–M |
| **P2-7** | **F2 (coverage)** — Coverage measurement (pytest-cov + vitest v8), baseline as artifact, then a per-module floor on clinical-critical code. | Testing | S→M |
| **P2-8** | **F5 (testing)** — Controlled GIAB/GeT-RM truth slice + reproducible concordance/diff harness; the input the mandated minor-release F13 validation needs. | Testing | M–L |
| **P2-9** | **F10** — External-call resilience: bounded timeouts + capped retry/backoff on gene-reference HTTP; explicit boto3 `Config(connect/read timeout, retries)`. | Architecture | M |
| **P2-10** | **Perf long-tail (S)** — cram GET/HEAD S3 offload (N6), Explorer `keepPreviousData` (N7), batched Postgres inserts (N8), upload-hash offload (N9), `StructuralVariantTable` memo (N10). | Performance | S each |

### Phase 3 — Maintainability & dossier completion (quarter / external-dependent)

| ID | Item | Area | Effort |
| --- | --- | --- | --- |
| **P3-1** | **F-4 (IVDR)** — Execute the performance evaluation (TF-10→TF-11): clinical leads set comparators/thresholds/N, then run the 5 studies. Largest substantive regulatory gap; gates GSPR §9.1 & the Declaration. | IVDR/Clinical | L |
| **P3-2** | **F12** — Unify the dual SQL/Python inheritance & frequency-filter logic into one declarative spec emitting both, + property tests asserting agreement (NULL-AF semantics already diverge). | Architecture | M–L |
| **P3-3** | **F13** — Maintainability: add mypy to CI (start with `core/` + storage), extract a ClickHouse query-builder + thin Postgres repositories, split the 7k/5.8k-LOC god-modules and the 2.7k-LOC `schemas.py`. | Architecture | L |
| **P3-4** | **F-9/F-8/F-7 (IVDR)** — Stand up the controlled Risk Management File (per-hazard trace), run usability summative (IEC 62366-1), resolve placeholders + obtain DPO / owner / DoC sign-offs. | IVDR/External | M (each) |
| **P3-5** | **H4/M4** — Expand the per-classification evidence snapshot to freeze the actual values (AF, in-silico scores, ClinVar, constraint, zygosity) and the full auto-suggested+rejected criteria set. | Traceability | M |
| **P3-6** | **F7/F9 (e2e)** — A thin Playwright smoke for the critical journeys (login → classify → sign out → frozen report); global exception handler + normalized error envelope; frontend crash reporting. | Testing/Arch | M |
| **P3-7** | **Doc reconciliation (F-11/F-12)** — Update stale "open" TF actions that are actually done (dep pinning, SBOM, branch protection), reconcile the requirement count (71/73/74), resolve the F2/F14 template-code question. | IVDR | S |

---

## 5. Workstream A — Traceability & clinical safety (IVDR GSPR / ISO 14971)

The four traceability phases (manifest, evidence snapshot, drift, clinical audit, sign-out)
**are shipped and broadly work**. These are the remaining gaps.

- **A1 · Bind the report to its software & inputs (T1/T2).** [P0-2..4, P1-1, P1-14]
  No software version/git SHA exists; reference-DB versions are captured for only
  **3 of ~11** sources (assembly, Monarch, HPO); the input-VCF sha256 **is** computed
  (`raw_import_files_pg.py`) but **not** included in the immutable sign-out. A signed report
  therefore cannot be reproduced from, or bound to, the code + data that produced it.
  *Standard:* IEC 62304 §16.1 reproducibility; IVDR Annex I §16; ISO 15189.
- **A2 · Gate sign-out on Sample QC (C3).** [P1-2] The only sign-out gate today is drift
  (`report_signout_service.py:138`). Sample QC is **read-only display**, computed live,
  **never persisted**, and `build_report_snapshot` omits it — so a family with QC
  `overall_status == "fail"` signs out with zero friction. TF-06 H4 rates sample/pedigree
  swap **S5 catastrophic**. *Fix:* mirror the drift gate; block + acknowledge-with-reason +
  freeze QC into the snapshot; add a test. **Single most material clinical-safety gap.**
- **A3 · Make immutability real & evident (T3).** [P1-3..5] A `BEFORE` trigger does not
  constrain the table owner; the app role *is* the owner/superuser, so the "immutable"
  claim collapses against the running credential. No hash-chain means deletion/reordering
  is invisible, and the stored `content_hash` is never re-verified on read. *Fix:* privilege
  separation + hash-chain + verifier + tests (which also closes the untested RTM
  REQ-TRACE-008). *Standard:* ISO 14971 H9; IVDR Annex I §16 data integrity.
- **A4 · Determinism guarantees (T4).** [P0-2..4] Three real run-to-run variability sources
  (phenotype-term cap, drift-list order, ranking sort). All one-line, all feed the cache and
  potentially the report.
- **A5 · Freeze the evidence values (H4) & full override provenance (M4).** [P3-5] The
  evidence snapshot stores only an annotation-set hash + one ClinVar string — not gnomAD AF,
  REVEL/SpliceAI/CADD, constraint, zygosity. Auto-suggested-then-**rejected** criteria and
  strength-only overrides aren't recorded. The signed record isn't self-contained for an
  ACMG defensibility audit.
- **A6 · ACMG scorer parity (M3).** Frontend (`score.ts`) and backend (`acmg_points.py`)
  duplicate thresholds with **no cross-implementation parity test**; server recompute is
  authoritative (good) but the *displayed* class the analyst signs off could silently
  diverge. *Fix:* a shared golden fixture consumed by pytest + vitest. [P2-7 adjacent]

## 6. Workstream B — Security & cybersecurity (TF-13 / ISO 27001 / GDPR)

Application-layer posture is **strong** (no injection, consistent RBAC, presigned-URL
access checks, hash-locked deps, refuse-to-start on default secrets, real login throttling).
Gaps:

- **B1 · No CI security scanning (HIGH).** [P1-6] The SBOM is *generated* but nothing
  scans it; no SAST, dependency-audit, or secret-scanning gates the build. *Fix:*
  osv-scanner/pip-audit/npm-audit on lockfiles, gitleaks, CodeQL/semgrep — all failing on
  high-severity. *Standard:* IEC 81001-5-1 continuous vuln monitoring; ISO 27001 A.8.8.
- **B2 · PHI to stdout (HIGH).** [P0-5] `request_body` (up to 25 KB of clinical payload) is
  emitted to container stdout on every write, landing special-category data (GDPR Art 9) in
  a second, less-controlled store. Redaction only masks `password/secret/token/...` keys.
- **B3 · No security headers (MED).** [P1-7] Zero HSTS/CSP/`X-Frame-Options`/`nosniff`/
  Referrer-Policy anywhere. *Fix:* a small response-header middleware (or codify the proxy
  config and reference it from TF-13 §7).
- **B4 · Auth hardening (MED).** [P1-8] `AZURE_ADMIN_OVERRIDE` gives a `SECRET_KEY`-signed
  admin bypass of all Azure controls; 6-hour HS256 tokens have no `jti`/revocation/logout —
  a stolen token or role-downgrade stays valid. Plus: login timing oracle + case-sensitive
  email vs lowercased rate-limit key + unproxied client IP (LOW–MED, finding #8).
- **B5 · Upload DoS & path traversal (MED).** [P1-9] Uploads `await file.read()` the whole
  body (then re-read for hashing); no size limit; `gzip.decompress()` of SV files is
  inflation-unbounded. `_resolve_package_path` doesn't re-confine absolute/relative manifest
  paths (admin-gated). *Fix:* size caps + streaming + bounded inflation + path confinement.
- **B6 · Hardening & retention (LOW).** [P1-7, P1-13] Container runs as **root**; OpenAPI
  `/docs` served in prod; no password-strength policy and dev-env skips the weak-secret guard;
  audit-log retention undefined (append-only + no retention → unbounded growth, and an open
  GDPR storage-limitation item with the DPO). Deployment items S-1..S-8 (TLS, encryption at
  rest, secrets manager, network isolation) remain open — track in the IaC workstream.

## 7. Workstream C — Performance & scalability

The 2026-06-14 perf items are genuinely fixed (verified — see §9). Remaining:

- **C1 · Track-availability full-SV materialization (highest new impact).** [P2-1, N1]
  `get_family_track_availability_for_user` fetches the **entire** family SV set (+ JSON
  decode) for a per-sample boolean, on **every** chromosome/genome-workspace load. → use
  the existing `count_*_by_sample` aggregate.
- **C2 · Single-worker event loop + import on the loop.** [P2-2, N2/N3] `Dockerfile` runs
  one uvicorn worker that serves all HTTP **plus** the gene-refresh and import workers; heavy
  synchronous VCF/BED parsing runs on that loop. Any block stalls everything and caps
  throughput at one core. → `--workers N` (job leasing is already process-safe) + offload the
  import job.
- **C3 · Missing `genes`/`gene_info` expression indexes.** [P2-3, N5] `upper()/lower()`-
  wrapped predicates can't use the raw-symbol index → seq scans on every autocomplete
  keystroke and every panel-filtered variant query.
- **C4 · Global explorer count + deep pagination.** [P2-4, N4] Unbounded `uniqExact` over the
  full cohort every page + `OFFSET`-based deep paging. → bounded count + keyset.
- **C5 · Still-open from prior review.** Compound-het fallback unbounded [P0-6, O1];
  shared-SV-count matrix materializes all family SVs [O2, M/push-down to ClickHouse].
- **C6 · Long-tail (S).** [P2-10] cram GET/HEAD S3 offload (N6), Explorer `keepPreviousData`
  (N7), per-sample/pair Postgres insert loops (N8), upload-hash offload (N9),
  `StructuralVariantTable` render-body maps → `useMemo` (N10).

## 8. Workstream D — Architecture, resilience & observability + E — Testing/CI/release

**Resilience / operability (D):**
- **D1 · Migration safety.** [P0-1, P1-12] The boot-time destructive `UPDATE` (P0-1) is a
  symptom of the "re-run all SQL every boot, no ledger" model — adopt a versioned ledger.
  No forward-migration test on a *populated* DB (smoke only tests an empty one — F8).
- **D2 · Durable audit + observability.** [P1-10, P1-11] Best-effort audit queue (silent
  drop/loss); no metrics/tracing/error-reporting — an operator can't see a degraded
  classifier, stuck import, rising 5xx, or ClickHouse corruption. Add `/metrics`, durable
  audit, app-version in `/health` + logs, a global exception handler/normalized error
  envelope, and frontend crash reporting (F9/F11).
- **D3 · Recoverability.** [P1-13, P2-5, P2-6] No backups/DR after a real corruption event;
  stuck-job recovery is lazy-only (no startup reaper); family import is non-transactional and
  not cleanly resumable (orphaned partial data, `conflict_mode='cancel'` dead-ends on resume);
  corruption detection is manual-only. Add backups+tested restore, a reaper, an
  incomplete-import terminal state gating report generation, and a scheduled integrity check.
- **D4 · External-dependency resilience.** [P2-9] Gene-reference HTTP has no retry/backoff/
  circuit-breaker; boto3 uses default timeouts. A flaky source stalls a 20k-symbol refresh
  for hours; a slow S3 hangs unboundedly.
- **D5 · Maintainability (longer-horizon).** [P3-2, P3-3] God-modules
  (`family_package_import.py` 7k LOC, `clickhouse_family_variants.py` 5.8k), no repository
  layer, no mypy, framework coupling inside services, a 2.7k-LOC `schemas.py`, and **dual
  SQL/Python inheritance+frequency logic that already diverges** (a correctness hazard, F12).

**Testing / CI / release control (E):**
- **E1 · Verify the controls.** [P1-5] Immutability triggers, the linchpin data-integrity
  control, have **no test that fires them**.
- **E2 · Release identity & change control.** [P1-15, P1-16] 0 tags, no CHANGELOG, version
  frozen at `1.0.0`, branch protection unenforced with a 0-independent-review single-committer
  history. Make releases real and the gates binding. *Standard:* Art 5(5)(h); H11.1-OP5.
- **E3 · Coverage & regression.** [P2-7, P2-8] No coverage measurement/gate; no controlled
  GIAB truth set or concordance harness (the input the mandated minor-release F13 validation
  needs); no perf-regression guard; no end-to-end UI test (F7); requirement→test traceability
  is doc-only, rarely carried as `REQ-*` markers in the tests (F9).
- **E4 · Clinical-path test gaps.** Compound-het **trans/cis phasing** and parental-origin
  attribution are not asserted (F6) — reporting a cis pair as biallelic is a false-positive
  hazard (TF-06 H2). Two untested clinical surfaces: Global Variant Explorer and Clinical CNV
  Explorer (F11).

## 8a. Workstream F — Regulatory dossier completeness (IVDR Art 5(5) / Annex XIII)

The dossier is structurally strong and unusually candid. To make it **release-ready and
evidence-backed**:
- **F-blockers (clinical release):** execute the **performance evaluation** (TF-11 is an
  empty template; all acceptance criteria unset) [P3-1]; implement the **version identity**
  the docs already claim [P1-1]; obtain **DPO / owner / DoC sign-offs** and resolve the
  pervasive `‹…›` / 🔲 placeholders [P3-4].
- **F-high:** make **reference-DB version capture real or re-scope TF-08** [P1-14]; stand up
  the controlled **Risk Management File** with per-hazard trace and implement/justify the H7
  (aneuploidy threshold) + H12 (off-scope guard) controls [P3-4]; establish **change-control
  evidence** (tags, CHANGELOG, first release record, independent review) [P1-15/16]; run the
  **usability summative** [P3-4]; close the deployment security items (TLS/encryption-at-rest)
  [Security B6].
- **F-consistency (S):** reconcile the requirement count stated three ways (71 / 73-actual /
  74); resolve the **F2-vs-F14** clinical-opvolgvalidatie template-code question; and **update
  stale "open" actions that are actually done** (dependency pinning, SBOM, branch protection)
  so the docs don't understate real readiness [P3-7].

---

## 9. What's already solid — do not re-litigate

So the workplan doesn't waste effort re-investigating verified-good ground:

- **Performance (2026-06-14 cleanup):** ClickHouse caps, batched inserts, memoized DDL,
  blocking-I/O offloads in cram/reference/object_storage, frontend memoization &
  `keepPreviousData`, the variant-ranking & Monarch IC caches — all genuinely landed.
- **Security:** no SQL/ClickHouse injection (server-side parameter binding everywhere,
  allowlisted `ORDER BY`, int-coerced LIMIT/OFFSET); consistent project-scoped RBAC;
  presigned-URL access checks before issuance; hash-locked deps (2142 hashes) + Dependabot +
  SBOM; refuse-to-start on default secrets; real per-email/per-IP login throttling
  (DB-backed, multi-replica safe).
- **Traceability:** server-side ACMG recompute is authoritative and tested (client score
  ignored, codes allowlisted); family deletion does **not** orphan the immutable trail
  (`ON DELETE SET NULL` + denormalized identifiers); canonical-JSON hashing is order-
  independent for the reported-variants portion; the ranking-cache `inputs_hash` is computed
  over sorted sets.
- **Dossier/engineering cross-check:** all 43 RTM-cited test files and 20 implementation
  files exist; append-only triggers (029/032/033) are real; branch protection *is* enabled;
  dependencies *are* hash-locked; SBOM *is* auto-generated — several TF "open actions" are in
  fact closed (reconcile per F-12).
- **Tests:** ~662 backend + ~388 frontend tests, catalogue-enforced; clinical-logic unit
  tests (ACMG/CNV/prioritization/de-novo/Sample-QC/drift) assert real thresholds and
  abstain-on-degraded behaviour, not just smoke; de-flaking handled with readiness gates, not
  retries.

---

## 10. IVDR / standards mapping (key gaps → clauses)

| Gap (this plan) | IVDR / standard touchpoint | Dossier doc |
| --- | --- | --- |
| Software version & per-sample linkage (P1-1) | Art 5(5)(h); Annex I §20.4.1; IEC 62304 §16.1 | TF-09 §6, TF-16, TF-18 §2 |
| Reference-DB / SOUP provenance (P1-14) | IEC 62304 §8 (SOUP); Annex I §16 | TF-08, TF-06 H8 |
| Enforced + evident immutability (P1-3/4/5) | Annex I §16 data integrity; ISO 14971; IEC 62304 §5.7 | TF-06, TF-09 §7, TF-09b REQ-TRACE-008 |
| Sample-QC sign-out gate (P1-2) | ISO 14971 (H4 S5); ISO 15189 | TF-06 H4 |
| CI security scanning / headers / auth (B1–B6) | IEC 81001-5-1; MDCG 2019-16; ISO 27001; GDPR Art 9 | TF-13, TF-14 |
| Observability / backups / migrations (D1–D4) | ISO 15189 operational phase; IVDR PMS Art 78–81 | TF-16, TF-07 |
| Release records / change control (E2) | Art 5(5)(h); IEC 62304 §6,§8 | TF-18, H11.1-OP5 |
| Coverage / GIAB regression / e2e (E3) | IEC 62304 §5.7.3; analytical performance | TF-09, TF-10 |
| Performance evaluation (P3-1) | IVDR Annex XIII (scientific validity + analytical + clinical) | TF-10, TF-11 |
| RMF / usability / DPO / DoC (P3-4) | ISO 14971; IEC 62366-1; GDPR Art 35; Art 5(5)(f) | TF-06, TF-12, TF-14, TF-04 |

---

## 11. Suggested sequencing notes

- **Do Phase 0 immediately** — P0-1 is active data loss on every restart; the rest are
  one-liners that remove real nondeterminism and a PHI-logging exposure.
- **P1-1 (version identity) is the keystone** — sequence it early; it unblocks the IVDR
  version-traceability blockers, the PMS/vigilance linkage, and lets the TF-09 §6 release
  checkbox be honestly ticked. Land it *with* tags/CHANGELOG (P1-15) so the first real
  release record exists.
- **T3 immutability (P1-3/4/5) ships as one unit** — privilege separation, hash-chaining,
  and the verifying tests are only meaningful together.
- **Observability before its consumers** — P1-11 (`/metrics`) is the substrate that makes
  the scheduled integrity check (P2-6), the job reaper (P2-5), and PMS trending actionable;
  do it before them.
- **P3-1 (performance evaluation) is the long pole for clinical release** and is gated on
  clinical-lead input (comparators/thresholds/N), not engineering — start that conversation
  in parallel with Phase 1 so it isn't on the critical path at the end.

---

_Findings are evidence-based with `file:line` citations in §3–§8; see the per-workstream
reviews for full detail. No source files were modified in producing this plan._
