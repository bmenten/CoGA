# Roadmap

The open work, in one list. Items that need an owner or QA decision are tracked in issue
[#518](https://github.com/bmenten/CoGA/issues/518).

## Before clinical use

- **Performance evaluation**: run the TF-10 studies and write the TF-11 report. For each application, the comparator, acceptance criteria and sample size are still to be confirmed.
- **First release**: no version has been tagged or released; [RELEASING.md](../RELEASING.md) is the procedure.
- **Independent review**: `main` requires no approving review and has no CODEOWNERS, and an administrator can bypass the required checks ([TF-18 §6](regulatory/TF-18-change-configuration-management.md)).
- **Technical file**: every document is a draft awaiting approval; the usability summative evaluation (TF-12) and the signed Declaration (TF-04) are still to come.
- **Google Cloud go-live**: the switches that restrict access to institutional networks, run the API as the restricted database role and lock down ClickHouse egress ([#364](https://github.com/bmenten/CoGA/issues/364), [deployment-gcp.md](deployment-gcp.md)); and a restore drill, since the backups configured in Terraform have never been restored.

## Engineering

- **Sessions**: there is no server-side logout or token revocation; a token stays valid until it expires.
- **Operations**: `/metrics` exists ([monitoring.md](monitoring.md)), but the deployment does not scrape it yet and no alert policy is defined; no migration ledger (every schema file is re-applied on each start).
- **Imports and scaling**: a stuck import job is picked up again only when a worker next looks for work, once its heartbeat is ten minutes old, and until it has run again its family's report cannot be signed out: sign-out is refused while a family's import job is queued, validating or running (#727), and an import whose job cannot record the family it imports writes nothing of it (#736). Each backend container runs one uvicorn process whose event loop the API shares with the import and refresh workers.
- **Regression truth set**: no GIAB or GeT-RM truth set with a concordance harness for minor-release validation.
- **Frozen evidence**: a small-variant classification's evidence snapshot keeps the annotation-set hash and the ClinVar significance, not the frequencies or in-silico scores.
- **One filter definition**: the small-variant filters exist twice, as ClickHouse SQL and as Python; parity tests cover genotype classes and hemizygous positions only.

## Product

- **Variant Explorer**: gene-, transcript- and cohort-frequency views beside the variant view.
