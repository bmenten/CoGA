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
