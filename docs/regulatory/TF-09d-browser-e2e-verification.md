# TF-09d — Browser (GUI) End-to-End Verification & Manual Reproduction

| Field | Value |
| --- | --- |
| Document ID | TF-09d |
| Version | v0.1 DRAFT |
| Status | Draft for internal review |
| Owner | ‹CMGG software lead› |
| Approver | ‹Lab director› |
| Date | 2026-06-29 |
| Parent | [TF-09 — Verification & Validation](TF-09-verification-validation.md) |
| Sibling | [TF-09c — End-to-End Pipeline Verification](TF-09c-e2e-pipeline-verification.md) |
| Standards | IEC 62304 §5.7 (system testing); IVDR Annex I §16.1 (repeatability), §20.4.1; usability boundary to IEC 62366-1 ([TF-12](TF-12-usability.md)) |

> Controlled companion to [TF-09](TF-09-verification-validation.md) and sibling to
> [TF-09c](TF-09c-e2e-pipeline-verification.md). Where TF-09c verifies the device boundary at the
> **headless pipeline/API level** (ingestion → datastores → query/API → review/audit → signed
> report), TF-09d verifies that **same boundary through the actual graphical user interface a
> clinician uses** — a real **Chromium** browser driving the React UI against a *live* backend
> (uvicorn) and *live* Postgres and ClickHouse. The UI is the **production build**, served by the
> production frontend server (`server.mjs`, with its enforcing Content-Security-Policy and `/api`
> proxy), and every journey fails if the browser reports a CSP violation.
>
> It documents two things an external reviewer or auditor needs: **(1)** the automated browser
> verification (the Playwright suite + CI `e2e-playwright` job), and **(2)** a **step-by-step
> manual reproduction procedure** (§4) so the verification can be **independently re-run and
> visually witnessed** — either by watching the automated journeys execute in a headed browser or
> by clicking through the same journeys by hand. It runs on synthetic data, so it does not replace
> the acceptance testing on real data that H11.1-OP5 expects ([TF-09](TF-09-verification-validation.md)).

---

## 1. Scope & relationship to the other verification levels

The browser verification exercises the intended-use workflows end-to-end **as rendered**, catching
defects the API-level suite cannot (routing, authentication round-trip in the SPA, data→view
wiring, visualisation render). It is deliberately thin; its limits are in §10.

| Aspect | [TF-09c](TF-09c-e2e-pipeline-verification.md) (pipeline) | **TF-09d (browser)** |
| --- | --- | --- |
| Driven through | Python pipeline + HTTP API | Real Chromium → React UI → API |
| Layer verified | Ingestion, storage, query/API, review/audit, sign-out | Login, navigation, view render, in-browser sign-out |
| Assertion style | Per-stage expected-vs-`EXPECTED.yaml` | Screen/route reached + element/visualisation visible |
| CI job | `e2e` (**required**) | `e2e-playwright` (**required**, §7) |
| Summative usability? | No | **No** — that remains [TF-12](TF-12-usability.md) |

The device boundary under test is the one defined in [TF-02](TF-02-device-description.md):
**annotated VCF (+ tracks) → signed clinical report**, here observed through the operator's screen.

## 2. Controlled dataset & test identity

The browser suite reuses the **same synthetic golden-trio fixture** as TF-09c, so the two layers
verify one consistent ground truth. No patient data is involved.

| Artefact | Path | Role |
| --- | --- | --- |
| Golden-trio fixture | `backend/tests/e2e/fixtures/golden_trio/` | Synthetic strict trio (FATHER/MOTHER unaffected, PROBAND affected); see [TF-09c §1](TF-09c-e2e-pipeline-verification.md). |
| Browser seed script | [scripts/seed_playwright_e2e.py](../../scripts/seed_playwright_e2e.py) | Idempotently imports the golden trio **and** creates a known-credential login user. Synthetic only — no PHI. |
| Login helper / identifiers | [frontend/e2e/helpers.ts](../../frontend/e2e/helpers.ts) | Defines the e2e user credentials and the golden family id the specs use. |

