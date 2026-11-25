# Clinical traceability, sign-out and audit

A signed-out analysis must be reproducible and defensible: it states what produced it, and who
decided what and when. This page covers what CoGA records, how sign-out works, how tampering is
detected, and the limits. It is for developers and auditors; lab users find the same in plain
words in the in-app reference
([clinical-traceability](../frontend/src/content/docs/clinical-traceability.md), at
`/docs/reference/clinical-traceability`).

**What sign-out is.** The analytical sign-off, by a supervisor: it closes the analysis and freezes
it into a *signed version* (in the UI also *signed report*; *Sign out report* makes one). It is not
the medical sign-off. The device boundary is *annotated VCF → closed, frozen analysis in CoGA*
([TF-02 §3](regulatory/TF-02-device-description.md)). The clinical report is drafted and medically
signed in the hospital's electronic patient record (EPD), outside the device, and refers 1:1 to the
signed version. A family that holds several analyses signs each out on its own (*One sign-out per
analysis*, below).

Code comments number the parts: Phase 0 annotation manifest, Phase 1 evidence snapshots and drift,
Phase 2 clinical audit trail, Phase 3 sign-out.

## What is recorded

| Record | Where | What it holds |
| --- | --- | --- |
| Annotation manifest | `family_annotation_manifest` | per analysis of a family, the versions of the tools and databases that produced its annotated input (VEP, ClinVar, gnomAD, dbNSFP, SpliceAI, callers, pipeline) |
| Evidence snapshot | `small_variant_reviews.acmg_evidence_snapshot`, `structural_variant_reviews.cnv_evidence_snapshot`, `repeat_expansion_reviews.evidence_snapshot`, `paraphase_reviews.evidence_snapshot` | what the classifier saw, per ACMG classification of a small variant and per CNV (ClinGen) classification of a structural variant or CNV; the calls a repeat-locus or Paraphase-region review rests on, when it carries a classification or the Report tag |
| Clinical audit trail | `clinical_audit_events` | who changed what and when, with before and after: reviews and compound-het pairs, the pedigree, members and phenotypes, the manifest, the NIPT artifact list, a gene-panel version's first use, the family's deletion, sign-out (below) |
| Pedigree history | `family_structure_versions` | the pedigree after each change (members with sex, role, affected and carrier status, and the relationships), one version per change, hash-chained per family |
| Signed reports | `report_signouts` | each sign-out as a frozen, versioned, content-hashed snapshot, found again by its report code |
| HTTP audit log | `audit_log_events` | every API request, with the user and a masked body |

### Annotation manifest

Three sources, each named in `source` (how each is captured:
[annotation-provenance.md](annotation-provenance.md)):

- `vcf_header`: the VCF headers, read at import;
- `manifest`: the pipeline's own record in a package;
- `manual`: an admin's replacement through `PUT /families/{family_id}/annotation-manifest`, always
  recorded as manual. Later imports leave a manual manifest alone.

The manifest is kept **per analysis**: one row per family and analysis (`analysis_type`, the key of
the analysis's package record; `unspecified` for a package that named no analysis and for a
single-file upload). A family sequenced short-read and then long-read WGS keeps both: each import
writes its own analysis's row and leaves the other's, so the long-read import never replaces the
short-read analysis's VEP, ClinVar, reference FASTA or caller.

An analysis's manifest is its own row laid over the row of no analysis (`unspecified`: an
upload's, a package's that named none: their data is every analysis's, *One sign-out per analysis*,
below). For a module both name, the analysis's own fields win and the versions per modality are
joined. The row of an analysis the family does not hold (an import that was put back) states
nothing. A row an admin replaced is that analysis's whole manifest. A family that holds no analysis
has one manifest, of the row of no analysis.

