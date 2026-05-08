import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi, type Mock } from 'vitest';
import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';
import AdminVariantTagsPage from '../AdminVariantTagsPage';

vi.mock('../../../lib/api', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

describe('AdminVariantTagsPage', () => {
  it('updates and deletes custom project-scoped tags via admin endpoints', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);

    (api.get as unknown as Mock).mockImplementation((url: string) => {
      if (url === '/projects') {
        return Promise.resolve({
          data: [
            {
              id: 'P1',
              name: 'Project One',
              species_id: 'sp1',
              assembly_id: 'as1',
              user_ids: [],
              metadata: {},
            },
          ],
        });
      }
      if (url === '/admin/variant-tags') {
        return Promise.resolve({
          data: [
            {
              key: 'review',
              label: 'Review',
              description: 'Built-in',
              group: 'collaboration',
              color: '#2563eb',
              sort_order: 10,
              scope: 'system',
              is_custom: false,
              shared_project_ids: [],
            },
            {
              key: 'custom_follow_up',
              label: 'Follow-up',
              description: 'Custom tag',
              group: 'custom',
              color: '#5b6b79',
              sort_order: 500,
              scope: 'project',
              project_id: 'P1',
              shared_project_ids: [],
              is_custom: true,
            },
          ],
        });
      }
      return Promise.resolve({ data: [] });
    });
    (api.put as unknown as Mock).mockResolvedValue({
      data: {
        key: 'custom_follow_up',
        label: 'Follow-up updated',
        description: 'Custom tag',
        group: 'custom',
        color: '#5b6b79',
        sort_order: 500,
        scope: 'project',
        project_id: 'P1',
        shared_project_ids: [],
        is_custom: true,
      },
    });
    (api.delete as unknown as Mock).mockResolvedValue({ status: 204 });

    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <AdminVariantTagsPage />
        </MemoryRouter>
      </QueryClientProvider>
    );

    expect(await screen.findByText('Variant tag management')).toBeInTheDocument();

    const editButtons = await screen.findAllByRole('button', { name: 'Edit' });
    fireEvent.click(editButtons[0]);
    const labelInput = screen.getByDisplayValue('Follow-up');
    fireEvent.change(labelInput, { target: { value: 'Follow-up updated' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(api.put).toHaveBeenCalledWith(
        '/admin/variant-tags/custom_follow_up',
        expect.objectContaining({ label: 'Follow-up updated' }),
        expect.objectContaining({ params: undefined }),
      ),
    );

    const deleteButtons = await screen.findAllByRole('button', { name: 'Delete' });
    fireEvent.click(deleteButtons[0]);
    await waitFor(() =>
      expect(api.delete).toHaveBeenCalledWith('/admin/variant-tags/custom_follow_up'),
    );
  });
});
