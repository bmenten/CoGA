import React, { useMemo, useState } from 'react';
import { Link } from 'react-router';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import AdminModal from '../../components/AdminModal';
import { isAdmin } from '../../lib/auth';
import { withEntityId } from '../../lib/entity';
import { getErrorMessage } from '../../lib/errorMessage';

interface Species {
  id: string;
  name: string;
  common_name: string;
  tax_id: number;
}

interface Assembly {
  id: string;
  species_id: string;
  assembly_name: string;
  version: string;
  release_date: string;
}

interface ReferenceDatasetImport {
  dataset_type: string;
  inserted: number;
  replaced: boolean;
  source?: string | null;
  performed_by?: string | null;
  performed_at: string;
}

interface AssemblyReferenceStatus {
  assembly_id: string;
  assembly_name: string;
  chromosomes: number;
  genes: number;
  blacklist_regions: number;
  clinical_cnvs: number;
  segmental_duplications: number;
  dgv?: number;
  last_imports?: ReferenceDatasetImport[];
}

interface ReferenceImportSourceOrganism {
  scientific_name: string;
  common_name: string;
  tax_id: number;
  assembly_count: number;
}

interface ClinicalCnvKbJob {
  _id: string;
  assembly_name: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  skip_clinvar: boolean;
  requested_by?: string | null;
  requested_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  inserted: number;
  error?: string | null;
}

interface ClinicalCnvKbStatus {
  active_job: ClinicalCnvKbJob | null;
  recent_jobs: ClinicalCnvKbJob[];
  available: boolean;
  detail?: string | null;
}

interface GeneMetadataStatus {
  active_job?: { status: string } | null;
  human_gene_symbols: number;
  total_cached_records: number;
  last_completed_at?: string | null;
}

interface ReferenceImportActivity {
  assembly_id: string;
  assembly_name: string;
  species_name: string;
  dataset_type: string;
  inserted: number;
  replaced: boolean;
  source?: string | null;
  performed_by?: string | null;
  performed_at: string;
}

interface ReferenceImportSourceAssembly {
  scientific_name: string;
  common_name: string;
  tax_id: number;
  ucsc_genome: string;
  assembly_name: string;
  assembly_version: string;
  release_date: string | null;
  description: string;
  source_name: string;
  cytobands_available: boolean;
  genes_available: boolean;
  gene_source: string;
}

interface ReferenceAutoImportResult {
  species_id: string;
  species_name: string;
  assembly_id: string;
  assembly_name: string;
  assembly_version: string;
  ucsc_genome: string;
  created_species: boolean;
  created_assembly: boolean;
  cytobands_inserted: number;
  genes_inserted: number;
  cytobands_replaced: boolean;
  genes_replaced: boolean;
  cytoband_source_url: string;
  gene_source_url: string;
  gene_source: string;
  gene_warning?: string | null;
}

const formatCatalogCount = (value: number | undefined) => (value ?? 0).toLocaleString();

const formatDate = (value: string) => {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString();
};

const formatDateTime = (value: string) => {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
};

// Static copy table — hoisted to module scope so it is not reallocated on every
// render (two 3 s polling queries re-render this page while jobs run).
const datasetCopy: Record<string, { title: string; description: string }> = {
  cytobands: {
    title: 'Cytobands',
    description: 'Upload a cytoband file with chrom, start, end, band, and stain columns.',
  },
  genes: {
    title: 'Genes',
    description:
      'Upload the tab-delimited reference gene format used by the import script, including exon and intron interval columns.',
  },
  blacklist: {
    title: 'Blacklist regions',
    description: 'Upload a BED-like file with chrom, start, end, and label columns.',
  },
  clinical_cnvs: {
    title: 'Clinical CNVs',
    description:
      'Upload a UCSC bedDetail-style file with at least 11 columns for curated CNV syndromes.',
  },
  segmental_duplications: {
    title: 'Segmental duplications/LCRs',
    description:
      'Upload a BED-like file with segmental duplication/LCR intervals; ClinGen recurrent CNV BED keeps black LCR bars.',
  },
  dgv: {
    title: 'DGV variants',
    description:
      'Upload the Database of Genomic Variants TSV (variantaccession, chr, start, end, varianttype, variantsubtype, …). Large files load in the background.',
  },
};

