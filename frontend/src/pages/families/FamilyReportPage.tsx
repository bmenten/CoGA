import React, { useMemo, useState } from 'react';
import { Link, useLocation, useNavigate, useParams } from 'react-router';
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';

import api from '../../lib/api';
import ModalDialog from '../../components/ModalDialog';
import PageState from '../../components/PageState';
import FamilyPageHeader from './FamilyPageHeader';
import SignedFamilyReport from './SignedFamilyReport';
import type {
  ApiAnnotationManifest,
  ApiClassificationDrift,
  ApiClinicalAudit,
  ApiStructuralClassificationDriftItem,
} from '../../lib/apiTypes';
import {
  apiErrorMessage,
  useReportSignoutCheck,
  useReportSignouts,
  useSignedVersionDownload,
} from './reportSignoutQueries';
import {
  describeModuleVersions,
  describeReportSections,
  describeStructuralEvidenceChange,
  formatReportTime,
  parseSignedVersionParam,
  reportViewSearch,
} from './signedReportRecord';
import { formatResolvedReferenceLabel, useFamilyReference } from '../../lib/reference';
import { formatLocus } from './smallVariantResultUtils';
import {
  normalizeReviewClassification,
  REPORT_TAG_KEY,
  type FamilyMember,
  type SmallVariant,
  type SmallVariantFamily,
  type SmallVariantPage,
} from './smallVariantSearch';
import { structuralVariantRowKey, type StructuralVariant } from './structuralVariantSearch';
import PipelineSettingsPanel, { pipelineSettingsFromMetadata } from './PipelineSettingsPanel';
import ReportSoftwareIdentity from './ReportSoftwareIdentity';
import { useReportBuild } from '../../lib/appVersion';
import {
  acmgClassificationLabel,
  buildSegregationSentence,
  buildStructuralSegregationSentence,
  buildStructuralVariantSentence,
  buildVariantSentence,
  collectInSilicoPredictions,
  collectReportCriteria,
  describeClinvar,
  describeGnomadFrequency,
  describeInSilico,
  describeStructuralFrequency,
  joinWithAnd,
} from './reportNarrative';
import { apiPath, raw } from '../../lib/apiPath';
import { REPORT_LIST_PAGE_SIZE, reportedListNotice, type ReportedListPage } from './reportedListCompleteness';

// The report's SV list: its variants, and what the page says about its completeness.
type StructuralReportPage = Omit<ReportedListPage, 'variants'> & { variants: StructuralVariant[] };

interface GeneHpoTerm {
  hpo_id?: string | null;
  label?: string | null;
}

interface GeneGenccAssertion {
  disease_title?: string | null;
  moi_title?: string | null;
}

interface GeneProfileResponse {
  symbol: string;
  display_name?: string | null;
  summary?: string | null;
  omim_gene_id?: string | null;
  panels?: Array<{ panel_id: string; name: string }>;
  extra?: {
    hpo_terms?: GeneHpoTerm[];
    gencc_assertions?: GeneGenccAssertion[];
    omim_diseases?: Array<string | { disease?: string | null; title?: string | null }>;
  };
}

interface FamilyHpoAnnotation {
  sample_id: string;
  hpo_id: string;
  label: string;
  status: 'present' | 'absent' | 'unknown';
}

const memberName = (member: FamilyMember): string =>
  member.role?.trim() ? `${member.role} (${member.sample_id})` : member.sample_id;

const STRUCTURAL_TYPE_HEADINGS: Record<string, string> = {
  DEL: 'Deletion',
  DUP: 'Duplication',
  INS: 'Insertion',
  INV: 'Inversion',
  BND: 'Breakend / translocation',
  TRA: 'Translocation',
  CNV: 'Copy-number variant',
};

const structuralTypeHeading = (variant: StructuralVariant): string =>
  STRUCTURAL_TYPE_HEADINGS[(variant.type || '').trim().toUpperCase()] ||
  (variant.type ? `${variant.type} structural variant` : 'Structural variant');

const uniqueGeneSymbols = (variants: SmallVariant[]): string[] =>
  Array.from(
    new Set(
      variants
        .map((variant) => variant.gene?.trim())
        .filter((gene): gene is string => Boolean(gene)),
    ),
  );

const omimDiseaseTitles = (profile?: GeneProfileResponse): string[] => {
  const entries = profile?.extra?.omim_diseases ?? [];
  return entries
    .map((entry) => (typeof entry === 'string' ? entry : entry.disease || entry.title || ''))
    .map((value) => value.trim())
    .filter(Boolean);
};

const genccDiseases = (profile?: GeneProfileResponse): string[] =>
  Array.from(
    new Set(
      (profile?.extra?.gencc_assertions ?? [])
        .map((entry) => entry.disease_title?.trim())
        .filter((value): value is string => Boolean(value)),
    ),
  );

const modesOfInheritance = (profile?: GeneProfileResponse): string[] =>
  Array.from(
    new Set(
      (profile?.extra?.gencc_assertions ?? [])
        .map((entry) => entry.moi_title?.trim())
        .filter((value): value is string => Boolean(value)),
    ),
  );

// What moved in the evidence of an SV/CNV classification, in the words of its inputs.
const describeStructuralDrift = (item: ApiStructuralClassificationDriftItem): string => {
  if (item.status === 'variant_missing') return 'no longer present in the dataset';
  if (item.status === 'unknown') return 'its frozen evidence cannot be compared';
  return (
    describeStructuralEvidenceChange(item.changed, item.evidence_from, item.evidence_to) ??
    (item.changed.includes('annotations') ? 'annotation changed' : 'evidence changed')
  );
};

// How the live report relates to the latest signed version. It is never the signed report,
// whatever the state: only the signed version, rendered from its record, is (#508).
type SignedState =
  | 'loading'
  | 'none'
  | 'checking'
  | 'matches'
  | 'changed'
  | 'unverified'
  | 'unknown';

/**
 * The live report: current data, and where the case is signed out. Labelled on screen and in
 * print as not the signed version.
 */
