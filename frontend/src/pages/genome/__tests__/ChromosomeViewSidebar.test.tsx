// Pins the chromosome-view sidebar: sample and track checkboxes mirror the parent's state, track
// names come from the caller's labels, and each toggle reports the sample id or the track key.
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ComponentProps } from 'react';
import { describe, expect, it, vi } from 'vitest';

import ChromosomeViewSidebar, { type ChromosomeTrackKey } from '../ChromosomeViewSidebar';
import type { ApiFamilyMember } from '../../../lib/apiTypes';

type Props = ComponentProps<typeof ChromosomeViewSidebar>;

const MEMBERS: ApiFamilyMember[] = [
  { sample_id: 'S1', role: 'proband', affected: true, sex: 'female' },
  { sample_id: 'S2', role: 'father', affected: false, sex: 'male' },
  { sample_id: 'S3', role: 'mother', affected: false, sex: 'female' },
];

// The labels ChromosomeViewPage passes in.
const TRACK_LABELS: Record<ChromosomeTrackKey, string> = {
  coverage: 'Coverage',
  apcad: 'APCAD',
  variants: 'SVs',
  smallVariants: 'Small variants',
  haplotypes: 'Haplotypes',
  phasedMarkers: 'Phased markers',
  repeatExpansions: 'Repeat expansions',
};

const EVERY_TRACK = Object.keys(TRACK_LABELS) as ChromosomeTrackKey[];

const renderSidebar = (overrides: Partial<Props> = {}): Props => {
  const props: Props = {
    members: MEMBERS,
    // S3 is missing from the selection map altogether.
    selected: { S1: true, S2: false },
    availableTracks: EVERY_TRACK,
    trackVisibility: {
      coverage: true,
      apcad: false,
      variants: true,
      smallVariants: false,
      haplotypes: true,
      phasedMarkers: false,
      repeatExpansions: true,
    },
    trackLabels: TRACK_LABELS,
    onToggleSample: vi.fn(),
    onToggleTrack: vi.fn(),
    ...overrides,
  };
  render(<ChromosomeViewSidebar {...props} />);
  return props;
};

const section = (title: string): HTMLElement => {
  const element = screen.getByRole('heading', { name: title }).closest('section');
  if (!element) throw new Error(`no ${title} section`);
  return element;
};

const checkboxLabels = (container: HTMLElement) =>
  within(container)
    .getAllByRole('checkbox')
    .map((box) => box.closest('label')?.textContent);

describe('ChromosomeViewSidebar', () => {
  it('lists one checkbox per sample, ticked from the selection — a sample missing from it is unticked', () => {
    renderSidebar();
    const samples = section('Samples');

    expect(checkboxLabels(samples)).toEqual(['S1', 'S2', 'S3']);
    expect(within(samples).getByRole('checkbox', { name: 'S1' })).toBeChecked();
    expect(within(samples).getByRole('checkbox', { name: 'S2' })).not.toBeChecked();
    expect(within(samples).getByRole('checkbox', { name: 'S3' })).not.toBeChecked();
  });

  it('reports the sample id when a sample is toggled', async () => {
    const user = userEvent.setup();
    const props = renderSidebar();

    await user.click(screen.getByRole('checkbox', { name: 'S2' }));

    expect(vi.mocked(props.onToggleSample).mock.calls).toEqual([['S2']]);
    expect(props.onToggleTrack).not.toHaveBeenCalled();
  });

  it('offers only the available tracks, in the order given, named by the caller’s labels', () => {
    renderSidebar({
      availableTracks: ['phasedMarkers', 'coverage', 'variants'],
      trackLabels: { ...TRACK_LABELS, variants: 'Structural variants' },
    });

    expect(checkboxLabels(section('Tracks'))).toEqual([
      'Phased markers',
      'Coverage',
      'Structural variants',
    ]);
  });

  it('ticks each track from the visibility state', () => {
    renderSidebar();
    const tracks = section('Tracks');

    expect(within(tracks).getByRole('checkbox', { name: 'Coverage' })).toBeChecked();
    expect(within(tracks).getByRole('checkbox', { name: 'APCAD' })).not.toBeChecked();
    expect(within(tracks).getByRole('checkbox', { name: 'SVs' })).toBeChecked();
    expect(within(tracks).getByRole('checkbox', { name: 'Small variants' })).not.toBeChecked();
    expect(within(tracks).getByRole('checkbox', { name: 'Haplotypes' })).toBeChecked();
    expect(within(tracks).getByRole('checkbox', { name: 'Phased markers' })).not.toBeChecked();
    expect(within(tracks).getByRole('checkbox', { name: 'Repeat expansions' })).toBeChecked();
  });

  it('reports the track key, not its label, when a track is toggled', async () => {
    const user = userEvent.setup();
    const props = renderSidebar();

    for (const key of EVERY_TRACK) {
      await user.click(screen.getByRole('checkbox', { name: TRACK_LABELS[key] }));
    }

    expect(vi.mocked(props.onToggleTrack).mock.calls).toEqual(EVERY_TRACK.map((key) => [key]));
    expect(props.onToggleSample).not.toHaveBeenCalled();
  });
});
