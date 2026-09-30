import { useEffect, useRef, useMemo, type FC } from 'react';
import * as d3 from 'd3';
import { cssVar } from '../../lib/colors';
import {
  collapseBandsForResolution,
  getAcenDirection,
  getBandGradientStops,
} from '../../lib/ideogram';
import { getStainColor } from '../../lib/stainColors';
import { escapeHtml } from '../../lib/escapeHtml';
import { countOf } from '../../lib/countOf';

interface IdeogramBand {
  name: string;
  start: number;
  end: number;
  stain: string;
}

export interface Chromosome {
  chr: string;
  size: number;
  bands: IdeogramBand[];
}

export interface Variant {
  chr: string;
  start: number;
  end?: number;
  type?: string;
  remote_chr?: string;
  remote_start?: number;
}

export const CHROMS = [
  ...Array.from({ length: 22 }, (_, i) => String(i + 1)),
  'X',
  'Y',
];

interface CircosPlotProps {
  chromData: Chromosome[];
  variants?: Variant[];
  /** More SVs than the backend returns at once: none are drawn, and the name says why. */
  tooManyVariants?: boolean;
  selected: Record<string, boolean>;
  onChromosomeClick?: (chr: string) => void;
  onVariantClick?: (v: Variant) => void;
}

const BAND_STROKE = 0.3;
const BAND_FINISH = 'glossy';
export const TELOMERE_CORNER_RADIUS = 5.5;
const CHROMOSOME_GAP = 0.04; // radians of whitespace between adjacent chromosomes
const TELOMERE_END_WHITESPACE = 0.0007;

// The types the legend names (typeColors below), in its order.
const LEGEND_TYPES = ['DEL', 'DUP', 'INS', 'INV', 'BND'];

/**
 * The plot's accessible name: what is drawn now. A variant is counted only when both of
 * its ends sit on a drawn chromosome and it has a type, as in the link layout below.
 * `variants` is undefined until they have loaded (or when they could not), which is
 * never reported as "no structural variants".
 */
const describeCircos = (
  chroms: Chromosome[],
  selected: Record<string, boolean>,
  variants: Variant[] | undefined,
  tooManyVariants = false,
): string => {
  const shown = new Set(chroms.filter((chrom) => selected[chrom.chr]).map((chrom) => chrom.chr));
  if (shown.size === 0) return 'Circos plot: no chromosomes selected';
  const across = countOf(shown.size, 'chromosome');
  if (tooManyVariants) return `Circos plot of ${across}; too many structural variants to draw`;
  if (variants === undefined) return `Circos plot of ${across}; structural variants not loaded`;

  const isShown = (chr?: string) => !!chr && (shown.has(chr) || shown.has(chr.replace(/^chr/i, '')));
  const byType = new Map<string, number>();
  variants.forEach((v) => {
    const type = v.type?.toUpperCase();
    if (!type || !isShown(v.chr) || !isShown(v.remote_chr || v.chr)) return;
    byType.set(type, (byType.get(type) ?? 0) + 1);
  });
  const drawn = [...byType.values()].reduce((sum, count) => sum + count, 0);
  if (drawn === 0) {
    return `Circos plot of ${across}: no structural variants${variants.length ? ' in this selection' : ''}`;
  }
  const other = drawn - LEGEND_TYPES.reduce((sum, type) => sum + (byType.get(type) ?? 0), 0);
  const breakdown = [
    ...LEGEND_TYPES.filter((type) => byType.has(type)).map((type) => `${byType.get(type)} ${type}`),
    ...(other ? [`${other} other`] : []),
  ];
  return `Circos plot of ${countOf(drawn, 'structural variant')} across ${across}: ${breakdown.join(', ')}`;
};

const toCartesianAngle = (angle: number) => angle - Math.PI / 2;

const polarPoint = (radius: number, angle: number) => ({
  x: radius * Math.sin(angle),
  y: -radius * Math.cos(angle),
});

const buildBandSectorPath = (
  innerRadius: number,
  outerRadius: number,
  startAngle: number,
  endAngle: number,
) => {
  const arc = d3
    .arc<d3.DefaultArcObject>()
    .innerRadius(innerRadius)
    .outerRadius(outerRadius)
    .startAngle(startAngle)
    .endAngle(endAngle);
  return arc({} as d3.DefaultArcObject) ?? '';
};

