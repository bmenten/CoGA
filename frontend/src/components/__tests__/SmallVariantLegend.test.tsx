// The key to the small-variant marks: the track's legend, with the shape and colour the track
// draws for each class.

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import HaplotypeLegend from '../visualizations/HaplotypeLegend';
import SmallVariantLegend from '../visualizations/SmallVariantLegend';
import { SMALL_VARIANT_MARKS, SMALL_VARIANT_MARK_ORDER, smallVariantMarkPath } from '../../lib/smallVariantMarks';

describe('SmallVariantLegend', () => {
  it('names every mark, with the shape and colour the track draws', () => {
    const { container } = render(<SmallVariantLegend />);
    const legend = screen.getByRole('group', { name: 'Small-variant marks' });
    SMALL_VARIANT_MARK_ORDER.forEach((kind) => {
      expect(legend).toHaveTextContent(SMALL_VARIANT_MARKS[kind].label);
      const swatch = container.querySelector(`[data-legend-mark="${kind}"] path`);
      expect(swatch?.getAttribute('d')).toBe(smallVariantMarkPath(kind, 3.6));
      expect(swatch?.getAttribute('fill')).toBe(SMALL_VARIANT_MARKS[kind].color);
    });
    expect(legend).toHaveTextContent('Ring: review tag');
  });
});

describe('HaplotypeLegend', () => {
  it('shows a risk lane bracketed by edges, dashed for a recessive carrier and solid for an affected one', () => {
    const { container, rerender } = render(<HaplotypeLegend inheritanceModel="recessive" />);
    const carrier = Array.from(container.querySelectorAll<HTMLElement>('.haplotype-legend-swatch')).at(-1);
    expect(carrier?.parentElement).toHaveTextContent('Carrier');
    expect(carrier?.style.borderTopStyle).toBe('dashed');
    expect(carrier?.style.borderBottomStyle).toBe('dashed');

    rerender(<HaplotypeLegend inheritanceModel="dominant" />);
    const affected = Array.from(container.querySelectorAll<HTMLElement>('.haplotype-legend-swatch')).at(-1);
    expect(affected?.parentElement).toHaveTextContent('Affected');
    expect(affected?.style.borderTopStyle).toBe('solid');
    expect(affected?.style.borderBottomStyle).toBe('solid');
  });
});
