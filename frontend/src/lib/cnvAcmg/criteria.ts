// ClinGen 2019 CNV evidence catalogs (loss & gain). Mirrors
// backend/app/services/cnv_acmg_points.py — keep the codes and point ranges in sync
// (backend/tests/test_acmg_frontend_parity.py reads this file and fails when they differ).

import type { CnvCriterionDef, CnvKind } from './types';

const c = (
  code: string,
  section: string,
  name: string,
  defaultPoints: number,
  minPoints: number,
  maxPoints: number,
): CnvCriterionDef => ({ code, section, name, defaultPoints, minPoints, maxPoints });

export const CNV_LOSS_CRITERIA: CnvCriterionDef[] = [
  c('1A', '1', 'Contains protein-coding or known functional elements', 0, 0, 0),
  c('1B', '1', 'Does NOT contain protein-coding or known functional elements', -0.6, -0.6, -0.6),
  c('2A', '2', 'Complete overlap of an established haploinsufficient (HI) gene/region', 1.0, 1.0, 1.0),
  c('2B', '2', 'Partial overlap of an established HI genomic region', 0, 0, 0.9),
  c('2C-1', '2', "Partial overlap of 5' end of an established HI gene — coding sequence involved", 0.9, 0, 0.9),
  c('2C-2', '2', "Partial overlap of 5' end of an established HI gene — 5'UTR only", 0, 0, 0.45),
  c('2D-2', '2', "Partial overlap of 3' end of an established HI gene — last exon, other pathogenic SNVs reported", 0.9, 0, 0.9),
  c('2D-3', '2', "Partial overlap of 3' end of an established HI gene — last exon, no pathogenic SNVs", 0.3, 0, 0.45),
  c('2E', '2', 'Both breakpoints within the same established HI gene (intragenic, PVS1-like)', 0.9, -0.45, 0.9),
  c('2F', '2', 'Completely contained within an established benign CNV region', -1.0, -1.0, -1.0),
  c('2G', '2', 'Overlaps an established benign CNV but includes additional genomic material', 0, 0, 0),
  c('2H', '2', 'Two or more HI predictors suggest the gene(s) are haploinsufficient', 0.15, 0, 0.15),
  c('3A', '3', '0–24 protein-coding genes wholly or partially included', 0, 0, 0),
  c('3B', '3', '25–34 protein-coding genes wholly or partially included', 0.45, 0.45, 0.45),
  c('3C', '3', '35+ protein-coding genes wholly or partially included', 0.9, 0.9, 0.9),
  c('4A', '4', 'Reported proband(s) with a consistent phenotype, de novo', 0, 0, 0.9),
  c('4B', '4', 'Reported proband(s) with a consistent phenotype, inheritance unknown', 0, 0, 0.45),
  c('4C', '4', 'Reported proband(s) with a nonspecific or limited phenotype', 0, 0, 0.3),
  c('4D', '4', 'Reported in unaffected individuals or population controls', 0, -0.9, 0),
  c('4F', '4', 'Functional studies support a deleterious effect', 0, 0, 0.45),
  c('4G', '4', 'Functional studies support no deleterious effect', 0, -0.45, 0),
  c('5A', '5', 'De novo, confirmed (both parents tested)', 0, 0, 0.45),
  c('5B', '5', 'De novo, assumed (parental testing incomplete)', 0, 0, 0.3),
  c('5C', '5', 'Inherited from an affected parent / segregates with disease', 0, 0, 0.45),
  c('5D', '5', 'Inherited from an unaffected parent', 0, -0.45, 0),
  c('5E', '5', 'Does not segregate with the phenotype in the family', 0, -0.9, 0),
  c('5F', '5', 'Inheritance information unavailable', 0, 0, 0),
];

