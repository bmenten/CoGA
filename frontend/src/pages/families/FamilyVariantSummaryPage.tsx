import React from 'react';
import { useLocation, useNavigate, useParams } from 'react-router';
import { useQuery } from '@tanstack/react-query';
import api from '../../lib/api';
import PageState from '../../components/PageState';
import FamilyLoadFailure from '../../components/FamilyLoadFailure';
import FamilyCaseHeader from './FamilyCaseHeader';
import { apiPath } from '../../lib/apiPath';
import type { FamilyOut } from '../../lib/apiSchema.generated';
import {
  formatResolvedReferenceLabel,
  useFamilyReference,
} from '../../lib/reference';
import { useFamilyDataPresence } from './familyDataPresence';
import VariantSummaryNipt from './VariantSummaryNipt';
import VariantSummarySmallVariants from './VariantSummarySmallVariants';
import { MONOGENIC_NIPT_ANALYSIS_TYPE } from './niptClassification';
import VariantSummaryStructuralVariants from './VariantSummaryStructuralVariants';
import {
  MitoDnaSummary,
  ParaphaseSummary,
  RepeatExpansionSummary,
} from './VariantSummaryOtherCalls';

/**
 * The family's variant summary: every kind of variant data the family holds, one tab each.
 * A row of tabs names the kinds it has: small variants, structural variants, repeat
 * expansions, Paraphase and mtDNA, and, first, NIPT-M for a monogenic NIPT family (its plasma's
 * sites as the NIPT analysis classes them); the open one's section is shown and the others hidden. A
 * section loads when its tab is first opened and stays loaded, so switching back keeps its
 * view. A kind the family lacks has no tab; one whose check failed has one, so its section can
 * say for itself. The open tab is in the URL's hash (#structural-variants), so a reload
 * or a shared link opens it again.
 */

type SectionKey =
  | 'nipt-m'
  | 'small-variants'
  | 'structural-variants'
  | 'repeat-expansions'
  | 'paraphase'
  | 'mitochondrial-dna';

const SECTIONS: { key: SectionKey; label: string }[] = [
  { key: 'nipt-m', label: 'NIPT-M' },
  { key: 'small-variants', label: 'Small variants (SNV/indel)' },
  { key: 'structural-variants', label: 'Structural variants' },
  { key: 'repeat-expansions', label: 'Repeat expansions' },
  { key: 'paraphase', label: 'Paraphase' },
  { key: 'mitochondrial-dna', label: 'Mitochondrial DNA' },
];

const tabId = (key: SectionKey) => `variant-summary-tab-${key}`;
const panelId = (key: SectionKey) => `variant-summary-panel-${key}`;

