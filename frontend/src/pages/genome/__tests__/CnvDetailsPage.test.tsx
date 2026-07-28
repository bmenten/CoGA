import { render, screen } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { useQueryMock } = vi.hoisted(() => ({ useQueryMock: vi.fn() }));

vi.mock('@tanstack/react-query', () => ({ useQuery: useQueryMock }));
vi.mock('../../../lib/api', () => ({ default: { get: vi.fn() } }));

import CnvDetailsPage from '../CnvDetailsPage';

const renderAt = (id: string) =>
  render(
    <MemoryRouter initialEntries={[`/cnv-details/${id}`]}>
      <Routes>
        <Route path="/cnv-details/:cnvId" element={<CnvDetailsPage />} />
      </Routes>
    </MemoryRouter>,
  );

type QueryResult = { data: unknown; isLoading: boolean; isError: boolean };

const mockQueries = (cnvResult: QueryResult, chromResult: QueryResult) => {
  useQueryMock.mockImplementation((options?: { queryKey?: unknown[] }) => {
    const key = Array.isArray(options?.queryKey) ? options?.queryKey[0] : undefined;
    return key === 'clinical-cnv' ? cnvResult : chromResult;
  });
};

beforeEach(() => useQueryMock.mockReset());

describe('CnvDetailsPage', () => {
  it('renders structured CNV details with computed cytoband and external links', () => {
    mockQueries(
      {
        data: {
          _id: 'cnv-1',
          chr: 'chr1',
          start: 10000,
          end: 27600000,
          type: 'loss',
          label: '1p36 deletion syndrome',
          assembly: 'GRCh38',
          description: 'Developmental delay and characteristic facial features.',
        },
        isLoading: false,
        isError: false,
      },
      {
        data: {
          chr: '1',
          size: 248956422,
          bands: [
            { name: 'p36.33', start: 0, end: 2300000, stain: 'gneg' },
            { name: 'p36.32', start: 2300000, end: 5300000, stain: 'gpos25' },
          ],
        },
        isLoading: false,
        isError: false,
      },
    );

    renderAt('cnv-1');

    expect(screen.getByText('1p36 deletion syndrome')).toBeInTheDocument();
    expect(screen.getByText(/Developmental delay/)).toBeInTheDocument();
    expect(screen.getByText(/1p36\.33.*1p36\.32/)).toBeInTheDocument();

    expect(screen.getByRole('link', { name: /OMIM/i })).toHaveAttribute(
      'href',
      expect.stringContaining('omim.org/search'),
    );
    expect(screen.getByRole('link', { name: /DECIPHER/i })).toHaveAttribute(
      'href',
      expect.stringContaining('deciphergenomics.org/browser#q/1:10000-27600000'),
    );
  });

  it('shows a not-found state when the CNV cannot be loaded', () => {
    mockQueries(
      { data: undefined, isLoading: false, isError: true },
      { data: undefined, isLoading: false, isError: false },
    );
    renderAt('missing');
    expect(screen.getByText(/CNV not found/i)).toBeInTheDocument();
  });
});
