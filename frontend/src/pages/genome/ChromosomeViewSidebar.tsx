import React from 'react';
import type { ApiFamilyMember } from '../../lib/apiTypes';

export interface ChromosomeTrackVisibility {
  coverage: boolean;
  apcad: boolean;
  variants: boolean;
  smallVariants: boolean;
  haplotypes: boolean;
  repeatExpansions: boolean;
}

export type ChromosomeTrackKey = keyof ChromosomeTrackVisibility;

interface ChromosomeViewSidebarProps {
  members: ApiFamilyMember[];
  selected: Record<string, boolean>;
  availableTracks: ChromosomeTrackKey[];
  trackVisibility: ChromosomeTrackVisibility;
  trackLabels: Record<ChromosomeTrackKey, string>;
  onToggleSample: (sampleId: string) => void;
  onToggleTrack: (track: ChromosomeTrackKey) => void;
}

const ChromosomeViewSidebar: React.FC<ChromosomeViewSidebarProps> = ({
  members,
  selected,
  availableTracks,
  trackVisibility,
  trackLabels,
  onToggleSample,
  onToggleTrack,
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
              {trackLabels[track]}
            </label>
          </li>
        ))}
      </ul>
    </section>
  </aside>
);

export default ChromosomeViewSidebar;
