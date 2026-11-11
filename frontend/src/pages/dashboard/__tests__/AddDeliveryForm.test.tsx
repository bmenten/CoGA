import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';
import AddDeliveryForm from '../AddDeliveryForm';

vi.mock('../../../lib/api', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}));

// Synthetic folders and projects.
const packages = [
  {
    folder_path: '/data/families/FAM001',
    name: 'FAM001',
    family_id: 'FAM001',
    has_ped: true,
    imported_families: [],
    changed_during_import: false,
  },
  {
    folder_path: '/data/families/NIPT1',
    name: 'NIPT1',
    family_id: 'NIPT1',
    has_ped: true,
    imported_families: [
      { family_id: 'NIPT1', registered_at: null, import_incomplete: false },
    ],
    changed_during_import: false,
  },
];
// A folder whose last import did not complete: listed with the others.
packages.push({
  folder_path: '/data/families/FAM003',
  name: 'FAM003',
  family_id: 'FAM003',
  has_ped: true,
  imported_families: [
    { family_id: 'FAM003', registered_at: null, import_incomplete: true },
  ],
  changed_during_import: false,
});
// A folder in the project folder of a project CoGA has.
packages.push({
  folder_path: '/data/families/Rare disease/FAM002',
  name: 'FAM002',
  family_id: 'FAM002',
  has_ped: true,
  project_folder: 'Rare disease',
  imported_families: [],
  changed_during_import: false,
} as (typeof packages)[number]);
const projects = [{ _id: 'p1', name: 'Rare disease' }];

describe('AddDeliveryForm', () => {
  const onAdded = vi.fn();

  const renderForm = () =>
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <AddDeliveryForm onAdded={onAdded} />
      </QueryClientProvider>
    );

  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    onAdded.mockReset();
    vi.mocked(api.get).mockImplementation((url: string) =>
      Promise.resolve({ data: url === '/projects' ? projects : packages })
    );
  });

  it('adds a folder for a project and an analysis, with the family ID when given', async () => {
    vi.mocked(api.post).mockResolvedValue({
      data: { _id: 'd1', folder_name: 'FAM001', state: 'ready' },
    });
    renderForm();
    const add = screen.getByRole('button', { name: 'Add to Deliveries' });
    expect(add).toBeDisabled();

    await screen.findByRole('option', { name: 'FAM001' });
    fireEvent.change(screen.getByRole('combobox', { name: 'Folder to add' }), {
      target: { value: '/data/families/FAM001' },
    });
    await screen.findByRole('option', { name: 'Rare disease' });
    fireEvent.change(
      screen.getByRole('combobox', { name: 'Project to add it to' }),
      {
        target: { value: 'p1' },
      }
    );
    expect(add).toBeDisabled();
    fireEvent.change(
      screen.getByRole('combobox', { name: 'Analysis of the folder' }),
      {
        target: { value: 'wgs_trio' },
      }
    );
    fireEvent.change(
      screen.getByRole('textbox', { name: 'Family ID of the folder' }),
      {
        target: { value: ' FAM001A ' },
      }
    );
    fireEvent.click(add);

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/family-imports/deliveries', {
        folder_path: '/data/families/FAM001',
        project_id: 'p1',
        family_id: 'FAM001A',
        analysis_type: 'wgs_trio',
      })
    );
    await waitFor(() =>
      expect(onAdded).toHaveBeenCalledWith({
        _id: 'd1',
        folder_name: 'FAM001',
        state: 'ready',
      })
    );
  });

  it('hides an imported folder until asked, marks it, and takes a typed path', async () => {
    vi.mocked(api.post).mockResolvedValue({ data: { _id: 'd2' } });
    renderForm();

    expect(
      await screen.findByRole('option', { name: 'FAM003 · import incomplete' })
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('option', { name: 'NIPT1 · imported' })
    ).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('checkbox', { name: 'Show imported folders (1)' })
    );
    expect(
      screen.getByRole('option', { name: 'NIPT1 · imported' })
    ).toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox', { name: 'Folder to add' }), {
      target: { value: '/data/families/NIPT1' },
    });
    fireEvent.change(
      screen.getByRole('combobox', { name: 'Analysis of the folder' }),
      { target: { value: 'monogenic_nipt' } }
    );
    fireEvent.change(
      screen.getByRole('textbox', { name: 'Or the path of the folder to add' }),
      {
        target: { value: '/data/families/OTHER' },
      }
    );
    await screen.findByRole('option', { name: 'Rare disease' });
    fireEvent.change(
      screen.getByRole('combobox', { name: 'Project to add it to' }),
      {
        target: { value: 'p1' },
      }
    );
    fireEvent.click(screen.getByRole('button', { name: 'Add to Deliveries' }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/family-imports/deliveries', {
        folder_path: '/data/families/OTHER',
        project_id: 'p1',
        family_id: null,
        analysis_type: 'monogenic_nipt',
      })
    );
  });

  it('shows a folder by its project folder, and takes that project when chosen', async () => {
    renderForm();

    await screen.findByRole('option', { name: 'Rare disease' });
    const option = await screen.findByRole('option', {
      name: 'Rare disease / FAM002',
    });
    fireEvent.change(screen.getByRole('combobox', { name: 'Folder to add' }), {
      target: { value: option.getAttribute('value') },
    });

    expect(
      screen.getByRole('combobox', { name: 'Project to add it to' })
    ).toHaveValue('p1');
  });

  it('says why a folder was not added', async () => {
    vi.mocked(api.post).mockRejectedValue({
      response: {
        status: 409,
        data: {
          detail: 'The folder is a delivery waiting for a decision already.',
        },
      },
      isAxiosError: true,
    });
    renderForm();
    fireEvent.change(
      screen.getByRole('textbox', { name: 'Or the path of the folder to add' }),
      {
        target: { value: '/data/families/FAM001' },
      }
    );
    await screen.findByRole('option', { name: 'Rare disease' });
    fireEvent.change(
      screen.getByRole('combobox', { name: 'Project to add it to' }),
      {
        target: { value: 'p1' },
      }
    );
    fireEvent.change(
      screen.getByRole('combobox', { name: 'Analysis of the folder' }),
      {
        target: { value: 'wgs' },
      }
    );
    fireEvent.click(screen.getByRole('button', { name: 'Add to Deliveries' }));

    expect(
      await screen.findByText(
        /waiting for a decision already|could not be added/
      )
    ).toBeInTheDocument();
    expect(onAdded).not.toHaveBeenCalled();
  });
});
