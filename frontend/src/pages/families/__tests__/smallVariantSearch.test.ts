import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import {
  ALL_GT_GROUPS,
  HET_GT_GROUP,
  addableTagDefinitions,
  buildSmallVariantQueryParams,
  buildPresetPayload,
  createEmptySmallFilters,
  hasLocationProblems,
  heldTagOptions,
  resolveSampleFiltersFromPreset,
  smallVariantLocationProblems,
  tagDefinitionLabel,
  useSmallVariantSearchState,
  type FamilyMember,
  type SmallPreset,
  type SmallVariantFilterPreset,
  type SmallVariantSampleFilter,
  type SmallVariantTagDefinition,
} from '../smallVariantSearch';

const members: FamilyMember[] = [
  { sample_id: 'S1', role: 'proband', affected: true, sex: 'male' },
  { sample_id: 'S2', role: 'mother', affected: false, sex: 'female' },
];

const defaultSampleFilter = (): SmallVariantSampleFilter => ({
  gt: [...ALL_GT_GROUPS],
  qual: '',
  dp: '',
  af: '',
  ad_alt: '',
});

describe('smallVariantSearch preset helpers', () => {
  it('builds a compact preset payload without default sample filters', () => {
    const sampleFilters = {
      S1: {
        gt: [...HET_GT_GROUP],
        qual: '20',
        dp: '10',
        af: '0.2',
        ad_alt: '4',
      },
      S2: defaultSampleFilter(),
    };

    const payload = buildPresetPayload({
      filters: {
        locus: '',
        chr: '',
        start: '',
        end: '',
        intervals: '',
        inheritance: '',
        prioritize: '',
        require_sv_second_hit: '',
        expanded_carrier_screening: '',
        ps: '',
        type: '',
        source: '',
        gene: '',
        transcript: '',
        impact: 'HIGH',
        effect: '',
        clinvar: '',
        exclude_clinvar: '',
        clinvar_overrides_frequency: '',
        exclude_review_tags: '',
        exclude_gene: '',
        exclude_intervals: '',
        rsid: '',
        hgvsc: '',
        hgvsp: '',
        canonical_only: '',
        mane_only: '',
        lof_only: '',
        max_gnomad_af: '',
        max_gnomad_exomes_af: '',
        max_gnomad_genomes_af: '',
        max_gnomad_popmax_af: '',
        max_topmed_af: '',
        max_gnomad_ac: '',
        max_gnomad_hom_count: '',
        max_gnomad_hemi_count: '',
        min_cadd: '',
        min_revel: '',
        min_spliceai: '',
        sift: '',
        polyphen: '',
        panel_id: '',
        classification: '',
        review_tags: '',
        has_notes: '',
        category: '',
        min_confidence: '',
        include_not_inherited: '',
        de_novo_priority: '',
      },
      members,
      sampleFilters,
    });

    expect(payload.sample_filters).toEqual({
      S1: {
        gt: [...HET_GT_GROUP],
        qual: '20',
        dp: '10',
        af: '0.2',
        ad_alt: '4',
      },
    });
    expect(payload.sample_templates).toEqual({
      'role:proband': {
        gt: [...HET_GT_GROUP],
        qual: '20',
        dp: '10',
        af: '0.2',
        ad_alt: '4',
      },
      'status:affected': {
        gt: [...HET_GT_GROUP],
        qual: '20',
        dp: '10',
        af: '0.2',
        ad_alt: '4',
      },
      proband: {
        gt: [...HET_GT_GROUP],
        qual: '20',
        dp: '10',
        af: '0.2',
        ad_alt: '4',
      },
    });
  });

  it('merges shared, role, status, and exact preset sample filters in order', () => {
    const preset: SmallVariantFilterPreset = {
      _id: 'preset-1',
      owner: 'reviewer',
      name: 'Layered preset',
      description: null,
      filters: {},
      sample_filters: {
        S1: {
          af: '0.25',
        },
      },
      sample_templates: {
        all: {
          qual: '15',
        },
        'status:affected': {
          dp: '8',
        },
        'role:proband': {
          gt: [...HET_GT_GROUP],
        },
      },
      created_at: '2026-04-15T09:00:00Z',
      updated_at: '2026-04-15T09:00:00Z',
    };

    expect(resolveSampleFiltersFromPreset(preset, members)).toEqual({
      S1: {
        gt: [...HET_GT_GROUP],
        qual: '15',
        dp: '8',
        af: '0.25',
        ad_alt: '',
      },
      S2: {
        gt: [...ALL_GT_GROUPS],
        qual: '15',
        dp: '',
        af: '',
        ad_alt: '',
      },
    });
  });

  it('serializes the explicit inheritance mode into the query string', () => {
    const filters = createEmptySmallFilters();
    filters.inheritance = 'compound_het';
    const params = buildSmallVariantQueryParams(
      filters,
      {
        S1: defaultSampleFilter(),
        S2: defaultSampleFilter(),
      },
      1,
    );

    expect(params.get('inheritance')).toBe('compound_het');
  });

  it('serializes excluded review tags into the query string', () => {
    const filters = createEmptySmallFilters();
    filters.exclude_review_tags = 'excluded, needs_rna';
    const params = buildSmallVariantQueryParams(
      filters,
      {
        S1: defaultSampleFilter(),
        S2: defaultSampleFilter(),
      },
      1,
    );

    expect(params.getAll('exclude_review_tag')).toEqual(['excluded', 'needs_rna']);
  });

  it('preserves project scope in the query string', () => {
    const params = buildSmallVariantQueryParams(
      createEmptySmallFilters(),
      {
        S1: defaultSampleFilter(),
        S2: defaultSampleFilter(),
      },
      1,
      'project-123',
    );

    expect(params.get('project_id')).toBe('project-123');
  });
});

