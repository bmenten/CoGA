import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import FamilyRepeatExpansionsPage from '../FamilyRepeatExpansionsPage';
import FamilyParaphasePage from '../FamilyParaphasePage';
import { differsFromStatus, findingQuickTagPayload } from '../findingReview';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

// The reviews of repeat loci and Paraphase regions on their cards
// (docs/repeat-paraphase-review-design.md): quick tags, the dialog with the type's own scale and
// no ACMG class tags (B14), the classification beside CoGA's status (H18), the drift chip (B5)
// and an excluded card folded to its heading (B3).

const apiMock = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn() }));
vi.mock('../../../lib/api', () => ({ default: apiMock }));
vi.mock('../../../lib/reference', () => ({
  // The family header lists the family's projects by name.
  useProjectCatalog: () => ({ data: [] }),
  useFamilyReference: () => ({ assemblyName: 'GRCh38', assemblyVersion: 'p14', projectId: undefined, isLoading: false }),
  formatResolvedReferenceLabel: () => 'GRCh38',
}));

const tag = (key: string, label: string, group: 'collaboration' | 'classification' | 'custom', isCustom = false) => ({
  key,
  label,
  description: null,
  group,
  color: '#888888',
  sort_order: 10,
  scope: isCustom ? 'project' : 'system',
  project_id: null,
  shared_project_ids: [],
  is_custom: isCustom,
  is_active: true,
});
const TAGS = [
  tag('review', 'Review', 'collaboration'),
  tag('excluded', 'Excluded', 'collaboration'),
  tag('report', 'Report', 'collaboration'),
  tag('acmg_class_5', 'Pathogenic', 'classification'),
  tag('acmg_vus_hot', 'VUS hot', 'classification'),
  tag('secondary_finding', 'Secondary finding', 'classification'),
  tag('mosaic', 'Mosaic', 'custom', true),
];

const members = [
  { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'male' },
  { sample_id: 'MOTHER', role: 'mother', affected: false, sex: 'female' },
];
const repeatCall = (sample: string, status: string, counts: number[]) => ({
  sample,
  role: sample === 'PROBAND' ? 'proband' : 'mother',
  affected: sample === 'PROBAND',
  sex: sample === 'PROBAND' ? 'male' : 'female',
  genotype: '1/2',
  allele_count: counts.length,
  status,
  alleles: counts.map((count) => ({ repeat_count: count, status })),
});
const review = (overrides: Record<string, unknown> = {}) => ({
  key: 'HD_HTT',
  classification: null,
  tags: [],
  tag_metadata: {},
  note: null,
  updated_by: 'analyst',
  updated_at: '2026-10-28T10:00:00Z',
  evidence_captured_at: null,
  drift: null,
  ...overrides,
});
const htt = (extra: Record<string, unknown> = {}) => ({
  locus_id: 'HD_HTT',
  gene: 'HTT',
  display_name: 'HTT',
  disease: 'Huntington disease',
  chr: '4',
  start: 3074876,
  end: 3074933,
  motif: 'CAG',
  warning_min: 27,
  pathogenic_min: 36,
  status: 'pathogenic',
  calls: { PROBAND: repeatCall('PROBAND', 'pathogenic', [17, 42]), MOTHER: repeatCall('MOTHER', 'normal', [17, 20]) },
  review: null,
  ...extra,
});

