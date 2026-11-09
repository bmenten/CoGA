# Closed issues

The open-issue IDs that the technical file (`docs/regulatory/`) cites and that are no longer open,
so an auditor can still look one up. Each row says what the issue was when the list last
described it, and what closed it. A change is named by its commit on `main` and its date. `git
show <commit>` gives the change and its description. Open issues are in
[open-issues.md](open-issues.md), and the roadmap's items in [ROADMAP.md](ROADMAP.md).

When the technical file cites an ID that is no longer open, the ID gets a row here.
`test_regulatory_docs_cite_no_pr_numbers.py` refuses a cited ID that is neither open nor listed
here.

## Clinical (CLIN)

| ID | What it was | What closed it |
|---|---|---|
| CLIN-6 | An X-linked recessive embryo call rested on an unconfirmed assumption: that an unaffected father's X is not at risk when a female embryo is classified. | The owner confirmed the rule for a fully penetrant X-linked recessive disorder; TF-09a REQ-PGT-005 records it (2026-10-14, `cc0c5be4`). |
| CLIN-7 | The evidence snapshot did not hold the frequencies and in-silico scores a classification rested on. | A classification freezes its frequencies and in-silico scores and names the one that moved (2026-10-19, `b138e934`); also the consequence, the LoF call and the gene's missense Z (2026-10-20, `9c5551b9`). |
| CLIN-8 | A sign-out refusal reason did not name its sample. | Each Sample-QC refusal reason names its sample, pair or child (2026-10-14, `dd7139f6`). |
| CLIN-13 | The NIPT per-sample SNV file's one column was read unchecked, since NIPT manifests map tubes to files named after other tubes. | Discover, the dry run and the import warn when the column is named neither as its sample nor in its entry (2026-10-28, `9be5a68f`). |
| CLIN-16 | A genotype written as a word ("HET") was read three different ways by three helpers. | Such a genotype is refused at import, and genotypes are read one way (2026-10-28, `6192ac26`). |
| CLIN-22 | The built-in repeat thresholds disagreed with STRchive's at eleven loci, so a call depended on whether the pipeline's TRID was a gene name or a STRchive ID. | A repeat allele is called the same under either TRID (2026-10-09, `1ecb0a00`). |
| CLIN-25 | The SV length filters measured END − POS, so they dropped insertions and translocations. | The filters measure SVLEN, and a translocation passes any minimum (2026-10-13, `6cc717de`). |
| CLIN-26 | A call at a repeat locus the catalogue gives no thresholds read normal. | Such a call reads not assessed (2026-10-13, `ac415b38`). |
| CLIN-27 | A sub-panel's ranking could be served from a broader ranking made before the panel's genes changed. | A sub-panel is served from a broader ranking only while its panel is unchanged (2026-10-13, `ba8c7aec`). |
| CLIN-28 | The SNV + SV second-hit badge let any cis verdict win. | Each SV of a gene is phased on its own, so a deletion in trans wins the badge (2026-10-13, `ec756b7f`). |
| CLIN-29 | The Variant Explorer applied the annotation filters per variant, not per transcript. | The Explorer applies them per transcript, as the family search does (2026-10-13, `edbec6f5`). |
| CLIN-31 | The repeat catalogue took other STRchive releases than the shipped one, and read a gene with several tracts at one. | The catalogue is built from the shipped STRchive release only, and a gene's tract is chosen by the call's position (2026-10-20, `da93a709`). |
| CLIN-33 | An uploaded SV without an END covered one base. | An SV record without END or SVTYPE is read as VCF 4.4 defines them (2026-10-13, `63f45cb2`). |
| CLIN-34 | A sub-panel served from the Mendeliome's ranking was checked on gene symbols only. | A sub-panel is served from a broader ranking only as its own computation ranks it (2026-10-13, `612dccb0`). |
| CLIN-35 | A sub-panel served from a broader ranking kept that ranking's numbers and compound-het pairs. | As CLIN-34 (2026-10-13, `612dccb0`). |
| CLIN-36 | Two STRchive loci without thresholds read not assessed in every family. | A count inside a stated benign range reads normal at a locus without thresholds (2026-10-20, `c2f97920`). |
| CLIN-37 | A breakend without SVTYPE was stored with its ALT as its type. | As CLIN-33 (2026-10-13, `63f45cb2`). |
| CLIN-40 | A NeedlR breakend was stored without its mate. | A NeedlR breakend is stored with the mate its ALT names (2026-10-13, `5be998fa`). |
| CLIN-43 | A NeedlR SV was named by its record ID, which repeats across a trio's per-sample files. | A NeedlR SV is named by its coordinates, never its record ID: the owner's choice (2026-10-13, `e9bee283`). |
| CLIN-44 | Ranking by phenotype could change which compound-het, recessive and carrier-screening results the search kept. | The ranking orders what the search keeps and changes none of them (2026-10-13, `93ad19d1`). |
| CLIN-45 | SIFT and PolyPhen filters read the score in one place and the prediction in another, so two views kept different variants. | Both are read by their prediction, in SQL and Python alike (2026-10-13, `2a2130c0`). |
| CLIN-46 | The coverage readers took every caller's track as read depth. | Only a track of read depth is read as depth, one per sample (2026-10-13, `ff59186d`). |
| CLIN-47 | The SV ranking's deletion types missed DEL:ME and CNV losses. | An SV is scored by the copy change the second hit reads (2026-10-13, `9d1bb223`). |
| CLIN-48 | The Variant Explorer's panel filter read only a panel's genes, not its regions. | The Explorer applies a panel's regions as the family page does (2026-10-13, `0db26987`). |
| CLIN-51 | A variant card could show another transcript's SIFT and PolyPhen than the one the filter matched. | The card names the transcript the filters held on beside the one it shows (2026-10-13, `52a60ea1`). |
| CLIN-54 | The genome SV tracks drew only five exact SV types. | They draw every SV the list shows: a CNV by its copy change, an unknown type as Other (2026-10-14, `cad412ab`). |
| CLIN-56 | The mtDNA coverage counted each half-open interval one base too wide, and named its source after the first rows' record IDs. | Each row counts the bases it covers, and the track names where it came from (2026-10-21, `eea4eb4a`). |
| CLIN-58 | The sign-out dialog could hide a Sample-QC check that could not run. | The dialog shows those checks beside a failure (2026-10-20, `78c176f4`). |
| CLIN-59 | The callset's own allele count was read as gnomAD's. | gnomAD's counts are read only from gnomAD's own fields (2026-10-20, `3e652e9e`). |
| CLIN-62 | Whether a member's small-variant track was offered ignored a panel's genes. | The track is offered under a panel exactly when the list shows that member a variant (2026-10-20, `18924faf`). |
| CLIN-66 | Stored haplotype blocks were not rebuilt after the pedigree edits they rest on. | No embryo is called, and no risk state is drawn, from blocks built for another pedigree (2026-10-27, `72d9d100` and `c21c4363`). |
| CLIN-67 | gnomAD's homozygote and hemizygote counts under VEP's prefixes were not read. | gnomAD's exome and genome counts are read; BS2 stays unmarked without a count, and a count filter says what it kept (2026-10-27, `8e62f023`). |

