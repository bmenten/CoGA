# Clinical traceability, sign-out and audit

A signed-out analysis must be reproducible and defensible: it has to say exactly what
produced it, and show who decided what and when. This page describes what CoGA records for
that, how sign-out works, how tampering is detected, and what is not covered. It is written for
developers and auditors. Lab users find the same in plain words in the in-app reference
([clinical-traceability](../frontend/src/content/docs/clinical-traceability.md), shown at
`/docs/reference/clinical-traceability`).

**What sign-out is.** CoGA's sign-out is the analyst's analytical sign-off: it closes the
analysis and freezes it into a signed version. It is not the medical sign-off. The clinical
report is drafted and medically signed in the hospital's electronic patient record (EPD),
outside the device, and refers 1:1 to the signed version it rests on: the device boundary is
*annotated VCF → closed, frozen analysis in CoGA; the clinical report is drafted and medically
signed in the EPD and refers 1:1 to that analysis*
([TF-02 §3](regulatory/TF-02-device-description.md)). The UI keeps the names *Sign out
report*, *signed version* and *signed report* for that frozen analysis.

Code comments refer to four parts by number: Phase 0 (the annotation manifest), Phase 1
(evidence snapshots and drift), Phase 2 (the clinical audit trail) and Phase 3 (sign-out).

## What is recorded

| Record | Where | What it holds |
| --- | --- | --- |
| Annotation manifest | `family_annotation_manifest` | per family, the versions of the tools and databases that produced its annotated input (VEP, ClinVar, gnomAD, dbNSFP, SpliceAI, callers, pipeline) |
| Evidence snapshot | `small_variant_reviews.acmg_evidence_snapshot`, `structural_variant_reviews.cnv_evidence_snapshot` | for each ACMG classification of a small variant, and each CNV (ClinGen) classification of a structural variant or CNV, what the classifier saw |
| Clinical audit trail | `clinical_audit_events` | who changed the classification, tags or note of a small variant, structural variant or CNV, reviewed a compound-heterozygous pair, changed the family's pedigree, members or phenotypes, replaced the annotation manifest, changed the NIPT artifact list, first applied a gene-panel version to a search of the family, deleted the family, or signed out, when, with before and after |
| Pedigree history | `family_structure_versions` | the family's pedigree after each change (members with their sex, role, affected and carrier status, and the relationships), one version per change, hash-chained per family |
| Signed reports | `report_signouts` | each sign-out as a frozen, versioned, content-hashed snapshot, found again by its report code |
| HTTP audit log | `audit_log_events` | every API request, with the user and a masked body |

**The manifest** is filled from three sources: the VCF headers read at import
(`vcf_header`, see [annotation-provenance.md](annotation-provenance.md)), the pipeline's own
record in a package (`manifest`), and a replacement by an admin through
`PUT /families/{family_id}/annotation-manifest`. A replacement is always recorded as `manual`,
and later imports leave a manual manifest alone. An import and a replacement of the same
family take turns on one lock, so neither overwrites the other halfway.
`GET /families/{family_id}/annotation-manifest` merges it with what CoGA itself
loaded: the reference assembly, the source of the gene loci (`gene_loci`: GENCODE, or the
UCSC table used when GENCODE could not be fetched), the Monarch release and the HPO release
(how each is read: [annotation-provenance.md](annotation-provenance.md#the-reference-modules)).
The report footer shows the merged list, and sign-out freezes it.

**The evidence snapshot** is taken on every ACMG save. It holds the variant's annotation
version, its annotation-set hash and its ClinVar significance, with the time. The hash
changes whenever any annotation of the variant changes, so it is the drift key. It also holds
the population frequencies and in-silico scores the classification rests on, as the ACMG dialog
reads them: from the variant's primary transcript (MANE Select, then canonical, then the most
severe impact), whose id it records in `transcript_id`, with the helpers the variant list serves
them with (`small_variant_review_acmg.small_variant_evidence`). Under `consequence`: the
consequence term or terms (`effect`) the criteria for a null, missense, synonymous or
length-changing variant read (PVS1, PM4, PP2, BP7, and the criteria a consequence rules out), the
`impact`, and LOFTEE's loss-of-function call (`lof`) the PVS1 strength reads. Under
`frequencies`: the gnomAD allele frequency the frequency criteria read (`gnomad_af`), the exome,
genome, popmax and TOPMed frequencies and the gnomAD allele, homozygote and hemizygote counts the
filters read. Under `in_silico`: CADD (`cadd_phred`), REVEL, SpliceAI (`spliceai_max`), SIFT,
PolyPhen and the AlphaMissense class and score. Under `gene_constraint`, for a missense variant
only: the gene's gnomAD missense Z (`gene_missense_z`) the PP2 criterion reads. A missense variant
is one whose consequence includes `missense_variant`, as the dialog tells one for PP2
(`reads_gene_missense_z`); PP2 reads the missense Z for no other variant, so for any other the
group is left out and the gene's profile is not read (the owner's decision, 2026-10-20). The
missense Z is not in the variant's annotation but in the gene's profile (`gene_info`), so it is
read with the variant list's own lookup (`gene_constraint_metrics.fetch_gene_constraint_metrics`),
for the gene the variant is served with: the primary transcript's gene id, else its symbol, else
the variant's first gene. Every field is frozen, `null` without a value, so a value that appears
later is a change; a value JSONB cannot hold (NaN, infinity) is frozen as `null`. A snapshot taken
before these values were frozen (CLIN-7) holds none of them; one taken before the consequence and
the missense Z were holds `transcript_id`, `frequencies` and `in_silico` only.

A structural variant or CNV has no annotation-set hash in ClickHouse, so its snapshot
(`services/structural_variant_evidence.py`, taken on every save of a CNV scoring) holds the
values the CNV classifier reads instead: the SV's type, the genes it overlaps and their count,
the pLI and the annotated inheritance. The classifier reads no clinical-CNV, dosage-score or
DGV data; the analyst scores those criteria by hand. The snapshot also holds the event (its
caller, chromosome, start, end, length and breakend partner), a hash of its whole annotation
record, the time, and the versions the evidence comes from: those the family's manifest records
for its SV callset (`by_modality.sv`, else `pipeline`) and the reference assembly and gene loci.
The drift key is `evidence_hash`, SHA-256 over the evidence; the versions are recorded, not
compared. It does not hold the genotype calls. A scoring for an SV that is not in the family's
data is refused with 404; one saved for a family without an assembly (no SV storage to read) is
stored without a snapshot.

