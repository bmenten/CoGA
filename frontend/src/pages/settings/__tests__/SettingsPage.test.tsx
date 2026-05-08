import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
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

beforeEach(() => {
  localStorage.clear();
  vi.mocked(api.get).mockReset();
  vi.mocked(api.delete).mockReset();
  vi.mocked(api.get).mockImplementation(async (url: string) => {
    if (url === '/auth/small-variant-filter-presets') {
      return Promise.resolve({
        data: [
          {
            _id: 'preset-1',
            family_id: null,
            scope: 'global',
            owner: 'reviewer',
            name: 'Dominant shortlist',
            description: 'Clinical dominant review filter',
            filters: { impact: ['HIGH'] },
            sample_filters: {},
            sample_templates: {},
            created_at: '2026-04-14T10:00:00Z',
            updated_at: '2026-04-14T10:00:00Z',
          },
        ],
      });
    }
    return Promise.resolve({ data: [] });
  });
  vi.mocked(api.delete).mockResolvedValue({ data: {} });
});

test('saves window sizes and shows saved small-variant filters', async () => {
  localStorage.setItem('genomeWindow', '123');
  localStorage.setItem('chromosomeWindow', '456');
  localStorage.setItem('coverageUpperThreshold', '0.5');
  localStorage.setItem('coverageLowerThreshold', '-0.5');
  localStorage.setItem('coverageRange', '2');

  const queryClient = createTestQueryClient({
    defaultOptions: {
      queries: {
        staleTime: Number.POSITIVE_INFINITY,
      },
    },
  });
  queryClient.setQueryData(['auth', 'small-variant-filter-presets'], [
    {
      _id: 'preset-1',
      family_id: null,
      scope: 'global',
      owner: 'reviewer',
      name: 'Dominant shortlist',
      description: 'Clinical dominant review filter',
      filters: { impact: ['HIGH'] },
      sample_filters: {},
      sample_templates: {},
      created_at: '2026-04-14T10:00:00Z',
      updated_at: '2026-04-14T10:00:00Z',
    },
  ]);

  render(
    <QueryClientProvider client={queryClient}>
      <SettingsPage />
    </QueryClientProvider>,
  );

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

  expect(localStorage.getItem('coverageUpperThreshold')).toBe('0.75');
  expect(localStorage.getItem('coverageRange')).toBe('3');

  expect(await screen.findByText('Saved small-variant filters')).toBeInTheDocument();
  expect(screen.getByText('Dominant shortlist')).toBeInTheDocument();
  expect(screen.getByText('Clinical dominant review filter')).toBeInTheDocument();
});
