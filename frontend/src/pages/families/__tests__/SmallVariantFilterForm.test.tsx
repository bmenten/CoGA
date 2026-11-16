// The small-variant filter form, rendered whole in the three ways the pages use
// it: a family's search, the family-agnostic Global Small Variant Explorer, and
// monogenic NIPT. The markup is recorded (__snapshots__), so splitting the form into
// section components must reproduce it exactly: every section, label, control, value
// and summary. Update the snapshot only for an intended change to the form's markup.
import { fireEvent, render, screen } from '@testing-library/react';
import type { ComponentProps } from 'react';
import { MemoryRouter } from 'react-router';
import { expect, test, vi } from 'vitest';

import SmallVariantFilterForm from '../SmallVariantFilterForm';
import { createEmptySmallFilters, NIPT_UNSUPPORTED_FILTERS, type SmallFilterState } from '../smallVariantSearch';
import { GLOBAL_UNSUPPORTED_FILTERS } from '../../variant-explorer/globalSmallVariantSearch';

const MEMBERS = [
  { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'male' },
  { sample_id: 'MOM', role: 'mother', affected: false, sex: 'female' },
  { sample_id: 'DAD', role: 'father', affected: false, sex: 'male' },
];

const TAGS = [
  { key: 'review', label: 'Review', group: 'collaboration' as const, color: '#2563eb', sort_order: 10, scope: 'system' as const, is_custom: false },
  { key: 'acmg_class_4', label: 'Likely pathogenic - class 4', group: 'classification' as const, color: '#ea580c', sort_order: 120, scope: 'system' as const, is_custom: false },
];

// A draft with a value in most sections, so each renders its filled-in state.
const draft = (): SmallFilterState => ({
  ...createEmptySmallFilters(),
  locus: 'BRCA2',
  inheritance: 'compound_het',
  impact: 'HIGH',
  clinvar: 'Pathogenic',
  max_gnomad_af: '0.01',
  min_cadd: '20',
  panel_id: 'panel-1',
  exclude_gene: 'TTN',
  review_tags: 'review',
});

const props = (overrides: Record<string, unknown> = {}) => ({
  activeFilterChips: [],
  applyPreset: vi.fn(),
  applySavedPreset: vi.fn(),
  draftFilters: draft(),
  handleApply: vi.fn(),
  handleGtToggle: vi.fn(),
  handleReset: vi.fn(),
  handleSampleFieldChange: vi.fn(),
  members: MEMBERS,
  relationships: [],
  panels: [{ _id: 'panel-1', name: 'Mendeliome', source: 'panelapp' }],
  presets: [],
  removeActiveFilterChip: vi.fn(),
  sampleDraftFilters: {
    PROBAND: { gt: ['het'], qual: '20', dp: '10', af: '', ad_alt: '' },
    MOM: { gt: [], qual: '', dp: '', af: '', ad_alt: '' },
  },
  setDraftFilterValue: vi.fn(),
  tags: TAGS,
  toggleDraftFilterListValue: vi.fn(),
  onSaveCurrentPreset: vi.fn(async () => undefined),
  ...overrides,
});

const markup = (overrides: Record<string, unknown> = {}) => {
  const { container } = render(
    <MemoryRouter>
      <SmallVariantFilterForm {...(props(overrides) as unknown as ComponentProps<typeof SmallVariantFilterForm>)} />
    </MemoryRouter>,
  );
  return container.innerHTML;
};

test('renders a family search form as recorded', () => {
  expect(markup()).toMatchSnapshot();
});

test('renders the Global Small Variant Explorer form as recorded', () => {
  expect(markup({ familyAware: false, unsupportedFilters: GLOBAL_UNSUPPORTED_FILTERS, members: [] })).toMatchSnapshot();
});

// With the review tags the NIPT list takes, and no classification or saved-notes
// filter, which it does not.
test('renders the monogenic NIPT form as recorded', () => {
  expect(
    markup({
      mode: 'nipt',
      unsupportedFilters: NIPT_UNSUPPORTED_FILTERS,
      categoryCounts: { '1': 2, '3': 5, '7': 1 },
      categoryLabels: { 1: 'De novo', 3: 'Maternal dominant', 7: 'Paternal dominant' },
      niptInheritancePresets: [
        { value: 'de_novo', label: 'De novo', categories: '1' },
        { value: 'paternal_dominant', label: 'Paternal dominant', categories: '7' },
      ],
    }),
  ).toMatchSnapshot();
});

