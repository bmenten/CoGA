import type { PgtAnalysisOut, PgtEmbryoOut } from '../lib/apiSchema.generated';

/**
 * A PGT analysis as `GET /families/{id}/pgt-analysis` serves it, built from synthetic values.
 * The backend makes the embryo calls (backend/app/services/pgt_embryo_calls.py); the frontend
 * reads them as served.
 */

/** One embryo's call: unaffected, female, nothing to warn about, unless overridden. */
export const pgtEmbryo = (overrides: Partial<PgtEmbryoOut>): PgtEmbryoOut => ({
  sample: 'E1',
  sex: 'female',
  call: 'unaffected_non_carrier',
  roi_not_covered: false,
  sex_dependent: null,
  inherited: [],
  recombinations: [],
  qc: null,
  qc_verdict: null,
  mixed_homologs: [],
  ...overrides,
});

/** A dominant analysis at a chr7 ROI whose disease haplotype is resolved (paternal 1). */
export const pgtAnalysis = (embryos: PgtEmbryoOut[], overrides: Partial<PgtAnalysisOut> = {}): PgtAnalysisOut => ({
  region_of_interest: { query: 'GENE1', label: 'GENE1', source: 'gene', chrom: '7', start: 30_500_000, end: 30_600_000 },
  window: { chrom: '7', start: 29_500_000, end: 31_600_000 },
  inheritance_model: { recorded: 'AD', applied: 'AD', mode: 'dominant' },
  members: [],
  disease_haplotype_resolved: true,
  disease_haplotypes: [{ origin: 'paternal', homolog: '1', kind: 'dominant' }],
  phase_corrections: [],
  embryos,
  inputs: { haplotype_sources: [], blocks_read: 0, blocks_digest: null, markers_truncated: false, blocks_outdated: false },
  rules: {
    window_flank: 1_000_000,
    recombination_flank: 250_000,
    phase_switch_uncertainty: 2_000_000,
    qc_thresholds: {
      min_informative_sites: 50,
      max_mendel_rate: 0.02,
      min_markers_per_flank: 6,
      max_marker_discordance: 0.1,
      min_discordance_markers: 20,
    },
  },
  ...overrides,
});
