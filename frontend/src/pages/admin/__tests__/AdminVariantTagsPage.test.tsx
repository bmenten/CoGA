// Pins the admin variant-tag page: the tags of a project context, a custom tag created, edited
// and deleted through the admin endpoints, with its scope (global, or a primary project and the
// projects it is shared with, never the primary one twice), and a refused change said with its
// reason. The built-in tags are not editable.
import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
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

const project = (id: string, name: string) => ({
  id,
  name,
  species_id: 'sp1',
  assembly_id: 'as1',
  user_ids: [],
  metadata: {},
});

const PROJECTS = [project('P1', 'Project One'), project('P2', 'Project Two'), project('P3', 'Project Three')];

const REVIEW = {
  key: 'review',
  label: 'Review',
  description: 'Built-in',
  group: 'collaboration',
  color: '#2563eb',
  sort_order: 10,
  scope: 'system',
  is_custom: false,
  shared_project_ids: [],
};

const FOLLOW_UP = {
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
};

/** Serves the project catalogue and the tags, with any project context. */
const serve = (projects: unknown[] = [PROJECTS[0]], tags: unknown[] = [REVIEW, FOLLOW_UP]) => {
  (api.get as unknown as Mock).mockImplementation((url: string) => {
    if (url === '/projects') return Promise.resolve({ data: projects });
    if (url === '/admin/variant-tags') return Promise.resolve({ data: tags });
    return Promise.resolve({ data: [] });
  });
};

const renderPage = () =>
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <MemoryRouter>
        <AdminVariantTagsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );

const createForm = () =>
  within(screen.getByRole('heading', { name: 'Create custom tag' }).closest('section') as HTMLElement);

describe('AdminVariantTagsPage', () => {
  beforeEach(() => {
    serve();
  });

  it('updates and deletes custom project-scoped tags via admin endpoints', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    (api.put as unknown as Mock).mockResolvedValue({ data: { ...FOLLOW_UP, label: 'Follow-up updated' } });
    (api.delete as unknown as Mock).mockResolvedValue({ status: 204 });

    renderPage();

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

  it('offers no edit of a built-in tag', async () => {
    renderPage();

    const review = (await screen.findByText('Review')).closest('tr') as HTMLElement;
    expect(within(review).getByText('Not editable')).toBeInTheDocument();
    expect(within(review).queryByRole('button')).not.toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'Edit' })).toHaveLength(1);
  });

  it('creates a project tag shared with other projects, never with its own primary project', async () => {
    serve(PROJECTS);
    (api.post as unknown as Mock).mockResolvedValue({ data: { ...FOLLOW_UP, key: 'custom_follow_up_sanger' } });
    renderPage();

    await screen.findByText('Variant tag management');
    const form = createForm();
    const create = form.getByRole('button', { name: 'Create tag' });
    // A tag needs its label.
    expect(create).toBeDisabled();
    fireEvent.change(form.getByLabelText('Label'), { target: { value: '  Follow-up Sanger  ' } });
    // The primary project starts as the first project; the others can share the tag.
    await waitFor(() => expect(form.getByLabelText('Primary project')).toHaveValue('P1'));
    fireEvent.click(form.getByRole('checkbox', { name: 'Project Two' }));
    fireEvent.click(form.getByRole('checkbox', { name: 'Project Three' }));
    // Project Two becomes the primary project: no longer one it is shared with.
    fireEvent.change(form.getByLabelText('Primary project'), { target: { value: 'P2' } });
    expect(form.queryByRole('checkbox', { name: 'Project Two' })).not.toBeInTheDocument();
    expect(form.getByRole('checkbox', { name: 'Project Three' })).toBeChecked();
    fireEvent.change(form.getByLabelText('Group'), { target: { value: 'collaboration' } });
    fireEvent.change(form.getByLabelText('Description'), { target: { value: 'Confirm by Sanger' } });
    fireEvent.click(create);

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        '/admin/variant-tags',
        {
          label: 'Follow-up Sanger',
          description: 'Confirm by Sanger',
          scope: 'project',
          project_id: 'P2',
          shared_project_ids: ['P3'],
          group: 'collaboration',
          color: '#5b6b79',
        },
        { params: undefined },
      ),
    );
    expect(await screen.findByText('Created tag "Follow-up Sanger".')).toHaveClass('status-note--success');
    // The form is ready for the next tag, on the same primary project.
    expect(form.getByLabelText('Label')).toHaveValue('');
    expect(form.getByLabelText('Primary project')).toHaveValue('P2');
  });

  it('creates a global tag without a project, and in a project context asks with that project', async () => {
    serve(PROJECTS);
    (api.post as unknown as Mock).mockResolvedValue({ data: { ...FOLLOW_UP, scope: 'global' } });
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /Project Two/ }));
    await waitFor(() =>
      expect(api.get).toHaveBeenCalledWith('/admin/variant-tags', { params: { project_id: 'P2' } }),
    );
    expect(screen.getByRole('heading', { name: 'Project Two', level: 2 })).toBeInTheDocument();

    const form = createForm();
    fireEvent.change(form.getByLabelText('Label'), { target: { value: 'Lab-wide' } });
    fireEvent.click(form.getByRole('checkbox', { name: 'Project Three' }));
    fireEvent.change(form.getByLabelText('Scope'), { target: { value: 'global' } });
    // A global tag has no primary project, and is shared with no project of its own.
    expect(form.getByLabelText('Primary project')).toBeDisabled();
    expect(form.getByLabelText('Primary project')).toHaveValue('');
    expect(form.queryByRole('checkbox')).not.toBeInTheDocument();
    fireEvent.click(form.getByRole('button', { name: 'Create tag' }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        '/admin/variant-tags',
        expect.objectContaining({ label: 'Lab-wide', scope: 'global', project_id: undefined, shared_project_ids: [] }),
        { params: { project_id: 'P2' } },
      ),
    );
  });

  it('deletes nothing unless confirmed, and says why a change was refused', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    (api.put as unknown as Mock).mockRejectedValue({ response: { status: 409, data: { detail: 'A tag named Follow-up exists.' } } });
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'Delete' }));
    expect(confirm).toHaveBeenCalledWith(
      'Delete custom tag "Follow-up"? Reviews that hold it keep it, marked deleted; it can no longer be added.',
    );
    expect(api.delete).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText('A tag named Follow-up exists.')).toHaveClass('status-note--error');
    // Still editing: the draft is not lost.
    expect(screen.getByDisplayValue('Follow-up')).toBeInTheDocument();
  });

  it('says the project catalogue could not be loaded', async () => {
    (api.get as unknown as Mock).mockImplementation((url: string) =>
      url === '/projects'
        ? Promise.reject({ response: { status: 500, data: { detail: 'Projects unavailable' } } })
        : Promise.resolve({ data: [] }),
    );
    renderPage();

    expect(await screen.findByRole('heading', { name: 'Could not load projects' })).toBeInTheDocument();
    expect(screen.getByText('Projects unavailable')).toBeInTheDocument();
  });
});
