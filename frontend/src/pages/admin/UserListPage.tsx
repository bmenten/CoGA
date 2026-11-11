import React from 'react';
import { Link } from 'react-router';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import PageState from '../../components/PageState';
import ConfirmDeleteDialog from '../../components/ConfirmDeleteDialog';
import { apiPath } from '../../lib/apiPath';
import type {
  AccountRequestMailOut,
  AccountRequestMailTestOut,
  ProjectDashboardOut,
  UserRead,
  UserUpdate,
} from '../../lib/apiSchema.generated';
import { USER_ROLES, getStoredUsername } from '../../lib/auth';
import { getErrorMessage } from '../../lib/errorMessage';
import { formatShortDate, formatTimestamp } from '../../lib/format';
import { userAccountsQuery } from '../../lib/users';
import { memberAccessLevel } from '../../lib/familyAccess';
import type { ProjectOption } from './dataManagementTypes';
import AccountChangeDialog from './AccountChangeDialog';
import UserProjectAccessDialog, { type ProjectAccessChange } from './UserProjectAccessDialog';

type User = UserRead;
type Role = NonNullable<UserUpdate['role']>;

// The global roles, lowest first, each with every right of the ones before it (lib/auth.ts).
const ROLES: readonly Role[] = USER_ROLES;
// The roles with every right in every project, member or not.
const EVERY_PROJECT_ROLES: ReadonlySet<string> = new Set(['superuser', 'admin']);
// What each role may do, as the role picker's tooltip says it.
const ROLE_HINTS: Record<Role, string> = {
  viewer: 'Reads the records of their projects; changes none.',
  analyst: 'Also analyses them in the projects where they are an analyst: reviews, tags, classifications, phenotypes.',
  supervisor: 'Also signs an analysis out.',
  superuser:
    'Also every project, and the gene panels, variant tags, preset filters, sequencing QC thresholds and package import.',
  admin: 'Also the whole administration: accounts, projects, reference data, data, audit logs.',
};
const SIGN_IN_LABELS: Record<User['sign_in'], string> = {
  microsoft: 'Microsoft',
  cas: 'Organisation (CAS)',
  password: 'Password',
};
// How each confirmation says it: "Signs in with …".
const SIGN_IN_PHRASES: Record<User['sign_in'], string> = {
  microsoft: 'Microsoft',
  cas: 'the organisation sign-in (CAS)',
  password: 'a password',
};

// Where an account stands: waiting for approval (never active: a sign-up or a first CAS
// sign-in), active, inactive (deactivated since it was active), or archived.
type AccountState = 'waiting' | 'active' | 'inactive' | 'archived';
const accountState = (user: User): AccountState => {
  if (user.archived_at) return 'archived';
  if (user.is_active) return 'active';
  return user.activated_at ? 'inactive' : 'waiting';
};
const STATE_LABELS: Record<AccountState, string> = {
  waiting: 'Waiting for approval',
  active: 'Active',
  inactive: 'Inactive',
  archived: 'Archived',
};
const STATE_CHIPS: Record<AccountState, string> = {
  waiting: 'badge-chip badge-chip--signature',
  active: 'badge-chip badge-chip--success',
  inactive: 'badge-chip',
  archived: 'badge-chip',
};

// A change an administrator asked for, waiting for their confirmation.
type AccountChange =
  | { kind: 'activate' | 'deactivate' | 'archive' | 'restore'; user: User }
  | { kind: 'role'; user: User; role: Role };

const changeBody = (change: AccountChange): UserUpdate => {
  switch (change.kind) {
    case 'activate':
      return { is_active: true };
    case 'deactivate':
      return { is_active: false };
    case 'archive':
      return { archived: true };
    case 'restore':
      return { archived: false };
    case 'role':
      return { role: change.role };
  }
};

const changeDone = (change: AccountChange): string => {
  const { email } = change.user;
  switch (change.kind) {
    case 'activate':
      return `${email} is now active.`;
    case 'deactivate':
      return `${email} is now inactive.`;
    case 'archive':
      return `${email} is archived.`;
    case 'restore':
      return `${email} is restored from the archive. It stays inactive until you activate it.`;
    case 'role':
      return `${email} is now ${change.role}.`;
  }
};

const changeVerb = (change: AccountChange): string =>
  change.kind === 'role' ? 'change the role of' : change.kind;