An import and a replacement of one family take turns on one lock, so neither overwrites the other
halfway. A replacement names the analysis whose manifest it replaces (`analysis_type`; required
for a family that holds several, 422 otherwise), and its audit event names it too.
`GET /families/{family_id}/annotation-manifest` returns one manifest per analysis the family holds
(`analyses`), each merged with what CoGA itself loaded: the reference assembly, the gene-loci source
(`gene_loci`: GENCODE, or the UCSC table used when GENCODE could not be fetched), the Monarch
release, and the HPO release with the SHA-256 of the ontology file its terms came from
([how each is read](annotation-provenance.md#the-reference-modules)). The pages show each
analysis's list; a sign-out of an analysis freezes that analysis's.

### Evidence snapshot of a small variant

Taken on every ACMG save, with the time. It holds:

- the variant's annotation version and annotation-set hash. The hash changes with any annotation of
  the variant, so it is the drift key;
- its ClinVar significance, with the record's review status (`clinvar_review_status`), which the
  PP5/BP6 evidence names;
- the values the classification rests on, as the ACMG dialog reads them: from the primary
  transcript (MANE Select, then canonical, then most severe impact; its id in `transcript_id`),
  through the variant list's own helpers (`small_variant_review_acmg.small_variant_evidence`).

Those values, by group:

- `consequence`: the term or terms (`effect`) the null, missense, synonymous and length-change
  criteria read (PVS1, PM4, PP2, BP7, and those a consequence rules out); the `impact`; LOFTEE's
  loss-of-function call (`lof`) for the PVS1 strength; LOFTEE's flags (`lof_flags`) and VEP's NMD
  prediction (`nmd`), either of which makes PVS1 Consider. For a multi-allelic record (ALT `G,T`)
  only, the ALT these values are of (`alt_allele`): the transcript alone does not say which
  allele's annotation was read.
- `frequencies`: the gnomAD allele frequency the frequency criteria read (`gnomad_af`); the gnomAD
  datasets the variant's callset was annotated with (`gnomad_annotated`; none when none), which say
  whether a variant without that frequency is absent from gnomAD (PM2); the exome, genome, gnomAD
  popmax, VEP `MAX_AF` (`max_af`) and TOPMed frequencies and gnomAD allele, homozygote and
  hemizygote counts the filters read; gnomAD's FAF95 (`gnomad_faf95_af`), which BA1 and BS1 read.
  For a chrM variant only, the homoplasmic and heteroplasmic frequencies the mtDNA criteria read
  (`gnomad_mt_af_hom`, `gnomad_mt_af_het`, `helix_af_hom`, `helix_af_het`).
- `in_silico`: CADD (`cadd_phred`), REVEL, SpliceAI (`spliceai_max`), SIFT, PolyPhen and the
  AlphaMissense class and score.
- `gene_constraint`, for a missense variant only: the gene's gnomAD missense Z that PP2 reads
  (`gene_missense_z`) and its source (`gene_missense_z_source`).

Every field is frozen, `null` when empty, so a value that appears later is a change. A value JSONB
cannot hold (NaN, infinity) is frozen as `null`.

**PP2's missense Z.** A missense variant has `missense_variant` among its consequences, as the
dialog decides for PP2 (`reads_gene_missense_z`). PP2 reads the missense Z for no other variant, so
for those the group is left out and the gene's profile is not read (the owner's decision,
2026-10-20). The value comes from:

1. the gene's profile (`gene_info`), not the variant's annotation, through the variant list's own
   lookup (`gene_constraint_metrics.fetch_gene_constraint_metrics`) in the profiles of the
   family's assembly, for the gene the variant is served with: the primary transcript's gene id,
   else its symbol (in any letter case), else the variant's first gene;
2. else the VCF's own `MISSENSE_Z`, which the import stores in the primary transcript's
   annotation as `gene_missense_z` and the variant list serves in `annotation_extra`
   (`small_variant_review_acmg.pp2_gene_missense_z`, the owner's decision of 2026-10-28).

The source is `gene_profile` or `vcf`; it is `null`, with the value, when neither holds one.

### Evidence snapshot of an SV or CNV

A structural variant (SV) or CNV has no annotation-set hash in ClickHouse. Its snapshot
(`services/structural_variant_evidence.py`) is taken on every save of a CNV scoring and holds:

- what the CNV classifier reads: the SV's type, the genes it overlaps and their count, the pLI and
  the annotated inheritance. The classifier reads no clinical-CNV, dosage-score or DGV data; the
  analyst scores those criteria by hand;
- the event (caller, chromosome, start, end, length, breakend partner), a hash of its whole
  annotation record, and the time;
- the versions the evidence comes from: those the manifest records for the SV callset
  (`by_modality.sv`, else `pipeline`), the reference assembly and the gene loci. They are recorded,
  not compared.

The drift key is `evidence_hash`, SHA-256 over the evidence. Genotype calls are not held. A
scoring for an SV not in the family's data gets 404; one saved for a family without an assembly
(no SV storage to read) is stored without a snapshot.

**One SV, several callers.** Several callers can call one event under one SV id (an uploaded SV's
id names no caller, and a NeedlR SV's has the same form); they share its review. Each caller's call
is its own row on the SV page, with its own genes, pLI and inheritance. So the snapshot is taken
from the row of the caller the scoring was made on, which the save names in `source` (the
classification dialog sends the row's caller):

- a save naming no caller takes the SV's one row. When several callers call the SV the row cannot
  be told, so it gets 422 rather than a snapshot of another caller's row;
- a save naming a caller that does not call the SV gets 404.

The snapshot names its caller (`evidence.source`) and holds that caller's genes, pLI and
inheritance; the drift check compares it with that caller's row.

### Clinical audit trail

Written in the same transaction as the change it records, so it cannot drift from the data. Each
family has its own chain; three system chains hold what belongs to no family.

| Action | Written by |
| --- | --- |
| `classification`, `tags`, `note` | a review save of a small variant, SV, CNV, repeat locus (`repeat:<locus_id>`) or Paraphase region (`paraphase:<gene_symbol>`) |
| `compound_het_pair` | a save that records, changes or breaks up a compound-het pair |
| `pedigree` | a change of the pedigree or its members |
| `phenotype` | every change to a family's phenotypes |
| `family_deleted` | a family's deletion |
| `annotation_manifest` | an admin's manifest replacement, with the old and the new manifest |
| `panel_applied` | a gene-panel version's first use (*Gene panels applied*, below) |
| `sign_out` | a sign-out (*Sign-out*) |
| `nipt_artifact_added`, `nipt_artifact_updated`, `nipt_artifact_removed`, `nipt_artifacts_auto_seeded`, `nipt_artifacts_imported` | every NIPT artifact-list change, on its own chain (`system:nipt-artifacts`); an import of the NIPT-M pipeline's recurrent table names the file and every allele it listed |
| `repeat_count_correction_recorded` | every record of a repeat locus's count correction, on another (`system:repeat-loci`): the locus, and the record before and after (its count correction, `count_offset`, and its reason) |
| `import_delivery_incomplete`, `import_delivery_ready`, `import_delivery_needs_attention`, `import_delivery_checked_again`, `import_delivery_superseded`, `import_delivery_gone`, `import_delivery_added`, `import_delivery_revised`, `import_delivery_dry_run`, `import_delivery_queued`, `import_delivery_refused`, `import_delivery_imported`, `import_delivery_failed`, `import_delivery_offered_again`, `import_delivery_reopened`, `import_delivery_overwrite_failed`, `import_delivery_changed_after_import`, `import_delivery_archived`, `import_delivery_change_resolved` | every change of a delivery's state and every correction of its package, on the third (`system:import-inbox`): the watcher's by `system:import-watcher`, an addition by hand, a correction and a decision by the person who made it ([data-import.md](data-import.md#watched-import-folders)) |

An unchanged re-save writes no event; a save that clears or deletes a review is recorded like any
other change.

**`classification`.** For a small variant: the ACMG class, the accepted criteria, the point total,
the VUS tier, every stored criterion (its strength, whether accepted, its evidence text, whether an
automatic suggestion), and the review's classification text as stored (the label the review dialog
writes with the class tag, or a text sent through the API alone). A change of that text is
recorded like a change of the ACMG record, and the summary names it after the class
(*Classification text VUS - class 3 → Likely pathogenic - class 4*). A quick tag toggle
(*Review*, *Exclude*, *Report*) sends the text back as stored, so it writes a `tags` event and no
`classification` event; the review dialog changes the text only when another class is chosen.

For an SV or CNV: the reviewer's classification and the CNV (ClinGen) scoring (class, kind, point
total, the accepted criteria with their points, every stored criterion in the same detail). SV and
CNV events carry `metadata.modality = "sv"`, so an SV id is never read as a small variant's.

Every `classification`, `tags`, `note` and `compound_het_pair` event of a small variant, SV or CNV
review names the family's analysis whose review it changed (`metadata.analysis`; '' for a review
made while the family held none, *Reviews per analysis*, below). The trail serves it as the
event's `analysis`, '' as null. A repeat-locus or Paraphase-region review is the family's for one
assembly, not one analysis's, so its events name no analysis (`metadata` holds the `modality`,
the `assembly_id` and the `evidence_digest` of the calls it rests on) and the trail serves their
`analysis` as null.

Any change to the record writes an event, even when the class stays, and the summary names the
change (*ACMG criteria updated (…): PM2 moderate → supporting*). Points are recorded as the
database reads them back, so a total of -0.0 is stored as 0 and the chain still verifies.

**`compound_het_pair`.** The review of a compound-heterozygous pair, given on one variant's review
with its partner. A save that records, changes or breaks up a pair writes one event per changed
pair, with the pair before and after (partner, gene, classification, tags, note, phase). It is
filed under the variant whose review held the pair: the saved variant or, for a pair the save
broke up by pairing either variant with another, the variant that held it. The summary names the
partner (*Compound-het pair with 1-200-C-T: classification Likely pathogenic → Pathogenic*);
`metadata.compound_het_group_id` names the pair's group.

**`pedigree`.** A change of the pedigree or members:

- its creation (*Pedigree recorded with 3 members: …*);
- an edit of the structure or of members (sex, role, affected or carrier status, parents, a rename,
  a member made inactive);
- a package import that changes a member's affected or carrier status;
- an admin's deletion of a sample with its data.

It holds each changed member and, when they changed, the couples, links of unknown degree and
same-individual links (two samples of one person), before and after. Its metadata names the structure version the change stored, with its hash and the
change's reason. An edit that leaves the pedigree as it was writes none
([family-member-management.md](family-member-management.md#what-is-recorded-of-a-change)).

Structure versions are numbered per family row, so a family made again under a deleted family's ID
numbers its own from 1 again. Its events then also name how many structure versions the deleted
families under the ID stored (`metadata.deleted_family_versions`): its first event in its summary
(*Pedigree recorded with 3 members: …. Made again under this family ID, after 2 structure versions
of a deleted family: its own start again at 1.*), and its deletion with its latest version
(`before.deleted_family_versions`). So the ID's trail names each version once. A family never made
again carries no such count.

**`phenotype`.** A phenotype edit writes one event per HPO term added, changed or removed: the
member, the term and its label, and the status, onset, evidence, source and note before and after
(none before an added term, none after a removed one). The summary names the change (*Phenotype
changed for S1: HP:0001250 Seizure: status present → absent*).

An import that changes a family's phenotypes writes one event per changed member, as the user who
ran it, in the change's transaction and under the edit lock
(`hpo_service.record_imported_phenotype_changes`):

- a package import (*Phenotypes imported for S1 by a package import of FAM1 (phenotypes.tsv):
  added HP:0001250 Seizure (present)*);
- the restore of a failed overwrite (*Phenotypes put back for S1 after a failed package import of
  FAM1: …*);
- a failed import that removes the family it created;
- a PED upload or the Family Builder replacing a family (*Phenotypes removed for S1: the family
  was replaced by a PED upload (FAM1.ped): …*);
- an administrator deleting a family or a sample with its data (*Phenotypes removed for S1: an
  administrator deleted the sample with its data: …*, metadata `source: admin_deletion`).

Before and after hold the member and the annotations the change touched
(`{"sample_id": …, "phenotypes": […]}`, each as an edit's event holds it): before, those altered or
removed, as they were; after, those added or altered, as they are (none before when it only added,
none after when it only removed). The summary names five changes at most; the metadata names the
package (its folder) or uploaded file, the phenotype files and the import job. A family deletion
writes these events just before its `family_deleted` event; a sample deletion, before its
`pedigree` event. A member removed from the pedigree keeps its phenotypes: its sample stays,
inactive.

**`family_deleted`.** A deleted family's last event, for an admin's deletion with its data
(Admin → Data), a PED upload or hand-made family that replaces it with overwrite, or a package
import that created it and imported nothing; `metadata.source` says which. It holds who deleted it
and when, its samples by ID, its latest structure version, and what went with it: the variant
reviews, repeat calls, Paraphase results, interval tracks and raw files Postgres held, and for an
admin's deletion the small and structural variants counted in ClickHouse (*Family deleted with its
data: 3 samples (F1, M1, P1), 1204 small variants, 3 structural variants, 5 variant reviews*).

- An admin's deletion requires a reason (the owner's decision of 2026-10-27): the dialog asks it,
  and `DELETE /api/admin/families/{family_id}` without `{"reason": …}` in its body gets 400. The
  event holds it as `metadata.reason` and ends its summary with it (*… 5 variant reviews. Reason:
  duplicate of F2*). The other deletions, which their `source` explains, ask none.
- It is written in the deletion's transaction, before anything of the family is deleted (an admin's
  deletion) or before the family row goes, so a failed deletion records nothing.
- The family's chains (clinical audit events, sign-outs, structure versions) stay, unlinked from the
  family row and verifiable by the family's ID. A family made again under that ID (a replacement
  with overwrite does so at once) writes on the same three chains, so each chain of a family ID
  covers every family made under it.

### Gene panels applied

REQ-CARR-003, `services/panel_use_audit.py`. The first time a search of the family's data applies a
panel version, it writes a `panel_applied` event before it runs. Such searches are the small-variant
and SV lists and their CSV exports, the NIPT variant list and the NIPT coverage. The event holds the
panel's id, name, local version and gene count, and its source's release: for a PanelApp panel,
PanelApp's panel id, its version of the panel, the date PanelApp gives that version, the confidence
levels imported and when CoGA imported it; for the Mendeliome, the Monarch release.

- One event per panel version and family. A version already recorded writes nothing; the check runs
  again under the family's audit-chain lock, so two searches at once record it once.
- A panel edited or imported again (each such version is archived in `gene_panel_versions`), or a
  PanelApp version imported over the same genes, is recorded again at the next search that applies
  it.
- A search whose event cannot be recorded fails with it.
- A panel applied in the variant explorer, which searches across families, is recorded for no
  family.

The HTTP audit log cannot record the panel instead: it keeps only the query's keys by default
(`AUDIT_LOG_QUERY_STRING_MODE=keys`), writes in batches after the response and can be switched
off, so it cannot anchor a signed record.

### Reading the trail

`GET /families/{family_id}/clinical-audit` returns a family's trail a page at a time, newest first,
in one fixed order (`created_at`, then `id`: the chain's order, reversed): 200 events by default, at
most 500 (`limit`). A viewer, and a reader in the family's project, are refused it
(`access_control.require_audit_trail_access`).

- `before`, the id of the previous page's last event, reads the events recorded before it, so an
  event written meanwhile is neither repeated nor skipped. An id that is not an event of the
  family's trail gets 404.
- `total` is the trail's event count, read in the same statement as the page; `has_more` says older
  events remain.

The report shows the trail as the clinical audit trail. When it shows only the newest events it
says so, on screen and atop every printed page, and **Show older events** reads the next page.

### Reviews per analysis

A review (classification, tags, note, compound-het pair, ACMG record, CNV scoring) is the review
of one of the family's analyses (the owner's decision of 2026-11-20;
`services/analysis_reviews.py`). A family may hold several analyses on the same DNA, short-read and
then long-read WGS, each signed out on its own, and the report in the EPD refers 1:1 to one
analysis's signed record. So a variant reviewed in the short-read analysis is not in the long-read
analysis's record unless it is reviewed there too, and a variant reviewed in both holds its own
review in each.

- **Stored per analysis.** Each review row names the analysis it was made in
  (`small_variant_reviews.analysis`, `structural_variant_reviews.analysis`): one review per family,
  analysis and variant. A review made while the family held no analysis is stored under none ('').
- **Named on each save.** A review save names the analysis (`analysis`) when the family holds
  several analyses with small variants of their own (for an SV review: with structural variants of
  their own): 422 without it, and 422 for an analysis the family does not hold. A family of one
  such analysis, or of none, need name none.
- **What an analysis reads.** A reading for analysis X (the variant list and its review filters,
  the review summary, the drift check, the sign-out) reads, per variant, X's own review, else the
  review stored under no analysis the family holds (the shared one). Another analysis's review is
  never X's.
- **What a save writes.** A save in X always writes X's own review: when X has none, it is made
  from the review X read (the shared one, its ACMG record and compound-het pair included) and the
  save's values. The shared review stays as it is, for the other analyses. A save that clears X's
  review keeps it empty while a shared review of the variant holds something, so X reads the
  variant as not reviewed; an empty review is no review to every reader. A compound-het pair is
  made and broken up within one analysis.
- **Read across analyses.** The Variant Explorer shows, for each family, the tags and classes of
  every review in effect in one of its analyses (each analysis's own, and a shared one an analysis
  still reads). A tag-catalogue change, a family's tag list, a structure change (a member renamed or
  removed) and the family's data clear act on the reviews of every analysis.

### Concurrent review saves

Two people saving the same review cannot silently overwrite each other.

- A save carrying the `expected_updated_at` it loaded gets 409 `review_conflict` when the review
  changed in between. Saves of one variant's review wait for each other (a transaction-scoped lock
  per variant), so the check sees every earlier save, and no save is stamped with an `updated_at`
  earlier than the version it replaces.
- A compound-het pair save (`compound_het` with a `partner_variant_id`) also writes the partner's
  review, and breaking up a pair rewrites the review of each of its variants. Each review is locked
  as its own save would lock it, all in one order (so two pair saves cannot wait on each other), and
  read only under its lock: a partner review saved meanwhile is kept, never overwritten from an
  older copy.
- The partner's version is checked like the variant's own. A pair save carrying
  `compound_het.partner_expected_updated_at` (the partner review's `updated_at` as loaded, null for
  none) gets the same 409 `review_conflict`, which names the partner (`variant_id`) and carries its
  current review. A pair that changed between the save's look-up and its locks is refused the same
  way.
- A refused save writes nothing, so it leaves no clinical audit event; the request log records the
  409 as for every refused save.

## Evidence drift

`GET /families/{family_id}/classification-drift` compares each classification's snapshot with the
variant's current annotation, read as a classification saved now would freeze it.

| Status | Meaning |
| --- | --- |
| `current` | the annotation-set hash and every frozen value are unchanged |
| `drifted` | the hash changed (a new annotation release; ClinVar and version shown from → to), or a frozen value did, even under the same hash |
| `unknown` | nothing is seen to have moved, but a hash is missing or a frozen group cannot be read, so the match cannot be verified |
| `variant_missing` | the variant is no longer in the data |

- `changed` names what moved: `clinvar`, `transcript_id` (the primary transcript), each
  consequence, frequency or in-silico field by name (`effect`, `impact`, `lof`, `gnomad_af`,
  `revel`, …), `gene_missense_z`, `gene_missense_z_source` when PP2 reads the missense Z from the
  other source (the gene's profile gained or lost one: *gnomAD missense Z 4.20 (VCF) → 3.52 (gene
  profile)*), and `annotations` when the hash moved.
- `evidence_from` and `evidence_to` hold the transcript, consequence, frequencies, in-silico scores
  and missense Z with its source, as frozen and as now.
- Each frozen value is compared, numbers by value (JSONB keeps a number's digits, not whether it
  was an integer).
- A stored snapshot that is not a JSON object (the writer never stores one) counts as one that
  froze nothing: `unknown` (or `variant_missing`).
- The gene missense Z is outside the hash, so a gene-profile refresh can move a missense variant's
  classification. A variant that is not missense froze none, so its gene's profile never makes it
  drift. A variant classified as missense and no longer called missense names its consequence and
  missense Z as gone (*gnomAD missense Z 2.95 → n/a*); one that became missense names its
  consequence only. The check reads the missense Z of all the family's missense variants in one
  query.

**SVs and CNVs** are under `structural`. Each CNV scoring's snapshot is compared, whole and by its
hash, with the SV as the SV page reads it now, in the row of the caller the snapshot names
(`evidence.source`); another caller's call of the SV is other evidence, not a change to this one.

