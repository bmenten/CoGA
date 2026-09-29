# Security policy

CoGA is an in-house IVD operated by CMGG (see the [README](README.md#regulatory-status)).
Security issues in it are handled under the cybersecurity item of its technical file,
[TF-13](docs/regulatory/TF-13-cybersecurity.md); this page is the public entry point to that
process.

> The data in this repository is **synthetic**. It contains no patient data, and none should
> ever be attached to a report here.

---

## Reporting a vulnerability

**Use GitHub's private vulnerability reporting** — the *Report a vulnerability* button under
the repository's [Security tab](https://github.com/bmenten/CoGA/security/advisories/new). It
is enabled on this repository, and the report stays private to the maintainers until an
advisory is published.

**Please do not open a public issue for a security problem**, and please do not include real
patient data, credentials or PHI in a report — a synthetic reproduction is always sufficient
and is what we will ask for.

Helpful to include: affected version or commit, component (backend API, frontend, import
pipeline, report sign-out, deployment code), reproduction steps, and the impact you believe
it has.

If you would rather not use GitHub, or the issue needs institutional escalation, contact
the project owner directly: **Björn Menten — <bjorn.menten@ugent.be>**.

We aim to acknowledge a report within **five working days**. Formal response and remediation
targets are being aligned with the CMGG vigilance process
([TF-17](docs/regulatory/TF-17-vigilance-capa.md)) and will be stated here with the first
beta release.

### This is not the route for clinical incidents

A suspected **patient-safety or diagnostic incident** — a wrong or misleading result on a
real case — is a **vigilance** matter, not a code-security report. It goes through the CMGG
QMS incident route ([TF-17](docs/regulatory/TF-17-vigilance-capa.md)), not this repository,
and must not wait on a GitHub advisory.

---

## Supported versions

| Version | Supported |
| --- | --- |
| `main` (current, `0.1.0`) | ✅ Fixes land here |
| Anything earlier | ❌ Not maintained |

CoGA is **pre-release** and has no production deployment yet; the target is Google Cloud,
set up with the Terraform code in [`terraform/`](terraform/)
([TF-02 §10](docs/regulatory/TF-02-device-description.md)). There is no released version
stream to backport to: fixes go to `main` under change control
([TF-18](docs/regulatory/TF-18-change-configuration-management.md)).

---

## Scope

**In scope** — everything in this repository: the FastAPI backend and its routers,
authentication and authorization (roles, project scoping), the variant and family import
pipeline, report generation and sign-out, the frontend and the server that serves it, and
the deployment code in `terraform/`.

**Out of scope** — the services CoGA runs on but does not contain: the institution's network
and identity provider, and the Google Cloud platform itself. Report those to their operators.
Findings in third-party dependencies are usually best reported upstream first; if one affects
CoGA specifically, tell us and we will track it in the SOUP register
([TF-08](docs/regulatory/TF-08-soup-register.md)).

---

## How we handle what you report

Automated checks run on every pull request. Every exception they allow is recorded, with a
justification, an owner and a review date, in
[SECURITY-AUDIT-ALLOWLIST.md](SECURITY-AUDIT-ALLOWLIST.md):

- **Dependency audit** — `pip-audit` over the hash-locked backend blocks on any advisory;
  `npm audit` blocks on any high or critical advisory in the frontend's production tree and
  reports on its build and test tools. A component with a non-breaking fix is fixed, not
  suppressed.
- **Secret scanning** — `gitleaks` over the full git history.
- **SAST** — CodeQL for Python and TypeScript.
- **SBOM** — CycloneDX inventories of the backend and frontend, generated on every CI run
  ([sbom/README.md](sbom/README.md)).

Triage assesses exploitability in CoGA's actual deployment and impact on safety and PHI;
remediation goes through change control with CI and review, with an expedited path for
actively exploited criticals (TF-13 §6).
