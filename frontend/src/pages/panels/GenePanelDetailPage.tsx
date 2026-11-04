import React, { useMemo, useState } from 'react';
import { useParams } from 'react-router';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import { isAdmin } from '../../lib/auth';
import PageState from '../../components/PageState';
import QueryFailure from '../../components/QueryFailure';
import { getErrorMessage, type ApiErrorLike } from '../../lib/errorMessage';
import type {
  GeneLocation,
  GenePanel,
  GenePanelVersionList,
} from '../../lib/apiTypes';
import type { GenePanelCreateResponse, GeneSymbolCheckOut } from '../../lib/apiSchema.generated';
import { apiPath } from '../../lib/apiPath';
import { formatDateTime } from '../../lib/format';

// The region table's columns: the field each sorts and filters on, and its heading.
const REGION_COLUMNS: Array<['gene' | 'assembly' | 'chr' | 'start' | 'end', string]> = [
  ['gene', 'Gene'],
  ['assembly', 'Assembly'],
  ['chr', 'Chromosome'],
  ['start', 'Start'],
  ['end', 'End'],
];

/** A gene name as the panel compares it: a symbol is the same gene in any letter case. */
const geneKey = (gene: string): string => gene.trim().toUpperCase();

/** Gene names typed in a box, separated by commas, semicolons or white space: each once, in
 * the order typed. */
const parseGeneNames = (value: string): string[] => {
  const seen = new Set<string>();
  const names: string[] = [];
  for (const part of value.split(/[\s,;]+/)) {
    const name = part.trim();
    if (!name || seen.has(geneKey(name))) continue;
    seen.add(geneKey(name));
    names.push(name);
  }
  return names;
};

/** The edits waiting for the admin's confirmation: nothing is saved before it. */
interface StagedChanges {
  remove: string[];
  add: string[];
}

const NO_CHANGES: StagedChanges = { remove: [], add: [] };

const listNames = (names: string[]): string => names.join(', ');

