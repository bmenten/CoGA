# Releasing CoGA

The mechanics of cutting a release. This is the *how*; the **clinical** release gate is
[TF-09 §6](docs/regulatory/TF-09-verification-validation.md), and the change-control rules
(patch / minor / major, and what evidence each level needs) are
[TF-18 §4](docs/regulatory/TF-18-change-configuration-management.md). Follow those first —
this page assumes the decision to release has already been made and recorded.

> **Beta releases.** TF-09 §6 is headed *per clinical release*. A pre-release on synthetic
> data is not one, so its clinical items do not gate a beta. Run this procedure anyway: the
> beta is the rehearsal, and the release record it produces is the first evidence that the
> process works.

---

## Before you start

- [ ] The change level (patch / minor / major) is decided and its TF-18 evidence exists.
- [ ] QA has signed its four-eyes review of the pull requests merged since the last tag it
      reviewed ([TF-18 §6](docs/regulatory/TF-18-change-configuration-management.md)). Do not
      tag before it.
- [ ] `main` is green and you are releasing the commit you think you are.
- [ ] You can reach the GCP project, or you accept that `deploy` will skip (see
      [Known limitations](#known-limitations)).

---

## 1. Prepare the release commit

Decide the version. The convention (TF-18 §2) is that **`VERSION` is the single source of
truth and the tag mirrors it** as `v<VERSION>`. Pre-releases take a SemVer suffix:
`0.1.0-beta.1`, `0.1.0-rc.2`.

Only the release build reports that version. Every other build (a push to `main`, a manual or
local build) reports `VERSION` with the pre-release identifier `dev` added: `0.1.0-dev`, or
`0.1.0-rc.2.dev` once `VERSION` is `0.1.0-rc.2`, in `/api/version`, the footers and the
signed reports ([`scripts/build-version.sh`](scripts/build-version.sh)). So `VERSION` never
carries `dev` or build metadata (`+…`).

```bash
# 1a. Bump VERSION, give the frontend package the same version (its SBOM names the
#     frontend by it), and rebuild the handleiding, whose version chip reads VERSION
#     (CI's handleiding check fails until the rebuilt HTML is committed).
printf '0.1.0-beta.1\n' > VERSION
(cd frontend && npm version --no-git-tag-version 0.1.0-beta.1)
python docs/handleiding/build_site.py

# 1b. Move the CHANGELOG's [Unreleased] heading to the new version, dated.
#     From the first release onward each entry also carries its TF-18 level —
#     see the format note at the top of CHANGELOG.md.

# 1c. Check it before you push anything
./scripts/check-release-version.sh v0.1.0-beta.1
```

That last command is the same guard CI runs. It fails if the tag and `VERSION` disagree,
if `frontend/package.json` does not carry `VERSION`, or if `VERSION` is not valid SemVer or
carries the identifier `dev` or build metadata — **run it locally so you find out now, not
after pushing a tag you then have to delete.**

Open a PR with the bump, let the required checks pass, and merge it.

## 2. Tag and publish

**Publishing the GitHub Release is the only thing that triggers a release build.** Pushing a
tag on its own does nothing (deliberately — see [Known limitations](#known-limitations)).

```bash
git checkout main && git pull
git tag -a v0.1.0-beta.1 -m "CoGA 0.1.0-beta.1"
git push origin v0.1.0-beta.1
./scripts/check-release-on-main.sh v0.1.0-beta.1   # the tag is on main, as build.yml checks

gh release create v0.1.0-beta.1 \
  --title "CoGA 0.1.0-beta.1" \
  --notes-file <(sed -n '/## \[0.1.0-beta.1\]/,/^## /p' CHANGELOG.md) \
  --prerelease            # omit for a clinical release
```

The release build refuses a tag whose commit is not on `main`: a release is built only from
a commit that passed `main`'s required checks and that QA's four-eyes review covers
([TF-18 §6](docs/regulatory/TF-18-change-configuration-management.md)).

## 3. Watch the build

`build.yml` runs once. `tf-validate` runs alongside the other jobs, which run in this order:

| Job | What it does | If it fails |
| --- | --- | --- |
| `tf-validate` | `terraform fmt -check` and `terraform validate`, without cloud credentials | The other jobs do not wait for it, so the build still runs; fix the Terraform before you rely on a deploy |
| `prepare` | Asserts the tag is on `main` (with `main`'s whole history) and matches `VERSION`, then computes build metadata: the release's version for the image | **Nothing was built.** Fix `VERSION`, or tag the right commit on `main`; delete the tag and release (`gh release delete <tag> --cleanup-tag`), start again at step 1 |
| `build-backend` / `build-frontend` | Cloud Build → Artifact Registry, stamped with `APP_VERSION` + `GIT_SHA`; each writes the digest of the image it pushed in the run's summary | Images may be partially pushed; safe to re-run. A build that reports no digest fails, and nothing deploys |
| `deploy` | `terraform plan` then `apply` against the `gcp-deploy` environment, each image by the digest its build pushed — on a release only when `COGA_DEPLOY_TRIGGER` is `release` (see [Known limitations](#known-limitations)). Before the schema migration the new backend image brings, the apply takes an on-demand Cloud SQL backup, described as `Before the schema migration of coga-backend@sha256:<digest>` ([deployment-gcp.md](docs/deployment-gcp.md), 12.1) | Infrastructure may be partly applied — read the plan output before re-running. A failed backup stops the apply before the migration, and the running version keeps serving; re-running takes a new one |
| `release-sbom` | Generates the CycloneDX SBOMs from the release commit, as `ci.yml`'s `sbom` job does, and attaches them with their SHA-256 sums (`sbom-sha256sums.txt`) to the release. Runs whether or not GCP is configured | Nothing deploys from it; re-run it. Once the files are attached a re-run fails on them, and the attached files stay as they are |

## 4. Capture the evidence

```bash
# SBOMs (CycloneDX 1.6, backend + frontend): the release-sbom job attached them to the
# release with their SHA-256 sums. Check the sums, and copy them into the release record.
gh release download v0.1.0-beta.1 --dir release-evidence/ \
  --pattern '*.cdx.json' --pattern 'sbom-sha256sums.txt'
(cd release-evidence && shasum -a 256 -c sbom-sha256sums.txt)

# Image digests: each build job wrote the digest of the image it pushed in the summary of
# the build.yml run, and deploy ran exactly those. The release tag names the same images:
REG=europe-west1-docker.pkg.dev/<registry-project>/gen-ghreg-shared-gbl
for c in backend frontend; do
  gcloud artifacts docker images describe "$REG/coga-$c:v0.1.0-beta.1" \
    --format='value(image_summary.digest)'
done
```

The SBOMs stay on the release for as long as the release exists. If `release-sbom` could not
attach them, check out the tag and run `./scripts/generate-sbom.sh` (needs Docker), and attach
its files with `gh release upload`, or, if the release takes no more files, file them with the
release record: the script uses CI's generators and flags, so it lists the same components,
though the file hashes differ (the backend SBOM carries a fresh serial number and timestamp).
Record that as a deviation.

Then verify the deployed build really is the one you released:

```bash
curl -s https://<host>/api/version
# -> {"version":"0.1.0-beta.1","git_sha":"<12-char sha>"}
# `version` must equal VERSION, `git_sha` the tagged commit. A version with `dev` in it
# (0.1.0-beta.1.dev) is a build of main or a manual build, not the release. An image built
# without the GIT_SHA build arg does not start outside development, so a deploy of one never
# goes healthy. (An image built without APP_VERSION reports VERSION as a development build.)
```

## 5. File the release record

Copy [`docs/release-record-template.md`](docs/release-record-template.md), fill it, and file
it per the CMGG QMS ([TF-18 §7](docs/regulatory/TF-18-change-configuration-management.md)).
It is the artefact that ties a signed-out analysis back to a specific build, and through it the
clinical report in the EPD that refers to that analysis, so the image digests and SBOM hashes
matter more than they look.

---

## Rollback

There is no automated rollback. To go back to a known-good release, re-point the deployment
at its images, by the digests in its release record, and re-apply:

```bash
gcloud artifacts docker images describe "$REG/coga-backend:<previous-tag>" \
  --format='value(image_summary.digest)'    # confirm the target exists first
```

Then re-run only the `deploy` job of the previous release's `build.yml` run (it reuses that
run's digests, so it deploys exactly those images; GitHub re-runs a run for 30 days), or
apply Terraform locally with `backend_image` / `frontend_image` set to the previous release's
images by digest (`…/coga-backend@sha256:<digest>`). Do not re-run the whole run: that builds
new images. Record the rollback as a change under TF-18 — a rollback is a release.

> Rolling back the **application** does not roll back a **database migration**. Schema
> revisions run forward only, and a version older than the database's revision (the ledger
> `alembic_version`) refuses to start on it. Rolling back across a schema revision therefore
> means restoring the Postgres backup the deploy took just before the upgrade (its description
> names the image it preceded), then deploying the previous release at once, as
> [deployment-gcp.md](docs/deployment-gcp.md), 12.3, describes. The restore loses what was
> written since, so record it with the rollback; otherwise go forward with a fix
> ([database.md](docs/database.md#schema-migrations)).

---

## Known limitations

Stated here so nobody discovers them mid-release:

- **A tag on its own builds nothing.** `build.yml` starts a release build only when a GitHub
  Release is published: publishing is the deliberate, recorded act, and the only tag-borne
  event that can reach `deploy`. A `push: tags` trigger used to start a second, complete build
  of the same commit, which raced the release's run to push the same image tags and could never
  deploy: duplicate work with a winner nobody could predict. A tag pushed without its release
  therefore leaves no images; publish the release (step 2) for the build to run.
- **A release does not deploy by default.** `deploy` runs on a release only when the
  repository variable `COGA_DEPLOY_TRIGGER` is `release`; otherwise pushes to `main` deploy
  and a release only builds its images. Only one of the two may deploy, because there is one
  environment and one Terraform state ([deployment-gcp.md](docs/deployment-gcp.md), section
  "CI/CD").
- **A deployment of `main` reports a development version.** With `COGA_DEPLOY_TRIGGER` unset
  or `main`, the environment runs builds of `main`, which report `VERSION` with `dev` added
  (`0.1.0-rc.1.dev`), as do the reports they sign. Only a release deployed by the release
  trigger reports the release's version.
- **The SBOMs live on the GitHub release.** They stay as long as the release does: deleting
  the release deletes them, so the release record keeps their SHA-256 sums. If the repository's
  releases are made immutable (a GitHub setting), a published release takes no more files, so
  `release-sbom` fails and step 4's fallback applies.
- **The image builds and `deploy` skip entirely when GCP is not configured** (no
  `GCP_WIF_PROVIDER` secret). The run goes green having built and deployed nothing. Check the
  jobs actually ran. `release-sbom` still runs.
- **The `gcp-deploy` environment needs a required reviewer** before anything deploys: the
  deploy job's first step fails until one is configured (Settings → Environments), so
  `terraform apply -auto-approve` never runs unattended.
