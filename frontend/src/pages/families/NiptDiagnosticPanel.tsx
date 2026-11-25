import React, { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import { apiPath } from '../../lib/apiPath';
import { getErrorMessage } from '../../lib/errorMessage';
import type { NiptDiagnosticPanelStateOut } from '../../lib/apiSchema.generated';
import type { GenePanel } from './smallVariantSearch';

/** The query key of a monogenic NIPT's diagnostic panel (one per pregnancy's analysis). */
export const niptDiagnosticPanelKey = (familyId: string | undefined, analysis: string | null) =>
  ['family', familyId, 'nipt', 'diagnostic-panel', analysis] as const;

/** "NAME (version N, G genes)", as the page and the report name a diagnostic panel. */
export const describeDiagnosticPanel = (panel: NonNullable<NiptDiagnosticPanelStateOut['panel']>): string =>
  `${panel.name} (version ${panel.version}, ${panel.gene_count} ${panel.gene_count === 1 ? 'gene' : 'genes'})`;

interface NiptDiagnosticPanelProps {
  familyId: string;
  /** The pregnancy's analysis key, sent when the family holds several (`analysis`). */
  analysisParams: Record<string, string>;
  analysisKey: string | null;
  state: NiptDiagnosticPanelStateOut | undefined;
  loadFailed: boolean;
  panels: GenePanel[];
  readOnly: boolean;
  /** Show the variants of the diagnostic panel in the list below. */
  onShowPanel: (panelId: string) => void;
  /** The panel the list below applies now ('' for none). */
  listPanelId: string;
}

/**
 * The monogenic NIPT's diagnostic gene panel (REQ-NIPT-019): the panel the analysis is read and
 * reported within. The analyst sets it per pregnancy's analysis and may change it; each change is
 * a clinical audit event, and the sign-out freezes the panel's version, gene list and target
 * coverage. Without one the sign-out asks why.
 */
const NiptDiagnosticPanel: React.FC<NiptDiagnosticPanelProps> = ({
  familyId,
  analysisParams,
  analysisKey,
  state,
  loadFailed,
  panels,
  readOnly,
  onShowPanel,
  listPanelId,
}) => {
  const queryClient = useQueryClient();
  const current = state?.panel ?? null;
  const [choice, setChoice] = useState<string>('');
  const save = useMutation({
    mutationFn: async (panelId: string | null) => {
      const res = await api.put(
        apiPath`/families/${familyId}/nipt/diagnostic-panel`,
        { panel_id: panelId },
        { params: analysisParams },
      );
      return res.data as NiptDiagnosticPanelStateOut;
    },
    onSuccess: (data) => {
      queryClient.setQueryData(niptDiagnosticPanelKey(familyId, analysisKey), data);
      setChoice('');
      // The record the sign-out would freeze changed: its check and the report read it again.
      void queryClient.invalidateQueries({ queryKey: ['family', familyId] });
    },
  });
  const selectable = panels.filter((panel) => panel._id !== current?.panel_id);

  return (
    <section className="surface-card space-y-2" aria-label="Diagnostic gene panel">
      <h2 className="section-title">Diagnostic gene panel</h2>
      {loadFailed ? (
        <p className="status-note status-note--warning" role="alert">
          The diagnostic panel could not be loaded: what the analysis is read within is unknown.
        </p>
      ) : current ? (
        <p>
          The analysis is read and reported within <strong>{describeDiagnosticPanel(current)}</strong>. The sign-out
          freezes this version, its gene list and the coverage of its capture targets. The NIPT report lists its
          diagnostic candidates by the locked criteria of the R validation, which the sign-out freezes too; the
          filters below are for exploring.{' '}
          {listPanelId !== current.panel_id ? (
            <button type="button" className="button-link" onClick={() => onShowPanel(current.panel_id)}>
              Show its variants below
            </button>
          ) : null}
        </p>
      ) : (
        <p className="status-note status-note--warning" role="note">
          No diagnostic panel is set: the analysis states no scope, and the sign-out asks for a reason. Choose the
          indication&rsquo;s panel.
        </p>
      )}
      {readOnly ? null : (
        <div className="flex flex-wrap items-center gap-2">
          <label className="field-label" htmlFor="nipt-diagnostic-panel">
            {current ? 'Change to' : 'Set'}
          </label>
          <select
            id="nipt-diagnostic-panel"
            aria-label="Diagnostic gene panel"
            value={choice}
            onChange={(event) => setChoice(event.target.value)}
          >
            <option value="">Choose a gene panel…</option>
            {selectable.map((panel) => (
              <option key={panel._id} value={panel._id}>
                {panel.name}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="button-secondary"
            disabled={!choice || save.isPending}
            onClick={() => save.mutate(choice)}
          >
            Set as diagnostic panel
          </button>
          {current ? (
            <button type="button" className="button-secondary" disabled={save.isPending} onClick={() => save.mutate(null)}>
              Clear
            </button>
          ) : null}
        </div>
      )}
      {save.isError ? (
        <p className="status-note status-note--warning" role="alert">
          The diagnostic panel was not changed: {getErrorMessage(save.error)}
        </p>
      ) : null}
    </section>
  );
};

export default NiptDiagnosticPanel;
