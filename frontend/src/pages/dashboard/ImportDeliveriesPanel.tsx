import React, { useId, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import api from '../../lib/api';
import { apiPath } from '../../lib/apiPath';
import type {
  ImportDeliveryIssue,
  ImportDeliveryOut,
} from '../../lib/apiSchema.generated';
import { isAdmin } from '../../lib/auth';
import { getErrorMessage } from '../../lib/errorMessage';
import { formatTimestamp } from '../../lib/format';
import ModalDialog from '../../components/ModalDialog';
import QueryFailure from '../../components/QueryFailure';
import AddDeliveryForm from './AddDeliveryForm';
import DeliveryCorrectionDialog from './DeliveryCorrectionDialog';

type Delivery = ImportDeliveryOut;
type ConflictMode = 'cancel' | 'update' | 'overwrite';

// The deliveries still waiting for someone, or for their import.
const OPEN_STATES = new Set([
  'incomplete',
  'ready',
  'needs_attention',
  'queued',
]);
const REFUSABLE_STATES = new Set(['incomplete', 'ready', 'needs_attention']);
// The deliveries whose package is corrected before approval: open, and their package read.
const CORRECTABLE_STATES = new Set(['ready', 'needs_attention']);
// The issues an approver resolves by choosing update or overwrite: the family exists.
const RESOLVED_BY_A_CHOICE = new Set(['existing_family', 'existing_samples']);
// The issue an approver resolves only by choosing overwrite: the package's members differ from
// the family's beyond the members it adds, and an overwrite makes them the family's pedigree.
const MEMBERS_DIFFER = 'package_members_differ_from_family';

const STATE_LABELS: Record<string, string> = {
  incomplete: 'Still copying',
  ready: 'Ready',
  needs_attention: 'Needs attention',
  approved: 'Approved',
  queued: 'Import queued',
  imported: 'Imported',
  failed: 'Import failed',
  refused: 'Refused',
  superseded: 'Superseded',
  changed_after_import: 'Changed after import',
  gone: 'Gone',
};

const stateClass = (state: string) =>
  state === 'ready' || state === 'imported'
    ? 'badge-chip badge-chip--success'
    : state === 'needs_attention' || state === 'failed'
      ? 'badge-chip badge-chip--danger'
      : 'badge-chip';

/** A delivery whose only issue is that its family exists: approved with update or overwrite
 * (only overwrite when its package's members differ from the family's, `needsAnOverwrite`). */
export const needsAChoice = (delivery: Delivery) =>
  delivery.state === 'needs_attention' &&
  delivery.issues.some((issue) => issue.code === 'existing_family') &&
  delivery.issues.every(
    (issue) => RESOLVED_BY_A_CHOICE.has(issue.code) || issue.code === MEMBERS_DIFFER
  );

/** A delivery whose package's members differ from its family's beyond the members it adds:
 * an update changes none the family holds, so it is approved only with overwrite. */
export const needsAnOverwrite = (delivery: Delivery) =>
  needsAChoice(delivery) &&
  delivery.issues.some((issue) => issue.code === MEMBERS_DIFFER);

export const isApprovable = (delivery: Delivery) =>
  delivery.state === 'ready' || needsAChoice(delivery);

const IssueList: React.FC<{
  issues: ImportDeliveryIssue[];
  tone: 'issue' | 'warning';
}> = ({ issues, tone }) => (
  <ul
    className={`space-y-1 text-xs ${tone === 'issue' ? 'text-(--color-variant-del)' : 'text-(--color-text-muted)'}`}
  >
    {issues.map((issue, index) => (
      <li key={`${issue.code}-${index}`}>
        {issue.message}
        {issue.path ? <code className="ml-1">{issue.path}</code> : null}
      </li>
    ))}
  </ul>
);

type Decision =
  | { kind: 'approve'; deliveries: Delivery[]; dryRun: boolean }
  | { kind: 'refuse'; delivery: Delivery }
  | { kind: 'correct'; delivery: Delivery };

interface ApproveDialogProps {
  deliveries: Delivery[];
  dryRun: boolean;
  busy: boolean;
  onCancel: () => void;
  onConfirm: (choice: {
    conflictMode: ConflictMode;
    acknowledged: boolean;
  }) => void;
}

/** Asks before deliveries are approved: what each is, its warnings, and for one whose family
 * exists, update or overwrite (an administrator's). Nothing is sent before the confirming
 * button is pressed. */
const ApproveDialog: React.FC<ApproveDialogProps> = ({
  deliveries,
  dryRun,
  busy,
  onCancel,
  onConfirm,
}) => {
  const headingId = useId();
  const existing = deliveries.length === 1 && needsAChoice(deliveries[0]);
  const overwriteOnly = existing && needsAnOverwrite(deliveries[0]);
  const [conflictMode, setConflictMode] = useState<ConflictMode>(
    overwriteOnly ? 'overwrite' : existing ? 'update' : 'cancel'
  );
  const [acknowledged, setAcknowledged] = useState(false);
  const warned = deliveries.filter((delivery) => delivery.warnings.length > 0);
  const overwriteRefused =
    conflictMode === 'overwrite' && !dryRun && !isAdmin();
  const canConfirm =
    !busy && (warned.length === 0 || acknowledged) && !overwriteRefused;
  return (
    <ModalDialog
      onClose={() => {
        if (!busy) onCancel();
      }}
      labelledBy={headingId}
      className="modal-surface surface-card confirm-delete-modal"
      closeOnBackdrop={false}
      confirmDiscard={false}
    >
      <div className="space-y-2">
        <p className="page-kicker">
          {dryRun ? 'Dry run' : 'Approve for import'}
        </p>
        <h2 id={headingId} className="section-title">
          {dryRun
            ? `Dry-run ${deliveries.length === 1 ? deliveries[0].folder_name : `${deliveries.length} deliveries`}?`
            : `Import ${deliveries.length === 1 ? deliveries[0].folder_name : `${deliveries.length} deliveries`}?`}
        </h2>
      </div>
      <ul className="space-y-2 text-sm">
        {deliveries.map((delivery) => (
          <li key={delivery._id}>
            <strong>{delivery.family_id || delivery.folder_name}</strong> into{' '}
            {delivery.project || 'no project'}
            {delivery.run_id ? ` · run ${delivery.run_id}` : ''}
            {delivery.warnings.length > 0 ? (
              <IssueList issues={delivery.warnings} tone="warning" />
            ) : null}
          </li>
        ))}
      </ul>
      {existing ? (
        <label className="field-label">
          The family exists already
          <select
            aria-label="Existing data policy"
            value={conflictMode}
            onChange={(event) =>
              setConflictMode(event.target.value as ConflictMode)
            }
          >
            <option value="update" disabled={overwriteOnly}>
              Update: add the missing datasets and members
            </option>
            <option value="overwrite">
              Overwrite: replace its datasets and pedigree (administrators)
            </option>
          </select>
        </label>
      ) : null}
      {overwriteOnly ? (
        <p className="status-note">
          The package&apos;s members differ from the family in CoGA: an update
          changes none of the family&apos;s members. Correct the package, or
          overwrite, which makes its members the family&apos;s pedigree.
        </p>
      ) : null}
      {overwriteRefused ? (
        <p className="status-note status-note--error">
          Only an administrator overwrites an existing family.
        </p>
      ) : null}
      {warned.length > 0 ? (
        <label className="inline-flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={acknowledged}
            onChange={(event) => setAcknowledged(event.target.checked)}
          />
          I checked the warnings
        </label>
      ) : null}
      <div className="inline-actions modal-actions">
        <button
          type="button"
          className="button-secondary"
          onClick={onCancel}
          disabled={busy}
        >
          Cancel
        </button>
        <button
          type="button"
          className="form-button"
          disabled={!canConfirm}
          onClick={() => onConfirm({ conflictMode, acknowledged })}
        >
          {busy ? 'Starting…' : dryRun ? 'Start dry run' : 'Approve and import'}
        </button>
      </div>
    </ModalDialog>
  );
};

const RefuseDialog: React.FC<{
  delivery: Delivery;
  busy: boolean;
  onCancel: () => void;
  onConfirm: (reason: string) => void;
}> = ({ delivery, busy, onCancel, onConfirm }) => {
  const headingId = useId();
  const [reason, setReason] = useState('');
  return (
    <ModalDialog
      onClose={() => {
        if (!busy) onCancel();
      }}
      labelledBy={headingId}
      className="modal-surface surface-card confirm-delete-modal"
      closeOnBackdrop={false}
      isDirty={() => reason.trim().length > 0}
    >
      <div className="space-y-2">
        <p className="page-kicker">Refuse the delivery</p>
        <h2 id={headingId} className="section-title">
          Refuse {delivery.folder_name}?
        </h2>
      </div>
      <p className="text-sm">
        CoGA leaves the folder as it is until it changes; a new delivery of it
        is checked again. The reason is on the audit trail.
      </p>
      <label className="field-label">
        Reason
        <textarea
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          rows={3}
        />
      </label>
      <div className="inline-actions modal-actions">
        <button
          type="button"
          className="button-secondary"
          onClick={onCancel}
          disabled={busy}
        >
          Cancel
        </button>
        <button
          type="button"
          className="form-button"
          disabled={busy || reason.trim().length === 0}
          onClick={() => onConfirm(reason.trim())}
        >
          {busy ? 'Saving…' : 'Refuse'}
        </button>
      </div>
    </ModalDialog>
  );
};

/** The packages pipelines delivered into the watched import folders, waiting for a decision. */
const ImportDeliveriesPanel: React.FC = () => {
  const queryClient = useQueryClient();
  const [showClosed, setShowClosed] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [decision, setDecision] = useState<Decision | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [statusTone, setStatusTone] = useState<'success' | 'error'>('success');

  const deliveriesQuery = useQuery<Delivery[]>({
    queryKey: ['family-import-deliveries'],
    queryFn: async () => {
      const response = await api.get('/family-imports/deliveries', {
        params: { limit: 200 },
      });
      return (response.data as Delivery[]) || [];
    },
    refetchInterval: 30_000,
  });
  const deliveries = useMemo(
    () => deliveriesQuery.data ?? [],
    [deliveriesQuery.data]
  );
  const open = deliveries.filter((delivery) => OPEN_STATES.has(delivery.state));
  const closedCount = deliveries.length - open.length;
  const shown = showClosed ? deliveries : open;
  const waiting = deliveries.filter(
    (delivery) =>
      delivery.state === 'ready' || delivery.state === 'needs_attention'
  ).length;
  const selectable = shown.filter((delivery) => delivery.state === 'ready');
  const chosen = selectable.filter((delivery) => selected.has(delivery._id));

  const refresh = () => {
    setSelected(new Set());
    queryClient.invalidateQueries({ queryKey: ['family-import-deliveries'] });
    queryClient.invalidateQueries({ queryKey: ['family-import-jobs'] });
    queryClient.invalidateQueries({ queryKey: ['family-import-packages'] });
  };

  const approveMutation = useMutation({
    mutationFn: async (input: {
      deliveries: Delivery[];
      dryRun: boolean;
      conflictMode: ConflictMode;
      acknowledged: boolean;
    }) => {
      const results: { delivery: Delivery; jobId?: string; error?: string }[] =
        [];
      for (const delivery of input.deliveries) {
        try {
          const response = await api.post(
            apiPath`/family-imports/deliveries/${delivery._id}/approve`,
            {
              // The revision of the package shown: one corrected since is refused.
              revision: delivery.revision ?? 0,
              dry_run: input.dryRun,
              conflict_mode: input.conflictMode,
              acknowledge_warnings: input.acknowledged,
            }
          );
          results.push({
            delivery,
            jobId: (response.data as { job: { _id: string } }).job._id,
          });
        } catch (error) {
          results.push({
            delivery,
            error: getErrorMessage(error, 'It could not be approved.'),
          });
        }
      }
      return { results, dryRun: input.dryRun };
    },
    onSuccess: ({ results, dryRun }) => {
      const failed = results.filter((result) => result.error);
      setStatusTone(failed.length ? 'error' : 'success');
      setStatusMessage(
        [
          ...results
            .filter((result) => result.jobId)
            .map(
              (result) =>
                `${dryRun ? 'Dry run' : 'Import'} job ${result.jobId} started for ${result.delivery.folder_name}.`
            ),
          ...failed.map(
            (result) => `${result.delivery.folder_name}: ${result.error}`
          ),
        ].join(' ')
      );
      setDecision(null);
      refresh();
    },
  });

  const refuseMutation = useMutation({
    mutationFn: async (input: { delivery: Delivery; reason: string }) => {
      await api.post(
        apiPath`/family-imports/deliveries/${input.delivery._id}/refuse`,
        { reason: input.reason }
      );
      return input.delivery;
    },
    onSuccess: (delivery) => {
      setStatusTone('success');
      setStatusMessage(`Refused ${delivery.folder_name}.`);
      setDecision(null);
      refresh();
    },
    onError: (error) => {
      setStatusTone('error');
      setStatusMessage(
        getErrorMessage(error, 'The delivery could not be refused.')
      );
      setDecision(null);
    },
  });

  const toggle = (id: string) =>
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <section className="surface-card space-y-4 import-deliveries-panel">
      <div className="catalog-card-header">
        <div>
          <p className="page-kicker">Watched import folders</p>
          <h2 className="catalog-card-title">Deliveries</h2>
        </div>
        <span
          className={
            waiting ? 'badge-chip badge-chip--signature' : 'badge-chip'
          }
        >
          {waiting} waiting
        </span>
      </div>
      <p className="table-subtle">
        Packages pipelines delivered with their package file
        (coga-package.json), checked by CoGA. Nothing is imported until you
        approve it.
      </p>

      <AddDeliveryForm
        onAdded={(delivery) => {
          setStatusTone('success');
          setStatusMessage(
            `Added ${delivery.folder_name}: ${(STATE_LABELS[delivery.state] ?? delivery.state).toLowerCase()}. Check and correct its package with Correct…, then approve it.`
          );
          refresh();
        }}
      />

      {deliveriesQuery.isError ? (
        <QueryFailure what="the deliveries" error={deliveriesQuery.error} />
      ) : null}

      <div className="action-row">
        {closedCount > 0 ? (
          <label className="inline-flex items-center gap-2 text-sm text-(--color-text-muted)">
            <input
              type="checkbox"
              checked={showClosed}
              onChange={(event) => setShowClosed(event.target.checked)}
            />
            Show decided and superseded deliveries ({closedCount})
          </label>
        ) : null}
        <button
          type="button"
          className="form-button"
          disabled={chosen.length === 0 || approveMutation.isPending}
          onClick={() =>
            setDecision({ kind: 'approve', deliveries: chosen, dryRun: false })
          }
        >
          Approve selected ({chosen.length})
        </button>
      </div>

      {statusMessage ? (
        <p
          className={`status-note ${statusTone === 'success' ? 'status-note--success' : 'status-note--error'}`}
        >
          {statusMessage}
        </p>
      ) : null}

      {shown.length > 0 ? (
        <div className="data-table-shell overflow-x-auto">
          <table className="analysis-table">
            <thead>
              <tr>
                <th>
                  <span className="sr-only">Select</span>
                </th>
                <th>Folder</th>
                <th>Project</th>
                <th>Pipeline</th>
                <th>State</th>
                <th>Seen</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((delivery) => (
                <tr key={delivery._id}>
                  <td>
                    {delivery.state === 'ready' ? (
                      <input
                        type="checkbox"
                        aria-label={`Select ${delivery.folder_name}`}
                        checked={selected.has(delivery._id)}
                        onChange={() => toggle(delivery._id)}
                      />
                    ) : null}
                  </td>
                  <td>
                    <div>{delivery.folder_name}</div>
                    <div className="table-subtle">
                      {delivery.family_id
                        ? `Family ${delivery.family_id}`
                        : 'No family named'}
                      {delivery.analysis_type
                        ? ` · ${delivery.analysis_type}`
                        : ''}
                      {delivery.request_id
                        ? ` · request ${delivery.request_id}`
                        : ''}
                    </div>
                  </td>
                  <td>{delivery.project || '—'}</td>
                  <td>
                    <div>
                      {delivery.origin === 'manual'
                        ? 'Added by hand'
                        : `${delivery.pipeline_name || '—'}${delivery.pipeline_version ? ` ${delivery.pipeline_version}` : ''}`}
                    </div>
                    <div className="table-subtle">
                      {delivery.run_id
                        ? `run ${delivery.run_id}`
                        : 'no run named'}
                    </div>
                  </td>
                  <td>
                    <span className={stateClass(delivery.state)}>
                      {STATE_LABELS[delivery.state] ?? delivery.state}
                    </span>
                    {delivery.job_id ? (
                      <div className="table-subtle">
                        {delivery.job_dry_run ? 'Dry run' : 'Import'}{' '}
                        {delivery.job_status ?? ''}
                      </div>
                    ) : null}
                    {delivery.issues.length > 0 ? (
                      <IssueList issues={delivery.issues} tone="issue" />
                    ) : null}
                    {delivery.warnings.length > 0 ? (
                      <details className="text-xs">
                        <summary>{delivery.warnings.length} warning(s)</summary>
                        <IssueList issues={delivery.warnings} tone="warning" />
                      </details>
                    ) : null}
                  </td>
                  <td>
                    <div>{formatTimestamp(delivery.first_seen_at)}</div>
                    {delivery.revision ? (
                      <div className="table-subtle">
                        Corrected (revision {delivery.revision})
                      </div>
                    ) : null}
                    <div className="table-subtle font-mono">
                      {delivery.package_fingerprint.slice(0, 12)}
                    </div>
                  </td>
                  <td>
                    <div className="inline-actions">
                      {isApprovable(delivery) ? (
                        <>
                          <button
                            type="button"
                            className="button-secondary"
                            onClick={() =>
                              setDecision({
                                kind: 'approve',
                                deliveries: [delivery],
                                dryRun: true,
                              })
                            }
                          >
                            Dry run
                          </button>
                          <button
                            type="button"
                            className="form-button"
                            onClick={() =>
                              setDecision({
                                kind: 'approve',
                                deliveries: [delivery],
                                dryRun: false,
                              })
                            }
                          >
                            Approve…
                          </button>
                        </>
                      ) : null}
                      {CORRECTABLE_STATES.has(delivery.state) ? (
                        <button
                          type="button"
                          className="button-secondary"
                          onClick={() =>
                            setDecision({ kind: 'correct', delivery })
                          }
                        >
                          Correct…
                        </button>
                      ) : null}
                      {REFUSABLE_STATES.has(delivery.state) ? (
                        <button
                          type="button"
                          className="button-secondary"
                          onClick={() =>
                            setDecision({ kind: 'refuse', delivery })
                          }
                        >
                          Refuse…
                        </button>
                      ) : null}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : deliveriesQuery.isLoading ? (
        <p className="table-subtle">Loading the deliveries…</p>
      ) : (
        <p className="table-subtle">
          No delivery is waiting. A pipeline delivers a package by writing it
          into a folder FAMILY_IMPORT_WATCH_ROOTS names, with its package file
          coga-package.json, which says when the package is complete.
        </p>
      )}

      {decision?.kind === 'approve' ? (
        <ApproveDialog
          deliveries={decision.deliveries}
          dryRun={decision.dryRun}
          busy={approveMutation.isPending}
          onCancel={() => setDecision(null)}
          onConfirm={({ conflictMode, acknowledged }) =>
            approveMutation.mutate({
              deliveries: decision.deliveries,
              dryRun: decision.dryRun,
              conflictMode,
              acknowledged,
            })
          }
        />
      ) : null}
      {decision?.kind === 'correct' ? (
        <DeliveryCorrectionDialog
          delivery={decision.delivery}
          onCancel={() => setDecision(null)}
          onSaved={(result) => {
            setStatusTone('success');
            setStatusMessage(
              `Corrected ${result.delivery.folder_name} (revision ${result.package.revision}): ${(STATE_LABELS[result.delivery.state] ?? result.delivery.state).toLowerCase()}.`
            );
            setDecision(null);
            refresh();
          }}
        />
      ) : null}
      {decision?.kind === 'refuse' ? (
        <RefuseDialog
          delivery={decision.delivery}
          busy={refuseMutation.isPending}
          onCancel={() => setDecision(null)}
          onConfirm={(reason) =>
            refuseMutation.mutate({ delivery: decision.delivery, reason })
          }
        />
      ) : null}
    </section>
  );
};

export default ImportDeliveriesPanel;
