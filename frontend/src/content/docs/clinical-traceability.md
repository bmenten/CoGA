# Report traceability & sign-out — reference

The **report** (the **Report** button on the family page) is where a case is traced and signed out. It
ties the reported result to what produced it — the annotation and reference versions, the reported
variants, each classification and its evidence — and sign-out freezes that into a signed record that
can never change.

**Sign-out is the analyst's analytical sign-off, not the medical one.** It closes the analysis and
freezes it. The clinical report is drafted and medically signed in the hospital's electronic patient
record (EPD), outside CoGA, and refers to exactly one signed version of the analysis, by its **report
code** (below). CoGA does not see the EPD report and does not check that reference.

How to prepare and sign out a report is in the [user guide](/docs), section *Clinical report and
sign-out*. This page holds the rules.

---

## Why traceability matters

A reported result is only defensible if you can answer later, exactly: *which data, which versions and
whose decisions produced this?* Annotation sources move — a new ClinVar or gnomAD release — so a
classification made last month may rest on evidence that has since changed. CoGA makes the versions,
the changes and the decisions explicit and permanent.

The report carries four things.

## 1. Provenance footer — which versions

A footer at the end of the report states when it was generated and which versions backed the data:

- the **pipeline** layer: the tools that produced the family's annotated input (for example VEP,
  ClinVar, gnomAD, dbNSFP, SpliceAI, GenCC, PanelApp);
- the **reference** layer: what CoGA itself loaded — the genome assembly (with its release date), the
  gene loci (their source and import date), the Monarch release and the HPO release (with its date).

The pipeline versions are taken from the family's import manifest; there is no screen to edit them.
Only an admin can replace them, through the API, and the replacement is written to the audit trail
(section 3); a later import leaves it in place. A version that is not known is left out, never
guessed. The footer prints with the report and is part of the signed record.

The footer also names the software: *Software: CoGA X.Y.Z (commit)*, the build that produced the page,
followed by the in-house IVD statement and the manufacturer. The report asks for the build each time it
opens. A signed version's footer names two builds: *Signed with*, the build that froze it, and *Rendered
by*, the build that drew the page from its record. After a CoGA update the two can differ.

## 2. Evidence-drift banner — has anything changed

When you save a classification, CoGA freezes the evidence it rests on:

- **ACMG classify** (a small variant): a fingerprint of the variant's annotation, its ClinVar
  significance, and the population frequencies and in-silico scores the dialog read — the gnomAD
  frequencies (overall, exomes, genomes, popmax), the TOPMed frequency, the gnomAD allele,
  homozygote and hemizygote counts, CADD, REVEL, SpliceAI, SIFT, PolyPhen and AlphaMissense —
  taken from the variant's primary transcript, as the dialog shows them;
- **ACMG (CNV)** (a structural variant or CNV): what the CNV classifier reads — the type, the genes the
  event overlaps and their number, the pLI and the annotated inheritance — with the event's caller and
  position, a fingerprint of its annotation, and the annotation versions its genes came from. The
  classifier reads no clinical CNVs, dosage scores or DGV; you score those criteria yourself. When
  several callers call the same event (a NeedlR call and an uploaded Sniffles call, for example), each
  is a row of its own with its own genes, pLI and inheritance, and the rows share one review: the
  classification freezes the evidence of the row you opened it from.

When you open the report, each frozen classification is compared with the current data. An amber
banner lists every classification whose evidence changed:

- the ClinVar significance changed (for example *ClinVar Uncertain significance → Pathogenic*);
- a frozen frequency or in-silico score changed, or a value missing then is there now: each is
  named with its value then and now (for example *REVEL 0.412 → 0.951* or *gnomAD AF 3.10e-5 →
  0.012*), and so is a change of the primary transcript they are read from;
- the annotation changed in another way, or cannot be compared because a fingerprint is missing
  (*annotation set changed*). A classification saved before CoGA froze the frequencies and
  in-silico scores is compared on its fingerprint and ClinVar significance alone, so its banner
  line does not say which value moved;
