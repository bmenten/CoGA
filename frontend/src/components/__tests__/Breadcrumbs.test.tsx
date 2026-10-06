import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import Breadcrumbs from '../Breadcrumbs';

const renderAt = (entry: string) => {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[entry]}>
        <Breadcrumbs />
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

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

test('reads route words in sentence case and shows identifiers as they are stored', () => {
  renderAt('/families/demo_family/mitochondrial-dna');

  expect(screen.getByText('Dashboard')).toBeInTheDocument();
  expect(screen.getByText('Families')).toBeInTheDocument();
  // An identifier keeps its case: it is not "Demo_family" or "DEMO_FAMILY".
  expect(screen.getByText('demo_family').closest('a')).toHaveAttribute(
    'href',
    '/families/demo_family',
  );
  expect(screen.getByText('Mitochondrial DNA')).toBeInTheDocument();
});

test('the families crumb leads to the dashboard, where the families are listed', () => {
  renderAt('/families/FAM_TRIO/small-variants');

  expect(screen.getByText('Families').closest('a')).toHaveAttribute('href', '/dashboard');
  expect(screen.getByText('FAM_TRIO').closest('a')).toHaveAttribute('href', '/families/FAM_TRIO');
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
