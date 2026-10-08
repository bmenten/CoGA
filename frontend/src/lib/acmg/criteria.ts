// Static catalog of the 28 ACMG/AMP 2015 criteria plus the strength→points
// mapping used by the Bayesian points scorer.

import type {
  AcmgClassKey,
  AcmgCriterionCode,
  AcmgCriterionDef,
  AcmgStrength,
} from './types';

// Point magnitude per strength level (unsigned). Benign criteria negate these.
// Stand-alone (BA1) does not score by points — it is a Benign override.
export const STRENGTH_POINTS: Record<AcmgStrength, number> = {
  very_strong: 8,
  strong: 4,
  moderate: 2,
  supporting: 1,
  stand_alone: 0,
};

export const STRENGTH_LABELS: Record<AcmgStrength, string> = {
  very_strong: 'Very strong',
  strong: 'Strong',
  moderate: 'Moderate',
  supporting: 'Supporting',
  stand_alone: 'Stand-alone',
};

// ACMG class bands over the signed point total (ClinGen/Tavtigian thresholds):
// ≥10 Pathogenic · 6–9 Likely Pathogenic · 0–5 VUS · −1…−6 Likely Benign · ≤−7 Benign.
export const ACMG_CLASS_LABELS: Record<AcmgClassKey, string> = {
  acmg_class_5: 'Pathogenic - class 5',
  acmg_class_4: 'Likely Pathogenic - class 4',
  acmg_class_3: 'VUS - class 3',
  acmg_class_2: 'Likely benign - class 2',
  acmg_class_1: 'Benign - class 1',
};

const PATHOGENIC_STRENGTHS: AcmgStrength[] = [
  'very_strong',
  'strong',
  'moderate',
  'supporting',
];
const BENIGN_STRENGTHS: AcmgStrength[] = ['strong', 'moderate', 'supporting'];

