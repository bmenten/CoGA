import { compareChromosomes } from '../../lib/chromosomes';
import {
  COMPOUND_HET_PHASE_STATUS_LABELS,
  type SmallVariant,
  type SmallVariantReviewTagMetadata,
  type SmallVariantTagDefinition,
} from './smallVariantSearch';

export type TableSortKey = 'position' | 'gene' | 'impact' | 'priority';

export const SEGREGATION_MODE_LABELS: Record<string, string> = {
  de_novo: 'de novo',
  homozygous_recessive: 'homozygous recessive',
  compound_het: 'compound heterozygous',
  x_linked_recessive: 'X-linked recessive',
  dominant: 'dominant',
};

export const buildPriorityTooltip = (variant: SmallVariant): string => {
  const p = variant.priority;
  if (!p) return '';
  const lines = [
    `Priority ${p.combined_score.toFixed(2)}${p.rank ? ` (rank ${p.rank})` : ''}`,
    `Variant ${p.variant_score.toFixed(2)} — pathogenicity ${p.pathogenicity_score.toFixed(
      2,
    )}, rarity ${p.frequency_score.toFixed(2)}`,
  ];
  if (p.segregation_modes.length) {
    lines.push(
      `Segregation: ${p.segregation_modes
        .map((mode) => SEGREGATION_MODE_LABELS[mode] || mode)
        .join(', ')}`,
    );
  }
  if (typeof p.phenotype_score === 'number') {
    const matches = p.phenotype_matches
      .map((match) => match.label || match.hpo_id)
      .slice(0, 4)
      .join(', ');
    lines.push(
      `Phenotype ${p.phenotype_score.toFixed(2)}${p.phenotype_gene ? ` (${p.phenotype_gene})` : ''}${
        matches ? `: ${matches}` : ''
      }`,
    );
  } else {
    lines.push('Phenotype: no Monarch data for this gene');
  }
  return lines.join('\n');
};

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

// VEP writes the call and its score joined up — `deleterious_low_confidence(0)` — which
// reads as one word. Space the score off and un-snake the term.
export const formatPredictionScore = (value?: string | null): string => {
  const text = (value || '').trim();
  if (!text || text === '-') return '—';
  const match = /^(.*?)\s*\(([^)]*)\)$/.exec(text);
  if (!match) return text.replace(/_/g, ' ');
  return `${match[1].replace(/_/g, ' ')} (${match[2]})`;
};

export const formatLocus = (variant: Pick<SmallVariant, 'chr' | 'start' | 'end'>) => {
  const chr = variant.chr.startsWith('chr') ? variant.chr : `chr${variant.chr}`;
  return `${chr}:${variant.start.toLocaleString()}-${Math.max(
    variant.start,
    variant.end,
  ).toLocaleString()}`;
};

// HGVS genomic notation derived from the VCF-style chr/pos/ref/alt the API returns.
// The reference is named by its UCSC-style chromosome rather than a versioned RefSeq
// accession, which keeps it consistent with the rest of the UI; the assembly is stated
// on the analysis page. Duplications are written as insertions because deciding `dup`
// needs the flanking reference sequence, which the client does not have.
export const formatHgvsG = (
  variant: Pick<SmallVariant, 'chr' | 'start' | 'ref' | 'alt'>,
): string | null => {
  const ref = (variant.ref || '').toUpperCase();
  const alt = (variant.alt || '').toUpperCase();
  if (!/^[ACGTN]+$/.test(ref) || !/^[ACGTN]+$/.test(alt) || ref === alt) return null;
  if (!Number.isFinite(variant.start)) return null;
  const chr = variant.chr.startsWith('chr') ? variant.chr : `chr${variant.chr}`;

  let refCore = ref;
  let altCore = alt;
  // Trim the shared suffix first, then the shared padding base VCF carries on indels.
  while (
    refCore.length > 1 &&
    altCore.length > 1 &&
    refCore[refCore.length - 1] === altCore[altCore.length - 1]
  ) {
    refCore = refCore.slice(0, -1);
    altCore = altCore.slice(0, -1);
  }
  let offset = 0;
  while (offset < refCore.length && offset < altCore.length && refCore[offset] === altCore[offset]) {
    offset += 1;
  }
  const refRest = refCore.slice(offset);
  const altRest = altCore.slice(offset);
  const first = variant.start + offset;
  const last = first + refRest.length - 1;
  const span = refRest.length === 1 ? `${first}` : `${first}_${last}`;

  if (refRest.length === 1 && altRest.length === 1) return `${chr}:g.${first}${refRest}>${altRest}`;
  if (!altRest) return `${chr}:g.${span}del`;
  // A pure insertion sits between the last unchanged base and the next one.
  if (!refRest) return `${chr}:g.${first - 1}_${first}ins${altRest}`;
  return `${chr}:g.${span}delins${altRest}`;
};

export interface VariantIdLink {
  id: string;
  source: string | null;
  href: string | null;
}

