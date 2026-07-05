import React from 'react';
import { useQuery } from '@tanstack/react-query';
import * as d3 from 'd3';
import api from '../../lib/api';
import type { ApiVariantPage } from '../../lib/apiTypes';
import { hasAltAllele } from '../../lib/genotypes';
import { cssVar } from '../../lib/colors';
import {
  SMALL_VARIANT_TRACK_RESULT_LIMIT,
  TRACK_DOT_RADIUS,
  shouldShowSmallVariantDetails,
} from '../../lib/trackSampling';
import VizLoadingOverlay from './VizLoadingOverlay';
import VizTooltip from './VizTooltip';

interface Genotype {
  sample: string;
  gt: string;
}

interface SmallVariantReview {
  tags?: string[];
}

interface Variant {
  chr: string;
  start: number;
  end: number;
  type: string;
  ref?: string;
  alt?: string;
  gene?: string | null;
  gene_id?: string | null;
  hgvsc?: string | null;
  hgvsp?: string | null;
  impact?: string | null;
  clinvar?: string | null;
  genotypes?: Genotype[];
  review?: SmallVariantReview | null;
}

interface TagDefinition {
  key: string;
  color?: string | null;
}

interface Props {
  familyId: string;
  sampleId: string;
  chrom: string;
  regionStart: number;
  regionEnd: number;
  width: number;
  height: number;
  filters?: Record<string, string>;
  /**
   * When set (the displayed sample is a child with a parent in the family), the
   * track splits into three rows by parental origin: paternal (top), undetermined
   * (middle), maternal (bottom). Pass the father's / mother's sample id.
   */
  paternalSampleId?: string | null;
  maternalSampleId?: string | null;
}

const NON_REFERENCE_GENOTYPES = ['0/1', '1/0', '0|1', '1|0', '1/1', '1|1'];

const samplePresenceFilter = (sampleId: string) =>
  `${sampleId}:${NON_REFERENCE_GENOTYPES.join('|')}`;

const hasActiveFilterValue = (value: unknown): boolean => {
  if (Array.isArray(value)) return value.some(hasActiveFilterValue);
  return String(value ?? '').trim().length > 0;
};

// Functional-impact colours (raw VEP/SnpEff IMPACT). Tuned for contrast against
// the light track background; ClinVar benign/pathogenic override these (see
// getVariantColor). Kept as literal hex so the component renders without the
// theme stylesheet (and stays unit-testable).
const IMPACT_COLORS = {
  high: '#fb923c', // orange — HIGH
  medium: '#4ade80', // green — MODERATE / MEDIUM
  low: '#9ca3af', // gray — LOW / MODIFIER / unknown
} as const;

// ClinVar overrides: only benign and pathogenic categories recolour the dot.
const CLINVAR_OVERRIDE_COLORS = {
  pathogenic: '#dc2626', // red — pathogenic / likely pathogenic
  benign: '#60a5fa', // blue — benign / likely benign
} as const;

const normalizeClinvar = (clinvar?: string | null): string =>
  (clinvar || '')
    .toLowerCase()
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();

// Light blue for benign/likely-benign, red for pathogenic/likely-pathogenic;
// every other ClinVar value (uncertain, conflicting, risk factor, …) returns
// undefined so the variant falls through to its functional-impact colour.
const getClinvarOverrideColor = (clinvar?: string | null): string | undefined => {
  const value = normalizeClinvar(clinvar);
  if (!value) return undefined;
  // "conflicting interpretations of pathogenicity" must not read as pathogenic.
  if (value.includes('conflicting')) return undefined;
  if (value.includes('pathogenic')) return CLINVAR_OVERRIDE_COLORS.pathogenic;
  if (value.includes('benign')) return CLINVAR_OVERRIDE_COLORS.benign;
  return undefined;
};

