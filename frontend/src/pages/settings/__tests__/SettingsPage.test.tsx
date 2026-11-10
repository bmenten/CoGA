import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';
import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';
import SettingsPage from '../SettingsPage';

vi.mock('../../../lib/api', () => ({
  default: {
    get: vi.fn(),
    delete: vi.fn(),
  },
}));

const PRESET = {
  _id: 'preset-1',
  owner: 'reviewer',
  name: 'Dominant shortlist',
  description: 'Clinical dominant review filter',
  filters: { impact: ['HIGH'] },
  sample_filters: {},
  sample_templates: {},
  created_at: '2026-04-14T10:00:00Z',
  updated_at: '2026-04-14T10:00:00Z',
};

const renderPage = (queryClient: QueryClient = createTestQueryClient()) =>
  render(
    <QueryClientProvider client={queryClient}>
      <SettingsPage />
    </QueryClientProvider>,
  );

/** Every stored setting the page saves, by its storage key. */
const savedSettings = () =>
  Object.fromEntries(
    ['genomeWindow', 'chromosomeWindow', 'coverageUpperThreshold', 'coverageLowerThreshold', 'coverageRange'].map(
      (key) => [key, localStorage.getItem(key)],
    ),
  );

const storeSettings = () => {
  localStorage.setItem('genomeWindow', '123');
  localStorage.setItem('chromosomeWindow', '456');
  localStorage.setItem('coverageUpperThreshold', '0.5');
  localStorage.setItem('coverageLowerThreshold', '-0.5');
  localStorage.setItem('coverageRange', '2');
};

beforeEach(() => {
  localStorage.clear();
  vi.mocked(api.get).mockReset();
  vi.mocked(api.delete).mockReset();
  vi.mocked(api.get).mockImplementation(async (url: string) => {
    if (url === '/auth/small-variant-filter-presets') {
      return Promise.resolve({ data: [PRESET] });
    }
    return Promise.resolve({ data: [] });
  });
  vi.mocked(api.delete).mockResolvedValue({ data: {} });
});

test('saves window sizes and shows saved small-variant filters', async () => {
  storeSettings();

  const queryClient = createTestQueryClient({
    defaultOptions: {
      queries: {
        staleTime: Number.POSITIVE_INFINITY,
      },
    },
  });
  queryClient.setQueryData(['auth', 'small-variant-filter-presets'], [PRESET]);

  renderPage(queryClient);

  const genomeInput = screen.getByLabelText(/Genome view window size/i) as HTMLInputElement;
  const chromInput = screen.getByLabelText(/Chromosome view window size/i) as HTMLInputElement;
  const upperInput = screen.getByLabelText(/Coverage upper threshold/i) as HTMLInputElement;
  const lowerInput = screen.getByLabelText(/Coverage lower threshold/i) as HTMLInputElement;
  const rangeInput = screen.getByLabelText(/Coverage range \(±\)/i) as HTMLInputElement;
  expect(genomeInput.value).toBe('123');
  expect(chromInput.value).toBe('456');
  expect(upperInput.value).toBe('0.5');
  expect(lowerInput.value).toBe('-0.5');
  expect(rangeInput.value).toBe('2');

  fireEvent.change(upperInput, { target: { value: '0.75' } });
  fireEvent.change(rangeInput, { target: { value: '3' } });
  fireEvent.click(screen.getByText('Save'));

  // The changed settings are saved, and the others stay as they were.
  expect(savedSettings()).toEqual({
    genomeWindow: '123',
    chromosomeWindow: '456',
    coverageUpperThreshold: '0.75',
    coverageLowerThreshold: '-0.5',
    coverageRange: '3',
  });
  expect(screen.getByText('Settings saved')).toBeInTheDocument();

  expect(await screen.findByText('Saved small-variant filters')).toBeInTheDocument();
  expect(screen.getByText('Dominant shortlist')).toBeInTheDocument();
  expect(screen.getByText('Clinical dominant review filter')).toBeInTheDocument();
});

test.each([
  [/Genome view window size/i, 'genomeWindow', '250000'],
  [/Chromosome view window size/i, 'chromosomeWindow', '5000'],
  [/Coverage upper threshold/i, 'coverageUpperThreshold', '0.4'],
  [/Coverage lower threshold/i, 'coverageLowerThreshold', '-0.4'],
  [/Coverage range \(±\)/i, 'coverageRange', '1'],
])('saves a changed %s under its own key only', (label, key, value) => {
  storeSettings();
  const before = savedSettings();
  vi.useFakeTimers();
  renderPage();

  fireEvent.change(screen.getByLabelText(label), { target: { value } });
  // Nothing is stored before Save.
  expect(savedSettings()).toEqual(before);
  fireEvent.click(screen.getByText('Save'));

  expect(savedSettings()).toEqual({ ...before, [key]: value });
  // The note goes after three seconds.
  expect(screen.getByText('Settings saved')).toBeInTheDocument();
  act(() => {
    vi.advanceTimersByTime(3000);
  });
  expect(screen.queryByText('Settings saved')).not.toBeInTheDocument();
});

// The coverage thresholds decide which bins are coloured as gain or loss: a reset puts back
// the defaults (it removes the stored values) and leaves the range.
test('resets the coverage thresholds to the defaults, and keeps the range', () => {
  storeSettings();
  vi.useFakeTimers();
  renderPage();

  fireEvent.click(screen.getByRole('button', { name: 'Reset coverage thresholds' }));

  expect(screen.getByLabelText(/Coverage upper threshold/i)).toHaveValue(0.35);
  expect(screen.getByLabelText(/Coverage lower threshold/i)).toHaveValue(-0.35);
  expect(screen.getByLabelText(/Coverage range \(±\)/i)).toHaveValue(2);
  expect(localStorage.getItem('coverageUpperThreshold')).toBeNull();
  expect(localStorage.getItem('coverageLowerThreshold')).toBeNull();
  expect(localStorage.getItem('coverageRange')).toBe('2');
  expect(screen.getByText('Coverage thresholds reset to the defaults')).toBeInTheDocument();

  // The note goes after three seconds.
  act(() => {
    vi.advanceTimersByTime(3000);
  });
  expect(screen.queryByText('Coverage thresholds reset to the defaults')).not.toBeInTheDocument();
});

test('removes a saved filter under its own path, and reloads the list', async () => {
  renderPage();

  fireEvent.click(await screen.findByRole('button', { name: 'Delete' }));

  expect(await screen.findByText('Saved filter removed.')).toBeInTheDocument();
  expect(api.delete).toHaveBeenCalledWith('/auth/small-variant-filter-presets/preset-1');
  await waitFor(() =>
    expect(vi.mocked(api.get).mock.calls.filter(([url]) => url === '/auth/small-variant-filter-presets')).toHaveLength(2),
  );
});

test('gives the Delete button back when removing a saved filter fails', async () => {
  vi.mocked(api.delete).mockRejectedValueOnce({ response: { status: 500, data: { detail: 'The database is unavailable.' } } });
  renderPage();

  fireEvent.click(await screen.findByRole('button', { name: 'Delete' }));

  expect(await screen.findByText('The database is unavailable.')).toBeInTheDocument();
  const button = screen.getByRole('button', { name: 'Delete' });
  expect(button).toBeEnabled();
  expect(screen.queryByText('Removing…')).not.toBeInTheDocument();
});
