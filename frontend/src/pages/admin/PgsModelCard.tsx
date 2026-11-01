import React from 'react';
import type { PgsVersionOut } from '../../lib/apiSchema.generated';

const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
const text = (value: unknown): string => typeof value === 'string' ? value.trim() : '';
const texts = (value: unknown): string[] => (Array.isArray(value) ? value : [value]).map(text).filter(Boolean);
const records = (value: unknown): Record<string, unknown>[] => Array.isArray(value) ? value.map(record).filter((item) => Object.keys(item).length > 0) : [];
const reported = (value: unknown): string => text(value) && text(value) !== 'NR' ? text(value) : 'Not reported';
const date = (value: unknown): string => {
  const parsed = new Date(text(value));
  return Number.isNaN(parsed.getTime()) ? 'Not reported'
    : new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }).format(parsed);
};
const trait = (data: Record<string, unknown>): string =>
  text(data.trait_reported) || records(data.trait_efo).map((item) => text(item.label)).filter(Boolean).join(', ') || 'Not reported';
const doi = (publication: Record<string, unknown>): string => {
  const value = text(publication.doi).replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, '');
  return /^10\.\d{4,9}\/\S+$/.test(value) ? `https://doi.org/${value.split('/').map(encodeURIComponent).join('/')}` : '';
};
const pubmed = (publication: Record<string, unknown>): string => {
  const value = String(publication.PMID ?? '');
  return /^[1-9]\d*$/.test(value) ? `https://pubmed.ncbi.nlm.nih.gov/${value}/` : '';
};

const Field: React.FC<{ label: string; children: React.ReactNode; wide?: boolean }> = ({ label, children, wide }) =>
  <div className={wide ? 'pgs-field pgs-field--wide' : 'pgs-field'}><dt>{label}</dt><dd>{children}</dd></div>;

const Study: React.FC<{ version: PgsVersionOut }> = ({ version }) => {
  const publication = record(record(version.catalog_metadata?.record).publication);
  const title = text(publication.title) || version.study;
  const href = doi(publication) || pubmed(publication);
  const citation = [text(publication.firstauthor), text(publication.journal),
    text(publication.date_publication) ? date(publication.date_publication) : ''].filter(Boolean).join(' · ');
  return <div className="pgs-study">
    {href ? <a href={href} target="_blank" rel="noreferrer">{title}</a> : <span>{title}</span>}
    {citation && <span className="catalog-card-copy">{citation}</span>}
    {(doi(publication) || pubmed(publication)) && <span className="pgs-study-links">
      {doi(publication) && <a href={doi(publication)} target="_blank" rel="noreferrer">DOI</a>}
      {pubmed(publication) && <a href={pubmed(publication)} target="_blank" rel="noreferrer">PubMed</a>}
    </span>}
  </div>;
};

const ScoringStatus: React.FC<{ version: PgsVersionOut }> = ({ version }) =>
  <span className={`badge-chip ${!version.scoring_status || version.scoring_status === 'ready' ? 'badge-chip--success' : 'pgs-status--attention'}`}>
    {version.scoring_status === 'needs_reference' ? 'Reference required'
      : version.scoring_status === 'unsupported' ? 'Unsupported for GT scoring' : 'Ready'}
  </span>;

