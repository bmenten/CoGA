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
- **Operations**: `/metrics`, its collector and the alert policies exist ([monitoring.md](monitoring.md)), but nothing is deployed until Terraform is first applied; no migration ledger (every schema file is re-applied on each start).
- **Imports and scaling**: an import whose process stopped is noticed only when a worker next looks for work, once its job's heartbeat is ten minutes old, and until then its family's report cannot be signed out: sign-out is refused while a family's import job is queued, validating or running (#727). The worker then runs a job that was still validating again, but ends one that was running (it had begun writing the family) as interrupted, without running it again, and the family stays marked import-incomplete until what that import had not finished is imported again with overwrite (#743). A live import whose heartbeat goes stale is taken for stopped the same way, and is stopped where it is once its heartbeat reaches the database again and finds its job ended; until then it may go on, but a restore it reaches deletes nothing without its backup, and its flag claims no dataset the restore may have removed ([#746](https://github.com/bmenten/CoGA/issues/746)). Still open: an import whose heartbeat never reaches the database again is not stopped (nothing stops an import itself before its heartbeat is ten minutes old), and nothing checks the connection that holds an import's variant-write locks. An import whose job cannot record the family it imports writes nothing of it (#736). Each backend container runs one uvicorn process whose event loop the API shares with the import and refresh workers.
- **Regression truth set**: no GIAB or GeT-RM truth set with a concordance harness for minor-release validation.
- **Frozen evidence**: a small-variant classification's evidence snapshot keeps the annotation-set hash and the ClinVar significance, not the frequencies or in-silico scores.
- **One filter definition**: the small-variant filters exist twice, as ClickHouse SQL and as Python; parity tests cover genotype classes and hemizygous positions only.

## Product

- **Variant Explorer**: gene-, transcript- and cohort-frequency views beside the variant view.
