import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { beforeEach, describe, it, vi } from 'vitest';
import ProjectsPage from '../ProjectsPage';
import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';
import { useFactoryGet } from '../../../test/factoryGet';

vi.mock('../../../lib/api', () => ({
  default: {
    get: vi.fn((url: string) => {
      if (url === '/projects') {
        return Promise.resolve({
          data: [
            {
              id: 'p1',
              name: 'Project One',
              description: 'Pilot oncology cohort',
              species_id: 'sp1',
              assembly_id: 'asm1',
              user_ids: ['u1'],
              reader_user_ids: [],
              families: [
                {
                  family_id: 'F2',
                  created_at: '2026-05-10T12:00:00Z',
                  members: [{ sample_id: 'S3' }],
                },
                {
                  family_id: 'F1',
                  created_at: '2026-04-01T12:00:00Z',
                  members: [{ sample_id: 'S1' }, { sample_id: 'S2' }],
                },
              ],
              samples: ['S1'],
            },
          ],
        });
      }
      if (url === '/species') {
        return Promise.resolve({
          data: [
            { _id: 'sp1', name: 'Homo sapiens', common_name: 'human' },
            { _id: 'sp2', name: 'Mus musculus', common_name: 'mouse' },
          ],
        });
      }
      if (url === '/assemblies') {
        return Promise.resolve({
          data: [
            { _id: 'asm1', assembly_name: 'GRCh38', version: 'p14', species_id: 'sp1' },
            { _id: 'asm2', assembly_name: 'GRCm39', version: 'v1', species_id: 'sp2' },
          ],
        });
      }
      if (url === '/auth/users') {
        return Promise.resolve({
          data: [
            {
              id: 'u1',
              email: 'viewer@example.com',
              first_name: 'View',
              last_name: 'Er',
              role: 'viewer',
              is_active: true,
              projects: ['p1'],
            },
            {
              id: 'u2',
              email: 'student@example.com',
              first_name: 'Stu',
              last_name: 'Dent',
              role: 'viewer',
              is_active: true,
              projects: [],
            },
          ],
        });
      }
      return Promise.resolve({ data: [] });
    }),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

type ProjectRow = Record<string, unknown>;

const renderPage = (queryClient: QueryClient = createTestQueryClient()) => {
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/projects']}>
        <Routes>
          <Route path="/projects" element={<ProjectsPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return queryClient;
};


const section = (heading: string) =>
  screen.getByRole('heading', { name: heading }).closest('section') as HTMLElement;

// A project that names its organism and assembly itself, with notes too long for the table,
// and one with no organism, assembly, notes or families.
const PROJECT_TWO = {
  id: 'p2',
  name: 'Project Two',
  description: `Carrier screening of couples ${'before a pregnancy '.repeat(8)}`,
  species_id: 'sp9',
  assembly_id: 'asm9',
  species_name: 'Danio rerio',
  assembly_name: 'GRCz11',
  assembly_version: null,
  user_ids: [],
  reader_user_ids: [],
  families: [{ family_id: 'F9', created_at: '2026-06-01T12:00:00Z', members: [{ sample_id: 'S9' }] }],
  samples: [],
};
const PROJECT_THREE = {
  id: 'p3',
  name: 'Project Three',
  description: '',
  user_ids: [],
  reader_user_ids: [],
  families: [],
  samples: [],
};

const catalogRow = (name: string) => screen.getByRole('button', { name }).closest('tr') as HTMLElement;

/** Clicks Delete project, then confirms in the dialog that asks first (UI-7). */
const deleteProject = () => {
  fireEvent.click(screen.getByRole('button', { name: 'Delete project' }));
  const dialog = screen.getByRole('dialog', { name: /^Delete project / });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Delete project' }));
};

describe('ProjectsPage', () => {
  const { get, factory } = useFactoryGet(api.get);

  /** Serves `projects(defaults)` at /projects, and the factory's answer everywhere else. */
  const serveProjects = (projects: (defaults: ProjectRow[]) => ProjectRow[]) =>
    get.mockImplementation(async (url, config) => {
      const response = await factory(url, config);
      return url === '/projects' ? { data: projects(response.data as ProjectRow[]) } : response;
    });

  const projectRequests = () => get.mock.calls.filter(([url]) => url === '/projects').length;

  beforeEach(() => {
    vi.mocked(api.post).mockReset().mockResolvedValue({ data: {} });
    vi.mocked(api.put).mockReset().mockResolvedValue({ data: {} });
    vi.mocked(api.delete).mockReset().mockResolvedValue({ data: {} });
    localStorage.setItem('role', 'admin');
  });

  it('shows a searchable project catalog and editable project settings', async () => {
    renderPage();

    await waitFor(() => expect(screen.getByText(/Project One/)).toBeInTheDocument());
    expect(screen.getByLabelText(/Search projects/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /create new project/i })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/2 families · 3 samples/)).toBeInTheDocument());
    expect(screen.getAllByText(/Homo sapiens/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/GRCh38 p14/i).length).toBeGreaterThan(0);
    await waitFor(() => expect(screen.getAllByDisplayValue('Project One').length).toBeGreaterThan(0));
    await waitFor(() =>
      expect(screen.getAllByDisplayValue('Pilot oncology cohort').length).toBeGreaterThan(0),
    );
    expect(screen.getByText(/View Er · viewer@example.com/)).toBeInTheDocument();
    expect(screen.getAllByText('F1').length).toBeGreaterThan(0);
    expect(screen.getByText('S1')).toBeInTheDocument();
    // The selected project reads top to bottom in one column: its settings, who has access,
    // then its families and samples; none of them a card inside the project's card.
    expect(screen.getAllByRole('heading', { level: 3 }).map((heading) => heading.textContent)).toEqual([
      'Project settings',
      'User access',
      'Project overview',
    ]);
    expect(document.querySelector('.surface-card .surface-card, .surface-card .surface-card-muted')).toBeNull();
  });

  // #610 — a failed list read "No projects yet", with counts of 0.
  it('says the projects could not be loaded, not that there are none, and retries', async () => {
    let failing = true;
    get.mockImplementation((url, config) =>
      failing && url === '/projects'
        ? Promise.reject(Object.assign(new Error('HTTP 500'), { response: { status: 500 } }))
        : factory(url, config),
    );
    renderPage();

    expect(
      await screen.findByText(/Could not load the projects — this is not an empty result/),
    ).toBeInTheDocument();
    expect(screen.queryByText('No projects yet')).not.toBeInTheDocument();
    expect(screen.queryByText(/Showing 0 of 0 projects/)).not.toBeInTheDocument();
    expect(screen.getByText('Projects —')).toBeInTheDocument();

    failing = false;
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Projects 1')).toBeInTheDocument();
  });

  it('says there are no projects yet when the list is empty, and selects none', async () => {
    serveProjects(() => []);
    renderPage();

    expect(await screen.findByText('No projects yet')).toBeInTheDocument();
    expect(screen.getByText(/Create the first project/)).toBeInTheDocument();
    expect(screen.getByText('Showing 0 of 0 projects.')).toBeInTheDocument();
    expect(screen.getByText('No project selected')).toBeInTheDocument();
    expect(screen.getByText('Projects 0')).toBeInTheDocument();
  });

  // The catalogue reads a project's organism and assembly from its own names when it has
  // them, else from the records it points to, and says which are not set.
  it('lists each project’s organism, assembly and notes, or says they are not set', async () => {
    serveProjects((defaults) => [...defaults, PROJECT_TWO, PROJECT_THREE]);
    renderPage();

    await screen.findByRole('button', { name: 'Project Two' });
    await waitFor(() => expect(within(catalogRow('Project One')).getByText('GRCh38 p14')).toBeInTheDocument());
    await waitFor(() => expect(within(catalogRow('Project One')).getByText('Homo sapiens')).toBeInTheDocument());
    expect(within(catalogRow('Project One')).getByText('Pilot oncology cohort')).toBeInTheDocument();

    const two = catalogRow('Project Two');
    expect(within(two).getByText('Danio rerio')).toBeInTheDocument();
    expect(within(two).getByText('GRCz11')).toBeInTheDocument();
    const notes = two.querySelector('.project-catalog-note-cell')!.textContent!;
    expect(notes).toMatch(/^Carrier screening of couples before a pregnancy .*…$/);
    expect(notes).toHaveLength(110);

    const three = catalogRow('Project Three');
    expect(within(three).getByText('Not set')).toBeInTheDocument();
    expect(within(three).getByText('Reference not set')).toBeInTheDocument();
    expect(within(three).getByText('No linked families')).toBeInTheDocument();
    expect(three.querySelector('.project-catalog-note-cell')).toHaveTextContent(/^—$/);

    fireEvent.click(screen.getByRole('button', { name: 'Project Three' }));
    expect(await screen.findByRole('heading', { level: 2, name: 'Project Three' })).toBeInTheDocument();
    expect(screen.getByText('0 families · 0 samples')).toBeInTheDocument();
    expect(screen.getByText('No families are currently linked to this project.')).toBeInTheDocument();
    expect(screen.getByText('No project notes have been added yet.')).toBeInTheDocument();
  });

  // The search reads a project's title, notes, organism, assembly, family IDs and sample IDs,
  // case-insensitively; a selected project the search hides gives way to the first it shows.
  it.each([
    ['its title', 'project TWO', 'Project Two'],
    ['its notes', 'carrier screening', 'Project Two'],
    ['the organism it names', 'danio', 'Project Two'],
    ['the assembly it names', 'grcz11', 'Project Two'],
    ['the organism of the record it points to', 'homo sapiens', 'Project One'],
    ['the assembly of the record it points to', 'grch38 p14', 'Project One'],
    ['a family ID', 'f9', 'Project Two'],
    ['a sample ID', 's9', 'Project Two'],
  ])('finds a project by %s', async (_field, term, expected) => {
    serveProjects((defaults) => [...defaults, PROJECT_TWO, PROJECT_THREE]);
    renderPage();
    await screen.findByRole('button', { name: 'Project Two' });

    fireEvent.change(screen.getByLabelText(/Search projects/i), { target: { value: term } });

    expect(await screen.findByText('Showing 1 of 3 projects.')).toBeInTheDocument();
    const catalog = section('Existing projects');
    expect(within(catalog).getAllByRole('button').map((button) => button.textContent)).toEqual([expected]);
    expect(await screen.findByRole('heading', { level: 2, name: expected })).toBeInTheDocument();
  });

  it('says no project matches a search that finds none, and lists them all again once cleared', async () => {
    serveProjects((defaults) => [...defaults, PROJECT_TWO]);
    renderPage();
    await screen.findByRole('button', { name: 'Project Two' });
    const search = screen.getByLabelText(/Search projects/i);

    fireEvent.change(search, { target: { value: 'no such cohort' } });
    expect(await screen.findByText('No matching projects')).toBeInTheDocument();
    expect(screen.getByText(/Adjust the search/)).toBeInTheDocument();
    expect(screen.getByText('Showing 0 of 2 projects.')).toBeInTheDocument();
    expect(screen.getByText('No project selected')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Save settings' })).not.toBeInTheDocument();

    fireEvent.change(search, { target: { value: '   ' } });
    expect(await screen.findByText('Showing 2 of 2 projects.')).toBeInTheDocument();
    expect(await screen.findByRole('heading', { level: 2, name: 'Project One' })).toBeInTheDocument();
  });

  // SEC-1: each member is an analyst (read and change) or a reader (read only); a member added
  // starts as an analyst, and the save sends the readers among the members.
  it('sets each member’s access level and saves the readers with the members', async () => {
    renderPage();

    const level = await screen.findByRole('combobox', { name: 'Access level of viewer@example.com' });
    expect(level).toHaveValue('analyst');
    // No level for someone who is no member.
    expect(screen.queryByRole('combobox', { name: 'Access level of student@example.com' })).not.toBeInTheDocument();

    const userAccess = section('User access');
    fireEvent.click(within(userAccess).getByRole('button', { name: /^Add Stu Dent/ }));
    const studentLevel = screen.getByRole('combobox', { name: 'Access level of student@example.com' });
    expect(studentLevel).toHaveValue('analyst');
    fireEvent.change(studentLevel, { target: { value: 'reader' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));

    await waitFor(() => expect(api.put).toHaveBeenCalledTimes(1));
    // The save sends the settings it did not change too, under the project's own path.
    expect(vi.mocked(api.put).mock.calls[0]).toEqual([
      '/projects/p1',
      {
        name: 'Project One',
        description: 'Pilot oncology cohort',
        species_id: 'sp1',
        assembly_id: 'asm1',
        user_ids: ['u1', 'u2'],
        reader_user_ids: ['u2'],
      },
    ]);
    expect(await screen.findByText('Saved Project One.')).toBeInTheDocument();
  });

  // SEC-1: a member removed is no reader, so one removed and added again is an analyst; and a
  // reader set back to analyst is no longer among the readers.
  it('makes a member removed and added again an analyst, and saves a reader set back as an analyst', async () => {
    serveProjects((defaults) => defaults.map((project) => ({ ...project, user_ids: ['u1', 'u2'], reader_user_ids: ['u1', 'u2'] })));
    renderPage();

    const studentLevel = await screen.findByRole('combobox', { name: 'Access level of student@example.com' });
    await waitFor(() => expect(studentLevel).toHaveValue('reader'));
    const userAccess = section('User access');
    fireEvent.click(within(userAccess).getByRole('button', { name: /^Remove Stu Dent/ }));
    expect(screen.queryByRole('combobox', { name: 'Access level of student@example.com' })).not.toBeInTheDocument();
    fireEvent.click(within(userAccess).getByRole('button', { name: /^Add Stu Dent/ }));
    expect(screen.getByRole('combobox', { name: 'Access level of student@example.com' })).toHaveValue('analyst');
    fireEvent.change(screen.getByRole('combobox', { name: 'Access level of viewer@example.com' }), {
      target: { value: 'analyst' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));

    await waitFor(() => expect(api.put).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.put).mock.calls[0][1]).toMatchObject({ user_ids: ['u1', 'u2'], reader_user_ids: [] });
  });

  // However many accounts there are, User access lists the members and a few users without
  // access; a search finds the others, and Enter adds the one user it found.
  it('lists a few users without access at a time and finds the others by a search', async () => {
    const accounts = [
      { id: 'u1', email: 'viewer@example.com', first_name: 'View', last_name: 'Er', role: 'viewer', is_active: true, projects: ['p1'] },
      ...Array.from({ length: 12 }, (_, index) => ({
        id: `n${index + 1}`,
        email: `user${String(index + 1).padStart(2, '0')}@example.com`,
        first_name: null,
        last_name: null,
        role: 'viewer',
        is_active: true,
        projects: [],
      })),
    ];
    get.mockImplementation(async (url, config) =>
      url === '/auth/users' ? { data: accounts } : factory(url, config),
    );
    renderPage();

    await screen.findByRole('combobox', { name: 'Access level of viewer@example.com' });
    const userAccess = section('User access');
    const listed = () =>
      within(userAccess)
        .queryAllByRole('button', { name: /^Add / })
        .map((button) => button.getAttribute('aria-label'));
    // The first eight by name, and how many more there are.
    expect(listed()).toEqual(
      Array.from({ length: 8 }, (_, index) => `Add user0${index + 1}@example.com`),
    );
    expect(within(userAccess).getByText('8 of 12 shown: search by name or email for the others.')).toBeInTheDocument();

    const search = within(userAccess).getByRole('searchbox', { name: 'Add a member' });
    fireEvent.change(search, { target: { value: 'USER1' } });
    expect(listed()).toEqual(['Add user10@example.com', 'Add user11@example.com', 'Add user12@example.com']);
    expect(within(userAccess).queryByText(/ shown: search/)).not.toBeInTheDocument();

    fireEvent.change(search, { target: { value: 'nobody' } });
    expect(within(userAccess).getByText('No user without access matches “nobody”.')).toBeInTheDocument();

    // Enter adds the single match, clears the search, and submits nothing.
    fireEvent.change(search, { target: { value: 'user12' } });
    expect(fireEvent.keyDown(search, { key: 'Enter' })).toBe(false);
    expect(screen.getByRole('combobox', { name: 'Access level of user12@example.com' })).toHaveValue('analyst');
    expect(search).toHaveValue('');
    expect(api.put).not.toHaveBeenCalled();

    // Enter with several matches adds none of them.
    fireEvent.change(search, { target: { value: 'user0' } });
    fireEvent.keyDown(search, { key: 'Enter' });
    expect(screen.queryByRole('combobox', { name: 'Access level of user01@example.com' })).not.toBeInTheDocument();
  });

  it('says only superusers and administrators open a project without members', async () => {
    serveProjects((defaults) => defaults.map((project) => ({ ...project, user_ids: [], reader_user_ids: [] })));
    renderPage();

    expect(
      await screen.findByText('No members yet: only superusers and administrators can open the project.'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: /^Access level/ })).not.toBeInTheDocument();
    fireEvent.click(within(section('User access')).getByRole('button', { name: /^Add View Er/ }));
    expect(screen.getByRole('combobox', { name: 'Access level of viewer@example.com' })).toHaveValue('analyst');
    expect(screen.queryByText(/^No members yet/)).not.toBeInTheDocument();
  });

  // A project's assembly belongs to its organism: the assembly list offers the organism's own
  // only, and a change of organism clears the assembly chosen for the previous one.
  it('offers the selected organism’s assemblies only, and clears the assembly when the organism changes', async () => {
    renderPage();
    await screen.findByRole('heading', { level: 2, name: 'Project One' });
    const settings = section('Project settings');
    const organism = within(settings).getByRole('combobox', { name: /^Organism/ });
    const assembly = within(settings).getByRole('combobox', { name: /^Assembly/ });
    await waitFor(() => expect(assembly).toHaveValue('asm1'));
    const offered = () =>
      within(assembly).getAllByRole('option').map((option) => option.textContent);
    expect(offered()).toEqual(['Select assembly', 'GRCh38 p14']);

    fireEvent.change(organism, { target: { value: 'sp2' } });
    expect(assembly).toHaveValue('');
    expect(offered()).toEqual(['Select assembly', 'GRCm39 v1']);
    fireEvent.change(assembly, { target: { value: 'asm2' } });
    fireEvent.change(within(settings).getByRole('textbox', { name: /^Title/ }), { target: { value: 'Mouse model' } });
    fireEvent.change(within(settings).getByRole('textbox', { name: /^Notes/ }), { target: { value: 'Knock-out line' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));

    await waitFor(() => expect(api.put).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.put).mock.calls[0][1]).toMatchObject({
      name: 'Mouse model',
      description: 'Knock-out line',
      species_id: 'sp2',
      assembly_id: 'asm2',
    });
  });

  it('creates a project with its organism, assembly and members, then closes the form', async () => {
    renderPage();
    await screen.findByRole('heading', { level: 2, name: 'Project One' });
    expect(projectRequests()).toBe(1);

    fireEvent.click(screen.getByRole('button', { name: 'Create new project' }));
    const form = screen.getByRole('region', { name: 'New project' });
    const assembly = within(form).getByRole('combobox', { name: /^Assembly/ });
    // No assembly before an organism.
    expect(assembly).toBeDisabled();
    fireEvent.change(within(form).getByRole('textbox', { name: /^Title/ }), { target: { value: 'Carrier couples' } });
    fireEvent.change(within(form).getByRole('textbox', { name: /^Notes/ }), { target: { value: 'Preconception' } });
    fireEvent.change(within(form).getByRole('combobox', { name: /^Organism/ }), { target: { value: 'sp2' } });
    fireEvent.change(assembly, { target: { value: 'asm2' } });
    // Changing the organism clears the assembly, and offers that organism's assemblies only.
    fireEvent.change(within(form).getByRole('combobox', { name: /^Organism/ }), { target: { value: 'sp1' } });
    expect(assembly).toHaveValue('');
    expect(within(assembly).getAllByRole('option').map((option) => option.textContent)).toEqual([
      'Select assembly',
      'GRCh38 p14',
    ]);
    fireEvent.change(assembly, { target: { value: 'asm1' } });
    fireEvent.click(within(form).getByRole('button', { name: /^Add Stu Dent/ }));
    fireEvent.change(within(form).getByRole('combobox', { name: 'Access level of student@example.com' }), {
      target: { value: 'reader' },
    });
    fireEvent.click(within(form).getByRole('button', { name: /^Add View Er/ }));
    fireEvent.click(within(form).getByRole('button', { name: 'Create project' }));

    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.post).mock.calls[0]).toEqual([
      '/projects',
      {
        name: 'Carrier couples',
        description: 'Preconception',
        species_id: 'sp1',
        assembly_id: 'asm1',
        user_ids: ['u2', 'u1'],
        reader_user_ids: ['u2'],
      },
    ]);
    expect(await screen.findByText('Project created.')).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'New project' })).not.toBeInTheDocument();
    await waitFor(() => expect(projectRequests()).toBe(2));

    // Opened again, the form starts empty.
    fireEvent.click(screen.getByRole('button', { name: 'Create new project' }));
    const reopened = screen.getByRole('region', { name: 'New project' });
    expect(within(reopened).getByRole('textbox', { name: /^Title/ })).toHaveValue('');
    expect(within(reopened).queryByRole('combobox', { name: /^Access level/ })).not.toBeInTheDocument();
    // The status of the last action clears with the toggle.
    expect(screen.queryByText('Project created.')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Close new project form' }));
    expect(screen.queryByRole('region', { name: 'New project' })).not.toBeInTheDocument();
  });

  /** Project One as served, without its families: a project that can be deleted. */
  const serveWithoutFamilies = () =>
    serveProjects((defaults) => defaults.map((project) => ({ ...project, families: [] })));

  it('deletes the selected project under its own path and reloads the list', async () => {
    serveWithoutFamilies();
    renderPage();
    await screen.findByRole('heading', { level: 2, name: 'Project One' });

    deleteProject();

    await waitFor(() => expect(api.delete).toHaveBeenCalledWith('/projects/p1'));
    expect(await screen.findByText('Project deleted.')).toBeInTheDocument();
    await waitFor(() => expect(projectRequests()).toBe(2));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  // A family's small and structural variants are stored per project, and a project's deletion
  // would unlink its families and leave their variants under the deleted project, where none of
  // their pages looks. A project families are linked to is not deleted (the server refuses it, 409):
  // Delete project is off and says why, naming how many, until each family is moved or deleted.
  it.each<[string, ProjectRow[], string]>([
    [
      'one family',
      [],
      "1 family is linked to this project. Move it to other projects, or to none, or delete it in Admin → Family & sample data first: a family's small and structural variants are stored per project, and would stay behind under the deleted one.",
    ],
    [
      'more families',
      [{ family_id: 'F1', members: [] }, { family_id: 'F2', members: [] }],
      "2 families are linked to this project. Move each to other projects, or to none, or delete them in Admin → Family & sample data first: a family's small and structural variants are stored per project, and would stay behind under the deleted one.",
    ],
  ])('keeps Delete project off for a project with %s, saying why', async (_how, families, reason) => {
    serveProjects(() => [{ ...PROJECT_TWO, families: families.length ? families : PROJECT_TWO.families }]);
    renderPage();
    await screen.findByRole('heading', { level: 2, name: 'Project Two' });

    const button = screen.getByRole('button', { name: 'Delete project' });
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription(reason);
    fireEvent.click(button);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(api.delete).not.toHaveBeenCalled();
  });

  // UI-7: a project was deleted on one click. It now asks first, saying what goes with it (its
  // members' access; its review tags, kept on the reviews: SAFE-37), and sends nothing until
  // confirmed: neither on Cancel nor on Escape.
  it('asks before deleting a project, saying what goes with it, and sends nothing on cancel', async () => {
    serveWithoutFamilies();
    renderPage();
    await screen.findByRole('heading', { level: 2, name: 'Project One' });

    const button = screen.getByRole('button', { name: 'Delete project' });
    expect(button).toBeEnabled();
    expect(button).not.toHaveAttribute('aria-describedby');
    fireEvent.click(button);
    const dialog = screen.getByRole('dialog', { name: 'Delete project Project One' });
    expect(dialog).toHaveTextContent('This deletes the project Project One, which no family is linked to.');
    expect(within(dialog).getAllByRole('listitem').map((item) => item.textContent)).toEqual([
      '1 member loses the access the project gave them.',
      'Review tags defined for this project are deleted with it: each stays on the reviews that hold it, marked deleted, and can no longer be added.',
    ]);
    expect(dialog).toHaveTextContent('This cannot be undone.');
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Delete project' }));
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(api.delete).not.toHaveBeenCalled();
    expect(projectRequests()).toBe(1);
  });

  // A project with no member says only what happens to its tags, rather than counting zeros.
  it('says a project to delete without members only loses its tags', async () => {
    serveProjects(() => [PROJECT_THREE]);
    renderPage();
    await screen.findByRole('heading', { level: 2, name: 'Project Three' });

    fireEvent.click(screen.getByRole('button', { name: 'Delete project' }));
    const dialog = screen.getByRole('dialog', { name: 'Delete project Project Three' });
    expect(within(dialog).getAllByRole('listitem').map((item) => item.textContent)).toEqual([
      'Review tags defined for this project are deleted with it: each stays on the reviews that hold it, marked deleted, and can no longer be added.',
    ]);
  });

  // A refused write says why (the server's reason, else a fixed sentence) and changes nothing
  // shown: the create form stays open with what was typed.
  it.each<['create' | 'save' | 'delete', string, string | undefined, string]>([
    ['create', 'the server’s reason', 'A project with this name already exists.', 'A project with this name already exists.'],
    ['create', 'a fixed sentence without one', undefined, 'Unable to create project.'],
    ['save', 'the server’s reason', 'Assembly does not belong to the organism.', 'Assembly does not belong to the organism.'],
    ['save', 'a fixed sentence without one', undefined, 'Unable to save project.'],
    // A family linked to the project after the list was read: the server refuses it.
    ['delete', 'the server’s reason', '1 family is linked to this project.', '1 family is linked to this project.'],
    ['delete', 'a fixed sentence without one', undefined, 'Unable to delete project.'],
  ])('says why a refused %s failed: %s', async (action, _how, detail, message) => {
    const refusal = detail ? { response: { status: 409, data: { detail } } } : {};
    if (action === 'delete') serveWithoutFamilies();
    renderPage();
    await screen.findByRole('heading', { level: 2, name: 'Project One' });

    if (action === 'create') {
      vi.mocked(api.post).mockRejectedValueOnce(refusal);
      fireEvent.click(screen.getByRole('button', { name: 'Create new project' }));
      const form = screen.getByRole('region', { name: 'New project' });
      fireEvent.change(within(form).getByRole('textbox', { name: /^Title/ }), { target: { value: 'Project One' } });
      fireEvent.click(within(form).getByRole('button', { name: 'Create project' }));
    } else if (action === 'save') {
      vi.mocked(api.put).mockRejectedValueOnce(refusal);
      fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    } else {
      vi.mocked(api.delete).mockRejectedValueOnce(refusal);
      deleteProject();
    }

    const status = await screen.findByText(message);
    expect(status).toHaveClass('status-note--error');
    expect(projectRequests()).toBe(1);
    if (action === 'create') {
      const form = screen.getByRole('region', { name: 'New project' });
      expect(within(form).getByRole('textbox', { name: /^Title/ })).toHaveValue('Project One');
      expect(within(form).getByRole('button', { name: 'Create project' })).toBeEnabled();
    }
  });

  // ENG-10: the Users page read the accounts' projects from its own cache, and showed a saved
  // change of access only once that went stale, up to 30 s later.
  it('refreshes the account list the Users page shows once project access is saved', async () => {
    const queryClient = createTestQueryClient();
    queryClient.setQueryData(['admin', 'projects'], [{ id: 'p1', name: 'Project One' }]);
    renderPage(queryClient);

    await screen.findByRole('combobox', { name: 'Access level of viewer@example.com' });
    expect(queryClient.getQueryState(['admin', 'users'])?.isInvalidated).toBe(false);

    const userAccess = section('User access');
    fireEvent.click(within(userAccess).getByRole('button', { name: /^Add Stu Dent/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));

    await waitFor(() => expect(api.put).toHaveBeenCalledTimes(1));
    // The users list is fetched again (it is in use here); the Users page's projects are stale.
    await waitFor(() =>
      expect(get.mock.calls.filter(([url]) => url === '/auth/users')).toHaveLength(2),
    );
    expect(queryClient.getQueryState(['admin', 'projects'])?.isInvalidated).toBe(true);
  });

  it('counts a project’s readers among its users', async () => {
    serveProjects((defaults) => defaults.map((project) => ({ ...project, user_ids: ['u1', 'u2'], reader_user_ids: ['u2'] })));
    renderPage();
    expect(await screen.findByText('2 (1 reader)')).toBeInTheDocument();
    expect(await screen.findByRole('combobox', { name: 'Access level of student@example.com' })).toHaveValue('reader');
  });

  // Families sort by upload date, newest first, by default; a family without a date sorts as
  // the oldest, and ties fall back to the family ID in natural order (F2 before F10).
  it('sorts linked families by upload date newest first by default, and by each column on a click', async () => {
    serveProjects((defaults) =>
      defaults.map((project) => ({
        ...project,
        families: [
          ...(project.families as ProjectRow[]),
          { family_id: 'F10', created_at: null, members: [{ sample_id: 'S0' }] },
        ],
      })),
    );
    renderPage();

    await screen.findByRole('heading', { level: 2, name: 'Project One' });
    const linkedFamiliesSection = screen
      .getByRole('heading', { name: /linked families/i })
      .closest('.project-linked-families') as HTMLElement;
    const order = () =>
      within(linkedFamiliesSection)
        .getAllByRole('row')
        .slice(1)
        .map((row) => row.querySelector('td')!.textContent);
    const sortBy = (column: RegExp) => fireEvent.click(within(linkedFamiliesSection).getByText(column));

    await waitFor(() => expect(order()).toEqual(['F2', 'F1', 'F10']));
    expect(within(linkedFamiliesSection).getByText(/Uploaded/)).toHaveTextContent('Uploaded ▼');
    expect(linkedFamiliesSection.querySelector('tbody tr .project-family-uploaded-cell')).toHaveAttribute(
      'title',
      '2026-05-10T12:00:00Z',
    );

    sortBy(/Uploaded/);
    expect(order()).toEqual(['F10', 'F1', 'F2']);
    expect(within(linkedFamiliesSection).getByText(/Uploaded/)).toHaveTextContent('Uploaded ▲');

    // F2 and F10 have one sample each: the tie goes to the family ID.
    sortBy(/^Samples/);
    expect(order()).toEqual(['F2', 'F10', 'F1']);
    expect(within(linkedFamiliesSection).getByText(/Uploaded/)).toHaveTextContent(/^Uploaded\s*$/);

    sortBy(/^Sample IDs/);
    expect(order()).toEqual(['F10', 'F1', 'F2']);

    sortBy(/^Family/);
    expect(order()).toEqual(['F1', 'F2', 'F10']);
    sortBy(/^Family/);
    expect(order()).toEqual(['F10', 'F2', 'F1']);
  });
});
