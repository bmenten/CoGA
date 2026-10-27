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

- **Genome overview** — served from a precompute. After a family import, a PED upload, a structure save,
  a member edit or a change of the region of interest, CoGA runs the genome-wide IBD once in the
  background and stores it as a `haplotype_lineage` interval track (the two lane tags packed into the
  `origin` column).
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
`backend/app/services/pgt_embryo_calls.py`), the embryo warning covers that stretch too.

Elsewhere: the embryo recombination warning uses a 250 kb flank around the ROI
(`ROI_RECOMBINATION_FLANK`, `backend/app/services/pgt_embryo_calls.py`), the embryo calls read the
haplotype blocks over the ROI and 1 Mb on each side (`ROI_WINDOW_FLANK`), and the phased-marker fetch is
capped at 500,000 sites (`PHASED_FETCH_LIMIT`, `phased_marker_service.py`), beyond which the marker
overlay is hidden.

An embryo call's phasing QC at the ROI is checked against three thresholds, in one place
(`backend/app/services/pgt_embryo_qc.py`). A call that does not meet one gets a warning beside it,
never a gate (the owner's decision of 2026-10-20); the values are **provisional**, to be confirmed by
the laboratory director and QA, and the in-app reference lists them for the lab (*Phasing QC at the
ROI*):

| Constant | Value | Role |
| --- | --- | --- |
| `MIN_INFORMATIVE_SITES` | 50 | sites at the ROI where both parents and the embryo are genotyped (a son on chrX outside the PARs: his mother and he); fewer is a warning (1 / `MAX_MENDEL_RATE`: with fewer, one Mendelian error is above the maximum rate) |
| `MAX_MENDEL_RATE` | 0.02 | a Mendelian error rate at the ROI above it is a warning (the Sample QC's warning rate, and the haplotype track's) |
| `MIN_MARKERS_PER_SIDE` | 6 | sites where the homolog inherited from a parent is read, on each side the call reads (`pgt_embryo_calls.call_sides`); fewer is a warning (ESHRE PGT Consortium 2020: six informative SNPs on each side of the locus for markers-only testing) |

`embryo_qc_verdict` makes the verdict (`pass`, `warning` with each measure outside its threshold, or
`not_counted` when no QC was counted at the ROI); `pgt_analysis` adds it to each called embryo and
serves the thresholds in `rules.qc_thresholds`, and a sign-out freezes both.

## Where this lives

- **Pedigree IBD colouring** — `backend/app/services/haplotype_lineage_service.py` (`annotate_lineage`:
  builds the pedigree, finds the embryo-anchored nuclear core, propagates founder colour by IBD
  matching, splits each relative at recombinations, reads chrX one stretch at a time by the PARs of
  the family's assembly (`_lineage_stretches`; between the PARs a man recorded as male is one copy, a
  man and a member with two copies are read from the runs of her homolog he carries,
  `one_copy_share_runs` and `_one_copy_relative_blocks`, and two men not at all; CLIN-12), and greys
  chrY, chrM, unplaceable members and truncation tails). Pure; the callers in `bed_service.py` fetch
  the data, including the genome-wide `/haplotypes/batch` path, and pass the family's assembly; the
  precompute's fingerprint holds each member's sex.
- **Genome-overview precompute** — `backend/app/services/bed_service.py`
  (`precompute_family_haplotype_lineage`, the hash-guarded read `_fetch_precomputed_lineage` used by
  `_apply_haplotype_lineage_genomewide`, and the best-effort background refresh
  `precompute_family_lineage_safe`). Triggered by `family_package_import.py`, `routers/families.py`
  (member, structure and region-of-interest edits) and `routers/ped.py`.
