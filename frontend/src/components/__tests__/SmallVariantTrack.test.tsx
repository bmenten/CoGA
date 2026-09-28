import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';

const { apiGetMock, useQueryMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  useQueryMock: vi.fn(),
}));

vi.mock('@tanstack/react-query', () => ({
  useQuery: useQueryMock,
  keepPreviousData: (previous: unknown) => previous,
}));

vi.mock('../../lib/api', () => ({
  default: {
    get: apiGetMock,
  },
}));
import SmallVariantTrack from '../visualizations/SmallVariantTrack';

beforeEach(() => {
  apiGetMock.mockReset();
  useQueryMock.mockReset();
});

// The drawn marks in position order (they are drawn by salience, not position).
const drawnMarks = (container: HTMLElement) =>
  Array.from(container.querySelectorAll<SVGPathElement>('path.small-variant-mark'))
    .map((mark) => {
      const [, x, y] = /translate\(([-\d.]+),([-\d.]+)\)/.exec(mark.getAttribute('transform') || '') ?? [];
      return {
        kind: mark.getAttribute('data-variant-mark'),
        fill: mark.getAttribute('fill'),
        stroke: mark.getAttribute('stroke'),
        x: Number(x),
        y: Number(y),
      };
    })
    .sort((a, b) => a.x - b.x);

test('renders message when no small variants', () => {
  useQueryMock.mockReturnValue({ data: { variants: [] }, isLoading: false });
  render(
    <SmallVariantTrack
      familyId="F1"
      sampleId="S1"
      chrom="1"
      regionStart={0}
      regionEnd={100}
      width={100}
      height={20}
    />
  );
  expect(
    screen.getByText(/no small variants for this region \/ sample/i)
  ).toBeInTheDocument();
});

test('renders loader while small variants are loading', () => {
  useQueryMock.mockReturnValue({ data: undefined, isLoading: true });
  render(
    <SmallVariantTrack
      familyId="F1"
      sampleId="S1"
      chrom="1"
      regionStart={0}
      regionEnd={100}
      width={100}
      height={20}
    />
  );
  expect(screen.getByText(/loading small variants/i)).toBeInTheDocument();
});

test('requests broad regions and shows too many when over the cap (no span gate)', () => {
  useQueryMock.mockImplementation(({ queryKey }) =>
    queryKey[0] === 'small-variant-track-tags'
      ? { data: [], isLoading: false }
      : {
          data: { total: 10000, total_is_estimated: true, count_limit: 10000, variants: [] },
          isLoading: false,
        },
  );

  render(
    <SmallVariantTrack
      familyId="F1"
      sampleId="S1"
      chrom="1"
      regionStart={0}
      regionEnd={10_000_000}
      width={100}
      height={20}
    />
  );

  // The span no longer gates the request — it fires and the variant cap governs.
  expect(useQueryMock.mock.calls[0][0].enabled).toBe(true);
  expect(screen.getByText(/too many variants to display/i)).toBeInTheDocument();
});

test('renders too many message when bounded track count reaches the limit', () => {
  useQueryMock.mockImplementation(({ queryKey }) =>
    queryKey[0] === 'small-variant-track-tags'
      ? { data: [], isLoading: false }
      : {
          data: {
            total: 10000,
            total_is_estimated: true,
            count_limit: 10000,
            variants: [],
          },
          isLoading: false,
        },
  );

  render(
    <SmallVariantTrack
      familyId="F1"
      sampleId="S1"
      chrom="1"
      regionStart={0}
      regionEnd={100}
      width={100}
      height={20}
    />,
  );

  expect(screen.getByText(/too many variants to display/i)).toBeInTheDocument();
});

test('requests small variants carried by the displayed sample before pagination', async () => {
  useQueryMock.mockReturnValue({ data: { variants: [] }, isLoading: false });
  apiGetMock.mockResolvedValue({ data: { variants: [] } });

  render(
    <SmallVariantTrack
      familyId="F1"
      sampleId="S1"
      chrom="1"
      regionStart={0}
      regionEnd={100}
      width={100}
      height={20}
    />
  );

  await useQueryMock.mock.calls[0][0].queryFn();

  expect(apiGetMock).toHaveBeenCalledWith(
    '/families/F1/small-variants',
    expect.objectContaining({
      params: expect.objectContaining({
        sample_filter: 'S1:het|hom',
        page_size: 9999,
        track_result_limit: 10000,
      }),
    }),
  );
});