| Test identity | Default value | Override |
| --- | --- | --- |
| E2E user (role `admin`, so it can see the golden family's project) | `e2e.playwright@example.com` | `E2E_USER_EMAIL` |
| E2E password | `e2e-playwright-pw` | `E2E_USER_PASSWORD` |
| Golden family id | `FAM_TRIO` | — |

> The seed creates a dedicated test user, since the production admin's password comes from a
> secret. Seed and specs read the same environment variables, and the user exists only in the
> **test** database (`APP_ENV=test`).

## 3. Verified journeys & evidence

The specs live in [frontend/e2e/](../../frontend/e2e/). Each drives the real stack; a consolidated
operational catalogue is in [docs/testing.md](../testing.md) ("Browser end-to-end (Playwright)").

| Journey | Spec | What it asserts |
| --- | --- | --- |
| Authentication | [auth.spec.ts](../../frontend/e2e/auth.spec.ts) | Bad credentials stay on `/login` (no auth bypass); the seeded user logs in via the real form and lands authenticated on `/dashboard`. |
| Family workspace + genome view | [family.spec.ts](../../frontend/e2e/family.spec.ts) | Opens the golden family workspace (stays authenticated, identifier rendered); the genome overview draws its tracks/ideogram (`canvas`/`svg` present) — confirms frontend↔backend wiring and that the visualisation renders. |
| Clinical report sign-out | [signout.spec.ts](../../frontend/e2e/signout.spec.ts) | The seeded report-tagged variant renders; clicking **Sign out report** opens each acknowledge-with-reason dialog that applies (evidence drift, then Sample QC), a reason is typed in each, the signed-out **version advances**, and the page then matches the signed version — the clinical traceability control of [clinical-traceability.md](../clinical-traceability.md) witnessed through the UI. |
| Security headers | [security-headers.spec.ts](../../frontend/e2e/security-headers.spec.ts) | The production frontend server sends its security headers: a Content-Security-Policy (`default-src 'self'`, `frame-ancestors 'none'`, `object-src 'none'`), `X-Content-Type-Options: nosniff` and a Permissions-Policy. |

## 4. Manual reproduction procedure (for external reviewers / auditors)

This procedure lets a reviewer **independently re-run and visually witness** the verification on a
workstation. It is the controlled, citable form of "examining the e2e validation in the browser".

### 4.0 Prerequisites

- **Docker** (for the datastores), **Node.js 22** (at least 22.22.0), and a **Python** interpreter with the backend
  dependencies installed (`pip install -r backend/requirements-dev.txt`). If those deps live in a
  specific interpreter, point Playwright at it with `E2E_PYTHON=/path/to/python` (used in §4.1).
- Run all commands from the **repository root** unless noted.
- **No PHI** is used or produced; the seed writes only the synthetic golden trio into the local
  **test** databases.

### 4.1 Bring up the stack and seed the data

```bash
# 1. Start the datastores (the same images as CI)
docker compose up -d postgres clickhouse

# 2. Seed the synthetic golden trio + the known e2e login user (idempotent)
RUN_INTEGRATION=1 python scripts/seed_playwright_e2e.py

# 3. Install the browser engine once (Chromium)
cd frontend && npx playwright install chromium
```

### 4.2 Option A — Watch the automated journeys run in a real browser *(recommended)*

Playwright starts the backend (uvicorn :8000) and the production frontend itself: it builds the
bundle (`npm run build`) and serves it with `node server.mjs` on :4173, the server the frontend
image runs. It then drives Chromium through the journeys of §3. From `frontend/`:

```bash
# Interactive UI: pick a journey, watch the live browser, time-travel each step
E2E_PYTHON=/path/to/python npx playwright test --ui

# …or just watch them run headed end-to-end
E2E_PYTHON=/path/to/python npx playwright test --headed

# …or step through one journey under the inspector
E2E_PYTHON=/path/to/python npx playwright test signout --debug
```

`E2E_FRONTEND=dev` swaps the production server for the Vite dev server (:5173), which is quicker
when iterating on a journey but sends no CSP, so it is not the verification run.
`E2E_BACKEND_PORT` moves the backend off :8000 when that port is taken.

After a run, open the recorded evidence (§5):

```bash
npx playwright show-report          # HTML pass/fail report
npx playwright show-trace <trace>   # step-by-step DOM/network/console time-travel
```

### 4.3 Option B — Click through the journeys by hand

To witness the workflows as a human operator, start the two servers and navigate manually:

```bash
# Terminal 1 — backend (light startup: reference/HPO/gene loads disabled)
APP_ENV=test REFERENCE_BOOTSTRAP_ENABLED=false HPO_BOOTSTRAP_ON_STARTUP=false \
  GENE_REFERENCE_BOOTSTRAP_ON_STARTUP=false \
  python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000

# Terminal 2 — the production frontend (built bundle, CSP, proxies /api to the backend)
cd frontend && npm run build && PORT=4173 BACKEND_URL=http://127.0.0.1:8000 node server.mjs
```

Then open **http://127.0.0.1:4173** and reproduce §3 by hand:

| Step | Action | Expected observation (acceptance) |
| --- | --- | --- |
| 1 | Submit `/login` with a wrong password | Stays on `/login`; no access granted. |
| 2 | Log in as `e2e.playwright@example.com` / `e2e-playwright-pw` | Lands authenticated on `/dashboard`. |
| 3 | Open `Families → FAM_TRIO` | Family workspace renders with the `FAM_TRIO` identifier. |
| 4 | Open the family's **Genome overview** | Ideogram / track visualisation draws (SVG/canvas). |
| 5 | Open the family **Report**, click **Sign out report**, and type a reason in each override dialog that appears (evidence drift, then Sample QC) | Report signs out; the signed-out **version number increments**, and the page says it matches the signed version. |
| 6 | Reload `/login` with the browser's network panel open | The response carries a Content-Security-Policy (`default-src 'self'`, `frame-ancestors 'none'`) and `X-Content-Type-Options: nosniff`. |

> The default credentials are non-secret test values; override via `E2E_USER_EMAIL` /
> `E2E_USER_PASSWORD` at seed time if your environment requires it.

## 5. Evidence artefacts produced

| Artefact | Where | Notes |
| --- | --- | --- |
| HTML test report | `frontend/playwright-report/` | Pass/fail per journey; open with `npx playwright show-report`. |
| Trace (DOM/network/console time-travel) | attached on first retry (`trace: 'on-first-retry'`) | Open with `npx playwright show-trace`. |
| Screenshot | captured **only on failure** (`screenshot: 'only-on-failure'`) | Embedded in the report. |
| CI artefact | `e2e-playwright` job uploads `frontend/playwright-report` (`if: always()`, **14-day** retention) | Auditable record of each CI run. |

Configuration of record: [frontend/playwright.config.ts](../../frontend/playwright.config.ts).

## 6. Acceptance criteria

The browser verification **passes** when, against a freshly seeded test stack, a reviewer observes:

- bad credentials are rejected and never leave `/login`; the seeded user authenticates and reaches
  `/dashboard`;
- the `FAM_TRIO` workspace renders authenticated, and the genome overview draws its
  visualisation (SVG/canvas present);
- the clinical report signs out from the browser — each override dialog that appears (evidence
  drift, then Sample QC) takes a reason, the signed-out **version advances**, and the page then
  matches the signed version;
- the production frontend server sends its security headers;
- all journeys in §3 are green in the Playwright report.

Any deviation is a verification finding handled per [TF-09 §5](TF-09-verification-validation.md)
(anomaly handling) and, if clinically relevant, [TF-17](TF-17-vigilance-capa.md) vigilance/CAPA.

## 7. How it runs (gate)

- **Locally / for audit:** the §4 procedure (`npx playwright test`, with the datastores up and the
  data seeded).
- **CI:** the **`e2e-playwright`** job in `.github/workflows/ci.yml` provisions the datastores,
  seeds the golden trio + e2e user, installs Chromium, runs the journeys on every PR and push to
  `main`, and uploads the report (§5). It is a **required status check**
  ([TF-18 §6](TF-18-change-configuration-management.md)), so a failing browser journey blocks the
  merge.

## 8. Mapping to the CMGG report form (H11.1-F12.2)

This document is evidence for the validation axes of the bio-IT ingangsvalidatie; the mapping
is in [TF-09 §7](TF-09-verification-validation.md).

## 9. Reproducibility & data handling

- **Synthetic, deterministic, idempotent.** The fixture and seed are version-controlled; the same
  commit yields the same journeys against the same data. Re-running the seed does not duplicate.
- **No PHI.** Only the synthetic golden trio is loaded, into the local **test** datastores.

## 10. Limitations / out of scope

- **Shallow by design** — a required, blocking check (§7), but it asserts that a journey completes,
  the right screen appears and a visualisation draws (SVG/canvas present). It does not check that
  the rendered content is clinically correct, chart internals or exhaustive review flows; chart
  correctness is covered by the frontend component tests and the per-feature logic tests (TF-09b
  RTM).
- **Chromium only** — cross-browser/responsive coverage is not in scope here.
- **Not summative usability** — formative/summative usability engineering is [TF-12](TF-12-usability.md)
  (IEC 62366-1).
- **Synthetic data only** — analytical/clinical accuracy on real material is the concordance study in
  [TF-10](TF-10-performance-evaluation-plan.md)/[TF-11](TF-11-performance-evaluation-report.md).
