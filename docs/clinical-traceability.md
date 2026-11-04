# Clinical traceability, sign-out and audit

A signed-out analysis must be reproducible and defensible: it says what produced it, and who
decided what and when. This page covers what CoGA records for that, how sign-out works, how
tampering is detected, and what is not covered. It is for developers and auditors; lab users find
the same in plain words in the in-app reference
([clinical-traceability](../frontend/src/content/docs/clinical-traceability.md), at
`/docs/reference/clinical-traceability`).

**What sign-out is.** CoGA's sign-out is the analyst's analytical sign-off: it closes the analysis
and freezes it into a signed version. It is not the medical sign-off. The device boundary is
*annotated VCF → closed, frozen analysis in CoGA* ([TF-02 §3](regulatory/TF-02-device-description.md)).
The clinical report is drafted and medically signed in the hospital's electronic patient record
(EPD), outside the device, and refers 1:1 to the signed version. The UI keeps the names *Sign out
report*, *signed version* and *signed report* for the frozen analysis.

Code comments number four parts: Phase 0 (annotation manifest), Phase 1 (evidence snapshots and
drift), Phase 2 (clinical audit trail) and Phase 3 (sign-out).

## What is recorded

| Record | Where | What it holds |
| --- | --- | --- |
| Annotation manifest | `family_annotation_manifest` | per family, the versions of the tools and databases that produced its annotated input (VEP, ClinVar, gnomAD, dbNSFP, SpliceAI, callers, pipeline) |
| Evidence snapshot | `small_variant_reviews.acmg_evidence_snapshot`, `structural_variant_reviews.cnv_evidence_snapshot`, `repeat_expansion_reviews.evidence_snapshot`, `paraphase_reviews.evidence_snapshot` | what the classifier saw, per ACMG classification of a small variant and per CNV (ClinGen) classification of a structural variant or CNV; the calls a repeat-locus or Paraphase-region review rests on, when it carries a classification or the Report tag |
| Clinical audit trail | `clinical_audit_events` | who changed what and when, with before and after: reviews and compound-het pairs, the pedigree, members and phenotypes, the manifest, the NIPT artifact list, a gene-panel version's first use, the family's deletion, sign-out (below) |
| Pedigree history | `family_structure_versions` | the pedigree after each change (members with sex, role, affected and carrier status, and the relationships), one version per change, hash-chained per family |
| Signed reports | `report_signouts` | each sign-out as a frozen, versioned, content-hashed snapshot, found again by its report code |
| HTTP audit log | `audit_log_events` | every API request, with the user and a masked body |

