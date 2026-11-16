# ClickHouse scale benchmark

Can CoGA hold and search 1,000 whole genomes? This page records how the ClickHouse small-variant
storage behaves as a synthetic cohort grows, where it fails, and what hardware 1,000 genomes need.
The benchmark is
[`scripts/benchmark_clickhouse_scale.py`](../scripts/benchmark_clickhouse_scale.py); every figure
below comes from its results, measured on 2 and 3 November 2026, the Variant Explorer's on
12 November 2026. The family
search's annotation filters are narrowed to the variants the family carries
(`_small_family_key_condition`); this page calls that *the family-key bound*, and measures the
search with and without it.

## Summary

- **Storage: about 240 GB of small variants for 1,000 genomes** (2.4 billion `entries` rows at
  82 B each; 61 M distinct variants at about 0.8 KB each). The GCP deployment's default ClickHouse
  disk (`clickhouse_data_disk_gb = 200`) fills at about 800 genomes, before any room for merges.
- **The family search runs at every size measured, but slows as the cohort grows.** Its annotation filters still read the whole cohort's annotation table twice per
  search (list and count): 360 M rows at 250 genomes, about 750 M at 1,000. On a quiet machine a
  broad search took 2.4–4.8 s at 250 genomes; at 1,000 it will take about 5–10 s, and it keeps
  growing with the cohort.
- **The family-key bound holds the set of matching variants to the family's own:** 0.55 GiB for
  a trio at every step. Left out, the rare-only search held 3 GiB for that set at about 250 genomes
  (75 % of the 4 GB cap) and passed the 110 s limit, which the app answers with "This filter
  combination matches too many variants".
- **The family-key bound costs every narrow search about 240 MiB:** a one-gene search held
  240 MiB and took 1.1 s with it, 26 MiB and 0.4 s with the bound left out; a rare and
  HIGH/MODERATE search 240 MiB with it, 100 MiB without it.