const fullName = (user: User): string => `${user.first_name ?? ''} ${user.last_name ?? ''}`.trim();

// Who the account is, as each confirmation names it.
const AccountSummary: React.FC<{ user: User }> = ({ user }) => (
  <p className="catalog-card-copy">
    <strong>{fullName(user) || user.email}</strong>
    {fullName(user) ? ` (${user.email})` : null}
    {user.affiliation ? `, ${user.affiliation}` : null}. Signs in with{' '}
    {SIGN_IN_PHRASES[user.sign_in] ?? user.sign_in}; added on {formatShortDate(user.created_at)}.
  </p>
);

const changeDialogText = (change: AccountChange): { title: string; confirm: string; body: string } => {
  const { user } = change;
  switch (change.kind) {
    case 'activate':
      return {
        title: `Activate ${user.email}?`,
        confirm: 'Activate',
        body:
          accountState(user) === 'waiting'
            ? `The account can then sign in, as a ${user.role}, and sees the projects it is a member of${
                user.projects.length ? '' : ' (none yet: give it access with Edit, under Project access)'
              }. Once activated, it can no longer be deleted, only archived.`
            : `The account can sign in again, as a ${user.role}, with its project access as it is.`,
      };
    case 'deactivate':
      return {
        title: `Deactivate ${user.email}?`,
        confirm: 'Deactivate',
        body: 'The account can no longer sign in. Every record that names it stays as it is, and you can activate it again.',
      };
    case 'archive':
      return {
        title: `Archive ${user.email}?`,
        confirm: 'Archive',
        body:
          'The account is deactivated and leaves this list (tick “Show archived accounts” to see it). Every record that names it stays as it is, and a later sign-in of the same person makes no new request. You can restore it.',
      };
    case 'restore':
      return {
        title: `Restore ${user.email} from the archive?`,
        confirm: 'Restore',
        body: 'The account comes back to this list, inactive: activate it separately if it should sign in again.',
      };
    case 'role':
      return {
        title: `Change the role of ${user.email}?`,
        confirm: 'Change role',
        body: `From ${user.role} to ${change.role}. ${ROLE_HINTS[change.role]}`,
      };
  }
};

// The account-request mail: who it reaches, and a test of it.
const AccountRequestMail: React.FC = () => {
  const [result, setResult] = React.useState<{ tone: 'success' | 'error'; text: string } | null>(null);
  const { data: mail } = useQuery({
    queryKey: ['admin', 'account-request-mail'],
    queryFn: async (): Promise<AccountRequestMailOut> => {
      const response = await api.get('/auth/account-request-mail');
      return response.data as AccountRequestMailOut;
    },
    retry: false,
  });
  const testMutation = useMutation({
    mutationFn: async () => {
      const response = await api.post('/auth/account-request-mail/test');
      return response.data as AccountRequestMailTestOut;
    },
    onMutate: () => setResult(null),
    onSuccess: (sent) =>
      setResult({ tone: 'success', text: `A test mail went to ${sent.recipients.join(', ')}.` }),
    onError: (error) =>
      setResult({
        tone: 'error',
        text: `The test mail was not sent: ${getErrorMessage(error, 'the request failed')}`,
      }),
  });
  if (!mail) return null;
  return (
    <div className="space-y-2">
      <div className="inline-actions">
        <p className="catalog-card-copy">
          {mail.enabled
            ? mail.recipients.length
              ? `A new account waiting for approval is e-mailed to every active administrator: ${mail.recipients.join(', ')}.`
              : 'A new account waiting for approval would be e-mailed to every active administrator, but there is none.'
            : 'No e-mail goes out for a new account waiting for approval: no mail server is set (SMTP_HOST).'}
        </p>
        {mail.enabled ? (
          <button
            type="button"
            className="button-secondary"
            onClick={() => testMutation.mutate()}
            disabled={testMutation.isPending || mail.recipients.length === 0}
          >
            {testMutation.isPending ? 'Sending…' : 'Send a test mail'}
          </button>
        ) : null}
      </div>
      {result ? (
        <p
          className={`status-note ${result.tone === 'error' ? 'status-note--error' : 'status-note--success'}`}
          role={result.tone === 'error' ? 'alert' : 'status'}
        >
          {result.text}
        </p>
      ) : null}
    </div>
  );
};

