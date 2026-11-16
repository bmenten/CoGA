import { useLayoutEffect, useRef, useState } from 'react';

import { type RankedBy, type SmallVariantPriority } from './smallVariantSearch';
import VariantPriorityBlock from './VariantPriorityBlock';

const VIEWPORT_MARGIN = 8;

type TriggerRect = { top: number; bottom: number; left: number };

/**
 * The table's Score cell with a reliable hover/focus popover showing the priority
 * breakdown. A fixed-positioned popover (rather than a native `title`, which is easy to
 * miss, or a CSS tooltip, which the table's scroll container would clip) so the
 * breakdown is always visible.
 *
 * The popover sizes to its content (no inner scrollbar — a tooltip can't be
 * scrolled); after it renders we measure it and clamp/flip it so it always stays
 * within the viewport.
 *
 * The cell shows the score the page is ranked by (`rankedBy`): the combined score, or the
 * variant score for a page ranked by it, so the column reads in the order of its ranks.
 */
export default function VariantScoreCell({
  variant,
  rankedBy = null,
}: {
  variant: { priority?: SmallVariantPriority | null };
  rankedBy?: RankedBy;
}) {
  const [trigger, setTrigger] = useState<TriggerRect | null>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const priority = variant.priority;

  useLayoutEffect(() => {
    if (!trigger || !popoverRef.current) {
      setPos(null);
      return;
    }
    const { offsetWidth: width, offsetHeight: height } = popoverRef.current;
    const left = Math.max(
      VIEWPORT_MARGIN,
      Math.min(trigger.left, window.innerWidth - width - VIEWPORT_MARGIN),
    );
    // Prefer below the cell; flip above when it would overrun the bottom edge.
    let top = trigger.bottom + 6;
    if (top + height > window.innerHeight - VIEWPORT_MARGIN) {
      const above = trigger.top - height - 6;
      top =
        above >= VIEWPORT_MARGIN
          ? above
          : Math.max(VIEWPORT_MARGIN, window.innerHeight - height - VIEWPORT_MARGIN);
    }
    setPos({ top, left });
  }, [trigger]);

  if (!priority) {
    return <td className="table-mono">—</td>;
  }

  const open = (event: React.MouseEvent | React.FocusEvent) => {
    const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
    setTrigger({ top: rect.top, bottom: rect.bottom, left: rect.left });
  };
  const close = () => {
    setTrigger(null);
    setPos(null);
  };
  const byVariantScore = rankedBy === 'variant_score';
  const shownScore = (byVariantScore ? priority.variant_score : priority.combined_score).toFixed(2);

  return (
    <td className="table-mono variant-score-cell">
      <span
        className="variant-priority-score"
        tabIndex={0}
        role="button"
        aria-label={`${byVariantScore ? 'Variant score' : 'Priority score'} ${shownScore} — hover for the breakdown`}
        onMouseEnter={open}
        onMouseLeave={close}
        onFocus={open}
        onBlur={close}
      >
        {shownScore}
        {priority.phenotype_matches.length ? (
          <span className="variant-priority-pheno" aria-hidden>
            ✦
          </span>
        ) : null}
      </span>
      {trigger ? (
        <div
          ref={popoverRef}
          className="variant-priority-popover"
          role="tooltip"
          // Keep it invisible (but measurable) until positioned to avoid a flash.
          style={pos ? { top: pos.top, left: pos.left } : { top: 0, left: 0, visibility: 'hidden' }}
        >
          <VariantPriorityBlock priority={priority} rankedBy={rankedBy} />
        </div>
      ) : null}
    </td>
  );
}
