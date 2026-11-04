// The repeat catalogue's admin page: each locus with QA's record of TRGT's count there (its count
// correction, and whether the count, so corrected, is the clinical repeat number), set with a
// required reason, and the record's history from the clinical audit trail. Until a locus is
// validated, the repeat page and the genome tracks mark a count near a boundary there (owner's
// decision, 2026-10-14); a correction applies validated or not (2026-11-04, CLIN-57).
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
const FMR1_OFFSET_NOTE =
  'No constant correction fits FMR1: TRGT leaves out the AGG interruptions, 1 to 3 per allele, so its count reads 1 to 3 below the clinical repeat number depending on the allele. Leave the correction at 0 and the locus not validated: its counts near a boundary stay marked.';

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
  count_offset: 0,
  reason: null,
  updated_by: null,
  updated_at: null,
  call_count: 0,
  known_difference: null,
  offset_note: null,
  ...overrides,
});

const CATALOGUE: RepeatLocusCountUnitsCatalogueOut = {
  margin: 3,
  offset_limit: 20,
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
      offset_note: FMR1_OFFSET_NOTE,
    }),
    locus({
      locus_id: 'HD_HTT',
      gene: 'HTT',
      display_name: 'HTT',
      disease: 'Huntington disease',
      source: 'STRchive',
      validated: true,
      count_offset: -1,
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
      count_offset: -1,
      summary: "TRGT's counting units at HD_HTT (HTT): not validated → validated; correction no correction → TRGT's count −1",
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
    expect(within(htt).getByText('Correction: TRGT’s count −1')).toBeInTheDocument();
    expect(within(htt).getByText('Validation report 12, TRGT 5 catalogue')).toBeInTheDocument();
    // A locus without a correction says none.
    expect(within(fmr1).queryByText(/Correction:/)).not.toBeInTheDocument();
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
    // What is known of TRGT's count at the gene, while QA decides, and why no constant
    // correction fits FMR1, next to the field.
    expect(within(dialog).getByText(`Known: ${FMR1_NOTE}`)).toBeInTheDocument();
    expect(within(dialog).getByText(FMR1_OFFSET_NOTE)).toBeInTheDocument();
    expect(within(dialog).getByRole('spinbutton', { name: /Count correction/ })).toHaveValue(0);
    const confirm = within(dialog).getByRole('button', { name: 'Confirm' });
    expect(confirm).toBeDisabled();

    await user.type(within(dialog).getByRole('textbox'), '  Validation report 13  ');
    await user.click(confirm);

    await waitFor(() =>
      expect(mockedPut).toHaveBeenCalledWith('/admin/repeat-loci/FXS_FMR1/count-units', {
        validated: true,
        count_offset: 0,
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

    // The locus's correction is kept: the whole record is stated.
    await waitFor(() =>
      expect(mockedPut).toHaveBeenCalledWith('/admin/repeat-loci/HD_HTT/count-units', {
        validated: false,
        count_offset: -1,
        reason: 'New TRGT catalogue',
      }),
    );
    expect(
      await screen.findByText(
        "Recorded TRGT's counting units at HD_HTT as not validated, with the count correction TRGT’s count −1.",
      ),
    ).toBeInTheDocument();
  });

  it('sets a count correction with a reason and keeps the validation as it is', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('FXS_FMR1 · STRchive');

    await user.click(within(locusRows()[2]).getByRole('button', { name: 'Set count correction' }));
    const dialog = screen.getByRole('dialog', { name: 'Confirm the counting-unit record' });
    expect(within(dialog).getByText(/Count correction: ATXN1 \(ATXN1\)/)).toBeInTheDocument();
    expect(within(dialog).getByText(/stay marked until the locus is validated/)).toBeInTheDocument();
    // No note where a constant correction is not known to be wrong.
    expect(within(dialog).queryByText(/No constant correction fits/)).not.toBeInTheDocument();
    const field = within(dialog).getByRole('spinbutton', { name: /Count correction/ });
    await user.clear(field);
    await user.type(field, '-2');
    await user.type(within(dialog).getByRole('textbox'), 'Offset from validation report VAL-7');
    await user.click(within(dialog).getByRole('button', { name: 'Confirm' }));

    await waitFor(() =>
      expect(mockedPut).toHaveBeenCalledWith('/admin/repeat-loci/ATXN1/count-units', {
        validated: false,
        count_offset: -2,
        reason: 'Offset from validation report VAL-7',
      }),
    );
    expect(
      await screen.findByText(
        "Recorded TRGT's counting units at ATXN1 as not validated, with the count correction TRGT’s count −2.",
      ),
    ).toBeInTheDocument();
  });

  it('refuses a correction that is not a whole number within the bound', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('FXS_FMR1 · STRchive');

    await user.click(within(locusRows()[1]).getByRole('button', { name: 'Set count correction' }));
    const dialog = screen.getByRole('dialog', { name: 'Confirm the counting-unit record' });
    const field = within(dialog).getByRole('spinbutton', { name: /Count correction/ });
    expect(field).toHaveValue(-1);
    expect(within(dialog).getByText(/A different correction is recorded as not validated/)).toBeInTheDocument();
    await user.type(within(dialog).getByRole('textbox'), 'checked');
    const confirm = within(dialog).getByRole('button', { name: 'Confirm' });
    expect(confirm).toBeEnabled();

    for (const typed of ['21', '-21', '1.5', '']) {
      await user.clear(field);
      if (typed) await user.type(field, typed);
      expect(confirm).toBeDisabled();
    }
    await user.clear(field);
    await user.type(field, '20');
    expect(confirm).toBeEnabled();
    await user.click(confirm);
    // A validation covers the correction it was given with: a changed correction alone is
    // recorded as not validated.
    await waitFor(() =>
      expect(mockedPut).toHaveBeenCalledWith('/admin/repeat-loci/HD_HTT/count-units', {
        validated: false,
        count_offset: 20,
        reason: 'checked',
      }),
    );
  });

  it('keeps a validated locus validated when its correction is confirmed unchanged', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('HD_HTT · STRchive');

    await user.click(within(locusRows()[1]).getByRole('button', { name: 'Set count correction' }));
    const dialog = screen.getByRole('dialog', { name: 'Confirm the counting-unit record' });
    await user.type(within(dialog).getByRole('textbox'), 'Checked on TRGT 5');
    await user.click(within(dialog).getByRole('button', { name: 'Confirm' }));

    await waitFor(() =>
      expect(mockedPut).toHaveBeenCalledWith('/admin/repeat-loci/HD_HTT/count-units', {
        validated: true,
        count_offset: -1,
        reason: 'Checked on TRGT 5',
      }),
    );
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
        count_offset: 0,
        reason: 'checked',
      }),
    );
  });

  it('lists the record’s history', async () => {
    renderPage();
    await screen.findByText('FXS_FMR1 · STRchive');

    const history = screen.getAllByRole('table')[1];
    expect(
      within(history).getByText(
        "TRGT's counting units at HD_HTT (HTT): not validated → validated; correction no correction → TRGT's count −1",
      ),
    ).toBeInTheDocument();
    expect(within(history).getByText('2026-10-15 09:30 UTC')).toBeInTheDocument();
  });

  it('says when the catalogue cannot be loaded', async () => {
    mockedGet.mockRejectedValue(new Error('boom'));
    renderPage();

    expect(await screen.findByText('Could not load the repeat catalogue')).toBeInTheDocument();
  });
});
