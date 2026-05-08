import React from 'react';
import type { ApiFamilyMember } from '../../lib/apiTypes';
import { CHROMS } from './viewerShared';

export interface GenomeTrackVisibility {
  coverage: boolean;
  segments: boolean;
  apcad: boolean;
  sv: boolean;
  haplotypes: boolean;
  repeatExpansions: boolean;
}

export type GenomeTrackKey = keyof GenomeTrackVisibility;

interface GenomeOverviewSidebarProps {
  members: ApiFamilyMember[];
  selected: Record<string, boolean>;
  availableTracks: GenomeTrackKey[];
  trackVisibility: GenomeTrackVisibility;
  chromSelected: Record<string, boolean>;
  onToggleSample: (sampleId: string) => void;
  onToggleTrack: (track: GenomeTrackKey) => void;
  onToggleChrom: (chrom: string) => void;
  onSelectAllChroms: () => void;
  onDeselectAllChroms: () => void;
}

const TRACK_LABELS: Record<GenomeTrackKey, string> = {
  coverage: 'Coverage',
  segments: 'Segments',
  apcad: 'APCAD',
  sv: 'SVs',
  haplotypes: 'Haplotypes',
  repeatExpansions: 'Repeat expansions',
};

const GenomeOverviewSidebar: React.FC<GenomeOverviewSidebarProps> = ({
  members,
  selected,
  availableTracks,
  trackVisibility,
  chromSelected,
  onToggleSample,
  onToggleTrack,
  onToggleChrom,
  onSelectAllChroms,
  onDeselectAllChroms,
}) => (
  <aside className="analysis-sidebar analysis-sidebar--viewer">
    <section className="analysis-panel-muted">
      <h2 className="analysis-section-title">Samples</h2>
      <ul className="mt-3 space-y-2">
        {members.map((member) => (
          <li key={member.sample_id}>
            <label className="analysis-checkbox">
              <input
                type="checkbox"
                checked={selected[member.sample_id] ?? false}
                onChange={() => onToggleSample(member.sample_id)}
              />
              {member.sample_id}
            </label>
          </li>
        ))}
      </ul>
    </section>
    <section className="analysis-panel-muted">
      <h2 className="analysis-section-title">Tracks</h2>
      <ul className="mt-3 space-y-2">
        {availableTracks.map((track) => (
          <li key={track}>
            <label className="analysis-checkbox">
              <input
                type="checkbox"
                checked={trackVisibility[track]}
                onChange={() => onToggleTrack(track)}
              />
              {TRACK_LABELS[track]}
            </label>
          </li>
        ))}
      </ul>
    </section>
    <section className="analysis-panel-muted">
      <h2 className="analysis-section-title">Chromosomes</h2>
      <div className="mt-3 flex gap-2 text-sm">
        <button type="button" onClick={onSelectAllChroms} className="subtle-link">
          Select all
        </button>
        <button type="button" onClick={onDeselectAllChroms} className="subtle-link">
          Deselect all
        </button>
      </div>
      <ul className="mt-3 grid grid-cols-2 gap-y-2 gap-x-2">
        {CHROMS.map((chrom) => (
          <li key={chrom}>
            <label className="analysis-checkbox whitespace-nowrap">
              <input
                type="checkbox"
                checked={chromSelected[chrom]}
                onChange={() => onToggleChrom(chrom)}
              />
              {`chr${chrom}`}
            </label>
          </li>
        ))}
      </ul>
    </section>
  </aside>
);

export default GenomeOverviewSidebar;
