import { act, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { useMeasuredWidth } from '../useMeasuredWidth';

/**
 * The viewers size their tracks from this. A link opened in a background tab lays out
 * while hidden, so the observer may never deliver a size — and because the element does
 * not change size once the tab is focused, no later notification arrives either.
 */
const Probe = ({ onWidth }: { onWidth: (width: number) => void }) => {
  const [ref, width] = useMeasuredWidth<HTMLDivElement>();
  onWidth(width);
  return <div ref={ref} data-testid="probe" />;
};

const withBox = (
  node: HTMLElement,
  { width, paddingLeft = '0px', paddingRight = '0px' }: {
    width: number;
    paddingLeft?: string;
    paddingRight?: string;
  },
) => {
  vi.spyOn(node, 'getBoundingClientRect').mockReturnValue({ width } as DOMRect);
  vi.spyOn(window, 'getComputedStyle').mockReturnValue({
    paddingLeft,
    paddingRight,
  } as CSSStyleDeclaration);
};

afterEach(() => {
  vi.restoreAllMocks();
  Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
});

describe('useMeasuredWidth', () => {
  it('reports the content box, not the border box', () => {
    const widths: number[] = [];
    const { getByTestId } = render(<Probe onWidth={(w) => widths.push(w)} />);
    const node = getByTestId('probe');
    withBox(node, { width: 1000, paddingLeft: '23.2px', paddingRight: '23.2px' });

    act(() => {
      window.dispatchEvent(new Event('resize'));
      document.dispatchEvent(new Event('visibilitychange'));
    });

    // The caller subtracts its own inset from this, so padding must not be counted twice
    // — that double count is what made the tracks overflow their panel.
    expect(widths.at(-1)).toBe(954);
  });

  it('re-measures when the tab becomes visible', () => {
    Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true });
    const widths: number[] = [];
    const { getByTestId } = render(<Probe onWidth={(w) => widths.push(w)} />);
    const node = getByTestId('probe');

    // Nothing measurable while hidden: the caller keeps its fallback.
    expect(widths.at(-1)).toBe(0);

    withBox(node, { width: 1800 });
    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
    });

    expect(widths.at(-1)).toBe(1800);
  });

  it('ignores a zero measurement rather than collapsing a known width', () => {
    const widths: number[] = [];
    const { getByTestId } = render(<Probe onWidth={(w) => widths.push(w)} />);
    const node = getByTestId('probe');

    withBox(node, { width: 1400 });
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(widths.at(-1)).toBe(1400);

    // A hidden or detached element measures 0; that is not a new layout.
    withBox(node, { width: 0 });
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(widths.at(-1)).toBe(1400);
  });

  it('stops listening once unmounted', () => {
    const remove = vi.spyOn(document, 'removeEventListener');
    const { unmount } = render(<Probe onWidth={() => {}} />);
    unmount();
    expect(remove).toHaveBeenCalledWith('visibilitychange', expect.any(Function));
  });
});
