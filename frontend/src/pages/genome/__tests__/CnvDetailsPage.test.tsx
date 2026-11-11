import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';
import CnvDetailsPage from '../CnvDetailsPage';

vi.mock('../../../lib/api', () => ({ default: { get: vi.fn() } }));

// The page is opened from the CNV explorer, so Back returns there.
const renderAt = (id: string) =>
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <MemoryRouter initialEntries={['/cnv-explorer', `/cnv-details/${id}`]} initialIndex={1}>
        <Routes>
          <Route path="/cnv-details/:cnvId" element={<CnvDetailsPage />} />
          <Route path="/cnv-explorer" element={<p>CNV explorer</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );

/** Answers each GET by its path; any other path is a 404. */
const serve = (routes: Record<string, () => Promise<unknown>>) =>
  vi.mocked(api.get).mockImplementation((url: string) =>
    routes[url] ? routes[url]() : Promise.reject({ response: { status: 404 } }),
  );
const data = (value: unknown) => () => Promise.resolve({ data: value });
const requestsTo = (path: string) => vi.mocked(api.get).mock.calls.filter(([url]) => url === path).length;

const DELETION_1P36 = {
  _id: 'cnv-1',
  chr: 'chr1',
  start: 10000,
  end: 27600000,
  type: 'loss',
  label: '1p36 deletion syndrome',
  assembly: 'GRCh38',
  description: 'Developmental delay and characteristic facial features.',
};

// Braces: a function beforeEach returns is run as a cleanup hook.
beforeEach(() => {
  vi.mocked(api.get).mockReset();
});

