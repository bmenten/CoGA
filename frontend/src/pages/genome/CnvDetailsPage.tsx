import React, { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useNavigate, useParams } from 'react-router';
import api from '../../lib/api';
import type { ApiChromosome, ApiClinicalCnv } from '../../lib/apiTypes';
import PageState from '../../components/PageState';
import { sanitizeHtml } from '../../lib/sanitizeHtml';
import { apiPath } from '../../lib/apiPath';
import { getErrorMessage, isNotFoundError } from '../../lib/errorMessage';
import {
  CLINVAR_SUPPORT_NOT_RECORDED,
  clinvarRecordHref,
  clinvarSupportSummary,
} from '../../lib/clinicalCnvSupport';
import { formatRegionSize } from '../../lib/format';

const formatBp = (bp: number) => bp.toLocaleString();

const chromNumber = (chr: string) => chr.replace(/^chr/i, '');

const computeCytoband = (cnv: ApiClinicalCnv, chromosome?: ApiChromosome): string => {
  if (!chromosome?.bands?.length) return '';
  const overlapping = chromosome.bands
    .filter((band) => band.end > cnv.start && band.start < cnv.end)
    .sort((a, b) => a.start - b.start);
  if (!overlapping.length) return '';
  const chr = chromNumber(cnv.chr);
  const first = overlapping[0].name;
  const last = overlapping[overlapping.length - 1].name;
  return first === last ? `${chr}${first}` : `${chr}${first}–${chr}${last}`;
};

const omimHref = (cnv: ApiClinicalCnv) =>
  cnv.omim_id
    ? `https://www.omim.org/entry/${encodeURIComponent(cnv.omim_id.replace(/^OMIM:/i, ''))}`
    : `https://www.omim.org/search?index=entry&search=${encodeURIComponent(cnv.label)}`;

const decipherHref = (cnv: ApiClinicalCnv) =>
  `https://www.deciphergenomics.org/browser#q/${chromNumber(cnv.chr)}:${cnv.start}-${cnv.end}`;

