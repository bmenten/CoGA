import React from "react";
import { useQuery } from "@tanstack/react-query";
import api from "../../lib/api";

interface ReadTrackProps {
  sampleId: string;
  chrom: string;
  start: number;
  end: number;
}

interface Read {
  pos: number;
  seq: string;
}

const ReadTrack: React.FC<ReadTrackProps> = ({ sampleId, chrom, start, end }) => {
  const enabled = end > start && end - start <= 200;
  const { data } = useQuery<{ reads: Read[] }>({
    queryKey: ["reads", sampleId, chrom, start, end],
    queryFn: async () => {
      const res = await api.get(`/reference/reads/${sampleId}`, {
        params: { chrom, start, end },
      });
      return res.data as { reads: Read[] };
    },
    enabled,
  });

  if (!enabled || !data || data.reads.length === 0) return null;

  return (
    <div className="font-mono text-[10px] leading-snug">
      {data.reads.map((r, i) => (
        <div key={i} className="whitespace-nowrap">
          {r.seq}
        </div>
      ))}
    </div>
  );
};

export default ReadTrack;
