import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';
import FamilyStructuralVariantsPage from '../FamilyStructuralVariantsPage';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

vi.mock('../../../lib/api', () => ({
  default: {
    get: vi.fn((url: string) => {
      if (url.startsWith('/families/F1/structural-variants?page=1&page_size=100')) {
        return Promise.resolve({
          data: {
            variants: [
              {
                _id: 'v1',
                chr: 'chr1',
                start: 1,
                end: 2,
                length: 1,
                type: 'DEL',
                annotation_extra: {
                  cytoband: '1p36.33',
                },
                genotypes: [],
              },
            ],
            total: 1,
          },
        });
      }
      if (url.startsWith('/families/F1/structural-variants?page=1&page_size=1')) {
        return Promise.resolve({ data: { variants: [], total: 5 } });
      }
      if (url === '/families/F1') {
        return Promise.resolve({
          data: {
            pedigree: 'F1\tS1\t0\t0\t1\t2',
            members: [
              {
                sample_id: 'S1',
                role: 'proband',
                affected: true,
                sex: 'male',
              },
            ],
            projects: [],
          },
        });
      }
      if (url === '/panels') {
        return Promise.resolve({ data: [] });
      }
      if (url === '/families/F1/structural-variant-filter-presets') {
        return Promise.resolve({ data: [] });
      }
      if (url === '/families/F1/structural-variant-tags') {
        return Promise.resolve({
          data: [
            {
              key: 'review',
              label: 'Review',
              group: 'collaboration',
              color: '#2563eb',
              sort_order: 10,
              scope: 'system',
              shared_project_ids: [],
              is_custom: false,
            },
            {
              key: 'excluded',
              label: 'Excluded',
              group: 'collaboration',
              color: '#6b7280',
              sort_order: 20,
              scope: 'system',
              shared_project_ids: [],
              is_custom: false,
            },
          ],
        });
      }
      return Promise.resolve({ data: [] });
    }),
    put: vi.fn(() =>
      Promise.resolve({
        data: {
          variant_id: 'v1',
          classification: null,
          tags: ['review'],
          tag_metadata: {},
          note: null,
        },
      }),
    ),
  },
}));

describe('FamilyStructuralVariantsPage', () => {
  it('shows filtered and overall variant counts', async () => {
    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1/structural-variants']}>
          <Routes>
            <Route path="/families/:familyId/structural-variants" element={<FamilyStructuralVariantsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );
    await waitFor(() =>
      expect(screen.getByText(/Showing 1/)).toBeInTheDocument()
    );
    expect(screen.getByText(/All variants 5/)).toBeInTheDocument();
    expect(screen.getByText(/Pedigree/)).toBeInTheDocument();
    expect(screen.getByText(/Tag library 2/)).toBeInTheDocument();
    expect(screen.getByText('1p36.33')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('Gene or region')).toBeInTheDocument();
    expect(screen.getByText(/Saved filters/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /apply filters/i })).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /review/i }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole('button', { name: /clear all filters/i }).length).toBeGreaterThan(0);
  });

  it('can save a quick review tag without updating count-only cache entries as variant pages', async () => {
    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1/structural-variants']}>
          <Routes>
            <Route path="/families/:familyId/structural-variants" element={<FamilyStructuralVariantsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    await waitFor(() => expect(screen.getByText(/Showing 1/)).toBeInTheDocument());
    fireEvent.click(screen.getAllByRole('button', { name: /^review$/i })[0]);

    await waitFor(() => expect(screen.getByText(/Variant review saved/i)).toBeInTheDocument());
  });
});