const buildChromosomeOutlinePath = ({
  startAngle,
  endAngle,
  innerRadius,
  outerRadius,
  cornerRadius,
  pAcenStartAngle,
  qAcenEndAngle,
  pinchAngle,
}: {
  startAngle: number;
  endAngle: number;
  innerRadius: number;
  outerRadius: number;
  cornerRadius: number;
  pAcenStartAngle?: number;
  qAcenEndAngle?: number;
  pinchAngle?: number;
}) => {
  const path = d3.path();
  const outerTrim = Math.min(
    cornerRadius / outerRadius,
    Math.max((endAngle - startAngle) / 2 - 0.0001, 0),
  );
  const innerTrim = Math.min(
    cornerRadius / innerRadius,
    Math.max((endAngle - startAngle) / 2 - 0.0001, 0),
  );
  const outerStartAngle = startAngle + outerTrim;
  const outerEndAngle = endAngle - outerTrim;
  const innerStartAngle = startAngle + innerTrim;
  const innerEndAngle = endAngle - innerTrim;
  const startOuterArcPoint = polarPoint(outerRadius, outerStartAngle);
  const endInnerArcPoint = polarPoint(innerRadius, innerEndAngle);
  const sharpStartOuter = polarPoint(outerRadius, startAngle);
  const sharpEndOuter = polarPoint(outerRadius, endAngle);
  const sharpStartInner = polarPoint(innerRadius, startAngle);
  const sharpEndInner = polarPoint(innerRadius, endAngle);
  const startFaceOuterTangent = polarPoint(outerRadius - cornerRadius, startAngle);
  const endFaceInnerTangent = polarPoint(innerRadius + cornerRadius, endAngle);

  path.moveTo(startOuterArcPoint.x, startOuterArcPoint.y);

  if (
    pinchAngle !== undefined &&
    pAcenStartAngle !== undefined &&
    qAcenEndAngle !== undefined
  ) {
    const pOuter = polarPoint(outerRadius, pAcenStartAngle);
    const qOuter = polarPoint(outerRadius, qAcenEndAngle);
    const pinch = polarPoint((innerRadius + outerRadius) / 2, pinchAngle);
    const pInner = polarPoint(innerRadius, pAcenStartAngle);

    path.arc(
      0,
      0,
      outerRadius,
      toCartesianAngle(outerStartAngle),
      toCartesianAngle(pAcenStartAngle),
      false,
    );
    path.lineTo(pOuter.x, pOuter.y);
    path.lineTo(pinch.x, pinch.y);
    path.lineTo(qOuter.x, qOuter.y);
    path.arc(
      0,
      0,
      outerRadius,
      toCartesianAngle(qAcenEndAngle),
      toCartesianAngle(outerEndAngle),
      false,
    );
    path.arcTo(
      sharpEndOuter.x,
      sharpEndOuter.y,
      endFaceInnerTangent.x,
      endFaceInnerTangent.y,
      cornerRadius,
    );
    path.lineTo(endFaceInnerTangent.x, endFaceInnerTangent.y);
    path.arcTo(
      sharpEndInner.x,
      sharpEndInner.y,
      endInnerArcPoint.x,
      endInnerArcPoint.y,
      cornerRadius,
    );
    path.arc(
      0,
      0,
      innerRadius,
      toCartesianAngle(innerEndAngle),
      toCartesianAngle(qAcenEndAngle),
      true,
    );
    path.lineTo(pinch.x, pinch.y);
    path.lineTo(pInner.x, pInner.y);
    path.arc(
      0,
      0,
      innerRadius,
      toCartesianAngle(pAcenStartAngle),
      toCartesianAngle(innerStartAngle),
      true,
    );
  } else {
    path.arc(
      0,
      0,
      outerRadius,
      toCartesianAngle(outerStartAngle),
      toCartesianAngle(outerEndAngle),
      false,
    );
    path.arcTo(
      sharpEndOuter.x,
      sharpEndOuter.y,
      endFaceInnerTangent.x,
      endFaceInnerTangent.y,
      cornerRadius,
    );
    path.lineTo(endFaceInnerTangent.x, endFaceInnerTangent.y);
    path.arcTo(
      sharpEndInner.x,
      sharpEndInner.y,
      endInnerArcPoint.x,
      endInnerArcPoint.y,
      cornerRadius,
    );
    path.arc(
      0,
      0,
      innerRadius,
      toCartesianAngle(innerEndAngle),
      toCartesianAngle(innerStartAngle),
      true,
    );
  }

  path.arcTo(
    sharpStartInner.x,
    sharpStartInner.y,
    startFaceOuterTangent.x,
    startFaceOuterTangent.y,
    cornerRadius,
  );
  path.lineTo(startFaceOuterTangent.x, startFaceOuterTangent.y);
  path.arcTo(
    sharpStartOuter.x,
    sharpStartOuter.y,
    startOuterArcPoint.x,
    startOuterArcPoint.y,
    cornerRadius,
  );
  path.closePath();
  return path.toString();
};

