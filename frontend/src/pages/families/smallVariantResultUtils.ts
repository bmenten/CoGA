import { compareChromosomes } from '../../lib/chromosomes';
import {
  COMPOUND_HET_PHASE_STATUS_LABELS,
  type SmallVariant,
  type SmallVariantReviewTagMetadata,
  type SmallVariantTagDefinition,
} from './smallVariantSearch';

export type TableSortKey = 'position' | 'gene' | 'impact';

export const formatFrequency = (value?: number) => {
  if (typeof value !== 'number' || Number.isNaN(value)) return '—';
  if (value === 0) return '0';
  if (value < 0.001) return value.toExponential(1);
  return value.toFixed(4);
};

export const formatScore = (value?: number, digits = 2) => {
  if (typeof value !== 'number' || Number.isNaN(value)) return '—';
  return value.toFixed(digits);
};

export const formatTokenLabel = (value?: string) => {
  if (!value) return '—';
  return value.replace(/_/g, ' ');
};

export const formatLocus = (variant: Pick<SmallVariant, 'chr' | 'start' | 'end'>) => {
  const chr = variant.chr.startsWith('chr') ? variant.chr : `chr${variant.chr}`;
  return `${chr}:${variant.start.toLocaleString()}-${Math.max(
    variant.start,
    variant.end,
  ).toLocaleString()}`;
};

export const getImpactTone = (impact?: string) => {
  switch ((impact || '').toUpperCase()) {
    case 'HIGH':
      return 'critical';
    case 'MODERATE':
    case 'MEDIUM':
      return 'strong';
    case 'LOW':
      return 'soft';
    default:
      return 'neutral';
  }
};

export const getImpactRank = (impact?: string) => {
  switch ((impact || '').toUpperCase()) {
    case 'HIGH':
      return 0;
    case 'MODERATE':
    case 'MEDIUM':
      return 1;
    case 'LOW':
      return 2;
    case 'MODIFIER':
      return 3;
    default:
      return 4;
  }
};

export const getClinvarTone = (clinvar?: string) => {
  const value = (clinvar || '').toLowerCase();
  if (
    value === 'pathogenic' ||
    value.includes('pathogenic/likely pathogenic') ||
    value.includes('likely pathogenic') ||
    value.includes('likely_pathogenic')
  ) {
    return 'critical';
  }
  if (value.includes('risk')) return 'warning';
  if (
    value === 'benign' ||
    value.includes('likely benign') ||
    value.includes('likely_benign') ||
    value.includes('benign/likely benign')
  ) {
    return 'success';
  }
  return 'neutral';
};

export const getClinvarHighlightTone = (clinvar?: string) => {
  const value = (clinvar || '').toLowerCase();
  if (
    value === 'pathogenic' ||
    value.includes('pathogenic/likely pathogenic') ||
    value.includes('likely pathogenic') ||
    value.includes('likely_pathogenic')
  ) {
    return 'pathogenic';
  }
  if (
    value === 'benign' ||
    value.includes('likely benign') ||
    value.includes('likely_benign') ||
    value.includes('benign/likely benign')
  ) {
    return 'benign';
  }
  return 'neutral';
};

export const getFrequencyTone = (value?: number) => {
  if (typeof value !== 'number' || Number.isNaN(value)) return 'neutral';
  if (value === 0 || value <= 0.0001) return 'success';
  if (value <= 0.001) return 'strong';
  if (value <= 0.01) return 'warning';
  return 'neutral';
};

export const getReviewClassificationTone = (classification?: string | null) => {
  const value = (classification || '').toLowerCase();
  if (value.includes('pathogenic')) return 'critical';
  if (value.includes('candidate')) return 'accent';
  if (value.includes('vus')) return 'warning';
  if (value.includes('benign')) return 'success';
  if (value.includes('reject')) return 'neutral';
  return 'strong';
};

export const getReviewTagTone = (
  tagKey: string,
  tagMap?: Record<string, SmallVariantTagDefinition>,
) => (tagMap?.[tagKey]?.group === 'classification' ? 'strong' : 'neutral');

const DEFAULT_TAG_COLOR = '#5b6b79';

const hexToRgb = (hex: string) => {
  const value = hex.replace('#', '');
  if (value.length !== 6) return { r: 91, g: 107, b: 121 };
  return {
    r: Number.parseInt(value.slice(0, 2), 16),
    g: Number.parseInt(value.slice(2, 4), 16),
    b: Number.parseInt(value.slice(4, 6), 16),
  };
};

const toRgba = (hex: string, alpha: number) => {
  const { r, g, b } = hexToRgb(hex);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
};

const getReadableTextColor = (hex: string) => {
  const { r, g, b } = hexToRgb(hex);
  const luminance = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
  return luminance > 0.62 ? '#183046' : hex;
};