const TrashIcon: React.FC = () => (
  <svg viewBox="0 0 16 16" aria-hidden="true" className="panel-gene-trash-icon">
    <path
      d="M6 2.5h4M2.5 4.5h11M4 4.5l.6 8.6a1 1 0 0 0 1 .9h4.8a1 1 0 0 0 1-.9l.6-8.6M6.75 7v4.5M9.25 7v4.5"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.3"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);

const GenePanelDetailPage: React.FC = () => {
  const { panelId } = useParams();
  const queryClient = useQueryClient();
  const userIsAdmin = isAdmin();
  const {
    data: panel,
    isError: panelFailed,
    error: panelError,
    refetch: refetchPanel,
  } = useQuery<GenePanel>({
    queryKey: ['panel', panelId],
    queryFn: async () => {
      const res = await api.get(apiPath`/panels/${panelId}`);
      return res.data as GenePanel;
    },
  });

  const {
    data: versionList,
    isError: versionsFailed,
    error: versionsError,
    refetch: refetchVersions,
  } = useQuery<GenePanelVersionList>({
    queryKey: ['panel', panelId, 'versions'],
    enabled: Boolean(panelId),
    queryFn: async () =>
      (await api.get(apiPath`/panels/${panelId}/versions`)).data as GenePanelVersionList,
  });

  const [staged, setStaged] = useState<StagedChanges>(NO_CHANGES);
  const [addInput, setAddInput] = useState('');
  const [addNote, setAddNote] = useState('');
  const [listEditing, setListEditing] = useState(false);
  const [listText, setListText] = useState('');
  const [editStatus, setEditStatus] = useState<{ tone: 'success' | 'error'; text: string } | null>(null);
  const [saving, setSaving] = useState(false);

  const [sortKey, setSortKey] = useState<keyof GeneLocation>('gene');
  const [sortAsc, setSortAsc] = useState(true);
  const [filters, setFilters] = useState({
    gene: '',
    assembly: '',
    chr: '',
    start: '',
    end: '',
  });

  const currentGenes = useMemo(() => panel?.genes ?? [], [panel]);
  const currentKeys = useMemo(() => new Set(currentGenes.map(geneKey)), [currentGenes]);
  // What the staged changes do to the panel as loaded: a removal of a gene it no longer
  // holds, or an addition of one it holds already, changes nothing.
  const removals = staged.remove.filter((gene) => currentKeys.has(geneKey(gene)));
  const additions = staged.add.filter((gene) => !currentKeys.has(geneKey(gene)));
  const removalKeys = new Set(removals.map(geneKey));
  const nextGenes = [...currentGenes.filter((gene) => !removalKeys.has(geneKey(gene))), ...additions];
  const hasChanges = removals.length > 0 || additions.length > 0;

  // The names to add are checked before they are saved: a name no loaded assembly knows
  // would be stored without coordinates.
  const {
    data: additionCheck,
    isError: additionCheckFailed,
    isFetching: checkingAdditions,
  } = useQuery<GeneSymbolCheckOut>({
    queryKey: ['panel-gene-check', additions],
    enabled: additions.length > 0,
    staleTime: Infinity,
    queryFn: async () =>
      (await api.post(apiPath`/panels/genes/check`, { genes: additions })).data as GeneSymbolCheckOut,
  });
  const missingAdditionKeys = new Set((additionCheck?.missing ?? []).map(geneKey));
  const notFoundAdditions = additions.filter((gene) => missingAdditionKeys.has(geneKey(gene)));

  const refreshPanel = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ['panel', panelId] }),
      queryClient.invalidateQueries({ queryKey: ['panel', panelId, 'versions'] }),
      queryClient.invalidateQueries({ queryKey: ['panels'] }),
    ]);

  const stageRemoval = (gene: string) => {
    setEditStatus(null);
    setStaged((current) =>
      current.remove.some((name) => geneKey(name) === geneKey(gene))
        ? current
        : { ...current, remove: [...current.remove, gene] },
    );
  };

  const keepGene = (gene: string) =>
    setStaged((current) => ({
      ...current,
      remove: current.remove.filter((name) => geneKey(name) !== geneKey(gene)),
    }));

  const dropAddition = (gene: string) =>
    setStaged((current) => ({
      ...current,
      add: current.add.filter((name) => geneKey(name) !== geneKey(gene)),
    }));

  const stageAdditions = (event: React.FormEvent) => {
    event.preventDefault();
    const names = parseGeneNames(addInput);
    if (!names.length) return;
    const already: string[] = [];
    let remove = staged.remove;
    const add = [...staged.add];
    for (const name of names) {
      const key = geneKey(name);
      if (currentKeys.has(key)) {
        // A gene the panel holds: adding it back takes back its removal.
        if (remove.some((gene) => geneKey(gene) === key)) {
          remove = remove.filter((gene) => geneKey(gene) !== key);
        } else {
          already.push(name);
        }
      } else if (!add.some((gene) => geneKey(gene) === key)) {
        add.push(name);
      }
    }
    setEditStatus(null);
    setStaged({ remove, add });
    setAddInput('');
    setAddNote(already.length ? `Already in the panel: ${listNames(already)}.` : '');
  };

  const openListEditor = () => {
    setListText(nextGenes.join(', '));
    setEditStatus(null);
    setListEditing(true);
  };

  // The whole list, as edited, becomes the changes it makes to the panel.
  const reviewList = () => {
    const desired = parseGeneNames(listText);
    const desiredKeys = new Set(desired.map(geneKey));
    setStaged({
      remove: currentGenes.filter((gene) => !desiredKeys.has(geneKey(gene))),
      add: desired.filter((gene) => !currentKeys.has(geneKey(gene))),
    });
    setAddNote('');
    setListEditing(false);
    if (desired.length === currentGenes.length && desired.every((gene) => currentKeys.has(geneKey(gene)))) {
      setEditStatus({ tone: 'success', text: 'The list is unchanged: there is nothing to save.' });
    }
  };

  const discardChanges = () => {
    setStaged(NO_CHANGES);
    setAddNote('');
    setListEditing(false);
  };

  const confirmChanges = async () => {
    if (!panel) return;
    setSaving(true);
    setEditStatus(null);
    try {
      const res = await api.put<GenePanelCreateResponse>(apiPath`/panels/${panelId}`, {
        genes: nextGenes,
        expected_version: panel.version ?? 1,
      });
      setEditStatus({ tone: 'success', text: res.data.message || 'Panel updated' });
      setStaged(NO_CHANGES);
      setAddNote('');
      await refreshPanel();
    } catch (err) {
      const response = (err as ApiErrorLike).response;
      const detail = response?.data?.detail;
      if (response?.status === 409) {
        // Another version was saved meanwhile. The changes stay listed: they are read again
        // against the panel as it is now.
        setEditStatus({
          tone: 'error',
          text: `${typeof detail === 'string' ? detail : 'The panel has changed since you opened it.'} It has been reloaded: check the changes against it, then confirm again.`,
        });
        await refreshPanel();
      } else {
        setEditStatus({ tone: 'error', text: typeof detail === 'string' ? detail : 'Error updating panel' });
      }
    } finally {
      setSaving(false);
    }
  };

  const handleSort = (key: keyof GeneLocation) => {
    if (sortKey === key) {
      setSortAsc(!sortAsc);
    } else {
      setSortKey(key);
      setSortAsc(true);
    }
  };

  const handleFilterChange = (
    e: React.ChangeEvent<HTMLInputElement>,
    key: keyof GeneLocation,
  ) => {
    const { value } = e.target;
    setFilters((f) => ({ ...f, [key]: value }));
  };

  const regions = useMemo(() => {
    if (!panel) return [];
    return [...panel.regions]
      .filter((r) =>
        (!filters.gene ||
          r.gene.toLowerCase().includes(filters.gene.toLowerCase())) &&
        (!filters.assembly ||
          (r.assembly ?? '').toLowerCase().includes(filters.assembly.toLowerCase())) &&
        (!filters.chr ||
          r.chr.toLowerCase().includes(filters.chr.toLowerCase())) &&
        (!filters.start || String(r.start).includes(filters.start)) &&
        (!filters.end || String(r.end).includes(filters.end)),
      )
      .sort((a, b) => {
        const aVal = a[sortKey] ?? '';
        const bVal = b[sortKey] ?? '';
        if (aVal < bVal) return sortAsc ? -1 : 1;
        if (aVal > bVal) return sortAsc ? 1 : -1;
        return 0;
      });
  }, [panel, filters, sortKey, sortAsc]);

  // The names in the panel's gene list that got no coordinates in any loaded assembly when
  // the version was saved: a typo, an old symbol, a name no gene reference knows.
  const unresolvedGenes = useMemo(() => {
    if (!panel) return [];
    const located = new Set(panel.regions.map((region) => geneKey(region.gene)));
    return panel.genes.filter((gene) => !located.has(geneKey(gene)));
  }, [panel]);

  // It stayed at "Loading gene panel" for good (#610).
  if (panelFailed) {
    return (
      <PageState
        kicker="Panel detail"
        title="Gene panel could not be loaded"
        message={getErrorMessage(panelError, 'The gene panel could not be retrieved.')}
        action={
          <button type="button" className="button-secondary" onClick={() => void refetchPanel()}>
            Retry
          </button>
        }
      />
    );
  }
  if (!panel) {
    return (
      <PageState
        loading
        kicker="Panel detail"
        title="Loading gene panel"
        message="Preparing panel regions and sorting controls."
      />
    );
  }
  const sourceLabel =
    panel.source === 'panelapp'
      ? 'PanelApp'
      : panel.source === 'mendeliome'
        ? 'Mendeliome'
        : 'Local';
  const isLocal = !panel.source || panel.source === 'local';
  const canEdit = userIsAdmin && isLocal;
  const currentVersion = panel.version ?? 1;
  const nextVersion = currentVersion + 1;
  const unresolvedCount = unresolvedGenes.length;

  const removeButton = (gene: string) => (
    <button
      type="button"
      className="panel-gene-remove"
      aria-label={`Remove ${gene} from the next version`}
      title={`Remove ${gene} from the next version`}
      onClick={() => stageRemoval(gene)}
      disabled={saving}
    >
      <TrashIcon />
    </button>
  );

  return (
    <div className="page-shell space-y-6">
      <section className="surface-card page-top-card">
        <div className="page-header">
          <div className="space-y-2">
            <p className="page-kicker">Panel detail</p>
            <h2 className="catalog-card-title">
              {panel.name} <span className="panel-version-chip">v{currentVersion}</span>
            </h2>
            <p className="catalog-card-copy">
              Filter and sort the genomic regions in this panel.
            </p>
            <p className="catalog-card-copy">
              Created {formatDateTime(panel.created_at)} by {panel.created_by_email || panel.created_by} •{' '}
              {panel.gene_count ?? panel.genes.length} genes
              {unresolvedCount ? ` (${unresolvedCount} without coordinates)` : ''}
            </p>
            <p className="catalog-card-copy">
              {sourceLabel}
              {panel.external_id ? ` ${panel.external_id}` : ''}
              {panel.external_version ? ` · version ${panel.external_version}` : ''}
              {panel.source_updated_at ? ` · updated ${formatDateTime(panel.source_updated_at)}` : ''}
            </p>
            {panel.description && <p className="catalog-card-copy">{panel.description}</p>}
            {panel.external_url && (
              <a className="table-link" href={panel.external_url} target="_blank" rel="noreferrer">
                Open source panel
              </a>
            )}
            {userIsAdmin && !isLocal && (
              <p className="catalog-card-copy table-subtle">
                This panel is generated/imported — update it from the panel catalog
                (regenerate / re-import), not by editing genes here.
              </p>
            )}
            {editStatus ? (
              editStatus.tone === 'success' ? (
                <p className="form-status">{editStatus.text}</p>
              ) : (
                <p className="status-note status-note--error" role="alert">
                  {editStatus.text}
                </p>
              )
            ) : null}
          </div>
        </div>
        {unresolvedCount > 0 && (
          <div className="status-note status-note--warning panel-unresolved-note" role="note">
            <p>
              <strong>
                {unresolvedCount === 1 ? '1 gene name was' : `${unresolvedCount} gene names were`} not imported.
              </strong>{' '}
              No loaded assembly knows {unresolvedCount === 1 ? 'it' : 'them'} as a gene, so{' '}
              {unresolvedCount === 1 ? 'it has' : 'they have'} no coordinates, and a filter on this panel finds no
              variant in {unresolvedCount === 1 ? 'it' : 'them'}. Check the spelling, or use the current HGNC symbol.
            </p>
            <ul className="panel-gene-chip-list" aria-label="Gene names not imported">
              {unresolvedGenes.map((gene) => (
                <li key={gene} className="table-chip table-chip--warning">
                  {gene}
                  {canEdit ? (
                    removalKeys.has(geneKey(gene)) ? (
                      <span className="panel-gene-chip-note">removed in v{nextVersion}</span>
                    ) : (
                      removeButton(gene)
                    )
                  ) : null}
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      {versionsFailed ? (
        <QueryFailure
          what="the panel's version history"
          error={versionsError}
          onRetry={() => void refetchVersions()}
        />
      ) : null}
      {versionList?.versions?.length ? (
        <section className="surface-card space-y-3">
          <h3 className="section-title">Version history</h3>
          <p className="section-copy">
            Every version is archived with a timestamp. Deleting the panel deletes its versions
            too.
          </p>
          <div className="data-table-shell overflow-x-auto">
            <table className="analysis-table">
              <thead>
                <tr>
                  <th>Version</th>
                  <th># Genes</th>
                  <th>Source release</th>
                  <th>Created</th>
                  <th>By</th>
                </tr>
              </thead>
              <tbody>
                {versionList.versions.map((v) => (
                  <tr key={v.version}>
                    <td>
                      v{v.version}
                      {v.version === versionList.current_version ? (
                        <span className="table-subtle"> (current)</span>
                      ) : null}
                    </td>
                    <td>{v.gene_count}</td>
                    <td>{v.external_version || '—'}</td>
                    <td>{formatDateTime(v.created_at)}</td>
                    <td>{v.created_by_email || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
      {/* Right above the genes they change, where a bin was clicked. */}
      {canEdit && hasChanges && (
        <section className="surface-card space-y-3" aria-labelledby="panel-pending-changes-title">
          <h3 id="panel-pending-changes-title" className="section-title">
            Changes for version {nextVersion}
          </h3>
          <p className="section-copy">
            Nothing is saved yet. Confirming saves version {nextVersion} with {nextGenes.length}{' '}
            {nextGenes.length === 1 ? 'gene' : 'genes'}; version {currentVersion} stays in the version history.
          </p>
          {removals.length > 0 && (
            <div className="space-y-1">
              <p className="panel-change-label">Remove ({removals.length})</p>
              <ul className="panel-gene-chip-list" aria-label={`Genes to remove in version ${nextVersion}`}>
                {removals.map((gene) => (
                  <li key={gene} className="table-chip table-chip--critical">
                    {gene}
                    <button
                      type="button"
                      className="button-link"
                      aria-label={`Keep ${gene}`}
                      onClick={() => keepGene(gene)}
                      disabled={saving}
                    >
                      Keep
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {additions.length > 0 && (
            <div className="space-y-1">
              <p className="panel-change-label">Add ({additions.length})</p>
              <ul className="panel-gene-chip-list" aria-label={`Genes to add in version ${nextVersion}`}>
                {additions.map((gene) => {
                  const notFound = missingAdditionKeys.has(geneKey(gene));
                  return (
                    <li
                      key={gene}
                      className={`table-chip ${notFound ? 'table-chip--warning' : 'table-chip--success'}`}
                    >
                      {gene}
                      {notFound ? <span className="panel-gene-chip-note">not found</span> : null}
                      <button
                        type="button"
                        className="button-link"
                        aria-label={`Do not add ${gene}`}
                        onClick={() => dropAddition(gene)}
                        disabled={saving}
                      >
                        ×
                      </button>
                    </li>
                  );
                })}
              </ul>
              {checkingAdditions ? (
                <p className="table-subtle">Checking the names against the loaded assemblies…</p>
              ) : additionCheckFailed ? (
                <p className="status-note status-note--warning" role="note">
                  The names could not be checked against the loaded assemblies. A name no assembly knows is
                  saved without coordinates; the panel lists it as not imported.
                </p>
              ) : notFoundAdditions.length > 0 ? (
                <div className="status-note status-note--warning" role="note">
                  <p>
                    {notFoundAdditions.length === 1
                      ? `${notFoundAdditions[0]} matches no gene in a loaded assembly. Saved, it gets no coordinates, and a filter on the panel finds no variant in it.`
                      : `${listNames(notFoundAdditions)} match no gene in a loaded assembly. Saved, they get no coordinates, and a filter on the panel finds no variant in them.`}
                  </p>
                  <button
                    type="button"
                    className="button-link"
                    onClick={() =>
                      setStaged((current) => ({
                        ...current,
                        add: current.add.filter((gene) => !missingAdditionKeys.has(geneKey(gene))),
                      }))
                    }
                  >
                    {notFoundAdditions.length === 1 ? 'Leave it out' : 'Leave them out'}
                  </button>
                </div>
              ) : null}
            </div>
          )}
          <div className="analysis-toolbar">
            <button
              type="button"
              className="form-button"
              onClick={() => void confirmChanges()}
              disabled={saving || checkingAdditions}
            >
              {saving ? 'Saving…' : `Confirm and save version ${nextVersion}`}
            </button>
            <button type="button" className="button-secondary" onClick={discardChanges} disabled={saving}>
              Discard changes
            </button>
          </div>
        </section>
      )}

      <section className="surface-card space-y-3">
        {canEdit && (
          <div className="space-y-2">
            <h3 className="section-title">Edit genes</h3>
            <p className="section-copy">
              Remove a gene with the bin on its row, add genes by name, or edit the whole list. The
              changes are listed first, and saved as version {nextVersion} only when you confirm them.
            </p>
            <form className="analysis-toolbar" onSubmit={stageAdditions}>
              <input
                type="text"
                className="panel-gene-add-input"
                value={addInput}
                onChange={(e) => setAddInput(e.target.value)}
                aria-label="Genes to add"
                placeholder="Genes to add, e.g. BRCA1, TP53"
                disabled={saving}
              />
              <button type="submit" className="button-secondary" disabled={saving || !addInput.trim()}>
                Add to changes
              </button>
              {!listEditing && (
                <button type="button" className="button-ghost" onClick={openListEditor} disabled={saving}>
                  Edit the whole list
                </button>
              )}
            </form>
            {addNote ? <p className="table-subtle">{addNote}</p> : null}
            {listEditing && (
              <div className="field-grid">
                <p className="section-copy">
                  The panel&apos;s genes with the changes listed so far, comma or space separated.
                  Reviewing the list replaces those changes with the ones it makes.
                </p>
                <textarea
                  value={listText}
                  onChange={(e) => setListText(e.target.value)}
                  rows={6}
                  aria-label="Panel genes"
                />
                <div className="analysis-toolbar">
                  <button type="button" className="form-button" onClick={reviewList}>
                    Review changes
                  </button>
                  <button type="button" className="button-secondary" onClick={() => setListEditing(false)}>
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
        <div className="data-table-shell overflow-x-auto">
          <table className="analysis-table table-sticky">
            <thead>
              <tr>
                {REGION_COLUMNS.map(([key, label]) => (
                  <th key={key} className="table-sortable" onClick={() => handleSort(key)}>
                    {`${label} `}
                    {sortKey === key && (sortAsc ? '▲' : '▼')}
                  </th>
                ))}
                {canEdit && (
                  <th>
                    <span className="sr-only">Remove</span>
                  </th>
                )}
              </tr>
              <tr className="table-filter-row">
                {REGION_COLUMNS.map(([key]) => (
                  <th key={key}>
                    <input
                      placeholder={`Filter ${key}`}
                      value={filters[key]}
                      onChange={(e) => handleFilterChange(e, key)}
                    />
                  </th>
                ))}
                {canEdit && <th />}
              </tr>
            </thead>
            <tbody>
              {regions.map((r) => {
                const removed = removalKeys.has(geneKey(r.gene));
                return (
                  // The assembly is part of the key: chrM loci are identical in GRCh38 and T2T.
                  <tr
                    key={`${r.assembly_id ?? ''}:${r.gene}:${r.chr}:${r.start}-${r.end}`}
                    className={removed ? 'panel-gene-row--removed' : undefined}
                  >
                    <td>{r.gene}</td>
                    <td>{r.assembly || '—'}</td>
                    <td>{r.chr}</td>
                    <td>{r.start}</td>
                    <td>{r.end}</td>
                    {canEdit && (
                      <td className="panel-gene-action-cell">
                        {removed ? (
                          <button
                            type="button"
                            className="button-link"
                            aria-label={`Keep ${r.gene}`}
                            onClick={() => keepGene(r.gene)}
                            disabled={saving}
                          >
                            Undo
                          </button>
                        ) : (
                          removeButton(r.gene)
                        )}
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
};

export default GenePanelDetailPage;
