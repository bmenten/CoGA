import { useEffect, useMemo, useState } from 'react';
import type { ChangeEvent, FormEvent } from 'react';
import type { NavigateFunction } from 'react-router-dom';
import type { ApiFamilyMember, ApiFamilyRecord } from '../../lib/apiTypes';
import { compareChromosomes } from '../../lib/chromosomes';
import { sortFamilyMembersProbandFirst } from '../../lib/familyMembers';
import { parseGeneOrRegionInput } from '../../lib/variantSearch';
import {
  hasNonDefaultGenotypeSelection,
  parseSerializedGenotypeSelection,
} from '../../lib/sampleFilterState';
import type {
  SmallVariantFilterPreset,
  SmallVariantReview,
  SmallVariantReviewSavePayload,
  SmallVariantTagDefinition,
} from './smallVariantSearch';

export interface StructuralVariantGenotype {
  sample?: string;
  gt: string;
  read_support?: number;
  qual?: number;
  filter?: string;
}

export interface StructuralVariant {
  _id: string;
  chr: string;
  start: number;
  end: number;
  length: number;
  type: string;
  source?: string;
  qual?: number;
  read_support?: number;
  filter?: string;
  remote_chr?: string;
  remote_start?: number;
  gene?: string;
  gene_pli?: number;
  population_frequencies?: Record<string, number>;
  annotation_extra?: StructuralVariantAnnotationExtra;
  genotypes: StructuralVariantGenotype[];
  review?: SmallVariantReview | null;
}

export interface StructuralVariantAnnotationExtra {
  inheritance?: string;
  query_id?: string;
  control_support?: string;
  omim_phenotype?: string;
  omim_moi?: string;
  gencc_phenotype?: string;
  gencc_support?: string;
  gencc_moi?: string;
  hpo_terms?: string;
  pli?: number;
  region_flags?: string[];
  control_af?: number;
  population_af?: number;
  population_frequencies?: Record<string, number>;
  genotype_counts?: Record<string, number>;
  read_depths?: Record<string, number>;
  hwe?: string;
  cytoband?: string;
  [key: string]: string | number | boolean | string[] | Record<string, number> | undefined;
}

export type StructuralVariantFamilyMember = ApiFamilyMember;
export type StructuralVariantFamily = Pick<ApiFamilyRecord, 'members' | 'pedigree' | 'projects'>;
export type StructuralVariantFilterPreset = SmallVariantFilterPreset;
export type StructuralVariantTagDefinition = SmallVariantTagDefinition;
export type StructuralVariantReview = SmallVariantReview;
export type StructuralVariantReviewSavePayload = SmallVariantReviewSavePayload;

export interface StructuralGenePanel {
  _id: string;
  name: string;
}

export type StructuralSummary = Record<string, Record<string, number>>;

export type StructuralSortableKeys =
  | 'chr'
  | 'start'
  | 'end'
  | 'length'
  | 'type'
  | 'source'
  | 'qual'
  | 'read_support'
  | 'filter'
  | 'remote_chr'
  | 'remote_start'
  | 'gene'
  | 'cytoband'
  | 'inheritance'
  | 'control_af'
  | 'phenotype'
  | 'region_flags';

export type StructuralSampleFilter = {
  gt: string[];
  qual: string;
  read_support: string;
  filter: string;
};

export type StructuralFilterState = {
  locus: string;
  chr: string;
  start: string;
  end: string;
  length: string;
  minLength: string;
  type: string;
  source: string;
  remote_chr: string;
  remote_start: string;
  gene: string;
  panel_id: string;
  inheritance: string;
  phenotype: string;
  hpo: string;
  moi: string;
  gencc_support: string;
  region_flags: string;
  max_control_af: string;
  max_population_af: string;
  min_pli: string;
  classification: string;
  review_tags: string;
  exclude_review_tags: string;
  has_notes: string;
};

export type ActiveStructuralFilterChip =
  | {
      id: string;
      label: string;
      kind: 'top';
      key: keyof StructuralFilterState;
    }
  | {
      id: string;
      label: string;
      kind: 'sample-gt';
      sample: string;
    }
  | {
      id: string;
      label: string;
      kind: 'sample-field';
      sample: string;
      field: Exclude<keyof StructuralSampleFilter, 'gt'>;
    };

