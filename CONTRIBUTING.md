# Contributing to CoGA

Thanks for looking. Please read the next section before opening a pull request — CoGA is not
an ordinary open-source project, and that changes what can be accepted.

## What CoGA is, and what that means for contributions

CoGA is a diagnostic medical device in use at CMGG: an in-house IVD under IVDR Article 5(5)
(see the [README](README.md#regulatory-status)). Every change to it is a change to that
device, governed by CMGG SOP **H11.1-OP5** and recorded in the technical file under
[docs/regulatory/](docs/regulatory/README.md).

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

You need Docker, **Python 3.12** and **Node 22** (at least the `engines` floor in
`frontend/package.json`). Then:

```bash
cp .env.example .env         # and set APP_ENV=development in it
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build -d
```

The web app is on <http://localhost:3000>. [docs/development.md](docs/development.md) covers
the rest: running the backend or frontend outside Docker, demo data, reset and
troubleshooting.

## Before you open a PR

Run the gates CI runs. From the repository root, with the backend tools installed
(`pip install -r backend/requirements-dev.txt`):

```bash
# backend
ruff check . && mypy && python -m pytest -q
python scripts/generate-api-types.py --check

# frontend
(cd frontend && npm run tsc && npm run lint && npx vitest run)

# repository gates
./scripts/check-test-catalogue.sh      # docs/testing.md lists every test file
./scripts/check-handleiding-sync.sh    # the handleiding HTML matches its Markdown
node scripts/audit-frontend-prod.mjs   # production dependency audit
```

Run both `tsc` and `vitest` for any frontend change: the type check misses broken component
tests, and vitest misses type errors in the tests. In CI, vitest also enforces the coverage
floors in `frontend/vite.config.mts` (`npm run test:coverage`). The handleiding check needs
the generator's pinned dependency: `pip install markdown==3.10.3`.

The rules these gates enforce, and the ones they cannot:

- **Tests are catalogued.** A new test file needs its row in [docs/testing.md](docs/testing.md),
  and a deleted one loses its row. The `catalogue` check fails otherwise; the same check
  also fails when the handleiding HTML is out of date.
- **The handleiding is rebuilt.** After editing a chapter under `docs/handleiding/`, run
  `python docs/handleiding/build_site.py` and commit the regenerated `coga-handleiding.html`.
- **The frontend's API types follow the backend.** After changing a Pydantic model the API
  serves, run `python scripts/generate-api-types.py` in an environment installed from
  `backend/requirements-dev.txt`, and commit the regenerated types.
- **API paths are encoded.** Build every frontend API path with ``apiPath`…` ``
  (`frontend/src/lib/apiPath.ts`), so an identifier from imported data cannot change which
  endpoint is called.
- **No lint warnings.** `npm run lint` fails on any ESLint warning (`--max-warnings 0` in
  `frontend/package.json`); type what you would have typed `any`.
- **Clinical-critical modules keep their coverage.** A module listed under `[tool.mypy]` in
  `pyproject.toml` needs a floor in `scripts/check-coverage-floor.py`, or CI fails.
- **The change is recorded.** Until the first release candidate the pull request itself is the
  record ([TF-18 §3a](docs/regulatory/TF-18-change-configuration-management.md)):
  [CHANGELOG.md](CHANGELOG.md) holds one summary of the pre-release work and takes no
  per-change entries, and
  [TF-10 §8](docs/regulatory/TF-10-performance-evaluation-plan.md) lists the clinical behaviour
  the first validation must cover by area, not by change: update it only when your change adds
  clinical behaviour that no area covers. From the release candidate on, each change adds an entry under `[Unreleased]` in
  CHANGELOG.md carrying its level, and a row to the change-record log of
  [TF-18 §8](docs/regulatory/TF-18-change-configuration-management.md). QA confirms the level.

### Branches and commits

Branch from `main` as `type/short-description` (`fix/…`, `feat/…`, `chore/…`, `docs/…`,
`refactor/…`, `ci/…`, `build/…`, `deploy/…`).

Commits follow [Conventional Commits](https://www.conventionalcommits.org/):
`type(scope): imperative summary`. Explain **why** in the body — the diff already shows what.
For anything touching clinical behaviour, state what you verified and how.

### CI

`main` requires the CI and security checks that [docs/testing.md](docs/testing.md) describes,
and is strict, so your branch must be up to date before it can merge. How that protection is
enforced, and where it falls short, is recorded in
[TF-18 §6](docs/regulatory/TF-18-change-configuration-management.md).

If `deps` fails on something you did not introduce, it is usually a newly published advisory
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
| **Major `X.y.z`** | Backward-incompatible, or **potential impact on clinical output, interpretation or intended use** — caller/cut-off changes, annotation or reference-version changes, filter/decision-rule changes. | Minor steps **+ clinical opvolgvalidatie** (H11.1-F2) and a CMGGMC ICT update. A change to the intended purpose or scope also updates TF-01 to TF-05. |

> Rule of thumb, and the line between minor and major: **if it could change a clinical output,
> its interpretation, or the validated scope, it is major** — and it cannot reach clinical use
> without clinical opvolgvalidatie.

Say which level you believe your change is, and why, in the PR description. Getting it wrong
is not a problem; not thinking about it is.

## Review and approval

Every change needs **4-eye review** — a second (bio-)IT team member for patch level, and
progressively broader sign-off for minor and major (TF-18 §4). Please request a review rather
than self-merging.

> **Enforcement:** branch protection requires the status checks but does **not** require an
> approving review, so the 4-eye rule is today a process commitment rather than an enforced
> control. Treat it as binding regardless. Enforcement is scheduled to be turned on with the
> **first beta release**.

## Releasing

Cutting a release follows [`RELEASING.md`](RELEASING.md). Contributors do not cut releases,
but it is worth reading before proposing a change that affects versioning, the build, or the
deployment.

## Code of conduct

Participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) (Contributor Covenant
2.1).
