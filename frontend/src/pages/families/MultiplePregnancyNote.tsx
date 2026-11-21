import React from 'react';

/**
 * What a NIPT result of a multiple pregnancy says of its fetuses (backend
 * `nipt.multiple_pregnancy_note`): shown on the NIPT and cNIPT pages, their reports and the signed
 * record whenever the pregnancy's package names two or more fetuses, whatever their zygosity (the
 * owner's decision of 2026-11-21). Nothing for a singleton pregnancy.
 */
const MultiplePregnancyNote: React.FC<{ note: string | null | undefined; fetuses?: string[] }> = ({ note, fetuses }) =>
  note ? (
    <p className="status-note status-note--warning" role="note" data-testid="multiple-pregnancy-note">
      <strong>Multiple pregnancy</strong>
      {fetuses?.length ? ` (${fetuses.join(', ')})` : ''}: {note}
    </p>
  ) : null;

export default MultiplePregnancyNote;
