import React from 'react';
import { Link, useLocation, useParams } from 'react-router';
import { useQuery } from '@tanstack/react-query';
import api from '../../lib/api';
import PageState from '../../components/PageState';
import FamilyLoadFailure from '../../components/FamilyLoadFailure';
import FamilyCaseHeader from './FamilyCaseHeader';
import { apiPath } from '../../lib/apiPath';
import { raw, routePath } from '../../lib/routePath';
import type { FamilyOut } from '../../lib/apiSchema.generated';
import {
  formatResolvedReferenceLabel,
  useFamilyReference,
} from '../../lib/reference';
import { useFamilyDataPresence } from './familyDataPresence';
import VariantSummarySmallVariants from './VariantSummarySmallVariants';
import VariantSummaryStructuralVariants from './VariantSummaryStructuralVariants';
import {
  MitoDnaSummary,
  ParaphaseSummary,
  RepeatExpansionSummary,
} from './VariantSummaryOtherCalls';

/**
 * The family's variant summary: every kind of variant data the family holds, one section each
 * (PROD-1). An overview first says which kinds the family has, with their analysis pages;
 * then small variants, structural variants, repeat expansions, Paraphase and mtDNA, each
 * loading on its own. A kind the family lacks has no section; one whose check failed is shown,
 * so its section can say for itself (#607).
 */

type SectionKey =
  | 'small-variants'
  | 'structural-variants'
  | 'repeat-expansions'
  | 'paraphase'
  | 'mitochondrial-dna';

const SECTIONS: {
  key: SectionKey;
  label: string;
  page: string;
  withProject: boolean;
}[] = [
  {
    key: 'small-variants',
    label: 'Small variants (SNV/indel)',
    page: 'Small variants',
    withProject: true,
  },
  {
    key: 'structural-variants',
    label: 'Structural variants',
    page: 'Structural variants',
    withProject: true,
  },
  {
    key: 'repeat-expansions',
    label: 'Repeat expansions',
    page: 'Repeat expansions',
    withProject: false,
  },
  {
    key: 'paraphase',
    label: 'Paraphase',
    page: 'Paraphase',
    withProject: false,
  },
  {
    key: 'mitochondrial-dna',
    label: 'Mitochondrial DNA',
    page: 'mtDNA analysis',
    withProject: false,
  },
];

