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
  related_to?: string[] | null;
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
  related_to: string[];
}
type MemberTextField = Exclude<keyof MemberForm, 'related_to'>;
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
    related_to: [...(member.related_to ?? [])],
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

const MEMBER_TEXT_FIELDS = [
  'father',
  'mother',
  'role',
  'clinical_status',
  'carrier_status',
  'carrier_type',
] as const;

/** A member the form adds: with no field set but its sex, a relative. */
export const newMember = (sampleId: string): MemberForm => ({
  sample_id: sampleId,
  sex: 'unknown',
  father: '',
  mother: '',
  role: 'relative',
  clinical_status: '',
  carrier_status: '',
  carrier_type: '',
  hpo_present: '',
  hpo_absent: '',
  related_to: [],
});

/** A member the form adds, as the package holds it: the fields set. */
const memberValue = (member: MemberForm): Record<string, unknown> => {
  const value: Record<string, unknown> = { sex: member.sex };
  for (const name of MEMBER_TEXT_FIELDS) {
    if (member[name]) value[name] = member[name];
  }
  const hpo = hpoValue(terms(member.hpo_present), terms(member.hpo_absent));
  if (hpo) value.hpo = hpo;
  if (member.related_to.length) value.related_to = sorted(member.related_to);
  return value;
};

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
  const editedMembers = new Map(
    form.members.map((member) => [member.sample_id, member])
  );
  for (const member of pkg.members ?? []) {
    const edited = editedMembers.get(member.sample_id);
    if (!edited) {
      changes.push({ path: ['members', member.sample_id], value: null });
      continue;
    }
    const path = (name: string) => ['members', member.sample_id, name];
    field(path('sex'), member.sex, edited.sex);
    for (const name of MEMBER_TEXT_FIELDS) {
      field(path(name), member[name], edited[name] || null);
    }
    field(
      path('hpo'),
      hpoValue(member.hpo?.present ?? [], member.hpo?.absent ?? []),
      hpoValue(terms(edited.hpo_present), terms(edited.hpo_absent))
    );
    const relatedBefore = sorted(member.related_to ?? []);
    const relatedAfter = sorted(edited.related_to);
    if (!same(relatedBefore, relatedAfter)) {
      changes.push({
        path: path('related_to'),
        value: relatedAfter.length ? relatedAfter : null,
      });
    }
  }
  const delivered = new Set(
    (pkg.members ?? []).map((member) => member.sample_id)
  );
  for (const member of form.members) {
    if (!delivered.has(member.sample_id)) {
      changes.push({
        path: ['members', member.sample_id],
        value: memberValue(member),
      });
    }
  }
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

/** A choice among the format's values (shown with spaces for underscores), or among sample IDs
 * (`verbatim`: an ID is shown as it is). */
const Select: React.FC<{
  label: string;
  value: string;
  options: string[];
  blank?: string;
  verbatim?: boolean;
  onChange: (value: string) => void;
}> = ({ label, value, options, blank, verbatim = false, onChange }) => (
  <select
    aria-label={label}
    value={value}
    onChange={(event) => onChange(event.target.value)}
  >
    {blank !== undefined ? <option value="">{blank}</option> : null}
    {options.map((option) => (
      <option key={option} value={option}>
        {verbatim ? option : option.replaceAll('_', ' ')}
      </option>
    ))}
  </select>
);

// The order the format lists a package's fields and a member's in (docs/coga-package.md): the
// package is shown and saved in it, whatever order it was kept in.
const PACKAGE_FIELD_ORDER = [
  'schema',
  'status',
  'written_at',
  'pipeline',
  'project',
  'assembly',
  'analysis_type',
  'family',
  'members',
  'couples',
  'datasets',
  'pipeline_used_pedigree',
  'files',
  'qc',
  'request_id',
  'reference',
  'notes',
  'extra',
];
const MEMBER_FIELD_ORDER = [
  'sample_id',
  'sex',
  'father',
  'mother',
  'role',
  'clinical_status',
  'carrier_status',
  'carrier_type',
  'assay',
  'assay_panel',
  'hpo',
  'related_to',
];
const inOrder = (
  value: Record<string, unknown>,
  order: string[]
): Record<string, unknown> =>
  Object.fromEntries(
    Object.entries(value).sort(([left], [right]) => {
      const rank = (key: string) =>
        order.includes(key) ? order.indexOf(key) : order.length;
      return rank(left) - rank(right) || left.localeCompare(right);
    })
  );

