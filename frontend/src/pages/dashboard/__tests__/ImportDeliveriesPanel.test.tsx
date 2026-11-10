import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import api from '../../../lib/api';
import { AUTH_STORAGE_KEYS } from '../../../lib/auth';
import { storage } from '../../../lib/storage';
import { createTestQueryClient } from '../../../test/createTestQueryClient';
import ImportDeliveriesPanel, {
  isApprovable,
  needsAChoice,
  needsAnOverwrite,
} from '../ImportDeliveriesPanel';

vi.mock('../../../lib/api', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}));

const delivery = (name: string, overrides: Record<string, unknown> = {}) => ({
  _id: `id-${name}`,
  watch_root: '/data/share',
  folder_path: `/data/share/${name}`,
  folder_name: name,
  state: 'ready',
  package_fingerprint: 'a'.repeat(64),
  marker_sha256: 'b'.repeat(64),
  run_id: `run-${name}`,
  family_id: name,
  project_id: 'p1',
  project: 'PROJECT1',
  analysis_type: 'wgs_trio',
  pipeline_name: 'nf-core/example',
  pipeline_version: '1.0.0',
  request_id: null,
  issues: [],
  warnings: [],
  manifest_sha256: null,
  revision: 0,
  job_id: null,
  job_status: null,
  job_dry_run: null,
  first_seen_at: '2026-11-05T10:00:00Z',
  last_seen_at: '2026-11-05T10:05:00Z',
  state_changed_at: '2026-11-05T10:00:00Z',
  ...overrides,
});

const existingFamily = delivery('FAMX', {
  state: 'needs_attention',
  issues: [
    {
      code: 'existing_family',
      message: "Family 'FAMX' already exists.",
      path: null,
    },
    {
      code: 'existing_samples',
      message: 'Sample ID(s) already exist in the system: PROBAND.',
      path: null,
    },
  ],
});

const membersDiffer = delivery('FAMY', {
  state: 'needs_attention',
  issues: [
    {
      code: 'existing_family',
      message: "Family 'FAMY' already exists.",
      path: null,
    },
    {
      code: 'package_members_differ_from_family',
      message:
        "The package's members differ from family 'FAMY' in CoGA: MOTHER is a member of the family, not of the package.",
      path: null,
    },
  ],
});

const deliveries = [
  delivery('FAM1', {
    warnings: [
      {
        code: 'optional_dataset_missing',
        message: 'No coverage dataset.',
        path: null,
      },
    ],
  }),
  delivery('FAM2'),
  existingFamily,
  delivery('FAM3', {
    state: 'needs_attention',
    issues: [
      {
        code: 'file_checksum_differs',
        message: "A file's checksum differs.",
        path: 'family.ped',
      },
    ],
  }),
  delivery('FAM4', {
    state: 'incomplete',
    issues: [
      { code: 'file_missing', message: 'Not there yet.', path: 'a.vcf' },
    ],
  }),
  delivery('OLD1', { state: 'superseded' }),
  delivery('OLD2', { state: 'refused' }),
];

const renderPanel = () =>
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <ImportDeliveriesPanel />
    </QueryClientProvider>
  );

const row = (name: string) =>
  screen.getByText(name, { selector: 'td > div' }).closest('tr') as HTMLElement;

/** The row of the delivery in folder ``name``, once the list has loaded. */
const loadedRow = async (name: string) => {
  await screen.findByText(name, { selector: 'td > div' });
  return within(row(name));
};