test('preserves an explicit small-variant sample filter', async () => {
  useQueryMock.mockReturnValue({ data: { variants: [] }, isLoading: false });
  apiGetMock.mockResolvedValue({ data: { variants: [] } });

  render(
    <SmallVariantTrack
      familyId="F1"
      sampleId="S1"
      chrom="1"
      regionStart={0}
      regionEnd={100}
      width={100}
      height={20}
      filters={{ sample_filter: 'S1:1/1', source: 'glimpse2' }}
    />
  );

  await useQueryMock.mock.calls[0][0].queryFn();

  expect(apiGetMock).toHaveBeenCalledWith(
    '/families/F1/small-variants',
    expect.objectContaining({
      params: expect.objectContaining({
        sample_filter: 'S1:1/1',
        source: 'glimpse2',
        page_size: 9999,
        track_result_limit: 10000,
      }),
    }),
  );
});

test('rings a tagged variant in the tag colour and keeps its ClinVar mark (#529)', async () => {
  useQueryMock.mockImplementation(({ queryKey }) =>
    queryKey[0] === 'small-variant-track-tags'
      ? { data: [{ key: 'priority', color: '#123456' }], isLoading: false }
      : {
          data: {
            total: 2,
            variants: [
              {
                chr: '1',
                start: 10,
                end: 10,
                type: 'SNV',
                clinvar: 'Pathogenic',
                genotypes: [{ sample: 'S1', gt: '0/1' }],
                review: { tags: ['priority'] },
              },
              {
                chr: '1',
                start: 20,
                end: 20,
                type: 'SNV',
                clinvar: 'Pathogenic',
                genotypes: [{ sample: 'S1', gt: '0/1' }],
                review: { tags: [] },
              },
            ],
          },
          isLoading: false,
        },
  );

  const { container } = render(
    <SmallVariantTrack
      familyId="F1"
      sampleId="S1"
      chrom="1"
      regionStart={0}
      regionEnd={100}
      width={100}
      height={20}
    />,
  );

  await waitFor(() => expect(drawnMarks(container)).toHaveLength(2));
  // A tag used to replace the fill, hiding that the variant is pathogenic.
  expect(drawnMarks(container).map((mark) => [mark.kind, mark.fill])).toEqual([
    ['pathogenic', '#dc2626'],
    ['pathogenic', '#dc2626'],
  ]);
  const rings = container.querySelectorAll('circle.small-variant-tag-ring');
  expect(rings).toHaveLength(1);
  expect(rings[0]).toHaveAttribute('stroke', '#123456');
  expect(rings[0]).toHaveAttribute('fill', 'none');
  expect(rings[0]).toHaveAttribute('cx', String(drawnMarks(container)[0].x));
});

test('colors ClinVar benign/pathogenic; other ClinVar falls through to impact', async () => {
  useQueryMock.mockImplementation(({ queryKey }) =>
    queryKey[0] === 'small-variant-track-tags'
      ? { data: [], isLoading: false }
      : {
          data: {
            total: 3,
            variants: [
              {
                chr: '1',
                start: 10,
                end: 10,
                type: 'SNV',
                clinvar: 'Likely pathogenic',
                genotypes: [{ sample: 'S1', gt: '0/1' }],
              },
              {
                chr: '1',
                start: 20,
                end: 20,
                type: 'SNV',
                clinvar: 'Likely benign',
                genotypes: [{ sample: 'S1', gt: '0/1' }],
              },
              {
                chr: '1',
                start: 30,
                end: 30,
                type: 'SNV',
                clinvar: 'Conflicting interpretations of pathogenicity',
                genotypes: [{ sample: 'S1', gt: '0/1' }],
              },
            ],
          },
          isLoading: false,
        },
  );

  const { container } = render(
    <SmallVariantTrack
      familyId="F1"
      sampleId="S1"
      chrom="1"
      regionStart={0}
      regionEnd={100}
      width={100}
      height={20}
    />,
  );

  await waitFor(() => expect(drawnMarks(container)).toHaveLength(3));
  const [likelyPathogenic, likelyBenign, conflicting] = drawnMarks(container);
  // Shape carries the ClinVar class as well as colour (#529): a diamond for P/LP, a
  // hollow square for B/LB.
  expect(likelyPathogenic).toMatchObject({ kind: 'pathogenic', fill: '#dc2626' });
  expect(likelyBenign).toMatchObject({ kind: 'benign', fill: 'white', stroke: '#60a5fa' });
  // "Conflicting" is not benign/pathogenic → marked by impact (none → a grey dot).
  expect(conflicting).toMatchObject({ kind: 'other', fill: '#9ca3af' });
});

