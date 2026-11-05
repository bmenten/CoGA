import type { PanelAppConfidenceCounts, PanelAppPanelCountsOut } from '../../lib/apiSchema.generated';

/** PanelApp's confidence levels by name, highest first. Level 0 (no rating, or removed by an
 * expert review) is none of them, and no import takes it. */
const CONFIDENCE_NAMES: ReadonlyArray<readonly [string, 'green' | 'amber' | 'red']> = [
  ['3', 'green'],
  ['2', 'amber'],
  ['1', 'red'],
];

/** The choices of confidence levels a PanelApp panel can be imported with. Each is a panel of
 * its own in the catalogue, as the backend keys it (`external_selection`: the levels, lowest
 * first). */
export const PANELAPP_IMPORT_CHOICES: ReadonlyArray<{ levels: string[]; label: string }> = [
  { levels: ['3'], label: 'Green' },
  { levels: ['2', '3'], label: 'Green + amber' },
  { levels: ['1', '2', '3'], label: 'Green + amber + red' },
];

/** The levels as the panel's `external_selection` holds them: each once, lowest first. */
export const panelAppSelection = (levels: readonly string[]): string =>
  [...new Set(levels)].sort().join(',');

/** A selection by name, highest level first: '2,3' is 'green + amber'. */
export const panelAppSelectionLabel = (selection: string | null | undefined): string => {
  const levels = new Set((selection ?? '').split(',').map((level) => level.trim()));
  return CONFIDENCE_NAMES.filter(([level]) => levels.has(level))
    .map(([, name]) => name)
    .join(' + ');
};

/** How many of one kind of entity a choice of levels takes. */
export const countAtLevels = (counts: PanelAppConfidenceCounts, levels: readonly string[]): number =>
  CONFIDENCE_NAMES.filter(([level]) => levels.includes(level)).reduce(
    (total, [, name]) => total + counts[name],
    0,
  );

/** Whether an import with these levels and options would take no entity at all (the backend
 * refuses it: "No PanelApp entities matched the selected import options"). */
export const importsNothing = (
  counts: PanelAppPanelCountsOut,
  levels: readonly string[],
  options: { includeRegions: boolean; includeStrs: boolean },
): boolean =>
  countAtLevels(counts.genes, levels) === 0 &&
  (!options.includeRegions || countAtLevels(counts.regions, levels) === 0) &&
  (!options.includeStrs || countAtLevels(counts.strs, levels) === 0);