export type StructuralPreset = 'dominant' | 'recessive' | 'any_affected';

export const CARD_VIEW_THRESHOLD = 20;
export const STRUCTURAL_HOM_GT_GROUP = ['1/1', '1|1'];
export const STRUCTURAL_HET_GT_GROUP = ['0/1', '1/0', '0|1', '1|0'];
export const STRUCTURAL_REF_GT_GROUP = ['0/0', '0|0', './.', 'absent'];
export const STRUCTURAL_ALL_GT_GROUPS = [
  ...STRUCTURAL_HOM_GT_GROUP,
  ...STRUCTURAL_HET_GT_GROUP,
  ...STRUCTURAL_REF_GT_GROUP,
];

const STRUCTURAL_FILTER_LABELS: Record<keyof StructuralFilterState, string> = {
  locus: 'Location',
  chr: 'Chromosome',
  start: 'Start',
  end: 'End',
  length: 'Length',
  minLength: 'Min length',
  type: 'SV type',
  source: 'Callset',
  remote_chr: 'Remote chr',
  remote_start: 'Remote start',
  gene: 'Gene',
  panel_id: 'Gene panel',
  inheritance: 'Inheritance',
  phenotype: 'Phenotype',
  hpo: 'HPO',
  moi: 'Mode of inheritance',
  gencc_support: 'GenCC support',
  region_flags: 'Region flags',
  max_control_af: 'Max control AF',
  max_population_af: 'Max population AF',
  min_pli: 'Min pLI',
  classification: 'Classification',
  review_tags: 'Review tags',
  exclude_review_tags: 'Exclude review tags',
  has_notes: 'Has notes',
};

const SAMPLE_FIELD_LABELS: Record<Exclude<keyof StructuralSampleFilter, 'gt'>, string> = {
  qual: 'QUAL',
  read_support: 'Read support',
  filter: 'Filter',
};

export const createEmptyStructuralFilters = (): StructuralFilterState => ({
  locus: '',
  chr: '',
  start: '',
  end: '',
  length: '',
  minLength: '',
  type: '',
  source: '',
  remote_chr: '',
  remote_start: '',
  gene: '',
  panel_id: '',
  inheritance: '',
  phenotype: '',
  hpo: '',
  moi: '',
  gencc_support: '',
  region_flags: '',
  max_control_af: '',
  max_population_af: '',
  min_pli: '',
  classification: '',
  review_tags: '',
  exclude_review_tags: '',
  has_notes: '',
});

const parseCommaSeparatedValues = (value: string) =>
  value
    .split(',')
    .map((entry) => entry.trim())
    .filter(Boolean);

const joinFilterValues = (values: Iterable<string>) =>
  Array.from(new Set(values))
    .map((value) => value.trim())
    .filter(Boolean)
    .sort((a, b) => a.localeCompare(b))
    .join(', ');

const describeGenotypeSelection = (selection: string[]) => {
  const labels: string[] = [];
  if (STRUCTURAL_HOM_GT_GROUP.every((gt) => selection.includes(gt))) labels.push('Hom');
  if (STRUCTURAL_HET_GT_GROUP.every((gt) => selection.includes(gt))) labels.push('Het');
  if (STRUCTURAL_REF_GT_GROUP.every((gt) => selection.includes(gt))) labels.push('WT');
  return labels.length ? labels.join(' / ') : 'No genotype';
};

const buildDefaultSampleFilters = (
  members: StructuralVariantFamilyMember[],
): Record<string, StructuralSampleFilter> =>
  Object.fromEntries(
    members.map((member) => [
      member.sample_id,
      {
        gt: [...STRUCTURAL_ALL_GT_GROUPS],
        qual: '',
        read_support: '',
        filter: '',
      },
    ]),
  );

const cloneSampleFilters = (filters: Record<string, StructuralSampleFilter>) =>
  Object.fromEntries(
    Object.entries(filters).map(([sample, filter]) => [
      sample,
      { ...filter, gt: [...filter.gt] },
    ]),
  );

