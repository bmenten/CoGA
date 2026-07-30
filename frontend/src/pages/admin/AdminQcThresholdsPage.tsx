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
  // A cut-off decides whether a run is reported as inadequate, so it is not saved on a
  // single stray click — the change is restated and confirmed first.
  const [pendingMetric, setPendingMetric] = useState<string | null>(null);
  const [pendingReason, setPendingReason] = useState('');
  const [newProfileLabel, setNewProfileLabel] = useState('');
  const [addingProfile, setAddingProfile] = useState(false);

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
        reason: pendingReason.trim(),
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
      setPendingReason('');
      setStatus({ tone: 'success', message: `Saved thresholds for ${metricKey}.` });
    },
    onError: (err) =>
      setStatus({ tone: 'error', message: getErrorMessage(err, 'Could not save the threshold.') }),
  });

  const createProfileMutation = useMutation({
    mutationFn: async (label: string) =>
      (await api.post('/admin/qc-thresholds/profiles', { label })).data as { key: string },
    onSuccess: async (profile) => {
      await queryClient.invalidateQueries({ queryKey: QUERY_KEY });
      setSelectedProfile(profile.key);
      setNewProfileLabel('');
      setAddingProfile(false);
      setStatus({
        tone: 'success',
        message: 'Profile added. It gates nothing until cut-offs are entered.',
      });
    },
    onError: (err) =>
      setStatus({ tone: 'error', message: getErrorMessage(err, 'Could not add the profile.') }),
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
          A warning bound shows the sample amber in the family workspace, an error bound red, and a
          metric with no bound is left unchecked rather than reported as passing. Cut-offs are a
          clinical decision: none are shipped by default, and the numbers entered here define what
          the interface reports as an inadequate run.
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

      <section className="surface-card qc-threshold-layout">
        <aside className="qc-profile-list" aria-label="QC threshold profiles">
          <h2 className="qc-section-label">Profiles</h2>
          <ul>
            {profiles.map((profile) => {
              const configured = profile.thresholds.length;
              return (
                <li key={profile.key}>
                  <button
                    type="button"
                    className={`qc-profile-item${
                      profile.key === activeProfileKey ? ' qc-profile-item--active' : ''
                    }`}
                    aria-current={profile.key === activeProfileKey}
                    onClick={() => setSelectedProfile(profile.key)}
                  >
                    <span className="qc-profile-item__label">
                      {profile.label}
                      {profile.is_default ? <span className="qc-profile-item__tag">default</span> : null}
                    </span>
                    {/* How many metrics this profile actually gates — the difference
                        between a configured profile and an empty one, which is
                        otherwise invisible until you open it. */}
                    <span className="qc-profile-item__count">
                      {configured ? `${configured} set` : 'none set'}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
          {addingProfile ? (
            <form
              className="qc-profile-add"
              onSubmit={(event) => {
                event.preventDefault();
                if (newProfileLabel.trim()) createProfileMutation.mutate(newProfileLabel.trim());
              }}
            >
              <input
                type="text"
                aria-label="New profile name"
                placeholder="Assay name"
                value={newProfileLabel}
                onChange={(event) => setNewProfileLabel(event.target.value)}
              />
              <button
                type="submit"
                className="form-button"
                disabled={!newProfileLabel.trim() || createProfileMutation.isPending}
              >
                Add
              </button>
              <button
                type="button"
                className="button-ghost"
                onClick={() => {
                  setAddingProfile(false);
                  setNewProfileLabel('');
                }}
              >
                Cancel
              </button>
            </form>
          ) : (
            <button type="button" className="button-ghost" onClick={() => setAddingProfile(true)}>
              + Add profile
            </button>
          )}
          <p className="dashboard-link-note">
            A family uses the profile named in its <code>qc_profile</code> metadata, and the default
            otherwise. A new profile starts with no cut-offs, so it gates nothing until values are
            entered.
          </p>
        </aside>

        <div className="qc-threshold-metrics">
          <div className="family-workspace-card-head">
            <div className="space-y-1">
              <h2 className="qc-section-label">{activeProfile?.label ?? 'Metrics'}</h2>
              <p className="family-workspace-card-subtitle">
                {activeProfile?.description ?? 'Leave both bounds blank to leave a metric unchecked.'}
              </p>
            </div>
          </div>
        <div className="data-table-shell overflow-x-auto">
          <table className="analysis-table qc-threshold-table">
            <thead>
              <tr>
                <th>Metric</th>
                {/* The failing side is a property of the metric, not of the bound, so it
                    heads the column rather than repeating on all 16 rows. */}
                <th>Warning</th>
                <th>Error</th>
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
                const worseWhenLower = metric.direction === 'lower_is_worse';
                return (
                  <tr key={metric.key}>
                    <td>
                      <InfoTip
                        label={`${metric.help_text} Fails when the value is ${
                          worseWhenLower ? 'below' : 'above'
                        } the bound.`}
                        className="table-link"
                      >
                        {metric.label}
                      </InfoTip>{' '}
                      {/* Unit and failing side, inline: three former columns in the space
                          of one, and both belong to the metric rather than to a bound. */}
                      <span className="table-subtle">
                        {metric.unit ? `${metric.unit} ` : ''}
                        {worseWhenLower ? '↓' : '↑'}
                      </span>
                    </td>
                    <td>
                      <input
                        type="number"
                        step="any"
                        className="qc-threshold-input"
                        aria-label={`${metric.label} warning threshold`}
                        // The stored value as placeholder: a blank input reads as "no
                        // bound", and the former Stored column existed only to say
                        // otherwise.
                        placeholder={stored?.warn ? `${stored.warn}` : 'none'}
                        value={draft.warn}
                        onChange={(event) => setDraft(metric.key, { warn: event.target.value })}
                      />
                    </td>
                    <td>
                      <input
                        type="number"
                        step="any"
                        className="qc-threshold-input"
                        aria-label={`${metric.label} error threshold`}
                        placeholder={stored?.error ? `${stored.error}` : 'none'}
                        value={draft.error}
                        onChange={(event) => setDraft(metric.key, { error: event.target.value })}
                      />
                    </td>
                    <td>
                      {/* Only the row being edited offers a Save; 16 permanently disabled
                          buttons were 16 rows of noise. */}
                      {dirty ? (
                        <button
                          type="button"
                          className="button-ghost"
                          disabled={saveMutation.isPending}
                          onClick={() => setPendingMetric(metric.key)}
                        >
                          Save
                        </button>
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        </div>
      </section>

      {pendingMetric && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setPendingMetric(null)}>
          <section
            className="modal-surface surface-card"
            role="dialog"
            aria-modal="true"
            aria-label="Confirm threshold change"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <h2 className="section-title">Confirm QC cut-off change</h2>
            <p className="section-copy">
              {(() => {
                const metric = (data?.metrics ?? []).find((entry) => entry.key === pendingMetric);
                const draft = draftFor(pendingMetric);
                const stored = storedByMetric[pendingMetric];
                const shown = (value: string) => (value.trim() ? value : 'none');
                return (
                  <>
                    <strong>{metric?.label ?? pendingMetric}</strong> in the{' '}
                    <strong>{activeProfile?.label}</strong> profile:{' '}
                    warning {shown(stored?.warn ?? '')} → {shown(draft.warn)}, error{' '}
                    {shown(stored?.error ?? '')} → {shown(draft.error)}.
                  </>
                );
              })()}
            </p>
            <p className="report-disclaimer">
              This changes what the interface flags as an inadequate run for every sample using
              this profile. It is advisory — nothing is withheld from a report — but the change is
              recorded permanently with your account.
            </p>
            <label className="form-field">
              <span>Reason for the change</span>
              {/* Required. The values either side are recorded automatically; what they
                  cannot say is whether a limit moved because a validation study supported
                  it or because a run was inconvenient. */}
              <textarea
                rows={2}
                value={pendingReason}
                onChange={(event) => setPendingReason(event.target.value)}
                placeholder="e.g. per VAL-P07 opvolgvalidatie, 2026-07"
              />
            </label>
            <div className="compact-toolbar family-toolbar">
              <button
                type="button"
                className="form-button"
                disabled={saveMutation.isPending || !pendingReason.trim()}
                onClick={() => {
                  const metricKey = pendingMetric;
                  setPendingMetric(null);
                  saveMutation.mutate(metricKey);
                }}
              >
                Confirm change
              </button>
              <button
                type="button"
                className="button-ghost"
                onClick={() => {
                  setPendingMetric(null);
                  setPendingReason('');
                }}
              >
                Cancel
              </button>
            </div>
          </section>
        </div>
      )}

      <section className="surface-card space-y-3">
        <div className="family-workspace-card-head">
          <div className="space-y-1">
            <h2 className="qc-section-label">Change history</h2>
            <p className="family-workspace-card-subtitle">
              Every cut-off edit, with the value it replaced. Append-only — entries cannot be
              altered or removed.
            </p>
          </div>
        </div>
        {(data?.changes ?? []).length === 0 ? (
          <p className="dashboard-link-note">No cut-off has been changed yet.</p>
        ) : (
          <div className="data-table-shell overflow-x-auto">
            <table className="analysis-table">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Who</th>
                  <th>Profile</th>
                  <th>Metric</th>
                  <th>Warning</th>
                  <th>Error</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {(data?.changes ?? []).map((change, index) => (
                  <tr key={`${change.changed_at}-${change.metric_key}-${index}`}>
                    <td className="table-subtle">
                      {change.changed_at.replace('T', ' ').slice(0, 16)} UTC
                    </td>
                    <td className="table-subtle">{change.changed_by_email ?? 'unknown'}</td>
                    <td className="table-subtle">{change.profile_key}</td>
                    <td>{change.metric_key}</td>
                    <td className="table-subtle">
                      {boundText(change.previous_warn_value) || '—'} → {boundText(change.warn_value) || '—'}
                    </td>
                    <td className="table-subtle">
                      {boundText(change.previous_error_value) || '—'} → {boundText(change.error_value) || '—'}
                    </td>
                    {/* Null only for edits made before a reason was required. */}
                    <td>{change.reason || <span className="table-subtle">not recorded</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
};

export default AdminQcThresholdsPage;
