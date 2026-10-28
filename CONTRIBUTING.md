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
  change carries a regulatory classification, and the release that ships it waits for QA's
  review (below). A perfectly good patch may still need a validation activity before it can
  land.
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
  `backend/requirements-dev.txt`, and commit the regenerated types. Read a response through
  its generated type (`frontend/src/lib/apiSchema.generated.ts`) where it has one; a
  hand-written type in `frontend/src/lib/apiTypes.ts` is checked against it in `apiContract.ts`.
- **API paths are encoded.** Build every frontend API path with ``apiPath`…` ``
  (`frontend/src/lib/apiPath.ts`), so an identifier from imported data cannot change which
  endpoint is called. An in-app route that carries an identifier is built with
  ``routePath`…` `` (`frontend/src/lib/routePath.ts`), which encodes it the same way. A URL
  the backend hands the browser to follow is built with `url_path`
  (`backend/app/core/url_path.py`), and a file name in `Content-Disposition` with
  `content_disposition` (`backend/app/core/content_disposition.py`).
- **No lint warnings.** `npm run lint` fails on any ESLint warning (`--max-warnings 0` in
  `frontend/package.json`); type what you would have typed `any`.
- **Clinical-critical modules keep their coverage.** A module listed under `[tool.mypy]` in
  `pyproject.toml` needs a floor in `scripts/check-coverage-floor.py`, or CI fails. The whole
  backend's floor there is also the unit run's `--cov-fail-under` in `ci.yml`: change both.
- **The backend locks are compiled, not edited.** After changing `backend/requirements*.in`,
  run `./scripts/compile-requirements.sh` (Docker, Python 3.12 for linux/amd64) and commit
  both locks. CI's `lock` job runs the script again and fails when a lock differs from what
  it compiles.
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

`main` requires the CI and security checks that [docs/testing.md](docs/testing.md) describes.
Your branch need not be up to date with `main` before it merges: `main`'s own run after each
merge checks the combined result, so merge `main` in yourself when it may clash. The
workflows also run for GitHub's merge queue (`merge_group`), which `main` does not require
yet (OPS-22). How that protection is enforced, and where it falls short, is recorded in
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

A pull request needs **no approving review**: it merges once the required checks pass. CoGA
has one developer, so no second (bio-)IT team member can approve each change, and the owner
decided on 2026-10-14 that the four-eyes principle is met per release instead. Before a release
is tagged, **QA reviews** every change to the clinical-critical modules (those listed under
`[tool.mypy]` in `pyproject.toml`) and a sample of the rest, and signs that review
([TF-18 §6](docs/regulatory/TF-18-change-configuration-management.md)). It is a compensating
measure for the missing per-change approval. Minor and major changes keep their broader
sign-off (TF-18 §4).

QA reviews from what the pull request holds: its description (what changed and why, the change
level and what you verified, and how), the summary of its diff, its tests and the verification
records it updated. Write the description with that reader in mind.

## Releasing

Cutting a release follows [`RELEASING.md`](RELEASING.md). Contributors do not cut releases,
but it is worth reading before proposing a change that affects versioning, the build, or the
deployment.

## Code of conduct

Participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) (Contributor Covenant
2.1).
