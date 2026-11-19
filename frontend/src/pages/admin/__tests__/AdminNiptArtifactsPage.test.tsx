// The admin page of the monogenic-NIPT recurrent-artefact list: per assembly and assay scope, its
// entries; the auto-seed, previewed first without writing, then confirmed; the pipeline's table,
// confirmed, with what the import did; a curated entry added and an entry removed, each confirmed.
// The removal asks no reason: the endpoint takes none, and the audit event records the entry.
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import AdminNiptArtifactsPage, { parseArtifactVariant } from '../AdminNiptArtifactsPage';
import api from '../../../lib/api';
import type {
  AssemblyOut,
  NiptArtifactAutoSeedOut,
  NiptArtifactAutoSeedPreviewOut,
  NiptArtifactImportOut,
  NiptArtifactOut,
  NiptArtifactScopeOut,
} from '../../../lib/apiSchema.generated';

vi.mock('../../../lib/api', () => ({
  default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));

const mockedGet = vi.mocked(api.get);
const mockedPost = vi.mocked(api.post);
const mockedDelete = vi.mocked(api.delete);

const GRCH38 = '0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d';
const GRCH37 = '5e6f7a8b-9c0d-4e1f-8a2b-3c4d5e6f7a8b';

const ASSEMBLIES: AssemblyOut[] = [
  { _id: GRCH38, species_id: 's1', assembly_name: 'GRCh38', version: '38', release_date: '2013-12-17' },
  { _id: GRCH37, species_id: 's1', assembly_name: 'GRCh37', version: '37', release_date: '2009-02-27' },
];

const SCOPES: NiptArtifactScopeOut[] = [
  { assay_key: 'nipt_cfdna', cfdna_samples: 18, listed: 2 },
  { assay_key: 'PANEL_A', cfdna_samples: 4, listed: 1 },
];

const entry = (overrides: Partial<NiptArtifactOut>): NiptArtifactOut => ({
  id: '11111111-1111-4111-8111-111111111111',
  assembly_id: GRCH38,
  assay_key: 'nipt_cfdna',
  variant_id: '7-1000-A-G',
  recurrence_count: 3,
  source: 'auto',
  label: 'recurrent (auto)',
  created_at: '2026-11-18T09:30:00Z',
  updated_at: '2026-11-18T09:30:00Z',
  ...overrides,
});

const ENTRIES: Record<string, NiptArtifactOut[]> = {
  nipt_cfdna: [
    entry({}),
    entry({
      id: '22222222-2222-4222-8222-222222222222',
      variant_id: '17-41245466-G-A',
      recurrence_count: 0,
      source: 'curated',
      label: 'NTC run 12',
      updated_at: '2026-11-19T08:00:00Z',
    }),
  ],
  PANEL_A: [entry({ id: '33333333-3333-4333-8333-333333333333', assay_key: 'PANEL_A', variant_id: 'X-200-C-T' })],
};

const PREVIEW: NiptArtifactAutoSeedPreviewOut = {
  assay_key: 'nipt_cfdna',
  min_unexplained_samples: 3,
  cfdna_samples: 18,
  variants: [
    { variant_id: '2-200-C-T', recurrence_count: 9, listed_source: null },
    { variant_id: '7-1000-A-G', recurrence_count: 4, listed_source: 'auto' },
  ],
};

const SEEDED: NiptArtifactAutoSeedOut = { ...PREVIEW, seeded: 2 };

const IMPORTED: NiptArtifactImportOut = {
  rows_read: 40,
  not_flagged: 30,
  invalid: 1,
  imported: 7,
  protected_common: 1,
  protected_clinvar: 1,
};

type Config = { params?: unknown } | undefined;

const answer = (url: string, config: Config) => {
  if (url === '/assemblies') return { data: ASSEMBLIES };
  if (url === '/admin/nipt/artifacts/scopes') return { data: SCOPES };
  if (url === '/admin/nipt/artifacts') {
    const params = (config?.params ?? {}) as Record<string, unknown>;
    return { data: params.assembly_id === GRCH38 ? (ENTRIES[String(params.assay_key)] ?? []) : [] };
  }
  if (url === '/admin/nipt/artifacts/auto-seed/preview') return { data: PREVIEW };
  throw new Error(`unexpected GET ${url}`);
};

const renderPage = () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <AdminNiptArtifactsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

const entryTable = () => screen.getAllByRole('table')[0];
const entryRows = () => within(entryTable()).getAllByRole('row').slice(1);

describe('parseArtifactVariant', () => {
  it('reads the stored form, the pipeline key and a written allele as the list stores them', () => {
    expect(parseArtifactVariant('7-1000-A-G')).toBe('7-1000-A-G');
    expect(parseArtifactVariant('chr7:1000:a:g')).toBe('7-1000-A-G');
    expect(parseArtifactVariant(' chrX:01000 C>T ')).toBe('X-1000-C-T');
    expect(parseArtifactVariant('CHR17-41245466-GA-G')).toBe('17-41245466-GA-G');
  });

  it('refuses what is not one allele', () => {
    for (const value of ['', '7-1000-A', '7-0-A-G', '7-1e3-A-G', '7-1000-A-G,T', '7-1000-A-.', '7-1000-A-*', 'chr-1000-A-G']) {
      expect(parseArtifactVariant(value)).toBeNull();
    }
  });
});

describe('AdminNiptArtifactsPage', () => {
  beforeEach(() => {
    mockedGet.mockReset();
    mockedPost.mockReset();
    mockedDelete.mockReset();
    mockedGet.mockImplementation(async (url: string, config?: Config) => answer(url, config) as never);
    mockedDelete.mockResolvedValue({ data: { deleted: true } });
  });

  it('lists the default scope of the first assembly, with each entry’s source, recurrence and label', async () => {
    renderPage();

    expect(await screen.findByRole('heading', { name: 'NIPT artefact list' })).toBeInTheDocument();
    await waitFor(() => expect(entryRows()).toHaveLength(2));
    expect(mockedGet).toHaveBeenCalledWith('/admin/nipt/artifacts/scopes', { params: { assembly_id: GRCH38 } });
    expect(mockedGet).toHaveBeenCalledWith('/admin/nipt/artifacts', {
      params: { assembly_id: GRCH38, assay_key: 'nipt_cfdna' },
    });
    const [auto, curated] = entryRows();
    expect(within(auto).getAllByRole('cell').map((cell) => cell.textContent)).toEqual([
      '7-1000-A-G',
      'auto',
      '3',
      'recurrent (auto)',
      '2026-11-18 09:30 UTC',
      '2026-11-18 09:30 UTC',
      'Remove',
    ]);
    expect(within(curated).getByText('curated')).toBeInTheDocument();
    expect(within(curated).getByText('NTC run 12')).toBeInTheDocument();
    // Each scope says how many cfDNA samples it has and how many alleles it lists.
    expect(screen.getByRole('option', { name: 'nipt_cfdna (18 cfDNA samples, 2 listed)' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'PANEL_A (4 cfDNA samples, 1 listed)' })).toBeInTheDocument();
    // Nothing is written by reading the page.
    expect(mockedPost).not.toHaveBeenCalled();
  });

  it('reads the list of the scope and assembly chosen, and filters it by variant or label', async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(entryRows()).toHaveLength(2));

    await user.type(screen.getByRole('searchbox', { name: 'Filter' }), 'ntc');
    expect(entryRows()).toHaveLength(1);
    expect(within(entryRows()[0]).getByText('17-41245466-G-A')).toBeInTheDocument();
    await user.clear(screen.getByRole('searchbox', { name: 'Filter' }));

    await user.selectOptions(screen.getByRole('combobox', { name: 'Assay scope' }), 'PANEL_A');
    await waitFor(() => expect(within(entryTable()).getByText('X-200-C-T')).toBeInTheDocument());
    expect(mockedGet).toHaveBeenCalledWith('/admin/nipt/artifacts', {
      params: { assembly_id: GRCH38, assay_key: 'PANEL_A' },
    });

    await user.selectOptions(screen.getByRole('combobox', { name: 'Assembly' }), GRCH37);
    await waitFor(() =>
      expect(mockedGet).toHaveBeenCalledWith('/admin/nipt/artifacts/scopes', { params: { assembly_id: GRCH37 } }),
    );
    // Another assembly starts on the default scope.
    await waitFor(() =>
      expect(mockedGet).toHaveBeenCalledWith('/admin/nipt/artifacts', {
        params: { assembly_id: GRCH37, assay_key: 'nipt_cfdna' },
      }),
    );
    expect(await screen.findByText('The list of this scope is empty.')).toBeInTheDocument();
  });

  it('previews the auto-seed without writing, then seeds what was previewed once confirmed', async () => {
    const user = userEvent.setup();
    mockedPost.mockResolvedValue({ data: SEEDED });
    renderPage();
    await waitFor(() => expect(entryRows()).toHaveLength(2));

    expect(screen.getByRole('spinbutton', { name: 'Minimum samples' })).toHaveValue(3);
    await user.click(screen.getByRole('button', { name: 'Preview' }));

    expect(mockedGet).toHaveBeenCalledWith('/admin/nipt/artifacts/auto-seed/preview', {
      params: { assembly_id: GRCH38, assay_key: 'nipt_cfdna', min_unexplained_samples: 3 },
    });
    const previewTable = await screen.findByRole('table', { name: 'Alleles the auto-seed would list' });
    const rows = within(previewTable).getAllByRole('row').slice(1);
    expect(rows.map((row) => within(row).getAllByRole('cell').map((cell) => cell.textContent))).toEqual([
      ['2-200-C-T', '9', 'New'],
      ['7-1000-A-G', '4', 'Listed (auto): count refreshed'],
    ]);
    expect(
      screen.getByText(/the seed would list 2 alleles: 1 new allele, 1 allele on the list already/),
    ).toBeInTheDocument();
    expect(mockedPost).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: 'Seed the list' }));
    const dialog = screen.getByRole('dialog', { name: 'Confirm the auto-seed' });
    expect(within(dialog).getByText(/at least 3 cfDNA samples of the scope/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Seed' }));

    await waitFor(() =>
      expect(mockedPost).toHaveBeenCalledWith('/admin/nipt/artifacts/auto-seed', {
        assembly_id: GRCH38,
        assay_key: 'nipt_cfdna',
        min_unexplained_samples: 3,
      }),
    );
    expect(
      await screen.findByText('Auto-seeded 2 alleles into the nipt_cfdna list at 3 samples.'),
    ).toBeInTheDocument();
    expect(screen.getByRole('table', { name: 'Alleles the auto-seed listed' })).toBeInTheDocument();
  });

  it('cancels a seed without writing, and drops a preview read at another threshold', async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(entryRows()).toHaveLength(2));

    await user.click(screen.getByRole('button', { name: 'Preview' }));
    await user.click(await screen.findByRole('button', { name: 'Seed the list' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(mockedPost).not.toHaveBeenCalled();

    const threshold = screen.getByRole('spinbutton', { name: 'Minimum samples' });
    await user.clear(threshold);
    await user.type(threshold, '0');
    expect(screen.queryByRole('table', { name: 'Alleles the auto-seed would list' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Preview' })).toBeDisabled();
    expect(screen.getByText('A whole number from 1.')).toBeInTheDocument();
  });

  it('imports the pipeline’s table into the scope once confirmed, and shows what the import did', async () => {
    const user = userEvent.setup();
    mockedPost.mockResolvedValue({ data: IMPORTED });
    renderPage();
    await waitFor(() => expect(entryRows()).toHaveLength(2));

    const file = new File(['variant_key\tfilter_as_recurrent_artifact\n'], 'recurrent_cfdna_artifact_filter_run.tsv', {
      type: 'text/tab-separated-values',
    });
    fireEvent.change(screen.getByLabelText('Artefact table'), { target: { files: [file] } });
    await user.click(screen.getByRole('button', { name: 'Import' }));
    const dialog = screen.getByRole('dialog', { name: 'Confirm the import' });
    expect(within(dialog).getByText(/added to the nipt_cfdna on GRCh38 list as curated entries/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Import' }));

    await waitFor(() => expect(mockedPost).toHaveBeenCalledTimes(1));
    const [url, body, config] = mockedPost.mock.calls[0] as [string, FormData, unknown];
    expect(url).toBe('/admin/nipt/artifacts/import');
    expect(body.get('file')).toBe(file);
    expect(body.get('assembly_id')).toBe(GRCH38);
    expect(body.get('assay_key')).toBe('nipt_cfdna');
    expect(config).toEqual({ headers: { 'Content-Type': 'multipart/form-data' } });

    const summary = await screen.findByLabelText(
      'What the import of recurrent_cfdna_artifact_filter_run.tsv did',
    );
    expect(within(summary).getByText('Imported').nextSibling?.textContent).toBe('7');
    expect(within(summary).getByText('Refused: frequent').nextSibling?.textContent).toBe('1');
    expect(within(summary).getByText('Refused: ClinVar').nextSibling?.textContent).toBe('1');
    expect(within(summary).getByText('Not flagged').nextSibling?.textContent).toBe('30');
  });

  it('adds a curated entry once confirmed, as the list stores the allele', async () => {
    const user = userEvent.setup();
    mockedPost.mockResolvedValue({ data: entry({ variant_id: '2-200-C-T', source: 'curated' }) });
    renderPage();
    await waitFor(() => expect(entryRows()).toHaveLength(2));

    await user.click(screen.getByRole('button', { name: 'Add an entry' }));
    const dialog = screen.getByRole('dialog', { name: 'Add an entry' });
    const add = within(dialog).getByRole('button', { name: 'Add to the list' });
    await user.type(within(dialog).getByRole('textbox', { name: /Variant/ }), 'chr2:200:c');
    expect(add).toBeDisabled();
    await user.type(within(dialog).getByRole('textbox', { name: /Variant/ }), ':t');
    expect(within(dialog).getByText('Stored as 2-200-C-T.')).toBeInTheDocument();
    await user.type(within(dialog).getByRole('textbox', { name: 'Label' }), 'NTC run 14');
    await user.click(add);

    await waitFor(() =>
      expect(mockedPost).toHaveBeenCalledWith('/admin/nipt/artifacts', {
        assembly_id: GRCH38,
        assay_key: 'nipt_cfdna',
        variant_id: '2-200-C-T',
        label: 'NTC run 14',
        source: 'curated',
        recurrence_count: 0,
      }),
    );
    expect(
      await screen.findByText('Listed 2-200-C-T as a curated entry of the nipt_cfdna list.'),
    ).toBeInTheDocument();
  });

  it('says when the allele is listed already, and keeps its recurrence', async () => {
    const user = userEvent.setup();
    mockedPost.mockResolvedValue({ data: entry({ source: 'curated' }) });
    renderPage();
    await waitFor(() => expect(entryRows()).toHaveLength(2));

    await user.click(screen.getByRole('button', { name: 'Add an entry' }));
    const dialog = screen.getByRole('dialog', { name: 'Add an entry' });
    await user.type(within(dialog).getByRole('textbox', { name: /Variant/ }), '7-1000-A-G');
    expect(within(dialog).getByText(/is on the list already \(auto, recurrence 3\)/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Add to the list' }));

    await waitFor(() =>
      expect(mockedPost).toHaveBeenCalledWith(
        '/admin/nipt/artifacts',
        expect.objectContaining({ variant_id: '7-1000-A-G', label: null, recurrence_count: 3 }),
      ),
    );
  });

  it('removes an entry once confirmed, without asking a reason', async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(entryRows()).toHaveLength(2));

    await user.click(screen.getByRole('button', { name: 'Remove 17-41245466-G-A' }));
    const dialog = screen.getByRole('dialog', { name: 'Confirm the removal' });
    expect(within(dialog).getByText(/every NIPT analysis of the scope keeps the allele again/)).toBeInTheDocument();
    expect(within(dialog).queryByRole('textbox')).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Remove' }));

    await waitFor(() =>
      expect(mockedDelete).toHaveBeenCalledWith('/admin/nipt/artifacts/22222222-2222-4222-8222-222222222222'),
    );
    expect(await screen.findByText('Removed 17-41245466-G-A from the nipt_cfdna list.')).toBeInTheDocument();
  });

  it('removes nothing when the removal is cancelled', async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(entryRows()).toHaveLength(2));

    await user.click(screen.getByRole('button', { name: 'Remove 7-1000-A-G' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));
    expect(mockedDelete).not.toHaveBeenCalled();
  });
});
