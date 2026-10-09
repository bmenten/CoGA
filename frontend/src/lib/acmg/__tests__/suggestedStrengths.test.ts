import { describe, expect, it } from 'vitest';

import { ACMG_CRITERIA, ACMG_CRITERIA_BY_CODE } from '../criteria';
import { evaluateAcmg, type AcmgVariantInput } from '../evaluate';
import { evaluateMitoAcmg } from '../evaluateMito';
import type {
  AcmgFamilyContext,
  AcmgGeneContext,
  AcmgMitoContext,
  AcmgPhenotypeContext,
  AcmgSuggestion,
} from '../types';

// CLIN-3: the server refuses a criterion at a strength its `allowedStrengths` do not list
// (backend acmg_points.ALLOWED_STRENGTHS mirrors them). The dialog saves a criterion at the
// strength it starts from unless the analyst picks another: its default, or the strength the
// pre-evaluation suggests. Each must be one the criterion allows, or the classification could
// not be saved as suggested.

const notAllowed = (suggestions: AcmgSuggestion[]) =>
  suggestions
    .filter((s) => !ACMG_CRITERIA_BY_CODE[s.code].allowedStrengths.includes(s.strength))
    .map((s) => `${s.code} at ${s.strength}`);

const HPO = 'HP:0001250';
const GENES: (AcmgGeneContext | undefined)[] = [
  undefined,
  {
    clingenDosageHaploinsufficiency: 'Sufficient evidence for dosage pathogenicity',
    modesOfInheritance: ['Autosomal recessive'],
    geneHpoIds: [HPO],
  },
  { unavailable: true },
];
const PHENOTYPES: (AcmgPhenotypeContext | undefined)[] = [
  undefined,
  { probandHpoIds: [HPO] },
  { probandHpoIds: [HPO], phenotypeScore: 0.4 },
  { probandHpoIds: [HPO], phenotypeScore: 0.7 },
  { probandHpoUnavailable: true },
];
const EFFECTS = [
  'stop_gained',
  'missense_variant',
  'synonymous_variant',
  'inframe_deletion',
  'splice_region_variant',
  undefined,
];
const FREQUENCIES = [undefined, 1e-5, 5e-4, 0.02, 0.2];

const trio = (proband: string, father: string, mother: string, fatherDp?: number): AcmgFamilyContext => ({
  members: [
    { sampleId: 'P', role: 'proband', affected: true, gt: proband, sex: 'male' },
    { sampleId: 'F', role: 'father', affected: false, gt: father, dp: fatherDp },
    { sampleId: 'M', role: 'mother', affected: false, gt: mother },
    { sampleId: 'S', role: 'sibling', affected: true, gt: father === '0/1' ? '0/1' : '0/0' },
  ],
  parentLinks: [
    { childId: 'P', parentId: 'F', role: 'father' },
    { childId: 'P', parentId: 'M', role: 'mother' },
  ],
});
const FAMILIES: (AcmgFamilyContext | undefined)[] = [
  undefined,
  trio('0/1', '0/0', '0/0'),
  trio('0/1', '0/0', '0/0', 5),
  trio('1/1', '0/0', '0/0'),
  trio('1', '0/0', '0/0'),
  trio('0/1', '0/1', '0/0'),
];

describe('the strength the dialog starts a criterion at', () => {
  it('is one of the criterion’s allowed strengths for its default', () => {
    expect(
      ACMG_CRITERIA.filter((def) => !def.allowedStrengths.includes(def.defaultStrength)).map((def) => def.code),
    ).toEqual([]);
  });

  it('is an allowed strength wherever the nuclear pre-evaluation suggests one', () => {
    const wrong = new Set<string>();
    const check = (suggestions: AcmgSuggestion[]) => notAllowed(suggestions).forEach((item) => wrong.add(item));
    for (const gene of GENES) {
      for (const effect of EFFECTS) {
        for (const lof of ['HC', undefined]) {
          for (const gnomad_af of FREQUENCIES) {
            for (const gnomad_hom_count of [0, 3]) {
              for (const clinvar of [undefined, 'Pathogenic', 'Benign']) {
                check(evaluateAcmg({ effect, lof, gnomad_af, gnomad_hom_count, clinvar, gene_missense_z: 4 }, gene));
              }
            }
          }
        }
      }
    }
    // In-silico evidence scales PP3 and BP4.
    for (const effect of ['missense_variant', 'synonymous_variant', 'splice_region_variant']) {
      for (const revel of [undefined, 0.01, 0.1, 0.25, 0.5, 0.7, 0.8, 0.95]) {
        for (const spliceai_max of [undefined, 0.05, 0.3, 0.6]) {
          for (const alphamissense_class of [null, 'likely_pathogenic', 'likely_benign']) {
            const variant: AcmgVariantInput = { effect, revel, spliceai_max, annotation_extra: { alphamissense_class } };
            check(evaluateAcmg(variant));
          }
        }
      }
    }
    // The phenotype scales PP4; the family decides PM6, PS2, PP1 and BS4.
    for (const gene of GENES) {
      for (const phenotype of PHENOTYPES) {
        for (const family of FAMILIES) {
          for (const chr of ['1', 'X']) {
            check(evaluateAcmg({ effect: 'missense_variant', chr, hemizygous_in_males: chr === 'X' }, gene, phenotype, family));
          }
        }
      }
    }
    expect([...wrong]).toEqual([]);
  });

  it('is an allowed strength wherever the mtDNA pre-evaluation suggests one', () => {
    const wrong = new Set<string>();
    const calls: AcmgMitoContext['calls'][] = [
      undefined,
      [
        { sampleId: 'P', role: 'proband', affected: true, zygosity: 'heteroplasmic', alleleFraction: 0.6 },
        { sampleId: 'M', role: 'mother', affected: true, zygosity: 'low_level' },
        { sampleId: 'S', role: 'sibling', affected: true, zygosity: 'reference' },
      ],
    ];
    for (const effect of ['stop_gained', 'missense_variant', undefined]) {
      for (const category of ['protein coding', 'tRNA', 'rRNA', 'control', null] as const) {
        for (const gnomad_af of [undefined, 1e-5, 1e-4, 1e-3, 0.01]) {
          for (const clinicalSignificance of [undefined, 'pathogenic', 'benign', 'polymorphism']) {
            for (const maternalTransmission of ['maternal_shared', undefined]) {
              for (const mitoCalls of calls) {
                for (const phenotype of PHENOTYPES) {
                  const mito: AcmgMitoContext = { category, clinicalSignificance, maternalTransmission, calls: mitoCalls };
                  const suggestions = evaluateMitoAcmg({ effect, gnomad_af, gene: 'MT-ND1' }, mito, GENES[1], phenotype);
                  notAllowed(suggestions).forEach((item) => wrong.add(item));
                }
              }
            }
          }
        }
      }
    }
    expect([...wrong]).toEqual([]);
  });
});
