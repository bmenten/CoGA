import { describe, expect, it } from 'vitest';

import type { GenotypeRow } from '../FamilyGenotypeGrid';
import { niptCompactGenotypes, niptGenotypeEntries, type NiptTrio } from '../niptGenotypes';
import type { FamilyMember, SmallVariant } from '../smallVariantSearch';
import { niptClassification } from '../../../test/variants';

const MEMBERS: FamilyMember[] = [
  { sample_id: 'EMB1', role: 'proband', sex: 'male', affected: false, active: true },
  { sample_id: 'FATHER1', role: 'father', sex: 'male', affected: false, active: true },
  { sample_id: 'MOTHER', role: 'mother', sex: 'female', affected: false, active: true },
  { sample_id: 'SIB1', role: 'sibling', sex: 'female', affected: true, active: true },
] as FamilyMember[];

// The plasma is held by the mother's sample (no sample carries the nipt_cfdna assay).
const TRIO: NiptTrio = { cfdna_sample_id: 'MOTHER', father_sample_id: 'FATHER1', fetus_sample_id: 'EMB1' };

// A plasma MNV the father's VCF writes as its two SNVs: his call is read off one of them.
const NIPT = niptClassification({
  category: 4,
  category_label: 'maternal het, inherited (hom fetus)',
  maternal_state: 'het',
  fetal_inheritance: 'maternal_inherited_hom',
  expected_vaf: 0.561,
  observed_vaf: 0.541,
  confidence: 0.6,
  cf_alt_reads: 1129,
  cf_depth: 2086,
  father_state: 'het',
  father_vaf: 0.488,
  father_depth: 299,
  father_alt_reads: 146,
  father_call_variant_id: '1-101-G-A',
  flags: ['father_call_other_representation'],
});

const ownRow = (member: FamilyMember): GenotypeRow => ({ member, tone: 'warn', call: 'own GT' });

const rows = (nipt = NIPT) =>
  niptGenotypeEntries(nipt, MEMBERS, TRIO, ownRow).filter((entry): entry is GenotypeRow => 'member' in entry);

describe('niptGenotypeEntries', () => {
  it('shows the plasma and the father as the analysis read them, and no fetus', () => {
    const [father, plasma, sibling] = rows();
    expect(rows().map((row) => row.member.sample_id)).toEqual(['FATHER1', 'MOTHER', 'SIB1']);
    // The plasma: a mixture, so no genotype; its reads are those the classification used.
    expect(plasma.member.role).toBe('plasma (cfDNA)');
    expect(plasma.call).toBe('mixture');
    expect(plasma.values).toEqual({ dp: 2086, ad: '957,1129', af: '0.54' });
    expect(plasma.detail).toBeUndefined();
    // The father: his genotype class from his allele depths, also where the variant's own record
    // holds no call of his, with the record it was read from.
    expect(father.member.role).toBe('father');
    expect([father.call, father.tone]).toEqual(['het', 'warn']);
    expect(father.values).toEqual({ dp: 299, ad: '153,146', af: '0.49' });
    expect(father.detail).toBe('Read from his call on 1-101-G-A: his VCF writes this allele another way.');
    // Any other member keeps its own call.
    expect(sibling.call).toBe('own GT');
  });

  it('says where the plasma has no call, and where a sample calls only part of the allele', () => {
    const [father, plasma] = rows(
      niptClassification({
        ...NIPT,
        cf_alt_reads: 0,
        cf_depth: 1500,
        cf_depth_estimated: true,
        observed_vaf: null,
        father_state: 'absent',
        father_vaf: null,
        father_depth: 310,
        father_alt_reads: null,
        father_call_variant_id: null,
        flags: ['father_call_overlaps'],
      }),
    );
    expect([plasma.call, plasma.tone]).toEqual(['no call', 'na']);
    expect(plasma.values).toEqual({ dp: '~1500', ad: '—', af: '—' });
    expect(plasma.detail).toBe('No plasma call here: the depth is its target coverage.');
    expect([father.call, father.tone]).toEqual(['no call (reference)', 'ref']);
    expect(father.values).toEqual({ dp: 310, ad: '—', af: '—' });
    expect(father.detail).toBe("His calls carry only part of this allele's bases: read as no call.");
  });

  it("names the record the plasma's call was read from", () => {
    const [, plasma] = rows(niptClassification({ ...NIPT, cf_call_variant_id: '1-100-T-C', father_call_variant_id: null }));
    expect(plasma.detail).toBe("Read from the plasma's call on 1-100-T-C: its VCF writes this allele another way.");
  });
});

describe('niptCompactGenotypes', () => {
  it("gives the plasma's allele fraction and the father's genotype class, without the fetus", () => {
    const variant = {
      genotypes: [
        { sample: 'MOTHER', gt: '0/1' },
        { sample: 'SIB1', gt: '1/1' },
      ],
      nipt: NIPT,
    } as unknown as SmallVariant & { nipt: typeof NIPT };
    expect(niptCompactGenotypes(variant, MEMBERS, TRIO).map((entry) => [entry.sampleId, entry.gt])).toEqual([
      ['FATHER1', 'father: het'],
      ['MOTHER', 'plasma 54.1%'],
      ['SIB1', '1/1'],
    ]);
  });
});
