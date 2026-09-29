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
      return (response.data as any[]).map((entry) => withEntityId(entry)) as ProjectReference[];
    },
  });
}

export function mapAssemblyToIgvGenome(assemblyName?: string): string {
  if (!assemblyName || assemblyName === 'GRCh38') return 'hg38';
  if (assemblyName === 'GRCh37' || assemblyName === 'hg19') return 'hg19';
  if (assemblyName === 'GRCm39') return 'mm39';
  if (assemblyName === 'GRCm38') return 'mm10';
  if (assemblyName.startsWith('T2T-CHM13')) return 'hs1';
  if (assemblyName === 'EquCab3.0') return 'equCab3';
  return 'hg38';
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
