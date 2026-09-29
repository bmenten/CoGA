# Annotation and tool provenance

For every family, CoGA records which versions of which tools and databases produced its
data: the variant callers (DeepVariant, GATK, Sniffles, Spectre, TRGT, …), the annotation
engine (VEP, snpEff, bcftools) and the reference databases it names (gnomAD, ClinVar,
dbNSFP, SpliceAI, dbSNP, GENCODE, …). The versions are read from the input files at import,
kept per family, shown on the filter pages and the report, and frozen into every sign-out.

A result must be traceable to exactly what produced it (IVDR Annex I §16.1; see
[TF-09](regulatory/TF-09-verification-validation.md), "Traceerbaarheid"). This record is the
per-family annotation manifest described in
[clinical-traceability.md](clinical-traceability.md); this page covers how it is captured.

## What is captured

The `##` header lines are parsed by
[`vcf_header_provenance.py`](../backend/app/services/vcf_header_provenance.py).

| Input | Captured by | What is read |
| --- | --- | --- |
| Small variants (SNV/indel), package or direct upload | `variant_upload_service.upload_family_small_variant_file` | the VCF header: caller (`##source`, `##DeepVariant_version`, `##GATKCommandLine`), annotation engine (`##VEP=…` with the gnomAD, ClinVar, dbNSFP, SpliceAI, dbSNP, COSMIC, SIFT, PolyPhen, assembly and GENCODE releases it embeds; `##SnpEffVersion`; `##bcftools_*Version`); and, when the annotation comes as a separate VEP table, that file's `## … version …` lines (`extract_vep_tab_provenance`) |
| Structural variants from a package (NeedlR) | `family_package_datasets._import_sv_needlr_dataset` | the SV caller (`##source=Sniffles2_…`, Spectre, NeedlR), `##reference`, `##fileDate`, and database releases named in the `##INFO` descriptions (`extract_info_description_provenance`: GENCODE, OMIM, GenCC, gnomAD, GIAB, ClinVar, dbSNP, COSMIC, each tied to a version-shaped token) |
| Repeat expansions (TRGT), family or per sample | `repeat_expansion_pg.ingest_family_trgt_text`, `ingest_trgt_text` | `##trgtVersion`, `##trgtCommand`, `##source=TRGT`, `##reference` |
| The pipeline run record of a long-read package | `family_package_datasets._import_pipeline_info_dataset` | not a VCF: the Nextflow `software_versions.yaml`, recorded with `source='manifest'`. It names every tool behind the data, including tools whose outputs carry no version. The run parameters go to `families.metadata["pipeline"]`. |

The parser is best-effort and never raises: a header it does not recognise yields less
information, never an error, so capture cannot fail an import.

## How it is stored

One row per family in `family_annotation_manifest`
([04_traceability.sql](../backend/db/schema/postgres/04_traceability.sql)):

| Column | Meaning |
| --- | --- |
| `modules` (JSON) | `{ moduleKey: { version, detail, by_modality } }`, one entry per tool or database |
| `source` | `vcf_header` (from headers), `manifest` (from the package) or `manual` (entered through the API) |
| `recorded_by`, `recorded_at` | who or what wrote it, and when |
| `assembly_id` | the family's assembly |

The parser normalises module keys (`vep`, `deepvariant`, `gnomad`, `clinvar`, …), so the
versions from different input files merge into one manifest. The variant caller is tagged
with its input (`detail: "snv caller"`, `"sv caller"`, `"repeats caller"`), so a family can
list several callers.

Different inputs can cite different releases of the same database: the SNV annotation may
state GENCODE 49 while the SV annotator states GENCODE 45. Each module therefore keeps
`by_modality` (for example `{"snv": "49", "sv": "45"}`) next to a single `version`, which is
the latest write.

## The update rules

All in `merge_vcf_header_provenance`
([annotation_manifest_service.py](../backend/app/services/annotation_manifest_service.py)):

- **A re-import refreshes.** The newly read versions replace the old ones key by key.
  Modules the new input does not mention are kept, so re-importing only the SV file does not
  remove the SNV-derived versions. The manifest always reflects the latest import.
- **A manual manifest is never overwritten.** Once the manifest was set through
  `PUT /families/{family_id}/annotation-manifest` (`source='manual'`), imports leave it alone.
- **It commits with the data.** The write runs in a savepoint inside the import's transaction,
  so the provenance is stored exactly when the data it describes is, and a provenance failure
  cannot roll back an import.
- **History lives in the sign-outs.** The table holds the current manifest only. Each
  sign-out freezes a copy into its content-hashed snapshot.

The manifest names versions; it does not detect change. That is the job of the
`annotationSetHash` that ClickHouse keeps per variant: it changes whenever a variant's
annotation changes, and the drift check compares it with the hash frozen at classification
time. The hash proves that something changed; the manifest says which versions are in use.

## Where it is shown

- **Filter pages.** `AnnotationProvenanceSummary.tsx` shows "Annotation versions" at the
  bottom of the small-variant and structural-variant pages, each with its own input's
  versions plus the shared reference modules, and the source (for example "from VCF
  headers").
- **Report.** `FamilyReportPage.tsx` shows the full list ("Modules & versions"), with the
  per-input values where they differ (for example `GENCODE 49 (snv), 45 (sv)`); sign-out
  freezes it.
- **API.** `GET /families/{family_id}/annotation-manifest` returns the merged list;
  `PUT /families/{family_id}/annotation-manifest` replaces it (`source='manual'`).

## Limitations

- Direct structural-variant uploads and HiFiCNV CNV files are not header-parsed for
  provenance. For long-read packages the pipeline run record names their tools.
- Paraphase results are JSON, with no header. Their version is recorded only if the package
  declares it.
- Mining free-text `##INFO` descriptions is more fragile than reading a `##VEP=` line, hence
  the allowlist of databases.
- An unknown tool is still captured, under its key as written, so a new caller or annotator
  shows up without a code change.

## Tests

- [test_vcf_header_provenance.py](../backend/tests/test_vcf_header_provenance.py): the
  parsers, with realistic headers for every input, and the merge rules.
- [test_annotation_manifest.py](../backend/tests/test_annotation_manifest.py): the refresh
  rules and the module list.
- [AnnotationProvenanceSummary.test.tsx](../frontend/src/pages/families/__tests__/AnnotationProvenanceSummary.test.tsx):
  the filter-page summary.

See also [TF-08](regulatory/TF-08-soup-register.md) (the SOUP register) and
[TF-09c](regulatory/TF-09c-e2e-pipeline-verification.md) /
[TF-09d](regulatory/TF-09d-browser-e2e-verification.md) (pipeline and browser verification).
