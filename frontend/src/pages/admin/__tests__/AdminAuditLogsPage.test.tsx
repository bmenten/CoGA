import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi, type Mock } from 'vitest';
import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';
import AdminAuditLogsPage from '../AdminAuditLogsPage';

vi.mock('../../../lib/api', () => ({
  default: {
    get: vi.fn(),
  },
}));

describe('AdminAuditLogsPage', () => {
  it('renders audit log entries and applies method filter', async () => {
    (api.get as unknown as Mock).mockImplementation((url: string) => {
      if (url === '/auth/users') {
        return Promise.resolve({
          data: [{ id: 'u1', email: 'admin@example.com' }],
        });
      }
      if (url === '/admin/audit-logs') {
        return Promise.resolve({
          data: {
            page: 1,
            page_size: 50,
            total: 1,
            items: [
              {
                id: 'evt-1',
                created_at: '2026-04-28T20:00:00Z',
                user_email: 'admin@example.com',
                method: 'PATCH',
                route_path: '/families/{family_id}/small-variant-tags/{tag_key}',
                path: '/families/F1/small-variant-tags/review',
                status_code: 200,
                duration_ms: 23,
                db_update: {
                  dbEntity: 'families',
                  entityId: 'F1',
                  updateType: 'update',
                },
                error: null,
              },
            ],
          },
        });
      }
      return Promise.resolve({ data: { page: 1, page_size: 50, total: 0, items: [] } });
    });

    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <AdminAuditLogsPage />
        </MemoryRouter>
      </QueryClientProvider>
    );

    expect(await screen.findByText('Audit logs')).toBeInTheDocument();
    expect((await screen.findAllByText('admin@example.com')).length).toBeGreaterThan(0);
    expect(screen.getByText('update families F1')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/filter user/i), {
      target: { value: 'admin@example.com' },
    });
    fireEvent.change(screen.getByLabelText(/filter method/i), {
      target: { value: 'PATCH' },
    });
    fireEvent.click(screen.getByRole('button', { name: /apply filters/i }));

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith(
        '/admin/audit-logs',
        expect.objectContaining({
          params: expect.objectContaining({
            method: 'PATCH',
            user_email: 'admin@example.com',
            page: 1,
            page_size: 50,
          }),
        })
      );
    });
  });
});
