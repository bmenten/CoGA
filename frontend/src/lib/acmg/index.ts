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
  CAPPED_CRITERIA,
  capsOf,
  exclusivePartners,
} from './criteria';
export { computeClassification, selectionPoints, vusTierForPoints } from './score';
export { deNovoPhenotypeTier, deNovoStrength, evaluateAcmg, type AcmgVariantInput } from './evaluate';
export { evaluateMitoAcmg } from './evaluateMito';
export {
  parentageFromSampleQc,
  parentLinksFromRelationships,
  parentsOf,
  personOfSamples,
  type MendelianCheckLike,
  type PedigreeRelationshipLike,
  type RelatednessCheckLike,
  type SampleQcLike,
} from './pedigree';

import { ACMG_CRITERIA_BY_CODE, CAPPED_CRITERIA, EXCLUSIVE_CRITERIA, STRENGTH_POINTS, capsOf, exclusivePartners } from './criteria';
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
// Two criteria never applied together (EXCLUSIVE_CRITERIA) are never both checked, and a capped
// group (CAPPED_CRITERIA) never scores over its cap: a saved criterion is kept over a fresh
// suggestion, then the one scoring more, then the one listed first; the other is unchecked, or
// for a cap lowered to fit (resolveExclusiveCriteria).
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
 * The selections with at most one criterion of each exclusive pair checked, and each capped
 * group within its points. Where both of a pair are checked, the one kept is a saved one
 * (`savedCodes`) over a fresh suggestion, then the one scoring more, then the one the pair lists
 * first; the other is unchecked and stays listed. A capped group over its points keeps its
 * criterion chosen the same way and lowers the others (fitCappedCriteria).
 */
export function resolveExclusiveCriteria(
  selections: AcmgSelection[],
  savedCodes: ReadonlySet<AcmgCriterionCode> = new Set(),
): AcmgSelection[] {
  const byCode = new Map(selections.map((selection) => [selection.code, { ...selection }]));
  // The criterion of `codes` to keep: a saved one, then the one scoring most, then the first.
  const pick = (codes: readonly AcmgCriterionCode[]) =>
    codes
      .map((code, order) => ({ code, order, selection: byCode.get(code) }))
      .filter((entry) => entry.selection?.accepted)
      .sort(
        (a, b) =>
          Number(savedCodes.has(b.code)) - Number(savedCodes.has(a.code)) ||
          Math.abs(selectionPoints(b.selection!)) - Math.abs(selectionPoints(a.selection!)) ||
          a.order - b.order,
      )[0]?.code;
  for (const [first, second] of EXCLUSIVE_CRITERIA) {
    if (!byCode.get(first)?.accepted || !byCode.get(second)?.accepted) continue;
    byCode.get(pick([first, second]) === first ? second : first)!.accepted = false;
  }
  let fitted = [...byCode.values()];
  for (const cap of CAPPED_CRITERIA) {
    const kept = pick(cap.codes);
    if (kept) fitted = fitCappedCriteria(fitted, kept);
  }
  const result = new Map(fitted.map((selection) => [selection.code, selection]));
  return selections.map((selection) => result.get(selection.code) ?? selection);
}

const capPoints = (selections: Map<AcmgCriterionCode, AcmgSelection>, codes: readonly AcmgCriterionCode[]) =>
  codes.reduce((sum, code) => {
    const selection = selections.get(code);
    return sum + (selection?.accepted ? Math.abs(selectionPoints(selection)) : 0);
  }, 0);

// The strongest strength `code` allows that scores no more than `room`, or null.
function strongestWithin(code: AcmgCriterionCode, room: number): AcmgSelection['strength'] | null {
  const fitting = ACMG_CRITERIA_BY_CODE[code].allowedStrengths
    .filter((strength) => STRENGTH_POINTS[strength] > 0 && STRENGTH_POINTS[strength] <= room)
    .sort((a, b) => STRENGTH_POINTS[b] - STRENGTH_POINTS[a]);
  return fitting[0] ?? null;
}

/**
 * The selections with each capped group `kept` is in (CAPPED_CRITERIA) within its points: where
 * the group's checked criteria score more, every one other than `kept` is lowered to the
 * strongest strength it allows that fits, or unchecked where none does. `kept` stays as it is:
 * the criterion the analyst just checked or set.
 */
export function fitCappedCriteria(selections: AcmgSelection[], kept: AcmgCriterionCode): AcmgSelection[] {
  const byCode = new Map(selections.map((selection) => [selection.code, { ...selection }]));
  for (const cap of capsOf(kept)) {
    for (const code of cap.codes) {
      const other = byCode.get(code);
      if (code === kept || !other?.accepted || capPoints(byCode, cap.codes) <= cap.maxPoints) continue;
      const room = cap.maxPoints - (capPoints(byCode, cap.codes) - Math.abs(selectionPoints(other)));
      const strength = strongestWithin(code, room);
      if (strength) other.strength = strength;
      else other.accepted = false;
    }
  }
  return selections.map((selection) => byCode.get(selection.code) ?? selection);
}

/**
 * The checked criteria that keep `code` from being checked as it is: the ones never applied
 * together with it, and those of a capped group that leave it no strength within the cap.
 */
export function criteriaBlocking(
  code: AcmgCriterionCode,
  selections: Readonly<Record<string, AcmgSelection | undefined>>,
): AcmgCriterionCode[] {
  const blocking = exclusivePartners(code)
    .filter((partner) => selections[partner.code]?.accepted)
    .map((partner) => partner.code);
  const byCode = new Map(
    Object.values(selections)
      .filter((selection): selection is AcmgSelection => Boolean(selection))
      .map((selection) => [selection.code, selection]),
  );
  for (const cap of capsOf(code)) {
    const others = cap.codes.filter((other) => other !== code && byCode.get(other)?.accepted);
    if (others.length && !strongestWithin(code, cap.maxPoints - capPoints(byCode, others))) blocking.push(...others);
  }
  return blocking;
}
