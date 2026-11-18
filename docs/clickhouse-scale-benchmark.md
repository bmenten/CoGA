# ClickHouse scale benchmark

Can CoGA hold and search 1,000 whole genomes? This page records how the ClickHouse small-variant
storage behaves as a synthetic cohort grows, where it fails, and what hardware 1,000 genomes need.
The benchmark is
[`scripts/benchmark_clickhouse_scale.py`](../scripts/benchmark_clickhouse_scale.py); every figure
below comes from its results, measured on 2 and 3 November 2026, the Variant Explorer's on
12 and 18 November 2026, the family search's on 18 November 2026. The family search tests its
per-transcript annotation filters on each `entries` row's own transcripts (`transcripts`), and
narrows the subqueries that still read the shared annotation tables to the variants the family
carries (`_small_family_key_condition`); this page calls that *the family-key bound*.

## Summary

- **Storage: about 340 GB of small variants for 1,000 genomes** (2.4 billion `entries` rows at
  124 B each, 41 B of them the row's own transcripts; 61 M distinct variants at about 0.8 KB
  each). The GCP deployment's default ClickHouse disk (`clickhouse_data_disk_gb = 200`) fills at
  about 600 genomes, before any room for merges.
- **The family search reads the family's rows, whatever the cohort's size.** It tests its
  frequency, impact, ClinVar and other per-transcript filters on each row's own transcripts: a
  rare search of a trio read 12.6 M rows at 50, 100 and 250 genomes alike, where reading the
  cohort's annotation table it read 198, 248 and 359 M, about 750 M at 1,000. It held 0.2–0.5 GiB
  and took 1–3 s here at every size.
- **The family-key bound stays where a subquery reads the shared tables:** a panel's genes, the
  rsID, a transcript or HGVS text. Without it, a 5,300-gene panel's set grew with the cohort
  (745 MiB at 250 genomes, 437 MiB with it), and the rare-only search, when it read the shared
  table, held 3 GiB at 250 genomes and passed the 110 s limit. A gene typed in the gene filter
  goes without it: 35 MiB, where the bound cost 240 MiB.