const LiveFamilyReport: React.FC = () => {
  const { familyId } = useParams<{ familyId: string }>();
  const location = useLocation();
  const navigate = useNavigate();
  const preferredProjectId = useMemo(
    () => new URLSearchParams(location.search).get('project_id') || undefined,
    [location.search],
  );

  const {
    data: family,
    isLoading: familyLoading,
    isError: familyFailed,
    refetch: refetchFamily,
  } = useQuery<SmallVariantFamily>({
    queryKey: ['family', familyId],
    enabled: Boolean(familyId),
    queryFn: async () => {
      const res = await api.get(apiPath`/families/${familyId}`);
      return res.data as SmallVariantFamily;
    },
  });

  const members = useMemo<FamilyMember[]>(
    () => ((family?.members as FamilyMember[] | undefined) ?? []).filter((m) => m.active !== false),
    [family],
  );

  const {
    speciesName,
    assemblyName,
    assemblyValidated,
    assemblyVersion,
    projectId,
    isLoading: referenceLoading,
    isError: referenceFailed,
    retry: retryReference,
  } = useFamilyReference(family?.projects as string[] | undefined, preferredProjectId);

  const referenceLabel = formatResolvedReferenceLabel(
    { speciesName, assemblyName, assemblyVersion, isError: referenceFailed },
    'Reference not linked',
  );

  const variantQueryReady = Boolean(
    familyId && family && (!family.projects?.length || projectId),
  );

  const reportQueryString = useMemo(() => {
    const params = new URLSearchParams();
    params.set('review_tag', REPORT_TAG_KEY);
    // Every reported variant, up to the API's page maximum; a list holding more says so
    // (reportedListNotice) instead of ending without a trace at the page size.
    params.set('page_size', String(REPORT_LIST_PAGE_SIZE));
    if (projectId) params.set('project_id', projectId);
    return params.toString();
  }, [projectId]);

  const {
    data: reportPage,
    isLoading: variantsLoading,
    isError,
    refetch: refetchReportVariants,
  } = useQuery<SmallVariantPage>({
    queryKey: ['family', familyId, 'report-variants', reportQueryString],
    enabled: variantQueryReady,
    queryFn: async () => {
      const res = await api.get(apiPath`/families/${familyId}/small-variants?${raw(reportQueryString)}`);
      return res.data as SmallVariantPage;
    },
  });

  const variants = useMemo(() => reportPage?.variants ?? [], [reportPage]);

  const {
    data: structuralReportPage,
    isLoading: structuralLoading,
    isError: structuralFailed,
    refetch: refetchStructural,
  } = useQuery<StructuralReportPage>({
    queryKey: ['family', familyId, 'report-structural-variants', reportQueryString],
    enabled: variantQueryReady,
    queryFn: async () => {
      const res = await api.get(apiPath`/families/${familyId}/structural-variants?${raw(reportQueryString)}`);
      return res.data as StructuralReportPage;
    },
  });
  const structuralVariants = useMemo(
    () => structuralReportPage?.variants ?? [],
    [structuralReportPage],
  );

  const geneSymbols = useMemo(() => {
    const symbols = new Set(uniqueGeneSymbols(variants));
    structuralVariants.forEach((variant) => {
      if (variant.gene?.trim()) symbols.add(variant.gene.trim());
    });
    return Array.from(symbols);
  }, [variants, structuralVariants]);

  const geneProfileQueries = useQueries({
    queries: geneSymbols.map((symbol) => ({
      queryKey: ['gene-profile', symbol, familyId, projectId || null],
      queryFn: async () => {
        const res = await api.get('/genes/profile', {
          params: { symbol, family_id: familyId, project_id: projectId },
        });
        return res.data as GeneProfileResponse;
      },
      staleTime: 5 * 60 * 1000,
    })),
  });

  const geneProfiles = useMemo(() => {
    const map = new Map<string, GeneProfileResponse>();
    geneProfileQueries.forEach((query) => {
      if (query.data) map.set(query.data.symbol, query.data);
    });
    return map;
  }, [geneProfileQueries]);
  // A gene whose profile could not be loaded has no description on record as far as this
  // page knows: said as such, not as "no curated description" (#605).
  const failedGeneProfiles = useMemo(
    () => new Set(geneSymbols.filter((_symbol, index) => geneProfileQueries[index]?.isError)),
    [geneSymbols, geneProfileQueries],
  );

  const {
    data: hpoAnnotations = [],
    isError: hpoFailed,
    refetch: refetchHpo,
  } = useQuery<FamilyHpoAnnotation[]>({
    queryKey: ['family', familyId, 'hpo'],
    enabled: Boolean(familyId),
    queryFn: async () => {
      const res = await api.get(apiPath`/families/${familyId}/hpo`);
      return res.data as FamilyHpoAnnotation[];
    },
  });

  // Provenance footer: which annotation/reference modules + versions backed the report.
  const {
    data: manifest,
    isError: manifestFailed,
    refetch: refetchManifest,
  } = useQuery<ApiAnnotationManifest>({
    queryKey: ['family', familyId, 'annotation-manifest'],
    enabled: Boolean(familyId),
    queryFn: async () =>
      (await api.get(apiPath`/families/${familyId}/annotation-manifest`)).data as ApiAnnotationManifest,
  });
  // The moment the report was produced (becomes the frozen sign-out time in Phase 3).
  const generatedAt = useMemo(() => new Date(), []);
  // The build that renders the report, named in its footer (TF-15 §1).
  const reportBuild = useReportBuild();

  // Evidence drift: classifications whose backing annotation changed since they
  // were made — a sign-out guardrail against stale interpretations.
  const { data: drift, isError: driftFailed, refetch: refetchDrift } = useQuery<ApiClassificationDrift>({
    queryKey: ['family', familyId, 'classification-drift'],
    enabled: Boolean(familyId),
    queryFn: async () =>
      (await api.get(apiPath`/families/${familyId}/classification-drift`)).data as ApiClassificationDrift,
  });
  // The small variants' drift and the structural variants' / CNVs' drift, in one banner.
  const structuralDrift = drift?.structural?.drifted ?? [];
  const driftCount = (drift?.drifted_count ?? 0) + (drift?.structural?.drifted_count ?? 0);

  // Immutable clinical audit trail (who classified / tagged / annotated what, when).
  const { data: audit, isError: auditFailed, refetch: refetchAudit } = useQuery<ApiClinicalAudit>({
    queryKey: ['family', familyId, 'clinical-audit'],
    enabled: Boolean(familyId),
    queryFn: async () =>
      (await api.get(apiPath`/families/${familyId}/clinical-audit`)).data as ApiClinicalAudit,
  });

  // Case sign-out: the frozen, versioned, content-hashed report record. This page is never
  // it; a signed version is rendered from its record by SignedFamilyReport.
  const queryClient = useQueryClient();
  const {
    data: signouts,
    isError: signoutsFailed,
    isPending: signoutsPending,
    refetch: refetchSignouts,
  } = useReportSignouts(familyId);

  // Does the live content below still match the latest signed version? It says so, and
  // what changed, but even a match does not make this page the signed report (#508).
  const latestSignout = signouts?.latest ?? null;
  const { data: signoutCheck, isError: signoutCheckFailed } = useReportSignoutCheck(
    familyId,
    latestSignout?.version ?? null,
  );
  // A sign-out record that could not be loaded leaves it unknown whether the case is
  // signed: the page is then neither a draft nor the signed report (#605).
  // Still loading, it is not known either: not yet a draft (#605).
  const signedState: SignedState = signoutsFailed
    ? 'unknown'
    : signoutsPending
      ? 'loading'
      : !latestSignout
    ? 'none'
    : signoutCheckFailed
      ? 'unverified'
      : !signoutCheck
        ? 'checking'
        : signoutCheck.version !== latestSignout.version ||
            typeof signoutCheck.matches !== 'boolean'
          ? 'unverified'
          : signoutCheck.matches
            ? 'matches'
            : 'changed';
  // Every printout of this page says it is not the signed report: even content that matches
  // the latest signed version is drawn from current data, the gene and phenotype context
  // included, which the signed record does not hold.
  const signedNotice =
    signedState === 'unknown'
      ? 'The sign-out record could not be loaded — do not use as the signed report.'
      : signedState === 'loading'
        ? 'The sign-out record is still loading — do not use as the signed report.'
      : signedState === 'none'
      ? 'Draft — this report has not been signed.'
      : signedState === 'changed'
        ? `Not the signed report — the content differs from signed version ${latestSignout?.version}.`
        : signedState === 'matches'
          ? `Not the signed report — this is the live report. Print signed version ${latestSignout?.version} from its record.`
          : `Not verified against signed version ${latestSignout?.version} — do not use as the signed report.`;
  // The parts of the report that could not be loaded. Each says so where it belongs, and
  // together they head every printed page: a printout without them used to read as
  // complete, their sections as "none" (#605).
  const failedParts = [
    failedGeneProfiles.size
      ? `the description${failedGeneProfiles.size === 1 ? '' : 's'} of ${joinWithAnd(Array.from(failedGeneProfiles))}`
      : null,
    hpoFailed ? 'the family’s HPO terms' : null,
    driftFailed ? 'the evidence-drift check' : null,
    auditFailed ? 'the audit trail' : null,
    manifestFailed ? 'the annotation provenance' : null,
    // A printout that cannot name the build that produced it is not complete either.
    reportBuild.failed ? 'the software version' : null,
  ].filter((part): part is string => Boolean(part));
  const retryFailedParts = () => {
    geneProfileQueries.forEach((query) => {
      if (query.isError) void query.refetch();
    });
    if (hpoFailed) void refetchHpo();
    if (driftFailed) void refetchDrift();
    if (auditFailed) void refetchAudit();
    if (manifestFailed) void refetchManifest();
    if (reportBuild.failed) reportBuild.retry();
  };
  const incompleteNotice = failedParts.length
    ? `Incomplete — ${joinWithAnd(failedParts)} could not be loaded, so this printout does not show the whole report.`
    : null;
  // A list of reported variants is not the whole list when the search behind it read a
  // capped candidate window, or when it holds more variants than the page it came in:
  // said on screen and in print, so a printed report cannot pass for the whole list.
  const reportListNotice = reportedListNotice([
    { label: 'small variants', page: reportPage },
    { label: 'structural variants', page: structuralReportPage },
  ]);
  const printNotice = [incompleteNotice, reportListNotice, signedNotice].filter(Boolean).join(' ') || null;

  // Override dialogs. Each gate is acknowledged with a reason that is frozen into the
  // signed record: evidence drift first, then a failing / unverifiable Sample QC, then an
  // incomplete import. Each dialog keeps the attempt its gate refused (`vars`), so the
  // acknowledgements already given travel on with the next one.
  type SignOutVars = {
    acknowledgeDrift: boolean;
    driftReason?: string;
    acknowledgeQc?: boolean;
    qcReason?: string;
    acknowledgeImportIncomplete?: boolean;
    importReason?: string;
  };
  const [driftGate, setDriftGate] = useState<{ message: string; vars: SignOutVars } | null>(
    null,
  );
  const [driftReason, setDriftReason] = useState('');
  const [qcGate, setQcGate] = useState<{
    message: string;
    vars: SignOutVars;
    summary?: { overall_status?: string; messages?: string[] };
  } | null>(null);
  const [qcReason, setQcReason] = useState('');
  const [importGate, setImportGate] = useState<{
    message: string;
    vars: SignOutVars;
    failed: string[];
    imported: string[];
    jobId: string | null;
  } | null>(null);
  const [importReason, setImportReason] = useState('');
  const [signOutError, setSignOutError] = useState<string | null>(null);

  const closeGates = () => {
    setDriftGate(null);
    setQcGate(null);
    setImportGate(null);
  };

  const signOut = useMutation({
    mutationFn: async (vars: SignOutVars) =>
      (
        await api.post(apiPath`/families/${familyId}/report/sign-out`, {
          acknowledge_drift: vars.acknowledgeDrift,
          drift_acknowledgement_reason: vars.driftReason,
          acknowledge_qc: vars.acknowledgeQc ?? false,
          qc_acknowledgement_reason: vars.qcReason,
          acknowledge_import_incomplete: vars.acknowledgeImportIncomplete ?? false,
          import_incomplete_acknowledgement_reason: vars.importReason,
        })
      ).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['family', familyId, 'report-signouts'] });
      queryClient.invalidateQueries({ queryKey: ['family', familyId, 'report-signout-check'] });
      queryClient.invalidateQueries({ queryKey: ['family', familyId, 'clinical-audit'] });
    },
  });

  const attemptSignOut = async (vars: SignOutVars) => {
    setSignOutError(null);
    try {
      const signed = (await signOut.mutateAsync(vars)) as { version?: unknown } | undefined;
      closeGates();
      setDriftReason('');
      setQcReason('');
      setImportReason('');
      // Show the signer what was signed: the new version, rendered from its record.
      if (typeof signed?.version === 'number') {
        navigate({ search: reportViewSearch(location.search, { version: signed.version }) });
      }
    } catch (error) {
      const response = (
        error as { response?: { status?: number; data?: { detail?: unknown } } }
      ).response;
      if (response?.status !== 409) {
        // Anything but a gate is a failure the reviewer must see: the report was NOT
        // signed out.
        closeGates();
        setSignOutError(apiErrorMessage(error, 'The report could not be signed out'));
        return;
      }
      const detail = response.data?.detail;
      const gate =
        detail && typeof detail === 'object' ? (detail as { gate?: string }).gate : undefined;
      closeGates();
      // Off the validated assembly scope: a refusal with no override, never the drift
      // dialog that a plain 409 would open (#515).
      if (gate === 'assembly_scope') {
        setSignOutError(
          (detail as { message?: string }).message ||
            'This family is outside the validated assembly scope.',
        );
        return;
      }
      // The family's data is being written: an import queued or running, or another write
      // of its variants. No override either; the reviewer signs out once it has finished.
      if (gate === 'import_in_progress' || gate === 'variant_writes_in_progress') {
        setSignOutError(
          (detail as { message?: string }).message ||
            'This family’s data is being written. Sign out once that has finished.',
        );
        return;
      }
      // A failing Sample QC returns a structured detail (gate discriminator + a failure
      // summary); it needs an acknowledge-WITH-REASON override, so open the dialog.
      if (gate === 'sample_qc') {
        const qc = detail as {
          message?: string;
          qc_summary?: { overall_status?: string; messages?: string[] };
        };
        setQcGate({
          message: qc.message || 'Sample-integrity QC failed.',
          summary: qc.qc_summary,
          vars,
        });
        return;
      }
      // An import that partly failed: structured like the QC gate, naming what the import
      // left out, and acknowledged the same way.
      if (gate === 'import_incomplete') {
        const incomplete = detail as {
          message?: string;
          import_incomplete?: {
            failed_datasets?: string[];
            imported_datasets?: string[];
            job_id?: string | null;
          } | null;
        };
        setImportGate({
          message: incomplete.message || 'The family’s import is incomplete.',
          failed: incomplete.import_incomplete?.failed_datasets ?? [],
          imported: incomplete.import_incomplete?.imported_datasets ?? [],
          jobId: incomplete.import_incomplete?.job_id ?? null,
          vars,
        });
        return;
      }
      // A gate this page does not know is a refusal, never the drift override below.
      if (gate !== undefined) {
        setSignOutError(
          (detail as { message?: string }).message || 'The report could not be signed out.',
        );
        return;
      }
      // Evidence-drift gate (plain string detail): acknowledge WITH a reason, like QC.
      setDriftGate({
        message:
          typeof detail === 'string'
            ? detail
            : 'Evidence has changed since some classifications were made.',
        vars,
      });
    }
  };

  const handleSignOut = () =>
    attemptSignOut({
      acknowledgeDrift: false,
      acknowledgeQc: false,
      acknowledgeImportIncomplete: false,
    });

  const submitDriftAcknowledgement = async () => {
    const reason = driftReason.trim();
    if (!reason || !driftGate) {
      return;
    }
    await attemptSignOut({ ...driftGate.vars, acknowledgeDrift: true, driftReason: reason });
  };

  const submitQcAcknowledgement = async () => {
    const reason = qcReason.trim();
    if (!reason || !qcGate) {
      return;
    }
    await attemptSignOut({ ...qcGate.vars, acknowledgeQc: true, qcReason: reason });
  };

  const submitImportAcknowledgement = async () => {
    const reason = importReason.trim();
    if (!reason || !importGate) {
      return;
    }
    await attemptSignOut({
      ...importGate.vars,
      acknowledgeImportIncomplete: true,
      importReason: reason,
    });
  };

  // The frozen record itself, as stored at sign-out.
  const { download: downloadSignedVersion, error: downloadError } = useSignedVersionDownload(
    familyId ?? '',
  );

  const presentHpoTerms = useMemo(() => {
    const byId = new Map<string, string>();
    hpoAnnotations
      .filter((annotation) => annotation.status === 'present')
      .forEach((annotation) => byId.set(annotation.hpo_id, annotation.label));
    return byId;
  }, [hpoAnnotations]);

  if (!familyId) {
    return <PageState kicker="Report" title="Family not specified" />;
  }

  if (
    familyLoading ||
    (variantQueryReady && (variantsLoading || structuralLoading)) ||
    referenceLoading
  ) {
    return (
      <PageState
        kicker="Report"
        title="Preparing the family report"
        message="Gathering reported variants, gene context and phenotype data."
      />
    );
  }

  // Without the family the report queries never run: it used to render as a report of
  // 0 small and 0 structural variants (#605).
  if (familyFailed) {
    return (
      <PageState
        kicker="Report"
        title="Report could not be loaded"
        message="The family could not be loaded, so the report cannot be prepared. This is not a report without variants."
        action={
          <button type="button" className="button-secondary" onClick={() => void refetchFamily()}>
            Retry
          </button>
        }
      />
    );
  }

  // The project catalogue failed. The reported variants are read within the family's
  // project, so without it the report would render with none (#608).
  if (referenceFailed) {
    return (
      <PageState
        kicker="Report"
        title="Report could not be prepared"
        message="The family's project, and with it the reference assembly, could not be loaded, so the reported variants cannot be retrieved. This is not a report without variants."
        action={
          <button type="button" className="button-secondary" onClick={retryReference}>
            Retry
          </button>
        }
      />
    );
  }

  // Either list of reported variants: the SVs used to drop out of the report unnoticed,
  // and the introduction to count none (#605).
  if (isError || structuralFailed) {
    return (
      <PageState
        kicker="Report"
        title="Report could not be loaded"
        message={`The reported ${isError ? 'small' : 'structural'} variants for this family could not be retrieved. This is not a report without them.`}
        action={
          <div className="inline-actions">
            <button
              type="button"
              className="button-secondary"
              onClick={() => {
                if (isError) void refetchReportVariants();
                if (structuralFailed) void refetchStructural();
              }}
            >
              Retry
            </button>
            <Link to={`/families/${familyId}`} className="button-secondary">
              Back to family
            </Link>
          </div>
        }
      />
    );
  }

  return (
    <div className="page-shell report-page space-y-6">
      {printNotice ? (
        // Printed at the top of every page that is not the verified signed record, so a
        // printout can never pass for the signed report (#508).
        <p className="report-print-notice print-only">{printNotice}</p>
      ) : null}
      <FamilyPageHeader
        assemblyScope={{
          name: assemblyName,
          validated: assemblyValidated,
          unavailable: referenceFailed,
          onRetry: retryReference,
        }}
        kicker={
          latestSignout
            ? 'Clinical report — live, not the signed version'
            : signoutsFailed || signoutsPending
              ? 'Clinical report — live'
              : 'Clinical report — draft, not signed'
        }
        familyId={familyId}
        family={family}
        projectId={projectId}
        className="report-header"
        actions={
          <div className="report-header-actions no-print">
            <button type="button" className="form-button" onClick={() => window.print()}>
              Print report
            </button>
            <button
              type="button"
              className="form-button"
              onClick={handleSignOut}
              // Off the validated scope the server refuses anyway; do not offer it (#515).
              // Nor while the sign-out record is unknown: this may already be signed (#605).
              disabled={
                signOut.isPending || assemblyValidated === false || signoutsFailed || signoutsPending
              }
              title={
                assemblyValidated === false
                  ? 'Not validated for clinical use — this report cannot be signed out'
                  : signoutsFailed
                    ? 'The sign-out record could not be loaded'
                    : signoutsPending
                      ? 'The sign-out record is still loading'
                      : undefined
              }
            >
              {signOut.isPending
                ? 'Signing out…'
                : signouts?.latest
                  ? 'Amend sign-out'
                  : 'Sign out report'}
            </button>
          </div>
        }
      >
        <p className="report-header-meta">{referenceLabel}</p>
      </FamilyPageHeader>

      {signOutError ? (
        <section className="surface-card report-signout-error no-print" role="alert">
          <p className="report-paragraph">
            <strong>Not signed out.</strong> {signOutError}
          </p>
        </section>
      ) : null}

      {driftGate ? (
        <ModalDialog
          label="Evidence drift acknowledgement required"
          className="modal-surface surface-card report-qc-ack-modal"
          closeOnBackdrop={false}
          discardMessage="Discard the reason you have typed?"
          onClose={() => {
            setDriftGate(null);
            setDriftReason('');
          }}
        >
            <h2 className="report-paragraph">
              <strong>Evidence drift — acknowledgement required</strong>
            </h2>
            <p className="report-paragraph">{driftGate.message}</p>
            <p className="report-paragraph">
              To sign out anyway you must record a reason — it is frozen into the signed record.
            </p>
            <label className="report-footer-label" htmlFor="drift-ack-reason">
              Reason for signing out despite the evidence drift (required)
            </label>
            <textarea
              id="drift-ack-reason"
              className="variant-review-textarea"
              rows={3}
              value={driftReason}
              onChange={(event) => setDriftReason(event.target.value)}
            />
            <div className="inline-actions modal-actions">
              <button
                type="button"
                className="form-button"
                onClick={() => {
                  setDriftGate(null);
                  setDriftReason('');
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                className="form-button"
                disabled={!driftReason.trim() || signOut.isPending}
                onClick={submitDriftAcknowledgement}
              >
                Sign out anyway
              </button>
            </div>
        </ModalDialog>
      ) : null}

      {qcGate ? (
        <ModalDialog
          label="Sample-integrity QC acknowledgement required"
          className="modal-surface surface-card report-qc-ack-modal"
          closeOnBackdrop={false}
          discardMessage="Discard the reason you have typed?"
          onClose={() => {
            setQcGate(null);
            setQcReason('');
          }}
        >
            <h2 className="report-paragraph">
              <strong>Sample-integrity QC — acknowledgement required</strong>
            </h2>
            {/* The backend message describes the specific concern — a detected mismatch
                (fail) OR an asserted relationship that could not be verified (missing
                data). Render it verbatim so the reviewer attests to the right thing. */}
            <p className="report-paragraph">{qcGate.message}</p>
            <p className="report-paragraph">
              To sign out anyway you must record a reason — it is frozen into the signed record.
            </p>
            {qcGate.summary?.messages?.length ? (
              <ul>
                {qcGate.summary.messages.map((message, index) => (
                  <li key={index}>{message}</li>
                ))}
              </ul>
            ) : null}
            <label className="report-footer-label" htmlFor="qc-ack-reason">
              Reason for signing out despite the QC concern (required)
            </label>
            <textarea
              id="qc-ack-reason"
              className="variant-review-textarea"
              rows={3}
              value={qcReason}
              onChange={(event) => setQcReason(event.target.value)}
            />
            <div className="inline-actions modal-actions">
              <button
                type="button"
                className="form-button"
                onClick={() => {
                  setQcGate(null);
                  setQcReason('');
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                className="form-button"
                disabled={!qcReason.trim() || signOut.isPending}
                onClick={submitQcAcknowledgement}
              >
                Sign out anyway
              </button>
            </div>
        </ModalDialog>
      ) : null}

      {importGate ? (
        <ModalDialog
          label="Incomplete import acknowledgement required"
          className="modal-surface surface-card report-qc-ack-modal"
          closeOnBackdrop={false}
          discardMessage="Discard the reason you have typed?"
          onClose={() => {
            setImportGate(null);
            setImportReason('');
          }}
        >
            <h2 className="report-paragraph">
              <strong>Incomplete import — acknowledgement required</strong>
            </h2>
            {/* The backend message names what the import left out. Render it verbatim so
                the signer attests to the right thing. */}
            <p className="report-paragraph">{importGate.message}</p>
            <p className="report-paragraph">
              To sign out anyway you must record a reason — it is frozen into the signed record.
            </p>
            {importGate.failed.length || importGate.imported.length || importGate.jobId ? (
              <ul>
                {importGate.failed.length ? (
                  <li>Failed to import: {importGate.failed.join(', ')}</li>
                ) : null}
                {importGate.imported.length ? (
                  <li>Imported: {importGate.imported.join(', ')}</li>
                ) : null}
                {/* Its record holds each dataset's error. */}
                {importGate.jobId ? <li>Import job: {importGate.jobId}</li> : null}
              </ul>
            ) : null}
            <label className="report-footer-label" htmlFor="import-ack-reason">
              Reason for signing out despite the incomplete import (required)
            </label>
            <textarea
              id="import-ack-reason"
              className="variant-review-textarea"
              rows={3}
              value={importReason}
              onChange={(event) => setImportReason(event.target.value)}
            />
            <div className="inline-actions modal-actions">
              <button
                type="button"
                className="form-button"
                onClick={() => {
                  setImportGate(null);
                  setImportReason('');
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                className="form-button"
                disabled={!importReason.trim() || signOut.isPending}
                onClick={submitImportAcknowledgement}
              >
                Sign out anyway
              </button>
            </div>
        </ModalDialog>
      ) : null}

      {signoutsFailed ? (
        <section className="surface-card report-signout report-signout-unknown" role="alert">
          <p className="report-signout-line">
            <strong>The sign-out record could not be loaded,</strong> so it is not known whether this
            report has been signed out. Do not treat this page as a draft or as the signed report.{' '}
            <button type="button" className="button-link no-print" onClick={() => void refetchSignouts()}>
              Retry
            </button>
          </p>
        </section>
      ) : null}

      {reportListNotice ? (
        <section className="surface-card report-incomplete no-print" role="alert">
          <p className="report-paragraph">{reportListNotice}</p>
        </section>
      ) : null}

      {failedParts.length ? (
        <section className="surface-card report-incomplete no-print" role="alert">
          <p className="report-paragraph">
            <strong>Parts of this report could not be loaded:</strong> {joinWithAnd(failedParts)}.
            Each is marked where it belongs, and a printout says the report is incomplete.{' '}
            <button type="button" className="button-link" onClick={retryFailedParts}>
              Retry
            </button>
          </p>
        </section>
      ) : null}

      {latestSignout ? (
        // The latest signed version, next to what this page is: never that version, only a
        // comparison with it (#508). The version itself is rendered from its record.
        <section
          className={`surface-card report-signout report-signout-${
            signedState === 'matches' ? 'live' : signedState
          }`}
          role={signedState === 'changed' || signedState === 'unverified' ? 'alert' : undefined}
        >
          <p className="report-signout-line">
            This is the live report, not signed version {latestSignout.version}.
          </p>
          <p className="report-signout-status">
            Signed version {latestSignout.version} was signed out by{' '}
            <strong>{latestSignout.signed_out_by}</strong> on{' '}
            {formatReportTime(latestSignout.signed_out_at)}.{' '}
            {signedState === 'checking'
              ? `Checking this page against signed version ${latestSignout.version}…`
              : null}
            {signedState === 'matches'
              ? `This page still matches signed version ${latestSignout.version}.`
              : null}
            {signedState === 'changed' ? (
              <>
                <strong>⚠ Changed since sign-out:</strong>{' '}
                {describeReportSections(signoutCheck?.changed_sections ?? [])}. This page shows the
                current state, not signed version {latestSignout.version} — sign out again to
                issue a new version.
              </>
            ) : null}
            {signedState === 'unverified'
              ? `This page could not be checked against signed version ${latestSignout.version} — treat it as unsigned.`
              : null}
          </p>
          <p className="report-signout-actions no-print">
            <Link
              to={{ search: reportViewSearch(location.search, { version: latestSignout.version }) }}
              className="button-secondary"
            >
              View signed version {latestSignout.version}
            </Link>{' '}
            <button
              type="button"
              className="button-secondary"
              onClick={() => void downloadSignedVersion(latestSignout.version)}
            >
              Download signed version {latestSignout.version} (JSON)
            </button>
            {downloadError ? (
              <span className="report-signout-download-error" role="alert">
                {' '}
                {downloadError.message}
              </span>
            ) : null}
          </p>
        </section>
      ) : null}

      <section className="surface-card report-intro">
        <p className="report-paragraph">
          This report summarises {variants.length} small variant
          {variants.length === 1 ? '' : 's'} and {structuralVariants.length} structural variant
          {structuralVariants.length === 1 ? '' : 's'} selected for reporting (tagged{' '}
          <strong>report</strong>) in family <strong>{familyId}</strong>
          {members.length ? `, comprising ${members.map(memberName).join(', ')}` : ''}. Each variant
          is described together with its consequence and the ACMG/AMP criteria that motivated its
          selection.
        </p>
        <p className="report-disclaimer">
          ACMG/AMP classifications are decision support and must be confirmed by a qualified clinical
          scientist before clinical use.
        </p>
      </section>

      {driftFailed ? (
        <section className="surface-card report-drift" role="alert">
          <p className="report-drift-title">⚠ Evidence drift could not be checked</p>
          <p className="report-paragraph report-drift-lead">
            Whether the annotation behind a reported classification has changed since it was made
            is not known. Sign-out checks it again.
          </p>
        </section>
      ) : drift && driftCount > 0 ? (
        <section className="surface-card report-drift" role="alert">
          <p className="report-drift-title">
            ⚠ {driftCount} classification{driftCount === 1 ? '' : 's'}{' '}
            {driftCount === 1 ? 'has' : 'have'} evidence changes since being made
          </p>
          <p className="report-paragraph report-drift-lead">
            The evidence behind the following classification
            {driftCount === 1 ? '' : 's'} has changed since it was recorded. Re-review before
            sign-out.
          </p>
          <ul className="report-drift-list">
            {drift.drifted.map((item) => (
              <li key={item.variant_id}>
                <strong>{item.variant_id}</strong>
                {item.status === 'variant_missing'
                  ? ' — no longer present in the dataset'
                  : item.clinvar_from !== item.clinvar_to
                    ? ` — ClinVar ${item.clinvar_from || 'n/a'} → ${item.clinvar_to || 'n/a'}`
                    : ' — annotation set changed'}
                {item.classified_by ? (
                  <span className="report-drift-meta"> (classified by {item.classified_by})</span>
                ) : null}
              </li>
            ))}
            {structuralDrift.map((item) => (
              <li key={`sv:${item.variant_id}`}>
                <strong>{item.variant_id}</strong> (structural variant) —{' '}
                {describeStructuralDrift(item)}
                {item.classified_by ? (
                  <span className="report-drift-meta"> (classified by {item.classified_by})</span>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {variants.length === 0 && structuralVariants.length === 0 ? (
        <section className="surface-card report-empty">
          <p className="report-paragraph">
            No variants are currently tagged for reporting. Tag variants with the{' '}
            <strong>Report</strong> chip on the small-variant or structural-variant table to
            include them here.
          </p>
        </section>
      ) : (
        <>
        {variants.map((variant) => {
          const profile = variant.gene ? geneProfiles.get(variant.gene) : undefined;
          const criteria = collectReportCriteria(variant);
          const classification = acmgClassificationLabel(variant);
          const pointTotal = variant.review?.acmg?.point_total;
          const clinvarSentence = describeClinvar(variant);
          const segregation = buildSegregationSentence(variant, members);
          const predictions = collectInSilicoPredictions(variant);

          const geneHpoTerms = profile?.extra?.hpo_terms ?? [];
          const overlappingHpo = geneHpoTerms
            .map((term) => term.hpo_id?.trim())
            .filter((id): id is string => Boolean(id) && presentHpoTerms.has(id as string))
            .map((id) => ({ id, label: presentHpoTerms.get(id) || id }));
          const omim = omimDiseaseTitles(profile);
          const gencc = genccDiseases(profile);
          const moi = modesOfInheritance(profile);
          const diseases = Array.from(new Set([...omim, ...gencc]));

          return (
            <article key={variant._id} className="surface-card report-variant">
              <div className="report-variant-head">
                <h2 className="section-title">
                  {`${variant.gene || variant.gene_id || 'Intergenic variant'}${
                    variant.hgvsc ? ` ${variant.hgvsc}` : ''
                  }`}
                </h2>
                {classification ? (
                  <span className="table-chip report-classification-chip">
                    {classification}
                    {pointTotal !== undefined && pointTotal !== null
                      ? ` · ${pointTotal} pts`
                      : ''}
                  </span>
                ) : (
                  <span className="table-chip report-classification-chip report-classification-chip--none">
                    Not classified
                  </span>
                )}
              </div>

              <div className="report-section">
                <h3 className="report-subheading">Variant description</h3>
                <p className="report-paragraph">{buildVariantSentence(variant, members)}</p>
                <p className="report-paragraph">
                  The variant {describeGnomadFrequency(variant)}.{' '}
                  {clinvarSentence ? `${clinvarSentence} ` : ''}
                  {describeInSilico(variant)}
                </p>
                {segregation ? <p className="report-paragraph">{segregation}</p> : null}
              </div>

              <div className="report-section">
                <h3 className="report-subheading">Classification motivation</h3>
                {criteria.length ? (
                  <>
                    <p className="report-paragraph">
                      This variant was classified as{' '}
                      <strong>{classification || 'uncertain significance'}</strong> based on{' '}
                      {joinWithAnd(criteria.map((criterion) => criterion.code))}.
                    </p>
                    <ul className="report-criteria-list">
                      {criteria.map((criterion) => (
                        <li
                          key={criterion.code}
                          className={`report-criterion report-criterion--${criterion.direction}`}
                        >
                          <span className="report-criterion-code">{criterion.code}</span>
                          <span className="report-criterion-strength">{criterion.strengthLabel}</span>
                          <span className="report-criterion-text">
                            <strong>{criterion.name}.</strong> {criterion.description}
                            {criterion.evidence ? ` Evidence: ${criterion.evidence}` : ''}
                          </span>
                        </li>
                      ))}
                    </ul>
                    <p className="report-paragraph report-evidence-summary">
                      Supporting evidence: the variant {describeGnomadFrequency(variant)};{' '}
                      {clinvarSentence ? `${clinvarSentence.toLowerCase()} ` : 'no ClinVar assertion is available. '}
                      {predictions.length
                        ? `in silico predictors report ${joinWithAnd(
                            predictions.map((p) => `${p.label} ${p.value}`),
                          )}. `
                        : 'No in silico predictions were available. '}
                      {segregation
                        ? segregation.replace(/^Within the family, the variant is/, 'Segregation shows it is')
                        : ''}
                    </p>
                  </>
                ) : (
                  <p className="report-paragraph">
                    No ACMG/AMP criteria have been recorded for this variant. Open the ACMG
                    classification modal from the small-variant table to document the motivation.
                  </p>
                )}
              </div>

              <div className="report-section">
                <h3 className="report-subheading">Gene</h3>
                {profile?.summary ? (
                  <p className="report-paragraph">
                    <strong>{profile.display_name || variant.gene}</strong> — {profile.summary}
                  </p>
                ) : variant.gene && failedGeneProfiles.has(variant.gene) ? (
                  <p className="report-paragraph">
                    The description of <strong>{variant.gene}</strong> could not be loaded.
                  </p>
                ) : (
                  <p className="report-paragraph">
                    No curated description is available for{' '}
                    <strong>{variant.gene || 'this gene'}</strong>.
                  </p>
                )}
                {diseases.length ? (
                  <p className="report-paragraph">
                    Associated condition{diseases.length === 1 ? '' : 's'}:{' '}
                    {joinWithAnd(diseases)}
                    {moi.length ? ` (${joinWithAnd(moi)})` : ''}.
                  </p>
                ) : null}
                {profile?.panels?.length ? (
                  <p className="report-paragraph report-gene-panels">
                    Gene panels: {profile.panels.map((panel) => panel.name).join(', ')}.
                  </p>
                ) : null}
              </div>

              <div className="report-section">
                <h3 className="report-subheading">Phenotype (HPO)</h3>
                {hpoFailed ? (
                  <p className="report-paragraph">
                    The family&rsquo;s HPO terms could not be loaded, so the phenotype match is not
                    assessed.
                  </p>
                ) : presentHpoTerms.size === 0 ? (
                  <p className="report-paragraph">
                    No HPO phenotype terms have been recorded for this family.
                  </p>
                ) : overlappingHpo.length ? (
                  <p className="report-paragraph">
                    The patient phenotype overlaps the gene&rsquo;s known HPO associations for{' '}
                    {joinWithAnd(overlappingHpo.map((term) => `${term.label} (${term.id})`))},
                    supporting a phenotypic match.
                  </p>
                ) : variant.gene && failedGeneProfiles.has(variant.gene) ? (
                  <p className="report-paragraph">
                    The gene&rsquo;s HPO associations could not be loaded, so their overlap with the
                    recorded phenotype is not assessed.
                  </p>
                ) : (
                  <p className="report-paragraph">
                    The family&rsquo;s recorded phenotype (
                    {joinWithAnd(Array.from(presentHpoTerms.values()))}) does not directly overlap the
                    gene&rsquo;s annotated HPO terms; clinical correlation is advised.
                  </p>
                )}
              </div>

              {variant.review?.note ? (
                <div className="report-section">
                  <h3 className="report-subheading">Analyst note</h3>
                  <p className="report-paragraph report-note">{variant.review.note}</p>
                </div>
              ) : null}

              <p className="report-variant-locus">
                {formatLocus(variant)} · {variant.ref || '—'} → {variant.alt || '—'}
              </p>
            </article>
          );
        })}

        {structuralVariants.map((variant) => {
          const profile = variant.gene ? geneProfiles.get(variant.gene) : undefined;
          const classification = normalizeReviewClassification(
            variant.review?.classification,
            variant.review?.tags,
          );
          const segregation = buildStructuralSegregationSentence(variant, members);
          const geneHpoTerms = profile?.extra?.hpo_terms ?? [];
          const overlappingHpo = geneHpoTerms
            .map((term) => term.hpo_id?.trim())
            .filter((id): id is string => Boolean(id) && presentHpoTerms.has(id as string))
            .map((id) => ({ id, label: presentHpoTerms.get(id) || id }));
          const omim = omimDiseaseTitles(profile);
          const gencc = genccDiseases(profile);
          const moi = modesOfInheritance(profile);
          const diseases = Array.from(new Set([...omim, ...gencc]));

          return (
            <article key={`sv-${structuralVariantRowKey(variant)}`} className="surface-card report-variant">
              <div className="report-variant-head">
                <h2 className="section-title">
                  {`${structuralTypeHeading(variant)}${variant.gene ? ` — ${variant.gene}` : ''}`}
                </h2>
                {classification ? (
                  <span className="table-chip report-classification-chip">{classification}</span>
                ) : (
                  <span className="table-chip report-classification-chip report-classification-chip--none">
                    Not classified
                  </span>
                )}
              </div>

              <div className="report-section">
                <h3 className="report-subheading">Variant description</h3>
                <p className="report-paragraph">{buildStructuralVariantSentence(variant)}</p>
                <p className="report-paragraph">The variant {describeStructuralFrequency(variant)}.</p>
                {segregation ? <p className="report-paragraph">{segregation}</p> : null}
              </div>

              <div className="report-section">
                <h3 className="report-subheading">Gene</h3>
                {profile?.summary ? (
                  <p className="report-paragraph">
                    <strong>{profile.display_name || variant.gene}</strong> — {profile.summary}
                  </p>
                ) : variant.gene && failedGeneProfiles.has(variant.gene) ? (
                  <p className="report-paragraph">
                    The description of <strong>{variant.gene}</strong> could not be loaded.
                  </p>
                ) : (
                  <p className="report-paragraph">
                    No curated description is available for{' '}
                    <strong>{variant.gene || 'this region'}</strong>.
                  </p>
                )}
                {diseases.length ? (
                  <p className="report-paragraph">
                    Associated condition{diseases.length === 1 ? '' : 's'}: {joinWithAnd(diseases)}
                    {moi.length ? ` (${joinWithAnd(moi)})` : ''}.
                  </p>
                ) : null}
              </div>

              <div className="report-section">
                <h3 className="report-subheading">Phenotype (HPO)</h3>
                {hpoFailed ? (
                  <p className="report-paragraph">
                    The family&rsquo;s HPO terms could not be loaded, so the phenotype match is not
                    assessed.
                  </p>
                ) : presentHpoTerms.size === 0 ? (
                  <p className="report-paragraph">
                    No HPO phenotype terms have been recorded for this family.
                  </p>
                ) : overlappingHpo.length ? (
                  <p className="report-paragraph">
                    The patient phenotype overlaps the gene&rsquo;s known HPO associations for{' '}
                    {joinWithAnd(overlappingHpo.map((term) => `${term.label} (${term.id})`))},
                    supporting a phenotypic match.
                  </p>
                ) : variant.gene && failedGeneProfiles.has(variant.gene) ? (
                  <p className="report-paragraph">
                    The gene&rsquo;s HPO associations could not be loaded, so their overlap with the
                    recorded phenotype is not assessed.
                  </p>
                ) : (
                  <p className="report-paragraph">
                    The family&rsquo;s recorded phenotype does not directly overlap the
                    gene&rsquo;s annotated HPO terms; clinical correlation is advised.
                  </p>
                )}
              </div>

              {variant.review?.note ? (
                <div className="report-section">
                  <h3 className="report-subheading">Analyst note</h3>
                  <p className="report-paragraph report-note">{variant.review.note}</p>
                </div>
              ) : null}

              <p className="report-variant-locus">
                {variant.chr}:{variant.start.toLocaleString()}-{variant.end.toLocaleString()} ·{' '}
                {variant.type || 'SV'}
              </p>
            </article>
          );
        })}
        </>
      )}

      {auditFailed ? (
        <section className="surface-card report-audit" role="alert">
          <h2 className="report-audit-heading">Classification audit trail</h2>
          <p className="report-paragraph report-audit-lead">The audit trail could not be loaded.</p>
        </section>
      ) : audit?.events?.length ? (
        <section className="surface-card report-audit">
          <h2 className="report-audit-heading">Classification audit trail</h2>
          <p className="report-paragraph report-audit-lead">
            An immutable record of who classified, tagged or annotated each variant.
          </p>
          <ul className="report-audit-list">
            {audit.events.map((event) => (
              <li key={event.id} className="report-audit-item">
                <span className="report-audit-time">
                  {event.created_at.replace('T', ' ').slice(0, 16)} UTC
                </span>
                <span className="report-audit-summary">
                  {event.variant_id ? <strong>{event.variant_id}</strong> : null}{' '}
                  {event.summary || event.action}
                </span>
                <span className="report-audit-actor">{event.actor}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <PipelineSettingsPanel
        familyId={familyId}
        settings={pipelineSettingsFromMetadata(family?.metadata)}
        variant="report"
      />

      <footer className="surface-card report-footer">
        <p className="report-footer-timestamp">
          Report generated {generatedAt.toISOString().replace('T', ' ').slice(0, 16)} UTC
        </p>
        <ReportSoftwareIdentity reportBuild={reportBuild} />
        <p className="report-footer-versions">
          <span className="report-footer-label">Modules &amp; versions:</span>{' '}
          {manifestFailed
            ? 'could not be loaded'
            : (describeModuleVersions(manifest?.modules ?? []) ?? 'not recorded for this family')}
        </p>
      </footer>
    </div>
  );
};

/**
 * The family report. A case that has been signed out opens on its latest signed version,
 * rendered from the frozen record; `?version=N` opens signed version N and `?view=live` the
 * live report, which is where the case is signed out. A case never signed out opens on the
 * live report, a draft.
 */
const FamilyReportPage: React.FC = () => {
  const { familyId } = useParams<{ familyId: string }>();
  const location = useLocation();
  const params = useMemo(() => new URLSearchParams(location.search), [location.search]);
  const signouts = useReportSignouts(familyId);

  if (!familyId) {
    return <PageState kicker="Report" title="Family not specified" />;
  }
  const projectId = params.get('project_id') || undefined;
  const versionParam = params.get('version');
  if (versionParam !== null) {
    const version = parseSignedVersionParam(versionParam);
    if (version === null) {
      return (
        <PageState
          kicker="Report"
          title="Not a signed version"
          message={`“${versionParam}” is not the number of a signed version.`}
          action={
            <Link to={{ search: reportViewSearch(location.search, 'live') }} className="button-secondary">
              Open the live report
            </Link>
          }
        />
      );
    }
    return <SignedFamilyReport familyId={familyId} version={version} projectId={projectId} />;
  }
  if (params.get('view') !== 'live') {
    // Only until the first answer. A refetch (the live report's own, after a failure) must not
    // swap the page back to this state: it would unmount the report that asked, over and over.
    if (!signouts.isFetched) {
      return (
        <PageState
          kicker="Report"
          title="Preparing the family report"
          message="Looking up the signed versions."
        />
      );
    }
    const latest = signouts.data?.latest;
    if (latest) {
      return <SignedFamilyReport familyId={familyId} version={latest.version} projectId={projectId} />;
    }
  }
  // Never signed out, or the sign-out record could not be loaded: the live report says which.
  return <LiveFamilyReport />;
};

export default FamilyReportPage;
