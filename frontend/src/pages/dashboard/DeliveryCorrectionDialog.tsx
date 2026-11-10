import React, { useId, useMemo, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';

import api from '../../lib/api';
import { apiPath } from '../../lib/apiPath';
import type {
  ImportDeliveryChange,
  ImportDeliveryOut,
  ImportDeliveryPackageOut,
  ImportDeliveryRevisedOut,
  ImportDeliveryRevisionOut,
} from '../../lib/apiSchema.generated';
import { getErrorMessage } from '../../lib/errorMessage';
import { formatTimestamp } from '../../lib/format';
import ModalDialog from '../../components/ModalDialog';
import QueryFailure from '../../components/QueryFailure';

// The package as CoGA keeps it (docs/coga-package.md): only the parts the form shows.
interface PackageHpo {
  present?: string[];
  absent?: string[];
}
interface PackageMember {
  sample_id: string;
  sex: string;
  father?: string | null;
  mother?: string | null;
  role?: string | null;
  clinical_status?: string | null;
  carrier_status?: string | null;
  carrier_type?: string | null;
  hpo?: PackageHpo | null;
}
interface PackagePgt {
  inheritance_model?: string | null;
  affected_parents?: string[];
  indexes?: string[];
}
export interface PackageDocument {
  project?: string | null;
  analysis_type?: string | null;
  family?: { family_id?: string; roi?: string | null; pgt?: PackagePgt | null };
  members?: PackageMember[] | null;
  datasets?: Record<string, Record<string, unknown>> | null;
  pipeline_used_pedigree?: string[];
}

interface MemberForm {
  sample_id: string;
  sex: string;
  father: string;
  mother: string;
  role: string;
  clinical_status: string;
  carrier_status: string;
  carrier_type: string;
  hpo_present: string;
  hpo_absent: string;
}
export interface CorrectionForm {
  project: string;
  analysis_type: string;
  roi: string;
  inheritance_model: string;
  affected_parents: string[];
  indexes: string[];
  members: MemberForm[];
  enabled: Record<string, boolean>;
}

const ANALYSIS_TYPES = [
  'wgs',
  'wgs_trio',
  'pgt_m',
  'monogenic_nipt',
  'long_read_wgs',
  'long_read_couple',
];
const INHERITANCE_MODELS = ['AD', 'AR', 'XLD', 'XLR', 'mitochondrial'];
const ROLES = ['proband', 'father', 'mother', 'sibling', 'embryo', 'relative'];
const CLINICAL_STATUSES = ['affected', 'unaffected', 'unknown'];
const CARRIER_STATUSES = ['carrier', 'not_carrier', 'unknown'];
const CARRIER_TYPES = ['obligate', 'proven', 'reported', 'inferred'];

const text = (value: unknown) => (typeof value === 'string' ? value : '');
const terms = (value: string) =>
  value
    .split(/[\s,;]+/)
    .map((term) => term.trim())
    .filter(Boolean);
const sorted = (values: string[]) => [...values].sort();
const same = (left: unknown, right: unknown) =>
  JSON.stringify(left) === JSON.stringify(right);
const isEnabled = (entry: Record<string, unknown>) => entry.enabled !== false;

/** The form a package is corrected in, filled from the package as the import would read it. */
export const toForm = (pkg: PackageDocument): CorrectionForm => ({
  project: text(pkg.project),
  analysis_type: text(pkg.analysis_type),
  roi: text(pkg.family?.roi),
  inheritance_model: text(pkg.family?.pgt?.inheritance_model),
  affected_parents: [...(pkg.family?.pgt?.affected_parents ?? [])],
  indexes: [...(pkg.family?.pgt?.indexes ?? [])],
  members: (pkg.members ?? []).map((member) => ({
    sample_id: member.sample_id,
    sex: member.sex,
    father: text(member.father),
    mother: text(member.mother),
    role: text(member.role),
    clinical_status: text(member.clinical_status),
    carrier_status: text(member.carrier_status),
    carrier_type: text(member.carrier_type),
    hpo_present: (member.hpo?.present ?? []).join(', '),
    hpo_absent: (member.hpo?.absent ?? []).join(', '),
  })),
  enabled: Object.fromEntries(
    Object.entries(pkg.datasets ?? {}).map(([name, entry]) => [
      name,
      isEnabled(entry),
    ])
  ),
});

const hpoValue = (present: string[], absent: string[]) =>
  present.length || absent.length ? { present, absent } : null;

/** What the form changes in the package, as the correction the API takes: each field changed,
 * set to its new value or removed (null). */
export const changesBetween = (
  pkg: PackageDocument,
  form: CorrectionForm
): ImportDeliveryChange[] => {
  const changes: ImportDeliveryChange[] = [];
  const field = (path: string[], before: unknown, after: unknown) => {
    if (!same(before ?? null, after ?? null)) {
      changes.push({ path, value: after ?? null });
    }
  };
  field(['project'], pkg.project, form.project.trim() || null);
  field(['analysis_type'], pkg.analysis_type, form.analysis_type || null);
  field(['family', 'roi'], pkg.family?.roi, form.roi.trim() || null);
  const pgt = pkg.family?.pgt ?? {};
  field(
    ['family', 'pgt', 'inheritance_model'],
    pgt.inheritance_model,
    form.inheritance_model || null
  );
  for (const key of ['affected_parents', 'indexes'] as const) {
    const before = sorted(pgt[key] ?? []);
    const after = sorted(form[key]);
    if (!same(before, after)) {
      changes.push({
        path: ['family', 'pgt', key],
        value: after.length ? after : null,
      });
    }
  }
  (pkg.members ?? []).forEach((member, index) => {
    const edited = form.members[index];
    if (!edited) return;
    const path = (name: string) => ['members', member.sample_id, name];
    field(path('sex'), member.sex, edited.sex);
    for (const name of [
      'father',
      'mother',
      'role',
      'clinical_status',
      'carrier_status',
      'carrier_type',
    ] as const) {
      field(path(name), member[name], edited[name] || null);
    }
    field(
      path('hpo'),
      hpoValue(member.hpo?.present ?? [], member.hpo?.absent ?? []),
      hpoValue(terms(edited.hpo_present), terms(edited.hpo_absent))
    );
  });
  for (const [name, entry] of Object.entries(pkg.datasets ?? {})) {
    if (form.enabled[name] === isEnabled(entry)) continue;
    const next: Record<string, unknown> = { ...entry };
    if (form.enabled[name]) delete next.enabled;
    else next.enabled = false;
    changes.push({ path: ['datasets', name], value: next });
  }
  return changes;
};

const showValue = (value: unknown) =>
  value === null || value === undefined
    ? 'not set'
    : typeof value === 'string'
      ? value
      : JSON.stringify(value);

const History: React.FC<{ revisions: ImportDeliveryRevisionOut[] }> = ({
  revisions,
}) => (
  <details className="text-sm">
    <summary>Corrections made ({revisions.length})</summary>
    <ol className="space-y-2">
      {revisions.map((revision) => (
        <li key={revision.revision}>
          <div>
            <strong>Revision {revision.revision}</strong> by{' '}
            {revision.created_by_name}, {formatTimestamp(revision.created_at)}:{' '}
            {revision.reason}
          </div>
          <ul className="table-subtle">
            {revision.changes.map((change) => (
              <li key={change.path.join('.')}>
                <code>{change.path.join('.')}</code>: {showValue(change.before)}{' '}
                → {showValue(change.after)}
                {change.kind === 'pipeline_used'
                  ? ' (the pipeline used it)'
                  : ''}
              </li>
            ))}
          </ul>
        </li>
      ))}
    </ol>
  </details>
);

const Select: React.FC<{
  label: string;
  value: string;
  options: string[];
  blank?: string;
  onChange: (value: string) => void;
}> = ({ label, value, options, blank, onChange }) => (
  <select
    aria-label={label}
    value={value}
    onChange={(event) => onChange(event.target.value)}
  >
    {blank !== undefined ? <option value="">{blank}</option> : null}
    {options.map((option) => (
      <option key={option} value={option}>
        {option.replaceAll('_', ' ')}
      </option>
    ))}
  </select>
);

interface DeliveryCorrectionDialogProps {
  delivery: ImportDeliveryOut;
  onCancel: () => void;
  onSaved: (result: ImportDeliveryRevisedOut) => void;
}

/** Corrects a delivered package before its approval (docs/data-import.md, "Correcting a
 * delivery"): what the laboratory knows and the pipeline did not, or what it got wrong. Each save
 * is a revision with its reason; the delivery is checked again at once. The pipeline's own fields
 * and the sample IDs are not offered: a wrong one is a refused delivery. */
const DeliveryCorrectionDialog: React.FC<DeliveryCorrectionDialogProps> = ({
  delivery,
  onCancel,
  onSaved,
}) => {
  const headingId = useId();
  const packageQuery = useQuery<ImportDeliveryPackageOut>({
    queryKey: ['family-import-delivery-package', delivery._id],
    queryFn: async () =>
      (
        await api.get(
          apiPath`/family-imports/deliveries/${delivery._id}/package`
        )
      ).data as ImportDeliveryPackageOut,
  });
  const pkg = (packageQuery.data?.package ?? null) as PackageDocument | null;
  const [form, setForm] = useState<CorrectionForm | null>(null);
  const [reason, setReason] = useState('');
  const current = form ?? (pkg ? toForm(pkg) : null);
  const changes = useMemo(
    () => (pkg && current ? changesBetween(pkg, current) : []),
    [pkg, current]
  );
  const usedBy = pkg?.pipeline_used_pedigree ?? [];
  const memberIds = (pkg?.members ?? []).map((member) => member.sample_id);

  const saveMutation = useMutation({
    mutationFn: async () =>
      (
        await api.post(
          apiPath`/family-imports/deliveries/${delivery._id}/revisions`,
          {
            revision: packageQuery.data?.revision ?? 0,
            reason: reason.trim(),
            changes,
          }
        )
      ).data as ImportDeliveryRevisedOut,
    onSuccess: onSaved,
  });

  const update = (change: (draft: CorrectionForm) => void) => {
    if (!current) return;
    const draft: CorrectionForm = structuredClone(current);
    change(draft);
    setForm(draft);
  };
  const toggleIn = (list: string[], id: string) =>
    list.includes(id) ? list.filter((item) => item !== id) : [...list, id];

  return (
    <ModalDialog
      onClose={() => {
        if (!saveMutation.isPending) onCancel();
      }}
      labelledBy={headingId}
      className="modal-surface surface-card delivery-correction-modal"
      closeOnBackdrop={false}
      isDirty={() => changes.length > 0 || reason.trim().length > 0}
    >
      <div className="space-y-2">
        <p className="page-kicker">Correct the package</p>
        <h2 id={headingId} className="section-title">
          Correct {delivery.folder_name}
        </h2>
        <p className="text-sm">
          The pipeline&apos;s file stays as it is: each correction is kept
          beside it with its reason, on the audit trail, and the delivery is
          checked again at once. The run, the assembly, the family and sample
          IDs and the files are the pipeline&apos;s: a wrong one is a refused
          delivery.
        </p>
      </div>

      {packageQuery.isError ? (
        <QueryFailure what="the package" error={packageQuery.error} />
      ) : null}
      {packageQuery.isLoading ? (
        <p className="table-subtle">Loading the package…</p>
      ) : null}
      {packageQuery.data && !pkg ? (
        <p className="status-note status-note--error">
          CoGA could not read this delivery&apos;s package: correct it in the
          package, which makes a new delivery.
        </p>
      ) : null}

      {pkg && current ? (
        <>
          {packageQuery.data?.proposed.length ? (
            <p className="status-note">
              CoGA proposed the {packageQuery.data.proposed.join(' and ')} from
              the folder: check them.
            </p>
          ) : null}

          <div className="delivery-correction-grid">
            <label className="field-label">
              Project
              <input
                value={current.project}
                onChange={(event) =>
                  update((draft) => {
                    draft.project = event.target.value;
                  })
                }
              />
            </label>
            <div className="field-label">
              Analysis
              <Select
                label="Analysis"
                value={current.analysis_type}
                options={ANALYSIS_TYPES}
                blank="Not set"
                onChange={(value) =>
                  update((draft) => {
                    draft.analysis_type = value;
                  })
                }
              />
            </div>
            <label className="field-label">
              Region of interest
              <input
                value={current.roi}
                placeholder="A gene or chr7:1000-2000"
                onChange={(event) =>
                  update((draft) => {
                    draft.roi = event.target.value;
                  })
                }
              />
            </label>
            <div className="field-label">
              Inheritance model (PGT)
              <Select
                label="Inheritance model"
                value={current.inheritance_model}
                options={INHERITANCE_MODELS}
                blank="Not set"
                onChange={(value) =>
                  update((draft) => {
                    draft.inheritance_model = value;
                  })
                }
              />
            </div>
          </div>

          <h3 className="eyebrow-label">Members</h3>
          {usedBy.length ? (
            <p className="text-sm">
              The pipeline computed {usedBy.join(', ')} with the sex and parents
              below: a change to them needs attention while those datasets are
              in the import.
            </p>
          ) : null}
          <div className="data-table-shell overflow-x-auto">
            <table className="analysis-table">
              <thead>
                <tr>
                  <th>Sample</th>
                  <th>Sex</th>
                  <th>Father</th>
                  <th>Mother</th>
                  <th>Role</th>
                  <th>Status</th>
                  <th>Carrier</th>
                  <th>HPO present</th>
                  <th>HPO absent</th>
                  <th>PGT</th>
                </tr>
              </thead>
              <tbody>
                {current.members.map((member, index) => {
                  const id = member.sample_id;
                  const others = memberIds.filter((other) => other !== id);
                  const set = (name: keyof MemberForm) => (value: string) =>
                    update((draft) => {
                      draft.members[index][name] = value;
                    });
                  return (
                    <tr key={id}>
                      <td className="font-mono">{id}</td>
                      <td>
                        <Select
                          label={`Sex of ${id}`}
                          value={member.sex}
                          options={['male', 'female', 'unknown']}
                          onChange={set('sex')}
                        />
                      </td>
                      <td>
                        <Select
                          label={`Father of ${id}`}
                          value={member.father}
                          options={others}
                          blank="—"
                          onChange={set('father')}
                        />
                      </td>
                      <td>
                        <Select
                          label={`Mother of ${id}`}
                          value={member.mother}
                          options={others}
                          blank="—"
                          onChange={set('mother')}
                        />
                      </td>
                      <td>
                        <Select
                          label={`Role of ${id}`}
                          value={member.role}
                          options={ROLES}
                          blank="—"
                          onChange={set('role')}
                        />
                      </td>
                      <td>
                        <Select
                          label={`Clinical status of ${id}`}
                          value={member.clinical_status}
                          options={CLINICAL_STATUSES}
                          blank="—"
                          onChange={set('clinical_status')}
                        />
                      </td>
                      <td>
                        <Select
                          label={`Carrier status of ${id}`}
                          value={member.carrier_status}
                          options={CARRIER_STATUSES}
                          blank="—"
                          onChange={set('carrier_status')}
                        />
                        <Select
                          label={`Carrier type of ${id}`}
                          value={member.carrier_type}
                          options={CARRIER_TYPES}
                          blank="—"
                          onChange={set('carrier_type')}
                        />
                      </td>
                      <td>
                        <input
                          aria-label={`HPO terms present in ${id}`}
                          value={member.hpo_present}
                          placeholder="HP:0001250"
                          onChange={(event) =>
                            set('hpo_present')(event.target.value)
                          }
                        />
                      </td>
                      <td>
                        <input
                          aria-label={`HPO terms absent in ${id}`}
                          value={member.hpo_absent}
                          onChange={(event) =>
                            set('hpo_absent')(event.target.value)
                          }
                        />
                      </td>
                      <td>
                        <label className="inline-flex items-center gap-1 text-xs">
                          <input
                            type="checkbox"
                            checked={current.affected_parents.includes(id)}
                            onChange={() =>
                              update((draft) => {
                                draft.affected_parents = toggleIn(
                                  draft.affected_parents,
                                  id
                                );
                              })
                            }
                          />
                          Affected parent
                        </label>
                        <label className="inline-flex items-center gap-1 text-xs">
                          <input
                            type="checkbox"
                            checked={current.indexes.includes(id)}
                            onChange={() =>
                              update((draft) => {
                                draft.indexes = toggleIn(draft.indexes, id);
                              })
                            }
                          />
                          Index
                        </label>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <h3 className="eyebrow-label">Datasets</h3>
          <ul className="space-y-1 text-sm">
            {Object.keys(current.enabled).map((name) => (
              <li key={name}>
                <label className="inline-flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={current.enabled[name]}
                    onChange={(event) =>
                      update((draft) => {
                        draft.enabled[name] = event.target.checked;
                      })
                    }
                  />
                  <span className="font-mono">{name}</span> in the import
                  {usedBy.includes(name) ? (
                    <span className="table-subtle">
                      {' '}
                      (computed with the pedigree)
                    </span>
                  ) : null}
                </label>
              </li>
            ))}
          </ul>

          {packageQuery.data?.revisions.length ? (
            <History revisions={packageQuery.data.revisions} />
          ) : null}

          <label className="field-label">
            Reason for the correction
            <textarea
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              rows={2}
            />
          </label>
          <p className="table-subtle">
            {changes.length === 0
              ? 'Nothing changed yet.'
              : `${changes.length} field(s) changed: ${changes.map((change) => change.path.join('.')).join(', ')}.`}
          </p>
          {saveMutation.isError ? (
            <p className="status-note status-note--error">
              {getErrorMessage(
                saveMutation.error,
                'The correction could not be saved.'
              )}
            </p>
          ) : null}
        </>
      ) : null}

      <div className="inline-actions modal-actions">
        <button
          type="button"
          className="button-secondary"
          onClick={onCancel}
          disabled={saveMutation.isPending}
        >
          Cancel
        </button>
        <button
          type="button"
          className="form-button"
          disabled={
            saveMutation.isPending ||
            changes.length === 0 ||
            reason.trim().length === 0
          }
          onClick={() => saveMutation.mutate()}
        >
          {saveMutation.isPending ? 'Saving…' : 'Save the correction'}
        </button>
      </div>
    </ModalDialog>
  );
};

export default DeliveryCorrectionDialog;
