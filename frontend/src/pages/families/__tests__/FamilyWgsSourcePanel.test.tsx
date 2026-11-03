import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import FamilyWgsSourcePanel from '../FamilyWgsSourcePanel';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock('../../../lib/api', () => ({ default: api }));

const candidates = {
  can_register: true,
  candidates: [
    { file_id: 'file-2', file_name: 'family-new.vcf.gz', file_size: 2048 },
    { file_id: 'file-1', file_name: 'family-old.vcf.gz', file_size: 1024 },
  ],
};
const renderPanel = (currentSource = false) =>
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <FamilyWgsSourcePanel
        familyId="SYN/F1"
        projectId="project1"
        enabled
        currentSource={currentSource}
      />
    </QueryClientProvider>
  );

describe('WGS source registration', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    api.get.mockResolvedValue({ data: candidates });
    api.post.mockResolvedValue({ data: { id: 'source1' } });
  });
  it('registers the chosen retained VCF only once the user declares it a whole genome, with both policies', async () => {
    renderPanel();
    const register = await screen.findByRole('button', {
      name: 'Check and register the WGS source',
    });
    expect(api.get).toHaveBeenCalledWith('/families/SYN%2FF1/wgs-source', {
      params: { project_id: 'project1' },
    });
    // The newest retained upload is offered first; the declaration is the user's, never implied.
    expect(screen.getByLabelText('Retained VCF')).toHaveValue('file-2');
    expect(register).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Retained VCF'), {
      target: { value: 'file-1' },
    });
    fireEvent.click(
      screen.getByRole('checkbox', {
        name: /This is a complete whole-genome callset/,
      })
    );
    fireEvent.change(screen.getByLabelText('Absent positions'), {
      target: { value: 'reference' },
    });
    fireEvent.click(
      screen.getByRole('checkbox', { name: /Accept unfiltered calls/ })
    );
    expect(register).toBeEnabled();
    fireEvent.click(register);
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        '/families/SYN%2FF1/wgs-source',
        {
          file_id: 'file-1',
          full_wgs: true,
          genome_build: 'GRCh38',
          absent_record_is_hom_ref: true,
          allow_unfiltered: true,
        },
        { params: { project_id: 'project1' } }
      )
    );
    expect(await screen.findByRole('status')).toHaveTextContent(
      'WGS source registered.'
    );
  });
  it('says why a source cannot be registered, and why it was refused', async () => {
    api.get.mockResolvedValueOnce({
      data: { ...candidates, can_register: false },
    });
    const { unmount } = renderPanel();
    expect(
      await screen.findByText(
        /needs a family on GRCh38 and permission to change this project/
      )
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /register the WGS source/ })
    ).not.toBeInTheDocument();
    unmount();

    api.get.mockResolvedValue({ data: { can_register: true, candidates: [] } });
    const empty = renderPanel();
    expect(
      await screen.findByText(
        'No retained nuclear VCF of this family is available.'
      )
    ).toBeInTheDocument();
    empty.unmount();

    api.get.mockResolvedValue({ data: candidates });
    api.post.mockRejectedValue(
      new Error(
        'The retained VCF genotypes do not match the current primary callset.'
      )
    );
    renderPanel(true);
    // A current source folds the form away under a summary that says it would change it.
    fireEvent.click(
      await screen.findByText('Change the source or its assumptions')
    );
    fireEvent.click(
      screen.getByRole('checkbox', {
        name: /This is a complete whole-genome callset/,
      })
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Check and register the WGS source' })
    );
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'do not match the current primary callset'
    );
  });
});
