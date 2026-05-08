import { render, screen } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { describe, it } from 'vitest';
import CnvDetailsPage from '../CnvDetailsPage';

describe('CnvDetailsPage', () => {
  it('renders provided HTML content', () => {
    const html = '<p>CNV info</p>';
    render(
      <MemoryRouter initialEntries={[{ pathname: '/cnv-details', state: { html } }]}> 
        <Routes>
          <Route path="/cnv-details" element={<CnvDetailsPage />} />
        </Routes>
      </MemoryRouter>
    );
    expect(screen.getByText('CNV info')).toBeInTheDocument();
  });

  it('shows fallback when no content provided', () => {
    render(
      <MemoryRouter initialEntries={['/cnv-details']}>
        <Routes>
          <Route path="/cnv-details" element={<CnvDetailsPage />} />
        </Routes>
      </MemoryRouter>
    );
    expect(screen.getByText(/no details available/i)).toBeInTheDocument();
  });
});