const ReferenceCatalogPage: React.FC = () => {
  const userIsAdmin = isAdmin();
  const queryClient = useQueryClient();
  const [speciesForm, setSpeciesForm] = useState({
    name: '',
    common_name: '',
    tax_id: '',
  });
  const [assemblyForm, setAssemblyForm] = useState({
    species_id: '',
    assembly_name: '',
    version: '',
    release_date: '',
  });
  const [referenceUpload, setReferenceUpload] = useState({
    assembly_id: '',
    dataset_type: 'cytobands',
  });
  const [autoImportForm, setAutoImportForm] = useState({
    tax_id: '',
    ucsc_genome: '',
    overwrite: false,
  });
  const [referenceFile, setReferenceFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [autoImportError, setAutoImportError] = useState<string | null>(null);
  const [autoImportSuccess, setAutoImportSuccess] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadSuccess, setUploadSuccess] = useState<string | null>(null);
  const [activeModal, setActiveModal] = useState<'add' | 'manual' | 'upload' | null>(null);
  const [confirmAction, setConfirmAction] = useState<{
    title: string;
    message: string;
    confirmLabel: string;
    tone?: 'default' | 'danger';
    run: () => Promise<void>;
    onCancel?: () => void;
  } | null>(null);
  const [confirmBusy, setConfirmBusy] = useState(false);

  const { data: species = [] } = useQuery<Species[]>({
    queryKey: ['species'],
    queryFn: async () => {
      const res = await api.get('/species');
      return (res.data as any[]).map((entry) => withEntityId(entry)) as Species[];
    },
  });

  const { data: assemblies = [] } = useQuery<Assembly[]>({
    queryKey: ['assemblies', 'all'],
    queryFn: async () => {
      const res = await api.get('/assemblies');
      return (res.data as any[]).map((entry) => withEntityId(entry)) as Assembly[];
    },
  });

  const { data: referenceStatuses = [] } = useQuery<AssemblyReferenceStatus[]>({
    queryKey: ['assemblies', 'reference-status'],
    queryFn: async () => {
      const res = await api.get('/assemblies/reference-status');
      return res.data as AssemblyReferenceStatus[];
    },
  });

  const { data: cnvKbStatus } = useQuery<ClinicalCnvKbStatus>({
    queryKey: ['admin', 'clinical-cnv-kb', 'status'],
    enabled: userIsAdmin,
    queryFn: async () => (await api.get('/admin/clinical-cnv-kb/status')).data as ClinicalCnvKbStatus,
    refetchInterval: (query) => (query.state.data?.active_job ? 3000 : false),
    retry: false,
  });

  const { data: geneMetaStatus } = useQuery<GeneMetadataStatus>({
    queryKey: ['admin', 'gene-reference-status'],
    enabled: userIsAdmin,
    queryFn: async () => (await api.get('/admin/gene-reference/status')).data as GeneMetadataStatus,
    refetchInterval: (query) => {
      const active = query.state.data?.active_job;
      return active && (active.status === 'queued' || active.status === 'running') ? 3000 : false;
    },
    retry: false,
  });

  const { data: referenceActivity } = useQuery<ReferenceImportActivity[]>({
    queryKey: ['assemblies', 'reference-import', 'recent'],
    enabled: userIsAdmin,
    queryFn: async () => {
      const res = await api.get('/assemblies/reference-import/recent', {
        params: { limit: 12 },
      });
      return res.data as ReferenceImportActivity[];
    },
  });

  const { data: sourceOrganisms = [] } = useQuery<ReferenceImportSourceOrganism[]>({
    queryKey: ['assemblies', 'reference-import', 'organisms'],
    enabled: userIsAdmin,
    queryFn: async () => {
      const res = await api.get('/assemblies/reference-import/organisms');
      return res.data as ReferenceImportSourceOrganism[];
    },
  });

  const selectedSourceTaxId = autoImportForm.tax_id ? Number(autoImportForm.tax_id) : null;
  const { data: sourceAssemblies = [] } = useQuery<ReferenceImportSourceAssembly[]>({
    queryKey: ['assemblies', 'reference-import', 'assemblies', selectedSourceTaxId],
    enabled: userIsAdmin && selectedSourceTaxId !== null,
    queryFn: async () => {
      const res = await api.get('/assemblies/reference-import/assemblies', {
        params: { tax_id: selectedSourceTaxId },
      });
      return res.data as ReferenceImportSourceAssembly[];
    },
  });

  const assembliesBySpecies = useMemo(() => {
    const grouped = new Map<string, Assembly[]>();
    species.forEach((entry) => grouped.set(entry.id, []));
    assemblies.forEach((assembly) => {
      const current = grouped.get(assembly.species_id) ?? [];
      current.push(assembly);
      grouped.set(assembly.species_id, current);
    });
    grouped.forEach((entries, key) => {
      grouped.set(
        key,
        [...entries].sort((left, right) => {
          const leftDate = left.release_date || '';
          const rightDate = right.release_date || '';
          return rightDate.localeCompare(leftDate) || left.assembly_name.localeCompare(right.assembly_name);
        })
      );
    });
    return grouped;
  }, [assemblies, species]);

  const statusByAssembly = useMemo(
    () => new Map(referenceStatuses.map((entry) => [entry.assembly_id, entry])),
    [referenceStatuses]
  );

  const populatedSpeciesCount = useMemo(
    () => species.filter((entry) => (assembliesBySpecies.get(entry.id) ?? []).length > 0).length,
    [assembliesBySpecies, species]
  );

  const latestAssembly = useMemo(
    () =>
      [...assemblies]
        .filter((entry) => entry.release_date)
        .sort(
          (left, right) =>
            right.release_date.localeCompare(left.release_date) ||
            left.assembly_name.localeCompare(right.assembly_name)
        )[0] ?? null,
    [assemblies]
  );

  const selectedAssemblyStatus = useMemo(
    () => statusByAssembly.get(referenceUpload.assembly_id) ?? null,
    [referenceUpload.assembly_id, statusByAssembly]
  );

  const selectedSourceAssembly = useMemo(
    () => sourceAssemblies.find((entry) => entry.ucsc_genome === autoImportForm.ucsc_genome) ?? null,
    [autoImportForm.ucsc_genome, sourceAssemblies]
  );

  const handleSpeciesSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      await api.post('/species', {
        name: speciesForm.name,
        common_name: speciesForm.common_name,
        tax_id: Number(speciesForm.tax_id),
      });
      setSpeciesForm({ name: '', common_name: '', tax_id: '' });
      setSuccess('Species added.');
      await queryClient.invalidateQueries({ queryKey: ['species'] });
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? 'Failed to add species');
    }
  };

  const handleAssemblySubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      await api.post('/assemblies', assemblyForm);
      setAssemblyForm({
        species_id: '',
        assembly_name: '',
        version: '',
        release_date: '',
      });
      setSuccess('Assembly added.');
      await queryClient.invalidateQueries({ queryKey: ['assemblies', 'all'] });
      await queryClient.invalidateQueries({ queryKey: ['assemblies', 'reference-status'] });
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? 'Failed to add assembly');
    }
  };

  const handleReferenceUpload = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!referenceUpload.assembly_id || !referenceFile) {
      setUploadError('Choose an assembly and a reference file first.');
      setUploadSuccess(null);
      return;
    }

    const formData = new FormData();
    formData.append('file', referenceFile);
    setUploadError(null);
    setUploadSuccess(null);

    const runUpload = async (overwrite: boolean) =>
      api.post(
        `/assemblies/${referenceUpload.assembly_id}/reference-upload/${referenceUpload.dataset_type}`,
        formData,
        {
          params: { overwrite },
          headers: { 'Content-Type': 'multipart/form-data' },
        }
      );

    try {
      const { data } = await runUpload(false);
      setUploadSuccess(
        `Loaded ${data.inserted} ${data.dataset_type.replace('_', ' ')} records into ${data.assembly_name}.`
      );
      setReferenceFile(null);
      await queryClient.invalidateQueries({ queryKey: ['assemblies', 'reference-status'] });
    } catch (err: unknown) {
      if ((err as { response?: { status?: number } })?.response?.status === 409) {
        const datasetLabel = datasetCopy[referenceUpload.dataset_type]?.title ?? 'reference data';
        setConfirmAction({
          title: 'Overwrite existing reference data',
          message: `${datasetLabel} already exist for the selected assembly. Replace them with the uploaded file? This cannot be undone.`,
          confirmLabel: 'Overwrite',
          tone: 'danger',
          run: async () => {
            try {
              const { data } = await runUpload(true);
              setUploadSuccess(
                `Replaced ${data.dataset_type.replace('_', ' ')} for ${data.assembly_name} with ${data.inserted} records.`
              );
              setReferenceFile(null);
              await queryClient.invalidateQueries({ queryKey: ['assemblies', 'reference-status'] });
            } catch (overwriteError: unknown) {
              setUploadError(getErrorMessage(overwriteError, 'Reference upload failed.'));
            }
          },
          onCancel: () => setUploadError('Reference upload cancelled.'),
        });
      } else {
        setUploadError(getErrorMessage(err, 'Reference upload failed.'));
      }
    }
  };

  const handleReferenceImport = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!autoImportForm.tax_id || !autoImportForm.ucsc_genome) {
      setAutoImportError('Choose an organism and assembly first.');
      setAutoImportSuccess(null);
      return;
    }

    setAutoImportError(null);
    setAutoImportSuccess(null);
    setImporting(true);

    try {
      const { data } = await api.post<ReferenceAutoImportResult>('/assemblies/reference-import', {
        tax_id: Number(autoImportForm.tax_id),
        ucsc_genome: autoImportForm.ucsc_genome,
        overwrite: autoImportForm.overwrite,
      });
      const genesPart = data.gene_warning
        ? ''
        : ` and ${data.genes_inserted} genes`;
      let message = `Imported ${data.assembly_name} ${data.assembly_version}: ${data.cytobands_inserted} cytobands${genesPart} loaded.`;
      if (data.gene_warning) {
        message += ` No gene table was available from UCSC (${data.gene_warning}) — you can upload genes manually for this assembly.`;
      }
      setAutoImportSuccess(message);
      setAutoImportForm((current) => ({
        ...current,
        ucsc_genome: '',
      }));
      await queryClient.invalidateQueries({ queryKey: ['species'] });
      await queryClient.invalidateQueries({ queryKey: ['assemblies', 'all'] });
      await queryClient.invalidateQueries({ queryKey: ['assemblies', 'reference-status'] });
      await queryClient.invalidateQueries({ queryKey: ['assemblies', 'reference-import', 'recent'] });
    } catch (err: unknown) {
      setAutoImportError(getErrorMessage(err, 'Automatic reference import failed.'));
    } finally {
      setImporting(false);
    }
  };

  const performCnvRebuild = async (assemblyName: string) => {
    setError(null);
    setSuccess(null);
    try {
      await api.post('/admin/clinical-cnv-kb/rebuild', {
        assembly: assemblyName,
        skip_clinvar: false,
      });
      setSuccess(`Started clinical CNV knowledgebase rebuild for ${assemblyName}.`);
      await queryClient.invalidateQueries({ queryKey: ['admin', 'clinical-cnv-kb', 'status'] });
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Could not start the clinical CNV knowledgebase rebuild.'));
    }
  };

  const performRefreshGeneMetadata = async () => {
    setError(null);
    setSuccess(null);
    try {
      await api.post('/admin/gene-reference/refresh-all');
      setSuccess('Started gene metadata refresh.');
      await queryClient.invalidateQueries({ queryKey: ['admin', 'gene-reference-status'] });
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Could not start the gene metadata refresh.'));
    }
  };

  const requestCnvRebuild = (assemblyName: string) => {
    setConfirmAction({
      title: 'Rebuild clinical CNV knowledgebase',
      message: `Rebuild the clinical CNV knowledgebase for ${assemblyName}? This pulls from ClinGen, UCSC, and ClinVar / OMIM / Orphanet, runs in the background, and may take several minutes.`,
      confirmLabel: 'Rebuild',
      run: () => performCnvRebuild(assemblyName),
    });
  };

  const requestRefreshGeneMetadata = () => {
    setConfirmAction({
      title: 'Refresh gene metadata',
      message:
        'Refresh cached human gene metadata (HGNC / Ensembl / ClinGen)? This runs in the background and may take a while.',
      confirmLabel: 'Refresh',
      run: performRefreshGeneMetadata,
    });
  };

  const handleConfirmAction = async () => {
    if (!confirmAction) {
      return;
    }
    setConfirmBusy(true);
    try {
      await confirmAction.run();
    } finally {
      setConfirmBusy(false);
      setConfirmAction(null);
    }
  };

  const dismissConfirm = () => {
    if (confirmBusy) {
      return;
    }
    confirmAction?.onCancel?.();
    setConfirmAction(null);
  };

  const openUploadFor = (assemblyId: string, datasetType: string) => {
    setReferenceUpload({ assembly_id: assemblyId, dataset_type: datasetType });
    setReferenceFile(null);
    setUploadError(null);
    setUploadSuccess(null);
    setActiveModal('upload');
  };

  /**
   * The action affordance shown beside a per-assembly count: a refresh icon where
   * a source re-sync exists (human gene metadata, human clinical CNV rebuild),
   * otherwise an upload icon that opens the upload modal pre-targeted at this
   * assembly + dataset so admins can supply their own reference file.
   */
  const renderCountAction = (assembly: Assembly, taxId: number, datasetType: string) => {
    if (!userIsAdmin) {
      return null;
    }

    if (datasetType === 'genes' && taxId === 9606) {
      return (
        <button
          type="button"
          className="reference-refresh-icon"
          title="Refresh cached human gene metadata"
          aria-label="Refresh gene metadata"
          disabled={Boolean(geneMetaStatus?.active_job)}
          onClick={requestRefreshGeneMetadata}
        >
          ↻
        </button>
      );
    }

    if (datasetType === 'clinical_cnvs' && taxId === 9606 && cnvKbStatus?.available !== false) {
      return (
        <button
          type="button"
          className="reference-refresh-icon"
          title={`Rebuild clinical CNV knowledgebase for ${assembly.assembly_name}`}
          aria-label="Rebuild clinical CNV knowledgebase"
          disabled={Boolean(cnvKbStatus?.active_job)}
          onClick={() => requestCnvRebuild(assembly.assembly_name)}
        >
          ↻
        </button>
      );
    }

    const datasetLabel = datasetCopy[datasetType]?.title ?? datasetType.replace('_', ' ');
    return (
      <button
        type="button"
        className="reference-refresh-icon reference-refresh-icon--upload"
        title={`Upload ${datasetLabel} for ${assembly.assembly_name}`}
        aria-label={`Upload ${datasetLabel}`}
        onClick={() => openUploadFor(assembly.id, datasetType)}
      >
        ↥
      </button>
    );
  };

  return (
    <div className="page-shell admin-compact space-y-5">
      <section className="surface-card dashboard-hero dashboard-hero-compact">
        <div className="dashboard-hero-shell">
          <div className="page-header">
            <div className="space-y-1">
              <p className="page-kicker">Reference catalog</p>
              <h1 className="catalog-card-title">Organisms and assemblies</h1>
            </div>
            <div className="reference-summary-grid">
              <div className="surface-card-muted reference-summary-card">
                <span className="reference-summary-value">{species.length}</span>
                <span className="reference-summary-label">Species in catalog</span>
              </div>
              <div className="surface-card-muted reference-summary-card">
                <span className="reference-summary-value">{assemblies.length}</span>
                <span className="reference-summary-label">Assemblies available</span>
              </div>
              <div className="surface-card-muted reference-summary-card">
                <span className="reference-summary-value">{populatedSpeciesCount}</span>
                <span className="reference-summary-label">Species with assembly support</span>
              </div>
            </div>
          </div>
          <aside className="surface-card-muted dashboard-hero-panel">
            <p className="page-kicker">Catalog status</p>
            <div className="dashboard-link-stack">
              <p className="dashboard-link-note">
                Latest dated assembly:{' '}
                <strong>{latestAssembly ? `${latestAssembly.assembly_name} ${latestAssembly.version}` : 'None yet'}</strong>
                {latestAssembly?.release_date ? ` (${latestAssembly.release_date})` : ''}
              </p>
              {!userIsAdmin && (
                <p className="dashboard-link-note">
                  Read-only: only admins can register species, assemblies, or uploads.
                </p>
              )}
            </div>
          </aside>
        </div>
      </section>

      {userIsAdmin && (
        <div className="compact-toolbar reference-data-toolbar">
          <button type="button" className="form-button" onClick={() => setActiveModal('add')}>
            Add organism / assembly
          </button>
          <button type="button" className="button-secondary" onClick={() => setActiveModal('upload')}>
            Upload reference data
          </button>
          <button type="button" className="button-secondary" onClick={() => setActiveModal('manual')}>
            Manual entry
          </button>
        </div>
      )}

      {(error || success) && (
        <section className="surface-card">
          <p className="section-copy" style={{ color: error ? 'var(--color-signature-red-dark)' : 'var(--color-secondary)' }}>
            {error ?? success}
          </p>
        </section>
      )}

      <section className="space-y-5">
        <section className="surface-card space-y-4">
          <div className="analysis-toolbar items-center">
            <h2 className="section-title">Configured species and assemblies</h2>
            {userIsAdmin ? (
              <Link
                to="/admin/reference/gene-reference"
                className="subtle-link"
                style={{ marginLeft: 'auto' }}
              >
                Advanced gene sync →
              </Link>
            ) : null}
          </div>
          {species.length === 0 ? (
            <p className="section-copy">
              No species are configured yet. Add one first, then attach one or more assemblies to
              it.
            </p>
          ) : (
            <div className="data-table-shell reference-catalog-shell">
              <table className="analysis-table reference-catalog-table">
                <thead>
                  <tr>
                    <th>Organism</th>
                    <th>Catalog summary</th>
                    <th>Latest assembly</th>
                  </tr>
                </thead>
                <tbody>
                  {species.map((entry) => {
                    const speciesAssemblies = assembliesBySpecies.get(entry.id) ?? [];
                    const latestSpeciesAssembly = speciesAssemblies[0] ?? null;
                    return (
                      <React.Fragment key={entry.id}>
                        <tr className="reference-catalog-species-row">
                          <td>
                            <div className="reference-catalog-species-main">
                              <div className="reference-catalog-species-head">
                                <h3 className="reference-catalog-species-title">{entry.name}</h3>
                                <span className="badge-chip">
                                  {speciesAssemblies.length}{' '}
                                  {speciesAssemblies.length === 1 ? 'assembly' : 'assemblies'}
                                </span>
                              </div>
                              <p className="reference-catalog-species-meta">
                                {entry.common_name} • tax id {entry.tax_id}
                              </p>
                            </div>
                          </td>
                          <td className="reference-catalog-summary-cell">
                            {speciesAssemblies.length === 0 ? (
                              <span className="dashboard-link-note">
                                No assemblies added yet
                              </span>
                            ) : (
                              <div className="table-chip-list">
                                <span className="table-chip table-chip--strong">
                                  {speciesAssemblies.length} in catalog
                                </span>
                                <span className="table-chip table-chip--neutral">
                                  {speciesAssemblies.filter((assembly) => (statusByAssembly.get(assembly.id)?.genes ?? 0) > 0).length} with genes
                                </span>
                                <span className="table-chip table-chip--neutral">
                                  {speciesAssemblies.filter((assembly) => (statusByAssembly.get(assembly.id)?.chromosomes ?? 0) > 0).length} with cytobands
                                </span>
                              </div>
                            )}
                          </td>
                          <td className="reference-catalog-latest-cell">
                            {latestSpeciesAssembly ? (
                              <div className="reference-catalog-latest-main">
                                <strong>
                                  {latestSpeciesAssembly.assembly_name} {latestSpeciesAssembly.version}
                                </strong>
                                <span className="dashboard-link-note">
                                  {latestSpeciesAssembly.release_date || 'No release date'}
                                </span>
                              </div>
                            ) : (
                              <span className="dashboard-link-note">No assembly metadata yet</span>
                            )}
                          </td>
                        </tr>
                        <tr className="reference-catalog-detail-row">
                          <td colSpan={3}>
                            <div className="reference-catalog-detail">
                              {speciesAssemblies.length === 0 ? (
                                <p className="section-copy">
                                  No assemblies have been added for this species yet.
                                </p>
                              ) : (
                                <div className="overflow-x-auto">
                                  <table className="analysis-table reference-catalog-assembly-table">
                                    <thead>
                                      <tr>
                                        <th>Assembly</th>
                                        <th>Release</th>
                                        <th>Cytobands</th>
                                        <th>Genes</th>
                                        <th>Blacklist</th>
                                        <th>Clin CNVs</th>
                                        <th>SegDup/LCR</th>
                                        <th>DGV</th>
                                        <th>Last updated</th>
                                      </tr>
                                    </thead>
                                    <tbody>
                                      {speciesAssemblies.map((assembly) => {
                                        const status = statusByAssembly.get(assembly.id);
                                        const lastImports = status?.last_imports ?? [];
                                        const latestImport = lastImports.reduce<
                                          ReferenceDatasetImport | null
                                        >(
                                          (latest, entry) =>
                                            !latest || entry.performed_at > latest.performed_at
                                              ? entry
                                              : latest,
                                          null,
                                        );
                                        const lastUpdatedTooltip = lastImports
                                          .map(
                                            (entry) =>
                                              `${entry.dataset_type}: ${formatDateTime(entry.performed_at)}` +
                                              (entry.performed_by ? ` by ${entry.performed_by}` : ''),
                                          )
                                          .join('\n');
                                        return (
                                          <tr key={assembly.id}>
                                            <td>
                                              <div className="reference-catalog-assembly-main">
                                                <strong className="reference-assembly-title">
                                                  {assembly.assembly_name}
                                                </strong>
                                                <span className="dashboard-link-note">
                                                  Version {assembly.version}
                                                </span>
                                              </div>
                                            </td>
                                            <td className="table-mono">
                                              {assembly.release_date || '—'}
                                            </td>
                                            <td className="table-mono">
                                              <span className="reference-count-with-action">
                                                {formatCatalogCount(status?.chromosomes)}
                                                {renderCountAction(assembly, entry.tax_id, 'cytobands')}
                                              </span>
                                            </td>
                                            <td className="table-mono">
                                              <span className="reference-count-with-action">
                                                {formatCatalogCount(status?.genes)}
                                                {renderCountAction(assembly, entry.tax_id, 'genes')}
                                              </span>
                                            </td>
                                            <td className="table-mono">
                                              <span className="reference-count-with-action">
                                                {formatCatalogCount(status?.blacklist_regions)}
                                                {renderCountAction(assembly, entry.tax_id, 'blacklist')}
                                              </span>
                                            </td>
                                            <td className="table-mono">
                                              <span className="reference-count-with-action">
                                                {formatCatalogCount(status?.clinical_cnvs)}
                                                {renderCountAction(assembly, entry.tax_id, 'clinical_cnvs')}
                                              </span>
                                            </td>
                                            <td className="table-mono">
                                              <span className="reference-count-with-action">
                                                {formatCatalogCount(status?.segmental_duplications)}
                                                {renderCountAction(assembly, entry.tax_id, 'segmental_duplications')}
                                              </span>
                                            </td>
                                            <td className="table-mono">
                                              <span className="reference-count-with-action">
                                                {formatCatalogCount(status?.dgv)}
                                                {renderCountAction(assembly, entry.tax_id, 'dgv')}
                                              </span>
                                            </td>
                                            <td className="table-mono" title={lastUpdatedTooltip || undefined}>
                                              {latestImport ? (
                                                <span>
                                                  {formatDate(latestImport.performed_at)}
                                                  {latestImport.performed_by ? (
                                                    <span className="dashboard-link-note">
                                                      {' '}
                                                      by {latestImport.performed_by}
                                                    </span>
                                                  ) : null}
                                                </span>
                                              ) : (
                                                <span className="table-empty">—</span>
                                              )}
                                            </td>
                                          </tr>
                                        );
                                      })}
                                    </tbody>
                                  </table>
                                </div>
                              )}
                            </div>
                          </td>
                        </tr>
                      </React.Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="space-y-4">
          {activeModal === 'add' && (
          <AdminModal
            title="Add organism / assembly (from UCSC)"
            onClose={() => {
              if (!importing) {
                setActiveModal(null);
              }
            }}
          >
            {autoImportError && (
              <p className="section-copy" style={{ color: 'var(--color-signature-red-dark)' }}>
                {autoImportError}
              </p>
            )}
            {autoImportSuccess && (
              <p className="section-copy" style={{ color: 'var(--color-secondary)' }}>
                {autoImportSuccess}
              </p>
            )}
            {userIsAdmin ? (
              <form onSubmit={handleReferenceImport} className="field-grid">
                <label className="field-label">
                  Source organism
                  <select
                    value={autoImportForm.tax_id}
                    onChange={(e) =>
                      setAutoImportForm({
                        tax_id: e.target.value,
                        ucsc_genome: '',
                        overwrite: autoImportForm.overwrite,
                      })
                    }
                    disabled={importing}
                  >
                    <option value="">Select organism</option>
                    {sourceOrganisms.map((entry) => (
                      <option key={entry.tax_id} value={entry.tax_id}>
                        {entry.scientific_name}
                        {entry.common_name ? ` (${entry.common_name})` : ''} • {entry.assembly_count} assemblies
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field-label">
                  Source assembly
                  <select
                    value={autoImportForm.ucsc_genome}
                    onChange={(e) =>
                      setAutoImportForm((current) => ({
                        ...current,
                        ucsc_genome: e.target.value,
                      }))
                    }
                    disabled={!autoImportForm.tax_id || importing}
                  >
                    <option value="">Select assembly</option>
                    {sourceAssemblies.map((entry) => (
                      <option key={entry.ucsc_genome} value={entry.ucsc_genome}>
                        {entry.assembly_name} {entry.assembly_version} ({entry.ucsc_genome})
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field-label">
                  Replace existing cytobands and genes
                  <input
                    type="checkbox"
                    checked={autoImportForm.overwrite}
                    onChange={(e) =>
                      setAutoImportForm((current) => ({
                        ...current,
                        overwrite: e.target.checked,
                      }))
                    }
                    disabled={importing}
                  />
                </label>
                <button
                  type="submit"
                  className="form-button w-full justify-center"
                  disabled={importing || !autoImportForm.ucsc_genome}
                >
                  {importing ? 'Downloading from UCSC…' : 'Download cytobands and genes'}
                </button>
                {importing && (
                  <div className="reference-import-progress" aria-live="polite">
                    <div className="reference-import-progress-shell">
                      <div className="reference-import-progress-bar" />
                    </div>
                    <p className="dashboard-link-note">
                      Fetching cytobands and gene tables from UCSC — this can take up to a minute.
                    </p>
                  </div>
                )}
              </form>
            ) : (
              <p className="section-copy">
                Admin access is required to import organisms and assemblies from upstream sources.
              </p>
            )}
            {selectedSourceAssembly && (
              <div className="dashboard-link-stack">
                <p className="dashboard-link-note">
                  <strong>{selectedSourceAssembly.assembly_name}</strong> will be stored locally as
                  version <strong>{selectedSourceAssembly.assembly_version}</strong> and mapped to
                  UCSC assembly <strong>{selectedSourceAssembly.ucsc_genome}</strong>.
                </p>
                <p className="dashboard-link-note">
                  Release date: {selectedSourceAssembly.release_date || 'unknown'}.
                </p>
                <p className="dashboard-link-note">
                  Upstream source: {selectedSourceAssembly.source_name || 'UCSC'}.
                </p>
                <p className="dashboard-link-note">{selectedSourceAssembly.description}</p>
              </div>
            )}
          </AdminModal>
          )}

          {activeModal === 'upload' && (
          <AdminModal
            title="Upload reference data"
            onClose={() => setActiveModal(null)}
            inactive={Boolean(confirmAction)}
          >
            {uploadError && (
              <p className="section-copy" style={{ color: 'var(--color-signature-red-dark)' }}>
                {uploadError}
              </p>
            )}
            {uploadSuccess && (
              <p className="section-copy" style={{ color: 'var(--color-secondary)' }}>
                {uploadSuccess}
              </p>
            )}
            {userIsAdmin ? (
              <form onSubmit={handleReferenceUpload} className="field-grid">
                <label className="field-label">
                  Assembly
                  <select
                    value={referenceUpload.assembly_id}
                    onChange={(e) =>
                      setReferenceUpload((current) => ({
                        ...current,
                        assembly_id: e.target.value,
                      }))
                    }
                  >
                    <option value="">Select assembly</option>
                    {assemblies.map((entry) => (
                      <option key={entry.id} value={entry.id}>
                        {entry.assembly_name} {entry.version}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field-label">
                  Dataset
                  <select
                    value={referenceUpload.dataset_type}
                    onChange={(e) =>
                      setReferenceUpload((current) => ({
                        ...current,
                        dataset_type: e.target.value,
                      }))
                    }
                  >
                    <option value="cytobands">Cytobands</option>
                    <option value="genes">Genes</option>
                    <option value="blacklist">Blacklist</option>
                    <option value="clinical_cnvs">Clinical CNVs</option>
                    <option value="segmental_duplications">Segmental duplications/LCRs</option>
                    <option value="dgv">DGV variants</option>
                  </select>
                </label>
                <label className="field-label">
                  Reference file
                  <input
                    type="file"
                    accept=".txt,.tsv,.bed,.gz"
                    onChange={(e) => setReferenceFile(e.target.files?.[0] || null)}
                  />
                </label>
                <button type="submit" className="form-button w-full justify-center">
                  Upload file
                </button>
              </form>
            ) : (
              <p className="section-copy">
                Admin access is required to upload reference files.
              </p>
            )}
            <div className="dashboard-link-stack">
              <p className="dashboard-link-note">
                <strong>{datasetCopy[referenceUpload.dataset_type]?.title ?? 'Reference data'}:</strong>{' '}
                {datasetCopy[referenceUpload.dataset_type]?.description ?? 'Upload a reference data file for the selected assembly.'}
              </p>
              {selectedAssemblyStatus && (
                <p className="dashboard-link-note">
                  Current assembly status for <strong>{selectedAssemblyStatus.assembly_name}</strong>:
                  {' '}cytobands {selectedAssemblyStatus.chromosomes}, genes {selectedAssemblyStatus.genes},
                  {' '}blacklist {selectedAssemblyStatus.blacklist_regions}, clinical CNVs {selectedAssemblyStatus.clinical_cnvs},
                  {' '}SegDup/LCR {selectedAssemblyStatus.segmental_duplications ?? 0}.
                </p>
              )}
            </div>
          </AdminModal>
          )}

          {activeModal === 'manual' && (
          <AdminModal title="Manual entry — species & assembly" onClose={() => setActiveModal(null)}>
            <div className="space-y-4">
            <div className="space-y-3">
            <h3 className="section-title">Add organism manually</h3>
            {userIsAdmin ? (
              <form onSubmit={handleSpeciesSubmit} className="field-grid">
                <label className="field-label">
                  Scientific name
                  <input
                    value={speciesForm.name}
                    onChange={(e) =>
                      setSpeciesForm((current) => ({ ...current, name: e.target.value }))
                    }
                    placeholder="Homo sapiens"
                  />
                </label>
                <label className="field-label">
                  Common name
                  <input
                    value={speciesForm.common_name}
                    onChange={(e) =>
                      setSpeciesForm((current) => ({ ...current, common_name: e.target.value }))
                    }
                    placeholder="human"
                  />
                </label>
                <label className="field-label">
                  Taxonomy id
                  <input
                    value={speciesForm.tax_id}
                    onChange={(e) =>
                      setSpeciesForm((current) => ({ ...current, tax_id: e.target.value }))
                    }
                    placeholder="9606"
                  />
                </label>
                <button type="submit" className="form-button w-full justify-center">
                  Add species
                </button>
              </form>
            ) : (
              <p className="section-copy">
                Admin access is required to add a new species entry.
              </p>
            )}
            </div>

            <div className="space-y-3">
            <h3 className="section-title">Add assembly manually</h3>
            {userIsAdmin ? (
              <form onSubmit={handleAssemblySubmit} className="field-grid">
                <label className="field-label">
                  Species
                  <select
                    value={assemblyForm.species_id}
                    onChange={(e) =>
                      setAssemblyForm((current) => ({
                        ...current,
                        species_id: e.target.value,
                      }))
                    }
                  >
                    <option value="">Select species</option>
                    {species.map((entry) => (
                      <option key={entry.id} value={entry.id}>
                        {entry.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field-label">
                  Assembly name
                  <input
                    value={assemblyForm.assembly_name}
                    onChange={(e) =>
                      setAssemblyForm((current) => ({
                        ...current,
                        assembly_name: e.target.value,
                      }))
                    }
                    placeholder="GRCh38"
                  />
                </label>
                <label className="field-label">
                  Version
                  <input
                    value={assemblyForm.version}
                    onChange={(e) =>
                      setAssemblyForm((current) => ({
                        ...current,
                        version: e.target.value,
                      }))
                    }
                    placeholder="p14"
                  />
                </label>
                <label className="field-label">
                  Release date
                  <input
                    type="date"
                    value={assemblyForm.release_date}
                    onChange={(e) =>
                      setAssemblyForm((current) => ({
                        ...current,
                        release_date: e.target.value,
                      }))
                    }
                  />
                </label>
                <button type="submit" className="form-button w-full justify-center">
                  Add assembly
                </button>
              </form>
            ) : (
              <p className="section-copy">
                Admin access is required to attach a new assembly to the catalog.
              </p>
            )}
            </div>
            </div>
          </AdminModal>
          )}
        </section>
      </section>

      {confirmAction && (
        <AdminModal title={confirmAction.title} onClose={dismissConfirm}>
          <div className="space-y-4">
            <p className="section-copy">{confirmAction.message}</p>
            <div className="analysis-toolbar items-center" style={{ justifyContent: 'flex-end', gap: '0.5rem' }}>
              <button
                type="button"
                className="button-ghost"
                onClick={dismissConfirm}
                disabled={confirmBusy}
                autoFocus
              >
                Cancel
              </button>
              <button
                type="button"
                className={confirmAction.tone === 'danger' ? 'button-danger' : 'form-button'}
                onClick={handleConfirmAction}
                disabled={confirmBusy}
              >
                {confirmBusy ? 'Working…' : confirmAction.confirmLabel}
              </button>
            </div>
          </div>
        </AdminModal>
      )}

      {userIsAdmin && referenceActivity && referenceActivity.length > 0 ? (
        <section className="surface-card space-y-3">
          <h2 className="section-title">Recent reference activity</h2>
          <div className="data-table-shell overflow-x-auto">
            <table className="analysis-table">
              <thead>
                <tr>
                  <th>When</th>
                  <th>By</th>
                  <th>Assembly</th>
                  <th>Dataset</th>
                  <th>Rows</th>
                  <th>Source</th>
                </tr>
              </thead>
              <tbody>
                {referenceActivity.map((event, index) => (
                  <tr key={`${event.performed_at}-${event.assembly_id}-${event.dataset_type}-${index}`}>
                    <td className="table-mono">{formatDateTime(event.performed_at)}</td>
                    <td>{event.performed_by || 'system'}</td>
                    <td>
                      <strong>{event.assembly_name}</strong>
                      <span className="dashboard-link-note"> · {event.species_name}</span>
                    </td>
                    <td>{datasetCopy[event.dataset_type]?.title ?? event.dataset_type}</td>
                    <td className="table-mono">
                      {formatCatalogCount(event.inserted)}
                      <span className="dashboard-link-note">
                        {' '}
                        {event.replaced ? 'replaced' : 'added'}
                      </span>
                    </td>
                    <td className="table-mono">{event.source || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
    </div>
  );
};

export default ReferenceCatalogPage;
