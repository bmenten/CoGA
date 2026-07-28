# Release record — CoGA `<version>`

> Copy this file, fill every field, and file it per the CMGG QMS
> ([TF-18 §7](regulatory/TF-18-change-configuration-management.md)). Produced by the
> procedure in [`RELEASING.md`](../RELEASING.md).
>
> This record is what ties a signed clinical report back to a specific build. If a result is
> ever questioned, this is the document that says which code, which dependencies and which
> infrastructure produced it — so prefer "unknown" over a plausible guess in any field.

## 1. Identification

| Field | Value |
| --- | --- |
| Version (`VERSION`) | `<e.g. 0.1.0-beta.1>` |
| Git tag | `<v0.1.0-beta.1>` |
| Commit SHA (full) | `<40-char sha>` |
| Tag is an ancestor of `main` | ☐ verified (`git merge-base --is-ancestor <sha> origin/main`) |
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

*The digests are not captured automatically — resolve them with
`gcloud artifacts docker images describe` (RELEASING.md §4). A record without digests cannot
identify the deployed artefact, because the tag is mutable.*

## 3. Verification evidence

| Item | Value |
| --- | --- |
| All ten required checks green | ☐ — CI run: `<url>` |
| Backend tests | `<n passed / n skipped>` |
| Frontend tests | `<n passed>` |
| `sbom/backend.cdx.json` SHA-256 | `<…>` |
| `sbom/frontend.cdx.json` SHA-256 | `<…>` |
| SBOM archived to the technical file | ☐ — *artifact expires 90 days after the build* |
| Deployed `/api/version` matches | ☐ — `version=<…>` `git_sha=<…>` |

## 4. Clinical release gate

Leave blank for a pre-release; every box is required for a clinical release
([TF-09 §6](regulatory/TF-09-verification-validation.md)).

- [ ] RTM updated; no requirement without a passing verifying test
- [ ] Risk file (TF-06) reviewed for new/affected hazards; controls verified
- [ ] SOUP register / SBOM (TF-08 / TF-13) reconciled; no unaddressed high-severity vulnerability
- [ ] Change significance assessed (TF-18); re-validation run if triggered (TF-10)
- [ ] Version/build identifier visible in the report footer
- [ ] Reference-data versions frozen and recorded (§5)

## 5. Reference-data baseline

The versions in force for this build — assembly, HPO release, gene panels, ClinVar/gnomAD/
dbNSFP and any other annotation source. A clinical result is only reproducible against the
reference data that produced it.

| Source | Version / release | Recorded where |
| --- | --- | --- |
| `<…>` | `<…>` | `<…>` |

## 6. Deviations

Anything that did not follow the procedure — a skipped step, a failed job that was re-run, a
bypassed check, a manual fix applied during the release. **An empty section means "nothing
deviated", so do not leave it empty by default.**

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
