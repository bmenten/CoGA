/**
 * The analyses a family holds (`families.metadata.analysis_types`, backend
 * `family_analysis_types.py`). A family may hold several, such as the monogenic and the
 * conventional NIPT of one pregnancy, each imported from a package of its own; every reader asks
 * whether the family holds an analysis, never which one it holds.
 */
export const familyAnalysisTypes = (metadata: unknown): string[] => {
  const types = (metadata as { analysis_types?: unknown } | null | undefined)?.analysis_types;
  return Array.isArray(types) ? types.filter((value): value is string => typeof value === 'string') : [];
};

/**
 * The analysis a sign-out of the family, or its check, is of when the page names none: the
 * family's only analysis, or null for a family that holds none or several (backend
 * `report_signout_service.resolve_signout_analysis`, which asks for one of several).
 */
export const soleAnalysis = (metadata: unknown): string | null => {
  const types = familyAnalysisTypes(metadata);
  return types.length === 1 ? types[0] : null;
};

/** Whether a family's metadata holds the analysis `type`. */
export const holdsAnalysis = (metadata: unknown, type: string): boolean => familyAnalysisTypes(metadata).includes(type);

/** How the pages name each analysis a package may name (backend `family_package_records.ANALYSIS_LABELS`). */
const ANALYSIS_LABELS: Record<string, string> = {
  long_read_wgs: 'long-read WGS',
  long_read_adaptive_sampling: 'long-read adaptive sampling',
  short_read_wgs: 'short-read WGS',
  pgt: 'PGT',
  expanded_carrier_screening: 'expanded carrier screening',
  monogenic_nipt: 'monogenic NIPT',
  conventional_nipt: 'conventional NIPT',
};

/** An analysis's name in a sentence ("monogenic NIPT"); null for one without a label (`unspecified`). */
export const analysisLabel = (analysisType: string | null | undefined): string | null =>
  (analysisType && ANALYSIS_LABELS[analysisType]) || null;

/**
 * The analyses of a family that are each signed out on their own, sorted: every analysis of a
 * family that holds several, whose sign-out of one freezes and checks that analysis's data only
 * (the owner's decision of 2026-11-19). None for a family of one analysis or none, which is
 * signed out whole.
 */
export const separatelySignedAnalyses = (metadata: unknown): string[] => {
  const types = familyAnalysisTypes(metadata);
  return types.length > 1 ? [...types].sort() : [];
};

/** The analysis the NIPT report signs out: the monogenic NIPT of a family that holds several, else null (the whole family). */
export const niptReportAnalysis = (metadata: unknown): string | null =>
  separatelySignedAnalyses(metadata).includes('monogenic_nipt') ? 'monogenic_nipt' : null;

/**
 * The analyses the family's Report page signs out of a family that holds several: every one but
 * the monogenic NIPT, which the NIPT report signs out. Empty for a family signed out whole.
 */
export const reportPageAnalyses = (metadata: unknown): string[] =>
  separatelySignedAnalyses(metadata).filter((type) => type !== 'monogenic_nipt');

type PackageRecordLike = { sources?: { small_variants?: unknown }; registered_at?: unknown };

/**
 * The family's analyses whose package record lists a small-variant callset of its own, in the
 * order they were imported (backend `analysis_scope.analyses_with_small_variants`). A family with
 * two or more is read one analysis at a time: its small-variant list, filters, counts, export and
 * reviews name the analysis (the owner's decision of 2026-11-20).
 */
export const smallVariantAnalyses = (metadata: unknown): string[] => {
  const held = new Set(familyAnalysisTypes(metadata));
  const records = (metadata as { package_imports?: unknown } | null | undefined)?.package_imports;
  if (!records || typeof records !== 'object') return [];
  return Object.entries(records as Record<string, PackageRecordLike>)
    .filter(
      ([key, record]) =>
        held.has(key) &&
        Array.isArray(record?.sources?.small_variants) &&
        (record.sources?.small_variants as unknown[]).length > 0,
    )
    .sort(([keyA, a], [keyB, b]) => {
      const order = String(a?.registered_at ?? '').localeCompare(String(b?.registered_at ?? ''));
      return order !== 0 ? order : keyA.localeCompare(keyB);
    })
    .map(([key]) => key);
};

/** The URL search parameter, and API query parameter, that names the analysis a page reads. */
export const ANALYSIS_PARAM = 'analysis';

/**
 * The analysis a page of the family's variants reads: of a family with two or more analyses with
 * small variants (`smallVariantAnalyses`), the one the URL names, else the one imported last;
 * null for any other family, which is read whole (or as its one analysis) without naming one.
 */
export const variantAnalysisOf = (metadata: unknown, search: string): string | null => {
  const analyses = smallVariantAnalyses(metadata);
  if (analyses.length < 2) return null;
  const asked = new URLSearchParams(search).get(ANALYSIS_PARAM);
  return asked && analyses.includes(asked) ? asked : analyses[analyses.length - 1];
};

/** `params` with the analysis a request reads set, when there is one to name. */
export const withAnalysisParam = (params: URLSearchParams, analysis: string | null | undefined): URLSearchParams => {
  if (analysis) params.set(ANALYSIS_PARAM, analysis);
  return params;
};

/** `?analysis=…` for a request or link that names the analysis, or '' for none. */
export const analysisQuery = (analysis: string | null | undefined): string =>
  analysis ? `?${ANALYSIS_PARAM}=${encodeURIComponent(analysis)}` : '';

/**
 * The analysis the monogenic NIPT page reads and reviews the family's small variants for: the
 * monogenic NIPT, named when the family holds another analysis with small variants of its own
 * (the couple's carrier screening); null otherwise, where it is the family's one.
 */
export const niptVariantAnalysis = (metadata: unknown): string | null =>
  smallVariantAnalyses(metadata).length > 1 && holdsAnalysis(metadata, 'monogenic_nipt') ? 'monogenic_nipt' : null;
