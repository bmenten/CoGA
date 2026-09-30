import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi } from 'vitest';

import StructuralVariantCards from '../StructuralVariantCards';
import type { StructuralVariant } from '../structuralVariantSearch';

const variant = {
  _id: '2-160120294-160120688-DEL---',
  chr: '2',
  start: 160120294,
  end: 160120688,
  length: 394,
  type: 'DEL',
  gene: 'ITGB6',
  genotypes: [],
} as unknown as StructuralVariant;

const renderCards = (assembly?: { speciesName?: string; assemblyName?: string }) =>
  render(
    <MemoryRouter>
      <StructuralVariantCards
        familyId="F1"
        projectId="P1"
        linkSearch=""
        members={[]}
        variants={[variant]}
        tags={[]}
        {...assembly}
      />
    </MemoryRouter>,
  );

describe('StructuralVariantCards frequencies heading', () => {
  it('links to the gnomAD SV browser over the call span', () => {
    renderCards({ speciesName: 'Homo sapiens', assemblyName: 'GRCh38' });
    const heading = screen.getByRole('link', { name: 'Frequencies' });
    // A region view, not a variant page: gnomAD has no id matching a caller's SV.
    expect(heading).toHaveAttribute(
      'href',
      'https://gnomad.broadinstitute.org/region/2-160120294-160120688?dataset=gnomad_sv_r4',
    );
    expect(heading).toHaveAttribute('target', '_blank');
    expect(heading).toHaveAttribute('rel', 'noreferrer');
  });

  it('stays plain text when the assembly has no gnomAD to point at', () => {
    renderCards({ speciesName: 'Homo sapiens', assemblyName: 'T2T-CHM13v2.0' });
    expect(screen.queryByRole('link', { name: 'Frequencies' })).not.toBeInTheDocument();
    // The column itself is still there.
    expect(screen.getByText('Frequencies')).toBeInTheDocument();
  });

  it('stays plain text when no assembly is known at all', () => {
    renderCards();
    expect(screen.queryByRole('link', { name: 'Frequencies' })).not.toBeInTheDocument();
    expect(screen.getByText('Frequencies')).toBeInTheDocument();
  });
});

describe('StructuralVariantCards HPO link', () => {
  it('carries the SV page, with its project and filters, so the HPO page can return to it (#526)', () => {
    const withHpo = {
      ...variant,
      annotation_extra: { hpo_terms: 'HP:0001250;HP:0000707' },
    } as unknown as StructuralVariant;
    render(
      <MemoryRouter initialEntries={['/families/F1/structural-variants?project_id=P1&type=DEL']}>
        <StructuralVariantCards
          familyId="F1"
          projectId="P1"
          linkSearch=""
          members={[]}
          variants={[withHpo]}
          tags={[]}
        />
      </MemoryRouter>,
    );
    const link = screen.getByRole('link', { name: /HP:0001250/ });
    const params = new URLSearchParams((link.getAttribute('href') ?? '').split('?')[1]);
    expect(params.getAll('term')).toEqual(['HP:0001250', 'HP:0000707']);
    expect(params.get('family_id')).toBe('F1');
    expect(params.get('from')).toBe('/families/F1/structural-variants?project_id=P1&type=DEL');
  });
});

describe('StructuralVariantCards per caller', () => {
  it('shows one card per caller when two callers share an SV id', () => {
    const errors = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    render(
      <MemoryRouter>
        <StructuralVariantCards
          familyId="F1"
          projectId="P1"
          linkSearch=""
          members={[]}
          variants={[
            { ...variant, source: 'sniffles' },
            { ...variant, source: 'spectre' },
          ]}
          tags={[]}
        />
      </MemoryRouter>,
    );
    expect(screen.getAllByRole('article')).toHaveLength(2);
    expect(errors.mock.calls.flat().join(' ')).not.toMatch(/same key/);
    errors.mockRestore();
  });
});