- for a structural variant, what moved in the row of the caller its evidence was frozen from: for
  example *genes TCF4, TXNL1 → TCF4* or *pLI 0.990 → 0.410*, and likewise its inheritance, type or
  position. It says *annotation changed* when only its other annotation changed, and *its frozen
  evidence cannot be compared* when the frozen evidence cannot be read or does not name its caller;
- the variant is no longer in the data (for a structural variant: the call of the caller its evidence
  was frozen from, even when another caller still calls the event).

Re-review a listed variant before sign-out. Saving its classification again freezes the evidence as it
is now.

**Reported variants without frozen evidence.** A variant tagged **Report** that was never saved through
**ACMG classify**, or a structural variant never saved through **ACMG (CNV)**, has no frozen evidence,
so its evidence cannot be checked. The banner does not list it, but sign-out does: it counts as drift
and needs an acknowledgement (see *The sign-out checks*).

## 3. Classification audit trail — who did what, when

The **Classification audit trail** lists, most recent first, every clinical action on the family's
small variants, structural variants and CNVs: who classified, tagged or annotated which variant, when,
and what changed (before → after), including each sign-out, each replacement of the pipeline versions
and each change to the family's phenotypes (an HPO term added, changed or removed). A change to
the criteria behind a class is listed too, even when the class stays the same: a criterion accepted or
rejected, its strength (for a CNV, its points) or its evidence changed, a suggestion that appeared or
went. Clearing a review is listed too.

- *Classification VUS (class 3) → Likely pathogenic (class 4)*
- *ACMG criteria updated (Likely pathogenic (class 4)): PM2 moderate → supporting; total 7 → 6*
- *CNV classification VUS - class 3 → Pathogenic - class 5*
- *Tags added report*
- *Annotation manifest replaced (was vcf_header, now manual): VEP 110 → 112*
- *Phenotype changed for S1: HP:0001250 Seizure: status present → absent*
- *Gene panel Epilepsy version 2 (PanelApp panel 402, version 3.1) first applied to the small-variant search*
- *Report signed out (v2, report code 7KQM2X-2-4PHT) — 3 reported variant(s)*

A search of the family that applies a gene panel (the small-variant and structural-variant lists and
their exports, the NIPT variants and coverage) writes the panel to the trail the first time it applies
that version of the panel: its name, local version and, for a PanelApp panel, PanelApp's version of it.
Applying the same version again writes nothing; a new version of the panel is written again. A panel
applied in the variant explorer is not written for any family.

Each entry is written together with the change itself, and entries can never be changed or deleted.
This is the record of clinical actions; the record of who opened what (**Admin → Audit Logs**) is
separate. Changes to the pedigree or the family members, and the review of a
compound-heterozygous pair, are not in this trail; **Admin → Audit Logs** records them. The trail is
for analysts and administrators: a reader of the family's project does not see it.

The trail opens on its 200 most recent entries. When it holds more, it says how many it shows
(*Showing the 200 most recent of 450 events; the older ones are not shown.*), and **Show older
events** adds the next 200. A printout of a report that shows only part of the trail says so at the
top of every page.

## 4. Case sign-out — freeze the result

**Sign out report** is the analyst's sign-off of the analysis. It closes the analysis and freezes the
reported result into a numbered version with a unique fingerprint (a SHA-256 content hash), the version
the clinical report in the EPD refers to:

- the provenance footer (the versions);
- the reported small variants, each with its classification, ACMG criteria, tags, note and frozen
  evidence;
- the reported structural variants and CNVs, with their classification, CNV criteria, tags, note and
  frozen evidence;
- the drift state, the Sample QC verdict, and the sequencing-QC verdicts with the cut-offs they were
  judged against;
