import React, { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import PageHeader from '../../components/PageHeader';
import api from '../../lib/api';
import { apiPath } from '../../lib/apiPath';
import { getErrorMessage } from '../../lib/errorMessage';
import type { PgsCatalogueOut, PgsVersionOut, PgsCatalogImportsOut, PgsCatalogImportOut } from '../../lib/apiSchema.generated';

const record = (value: unknown): Record<string, unknown> => value && typeof value === 'object' ? value as Record<string, unknown> : {};
const text = (value: unknown): string => typeof value === 'string' ? value : '';

const AdminPgsPage: React.FC = () => {
  const client = useQueryClient();
  const [pgsId, setPgsId] = useState('PGS005091');
  const [version, setVersion] = useState('');
  const [study, setStudy] = useState('');
  const [sourceUrl, setSourceUrl] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [notice, setNotice] = useState('');
  const [catalogId, setCatalogId] = useState('');
  const query = useQuery({
    queryKey: ['pgs-models'],
    queryFn: async () => (await api.get<PgsCatalogueOut>(apiPath`/pgs/models`)).data,
  });
  const publish = useMutation({
    onMutate: () => setNotice(''),
    mutationFn: async () => {
      const body = new FormData();
      body.set('version', version);
      body.set('study', study);
      body.set('source_url', sourceUrl);
      if (file) body.set('file', file);
      return (await api.post<PgsVersionOut>(apiPath`/admin/pgs/models/${pgsId}/versions`, body)).data;
    },
    onSuccess: async () => {
      setNotice('Model version saved. Existing families are not recalculated automatically. Open a family to request calculation.');
      await client.invalidateQueries({ queryKey: ['pgs-models'] });
      await client.invalidateQueries({ queryKey: ['family-pgs'] });
    },
  });
  const archive = useMutation({
    onMutate: () => setNotice(''),
    mutationFn: async (model: PgsVersionOut) =>
      api.put(apiPath`/admin/pgs/models/${model.pgs_id}/archive`, { archived: !model.archived }),
    onSuccess: async () => {
      setNotice('Model status saved. Historical weights and results remain available.');
      await client.invalidateQueries({ queryKey: ['pgs-models'] });
      await client.invalidateQueries({ queryKey: ['family-pgs'] });
    },
  });
  const imports = useQuery({
    queryKey: ['pgs-catalog-imports'],
    queryFn: async () => (await api.get<PgsCatalogImportsOut>(apiPath`/admin/pgs/catalog-imports`)).data,
    refetchInterval: (q) => q.state.data?.jobs?.some((job) => ['queued', 'running'].includes(job.status)) ? 2000 : false,
  });
  const completed = (imports.data?.jobs ?? []).filter((job) => job.status === 'completed').map((job) => job.id).join(',');
  useEffect(() => {
    if (completed) {
      void client.invalidateQueries({ queryKey: ['pgs-models'] });
      void client.invalidateQueries({ queryKey: ['family-pgs'] });
    }
  }, [client, completed]);
  const importScore = useMutation({
    onMutate: () => setNotice(''),
    mutationFn: async () => (await api.post<PgsCatalogImportOut>(apiPath`/admin/pgs/catalog-imports`, { pgs_id: catalogId.trim().toUpperCase() })).data,
    onSuccess: async () => {
      setNotice('Catalog import queued. Download and validation continue in the background. Existing families are not recalculated automatically.');
      await client.invalidateQueries({ queryKey: ['pgs-catalog-imports'] });
    },
  });
  const latest = (query.data?.versions ?? []).filter((v, i, all) => all.findIndex((m) => m.pgs_id === v.pgs_id) === i);
  const error = query.error || publish.error || archive.error || imports.error || importScore.error;
  return (
    <div className="page-shell space-y-5">
      <PageHeader kicker="Administration" title="Exploratory PGS" description="Manage additive autosomal SNV models on GRCh38. Raw GT scores are exploratory and excluded from clinical sign-out." />
      {error && <p role="alert" className="status-note status-note--error">{getErrorMessage(error, 'Could not save or load PGS models.')}</p>}
      {notice && <p role="status" className="status-note">{notice}</p>}
      {query.isPending && <p role="status">Loading models…</p>}
      <section className="surface-card space-y-3">
        <h2>Import from the PGS catalog</h2>
        <p><a href="https://www.pgscatalog.org/" target="_blank" rel="noreferrer">Find a score in the PGS Catalog</a>, then enter its PGS ID here. CoGA downloads the harmonized GRCh38 scoring file and imports its complete metadata and weights.</p>
        <form className="space-y-3" onSubmit={(event) => { event.preventDefault(); importScore.mutate(); }}>
          <label className="block">Catalog PGS ID <input required pattern="PGS[0-9]{6}" placeholder="PGS000004" value={catalogId} onChange={(e) => setCatalogId(e.target.value.toUpperCase())} /></label>
          <button className="form-button" disabled={importScore.isPending || !/^PGS[0-9]{6}$/.test(catalogId.trim())} type="submit">Import GRCh38 from Catalog</button>
        </form>
        <p>Models that require a reference FASTA or exceed the supported GT model scope are imported with their full source file and metadata, and marked as not ready for calculation. No variants are silently left out.</p>
        {imports.data?.reference_configured === false && <p>Configure the indexed GRCh38 reference FASTA once to validate files without explicit REF alleles. Import the ID again after configuration to prepare a new version.</p>}
        {(imports.data?.jobs ?? []).map((job) => <p key={job.id}>{job.pgs_id}: {job.status}{job.error ? ` — ${job.error}` : ''}</p>)}
      </section>
      <form className="surface-card space-y-3" onSubmit={(event) => { event.preventDefault(); publish.mutate(); }}>
        <h2>Manual model version upload</h2>
        <p>Upload a PGS Catalog scoring TSV or TSV.gz with explicit GRCh38 coordinates, effect_weight and ref_allele, or configure the indexed GRCh38 reference FASTA.</p>
        <label className="block">PGS ID <input required pattern="PGS[0-9]{6}" list="pgs-suggestions" value={pgsId} onChange={(e) => setPgsId(e.target.value.toUpperCase())} /></label>
        <datalist id="pgs-suggestions">{(query.data?.shortlist ?? []).map((id) => <option key={id} value={id} />)}</datalist>
        <label className="block">Version label <input required value={version} maxLength={100} onChange={(e) => setVersion(e.target.value)} /></label>
        <label className="block">Study citation <input required value={study} onChange={(e) => setStudy(e.target.value)} /></label>
        <label className="block">Source URL <input required type="url" value={sourceUrl} onChange={(e) => setSourceUrl(e.target.value)} /></label>
        <label className="block">Scoring file <input required type="file" accept=".txt,.tsv,.gz" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></label>
        <button className="form-button" disabled={publish.isPending || !file} type="submit">{publish.isPending ? 'Saving…' : 'Save model version'}</button>
      </form>
      <section className="surface-card space-y-3">
        <h2>Model catalogue and version history</h2>
        {!query.isPending && !latest.length && <p>No models have been added. The shortlist contains candidates, without installed weights.</p>}
        {latest.map((model) => <article className="space-y-2" key={model.pgs_id}>
          <h3>{model.pgs_id} — {model.archived ? 'Archived' : 'Active'}</h3>
          <button className="button-secondary" disabled={archive.isPending} onClick={() => archive.mutate(model)}>{model.archived ? 'Reactivate' : 'Archive'} {model.pgs_id}</button>
          {(query.data?.versions ?? []).filter((v) => v.pgs_id === model.pgs_id).map((v) => <details key={v.id}>
            <summary>{v.version} · GRCh38 · {v.variant_count.toLocaleString()} loci · {v.scoring_status === 'needs_reference' ? 'Reference required' : v.scoring_status === 'unsupported' ? 'Unsupported for GT scoring' : 'Ready'}</summary>
            {v.scoring_error && <p>{v.scoring_error}</p>}
            <p>{v.study} · <a href={v.source_url} target="_blank" rel="noreferrer">Scoring source</a></p>
            <p>Added: {v.created_at}. Weights SHA-256: <code>{v.weights_sha256}</code></p>
            <p>Resolved weights SHA-256: <code>{v.resolved_sha256}</code></p>
            <pre className="overflow-auto">{JSON.stringify(v.reference, null, 2)}</pre>
            {Object.keys(v.catalog_metadata ?? {}).length > 0 && <>
              <p>Catalog model: {text(record(v.catalog_metadata.record).name)}. Trait: {text(record(v.catalog_metadata.record).trait_reported)}.</p>
              <p>Downloaded: {text(v.catalog_metadata.retrieved_at)}. Compressed-file SHA-256: <code>{text(v.catalog_metadata.download_sha256)}</code>.</p>
              <details><summary>Catalog metadata: study, traits, ancestry, licence and scoring-file header</summary>
                <pre className="overflow-auto">{JSON.stringify(v.catalog_metadata, null, 2)}</pre>
              </details>
            </>}
          </details>)}
        </article>)}
      </section>
    </div>
  );
};
export default AdminPgsPage;
