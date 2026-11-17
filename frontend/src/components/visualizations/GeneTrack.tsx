import React, { useEffect, useRef, useMemo } from "react";
import { createPortal } from "react-dom";
import { useQuery } from "@tanstack/react-query";
import { useSameSpanFallbackData } from '../../lib/useSameSpanFallbackData';
import VizErrorOverlay from './VizErrorOverlay';
import { NO_REGION_IN_VIEW, describeTrackRegion, hasRegionInView } from './trackRegion';
import { select } from "d3-selection";
import api from "../../lib/api";
import type { GeneOut, GenePanelOut } from "../../lib/apiSchema.generated";
import { cssVar } from "../../lib/colors";
import { apiPath } from '../../lib/apiPath';
import { escapeHtml } from "../../lib/escapeHtml";

interface Props {
  assembly: string;
  chrom: string;
  width: number;
  regionStart: number;
  regionEnd: number;
}

const GENE_HEIGHT = 8;
const LINE_HEIGHT = GENE_HEIGHT + 4;
// The strand chevrons along an intron: one every CHEVRON_SPACING px, each CHEVRON_HALF_WIDTH px
// either side of its point, and none that would touch an exon or the arrow at the gene's end.
const CHEVRON_SPACING = 12;
const CHEVRON_HALF_WIDTH = 2;
const CHEVRON_END_CLEARANCE = 6;

// An exon of the gene's transcript, numbered as the transcript reads it: from its 5′ end, so
// exon 1 is the leftmost on the + strand and the rightmost on the − strand.
interface NumberedExon {
  start: number;
  end: number;
  number: number;
}

const numberExons = (gene: GeneOut): NumberedExon[] => {
  const sorted = [...gene.exons].sort((a, b) => a.start - b.start);
  return sorted.map((exon, index) => ({
    start: exon.start,
    end: exon.end,
    number: gene.strand === -1 ? sorted.length - index : index + 1,
  }));
};

const strandLabel = (strand: number): string | null =>
  strand === 1
    ? "Strand + (transcribed left → right)"
    : strand === -1
      ? "Strand − (transcribed right → left)"
      : null;

// Which exon or intron of the gene a position lies in, in the transcript's numbering:
// "Exon 3 / 12" or "Intron 3 / 11". Null outside the transcript's exons and introns.
const describeExonPosition = (
  exons: NumberedExon[],
  strand: number,
  position: number,
  hitExon: NumberedExon | undefined,
): string | null => {
  if (!exons.length) return null;
  if (hitExon) return `Exon ${hitExon.number} / ${exons.length}`;
  const index = exons.findIndex(
    (exon, i) => i + 1 < exons.length && position >= exon.end && position < exons[i + 1].start,
  );
  if (index < 0) return null;
  const introns = exons.length - 1;
  return `Intron ${strand === -1 ? introns - index : index + 1} / ${introns}`;
};

