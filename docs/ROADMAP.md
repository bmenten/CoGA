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
- **Google Cloud go-live**: the switches that restrict access to institutional networks, run the API as the restricted database role and lock down ClickHouse egress ([deployment-gcp.md](deployment-gcp.md)); and a restore drill, as the backups configured in Terraform have never been restored.

## Engineering

- **Operations**: `/metrics`, its collector and the alert policies exist ([monitoring.md](monitoring.md)), but nothing is deployed until Terraform is first applied. The release candidate freezes the Postgres baselines; from then on each schema change is a revision ([database.md](database.md#freezing-the-baselines-at-the-release-candidate)).
- **Imports and scaling**:
  - A stopped import is noticed only when a worker next looks for work, once its job's heartbeat is ten minutes old. Until then its family's report cannot be signed out: sign-out is refused while a family's import job is queued, validating or running.
  - The worker then runs a job that was still validating again. One that was running (it had begun writing the family) it ends as interrupted, without running it again; the family stays marked import-incomplete until what that import had not finished is imported again with overwrite.
  - A live import whose heartbeat goes stale is taken for stopped the same way. It is stopped where it is once its heartbeat reaches the database again and finds its job ended; until then it may go on, but a restore it reaches deletes nothing without its backup.
  - A restore that fails part-way may have removed rows of any dataset, whichever import wrote them. Its flag names every dataset of its import as failed, those it imported too. Its import's entry stays, naming every dataset the family's imports and single-file uploads recorded (another small-variant source, another package's SV calls, a caller's SV calls an upload loaded). So the family stays marked until the whole family has been imported again with overwrite, and what an upload brought uploaded again with overwrite.
  - An import whose heartbeat has not reached the database for five minutes stops itself where it is, before a worker can take it for stopped. One whose variant-write locks' connection is lost is stopped where it is: nothing restored, its job ended failed.
  - An import whose job cannot record the family it imports writes nothing of it.
  - Watched import folders: a pipeline's package with its package file `coga-package.json` is checked and waits for a person to approve it ([data-import.md](data-import.md#watched-import-folders)). Still to come:
    - Watching the packages already imported: a change after the import is noticed, shown with what changed and noted on the family page (the delivery states `approved` and `changed_after_import` are reserved for it).
    - Optionally, a mail to the administrators when a delivery is ready.
    - An import without a person's approval, only by a separate decision of the owner.
    - The format's open points, to settle with the pipeline owners (REG-12).
  - Each backend container runs one uvicorn process, whose event loop the API shares with the refresh worker. With Docker Compose the imports run in import-worker processes of their own, one core each; on Google Cloud they still run in the API's instances.
- **Regression truth set**: no GIAB or GeT-RM truth set with a concordance harness for minor-release validation.
- **Request tracing** (SEC-16): nothing carries a request ID from a log line to its audit row; `/api/health/ready`, which also checks that Postgres and ClickHouse answer, is polled by nothing (the probes and the uptime check use `/api/health`; the deployment guide calls it by hand).

## Product

- **Variant Explorer** (PROD-2): gene-, transcript- and cohort-frequency views beside the variant view.
- **Variant summary statistics** (PROD-1): what the family's *Variant summary* still lacks ([#1](https://github.com/bmenten/CoGA/issues/1)):
  - A call of uniparental disomy or of runs of homozygosity. The page counts the inconsistencies and the homozygous share per chromosome but calls nothing; a call needs its own requirement and validation (REG-15).
  - Speed at genome scale: the statistics are counted on each visit. A synthetic trio of five million variants took about 6 s on a laptop's Docker, with up to 2 GiB for one query; a real genome on the real stack is not yet timed (OPS-9). If it proves slow, keep the statistics at import beside the family's summary tables.
  - A Mendelian table for a callset of one file per sample: a trio's calls then lie in different rows, so it has no sites.
- **Annotation coverage per callset** (PROD-10): CoGA cannot tell a variant absent from gnomAD from one the pipeline did not annotate with gnomAD, so PM2 is offered to consider, never applied, without a gnomAD frequency (and BP4/BP7 without a prediction). Recording, per callset, which sources its VCF header declares would let PM2 be applied where gnomAD was annotated.
- **Long-read packages** (PROD-4): no dataset type reads a plain multi-sample Sniffles2 VCF, and QDNAseq-style raw-bin CNV files are not importable.
- **Per-sample GLIMPSE2 haplotypes** (PROD-5): per-sample GLIMPSE2 BCF haplotypes are registered but not imported.
- **Single-parent recessive risk** (PROD-6): it reads "uninformative" by design; a later version could say "carries the known parent's risk allele, donor unknown".
- **Haplogroup comparison** (PROD-7): REQ-MITO-003 has no automated haplogroup comparison; the analyst compares the haplogroups CoGA lists, and the owner decides whether that stays a manual review step (TF-09b §3).
- **More than one PGT region** (PROD-9): a PGT family has one ROI, and the signed record's PGT section holds one, so two loci need its format extended after the RC.
- **Sort by the raw variant score** (ENG-9): the UI could offer novel-gene candidates sorted by the raw variant score.
