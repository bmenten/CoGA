import { render, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import CircosPlot, { type Chromosome } from '../visualizations/CircosPlot';

describe('CircosPlot', () => {
  it('renders glossy band gradients, rounded telomeres, and tapered centromeres', async () => {
    const chromData: Chromosome[] = [
      {
        chr: '1',
        size: 100,
        bands: [
          { name: 'p11', start: 0, end: 30, stain: 'gneg' },
          { name: 'p12', start: 30, end: 45, stain: 'acen' },
          { name: 'q11', start: 45, end: 60, stain: 'acen' },
          { name: 'q12', start: 60, end: 100, stain: 'gpos50' },
        ],
      },
      {
        chr: '2',
        size: 80,
        bands: [
          { name: 'p11', start: 0, end: 30, stain: 'gneg' },
          { name: 'q11', start: 30, end: 80, stain: 'gpos25' },
        ],
      },
    ];

    const { container } = render(
      <CircosPlot chromData={chromData} selected={{ '1': true, '2': true }} variants={[]} />,
    );

    await waitFor(() => {
      expect(container.querySelectorAll('.circos-band--acen')).toHaveLength(2);
    });

    const gradients = container.querySelectorAll('.circos-band-gradient');
    expect(gradients).toHaveLength(6);

    const sectorBands = Array.from(
      container.querySelectorAll<SVGPathElement>('.circos-band--sector'),
    );
    expect(sectorBands).toHaveLength(4);
    expect(sectorBands.every((band) => band.getAttribute('stroke') === 'none')).toBe(true);

    const bandBoundaries = container.querySelectorAll('.circos-band-boundary');
    expect(bandBoundaries).toHaveLength(1);
    expect(container.querySelectorAll('.circos-chromosome-separator')).toHaveLength(0);

    const clipPath = container.querySelector('.circos-chromosome-clip');
    expect(clipPath).not.toBeNull();
    expect(clipPath?.getAttribute('d')).toContain('L');

    const outline = container.querySelector<SVGPathElement>('.circos-chromosome-outline');
    expect(outline).not.toBeNull();
    expect(outline?.getAttribute('d')).toContain('A4.5,4.5');
    expect(outline?.getAttribute('d')?.startsWith('M0,-240')).toBe(false);

    const acenBands = Array.from(
      container.querySelectorAll<SVGPathElement>('.circos-band--acen'),
    );
    expect(acenBands.every((band) => band.getAttribute('fill')?.startsWith('url(#circos-band-gradient-1-'))).toBe(true);
  });
});