- whether the family's data imported completely, and if not, which datasets failed;
- what the analysis looked at: each reported variant's **description** as you reviewed it (gene,
  transcript, HGVS c. and p., consequence and each sample's genotype; for a structural variant its type,
  copy change, position, genes and each sample's genotype and copy number), the **gene panels** the
  family's searches applied (each version, with PanelApp's version of a PanelApp panel), the
  **reference-data releases** (the gene reference for the reported genes and the clinical CNV
  knowledgebase), and the family's **repeat expansions** (TRGT) and **Paraphase** results, each with
  its status as CoGA reads it at sign-out and the catalogue behind it;
- in a PGT family, the **embryo calls** at the region of interest, the calls the family page shows: the
  ROI, the inheritance model, the disease haplotype, and for each embryo its call, the haplotypes it
  inherited at the ROI, its warnings (missing data, an unrecorded sex, a recombination or a parent's
  phase correction near the ROI), its QC at the ROI (informative sites and Mendelian error rate), and
  the haplotype file the calls were read from;
- a sentence that says what the record is (the analytical sign-off, referred to by the EPD);
- the CoGA software version that produced it.

A signed version can never change; any later tampering would show as a fingerprint mismatch. A part
that could not be read while the version was frozen is recorded as not captured, never as empty.

### The report code

Each signed version has a **report code**, for example `7KQM2X-2-4PHT`: six characters that every
version of the family's record shares, the version number, and four characters tied to the signed
content. Quote it in the EPD report. A code is unique, and its last character is a check: a mistyped
character or two swapped neighbours is refused as a typo, never taken for another record's code. Upper
or lower case and the hyphens do not matter, and an I or L reads as 1, an O as 0.

The code is on the signed version, on screen and in print (top card and footer), on the live report
next to the latest signed version, and in the audit trail entry of the sign-out. Straight after you sign
out, the page confirms *Signed out as version N. Report code …*.

To find a signed record from its code, open **Report codes** in the main menu (the arrow at the right of
the header), enter the code, and open the signed version it names. The EPD can link to
`/report-codes/<code>` directly. A code that no record you can open has gives the same answer whether
no record has it or it belongs to a family outside your projects.

### The sign-out checks

Sign-out stops at each of these, in this order:

| Check | Stops when | To go on |
| --- | --- | --- |
| **Assembly scope** | The family is on an assembly outside the validated scope (GRCh38 unless the laboratory set otherwise). The pages carry *Not validated for clinical use*. | No override: the report cannot be signed out. |
| **Data being written** | A data import of the family is queued or running, or its data are being written (an upload, or a deletion of its variants). | No override: sign out once it has finished. |
| **Evidence drift** | A reported classification, of a small variant, a structural variant or a CNV, drifted (banner above), or has no frozen evidence. | Re-review, or acknowledge with a reason (*Evidence drift — acknowledgement required*). |
| **Sample QC** | Sample QC failed, or a check that confirms the pedigree or a sample's identity could not run for lack of data (a parent–child or sibling relationship, a Mendelian check, NIPT paternity or maternal lineage, or the sex of a sample that no relatedness check covers, such as a NIPT parent). | Acknowledge with a reason (*Sample-integrity QC — acknowledgement required*). |
| **Incomplete import** | A data import for the family partly failed, or stopped part-way, so some of its data is missing or partly written. The pages carry *Import incomplete* (below). | Import what failed again, or acknowledge with a reason (*Incomplete import — acknowledgement required*). |

An acknowledgement and its reason are frozen into the signed version and written to the audit trail,
so "signed out over a known problem, and why" is part of the permanent record.

The Sample QC dialog names, in front of each concern, the sample, the pair, the child with its parents
or the NIPT father its check is about, for example *Sample X: no chrX genotypes available for sex
inference.* The audit trail records the checks that could not run the same way. Fetal sex and the NIPT
maternal lineage are read from the cfDNA as a whole and name no sample.

An import or upload that starts while a sign-out records the family either waits for the sign-out to
finish or refuses the sign-out, so a signed version never mixes the family's data from before and after
a write.