describe('ImportDeliveriesPanel', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    vi.mocked(api.get).mockResolvedValue({ data: deliveries });
    vi.mocked(api.post).mockImplementation((url: string) =>
      Promise.resolve({
        data: { job: { _id: `job-for-${url.split('/')[3]}` }, delivery: {} },
      })
    );
    storage.clear();
    storage.setItem(AUTH_STORAGE_KEYS.role, 'superuser');
  });

  it('offers to correct a delivery that is ready or needs attention, and says when one was', async () => {
    vi.mocked(api.get).mockImplementation((url: string) =>
      Promise.resolve({
        data: url.endsWith('/package')
          ? {
              delivery_id: 'id-FAM1',
              revision: 0,
              package: null,
              package_sha256: null,
              original: null,
              proposed: [],
              revisions: [],
            }
          : [
              ...deliveries.filter((item) => item.folder_name !== 'FAM2'),
              delivery('FAM2', { revision: 2, origin: 'manual' }),
            ],
      })
    );
    renderPanel();

    const ready = await loadedRow('FAM1');
    expect(
      within(row('FAM3')).getByRole('button', { name: 'Correct…' })
    ).toBeInTheDocument();
    expect(
      within(row('FAM4')).queryByRole('button', { name: 'Correct…' })
    ).not.toBeInTheDocument();
    expect(
      within(row('FAM2')).getByText('Corrected (revision 2)')
    ).toBeInTheDocument();
    // A folder added by hand says so where a pipeline's delivery names its pipeline.
    expect(within(row('FAM2')).getByText('Added by hand')).toBeInTheDocument();
    expect(
      within(row('FAM1')).getByText('nf-core/example 1.0.0')
    ).toBeInTheDocument();

    fireEvent.click(ready.getByRole('button', { name: 'Correct…' }));

    expect(
      await screen.findByRole('dialog', { name: 'Correct FAM1' })
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(api.get).toHaveBeenCalledWith(
        '/family-imports/deliveries/id-FAM1/package'
      )
    );
  });

  it('tells which deliveries can be approved', () => {
    expect(isApprovable(deliveries[0] as never)).toBe(true);
    expect(needsAChoice(existingFamily as never)).toBe(true);
    expect(needsAnOverwrite(existingFamily as never)).toBe(false);
    expect(isApprovable(membersDiffer as never)).toBe(true);
    expect(needsAnOverwrite(membersDiffer as never)).toBe(true);
    expect(isApprovable(deliveries[3] as never)).toBe(false);
    expect(isApprovable(deliveries[4] as never)).toBe(false);
  });

  it('lists the open deliveries, how many wait, and the decided ones only when asked', async () => {
    renderPanel();

    expect(
      await screen.findByText('FAM1', { selector: 'td > div' })
    ).toBeInTheDocument();
    expect(screen.getByText('4 waiting')).toBeInTheDocument();
    expect(
      screen.queryByText('OLD1', { selector: 'td > div' })
    ).not.toBeInTheDocument();
    expect(
      within(row('FAM3')).getByText("A file's checksum differs.")
    ).toBeInTheDocument();
    expect(
      within(row('FAM3')).queryByRole('button', { name: /approve/i })
    ).not.toBeInTheDocument();
    expect(
      within(row('FAM3')).getByRole('button', { name: /refuse/i })
    ).toBeInTheDocument();
    expect(within(row('FAM4')).getByText('Still copying')).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole('checkbox', {
        name: /show decided and superseded deliveries \(2\)/i,
      })
    );
    expect(
      screen.getByText('OLD1', { selector: 'td > div' })
    ).toBeInTheDocument();
    expect(within(row('OLD2')).queryByRole('button')).not.toBeInTheDocument();
  });

  it('approves a delivery once its warnings are acknowledged', async () => {
    renderPanel();
    fireEvent.click(
      (await loadedRow('FAM1')).getByRole('button', { name: 'Approve…' })
    );

    const dialog = screen.getByRole('dialog', { name: 'Import FAM1?' });
    const confirm = within(dialog).getByRole('button', {
      name: 'Approve and import',
    });
    expect(confirm).toBeDisabled();
    fireEvent.click(
      within(dialog).getByRole('checkbox', { name: 'I checked the warnings' })
    );
    fireEvent.click(confirm);

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        '/family-imports/deliveries/id-FAM1/approve',
        {
          revision: 0,
          dry_run: false,
          conflict_mode: 'cancel',
          acknowledge_warnings: true,
        }
      )
    );
    expect(
      await screen.findByText('Import job job-for-id-FAM1 started for FAM1.')
    ).toBeInTheDocument();
  });

  it('dry-runs a delivery', async () => {
    renderPanel();
    fireEvent.click(
      (await loadedRow('FAM2')).getByRole('button', { name: 'Dry run' })
    );
    fireEvent.click(
      within(screen.getByRole('dialog', { name: 'Dry-run FAM2?' })).getByRole(
        'button',
        { name: 'Start dry run' }
      )
    );

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        '/family-imports/deliveries/id-FAM2/approve',
        {
          revision: 0,
          dry_run: true,
          conflict_mode: 'cancel',
          acknowledge_warnings: false,
        }
      )
    );
  });

  it('approves a delivery of an existing family with update, and overwrite only for an administrator', async () => {
    renderPanel();
    fireEvent.click(
      (await loadedRow('FAMX')).getByRole('button', { name: 'Approve…' })
    );

    const dialog = screen.getByRole('dialog', { name: 'Import FAMX?' });
    const policy = within(dialog).getByRole('combobox', {
      name: 'Existing data policy',
    });
    fireEvent.change(policy, { target: { value: 'overwrite' } });
    expect(
      within(dialog).getByText(
        'Only an administrator overwrites an existing family.'
      )
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole('button', { name: 'Approve and import' })
    ).toBeDisabled();

    fireEvent.change(policy, { target: { value: 'update' } });
    fireEvent.click(
      within(dialog).getByRole('button', { name: 'Approve and import' })
    );
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        '/family-imports/deliveries/id-FAMX/approve',
        {
          revision: 0,
          dry_run: false,
          conflict_mode: 'update',
          acknowledge_warnings: false,
        }
      )
    );
  });

  it('approves a delivery whose members differ from its family only with overwrite', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: [membersDiffer] });
    storage.setItem(AUTH_STORAGE_KEYS.role, 'admin');
    renderPanel();
    fireEvent.click(
      (await loadedRow('FAMY')).getByRole('button', { name: 'Approve…' })
    );

    const dialog = screen.getByRole('dialog', { name: 'Import FAMY?' });
    const policy = within(dialog).getByRole('combobox', {
      name: 'Existing data policy',
    });
    expect(policy).toHaveValue('overwrite');
    expect(
      within(dialog).getByRole('option', {
        name: 'Update: add the missing datasets and members',
      })
    ).toBeDisabled();
    expect(
      within(dialog).getByText(/an update changes none of the family/)
    ).toBeInTheDocument();
    fireEvent.click(
      within(dialog).getByRole('button', { name: 'Approve and import' })
    );
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        '/family-imports/deliveries/id-FAMY/approve',
        {
          revision: 0,
          dry_run: false,
          conflict_mode: 'overwrite',
          acknowledge_warnings: false,
        }
      )
    );
  });

  it('approves the selected ready deliveries together', async () => {
    renderPanel();
    await screen.findByText('FAM1', { selector: 'td > div' });
    const approveSelected = screen.getByRole('button', {
      name: 'Approve selected (0)',
    });
    expect(approveSelected).toBeDisabled();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select FAM1' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select FAM2' }));
    fireEvent.click(
      screen.getByRole('button', { name: 'Approve selected (2)' })
    );

    const dialog = screen.getByRole('dialog', { name: 'Import 2 deliveries?' });
    fireEvent.click(
      within(dialog).getByRole('checkbox', { name: 'I checked the warnings' })
    );
    fireEvent.click(
      within(dialog).getByRole('button', { name: 'Approve and import' })
    );

    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(2));
    expect(vi.mocked(api.post).mock.calls.map((call) => call[0])).toEqual([
      '/family-imports/deliveries/id-FAM1/approve',
      '/family-imports/deliveries/id-FAM2/approve',
    ]);
  });

  it('says which approval failed', async () => {
    vi.mocked(api.post).mockRejectedValue(
      Object.assign(new Error('HTTP 409'), {
        response: { status: 409, data: { detail: 'The folder changed.' } },
      })
    );
    renderPanel();
    fireEvent.click(
      (await loadedRow('FAM2')).getByRole('button', { name: 'Approve…' })
    );
    fireEvent.click(
      within(screen.getByRole('dialog')).getByRole('button', {
        name: 'Approve and import',
      })
    );

    expect(
      await screen.findByText('FAM2: The folder changed.')
    ).toBeInTheDocument();
  });

  it('refuses a delivery with a reason', async () => {
    renderPanel();
    fireEvent.click(
      (await loadedRow('FAM3')).getByRole('button', { name: 'Refuse…' })
    );

    const dialog = screen.getByRole('dialog', { name: 'Refuse FAM3?' });
    const refuse = within(dialog).getByRole('button', { name: 'Refuse' });
    expect(refuse).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText('Reason'), {
      target: { value: '  Rerun requested.  ' },
    });
    fireEvent.click(refuse);

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        '/family-imports/deliveries/id-FAM3/refuse',
        { reason: 'Rerun requested.' }
      )
    );
    expect(await screen.findByText('Refused FAM3.')).toBeInTheDocument();
  });

  it('says when no delivery waits', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: [] });
    renderPanel();

    expect(
      await screen.findByText(/No delivery is waiting/)
    ).toBeInTheDocument();
    expect(screen.getByText('0 waiting')).toBeInTheDocument();
  });
});
