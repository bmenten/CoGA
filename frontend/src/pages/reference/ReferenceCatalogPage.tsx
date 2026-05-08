import React, { useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
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

interface AssemblyReferenceStatus {
  assembly_id: string;
  assembly_name: string;
  chromosomes: number;
  genes: number;
  blacklist_regions: number;
  clinical_cnvs: number;
}

interface ReferenceImportSourceOrganism {
  scientific_name: string;
  common_name: string;
  tax_id: number;
  assembly_count: number;
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
}

const formatCatalogCount = (value: number | undefined) => (value ?? 0).toLocaleString();

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
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadSuccess, setUploadSuccess] = useState<string | null>(null);

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
        .sort((left, right) => right.release_date.localeCompare(left.release_date))[0] ?? null,
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
  };

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
        const overwrite = window.confirm(
          'Reference data of this type already exist for the selected assembly. Overwrite them?'
        );
        if (overwrite) {
          try {
            const { data } = await runUpload(true);
            setUploadSuccess(
              `Replaced ${data.dataset_type.replace('_', ' ')} for ${data.assembly_name} with ${data.inserted} records.`
            );
            await queryClient.invalidateQueries({ queryKey: ['assemblies', 'reference-status'] });
          } catch (overwriteError: unknown) {
            setUploadError(getErrorMessage(overwriteError, 'Reference upload failed.'));
          }
        } else {
          setUploadError('Reference upload cancelled.');
        }
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

    try {
      const { data } = await api.post<ReferenceAutoImportResult>('/assemblies/reference-import', {
        tax_id: Number(autoImportForm.tax_id),
        ucsc_genome: autoImportForm.ucsc_genome,
        overwrite: autoImportForm.overwrite,
      });
      setAutoImportSuccess(
        `Imported ${data.assembly_name} ${data.assembly_version}: ${data.cytobands_inserted} cytobands and ${data.genes_inserted} genes loaded.`
      );
      setAutoImportForm((current) => ({
        ...current,
        ucsc_genome: '',
      }));
      await queryClient.invalidateQueries({ queryKey: ['species'] });
      await queryClient.invalidateQueries({ queryKey: ['assemblies', 'all'] });
      await queryClient.invalidateQueries({ queryKey: ['assemblies', 'reference-status'] });
    } catch (err: unknown) {
      setAutoImportError(getErrorMessage(err, 'Automatic reference import failed.'));
    }
  };

  return (
    <div className="page-shell space-y-8">
      <section className="surface-card dashboard-hero dashboard-hero-compact">
        <div className="dashboard-hero-shell">
          <div className="page-header">
            <div className="space-y-2">
              <p className="page-kicker">Reference catalog</p>
              <h1 className="catalog-card-title">Organisms and assemblies</h1>
              <p className="catalog-card-copy">
                Review which organisms and assemblies are already available in the database, and
                add new entries when the platform needs to support another reference context.
              </p>
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
                Use this page to confirm which reference contexts already exist before creating a
                project or starting an import.
              </p>
              <p className="dashboard-link-note">
                Latest dated assembly:{' '}
                <strong>{latestAssembly ? `${latestAssembly.assembly_name} ${latestAssembly.version}` : 'None yet'}</strong>
                {latestAssembly?.release_date ? ` (${latestAssembly.release_date})` : ''}
              </p>
              {!userIsAdmin && (
                <p className="dashboard-link-note">
                  You can inspect the catalog here, but only admins can register new species,
                  assemblies, or reference uploads.
                </p>
              )}
            </div>
          </aside>
        </div>
      </section>

      {(error || success) && (
        <section className="surface-card">
          <p className="section-copy" style={{ color: error ? 'var(--color-signature-red-dark)' : 'var(--color-secondary)' }}>
            {error ?? success}
          </p>
        </section>
      )}

      <section className="grid gap-6 xl:grid-cols-[minmax(0,1.45fr)_minmax(21rem,0.95fr)]">
        <section className="surface-card space-y-6">
          <div>
            <p className="page-kicker">Catalog</p>
            <h2 className="section-title">Configured species and assemblies</h2>
            <p className="section-copy">
              Each organism row expands to show the assemblies already registered for that species,
              together with the imported reference layers available on each assembly.
            </p>
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
                                      </tr>
                                    </thead>
                                    <tbody>
                                      {speciesAssemblies.map((assembly) => {
                                        const status = statusByAssembly.get(assembly.id);
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
                                              {formatCatalogCount(status?.chromosomes)}
                                            </td>
                                            <td className="table-mono">
                                              {formatCatalogCount(status?.genes)}
                                            </td>
                                            <td className="table-mono">
                                              {formatCatalogCount(status?.blacklist_regions)}
                                            </td>
                                            <td className="table-mono">
                                              {formatCatalogCount(status?.clinical_cnvs)}
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

        <section className="space-y-6">
          <section className="surface-card space-y-4">
            <div>
              <p className="page-kicker">How it is used</p>
              <h2 className="section-title">Reference context in the platform</h2>
            </div>
            <div className="dashboard-link-stack">
              <p className="dashboard-link-note">
                Species define the organism-level container. Assemblies define the coordinate system
                that projects, genes, chromosomes, and imported variant data are tied to.
              </p>
              <p className="dashboard-link-note">
                Add the species first, then add one or more assemblies, then create or update
                projects so new datasets can use that reference context.
              </p>
              <p className="dashboard-link-note">
                Admins can bootstrap UCSC-backed organisms and assemblies here, then fall back to
                manual file uploads for custom reference layers that are not available upstream.
              </p>
            </div>
          </section>

          <section className="surface-card space-y-4">
            <div>
              <p className="page-kicker">Automatic setup</p>
              <h2 className="section-title">Import organism, assembly, cytobands, and genes</h2>
            </div>
            <p className="section-copy">
              Select a UCSC-backed organism and assembly and the platform will create the local
              species and assembly records when needed, then download cytobands and gene
              annotations automatically.
            </p>
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
                    disabled={!autoImportForm.tax_id}
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
                  />
                </label>
                <button type="submit" className="form-button w-full justify-center">
                  Download cytobands and genes
                </button>
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
          </section>

          <section className="surface-card space-y-4">
            <div>
              <p className="page-kicker">Reference files</p>
              <h2 className="section-title">Upload assembly reference data</h2>
            </div>
            <p className="section-copy">
              Use this form for custom assemblies or extra reference layers that are not available
              through the automatic UCSC import flow. Uploads are admin-only and replace existing
              data only when you explicitly confirm overwrite.
            </p>
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
                  Upload reference data
                </button>
              </form>
            ) : (
              <p className="section-copy">
                Admin access is required to upload reference files.
              </p>
            )}
            <div className="dashboard-link-stack">
              <p className="dashboard-link-note">
                <strong>{datasetCopy[referenceUpload.dataset_type].title}:</strong>{' '}
                {datasetCopy[referenceUpload.dataset_type].description}
              </p>
              {selectedAssemblyStatus && (
                <p className="dashboard-link-note">
                  Current assembly status for <strong>{selectedAssemblyStatus.assembly_name}</strong>:
                  {' '}cytobands {selectedAssemblyStatus.chromosomes}, genes {selectedAssemblyStatus.genes},
                  {' '}blacklist {selectedAssemblyStatus.blacklist_regions}, clinical CNVs {selectedAssemblyStatus.clinical_cnvs}.
                </p>
              )}
            </div>
          </section>

          <section className="surface-card space-y-4">
            <div>
              <p className="page-kicker">Species</p>
              <h2 className="section-title">Add organism</h2>
            </div>
            <p className="section-copy">
              Use manual species creation only for organisms that are not available through the
              automatic import flow above.
            </p>
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
          </section>

          <section className="surface-card space-y-4">
            <div>
              <p className="page-kicker">Assemblies</p>
              <h2 className="section-title">Add assembly</h2>
            </div>
            <p className="section-copy">
              Manual assembly creation remains available for references that do not have a matching
              UCSC source or need custom naming.
            </p>
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
          </section>
        </section>
      </section>
    </div>
  );
};

export default ReferenceCatalogPage;
