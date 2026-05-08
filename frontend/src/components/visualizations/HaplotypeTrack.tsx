import React, { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import * as d3 from "d3";
import api from "../../lib/api";
import { cssVar } from "../../lib/colors";
import VizLoadingOverlay from "./VizLoadingOverlay";

interface Segment {
  start: number;
  end: number;
  hap1: string;
  hap2: string;
}

interface SampleSegments {
  sample: string;
  segments: Segment[];
}

interface HaplotypeResponse {
  chr: string;
  start: number;
  end: number;
  samples: SampleSegments[];
}

const isDeletedHaplotype = (value: string): boolean => value === ".";

interface Props {
  familyId: string;
  sampleId: string;
  chrom: string;
  regionStart: number;
  regionEnd: number;
  width: number;
  height: number;
  role: string;
  affected: boolean;
  disorder?: "dominant" | "recessive";
}

const HaplotypeTrack: React.FC<Props> = ({
  familyId,
  sampleId,
  chrom,
  regionStart,
  regionEnd,
  width,
  height,
  role,
  affected,
  disorder = "dominant",
}) => {
  const { data, isLoading } = useQuery<HaplotypeResponse>({
    queryKey: ["haplotypes", familyId, chrom, regionStart, regionEnd],
    queryFn: async () => {
      const params = { chr: chrom, start: regionStart, end: regionEnd };
      const res = await api.get(`/families/${familyId}/haplotypes`, { params });
      return res.data as HaplotypeResponse;
    },
    enabled: regionEnd > regionStart,
    staleTime: Infinity,
    gcTime: Infinity,
  });

  const svgRef = useRef<SVGSVGElement | null>(null);

  useEffect(() => {
    if (isLoading) {
      return;
    }

    if (!svgRef.current) return;
    const fatherColors = [
      cssVar("--color-haplotype-father-dark"),
      cssVar("--color-haplotype-father-light"),
    ];
    const motherColors = [
      cssVar("--color-haplotype-mother-dark"),
      cssVar("--color-haplotype-mother-light"),
    ];
    const affectedColors: Record<string, string> = {
      dominant: cssVar("--color-haplotype-dominant"),
      recessive: cssVar("--color-haplotype-recessive"),
    };
    const unknownColor = cssVar("--color-haplotype-unknown");
    const deletedFill = cssVar("--color-haplotype-deleted-fill");
    const deletedStroke = cssVar("--color-haplotype-deleted-stroke");

    const sample = data?.samples.find((sampleEntry) => sampleEntry.sample === sampleId);
    const segments = sample?.segments || [];
    const span = regionEnd - regionStart || 1;
    const half = height / 2;
    const affectedColor = affectedColors[disorder];

    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();

    const g = svg.append("g");

    const recombXs: number[] = [];

    segments.forEach((seg: Segment, idx: number) => {
      const x1 = ((seg.start - regionStart) / span) * width;
      const x2 = ((seg.end - regionStart) / span) * width;
      const w = Math.max(x2 - x1, 1);
      let c1: string;
      let c2: string;
      const h1 = parseInt(seg.hap1, 10);
      const h2 = parseInt(seg.hap2, 10);
      if (isDeletedHaplotype(seg.hap1)) {
        c1 = deletedFill;
      } else if (role === "father") {
        c1 = isNaN(h1) ? unknownColor : fatherColors[h1] || unknownColor;
      } else if (role === "mother") {
        c1 = isNaN(h1) ? unknownColor : motherColors[h1] || unknownColor;
      } else {
        c1 = isNaN(h1) ? unknownColor : fatherColors[h1] || unknownColor;
      }
      if (isDeletedHaplotype(seg.hap2)) {
        c2 = deletedFill;
      } else if (role === "father") {
        c2 = isNaN(h2) ? unknownColor : fatherColors[h2] || unknownColor;
      } else if (role === "mother") {
        c2 = isNaN(h2) ? unknownColor : motherColors[h2] || unknownColor;
      } else {
        c2 = isNaN(h2) ? unknownColor : motherColors[h2] || unknownColor;
      }
      if (affected) {
        if (seg.hap1 === "1") c1 = affectedColor;
        if (seg.hap2 === "1") c2 = affectedColor;
      }
      if (idx > 0) {
        const prev = segments[idx - 1];
        if (seg.hap1 !== prev.hap1 || seg.hap2 !== prev.hap2) {
          recombXs.push(x1);
        }
      }
      const grp = g.append("g");
      grp
        .append("rect")
        .attr("x", x1)
        .attr("y", 0)
        .attr("width", w)
        .attr("height", half - 1)
        .attr("fill", c1);
      if (isDeletedHaplotype(seg.hap1)) {
        grp
          .append("line")
          .attr("x1", x1 + 0.75)
          .attr("y1", 1)
          .attr("x2", x2 - 0.75)
          .attr("y2", half - 2)
          .attr("stroke", deletedStroke)
          .attr("stroke-width", 1);
      }
      grp
        .append("rect")
        .attr("x", x1)
        .attr("y", half + 1)
        .attr("width", w)
        .attr("height", half - 1)
        .attr("fill", c2);
      if (isDeletedHaplotype(seg.hap2)) {
        grp
          .append("line")
          .attr("x1", x1 + 0.75)
          .attr("y1", half + 2)
          .attr("x2", x2 - 0.75)
          .attr("y2", height - 1)
          .attr("stroke", deletedStroke)
          .attr("stroke-width", 1);
      }
    });

    recombXs.forEach((x) => {
      g.append("line")
        .attr("x1", x)
        .attr("x2", x)
        .attr("y1", 0)
        .attr("y2", height)
        .attr("stroke", cssVar("--color-axis"))
        .attr("stroke-dasharray", "4 2");
    });

  }, [
    data,
    sampleId,
    regionStart,
    regionEnd,
    width,
    height,
    role,
    affected,
    disorder,
    isLoading,
  ]);

  const sample = data?.samples.find((sampleEntry) => sampleEntry.sample === sampleId);
  const hasSegments = (sample?.segments.length || 0) > 0;

  return (
    <div className="relative" style={{ width, height }}>
      <svg ref={svgRef} width={width} height={height} />
      {isLoading && <VizLoadingOverlay message="Loading haplotypes" />}
      {!isLoading && !hasSegments && (
        <div className="viz-empty-overlay">No haplotype data in this region</div>
      )}
    </div>
  );
};

export default HaplotypeTrack;
