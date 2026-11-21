/**
 * The analyses a family holds (`families.metadata.analyses`, backend `family_analysis_types.py`),
 * each by its key: its analysis type, and for the NIPT of a pregnancy the type and the pregnancy
 * its package names (`monogenic_nipt:ZW1`). A family may hold several: short-read and then
 * long-read WGS of the same DNA, or the monogenic and the conventional NIPT of each of a woman's
 * pregnancies, each imported from a package of its own. A reader asks whether the family holds an
 * analysis type (`holdsAnalysis`), or reads one analysis by its key.
 */
export const familyAnalyses = (metadata: unknown): string[] => {
  const keys = (metadata as { analyses?: unknown } | null | undefined)?.analyses;
  return Array.isArray(keys) ? keys.filter((value): value is string => typeof value === 'string' && value !== '') : [];
};

/** The analysis types whose analyses are each one pregnancy's (backend `PREGNANCY_ANALYSIS_TYPES`). */
const PREGNANCY_ANALYSIS_TYPES = ['monogenic_nipt', 'conventional_nipt'];
/** Between the analysis type and the pregnancy in a pregnancy's analysis's key. */
const PREGNANCY_SEPARATOR = ':';

/** The analysis type of the analysis whose key is `key`: the key up to its pregnancy. */
export const analysisTypeOf = (key: string | null | undefined): string => (key ?? '').split(PREGNANCY_SEPARATOR)[0];

/** The pregnancy of the analysis whose key is `key`; null for an analysis of no pregnancy. */
export const pregnancyOf = (key: string | null | undefined): string | null => {
  const text = key ?? '';
  const at = text.indexOf(PREGNANCY_SEPARATOR);
  if (at < 0 || !PREGNANCY_ANALYSIS_TYPES.includes(text.slice(0, at))) return null;
  return text.slice(at + 1);
};

/** The analysis types of the analyses a family holds, each once. */
export const familyAnalysisTypes = (metadata: unknown): string[] => [
  ...new Set(familyAnalyses(metadata).map((key) => analysisTypeOf(key))),
];

/**
 * The analysis a sign-out of the family, or its check, is of when the page names none: the
 * family's only analysis, or null for a family that holds none or several (backend
 * `report_signout_service.resolve_signout_analysis`, which asks for one of several).
 */
export const soleAnalysis = (metadata: unknown): string | null => {
  const keys = familyAnalyses(metadata);
  return keys.length === 1 ? keys[0] : null;
};

/** Whether a family's metadata holds an analysis of the type `type`. */
export const holdsAnalysis = (metadata: unknown, type: string): boolean => familyAnalysisTypes(metadata).includes(type);

/** How the pages name each analysis type a package may name (backend `family_package_records.ANALYSIS_LABELS`). */
const ANALYSIS_LABELS: Record<string, string> = {
  long_read_wgs: 'long-read WGS',
  long_read_adaptive_sampling: 'long-read adaptive sampling',
  short_read_wgs: 'short-read WGS',
  pgt: 'PGT',
  expanded_carrier_screening: 'expanded carrier screening',
  monogenic_nipt: 'monogenic NIPT',
  conventional_nipt: 'conventional NIPT',
};

/**
 * An analysis's name in a sentence, by its key: "monogenic NIPT", or for a pregnancy's analysis
 * "monogenic NIPT of pregnancy ZW1"; null for one without a label (`unspecified`).
 */
export const analysisLabel = (key: string | null | undefined): string | null => {
  const label = ANALYSIS_LABELS[analysisTypeOf(key)];
  if (!label) return null;
  const pregnancy = pregnancyOf(key);
  return pregnancy !== null ? `${label} of pregnancy ${pregnancy}` : label;
};

/**
 * The analyses of a family that are each signed out on their own, sorted: every analysis of a
 * family that holds several, whose sign-out of one freezes and checks that analysis's data only
 * (the owner's decision of 2026-11-19). None for a family of one analysis or none, which is
 * signed out whole.
 */
export const separatelySignedAnalyses = (metadata: unknown): string[] => {
  const keys = familyAnalyses(metadata);
  return keys.length > 1 ? [...keys].sort() : [];
};

/**
 * The analysis the NIPT report of the monogenic NIPT `niptAnalysis` (one pregnancy's) signs out:
 * that analysis, of a family that holds several, else null (the whole family).
 */
export const niptReportAnalysis = (metadata: unknown, niptAnalysis: string | null): string | null =>
  niptAnalysis !== null && separatelySignedAnalyses(metadata).includes(niptAnalysis) ? niptAnalysis : null;

/**
 * The analyses the family's Report page signs out of a family that holds several: every one but
 * the monogenic NIPTs, which the NIPT report of each pregnancy signs out. Empty for a family
 * signed out whole.
 */
export const reportPageAnalyses = (metadata: unknown): string[] =>
  separatelySignedAnalyses(metadata).filter((key) => analysisTypeOf(key) !== 'monogenic_nipt');

type PackageRecordLike = { sources?: { small_variants?: unknown }; registered_at?: unknown };

const packageRecords = (metadata: unknown): Record<string, PackageRecordLike> => {
  const records = (metadata as { package_imports?: unknown } | null | undefined)?.package_imports;
  return records && typeof records === 'object' ? (records as Record<string, PackageRecordLike>) : {};
};

/** `keys` in the order their analyses were imported (a package record's `registered_at`), then by key. */
const inImportOrder = (metadata: unknown, keys: string[]): string[] => {
  const records = packageRecords(metadata);
  return [...keys].sort((keyA, keyB) => {
    const order = String(records[keyA]?.registered_at ?? '').localeCompare(String(records[keyB]?.registered_at ?? ''));
    return order !== 0 ? order : keyA.localeCompare(keyB);
  });
};

/**
 * The family's analyses whose package record lists a small-variant callset of its own, in the
 * order they were imported (backend `analysis_scope.analyses_with_small_variants`). A family with
 * two or more is read one analysis at a time: its small-variant list, filters, counts, export and
 * reviews name the analysis (the owner's decision of 2026-11-20).
 */
export const smallVariantAnalyses = (metadata: unknown): string[] => {
  const held = new Set(familyAnalyses(metadata));
  const records = packageRecords(metadata);
  return inImportOrder(
    metadata,
    Object.keys(records).filter((key) => {
      const sources = records[key]?.sources?.small_variants;
      return held.has(key) && Array.isArray(sources) && sources.length > 0;
    }),
  );
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
 * The family's analyses of the pregnancy analysis type `type` (`monogenic_nipt`,
 * `conventional_nipt`): one per pregnancy, in the order they were imported.
 */
export const pregnancyAnalyses = (metadata: unknown, type: string): string[] =>
  inImportOrder(
    metadata,
    familyAnalyses(metadata).filter((key) => analysisTypeOf(key) === type),
  );

/**
 * The NIPT of `type` a page of the family reads (backend `nipt.nipt_analysis_of`): of the
 * family's analyses of that type, one per pregnancy, the one the URL names, else the one imported
 * last; null for a family that holds none. A family's pregnancies are read one at a time.
 */
export const pregnancyAnalysisOf = (metadata: unknown, type: string, search: string): string | null => {
  const analyses = pregnancyAnalyses(metadata, type);
  if (!analyses.length) return null;
  const asked = new URLSearchParams(search).get(ANALYSIS_PARAM);
  return asked && analyses.includes(asked) ? asked : analyses[analyses.length - 1];
};
