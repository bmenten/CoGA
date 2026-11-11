import { act, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, vi } from 'vitest';
import Breadcrumbs from '../Breadcrumbs';

const apiMock = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock('../../lib/api', () => ({ default: apiMock }));

// The project catalog a family workspace's crumb reads: PROBAND_FAM is linked to both
// projects, the second one first; TRIO_FAM to one.
const PROJECTS = [
  {
    _id: 'p-first-listed',
    name: 'Cardiogenetics',
    families: [{ family_id: 'PROBAND_FAM', projects: ['p-linked-first', 'p-first-listed'] }],
  },
  {
    _id: 'p-linked-first',
    name: 'Rare disease WGS',
    families: [
      { family_id: 'PROBAND_FAM', projects: ['p-linked-first', 'p-first-listed'] },
      { family_id: 'TRIO_FAM', projects: ['p-linked-first'] },
    ],
  },
];

beforeEach(() => {
  apiMock.get.mockReset();
  apiMock.get.mockImplementation((url: string) =>
    url === '/projects'
      ? Promise.resolve({ data: PROJECTS })
      : Promise.reject(new Error(`unexpected request ${url}`)),
  );
});

const renderAt = (entry: string) => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[entry]}>
        <Breadcrumbs />
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

/** The crumbs' text, in order, separators left out. */
const crumbTexts = () =>
  Array.from(
    screen.getByRole('navigation').querySelectorAll('.breadcrumb-link, .breadcrumb-current'),
  ).map((crumb) => crumb.textContent);

test('preserves variant filters when navigating from chromosome to genome', () => {
  renderAt('/families/123/chromosome/1?af=0.5&start=1&end=2');

  const link = screen.getByText('Chromosome').closest('a');
  expect(link).toHaveAttribute('href', '/families/123/genome?af=0.5');
});

test('admin breadcrumb links back to admin dashboard', () => {
  renderAt('/admin/users');

  const adminLink = screen.getByText('Admin').closest('a');
  expect(adminLink).toHaveAttribute('href', '/admin');
});

test('admin access intermediate breadcrumb points to admin dashboard', () => {
  renderAt('/admin/access/projects');

  const accessLink = screen.getByText('Access').closest('a');
  expect(accessLink).toHaveAttribute('href', '/admin');
});

test('admin family structure id breadcrumb links back to families list', () => {
  renderAt('/admin/data/families/F1/structure');

  const familyIdLink = screen.getByText('F1').closest('a');
  expect(familyIdLink).toHaveAttribute('href', '/admin/data/families');
});

test('shows the clinical CNV name in the breadcrumb instead of its id', () => {
  const queryClient = new QueryClient();
  queryClient.setQueryData(['clinical-cnv', 'cnv-1'], {
    _id: 'cnv-1',
    chr: '5',
    start: 1,
    end: 2,
    label: '1q21.1 recurrent (TAR syndrome) region',
  });

  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/cnv-details/cnv-1']}>
        <Breadcrumbs />
      </MemoryRouter>
    </QueryClientProvider>,
  );

  expect(
    screen.getByText('1q21.1 recurrent (TAR syndrome) region'),
  ).toBeInTheDocument();
  expect(screen.queryByText('Cnv 1')).not.toBeInTheDocument();
  // The "cnv-details" crumb reads "CNV explorer" and links to the overview.
  const explorerLink = screen.getByText('CNV explorer').closest('a');
  expect(explorerLink).toHaveAttribute('href', '/cnv-explorer');
});

