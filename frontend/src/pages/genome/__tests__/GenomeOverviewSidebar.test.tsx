// Pins the genome-overview sidebar: its sample, track and chromosome checkboxes mirror the
// parent's state, and each toggle reports the sample id, the track key or the bare chromosome.
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ComponentProps } from 'react';
import { describe, expect, it, vi } from 'vitest';

import GenomeOverviewSidebar, { type GenomeTrackKey } from '../GenomeOverviewSidebar';
import { CHROMS } from '../viewerShared';
import type { ApiFamilyMember } from '../../../lib/apiTypes';

type Props = ComponentProps<typeof GenomeOverviewSidebar>;

const MEMBERS: ApiFamilyMember[] = [
  { sample_id: 'S1', role: 'proband', affected: true, sex: 'male' },
  { sample_id: 'S2', role: 'mother', affected: false, sex: 'female' },
  { sample_id: 'S3', role: 'father', affected: false, sex: 'male' },
];

// Every track key with the label the sidebar gives it.
const TRACKS: Array<[key: GenomeTrackKey, label: string]> = [
  ['coverage', 'Coverage'],
  ['segments', 'Segments'],
  ['apcad', 'APCAD'],
  ['sv', 'SVs'],
  ['haplotypes', 'Haplotypes'],
  ['repeatExpansions', 'Repeat expansions'],
];

const allChroms = (on: boolean): Record<string, boolean> =>
  Object.fromEntries(CHROMS.map((chrom) => [chrom, on]));

const renderSidebar = (overrides: Partial<Props> = {}): Props => {
  const props: Props = {
    members: MEMBERS,
    // S3 is missing from the selection map altogether.
    selected: { S1: true, S2: false },
    availableTracks: TRACKS.map(([key]) => key),
    trackVisibility: {
      coverage: true,
      segments: false,
      apcad: true,
      sv: false,
      haplotypes: true,
      repeatExpansions: false,
    },
    chromSelected: { ...allChroms(true), Y: false, MT: false },
    onToggleSample: vi.fn(),
    onToggleTrack: vi.fn(),
    onToggleChrom: vi.fn(),
    onSelectAllChroms: vi.fn(),
    onDeselectAllChroms: vi.fn(),
    ...overrides,
  };
  render(<GenomeOverviewSidebar {...props} />);
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

describe('GenomeOverviewSidebar', () => {
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

    await user.click(screen.getByRole('checkbox', { name: 'S3' }));

    expect(vi.mocked(props.onToggleSample).mock.calls).toEqual([['S3']]);
    expect(props.onToggleTrack).not.toHaveBeenCalled();
    expect(props.onToggleChrom).not.toHaveBeenCalled();
  });

  it('offers only the tracks the family has data for, in the order given', () => {
    renderSidebar({ availableTracks: ['sv', 'coverage', 'repeatExpansions'] });

    expect(checkboxLabels(section('Tracks'))).toEqual(['SVs', 'Coverage', 'Repeat expansions']);
  });

  it('ticks each track from the visibility state', () => {
    renderSidebar();
    const tracks = section('Tracks');

    expect(within(tracks).getByRole('checkbox', { name: 'Coverage' })).toBeChecked();
    expect(within(tracks).getByRole('checkbox', { name: 'Segments' })).not.toBeChecked();
    expect(within(tracks).getByRole('checkbox', { name: 'APCAD' })).toBeChecked();
    expect(within(tracks).getByRole('checkbox', { name: 'SVs' })).not.toBeChecked();
    expect(within(tracks).getByRole('checkbox', { name: 'Haplotypes' })).toBeChecked();
    expect(within(tracks).getByRole('checkbox', { name: 'Repeat expansions' })).not.toBeChecked();
  });

  it('reports the track key, not its label, when a track is toggled', async () => {
    const user = userEvent.setup();
    const props = renderSidebar();

    for (const [, label] of TRACKS) {
      await user.click(screen.getByRole('checkbox', { name: label }));
    }

    expect(vi.mocked(props.onToggleTrack).mock.calls).toEqual(TRACKS.map(([key]) => [key]));
    expect(props.onToggleSample).not.toHaveBeenCalled();
  });

  it('lists chr1–chr22, chrX, chrY and chrMT, ticked from the chromosome selection', () => {
    renderSidebar();
    const chromosomes = section('Chromosomes');

    expect(checkboxLabels(chromosomes)).toEqual([
      ...Array.from({ length: 22 }, (_, index) => `chr${index + 1}`),
      'chrX',
      'chrY',
      'chrMT',
    ]);
    expect(within(chromosomes).getByRole('checkbox', { name: 'chr1' })).toBeChecked();
    expect(within(chromosomes).getByRole('checkbox', { name: 'chrX' })).toBeChecked();
    expect(within(chromosomes).getByRole('checkbox', { name: 'chrY' })).not.toBeChecked();
    expect(within(chromosomes).getByRole('checkbox', { name: 'chrMT' })).not.toBeChecked();
  });

  it('reports the bare chromosome name, without the chr prefix, when one is toggled', async () => {
    const user = userEvent.setup();
    const props = renderSidebar();

    await user.click(screen.getByRole('checkbox', { name: 'chr7' }));
    await user.click(screen.getByRole('checkbox', { name: 'chrX' }));
    await user.click(screen.getByRole('checkbox', { name: 'chrMT' }));

    expect(vi.mocked(props.onToggleChrom).mock.calls).toEqual([['7'], ['X'], ['MT']]);
  });

  it('selects or clears every chromosome through its own callback, not per-chromosome toggles', async () => {
    const user = userEvent.setup();
    const props = renderSidebar();

    await user.click(screen.getByRole('button', { name: 'Select all' }));
    expect(props.onSelectAllChroms).toHaveBeenCalledTimes(1);
    expect(props.onDeselectAllChroms).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: 'Deselect all' }));
    expect(props.onDeselectAllChroms).toHaveBeenCalledTimes(1);
    expect(props.onSelectAllChroms).toHaveBeenCalledTimes(1);
    expect(props.onToggleChrom).not.toHaveBeenCalled();
  });
});