- `drifted` names what moved in `changed` (`gene_symbols`, `pli`, `inheritance`, `sv_type`,
  `locus`, or `annotations` for any other annotation), with the evidence from → to.
- `variant_missing`: that caller's call is no longer in the data, even while another caller still
  calls the SV. The drift banner and the signed version name that caller (*its needlr call is no
  longer present*).
- Frozen evidence that cannot be read, or that names no caller, is `unknown`, never `current`.

At sign-out, a reported classification without a snapshot, of a small variant or an SV, also
counts as drift (`no_snapshot`).

**Repeat and Paraphase reviews** (REQ-TRACE-015,
[repeat-paraphase-review-design.md](repeat-paraphase-review-design.md)). A review of a repeat locus
or Paraphase region that carries a classification or the Report tag freezes the calls it rests on
(`services/finding_review_pg.py`):

- a repeat locus: its entry as the signed record's `repeat_expansions` section freezes it (each
  member's call, alleles, near-boundary warnings, the locus's thresholds and QA's counting-units
  record);
- a Paraphase region: its entry in `paraphase`, without each result's load time.

A save that sets or changes the classification, or adds the Report tag, freezes them again. A save
of only tags or the note keeps them, so it cannot clear a change without a new judgement. A re-import
with other counts or copy numbers, a new catalogue threshold, or a count correction at the locus
each changes what a review froze. The repeat and Paraphase tables
(`GET /families/{family_id}/repeat-expansions`, `/paraphase`) give each review's `drift`:
`unchanged`; `changed`, with `changes` listing each difference as `path: before → after`, a
member's call named by its sample (`calls[PROBAND].alleles[1].repeat_count: 34 → 38`); `not_found`
(no call at the locus or region now); `no_snapshot`; or `unverifiable`. A review with only tags or
a note is not checked.

## Sign-out

A sign-out is the analytical sign-off (*What sign-out is*, above).
`POST /families/{family_id}/report/sign-out` signs out one analysis of the family (`analysis_type`;
*One sign-out per analysis*, below): it runs seven gates (*The gates*, below), then freezes a
snapshot, which the clinical report in the EPD refers to by its report code (*The report code*).

### What a sign-out freezes

A sign-out of one analysis of a family that holds several freezes that analysis's part of each
item below (*One sign-out per analysis*).

- **The analysis** (`analysis_type`): the analysis the record closes, by its key as the family's
  `analyses` name it (`short_read_wgs`, or a NIPT with its pregnancy: `monogenic_nipt:ZW1`,
  `conventional_nipt:ZW1`), or null for a family that holds none. The content hash covers it.
- **What the record is** (`record_statement`): *This record is the analytical sign-off in CoGA,
  which closes and freezes the analysis; the medical sign-off of the patient report happens in
  the EPD, which refers to this record by its report code.*
- **Provenance:** the analysis's merged manifest (its own versions, never another analysis's) and
  the reference assembly; the software version and git commit that produced the snapshot.
- **Reported variants:** the small variants tagged `report`, each with its classification, ACMG
  criteria and evidence snapshot; the reported SVs and CNVs with their classification, CNV criteria
  and evidence snapshot.
  - A small variant is tagged `report` when its review or its compound-het pair review holds the
    tag: the review-tag filter's rule, by which the live report and the NIPT page's *Show reported*
    list the reported variants. A variant its pair review reports (only the API sets a pair
    review's tags) is frozen with that pair review (`compound_het`: partner, gene, the pair's
    classification, tags and note, and its phase), shown in the signed version under *Compound-het
    pair*. A variant only its own tag reports is frozen without it.
  - Each reported small variant and SV also holds its review's classification text as stored
    (`classification`) and the class its review shows (`class_shown`, null when the review shows
    no class): the family page's one rule (`review_pg_utils.review_class_label`: a bare class word
    in the text, else the most severe ACMG class tag, else the text), worked out by the backend at
    sign-out from the review as stored, never taken from the request. The ACMG record and the CNV
    scoring are frozen beside it, so a class the analyst chose over the ACMG score keeps that score
    in the record.
- **Repeat and Paraphase reviews** (`repeat_expansion_reviews`, `paraphase_reviews`;
  REQ-TRACE-017): those in the family's assembly that carry a classification or the Report tag,
  each with its key, classification and label, tags, whether reported, note, who saved it last and
  when, the calls it froze, and whether they still are the family's (`drift`); those whose calls
  are not (`finding_review_drift`); and each repeat locus and Paraphase region with an abnormal call
  and no review (`unreviewed_findings`, gate 7). The `repeat_expansions` and `paraphase` sections
  still freeze every call: a review marks a finding, it removes none.
