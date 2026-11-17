// Public surface of the ACMG engine.

export * from './types';
export {
  ACMG_CRITERIA,
  ACMG_CRITERIA_BY_CODE,
  ACMG_CLASS_LABELS,
  PATHOGENIC_CRITERIA,
  BENIGN_CRITERIA,
  STRENGTH_LABELS,
  STRENGTH_POINTS,
  EXCLUSIVE_CRITERIA,
  exclusivePartners,
} from './criteria';
export { computeClassification, selectionPoints, vusTierForPoints } from './score';
export { deNovoPhenotypeTier, deNovoStrength, evaluateAcmg, type AcmgVariantInput } from './evaluate';
export { evaluateMitoAcmg } from './evaluateMito';
export {
  parentageFromSampleQc,
  parentLinksFromRelationships,
  parentsOf,
  type MendelianCheckLike,
  type PedigreeRelationshipLike,
  type RelatednessCheckLike,
  type SampleQcLike,
} from './pedigree';

import { ACMG_CRITERIA_BY_CODE, EXCLUSIVE_CRITERIA } from './criteria';
import { selectionPoints } from './score';
import type { AcmgCriterionCode, AcmgSelection, AcmgSuggestion } from './types';

// Build the working selection list shown in the modal.
//
// When there is no saved classification, the evaluator drives the initial state:
//   - 'applies'         → auto-checked (positive, counts toward the score)
//   - 'consider'        → surfaced unchecked (suggested, decision support)
//   - 'contraindicated' → surfaced unchecked and flagged (negative)
// Saved selections (a prior classification) take precedence and keep the
// analyst's accepted state/strength; the evaluator only refreshes their evidence.
// Two criteria never applied together (EXCLUSIVE_CRITERIA) are never both checked: a saved one
// is kept over a fresh suggestion, then the one scoring more, then the one the pair lists first;
// the other is left unchecked (resolveExclusiveCriteria).
export function buildInitialSelections(
  suggestions: AcmgSuggestion[],
  saved: AcmgSelection[] = [],
): AcmgSelection[] {
  const byCode = new Map<string, AcmgSelection>();

  for (const selection of saved) {
    if (!ACMG_CRITERIA_BY_CODE[selection.code]) continue;
    byCode.set(selection.code, { ...selection });
  }

  // Higher-precedence dispositions win when the evaluator emits several for one
  // code (e.g. a positive should never be downgraded to not-applicable).
  const RANK: Record<AcmgSuggestion['disposition'], number> = {
    applies: 4,
    consider: 3,
    contraindicated: 2,
    not_applicable: 1,
  };
  const chosen = new Map<string, AcmgSuggestion>();
  for (const suggestion of suggestions) {
    if (!ACMG_CRITERIA_BY_CODE[suggestion.code]) continue;
    const current = chosen.get(suggestion.code);
    if (!current || RANK[suggestion.disposition] > RANK[current.disposition]) {
      chosen.set(suggestion.code, suggestion);
    }
  }

  for (const suggestion of chosen.values()) {
    if (byCode.has(suggestion.code)) {
      // Respect the saved decision; just refresh the evidence text.
      const existing = byCode.get(suggestion.code)!;
      if (!existing.evidence) existing.evidence = suggestion.evidence;
      continue;
    }
    byCode.set(suggestion.code, {
      code: suggestion.code,
      accepted: suggestion.disposition === 'applies',
      strength: suggestion.strength,
      evidence: suggestion.evidence,
      autoSuggested: suggestion.disposition === 'applies' || suggestion.disposition === 'consider',
      contraindicated: suggestion.disposition === 'contraindicated',
      notApplicable: suggestion.disposition === 'not_applicable',
    });
  }

  return resolveExclusiveCriteria([...byCode.values()], new Set(saved.map((selection) => selection.code)));
}

/**
 * The selections with at most one criterion of each exclusive pair checked. Where both are,
 * the one kept is a saved one (`savedCodes`) over a fresh suggestion, then the one scoring
 * more, then the one the pair lists first; the other is unchecked and stays listed.
 */
export function resolveExclusiveCriteria(
  selections: AcmgSelection[],
  savedCodes: ReadonlySet<AcmgCriterionCode> = new Set(),
): AcmgSelection[] {
  const byCode = new Map(selections.map((selection) => [selection.code, { ...selection }]));
  for (const [first, second] of EXCLUSIVE_CRITERIA) {
    const a = byCode.get(first);
    const b = byCode.get(second);
    if (!a?.accepted || !b?.accepted) continue;
    const savedA = savedCodes.has(first);
    const savedB = savedCodes.has(second);
    const keepFirst =
      savedA !== savedB ? savedA : Math.abs(selectionPoints(a)) >= Math.abs(selectionPoints(b));
    (keepFirst ? b : a).accepted = false;
  }
  return selections.map((selection) => byCode.get(selection.code) ?? selection);
}