## Data (DATA)

| ID | What it was | What closed it |
|---|---|---|
| DATA-4 | A bgzip file cut exactly at a block boundary read as complete, and unusable upstream reference data was taken in. | Unusable upstream data answers 502 and the loaded Monarch release stays (2026-10-21, `5be03940`); a BGZF file without its end-of-file block is refused (2026-10-28, `05b66302`). |
| DATA-7 | Values read from files were stored unchecked against their columns: allele fractions, the common-variant flag, TRGT values and Paraphase copy numbers. | Allele fractions are stored all or none and common variants flagged by stored frequencies (2026-10-21, `1118ec08`); a TRGT value that is no number is refused before anything is written (2026-10-27, `1dff56f9`); a Paraphase copy number no integer column holds is refused before any sample's results are replaced (2026-10-27, `e7ebe250`). |
| DATA-9 | A refused family TRGT VCF's message named the samples but not the file, and a refused per-target coverage table's named the file but not the sample. | Both name the file and the sample (2026-10-28, `36da63de`). |
| DATA-10 | A manifest ROI that did not resolve was ignored without a word, and the earlier ROI, which the PGT calls read, stayed. | The import clears and reports such an ROI: the owner's decision (2026-11-04, `6bc19d4d`). |
| DATA-11 | Discover and the validation read a corrupt per-sample `.vcf.gz` unguarded. | Such a file is refused by name (2026-11-04, `a81becb8`). |

## Engineering (ENG)

