// The repeat catalogue's admin page: each locus with QA's record of whether TRGT's count there is
// the clinical repeat number (the counting units), set with a required reason, and the record's
// history from the clinical audit trail. Until a locus is validated, the repeat page and the
// genome tracks mark a count near a boundary there (owner's decision, 2026-10-14).
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import AdminRepeatLociPage from '../AdminRepeatLociPage';
import api from '../../../lib/api';
import type { RepeatLocusCountUnitsCatalogueOut, RepeatLocusCountUnitsOut } from '../../../lib/apiSchema.generated';

vi.mock('../../../lib/api', () => ({
  default: { get: vi.fn(), put: vi.fn() },
}));

const mockedGet = vi.mocked(api.get);
const mockedPut = vi.mocked(api.put);

const FMR1_NOTE =
  'TRGT counts the CGG copies only, not the AGG interruptions: an allele reads 1 to 3 below its clinical repeat number (about 31 triplets read 29).';

const locus = (overrides: Partial<RepeatLocusCountUnitsOut>): RepeatLocusCountUnitsOut => ({
  locus_id: 'ATXN1',
  gene: 'ATXN1',
  display_name: 'ATXN1',
  disease: 'Spinocerebellar ataxia type 1',
  motif: 'CAG',
  warning_min: 36,
  pathogenic_min: 39,
  benign_min: null,
  benign_max: null,
  premutation_min: null,
  source: 'built-in',
  validated: false,
  reason: null,
  updated_by: null,
  updated_at: null,
  call_count: 0,
  known_difference: null,
  ...overrides,
});

const CATALOGUE: RepeatLocusCountUnitsCatalogueOut = {
  margin: 3,
  loci: [
    locus({}),
    locus({
      locus_id: 'FXS_FMR1',
      gene: 'FMR1',
      display_name: 'FMR1',
      disease: 'Fragile X syndrome',
      motif: 'CGG',
      source: 'STRchive',
      call_count: 4,
      known_difference: FMR1_NOTE,
    }),
    locus({
      locus_id: 'HD_HTT',
      gene: 'HTT',
      display_name: 'HTT',
      disease: 'Huntington disease',
      source: 'STRchive',
      validated: true,
      reason: 'Validation report 12, TRGT 5 catalogue',
      updated_by: 'qa-admin',
      updated_at: '2026-10-15T09:30:00Z',
      call_count: 2,
    }),
  ],
  changes: [
    {
      changed_at: '2026-10-15T09:30:00Z',
      changed_by: 'qa-admin',
      locus_id: 'HD_HTT',
      gene: 'HTT',
      validated: true,
      summary: "TRGT's counting units at HD_HTT (HTT): not validated → validated",
      reason: 'Validation report 12, TRGT 5 catalogue',
    },
  ],
};

const renderPage = () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <AdminRepeatLociPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

const locusRows = () =>
  within(screen.getAllByRole('table')[0])
    .getAllByRole('row')
    .slice(1);

