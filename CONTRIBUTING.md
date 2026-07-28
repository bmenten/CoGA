# Contributing to CoGA

Thanks for looking. Please read the next section before opening a pull request — CoGA is not
an ordinary open-source project, and that changes what can be accepted.

## What CoGA is, and what that means for contributions

CoGA is operated by CMGG as an **in-house IVD under IVDR Article 5(5)** (ISO 15189), with the
device boundary _annotated VCF → signed clinical report_. Every change to it is a change to a
diagnostic medical device, governed by CMGG SOP **H11.1-OP5** and recorded in the technical
file under [docs/regulatory/](docs/regulatory/README.md).

Practically:

- The source is published under [Apache-2.0](LICENSE) so it can be **read, audited and
  learned from**. Reuse is permitted by the licence, but see [NOTICE](NOTICE): the validation
  does not travel with the code.
- **External pull requests are welcome but cannot be merged on technical merit alone.** Every
  change carries a regulatory classification and an approval step (below). A perfectly good
  patch may still need a validation activity before it can land.
- If you are unsure whether something is worth your time, **open an issue first**. That costs
  you nothing and may save you a rewrite.

**Do not report security vulnerabilities in a pull request or issue** — see
[SECURITY.md](SECURITY.md). **Do not report suspected clinical incidents here at all**; those
go through CMGG's vigilance route (TF-17).

All data in this repository is **synthetic**. Never add real patient data, PHI, credentials
or identifiable material to a branch, test fixture, issue or PR — including in a screenshot.

## Getting set up

**Node 22** (floor `22.22.0`, see [`.nvmrc`](.nvmrc)) and **Python 3.10**.

```bash
nvm use                      # reads .nvmrc
docker compose up --build -d # postgres, clickhouse, backend, frontend
```

Frontend on <http://localhost:3000>, backend health at `/api/health`. More detail, including
the dev stack with hot reload, is in [docs/development.md](docs/development.md).

## Before you open a PR

Run the same gates CI will. These are the fast ones and they catch most of it:

```bash
# frontend
cd frontend && npm run tsc && npm run lint && npx vitest run

# backend
python -m pytest -q

# repo gates
./scripts/check-test-catalogue.sh        # docs/testing.md lists every test file
./scripts/check-handleiding-sync.sh      # handleiding HTML matches its Markdown
node scripts/audit-frontend-prod.mjs     # production dependency audit
```

Two gates surprise people:

- **`catalogue`** fails if you add a test file without a row in
  [docs/testing.md](docs/testing.md), or leave a row for a deleted one.
- **`handleiding`** fails if you edit a chapter under `docs/handleiding/` without rerunning
  `python docs/handleiding/build_site.py`. It rebuilds the file for you — just commit it.

### Branches and commits

Branch from `main` as `type/short-description` (`fix/…`, `feat/…`, `chore/…`, `docs/…`,
`refactor/…`, `ci/…`, `build/…`, `deploy/…`).

Commits follow [Conventional Commits](https://www.conventionalcommits.org/):
`type(scope): imperative summary`. Explain **why** in the body — the diff already shows what.
For anything touching clinical behaviour, state what you verified and how.

### CI

Ten checks are required and `main` is strict, so your branch must be up to date before merge:

`frontend (tsc + eslint + vitest)` · `backend (pytest)` ·
`smoke (real startup against Postgres + ClickHouse)` · `catalogue (test overview in sync)` ·
`codeql (javascript-typescript)` · `codeql (python)` · `deps (pip-audit + npm audit)` ·
`secret-scan (gitleaks)` · `e2e (golden-trio pipeline against Postgres + ClickHouse)` ·
`e2e-playwright (browser journeys)`

If `deps` fails on something you did not introduce, it is usually a newly-published advisory
against the existing tree — see [SECURITY-AUDIT-ALLOWLIST.md](SECURITY-AUDIT-ALLOWLIST.md)
for how those are handled. Fix it if a non-breaking fix exists; suppression is a last resort
and must be justified and dated.

## Change classification — the part that is specific to this project

Every change is classified by the semantic version it produces. This determines what evidence
is required before it can reach clinical use. The authority is
[TF-18](docs/regulatory/TF-18-change-configuration-management.md) §4; this is the short form.

| Level | Means | Required |
| --- | --- | --- |
| **Patch `x.y.Z`** | Backward-compatible, **no functional or clinical impact** on output — bugfix with no output change, refactor, logging, dependency patch, security update. | System test + a unit test for the fix; a [`CHANGELOG.md`](CHANGELOG.md) note. No validation report. |
| **Minor `x.Y.z`** | New backward-compatible functionality, **no change to clinical meaning**. | Patch steps **+ technical opvolgvalidatie** (H11.1-F13) against the previous validated version on a fixed dataset; review the risk analysis ([TF-06](docs/regulatory/TF-06-risk-management-plan.md)). |
| **Major `X.y.z`** | Backward-incompatible, or **potential impact on clinical output, interpretation or intended use** — caller/cut-off changes, annotation or reference-version changes, filter/decision-rule changes. | Minor steps **+ clinical opvolgvalidatie** (H11.1-F2), CMGGMC ICT update, and TF-01/02/03/04/05 review. |

> Rule of thumb, and the line between minor and major: **if it could change a clinical output,
> its interpretation, or the validated scope, it is major** — and it cannot reach clinical use
> without clinical opvolgvalidatie.

Say which level you believe your change is, and why, in the PR description. Getting it wrong
is not a problem; not thinking about it is.

## Review and approval

Every change needs **4-eye review** — a second (bio-)IT team member for patch level, and
progressively broader sign-off for minor and major (TF-18 §4). Please request a review rather
than self-merging.

> **Note on enforcement:** branch protection currently requires the ten status checks but does
> **not** mechanically require an approving review, so the 4-eye rule is today a process
> commitment rather than an enforced one. Treat it as binding regardless. Enforcement is
> scheduled to be turned on with the **first beta release**.

## Known gaps

Recorded here rather than left to be discovered:

- **4-eye review is not yet mechanically enforced** (above) — enforcement lands with the first
  beta release.
- The `catalogue` status check also guards the handleiding page despite its name; the name is
  kept verbatim because it is a required check and renaming it would leave the requirement
  permanently pending.

## Code of conduct

Participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) (Contributor Covenant
2.1).
