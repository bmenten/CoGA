# Roadmap

The open work, in one list. The P0–P3 IDs used across the repository refer to the improvement
workplan of 2026-06-26, which is retired; it is in the git history
(`git log -- docs/IMPROVEMENT-WORKPLAN.md`). Items that need an owner or QA decision are
tracked in issue [#518](https://github.com/bmenten/CoGA/issues/518).

## Before clinical use

- **Performance evaluation** (P3-1): run the TF-10 studies and write the TF-11 report. For each application, the comparator, acceptance criteria and sample size are still to be confirmed.
- **First release** (P1-15): no version has been tagged or released; [RELEASING.md](../RELEASING.md) is the procedure.
- **Independent review** (P1-16): `main` requires no approving review and has no CODEOWNERS, and an administrator can bypass the required checks ([TF-18 §6](regulatory/TF-18-change-configuration-management.md)).
- **Technical file** (P3-4): every document is a draft awaiting approval; the usability summative evaluation (TF-12) and the signed Declaration (TF-04) are still to come.
- **Google Cloud go-live**: the switches that restrict access to institutional networks, run the API as the restricted database role and lock down ClickHouse egress ([#364](https://github.com/bmenten/CoGA/issues/364), [deployment-gcp.md](deployment-gcp.md)); and a restore drill (P1-13), since the backups configured in Terraform have never been restored.

## Engineering

- **Sessions** (P1-8): there is no server-side logout or token revocation; a token stays valid until it expires.
- **Operations** (P1-11, P1-12): no `/metrics` endpoint (the ClickHouse integrity check reports through the log and the admin page), and no migration ledger (every schema file is re-applied on each start).
- **Imports and scaling** (P2-5, P2-2): a stuck import job is picked up again only when a worker next looks for work, and nothing reads the import-incomplete flag, so such a family can still be signed out. Each backend container runs one uvicorn process whose event loop the API shares with the import and refresh workers.
- **Regression truth set** (P2-8): no GIAB or GeT-RM truth set with a concordance harness for minor-release validation.
- **Frozen evidence** (P3-5): a classification's evidence snapshot keeps the annotation-set hash and the ClinVar significance, not the frequencies or in-silico scores.
- **One filter definition** (P3-2): the small-variant filters exist twice, as ClickHouse SQL and as Python; parity tests cover genotype classes and hemizygous positions only.
- **HPO release in the report**: the signed record names the assembly, gene loci and Monarch release CoGA loaded, but not its HPO release.

## Product

- **Variant Explorer**: gene-, transcript- and cohort-frequency views beside the variant view.