export const CNV_GAIN_CRITERIA: CnvCriterionDef[] = [
  c('1A', '1', 'Contains protein-coding or known functional elements', 0, 0, 0),
  c('1B', '1', 'Does NOT contain protein-coding or known functional elements', -0.6, -0.6, -0.6),
  c('2A', '2', 'Complete overlap of an established triplosensitive (TS) gene/region', 1.0, 1.0, 1.0),
  c('2B', '2', 'Partial overlap of an established TS genomic region', 0, 0, 0.9),
  c('2C', '2', 'Identical in gene content to an established benign copy-number gain', -1.0, -1.0, -1.0),
  c('2D', '2', 'Smaller than an established benign gain; breakpoints do not interrupt protein-coding genes', -1.0, -1.0, -1.0),
  c('2E', '2', 'Smaller than an established benign gain; breakpoints potentially interrupt protein-coding genes', 0, 0, 0),
  c('2F', '2', 'Larger than an established benign gain; no additional protein-coding genes', -1.0, -1.0, -1.0),
  c('2G', '2', 'Overlaps an established benign gain but includes additional genomic material', 0, 0, 0),
  c('2H', '2', 'An established HI gene is fully contained within the observed gain', 0, 0, 0),
  c('2I', '2', 'Both breakpoints are within the same gene (gene disruption)', 0, 0, 0.45),
  c('3A', '3', '0–34 protein-coding genes wholly or partially included', 0, 0, 0),
  c('3B', '3', '35–49 protein-coding genes wholly or partially included', 0.45, 0.45, 0.45),
  c('3C', '3', '50+ protein-coding genes wholly or partially included', 0.9, 0.9, 0.9),
  c('4A', '4', 'Reported proband(s) with a consistent phenotype, de novo', 0, 0, 0.9),
  c('4B', '4', 'Reported proband(s) with a consistent phenotype, inheritance unknown', 0, 0, 0.45),
  c('4C', '4', 'Reported proband(s) with a nonspecific or limited phenotype', 0, 0, 0.3),
  c('4D', '4', 'Reported in unaffected individuals or population controls', 0, -0.9, 0),
  c('4F', '4', 'Functional studies support a deleterious effect', 0, 0, 0.45),
  c('4G', '4', 'Functional studies support no deleterious effect', 0, -0.45, 0),
  c('5A', '5', 'De novo, confirmed (both parents tested)', 0, 0, 0.45),
  c('5B', '5', 'De novo, assumed (parental testing incomplete)', 0, 0, 0.3),
  c('5C', '5', 'Inherited from an affected parent / segregates with disease', 0, 0, 0.45),
  c('5D', '5', 'Inherited from an unaffected parent', 0, -0.45, 0),
  c('5E', '5', 'Does not segregate with the phenotype in the family', 0, -0.9, 0),
  c('5F', '5', 'Inheritance information unavailable', 0, 0, 0),
];

export const CNV_SECTION_TITLES: Record<string, string> = {
  '1': 'Section 1 — Initial genomic content',
  '2': 'Section 2 — Overlap with established dosage-sensitive / benign regions',
  '3': 'Section 3 — Number of protein-coding genes',
  '4': 'Section 4 — Literature / case evidence',
  '5': 'Section 5 — Inheritance & family history',
};

export const CNV_CLASS_LABELS: Record<string, string> = {
  cnv_class_5: 'Pathogenic - class 5',
  cnv_class_4: 'Likely Pathogenic - class 4',
  cnv_class_3: 'VUS - class 3',
  cnv_class_2: 'Likely benign - class 2',
  cnv_class_1: 'Benign - class 1',
};

export const cnvCriteriaForKind = (kind: CnvKind): CnvCriterionDef[] =>
  kind === 'gain' ? CNV_GAIN_CRITERIA : CNV_LOSS_CRITERIA;

export const cnvCriterionMap = (kind: CnvKind): Record<string, CnvCriterionDef> =>
  Object.fromEntries(cnvCriteriaForKind(kind).map((def) => [def.code, def]));