export const getReviewTagStyle = (
  tagKey: string,
  tagMap?: Record<string, SmallVariantTagDefinition>,
) => {
  const color = tagMap?.[tagKey]?.color || DEFAULT_TAG_COLOR;
  return {
    borderColor: toRgba(color, 0.28),
    background: toRgba(color, 0.14),
    color: getReadableTextColor(color),
  };
};

export const sortReviewTagKeys = (
  tagKeys: Iterable<string>,
  tagMap?: Record<string, SmallVariantTagDefinition>,
) =>
  [...tagKeys].sort((left, right) => {
    const leftTag = tagMap?.[left];
    const rightTag = tagMap?.[right];
    const groupOrder = ['collaboration', 'classification', 'custom'];
    const leftGroup = groupOrder.indexOf(leftTag?.group || 'custom');
    const rightGroup = groupOrder.indexOf(rightTag?.group || 'custom');
    if (leftGroup !== rightGroup) return leftGroup - rightGroup;
    const leftOrder = leftTag?.sort_order ?? 500;
    const rightOrder = rightTag?.sort_order ?? 500;
    if (leftOrder !== rightOrder) return leftOrder - rightOrder;
    return (leftTag?.label || left).localeCompare(rightTag?.label || right, undefined, {
      sensitivity: 'base',
    });
  });

export const buildReviewTagTooltip = ({
  tagKey,
  tagMap,
  tagMetadata,
}: {
  tagKey: string;
  tagMap?: Record<string, SmallVariantTagDefinition>;
  tagMetadata?: Record<string, SmallVariantReviewTagMetadata>;
}) => {
  const tagLabel = tagMap?.[tagKey]?.label || tagKey;
  const metadata = tagMetadata?.[tagKey];
  if (!metadata?.updated_by && !metadata?.updated_at) {
    return tagLabel;
  }
  const timestamp = formatReviewTimestamp(metadata.updated_at || null);
  return `${tagLabel} · ${metadata.updated_by || 'Unknown'}${
    timestamp ? ` · ${timestamp}` : ''
  }`;
};

export const formatReviewTimestamp = (value?: string | null) => {
  if (!value) return null;
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) return null;
  return timestamp.toLocaleString();
};

export const formatCompoundHetPhaseStatus = (value?: string | null) => {
  if (!value) return 'Phase unknown';
  return COMPOUND_HET_PHASE_STATUS_LABELS[value] || value.replace(/_/g, ' ');
};

export const getUcscDb = (
  speciesName?: string,
  assemblyName?: string,
  assemblyVersion?: string,
) => {
  const species = `${speciesName || ''}`.toLowerCase();
  const assembly = `${assemblyName || ''} ${assemblyVersion || ''}`.toLowerCase();

  if (species.includes('mouse') || species.includes('mus musculus')) {
    if (assembly.includes('grcm39') || assembly.includes('mm39')) return 'mm39';
    if (assembly.includes('grcm38') || assembly.includes('mm10')) return 'mm10';
  }

  if (species.includes('human') || species.includes('homo sapiens') || !species) {
    if (assembly.includes('grch38') || assembly.includes('hg38')) return 'hg38';
    if (assembly.includes('grch37') || assembly.includes('hg19')) return 'hg19';
    if (assembly.includes('chm13') || assembly.includes('hs1')) return 'hs1';
  }

  return null;
};

export const getGnomadDataset = (
  speciesName?: string,
  assemblyName?: string,
  assemblyVersion?: string,
) => {
  const species = `${speciesName || ''}`.toLowerCase();
  const assembly = `${assemblyName || ''} ${assemblyVersion || ''}`.toLowerCase();
  if (!(species.includes('human') || species.includes('homo sapiens') || !species)) return null;
  if (assembly.includes('grch38') || assembly.includes('hg38')) return 'gnomad_r4';
  if (assembly.includes('grch37') || assembly.includes('hg19')) return 'gnomad_r2_1';
  return null;
};

export const buildSmallVariantNavigation = ({
  variant,
  familyId,
  locationSearch,
  projectId,
}: {
  variant: SmallVariant;
  familyId?: string;
  locationSearch: string;
  projectId?: string;
}) => {
  const chr = variant.chr.startsWith('chr') ? variant.chr : `chr${variant.chr}`;
  const locus = `${chr}:${Math.max(1, variant.start)}-${Math.max(variant.start, variant.end)}`;
  const backSearch = locationSearch.startsWith('?') ? locationSearch.slice(1) : locationSearch;
  const igvBackPath = backSearch
    ? `/families/${familyId}/small-variants?${backSearch}`
    : `/families/${familyId}/small-variants`;
  const igvHref = `/families/${familyId}/igv?locus=${encodeURIComponent(locus)}${
    projectId ? `&project_id=${projectId}` : ''
  }${backSearch ? `&back=${encodeURIComponent(backSearch)}` : ''}&back_path=${encodeURIComponent(
    igvBackPath,
  )}`;
  const viewHref = (() => {
    const chrom = variant.chr.replace(/^chr/, '');
    const start = Math.max(0, variant.start - 1_000_000);
    const end = variant.end + 1_000_000;
    const params = new URLSearchParams(locationSearch);
    params.set('start', String(start));
    params.set('end', String(end));
    params.set('origin', 'small');
    if (projectId) params.set('project_id', projectId);
    return `/families/${familyId}/chromosome/${chrom}?${params.toString()}`;
  })();

  return { igvHref, locus, viewHref };
};

