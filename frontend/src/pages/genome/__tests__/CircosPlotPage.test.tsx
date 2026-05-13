import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
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
});
