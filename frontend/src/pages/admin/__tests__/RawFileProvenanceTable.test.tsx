import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import RawFileProvenanceTable from '../RawFileProvenanceTable';
import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

vi.mock('../../../lib/api', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

// One file on local disk, one gone from it, and one kept in the bucket the package was
// imported from: the last is neither downloadable here nor missing. Two more are recorded
// where CoGA does not read from, on disk and in a bucket (an edited row, say).
const file = (overrides: Record<string, unknown>) => ({
  scope: 'individual',
  dataset: 'alignments',
  file_type: 'CRAM',
  sample_id: 'S1',
  file_size: 4,
  sha256: null,
  source: 'family_package',
  created_at: '2026-06-01T00:00:00+00:00',
  exists: true,
  download_available: true,
  in_object_store: false,
  ...overrides,
});

const rawFiles = {
  family_id: 'F1',
  family_files: [],
  individual_files: [
    file({ id: 'local', file_name: 'local.cram', storage_path: '/data/F1/bams/local.cram' }),
    file({
      id: 'gone',
      file_name: 'gone.cram',
      storage_path: '/data/F1/bams/gone.cram',
      exists: false,
      download_available: false,
    }),
    file({
      id: 'remote',
      file_name: 'S1.cram',
      storage_path: 'gs://phi/imports/F1/bams/S1.cram',
      exists: null,
      download_available: false,
      in_object_store: true,
    }),
    file({
      id: 'outside',
      file_name: 'edited.cram',
      storage_path: '/elsewhere/edited.cram',
      exists: false,
      download_available: false,
      outside_read_folders: true,
    }),
    file({
      id: 'remote-outside',
      file_name: 'report.pdf',
      storage_path: 'gs://phi/reports/F1/report.pdf',
      exists: null,
      download_available: false,
      in_object_store: true,
      outside_read_folders: true,
    }),
  ],
};

const OUTSIDE =
  'Its recorded location lies outside the folders and stores CoGA reads source files from, so it is not read';

const renderTable = () => {
  (api.get as unknown as Mock).mockResolvedValue({ data: rawFiles });
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <RawFileProvenanceTable familyId="F1" />
    </QueryClientProvider>,
  );
};

const rowOf = async (fileName: string) => {
  const cell = await screen.findByText(fileName);
  const row = cell.closest('tr');
  if (!row) throw new Error(`no row for ${fileName}`);
  return within(row);
};

describe('RawFileProvenanceTable', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('does not call a file kept in the object store missing', async () => {
    renderTable();

    const remote = await rowOf('S1.cram');
    const download = remote.getByRole('button', { name: 'Download' });
    expect(download).toBeDisabled();
    expect(download).toHaveAttribute('title', expect.stringMatching(/object store/i));
    expect(download.getAttribute('title')).not.toMatch(/no longer available/i);
    expect(remote.getByRole('button', { name: 'Verify' })).toHaveAttribute(
      'title',
      expect.stringMatching(/store/i),
    );

    // A local file that is really gone still says so.
    const gone = await rowOf('gone.cram');
    expect(gone.getByRole('button', { name: 'Download' })).toHaveAttribute(
      'title',
      'Source file is no longer available',
    );
    const local = await rowOf('local.cram');
    expect(local.getByRole('button', { name: 'Verify' })).toHaveAttribute(
      'title',
      'Recompute SHA-256 and compare to the stored checksum',
    );
  });

  it('says what was compared when a file in the object store verifies', async () => {
    (api.post as unknown as Mock).mockResolvedValue({
      data: {
        file_id: 'remote',
        status: 'verified',
        expected_sha256: null,
        computed_sha256: null,
        message: 'The object is in the store with the recorded size and generation.',
      },
    });
    renderTable();

    const remote = await rowOf('S1.cram');
    fireEvent.click(remote.getByRole('button', { name: 'Verify' }));

    expect(await remote.findByText('verified')).toBeInTheDocument();
    // Not a SHA-256 check, so the chip alone would overstate it.
    expect(
      remote.getByText('The object is in the store with the recorded size and generation.'),
    ).toBeInTheDocument();
    expect(api.post).toHaveBeenCalledWith('/admin/data/files/remote/verify');
  });

  it('marks a file recorded where CoGA does not read from, not as kept in the store or gone', async () => {
    renderTable();

    for (const fileName of ['edited.cram', 'report.pdf']) {
      const row = await rowOf(fileName);
      // Before: the bucket row said it was kept in the object store, the disk row that it was gone.
      expect(row.getByRole('button', { name: 'Download' })).toHaveAttribute('title', OUTSIDE);
      expect(row.getByRole('button', { name: 'Verify' })).toHaveAttribute('title', OUTSIDE);
      expect(row.getByText(`${OUTSIDE}.`)).toBeInTheDocument();
    }
    // The other rows carry no such line.
    expect(screen.getAllByText(`${OUTSIDE}.`)).toHaveLength(2);
  });

  it('gives the reason a Verify was refused', async () => {
    const reason =
      "The file's recorded storage path names no object below FAMILY_IMPORT_ROOTS, so the object store is not asked about it.";
    (api.post as unknown as Mock).mockRejectedValue({ response: { status: 403, data: { detail: reason } } });
    renderTable();

    const remote = await rowOf('report.pdf');
    fireEvent.click(remote.getByRole('button', { name: 'Verify' }));

    // Before: "Could not verify report.pdf." and nothing more.
    expect(await screen.findByText(`Could not verify report.pdf: ${reason}`)).toBeInTheDocument();
    expect(api.post).toHaveBeenCalledWith('/admin/data/files/remote-outside/verify');
  });
});
