import React from 'react';

/**
 * A coverage track's note, in its head: why it is drawn on the axis it is (TRACE-9), as
 * `coverageScaleNote` words it. A track whose values are of unknown kind says so in the
 * warning tone, so it is not read as depth or as a log2 ratio.
 */
const CoverageScaleNote: React.FC<{ note: { text: string; warning: boolean } | null }> = ({ note }) =>
  note ? (
    <span className={`viz-scale-note${note.warning ? ' viz-scale-note--warning' : ''}`} role="note">
      {note.text}
    </span>
  ) : null;

export default CoverageScaleNote;