const buildPresetSampleFilters = (
  preset: StructuralPreset,
  members: StructuralVariantFamilyMember[],
): Record<string, StructuralSampleFilter> =>
  Object.fromEntries(
    members.map((member) => {
      let gt = [...STRUCTURAL_ALL_GT_GROUPS];
      let qual = '';
      let read_support = '';
      let filter = '';

      if (preset === 'dominant') {
        gt = member.affected
          ? [...STRUCTURAL_HET_GT_GROUP, ...STRUCTURAL_HOM_GT_GROUP]
          : [...STRUCTURAL_REF_GT_GROUP];
        read_support = '5';
      } else if (preset === 'recessive') {
        if (member.role === 'father' || member.role === 'mother') {
          gt = [...STRUCTURAL_HET_GT_GROUP];
        } else if (member.affected) {
          gt = [...STRUCTURAL_HOM_GT_GROUP, ...STRUCTURAL_HET_GT_GROUP];
        } else {
          gt = [...STRUCTURAL_REF_GT_GROUP, ...STRUCTURAL_HET_GT_GROUP];
        }
        read_support = '5';
      } else if (preset === 'any_affected') {
        gt = member.affected
          ? [...STRUCTURAL_HET_GT_GROUP, ...STRUCTURAL_HOM_GT_GROUP]
          : [...STRUCTURAL_ALL_GT_GROUPS];
      }

      qual = member.affected ? '20' : '';
      filter = '';
      return [member.sample_id, { gt, qual, read_support, filter }];
    }),
  );

const countActiveFilters = (
  filters: Record<string, string>,
  sampleFilters: Record<string, StructuralSampleFilter>,
) => {
  const topLevel =
    Object.entries(filters).filter(([key, value]) => key !== 'locus' && value.trim()).length +
    (filters.locus.trim() ? 1 : 0);

  const sampleLevel = Object.values(sampleFilters).reduce((sum, filter) => {
    const genotypeActive =
      filter.gt.length > 0 && filter.gt.length < STRUCTURAL_ALL_GT_GROUPS.length ? 1 : 0;
    const thresholdActive = [filter.qual, filter.read_support, filter.filter].filter(Boolean).length;
    return sum + genotypeActive + thresholdActive;
  }, 0);

  return topLevel + sampleLevel;
};

const hasActiveSampleFilter = (filter: StructuralSampleFilter) => {
  if (filter.qual || filter.read_support || filter.filter) return true;
  return hasNonDefaultGenotypeSelection(filter.gt, STRUCTURAL_ALL_GT_GROUPS);
};

const cloneSingleSampleFilter = (filter: StructuralSampleFilter): StructuralSampleFilter => ({
  gt: [...filter.gt],
  qual: filter.qual,
  read_support: filter.read_support,
  filter: filter.filter,
});

const serializeStructuralPresetFilters = (filters: StructuralFilterState) =>
  Object.fromEntries(
    (Object.entries(filters) as [keyof StructuralFilterState, string][])
      .filter(([, value]) => value.trim())
      .map(([key, value]) => [key, value]),
  );

const deserializeStructuralPresetFilters = (
  payload: Record<string, unknown>,
): StructuralFilterState => {
  const filters = createEmptyStructuralFilters();
  (Object.keys(filters) as (keyof StructuralFilterState)[]).forEach((key) => {
    const value = payload?.[key];
    if (typeof value === 'string') filters[key] = value;
  });
  return filters;
};

const resolveStructuralSampleFiltersFromPreset = (
  preset: StructuralVariantFilterPreset,
  members: StructuralVariantFamilyMember[],
) => {
  const base = buildDefaultSampleFilters(members);
  Object.entries(preset.sample_filters || {}).forEach(([sample, raw]) => {
    if (!base[sample] || !raw || typeof raw !== 'object') return;
    const value = raw as Partial<StructuralSampleFilter>;
    base[sample] = {
      gt: Array.isArray(value.gt) && value.gt.length ? value.gt.map(String) : base[sample].gt,
      qual: typeof value.qual === 'string' ? value.qual : base[sample].qual,
      read_support:
        typeof value.read_support === 'string' ? value.read_support : base[sample].read_support,
      filter: typeof value.filter === 'string' ? value.filter : base[sample].filter,
    };
  });
  return base;
};

