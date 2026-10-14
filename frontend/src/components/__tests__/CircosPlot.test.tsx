import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
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

    // Past the backend's cap none is drawn, and the name says why (#589).
    rerender(<CircosPlot chromData={twoChroms} selected={{ '1': true, '2': true }} tooManyVariants />);
    expect(
      screen.getByRole('img', { name: 'Circos plot of 2 chromosomes; too many structural variants to draw' }),
    ).toBeInTheDocument();
  });

  // CLIN-54: the plot drew a DEL:ME, a DUP:TANDEM or a CNV as a type it did not know, thin and
  // grey, and a TRA as a plain link, not as the breakend it is.
  describe('draws an SV as the SV tracks do (CLIN-54)', () => {
    const colours = {
      '--color-variant-del': 'rgb(141, 63, 68)',
      '--color-variant-dup': 'rgb(85, 111, 135)',
      '--color-variant-bnd': 'rgb(68, 98, 94)',
      '--color-variant-default': 'rgb(128, 128, 128)',
    };
    afterEach(() => {
      Object.keys(colours).forEach((name) => document.documentElement.style.removeProperty(name));
    });

    it('a DEL:ME and a CNV loss as deletions, a DUP:TANDEM as a duplication, a TRA as a breakend and any other type as other', () => {
      Object.entries(colours).forEach(([name, value]) => document.documentElement.style.setProperty(name, value));
      const variants: Variant[] = [
        { chr: '1', start: 10, end: 20, type: 'DEL:ME', copy_change: 'loss' },
        { chr: '1', start: 30, end: 40, type: 'CNV', copy_change: 'loss' },
        { chr: '1', start: 50, end: 60, type: 'DUP:TANDEM', copy_change: 'gain' },
        { chr: '1', start: 70, type: 'TRA', remote_chr: '2', remote_start: 30 },
        // A CNV that keeps its carriers' copies, and a type no row names.
        { chr: '2', start: 10, end: 20, type: 'CNV' },
        { chr: '2', start: 40, end: 50, type: 'CPX' },
      ];
      const onVariantClick = vi.fn();
      const { container } = render(
        <CircosPlot
          chromData={twoChroms}
          selected={{ '1': true, '2': true }}
          variants={variants}
          onVariantClick={onVariantClick}
        />,
      );

      expect(
        screen.getByRole('img', {
          name: 'Circos plot of 6 structural variants across 2 chromosomes: 2 DEL, 1 DUP, 1 BND, 2 other',
        }),
      ).toBeInTheDocument();
      const links = [...container.querySelectorAll<SVGPathElement>('path.circos-variant')];
      expect(links.map((link) => [link.getAttribute('stroke'), link.getAttribute('stroke-width')])).toEqual([
        [colours['--color-variant-del'], '15'],
        [colours['--color-variant-del'], '15'],
        [colours['--color-variant-dup'], '15'],
        [colours['--color-variant-bnd'], '1'],
        [colours['--color-variant-default'], '1'],
        [colours['--color-variant-default'], '1'],
      ]);
      // A breakend opens its two chromosomes; the TRA is one.
      fireEvent.click(links[3]);
      expect(onVariantClick).toHaveBeenCalledWith(variants[3]);
      fireEvent.click(links[5]);
      expect(onVariantClick).toHaveBeenCalledTimes(1);
      // The legend names the neutral colour too.
      expect(screen.getByText('Other')).toBeInTheDocument();
    });

    it('names a CNV in its tooltip with the copy change it is drawn by', () => {
      const { container } = render(
        <CircosPlot
          chromData={twoChroms}
          selected={{ '1': true, '2': true }}
          variants={[{ chr: '1', start: 30, end: 40, type: 'cnv', copy_change: 'gain' }]}
        />,
      );
      fireEvent.mouseOver(container.querySelector('path.circos-variant') as Element);
      expect(document.querySelector('.circos-tooltip strong')?.textContent).toBe('CNV (copy-number gain)');
    });
  });
});