- **The Variant Explorer reads a carrier summary, not the families' rows:** per variant, what
  each family adds, kept as the families' variants are written
  ([database.md](database.md#the-variant-carrier-summary)). Counted from every `entries` row, its
  unfiltered page passed the 110 s limit from 60 M rows (100 genomes at a quarter of the variants
  each) and a one-gene page took 21 s at 150 M rows. From the summary, at 150 M rows: 1.7 s
  unfiltered, 1.3 s rare and HIGH/MODERATE, 0.6 s one gene. On a synthetic summary the size of
  1,000 genomes' (61 M variants) the unfiltered page takes about 8 s and holds under 200 MiB.
  Adding a family costs about 7 s for a quarter-size trio, so some 25 s for a whole-genome one,
  twice per write of the family (taken out, then added again).
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
| Disk | a 500 GB SSD for the ClickHouse volume at least, 1 TB to grow | 240 GB of small variants measured; a merge writes its new part before it removes the old ones; an overwrite copies the family into backup tables; the SV, coverage and other interval tables come on top and are not measured here |
| Memory | 64 GB | every filtered family search reads the cohort's annotation tables (about 25 GB compressed at 1,000 genomes), which then fit in the page cache; each query may hold 4 GB |
| CPU | 16 vCPU | the scans run in parallel; these measurements had 10 cores, shared with other work |

This is the "16 vCPU and 64 GB to test at scale" of [deployment-vm.md](deployment-vm.md), with a
larger disk. Hardware does not fix the growth of the family search's annotation scans with the
cohort: that needs code changes (see "What would scale better").

## Where it fails

| Operation | Fails at | How |
| --- | --- | --- |
| Family search | not within 250 genomes | rows read grow with the cohort: about 750 M per broad search at 1,000 genomes, about 5–10 s here |
| Overwrite of a whole-genome trio | on a loaded machine | the snapshot's copy of 6.3 M rows passed the 110 s limit |
| Delete or overwrite of a family | a project of more than about 70 GB of `entries`, some 350 genomes in one project (extrapolated) | the delete rewrites its project's parts at about 0.6 GB a second, past the client's 120 s read timeout (`CLICKHOUSE_SEND_RECEIVE_TIMEOUT`) |
| GCP default disk (200 GB) | about 800 genomes | full |

## Results

### Hardware and runs

One Apple M4 (10 cores) with Docker Desktop: a Linux VM of 10 CPUs and 7.75 GiB, shared with other
projects' containers. ClickHouse capped each query at 4 GB (`memory-guards.xml`) and the whole
server at 4.3–4.8 GB, from the memory the VM had free. The machine was busy: the host's load
average was 9–12 on its 10 cores throughout, and during the 250-genome step the load inside the VM
reached 600 with its swap full. Memory and rows read are therefore the measures to trust. Times
show the order of magnitude and are inflated at the 250-genome step: a one-gene search that took
0.3 s at 100 genomes took up to 32 s there. Pass D repeated that step once the other containers had
stopped (host load about 7); read its times for the family search at scale.

The disk had 30 to 50 GB free, not the 240 GB of 1,000 genomes, so the benchmark ran in four
passes:

| Pass | What it holds | Steps |
| --- | --- | --- |
| A | every table, every family, at a quarter of the variants per genome (`--variant-scale 0.25`) | 10, 20 and 40 genomes |
| B | the cohort's annotation tables at full size, `entries` only for the measured families (`--entries probes --skip-details`) | 50, 100 and 250 genomes |
| C | every family, at a quarter of the variants per genome, without `variants/details` | 100 genomes |
| D | as B, on a quieter machine, with the family-key bound only | 250 genomes |

Pass B grows exactly what the family search's annotation filters read; passes A and C grow what the
Variant Explorer, the overwrite and the integrity check read. The family-key bound changes the
family search only. The query code measured:

- pass B: each step with the query code at `3ff4c84b`, which has the family-key bound, and again
  with the code at `64631e0e`, which does not;
- pass A: its last step with both;
- pass C: the code with the family-key bound.

A 500-genome step of pass B was dropped: with every broad query passing the time limit on the
overloaded machine, it would have measured the machine, not the code. Pass C was cut to one step
without the annotation JSON: with 3.4 GB left to ClickHouse by then, the JSON insert ran out of
memory (that run wrote the table in larger blocks than the script does).

### Storage

At full size (pass B, and pass A for `variants/details`):

| Table | On disk per row | Rows at 1,000 genomes | At 1,000 genomes |
| --- | ---: | ---: | ---: |
| `entries` | 82 B | 2.37 billion | 194 GB |
| `variants/details` | 318 B | 61 M | 19 GB |
| `variants/annotations` | 40 B | 363 M | 15 GB |
| `variants/annotation_index` | 146 B | 61 M | 9 GB |
| `variants/gene_index` | 35 B | 60 M | 2 GB |
| **Total** | | | **about 240 GB** |

The largest columns are `annotationsJson` (85 % of `variants/details`), `hgvsc` and
`annotationHash` in `variants/annotations`, `hgvsc_values` in `variants/annotation_index`, and
`rsid`, `variantId` and `calls.ad` in `entries`. The family summaries and data versions are
negligible.

### Family search

Pass B, the trio (6.3 M `entries` rows) and the singleton (4.8 M). Memory is the largest of the
search's queries; rows read are the list and the count together; time is the median of three runs
on the loaded machine:

| Search | Genomes (distinct variants) | Without the family-key bound: memory, rows read, time | With the family-key bound: memory, rows read, time |
| --- | --- | --- | --- |
| Rare only, trio | 50 (14.5 M) | 1.0 GiB, 185 M, 11 s | 584 MiB, 198 M, 5 s |
| | 100 (18.7 M) | 1.6 GiB, 235 M, 15 s | 582 MiB, 248 M, 12 s |
| | 250 (28.1 M) | 3.0 GiB, 288 M, **failed 2 of 3** | 551 MiB, 359 M, 22 s |
| Rare only, singleton | 250 | 3.0 GiB, **failed 3 of 3** | 238 MiB, 353 M, 13 s |
| Rare, HIGH/MODERATE, trio | 50 | 95 MiB, 185 M, 1.4 s | 245 MiB, 198 M, 2.4 s |
| | 250 | 100 MiB, 346 M, 6 s | 239 MiB, 359 M, 48 s |
| Panel (5,300 genes), trio | 50 | 544 MiB, 39 M, 4.5 s | 463 MiB, 52 M, 4.4 s |
| | 250 | 814 MiB, 53 M, 21 s | 502 MiB, 65 M, 22 s |
| Panel, rare, HIGH/MODERATE, singleton | 250 | 700 MiB, 184 M, **failed 3 of 3** | 338 MiB, 399 M, 24 s |
| One gene, trio | 50 | 26 MiB, 13 M, 0.4 s | 248 MiB, 25 M, 1.1 s |
| | 250 | 26 MiB, 13 M, 1.8 s | 239 MiB, 25 M, 3.3 s |

- Every search with a frequency, impact or ClinVar filter reads the cohort's whole
  `variants/annotations` table in its list query and again in its count query. The rows read grow
  by the six transcript rows of each new distinct variant, with and without the family-key bound:
  the bound limits the set those rows build, not the reading.
- The panel and one-gene searches read the `gene_index` by gene first (its sort key), so they read
  little of the cohort. With the family-key bound they also read the family's keys, twice: most
  of the 240 MiB and the extra rows.
- The 250-genome failures are the 110 s limit (ClickHouse code 159), answered as HTTP 422. The
  rare-only set grew by about 0.15 GiB per million distinct variants (1.0, 1.6 and 3.0 GiB). Had the
  time limit not stopped it first, it would have passed the 4 GB cap at about 33 M distinct
  variants, some 350 genomes.

On the quieter machine (pass D, 250 genomes, with the family-key bound) the same searches held the same memory,
read the same rows, and took:

| Search | Trio | Singleton |
| --- | --- | --- |
| Rare only | 4.8 s, 359 M rows | 2.4 s, 353 M rows |
| Rare, HIGH/MODERATE | 2.5 s | 1.2 s |
| Panel (5,300 genes) | 4.2 s | 2.2 s |
| Panel, rare, HIGH/MODERATE | 4.2 s, 412 M rows | 3.3 s |
| One gene | 0.8 s | 0.6 s |

That is 75–150 M rows a second. At 1,000 genomes a broad search reads about 750 M rows, so it takes
about 5–10 s on this machine: twice its time at 250 genomes, and growing with every genome added.
With the family-key bound, the scan, not the memory, limits the family search.

At smaller scale (pass A, 3.4 M distinct variants) every search took under 2.2 s, and the
family-key bound already held the rare-only memory of a quarter-size trio to 216 MiB, against
326 MiB without it.

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
- **The filtered pages** read the cohort's annotation tables for their annotation filters, as the
  family search does (`(key, annotationSetHash) IN (…)`), and the summary instead of `entries`:
  200 M rows for the rare and HIGH/MODERATE page, against 405 M before. That reading still grows
  with the cohort, as the family search's does (below, "What would scale better").
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

The snapshot and the restore stream the rows in insert blocks, so their memory stays at 1.5 GiB
whatever the family's size. Their time grows with the family: 20–35 s for a whole-genome trio on
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

It reads 0.7 to 1.4 GB a second on a quiet machine, so at 1,000 genomes (240 GB) it takes about 3
to 6 minutes, and longer under load (0.2 GB a second at the 250-genome step: about 20 minutes). The
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

The writer writes a variant's shared rows (details, annotations and indexes) again for every family
that carries it; ReplacingMergeTree folds them together when parts merge. The tables above are the
merged size: at 1,000 genomes the shared tables take in about 40 times more rows than they keep.
This benchmark generates each shared row once and does not measure that merge load.

## What would scale better

- **The family search's annotation filters** read the whole cohort's annotation table for every
  search. Reading only the annotations of the family's own keys, or keeping the values the filters
  test (frequencies, impacts, ClinVar) on the family's `entries` rows, would make a search's cost
  the family's alone.
- **The family-key bound** could be left out of a narrow subquery (a gene, a panel's genes), which
  reads little of the cohort without it.
- **The Variant Explorer's annotation filters** gather every matching variant of the cohort into
  the set their `IN` tests, as the family search's did before the family-key bound: a broad one
  (rare only) at 1,000 genomes is a set of tens of millions of variants. Reading the summary per
  chromosome and merging the pages would bound it.
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
| `entries` | one row per family that carries it, with the members' calls |

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
| Variant Explorer, unfiltered, rare and HIGH/MODERATE impact, one gene | `search_global_small_variants`, every project in scope, once the carrier summary's merges have settled; its Postgres parts (scope, members shown, families pending, review filter, reviews shown) stubbed |
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
- To compare two versions of the query code on one dataset,
  `--compare-backend-dir <other checkout>/backend` measures every step a second time with the other
  checkout's code, as pass B did. Or keep the container (`--keep-container`) and measure later with
  `--reuse-container --measure-only --backend-dir <other checkout>/backend`.