const FamilyVariantSummaryPage: React.FC = () => {
  const { familyId = '' } = useParams<{ familyId: string }>();
  const location = useLocation();
  // The project the link came with (the analysis pages pass theirs on), else the family's first.
  const projectIdParam = React.useMemo(
    () => new URLSearchParams(location.search).get('project_id') || undefined,
    [location.search]
  );
  // For the shared header: the pedigree, and what every family page warns of — an import
  // that left the family incomplete, and an assembly outside the validated scope.
  const {
    data: family,
    isError: familyFailed,
    error: familyError,
    refetch: refetchFamily,
  } = useQuery<
    Pick<FamilyOut, 'pedigree' | 'members' | 'projects' | 'metadata'>
  >({
    queryKey: ['family', familyId],
    queryFn: async () => {
      const res = await api.get(apiPath`/families/${familyId}`);
      return res.data;
    },
  });
  const {
    assemblyName,
    assemblyValidated,
    assemblyVersion,
    projectId,
    isLoading: referenceLoading,
    isError: referenceFailed,
    retry: retryReference,
  } = useFamilyReference(family?.projects, projectIdParam);
  const referenceReady =
    Boolean(family) && (!family?.projects?.length || !referenceLoading);
  // The same checks the header's links make (one read for both, once the project is known).
  // They wait for the reference, as the header's do, but not for a project it could not
  // resolve: without one they ask for the family as a whole, so the page never hangs.
  const presence = useFamilyDataPresence({
    familyId,
    projectId,
    ready: referenceReady,
  });

  // Without the family it is not known whether its import is complete, nor whether its
  // assembly is validated: the summary is not shown without those warnings (SAFE-14).
  if (familyFailed) {
    return (
      <FamilyLoadFailure
        kicker="Summary"
        what="Family"
        error={familyError}
        notFoundMessage="This family could not be found."
        onRetry={() => void refetchFamily()}
      />
    );
  }

  // Nor is it shown before the family and its reference are in, the header with them.
  if (!family || !referenceReady) {
    return (
      <PageState
        loading
        kicker="Summary"
        title="Loading variant summary"
        message="Loading the family and its reference."
      />
    );
  }

  const has: Record<SectionKey, boolean> = {
    'small-variants': presence.hasSmallVariants,
    'structural-variants': presence.hasStructuralVariants,
    'repeat-expansions': presence.hasRepeatExpansions,
    paraphase: presence.hasParaphase,
    'mitochondrial-dna': presence.hasMitoDna,
  };
  const failed = new Set(presence.failedChecks);
  const checkFailed: Record<SectionKey, boolean> = {
    'small-variants': failed.has('small variants'),
    'structural-variants': failed.has('structural variants'),
    'repeat-expansions': failed.has('repeat expansions'),
    paraphase: failed.has('Paraphase results'),
    'mitochondrial-dna': failed.has('mtDNA data'),
  };
  const shown = SECTIONS.filter((section) => has[section.key]);
  const projectSuffix = projectId
    ? `?project_id=${encodeURIComponent(projectId)}`
    : '';

  return (
    <div className="page-shell analysis-shell">
      <FamilyCaseHeader
        assemblyScope={{
          name: assemblyName,
          validated: assemblyValidated,
          unavailable: referenceFailed,
          onRetry: retryReference,
        }}
        assemblyLabel={formatResolvedReferenceLabel(
          { assemblyName, assemblyVersion, isError: referenceFailed },
          'Not linked'
        )}
        kicker="Variant summary"
        familyId={familyId}
        family={family}
        projectId={projectId}
        current="variant-summary"
      />

      {/* The header's links read the same checks: both start in this render, so a check that
          failed is not asked again when one of them mounts, and the sections stay put. */}
      {!presence.loaded ? (
        <p className="analysis-count" role="status">
          Checking which variant data this family has…
        </p>
      ) : null}

      {presence.loaded && shown.length ? (
        <nav className="analysis-nav" aria-label="Variant summary sections">
          <a href="#overview">Overview</a>
          {shown.map((section) => (
            <a key={section.key} href={`#${section.key}`}>
              {section.label}
            </a>
          ))}
        </nav>
      ) : null}

      {presence.loaded ? (
        <section id="overview" className="analysis-panel space-y-4">
          <h2 className="section-title">What this family has</h2>
          <div className="analysis-results-card overflow-x-auto">
            <table
              className="analysis-table"
              aria-label="Variant data of this family"
            >
              <thead>
                <tr>
                  <th>Data</th>
                  <th>In this family</th>
                  <th>Analysis page</th>
                </tr>
              </thead>
              <tbody>
                {SECTIONS.map((section) => (
                  <tr key={section.key}>
                    <td>{section.label}</td>
                    <td>
                      {checkFailed[section.key] ? (
                        <a href={`#${section.key}`}>
                          Could not be checked: see below
                        </a>
                      ) : has[section.key] ? (
                        <a href={`#${section.key}`}>Summarised below</a>
                      ) : (
                        'None'
                      )}
                    </td>
                    <td>
                      {has[section.key] ? (
                        <Link
                          to={routePath`/families/${familyId}/${section.key}${raw(section.withProject ? projectSuffix : '')}`}
                        >
                          {section.page}
                        </Link>
                      ) : (
                        '–'
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="analysis-count">
            The WisecondorX and QDNAseq segments, coverage and haplotypes are
            tracks of the genome view, not variants, and are not summarised
            here.
          </p>
        </section>
      ) : null}

      {has['small-variants'] ? (
        <VariantSummarySmallVariants
          familyId={familyId}
          projectId={projectId}
        />
      ) : null}
      {has['structural-variants'] ? (
        <VariantSummaryStructuralVariants familyId={familyId} />
      ) : null}
      {has['repeat-expansions'] ? (
        <RepeatExpansionSummary familyId={familyId} projectId={projectId} />
      ) : null}
      {has.paraphase ? (
        <ParaphaseSummary familyId={familyId} projectId={projectId} />
      ) : null}
      {has['mitochondrial-dna'] ? (
        <MitoDnaSummary familyId={familyId} projectId={projectId} />
      ) : null}
    </div>
  );
};

export default FamilyVariantSummaryPage;
