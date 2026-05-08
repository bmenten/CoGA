import React from "react";
import { useQuery } from "@tanstack/react-query";
import api from "../../lib/api";

interface SequenceTrackProps {
  chrom: string;
  start: number;
  end: number;
}

const SequenceTrack: React.FC<SequenceTrackProps> = ({ chrom, start, end }) => {
  const enabled = end > start && end - start <= 200;
  const { data } = useQuery<{ sequence: string }>({
    queryKey: ["sequence", chrom, start, end],
    queryFn: async () => {
      const res = await api.get(`/reference/sequence`, {
        params: { chrom, start, end },
      });
      return res.data as { sequence: string };
    },
    enabled,
  });

  if (!enabled || !data) return null;

  return (
    <div className="font-mono text-xs whitespace-nowrap overflow-x-auto">
      {data.sequence}
    </div>
  );
};

export default SequenceTrack;
