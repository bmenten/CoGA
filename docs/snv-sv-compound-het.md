# SNV + SV compound heterozygosity — developer notes

The small-variant page flags genes hit by both a small variant and a structural variant (the
cross-type "second hit"), offers an "Also hit by an SV" filter, and gives each hit a trans / cis
verdict. The behaviour lab users see — the badge, the filter, the phase rules and their limits — is in
the in-app reference [`frontend/src/content/docs/sv-second-hit.md`](../frontend/src/content/docs/sv-second-hit.md)
(shown at `/docs/reference/sv-second-hit`). That page is the canonical home; this note covers the
implementation.

- **Index.** `family_sv_gene_index` (one row per family and gene: `sv_count` and the SVs as JSONB) and
  `family_sv_gene_index_status` (marks the index as built), in baseline
  `backend/db/schema/postgres/03_assay.sql`. `_ensure_family_sv_gene_index`
  (`clickhouse_family_variants.py`) scans the family's SVs and builds the index the first time the
  small-variant page is opened; `store_sv_gene_index` replaces it.
- **Page time.** `_attach_sv_second_hits` looks up the page's genes (`get_sv_second_hits`) and attaches
  `sv_second_hit`, summarised by `summarize_second_hit` (`sv_gene_index_service.py`). The
  `require_sv_second_hit` filter restricts the query to `get_sv_hit_genes`.
- **Phase.** `_read_phase_verdict` compares the SNV's and the SV's haplotype within a shared phase set
  in an affected sample; otherwise `_phase_verdict` decides by segregation. `deletion_unmasked` marks a
  deletion in trans. SNV + SNV pairs use `_compound_het_pair_phase` (`clickhouse_variant_queries.py`),
  which reports trans only from read phasing.
- **Invalidation.** `clear_family_sv_gene_index` runs after a package import
  (`family_package_import.py`), next to the ranking-cache clear.

## Known defects

- `_phase_verdict` returns `trans` whenever the family has at least one unaffected member and none
  carries both hits — including relatives who carry neither, which says nothing about phase.
- A per-sample SV upload (`POST /structural-variants/upload/{sample_id}`) does not clear the index, so
  the badge and the filter miss the new SVs until the next package import.

Both are described for lab users in the in-app reference.
