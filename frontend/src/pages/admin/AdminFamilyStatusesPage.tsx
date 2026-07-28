import React, { useState } from 'react';
import { Link } from 'react-router';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import PageState from '../../components/PageState';
import FamilyStatusBadge from '../../components/FamilyStatusBadge';
import { getErrorMessage } from '../../lib/errorMessage';
import type { ApiFamilyStatusDefinition } from '../../lib/apiTypes';

type StatusTone = 'success' | 'error';

interface StatusDraft {
  label: string;
  description: string;
  color: string;
  sort_order: string;
}

const EMPTY_DRAFT: StatusDraft = { label: '', description: '', color: '#5b6b79', sort_order: '' };
const QUERY_KEY = ['admin', 'family-statuses'];

const AdminFamilyStatusesPage: React.FC = () => {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<{ tone: StatusTone; message: string } | null>(null);
  const [newStatus, setNewStatus] = useState<StatusDraft>(EMPTY_DRAFT);
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [editingDraft, setEditingDraft] = useState<StatusDraft>(EMPTY_DRAFT);

  const {
    data: statuses = [],
    isLoading,
    error,
  } = useQuery<ApiFamilyStatusDefinition[]>({
    queryKey: QUERY_KEY,
    queryFn: async () => {
      const response = await api.get('/admin/family-statuses');
      return response.data as ApiFamilyStatusDefinition[];
    },
    retry: false,
  });

  const sortOrderValue = (value: string): number | undefined =>
    value.trim() ? Number(value) : undefined;

  const createMutation = useMutation({
    mutationFn: async () => {
      const response = await api.post('/admin/family-statuses', {
        label: newStatus.label.trim(),
        description: newStatus.description.trim() || undefined,
        color: newStatus.color || undefined,
        sort_order: sortOrderValue(newStatus.sort_order),
      });
      return response.data as ApiFamilyStatusDefinition;
    },
    onSuccess: async (created) => {
      await queryClient.invalidateQueries({ queryKey: QUERY_KEY });
      setStatus({ tone: 'success', message: `Created status “${created.label}”.` });
      setNewStatus(EMPTY_DRAFT);
    },
    onError: (err) =>
      setStatus({ tone: 'error', message: getErrorMessage(err, 'Could not create the family status.') }),
  });

  const updateMutation = useMutation({
    mutationFn: async (key: string) => {
      const response = await api.put(`/admin/family-statuses/${key}`, {
        label: editingDraft.label.trim(),
        description: editingDraft.description.trim() || null,
        color: editingDraft.color || undefined,
        sort_order: sortOrderValue(editingDraft.sort_order),
      });
      return response.data as ApiFamilyStatusDefinition;
    },
    onSuccess: async (updated) => {
      await queryClient.invalidateQueries({ queryKey: QUERY_KEY });
      setStatus({ tone: 'success', message: `Updated status “${updated.label}”.` });
      setEditingKey(null);
    },
    onError: (err) =>
      setStatus({ tone: 'error', message: getErrorMessage(err, 'Could not update the family status.') }),
  });

  const toggleActiveMutation = useMutation({
    mutationFn: async (target: ApiFamilyStatusDefinition) => {
      const response = await api.put(`/admin/family-statuses/${target.key}`, {
        is_active: !target.is_active,
      });
      return response.data as ApiFamilyStatusDefinition;
    },
    onSuccess: async (updated) => {
      await queryClient.invalidateQueries({ queryKey: QUERY_KEY });
      setStatus({
        tone: 'success',
        message: `${updated.is_active ? 'Activated' : 'Deactivated'} “${updated.label}”.`,
      });
    },
    onError: (err) =>
      setStatus({ tone: 'error', message: getErrorMessage(err, 'Could not update the family status.') }),
  });

  const deleteMutation = useMutation({
    mutationFn: async (key: string) => {
      await api.delete(`/admin/family-statuses/${key}`);
      return key;
    },
    onSuccess: async (key) => {
      await queryClient.invalidateQueries({ queryKey: QUERY_KEY });
      setStatus({ tone: 'success', message: `Deleted status “${key}”.` });
      if (editingKey === key) setEditingKey(null);
    },
    onError: (err) =>
      setStatus({ tone: 'error', message: getErrorMessage(err, 'Could not delete the family status.') }),
  });

  const beginEdit = (target: ApiFamilyStatusDefinition) => {
    setEditingKey(target.key);
    setEditingDraft({
      label: target.label,
      description: target.description ?? '',
      color: target.color,
      sort_order: String(target.sort_order),
    });
  };

  if (isLoading) {
    return (
      <PageState
        kicker="Admin"
        title="Loading family statuses"
        message="Fetching the family status catalogue."
      />
    );
  }

  if (error) {
    return (
      <PageState
        kicker="Admin"
        title="Could not load family statuses"
        message={getErrorMessage(error, 'The family status catalogue could not be loaded.')}
      />
    );
  }

  const busy =
    createMutation.isPending ||
    updateMutation.isPending ||
    deleteMutation.isPending ||
    toggleActiveMutation.isPending;

  return (
    <div className="page-shell space-y-6">
      <section className="surface-card page-top-card">
        <p className="page-kicker">
          <Link to="/admin" className="table-link">
            Admin
          </Link>{' '}
          / Family statuses
        </p>
        <h1 className="catalog-card-title">Family statuses</h1>
        <p className="section-copy">
          Curate the workflow statuses analysts can assign to a family from the family page. Deleting
          a status clears it from any family currently using it.
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

      <section className="surface-card-flat space-y-4">
        <h2 className="section-title">Add a status</h2>
        <form
          className="family-status-form"
          onSubmit={(event) => {
            event.preventDefault();
            createMutation.mutate();
          }}
        >
          <label className="field-label">
            Label
            <input
              type="text"
              value={newStatus.label}
              onChange={(event) => setNewStatus((current) => ({ ...current, label: event.target.value }))}
              placeholder="e.g. Pending lab confirmation"
            />
          </label>
          <label className="field-label">
            Description
            <input
              type="text"
              value={newStatus.description}
              onChange={(event) =>
                setNewStatus((current) => ({ ...current, description: event.target.value }))
              }
            />
          </label>
          <label className="field-label family-status-color-field">
            Colour
            <input
              type="color"
              aria-label="Status colour"
              value={newStatus.color}
              onChange={(event) => setNewStatus((current) => ({ ...current, color: event.target.value }))}
            />
          </label>
          <label className="field-label family-status-sort-field">
            Sort order
            <input
              type="number"
              value={newStatus.sort_order}
              onChange={(event) =>
                setNewStatus((current) => ({ ...current, sort_order: event.target.value }))
              }
              placeholder="auto"
            />
          </label>
          <button type="submit" className="form-button" disabled={busy || !newStatus.label.trim()}>
            Add status
          </button>
        </form>
      </section>

      <section className="surface-card-flat space-y-4">
        <h2 className="section-title">Status catalogue</h2>
        <div className="data-table-shell overflow-x-auto">
          <table className="analysis-table">
            <thead>
              <tr>
                <th>Status</th>
                <th>Key</th>
                <th>Sort</th>
                <th>Active</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {statuses.length === 0 && (
                <tr>
                  <td colSpan={5}>
                    <p className="table-empty">No statuses defined yet.</p>
                  </td>
                </tr>
              )}
              {statuses.map((item) => {
                const editing = editingKey === item.key;
                return (
                  <tr key={item.key}>
                    {editing ? (
                      <>
                        <td>
                          <div className="family-status-edit-fields">
                            <input
                              type="text"
                              aria-label={`Label for ${item.key}`}
                              value={editingDraft.label}
                              onChange={(event) =>
                                setEditingDraft((current) => ({ ...current, label: event.target.value }))
                              }
                            />
                            <input
                              type="color"
                              aria-label={`Colour for ${item.key}`}
                              value={editingDraft.color}
                              onChange={(event) =>
                                setEditingDraft((current) => ({ ...current, color: event.target.value }))
                              }
                            />
                          </div>
                          <input
                            type="text"
                            className="family-status-edit-description"
                            aria-label={`Description for ${item.key}`}
                            value={editingDraft.description}
                            placeholder="Description"
                            onChange={(event) =>
                              setEditingDraft((current) => ({ ...current, description: event.target.value }))
                            }
                          />
                        </td>
                        <td>{item.key}</td>
                        <td>
                          <input
                            type="number"
                            aria-label={`Sort order for ${item.key}`}
                            value={editingDraft.sort_order}
                            onChange={(event) =>
                              setEditingDraft((current) => ({ ...current, sort_order: event.target.value }))
                            }
                          />
                        </td>
                        <td>{item.is_active ? 'Active' : 'Inactive'}</td>
                        <td>
                          <div className="family-status-row-actions">
                            <button
                              type="button"
                              className="form-button"
                              disabled={busy || !editingDraft.label.trim()}
                              onClick={() => updateMutation.mutate(item.key)}
                            >
                              Save
                            </button>
                            <button
                              type="button"
                              className="table-link"
                              disabled={busy}
                              onClick={() => setEditingKey(null)}
                            >
                              Cancel
                            </button>
                          </div>
                        </td>
                      </>
                    ) : (
                      <>
                        <td>
                          <FamilyStatusBadge status={item} />
                          {item.description && (
                            <p className="family-status-description">{item.description}</p>
                          )}
                        </td>
                        <td>{item.key}</td>
                        <td>{item.sort_order}</td>
                        <td>
                          <button
                            type="button"
                            className="table-link"
                            disabled={busy}
                            onClick={() => toggleActiveMutation.mutate(item)}
                          >
                            {item.is_active ? 'Active' : 'Inactive'}
                          </button>
                        </td>
                        <td>
                          <div className="family-status-row-actions">
                            <button
                              type="button"
                              className="table-link"
                              disabled={busy}
                              onClick={() => beginEdit(item)}
                            >
                              Edit
                            </button>
                            <button
                              type="button"
                              className="table-link family-status-delete"
                              disabled={busy}
                              onClick={() => {
                                if (
                                  window.confirm(
                                    `Delete status “${item.label}”? Families using it will lose this status.`,
                                  )
                                ) {
                                  deleteMutation.mutate(item.key);
                                }
                              }}
                            >
                              Delete
                            </button>
                          </div>
                        </td>
                      </>
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

export default AdminFamilyStatusesPage;
