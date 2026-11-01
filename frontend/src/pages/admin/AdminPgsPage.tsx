import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import PageHeader from '../../components/PageHeader';
import api from '../../lib/api';
import { apiPath } from '../../lib/apiPath';
import { getErrorMessage } from '../../lib/errorMessage';
import type { PgsCatalogueOut, PgsVersionOut } from '../../lib/apiSchema.generated';

const AdminPgsPage: React.FC = () => {
  const client = useQueryClient();
  const [pgsId, setPgsId] = useState('PGS005091');
  const [version, setVersion] = useState('');
  const [study, setStudy] = useState('');
  const [sourceUrl, setSourceUrl] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [notice, setNotice] = useState('');
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
  const latest = (query.data?.versions ?? []).filter((v, i, all) => all.findIndex((m) => m.pgs_id === v.pgs_id) === i);
  const error = query.error || publish.error || archive.error;
  return (
    <div className="page-shell space-y-5">
      <PageHeader kicker="Administration" title="Exploratory PGS" description="Manage additive autosomal SNV models on GRCh38. Raw GT scores are exploratory and excluded from clinical sign-out." />
      {error && <p role="alert" className="status-note status-note--error">{getErrorMessage(error, 'Could not save or load PGS models.')}</p>}
      {notice && <p role="status" className="status-note">{notice}</p>}
      {query.isPending && <p role="status">Loading models…</p>}
      <form className="surface-card space-y-3" onSubmit={(event) => { event.preventDefault(); publish.mutate(); }}>
        <h2>Add a model version</h2>
        <p>Upload a PGS Catalog scoring TSV or TSV.gz with explicit GRCh38 coordinates, effect_weight and ref_allele, or configure the indexed GRCh38 reference FASTA.</p>
        <label className="block">PGS ID <select aria-label="PGS ID" value={pgsId} onChange={(e) => setPgsId(e.target.value)}>
          {(query.data?.shortlist ?? ['PGS005091']).map((id) => <option key={id}>{id}</option>)}
        </select></label>
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
            <summary>{v.version} · GRCh38 · {v.variant_count.toLocaleString()} loci</summary>
            <p>{v.study} · <a href={v.source_url} target="_blank" rel="noreferrer">Scoring source</a></p>
            <p>Added: {v.created_at}. Weights SHA-256: <code>{v.weights_sha256}</code></p>
            <p>Resolved weights SHA-256: <code>{v.resolved_sha256}</code></p>
            <pre className="overflow-auto">{JSON.stringify(v.reference, null, 2)}</pre>
          </details>)}
        </article>)}
      </section>
    </div>
  );
};
export default AdminPgsPage;
