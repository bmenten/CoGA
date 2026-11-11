// The monogenic NIPT readouts the NIPT page and its printed report share: the plasma reads
// behind a call, a panel gene's coverage failure, a variant's fetal-inheritance evidence and
// the fetal-sex words, and how an allele recurs in the assay's other cfDNA samples. Each says what it does not know as such ("—", "no call"), never as a
// value: a missing depth is not 0 reads, a missing probability is not 0%.
import { describe, expect, it } from 'vitest';

import {
  depth,
  lowCoverageChipClass,
  lowCoverageDetail,
  niptChryProfileLabel,
  niptEvidenceSummary,
  niptFetalSexLabel,
  niptGeneTerms,
  pct,
  readsText,
  recurrenceShortText,
  recurrenceText,
  targetExon,
} from '../niptClassification';

describe('the plasma reads behind a call', () => {
  it('writes the alt reads of the depth, and no alt reads as 0', () => {
    expect(readsText({ cf_alt_reads: 30, cf_depth: 500 })).toBe('30 of 500');
    expect(readsText({ cf_alt_reads: null, cf_depth: 500 })).toBe('0 of 500');
  });

  it('says a depth estimated from the target coverage is a no-call there', () => {
    expect(readsText({ cf_alt_reads: 0, cf_depth: 1500, cf_depth_estimated: true })).toBe(
      'no call at ~1500× (target coverage)',
    );
    expect(readsText({ cf_depth: null, cf_depth_estimated: true })).toBe('no call, outside the targets');
  });

  it('writes no depth as unknown, never as 0 reads', () => {
    expect(readsText({ cf_alt_reads: 3, cf_depth: null })).toBe('—');
    expect(readsText({})).toBe('—');
  });
});

describe('why a panel gene failed the coverage check', () => {
  const region = (fields: Partial<Parameters<typeof lowCoverageDetail>[0]>) => ({
    label: 'GENEA',
    chr: '1',
    covered_fraction: 1,
    reason: 'low_depth' as const,
    ...fields,
  });

  it('names no coverage, the share of the target covered, or the median depth', () => {
    expect(lowCoverageDetail(region({ reason: 'no_coverage', covered_fraction: 0 }))).toBe('no coverage');
    expect(lowCoverageDetail(region({ reason: 'partial_coverage', covered_fraction: 0.456 }))).toBe(
      '46% of target covered',
    );
    expect(lowCoverageDetail(region({ reason: 'low_depth', median_coverage: 12.4 }))).toBe('12x median');
    expect(lowCoverageDetail(region({ reason: 'low_depth', median_coverage: null }))).toBe('— median');
  });

  it('marks no coverage as critical and the others as a warning', () => {
    expect(lowCoverageChipClass('no_coverage')).toBe('table-chip table-chip--critical');
    expect(lowCoverageChipClass('partial_coverage')).toBe('table-chip table-chip--warning');
    expect(lowCoverageChipClass('low_depth')).toBe('table-chip table-chip--warning');
  });
});

describe("a variant's fetal-inheritance evidence", () => {
  it('lists the de novo triage and each probability the analysis gave, in that order', () => {
    expect(
      niptEvidenceSummary({
        de_novo: { label: 'high', score: 14 },
        paternal_transmission_probability: 0.5,
        maternal_allele_probability: 0.25,
        fetal_hom_alt_probability: 0.125,
      }),
    ).toBe('de novo: high priority (14) · paternal inherited 50.0% · maternal inherited 25.0% · fetus hom 12.5%');
  });

  it('writes a probability of 0 as one, and leaves out one the analysis did not give', () => {
    expect(niptEvidenceSummary({ maternal_allele_probability: 0, fetal_hom_alt_probability: null })).toBe(
      'maternal inherited 0.0%',
    );
    expect(niptEvidenceSummary({})).toBe('');
  });

  it('names a de novo priority it has no words for as the analysis wrote it', () => {
    expect(niptEvidenceSummary({ de_novo: { label: 'new_tier', score: 3 } })).toBe('de novo: new_tier (3)');
  });
});

