import React from 'react';
import * as d3 from 'd3';

interface Props {
  data: number[];
  width?: number;
  height?: number;
  bins?: number;
  binEdges?: number[];
  binLabels?: string[];
  logScale?: boolean;
}

const Histogram: React.FC<Props> = ({
  data,
  width = 400,
  height = 200,
  bins = 20,
  binEdges,
  binLabels,
  logScale = true,
}) => {
  if (!data.length) return <p className="analysis-count">No data available for this view.</p>;

  const svgRef = React.useRef<SVGSVGElement | null>(null);

  React.useEffect(() => {
    if (!data.length) return;

    const margin = { top: 10, right: 10, bottom: 30, left: 40 };
    const innerWidth = width - margin.left - margin.right;
    const innerHeight = height - margin.top - margin.bottom;

    let counts: number[] = [];
    let labels: string[] = [];
    if (binEdges && binEdges.length > 1) {
      counts = Array(binEdges.length - 1).fill(0);
      data.forEach((v) => {
        const idx = binEdges.findIndex(
          (edge, i) => i < binEdges.length - 1 && v >= edge && v < binEdges[i + 1]
        );
        if (idx === -1) counts[counts.length - 1]++;
        else counts[idx]++;
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
      labels = Array.from({ length: bins }, (_, i) =>
        String(Math.round(min + binSize * i))
      );
    }

    const maxCount = Math.max(...counts) || 1;

    const svg = d3.select(svgRef.current);
    svg.selectAll('*').remove();
    const g = svg
      .append('g')
      .attr('transform', `translate(${margin.left},${margin.top})`);

    const x = d3
      .scaleBand<string>()
      .domain(labels)
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
      .attr('x', (_, i) => x(labels[i]) || 0)
      .attr('width', x.bandwidth())
      .attr('y', (d) => (logScale ? y(d + 1) : y(d)))
      .attr('height', (d) => innerHeight - (logScale ? y(d + 1) : y(d)))
      .attr('class', 'fill-secondary');

    const xAxis = d3.axisBottom(x);
    g
      .append('g')
      .attr('transform', `translate(0,${innerHeight})`)
      .call(xAxis)
      .selectAll('text')
      .attr('font-size', 10);

    const logTicks = [10, 100, 1000, 10000].filter((v) => v <= maxCount);
    const yAxis = logScale
      ? d3
          .axisLeft(y)
          .tickValues(logTicks.map((v) => v + 1))
          .tickFormat((d) => String(Math.round((d as number) - 1)))
      : d3.axisLeft(y).ticks(5);

    g.append('g').call(yAxis).selectAll('text').attr('font-size', 10);
  }, [data, width, height, bins, binEdges, binLabels, logScale]);

  return <svg ref={svgRef} width={width} height={height} />;
};

export default Histogram;
