import React, { useEffect, useMemo, useState } from 'react';
import { useParams, Link, useLocation } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import MonarchPhenotypeMatchPanel from './MonarchPhenotypeMatchPanel';
import { useEmbryoSegregation } from '../../lib/useEmbryoSegregation';
import { segregationStateLabel } from '../../lib/embryoSegregation';
import InfoTip from '../../components/InfoTip';
import type {
  ApiFamilyRecord,
  ApiFamilyMemberBatchUpdateItem,
  ApiFamilyMemberBatchUpdateResponse,
  ApiFamilyMemberDeleteResponse,
  ApiFamilyMemberDetail,
  ApiFamilyRegionOfInterest,
  ApiFamilyStatusDefinition,
  ApiHpoAnnotation,
  ApiHpoTerm,
  ApiPaginatedTotalResponse,
  ApiSmallVariantReviewSummary,
  ApiUserRef,
} from '../../lib/apiTypes';
import Pedigree from '../../components/visualizations/Pedigree';
import PageState from '../../components/PageState';
import { formatUserRef } from '../../lib/users';
import { isAdmin } from '../../lib/auth';
import { buildApiUnavailableMessage, getErrorMessage } from '../../lib/errorMessage';
import { sortFamilyMembersProbandFirst } from '../../lib/familyMembers';
import {
  formatResolvedReferenceLabel,
  useFamilyReference,
  useProjectCatalog,
} from '../../lib/reference';
import {
  getTagDefinitionMap,
  sortTagDefinitions,
  type SmallVariantTagDefinition,
} from './smallVariantSearch';
import { getReviewTagStyle } from './smallVariantResultUtils';

interface PedRow {
  fid: string;
  iid: string;
  pid: string;
  mid: string;
  sex: string;
  phen: string;
}

type ClinicalStatus = 'unknown' | 'unaffected' | 'affected';
type CarrierStatus = 'unknown' | 'not_carrier' | 'carrier';
type CarrierType = 'obligate' | 'proven' | 'reported' | 'inferred';
type HpoAnnotationStatus = 'present' | 'absent' | 'unknown';

interface StructureMemberDraft {
  sample_id: string;
  sex: 'male' | 'female' | 'und';
  role: 'proband' | 'father' | 'mother' | 'sibling' | 'embryo' | 'relative';
  clinical_status: ClinicalStatus;
  carrier_status: CarrierStatus;
  carrier_type: CarrierType | '';
  isNew?: boolean;
  removed?: boolean;
}

interface ParentChildDraft {
  id: string;
  parent: string;
  child: string;
  parent_role: 'father' | 'mother' | 'parent';
}

interface CoupleDraft {
  id: string;
  partnerA: string;
  partnerB: string;
  context: string;
}

interface MemberDetailDraft {
  sample_id: string;
  sex: StructureMemberDraft['sex'];
  role: StructureMemberDraft['role'];
  clinical_status: ClinicalStatus;
  carrier_status: CarrierStatus;
  carrier_type: CarrierType | '';
  father_id: string;
  mother_id: string;
}

const ROLE_OPTIONS: StructureMemberDraft['role'][] = [
  'proband',
  'father',
  'mother',
  'sibling',
  'embryo',
  'relative',
];
const SEX_OPTIONS: StructureMemberDraft['sex'][] = ['male', 'female', 'und'];
const CLINICAL_STATUS_OPTIONS: ClinicalStatus[] = ['unknown', 'unaffected', 'affected'];
const CARRIER_STATUS_OPTIONS: CarrierStatus[] = ['unknown', 'not_carrier', 'carrier'];
const CARRIER_TYPE_OPTIONS: CarrierType[] = ['proven', 'obligate', 'reported', 'inferred'];
const HPO_STATUS_OPTIONS: HpoAnnotationStatus[] = ['present', 'absent', 'unknown'];

const defaultNewMember: StructureMemberDraft = {
  sample_id: '',
  sex: 'und',
  role: 'relative',
  clinical_status: 'unknown',
  carrier_status: 'unknown',
  carrier_type: '',
  isNew: true,
};

const parsePedigree = (pedigree?: string | null): PedRow[] => {
  if (!pedigree) return [];
  return pedigree
    .split('\n')
    .filter((line) => line.trim())
    .map((line) => {
      const [fid, iid, pid, mid, sex, phen] = line.trim().split(/\s+/);
      return { fid, iid, pid, mid, sex, phen };
    });
};

const sampleKey = (sampleId: string): string => sampleId.trim().toLowerCase();

const formatHpoTermOption = (term: ApiHpoTerm): string => `${term.hpo_id} ${term.label}`;

const clinicalStatusForMember = (member: {
  clinical_status?: string | null;
  affected?: boolean;
}): ClinicalStatus => {
  if (member.clinical_status === 'affected' || member.clinical_status === 'unaffected') {
    return member.clinical_status;
  }
  if (member.affected) return 'affected';
  return 'unknown';
};

const carrierStatusForMember = (member: {
  carrier_status?: string | boolean | null;
}): CarrierStatus => {
  if (member.carrier_status === 'carrier') return 'carrier';
  if (member.carrier_status === 'not_carrier') return 'not_carrier';
  if (member.carrier_status === true) return 'carrier';
  return 'unknown';
};

const hpoTooltip = (annotation: ApiHpoAnnotation): string => {
  return [
    `${annotation.hpo_id}: ${annotation.definition || annotation.label}`,
    annotation.note ? `Note: ${annotation.note}` : '',
    annotation.evidence ? `Evidence: ${annotation.evidence}` : '',
  ]
    .filter(Boolean)
    .join(' | ');
};

const memberDraftFromFamilyMember = (member: ApiFamilyRecord['members'][number]): StructureMemberDraft => ({
  sample_id: member.sample_id,
  sex: member.sex === 'male' || member.sex === 'female' ? member.sex : 'und',
  role: ROLE_OPTIONS.includes(member.role as StructureMemberDraft['role'])
    ? (member.role as StructureMemberDraft['role'])
    : 'relative',
  clinical_status: clinicalStatusForMember(member),
  carrier_status: carrierStatusForMember(member),
  carrier_type: member.carrier_type ?? '',
  isNew: false,
  removed: member.active === false,
});

const parentChildDraftsFromRelationships = (family: ApiFamilyRecord | undefined): ParentChildDraft[] => {
  const relationships = family?.relationships?.filter(
    (relationship) => relationship.relationship_type === 'parent_child',
  );
  return (relationships || []).map((relationship, index) => ({
    id: relationship.id || `parent-child-${index}`,
    parent: relationship.sample_id_a,
    child: relationship.sample_id_b,
    parent_role:
      relationship.role_a === 'father' || relationship.role_a === 'mother'
        ? relationship.role_a
        : 'parent',
  }));
};

const parentsForSample = (
  family: ApiFamilyRecord | undefined,
  sampleId: string,
): { father_id: string; mother_id: string } => {
  const childKey = sampleKey(sampleId);
  return (family?.relationships || []).reduce(
    (parents, relationship) => {
      if (
        relationship.relationship_type !== 'parent_child' ||
        sampleKey(relationship.sample_id_b) !== childKey
      ) {
        return parents;
      }
      if (relationship.role_a === 'father') {
        parents.father_id = relationship.sample_id_a;
      } else if (relationship.role_a === 'mother') {
        parents.mother_id = relationship.sample_id_a;
      }
      return parents;
    },
    { father_id: '', mother_id: '' },
  );
};

const coupleDraftsFromRelationships = (family: ApiFamilyRecord | undefined): CoupleDraft[] =>
  (family?.relationships || [])
    .filter((relationship) => relationship.relationship_type === 'couple')
    .map((relationship, index) => ({
      id: relationship.id || `couple-${index}`,
      partnerA: relationship.sample_id_a,
      partnerB: relationship.sample_id_b,
      context: typeof relationship.metadata?.context === 'string' ? relationship.metadata.context : '',
    }));

const formatRegion = (roi: ApiFamilyRegionOfInterest): string => {
  const chrom = roi.chr.startsWith('chr') ? roi.chr : `chr${roi.chr}`;
  return `${chrom}:${roi.start.toLocaleString()}-${roi.end.toLocaleString()}`;
};

const getReviewSummaryTags = (
  tagCounts: Record<string, number> | undefined,
  tagDefinitions: SmallVariantTagDefinition[],
) => {
  const counts = tagCounts || {};
  const activeKeys = new Set<string>();
  const knownTags = sortTagDefinitions(tagDefinitions)
    .map((tag) => {
      const count = counts[tag.key] ?? 0;
      if (count <= 0) return null;
      activeKeys.add(tag.key);
      return {
        key: tag.key,
        label: tag.label,
        count,
      };
    })
    .filter((entry): entry is { key: string; label: string; count: number } => entry !== null);
  const unknownTags = Object.entries(counts)
    .filter(([key, count]) => count > 0 && !activeKeys.has(key))
    .sort(([left], [right]) => left.localeCompare(right, undefined, { sensitivity: 'base' }))
    .map(([key, count]) => ({
      key,
      label: key,
      count,
    }));
  return [...knownTags, ...unknownTags];
};

interface FamilyDetailPageProps {
  editable?: boolean;
  /**
   * When provided, render this family instead of reading the id from the route.
   * Used to embed the workspace inside the admin Families page.
   */
  familyId?: string;
  /** Drop the standalone page-shell chrome so the workspace nests inside a card. */
  embedded?: boolean;
}

/**
 * A variant-workspace navigation control. Renders a link only when its data type
 * is available; when there is no underlying data the control is omitted entirely
 * so the family page surfaces only the workspaces this family actually has.
 */
const VariantWorkspaceLink: React.FC<{
  active: boolean;
  to: string;
  className: string;
  children: React.ReactNode;
}> = ({ active, to, className, children }) =>
  active ? (
    <Link to={to} className={`${className} hover:no-underline`}>
      {children}
    </Link>
  ) : null;