describe('AdminRepeatLociPage', () => {
  beforeEach(() => {
    mockedGet.mockReset();
    mockedPut.mockReset();
    mockedGet.mockResolvedValue({ data: CATALOGUE });
    mockedPut.mockResolvedValue({ data: {} });
  });

  it('lists the loci the stored calls name first, with what is known and QA’s record', async () => {
    renderPage();

    expect(await screen.findByRole('heading', { name: 'Repeat catalogue: TRGT counting units' })).toBeInTheDocument();
    expect(mockedGet).toHaveBeenCalledWith('/admin/repeat-loci');
    const [fmr1, htt, atxn1] = locusRows();
    expect(within(fmr1).getByText('FXS_FMR1 · STRchive')).toBeInTheDocument();
    expect(within(fmr1).getByText(FMR1_NOTE)).toBeInTheDocument();
    expect(within(fmr1).getByText('Not validated')).toBeInTheDocument();
    expect(within(htt).getByText('Validated')).toBeInTheDocument();
    expect(within(htt).getByText('Validation report 12, TRGT 5 catalogue')).toBeInTheDocument();
    expect(within(htt).getByText('qa-admin, 2026-10-15 09:30 UTC')).toBeInTheDocument();
    expect(within(atxn1).getByText('ATXN1 · built-in')).toBeInTheDocument();
    expect(screen.getByText(/within 3 repeats of a boundary/)).toBeInTheDocument();
    expect(screen.getByText(/1 of 3 loci validated/)).toBeInTheDocument();
  });

  it('shows only the loci with calls when asked', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('FXS_FMR1 · STRchive');

    await user.click(screen.getByLabelText('Loci with calls only'));

    expect(locusRows()).toHaveLength(2);
    expect(screen.queryByText('ATXN1 · built-in')).not.toBeInTheDocument();
  });

  it('records a locus as validated only with a reason, and says so', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('FXS_FMR1 · STRchive');

    await user.click(within(locusRows()[0]).getByRole('button', { name: 'Record as validated' }));
    const dialog = screen.getByRole('dialog', { name: 'Confirm the counting-unit record' });
    expect(within(dialog).getByText(/Record as validated: FMR1 \(FXS_FMR1\)/)).toBeInTheDocument();
    // What is known of TRGT's count at the gene, while QA decides.
    expect(within(dialog).getByText(`Known: ${FMR1_NOTE}`)).toBeInTheDocument();
    const confirm = within(dialog).getByRole('button', { name: 'Confirm' });
    expect(confirm).toBeDisabled();

    await user.type(within(dialog).getByRole('textbox'), '  Validation report 13  ');
    await user.click(confirm);

    await waitFor(() =>
      expect(mockedPut).toHaveBeenCalledWith('/admin/repeat-loci/FXS_FMR1/count-units', {
        validated: true,
        reason: 'Validation report 13',
      }),
    );
    expect(await screen.findByText("Recorded TRGT's counting units at FXS_FMR1 as validated.")).toBeInTheDocument();
  });

  it('withdraws a validation with a reason', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('HD_HTT · STRchive');

    await user.click(within(locusRows()[1]).getByRole('button', { name: 'Withdraw validation' }));
    const dialog = screen.getByRole('dialog', { name: 'Confirm the counting-unit record' });
    expect(within(dialog).getByText(/will be marked again/)).toBeInTheDocument();
    await user.type(within(dialog).getByRole('textbox'), 'New TRGT catalogue');
    await user.click(within(dialog).getByRole('button', { name: 'Confirm' }));

    await waitFor(() =>
      expect(mockedPut).toHaveBeenCalledWith('/admin/repeat-loci/HD_HTT/count-units', {
        validated: false,
        reason: 'New TRGT catalogue',
      }),
    );
    expect(await screen.findByText("Recorded TRGT's counting units at HD_HTT as not validated.")).toBeInTheDocument();
  });

  it('encodes a locus ID in the path', async () => {
    const user = userEvent.setup();
    mockedGet.mockResolvedValue({
      data: { ...CATALOGUE, loci: [locus({ locus_id: 'A/B C', display_name: 'A/B C', call_count: 1 })] },
    });
    renderPage();
    await screen.findByText('A/B C · built-in');

    await user.click(screen.getByRole('button', { name: 'Record as validated' }));
    await user.type(screen.getByRole('textbox'), 'checked');
    await user.click(screen.getByRole('button', { name: 'Confirm' }));

    await waitFor(() =>
      expect(mockedPut).toHaveBeenCalledWith('/admin/repeat-loci/A%2FB%20C/count-units', {
        validated: true,
        reason: 'checked',
      }),
    );
  });

  it('lists the record’s history', async () => {
    renderPage();
    await screen.findByText('FXS_FMR1 · STRchive');

    const history = screen.getAllByRole('table')[1];
    expect(
      within(history).getByText("TRGT's counting units at HD_HTT (HTT): not validated → validated"),
    ).toBeInTheDocument();
    expect(within(history).getByText('2026-10-15 09:30 UTC')).toBeInTheDocument();
  });

  it('says when the catalogue cannot be loaded', async () => {
    mockedGet.mockRejectedValue(new Error('boom'));
    renderPage();

    expect(await screen.findByText('Could not load the repeat catalogue')).toBeInTheDocument();
  });
});
