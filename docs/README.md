# Documentation

What CoGA is, and who should read what, is in the [README](../README.md). This page lists
every document.

## Using CoGA

- **User guide and clinical reference** — inside the app at `/docs`; the source is in
  [frontend/src/content/docs/](../frontend/src/content/docs/).
- [Demo data](../demo/README.md) — two synthetic families to try the app with.

## Running and operating CoGA

- [development.md](development.md) — run CoGA locally, reset it, troubleshoot.
- [data-import.md](data-import.md) — load reference data and family data.
- [deployment-gcp.md](deployment-gcp.md) — deploy to Google Cloud with Terraform, and run it day to day.
- [db-runtime-role-runbook.md](db-runtime-role-runbook.md) — switch the API to the restricted database role.
- [RELEASING.md](../RELEASING.md) and [release-record-template.md](release-record-template.md) — cut a release and record it.
- [scripts/](../scripts/README.md) — the helper scripts.

## How CoGA is built

- [application-scheme.md](application-scheme.md) — architecture: the parts, which database holds what, startup, the code map.
- [database.md](database.md) — every Postgres and ClickHouse table.
- [security-posture.md](security-posture.md) — access control, audit logging, encryption and the security checks.
- [testing.md](testing.md) — what each test file covers, and the CI jobs.
- [CONTRIBUTING.md](../CONTRIBUTING.md) — the checks to run before a pull request, and how changes are classified.

## Clinical design, developer detail

The rules for lab users are in the in-app reference docs; these files hold the implementation.

- [acmg-classification.md](acmg-classification.md) — the semi-automatic ACMG/AMP classifier.
- [snv-sv-compound-het.md](snv-sv-compound-het.md) — a small variant and an SV as the two hits in one gene.
- [variant-ranking-cache.md](variant-ranking-cache.md) — the cache behind the phenotype-prioritised ranking.
- [monarch-integration.md](monarch-integration.md) — Monarch gene–disease and disease–phenotype data.
- [haplotype-segregation-analysis.md](haplotype-segregation-analysis.md) — PGT haplotype segregation.
- [monogenic-nipt.md](monogenic-nipt.md) — monogenic NIPT, with its fetal-fraction and classification algorithm.
- [family-member-management.md](family-member-management.md) — what an edit to a family member changes.
- [report-template.md](report-template.md) — how the family report is drafted.
- [clinical-traceability.md](clinical-traceability.md) — sign-out, the clinical audit trail and tamper evidence.
- [annotation-provenance.md](annotation-provenance.md) — how tool and database versions are read from VCF headers.

## Regulatory and review

- [regulatory/README.md](regulatory/README.md) — the IVDR technical file.
- [handleiding/README.md](handleiding/README.md) — the Dutch technical manual for the review board.
- [ROADMAP.md](ROADMAP.md) — the open work.
- [SECURITY.md](../SECURITY.md) and [SECURITY-AUDIT-ALLOWLIST.md](../SECURITY-AUDIT-ALLOWLIST.md) — how to report a vulnerability, and the register of security-check exceptions.
- [CHANGELOG.md](../CHANGELOG.md) — notable changes.
