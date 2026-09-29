import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import api from '../../lib/api';
import {
  formatStorageBytes,
  type FamilyRawFiles,
  type RawImportFile,
  type RawFileVerifyResult,
} from './dataManagementTypes';
import { apiPath } from '../../lib/apiPath';

interface RawFileProvenanceTableProps {
  familyId: string;
}

const shortHash = (value: string | null): string => {
  if (!value) return '—';
  return value.length > 16 ? `${value.slice(0, 12)}…` : value;
};

const verifyToneClass = (status: RawFileVerifyResult['status']): string => {
  switch (status) {
    case 'verified':
      return 'badge-chip badge-chip--success';
    case 'mismatch':
    case 'missing':
      return 'badge-chip badge-chip--danger';
    default:
      return 'badge-chip';
  }
};

// A file kept in the bucket a package was imported from is not downloaded here, and it
// is not missing either.
const downloadTitle = (file: RawImportFile): string => {
  if (file.in_object_store) return 'Kept in the object store at its storage path; download it from there';
  return file.download_available ? `Download ${file.file_name}` : 'Source file is no longer available';
};

const RawFileProvenanceTable: React.FC<RawFileProvenanceTableProps> = ({ familyId }) => {
  const [verifyResults, setVerifyResults] = useState<Record<string, RawFileVerifyResult>>({});
  const [busyFileId, setBusyFileId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const {
    data,
    isLoading,
    error,
  } = useQuery<FamilyRawFiles>({
    queryKey: ['admin', 'data-inventory', 'family', familyId, 'files'],
    queryFn: async () => {
      const response = await api.get(apiPath`/admin/data/families/${familyId}/files`);
      return response.data as FamilyRawFiles;
    },
    enabled: Boolean(familyId),
    retry: false,
  });

  const downloadFile = async (file: RawImportFile) => {
    setActionError(null);
    setBusyFileId(`download:${file.id}`);
    try {
      const response = await api.get(apiPath`/admin/data/files/${file.id}/download`, {
        responseType: 'blob',
      });
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = file.file_name;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.URL.revokeObjectURL(url);
    } catch {
      setActionError(
        `Could not download ${file.file_name}. The source file may no longer be available.`,
      );
    } finally {
      setBusyFileId(null);
    }
  };

  const verifyFile = async (file: RawImportFile) => {
    setActionError(null);
    setBusyFileId(`verify:${file.id}`);
    try {
      const response = await api.post(apiPath`/admin/data/files/${file.id}/verify`);
      setVerifyResults((current) => ({
        ...current,
        [file.id]: response.data as RawFileVerifyResult,
      }));
    } catch {
      setActionError(`Could not verify ${file.file_name}.`);
    } finally {
      setBusyFileId(null);
    }
  };

  const renderRow = (file: RawImportFile) => {
    const verify = verifyResults[file.id];
    return (
      <tr key={file.id}>
        <td className="admin-raw-file-name">
          <strong>{file.file_name}</strong>
          {file.dataset && <span className="admin-raw-file-dataset">{file.dataset}</span>}
        </td>
        <td>{file.file_type || '—'}</td>
        <td>{file.scope === 'family' ? 'Family-level' : 'Individual-level'}</td>
        <td>{file.sample_id || '—'}</td>
        <td className="admin-raw-file-path" title={file.storage_path}>
          {file.storage_path || '—'}
        </td>
        <td className="admin-count-cell">
          {file.file_size != null ? formatStorageBytes(file.file_size) : '—'}
        </td>
        <td className="admin-raw-file-hash" title={file.sha256 ?? undefined}>
          {shortHash(file.sha256)}
          {verify && (
            <span className={`${verifyToneClass(verify.status)} admin-raw-file-verify-chip`}>
              {verify.status}
            </span>
          )}
        </td>
        <td>
          <div className="admin-track-inline">
            <button
              type="button"
              className="button-secondary admin-track-inline-action"
              disabled={!file.download_available || busyFileId === `download:${file.id}`}
              onClick={() => downloadFile(file)}
              title={downloadTitle(file)}
            >
              {busyFileId === `download:${file.id}` ? 'Downloading…' : 'Download'}
            </button>
            <button
              type="button"
              className="button-secondary admin-track-inline-action"
              disabled={busyFileId === `verify:${file.id}`}
              onClick={() => verifyFile(file)}
              title={
                file.in_object_store
                  ? 'Compare the object in the store with what was recorded about it at import'
                  : 'Recompute SHA-256 and compare to the stored checksum'
              }
            >
              {busyFileId === `verify:${file.id}` ? 'Verifying…' : 'Verify'}
            </button>
          </div>
          {/* A file in the object store is not re-hashed: say what was compared. */}
          {verify && (verify.status !== 'verified' || file.in_object_store) && (
            <p className="admin-raw-file-verify-message">{verify.message}</p>
          )}
        </td>
      </tr>
    );
  };

  const renderTable = (files: RawImportFile[], emptyLabel: string) => (
    <div className="data-table-shell overflow-x-auto">
      <table className="analysis-table admin-raw-file-table">
        <thead>
          <tr>
            <th>File name</th>
            <th>File type</th>
            <th>Scope</th>
            <th>Associated sample</th>
            <th>Storage path</th>
            <th>File size</th>
            <th>SHA-256</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {files.length === 0 ? (
            <tr>
              <td colSpan={8}>
                <p className="table-empty">{emptyLabel}</p>
              </td>
            </tr>
          ) : (
            files.map(renderRow)
          )}
        </tbody>
      </table>
    </div>
  );

  return (
    <section className="space-y-4">
      <div className="page-header">
        <div className="space-y-2">
          <p className="page-kicker">Provenance</p>
          <h3 className="section-title">Raw data provenance</h3>
          <p className="catalog-card-copy">
            Complete traceability of every raw file used to import data for this
            family. Download the original source files and verify their integrity
            against the stored SHA-256 checksum. A file kept in an object store is
            verified against the store&apos;s record of the object instead.
          </p>
        </div>
      </div>

      {actionError && <div className="status-note status-note--error">{actionError}</div>}

      {isLoading ? (
        <p className="table-empty">Loading raw import files…</p>
      ) : error ? (
        <p className="table-empty">Raw file provenance could not be loaded.</p>
      ) : (
        <div className="space-y-6">
          <div className="space-y-2">
            <h4 className="admin-raw-file-group-title">Family-level files</h4>
            <p className="catalog-card-copy">
              Multi-sample VCFs, joint-called VCFs, and family-wide annotation files.
            </p>
            {renderTable(
              data?.family_files ?? [],
              'No family-level source files are recorded for this family.',
            )}
          </div>

          <div className="space-y-2">
            <h4 className="admin-raw-file-group-title">Individual-level files</h4>
            <p className="catalog-card-copy">
              Coverage profiles, BAM/CRAM files, gVCFs, and sample-specific QC files.
            </p>
            {renderTable(
              data?.individual_files ?? [],
              'No individual-level source files are recorded for this family.',
            )}
          </div>
        </div>
      )}
    </section>
  );
};

export default RawFileProvenanceTable;