**The manifest** has three sources: the VCF headers read at import (`vcf_header`, see
[annotation-provenance.md](annotation-provenance.md)), the pipeline's own record in a package
(`manifest`), and an admin's replacement through `PUT /families/{family_id}/annotation-manifest`.
A replacement is always recorded as `manual`, and later imports leave a manual manifest alone. An
import and a replacement of one family take turns on one lock, so neither overwrites the other
halfway. `GET /families/{family_id}/annotation-manifest` merges it with what CoGA itself loaded:
the reference assembly, the gene-loci source (`gene_loci`: GENCODE, or the UCSC table used when
GENCODE could not be fetched), the Monarch release and the HPO release, with the SHA-256 of the
ontology file its terms came from where the import recorded it (TRACE-18; how each is read:
[annotation-provenance.md](annotation-provenance.md#the-reference-modules)). The report footer
shows the merged list; sign-out freezes it.

**The evidence snapshot** is taken on every ACMG save, with the time. It holds the variant's
annotation version, annotation-set hash and ClinVar significance. The hash changes with any
annotation of the variant, so it is the drift key. It also holds the values the classification
rests on, as the ACMG dialog reads them: from the primary transcript (MANE Select, then canonical,
then most severe impact; its id in `transcript_id`), through the variant list's own helpers
(`small_variant_review_acmg.small_variant_evidence`):

- under `consequence`: the term or terms (`effect`) the null, missense, synonymous and
  length-change criteria read (PVS1, PM4, PP2, BP7, and those a consequence rules out), the
  `impact`, and LOFTEE's loss-of-function call (`lof`) for the PVS1 strength;
- under `frequencies`: the gnomAD allele frequency the frequency criteria read (`gnomad_af`), and
  the exome, genome, popmax and TOPMed frequencies and gnomAD allele, homozygote and hemizygote
  counts the filters read;
- under `in_silico`: CADD (`cadd_phred`), REVEL, SpliceAI (`spliceai_max`), SIFT, PolyPhen and
  the AlphaMissense class and score;
- under `gene_constraint`, for a missense variant only: the gene's gnomAD missense Z that PP2
  reads (`gene_missense_z`) and its source (`gene_missense_z_source`).

A missense variant has `missense_variant` among its consequences, as the dialog decides for PP2
(`reads_gene_missense_z`). PP2 reads the missense Z for no other variant, so for those the group
is left out and the gene's profile is not read (the owner's decision, 2026-10-20). PP2's missense
Z comes from:

1. the gene's profile (`gene_info`), not the variant's annotation, through the variant list's own
   lookup (`gene_constraint_metrics.fetch_gene_constraint_metrics`) in the profiles of the
   family's assembly, for the gene the variant is served with: the primary transcript's gene id,
   else its symbol (in any letter case), else the variant's first gene. Before TRACE-15 the lookup
   read the newest profile of any assembly and matched a symbol only as written;
2. else the VCF's own `MISSENSE_Z`, which the import stores in the primary transcript's
   annotation as `gene_missense_z` and the variant list serves in `annotation_extra`
   (`small_variant_review_acmg.pp2_gene_missense_z`, the owner's decision of 2026-10-28).

The source is `gene_profile` or `vcf`, and `null` with the value when neither holds one. Every
field is frozen, `null` when empty, so a value that appears later is a change. A value JSONB
cannot hold (NaN, infinity) is frozen as `null`. Older snapshots hold less: one from before these
values were frozen (CLIN-7) holds none; one from before the consequence and missense Z holds
`transcript_id`, `frequencies` and `in_silico` only; one from before the source (TRACE-15) holds
the gene profile's missense Z without it.

A structural variant (SV) or CNV has no annotation-set hash in ClickHouse. Its snapshot
(`services/structural_variant_evidence.py`, taken on every save of a CNV scoring) holds what the
CNV classifier reads: the SV's type, the genes it overlaps and their count, the pLI and the
annotated inheritance. The classifier reads no clinical-CNV, dosage-score or DGV data; the analyst
scores those criteria by hand. The snapshot also holds the event (caller, chromosome, start, end,
length, breakend partner), a hash of its whole annotation record, the time, and the versions the
evidence comes from: those the manifest records for the SV callset (`by_modality.sv`, else
`pipeline`), the reference assembly and the gene loci. The drift key is `evidence_hash`, SHA-256
over the evidence; the versions are recorded, not compared. Genotype calls are not held. A
scoring for an SV not in the family's data gets 404. One saved for a family without an assembly
(no SV storage to read) is stored without a snapshot.

Several callers can call one event under one SV id (an uploaded SV's id names no caller, and a
NeedlR SV's has the same form); they share its review. Each caller's call is its own row on the SV
page, with its own genes, pLI and inheritance. So the snapshot is taken from the row of the
caller the scoring was made on, which the save names in `source` (the classification dialog sends
the row's caller). A save naming no caller (a client from before the field) takes the SV's one
row; when several callers call the SV, the row cannot be told, so it gets 422 rather than a
snapshot of another caller's row. A save naming a caller that does not call the SV gets 404.
Before TRACE-11 the snapshot came from the row with the lowest storage key, which could be
another caller's. Such a snapshot is kept as stored: it names its caller (`evidence.source`) and
holds that caller's genes, pLI and inheritance, and the drift check compares it with that
caller's row. Saving the scoring again from the right row freezes that row's evidence.

**The clinical audit trail** is written in the same transaction as the change it records, so it
cannot drift from the data. It is written by:

- a review save (classification, tags or note) of a small variant, SV, CNV, repeat locus
  (`repeat:<locus_id>`) or Paraphase region (`paraphase:<gene_symbol>`);
- sign-out, and an admin's manifest replacement;
- every NIPT artifact-list change, on its own chain (`system:nipt-artifacts`, not a family);
- every record of whether TRGT's counting units at a repeat locus are validated, on another
  (`system:repeat-loci`);
- every change to a family's phenotypes (SEC-1).

A phenotype edit writes one `phenotype` event per HPO term added, changed or removed: the member,
the term and its label, and the status, onset, evidence, source and note before and after (none
before an added term, none after a removed one). Its summary names the change (*Phenotype changed
for S1: HP:0001250 Seizure: status present → absent*). A save that changes nothing writes none.

An import that changes a family's phenotypes writes `phenotype` events too: one per changed
member, as the user who ran it, in the change's transaction and under the edit lock
(`hpo_service.record_imported_phenotype_changes`). This covers:

- a package import (*Phenotypes imported for S1 by a package import of FAM1 (phenotypes.tsv):
  added HP:0001250 Seizure (present)*);
- the restore of a failed overwrite (*Phenotypes put back for S1 after a failed package import of
  FAM1: …*);
- a failed import that removes the family it created;
- a PED upload or the Family Builder replacing a family (*Phenotypes removed for S1: the family
  was replaced by a PED upload (FAM1.ped): …*);
- an administrator deleting a family or a sample with its data (*Phenotypes removed for S1: an
  administrator deleted the sample with its data: …*, metadata `source: admin_deletion`,
  TRACE-16).

A family deletion writes them just before its `family_deleted` event (below); a sample deletion,
before its `pedigree` event. A member removed from the pedigree keeps its phenotypes: its sample
stays, inactive. Before and after hold the member and the annotations the change touched
(`{"sample_id": …, "phenotypes": […]}`, each as an edit's event holds it): before, those altered
or removed, as they were; after, those added or altered, as they are; as for an edit, none before
when it only added and none after when it only removed. The summary names five changes at most;
the metadata names the package (its folder or bucket) or uploaded file, the phenotype files and
the import job. An import that changes no phenotype writes none.

The actions are `classification`, `tags`, `note`, `compound_het_pair`, `pedigree`, `phenotype`,
`family_deleted`, `annotation_manifest` (the replacement, with the old and the new manifest),
`panel_applied` (below), `sign_out`, `nipt_artifact_added`, `nipt_artifact_updated`,
`nipt_artifact_removed`, `nipt_artifacts_auto_seeded`, `nipt_artifacts_imported` (an import of
the NIPT-M pipeline's recurrent table, naming the file and every allele it listed), and
`repeat_count_units_validated` and `repeat_count_units_not_validated` (the locus, and the record
before and after, with its reason).

`classification`, for a small variant, holds the ACMG class, the accepted criteria, the point
total, the VUS tier and every stored criterion (its strength, whether accepted, its evidence text,
whether an automatic suggestion). For an SV or CNV it holds the reviewer's classification and the
CNV (ClinGen) scoring: class, kind, point total, the accepted criteria with their points, and
every stored criterion in the same detail. Any change to that record writes an event, even when
the class stays, and the summary names the change (*ACMG criteria updated (…): PM2 moderate →
supporting*). An unchanged re-save writes none. Points are recorded as the database reads them
back, so a total of -0.0 is stored as 0 and the chain still verifies. SV and CNV events carry
`metadata.modality = "sv"`, so an SV id is never read as a small variant's. A save that clears or
deletes a review is recorded like any other change.

`compound_het_pair` (SEC-7) is the review of a compound-heterozygous pair, given on one variant's
review with its partner. A save that records, changes or breaks up a pair writes one event per
changed pair, with the pair before and after (partner, gene, classification, tags, note, phase).
It is filed under the variant whose review held the pair: the saved variant or, for a pair the
save broke up by pairing either variant with another, the variant that held it. Its summary names
the partner (*Compound-het pair with 1-200-C-T: classification Likely pathogenic → Pathogenic*);
`metadata.compound_het_group_id` names the pair's group. An unchanged re-save writes none.

`pedigree` (SEC-7) is a change of the pedigree or members: its creation (*Pedigree recorded with 3
members: …*); an edit of the structure or of members (sex, role, affected or carrier status,
parents, a rename, a member made inactive); a package import that changes a member's affected or
carrier status; an admin's deletion of a sample with its data. It holds each changed member and,
when they changed, the couples and links of unknown degree, before and after. Its metadata names
the structure version the change stored, with its hash and the change's reason. An edit that
leaves the pedigree as it was writes none
([family-member-management.md](family-member-management.md#what-is-recorded-of-a-change)).
Structure versions are numbered per family row, so a family made again under a deleted family's
ID numbers its own from 1 again. Its events then also name how many structure versions the
deleted families under the ID stored (`metadata.deleted_family_versions`). Its first event says
so in its summary (*Pedigree recorded with 3 members: …. Made again under this family ID, after 2
structure versions of a deleted family: its own start again at 1.*), and its deletion names its
latest version the same way (`before.deleted_family_versions`). So the ID's trail names each
version once (ENG-19). A family never made again is recorded as before.

`family_deleted` (SEC-7) is a deleted family's last event: for an admin's deletion with its data
(Admin → Data), a PED upload or hand-made family that replaces it with overwrite, or a package
import that created it and imported nothing; `metadata.source` says which. It holds who deleted
it and when, its samples by ID, its latest structure version, and what went with it: the variant
reviews, repeat calls, Paraphase results, interval tracks and raw files Postgres held, and for an
admin's deletion the small and structural variants counted in ClickHouse (*Family deleted with
its data: 3 samples (F1, M1, P1), 1204 small variants, 3 structural variants, 5 variant
reviews*).

- An admin's deletion requires a reason (TRACE-16, the owner's decision of 2026-10-27). The
  dialog asks it; `DELETE /api/admin/families/{family_id}` without `{"reason": …}` in its body
  gets 400. The event holds it as `metadata.reason` and ends its summary with it (*… 5 variant
  reviews. Reason: duplicate of F2*). The other deletions, which their `source` explains, ask
  none.
- It is written in the deletion's transaction, before anything of the family is deleted (an
  admin's deletion) or before the family row goes, so a failed deletion records nothing.
- The family's chains (clinical audit events, sign-outs, structure versions) stay, unlinked from
  the family row and verifiable by the family's ID. A family made again under that ID (a
  replacement with overwrite does so at once) writes on the same three chains after the
  deletion, so each chain of a family ID covers every family made under it (TRACE-13).

**The gene panels a family's searches apply** (`services/panel_use_audit.py`, REQ-CARR-003). The
first time a search of the family's data applies a panel version, it writes a `panel_applied`
event before it runs. Such searches are the small-variant and SV lists and their CSV exports, the
NIPT variant list and the NIPT coverage. The event holds the panel's id, name, local version and
gene count, and its source's release: for a PanelApp panel, PanelApp's panel id, its version of
the panel, the date PanelApp gives that version, the confidence levels imported and when CoGA
imported it; for the Mendeliome, the Monarch release.

- One event per panel version and family. A version already recorded writes nothing; the check
  runs again under the family's audit-chain lock, so two searches at once record it once.
- A panel edited or imported again since (each such version is archived in
  `gene_panel_versions`), or a PanelApp version imported over the same genes, is recorded
  again at the next search that applies it.
- A search whose event cannot be recorded fails with it.
- A panel applied in the variant explorer, which searches across families, is recorded for no
  family.

Before this, nothing recorded the panel: the HTTP audit log keeps only the query's keys by
default (`AUDIT_LOG_QUERY_STRING_MODE=keys`), writes in batches after the response and can be
switched off, so it cannot anchor a signed record.

`GET /families/{family_id}/clinical-audit` returns a family's trail a page at a time, newest
first, in one fixed order (`created_at`, then `id`: the chain's order, reversed): 200 events by
default, at most 500 (`limit`). `before`, the id of the previous page's last event, reads the
events recorded before it, so an event written meanwhile is neither repeated nor skipped. An id
that is not an event of the family's trail gets 404. `total` is the trail's event count, read in
the same statement as the page; `has_more` says older events remain. The report shows the trail
as the clinical audit trail. When it shows only the newest events it says so, on screen and atop
every printed page, and **Show older events** reads the next page.

Two people saving the same review cannot silently overwrite each other: a save carrying the
`expected_updated_at` it loaded gets 409 `review_conflict` when the review changed in between.
Saves of one variant's review wait for each other (a transaction-scoped lock per variant), so the
check sees every earlier save, and no save is stamped with an `updated_at` earlier than the
version it replaces.

A compound-het pair save (`compound_het` with a `partner_variant_id`) also writes the partner's
review, and breaking up a pair rewrites the review of each of its variants. Each of those reviews
is locked as its own save would lock it, all in one order (so two pair saves cannot wait on each
other), and read only under its lock: a partner review saved meanwhile is kept, never overwritten
from an older copy. Before this fix, a pair save racing a partner's save could put the partner's
earlier review back: its class, ACMG criteria and note lost, its `updated_at` moved back. The
partner's version is checked like the variant's own. A pair save carrying
`compound_het.partner_expected_updated_at` (the partner review's `updated_at` as loaded, null for
none) gets the same 409 `review_conflict`, which names the partner (`variant_id`) and carries its
current review. A pair that changed between the save's look-up and its locks is refused the same
way. A refused save writes nothing, so it leaves no clinical audit event; the request log records
the 409 as for every refused save.

## Evidence drift

`GET /families/{family_id}/classification-drift` compares each classification's snapshot with
the variant's current annotation:

- `current`: the annotation-set hash and every frozen value are unchanged;
- `drifted`: the hash changed (a new annotation release; ClinVar and version shown from → to), or
  a frozen value did, even under the same hash. A missense variant's gene missense Z is outside
  the hash, so a gene-profile refresh can move it; it never moves another variant's
  classification, which froze none. `changed` names what moved: `clinvar`, `transcript_id` (the
  primary transcript), each consequence, frequency or in-silico field by name (`effect`,
  `impact`, `lof`, `gnomad_af`, `revel`, …), `gene_missense_z`, `gene_missense_z_source` when PP2
  now reads the missense Z from the other source (the gene's profile gained or lost one: *gnomAD
  missense Z 4.20 (VCF) → 3.52 (gene profile)*), and `annotations` when the hash moved.
  `evidence_from` and `evidence_to` hold the transcript, consequence, frequencies, in-silico
  scores and missense Z with its source, as frozen and as now;
- `unknown`: nothing is seen to have moved, but a hash is missing or a frozen group cannot be
  read, so the match cannot be verified. A stored snapshot that is not a JSON object (the writer
  never stores one) counts as one that froze nothing, so it is `unknown` too (or
  `variant_missing`). Before TRACE-15 it was skipped, counted neither as checked nor, at sign-out,
  as without a snapshot;
- `variant_missing`: the variant is no longer in the data.

Only the values a snapshot froze are compared, numbers by value (JSONB keeps a number's digits,
not whether it was an integer). Older snapshots:

- before the frequencies and in-silico scores were frozen: `evidence_from` is null; compared by
  hash and ClinVar significance alone, as before, so it does not turn into drift;
- before the consequence and missense Z were: compared on transcript, frequencies and in-silico
  scores; a consequence or missense Z it does not hold is never drift;
- before the missense Z's source was: compared on the missense Z alone. Once the gene's profile
  holds none and the VCF does, the value PP2 reads has moved (*gnomAD missense Z 2.95 → 4.20*).

Current values are read as a classification saved now would freeze them. A variant classified as
missense and no longer called missense names its consequence and missense Z as gone (*gnomAD
missense Z 2.95 → n/a*); one that became missense names its consequence only. The check reads the
missense Z of all the family's missense variants in one query.

SVs and CNVs are under `structural`. Each CNV scoring's snapshot is compared with the SV as the SV
page reads it now, in the row of the caller the snapshot names (`evidence.source`); another
caller's call of the SV is other evidence, not a change to this one. `drifted` names what moved in
`changed` (`gene_symbols`, `pli`, `inheritance`, `sv_type`, `locus`, or `annotations` for any
other annotation), with the evidence from → to. `variant_missing` means that caller's call is no
longer in the data, even while another caller still calls the SV; the drift banner and the signed
version name that caller (*its needlr call is no longer present*; TRACE-12: before, they named
none, so an SV another caller still lists read as gone). Only frozen fields are compared. Frozen
evidence that cannot be read, or that names no caller, is `unknown`, never `current`.

At sign-out, a reported classification without a snapshot, of a small variant or an SV, also
counts as drift (`no_snapshot`).

**Repeat and Paraphase reviews** (REQ-TRACE-015, [repeat-paraphase-review-design.md](repeat-paraphase-review-design.md)).
A review of a repeat locus or Paraphase region that carries a classification or the Report tag
freezes the calls it rests on (`services/finding_review_pg.py`): for a repeat locus, its entry as
the signed record's `repeat_expansions` section freezes it (each member's call, alleles,
near-boundary warnings, the locus's thresholds and QA's counting-units record); for a Paraphase
region, its entry in `paraphase` without each result's load time. A save that sets or changes the
classification, or adds the Report tag, freezes them again. A save of only tags or the note keeps
them, so it cannot clear a change without a new judgement. The repeat and Paraphase tables
(`GET /families/{family_id}/repeat-expansions`, `/paraphase`) give each review's `drift`:
`unchanged`; `changed`, with `changes` listing each difference as `path: before → after` and a
member's call named by its sample (`calls[PROBAND].alleles[1].repeat_count: 34 → 38`);
`not_found` (no call at the locus or region now); `no_snapshot`; or `unverifiable`. A re-import
with other counts or copy numbers, a new catalogue threshold, or a validation of TRGT's counting
units at the locus each changes what a review froze. A review with only tags or a note is not
checked.

## Sign-out

A sign-out is the analyst's analytical sign-off (*What sign-out is*, above). The clinical report
in the EPD refers to the signed version by its report code (*The report code* below).

`POST /families/{family_id}/report/sign-out` freezes:

- what the record is (`record_statement`): *This record is the analytical sign-off in CoGA,
  which closes and freezes the analysis; the medical sign-off of the patient report happens in
  the EPD, which refers to this record by its report code.*;
- the merged manifest and the reference assembly;
- the software version and git commit that produced the snapshot;
- the small variants tagged `report`, each with its classification, ACMG criteria and evidence
  snapshot, and the reported SVs and CNVs with their classification, CNV criteria and evidence
  snapshot. A small variant is tagged `report` when its review or its compound-het pair review
  holds the tag: the review-tag filter's rule, by which the live report and the NIPT page's *Show
  reported* list the reported variants (TRACE-19). Before, the sign-out read a variant's own tag
  alone, so a tag only the pair held (only the API sets a pair review's tags) was listed there
  and frozen nowhere. Such a variant is frozen with the pair review that reports it
  (`compound_het`: partner, gene, the pair's classification, tags and note, and its phase),
  shown in the signed version under *Compound-het pair*. A variant only its own tag reports is
  frozen as before;
- the reviews of repeat loci and Paraphase regions in the family's assembly that carry a
  classification or the Report tag (`repeat_expansion_reviews`, `paraphase_reviews`;
  REQ-TRACE-017): each with its key, classification and label, tags, whether reported, note, who
  saved it last and when, the calls it froze, and whether they still are the family's (`drift`);
  those whose calls are not (`finding_review_drift`); and each repeat locus and Paraphase region
  with an abnormal call and no review (`unreviewed_findings`, gate 6). The `repeat_expansions`
  and `paraphase` sections below still freeze every call: a review marks a finding, it removes
  none. A record signed before these keys names them as not compared;
- the drift state (`drift` for the small variants, `structural_drift` for the SVs and CNVs), the
  sample-integrity QC result, and the sequencing QC with the cut-offs it was judged against;
- the import state. `import_incomplete`: null when the family's data imported completely,
  otherwise the failed and the imported datasets, when, and the import job.
  `import_unfinished`, the imports that stopped part-way: empty when there are none, otherwise
  for each its import job, when it began, its datasets and those it finished. For a failed
  overwrite whose restore did not finish, that is every dataset of the family still to import
  again, none finished; for a single-file upload or a deletion that did not finish, no job, under
  its `upload-…` or `deletion-…` key;
- what the analysis looked at (`services/signed_record_sections.py`, one section each):
  - `variant_descriptions` (REQ-TRACE-007): each reported variant as the analyst reviewed it,
    read from the family's variant lists as the report page reads them (`review_tag=report`),
    one entry per callset or caller that calls it. A small variant: locus, reference and
    alternative allele, gene, transcript (and whether MANE Select), HGVS c. and p., consequence
    and impact, and each sample's genotype with depth, genotype quality, allele depths and allele
    fraction. An SV or CNV: type, copy change (loss or gain), locus, length, breakend mate, genes,
    cytoband and caller, and each sample's genotype, copy number, supporting reads, quality and
    filter;
  - `gene_panels` (REQ-CARR-003): each gene-panel version the family's searches applied, as its
    `panel_applied` event recorded it (above): name, id, local version, gene count, source and
    the source's release (PanelApp's version of the panel, or the Mendeliome's Monarch release),
    when CoGA imported that release, and when and by whom it was first applied;
  - `reference_data` (TRACE-5), beside the manifest's modules: the gene reference's releases for
    the reported variants' genes (per source, the release and checksum of the file that answered
    for each gene, and the genes it has no record of), and the clinical CNV knowledgebase loaded
    for the assembly (its source, the release its source states, for a rebuild the ClinGen
    recurrent CNV file it read by release and SHA-256 (TRACE-14), and when it was loaded).
    PanelApp gives each panel its own version, which `gene_panels` holds;
  - `repeat_expansions`: every TRGT call of the family's samples, with its status at sign-out
    (as the repeat page classifies it), its alleles and the thresholds behind it; and the
    catalogue it was classified against: how many loci, from which sources (STRchive, CoGA's
    built-in rows) and a SHA-256 digest of every locus's thresholds, since STRchive's file states
    no release. Per allele, its near-boundary warning as the repeat page serves it
    (`count_unit_warning`, `services/repeat_count_units.py`: the count, the nearest count within
    the margin on each side that reads another status, and the margin; the allele's own status
    is the one on its side of the boundary; null when there is none). Per locus, QA's record of
    TRGT's counting units there, as `repeat_locus_count_units` held it (`count_units`:
    `validated`, the `reason`, who (`updated_by`) and when (`updated_at`); for a locus without a
    record `validated: false` and the rest null), with the margin in force
    (`count_unit_margin`). The warnings and the record never disagree: a validation or withdrawal
    that commits while the section is read is frozen with the warnings made again against it, by
    the family table's own code. A locus validated or withdrawn since reads as a change;
  - `paraphase`: every Paraphase result of the family's samples, with its status as CoGA reads it
    at sign-out, its copy numbers, the region's status rule and copy-number norm, and the
    catalogue of regions (how many, and a digest of their rules);
  - `pgt_analysis`, in a PGT family only (one with an embryo; REQ-PGT-005): the PGT analysis at
    the family's region of interest (ROI), the object the family page reads its embryo calls
    from (`services/pgt_analysis.py`, `GET /families/{family_id}/pgt-analysis`). It holds:
    - the ROI and the span of haplotype blocks read (the ROI and 1 Mb on each side);
    - the inheritance model as recorded and as applied (without one: AR when a member is marked a
      carrier, else AD);
    - each member's role, sex, affected and carrier status, from which the disease haplotype is
      inferred, and the disease haplotype(s) found at the ROI (side, homolog, kind) or that none
      was;
    - the parents' phase corrections near the ROI;
    - per embryo: its recorded sex; its call (affected / at risk, carrier, unaffected,
      uninformative; none when the family has no ROI); whether an uninformative call is due to
      its own missing data at the ROI; both calls when an X-linked recessive call depends on an
      unrecorded sex; the homologs it carries at the ROI with their blocks' extent; and the block
      boundaries inside the ROI or within 250 kb of it (recombinations);
    - per embryo, its phasing QC at the ROI, counted over the same window as the ROI marker
      review: informative sites, Mendelian errors and their rate, the sites read on each
      parent's side (in all and on each ROI flank), and the share of each side's markers off one
      homolog, or two either side of one crossover;
    - the parental sides whose markers mix both of that parent's homologs (`mixed_homologs`): the
      embryo is then not called, since it may carry both, as in a trisomy, which its blocks
      cannot show;
    - that QC's verdict against the thresholds of `services/pgt_embryo_qc.py` (`qc_verdict`:
      `pass`; `warning`, naming each measure that does not meet its threshold, with its value
      and the threshold; or `not_counted` when no QC was counted, which is no pass), with the
      parental sides whose markers were checked (the sides the call reads);
    - its inputs: the haplotype file each member's blocks were built from and its load time; the
      number of blocks read and a SHA-256 digest of them as read (lineage tags included); whether
      the blocks were built for another pedigree or other sexes than the family's
      (`blocks_outdated`, CLIN-66: every embryo is then uninformative, and the signed version
      says why); the distances the calls and warnings use; and the QC thresholds in force
      (`rules.qc_thresholds`).

    A family without an embryo has no such key, so its record is as it was. Nor has a monogenic
    NIPT family none of whose embryos has haplotype blocks of its own
    (`pgt_analysis.embryo_has_haplotype_blocks`, TRACE-19). The import reads a child of unknown
    sex and unknown or unaffected status as an embryo, as a NIPT PED usually gives the fetus, and
    the NIPT trio takes that embryo for the fetus. But the fetus has no sequencing column, so no
    blocks and no call. Such a record held the fetus's PGT analysis all the same (found on
    2026-10-20). The blocks an embryo call reads are the narrowest input that makes the section
    a PGT analysis: a NIPT family with them keeps it, as the family page calls such an embryo;
  - `nipt`, a monogenic NIPT family's only (`services/signed_record_nipt.py`; REQ-NIPT-001,
    -004, -006, -010 to -013, -015): the NIPT analysis as the NIPT page computes it at sign-out,
    by the backend's code (`nipt_service`, `nipt_quality_checks`), never the browser. It holds:
    - the trio (`samples`);
    - the fetal fraction with its interval, site count, per-site median and verdict, and the de
      novo window;
    - the category counts, the filter funnel and why cfDNA calls failed the quality filter;
    - the quality checks, each with its verdict and the sentence that says why (`checks`:
      paternity, the fetal sex from the father's X alleles and from the plasma's chrY coverage,
      the maternal plasma sample's sex profile);
    - the coverage of every capture target and each gene with a weak target (`target_coverage`,
      null without a per-target coverage table);
    - without a per-target table, the coverage check's notes over the plasma's coverage tracks,
      word for word as the NIPT page shows them, each naming a track the check does not read as
      depth and why (TRACE-9), with what the check ran for (`coverage_notes`: `gene` and
      `panel`, both null, the `regions` it read the tracks over, and the `notes`; owner decision
      of 2026-10-28, TRACE-17). The check depends on the gene or panel of the NIPT page's search,
      which the sign-out is not told. So the sign-out runs the page's own code
      (`nipt_service.nipt_coverage_notes`) as the page runs it without a gene or panel, over the
      family's region of interest (without one, no region and so no note), as `target_coverage`
      holds the check of every capture target. Null with a per-target table, whose targets the
      check reads instead;
    - each variant tagged `report` as the NIPT variant list classifies it (`variants`: category,
      maternal state and fetal inheritance, observed and expected allele fraction, confidence,
      fetal-inheritance probabilities, flags and quality-filter failures, the plasma's and the
      father's reads, and for a de novo candidate in the fetal window its triage, including one
      the de novo view leaves out as recurrent);
    - each reported variant it does not classify, with why (`not_classified`: not in the data, on
      the artifact list, no plasma call);
    - the recessive fetal risk of the genes the reported variants make both parents carriers in,
      as the recessive view computes it over them (`recessive_genes`);
    - what the analysis ran with. `inputs`: the per-target coverage tables with their file,
      number of targets and load time, and the assay's artifact list with its scope, number of
      alleles, a SHA-256 digest of them, how many it left out of this analysis and which the
      family's annotation kept in. `model`: the R NIPT-M v0.5.1 validation, its constants, the
      cfDNA quality filter and the fetal-fraction rules.

    A family that is not a monogenic NIPT family holds no `nipt` key: its record is as before. A
    trio that cannot be resolved (no active father, or no maternal plasma sample) is frozen as
    unavailable, with a sentence of its own;
  - `coverage_tracks` (owner decision of 2026-10-20; TRACE-9): every coverage track of the
    family's members, one per sample and caller (`source`: `web` for a coverage BED, an upload's
    or a package's; `wisecondorx`, `qdnaseq`, `hificnv`). For each: what it holds as the depth
    readers read it (`content`: `read_depth`, `log2_ratio` or `unknown`, by their one rule,
    `coverage_read_depth.coverage_track_content`, over the same records: for a coverage BED, what
    it declared when loaded; for another caller's track, what its import's transform made of its
    file); and each file it was loaded from (`files`: name, rows, load time, and from where:
    `upload` for a single-file upload, `package` for a package import's dataset, null when its
    record does not say, as for a coverage BED loaded before 2026-10-20), with what a coverage
    BED declared and the transform another caller's import recorded. Only a read-depth track is
    read as depth (by the mtDNA coverage and its `mtdna.mean_depth` verdict, and the NIPT
    coverage check without a per-target table), so the record says which kind of coverage a
    depth check rested on. It is read from the tracks' records in Postgres, as the PGT and NIPT
    sections read their inputs. Every family's record holds it, as `{"tracks": []}` for a family
    without a coverage track, so a track loaded after sign-out reads as a change. A monogenic NIPT
    family's per-target coverage tables are another track type (`target_coverage`), which `nipt`
    holds (`inputs.target_tables`); without them `nipt` also holds the coverage check's notes over
    these tracks, word for word (`nipt.coverage_notes`, above; TRACE-17);
- the signer, the time, and any acknowledgement with its reason.

Each section of what the analysis looked at is read inside a savepoint. One that cannot be read
is frozen as `{"unavailable": <a fixed sentence>}`, never as an empty section, and named in
`not_captured` (a part of `reference_data` on its own). The error's own text, which may quote
what it read, is not frozen.

Sign-out exists for the family report; a monogenic NIPT family's analysis is signed out through
it (below). In a PGT family the snapshot holds the embryo calls (`pgt_analysis`, above; the
owner's decision of 2026-10-14, REG-2). The backend makes them from the stored haplotype blocks
(`services/pgt_embryo_calls.py`), the family page shows the calls the backend serves, and
sign-out freezes the same object. So the record holds the calls the analyst saw, made by one
implementation from stored data. The haplotype tracks still draw the risk line and each member's
risk state in the browser (`frontend/src/lib/haplotypeRisk.ts`). Both implementations answer the
cases of `frontend/src/lib/__tests__/fixtures/pgtEmbryoCallCases.json` alike, which
`test_pgt_embryo_calls.py` and `haplotypeRisk.test.ts` both run. The live family report does not
show the embryo calls; its signed version does.

**A NIPT family's record** (owner decision of 2026-10-14, REG-2). A monogenic NIPT family is
signed out through the same `POST /families/{family_id}/report/sign-out`, from the NIPT report
or the report page: one signed record per analysis, with the same gates, version and report code,
which also holds the NIPT results (`nipt`, above). Where it is signed out from does not change
what is frozen.

- The Sample QC's NIPT checks stay in `sample_qc` (`paternity_check`, `fetal_sex_check` from the
  father's X alleles, `category_qc_check`, the parents' sex, and their fixed notes); the gene
  panels its NIPT searches applied stay in `gene_panels`.
- The paternity check is frozen with its four counts (the father's homozygous and het alleles
  seen and not seen), not the totals the Sample QC page is served.
- The Sample QC's summary (`application_summary`) is frozen as it read. A record signed before
  ENG-4 says paternity was confirmed from "categories 7/8", named after the check's logic before
  the NIPT-M pair rework (2026-10-04); one signed since says it is read from the father's
  alleles in the plasma. So the sign-out check names the sample-integrity QC as changed for a
  NIPT family whose latest record holds the old wording, until it is signed again.
- A NIPT variant whose review, or compound-het pair review, is tagged `report` is frozen in
  `reported_variants` and `variant_descriptions` like any reported small variant, with its
  classification, and in `nipt` with its category, reads and probabilities. The recessive fetal
  risk in `nipt` is computed over them, so it equals the NIPT page's recessive view over *Show
  reported* (TRACE-19).
- The Sample QC's NIPT checks and the NIPT section read one run of the NIPT analysis
  (`nipt_service.SharedNiptAnalysis`, OPS-9): the same artifact list (which the lab curates apart
  from the family's data) and the same values. A failed run fails both: the Sample QC notes that
  the NIPT cfDNA analysis could not run, and the section is frozen as unavailable.
- The Sample QC runs its NIPT checks in a savepoint, as each section is read. The analysis reads
  the family and the artifact list from Postgres; a database error there is rolled back to the
  savepoint and reads like any other failure of the analysis. The gate then lists the NIPT checks
  among those that could not run, and the sign-out goes on in a clean transaction. Before ENG-19
  such an error aborted the sign-out's transaction, and the sign-out failed (it signed nothing).

The snapshot is hashed with SHA-256 over a canonical encoding and stored as the next version.
Versions are numbered per family ID: a family made again under a deleted family's ID numbers on
from the deleted family's last version (its first sign-out is version 3 after two), on the same
chain. So a family ID and a version name one signed record for good, and the database refuses a
second record under them (`uq_report_signouts_family_identifier_version`, TRACE-13). Before
TRACE-13 they were numbered per family row: the new family took version 1 again, and the chain
failed verification as if tampered with. The family's pages list only its own versions; the
deleted family's stay on the chain, verifiable by the ID, and are not opened through the new
family.

An amendment is a new sign-out; no version is ever changed. The sign-out is also written to the
clinical audit trail. A stored hash is always checked against the snapshot as stored, so a
version signed before a field was added still verifies; the sign-out check reports that section
as `not_compared`. A section neither the record nor the family's current data holds (the NIPT
results of a family that is not a NIPT family) is neither compared nor listed. The live report,
the signed version and the NIPT pages name what the check lists as not compared, beside *still
matches* and beside what changed (*This page still matches signed version 2 in everything that
version holds. Not in signed version 2, so not compared: the HPO version.*), so neither reads as
covering it (TRACE-19). Before, they showed `matches` and `changed_sections` alone.

Six gates run first, in this order:

1. **Assembly scope.** A family whose assembly is not in `VALIDATED_ASSEMBLIES` (default
   GRCh38), or that has none, gets 409 (`gate: "assembly_scope"`). This cannot be acknowledged.
   The family and report pages say such a family is not validated for clinical use, and neither
   the report page nor the NIPT report offers a sign-out.
2. **Data being written.** A package import writes a family over minutes, commits as it goes,
   and flags it `import_incomplete` only once it has failed, so a snapshot read meanwhile can
   hold a half-imported family. These get 409, which cannot be acknowledged (the sign-out does
   not wait):
   - a family with a package-import job (not a dry run) that is `queued` (naming the family in
     its request), `validating` or `running` (`gate: "import_in_progress"`, naming the job);
   - a family whose variant-write locks a writer holds (`gate: "variant_writes_in_progress"`: an
     import, an upload, an admin delete).

   Past this gate the sign-out shares the family's variant-write locks
   (`pg_try_advisory_xact_lock_shared`) until it commits, so no write of the family's variants
   starts while it reads. It checks the import jobs again after the snapshot: an import claimed
   meanwhile commits its job as `running` on the family before writing anything of it, and
   refuses the sign-out. That record is not best-effort. An import whose job cannot record it (a
   database error, or another worker has claimed the job) stops without writing anything of the
   family; its job ends `failed` with the reason while it is still this worker's and can be
   updated. The job's later updates (logs, dataset summaries, heartbeat) are
   informational: one that fails leaves the status and the family as they are.

   - A job whose worker stopped keeps refusing until a worker claims it again.
   - A running job writes its heartbeat every minute (the database's clock stamps and judges
     it); a job whose heartbeat is ten minutes old is taken for one whose process has stopped.
     The worker that claims it runs it again only if its import had not begun writing the family
     (`validating`). One that was `running` it ends `failed`, interrupted, keeping its log, and
     the family stays marked (gate 5).
   - An import whose process is alive but could not write its heartbeat stops where it is once
     its heartbeat reaches the database again and finds the job ended, leaving the family marked
     the same way. Between the claim and that stop the job no longer refuses a sign-out,
     but the import's variant-write locks do (`variant_writes_in_progress`) while their
     connection holds them.
   - An import whose heartbeat has not reached the database for five minutes stops itself the
     same way, before a worker can take its job for stopped. One whose locks' connection is lost
     is stopped where it is, its job ended `failed` and the family left marked (SAFE-15).
3. **Evidence drift.** Any drifted, unknown, missing or unsnapshotted classification, of a small
   variant or of an SV or CNV, gets 409 (`gate: "evidence_drift"`), unless the request sets
   `acknowledge_drift` with a `drift_acknowledgement_reason` (422 without a reason). One
   acknowledgement covers both; the message says how many are SVs or CNVs. The 409 lists each
   classification it counts, as the snapshot freezes it: small variants in `drifted`, SVs and
   CNVs in `structural_drifted` (without `classified_by` and `classified_at`, which a note or tag
   saved on the review changes). The sign-out dialog lists them as the signed version does.
   Before TRACE-19 the 409's detail was the message alone, and the dialog named no
   classification. Repeat and Paraphase reviews whose frozen calls are no longer the family's
   (`finding_review_drift`, *Evidence drift* above) count in the same gate and acknowledgement.
   They are listed in `finding_drifted` (kind, key, classification, whether reported, status and
   what changed, without the review's time), and the message says how many.
4. **Sample-integrity QC.** 409 (`gate: "sample_qc"`), unless the request sets `acknowledge_qc`
   with a `qc_acknowledgement_reason` (422 without), for:
   - a QC fail (a detected sample or pedigree swap);
   - a check that could not run for lack of data where it is what confirms the pedigree or a
     sample's identity: a relationship or Mendelian check the pedigree asserts, NIPT paternity or
     maternal lineage, or the sex of a sample no relatedness check covers, such as a NIPT parent;
   - in a monogenic NIPT family, a NIPT quality check that failed in the record's `nipt` section,
     under the same acknowledgement: no fetal-fraction estimate, a fetal sex whose paternal-X and
     chrY signals disagree, or male DNA given as the maternal plasma (REQ-NIPT-015; TF-06 H4). The
     409 lists each in `nipt_failed_checks` and in `qc_summary.messages`, the plasma's naming its
     sample ("Sample X (maternal plasma): male DNA …"); the audit event records them
     (`nipt_failed_checks`). Paternity is not among them: the Sample QC reads it from the same
     analysis and gates on it already. NIPT results that could not be read are not captured
     (named in `not_captured`), not a QC concern.

   Each concern names the sample, the pair, the child with its parents, or the NIPT father its
   check read ("Sample X: no chrX genotypes available for sex inference."), in
   `qc_summary.messages` and in each of the groups below, as do the groups the sign-out's audit
   event records. Fetal sex and the NIPT maternal lineage, read from the cfDNA as a whole, name no
   sample. The checks frozen in `sample_qc` keep their own messages beside their sample fields,
   so a record signed before the names were added compares the same; an audit event recorded
   before names none (CLIN-8).

   The 409 lists every concern in its group (CLIN-58): checks that failed (`failed_checks`),
   could not run (`unverifiable_checks`), NIPT quality checks that failed
   (`nipt_failed_checks`), and checks that warn (`warning_checks`), which do not gate on their
   own. A check that warns because it could not run is listed only with those that could not
   run, so each concern is listed once. The message names each kind of concern the gate holds,
   so a failure does not hide a check that could not run, and leaves the concerns to the groups.
   The sign-out dialog of the report page and of the NIPT report shows each group under its
   heading, before the reason. The acknowledged sign-out's audit event records the same groups
   (`qc_failed`, `qc_unverifiable`, `nipt_failed_checks`, `qc_warnings`): what the
   acknowledgement covered. `qc_summary.messages` lists the warnings, failures and failed NIPT
   checks together, as before; an audit event recorded before CLIN-58 holds no `qc_failed` or
   `qc_warnings`.
5. **Incomplete import.** 409 (`gate: "import_incomplete"`, naming the failed datasets and the
   import job), unless the request sets `acknowledge_import_incomplete` with an
   `import_incomplete_acknowledgement_reason` (422 without). Every mark below gates in this one
   409, under the one acknowledgement. Every family page shows *Import incomplete* while one is
   set.
   - **The flag** `metadata.import_incomplete`, set by a package import that left the family
     partly loaded ([data-import.md](data-import.md)), or by a small-variant upload that failed
     after deleting the callset it replaced (with no import job;
     [What an upload records](data-import.md#what-an-upload-records)). Any set flag counts,
     whatever its shape. It clears only once an import has imported each failed dataset again,
     or an upload has brought it again, in any mode, for the same samples and small-variant
     source. An import counts a dataset it imports with warnings, or registers when its package
     carries nothing CoGA imports for it. A later failure keeps an earlier one's failed datasets,
     and every callset or set of samples a dataset failed for, each with its job
     (`failed_scopes`). The gate names each dataset with the job of its latest failure
     (`failed_jobs`), or none for a single-file upload's failure.
   - **A stopped import.** An import whose process stopped part-way sets no flag; the entry it
     wrote in `metadata.import_unfinished` before its first write of the family gates instead
     (`import_unfinished`, naming the job and the datasets it had not finished, which may be
     partly written or missing). Only an import that completes with `overwrite` and imports
     again, for that scope, the datasets it had not finished removes that entry.
   - **A restore that did not finish.** A failed `overwrite` whose restore failed too (other than
     by refusing to start without its whole backup) may have lost rows of any dataset of the
     family, whichever import or upload wrote them. Its flag names every dataset of the import as
     failed, those it imported too. Its `import_unfinished` entry stays, naming every dataset of
     the import and every dataset the family holds in ClickHouse (what its imports and
     single-file uploads recorded in `metadata.imported_scopes`; Postgres, put back in one
     transaction, cannot be left partly written). The entry is frozen with
     `whole_family: true`, so the gate and the signed record name it as a restore that did not
     finish. The gate holds until the whole family has been imported again with `overwrite`, not
     only what failed: each import that completes with overwrite, and each upload with
     overwrite, removes from the entry what it brought again, for that scope; an update removes
     nothing.
   - **An upload or deletion that did not finish.** A single-file upload that writes ClickHouse
     marks the family the same way before its first write there, with an entry with no job keyed
     `upload-…`. So does a deletion of the family's data that deletes or rewrites rows there,
     keyed `deletion-…` (SAFE-31): an admin's deletion of a sample, of its structural variants or
     of the family; a PED upload or manual family that replaces it; a structure change that
     clears its data. A failure leaves the entry listing exactly what may be partly written or
     missing
     ([The mark of an upload that did not finish](data-import.md#the-mark-of-an-upload-that-did-not-finish)).
     The 409's message names "a single-file upload" or "a deletion of this family's data" that
     "did not finish", and what may be partly written or missing. The entry is frozen with its
     key, from which the signed record names what wrote it. Each upload or import with overwrite
     removes from it what it brought again. Deleting a sample removes that sample's items, and
     the sample from the import-incomplete flag's failures and from a stopped import's entry;
     each goes once it names nothing else (SAFE-35).
6. **Findings without a review** (REQ-TRACE-016). A repeat locus or Paraphase region with an
   abnormal call and no review gets 409 (`gate: "unreviewed_findings"`), unless the request sets
   `acknowledge_unreviewed_findings` with an `unreviewed_findings_acknowledgement_reason` (422
   without). A repeat call is abnormal when its status is `pathogenic`, `premutation`, `review`
   or `not_assessed`, or when one of its counts lies near a boundary at a locus whose counting
   units QA has not validated (a grey zone alone is not). A Paraphase call is abnormal when its
   status is `pathogenic`, `carrier` or `review`. A review with a classification, a tag or a note
   counts as a review. The 409 lists each finding in `unreviewed_findings` (kind, key, gene, and
   each abnormal member's sample and status, with `near_boundary` for a repeat), as the record
   freezes it. A repeat or Paraphase section that could not be read is listed as such (`key`
   null, `status: "unreadable"`). The sign-out dialog lists them before the reason.

The acknowledgements and their reasons are part of the hashed snapshot and the audit event.

**An acknowledgement covers the concerns its dialog showed (TRACE-19).** Each 409 of gates 3 to
6 carries `concerns_fingerprint`: a SHA-256 over the canonical JSON (sorted keys) of the gate and
its concerns as the 409 lists them, each list sorted (`concerns_fingerprint` in
`report_signout_service.py`). The concerns are: for evidence drift, `drifted`,
`structural_drifted` and `finding_drifted`; for the Sample QC, its four groups
(`failed_checks`, `unverifiable_checks`, `nipt_failed_checks`, `warning_checks`); for an
incomplete import, `import_incomplete` and `import_unfinished`, each without its `at` (the
message names the time; the jobs and datasets say which failure it is); for findings without a
review, `unreviewed_findings`. It holds no time and nothing that changes while the concerns do
not, so the same concerns give the same fingerprint. The dialog sends it back with the
acknowledgement (`drift_concerns_fingerprint`, `qc_concerns_fingerprint`,
`import_incomplete_concerns_fingerprint`, `unreviewed_findings_concerns_fingerprint`). The
sign-out reads the gate again and compares:

- the fingerprint of the concerns now: the acknowledgement covers them;
- another (a concern arose, went or changed after the dialog listed them): 409 again, with the
  concerns as they are now, their fingerprint and `concerns_changed: true`, and nothing written
  (no signed record, no audit event). The dialog lists them again, says why, and takes a fresh
  reason, sent with the new fingerprint;
- none: 422. The SPA, the only client, always sends the fingerprint its dialog was given. A value
  that is not a SHA-256 in lower-case hex fails the request's own validation (422).

Before TRACE-19 the acknowledgement was a flag and the sign-out read the gate again on
confirmation, so a concern that arose after the dialog listed them was signed out over, as
acknowledged, unseen. A gate without a concern needs no fingerprint, and an acknowledgement of
concerns that went meanwhile acknowledges nothing (as before). The gates are read in order, so a
change at an earlier gate is refused there first; the acknowledgements already given travel on to
the next attempt. The sign-out's audit event records the fingerprint each acknowledgement carried
(the four fields above; None for a gate without concerns). The snapshot freezes the concerns
themselves (the drift sections, `sample_qc`, the import state) and the audit event the Sample-QC
groups, so each fingerprint can be recomputed from the record. The snapshot holds no
fingerprint: the concerns it freezes are those the sign-out checked the fingerprint against.

A lookup that fails while the snapshot is built is frozen as an explicit marker, never an empty
value: missing QC cut-offs as `sequencing_qc.unavailable`; a failed lookup of a reference module,
or an HPO ontology that recorded no release, as module version `unavailable`. The audit event
lists these as `not_captured`, and the report page names them.

The other sign-out endpoints:

| Endpoint | Returns |
| --- | --- |
| `GET /families/{family_id}/report/sign-outs` | the list of signed versions, each with its `report_code` |
| `GET /families/{family_id}/report/sign-outs/{version}` | one signed version, with its `report_code`, `verified` (its content hash recomputed on read) and `not_captured` (what its record could not capture) |
| `GET /families/{family_id}/report/sign-out-check` | whether the report as it would be signed now matches the latest version: `matches`, `changed_sections`, `not_compared`, `not_captured` |
| `GET /report-codes/{code}` | the family and version of the signed record a report code names (*The report code*) |

### The report code

The EPD report quotes the signed record by its report code (`services/report_code.py`). The code
is made from fields of the record that never change, so no column holds it, and records signed
before it existed have one too:

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
version; one never signed out opens on the live report.

**A signed version** (`?version=N`) is rendered from the snapshot that
`GET …/report/sign-outs/{version}` returns, and from nothing else
(`frontend/src/pages/families/signedReportRecord.ts` reads it). It shows:

- the version, signer, time, **report code**, content hash, `verified` and the frozen build, and
  the sentence that says what the record is (the record's own or, for a record signed before
  CoGA froze it, the same sentence);
- each reported variant's description as reviewed, classification, accepted criteria, evidence
  snapshot, tags and note; the reported SVs with their description and ClinGen CNV scoring;
- in a PGT family, the embryo calls (*PGT embryo results*): the ROI, the inheritance model, the
  disease haplotype, and per embryo its call, the homologs it carries at the ROI as the track
  names them (P1, P2, M1, M2), its warnings, its QC at the ROI and, in a record that holds them,
  that QC's verdict and the thresholds it was checked against, and the haplotype file read;
- in a monogenic NIPT family, its NIPT results (*NIPT results*): the fetal fraction and the
  quality checks with their verdicts; the coverage; without a per-target table, the coverage
  check's notes on each coverage track it did not read as depth, word for word, with what the
  check ran for (*Coverage tracks not read as depth: … The coverage check ran without a gene or
  panel, over the family’s region of interest …*), or, for a record signed before CoGA froze
  them, that it does not hold them; the funnel and the categories; each reported variant as the
  NIPT analysis classified it; the recessive fetal risk; and what the analysis ran with. No other
  family's record shows or mentions PGT or NIPT results;
- the gene panels applied; the repeat expansions (in a record that holds them, each count near a
  boundary marked beside its call, *⚠ Near a boundary: 53 is within 3 repeats of 55
  (Premutation)*, and each locus's counting units, *TRGT’s counting units: not validated (no QA
  record)*, or validated or not with the reason, by whom and when); the Paraphase results; the
  coverage tracks (*Coverage tracks*: per sample and caller, what the track held, read depth, a
  log2 ratio or unknown, and the file it was loaded from, from where and when); and the
  reference-data releases;
- the drift, Sample QC, sequencing QC and import state, with the acknowledgements;
- in the footer, with the report code, the frozen modules and beside them the reference-data
  releases and the coverage tracks (*Coverage tracks as signed: S1 coverage BED (package) — read
  depth; S1 WisecondorX (package) — log2 ratio; …*);
- `not_captured`.

What it cannot show, it says:

- A section the snapshot lacks shows as not in the record (*Not in the signed record: version N
  was signed before CoGA recorded …*), never as empty; one frozen as unavailable shows as not
  captured, with its reason.
- A list of a section that the view cannot read (a value that is not a list, an entry not of the
  list's kind) is left out, and the section says so (*Part of … cannot be read here (N lists or
  entries): it is left out below, and the downloaded record holds it as stored*). It used to read
  as empty (TRACE-17).
- A parents' phase correction near the ROI that it cannot read is said where that warning stands
  (*A phase correction the record holds cannot be read here; …*), never dropped unsaid.
- A reported variant without a description it can read, in a record holding descriptions it
  cannot read, names both causes.
- A record without `pgt_analysis` says so only when its Sample QC read the family as a PGT family
  (`sample_qc.application`); any other family's record shows no PGT section.
- What no snapshot holds is said on the page: the population frequencies and in silico
  predictions the live report shows, the segregation, the gene and phenotype context, the audit
  trail and the pipeline settings (and, for a record signed before CoGA froze it, the variant
  description).

Print prints this view, report code included. A printout starts with a notice when the record
fails its content hash, when a later version supersedes it, or when the version list could not be
loaded. On the latest version the page also runs `sign-out-check` and says, on screen only,
whether the family's data has changed since. Straight after a sign-out it says, on screen only,
*Signed out as version N. Report code …: the EPD report refers to this record by it.*

**The live report** (`?view=live`) draws live data. It is where a case is signed out, and it is
never presented as the signed record. It says what a sign-out is (*Signing out is the analytical
sign-off in CoGA, which closes and freezes the analysis; the medical sign-off of the patient
report happens in the EPD, which refers to the signed record by its report code.*), names the
latest signed version with its report code, and checks itself against the latest sign-out
(`sign-out-check`):

- the content matches the signed version: it says so, and is still not the signed report;
- the content changed after sign-out: the changed sections are named;
- in either case, what the signed version does not hold, so the check could not compare
  (`not_compared`), is named after it (*Not in signed version 2, so not compared: …*), with words
  for each section, reference module and part (`describeNotCompared` in `signedReportRecord.ts`);
- the check could not be made: treat the page as unsigned.

Every printout of the live report starts with a notice ("Draft …", "Not the signed report …",
"Not verified …", or that the sign-out record is loading or could not be loaded). When the
clinical audit trail shows only its newest events, the notice says that too ("Incomplete — the
audit trail lists the 200 most recent of its 450 events …"). While the sign-out record is loading
or could not be loaded, sign-out is not offered. After a sign-out the page shows the new version.

**A monogenic NIPT family** is signed out from the NIPT report too (`FamilyNiptReportPage.tsx`,
*Sign out NIPT analysis*, or *Amend sign-out* once signed), through the same request, gates and
acknowledgement dialogs as the live report (`reportSignOut.tsx`, which both pages use); the NIPT
page offers no sign-out. The NIPT report names the latest signed version with its report code,
says whether the family's data still matches it (`sign-out-check`) and links to it; after a
sign-out it opens the new version on the report page. It offers no sign-out outside the
validated assembly scope, nor while the sign-out record is loading or could not be loaded. The
NIPT report is live too: it says so on screen, beside the latest signed version, and every
printout starts with the live report's notice ("Draft …", "Not the signed report …", "Not
verified …").

Each snapshot also records the reference modules its build looked up (`reference_modules`). A
module CoGA adds later is not in that list: for a record signed before it, the check lists
`modules.<key>` under `not_compared` instead of calling the record changed, and `not_captured`
names the missing version. A module in the list but not held was not loaded at sign-out, so one
loaded since is a change. A snapshot without the list names no module, so every reference module
it does not hold is listed as missing.

A record signed before CoGA froze one of the parts below is compared on what it holds: the check
lists the missing part under `not_compared` instead of calling the record changed, and a change
to what it does hold still reads as a change. A record that holds the part is compared whole.

- **A PGT embryo's QC verdict.** A record signed before CoGA checked each embryo's phasing QC
  against thresholds holds no `qc_verdict` and no `rules.qc_thresholds`. Listed:
  `pgt_analysis.qc_verdict` (`pgt_analysis.comparable_with_record`); a call that changed still
  reads as a change. In a record that holds the verdict, a verdict or threshold changed since
  signing reads as a change.
- **The repeat calls' near-boundary warnings.** A record from before holds no
  `count_unit_margin`, no `count_units` per locus and no `count_unit_warning` per allele. Listed:
  `repeat_expansions.count_units`
  (`signed_record_sections.repeat_expansions_comparable_with_record`); a call that changed still
  reads as a change. The signed version shows such a record as signed, with nothing called not
  captured. In a record that holds them, a locus whose counting units QA validated or withdrew
  since signing reads as a change.
- **The NIPT coverage check's notes** over the coverage tracks (TRACE-17). A NIPT record from
  before holds no `coverage_notes` in its `nipt` section. Listed: `nipt.coverage_notes`
  (`signed_record_nipt.nipt_comparable_with_record`); a fetal fraction or classification that
  changed still reads as a change. A record that holds them (as null too, with a per-target
  table) is compared whole: a track declared, loaded or removed since, or a changed region of
  interest, that changes the notes or what the check ran for reads as a change. Without a
  per-target table the signed view says such a record does not hold them (*Coverage tracks not
  read as depth: not in the signed record …*); with a table the check read its targets and noted
  no track.

A section CoGA has since computed differently reads as changed too. The Sample QC now counts a
check that could not run as `warn`, with a note naming each sample whose sex could not be checked.
So for a family with such a sex check, the check lists `sample_qc` as changed against a version
signed before: that record holds no such note, and holds `pass` where the other checks passed.
The signed version still shows what was signed; signing out again records the new verdict.

A signed version lists the SV/CNV classifications whose evidence had moved at sign-out with the
small variants', under *Evidence drift at sign-out*, and says when its record holds no SV/CNV
drift check. A small variant's entry names the frozen values that had moved, with the values
(*REVEL 0.412 → 0.951*, *LoF LC → HC*, *gnomAD missense Z 2.95 → 3.52*). Under *Evidence when
classified*, each reported small variant shows the consequence, frequencies and in-silico scores
its snapshot froze from the primary transcript, and in a sentence of its own a missense variant's
gene missense Z with where PP2 read it (*From its gene's profile: gnomAD missense Z 3.52.*, *From
the VCF's MISSENSE_Z, as its gene's profile held none: …*, or *Neither its gene's profile nor the
VCF held a gnomAD missense Z.*); one frozen before the source was reads as the gene profile's,
which it was. A group the snapshot did not freeze is left out, so a snapshot from before CoGA
froze any of them shows its ClinVar significance and annotation version only. The check lists the
`drift` section as changed for a version signed before a small-variant drift entry named what
moved (CLIN-7), while it holds such an entry; and for one signed before the consequence and
missense Z were frozen, or before the missense Z's source was (TRACE-15), while it holds a
missense variant's entry. The entry the check recomputes also holds them as they are now
(`evidence_to`).

The release candidate's snapshot format is the first one CoGA reads: records signed by earlier
development builds get no reading of their own (the owner's decision of 2026-09-30). They still
verify, since verification re-hashes the record as stored.

Both views download the frozen record as JSON.

### Adding a section to the signed record

The PGT embryo calls (`pgt_analysis`) and the NIPT results (`nipt`) were added as sections of
what the analysis looked at. A new section is added in four places, or it is missing from the
signed view without a trace.

A section only one application's families hold names that application in its `RecordSection`
(`application="pgt"`, `application="nipt"`). `build_record_sections` builds it for those families
alone, so another family's record holds no such key and stays as it was. The sign-out check
compares it where the record or the family's current data holds it, names it as not compared
only where the data holds it and the record does not, and neither compares nor lists it where
neither holds it. The signed view renders it only for a record of that application, read from
the section itself or, for a record signed before it, from `sample_qc.application`
(`PgtAnalysisSection`, `isNiptRecord`).

1. **Its builder** in `backend/app/services/signed_record_sections.py`: an async function that
   reads what the analysis showed the analyst and returns a JSON-safe, deterministic dict (lists
   sorted, no clock, no random order: the sign-out check compares it right after sign-out,
   through the JSONB round trip). Give it a `RecordSection` (its key, the words that name it, the
   fixed sentence frozen when it cannot be read, and for one application's section its
   `application`), add it to `RECORD_SECTIONS` and call it in `build_record_sections` through
   `_guarded`. It is then frozen and hashed with the record, compared by `sign-out-check`
   (`REPORT_CONTENT_SECTIONS` holds every key of `RECORD_SECTIONS`), listed as `not_compared`
   against a record signed before it, and named in `not_captured` when it could not be read.
2. **Its reading** in `frontend/src/pages/families/signedReportRecord.ts`: a field of
   `SignedReport` parsed with `parseRecordSection`, which gives the absent, unavailable and
   recorded states; and its label in `REPORT_SECTION_LABELS`, which names the check's changed and
   not-compared sections (a part of a section the check leaves out of the comparison for an older
   record gets its words in `NOT_COMPARED_PART_LABELS`).
3. **Its rendering** in `SignedFamilyReport.tsx`, through `RecordSectionBlock`, which says what a
   record that does not hold the section lacks and what one could not capture.
4. **Its description** here, under *Sign-out*, and in the in-app reference and handleiding
   chapter 11, with tests of each (`test_signed_record_sections.py`,
   `signedReportRecord.test.ts`, `FamilyReportPage.test.tsx`).

## Tamper evidence and its limits

- **Append-only tables.** Database triggers refuse UPDATE and DELETE on `audit_log_events`,
  `ui_events`, `clinical_audit_events`, `report_signouts`, `integrity_anchors`,
  `qc_threshold_changes` and `family_structure_versions` (see [database.md](database.md)). The
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

- The variant description holds no population frequency or in silico prediction, and the
  snapshot no gene or phenotype context: a signed version says what its record lacks, and only
  the live report shows the rest, from current data. A record signed before CoGA froze the
  variant description (2026-10-14) names each variant by its ID alone. There is no byte-stable
  PDF: a signed version is printed from the browser, laid out by the build that renders it (named
  in its footer).
- The gene reference is frozen for the reported variants' genes only: a record without a reported
  variant names no gene-reference release. The gene loci the panel filters read are in the
  manifest (`gene_loci`). The clinical CNV knowledgebase states no release of its own. The record
  names the files a rebuild read: the pinned one, ClinGen's recurrent CNV file, by release and
  SHA-256; the rest (the ClinGen dosage curation, ClinVar, the cytobands, Orphanet and a DECIPHER
  map, as they were that day) by file name and SHA-256 (TRACE-18). A rebuild loaded before the
  pin (TRACE-14) reads `not stated`; one loaded before TRACE-18 names its pinned file alone.
- A panel version is recorded the first time a search of the family applies it, not at each
  search. A panel applied in the variant explorer, which searches across families, is recorded
  for no family. Searches before the `panel_applied` event existed recorded nothing, so a record
  signed for such a family names only the panels applied since.
- A repeat-expansion call's and a Paraphase result's status is frozen as CoGA classifies it at
  sign-out, from the catalogue loaded then. The repeat page and the genome tracks classify a call
  alike, against the current catalogue (CLIN-14; *TRGT repeat catalogue*,
  [data-import.md](data-import.md)).
- Anchors are made by hand: nothing in the code, CI or Terraform calls
  `POST /admin/integrity/anchor` on a schedule.
- Exporting each anchor to a store outside the database is not implemented (the `export_anchor`
  hook does nothing), so deleting the latest anchors is not detected.
- Verification trusts only the configured signing key. After a key rotation, anchors signed with
  the old key report `unknown_key`; keep the old public key for audits.
- `coga_app` keeps INSERT on the chained tables, so it could append a forged but self-consistent
  row; it cannot rewrite or delete existing ones.
- The manifest's platform layer covers the assembly, the gene loci, Monarch and HPO. The releases
  of the gene reference, PanelApp and the clinical CNVs are frozen beside it (`reference_data`,
  `gene_panels`), not in it, so `GET …/annotation-manifest` and the live report's footer do not
  show them.
- Any analyst of the family's project, and any admin, can sign out; a reader cannot. Sign-out is
  not limited to a signer role.
- CoGA does not see the clinical report in the EPD. Nothing in CoGA checks that the report refers
  to a signed version, which one, or that it says what that version holds; whoever drafts the
  report writes the reference into it. The report code makes that reference short and refuses a
  mistyped one, and `GET /report-codes/{code}` finds the version it names, but CoGA does not see
  where a code is quoted.
- The embryo calls are frozen as the backend makes them at sign-out. The haplotype tracks of the
  genome overview and the chromosome view show each member's risk state from the browser's
  implementation of the same rules, which the parity cases pin but do not prove equal on every
  input.
- One QC measure gates an embryo call; the others only warn. The QC is counted over the ROI and
  1 Mb on each side. An embryo whose markers there, on a parental side the call reads, mix both of
  that parent's homologs (a discordance above 10 % over at least 20 markers, `mixed_homolog_sides`
  in `services/pgt_embryo_qc.py`) is not called: it reads as uninformative, whatever its blocks
  say (the owner's decision of 2026-10-30). This gate is a safety net, not an aneuploidy check:
  two copies of one homolog leave the markers in agreement, and a trisomy away from the ROI is
  not looked at. A call whose phasing QC does not meet the other thresholds of
  `services/pgt_embryo_qc.py` (at least 50 informative sites, a Mendelian error rate of at most
  2 %, at least 6 markers on each flank of the ROI on each parental side the call reads) carries a
  warning beside it on the family page and in the signed version; so does one whose QC was not
  counted. Such a call stands, the sign-out is not held up, and the analyst decides (the owner's
  decision of 2026-10-20). The markers are counted on each flank apart, left and right of the ROI;
  the ROI's own sites count on neither. The thresholds are provisional: the laboratory director
  and QA are to confirm them.
- A NIPT family's record freezes the NIPT classification of the variants tagged `report`, not
  every candidate the NIPT report or a view of the NIPT page lists, and their recessive fetal risk
  over the reported variants alone. It freezes the coverage of every capture target and each gene
  with a weak target, not the coverage of a search's gene panel or genes as the NIPT page's
  coverage card reads it. Without a per-target coverage table it holds the coverage check's notes
  on the plasma's coverage tracks (`nipt.coverage_notes`), not the coverage the check read or its
  verdict. The check is run as the NIPT page runs it without a gene or panel, over the family's
  region of interest, not for the genes or panel of a search; `coverage_tracks` says which kind of
  coverage it rested on. The artifact list and the de novo recurrence are shared by every family
  of the assay: a change to the list, or another pregnancy of the assay imported since, makes the
  sign-out check name `nipt` as changed.
- `coverage_tracks` names each coverage track of the members as their records list it, not the
  chromosomes a depth check read: the mtDNA coverage reads a track only where it has rows on
  chrM, the NIPT coverage check its targets' chromosomes, and a sample with two read-depth tracks
  is read from its coverage BED. The mtDNA coverage and its verdict are not frozen. A coverage
  BED loaded before 2026-10-20 has no record of where it came from (`loaded_from` null, *origin
  not recorded* on the signed version).
