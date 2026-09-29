// The haplotype risk line — #529: it was a red or orange line told from the band, and
// affected from carrier, by hue alone.

import { describe, expect, it } from 'vitest';

import { drawHaplotypeRiskOverlay, haplotypeRiskPattern } from '../haplotypeCanvas';

type Rect = { fill: string; x: number; y: number; w: number; h: number };

const recordingContext = () => {
  const rects: Rect[] = [];
  const ctx = {
    fillStyle: '',
    save: () => undefined,
    restore: () => undefined,
    fillRect(x: number, y: number, w: number, h: number) {
      rects.push({ fill: String(this.fillStyle), x, y, w, h });
    },
  };
  return { ctx: ctx as unknown as CanvasRenderingContext2D, rects };
};

describe('haplotypeRiskPattern', () => {
  it('dashes a recessive carrier haplotype and keeps an affected one solid', () => {
    expect(haplotypeRiskPattern('recessive-maternal')).toBe('dashed');
    expect(haplotypeRiskPattern('recessive-paternal')).toBe('dashed');
    expect(haplotypeRiskPattern('dominant')).toBe('solid');
    expect(haplotypeRiskPattern('x-linked')).toBe('solid');
  });
});

describe('drawHaplotypeRiskOverlay', () => {
  it('sets a solid line off from the band with a light gap along its bottom edge', () => {
    const { ctx, rects } = recordingContext();
    drawHaplotypeRiskOverlay(ctx, 10, 0, 30, 9, '#c61f2d');
    expect(rects).toEqual([
      { fill: 'rgba(255, 255, 255, 0.85)', x: 10, y: 5.5, w: 30, h: 1 },
      { fill: '#c61f2d', x: 10, y: 6.5, w: 30, h: 2.5 },
    ]);
  });

  it('dashes a carrier line: 4 px on, 2 px off, clipped at the band end', () => {
    const { ctx, rects } = recordingContext();
    drawHaplotypeRiskOverlay(ctx, 0, 0, 15, 9, '#d9822e', { pattern: 'dashed' });
    const dashes = rects.filter((rect) => rect.fill === '#d9822e');
    expect(dashes.map((dash) => [dash.x, dash.w])).toEqual([
      [0, 4],
      [6, 4],
      [12, 3],
    ]);
  });

  it('puts the line below a thin band, which keeps its whole colour', () => {
    const { ctx, rects } = recordingContext();
    drawHaplotypeRiskOverlay(ctx, 0, 8, 20, 4, '#c61f2d', { placement: 'below' });
    // No light gap is painted over the band: the track background shows between.
    expect(rects).toEqual([{ fill: '#c61f2d', x: 0, y: 13, w: 20, h: 2 }]);
  });
});
