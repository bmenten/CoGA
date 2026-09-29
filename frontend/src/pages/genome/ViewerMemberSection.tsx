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
          <>
            {/* The star is decoration a screen reader would read as "black star"; it
                hears the word instead (#529). */}
            <span className="ml-1 text-(--color-signature-red)" title="Affected" aria-hidden="true">
              ★
            </span>
            <span className="sr-only">, affected</span>
          </>
        )}
      </h3>
      <span className="analysis-pill analysis-pill--muted">{member.role}</span>
    </div>
    {children}
  </div>
);

export default ViewerMemberSection;
