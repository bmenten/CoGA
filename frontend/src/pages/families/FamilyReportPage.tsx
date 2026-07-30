import React, { useMemo, useState } from 'react';
import { Link, useLocation, useParams } from 'react-router';
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';

import api from '../../lib/api';
import PageState from '../../components/PageState';
import type {
  ApiAnnotationManifest,
  ApiClassificationDrift,
  ApiClinicalAudit,
  ApiReportSignoutList,
} from '../../lib/apiTypes';
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
import { type StructuralVariant } from './structuralVariantSearch';
import PipelineSettingsPanel, { pipelineSettingsFromMetadata } from './PipelineSettingsPanel';
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

const FamilyReportPage: React.FC = () => {
  const { familyId } = useParams<{ familyId: string }>();
  const location = useLocation();
  const preferredProjectId = useMemo(
    () => new URLSearchParams(location.search).get('project_id') || undefined,
    [location.search],
  );

  const { data: family, isLoading: familyLoading } = useQuery<SmallVariantFamily>({
    queryKey: ['family', familyId],
    enabled: Boolean(familyId),
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}`);
      return res.data as SmallVariantFamily;
    },
  });

  const members = useMemo<FamilyMember[]>(
    () => ((family?.members as FamilyMember[] | undefined) ?? []).filter((m) => m.active !== false),
    [family],
  );

  const { speciesName, assemblyName, assemblyVersion, projectId, isLoading: referenceLoading } =
    useFamilyReference(family?.projects as string[] | undefined, preferredProjectId);

  const referenceLabel = formatResolvedReferenceLabel(
    { speciesName, assemblyName, assemblyVersion },
    'Reference not linked',
  );

  const variantQueryReady = Boolean(
    familyId && family && (!family.projects?.length || projectId),
  );

  const reportQueryString = useMemo(() => {
    const params = new URLSearchParams();
    params.set('review_tag', REPORT_TAG_KEY);
    params.set('page_size', '500');
    if (projectId) params.set('project_id', projectId);
    return params.toString();
  }, [projectId]);

  const {
    data: reportPage,
    isLoading: variantsLoading,
    isError,
  } = useQuery<SmallVariantPage>({
    queryKey: ['family', familyId, 'report-variants', reportQueryString],
    enabled: variantQueryReady,
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}/small-variants?${reportQueryString}`);
      return res.data as SmallVariantPage;
    },
  });

  const variants = useMemo(() => reportPage?.variants ?? [], [reportPage]);

  const { data: structuralReportPage } = useQuery<{ variants: StructuralVariant[] }>({
    queryKey: ['family', familyId, 'report-structural-variants', reportQueryString],
    enabled: variantQueryReady,
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}/structural-variants?${reportQueryString}`);
      return res.data as { variants: StructuralVariant[] };
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

  const { data: hpoAnnotations = [] } = useQuery<FamilyHpoAnnotation[]>({
    queryKey: ['family', familyId, 'hpo'],
    enabled: Boolean(familyId),
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}/hpo`);
      return res.data as FamilyHpoAnnotation[];
    },
  });

  // Provenance footer: which annotation/reference modules + versions backed the report.
  const { data: manifest } = useQuery<ApiAnnotationManifest>({
    queryKey: ['family', familyId, 'annotation-manifest'],
    enabled: Boolean(familyId),
    queryFn: async () =>
      (await api.get(`/families/${familyId}/annotation-manifest`)).data as ApiAnnotationManifest,
  });
  // The moment the report was produced (becomes the frozen sign-out time in Phase 3).
  const generatedAt = useMemo(() => new Date(), []);

  // Evidence drift: classifications whose backing annotation changed since they
  // were made — a sign-out guardrail against stale interpretations.
  const { data: drift } = useQuery<ApiClassificationDrift>({
    queryKey: ['family', familyId, 'classification-drift'],
    enabled: Boolean(familyId),
    queryFn: async () =>
      (await api.get(`/families/${familyId}/classification-drift`)).data as ApiClassificationDrift,
  });

  // Immutable clinical audit trail (who classified / tagged / annotated what, when).
  const { data: audit } = useQuery<ApiClinicalAudit>({
    queryKey: ['family', familyId, 'clinical-audit'],
    enabled: Boolean(familyId),
    queryFn: async () =>
      (await api.get(`/families/${familyId}/clinical-audit`)).data as ApiClinicalAudit,
  });

  // Case sign-out: the frozen, versioned, content-hashed report record.
  const queryClient = useQueryClient();
  const { data: signouts } = useQuery<ApiReportSignoutList>({
    queryKey: ['family', familyId, 'report-signouts'],
    enabled: Boolean(familyId),
    queryFn: async () =>
      (await api.get(`/families/${familyId}/report/sign-outs`)).data as ApiReportSignoutList,
  });

  // Sample-QC gate dialog state (acknowledge-with-reason override of a failing QC).
  const [qcGate, setQcGate] = useState<{
    message: string;
    acknowledgeDrift: boolean;
    summary?: { overall_status?: string; messages?: string[] };
  } | null>(null);
  const [qcReason, setQcReason] = useState('');

  const signOut = useMutation({
    mutationFn: async (vars: {
      acknowledgeDrift: boolean;
      acknowledgeQc?: boolean;
      qcReason?: string;
    }) =>
      (
        await api.post(`/families/${familyId}/report/sign-out`, {
          acknowledge_drift: vars.acknowledgeDrift,
          acknowledge_qc: vars.acknowledgeQc ?? false,
          qc_acknowledgement_reason: vars.qcReason,
        })
      ).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['family', familyId, 'report-signouts'] });
      queryClient.invalidateQueries({ queryKey: ['family', familyId, 'clinical-audit'] });
    },
  });

  const attemptSignOut = async (vars: {
    acknowledgeDrift: boolean;
    acknowledgeQc?: boolean;
    qcReason?: string;
  }) => {
    try {
      await signOut.mutateAsync(vars);
      setQcGate(null);
      setQcReason('');
    } catch (error) {
      const response = (
        error as { response?: { status?: number; data?: { detail?: unknown } } }
      ).response;
      if (response?.status !== 409) {
        throw error;
      }
      const detail = response.data?.detail;
      // A failing Sample QC returns a structured detail (gate discriminator + a failure
      // summary); it needs an acknowledge-WITH-REASON override, so open the dialog.
      if (
        detail &&
        typeof detail === 'object' &&
        (detail as { gate?: string }).gate === 'sample_qc'
      ) {
        const qc = detail as {
          message?: string;
          qc_summary?: { overall_status?: string; messages?: string[] };
        };
        setQcGate({
          message: qc.message || 'Sample-integrity QC failed.',
          summary: qc.qc_summary,
          acknowledgeDrift: vars.acknowledgeDrift,
        });
        return;
      }
      // Evidence-drift gate (plain string detail) — acknowledge-only confirm (unchanged).
      const message =
        typeof detail === 'string'
          ? detail
          : 'Evidence has changed since some classifications were made.';
      if (window.confirm(`${message}\n\nSign out anyway?`)) {
        await attemptSignOut({ ...vars, acknowledgeDrift: true });
      }
    }
  };

  const handleSignOut = () => attemptSignOut({ acknowledgeDrift: false, acknowledgeQc: false });

  const submitQcAcknowledgement = async () => {
    const reason = qcReason.trim();
    if (!reason || !qcGate) {
      return;
    }
    await attemptSignOut({
      acknowledgeDrift: qcGate.acknowledgeDrift,
      acknowledgeQc: true,
      qcReason: reason,
    });
  };

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

  if (familyLoading || (variantQueryReady && variantsLoading) || referenceLoading) {
    return (
      <PageState
        kicker="Report"
        title="Preparing the family report"
        message="Gathering reported variants, gene context and phenotype data."
      />
    );
  }

  if (isError) {
    return (
      <PageState
        kicker="Report"
        title="Report could not be loaded"
        message="The reported variants for this family could not be retrieved."
        action={
          <Link to={`/families/${familyId}`} className="button-secondary">
            Back to family
          </Link>
        }
      />
    );
  }

  return (
    <div className="page-shell report-page space-y-6">
      <header className="surface-card report-header">
        <div className="space-y-1">
          <p className="page-kicker">Clinical report</p>
          <h1 className="page-state-title">Family {familyId}</h1>
          <p className="report-header-meta">{referenceLabel}</p>
        </div>
        <div className="report-header-actions no-print">
          <Link to={`/families/${familyId}`} className="button-secondary hover:no-underline">
            Back to family
          </Link>
          <button type="button" className="form-button" onClick={() => window.print()}>
            Print report
          </button>
          <button
            type="button"
            className="form-button"
            onClick={handleSignOut}
            disabled={signOut.isPending}
          >
            {signOut.isPending
              ? 'Signing out…'
              : signouts?.latest
                ? 'Amend sign-out'
                : 'Sign out report'}
          </button>
        </div>
      </header>

      {qcGate ? (
        <div
          className="modal-backdrop"
          role="dialog"
          aria-modal="true"
          aria-label="Sample-integrity QC acknowledgement required"
        >
          <div className="modal-surface surface-card report-qc-ack-modal">
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
          </div>
        </div>
      ) : null}

      {signouts?.latest ? (
        <section className="surface-card report-signout">
          <p className="report-signout-line">
            ✓ Signed out — version {signouts.latest.version} by{' '}
            <strong>{signouts.latest.signed_out_by}</strong> on{' '}
            {signouts.latest.signed_out_at.replace('T', ' ').slice(0, 16)} UTC
          </p>
          <p className="report-signout-hash">
            <span className="report-footer-label">Content hash</span> {signouts.latest.content_hash}
          </p>
          {signouts.latest.software_version ? (
            <p className="report-signout-software">
              <span className="report-footer-label">Software</span>{' '}
              {`CoGA ${signouts.latest.software_version}${
                signouts.latest.git_sha && signouts.latest.git_sha !== 'unknown'
                  ? ` (${signouts.latest.git_sha.slice(0, 7)})`
                  : ''
              }`}
            </p>
          ) : null}
          {signouts.latest.qc_status ? (
            <p className="report-signout-qc">
              <span className="report-footer-label">Sample QC</span>{' '}
              {signouts.latest.qc_status}
              {signouts.latest.qc_acknowledged ? (
                <> — override acknowledged: {signouts.latest.qc_acknowledgement_reason}</>
              ) : null}
            </p>
          ) : null}
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

      {drift && drift.drifted_count > 0 ? (
        <section className="surface-card report-drift" role="alert">
          <p className="report-drift-title">
            ⚠ {drift.drifted_count} classification{drift.drifted_count === 1 ? '' : 's'}{' '}
            {drift.drifted_count === 1 ? 'has' : 'have'} evidence changes since being made
          </p>
          <p className="report-paragraph report-drift-lead">
            The annotation backing the following classification
            {drift.drifted_count === 1 ? '' : 's'} has changed since it was recorded. Re-review
            before sign-out.
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
                {presentHpoTerms.size === 0 ? (
                  <p className="report-paragraph">
                    No HPO phenotype terms have been recorded for this family.
                  </p>
                ) : overlappingHpo.length ? (
                  <p className="report-paragraph">
                    The patient phenotype overlaps the gene&rsquo;s known HPO associations for{' '}
                    {joinWithAnd(overlappingHpo.map((term) => `${term.label} (${term.id})`))},
                    supporting a phenotypic match.
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
            <article key={`sv-${variant._id}`} className="surface-card report-variant">
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
                {presentHpoTerms.size === 0 ? (
                  <p className="report-paragraph">
                    No HPO phenotype terms have been recorded for this family.
                  </p>
                ) : overlappingHpo.length ? (
                  <p className="report-paragraph">
                    The patient phenotype overlaps the gene&rsquo;s known HPO associations for{' '}
                    {joinWithAnd(overlappingHpo.map((term) => `${term.label} (${term.id})`))},
                    supporting a phenotypic match.
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

      {audit?.events?.length ? (
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
        <p className="report-footer-versions">
          <span className="report-footer-label">Modules &amp; versions:</span>{' '}
          {manifest?.modules?.some((module) => module.version)
            ? manifest.modules
                .filter((module) => module.version)
                .map((module) => {
                  // Per-modality divergence (issue #294): when a database was cited
                  // at different releases by different pipelines, show each — e.g.
                  // "GENCODE 49 (snv), 45 (sv)" — so the report is fully traceable.
                  const byModality = module.by_modality;
                  const distinct = byModality
                    ? Array.from(new Set(Object.values(byModality)))
                    : [];
                  if (byModality && distinct.length > 1) {
                    const perModality = Object.entries(byModality)
                      .map(([mod, version]) => `${version} (${mod})`)
                      .join(', ');
                    return `${module.label} ${perModality}`;
                  }
                  return `${module.label} ${module.version}${module.detail ? ` (${module.detail})` : ''}`;
                })
                .join(' · ')
            : 'not recorded for this family'}
        </p>
      </footer>
    </div>
  );
};

export default FamilyReportPage;
