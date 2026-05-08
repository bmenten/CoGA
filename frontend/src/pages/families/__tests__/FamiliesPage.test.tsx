import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, it } from 'vitest';

import FamiliesPage from '../FamiliesPage';

describe('FamiliesPage', () => {
  it('redirects the legacy families landing route to the dashboard', async () => {
    render(
      <MemoryRouter initialEntries={['/families']}>
        <Routes>
          <Route path="/families" element={<FamiliesPage />} />
          <Route path="/dashboard" element={<div>Dashboard landing</div>} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText('Dashboard landing')).toBeInTheDocument();
  });
});
