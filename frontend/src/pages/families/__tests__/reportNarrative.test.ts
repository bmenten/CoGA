import { describe, expect, it } from 'vitest';

import type { FamilyMember, SmallVariant } from '../smallVariantSearch';
import type { StructuralVariant } from '../structuralVariantSearch';
import { genotype, review, smallVariant, structuralVariant } from '../../../test/variants';
import {
  buildSegregationSentence,
  buildStructuralSegregationSentence,
  buildStructuralVariantSentence,
  buildVariantSentence,
  collectReportCriteria,
  describeClinvar,
  describeGnomadFrequency,
  describeInSilico,
  describeStructuralFrequency,
  describeZygosity,
  formatAlleleFrequency,
  humanizeEffect,
} from '../reportNarrative';

const makeVariant = (overrides: Partial<SmallVariant> = {}): SmallVariant =>
  smallVariant({
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
    genotypes: [genotype({ sample: 'S1', gt: '0/1' })],
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

// ENG-10: the draft printed VEP's own spelling, "SIFT deleterious_low_confidence(0.03)", where
// the cards print "deleterious low confidence (0.03)".
describe('describeInSilico', () => {
  it('writes SIFT and PolyPhen as the cards write them', () => {
    const text = describeInSilico(
      makeVariant({ sift: 'deleterious_low_confidence(0.03)', polyphen: 'probably_damaging(0.998)' }),
    );
    expect(text).toContain('SIFT deleterious low confidence (0.03)');
    expect(text).toContain('PolyPhen probably damaging (0.998)');
    expect(text).not.toContain('_');
  });

  it('lists every predictor given, in a fixed order, and says when there is none', () => {
    expect(
      describeInSilico(
        makeVariant({
          cadd_phred: 28.14,
          revel: 0.4126,
          sift: 'deleterious(0.01)',
          polyphen: 'benign(0.1)',
          spliceai_max: 0.026,
        }),
      ),
    ).toBe(
      'In silico predictors report CADD 28.1, REVEL 0.413, SIFT deleterious (0.01), PolyPhen benign (0.1) and SpliceAI 0.03.',
    );
    // A score of 0 is a score.
    expect(describeInSilico(makeVariant({ revel: 0 }))).toBe('In silico predictors report REVEL 0.000.');
    expect(describeInSilico(makeVariant({ cadd_phred: null, revel: null, spliceai_max: null }))).toBe(
      'No in silico pathogenicity predictions were available for this variant.',
    );
  });
});

describe('describeClinvar', () => {
  it('says what ClinVar reports in words, and nothing without a ClinVar significance', () => {
    expect(describeClinvar(makeVariant({ clinvar: 'Likely_pathogenic' }))).toBe(
      'ClinVar reports this variant as Likely pathogenic.',
    );
    expect(describeClinvar(makeVariant({ clinvar: '  ' }))).toBeNull();
    expect(describeClinvar(makeVariant({ clinvar: null }))).toBeNull();
  });
});

describe('describeGnomadFrequency', () => {
  it('handles absent, missing and present frequencies', () => {
    expect(describeGnomadFrequency(makeVariant({ gnomad_af: 0 }))).toBe('is absent from gnomAD');
    expect(describeGnomadFrequency(makeVariant({ gnomad_af: null }))).toBe('has no reported gnomAD allele frequency');
    expect(describeGnomadFrequency(makeVariant({ gnomad_af: undefined }))).toBe(
      'has no reported gnomAD allele frequency',
    );
    expect(describeGnomadFrequency(makeVariant({ gnomad_af: 0.2 }))).toBe(
      'is present in gnomAD at an allele frequency of 0.2',
    );
  });

  it('calls a variant extremely rare below 1 in 10,000 and rare below 1 in 100', () => {
    expect(describeGnomadFrequency(makeVariant({ gnomad_af: 0.00005 }))).toBe(
      'is extremely rare in gnomAD (allele frequency 5.00e-5)',
    );
    expect(describeGnomadFrequency(makeVariant({ gnomad_af: 0.0001 }))).toBe('is rare in gnomAD (allele frequency 0.0001)');
    expect(describeGnomadFrequency(makeVariant({ gnomad_af: 0.0042 }))).toBe('is rare in gnomAD (allele frequency 0.0042)');
    expect(describeGnomadFrequency(makeVariant({ gnomad_af: 0.01 }))).toBe(
      'is present in gnomAD at an allele frequency of 0.01',
    );
  });

  it('mentions homozygotes when present', () => {
    expect(describeGnomadFrequency(makeVariant({ gnomad_af: 0.00005, gnomad_hom_count: 2 }))).toBe(
      'is extremely rare in gnomAD (allele frequency 5.00e-5 with 2 homozygotes)',
    );
    expect(describeGnomadFrequency(makeVariant({ gnomad_af: 0.002, gnomad_hom_count: 1 }))).toBe(
      'is rare in gnomAD (allele frequency 0.002 with 1 homozygote)',
    );
    expect(describeGnomadFrequency(makeVariant({ gnomad_af: 0.002, gnomad_hom_count: 0 }))).toBe(
      'is rare in gnomAD (allele frequency 0.002)',
    );
  });

  it('writes a frequency without trailing zeros, a tiny one in scientific notation', () => {
    expect(formatAlleleFrequency(0)).toBe('0');
    expect(formatAlleleFrequency(0.000012)).toBe('1.20e-5');
    expect(formatAlleleFrequency(0.0123)).toBe('0.0123');
    expect(formatAlleleFrequency(0.5)).toBe('0.5');
    expect(formatAlleleFrequency(1)).toBe('1');
  });
});

describe('buildVariantSentence', () => {
  it('describes the variant with zygosity, gene, HGVS and locus', () => {
    const sentence = buildVariantSentence(makeVariant(), [member()]);
    expect(sentence).toContain('heterozygous');
    expect(sentence).toContain('BRCA1 c.123A>G (p.Lys41Arg)');
    expect(sentence).toContain('chr17:43,000,000');
    expect(sentence).toContain('proband (S1)');
    expect(sentence).toBe(
      'A heterozygous missense variant, BRCA1 c.123A>G (p.Lys41Arg), was identified at chr17:43,000,000-43,000,000 in the proband (S1).',
    );
  });

  it('names the affected member when no member is the proband, and leaves out what it does not know', () => {
    const variant = makeVariant({
      gene: null,
      gene_id: 'ENSG00000012048',
      hgvsp: null,
      rsid: 'rs80357713',
      genotypes: [genotype({ sample: 'S1', gt: '0/0' }), genotype({ sample: 'S2', gt: '1/1' })],
    });
    expect(
      buildVariantSentence(variant, [
        member({ sample_id: 'S1', role: 'mother', affected: false }),
        member({ sample_id: 'S2', role: 'sibling', affected: false, clinical_status: 'affected' }),
      ]),
    ).toBe(
      'A homozygous missense variant, ENSG00000012048 c.123A>G, was identified at chr17:43,000,000-43,000,000 (rs80357713) in the sibling (S2).',
    );
    // No member, no zygosity: the sentence names neither.
    expect(buildVariantSentence(makeVariant({ effect: null, hgvsc: null, hgvsp: null }), [])).toBe(
      'A sequence variant, BRCA1, was identified at chr17:43,000,000-43,000,000.',
    );
  });
});

describe('buildSegregationSentence', () => {
  it('enumerates each member with affected status and call', () => {
    const variant = makeVariant({
      genotypes: [genotype({ sample: 'S1', gt: '0/1' }), genotype({ sample: 'S2', gt: '0/0' })],
    });
    const sentence = buildSegregationSentence(variant, [
      member({ sample_id: 'S1', role: 'proband', affected: true }),
      member({ sample_id: 'S2', role: 'father', affected: false }),
    ]);
    expect(sentence).toContain('heterozygous in the affected proband (S1)');
    expect(sentence).toContain('wild-type in the unaffected father (S2)');
    expect(sentence).toBe(
      'Within the family, the variant is heterozygous in the affected proband (S1) and wild-type in the unaffected father (S2).',
    );
  });

  it('says a member without a confident call is one, and nothing for a family without members', () => {
    const variant = makeVariant({ genotypes: [genotype({ sample: 'S1', gt: '1/1' }), genotype({ sample: 'S3', gt: './.' })] });
    expect(
      buildSegregationSentence(variant, [
        member({ sample_id: 'S1' }),
        member({ sample_id: 'S2', role: 'mother', affected: false }),
        member({ sample_id: 'S3', role: 'sibling', affected: false, clinical_status: 'affected' }),
      ]),
    ).toBe(
      'Within the family, the variant is homozygous in the affected proband (S1), no confident call in the unaffected mother (S2) and no confident call in the affected sibling (S3).',
    );
    expect(buildSegregationSentence(variant, [])).toBeNull();
  });
});

describe('collectReportCriteria', () => {
  it('keeps only accepted criteria and resolves their definition', () => {
    const variant = makeVariant({
      review: review({
        variant_id: 'v1',
        tags: ['report', 'acmg_class_4'],
        acmg: {
          criteria: [
            { code: 'PM2', strength: 'moderate', accepted: true, auto_suggested: true },
            { code: 'PP3', strength: 'supporting', accepted: false, auto_suggested: true },
          ],
          point_total: 2,
          classification: 'Likely pathogenic - class 4',
        },
      }),
    });
    const criteria = collectReportCriteria(variant);
    expect(criteria).toHaveLength(1);
    expect(criteria[0].code).toBe('PM2');
    expect(criteria[0].strengthLabel).toBe('Moderate');
    expect(criteria[0].direction).toBe('pathogenic');
  });
});

describe('buildStructuralVariantSentence', () => {
  const sv = (overrides: Partial<StructuralVariant> = {}): StructuralVariant =>
    structuralVariant({
      _id: 'sv1',
      chr: '1',
      start: 20000,
      end: 25000,
      length: -5000,
      type: 'DEL',
      gene: 'GENE1',
      genotypes: [],
      ...overrides,
    });

  it('gives a translocation no size, and names its mate', () => {
    // Before: "A 0 bp breakend / translocation" (CLIN-39).
    expect(
      buildStructuralVariantSentence(
        sv({ type: 'BND', end: 20000, length: null, remote_chr: '5', remote_start: 3000000, gene: null }),
      ),
    ).toBe(
      'A breakend / translocation at 1:20,000-20,000 in an intergenic region with a remote breakpoint at 5:3,000,000.',
    );
    expect(buildStructuralVariantSentence(sv({ type: 'tra', length: 1 }))).toBe(
      'A translocation at 1:20,000-25,000 overlapping GENE1.',
    );
  });

  it('writes the size, with an article as the size is said', () => {
    expect(buildStructuralVariantSentence(sv())).toBe('A 5.0 kb deletion at 1:20,000-25,000 overlapping GENE1.');
    expect(buildStructuralVariantSentence(sv({ length: 8000 }))).toBe(
      'An 8.0 kb deletion at 1:20,000-25,000 overlapping GENE1.',
    );
    expect(buildStructuralVariantSentence(sv({ length: 11, type: 'INS' }))).toBe(
      'An 11 bp insertion at 1:20,000-25,000 overlapping GENE1.',
    );
    expect(buildStructuralVariantSentence(sv({ length: 110, type: 'INS' }))).toBe(
      'A 110 bp insertion at 1:20,000-25,000 overlapping GENE1.',
    );
    expect(buildStructuralVariantSentence(sv({ length: null, type: 'INV' }))).toBe(
      'An inversion of unknown size at 1:20,000-25,000 overlapping GENE1.',
    );
    expect(buildStructuralVariantSentence(sv({ length: 2_350_000, type: 'dup' }))).toBe(
      'A 2.4 Mb duplication at 1:20,000-25,000 overlapping GENE1.',
    );
    expect(buildStructuralVariantSentence(sv({ length: 1000, type: 'CNV' }))).toBe(
      'A 1000 bp copy-number variant at 1:20,000-25,000 overlapping GENE1.',
    );
  });

  it('names a type it has no word for as such, and a call without a type as a structural variant', () => {
    expect(buildStructuralVariantSentence(sv({ type: 'CPX', gene: null }))).toBe(
      'A 5.0 kb CPX structural variant at 1:20,000-25,000 in an intergenic region.',
    );
    expect(buildStructuralVariantSentence(sv({ type: '' }))).toBe(
      'A 5.0 kb structural variant at 1:20,000-25,000 overlapping GENE1.',
    );
  });
});

describe('the frequency and segregation of a structural variant', () => {
  const sv = (annotation_extra?: StructuralVariant['annotation_extra']): StructuralVariant =>
    structuralVariant({ _id: 'sv1', chr: '1', start: 1, end: 2, type: 'DEL', genotypes: [], annotation_extra });

  it('gives the control cohort and population frequencies it has, and says when it has none', () => {
    expect(describeStructuralFrequency(sv({ control_af: 0.012, population_af: 0.00002 }))).toBe(
      'has a control cohort allele frequency 0.012 and population allele frequency 2.00e-5',
    );
    expect(describeStructuralFrequency(sv({ control_af: 0 }))).toBe('has a control cohort allele frequency 0');
    expect(describeStructuralFrequency(sv({ population_af: 0.3 }))).toBe('has a population allele frequency 0.3');
    expect(describeStructuralFrequency(sv())).toBe('has no reported control or population allele frequency');
  });

  it('says the inheritance the call is annotated with, and the affected members', () => {
    const members = [
      { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'female' },
      { sample_id: 'MOTHER', role: 'mother', affected: false, sex: 'female' },
      { sample_id: 'FATHER1', role: 'father', affected: true, sex: 'male' },
    ];
    expect(buildStructuralSegregationSentence(sv({ inheritance: ' de_novo ' }), members)).toBe(
      'The call is annotated as de novo in PROBAND and FATHER1.',
    );
    expect(buildStructuralSegregationSentence(sv({ inheritance: 'inherited' }))).toBe('The call is annotated as inherited.');
    expect(buildStructuralSegregationSentence(sv({ inheritance: ' ' }), members)).toBeNull();
    expect(buildStructuralSegregationSentence(sv(), members)).toBeNull();
  });
});
