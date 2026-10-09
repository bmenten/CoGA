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
| Small variants (SNV/indel), package or direct upload | `variant_upload_service.upload_family_small_variant_file` (a monogenic NIPT pair's files one by one); for a long-read package's one VCF per sample read as one callset, `per_sample_small_variants.upload_family_per_sample_small_variant_files` | the VCF header: caller (`##source`, `##DeepVariant_version`, `##GATKCommandLine`), annotation engine (`##VEP=…` with the gnomAD, ClinVar, dbNSFP, SpliceAI, dbSNP, COSMIC, SIFT, PolyPhen, assembly and GENCODE releases it embeds; `##SnpEffVersion`; `##bcftools_*Version`); and, when the annotation comes as a separate VEP table, that file's `## … version …` lines (`extract_vep_tab_provenance`) |
| Structural variants, from a package (NeedlR, and the `mito` dataset's chrM SV files) or a per-sample upload (Sniffles, Spectre) | `family_package_datasets._import_sv_needlr_dataset`, `_import_mito_structural_variants`, `variant_upload_service.upload_structural_variant_file` | the SV caller (`##source=Sniffles2_…`, Spectre, NeedlR) and `##reference`; for NeedlR and an upload also the database releases named in the `##INFO` descriptions (`extract_info_description_provenance`: GENCODE, OMIM, GenCC, gnomAD, GIAB, ClinVar, dbSNP, COSMIC, each tied to a version-shaped token) |
| Repeat expansions (TRGT), family or per sample | `repeat_expansion_pg.ingest_family_trgt_text`, `ingest_trgt_text` | `##trgtVersion`, `##source=TRGT`, `##reference` |
| The pipeline run record of a long-read or PGT package | `family_package_datasets._import_pipeline_info_dataset` | not a VCF: the Nextflow `software_versions.yaml` (the PGT pipeline's `copgtm_software_mqc_versions.yml`), recorded with `source='manifest'`. It names every tool behind the data, including tools whose outputs carry no version. The run parameters go to `families.metadata["pipeline"]`. |

A version is read only from a line that states one: a `##<tool>Version` or
`##<tool>_version` line, the `Version` field of a structured line (`##GATKCommandLine`,
DRAGEN's `##DRAGENVersion=<…>`), the `##VEP=` line, or a version-shaped token in `##source`
(`Sniffles2_2.2`). A version line gives its first version-shaped word, one that starts with a
digit, or with a `v` and a digit: `1.21+htslib-1.21`, `v1.4.1-0-g68e25e5`, and `5.1d` from
SnpSift's `"SnpSift 5.1d (build …)"`. A value without one counts only as a single word with a
digit that is not the tool's own name, such as the release tag `r0.8`. A command line
(`##bcftools_viewCommand=view …`, `##SnpSiftCmd="SnpSift annotate …"`,
`##trgtCommand=trgt genotype …`) starts with the program or subcommand, so it gives no
version; the tool's version line gives it, whether it comes before or after the command line.

The parser is best-effort and never raises: a header it does not recognise yields less
information, never an error, so capture cannot fail an import.

## How it is stored

One row per family in `family_annotation_manifest`
([04_traceability.sql](../backend/db/schema/postgres/04_traceability.sql)):

| Column | Meaning |
| --- | --- |
| `modules` (JSON) | `{ moduleKey: { version, detail, by_modality } }`, one entry per tool or database |
| `source` | `vcf_header` (from headers), `manifest` (from the package) or `manual` (an admin's replacement through the API) |
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
- **A manual manifest is never overwritten.** Once an admin has replaced the manifest through
  `PUT /families/{family_id}/annotation-manifest` (`source='manual'`), imports leave it alone.
- **An import and a replacement take turns.** Both take the family's manifest lock before they
  read the row, and an import holds it until it commits. A replacement cannot land between an
  import's read and its write, and waits while an import of the same family is running.
- **It commits with the data.** The write runs in a savepoint inside the import's transaction,
  so the provenance is stored exactly when the data it describes is, and a provenance failure
  cannot roll back an import.
- **History lives in the sign-outs and the audit trail.** The table holds the current manifest
  only. Each sign-out freezes a copy into its content-hashed snapshot, and each replacement is
  written to the clinical audit trail with the manifest it replaced
  ([clinical-traceability.md](clinical-traceability.md)).

The manifest names versions; it does not detect change. That is the job of the
`annotationSetHash` that ClickHouse keeps per variant: it changes whenever a variant's
annotation changes, and the drift check compares it with the hash frozen at classification
time. The hash proves that something changed; the manifest says which versions are in use.

## The reference modules

Next to the pipeline versions, the manifest lists what CoGA itself loaded. `_platform_modules`
(in the same service) reads these live each time the manifest is built, so a sign-out freezes
the versions in use at that moment.

| Module | What is read |
| --- | --- |
| `assembly` | the family's assembly, with its release date |
| `gene_loci` | the source of the latest gene import for the assembly (GENCODE, or the UCSC table used when GENCODE could not be fetched), with its date |
| `monarch` | the loaded Monarch release |
| `hpo` | the release of the latest HPO ontology import, with its release date |

Each lookup runs in its own savepoint, so a failure cannot break the sign-out around it. A
failed lookup is listed as version `unavailable` with the detail `lookup failed`, never left
out. A module with nothing loaded is left out.

No table holds the HPO release. An import writes its release onto every term in the file, and
a term that a newer release no longer lists keeps the release it came with. So the loaded
release is the one on the most recently written term (`get_loaded_hpo_release` in
`hpo_service.py`). It is not the highest release string, since an older release can be
imported again, nor the latest one recorded. An ontology imported from a file without a
`data-version` header has no release; the module then reads `unavailable` with the detail
`release not recorded`. The HPO admin page and the cached phenotype ranking
([variant-ranking-cache.md](variant-ranking-cache.md)) read the release the same way.

When the family's pipeline declares a module under the same key (for example `assembly` from a
VCF `##reference` line), the pipeline's value is the one listed.

## Where it is shown

- **Filter pages.** `AnnotationProvenanceSummary.tsx` shows "Annotation versions" at the
  bottom of the small-variant and structural-variant pages, each with its own input's
  versions plus the shared reference modules, and the source (for example "from VCF
  headers").
- **Report.** `FamilyReportPage.tsx` shows the full list ("Modules & versions"), with the
  per-input values where they differ (for example `GENCODE 49 (snv), 45 (sv)`); sign-out
  freezes it.
- **API.** `GET /families/{family_id}/annotation-manifest` returns the merged list.
  `PUT /families/{family_id}/annotation-manifest` lets an admin replace it; the replacement is
  recorded as `source='manual'`, whatever `source` the request sends.

## Limitations

- HiFiCNV CNV files are not header-parsed for provenance, and a manual TSV of structural
  variants has no header. For long-read packages the pipeline run record names their tools.
- Paraphase results are JSON, with no header. Their version is recorded only if the package
  declares it.
- Mining free-text `##INFO` descriptions is more fragile than reading a `##VEP=` line, hence
  the allowlist of databases.
- An unknown tool is still captured, under its key as written, so a new caller or annotator
  shows up without a code change.
- `##fileformat`, `##fileDate` and a bare command line (Sniffles' `##command=…`) are read but
  not stored: the manifest keeps only the modules (`HeaderProvenance.as_modules`).
- A tool that a header names only in a command line, with no version line, has no version in
  that header, and the manifest does not list it from that file. The package's pipeline run
  record, or an admin's replacement, can state it.

## Tests

- [test_vcf_header_provenance.py](../backend/tests/test_vcf_header_provenance.py): the
  parsers, with realistic headers for every input, and the merge rules; a version only from a
  line that states one, never from a command line, wherever it stands.
- [test_annotation_manifest.py](../backend/tests/test_annotation_manifest.py): the refresh
  rules (a re-import whose header names a tool only in a command line keeps its recorded
  version), the module list, the reference modules with their `unavailable` states, the
  replacement's audit event and the lock both writers take.
- [integration/test_hpo_release_provenance.py](../backend/tests/integration/test_hpo_release_provenance.py):
  the HPO release read from a real database, after a re-import of an older release and after an
  import without one.
- [integration/test_annotation_manifest_integration.py](../backend/tests/integration/test_annotation_manifest_integration.py):
  a replacement on the family's hash chain, and an import and a replacement taking turns, on
  real Postgres.
- [test_sv_rewrite_keeps_every_call.py](../backend/tests/test_sv_rewrite_keeps_every_call.py):
  a per-sample SV upload records its caller and the releases its `##INFO` lines cite.
- [AnnotationProvenanceSummary.test.tsx](../frontend/src/pages/families/__tests__/AnnotationProvenanceSummary.test.tsx):
  the filter-page summary.

See also [TF-08](regulatory/TF-08-soup-register.md) (the SOUP register) and
[TF-09c](regulatory/TF-09c-e2e-pipeline-verification.md) /
[TF-09d](regulatory/TF-09d-browser-e2e-verification.md) (pipeline and browser verification).
