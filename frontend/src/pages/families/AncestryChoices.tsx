import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import { apiPath } from '../../lib/apiPath';
import { getErrorMessage } from '../../lib/errorMessage';
import type { GenePanel } from '../../lib/apiTypes';
import type {
  AncestrySampleChoiceOut,
  AncestrySampleOut,
  FamilyAncestryOut,
} from '../../lib/apiSchema.generated';

type Panel = Pick<GenePanel, '_id' | 'name'>;

const CALLSET_LABELS: Record<string, string> = {
  clair3: 'Direct calls',
  nipt: 'NIPT per-sample calls',
  glimpse2: 'Imputed genotypes',
};

const describe = (choice?: AncestrySampleChoiceOut) => {
  if (!choice || choice.mode === 'auto') return 'Automatic';
  if (choice.mode === 'exclude') return 'Left out';
  const label = CALLSET_LABELS[choice.callset ?? ''] ?? choice.callset;
  return choice.regions
    ? `${label}, in ${choice.regions.name} (${choice.regions.covered_markers.toLocaleString()} markers)`
    : `${label}`;
};

const ChoiceRow: React.FC<{
  familyId: string;
  projectId?: string;
  sample: AncestrySampleOut;
  choice?: AncestrySampleChoiceOut;
  panels: Panel[];
  queryKey: unknown[];
}> = ({ familyId, projectId, sample, choice, panels, queryKey }) => {
  const client = useQueryClient();
  const callsets = (sample.candidates ?? []).map((scan) => scan.callset);
  const [mode, setMode] = useState(
    choice?.mode === 'callset' ? `callset:${choice.callset}` : (choice?.mode ?? 'auto')
  );
  const [regions, setRegions] = useState(
    choice?.regions?.source === 'panel' && choice.regions.panel_id
      ? `panel:${choice.regions.panel_id}`
      : choice?.regions?.source === 'bed'
        ? 'bed-kept'
        : ''
  );
  const [bed, setBed] = useState<File | null>(null);
  const params = projectId ? { project_id: projectId } : {};
  const save = useMutation({
    mutationFn: async () => {
      const [kind, callset] = mode.split(':');
      if (kind === 'callset' && regions === 'bed') {
        const form = new FormData();
        form.append('callset', callset);
        if (bed) form.append('file', bed);
        return api.put(apiPath`/families/${familyId}/ancestry/choices/${sample.sample_id}/bed`, form, { params });
      }
      return api.put(
        apiPath`/families/${familyId}/ancestry/choices/${sample.sample_id}`,
        {
          mode: kind,
          callset: kind === 'callset' ? callset : null,
          panel_id: kind === 'callset' && regions.startsWith('panel:') ? regions.slice(6) : null,
          keep_regions: kind === 'callset' && regions === 'bed-kept',
        },
        { params }
      );
    },
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey });
    },
  });
  const isCallset = mode.startsWith('callset:');
  return (
    <tr>
      <td>{sample.sample_id}</td>
      <td>{describe(choice)}</td>
      <td>
        <select
          aria-label={`How ${sample.sample_id} is read`}
          value={mode}
          onChange={(e) => setMode(e.target.value)}
        >
          <option value="auto">Automatic</option>
          {callsets.map((callset) => (
            <option key={callset} value={`callset:${callset}`}>
              {CALLSET_LABELS[callset] ?? callset}
            </option>
          ))}
          <option value="exclude">Leave out</option>
        </select>
      </td>
      <td>
        <select
          aria-label={`Regions ${sample.sample_id}'s callset covers`}
          value={regions}
          disabled={!isCallset}
          onChange={(e) => setRegions(e.target.value)}
        >
          <option value="">As measured</option>
          {choice?.regions?.source === 'bed' ? (
            <option value="bed-kept">{choice.regions.name}</option>
          ) : null}
          {panels.map((panel) => (
            <option key={panel._id} value={`panel:${panel._id}`}>
              Gene panel: {panel.name}
            </option>
          ))}
          <option value="bed">A BED file…</option>
        </select>
        {isCallset && regions === 'bed' ? (
          <input
            type="file"
            accept=".bed,.txt,text/plain"
            aria-label={`BED file for ${sample.sample_id}`}
            onChange={(e) => setBed(e.target.files?.[0] ?? null)}
          />
        ) : null}
      </td>
      <td>
        <button
          type="button"
          className="button-secondary"
          disabled={save.isPending || (isCallset && regions === 'bed' && !bed)}
          onClick={() => save.mutate()}
        >
          {save.isPending ? 'Saving…' : 'Save'}
        </button>
        {save.error ? (
          <span role="alert" className="table-subtle ancestry-reason">
            {getErrorMessage(save.error, 'The choice could not be saved.')}
          </span>
        ) : null}
      </td>
    </tr>
  );
};

/**
 * How each individual is read, when a user wants other than CoGA's own reading: one of the
 * callsets that hold it (an embryo included), with the regions it covers (a gene panel or a
 * BED file), or left out. Saving queues a new calculation.
 */
const AncestryChoices: React.FC<{
  familyId: string;
  projectId?: string;
  data: FamilyAncestryOut;
  queryKey: unknown[];
}> = ({ familyId, projectId, data, queryKey }) => {
  const panels = useQuery({
    queryKey: ['panels'],
    queryFn: async () => (await api.get<GenePanel[]>(apiPath`/panels`)).data,
  });
  // An individual with a callset to choose: a cfDNA mixture has none.
  const samples = (data.analysis?.samples ?? []).filter(
    (sample) => (sample.candidates?.length ?? 0) > 0
  );
  if (!samples.length) return null;
  const byId = new Map((data.choices ?? []).map((choice) => [choice.sample_id, choice]));
  return (
    <details>
      <summary>Choose how each individual is read</summary>
      <p className="table-subtle">
        By default CoGA reads each individual from its best callset, as measured. Choose a
        callset to read instead (this includes an embryo), give the regions a panel or capture
        kit covers, or leave the individual out. Saving calculates the projection again.
      </p>
      <div className="overflow-auto">
        <table className="data-table ancestry-quality-table">
          <caption>Choices per individual</caption>
          <thead>
            <tr>
              <th>Individual</th>
              <th>Now</th>
              <th>Read</th>
              <th>Regions</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {samples.map((sample) => (
              <ChoiceRow
                key={`${sample.sample_id}:${byId.get(sample.sample_id)?.chosen_at ?? ''}`}
                familyId={familyId}
                projectId={projectId}
                sample={sample}
                choice={byId.get(sample.sample_id)}
                panels={panels.data ?? []}
                queryKey={queryKey}
              />
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
};

export default AncestryChoices;