Several callers can call one event under one SV id (an uploaded SV's id names no caller, and a
NeedlR SV's has the same form), and they share its review. Each caller's call is a row of its
own on the SV page, with its own genes, pLI and inheritance, so the snapshot is taken from the
row of the caller the scoring was made on: the save names it in `source` (the row's caller,
which the classification dialog sends). A save that names no caller (a client from before the
field) takes the SV's one row; when several callers call the SV, which row it was made on cannot
be told, and it is refused with 422 rather than frozen from another caller's row. A save naming
a caller that does not call the SV is refused with 404. Before this (TRACE-11) the snapshot was
taken from the row with the lowest storage key, which could be another caller's than the one
classified. Such a snapshot is kept as it was stored: it names the caller it came from
(`evidence.source`) and holds that caller's genes, pLI and inheritance, the drift check compares
it with that caller's row, and saving the scoring again from the row it was made on freezes that
row's evidence.

**The clinical audit trail** is written in the same transaction as the change it describes,
so it cannot drift from the data. Small-variant, structural-variant and CNV review saves write
to it, and so does sign-out. An admin's replacement of the annotation manifest writes to it too, and so does every change to the NIPT artifact list, on a chain of its own (`system:nipt-artifacts`, not a family), and every record of whether TRGT's counting units at a repeat locus are validated, on another (`system:repeat-loci`). So does every change to a family's phenotypes (SEC-1): a `phenotype` event per HPO term added, changed or removed, with the member, the term and its label, the status, onset, evidence, source and note before and after (none before a term added, none after one removed), and a summary that names what changed (*Phenotype changed for S1: HP:0001250 Seizure: status present → absent*); a save that changes nothing writes none. Its actions are `classification`, `tags`, `note`, `compound_het_pair`, `pedigree`, `phenotype`, `family_deleted`, `annotation_manifest` (the replacement, with the manifest it replaced and the new one), `panel_applied` (below), `sign_out`, `nipt_artifact_added`, `nipt_artifact_updated`, `nipt_artifact_removed`, `nipt_artifacts_auto_seeded` and `nipt_artifacts_imported` (an import of the NIPT-M pipeline's recurrent table, naming the file and every allele it listed), and `repeat_count_units_validated` and `repeat_count_units_not_validated` (the locus, and the record before and after, with its reason).
`compound_het_pair` (SEC-7) is the review of a compound-heterozygous pair, given on one
variant's review with its partner: a save that records a pair, changes it or breaks it up
writes one event per pair that changed, with the pair as it was and as it is (its partner,
gene, classification, tags, note and phase). It is filed under the variant whose review held
the pair (the saved variant; for a pair the save broke up by pairing either variant with
another, the variant that held it) and names the partner in its summary (*Compound-het pair with
1-200-C-T: classification Likely pathogenic → Pathogenic*); `metadata.compound_het_group_id`
names the pair's group. An unchanged re-save writes none. `pedigree` (SEC-7) is a change of the
family's pedigree or members: its creation (*Pedigree recorded with 3 members: …*), an edit of
the structure or of members (sex, role, affected status, carrier status, parents, a rename, a
member made inactive), a package import that gives a member another affected or carrier status,
and an admin's deletion of a sample with its data. It holds each changed member and, when they
changed, the couples and the links of unknown degree, as they were and as they are, and its
metadata names the structure version the change stored, with its hash and the change's reason;
an edit that leaves the pedigree as it was writes none
([family-member-management.md](family-member-management.md#what-is-recorded-of-a-change)).
`family_deleted` (SEC-7) is the last event of a deleted family: an admin's deletion with its
data (Admin → Data), a PED upload or a family made by hand that replaces it with overwrite, or a
package import that created it and imported nothing. It holds who deleted it and when, its
samples by ID, its latest structure version, and what went with it: the variant reviews, repeat
calls, Paraphase results, interval tracks and raw files Postgres held, and for an admin's
deletion the small and structural variants counted in ClickHouse (*Family deleted with its
data: 3 samples (F1, M1, P1), 1204 small variants, 3 structural variants, 5 variant reviews*);
`metadata.source` says which deletion it was. The deletion asks no reason, so the event holds
none. It is written in the deletion's transaction, before anything of the family is deleted (an
admin's deletion) or before the family row goes, so a deletion that fails records nothing; the
family's chains (its clinical audit events, sign-outs and structure versions) stay after it,
unlinked from the family row and verifiable by the family's ID.

**The gene panels a family's searches apply** (`services/panel_use_audit.py`, REQ-CARR-003). A
search of the family's data that applies a gene panel writes a `panel_applied` event before it
runs, the first time it applies that version of the panel: the small-variant and the
structural-variant list and their CSV exports, the NIPT variant list and the NIPT coverage. The
event holds the panel's id, name, local version and gene count, and the release of its source:
for a PanelApp panel PanelApp's panel id, its version of the panel, the date PanelApp gives
that version, the confidence levels imported and when CoGA imported it; for the Mendeliome the
Monarch release. One event per panel version and family: a version already recorded writes
nothing, and the check runs again under the family's audit-chain lock, so two searches at once
record it once. A panel edited or imported again since (each such version is archived in
`gene_panel_versions`, #809), or a PanelApp version imported over the same genes, is recorded
again when a search next applies it. A search the event cannot be recorded for fails with it.
Nothing recorded the panel before: the HTTP audit log keeps only the query's keys by default
(`AUDIT_LOG_QUERY_STRING_MODE=keys`), writes in batches after the response and can be switched
off, so it could not anchor a signed record. A panel applied in the variant explorer, which
searches across families, is recorded for no family.
For a small variant, `classification` holds the ACMG class, the accepted criteria, the point
total, the VUS tier and every stored criterion: its strength, whether it was accepted, its
evidence text and whether it was an automatic suggestion. For a structural variant or CNV, it
holds the reviewer's classification and the CNV (ClinGen) scoring: the class, the kind, the
point total, the accepted criteria with their points, and every stored criterion in the same
detail. Any change to that record writes an event, even when the class stays the same, and its
summary names what changed (*ACMG criteria updated (…): PM2 moderate → supporting*). An
unchanged re-save writes none.
Points are recorded as the database reads them back, so a total of -0.0 is stored as 0 and the
chain still verifies. These events carry `metadata.modality = "sv"`, so an SV id is never read
as a small variant's. A save that clears or deletes a review is recorded like any other
change. `GET /families/{family_id}/clinical-audit` returns a
family's trail a page at a time, most recent first, in one fixed order (`created_at`, then `id`:
the chain's order, reversed): 200 events by default, at most 500 (`limit`). `before`, the id of
the last event of the previous page, reads the events recorded before it, so an event written in
the meantime neither repeats one nor skips one; an id that is not an event of the family's trail
is answered 404. `total` is how many events the trail holds, read in the same statement as the
page, and `has_more` says older events remain. The report shows the trail as the clinical audit
trail: when it shows only the most recent events it says so, on screen and at the top of
every printed page, and **Show older events** reads the next page. Two people saving
the same review cannot silently overwrite each other: a save that carries the
`expected_updated_at` it loaded is refused with 409 `review_conflict` when the review changed
in between.

## Evidence drift

`GET /families/{family_id}/classification-drift` compares each classification's snapshot with
the variant's current annotation:

- `current`: the annotation-set hash and every value the snapshot froze are unchanged;
- `drifted`: the hash changed (a new annotation release, ClinVar and version shown from → to),
  or a frozen value did (even under the same hash: a missense variant's gene missense Z is not
  under it, so a refresh of the gene profiles can move it while the hash stays the same; it never
  moves another variant's classification, which froze none). `changed` names what moved:
  `clinvar`, `transcript_id` (the primary transcript), each consequence, frequency or in-silico
  field by its name (`effect`, `impact`, `lof`, `gnomad_af`, `revel`, …), `gene_missense_z`, and
  `annotations` when the hash moved; `evidence_from` and `evidence_to` hold the transcript,
  consequence, frequencies, in-silico scores and missense Z as frozen and as they are now;
- `unknown`: nothing is seen to have moved, but a hash is missing or a frozen group cannot be
  read, so the match cannot be verified;
- `variant_missing`: the variant is no longer in the data.

Only the values a snapshot froze are compared, numbers by value (JSONB keeps a number's digits,
not whether it was an integer). A snapshot taken before the frequencies and in-silico scores
were frozen has `evidence_from` null and is compared by its hash and ClinVar significance alone,
as before: it does not turn into drift. One taken before the consequence and the missense Z were
is compared on its transcript, frequencies and in-silico scores: a consequence or missense Z it
does not hold is never drift. The current values are read as a classification saved now would
freeze them: a variant classified as a missense variant and no longer called one names its
consequence and its missense Z as gone (*gnomAD missense Z 2.95 → n/a*), and one that became a
missense variant names its consequence only. The check reads the missense Z of the family's
missense variants in one query.

The structural variants and CNVs are under `structural`: each CNV scoring's snapshot compared
with the SV as the SV page reads it now, in the row of the caller the snapshot names
(`evidence.source`): another caller's call of the SV is other evidence, not a change to this
one. `drifted` names what moved in `changed` (`gene_symbols`, `pli`, `inheritance`, `sv_type`,
`locus`, or `annotations` for any other annotation), with the evidence from → to; `variant_missing`
means that caller's call is no longer in the data, even while another caller still calls the SV.
Only the fields a snapshot froze are compared. Frozen evidence that cannot be read, or that names
no caller, is `unknown`, never `current`.

At sign-out, a reported classification that has no snapshot counts as drift too
(`no_snapshot`), whether it is a small variant's or a structural variant's.

## Sign-out

A sign-out is the analyst's analytical sign-off (*What sign-out is*, above). The clinical report
in the EPD refers to the signed version by its report code (*The report code* below).

`POST /families/{family_id}/report/sign-out` freezes:

- what the record is (`record_statement`): *This record is the analytical sign-off in CoGA,
  which closes and freezes the analysis; the medical sign-off of the patient report happens in
  the EPD, which refers to this record by its report code.*;
- the merged manifest and the reference assembly;
- the software version and git commit that produced the snapshot;
- the small variants tagged `report`, each with its classification, ACMG criteria and
  evidence snapshot, and the reported structural variants and CNVs with their classification,
  CNV criteria and evidence snapshot;
- the drift state (`drift` for the small variants, `structural_drift` for the structural
  variants and CNVs), the sample-integrity QC result, and the sequencing QC with the cut-offs it
  was judged against;
- the import state (`import_incomplete`): null when the family's data imported completely,
  otherwise the datasets that failed and those that imported, when, and the import job;
  and the imports that stopped part-way (`import_unfinished`): empty when there are none,
  otherwise for each its import job, when it began, its datasets and those it finished (for
  a failed overwrite whose restore did not finish, every dataset of the family still to
  import again, none finished; for a single-file upload or a deletion that did not finish, no
  job, under its `upload-…` or `deletion-…` key);
- what the analysis looked at (`services/signed_record_sections.py`, one section each):
  - `variant_descriptions` (REQ-TRACE-007): each reported variant as the analyst reviewed it,
    read from the family's variant lists as the report page reads them (`review_tag=report`),
    one entry per callset or caller that calls it. A small variant: its locus, reference and
    alternative allele, gene, transcript (and whether it is MANE Select), HGVS c. and p.,
    consequence and impact, and each sample's genotype with its depth, genotype quality,
    allele depths and allele fraction. A structural variant or CNV: its type, copy change
    (loss or gain), locus, length, breakend mate, genes, cytoband and caller, and each sample's
    genotype, copy number, supporting reads, quality and filter;
  - `gene_panels` (REQ-CARR-003): each gene-panel version the family's searches applied, as
    the `panel_applied` events recorded it (above): name, id, local version, gene count, source
    and the source's release (PanelApp's version of the panel, or the Monarch release of the
    Mendeliome), when CoGA imported that release, and when and by whom it was first applied;
  - `reference_data` (TRACE-5), beside the manifest's modules: the gene reference's releases
    for the reported variants' genes (for each source, the release and checksum of the file
    that answered for each gene, and the genes it has no record of) and the clinical CNV
    knowledgebase loaded for the assembly (its source, the release its source states, none for
    a rebuild, and when it was loaded). PanelApp gives each panel a version of its own, which
    `gene_panels` holds;
  - `repeat_expansions`: every TRGT call of the family's samples, with the status CoGA gives it
    at sign-out (as the repeat page classifies it), its alleles, and the thresholds behind it,
    and the catalogue it was classified against: how many loci, from which sources (STRchive,
    CoGA's built-in rows) and a SHA-256 digest of every locus's thresholds, since STRchive's
    file states no release;
  - `paraphase`: every Paraphase result of the family's samples, with its status as CoGA reads
    it at sign-out, its copy numbers, the region's status rule and copy-number norm, and the
    catalogue of regions (how many, and a digest of their rules);
  - `pgt_analysis`, in a PGT family only (one with an embryo; REQ-PGT-005): the PGT analysis at
    the family's region of interest, the object the family page reads its embryo calls from
    (`services/pgt_analysis.py`, `GET /families/{family_id}/pgt-analysis`). It holds the ROI and
    the span of haplotype blocks read (the ROI and 1 Mb on each side); the inheritance model as
    recorded and as applied (without one, AR when a member is marked a carrier, else AD); each
    member's role, sex, affected and carrier status, which the disease haplotype is inferred from;
    the disease haplotype(s) found at the ROI (side, homolog and kind) or that none was; the
    parents' phase corrections near the ROI; and for each embryo its recorded sex, its call
    (affected / at risk, carrier, unaffected, uninformative; none when the family has no ROI),
    whether an uninformative call is down to its own missing data at the ROI, both calls when an
    X-linked recessive call depends on a sex that is not recorded, the homologs it carries at the
    ROI with the extent of their blocks, the block boundaries inside the ROI or within 250 kb of
    it (recombinations), its phasing QC at the ROI as the ROI marker review counts it
    (informative sites, Mendelian errors and their rate, and the sites read on each parent's
    side), and that QC's verdict against the thresholds of `services/pgt_embryo_qc.py`
    (`qc_verdict`: `pass`; `warning`, naming each measure that does not meet its threshold with
    its value and the threshold; or `not_counted` when no QC was counted, which is no pass), with
    the parental sides whose markers were checked, the sides the call reads. Its inputs: the
    haplotype file each member's blocks were built from and when it was loaded, the number of
    blocks read and a SHA-256 digest of them as read (lineage tags included), the distances the
    calls and warnings use, and the QC thresholds in force (`rules.qc_thresholds`). A family
    without an embryo has no such key, so its record is as it was;
  - `nipt`, a monogenic NIPT family's only (`services/signed_record_nipt.py`; REQ-NIPT-001,
    -004, -006, -010 to -013, -015): the NIPT analysis as the NIPT page computes it at
    sign-out, by the backend's code (`nipt_service`, `nipt_quality_checks`), never by the
    browser. The trio (`samples`); the fetal fraction with its interval, site count, per-site
    median and verdict, and the de novo window; the category counts, the filter funnel and why
    cfDNA calls failed the quality filter; the quality checks, each with its verdict and the
    sentence that says why (`checks`: paternity, the fetal sex from the father's X alleles and
    from the plasma's chrY coverage, the maternal plasma sample's sex profile); the coverage of
    every capture target and each gene with a weak target (`target_coverage`, null without a
    per-target coverage table); each variant tagged `report` as the NIPT variant list
    classifies it (`variants`: its category, maternal state and fetal inheritance, the observed
    and expected allele fraction, the confidence, the fetal-inheritance probabilities, its flags
    and quality-filter failures, the plasma's and the father's reads, and for a de novo candidate
    in the fetal window its triage, one the de novo view leaves out as recurrent included); each
    reported variant it does not classify, with why (`not_classified`: not in the data, on the
    artifact list, no plasma call); the recessive fetal risk of the genes the reported variants
    make both parents carriers in, as the recessive view computes it over them
    (`recessive_genes`); and what the analysis ran with (`inputs`: the per-target coverage
    tables with their file, number of targets and load time, and the assay's artifact list with
    its scope, its number of alleles, a SHA-256 digest of them, how many it left out of this
    analysis and which the family's annotation kept in; `model`: the R NIPT-M v0.5.1
    validation, its constants, the cfDNA quality filter and the fetal-fraction rules). A family
    that is no monogenic NIPT family holds no `nipt` key: its record is as before. A trio that
    cannot be resolved (no active father, or no maternal plasma sample) is frozen as unavailable,
    with a sentence of its own;
  - `coverage_tracks` (owner decision of 2026-10-20; TRACE-9): every coverage track of the
    family's members, one per sample and caller (`source`: `web` for a coverage BED, an
    upload's or a package's; `wisecondorx`, `qdnaseq`, `hificnv`), with what it holds as the
    depth readers read it (`content`: `read_depth`, `log2_ratio` or `unknown`, by their one rule,
    `coverage_read_depth.coverage_track_content`, over the same records: a coverage BED what it
    declared when it was loaded, another caller's track what its import's transform made of its
    file), and each file it was loaded from (`files`: its name, its rows, when it was loaded, and
    from where: `upload` for a single-file upload, `package` for a package import's dataset, null
    when its record does not say, as for a coverage BED loaded before 2026-10-20; with what a
    coverage BED declared and the transform another caller's import recorded). Only a track of
    read depth is read as depth (the mtDNA coverage and its `mtdna.mean_depth` verdict, the NIPT
    coverage check without a per-target table), so the record says which kind of coverage a depth
    check rested on. Read from the tracks' records in Postgres, as the PGT and NIPT sections read
    their inputs. Every family's record holds it, as `{"tracks": []}` for a family without a
    coverage track, so a track loaded after sign-out reads as a change. A monogenic NIPT family's
    per-target coverage tables are another track type (`target_coverage`), which `nipt` holds
    (`inputs.target_tables`); the NIPT section freezes no coverage note, so nothing is held twice;
- the signer, the time, and any acknowledgement with its reason.

Each section of what the analysis looked at is read inside a savepoint. One that cannot be
read is frozen as `{"unavailable": <a fixed sentence>}`, never as an empty section, and is
named in `not_captured` (a part of `reference_data` on its own); the error's own text, which may
quote what it read, is not frozen.

Sign-out exists for the family report, and a monogenic NIPT family's analysis is signed out
through it (below). In a PGT family its snapshot holds the embryo calls (`pgt_analysis`,
above; the owner's decision of 2026-10-14, REG-2). The backend makes them from the stored
haplotype blocks (`services/pgt_embryo_calls.py`), the family page shows the calls the backend
serves, and a sign-out freezes the same object: the record holds the calls the analyst saw,
made by one implementation from stored data. The haplotype tracks still draw the risk line
and each member's risk state in the browser (`frontend/src/lib/haplotypeRisk.ts`); the two
implementations answer the cases of `frontend/src/lib/__tests__/fixtures/pgtEmbryoCallCases.json`
alike, which `test_pgt_embryo_calls.py` and `haplotypeRisk.test.ts` both run. The live family
report does not show the embryo calls; its signed version does.

**A NIPT family's record** (owner decision of 2026-10-14, REG-2). A monogenic NIPT family is
signed out through the same sign-out, `POST /families/{family_id}/report/sign-out`, from the
NIPT page or from the report page: one signed record per analysis, with the same gates, version
and report code, which also holds the NIPT results (`nipt`, above). Where it is signed out from
makes no difference to what is frozen. The Sample QC's NIPT checks stay in `sample_qc`
(`paternity_check`, `fetal_sex_check` from the father's X alleles, `category_qc_check`, the
parents' sex, and their fixed notes), and the gene panels its NIPT searches applied in
`gene_panels`. A NIPT variant whose review is tagged `report` is frozen in `reported_variants`
and `variant_descriptions` like any reported small variant, with its classification, and in
`nipt` with its category, reads and probabilities. Reading the NIPT section runs the NIPT
analysis once more, beside the one the Sample QC runs.

The snapshot is hashed with SHA-256 over a canonical encoding and stored as the next version.
An amendment is a new sign-out; no version is ever changed. The sign-out is also written to the
clinical audit trail. A stored hash is always checked against the snapshot as it was stored, so
a version signed before a field was added still verifies; the sign-out check reports that
section as `not_compared`. A section neither the record nor the family's current data holds
(the NIPT results of a family that is no NIPT family) is neither compared nor listed.

Five gates run first, in this order:

1. **Assembly scope.** A family whose assembly is not in `VALIDATED_ASSEMBLIES` (default
   GRCh38), or that has none, is refused with 409 (`gate: "assembly_scope"`). This cannot be
   acknowledged. The family and report pages say such a family is not validated for clinical
   use, and neither the report page nor the NIPT page offers a sign-out.
2. **Data being written.** A package import writes a family over minutes and commits as it
   goes, and flags it `import_incomplete` only once it has failed, so a snapshot read while it
   runs can hold a half-imported family. A family with a package-import job (not a dry run)
   that is `queued` (naming the family in its request), `validating` or `running` is refused
   with 409 (`gate: "import_in_progress"`, naming the job), and so is a family whose
   variant-write locks a writer holds (`gate: "variant_writes_in_progress"`: an import, an
   upload, an admin delete). Neither can be acknowledged; the sign-out does not wait. Past
   this gate the sign-out shares the family's variant-write locks
   (`pg_try_advisory_xact_lock_shared`) until it commits, so no write of the family's variants
   starts while it reads, and it checks the import jobs once more after the snapshot: an
   import claimed meanwhile commits its job as `running` on the family before it writes
   anything of it, and refuses the sign-out. That record is not best-effort: an import whose
   job cannot record it (a database error, or another worker has claimed the job) stops
   without writing anything of the family, and its job ends `failed` with the reason while it
   is still this worker's and can be updated (#736). The job's later updates (its logs,
   dataset summaries and heartbeat) are informational: one that fails leaves the status and
   the family as they are. A job whose worker stopped keeps refusing until a worker claims it
   again. A running job writes its heartbeat every minute (the database's clock stamps and
   judges it), so a job whose heartbeat is ten minutes old is taken for one whose process has
   stopped. The worker that claims it runs it again only if its import had not begun writing
   the family (`validating`); one that was `running` it ends `failed`, interrupted, keeping
   its log, and the family stays marked (gate 5). An import whose process is alive but could
   not write the heartbeat is stopped where it is once its heartbeat reaches the database
   again and finds the job ended, and leaves the family marked the same way (#746). Between
   the claim and that stop the job no longer refuses a sign-out, but the import's
   variant-write locks do (`variant_writes_in_progress`) while their connection holds them.
3. **Evidence drift.** Any drifted, unknown, missing or unsnapshotted classification, of a small
   variant or of a structural variant or CNV, gives 409, unless the request sets
   `acknowledge_drift` with a `drift_acknowledgement_reason` (422 without a reason). One
   acknowledgement covers both; the message says how many are structural variants or CNVs.
4. **Sample-integrity QC.** A QC fail (a detected sample or pedigree swap), or a check that
   could not run for lack of data where it is what confirms the pedigree or a sample's
   identity (a relationship or Mendelian check the pedigree asserts, NIPT paternity or maternal
   lineage, or the sex of a sample that no relatedness check covers, such as a NIPT parent),
   gives 409 (`gate: "sample_qc"`), unless the request sets `acknowledge_qc` with a
   `qc_acknowledgement_reason` (422 without). Each concern the 409 lists names the sample,
   the pair, the child with its parents, or the NIPT father its check read ("Sample X: no
   chrX genotypes available for sex inference."), in `qc_summary.messages`, in
   `unverifiable_checks` and in the message, and so does the list of checks that could not
   run which the sign-out's audit event records (`qc_unverifiable`). Fetal sex and the NIPT
   maternal lineage, read from the cfDNA as a whole, name no sample. The checks frozen in
   `sample_qc` keep their own messages beside their sample fields, so a record signed before
   the names were added compares the same; an audit event recorded before names none (CLIN-8).
   For a monogenic NIPT family a NIPT quality check that failed in the record's `nipt` section
   counts as well, under the same acknowledgement: no fetal-fraction estimate, a fetal sex
   whose paternal-X and chrY signals disagree, or male DNA given as the maternal plasma
   (REQ-NIPT-015; TF-06 H4). The 409 lists each in `nipt_failed_checks` and in
   `qc_summary.messages`, the plasma's naming its sample ("Sample X (maternal plasma): male
   DNA …"), and the audit event records them (`nipt_failed_checks`). Paternity is not among
   them: the Sample QC reads it from the same analysis, and gates on it already. NIPT results
   that could not be read are not captured (named in `not_captured`), not a QC concern.
5. **Incomplete import.** A family flagged `metadata.import_incomplete` by a package import
   that left it partly loaded ([data-import.md](data-import.md)), or by a small-variant upload
   that failed after deleting the callset it replaced (with no import job;
   [What an upload records](data-import.md#what-an-upload-records)), gives 409
   (`gate: "import_incomplete"`, naming the failed datasets and the import job), unless the
   request sets `acknowledge_import_incomplete` with an
   `import_incomplete_acknowledgement_reason` (422 without). Any set flag counts, whatever its
   shape. An import whose process stopped part-way sets no flag; the entry it wrote in
   `metadata.import_unfinished` before its first write of the family gates the same way, in
   the same 409 (`import_unfinished`, naming the job and the datasets it had not finished,
   which may be partly written or missing), and the one acknowledgement covers both. Every
   family page shows *Import incomplete* while either is set. The flag clears only once an
   import has imported each failed dataset again, or an upload has brought it again (in any
   mode, for the same samples and small-variant source, an import counting a dataset it imports
   with warnings, or registers when its package carries nothing CoGA imports for it; a later
   failure keeps an earlier one's failed datasets, and every callset or set of samples a
   dataset failed for, each with its job, `failed_scopes`; the gate names each dataset with the
   job of its latest failure, `failed_jobs`). A failed `overwrite` whose restore failed too, other than by refusing
   to start without its whole backup, may have lost rows of any dataset of the family,
   whichever import or upload wrote them: its flag names every dataset of the import as
   failed, those it imported too, and its `import_unfinished` entry stays, naming every dataset
   the family holds (what its imports and single-file uploads recorded in
   `metadata.imported_scopes`) and every dataset of the import, so the gate holds until the
   whole family has been imported again with `overwrite`, not only what failed: each import
   that completes with overwrite, and each upload with overwrite, removes from the entry what
   it brought again, for that scope, and an update removes nothing. Only an import that
   completes with `overwrite` and imports again the datasets a stopped import had not
   finished, for that scope, removes that import's entry. A single-file upload that writes
   ClickHouse marks the family the same way before its first write there, an entry with no
   job keyed `upload-…`, and an admin's delete of a sample's structural variants that could
   not put the family's other calls back leaves one keyed `deletion-…`
   ([The mark of an upload that did not finish](data-import.md#the-mark-of-an-upload-that-did-not-finish)):
   it gates in the same 409, whose message names "a single-file upload" or "a deletion of a
   sample's data" that "did not finish" and what may be partly written or missing, and is
   frozen with its key, from which the signed record names what wrote it. Each upload or
   import with overwrite removes from it what it brought again.

The acknowledgements and their reasons are part of the hashed snapshot and the audit event.

A lookup that fails while the snapshot is built is frozen as an explicit marker, never as an
empty value: missing QC cut-offs as `sequencing_qc.unavailable`, and a failed lookup of a
reference module, or an HPO ontology that recorded no release, as module version `unavailable`.
The audit event lists these as `not_captured`, and the report page names them.

The other sign-out endpoints:

| Endpoint | Returns |
| --- | --- |
| `GET /families/{family_id}/report/sign-outs` | the list of signed versions, each with its `report_code` |
| `GET /families/{family_id}/report/sign-outs/{version}` | one signed version, with its `report_code`, `verified` (its content hash recomputed on read) and `not_captured` (what its record could not capture) |
| `GET /families/{family_id}/report/sign-out-check` | whether the report as it would be signed now matches the latest version: `matches`, `changed_sections`, `not_compared`, `not_captured` |
| `GET /report-codes/{code}` | the family and version of the signed record a report code names (*The report code*) |

### The report code

The EPD report quotes the signed record by its report code (`services/report_code.py`). The code
is made from fields of the record that never change, so no column holds it and records signed
before it existed have one too:

```
FFFFFF-V-HHHC        e.g. 7KQM2X-2-4PHT
```

- `FFFFFF`: six symbols from the SHA-256 of the family identifier, the same in every version of
  the family's record, so an amendment's code starts like the code of the record it amends;
- `V`: the version, in decimal;
- `HHH`: three symbols from the first 15 bits of the record's content hash, which tie the code
  to the content that was signed;
- `C`: a check symbol over all the others, by the Damm algorithm over GF(32) (the operation
  `x * y = 2x + y`, modulo `x^5 + x^2 + 1`). It detects every single mistyped symbol and every
  swap of two neighbouring symbols, so a code copied wrong is refused rather than finding
  another record.

The symbols are Crockford's base 32: the digits and the capitals without I, L, O and U. A code
is read back in any case, with or without its hyphens or with spaces, an I or L as 1 and an O as
0. It is 13 characters for versions 1 to 9 and at most 16 up to version 9999.

**Unique.** Every sign-out takes one lock (`report-code`) from the moment it makes its code to its
commit, and looks the code up among the records signed before. A sign-out whose code another
record already has is signed again a microsecond later: the new time changes its content hash and
so its code. The code is in the sign-out's clinical audit event (its summary and `after.report_code`)
and in every sign-out the API returns.

**Finding a record.** `GET /report-codes/{code}` answers with the family, version, signer, time
and content hash of the record the code names. A string that is not a report code (a letter a code
never holds, too short, a version that is not a number, or a check symbol that does not match) is
refused with 422 `report_code_invalid` and says why; that is read from the string alone. A
well-formed code no record has, one whose record's family is outside the caller's projects, and one
whose family has been deleted all get the same 404 (*No signed record has this report code.*), so a
code tells nobody whether a record exists outside their projects; only the request's audit row
names the hidden family (REQ-SEC-001). The candidates are read by the code's version and the
content-hash prefix its hash symbols spell, as bound parameters. In the interface the lookup is
*Report codes* in the main menu (`/report-codes`, and `/report-codes/<code>` as a link from the
EPD); it shows the record's family, version, signer and content hash, and opens the signed version.

## The report page and the signed record

`/families/{family_id}/report` has two views. A family that has been signed out opens on its
latest signed version; one that never was opens on the live report.

**A signed version** (`?version=N`) is rendered from the snapshot that
`GET …/report/sign-outs/{version}` returns, and from nothing else
(`frontend/src/pages/families/signedReportRecord.ts` reads it). It shows the version, signer,
time, **report code**, content hash, `verified` and the frozen build, and the sentence that says
what the record is (the record's own, or for a record signed before CoGA froze it, the same
sentence); each reported variant's description as reviewed, classification, accepted criteria,
evidence snapshot, tags and note; the reported SVs with their description and ClinGen CNV
scoring; in a PGT family the embryo calls (*PGT embryo results*: the ROI, the inheritance model,
the disease haplotype, and per embryo its call, the homologs it carries at the ROI as the track
names them (P1, P2, M1, M2), its warnings, its QC at the ROI and, in a record that holds them, that
QC's verdict and the thresholds it was checked against, and the haplotype file read); in a
monogenic NIPT family its NIPT results (*NIPT results*: the fetal fraction and the quality
checks with their verdicts, the coverage, the funnel and the categories, each reported variant
as the NIPT analysis classified it, the recessive fetal risk, and what the analysis ran with);
no other family's record shows or mentions either; the gene panels applied, the repeat
expansions, the Paraphase results, the coverage tracks (*Coverage tracks*: per sample and caller,
what the track held, read depth, a log2 ratio or unknown, and the file it was loaded from, from
where and when) and the reference-data releases; the drift, Sample QC, sequencing QC and import
state, with the acknowledgements; the frozen modules, and the reference-data releases and the
coverage tracks beside them in the footer (*Coverage tracks as signed: S1 coverage BED (package)
— read depth; S1 WisecondorX (package) — log2 ratio; …*) with the report code; and
`not_captured`. A section the snapshot lacks is shown as not in the
record (*Not in the signed record: version N was signed before CoGA recorded …*), never as
empty, and one frozen as unavailable as not captured, with its reason. A record without
`pgt_analysis` says so only when its Sample QC read the family as a PGT family
(`sample_qc.application`); the record of any other family shows no PGT section. What no snapshot holds is
said on the page: the population frequencies and in silico predictions the live report shows,
the segregation, the gene and phenotype context, the audit trail and the pipeline settings (and,
for a record signed before CoGA froze it, the variant description). Print prints this view, the
report code included. A printout starts with a notice when the record fails its content hash,
when a later version supersedes it, or when the list of versions could not be loaded. On the
latest version the page also runs `sign-out-check` and says, on screen only, whether the
family's data has changed since. Straight after a sign-out the page says, on screen only,
*Signed out as version N. Report code …: the EPD report refers to this record by it.*

**The live report** (`?view=live`) draws live data. It is where a case is signed out, and it is
never presented as the signed record. It says what a sign-out is (*Signing out is the analytical
sign-off in CoGA, which closes and freezes the analysis; the medical sign-off of the patient
report happens in the EPD, which refers to the signed record by its report code.*), names the
latest signed version with its report code, and checks itself against the latest sign-out
(`sign-out-check`):

- the content matches the signed version: it says so, and is still not the signed report;
- the content changed after sign-out: the changed sections are named;
- the check could not be made: treat the page as unsigned.

Every printout of the live report starts with a notice ("Draft …", "Not the signed report …",
"Not verified …", or that the sign-out record is loading or could not be loaded). When the
clinical audit trail shows only its most recent events, the notice says that too
("Incomplete — the audit trail lists the 200 most recent of its 450 events …"). While the
sign-out record is loading or could not be loaded, sign-out is not offered. After a sign-out the
page shows the new version.

**A monogenic NIPT family** is signed out from the NIPT page too (`FamilyNiptPage.tsx`,
*Sign out NIPT analysis*, or *Amend sign-out* once signed), through the same request, gates and
acknowledgement dialogs as the live report (`reportSignOut.tsx`, which both pages use). The NIPT
page names the latest signed version with its report code, says whether the family's data still
matches it (`sign-out-check`) and links to it; after a sign-out it opens the new version on the
report page. Sign-out is not offered there off the validated assembly scope, nor while the
sign-out record is loading or could not be loaded. The NIPT report (`FamilyNiptReportPage.tsx`)
is live too: it says so on screen, beside the latest signed version, and every printout of it
starts with the live report's notice ("Draft …", "Not the signed report …", "Not verified …").

Each snapshot also records the reference modules its build looked up (`reference_modules`). A
reference module CoGA adds later is not in that list: for a record signed before it, the check
lists `modules.<key>` under `not_compared` instead of calling the record changed, and
`not_captured` names the missing version. A module in the list but not held was not loaded at
sign-out, so one loaded since is a change. A snapshot without the list names no module, so every
reference module it does not hold is listed as missing.

A PGT family's record signed before CoGA checked each embryo's phasing QC against thresholds holds
no `qc_verdict` and no `rules.qc_thresholds`. The check compares its `pgt_analysis` on what it holds
and lists `pgt_analysis.qc_verdict` under `not_compared`, instead of calling the record changed
(`pgt_analysis.comparable_with_record`); a call that changed since still reads as a change. A
record that holds the verdict is compared whole, so a verdict or a threshold changed since it was
signed reads as a change.

A section CoGA has since computed differently reads as changed too. The Sample QC now counts a check
that could not run as `warn`, with a note naming each sample whose sex could not be checked, so for a
family with such a sex check the check lists `sample_qc` as changed against a version signed before:
that record holds no such note, and holds `pass` where the other checks passed. The signed version
still shows what was signed; signing out again records the new verdict.

A signed version lists the SV/CNV classifications whose evidence had moved at sign-out with the
small variants', under *Evidence drift at sign-out*, and says when its record holds no SV/CNV drift
check. A small variant's entry names the frozen values that had moved, with the values
(*REVEL 0.412 → 0.951*, *LoF LC → HC*, *gnomAD missense Z 2.95 → 3.52*), and each reported small
variant shows, under *Evidence when classified*, the consequence, frequencies and in-silico scores
its snapshot froze from the primary transcript and, in a sentence of its own, a missense variant's
gene missense Z; a group the snapshot did not freeze is left out, so one frozen before CoGA froze
any of them shows its ClinVar significance and annotation version only. A version signed before a
small-variant drift entry named what moved (CLIN-7) has its `drift` section listed as changed by
the check while it holds such an entry, and so does one signed before the consequence and the
missense Z were frozen: the entry the check computes again also holds them as they are now
(`evidence_to`).

The release candidate's snapshot format is the first one CoGA reads: records signed by earlier
development builds get no reading of their own (#681). They still verify, since verification
re-hashes the record as stored.

Both views download the frozen record as JSON.

### Adding a section to the signed record

The PGT embryo calls (`pgt_analysis`) and the NIPT results (`nipt`) were added as sections of
what the analysis looked at. A new section is added in four places, or it is missing from the
signed view without a trace. A section only one application's families hold names that
application in its `RecordSection` (`application="pgt"`, `application="nipt"`):
`build_record_sections` builds it for those families alone, another family's record holds no
such key, and the sign-out check neither compares nor lists a section that neither the record
nor the family's current data holds. The signed view renders it only for a record of that
application, read from the section itself or, for a record signed before it, from
`sample_qc.application` (`PgtAnalysisSection`, `isNiptRecord`).

1. **Its builder** in `backend/app/services/signed_record_sections.py`: an async function that
   reads what the analysis showed the analyst and returns a JSON-safe dict, deterministic (lists
   sorted, no clock, no random order: the sign-out check compares it right after sign-out
   through the JSONB round trip). Give it a `RecordSection` (its key, the words that name it,
   the fixed sentence frozen when it cannot be read), add it to `RECORD_SECTIONS` and call it in
   `build_record_sections` through `_guarded`. It is then frozen and hashed with the record,
   compared by `sign-out-check` (`REPORT_CONTENT_SECTIONS` holds every key of `RECORD_SECTIONS`),
   listed as `not_compared` against a record signed before it, and named in `not_captured` when
   it could not be read. A section of one application (`pgt_analysis`) names it in its
   `RecordSection` (`application`) and is built only for a family of it: the check compares it
   where the record or the family's current data holds it and names it as not compared only where
   the family's data holds it and the record does not, so the record of any other family stays
   as it was.
2. **Its reading** in `frontend/src/pages/families/signedReportRecord.ts`: a field of
   `SignedReport` parsed with `parseRecordSection`, which gives the absent, unavailable and
   recorded states; and its label in `REPORT_SECTION_LABELS`, which the check's changed sections
   are named with.
3. **Its rendering** in `SignedFamilyReport.tsx`, through `RecordSectionBlock`, which says what
   a record that does not hold the section lacks and what one could not capture.
4. **Its description** here, under *Sign-out*, and in the in-app reference and handleiding
   chapter 11, with tests of each (`test_signed_record_sections.py`,
   `signedReportRecord.test.ts`, `FamilyReportPage.test.tsx`).

## Tamper evidence and its limits

- **Append-only tables.** Database triggers refuse UPDATE and DELETE on `audit_log_events`,
  `ui_events`, `clinical_audit_events`, `report_signouts`, `integrity_anchors`,
  `qc_threshold_changes` and `family_structure_versions` (see [database.md](database.md)). The restricted runtime role `coga_app` also lacks those
  rights, but only once the deployment has switched to it
  ([db-runtime-role-runbook.md](db-runtime-role-runbook.md)). Until then the application
  connects as the table owner, who can disable a trigger.
- **Hash chains.** Each row of `clinical_audit_events`, `report_signouts` and
  `family_structure_versions` carries a `row_hash` over its content and the previous row's
  hash, per family. A deleted, reordered or edited row breaks the chain.
  `GET /admin/integrity/verify?table=…&family_id=…` walks one family's chain and names the first
  bad row; for sign-outs it also recomputes each content hash. `ui_events` and
  `audit_log_events` are append-only without a chain: a UI event is what the browser reports of
  itself, not the device's record of a clinical action.
- **Signed anchors.** `POST /admin/integrity/anchor` records the head of every chain and signs
  that snapshot with the Ed25519 key in `INTEGRITY_ANCHOR_SIGNING_KEY`, which the database does
  not hold. Outside development the backend refuses to start without the key and never writes
  an unsigned anchor. `GET /admin/integrity/anchor/verify` checks the live chains against the
  latest anchor; `GET /admin/integrity/anchor/verify-chain` checks the anchors themselves. An
  owner who rebuilds or shortens a chain after an anchor was made is detected by that check.

This is tamper-evident against someone who has only the database, between retained anchors.
It is not tamper-proof: someone who holds the signing key can forge anchors, and the most recent
anchors can be deleted without trace unless a copy is kept outside the database.

## Known limitations

- The variant description holds no population frequency or in silico prediction, and the
  snapshot holds no gene or phenotype context: a signed version says what its record lacks, and
  only the live report shows the rest, from current data. A record signed before CoGA froze the
  variant description (2026-10-14) names each variant by its ID alone. There is no byte-stable
  PDF: a signed version is printed from the browser, laid out by the build that renders it
  (named in its footer).
- The gene reference is frozen for the reported variants' genes only: a record without a reported
  variant names no gene-reference release. The gene loci the panel filters read are in the
  manifest (`gene_loci`). The clinical CNV knowledgebase states no release, so the record dates it
  by its load.
- A panel version is recorded the first time a search of the family applies it, not at each search,
  and a panel applied in the variant explorer, which searches across families, is recorded for no
  family. Searches made before the `panel_applied` event existed recorded nothing, so a record
  signed for such a family names only the panels applied since.
- A repeat-expansion call's and a Paraphase result's status is frozen as CoGA classifies it at
  sign-out, from the catalogue loaded then; the genome tracks still show the status stored at
  import until the calls are imported again (*TRGT repeat catalogue*,
  [data-import.md](data-import.md)).
- Phenotypes a package import loads are not on the clinical audit trail: the import job's
  dataset summary says how many it imported.
- Anchors are made by hand: nothing in the code, CI or Terraform calls
  `POST /admin/integrity/anchor` on a schedule.
- The export of each anchor to a store outside the database is not implemented (the
  `export_anchor` hook does nothing), so deleting the latest anchors is not detected.
- Verification trusts only the configured signing key. After a key rotation, anchors signed with
  the old key report `unknown_key`; keep the old public key for audits.
- `coga_app` keeps INSERT on the chained tables, so it could append a forged but
  self-consistent row; it cannot rewrite or delete existing ones.
- The manifest's platform layer covers the assembly, the gene loci, Monarch and HPO. The
  releases of the gene reference, PanelApp and the clinical CNVs are frozen beside it
  (`reference_data`, `gene_panels`), not in it: `GET …/annotation-manifest` and the live report's
  footer do not show them.
- Any analyst of the family's project can sign out, and any admin; a reader cannot. Sign-out is
  not limited to a signer role.
- CoGA does not see the clinical report in the EPD. Nothing in CoGA checks that the report
  refers to a signed version, which one, or that it says what that version holds; the
  reference is written into the report by the person who drafts it. The report code makes that
  reference short and refuses a mistyped one, and `GET /report-codes/{code}` finds the version
  it names, but CoGA does not see where a code is quoted.
- The embryo calls are frozen as the backend makes them at sign-out; the genome overview's and
  the chromosome view's haplotype tracks show each member's risk state from the browser's
  implementation of the same rules, which the parity cases pin but do not prove equal on every
  input.
- No threshold gates an embryo call on its QC. A call whose phasing QC at the ROI does not meet
  the thresholds of `services/pgt_embryo_qc.py` (at least 50 informative sites, a Mendelian error
  rate of at most 2 %, at least 6 markers on each parental side the call reads) carries a warning
  beside it on the family page and in the signed version, and so does one whose QC was not
  counted; the call stands, the sign-out is not held up, and the analyst decides (the owner's
  decision of 2026-10-20). The thresholds are provisional: the laboratory director and QA are to
  confirm them. The markers are counted over the ROI as a whole, not on each flank of the locus.
- A NIPT family's record freezes the NIPT classification of the variants tagged `report`, not
  every candidate the NIPT report or a view of the NIPT page lists, and their recessive fetal
  risk over the reported variants alone. It freezes the coverage of every capture target and
  each gene with a weak target, not the coverage of a search's gene panel or genes as the NIPT
  page's coverage card reads it; without a per-target coverage table it holds no coverage QC at
  all (the page's coverage over a coverage track is read for a search's genes or the family's
  region of interest): `coverage_tracks` holds what the plasma's coverage tracks held, so which
  kind of coverage that check rested on, not its result. The artifact list and the de novo
  recurrence are shared by every family of the assay: a change to the list, or another pregnancy
  of the assay imported since, makes the sign-out check name `nipt` as changed.
- `coverage_tracks` names each coverage track of the members as their records list it, not the
  chromosomes a depth check read: the mtDNA coverage reads a track only where it has rows on
  chrM, the NIPT coverage check on its targets' chromosomes, and a sample with two tracks of read
  depth is read from its coverage BED. The mtDNA coverage and its verdict are not frozen. A
  coverage BED loaded before 2026-10-20 has no record of where it came from (`loaded_from` null,
  *origin not recorded* on the signed version).
