// Auto-suggest ClinGen CNV evidence from the structural variant's available data.
// Conservative by design: it fires only on signals the SV payload reliably carries
// (gene overlap, gene constraint, annotated inheritance). The analyst confirms and
// completes the rest in the modal.

import { cnvCriterionMap } from './criteria';
import type { CnvKind, CnvSuggestion } from './types';

export interface CnvVariantInput {
  type?: string | null;
  gene?: string | null;
  genePli?: number | null;
  inheritance?: string | null;
  /** Number of protein-coding genes the event overlaps (for section-3 scoring). */
  geneCount?: number | null;
}

// Section-3 gene-count tiers (ClinGen): losses 0–24 / 25–34 / 35+; gains use the
// higher 0–34 / 35–49 / 50+ thresholds.
export const cnvGeneCountTier = (
  kind: CnvKind,
  count: number,
): { code: '3A' | '3B' | '3C'; points: number } => {
  if (kind === 'gain') {
    if (count >= 50) return { code: '3C', points: 0.9 };
    if (count >= 35) return { code: '3B', points: 0.45 };
    return { code: '3A', points: 0 };
  }
  if (count >= 35) return { code: '3C', points: 0.9 };
  if (count >= 25) return { code: '3B', points: 0.45 };
  return { code: '3A', points: 0 };
};

// The SV type without its subtypes, in capitals: DEL:ME is DEL and DUP:TANDEM is DUP, as the
// server reads an SV's type (sv_copy_change.sv_kind). The exact type was matched, so a
// tandem duplication opened on the loss criteria, with 2H pre-selected in a constrained
// gene (CLIN-47).
const svKind = (type?: string | null): string => (type || '').trim().toUpperCase().split(':')[0];

const GAIN_TYPES = new Set(['DUP', 'GAIN', 'CNV_GAIN', 'INS']);

export const cnvKindForType = (type?: string | null): CnvKind =>
  GAIN_TYPES.has(svKind(type)) ? 'gain' : 'loss';

// Section-3 gene counting differs by event class: a deletion changes the copy
// number of every gene it spans (count all wholly/partially included genes),
// whereas inversions, translocations/breakends and insertions only alter genes
// their breakpoints disrupt (count only disrupted genes).
const DISRUPTION_ONLY_TYPES = new Set(['INV', 'BND', 'TRA', 'INS']);

export type CnvGeneCountMode = 'all' | 'disrupted';

export const cnvGeneCountMode = (type?: string | null): CnvGeneCountMode =>
  DISRUPTION_ONLY_TYPES.has(svKind(type)) ? 'disrupted' : 'all';

export const cnvGeneCountHint = (type?: string | null): string =>
  cnvGeneCountMode(type) === 'all'
    ? 'Count every protein-coding gene wholly or partially within the event.'
    : 'Count only genes disrupted by a breakpoint — fully contained genes keep their copy number for inversions, translocations and insertions.';

export const evaluateCnv = (input: CnvVariantInput): CnvSuggestion[] => {
  const kind = cnvKindForType(input.type);
  const map = cnvCriterionMap(kind);
  const out: CnvSuggestion[] = [];
  const hasGene = Boolean(input.gene && input.gene.trim());

  const geneCount = typeof input.geneCount === 'number' ? input.geneCount : null;
  if (hasGene || (geneCount ?? 0) > 0) {
    out.push({ code: '1A', points: 0, evidence: `Overlaps ${input.gene || 'a protein-coding gene'}.` });
    if (geneCount !== null) {
      if (cnvGeneCountMode(input.type) === 'all') {
        // Copy-number events (DEL/DUP): the overlapped protein-coding gene count is
        // the ClinGen section-3 count, so auto-score the tier.
        const tier = cnvGeneCountTier(kind, geneCount);
        out.push({
          code: tier.code,
          points: tier.points,
          evidence: `${geneCount} overlapped protein-coding gene${geneCount === 1 ? '' : 's'}.`,
        });
      } else {
        // Inversions/translocations/insertions: only breakpoint-disrupted genes
        // count, which can't be derived from overlap — leave the tier at 3A.
        out.push({
          code: '3A',
          points: 0,
          evidence: `${geneCount} overlapped gene${geneCount === 1 ? '' : 's'}. ${cnvGeneCountHint(input.type)}`,
        });
      }
    }
  } else {
    out.push({ code: '1B', points: -0.6, evidence: 'No protein-coding gene overlap detected.' });
  }

  const pli = input.genePli;
  if (kind === 'loss' && typeof pli === 'number' && pli >= 0.9) {
    out.push({
      code: '2H',
      points: map['2H']?.maxPoints ?? 0.15,
      evidence: `Constrained gene (pLI ${pli.toFixed(3)}) supports haploinsufficiency.`,
    });
  }

  const inh = (input.inheritance || '').toLowerCase();
  if (inh.includes('de') && inh.includes('novo')) {
    out.push({
      code: '5B',
      points: map['5B']?.maxPoints ?? 0.3,
      evidence: 'Annotated de novo (assumed — confirm with parental testing for 5A).',
    });
  } else if (
    inh.includes('maternal') ||
    inh.includes('paternal') ||
    inh.includes('inherited')
  ) {
    out.push({ code: '5F', points: 0, evidence: `Annotated as ${inh}.` });
  }

  return out;
};