| ID | What it was | What closed it |
|---|---|---|
| ENG-1 | The small-variant filters existed twice, in SQL and in Python. | Both kept the same variants, with a parity test over every filter field (2026-10-14, `bcabe7e7`); since 2026-11-06 the SQL is the only definition (`1353b80f`). |
| ENG-2 | The SV list and its CSV export declared the same filters twice. | Both read their filters through one dependency (2026-10-20, `a2b86fd1`). |
| ENG-4 | API leftovers: fields no code set or read, duplicate models, totals named after categories. | Removed (2026-10-28, `8611b476` and `27e64874`; 2026-11-04, `9119f16a`). |
| ENG-6 | Columns no code read; class labels and VUS tiers not in sentence case. | The columns were dropped (2026-10-28, `d5ca49bf`); class labels and VUS tiers are written in sentence case, with a migration (2026-11-04, `be5fdb83`). The owner accepted the migration without an audit event. |
| ENG-12 | Four family pages did not wait for the project's reference, and a limit on a metric that had left the catalogue could not be removed. | Both fixed (2026-10-26, `0b51d5a4`). |
| ENG-13 | The ranking cache's background warm computed queries without their intervals and exclusions, and read the pedigree before its structure hash. | The warm prepares a view as a live request does and signs the pedigree the ranking read (2026-10-26, `016d6c41`). |
| ENG-15 | Applying a large panel cost rows × panel size. | A panel's regions are joined to the family's rows on windows (2026-10-19, `580b61ef`). |
| ENG-19 | Leftovers of one review round: the Sample QC's NIPT step outside a savepoint, a zero-length haplotype block at a stretch's end, re-created families' structure versions named alike, and several frontend points. | Fixed (2026-10-27, `a425ef6c` and `4a1406d8`). What remained was left as designed: the panel track-availability query plans slowly in small windows, and a track may be offered that the list's other filters leave empty, never the reverse. |
| ENG-20 | The Circos SV tooltip and the ROI marker review named chromosomes otherwise than the viewer. | Both name them as the viewer does (2026-10-28, `4029a746` and `3a13427a`; 2026-11-04, `eadb6ef8`). |

## Operations (OPS)

| ID | What it was | What closed it |
|---|---|---|
| OPS-8 | Version identity and release mechanics were not defined. | Only a release build reports a release's version; release tags must be on `main`; a deploy runs images by digest; a release attaches its SBOMs (2026-10-20, `1e12a42d`). |
| OPS-10 | Secrets were kept in the Terraform state. | The owner password and the ClickHouse TLS keys stay out of the state (2026-10-20, `aa223c0e`). |
| OPS-11 | The lock check did not compare the lock files' header, and code outside the type-check gate had mypy errors. | The floored modules are type-checked (2026-10-27, `6a11c841`); CI checks the backend locks are what `compile-requirements.sh` compiles (2026-10-28, `b7591823`); `click` is pinned so the header is stable (2026-11-04, `95b4e5df`); the mypy errors outside the gate are cleared (2026-11-04, `fe2c30b2`). |
| OPS-25 | TF-13 S-7 said the backend lock files are reproduced in Docker, which nothing verified. | TF-13 S-7 says the lock's reproducibility is not verified (2026-10-26, `20a76f5a`); see OPS-11 for the check added later. |

## Regulatory (REG)

| ID | What it was | What closed it |
|---|---|---|
| REG-2 | What the signed record holds per application: the PGT embryo calls, the NIPT results and the variant descriptions were not signed. | The owner set the device boundary at the frozen analysis, with the clinical report signed in the EPD (2026-10-14, `cc0c5be4`). The signed record freezes a PGT family's embryo calls (2026-10-20, `d39f6ecc`) and the NIPT analysis (2026-10-20, `a6836c5d`); the technical file says so (2026-10-27, `40382cd6`). |

## Data safety (SAFE)