- **Drift and QC:** the drift state (`drift` for the small variants, `structural_drift` for the SVs
  and CNVs), the sample-integrity QC result, and the sequencing QC with the cut-offs it was judged
  against (`sequencing_qc`). The sequencing QC is kept per analysis (`analyses`, keyed as the
  family's package records are), each judged against its own analysis's cut-offs. For each analysis
  the record holds the profiles its cut-offs were read from, in order (`profiles`: key and label);
  each metric that had a cut-off, with its warning and error value and the profile it came from
  (`thresholds`: `warn_value`, `error_value`, `profile_key`); and each sample's values and verdicts
  (`samples`). The record holds the QC of the analysis it closes and of a package that named no
  analysis; a record of a family of one analysis or none holds every analysis's.
- **Import state:**
  - `import_incomplete`: null when the family's data imported completely; otherwise the failed and
    the imported datasets, when, the import job, the job of each failed dataset (`failed_jobs`) and
    the analyses its failures are of (`failed_analysis_types`: each the key of the package record
    of the import it failed in, null for a single-file upload's);
  - `import_unfinished`, the imports that stopped part-way: empty when there are none; otherwise
    for each its import job, when it began, the analysis it is of (`analysis_type`), its datasets
    and those it finished. For a failed overwrite whose restore did not finish, that is every
    dataset of the family still to import again, none finished; for a single-file upload or a
    deletion that did not finish, no job and no analysis, under its `upload-…` or `deletion-…` key.
  - A record of one of a family's analyses holds only that analysis's marks and those of no
    analysis, and the entry of a failed overwrite whose restore did not finish, whichever
    analysis's import it was (*One sign-out per analysis*, below).
- **Package folders** (`package_source_changes`, gate 6): a family holds one package record per
  analysis it was imported for (the monogenic and the conventional NIPT of each pregnancy each have
  their own), and CoGA serves each analysis's source files from the folder its record names. The
  list is empty when each such folder is as its analysis's reference delivery was imported, or the
  family was not imported from a delivery. Otherwise it holds one entry per analysis whose folder
  is not, in the order of the analyses: the analysis (`analysis_type`, its record's key), that
  delivery (its id), the name of the folder, the fingerprint the reference was imported with and
  the folder's now; what was found (`changed`, with the paths added, removed and changed in size or
  modification time, each list to 50 paths with its count, none named when `listed` is false; or
  `gone`, the source files no longer available); when the watcher found it, or `found_at_sign_out`
  when the sign-out's own check did; and, for a gone folder a superuser confirmed as deliberately
  gone, `archived` (when, by whom and why)
  ([data-import.md](data-import.md#after-the-import-a-package-folder-that-changes)). It is read
  outside any savepoint: a read that fails stops the sign-out, never lets the gate pass. The
  sign-out check compares the list without `detected_at` and `found_at_sign_out`.
- **What the analysis looked at:** the sections below.
- **Who and when:** the signer, the time, and any acknowledgement with its reason.

### What the analysis looked at

`services/signed_record_sections.py` builds one section each. Each is read inside a savepoint. One
that cannot be read is frozen as `{"unavailable": <a fixed sentence>}`, never as an empty section,
and named in `not_captured` (a part of `reference_data` on its own). The error's own text, which
may quote what it read, is not frozen.

**`variant_descriptions`** (REQ-TRACE-007). Each reported variant as the analyst reviewed it, read
from the family's variant lists as the report page reads them (`review_tag=report`), one entry per
callset or caller that calls it.

- A small variant: locus, reference and alternative allele, gene, transcript (and whether MANE
  Select), HGVS g. (VEP's HGVSg, null where the annotation has none), c. and p., consequence and
  impact, and each sample's genotype with depth, genotype quality, allele depths and allele
  fraction.
- An SV or CNV: type, copy change (loss or gain), locus, length, breakend mate, genes, cytoband and
  caller, and each sample's genotype, copy number, supporting reads, quality and filter.

**`gene_panels`** (REQ-CARR-003). Each gene-panel version the family's searches applied, as its
`panel_applied` event recorded it (above): name, id, local version, gene count, source and the
source's release (PanelApp's version of the panel, or the Mendeliome's Monarch release), when CoGA
imported that release, and when and by whom it was first applied.

**`reference_data`**, beside the manifest's modules:

- the gene reference's releases for the reported variants' genes: per source, the release and
  checksum of the file that answered for each gene, and the genes it has no record of;
- the clinical CNV knowledgebase loaded for the assembly: its source, the release its source
  states, for a rebuild the ClinGen recurrent CNV file it read by release and SHA-256, and when it
  was loaded.

PanelApp gives each panel its own version, which `gene_panels` holds.

**`repeat_expansions`.** Every TRGT call of the family's samples, with its status at sign-out (as
the repeat page classifies it), its alleles and the thresholds behind it; and the catalogue it was
classified against: how many loci, from which sources (STRchive, CoGA's built-in rows) and a
SHA-256 digest of every locus's thresholds, since STRchive's file states no release.

- Per allele: the count classified (`repeat_count`: TRGT's count with QA's count correction for the
  locus, CLIN-57), TRGT's own count (`trgt_repeat_count`), and its near-boundary warning as the
  repeat page serves it (`count_unit_warning`, `services/repeat_count_units.py`: the count, the
  nearest count within the margin on each side that reads another status, and the margin; null
  when there is none). The allele's own status is the one on its side of the boundary.
- Per locus: QA's record of TRGT's counting units there, as `repeat_locus_count_units` held it
  (`count_units`: the correction `count_offset`, the `reason`, who (`updated_by`) and when
  (`updated_at`); for a locus without a record `count_offset: 0` and the rest null), with the
  margin in force (`count_unit_margin`).

The counts, statuses, warnings and record never disagree: the family table reads QA's record in the
statement that reads the calls, and the record frozen is the one they were classified and marked
with. A locus corrected since reads as a change.

**`paraphase`.** Every Paraphase result of the family's samples, with its status as CoGA reads it at
sign-out, its copy numbers, the region's status rule and copy-number norm, and the catalogue of
regions (how many, and a digest of their rules).

**`pgt_analysis`** (REQ-PGT-005; the owner's decision of 2026-10-14). The PGT analysis at the
family's region of interest (ROI), the object the family page reads its embryo calls from
(`services/pgt_analysis.py`, `GET /families/{family_id}/pgt-analysis`):

- the ROI and the span of haplotype blocks read (the ROI and 1 Mb on each side);
- the inheritance model as recorded and as applied (without one: AR when a member is marked a
  carrier, else AD);
- each member's role, sex, affected and carrier status, from which the disease haplotype is
  inferred, and the disease haplotype(s) found at the ROI (side, homolog, kind) or that none was;
- the parents' phase corrections near the ROI;
- per embryo: its recorded sex; its call (affected / at risk, carrier, unaffected, uninformative;
  none when the family has no ROI); whether an uninformative call is due to its own missing data at
  the ROI; both calls when an X-linked recessive call depends on an unrecorded sex; the homologs it
  carries at the ROI with their blocks' extent; and the block boundaries inside the ROI or within
  250 kb of it (recombinations);
- per embryo, its phasing QC at the ROI, counted over the same window as the ROI marker review:
  informative sites, Mendelian errors and their rate, the sites read on each parent's side (in all
  and on each ROI flank), and the share of each side's markers off one homolog, or two either side
  of one crossover;
- the parental sides whose markers mix both of that parent's homologs (`mixed_homologs`): the
  embryo is then not called, since it may carry both, as in a trisomy, which its blocks cannot show;
- that QC's verdict against the thresholds of `services/pgt_embryo_qc.py` (`qc_verdict`: `pass`;
  `warning`, naming each measure that does not meet its threshold, with its value and the
  threshold; or `not_counted` when no QC was counted, which is no pass), with the parental sides
  whose markers were checked (the sides the call reads);
- its inputs: the haplotype file each member's blocks were built from and its load time; the number
  of blocks read and a SHA-256 digest of them as read (lineage tags included); whether the blocks
  were built for another pedigree or other sexes than the family's (`blocks_outdated`: every embryo
  is then uninformative, and the signed version says why); the distances the calls and warnings
  use; and the QC thresholds in force (`rules.qc_thresholds`).

Only a PGT family (one with an embryo) holds the key. A monogenic NIPT family none of whose embryos
has haplotype blocks of its own holds none either (`pgt_analysis.embryo_has_haplotype_blocks`): the
import reads a child of unknown sex and unknown or unaffected status as an embryo, as a NIPT PED
usually gives the fetus, and the NIPT trio takes that embryo for the fetus; but the fetus has no
sequencing column, so no blocks and no call. The blocks an embryo call reads are the narrowest input
that makes the section a PGT analysis: a NIPT family with them keeps it, as the family page calls
such an embryo.

The backend makes the embryo calls from the stored haplotype blocks
(`services/pgt_embryo_calls.py`); the family page shows the calls the backend serves, and sign-out
freezes the same object. So the record holds the calls the analyst saw, made by one implementation
from stored data. The haplotype tracks also draw the risk lane and each member's risk state in the
browser (`frontend/src/lib/haplotypeRisk.ts`). Both implementations answer the cases of
`frontend/src/lib/__tests__/fixtures/pgtEmbryoCallCases.json` alike, which
`test_pgt_embryo_calls.py` and `haplotypeRisk.test.ts` both run. The live family report does not
show the embryo calls; its signed version does.

**`nipt`** (a monogenic NIPT family only; `services/signed_record_nipt.py`; REQ-NIPT-001, -004,
-006, -010 to -013, -015). The NIPT analysis as the NIPT page computes it at sign-out, by the
backend's code (`nipt_service`, `nipt_quality_checks`), never the browser:

- the trio (`samples`);
- the fetal fraction with its interval, site count, per-site median and verdict, and the de novo
  window;
- the category counts, the filter funnel and why cfDNA calls failed the quality filter;
- the quality checks, each with its verdict and the sentence that says why (`checks`: the QC gate
  on the expected fetal reads, paternity, the fetal sex from the father's X alleles and from the
  plasma's chrY coverage, the maternal plasma sample's sex profile). The QC gate
  (`expected_fetal_reads`, REQ-NIPT-021) holds what it read (`median_depth`, `fetal_fraction`,
  `expected_reads`) and the bound it applied (`metric`, `warn_value`, `error_value`, and
  `provisional` while QA has not set it);
- the coverage of every capture target and each gene with a weak target (`target_coverage`, null
  without a per-target coverage table), with the depth that gives the QC gate's fetal reads at the
  estimated fetal fraction (`fetal_read_depth`) and the targets below it (`below_fetal_read_depth`,
  in all and per gene);
- without a per-target table, the coverage check's notes over the plasma's coverage tracks
  (`coverage_notes`; owner decision of 2026-10-28), word for word as the NIPT page shows them, each
  naming a track the check does not read as depth and why, with what the check ran for: `gene` and
  `panel`, both null, the `regions` it read the tracks over, and the `notes`. Null with a
  per-target table, whose targets the check reads instead;
- each variant tagged `report` as the NIPT variant list classifies it (`variants`): category,
  maternal state and fetal inheritance, observed and expected allele fraction, confidence,
  fetal-inheritance probabilities, flags and quality-filter failures, the plasma's and the father's
  reads, its recurrence in the assay's other cfDNA samples (the counts, not how many other samples
  there were), and for a de novo candidate in the fetal window its triage, including one the de
  novo view leaves out as recurrent;
- each reported variant it does not classify, with why (`not_classified`: not in the data, on the
  artifact list, no plasma call);
- the recessive fetal status of the genes the reported variants make both parents carriers in (and,
  for a son, of the X-linked genes his mother carries), with its probabilities (`status`, `risk`,
  `carrier_probability`, `unaffected_probability`, `mode`), as the recessive view computes them over
  them (`recessive_genes`);
- the analysis's diagnostic gene panel (`diagnostic_panel`, REQ-NIPT-019): the panel's id, name,
  version, source release and gene count, and a digest of its gene list (`genes_digest`); null when
  the analysis had none, which stops the sign-out until a reason is recorded
  (`nipt_signout_concerns`). And the coverage of its capture targets (`panel_target_coverage`): the
  panel's gene count, the totals over its genes' targets, each gene with a weak target, each gene
  with a target too thin for the QC gate's fetal reads (`genes_below_fetal_read_depth`) and each
  gene without a target (not tested); null without a panel or without a per-target table. The record is
  rebuilt from the saved panel and its current version, so a panel edited since the sign-out shows
  as a change;
- the locked diagnostic candidates of the diagnostic panel (`diagnostic_candidates`,
  REQ-NIPT-020): the criteria with their version (`criteria`), the scope (`panel_id`, null: every
  variant), whether the scope held more variants than the analysis classifies (`capped`), and each
  view's candidates (de novo, paternal, maternal, the recessive genes and their alleles), each with
  what the criteria read (frequency, impact, SpliceAI, a strict ClinVar call, its count in the
  assay's other cfDNA samples), its confidence and its flags. A count that grows with the assay can
  change the list, which the sign-out check then shows;
- what the analysis ran with. `inputs`: the per-target coverage tables with their file, number of
  targets and load time, and the assay's artifact list with its scope, number of alleles, a SHA-256
  digest of its alleles with their recurrence counts, how many it left out of this analysis, and
  which stayed in and why (`clinvar` or `common` by the family's annotation, `paternal_carrier`
  when the family's father carries the allele). `model`: the R NIPT-M v0.7.3 validation, its
  constants, the cfDNA quality filter and the father's genotype classes, and the fetal-fraction
  rules.

The coverage check depends on the gene or panel of the NIPT page's search, which the sign-out is
not told. So the sign-out runs the page's own code (`nipt_service.nipt_coverage_notes`) over the
analysis's diagnostic panel's genes, or, without one, as the page runs it without a gene or panel,
over the family's region of interest (without one, no region and so no note), as `target_coverage`
holds the check of every capture target. A family that is not a
monogenic NIPT family holds no `nipt` key. A trio that cannot be resolved (no active father, or no
maternal plasma sample) is frozen as unavailable, with a sentence of its own.

**`cnipt`** (a family that holds a ccnv output only: a conventional NIPT;
`services/signed_record_cnipt.py`). Each maternal plasma's result as the cNIPT page shows it, the
same object from the same code (`cnipt_service.family_cnipt_results`, `cnipt_result`), in sample
order (`samples`):

- the status (`result`, or `no_result` with its reasons) and each target's verdict with its reasons
  (each autosome, `sex_chromosomes`, `segmental`) and whether the intended purpose claims it
  (`claimed`: chromosomes 21, 18 and 13 and the sex chromosomes);
- the findings the rule looked at, each with ccnv's values (log2 ratio, z-score, implied fetal
  fraction, discordance, origin), whether it is read as an aneuploidy or a segmental CNV, and its
  verdict;
- the fetal fractions (the effective one the rule reads and every other estimate), the fetal sex,
  the sex-chromosome call, the pregnancy, the limits of detection, ccnv's read metrics and warnings;
- the bounds the rule applied, each QA's or provisional (`bounds`), and what of the rule is fixed in
  code (`rule`);
- what it was computed from (`inputs`): the SHA-256 digest of the QC object as the import kept it
  (`qc_digest`; the file's name is `qc_file`), the number of segments read and a digest of them
  (`segments_digest`), and the segments files the track was loaded from, with their rows and load
  time (`segment_files`). The plasma's bins are in `coverage_tracks`.

A family without a ccnv output holds no `cnipt` key.

**`coverage_tracks`** (owner decision of 2026-10-20). Every coverage track of the family's members,
one per sample and caller (`source`: `web` for a coverage BED, an upload's or a package's;
`wisecondorx`, `qdnaseq`, `hificnv`, `ccnv`). For each:

- what it holds as the depth readers read it (`content`: `read_depth`, `log2_ratio` or `unknown`),
  by their one rule (`coverage_read_depth.coverage_track_content`) over the same records: for a
  coverage BED, what it declared when loaded; for another caller's track, what its import's
  transform made of its file;
- each file it was loaded from (`files`: name, rows, load time, and from where: `upload` for a
  single-file upload, `package` for a package import's dataset, null when its record does not say),
  with what a coverage BED declared and the transform another caller's import recorded.

Only a read-depth track is read as depth (by the mtDNA coverage and its `mtdna.mean_depth` verdict,
and by the NIPT coverage check without a per-target table), so the record says which kind of
coverage a depth check rested on. The section is read from the tracks' records in Postgres, as the
PGT and NIPT sections read their inputs. Every family's record holds it, as `{"tracks": []}` for a
family without a coverage track, so a track loaded after sign-out reads as a change. A monogenic
NIPT family's per-target coverage tables are another track type (`target_coverage`), held in
`nipt` (`inputs.target_tables`), as are, without them, the coverage check's notes over these tracks
(`nipt.coverage_notes`, above).

**`package_intake`** (owner decision of 2026-11-10). Per analysis the family was imported for, the
delivered package that analysis's last package import came from, as its package record names it
(`families.metadata.package_imports[<analysis>].delivery`), in `deliveries`, in the order of the
analyses: the analysis (`analysis_type`), the delivery's id, the pipeline's run, the SHA-256 of the
pipeline's package file, the package's fingerprint, the revision imported and the SHA-256 of the
package imported. With each, each correction made to that package in CoGA before the import
(`import_delivery_revisions`), in revision order: every field changed, by its path
(`members.PROBAND.sex`), its kind (`pipeline_used` for a member's sex or parents and the couples,
which the pipeline computed with; `laboratory` else) and its value before and after, with the
reason, who made it and when. Every family's record holds it: `{"deliveries": []}` for one no
analysis of which was last imported from a delivery, so an import after sign-out reads as a change.
The family page shows the same corrections, one *Corrected at intake* note per corrected delivery,
naming its analysis.

### A NIPT family's record

A monogenic NIPT family is signed out through the same `POST /families/{family_id}/report/sign-out`
(owner decision of 2026-10-14), from the NIPT report or the report page (of a family that holds
other analyses too, from the NIPT report alone): one signed record per analysis, with the same
gates, version and report code, which also holds the NIPT results (`nipt`, above). Where it is
signed out from does not change what is frozen.

- The Sample QC's NIPT checks stay in `sample_qc` (`paternity_check`, `fetal_sex_check` from the
  father's X alleles, `category_qc_check`, the parents' sex, and their fixed notes); the gene panels
  its NIPT searches applied stay in `gene_panels`.
- The paternity check is frozen with its four counts (the father's homozygous and het alleles seen
  and not seen), not the totals the Sample QC page is served.
- The Sample QC's summary (`application_summary`) is frozen as it read: it says paternity is read
  from the father's alleles in the plasma.
- A NIPT variant whose review, or compound-het pair review, is tagged `report` is frozen in
  `reported_variants` and `variant_descriptions` like any reported small variant, with its
  classification, and in `nipt` with its category, reads and probabilities. The recessive fetal
  status in `nipt` is computed over them, so it equals the NIPT page's recessive view over *Show
  reported*.
- The Sample QC's NIPT checks and the NIPT section read one run of the NIPT analysis
  (`nipt_service.SharedNiptAnalysis`): the same artifact list (which the lab curates apart from the
  family's data) and the same values. A failed run fails both: the Sample QC notes that the NIPT
  cfDNA analysis could not run, and the section is frozen as unavailable.
- The Sample QC runs its NIPT checks in a savepoint, as each section is read. The analysis reads the
  family and the artifact list from Postgres; a database error there is rolled back to the
  savepoint and reads like any other failure of the analysis. The gate then lists the NIPT checks
  among those that could not run, and the sign-out goes on in a clean transaction.

### A conventional NIPT family's record

A family that holds a ccnv output is signed out through the same sign-out, from its report page,
which shows the live cNIPT result above the reported findings (*Conventional NIPT result*): one
signed record per analysis, with the same gates, version and report code, which also holds the
cNIPT results (`cnipt`, above).

- The ccnv calls a reviewer tags `report` are frozen in `reported_structural_variants` and
  `variant_descriptions`, like any reported CNV.
- A plasma without a result, a result that rests on provisional bounds, and a conventional NIPT's
  record that froze no result (a section that could not be read, none, or one without a plasma of
  the analysis) gate the sign-out under the Sample QC's acknowledgement (gate 4 below): signing out such a result is a choice the signer
  makes with a reason, which the record and the audit event keep.
- The bounds are read at sign-out. A bound QA sets afterwards changes the section (the bound's value
  or its provisional mark), so the sign-out check names `cnipt` as changed.

### One sign-out per analysis

A family may hold several analyses (`families.metadata.analyses`), each imported from a package of
its own: the monogenic and the conventional NIPT of each of a woman's pregnancies, or short-read and
then long-read WGS of the same DNA. Each analysis is signed out on its own, and its record freezes
and checks that analysis's data only (the owner's decisions of 2026-11-19 and 2026-11-21;
`services/signout_scope.py`). An analysis is named by its key: its type, and a NIPT's pregnancy
(`monogenic_nipt:ZW1`); a pregnancy's NIPT section (`nipt`, `cnipt`) is its own pregnancy's, its
trio, its fetuses and, for a multiple pregnancy, the note that a fetal result may be present in one
fetus or in more than one.

- **Which analysis.** The request names it (`analysis_type`, the analysis's key). A family that holds two or more
  analyses must name one: 422 without it, and 422 for an analysis the family does not hold. A
  family of one analysis or none need name none: its record names its only analysis, or none, and
  it is signed out whole.
- **One series of versions.** A family's versions are numbered in one series, whichever analysis
  each closes, and the report code is made as for any record (*The report code*). Each snapshot
  names its analysis (`analysis_type`), which the content hash covers.
- **What is the analysis's data.** Each package record
  (`families.metadata.package_imports[<analysis>]`) lists the storage sources its import wrote, per
  kind of data (`sources`: `small_variants`, `structural_variants`, `tracks`, `repeats`,
  `paraphase`; `family_package_records.package_sources`), and its `datasets`. An item belongs to
  analysis X when one of its sources is X's own or is claimed by no other analysis the family holds.
  So the data of an upload, of an analyst's addition or of a package that named no analysis belong
  to every analysis, and each sign-out freezes and checks them, unless an analysis's record lists
  the upload's source. A package's `coverage` dataset outside a pregnancy stores its coverage BED
  under the upload's source `web`, and its record lists `web` and `coverage_table` among its
  tracks whatever files it held (`family_package_records._DATASET_SOURCES`). So once such a
  package's analysis is imported, a track uploaded under `web` (a coverage, APCAD or APCAD PCF
  BED) is that analysis's alone, and the other analyses' sign-outs neither freeze nor check it.
- **Whose import mark.** The analyses of a family share dataset types (both a short-read and a
  long-read package carry `snv`, `qc`, `alignments`), so the dataset an import mark names cannot
  say whose it is. Each mark records the analysis it is of (`analysis_type`): an import's entry in
  `import_unfinished` and each failure of its `import_incomplete` flag, the key of its package's
  record (`unspecified` for a package that named no analysis); an upload's or a deletion's mark,
  none. A mark of X, or of no analysis the family holds, or of none, is X's
  (`SignoutScope.holds_mark`).
- **A record not in the import's shape.** A package record whose `sources` are not in the shape the
  import writes them stops the sign-out (`PackageRecordShapeError`): it does not guess which data
  are the analysis's.
- **Its reviews.** A review is one analysis's (*Reviews per analysis*): a sign-out of X reads X's
  own review of each variant, else the shared one, and never another analysis's.

A sign-out of analysis X freezes and checks:

| Part of the record | X's part |
| --- | --- |
| `reported_variants`, `reported_structural_variants`, their evidence drift (`drift`, `structural_drift`) and their descriptions (`variant_descriptions`) | the variants X's reviews report: X's own review of each variant, else the shared one (*Reviews per analysis*); the drift checks the classifications of those reviews. Each small variant's description, genotypes and evidence now are read from X's own callsets (`analysis_scope.py`): a variant another analysis's callset holds too is described from X's |
| `coverage_tracks` | the tracks of X's sources or of no other analysis's |
| `repeat_expansions` and `paraphase`, with their reviews, `finding_review_drift` and `unreviewed_findings` (gate 7) | each section whole when the calls are X's by the sources they are stored under (`repeat_expansions.source`; Paraphase's one source), or when the family holds none; otherwise the section and its reviews hold no call, as in the record of a family without such calls |
| `pgt_analysis`, `nipt`, `cnipt` | the PGT embryo calls for `pgt` or `monogenic_nipt`, the NIPT results for `monogenic_nipt`, the cNIPT results for `conventional_nipt`; a section of an application none of whose analyses the family holds goes to every analysis |
| `sample_qc` | the QC of X's own application (the conventional NIPT runs no check), of X's members (not a member only another analysis's package lists), on genotypes from a callset of X's own: of several of one kind, the one with calls for the most of them |
| `sequencing_qc` | the sequencing QC X's own import recorded, and that of a package that named no analysis, each judged against its analysis's cut-offs |
| `package_intake`, `package_source_changes` (gate 6) | X's own package record, and that of a package that named no analysis |
| `import_incomplete`, `import_unfinished` (gate 5) | the marks of X's imports and of no analysis: the flag's failures of X or of none, with the time, job and imported datasets of the latest of them, and the entries of X or of none; the entry of a failed overwrite whose restore did not finish (`whole_family`), whichever analysis's import it was, as the restore rewrote the whole family's rows |
| `modules` | X's annotation manifest (*Annotation manifest*, above) |
| `gene_panels`, `reference_data` | whole (`reference_data` for the genes the record describes) |

The gates read this snapshot, so each gate is the analysis's own: a drifted classification, a QC
concern or a failed dataset of another analysis does not stop it. The sign-out check of X compares
X's snapshot as a sign-out would freeze it now with X's latest record, and skips a section neither
holds, so a change to another analysis's data leaves X's check matching.

No two analyses of a family share a storage source: an import that would store data under a source
another analysis's record lists is refused before it writes anything
([data-import.md](data-import.md), *Several analyses in one family*). So "X's sources" and "another
analysis's sources" never overlap, and no import of one analysis replaces, skips or claims another's
data. The family's variant pages read one analysis at a time too (`analysis=`; a family with two
or more analyses with small variants of their own names it, and its SV list one of its analyses
with structural variants of their own, or the reading is refused), so what an analyst reviews is
the analysis's own callset, as its sign-out freezes it.

The family's report pages show its current data whole; a sign-out freezes the analysis's part of it.
The NIPT report signs out the monogenic NIPT, and the Report page every other analysis (*The live
report*, *The NIPT report*, below).

### Versions and the sign-out check

- **Hash and version.** The snapshot is hashed with SHA-256 over a canonical encoding and stored as
  the next version. An amendment is a new sign-out; no version is ever changed. The sign-out is also
  written to the clinical audit trail, naming the analysis it closes (*Report of the short-read WGS
  signed out (v3, report code …)*, with its `analysis_type`). A stored hash is always checked
  against the snapshot as stored.
- **Numbering.** Versions are numbered per family ID: a family made again under a deleted family's
  ID numbers on from the deleted family's last version (its first sign-out is version 3 after two),
  on the same chain. So a family ID and a version name one signed record for good, and the database
  refuses a second record under them (`uq_report_signouts_family_identifier_version`). The family's
  pages list only its own versions; the deleted family's stay on the chain, verifiable by the ID,
  and are not opened through the new family.
- **The sign-out check** (`compare_report_with_latest_signout`) is of one analysis
  (`analysis_type`, named as for a sign-out): it compares that analysis's latest record with the
  snapshot a sign-out of it would freeze now. It reads the one snapshot format CoGA writes: every
  record holds every section and field (a section of one application, that application's families'
  records only). Each section is compared whole, so a section that differs, or that only one of the
  record and the family's current data holds, is changed. A section that neither holds (the NIPT
  results of a family that is not a NIPT family) is not compared. An analysis signed out whole, as
  the family's only one, and checked once the family holds another compares the same sections: its
  own manifest, and repeat and Paraphase sections without the other analysis's calls. The live
  report, the signed version and the NIPT pages say whether the family's data still matches the
  analysis's latest version, and name the sections that changed. A page that signs out a family of
  one analysis reads that analysis's latest version, as the check does, never the latest version of
  another analysis or of none.

Because each section is compared whole, these read as a change since signing:

- a reference module loaded since sign-out, or a new release of one: a change to `modules` (a
  module in the record's `modules` was loaded at sign-out);
- **a PGT embryo's QC verdict**: a verdict or threshold changed since signing;
- **the repeat calls' near-boundary warnings**: a warning a count correction or a catalogue
  threshold added or lifted since signing;
- **QA's count corrections** (CLIN-57): a correction recorded or changed since signing that moves a
  count or a status;
- **the NIPT coverage check's notes** over the coverage tracks (as null too, with a per-target
  table): a track declared, loaded or removed since, or a changed region of interest, that changes
  the notes or what the check ran for;
- a section that another build of CoGA computes differently.

The signed version still shows what was signed; signing out again records the new result.

### The gates

Seven gates run first, in this order. Each refuses the sign-out with 409. Gates 3 to 7 can be
acknowledged: the request sets the gate's flag with a reason (a flag without its reason gets 422).
Gates 3 to 7 read the snapshot of the analysis signed out, so each is that analysis's own (*One
sign-out per analysis*).

| # | Gate | Refusal | Acknowledgement |
| --- | --- | --- | --- |
| 1 | Assembly scope | `gate: "assembly_scope"` | none |
| 2 | Data being written | two kinds (below) | none |
| 3 | Evidence drift | `gate: "evidence_drift"` | `acknowledge_drift`, `drift_acknowledgement_reason` |
| 4 | Sample-integrity QC | `gate: "sample_qc"` | `acknowledge_qc`, `qc_acknowledgement_reason` |
| 5 | Incomplete import | `gate: "import_incomplete"` | `acknowledge_import_incomplete`, `import_incomplete_acknowledgement_reason` |
| 6 | Package folder changed after its import | `gate: "package_source_change"` | `acknowledge_package_source_change`, `package_source_change_acknowledgement_reason` |
| 7 | Findings without a review | `gate: "unreviewed_findings"` | `acknowledge_unreviewed_findings`, `unreviewed_findings_acknowledgement_reason` |

The acknowledgements and their reasons are part of the hashed snapshot and the audit event.

**1. Assembly scope.** A family whose assembly is not in `VALIDATED_ASSEMBLIES` (default GRCh38),
or that has none. The family and report pages say such a family is not validated for clinical use,
and neither the report page nor the NIPT report offers a sign-out.

**2. Data being written.** A package import writes a family over minutes, commits as it goes, and
flags it `import_incomplete` only once it has failed, so a snapshot read meanwhile could hold a
half-imported family. The sign-out does not wait; it is refused for:

- a family with a package-import job (not a dry run) that is `queued` (naming the family in its
  request), `validating` or `running` (`gate: "import_in_progress"`, naming the job);
- a family whose variant-write locks a writer holds (`gate: "variant_writes_in_progress"`: an
  import, an upload, an admin delete).

Past this gate the sign-out shares the family's variant-write locks
(`pg_try_advisory_xact_lock_shared`) until it commits, so no write of the family's variants starts
while it reads, and it checks the import jobs again after the snapshot. An import claimed meanwhile
commits its job as `running` on the family before writing anything of it, and so refuses the
sign-out. That record is not best-effort: an import whose job cannot record it (a database error,
or another worker has claimed the job) stops without writing anything of the family, its job ended
`failed` with the reason while it is still this worker's. The job's later updates (logs, dataset
summaries, heartbeat) are informational: one that fails leaves the status and the family as they
are.

A stopped or stalled import:

- A job whose worker stopped keeps refusing until a worker claims it again.
- A running job writes its heartbeat every minute, stamped and judged by the database's clock. One
  whose heartbeat is ten minutes old is taken for stopped: the worker that claims it runs it again
  only if it had not begun writing the family (`validating`); one that was `running` it ends
  `failed`, interrupted, keeping its log, and the family stays marked (gate 5).
- An import that is alive but could not write its heartbeat stops where it is once its heartbeat
  reaches the database again and finds the job ended, leaving the family marked the same way.
  Between the claim and that stop its variant-write locks, not its job, refuse a sign-out
  (`variant_writes_in_progress`), while their connection holds them.
- An import whose heartbeat has not reached the database for five minutes stops itself the same
  way, before a worker can take its job for stopped; one whose locks' connection is lost is stopped
  where it is, its job ended `failed` and the family left marked.

**3. Evidence drift.** Any drifted, unknown, missing or unsnapshotted classification, of a small
variant or of an SV or CNV. One acknowledgement covers both; the message says how many are SVs or
CNVs. The 409 lists each classification it counts, as the snapshot freezes it: small variants in
`drifted`, SVs and CNVs in `structural_drifted` (without `classified_by` and `classified_at`, which
a note or tag saved on the review changes). The sign-out dialog lists them as the signed version
does. Repeat and Paraphase reviews whose frozen calls are no longer the family's
(`finding_review_drift`, *Evidence drift* above) count in the same gate and acknowledgement: they
are listed in `finding_drifted` (kind, key, classification, whether reported, status and what
changed, without the review's time), and the message says how many.

**4. Sample-integrity QC.** Refused for:

- a QC fail (a detected sample or pedigree swap);
- a check that could not run for lack of data where it is what confirms the pedigree or a sample's
  identity: a relationship or Mendelian check the pedigree asserts (two samples it records as the
  same individual included), NIPT paternity or maternal lineage, or the sex of a sample no
  relatedness check covers, such as a NIPT parent;
- in a conventional NIPT family, from the record's `cnipt` section under the same acknowledgement: a
  maternal plasma without a result, with its reasons ("Sample X (cNIPT maternal plasma): no result
  — the fetal fraction (3.1%) is below 4.0%."), a result that rests on bounds QA has not set,
  naming them, and a conventional NIPT's record that froze no result (its results could not be
  read, or no plasma of the analysis holds a ccnv result), since its result is what the sign-out
  closes. The 409 lists each in
  `cnipt_concerns` and in `qc_summary.messages`; the audit event records them (`cnipt_concerns`).
  The Sample QC of a conventional NIPT runs no check (application `cnipt`, verdict `skip`; owner
  decision of 2026-11-19): ccnv's output holds no genotypes, and as for a PGT embryo the fetal sex
  is known only from the analysis, ccnv's call in `cnipt`. In the record of another analysis, cNIPT
  results that could not be read are not captured (named in `not_captured`), not a QC concern;
- in a monogenic NIPT family, a NIPT quality check that failed in the record's `nipt` section, under
  the same acknowledgement: no fetal-fraction estimate, too few expected fetal reads or a QC gate
  that rests on the provisional bound QA has not set (REQ-NIPT-021), a fetal sex whose paternal-X
  and chrY signals disagree, male DNA given as the maternal plasma (REQ-NIPT-015; TF-06 H4), no
  diagnostic gene panel (REQ-NIPT-019), or a `nipt` section that could not be read (the record then
  verifies none of these; as a conventional NIPT's record without a result). The 409
  lists each in `nipt_failed_checks` and in `qc_summary.messages`, the plasma's naming its sample
  ("Sample X (maternal plasma): male DNA …"); the audit event records them (`nipt_failed_checks`).
  Paternity is not among them: the Sample QC reads it from the same analysis and gates on it
  already. NIPT results that could not be read are also named in `not_captured`.

The 409 lists every concern in its group: checks that failed (`failed_checks`), could not run
(`unverifiable_checks`), NIPT quality checks that failed (`nipt_failed_checks`), the conventional
NIPT's concerns (`cnipt_concerns`), and checks that warn (`warning_checks`), which do not gate on
their own. A check that warns because it could not
run is listed only with those that could not run, so each concern is listed once.
`qc_summary.messages` lists the warnings, failures, failed NIPT checks and cNIPT concerns together. The message
names each kind of concern the gate holds, so a failure does not hide a check that could not run,
and leaves the concerns to the groups.

- Each concern names the sample, the pair, the child with its parents, or the NIPT father its check
  read ("Sample X: no chrX genotypes available for sex inference."), in `qc_summary.messages`, in
  each group and in the groups the sign-out's audit event records. Fetal sex and the NIPT maternal
  lineage, read from the cfDNA as a whole, name no sample. The checks frozen in `sample_qc` keep
  their own messages beside their sample fields.
- The sign-out dialog of the report page and of the NIPT report shows each group under its heading,
  before the reason. The acknowledged sign-out's audit event records the same groups (`qc_failed`,
  `qc_unverifiable`, `nipt_failed_checks`, `cnipt_concerns`, `qc_warnings`): what the
  acknowledgement covered.

**5. Incomplete import.** Refused, naming the failed datasets and the import job, while any of the
marks below is set. Every mark gates in this one 409, under the one acknowledgement, and every
family page shows *Import incomplete* while one is set.

- **The flag** `metadata.import_incomplete`: set by a package import that left the family partly
  loaded ([data-import.md](data-import.md)), or by a small-variant upload that failed after deleting
  the callset it replaced (no import job;
  [What an upload records](data-import.md#what-an-upload-records)). A flag, or an
  `import_unfinished` entry, in a shape no writer writes is never read: the sign-out fails on it
  (`ImportMarkShapeError`, a 500), so the gate stays closed. The flag clears only once an import
  has imported each failed dataset again, or an upload has brought it again, in any mode, for the
  same samples and small-variant source; an import counts a dataset it imports with warnings, or
  registers when its package carries nothing CoGA imports for it. A later failure keeps an earlier
  one's failed datasets, and every callset or set of samples a dataset failed for, each with its
  job (`failed_scopes`). The gate names each dataset with the job of its latest failure
  (`failed_jobs`), or none for a single-file upload's failure.
- **A stopped import.** An import whose process stopped part-way sets no flag: the entry it wrote
  in `metadata.import_unfinished` before its first write of the family gates instead
  (`import_unfinished`, naming the job and the datasets it had not finished, which may be partly
  written or missing). Only an import that completes with `overwrite` and imports those datasets
  again, for that scope, removes the entry.
- **A restore that did not finish.** A failed `overwrite` whose restore failed too (other than by
  refusing to start without its whole backup) may have lost rows of any dataset of the family,
  whichever import or upload wrote them. Its flag names every dataset of the import as failed,
  those it imported too. Its `import_unfinished` entry stays, naming every dataset of the import and
  every dataset the family holds in ClickHouse (what its imports and single-file uploads recorded in
  `metadata.imported_scopes`; Postgres, put back in one transaction, cannot be left partly
  written). The entry is frozen with `whole_family: true`, so the gate and the signed record name it
  as a restore that did not finish. The gate holds until the whole family has been imported again
  with `overwrite`, not only what failed: each import that completes with overwrite, and each upload
  with overwrite, removes from the entry what it brought again, for that scope; an update removes
  nothing.
- **An upload or deletion that did not finish.** Before its first write to ClickHouse, a single-file
  upload marks the family the same way, with an entry with no job keyed `upload-…`; so does a
  deletion of the family's data that deletes or rewrites rows there, keyed `deletion-…` (an admin's
  deletion of a sample, of its structural variants or of the family; a PED upload or manual family
  that replaces it; a structure change that clears its data). A failure leaves the entry listing
  exactly what may be partly written or missing
  ([The mark of an upload that did not finish](data-import.md#the-mark-of-an-upload-that-did-not-finish)),
  and the 409's message names "a single-file upload" or "a deletion of this family's data" that
  "did not finish", with what may be partly written or missing. The entry is frozen with its key,
  from which the signed record names what wrote it. Each upload or import with overwrite removes
  from it what it brought again. Deleting a sample removes that sample's items, and the sample from
  the import-incomplete flag's failures and from a stopped import's entry; each goes once it names
  nothing else.
- **Whose mark, in a family of several analyses.** Each mark records the analysis it is of
  (`analysis_type`): a package import's flag failures and entry, the key of its package's record;
  an upload's or a deletion's mark, none. A sign-out of one analysis is held by that analysis's
  marks and by those of no analysis, never by another analysis's: a failed `snv` of the long-read
  import does not hold the short-read sign-out, whose package carries an `snv` dataset too. A
  restore that did not finish holds every analysis's sign-out, as it rewrote the whole family's
  rows. Only an import of the same analysis, or an upload, completes a mark of an analysis: each
  analysis's sequencing QC, alignments and pipeline record are its own, so another analysis's
  import of the same dataset for the same samples brings none of them again. The gate, its dialog,
  the family pages and the signed record name the analysis of each mark.

**6. Package folder changed after its import** (REQ-TRACE-018; owner decisions of 2026-11-11). A
family holds one package record per analysis it was imported for, and CoGA shows each analysis's
read files (IGV), signal tracks and QC report from the package folder that analysis's record names.
Each such folder must be as its analysis's *reference delivery* was imported: the delivery that
analysis's data was last imported from whole, as a new family or with overwrite (an update skips the
datasets the family holds, so it keeps the reference it had). Each folder is held against its own
analysis's reference, never another's. For each, the sign-out:

- reads what the import watcher last found (the reference is `changed_after_import`, each
  `FAMILY_IMPORT_WATCH_INTERVAL_SECONDS`);
- takes the folder's fingerprint itself, stat-only and off the event loop (`found_at_sign_out`). A
  folder that differs from the reference's fingerprint, that is gone, that cannot be read within 30
  seconds (`SIGN_OUT_FOLDER_CHECK_SECONDS`) or at all, or that CoGA serves no file from (outside
  every import root: it is not listed) is a change, never a pass. In development with no import
  root configured, any local folder is read, as the watcher watches any. A folder too large to list
  in full is still fingerprinted, and one too large to fingerprint has changed.

Either finding refuses the sign-out, with `package_source_changes` (one entry per analysis whose
folder changed, each naming its `analysis_type`) and a message saying, for each analysis by name
(*the package folder the data of this family's monogenic NIPT of pregnancy ZW1 was imported
from*, *the package folder this family's short-read WGS data was imported from*), what changed, or that
the source files are no longer available. It is a warning: the sign-out stays possible. Every family
page shows one *Package folder changed after import* (or *gone*) banner per changed folder, naming
the analysis, while the watcher's finding holds. It ends only when the folder is as the reference
was imported again, when a delivery of that analysis is imported with overwrite, or when the
folder's new delivery is refused with a reason. A gone folder a superuser confirmed as deliberately
gone (`archived`) does not gate: the snapshot freezes it, and the family page says the source files
are archived; a check that finds that folder back otherwise gates again.

**7. Findings without a review** (REQ-TRACE-016). A repeat locus or Paraphase region with an
abnormal call and no review; a review with a classification, a tag or a note counts.

- A repeat call is abnormal when its status is `pathogenic`, `premutation`, `review` or
  `not_assessed`, or when one of its counts lies near a boundary (a grey zone alone is not).
- A Paraphase call is abnormal when its status is `pathogenic`, `carrier` or `review`.

The 409 lists each finding in `unreviewed_findings` (kind, key, gene, and each abnormal member's
sample and status, with `near_boundary` for a repeat), as the record freezes it. A repeat or
Paraphase section that could not be read is listed as such (`key` null, `status: "unreadable"`). The
sign-out dialog lists them before the reason.

### An acknowledgement covers the concerns its dialog showed

Each 409 of gates 3 to 7 carries `concerns_fingerprint`: a SHA-256 over the canonical JSON (sorted
keys) of the gate and its concerns as the 409 lists them, each list sorted (`concerns_fingerprint`
in `report_signout_service.py`). It holds no time and nothing that changes while the concerns do
not, so the same concerns give the same fingerprint. The dialog sends it back with the
acknowledgement.

| Gate | Concerns | Sent back as |
| --- | --- | --- |
| Evidence drift | `drifted`, `structural_drifted`, `finding_drifted` | `drift_concerns_fingerprint` |
| Sample QC | its five groups (gate 4) | `qc_concerns_fingerprint` |
| Incomplete import | `import_incomplete` and `import_unfinished`, each without its `at` (the message names the time; the jobs and datasets say which failure it is) | `import_incomplete_concerns_fingerprint` |
| Package folder | `package_source_changes`: each change that gates (one per analysis), without `detected_at` (a change found again, as it was, is the same concern; a change the sign-out's own check found has none) | `package_source_change_concerns_fingerprint` |
| Findings without a review | `unreviewed_findings` | `unreviewed_findings_concerns_fingerprint` |

The sign-out reads the gate again and compares the fingerprint it was sent with:

- the fingerprint of the concerns now: the acknowledgement covers them;
- another (a concern arose, went or changed after the dialog listed them): 409 again, with the
  concerns as they are now, their fingerprint and `concerns_changed: true`, and nothing written (no
  signed record, no audit event). The dialog lists them again, says why, and takes a fresh reason,
  sent with the new fingerprint;
- none: 422. The SPA, the only client, always sends the fingerprint its dialog was given. A value
  that is not a SHA-256 in lower-case hex fails the request's own validation (422).

A gate without a concern needs no fingerprint, and an acknowledgement of concerns that went
meanwhile acknowledges nothing. The gates are read in order, so a change at an earlier gate is
refused there first; the acknowledgements already given travel on to the next attempt. The
sign-out's audit event records the fingerprint each acknowledgement carried (the five fields above;
None for a gate without concerns). The snapshot freezes the concerns themselves (the drift
sections, `sample_qc`, the import state, `package_source_changes`) and the audit event the Sample-QC
groups, so each fingerprint can be recomputed from the record. The snapshot holds no fingerprint:
the concerns it freezes are those the sign-out checked the fingerprint against.

**A lookup that fails** while the snapshot is built is frozen as an explicit marker, never an empty
value: missing QC cut-offs as `sequencing_qc.unavailable`; a failed lookup of a reference module as
module version `unavailable`. The audit event lists these as `not_captured`, and the report page
names them.

### Endpoints

| Endpoint | Returns |
| --- | --- |
| `GET /families/{family_id}/report/sign-outs` | the list of signed versions, each with its `report_code` and the analysis it closes (`analysis_type`); `latest`, the family's newest version, and `latest_per_analysis`, the newest of each analysis signed out |
| `GET /families/{family_id}/report/sign-outs/{version}` | one signed version, with its `analysis_type`, `report_code`, `verified` (its content hash recomputed on read) and `not_captured` (what its record could not capture) |
| `GET /families/{family_id}/report/sign-out-check?analysis_type=…` | whether the report of that analysis (named as for a sign-out) as it would be signed now matches the analysis's latest version: `analysis_type`, `matches`, `changed_sections`, `not_captured` |
| `GET /report-codes/{code}` | the family and version of the signed record a report code names (*The report code*) |

### The report code

The EPD report quotes the signed record by its report code (`services/report_code.py`). The code
is made from fields of the record that never change, so no column holds it:

```
FFFFFF-V-HHHC        e.g. 7KQM2X-2-4PHT
```

- `FFFFFF`: six symbols from the SHA-256 of the family identifier, the same in every version of
  the family's record, so an amendment's code starts like the code of the record it amends;
- `V`: the version, in decimal;
- `HHH`: three symbols from the first 15 bits of the record's content hash, tying the code to the
  content that was signed;
- `C`: a check symbol over all the others, by the Damm algorithm over GF(32) (the operation
  `x * y = 2x + y`, modulo `x^5 + x^2 + 1`). It detects every single mistyped symbol and every
  swap of two neighbouring symbols, so a code copied wrong is refused rather than finding
  another record.

The symbols are Crockford's base 32: the digits and the capitals without I, L, O and U. A code is
read back in any case, with or without hyphens or with spaces, an I or L as 1 and an O as 0. It
is 13 characters for versions 1 to 9, and at most 16 up to version 9999.

**Unique.** Every sign-out holds one lock (`report-code`) from making its code to its commit, and
looks the code up among earlier records. A sign-out whose code another record already has is
signed again a microsecond later: the new time changes its content hash, and so its code. The
lookup includes a deleted family's records, and versions are numbered per family ID (*Sign-out*),
so a family made again under its ID never repeats the deleted family's codes either: their
versions differ. The code is in the sign-out's clinical audit event (its summary and
`after.report_code`) and in every sign-out the API returns.

**Finding a record.** `GET /report-codes/{code}` answers with the family, version, signer, time
and content hash of the record the code names. A string that is not a report code (a letter a
code never holds, too short, a version that is not a number, or a check symbol that does not
match) gets 422 `report_code_invalid`, saying why; this is read from the string alone. A
well-formed code no record has, one whose record's family is outside the caller's projects, and
one whose family has been deleted all get the same 404 (*No signed record has this report
code.*). So a code tells nobody whether a record exists outside their projects; only the
request's audit row names the hidden family (REQ-SEC-001). The candidates are read by the code's
version and the content-hash prefix its hash symbols spell, as bound parameters. In the interface
the lookup is *Report codes* in the main menu (`/report-codes`, and `/report-codes/<code>` as a
link from the EPD); it shows the record's family, version, signer and content hash, and opens
the signed version.

## The report page and the signed record

`/families/{family_id}/report` has two views. A signed-out family opens on its latest signed
version (of a family that holds several analyses, the latest of the analyses the page signs out);
one never signed out opens on the live report. Both views download the frozen record as JSON.

### The signed version

`?version=N` is rendered from the snapshot that `GET …/report/sign-outs/{version}` returns, and from
nothing else (`frontend/src/pages/families/signedReportRecord.ts` reads it). It shows:

- the version, signer, time, **report code**, content hash, `verified` and the frozen build, and the
  sentence that says what the record is;
- the analysis the record closes, in its title (*Clinical report — signed version 2 of the
  conventional NIPT*) and, where the family's versions close more than one analysis, beside each
  version in the versions list. Its *latest* and its check are that analysis's;
- each reported variant's description as reviewed, classification, accepted criteria, evidence
  snapshot, tags and note; the reported SVs with their description and ClinGen CNV scoring;
- in a PGT family, the embryo calls (*PGT embryo results*): the ROI, the inheritance model, the
  disease haplotype, and per embryo its call, the homologs it carries at the ROI as the track names
  them (P1, P2, M1, M2), its warnings, its QC at the ROI, that QC's verdict and the thresholds it was
  checked against, and the haplotype file read;
- in a monogenic NIPT family, its NIPT results (*NIPT results*): the fetal fraction and the quality
  checks with their verdicts; the coverage; without a per-target table, the coverage check's notes
  on each coverage track it did not read as depth, word for word, with what the check ran for
  (*Coverage tracks not read as depth: … The coverage check ran without a gene or panel, over the
  family’s region of interest …*); the funnel and the categories; each reported variant as the NIPT
  analysis classified it; the recessive fetal status; and what the analysis ran with. No other
  family's record shows or mentions PGT or NIPT results;
- in a conventional NIPT family, its results (*Conventional NIPT results*): per maternal plasma the
  verdict of chromosomes 21, 18 and 13, the sex chromosomes and the segmental CNVs, then every other
  autosome that is not low risk and a line for the rest, each target the record does not mark
  `claimed` named *outside the validated claim*, and the claim stated; the fetal fractions, fetal sex and
  sex-chromosome call; the findings; the bounds, each QA's or provisional, with a note when any was
  provisional; and the inputs (the QC file and its digest, the segments read and their digest, and
  the files they were loaded from);
- the gene panels applied; the repeat expansions; the Paraphase results; the coverage tracks
  (*Coverage tracks*: per sample and caller, what the track held, read depth, a log2 ratio or
  unknown, and the file it was loaded from, from where and when); per analysis, the delivered
  package and its corrections (*Package intake*: the analysis, the run, the package file and the
  revision imported, and each correction with what it changed, a change the pipeline used marked,
  why, by whom and when); and the reference-data releases;
- the drift, Sample QC, sequencing QC and import state, with the acknowledgements; the sequencing
  QC per analysis the record holds, named when it holds several, with the profiles its cut-offs
  came from (*judged against the cut-offs of Short-read WGS, else Default, each warning and error
  cut-off on its own*);
- in the footer, with the report code, the frozen modules and beside them the reference-data
  releases and the coverage tracks (*Coverage tracks as signed: S1 coverage BED (package) — read
  depth; S1 WisecondorX (package) — log2 ratio; …*);
- `not_captured`.

**The class.** The classification shown is the class the record froze as shown (`class_shown`),
*Not classified* when it is null. Where the ACMG classification, or an SV's CNV scoring, holds
another class, that is shown beside it as the score, with its points (*VUS - class 3 (ACMG score:
Likely pathogenic - class 4 · 8 pts)*, *Not classified (CNV score: Pathogenic - class 5 · +1.00
pts)*), and the classification motivation names the class the criteria score apart from the class
shown (*The ACMG/AMP criteria PVS1 score this variant Likely pathogenic - class 4; it is classified
as VUS - class 3.*). The sign-out check compares the class shown: a class changed since signing
reads as a change, and so does a small variant's text changed alone (*VUS* beside the tag of class 4
rewritten as *Likely benign*).

**Repeat counts.** Each count near a boundary is marked beside its call (*⚠ Near a boundary: 53 is
within 3 repeats of 55 (Premutation)*); a count QA's correction changed shows TRGT's own in brackets
(*18 (TRGT 19)*); each locus shows its count correction (*TRGT’s count −1*, or none), with the
reason, by whom and when. Where a record holds a correction, one sentence says what the bracketed
count is.

**Evidence.** Under *Evidence drift at sign-out* a signed version lists the SV/CNV classifications
whose evidence had moved at sign-out with the small variants', and says when its record holds no
SV/CNV drift check. A small variant's entry names the frozen values that had moved, with the values
(*REVEL 0.412 → 0.951*, *LoF LC → HC*, *gnomAD missense Z 2.95 → 3.52*); a drift entry the check
recomputes holds the values as they are now (`evidence_to`). Under *Evidence when classified*, each
reported small variant shows the consequence, frequencies and in-silico scores its snapshot froze
from the primary transcript, and in a sentence of its own a missense variant's gene missense Z with
where PP2 read it (*From its gene's profile: gnomAD missense Z 3.52.*, *From the VCF's MISSENSE_Z,
as its gene's profile held none: …*, or *Neither its gene's profile nor the VCF held a gnomAD
missense Z.*). A group the snapshot did not freeze is left out.

**What it cannot show, it says:**

- A section frozen as unavailable shows as not captured, with its reason; one the view cannot read
  (not in the record, or not an object) shows as not captured too (*it cannot be read here; the
  downloaded record holds it as stored*), never as empty. A check or list the snapshot lacks reads
  as *Not in the signed record*, never as none.
- A list of a section that the view cannot read (a value that is not a list, an entry not of the
  list's kind) is left out, and the section says so (*Part of … cannot be read here (N lists or
  entries): it is left out below, and the downloaded record holds it as stored*).
- A parents' phase correction near the ROI that it cannot read is said where that warning stands
  (*A phase correction the record holds cannot be read here; …*), never dropped unsaid.
- A reported variant without a description it can read, in a record holding descriptions it cannot
  read, names both causes.
- A record without `pgt_analysis`, `nipt` or `cnipt` is another application's family's: it shows no
  PGT, NIPT or conventional NIPT section.
- A repeat or Paraphase section without a call names whose calls it holds: those of the analysis
  the record closes (*The short-read WGS had no repeat-expansion calls.*), or, for a family that
  holds no analysis, its samples'. Another analysis's calls are in that analysis's record (*One
  sign-out per analysis*).
- What no snapshot holds is said on the page: the population frequencies and in silico predictions
  the live report shows, the segregation, the gene and phenotype context, the audit trail and the
  pipeline settings.

**Print and check.** Print prints this view, report code included. A printout starts with a notice
when the record fails its content hash, when a later version supersedes it, or when the version
list could not be loaded. On the latest version the page also runs `sign-out-check` and says, on
screen only, whether the family's data has changed since. Straight after a sign-out it says, on
screen only, *Signed out as version N. Report code …: the EPD report refers to this record by it.*

### The live report

`?view=live` draws live data. It is where a case is signed out, and it is never presented as the
signed record.

- Each reported variant shows the class its review shows, as the family page shows it
  (`reportNarrative.ts`, `reportedSmallVariantClass`, `reportedStructuralVariantClass`), with the
  ACMG or CNV score beside it as on the signed version (`ReportClassification.tsx`, which both views
  use).
- It says what a sign-out is (*Signing out is the analytical sign-off in CoGA, which closes and
  freezes the analysis; the medical sign-off of the patient report happens in the EPD, which refers
  to the signed record by its report code.*) and names the latest signed version with its report
  code.
- It checks itself against the latest sign-out (`sign-out-check`): the content matches the signed
  version (it says so, and is still not the signed report); the content changed after sign-out (the
  changed sections are named); or the check could not be made (treat the page as unsigned). Of a
  family that holds several analyses the page shows every analysis's data, and the record holds
  one analysis's: a match is worded of that analysis's data (*The conventional NIPT data on this
  page still match signed version 2, which holds the conventional NIPT data only: the other
  analyses' data this page shows are not part of it.*).
- Its footer names the modules and versions of each analysis the family holds, each analysis's
  own (*Modules & versions (short-read WGS): …*); the pipeline settings show each analysis's run
  parameters with that analysis's tool versions.
- Every printout starts with a notice ("Draft …", "Not the signed report …", "Not verified …", or
  that the sign-out record is loading or could not be loaded). When the clinical audit trail shows
  only its newest events, the notice says that too ("Incomplete — the audit trail lists the 200 most
  recent of its 450 events …").
- While the sign-out record is loading or could not be loaded, sign-out is not offered. After a
  sign-out the page shows the new version.
- Of a family that holds several analyses, it signs out every analysis but the monogenic NIPT,
  which the NIPT report signs out. With several left (short-read and long-read WGS), *Analysis to
  sign out* chooses one. The button names it (*Sign out the conventional NIPT*, *Amend sign-out of
  the conventional NIPT*), and the page checks itself against that analysis's latest version.
- Of such a family it also lists each analysis (*Sign-out of each analysis*): its latest signed
  version with its report code, signer and date, and whether the family's data still match it; for
  the monogenic NIPT, that it is signed out on the NIPT report.

### The NIPT report

A monogenic NIPT family is signed out from the NIPT report too (`FamilyNiptReportPage.tsx`, *Sign
out NIPT analysis*, or *Amend sign-out* once signed), through the same request, gates and
acknowledgement dialogs as the live report (`reportSignOut.tsx`, which both pages use); the NIPT
page offers no sign-out. The NIPT report names the latest signed version with its report code, says
whether the family's data still matches it (`sign-out-check`) and links to it; after a sign-out it
opens the new version on the report page. It offers no sign-out outside the validated assembly
scope, nor while the sign-out record is loading or could not be loaded. Of a family that holds
other analyses too, it signs out its pregnancy's monogenic NIPT (`analysis_type: monogenic_nipt:ZW1`,
the pregnancy the page reads), and names and checks that analysis's latest signed version. The NIPT report is live too: it says so on
screen, beside the latest signed version, and every printout starts with the live report's notice
("Draft …", "Not the signed report …", "Not verified …").

### Adding a section to the signed record

A new section of what the analysis looked at (as the PGT embryo calls, `pgt_analysis`, and the NIPT
and conventional NIPT results, `nipt` and `cnipt`, are) is added in the four places below; one left out is missing from the signed
view without a trace.

A section only one application's families hold names that application in its `RecordSection`
(`application="pgt"`, `application="nipt"`, `application="cnipt"`). `build_record_sections` builds
it for those families alone, so another family's record holds no such key. The sign-out check
compares it where the record or the family's current data holds it (one side alone is a change),
and skips it where neither does. The signed view renders it only for a record that holds it
(`PgtAnalysisSection`, `isNiptRecord`, `CniptResults`). A section of data that one of a family's
analyses may hold reads the sign-out's scope (`SignoutScope`, which `build_record_sections` is
given) for what a sign-out of one analysis freezes of it (*One sign-out per analysis*).

1. **Its builder** in `backend/app/services/signed_record_sections.py`: an async function that
   reads what the analysis showed the analyst and returns a JSON-safe, deterministic dict (lists
   sorted, no clock, no random order: the sign-out check compares it right after sign-out,
   through the JSONB round trip). Give it a `RecordSection` (its key, the words that name it, the
   fixed sentence frozen when it cannot be read, and for one application's section its
   `application`), add it to `RECORD_SECTIONS` and call it in `build_record_sections` through
   `_guarded`. It is then frozen and hashed with the record, compared by `sign-out-check`
   (`REPORT_CONTENT_SECTIONS` holds every key of `RECORD_SECTIONS`), and named in `not_captured`
   when it could not be read.
2. **Its reading** in `frontend/src/pages/families/signedReportRecord.ts`: a field of
   `SignedReport` parsed with `parseRecordSection`, which gives the unavailable and recorded states
   (an application's section is `absent` from another family's record); and its label in
   `REPORT_SECTION_LABELS`, which names the check's changed sections.
3. **Its rendering** in `SignedFamilyReport.tsx`, through `RecordSectionBlock`, which says what
   a record could not capture or this view cannot read.
4. **Its description** here, under *Sign-out* (*What the analysis looked at*), and in the in-app
   reference and handleiding chapter 11, with tests of each (`test_signed_record_sections.py`,
   `signedReportRecord.test.ts`, `FamilyReportPage.test.tsx`).

## Tamper evidence and its limits

- **Append-only tables.** Database triggers refuse UPDATE and DELETE on `audit_log_events`,
  `ui_events`, `clinical_audit_events`, `report_signouts`, `integrity_anchors`,
  `qc_threshold_changes`, `family_structure_versions` and `import_delivery_revisions` (the
  corrections of a delivered package before its import; see [database.md](database.md)). The
  restricted runtime role `coga_app` also lacks those rights, but only once the deployment has
  switched to it ([db-runtime-role-runbook.md](db-runtime-role-runbook.md)). Until then the
  application connects as the table owner, who can disable a trigger.
- **Hash chains.** Each row of `clinical_audit_events`, `report_signouts` and
  `family_structure_versions` carries a `row_hash` over its content and the previous row's hash,
  per family. A deleted, reordered or edited row breaks the chain.
  `GET /admin/integrity/verify?table=…&family_id=…` walks one family's chain and names the first
  bad row; for sign-outs it also recomputes each content hash. `ui_events` and
  `audit_log_events` are append-only without a chain: a UI event is what the browser reports of
  itself, not the device's record of a clinical action.
- **Signed anchors.** `POST /admin/integrity/anchor` records the head of every chain and signs
  that snapshot with the Ed25519 key in `INTEGRITY_ANCHOR_SIGNING_KEY`, which the database does
  not hold. Outside development the backend refuses to start without the key and never writes an
  unsigned anchor. `GET /admin/integrity/anchor/verify` checks the live chains against the latest
  anchor; `GET /admin/integrity/anchor/verify-chain` checks the anchors themselves, and so
  detects an owner who rebuilds or shortens a chain after an anchor was made.

This is tamper-evident against someone who has only the database, between retained anchors. It
is not tamper-proof: someone holding the signing key can forge anchors, and the most recent
anchors can be deleted without trace unless a copy is kept outside the database.

## Known limitations

**The record**

- The variant description holds no population frequency or in silico prediction, and the snapshot
  no gene or phenotype context: a signed version says what its record lacks, and only the live
  report shows the rest, from current data.
- There is no byte-stable PDF: a signed version is printed from the browser, laid out by the build
  that renders it (named in its footer).
- The gene reference is frozen for the reported variants' genes only: a record without a reported
  variant names no gene-reference release. The gene loci the panel filters read are in the manifest
  (`gene_loci`).
- The clinical CNV knowledgebase states no release of its own. The record names the files a rebuild
  read: the pinned one, ClinGen's recurrent CNV file, by release and SHA-256; the rest (the ClinGen
  dosage curation, ClinVar, the cytobands, Orphanet and a DECIPHER map, as they were that day) by
  file name and SHA-256.
