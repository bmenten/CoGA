# ClinGen recurrent CNV regions, GRCh38 — CoGA's note

`ClinGen_recurrent_CNV_V2.1-hg38.bed` and `README` in this folder are ClinGen's files, kept
byte for byte as ClinGen publishes them: never edit them; replace them with a new download.
This note is CoGA's.

| Field | Value |
| --- | --- |
| Source | ClinGen Dosage Sensitivity Curation, recurrent CNV regions (<https://dosage.clinicalgenome.org/>) |
| File | `ClinGen_recurrent_CNV_V2.1-hg38.bed` (GRCh38) |
| Release | **V2.1** (ClinGen moved its recurrent CNV files to V2.1 on 2026-03-18, as its `README` says) |
| SHA-256 | `e6a7428f58b055e51a956941da85cdc984d9519ea7839c365c9b557ccc76a763` |
| Checked | 2026-10-14, with the owner's permission: byte-identical to ClinGen's V2.1 GRCh38 file downloaded from ftp.clinicalgenome.org (the same SHA-256) |
| Track line | `track name="ClinGen_recurrent_CNV_V2.0-hg38"`: ClinGen's own V2.1 file says V2.0 there. The release is V2.1, as the file name says |

## How CoGA uses it

At startup the backend loads the file's segmental-duplication clusters (its black intervals)
as the GRCh38 segmental duplications/LCR track (`REFERENCE_SEGMENTAL_DUPLICATIONS_PATH`). The
import skips the track line and records the file name as its source, so the reference
catalogue's *Recent reference activity* list shows `ClinGen_recurrent_CNV_V2.1-hg38.bed`.
Nothing in CoGA shows the V2.0 track name. The clinical CNV knowledgebase build does not read
this file: it downloads ClinGen's V2.1 files itself.

## Updating (controlled change)

Replacing the file is a TF-18 controlled change. Download ClinGen's file and keep it byte for
byte under ClinGen's file name, which carries the release (the default of
`REFERENCE_SEGMENTAL_DUPLICATIONS_PATH` and `REPO_SEGMENTAL_DUPLICATIONS_PATH` in
`reference_metadata_service.py` name it). Record the release, its SHA-256
(`shasum -a 256`), the date and how it was checked, here and in
[TF-08](../../../docs/regulatory/TF-08-soup-register.md).
[`test_clingen_recurrent_cnv_bundle.py`](../../../backend/tests/test_clingen_recurrent_cnv_bundle.py)
pins the SHA-256 and fails until the file, this note and TF-08 agree.