export const buildSmallVariantGeneInfoHref = (
  variant: SmallVariant,
  familyId?: string,
  projectId?: string,
) => {
  const geneLabel = (variant.gene || variant.gene_id || '').trim();
  if (!geneLabel) return null;
  const params = new URLSearchParams({ gene: geneLabel });
  if (familyId) params.set('family_id', familyId);
  if (projectId) params.set('project_id', projectId);
  return `/genes?${params.toString()}`;
};

export const buildSmallVariantExternalLinks = ({
  variant,
  speciesName,
  assemblyName,
  assemblyVersion,
}: {
  variant: SmallVariant;
  speciesName?: string;
  assemblyName?: string;
  assemblyVersion?: string;
}) => {
  const locusLabel = formatLocus(variant);
  const geneLabel = variant.gene || variant.gene_id;
  const proteinLabel = variant.hgvsp || variant.hgvsc || variant.rsid;
  const querySeed = [geneLabel, proteinLabel].filter(Boolean).join(' ');
  const variantId =
    variant.ref && variant.alt
      ? `${variant.chr.replace(/^chr/, '')}-${variant.start}-${variant.ref}-${variant.alt}`
      : null;
  const gnomadDataset = getGnomadDataset(speciesName, assemblyName, assemblyVersion);
  const gnomadHref = variantId
    ? `https://gnomad.broadinstitute.org/variant/${encodeURIComponent(variantId)}${
        gnomadDataset ? `?dataset=${gnomadDataset}` : ''
      }`
    : null;
  const clinvarTerm = variant.rsid || variant.hgvsc || querySeed || locusLabel;
  const pubmedTerm = querySeed || locusLabel;
  const ucscDb = getUcscDb(speciesName, assemblyName, assemblyVersion);
  const ucscPosition = encodeURIComponent(
    `${variant.chr.startsWith('chr') ? variant.chr : `chr${variant.chr}`}:${variant.start}-${Math.max(
      variant.start,
      variant.end,
    )}`,
  );

  return [
    gnomadHref ? { label: 'gnomAD', href: gnomadHref } : null,
    {
      label: 'ClinVar',
      href: `https://www.ncbi.nlm.nih.gov/clinvar/?term=${encodeURIComponent(clinvarTerm)}`,
    },
    {
      label: 'PubMed',
      href: `https://pubmed.ncbi.nlm.nih.gov/?term=${encodeURIComponent(pubmedTerm)}`,
    },
    geneLabel
      ? {
          label: 'OMIM',
          href: `https://www.omim.org/search?search=${encodeURIComponent(geneLabel)}`,
        }
      : null,
    variant.gene
      ? {
          label: 'DECIPHER',
          href: `https://www.deciphergenomics.org/gene/${encodeURIComponent(variant.gene)}`,
        }
      : null,
    ucscDb
      ? {
          label: 'UCSC',
          href: `https://genome.ucsc.edu/cgi-bin/hgTracks?db=${encodeURIComponent(
            ucscDb,
          )}&position=${ucscPosition}`,
        }
      : null,
  ].filter((link): link is { label: string; href: string } => Boolean(link));
};

export const sortSmallVariants = (
  variants: SmallVariant[],
  tableSortKey: TableSortKey,
  tableSortAsc: boolean,
) => {
  const sorted = [...variants];
  sorted.sort((left, right) => {
    let diff = 0;

    if (tableSortKey === 'position') {
      diff = compareChromosomes(left.chr, right.chr);
      if (diff === 0) diff = left.start - right.start;
      if (diff === 0) diff = left.end - right.end;
      if (diff === 0) {
        diff = (left.gene || '').localeCompare(right.gene || '', undefined, {
          sensitivity: 'base',
          numeric: true,
        });
      }
    } else if (tableSortKey === 'gene') {
      diff = (left.gene || '').localeCompare(right.gene || '', undefined, {
        sensitivity: 'base',
        numeric: true,
      });
      if (diff === 0) diff = compareChromosomes(left.chr, right.chr);
      if (diff === 0) diff = left.start - right.start;
    } else if (tableSortKey === 'impact') {
      diff = getImpactRank(left.impact) - getImpactRank(right.impact);
      if (diff === 0) {
        diff = (left.gene || '').localeCompare(right.gene || '', undefined, {
          sensitivity: 'base',
          numeric: true,
        });
      }
      if (diff === 0) diff = compareChromosomes(left.chr, right.chr);
      if (diff === 0) diff = left.start - right.start;
    }

    return tableSortAsc ? diff : -diff;
  });

  return sorted;
};