export const buildStructuralVariantQueryParams = (
  currentFilters: StructuralFilterState,
  currentSampleFilters: Record<string, StructuralSampleFilter>,
  nextPage: number,
  includePageSize: boolean,
) => {
  const params = new URLSearchParams();
  params.set('page', String(nextPage));
  if (includePageSize) params.set('page_size', '100');

  if (currentFilters.locus) {
    params.set('locus', currentFilters.locus);
    const parsedLocus = parseGeneOrRegionInput(currentFilters.locus);
    if (parsedLocus?.kind === 'region') {
      params.set('chr', parsedLocus.chr);
      params.set('start', parsedLocus.start);
      params.set('end', parsedLocus.end);
    } else if (parsedLocus?.kind === 'gene') {
      params.set('gene', parsedLocus.gene);
    }
  } else {
    if (currentFilters.gene) params.set('gene', currentFilters.gene);
    if (currentFilters.chr) params.set('chr', currentFilters.chr);
    if (currentFilters.start) params.set('start', currentFilters.start);
    if (currentFilters.end) params.set('end', currentFilters.end);
  }

  if (currentFilters.length) params.set('length', currentFilters.length);
  if (currentFilters.minLength) params.set('min_length', currentFilters.minLength);
  if (currentFilters.type) params.set('type', currentFilters.type);
  if (currentFilters.source) params.set('source', currentFilters.source);
  if (currentFilters.remote_chr) params.set('remote_chr', currentFilters.remote_chr);
  if (currentFilters.remote_start) params.set('remote_start', currentFilters.remote_start);
  if (currentFilters.panel_id) params.set('panel_id', currentFilters.panel_id);
  if (currentFilters.inheritance) params.set('inheritance', currentFilters.inheritance);
  if (currentFilters.phenotype) params.set('phenotype', currentFilters.phenotype);
  if (currentFilters.hpo) params.set('hpo', currentFilters.hpo);
  if (currentFilters.moi) params.set('moi', currentFilters.moi);
  if (currentFilters.gencc_support) params.set('gencc_support', currentFilters.gencc_support);
  if (currentFilters.max_control_af) params.set('max_control_af', currentFilters.max_control_af);
  if (currentFilters.max_population_af) {
    params.set('max_population_af', currentFilters.max_population_af);
  }
  if (currentFilters.min_pli) params.set('min_pli', currentFilters.min_pli);
  currentFilters.region_flags
    .split(',')
    .map((entry) => entry.trim())
    .filter(Boolean)
    .forEach((flag) => params.append('region_flag', flag));
  parseCommaSeparatedValues(currentFilters.classification).forEach((value) => {
    params.append('classification', value);
  });
  parseCommaSeparatedValues(currentFilters.review_tags).forEach((value) => {
    params.append('review_tag', value);
  });
  parseCommaSeparatedValues(currentFilters.exclude_review_tags).forEach((value) => {
    params.append('exclude_review_tag', value);
  });
  if (currentFilters.has_notes === 'true') params.set('has_notes', 'true');

  Object.entries(currentSampleFilters).forEach(([sample, filter]) => {
    const { gt, qual, read_support, filter: filterText } = filter;
    if (hasActiveSampleFilter(filter)) {
      params.append('sample_filter', [sample, gt.join('|'), qual, read_support, filterText].join(':'));
    }
  });

  return params;
};

const buildActiveFilterChips = (
  filters: StructuralFilterState,
  members: StructuralVariantFamilyMember[],
  sampleFilters: Record<string, StructuralSampleFilter>,
): ActiveStructuralFilterChip[] => {
  const chips: ActiveStructuralFilterChip[] = [];
  const skipDerivedLocusKeys = filters.locus
    ? new Set<keyof StructuralFilterState>(['chr', 'start', 'end', 'gene'])
    : null;

  (Object.entries(filters) as [keyof StructuralFilterState, string][]).forEach(([key, value]) => {
    if (!value) return;
    if (skipDerivedLocusKeys?.has(key)) return;
    chips.push({
      id: `top:${key}`,
      label:
        value === 'true'
          ? STRUCTURAL_FILTER_LABELS[key]
          : `${STRUCTURAL_FILTER_LABELS[key]}: ${value}`,
      kind: 'top',
      key,
    });
  });

  members.forEach((member) => {
    const filter = sampleFilters[member.sample_id];
    if (!filter) return;

    if (hasNonDefaultGenotypeSelection(filter.gt, STRUCTURAL_ALL_GT_GROUPS)) {
      chips.push({
        id: `sample:${member.sample_id}:gt`,
        label: `${member.sample_id}: ${describeGenotypeSelection(filter.gt)}`,
        kind: 'sample-gt',
        sample: member.sample_id,
      });
    }

    (Object.entries(SAMPLE_FIELD_LABELS) as [
      Exclude<keyof StructuralSampleFilter, 'gt'>,
      string,
    ][]).forEach(([field, label]) => {
      const value = filter[field];
      if (!value) return;
      chips.push({
        id: `sample:${member.sample_id}:${field}`,
        label: `${member.sample_id} ${label} ${value}`,
        kind: 'sample-field',
        sample: member.sample_id,
        field,
      });
    });
  });

  return chips;
};

