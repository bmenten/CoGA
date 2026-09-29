import React, { useEffect, useRef, useMemo } from "react";
import { createPortal } from "react-dom";
import { useQuery } from "@tanstack/react-query";
import { useSameSpanFallbackData } from '../../lib/useSameSpanFallbackData';
import VizErrorOverlay from './VizErrorOverlay';
import { NO_REGION_IN_VIEW, describeTrackRegion, hasRegionInView } from './trackRegion';
import { select } from "d3-selection";
import api from "../../lib/api";
import { cssVar } from "../../lib/colors";
import { apiPath } from '../../lib/apiPath';
import { escapeHtml } from "../../lib/escapeHtml";

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

const GENE_HEIGHT = 8;
const LINE_HEIGHT = GENE_HEIGHT + 4;

// How many genes are in view and the first three, in the order drawn (#529).
const describeGenes = (genes: Gene[]): string => {
  const names = genes.slice(0, 3).map((gene) => gene.hgnc_symbol).join(", ");
  return `${genes.length.toLocaleString()} (${names}${genes.length > 3 ? ", …" : ""})`;
};

const GeneTrack: React.FC<Props> = ({
  assembly,
  chrom,
  width,
  regionStart,
  regionEnd,
}) => {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const tooltipRef = useRef<HTMLDivElement | null>(null);

  const { data: rawGenes, isError, refetch } = useQuery<Gene[]>({
    queryKey: ["genes", assembly, chrom, regionStart, regionEnd],
    queryFn: async () => {
      const res = await api.get(apiPath`/genes/${assembly}/${chrom}`, {
        params: { start: regionStart, end: regionEnd },
      });
      return res.data as Gene[];
    },
    enabled: regionEnd > regionStart,
    staleTime: Infinity,
    gcTime: Infinity,
  });
  const genes = useSameSpanFallbackData(
    rawGenes,
    (regionEnd ?? 0) - (regionStart ?? 0),
    `${assembly}|${chrom}`,
    isError,
  );

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
  const geneFill = cssVar("--color-gene-fill");
  const geneStroke = cssVar("--color-gene-stroke");

  const { genesWithLines, svgHeight } = useMemo(() => {
    if (!genes) return { genesWithLines: [], svgHeight: 0 };
    const lines: number[] = [];
    // Only what overlaps the region: while a pan loads, the previous window's genes are
    // held, and one left of the new window was drawn at its left edge under its own name,
    // as if that gene lay there (#586; the other interval tracks since #526).
    const sortedGenes = genes
      .filter((g) => g.end > regionStart && g.start < regionEnd)
      .sort((a, b) => a.start - b.start);
    const withLines = sortedGenes.map((g) => {
      const start = Math.max(g.start, regionStart);
      const end = Math.min(g.end, regionEnd);
      let lineIndex = 0;
      while (lineIndex < lines.length && start < lines[lineIndex]) lineIndex++;
      lines[lineIndex] = end;
      return { g, start, end, lineIndex };
    });
    return { genesWithLines: withLines, svgHeight: lines.length * LINE_HEIGHT + 4 };
  }, [genes, regionStart, regionEnd]);
  const hasGenes = (genes?.length || 0) > 0;
  const containerHeight = Math.max(svgHeight, 24);

  // The track's name for a screen reader (#529): the genes in the region. A failure is
  // never "none" (#510), and neither is a pan whose window has not arrived yet: the
  // held genes are named, like they are drawn, only where they lie in the new region.
  // A view with no width asks for nothing, so it is not "loading" either (#602).
  const genesInView = genesWithLines.map(({ g }) => g);
  const geneSummary = isError
    ? "failed to load"
    : !hasRegionInView(regionStart, regionEnd)
      ? NO_REGION_IN_VIEW
      : genesInView.length > 0
        ? describeGenes(genesInView)
        : rawGenes
          ? "none"
          : "loading";
  const ariaLabel = `Genes on ${describeTrackRegion(chrom, regionStart, regionEnd)}: ${geneSummary}`;

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
        const y = 2 + d.lineIndex * LINE_HEIGHT;
        return `translate(${x},${y})`;
      })
      .on("mousemove", function (event, d) {
        const panels = panelMap[d.g.hgnc_symbol] || [];
        // Position in viewport coordinates: the tooltip is portalled to <body>
        // (position: fixed) so it escapes the track frame's overflow clip.
        const offset = 12;
        const estimatedWidth = 320;
        const left =
          event.clientX + offset + estimatedWidth > window.innerWidth
            ? Math.max(offset, event.clientX - offset - estimatedWidth)
            : event.clientX + offset;
        tooltip
          .style("display", "block")
          .style("left", `${left}px`)
          .style("top", `${event.clientY + offset}px`)
          .html(
            `<div>${escapeHtml(d.g.hgnc_symbol)}</div>` +
              (panels.length ? `<div>Panels: ${escapeHtml(panels.join(", "))}</div>` : "")
          );
      })
      .on("mouseout", () => tooltip.style("display", "none"));

    groups.each(function (d) {
      const g = select(this);
      const geneWidth = Math.max(((d.end - d.start) / regionLength) * width, 1);
      const midY = GENE_HEIGHT / 2;
      if (geneWidth < 6) {
        g.append("rect")
          .attr("width", 6)
          .attr("height", GENE_HEIGHT)
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
          .attr("height", GENE_HEIGHT)
          .attr("fill", geneFill)
          .attr("stroke", geneStroke);
        g.append("path").attr("d", arrowPath).attr("fill", cssVar("--color-gene-stroke"));
        return;
      }

      const exons = d.g.exons
        .filter((e) => e.end > regionStart && e.start < regionEnd)
        .sort((a, b) => a.start - b.start);

      // The gene's whole extent in view, under the exons. Drawn once, it also covers a
      // view that falls inside an intron: with lines only between neighbouring exons in
      // view, such a view showed no gene, as if the region were intergenic (#526).
      g.append("line")
        .attr("class", "gene-body")
        .attr("x1", 0)
        .attr("x2", geneWidth)
        .attr("y1", midY)
        .attr("y2", midY)
        .attr("stroke", geneStroke)
        .attr("stroke-width", 1);

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
        .attr("height", GENE_HEIGHT)
        .attr("fill", geneStroke);

      g.append("path").attr("d", arrowPath).attr("fill", cssVar("--color-gene-stroke"));
    });
  }, [genesWithLines, panelMap, width, regionLength, regionStart, regionEnd, geneFill, geneStroke]);

  return (
    <div
      style={{ position: "relative", width, height: containerHeight }}
      className="text-text"
    >
      <svg
        ref={svgRef}
        width={width}
        height={containerHeight}
        role="img"
        aria-label={ariaLabel}
      />
      {isError && <VizErrorOverlay what="genes" onRetry={() => void refetch()} />}
      {!isError && genes !== null && !hasGenes && (
        <div className="viz-empty-overlay">No genes in this region</div>
      )}
      {createPortal(
        <div ref={tooltipRef} className="viz-tooltip viz-tooltip--floating hidden" />,
        document.body,
      )}
    </div>
  );
};

export default GeneTrack;
