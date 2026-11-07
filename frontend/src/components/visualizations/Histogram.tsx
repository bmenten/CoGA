import React from 'react';
import * as d3 from 'd3';
import { countOf } from '../../lib/countOf';

interface Props {
  data: number[];
  /** Counts the server binned, one per bin of `binLabels`: drawn as they are, and `data` is
   *  not read. */
  counts?: number[];
  width?: number;
  height?: number;
  bins?: number;
  binEdges?: number[];
  binLabels?: string[];
  logScale?: boolean;
  /** What the values are, e.g. "SV lengths". Only named in the chart's accessible name. */
  subject?: string;
}

// The x axis's labels are drawn in 10px sans-serif. Their widths, in pixels, from Helvetica's
// (and Arial's) advance widths at that size: digits 5.56, the point 2.78, the minus 3.33.
const LABEL_FONT_SIZE = 10;
const CHAR_WIDTHS: Record<string, number> = { '.': 2.78, ',': 2.78, ' ': 2.78, '-': 3.33, '+': 5.84, '>': 5.84, k: 5, M: 8.33 };
const DEFAULT_CHAR_WIDTH = 5.56;
// The least room between two labels.
const LABEL_GAP = 3;

const labelWidth = (label: string): number =>
  Array.from(label).reduce((sum, char) => sum + (CHAR_WIDTHS[char] ?? DEFAULT_CHAR_WIDTH), 0);

/**
 * The bins whose label is drawn: every bin's, when no two neighbouring labels run into each
 * other; else every n-th bin's, n the least that leaves room between each label and the next
 * (the allele balance's 20 bins of "0.05" overlapped). The last bin keeps its label, in place
 * of the one before it: an open-ended bin ("200+") says where the axis stops.
 */
export const labelledBins = (labels: string[], binStep: number): number[] => {
  if (!labels.length) return [];
  const widths = labels.map(labelWidth);
  const last = labels.length - 1;
  const fits = (shown: number[]) =>
    shown.every(
      (bin, k) =>
        k === 0 ||
        (widths[shown[k - 1]] + widths[bin]) / 2 + LABEL_GAP <= (bin - shown[k - 1]) * binStep,
    );
  for (let every = 1; every <= last; every += 1) {
    const shown = labels.map((_, i) => i).filter((i) => i % every === 0);
    if (shown[shown.length - 1] !== last) {
      if (shown.length > 1) shown.pop();
      shown.push(last);
    }
    if (fits(shown)) return shown;
  }
  return [0];
};

interface Binned {
  counts: number[];
  labels: string[];
}

const binValues = (
  data: number[],
  bins: number,
  binEdges?: number[],
  binLabels?: string[],
): Binned => {
  let counts: number[] = [];
  let labels: string[] = [];
  if (binEdges && binEdges.length > 1) {
    counts = Array(binEdges.length - 1).fill(0);
    data.forEach((v) => {
      const idx = binEdges.findIndex(
        (edge, i) => i < binEdges.length - 1 && v >= edge && v < binEdges[i + 1]
      );
      if (idx === -1) {
        if (v < binEdges[0]) counts[0]++;
        else counts[counts.length - 1]++;
      } else {
        counts[idx]++;
      }
    });
    labels =
      binLabels && binLabels.length === counts.length
        ? binLabels
        : binEdges.slice(0, -1).map((edge, i) => `${edge}-${binEdges[i + 1]}`);
  } else {
    let min = data[0];
    let max = data[0];
    for (const v of data) {
      if (v < min) min = v;
      if (v > max) max = v;
    }
    const range = max - min || 1;
    const binSize = range / bins;
    counts = Array(bins).fill(0);
    data.forEach((v) => {
      const idx = Math.min(Math.floor((v - min) / binSize), bins - 1);
      counts[idx]++;
    });
    // Each bin is labelled by its lower edge, with as many decimals as the bin width needs:
    // rounded to whole numbers, bins narrower than 1 shared a label (#602).
    const decimals = binSize >= 1 ? 0 : Math.min(Math.ceil(-Math.log10(binSize)), 6);
    labels = Array.from({ length: bins }, (_, i) => {
      const edge = min + binSize * i;
      return decimals === 0 ? String(Math.round(edge)) : edge.toFixed(decimals);
    });
  }
  return { counts, labels };
};