- **The Variant Explorer reads a carrier summary, not the families' rows:** per variant, what
  each family adds, kept as the families' variants are written
  ([database.md](database.md#the-variant-carrier-summary)). Counted from every `entries` row, its
  unfiltered page passed the 110 s limit from 60 M rows (100 genomes at a quarter of the variants
  each) and a one-gene page took 21 s at 150 M rows. From the summary, at 150 M rows: 1.7 s
  unfiltered, 1.3 s rare and HIGH/MODERATE, 0.6 s one gene. On a synthetic summary the size of
  1,000 genomes' (61 M variants) the unfiltered page takes about 8 s and holds under 200 MiB.
  Adding a family costs about 7 s for a quarter-size trio, so some 25 s for a whole-genome one,
  twice per write of the family (taken out, then added again).
- **A broad Explorer filter is read 50 Mb of the genome at a time.** Rare only keeps 52 M of the
  61 M variants of 1,000 genomes: gathered into one query's set, it passed the 4 GB a query may
  hold. Read window by window, its page takes 25–35 s and holds 320 MiB (6–9 s sorted by
  position), and the 5,300-gene panel's 150 MiB instead of 1.2 GiB in one query, in about the same
  time. A narrow filter stays one query.
- **An overwrite of a whole-genome trio takes about a minute:** snapshot and restore of 6.3 M rows,
  20–35 s and 1.5 GiB each, whatever the family's size. The snapshot runs under the 110 s query
  limit; on the loaded test machine one passed it. The delete rewrites the family's parts, which
  merges share with the rest of its project: 7.4 GB for one trio at 100 genomes in four projects,
  about 50 GB at 1,000.
- **The integrity check reads every byte of the variant tables:** 0.7–1.4 GB a second on a quiet
  machine, so 3 to 6 minutes every 6 hours at 1,000 genomes.
- **The storage writer writes about 2,600 records a second**, the import reading each annotation
  once and inserting synchronously. Writing a
  whole-genome trio takes about 40 minutes; 1,000 genomes take about 10 days of one core's work,
  shared by the import-worker processes (`IMPORT_WORKER_REPLICAS`, one core each).

## Hardware for 1,000 genomes

| Resource | Recommendation | Why |
| --- | --- | --- |
| Disk | a 1 TB SSD for the ClickHouse volume | about 340 GB of small variants measured; a merge writes its new part before it removes the old ones; an overwrite copies the family into backup tables; the SV, coverage and other interval tables come on top and are not measured here |
| Memory | 64 GB | the Variant Explorer's filtered pages read the cohort's annotation tables (about 25 GB compressed at 1,000 genomes), which then fit in the page cache; each query may hold 4 GB |
| CPU | 16 vCPU | the scans run in parallel; these measurements had 10 cores, shared with other work |

This is the "16 vCPU and 64 GB to test at scale" of [deployment-vm.md](deployment-vm.md), with a
larger disk. The family search reads the family's own rows, whatever the cohort's size; the
Variant Explorer's broad filters read the cohort's annotation tables (see "What would scale
better").

## Where it fails

| Operation | Fails at | How |
| --- | --- | --- |
| Overwrite of a whole-genome trio | on a loaded machine | the snapshot's copy of 6.3 M rows passed the 110 s limit |
| Delete or overwrite of a family | a project of more than about 70 GB of `entries`, some 350 genomes in one project (extrapolated) | the delete rewrites its project's parts at about 0.6 GB a second, past the client's 120 s read timeout (`CLICKHOUSE_SEND_RECEIVE_TIMEOUT`) |
| GCP default disk (200 GB) | about 600 genomes | full |

## Results

### Hardware and runs

One Apple M4 (10 cores) with Docker Desktop: a Linux VM of 10 CPUs and 7.75 GiB, shared with other
projects' containers. ClickHouse capped each query at 4 GB (`memory-guards.xml`) and the whole
server at 4.3–4.8 GB, from the memory the VM had free. The machine was busy: the host's load
average was 9–12 on its 10 cores throughout, and during the 250-genome step the load inside the VM
reached 600 with its swap full. Memory and rows read are therefore the measures to trust. Times
show the order of magnitude and are inflated at the 250-genome step: a one-gene search that took
0.3 s at 100 genomes took up to 32 s there. Pass D repeated that step once the other containers had
stopped (host load about 7). Pass F ran on a quieter machine still (host load about 5).

The disk had 30 to 60 GB free, not the 340 GB of 1,000 genomes, so the benchmark ran in
passes:

| Pass | What it holds | Steps |
| --- | --- | --- |
| A | every table, every family, at a quarter of the variants per genome (`--variant-scale 0.25`) | 10, 20 and 40 genomes |
| B | the cohort's annotation tables at full size, `entries` only for the measured families (`--entries probes --skip-details`) | 50, 100 and 250 genomes |
| C | every family, at a quarter of the variants per genome, without `variants/details` | 100 genomes |
| D | as B, on a quieter machine, with the family-key bound only | 250 genomes |
| E | the annotation tables at full size and a carrier summary of their size written from the variant pools, no family's rows (`--entries probes --skip-details --summary synthetic`) | 1,000 genomes |
| F | as B, each `entries` row with its own transcripts; on 18 November 2026, at a host load of about 5, the dev stack's containers idle beside it | 50, 100 and 250 genomes |

Pass B grows exactly what the family search's annotation filters read; passes A and C grow what the
Variant Explorer, the overwrite and the integrity check read. The family-key bound changes the
family search only. The query code measured:

- pass B: each step with the query code at `3ff4c84b`, which has the family-key bound, and again
  with the code at `64631e0e`, which does not;
- pass A: its last step with both;
- pass C: the code with the family-key bound;
- pass E: the Variant Explorer at `3c780455`, which reads every filter in one query, and with
  the code that reads a broad filter window by window.
- pass F: each step with the code that tests the family search's per-transcript filters on
  the rows' own transcripts, and again with the code at `5c6a8645`, which tests them on the
  shared `variants/annotations` table with the family-key bound. Its family searches come from
  a run of them alone (`--operations family`), with the other measurements from a full run.

A 500-genome step of pass B was dropped: with every broad query passing the time limit on the
overloaded machine, it would have measured the machine, not the code. Pass C was cut to one step
without the annotation JSON: with 3.4 GB left to ClickHouse by then, the JSON insert ran out of
memory (that run wrote the table in larger blocks than the script does).

### Storage

At full size (pass B, and pass A for `variants/details`):

| Table | On disk per row | Rows at 1,000 genomes | At 1,000 genomes |
| --- | ---: | ---: | ---: |
| `entries` | 124 B | 2.37 billion | 294 GB |
| `variants/details` | 318 B | 61 M | 19 GB |
| `variants/annotations` | 40 B | 363 M | 15 GB |
| `variants/annotation_index` | 146 B | 61 M | 9 GB |
| `variants/gene_index` | 35 B | 60 M | 2 GB |
| **Total** | | | **about 340 GB** |

The largest columns are `annotationsJson` (85 % of `variants/details`), `hgvsc` and
`annotationHash` in `variants/annotations`, `hgvsc_values` in `variants/annotation_index`, and
`transcripts` (41 B a row, compressed with ZSTD), `rsid`, `variantId` and `calls.ad` in
`entries`. The rows' transcripts hold 1.8 distinct tuples of filtered values a row, against the
5.9 transcripts of a variant; the transcript and HGVS text, which they leave out, would add
92 B a row. The `entries` figure is pass F's (pass B measured 82 B a row without the
transcripts). The family summaries and data versions are negligible.

### Family search

Pass F, the trio (6.3 M `entries` rows) and the singleton (4.8 M). Memory is the largest of the
search's queries; rows read are the list and the count together; time is the median of three runs.
The code at `5c6a8645` tests the per-transcript filters on the shared `variants/annotations`
table, with the family-key bound; the code measured tests them on the rows' own transcripts:

| Search | Genomes (distinct variants) | On the shared table: memory, rows read, time | On the rows' own transcripts: memory, rows read, time |
| --- | --- | --- | --- |
| Rare only, trio | 50 (14.5 M) | 583 MiB, 198 M, 2.7 s | 523 MiB, 12.6 M, 2.2 s |
| | 100 (18.7 M) | 568 MiB, 248 M, 2.5 s | 526 MiB, 12.6 M, 2.4 s |
| | 250 (28.1 M) | 571 MiB, 359 M, 4.2 s | 507 MiB, 12.6 M, 3.1 s |
| Rare only, singleton | 250 | 244 MiB, 353 M, 2.5 s | 237 MiB, 9.6 M, 1.2 s |
| Rare, HIGH/MODERATE, trio | 50 | 247 MiB, 198 M, 1.1 s | 195 MiB, 12.6 M, 1.2 s |
| | 250 | 244 MiB, 359 M, 1.7 s | 166 MiB, 12.6 M, 1.7 s |
| Panel (5,300 genes), trio | 50 | 437 MiB, 52 M, 3.1 s | 458 MiB, 52 M, 2.9 s |
| | 250 | 436 MiB, 65 M, 2.6 s | 437 MiB, 65 M, 3.0 s |
| Panel, rare, HIGH/MODERATE, trio | 250 | 308 MiB, 412 M, 4.3 s | 305 MiB, 65 M, 2.9 s |
| Panel, rare, HIGH/MODERATE, singleton | 250 | 307 MiB, 400 M, 3.2 s | 305 MiB, 56 M, 2.7 s |
| One gene, trio | 50 | 244 MiB, 25 M, 0.4 s | 35 MiB, 12.7 M, 0.2 s |
| | 250 | 244 MiB, 25 M, 0.5 s | 35 MiB, 12.7 M, 0.2 s |

Every search kept the same variants and the same count both ways.

- Read on the rows' own transcripts, a search with a frequency, impact or ClinVar filter reads the
  family's rows and nothing else: 12.6 M rows for the trio, its 6.3 M rows in the list and again
  in the count, at every size. On the shared table it read the cohort's whole
  `variants/annotations` table twice, growing by the six transcript rows of each new distinct
  variant: about 750 M rows at 1,000 genomes, where the rows' own transcripts still read 12.6 M.
- Those filters build no set: the memory is the list's own, and it does not grow with the cohort.
- The one-gene search reads the gene index by gene (its sort key) without the family-key bound:
  35 MiB and half the rows, where the bound held a set of the family's keys, 240 MiB.
- The panel's genes keep the bound. Without it, the set of the cohort's variants in 5,300 genes
  grew with the cohort: a run of the trio's panel search held 513, 536 and 745 MiB at 50, 100 and
  250 genomes; with it, 437 to 458 MiB at every size. Its rows read grow a little with the cohort
  (52 to 65 M): the gene index's rows of the panel's genes.
- The rsID and a transcript or HGVS text still read the shared annotation index or table, with the
  bound; this benchmark does not measure them. Pass B measured why the bound is there: on the
  shared table without it, the rare-only search gathered the cohort's matching variants into one
  set, 1.0, 1.6 and 3.0 GiB at 50, 100 and 250 genomes, and passed the 110 s limit (ClickHouse
  code 159, answered as HTTP 422) at 250.

### Variant Explorer

The Variant Explorer counts, per variant, the families and samples of the user's projects that
carry it. It reads them from the carrier summary (`SNV_INDEL/carriers`,
[database.md](database.md#the-variant-carrier-summary)): one row per variant, set of projects and
class of rows (annotation set, rsID, gene symbols, imputed or not), summed over the families, and
a row per carrier where a family's rows of a variant fall in several classes or a sample is shown
by two families. A page groups those rows per variant in the table's order, so it holds one
variant's sums at a time, and reads what the number of distinct variants makes, not the number of
genomes.

Before, a page grouped every carrier of every `entries` row (`uniqExact` of families and samples
per key), spilling past 2 GiB. Measured with the code before the summary, on a cohort at a quarter
of the variants per genome (every family's `entries` rows, no `variants/details`), one run each:

| Genomes | `entries` rows | Unfiltered | Rare, HIGH/MODERATE | One gene |
| ---: | ---: | --- | --- | --- |
| 10 | 5.9 M | 8.1 s, 2.5 GiB | 0.9 s | 0.7 s |
| 40 | 23.9 M | 32 s, 2.5 GiB | 3.9 s | 2.6 s |
| 100 | 59.9 M | passed the 110 s limit, 3.4 GiB | 10.4 s | 7.7 s |
| 250 | 150.2 M | passed the 110 s limit | 31 s | 21 s |

The same 250-genome cohort (7.0 M distinct variants) with the summary, the median of three runs:

| Page | Summary as merges leave it (5 parts, 26 M rows, 0.9 GiB) | Right after 100 families were added at once (102 parts, 153 M rows) |
| --- | --- | --- |
| Unfiltered | 1.7 s, 224 MiB, 47 M rows read | 21 s, 527 MiB |
| Rare, HIGH/MODERATE | 1.3 s, 41 MiB | 14 s, 266 MiB |
| One gene | 0.6 s, 21 MiB | 7 s, 95 MiB |

- **Merges.** A family's contribution is attached as one part; ClickHouse's background merges then
  sum the families' rows into a few parts. Adding the 100 families at once left 102 parts, which
  merged in 218 s; until then a page read every family's part. In normal use families are added
  one at a time, and each part is merged within minutes.
- **The filtered pages** read the cohort's annotation tables for their annotation filters
  (`(key, annotationSetHash) IN (…)`), and the summary instead of `entries`: 200 M rows for the
  rare and HIGH/MODERATE page, against 405 M before. That reading still grows with the cohort
  (below, "What would scale better"), and a broad filter is read window by window (below).
- **At 1,000 genomes.** A synthetic summary of 61 M variants (9 M common ones carried in each of
  four projects, 52 M rare ones in one), 88 M rows merged into 4 parts and 2.9 GB, read by the
  app's own queries (median of three): the unfiltered page took 8.4 s sorted by carriers, 8.1 s
  ascending and 7.9 s by position, at most 186 MiB; the bounded count 0.1 s. Grouped without the
  table's order the page held 2 GiB; with `FINAL` on an unmerged summary it was far slower (152 s
  at 250 genomes), so the page reads without it.
- **Keeping it.** Adding a family reads its rows once (`FINAL`, in their order), writes its rows to
  a staging table and merges them into one part: 6.9 s for a quarter-size trio and 3.7 s for a
  singleton, at most 0.6 GiB, whatever the cohort's size. A whole-genome trio has four times the
  rows: about 25 s. A write of a family pays it twice: its contribution is taken out before the
  write (6.5 s for the trio) and added again after it (7.7 s), against some 40 minutes for the
  import of a whole-genome trio.
- **Its size.** 0.9 GiB merged for 150 M `entries` rows (11.8 GiB): about 3 GB at 1,000 genomes.

#### Broad filters

A filter read by a subquery (an annotation filter, the gene panel, the rsID, a sample's genotype)
gathers the variants it keeps, across the cohort, into a set its query holds whole. At 1,000
genomes (pass E: 61 M variants, a summary of 88 M rows in 7 parts, 3.2 GiB) rare only keeps
52 M variants, and the Mendeliome-like panel of 5,300 genes 5.05 M in each of its two sets (its
regions and its genes). A page therefore asks in one query whose sets may each hold two million
variants (`max_rows_in_set`, two million at about 75 B each); past that ClickHouse refuses the
query, within a second, and the page reads the summary one window of 50 Mb at a time
(`ExplorerWindow` in
[variant_explorer_service.py](../backend/app/services/variant_explorer_service.py)): 71 windows,
four at once, each window's set holding its own variants (860 k for rare only on chr1:50–100 Mb),
its annotation subqueries reading the window through the annotation tables' sort key. The count
stops once the windows pass its cap; a page sorted by position reads the windows in order until
it is full; a page sorted by a count keeps the first rows of every window. Two runs each, on the
shared machine:

| Page at 1,000 genomes | In one query | Window by window |
| --- | --- | --- |
| Rare only, by carriers | passed the 4 GB a query may hold after 45 s (3.6 GiB) | 25–35 s, 320 MiB, 899 M rows read; longest query 7 s |
| Rare only, by position | passed the 4 GB, as above | 6–9 s, 300 MiB, 464 M rows |
| The panel of 5,300 genes, by carriers | 20–26 s, 1.2 GiB, 492 M rows (sets allowed 6 M) | 23–35 s, 150 MiB, 891 M rows |

- **The limit.** Allowed six million variants a set, the panel stays one query, holding 1.2 GiB,
  and takes as long as window by window; rare only then fails after 2 s at 430 MiB before it is
  read window by window. Two million keeps every query of a page under about 320 MiB at this size.
- **The windows' cost** is one reading of the summary and of the annotation tables a page, as
  one query's would be; the panel's gene index is read again in each window (its 5,300 genes lie
  in half of the index's granules), and the windows' extent, a reading of the summary's `xpos` and
  `chrom`, takes about a second.
- **Narrow filters** stay one query: rare and HIGH/MODERATE 9.5–9.7 s, 152 MiB; one gene
  4.0–4.5 s, 21 MiB; unfiltered 7.5–8.0 s, 416 MiB. A page's columns (ID, rsID, gene symbols) are
  read for its variants alone, each filter's subquery bound to their keys: 1.2 billion rows read
  for the rare and HIGH/MODERATE page, against 1.4 billion with the subqueries gathering the
  cohort's variants a third time.

### Overwrite of a family

An overwrite import snapshots the family's rows (`INSERT … SELECT` into backup tables), deletes them
(`ALTER TABLE … DELETE … SETTINGS mutations_sync = 1`), writes the new rows and drops the snapshot.
A failed import restores the snapshot (delete, then `INSERT … SELECT` back). The benchmark runs the
snapshot, the delete and the restore of one trio:

| Data | Trio's rows | Snapshot | Delete | Restore |
| --- | ---: | --- | --- | --- |
| Pass A, 40 genomes (quarter size) | 1.6 M | 6–7 s, 1.4–1.5 GiB | 0.5–1.9 s; rewrote the parts that held it, 2.7 M rows | 7–9 s, 1.4–1.5 GiB |
| Pass B, 50 and 100 genomes (whole genome) | 6.3 M | 20–44 s, 1.5 GiB | 0.1–0.7 s; its parts held no other family | 25–60 s, 1.5 GiB |
| Pass B, 250 genomes, loaded | 6.3 M | passed the 110 s limit after 3.7 M rows | | |
| Pass D, 250 genomes, quieter machine | 6.3 M | 19 s, 1.5 GiB | 0.05 s; its parts held no other family | 22 s, 1.5 GiB |
| Pass C, 100 genomes (quarter size, ten families per project) | 1.6 M | 5.2 s, 1.3 GiB | 12.5 s; rewrote the parts that held it: 14.3 M rows and 7.4 GB, nearly all of its project's 15 M | 7.3 s, 1.5 GiB |
| Pass F, 50, 100 and 250 genomes, the rows with their transcripts | 6.3 M | 17–20 s, 1.6 GiB | 15–16 s | 18–21 s, 1.6 GiB |

The snapshot and the restore stream the rows in insert blocks, so their memory stays at 1.5 GiB
whatever the family's size. They copy each row's transcripts with it: in pass F the code before
them took the same time and memory, within a run's spread. Their time grows with the family: 20–35 s for a whole-genome trio on
this machine, a fifth to a third of the 110 s query limit they run under.

The delete rewrites every part that holds a row of the family. A part holds one project's rows
(`PARTITION BY project_guid`), and merges join the parts of that project's families, so the
delete's cost grows with the project's size, not the family's. In pass C it rewrote 7.4 GB in
12.5 s, about 0.6 GB a second. At 1,000 genomes in four projects a project holds about 50 GB of
`entries`, so one delete may take over a minute, and the restore of a failed import deletes again.
The delete waits for its mutation (`mutations_sync = 1`) within the client's 120 s read timeout: at
that rate a project of more than about 70 GB, some 350 genomes, would pass it.

### Integrity check

The 6-hourly check (`check_clickhouse_variant_integrity`) runs `CHECK TABLE` on every variant table,
which reads and checksums every part, then counts the distinct keys of `gene_index` and
`annotation_index`:

| Data | On disk | Time | Largest memory |
| --- | ---: | --- | ---: |
| Pass A, 40 genomes | 4.6 GB | 5.3–6.9 s | 141 MiB |
| Pass B, 50 genomes | 7.6 GB | 6.5–7.6 s | 527 MiB |
| Pass B, 100 genomes | 9.4 GB | 11–14 s | 652 MiB |
| Pass B, 250 genomes, loaded | 13.2 GB | 32–59 s | 515 MiB |
| Pass D, 250 genomes, quieter machine | 13.2 GB | 9.7 s | 652 MiB |
| Pass F, 250 genomes, the rows with their transcripts | 14.4 GB | 10–13 s | 782 MiB |

It reads 0.7 to 1.4 GB a second on a quiet machine, so at 1,000 genomes (340 GB) it takes about 4
to 8 minutes, and longer under load (0.2 GB a second at the 250-genome step: about 20 minutes). The
key counts held at most 0.65 GiB at 19–28 M distinct variants, so 1 to 2 GiB at 61 M. It never
failed here, but it reads the whole data disk four times a day.

### Writing the rows

The storage writer (`insert_small_variant_records`), on trio records with six transcripts each,
wrote 2,605 records a second (20,000 records in 7.7 s, on the loaded machine), the import reading
each annotation once and inserting synchronously. That is about 40 minutes for the 6.3 M records of a
whole-genome trio, and some 10 days of one core for the 2.4 billion of 1,000 genomes. The
[import-worker processes](application-scheme.md#import-worker-processes) share that work, each on a
core of its own ([application-scheme.md](application-scheme.md#small-variant-import) measures the
whole import).

Building and writing each row's transcripts costs the writer about 5 %: on 18 November, on a
quieter machine, the same check wrote a median of 4,490 records a second with them and 4,720
with the code before them (six runs each, alternating).

The writer writes a variant's shared rows (details, annotations and indexes) again for every family
that carries it; ReplacingMergeTree folds them together when parts merge. The tables above are the
merged size: at 1,000 genomes the shared tables take in about 40 times more rows than they keep.
This benchmark generates each shared row once and does not measure that merge load.

## What would scale better

- **The family search's rsID, transcript and HGVS filters** still read the cohort's annotation
  index or table, narrowed to the family's keys: the row's own transcripts hold no text, and the
  rsID filter also finds the rsID another import of the variant wrote.
- **The family's variant statistics** read each variant's consequences from the cohort's
  `annotation_index`, for the family's keys; the rows' own `transcripts` hold them.
- **The Variant Explorer's broad filters** read the cohort's annotation tables once a page, window
  by window (363 M transcript rows for rare only at 1,000 genomes), and a large panel's gene-index
  rows once a window. Keeping the values the filters test on the summary's rows would make a
  page's cost that of the variants it keeps.
- **A Variant Explorer page's annotation display** reads its 50 variants by key through the
  annotation tables' bloom-filter index: 140 M of the 363 M transcript rows at 1,000 genomes.
  Their positions would narrow it to the granules that hold them.
- **A family's delete** rewrites the parts it shares with the rest of its project; partitioning
  `entries` finer than by project would bound what one delete rewrites.
- **The import** writes each variant's shared rows once per family; writing only the variants the
  shared tables lack would cut the writes and the merges.
- **The GCP deployment's ClickHouse disk** (200 GB) and VM (`e2-standard-4`) are below what 1,000
  genomes need.

## Method

### The cohort

The script builds the cohort in a throwaway ClickHouse container: the image of
`docker-compose.yml`, with
[`clickhouse/users.d/memory-guards.xml`](../clickhouse/users.d/memory-guards.xml) mounted, so every
query is capped at 4 GB as in production. It refuses a server that holds any database but its own.
The app's own DDL (`ensure_clickhouse_variant_tables`) creates the tables.

Families are added in steps, three trios for every singleton: 1,000 genomes are 300 trios and 100
singletons, 700 unrelated founders, spread over four projects. Every value is synthetic
(`FAM-0001`, `PROBAND-0001`, `MOTHER-0001`, `FATHER1-0001`); no file from `data/` and no other
server is read.

Each genome carries about 4.8 M small variants (86 % SNVs):

- **Common variants** (gnomAD AF over 1 %): a pool of 9.1 M sites, each with a population
  frequency. A founder carries each one with Hardy–Weinberg probabilities: about 4.35 M per genome.
- **Rare variants** (gnomAD AF at most 1 %, or absent from gnomAD): 450,000 per genome. The first
  *n* founders carry `450,000 · n^0.725` distinct ones between them, so each founder adds new ones
  and shares the rest with earlier founders. The curve puts about 61 M distinct variants in 700
  unrelated genomes, in line with the 1000 Genomes Project's 30x callset (about 125 M for 3,202
  genomes) and gnomAD v3 (about 760 M for 76,000).
- A trio's child takes one allele of each parent: always a homozygous parent's, a heterozygous
  parent's with probability one half. A trio stores about 6.3 M `entries` rows (every variant any
  member carries, with all three calls), a singleton about 4.8 M.

ClickHouse itself generates the rows (`INSERT … SELECT` over a numbers table), with the columns the
storage writer derives (`clickhouse_variant_rows._small_variant_entry_rows`):

| Table | Per distinct variant |
| --- | --- |
| `variants/details` | one row; `annotationsJson` holds one object per transcript with the keys the VCF annotation parser stores, about 3.8 KB |
| `variants/annotations` | one row per transcript, 5.9 on average: 5–22 for a variant in a protein-coding gene, 2–9 in a lncRNA, 1–2 between genes |
| `variants/annotation_index` | one row |
| `variants/gene_index` | two rows (symbol and gene id) for a variant in a gene, none otherwise |
| `entries` | one row per family that carries it, with the members' calls and the variant's transcripts (the filtered values of its `variants/annotations` rows, each distinct tuple once) |

Half the genome lies in one of 40,000 genes, every other one protein coding. 3 % of the positions in
a protein-coding gene are coding (missense, synonymous, stop-gained, frameshift, in-frame), with
splice and UTR variants beside them. The panel search uses a Mendeliome-like panel of 5,300
protein-coding genes with their loci, as the family page reads a panel (genes and regions). The
variant key is `sipHash64` of the writer's key text, not BLAKE2b: ClickHouse has no BLAKE2b, and a
key only has to be one 64-bit hash per variant.

The writer itself (`insert_small_variant_records`) is too slow to build a cohort (about 10 days for
1,000 genomes, see [Writing the rows](#writing-the-rows)), so it is measured once per run.

### What is measured

After each step's merges have settled, the script calls the app's own functions, each with a
`log_comment`, and reads duration, memory and rows read back from `system.query_log`:

| Operation | The app's code |
| --- | --- |
| Family search: list and count, for a trio and a singleton | `_fetch_small_variant_rows` (page of 50) and `_count_small_variant_rows_bounded`, the two queries of `_small_variants_native_page` |
| — rare and HIGH/MODERATE impact | `max_gnomad_af = 0.01`, `impact = [HIGH, MODERATE]` |
| — rare only | `max_gnomad_af = 0.01` |
| — panel | the 5,300-gene panel, no other filter |
| — panel, rare and HIGH/MODERATE impact | both together |
| — one gene | `gene = SG20000` |
| Variant Explorer, unfiltered, rare and HIGH/MODERATE impact, one gene; rare only, by carriers and by position; the 5,300-gene panel | `search_global_small_variants`, every project in scope, once the carrier summary's merges have settled; its Postgres parts (scope, members shown, families pending, panel, review filter, reviews shown) stubbed |
| The carrier summary: each family added, one trio taken out and added again | the reconciler's refresh of one family (`variant_carrier_summary._refresh_family`) and the writers' hook (`detach_family_variant_carriers`), the Postgres parts (projects, members, locks) stubbed |
| Overwrite of one trio | `snapshot_family_clickhouse_state`, `delete_family_small_variants`, `restore_family_clickhouse_state`, `discard_family_clickhouse_snapshot`, and the parts the mutations rewrote (`system.part_log`) |
| Integrity check | `check_clickhouse_variant_integrity`, as the 6-hourly monitor runs it |
| Storage | rows, parts and bytes on disk per table (`system.parts`) |

The searches run three times; the tables give the median time and the largest memory of any of
their queries. The query settings are the app's (110 s time limit, spill to disk past 2 GiB).

## Running it

On a machine with Docker and the backend's environment
(`pip install -r backend/requirements-dev.txt`):

```bash
python scripts/benchmark_clickhouse_scale.py --genomes 50,250,500,1000 --results /tmp/coga-scale.json
```

It writes the JSON results and a Markdown summary beside them, and removes the container when it
ends. Options:

- `--variant-scale` shrinks every variant pool by one factor, to fit a small disk.
- `--entries probes` stores `entries` rows only for the three families it measures, and
  `--skip-details` leaves `variants/details` empty, to test the cohort-wide annotation tables alone.
- The script stops before a step that would leave less than `--min-free-gb` free.
- `--operations` runs some of the measurements alone: `family`, `explorer` (with the carrier
  summary), `overwrite` and `integrity`.
- `--summary synthetic` writes the carrier summary straight from the variant pools (each common
  variant in every project, each rare one in one, with made-up counts) instead of from the
  families' rows: a summary the size of the cohort's, with `--entries probes`.
- To compare two versions of the query code on one dataset,
  `--compare-backend-dir <other checkout>/backend` measures every step a second time with the other
  checkout's code, as pass B did. Or keep the container (`--keep-container`) and measure later with
  `--reuse-container --measure-only --backend-dir <other checkout>/backend`.