const getImpactColor = (impact?: string | null): string => {
  switch ((impact || '').toUpperCase()) {
    case 'HIGH':
      return IMPACT_COLORS.high;
    case 'MODERATE':
    case 'MEDIUM':
      return IMPACT_COLORS.medium;
    default:
      // LOW, MODIFIER, or unannotated.
      return IMPACT_COLORS.low;
  }
};

type ParentalOrigin = 'paternal' | 'maternal' | 'unknown';

const ORIGIN_LABEL: Record<ParentalOrigin, string> = {
  paternal: 'Paternal (hap1)',
  maternal: 'Maternal (hap2)',
  unknown: 'Undetermined origin',
};

const REF_OR_MISSING = new Set(['0', '.', '']);
const isAltAllele = (allele: string): boolean => !REF_OR_MISSING.has(allele);
const carriesAlt = (gt?: string | null): boolean =>
  !!gt && gt.split(/[|/]/).some(isAltAllele);

const parseAlleles = (
  gt?: string | null,
): { hap1: string; hap2: string; phased: boolean } | null => {
  if (!gt) return null;
  const phased = gt.includes('|');
  const parts = gt.split(/[|/]/);
  if (parts.length < 2) return null;
  return { hap1: parts[0], hap2: parts[1], phased };
};

/**
 * Parental origin of a child's ALT allele.
 *
 * 1. When both parents are present, Mendelian transmission is authoritative: if
 *    exactly one parent carries the ALT, the child got it from that parent;
 *    otherwise (both carry → ambiguous, neither → de novo) it is undetermined.
 * 2. When both parents are NOT available, fall back to the child's phased
 *    haplotype order (hap1 = paternal, hap2 = maternal) — the haplotype track's
 *    convention.
 * 3. Homozygous-ALT (on both homologs) is always undetermined.
 */
const variantOrigin = (
  childGt?: string | null,
  fatherGt?: string | null,
  motherGt?: string | null,
): ParentalOrigin => {
  const child = parseAlleles(childGt);
  if (!child) return 'unknown';
  const hap1Alt = isAltAllele(child.hap1);
  const hap2Alt = isAltAllele(child.hap2);
  // On both homologs (hom-alt) or neither (shouldn't occur — filtered): no side.
  if (hap1Alt === hap2Alt) return 'unknown';

  if (fatherGt && motherGt) {
    const fatherAlt = carriesAlt(fatherGt);
    const motherAlt = carriesAlt(motherGt);
    if (fatherAlt && !motherAlt) return 'paternal';
    if (motherAlt && !fatherAlt) return 'maternal';
    // Both carry (ambiguous) or neither (de novo): Mendelian can't attribute it.
    return 'unknown';
  }

  // Only one parent (or none) available — use phase orientation if we have it.
  if (child.phased) {
    if (hap1Alt) return 'paternal';
    if (hap2Alt) return 'maternal';
  }

  return 'unknown';
};

type PositionedVariant = Variant & { x: number; origin: ParentalOrigin };

