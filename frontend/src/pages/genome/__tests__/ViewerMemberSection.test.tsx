// Pins the per-member panel of the genome viewers: the sample id heading, a star titled
// "Affected" for affected members only, the pedigree role, and the member's tracks inside.
import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import ViewerMemberSection from '../ViewerMemberSection';
import type { ApiFamilyMember } from '../../../lib/apiTypes';

const PROBAND: ApiFamilyMember = { sample_id: 'S1', role: 'proband', affected: true, sex: 'male' };
const MOTHER: ApiFamilyMember = { sample_id: 'S2', role: 'mother', affected: false, sex: 'female' };

describe('ViewerMemberSection', () => {
  it('heads the panel with the sample id and role, and renders the member’s tracks inside it', () => {
    render(
      <ViewerMemberSection member={MOTHER}>
        <p>Coverage track for S2</p>
      </ViewerMemberSection>,
    );

    expect(screen.getByRole('heading', { level: 3 })).toHaveTextContent(/^S2$/);
    expect(screen.getByText('mother')).toBeInTheDocument();
    expect(screen.getByText('Coverage track for S2')).toBeInTheDocument();
  });

  it('marks an affected member with a star titled "Affected"', () => {
    render(
      <ViewerMemberSection member={PROBAND}>
        <p>Coverage track for S1</p>
      </ViewerMemberSection>,
    );

    const heading = screen.getByRole('heading', { level: 3 });
    expect(heading).toHaveTextContent(/^S1★$/);
    expect(within(heading).getByTitle('Affected')).toHaveTextContent('★');
    expect(screen.getByText('proband')).toBeInTheDocument();
  });

  it('shows no affected marker for an unaffected member', () => {
    render(
      <ViewerMemberSection member={MOTHER}>
        <p>Coverage track for S2</p>
      </ViewerMemberSection>,
    );

    expect(screen.queryByTitle('Affected')).not.toBeInTheDocument();
    expect(screen.queryByText('★')).not.toBeInTheDocument();
  });
});