test('shows the gene panel name in the breadcrumb instead of its id', () => {
  const panelId = '6f1c2a40-1111-4222-8333-944455556666';
  const queryClient = new QueryClient();
  queryClient.setQueryData(['panel', panelId], { _id: panelId, name: 'Cardiomyopathy', genes: [], regions: [] });

  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/panels/${panelId}`]}>
        <Breadcrumbs />
      </MemoryRouter>
    </QueryClientProvider>,
  );

  expect(screen.getByText('Cardiomyopathy')).toHaveClass('breadcrumb-current');
  expect(screen.queryByText(panelId)).not.toBeInTheDocument();
  expect(screen.getByText('Panels').closest('a')).toHaveAttribute('href', '/panels');
});

test('shows the gene panel id until its page has read the panel', () => {
  renderAt('/panels/6f1c2a40-1111-4222-8333-944455556666');

  expect(screen.getByText('6f1c2a40-1111-4222-8333-944455556666')).toHaveClass('breadcrumb-current');
});

test.each([['mitochondrial-dna', 'Mitochondrial DNA'], ['repeat-expansions', 'Repeat expansions']])('reads %s in sentence case and shows identifiers as they are stored', (route, label) => {
  renderAt(`/families/demo_family/${route}`);

  expect(screen.getByText('Dashboard')).toBeInTheDocument();
  expect(screen.getByText('Families')).toBeInTheDocument();
  // An identifier keeps its case: it is not "Demo_family" or "DEMO_FAMILY".
  expect(screen.getByText('demo_family').closest('a')).toHaveAttribute(
    'href',
    '/families/demo_family',
  );
  expect(screen.getByText(label)).toBeInTheDocument();
});

test('the families crumb leads to the dashboard, where the families are listed', () => {
  renderAt('/families/FAM_TRIO/small-variants');

  expect(screen.getByText('Families').closest('a')).toHaveAttribute('href', '/dashboard');
  expect(screen.getByText('FAM_TRIO').closest('a')).toHaveAttribute('href', '/families/FAM_TRIO');
});

test('a family workspace shows its project between Families and the family, leading to the dashboard with it open', async () => {
  renderAt('/families/TRIO_FAM/small-variants');

  const projectLink = (await screen.findByText('Rare disease WGS')).closest('a');
  expect(projectLink).toHaveAttribute('href', '/dashboard?project_id=p-linked-first');
  expect(crumbTexts()).toEqual(['Dashboard', 'Families', 'Rare disease WGS', 'TRIO_FAM', 'Small variants']);
  expect(apiMock.get).toHaveBeenCalledWith('/projects');
});

test('the project crumb is the one the workspace works in: its project_id, else the family’s first linked project', async () => {
  const { unmount } = renderAt('/families/PROBAND_FAM?project_id=p-first-listed');
  expect((await screen.findByText('Cardiogenetics')).closest('a')).toHaveAttribute(
    'href',
    '/dashboard?project_id=p-first-listed',
  );
  expect(screen.queryByText('Rare disease WGS')).not.toBeInTheDocument();
  unmount();

  // Without one, the family's first linked project, not the first the catalog lists.
  renderAt('/families/PROBAND_FAM');
  expect(await screen.findByText('Rare disease WGS')).toBeInTheDocument();
  expect(screen.queryByText('Cardiogenetics')).not.toBeInTheDocument();
});

test('a project_id the family is not linked to does not name a project', async () => {
  renderAt('/families/TRIO_FAM?project_id=p-first-listed');

  expect(await screen.findByText('Rare disease WGS')).toBeInTheDocument();
  expect(screen.queryByText('Cardiogenetics')).not.toBeInTheDocument();
});

test('a family under no project has no project crumb', async () => {
  renderAt('/families/UNLINKED_FAM/small-variants');

  await vi.waitFor(() => expect(apiMock.get).toHaveBeenCalledWith('/projects'));
  // Once the catalog has come, still no project between Families and the family.
  await act(async () => {});
  expect(crumbTexts()).toEqual(['Dashboard', 'Families', 'UNLINKED_FAM', 'Small variants']);
});

test('only a family workspace reads the project catalog', () => {
  renderAt('/admin/data/families/TRIO_FAM/structure');
  renderAt('/panels');

  expect(apiMock.get).not.toHaveBeenCalled();
});

test('the admin families crumb stays on the admin families page', () => {
  renderAt('/admin/data/families/F1/structure');

  expect(screen.getByText('Families').closest('a')).toHaveAttribute('href', '/admin/data/families');
});

test('the reference crumb of a reference doc leads to the user guide', () => {
  renderAt('/docs/reference/sample-qc');

  expect(screen.getByText('Reference').closest('a')).toHaveAttribute('href', '/docs');
  expect(screen.getByText('Sample QC')).toBeInTheDocument();
});