describe('the NIPT words and numbers', () => {
  it('writes a share and a depth, and no value as unknown', () => {
    expect(pct(0.0625)).toBe('6.3%');
    expect(pct(0)).toBe('0.0%');
    expect(pct(null)).toBe('—');
    expect(pct(undefined)).toBe('—');
    expect(depth(49.6)).toBe('50x');
    expect(depth(0)).toBe('0x');
    expect(depth(null)).toBe('—');
  });

  it('reads the fetal sex and the chrY profile in words, and an unknown token as it is', () => {
    expect(niptFetalSexLabel('indeterminate')).toBe('Indeterminate');
    expect(niptFetalSexLabel('xxy')).toBe('xxy');
    expect(niptChryProfileLabel('female_no_chrY_signal')).toBe('no chrY signal');
    expect(niptChryProfileLabel('male_like_not_maternal_plasma')).toBe('male DNA');
    expect(niptChryProfileLabel('other_profile')).toBe('other_profile');
    expect(niptChryProfileLabel(null)).toBe('no target table');
    expect(niptChryProfileLabel(undefined)).toBe('no target table');
  });

  it("splits a gene query as the backend does, and reads a target's exon from its attribute", () => {
    expect(niptGeneTerms(' CFTR, SMN1;HBB  DMD ')).toEqual(['CFTR', 'SMN1', 'HBB', 'DMD']);
    expect(niptGeneTerms('')).toEqual([]);
    expect(niptGeneTerms()).toEqual([]);
    expect(targetExon('CFTR;ENST00000003084;x;y; exon 10 ')).toBe('exon 10');
    expect(targetExon('CFTR;ENST00000003084')).toBe('');
  });
});

describe("how an allele recurs in the assay's other cfDNA samples", () => {
  const counts = {
    other_samples: 31,
    carriers: 7,
    maternal: 2,
    paternal_low_vaf: 1,
    unexplained_low_vaf: 3,
    unexplained_median_vaf: 0.0125,
    protection: null,
  };

  it('names the unexplained low-VAF calls of how many, their median, and the explained ones', () => {
    // One carrier has no allele depths: a carrier, in none of the three groups.
    expect(recurrenceText(counts)).toBe(
      "unexplained low-VAF in 3 of 31 other cfDNA samples (median 1.3%) · father's allele in 1 · maternal in 2 · without allele depths in 1",
    );
    expect(recurrenceShortText(counts)).toBe('recurrent 3/31');
  });

  it('says what keeps a protected variant listed whatever its count', () => {
    const kept = { ...counts, carriers: 3, maternal: 0, paternal_low_vaf: 0, protection: 'clinvar' };
    expect(recurrenceText(kept)).toBe(
      'unexplained low-VAF in 3 of 31 other cfDNA samples (median 1.3%) · kept by the recurrence filter: ClinVar may call it pathogenic',
    );
    expect(recurrenceShortText(kept)).toBe('recurrent 3/31 (kept)');
    expect(recurrenceText({ ...kept, protection: 'common' })).toContain('common in the population');
  });

  it('says when no other sample carries it, and when there is none to compare with', () => {
    const none = { ...counts, carriers: 0, maternal: 0, paternal_low_vaf: 0, unexplained_low_vaf: 0 };
    expect(recurrenceText(none)).toBe('in none of 31 other cfDNA samples');
    expect(recurrenceShortText(none)).toBeNull();
    expect(recurrenceText({ ...none, other_samples: 1 })).toBe('in none of 1 other cfDNA sample');
    expect(recurrenceText({ ...none, other_samples: 0 })).toBe('no other cfDNA sample of the assay to compare with');
  });

  it('reads a signed record, which does not hold how many other samples there were', () => {
    expect(recurrenceText({ ...counts, other_samples: null, carriers: 6 })).toBe(
      "unexplained low-VAF in 3 other cfDNA samples (median 1.3%) · father's allele in 1 · maternal in 2",
    );
    expect(
      recurrenceText({ ...counts, other_samples: null, carriers: 0, maternal: 0, paternal_low_vaf: 0, unexplained_low_vaf: 0 }),
    ).toBe('in no other cfDNA sample of the assay');
  });

  it('leaves out the median when the record holds none', () => {
    expect(recurrenceText({ ...counts, unexplained_median_vaf: null, carriers: 6 })).toBe(
      "unexplained low-VAF in 3 of 31 other cfDNA samples · father's allele in 1 · maternal in 2",
    );
  });
});