test('colors variants by functional impact when no ClinVar override applies', async () => {
  useQueryMock.mockImplementation(({ queryKey }) =>
    queryKey[0] === 'small-variant-track-tags'
      ? { data: [], isLoading: false }
      : {
          data: {
            total: 4,
            variants: [
              { chr: '1', start: 10, end: 10, type: 'SNV', impact: 'HIGH', genotypes: [{ sample: 'S1', gt: '0/1' }] },
              { chr: '1', start: 20, end: 20, type: 'SNV', impact: 'MODERATE', genotypes: [{ sample: 'S1', gt: '0/1' }] },
              { chr: '1', start: 30, end: 30, type: 'SNV', impact: 'LOW', genotypes: [{ sample: 'S1', gt: '0/1' }] },
              { chr: '1', start: 40, end: 40, type: 'SNV', impact: 'MODIFIER', genotypes: [{ sample: 'S1', gt: '0/1' }] },
            ],
          },
          isLoading: false,
        },
  );

  const { container } = render(
    <SmallVariantTrack familyId="F1" sampleId="S1" chrom="1" regionStart={0} regionEnd={100} width={100} height={20} />,
  );

  await waitFor(() => expect(drawnMarks(container)).toHaveLength(4));
  const [high, moderate, low, modifier] = drawnMarks(container);
  expect(high).toMatchObject({ kind: 'high', fill: '#fb923c' }); // orange triangle
  expect(moderate).toMatchObject({ kind: 'moderate', fill: '#4ade80' }); // green dot
  expect(low).toMatchObject({ kind: 'other', fill: '#9ca3af' }); // grey dot
  expect(modifier).toMatchObject({ kind: 'other', fill: '#9ca3af' }); // grey dot (lowest)
  // The salient marks are drawn last, so dense low-impact dots cannot cover them.
  const drawOrder = Array.from(container.querySelectorAll('path.small-variant-mark')).map((mark) =>
    mark.getAttribute('data-variant-mark'),
  );
  expect(drawOrder).toEqual(['other', 'other', 'moderate', 'high']);
});

test('ClinVar pathogenic overrides functional impact', async () => {
  useQueryMock.mockImplementation(({ queryKey }) =>
    queryKey[0] === 'small-variant-track-tags'
      ? { data: [], isLoading: false }
      : {
          data: {
            total: 1,
            variants: [
              {
                chr: '1',
                start: 10,
                end: 10,
                type: 'SNV',
                impact: 'LOW',
                clinvar: 'Pathogenic',
                genotypes: [{ sample: 'S1', gt: '0/1' }],
              },
            ],
          },
          isLoading: false,
        },
  );

  const { container } = render(
    <SmallVariantTrack familyId="F1" sampleId="S1" chrom="1" regionStart={0} regionEnd={100} width={100} height={20} />,
  );

  await waitFor(() => expect(drawnMarks(container)).toHaveLength(1));
  expect(drawnMarks(container)[0]).toMatchObject({ kind: 'pathogenic', fill: '#dc2626' });
});

test('splits variants into parental-origin rows when parents are provided', async () => {
  useQueryMock.mockImplementation(({ queryKey }) =>
    queryKey[0] === 'small-variant-track-tags'
      ? { data: [], isLoading: false }
      : {
          data: {
            total: 4,
            variants: [
              {
                // ALT only in father → paternal (top row).
                chr: '1', start: 10, end: 10, type: 'SNV',
                genotypes: [
                  { sample: 'C', gt: '0/1' },
                  { sample: 'F', gt: '0/1' },
                  { sample: 'M', gt: '0/0' },
                ],
              },
              {
                // ALT only in mother → maternal (bottom row).
                chr: '1', start: 20, end: 20, type: 'SNV',
                genotypes: [
                  { sample: 'C', gt: '0/1' },
                  { sample: 'F', gt: '0/0' },
                  { sample: 'M', gt: '0/1' },
                ],
              },
              {
                // Homozygous-ALT in the child → undetermined (middle row).
                chr: '1', start: 30, end: 30, type: 'SNV',
                genotypes: [
                  { sample: 'C', gt: '1/1' },
                  { sample: 'F', gt: '0/1' },
                  { sample: 'M', gt: '0/1' },
                ],
              },
              {
                // Phased de novo with both parents present → Mendelian is
                // authoritative (neither parent carries) → middle row, not the
                // hap2 the phase order would otherwise imply.
                chr: '1', start: 40, end: 40, type: 'SNV',
                genotypes: [
                  { sample: 'C', gt: '0|1' },
                  { sample: 'F', gt: '0/0' },
                  { sample: 'M', gt: '0/0' },
                ],
              },
            ],
          },
          isLoading: false,
        },
  );

  const { container } = render(
    <SmallVariantTrack
      familyId="F1"
      sampleId="C"
      chrom="1"
      regionStart={0}
      regionEnd={100}
      width={100}
      height={45}
      paternalSampleId="F"
      maternalSampleId="M"
    />,
  );

  await waitFor(() => expect(drawnMarks(container)).toHaveLength(4));
  const [paternal, maternal, homAlt, deNovo] = drawnMarks(container);
  // 3 rows over height 45 → row centres at 7.5 / 22.5 / 37.5.
  expect(paternal.y).toBe(7.5);
  expect(maternal.y).toBe(37.5);
  expect(homAlt.y).toBe(22.5);
  expect(deNovo.y).toBe(22.5);
});

