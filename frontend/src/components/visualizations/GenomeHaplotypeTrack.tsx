import React, { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { cssVar } from "../../lib/colors";
import { storage } from "../../lib/storage";
import VizLoadingOverlay from "./VizLoadingOverlay";

const DEFAULT_CHROMS = [
  ...Array.from({ length: 22 }, (_, i) => String(i + 1)),
  "X",
  "Y",
];

interface Segment {
  chr: string;
  start: number;
  end: number;
  hap1: string;
  hap2: string;
}

interface HaplotypeSourceSample {
  sample: string;
  segments: Array<Segment | Omit<Segment, 'chr'>>;
}

interface HaplotypeSourceResponse {
  samples?: HaplotypeSourceSample[];
}

interface Layout {
  offsets: Record<string, number>;
  lengths: Record<string, number>;
  total: number;
  chroms: string[];
}

interface Props {
  urls: string[];
  sampleId: string;
  role: string;
  affected: boolean;
  layout: Layout | null;
  width?: number;
  height?: number;
  disorder?: "dominant" | "recessive";
  chroms?: string[];
}

const isDeletedHaplotype = (value: string): boolean => value === ".";

const GenomeHaplotypeTrack: React.FC<Props> = ({
  urls,
  sampleId,
  role,
  affected,
  layout,
  width = 800,
  height = 40,
  disorder = "dominant",
  chroms = DEFAULT_CHROMS,
}) => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const { data: segmentMap = {}, isLoading } = useQuery<Record<string, Segment[]>>({
    queryKey: ["genome-haplotypes", urls.join(","), chroms.join(",")],
    queryFn: async () => {
      if (!layout) return {};
      const headers: Record<string, string> = {};
      const token = storage.getItem("token");
      if (token) headers.Authorization = `Bearer ${token}`;
      const responses = await Promise.all(
        urls.map((u) =>
          fetch(u, { headers })
            .then((res) => (res.ok ? (res.json() as Promise<HaplotypeSourceResponse>) : null))
            .catch(() => null)
        )
      );
      const map: Record<string, Segment[]> = {};
      responses.forEach((j, idx) => {
        if (!j) return;
        (j.samples || []).forEach((s: HaplotypeSourceSample) => {
          const arr = map[s.sample] || [];
          (s.segments || []).forEach((seg) => {
            const chrom = ("chr" in seg && seg.chr ? seg.chr : undefined) || chroms[idx];
            if (!chrom) return;
            arr.push({ ...seg, chr: chrom });
          });
          map[s.sample] = arr;
        });
      });
      return map;
    },
    enabled: !!layout && urls.length > 0,
    staleTime: Infinity,
    gcTime: Infinity,
  });

  const segments = segmentMap[sampleId] || [];

  useEffect(() => {
    if (isLoading) return;
    if (!layout || !canvasRef.current) return;
    const ctx = canvasRef.current.getContext("2d");
    if (!ctx) return;

    canvasRef.current.width = width;
    canvasRef.current.height = height;
    ctx.clearRect(0, 0, width, height);

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

    const half = height / 2;
    const affectedColor = affectedColors[disorder];

    const recombXs: number[] = [];
    const prevByChr: Record<string, Segment> = {};

    segments.forEach((seg) => {
      const chr = seg.chr;
      const offset = layout.offsets[chr];
      if (offset === undefined) return;
      const x1 = ((offset + seg.start) / layout.total) * width;
      const x2 = ((offset + seg.end) / layout.total) * width;
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
      const prev = prevByChr[chr];
      if (prev && (seg.hap1 !== prev.hap1 || seg.hap2 !== prev.hap2)) {
        recombXs.push(x1);
      }
      prevByChr[chr] = seg;
      ctx.fillStyle = c1;
      ctx.fillRect(x1, 0, w, half - 1);
      if (isDeletedHaplotype(seg.hap1)) {
        ctx.strokeStyle = deletedStroke;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(x1 + 0.75, 1);
        ctx.lineTo(x2 - 0.75, half - 2);
        ctx.stroke();
      }
      ctx.fillStyle = c2;
      ctx.fillRect(x1, half + 1, w, half - 1);
      if (isDeletedHaplotype(seg.hap2)) {
        ctx.strokeStyle = deletedStroke;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(x1 + 0.75, half + 2);
        ctx.lineTo(x2 - 0.75, height - 1);
        ctx.stroke();
      }
    });

    ctx.strokeStyle = cssVar("--color-axis");
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 2]);
    recombXs.forEach((x) => {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, height);
      ctx.stroke();
    });
    ctx.setLineDash([]);
  }, [segments, role, affected, layout, width, height, disorder, chroms, isLoading]);

  return (
    <div className="relative" style={{ width, height }}>
      <canvas ref={canvasRef} />
      {isLoading && <VizLoadingOverlay message="Loading haplotypes" />}
      {!isLoading && layout && segments.length === 0 && (
        <div className="viz-empty-overlay">No haplotype data</div>
      )}
    </div>
  );
};

export default GenomeHaplotypeTrack;
