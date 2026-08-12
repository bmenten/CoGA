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

describe('SvSecondHitBadge navigation', () => {
  const hit = {
    sv_count: 2,
    sv_types: ['DEL'],
    affected_zygosity: 'het',
    has_deletion: true,
    phase: 'trans' as const,
    deletion_unmasked: true,
  };

  it('links to the gene-filtered SV list in a new tab when given an href', () => {
    render(<SvSecondHitBadge hit={hit} href="/families/F1/structural-variants?gene=TRNT1" />);
    const link = screen.getByRole('link');
    expect(link).toHaveAttribute('href', '/families/F1/structural-variants?gene=TRNT1');
    // New tab, so the filtered small-variant list behind it survives.
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noreferrer');
    expect(link).toHaveClass('sv-second-hit-badge--link');
  });

  it('stays a plain badge when there is nowhere to go', () => {
    const { container } = render(<SvSecondHitBadge hit={hit} />);
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    const badge = container.querySelector('.sv-second-hit-badge') as HTMLElement;
    expect(badge.classList.contains('sv-second-hit-badge--link')).toBe(false);
  });

  it('says where the click goes, since a badge does not look like a link', () => {
    render(<SvSecondHitBadge hit={hit} href="/families/F1/structural-variants?gene=TRNT1" />);
    fireEvent.mouseEnter(screen.getByRole('link'));
    expect(screen.getByRole('tooltip').textContent).toContain('in a new tab');
    // The explanation of the pattern itself is still there.
    expect(screen.getByRole('tooltip').textContent).toContain('effectively biallelic');
  });

  it('leaves the tooltip free of navigation wording when it is not a link', () => {
    const { container } = render(<SvSecondHitBadge hit={hit} />);
    fireEvent.mouseEnter(container.querySelector('.sv-second-hit-badge') as HTMLElement);
    expect(screen.getByRole('tooltip').textContent).not.toContain('in a new tab');
  });
});
