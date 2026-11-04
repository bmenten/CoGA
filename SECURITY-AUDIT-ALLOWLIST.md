# Security-audit suppression register

_Companion to the automated security checks in [`.github/workflows/security.yml`](.github/workflows/security.yml)
(dependency audit, secret scan, SAST). Every exception those checks apply is recorded here,
so it is **code-reviewed**, justified and dated rather than buried in a CI flag. This is
evidence for the cybersecurity item of the technical file (TF-13)._

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
| Resolution | **Removed the dependency.** JWT handling moved from `python-jose` to **PyJWT** (`PyJWT[crypto]`), which verifies HS256/RS256 through the `cryptography` backend, without `ecdsa`. The hash-locked requirements no longer contain `ecdsa`, `python-jose` or `rsa`. |
| Suppression | **None.** `pip-audit` blocks on any advisory, including a reappearance of `ecdsa`. |

### 1b. Frontend production tree — **no suppressions**

`npm audit --omit=dev` reports no advisories, and the allowlist the gate reads
([`scripts/frontend-audit-allowlist.json`](scripts/frontend-audit-allowlist.json)) is empty,
so the production gate blocks on **any** high or critical advisory. Past findings, newest
first:

- `follow-redirects` **GHSA-mrwp-f3q8-3g9p** (high, through axios): fixed on 2026-10-31 by the
  lockfile update to 1.16.2, within the range axios 1.20.0 accepts.
- `body-parser` **GHSA-27r3-jxmh-p828** (high, through express): fixed on 2026-10-26 by the
  update to 2.4.0.
- The gate's one past exemption, the react-router RSC-mode CSRF bypass
  **GHSA-qwww-vcr4-c8h2**: retired by the upgrade to react-router 8 (#389).

### 1c. Frontend dev/build tree — report-only

| Field | Value |
| --- | --- |
| Exposure | **Build and test tools only** — nothing in this tree ships in the deployed frontend. |
| Mechanism | A non-blocking `npm audit --audit-level=high` step in `security.yml` shows any advisory as a CI `::warning::`. |
| Status (2026-11-05) | `npm audit` over the full tree reports **no advisories**. The last one was `minimatch` GHSA-g3hv-75wg-m249 (moderate, denial of service through glob-pattern amplification, published 2026-10-30). On 2026-11-04 its affected range was narrowed to 7.0.0 up to 11.0.1. That leaves the ESLint toolchain's `minimatch` 3.1.5 (`eslint` 9, `@eslint/config-array`, `@eslint/eslintrc`, `eslint-plugin-react@7.37.5`, `eslint-plugin-jsx-a11y@6.10.2`) unaffected, and only the 10.2.6 copy under `@typescript-eslint/typescript-estree` affected. Its fixed release, 11.0.1, is a major version that typescript-estree's `^10.2.2` does not accept. So an npm `overrides` entry in `frontend/package.json` gives typescript-estree `minimatch` ^11.0.1 (now 11.0.4). It calls only the `minimatch()` function, which 11 keeps. **Remove the override** once typescript-eslint accepts `minimatch` 11. Earlier findings were fixed by non-breaking updates: most recently `zod` GHSA-mrhx-c7xc-m3w5 (pulled in by eslint-plugin-react-hooks) by 4.6.7, `source-map-js` GHSA-68fv-2mgg-jv7q by 1.2.2 (#755) and `brace-expansion` GHSA-q2hr-2g5m-vwhr by 1.1.21 (#722). |
| Flip action | Make the step blocking. Nothing in the tree prevents it today; **🔲 owner decision**. |

---

## 2. Secret scanning (`gitleaks`)

Run as the **gitleaks binary** (pinned and checksum-verified in `security.yml`), not
`gitleaks-action@v2`, which needs a paid `GITLEAKS_LICENSE` for repositories owned by an
organisation. The binary is MIT-licensed, uses the same `.gitleaks.toml`, and scans the
**full git history** (`fetch-depth: 0`) — stricter than the action's diff-only default.

Allowlisted in [`.gitleaks.toml`](.gitleaks.toml) — all **non-secrets**:

| Entry | Why it is not a secret |
| --- | --- |
| `ci-smoke-not-a-real-secret`, `ci-admin-not-a-real-secret` | Deliberate placeholder values used only by the CI smoke job (`.github/workflows/ci.yml`); not valid for any real environment. |
| `grch3[78]_coordinates` | False positive: the `grch37_coordinates` / `grch38_coordinates` dict-field selector in `panelapp_service.py` (PanelApp is a public, key-less API). The default `generic-api-key` rule flags the assignment shape around it, not a credential. |
| `(?:upload\|deletion)-[0-9a-f]{32}` | False positive: the key of an upload's or a deletion's mark on a family (`import_unfinished` entries, SAFE-28, #876), a random identifier, not a credential. Tests hold fixed ones (`DELETION_KEY = 'deletion-…'`), which the default `generic-api-key` rule flags on the `KEY =` assignment shape. |
| `.env.example` (path) | The template operators copy and fill in; ships `change-me`-style placeholders by design. |
| `sbom/*.cdx.json` (path) | Generated dependency inventories (names + hashes), not credentials. |

The check fires on any **new** secret outside these documented entries.

---

## 3. SAST (CodeQL)

No file-based allowlist. CodeQL's per-PR **diff baseline** is the mechanism: on a pull
request the Code Scanning check fails only on alerts **introduced by the PR**.
Pre-existing alerts on `main` are recorded in the Security tab without blocking, and are
triaged there, not suppressed here.

---

## 4. Branch protection

`deps (pip-audit + npm audit)`, `secret-scan (gitleaks)`, `codeql (python)` and
`codeql (javascript-typescript)` are required status checks on `main`, so these checks block
merges. The protection and its open gaps are recorded in
[TF-18 §6](docs/regulatory/TF-18-change-configuration-management.md).