const FamilyVariantSummaryPage: React.FC = () => {
  const { familyId = '' } = useParams<{ familyId: string }>();
  const location = useLocation();
  const navigate = useNavigate();
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

  const isMonogenicNipt =
    (family?.metadata as { analysis_type?: string } | null | undefined)?.analysis_type ===
    MONOGENIC_NIPT_ANALYSIS_TYPE;
  const has: Record<SectionKey, boolean> = {
    'nipt-m': isMonogenicNipt,
    'small-variants': presence.hasSmallVariants,
    'structural-variants': presence.hasStructuralVariants,
    'repeat-expansions': presence.hasRepeatExpansions,
    paraphase: presence.hasParaphase,
    'mitochondrial-dna': presence.hasMitoDna,
  };
  // The tabs wait for every check, so the first tab does not change as the checks come in.
  const shown = presence.loaded
    ? SECTIONS.filter((section) => has[section.key])
    : [];
  // The tab the hash names, if the family has that kind; else the first.
  const fromHash = location.hash.slice(1);
  const selected =
    shown.find((section) => section.key === fromHash)?.key ?? shown[0]?.key;
  // The tabs opened before: their sections stay mounted (hidden), so they are not loaded
  // again and keep their view. One never opened is not loaded at all.
  const [opened, setOpened] = React.useState<ReadonlySet<SectionKey>>(
    () => new Set()
  );
  const tabRefs = React.useRef<
    Partial<Record<SectionKey, HTMLButtonElement | null>>
  >({});

  const selectTab = (key: SectionKey) => {
    if (key === selected) return;
    setOpened((before) => {
      const after = new Set(before).add(key);
      if (selected) after.add(selected);
      return after;
    });
    // Replaced, not pushed: Back leaves the page rather than stepping through its tabs.
    navigate({ search: location.search, hash: key }, { replace: true });
  };

  // The tabs' keys (WAI-ARIA tabs, manual activation): the arrows, Home and End move the
  // focus between tabs; Enter or Space opens the focused one, as a click, which the UI
  // telemetry records like any other.
  const onTabKeyDown = (
    event: React.KeyboardEvent<HTMLButtonElement>,
    index: number
  ) => {
    const last = shown.length - 1;
    const target =
      event.key === 'ArrowRight'
        ? index === last
          ? 0
          : index + 1
        : event.key === 'ArrowLeft'
          ? index === 0
            ? last
            : index - 1
          : event.key === 'Home'
            ? 0
            : event.key === 'End'
              ? last
              : null;
    if (target === null) return;
    event.preventDefault();
    tabRefs.current[shown[target].key]?.focus();
  };

  // Without the family it is not known whether its import is complete, nor whether its
  // assembly is validated: the summary is not shown without those warnings.
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

  const sectionOf = (key: SectionKey) => {
    switch (key) {
      case 'nipt-m':
        return <VariantSummaryNipt familyId={familyId} projectId={projectId} />;
      case 'small-variants':
        return (
          <VariantSummarySmallVariants
            familyId={familyId}
            projectId={projectId}
          />
        );
      case 'structural-variants':
        return <VariantSummaryStructuralVariants familyId={familyId} />;
      case 'repeat-expansions':
        return (
          <RepeatExpansionSummary familyId={familyId} projectId={projectId} />
        );
      case 'paraphase':
        return <ParaphaseSummary familyId={familyId} projectId={projectId} />;
      case 'mitochondrial-dna':
        return <MitoDnaSummary familyId={familyId} projectId={projectId} />;
    }
  };

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
          failed is not asked again when one of them mounts. */}
      {!presence.loaded ? (
        <p className="analysis-count" role="status">
          Checking which variant data this family has…
        </p>
      ) : null}

      {presence.loaded && selected ? (
        <div
          className="analysis-nav"
          role="tablist"
          aria-label="Variant data of this family"
        >
          {shown.map((section, index) => {
            const active = section.key === selected;
            const mounted = active || opened.has(section.key);
            return (
              <button
                key={section.key}
                ref={(element) => {
                  tabRefs.current[section.key] = element;
                }}
                type="button"
                role="tab"
                id={tabId(section.key)}
                aria-selected={active}
                aria-controls={mounted ? panelId(section.key) : undefined}
                tabIndex={active ? 0 : -1}
                className={`pill-toggle${active ? ' pill-toggle--active' : ''}`}
                onClick={() => selectTab(section.key)}
                onKeyDown={(event) => onTabKeyDown(event, index)}
              >
                {section.label}
              </button>
            );
          })}
        </div>
      ) : null}

      {presence.loaded && !selected ? (
        <p className="analysis-count">
          This family has no variant data to summarise.
        </p>
      ) : null}

      {shown.map((section) =>
        section.key === selected || opened.has(section.key) ? (
          <div
            key={section.key}
            id={panelId(section.key)}
            role="tabpanel"
            aria-labelledby={tabId(section.key)}
            hidden={section.key !== selected}
          >
            {sectionOf(section.key)}
          </div>
        ) : null
      )}

      {presence.loaded ? (
        <p className="analysis-count">
          The WisecondorX and QDNAseq segments, coverage and haplotypes are
          tracks of the genome view, not variants, and are not summarised here.
        </p>
      ) : null}
    </div>
  );
};

export default FamilyVariantSummaryPage;