- The manifest's platform layer covers the assembly, the gene loci, Monarch and HPO. The releases
  of the gene reference, PanelApp and the clinical CNVs are frozen beside it (`reference_data`,
  `gene_panels`), not in it, so `GET …/annotation-manifest` and the live report's footer do not
  show them.
- A panel version is recorded the first time a search of the family applies it, not at each search;
  one applied in the variant explorer, which searches across families, for no family.
- A repeat-expansion call's and a Paraphase result's status is frozen as CoGA classifies it at
  sign-out, from the catalogue loaded then. The repeat page and the genome tracks classify a call
  alike, against the current catalogue (*TRGT repeat catalogue*, [data-import.md](data-import.md)).
- `coverage_tracks` names each coverage track of the members as their records list it, not the
  chromosomes a depth check read: the mtDNA coverage reads a track only where it has rows on chrM,
  the NIPT coverage check its targets' chromosomes, and a sample with two read-depth tracks is read
  from its coverage BED. The mtDNA coverage and its verdict are not frozen.
- An analysis's sequencing QC is judged with each warning and error cut-off on its own: each comes
  from the analysis's own profile, else from the profile the family names, else from the default
  profile ([database.md](database.md), *Sequencing-QC limits*). So a cut-off QA set on the family's
  or the default profile still judges an analysis whose own profile sets none for that bound (a
  warning set alone in the analysis's profile keeps the default's error cut-off), and a short-read
  and a long-read analysis of one family can be judged by the same cut-off. The record names the
  profile each bound came from.

