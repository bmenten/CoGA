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
`overrides`). Both remain fixed rather than suppressed.

The **GHSA-qwww-vcr4-c8h2** react-router RSC-mode CSRF bypass (high) was briefly
suppressed here as a dated entry (#386): published 2026-07-24 with a fix only in 8.3.0
(major), it had no non-breaking remedy, and CoGA was not exposed (the vulnerable path is
RSC / framework server mode; this is a Vite SPA on a plain `<BrowserRouter>`). The
documented flip action has since been carried out — Node was raised to 22 (#388) and
`react-router-dom@7.18.1` replaced by `react-router@^8.3.0` (#389). `npm audit
--omit=dev` reports **0 vulnerabilities** and the allowlist in
[`scripts/frontend-audit-allowlist.json`](scripts/frontend-audit-allowlist.json) is
empty, so the production gate now blocks on **any** high/critical with no exemptions.

### 1c. Frontend dev/build tree — report-only (dated flip)

| Advisories | `glob`, `minimatch`, `picomatch`, `ws`, `ajv`, `brace-expansion` (via the `vitest`/`eslint` toolchain) |
| --- | --- |
| Exposure | **Build/test-only** — none are in the deployed runtime artifact (`npm audit --omit=dev` is clean). |
| Mechanism | Non-blocking `npm audit --audit-level=high` step that surfaces them as a CI `::warning::`. |
| Fixed where possible | `brace-expansion` **GHSA-3jxr-9vmj-r5cp** was resolved by a non-breaking `npm audit fix` (1.1.15 → 1.1.16), closing the Dependabot alert. Per the policy above it is fixed, not suppressed. |
| Known blocker | `brace-expansion` **GHSA-mh99-v99m-4gvg** remains: it needs `> 5.0.7`, but the vulnerable copy is pulled in by `eslint-plugin-react@7.37.5 → minimatch@3.1.5`, which requires `brace-expansion@^1`. An `overrides` pin to `^5.0.8` was tested — it does zero the audit, but `npm run lint` then dies in `new Minimatch(...)`, so it is not shippable. Same upstream blocker as the `eslint 9→10` bump (`eslint-plugin-react` has no eslint-10 support). |
| Flip action | Convert that step to **blocking** once `eslint-plugin-react` ships eslint-10 support (or is swapped for `@eslint-react/eslint-plugin`) and the `vitest` toolchain upgrade lands, which removes the `minimatch@3` chain with it. |

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

## 4. Branch protection (enforced)

These gates block merges today. **`deps (pip-audit + npm audit)`**,
**`secret-scan (gitleaks)`**, **`codeql (python)`** and **`codeql (javascript-typescript)`**
are four of the **ten required status checks** on `main`, alongside
`backend`/`frontend`/`smoke`/`e2e`/`e2e-playwright`/`catalogue`, with **strict**
(up-to-date-before-merge) enforcement.

Residual, tracked in [TF-18 §6](docs/regulatory/TF-18-change-configuration-management.md):
`enforce_admins` is disabled (an administrator can bypass), and no approving review is
mechanically required.
