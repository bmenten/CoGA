import { renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import {
  ALL_GT_GROUPS,
  HET_GT_GROUP,
  buildSmallVariantQueryParams,
  buildPresetPayload,
  createEmptySmallFilters,
  resolveSampleFiltersFromPreset,
  useSmallVariantSearchState,
  type FamilyMember,
  type SmallVariantFilterPreset,
  type SmallVariantSampleFilter,
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
      scope: 'global',
      owner: 'reviewer',
      name: 'Layered preset',
      description: null,
      family_id: null,
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