type UseStructuralVariantSearchStateArgs = {
  family?: StructuralVariantFamily;
  locationSearch: string;
  navigate: NavigateFunction;
};

export const useStructuralVariantSearchState = ({
  family,
  locationSearch,
  navigate,
}: UseStructuralVariantSearchStateArgs) => {
  const emptyFilters = useMemo(() => createEmptyStructuralFilters(), []);
  const orderedMembers = useMemo(
    () => sortFamilyMembersProbandFirst(family?.members || []),
    [family?.members],
  );

  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState(emptyFilters);
  const [draftFilters, setDraftFilters] = useState(emptyFilters);
  const [sampleFilters, setSampleFilters] = useState<Record<string, StructuralSampleFilter>>({});
  const [sampleDraftFilters, setSampleDraftFilters] = useState<Record<
    string,
    StructuralSampleFilter
  >>({});

  useEffect(() => {
    if (!family) return;

    const params = new URLSearchParams(locationSearch);
    const initialFilters = { ...emptyFilters };
    (Object.keys(initialFilters) as (keyof StructuralFilterState)[]).forEach((key) => {
      const paramKey = key === 'minLength' ? 'min_length' : key;
      const value = params.get(paramKey);
      if (value !== null) initialFilters[key] = value;
    });
    const regionFlags = params.getAll('region_flag');
    if (regionFlags.length) initialFilters.region_flags = regionFlags.join(', ');
    const classifications = params.getAll('classification');
    if (classifications.length) initialFilters.classification = joinFilterValues(classifications);
    const reviewTags = params.getAll('review_tag');
    if (reviewTags.length) initialFilters.review_tags = joinFilterValues(reviewTags);
    const excludeReviewTags = params.getAll('exclude_review_tag');
    if (excludeReviewTags.length) {
      initialFilters.exclude_review_tags = joinFilterValues(excludeReviewTags);
    }
    if (params.get('has_notes') === 'true') initialFilters.has_notes = 'true';
    if (initialFilters.locus) {
      initialFilters.chr = '';
      initialFilters.start = '';
      initialFilters.end = '';
      initialFilters.gene = '';
    }

    const initialSampleFilters = buildDefaultSampleFilters(family.members);
    params.getAll('sample_filter').forEach((entry) => {
      const parts = entry.split(':');
      const [sample, , qual, read_support, filter] = parts;
      if (!sample || !initialSampleFilters[sample]) return;
      initialSampleFilters[sample] = {
        gt: parseSerializedGenotypeSelection(entry, initialSampleFilters[sample].gt),
        qual: qual ?? '',
        read_support: read_support ?? '',
        filter: filter ?? '',
      };
    });

    const parsedPage = Math.max(1, parseInt(params.get('page') || '1', 10) || 1);
    setDraftFilters(initialFilters);
    setFilters(initialFilters);
    setSampleDraftFilters(cloneSampleFilters(initialSampleFilters));
    setSampleFilters(cloneSampleFilters(initialSampleFilters));
    setPage(parsedPage);
  }, [emptyFilters, family, locationSearch]);

  const applySearchState = (
    nextFilters: StructuralFilterState,
    nextSampleFilters: Record<string, StructuralSampleFilter>,
    nextPage: number,
  ) => {
    setDraftFilters(nextFilters);
    setFilters(nextFilters);
    setSampleDraftFilters(cloneSampleFilters(nextSampleFilters));
    setSampleFilters(cloneSampleFilters(nextSampleFilters));
    setPage(nextPage);
    navigate({
      search: buildStructuralVariantQueryParams(nextFilters, nextSampleFilters, nextPage, true).toString(),
    });
  };

  const setDraftFilterValue = (name: keyof StructuralFilterState, value: string) => {
    setDraftFilters((prev) => ({ ...prev, [name]: value }));
  };

  const toggleDraftFilterListValue = (
    key: Extract<
      keyof StructuralFilterState,
      'classification' | 'review_tags' | 'exclude_review_tags' | 'region_flags'
    >,
    value: string,
  ) => {
    setDraftFilters((prev) => {
      const current = new Set(parseCommaSeparatedValues(prev[key]));
      if (current.has(value)) {
        current.delete(value);
      } else {
        current.add(value);
      }
      return { ...prev, [key]: joinFilterValues(current) };
    });
  };

  const handleFilterChange = (event: ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    const { name, value } = event.target;
    setDraftFilterValue(name as keyof StructuralFilterState, value);
  };

  const handleSampleFieldChange = (
    sample: string,
    field: Exclude<keyof StructuralSampleFilter, 'gt'>,
    value: string,
  ) => {
    setSampleDraftFilters((prev) => ({
      ...prev,
      [sample]: { ...prev[sample], [field]: value },
    }));
  };

  const handleGtToggle = (sample: string, group: string, checked: boolean) => {
    setSampleDraftFilters((prev) => {
      const current = prev[sample];
      const next = new Set(current.gt);
      const groupValues =
        group === 'ref-group'
          ? STRUCTURAL_REF_GT_GROUP
          : group === 'het-group'
          ? STRUCTURAL_HET_GT_GROUP
          : STRUCTURAL_HOM_GT_GROUP;
      if (checked) {
        groupValues.forEach((gt) => next.add(gt));
      } else {
        groupValues.forEach((gt) => next.delete(gt));
      }
      return { ...prev, [sample]: { ...current, gt: Array.from(next) } };
    });
  };

  const applyPreset = (preset: StructuralPreset) => {
    if (!orderedMembers.length) return;
    setSampleDraftFilters(buildPresetSampleFilters(preset, orderedMembers));
  };

  const applySavedPreset = (preset: StructuralVariantFilterPreset) => {
    if (!orderedMembers.length) return;
    const nextFilters = deserializeStructuralPresetFilters(preset.filters);
    const nextSampleFilters = resolveStructuralSampleFiltersFromPreset(preset, orderedMembers);
    applySearchState(nextFilters, nextSampleFilters, 1);
  };

  const handleSearch = (event: FormEvent) => {
    event.preventDefault();
    const nextSampleFilters = cloneSampleFilters(sampleDraftFilters);
    applySearchState(draftFilters, nextSampleFilters, 1);
  };

  const handleReset = () => {
    if (!family) return;
    const resetSampleFilters = buildDefaultSampleFilters(family.members);
    setDraftFilters(emptyFilters);
    setFilters(emptyFilters);
    setSampleDraftFilters(cloneSampleFilters(resetSampleFilters));
    setSampleFilters(cloneSampleFilters(resetSampleFilters));
    setPage(1);
    navigate({ search: '' });
  };

  const requestQueryString = useMemo(
    () => buildStructuralVariantQueryParams(filters, sampleFilters, page, true).toString(),
    [filters, sampleFilters, page],
  );

  const linkSearch = useMemo(() => {
    const params = buildStructuralVariantQueryParams(filters, sampleFilters, page, false);
    const queryString = params.toString();
    return queryString ? `?${queryString}` : '';
  }, [filters, sampleFilters, page]);

  const activeFilterCount = useMemo(
    () => countActiveFilters(filters, sampleFilters),
    [filters, sampleFilters],
  );

  const activeFilterChips = useMemo(
    () => buildActiveFilterChips(filters, orderedMembers, sampleFilters),
    [filters, orderedMembers, sampleFilters],
  );

  const removeActiveFilterChip = (chip: ActiveStructuralFilterChip) => {
    const nextFilters = { ...filters };
    const nextSampleFilters = cloneSampleFilters(sampleFilters);

    if (chip.kind === 'top') {
      if (
        chip.key === 'classification' ||
        chip.key === 'review_tags' ||
        chip.key === 'exclude_review_tags' ||
        chip.key === 'region_flags'
      ) {
        nextFilters[chip.key] = '';
      } else {
        nextFilters[chip.key] = '';
      }
      if (chip.key === 'locus') {
        nextFilters.chr = '';
        nextFilters.start = '';
        nextFilters.end = '';
        nextFilters.gene = '';
      }
    } else if (chip.kind === 'sample-gt') {
      nextSampleFilters[chip.sample] = {
        ...nextSampleFilters[chip.sample],
        gt: [...STRUCTURAL_ALL_GT_GROUPS],
      };
    } else {
      nextSampleFilters[chip.sample] = {
        ...nextSampleFilters[chip.sample],
        [chip.field]: '',
      };
    }

    applySearchState(nextFilters, nextSampleFilters, 1);
  };

  const goToPage = (nextPage: number) => {
    applySearchState(filters, sampleFilters, nextPage);
  };

  const buildOrderedGenotypes = (variant: StructuralVariant) => {
    const seen = new Set<string>();
    const ordered = orderedMembers
      .map((member) => {
        const genotype = variant.genotypes.find((entry) => entry.sample === member.sample_id);
        if (!genotype) return null;
        seen.add(member.sample_id);
        return genotype;
      })
      .filter((entry): entry is StructuralVariantGenotype => Boolean(entry));

    const extras = variant.genotypes.filter((entry) => !entry.sample || !seen.has(entry.sample));

    return [...ordered, ...extras];
  };

  return {
    activeFilterChips,
    activeFilterCount,
    applyPreset,
    applySavedPreset,
    buildOrderedGenotypes,
    draftFilters,
    emptyFilters,
    filters,
    goToPage,
    handleFilterChange,
    handleGtToggle,
    handleReset,
    handleSampleFieldChange,
    handleSearch,
    linkSearch,
    orderedMembers,
    page,
    removeActiveFilterChip,
    requestQueryString,
    sampleDraftFilters,
    sampleFilters,
    setDraftFilterValue,
    toggleDraftFilterListValue,
  };
};