/** The package as coga-package.json: its fields in the format's order, two spaces deep. */
export const packageJson = (pkg: PackageDocument): string => {
  const ordered = inOrder(pkg as Record<string, unknown>, PACKAGE_FIELD_ORDER);
  if (Array.isArray(ordered.members)) {
    ordered.members = ordered.members.map((member) =>
      member && typeof member === 'object'
        ? inOrder(member as Record<string, unknown>, MEMBER_FIELD_ORDER)
        : member
    );
  }
  return JSON.stringify(ordered, null, 2);
};

/** Saves the package as coga-package.json. */
export const downloadPackage = (pkg: PackageDocument) => {
  const url = window.URL.createObjectURL(
    new Blob([`${packageJson(pkg)}\n`], {
      type: 'application/json',
    })
  );
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = 'coga-package.json';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.URL.revokeObjectURL(url);
};

/** The package as the import would read it now, as coga-package.json: shown and saved. */
const PackageJson: React.FC<{
  pkg: PackageDocument;
  open?: boolean;
  onEdit?: () => void;
}> = ({ pkg, open = false, onEdit }) => (
  <details className="text-sm" open={open}>
    <summary>The package as coga-package.json</summary>
    <div className="space-y-2">
      <div className="inline-actions">
        <button
          type="button"
          className="button-secondary"
          onClick={() => downloadPackage(pkg)}
        >
          Download coga-package.json
        </button>
        {onEdit ? (
          <button type="button" className="button-secondary" onClick={onEdit}>
            Edit as JSON
          </button>
        ) : null}
      </div>
      <pre className="delivery-package-json">{packageJson(pkg)}</pre>
    </div>
  </details>
);

const usePackage = (deliveryId: string) =>
  useQuery<ImportDeliveryPackageOut>({
    queryKey: ['family-import-delivery-package', deliveryId],
    queryFn: async () =>
      (await api.get(apiPath`/family-imports/deliveries/${deliveryId}/package`))
        .data as ImportDeliveryPackageOut,
  });

/** Shows a delivery's package as coga-package.json, with the corrections made to it, for a
 * delivery no longer corrected (docs/data-import.md, "Correcting a delivery"). */
export const PackageFileDialog: React.FC<{
  delivery: ImportDeliveryOut;
  onClose: () => void;
}> = ({ delivery, onClose }) => {
  const headingId = useId();
  const packageQuery = usePackage(delivery._id);
  const pkg = (packageQuery.data?.package ?? null) as PackageDocument | null;
  return (
    <ModalDialog
      onClose={onClose}
      labelledBy={headingId}
      className="modal-surface surface-card delivery-correction-modal"
      confirmDiscard={false}
    >
      <div className="space-y-2">
        <p className="page-kicker">The package</p>
        <h2 id={headingId} className="section-title">
          Package of {delivery.folder_name}
        </h2>
        <p className="text-sm">
          The package as its import reads it
          {packageQuery.data?.revision
            ? `: revision ${packageQuery.data.revision}, corrected in CoGA`
            : ''}
          .
        </p>
      </div>
      {packageQuery.isError ? (
        <QueryFailure what="the package" error={packageQuery.error} />
      ) : null}
      {packageQuery.isLoading ? (
        <p className="table-subtle">Loading the package…</p>
      ) : null}
      {packageQuery.data && !pkg ? (
        <p className="status-note">
          CoGA could not read this delivery&apos;s package.
        </p>
      ) : null}
      {pkg ? <PackageJson pkg={pkg} open /> : null}
      {packageQuery.data?.revisions.length ? (
        <History revisions={packageQuery.data.revisions} />
      ) : null}
      <div className="inline-actions modal-actions">
        <button type="button" className="button-secondary" onClick={onClose}>
          Close
        </button>
      </div>
    </ModalDialog>
  );
};

interface DeliveryCorrectionDialogProps {
  delivery: ImportDeliveryOut;
  onCancel: () => void;
  onSaved: (result: ImportDeliveryRevisedOut) => void;
}

/** The edited JSON as a package, or why it is not one. */
const parsePackage = (
  value: string
): { package: Record<string, unknown> } | { error: string } => {
  try {
    const parsed: unknown = JSON.parse(value);
    if (
      typeof parsed !== 'object' ||
      parsed === null ||
      Array.isArray(parsed)
    ) {
      return { error: 'The package is one JSON object.' };
    }
    return { package: parsed as Record<string, unknown> };
  } catch (error) {
    return {
      error: `This is no JSON: ${error instanceof Error ? error.message : 'it cannot be read'}.`,
    };
  }
};

