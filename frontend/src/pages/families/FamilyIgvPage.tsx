import React, { useMemo } from 'react';
import { useParams, useSearchParams, Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import api from '../../lib/api';
import type { ApiFamilyRecord } from '../../lib/apiTypes';
import IgvViewer from '../../components/IgvViewer';
import { sortFamilyMembersProbandFirst } from '../../lib/familyMembers';
import {
  formatResolvedReferenceLabel,
  mapAssemblyToIgvGenome,
  useFamilyReference,
} from '../../lib/reference';
import PageState from '../../components/PageState';

const FamilyIgvPage: React.FC = () => {
  const { familyId } = useParams<{ familyId: string }>();
  const [searchParams] = useSearchParams();
  const initialLocus = searchParams.get('locus') || undefined;
  const projectIdParam = searchParams.get('project_id') || undefined;
  const backSearch = searchParams.get('back') || undefined;
  const backPathParam = searchParams.get('back_path') || undefined;

  const { data, isLoading } = useQuery<Pick<ApiFamilyRecord, 'members' | 'projects'>>({
    queryKey: ['family', familyId],
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}`);
      return res.data as Pick<ApiFamilyRecord, 'members' | 'projects'>;
    },
    enabled: !!familyId,
  });

  const {
    speciesName,
    assemblyName,
    assemblyVersion,
    isLoading: referenceLoading,
  } = useFamilyReference(
    data?.projects,
    projectIdParam,
  );
  const resolvedGenome = useMemo(() => mapAssemblyToIgvGenome(assemblyName), [assemblyName]);
  const referenceLabel = formatResolvedReferenceLabel(
    { speciesName, assemblyName, assemblyVersion },
    'Reference not linked',
  );

  if (!familyId) {
    return <p>Missing family identifier</p>;
  }

  if (isLoading || (data?.projects?.length && referenceLoading)) {
    return (
      <PageState
        kicker="Viewer"
        title="Loading IGV workspace"
        message="Resolving family context and genome reference before opening the viewer."
      />
    );
  }

  if (!data) {
    return (
      <PageState
        kicker="Viewer"
        title="Family not found"
        message="The IGV view could not resolve the requested family."
      />
    );
  }

  if (!assemblyName) {
    return (
      <PageState
        kicker="Viewer"
        title="Reference not linked"
        message="This IGV workspace requires a project-linked assembly."
      />
    );
  }

  const sampleIds = sortFamilyMembersProbandFirst(data.members).map((m) => m.sample_id);

  return (
    <div className="page-shell analysis-shell">
      <section className="surface-card page-top-card">
        <div className="page-header">
          <div className="space-y-2">
            <p className="page-kicker">Viewer</p>
            <h1 className="catalog-card-title">IGV for family {familyId}</h1>
            <p className="catalog-card-copy">
              {referenceLabel}
              {initialLocus ? ` • ${initialLocus}` : ''}
            </p>
          </div>
          <Link
            to={backPathParam || (backSearch ? `/families/${familyId}/small-variants?${backSearch}` : `/families/${familyId}/small-variants`)}
            className="button-secondary hover:no-underline"
          >
            Back
          </Link>
        </div>
      </section>
      <section className="viz-panel">
        <IgvViewer
          familyId={familyId}
          sampleIds={sampleIds}
          genome={resolvedGenome}
          locus={initialLocus}
        />
      </section>
    </div>
  );
};

export default FamilyIgvPage;
