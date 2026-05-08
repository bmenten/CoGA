import { useState, type FC } from 'react';
import api from '../../lib/api';

const PedUpload: FC = () => {
  const [file, setFile] = useState<File | null>(null);
  const [status, setStatus] = useState<string>('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file) return;
    const formData = new FormData();
    formData.append('file', file);
    setStatus('');
    setLoading(true);
    try {
      await api.post('/ped/upload', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      setStatus('Upload successful');
    } catch (err: any) {
      if (err.response?.status === 409) {
        const overwrite = window.confirm(
          'Families already exist in database. Overwrite?'
        );
        if (overwrite) {
          try {
            await api.post('/ped/upload?overwrite=true', formData, {
              headers: { 'Content-Type': 'multipart/form-data' },
            });
            setStatus('Upload successful');
          } catch {
            setStatus('Upload failed');
          }
        } else {
          setStatus('Upload cancelled');
        }
      } else {
        setStatus('Upload failed');
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="page-shell-narrow">
      <div className="surface-card page-top-card space-y-5">
        <div className="space-y-2">
          <p className="page-kicker">Upload</p>
          <h1 className="section-title">Upload PED File</h1>
          <p className="section-copy">
            Import an existing PED file when the family structure is already defined.
          </p>
        </div>
        <form onSubmit={handleSubmit} className="field-grid">
          <label className="field-label">
            PED file
            <input
              type="file"
              accept=".ped"
              onChange={(e) => setFile(e.target.files?.[0] || null)}
            />
          </label>
          <button type="submit" className="w-full justify-center" disabled={loading}>
            Upload
          </button>
        </form>
      {loading && (
          <div className="loading-spinner" />
      )}
        {status && <p className="form-status text-center">{status}</p>}
      </div>
    </div>
  );
};

export default PedUpload;