describe('useSmallVariantSearchState default (fresh open)', () => {
  const family = {
    members: [
      { sample_id: 'S1', role: 'proband', affected: true, sex: 'female' },
      { sample_id: 'S2', role: 'father', affected: false, sex: 'male' },
    ],
    relationships: [],
    projects: [],
  } as never;

  it('defaults to the phenotype-priority preset scoped to the Mendeliome panel', async () => {
    const { result } = renderHook(() =>
      useSmallVariantSearchState({
        family,
        locationSearch: '',
        navigate: () => {},
        mendeliomePanelId: 'mendel-1',
        panelsLoaded: true,
      }),
    );
    await waitFor(() => expect(result.current.filters.panel_id).toBe('mendel-1'));
    // The Phenotype-priority (Exomiser-style) preset is applied.
    expect(result.current.filters.prioritize).toBe('true');
    expect(result.current.filters.impact).toContain('HIGH');
    expect(result.current.filters.exclude_clinvar).toContain('Benign');
  });

  it('does not override a deep-linked search', async () => {
    const { result } = renderHook(() =>
      useSmallVariantSearchState({
        family,
        locationSearch: '?gene=BRCA1',
        navigate: () => {},
        mendeliomePanelId: 'mendel-1',
        panelsLoaded: true,
      }),
    );
    await waitFor(() => expect(result.current.filters.gene).toBe('BRCA1'));
    // The Mendeliome default is not force-applied over an explicit query.
    expect(result.current.filters.panel_id).toBe('');
  });

  it('waits for the panels query before applying the default', async () => {
    const { result, rerender } = renderHook(
      ({ loaded, id }: { loaded: boolean; id?: string }) =>
        useSmallVariantSearchState({
          family,
          locationSearch: '',
          navigate: () => {},
          mendeliomePanelId: id,
          panelsLoaded: loaded,
        }),
      { initialProps: { loaded: false, id: undefined } as { loaded: boolean; id?: string } },
    );
    // Not yet applied while panels are still loading.
    expect(result.current.filters.panel_id).toBe('');
    rerender({ loaded: true, id: 'mendel-1' });
    await waitFor(() => expect(result.current.filters.panel_id).toBe('mendel-1'));
  });
});

