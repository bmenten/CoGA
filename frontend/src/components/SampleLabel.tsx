import React from 'react';

/**
 * A member's sample ID and role on one line, as each finding page lists a family's calls:
 * the ID in bold, the role after it, muted. `emphasis` darkens the ID for a member whose
 * call is abnormal.
 */
const SampleLabel: React.FC<{
  sampleId: string;
  role?: string | null;
  emphasis?: boolean;
  className?: string;
}> = ({ sampleId, role, emphasis = false, className }) => (
  <span
    className={`sample-label${emphasis ? ' sample-label--emphasis' : ''}${
      className ? ` ${className}` : ''
    }`}
  >
    <strong className="sample-label-id">{sampleId}</strong>
    {role && <span className="sample-label-role">{role}</span>}
  </span>
);

export default SampleLabel;