const UserListPage: React.FC = () => {
  const queryClient = useQueryClient();
  // What the last change did: a refused (de)activation says why, not only by a checkbox
  // snapping back.
  const [activationStatus, setActivationStatus] = React.useState<{
    tone: 'success' | 'error';
    text: string;
  } | null>(null);
  // The change waiting for the administrator's confirmation: nothing is sent before it.
  const [pendingChange, setPendingChange] = React.useState<AccountChange | null>(null);
  const [pendingDelete, setPendingDelete] = React.useState<User | null>(null);
  // The account whose project access is being edited, and why its last save was refused: the
  // dialog stays open on a refusal, with the edits.
  const [accessUser, setAccessUser] = React.useState<User | null>(null);
  const [accessError, setAccessError] = React.useState<string | null>(null);
  const [showArchived, setShowArchived] = React.useState(false);

  const {
    data: users = [],
    isLoading: usersLoading,
    error: usersError,
  } = useQuery({ ...userAccountsQuery, retry: false });

  const {
    data: projects = [],
    isLoading: projectsLoading,
    error: projectsError,
  } = useQuery<ProjectOption[]>({
    queryKey: ['admin', 'projects'],
    queryFn: async () => {
      const response = await api.get('/projects');
      // A project's id is `_id`.
      return (response.data as ProjectDashboardOut[]).map((p) => ({
        id: p._id,
        name: p.name,
      }));
    },
    retry: false,
  });

  // The administrator signed in here: nobody changes their own role, or deactivates, archives
  // or deletes their own account (the backend refuses each).
  const signedInAs = getStoredUsername();

  const changeMutation = useMutation({
    mutationFn: async (change: AccountChange) => {
      const response = await api.patch(apiPath`/auth/users/${change.user.id}`, changeBody(change));
      return response.data as User;
    },
    onMutate: () => setActivationStatus(null),
    onSuccess: async (_updated, change) => {
      setPendingChange(null);
      setActivationStatus({ tone: 'success', text: changeDone(change) });
      await queryClient.invalidateQueries({ queryKey: userAccountsQuery.queryKey });
    },
    onError: (error, change) => {
      setPendingChange(null);
      setActivationStatus({
        tone: 'error',
        text: `Could not ${changeVerb(change)} ${change.user.email}: ${getErrorMessage(
          error,
          'the request failed',
        )}`,
      });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async (user: User) => {
      await api.delete(apiPath`/auth/users/${user.id}`);
    },
    onMutate: () => setActivationStatus(null),
    onSuccess: async (_none, user) => {
      setPendingDelete(null);
      setActivationStatus({ tone: 'success', text: `${user.email} is deleted.` });
      await queryClient.invalidateQueries({ queryKey: userAccountsQuery.queryKey });
    },
    onError: (error, user) => {
      setPendingDelete(null);
      setActivationStatus({
        tone: 'error',
        text: `Could not delete ${user.email}: ${getErrorMessage(error, 'the request failed')}`,
      });
    },
  });

  const accessMutation = useMutation({
    mutationFn: async ({ user, change }: { user: User; change: ProjectAccessChange }) => {
      const response = await api.patch(apiPath`/auth/users/${user.id}`, change);
      return response.data as User;
    },
    onMutate: () => {
      setActivationStatus(null);
      setAccessError(null);
    },
    onSuccess: async (_updated, { user }) => {
      setAccessUser(null);
      setActivationStatus({ tone: 'success', text: `Saved the project access of ${user.email}.` });
      // The Projects page lists each project's members from caches of its own.
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: userAccountsQuery.queryKey }),
        queryClient.invalidateQueries({ queryKey: ['projects'] }),
        queryClient.invalidateQueries({ queryKey: ['admin', 'projects'] }),
      ]);
    },
    onError: (error) => {
      setAccessError(
        `The project access was not saved: ${getErrorMessage(error, 'the request failed')}`,
      );
    },
  });

  const busyWith = (user: User): boolean =>
    (changeMutation.isPending && changeMutation.variables?.user.id === user.id) ||
    (deleteMutation.isPending && deleteMutation.variables?.id === user.id) ||
    (accessMutation.isPending && accessMutation.variables?.user.id === user.id);

  const askRole = (user: User, role: Role) => {
    // A select changes with an arrow key too: a role is changed only once confirmed.
    if (role === user.role) return;
    setPendingChange({ kind: 'role', user, role });
  };

  if (usersLoading || projectsLoading) {
    return (
      <PageState
        loading
        kicker="Administration"
        title="Loading users"
        message="Preparing user activation state and project access."
      />
    );
  }

  if (usersError || projectsError) {
    return (
      <PageState
        kicker="Administration"
        title="Could not load users"
        message={getErrorMessage(
          usersError ?? projectsError,
          'The user list could not be loaded.'
        )}
      />
    );
  }

  const waiting = users
    .filter((u) => accountState(u) === 'waiting')
    .sort((a, b) => b.created_at.localeCompare(a.created_at));
  const archivedCount = users.filter((u) => accountState(u) === 'archived').length;
  // The accounts waiting for approval first, newest first; then the others as the API lists
  // them (by e-mail address), the archived ones only when asked for.
  const shown = [
    ...waiting,
    ...users.filter((u) => {
      const state = accountState(u);
      return state !== 'waiting' && (showArchived || state !== 'archived');
    }),
  ];
  const dialog = pendingChange ? changeDialogText(pendingChange) : null;

  return (
    <div className="page-shell admin-compact space-y-5">
      <section className="surface-card page-top-card">
        <div className="page-header">
          <div className="space-y-2">
            <p className="page-kicker">Administration</p>
            <h1 className="catalog-card-title">Users</h1>
            {/* The rules (roles, approval, archive) are in the user guide, not repeated here. */}
            <p className="catalog-card-copy">
              Approve new accounts and set each account&apos;s role, activation and project access.
              A project&apos;s members are also set in{' '}
              <Link to="/admin/access/projects">Projects &amp; access</Link>.{' '}
              <Link to="/docs#administration">How accounts and roles work</Link>
            </p>
            <AccountRequestMail />
          </div>
        </div>
      </section>
      <div className="surface-card">
        {activationStatus ? (
          <p
            className={`status-note ${
              activationStatus.tone === 'error' ? 'status-note--error' : 'status-note--success'
            }`}
            role={activationStatus.tone === 'error' ? 'alert' : 'status'}
          >
            {activationStatus.text}
          </p>
        ) : null}
        <div className="inline-actions">
          {waiting.length ? (
            <p className="status-note status-note--warning">
              {waiting.length === 1
                ? '1 account waits for approval.'
                : `${waiting.length} accounts wait for approval.`}
            </p>
          ) : null}
          {archivedCount ? (
            <label className="inline-flex items-center gap-2">
              <input
                type="checkbox"
                checked={showArchived}
                onChange={(event) => setShowArchived(event.target.checked)}
              />
              Show archived accounts ({archivedCount})
            </label>
          ) : null}
        </div>
        <div className="data-table-shell overflow-x-auto">
          <table className="analysis-table admin-user-table">
            <thead>
            <tr>
              <th>Email</th>
              <th>Name</th>
              <th>Affiliation</th>
              <th>Sign-in</th>
              <th>Added</th>
              <th>Status</th>
              <th>Role</th>
              <th>Active</th>
              <th>Project access</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((u) => {
              const state = accountState(u);
              const own = u.email === signedInAs;
              const busy = busyWith(u);
              return (
                <tr key={u.id}>
                  <td>{u.email}</td>
                  <td>{fullName(u) || '—'}</td>
                  <td>{u.affiliation || '—'}</td>
                  <td>{SIGN_IN_LABELS[u.sign_in] ?? u.sign_in}</td>
                  <td>
                    <time dateTime={u.created_at} title={formatTimestamp(u.created_at)}>
                      {formatShortDate(u.created_at)}
                    </time>
                  </td>
                  <td>
                    <span className={STATE_CHIPS[state]}>{STATE_LABELS[state]}</span>
                  </td>
                  <td>
                    <select
                      aria-label={`Role: ${u.email}`}
                      value={u.role}
                      title={own ? 'You cannot change your own role.' : undefined}
                      disabled={own || busy}
                      onChange={(event) => askRole(u, event.target.value as Role)}
                    >
                      {ROLES.map((role) => (
                        <option key={role} value={role} title={ROLE_HINTS[role]}>
                          {role}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="table-cell-center">
                    <input
                      type="checkbox"
                      aria-label={`Active: ${u.email}`}
                      checked={u.is_active}
                      title={
                        own
                          ? 'You cannot deactivate your own account.'
                          : state === 'archived'
                            ? 'Restore the account from the archive before activating it.'
                            : undefined
                      }
                      disabled={own || busy || state === 'archived'}
                      onChange={() =>
                        setPendingChange({ kind: u.is_active ? 'deactivate' : 'activate', user: u })
                      }
                    />
                  </td>
                  <td>
                    <div className="admin-user-access">
                      {EVERY_PROJECT_ROLES.has(u.role) ? (
                        <span className="table-subtle">Every project, as {u.role}</span>
                      ) : null}
                      {u.projects.length > 0 ? (
                        // One project a line: in two columns of no minimum width a project's
                        // chip, which never wraps, would run over the Actions column.
                        <div className="table-checkbox-grid">
                          {projects
                            .filter((project) => u.projects.includes(project.id))
                            .map((project) => (
                              <span key={project.id} className="table-chip">
                                {project.name}
                                <span> · {memberAccessLevel(u.reader_projects, project.id)}</span>
                              </span>
                            ))}
                        </div>
                      ) : EVERY_PROJECT_ROLES.has(u.role) ? null : (
                        <span className="table-empty">No project access</span>
                      )}
                      <button
                        type="button"
                        className="button-ghost project-member-action"
                        aria-label={`Edit project access: ${u.email}`}
                        disabled={busy}
                        onClick={() => {
                          setAccessError(null);
                          setAccessUser(u);
                        }}
                      >
                        Edit
                      </button>
                    </div>
                  </td>
                  <td>
                    <div className="admin-user-actions">
                      {state === 'archived' ? (
                        <button
                          type="button"
                          className="button-ghost"
                          aria-label={`Restore: ${u.email}`}
                          disabled={busy}
                          onClick={() => setPendingChange({ kind: 'restore', user: u })}
                        >
                          Restore
                        </button>
                      ) : (
                        <button
                          type="button"
                          className="button-ghost"
                          aria-label={`Archive: ${u.email}`}
                          title={own ? 'You cannot archive your own account.' : undefined}
                          disabled={own || busy}
                          onClick={() => setPendingChange({ kind: 'archive', user: u })}
                        >
                          Archive
                        </button>
                      )}
                      {!u.is_active && !u.activated_at ? (
                        <button
                          type="button"
                          className="button-danger"
                          aria-label={`Delete: ${u.email}`}
                          disabled={busy}
                          onClick={() => setPendingDelete(u)}
                        >
                          Delete
                        </button>
                      ) : null}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
          </table>
        </div>
      </div>
      {pendingChange && dialog ? (
        <AccountChangeDialog
          title={dialog.title}
          confirmLabel={dialog.confirm}
          busy={changeMutation.isPending}
          onCancel={() => setPendingChange(null)}
          onConfirm={() => changeMutation.mutate(pendingChange)}
        >
          <AccountSummary user={pendingChange.user} />
          <p className="catalog-card-copy">{dialog.body}</p>
        </AccountChangeDialog>
      ) : null}
      {accessUser ? (
        <UserProjectAccessDialog
          user={accessUser}
          projects={projects}
          busy={accessMutation.isPending}
          error={accessError}
          onCancel={() => setAccessUser(null)}
          onSave={(change) => accessMutation.mutate({ user: accessUser, change })}
        >
          <AccountSummary user={accessUser} />
        </UserProjectAccessDialog>
      ) : null}
      {pendingDelete ? (
        <ConfirmDeleteDialog
          title={`Delete ${pendingDelete.email}?`}
          confirmLabel="Delete account"
          busy={deleteMutation.isPending}
          onCancel={() => setPendingDelete(null)}
          onConfirm={() => deleteMutation.mutate(pendingDelete)}
        >
          <AccountSummary user={pendingDelete} />
          <ul>
            <li>The account was never activated: it signed in to nothing, and no record names it.</li>
            <li>Its row is removed; the deletion, with whom it was, is kept in the audit log.</li>
            <li>
              If the same person signs up or signs in again, a new account waits for approval. To
              keep them from asking again, archive the account instead.
            </li>
          </ul>
        </ConfirmDeleteDialog>
      ) : null}
    </div>
  );
};

export default UserListPage;
