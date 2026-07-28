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

  const link = screen.getByText('CHROMOSOME').closest('a');
  expect(link).toHaveAttribute('href', '/families/123/genome?af=0.5');
});

test('admin breadcrumb links back to admin dashboard', () => {
  renderAt('/admin/users');

  const adminLink = screen.getByText('ADMIN').closest('a');
  expect(adminLink).toHaveAttribute('href', '/admin');
});

test('admin access intermediate breadcrumb points to admin dashboard', () => {
  renderAt('/admin/access/projects');

  const accessLink = screen.getByText('ACCESS').closest('a');
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
    screen.getByText('1Q21.1 RECURRENT (TAR SYNDROME) REGION'),
  ).toBeInTheDocument();
  expect(screen.queryByText('Cnv 1')).not.toBeInTheDocument();
  // The "cnv-details" crumb reads "CNV EXPLORER" and links to the overview.
  const explorerLink = screen.getByText('CNV EXPLORER').closest('a');
  expect(explorerLink).toHaveAttribute('href', '/cnv-explorer');
});
