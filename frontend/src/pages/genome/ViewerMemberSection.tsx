import React from 'react';
import type { ApiFamilyMember } from '../../lib/apiTypes';

interface ViewerMemberSectionProps {
  member: ApiFamilyMember;
  children: React.ReactNode;
}

const ViewerMemberSection: React.FC<ViewerMemberSectionProps> = ({ member, children }) => (
  <div className="viz-panel">
    <div className="analysis-toolbar mb-4 justify-between">
      <h3 className="text-lg font-semibold">
        {member.sample_id}
        {member.affected && (
          <span className="ml-1 text-[var(--color-signature-red)]" title="Affected">
            ★
          </span>
        )}
      </h3>
      <span className="analysis-pill analysis-pill--muted">{member.role}</span>
    </div>
    {children}
  </div>
);

export default ViewerMemberSection;
