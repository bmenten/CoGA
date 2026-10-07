// The small-variant filter form (#528), rendered whole in the three ways the pages use
// it: a family's search, the family-agnostic Global Small Variant Explorer, and
// monogenic NIPT. The markup is recorded (__snapshots__), so splitting the form into
// section components must reproduce it exactly: every section, label, control, value
// and summary. Update the snapshot only for an intended change to the form's markup.
import { render, screen } from '@testing-library/react';
import type { ComponentProps } from 'react';
import { MemoryRouter } from 'react-router';
import { expect, test, vi } from 'vitest';

import SmallVariantFilterForm from '../SmallVariantFilterForm';
import { createEmptySmallFilters, type SmallFilterState } from '../smallVariantSearch';
import { GLOBAL_UNSUPPORTED_FILTERS } from '../../variant-explorer/globalSmallVariantSearch';

const MEMBERS = [
  { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'male' },
  { sample_id: 'MOM', role: 'mother', affected: false, sex: 'female' },
  { sample_id: 'DAD', role: 'father', affected: false, sex: 'male' },
];

const TAGS = [
  { key: 'review', label: 'Review', group: 'collaboration' as const, color: '#2563eb', sort_order: 10, scope: 'system' as const, is_custom: false },
  { key: 'acmg_class_4', label: 'Likely Pathogenic - class 4', group: 'classification' as const, color: '#ea580c', sort_order: 120, scope: 'system' as const, is_custom: false },
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

test('renders the monogenic NIPT form as recorded', () => {
  expect(
    markup({
      mode: 'nipt',
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
