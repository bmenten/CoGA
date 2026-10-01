// ClinGen CNV points-scale readout — REQ-UI-003 (risk H3).

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import CnvScaleBar from '../CnvScaleBar';
import type { CnvClassification } from '../../../lib/cnvAcmg';

describe('CnvScaleBar', () => {
  it('shows the CNV class label and signed point total', () => {
    const classification: CnvClassification = {
      pointTotal: 0.99,
      classKey: 'pathogenic',
      classLabel: 'Pathogenic',
    };
    render(<CnvScaleBar classification={classification} />);

    // Disambiguate the readout label from the identical legend label.
    expect(screen.getByText('Pathogenic', { selector: '.acmg-scalebar-class' })).toBeInTheDocument();
    expect(screen.getByText('+0.99 pts')).toBeInTheDocument();
    expect(screen.getByRole('img')).toHaveAttribute(
      'aria-label',
      expect.stringContaining('Pathogenic, +0.99 points'),
    );
  });

  it('formats a negative (benign) total without a plus sign', () => {
    const classification: CnvClassification = {
      pointTotal: -0.9,
      classKey: 'benign',
      classLabel: 'Benign',
    };
    render(<CnvScaleBar classification={classification} />);

    expect(screen.getByText('-0.90 pts')).toBeInTheDocument();
  });

  it('writes the total to two decimals, as the classification modal writes its points', () => {
    // A Likely-pathogenic 0.90 must not read "+0.9" beside a Pathogenic "+0.99".
    const { unmount } = render(
      <CnvScaleBar
        classification={{ pointTotal: 0.9, classKey: 'cnv_class_4', classLabel: 'Likely Pathogenic' }}
      />,
    );
    expect(screen.getByText('+0.90 pts')).toBeInTheDocument();
    expect(screen.getByRole('img')).toHaveAttribute(
      'aria-label',
      expect.stringContaining('Likely Pathogenic, +0.90 points'),
    );
    unmount();

    render(
      <CnvScaleBar classification={{ pointTotal: 1, classKey: 'cnv_class_5', classLabel: 'Pathogenic' }} />,
    );
    expect(screen.getByText('+1.00 pts')).toBeInTheDocument();
  });

  it('writes a zero total as 0.00, without a sign', () => {
    render(
      <CnvScaleBar classification={{ pointTotal: 0, classKey: 'cnv_class_3', classLabel: 'VUS' }} />,
    );
    expect(screen.getByText('0.00 pts')).toBeInTheDocument();
  });
});
