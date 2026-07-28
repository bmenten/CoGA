# Security-audit suppression register

_Companion to the automated security gates in [`.github/workflows/security.yml`](.github/workflows/security.yml)
(dependency-audit, secret-scan, SAST). Every suppression those gates apply is recorded
here so it is **code-reviewed**, justified, and dated, rather than buried in a CI flag.
This is direct evidence for the cybersecurity technical-file item (TF-13)._

> **Policy:** suppress only what genuinely cannot be fixed, scope the justification to
> why the device is not exposed, name an owner, and record a flip/review date. Anything
> with an available non-breaking fix is **fixed**, not suppressed.

Owner: ‹CMGG software lead› · Review cadence: each release, and on every Dependabot alert.

---

## 1. Dependency audit (`pip-audit` / `npm audit`)

### 1a. Backend — `ecdsa` Minerva timing attack (**resolved, no longer suppressed**)

| Field | Value |
| --- | --- |
| Advisory | **GHSA-wj6h-64fc-37mp** — Minerva timing attack on P-256 in `python-ecdsa` |
| Resolution | **Removed the dependency.** JWT handling was migrated from `python-jose` to **PyJWT** (`PyJWT[crypto]`), which verifies HS256/RS256 via the `cryptography` backend and does not depend on `ecdsa`. The hash-locked requirements no longer contain `ecdsa`, `python-jose`, `rsa` or `pyasn1`. |
| Suppression | **None.** The former `pip-audit --ignore-vuln GHSA-wj6h-64fc-37mp` carve-out has been removed from `security.yml`; the dependency-audit gate now blocks on _any_ advisory, including a reappearance of `ecdsa`. |
| History | Previously suppressed as a no-fix transitive of `python-jose[cryptography]`; CoGA only ever used HS256 (local) + RS256 (Azure), never the vulnerable ECDSA P-256 path. The documented flip action (migrate off `python-jose`/`ecdsa`) has now been taken. |

### 1b. Frontend production tree — **fixed, not suppressed**

`vite` (high) and `yaml` (moderate) advisories were resolved in this change by a
non-major lockfile bump (`vite ^7.0.6 → ^7.3.6`, `yaml` pinned `^2.8.3` via
`overrides`). Both remain fixed rather than suppressed; the gate blocks any new
production high/critical other than the single dated entry in **1c** below.

### 1c. Frontend production tree — `react-router` RSC-mode CSRF bypass (**suppressed, dated**)

| Field | Value |
| --- | --- |
| Advisory | **GHSA-qwww-vcr4-c8h2** (high) — _React Router: RSC Mode CSRF Bypass Allows Action Execution Before 400 Response_. Affects `react-router` `>= 7.12.0, < 8.3.0`; reaches us transitively through `react-router-dom@7.18.1`. Published 2026-07-24, surfaced on this repository 2026-07-28. |
| Exposure | **None — the vulnerable path is not reachable.** The advisory is specific to react-router's **RSC / framework server mode**, where a server action can execute before the CSRF check returns its 400. CoGA ships a **Vite SPA**: routing is a plain `<BrowserRouter>` mounted in `frontend/src/index.tsx`, with no `@react-router/dev`, no `react-router.config.*`, no RSC server entry and no server actions. There is no request path into the affected handler, and the deployed artifact is static assets behind the FastAPI backend. |
| Why not fixed | **No non-breaking fix exists.** The patch lands only in `react-router` **8.3.0**, a major upgrade of the router; `npm audit fix --force` instead resolves _downward_ to `react-router-dom@7.11.0`, also a breaking change. Per the policy above, a fix requiring a major migration is suppressed with justification rather than rushed into a release. |
| Mechanism | Named entry in [`scripts/frontend-audit-allowlist.json`](scripts/frontend-audit-allowlist.json), enforced by [`scripts/audit-frontend-prod.mjs`](scripts/audit-frontend-prod.mjs). The gate stays **blocking** for every other high/critical production advisory, and fails if this entry outlives its review date or stops matching a real finding. |
| Flip action | Migrate `react-router` **v7 → v8** (≥ 8.3.0) and delete the entry from both this register and the allowlist file. |
| Owner | ‹CMGG software lead› |
| Added / review by | **2026-07-28** / **2026-10-28** |

### 1d. Frontend dev/build tree — report-only (dated flip)

| Advisories | `glob`, `minimatch`, `picomatch`, `ws`, `ajv`, `brace-expansion` (via the `vitest`/`eslint` toolchain) |
| --- | --- |
| Exposure | **Build/test-only** — none are in the deployed runtime artifact (`npm audit --omit=dev` is clean). |
| Mechanism | Non-blocking `npm audit --audit-level=high` step that surfaces them as a CI `::warning::`. |
| Flip action | Convert that step to **blocking** once the `eslint 8→9` / `vitest` toolchain upgrade lands (next dependency-maintenance sprint). |

---

## 2. Secret scanning (`gitleaks`)

Run via the **gitleaks binary** (pinned, `security.yml`), not `gitleaks-action@v2`:
under the `CenterForMedicalGeneticsGhent` org the action requires a paid
`GITLEAKS_LICENSE`. The binary is MIT-licensed, uses the same `.gitleaks.toml`, and
scans the **full git history** (`fetch-depth: 0`) — stricter than the action's
diff-only default.

Allowlisted in [`.gitleaks.toml`](.gitleaks.toml) — all **non-secrets**:

| Entry | Why it is not a secret |
| --- | --- |
| `ci-smoke-not-a-real-secret`, `ci-admin-not-a-real-secret` | Deliberate placeholder values used only by the CI smoke job (`.github/workflows/ci.yml`); not valid for any real environment. |
| `grch3[78]_coordinates` | False positive: the `grch37_coordinates` / `grch38_coordinates` dict-field selector in `panelapp_service.py` (PanelApp is a public, key-less API); the default `generic-api-key` rule flags the surrounding assignment shape, not a credential. |
| `.env.example` (path) | The template operators copy and fill in; ships `change-me`-style placeholders by design. |
| `sbom/*.cdx.json` (path) | Generated dependency inventories (names + hashes), not credentials. |

The gate fires on any **new** secret outside these documented entries.

---

## 3. SAST (CodeQL)

No file-based allowlist. CodeQL's per-PR **diff baseline** is the mechanism: on a pull
request the Code Scanning check fails only on alerts **introduced by the PR**;
pre-existing alerts on `main` are recorded in the Security tab without blocking. Triage
of the existing backlog happens in the Security tab, not via suppression here.

---

## 4. Branch-protection action required (not code)

For these gates to actually block merges, add **`deps`**, **`secret-scan`**, and the
**`codeql`** checks to the required status checks for `main` in branch protection — the
same step needed for the existing `backend`/`frontend`/`smoke` checks. Until then the
gates are advisory.