- **Raw phased markers and per-child QC** — `backend/app/services/phased_marker_service.py`
  (`compute_phased_markers`: per-site lane values for the couple's children, trio and single-parent
  modes, the informative-site count and Mendel-error rate, the fetch cap). On chrX outside the PARs
  of the family's assembly (`is_one_copy_x_site`, from `sex_chromosomes.hemizygous_chromosome`;
  defined with the block builder, which reads chrX by the same rule) a
  member recorded as male is read as one copy: a call of one allele (`1`) or of one allele twice
  (`1|1`) is `(1, 1)`, a call of two different alleles is no genotype he can have. A son is counted
  against his mother alone (his father, or a donor, passes him a Y; with no known mother he is not
  counted), and his call of two different alleles is a Mendelian error; a daughter is counted against
  her father's one X and her mother's two; a father's call of two different alleles is read as no
  call. A member whose sex is not recorded, and every member on an assembly whose PAR bounds the
  table does not hold (T2T-CHM13), is read as two copies. The response serves the window's one-copy
  ranges (`hemizygous_x_ranges`), and the ROI marker review (`FamilyRoiMarkersPage.tsx`) shades its
  Mendelian errors by the same rule (CLIN-60). The review's rows and the haplotype track's marker
  tooltip show a man's call there, and any call of one allele, as that one allele (`rowAlleles` in
  `frontend/src/lib/phasedMarkers.ts`, ENG-19).
- **Disease haplotype and embryo call** — the backend makes the embryo calls:
  `backend/app/services/pgt_embryo_calls.py` (`infer_disease_haplotypes`; `assess_member`, the call
  and, when it is uninformative or depends on the sex, why; `call_embryos`, each embryo's call with
  the homologs it carries at the ROI and the recombinations near it; `lane_signature`, which treats the
  lineage tags as authoritative over the flat `role`; `call_sides`, the parental sides a call reads).
  `pgt_analysis.py` reads what the calls rest on (the ROI, the inheritance model, the members, the
  blocks as `/haplotypes` serves them, the phase corrections) and each embryo's phasing QC at the ROI
  (`phased_marker_service`), checks that QC against the thresholds (`pgt_embryo_qc.py`), and serves
  them at `GET /families/{family_id}/pgt-analysis`. The family page shows those calls
  (`frontend/src/lib/useEmbryoSegregation.ts`; `embryoSegregation.ts` reads and words them), and a
  sign-out freezes the same object (`pgt_analysis` in `signed_record_sections.py`). The haplotype
  tracks still draw the risk line and each member's risk state in the browser,
  `frontend/src/lib/haplotypeRisk.ts` (`inferDiseaseHaplotypes`, `assessSampleHaplotypeRisk`,
  `getHaplotypeLaneSignature`): the same rules, which both sides prove on the parity cases of
  `frontend/src/lib/__tests__/fixtures/pgtEmbryoCallCases.json` (`test_pgt_embryo_calls.py`,
  `haplotypeRisk.test.ts`). A change to the rules is made on both sides and in that file.
- **X-linked recessive, a female embryo** — `assess_member` (and `assessSampleHaplotypeRisk`) reads her
  only on the sides a disease haplotype was found on. With an unaffected father none is found on his side, so his X is
  taken as not at risk and her call rests on her maternal X: *Carrier* with the mother's risk
  haplotype, *Unaffected* with the mother's other X seen across the ROI. This is the owner's confirmed
  rule for a fully penetrant X-linked recessive disorder (2026-10-14; CLIN-6, TF-09a REQ-PGT-005).
  `haplotypeRisk.test.ts` (*still calls a son from his maternal X alone, and a daughter carrier from
  hers*) pins it, and so does the parity case *X-linked recessive from an affected son* on both sides.
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
  reads a male on one lane only on such blocks, so in a PAR he is read on both. The per-child QC and
  the block builder read the same table site by site (`is_one_copy_x_site`).
