import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import CircosPlot, {
  TELOMERE_CORNER_RADIUS,
  type Chromosome,
  type Variant,
} from '../visualizations/CircosPlot';

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
    expect(outline?.getAttribute('d')).toContain(
      `A${TELOMERE_CORNER_RADIUS},${TELOMERE_CORNER_RADIUS}`,
    );
    expect(outline?.getAttribute('d')?.startsWith('M0,-240')).toBe(false);

    const acenBands = Array.from(
      container.querySelectorAll<SVGPathElement>('.circos-band--acen'),
    );
    expect(acenBands.every((band) => band.getAttribute('fill')?.startsWith('url(#circos-band-gradient-1-'))).toBe(true);
  });

  // #529: the plot is a named image whose name counts what it draws.
  const twoChroms: Chromosome[] = [
    { chr: '1', size: 100, bands: [] },
    { chr: '2', size: 80, bands: [] },
  ];

  it('is named with the variants drawn, by type, and the chromosomes they are drawn on (#529)', () => {
    const variants: Variant[] = [
      { chr: '1', start: 10, end: 40, type: 'DEL' },
      { chr: 'chr1', start: 50, end: 60, type: 'dup' },
      { chr: '1', start: 20, type: 'BND', remote_chr: '2', remote_start: 30 },
      // Its partner end is on a chromosome the plot does not draw, so the link is not drawn.
      { chr: '2', start: 5, type: 'BND', remote_chr: '3', remote_start: 10 },
    ];

    const { container, rerender } = render(
      <CircosPlot chromData={twoChroms} selected={{ '1': true, '2': true }} variants={variants} />,
    );
    expect(
      screen.getByRole('img', {
        name: 'Circos plot of 3 structural variants across 2 chromosomes: 1 DEL, 1 DUP, 1 BND',
      }),
    ).toBeInTheDocument();
    expect(container.querySelectorAll('path.circos-variant')).toHaveLength(3);

    // Deselecting chr2 takes the translocation to it along.
    rerender(<CircosPlot chromData={twoChroms} selected={{ '1': true, '2': false }} variants={variants} />);
    expect(
      screen.getByRole('img', {
        name: 'Circos plot of 2 structural variants across 1 chromosome: 1 DEL, 1 DUP',
      }),
    ).toBeInTheDocument();
  });

  it('tells no variants apart from variants not loaded, and says when nothing is selected (#529, #510)', () => {
    const { rerender } = render(
      <CircosPlot chromData={twoChroms} selected={{ '1': true, '2': true }} variants={undefined} />,
    );
    expect(
      screen.getByRole('img', { name: 'Circos plot of 2 chromosomes; structural variants not loaded' }),
    ).toBeInTheDocument();

    rerender(<CircosPlot chromData={twoChroms} selected={{ '1': true, '2': true }} variants={[]} />);
    expect(
      screen.getByRole('img', { name: 'Circos plot of 2 chromosomes: no structural variants' }),
    ).toBeInTheDocument();

    rerender(
      <CircosPlot
        chromData={twoChroms}
        selected={{ '1': false, '2': true }}
        variants={[{ chr: '1', start: 10, end: 40, type: 'DEL' }]}
      />,
    );
    expect(
      screen.getByRole('img', { name: 'Circos plot of 1 chromosome: no structural variants in this selection' }),
    ).toBeInTheDocument();

    rerender(<CircosPlot chromData={twoChroms} selected={{ '1': false, '2': false }} variants={[]} />);
    expect(screen.getByRole('img', { name: 'Circos plot: no chromosomes selected' })).toBeInTheDocument();
  });
});
