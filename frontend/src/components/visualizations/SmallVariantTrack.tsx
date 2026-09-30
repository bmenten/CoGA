import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { useSameSpanFallbackData } from '../../lib/useSameSpanFallbackData';
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
import VizErrorOverlay from './VizErrorOverlay';
import VizTooltip from './VizTooltip';
import { NO_REGION_IN_VIEW, describeTrackRegion, hasRegionInView } from './trackRegion';
import { apiPath } from '../../lib/apiPath';
import {
  SMALL_VARIANT_MARKS,
  markDrawRank,
  smallVariantMarkExtent,
  smallVariantMarkKind,
  smallVariantMarkPath,
  type SmallVariantMarkKind,
} from '../../lib/smallVariantMarks';

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
  label?: string | null;
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

// Any call with an ALT allele — the backend reads these as genotype classes, so haploid
// and multi-allelic calls count as present too (#511).
const samplePresenceFilter = (sampleId: string) => `${sampleId}:het|hom`;

const hasActiveFilterValue = (value: unknown): boolean => {
  if (Array.isArray(value)) return value.some(hasActiveFilterValue);
  return String(value ?? '').trim().length > 0;
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

/**
 * The drawn variants in words, for the chart's accessible name (#529): how many, and how
 * many carry the salient marks. Counted by mark, as the legend reads them: a ClinVar P/LP
 * variant of HIGH impact is drawn, and counted, as P/LP.
 */
const describeMarks = (variants: Variant[]): string => {
  const counts: Record<SmallVariantMarkKind, number> = {
    pathogenic: 0,
    high: 0,
    moderate: 0,
    benign: 0,
    other: 0,
  };
  variants.forEach((variant) => {
    counts[smallVariantMarkKind(variant)] += 1;
  });
  const salient = (['pathogenic', 'high'] as const)
    .filter((kind) => counts[kind] > 0)
    .map((kind) => `${counts[kind].toLocaleString()} ${SMALL_VARIANT_MARKS[kind].label}`);
  const detail = salient.length
    ? `of which ${salient.join(' and ')}`
    : `none ${SMALL_VARIANT_MARKS.pathogenic.label} or ${SMALL_VARIANT_MARKS.high.label}`;
  return `${variants.length.toLocaleString()}, ${detail}`;
};

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
  const regionInView = hasRegionInView(regionStart, regionEnd);
  const canRequestSmallVariants = regionInView && (hasUserFilters || regionRestricted);
  const requestFilters = React.useMemo(() => {
    const nextFilters = { ...(filters || {}) };
    if (!nextFilters.sample_filter) {
      nextFilters.sample_filter = samplePresenceFilter(sampleId);
    }
    return nextFilters;
  }, [filters, sampleId]);
  const { data: rawData, isLoading, isError, refetch } = useQuery<ApiVariantPage<Variant>>({
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
      const params: Record<string, unknown> = {
        chr: chrom,
        start: regionStart,
        end: regionEnd,
        overlap: true,
        page_size: pageSize,
        track_mode: true,
        track_result_limit: SMALL_VARIANT_TRACK_RESULT_LIMIT,
        ...requestFilters,
      };
      const res = await api.get(apiPath`/families/${familyId}/small-variants`, {
        params,
      });
      return res.data as ApiVariantPage<Variant>;
    },
    enabled: canRequestSmallVariants,
  });
  const data = useSameSpanFallbackData(
    rawData,
    (regionEnd ?? 0) - (regionStart ?? 0),
    `${familyId}|${sampleId}|${chrom}`,
    isError,
  );
  const { data: tagDefinitions = [] } = useQuery<TagDefinition[]>({
    queryKey: ['small-variant-track-tags', familyId],
    queryFn: async () => {
      const res = await api.get(apiPath`/families/${familyId}/small-variant-tags`);
      return res.data as TagDefinition[];
    },
    enabled: canRequestSmallVariants,
  });

  // A view with no width asks for nothing; that is not a view with too many (#602).
  const tooManyVariants =
    (regionInView && !canRequestSmallVariants) ||
    Boolean(
      data &&
        (data.total_is_estimated ||
          data.total >= SMALL_VARIANT_TRACK_RESULT_LIMIT ||
          (data.count_limit != null && data.total >= data.count_limit)),
    );
  const tagByKey = React.useMemo(() => {
    const byKey = new Map<string, TagDefinition>();
    if (Array.isArray(tagDefinitions)) {
      tagDefinitions.forEach((tag) => {
        if (tag.key) byKey.set(tag.key, tag);
      });
    }
    return byKey;
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

  // A review tag rings the mark in the tag's colour; the mark keeps its own class (#529).
  const getVariantTagColor = React.useCallback(
    (variant: Variant): string | undefined =>
      variant.review?.tags?.map((tagKey) => tagByKey.get(tagKey)?.color).find(Boolean) ?? undefined,
    [tagByKey],
  );
  const emptyMessage = !regionInView
    ? 'No region in view'
    : tooManyVariants
      ? 'Too many variants to display. Zoom in or apply filters.'
      : 'no small variants for this region / sample';

  // The chart's accessible name (#529): what it shows now. A failure, a load or a view
  // over the cap is said as such, never as zero variants (#510).
  const markSummary = React.useMemo(() => describeMarks(variants), [variants]);
  const chartRegion = describeTrackRegion(chrom, regionStart, regionEnd);
  const chartState = isError
    ? 'failed to load'
    : !regionInView
      ? NO_REGION_IN_VIEW
      : isLoading
        ? 'loading'
        : tooManyVariants
          ? 'too many to display; zoom in or apply filters'
          : variants.length === 0
            ? 'none'
            : markSummary;
  const chartLabel = `Small variants of ${sampleId} on ${chartRegion}: ${chartState}`;

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

    // A failed request draws nothing: the error overlay says so, and the empty-state text
    // below must never stand in for a failure (#510).
    if (isError) {
      return;
    }

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

    // One data-join per layer instead of an append per variant. The salient marks are
    // drawn last, so a dense run of low-impact dots cannot cover a pathogenic one.
    const marked = withPos
      .map((v) => ({ v, kind: smallVariantMarkKind(v), tagColor: getVariantTagColor(v) }))
      .sort((a, b) => markDrawRank(a.kind) - markDrawRank(b.kind));
    g.selectAll<SVGCircleElement, (typeof marked)[number]>('circle.small-variant-tag-ring')
      .data(marked.filter((entry) => entry.tagColor))
      .join('circle')
      .attr('class', 'small-variant-tag-ring')
      .attr('data-variant-tag-ring', (entry) => entry.v.start)
      .attr('cx', (entry) => entry.v.x)
      .attr('cy', (entry) => cyForOrigin(entry.v.origin))
      .attr('r', (entry) => smallVariantMarkExtent(entry.kind, radius) + 1.8)
      .attr('fill', 'none')
      .attr('stroke', (entry) => entry.tagColor as string)
      .attr('stroke-width', 1.2);
    g.selectAll<SVGPathElement, (typeof marked)[number]>('path.small-variant-mark')
      .data(marked)
      .join('path')
      .attr('class', 'small-variant-mark')
      .attr('data-variant-mark', (entry) => entry.kind)
      .attr('transform', (entry) => `translate(${entry.v.x},${cyForOrigin(entry.v.origin)})`)
      .attr('d', (entry) => smallVariantMarkPath(entry.kind, radius))
      .attr('fill', (entry) =>
        SMALL_VARIANT_MARKS[entry.kind].hollow ? 'white' : SMALL_VARIANT_MARKS[entry.kind].color,
      )
      .attr('stroke', (entry) =>
        SMALL_VARIANT_MARKS[entry.kind].hollow ? SMALL_VARIANT_MARKS[entry.kind].color : 'none',
      )
      .attr('stroke-width', (entry) => (SMALL_VARIANT_MARKS[entry.kind].hollow ? 1 : 0));

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
  }, [withPos, height, originMode, width, getVariantTagColor, emptyMessage, isLoading, isError]);

  return (
    <div className="relative" style={{ width, height }}>
      <svg ref={svgRef} width={width} height={height} role="img" aria-label={chartLabel} />
      {isLoading && <VizLoadingOverlay message="Loading small variants" />}
      {isError && <VizErrorOverlay what="small variants" onRetry={() => void refetch()} />}
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
          {tooltip.variant.review?.tags?.length ? (
            <div>
              Tags:{' '}
              {tooltip.variant.review.tags
                .map((tagKey) => tagByKey.get(tagKey)?.label || tagKey)
                .join(', ')}
            </div>
          ) : null}
          {originMode ? <div>{ORIGIN_LABEL[tooltip.variant.origin]}</div> : null}
        </VizTooltip>
      )}
    </div>
  );
};

export default SmallVariantTrack;