const buildAcenBandPath = ({
  baseAngle,
  tipAngle,
  innerRadius,
  outerRadius,
}: {
  baseAngle: number;
  tipAngle: number;
  innerRadius: number;
  outerRadius: number;
}) => {
  const outer = polarPoint(outerRadius, baseAngle);
  const inner = polarPoint(innerRadius, baseAngle);
  const tip = polarPoint((innerRadius + outerRadius) / 2, tipAngle);
  return `M ${outer.x} ${outer.y} L ${tip.x} ${tip.y} L ${inner.x} ${inner.y} Z`;
};

interface AcenContext {
  pinchAngle: number;
  pBand: IdeogramBand;
  qBand: IdeogramBand;
  pAcenStartAngle: number;
  qAcenEndAngle: number;
}

interface ChromLayout {
  chrom: Chromosome;
  chromAngle: number;
  startAngle: number;
  endAngle: number;
  renderBands: IdeogramBand[];
  scale: d3.ScaleLinear<number, number>;
  acenContext: AcenContext | null;
  outlinePath: string;
}

interface RadialNode {
  angle: number;
  radius: number;
}

interface RadialLink {
  source: RadialNode;
  target: RadialNode;
}

interface VariantRender {
  key: string;
  d: string | null;
  stroke: string;
  strokeWidth: number;
  type: string;
  clickable: boolean;
  variant: Variant;
}

