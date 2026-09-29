// Pins the HPO terminology admin page: the installed release and its gaps, the term browser
// (search, details, 20-child cap), and that a sync previews freely but applies only once confirmed.
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import HpoTerminologyAdminPage from '../HpoTerminologyAdminPage';
import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

vi.mock('../../../lib/api', () => ({ default: { get: vi.fn(), post: vi.fn() } }));

const mockedGet = vi.mocked(api.get);
const mockedPost = vi.mocked(api.post);

type Relation = { hpo_id: string; label: string; relation: string };
type Term = {
  hpo_id: string;
  label: string;
  definition: string | null;
  is_obsolete: boolean;
  replaced_by: string | null;
  synonyms: string[];
  parents: Relation[];
  children: Relation[];
  parent_count: number;
  child_count: number;
};

const SUMMARY = {
  total_terms: 18954,
  active_terms: 17902,
  obsolete_terms: 1052,
  release_version: 'hp/releases/2026-06-06',
  release_date: '2026-06-06',
  last_sync_date: '2026-07-01T09:30:00Z',
  automatic_update_supported: false,
  ontology_loaded: true,
};

const SEIZURE: Term = {
  hpo_id: 'HP:0001250',
  label: 'Seizure',
  definition: 'An intermittent abnormality of nervous system physiology.',
  is_obsolete: false,
  replaced_by: null,
  synonyms: ['Seizures', 'Epileptic seizure'],
  parents: [{ hpo_id: 'HP:0012638', label: 'Abnormal nervous system physiology', relation: 'is_a' }],
  children: [
    { hpo_id: 'HP:0020219', label: 'Motor seizure', relation: 'is_a' },
    { hpo_id: 'HP:0002121', label: 'Generalized non-motor seizure', relation: 'is_a' },
  ],
  parent_count: 1,
  child_count: 2,
};

const OBSOLETE: Term = {
  hpo_id: 'HP:0100000',
  label: 'obsolete Fits',
  definition: null,
  is_obsolete: true,
  replaced_by: 'HP:0001250',
  synonyms: [],
  parents: [],
  children: [],
  parent_count: 0,
  child_count: 0,
};

const apiError = (detail: string) => ({ response: { status: 400, data: { detail } } });

// What the two GETs answer; a Promise is handed back as-is (pending or rejected).
let summaryReply: () => unknown;
let termsReply: (q: string | undefined) => unknown;

const reply = (value: unknown) => (value instanceof Promise ? value : Promise.resolve({ data: value }));

const renderPage = () => {
  const queryClient = createTestQueryClient();
  const view = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <HpoTerminologyAdminPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { queryClient, ...view };
};

const statValue = (label: string) =>
  within(screen.getByRole('region', { name: 'HPO statistics' })).getByText(label).nextElementSibling
    ?.textContent;

const readTable = (table: HTMLElement) =>
  within(table)
    .getAllByRole('row')
    .map((row) => Array.from(row.querySelectorAll('th, td')).map((cell) => cell.textContent));

const termsTable = () => screen.getByRole('columnheader', { name: 'HPO ID' }).closest('table') as HTMLElement;

/** The details panel beside the table, found through the selected term's heading. */
const details = () => screen.getByRole('heading', { level: 3 }).closest('aside') as HTMLElement;

const listUnder = (panel: HTMLElement, heading: string) =>
  within(within(panel).getByRole('heading', { name: heading, level: 4 }).parentElement as HTMLElement)
    .getAllByRole('listitem')
    .map((item) => item.textContent);

const searchBox = () => screen.getByRole('textbox', { name: /search terms/i });

const termCalls = (q?: string) =>
  mockedGet.mock.calls.filter(
    ([url, config]) =>
      url === '/admin/hpo/terms' && (config as { params?: { q?: string } } | undefined)?.params?.q === q,
  );

