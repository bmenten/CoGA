# Exploratory family polygenic scores

Change classification: **minor** — a separate, backward-compatible exploratory module.
It adds no clinical classification, interpretation, report section or sign-out content.
These raw scores cannot be compared as risks, percentiles or ancestry-adjusted values.

## Administrator workflow

Open **Administration → Reference data → Exploratory PGS**. The shortlist is a list of
candidates, not a set of downloaded or validated models:

`PGS005091`, `PGS000013`, `PGS000024`, `PGS012555`, `PGS004930`, `PGS000004`,
`PGS000662`, `PGS000310`, `PGS002804`, `PGS003724`, `PGS000327`, `PGS002790`.

Use the link to the public [PGS Catalog](https://www.pgscatalog.org/) to find a score, then
enter its PGS ID and choose **Import GRCh38 from Catalog**. Any valid Catalog PGS ID may be
imported; the shortlist is only a set of suggestions. A durable background job downloads
the official harmonized GRCh38 `.txt.gz` (a gzip-compressed TSV) and its complete score API
metadata. No local search engine or copy of the Catalog search interface is maintained.
Only the public PGS ID leaves CoGA; no family, sample, genotype or caller credentials are sent.

CoGA preserves the full scoring text, file headers, study/publication, DOI/PMID, traits,
ancestry descriptions and development/training cohorts, method, licence, release dates,
original/harmonized builds, URLs, retrieval time and compressed/API/weight checksums.
The Catalog's original build may be GRCh37 while the imported harmonized coordinates are
GRCh38. Metadata remains descriptive; ancestry descriptions never adjust scores.

A complete valid download is saved even when reference resolution or model support is
missing. Its version is marked `needs_reference` or `unsupported`, with the reason visible
in administration. No subset of its variants is published as a usable score. A `ready`
version must contain every declared weight, enforced in Postgres as well as by the parser.
Configure the indexed GRCh38 FASTA once for files without explicit REF and import the ID
again to append a prepared version. An unsupported newer version blocks selection of that
model; CoGA never silently falls back to an older version.

Consecutive imports of unchanged metadata/weights/reference preparation reuse the current
version. New contents or a Catalog rollback to earlier contents append a current revision;
previous weights/results remain immutable. Importing a model does not queue family scores.
Check the displayed licence before using a score. Manual TSV/TSV.gz upload remains available
with an explicit version, study and HTTPS source URL; invalid manual files are refused.
No approximate, partial, local liftover or strand-complement matching is applied.

Supported columns: `chr_name`, `chr_position`, `effect_allele`, `effect_weight`, optional
`other_allele` and `ref_allele`. Coordinates must be explicitly declared by
`#genome_build=GRCh38`. Harmonized Catalog files instead use `hm_chr`, `hm_pos` and
`#HmPOS_build=GRCh38`. Only additive autosomal SNV models with finite weights and one weight
per position are supported. Odds ratios are not automatically log-transformed. The complete
declared `variants_number`, if present, must match. Haplotypes, interactions and non-additive
models are refused.

Every locus needs an explicit `ref_allele`, or set `PGS_REFERENCE_FASTA` to an indexed GRCh38
FASTA with an existing `.fai`. The server never creates that index. It resolves and freezes
REF at publication; when both sources exist they must agree. `other_allele` is **never**
assumed to be REF, and may differ from REF. FASTA and index checksums are retained.

New versions append weights and metadata. The SHA-256 of the decompressed UTF-8 scoring
text and a separate SHA-256 of resolved weights identify the exact model. A duplicate
version label returns 409. Publishing a version queues no retrospective work. Archiving
stops selection for future requests; reactivation makes its latest version selectable.
Already queued jobs retain their pinned versions, and must still be ready for scoring. All historical versions and values
remain visible, including when a model is archived.

## Full-WGS import contract

Add this explicit declaration to a family package's manifest:

```yaml
metadata:
  pgs:
    full_wgs: true
    genome_build: GRCh38
    absent_record_is_hom_ref: true
datasets:
  snv:
    family_vcf: snv/family.vcf.gz
    source_format: clair3
```

`clair3` denotes CoGA's direct-call storage source (also used for DeepVariant). The joint
VCF must contain GT calls on GRCh38, use autosome names `1`–`22` or `chr1`–`chr22`, and use
exact CoGA sample IDs as its sample columns. Per-sample sources and imputed/GLIMPSE2 inputs
are not eligible. Set `absent_record_is_hom_ref: false` to abstain at absent loci instead.
The declaration is an explicit assumption about absent records; it is not evidence that
each position is callable. It must not be used for exomes, panels, region-filtered VCFs or
VCFs stripped of no-call/filtered records. For sources without that guarantee, use `false`.

Only an entirely successful, real import that imported the SNV dataset registers a WGS
source and queues the active model versions. A dry run, skipped or metadata-only SNV
dataset, failed import or stopped import never creates a usable WGS source. The original
file is hashed before and after import. PGS registration failure is recorded in the
import log without changing the clinical import's outcome. Keep the complete original
package available: local package paths stay local; remote sources retain their original
package URI and are staged again for calculation, with existing import-root checks.

## Calculation and family workflow

Open **Exploratory PGS** in the family's analysis navigation, within one selected project.
The page shows new/uncalculated versions and offers **Calculate active models for this
family** to analysts and administrators. Readers can see records but cannot calculate;
users outside the project receive the same 404 as an unknown record. Administrators alone
may manage models. Each selected model version is pinned when a run is queued.

The calculation streams the full original VCF, rather than the display-filtered variant
store. For each locus and individual, `raw_score = sum(effect_weight × GT effect-allele
count)`. Phased and unphased diploid GT are equivalent; REF can be the effect allele.
Multiallelic records are resolved by allele base and GT index, with all alleles checked
against the model. DS and genotype probabilities are ignored.

| Situation | QC outcome |
| --- | --- |
| Passing, compatible diploid GT | `called` |
| Record absent, sample exists in header, explicit WGS assumption true | `assumed_ref`, with effect dosage 2 for a REF effect allele and 0 otherwise |
| Record absent, assumption false | `missing_record` |
| Sample column/call missing | `missing_sample`, including absent loci for a missing header sample |
| GT missing, explicit or partial no-call | `no_call` |
| Non-PASS record or failing sample FT | `filtered`; site FILTER `.` also abstains |
| Wrong REF, incompatible/symbolic alleles | `allele_mismatch` |
| Haploid/polyploid, malformed or out-of-range GT | `invalid_gt` |
| More than one record at a model position, including split records | `ambiguous_record` |

Each result retains all QC counts, including the exact `assumed_ref` count. If any locus
is unusable, the result is `incomplete` with `raw_score = null`; no partial sum is presented
as a full score. No-calls and other failed calls never enter the assumed-REF path.

History records the study, PGS ID, version, build, original/resolved weight checksums,
reference provenance, import job, WGS location and checksum, family genotype-storage
version, calculation version (`coga-pgs-gt-v1`) and exact assumptions. The page retains
scores from earlier imports and model versions, marking historical imports separately.

## Integrity and interrupted tasks

Sources, model versions (including Catalog metadata) and results are append-only, with
mutation-blocking triggers and restricted-role grants. The HTTP audit and global UI telemetry cover the new controls and routes. None of
the PGS tables participates in the clinical sign-out snapshot or clinical interpretation.

Runs are durable Postgres jobs, processed by a lifespan-managed worker. A run holds its
row lock and shared family variant-write locks through an atomic transaction that publishes
all results and marks the run completed. Import/upload/delete writers cannot interleave
with scoring. The worker checks the completed import, unfinished/failed-import flags,
current source, family members, project rights and genotype-storage version before and
after scoring. The WGS checksum is checked before and after scanning. Source stat changes
or deletion are also shown on the family page; a fresh checksum check remains mandatory
at calculation. A second storage fingerprint (record count, sum and XOR of genotype-row
hashes) detects direct genotype row changes or deletions even without a version-token bump.
Unavailable genotype storage blocks calculation while keeping Postgres history visible.

A cancelled worker rolls back every result and marks its run interrupted. A crashed worker
releases its database lock; after ten minutes an unlocked stale running job is marked
interrupted. A live calculation's row lock prevents it being taken for crashed. Interrupted
and failed jobs are never automatically retried; the page shows the reason and lets the
user request a new run. Deleted/deactivated requesters or revoked project rights prevent
publication. Storage changes/deletion preserve historical values but require a new successful
full-WGS import before another calculation.

## Verification and references

`backend/tests/test_pgs_scoring.py` exercises the arithmetic and every REF/QC boundary.
`backend/tests/e2e/test_e2e_family_pgs.py` runs real HTTP model management, a real background
full-WGS import, score/QC publication, manual version recalculation, history, archive/
reactivation, project access, cancellation and invalidation against Postgres and ClickHouse.
The frontend tests and `frontend/e2e/pgs.spec.ts` verify the actual controls and browser flow
using synthetic inputs only. See [testing.md](testing.md) for suite commands.

The conceptual reference is the [PGS Catalog Calculator](https://github.com/pgscatalog/pgsc_calc)
and its [documentation](https://pgsc-calc.readthedocs.io/en/latest/). CoGA does not invoke
`pgsc_calc` or claim pipeline equivalence. This implementation deliberately uses its own
explicit full-WGS absence contract; DS/imputed scoring, reference-population normalization
and ancestry correction are deferred.

## Local verification record — 2026-11-01

Verified from local `main` at `64631e0e`, on `feat/family-pgs`. Test execution used a copy
of tracked repository files, installed frontend dependencies, Python 3.12 in a temporary
container, and separate temporary Postgres 16 and ClickHouse 26.8 databases. No ignored
family/patient-data folders were mounted into the test environment. Backend and frontend
suites ran sequentially.

| Check | Result |
| --- | --- |
| Backend unit suite (`python -m pytest -q`) | 6,930 passed; 1,187 datastore-dependent tests skipped in this unit run |
| Final PGS scoring units, including zero-weight checksum normalization | 36 passed; scoring-module line coverage 92% |
| Integration suite (`RUN_INTEGRATION=1`, separate run) | 273 passed, 9 skipped (optional GCS emulator/analyzer conditions) |
| Full backend end-to-end suite (`RUN_INTEGRATION=1`, separate clean databases) | 905 passed |
| PGS service lifecycle coverage | 82% in the real synthetic HTTP/import lifecycle |
| Frontend (`vitest run --coverage`) | 204 test files, 2,518 tests passed; all coverage gates passed (lines 89.39%, statements 88.1%, branches 78.76%, functions 85.73%) |
| New admin/family page line coverage | 97.5% / 94.73% |
| Chromium PGS browser journey against production frontend/CSP and real backend worker | 1 passed; model publication, full-WGS import, automatic score/QC, pending version notice, manual recalculation, provenance, archive/history/reactivation |
| Ruff / mypy / TypeScript / ESLint | Passed; mypy checked 53 source files |
| Generated API types / test catalogue / handleiding sync | Passed |
| Frontend production dependency audit | Passed with registry access; no high/critical findings |

The synthetic arithmetic oracle is independent of the scorer: at one observed A/G site,
the effect G has weight 0.5; at an absent site, REF C is the effect allele with weight -1.
With the explicit full-WGS assumption, the heterozygous proband scores -1.5 and the G/G
mother -1. A father's explicit no-call yields `raw_score = null`, with one `no_call` and
one `assumed_ref`, rather than a fabricated REF score. A second version changes the first
weight to 1 and produces -1 / 0 for proband/mother only after manual calculation; the
original scores stay in history. Real database tests verify mutation-blocking grants and
triggers, cancellation, orphan-run recovery, revoked project access, dry runs, failed
imports, source changes/deletion, direct genotype row deletion without a version bump,
unavailable genotype storage, and exclusion from the clinical report snapshot.

## Catalog import integrity

`pgs_catalog_imports` records queued/running/completed/failed/interrupted acquisition jobs.
The existing PGS worker processes them before family calculations. A running import keeps
its row lock until the complete model version and completed job commit together; failed,
cancelled or unauthorized jobs publish no partial versions. The requester must remain an
active administrator at execution and publication. Unlocked running jobs older than ten
minutes become interrupted, while a live row lock prevents false recovery; retry is manual.
Duplicate pending requests for one PGS ID share a job.

Metadata is fetched only from `https://www.pgscatalog.org/rest/score/<PGS ID>/`. Downloads
must match the exact official EBI GRCh38 path for that ID; redirects, alternate hosts,
credentials, ports and query strings are refused. Transfer size, decompressed size and
time are bounded. Gzip integrity, PGS ID, harmonized build, column structure and full
variant counts are checked before publication. Tests use synthetic HTTP responses and
synthetic weights, including IDs outside the shortlist; no live Catalog or patient data
is embedded in fixtures.


## Catalog import verification — 2026-11-01

Follow-up to the initial PGS module: administration now links to the public Catalog and
imports by PGS ID, without replicating Catalog search. Verification used the same isolated
tracked-source copy, temporary databases and synthetic family data described above.

| Check | Result |
| --- | --- |
| Backend unit suite | 6,952 passed; 1,188 datastore-dependent tests skipped in this unit run |
| Focused migration and restricted-role integration tests | 31 passed, including upgrade from 0013 with historical weights unchanged, with frozen and pre-RC baselines |
| Focused real backend PGS and Catalog lifecycle tests | 2 passed; real Postgres/ClickHouse and HTTP routes, with synthetic upstream Catalog responses |
| Catalog acquisition / PGS lifecycle line coverage | 90% / 82% in the focused lifecycle run |
| Frontend with coverage | 204 files, 2,519 tests passed; lines 89.4%, statements 88.11%, branches 78.78%, functions 85.76% |
| Chromium browser journeys | 2 passed: full real family import/scoring/history flow; Catalog ID form, automatic model refresh, metadata and reference status using synthetic API responses |
| Ruff / mypy / TypeScript / ESLint | Passed; mypy checked 54 source files |
| Generated API types / test catalogue / handleiding sync | Passed |
| Frontend production dependency audit | Passed with registry access; no high/critical findings |

The Catalog lifecycle test covers complete weights and metadata, IDs outside the shortlist,
unchanged-import reuse, updated weights and a Catalog rollback, reference preparation,
unsupported models, failed downloads, revoked administrator rights before/after preparation,
cancellation, locked/live and orphan jobs, and absence of retrospective scoring jobs.
The full integration and backend end-to-end suites recorded above were not rerun for this
follow-up; the focused suites exercise the changed migration and PGS paths.

A separate read-only public format check validated the official PGS000004 score API response
and harmonized GRCh38 file (313 rows). No public score was installed into family data or
committed as a fixture. The complete model includes unsupported variant types and lacks
explicit REF; acquisition and readiness are deliberately separate so these properties cannot
produce a silently reduced or incorrectly oriented score.
