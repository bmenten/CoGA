export interface ParsedLocusRegion {
  kind: 'region';
  chr: string;
  start: string;
  end: string;
}

export interface ParsedLocusGene {
  kind: 'gene';
  gene: string;
}

/** A locus that reads as neither a gene nor a region; `problem` says why, for the user. */
export interface ParsedLocusInvalid {
  kind: 'invalid';
  problem: string;
}

export type ParsedLocus = ParsedLocusRegion | ParsedLocusGene | ParsedLocusInvalid;

// chromosome:start[-end]. The dash may be an en dash, as the viewer writes a range.
const GENOMIC_REGION_PATTERN =
  /^(?<chrom>(?:chr)?[A-Za-z0-9_]+):(?<start>[0-9,]+)(?:\s*[-–]\s*(?<end>[0-9,]+))?$/i;
const BED_LIKE_PATTERN = /^(?:chr)?(?:\d{1,2}|X|Y|MT?)\s+[\d,]+(?:\s+[\d,]+)?$/i;

export function parseGeneOrRegionInput(rawValue: string): ParsedLocus | null {
  const value = rawValue.trim();
  if (!value) return null;

  const regionMatch = value.match(GENOMIC_REGION_PATTERN);
  if (regionMatch?.groups) {
    const chr = regionMatch.groups.chrom.replace(/^chr/i, '');
    const start = regionMatch.groups.start.replace(/,/g, '');
    const end = (regionMatch.groups.end || regionMatch.groups.start).replace(/,/g, '');
    if (Number(end) < Number(start)) {
      return { kind: 'invalid', problem: `Location '${value}' ends before it starts.` };
    }
    return { kind: 'region', chr, start, end };
  }

  // No gene symbol has a colon, nor is one a chromosome followed by numbers (a BED line):
  // either is a region that does not parse. Searched as a gene name it matched nothing,
  // and read as a family without variants (#604).
  if (value.includes(':') || BED_LIKE_PATTERN.test(value)) {
    return { kind: 'invalid', problem: `Location '${value}' is not a gene or chr:start-end.` };
  }
  return { kind: 'gene', gene: value };
}

// One entry of an interval list, as the backend reads it: chr:start-end with a hyphen or an
// en dash, or chr:position for one base. A BED line is not read: BED is 0-based.
const INTERVAL_ENTRY_PATTERN =
  /^(?<chr>[^:\s]+)\s*:\s*(?<start>\d[\d,]*)\s*(?:[-–]\s*(?<end>\d[\d,]*)\s*)?$/;

/**
 * The entries of an interval list (one per line, or `;`-separated) that cannot be read,
 * each said as the backend refuses it. The backend skipped them, so the search covered
 * less than the list asked (#604); the form now names them before the search is run.
 */
export function intervalListProblems(rawValue: string, label = 'Interval'): string[] {
  const problems: string[] = [];
  rawValue.split(/[\n;]+/).forEach((rawEntry) => {
    const entry = rawEntry.trim();
    if (!entry) return;
    const match = entry.match(INTERVAL_ENTRY_PATTERN);
    if (!match?.groups) {
      problems.push(`${label} '${entry}' is not chr:start-end.`);
      return;
    }
    const start = Number(match.groups.start.replace(/,/g, ''));
    const end = Number((match.groups.end || match.groups.start).replace(/,/g, ''));
    if (end < start) problems.push(`${label} '${entry}' ends before it starts.`);
  });
  return problems;
}
