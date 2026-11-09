import React, { useId, useState } from 'react';
import ModalDialog from '../../components/ModalDialog';
import AccessLevelPicker, { type AccessLevelPickerText } from '../../components/AccessLevelPicker';
import type { UserRead, UserUpdate } from '../../lib/apiSchema.generated';
import { memberAccessLevel, type ProjectAccessLevel } from '../../lib/familyAccess';
import type { ProjectOption } from './dataManagementTypes';

/** What the dialog saves: the account's projects and the readers among them (SEC-1). */
export interface ProjectAccessChange extends UserUpdate {
  project_ids: string[];
  reader_project_ids: string[];
}

const PICKER_TEXT: AccessLevelPickerText = {
  column: 'Project',
  none: 'No project access: the account opens no project.',
  add: 'Give access to a project',
  placeholder: 'Search by project name',
  noMatch: (search) => <>No project without access matches &ldquo;{search}&rdquo;.</>,
  all: 'The account is a member of every project.',
};

/** What a role makes of the levels, said above the list when it is not the plain case. */
const roleNote = (user: UserRead): string | null => {
  if (user.role === 'viewer') {
    return 'The account is a viewer: it reads the records of its projects and changes none, whatever its level there.';
  }
  if (user.role === 'superuser' || user.role === 'admin') {
    return `The account is ${user.role === 'admin' ? 'an administrator' : 'a superuser'}: it has every right in every project, member or not. The levels below count once its role is lowered.`;
  }
  return null;
};

/** The saved access against the edited one, one line per project that changes. */
const accessChanges = (
  user: UserRead,
  projectIds: readonly string[],
  readerIds: readonly string[],
  nameOf: (projectId: string) => string,
): string[] => {
  const lines: string[] = [];
  for (const projectId of projectIds) {
    const level = memberAccessLevel(readerIds, projectId);
    if (!user.projects.includes(projectId)) {
      lines.push(`Gives access to ${nameOf(projectId)}, as ${level}.`);
    } else if (memberAccessLevel(user.reader_projects, projectId) !== level) {
      lines.push(
        `${nameOf(projectId)}: from ${memberAccessLevel(user.reader_projects, projectId)} to ${level}.`,
      );
    }
  }
  for (const projectId of user.projects) {
    if (!projectIds.includes(projectId)) lines.push(`Takes away the access to ${nameOf(projectId)}.`);
  }
  return lines;
};

/**
 * Sets an account's project access on the Users page: its projects and its level in each
 * (analyst or reader), the same memberships a project's settings set from the project's side.
 * The edits stay in the dialog, which lists what they change, until **Save project access**
 * sends them in one request; Cancel and Escape ask before discarding them. A refused save keeps
 * the dialog and the edits open, with the reason.
 */
const UserProjectAccessDialog: React.FC<{
  user: UserRead;
  projects: ProjectOption[];
  busy: boolean;
  /** Why the last save was refused, shown in the dialog. */
  error: string | null;
  onCancel: () => void;
  onSave: (change: ProjectAccessChange) => void;
  /** Who the account is (the page's account summary). */
  children: React.ReactNode;
}> = ({ user, projects, busy, error, onCancel, onSave, children }) => {
  const headingId = useId();
  const [projectIds, setProjectIds] = useState<string[]>(() => [...user.projects]);
  // Read as memberAccessLevel reads it: no reader list, no reader.
  const [readerIds, setReaderIds] = useState<string[]>(() => [...(user.reader_projects ?? [])]);
  const nameOf = (projectId: string) =>
    projects.find((project) => project.id === projectId)?.name ?? projectId;
  const changes = accessChanges(user, projectIds, readerIds, nameOf);
  const note = roleNote(user);

  const toggle = (projectId: string) => {
    setProjectIds((current) =>
      current.includes(projectId)
        ? current.filter((value) => value !== projectId)
        : [...current, projectId],
    );
    // A project removed leaves no reader level behind; one added starts as an analyst.
    setReaderIds((current) => current.filter((value) => value !== projectId));
  };
  const setLevel = (projectId: string, level: ProjectAccessLevel) =>
    setReaderIds((current) =>
      level === 'reader'
        ? Array.from(new Set([...current, projectId]))
        : current.filter((value) => value !== projectId),
    );

  return (
    <ModalDialog
      onClose={() => {
        if (!busy) onCancel();
      }}
      labelledBy={headingId}
      className="modal-surface surface-card confirm-delete-modal user-project-access-modal"
      closeOnBackdrop={false}
      isDirty={() => changes.length > 0}
    >
      {(requestClose) => (
        <>
          <div className="space-y-2">
            <p className="page-kicker">Project access</p>
            <h2 id={headingId} className="section-title">
              Project access of {user.email}
            </h2>
          </div>
          <div className="space-y-2">
            {children}
            <p className="catalog-card-copy">
              An analyst reads and changes the project&rsquo;s records; a reader only reads them.
            </p>
            {note ? <p className="status-note">{note}</p> : null}
          </div>
          <AccessLevelPicker
            entries={projects.map((project) => ({ id: project.id, label: project.name }))}
            memberIds={projectIds}
            readerIds={readerIds}
            onToggle={toggle}
            onLevel={setLevel}
            text={PICKER_TEXT}
          />
          <div className="space-y-1" aria-live="polite">
            <p className="eyebrow-label">What the save changes</p>
            {changes.length ? (
              <ul className="user-project-access-changes">
                {changes.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            ) : (
              <p className="table-subtle">Nothing yet: the access is as saved.</p>
            )}
          </div>
          {error ? (
            <p className="status-note status-note--error" role="alert">
              {error}
            </p>
          ) : null}
          <div className="inline-actions modal-actions">
            <button type="button" className="button-secondary" onClick={requestClose} disabled={busy}>
              Cancel
            </button>
            <button
              type="button"
              className="form-button"
              disabled={busy || changes.length === 0}
              onClick={() =>
                onSave({
                  project_ids: projectIds,
                  reader_project_ids: readerIds.filter((projectId) => projectIds.includes(projectId)),
                })
              }
            >
              {busy ? 'Saving…' : 'Save project access'}
            </button>
          </div>
        </>
      )}
    </ModalDialog>
  );
};

export default UserProjectAccessDialog;