// How many genes are in view and the first three, in the order drawn.
const describeGenes = (genes: GeneOut[]): string => {
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

  const { data: rawGenes, isError, refetch } = useQuery<GeneOut[]>({
    queryKey: ["genes", assembly, chrom, regionStart, regionEnd],
    queryFn: async () => {
      const res = await api.get(apiPath`/genes/${assembly}/${chrom}`, {
        params: { start: regionStart, end: regionEnd },
      });
      return res.data as GeneOut[];
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

  const { data: panels } = useQuery<GenePanelOut[]>({
    queryKey: ["gene-panels"],
    queryFn: async () => {
      const res = await api.get(`/panels`);
      return res.data as GenePanelOut[];
    },
  });

  // The track names genes by symbol: a gene a panel names by its Ensembl gene ID is listed
  // under the ID's symbol.
  const panelMap = useMemo(() => {
    const map: Record<string, string[]> = {};
    panels?.forEach((p) => {
      p.genes.forEach((g) => {
        const name = p.gene_symbols?.[g] ?? g;
        map[name] = map[name] ? [...map[name], p.name] : [p.name];
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
    // Only what overlaps the region, as on the other interval tracks: while a pan loads, the
    // previous window's genes are held, and one left of the new window would be drawn at its
    // left edge under its own name, as if that gene lay there.
    const sortedGenes = genes
      .filter((g) => g.end > regionStart && g.start < regionEnd)
      .sort((a, b) => a.start - b.start);
    const withLines = sortedGenes.map((g) => {
      const start = Math.max(g.start, regionStart);
      const end = Math.min(g.end, regionEnd);
      let lineIndex = 0;
      while (lineIndex < lines.length && start < lines[lineIndex]) lineIndex++;
      lines[lineIndex] = end;
      return { g, start, end, lineIndex, exons: numberExons(g) };
    });
    return { genesWithLines: withLines, svgHeight: lines.length * LINE_HEIGHT + 4 };
  }, [genes, regionStart, regionEnd]);
  const hasGenes = (genes?.length || 0) > 0;
  const containerHeight = Math.max(svgHeight, 24);

  // The track's name for a screen reader: the genes in the region. A failure is
  // never "none", and neither is a pan whose window has not arrived yet: the
  // held genes are named, like they are drawn, only where they lie in the new region.
  // A view with no width asks for nothing, so it is not "loading" either.
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

    // Where a gene and its exons are drawn: x in px from the region's left edge, and an exon's
    // x and width within its gene's group.
    const toX = (position: number) => ((position - regionStart) / regionLength) * width;
    const geneOffset = (d: (typeof genesWithLines)[0]) => toX(d.start);
    const exonsInView = (d: (typeof genesWithLines)[0]) =>
      d.exons.filter((e) => e.end > regionStart && e.start < regionEnd);
    const exonBox = (d: (typeof genesWithLines)[0], exon: NumberedExon) => {
      const exonStart = Math.max(exon.start, regionStart);
      const exonEnd = Math.min(exon.end, regionEnd);
      return {
        x: toX(exonStart) - geneOffset(d),
        width: Math.max(((exonEnd - exonStart) / regionLength) * width, 1),
      };
    };
    const geneWidthOf = (d: (typeof genesWithLines)[0]) =>
      Math.max(((d.end - d.start) / regionLength) * width, 1);
    // Exons are drawn, and numbered on hover, only where a gene is wide enough to show them.
    const showsExons = (d: (typeof genesWithLines)[0]) => geneWidthOf(d) >= 20;

    const groups = svg
      .selectAll<SVGGElement, typeof genesWithLines[0]>("g")
      .data(genesWithLines)
      .join("g")
      .attr("transform", (d) => {
        const x = geneOffset(d);
        const y = 2 + d.lineIndex * LINE_HEIGHT;
        return `translate(${x},${y})`;
      })
      .on("mousemove", function (event, d) {
        const panels = panelMap[d.g.hgnc_symbol] || [];
        const strand = strandLabel(d.g.strand);
        // The exon or intron under the pointer, read from where the exons are drawn.
        let exonPosition: string | null = null;
        if (showsExons(d)) {
          const svgLeft = svgRef.current?.getBoundingClientRect().left ?? 0;
          const localX = event.clientX - svgLeft - geneOffset(d);
          const hit = exonsInView(d).find((exon) => {
            const box = exonBox(d, exon);
            return localX >= box.x - 0.5 && localX <= box.x + box.width + 0.5;
          });
          const position = regionStart + ((localX + geneOffset(d)) / width) * regionLength;
          exonPosition = describeExonPosition(d.exons, d.g.strand, position, hit);
        }
        const transcript =
          d.g.gene_id && d.g.gene_id !== d.g.hgnc_symbol ? d.g.gene_id : null;
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
              (strand ? `<div>${strand}</div>` : "") +
              (exonPosition ? `<div>${exonPosition}</div>` : "") +
              (transcript ? `<div>Transcript ${escapeHtml(transcript)}</div>` : "") +
              (panels.length ? `<div>Panels: ${escapeHtml(panels.join(", "))}</div>` : "")
          );
      })
      .on("mouseout", () => tooltip.style("display", "none"));

    groups.each(function (d) {
      const g = select(this);
      const geneWidth = geneWidthOf(d);
      const midY = GENE_HEIGHT / 2;
      if (geneWidth < 6) {
        g.append("rect")
          .attr("width", 6)
          .attr("height", GENE_HEIGHT)
          .attr("fill", geneStroke);
        return;
      }

      const showExons = showsExons(d);
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

      const exons = exonsInView(d);
      const boxes = exons.map((exon) => exonBox(d, exon));

      // The whole gene's row takes the pointer, so an intron can be hovered and not only its
      // 1 px line.
      g.append("rect")
        .attr("class", "gene-hit")
        .attr("width", geneWidth)
        .attr("height", GENE_HEIGHT)
        .attr("fill", "transparent")
        .attr("pointer-events", "all");

      // The gene's whole extent in view, under the exons. Drawn once, it also covers a
      // view that falls inside an intron: with lines only between neighbouring exons in
      // view, such a view would show no gene, as if the region were intergenic.
      g.append("line")
        .attr("class", "gene-body")
        .attr("x1", 0)
        .attr("x2", geneWidth)
        .attr("y1", midY)
        .attr("y2", midY)
        .attr("stroke", geneStroke)
        .attr("stroke-width", 1);

      // The direction of transcription along the introns, as a genome browser draws it:
      // chevrons pointing the way the strand is read, clear of the exons and the end arrow.
      if (d.g.strand === 1 || d.g.strand === -1) {
        const tip = d.g.strand === 1 ? 1 : -1;
        const chevrons: string[] = [];
        for (
          let x = CHEVRON_END_CLEARANCE + CHEVRON_HALF_WIDTH;
          x <= geneWidth - CHEVRON_END_CLEARANCE - CHEVRON_HALF_WIDTH;
          x += CHEVRON_SPACING
        ) {
          const touchesExon = boxes.some(
            (box) =>
              x + CHEVRON_HALF_WIDTH + 1 > box.x && x - CHEVRON_HALF_WIDTH - 1 < box.x + box.width,
          );
          if (touchesExon) continue;
          const back = x - tip * CHEVRON_HALF_WIDTH;
          const front = x + tip * CHEVRON_HALF_WIDTH;
          chevrons.push(`M ${back} ${midY - 3} L ${front} ${midY} L ${back} ${midY + 3}`);
        }
        if (chevrons.length) {
          g.append("path")
            .attr("class", "gene-strand")
            .attr("d", chevrons.join(" "))
            .attr("fill", "none")
            .attr("stroke", geneStroke)
            .attr("stroke-width", 1);
        }
      }

      g.selectAll("rect.exon")
        .data(exons)
        .enter()
        .append("rect")
        .attr("class", "exon")
        .attr("x", (exon) => exonBox(d, exon).x)
        .attr("width", (exon) => exonBox(d, exon).width)
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
