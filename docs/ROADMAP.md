# Roadmap

The open work, in one list. Items that need an owner or QA decision are tracked in issue
[#518](https://github.com/bmenten/CoGA/issues/518). The defects are ranked in
[open-issues.md](open-issues.md); an item that moved here from that list keeps its ID in
brackets.

## Before clinical use

- **Performance evaluation**: run the TF-10 studies and write the TF-11 report. For each application, the comparator, acceptance criteria and sample size are still to be confirmed.
- **First release**: no version has been tagged or released; [RELEASING.md](../RELEASING.md) is the procedure.
- **Independent review**: pull requests need no approval; QA reviews the changes per release and signs off before the tag (owner decision, 2026-10-14). That review has not run yet. The ruleset that requires the checks has no bypass actors, but an administrator can change or disable it ([TF-18 §6](regulatory/TF-18-change-configuration-management.md)).
- **Technical file**: every document is a draft awaiting approval; the usability summative evaluation (TF-12) and the signed Declaration (TF-04) are still to come.
- **Google Cloud go-live**: the switches that restrict access to institutional networks, run the API as the restricted database role and lock down ClickHouse egress ([deployment-gcp.md](deployment-gcp.md)); and a restore drill, as the backups configured in Terraform have never been restored.

## Engineering

- **Operations**: `/metrics`, its collector and the alert policies exist ([monitoring.md](monitoring.md)), but nothing is deployed until Terraform is first applied. The release candidate freezes the Postgres baselines; from then on each schema change is a revision ([database.md](database.md#freezing-the-baselines-at-the-release-candidate)).
- **Imports and scaling**:
  - A stopped import is noticed only when its job's heartbeat is ten minutes old and a worker next looks for work. Until then the family cannot be signed out: sign-out is refused while its import is queued, validating or running. The worker runs a job that was validating again; one that was running (it had begun writing the family) it ends as interrupted, and the family stays marked import-incomplete until what that import had not finished is imported again with overwrite.
  - A live import whose heartbeat goes stale is taken for stopped the same way. It stops where it is once its heartbeat reaches the database and finds its job ended; until then it may go on, but a restore it reaches deletes nothing without its backup. An import whose heartbeat has not reached the database for five minutes stops itself first; one that loses its variant-write locks' connection stops where it is, restores nothing and ends failed.
  - A restore that fails part-way may have removed rows of any dataset, whichever import wrote them. Its flag names every dataset of its import as failed, those it imported too, and its import's entry names every dataset the family's imports and single-file uploads recorded, so the family stays marked until the whole family is imported again with overwrite, and what an upload brought is uploaded again with overwrite.
  - An import whose job cannot record the family it imports writes nothing of it.
  - Watched import folders ([data-import.md](data-import.md#watched-import-folders)) still lack: an optional mail to the administrators when a delivery is ready; an import without a person's approval (only by a separate decision of the owner); the package format's open points, to settle with the pipeline owners (REG-12).
  - Each backend container runs one uvicorn process, whose event loop the API shares with the refresh worker. With Docker Compose the imports run in import-worker processes of their own, one core each; on Google Cloud they still run in the API's instances.
- **Regression truth set**: no GIAB or GeT-RM truth set with a concordance harness for minor-release validation.
- **Request tracing** (SEC-16): nothing carries a request ID from a log line to its audit row; `/api/health/ready`, which also checks that Postgres and ClickHouse answer, is polled by nothing (the probes and the uptime check use `/api/health`; the deployment guide calls it by hand).

## Product

- **Variant summary statistics** (PROD-1): what the family's *Variant summary* still lacks ([#1](https://github.com/bmenten/CoGA/issues/1)):
  - A call of uniparental disomy or of runs of homozygosity. The page counts the inconsistencies and the homozygous share per chromosome but calls nothing; a call needs its own requirement and validation (REG-15).
  - Speed at genome scale: the statistics are counted on each visit. A synthetic whole-genome trio (6.3 M rows) took about 2.4 s on a laptop's Docker, with up to 385 MiB for one query, reading the family's rows alone; a real genome on the real stack is not yet timed (OPS-9). If it proves slow, keep the statistics at import beside the family's summary tables.
  - A full Mendelian table for a callset of one file per sample: the table counts only sites where all three members have a call, and such a callset holds a member's call only where that member's file has a record, so a site where a parent's file has none is not counted.
- **Long-read packages** (PROD-4): no dataset type reads a plain multi-sample Sniffles2 VCF, and QDNAseq-style raw-bin CNV files are not importable.
- **Per-sample GLIMPSE2 haplotypes** (PROD-5): per-sample GLIMPSE2 BCF haplotypes are registered but not imported.
- **Single-parent recessive risk** (PROD-6): it reads "uninformative" by design; a later version could say "carries the known parent's risk allele, donor unknown".
- **Haplogroup comparison** (PROD-7): REQ-MITO-003 has no automated haplogroup comparison; the analyst compares the haplogroups CoGA lists, and the owner decides whether that stays a manual review step (TF-09b §3).
- **More than one PGT region** (PROD-9): a PGT family has one ROI, and the signed record's PGT section holds one, so two loci need its format extended after the RC.
