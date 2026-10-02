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

- **Genome overview** — served from a precompute. After a family import, a PED upload, a structure save or a member edit,
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

A relative linked by a `relative` edge (related through that member by an unknown degree) is coloured
along the genome when it turns out to be that member's parent or child: when the parent-child share
test above (`match_shared_homolog`) finds it sharing one of the member's haplotypes along at least 90%
of the autosomes it could read, and it could read at least 15 (`RELATIVE_PARENT_OR_CHILD_MIN_FRACTION`,
`RELATIVE_PARENT_OR_CHILD_MIN_CHROMOSOMES`). It is then coloured as a parent or child is
(`_segment_relative_blocks`; linked to both parents, a lane from each, `_merge_lane_claims`), and
nothing is coloured through it. A more distant relative (a sibling, an aunt, a cousin) shares a
haplotype only in stretches, and on low-pass imputed genotypes a stretch cannot be found site by site:
it cannot be told from the long runs unrelated people share by state. (In an example family, a model
that read such stretches site by site called a share at most sites of an unrelated pair, and called
both haplotypes shared at a third of a parent and child's sites; the whole-chromosome test read the
index as the mother's parent or child on 22 of 22 autosomes and the unrelated father on none.)

Such a relative is read at the ROI only, as PGT-M reads a distant reference: from the informative
sites on both sides of the ROI (`relative_share_at_locus`). On each flank, the sites where the member
is heterozygous and the relative homozygous (pins) say which of the member's haplotypes the relative
carries there; in a stretch where it carries both, nearly none are pins. Both flanks must name the
same haplotype, and the member's colour of it must not change across the window (`_colour_across`,
for a member that is itself a relative). The relative's lane that carries that haplotype then takes
its colour across the ROI and both flanks, its other lane is grey, and the rest of the chromosome is
grey (`_locus_share_blocks`):

| Constant | Value | Role |
| --- | --- | --- |
| `RELATIVE_LOCUS_FLANK` | 3 Mb | width of each flank read, and of the colour beyond the ROI |
| `RELATIVE_LOCUS_MIN_PINS` | 100 | pins a flank needs |
| `RELATIVE_LOCUS_MIN_PIN_FRACTION` | 0.15 | pins as a share of the member's heterozygous sites on the flank (a relative carrying both haplotypes has nearly none) |
| `RELATIVE_LOCUS_MIN_CONSISTENCY` | 0.96 | share of the pins that must name the one haplotype |

On the phased genotypes of an example family's embryo pairs (siblings, whose sharing the trios give),
read at every megabase far from their crossovers, this found the shared haplotype at 84% of the loci
where one was shared, never the other one, and none where none was; it read one at 9 of 5,768 loci
where the trios give both shared, and at one locus of an unrelated pair. Both decisions need the
precompute: the parent-or-child one the whole genome (`bed_service._compute_genomewide_lineage`, a
first pass over every autosome, `relative_link_matches` and `parent_or_child_links`), the ROI one the
family's ROI (`FamilyMetadataContext.roi`, in the lineage fingerprint for a family with such a link,
and read again when the ROI changes). A window takes the relative's colours from the current
precompute, grey while there is none.

A parent's phase switch (`haplotype_phase_correction.py`) is read from the couple's children
(`couple_children`: the pedigree's core children) on one chromosome at a time:

| Constant | Value | Role |
| --- | --- | --- |
| `PHASE_SWITCH_MIN_CHILD_MARKERS` | 100 | sites with a known inherited homolog a child needs on that parent's side to count |
| `required_switching_children` | all of 3; all but one of 4 or more | children that must switch together (none read with fewer than 3) |
| `PHASE_SWITCH_CLUSTER_SPAN` | 2 Mb | how far apart their switches may lie (each child's switch is placed where its own run of agreeing sites begins, which noise delays; up to 1.8 Mb in an example family) |
| `PHASE_SWITCH_MAX_CHANCE_CLUSTERS` | 0.05 | clusters of that size and spread expected by chance from the children's switches, each counted at its own rate; at or above, no correction |

The correction sits at the cluster's earliest switch and flips every child's transmissions from there,
the same for all, so the children's sharing (and every embryo call) is unchanged; only where a crossover
shows moves. It runs on the autosomes only. A chromosome an unsorted file splits keeps the corrections
its first part found, so the blocks and the views swap the same sites. As the parent's phase may have
switched up to 2 Mb before the correction (`PHASE_SWITCH_UNCERTAINTY` in
`frontend/src/lib/haplotypePhaseCorrections.ts`), the embryo warning covers that stretch too.

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
- **Phase switches in a parent** — `backend/app/services/haplotype_phase_correction.py`
  (`find_phase_switches`: the densest clusters of the couple's children switching on one parent's
  side, one switch per child; `expected_chance_clusters`: the chance gate; `corrected_genotype_rows`:
  the swap the readers apply). `haplotype_block_builder.py` keeps a chromosome's transmissions
  (`_ChildTransmissions`), reads each child's switches with the blocks' own rule, and replays the
  corrected transmissions into the blocks (`_build_child_blocks`); the upload keeps the corrections in
  `families.metadata.haplotype_phase_corrections` (`record_haplotype_phase_corrections`), and the
  lineage (`bed_service.py`) and the markers (`phased_marker_service.py`) read the parents' phase through
  them, via `FamilyMetadataContext.phase_corrections`. The precompute fingerprint includes them.
- **One copy in males** — `bed_service._mark_hemizygous_blocks` marks each block `hemizygous_in_males`
  with `sex_chromosomes.hemizygous_interval`, the PAR table the variant queries use; `haplotypeRisk.ts`
  reads a male on one lane only on such blocks, so in a PAR he is read on both.

## Known issues

- **Behaviour to know:** when the ROI is not on the chromosome shown (or there is none), the chromosome
  view assesses the risk over the visible window (`defaultHaplotypeRiskRegion`); the genome overview
  and the family page need the ROI.