describe('preset frequency ceilings bound popmax', () => {
  const family = {
    members: [
      { sample_id: 'S1', role: 'proband', affected: true, sex: 'female' },
      { sample_id: 'S2', role: 'mother', affected: false, sex: 'female' },
      { sample_id: 'S3', role: 'father', affected: false, sex: 'male' },
    ],
    relationships: [],
    projects: [],
  } as never;

  const renderSearch = () =>
    renderHook(() =>
      useSmallVariantSearchState({
        family,
        locationSearch: '',
        navigate: () => {},
        panelsLoaded: true,
      }),
    );

  // An annotation run can carry VEP's MAX_AF and no gnomAD AF at all, and the query
  // reads a missing AF as 0. A preset that caps only the AF therefore lets a variant at
  // popmax 1.0 through untouched, which is the whole point of bounding both.
  const PRESETS: SmallPreset[] = [
    'dominant_strict',
    'dominant_relaxed',
    'compound_het',
    'phenotype_priority',
    'recessive_hom',
    'recessive_permissive',
    'any_affected',
    'clinvar_review',
    'nipt_de_novo',
    'nipt_recessive',
    'expanded_carrier_screening',
  ];

  it.each(PRESETS)('%s caps popmax as tightly as the AF it sets', async (preset) => {
    const { result } = renderSearch();
    await waitFor(() => expect(result.current.filters).toBeTruthy());

    act(() => result.current.applyPreset(preset));

    const { max_gnomad_popmax_af: popmax, max_gnomad_af: af } = result.current.draftFilters;
    expect(popmax, `${preset} left popmax unbounded`).not.toBe('');
    if (af) {
      expect(Number(popmax), `${preset} popmax looser than its AF`).toBeLessThanOrEqual(Number(af));
    }
  });

  it('keeps the strictest preset strict', async () => {
    const { result } = renderSearch();
    await waitFor(() => expect(result.current.filters).toBeTruthy());

    act(() => result.current.applyPreset('dominant_strict'));
    expect(result.current.draftFilters.max_gnomad_popmax_af).toBe('0.001');

    act(() => result.current.applyPreset('compound_het'));
    expect(result.current.draftFilters.max_gnomad_popmax_af).toBe('0.02');
  });

  it('screens a couple for rare damaging variants, keeping ClinVar pathogenic founder alleles', async () => {
    const { result } = renderSearch();
    await waitFor(() => expect(result.current.filters).toBeTruthy());

    act(() => result.current.applyPreset('expanded_carrier_screening'));
    const draft = result.current.draftFilters;
    expect(draft.expanded_carrier_screening).toBe('true');
    // Without an impact filter a long-read genome's rare variants fill the candidate
    // window long before chrX, and the female partner's X-linked variants are never read.
    expect(draft.impact).toBe('HIGH, MODERATE');
    expect(draft.max_gnomad_popmax_af).toBe('0.01');
    // A pathogenic founder allele above 1% in some population (CFTR p.Phe508del) stays.
    expect(draft.clinvar_overrides_frequency).toBe('true');
  });

  it('bounds popmax on the exomes/genomes preset that sets no global AF', async () => {
    const { result } = renderSearch();
    await waitFor(() => expect(result.current.filters).toBeTruthy());

    act(() => result.current.applyPreset('phenotype_priority'));
    // This one caps exomes and genomes rather than the global AF, so popmax is the only
    // thing standing between a MAX_AF-only annotation and the result list.
    expect(result.current.draftFilters.max_gnomad_af).toBe('');
    expect(result.current.draftFilters.max_gnomad_exomes_af).toBe('0.01');
    expect(result.current.draftFilters.max_gnomad_popmax_af).toBe('0.01');
  });
});

