import { describe, expect, it } from 'vitest';

import type { FamilyMember, SmallVariant } from '../smallVariantSearch';
import {
  buildSegregationSentence,
  buildVariantSentence,
  collectReportCriteria,
  describeGnomadFrequency,
  describeZygosity,
  humanizeEffect,
} from '../reportNarrative';

const makeVariant = (overrides: Partial<SmallVariant> = {}): SmallVariant => ({
  _id: 'v1',
  chr: 'chr17',
  start: 43000000,
  end: 43000000,
  type: 'snv',
  ref: 'A',
  alt: 'G',
  gene: 'BRCA1',
  effect: 'missense_variant',
  hgvsc: 'c.123A>G',
  hgvsp: 'p.Lys41Arg',
  genotypes: [{ sample: 'S1', gt: '0/1' }],
  ...overrides,
});

const member = (overrides: Partial<FamilyMember> = {}): FamilyMember => ({
  sample_id: 'S1',
  role: 'proband',
  affected: true,
  sex: 'female',
  ...overrides,
});

describe('describeZygosity', () => {
  it('classifies genotypes', () => {
    expect(describeZygosity('0/1')).toBe('heterozygous');
    expect(describeZygosity('1/1')).toBe('homozygous');
    expect(describeZygosity('1|0')).toBe('heterozygous');
    expect(describeZygosity('1')).toBe('hemizygous');
    expect(describeZygosity('0/0')).toBe('reference');
    expect(describeZygosity('./.')).toBe('unknown');
    expect(describeZygosity(undefined)).toBe('unknown');
  });
});

describe('humanizeEffect', () => {
  it('splits and de-snakes compound consequences', () => {
    expect(humanizeEffect('missense_variant&splice_region_variant')).toBe(
      'missense variant / splice region variant',
    );
    expect(humanizeEffect(undefined)).toBe('sequence variant');
  });
});

describe('describeGnomadFrequency', () => {
  it('handles absent, missing and present frequencies', () => {
    expect(describeGnomadFrequency(makeVariant({ gnomad_af: 0 }))).toContain('absent from gnomAD');
    expect(describeGnomadFrequency(makeVariant({ gnomad_af: undefined }))).toContain(
      'no reported gnomAD',
    );
    expect(describeGnomadFrequency(makeVariant({ gnomad_af: 0.2 }))).toContain('allele frequency');
  });

  it('mentions homozygotes when present', () => {
    const text = describeGnomadFrequency(makeVariant({ gnomad_af: 0.00005, gnomad_hom_count: 2 }));
    expect(text).toContain('extremely rare');
    expect(text).toContain('2 homozygotes');
  });
});

describe('buildVariantSentence', () => {
  it('describes the variant with zygosity, gene, HGVS and locus', () => {
    const sentence = buildVariantSentence(makeVariant(), [member()]);
    expect(sentence).toContain('heterozygous');
    expect(sentence).toContain('BRCA1 c.123A>G (p.Lys41Arg)');
    expect(sentence).toContain('chr17:43,000,000');
    expect(sentence).toContain('proband (S1)');
  });
});

describe('buildSegregationSentence', () => {
  it('enumerates each member with affected status and call', () => {
    const variant = makeVariant({
      genotypes: [
        { sample: 'S1', gt: '0/1' },
        { sample: 'S2', gt: '0/0' },
      ],
    });
    const sentence = buildSegregationSentence(variant, [
      member({ sample_id: 'S1', role: 'proband', affected: true }),
      member({ sample_id: 'S2', role: 'father', affected: false }),
    ]);
    expect(sentence).toContain('heterozygous in the affected proband (S1)');
    expect(sentence).toContain('wild-type in the unaffected father (S2)');
  });
});

describe('collectReportCriteria', () => {
  it('keeps only accepted criteria and resolves their definition', () => {
    const variant = makeVariant({
      review: {
        variant_id: 'v1',
        tags: ['report', 'acmg_class_4'],
        tag_metadata: {},
        acmg: {
          criteria: [
            { code: 'PM2', strength: 'moderate', accepted: true, auto_suggested: true },
            { code: 'PP3', strength: 'supporting', accepted: false, auto_suggested: true },
          ],
          point_total: 2,
          classification: 'Likely Pathogenic - class 4',
        },
      },
    });
    const criteria = collectReportCriteria(variant);
    expect(criteria).toHaveLength(1);
    expect(criteria[0].code).toBe('PM2');
    expect(criteria[0].strengthLabel).toBe('Moderate');
    expect(criteria[0].direction).toBe('pathogenic');
  });
});