export type StructuralVariantSearchState = ReturnType<typeof useStructuralVariantSearchState>;

export const sortStructuralVariants = (
  variants: StructuralVariant[],
  sortKey: StructuralSortableKeys,
  sortAsc: boolean,
) =>
  [...variants].sort((a, b) => {
    const readValue = (variant: StructuralVariant) => {
      if (sortKey === 'inheritance') return variant.annotation_extra?.inheritance ?? '';
      if (sortKey === 'cytoband') return variant.annotation_extra?.cytoband ?? '';
      if (sortKey === 'control_af') return variant.annotation_extra?.control_af ?? '';
      if (sortKey === 'phenotype') {
        return variant.annotation_extra?.omim_phenotype || variant.annotation_extra?.gencc_phenotype || '';
      }
      if (sortKey === 'region_flags') return variant.annotation_extra?.region_flags?.join(', ') ?? '';
      return variant[sortKey] ?? '';
    };
    const aValue = readValue(a);
    const bValue = readValue(b);
    if (sortKey === 'chr' || sortKey === 'remote_chr') {
      const diff = compareChromosomes(String(aValue), String(bValue));
      return sortAsc ? diff : -diff;
    }
    if (aValue < bValue) return sortAsc ? -1 : 1;
    if (aValue > bValue) return sortAsc ? 1 : -1;
    return 0;
  });

export const buildStructuralPresetPayload = ({
  filters,
  members,
  sampleFilters,
}: {
  filters: StructuralFilterState;
  members: StructuralVariantFamilyMember[];
  sampleFilters: Record<string, StructuralSampleFilter>;
}) => {
  const activeSampleFilters = Object.fromEntries(
    Object.entries(sampleFilters)
      .filter(([, filter]) => hasActiveSampleFilter(filter))
      .map(([sample, filter]) => [sample, cloneSingleSampleFilter(filter)]),
  );

  return {
    filters: serializeStructuralPresetFilters(filters),
    sample_filters: activeSampleFilters,
    sample_templates: Object.fromEntries(members.map((member) => [member.sample_id, member.role])),
  };
};