const VersionDetails: React.FC<{ version: PgsVersionOut; current: boolean }> = ({ version, current }) => {
  const data = record(version.catalog_metadata?.record);
  const metadata = record(version.catalog_metadata);
  const training = records(data.samples_training);
  const ancestry = [...new Set(training.flatMap((cohort) => texts(cohort.ancestry_broad)))].join(', ');
  const terms = records(data.trait_efo).map((item) => [text(item.label), text(item.id)].filter(Boolean).join(' · ')).filter(Boolean);
  const checksums: Array<[string, string | undefined]> = [
          ['Weights SHA-256', version.weights_sha256], ['Resolved weights SHA-256', version.resolved_sha256],
          ['Downloaded file SHA-256', text(metadata.download_sha256)], ['Catalog record SHA-256', text(metadata.record_sha256)],
          ['Catalog response SHA-256', text(metadata.api_response_sha256)],
          ['Reference FASTA SHA-256', version.reference.fasta_sha256], ['Reference index SHA-256', version.reference.fai_sha256],
        ];
  return <li className="pgs-version">
    <div className="pgs-version-heading">
      <h4>Revision {version.revision} · {date(version.created_at)}</h4>
      {current && <span className="badge-chip">Current version</span>}
      <ScoringStatus version={version} />
    </div>
    <dl className="pgs-details-grid">
      <Field label="Version label" wide><code>{version.version}</code></Field>
      <Field label="Trait">{trait(data)}</Field>
      <Field label="Variants">{version.variant_count.toLocaleString()} loci · {version.genome_build}</Field>
      <Field label="Study" wide><Study version={version} /></Field>
      {texts(data.trait_additional).length > 0 && <Field label="Additional traits" wide>{texts(data.trait_additional).join(', ')}</Field>}
      {terms.length > 0 && <Field label="Trait ontology" wide>{terms.join('; ')}</Field>}
      <Field label="Scoring method">{reported(data.method_name)}</Field>
      <Field label="Method parameters">{reported(data.method_params)}</Field>
      <Field label="Weight type">{reported(data.weight_type)}</Field>
      <Field label="Original genome build">{reported(data.variants_genomebuild)}</Field>
      <Field label="Catalog release">{date(data.date_release)}</Field>
      <Field label="Imported into CoGA">{date(version.created_at)}</Field>
      <Field label="Training cohorts">{training.length ? `${training.length} reported` : 'Not reported'}</Field>
      <Field label="Training ancestry">{ancestry || 'Not reported'}</Field>
      <Field label="Reference">{version.reference.reference || 'Not reported'}</Field>
      <Field label="Licence" wide>{reported(data.license)}</Field>
    </dl>
    {version.scoring_error && !current && <p className="status-note">{version.scoring_error}</p>}
    <details className="pgs-provenance">
      <summary>Sources and checksums</summary>
      <dl className="pgs-details-grid">
        <Field label="Scoring file" wide><a href={version.source_url} target="_blank" rel="noreferrer">Download scoring source</a></Field>
        <Field label="Retrieved from Catalog">{date(metadata.retrieved_at)}</Field>
        <Field label="Provider">{text(metadata.provider) || (Object.keys(data).length ? 'PGS Catalog' : 'Custom source')}</Field>
        {checksums.filter(([, value]) => value).map(([label, value]) =>
          <Field key={label} label={label} wide><code className="pgs-checksum">{value}</code></Field>)}
      </dl>
    </details>
  </li>;
};

const PgsModelCard: React.FC<{ versions: PgsVersionOut[]; busy: boolean; onArchive: (version: PgsVersionOut) => void }> =
  ({ versions, busy, onArchive }) => {
    const model = versions[0];
    const data = record(model.catalog_metadata?.record);
    return <article className={`surface-card catalog-card pgs-model-card${model.archived ? ' pgs-model-card--archived' : ''}`} aria-label={`${model.pgs_id} model`}>
      <div className="catalog-card-header pgs-model-header">
        <div className="pgs-model-identity">
          <h3 className="catalog-card-title"><a href={`https://www.pgscatalog.org/score/${encodeURIComponent(model.pgs_id)}/`} target="_blank" rel="noreferrer">{model.pgs_id}</a></h3>
          {text(data.name) && <p className="pgs-model-name">{text(data.name)}</p>}
        </div>
        <div className="action-row">
          <span className="badge-chip">{model.archived ? 'Archived' : 'Active'}</span>
          <ScoringStatus version={model} />
          <button className="button-secondary" type="button" aria-label={`${model.archived ? 'Reactivate' : 'Archive'} ${model.pgs_id}`} disabled={busy} onClick={() => onArchive(model)}>
            {model.archived ? 'Reactivate' : 'Archive'}
          </button>
        </div>
      </div>
      <dl className="pgs-model-overview">
        <Field label="Trait"><span className="pgs-model-trait">{trait(data)}</span></Field>
        <Field label="Study"><Study version={model} /></Field>
      </dl>
      <ul className="pgs-model-facts" aria-label="Current model summary">
        <li>{model.genome_build}</li><li>{model.variant_count.toLocaleString()} loci</li>
        <li>Revision {model.revision}</li><li>Released {date(data.date_release)}</li>
      </ul>
      {model.scoring_error && <p className="status-note">{model.scoring_error}</p>}
      <details className="pgs-model-history">
        <summary>Model details and version history ({versions.length})</summary>
        <ol className="pgs-version-list">{versions.map((version, i) =>
          <VersionDetails key={version.id} version={version} current={i === 0} />)}</ol>
      </details>
    </article>;
  };

export default PgsModelCard;
