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

export interface SampleData {
  sample_id: string;
  role: string;
  affected: boolean;
  sex: string;
  projects: string[];
  track_counts: Record<SampleTrackType, number>;
  total_records: number;
}

export interface FamilySummaryData {
  family_id: string;
  metadata: Record<string, unknown>;
  projects: string[];
  sample_count: number;
  track_counts: Record<FamilyTrackType, number>;
  total_records: number;
}

export interface FamilyData extends FamilySummaryData {
  samples: SampleData[];
}

export interface ClickHouseVariantTableStatus {
  name: string;
  variant_type: 'small_variants' | 'structural_variants';
  kind: 'table' | 'materialized_view';
  exists: boolean;
  engine: string | null;
  row_count: number;
  bytes_on_disk: number;
  pending_mutations: number;
}

export interface ClickHouseVariantAssemblyStatus {
  assembly_name: string;
  health: 'ready' | 'mutating' | 'missing';
  expected_table_count: number;
  existing_table_count: number;
  missing_tables: string[];
  pending_mutations: number;
  total_rows: number;
  total_bytes_on_disk: number;
  small_variant_rows: number;
  structural_variant_rows: number;
  tables: ClickHouseVariantTableStatus[];
}

export interface ClickHouseVariantAssemblyList {
  assemblies: ClickHouseVariantAssemblyStatus[];
}

export interface ClickHouseVariantTableCheck {
  name: string;
  exists: boolean;
  passed: boolean | null;
  failed_parts: number;
  messages: string[];
}

export interface ClickHouseDetachedPart {
  table: string;
  reason: string;
  count: number;
}

export interface ClickHouseGeneIndexConsistency {
  checked: boolean;
  gene_index_keys: number;
  annotation_index_gene_keys: number;
  consistent: boolean;
  drift: number;
}

export interface ClickHouseVariantIntegrity {
  assembly_name: string;
  status: 'ok' | 'degraded' | 'corrupt' | 'missing';
  table_checks: ClickHouseVariantTableCheck[];
  detached_broken_parts: ClickHouseDetachedPart[];
  gene_index_consistency: ClickHouseGeneIndexConsistency;
  notes: string[];
}

export interface ProjectOption {
  id: string;
  name: string;
}

export interface RawImportFile {
  id: string;
  scope: 'family' | 'individual';
  dataset: string;
  file_name: string;
  file_type: string;
  sample_id: string | null;
  storage_path: string;
  file_size: number | null;
  sha256: string | null;
  source: string;
  created_at: string | null;
  // null for a file kept in an object store: the list does not ask the store; Verify does.
  exists: boolean | null;
  download_available: boolean;
  in_object_store: boolean;
}

export interface FamilyRawFiles {
  family_id: string;
  family_files: RawImportFile[];
  individual_files: RawImportFile[];
}

export type RawFileVerifyStatus =
  | 'verified'
  | 'mismatch'
  | 'missing'
  | 'unverifiable'
  | 'too_large';

export interface RawFileVerifyResult {
  file_id: string;
  status: RawFileVerifyStatus;
  expected_sha256: string | null;
  computed_sha256: string | null;
  message: string;
}

export type StatusTone = 'success' | 'error';

export const EMPTY_PROJECTS: ProjectOption[] = [];

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
