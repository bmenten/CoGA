import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import AdminPgsPage from '../AdminPgsPage';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));
vi.mock('../../../lib/api', () => ({ default: api }));
const version = { id: 'version1', pgs_id: 'PGS005091', version: 'v1', revision: 1, archived: false,
  study: 'Synthetic legacy study', source_url: 'https://example.org/study', genome_build: 'GRCh38',
  weights_sha256: 'synthetic-checksum', resolved_sha256: 'resolved-checksum', reference: { reference: 'explicit ref_allele' },
  variant_count: 2, created_at: '2026-01-01T00:00:00Z' };
const metadata = { provider: 'PGS Catalog', retrieved_at: '2026-11-01', download_sha256: 'download-checksum',
  record_sha256: 'catalog-record-checksum', api_response_sha256: 'response-checksum', record: {
    name: 'Synthetic catalog model', trait_reported: 'Synthetic trait', trait_additional: ['Additional synthetic trait'],
    trait_efo: [{ id: 'EFO_SYNTHETIC', label: 'Synthetic ontology trait' }],
    publication: { title: 'Synthetic publication title', firstauthor: 'Synthetic research team', journal: 'Synthetic journal',
      date_publication: '2025-05-01', doi: '10.1234/synthetic-study', PMID: 123456 },
    method_name: 'Synthetic additive method', method_params: 'Synthetic parameters', weight_type: 'beta',
    variants_genomebuild: 'GRCh37', date_release: '2026-01-01', license: 'Synthetic licence',
    samples_training: [{ ancestry_broad: 'European', sample_number: 1000 }, { ancestry_broad: 'East Asian', sample_number: 500 }],
    unused_internal_field: 'RAW_JSON_MUST_NOT_RENDER',
  } };
const renderPage = () => render(<QueryClientProvider client={createTestQueryClient()}><AdminPgsPage /></QueryClientProvider>);
const modelsResponse = (versions: unknown[]) => ({ data: { shortlist: ['PGS005091'], versions } });
const importsResponse = { data: { jobs: [], reference_configured: true } };

