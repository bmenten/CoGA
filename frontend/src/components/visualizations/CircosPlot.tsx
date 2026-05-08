import { useEffect, useRef, useMemo, type FC } from 'react';
import * as d3 from 'd3';
import { cssVar } from '../../lib/colors';
import {
  collapseBandsForResolution,
  getAcenDirection,
  getBandGradientStops,
} from '../../lib/ideogram';
import { getStainColor } from '../../lib/stainColors';

export interface IdeogramBand {
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
  selected: Record<string, boolean>;
  onChromosomeClick?: (chr: string) => void;
  onVariantClick?: (v: Variant) => void;
}

const BAND_STROKE = 0.7;
const BAND_FINISH = 'glossy';
const TELOMERE_CORNER_RADIUS = 4.5;
const CHROMOSOME_GAP = 0.022;
const TELOMERE_END_WHITESPACE = 0.0007;

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

const CircosPlot: FC<CircosPlotProps> = ({
  chromData,
  variants,
  selected,
  onChromosomeClick,
  onVariantClick,
}) => {
  const svgRef = useRef<SVGSVGElement | null>(null);
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

  const sortedChroms = useMemo(
    () =>
      chromData.map((chrom) => ({
        ...chrom,
        bands: [...chrom.bands].sort((a, b) => a.start - b.start),
      })),
    [chromData],
  );

  useEffect(() => {
    const selectedChroms = sortedChroms.filter((d) => selected[d.chr]);
    const svg = d3.select(svgRef.current);
    svg.selectAll('*').remove();

    // remove any existing tooltips from previous renders
    d3.select('body').selectAll('.circos-tooltip').remove();
    const tooltip = d3
      .select('body')
      .append('div')
      .attr('class',
        'circos-tooltip pointer-events-none absolute z-10 rounded border bg-bg p-2 text-xs text-text shadow'
      )
      .style('opacity', 0);

    const width = 560;
    const height = 580;
    const outerRadius = 240;
    const innerRadius = outerRadius - 20;
    const centerRadius = (innerRadius + outerRadius) / 2;
    const centerX = width / 2 + 20;
    const centerY = height / 2;
    const g = svg
      .attr('width', width)
      .attr('height', height)
      .append('g')
      .attr('transform', `translate(${centerX},${centerY})`);
    const defs = svg.append('defs');

    const gap = CHROMOSOME_GAP; // radians of spacing between chromosomes
    const totalSize = d3.sum(selectedChroms, (d) => d.size);
    const totalGap = gap * selectedChroms.length;
    const scaleFactor = (2 * Math.PI - totalGap) / totalSize;
    let currentAngle = 0;
    const angleScales: Record<string, d3.ScaleLinear<number, number>> = {};
    const black = cssVar('--color-black');

    selectedChroms.forEach((chrom) => {
      const chromAngle = chrom.size * scaleFactor;
      const startAngle = currentAngle;
      const endAngle = startAngle + chromAngle;
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

      angleScales[chrom.chr] = d3
        .scaleLinear()
        .domain([0, chrom.size])
        .range([visualStartAngle, visualEndAngle]);
      const acenBands = renderBands.filter((band) => band.stain === 'acen');
      const sortedAcenBands = [...acenBands].sort((a, b) => a.start - b.start);
      const [firstAcen, secondAcen] = sortedAcenBands;
      const acenContext =
        sortedAcenBands.length === 2 &&
        getAcenDirection(firstAcen, chrom.size) === 'p' &&
        getAcenDirection(secondAcen, chrom.size) === 'q'
          ? {
              pinchAngle: angleScales[chrom.chr]((firstAcen.end + secondAcen.start) / 2),
              pBand: firstAcen,
              qBand: secondAcen,
              pAcenStartAngle: angleScales[chrom.chr](firstAcen.start),
              qAcenEndAngle: angleScales[chrom.chr](secondAcen.end),
            }
          : null;
      const clipId = `circos-clip-${chrom.chr}`;
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

      defs
        .append('clipPath')
        .attr('id', clipId)
        .append('path')
        .attr('class', 'circos-chromosome-clip')
        .attr('d', outlinePath);

      const bandGroup = g
        .append('g')
        .attr('class', 'circos-chromosome-bands')
        .attr('data-chrom', chrom.chr)
        .attr('clip-path', `url(#${clipId})`);

      renderBands.forEach((band, index) => {
        const bandMidAngle = angleScales[chrom.chr]((band.start + band.end) / 2);
        const innerPoint = polarPoint(innerRadius, bandMidAngle);
        const outerPoint = polarPoint(outerRadius, bandMidAngle);
        const gradientId = `circos-band-gradient-${chrom.chr}-${index}`;
        const gradient = defs
          .append('linearGradient')
          .attr('id', gradientId)
          .attr('class', 'circos-band-gradient')
          .attr('gradientUnits', 'userSpaceOnUse')
          .attr('x1', innerPoint.x)
          .attr('y1', innerPoint.y)
          .attr('x2', outerPoint.x)
          .attr('y2', outerPoint.y);

        getBandGradientStops(getStainColor(band.stain), BAND_FINISH).forEach((stop) => {
          gradient
            .append('stop')
            .attr('offset', stop.offset)
            .attr('stop-color', stop.stopColor)
            .attr('stop-opacity', stop.stopOpacity ?? 1);
        });

        if (band.stain === 'acen' && acenContext) {
          const isPBand = band.start === acenContext.pBand.start && band.end === acenContext.pBand.end;
          const acenPath = buildAcenBandPath({
            baseAngle: angleScales[chrom.chr](isPBand ? band.start : band.end),
            tipAngle: angleScales[chrom.chr](isPBand ? band.end : band.start),
            innerRadius,
            outerRadius,
          });

          bandGroup
            .append('path')
            .attr('class', 'circos-band circos-band--acen')
            .attr('data-chrom', chrom.chr)
            .attr('d', acenPath)
            .attr('fill', `url(#${gradientId})`)
            .attr('stroke', black)
            .attr('stroke-width', BAND_STROKE)
            .attr('stroke-linejoin', 'round')
            .append('title')
            .text(band.name);
          return;
        }

        const bandPath = buildBandSectorPath(
          innerRadius,
          outerRadius,
          angleScales[chrom.chr](band.start),
          angleScales[chrom.chr](band.end),
        );

        bandGroup
          .append('path')
          .attr('class', 'circos-band circos-band--sector')
          .attr('data-chrom', chrom.chr)
          .attr('d', bandPath)
          .attr('fill', `url(#${gradientId})`)
          .attr('stroke', 'none')
          .append('title')
          .text(band.name);
      });

      renderBands.slice(1).forEach((band, index) => {
        const previousBand = renderBands[index];
        if (previousBand.stain === 'acen' || band.stain === 'acen') {
          return;
        }

        const boundaryAngle = angleScales[chrom.chr](band.start);
        const boundaryInner = polarPoint(innerRadius, boundaryAngle);
        const boundaryOuter = polarPoint(outerRadius, boundaryAngle);

        bandGroup
          .append('line')
          .attr('class', 'circos-band-boundary')
          .attr('data-chrom', chrom.chr)
          .attr('x1', boundaryInner.x)
          .attr('y1', boundaryInner.y)
          .attr('x2', boundaryOuter.x)
          .attr('y2', boundaryOuter.y)
          .attr('stroke', black)
          .attr('stroke-width', BAND_STROKE)
          .attr('stroke-linecap', 'round');
      });

      g.append('path')
        .attr('class', 'circos-chromosome-outline')
        .attr('data-chrom', chrom.chr)
        .attr('d', outlinePath)
        .attr('fill', 'none')
        .attr('stroke', black)
        .attr('stroke-width', BAND_STROKE)
        .attr('stroke-linejoin', 'round');

      const labelAngle = (startAngle + endAngle) / 2;
      const labelRadius = outerRadius + 12;

      let labelStartAngle = startAngle;
      let labelEndAngle = endAngle;
      if (labelAngle > Math.PI / 2 && labelAngle < (3 * Math.PI) / 2) {
        [labelStartAngle, labelEndAngle] = [endAngle, startAngle];
      }

      const labelArc = d3
        .arc<d3.DefaultArcObject>()
        .innerRadius(labelRadius)
        .outerRadius(labelRadius)
        .startAngle(labelStartAngle)
        .endAngle(labelEndAngle);
      const labelArcPath = labelArc({} as d3.DefaultArcObject);

      const labelId = `chrom-label-${chrom.chr}`;

      g.append('path')
        .attr('id', labelId)
        .attr('d', labelArcPath)
        .attr('fill', 'none');

      g.append('text')
        .attr('text-anchor', 'middle')
        .attr('fill', 'currentColor')
        .style('font-size', '12px')
        .append('textPath')
        .attr('href', `#${labelId}`)
        .attr('startOffset', '20%')
        .text(`${chrom.chr}`);

      const clickPath = d3.arc()({
        innerRadius,
        outerRadius,
        startAngle,
        endAngle: startAngle + chromAngle,
      });
      g.append('path')
        .attr('d', clickPath ?? null)
        .attr('fill', 'transparent')
        .style('cursor', onChromosomeClick ? 'pointer' : 'default')
        .on('click', () => onChromosomeClick?.(chrom.chr));

      currentAngle += chromAngle + gap;
    });

    if (variants) {
      const link = d3
        .linkRadial<any, any>()
        .angle((d: any) => d.angle)
        .radius((d: any) => d.radius);

      const getScale = (chr?: string) => {
        if (!chr) return undefined;
        return angleScales[chr] || angleScales[chr.replace(/^chr/i, '')];
      };

      variants.forEach((v) => {
        const sourceScale = getScale(v.chr);
        // default to the same chromosome when remote_chr is undefined so
        // intra-chromosomal variants like DEL, DUP and INS are still drawn
        const targetScale = getScale(v.remote_chr || v.chr);
        const sourcePos = v.start;
        const targetPos = v.remote_start ?? v.end ?? v.start;
        const type = v.type?.toUpperCase();

        if (!sourceScale || !targetScale || !type) return;

        const sourceAngle = sourceScale(sourcePos);
        let targetAngle = targetScale(targetPos);

        const minAngle = 0.015;
        if (Math.abs(targetAngle - sourceAngle) < minAngle) {
          targetAngle =
            targetAngle >= sourceAngle
              ? sourceAngle + minAngle
              : sourceAngle - minAngle;
        }
        const isIntrachrom = !v.remote_chr || v.remote_chr === v.chr;

        let strokeWidth = 1;
        let pathEl:
          | d3.Selection<SVGPathElement | SVGLineElement, unknown, null, undefined>
          | null = null;

        if (type === 'INS') {
          const insertionOuter = innerRadius - 20;
          const insertionInner = innerRadius - 40;
          const path = link({
            source: { angle: sourceAngle, radius: insertionOuter },
            target: { angle: sourceAngle, radius: insertionInner },
          } as any);
          pathEl = g
            .append('path')
            .attr('d', path ?? null)
            .attr('fill', 'none')
            .attr('stroke', typeColors[type] || cssVar('--color-variant-default'))
            .attr('stroke-width', strokeWidth);
        } else if (type === 'INV') {
          const invRadius = innerRadius - 45;
          const midAngle = (sourceAngle + targetAngle) / 2;
          const controlRadius = invRadius * 0.75;
          const p = d3.path();
          p.moveTo(
            invRadius * Math.sin(sourceAngle),
            -invRadius * Math.cos(sourceAngle)
          );
          p.quadraticCurveTo(
            controlRadius * Math.sin(midAngle),
            -controlRadius * Math.cos(midAngle),
            invRadius * Math.sin(targetAngle),
            -invRadius * Math.cos(targetAngle)
          );
          strokeWidth = 1;
          pathEl = g
            .append('path')
            .attr('d', p.toString())
            .attr('fill', 'none')
            .attr('stroke', typeColors[type] || cssVar('--color-variant-default'))
            .attr('stroke-width', strokeWidth);
        } else if (type === 'BND') {
          const radius = innerRadius - 45;
          const midAngle = (sourceAngle + targetAngle) / 2;
          const controlRadius = radius * 0.6;
          const p = d3.path();
          p.moveTo(
            radius * Math.sin(sourceAngle),
            -radius * Math.cos(sourceAngle)
          );
          p.quadraticCurveTo(
            controlRadius * Math.sin(midAngle),
            -controlRadius * Math.cos(midAngle),
            radius * Math.sin(targetAngle),
            -radius * Math.cos(targetAngle)
          );
          pathEl = g
            .append('path')
            .attr('d', p.toString())
            .attr('fill', 'none')
            .attr('stroke', typeColors[type] || cssVar('--color-variant-default'))
            .attr('stroke-width', strokeWidth);
        } else {
          const radius = isIntrachrom ? innerRadius - 10 : innerRadius;
          const path = link({
            source: { angle: sourceAngle, radius },
            target: { angle: targetAngle, radius },
          } as any);
          if (type === 'DEL' || type === 'DUP') {
            strokeWidth = 15;
          }
          pathEl = g
            .append('path')
            .attr('d', path ?? null)
            .attr('fill', 'none')
            .attr('stroke', typeColors[type] || cssVar('--color-variant-default'))
            .attr('stroke-width', strokeWidth);
        }

        if (!pathEl) return;

        if (type === 'BND') {
          pathEl
            .style('cursor', onVariantClick ? 'pointer' : 'default')
            .on('click', () => onVariantClick?.(v));
        }

        pathEl
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
              .html(`<strong>${type}</strong><br/>${coords}`);
            d3.select(event.currentTarget)
              .attr('stroke-width', strokeWidth + 2)
              .raise();
          })
          .on('mousemove', (event) => {
            tooltip
              .style('left', `${event.pageX + 8}px`)
              .style('top', `${event.pageY + 8}px`);
          })
          .on('mouseout', (event) => {
            tooltip.style('opacity', 0);
            d3.select(event.currentTarget).attr('stroke-width', strokeWidth);
          });
      });
    }
    return () => {
      tooltip.remove();
    };
  }, [chromData, selected, variants, onChromosomeClick, onVariantClick]);

  return (
    <div className="flex flex-col items-center">
      <svg ref={svgRef} className="mx-auto block text-text"></svg>
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
