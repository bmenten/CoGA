import React, { useMemo, useState } from 'react';
import { Link } from 'react-router';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import PageState from '../../components/PageState';
import InfoTip from '../../components/InfoTip';
import { getErrorMessage } from '../../lib/errorMessage';
import type { ApiQcThresholdCatalogue } from '../../lib/apiTypes';

type StatusTone = 'success' | 'error';

const QUERY_KEY = ['admin', 'qc-thresholds'];

/** A blank input clears the bound; anything unparseable is rejected before sending. */
const parseBound = (raw: string): number | null | undefined => {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : undefined;
};

const boundText = (value: number | null | undefined): string =>
  value === null || value === undefined ? '' : String(value);

/**
 * Sequencing-QC thresholds: a warning and an error cut-off per metric, grouped into
 * named profiles.
 *
 * Profiles exist because one cut-off cannot serve two assays — ~18x mean depth is normal
 * for PacBio HiFi WGS and a hard failure on a 100x panel. A family uses the profile
 * named in its `qc_profile` metadata, and the default profile otherwise.
 *
 * Which metrics exist and whether a low or a high value is the failing side come from
 * the backend catalogue, not from this form: they follow what the QC parsers emit, and
 * making direction editable would let an operator invert a comparison and turn a failing
 * run green.
 */
