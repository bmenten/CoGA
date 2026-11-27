import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import ClinvarCogaChip, { ClinvarCogaMark, clinvarCogaText } from '../ClinvarCogaChip';
import type { ClinvarCogaOut } from '../../../lib/apiSchema.generated';
import {
  clinvarAnnotationLabel,
  clinvarCogaChanged,
  clinvarCogaLabel,
  clinvarCogaOf,
} from '../../../lib/clinvar';

const value = (overrides: Partial<ClinvarCogaOut> = {}): ClinvarCogaOut => ({
  alt: 'A',
  status: 'in_release',
  release: '2026-11-22',
  classification: 'Likely_pathogenic',
  review_status: 'reviewed by expert panel',
  stars: 3,
  conditions: ['Synthetic disorder 1'],
  variation_id: 900002,
  included_only: false,
  ...overrides,
});

describe('the names of the two ClinVar values (B7)', () => {
  it('names each by its release', () => {
    expect(clinvarAnnotationLabel(false, '2026-05-01')).toBe('ClinVar (annotation, 2026-05-01)');
    expect(clinvarAnnotationLabel(false, null)).toBe('ClinVar (annotation)');
    expect(clinvarAnnotationLabel(true, '2026-05-01')).toBe('ClinVar (VEP cache)');
    expect(clinvarCogaLabel('2026-11-22')).toBe('ClinVar (CoGA, 2026-11-22)');
  });
});

describe('clinvarCogaChanged', () => {
  it.each([
    ['the same classification and stars', { clinvar: 'Likely_pathogenic', clinvar_stars: 3 }, value(), false],
    ['another classification', { clinvar: 'Pathogenic', clinvar_stars: 3 }, value(), true],
    ['the same classification, other stars', { clinvar: 'Likely_pathogenic', clinvar_stars: 2 }, value(), true],
    ['an aggregate in another order', { clinvar: 'Pathogenic/Likely_pathogenic', clinvar_stars: 3 }, value({ classification: 'Likely_pathogenic/Pathogenic' }), false],
    ['a classification only CoGA gives', { clinvar: null, clinvar_stars: null }, value(), true],
    ['a classification CoGA no longer holds', { clinvar: 'Benign', clinvar_stars: 1 }, value({ status: 'not_in_release', classification: null, stars: null }), true],
    ['no release to read', { clinvar: 'Benign', clinvar_stars: 1 }, value({ status: 'no_release', classification: null }), false],
    ['an Explorer row, whose stars are not known', { clinvar: 'Likely_pathogenic' }, value(), false],
  ])('%s', (_case, annotated, coga, changed) => {
    expect(clinvarCogaChanged(annotated, coga)).toBe(changed);
  });

  it('reads the value of the ALT the card shows', () => {
    const values = [value({ alt: 'A' }), value({ alt: 'AT', classification: 'Benign' })];
    expect(clinvarCogaOf({ alt: 'A,AT', alt_allele: 'AT', clinvar_coga: values })?.classification).toBe('Benign');
    expect(clinvarCogaOf({ alt: 'A', clinvar_coga: [values[0]] })?.alt).toBe('A');
  });
});

describe('ClinvarCogaChip', () => {
  it('shows CoGA’s classification with its stars, linked to the VariationID, and says it changed', () => {
    render(<ClinvarCogaChip value={value()} release="2026-11-22" annotated={{ clinvar: 'Pathogenic', clinvar_stars: 2 }} />);

    const chip = screen.getByRole('link');
    expect(chip).toHaveTextContent('ClinVar (CoGA, 2026-11-22)Likely pathogenic ★★★☆changed');
    expect(chip).toHaveAttribute('href', 'https://www.ncbi.nlm.nih.gov/clinvar/variation/900002/');
    expect(chip).toHaveAttribute('title', 'reviewed by expert panel — Synthetic disorder 1');
  });

  it('says why an allele has no classification', () => {
    expect(clinvarCogaText(value({ status: 'not_in_release' }))).toBe('not in this release');
    expect(clinvarCogaText(value({ status: 'no_release' }))).toBe('no release loaded');
    expect(clinvarCogaText(value({ status: 'not_available' }))).toBe('not available for this assembly');
    expect(clinvarCogaText(value({ classification: null, included_only: true }))).toBe('only as part of a combination');
  });

  it('marks a table row whose two values differ, and no other', () => {
    const { rerender, container } = render(
      <ClinvarCogaMark variant={{ alt: 'A', clinvar: 'Benign', clinvar_coga: [value()] }} release="2026-11-22" />,
    );
    expect(screen.getByRole('img', { name: 'ClinVar (CoGA, 2026-11-22): Likely pathogenic ★★★☆' })).toHaveTextContent('≠');

    rerender(<ClinvarCogaMark variant={{ alt: 'A', clinvar: 'Likely_pathogenic', clinvar_coga: [value()] }} />);
    expect(container).toBeEmptyDOMElement();
  });
});