// The VCF ID column is not dbSNP-only: annotation pipelines write COSMIC and HGMD
// accessions into it too, comma-separated (rs201819463,COSV53715949). Each is a
// separate record in a separate database, so each gets its own link. Anything
// unrecognised stays plain text rather than being pointed at the wrong database.
export const parseVariantIds = (rsid?: string | null): VariantIdLink[] => {
  // VEP joins co-located ids with `&`; the VCF ID column uses `,`. Both appear in the
  // stored data in roughly equal numbers, so both have to be split on.
  const ids = (rsid || '')
    .split(/[,;&\s]+/)
    .map((value) => value.trim())
    .filter((value) => value && value !== '.');
  const seen = new Set<string>();
  return ids
    .filter((id) => {
      const key = id.toUpperCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .map((id) => {
      if (/^rs\d+$/i.test(id)) {
        return {
          id,
          source: 'dbSNP',
          href: `https://www.ncbi.nlm.nih.gov/snp/${encodeURIComponent(id)}`,
        };
      }
      if (/^COS[VMN]\d+$/i.test(id)) {
        return {
          id,
          source: 'COSMIC',
          href: `https://cancer.sanger.ac.uk/cosmic/search?q=${encodeURIComponent(id)}`,
        };
      }
      // An HGMD accession is a record-set letter followed by a mutation-class letter:
      // M missense/nonsense, D deletion, I insertion, X indel, S splicing, R regulatory,
      // G/N gross lesion, P complex rearrangement. The live data carries C*, H* and B*.
      if (/^[A-Z][DGIMNPRSX]\d+$/i.test(id)) {
        return {
          id,
          source: 'HGMD',
          href: `https://www.hgmd.cf.ac.uk/ac/mut.php?acc=${encodeURIComponent(id)}`,
        };
      }
      return { id, source: null, href: null };
    });
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

export const getReviewClassificationTone = (classification?: string | null) => {
  const value = (classification || '').toLowerCase();
  if (value.includes('pathogenic')) return 'critical';
  if (value.includes('candidate')) return 'accent';
  if (value.includes('vus')) return 'warning';
  if (value.includes('benign')) return 'success';
  if (value.includes('reject')) return 'neutral';
  return 'strong';
};

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

/**
 * Where a second-hit badge goes: the family's SV list, filtered to this gene.
 *
 * The badge summary carries no SV identity — `summarize_second_hit` collapses the
 * matching SVs to counts, types, zygosity and phase — and a gene can be hit by several
 * SVs, so there is no single SV to jump to. The gene-filtered SV list shows all of them
 * with the type, size, frequency and review state needed to judge whether one completes
 * a recessive genotype, and links on to IGV per SV from there.
 */
export const buildSvSecondHitHref = (
  variant: Pick<SmallVariant, 'gene' | 'gene_id' | 'sv_second_hit'>,
  familyId?: string,
  projectId?: string,
) => {
  if (!familyId) return null;
  const hit = variant.sv_second_hit;
  const params = new URLSearchParams();
  // Prefer the SVs' own span. Filtering by gene sends the analyst to an empty page
  // whenever the SV is annotated to a gene it does not positionally overlap — the SV
  // annotation includes flanking genes, the SV search demands a real overlap.
  if (hit?.chr && typeof hit.start === 'number' && typeof hit.end === 'number') {
    params.set('locus', `${hit.chr}:${hit.start}-${hit.end}`);
  } else {
    // Older payloads carry no span: fall back to the gene the SV hit, then the
    // variant's own symbol.
    const geneLabel = (hit?.gene || variant.gene || variant.gene_id || '').trim();
    if (!geneLabel) return null;
    params.set('gene', geneLabel);
  }
  if (projectId) params.set('project_id', projectId);
  return `/families/${familyId}/structural-variants?${params.toString()}`;
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

// "Smart" PubMed search combining the gene/variant with the patient's HPO terms,
// to find literature linking this gene/variant to the observed phenotype.
export const buildLiteraturePubmedHref = ({
  gene,
  proteinChange,
  hpoLabels = [],
}: {
  gene?: string | null;
  proteinChange?: string | null;
  hpoLabels?: string[];
}): string | null => {
  const geneTerm = (gene || '').trim();
  if (!geneTerm) return null;

  // Anchor on the gene (optionally OR'd with the protein change), then AND the
  // phenotype terms together as an OR group of quoted phrases.
  const anchorParts = [geneTerm, (proteinChange || '').trim()].filter(Boolean);
  const anchor = anchorParts.length > 1 ? `(${anchorParts.join(' OR ')})` : anchorParts[0];

  const phenotypes = Array.from(
    new Set(hpoLabels.map((label) => label.trim()).filter(Boolean)),
  ).map((label) => `"${label}"`);
  const phenotypeGroup = phenotypes.length ? ` AND (${phenotypes.join(' OR ')})` : '';

  return `https://pubmed.ncbi.nlm.nih.gov/?term=${encodeURIComponent(`${anchor}${phenotypeGroup}`)}`;
};

export const sortSmallVariants = (
  variants: SmallVariant[],
  tableSortKey: TableSortKey,
  tableSortAsc: boolean,
) => {
  const sorted = [...variants];
  sorted.sort((left, right) => {
    let diff = 0;

    if (tableSortKey === 'priority') {
      // Higher combined score first; variants without a score fall to the bottom.
      const leftScore = left.priority?.combined_score ?? -1;
      const rightScore = right.priority?.combined_score ?? -1;
      diff = rightScore - leftScore;
      if (diff === 0) {
        const leftVar = left.priority?.variant_score ?? -1;
        const rightVar = right.priority?.variant_score ?? -1;
        diff = rightVar - leftVar;
      }
      if (diff === 0) diff = compareChromosomes(left.chr, right.chr);
      if (diff === 0) diff = left.start - right.start;
      // Priority is intrinsically descending; the asc/desc toggle still flips it below.
      return tableSortAsc ? -diff : diff;
    }
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
