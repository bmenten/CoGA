import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import api from '../../../lib/api';
import { AUTH_STORAGE_KEYS } from '../../../lib/auth';
import { storage } from '../../../lib/storage';
import { createTestQueryClient } from '../../../test/createTestQueryClient';
import { useFactoryGet } from '../../../test/factoryGet';
import { ACCESSIBLE_PROJECT_ID, waitForProject } from '../../../test/projectSelect';
import PackageImportPage from '../PackageImportPage';

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
      if (url === '/family-imports/packages') {
        return Promise.resolve({
          data: [
            {
              folder_path: '/data/families/FAM_NIPT_DEMO',
              name: 'FAM_NIPT_DEMO',
              family_id: 'FAM_NIPT_DEMO',
              has_manifest: true,
              has_ped: true,
              analysis_type: 'monogenic_nipt',
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
        <PackageImportPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe('PackageImportPage', () => {
  const { get, factory } = useFactoryGet(api.get);

  beforeEach(() => {
    vi.mocked(api.post).mockReset();
    storage.clear();
    storage.setItem(AUTH_STORAGE_KEYS.role, 'admin');
  });

  it('renders the package import workspace with a dashboard back link', () => {
    renderPage();

    expect(screen.getByRole('heading', { name: 'Package import', level: 1 })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /folder package/i })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /back to dashboard/i })).toHaveAttribute(
      'href',
      '/dashboard'
    );
  });

  it('starts an admin package dry run from a folder path', async () => {
    vi.mocked(api.post).mockResolvedValue({
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

    renderPage();
    await waitForProject();

    fireEvent.change(screen.getByLabelText(/family folder path/i), {
      target: { value: '/data/FAM-100' },
    });
    fireEvent.click(screen.getByRole('button', { name: /validate package/i }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/family-imports', {
        folder_path: '/data/FAM-100',
        project_id: ACCESSIBLE_PROJECT_ID,
        dry_run: true,
        family_id: null,
        conflict_mode: 'cancel',
      })
    );
    expect(screen.getByText(/started dry-run job job-1/i)).toBeInTheDocument();
  });

  it('fills the folder path from the scanned family dropdown', async () => {
    renderPage();

    const select = await screen.findByLabelText(/discovered family folder/i);
    await waitFor(() =>
      expect(
        screen.getByRole('option', { name: /FAM_NIPT_DEMO · monogenic_nipt/i })
      ).toBeInTheDocument()
    );

    fireEvent.change(select, { target: { value: '/data/families/FAM_NIPT_DEMO' } });

    expect(screen.getByLabelText(/family folder path/i)).toHaveValue(
      '/data/families/FAM_NIPT_DEMO'
    );
  });

  // #610 — a failed folder scan read "No families found in the import folder".
  it('says the import folder could not be scanned, not that it is empty', async () => {
    get.mockImplementation((url, config) =>
      url === '/family-imports/packages'
        ? Promise.reject(
            Object.assign(new Error('HTTP 500'), {
              response: { status: 500, data: { detail: 'Import folder is not mounted' } },
            }),
          )
        : factory(url, config),
    );
    renderPage();

    expect(
      await screen.findByText(/Could not load the import folder's families — this is not an empty result/),
    ).toHaveTextContent('Import folder is not mounted');
    expect(screen.getByRole('option', { name: 'The import folder could not be scanned' })).toBeInTheDocument();
    expect(screen.queryByText('No families found in the import folder')).not.toBeInTheDocument();
  });

  it('shows a running import’s progress and time left', async () => {
    const now = Date.now();
    const runningJob = {
      _id: 'job-run',
      submitted_path: '/data/FAM-200',
      family_id: 'FAM-200',
      status: 'running',
      dry_run: false,
      requested_by: 'admin@example.com',
      requested_at: new Date(now - 3_600_000).toISOString(),
      heartbeat_at: new Date(now - 5_000).toISOString(),
      validation_errors: [],
      validation_warnings: [],
      logs: [],
      metadata: {},
      datasets: [
        {
          dataset_type: 'qc',
          enabled: true,
          status: 'imported',
          files: [],
          samples: [],
          summary: {},
          progress: {
            started_at: new Date(now - 3_600_000).toISOString(),
            finished_at: new Date(now - 3_480_000).toISOString(),
          },
        },
        {
          dataset_type: 'snv',
          enabled: true,
          status: 'running',
          files: [],
          samples: [],
          message: 'Importing SNV VCF and VEP annotations',
          summary: {},
          progress: {
            started_at: new Date(now - 3_480_000).toISOString(),
            measured_at: new Date(now - 5_000).toISOString(),
            fraction_read: 0.4837,
            seconds_left: 85 * 60,
          },
        },
        {
          dataset_type: 'sv_needlr',
          enabled: true,
          status: 'valid',
          files: [],
          samples: [],
          summary: {},
        },
      ],
    };
    get.mockImplementation((url, config) => {
      if (url === '/family-imports') return Promise.resolve({ data: [runningJob] });
      if (url === '/family-imports/job-run') return Promise.resolve({ data: runningJob });
      return factory(url, config);
    });
    renderPage();

    // The jobs table: the running dataset, its share read and its time left.
    expect(await screen.findByText('snv: 48% read, about 1 h 25 min left')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'View' }));

    expect(
      await screen.findByText(
        /^snv: 48% read, about 1 h 25 min left \(done around .+\)\. 1 more dataset to follow\.$/
      )
    ).toBeInTheDocument();
    const rows = screen.getAllByRole('row');
    const row = (dataset: string) => rows.find((item) => item.textContent?.startsWith(dataset));
    expect(row('qc')).toHaveTextContent('took 2 min');
    expect(row('snv')).toHaveTextContent('48% read, about 1 h 25 min left');
    expect(row('sv_needlr')).toHaveTextContent('—');
  });

  it('discovers and writes a manifest draft for admins', async () => {
    vi.mocked(api.post).mockImplementation((url: string, payload: unknown) => {
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

    renderPage();

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
        family_id: null,
      })
    );
    expect(screen.getByText(/wrote \/data\/fam-100\/manifest.yaml/i)).toBeInTheDocument();
  });

  it('imports with the drafted manifest kept in CoGA, only for the folder it was drafted for', async () => {
    const draft = 'schema_version: 1\nfamily_id: FAM-100\nped: family.ped\n';
    vi.mocked(api.post).mockImplementation((url: string) => {
      if (url === '/family-imports/manifest/discover') {
        return Promise.resolve({
          data: {
            valid: true,
            family_id: 'FAM-100',
            ped_path: 'family.ped',
            manifest_path: '/data/share/FAM-100/manifest.yaml',
            naming_scheme: 'standard_v1',
            sample_ids: ['S1'],
            manifest_yaml: draft,
            datasets: [],
            errors: [],
            warnings: [],
            metadata: {},
          },
        });
      }
      return Promise.resolve({
        data: {
          _id: 'job-1',
          submitted_path: '/data/share/FAM-100',
          family_id: null,
          status: 'queued',
          dry_run: true,
          requested_by: 'admin@example.com',
          requested_at: '2026-04-29T12:00:00Z',
          validation_errors: [],
          validation_warnings: [],
          logs: [],
          datasets: [],
          manifest_sha256: 'a'.repeat(64),
        },
      });
    });

    renderPage();
    await waitForProject();

    const folder = screen.getByLabelText(/family folder path/i);
    fireEvent.change(folder, { target: { value: '/data/share/FAM-100' } });
    fireEvent.click(screen.getByRole('button', { name: /discover manifest/i }));
    const keep = await screen.findByRole('checkbox', { name: /import with this manifest, kept in coga/i });
    expect(keep).not.toBeChecked();
    fireEvent.click(keep);
    expect(keep).toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: /validate package/i }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/family-imports', {
        folder_path: '/data/share/FAM-100',
        project_id: ACCESSIBLE_PROJECT_ID,
        dry_run: true,
        family_id: null,
        conflict_mode: 'cancel',
        manifest_yaml: draft,
      })
    );
    expect(await screen.findByText(/with the manifest kept in coga/i)).toBeInTheDocument();

    // Another folder: the draft is not sent with it.
    vi.mocked(api.post).mockClear();
    fireEvent.change(folder, { target: { value: '/data/share/FAM-200' } });
    expect(keep).not.toBeChecked();
    expect(keep).toBeDisabled();
    expect(screen.getByText(/discover the manifest for this folder first/i)).toBeInTheDocument();
  });

  it("shows what Discover proposed for checking, not the datasets it did not find", async () => {
    vi.mocked(api.post).mockImplementation((url: string, payload: unknown) => {
      if (url === '/family-imports/manifest/discover') {
        return Promise.resolve({
          data: {
            valid: true,
            family_id: 'COUPLE1',
            ped_path: null,
            manifest_path: '/data/COUPLE1/manifest.yaml',
            naming_scheme: 'standard_v1',
            sample_ids: ['FATHER1', 'MOTHER1'],
            manifest_yaml: 'schema_version: 1\nfamily_id: COUPLE1\n',
            datasets: [],
            errors: [],
            warnings: [
              {
                code: 'ped_proposed_from_folders',
                message:
                  'The package has no PED. Discover took its two samples for a couple screened for carriership: MOTHER1 (female) and FATHER1 (male).',
              },
              { code: 'dataset_not_detected', message: 'No mito files were found', dataset: 'mito' },
            ],
            metadata: {},
          },
        });
      }
      return Promise.resolve({ data: payload });
    });

    renderPage();
    fireEvent.change(screen.getByLabelText(/family folder path/i), {
      target: { value: '/data/COUPLE1' },
    });
    fireEvent.click(screen.getByRole('button', { name: /discover manifest/i }));

    expect(await screen.findByText(/check before writing the manifest/i)).toBeInTheDocument();
    expect(screen.getByText(/MOTHER1 \(female\) and FATHER1 \(male\)/)).toBeInTheDocument();
    expect(screen.queryByText(/No mito files were found/)).not.toBeInTheDocument();
  });

  // An import into a family already in CoGA names that family; the import is refused until
  // one is chosen.
  it('imports into the existing family chosen as the target, under the policy chosen', async () => {
    vi.mocked(api.post).mockResolvedValue({
      data: {
        // The job the factory serves at /family-imports/job-1 as it is followed up.
        _id: 'job-1',
        submitted_path: '/data/FAM-200',
        family_id: 'FAM-200',
        status: 'queued',
        dry_run: false,
        requested_by: 'admin@example.com',
        requested_at: '2026-04-29T12:00:00Z',
        validation_errors: [],
        validation_warnings: [],
        logs: [],
        datasets: [],
      },
    });
    get.mockImplementation((url, config) =>
      url === '/families'
        ? Promise.resolve({ data: [{ family_id: 'FAM-100' }, { family_id: 'FAM-200' }] })
        : factory(url, config),
    );
    renderPage();
    await waitForProject();
    fireEvent.change(screen.getByLabelText(/family folder path/i), { target: { value: ' /data/FAM-200 ' } });

    fireEvent.click(screen.getByRole('button', { name: 'Existing family' }));
    const family = screen.getByLabelText(/^existing family$/i);
    await waitFor(() => expect(family).toHaveValue('FAM-100'));
    fireEvent.change(family, { target: { value: 'FAM-200' } });
    fireEvent.change(screen.getByLabelText(/existing data policy/i), { target: { value: 'update' } });
    fireEvent.click(screen.getByLabelText(/^dry run$/i));
    fireEvent.click(screen.getByRole('button', { name: 'Start import' }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/family-imports', {
        folder_path: '/data/FAM-200',
        project_id: ACCESSIBLE_PROJECT_ID,
        dry_run: false,
        family_id: 'FAM-200',
        conflict_mode: 'update',
      }),
    );
    expect(await screen.findByText('Started import job job-1.')).toBeInTheDocument();
  });

  it('starts no import into an existing family while there is none to choose', async () => {
    renderPage();
    await waitForProject();
    fireEvent.change(screen.getByLabelText(/family folder path/i), { target: { value: '/data/FAM-200' } });
    expect(screen.getByRole('button', { name: /validate package/i })).toBeEnabled();

    fireEvent.click(screen.getByRole('button', { name: 'Existing family' }));

    expect(screen.getByLabelText(/^existing family$/i)).toBeDisabled();
    expect(screen.getByRole('option', { name: 'No accessible families' })).toBeInTheDocument();
    expect(screen.getByText('Select family')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /validate package/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /discover manifest/i })).toBeDisabled();
  });

  it('starts no real import without a project, and says to choose one', async () => {
    get.mockImplementation((url, config) => (url === '/projects' ? Promise.resolve({ data: [] }) : factory(url, config)));
    renderPage();
    expect(await screen.findByRole('option', { name: 'No accessible projects' })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/family folder path/i), { target: { value: '/data/FAM-200' } });
    // A dry run needs no project.
    expect(screen.getByRole('button', { name: /validate package/i })).toBeEnabled();

    fireEvent.click(screen.getByLabelText(/^dry run$/i));

    expect(screen.getByText('Choose a project before starting an import.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start import' })).toBeDisabled();
  });

  // A refused call says why: the server's reason, else a fixed sentence.
  it.each([
    ['manifest discovery', '/family-imports/manifest/discover', 'No PED file in the folder.', 'No PED file in the folder.'],
    ['manifest discovery', '/family-imports/manifest/discover', undefined, 'Manifest discovery failed.'],
    ['manifest write', '/family-imports/manifest/write', 'manifest.yaml exists; tick overwrite.', 'manifest.yaml exists; tick overwrite.'],
    ['manifest write', '/family-imports/manifest/write', undefined, 'Manifest could not be written.'],
    ['package import', '/family-imports', 'Family FAM-100 is being imported.', 'Family FAM-100 is being imported.'],
    ['package import', '/family-imports', undefined, 'Family package import could not be started.'],
  ])('says why a refused %s failed (%s)', async (_call, refusedUrl, detail, message) => {
    vi.mocked(api.post).mockImplementation((url: string) => {
      if (url === refusedUrl) {
        return Promise.reject(detail ? { response: { status: 409, data: { detail } } } : {});
      }
      return Promise.resolve({
        data: { valid: true, family_id: 'FAM-100', manifest_yaml: 'schema_version: 1\n', datasets: [], errors: [], warnings: [] },
      });
    });
    renderPage();
    await waitForProject();
    fireEvent.change(screen.getByLabelText(/family folder path/i), { target: { value: '/data/FAM-100' } });

    if (refusedUrl === '/family-imports') {
      fireEvent.click(screen.getByRole('button', { name: /validate package/i }));
    } else {
      fireEvent.click(screen.getByRole('button', { name: /discover manifest/i }));
      if (refusedUrl === '/family-imports/manifest/write') {
        await screen.findByLabelText(/manifest.yaml preview/i);
        fireEvent.click(screen.getByRole('button', { name: /write manifest.yaml/i }));
      }
    }

    const status = await screen.findByText(message);
    expect(status).toHaveClass('status-note--error');
  });
});
