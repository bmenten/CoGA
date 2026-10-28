import React, { useCallback, useState } from 'react';
import { READ_ONLY_HINT } from '../../lib/familyAccess';

/**
 * An excluded variant's card folds to its heading and this bar: that it is excluded, a button
 * that shows the whole card again, and one that takes the exclusion back. The variant stays in
 * the list, so an excluded finding is never out of sight, only out of the way.
 */
const ExcludedCardBar: React.FC<{
  expanded: boolean;
  onToggleExpanded: () => void;
  onUndoExclude: () => void;
  readOnly?: boolean;
  pending?: boolean;
  /** What the buttons are for, read by a screen reader: the variant's name. */
  subject: string;
}> = ({ expanded, onToggleExpanded, onUndoExclude, readOnly = false, pending = false, subject }) => (
  <div className="variant-card-excluded-bar">
    <span className="variant-card-chip variant-card-chip--neutral">Excluded</span>
    <button
      type="button"
      className="variant-review-link"
      aria-expanded={expanded}
      aria-label={`${expanded ? 'Hide' : 'Show'} the excluded card of ${subject}`}
      onClick={onToggleExpanded}
    >
      {expanded ? 'Hide card' : 'Show card'}
    </button>
    <button
      type="button"
      className="variant-review-link"
      disabled={readOnly || pending}
      title={readOnly ? READ_ONLY_HINT : undefined}
      aria-label={`Undo the exclusion of ${subject}`}
      onClick={onUndoExclude}
    >
      Undo exclude
    </button>
  </div>
);

/** Which excluded cards the analyst unfolded, by variant: all fold again on a new list. */
export const useUnfoldedExcludedCards = () => {
  const [unfolded, setUnfolded] = useState<Set<string>>(() => new Set());
  const toggle = useCallback((id: string) => {
    setUnfolded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);
  return { isUnfolded: (id: string) => unfolded.has(id), toggle };
};

export default ExcludedCardBar;