| ID | What it was | What closed it |
|---|---|---|
| SAFE-3 | A package dataset's files were checked only as it wrote, after it had replaced what they replace. | Every embryo's APCAD file and every sample's coverage file are checked before any track is replaced (2026-10-27, `c9417294`), and every sample's file before a dataset writes (2026-10-27, `1dff56f9`). Left as designed: a long-read SNV file out of position order is found while streaming, and the overwrite snapshot restores the callset (a pre-scan would double the longest import). |
| SAFE-14 | Three family pages showed their data without the warning banners when the family record failed to load. | No family data is shown without the family record, and the page says when it failed (2026-10-13, `71619162`). |
| SAFE-15 | Nothing stopped an import before its heartbeat was ten minutes old, an import whose heartbeat never reached the database again kept running, and nothing checked the connection holding the variant-write locks. | An import stops itself when its heartbeat cannot reach the database or its locks' connection is lost (2026-10-27, `dc3a31a8`). |
| SAFE-17 | Some import-incomplete flags could never clear. | The flag keeps each failed scope, and an `auto` SNV import reads its own callset (2026-10-20, `8b357029`). |
| SAFE-18 | Import bookkeeping: the job's dataset table and the flag disagreed, a failed restore's leftovers were not recorded, and updates of imputed genotypes read no stored haplotype blocks. | Fixed (2026-10-26, `897c9aeb` and `62619b8f`). |
| SAFE-22 | A track loader replaced every source's track of a sample, not only its own, and replaced a BED before reading it. | A loader replaces only its own source's track, after reading the BED (2026-10-13, `2d35da83`). |
| SAFE-23 | Two `auto` SNV imports could strike each other off the whole-family import entry. | An `auto` import is recorded by the callset it stores (2026-10-13, `d7317167`). |
| SAFE-25 | A small-variant upload with overwrite deleted the callset before it read the file. | The upload reads the file whole first, and flags a write that fails after the delete (2026-10-13, `461c7971`). |
| SAFE-26 | The import-incomplete flag kept one scope per dataset. | As SAFE-17 (2026-10-20, `8b357029`). |
| SAFE-27 | An `auto` SNV import's update check and clean-up assumed direct calls. | As SAFE-17 (2026-10-20, `8b357029`). |
| SAFE-28 | A write that failed or stopped after its delete left the family unmarked. | A failed SV rewrite puts back what it deleted, and an upload marks the family before its first ClickHouse write (2026-10-20, `72296ce2`). |
| SAFE-29 | Loaders accepted a file with no records after deleting what it replaced. | A file without a record is refused before it replaces anything (2026-10-19, `2ad366e8`). |
| SAFE-30 | A value beyond its ClickHouse column failed the whole import. | Such a value is stored as missing, and the depth columns are wider (2026-10-14, `159fb9e2`). |
| SAFE-31 | Admin deletions and a pedigree replacement could leave data missing without marking the family. | A deletion marks the family before its first ClickHouse write (2026-10-20, `e783ba0e`). |
| SAFE-32 | A refused SNV or haplotype file could still cost its callset. | Only what the refused file's loader replaced is cleaned up (2026-10-20, `e783ba0e`). |
| SAFE-35 | A deletion removed kept raw files before it committed, and a replaced family's interval tracks stayed. | Kept files are unlinked only after the deletion commits, and a replaced family's interval tracks are erased (2026-10-27, `3d111d97`). |

## Security (SEC)

| ID | What it was | What closed it |
|---|---|---|
| SEC-1 | Any project member could change a family's phenotypes through the API. | A reader level per project membership; phenotype edits by analysts, audited (2026-10-20, `d655a363`). |
| SEC-4 | Accounts and sessions: Microsoft sign-in locked users out. | Sign-in with Microsoft, one emergency administrator, roles set in the UI (2026-10-20, `4485f4bf`, with the revision `04800cbf`). |
| SEC-7 | Gaps in what the clinical audit trail and the append-only rule covered. | Pair reviews, pedigree changes and family deletions are clinical audit events, and the structure versions are chained (2026-10-20, `09f37b57`). |
| SEC-9 | A record outside the caller's projects could be told from an unknown one: another user's preset and import job, a gene profile. | They answer as unknown ones (2026-10-21, `e5a9526d`). The residual, a taken family or sample ID answering 409 on create, is documented and accepted (2026-10-28, `5d2708ad`). |
| SEC-11 | The sign-up notification logged the new account's address and the mail server's error text. | It logs neither (2026-10-21, `6e63a6ce`). |
| SEC-15 | Family and sample IDs: invisible characters, `/` and `..` were accepted, IDs were not encoded in links, and parent IDs were quoted raw. | IDs are encoded in in-app links and download names (2026-10-27, `eee2a7ac`); an ID with a character a reader cannot see, or that is a path, is refused: the owner's decision (2026-10-28, `c02f67bc`); parent and carrier IDs are quoted with unseen characters escaped (2026-11-04, `6aca0216`). |
| SEC-17 | The request audit: a row could be stored twice, and a row naming a user deleted while it waited was lost. | Each row and UI event is stored once, and the row of a deleted user is kept (2026-10-27, `76bdbd1b`). The request audit is append-only but not hash-chained, by design (`security-posture.md`). |
| SEC-20 | The alignment file lookup could reach another family's folder. | A sample's reads are looked up only in its own family's folders (2026-10-13, `9c17f373`). |
| SEC-25 | The signal-track lookup checked only that a path stays in the data directory. | A sample's signal tracks and QC report are looked up only in its own family's folders (2026-10-13, `521b70bd`). |
| SEC-27 | Package-path residuals: a folder named like a PED taken for one, the staging root read for every import, links in a package's folders, the manifest written through a link. | Fixed (2026-10-27, `26bd381c`; 2026-10-28, `3c92875f`; 2026-11-04, `0a2af26b`). |
| SEC-29 | The raw-file download, verify and purge trusted the recorded storage path. | A recorded raw file is read and deleted only in CoGA's own folders (2026-10-14, `08556532`). |
| SEC-32 | Sign-in: an administrator could deactivate their own account, and import jobs and review presets were keyed on an e-mail address that can change. | Both fixed (2026-10-21, `f8fc25e5`; 2026-10-27, `8b965b75`). |
| SEC-33 | `GET /api/users` gave every signed-in user the names and e-mail addresses of all active accounts. | It answers administrators only: the owner's decision (2026-10-28, `1c0bca43`). |
| SEC-35 | A queued import, gene-reference refresh or knowledgebase rebuild ran for a requester who was no longer an active administrator. | It runs only for an active administrator (2026-10-28, `d2c79cec`). |
| SEC-36 | The UI-event log: a failed page-close keepalive was not retried, tabs shared the kept store, and events were made with no session. | Fixed (2026-10-28, `d23cd552` and `28238602`). |

