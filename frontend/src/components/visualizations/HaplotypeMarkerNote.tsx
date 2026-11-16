import React from 'react';
import { PHASED_MARKER_MAX_SPAN, formatSpanLimit } from '../../lib/trackSampling';
import { usePhasedMarkers } from '../../lib/usePhasedMarkers';

// A child's Mendel-error rate above this fraction of informative sites is a real
// signal (likely sample swap / wrong pedigree), not phasing noise; below it we stay
// silent so a stray imputation glitch never raises a false alarm in a clinical view.
export const MENDEL_WARN_RATE = 0.02;

interface Props {
  familyId: string;
  sampleId: string;
  chrom: string;
  regionStart: number;
  regionEnd: number;
  /** The track draws the phased markers in this window. */
  markersShown: boolean;
  /** The markers are on, but the window is too wide to draw them. */
  zoomedOut: boolean;
}

const percent = (rate: number): string => `${(rate * 100).toFixed(1)}%`;

/**
 * What the haplotype track's head says next to its name: the member's informative sites in the
 * window, or the Mendelian errors among them when their rate points to a sample swap or a wrong
 * pedigree; or, in a window too wide to draw the markers, that they are drawn when zoomed in.
 * Read from the markers the track draws (one shared request), only for the couple's children,
 * whose QC the server counts, and never from a truncated fetch, whose counts would cover only
 * part of the window.
 */
const HaplotypeMarkerNote: React.FC<Props> = ({
  familyId,
  sampleId,
  chrom,
  regionStart,
  regionEnd,
  markersShown,
  zoomedOut,
}) => {
  const { data } = usePhasedMarkers({ familyId, chrom, regionStart, regionEnd, enabled: markersShown });

  if (zoomedOut) {
    const limit = formatSpanLimit(PHASED_MARKER_MAX_SPAN);
    return (
      <span
        className="viewer-track-note"
        title={`The raw phased markers and the informative sites are drawn in a window of ${limit} or less.`}
      >
        Phased markers: zoom in to {limit} or less
      </span>
    );
  }
  if (!markersShown || !data || data.truncated) return null;
  const qc = data.samples?.find((entry) => entry.sample === sampleId)?.qc;
  if (!qc) return null;

  const sites = qc.informative_sites.toLocaleString();
  if (qc.mendel_rate > MENDEL_WARN_RATE) {
    return (
      <span
        className="viewer-track-note viewer-track-note--warning"
        role="note"
        title={
          `${qc.mendel_errors.toLocaleString()} Mendelian inconsistencies in ${sites} informative sites ` +
          `(${percent(qc.mendel_rate)}): check the sample identity and the pedigree.`
        }
      >
        ⚠ {qc.mendel_errors.toLocaleString()} Mendelian errors in {sites} informative sites ({percent(qc.mendel_rate)})
      </span>
    );
  }
  return (
    <span
      className="viewer-track-note"
      title={
        'Sites in this window where this child and its parents have a phased genotype; ' +
        `${qc.mendel_errors.toLocaleString()} of them Mendelian errors (${percent(qc.mendel_rate)}).`
      }
    >
      {sites} informative sites
    </span>
  );
};

export default HaplotypeMarkerNote;
