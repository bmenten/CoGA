# Release record — CoGA `<version>`

> Copy this file, fill every field, and file it per the CMGG QMS
> ([TF-18 §7](regulatory/TF-18-change-configuration-management.md)). The procedure in
> [`RELEASING.md`](../RELEASING.md) produces it.
>
> This record ties a signed-out analysis, and so the EPD clinical report that refers to it, to a
> specific build. If a result is questioned, it says which code, dependencies and infrastructure
> produced it. So in any field, prefer "unknown" over a plausible guess.

## 1. Identification

| Field | Value |
| --- | --- |
| Version (`VERSION`) | `<e.g. 0.1.0-beta.1>` |
| Git tag | `<v0.1.0-beta.1>` |
| Commit SHA (full) | `<40-char sha>` |
| Tag is an ancestor of `main` | ☐ verified (`build.yml`'s `prepare` job passed, or `./scripts/check-release-on-main.sh <tag>`) |
| Release type | ☐ pre-release (synthetic data) ☐ clinical release |
| TF-18 change level | ☐ patch ☐ minor ☐ major — *rationale:* `<…>` |
| CMGGMC software number | `S<xxxx>` |
| Date | `<YYYY-MM-DD>` |
| Released by | `<name, role>` |

## 2. Build artefacts

| Artefact | Value |
| --- | --- |
| Backend image | `<registry>/coga-backend:<tag>` |
| Backend digest | `sha256:<…>` |
| Frontend image | `<registry>/coga-frontend:<tag>` |
| Frontend digest | `sha256:<…>` |
| `build.yml` run | `<url>` |
| Deploy job outcome | ☐ applied ☐ **skipped (no GCP credentials)** ☐ failed |

*Copy the digests from the `build.yml` run summary, where each build job wrote the digest of the
image it pushed. Deploy runs each image by that digest (RELEASING.md §4). The tag is mutable, so a
record without digests cannot identify the deployed artefact.*

## 3. Verification evidence

| Item | Value |
| --- | --- |
| All twelve required checks green | ☐ — CI run: `<url>` |
| QA's four-eyes review of the pull requests since the last reviewed tag, signed before the tag ([TF-18 §6](regulatory/TF-18-change-configuration-management.md)) | ☐ — record: `<reference, date>` |
| Backend tests | `<n passed / n skipped>` |
| Frontend tests | `<n passed>` |
| `backend.cdx.json` SHA-256 | `<…>` — *from the release's `sbom-sha256sums.txt`* |
| `frontend.cdx.json` SHA-256 | `<…>` |
| SBOMs attached to the release | ☐ — *by `build.yml`'s `release-sbom` job; release URL:* `<…>` |
| Deployed `/api/version` matches | ☐ — `version=<…>` `git_sha=<…>` |

## 4. Clinical release gate

Leave blank for a pre-release; every box is required for a clinical release
([TF-09 §6](regulatory/TF-09-verification-validation.md)).

- [ ] RTM updated; no requirement without a passing verifying test
- [ ] Risk file (TF-06) reviewed for new/affected hazards; controls verified
- [ ] SOUP register / SBOM (TF-08 / TF-13) reconciled; no unaddressed high-severity vulnerability
- [ ] Change significance assessed (TF-18); re-validation run if triggered (TF-10)
- [ ] The version and commit of §3 shown in the app footer and on a family and a NIPT report footer
- [ ] **Report a problem** opens the CMGGMC route (`COGA_PROBLEM_REPORT_URL` set)
- [ ] Reference-data versions frozen and recorded (§5)

## 5. Reference-data baseline

The versions in force for this build: assembly, HPO release, gene panels, ClinVar/gnomAD/dbNSFP
and any other annotation source. A clinical result is reproducible only against the reference
data that produced it.

| Source | Version / release | Recorded where |
| --- | --- | --- |
| `<…>` | `<…>` | `<…>` |

## 6. Deviations

Anything that did not follow the procedure: a skipped step, a re-run failed job, a bypassed
check, a manual fix during the release. **An empty section means "nothing deviated", so do not
leave it empty by default.**

| Deviation | Why | Accepted by |
| --- | --- | --- |
| `<…>` | `<…>` | `<…>` |

## 7. Approval

Per H11.1-OP5. For a clinical release the H11.1-F12.2 signatures apply
([TF-09 §7](regulatory/TF-09-verification-validation.md)).

| Role | Name | Date | Signature |
| --- | --- | --- | --- |
| Eindverantwoordelijke | | | |
| IT-team coördinator | | | |
| Kwaliteitsbeheerder | | | |
