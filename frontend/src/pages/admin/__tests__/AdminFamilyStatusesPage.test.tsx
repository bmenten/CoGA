import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';
import AdminFamilyStatusesPage from '../AdminFamilyStatusesPage';

vi.mock('../../../lib/api', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

const status = (overrides: Record<string, unknown> = {}) => ({
  id: 'st1',
  key: 'solved',
  label: 'Solved',
  description: 'Case solved',
  color: '#1f9d57',
  sort_order: 10,
  is_active: true,
  ...overrides,
});

const renderPage = () => {
  const queryClient = createTestQueryClient();
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <AdminFamilyStatusesPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

describe('AdminFamilyStatusesPage', () => {
  beforeEach(() => {
    (api.get as unknown as Mock).mockReset();
    (api.post as unknown as Mock).mockReset();
    (api.put as unknown as Mock).mockReset();
    (api.delete as unknown as Mock).mockReset();
  });

  it('lists statuses and creates a new one via the admin endpoint', async () => {
    (api.get as unknown as Mock).mockImplementation((url: string) => {
      if (url === '/admin/family-statuses') {
        return Promise.resolve({ data: [status()] });
      }
      return Promise.resolve({ data: [] });
    });
    (api.post as unknown as Mock).mockResolvedValue({
      data: status({ id: 'st2', key: 'pending', label: 'Pending', sort_order: 20 }),
    });

    renderPage();

    expect(await screen.findByRole('heading', { name: 'Family statuses' })).toBeInTheDocument();
    expect(await screen.findByText('Solved')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Label'), { target: { value: 'Pending' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add status' }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        '/admin/family-statuses',
        expect.objectContaining({ label: 'Pending' }),
      ),
    );
  });

  it('updates and deletes a status via the admin endpoints', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    (api.get as unknown as Mock).mockImplementation((url: string) => {
      if (url === '/admin/family-statuses') {
        return Promise.resolve({ data: [status()] });
      }
      return Promise.resolve({ data: [] });
    });
    (api.put as unknown as Mock).mockResolvedValue({ data: status({ label: 'Resolved' }) });
    (api.delete as unknown as Mock).mockResolvedValue({ status: 204 });

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    fireEvent.change(screen.getByLabelText('Label for solved'), { target: { value: 'Resolved' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(api.put).toHaveBeenCalledWith(
        '/admin/family-statuses/solved',
        expect.objectContaining({ label: 'Resolved' }),
      ),
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(api.delete).toHaveBeenCalledWith('/admin/family-statuses/solved'));
  });
});