test('shows a hover tooltip with the gene and variant', async () => {
  useQueryMock.mockImplementation(({ queryKey }) =>
    queryKey[0] === 'small-variant-track-tags'
      ? { data: [], isLoading: false }
      : {
          data: {
            total: 1,
            variants: [
              {
                chr: '13',
                start: 32316461,
                end: 32316461,
                type: 'SNV',
                ref: 'G',
                alt: 'A',
                gene: 'BRCA2',
                hgvsc: 'c.7007G>A',
                hgvsp: 'p.Arg2336His',
                genotypes: [{ sample: 'S1', gt: '0/1' }],
              },
            ],
          },
          isLoading: false,
        },
  );

  const { container } = render(
    <SmallVariantTrack
      familyId="F1"
      sampleId="S1"
      chrom="13"
      regionStart={32316000}
      regionEnd={32317000}
      width={100}
      height={20}
    />,
  );

  // Each marker gets a transparent hover hitbox rect.
  await waitFor(() => expect(container.querySelectorAll('rect').length).toBeGreaterThan(0));
  fireEvent.mouseMove(container.querySelector('rect') as Element, { clientX: 40, clientY: 12 });

  const tooltip = document.body.querySelector('.viz-tooltip');
  expect(tooltip).not.toBeNull();
  expect(tooltip).toHaveClass('viz-tooltip--floating');
  expect(tooltip?.textContent).toContain('BRCA2');
  expect(tooltip?.textContent).toContain('13:32316461');
  expect(tooltip?.textContent).toContain('G>A');
  expect(tooltip?.textContent).toContain('c.7007G>A');
});

test('names the review tags in the tooltip, since a ring gives only a colour (#529)', async () => {
  useQueryMock.mockImplementation(({ queryKey }) =>
    queryKey[0] === 'small-variant-track-tags'
      ? { data: [{ key: 'priority', label: 'Priority', color: '#123456' }], isLoading: false }
      : {
          data: {
            total: 1,
            variants: [
              {
                chr: '13',
                start: 32316461,
                end: 32316461,
                type: 'SNV',
                gene: 'BRCA2',
                genotypes: [{ sample: 'S1', gt: '0/1' }],
                review: { tags: ['priority', 'unknown_tag'] },
              },
            ],
          },
          isLoading: false,
        },
  );

  const { container } = render(
    <SmallVariantTrack
      familyId="F1"
      sampleId="S1"
      chrom="13"
      regionStart={32316000}
      regionEnd={32317000}
      width={100}
      height={20}
    />,
  );

  await waitFor(() => expect(container.querySelectorAll('rect').length).toBeGreaterThan(0));
  fireEvent.mouseMove(container.querySelector('rect') as Element, { clientX: 40, clientY: 12 });
  expect(document.body.querySelector('.viz-tooltip')?.textContent).toContain(
    'Tags: Priority, unknown_tag',
  );
});

test('a failed request shows the failure, never "no small variants" (#510)', () => {
  const refetch = vi.fn();
  useQueryMock.mockReturnValue({ data: undefined, isLoading: false, isError: true, refetch });
  render(
    <SmallVariantTrack
      familyId="F1"
      sampleId="S1"
      chrom="1"
      regionStart={0}
      regionEnd={100}
      width={100}
      height={20}
    />
  );

  expect(
    screen.getByText(/Could not load small variants — this is not an empty result/),
  ).toBeInTheDocument();
  expect(screen.queryByText(/no small variants for this region/i)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(refetch).toHaveBeenCalled();
});