/** Corrects a delivered package before its approval (docs/data-import.md, "Correcting a
 * delivery"): what the laboratory knows and the pipeline did not, or what it got wrong. Each save
 * is a revision with its reason; the delivery is checked again at once. The pipeline's own fields
 * and the sample IDs are not offered: a wrong one is a refused delivery. A sample the package's
 * files name and no member is can be added as a member; the package of a folder added by hand,
 * which CoGA made, takes any member and is also edited as JSON. */
const DeliveryCorrectionDialog: React.FC<DeliveryCorrectionDialogProps> = ({
  delivery,
  onCancel,
  onSaved,
}) => {
  const headingId = useId();
  const packageQuery = usePackage(delivery._id);
  const pkg = (packageQuery.data?.package ?? null) as PackageDocument | null;
  // Known from the delivery while its package loads.
  const byHand = (packageQuery.data?.origin ?? delivery.origin) === 'manual';
  const [form, setForm] = useState<CorrectionForm | null>(null);
  const [jsonText, setJsonText] = useState<string | null>(null);
  const [newMemberId, setNewMemberId] = useState('');
  const [reason, setReason] = useState('');
  const current = form ?? (pkg ? toForm(pkg) : null);
  const changes = useMemo(
    () => (pkg && current ? changesBetween(pkg, current) : []),
    [pkg, current]
  );
  const usedBy = pkg?.pipeline_used_pedigree ?? [];
  const memberIds = (current?.members ?? []).map((member) => member.sample_id);
  const asChecked = new Set(packageQuery.data?.members_as_checked ?? []);
  const offered = (packageQuery.data?.addable_members ?? []).filter(
    (sampleId) => !memberIds.includes(sampleId)
  );
  const removable = (sampleId: string) => byHand || !asChecked.has(sampleId);
  const edited = jsonText === null ? null : parsePackage(jsonText);
  const jsonChanged =
    jsonText !== null && pkg !== null && jsonText !== packageJson(pkg);
  const newId = newMemberId.trim();

  const saveMutation = useMutation({
    mutationFn: async () =>
      (
        await api.post(
          apiPath`/family-imports/deliveries/${delivery._id}/revisions`,
          {
            revision: packageQuery.data?.revision ?? 0,
            reason: reason.trim(),
            ...(edited && 'package' in edited
              ? { package: edited.package }
              : { changes }),
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
  const addMember = (sampleId: string) =>
    update((draft) => {
      draft.members.push(newMember(sampleId));
    });
  // A member removed, and every place the form names it.
  const removeMember = (sampleId: string) =>
    update((draft) => {
      draft.members = draft.members
        .filter((member) => member.sample_id !== sampleId)
        .map((member) => ({
          ...member,
          father: member.father === sampleId ? '' : member.father,
          mother: member.mother === sampleId ? '' : member.mother,
          related_to: member.related_to.filter((id) => id !== sampleId),
        }));
      draft.affected_parents = draft.affected_parents.filter(
        (id) => id !== sampleId
      );
      draft.indexes = draft.indexes.filter((id) => id !== sampleId);
    });
  const canSave =
    !saveMutation.isPending &&
    reason.trim().length > 0 &&
    (jsonText !== null
      ? jsonChanged && edited !== null && 'package' in edited
      : changes.length > 0);

  return (
    <ModalDialog
      onClose={() => {
        if (!saveMutation.isPending) onCancel();
      }}
      labelledBy={headingId}
      className="modal-surface surface-card delivery-correction-modal"
      closeOnBackdrop={false}
      isDirty={() =>
        changes.length > 0 || jsonChanged || reason.trim().length > 0
      }
    >
      <div className="space-y-2">
        <p className="page-kicker">Correct the package</p>
        <h2 id={headingId} className="section-title">
          Correct {delivery.folder_name}
        </h2>
        {byHand ? (
          <p className="text-sm">
            CoGA made this package from the folder, which was added by hand:
            correct it here, members added and removed too, or edit it as JSON.
            Each correction is kept with its reason, on the audit trail, and the
            delivery is checked again at once. The family ID, the assembly and
            the files stay as CoGA read them; CoGA writes nothing in the folder.
          </p>
        ) : (
          <p className="text-sm">
            The pipeline&apos;s file stays as it is: each correction is kept
            beside it with its reason, on the audit trail, and the delivery is
            checked again at once. The run, the assembly, the family and sample
            IDs and the files are the pipeline&apos;s: a wrong one is a refused
            delivery. A sample the package&apos;s files name and no member is
            can be added as a member.
          </p>
        )}
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

      {pkg && current && jsonText !== null ? (
        <>
          <label className="field-label">
            The package as JSON
            <textarea
              className="delivery-package-json"
              value={jsonText}
              onChange={(event) => setJsonText(event.target.value)}
              rows={24}
              spellCheck={false}
            />
          </label>
          {edited && 'error' in edited ? (
            <p className="status-note status-note--error">{edited.error}</p>
          ) : null}
          <div className="inline-actions">
            <button
              type="button"
              className="button-secondary"
              onClick={() => setJsonText(null)}
            >
              Back to the form
            </button>
          </div>
        </>
      ) : null}

      {pkg && current && jsonText === null ? (
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
              below: a change to them, or a member added with parents or
              removed, needs attention while those datasets are in the import.
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
                  <th>Related to</th>
                  <th>Role</th>
                  <th>Status</th>
                  <th>Carrier</th>
                  <th>HPO present</th>
                  <th>HPO absent</th>
                  <th>PGT</th>
                  <th>
                    <span className="sr-only">Remove</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {current.members.map((member, index) => {
                  const id = member.sample_id;
                  const others = memberIds.filter((other) => other !== id);
                  const set = (name: MemberTextField) => (value: string) =>
                    update((draft) => {
                      draft.members[index][name] = value;
                    });
                  return (
                    <tr key={id}>
                      <td className="font-mono">
                        {id}
                        {asChecked.has(id) ? null : (
                          <div className="table-subtle">added in CoGA</div>
                        )}
                      </td>
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
                          verbatim
                          blank="—"
                          onChange={set('father')}
                        />
                      </td>
                      <td>
                        <Select
                          label={`Mother of ${id}`}
                          value={member.mother}
                          options={others}
                          verbatim
                          blank="—"
                          onChange={set('mother')}
                        />
                      </td>
                      <td>
                        {member.related_to.length > 1 ? (
                          <span className="font-mono">
                            {member.related_to.join(', ')}
                          </span>
                        ) : (
                          <Select
                            label={`Member ${id} is related to`}
                            value={member.related_to[0] ?? ''}
                            options={others}
                            verbatim
                            blank="—"
                            onChange={(value) =>
                              update((draft) => {
                                draft.members[index].related_to = value
                                  ? [value]
                                  : [];
                              })
                            }
                          />
                        )}
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
                      <td>
                        {removable(id) ? (
                          <button
                            type="button"
                            className="button-secondary"
                            aria-label={`Remove ${id}`}
                            onClick={() => removeMember(id)}
                          >
                            Remove
                          </button>
                        ) : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {byHand || offered.length ? (
            <div className="space-y-1">
              <div className="inline-actions">
                <label className="field-label">
                  Add a member
                  {byHand ? (
                    <input
                      value={newMemberId}
                      list={`${headingId}-offered`}
                      placeholder="Sample ID"
                      onChange={(event) => setNewMemberId(event.target.value)}
                    />
                  ) : (
                    <select
                      value={newMemberId}
                      onChange={(event) => setNewMemberId(event.target.value)}
                    >
                      <option value="">Select a sample…</option>
                      {offered.map((sampleId) => (
                        <option key={sampleId} value={sampleId}>
                          {sampleId}
                        </option>
                      ))}
                    </select>
                  )}
                </label>
                {byHand ? (
                  <datalist id={`${headingId}-offered`}>
                    {offered.map((sampleId) => (
                      <option key={sampleId} value={sampleId} />
                    ))}
                  </datalist>
                ) : null}
                <button
                  type="button"
                  className="button-secondary"
                  disabled={!newId || memberIds.includes(newId)}
                  onClick={() => {
                    addMember(newId);
                    setNewMemberId('');
                  }}
                >
                  Add
                </button>
              </div>
              <p className="table-subtle">
                {offered.length
                  ? `The package's files name ${offered.join(', ')}, which no member is: add it to import its data, such as an affected relative read for segregation or a PGT index. `
                  : ''}
                Give the member&apos;s sex, role and status, and how it is
                related: its parents, or the member it is related to by an
                unknown degree (a PGT index: the affected parent, or the parent
                on whose side it is).
              </p>
            </div>
          ) : null}

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

          <PackageJson
            pkg={pkg}
            onEdit={
              byHand && changes.length === 0
                ? () => setJsonText(packageJson(pkg))
                : undefined
            }
          />
        </>
      ) : null}

      {pkg && current ? (
        <>
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
            {jsonText !== null
              ? jsonChanged
                ? 'The package as JSON is edited: CoGA works out what changed when you save.'
                : 'Nothing changed yet.'
              : changes.length === 0
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
          disabled={!canSave}
          onClick={() => saveMutation.mutate()}
        >
          {saveMutation.isPending ? 'Saving…' : 'Save the correction'}
        </button>
      </div>
    </ModalDialog>
  );
};

export default DeliveryCorrectionDialog;