If the family's project cannot be loaded, the assembly — and so the scope — is not known: the pages say
*Validated scope not confirmed*, with **Retry**, and the report waits until it loads. If the family
itself cannot be loaded, neither its import state nor its assembly is known: a family page then shows
only *Family could not be loaded*, with **Retry**, and none of the family's data (a report: *Report
could not be loaded*).

### Import incomplete

If a data import fails for some datasets and leaves the family partly loaded, CoGA keeps what did
load and marks the family as incomplete. Every family page, the reports included, then shows
*Import incomplete*: the datasets that failed and those that did import, when, and the import job
whose record holds each dataset's error. The warning prints with the report, and with the NIPT
report, which has no sign-out to stop on it.

Results on such a family can lack whole datasets, for example all its structural variants. Import
the datasets that failed again to complete the family: the warning goes once each has been imported
again, and an import without them leaves it. A family small-variant upload that fails after it has
deleted the calls it replaced marks the family the same way, for those calls and with no import job;
uploading them again, or importing them, removes the warning. If a later import fails too, the warning names both,
each with its import job; a dataset that fails again for another callset or other samples stays named until
each has been imported again. A failed overwrite is put back as it was before the import; if that fails
too, any dataset of the family may have lost data, whichever import or upload it came from, so the
warning names every dataset of that import as failed, the ones that imported too, and every dataset
the family holds as possibly partly written or missing. It stays until the whole family has been
imported again with **overwrite**, not only what failed, and what an upload brought has been uploaded
again with **overwrite**. Signing out before then needs an acknowledgement with a reason, and the
signed version records which datasets were missing.

An import also marks the family before it writes anything of it, and removes the mark when it ends.
While it runs the pages carry *Import incomplete* too. If it stops part-way (the server restarted,
or ran out of memory), the mark stays: the warning names the import job and the datasets it had not
finished, which may be partly written. Re-import those with **overwrite**: an update skips data that
is already there, partly written data too, so only an overwrite removes the warning.

A single upload of small variants, structural variants or a track marks the family the same way
before it first writes to the variant store, and removes the mark when it has finished. If the server
stops in the middle of it, or it fails and cannot put back what it had deleted, the warning names "a
single-file upload" and what may be partly written or missing; so does the refusal at sign-out, and
a version signed over it records the upload. An administrator's deletion of a sample's structural
variants that cannot put the family's other calls back marks it too ("a deletion of a sample's data").
Upload again, or import again, with **overwrite** what the warning names: each part goes as it is
brought again.

### Who may sign out

Only people the laboratory has authorised to sign out an analysis. CoGA lets any analyst of the
family's project press **Sign out report** (a reader cannot) and does not check that authority itself. It records who signed each version,
in the signed record and in the audit trail. The medical sign-off of the clinical report is given in
the EPD, under its own signing rights.

### Amendments

Signing out again creates a new version (v2, v3, …); earlier versions are never overwritten. Once a
case is signed out, the button on the live report reads **Amend sign-out**. A clinical report that rests
on the amended analysis refers to the new version.

---

## The signed version and the live report

The report page has two views. Only the signed version is the signed report.

### The signed version

A case that has been signed out opens on its **latest signed version**. The page is drawn from the
frozen record alone; nothing on it is read from the family's current data. Its top card says *Signed
version 2 — signed out by … on …*, with the report code, the sentence that says what the record is,
the fingerprint (content hash), the build that signed it, and whether the stored record still matches
its fingerprint.

It shows what the record holds:

- each reported small variant, named by its variant ID, with its description as you reviewed it, its
  classification and points, the accepted ACMG criteria and their evidence, the evidence frozen when it
  was classified (the ClinVar significance and the annotation version and, for a classification saved
  since CoGA froze them, the population frequencies and in-silico scores as they were then, read from
  its primary transcript), its tags and its note;
- each reported structural variant or CNV, with its description, classification and ClinGen CNV
  criteria;