const FamilyDetailPage: React.FC<FamilyDetailPageProps> = ({
  editable = false,
  familyId: familyIdProp,
  embedded = false,
}) => {
  const { familyId: familyIdParam } = useParams<{ familyId: string }>();
  const familyId = familyIdProp ?? familyIdParam;
  const location = useLocation();
  const preferredProjectId = useMemo(
    () => new URLSearchParams(location.search).get('project_id') || undefined,
    [location.search],
  );
  const userIsAdmin = isAdmin();
  const canEditFamilyDetails = editable && userIsAdmin;
  // ROI editing is a standalone admin action (PUT /families/{id}/roi is
  // admin-only) and is independent of the family-structure editor, so it stays
  // available from the family dashboard for admins regardless of `editable`.
  const canEditRoi = userIsAdmin;
  const queryClient = useQueryClient();
  const [roiInput, setRoiInput] = useState('');
  const [roiBusy, setRoiBusy] = useState(false);
  const [roiStatus, setRoiStatus] = useState<{ tone: 'success' | 'error'; message: string } | null>(
    null,
  );
  const [structureMembers, setStructureMembers] = useState<StructureMemberDraft[]>([]);
  const [parentChildDrafts, setParentChildDrafts] = useState<ParentChildDraft[]>([]);
  const [coupleDrafts, setCoupleDrafts] = useState<CoupleDraft[]>([]);
  const [newMember, setNewMember] = useState<StructureMemberDraft>(defaultNewMember);
  const [newMemberRelations, setNewMemberRelations] = useState({ father: '', mother: '', partner: '' });
  const [structureBusy, setStructureBusy] = useState(false);
  const [structureStatus, setStructureStatus] = useState<{
    tone: 'success' | 'error';
    message: string;
  } | null>(null);
  const [selectedMemberId, setSelectedMemberId] = useState<string | null>(null);
  const [memberDraft, setMemberDraft] = useState<MemberDetailDraft | null>(null);
  const [memberBusy, setMemberBusy] = useState(false);
  const [pendingMemberUpdates, setPendingMemberUpdates] = useState<Record<string, MemberDetailDraft>>({});
  const [pendingMembersBusy, setPendingMembersBusy] = useState(false);
  const [pendingMembersStatus, setPendingMembersStatus] = useState<{
    tone: 'success' | 'error';
    message: string;
  } | null>(null);
  const [memberStatus, setMemberStatus] = useState<{ tone: 'success' | 'error'; message: string } | null>(null);
  const [hpoSearchInput, setHpoSearchInput] = useState('');
  const [selectedHpoTerm, setSelectedHpoTerm] = useState<ApiHpoTerm | null>(null);
  const [hpoAnnotationStatus, setHpoAnnotationStatus] = useState<HpoAnnotationStatus>('present');
  const [hpoNote, setHpoNote] = useState('');
  const [hpoBusy, setHpoBusy] = useState(false);
  const [hpoStatus, setHpoStatus] = useState<{ tone: 'success' | 'error'; message: string } | null>(null);
  const [statusDraft, setStatusDraft] = useState('');
  const [assignedToDraft, setAssignedToDraft] = useState('');
  const [reviewedByDraft, setReviewedByDraft] = useState('');
  const [metadataBusy, setMetadataBusy] = useState(false);
  const [metadataStatus, setMetadataStatus] = useState<{
    tone: 'success' | 'error';
    message: string;
  } | null>(null);

  const { data, isLoading } = useQuery<ApiFamilyRecord>({
    queryKey: ['family', familyId],
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}`);
      return res.data as ApiFamilyRecord;
    },
  });

  // Active status catalogue + assignable users power the status / assignment
  // pickers below. Both are small, app-wide lookups so they are cached by a
  // stable key and shared across family pages.
  const { data: familyStatusOptions = [] } = useQuery<ApiFamilyStatusDefinition[]>({
    queryKey: ['family-statuses'],
    queryFn: async () => {
      const res = await api.get('/family-statuses');
      return res.data as ApiFamilyStatusDefinition[];
    },
  });

  const { data: assignableUsers = [] } = useQuery<ApiUserRef[]>({
    queryKey: ['assignable-users'],
    queryFn: async () => {
      const res = await api.get('/users');
      return res.data as ApiUserRef[];
    },
  });

  const {
    assemblyName,
    assemblyVersion,
    projectId,
    isLoading: referenceLoading,
  } = useFamilyReference(data?.projects, preferredProjectId);
  const variantCountsReady = Boolean(
    familyId && data && (!(data.projects?.length) || projectId),
  );

  // Presence-only fetches: count_only returns a lightweight presence flag instead
  // of the full small/structural/repeat/Paraphase/mtDNA payloads. For small and
  // structural variants this skips the bounded row count + summary aggregation
  // (which scan the whole family) — the dashboard only needs total > 0.
  const buildPresenceParams = () => {
    const params = new URLSearchParams({ count_only: 'true' });
    if (projectId) {
      params.set('project_id', projectId);
    }
    return `?${params.toString()}`;
  };

  const { data: variantPage } = useQuery<ApiPaginatedTotalResponse>({
    queryKey: ['family', familyId, 'structural-variants', 'has-data', projectId || null],
    enabled: variantCountsReady,
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}/structural-variants${buildPresenceParams()}`);
      return res.data as ApiPaginatedTotalResponse;
    },
  });

  const { data: familyVariantPage } = useQuery<ApiPaginatedTotalResponse>({
    queryKey: ['family', familyId, 'small-variants', 'has-data', projectId || null],
    enabled: variantCountsReady,
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}/small-variants${buildPresenceParams()}`);
      return res.data as ApiPaginatedTotalResponse;
    },
  });

  const { data: repeatTable } = useQuery<{ loci_count?: number }>({
    queryKey: ['family', familyId, 'repeat-expansions', 'has-data', projectId || null],
    enabled: variantCountsReady,
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}/repeat-expansions${buildPresenceParams()}`);
      return res.data as { loci_count?: number };
    },
  });

  const { data: paraphaseTable } = useQuery<{ genes_count?: number }>({
    queryKey: ['family', familyId, 'paraphase', 'has-data', projectId || null],
    enabled: variantCountsReady,
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}/paraphase${buildPresenceParams()}`);
      return res.data as { genes_count?: number };
    },
  });

  const { data: mitoTable } = useQuery<{ variant_count?: number; has_coverage?: boolean }>({
    queryKey: ['family', familyId, 'mitochondrial-dna', 'has-data', projectId || null],
    enabled: variantCountsReady,
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}/mitochondrial-dna${buildPresenceParams()}`);
      return res.data as { variant_count?: number; has_coverage?: boolean };
    },
  });

  const hasVariants = (variantPage?.total ?? 0) > 0;
  const hasSmallVariants = (familyVariantPage?.total ?? 0) > 0;
  const hasRepeatExpansions = (repeatTable?.loci_count ?? 0) > 0;
  const hasParaphase = (paraphaseTable?.genes_count ?? 0) > 0;
  // mtDNA data is present when there are chrM variants or any sample carries
  // mtDNA coverage (coverage-only families still have something to show).
  const hasMitoDna = (mitoTable?.variant_count ?? 0) > 0 || (mitoTable?.has_coverage ?? false);
  const hasVariantSummary = hasVariants || hasSmallVariants;
  const hasAnyVariantData =
    hasVariants || hasSmallVariants || hasRepeatExpansions || hasParaphase || hasMitoDna;
  const variantCountsLoaded =
    variantCountsReady &&
    variantPage !== undefined &&
    familyVariantPage !== undefined &&
    repeatTable !== undefined &&
    paraphaseTable !== undefined &&
    mitoTable !== undefined;
  // Monogenic NIPT families surface a dedicated analysis tab (see docs/monogenic-nipt.md).
  const isMonogenicNipt = data?.metadata?.analysis_type === 'monogenic_nipt';
  const { data: projects = [] } = useProjectCatalog();
  const assemblyLabel = formatResolvedReferenceLabel(
    { assemblyName, assemblyVersion },
    data?.projects?.length && referenceLoading ? 'Loading linked reference...' : 'Not linked',
  );
  const { data: reviewSummary } = useQuery<ApiSmallVariantReviewSummary>({
    queryKey: ['family', familyId, 'small-variant-review-summary'],
    enabled: Boolean(familyId && hasSmallVariants),
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}/small-variant-review-summary`);
      return res.data as ApiSmallVariantReviewSummary;
    },
  });
  const { data: smallVariantTags = [] } = useQuery<SmallVariantTagDefinition[]>({
    queryKey: ['family', familyId, 'small-variant-tags', 'summary', projectId || null],
    enabled: Boolean(familyId && hasSmallVariants),
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}/small-variant-tags`, {
        params: projectId ? { project_id: projectId } : undefined,
      });
      return res.data as SmallVariantTagDefinition[];
    },
  });
  const { data: structuralReviewSummary } = useQuery<ApiSmallVariantReviewSummary>({
    queryKey: ['family', familyId, 'structural-variant-review-summary'],
    enabled: Boolean(familyId && hasVariants),
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}/structural-variant-review-summary`);
      return res.data as ApiSmallVariantReviewSummary;
    },
  });
  const { data: structuralVariantTags = [] } = useQuery<SmallVariantTagDefinition[]>({
    queryKey: ['family', familyId, 'structural-variant-tags', 'summary', projectId || null],
    enabled: Boolean(familyId && hasVariants),
    queryFn: async () => {
      // Structural and small-variant tags share the same store; there is no
      // separate structural endpoint.
      const res = await api.get(`/families/${familyId}/small-variant-tags`, {
        params: projectId ? { project_id: projectId } : undefined,
      });
      return res.data as SmallVariantTagDefinition[];
    },
  });
  const { data: hpoAnnotations = [] } = useQuery<ApiHpoAnnotation[]>({
    queryKey: ['family', familyId, 'hpo'],
    enabled: Boolean(familyId),
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}/hpo`);
      return res.data as ApiHpoAnnotation[];
    },
  });
  const hpoSearchQuery = hpoSearchInput.trim();
  const { data: hpoSearchResults = [] } = useQuery<ApiHpoTerm[]>({
    queryKey: ['hpo-search', hpoSearchQuery],
    enabled: hpoSearchQuery.length >= 2,
    queryFn: async () => {
      const res = await api.get('/hpo/search', { //
        params: { q: hpoSearchQuery, limit: 20 },
      });
      return res.data as ApiHpoTerm[];
    },
  });
  const { data: selectedMemberDetail } = useQuery<ApiFamilyMemberDetail>({
    queryKey: ['family', familyId, 'member', selectedMemberId],
    enabled: Boolean(familyId && selectedMemberId),
    queryFn: async () => {
      const res = await api.get(
        `/families/${familyId}/members/${encodeURIComponent(selectedMemberId!)}`,
      );
      return res.data as ApiFamilyMemberDetail;
    },
  });

  const familyProjects = useMemo(() => {
    if (!data?.projects?.length) return [];
    const projectNameById = new Map(projects.map((project) => [project.id, project.name]));
    return data.projects.map((projectIdValue) => ({
      id: projectIdValue,
      name: projectNameById.get(projectIdValue) ?? projectIdValue,
    }));
  }, [data?.projects, projects]);
  const smallVariantReviewTagMap = useMemo(() => getTagDefinitionMap(smallVariantTags), [smallVariantTags]);
  const structuralVariantReviewTagMap = useMemo(
    () => getTagDefinitionMap(structuralVariantTags),
    [structuralVariantTags],
  );
  const reviewSummaryTags = useMemo(() => {
    return getReviewSummaryTags(reviewSummary?.tag_counts, smallVariantTags);
  }, [reviewSummary?.tag_counts, smallVariantTags]);
  const structuralReviewSummaryTags = useMemo(() => {
    return getReviewSummaryTags(structuralReviewSummary?.tag_counts, structuralVariantTags);
  }, [structuralReviewSummary?.tag_counts, structuralVariantTags]);
  const variantPageQuerySuffix = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
  const pedRows = useMemo(() => parsePedigree(data?.pedigree), [data?.pedigree]);
  const orderedMembers = useMemo(
    () => sortFamilyMembersProbandFirst(data?.members || []),
    [data?.members],
  );
  // Derived embryo segregation at the ROI (carrier/affected/unaffected), shown per
  // embryo in the Family members table below. Derived from the haplotype analysis.
  const { byEmbryo: embryoSegregation } = useEmbryoSegregation({
    familyId: data?.family_id ?? '',
    roi: data?.roi ?? null,
    members: data?.members ?? [],
    inheritanceModel: (data?.metadata?.pgt as { inheritance_model?: string } | undefined)
      ?.inheritance_model,
  });
  const hpoAnnotationsBySample = useMemo(() => {
    return hpoAnnotations.reduce<Record<string, ApiHpoAnnotation[]>>((acc, annotation) => {
      acc[annotation.sample_id] = acc[annotation.sample_id] || [];
      acc[annotation.sample_id].push(annotation);
      return acc;
    }, {});
  }, [hpoAnnotations]);
  const pendingMemberCount = Object.keys(pendingMemberUpdates).length;
  const memberViewWithPending = (
    member: ApiFamilyRecord['members'][number],
  ): ApiFamilyRecord['members'][number] => {
    const pending = pendingMemberUpdates[member.sample_id];
    if (!pending) return member;
    return {
      ...member,
      sample_id: pending.sample_id || member.sample_id,
      sex: pending.sex,
      role: pending.role,
      affected: pending.clinical_status === 'affected',
      clinical_status: pending.clinical_status,
      carrier_status: pending.carrier_status,
      carrier_type: pending.carrier_status === 'carrier' ? pending.carrier_type || null : null,
    };
  };
  const phenotypeSampleIds = useMemo(
    () => Object.keys(hpoAnnotationsBySample),
    [hpoAnnotationsBySample],
  );

  useEffect(() => {
    setRoiInput(data?.roi?.query ?? '');
  }, [data?.roi?.query]);

  useEffect(() => {
    setStatusDraft(data?.status?.key ?? '');
    setAssignedToDraft(data?.assigned_to?.id ?? '');
    setReviewedByDraft(data?.reviewed_by?.id ?? '');
    setMetadataStatus(null);
  }, [data?.status?.key, data?.assigned_to?.id, data?.reviewed_by?.id]);

  useEffect(() => {
    setPendingMemberUpdates({});
    setSelectedMemberId(null);
  }, [familyId]);

  useEffect(() => {
    if (!data) {
      setStructureMembers([]);
      setParentChildDrafts([]);
      setCoupleDrafts([]);
      return;
    }
    setStructureMembers(data.members.map(memberDraftFromFamilyMember));
    setParentChildDrafts(parentChildDraftsFromRelationships(data));
    setCoupleDrafts(coupleDraftsFromRelationships(data));
    setNewMember(defaultNewMember);
    setNewMemberRelations({ father: '', mother: '', partner: '' });
  }, [data]);

  useEffect(() => {
    if (!selectedMemberDetail) {
      setMemberDraft(null);
      return;
    }
    const pending = pendingMemberUpdates[selectedMemberDetail.member.sample_id];
    if (pending) {
      setMemberDraft(pending);
      return;
    }
    setMemberDraft({
      sample_id: selectedMemberDetail.member.sample_id,
      sex:
        selectedMemberDetail.member.sex === 'male' || selectedMemberDetail.member.sex === 'female'
          ? selectedMemberDetail.member.sex
          : 'und',
      role: ROLE_OPTIONS.includes(selectedMemberDetail.member.role as StructureMemberDraft['role'])
        ? (selectedMemberDetail.member.role as StructureMemberDraft['role'])
        : 'relative',
      clinical_status: clinicalStatusForMember(selectedMemberDetail.member),
      carrier_status: carrierStatusForMember(selectedMemberDetail.member),
      carrier_type: selectedMemberDetail.member.carrier_type ?? '',
      father_id: selectedMemberDetail.father_id ?? '',
      mother_id: selectedMemberDetail.mother_id ?? '',
    });
    setMemberStatus(null);
    setHpoStatus(null);
    setHpoSearchInput('');
    setSelectedHpoTerm(null);
    setHpoNote('');
    setHpoAnnotationStatus('present');
  }, [pendingMemberUpdates, selectedMemberDetail]);

  useEffect(() => {
    if (selectedMemberId && !orderedMembers.some((member) => member.sample_id === selectedMemberId)) {
      setSelectedMemberId(null);
    }
  }, [orderedMembers, selectedMemberId]);

  if (isLoading) {
    return (
      <PageState
        kicker="Family"
        title="Loading family workspace"
        message="Preparing family metadata, pedigree and navigation links."
      />
    );
  }

  if (!data) {
    return (
      <PageState
        kicker="Family"
        title="Family not found"
        message="This workspace could not resolve the requested family."
      />
    );
  }

  const saveRoi = async (clear = false) => {
    if (!familyId) return;
    setRoiBusy(true);
    setRoiStatus(null);
    try {
      const response = await api.put(`/families/${familyId}/roi`, {
        query: clear ? '' : roiInput,
        project_id: projectId,
      });
      const updatedFamily = response.data as ApiFamilyRecord;
      queryClient.setQueryData(['family', familyId], updatedFamily);
      await queryClient.invalidateQueries({ queryKey: ['families'] });
      setRoiInput(updatedFamily.roi?.query ?? '');
      setRoiStatus({
        tone: 'success',
        message: clear
          ? 'Family ROI cleared.'
          : `Family ROI saved for ${updatedFamily.roi?.label ?? 'the selected locus'}.`,
      });
    } catch (error) {
      setRoiStatus({
        tone: 'error',
        message: getErrorMessage(error, 'Failed to update the family ROI.'),
      });
    } finally {
      setRoiBusy(false);
    }
  };

  // Each picker auto-saves the single field it changed (the backend applies a
  // partial update). On failure the pickers roll back to the stored values.
  const saveMetadata = async (patch: {
    status_key?: string | null;
    assigned_to?: string | null;
    reviewed_by?: string | null;
  }) => {
    if (!familyId) return;
    setMetadataBusy(true);
    setMetadataStatus(null);
    try {
      const response = await api.put(`/families/${familyId}/metadata`, patch);
      const updatedFamily = response.data as ApiFamilyRecord;
      queryClient.setQueryData(['family', familyId], updatedFamily);
      // The dashboard's nested table is fed by the project catalog (['projects']),
      // while the unassigned-families table uses ['families'] — refresh both.
      await queryClient.invalidateQueries({ queryKey: ['families'] });
      await queryClient.invalidateQueries({ queryKey: ['projects'] });
    } catch (error) {
      setStatusDraft(data?.status?.key ?? '');
      setAssignedToDraft(data?.assigned_to?.id ?? '');
      setReviewedByDraft(data?.reviewed_by?.id ?? '');
      setMetadataStatus({
        tone: 'error',
        message: getErrorMessage(error, 'Failed to update the family status.'),
      });
    } finally {
      setMetadataBusy(false);
    }
  };

  const activeStructureMembers = structureMembers.filter((member) => !member.removed);
  const activeSampleIds = activeStructureMembers.map((member) => member.sample_id);
  const parentLabelByChild = parentChildDrafts.reduce<Record<string, { father?: string; mother?: string }>>(
    (acc, relationship) => {
      if (!relationship.parent || !relationship.child) return acc;
      const childKey = sampleKey(relationship.child);
      acc[childKey] = acc[childKey] || {};
      if (relationship.parent_role === 'mother') {
        acc[childKey].mother = relationship.parent;
      } else if (relationship.parent_role === 'father') {
        acc[childKey].father = relationship.parent;
      }
      return acc;
    },
    {},
  );

  // Per-member relationship editing folded into the roster: father/mother are the
  // parent_child drafts where this member is the child; partner is the couple draft
  // involving this member. These read/write the same drafts saveStructure persists.
  const parentValueFor = (childId: string, role: 'father' | 'mother'): string => {
    const draft = parentChildDrafts.find(
      (relationship) =>
        sampleKey(relationship.child) === sampleKey(childId) && relationship.parent_role === role,
    );
    return draft?.parent || '';
  };

  const setParentValue = (childId: string, role: 'father' | 'mother', parentId: string) => {
    setParentChildDrafts((relationships) => {
      const filtered = relationships.filter(
        (relationship) =>
          !(sampleKey(relationship.child) === sampleKey(childId) && relationship.parent_role === role),
      );
      if (!parentId) return filtered;
      return [
        ...filtered,
        {
          id: `pc-${role}-${childId}-${Date.now()}`,
          parent: parentId,
          child: childId,
          parent_role: role,
        },
      ];
    });
  };

  const partnerValueFor = (memberId: string): string => {
    const draft = coupleDrafts.find(
      (relationship) =>
        sampleKey(relationship.partnerA) === sampleKey(memberId) ||
        sampleKey(relationship.partnerB) === sampleKey(memberId),
    );
    if (!draft) return '';
    return sampleKey(draft.partnerA) === sampleKey(memberId) ? draft.partnerB : draft.partnerA;
  };

  const setPartnerValue = (memberId: string, partnerId: string) => {
    setCoupleDrafts((relationships) => {
      // Each person keeps at most one partner, so drop any couple involving either
      // side before linking them.
      const filtered = relationships.filter(
        (relationship) =>
          ![relationship.partnerA, relationship.partnerB].some(
            (partner) => sampleKey(partner) === sampleKey(memberId) || sampleKey(partner) === sampleKey(partnerId),
          ),
      );
      if (!partnerId) return filtered;
      return [
        ...filtered,
        { id: `couple-${memberId}-${Date.now()}`, partnerA: memberId, partnerB: partnerId, context: '' },
      ];
    });
  };

  const updateStructureMember = (
    sampleId: string,
    updates: Partial<StructureMemberDraft>,
  ) => {
    setStructureMembers((members) =>
      members.map((member) =>
        member.sample_id === sampleId
          ? {
              ...member,
              ...updates,
              carrier_type:
                updates.carrier_status && updates.carrier_status !== 'carrier'
                  ? ''
                  : updates.carrier_type ?? member.carrier_type,
            }
          : member,
      ),
    );
  };

  const removeStructureMember = (sampleId: string) => {
    setStructureMembers((members) =>
      members.flatMap((member) => {
        if (member.sample_id !== sampleId) return [member];
        return member.isNew ? [] : [{ ...member, removed: true }];
      }),
    );
    setParentChildDrafts((relationships) =>
      relationships.filter(
        (relationship) => relationship.parent !== sampleId && relationship.child !== sampleId,
      ),
    );
    setCoupleDrafts((relationships) =>
      relationships.filter(
        (relationship) => relationship.partnerA !== sampleId && relationship.partnerB !== sampleId,
      ),
    );
  };

  const addStructureMember = () => {
    const sampleId = newMember.sample_id.trim();
    if (!sampleId) {
      setStructureStatus({ tone: 'error', message: 'Sample ID is required.' });
      return;
    }
    if (structureMembers.some((member) => sampleKey(member.sample_id) === sampleKey(sampleId))) {
      setStructureStatus({ tone: 'error', message: `Member ${sampleId} already exists.` });
      return;
    }
    setStructureMembers((members) => [
      ...members,
      {
        ...newMember,
        sample_id: sampleId,
        carrier_type: newMember.carrier_status === 'carrier' ? newMember.carrier_type : '',
        isNew: true,
      },
    ]);
    // Persist the relationships chosen inline on the add-member row.
    if (newMemberRelations.father) setParentValue(sampleId, 'father', newMemberRelations.father);
    if (newMemberRelations.mother) setParentValue(sampleId, 'mother', newMemberRelations.mother);
    if (newMemberRelations.partner) setPartnerValue(sampleId, newMemberRelations.partner);
    setNewMember(defaultNewMember);
    setNewMemberRelations({ father: '', mother: '', partner: '' });
    setStructureStatus(null);
  };

  const saveStructure = async () => {
    if (!familyId || !data) return;
    setStructureBusy(true);
    setStructureStatus(null);
    try {
      const removedExisting = structureMembers
        .filter((member) => member.removed && !member.isNew)
        .map((member) => member.sample_id);
      const addMembers = structureMembers
        .filter((member) => member.isNew && !member.removed)
        .map((member) => ({
          sample_id: member.sample_id,
          sex: member.sex,
          role: member.role,
          clinical_status: member.clinical_status,
          carrier_status: member.carrier_status,
          carrier_type: member.carrier_status === 'carrier' ? member.carrier_type || null : null,
          carrier_evidence: {},
        }));
      const members = structureMembers
        .filter((member) => !member.isNew)
        .map((member) => ({
          sample_id: member.sample_id,
          sex: member.sex,
          role: member.role,
          clinical_status: member.clinical_status,
          carrier_status: member.carrier_status,
          carrier_type: member.carrier_status === 'carrier' ? member.carrier_type || null : null,
          carrier_evidence: {},
          active: !member.removed,
        }));
      const activeDraftIds = new Set(activeSampleIds);
      const parentChild = parentChildDrafts
        .filter(
          (relationship) =>
            relationship.parent &&
            relationship.child &&
            activeDraftIds.has(relationship.parent) &&
            activeDraftIds.has(relationship.child),
        )
        .map((relationship) => ({
          parent: relationship.parent,
          child: relationship.child,
          parent_role: relationship.parent_role,
          metadata: {},
        }));
      const couples = coupleDrafts
        .filter(
          (relationship) =>
            relationship.partnerA &&
            relationship.partnerB &&
            activeDraftIds.has(relationship.partnerA) &&
            activeDraftIds.has(relationship.partnerB),
        )
        .map((relationship) => ({
          partners: [relationship.partnerA, relationship.partnerB],
          context: relationship.context.trim() || null,
          metadata: {},
        }));
      const response = await api.put(`/families/${familyId}/structure`, {
        expected_structure_version: data.structure_version?.version ?? null,
        change_reason: 'family_detail_page',
        clear_existing_genomic_data: false,
        add_members: addMembers,
        members,
        remove_members: removedExisting,
        relationships: {
          parent_child: parentChild,
          couples,
        },
      });
      const payload = response.data as {
        family: ApiFamilyRecord;
        warnings?: string[];
        cleared_data_counts?: Record<string, number>;
      };
      queryClient.setQueryData(['family', familyId], payload.family);
      await queryClient.invalidateQueries({ queryKey: ['families'] });
      setStructureStatus({
        tone: 'success',
        message: payload.warnings?.length
          ? `Family structure saved. ${payload.warnings.join(' ')}`
          : 'Family structure saved.',
      });
    } catch (error) {
      setStructureStatus({
        tone: 'error',
        message: getErrorMessage(error, 'Failed to update the family structure.'),
      });
    } finally {
      setStructureBusy(false);
    }
  };

  const updateHpoSearchInput = (value: string) => {
    setHpoSearchInput(value);
    const normalizedValue = value.trim().toLowerCase();
    const matchedTerm = hpoSearchResults.find((term) => {
      return (
        term.hpo_id.toLowerCase() === normalizedValue ||
        formatHpoTermOption(term).toLowerCase() === normalizedValue
      );
    });
    setSelectedHpoTerm(matchedTerm ?? null);
  };

  const openMemberDetail = (sampleId: string) => {
    setSelectedMemberId(sampleId);
  };

  const closeMemberDetail = () => {
    setSelectedMemberId(null);
    setMemberDraft(null);
    setMemberStatus(null);
    setHpoStatus(null);
    setHpoSearchInput('');
    setSelectedHpoTerm(null);
    setHpoNote('');
  };

  const applyMemberDetail = () => {
    if (!selectedMemberDetail || !memberDraft) return;
    setPendingMembersStatus(null);
    setPendingMemberUpdates((updates) => ({
      ...updates,
      [selectedMemberDetail.member.sample_id]: {
        ...memberDraft,
        sample_id: memberDraft.sample_id.trim() || selectedMemberDetail.member.sample_id,
      },
    }));
    setMemberStatus({
      tone: 'success',
      message: 'Member changes are pending. Save pending updates to commit them together.',
    });
  };

  const savePendingMemberUpdates = async () => {
    if (!familyId || !data || pendingMemberCount === 0) return;
    setPendingMembersBusy(true);
    setPendingMembersStatus(null);
    try {
      const membersByKey = new Map(data.members.map((member) => [sampleKey(member.sample_id), member]));
      const updates: ApiFamilyMemberBatchUpdateItem[] = Object.entries(pendingMemberUpdates)
        .map(([originalSampleId, draft]) => {
          const currentMember = membersByKey.get(sampleKey(originalSampleId));
          const currentParents = parentsForSample(data, originalSampleId);
          const nextSampleId = draft.sample_id.trim();
          const item: ApiFamilyMemberBatchUpdateItem = { sample_id: originalSampleId };

          if (nextSampleId && sampleKey(nextSampleId) !== sampleKey(originalSampleId)) {
            item.new_sample_id = nextSampleId;
          }
          if (currentMember && draft.sex !== currentMember.sex) {
            item.sex = draft.sex;
          }
          if (currentMember && draft.role !== currentMember.role) {
            item.role = draft.role;
          }
          if (currentMember && draft.clinical_status !== clinicalStatusForMember(currentMember)) {
            item.clinical_status = draft.clinical_status;
          }
          if (currentMember && draft.carrier_status !== carrierStatusForMember(currentMember)) {
            item.carrier_status = draft.carrier_status;
            item.carrier_type =
              draft.carrier_status === 'carrier' ? draft.carrier_type || null : null;
          } else if (
            currentMember &&
            draft.carrier_status === 'carrier' &&
            draft.carrier_type !== (currentMember.carrier_type ?? '')
          ) {
            item.carrier_type = draft.carrier_type || null;
          }
          if (sampleKey(draft.father_id) !== sampleKey(currentParents.father_id)) {
            item.father_id = draft.father_id.trim() || null;
          }
          if (sampleKey(draft.mother_id) !== sampleKey(currentParents.mother_id)) {
            item.mother_id = draft.mother_id.trim() || null;
          }
          return Object.keys(item).length > 1 ? item : null;
        })
        .filter((item): item is ApiFamilyMemberBatchUpdateItem => item !== null);
      if (updates.length === 0) {
        setPendingMemberUpdates({});
        setPendingMembersStatus({
          tone: 'success',
          message: 'No metadata changes to save.',
        });
        return;
      }
      const response = await api.put(
        `/families/${familyId}/members/batch`,
        {
          expected_structure_version: data.structure_version?.version ?? null,
          change_reason: 'family_member_batch_update',
          updates,
        },
      );
      const payload = response.data as ApiFamilyMemberBatchUpdateResponse;
      queryClient.setQueryData(['family', familyId], payload.family);
      setPendingMemberUpdates({});
      await queryClient.invalidateQueries({ queryKey: ['family', familyId, 'hpo'] });
      setPendingMembersStatus({
        tone: 'success',
        message: payload.warnings?.length
          ? `Pending updates saved. ${payload.warnings.join(' ')}`
          : 'Pending updates saved.',
      });
    } catch (error) {
      setPendingMembersStatus({
        tone: 'error',
        message: getErrorMessage(error, 'Failed to save pending member updates.', {
          networkFallback: buildApiUnavailableMessage(api.defaults.baseURL),
        }),
      });
    } finally {
      setPendingMembersBusy(false);
    }
  };

  const deleteMemberDetail = async () => {
    if (!familyId || !selectedMemberDetail) return;
    const impactWarnings = selectedMemberDetail.impact.warnings.join('\n');
    const confirmed = window.confirm(
      [
        `Remove ${selectedMemberDetail.member.sample_id} from the active family?`,
        impactWarnings,
        'This can make derived analyses stale.',
      ]
        .filter(Boolean)
        .join('\n\n'),
    );
    if (!confirmed) return;
    setMemberBusy(true);
    setMemberStatus(null);
    try {
      const response = await api.delete(
        `/families/${familyId}/members/${encodeURIComponent(selectedMemberDetail.member.sample_id)}`,
        {
          params: {
            confirm: true,
          },
        },
      );
      const payload = response.data as ApiFamilyMemberDeleteResponse;
      queryClient.setQueryData(['family', familyId], payload.family);
      await queryClient.invalidateQueries({ queryKey: ['families'] });
      await queryClient.invalidateQueries({ queryKey: ['family', familyId, 'hpo'] });
      closeMemberDetail();
    } catch (error) {
      setMemberStatus({
        tone: 'error',
        message: getErrorMessage(error, 'Failed to remove family member.'),
      });
    } finally {
      setMemberBusy(false);
    }
  };

  const addHpoAnnotation = async () => {
    if (!familyId || !selectedHpoTerm || !selectedMemberDetail) {
      setHpoStatus({ tone: 'error', message: 'Select a family member and HPO term.' });
      return;
    }
    setHpoBusy(true);
    setHpoStatus(null);
    try {
      await api.post(
        `/families/${familyId}/members/${encodeURIComponent(selectedMemberDetail.member.sample_id)}/hpo`,
        {
          hpo_id: selectedHpoTerm.hpo_id,
          status: hpoAnnotationStatus,
          source: 'manual',
          note: hpoNote.trim() || null,
        },
      );
      await queryClient.invalidateQueries({ queryKey: ['family', familyId, 'hpo'] });
      await queryClient.invalidateQueries({
        queryKey: ['family', familyId, 'member', selectedMemberDetail.member.sample_id],
      });
      setHpoNote('');
      setHpoSearchInput('');
      setSelectedHpoTerm(null);
      setHpoStatus({ tone: 'success', message: 'Phenotype annotation saved.' });
    } catch (error) {
      setHpoStatus({
        tone: 'error',
        message: getErrorMessage(error, 'Failed to save phenotype annotation.'),
      });
    } finally {
      setHpoBusy(false);
    }
  };

  const removeHpoAnnotation = async (annotationId: string) => {
    if (!familyId) return;
    setHpoBusy(true);
    setHpoStatus(null);
    try {
      await api.delete(`/families/${familyId}/hpo/${annotationId}`);
      await queryClient.invalidateQueries({ queryKey: ['family', familyId, 'hpo'] });
      await queryClient.invalidateQueries({ queryKey: ['family', familyId, 'member'] });
      setHpoStatus({ tone: 'success', message: 'Phenotype annotation removed.' });
    } catch (error) {
      setHpoStatus({
        tone: 'error',
        message: getErrorMessage(error, 'Failed to remove phenotype annotation.'),
      });
    } finally {
      setHpoBusy(false);
    }
  };

  return (
    <div
      className={`family-detail-page space-y-6${embedded ? '' : ' page-shell'}`}
    >
      <section className="surface-card page-top-card">
        <div className={`page-top-card-grid${pedRows.length ? ' page-top-card-grid--with-visual' : ''}`}>
          <div className="page-top-card-copy family-workspace-copy">
            <div className="space-y-1">
              <p className="page-kicker">Family Workspace</p>
              <h1 className="catalog-card-title">Family {data.family_id}</h1>
            </div>
            <div className="family-workspace-summary">
              <div className="family-workspace-stat">
                <span className="stat-label">Members</span>
                <strong className="family-workspace-stat-value">{orderedMembers.length}</strong>
              </div>
              <div className="family-workspace-stat">
                <span className="stat-label">Assembly</span>
                <strong className="family-workspace-stat-copy">{assemblyLabel}</strong>
              </div>
              <div className="family-workspace-stat">
                <span className="stat-label">Projects</span>
                {familyProjects.length > 0 ? (
                  <div className="family-workspace-projects">
                    {familyProjects.map((project) => (
                      <span key={project.id} className="table-chip">
                        {project.name}
                      </span>
                    ))}
                  </div>
                ) : (
                  <strong className="family-workspace-stat-copy">Not linked</strong>
                )}
              </div>
            </div>
            <div className="family-workspace-meta-row">
              <div className="family-workspace-meta-field">
                <span className="stat-label">Status</span>
                <select
                  className="family-workspace-select"
                  aria-label="Family status"
                  value={statusDraft}
                  onChange={(event) => {
                    const value = event.target.value;
                    setStatusDraft(value);
                    saveMetadata({ status_key: value || null });
                  }}
                  disabled={metadataBusy}
                >
                  <option value="">No status</option>
                  {familyStatusOptions.map((option) => (
                    <option key={option.key} value={option.key}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="family-workspace-meta-field">
                <span className="stat-label">Assigned to</span>
                <select
                  className="family-workspace-select"
                  aria-label="Assigned to"
                  value={assignedToDraft}
                  onChange={(event) => {
                    const value = event.target.value;
                    setAssignedToDraft(value);
                    saveMetadata({ assigned_to: value || null });
                  }}
                  disabled={metadataBusy}
                >
                  <option value="">Unassigned</option>
                  {assignableUsers.map((candidate) => (
                    <option key={candidate.id} value={candidate.id}>
                      {formatUserRef(candidate)}
                    </option>
                  ))}
                </select>
              </div>
              <div className="family-workspace-meta-field">
                <span className="stat-label">Reviewed by</span>
                <select
                  className="family-workspace-select"
                  aria-label="Reviewed by"
                  value={reviewedByDraft}
                  onChange={(event) => {
                    const value = event.target.value;
                    setReviewedByDraft(value);
                    saveMetadata({ reviewed_by: value || null });
                  }}
                  disabled={metadataBusy}
                >
                  <option value="">Not reviewed</option>
                  {assignableUsers.map((candidate) => (
                    <option key={candidate.id} value={candidate.id}>
                      {formatUserRef(candidate)}
                    </option>
                  ))}
                </select>
              </div>
              {(metadataBusy || metadataStatus) && (
                <span
                  className={`family-workspace-metadata-status${
                    metadataStatus?.tone === 'error' ? ' family-workspace-metadata-status--error' : ''
                  }`}
                  role="status"
                >
                  {metadataBusy ? 'Saving…' : metadataStatus?.message}
                </span>
              )}
            </div>
          </div>
          {pedRows.length > 0 && (
            <div className="page-top-card-visual">
              <div className="page-top-card-pedigree family-workspace-pedigree">
                <p className="stat-label">Pedigree</p>
                <div className="family-workspace-pedigree-frame overflow-x-auto">
                  <Pedigree
                    rows={pedRows}
                    members={data.members}
                    relationships={data.relationships}
                    inheritanceModel={(data.metadata?.pgt as { inheritance_model?: string } | undefined)?.inheritance_model}
                    phenotypeSampleIds={phenotypeSampleIds}
                  />
                </div>
              </div>
            </div>
          )}
        </div>
      </section>

      <section className="family-workspace-grid">
        <article className="surface-card-flat family-workspace-card family-workspace-card--variants">
          <div className="family-workspace-card-head">
            <div className="space-y-1">
              <h2 className="section-title">Variants</h2>
              <p className="family-workspace-card-subtitle">Filter, review and curate variant calls</p>
            </div>
          </div>
          {/*
            Each workspace renders as soon as its own presence check resolves, so
            the page no longer blocks on the slowest count query. Workspaces with
            no underlying data are simply omitted (see VariantWorkspaceLink).
          */}
          <div className="family-variant-links">
            {hasSmallVariants && (
              <div className="family-variant-row">
                <Link
                  to={`/families/${data.family_id}/small-variants${variantPageQuerySuffix}`}
                  className="form-button family-variant-link hover:no-underline"
                >
                  Small variants
                </Link>
                <div className="family-variant-curation" aria-label="Small variant curation">
                  <span className="family-review-summary-label">Curation</span>
                  <span className="table-chip family-review-summary-chip">
                    Reviewed <strong>{reviewSummary?.reviewed_variant_count ?? 0}</strong>
                  </span>
                  <span className="table-chip family-review-summary-chip family-review-summary-chip--notes">
                    Notes <strong>{reviewSummary?.note_count ?? 0}</strong>
                  </span>
                  {reviewSummaryTags.map((tag) => (
                    <span
                      key={tag.key}
                      className="table-chip table-chip--tag family-review-summary-chip"
                      style={getReviewTagStyle(tag.key, smallVariantReviewTagMap)}
                      title={`${tag.count} small variants tagged ${tag.label}`}
                    >
                      {tag.label} <strong>{tag.count}</strong>
                    </span>
                  ))}
                </div>
              </div>
            )}
            {hasVariants && (
              <div className="family-variant-row">
                <Link
                  to={`/families/${data.family_id}/structural-variants${variantPageQuerySuffix}`}
                  className="form-button family-variant-link hover:no-underline"
                >
                  Structural variants
                </Link>
                <div className="family-variant-curation" aria-label="Structural variant curation">
                  <span className="family-review-summary-label">Curation</span>
                  <span className="table-chip family-review-summary-chip">
                    Reviewed <strong>{structuralReviewSummary?.reviewed_variant_count ?? 0}</strong>
                  </span>
                  <span className="table-chip family-review-summary-chip family-review-summary-chip--notes">
                    Notes <strong>{structuralReviewSummary?.note_count ?? 0}</strong>
                  </span>
                  {structuralReviewSummaryTags.map((tag) => (
                    <span
                      key={tag.key}
                      className="table-chip table-chip--tag family-review-summary-chip"
                      style={getReviewTagStyle(tag.key, structuralVariantReviewTagMap)}
                      title={`${tag.count} structural variants tagged ${tag.label}`}
                    >
                      {tag.label} <strong>{tag.count}</strong>
                    </span>
                  ))}
                </div>
              </div>
            )}
            {(hasAnyVariantData || isMonogenicNipt) && (
              <div className="compact-toolbar family-toolbar family-variant-secondary">
                <VariantWorkspaceLink
                  active={hasSmallVariants || hasVariants}
                  to={`/families/${data.family_id}/report${variantPageQuerySuffix}`}
                  className="button-secondary"
                >
                  Report
                </VariantWorkspaceLink>
                <VariantWorkspaceLink
                  active={hasSmallVariants}
                  to={`/families/${data.family_id}/qc`}
                  className="button-secondary"
                >
                  Sample QC
                </VariantWorkspaceLink>
                <VariantWorkspaceLink
                  active={hasVariantSummary}
                  to={`/families/${data.family_id}/variant-summary`}
                  className="button-secondary"
                >
                  Variant summary
                </VariantWorkspaceLink>
                <VariantWorkspaceLink
                  active={hasRepeatExpansions}
                  to={`/families/${data.family_id}/repeat-expansions`}
                  className="button-secondary"
                >
                  Repeat expansions
                </VariantWorkspaceLink>
                <VariantWorkspaceLink
                  active={hasParaphase}
                  to={`/families/${data.family_id}/paraphase`}
                  className="button-secondary"
                >
                  Paraphase
                </VariantWorkspaceLink>
                <VariantWorkspaceLink
                  active={hasMitoDna}
                  to={`/families/${data.family_id}/mitochondrial-dna${variantPageQuerySuffix}`}
                  className="button-secondary"
                >
                  mtDNA analysis
                </VariantWorkspaceLink>
                {isMonogenicNipt && (
                  <VariantWorkspaceLink
                    active
                    to={`/families/${data.family_id}/nipt`}
                    className="button-secondary"
                  >
                    Monogenic NIPT
                  </VariantWorkspaceLink>
                )}
              </div>
            )}
          </div>
          {!variantCountsLoaded && (
            <p className="dashboard-link-note">Checking available family data…</p>
          )}
          {variantCountsLoaded && !hasAnyVariantData && !isMonogenicNipt && (
            <p className="dashboard-link-note">
              No variant data is loaded for this family yet — each type activates
              automatically once its data is imported.
            </p>
          )}
        </article>

        <div className="family-workspace-side">
          <article className="surface-card-flat family-workspace-card family-workspace-card--viz">
            <div className="family-workspace-card-head">
              <div className="space-y-1">
                <h2 className="section-title">Visualization</h2>
                <p className="family-workspace-card-subtitle">Browse the data across the genome</p>
              </div>
            </div>
            <div className="compact-toolbar family-toolbar">
              <Link
                to={`/families/${data.family_id}/genome`}
                className="button-secondary hover:no-underline"
              >
                Genome view
              </Link>
              <Link
                to={`/families/${data.family_id}/chromosome/1`}
                className="button-secondary hover:no-underline"
              >
                Chromosome view
              </Link>
              <Link
                to={`/families/${data.family_id}/circos`}
                className="button-secondary hover:no-underline"
              >
                Circos plot
              </Link>
              <Link
                to={`/families/${data.family_id}/igv`}
                className="button-secondary hover:no-underline"
              >
                IGV viewer
              </Link>
            </div>
          </article>

          <article className="surface-card-flat family-workspace-card family-workspace-card--roi">
            <div className="family-workspace-card-head">
              <div className="space-y-1">
                <h2 className="section-title">Region of interest</h2>
                <p className="family-workspace-card-subtitle">
                  {data.roi ? (
                    <Link
                      to={`/families/${data.family_id}/chromosome/${data.roi.chr}?start=${Math.max(
                        0,
                        data.roi.start - 1_000_000,
                      )}&end=${data.roi.end + 1_000_000}${
                        projectId ? `&project_id=${projectId}` : ''
                      }`}
                      className="family-roi-link"
                      title={`Open ${data.roi.label} in chromosome view`}
                    >
                      {data.roi.label}
                      <span className="family-roi-region"> · {formatRegion(data.roi)}</span>
                    </Link>
                  ) : (
                    'No region of interest set'
                  )}
                </p>
                {data.roi && (
                  <p className="family-workspace-card-subtitle">
                    <Link
                      to={`/families/${data.family_id}/roi-markers${
                        projectId ? `?project_id=${projectId}` : ''
                      }`}
                      className="family-roi-link"
                      title="Review all phased markers across the ROI ± 1 Mb"
                    >
                      Review ROI markers →
                    </Link>
                  </p>
                )}
              </div>
            </div>
            {canEditRoi && (
              <div className="family-roi-admin">
                <div className="family-roi-edit">
                  <input
                    type="text"
                    value={roiInput}
                    onChange={(e) => setRoiInput(e.target.value)}
                    placeholder="Gene or locus (e.g. BRCA1)"
                    disabled={roiBusy}
                    aria-label="Region of interest gene or locus"
                  />
                  <button
                    type="button"
                    onClick={() => saveRoi(false)}
                    disabled={roiBusy || roiInput.trim().length === 0}
                  >
                    Save
                  </button>
                  {data.roi && (
                    <button
                      type="button"
                      className="button-ghost"
                      onClick={() => saveRoi(true)}
                      disabled={roiBusy}
                    >
                      Clear
                    </button>
                  )}
                </div>
                {roiStatus && (
                  <div
                    className={`status-note ${
                      roiStatus.tone === 'success' ? 'status-note--success' : 'status-note--error'
                    }`}
                  >
                    {roiStatus.message}
                  </div>
                )}
              </div>
            )}
          </article>
        </div>
      </section>

      <section className="surface-card family-members-card space-y-3">
        <div className="family-workspace-card-head">
          <div className="space-y-1">
            <h2 className="section-title">Family members</h2>
            <p className="dashboard-link-note">
              Proband-first overview with pedigree relations and phenotype labels.
            </p>
          </div>
          {userIsAdmin && (
            <div className="compact-toolbar family-toolbar">
              {pendingMemberCount > 0 && (
                <span className="table-chip table-chip--critical">
                  {pendingMemberCount} pending
                </span>
              )}
              <button
                type="button"
                className="form-button"
                onClick={savePendingMemberUpdates}
                disabled={pendingMembersBusy || pendingMemberCount === 0}
              >
                Save pending updates
              </button>
              <button
                type="button"
                className="button-ghost"
                onClick={() => {
                  setPendingMemberUpdates({});
                  setPendingMembersStatus(null);
                }}
                disabled={pendingMembersBusy || pendingMemberCount === 0}
              >
                Discard
              </button>
            </div>
          )}
        </div>
        {pendingMembersStatus && (
          <div
            className={`status-note ${
              pendingMembersStatus.tone === 'success' ? 'status-note--success' : 'status-note--error'
            }`}
          >
            {pendingMembersStatus.message}
          </div>
        )}
        <div className="data-table-shell overflow-x-auto">
          <table className="analysis-table family-members-table">
            <colgroup>
              <col style={{ width: canEditFamilyDetails ? '13%' : '16%' }} />
              <col style={{ width: canEditFamilyDetails ? '9%' : '11%' }} />
              {canEditFamilyDetails && <col style={{ width: '7%' }} />}
              <col style={{ width: canEditFamilyDetails ? '11%' : '13%' }} />
              <col style={{ width: canEditFamilyDetails ? '11%' : '13%' }} />
              <col style={{ width: canEditFamilyDetails ? '11%' : '13%' }} />
              <col style={{ width: '16%' }} />
              <col style={{ width: canEditFamilyDetails ? '14%' : '18%' }} />
              {canEditFamilyDetails && <col style={{ width: '8%' }} />}
            </colgroup>
            <thead>
              <tr>
                <th>Sample</th>
                <th>Role</th>
                {canEditFamilyDetails && <th>Sex</th>}
                <th>Father</th>
                <th>Mother</th>
                <th>Partner</th>
                <th>Status</th>
                <th>HPO terms</th>
                {canEditFamilyDetails && <th>Actions</th>}
              </tr>
            </thead>
            <tbody>
              {(canEditFamilyDetails ? activeStructureMembers : orderedMembers).map((member) => {
                const draftMember = canEditFamilyDetails ? (member as StructureMemberDraft) : null;
                const apiMember = canEditFamilyDetails ? null : (member as ApiFamilyRecord['members'][number]);
                const displayMember = canEditFamilyDetails
                  ? member
                  : memberViewWithPending(apiMember!);
                const pending = !canEditFamilyDetails ? pendingMemberUpdates[apiMember!.sample_id] : null;
                const parents = pending
                  ? {
                      father: pending.father_id || undefined,
                      mother: pending.mother_id || undefined,
                    }
                  : parentLabelByChild[sampleKey(member.sample_id)] || {};
                const sexSymbol =
                  displayMember.sex === 'male' ? '♂' : displayMember.sex === 'female' ? '♀' : '⚧';
                const clinicalStatus = draftMember
                  ? draftMember.clinical_status
                  : clinicalStatusForMember(displayMember);
                const carrierStatus = draftMember
                  ? draftMember.carrier_status
                  : carrierStatusForMember(displayMember);
                const affected = clinicalStatus === 'affected';
                const annotations = hpoAnnotationsBySample[member.sample_id] || [];
                const embryoClass =
                  String(displayMember.role).toLowerCase() === 'embryo'
                    ? embryoSegregation.get(member.sample_id)
                    : undefined;
                return (
                  <tr key={member.sample_id}>
                    <td>
                      <span className="family-member-identity">
                        <span className="family-member-sex" aria-hidden="true">
                          {sexSymbol}
                        </span>
                        <button
                          type="button"
                          className="button-link family-member-name-button"
                          onClick={() => openMemberDetail(member.sample_id)}
                        >
                          {displayMember.sample_id}
                        </button>
                        {affected && (
                          <span className="family-member-affected" title="Affected">
                            *
                          </span>
                        )}
                        {pending && (
                          <span className="table-chip table-chip--critical" title="Pending update">
                            pending
                          </span>
                        )}
                      </span>
                    </td>
                    <td>
                      {canEditFamilyDetails ? (
                        <select
                          aria-label={`Role for ${member.sample_id}`}
                          value={draftMember!.role}
                          onChange={(event) =>
                            updateStructureMember(member.sample_id, {
                              role: event.target.value as StructureMemberDraft['role'],
                            })
                          }
                          disabled={structureBusy}
                        >
                          {ROLE_OPTIONS.map((role) => (
                            <option key={role} value={role}>
                              {role}
                            </option>
                          ))}
                        </select>
                      ) : (
                        displayMember.role
                      )}
                    </td>
                    {canEditFamilyDetails && (
                      <td>
                        <select
                          aria-label={`Sex for ${member.sample_id}`}
                          value={draftMember!.sex}
                          onChange={(event) =>
                            updateStructureMember(member.sample_id, {
                              sex: event.target.value as StructureMemberDraft['sex'],
                            })
                          }
                          disabled={structureBusy}
                        >
                          {SEX_OPTIONS.map((sex) => (
                            <option key={sex} value={sex}>
                              {sex}
                            </option>
                          ))}
                        </select>
                      </td>
                    )}
                    <td>
                      {canEditFamilyDetails ? (
                        <select
                          aria-label={`Father for ${member.sample_id}`}
                          value={parentValueFor(member.sample_id, 'father')}
                          onChange={(event) =>
                            setParentValue(member.sample_id, 'father', event.target.value)
                          }
                          disabled={structureBusy}
                        >
                          <option value="">—</option>
                          {activeSampleIds
                            .filter((sampleId) => sampleId !== member.sample_id)
                            .map((sampleId) => (
                              <option key={sampleId} value={sampleId}>
                                {sampleId}
                              </option>
                            ))}
                        </select>
                      ) : (
                        parents.father ?? '-'
                      )}
                    </td>
                    <td>
                      {canEditFamilyDetails ? (
                        <select
                          aria-label={`Mother for ${member.sample_id}`}
                          value={parentValueFor(member.sample_id, 'mother')}
                          onChange={(event) =>
                            setParentValue(member.sample_id, 'mother', event.target.value)
                          }
                          disabled={structureBusy}
                        >
                          <option value="">—</option>
                          {activeSampleIds
                            .filter((sampleId) => sampleId !== member.sample_id)
                            .map((sampleId) => (
                              <option key={sampleId} value={sampleId}>
                                {sampleId}
                              </option>
                            ))}
                        </select>
                      ) : (
                        parents.mother ?? '-'
                      )}
                    </td>
                    <td>
                      {canEditFamilyDetails ? (
                        <select
                          aria-label={`Partner for ${member.sample_id}`}
                          value={partnerValueFor(member.sample_id)}
                          onChange={(event) => setPartnerValue(member.sample_id, event.target.value)}
                          disabled={structureBusy}
                        >
                          <option value="">—</option>
                          {activeSampleIds
                            .filter((sampleId) => sampleId !== member.sample_id)
                            .map((sampleId) => (
                              <option key={sampleId} value={sampleId}>
                                {sampleId}
                              </option>
                            ))}
                        </select>
                      ) : (
                        partnerValueFor(member.sample_id) || '-'
                      )}
                    </td>
                    <td>
                      {canEditFamilyDetails ? (
                        <div className="family-member-status-controls">
                          <select
                            aria-label={`Phenotype for ${member.sample_id}`}
                            value={clinicalStatus}
                            onChange={(event) =>
                              updateStructureMember(member.sample_id, {
                                clinical_status: event.target.value as ClinicalStatus,
                              })
                            }
                            disabled={structureBusy}
                          >
                            {CLINICAL_STATUS_OPTIONS.map((status) => (
                              <option key={status} value={status}>
                                {status}
                              </option>
                            ))}
                          </select>
                          <select
                            aria-label={`Carrier status for ${member.sample_id}`}
                            value={carrierStatus}
                            onChange={(event) =>
                              updateStructureMember(member.sample_id, {
                                carrier_status: event.target.value as CarrierStatus,
                              })
                            }
                            disabled={structureBusy}
                          >
                            {CARRIER_STATUS_OPTIONS.map((status) => (
                              <option key={status} value={status}>
                                {status}
                              </option>
                            ))}
                          </select>
                        </div>
                      ) : (
                        <div className="family-member-status-chips">
                          <span
                            className={`table-chip ${
                              clinicalStatus === 'affected' ? 'table-chip--critical' : ''
                            }`}
                          >
                            {clinicalStatus}
                          </span>
                          {carrierStatus === 'carrier' && (
                            <span className="table-chip table-chip--warning">
                              {displayMember.carrier_type || 'carrier'}
                            </span>
                          )}
                          {embryoClass && (
                            <InfoTip
                              className={`segregation-badge segregation-badge--${embryoClass.state}`}
                              label="Derived from the haplotype analysis at the ROI — not entered by an analyst."
                            >
                              {segregationStateLabel(embryoClass.state)}
                            </InfoTip>
                          )}
                          {embryoClass?.recombinationNearRoi && (
                            <InfoTip
                              className="segregation-warning"
                              label="A recombination falls inside or close to the ROI — the call across the locus is uncertain. Review the ROI markers."
                            >
                              ⚠ recombination
                            </InfoTip>
                          )}
                          {embryoClass?.uninformative && !embryoClass.recombinationNearRoi && (
                            <InfoTip
                              className="segregation-warning"
                              label="No disease haplotype could be resolved at the ROI (uninformative markers)."
                            >
                              ⚠ uninformative
                            </InfoTip>
                          )}
                        </div>
                      )}
                    </td>
                    <td>
                      {annotations.length ? (
                        <div className="family-hpo-chip-list family-hpo-chip-list--compact">
                          {annotations.slice(0, 3).map((annotation) => (
                            <span
                              key={annotation.id}
                              className={`table-chip family-hpo-chip family-hpo-chip--${annotation.status}`}
                              title={hpoTooltip(annotation)}
                            >
                              <strong>{annotation.label}</strong>
                              {annotation.status !== 'present' && <em>{annotation.status}</em>}
                            </span>
                          ))}
                          {annotations.length > 3 && (
                            <span className="table-chip" title={`${annotations.length} HPO annotations`}>
                              +{annotations.length - 3}
                            </span>
                          )}
                        </div>
                      ) : (
                        <span className="dashboard-link-note">-</span>
                      )}
                    </td>
                    {canEditFamilyDetails && (
                      <td>
                        <button
                          type="button"
                          className="button-ghost"
                          onClick={() => removeStructureMember(member.sample_id)}
                          disabled={structureBusy || activeStructureMembers.length <= 1}
                        >
                          Remove
                        </button>
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {canEditFamilyDetails && (
          <div className="space-y-4 family-members-editor">
            <h3 className="family-member-subsection-title">Add member</h3>
            <div className="data-table-shell overflow-x-auto">
              <table className="analysis-table family-members-table">
                <colgroup>
                  <col style={{ width: '15%' }} />
                  <col style={{ width: '10%' }} />
                  <col style={{ width: '8%' }} />
                  <col style={{ width: '11%' }} />
                  <col style={{ width: '11%' }} />
                  <col style={{ width: '11%' }} />
                  <col style={{ width: '20%' }} />
                  <col style={{ width: '14%' }} />
                </colgroup>
                <thead>
                  <tr>
                    <th>New sample</th>
                    <th>Role</th>
                    <th>Sex</th>
                    <th>Father</th>
                    <th>Mother</th>
                    <th>Partner</th>
                    <th>Status</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>
                      <input
                        aria-label="New member sample ID"
                        type="text"
                        value={newMember.sample_id}
                        onChange={(event) =>
                          setNewMember((member) => ({ ...member, sample_id: event.target.value }))
                        }
                        disabled={structureBusy}
                      />
                    </td>
                    <td>
                      <select
                        aria-label="New member role"
                        value={newMember.role}
                        onChange={(event) =>
                          setNewMember((member) => ({
                            ...member,
                            role: event.target.value as StructureMemberDraft['role'],
                          }))
                        }
                        disabled={structureBusy}
                      >
                        {ROLE_OPTIONS.map((role) => (
                          <option key={role} value={role}>
                            {role}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <select
                        aria-label="New member sex"
                        value={newMember.sex}
                        onChange={(event) =>
                          setNewMember((member) => ({
                            ...member,
                            sex: event.target.value as StructureMemberDraft['sex'],
                          }))
                        }
                        disabled={structureBusy}
                      >
                        {SEX_OPTIONS.map((sex) => (
                          <option key={sex} value={sex}>
                            {sex}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <select
                        aria-label="New member father"
                        value={newMemberRelations.father}
                        onChange={(event) =>
                          setNewMemberRelations((relations) => ({ ...relations, father: event.target.value }))
                        }
                        disabled={structureBusy}
                      >
                        <option value="">—</option>
                        {activeSampleIds.map((sampleId) => (
                          <option key={sampleId} value={sampleId}>
                            {sampleId}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <select
                        aria-label="New member mother"
                        value={newMemberRelations.mother}
                        onChange={(event) =>
                          setNewMemberRelations((relations) => ({ ...relations, mother: event.target.value }))
                        }
                        disabled={structureBusy}
                      >
                        <option value="">—</option>
                        {activeSampleIds.map((sampleId) => (
                          <option key={sampleId} value={sampleId}>
                            {sampleId}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <select
                        aria-label="New member partner"
                        value={newMemberRelations.partner}
                        onChange={(event) =>
                          setNewMemberRelations((relations) => ({ ...relations, partner: event.target.value }))
                        }
                        disabled={structureBusy}
                      >
                        <option value="">—</option>
                        {activeSampleIds.map((sampleId) => (
                          <option key={sampleId} value={sampleId}>
                            {sampleId}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <div className="family-member-status-controls">
                        <select
                          aria-label="New member phenotype"
                          value={newMember.clinical_status}
                          onChange={(event) =>
                            setNewMember((member) => ({
                              ...member,
                              clinical_status: event.target.value as ClinicalStatus,
                            }))
                          }
                          disabled={structureBusy}
                        >
                          {CLINICAL_STATUS_OPTIONS.map((status) => (
                            <option key={status} value={status}>
                              {status}
                            </option>
                          ))}
                        </select>
                        <select
                          aria-label="New member carrier status"
                          value={newMember.carrier_status}
                          onChange={(event) =>
                            setNewMember((member) => ({
                              ...member,
                              carrier_status: event.target.value as CarrierStatus,
                              carrier_type: event.target.value === 'carrier' ? member.carrier_type : '',
                            }))
                          }
                          disabled={structureBusy}
                        >
                          {CARRIER_STATUS_OPTIONS.map((status) => (
                            <option key={status} value={status}>
                              {status}
                            </option>
                          ))}
                        </select>
                      </div>
                    </td>
                    <td>
                      <button
                        type="button"
                        className="button-secondary"
                        onClick={addStructureMember}
                        disabled={structureBusy}
                      >
                        Add member
                      </button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div className="compact-toolbar family-toolbar">
              <button
                type="button"
                className="family-structure-update-button"
                onClick={saveStructure}
                disabled={structureBusy || activeStructureMembers.length === 0}
              >
                Update Family Structure
              </button>
              {data.structure_version?.version !== undefined && (
                <span className="table-chip">Version {data.structure_version.version}</span>
              )}
            </div>
            {structureStatus && (
              <div
                className={`status-note ${
                  structureStatus.tone === 'success' ? 'status-note--success' : 'status-note--error'
                }`}
              >
                {structureStatus.message}
              </div>
            )}
          </div>
        )}
      </section>

      <MonarchPhenotypeMatchPanel familyId={familyId} projectId={projectId} />

      {selectedMemberId && (
        <div
          className="modal-backdrop family-member-modal-backdrop"
          role="presentation"
          onMouseDown={closeMemberDetail}
        >
          <section
            className="modal-surface surface-card family-member-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Family member details"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className="variant-review-modal-header">
              <div>
                <p className="page-kicker">Family Member</p>
                <h2 className="catalog-card-title">
                  {selectedMemberDetail?.member.sample_id ?? selectedMemberId}
                </h2>
              </div>
              <button type="button" className="button-secondary" onClick={closeMemberDetail}>
                Close
              </button>
            </div>

            {!selectedMemberDetail || !memberDraft ? (
              <p className="dashboard-link-note">Loading member details.</p>
            ) : (
              <>
                <div className="family-member-modal-grid">
                  <label className="field-label">
                    Identifier
                    <input
                      type="text"
                      value={memberDraft.sample_id}
                      onChange={(event) =>
                        setMemberDraft((draft) =>
                          draft ? { ...draft, sample_id: event.target.value } : draft,
                        )
                      }
                      disabled={!userIsAdmin || memberBusy}
                    />
                  </label>
                  <label className="field-label">
                    Sex
                    <select
                      value={memberDraft.sex}
                      onChange={(event) =>
                        setMemberDraft((draft) =>
                          draft
                            ? { ...draft, sex: event.target.value as StructureMemberDraft['sex'] }
                            : draft,
                        )
                      }
                      disabled={!userIsAdmin || memberBusy}
                    >
                      {SEX_OPTIONS.map((sex) => (
                        <option key={sex} value={sex}>
                          {sex}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="field-label">
                    Family role
                    <select
                      value={memberDraft.role}
                      onChange={(event) =>
                        setMemberDraft((draft) =>
                          draft
                            ? { ...draft, role: event.target.value as StructureMemberDraft['role'] }
                            : draft,
                        )
                      }
                      disabled={!userIsAdmin || memberBusy}
                    >
                      {ROLE_OPTIONS.map((role) => (
                        <option key={role} value={role}>
                          {role}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="field-label">
                    Phenotype
                    <select
                      value={memberDraft.clinical_status}
                      onChange={(event) =>
                        setMemberDraft((draft) =>
                          draft
                            ? { ...draft, clinical_status: event.target.value as ClinicalStatus }
                            : draft,
                        )
                      }
                      disabled={!userIsAdmin || memberBusy}
                    >
                      {CLINICAL_STATUS_OPTIONS.map((status) => (
                        <option key={status} value={status}>
                          {status}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="field-label">
                    Carrier status
                    <select
                      value={memberDraft.carrier_status}
                      onChange={(event) =>
                        setMemberDraft((draft) =>
                          draft
                            ? { ...draft, carrier_status: event.target.value as CarrierStatus }
                            : draft,
                        )
                      }
                      disabled={!userIsAdmin || memberBusy}
                    >
                      {CARRIER_STATUS_OPTIONS.map((status) => (
                        <option key={status} value={status}>
                          {status}
                        </option>
                      ))}
                    </select>
                  </label>
                  {memberDraft.carrier_status === 'carrier' && (
                    <label className="field-label">
                      Carrier type
                      <select
                        value={memberDraft.carrier_type}
                        onChange={(event) =>
                          setMemberDraft((draft) =>
                            draft
                              ? { ...draft, carrier_type: event.target.value as CarrierType }
                              : draft,
                          )
                        }
                        disabled={!userIsAdmin || memberBusy}
                      >
                        <option value="">type</option>
                        {CARRIER_TYPE_OPTIONS.map((type) => (
                          <option key={type} value={type}>
                            {type}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                  <label className="field-label">
                    Father
                    <select
                      value={memberDraft.father_id}
                      onChange={(event) =>
                        setMemberDraft((draft) =>
                          draft ? { ...draft, father_id: event.target.value } : draft,
                        )
                      }
                      disabled={!userIsAdmin || memberBusy}
                    >
                      <option value="">None</option>
                      {orderedMembers
                        .filter((member) => member.sample_id !== selectedMemberDetail.member.sample_id)
                        .map((member) => (
                          <option key={member.sample_id} value={member.sample_id}>
                            {member.sample_id}
                          </option>
                        ))}
                    </select>
                  </label>
                  <label className="field-label">
                    Mother
                    <select
                      value={memberDraft.mother_id}
                      onChange={(event) =>
                        setMemberDraft((draft) =>
                          draft ? { ...draft, mother_id: event.target.value } : draft,
                        )
                      }
                      disabled={!userIsAdmin || memberBusy}
                    >
                      <option value="">None</option>
                      {orderedMembers
                        .filter((member) => member.sample_id !== selectedMemberDetail.member.sample_id)
                        .map((member) => (
                          <option key={member.sample_id} value={member.sample_id}>
                            {member.sample_id}
                          </option>
                        ))}
                    </select>
                  </label>
                </div>

                <div className="family-member-modal-review-grid">
                  <div className="variant-review-modal-section family-member-modal-section">
                    <div className="family-workspace-card-head">
                      <h3 className="section-title">HPO Phenotypes</h3>
                      <span className="table-chip">
                        {selectedMemberDetail.hpo_annotations.length} terms
                      </span>
                    </div>
                    {selectedMemberDetail.hpo_annotations.length ? (
                      <div className="family-hpo-chip-list">
                        {selectedMemberDetail.hpo_annotations.map((annotation) => (
                          <span
                            key={annotation.id}
                            className={`table-chip family-hpo-chip family-hpo-chip--${annotation.status}`}
                            title={hpoTooltip(annotation)}
                          >
                            <span>{annotation.hpo_id}</span>
                            <strong>{annotation.label}</strong>
                            <em>{annotation.status}</em>
                            {userIsAdmin && (
                              <button
                                type="button"
                                className="button-ghost"
                                onClick={() => removeHpoAnnotation(annotation.id)}
                                disabled={hpoBusy}
                              >
                                Remove
                              </button>
                            )}
                          </span>
                        ))}
                      </div>
                    ) : (
                      <p className="dashboard-link-note">No HPO phenotypes linked.</p>
                    )}

                    {userIsAdmin && (
                      <>
                        <div className="family-hpo-controls">
                          <label className="field-label family-hpo-term-field">
                            HPO term
                            <input
                              type="text"
                              value={hpoSearchInput}
                              onChange={(event) => updateHpoSearchInput(event.target.value)}
                              placeholder="HP:0001250 or seizure"
                              disabled={hpoBusy}
                              list={`hpo-term-options-${data.family_id}`}
                            />
                            <datalist id={`hpo-term-options-${data.family_id}`}>
                              {hpoSearchResults.map((term) => (
                                <option key={term.hpo_id} value={formatHpoTermOption(term)} />
                              ))}
                            </datalist>
                          </label>
                          <label className="field-label">
                            Status
                            <select
                              value={hpoAnnotationStatus}
                              onChange={(event) =>
                                setHpoAnnotationStatus(event.target.value as HpoAnnotationStatus)
                              }
                              disabled={hpoBusy}
                            >
                              {HPO_STATUS_OPTIONS.map((status) => (
                                <option key={status} value={status}>
                                  {status}
                                </option>
                              ))}
                            </select>
                          </label>
                          <label className="field-label family-hpo-note-field">
                            Note
                            <input
                              type="text"
                              value={hpoNote}
                              onChange={(event) => setHpoNote(event.target.value)}
                              disabled={hpoBusy}
                            />
                          </label>
                          <button
                            type="button"
                            className="form-button"
                            onClick={addHpoAnnotation}
                            disabled={hpoBusy || !selectedHpoTerm}
                          >
                            Add phenotype
                          </button>
                        </div>


                      </>
                    )}
                    {hpoStatus && (
                      <div className={`status-note ${hpoStatus.tone === 'success' ? 'status-note--success' : 'status-note--error'}`}>
                        {hpoStatus.message}
                      </div>
                    )}
                  </div>
                  <div className="variant-review-modal-section family-member-modal-section">
                    <div className="family-workspace-card-head">
                      <h3 className="section-title">Impact</h3>
                      {selectedMemberDetail.impact.destructive && (
                        <span className="table-chip table-chip--critical">Linked data</span>
                      )}
                    </div>
                    {selectedMemberDetail.impact.warnings.length ? (
                      <ul className="family-member-impact-list">
                        {selectedMemberDetail.impact.warnings.map((warning) => (
                          <li key={warning}>{warning}</li>
                        ))}
                      </ul>
                    ) : (
                      <p className="dashboard-link-note">No derived-data dependencies detected.</p>
                    )}
                    <div className="family-member-impact-chips">
                      {Object.entries(selectedMemberDetail.impact.pedigree_references).map(([key, count]) => (
                        <span key={key} className="table-chip">
                          {key} {count}
                        </span>
                      ))}
                      {Object.entries(selectedMemberDetail.impact.data_counts).map(([key, count]) => (
                        <span key={key} className="table-chip">
                          {key} {count}
                        </span>
                      ))}
                      {selectedMemberDetail.impact.stale_analysis_scopes.map((scope) => (
                        <span key={scope} className="table-chip table-chip--critical">
                          {scope}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>

                {memberStatus && (
                  <div
                    className={`status-note ${
                      memberStatus.tone === 'success' ? 'status-note--success' : 'status-note--error'
                    }`}
                  >
                    {memberStatus.message}
                  </div>
                )}

                {userIsAdmin && (
                  <div className="variant-review-modal-actions compact-toolbar">
                    <button type="button" className="form-button"onClick={applyMemberDetail} disabled={memberBusy}>
                      Apply to pending
                    </button>
                    <button
                      type="button"
                      className="button-ghost"
                      onClick={deleteMemberDetail}
                      disabled={memberBusy || orderedMembers.length <= 1}
                    >
                      Remove member
                    </button>
                  </div>
                )}
              </>
            )}
          </section>
        </div>
      )}
    </div>
  );
};

export default FamilyDetailPage;
