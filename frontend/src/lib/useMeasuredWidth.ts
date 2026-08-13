import { useEffect, useRef, useState } from 'react';

/**
 * The observed element's content width.
 *
 * Reports the **content box**, so the caller does not have to guess at the element's own
 * padding, and re-measures when the document becomes visible again. That second part
 * matters for a link opened in a background tab: the tab lays out while hidden, the
 * ResizeObserver callback is throttled and may never deliver a size, and because the
 * element never actually *changes* size once the tab is focused there is no later
 * notification either. Without the visibility check the caller keeps whatever fallback
 * it started with — for the genome and chromosome viewers a fixed 1200px, well under the
 * real width on a wide screen.
 */
export const useMeasuredWidth = <T extends HTMLElement>() => {
  const ref = useRef<T | null>(null);
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;

    const update = () => {
      const style = window.getComputedStyle(node);
      const horizontalPadding =
        parseFloat(style.paddingLeft || '0') + parseFloat(style.paddingRight || '0');
      const next = Math.round(node.getBoundingClientRect().width - horizontalPadding);
      // A hidden tab can measure 0; keep the last real width rather than collapsing.
      if (next > 0) setWidth(next);
    };

    update();

    const onVisible = () => {
      if (document.visibilityState === 'visible') update();
    };
    document.addEventListener('visibilitychange', onVisible);

    let observer: ResizeObserver | undefined;
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(() => update());
      observer.observe(node);
    } else {
      window.addEventListener('resize', update);
    }

    return () => {
      document.removeEventListener('visibilitychange', onVisible);
      observer?.disconnect();
      if (!observer) window.removeEventListener('resize', update);
    };
  }, []);

  return [ref, width] as const;
};
