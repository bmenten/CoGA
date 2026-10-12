# SNV + SV compound heterozygosity — developer notes

The small-variant page flags genes hit by both a small variant and a structural variant (the
cross-type "second hit"), offers an "Also hit by an SV" filter, and gives each hit a trans / cis
verdict. The behaviour lab users see — the badge, the filter, the phase rules and their limits — is in
the in-app reference [`frontend/src/content/docs/sv-second-hit.md`](../frontend/src/content/docs/sv-second-hit.md)
(shown at `/docs/reference/sv-second-hit`). That page is the canonical home; this note covers the
implementation.

- **Index.** `family_sv_gene_index` (one row per family and gene: `sv_count` and the SVs as JSONB) and
  `family_sv_gene_index_status` (the SV and gene counts and `sv_data_version`, the SV data version the
  index was built from), in baseline `backend/db/schema/postgres/03_assay.sql`.
  `_ensure_family_sv_gene_index` (`clickhouse_family_variants.py`) reads the family's current SV data
  version, and scans the family's SVs to (re)build the index when the stored version differs;
  `store_sv_gene_index` replaces it under a per-family advisory lock.
- **Invalidation.** Every SV insert and delete in `clickhouse_variant_storage.py`, and the snapshot
  restore in `clickhouse_family_snapshot.py`, appends a token to ClickHouse `SV/family_data_version`
  (`bump_family_structural_variant_data_version`), so any write path moves the version: package import,
  per-sample upload, admin delete, or a structure edit that clears the family's genomic data. A write that
  bypasses those helpers must bump the version itself. The package import also clears the index (`clear_family_sv_gene_index`).
- **Page time.** `_attach_sv_second_hits` looks up the page's genes (`get_sv_second_hits`) and attaches
  `sv_second_hit`, summarised by `summarize_second_hit` (`sv_gene_index_service.py`). The
  `require_sv_second_hit` filter restricts the query to `get_sv_hit_genes`.
- **Phase.** `_read_phase_verdict` compares the SNV's and the SV's haplotype within a shared phase set
  in an affected sample. It reads each call with `phased_alt_haplotype` (`compound_het_phase.py`), as
  `_pair_phase_for_sample` does for an SNV + SNV pair: a call places its alt only when it is phased, both
  alleles are called and exactly one is an alt, so a half call (`.|1`, `1|.`) places nothing. Otherwise
  `_phase_verdict` applies `segregation_phase` (`compound_het_phase.py`) to the SNV genotypes and
  `_sv_carriage`, with the pedigree from `_family_pedigree` (the parent links the de novo check
  uses). `deletion_unmasked` marks a deletion in trans. SNV + SNV pairs go through the same
  `segregation_phase` after read phasing, in `_compound_het_pair_verdict`
  (`clickhouse_variant_queries.py`), which also sets `phase_evidence` (`read` or `segregation`); a pair
  traced to one parent is dropped. The rule itself is described for lab users in the in-app reference.