describe('CnvDetailsPage', () => {
  it('renders structured CNV details with its cytoband and external links', async () => {
    serve({ '/cnvs/entry/cnv-1': data({ ...DELETION_1P36, cytoband: '1p36.33-1p36.13' }) });

    renderAt('cnv-1');

    expect(await screen.findByText('1p36 deletion syndrome')).toBeInTheDocument();
    expect(screen.getByText(/Developmental delay/)).toBeInTheDocument();
    // The server gives the cytoband (stated, or read from the assembly's cytoband track):
    // the page reads no chromosome of its own.
    expect(screen.getByText('Cytoband').nextElementSibling).toHaveTextContent(/^1p36\.33-1p36\.13$/);
    expect(vi.mocked(api.get).mock.calls.map(([url]) => url)).toEqual(['/cnvs/entry/cnv-1']);

    expect(screen.getByRole('link', { name: /OMIM/i })).toHaveAttribute(
      'href',
      expect.stringContaining('omim.org/search'),
    );
    expect(screen.getByRole('link', { name: /DECIPHER/i })).toHaveAttribute(
      'href',
      expect.stringContaining('deciphergenomics.org/browser#q/1:10000-27600000'),
    );
    expect(screen.getByText('Type').nextElementSibling).toHaveTextContent('loss');
    expect(screen.getByText('Assembly').nextElementSibling).toHaveTextContent('GRCh38');
    expect(screen.getByRole('link', { name: 'All clinical CNVs' })).toHaveAttribute('href', '/cnv-explorer');
  });

  it('shows the loading state while the CNV is read', () => {
    serve({ '/cnvs/entry/cnv-1': () => new Promise(() => {}) });
    renderAt('cnv-1');
    expect(screen.getByText('Loading CNV details…')).toBeInTheDocument();
  });

  it('shows a not-found state when the CNV cannot be loaded', async () => {
    serve({});
    renderAt('missing');
    expect(await screen.findByText(/CNV not found/i)).toBeInTheDocument();
    expect(requestsTo('/cnvs/entry/missing')).toBe(1);

    fireEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect(screen.getByText('CNV explorer')).toBeInTheDocument();
  });

  // A server error is not a CNV that does not exist.
  it('says the CNV could not be loaded on a server error, and retries', async () => {
    let failing = true;
    serve({
      '/cnvs/entry/cnv-1': () =>
        failing
          ? Promise.reject({ response: { status: 500, data: { detail: 'Database unavailable' } } })
          : Promise.resolve({ data: DELETION_1P36 }),
    });
    renderAt('cnv-1');

    expect(await screen.findByRole('heading', { name: 'CNV could not be loaded' })).toBeInTheDocument();
    expect(screen.getByText('Database unavailable This is a failed request, not a missing CNV.')).toBeInTheDocument();
    expect(screen.queryByText(/CNV not found/i)).not.toBeInTheDocument();
    failing = false;
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('1p36 deletion syndrome')).toBeInTheDocument();
    expect(requestsTo('/cnvs/entry/cnv-1')).toBe(2);
  });

  it('goes back from the server-error state', async () => {
    serve({ '/cnvs/entry/cnv-1': () => Promise.reject({ response: { status: 500 } }) });
    renderAt('cnv-1');

    await screen.findByRole('heading', { name: 'CNV could not be loaded' });
    fireEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect(screen.getByText('CNV explorer')).toBeInTheDocument();
  });

  it('shows no cytoband when the server has none for the CNV', async () => {
    serve({ '/cnvs/entry/cnv-1': data({ ...DELETION_1P36, cytoband: null }) });
    renderAt('cnv-1');

    await screen.findByText('1p36 deletion syndrome');
    expect(screen.getByText('Cytoband').nextElementSibling).toHaveTextContent(/^—$/);
  });

  // The knowledgebase's ClinVar support, with a link to each supporting record.
  const withSupport = async (support: Record<string, unknown>) => {
    serve({
      '/cnvs/entry/cnv-1': data({
        _id: 'cnv-1',
        chr: '22',
        start: 18924718,
        end: 21111383,
        label: '22q11.2 recurrent (DGS) region',
        ...support,
      }),
    });
    renderAt('cnv-1');
    await screen.findByText('22q11.2 recurrent (DGS) region');
  };

  it('shows the ClinVar loss/gain support and links each supporting record', async () => {
    await withSupport({
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

  it('says the ClinVar support was not recorded, rather than none', async () => {
    await withSupport({});

    expect(screen.getByText('ClinVar pathogenic').nextElementSibling).toHaveTextContent('Not recorded');
    expect(screen.queryByText('Supporting ClinVar records')).not.toBeInTheDocument();
    // A CNV without an assembly shows no cytoband or assembly.
    expect(screen.getByText('Cytoband').nextElementSibling).toHaveTextContent(/^—$/);
    expect(screen.queryByText('Assembly')).not.toBeInTheDocument();
    expect(vi.mocked(api.get).mock.calls.map(([url]) => url)).toEqual(['/cnvs/entry/cnv-1']);
  });

  // details_html comes from imported reference data: it reaches the page sanitised.
  it('links the OMIM entry and Orphanet record a CNV names, and shows its source sanitised', async () => {
    await withSupport({
      cytoband: '22q11.21',
      omim_id: 'OMIM:188400',
      omim_title: 'DiGeorge syndrome',
      orpha_id: '567',
      orpha_name: '22q11.2 deletion syndrome',
      decipher_id: '2',
      source_id: 'ISCA-37446',
      details_html: '<p>Curated <b>source</b></p><script>window.injected = true</script><img src="x" onerror="window.injected = true">',
    });

    // A cytoband the record states is shown as stated.
    expect(screen.getByText('Cytoband').nextElementSibling).toHaveTextContent('22q11.21');
    expect(screen.getByText('Type').nextElementSibling).toHaveTextContent('Clinical CNV');
    expect(screen.getByText('OMIM').nextElementSibling).toHaveTextContent('OMIM:188400 · DiGeorge syndrome');
    expect(screen.getByText('Orphanet').nextElementSibling).toHaveTextContent('22q11.2 deletion syndrome · ORPHA:567');
    expect(screen.getByText('DECIPHER ID').nextElementSibling).toHaveTextContent('2');
    expect(screen.getByText('ISCA').nextElementSibling).toHaveTextContent('ISCA-37446');
    expect(screen.getByText(/No curated clinical description is available/)).toBeInTheDocument();

    expect(screen.getByRole('link', { name: 'OMIM ↗' })).toHaveAttribute('href', 'https://www.omim.org/entry/188400');
    expect(screen.getByRole('link', { name: 'Orphanet ↗' })).toHaveAttribute(
      'href',
      'https://www.orpha.net/en/disease/detail/567',
    );

    const source = document.querySelector('.content-html') as HTMLElement;
    expect(source).toHaveTextContent('Curated source');
    expect(source.querySelector('script')).toBeNull();
    expect(source.querySelector('[onerror]')).toBeNull();
    expect((window as { injected?: boolean }).injected).toBeUndefined();
  });
});