- **chrX in the blocks** — `haplotype_block_builder.py` reads chrX outside the PARs by the phasing
  QC's rule, with its helpers (`_one_copy_alleles`, `_transmitted_single_parent_haplotype`, moved
  there from `phased_marker_service.py`, which imports them back). A parent recorded as male is one
  allele, written once or twice, a call of two different alleles no call (`_parent_alleles`, which
  keeps his own blocks there whichever way his calls are written). A son's maternal homolog is read
  against his mother alone, where she is heterozygous, and he has no paternal side; a daughter's
  maternal homolog is read with her father's one allele, and her paternal side is his one X
  (`_FATHER_ONE_X`, his `hap1`, set in `_enter_stretch` rather than read, so it does not count
  towards orienting his labels by the affected child). Unrecorded sex is read as two copies. Every
  block lies in one stretch of chrX (`sex_chromosomes.x_stretches`, which the lineage reads chrX by
  too: before PAR1, PAR1, between the PARs, PAR2, after PAR2): at a PAR edge a member's open block ends with its stretch (`end_limit`, also at the
  chromosome's end) and a child's segregation states start afresh, so no homolog read in a PAR runs
  over the X and `hemizygous_in_males` marks exactly the blocks between the PARs. A block reaches past
  its first site, to the next block's first site or to the end of its chromosome or stretch; one opened
  at a stretch's last position could not without leaving its stretch, and would end where it began, so
  none is kept there (`_append_haplotype_state_row`, ENG-19): the member's block before it in the
  stretch, where there is one, already reaches that site. No stretch is cut
  on an assembly without PAR bounds (T2T-CHM13). Before this (CLIN-60's follow-up), with men's calls
  written as one allele no child had a transmission outside the PARs, and with PAR1 in the callset
  the PAR1 blocks ran over the X, so an embryo at an X-linked ROI read *Uninformative* or was called
  from its PAR1 homologs. The blocks are built at import with the sexes recorded then: a sex
  recorded or corrected later, and a family imported before this change, need the phased genotypes
  imported again (a package import also refreshes the genome-overview precompute).
- **Outdated blocks** (CLIN-66): the loader keeps, beside each member's blocks, what they were built
  for besides the genotypes: the index couple, which of the file's samples are the couple's children
  and each sample's recorded sex (`haplotype_block_builder.haplotype_block_basis`, kept as
  `pedigree_basis` in the haplotype track's `sample_interval_track_sources.metadata`). The PGT analysis
  compares it with the family's pedigree (`pgt_analysis.blocks_outdated`): blocks built for another
  couple, other children or other sexes, or without the record (loaded before 2026-10-27), are
  outdated. The analysis then still reads them for the inputs' digest, but calls every embryo
  *Uninformative* and says why (`inputs.blocks_outdated`), as the family page and the signed record
  do, until the phased genotypes are imported again (the owner's choice of 2026-10-27). The
  haplotypes endpoints answer the same flag (`HaplotypeResponse.blocks_outdated`, from
  `bed_service.haplotype_source_rows`), and the genome overview's and chromosome view's tracks
  (`GenomeHaplotypeTrack`, `HaplotypePhasedTrack`) then infer no disease haplotype, draw no risk
  haplotype, read each member's risk state as uninformative and say why. The affected
  statuses are not part of it: the calls read them when they are made, and at import they only orient
  the parents' labels. A member added who is not in the phased file changes nothing either.
- **The genome overview's lineage precompute** is fingerprinted with a digest of the stored haplotype
  blocks too (`clickhouse_interval_tracks.interval_track_rows_digest`, in `bed_service._lineage_hash`):
  a single-file re-upload rewrites the blocks, and the precompute made before it is no longer served.
  Before (CLIN-66) the fingerprint left the blocks out, and the overview showed the old ones until a
  pedigree, member or ROI edit refreshed it.

## Known issues

- **Behaviour to know:** when the ROI is not on the chromosome shown (or there is none), the chromosome
  view assesses the risk over the visible window (`defaultHaplotypeRiskRegion`); the genome overview
  and the family page need the ROI.
