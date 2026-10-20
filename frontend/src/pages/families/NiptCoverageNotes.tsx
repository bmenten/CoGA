import React from 'react';

/**
 * Why each of the plasma's coverage tracks over the regions was not read as depth (TRACE-9):
 * a track of log2 ratios, or a coverage BED that does not declare whether it holds read depth
 * or a log2 ratio. Shown with the coverage check, so regions without coverage say why.
 */
const NiptCoverageNotes: React.FC<{ notes?: string[] | null }> = ({ notes }) =>
  notes && notes.length ? (
    <div className="space-y-1" role="note" aria-label="Coverage tracks not read as depth">
      {notes.map((note) => (
        <p key={note} className="status-note status-note--warning">
          {note}
        </p>
      ))}
    </div>
  ) : null;

export default NiptCoverageNotes;
