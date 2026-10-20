import { describe, expect, it } from 'vitest';
import type { PgtAnalysisOut, PgtEmbryoOut } from '../apiSchema.generated';
import { embryoClassificationsFromAnalysis, segregationStateLabel, sexUnknownWarning } from '../embryoSegregation';

// The backend makes the embryo calls (backend/app/services/pgt_embryo_calls.py; its rules are
// tested in backend/tests/test_pgt_embryo_calls.py). The family page reads them as served.
const embryo = (overrides: Partial<PgtEmbryoOut>): PgtEmbryoOut => ({
  sample: 'E1',
  sex: 'female',
  call: 'unaffected_non_carrier',
  roi_not_covered: false,
  sex_dependent: null,
  inherited: [],
  recombinations: [],
  qc: null,
  ...overrides,
});

const correction = {
  parent: 'FATHER',
  side: 'father' as const,
  chr: '7',
  position: 30_400_000,
  end: 30_450_000,
  children_switching: 3,
  children: 3,
};

const analysis = (embryos: PgtEmbryoOut[], overrides: Partial<PgtAnalysisOut> = {}): PgtAnalysisOut => ({
  region_of_interest: { query: 'GENE1', label: 'GENE1', source: 'gene', chrom: '7', start: 30_500_000, end: 30_600_000 },
  window: { chrom: '7', start: 29_500_000, end: 31_600_000 },
  inheritance_model: { recorded: 'AD', applied: 'AD', mode: 'dominant' },
  members: [],
  disease_haplotype_resolved: true,
  disease_haplotypes: [{ origin: 'paternal', homolog: '1', kind: 'dominant' }],
  phase_corrections: [],
  embryos,
  inputs: { haplotype_sources: [], blocks_read: 0, blocks_digest: null, markers_truncated: false },
  rules: { window_flank: 1_000_000, recombination_flank: 250_000, phase_switch_uncertainty: 2_000_000 },
  ...overrides,
});

describe('embryoClassificationsFromAnalysis', () => {
  it('reads each called embryo with its warnings, as the backend made the call', () => {
    const out = embryoClassificationsFromAnalysis(
      analysis(
        [
          embryo({ sample: 'E_RISK', call: 'affected_or_at_risk', recombinations: [30_550_000] }),
          embryo({ sample: 'E_GAP', call: 'uninformative', roi_not_covered: true }),
          embryo({
            sample: 'E_SEX',
            call: 'affected_or_at_risk',
            sex: 'und',
            sex_dependent: { if_male: 'affected_or_at_risk', if_female: 'carrier' },
          }),
        ],
        { phase_corrections: [correction] },
      ),
    );

    expect([...out.keys()]).toEqual(['E_RISK', 'E_GAP', 'E_SEX']);
    expect(out.get('E_RISK')).toEqual({
      sampleId: 'E_RISK',
      state: 'affected_or_at_risk',
      recombinationNearRoi: true,
      uninformative: false,
      roiNotCovered: false,
      sexDependent: null,
      phaseCorrectionsNearRoi: [correction],
    });
    expect(out.get('E_GAP')).toMatchObject({ state: 'uninformative', uninformative: true, roiNotCovered: true });
    expect(out.get('E_SEX')?.sexDependent).toEqual({ ifMale: 'affected_or_at_risk', ifFemale: 'carrier' });
    // The parents' corrections near the ROI flag every embryo.
    expect(out.get('E_GAP')?.phaseCorrectionsNearRoi).toEqual([correction]);
  });

  it('reads an embryo of an analysis without a disease haplotype as uninformative for that reason', () => {
    const out = embryoClassificationsFromAnalysis(
      analysis([embryo({ call: 'uninformative' })], { disease_haplotype_resolved: false, disease_haplotypes: [] }),
    );
    expect(out.get('E1')).toMatchObject({ uninformative: true, roiNotCovered: false });
  });

  it('gives no badge to an embryo the analysis did not call', () => {
    // No ROI, or no haplotypes read: the backend calls no embryo, and the page shows none.
    expect(embryoClassificationsFromAnalysis(analysis([embryo({ call: null })])).size).toBe(0);
  });
});

describe('the embryo call wording', () => {
  it('names each call as the badge does', () => {
    expect(segregationStateLabel('affected_or_at_risk')).toBe('Affected / at risk');
    expect(segregationStateLabel('carrier')).toBe('Carrier');
    expect(segregationStateLabel('unaffected_non_carrier')).toBe('Unaffected');
    expect(segregationStateLabel('uninformative')).toBe('Uninformative');
  });

  it('gives both calls when the call depends on an unrecorded sex', () => {
    expect(sexUnknownWarning({ ifMale: 'affected_or_at_risk', ifFemale: 'carrier' })).toBe(
      "This embryo's sex is not recorded, and the call depends on it: affected / at risk if male, carrier if female. Record the sex to resolve it.",
    );
  });
});