export const ACMG_CRITERIA: AcmgCriterionDef[] = [
  // ---- Pathogenic ----
  {
    code: 'PVS1',
    direction: 'pathogenic',
    name: 'Null variant in a LOF gene',
    description:
      'Predicted null (nonsense, frameshift, canonical splice, start-loss, exon deletion) where loss of function is a known disease mechanism.',
    defaultStrength: 'very_strong',
    allowedStrengths: ['very_strong', 'strong', 'moderate', 'supporting'],
  },
  {
    code: 'PS1',
    direction: 'pathogenic',
    name: 'Same amino acid change as established pathogenic',
    description:
      'Same amino acid change as a previously established pathogenic variant regardless of nucleotide change.',
    defaultStrength: 'strong',
    allowedStrengths: PATHOGENIC_STRENGTHS,
  },
  {
    code: 'PS2',
    direction: 'pathogenic',
    name: 'De novo (confirmed)',
    description: 'De novo (maternity and paternity confirmed) in a patient with the disease and no family history.',
    defaultStrength: 'strong',
    allowedStrengths: PATHOGENIC_STRENGTHS,
  },
  {
    code: 'PS3',
    direction: 'pathogenic',
    name: 'Functional studies (damaging)',
    description: 'Well-established functional studies show a deleterious effect.',
    defaultStrength: 'strong',
    allowedStrengths: PATHOGENIC_STRENGTHS,
  },
  {
    code: 'PS4',
    direction: 'pathogenic',
    name: 'Increased prevalence in affecteds',
    description: 'Prevalence in affected individuals significantly increased over controls.',
    defaultStrength: 'strong',
    allowedStrengths: PATHOGENIC_STRENGTHS,
  },
  {
    code: 'PM1',
    direction: 'pathogenic',
    name: 'Mutational hot spot / functional domain',
    description: 'Located in a mutational hot spot and/or critical, well-established functional domain without benign variation.',
    defaultStrength: 'moderate',
    allowedStrengths: PATHOGENIC_STRENGTHS,
  },
  {
    code: 'PM2',
    direction: 'pathogenic',
    name: 'Absent / rare in population databases',
    description: 'Absent (or at extremely low frequency) from gnomAD and other controls. Applied at Supporting per ClinGen.',
    defaultStrength: 'supporting',
    allowedStrengths: ['moderate', 'supporting'],
  },
  {
    code: 'PM3',
    direction: 'pathogenic',
    name: 'In trans with a pathogenic variant (recessive)',
    description: 'For recessive disorders, detected in trans with a pathogenic variant.',
    defaultStrength: 'moderate',
    allowedStrengths: PATHOGENIC_STRENGTHS,
  },
  {
    code: 'PM4',
    direction: 'pathogenic',
    name: 'Protein length change',
    description: 'Protein length change from in-frame indels or stop-loss in a non-repeat region.',
    defaultStrength: 'moderate',
    allowedStrengths: PATHOGENIC_STRENGTHS,
  },
  {
    code: 'PM5',
    direction: 'pathogenic',
    name: 'Novel missense at a known pathogenic residue',
    description: 'Novel missense change at an amino acid residue where a different pathogenic missense has been seen.',
    defaultStrength: 'moderate',
    allowedStrengths: PATHOGENIC_STRENGTHS,
  },
  {
    code: 'PM6',
    direction: 'pathogenic',
    name: 'Assumed de novo',
    description: 'Assumed de novo without confirmation of maternity and paternity.',
    defaultStrength: 'moderate',
    allowedStrengths: PATHOGENIC_STRENGTHS,
  },
  {
    code: 'PP1',
    direction: 'pathogenic',
    name: 'Cosegregation with disease',
    description: 'Cosegregation with disease in multiple affected family members.',
    defaultStrength: 'supporting',
    allowedStrengths: PATHOGENIC_STRENGTHS,
  },
  {
    code: 'PP2',
    direction: 'pathogenic',
    name: 'Missense in a constrained gene',
    description: 'Missense in a gene with a low rate of benign missense variation where missense is a common mechanism.',
    defaultStrength: 'supporting',
    allowedStrengths: PATHOGENIC_STRENGTHS,
  },
  {
    code: 'PP3',
    direction: 'pathogenic',
    name: 'Computational evidence (deleterious)',
    description: 'Multiple in-silico lines of evidence support a deleterious effect. Strength scaled per ClinGen calibration.',
    defaultStrength: 'supporting',
    allowedStrengths: PATHOGENIC_STRENGTHS,
  },
  {
    code: 'PP4',
    direction: 'pathogenic',
    name: 'Phenotype specific for gene',
    description: "Patient's phenotype or family history is highly specific for a disease with a single genetic etiology.",
    defaultStrength: 'supporting',
    allowedStrengths: PATHOGENIC_STRENGTHS,
  },
  {
    code: 'PP5',
    direction: 'pathogenic',
    name: 'Reputable source reports pathogenic',
    description: 'Reputable source (e.g. ClinVar) reports the variant pathogenic but evidence is not available to evaluate.',
    defaultStrength: 'supporting',
    allowedStrengths: PATHOGENIC_STRENGTHS,
  },
  // ---- Benign ----
  {
    code: 'BA1',
    direction: 'benign',
    name: 'Allele frequency ≥ 5%',
    description: 'Allele frequency ≥ 5% in gnomAD or another large population database. Stand-alone benign.',
    defaultStrength: 'stand_alone',
    allowedStrengths: ['stand_alone'],
  },
  {
    code: 'BS1',
    direction: 'benign',
    name: 'Frequency greater than expected',
    description: 'Allele frequency greater than expected for the disorder.',
    defaultStrength: 'strong',
    allowedStrengths: BENIGN_STRENGTHS,
  },
  {
    code: 'BS2',
    direction: 'benign',
    name: 'Observed in healthy individuals',
    description: 'Observed in healthy adults (homozygous for recessive, heterozygous for dominant, hemizygous for X-linked).',
    defaultStrength: 'strong',
    allowedStrengths: BENIGN_STRENGTHS,
  },
  {
    code: 'BS3',
    direction: 'benign',
    name: 'Functional studies (no effect)',
    description: 'Well-established functional studies show no deleterious effect.',
    defaultStrength: 'strong',
    allowedStrengths: BENIGN_STRENGTHS,
  },
  {
    code: 'BS4',
    direction: 'benign',
    name: 'Lack of segregation',
    description: 'Lack of segregation in affected members of a family.',
    defaultStrength: 'strong',
    allowedStrengths: BENIGN_STRENGTHS,
  },
  {
    code: 'BP1',
    direction: 'benign',
    name: 'Missense where truncating causes disease',
    description: 'Missense variant in a gene where only truncating variants cause disease.',
    defaultStrength: 'supporting',
    allowedStrengths: ['supporting', 'moderate'],
  },
  {
    code: 'BP2',
    direction: 'benign',
    name: 'In trans/cis with pathogenic (dominant)',
    description: 'Observed in trans with a pathogenic variant for a dominant disorder, or in cis with a pathogenic variant.',
    defaultStrength: 'supporting',
    allowedStrengths: ['supporting', 'moderate'],
  },
  {
    code: 'BP3',
    direction: 'benign',
    name: 'In-frame indel in a repeat region',
    description: 'In-frame indel in a repetitive region without a known function.',
    defaultStrength: 'supporting',
    allowedStrengths: ['supporting', 'moderate'],
  },
  {
    code: 'BP4',
    direction: 'benign',
    name: 'Computational evidence (benign)',
    description: 'Multiple in-silico lines of evidence support no impact. Strength scaled per ClinGen calibration.',
    defaultStrength: 'supporting',
    allowedStrengths: ['supporting', 'moderate', 'strong'],
  },
  {
    code: 'BP5',
    direction: 'benign',
    name: 'Found with an alternate cause',
    description: 'Variant found in a case with an alternate molecular basis for disease.',
    defaultStrength: 'supporting',
    allowedStrengths: ['supporting', 'moderate'],
  },
  {
    code: 'BP6',
    direction: 'benign',
    name: 'Reputable source reports benign',
    description: 'Reputable source (e.g. ClinVar) reports the variant benign but evidence is not available to evaluate.',
    defaultStrength: 'supporting',
    allowedStrengths: ['supporting', 'moderate'],
  },
  {
    code: 'BP7',
    direction: 'benign',
    name: 'Synonymous with no splice impact',
    description: 'Synonymous variant with no predicted splice impact and not highly conserved.',
    defaultStrength: 'supporting',
    allowedStrengths: ['supporting', 'moderate'],
  },
];

export const ACMG_CRITERIA_BY_CODE: Record<AcmgCriterionCode, AcmgCriterionDef> =
  Object.fromEntries(ACMG_CRITERIA.map((def) => [def.code, def])) as Record<
    AcmgCriterionCode,
    AcmgCriterionDef
  >;

export const PATHOGENIC_CRITERIA = ACMG_CRITERIA.filter(
  (def) => def.direction === 'pathogenic',
);
export const BENIGN_CRITERIA = ACMG_CRITERIA.filter((def) => def.direction === 'benign');
