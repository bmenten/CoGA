import React, { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import PageHeader from '../../components/PageHeader';
import PgsModelCard from './PgsModelCard';
import api from '../../lib/api';
import { apiPath } from '../../lib/apiPath';
import { getErrorMessage } from '../../lib/errorMessage';
import type { PgsCatalogueOut, PgsVersionOut, PgsCatalogImportsOut, PgsCatalogImportOut } from '../../lib/apiSchema.generated';

const AdminPgsPage: React.FC = () => {
  const client = useQueryClient();
  const [notice, setNotice] = useState('');
  const [catalogId, setCatalogId] = useState('');
  const query = useQuery({
    queryKey: ['pgs-models'],
    queryFn: async () => (await api.get<PgsCatalogueOut>(apiPath`/pgs/models`)).data,
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
  const grouped = new Map<string, PgsVersionOut[]>();
  for (const version of [...(query.data?.versions ?? [])].sort((a, b) => a.pgs_id.localeCompare(b.pgs_id) || b.revision - a.revision)) {
    grouped.set(version.pgs_id, [...(grouped.get(version.pgs_id) ?? []), version]);
  }
  const models = [...grouped.values()];
  const latest = models.map((versions) => versions[0]);
  const ready = latest.filter((model) => !model.archived && (!model.scoring_status || model.scoring_status === 'ready')).length;
  const archived = latest.filter((model) => model.archived).length;
  const error = query.error || archive.error || imports.error || importScore.error;
  return (
    <div className="page-shell space-y-5">
      <PageHeader kicker="Administration" title="Exploratory PGS" description="Manage additive autosomal SNV models on GRCh38. Raw GT scores are exploratory and excluded from clinical sign-out." />
      {error && <p role="alert" className="status-note status-note--error">{getErrorMessage(error, 'Could not save or load PGS models.')}</p>}
      {notice && <p role="status" className="status-note">{notice}</p>}
      {query.isPending && <p role="status">Loading models…</p>}
      <section className="surface-card space-y-3">
        <h2>Import from the PGS catalog</h2>
        <p><a href="https://www.pgscatalog.org/" target="_blank" rel="noreferrer">Find a score in the PGS Catalog</a>, then enter its PGS ID here. CoGA downloads the harmonized GRCh38 scoring file and imports its complete metadata and weights.</p>
        <form className="pgs-catalog-import-form" onSubmit={(event) => { event.preventDefault(); importScore.mutate(); }}>
          <label className="block">Catalog PGS ID <input required pattern="PGS[0-9]{6}" placeholder="PGS000004" value={catalogId} onChange={(e) => setCatalogId(e.target.value.toUpperCase())} /></label>
          <button className="form-button" disabled={importScore.isPending || !/^PGS[0-9]{6}$/.test(catalogId.trim())} type="submit">Import GRCh38 from Catalog</button>
        </form>
        <p>Models that require a reference FASTA or exceed the supported GT model scope are imported with their full source file and metadata, and marked as not ready for calculation. No variants are silently left out.</p>
        {imports.data?.reference_configured === false && <p>The indexed GRCh38 reference FASTA is unavailable. Check reference-data preparation, then import the ID again to prepare a new version.</p>}
        {(imports.data?.jobs ?? []).map((job) => <p key={job.id}>{job.pgs_id}: {job.status}{job.error ? ` — ${job.error}` : ''}</p>)}
      </section>
      <section className="space-y-3" aria-labelledby="pgs-catalogue-heading">
        <div className="pgs-catalogue-heading">
          <h2 id="pgs-catalogue-heading">Model catalogue</h2>
          <p className="catalog-card-copy">Studies, traits and immutable version history for your imported models.</p>
        </div>
        {query.data && <dl className="admin-data-summary" aria-label="Catalogue summary">
          {[['Models', models.length], ['Ready for calculation', ready],
            ['Needs attention', models.length - ready - archived], ['Archived', archived]].map(([label, value]) =>
            <div className="admin-data-summary-item" key={label}>
              <dt className="admin-data-summary-label">{label}</dt><dd className="admin-data-summary-value">{value}</dd>
            </div>)}
        </dl>}
        {!query.isPending && !error && !models.length && <p className="surface-card">No models imported yet. Enter a PGS ID above to add a model.</p>}
        <div className="pgs-model-list">{models.map((versions) =>
          <PgsModelCard key={versions[0].pgs_id} versions={versions} busy={archive.isPending} onArchive={(model) => archive.mutate(model)} />)}
        </div>
      </section>
    </div>
  );
};
export default AdminPgsPage;
