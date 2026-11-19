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
