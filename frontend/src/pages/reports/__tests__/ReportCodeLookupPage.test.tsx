// The report code the EPD report quotes finds the signed analysis record it names: typed in,
// or followed as a link. A code typed wrong is refused as one, and a code no record you can
// open has says so, the same whether no record has it or its family is outside your projects.
import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import ReportCodeLookupPage from '../ReportCodeLookupPage';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

const apiMock = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock('../../../lib/api', () => ({ default: apiMock }));

const FOUND = {
  report_code: '7KQM2X-2-4PHT',
  family_id: 'FAM 1',
  version: 2,
  signed_out_at: '2026-10-14T09:00:00Z',
  signed_out_by: 'lab-signer',
  content_hash: 'ab'.repeat(32),
};

const renderAt = (path: string) =>
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/report-codes" element={<ReportCodeLookupPage />} />
          <Route path="/report-codes/:code" element={<ReportCodeLookupPage />} />
          <Route path="/families/:familyId/report" element={<p>The family report</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );

describe('ReportCodeLookupPage', () => {
  beforeEach(() => {
    apiMock.get.mockReset();
  });

  it('finds the signed record a typed code names, and opens it', async () => {
    apiMock.get.mockResolvedValue({ data: FOUND });
    renderAt('/report-codes');

    expect(apiMock.get).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('Report code'), { target: { value: ' 7kqm2x-2-4pht ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Find the signed record' }));

    const found = await screen.findByLabelText('Report code 7KQM2X-2-4PHT');
    // Asked for as typed: the server reads the code, its case and its separators.
    expect(apiMock.get).toHaveBeenCalledWith('/report-codes/7kqm2x-2-4pht');
    expect(found).toHaveTextContent('Report code 7KQM2X-2-4PHT: signed version 2 of family FAM 1');
    expect(found).toHaveTextContent('Signed out by lab-signer on 2026-10-14 09:00 UTC.');
    const open = screen.getByRole('link', { name: 'Open signed version 2' });
    expect(open).toHaveAttribute('href', '/families/FAM%201/report?version=2');
    fireEvent.click(open);
    expect(await screen.findByText('The family report')).toBeInTheDocument();
  });

  it('looks a code up from a link, as the EPD can link to it', async () => {
    apiMock.get.mockResolvedValue({ data: FOUND });
    renderAt('/report-codes/7KQM2X-2-4PHT');

    expect(await screen.findByLabelText('Report code 7KQM2X-2-4PHT')).toBeInTheDocument();
    expect(apiMock.get).toHaveBeenCalledWith('/report-codes/7KQM2X-2-4PHT');
    expect(screen.getByLabelText('Report code')).toHaveValue('7KQM2X-2-4PHT');
  });

  it('says a code typed wrong is not a report code, in the server’s words', async () => {
    apiMock.get.mockRejectedValue({
      response: {
        status: 422,
        data: {
          detail: {
            code: 'report_code_invalid',
            message: 'This report code does not check out: a symbol was mistyped or two were swapped.',
          },
        },
      },
    });
    renderAt('/report-codes/7KQM2X-2-4PTH');

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'This report code does not check out: a symbol was mistyped or two were swapped.',
    );
  });

  it('says the same for a code no record has and one outside your projects', async () => {
    apiMock.get.mockRejectedValue({
      response: { status: 404, data: { detail: 'No signed record has this report code.' } },
    });
    renderAt('/report-codes/7KQM2X-9-4PHT');

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'No signed record you can open has this report code. Check it against the EPD report.',
    );
    expect(screen.queryByRole('link', { name: /Open signed version/ })).not.toBeInTheDocument();
  });

  it('says when the code could not be looked up at all', async () => {
    apiMock.get.mockRejectedValue({ response: { status: 500, data: {} } });
    renderAt('/report-codes/7KQM2X-2-4PHT');

    expect(await screen.findByRole('alert')).toHaveTextContent('The report code could not be looked up. Try again.');
  });
});
