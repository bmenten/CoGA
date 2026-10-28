import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it } from 'vitest';

import FamilyGenotypeGrid from '../FamilyGenotypeGrid';
import FindingCard, { FindingLinks } from '../FindingCard';

// The anatomy every variant card shares (small variants, repeats, Paraphase, mtDNA): the
// heading, the variant column first and the genotypes second, the footer with the actions at
// the left and the links at the right in one order.
describe('FindingCard', () => {
  it('lays a finding out as every variant card does', () => {
    render(
      <MemoryRouter>
        <FindingCard
          aria-label="HTT repeat"
          rail="pathogenic"
          heading={<div className="variant-card-headline">HTT</div>}
          tags={<span>Pathogenic</span>}
          columns={[
            { key: 'variant', title: 'Repeat', content: <p>chr4</p> },
            { key: 'genotypes', title: 'Genotypes', content: <p>calls</p> },
          ]}
          actions={<button type="button">Extra info</button>}
          links={<FindingLinks omim="https://www.omim.org/search?search=HTT" />}
        />
      </MemoryRouter>,
    );
    const card = screen.getByRole('article', { name: 'HTT repeat' });
    expect(card).toHaveClass('variant-card', 'variant-card--clinvar-pathogenic');
    expect(card.querySelector('.variant-card-head .variant-card-headtags')).toHaveTextContent('Pathogenic');
    expect(Array.from(card.querySelectorAll('.variant-card-col-title')).map((title) => title.textContent)).toEqual([
      'Repeat',
      'Genotypes',
    ]);
    const footer = card.querySelector('.variant-card-footer') as HTMLElement;
    expect(within(footer).getByRole('button', { name: 'Extra info' }).closest('.variant-card-quick')).not.toBeNull();
    expect(within(footer).getByRole('link', { name: 'OMIM' }).closest('.variant-card-nav')).not.toBeNull();
  });

  it('puts the links in one order, the genome views in a new tab', () => {
    render(
      <MemoryRouter>
        <FindingLinks
          igv={{ href: '/families/F1/igv?locus=chrM%3A1-1', label: 'Open IGV at chrM:1-1' }}
          chromosome={{ href: '/families/F1/chromosome/MT', label: 'Open chromosome view at MT:1' }}
          omim="https://www.omim.org/search?search=MT-TL1"
        >
          <a href="https://www.mitomap.org/">MITOMAP</a>
        </FindingLinks>
      </MemoryRouter>,
    );
    const links = screen.getAllByRole('link');
    expect(links.map((link) => link.textContent)).toEqual(['IGV', 'Chromosome view', 'OMIM', 'MITOMAP']);
    expect(links[0]).toHaveAttribute('target', '_blank');
    expect(links[0]).toHaveAccessibleName('Open IGV at chrM:1-1 (opens in a new tab)');
    expect(links[2]).toHaveAttribute('href', 'https://www.omim.org/search?search=MT-TL1');
    expect(links[2]).toHaveAttribute('target', '_blank');
  });
});

describe('FamilyGenotypeGrid', () => {
  it('writes each member as sex, sample and role, the call, its measurements and its status', () => {
    const { container } = render(
      <FamilyGenotypeGrid
        callLabel="Heteroplasmy"
        columns={[{ key: 'dp', label: 'DP', numeric: true }]}
        withStatus
        entries={[
          { group: 'Mother(s) and children' },
          {
            member: { sample_id: 'PROBAND', role: 'proband', sex: 'female', affected: true },
            tone: 'warn',
            call: '46.0%',
            values: { dp: 420 },
            status: { label: 'Heteroplasmic', tone: 'warn' },
            detail: 'Interruption A2',
          },
          { group: 'Father', empty: 'No father sample' },
        ]}
      />,
    );
    expect(container.querySelector('.variant-card-gthead')).toHaveTextContent('SampleRoleHeteroplasmyDPStatus');
    expect(container.querySelector('.variant-card-gtlist')).toHaveStyle({ '--gt-cols': '4' });
    const line = container.querySelector('.variant-card-gtline') as HTMLElement;
    expect(line).toHaveClass('variant-card-gtline--warn', 'is-affected', 'is-proband');
    expect(line).toHaveTextContent('♀PROBANDproband46.0%420Heteroplasmic');
    expect(within(line).getByText('46.0%')).toHaveClass('variant-card-gtline-gt');
    expect(within(line).getByText('420')).toHaveClass('variant-card-gtline-metric');
    expect(within(line).getByText('Heteroplasmic')).toHaveClass('variant-card-chip--warning');
    expect(screen.getByText('Interruption A2')).toHaveClass('variant-card-gtline-detail');
    expect(Array.from(container.querySelectorAll('.variant-card-gtgroup')).map((group) => group.textContent)).toEqual([
      'Mother(s) and children',
      'Father',
    ]);
    expect(screen.getByText('No father sample')).toBeInTheDocument();
  });

  it('gives a call to review its own chip, apart from one not assessed', () => {
    render(
      <FamilyGenotypeGrid
        callLabel="Alleles"
        withStatus
        entries={[
          {
            member: { sample_id: 'A', role: 'proband' },
            tone: 'review',
            call: '20 / 21',
            status: { label: 'Review', tone: 'review' },
          },
          {
            member: { sample_id: 'B', role: 'mother' },
            tone: 'info',
            call: '12 / 300',
            status: { label: 'Not assessed', tone: 'info' },
          },
        ]}
      />,
    );
    expect(screen.getByText('Review')).toHaveClass('variant-card-chip--review');
    expect(screen.getByText('Not assessed')).toHaveClass('variant-card-chip--impact');
    // An unrecorded sex reads as such.
    expect(screen.getAllByTitle('unknown sex')).toHaveLength(2);
  });
});
