// Public API for the ClinGen 2019 CNV classification engine.

import { cnvCriteriaForKind } from './criteria';
import type { CnvKind, CnvSelection, CnvSuggestion } from './types';

export * from './types';
export {
  CNV_CLASS_LABELS,
  CNV_LOSS_CRITERIA,
  CNV_GAIN_CRITERIA,
  CNV_SECTION_TITLES,
  cnvCriteriaForKind,
  cnvCriterionMap,
} from './criteria';
export { computeCnvClassification, classKeyForPoints } from './score';
export { formatCnvPoints } from './format';
export {
  evaluateCnv,
  cnvKindForType,
  cnvGeneCountMode,
  cnvGeneCountHint,
  cnvGeneCountTier,
  type CnvVariantInput,
  type CnvGeneCountMode,
} from './evaluate';

interface SavedCnvCriterion {
  code: string;
  points: number;
  accepted: boolean;
  evidence?: string | null;
  auto_suggested?: boolean;
}

/**
 * Build the modal's working selection list for every criterion of the given kind.
 * Saved selections take precedence over fresh auto-suggestions; criteria with
 * neither are present but unaccepted at their default points.
 */
export const buildInitialCnvSelections = (
  kind: CnvKind,
  suggestions: CnvSuggestion[],
  saved?: SavedCnvCriterion[],
): CnvSelection[] => {
  const suggestionMap = new Map(suggestions.map((s) => [s.code, s]));
  const savedMap = new Map((saved ?? []).map((s) => [s.code, s]));
  return cnvCriteriaForKind(kind).map((def) => {
    const savedSelection = savedMap.get(def.code);
    if (savedSelection) {
      return {
        code: def.code,
        points: savedSelection.points,
        accepted: savedSelection.accepted,
        evidence: savedSelection.evidence ?? undefined,
        autoSuggested: savedSelection.auto_suggested ?? false,
      };
    }
    const suggestion = suggestionMap.get(def.code);
    if (suggestion) {
      return {
        code: def.code,
        points: suggestion.points,
        accepted: true,
        evidence: suggestion.evidence,
        autoSuggested: true,
      };
    }
    return {
      code: def.code,
      points: def.defaultPoints,
      accepted: false,
      evidence: undefined,
      autoSuggested: false,
    };
  });
};