const renderRepeats = (loci: unknown[], { readOnly = false } = {}) => {
  apiMock.get.mockImplementation((url: string) => {
    if (url === '/families/F1') {
      return Promise.resolve({ data: { family_id: 'F1', members, projects: [], read_only: readOnly } });
    }
    if (url === '/families/F1/repeat-expansions') return Promise.resolve({ data: { samples: members, loci } });
    if (url === '/families/F1/small-variant-tags') return Promise.resolve({ data: TAGS });
    return Promise.resolve({ data: [] });
  });
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <MemoryRouter initialEntries={['/families/F1/repeat-expansions']}>
        <Routes>
          <Route path="/families/:familyId/repeat-expansions" element={<FamilyRepeatExpansionsPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

describe('reviews on the repeat locus cards', () => {
  beforeEach(() => {
    apiMock.get.mockReset();
    apiMock.put.mockReset();
  });

  it('saves a quick tag with the review version and shows it on the card at once', async () => {
    apiMock.put.mockResolvedValue({ data: review({ tags: ['report'], updated_at: '2026-10-29T09:00:00Z' }) });
    renderRepeats([htt()]);
    const card = await screen.findByRole('article', { name: 'HTT repeat' });

    fireEvent.click(within(card).getByRole('button', { name: 'Report' }));

    await waitFor(() => expect(apiMock.put).toHaveBeenCalledTimes(1));
    expect(apiMock.put).toHaveBeenCalledWith('/families/F1/repeat-expansions/HD_HTT/review', {
      classification: null,
      tags: ['report'],
      note: null,
      expected_updated_at: null,
    });
    await waitFor(() =>
      expect(within(card).getByRole('button', { name: 'Report' })).toHaveAttribute('aria-pressed', 'true'),
    );
    // The table is not read again: the save wrote into it.
    expect(apiMock.get.mock.calls.filter(([url]) => url === '/families/F1/repeat-expansions')).toHaveLength(1);
  });

  it('puts the card back and says so when the save fails', async () => {
    apiMock.put.mockRejectedValue(new Error('Network down'));
    renderRepeats([htt()]);
    const card = await screen.findByRole('article', { name: 'HTT repeat' });

    fireEvent.click(within(card).getByRole('button', { name: 'Report' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Network down');
    expect(within(card).getByRole('button', { name: 'Report' })).toHaveAttribute('aria-pressed', 'false');
  });

  it('shows the classification, a mark when it differs from the status, the drift and the note', async () => {
    renderRepeats([
      htt({
        review: review({
          classification: 'normal',
          tags: ['mosaic'],
          note: 'Reads support 42 repeats.',
          drift: { status: 'changed', changes: ['PROBAND: 42 → 44'] },
        }),
      }),
    ]);
    const card = await screen.findByRole('article', { name: 'HTT repeat' });

    expect(within(card).getByText('Review: Normal')).toBeInTheDocument();
    // Normal agrees with the mother's call, so it is not marked as differing.
    expect(within(card).queryByText("Differs from CoGA's status")).not.toBeInTheDocument();
    expect(within(card).getByText('Changed since review')).toBeInTheDocument();
    expect(within(card).getByText('Mosaic')).toBeInTheDocument();
    expect(within(card).getByText('Reads support 42 repeats.')).toBeInTheDocument();
  });

  it('marks a classification that agrees with none of the calls', async () => {
    renderRepeats([
      htt({
        calls: { PROBAND: repeatCall('PROBAND', 'pathogenic', [17, 42]) },
        review: review({ classification: 'normal' }),
      }),
    ]);
    const card = await screen.findByRole('article', { name: 'HTT repeat' });
    expect(within(card).getByText("Differs from CoGA's status")).toBeInTheDocument();
  });

  it('folds an excluded card to its heading and unfolds it on request', async () => {
    renderRepeats([htt({ review: review({ tags: ['excluded'] }) })]);
    const card = await screen.findByRole('article', { name: 'HTT repeat' });

    expect(within(card).getByText('Excluded')).toBeInTheDocument();
    expect(within(card).queryByText('Cutoffs')).not.toBeInTheDocument();
    fireEvent.click(within(card).getByRole('button', { name: 'Show the excluded card of HTT' }));
    expect(within(card).getByText('Cutoffs')).toBeInTheDocument();
  });

  it('offers the repeat scale and no ACMG class tags, and lists a held one marked', async () => {
    apiMock.put.mockResolvedValue({ data: review({ classification: 'pathogenic_expansion' }) });
    renderRepeats([htt({ review: review({ tags: ['acmg_class_5'] }) })]);
    const card = await screen.findByRole('article', { name: 'HTT repeat' });

    fireEvent.click(within(card).getByRole('button', { name: 'Tags & notes' }));
    const dialog = await screen.findByRole('dialog');
    for (const label of ['Pathogenic expansion', 'Reduced penetrance', 'Premutation', 'Intermediate (grey zone)']) {
      expect(within(dialog).getByLabelText(label)).toBeInTheDocument();
    }
    expect(within(dialog).queryByLabelText('VUS hot')).not.toBeInTheDocument();
    expect(within(dialog).getByLabelText('Secondary finding')).toBeInTheDocument();
    expect(within(dialog).getByText('(an ACMG class tag, not offered here)')).toBeInTheDocument();

    fireEvent.click(within(dialog).getByLabelText('Pathogenic expansion'));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save review' }));

    await waitFor(() => expect(apiMock.put).toHaveBeenCalledTimes(1));
    expect(apiMock.put).toHaveBeenCalledWith('/families/F1/repeat-expansions/HD_HTT/review', {
      classification: 'pathogenic_expansion',
      tags: ['acmg_class_5'],
      note: null,
      expected_updated_at: '2026-10-28T10:00:00Z',
    });
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('lets a reader see the review but not change it', async () => {
    renderRepeats([htt()], { readOnly: true });
    const card = await screen.findByRole('article', { name: 'HTT repeat' });
    expect(within(card).getByRole('button', { name: 'Report' })).toBeDisabled();
    expect(within(card).getByRole('button', { name: 'Exclude' })).toBeDisabled();
  });
});

describe('reviews on the Paraphase region cards', () => {
  beforeEach(() => {
    apiMock.get.mockReset();
    apiMock.put.mockReset();
  });

  it('saves to the region and offers the Paraphase scale', async () => {
    const call = (sample: string, status: string) => ({
      sample,
      clinical_status: status,
      copy_number_metrics: [{ key: 'smn1_cn', label: 'SMN1 CN', value: status === 'carrier' ? 1 : 2 }],
      read_metrics: [],
    });
    const gene = {
      gene_symbol: 'smn1',
      is_medically_relevant: true,
      has_copy_number_signal: true,
      region_info: {
        region_id: 'smn1_smn2',
        display_name: 'SMN1/SMN2',
        genes: ['SMN1', 'SMN2'],
        summary: null,
        clinical: null,
        clinical_priority: 1,
        key_copy_number_fields: ['smn1_cn'],
        key_read_fields: [],
        key_haplotype_fields: [],
        key_extra_fields: [],
        field_descriptions: {},
        notes: [],
        disorders: [],
      },
      samples: { PROBAND: call('PROBAND', 'carrier'), MOTHER: call('MOTHER', 'normal') },
      review: null,
    };
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') return Promise.resolve({ data: { family_id: 'F1', members, projects: [] } });
      if (url === '/families/F1/paraphase') return Promise.resolve({ data: { samples: members, genes: [gene] } });
      if (url === '/families/F1/small-variant-tags') return Promise.resolve({ data: TAGS });
      return Promise.resolve({ data: [] });
    });
    apiMock.put.mockResolvedValue({ data: review({ key: 'smn1', tags: ['review'] }) });
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <MemoryRouter initialEntries={['/families/F1/paraphase']}>
          <Routes>
            <Route path="/families/:familyId/paraphase" element={<FamilyParaphasePage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const card = (await screen.findByText('SMN1/SMN2')).closest('article') as HTMLElement;

    fireEvent.click(within(card).getByRole('button', { name: 'Review' }));
    await waitFor(() => expect(apiMock.put).toHaveBeenCalledTimes(1));
    expect(apiMock.put.mock.calls[0][0]).toBe('/families/F1/paraphase/smn1/review');

    fireEvent.click(within(card).getByRole('button', { name: 'Tags & notes' }));
    const dialog = await screen.findByRole('dialog');
    for (const label of ['Affected', 'Carrier', 'Risk allele', 'Normal', 'Uncertain significance']) {
      expect(within(dialog).getByLabelText(label)).toBeInTheDocument();
    }
    expect(within(dialog).queryByLabelText('Pathogenic expansion')).not.toBeInTheDocument();
  });
});

describe('finding review rules', () => {
  it('marks a classification only when it agrees with none of the known statuses', () => {
    expect(differsFromStatus('repeat', 'pathogenic_expansion', ['pathogenic', 'normal'])).toBe(false);
    expect(differsFromStatus('repeat', 'normal', ['pathogenic'])).toBe(true);
    expect(differsFromStatus('repeat', 'uncertain', ['pathogenic'])).toBe(false);
    expect(differsFromStatus('repeat', 'reduced_penetrance', ['intermediate'])).toBe(false);
    expect(differsFromStatus('paraphase', 'risk_allele', ['normal'])).toBe(false);
    expect(differsFromStatus('paraphase', 'carrier', ['none', null])).toBe(false);
    expect(differsFromStatus('paraphase', 'affected', ['carrier'])).toBe(true);
    expect(differsFromStatus('paraphase', null, ['carrier'])).toBe(false);
  });

  it('keeps the classification and note when a quick tag toggles', () => {
    const held = review({ classification: 'premutation', tags: ['report'], note: 'n' });
    expect(findingQuickTagPayload(held, 'report')).toEqual({ classification: 'premutation', tags: [], note: 'n' });
    expect(findingQuickTagPayload(null, 'review')).toEqual({ classification: null, tags: ['review'], note: null });
  });
});
