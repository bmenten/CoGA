// The key to the small-variant marks — #529: the track had no legend, and its classes
// differed by colour only.

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import HaplotypeLegend from '../visualizations/HaplotypeLegend';
import SmallVariantLegend from '../visualizations/SmallVariantLegend';
import { SMALL_VARIANT_MARKS, SMALL_VARIANT_MARK_ORDER, smallVariantMarkPath } from '../../lib/smallVariantMarks';

describe('SmallVariantLegend', () => {
  it('names every mark, with the shape the track draws', () => {
    const { container } = render(<SmallVariantLegend />);
    const legend = screen.getByRole('group', { name: 'Small-variant marks' });
    SMALL_VARIANT_MARK_ORDER.forEach((kind) => {
      expect(legend).toHaveTextContent(SMALL_VARIANT_MARKS[kind].label);
      const swatch = container.querySelector(`[data-legend-mark="${kind}"] path`);
      expect(swatch?.getAttribute('d')).toBe(smallVariantMarkPath(kind, 2.4));
    });
    expect(legend).toHaveTextContent('Ring: review tag');
  });
});

describe('HaplotypeLegend', () => {
  it('shows a recessive carrier line dashed and an affected line solid (#529)', () => {
    const { container, rerender } = render(<HaplotypeLegend inheritanceModel="recessive" />);
    const carrier = Array.from(container.querySelectorAll<HTMLElement>('.haplotype-legend-swatch')).at(-1);
    expect(carrier?.style.borderBottomStyle).toBe('dashed');

    rerender(<HaplotypeLegend inheritanceModel="dominant" />);
    const affected = Array.from(container.querySelectorAll<HTMLElement>('.haplotype-legend-swatch')).at(-1);
    expect(affected?.style.borderBottomStyle).toBe('solid');
  });
});
