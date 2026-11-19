import React, { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import ModalDialog from '../../components/ModalDialog';
import PageHeader from '../../components/PageHeader';
import PageState from '../../components/PageState';
import api from '../../lib/api';
import { apiPath } from '../../lib/apiPath';
import type {
  AssemblyOut,
  NiptArtifactAutoSeedOut,
  NiptArtifactAutoSeedPreviewOut,
  NiptArtifactImportOut,
  NiptArtifactOut,
  NiptArtifactScopeOut,
  NiptArtifactSeedVariantOut,
} from '../../lib/apiSchema.generated';
import { getErrorMessage } from '../../lib/errorMessage';
import type { StatusTone } from './dataManagementTypes';

const DEFAULT_SCOPE = 'nipt_cfdna';
const DEFAULT_THRESHOLD = 3;
// The preview and the seed's answer show this many alleles at first; the rest on request.
const SHOWN_AT_FIRST = 200;

const when = (value: string | null | undefined): string =>
  value ? `${value.replace('T', ' ').slice(0, 16)} UTC` : '';

const plural = (count: number, one: string, many = `${one}s`): string =>
  `${count.toLocaleString()} ${count === 1 ? one : many}`;

/**
 * A typed allele as the artifact list stores it (``build_small_variant_id``): the chromosome
 * without a ``chr`` prefix, the position, REF and ALT, joined by ``-``. Reads the stored form
 * (``7-1000-A-G``), the R pipeline's ``variant_key`` (``chr7:1000:A:G``) and ``chr7:1000 A>G``.
 * Null when the text is not one allele: no whole position from 1, or a REF or ALT that is not
 * bases (a second ALT, ``.`` or ``*``).
 */
export const parseArtifactVariant = (value: string): string | null => {
  const parts = value.trim().split(/[\s:>-]+/);
  if (parts.length !== 4) return null;
  const [chromText, positionText, refText, altText] = parts;
  const chrom = chromText.replace(/^chr/i, '');
  if (!chrom || !/^\d+$/.test(positionText)) return null;
  const position = Number(positionText);
  if (!Number.isSafeInteger(position) || position < 1) return null;
  const ref = refText.toUpperCase();
  const alt = altText.toUpperCase();
  if (!/^[ACGTN]+$/.test(ref) || !/^[ACGTN]+$/.test(alt)) return null;
  return `${chrom}-${position}-${ref}-${alt}`;
};

const describeListed = (variant: NiptArtifactSeedVariantOut): string =>
  variant.listed_source ? `Listed (${variant.listed_source}): count refreshed` : 'New';

/** The alleles of a preview or a seed, the most recurrent first, with what the seed does to each. */
const SeedVariantsTable: React.FC<{ variants: NiptArtifactSeedVariantOut[]; caption: string }> = ({
  variants,
  caption,
}) => {
  const [showAll, setShowAll] = useState(false);
  const shown = showAll ? variants : variants.slice(0, SHOWN_AT_FIRST);
  return (
    <>
      <div className="data-table-shell overflow-x-auto">
        <table className="analysis-table">
          <caption className="sr-only">{caption}</caption>
          <thead>
            <tr>
              <th>Variant</th>
              <th>Unexplained low-VAF calls</th>
              <th>On the list</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((variant) => (
              <tr key={variant.variant_id}>
                <td className="font-mono">{variant.variant_id}</td>
                <td>{variant.recurrence_count.toLocaleString()}</td>
                <td className={variant.listed_source ? 'table-subtle' : undefined}>{describeListed(variant)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {variants.length > shown.length && (
        <button type="button" className="button-ghost" onClick={() => setShowAll(true)}>
          Show all {variants.length.toLocaleString()} alleles
        </button>
      )}
    </>
  );
};

/** The new and listed alleles of a preview or a seed, in words. */
const describeSeedVariants = (variants: NiptArtifactSeedVariantOut[]): string => {
  const listed = variants.filter((variant) => variant.listed_source).length;
  return `${plural(variants.length - listed, 'new allele')}, ${plural(listed, 'allele')} on the list already`;
};

/**
 * The monogenic-NIPT recurrent-artefact list (``nipt_artifact_variants``), per assembly and
 * assay scope: its entries, the auto-seed with a read-only preview, the R pipeline's recurrent
 * table, and a curated entry added or removed. Every NIPT analysis of the scope removes the
 * listed alleles, except one its annotation protects (common, or ClinVar may call it
 * pathogenic). Each write is confirmed first and is a clinical audit event on the chain
 * ``system:nipt-artifacts``; an admin's alone, as the endpoints are.
 */
const AdminNiptArtifactsPage: React.FC = () => {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<{ tone: StatusTone; message: string } | null>(null);
  const [assemblyChoice, setAssemblyChoice] = useState('');
  const [scope, setScope] = useState(DEFAULT_SCOPE);
  const [filter, setFilter] = useState('');
  // The auto-seed: the threshold as typed, its preview and the seed's answer.
  const [thresholdText, setThresholdText] = useState(String(DEFAULT_THRESHOLD));
  const [preview, setPreview] = useState<NiptArtifactAutoSeedPreviewOut | null>(null);
  const [seeded, setSeeded] = useState<NiptArtifactAutoSeedOut | null>(null);
  const [confirmSeed, setConfirmSeed] = useState(false);
  // The pipeline's table: the file, its confirmation and what the import did.
  const [tableFile, setTableFile] = useState<File | null>(null);
  const [fileInputKey, setFileInputKey] = useState(0);
  const [confirmImport, setConfirmImport] = useState(false);
  const [imported, setImported] = useState<{ filename: string; result: NiptArtifactImportOut } | null>(null);
  // A curated entry: the dialog's fields; and the entry whose removal is being confirmed.
  const [adding, setAdding] = useState(false);
  const [variantText, setVariantText] = useState('');
  const [labelText, setLabelText] = useState('');
  const [removing, setRemoving] = useState<NiptArtifactOut | null>(null);

  const assembliesQuery = useQuery<AssemblyOut[]>({
    queryKey: ['assemblies', 'all'],
    queryFn: async () => (await api.get(apiPath`/assemblies`)).data as AssemblyOut[],
    retry: false,
  });
  const assemblies = assembliesQuery.data ?? [];
  const assembly = assemblies.find((item) => item._id === assemblyChoice) ?? assemblies[0] ?? null;
  const assemblyId = assembly?._id ?? '';

  const scopesKey = ['admin', 'nipt-artifacts', 'scopes', assemblyId];
  const scopesQuery = useQuery<NiptArtifactScopeOut[]>({
    queryKey: scopesKey,
    queryFn: async () =>
      (await api.get(apiPath`/admin/nipt/artifacts/scopes`, { params: { assembly_id: assemblyId } }))
        .data as NiptArtifactScopeOut[],
    enabled: Boolean(assemblyId),
    retry: false,
  });
  const scopes = scopesQuery.data ?? [];
  const activeScope = scopes.find((item) => item.assay_key === scope);

  const entriesKey = ['admin', 'nipt-artifacts', 'entries', assemblyId, scope];
  const entriesQuery = useQuery<NiptArtifactOut[]>({
    queryKey: entriesKey,
    queryFn: async () =>
      (
        await api.get(apiPath`/admin/nipt/artifacts`, {
          params: { assembly_id: assemblyId, assay_key: scope },
        })
      ).data as NiptArtifactOut[],
    enabled: Boolean(assemblyId),
    retry: false,
  });
  const entries = useMemo(() => entriesQuery.data ?? [], [entriesQuery.data]);
  const visibleEntries = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return entries;
    return entries.filter(
      (entry) =>
        entry.variant_id.toLowerCase().includes(needle) || (entry.label ?? '').toLowerCase().includes(needle),
    );
  }, [entries, filter]);

  const threshold = /^\d+$/.test(thresholdText.trim()) ? Number(thresholdText.trim()) : null;
  const thresholdValid = threshold !== null && Number.isSafeInteger(threshold) && threshold >= 1;
  const scopeLabel = `${scope} on ${assembly?.assembly_name ?? 'the assembly'}`;

  // What the page shows of the scope is read again after every write; so are the NIPT pages,
  // whose analyses read the list.
  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ['admin', 'nipt-artifacts'] });
    await queryClient.invalidateQueries({ queryKey: ['family'] });
  };

  // A change of assembly or scope leaves the preview and the answers of the last one behind.
  const resetResults = () => {
    setPreview(null);
    setSeeded(null);
    setImported(null);
    setStatus(null);
  };

  const previewMutation = useMutation({
    mutationFn: async (minUnexplained: number) =>
      (
        await api.get(apiPath`/admin/nipt/artifacts/auto-seed/preview`, {
          params: { assembly_id: assemblyId, assay_key: scope, min_unexplained_samples: minUnexplained },
        })
      ).data as NiptArtifactAutoSeedPreviewOut,
    onSuccess: (data) => {
      setSeeded(null);
      setPreview(data);
    },
    onError: (err) =>
      setStatus({ tone: 'error', message: getErrorMessage(err, 'Could not read the auto-seed preview.') }),
  });

  const seedMutation = useMutation({
    mutationFn: async (minUnexplained: number) =>
      (
        await api.post(apiPath`/admin/nipt/artifacts/auto-seed`, {
          assembly_id: assemblyId,
          assay_key: scope,
          min_unexplained_samples: minUnexplained,
        })
      ).data as NiptArtifactAutoSeedOut,
    onSuccess: async (data) => {
      setPreview(null);
      setSeeded(data);
      await refresh();
      setStatus({
        tone: 'success',
        message:
          data.seeded > 0
            ? `Auto-seeded ${plural(data.seeded, 'allele')} into the ${data.assay_key} list at ${data.min_unexplained_samples} samples.`
            : `The auto-seed at ${data.min_unexplained_samples} samples found no allele to list; the list is unchanged.`,
      });
    },
    onError: (err) => setStatus({ tone: 'error', message: getErrorMessage(err, 'Could not auto-seed the list.') }),
  });

  const importMutation = useMutation({
    mutationFn: async (file: File) => {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('assembly_id', assemblyId);
      formData.append('assay_key', scope);
      return (
        await api.post(apiPath`/admin/nipt/artifacts/import`, formData, {
          headers: { 'Content-Type': 'multipart/form-data' },
        })
      ).data as NiptArtifactImportOut;
    },
    onSuccess: async (result, file) => {
      setImported({ filename: file.name, result });
      setTableFile(null);
      setFileInputKey((key) => key + 1);
      await refresh();
      setStatus({
        tone: 'success',
        message: `Imported ${plural(result.imported, 'allele')} from ${file.name} into the ${scope} list.`,
      });
    },
    onError: (err) =>
      setStatus({ tone: 'error', message: getErrorMessage(err, 'Could not import the artefact table.') }),
  });

  const addMutation = useMutation({
    mutationFn: async (entry: { variantId: string; label: string | null; recurrenceCount: number }) =>
      (
        await api.post(apiPath`/admin/nipt/artifacts`, {
          assembly_id: assemblyId,
          assay_key: scope,
          variant_id: entry.variantId,
          label: entry.label,
          source: 'curated',
          recurrence_count: entry.recurrenceCount,
        })
      ).data as NiptArtifactOut,
    onSuccess: async (row) => {
      await refresh();
      setStatus({ tone: 'success', message: `Listed ${row.variant_id} as a curated entry of the ${row.assay_key} list.` });
    },
    onError: (err) => setStatus({ tone: 'error', message: getErrorMessage(err, 'Could not add the entry.') }),
  });

  const removeMutation = useMutation({
    mutationFn: async (entry: NiptArtifactOut) => {
      await api.delete(apiPath`/admin/nipt/artifacts/${entry.id}`);
      return entry;
    },
    onSuccess: async (entry) => {
      await refresh();
      setStatus({ tone: 'success', message: `Removed ${entry.variant_id} from the ${entry.assay_key} list.` });
    },
    onError: (err) => setStatus({ tone: 'error', message: getErrorMessage(err, 'Could not remove the entry.') }),
  });

  if (assembliesQuery.isLoading) {
    return (
      <PageState
        loading
        kicker="Admin"
        title="Loading the NIPT artefact list"
        message="Fetching the assemblies the list is kept for."
      />
    );
  }

  if (assembliesQuery.error) {
    return (
      <PageState
        kicker="Admin"
        title="Could not load the NIPT artefact list"
        message={getErrorMessage(assembliesQuery.error, 'The assemblies could not be loaded.')}
      />
    );
  }

  const busy =
    seedMutation.isPending || importMutation.isPending || addMutation.isPending || removeMutation.isPending;
  const typedVariant = parseArtifactVariant(variantText);
  const existing = typedVariant ? entries.find((entry) => entry.variant_id === typedVariant) : undefined;

  const closeAdd = () => {
    setAdding(false);
    setVariantText('');
    setLabelText('');
  };

  return (
    <div className="page-shell admin-compact space-y-5">
      <PageHeader
        kicker="Administration"
        title="NIPT artefact list"
        description={
          <>
            The alleles that recur as an artefact in the cfDNA of one assay. Every monogenic NIPT analysis of the
            assay removes them, except an allele its annotation marks as more than 5% frequent or that ClinVar may
            call pathogenic: that one stays in, flagged. Each list is kept per assembly and assay scope. Every add,
            removal, auto-seed and import is a clinical audit event on the chain system:nipt-artifacts.
          </>
        }
      >
        {status && (
          <div className={`status-note ${status.tone === 'success' ? 'status-note--success' : 'status-note--error'}`}>
            {status.message}
          </div>
        )}
      </PageHeader>

      <section className="surface-card space-y-3">
        <div className="family-workspace-card-head">
          <div className="space-y-1">
            <h2 className="qc-section-label">Scope</h2>
            <p className="family-workspace-card-subtitle">
              A cfDNA sample&apos;s scope is its <code>assay_panel</code>, or {DEFAULT_SCOPE} without one. The samples
              are counted in every project.
            </p>
          </div>
        </div>
        <div className="admin-inline-form">
          <label className="field-label">
            Assembly
            <select
              value={assemblyId}
              onChange={(event) => {
                setAssemblyChoice(event.target.value);
                setScope(DEFAULT_SCOPE);
                resetResults();
              }}
            >
              {assemblies.map((item) => (
                <option key={item._id} value={item._id}>
                  {item.assembly_name}
                </option>
              ))}
            </select>
          </label>
          <label className="field-label">
            Assay scope
            <select
              value={scope}
              onChange={(event) => {
                setScope(event.target.value);
                resetResults();
              }}
            >
              {(scopes.length ? scopes : [{ assay_key: DEFAULT_SCOPE, cfdna_samples: 0, listed: 0 }]).map((item) => (
                <option key={item.assay_key} value={item.assay_key}>
                  {item.assay_key} ({plural(item.cfdna_samples, 'cfDNA sample')}, {item.listed.toLocaleString()} listed)
                </option>
              ))}
            </select>
          </label>
        </div>
        {assemblies.length === 0 && <p className="dashboard-link-note">No assembly is loaded yet.</p>}
        {scopesQuery.error && (
          <div className="status-note status-note--error">
            {getErrorMessage(scopesQuery.error, 'The scopes could not be loaded.')}
          </div>
        )}
      </section>

      <section className="surface-card space-y-3">
        <div className="family-workspace-card-head">
          <div className="space-y-1">
            <h2 className="qc-section-label">Entries</h2>
            <p className="family-workspace-card-subtitle">
              The {scopeLabel} list: {plural(entries.length, 'allele')}. The recurrence is the number of cfDNA samples
              in which the auto-seed or the pipeline&apos;s table found the allele.
            </p>
          </div>
          <button type="button" className="form-button" disabled={!assemblyId || busy} onClick={() => setAdding(true)}>
            Add an entry
          </button>
        </div>
        <label className="field-label admin-inline-form__filter">
          Filter
          <input
            type="search"
            value={filter}
            placeholder="Variant or label"
            onChange={(event) => setFilter(event.target.value)}
          />
        </label>
        {entriesQuery.error ? (
          <div className="status-note status-note--error">
            {getErrorMessage(entriesQuery.error, 'The entries could not be loaded.')}
          </div>
        ) : (
          <div className="data-table-shell overflow-x-auto">
            <table className="analysis-table">
              <thead>
                <tr>
                  <th>Variant</th>
                  <th>Source</th>
                  <th>Recurrence</th>
                  <th>Label</th>
                  <th>Added</th>
                  <th>Updated</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {visibleEntries.map((entry) => (
                  <tr key={entry.id}>
                    <td className="font-mono">{entry.variant_id}</td>
                    <td>{entry.source}</td>
                    <td>{entry.recurrence_count.toLocaleString()}</td>
                    <td>{entry.label ?? <span className="table-subtle">—</span>}</td>
                    <td className="table-subtle">{when(entry.created_at)}</td>
                    <td className="table-subtle">{when(entry.updated_at)}</td>
                    <td>
                      <button
                        type="button"
                        className="button-ghost"
                        disabled={busy}
                        aria-label={`Remove ${entry.variant_id}`}
                        onClick={() => setRemoving(entry)}
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                ))}
                {visibleEntries.length === 0 && (
                  <tr>
                    <td colSpan={7} className="table-subtle">
                      {entriesQuery.isLoading
                        ? 'Loading the entries…'
                        : entries.length
                          ? 'No entry matches the filter.'
                          : 'The list of this scope is empty.'}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="surface-card space-y-3">
        <div className="family-workspace-card-head">
          <div className="space-y-1">
            <h2 className="qc-section-label">Auto-seed</h2>
            <p className="family-workspace-card-subtitle">
              Lists the alleles that are an unexplained low-VAF call (below 0.25, not carried by that family&apos;s
              father) in at least the given number of cfDNA samples of the scope
              {activeScope ? ` (${plural(activeScope.cfdna_samples, 'sample')})` : ''}. A maternal het and the
              fetus&apos;s paternal allele do not count. Frequent alleles and those ClinVar may call pathogenic are never
              listed. A pregnancy without a father&apos;s sample cannot explain its paternal alleles: review the auto
              entries. Preview first; the preview writes nothing.
            </p>
          </div>
        </div>
        <form
          className="admin-inline-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (thresholdValid && threshold !== null) previewMutation.mutate(threshold);
          }}
        >
          <label className="field-label admin-inline-form__narrow">
            Minimum samples
            <input
              type="number"
              min={1}
              step={1}
              value={thresholdText}
              aria-invalid={!thresholdValid}
              onChange={(event) => {
                setThresholdText(event.target.value);
                setPreview(null);
              }}
            />
          </label>
          <button type="submit" className="form-button" disabled={!assemblyId || !thresholdValid || previewMutation.isPending}>
            {previewMutation.isPending ? 'Reading…' : 'Preview'}
          </button>
        </form>
        {!thresholdValid && <p className="field-hint">A whole number from 1.</p>}

        {preview && (
          <div className="space-y-2">
            <p className="dashboard-link-note">
              At {preview.min_unexplained_samples} samples, read in {plural(preview.cfdna_samples, 'cfDNA sample')}, the
              seed would list {plural(preview.variants.length, 'allele')}: {describeSeedVariants(preview.variants)}. A
              listed entry keeps its source and label; the seed refreshes its recurrence.
            </p>
            {preview.variants.length > 0 && (
              <>
                <SeedVariantsTable variants={preview.variants} caption="Alleles the auto-seed would list" />
                <button type="button" className="form-button" disabled={busy} onClick={() => setConfirmSeed(true)}>
                  Seed the list
                </button>
              </>
            )}
          </div>
        )}

        {seeded && (
          <div className="space-y-2">
            <p className="dashboard-link-note">
              Seeded at {seeded.min_unexplained_samples} samples, read in {plural(seeded.cfdna_samples, 'cfDNA sample')}
              : {plural(seeded.seeded, 'allele')} ({describeSeedVariants(seeded.variants)}).
            </p>
            {seeded.variants.length > 0 && (
              <SeedVariantsTable variants={seeded.variants} caption="Alleles the auto-seed listed" />
            )}
          </div>
        )}
      </section>

      <section className="surface-card space-y-3">
        <div className="family-workspace-card-head">
          <div className="space-y-1">
            <h2 className="qc-section-label">Import the pipeline&apos;s table</h2>
            <p className="family-workspace-card-subtitle">
              The NIPT-M pipeline&apos;s <code>recurrent_cfdna_artifact_filter_*.tsv</code>, or a tab-separated list of
              alleles (a <code>variant_key</code> column, or CHROM, POS, REF and ALT). Only the rows it marks{' '}
              <code>filter_as_recurrent_artifact</code> are added, as curated entries; frequent alleles and those
              ClinVar may call pathogenic are refused.
            </p>
          </div>
        </div>
        <form
          className="admin-inline-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (tableFile) setConfirmImport(true);
          }}
        >
          <label className="field-label">
            Artefact table
            <input
              key={fileInputKey}
              type="file"
              accept=".tsv,.txt,text/tab-separated-values,text/plain"
              onChange={(event) => setTableFile(event.target.files?.[0] ?? null)}
            />
          </label>
          <button type="submit" className="form-button" disabled={!assemblyId || !tableFile || busy}>
            Import
          </button>
        </form>
        {imported && (
          <div className="admin-data-summary" aria-label={`What the import of ${imported.filename} did`}>
            {(
              [
                ['Rows read', imported.result.rows_read],
                ['Not flagged', imported.result.not_flagged],
                ['Unreadable', imported.result.invalid],
                ['Imported', imported.result.imported],
                ['Refused: frequent', imported.result.protected_common],
                ['Refused: ClinVar', imported.result.protected_clinvar],
              ] as const
            ).map(([label, value]) => (
              <span key={label} className="admin-data-summary-item">
                <span className="admin-data-summary-label">{label}</span>
                <span className="admin-data-summary-value">{value.toLocaleString()}</span>
              </span>
            ))}
          </div>
        )}
      </section>

      {confirmSeed && preview && (
        <ModalDialog onClose={() => setConfirmSeed(false)} label="Confirm the auto-seed" confirmDiscard={false}>
          {(requestClose) => (
            <>
              <h2 className="section-title">Auto-seed the {scopeLabel} list</h2>
              <p className="section-copy">
                {`The seed lists the alleles that are an unexplained low-VAF call in at least ${preview.min_unexplained_samples} cfDNA samples of the scope. The preview found ${plural(preview.variants.length, 'allele')} (${describeSeedVariants(preview.variants)}). The seed reads the samples again when it runs, and shows what it wrote. Every NIPT analysis of the scope then removes these alleles.`}
              </p>
              <div className="compact-toolbar family-toolbar">
                <button
                  type="button"
                  className="form-button"
                  disabled={seedMutation.isPending}
                  onClick={() => {
                    setConfirmSeed(false);
                    seedMutation.mutate(preview.min_unexplained_samples);
                  }}
                >
                  Seed
                </button>
                <button type="button" className="button-ghost" onClick={requestClose}>
                  Cancel
                </button>
              </div>
            </>
          )}
        </ModalDialog>
      )}

      {confirmImport && tableFile && (
        <ModalDialog onClose={() => setConfirmImport(false)} label="Confirm the import" confirmDiscard={false}>
          {(requestClose) => (
            <>
              <h2 className="section-title">Import {tableFile.name}</h2>
              <p className="section-copy">
                {`The alleles the table flags are added to the ${scopeLabel} list as curated entries; an allele already listed keeps its source and label and takes the table's recurrence. Every NIPT analysis of the scope then removes them.`}
              </p>
              <div className="compact-toolbar family-toolbar">
                <button
                  type="button"
                  className="form-button"
                  disabled={importMutation.isPending}
                  onClick={() => {
                    setConfirmImport(false);
                    importMutation.mutate(tableFile);
                  }}
                >
                  Import
                </button>
                <button type="button" className="button-ghost" onClick={requestClose}>
                  Cancel
                </button>
              </div>
            </>
          )}
        </ModalDialog>
      )}

      {adding && (
        <ModalDialog
          onClose={closeAdd}
          label="Add an entry"
          discardMessage="Discard the entry you have typed?"
          isDirty={() => variantText.trim() !== '' || labelText.trim() !== ''}
        >
          {(requestClose) => (
            <>
              <h2 className="section-title">Add a curated entry to the {scopeLabel} list</h2>
              <p className="section-copy">
                Every NIPT analysis of the scope then removes this allele, unless its annotation marks it as frequent or
                ClinVar may call it pathogenic.
              </p>
              <div className="field-grid">
                <label className="field-label">
                  Variant
                  <input
                    type="text"
                    value={variantText}
                    aria-invalid={variantText.trim() !== '' && typedVariant === null}
                    placeholder="7-1000-A-G or chr7:1000:A:G"
                    onChange={(event) => setVariantText(event.target.value)}
                  />
                  <span className="field-hint">
                    {typedVariant
                      ? `Stored as ${typedVariant}.`
                      : 'Chromosome, position, REF and ALT of one allele.'}
                  </span>
                </label>
                {existing && (
                  <p className="report-disclaimer">
                    {`${existing.variant_id} is on the list already (${existing.source}, recurrence ${existing.recurrence_count}). Adding it makes it a curated entry with this label and keeps its recurrence.`}
                  </p>
                )}
                <label className="field-label">
                  Label
                  <input
                    type="text"
                    value={labelText}
                    placeholder="e.g. recurrent in the run's no-template control"
                    onChange={(event) => setLabelText(event.target.value)}
                  />
                </label>
              </div>
              <div className="compact-toolbar family-toolbar">
                <button
                  type="button"
                  className="form-button"
                  disabled={!typedVariant || addMutation.isPending}
                  onClick={() => {
                    if (!typedVariant) return;
                    const entry = {
                      variantId: typedVariant,
                      label: labelText.trim() || null,
                      recurrenceCount: existing?.recurrence_count ?? 0,
                    };
                    closeAdd();
                    addMutation.mutate(entry);
                  }}
                >
                  Add to the list
                </button>
                <button type="button" className="button-ghost" onClick={requestClose}>
                  Cancel
                </button>
              </div>
            </>
          )}
        </ModalDialog>
      )}

      {removing && (
        <ModalDialog onClose={() => setRemoving(null)} label="Confirm the removal" confirmDiscard={false}>
          {(requestClose) => (
            <>
              <h2 className="section-title">Remove {removing.variant_id}</h2>
              <p className="section-copy">
                {`The ${removing.source} entry leaves the ${removing.assay_key} list on ${assembly?.assembly_name ?? 'the assembly'}: every NIPT analysis of the scope keeps the allele again. The removal is a clinical audit event with the entry as it was.`}
              </p>
              <div className="compact-toolbar family-toolbar">
                <button
                  type="button"
                  className="button-danger"
                  disabled={removeMutation.isPending}
                  onClick={() => {
                    const entry = removing;
                    setRemoving(null);
                    removeMutation.mutate(entry);
                  }}
                >
                  Remove
                </button>
                <button type="button" className="button-ghost" onClick={requestClose}>
                  Cancel
                </button>
              </div>
            </>
          )}
        </ModalDialog>
      )}
    </div>
  );
};

export default AdminNiptArtifactsPage;
