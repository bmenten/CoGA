import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import api from '../../../lib/api';
import { AUTH_STORAGE_KEYS } from '../../../lib/auth';
import { storage } from '../../../lib/storage';
import { createTestQueryClient } from '../../../test/createTestQueryClient';
import FamilyIntakePage from '../FamilyIntakePage';

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
      if (url === '/family-imports/job-1') {
        return Promise.resolve({
          data: {
            _id: 'job-1',
            submitted_path: '/data/FAM-100',
            family_id: 'FAM-100',
            status: 'completed',
            dry_run: true,
            requested_by: 'admin@example.com',
            requested_at: '2026-04-29T12:00:00Z',
            heartbeat_at: '2026-04-29T12:00:01Z',
            validation_errors: [],
            validation_warnings: [],
            logs: ['Dry run completed successfully; no data were imported.'],
            datasets: [
              {
                dataset_type: 'snv',
                enabled: false,
                status: 'skipped',
                files: [],
                samples: [],
                summary: {},
              },
            ],
          },
        });
      }
      return Promise.resolve({ data: [] });
    }),
    post: vi.fn(),
  },
}));

const renderFamilyIntakePage = () => {
  const queryClient = createTestQueryClient();
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <FamilyIntakePage />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe('FamilyIntakePage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (api.post as any).mockReset();
    storage.clear();
  });

  it('renders the dedicated intake workspace with a dashboard back link', () => {
    renderFamilyIntakePage();

    expect(screen.getByRole('heading', { name: /family intake/i })).toBeInTheDocument();
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

    renderFamilyIntakePage();

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
            affected: true,
            is_proband: true,
          },
        ],
      })
    );

    expect(
      screen.getByText(/created fam-100 with 1 sample.*accessible project/i)
    ).toBeInTheDocument();
  });

  it('starts an admin package dry run from a folder path', async () => {
    storage.setItem(AUTH_STORAGE_KEYS.role, 'admin');
    (api.post as any).mockResolvedValue({
      data: {
        _id: 'job-1',
        submitted_path: '/data/FAM-100',
        family_id: null,
        status: 'queued',
        dry_run: true,
        requested_by: 'admin@example.com',
        requested_at: '2026-04-29T12:00:00Z',
        validation_errors: [],
        validation_warnings: [],
        logs: [],
        datasets: [],
      },
    });

    renderFamilyIntakePage();

    expect(screen.getByRole('heading', { name: /folder package/i })).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getAllByLabelText(/^project$/i)[0]).toHaveValue(
        '11111111-1111-1111-1111-111111111111'
      )
    );

    fireEvent.change(screen.getByLabelText(/family folder path/i), {
      target: { value: '/data/FAM-100' },
    });
    fireEvent.click(screen.getByRole('button', { name: /validate package/i }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/family-imports', {
        folder_path: '/data/FAM-100',
        project_id: '11111111-1111-1111-1111-111111111111',
        dry_run: true,
        family_id: null,
        conflict_mode: 'cancel',
      })
    );
    expect(screen.getByText(/started dry-run job job-1/i)).toBeInTheDocument();
  });

  it('discovers and writes a manifest draft for admins', async () => {
    storage.setItem(AUTH_STORAGE_KEYS.role, 'admin');
    (api.post as any).mockImplementation((url: string, payload: any) => {
      if (url === '/family-imports/manifest/discover') {
        return Promise.resolve({
          data: {
            valid: true,
            family_id: 'FAM-100',
            ped_path: 'family.ped',
            manifest_path: '/data/FAM-100/manifest.yaml',
            naming_scheme: 'standard_v1',
            sample_ids: ['S1'],
            manifest_yaml: 'schema_version: 1\nfamily_id: FAM-100\nped: family.ped\n',
            datasets: [
              {
                dataset_type: 'snv',
                enabled: true,
                complete: true,
                files: [
                  {
                    role: 'family_vcf',
                    path: 'snv/FAM-100.annotated.vcf.gz',
                    exists: true,
                  },
                ],
                samples: [],
                message: 'Available',
              },
            ],
            errors: [],
            warnings: [],
            metadata: {},
          },
        });
      }
      if (url === '/family-imports/manifest/write') {
        return Promise.resolve({
          data: {
            manifest_path: '/data/FAM-100/manifest.yaml',
            validation: {
              valid: true,
              errors: [],
              warnings: [],
            },
          },
        });
      }
      return Promise.resolve({ data: payload });
    });

    renderFamilyIntakePage();

    fireEvent.change(screen.getByLabelText(/family folder path/i), {
      target: { value: '/data/FAM-100' },
    });
    fireEvent.change(screen.getByLabelText(/ped path/i), {
      target: { value: 'family.ped' },
    });
    fireEvent.change(screen.getByLabelText(/hpo terms/i), {
      target: { value: 'HP:0001250' },
    });
    fireEvent.click(screen.getByRole('button', { name: /discover manifest/i }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/family-imports/manifest/discover', {
        folder_path: '/data/FAM-100',
        ped_path: 'family.ped',
        family_id: null,
        naming_scheme: 'standard_v1',
        hpo_terms: ['HP:0001250'],
        notes: null,
      })
    );
    expect(screen.getByLabelText(/manifest.yaml preview/i)).toHaveValue(
      'schema_version: 1\nfamily_id: FAM-100\nped: family.ped\n'
    );

    fireEvent.click(screen.getByRole('button', { name: /write manifest.yaml/i }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/family-imports/manifest/write', {
        folder_path: '/data/FAM-100',
        manifest_yaml: 'schema_version: 1\nfamily_id: FAM-100\nped: family.ped\n',
        overwrite: false,
      })
    );
    expect(screen.getByText(/wrote \/data\/fam-100\/manifest.yaml/i)).toBeInTheDocument();
  });
});