// #604 — a location filter that cannot be read as written is named, not searched. Sent
// on, a malformed locus was searched as a gene name and an unreadable interval skipped:
// the search covered less than it asked, or read as a family without variants.
describe('location filters that cannot be read', () => {
  it('lists the unreadable locus and interval entries, by where they are set', () => {
    expect(
      smallVariantLocationProblems({
        locus: 'chr1:100-',
        intervals: 'chr13:32315086-32400266\nchr17\t43044295\t43125482',
        exclude_intervals: 'chr1:200-100',
      }),
    ).toEqual({
      include: [
        "Location 'chr1:100-' is not a gene or chr:start-end.",
        "Interval 'chr17\t43044295\t43125482' is not chr:start-end.",
      ],
      exclude: ["Excluded interval 'chr1:200-100' ends before it starts."],
    });
    const readable = smallVariantLocationProblems({
      locus: 'chr1:100-200',
      intervals: 'chr1:1–2',
      exclude_intervals: '',
    });
    expect(readable).toEqual({ include: [], exclude: [] });
    expect(hasLocationProblems(readable)).toBe(false);
  });

  it('does not apply a draft with an unreadable interval, and says why', async () => {
    const family = {
      members: [{ sample_id: 'S1', role: 'proband', affected: true, sex: 'female' }],
      relationships: [],
      projects: [],
    } as never;
    const navigate = vi.fn();
    const { result } = renderHook(() =>
      useSmallVariantSearchState({ family, locationSearch: '?gene=BRCA1', navigate, panelsLoaded: true }),
    );
    await waitFor(() => expect(result.current.filters.gene).toBe('BRCA1'));
    navigate.mockClear();

    act(() => result.current.setDraftFilterValue('intervals', 'chr17 43044295 43125482'));
    act(() => result.current.handleApply({ preventDefault: () => {} } as never));

    expect(result.current.draftLocationProblems).toEqual({
      include: ["Interval 'chr17 43044295 43125482' is not chr:start-end."],
      exclude: [],
    });
    expect(result.current.filters.intervals).toBe('');
    expect(navigate).not.toHaveBeenCalled();

    // Corrected, it applies, and the problems clear.
    act(() => result.current.setDraftFilterValue('intervals', 'chr17:43044296-43125482'));
    act(() => result.current.handleApply({ preventDefault: () => {} } as never));
    expect(result.current.draftLocationProblems).toBeNull();
    expect(result.current.filters.intervals).toBe('chr17:43044296-43125482');
  });
});

describe('deleted tags', () => {
  // A deleted custom tag is listed (inactive) only for the reviews that still hold it: it is
  // never offered, and is listed where a review or a filter holds it, so it can be unticked.
  const tag = (key: string, label: string, isActive?: boolean): SmallVariantTagDefinition => ({
    key,
    label,
    group: 'custom',
    color: '#336699',
    sort_order: 500,
    scope: 'global',
    is_custom: true,
    ...(isActive === undefined ? {} : { is_active: isActive }),
  });
  const tags = [
    tag('review', 'Review'),
    tag('needs_segregation', 'Needs segregation', true),
    tag('probe_x', 'Probe X', false),
  ];

  it('offers the active tags only', () => {
    expect(addableTagDefinitions(tags).map((entry) => entry.key)).toEqual(['review', 'needs_segregation']);
  });

  it('marks a deleted tag\'s label', () => {
    expect(tags.map(tagDefinitionLabel)).toEqual(['Review', 'Needs segregation', 'Probe X (deleted)']);
  });

  it('lists the held keys no option offers, a deleted tag by its marked label', () => {
    expect(heldTagOptions(['review', 'probe_x', 'legacy_key'], tags, ['review', 'needs_segregation'])).toEqual([
      { value: 'legacy_key', label: 'legacy_key' },
      { value: 'probe_x', label: 'Probe X (deleted)' },
    ]);
  });

  it('lists none while the tag list is still loading', () => {
    expect(heldTagOptions(['probe_x'], [], [])).toEqual([]);
  });
});
