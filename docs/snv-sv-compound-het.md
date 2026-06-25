# SNV + SV compound heterozygosity — implementation plan

**Status:** Phases 0–2 implemented. The badge, the `require_sv_second_hit` filter, the
trans/cis verdict, and read-based phasing (when SVs carry a phase set) are all live;
trio/segregation is always applied as the fallback.
**Goal:** surface genes hit by **both** a small variant (SNV/indel) **and** a structural
variant (deletion, duplication, …) in the same individual — the cross-type compound-het /
"second hit" mechanism that single-type pipelines miss — and tell the analyst whether the
two hits are **in trans** (biallelic, candidate causal) or **in cis** (same allele).

This document maps what exists, the data reality (SVs are not phased today), and a phased
plan grounded in the current code.

---

## 1. Clinical motivation

Autosomal-recessive disease is frequently caused by **one point mutation on one allele and
a structural variant on the other**. The highest-yield pattern is a **heterozygous SNV +
an overlapping deletion**: the deletion removes the second copy, so a variant that *looks*
heterozygous is functionally **biallelic / hemizygous** and pathogenic. Because CoGA filters
SNVs and SVs in separate workspaces, these pairs are easy to miss.

The "done" criteria:

1. While filtering SNVs, the analyst is **told** when a candidate's gene is also hit by an
   SV, with the SV's type and zygosity.
2. The analyst can **search for** the pattern directly (restrict to genes with a second-hit
   SV), not just stumble on it.
3. Each candidate pair gets a **trans / cis / unknown** verdict so true compound hets are
   distinguishable from same-allele coincidences.
4. The deletion-unmasking case is recognised explicitly.

---

## 2. Current state (what exists)

| Capability | Status | Where |
| --- | --- | --- |
| SNV per-variant gene annotations | ✅ | `GRCh38/SNV_INDEL/entries.gene_symbols` (Array), `is_annotated_in_any_gene` |
| SV per-variant gene annotations | ✅ | `GRCh38/SV/entries.gene_symbols` (Array) + per-sample `calls.gt` |
| SNV compound-het pairing (SNV+SNV) | ✅ | `_compound_het_pairs` / `_records_form_compound_het_pair` (`clickhouse_family_variants.py`): both het in **all affected**, and **not both** in any unaffected → trans by segregation |
| Inheritance / de-novo (parent genotypes) | ✅ | `_record_matches_de_novo`, `_segregation_modes_by_variant`; pedigree via `FamilyMetadataContext` (`affected_sample_names`, `relationship_rows`, `sample_rows`) |
| **SNV phasing** | ✅ | `SNV_INDEL/entries.calls.ps` (phase set, per sample) |
| **SV phasing** | ✅ (Phase 2) | `SV/entries.calls.ps` now captured from the VCF `PS`; phased `GT` (`a\|b`) preserved. `NULL` for unphased calls (no phased SVs in current data) |
| Per-family precompute + invalidation pattern | ✅ (reuse) | `variant_ranking_cache.py` + `family_variant_ranking_cache` (inputs-hash cache, cleared on re-import in `family_package_import.py`) |
| SNV results UI (badges, filter form) | ✅ | `SmallVariantResults.tsx`, `SmallVariantFilterForm.tsx`, `smallVariantSearch.ts` |

**Key consequence of the SV-phasing gap:** read-based cis/trans between an SNV and an SV is
**not possible on current data** — the SV calls carry no haplotype/phase set. Trans must be
inferred from **segregation** (the existing compound-het approach) until SV phasing is added
upstream (see §7).

---

## 3. Approach

Four building blocks, layered so each is independently shippable.

### (A) SV→gene index per family  *(the foundation — your "precompute" idea)*

A precomputed map, per family, of **which genes are hit by an SV** and the SV details needed
to reason about a second hit:

```text
gene_symbol → [ { sv_id, sv_type, chr, start, end, length,
                  per_affected_gt: {sample: gt}, hits_count } ]
```

Built from `SV/entries` (`gene_symbols`, `calls`) for the family; small (~10k gene-SV links
even for the largest family). Stored append-style, keyed by an inputs hash so it is reused
until the SVs change.

### (B) SNV-side flag + "second hit" filter  *(your badge, made findable)*