const AdminQcThresholdsPage: React.FC = () => {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<{ tone: StatusTone; message: string } | null>(null);
  const [selectedProfile, setSelectedProfile] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, { warn: string; error: string }>>({});

  const { data, isLoading, error } = useQuery<ApiQcThresholdCatalogue>({
    queryKey: QUERY_KEY,
    queryFn: async () => (await api.get('/admin/qc-thresholds')).data as ApiQcThresholdCatalogue,
    retry: false,
  });

  const profiles = data?.profiles ?? [];
  const activeProfileKey =
    selectedProfile ?? profiles.find((profile) => profile.is_default)?.key ?? profiles[0]?.key ?? null;
  const activeProfile = profiles.find((profile) => profile.key === activeProfileKey);

  // Stored cut-offs for the active profile, addressable by metric.
  const storedByMetric = useMemo(() => {
    const map: Record<string, { warn: string; error: string }> = {};
    for (const threshold of activeProfile?.thresholds ?? []) {
      map[threshold.metric_key] = {
        warn: boundText(threshold.warn_value),
        error: boundText(threshold.error_value),
      };
    }
    return map;
  }, [activeProfile]);

  const draftFor = (metricKey: string) =>
    drafts[`${activeProfileKey}:${metricKey}`] ?? storedByMetric[metricKey] ?? { warn: '', error: '' };

  const setDraft = (metricKey: string, patch: Partial<{ warn: string; error: string }>) =>
    setDrafts((current) => {
      const key = `${activeProfileKey}:${metricKey}`;
      return { ...current, [key]: { ...draftFor(metricKey), ...patch } };
    });

  const saveMutation = useMutation({
    mutationFn: async (metricKey: string) => {
      const draft = draftFor(metricKey);
      const warn = parseBound(draft.warn);
      const errorBound = parseBound(draft.error);
      if (warn === undefined || errorBound === undefined) {
        throw new Error('Thresholds must be numbers, or blank to clear.');
      }
      await api.put(`/admin/qc-thresholds/${encodeURIComponent(activeProfileKey ?? '')}`, {
        metric_key: metricKey,
        warn_value: warn,
        error_value: errorBound,
      });
      return metricKey;
    },
    onSuccess: async (metricKey) => {
      await queryClient.invalidateQueries({ queryKey: QUERY_KEY });
      // Families read their thresholds through the family record, so drop those too.
      await queryClient.invalidateQueries({ queryKey: ['family'] });
      setDrafts((current) => {
        const next = { ...current };
        delete next[`${activeProfileKey}:${metricKey}`];
        return next;
      });
      setStatus({ tone: 'success', message: `Saved thresholds for ${metricKey}.` });
    },
    onError: (err) =>
      setStatus({ tone: 'error', message: getErrorMessage(err, 'Could not save the threshold.') }),
  });

  if (isLoading) {
    return (
      <PageState
        kicker="Admin"
        title="Loading sequencing QC thresholds"
        message="Fetching the metric catalogue and threshold profiles."
      />
    );
  }

  if (error) {
    return (
      <PageState
        kicker="Admin"
        title="Could not load sequencing QC thresholds"
        message={getErrorMessage(error, 'The QC threshold configuration could not be loaded.')}
      />
    );
  }

  return (
    <div className="page-shell space-y-6">
      <section className="surface-card page-top-card">
        <p className="page-kicker">
          <Link to="/admin" className="table-link">
            Admin
          </Link>{' '}
          / Sequencing QC thresholds
        </p>
        <h1 className="catalog-card-title">Sequencing QC thresholds</h1>
        <p className="section-copy">
          Set the warning and error cut-offs for each sequencing-QC metric. A sample whose
          measurement crosses the warning bound is shown amber in the family workspace; crossing
          the error bound shows red. A metric with no cut-off is left unchecked rather than
          reported as passing.
        </p>
        <p className="report-disclaimer">
          Cut-offs are a clinical decision. No values are shipped by default — the numbers entered
          here define what the interface reports as an inadequate run, and should be agreed with the
          laboratory before use.
        </p>
        {status && (
          <div
            className={`status-note ${
              status.tone === 'success' ? 'status-note--success' : 'status-note--error'
            }`}
          >
            {status.message}
          </div>
        )}
      </section>

      <section className="surface-card space-y-3">
        <div className="family-workspace-card-head">
          <div className="space-y-1">
            <h2 className="section-title">Profile</h2>
            <p className="family-workspace-card-subtitle">
              One cut-off cannot serve two assays, so thresholds are grouped per assay. A family uses
              the profile named in its <code>qc_profile</code> metadata, and the default profile
              otherwise.
            </p>
          </div>
        </div>
        <div className="compact-toolbar family-toolbar">
          {profiles.map((profile) => (
            <button
              key={profile.key}
              type="button"
              className={profile.key === activeProfileKey ? 'form-button' : 'button-secondary'}
              onClick={() => setSelectedProfile(profile.key)}
            >
              {profile.label}
              {profile.is_default ? ' (default)' : ''}
            </button>
          ))}
        </div>
        {activeProfile?.description && (
          <p className="dashboard-link-note">{activeProfile.description}</p>
        )}
      </section>

      <section className="surface-card space-y-3">
        <div className="family-workspace-card-head">
          <div className="space-y-1">
            <h2 className="section-title">Metrics</h2>
            <p className="family-workspace-card-subtitle">
              Leave both bounds blank to leave a metric unchecked.
            </p>
          </div>
        </div>
        <div className="data-table-shell overflow-x-auto">
          <table className="analysis-table">
            <thead>
              <tr>
                <th>Metric</th>
                <th>Unit</th>
                <th>Fails when</th>
                <th>Warning</th>
                <th>Error</th>
                <th>Stored</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {(data?.metrics ?? []).map((metric) => {
                const draft = draftFor(metric.key);
                const stored = storedByMetric[metric.key];
                const dirty =
                  drafts[`${activeProfileKey}:${metric.key}`] !== undefined &&
                  (draft.warn !== (stored?.warn ?? '') || draft.error !== (stored?.error ?? ''));
                return (
                  <tr key={metric.key}>
                    <td>
                      <InfoTip label={metric.help_text} className="table-link">
                        {metric.label}
                      </InfoTip>
                    </td>
                    <td className="table-subtle">{metric.unit}</td>
                    <td className="table-subtle">
                      {metric.direction === 'lower_is_worse' ? 'value below bound' : 'value above bound'}
                    </td>
                    <td>
                      <input
                        type="number"
                        step="any"
                        aria-label={`${metric.label} warning threshold`}
                        value={draft.warn}
                        onChange={(event) => setDraft(metric.key, { warn: event.target.value })}
                      />
                    </td>
                    <td>
                      <input
                        type="number"
                        step="any"
                        aria-label={`${metric.label} error threshold`}
                        value={draft.error}
                        onChange={(event) => setDraft(metric.key, { error: event.target.value })}
                      />
                    </td>
                    <td className="table-subtle">
                      {stored ? `${stored.warn || '—'} / ${stored.error || '—'}` : 'not set'}
                    </td>
                    <td>
                      <button
                        type="button"
                        className="button-ghost"
                        disabled={!dirty || saveMutation.isPending}
                        onClick={() => saveMutation.mutate(metric.key)}
                      >
                        Save
                      </button>
                    </td>
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

export default AdminQcThresholdsPage;