describe('HpoTerminologyAdminPage', () => {
  beforeEach(() => {
    mockedGet.mockReset();
    mockedPost.mockReset();
    summaryReply = () => SUMMARY;
    termsReply = () => [SEIZURE, OBSOLETE];
    mockedGet.mockImplementation((url: string, config?: { params?: unknown }) => {
      if (url === '/admin/hpo/summary') return reply(summaryReply());
      if (url === '/admin/hpo/terms') return reply(termsReply((config?.params as { q?: string } | undefined)?.q));
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('release status', () => {
    it('shows the installed release: term counts, version, dates and the update mode', async () => {
      renderPage();

      expect(await screen.findByRole('heading', { name: 'HPO terminology' })).toBeInTheDocument();
      expect(statValue('Total terms')).toBe('18,954');
      expect(statValue('Active')).toBe('17,902');
      expect(statValue('Obsolete')).toBe('1,052');
      expect(statValue('Release')).toBe('hp/releases/2026-06-06');
      expect(statValue('Release date')).toMatch(/2026/);
      expect(statValue('Last sync')).toBe(new Date('2026-07-01T09:30:00Z').toLocaleString());
      expect(screen.getByText('Automatic updates not configured')).toBeInTheDocument();
      expect(screen.queryByText(/No HPO terminology is installed/)).not.toBeInTheDocument();
    });

    it('shows a date-only release date on its own day, also west of UTC (#526)', async () => {
      // 2026-06-06 parsed as UTC midnight read as 6/5/2026 in New York.
      const previousTz = process.env.TZ;
      process.env.TZ = 'America/New_York';
      try {
        renderPage();
        await screen.findByText('Total terms');
        expect(statValue('Release date')).toBe(
          new Date(Date.UTC(2026, 5, 6)).toLocaleDateString(undefined, { timeZone: 'UTC' }),
        );
        expect(statValue('Release date')).not.toBe(new Date('2026-06-06').toLocaleDateString());
      } finally {
        process.env.TZ = previousTz;
      }
    });

    it('warns when no terminology is installed and reads the missing release plainly', async () => {
      summaryReply = () => ({
        total_terms: 0,
        active_terms: 0,
        obsolete_terms: 0,
        release_version: null,
        release_date: null,
        last_sync_date: null,
        automatic_update_supported: true,
        ontology_loaded: false,
      });
      termsReply = () => [];
      renderPage();

      expect(
        await screen.findByText(
          'No HPO terminology is installed. Preview and apply an HPO synchronization file to load terms.',
        ),
      ).toBeInTheDocument();
      expect(statValue('Total terms')).toBe('0');
      expect(statValue('Release')).toBe('Unknown');
      expect(statValue('Release date')).toBe('Not installed');
      expect(statValue('Last sync')).toBe('Not synced');
      expect(screen.getByText('Automatic updates available')).toBeInTheDocument();
    });

    it('shows a date it cannot parse as recorded, never as "Invalid Date"', async () => {
      summaryReply = () => ({ ...SUMMARY, release_date: '2026-13-45', last_sync_date: 'n/a' });
      renderPage();

      await screen.findByRole('heading', { name: 'HPO terminology' });
      expect(statValue('Release date')).toBe('2026-13-45');
      expect(statValue('Last sync')).toBe('n/a');
    });

    it('holds the page on a loading state until the summary arrives', async () => {
      summaryReply = () => new Promise(() => undefined);
      renderPage();

      expect(await screen.findByRole('heading', { name: 'Loading HPO terminology' })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Apply sync' })).not.toBeInTheDocument();
    });

    it('says why the summary could not be loaded, and offers no sync from there', async () => {
      summaryReply = () => Promise.reject(apiError('Admin privileges required'));
      const { unmount } = renderPage();

      expect(await screen.findByRole('heading', { name: 'Could not load HPO terminology' })).toBeInTheDocument();
      expect(screen.getByText('Admin privileges required')).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Apply sync' })).not.toBeInTheDocument();
      unmount();

      summaryReply = () => Promise.reject({});
      renderPage();
      expect(await screen.findByText('HPO summary could not be loaded.')).toBeInTheDocument();
    });
  });

  describe('term browser', () => {
    it('lists each term with its synonym, parent and child counts and status, the first one opened', async () => {
      renderPage();

      expect(await screen.findByText('2 shown')).toBeInTheDocument();
      expect(readTable(termsTable())).toEqual([
        ['HPO ID', 'Name', 'Synonyms', 'Parents', 'Children', 'Status'],
        ['HP:0001250', 'Seizure', '2', '1', '2', 'Active'],
        ['HP:0100000', 'obsolete Fits', '0', '0', '0', 'Obsolete'],
      ]);

      const panel = details();
      expect(within(panel).getByRole('heading', { level: 3 })).toHaveTextContent('Seizure');
      expect(within(panel).getByText('HP:0001250')).toBeInTheDocument();
      expect(within(panel).getByText(SEIZURE.definition as string)).toBeInTheDocument();
      expect(within(panel).getByText('Active')).toBeInTheDocument();
      expect(within(panel).getByText('Seizures, Epileptic seizure')).toBeInTheDocument();
      expect(listUnder(panel, 'Parent terms')).toEqual(['HP:0012638 Abnormal nervous system physiology']);
      expect(listUnder(panel, 'Child terms')).toEqual([
        'HP:0020219 Motor seizure',
        'HP:0002121 Generalized non-motor seizure',
      ]);
    });

    it('opens the clicked term, and says what replaced an obsolete one', async () => {
      const user = userEvent.setup();
      renderPage();

      await user.click(await screen.findByText('obsolete Fits'));

      const panel = details();
      expect(within(panel).getByRole('heading', { level: 3 })).toHaveTextContent('obsolete Fits');
      expect(within(panel).getByText('Obsolete')).toBeInTheDocument();
      expect(within(panel).getByText('Replaced by HP:0001250')).toBeInTheDocument();
      expect(within(panel).getByText('No definition is stored for this term.')).toBeInTheDocument();
      expect(within(panel).getByText('No synonyms stored.')).toBeInTheDocument();
      expect(listUnder(panel, 'Parent terms')).toEqual(['No parent terms stored.']);
      expect(listUnder(panel, 'Child terms')).toEqual(['No child terms stored.']);
    });

    it('lists at most 20 child terms and says how many there are', async () => {
      const children = Array.from({ length: 25 }, (_, index) => ({
        hpo_id: `HP:99000${String(index).padStart(2, '0')}`,
        label: `Child ${index + 1}`,
        relation: 'is_a',
      }));
      termsReply = () => [{ ...SEIZURE, children, child_count: 25 }];
      renderPage();

      await screen.findByText('1 shown');
      const shown = listUnder(details(), 'Child terms');
      expect(shown).toHaveLength(20);
      expect(shown[19]).toBe('HP:9900019 Child 20');
      expect(within(details()).getByText('Showing 20 of 25 child terms.')).toBeInTheDocument();
      // The table's count is the stored total, not the capped list.
      expect(readTable(termsTable())[1][4]).toBe('25');
    });

    it('searches with the trimmed query and a limit of 100, and sends no q for an empty search', async () => {
      termsReply = (q) => (q === 'seizure' ? [SEIZURE] : [SEIZURE, OBSOLETE]);
      renderPage();

      await screen.findByText('2 shown');
      expect(mockedGet).toHaveBeenCalledWith('/admin/hpo/terms', {
        params: { q: undefined, limit: 100 },
        signal: expect.any(AbortSignal),
      });

      // Typing searches on its own after a short pause.
      fireEvent.change(searchBox(), { target: { value: '  seizure  ' } });

      await waitFor(
        () =>
          expect(mockedGet).toHaveBeenCalledWith('/admin/hpo/terms', {
            params: { q: 'seizure', limit: 100 },
            signal: expect.any(AbortSignal),
          }),
        { timeout: 3000 },
      );
      expect(await screen.findByText('1 shown')).toBeInTheDocument();
    });

    it('searches at once on Enter, and Search re-runs an unchanged query', async () => {
      termsReply = (q) => (q === 'fits' ? [OBSOLETE] : [SEIZURE, OBSOLETE]);
      renderPage();
      await screen.findByText('2 shown');

      fireEvent.change(searchBox(), { target: { value: 'fits' } });
      fireEvent.keyDown(searchBox(), { key: 'Tab' });
      expect(termCalls('fits')).toHaveLength(0);
      fireEvent.keyDown(searchBox(), { key: 'Enter' });
      // Asserted without waiting: Enter does not sit out the typing pause.
      expect(termCalls('fits')).toHaveLength(1);

      expect(await screen.findByText('1 shown')).toBeInTheDocument();
      const before = termCalls('fits').length;

      fireEvent.click(screen.getByRole('button', { name: 'Search' }));

      await waitFor(() => expect(termCalls('fits')).toHaveLength(before + 1));
    });

    it('keeps the opened term across a refresh, while a new search opens its first result', async () => {
      const user = userEvent.setup();
      termsReply = (q) => (q === 'seizure' ? [SEIZURE] : [SEIZURE, OBSOLETE]);
      renderPage();
      await user.click(await screen.findByText('obsolete Fits'));

      await user.click(screen.getByRole('button', { name: 'Search' }));
      await waitFor(() => expect(termCalls(undefined)).toHaveLength(2));
      expect(within(details()).getByRole('heading', { level: 3 })).toHaveTextContent('obsolete Fits');

      fireEvent.change(searchBox(), { target: { value: 'seizure' } });
      fireEvent.keyDown(searchBox(), { key: 'Enter' });

      await waitFor(() =>
        expect(within(details()).getByRole('heading', { level: 3 })).toHaveTextContent('Seizure'),
      );
    });

    it('says why the terms could not be loaded', async () => {
      termsReply = () => Promise.reject(apiError('HPO tables are not available'));
      renderPage();

      expect(await screen.findByText('HPO tables are not available')).toHaveClass('status-note--error');
    });

    it('says when no term matches, with nothing opened', async () => {
      termsReply = () => [];
      renderPage();

      expect(await screen.findByText('0 shown')).toBeInTheDocument();
      expect(screen.getByText('No HPO terms match the current search.')).toBeInTheDocument();
      expect(screen.getByText('Select a term to view details.')).toBeInTheDocument();
    });
  });

  describe('synchronization', () => {
    it('previews without asking, sending the trimmed path and overrides, and labels the counts', async () => {
      const user = userEvent.setup();
      const confirm = vi.spyOn(window, 'confirm');
      mockedPost.mockResolvedValue({
        data: {
          preview_only: true,
          release_version: 'hp/releases/2026-09-01',
          release_date: '2026-09-01',
          current: SUMMARY,
          preview: {
            terms: 19120,
            new_terms: 166,
            changed_terms: 412,
            removed_from_release: 3,
            closure_rows: 412345,
            // A count the page has no label for yet still shows, under its key.
            unmapped_xrefs: 7,
          },
          imported: null,
        },
      });
      renderPage();

      const path = await screen.findByLabelText('Ontology file path');
      expect(path).toHaveValue('/data/ref-data/hpo/hpo.obo');
      await user.clear(path);
      await user.type(path, '  /data/ref-data/hpo/hp-2026-09-01.obo  ');
      await user.type(screen.getByLabelText('Release version override'), '  hp/releases/2026-09-01 ');
      fireEvent.change(screen.getByLabelText('Release date override'), { target: { value: '2026-09-01' } });
      await user.click(screen.getByRole('button', { name: 'Preview changes' }));

      await waitFor(() =>
        expect(mockedPost).toHaveBeenCalledWith('/admin/hpo/sync', {
          path: '/data/ref-data/hpo/hp-2026-09-01.obo',
          release_version: 'hp/releases/2026-09-01',
          release_date: '2026-09-01',
          preview_only: true,
        }),
      );
      expect(confirm).not.toHaveBeenCalled();
      expect(await screen.findByText('HPO synchronization preview is ready.')).toHaveClass(
        'status-note--success',
      );
      const preview = screen.getByRole('columnheader', { name: 'Change' }).closest('table') as HTMLElement;
      expect(readTable(preview)).toEqual([
        ['Change', 'Count'],
        ['Terms in release', '19,120'],
        ['New terms', '166'],
        ['Changed terms', '412'],
        ['Missing from new release', '3'],
        ['Closure rows', '412,345'],
        ['unmapped_xrefs', '7'],
      ]);
    });

    const PREVIEW_REPLY = {
      data: {
        preview_only: true,
        release_version: 'hp/releases/2026-09-01',
        release_date: '2026-09-01',
        current: SUMMARY,
        preview: { terms: 19120 },
        imported: null,
      },
    };

    it('applies only what was previewed: no Apply before a preview, nor after the path changes (#526)', async () => {
      const user = userEvent.setup();
      mockedPost.mockResolvedValue(PREVIEW_REPLY);
      renderPage();

      const apply = await screen.findByRole('button', { name: 'Apply sync' });
      // Apply used to send whatever the fields held, with or without a preview.
      expect(apply).toBeDisabled();
      expect(screen.getByText(/Preview these settings first/)).toBeInTheDocument();

      await user.click(screen.getByRole('button', { name: 'Preview changes' }));
      await waitFor(() => expect(screen.getByRole('button', { name: 'Apply sync' })).toBeEnabled());
      expect(screen.getByRole('columnheader', { name: 'Change' })).toBeInTheDocument();

      // Another file: the preview on screen no longer describes it, so it goes, and so
      // does Apply, until that file is previewed.
      const path = screen.getByLabelText('Ontology file path');
      await user.clear(path);
      await user.type(path, '/data/ref-data/hpo/other.obo');
      expect(screen.getByRole('button', { name: 'Apply sync' })).toBeDisabled();
      expect(screen.queryByRole('columnheader', { name: 'Change' })).not.toBeInTheDocument();
    });

    it('does not apply a sync the admin declines to confirm, and names the file it asks about', async () => {
      const user = userEvent.setup();
      const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
      mockedPost.mockResolvedValue(PREVIEW_REPLY);
      renderPage();

      await user.click(await screen.findByRole('button', { name: 'Preview changes' }));
      await waitFor(() => expect(screen.getByRole('button', { name: 'Apply sync' })).toBeEnabled());
      mockedPost.mockClear();
      await user.click(screen.getByRole('button', { name: 'Apply sync' }));

      expect(confirm).toHaveBeenCalledWith(
        'Apply the HPO ontology in /data/ref-data/hpo/hpo.obo now? The terminology is replaced as previewed.',
      );
      expect(mockedPost).not.toHaveBeenCalled();
      expect(screen.queryByText('HPO terminology synchronized.')).not.toBeInTheDocument();
    });

    it('labels only the running action as busy', async () => {
      const user = userEvent.setup();
      mockedPost.mockReturnValue(new Promise(() => undefined));
      renderPage();

      await user.click(await screen.findByRole('button', { name: 'Preview changes' }));
      // Both are disabled while a request runs, but only the preview says it is checking.
      expect(screen.getByRole('button', { name: 'Checking...' })).toBeDisabled();
      expect(screen.getByRole('button', { name: 'Apply sync' })).toBeDisabled();
      expect(screen.queryByRole('button', { name: 'Synchronizing...' })).not.toBeInTheDocument();
    });

    it('applies once confirmed, then shows the new release and invalidates cached HPO lookups', async () => {
      const user = userEvent.setup();
      vi.spyOn(window, 'confirm').mockReturnValue(true);
      const updated = { ...SUMMARY, total_terms: 19120, release_version: 'hp/releases/2026-09-01' };
      mockedPost.mockImplementation(async () => {
        summaryReply = () => updated;
        return {
          data: {
            preview_only: false,
            release_version: 'hp/releases/2026-09-01',
            release_date: '2026-09-01',
            current: updated,
            preview: { terms: 19120 },
            imported: { terms: 19120, synonyms: 30211, edges: 23554, closure_rows: 412345 },
          },
        };
      });
      const { queryClient } = renderPage();
      // A phenotype lookup cached by another page (e.g. a family's HPO picker).
      queryClient.setQueryData(['hpo', 'search', 'seiz'], [SEIZURE]);

      // Apply follows a preview of the same settings.
      mockedPost.mockResolvedValueOnce(PREVIEW_REPLY);
      await user.click(await screen.findByRole('button', { name: 'Preview changes' }));
      await waitFor(() => expect(screen.getByRole('button', { name: 'Apply sync' })).toBeEnabled());
      await user.click(screen.getByRole('button', { name: 'Apply sync' }));

      await waitFor(() =>
        expect(mockedPost).toHaveBeenCalledWith('/admin/hpo/sync', {
          path: '/data/ref-data/hpo/hpo.obo',
          release_version: null,
          release_date: null,
          preview_only: false,
        }),
      );
      expect(await screen.findByText('HPO terminology synchronized.')).toHaveClass('status-note--success');
      await waitFor(() => expect(statValue('Release')).toBe('hp/releases/2026-09-01'));
      expect(statValue('Total terms')).toBe('19,120');
      expect(termCalls(undefined).length).toBeGreaterThan(1);
      expect(queryClient.getQueryState(['hpo', 'search', 'seiz'])?.isInvalidated).toBe(true);
    });

    it("shows the server's reason when a sync fails, or a plain fallback", async () => {
      const user = userEvent.setup();
      mockedPost.mockRejectedValueOnce(apiError('Path is outside the configured HPO directory'));
      mockedPost.mockRejectedValueOnce({});
      renderPage();

      await user.click(await screen.findByRole('button', { name: 'Preview changes' }));
      expect(await screen.findByText('Path is outside the configured HPO directory')).toHaveClass(
        'status-note--error',
      );

      await user.click(screen.getByRole('button', { name: 'Preview changes' }));
      expect(await screen.findByText('HPO synchronization failed.')).toHaveClass('status-note--error');
    });

    it('needs a file path, and locks both actions while a request is running', async () => {
      const user = userEvent.setup();
      mockedPost.mockReturnValue(new Promise(() => undefined));
      renderPage();

      const path = await screen.findByLabelText('Ontology file path');
      const preview = screen.getByRole('button', { name: 'Preview changes' });
      const apply = screen.getByRole('button', { name: 'Apply sync' });

      await user.clear(path);
      expect(preview).toBeDisabled();
      expect(apply).toBeDisabled();
      await user.type(path, '   ');
      expect(preview).toBeDisabled();
      expect(apply).toBeDisabled();

      await user.type(path, '/data/ref-data/hpo/hp.obo');
      expect(preview).toBeEnabled();
      await user.click(preview);

      expect(preview).toHaveTextContent('Checking...');
      expect(preview).toBeDisabled();
      expect(apply).toBeDisabled();
      expect(mockedPost).toHaveBeenCalledTimes(1);
    });
  });
});
