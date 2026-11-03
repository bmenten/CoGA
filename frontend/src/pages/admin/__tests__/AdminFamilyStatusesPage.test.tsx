import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
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

/** Serves `statuses` as the catalogue. */
const serveStatuses = (statuses: unknown[] = [status()]) =>
  vi.mocked(api.get).mockImplementation((url: string) =>
    Promise.resolve({ data: url === '/admin/family-statuses' ? statuses : [] }),
  );

const catalogueReads = () => vi.mocked(api.get).mock.calls.filter(([url]) => url === '/admin/family-statuses').length;

describe('AdminFamilyStatusesPage', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    vi.mocked(api.put).mockReset();
    vi.mocked(api.delete).mockReset();
    serveStatuses();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('lists statuses and creates a new one via the admin endpoint', async () => {
    vi.mocked(api.post).mockResolvedValue({
      data: status({ id: 'st2', key: 'pending', label: 'Pending', sort_order: 20 }),
    });

    renderPage();

    expect(await screen.findByRole('heading', { name: 'Family statuses' })).toBeInTheDocument();
    expect(await screen.findByText('Solved')).toBeInTheDocument();
    expect(screen.getByText('Case solved')).toBeInTheDocument();
    // No status without a label.
    expect(screen.getByRole('button', { name: 'Add status' })).toBeDisabled();

    fireEvent.change(screen.getByLabelText('Label'), { target: { value: ' Pending ' } });
    fireEvent.change(screen.getByLabelText('Description'), { target: { value: 'Awaiting lab confirmation' } });
    fireEvent.change(screen.getByLabelText('Status colour'), { target: { value: '#aa3300' } });
    fireEvent.change(screen.getByLabelText('Sort order'), { target: { value: '20' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add status' }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/admin/family-statuses', {
        label: 'Pending',
        description: 'Awaiting lab confirmation',
        color: '#aa3300',
        sort_order: 20,
      }),
    );
    expect(await screen.findByText('Created status “Pending”.')).toBeInTheDocument();
    // The form is cleared for the next status, and the catalogue read again.
    expect(screen.getByLabelText('Label')).toHaveValue('');
    expect(screen.getByLabelText('Sort order')).toHaveValue(null);
    expect(catalogueReads()).toBe(2);
  });

  it('sends no description or sort order that is left blank', async () => {
    vi.mocked(api.post).mockResolvedValue({ data: status({ key: 'pending', label: 'Pending' }) });
    renderPage();
    await screen.findByText('Solved');

    fireEvent.change(screen.getByLabelText('Label'), { target: { value: 'Pending' } });
    fireEvent.change(screen.getByLabelText('Description'), { target: { value: '   ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add status' }));

    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.post).mock.calls[0][1]).toEqual({
      label: 'Pending',
      description: undefined,
      color: '#5b6b79',
      sort_order: undefined,
    });
  });

  it('updates and deletes a status via the admin endpoints', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    vi.mocked(api.put).mockResolvedValue({ data: status({ label: 'Resolved' }) });
    vi.mocked(api.delete).mockResolvedValue({ status: 204 });

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    // The edit starts from the status as it is.
    expect(screen.getByLabelText('Description for solved')).toHaveValue('Case solved');
    expect(screen.getByLabelText('Sort order for solved')).toHaveValue(10);
    fireEvent.change(screen.getByLabelText('Label for solved'), { target: { value: 'Resolved' } });
    fireEvent.change(screen.getByLabelText('Description for solved'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('Colour for solved'), { target: { value: '#226622' } });
    fireEvent.change(screen.getByLabelText('Sort order for solved'), { target: { value: '5' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(api.put).toHaveBeenCalledWith('/admin/family-statuses/solved', {
        label: 'Resolved',
        // A cleared description is removed, not left as it was.
        description: null,
        color: '#226622',
        sort_order: 5,
      }),
    );
    expect(await screen.findByText('Updated status “Resolved”.')).toBeInTheDocument();
    expect(screen.queryByLabelText('Label for solved')).not.toBeInTheDocument();

    fireEvent.click(await screen.findByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(api.delete).toHaveBeenCalledWith('/admin/family-statuses/solved'));
    expect(window.confirm).toHaveBeenCalledWith('Delete status “Solved”? Families using it will lose this status.');
    expect(await screen.findByText('Deleted status “solved”.')).toBeInTheDocument();
  });

  it('deletes nothing unless the delete is confirmed, and cancels an edit unsaved', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'Delete' }));
    expect(api.delete).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.change(screen.getByLabelText('Label for solved'), { target: { value: '  ' } });
    // No status without a label.
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByLabelText('Label for solved')).not.toBeInTheDocument();
    expect(screen.getByText('Solved')).toBeInTheDocument();
    expect(api.put).not.toHaveBeenCalled();
  });

  it.each([
    ['deactivates an active status', true, { is_active: false, label: 'Solved' }, 'Deactivated “Solved”.'],
    ['activates an inactive status', false, { is_active: true, label: 'Solved' }, 'Activated “Solved”.'],
  ])('%s', async (_case, active, served, message) => {
    serveStatuses([status({ is_active: active })]);
    vi.mocked(api.put).mockResolvedValue({ data: status(served) });
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: active ? 'Active' : 'Inactive' }));

    await waitFor(() => expect(api.put).toHaveBeenCalledWith('/admin/family-statuses/solved', { is_active: !active }));
    expect(await screen.findByText(message)).toBeInTheDocument();
  });

  // A refused change says why, else a fixed sentence.
  it.each<[string, () => void, 'post' | 'put' | 'delete', string]>([
    [
      'create',
      () => {
        fireEvent.change(screen.getByLabelText('Label'), { target: { value: 'Solved' } });
        fireEvent.click(screen.getByRole('button', { name: 'Add status' }));
      },
      'post',
      'Could not create the family status.',
    ],
    [
      'update',
      () => {
        fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
        fireEvent.click(screen.getByRole('button', { name: 'Save' }));
      },
      'put',
      'Could not update the family status.',
    ],
    ['activation', () => fireEvent.click(screen.getByRole('button', { name: 'Active' })), 'put', 'Could not update the family status.'],
    ['delete', () => fireEvent.click(screen.getByRole('button', { name: 'Delete' })), 'delete', 'Could not delete the family status.'],
  ])('says why a refused %s failed', async (_change, act, method, fallback) => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    vi.mocked(api[method])
      .mockRejectedValueOnce({ response: { status: 409, data: { detail: 'A status with this key already exists.' } } })
      .mockRejectedValueOnce({});
    renderPage();
    await screen.findByText('Solved');

    act();
    const refused = await screen.findByText('A status with this key already exists.');
    expect(refused).toHaveClass('status-note--error');

    if (screen.queryByRole('button', { name: 'Cancel' })) fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    act();
    expect(await screen.findByText(fallback)).toHaveClass('status-note--error');
  });

  it.each([
    ['says the catalogue could not be loaded', () => Promise.reject({ response: { status: 500, data: { detail: 'Database unavailable.' } } }), 'Database unavailable.'],
    ['says no status is defined yet', () => Promise.resolve({ data: [] }), 'No statuses defined yet.'],
  ])('%s', async (_case, answer, message) => {
    vi.mocked(api.get).mockImplementation(answer);
    renderPage();
    expect(await screen.findByText(message)).toBeInTheDocument();
  });
});
