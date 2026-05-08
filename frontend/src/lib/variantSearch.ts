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

export type ParsedLocus = ParsedLocusRegion | ParsedLocusGene;

const GENOMIC_REGION_PATTERN =
  /^(?<chrom>(?:chr)?[A-Za-z0-9_]+):(?<start>[0-9,]+)(?:-(?<end>[0-9,]+))?$/i;

export function parseGeneOrRegionInput(rawValue: string): ParsedLocus | null {
  const value = rawValue.trim();
  if (!value) return null;

  const regionMatch = value.match(GENOMIC_REGION_PATTERN);
  if (regionMatch?.groups) {
    const chr = regionMatch.groups.chrom.replace(/^chr/i, '');
    const start = regionMatch.groups.start.replace(/,/g, '');
    const end = (regionMatch.groups.end || regionMatch.groups.start).replace(/,/g, '');
    return { kind: 'region', chr, start, end };
  }

  return { kind: 'gene', gene: value };
}
