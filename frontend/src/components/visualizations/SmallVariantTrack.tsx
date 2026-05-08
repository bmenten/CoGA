import React from 'react';
import { useQuery } from '@tanstack/react-query';
import * as d3 from 'd3';
import api from '../../lib/api';
import type { ApiVariantPage } from '../../lib/apiTypes';
import { formatGt } from '../../lib/genotypes';
import { cssVar } from '../../lib/colors';
import { getTrackVariantLimit } from '../../lib/trackSampling';
import VizLoadingOverlay from './VizLoadingOverlay';

interface Genotype {
  sample: string;
  gt: string;
}

interface Variant {
  chr: string;
  start: number;
  end: number;
  type: string;
  ref?: string;
  alt?: string;
  genotypes?: Genotype[];
}

interface Props {
  familyId: string;
  sampleId: string;
  chrom: string;
  regionStart: number;
  regionEnd: number;
  width: number;
  height: number;
  filters?: Record<string, string>;
}

const NON_REFERENCE_GENOTYPES = ['0/1', '1/0', '0|1', '1|0', '1/1', '1|1'];

const samplePresenceFilter = (sampleId: string) =>
  `${sampleId}:${NON_REFERENCE_GENOTYPES.join('|')}`;

const SmallVariantTrack: React.FC<Props> = ({
  familyId,
  sampleId,
  chrom,
  regionStart,
  regionEnd,
  width,
  height,
  filters,
}) => {
  const pageSize = React.useMemo(() => getTrackVariantLimit(width), [width]);
  const requestFilters = React.useMemo(() => {
    const nextFilters = { ...(filters || {}) };
    if (!nextFilters.sample_filter) {
      nextFilters.sample_filter = samplePresenceFilter(sampleId);
    }
    return nextFilters;
  }, [filters, sampleId]);
  const { data, isLoading } = useQuery<ApiVariantPage<Variant>>({
    queryKey: [
      'small-variants-track',
      familyId,
      sampleId,
      chrom,
      regionStart,
      regionEnd,
      pageSize,
      requestFilters,
    ],
    queryFn: async () => {
      const params: Record<string, any> = {
        chr: chrom,
        start: regionStart,
        end: regionEnd,
        overlap: true,
        page_size: pageSize,
        track_mode: true,
        ...requestFilters,
      };
      const res = await api.get(`/families/${familyId}/small-variants`, {
        params,
      });
      return res.data as ApiVariantPage<Variant>;
    },
    enabled: regionEnd > regionStart,
  });

  const variants = React.useMemo(
    () =>
      (data?.variants || []).filter((v) =>
        v.genotypes?.some(
          (g) => g.sample === sampleId && formatGt(g.gt) !== 'WT'
        )
      ),
    [data?.variants, sampleId]
  );

  const span = regionEnd - regionStart || 1;
  const withPos = React.useMemo(
    () =>
      variants.map((v) => {
        const x = ((v.start - regionStart) / span) * width;
        return { ...v, x };
      }),
    [variants, regionStart, span, width]
  );

  const typeColors = React.useMemo<Record<string, string>>(
    () => ({
      SNV: cssVar('--color-variant-default'),
      INDEL: cssVar('--color-variant-ins'),
      DEL: cssVar('--color-variant-del'),
      INS: cssVar('--color-variant-ins'),
    }),
    []
  );

  const svgRef = React.useRef<SVGSVGElement | null>(null);

  React.useEffect(() => {
    if (isLoading) {
      return;
    }

    const svg = d3.select(svgRef.current);
    svg.selectAll('*').remove();

    if (withPos.length === 0) {
      svg
        .append('text')
        .attr('x', 4)
        .attr('y', height / 2 + 4)
        .attr('font-size', 12)
        .attr('fill', cssVar('--color-variant-default'))
        .text('no small variants for this region / sample');
      return;
    }

    const g = svg.append('g');
    withPos.forEach((v) => {
      const color =
        typeColors[v.type?.toUpperCase()] || cssVar('--color-variant-default');
      g
        .append('line')
        .attr('x1', v.x)
        .attr('x2', v.x)
        .attr('y1', 0)
        .attr('y2', height)
        .attr('stroke', color);
    });
  }, [withPos, height, typeColors, isLoading]);

  return (
    <div className="relative" style={{ width, height }}>
      <svg ref={svgRef} width={width} height={height} />
      {isLoading && <VizLoadingOverlay message="Loading small variants" />}
    </div>
  );
};

export default SmallVariantTrack;
