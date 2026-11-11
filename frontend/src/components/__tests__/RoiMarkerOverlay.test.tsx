// The region of interest marked over a viewer track (ViewerTrackBlock): where the disorder's
// locus lies on the track.
import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import RoiMarkerOverlay from '../visualizations/RoiMarkerOverlay';

const marker = (container: HTMLElement) => container.querySelector<HTMLElement>('.roi-marker-overlay');

describe('RoiMarkerOverlay', () => {
  it('marks the ROI between its two edges, whichever comes first, with its title', () => {
    const { container, rerender } = render(<RoiMarkerOverlay startX={40} endX={140} title="ROI: chr7:30,500,000–30,600,000" />);
    expect(marker(container)?.style.left).toBe('40px');
    expect(marker(container)?.style.width).toBe('100px');
    expect(marker(container)).toHaveAttribute('title', 'ROI: chr7:30,500,000–30,600,000');

    rerender(<RoiMarkerOverlay startX={140} endX={40} />);
    expect(marker(container)?.style.left).toBe('40px');
    expect(marker(container)?.style.width).toBe('100px');
  });

  it('keeps an ROI narrower than 2 px visible at 2 px', () => {
    const { container } = render(<RoiMarkerOverlay startX={70} endX={70.5} />);
    expect(marker(container)?.style.left).toBe('70px');
    expect(marker(container)?.style.width).toBe('2px');
  });

  it.each([
    ['no start', null, 140],
    ['no end', 40, null],
    ['a start that is not a number', Number.NaN, 140],
    ['an end that is not a number', 40, Number.NaN],
  ])('marks nothing with %s', (_label, startX, endX) => {
    const { container } = render(<RoiMarkerOverlay startX={startX} endX={endX} />);
    expect(marker(container)).toBeNull();
  });
});
