import React, { useMemo } from 'react';
import { useParams, useSearchParams, Link } from 'react-router';
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
import FamilyLoadFailure from '../../components/FamilyLoadFailure';
import FamilyPageBanners from '../../components/FamilyPageBanners';
import { apiPath } from '../../lib/apiPath';
import { familyPagePath, raw, routePath } from '../../lib/routePath';

const FamilyIgvPage: React.FC = () => {
  const { familyId } = useParams<{ familyId: string }>();
  const [searchParams] = useSearchParams();
  const initialLocus = searchParams.get('locus') || undefined;
  const projectIdParam = searchParams.get('project_id') || undefined;
  const backSearch = searchParams.get('back') || undefined;
  // Only one of the app's family pages: the link chose it (SEC-15).
  const backPathParam = familyPagePath(searchParams.get('back_path')) ?? undefined;

  const { data, isLoading, isError, error, refetch } = useQuery<
    Pick<ApiFamilyRecord, 'members' | 'projects' | 'metadata'>
  >({
    queryKey: ['family', familyId],
    queryFn: async () => {
      const res = await api.get(apiPath`/families/${familyId}`);
      return res.data as Pick<ApiFamilyRecord, 'members' | 'projects' | 'metadata'>;
    },
    enabled: !!familyId,
  });

  const {
    speciesName,
    assemblyName,
    assemblyValidated,
    assemblyVersion,
    isLoading: referenceLoading,
    isError: referenceFailed,
    retry: retryReference,
  } = useFamilyReference(
    data?.projects,
    projectIdParam,
  );
  const resolvedGenome = useMemo(() => mapAssemblyToIgvGenome(assemblyName), [assemblyName]);
  const referenceLabel = formatResolvedReferenceLabel(
    { speciesName, assemblyName, assemblyVersion, isError: referenceFailed },
    'Reference not linked',
  );

  if (!familyId) {
    return <p>Missing family identifier</p>;
  }

  if (isLoading || (data?.projects?.length && referenceLoading)) {
    return (
      <PageState
        loading
        kicker="Viewer"
        title="Loading IGV workspace"
        message="Resolving family context and genome reference before opening the viewer."
      />
    );
  }

  if (isError) {
    return (
      <FamilyLoadFailure
        kicker="Viewer"
        what="Family"
        error={error}
        notFoundMessage="The IGV view could not resolve the requested family."
        onRetry={() => void refetch()}
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

  // The project catalogue failed: the reference is unknown, not missing (#608).
  if (referenceFailed) {
    return (
      <PageState
        kicker="Viewer"
        title="Reference could not be loaded"
        message="The family's project, and with it the reference assembly, could not be loaded. IGV needs it to load the genome."
        action={
          <button type="button" className="button-secondary" onClick={retryReference}>
            Retry
          </button>
        }
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

  // Another assembly's genome would draw the reads and variants at the wrong positions: any
  // name the mapping did not know opened hg38, the T2T assembly's among them.
  if (!resolvedGenome) {
    return (
      <PageState
        kicker="Viewer"
        title={`No IGV genome for ${assemblyName}`}
        message={`IGV shows the reads on the genome of the family's reference assembly, and CoGA knows no IGV genome for ${assemblyName}. It opens none rather than another assembly's, on which the reads and variants would be drawn at the wrong positions.`}
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
            to={backPathParam || routePath`/families/${familyId}/small-variants${raw(backSearch ? `?${backSearch}` : '')}`}
            className="button-secondary hover:no-underline"
          >
            Back
          </Link>
        </div>
        <FamilyPageBanners
          metadata={data.metadata}
          assemblyScope={{ name: assemblyName, validated: assemblyValidated }}
        />
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
