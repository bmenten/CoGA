import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import type { SvSecondHitSvOut } from '../../../lib/apiSchema.generated';
import SvSecondHitBadge from '../SvSecondHitBadge';
import type { SvSecondHit } from '../smallVariantSearch';

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

  it('shows no phase when none was decided, as for a half call the reads cannot place', () => {
    // The backend reports a deletion next to a half-called SV (`.|1`) in a singleton as
    // phase unknown: no chip, no read-phasing mark, and nothing biallelic.
    const { container } = render(
      <SvSecondHitBadge
        hit={{
          sv_count: 1,
          sv_types: ['DEL'],
          affected_zygosity: 'het',
          has_deletion: true,
          phase: 'unknown',
          phase_evidence: null,
          deletion_unmasked: false,
        }}
      />,
    );
    const badge = container.querySelector('.sv-second-hit-badge') as HTMLElement;
    expect(badge.textContent).toBe('SV: DEL');
    expect(container.querySelector('.sv-second-hit-phase')).toBeNull();
    expect(badge.classList.contains('sv-second-hit-badge--del')).toBe(true);
    expect(badge.classList.contains('sv-second-hit-badge--unmasked')).toBe(false);
    fireEvent.mouseEnter(badge);
    const tooltip = screen.getByRole('tooltip').textContent ?? '';
    expect(tooltip).toBe(
      'This gene is also hit by a structural variant (DEL · het) — a possible cross-type second hit.',
    );
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

  it('is a button without a link, so the keyboard reaches its explanation (#529)', () => {
    render(<SvSecondHitBadge hit={hit} />);
    const badge = screen.getByRole('button', { name: /effectively biallelic/i });
    expect(badge).toHaveAttribute('type', 'button');
    fireEvent.focus(badge);
    expect(screen.getByRole('tooltip').textContent).toMatch(/effectively biallelic/i);
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

// CLIN-28: each SV of the gene has its own phase against the SNV (`svs`). The chip shows the
// gene's verdict, drawn from theirs by the backend, and the tooltip lists each SV.
describe('SvSecondHitBadge with several SVs in the gene', () => {
  const sv = (overrides: Partial<SvSecondHitSvOut>): SvSecondHitSvOut => ({
    sv_id: 'DEL-5000',
    sv_type: 'DEL',
    chr: '1',
    start: 5000,
    end: 9000,
    affected_zygosity: 'het',
    copy_loss: true,
    phase: 'unknown',
    phase_evidence: null,
    ...overrides,
  });
  const dup = (overrides: Partial<SvSecondHitSvOut>) =>
    sv({ sv_id: 'DUP-2000', sv_type: 'DUP', start: 2000, end: 3000, copy_loss: false, ...overrides });
  const span = (start: number, end: number) =>
    `chr1:${start.toLocaleString()}-${end.toLocaleString()}`;
  const DEL_AT = span(5000, 9000);
  const DUP_AT = span(2000, 3000);

  const gene = (overrides: Partial<SvSecondHit>): SvSecondHit => ({
    sv_count: 2,
    sv_types: ['DEL', 'DUP'],
    affected_zygosity: 'het',
    has_deletion: true,
    phase: 'unknown',
    phase_evidence: null,
    deletion_unmasked: false,
    ...overrides,
  });

  const hover = (container: HTMLElement) => {
    const badge = container.querySelector('.sv-second-hit-badge') as HTMLElement;
    fireEvent.mouseEnter(badge);
    return { badge, tooltip: screen.getByRole('tooltip').textContent ?? '' };
  };

  it('headlines a deletion in trans beside a duplication in cis, and lists each SV', () => {
    const { container } = render(
      <SvSecondHitBadge
        hit={gene({
          phase: 'trans',
          phase_evidence: 'read',
          deletion_unmasked: true,
          // By position, as the backend lists them; the tooltip puts the deletion first.
          svs: [
            dup({ phase: 'cis', phase_evidence: 'read' }),
            sv({ phase: 'trans', phase_evidence: 'read' }),
          ],
        })}
      />,
    );
    const { badge, tooltip } = hover(container);
    expect(badge.classList.contains('sv-second-hit-badge--unmasked')).toBe(true);
    expect(container.querySelector('.sv-second-hit-phase')?.textContent).toBe('trans✓');
    expect(tooltip).toBe(
      'This gene is also hit by 2 structural variants (DEL, DUP · het), among them a deletion ' +
        'in trans by read phasing with this heterozygous SNV — the deletion removes the other ' +
        'allele, so the gene is effectively biallelic. ' +
        `Each SV: DEL ${DEL_AT} (het), in trans by read phasing; ` +
        `DUP ${DUP_AT} (het), in cis by read phasing.`,
    );
  });

  it('shows a duplication in trans beside a deletion in cis as a candidate, not biallelic', () => {
    const { container } = render(
      <SvSecondHitBadge
        hit={gene({
          phase: 'trans',
          phase_evidence: 'read',
          svs: [
            dup({ phase: 'trans', phase_evidence: 'read' }),
            sv({ phase: 'cis', phase_evidence: 'read' }),
          ],
        })}
      />,
    );
    const { badge, tooltip } = hover(container);
    expect(badge.classList.contains('sv-second-hit-badge--unmasked')).toBe(false);
    expect(badge.classList.contains('sv-second-hit-badge--del')).toBe(true);
    expect(tooltip).toBe(
      'This gene is also hit by 2 structural variants (DEL, DUP · het) — possible cross-type ' +
        'second hits. At least one is in trans (on the other allele) by read phasing — a ' +
        'compound-heterozygous candidate. ' +
        `Each SV: DUP ${DUP_AT} (het), in trans by read phasing; ` +
        `DEL ${DEL_AT} (het), in cis by read phasing.`,
    );
  });

  it('says cis only of every SV the affected members carry', () => {
    const { container } = render(
      <SvSecondHitBadge
        hit={gene({
          phase: 'cis',
          phase_evidence: 'segregation',
          svs: [
            dup({ phase: 'cis', phase_evidence: 'read' }),
            sv({ phase: 'cis', phase_evidence: 'segregation' }),
          ],
        })}
      />,
    );
    const { tooltip } = hover(container);
    // One of the two is in cis by segregation, so the chip claims no read phasing.
    expect(container.querySelector('.sv-second-hit-phase')?.textContent).toBe('cis');
    expect(tooltip).toContain(
      'Every one the affected members carry is in cis (the same allele as this SNV) by segregation.',
    );
    expect(tooltip).toContain(
      `Each SV: DUP ${DUP_AT} (het), in cis by read phasing; DEL ${DEL_AT} (het), in cis by segregation.`,
    );
  });

  it('shows no chip when the deletion has no phase beside a duplication in cis', () => {
    // The duplication's cis no longer stands for the gene: the deletion may be in trans.
    const { container } = render(
      <SvSecondHitBadge
        hit={gene({ svs: [dup({ phase: 'cis', phase_evidence: 'read' }), sv({})] })}
      />,
    );
    const { badge, tooltip } = hover(container);
    expect(container.querySelector('.sv-second-hit-phase')).toBeNull();
    expect(badge.textContent).toBe('SV: DEL, DUP ×2');
    expect(tooltip).toBe(
      'This gene is also hit by 2 structural variants (DEL, DUP · het) — possible cross-type ' +
        `second hits. Each SV: DUP ${DUP_AT} (het), in cis by read phasing; ` +
        `DEL ${DEL_AT} (het), phase unknown.`,
    );
  });

  it('lists an SV no affected member carries last, without a phase', () => {
    const { container } = render(
      <SvSecondHitBadge
        hit={gene({
          phase: 'cis',
          phase_evidence: 'read',
          svs: [
            sv({ sv_id: 'DEL-1000', start: 1000, end: 1500, affected_zygosity: null }),
            dup({ phase: 'cis', phase_evidence: 'read' }),
          ],
        })}
      />,
    );
    const { tooltip } = hover(container);
    expect(tooltip).toContain(
      `Each SV: DUP ${DUP_AT} (het), in cis by read phasing; ` +
        `DEL ${span(1000, 1500)}, not carried by an affected member.`,
    );
  });

  it('names the five most telling SVs and counts the rest', () => {
    const many = [
      ...Array.from({ length: 5 }, (_, index) =>
        dup({ sv_id: `DUP-${index}`, start: 1000 * (index + 1), end: 1000 * (index + 1) + 500, phase: 'cis', phase_evidence: 'read' }),
      ),
      sv({ sv_id: 'DEL-90000', start: 90000, end: 95000, phase: 'trans', phase_evidence: 'read' }),
      sv({ sv_id: 'DEL-99000', start: 99000, end: 99500, affected_zygosity: null }),
    ];
    const { container } = render(
      <SvSecondHitBadge
        hit={gene({ sv_count: 7, phase: 'trans', phase_evidence: 'read', deletion_unmasked: true, svs: many })}
      />,
    );
    const { tooltip } = hover(container);
    // The deletion in trans is named first, though it is listed sixth by position.
    expect(tooltip).toContain(`Each SV: DEL ${span(90000, 95000)} (het), in trans by read phasing; DUP`);
    expect(tooltip).toMatch(/; and 2 more\.$/);
    expect(tooltip).not.toContain(span(99000, 99500));
  });

  it('lists nothing more for a gene hit by one SV', () => {
    const { container } = render(
      <SvSecondHitBadge
        hit={gene({
          sv_count: 1,
          sv_types: ['DEL'],
          phase: 'trans',
          phase_evidence: 'read',
          deletion_unmasked: true,
          svs: [sv({ phase: 'trans', phase_evidence: 'read' })],
        })}
      />,
    );
    const { tooltip } = hover(container);
    expect(tooltip).toBe(
      'This gene is also hit by a deletion (DEL · het) in trans by read phasing with this ' +
        'heterozygous SNV — the deletion removes the other allele, so the gene is effectively biallelic.',
    );
  });
});
