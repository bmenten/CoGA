// The responses these pages read are the generated types (FamilyInventoryDetailOut,
// ClickHouseVariantAssemblyListOut, FamilyRawFilesOut, …). What stays here is the page's own
// vocabulary: the track keys the backend counts, in the order the page shows them.
export type SampleTrackType =
  | 'coverage'
  | 'segments'
  | 'apcad'
  | 'apcad_pcf'
  | 'haplotype'
  | 'target_coverage'
  | 'structural_variants'
  | 'repeat_expansions';

export type FamilyTrackType = SampleTrackType | 'small_variants';

/** A project as the pages list it: from `/projects` (ProjectDashboardOut), by its `_id`. */
export interface ProjectOption {
  id: string;
  name: string;
}

export type StatusTone = 'success' | 'error';

export const EMPTY_PROJECTS: ProjectOption[] = [];

/** Project ids without duplicates, in a stable order, for comparing and saving a selection. */
export const normalizeProjectIds = (projectIds: string[]): string[] =>
  Array.from(new Set(projectIds)).sort((left, right) => left.localeCompare(right));

export const SAMPLE_TRACK_ORDER: SampleTrackType[] = [
  'coverage',
  'segments',
  'apcad',
  'apcad_pcf',
  'haplotype',
  'target_coverage',
  'structural_variants',
  'repeat_expansions',
];

export const FAMILY_TRACK_ORDER: FamilyTrackType[] = [
  'small_variants',
  'structural_variants',
  'repeat_expansions',
  'coverage',
  'segments',
  'apcad',
  'apcad_pcf',
  'haplotype',
  'target_coverage',
];

export const TRACK_LABELS: Record<FamilyTrackType, string> = {
  coverage: 'Coverage bins',
  segments: 'Segments',
  apcad: 'APCAD loci',
  apcad_pcf: 'APCAD PCF segments',
  haplotype: 'Haplotype blocks',
  target_coverage: 'Capture-target coverage',
  structural_variants: 'Structural variants',
  repeat_expansions: 'Repeat expansions',
  small_variants: 'Small variants',
};

export const phenotypeLabel = (affected: boolean): string =>
  affected ? 'Affected' : 'Unaffected';

export const roleLabel = (role: string): string =>
  role === 'proband' ? 'Proband' : role.charAt(0).toUpperCase() + role.slice(1);

export const formatStorageBytes = (value: number): string => {
  if (value <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let normalized = value;
  let index = 0;
  while (normalized >= 1024 && index < units.length - 1) {
    normalized /= 1024;
    index += 1;
  }
  return `${normalized >= 10 || index === 0 ? normalized.toFixed(0) : normalized.toFixed(1)} ${
    units[index]
  }`;
};
