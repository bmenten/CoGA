import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it } from 'vitest';

import FamilyPageHeader, { parsePedigree } from '../FamilyPageHeader';

const family = {
  family_id: 'F1',
  pedigree: 'F1 PROBAND DAD MOM 2 2\nF1 MOM 0 0 2 1\nF1 DAD 0 0 1 1',
  members: [
    { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'female' },
    { sample_id: 'MOM', role: 'mother', affected: false, sex: 'female' },
    { sample_id: 'DAD', role: 'father', affected: false, sex: 'male' },
  ],
  relationships: [],
  metadata: {},
};

const renderHeader = (props: Partial<React.ComponentProps<typeof FamilyPageHeader>> = {}) =>
  render(
    <MemoryRouter>
      <FamilyPageHeader kicker="Small Variants" familyId="F1" family={family} {...props} />
    </MemoryRouter>,
  );

describe('FamilyPageHeader', () => {
  it('makes the family title the way back to the workspace', () => {
    renderHeader();
    // Every analysis page reaches the workspace the same way: through its own title.
    // A separate "Family" button pointing at the same place is what this replaced.
    const title = screen.getByRole('link', { name: 'Family F1' });
    expect(title).toHaveAttribute('href', '/families/F1');
    expect(title.closest('h1')).toHaveClass('catalog-card-title');
  });

  it('keeps the project scope on the way back', () => {
    renderHeader({ projectId: 'P1' });
    expect(screen.getByRole('link', { name: 'Family F1' })).toHaveAttribute(
      'href',
      '/families/F1?project_id=P1',
    );
  });

  it('does not link the title to the page you are already on', () => {
    renderHeader({ kicker: 'Family Workspace', isWorkspace: true });
    expect(screen.queryByRole('link', { name: 'Family F1' })).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Family F1' })).toBeInTheDocument();
  });

  it('names the page above the title', () => {
    renderHeader({ kicker: 'Paraphase' });
    expect(screen.getByText('Paraphase')).toHaveClass('page-kicker');
  });

  it('draws the pedigree once the family has one', () => {
    const { container } = renderHeader();
    expect(container.querySelector('.page-top-card-pedigree')).not.toBeNull();
    expect(container.querySelector('.page-top-card-grid--with-visual')).not.toBeNull();
    expect(screen.getByText('Pedigree')).toHaveClass('analysis-section-title');
  });

  it('drops the visual column when there is no pedigree', () => {
    const { container } = render(
      <MemoryRouter>
        <FamilyPageHeader kicker="Sample QC" familyId="F1" family={{ ...family, pedigree: null }} />
      </MemoryRouter>,
    );
    expect(container.querySelector('.page-top-card-pedigree')).toBeNull();
    expect(container.querySelector('.page-top-card-grid--with-visual')).toBeNull();
  });

  it('renders page content under the title and actions beside it', () => {
    renderHeader({
      actions: <button type="button">Genome</button>,
      children: <span data-testid="summary">Showing 12</span>,
    });
    expect(screen.getByTestId('summary')).toBeInTheDocument();
    const actions = screen.getByRole('button', { name: 'Genome' }).closest('.inline-actions');
    expect(actions).not.toBeNull();
    expect(within(actions as HTMLElement).getByRole('button', { name: 'Genome' })).toBeInTheDocument();
  });

  it('renders a footer inside the card, below the grid', () => {
    const { container } = renderHeader({ footer: <div data-testid="filters">Filters</div> });
    const card = container.querySelector('.page-top-card') as HTMLElement;
    expect(within(card).getByTestId('filters')).toBeInTheDocument();
    // Below the grid, not inside it — filter bars span the whole card.
    expect(card.querySelector('.page-top-card-grid')?.contains(screen.getByTestId('filters'))).toBe(
      false,
    );
  });
});

describe('parsePedigree', () => {
  it('reads whitespace-separated PED rows', () => {
    expect(parsePedigree('F1 PROBAND DAD MOM 2 2')).toEqual([
      { fid: 'F1', iid: 'PROBAND', pid: 'DAD', mid: 'MOM', sex: '2', phen: '2' },
    ]);
  });

  it('ignores blank lines and a missing pedigree', () => {
    expect(parsePedigree('F1 A 0 0 1 1\n\n  \nF1 B 0 0 2 1')).toHaveLength(2);
    expect(parsePedigree(null)).toEqual([]);
    expect(parsePedigree('')).toEqual([]);
  });
});