const CircosPlot: FC<CircosPlotProps> = ({
  chromData,
  variants,
  tooManyVariants = false,
  selected,
  onChromosomeClick,
  onVariantClick,
}) => {
  const svgRef = useRef<SVGSVGElement | null>(null);
  // Keep the latest click callbacks in refs so they don't have to be effect deps
  // (a fresh callback identity would otherwise re-run the whole render).
  const onChromosomeClickRef = useRef(onChromosomeClick);
  const onVariantClickRef = useRef(onVariantClick);
  onChromosomeClickRef.current = onChromosomeClick;
  onVariantClickRef.current = onVariantClick;

  const typeColors = useMemo<Record<string, string>>(
    () => ({
      DEL: cssVar('--color-variant-del'),
      DUP: cssVar('--color-variant-dup'),
      INS: cssVar('--color-variant-ins'),
      INV: cssVar('--color-variant-inv'),
      BND: cssVar('--color-variant-bnd'),
    }),
    [],
  );

  const sortedChroms = useMemo<Chromosome[]>(
    () =>
      chromData.map((chrom) => ({
        ...chrom,
        bands: [...chrom.bands].sort((a, b) => a.start - b.start),
      })),
    [chromData],
  );

  const chartLabel = useMemo(
    () => describeCircos(chromData, selected, variants, tooManyVariants),
    [chromData, selected, variants, tooManyVariants],
  );

  useEffect(() => {
    if (!svgRef.current) return;
    const selectedChroms = sortedChroms.filter((d) => selected[d.chr]);
    const svg = d3.select(svgRef.current);

    const width = 560;
    const height = 580;
    const outerRadius = 240;
    const innerRadius = outerRadius - 20;
    const centerRadius = (innerRadius + outerRadius) / 2;
    const centerX = width / 2 + 20;
    const centerY = height / 2;
    const black = cssVar('--color-black');

    svg.attr('width', width).attr('height', height);

    // Persistent containers: keyed joins below mutate only the chromosomes /
    // variants that actually changed instead of tearing the whole SVG down and
    // rebuilding it (which the previous svg.selectAll('*').remove() did on every
    // selection toggle, regenerating hundreds of nodes for every chromosome).
    let root = svg.select<SVGGElement>('g.circos-root');
    if (root.empty()) {
      root = svg
        .append('g')
        .attr('class', 'circos-root')
        .attr('transform', `translate(${centerX},${centerY})`);
      root.append('g').attr('class', 'circos-chromosomes');
      root.append('g').attr('class', 'circos-variants');
    }
    const chromContainer = root.select<SVGGElement>('g.circos-chromosomes');
    const variantContainer = root.select<SVGGElement>('g.circos-variants');

    // Fresh tooltip per run (single cheap node); removed on cleanup.
    d3.select('body').selectAll('.circos-tooltip').remove();
    const tooltip = d3
      .select('body')
      .append('div')
      .attr('class',
        'circos-tooltip pointer-events-none absolute z-10 rounded border bg-bg p-2 text-xs text-text shadow'
      )
      .style('opacity', 0);

    // --- angle layout (unchanged math); also populates angleScales for variants ---
    const gap = CHROMOSOME_GAP; // radians of spacing between chromosomes
    const totalSize = d3.sum(selectedChroms, (d) => d.size);
    const totalGap = gap * selectedChroms.length;
    const scaleFactor = (2 * Math.PI - totalGap) / totalSize;
    const angleScales: Record<string, d3.ScaleLinear<number, number>> = {};
    let currentAngle = 0;

    const layouts: ChromLayout[] = selectedChroms.map((chrom) => {
      const chromAngle = chrom.size * scaleFactor;
      const startAngle = currentAngle;
      const endAngle = startAngle + chromAngle;
      currentAngle += chromAngle + gap;

      const ideogramInset = Math.min(
        TELOMERE_END_WHITESPACE,
        Math.max(chromAngle / 2 - 0.002, 0),
      );
      const visualStartAngle = startAngle + ideogramInset;
      const visualEndAngle = endAngle - ideogramInset;
      const compactBands = collapseBandsForResolution(
        chrom.bands,
        chrom.size,
        chromAngle * centerRadius,
        'compact',
      ).sort((a, b) => a.start - b.start) as IdeogramBand[];
      const renderBands = compactBands.length
        ? compactBands
        : [{ name: chrom.chr, start: 0, end: chrom.size, stain: 'gneg' }];

      const scale = d3
        .scaleLinear()
        .domain([0, chrom.size])
        .range([visualStartAngle, visualEndAngle]);
      angleScales[chrom.chr] = scale;
      const acenBands = renderBands.filter((band) => band.stain === 'acen');
      const sortedAcenBands = [...acenBands].sort((a, b) => a.start - b.start);
      const [firstAcen, secondAcen] = sortedAcenBands;
      const acenContext: AcenContext | null =
        sortedAcenBands.length === 2 &&
        getAcenDirection(firstAcen, chrom.size) === 'p' &&
        getAcenDirection(secondAcen, chrom.size) === 'q'
          ? {
              pinchAngle: scale((firstAcen.end + secondAcen.start) / 2),
              pBand: firstAcen,
              qBand: secondAcen,
              pAcenStartAngle: scale(firstAcen.start),
              qAcenEndAngle: scale(secondAcen.end),
            }
          : null;
      const outlinePath = buildChromosomeOutlinePath({
        startAngle: visualStartAngle,
        endAngle: visualEndAngle,
        innerRadius,
        outerRadius,
        cornerRadius: TELOMERE_CORNER_RADIUS,
        pAcenStartAngle: acenContext?.pAcenStartAngle,
        qAcenEndAngle: acenContext?.qAcenEndAngle,
        pinchAngle: acenContext?.pinchAngle,
      });

      return { chrom, chromAngle, startAngle, endAngle, renderBands, scale, acenContext, outlinePath };
    });

    const renderChromosome = (
      grp: d3.Selection<SVGGElement, ChromLayout, null, undefined>,
      layout: ChromLayout,
    ) => {
      const { chrom, chromAngle, startAngle, endAngle, renderBands, scale, acenContext, outlinePath } =
        layout;
      const defsSel = grp.select<SVGDefsElement>('defs.circos-chromosome-defs');

      grp.select('.circos-chromosome-clip').attr('d', outlinePath);

      // Per-band gradients (keyed by band start position).
      defsSel
        .selectAll<SVGLinearGradientElement, IdeogramBand>('linearGradient.circos-band-gradient')
        .data(renderBands, (band) => String(band.start))
        .join((enter) =>
          enter
            .append('linearGradient')
            .attr('class', 'circos-band-gradient')
            .attr('gradientUnits', 'userSpaceOnUse'),
        )
        .attr('id', (band) => `circos-band-gradient-${chrom.chr}-${band.start}`)
        .attr('x1', (band) => polarPoint(innerRadius, scale((band.start + band.end) / 2)).x)
        .attr('y1', (band) => polarPoint(innerRadius, scale((band.start + band.end) / 2)).y)
        .attr('x2', (band) => polarPoint(outerRadius, scale((band.start + band.end) / 2)).x)
        .attr('y2', (band) => polarPoint(outerRadius, scale((band.start + band.end) / 2)).y)
        .each(function (band) {
          d3.select(this)
            .selectAll<SVGStopElement, ReturnType<typeof getBandGradientStops>[number]>('stop')
            .data(getBandGradientStops(getStainColor(band.stain), BAND_FINISH))
            .join('stop')
            .attr('offset', (stop) => stop.offset)
            .attr('stop-color', (stop) => stop.stopColor)
            .attr('stop-opacity', (stop) => stop.stopOpacity ?? 1);
        });

      const fillGroup = grp.select<SVGGElement>('g.circos-band-fills');
      const boundaryGroup = grp.select<SVGGElement>('g.circos-band-boundaries');

      // Band paths (keyed by band start), branching on the acen special case.
      fillGroup
        .selectAll<SVGPathElement, IdeogramBand>('path.circos-band')
        .data(renderBands, (band) => String(band.start))
        .join((enter) => {
          const path = enter.append('path').attr('class', 'circos-band');
          path.append('title');
          return path;
        })
        .each(function (band) {
          const sel = d3.select(this);
          let dStr: string;
          if (band.stain === 'acen' && acenContext) {
            const isPBand =
              band.start === acenContext.pBand.start && band.end === acenContext.pBand.end;
            dStr = buildAcenBandPath({
              baseAngle: scale(isPBand ? band.start : band.end),
              tipAngle: scale(isPBand ? band.end : band.start),
              innerRadius,
              outerRadius,
            });
            sel
              .attr('class', 'circos-band circos-band--acen')
              .attr('stroke', black)
              .attr('stroke-width', BAND_STROKE)
              .attr('stroke-linejoin', 'round');
          } else {
            dStr = buildBandSectorPath(innerRadius, outerRadius, scale(band.start), scale(band.end));
            sel
              .attr('class', 'circos-band circos-band--sector')
              .attr('stroke', 'none')
              .attr('stroke-width', null)
              .attr('stroke-linejoin', null);
          }
          sel
            .attr('data-chrom', chrom.chr)
            .attr('d', dStr)
            .attr('fill', `url(#circos-band-gradient-${chrom.chr}-${band.start})`);
          sel.select('title').text(band.name);
        });

      // Boundary lines between consecutive non-acen bands (keyed by band start).
      const boundaryData = renderBands
        .slice(1)
        .map((band, index) => ({ band, prev: renderBands[index] }))
        .filter(({ band, prev }) => prev.stain !== 'acen' && band.stain !== 'acen');
      boundaryGroup
        .selectAll<SVGLineElement, { band: IdeogramBand; prev: IdeogramBand }>('line.circos-band-boundary')
        .data(boundaryData, (d) => String(d.band.start))
        .join((enter) =>
          enter
            .append('line')
            .attr('class', 'circos-band-boundary')
            .attr('stroke', black)
            .attr('stroke-width', BAND_STROKE)
            .attr('stroke-linecap', 'round'),
        )
        .attr('data-chrom', chrom.chr)
        .attr('x1', (d) => polarPoint(innerRadius, scale(d.band.start)).x)
        .attr('y1', (d) => polarPoint(innerRadius, scale(d.band.start)).y)
        .attr('x2', (d) => polarPoint(outerRadius, scale(d.band.start)).x)
        .attr('y2', (d) => polarPoint(outerRadius, scale(d.band.start)).y);

      grp.select('path.circos-chromosome-outline').attr('data-chrom', chrom.chr).attr('d', outlinePath);

      // Curved label.
      const labelAngle = (startAngle + endAngle) / 2;
      const labelRadius = outerRadius + 12;
      let labelStartAngle = startAngle;
      let labelEndAngle = endAngle;
      if (labelAngle > Math.PI / 2 && labelAngle < (3 * Math.PI) / 2) {
        labelStartAngle = endAngle;
        labelEndAngle = startAngle;
      }
      const labelArc = d3
        .arc<d3.DefaultArcObject>()
        .innerRadius(labelRadius)
        .outerRadius(labelRadius)
        .startAngle(labelStartAngle)
        .endAngle(labelEndAngle);
      const labelId = `chrom-label-${chrom.chr}`;
      grp
        .select('path.circos-chromosome-label-arc')
        .attr('id', labelId)
        .attr('d', labelArc({} as d3.DefaultArcObject));
      grp
        .select('text.circos-chromosome-label')
        .select('textPath')
        .attr('href', `#${labelId}`)
        .text(chrom.chr);

      // Invisible click target.
      const clickPath = d3.arc()({
        innerRadius,
        outerRadius,
        startAngle,
        endAngle: startAngle + chromAngle,
      });
      grp
        .select('path.circos-chromosome-click')
        .attr('d', clickPath ?? null)
        .style('cursor', onChromosomeClickRef.current ? 'pointer' : 'default')
        .on('click', () => onChromosomeClickRef.current?.(chrom.chr));
    };

    // --- chromosome keyed join: only added/removed chromosomes touch the DOM
    // structure; retained ones have their geometry attributes updated in place ---
    chromContainer
      .selectAll<SVGGElement, ChromLayout>('g.circos-chromosome')
      .data(layouts, (d) => d.chrom.chr)
      .join((enter) => {
        const grp = enter
          .append('g')
          .attr('class', 'circos-chromosome')
          .attr('data-chrom', (d) => d.chrom.chr);
        const enterDefs = grp.append('defs').attr('class', 'circos-chromosome-defs');
        enterDefs
          .append('clipPath')
          .attr('id', (d) => `circos-clip-${d.chrom.chr}`)
          .append('path')
          .attr('class', 'circos-chromosome-clip');
        const bandsGroup = grp
          .append('g')
          .attr('class', 'circos-chromosome-bands')
          .attr('clip-path', (d) => `url(#circos-clip-${d.chrom.chr})`);
        // Separate sub-groups so boundary lines always paint above band fills,
        // regardless of which bands enter/exit when the resolution changes.
        bandsGroup.append('g').attr('class', 'circos-band-fills');
        bandsGroup.append('g').attr('class', 'circos-band-boundaries');
        grp
          .append('path')
          .attr('class', 'circos-chromosome-outline')
          .attr('fill', 'none')
          .attr('stroke', 'none')
          .attr('stroke-width', BAND_STROKE)
          .attr('stroke-linejoin', 'round');
        grp.append('path').attr('class', 'circos-chromosome-label-arc').attr('fill', 'none');
        grp
          .append('text')
          .attr('class', 'circos-chromosome-label')
          .attr('text-anchor', 'middle')
          .attr('fill', 'currentColor')
          .style('font-size', '12px')
          .append('textPath')
          .attr('startOffset', '20%');
        grp.append('path').attr('class', 'circos-chromosome-click').attr('fill', 'transparent');
        return grp;
      })
      .order()
      .each(function (layout) {
        renderChromosome(d3.select(this), layout);
      });

    // --- variant links (computed once, then keyed-joined) ---
    const link = d3
      .linkRadial<RadialLink, RadialNode>()
      .angle((d) => d.angle)
      .radius((d) => d.radius);
    const getScale = (chr?: string) => {
      if (!chr) return undefined;
      return angleScales[chr] || angleScales[chr.replace(/^chr/i, '')];
    };

    const variantRenders: VariantRender[] = (variants ?? [])
      .map((v, index): VariantRender | null => {
        const sourceScale = getScale(v.chr);
        // default to the same chromosome when remote_chr is undefined so
        // intra-chromosomal variants like DEL, DUP and INS are still drawn
        const targetScale = getScale(v.remote_chr || v.chr);
        const sourcePos = v.start;
        const targetPos = v.remote_start ?? v.end ?? v.start;
        const type = v.type?.toUpperCase();
        if (!sourceScale || !targetScale || !type) return null;

        const sourceAngle = sourceScale(sourcePos);
        let targetAngle = targetScale(targetPos);
        const minAngle = 0.015;
        if (Math.abs(targetAngle - sourceAngle) < minAngle) {
          targetAngle =
            targetAngle >= sourceAngle ? sourceAngle + minAngle : sourceAngle - minAngle;
        }
        const isIntrachrom = !v.remote_chr || v.remote_chr === v.chr;

        let strokeWidth = 1;
        let d: string | null = null;
        if (type === 'INS') {
          const insertionOuter = innerRadius - 20;
          const insertionInner = innerRadius - 40;
          d = link({
            source: { angle: sourceAngle, radius: insertionOuter },
            target: { angle: sourceAngle, radius: insertionInner },
          });
        } else if (type === 'INV') {
          const invRadius = innerRadius - 45;
          const midAngle = (sourceAngle + targetAngle) / 2;
          const controlRadius = invRadius * 0.75;
          const p = d3.path();
          p.moveTo(invRadius * Math.sin(sourceAngle), -invRadius * Math.cos(sourceAngle));
          p.quadraticCurveTo(
            controlRadius * Math.sin(midAngle),
            -controlRadius * Math.cos(midAngle),
            invRadius * Math.sin(targetAngle),
            -invRadius * Math.cos(targetAngle),
          );
          d = p.toString();
        } else if (type === 'BND') {
          const radius = innerRadius - 45;
          const midAngle = (sourceAngle + targetAngle) / 2;
          const controlRadius = radius * 0.6;
          const p = d3.path();
          p.moveTo(radius * Math.sin(sourceAngle), -radius * Math.cos(sourceAngle));
          p.quadraticCurveTo(
            controlRadius * Math.sin(midAngle),
            -controlRadius * Math.cos(midAngle),
            radius * Math.sin(targetAngle),
            -radius * Math.cos(targetAngle),
          );
          d = p.toString();
        } else {
          const radius = isIntrachrom ? innerRadius - 10 : innerRadius;
          d = link({
            source: { angle: sourceAngle, radius },
            target: { angle: targetAngle, radius },
          });
          if (type === 'DEL' || type === 'DUP') {
            strokeWidth = 15;
          }
        }

        return {
          key: String(index),
          d,
          stroke: typeColors[type] || cssVar('--color-variant-default'),
          strokeWidth,
          type,
          clickable: type === 'BND',
          variant: v,
        };
      })
      .filter((r): r is VariantRender => r !== null);

    variantContainer
      .selectAll<SVGPathElement, VariantRender>('path.circos-variant')
      .data(variantRenders, (d) => d.key)
      .join((enter) => enter.append('path').attr('class', 'circos-variant').attr('fill', 'none'))
      .order()
      .each(function (render) {
        const sel = d3.select(this);
        const v = render.variant;
        sel
          .attr('d', render.d ?? null)
          .attr('stroke', render.stroke)
          .attr('stroke-width', render.strokeWidth)
          .style('cursor', render.clickable && onVariantClickRef.current ? 'pointer' : 'default')
          .on('mouseover', (event) => {
            const endChr = v.remote_chr || v.chr;
            const endPos = v.remote_start ?? v.end ?? v.start;
            const coords =
              endChr === v.chr && endPos === v.start
                ? `${v.chr}:${v.start}`
                : `${v.chr}:${v.start} → ${endChr}:${endPos}`;
            tooltip
              .style('opacity', 1)
              .style('left', `${event.pageX + 8}px`)
              .style('top', `${event.pageY + 8}px`)
              .html(`<strong>${escapeHtml(render.type)}</strong><br/>${escapeHtml(coords)}`);
            d3.select(event.currentTarget)
              .attr('stroke-width', render.strokeWidth + 2)
              .raise();
          })
          .on('mousemove', (event) => {
            tooltip
              .style('left', `${event.pageX + 8}px`)
              .style('top', `${event.pageY + 8}px`);
          })
          .on('mouseout', (event) => {
            tooltip.style('opacity', 0);
            d3.select(event.currentTarget).attr('stroke-width', render.strokeWidth);
          });
        if (render.clickable) {
          sel.on('click', () => onVariantClickRef.current?.(v));
        } else {
          sel.on('click', null);
        }
      });

    return () => {
      tooltip.remove();
    };
  }, [sortedChroms, selected, variants, typeColors]);

  return (
    <div className="flex flex-col items-center">
      <svg ref={svgRef} className="mx-auto block text-text" role="img" aria-label={chartLabel}></svg>
      <div className="mt-4 flex flex-wrap justify-center gap-4 text-xs">
        {Object.entries(typeColors).map(([type, color]) => (
          <div key={type} className="flex items-center gap-1">
            <span
              className="viz-swatch"
              style={
                type === 'INV'
                  ? { backgroundColor: cssVar('--color-white'), border: `2px solid ${color}` }
                  : { backgroundColor: color }
              }
            ></span>
            <span>{type}</span>
          </div>
        ))}
      </div>
    </div>
  );
};

export default CircosPlot;