// A deleted custom tag a preset or a link still filters on is offered nowhere; it is listed,
// marked, where it is selected, so it can be unticked.
const customTag = (key: string, label: string, isActive: boolean) => ({
  key,
  label,
  group: 'custom' as const,
  color: '#336699',
  sort_order: 500,
  scope: 'global' as const,
  is_custom: true,
  is_active: isActive,
});

const WITH_DELETED_TAG = [
  ...TAGS,
  customTag('needs_segregation', 'Needs segregation', true),
  customTag('probe_x', 'Probe X', false),
];

const tagBoxes = (label: string) =>
  screen
    .queryAllByText(label)
    .map((element) => element.closest('label')?.querySelector('input') as HTMLInputElement);

const renderForm = (filters: Partial<SmallFilterState>) =>
  render(
    <MemoryRouter>
      <SmallVariantFilterForm
        {...(props({ tags: WITH_DELETED_TAG, draftFilters: { ...draft(), ...filters } }) as unknown as ComponentProps<
          typeof SmallVariantFilterForm
        >)}
      />
    </MemoryRouter>,
  );

test('lists a deleted tag a review-tag filter holds, marked and ticked, in that list only', () => {
  renderForm({ review_tags: 'probe_x,review' });
  const boxes = tagBoxes('Probe X (deleted)');
  expect(boxes).toHaveLength(1);
  expect(boxes[0].checked).toBe(true);
  // The active custom tag is offered in both lists, the include and the exclude one.
  expect(tagBoxes('Needs segregation')).toHaveLength(2);
});

test('lists a deleted tag an exclude filter holds, marked and ticked', () => {
  renderForm({ review_tags: '', exclude_review_tags: 'probe_x' });
  const boxes = tagBoxes('Probe X (deleted)');
  expect(boxes).toHaveLength(1);
  expect(boxes[0].checked).toBe(true);
});

test('offers a deleted tag in no list that does not hold it', () => {
  renderForm({ review_tags: 'review' });
  expect(screen.queryAllByText(/Probe X/)).toHaveLength(0);
});

// The active-filter chips mark a deleted tag, as every other chip does, never as if it could
// still be chosen.
test('marks a deleted tag on the active review-tag and exclude chips', () => {
  render(
    <MemoryRouter>
      <SmallVariantFilterForm
        {...(props({
          tags: WITH_DELETED_TAG,
          activeFilterChips: [
            { id: 'review_tags:probe_x', label: 'Review tags: probe_x', kind: 'top', key: 'review_tags', value: 'probe_x' },
            { id: 'exclude_review_tags:probe_x', label: 'probe_x', kind: 'top', key: 'exclude_review_tags', value: 'probe_x' },
            {
              id: 'review_tags:needs_segregation',
              label: 'needs_segregation',
              kind: 'top',
              key: 'review_tags',
              value: 'needs_segregation',
            },
          ],
        }) as unknown as ComponentProps<typeof SmallVariantFilterForm>)}
      />
    </MemoryRouter>,
  );
  expect(screen.getByRole('button', { name: 'Review tags: Probe X (deleted)' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Exclude review tags: Probe X (deleted)' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Review tags: Needs segregation' })).toBeInTheDocument();
});

