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
| Resolution | **Removed the dependency.** JWT handling moved from `python-jose` to **PyJWT** (`PyJWT[crypto]`), which verifies HS256/RS256 through the `cryptography` backend and does not depend on `ecdsa`. The hash-locked requirements no longer contain `ecdsa`, `python-jose` or `rsa`. |
| Suppression | **None.** `pip-audit` blocks on any advisory, including a reappearance of `ecdsa`. |

### 1b. Frontend production tree — **no suppressions**

`npm audit --omit=dev` reports no advisories, and the allowlist the gate reads
([`scripts/frontend-audit-allowlist.json`](scripts/frontend-audit-allowlist.json)) is empty,
so the production gate blocks on **any** high or critical advisory. Its one past exemption,
the react-router RSC-mode CSRF bypass **GHSA-qwww-vcr4-c8h2**, was retired by the upgrade to
react-router 8 (#389).

### 1c. Frontend dev/build tree — report-only

| Field | Value |
| --- | --- |
| Exposure | **Build and test tools only** — nothing in this tree ships in the deployed frontend. |
| Mechanism | A non-blocking `npm audit --audit-level=high` step in `security.yml` shows any advisory as a CI `::warning::`. |
| Status (2026-09-29) | `npm audit` over the full tree reports **no advisories**. The last known finding, `brace-expansion` GHSA-mh99-v99m-4gvg, was fixed by the non-breaking update to 1.1.18 (#442). `minimatch@3` is still pulled in by `eslint-plugin-react@7.37.5`. |
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
| `grch3[78]_coordinates` | False positive: the `grch37_coordinates` / `grch38_coordinates` dict-field selector in `panelapp_service.py` (PanelApp is a public, key-less API); the default `generic-api-key` rule flags the surrounding assignment shape, not a credential. |
| `.env.example` (path) | The template operators copy and fill in; ships `change-me`-style placeholders by design. |
| `sbom/*.cdx.json` (path) | Generated dependency inventories (names + hashes), not credentials. |

The check fires on any **new** secret outside these documented entries.

---

## 3. SAST (CodeQL)

No file-based allowlist. CodeQL's per-PR **diff baseline** is the mechanism: on a pull
request the Code Scanning check fails only on alerts **introduced by the PR**;
pre-existing alerts on `main` are recorded in the Security tab without blocking. Triage
of the existing backlog happens in the Security tab, not via suppression here.

---

## 4. Branch protection

`deps (pip-audit + npm audit)`, `secret-scan (gitleaks)`, `codeql (python)` and
`codeql (javascript-typescript)` are required status checks on `main`, so these checks block
merges. The protection and its open gaps are recorded in
[TF-18 §6](docs/regulatory/TF-18-change-configuration-management.md).
