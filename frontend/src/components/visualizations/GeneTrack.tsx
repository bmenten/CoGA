import React, { useEffect, useRef, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { select, pointer } from "d3-selection";
import api from "../../lib/api";
import { cssVar } from "../../lib/colors";

interface GeneExon {
  start: number;
  end: number;
  name: string;
}

interface Gene {
  gene_id: string;
  hgnc_symbol: string;
  start: number;
  end: number;
  exons: GeneExon[];
  strand: number;
}

interface GenePanel {
  name: string;
  genes: string[];
}

interface Props {
  assembly: string;
  chrom: string;
  width: number;
  regionStart: number;
  regionEnd: number;
}

const GeneTrack: React.FC<Props> = ({
  assembly,
  chrom,
  width,
  regionStart,
  regionEnd,
}) => {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const tooltipRef = useRef<HTMLDivElement | null>(null);

  const { data: genes } = useQuery<Gene[]>({
    queryKey: ["genes", assembly, chrom, regionStart, regionEnd],
    queryFn: async () => {
      const res = await api.get(`/genes/${assembly}/${chrom}`, {
        params: { start: regionStart, end: regionEnd },
      });
      return res.data as Gene[];
    },
    enabled: regionEnd > regionStart,
  });

  const { data: panels } = useQuery<GenePanel[]>({
    queryKey: ["gene-panels"],
    queryFn: async () => {
      const res = await api.get(`/panels`);
      return res.data as GenePanel[];
    },
  });

  const panelMap = useMemo(() => {
    const map: Record<string, string[]> = {};
    panels?.forEach((p) => {
      p.genes.forEach((g) => {
        map[g] = map[g] ? [...map[g], p.name] : [p.name];
      });
    });
    return map;
  }, [panels]);

  const regionLength = regionEnd - regionStart;
  const geneHeight = 8;
  const lineHeight = geneHeight + 4;
  const geneFill = cssVar("--color-gene-fill");
  const geneStroke = cssVar("--color-gene-stroke");

  const { genesWithLines, svgHeight } = useMemo(() => {
    if (!genes) return { genesWithLines: [], svgHeight: 0 };
    const lines: number[] = [];
    const sortedGenes = genes.slice().sort((a, b) => a.start - b.start);
    const withLines = sortedGenes.map((g) => {
      const start = Math.max(g.start, regionStart);
      const end = Math.min(g.end, regionEnd);
      let lineIndex = 0;
      while (lineIndex < lines.length && start < lines[lineIndex]) lineIndex++;
      lines[lineIndex] = end;
      return { g, start, end, lineIndex };
    });
    return { genesWithLines: withLines, svgHeight: lines.length * lineHeight + 4 };
  }, [genes, regionStart, regionEnd]);
  const hasGenes = (genes?.length || 0) > 0;
  const containerHeight = Math.max(svgHeight, 24);

  useEffect(() => {
    const svg = select(svgRef.current);
    svg.selectAll("*").remove();
    const tooltip = select(tooltipRef.current);
    if (!genesWithLines.length) return;

    const groups = svg
      .selectAll<SVGGElement, typeof genesWithLines[0]>("g")
      .data(genesWithLines)
      .join("g")
      .attr("transform", (d) => {
        const x = ((d.start - regionStart) / regionLength) * width;
        const y = 2 + d.lineIndex * lineHeight;
        return `translate(${x},${y})`;
      })
      .on("mousemove", function (event, d) {
        const [x, y] = pointer(event, svg.node());
        const panels = panelMap[d.g.hgnc_symbol] || [];
        tooltip
          .style("display", "block")
          .style("left", `${x + 10}px`)
          .style("top", `${y + 10}px`)
          .html(
            `<div>${d.g.hgnc_symbol}</div>` +
              (panels.length ? `<div>Panels: ${panels.join(", ")}</div>` : "")
          );
      })
      .on("mouseout", () => tooltip.style("display", "none"));

    groups.each(function (d) {
      const g = select(this);
      const geneWidth = Math.max(((d.end - d.start) / regionLength) * width, 1);
      const midY = geneHeight / 2;
      if (geneWidth < 6) {
        g.append("rect")
          .attr("width", 6)
          .attr("height", geneHeight)
          .attr("fill", geneStroke);
        return;
      }

      const showExons = geneWidth >= 20;
      const arrowPath =
        d.g.strand === 1
          ? `M ${geneWidth - 4} ${midY - 4} L ${geneWidth} ${midY} L ${
              geneWidth - 4
            } ${midY + 4}`
          : `M 4 ${midY - 4} L 0 ${midY} L 4 ${midY + 4}`;

      if (!showExons) {
        g.append("rect")
          .attr("width", geneWidth)
          .attr("height", geneHeight)
          .attr("fill", geneFill)
          .attr("stroke", geneStroke);
        g.append("path").attr("d", arrowPath).attr("fill", cssVar("--color-gene-stroke"));
        return;
      }

      const exons = d.g.exons
        .filter((e) => e.end > regionStart && e.start < regionEnd)
        .sort((a, b) => a.start - b.start);

      g.selectAll("rect.exon")
        .data(exons)
        .enter()
        .append("rect")
        .attr("class", "exon")
        .attr("x", (exon) => {
          const exonStart = Math.max(exon.start, regionStart);
          return ((exonStart - regionStart) / regionLength) * width -
            ((d.start - regionStart) / regionLength) * width;
        })
        .attr("width", (exon) => {
          const exonStart = Math.max(exon.start, regionStart);
          const exonEnd = Math.min(exon.end, regionEnd);
          return Math.max(((exonEnd - exonStart) / regionLength) * width, 1);
        })
        .attr("height", geneHeight)
        .attr("fill", geneStroke);

      g.selectAll("line.intron")
        .data(exons.slice(0, -1))
        .enter()
        .append("line")
        .attr("class", "intron")
        .attr("x1", (exon) => {
          const exonEnd = Math.min(exon.end, regionEnd);
          return ((exonEnd - regionStart) / regionLength) * width -
            ((d.start - regionStart) / regionLength) * width;
        })
        .attr("x2", (_exon, i) => {
          const nextStart = Math.max(exons[i + 1].start, regionStart);
          return ((nextStart - regionStart) / regionLength) * width -
            ((d.start - regionStart) / regionLength) * width;
        })
        .attr("y1", midY)
        .attr("y2", midY)
        .attr("stroke", geneStroke)
        .attr("stroke-width", 1);

      g.append("path").attr("d", arrowPath).attr("fill", cssVar("--color-gene-stroke"));
    });
  }, [genesWithLines, panelMap, width, regionLength, regionStart, regionEnd]);

  return (
    <div
      style={{ position: "relative", width, height: containerHeight }}
      className="text-text"
    >
      <svg ref={svgRef} width={width} height={containerHeight} />
      {genes !== undefined && !hasGenes && (
        <div className="viz-empty-overlay">No genes in this region</div>
      )}
      <div
        ref={tooltipRef}
        className="viz-tooltip hidden"
      />
    </div>
  );
};

export default GeneTrack;