// The categories, one row each in a single column: a row with a filter set is marked, so the
// closed rows show where the filters are; "Expand all" and "Collapse all" open or close them all.
test('marks the categories with a filter set, and opens or closes every one at once', () => {
  const { container } = render(
    <MemoryRouter>
      <SmallVariantFilterForm
        {...(props({
          draftFilters: { ...createEmptySmallFilters(), clinvar: 'Pathogenic' },
          sampleDraftFilters: {},
        }) as unknown as ComponentProps<typeof SmallVariantFilterForm>)}
      />
    </MemoryRouter>,
  );
  const row = (title: string) =>
    screen.getByText(title, { selector: '.variant-filter-dropdown-title' }).closest('details') as HTMLElement;
  expect(row('Pathogenicity')).toHaveClass('variant-filter-dropdown--active');
  expect(row('Frequency')).not.toHaveClass('variant-filter-dropdown--active');
  expect(row('Inheritance')).not.toHaveClass('variant-filter-dropdown--active');

  const rows = Array.from(container.querySelectorAll('details.variant-filter-dropdown')) as HTMLDetailsElement[];
  expect(rows.length).toBeGreaterThan(1);
  expect(rows.every((entry) => !entry.open)).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Expand all' }));
  expect(rows.every((entry) => entry.open)).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Collapse all' }));
  expect(rows.every((entry) => !entry.open)).toBe(true);
});

// The NIPT categories row also holds the inheritance view, the minimum confidence and the
// recurrence limit: any of them marks it, not only a ticked category.
test('marks the NIPT categories row by every filter it holds', () => {
  renderWith({
    mode: 'nipt',
    familyAware: false,
    unsupportedFilters: NIPT_UNSUPPORTED_FILTERS,
    draftFilters: { ...createEmptySmallFilters(), inheritance: 'de_novo' },
    sampleDraftFilters: {},
    categoryLabels: { 1: 'De novo' },
    niptInheritancePresets: [{ value: 'de_novo', label: 'De novo', categories: '' }],
  });
  const row = screen
    .getByText('Maternal/fetal categories', { selector: '.variant-filter-dropdown-title' })
    .closest('details') as HTMLElement;
  expect(row).toHaveClass('variant-filter-dropdown--active');
  expect(row.querySelector('.variant-filter-dropdown-meta')).toHaveTextContent('1 active');
});

