import React, { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import SmallVariantFilterPresetTable from '../../components/SmallVariantFilterPresetTable';
import api from '../../lib/api';
import { getErrorMessage } from '../../lib/errorMessage';
import {
  getChromosomeWindow,
  getCoverageLowerThreshold,
  getCoverageRange,
  getCoverageUpperThreshold,
  getGenomeWindow,
  setChromosomeWindow,
  setCoverageLowerThreshold,
  setCoverageRange,
  setCoverageUpperThreshold,
  setGenomeWindow,
} from '../../lib/settings';
import type { SmallVariantFilterPreset } from '../families/smallVariantSearch';

const SettingsPage: React.FC = () => {
  const queryClient = useQueryClient();
  const [genomeWindow, setGenomeWindowState] = useState<number>(getGenomeWindow());
  const [chromosomeWindow, setChromosomeWindowState] = useState<number>(getChromosomeWindow());
  const [coverageUpper, setCoverageUpperState] = useState<number>(getCoverageUpperThreshold());
  const [coverageLower, setCoverageLowerState] = useState<number>(getCoverageLowerThreshold());
  const [coverageRange, setCoverageRangeState] = useState<number>(getCoverageRange());
  const [status, setStatus] = useState('');
  const [presetStatus, setPresetStatus] = useState<{
    tone: 'error' | 'success';
    message: string;
  } | null>(null);

  useEffect(() => {
    setGenomeWindowState(getGenomeWindow());
    setChromosomeWindowState(getChromosomeWindow());
    setCoverageUpperState(getCoverageUpperThreshold());
    setCoverageLowerState(getCoverageLowerThreshold());
    setCoverageRangeState(getCoverageRange());
  }, []);

  const { data: presets = [] } = useQuery<SmallVariantFilterPreset[]>({
    queryKey: ['auth', 'small-variant-filter-presets'],
    queryFn: async () => {
      const response = await api.get('/auth/small-variant-filter-presets');
      return response.data as SmallVariantFilterPreset[];
    },
  });

  const deletePresetMutation = useMutation({
    mutationFn: async (preset: SmallVariantFilterPreset) => {
      await api.delete(`/auth/small-variant-filter-presets/${preset._id}`);
      return preset._id;
    },
    onSuccess: async () => {
      setPresetStatus({ tone: 'success', message: 'Saved filter removed.' });
      await queryClient.invalidateQueries({
        queryKey: ['auth', 'small-variant-filter-presets'],
      });
    },
    onError: (error) => {
      setPresetStatus({
        tone: 'error',
        message: getErrorMessage(error, 'Unable to remove this saved filter'),
      });
    },
  });

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    setGenomeWindow(genomeWindow);
    setChromosomeWindow(chromosomeWindow);
    setCoverageUpperThreshold(coverageUpper);
    setCoverageLowerThreshold(coverageLower);
    setCoverageRange(coverageRange);
    setStatus('Settings saved');
    setTimeout(() => setStatus(''), 3000);
  };

  return (
    <div className="page-shell-narrow space-y-6">
      <section className="surface-card page-top-card">
        <div className="space-y-2">
          <p className="page-kicker">Preferences</p>
          <h1 className="catalog-card-title">User settings</h1>
        </div>
        <form onSubmit={handleSubmit} className="field-grid mt-6">
          <label className="field-label">
            Genome view window size (bp)
            <input
              type="number"
              value={genomeWindow}
              min={1}
              onChange={(event) => setGenomeWindowState(Number(event.target.value))}
            />
          </label>
          <label className="field-label">
            Chromosome view window size (bp)
            <input
              type="number"
              value={chromosomeWindow}
              min={1}
              onChange={(event) => setChromosomeWindowState(Number(event.target.value))}
            />
          </label>
          <label className="field-label">
            Coverage upper threshold
            <input
              type="number"
              step="any"
              value={coverageUpper}
              onChange={(event) => setCoverageUpperState(Number(event.target.value))}
            />
          </label>
          <label className="field-label">
            Coverage lower threshold
            <input
              type="number"
              step="any"
              value={coverageLower}
              onChange={(event) => setCoverageLowerState(Number(event.target.value))}
            />
          </label>
          <label className="field-label">
            Coverage range (±)
            <input
              type="number"
              step="any"
              value={coverageRange}
              onChange={(event) => setCoverageRangeState(Number(event.target.value))}
            />
          </label>
          <button type="submit">Save</button>
        </form>
        {status ? <p className="form-status">{status}</p> : null}
      </section>

      <section className="surface-card space-y-4">
        <div className="space-y-2">
          <h2 className="section-title">Saved small-variant filters</h2>
          <p className="section-copy">
            Reusable searches are managed here. Filters saved from the small-variant page appear in
            this list.
          </p>
        </div>
        {presetStatus ? (
          <div className={`variant-workspace-feedback variant-workspace-feedback--${presetStatus.tone}`}>
            {presetStatus.message}
          </div>
        ) : null}
        <SmallVariantFilterPresetTable
          presets={presets}
          emptyMessage="No saved filters yet."
          deletingPresetId={deletePresetMutation.variables?._id || null}
          onDeletePreset={async (preset) => {
            await deletePresetMutation.mutateAsync(preset);
          }}
        />
      </section>
    </div>
  );
};

export default SettingsPage;
