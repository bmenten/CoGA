import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import GeneDiseaseHeading, { DISEASE_NAME_MAX } from '../GeneDiseaseHeading';
import InfoIcon from '../InfoIcon';
import OmimLink from '../OmimLink';
import SampleLabel from '../SampleLabel';
import TruncatedText from '../TruncatedText';

describe('TruncatedText', () => {
  it('renders text no longer than its limit whole, without a tooltip or focus stop', () => {
    render(<TruncatedText text="CAGCAGCAGC" max={10} mono />);
    const text = screen.getByText('CAGCAGCAGC');
    expect(text).toHaveClass('truncated-text', 'truncated-text--mono');
    expect(text).not.toHaveAttribute('tabindex');
    fireEvent.mouseEnter(text);
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
  });

  it('cuts longer text on screen and keeps the full value in the DOM and its tooltip', () => {
    const full = 'AAGGGAAGGGAAGGGACAGG';
    render(<TruncatedText text={full} max={10} mono />);
    expect(screen.getByText('AAGGGAAGGG…')).toHaveAttribute('aria-hidden', 'true');
    const whole = screen.getByText(full);
    expect(whole).toHaveClass('sr-only');
    const anchor = whole.closest('.truncated-text') as HTMLElement;
    expect(anchor).toHaveClass('truncated-text--cut');
    // Keyboard reachable: focus shows the tip and describes the anchor with it.
    expect(anchor).toHaveAttribute('tabindex', '0');
    fireEvent.focus(anchor);
    const tip = screen.getByRole('tooltip');
    expect(tip).toHaveTextContent(full);
    expect(anchor).toHaveAttribute('aria-describedby', tip.id);
  });
});

describe('OmimLink', () => {
  it('opens OMIM in a new tab and names the disease it opens', () => {
    render(<OmimLink href="https://www.omim.org/entry/253300" subject="Spinal muscular atrophy" />);
    const link = screen.getByRole('link', {
      name: 'Open Spinal muscular atrophy on OMIM (opens in a new tab)',
    });
    expect(link).toHaveTextContent('OMIM');
    expect(link).toHaveAttribute('href', 'https://www.omim.org/entry/253300');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  });
});

describe('GeneDiseaseHeading', () => {
  it('reads "GENE — Disease (OMIM)", the gene first and prominent', () => {
    const { container } = render(
      <GeneDiseaseHeading
        gene="HTT"
        diseases={[{ name: 'Huntington disease', omimUrl: 'https://www.omim.org/entry/143100' }]}
      >
        <span>Pathogenic</span>
      </GeneDiseaseHeading>,
    );
    const heading = container.querySelector('.gene-disease-heading') as HTMLElement;
    expect(heading).toHaveTextContent('HTT—Huntington disease (OMIM)Pathogenic');
    expect(heading.firstElementChild).toHaveClass('gene-disease-heading-gene');
    expect(heading.firstElementChild).toHaveTextContent('HTT');
    expect(within(heading).getByRole('link', { name: /huntington disease on omim/i })).toHaveAttribute(
      'href',
      'https://www.omim.org/entry/143100',
    );
  });

  it('cuts a long disease name but never the gene, and links each disease it can', () => {
    const longName = 'Spinocerebellar ataxia type 3 / Machado-Joseph disease, adult onset';
    const { container } = render(
      <GeneDiseaseHeading
        gene="ATXN3"
        diseases={[
          { name: longName, omimUrl: 'https://www.omim.org/entry/109150' },
          { name: 'No entry', omimUrl: null },
        ]}
      />,
    );
    expect(longName.length).toBeGreaterThan(DISEASE_NAME_MAX);
    expect(screen.getByText('ATXN3')).toBeInTheDocument();
    expect(screen.getByText(longName)).toHaveClass('sr-only');
    fireEvent.mouseEnter(screen.getByText(longName).closest('.truncated-text') as HTMLElement);
    expect(screen.getByRole('tooltip')).toHaveTextContent(longName);
    expect(screen.getByText('No entry')).toBeInTheDocument();
    expect(screen.getAllByRole('link')).toHaveLength(1);
    expect(container.querySelectorAll('.gene-disease-heading-disease')).toHaveLength(2);
  });

  it('shows the gene alone when no disease is named', () => {
    const { container } = render(<GeneDiseaseHeading gene="MT-ND5" diseases={[{ name: ' ' }]} />);
    expect(container.querySelector('.gene-disease-heading')).toHaveTextContent(/^MT-ND5$/);
    expect(container.querySelector('.gene-disease-heading-sep')).toBeNull();
  });
});

describe('InfoIcon and SampleLabel', () => {
  it('keeps explanatory text behind a keyboard-reachable icon with a name', () => {
    render(<InfoIcon name="How to read SMN1/SMN2" text="Read SMN1 copy number first." />);
    expect(screen.queryByText('Read SMN1 copy number first.')).not.toBeInTheDocument();
    const icon = screen.getByText('How to read SMN1/SMN2').closest('.info-icon') as HTMLElement;
    expect(icon).toHaveAttribute('tabindex', '0');
    fireEvent.focus(icon);
    expect(screen.getByRole('tooltip')).toHaveTextContent('Read SMN1 copy number first.');
  });

  it('writes a member as their sample ID and role', () => {
    const { container } = render(<SampleLabel sampleId="PROBAND" role="proband" emphasis />);
    expect(container.firstElementChild).toHaveClass('sample-label', 'sample-label--emphasis');
    expect(container.firstElementChild).toHaveTextContent('PROBANDproband');
  });
});
