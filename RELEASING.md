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
- [ ] `main` is green and you are releasing the commit you think you are.
- [ ] You can reach the GCP project, or you accept that `deploy` will skip (see
      [Known limitations](#known-limitations)).

---

## 1. Prepare the release commit

Decide the version. The convention (TF-18 §2) is that **`VERSION` is the single source of
truth and the tag mirrors it** as `v<VERSION>`. Pre-releases take a SemVer suffix:
`0.1.0-beta.1`, `0.1.0-rc.2`.

```bash
# 1a. Bump VERSION
printf '0.1.0-beta.1\n' > VERSION

# 1b. Move the CHANGELOG's [Unreleased] heading to the new version, dated.
#     From the first release onward each entry also carries its TF-18 level —
#     see the format note at the top of CHANGELOG.md.

# 1c. Check it before you push anything
./scripts/check-release-version.sh v0.1.0-beta.1
```

That last command is the same guard CI runs. It fails if the tag and `VERSION` disagree, or
if `VERSION` is not valid SemVer — **run it locally so you find out now, not after pushing a
tag you then have to delete.**

Open a PR with the bump, let the ten required checks pass, and merge it.

## 2. Tag and publish

**Publishing the GitHub Release is the only thing that triggers a release build.** Pushing a
tag on its own does nothing (deliberately — see [Known limitations](#known-limitations)).

```bash
git checkout main && git pull
git tag -a v0.1.0-beta.1 -m "CoGA 0.1.0-beta.1"
git push origin v0.1.0-beta.1

gh release create v0.1.0-beta.1 \
  --title "CoGA 0.1.0-beta.1" \
  --notes-file <(sed -n '/## \[0.1.0-beta.1\]/,/^## /p' CHANGELOG.md) \
  --prerelease            # omit for a clinical release
```

## 3. Watch the build

`build.yml` runs once, in this order:

| Job | What it does | If it fails |
| --- | --- | --- |
| `prepare` | Asserts the tag matches `VERSION`, then computes build metadata | **Nothing was built.** Fix `VERSION`, delete the tag and release, start again at step 1 |
| `build-backend` / `build-frontend` | Cloud Build → Artifact Registry, stamped with `APP_VERSION` + `GIT_SHA` | Images may be partially pushed; safe to re-run |
| `deploy` | `terraform plan` then `apply` against the `gcp-deploy` environment | Infrastructure may be partly applied — read the plan output before re-running |

## 4. Capture the evidence

Do this **before** the artifacts expire — the SBOM is deleted after 90 days.

```bash
RUN=$(gh run list --workflow build.yml --limit 1 --json databaseId --jq '.[0].databaseId')

# SBOMs (CycloneDX 1.6, backend + frontend)
gh run download "$RUN" --name sbom-cyclonedx --dir release-evidence/
shasum -a 256 release-evidence/*.cdx.json

# Image digests — NOT captured automatically; resolve the tag to its digest
REG=europe-west1-docker.pkg.dev/<registry-project>/gen-ghreg-shared-gbl
for c in backend frontend; do
  gcloud artifacts docker images describe "$REG/coga-$c:v0.1.0-beta.1" \
    --format='value(image_summary.digest)'
done
```

Then verify the deployed build really is the one you released:

```bash
curl -s https://<host>/api/version
# -> {"version":"0.1.0-beta.1","git_sha":"<12-char sha>"}
# `version` must equal VERSION, `git_sha` the tagged commit. A response of
# {"version":"0.0.0+unknown","git_sha":"unknown"} means the image was built without
# APP_VERSION/GIT_SHA build args — the deploy did not ship a stamped build.
```

## 5. File the release record

Copy [`docs/release-record-template.md`](docs/release-record-template.md), fill it, and file
it per the CMGG QMS ([TF-18 §7](docs/regulatory/TF-18-change-configuration-management.md)).
It is the artefact that ties a signed clinical report back to a specific build, so the image
digests and SBOM hashes matter more than they look.

---

## Rollback

There is no automated rollback. To go back to a known-good release, re-point the deployment
at its images and re-apply:

```bash
gcloud artifacts docker images describe "$REG/coga-backend:<previous-tag>" \
  --format='value(image_summary.digest)'    # confirm the target exists first
```

Then re-run `deploy` from the previous release's tag, or apply Terraform locally with
`backend_image` / `frontend_image` set to the previous tag. Record the rollback as a change
under TF-18 — a rollback is a release.

> Rolling back the **application** does not roll back a **database migration**. The schema
> loader applies every file on boot; check whether the previous image can run against the
> current schema before rolling back.

---

## Known limitations

Stated here so nobody discovers them mid-release:

- **Deploy pins a tag, not a digest.** Terraform receives `…coga-backend:<tag>`. For a
  release that is effectively immutable (you do not re-tag), but on `main` the tag is
  literally `main`, so Cloud Run may not roll a new revision at all. Verify with
  `/api/version` rather than assuming a green deploy shipped your code.
- **No digest is captured automatically** — step 4 resolves them by hand.
- **The SBOM artifact expires after 90 days** and CI does not run on `release: published`
  for the SBOM job, so archiving it into the technical file is a manual step. Miss it and
  the dependency evidence for that build is gone.
- **`deploy` skips entirely when GCP is not configured** (no `GCP_WIF_PROVIDER` secret). The
  run goes green having deployed nothing. Check the job actually ran.
- **The `gcp-deploy` environment has no required reviewer** until one is configured, so
  `terraform apply -auto-approve` runs unattended.
- **A tag is not verified to be on `main`.** Nothing stops tagging an arbitrary commit.
