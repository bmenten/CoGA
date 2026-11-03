import { Link, useLocation, useParams } from 'react-router';
import { useQuery } from '@tanstack/react-query';
import api from '../../lib/api';
import { apiPath } from '../../lib/apiPath';
import type { ApiFamilyRecord } from '../../lib/apiTypes';
import type { FamilyAncestryOut } from '../../lib/apiSchema.generated';
import { useFamilyReference } from '../../lib/reference';
import FamilyCaseHeader from './FamilyCaseHeader';
import FamilyLoadFailure from '../../components/FamilyLoadFailure';
import PageState from '../../components/PageState';
import FamilyWgsSourcePanel from './FamilyWgsSourcePanel';
import FamilyAncestryPanel from './FamilyAncestryPanel';

/**
 * The exploratory ancestry PCA of a family: genetic similarity of its individuals to public
 * reference populations. Outside CoGA's intended purpose (TF-01 §4): it is not validated, never
 * read clinically and never part of the sign-out or the signed record, so the page says so
 * before anything else, whatever its data.
 */
const FamilyAncestryPage = () => {
  const { familyId = '' } = useParams<{ familyId: string }>();
  const location = useLocation();
  const requestedProject =
    new URLSearchParams(location.search).get('project_id') || undefined;
  const family = useQuery({
    queryKey: ['family', familyId],
    queryFn: async () =>
      (await api.get<ApiFamilyRecord>(apiPath`/families/${familyId}`)).data,
  });
  const reference = useFamilyReference(family.data?.projects, requestedProject);
  const projectId = reference.projectId ?? requestedProject;
  const enabled =
    Boolean(family.data) && !reference.isLoading && !reference.isError;
  const ancestry = useQuery({
    queryKey: ['family-ancestry', familyId, projectId, ''],
    enabled,
    queryFn: async () =>
      (
        await api.get<FamilyAncestryOut>(
          apiPath`/families/${familyId}/ancestry`,
          {
            params: projectId ? { project_id: projectId } : {},
          }
        )
      ).data,
  });
  if (
    family.isPending ||
    (family.data?.projects?.length && reference.isLoading)
  ) {
    return (
      <PageState
        loading
        kicker="Ancestry"
        title="Loading family ancestry"
        message="Loading the family and its project reference."
      />
    );
  }
  if (family.error || !family.data) {
    return (
      <FamilyLoadFailure
        kicker="Ancestry"
        what="Family"
        error={family.error}
        notFoundMessage="The requested family is unavailable."
        onRetry={() => {
          void family.refetch();
        }}
      />
    );
  }
  return (
    <div className="page-shell space-y-5">
      <FamilyCaseHeader
        familyId={familyId}
        family={family.data}
        projectId={projectId}
        kicker="Ancestry"
        assemblyLabel={reference.assemblyName || 'Not linked'}
        current="ancestry"
        assemblyScope={{
          name: reference.assemblyName,
          validated: reference.assemblyValidated,
          unavailable: reference.isError,
          onRetry: reference.retry,
        }}
      />
      <p className="status-note status-note--warning" role="note">
        <strong>Exploratory — not for clinical use.</strong> This page shows the
        genetic similarity of the family’s individuals to public reference
        populations. It is not an ancestry percentage or an ethnic identity, and
        it has not been validated. It is never part of the sign-out or of the
        signed record.{' '}
        <Link to="/docs/reference/ancestry-pca">How the PCA is calculated</Link>
      </p>
      <FamilyWgsSourcePanel
        key={`wgs:${familyId}:${projectId}`}
        familyId={familyId}
        projectId={projectId}
        enabled={enabled}
        currentSource={ancestry.data?.source_state === 'ready'}
      />
      <FamilyAncestryPanel
        key={`${familyId}:${projectId}`}
        familyId={familyId}
        projectId={projectId}
        enabled={enabled}
      />
    </div>
  );
};
export default FamilyAncestryPage;
