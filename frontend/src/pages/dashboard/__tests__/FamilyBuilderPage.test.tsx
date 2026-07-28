import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import api from '../../../lib/api';
import { storage } from '../../../lib/storage';
import { createTestQueryClient } from '../../../test/createTestQueryClient';
import FamilyBuilderPage from '../FamilyBuilderPage';

vi.mock('../../../lib/api', () => ({
  default: {
    get: vi.fn((url: string) => {
      if (url === '/projects') {
        return Promise.resolve({
          data: [
            {
              id: '11111111-1111-1111-1111-111111111111',
              name: 'Accessible Project',
              families: [],
              samples: [],
            },
          ],
        });
      }
      return Promise.resolve({ data: [] });
    }),
    post: vi.fn(),
  },
}));

const renderPage = () => {
  const queryClient = createTestQueryClient();
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <FamilyBuilderPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe('FamilyBuilderPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (api.post as any).mockReset();
    storage.clear();
  });

  it('renders the family builder workspace with a dashboard back link', () => {
    renderPage();

    expect(screen.getByRole('heading', { name: 'Family Builder', level: 1 })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /back to dashboard/i })).toHaveAttribute(
      'href',
      '/dashboard'
    );
  });

  it('submits the manual family builder and renders the pedigree sketch', async () => {
    (api.post as any).mockResolvedValue({
      data: {
        families: [{ family_id: 'FAM-100', samples: ['PROB-1'] }],
      },
    });

    renderPage();

    await waitFor(() =>
      expect(screen.getAllByLabelText(/^project$/i)[0]).toHaveValue(
        '11111111-1111-1111-1111-111111111111'
      )
    );

    fireEvent.change(screen.getByLabelText(/family id/i), {
      target: { value: 'FAM-100' },
    });
    fireEvent.change(screen.getByLabelText(/sample id/i), {
      target: { value: 'PROB-1' },
    });
    fireEvent.click(screen.getByLabelText(/^affected$/i));
    fireEvent.click(screen.getByLabelText(/^carrier$/i));
    fireEvent.change(screen.getByLabelText(/carrier type/i), {
      target: { value: 'obligate' },
    });

    await waitFor(() => {
      const sketch = screen.getByTestId('pedigree-sketch');
      expect(sketch.querySelector('svg')).not.toBeNull();
    });

    fireEvent.click(screen.getByRole('button', { name: /create family/i }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/ped/manual', {
        family_id: 'FAM-100',
        project_id: '11111111-1111-1111-1111-111111111111',
        members: [
          {
            sample_id: 'PROB-1',
            father_id: null,
            mother_id: null,
            sex: 'und',
            clinical_status: 'unaffected',
            affected: false,
            is_proband: true,
            carrier_status: 'carrier',
            carrier_type: 'obligate',
          },
        ],
        couples: [],
      })
    );

    expect(
      screen.getByText(/created fam-100 with 1 sample.*accessible project/i)
    ).toBeInTheDocument();
  });

  it('submits explicit partner relationships from the manual builder', async () => {
    (api.post as any).mockResolvedValue({
      data: {
        families: [{ family_id: 'FAM-COUPLE', samples: ['PARTNER-F', 'PARTNER-M'] }],
      },
    });

    renderPage();

    await waitFor(() =>
      expect(screen.getAllByLabelText(/^project$/i)[0]).toHaveValue(
        '11111111-1111-1111-1111-111111111111'
      )
    );

    fireEvent.change(screen.getByLabelText(/family id/i), {
      target: { value: 'FAM-COUPLE' },
    });
    fireEvent.change(screen.getByLabelText(/sample id/i), {
      target: { value: 'PARTNER-F' },
    });
    fireEvent.change(screen.getByLabelText(/^sex$/i), {
      target: { value: 'female' },
    });
    fireEvent.click(screen.getByRole('button', { name: /add father/i }));
    fireEvent.change(screen.getAllByLabelText(/sample id/i)[1], {
      target: { value: 'PARTNER-M' },
    });
    fireEvent.click(screen.getByRole('button', { name: /add couple/i }));
    fireEvent.change(screen.getByLabelText(/^context$/i), {
      target: { value: 'ECS' },
    });
    fireEvent.click(screen.getByRole('button', { name: /create family/i }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/ped/manual', {
        family_id: 'FAM-COUPLE',
        project_id: '11111111-1111-1111-1111-111111111111',
        members: [
          {
            sample_id: 'PARTNER-F',
            father_id: null,
            mother_id: null,
            sex: 'female',
            clinical_status: 'affected',
            affected: true,
            is_proband: true,
            carrier_status: 'not_carrier',
            carrier_type: null,
          },
          {
            sample_id: 'PARTNER-M',
            father_id: null,
            mother_id: null,
            sex: 'male',
            clinical_status: 'unaffected',
            affected: false,
            is_proband: false,
            carrier_status: 'not_carrier',
            carrier_type: null,
          },
        ],
        couples: [
          {
            partners: ['PARTNER-M', 'PARTNER-F'],
            context: 'ECS',
            metadata: {},
          },
        ],
      })
    );
  });

  it('tags a monogenic NIPT family and its cfDNA sample', async () => {
    (api.post as any).mockResolvedValue({
      data: { families: [{ family_id: 'FAM-NIPT', samples: ['CFDNA-1'] }] },
    });

    renderPage();

    await waitFor(() =>
      expect(screen.getAllByLabelText(/^project$/i)[0]).toHaveValue(
        '11111111-1111-1111-1111-111111111111'
      )
    );

    fireEvent.change(screen.getByLabelText(/family id/i), {
      target: { value: 'FAM-NIPT' },
    });
    fireEvent.change(screen.getByLabelText(/sample id/i), {
      target: { value: 'CFDNA-1' },
    });
    // The cfDNA toggle only appears once the family is marked monogenic NIPT.
    fireEvent.click(screen.getByLabelText(/monogenic nipt/i));
    fireEvent.click(screen.getByLabelText(/cfdna \(maternal plasma\)/i));
    fireEvent.click(screen.getByRole('button', { name: /create family/i }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        '/ped/manual',
        expect.objectContaining({
          family_id: 'FAM-NIPT',
          metadata: { analysis_type: 'monogenic_nipt' },
          members: expect.arrayContaining([
            expect.objectContaining({
              sample_id: 'CFDNA-1',
              metadata: { assay: 'nipt_cfdna' },
            }),
          ]),
        })
      )
    );
  });
});