test('offers "Expand all" only once the advanced filters are shown', () => {
  renderWith({ collapsibleAdvanced: true });
  const toggle = screen.getByRole('button', { name: /Advanced filters/ });
  expect(toggle).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByRole('button', { name: 'Expand all' })).not.toBeInTheDocument();
  fireEvent.click(toggle);
  expect(screen.getByRole('button', { name: 'Expand all' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Collapse all' })).toBeInTheDocument();
});

// The Annotations, Frequency and Call quality sections, laid out as seqr's.
const renderWith = (overrides: Record<string, unknown>) => {
  const formProps = props(overrides);
  render(
    <MemoryRouter>
      <SmallVariantFilterForm {...(formProps as unknown as ComponentProps<typeof SmallVariantFilterForm>)} />
    </MemoryRouter>,
  );
  return formProps;
};

const listValue = (value: unknown) =>
  new Set(
    String(value)
      .split(',')
      .map((entry) => entry.trim())
      .filter(Boolean),
  );

test('a group box ticks all its consequence terms, half-ticked while some are', () => {
  const { setDraftFilterValue } = renderWith({
    draftFilters: { ...draft(), impact: '', effect: 'missense_variant, stop_lost' },
  });
  const group = screen.getByRole('checkbox', { name: 'Missense (whole group)' }) as HTMLInputElement;
  expect(group.checked).toBe(false);
  expect(group.indeterminate).toBe(true);
  expect((screen.getByRole('checkbox', { name: 'Missense' }) as HTMLInputElement).checked).toBe(true);
  expect((screen.getByRole('checkbox', { name: 'Start lost' }) as HTMLInputElement).checked).toBe(false);

  fireEvent.click(group);
  const [key, value] = setDraftFilterValue.mock.calls.at(-1) ?? [];
  expect(key).toBe('effect');
  expect(listValue(value)).toEqual(
    new Set(['stop_lost', 'start_lost', 'protein_altering_variant', 'missense_variant']),
  );
});

test('clearing a full group removes its terms and keeps the others', () => {
  const { setDraftFilterValue } = renderWith({
    draftFilters: {
      ...draft(),
      impact: '',
      effect: 'inframe_insertion, inframe_deletion, stop_gained',
    },
  });
  const group = screen.getByRole('checkbox', { name: 'In frame (whole group)' }) as HTMLInputElement;
  expect(group.checked).toBe(true);
  fireEvent.click(group);
  expect(setDraftFilterValue).toHaveBeenLastCalledWith('effect', 'stop_gained');
});

// The search reads a term whatever its case, so the boxes do too; a term no box
// offers stays listed, ticked, so it can be unticked.
test('reads terms case aside and lists a term no box offers', () => {
  const { setDraftFilterValue } = renderWith({
    draftFilters: { ...draft(), impact: 'HIGH', effect: 'nmd_transcript_variant, feature_elongation' },
  });
  expect((screen.getByRole('checkbox', { name: 'NMD transcript variant' }) as HTMLInputElement).checked).toBe(true);
  expect((screen.getByRole('checkbox', { name: 'HIGH' }) as HTMLInputElement).checked).toBe(true);
  const held = screen.getByRole('checkbox', { name: 'feature_elongation' }) as HTMLInputElement;
  expect(held.checked).toBe(true);
  fireEvent.click(held);
  expect(setDraftFilterValue).toHaveBeenLastCalledWith('effect', 'nmd_transcript_variant');
});

const AF_KEYS = [
  'max_gnomad_genomes_af',
  'max_gnomad_exomes_af',
  'max_gnomad_popmax_af',
  'max_gnomad_af',
  'max_topmed_af',
];

test('the frequency header sets every population and shows what the set ones share', () => {
  const { setDraftFilterValue } = renderWith({});
  const header = screen.getByLabelText('Maximum AF, all populations') as HTMLSelectElement;
  // The draft caps gnomAD combined alone: the header shows its value.
  expect(header.value).toBe('0.01');
  fireEvent.change(header, { target: { value: '0.001' } });
  AF_KEYS.forEach((key) => expect(setDraftFilterValue).toHaveBeenCalledWith(key, '0.001'));

  fireEvent.change(screen.getByLabelText('Maximum gnomAD homozygotes and hemizygotes'), {
    target: { value: '10' },
  });
  expect(setDraftFilterValue).toHaveBeenCalledWith('max_gnomad_hom_count', '10');
  expect(setDraftFilterValue).toHaveBeenCalledWith('max_gnomad_hemi_count', '10');
});

test('the frequency header reads mixed when the populations differ', () => {
  renderWith({ draftFilters: { ...draft(), max_gnomad_af: '0.01', max_topmed_af: '0.05' } });
  expect((screen.getByLabelText('Maximum AF, all populations') as HTMLSelectElement).value).toBe('mixed');
});

test('a frequency slider moves over seqr steps, its last step setting no maximum', () => {
  const { setDraftFilterValue } = renderWith({
    draftFilters: { ...draft(), max_gnomad_exomes_af: '0.003' },
  });
  // A value between two steps is shown as it is.
  expect(screen.getByRole('slider', { name: 'gnomAD exomes maximum AF' })).toHaveAttribute('aria-valuetext', '0.003');
  const topmed = screen.getByRole('slider', { name: 'TOPMed maximum AF' });
  expect(topmed).toHaveAttribute('aria-valuetext', '1');
  fireEvent.change(topmed, { target: { value: '5' } });
  expect(setDraftFilterValue).toHaveBeenLastCalledWith('max_topmed_af', '0.01');
  fireEvent.change(screen.getByRole('slider', { name: 'gnomAD combined maximum AF' }), {
    target: { value: '11' },
  });
  expect(setDraftFilterValue).toHaveBeenLastCalledWith('max_gnomad_af', '');
});

const sampleCalls = (mock: { mock: { calls: unknown[][] } }) =>
  mock.mock.calls.map(([sample, field, value]) => `${String(sample)}:${String(field)}=${String(value)}`);

// seqr's "Affected only": on while the unaffected members have no minimum. A minimum such
// as an allele balance would otherwise drop a de novo variant the parents' reference calls
// cannot meet.
test('sets the call minimums of the affected members only while Affected only is on', () => {
  const formProps = renderWith({});
  const { handleSampleFieldChange } = formProps;
  const affectedOnly = screen.getByRole('switch', { name: 'Affected only' }) as HTMLInputElement;
  expect(affectedOnly.checked).toBe(true);
  // PROBAND holds GQ 20 and DP 10, no allele balance or alt reads: no quick choice.
  expect((screen.getByLabelText('Quick call quality') as HTMLSelectElement).value).toBe('custom');

  const { setDraftFilterValue } = formProps;
  fireEvent.change(screen.getByLabelText('Quick call quality'), { target: { value: 'high_quality' } });
  // As seqr's, High quality also keeps the variants whose calls passed their FILTER only.
  expect(setDraftFilterValue).toHaveBeenCalledWith('pass_only', 'true');
  expect(sampleCalls(handleSampleFieldChange)).toEqual([
    'PROBAND:qual=20',
    'PROBAND:af=0.2',
    'PROBAND:dp=10',
    'PROBAND:ad_alt=4',
  ]);

  handleSampleFieldChange.mockClear();
  fireEvent.change(screen.getByRole('slider', { name: 'Genotype quality minimum' }), {
    target: { value: '6' },
  });
  expect(sampleCalls(handleSampleFieldChange)).toEqual(['PROBAND:qual=30']);
});

test('Affected only off gives every member the minimums the affected members share', () => {
  const { handleSampleFieldChange } = renderWith({});
  fireEvent.click(screen.getByRole('switch', { name: 'Affected only' }));
  expect(sampleCalls(handleSampleFieldChange)).toEqual([
    'MOM:qual=20',
    'DAD:qual=20',
    'MOM:af=',
    'DAD:af=',
    'MOM:dp=10',
    'DAD:dp=10',
    'MOM:ad_alt=',
    'DAD:ad_alt=',
  ]);
});

test('reads Affected only off, and a minimum as mixed, when an unaffected member has one', () => {
  renderWith({
    sampleDraftFilters: {
      PROBAND: { gt: ['het'], qual: '20', dp: '8', af: '0.75', ad_alt: '5' },
      MOM: { gt: ['het'], qual: '15', dp: '8', af: '', ad_alt: '' },
      DAD: { gt: ['het'], qual: '15', dp: '8', af: '', ad_alt: '' },
    },
  });
  expect((screen.getByRole('switch', { name: 'Affected only' }) as HTMLInputElement).checked).toBe(false);
  expect(screen.getByRole('slider', { name: 'Genotype quality minimum' })).toHaveAttribute('aria-valuetext', 'mixed');
  expect(screen.getByRole('slider', { name: 'Depth minimum' })).toHaveAttribute('aria-valuetext', '8');
  expect((screen.getByLabelText('MOM GQ ≥') as HTMLInputElement).value).toBe('15');
});

const HIGH_QUALITY_PROBAND = {
  PROBAND: { gt: ['het'], qual: '20', dp: '10', af: '0.2', ad_alt: '4' },
  MOM: { gt: [], qual: '', dp: '', af: '', ad_alt: '' },
  DAD: { gt: [], qual: '', dp: '', af: '', ad_alt: '' },
};

test('the Filter value sets Pass variants only, which the quick choices read', () => {
  const { setDraftFilterValue } = renderWith({ sampleDraftFilters: HIGH_QUALITY_PROBAND });
  const quick = screen.getByLabelText('Quick call quality') as HTMLSelectElement;
  // The High quality minimums without Pass variants only are no quick choice.
  expect(quick.value).toBe('custom');
  expect((screen.getByRole('radio', { name: 'Show all variants' }) as HTMLInputElement).checked).toBe(true);
  fireEvent.click(screen.getByRole('radio', { name: 'Pass variants only' }));
  expect(setDraftFilterValue).toHaveBeenLastCalledWith('pass_only', 'true');
  fireEvent.change(quick, { target: { value: 'all_variants' } });
  expect(setDraftFilterValue).toHaveBeenLastCalledWith('pass_only', '');
});

test('reads High quality with Pass variants only on', () => {
  renderWith({ sampleDraftFilters: HIGH_QUALITY_PROBAND, draftFilters: { ...draft(), pass_only: 'true' } });
  expect((screen.getByLabelText('Quick call quality') as HTMLSelectElement).value).toBe('high_quality');
  expect((screen.getByRole('radio', { name: 'Pass variants only' }) as HTMLInputElement).checked).toBe(true);
});
