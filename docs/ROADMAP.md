# Roadmap

The open work, in one list. Items that need an owner or QA decision are tracked in issue
[#518](https://github.com/bmenten/CoGA/issues/518). The defects are ranked in
[open-issues.md](open-issues.md); an item that moved here from that list keeps its ID in
brackets.

## Before clinical use

- **Performance evaluation**: run the TF-10 studies and write the TF-11 report. For each application, the comparator, acceptance criteria and sample size are still to be confirmed.
- **First release**: no version has been tagged or released; [RELEASING.md](../RELEASING.md) is the procedure.
- **Independent review**: pull requests need no approval; QA reviews the changes per release and signs off before the tag (owner decision, 2026-10-14). That review has not run yet, and an administrator can bypass the required checks ([TF-18 §6](regulatory/TF-18-change-configuration-management.md)).
- **Technical file**: every document is a draft awaiting approval; the usability summative evaluation (TF-12) and the signed Declaration (TF-04) are still to come.
- **Google Cloud go-live**: the switches that restrict access to institutional networks, run the API as the restricted database role and lock down ClickHouse egress ([#364](https://github.com/bmenten/CoGA/issues/364), [deployment-gcp.md](deployment-gcp.md)); and a restore drill, since the backups configured in Terraform have never been restored.

## Engineering

- **Sessions**: there is no server-side logout or token revocation; a token stays valid until it expires.
- **Operations**: `/metrics`, its collector and the alert policies exist ([monitoring.md](monitoring.md)), but nothing is deployed until Terraform is first applied. The Postgres baselines are re-applied on every start until the release candidate freezes them ([database.md](database.md#freezing-the-baselines-at-the-release-candidate)).
- **Imports and scaling**: an import whose process stopped is noticed only when a worker next looks for work, once its job's heartbeat is ten minutes old, and until then its family's report cannot be signed out: sign-out is refused while a family's import job is queued, validating or running (#727). The worker then runs a job that was still validating again, but ends one that was running (it had begun writing the family) as interrupted, without running it again, and the family stays marked import-incomplete until what that import had not finished is imported again with overwrite (#743). A live import whose heartbeat goes stale is taken for stopped the same way, and is stopped where it is once its heartbeat reaches the database again and finds its job ended; until then it may go on, but a restore it reaches deletes nothing without its backup ([#746](https://github.com/bmenten/CoGA/issues/746)). A restore that fails part-way may have removed rows of any dataset, whichever import wrote them: its flag names every dataset of its import as failed, those it imported too, and its import's entry stays, naming every dataset the family's imports and single-file uploads recorded (another small-variant source, another package's SV calls, a caller's SV calls an upload loaded), so the family stays marked until the whole family has been imported again with overwrite, what an upload brought uploaded again with overwrite. An import whose heartbeat has not reached the database for five minutes stops itself where it is, before a worker can take it for stopped, and one whose variant-write locks' connection is lost is stopped where it is, nothing restored, its job ended failed (SAFE-15). An import whose job cannot record the family it imports writes nothing of it (#736). Each backend container runs one uvicorn process whose event loop the API shares with the import and refresh workers.
- **Regression truth set**: no GIAB or GeT-RM truth set with a concordance harness for minor-release validation.
- **One filter definition**: the small-variant filters exist twice, as ClickHouse SQL and as Python; parity tests cover genotype classes and hemizygous positions only.
- **Request tracing** (SEC-16): nothing carries a request ID from a log line to its audit row; `/api/health/ready`, which also checks that Postgres and ClickHouse answer, is polled by nothing (the probes and the uptime check use `/api/health`; the deployment guide calls it by hand).

## Product

- **Variant Explorer** (PROD-2): gene-, transcript- and cohort-frequency views beside the variant view.
- **Variant summary statistics** (PROD-1): small-variant summary statistics per individual and per family ([#1](https://github.com/bmenten/CoGA/issues/1)).
- **SV filter presets on screen** (PROD-3): no screen saves a reusable SV preset or deletes one; the API supports both.
- **Long-read packages** (PROD-4): no dataset type reads a plain multi-sample Sniffles2 VCF, and QDNAseq-style raw-bin CNV files are not importable.
- **Per-sample GLIMPSE2 haplotypes** (PROD-5): per-sample GLIMPSE2 BCF haplotypes are registered but not imported.
- **Single-parent recessive risk** (PROD-6): it reads "uninformative" by design; a later version could say "carries the known parent's risk allele, donor unknown".
- **Haplogroup comparison** (PROD-7): REQ-MITO-003 has no automated haplogroup comparison; the analyst compares the haplogroups CoGA lists, and the owner decides whether that stays a manual review step (TF-09b §3).
- **Editing a phenotype term** (PROD-8): the UI can add or delete a phenotype term but not edit one.
- **More than one PGT region** (PROD-9): a PGT family has one ROI, and the signed record's PGT section holds one, so two loci need its format extended after the RC (#899).
- **Sort by the raw variant score** (ENG-9): the UI could offer novel-gene candidates sorted by the raw variant score, as the ranking's comments once promised (the comments were corrected).
