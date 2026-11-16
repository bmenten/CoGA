// The repeat catalogue's admin page: each locus's cut-offs, as the pinned STRchive release states
// them (not validated per locus: the owner's decision, 2026-11-16), and QA's count correction
// there, set with a required reason (2026-11-04, CLIN-57), with the corrections' history from the
// clinical audit trail. No locus is validated: every count near a cut-off is marked.
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import AdminRepeatLociPage, { describeCutoffs } from '../AdminRepeatLociPage';
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
  'No constant correction fits FMR1: TRGT leaves out the AGG interruptions, 1 to 3 per allele, so its count reads 1 to 3 below the clinical repeat number depending on the allele. Leave the correction at 0: its counts near a boundary are marked.';

const locus = (overrides: Partial<RepeatLocusCountUnitsOut>): RepeatLocusCountUnitsOut => ({
  locus_id: 'ATXN1',
  gene: 'ATXN1',
  display_name: 'ATXN1',
  disease: 'Spinocerebellar ataxia type 1',
  motif: 'CAG',
  warning_min: 36,
  pathogenic_min: 39,
  benign_min: 6,
  benign_max: 35,
  pathogenic_max: 91,
  premutation_min: null,
  source: 'built-in',
  // A built-in row names its gene's one STRchive locus.
  strchive_id: 'SCA1_ATXN1',
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
  strchive_release: 'v2.28.0',
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
      warning_min: 45,
      pathogenic_min: 201,
      benign_min: 5,
      benign_max: 44,
      pathogenic_max: 2000,
      premutation_min: 55,
      source: 'STRchive',
      strchive_id: 'FXS_FMR1',
      call_count: 4,
      known_difference: FMR1_NOTE,
      offset_note: FMR1_OFFSET_NOTE,
    }),
    locus({
      locus_id: 'HD_HTT',
      gene: 'HTT',
      display_name: 'HTT',
      disease: 'Huntington disease',
      warning_min: 27,
      pathogenic_min: 36,
      benign_min: 6,
      benign_max: 26,
      pathogenic_max: 250,
      source: 'STRchive',
      strchive_id: 'HD_HTT',
      count_offset: -1,
      reason: 'Report 12, TRGT 5 catalogue',
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
      count_offset: -1,
      summary: "Count correction at HD_HTT (HTT): no correction → TRGT's count −1",
      reason: 'Report 12, TRGT 5 catalogue',
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

describe('describeCutoffs', () => {
  it('says each cut-off the release states, in order', () => {
    expect(describeCutoffs(CATALOGUE.loci[1])).toBe('normal 5–44 · grey zone ≥ 45 · premutation ≥ 55 · pathogenic ≥ 201');
    expect(describeCutoffs(CATALOGUE.loci[2])).toBe('normal 6–26 · grey zone ≥ 27 · pathogenic ≥ 36');
  });

  it('reads a locus pathogenic by contraction as its two ranges', () => {
    expect(
      describeCutoffs(locus({ warning_min: null, pathogenic_min: 3, pathogenic_max: 3, benign_min: 4, benign_max: 4 })),
    ).toBe('normal 4 · pathogenic 3');
  });

  it('says where the release states no cut-off', () => {
    expect(
      describeCutoffs(locus({ warning_min: null, pathogenic_min: null, pathogenic_max: null, benign_min: 10, benign_max: 10 })),
    ).toBe('normal 10 · no cut-off');
    expect(
      describeCutoffs(
        locus({ warning_min: null, pathogenic_min: null, pathogenic_max: null, benign_min: null, benign_max: null }),
      ),
    ).toBe('no cut-off');
  });
});

describe('AdminRepeatLociPage', () => {
  beforeEach(() => {
    mockedGet.mockReset();
    mockedPut.mockReset();
    mockedGet.mockResolvedValue({ data: CATALOGUE });
    mockedPut.mockResolvedValue({ data: {} });
  });

  it('lists the loci the stored calls name first, with their cut-offs, what is known and QA’s correction', async () => {
    renderPage();

    expect(await screen.findByRole('heading', { name: 'Repeat catalogue' })).toBeInTheDocument();
    expect(mockedGet).toHaveBeenCalledWith('/admin/repeat-loci');
    const [fmr1, htt, atxn1] = locusRows();
    expect(within(fmr1).getByText('FXS_FMR1 · STRchive')).toBeInTheDocument();
    expect(within(fmr1).getByText('normal 5–44 · grey zone ≥ 45 · premutation ≥ 55 · pathogenic ≥ 201')).toBeInTheDocument();
    expect(within(fmr1).getByText(FMR1_NOTE)).toBeInTheDocument();
    // A locus without a correction says none.
    expect(within(fmr1).getByText('None')).toBeInTheDocument();
    expect(within(htt).getByText('TRGT’s count −1')).toBeInTheDocument();
    expect(within(htt).getByText('Report 12, TRGT 5 catalogue')).toBeInTheDocument();
    expect(within(htt).getByText('qa-admin, 2026-10-15 09:30 UTC')).toBeInTheDocument();
    expect(within(atxn1).getByText('ATXN1 · built-in')).toBeInTheDocument();
    expect(screen.getByText(/within 3 repeats of a cut-off/)).toBeInTheDocument();
  });

  it('names the STRchive release the cut-offs come from, with its page', async () => {
    renderPage();

    const release = await screen.findByRole('link', { name: 'STRchive v2.28.0' });
    expect(release).toHaveAttribute('href', 'https://github.com/dashnowlab/STRchive/releases/tag/v2.28.0');
    expect(release).toHaveAttribute('target', '_blank');
    expect(screen.getByText(/3 loci\. Each count correction is kept in the clinical audit trail/)).toBeInTheDocument();
  });

  it('offers no validation: the count correction is the one thing recorded', async () => {
    renderPage();
    await screen.findByText('FXS_FMR1 · STRchive');

    expect(screen.queryByRole('button', { name: /validat/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/validated/i)).not.toBeInTheDocument();
    for (const row of locusRows()) {
      expect(within(row).getAllByRole('button').map((button) => button.textContent)).toEqual(['Set count correction']);
    }
  });

  it('links each locus’s disease to its page on STRchive', async () => {
    renderPage();

    await screen.findByText('FXS_FMR1 · STRchive');
    const [fmr1, , atxn1] = locusRows();
    const fmr1Link = within(fmr1).getByRole('link', { name: 'Fragile X syndrome' });
    expect(fmr1Link).toHaveAttribute('href', 'https://strchive.org/loci/FXS_FMR1');
    expect(fmr1Link).toHaveAttribute('target', '_blank');
    // A built-in row opens its gene's STRchive locus.
    expect(within(atxn1).getByRole('link', { name: 'Spinocerebellar ataxia type 1' })).toHaveAttribute(
      'href',
      'https://strchive.org/loci/SCA1_ATXN1',
    );
  });

  it('shows a disease without a link where no STRchive locus describes the row', async () => {
    mockedGet.mockResolvedValue({
      data: { ...CATALOGUE, loci: [locus({ locus_id: 'ARX', gene: 'ARX', display_name: 'ARX', disease: 'ARX disorders', strchive_id: null })] },
    });
    renderPage();

    expect(await screen.findByText('ARX disorders')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'ARX disorders' })).not.toBeInTheDocument();
  });

  it('shows only the loci with calls when asked', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('FXS_FMR1 · STRchive');

    await user.click(screen.getByLabelText('Loci with calls only'));

    expect(locusRows()).toHaveLength(2);
    expect(screen.queryByText('ATXN1 · built-in')).not.toBeInTheDocument();
  });

  it('sets a count correction only with a reason, and says so', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('FXS_FMR1 · STRchive');

    await user.click(within(locusRows()[2]).getByRole('button', { name: 'Set count correction' }));
    const dialog = screen.getByRole('dialog', { name: 'Confirm the count correction' });
    expect(within(dialog).getByText(/Count correction: ATXN1 \(ATXN1\)/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Counts within 3 repeats of a cut-off stay marked/)).toBeInTheDocument();
    // No note where a constant correction is not known to be wrong.
    expect(within(dialog).queryByText(/No constant correction fits/)).not.toBeInTheDocument();
    const field = within(dialog).getByRole('spinbutton', { name: /Count correction/ });
    expect(field).toHaveValue(0);
    const confirm = within(dialog).getByRole('button', { name: 'Confirm' });
    await user.clear(field);
    await user.type(field, '-2');
    expect(confirm).toBeDisabled();
    await user.type(within(dialog).getByRole('textbox'), '  Offset from report VAL-7  ');
    await user.click(confirm);

    await waitFor(() =>
      expect(mockedPut).toHaveBeenCalledWith('/admin/repeat-loci/ATXN1/count-units', {
        count_offset: -2,
        reason: 'Offset from report VAL-7',
      }),
    );
    expect(await screen.findByText('Recorded the count correction at ATXN1: TRGT’s count −2.')).toBeInTheDocument();
  });

  it('shows what is known of TRGT’s count and why no constant correction fits FMR1', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('FXS_FMR1 · STRchive');

    await user.click(within(locusRows()[0]).getByRole('button', { name: 'Set count correction' }));
    const dialog = screen.getByRole('dialog', { name: 'Confirm the count correction' });
    expect(within(dialog).getByText(`Known: ${FMR1_NOTE}`)).toBeInTheDocument();
    expect(within(dialog).getByText(FMR1_OFFSET_NOTE)).toBeInTheDocument();
  });

  it('refuses a correction that is not a whole number within the bound', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('FXS_FMR1 · STRchive');

    await user.click(within(locusRows()[1]).getByRole('button', { name: 'Set count correction' }));
    const dialog = screen.getByRole('dialog', { name: 'Confirm the count correction' });
    const field = within(dialog).getByRole('spinbutton', { name: /Count correction/ });
    expect(field).toHaveValue(-1);
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
    await waitFor(() =>
      expect(mockedPut).toHaveBeenCalledWith('/admin/repeat-loci/HD_HTT/count-units', {
        count_offset: 20,
        reason: 'checked',
      }),
    );
  });

  it('confirms an unchanged correction with a new reason', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('HD_HTT · STRchive');

    await user.click(within(locusRows()[1]).getByRole('button', { name: 'Set count correction' }));
    const dialog = screen.getByRole('dialog', { name: 'Confirm the count correction' });
    await user.type(within(dialog).getByRole('textbox'), 'Checked on TRGT 5');
    await user.click(within(dialog).getByRole('button', { name: 'Confirm' }));

    await waitFor(() =>
      expect(mockedPut).toHaveBeenCalledWith('/admin/repeat-loci/HD_HTT/count-units', {
        count_offset: -1,
        reason: 'Checked on TRGT 5',
      }),
    );
    expect(await screen.findByText('Recorded the count correction at HD_HTT: TRGT’s count −1.')).toBeInTheDocument();
  });

  it('encodes a locus ID in the path', async () => {
    const user = userEvent.setup();
    mockedGet.mockResolvedValue({
      data: { ...CATALOGUE, loci: [locus({ locus_id: 'A/B C', display_name: 'A/B C', call_count: 1 })] },
    });
    renderPage();
    await screen.findByText('A/B C · built-in');

    await user.click(screen.getByRole('button', { name: 'Set count correction' }));
    await user.type(screen.getByRole('textbox'), 'checked');
    await user.click(screen.getByRole('button', { name: 'Confirm' }));

    await waitFor(() =>
      expect(mockedPut).toHaveBeenCalledWith('/admin/repeat-loci/A%2FB%20C/count-units', {
        count_offset: 0,
        reason: 'checked',
      }),
    );
  });

  it('lists the corrections’ history', async () => {
    renderPage();
    await screen.findByText('FXS_FMR1 · STRchive');

    const history = screen.getAllByRole('table')[1];
    expect(
      within(history).getByText("Count correction at HD_HTT (HTT): no correction → TRGT's count −1"),
    ).toBeInTheDocument();
    expect(within(history).getByText('2026-10-15 09:30 UTC')).toBeInTheDocument();
  });

  it('says when the catalogue cannot be loaded', async () => {
    mockedGet.mockRejectedValue(new Error('boom'));
    renderPage();

    expect(await screen.findByText('Could not load the repeat catalogue')).toBeInTheDocument();
  });
});
