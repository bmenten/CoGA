# Security policy

CoGA is operated as an **in-house IVD under IVDR Article 5(5)** (CMGG, ISO 15189), with the
device boundary _annotated VCF → signed clinical report_. Security issues in it are handled
under the cybersecurity item of the technical file,
[TF-13](docs/regulatory/TF-13-cybersecurity.md); this page is the public-facing entry point
to that process.

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
pipeline, report sign-out), reproduction steps, and the impact you believe it has.

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

CoGA is **pre-release** and deployed only within the UZ Gent/CMGG managed environment. There
is no supported public deployment, and no released version stream to backport to: fixes go to
`main` under change control ([TF-18](docs/regulatory/TF-18-change-configuration-management.md)).

---

## Scope

**In scope** — anything reachable within the device boundary: the FastAPI backend and its
routers, authentication and authorization (roles, project scoping), the variant/family import
pipeline, report generation and sign-out, and the frontend served by this repository.

**Out of scope** — the surrounding managed environment (network, TLS termination, IdP,
database hosting) is operated by UZ Gent/CMGG IT and reported through their channels. Findings
in third-party dependencies are usually best reported upstream first; if one affects CoGA
specifically, tell us and we will track it in the SOUP register
([TF-08](docs/regulatory/TF-08-soup-register.md)).

---

## How we handle what you report

Automated gates run on every pull request and are described in
[SECURITY-AUDIT-ALLOWLIST.md](SECURITY-AUDIT-ALLOWLIST.md), which also records **every**
suppression those gates apply, with a justification, an owner and a review date:

- **Dependency audit** — `pip-audit` (hash-locked backend) and `npm audit` (frontend). The
  production tree blocks on any high/critical; a component with a non-breaking fix is fixed
  rather than suppressed.
- **Secret scanning** — `gitleaks` over the full git history.
- **SAST** — CodeQL for Python and TypeScript.
- **SBOM** — CycloneDX inventories generated per build.

Triage assesses exploitability in CoGA's actual deployment and impact on safety and PHI;
remediation goes through change control with CI and review, with an expedited path for
actively-exploited criticals (TF-13 §6).