**Sign-out and the EPD**

- A supervisor of the family's project, and any superuser or admin, can sign out; an analyst, a
  viewer and a reader cannot (TF-06 H15). CoGA checks the role, not which supervisors the
  laboratory has authorised: an administrator gives the role.
- CoGA does not see the clinical report in the EPD. Nothing in CoGA checks that the report refers
  to a signed version, which one, or that it says what that version holds; whoever drafts the
  report writes the reference into it. The report code makes that reference short and refuses a
  mistyped one, and `GET /report-codes/{code}` finds the version it names, but CoGA does not see
  where a code is quoted.

**Anchors and the runtime role**

- Anchors are made by hand: nothing in the code, CI or Terraform calls
  `POST /admin/integrity/anchor` on a schedule.
- Exporting each anchor to a store outside the database is not implemented (the `export_anchor`
  hook does nothing), so deleting the latest anchors is not detected.
- Verification trusts only the configured signing key. After a key rotation, anchors signed with
  the old key report `unknown_key`; keep the old public key for audits.
- `coga_app` keeps INSERT on the chained tables, so it could append a forged but self-consistent
  row; it cannot rewrite or delete existing ones.

**PGT**

- The embryo calls are frozen as the backend makes them at sign-out. The haplotype tracks of the
  genome overview and the chromosome view show each member's risk state from the browser's
  implementation of the same rules, which the parity cases pin but do not prove equal on every
  input.