const CnvDetailsPage: React.FC = () => {
  const navigate = useNavigate();
  const { cnvId } = useParams<{ cnvId: string }>();

  const {
    data: cnv,
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery<ApiClinicalCnv>({
    queryKey: ['clinical-cnv', cnvId],
    queryFn: async () => {
      const res = await api.get(apiPath`/cnvs/entry/${cnvId}`);
      return res.data as ApiClinicalCnv;
    },
    enabled: Boolean(cnvId),
  });

  const { data: chromosome } = useQuery<ApiChromosome>({
    queryKey: ['chromosome', cnv?.assembly, cnv?.chr],
    queryFn: async () => {
      const res = await api.get(apiPath`/chromosomes/${cnv?.assembly}/${cnv?.chr}`);
      return res.data as ApiChromosome;
    },
    enabled: Boolean(cnv?.assembly && cnv?.chr),
  });

  const cytoband = useMemo(() => (cnv ? computeCytoband(cnv, chromosome) : ''), [cnv, chromosome]);

  // details_html comes from imported reference data — strip it to a safe allowlist before
  // it reaches dangerouslySetInnerHTML (defense-in-depth alongside backend sanitisation).
  const sourceHtml = useMemo(
    () => (cnv?.details_html ? sanitizeHtml(cnv.details_html) : ''),
    [cnv?.details_html],
  );

  if (isLoading) {
    return (
      <div className="page-shell content-shell">
        <PageState kicker="Clinical CNV" title="Loading CNV details…" />
      </div>
    );
  }

  // A server error is not a CNV that does not exist (#624, as #610).
  if (isError && !isNotFoundError(error)) {
    return (
      <div className="page-shell content-shell">
        <PageState
          kicker="Clinical CNV"
          title="CNV could not be loaded"
          message={`${getErrorMessage(error, 'The request failed.')} This is a failed request, not a missing CNV.`}
          action={
            <>
              <button type="button" className="button-secondary" onClick={() => void refetch()}>
                Retry
              </button>
              <button onClick={() => navigate(-1)}>Back</button>
            </>
          }
        />
      </div>
    );
  }

  if (isError || !cnv) {
    return (
      <div className="page-shell content-shell">
        <PageState
          kicker="Clinical CNV"
          title="CNV not found."
          message="This clinical CNV could not be loaded."
          action={<button onClick={() => navigate(-1)}>Back</button>}
        />
      </div>
    );
  }

  const size = Math.max(cnv.end - cnv.start, 0);
  const clinvarSummary = clinvarSupportSummary(cnv);
  const clinvarAccessions = cnv.clinvar_pathogenic_accessions ?? [];

  return (
    <div className="page-shell content-shell">
      <section className="surface-card space-y-5">
        <div className="space-y-1">
          <p className="page-kicker">Clinical CNV</p>
          <h1 className="section-title">{cnv.label}</h1>
        </div>

        <dl className="cnv-detail-grid">
          <div>
            <dt>Location</dt>
            <dd>
              {cnv.chr}:{formatBp(cnv.start)}–{formatBp(cnv.end)} ({formatRegionSize(size)})
            </dd>
          </div>
          <div>
            <dt>Cytoband</dt>
            <dd>{cnv.cytoband || cytoband || '—'}</dd>
          </div>
          <div>
            <dt>Type</dt>
            <dd>{cnv.type || 'Clinical CNV'}</dd>
          </div>
          {cnv.assembly ? (
            <div>
              <dt>Assembly</dt>
              <dd>{cnv.assembly}</dd>
            </div>
          ) : null}
          {cnv.source_id ? (
            <div>
              <dt>ISCA</dt>
              <dd>{cnv.source_id}</dd>
            </div>
          ) : null}
          {cnv.omim_id || cnv.omim_title ? (
            <div>
              <dt>OMIM</dt>
              <dd>
                {[cnv.omim_id, cnv.omim_title].filter(Boolean).join(' · ')}
              </dd>
            </div>
          ) : null}
          {cnv.orpha_id || cnv.orpha_name ? (
            <div>
              <dt>Orphanet</dt>
              <dd>
                {[cnv.orpha_name, cnv.orpha_id ? `ORPHA:${cnv.orpha_id}` : null]
                  .filter(Boolean)
                  .join(' · ')}
              </dd>
            </div>
          ) : null}
          {cnv.decipher_id ? (
            <div>
              <dt>DECIPHER ID</dt>
              <dd>{cnv.decipher_id}</dd>
            </div>
          ) : null}
          <div>
            <dt>ClinVar pathogenic</dt>
            <dd>
              {clinvarSummary ?? (
                <span className="table-empty" title={CLINVAR_SUPPORT_NOT_RECORDED}>
                  Not recorded
                </span>
              )}
            </dd>
          </div>
        </dl>

        <div className="space-y-1">
          <p className="cnv-detail-section-label">Clinical description</p>
          <p className="cnv-detail-text">
            {cnv.description ||
              'No curated clinical description is available for this CNV in the reference set. Use the OMIM and DECIPHER links below for more information.'}
          </p>
        </div>

        {clinvarAccessions.length ? (
          <div className="space-y-1">
            <p className="cnv-detail-section-label">Supporting ClinVar records</p>
            <p className="cnv-detail-text">
              Pathogenic ClinVar CNVs that overlap this region by at least 30 % reciprocally, as
              counted when the knowledgebase was built.
            </p>
            <div className="compact-toolbar">
              {clinvarAccessions.map((accession) => (
                <a
                  key={accession}
                  href={clinvarRecordHref(accession)}
                  target="_blank"
                  rel="noreferrer"
                  className="variant-card-resource variant-card-resource--clinical"
                >
                  {accession} ↗
                </a>
              ))}
            </div>
          </div>
        ) : null}

        {sourceHtml ? (
          <div className="space-y-1">
            <p className="cnv-detail-section-label">Source</p>
            <div className="content-html" dangerouslySetInnerHTML={{ __html: sourceHtml }} />
          </div>
        ) : null}

        <div className="space-y-1">
          <p className="cnv-detail-section-label">External resources</p>
          <div className="compact-toolbar">
            <a
              href={omimHref(cnv)}
              target="_blank"
              rel="noreferrer"
              className="variant-card-resource variant-card-resource--clinical"
            >
              OMIM ↗
            </a>
            <a
              href={decipherHref(cnv)}
              target="_blank"
              rel="noreferrer"
              className="variant-card-resource variant-card-resource--clinical"
            >
              DECIPHER ↗
            </a>
            {cnv.orpha_id ? (
              <a
                href={`https://www.orpha.net/en/disease/detail/${encodeURIComponent(cnv.orpha_id)}`}
                target="_blank"
                rel="noreferrer"
                className="variant-card-resource variant-card-resource--clinical"
              >
                Orphanet ↗
              </a>
            ) : null}
          </div>
        </div>

        <div className="compact-toolbar">
          <Link to="/cnv-explorer" className="button-secondary hover:no-underline">
            All clinical CNVs
          </Link>
          <button className="button-ghost" onClick={() => navigate(-1)}>
            Back
          </button>
        </div>
      </section>
    </div>
  );
};

export default CnvDetailsPage;
