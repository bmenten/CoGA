import React, { useCallback, useState } from 'react';

/**
 * The caret before a variant card's heading that folds the card to that heading and opens it
 * again. An analyst going through the list one card at a time folds each card when done with it,
 * so the next card to look at is the first open one. The fold is the analyst's view of the list
 * only: it changes no review, is saved nowhere, and every card opens again when the page is
 * loaded anew.
 */
const CardCollapseToggle: React.FC<{
  collapsed: boolean;
  onToggle: () => void;
  /** What the button is for, read by a screen reader: the variant's name. */
  subject: string;
}> = ({ collapsed, onToggle, subject }) => (
  <button
    type="button"
    className="variant-card-collapse-toggle"
    aria-expanded={!collapsed}
    aria-label={`${collapsed ? 'Expand' : 'Collapse'} the card of ${subject}`}
    title={collapsed ? 'Expand the card' : 'Collapse the card'}
    onClick={onToggle}
  >
    <span className="variant-card-collapse-caret" aria-hidden="true">
      ▾
    </span>
  </button>
);

/** Which cards the analyst collapsed, by variant or pair. */
export const useCollapsedCards = () => {
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());
  const toggle = useCallback((id: string) => {
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);
  return { isCollapsed: (id: string) => collapsed.has(id), toggle };
};

/**
 * A card folded from its foot shrinks to its heading above the reader: that heading is brought
 * back into view once the fold is drawn, with the next card under it.
 */
export const keepCardHeadingInView = (card: Element | null) => {
  window.requestAnimationFrame(() => {
    if (card && typeof card.scrollIntoView === 'function') card.scrollIntoView({ block: 'nearest' });
  });
};

export default CardCollapseToggle;
