import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import SvSecondHitBadge from '../SvSecondHitBadge';

describe('SvSecondHitBadge', () => {
  it('renders nothing when there is no second hit', () => {
    const { container } = render(<SvSecondHitBadge hit={null} />);
    expect(container.firstChild).toBeNull();
  });

  it('flags a deletion in trans as biallelic and reveals the tooltip on hover', () => {
    const { container } = render(
      <SvSecondHitBadge
        hit={{
          sv_count: 2,
          sv_types: ['DEL', 'INS'],
          affected_zygosity: 'het',
          has_deletion: true,
          phase: 'trans',
          deletion_unmasked: true,
        }}
      />,
    );
    const badge = container.querySelector('.sv-second-hit-badge') as HTMLElement;
    expect(badge).not.toBeNull();
    expect(badge.classList.contains('sv-second-hit-badge--del')).toBe(true);
    expect(badge.classList.contains('sv-second-hit-badge--unmasked')).toBe(true);
    expect(screen.getByText(/SV: DEL, INS/)).toBeInTheDocument();
    expect(screen.getByText('trans')).toBeInTheDocument();

    expect(screen.queryByRole('tooltip')).toBeNull();
    fireEvent.mouseEnter(badge);
    expect(screen.getByRole('tooltip').textContent).toMatch(/effectively biallelic/i);
    fireEvent.mouseLeave(badge);
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('marks read-based phasing distinctly and says so in the tooltip', () => {
    const { container } = render(
      <SvSecondHitBadge
        hit={{
          sv_count: 1,
          sv_types: ['DEL'],
          affected_zygosity: 'het',
          has_deletion: true,
          phase: 'trans',
          phase_evidence: 'read',
          deletion_unmasked: true,
        }}
      />,
    );
    const phaseChip = container.querySelector('.sv-second-hit-phase') as HTMLElement;
    expect(phaseChip.classList.contains('sv-second-hit-phase--read')).toBe(true);
    fireEvent.mouseEnter(container.querySelector('.sv-second-hit-badge') as HTMLElement);
    expect(screen.getByRole('tooltip').textContent).toMatch(/by read phasing/i);
  });

  it('shows the cis phase and tooltip for a duplication-only hit, on focus', () => {
    const { container } = render(
      <SvSecondHitBadge
        hit={{
          sv_count: 1,
          sv_types: ['DUP'],
          affected_zygosity: 'het',
          has_deletion: false,
          phase: 'cis',
          deletion_unmasked: false,
        }}
      />,
    );
    const badge = container.querySelector('.sv-second-hit-badge') as HTMLElement;
    expect(badge.classList.contains('sv-second-hit-badge--del')).toBe(false);
    expect(screen.getByText('cis')).toBeInTheDocument();
    fireEvent.focus(badge);
    expect(screen.getByRole('tooltip').textContent).toMatch(/in cis \(the same allele/i);
  });
});