const SmallVariantTrack: React.FC<Props> = ({
  familyId,
  sampleId,
  chrom,
  regionStart,
  regionEnd,
  width,
  height,
  filters,
  paternalSampleId,
  maternalSampleId,
}) => {
  const pageSize = SMALL_VARIANT_TRACK_RESULT_LIMIT - 1;
  const hasUserFilters = React.useMemo(
    () => Object.values(filters || {}).some(hasActiveFilterValue),
    [filters],
  );
  const regionRestricted = React.useMemo(
    () => shouldShowSmallVariantDetails(regionEnd - regionStart),
    [regionEnd, regionStart],
  );
  const canRequestSmallVariants =
    regionEnd > regionStart && (hasUserFilters || regionRestricted);
  const requestFilters = React.useMemo(() => {
    const nextFilters = { ...(filters || {}) };
    if (!nextFilters.sample_filter) {
      nextFilters.sample_filter = samplePresenceFilter(sampleId);
    }
    return nextFilters;
  }, [filters, sampleId]);
  const { data, isLoading } = useQuery<ApiVariantPage<Variant>>({
    queryKey: [
      'small-variants-track',
      familyId,
      sampleId,
      chrom,
      regionStart,
      regionEnd,
      pageSize,
      requestFilters,
    ],
    queryFn: async () => {
      const params: Record<string, any> = {
        chr: chrom,
        start: regionStart,
        end: regionEnd,
        overlap: true,
        page_size: pageSize,
        track_mode: true,
        track_result_limit: SMALL_VARIANT_TRACK_RESULT_LIMIT,
        ...requestFilters,
      };
      const res = await api.get(`/families/${familyId}/small-variants`, {
        params,
      });
      return res.data as ApiVariantPage<Variant>;
    },
    enabled: canRequestSmallVariants,
  });
  const { data: tagDefinitions = [] } = useQuery<TagDefinition[]>({
    queryKey: ['small-variant-track-tags', familyId],
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}/small-variant-tags`);
      return res.data as TagDefinition[];
    },
    enabled: canRequestSmallVariants,
  });

  const tooManyVariants =
    !canRequestSmallVariants ||
    Boolean(
      data &&
        (data.total_is_estimated ||
          data.total >= SMALL_VARIANT_TRACK_RESULT_LIMIT ||
          (data.count_limit != null && data.total >= data.count_limit)),
    );
  const tagColorMap = React.useMemo(() => {
    if (!Array.isArray(tagDefinitions)) return {};
    return Object.fromEntries(
      tagDefinitions
        .filter((tag) => tag.key && tag.color)
        .map((tag) => [tag.key, tag.color as string]),
    );
  }, [tagDefinitions]);

  const variants = React.useMemo(
    () =>
      (tooManyVariants ? [] : data?.variants || []).filter((v) =>
        v.genotypes?.some(
          (g) => g.sample === sampleId && hasAltAllele(g.gt)
        )
      ),
    [data?.variants, sampleId, tooManyVariants]
  );

  // Three lanes (paternal / undetermined / maternal) only when the displayed
  // sample is a child with a parent available; otherwise a single centred lane.
  const originMode = Boolean(paternalSampleId || maternalSampleId);

  const span = regionEnd - regionStart || 1;
  const withPos = React.useMemo<PositionedVariant[]>(
    () =>
      variants.map((v) => {
        const x = ((v.start - regionStart) / span) * width;
        const gtFor = (sample?: string | null) =>
          sample ? v.genotypes?.find((g) => g.sample === sample)?.gt : undefined;
        const origin = originMode
          ? variantOrigin(gtFor(sampleId), gtFor(paternalSampleId), gtFor(maternalSampleId))
          : 'unknown';
        return { ...v, x, origin };
      }),
    [variants, regionStart, span, width, originMode, sampleId, paternalSampleId, maternalSampleId]
  );

  const getVariantColor = React.useCallback(
    (variant: Variant) => {
      const tagColor = variant.review?.tags
        ?.map((tagKey) => tagColorMap[tagKey])
        .find(Boolean);
      if (tagColor) return tagColor;
      return getClinvarOverrideColor(variant.clinvar) ?? getImpactColor(variant.impact);
    },
    [tagColorMap],
  );
  const emptyMessage = tooManyVariants
    ? 'Too many variants to display. Zoom in or apply filters.'
    : 'no small variants for this region / sample';

  const svgRef = React.useRef<SVGSVGElement | null>(null);
  const [tooltip, setTooltip] = React.useState<{
    x: number;
    y: number;
    variant: PositionedVariant;
  } | null>(null);

  React.useEffect(() => {
    if (isLoading) {
      return;
    }

    const svg = d3.select(svgRef.current);
    svg.selectAll('*').remove();

    if (withPos.length === 0) {
      svg
        .append('text')
        .attr('x', 4)
        .attr('y', height / 2 + 4)
        .attr('font-size', 12)
        .attr('fill', cssVar('--color-variant-default'))
        .text(emptyMessage);
      return;
    }

    const g = svg.append('g');
    const rowCount = originMode ? 3 : 1;
    const rowHeight = height / rowCount;
    // Shared with the coverage / APCAD tracks so all per-point dots match, but
    // never larger than half a row.
    const radius = Math.min(TRACK_DOT_RADIUS, rowHeight / 2 - 1);
    const cyForOrigin = (origin: ParentalOrigin): number => {
      if (!originMode) return height / 2;
      const row = origin === 'paternal' ? 0 : origin === 'maternal' ? 2 : 1;
      return row * rowHeight + rowHeight / 2;
    };

    // Faint dividers between the three parental-origin lanes.
    if (originMode) {
      [rowHeight, rowHeight * 2].forEach((y) => {
        g.append('line')
          .attr('x1', 0)
          .attr('x2', width)
          .attr('y1', y)
          .attr('y2', y)
          .attr('stroke', cssVar('--color-grid'))
          .attr('stroke-width', 1);
      });
    }

    // One data-join for all dots instead of an append per variant.
    g.selectAll<SVGCircleElement, PositionedVariant>('circle')
      .data(withPos)
      .join('circle')
      .attr('cx', (v) => v.x)
      .attr('cy', (v) => cyForOrigin(v.origin))
      .attr('r', radius)
      .attr('fill', (v) => getVariantColor(v));

    // A single delegated hit layer + quadtree replaces the per-variant transparent
    // hitbox rects (up to ~N extra DOM nodes at the 10k cap). On hover we look up
    // the nearest dot within a small radius instead of relying on N rects.
    const hitRadius = Math.max(8, radius + 6);
    const locator = d3
      .quadtree<PositionedVariant>()
      .x((v) => v.x)
      .y((v) => cyForOrigin(v.origin))
      .addAll(withPos);
    g.append('rect')
      .attr('x', 0)
      .attr('y', 0)
      .attr('width', width)
      .attr('height', height)
      .attr('fill', 'transparent')
      .attr('pointer-events', 'all')
      .style('cursor', 'pointer')
      .on('mousemove', (event: MouseEvent) => {
        const bounds = svgRef.current?.getBoundingClientRect();
        const localX = event.clientX - (bounds?.left ?? 0);
        const localY = event.clientY - (bounds?.top ?? 0);
        const found = locator.find(localX, localY, hitRadius);
        if (found) {
          setTooltip({ x: event.clientX, y: event.clientY, variant: found });
        } else {
          setTooltip(null);
        }
      })
      .on('mouseout', () => setTooltip(null));
  }, [withPos, height, originMode, width, getVariantColor, emptyMessage, isLoading]);

  return (
    <div className="relative" style={{ width, height }}>
      <svg ref={svgRef} width={width} height={height} />
      {isLoading && <VizLoadingOverlay message="Loading small variants" />}
      {tooltip && (
        <VizTooltip x={tooltip.x} y={tooltip.y}>
          <div>{tooltip.variant.gene || tooltip.variant.gene_id || 'Intergenic variant'}</div>
          <div>
            {tooltip.variant.chr}:{tooltip.variant.start}
            {tooltip.variant.ref || tooltip.variant.alt
              ? ` ${tooltip.variant.ref ?? ''}>${tooltip.variant.alt ?? ''}`
              : ''}{' '}
            {tooltip.variant.type}
          </div>
          {tooltip.variant.hgvsc ? <div>{tooltip.variant.hgvsc}</div> : null}
          {tooltip.variant.hgvsp ? <div>{tooltip.variant.hgvsp}</div> : null}
          {tooltip.variant.impact ? <div>Impact: {tooltip.variant.impact}</div> : null}
          {tooltip.variant.clinvar ? <div>ClinVar: {tooltip.variant.clinvar}</div> : null}
          {originMode ? <div>{ORIGIN_LABEL[tooltip.variant.origin]}</div> : null}
        </VizTooltip>
      )}
    </div>
  );
};

export default SmallVariantTrack;
