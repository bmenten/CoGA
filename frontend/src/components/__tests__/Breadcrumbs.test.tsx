import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import Breadcrumbs from '../Breadcrumbs';

test('preserves variant filters when navigating from chromosome to genome', () => {
  render(
    <MemoryRouter initialEntries={["/families/123/chromosome/1?af=0.5&start=1&end=2"]}>
      <Breadcrumbs />
    </MemoryRouter>
  );

  const link = screen.getByText('CHROMOSOME').closest('a');
  expect(link).toHaveAttribute('href', '/families/123/genome?af=0.5');
});

test('admin breadcrumb links back to dashboard', () => {
  render(
    <MemoryRouter initialEntries={["/admin/users"]}>
      <Breadcrumbs />
    </MemoryRouter>
  );

  const adminLink = screen.getByText('ADMIN').closest('a');
  expect(adminLink).toHaveAttribute('href', '/dashboard');
});
