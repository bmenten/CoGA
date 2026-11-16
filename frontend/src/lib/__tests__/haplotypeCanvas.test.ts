// The risk haplotype's lane: bracketed by edges in the risk colour (and tinted where the band
// is thinner than its lane), affected told from carrier by solid or dashed edges, never by hue
// alone.

import { describe, expect, it } from 'vitest';

import { drawHaplotypeRiskLane, fillHaplotypeRiskTint, haplotypeRiskPattern } from '../haplotypeCanvas';

type Rect = { fill: string; alpha: number; x: number; y: number; w: number; h: number };

const recordingContext = () => {
  const rects: Rect[] = [];
  const ctx = {
    fillStyle: '',
    globalAlpha: 1,
    save: () => undefined,
    restore: () => undefined,
    fillRect(x: number, y: number, w: number, h: number) {
      rects.push({ fill: String(this.fillStyle), alpha: this.globalAlpha, x, y, w, h });
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

describe('the risk lane', () => {
  it('brackets the lane with a solid edge along its top and its bottom', () => {
    const { ctx, rects } = recordingContext();
    drawHaplotypeRiskLane(ctx, 10, 0, 30, 12, '#c61f2d');
    expect(rects).toEqual([
      { fill: '#c61f2d', alpha: 1, x: 10, y: 0, w: 30, h: 2 },
      { fill: '#c61f2d', alpha: 1, x: 10, y: 10, w: 30, h: 2 },
    ]);
  });

  it('tints the whole lane, light, behind a thin band', () => {
    const { ctx, rects } = recordingContext();
    fillHaplotypeRiskTint(ctx, 0, 12, 20, 12, '#d9822e');
    expect(rects).toEqual([{ fill: '#d9822e', alpha: 0.28, x: 0, y: 12, w: 20, h: 12 }]);
  });

  it('dashes a carrier lane: 7 px on, 3 px off, on a grid of the canvas', () => {
    const { ctx, rects } = recordingContext();
    drawHaplotypeRiskLane(ctx, 0, 0, 25, 12, '#d9822e', { pattern: 'dashed' });
    const top = rects.filter((rect) => rect.y === 0);
    expect(top.map((dash) => [dash.x, dash.w])).toEqual([
      [0, 7],
      [10, 7],
      [20, 5],
    ]);
    // A block that starts mid-dash continues the dash of the block before it.
    const next = recordingContext();
    drawHaplotypeRiskLane(next.ctx, 25, 0, 15, 12, '#d9822e', { pattern: 'dashed' });
    expect(next.rects.filter((rect) => rect.y === 0).map((dash) => [dash.x, dash.w])).toEqual([
      [25, 2],
      [30, 7],
    ]);
  });
});
