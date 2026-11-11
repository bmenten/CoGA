import type { ImportDeliveryAdd } from '../../lib/apiSchema.generated';

/** An analysis a package names (`analysis_type`, docs/coga-package.md). */
export type AnalysisType = NonNullable<ImportDeliveryAdd['analysis_type']>;

/** The analyses, in the order they are offered, each with the name the laboratory uses. */
export const ANALYSIS_TYPES: readonly (readonly [AnalysisType, string])[] = [
  ['long_read_wgs', 'LR-WGS'],
  ['long_read_adaptive_sampling', 'LR-AS (long-read adaptive sampling)'],
  ['pgt', 'PGT'],
  ['expanded_carrier_screening', 'Expanded Carrier Screening'],
  ['monogenic_nipt', 'mNIPT'],
];

const LABELS: Record<string, string> = Object.fromEntries(ANALYSIS_TYPES);

/** The laboratory's name of the analysis a package names. */
export const analysisTypeLabel = (value: string): string =>
  LABELS[value] ?? value;
