# Haplotype segregation analysis — developer notes

The PGT haplotype track colours each family member's two homologs by founder, infers the disease
haplotype and derives an embryo call at the region of interest (ROI). What the lab needs to know — the
colours, the inference rules, the embryo calls, their warnings and the known limitations — is in the
in-app reference
[`frontend/src/content/docs/haplotype-segregation.md`](../frontend/src/content/docs/haplotype-segregation.md)
(shown at `/docs/reference/haplotype-segregation`). That page is the canonical home; keep it in step with
the code. This note covers the implementation.

## How the colours are served

Pedigree IBD matching reads every phased site and re-derives each relative's colour, which is too slow
for the genome overview on every page load. So:

- **Genome overview** — served from a precompute. After a family import, a PED upload or a member edit,
  CoGA runs the genome-wide IBD once in the background and stores it as a `haplotype_lineage` interval
  track (the two lane tags packed into the `origin` column).
- **Chromosome view and ROI** — computed on demand for the visible window only, which is cheap and gives
  untruncated breakpoints where the clinical call is made.

**Staleness is guarded.** The precompute records a fingerprint of the pedigree, the member roles and the
affected set. When any of these change, the stored colours are not served: the overview falls back to
the role-coloured nuclear family with grey relatives until the background refresh has run. An edit can
briefly grey the relatives, but never shows stale colours.

## Algorithm constants

`backend/app/services/haplotype_lineage_service.py`:

| Constant | Value | Role |
| --- | --- | --- |
| `MIN_SHARED_CONSISTENCY` | 0.90 | share of informative sites at which the shared homolog must agree |
| `MIN_INFORMATIVE_SITES` | 10 | informative sites needed to trust a match |
| `MIN_HOMOLOG_MARGIN` | 0.30 | how much the shared homolog must out-score its sibling homolog |
| `MIN_INFORMATIVE_PER_MB` | 0.5 | informative-site density floor over the matched span |
| `LINEAGE_SWITCH_MIN_MARKERS` / `LINEAGE_SWITCH_MIN_SPAN` | 50 / 500 kb | a lane switch (crossover) is committed only after a run this long and this wide |

Elsewhere: the embryo recombination warning uses a 250 kb flank around the ROI
(`ROI_RECOMBINATION_FLANK`, `frontend/src/lib/embryoSegregation.ts`), and the phased-marker fetch is
capped at 500,000 sites (`PHASED_FETCH_LIMIT`, `phased_marker_service.py`), beyond which the marker
overlay is hidden.

## Where this lives

- **Pedigree IBD colouring** — `backend/app/services/haplotype_lineage_service.py` (`annotate_lineage`:
  builds the pedigree, finds the embryo-anchored nuclear core, propagates founder colour by IBD
  matching, splits each relative at recombinations, greys non-autosomes, unplaceable members and
  truncation tails). Pure; the callers in `bed_service.py` fetch the data, including the genome-wide
  `/haplotypes/batch` path.
- **Genome-overview precompute** — `backend/app/services/bed_service.py`
  (`precompute_family_haplotype_lineage`, the hash-guarded read `_fetch_precomputed_lineage` used by
  `_apply_haplotype_lineage_genomewide`, and the best-effort background refresh
  `precompute_family_lineage_safe`). Triggered by `family_package_import.py`, `routers/families.py`
  (member and structure edits) and `routers/ped.py`.
- **Raw phased markers and per-child QC** — `backend/app/services/phased_marker_service.py`
  (`compute_phased_markers`: per-site lane values for the couple's children, trio and single-parent
  modes, the informative-site count and Mendel-error rate, the fetch cap).
- **Disease haplotype and embryo call** — `frontend/src/lib/haplotypeRisk.ts`: `inferDiseaseHaplotypes`;
  `assessSampleHaplotypeRisk`, the call and, when it is uninformative or depends on the sex, why
  (`interpretSampleHaplotypeRisk` returns the call alone); `getHaplotypeLaneSignature`, which treats the
  backend's lineage tags as authoritative over the flat `role`. `frontend/src/lib/embryoSegregation.ts`
  holds the family-page classification and its warnings.

## Known issues

- **Behaviour to know:** when the ROI is not on the chromosome shown (or there is none), the chromosome
  view assesses the risk over the visible window (`defaultHaplotypeRiskRegion`); the genome overview
  and the family page need the ROI.
