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
