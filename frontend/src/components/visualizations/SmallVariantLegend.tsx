import React from 'react';
import {
  SMALL_VARIANT_MARKS,
  SMALL_VARIANT_MARK_ORDER,
  smallVariantMarkPath,
} from '../../lib/smallVariantMarks';
import { SMALL_VARIANT_DOT_RADIUS } from '../../lib/trackSampling';

// The track's own size.
const LEGEND_RADIUS = SMALL_VARIANT_DOT_RADIUS;

const Swatch: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <svg width="14" height="14" viewBox="-7 -7 14 14" aria-hidden="true" focusable="false">
    {children}
  </svg>
);

/** The key to the small-variant marks: the P/LP diamond, the coloured dots and the tag ring. */
const SmallVariantLegend: React.FC = () => (
  <span className="small-variant-legend" role="group" aria-label="Small-variant marks">
    {SMALL_VARIANT_MARK_ORDER.map((kind) => {
      const style = SMALL_VARIANT_MARKS[kind];
      return (
        <span key={kind} className="small-variant-legend-item" data-legend-mark={kind}>
          <Swatch>
            <path d={smallVariantMarkPath(kind, LEGEND_RADIUS)} fill={style.color} />
          </Swatch>
          {style.label}
        </span>
      );
    })}
    <span className="small-variant-legend-item" data-legend-mark="tagged">
      <Swatch>
        <circle r={LEGEND_RADIUS + 2.6} fill="none" stroke="currentColor" strokeWidth={1.2} />
        <circle r={LEGEND_RADIUS} fill={SMALL_VARIANT_MARKS.other.color} />
      </Swatch>
      Ring: review tag, in its colour
    </span>
  </span>
);

export default SmallVariantLegend;