## Traceability (TRACE)

| ID | What it was | What closed it |
|---|---|---|
| TRACE-3 | The ClinGen recurrent-CNV file was recorded under another release than its file names. | It is recorded as ClinGen's V2.1 (2026-10-19, `5a833ec0`) and loaded only when its SHA-256 matches (2026-10-20, `7f2ee898`). |
| TRACE-7 | A depth track could be stored with raw depths under a log2-ratio label. | A depth track is recorded as read and kept off the log2-ratio axis (2026-10-13, `371afcf3`). |
| TRACE-8 | Tool versions in VCF headers: of several versions of one tool only the first was kept, and some program names were not folded into their tool. | Every version a header states is kept, and GLIMPSE, Manta and DRAGEN program names are folded into their tool (2026-10-26, `5bd374c6`). |
| TRACE-9 | What a coverage BED holds was not recorded. | Each coverage BED declares whether it holds read depth or a log2 ratio (2026-10-20, `8ccb6b21`). |
| TRACE-11 | A CNV classification could freeze another caller's evidence. | It freezes the evidence of the caller's row it was made on (2026-10-13, `18107a27`). |
| TRACE-12 | The drift banner and the signed view did not name the caller of a missing SV, and the CNV dialog and the SV table hid the caller. | All three name it (2026-10-21, `ca07ce7f`). |
| TRACE-13 | A family re-created under its ID failed its sign-out chain. | A family ID's sign-outs are numbered on across every family made under it (2026-10-20, `274b7f20`). |
| TRACE-14 | Some reference files were read without a checksum. | The segmental-duplication file, an HPO file in place and the ClinGen recurrent-CNV file are loaded only when their SHA-256 matches (2026-10-20, `7f2ee898`). |
| TRACE-15 | Evidence snapshots: the gene look-up matched symbols across assemblies, and a VCF's own `MISSENSE_Z` reached the evidence card unread. | A gene's constraint is read in the family's assembly (2026-10-21, `38bc1599`); PP2 is offered for review from the gene profile's missense Z, else the VCF's `MISSENSE_Z`, and the snapshot freezes which: the owner's decision (2026-10-28, `27c81d88`). |
| TRACE-17 | Where a package's APCAD BED and target tables came from was not recorded, and the NIPT coverage check's notes were frozen nowhere. | Both recorded (2026-10-21, `e8074653`); the notes are frozen in the signed record with what the check ran for (2026-10-28, `9da42b44`). |
| TRACE-18 | Some reference inputs were read without a pin. | The HPO sync reads its release from the file and keeps its SHA-256; ClinGen's GRCh37 recurrent-CNV file is pinned and a knowledgebase rebuild records every file it reads (2026-10-27, `dc3426b7` and `aa174ed3`). |
| TRACE-19 | "Sign out anyway" recomputed the checks, so a concern that arose after the dialog opened was acknowledged unseen. | A sign-out acknowledges exactly the concerns the dialog showed, by their fingerprint (2026-10-27, `90c7b380` and `0193d3b7`). |

## Interface (UI)

| ID | What it was | What closed it |
|---|---|---|
| UI-3 | After a session expired, signing in returned to the dashboard, not to the page, and a proxied redirect could leave the site. | Signing in returns to the page, and a proxied redirect stays on the site (2026-10-21, `e6b2c0ea`). |
| UI-4 | The NIPT page had no review-tag filter. | It has one, and lists what a sign-out freezes (2026-10-20, `f7bfd779`). |
