import { describe, expect, it } from 'vitest';
import type { PgtAnalysisOut, PgtEmbryoOut, PgtEmbryoQcVerdictOut } from '../apiSchema.generated';
import {
  describeQcFinding,
  describeQcSides,
  embryoClassificationsFromAnalysis,
  formatQcRate,
  mixedHomologsWarning,
  qcVerdictWarning,
  segregationStateLabel,
  sexUnknownWarning,
  BLOCKS_OUTDATED_WARNING,
} from '../embryoSegregation';

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
  qc_verdict: null,
  mixed_homologs: [],
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
      qcVerdict: null,
      qcNotCountedReason: 'the embryo is not recorded as a child of the couple',
      blocksOutdated: false,
      mixedHomologs: [],
      maxMarkerDiscordance: 0.1,
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

  it('reads every embryo as uninformative for outdated blocks, with the reason to give (CLIN-66)', () => {
    const out = embryoClassificationsFromAnalysis(
      analysis([embryo({ call: 'uninformative' }), embryo({ sample: 'E2', call: 'uninformative' })], {
        inputs: { haplotype_sources: [], blocks_read: 4, blocks_digest: 'abc', markers_truncated: false, blocks_outdated: true },
      }),
    );
    expect([...out.values()].map((call) => [call.sampleId, call.uninformative, call.blocksOutdated])).toEqual([
      ['E1', true, true],
      ['E2', true, true],
    ]);
    expect(BLOCKS_OUTDATED_WARNING).toBe(
      "The haplotype blocks were built for another pedigree or other sexes than the family's, so no embryo is called from them: import the phased genotypes again.",
    );
    // Current blocks: no such reason.
    expect(embryoClassificationsFromAnalysis(analysis([embryo({ call: 'carrier' })])).get('E1')?.blocksOutdated).toBe(false);
  });

  it('gives no badge to an embryo the analysis did not call', () => {
    // No ROI, or no haplotypes read: the backend calls no embryo, and the page shows none.
    expect(embryoClassificationsFromAnalysis(analysis([embryo({ call: null })])).size).toBe(0);
  });

  it("reads each embryo's phasing-QC verdict as served, and why a QC was not counted", () => {
    const warning: PgtEmbryoQcVerdictOut = {
      status: 'warning',
      sides: ['paternal'],
      findings: [{ measure: 'paternal_markers', value: 3, threshold: 6, flank: 'left' }],
    };
    const out = embryoClassificationsFromAnalysis(
      analysis([embryo({ sample: 'E_LOW', call: 'affected_or_at_risk', qc_verdict: warning })], {
        inputs: { haplotype_sources: [], blocks_read: 0, blocks_digest: null, markers_truncated: true, blocks_outdated: false },
      }),
    );
    // The call itself is as served: the verdict sits beside it.
    expect(out.get('E_LOW')).toMatchObject({
      state: 'affected_or_at_risk',
      qcVerdict: warning,
      qcNotCountedReason: 'the ROI and its flanks hold more sites than the marker review reads',
    });
  });
});