describe('exploratory PGS administration', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    api.get.mockImplementation((url: string) => Promise.resolve(url.endsWith('/catalog-imports') ? importsResponse : modelsResponse([version])));
    api.post.mockResolvedValue({ data: version });
    api.put.mockResolvedValue({ data: {} });
  });

  it('shows the trait and publication immediately with readable model metadata and no manual upload or raw JSON', async () => {
    api.get.mockImplementation((url: string) => Promise.resolve(url.endsWith('/catalog-imports') ? importsResponse
      : modelsResponse([{ ...version, catalog_metadata: metadata }])));
    const { container } = renderPage();
    const card = within(await screen.findByRole('article', { name: 'PGS005091 model' }));
    expect(card.getByRole('heading', { name: 'PGS005091' })).toBeVisible();
    expect(card.getByRole('link', { name: 'PGS005091' })).toHaveAttribute('href', 'https://www.pgscatalog.org/score/PGS005091/');
    expect(card.getByText('Synthetic catalog model')).toBeVisible();
    expect(card.getAllByText('Synthetic trait')[0]).toBeVisible();
    expect(card.getAllByRole('link', { name: 'Synthetic publication title' })[0]).toHaveAttribute('href', 'https://doi.org/10.1234/synthetic-study');
    expect(card.getAllByRole('link', { name: 'PubMed' })[0]).toHaveAttribute('href', 'https://pubmed.ncbi.nlm.nih.gov/123456/');
    expect(card.getAllByText('Synthetic research team · Synthetic journal · 1 May 2025')[0]).toBeVisible();
    expect(screen.queryByRole('heading', { name: 'Manual model version upload' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Scoring file')).not.toBeInTheDocument();
    expect(container.querySelector('input[type="file"]')).toBeNull();
    expect(container.querySelector('pre')).toBeNull();
    expect(screen.queryByText('RAW_JSON_MUST_NOT_RENDER')).not.toBeInTheDocument();

    await userEvent.click(card.getByText('Model details and version history (1)'));
    for (const value of ['Synthetic additive method', 'Synthetic parameters', 'beta', 'GRCh37', 'European, East Asian',
      '2 reported', 'Synthetic licence', 'Additional synthetic trait', 'Synthetic ontology trait · EFO_SYNTHETIC']) {
      expect(card.getByText(value)).toBeVisible();
    }
    await userEvent.click(card.getByText('Sources and checksums'));
    expect(card.getByText('catalog-record-checksum')).toBeVisible();
    expect(card.getByText('download-checksum')).toBeVisible();
    expect(card.getByRole('link', { name: 'Download scoring source' })).toHaveAttribute('href', version.source_url);
  });

  it('selects the latest revision regardless of response order and keeps previous traits, studies and reference provenance', async () => {
    const old = { ...version, catalog_metadata: metadata };
    const current = { ...version, id: 'version2', version: 'v2', revision: 2, scoring_status: 'needs_reference',
      scoring_error: 'Reference validation required.', catalog_metadata: { ...metadata, record: {
        ...metadata.record, trait_reported: 'Updated trait', publication: { title: 'Updated study', PMID: '456789' } } },
      reference: { reference: 'indexed GRCh38 FASTA', fasta_sha256: 'fasta-checksum', fai_sha256: 'index-checksum' } };
    api.get.mockImplementation((url: string) => Promise.resolve(url.endsWith('/catalog-imports') ? importsResponse : modelsResponse([old, current])));
    renderPage();
    const card = within(await screen.findByRole('article', { name: 'PGS005091 model' }));
    expect(card.getAllByText('Updated trait')[0]).toBeVisible();
    expect(card.getAllByRole('link', { name: 'Updated study' })[0]).toHaveAttribute('href', 'https://pubmed.ncbi.nlm.nih.gov/456789/');
    expect(card.getByText('Reference validation required.')).toBeVisible();
    expect(card.getByText('Revision 2')).toBeVisible();
    await userEvent.click(card.getByText('Model details and version history (2)'));
    expect(card.getByText('v1')).toBeVisible();
    expect(card.getByText('v2')).toBeVisible();
    expect(card.getByText('Synthetic trait')).toBeVisible();
    const latest = card.getByText('v2').closest('li')!;
    await userEvent.click(within(latest).getByText('Sources and checksums'));
    expect(within(latest).getByText('fasta-checksum')).toBeVisible();
    expect(within(latest).getByText('index-checksum')).toBeVisible();
  });

  it('archives and reactivates a model while keeping its version history', async () => {
    let archived = false;
    api.get.mockImplementation((url: string) => Promise.resolve(url.endsWith('/catalog-imports') ? importsResponse : modelsResponse([{ ...version, archived }])));
    api.put.mockImplementation(() => { archived = !archived; return Promise.resolve({ data: {} }); });
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: 'Archive PGS005091' }));
    await waitFor(() => expect(api.put).toHaveBeenCalledWith('/admin/pgs/models/PGS005091/archive', { archived: true }));
    const reactivate = await screen.findByRole('button', { name: 'Reactivate PGS005091' });
    fireEvent.click(reactivate);
    await waitFor(() => expect(api.put).toHaveBeenLastCalledWith('/admin/pgs/models/PGS005091/archive', { archived: false }));
    await screen.findByRole('button', { name: 'Archive PGS005091' });
    await userEvent.click(screen.getByText('Model details and version history (1)'));
    expect(screen.getByText('v1')).toBeVisible();
  });

  it('counts models once and distinguishes ready, blocked and archived models', async () => {
    const versions = [version, { ...version, id: 'old', revision: 0 },
      { ...version, id: 'blocked', pgs_id: 'PGS000013', scoring_status: 'unsupported' },
      { ...version, id: 'archived', pgs_id: 'PGS000024', archived: true }];
    api.get.mockImplementation((url: string) => Promise.resolve(url.endsWith('/catalog-imports') ? importsResponse : modelsResponse(versions)));
    renderPage();
    await screen.findByRole('article', { name: 'PGS005091 model' });
    const catalogue = screen.getByLabelText('Catalogue summary');
    expect(within(catalogue).getByText('Models').nextElementSibling).toHaveTextContent('3');
    for (const label of ['Ready for calculation', 'Needs attention', 'Archived']) {
      expect(within(catalogue).getByText(label).nextElementSibling).toHaveTextContent('1');
    }
  });

  it('supports legacy versions without Catalog metadata', async () => {
    const legacy = version;
    api.get.mockImplementation((url: string) => Promise.resolve(url.endsWith('/catalog-imports') ? importsResponse : modelsResponse([legacy])));
    renderPage();
    const card = within(await screen.findByRole('article', { name: 'PGS005091 model' }));
    expect(card.getAllByText('Synthetic legacy study')[0]).toBeVisible();
    expect(card.getAllByText('Not reported')[0]).toBeVisible();
    expect(screen.queryByText('[object Object]')).not.toBeInTheDocument();
    await userEvent.click(card.getByText('Model details and version history (1)'));
    await userEvent.click(card.getByText('Sources and checksums'));
    expect(card.getByText('Custom source')).toBeVisible();
    expect(card.getByText('synthetic-checksum')).toBeVisible();
  });

  it('handles malformed optional metadata without exposing objects or unsafe publication links', async () => {
    const malformed = { ...version, catalog_metadata: { record: { publication: { doi: 'javascript:invalid', PMID: 'invalid' },
      trait_efo: [null, 'invalid'], samples_training: [null], date_release: 'invalid', weight_type: 'NR' } } };
    api.get.mockImplementation((url: string) => Promise.resolve(url.endsWith('/catalog-imports') ? importsResponse : modelsResponse([malformed])));
    const { container } = renderPage();
    const card = within(await screen.findByRole('article', { name: 'PGS005091 model' }));
    expect(card.getAllByText('Synthetic legacy study')[0]).toBeVisible();
    expect(container.querySelector('a[href^="javascript:"]')).toBeNull();
    expect(card.queryByRole('link', { name: 'DOI' })).not.toBeInTheDocument();
    expect(card.queryByRole('link', { name: 'PubMed' })).not.toBeInTheDocument();
    expect(screen.queryByText('[object Object]')).not.toBeInTheDocument();
  });

  it('shows an empty catalogue and loading failures', async () => {
    api.get.mockImplementation((url: string) => Promise.resolve(url.endsWith('/catalog-imports') ? importsResponse : modelsResponse([])));
    const mounted = renderPage();
    expect(await screen.findByText('No models imported yet. Enter a PGS ID above to add a model.')).toBeVisible();
    mounted.unmount();
    api.get.mockRejectedValue(new Error('Unavailable'));
    renderPage();
    expect(await screen.findByRole('alert')).toHaveTextContent('Unavailable');
  });

  it('links to the Catalog and imports any PGS ID in the background with refreshed metadata and no retrospective scoring', async () => {
    let imported = false;
    const catalogVersion = { ...version, id: 'catalog-v1', pgs_id: 'PGS900001', scoring_status: 'needs_reference',
      scoring_error: 'Every locus needs ref_allele or an indexed GRCh38 FASTA.', catalog_metadata: metadata };
    api.get.mockImplementation((url: string) => Promise.resolve({ data: url.endsWith('/catalog-imports')
      ? { reference_configured: false, jobs: imported ? [{ id: 'job1', pgs_id: 'PGS900001', status: 'completed', version_id: 'catalog-v1' }] : [] }
      : { shortlist: ['PGS005091'], versions: imported ? [catalogVersion, version] : [version] } }));
    api.post.mockImplementation(() => { imported = true; return Promise.resolve({ data: { id: 'job1', status: 'queued' } }); });
    renderPage();
    expect(screen.getByRole('link', { name: 'Find a score in the PGS Catalog' })).toHaveAttribute('href', 'https://www.pgscatalog.org/');
    fireEvent.change(screen.getByLabelText('Catalog PGS ID'), { target: { value: 'pgs900001' } });
    fireEvent.click(screen.getByRole('button', { name: 'Import GRCh38 from Catalog' }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/admin/pgs/catalog-imports', { pgs_id: 'PGS900001' }));
    const card = within(await screen.findByRole('article', { name: 'PGS900001 model' }));
    expect(card.getAllByText('Reference required')[0]).toBeVisible();
    expect(card.getAllByText('Synthetic trait')[0]).toBeVisible();
    expect(screen.getByRole('status')).toHaveTextContent('Existing families are not recalculated automatically');
    expect(api.post).toHaveBeenCalledTimes(1);
  });
});