- **Flag:** when an SNV result's gene is in the SV index, attach a compact descriptor
  (`sv_second_hit: { type, zygosity, count }`) the table/cards render as a badge —
  *"⚠ also hit by an SV (DEL · het)"*, expandable to the SV detail.
- **Filter:** a new SNV filter `require_sv_second_hit` ("restrict to genes also hit by an
  SV"), so the analyst pulls the candidate list in one query. (And the symmetric flag on the
  SV page: "also hit by an SNV".)

### (C) Cross-type compound-het call  *(trans by segregation)*

Generalise the existing SNV+SNV pairing to **SNV + SV** using the same, proven rule — no new
phasing data required:

- both hits present (het) in **all affected** individuals, and
- **not both** present in any unaffected individual ⇒ **trans** (compound-het candidate).

Special-case the **deletion**: a het SNV + an overlapping **DEL** in trans is flagged
"deletion unmasks SNV → effectively biallelic" — the headline pattern. A **hom** SV or a CNV
that itself removes/duplicates both copies is annotated accordingly.

### (D) Phase verdict per pair

Each candidate pair carries a `phase` verdict with its evidence tier:

- **trio / segregation** (available now): SNV and SV transmitted from different parents ⇒
  trans; same parent ⇒ cis; ambiguous ⇒ unknown. Reuses the pedigree + the not-both-in-
  unaffected logic.
- **read-based** (long-read; *blocked on §7*): compare the SNV phase set/haplotype with the
  SV phase set once SVs are phased upstream.
- **unknown**: singleton family or no informative parent and no read phase.

---

## 4. Data model

`family_sv_gene_index` (Postgres), one row per (family, gene):

```sql
CREATE TABLE family_sv_gene_index (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    family_id    UUID NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    gene_symbol  TEXT NOT NULL,                 -- upper-cased
    sv_count     INTEGER NOT NULL,
    svs          JSONB NOT NULL,                -- [{sv_id, sv_type, chr, start, end, length, gt:{sample:gt}}]
    inputs_hash  TEXT NOT NULL,                 -- SV-data + sample-set digest (freshness)
    computed_at  TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    UNIQUE (family_id, gene_symbol)
);
CREATE INDEX ON family_sv_gene_index (family_id, upper(gene_symbol));
```

Lookup during SNV filtering is then a single `gene_symbol IN (…)` query over the page's
genes. Invalidated (rebuilt) when the family's SVs are re-imported — hook into the same
post-import point that clears the ranking cache (`family_package_import.py`).

> Alternative considered: compute the SV-gene set on the fly per SNV request (no table). The
> SV scan for a whole family is the slow part (§ the 33 s panel issue), so precomputing once
> is the right call — and it mirrors the ranking-cache pattern we already run.

## 5. Backend

- **`sv_gene_index_service.py`** — `rebuild_family_sv_gene_index(session, family_id)`
  (scan `SV/entries` for the family, group by gene, store), `get_sv_second_hits(session,
  family_uuid, gene_symbols)` (page-time lookup), `clear_*` (re-import). Mirrors
  `variant_ranking_cache.py`.
- **SNV page integration** (`clickhouse_family_variants.py` `get_family_small_variants_page`):
  after building the page's `VariantOut`s, look up `sv_second_hits` for the page genes and
  attach `variant.sv_second_hit`. When `filters.require_sv_second_hit` is set, restrict to
  genes present in the index (add the gene set as an include-filter, like a dynamic panel).
- **Cross-type pairs** (`compound_het` analysis): a `_snv_sv_compound_het_pairs(snv_records,
  sv_records, affected, unaffected)` mirroring `_records_form_compound_het_pair`, returning
  pairs with `phase` (trans/cis/unknown) and a `deletion_unmasked` flag.
- **Schema** (`schemas.py`): `SvSecondHitOut { sv_type, zygosity, count, svs[] }` on
  `VariantOut`; `require_sv_second_hit: bool` on the SNV filter; a `SnvSvPairOut` for the
  pair view.

## 6. Frontend

- **Badge** in `SmallVariantTable` / `SmallVariantCards`: render `variant.sv_second_hit`
  (icon + type + zygosity), with a popover listing the SV(s) and a link to the SV review.
- **Filter toggle** in `SmallVariantFilterForm`: *"Also hit by an SV (second hit)"* →
  `require_sv_second_hit`; serialised in `smallVariantSearch.ts`.
