import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { describe, expect, it, vi, type Mock } from 'vitest';
import CircosPlotPage from '../CircosPlotPage';
import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

const { circosSpy, mockedChroms } = vi.hoisted(() => ({
  circosSpy: vi.fn(),
  mockedChroms: [...Array.from({ length: 22 }, (_, index) => String(index + 1)), 'X', 'Y'],
}));

vi.mock('../../../lib/api', () => ({
  default: {
    get: vi.fn(),
    defaults: {
      baseURL: 'http://test-api',
    },
  },
}));

vi.mock('../../../components/visualizations/CircosPlot', () => ({
  default: (props: any) => {
    circosSpy(props);
    const firstChrom = props.chromData[0];
    return (
      <div data-testid="circos-plot">
        {firstChrom?.chr}:{firstChrom?.bands?.length ?? 0}
      </div>
    );
  },
  CHROMS: mockedChroms,
}));

describe('CircosPlotPage', () => {
  it('loads chromosome ideogram bands for the circos plot', async () => {
    (api.get as unknown as Mock).mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({ data: { projects: [] } });
      }
      if (url === '/chromosomes/GRCh38/details') {
        return Promise.resolve({
          data: [
            {
              chr: 'chr1',
              size: 248956422,
              bands: [{ name: 'p36.33', start: 0, end: 2300000, stain: 'gneg' }],
            },
          ],
        });
      }
      if (url === '/families/F1/structural-variants?page_size=0') {
        return Promise.resolve({ data: { variants: [] } });
      }
      return Promise.resolve({ data: {} });
    });

    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1/circos']}>
          <Routes>
            <Route path="/families/:familyId/circos" element={<CircosPlotPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByTestId('circos-plot')).toHaveTextContent('1:1'));

    expect((api.get as unknown as Mock).mock.calls.map(([url]) => String(url))).toContain(
      '/chromosomes/GRCh38/details',
    );

    const renderedChromosomes = circosSpy.mock.calls.at(-1)?.[0].chromData;
    expect(renderedChromosomes).toEqual([
      {
        chr: '1',
        size: 248956422,
        bands: [{ name: 'p36.33', start: 0, end: 2300000, stain: 'gneg' }],
      },
    ]);
  });

  it('preserves project scope in the structural-variant circos query', async () => {
    (api.get as unknown as Mock).mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({ data: { projects: ['p1'] } });
      }
      if (url === '/projects') {
        return Promise.resolve({
          data: [
            {
              id: 'p1',
              name: 'Project 1',
              assembly_name: 'GRCh38',
              assembly_version: '',
              families: [],
              samples: [],
            },
          ],
        });
      }
      if (url === '/chromosomes/GRCh38/details') {
        return Promise.resolve({
          data: [
            {
              chr: 'chr1',
              size: 248956422,
              bands: [{ name: 'p36.33', start: 0, end: 2300000, stain: 'gneg' }],
            },
          ],
        });
      }
      if (url === '/families/F1/structural-variants?project_id=p1&page_size=0') {
        return Promise.resolve({ data: { variants: [] } });
      }
      return Promise.resolve({ data: {} });
    });

    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1/circos?project_id=p1']}>
          <Routes>
            <Route path="/families/:familyId/circos" element={<CircosPlotPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByTestId('circos-plot')).toHaveTextContent('1:1'));

    expect((api.get as unknown as Mock).mock.calls.map(([url]) => String(url))).toContain(
      '/families/F1/structural-variants?project_id=p1&page_size=0',
    );
  });

  // #589: a failure is shown as a failure, never as a plot still loading or without SVs;
  // a capped SV list is not drawn as the whole.
  const CHROMOSOMES = [
    { chr: 'chr1', size: 248956422, bands: [{ name: 'p36.33', start: 0, end: 2300000, stain: 'gneg' }] },
  ];
  const serve = (routes: Record<string, () => Promise<unknown>>) =>
    (api.get as unknown as Mock).mockImplementation((url: string) =>
      (routes[url] ?? (() => Promise.resolve({ data: {} })))(),
    );
  const renderPage = () =>
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <MemoryRouter initialEntries={['/families/F1/circos']}>
          <Routes>
            <Route path="/families/:familyId/circos" element={<CircosPlotPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
  const failure = () => Promise.reject(Object.assign(new Error('HTTP 500'), { response: { status: 500 } }));

  it('says the chromosomes failed to load and offers a retry, instead of loading forever', async () => {
    let chromosomeCalls = 0;
    serve({
      '/families/F1': () => Promise.resolve({ data: { projects: [] } }),
      '/chromosomes/GRCh38/details': () => (++chromosomeCalls === 1 ? failure() : Promise.resolve({ data: CHROMOSOMES })),
      '/families/F1/structural-variants?page_size=0': () => Promise.resolve({ data: { variants: [] } }),
    });
    renderPage();

    expect(await screen.findByText('Could not load the chromosomes')).toBeInTheDocument();
    expect(screen.queryByText('Loading circos plot')).not.toBeInTheDocument();

    screen.getByRole('button', { name: 'Retry' }).click();
    await waitFor(() => expect(screen.getByTestId('circos-plot')).toHaveTextContent('1:1'));
  });

  it('says the SVs failed to load, not that the family has none', async () => {
    serve({
      '/families/F1': () => Promise.resolve({ data: { projects: [] } }),
      '/chromosomes/GRCh38/details': () => Promise.resolve({ data: CHROMOSOMES }),
      '/families/F1/structural-variants?page_size=0': failure,
    });
    renderPage();

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Could not load structural variants — this is not an empty result.',
    );
    expect(screen.queryByText('No variants for this family.')).not.toBeInTheDocument();
    expect(circosSpy.mock.calls.at(-1)?.[0].variants).toBeUndefined();
  });

  it('does not draw a capped SV list as the whole genome', async () => {
    circosSpy.mockClear();
    serve({
      '/families/F1': () => Promise.resolve({ data: { projects: [] } }),
      '/chromosomes/GRCh38/details': () => Promise.resolve({ data: CHROMOSOMES }),
      '/families/F1/structural-variants?page_size=0': () =>
        Promise.resolve({
          data: {
            total: 50000,
            total_is_estimated: true,
            count_limit: 50000,
            variants: [{ chr: '1', start: 10, end: 40, type: 'DEL' }],
          },
        }),
    });
    renderPage();

    expect(await screen.findByRole('status')).toHaveTextContent(
      'Too many structural variants to draw (more than 50,000).',
    );
    const props = circosSpy.mock.calls.at(-1)?.[0];
    expect(props.variants).toBeUndefined();
    expect(props.tooManyVariants).toBe(true);
  });
});