- One QC measure gates an embryo call; the others only warn. The QC is counted over the ROI and
  1 Mb on each side, each flank apart (left and right of the ROI); the ROI's own sites count on
  neither.
  - **Gate.** An embryo whose markers there, on a parental side the call reads, mix both of that
    parent's homologs (a discordance above 10 % over at least 20 markers, `mixed_homolog_sides` in
    `services/pgt_embryo_qc.py`) is not called: it reads as uninformative, whatever its blocks say
    (the owner's decision of 2026-10-30). This is a safety net, not an aneuploidy check: two copies
    of one homolog leave the markers in agreement, and a trisomy away from the ROI is not looked at.
  - **Warnings.** A call whose phasing QC does not meet the other thresholds of
    `services/pgt_embryo_qc.py` (at least 50 informative sites, a Mendelian error rate of at most
    2 %, at least 6 markers on each flank of the ROI on each parental side the call reads), or whose
    QC was not counted, carries a warning beside it on the family page and in the signed version.
    Such a call stands, the sign-out is not held up, and the analyst decides (the owner's decision
    of 2026-10-20).
  - The thresholds are provisional: the laboratory director and QA are to confirm them.

**NIPT**

- A NIPT family's record freezes the NIPT classification of the variants tagged `report`, not every
  candidate the NIPT report or a view of the NIPT page lists, and their recessive fetal status over
  the reported variants alone.
- It freezes the coverage of every capture target and each gene with a weak target, not the
  coverage of a search's gene panel or genes as the NIPT page's coverage card reads it. Without a
  per-target coverage table it holds the coverage check's notes on the plasma's coverage tracks
  (`nipt.coverage_notes`), run without a gene or panel over the family's region of interest, not
  the coverage the check read or its verdict; `coverage_tracks` says which kind of coverage it
  rested on.
- The artifact list and the recurrence counts are shared by every family of the assay: a change to
  the list, or another pregnancy of the assay imported since that carries a reported allele, makes
  the sign-out check name `nipt` as changed.