// The phasing QC at the ROI against its thresholds (backend/app/services/pgt_embryo_qc.py): a
// warning beside the call, never a change to it (the owner's decision of 2026-10-20).
describe('the phasing-QC warning', () => {
  const verdict = (overrides: Partial<PgtEmbryoQcVerdictOut>): PgtEmbryoQcVerdictOut => ({
    status: 'warning',
    sides: ['paternal'],
    findings: [],
    ...overrides,
  });

  it('names each measure that does not meet its threshold, with its value and the threshold', () => {
    expect(describeQcFinding({ measure: 'informative_sites', value: 6, threshold: 50, flank: null })).toBe(
      '6 informative sites (fewer than 50)',
    );
    expect(describeQcFinding({ measure: 'informative_sites', value: 1, threshold: 50, flank: null })).toBe(
      '1 informative site (fewer than 50)',
    );
    expect(describeQcFinding({ measure: 'mendel_rate', value: 1 / 6, threshold: 0.02, flank: null })).toBe(
      'Mendelian error rate 16.7% (above 2%)',
    );
    // A parent's markers are counted on each flank of the ROI, and the finding says which.
    expect(describeQcFinding({ measure: 'paternal_markers', value: 3, threshold: 6, flank: 'left' })).toBe(
      "3 markers on the father's side left of the ROI (fewer than 6)",
    );
    expect(describeQcFinding({ measure: 'maternal_markers', value: 1, threshold: 6, flank: 'right' })).toBe(
      "1 marker on the mother's side right of the ROI (fewer than 6)",
    );
    // A record from before the flanks were counted apart names none.
    expect(describeQcFinding({ measure: 'paternal_markers', value: 3, threshold: 6, flank: null })).toBe(
      "3 markers on the father's side (fewer than 6)",
    );
  });

  it('never shows a rate just above the maximum as the maximum itself', () => {
    // 2 errors in 99 sites is 2.02%: read to one decimal it would say "2.0% (above 2%)".
    expect(describeQcFinding({ measure: 'mendel_rate', value: 2 / 99, threshold: 0.02, flank: null })).toBe(
      'Mendelian error rate 2.02% (above 2%)',
    );
    expect(describeQcFinding({ measure: 'mendel_rate', value: 0.020004, threshold: 0.02, flank: null })).toBe(
      'Mendelian error rate 2.0004% (above 2%)',
    );
    expect(formatQcRate(0.0125)).toBe('1.25%');
  });

  it('says which parents’ markers were checked', () => {
    expect(describeQcSides(['paternal'])).toBe("markers checked on the father's side");
    expect(describeQcSides(['maternal'])).toBe("markers checked on the mother's side");
    expect(describeQcSides(['paternal', 'maternal'])).toBe("markers checked on both parents' sides");
    expect(describeQcSides([])).toBe("markers not checked: the call reads neither parent's side");
  });

  it('warns beside the call when the QC does not meet its thresholds, and says the call stands', () => {
    expect(
      qcVerdictWarning(
        verdict({
          findings: [
            { measure: 'informative_sites', value: 6, threshold: 50, flank: null },
            { measure: 'paternal_markers', value: 3, threshold: 6, flank: 'right' },
          ],
        }),
        'the embryo is not recorded as a child of the couple',
      ),
    ).toBe(
      "The phasing QC at the ROI does not meet its thresholds: 6 informative sites (fewer than 50); 3 markers on the father's side right of the ROI (fewer than 6). " +
        'The call stands: review the ROI markers before relying on it.',
    );
  });

  it('warns that a call was not checked when its QC was not counted, which is no pass', () => {
    expect(qcVerdictWarning(verdict({ status: 'not_counted' }), 'the embryo is not recorded as a child of the couple')).toBe(
      'The phasing QC at the ROI was not counted (the embryo is not recorded as a child of the couple), so this call ' +
        'was not checked against the QC thresholds. Review the ROI markers.',
    );
  });

  it('says nothing for a QC that meets its thresholds, or without a verdict', () => {
    expect(qcVerdictWarning(verdict({ status: 'pass' }), 'any')).toBeNull();
    expect(qcVerdictWarning(null, 'any')).toBeNull();
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

describe('markers mixing both homologs of a parent', () => {
  const qc = {
    informative_sites: 200,
    mendel_errors: 0,
    mendel_rate: 0,
    paternal_markers: 120,
    maternal_markers: 80,
    paternal_markers_left: 60,
    paternal_markers_right: 60,
    maternal_markers_left: 40,
    maternal_markers_right: 40,
    paternal_discordance: 0.01,
    maternal_discordance: 0.475,
  };

  it('reads the mixed sides with their discordance and the threshold the analysis states', () => {
    const out = embryoClassificationsFromAnalysis(
      analysis([embryo({ sample: 'E_TRI', call: 'uninformative', qc, mixed_homologs: ['maternal'] })]),
    );
    const tri = out.get('E_TRI')!;
    expect(tri.uninformative).toBe(true);
    expect(tri.mixedHomologs).toEqual([{ side: 'maternal', discordance: 0.475, markers: 80 }]);
    expect(tri.maxMarkerDiscordance).toBe(0.1);
    expect(out.get('E_TRI')).toBeDefined();
    expect(embryoClassificationsFromAnalysis(analysis([embryo({ sample: 'E1' })])).get('E1')!.mixedHomologs).toEqual([]);
  });

  it('says whose homologs mix, how far, and what to read instead', () => {
    expect(mixedHomologsWarning([{ side: 'maternal', discordance: 0.475, markers: 80 }], 0.1)).toBe(
      "This embryo's markers at the ROI mix both of the mother's homologs (47.5% of 80 markers off one homolog, or two either side of one crossover; above 10%): it may carry both, as in a trisomy of this chromosome, which its haplotype blocks cannot show, so no call is made. Read its copy-number and APCAD tracks.",
    );
    expect(
      mixedHomologsWarning(
        [
          { side: 'paternal', discordance: 0.2, markers: 30 },
          { side: 'maternal', discordance: null, markers: null },
        ],
        null,
      ),
    ).toBe(
      "This embryo's markers at the ROI mix both of the father's and the mother's homologs (the father's 20% of 30 markers off one homolog, or two either side of one crossover): it may carry both, as in a trisomy of this chromosome, which its haplotype blocks cannot show, so no call is made. Read its copy-number and APCAD tracks.",
    );
  });
});
