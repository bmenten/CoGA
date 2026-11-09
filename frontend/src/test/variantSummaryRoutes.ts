import { vi } from 'vitest';
import type {
  FamilyMitoDNAAnalysisOut,
  FamilyParaphaseTableOut,
  FamilyRepeatExpansionTableOut,
  FamilySmallVariantStatisticsOut,
  NiptVariantStatisticsOut,
  SmallVariantSampleStatisticsOut,
  StructuralVariantSampleCountsOut,
} from '../lib/apiSchema.generated';

/**
 * The answers the variant summary's tests serve: the family, the presence checks of its data,
 * and each section's request. A Promise value is used as is (pending or rejected). Synthetic
 * families only.
 */

export const SUMMARY_FAMILY = {
  family_id: 'F1',
  pedigree: 'F1 S1 S2 S3 1 2\nF1 S2 0 0 1 1\nF1 S3 0 0 2 1',
  members: [
    { sample_id: 'S1', role: 'proband', affected: true, sex: 'male' },
    { sample_id: 'S2', role: 'father', affected: false, sex: 'male' },
    { sample_id: 'S3', role: 'mother', affected: false, sex: 'female' },
  ],
};

export type LengthRow = {
  length: number;
  type: string;
  source?: string | null;
  chr: string;
};

export const sv = (
  chr: string,
  type: string,
  length = 5000,
  source: string | null = 'sniffles'
): LengthRow => ({
  chr,
  type,
  length,
  source,
});

/** A sample's small-variant statistics, every figure 0 unless given. */
export const sampleStats = (
  sampleId: string,
  overrides: Partial<SmallVariantSampleStatisticsOut> = {}
): SmallVariantSampleStatisticsOut => ({
  sample_id: sampleId,
  non_ref_count: 0,
  het_count: 0,
  hom_alt_count: 0,
  snv_count: 0,
  insertion_count: 0,
  deletion_count: 0,
  other_count: 0,
  transition_count: 0,
  transversion_count: 0,
  passing_count: 0,
  consequences: [],
  chromosomes: [],
  genotype_quality_histogram: [],
  depth_histogram: [],
  allele_balance_histogram: [],
  genotype_quality_missing: 0,
  depth_missing: 0,
  allele_balance_missing: 0,
  ...overrides,
});

export const smallStats = (
  overrides: Partial<FamilySmallVariantStatisticsOut> = {}
): FamilySmallVariantStatisticsOut => ({
  sources: ['deepvariant'],
  imputed_sources: [],
  total_variants: 0,
  snv_count: 0,
  insertion_count: 0,
  deletion_count: 0,
  other_count: 0,
  samples: [],
  trios: [],
  nipt_samples: [],
  ...overrides,
});

/** A request that fails with `reason`: the rejection is made when it is asked for, as a section
 *  asks only once the presence checks are in (an eager one would go unhandled meanwhile). */
export class Rejection {
  constructor(readonly reason: unknown) {}
}

export const failWith = (reason: unknown) => new Rejection(reason);

type Answer<T> = T | Promise<unknown> | Rejection;

export interface SummaryAnswers {
  family?: Answer<Record<string, unknown>>;
  /** Which kinds of data the presence checks report; a Promise fails or holds that check. */
  has?: Partial<
    Record<
      'small' | 'structural' | 'repeats' | 'paraphase' | 'mito',
      boolean | Promise<unknown> | Rejection
    >
  >;
  lengths?: Answer<LengthRow[]>;
  shared?: Answer<Record<string, Record<string, number>>>;
  svSamples?: Answer<StructuralVariantSampleCountsOut[]>;
  small?: Answer<FamilySmallVariantStatisticsOut>;
  nipt?: Answer<NiptVariantStatisticsOut>;
  repeats?: Answer<Partial<FamilyRepeatExpansionTableOut>>;
  paraphase?: Answer<Partial<FamilyParaphaseTableOut>>;
  mito?: Answer<Partial<FamilyMitoDNAAnalysisOut>>;
  projects?: Answer<unknown[]>;
}

const presenceBody = (key: string, has: boolean) => {
  switch (key) {
    case 'repeat-expansions':
      return { loci_count: has ? 1 : 0 };
    case 'paraphase':
      return { genes_count: has ? 1 : 0 };
    case 'mitochondrial-dna':
      return { variant_count: has ? 1 : 0, has_coverage: false };
    default:
      return { total: has ? 1 : 0 };
  }
};

const PRESENCE_KEYS: Record<string, keyof NonNullable<SummaryAnswers['has']>> =
  {
    'small-variants': 'small',
    'structural-variants': 'structural',
    'repeat-expansions': 'repeats',
    paraphase: 'paraphase',
    'mitochondrial-dna': 'mito',
  };

/** Answer the summary's requests for `familyId` (as the path encodes it) on `get`. */
export const serveVariantSummary = (
  get: ReturnType<typeof vi.fn>,
  familyId: string,
  answers: SummaryAnswers = {}
) => {
  const has = { structural: true, ...answers.has };
  const routes: Record<string, unknown> = {
    [`/families/${familyId}`]: answers.family ?? SUMMARY_FAMILY,
    '/projects': answers.projects ?? [],
    [`/families/${familyId}/structural-variant-lengths`]: answers.lengths ?? [],
    [`/families/${familyId}/shared-structural-variant-counts`]:
      answers.shared ?? {},
    [`/families/${familyId}/structural-variant-sample-counts`]:
      answers.svSamples ?? [],
    [`/families/${familyId}/small-variant-statistics`]:
      answers.small ?? smallStats(),
    ...(answers.nipt
      ? { [`/families/${familyId}/nipt/statistics`]: answers.nipt }
      : {}),
    [`/families/${familyId}/repeat-expansions`]: answers.repeats ?? {
      samples: [],
      loci: [],
      loci_count: 0,
    },
    [`/families/${familyId}/paraphase`]: answers.paraphase ?? {
      samples: [],
      genes: [],
      genes_count: 0,
    },
    [`/families/${familyId}/mitochondrial-dna`]: answers.mito ?? {
      samples: [],
      variants: [],
      structural_variants: [],
      variant_count: 0,
      has_coverage: false,
      qc_notes: [],
      heteroplasmy_threshold: 0.02,
      homoplasmy_threshold: 0.95,
    },
  };
  get.mockImplementation((url: string) => {
    const [path, query] = url.split('?');
    if (query && new URLSearchParams(query).get('count_only') === 'true') {
      const kind = path.slice(`/families/${familyId}/`.length);
      const key = PRESENCE_KEYS[kind];
      if (!key) return Promise.reject(new Error(`Unexpected GET ${url}`));
      const value = has[key] ?? false;
      if (value instanceof Rejection) return Promise.reject(value.reason);
      return value instanceof Promise
        ? value
        : Promise.resolve({ data: presenceBody(kind, value) });
    }
    if (!(path in routes))
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    const value = routes[path];
    if (value instanceof Rejection) return Promise.reject(value.reason);
    return value instanceof Promise ? value : Promise.resolve({ data: value });
  });
};
