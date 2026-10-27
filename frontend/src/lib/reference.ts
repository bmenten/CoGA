import { useCallback, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import api from './api';
import type { ApiProjectRecord } from './apiTypes';
import { withEntityId } from './entity';

type ProjectReference = ApiProjectRecord;

export interface FamilyReferenceContext {
  speciesName?: string;
  assemblyName?: string;
  assemblyVersion: string;
  assemblyId?: string;
  /** Inside the validated scope; undefined until a linked project has loaded (#515). */
  assemblyValidated?: boolean;
  projectId?: string;
  isLoading: boolean;
  /**
   * The project catalogue could not be loaded, so the reference and its validation scope
   * are unknown. Not "not linked": the family may well be linked, and off scope (#608).
   */
  isError: boolean;
  /** Load the project catalogue again. */
  retry: () => void;
  hasLinkedProject: boolean;
}

export function useProjectCatalog(enabled = true) {
  return useQuery<ProjectReference[]>({
    queryKey: ['projects'],
    enabled,
    staleTime: 5 * 60 * 1000,
    queryFn: async () => {
      const response = await api.get('/projects');
      return (response.data as ProjectReference[]).map((entry) => withEntityId(entry));
    },
  });
}

// T2T-CHM13v2.0 under the names it carries: NCBI's T2T-CHM13v2.0, the `T2T CHM13v2.0` the UCSC
// reference import takes from UCSC's description, the T2T_CHM13v2.0 key of its ClickHouse
// tables, CHM13v2.0, and UCSC's hs1. A version, where the name gives one, is v2.0: hs1's.
const T2T_CHM13_NAME = /^(?:(?:t2t[-_ ]?)?chm13(?:v2(?:\.0)?)?|hs1)$/i;

/**
 * Does an assembly's name denote T2T-CHM13v2.0, UCSC's hs1? In any case and under any of its
 * names, not another CHM13 release nor another telomere-to-telomere genome: the rule the
 * backend reads it by (`sex_chromosomes.is_t2t_chm13`).
 */
export function isT2tChm13(assemblyName?: string | null): boolean {
  return T2T_CHM13_NAME.test((assemblyName ?? '').trim());
}

// GRCh38 and GRCh37 under their GRC and UCSC names, in any case and with or without a patch
// suffix (GRCh38.p14), as the backend reads them (`sex_chromosomes.grc_build`).
const GRC_IGV_GENOMES = new Map([
  ['grch38', 'hg38'],
  ['hg38', 'hg38'],
  ['grch37', 'hg19'],
  ['hg19', 'hg19'],
]);

const OTHER_IGV_GENOMES = new Map([
  ['GRCm39', 'mm39'],
  ['GRCm38', 'mm10'],
  ['EquCab3.0', 'equCab3'],
]);

/**
 * The IGV genome of an assembly, or null where there is none for it.
 *
 * Never another assembly's genome in its place: IGV draws the reads and variants at their
 * positions on the genome it opens. Any name it did not know opened hg38, among them the
 * `T2T CHM13v2.0` the UCSC import gives T2T.
 */
export function mapAssemblyToIgvGenome(assemblyName?: string | null): string | null {
  const name = (assemblyName ?? '').trim();
  const grcGenome = GRC_IGV_GENOMES.get(name.toLowerCase().replace(/\.p\d+$/, ''));
  if (grcGenome) return grcGenome;
  if (isT2tChm13(name)) return 'hs1';
  return OTHER_IGV_GENOMES.get(name) ?? null;
}

export function formatResolvedReferenceLabel(
  reference: {
    speciesName?: string;
    assemblyName?: string;
    assemblyVersion?: string;
    isError?: boolean;
  },
  fallback = 'Reference not linked',
): string {
  if (reference.isError) {
    return 'Reference could not be loaded';
  }
  if (!reference.assemblyName) {
    return fallback;
  }

  const assemblyLabel = `${reference.assemblyName}${reference.assemblyVersion ? ` ${reference.assemblyVersion}` : ''}`;
  return reference.speciesName ? `${reference.speciesName} • ${assemblyLabel}` : assemblyLabel;
}

export function useFamilyReference(
  projectIds?: string[],
  preferredProjectId?: string,
): FamilyReferenceContext {
  const linkedProjectIds = useMemo(
    () => Array.from(new Set((projectIds || []).filter(Boolean))),
    [projectIds],
  );
  const linkedProjectIdSet = useMemo(() => new Set(linkedProjectIds), [linkedProjectIds]);
  const enabled = linkedProjectIds.length > 0;
  const { data: projects = [], isLoading, isError, refetch } = useProjectCatalog(enabled);
  const retry = useCallback(() => {
    void refetch();
  }, [refetch]);

  const project = useMemo(() => {
    if (!enabled || projects.length === 0) {
      return undefined;
    }

    const linkedProjectsById = new Map(
      projects
        .filter((entry) => linkedProjectIdSet.has(entry.id))
        .map((entry) => [entry.id, entry] as const),
    );

    if (preferredProjectId) {
      const preferredProject = linkedProjectsById.get(preferredProjectId);
      if (preferredProject) {
        return preferredProject;
      }
    }

    const linkedProjectId = linkedProjectIds.find((projectId) =>
      linkedProjectsById.has(projectId),
    );
    if (linkedProjectId) {
      return linkedProjectsById.get(linkedProjectId);
    }

    return undefined;
  }, [enabled, linkedProjectIds, linkedProjectIdSet, preferredProjectId, projects]);

  if (!enabled) {
    return {
      speciesName: undefined,
      assemblyName: undefined,
      assemblyVersion: '',
      assemblyId: undefined as string | undefined,
      assemblyValidated: undefined,
      projectId: undefined as string | undefined,
      isLoading: false,
      isError: false,
      retry,
      hasLinkedProject: false,
    };
  }

  return {
    speciesName: project?.species_name || undefined,
    assemblyName: project?.assembly_name || undefined,
    assemblyVersion: project?.assembly_version || '',
    assemblyId: project?.assembly_id,
    assemblyValidated: project ? project.assembly_validated === true : undefined,
    projectId: project?.id,
    isLoading,
    isError,
    retry,
    hasLinkedProject: true,
  };
}
