import { fireEvent, render, screen } from '@testing-library/react';
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

type QueryResult = { data: unknown; isLoading: boolean; isError: boolean; error?: unknown; refetch?: () => void };

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
      { data: undefined, isLoading: false, isError: true, error: { response: { status: 404 } } },
      { data: undefined, isLoading: false, isError: false },
    );
    renderAt('missing');
    expect(screen.getByText(/CNV not found/i)).toBeInTheDocument();
  });

  // A server error is not a CNV that does not exist (#624, as #610).
  it('says the CNV could not be loaded on a server error, and retries', () => {
    const refetch = vi.fn();
    mockQueries(
      {
        data: undefined,
        isLoading: false,
        isError: true,
        error: { response: { status: 500, data: { detail: 'Database unavailable' } } },
        refetch,
      },
      { data: undefined, isLoading: false, isError: false },
    );
    renderAt('cnv-1');

    expect(screen.getByRole('heading', { name: 'CNV could not be loaded' })).toBeInTheDocument();
    expect(screen.getByText('Database unavailable This is a failed request, not a missing CNV.')).toBeInTheDocument();
    expect(screen.queryByText(/CNV not found/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  // #624 — the knowledgebase's ClinVar support, with a link to each supporting record.
  const withSupport = (support: Record<string, unknown>) => {
    mockQueries(
      {
        data: { _id: 'cnv-1', chr: '22', start: 18924718, end: 21111383, label: '22q11.2 recurrent (DGS) region', ...support },
        isLoading: false,
        isError: false,
      },
      { data: undefined, isLoading: false, isError: false },
    );
    renderAt('cnv-1');
  };

  it('shows the ClinVar loss/gain support and links each supporting record', () => {
    withSupport({
      clinvar_pathogenic_loss_count: 132,
      clinvar_pathogenic_gain_count: 70,
      clinvar_pathogenic_accessions: ['57226', 'RCV000051234'],
    });

    expect(screen.getByText('ClinVar pathogenic').nextElementSibling).toHaveTextContent('132 loss · 70 gain');
    expect(screen.getByRole('link', { name: '57226 ↗' })).toHaveAttribute(
      'href',
      'https://www.ncbi.nlm.nih.gov/clinvar/variation/57226/',
    );
    expect(screen.getByRole('link', { name: 'RCV000051234 ↗' })).toHaveAttribute(
      'href',
      'https://www.ncbi.nlm.nih.gov/clinvar/RCV000051234/',
    );
  });

  it('says the ClinVar support was not recorded, rather than none', () => {
    withSupport({});

    expect(screen.getByText('ClinVar pathogenic').nextElementSibling).toHaveTextContent('Not recorded');
    expect(screen.queryByText('Supporting ClinVar records')).not.toBeInTheDocument();
  });
});