- **(Phase 2) Cross-type pair view**: a panel listing gene → {SNV, SV} candidate pairs with
  the trans/cis verdict and the deletion-unmasked highlight — analogous to the existing
  compound-het pair cards.
- Symmetric **"also hit by an SNV"** flag on the SV page (cheap once the index exists; build
  the SNV-gene side of the index too, or reuse the SNV gene set).

## 7. Read-based cis/trans (implemented)

Long-read cis/trans needs the **SV calls to be phased**. The ingestion now captures this:

1. **Upstream:** phase the SV VCF (e.g. Sniffles2 + HiPhase / LongPhase), so SV records carry
   a phase set (`PS`) per sample.
2. **Ingestion:** the SV loader reads `PS` (`structural_variant_ingest.parse_phase_set`) and
   stores it in a `calls.ps Array(Nullable(UInt64))` column on `SV/entries` (mirroring SNV);
   the phased `GT` (`a|b`) is preserved verbatim. Unphased calls store `NULL` — backward
   compatible.
3. **Backend:** `_read_phase_verdict` compares, per affected sample, the haplotype each alt
   sits on when the SNV and an SV **share a phase set**; a demonstrated cis in any affected
   sample rules out the biallelic mechanism. `sv_second_hit.phase_evidence` is `"read"` when
   this fires, else `"segregation"`.

**Segregation is always applied as the fallback** (trio/affected-unaffected), so non-phased
and short-read cases still get a verdict; read phasing simply upgrades the evidence when a
shared phase set is present. The badge marks read-based phase distinctly (accent chip).

## 8. Phased delivery

### Phase 0 — SV→gene index + SNV flag ✅ *(shipped)*

`family_sv_gene_index` + `sv_gene_index_service` + re-import invalidation; attach
`sv_second_hit` to SNV results; badge in the table/cards.

### Phase 1 — "second hit" filter + trans/cis verdict ✅ *(shipped)*

`require_sv_second_hit` filter (intersects the family's SV-hit genes with the panel/gene
constraints, pagination-safe); the `phase` verdict (trans/cis/unknown by segregation) and the
`deletion_unmasked` biallelic flag on `sv_second_hit`; the badge shows the phase and
highlights the unmasked case. The match runs over every gene a variant overlaps (not just the
primary annotation), so the filter and the badge agree.

### Phase 2 — read-based phasing ✅ *(shipped)*

SV phase set captured in ingestion (`calls.ps`) → haplotype-based cis/trans when the SNV and
SV share a phase set; `phase_evidence` (`read` / `segregation`) surfaced on the verdict and
the badge. Trio/segregation is always applied as the fallback, so the common case is covered
whether or not the SVs are phased.

## 9. Decisions & open questions

- **Index direction.** Build SV→gene now (drives the SNV-side flag/filter, the priority
  workflow). Add SNV→gene if/when the symmetric SV-side flag is wanted.
- **Gene vs coding.** Match on `gene_symbols` (overlap). Optionally restrict the SV side to
  events with a coding consequence on the gene; default to any overlap (a whole-gene deletion
  has no per-base consequence but is the most important case).
- **CNV dosage.** A DUP and a DEL differ clinically (dosage vs loss); carry `sv_type` through
  so the UI/zygosity logic can treat a DEL (loss → unmasking) differently from a DUP.
- **Phasing source (needs your input).** Can the ingestion pipeline emit phased SVs (Phase 2),
  or should we ship trio/segregation-only and treat read-phasing as future work?

## 10. File touch-points

- Schema: `db/schema/postgres/0XX_family_sv_gene_index.sql`
- New service: `app/services/sv_gene_index_service.py`
- SNV integration + cross-type pairs: `app/services/clickhouse_family_variants.py`
  (`get_family_small_variants_page`, new `_snv_sv_compound_het_pairs`)
- Filters/schemas: `app/services/family_variant_filters.py`, `app/schemas.py`
- Re-import invalidation: `app/services/family_package_import.py`
- Frontend: `SmallVariantResults.tsx`, `SmallVariantTable.tsx`, `SmallVariantCards.tsx`,
  `SmallVariantFilterForm.tsx`, `smallVariantSearch.ts`