- in a PGT family, the embryo calls (*PGT embryo results*): each embryo's call at the ROI, the
  haplotypes it inherited there as the track names them (P1, P2, M1, M2), its warnings and its QC;
- the gene panels applied, the repeat expansions, the Paraphase results and the reference-data releases;
- the evidence drift, the Sample QC, the sequencing QC and the import state at sign-out, with any
  acknowledgement and its reason;
- the versions it was signed with and the reference-data releases, in the footer, with the report code.

The record does not hold the rest of what the live report shows: the population frequency and the in
silico predictions, the segregation and the family's members, the gene description, conditions and
panels, the phenotype match, the audit trail and the pipeline settings. The page says so, once for the
report and on each variant, and never fills them in from current data. A part the record does not hold
is marked *Not in the signed record*, never shown as empty: a version signed before CoGA recorded the
variant description, the gene panels, the reference-data releases, the repeat expansions or the
Paraphase results says so for each (*Not in the signed record: version 2 was signed before CoGA
recorded …*), and so does a PGT family's version signed before CoGA recorded the embryo calls.

**Print signed version N** prints this view. The printout starts with a notice when the version is not
the latest, intact record: *Do not use — …* when the stored record does not match its fingerprint,
*Superseded — …* when a later version replaces it, *Not confirmed as the latest signed version — …*
when the versions could not be listed.

*Signed versions* at the top lists every version; an earlier one names the version that replaces it.
On the latest version the page says whether the family's current data still matches it, or what has
changed since (a review, a report tag, a re-import, a QC cut-off). That line is not printed: the
version stays what was signed. Sign out again to issue a new version.

**Download signed version N (JSON)** returns the frozen record itself.

### The live report

**Open the live report** shows the current data: the variant descriptions, the gene context, the
phenotype match, the drift banner and the audit trail. It is where the case is signed out. It is never
the signed report. Its top card says *This is the live report, not signed
version 2*, and whether it still matches that version:

- *This page still matches signed version 2* — even then, print the signed version for the record;
- amber — something changed after sign-out, and the card names what;
- grey — *This page could not be checked against signed version N — treat it as unsigned.*

Every printout of the live report starts with a notice: *Not the signed report — …*, or *Draft — this
report has not been signed* for a case that was never signed out. After **Sign out report**, the page
shows the version you signed.

**What a signed version could not capture.** If a lookup fails while the version is frozen — the QC
cut-offs, or the version of the assembly, the gene loci, Monarch or HPO — sign-out still goes ahead, but
that part is recorded as unavailable, not as empty, and the signed version says so: *Not captured in
signed version 2: …*. The audit trail lists the same parts. An HPO ontology imported from a file that
recorded no release is marked the same way (*release not recorded*).

A reference module CoGA starts recording after a version was signed is not in that version. The signed
version says so (*… (signed before CoGA recorded its version)*), and that alone does not count as a
change.

A part CoGA now computes differently does count. The Sample QC now counts a check that could not run
as a warning and names each sample whose sex could not be checked, so for a family with such a check
the page says the *sample-integrity QC* changed since a version signed before: that version holds no
such note, and *Pass* where the other checks passed. The signed version still shows what was signed.
Sign out again to record the new verdict.

**When part of the report cannot be loaded.** The page never shows a part it could not load as empty.
If a signed version cannot be loaded, the page shows only *Signed version N could not be loaded*, with
**Retry**. If the family or a list of reported variants cannot be loaded, the live report shows only
*Report could not be loaded*, with **Retry**. If the sign-out record cannot be loaded, the live report
says it is not known whether the case is signed. Until that record is known, sign-out is not offered.
Any other part that failed (a gene description, the HPO terms, the drift check, the audit trail, the
annotation versions, the CoGA version) is marked where it belongs, and a printout starts with
*Incomplete — … could not be loaded*. A list of reported variants that holds more than the report reads
(10,000 of each kind) is marked *Incomplete* too, on screen and in print. The NIPT report does the same for the fetal fraction, the coverage
check, the name of its gene panel and the CoGA version.
