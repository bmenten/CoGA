import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import ClinvarConcordanceReport from '../ClinvarConcordanceReport';
import type { ClinvarConcordanceOut, FamilyImportDatasetSummary } from '../../../lib/apiSchema.generated';

const dataset = (
  dataset_type: string,
  clinvar_concordances: ClinvarConcordanceOut[] = [],
): FamilyImportDatasetSummary => ({
  dataset_type,
  enabled: true,
  status: 'imported',
  files: [],
  samples: [],
  message: null,
  summary: {},
  progress: null,
  clinvar_concordances,
});

const compared: ClinvarConcordanceOut = {
  samples: ['PROBAND', 'MOTHER', 'FATHER1'],
  annotated_release: '2026-11-22',
  compared: true,
  reason: null,
  agree: 1234,
  differ: 2,
  differences: [
    {
      variant_id: 'chr1-1000300-A-AT',
      allele: '1-1000300-A-AT',
      reason: 'classification',
      annotated: 'Pathogenic; criteria provided, single submitter; VariationID 900003',
      coga: 'Conflicting classifications of pathogenicity; VariationID 900003',
    },
    { variant_id: null, allele: 'M-3243-A-G', reason: 'not in the annotation', annotated: null, coga: 'Pathogenic' },
  ],
};

const notLoaded = (samples: string[]): ClinvarConcordanceOut => ({
  samples,
  annotated_release: '2026-05-01',
  compared: false,
  reason: "Not compared: ClinVar 2026-05-01, the annotation's release, is not loaded in CoGA.",
  agree: 0,
  differ: 0,
  differences: [],
});

describe('ClinvarConcordanceReport', () => {
  it('shows each load’s agreement and every difference with its reason', () => {
    render(<ClinvarConcordanceReport datasets={[dataset('snv', [compared]), dataset('pedigree')]} />);

    const report = screen.getByRole('region', { name: 'ClinVar concordance of snv (PROBAND, MOTHER, FATHER1)' });
    expect(report).toHaveTextContent(
      "snv (PROBAND, MOTHER, FATHER1): Compared with CoGA's ClinVar 2026-11-22, the annotation's release: 1,234 allele(s) agree, 2 differ.",
    );
    const [, first, second] = within(report).getAllByRole('row');
    expect(first).toHaveTextContent('chr1-1000300-A-AT1-1000300-A-ATclassificationPathogenic;');
    expect(second).toHaveTextContent('—M-3243-A-Gnot in the annotation—Pathogenic');
    expect(screen.getAllByRole('region')).toHaveLength(1);
  });

  it('shows one report per sample of a dataset loaded one sample at a time', () => {
    render(
      <ClinvarConcordanceReport
        datasets={[dataset('snv', [{ ...compared, samples: ['CFDNA1'] }, { ...compared, samples: ['FATHER1'], differ: 0, differences: [] }])]}
      />,
    );

    expect(screen.getByRole('region', { name: 'ClinVar concordance of snv (CFDNA1)' })).toHaveTextContent('2 differ.');
    expect(screen.getByRole('region', { name: 'ClinVar concordance of snv (FATHER1)' })).toHaveTextContent('0 differ.');
  });

  it('says why a load was not compared', () => {
    render(<ClinvarConcordanceReport datasets={[dataset('snv', [notLoaded(['PROBAND'])])]} />);

    expect(screen.getByRole('region', { name: 'ClinVar concordance of snv (PROBAND)' })).toHaveTextContent(
      "snv (PROBAND): Not compared: ClinVar 2026-05-01, the annotation's release, is not loaded in CoGA.",
    );
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('says when it lists only the first differences', () => {
    render(<ClinvarConcordanceReport datasets={[dataset('snv', [{ ...compared, differ: 250 }])]} />);

    expect(screen.getByText('The first 2 of 250 differences.')).toBeInTheDocument();
  });

  it('shows nothing for an import without a small-variant dataset', () => {
    const { container } = render(<ClinvarConcordanceReport datasets={[dataset('pedigree')]} />);

    expect(container).toBeEmptyDOMElement();
  });
});