const Histogram: React.FC<Props> = ({
  data,
  counts: givenCounts,
  width = 400,
  height = 200,
  bins = 20,
  binEdges,
  binLabels,
  logScale = true,
  subject,
}) => {
  const svgRef = React.useRef<SVGSVGElement | null>(null);

  // Shared by the drawing and the accessible name, so the name counts what is drawn.
  const binned = React.useMemo(() => {
    if (givenCounts) {
      return givenCounts.some((count) => count > 0)
        ? { counts: givenCounts, labels: givenCounts.map((_, i) => binLabels?.[i] ?? String(i)) }
        : null;
    }
    return data.length ? binValues(data, bins, binEdges, binLabels) : null;
  }, [givenCounts, data, bins, binEdges, binLabels]);
  // How many values the bars count: the values given, or the server's counts summed.
  const valueCount = givenCounts ? givenCounts.reduce((sum, count) => sum + count, 0) : data.length;

  React.useEffect(() => {
    if (!binned) return;
    const { counts, labels } = binned;

    const margin = { top: 10, right: 10, bottom: 30, left: 40 };
    const innerWidth = width - margin.left - margin.right;
    const innerHeight = height - margin.top - margin.bottom;

    const maxCount = Math.max(...counts) || 1;

    const svg = d3.select(svgRef.current);
    svg.selectAll('*').remove();
    const g = svg
      .append('g')
      .attr('transform', `translate(${margin.left},${margin.top})`);

    // The band scale is keyed by bin, not by label: two bins with the same label (given
    // binLabels can repeat) would otherwise share one band and be drawn on top of each
    // other (#602).
    const x = d3
      .scaleBand<number>()
      .domain(counts.map((_, i) => i))
      .range([0, innerWidth])
      .padding(0.1);

    const y = logScale
      ? d3
          .scaleLog()
          .domain([1, maxCount + 1])
          .range([innerHeight, 0])
      : d3
          .scaleLinear()
          .domain([0, maxCount])
          .nice()
          .range([innerHeight, 0]);

    g
      .selectAll('rect')
      .data(counts)
      .join('rect')
      .attr('x', (_, i) => x(i) ?? 0)
      .attr('width', x.bandwidth())
      .attr('y', (d) => (logScale ? y(d + 1) : y(d)))
      .attr('height', (d) => innerHeight - (logScale ? y(d + 1) : y(d)))
      .attr('class', 'fill-secondary');

    const xAxis = d3
      .axisBottom(x)
      .tickValues(labelledBins(labels, x.step()))
      .tickFormat((i) => labels[i]);
    g
      .append('g')
      .attr('transform', `translate(0,${innerHeight})`)
      .call(xAxis)
      .selectAll('text')
      .attr('font-size', LABEL_FONT_SIZE);

    const logTicks = [10, 100, 1000, 10000].filter((v) => v <= maxCount);
    const yAxis = logScale
      ? d3
          .axisLeft(y)
          .tickValues(logTicks.map((v) => v + 1))
          .tickFormat((d) => String(Math.round((d as number) - 1)))
      : d3.axisLeft(y).ticks(5);

    g.append('g').call(yAxis).selectAll('text').attr('font-size', 10);
  }, [binned, width, height, logScale]);

  if (!binned) {
    return <p className="analysis-count">No data available for this view.</p>;
  }

  // The tallest bar is the chart's headline; the first one wins a tie.
  const peak = binned.counts.reduce((best, count, i) => (count > binned.counts[best] ? i : best), 0);
  const chartLabel =
    `Histogram${subject ? ` of ${subject}` : ''}: ${countOf(valueCount, 'value')} in ` +
    `${countOf(binned.counts.length, 'bin')}; tallest bin ${binned.labels[peak]} ` +
    `with ${binned.counts[peak].toLocaleString()}`;

  return <svg ref={svgRef} width={width} height={height} role="img" aria-label={chartLabel} />;
};

export default Histogram;
